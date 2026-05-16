"""
FunHarness - Agent team facade.

TeamManager keeps the public API stable while run snapshots, mailboxes,
subagents, and long-running teammate workers live in focused modules.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .llm import MODEL, client
from .runtime import RuntimeTaskManager
from .subagent import SubAgent
from .team_mailbox import TeamMailbox, TeamMailboxEnvelope
from .team_runs import TeamRun, TeamRunAgent, TeamRunArtifact, TeamRunEdge, TeamRunManager, TeamRunMessage, TeamRunTask
from .teammate import TeammateCallbacks, TeammateQueueTask, TeammateRegistry, TeammateState


@dataclass
class TeamMember:
    name: str
    role: str
    instructions: str = ""
    status: str = "idle"
    created_at: float = 0.0
    last_active_at: float = 0.0
    worker_id: str = ""
    runtime_id: str = ""
    queue_depth: int = 0
    current_task_id: str = ""
    last_error: str = ""

    def to_dict(self) -> dict:
        return self.__dict__.copy()

    @classmethod
    def from_dict(cls, data: dict) -> "TeamMember":
        return cls(
            name=data["name"],
            role=data.get("role", "generalist"),
            instructions=data.get("instructions", ""),
            status=data.get("status", "idle"),
            created_at=float(data.get("created_at", 0.0)),
            last_active_at=float(data.get("last_active_at", 0.0)),
            worker_id=data.get("worker_id", ""),
            runtime_id=data.get("runtime_id", ""),
            queue_depth=int(data.get("queue_depth", 0)),
            current_task_id=data.get("current_task_id", ""),
            last_error=data.get("last_error", ""),
        )


class TeamManager:
    def __init__(
        self,
        root: str | Path = ".funharness/team",
        runtime: RuntimeTaskManager | None = None,
        model: str = MODEL,
        llm_client=None,
        tool_registry=None,
    ):
        self.root = Path(root)
        self.config_path = self.root / "config.json"
        self.runtime = runtime
        self.model = model
        self.llm_client = llm_client or client
        self.tool_registry = tool_registry
        self.root.mkdir(parents=True, exist_ok=True)
        self.mailbox = TeamMailbox(self.root)
        self.inbox_dir = self.mailbox.inbox_dir
        self.history_dir = self.mailbox.history_dir
        self.runs = TeamRunManager(self.root.parent / "team_runs")
        self.event_sink: Callable[[str, dict], None] | None = None
        self._members: dict[str, TeamMember] = {}
        self.workers = TeammateRegistry(TeammateCallbacks(
            on_started=self._on_worker_started,
            on_done=self._on_worker_done,
            on_failed=self._on_worker_failed,
            on_cancelled=self._on_worker_cancelled,
            on_status=self._on_worker_status,
            on_shutdown=self._on_worker_shutdown,
        ))
        self._load()

    def _load(self):
        if not self.config_path.exists():
            return
        try:
            data = json.loads(self.config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        for item in data.get("members", []):
            try:
                member = TeamMember.from_dict(item)
            except (KeyError, TypeError, ValueError):
                continue
            if member.status in {"running", "working", "cancelling", "stopping"}:
                member.status = "stopped"
            member.queue_depth = 0
            member.current_task_id = ""
            self._members[member.name] = member

    def _save(self):
        data = {"members": [m.to_dict() for m in self._members.values()]}
        self.config_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    def create(self, name: str, role: str, instructions: str = "") -> TeamMember:
        name = _safe_name(name)
        if not name:
            raise ValueError("Team member name is required")
        now = time.time()
        member = TeamMember(
            name=name,
            role=role or "generalist",
            instructions=instructions,
            created_at=now,
            last_active_at=now,
        )
        self._members[name] = member
        self._save()
        self.send("lead", name, f"You joined the team as {member.role}.")
        run = self.runs.current()
        if run and run.status == "running":
            updated = self.runs.add_agent(run.run_id, member)
            self._emit("team_run_updated", {"run": updated.to_dict()} if updated else {})
        return member

    def get(self, name: str) -> TeamMember | None:
        return self._members.get(_safe_name(name))

    def list(self) -> list[TeamMember]:
        return sorted(self._members.values(), key=lambda m: m.name)

    def rename(self, old_name: str, new_name: str) -> TeamMember:
        old_safe = _safe_name(old_name)
        new_safe = _safe_name(new_name)
        if old_safe not in self._members:
            raise KeyError(old_safe)
        if not new_safe:
            raise ValueError("Team member name is required")
        if new_safe in self._members and new_safe != old_safe:
            raise ValueError(f"Team member already exists: {new_safe}")
        member = self._members.pop(old_safe)
        member.name = new_safe
        member.last_active_at = time.time()
        self._members[new_safe] = member
        self._save()
        run = self.runs.current()
        if run:
            updated = self.runs.rename_agent(run.run_id, old_safe, new_safe)
            self._emit("team_run_updated", {"run": updated.to_dict()} if updated else {})
        return member

    def delete(self, name: str) -> str:
        """Permanently delete a teammate from the team roster."""
        safe = _safe_name(name)
        if safe not in self._members:
            raise KeyError(safe)
        member = self._members[safe]
        # Stop the worker if it is running
        if member.status in {"queued", "working", "running", "cancelling", "stopping"}:
            try:
                self.workers.shutdown(safe, reason="Teammate deleted.", force=True)
            except Exception:
                pass
        # Remove from current run canvas if present
        run = self.runs.current()
        if run:
            try:
                self.runs.remove_agent(run.run_id, safe)
            except Exception:
                pass
        # Clean up inbox
        self.mailbox.clear(safe)
        # Remove from roster
        del self._members[safe]
        self._save()
        return f"Teammate '{safe}' has been permanently deleted."

    def send(self, sender: str, to: str, content: str) -> str:
        to = _safe_name(to)
        if to not in self._members:
            raise KeyError(to)
        envelope = self.mailbox.send_message(sender or "lead", to, content)
        self.workers.send_message(to, envelope.from_name, envelope.content)
        return f"Message sent to {to}"

    def drain_inbox(self, name: str) -> list[dict]:
        return self.mailbox.drain(name)

    def peek_inbox(self, name: str) -> list[dict]:
        return self.mailbox.peek(name)

    def delegate(self, to: str, task: str, context: str = "", run_id: str = "") -> str:
        member = self.get(to)
        if member is None:
            raise KeyError(to)
        if member.status in {"stopped", "stopping"}:
            raise ValueError(f"{member.name} is stopped. Start the member before assigning new work.")
        run = self.runs.get(run_id) if run_id else self.runs.current()
        run_id = run.run_id if run else ""
        team_task_id = ""
        if run_id:
            team_task_id = self.runs.assign_task(run_id, member.name, task, context)
            snapshot = self.runs.snapshot(run_id)
            self._emit("team_task_assigned", {"run": snapshot, "agent": member.name, "task_id": team_task_id})

        runtime_id = self.workers.submit_task(
            member,
            task,
            context=self._task_context(member.name, context),
            run_id=run_id,
            team_task_id=team_task_id,
            model=self.model,
            llm_client=self.llm_client,
            tool_registry=self.tool_registry,
        )
        self.mailbox.write_task("lead", member.name, task, context=context, task_id=team_task_id, run_id=run_id, runtime_id=runtime_id)
        member.status = "queued"
        member.runtime_id = runtime_id
        member.current_task_id = team_task_id
        member.queue_depth = (self.workers.state(member.name) or TeammateState(member.name, "")).queue_depth
        member.last_active_at = time.time()
        self._save()
        if run_id and team_task_id:
            self.runs.set_task_runtime(run_id, team_task_id, runtime_id)
            snapshot = self.runs.snapshot(run_id)
            self._emit("team_run_updated", {"run": snapshot} if snapshot else {})
        return runtime_id

    def start_member(self, name: str, run_id: str = "") -> dict[str, Any]:
        member = self.get(name)
        if member is None:
            raise KeyError(name)
        state = self.workers.start(member, model=self.model, llm_client=self.llm_client, tool_registry=self.tool_registry)
        self._apply_state(member.name, state)
        payload = self._update_run_agent_from_member(run_id or self._current_run_id(), member.name)
        self._emit("team_agent_started", payload)
        return payload

    def cancel_member(self, name: str, run_id: str = "", reason: str = "") -> dict[str, Any]:
        member = self.get(name)
        if member is None:
            raise KeyError(name)
        self.mailbox.write(TeamMailboxEnvelope("cancel", "lead", member.name, reason or "cancel requested", time.time(), run_id=run_id, reason=reason))
        ok = self.workers.cancel(member.name, reason=reason)
        if not ok:
            member.status = "idle"
            member.last_error = reason
            member.last_active_at = time.time()
            self._save()
        else:
            member.status = "cancelling"
            member.last_error = reason
            member.last_active_at = time.time()
            self._save()
        payload = self._update_run_agent_from_member(run_id or self._current_run_id(), member.name)
        self._emit("team_agent_cancelled", payload)
        return payload

    def shutdown_member(self, name: str, run_id: str = "", reason: str = "", force: bool = False) -> dict[str, Any]:
        member = self.get(name)
        if member is None:
            raise KeyError(name)
        self.mailbox.write(TeamMailboxEnvelope("shutdown", "lead", member.name, reason or "shutdown requested", time.time(), run_id=run_id, reason=reason))
        ok = self.workers.shutdown(member.name, reason=reason, force=force)
        if not ok:
            member.status = "stopped"
            member.queue_depth = 0
            member.current_task_id = ""
            member.last_error = reason
            member.last_active_at = time.time()
            self._save()
            if run_id:
                self.runs.update_agent(run_id, member.name, status="stopped", queue_depth=0, current_task_id="", last_error=reason)
        else:
            member.status = "stopping"
            member.last_error = reason
            member.last_active_at = time.time()
            self._save()
        payload = self._update_run_agent_from_member(run_id or self._current_run_id(), member.name)
        self._emit("team_agent_shutdown", payload)
        return payload

    def _task_context(self, name: str, context: str) -> str:
        inbox = self.drain_inbox(name)
        inbox_text = "\n".join(f"[{item.get('from', '?')}] {item.get('content', '')}" for item in inbox if item.get("type") == "message")
        if context and inbox_text:
            return f"{context}\n\nInbox:\n{inbox_text}"
        return context or (f"Inbox:\n{inbox_text}" if inbox_text else "")

    def _append_history(self, name: str, task: str, result: str):
        self.mailbox.append_history(name, task, result)

    def _inbox_path(self, name: str) -> Path:
        return self.mailbox.inbox_path(name)

    def summary(self) -> str:
        members = self.list()
        if not members:
            return "(no teammates)"
        lines = ["Team:"]
        for member in members:
            inbox_count = len(self.peek_inbox(member.name))
            queue_note = f", queue: {member.queue_depth}" if member.queue_depth else ""
            lines.append(f"  {member.name} [{member.role}] {member.status} (inbox: {inbox_count}{queue_note})")
        return "\n".join(lines)

    def start_run(self, goal: str) -> dict:
        run = self.runs.start(goal, self.list())
        payload = {"run": run.to_dict()}
        self._emit("team_run_started", payload)
        return run.to_dict()

    def current_run(self) -> dict | None:
        return self.runs.snapshot()

    def get_run(self, run_id: str) -> dict | None:
        return self.runs.snapshot(run_id)

    def add_run_agent(self, run_id: str, name: str) -> dict | None:
        member = self.get(name)
        if member is None:
            raise KeyError(name)
        run = self.runs.add_agent(run_id, member)
        if run is None:
            return None
        payload = {"run": run.to_dict()}
        self._emit("team_run_updated", payload)
        return run.to_dict()

    def remove_run_agent(self, run_id: str, name: str, reason: str = "") -> dict | None:
        member = self.get(name)
        if member is not None and member.status in {"queued", "working", "running", "cancelling", "stopping"}:
            self.shutdown_member(name, run_id=run_id, reason=reason or "Removed from Agent Teams canvas.", force=True)
        run = self.runs.remove_agent(run_id, name)
        if run is None:
            return None
        payload = {"run": run.to_dict()}
        self._emit("team_run_updated", payload)
        return run.to_dict()

    def send_run_message(self, run_id: str, sender: str, to: str, content: str) -> dict | None:
        if to != "lead" and to != "team" and self.get(to) is not None:
            self.send(sender or "lead", to, content)
        run = self.runs.record_message(run_id, sender or "lead", to, content, "message")
        if run is None:
            return None
        payload = {"run": run.to_dict()}
        self._emit("team_message", payload)
        return run.to_dict()

    def update_run_task(self, run_id: str, task_id: str, status: str = "", owner: str = "", output: str = "") -> dict | None:
        run = self.runs.update_task(run_id, task_id, status=status, owner=owner, output=output)
        if run is None:
            return None
        payload = {"run": run.to_dict()}
        self._emit("team_run_updated", payload)
        return run.to_dict()

    def _on_worker_started(self, name: str, task: TeammateQueueTask, state: TeammateState) -> None:
        self._apply_state(name, state)
        if task.run_id:
            updated = self.runs.update_agent(
                task.run_id,
                name,
                status="working",
                progress=35,
                current_task=task.task,
                runtime_id=task.runtime_id,
                worker_id=state.worker_id,
                queue_depth=state.queue_depth,
                current_task_id=task.team_task_id,
                last_error="",
            )
            self._emit("team_agent_status", {"run": updated.to_dict()} if updated else {})

    def _on_worker_done(self, name: str, task: TeammateQueueTask, result: str, state: TeammateState) -> None:
        self._append_history(name, task.task, result)
        self._apply_state(name, state, status="idle", current_task_id="")
        if task.run_id:
            self.runs.record_artifact(task.run_id, name, f"{name} result", result, runtime_id=task.runtime_id)
            self.runs.record_message(task.run_id, name, "lead", result[:600], "report")
            updated = self.runs.update_agent(
                task.run_id,
                name,
                status="done",
                progress=100,
                output=result,
                queue_depth=state.queue_depth,
                current_task_id="",
                last_error="",
            )
            updated = self.runs.record_lead_feedback(task.run_id, name, task.task, result) or updated
            self._emit("team_artifact", {"run": updated.to_dict()} if updated else {})
            self._emit("team_run_finished" if updated and updated.status in {"done", "failed", "cancelled"} else "team_run_updated", {"run": updated.to_dict()} if updated else {})

    def _on_worker_failed(self, name: str, task: TeammateQueueTask, error: str, state: TeammateState) -> None:
        self._apply_state(name, state, status="idle", current_task_id="", last_error=error)
        if task.run_id:
            updated = self.runs.update_agent(task.run_id, name, status="failed", output=error, queue_depth=state.queue_depth, current_task_id="", last_error=error)
            self._emit("team_run_error", {"run": updated.to_dict()} if updated else {})

    def _on_worker_cancelled(self, name: str, task: TeammateQueueTask | None, reason: str, state: TeammateState) -> None:
        self._apply_state(name, state, status="idle", current_task_id="", last_error=reason)
        if task and task.run_id:
            self.runs.record_message(task.run_id, "lead", name, reason or "Task cancelled.", "status")
            updated = self.runs.update_agent(task.run_id, name, status="cancelled", output=reason or "Task cancelled.", queue_depth=state.queue_depth, current_task_id="", last_error=reason)
            self._emit("team_agent_cancelled", {"run": updated.to_dict()} if updated else {})

    def _on_worker_status(self, name: str, state: TeammateState) -> None:
        member = self.get(name)
        if member is None:
            return
        # Avoid replacing a visible task state with idle during routine heartbeats.
        status = state.status if state.status not in {"idle"} or member.status not in {"working", "queued", "cancelling"} else member.status
        self._apply_state(name, state, status=status)

    def _on_worker_shutdown(self, name: str, state: TeammateState) -> None:
        self._apply_state(name, state, status="stopped", current_task_id="")
        run_id = self._current_run_id()
        if run_id:
            updated = self.runs.update_agent(run_id, name, status="stopped", queue_depth=0, current_task_id="", last_error=state.last_error)
            self._emit("team_agent_shutdown", {"run": updated.to_dict()} if updated else {})

    def _apply_state(
        self,
        name: str,
        state: TeammateState,
        *,
        status: str | None = None,
        current_task_id: str | None = None,
        last_error: str | None = None,
    ) -> None:
        member = self.get(name)
        if member is None:
            return
        member.status = status or state.status
        member.worker_id = state.worker_id
        member.runtime_id = state.runtime_id
        member.queue_depth = state.queue_depth
        member.current_task_id = state.current_task_id if current_task_id is None else current_task_id
        member.last_error = state.last_error if last_error is None else last_error
        member.last_active_at = time.time()
        self._save()

    def _current_run_id(self) -> str:
        run = self.runs.current()
        return run.run_id if run else ""

    def _run_payload(self, run_id: str) -> dict[str, Any]:
        snapshot = self.runs.snapshot(run_id) if run_id else self.runs.snapshot()
        return {"run": snapshot}

    def _update_run_agent_from_member(self, run_id: str, name: str) -> dict[str, Any]:
        member = self.get(name)
        if not run_id or member is None:
            return self._run_payload(run_id)
        updated = self.runs.update_agent(
            run_id,
            member.name,
            status=member.status,
            runtime_id=member.runtime_id,
            worker_id=member.worker_id,
            queue_depth=member.queue_depth,
            current_task_id=member.current_task_id,
            last_error=member.last_error,
        )
        return {"run": updated.to_dict()} if updated else self._run_payload(run_id)

    def _emit(self, event_type: str, payload: dict) -> None:
        if self.event_sink is None:
            return
        try:
            self.event_sink(event_type, payload)
        except Exception:
            pass


def _safe_name(name: str) -> str:
    return "".join(ch for ch in name.strip().lower() if ch.isalnum() or ch in ("-", "_"))
