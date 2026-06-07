"""Per-member runtime queues for group agents."""
from __future__ import annotations

import queue
import threading
import time
from typing import Any, Callable

from .models import AgentProfile, GroupAgentRun, GroupMessage
from .runner import GroupAgentRunner
from .store import GroupStore


class GroupRuntimePool:
    def __init__(
        self,
        *,
        store: GroupStore,
        runner_factory: Callable[..., GroupAgentRunner],
        event_sink: Callable[[str, dict[str, Any]], None] | None = None,
        max_workers: int = 4,
    ) -> None:
        self.store = store
        self.runner_factory = runner_factory
        self.event_sink = event_sink
        self._queues: dict[str, queue.Queue[str]] = {}
        self._threads: dict[str, threading.Thread] = {}
        self._cancel_events: dict[str, threading.Event] = {}
        self._lock = threading.RLock()
        self._parallel = threading.Semaphore(max(1, int(max_workers)))

    def submit(self, run: GroupAgentRun) -> None:
        with self._lock:
            self._cancel_events[run.id] = threading.Event()
            q = self._queues.setdefault(run.member_id, queue.Queue())
            q.put(run.id)
            self._ensure_worker(run.member_id)
            self._sync_member_queue(run.group_id, run.member_id)
        self._emit("group_run_queued", run.to_dict())

    def cancel(self, run_id: str) -> bool:
        event = self._cancel_events.get(run_id)
        if event is None:
            return False
        event.set()
        run = self.store.get_run(run_id)
        if run and run.status == "queued":
            run.status = "cancelled"
            run.finished_at = time.time()
            self.store.save_run(run)
            self._emit("group_run_cancelled", run.to_dict())
        return True

    def shutdown(self) -> None:
        for event in list(self._cancel_events.values()):
            event.set()
        for q in list(self._queues.values()):
            q.put("__shutdown__")
        for thread in list(self._threads.values()):
            if thread.is_alive():
                thread.join(timeout=1)

    def _ensure_worker(self, member_id: str) -> None:
        thread = self._threads.get(member_id)
        if thread and thread.is_alive():
            return
        thread = threading.Thread(target=self._worker_loop, args=(member_id,), name=f"group-member-{member_id}", daemon=True)
        self._threads[member_id] = thread
        thread.start()

    def _worker_loop(self, member_id: str) -> None:
        q = self._queues[member_id]
        while True:
            try:
                run_id = q.get(timeout=30)
            except queue.Empty:
                return
            if run_id == "__shutdown__":
                return
            run = self.store.get_run(run_id)
            if run is None:
                continue
            self._sync_member_queue(run.group_id, member_id)
            cancel_event = self._cancel_events.get(run_id) or threading.Event()
            if cancel_event.is_set():
                run.status = "cancelled"
                run.finished_at = time.time()
                self.store.save_run(run)
                self._emit("group_run_cancelled", run.to_dict())
                continue
            with self._parallel:
                self._execute_run(run, cancel_event)
            self._sync_member_queue(run.group_id, member_id)

    def _execute_run(self, run: GroupAgentRun, cancel_event: threading.Event) -> None:
        group = self.store.get_group(run.group_id)
        member = self.store.get_member(run.group_id, run.member_id)
        profile = self.store.get_profile(run.profile_id)
        if group is None or member is None or profile is None:
            run.status = "failed"
            run.error = "Group, member, or profile not found"
            run.finished_at = time.time()
            self.store.save_run(run)
            self._emit("group_run_failed", run.to_dict())
            return

        trigger = next((item for item in self.store.list_messages(run.group_id, limit=500) if item.id == run.trigger_message_id), None)
        if trigger is None:
            trigger = GroupMessage(group_id=run.group_id, content=run.input_text)
        session = self.store.get_or_create_session(run.group_id, member)

        member.status = "running"
        member.current_run_id = run.id
        member.current_task = run.input_text
        self.store.save_member(member)
        run.status = "running"
        run.started_at = time.time()
        self.store.save_run(run)
        self._emit("group_member_status", member.to_dict())
        self._emit("group_run_started", run.to_dict())

        try:
            runner = self.runner_factory(profile=profile)
            output = runner.run(
                group=group,
                member=member,
                profile=profile,
                session=session,
                run=run,
                trigger=trigger,
                cancel_event=cancel_event,
            )
            if cancel_event.is_set():
                run.status = "cancelled"
                run.output_text = output
                event_type = "group_run_cancelled"
            else:
                run.status = "done"
                run.output_text = output
                event_type = "group_run_done"
                has_agent_message = any(
                    message.sender_type == "agent" and message.run_id == run.id
                    for message in self.store.list_messages(run.group_id, limit=500)
                )
                if output and not has_agent_message:
                    message = GroupMessage(
                        group_id=run.group_id,
                        sender_type="agent",
                        sender_id=member.id,
                        sender_name=member.display_name,
                        content=output,
                        mentions=[],
                        run_id=run.id,
                    )
                    self.store.append_message(message)
                    self._emit("group_message_created", message.to_dict())
            session.updated_at = time.time()
            self.store.save_session(session)
        except Exception as exc:
            run.status = "failed"
            run.error = f"{type(exc).__name__}: {exc}"
            event_type = "group_run_failed"
        finally:
            run.finished_at = time.time()
            self.store.save_run(run)
            refreshed = self.store.get_member(run.group_id, run.member_id)
            if refreshed:
                refreshed.status = "idle" if run.status in {"done", "cancelled"} else "failed"
                refreshed.current_run_id = ""
                refreshed.current_task = ""
                refreshed.last_error = run.error
                self.store.save_member(refreshed)
                self._emit("group_member_status", refreshed.to_dict())
            self._emit(event_type, run.to_dict())
            self._emit("group_snapshot_updated", self.store.snapshot(run.group_id))

    def _sync_member_queue(self, group_id: str, member_id: str) -> None:
        member = self.store.get_member(group_id, member_id)
        if member is None:
            return
        q = self._queues.get(member_id)
        member.queue_depth = q.qsize() if q else 0
        self.store.save_member(member)
        self._emit("group_member_status", member.to_dict())

    def _emit(self, event_type: str, payload: dict[str, Any]) -> None:
        if self.event_sink:
            self.event_sink(event_type, payload)
