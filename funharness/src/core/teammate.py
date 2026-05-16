"""
FunHarness - Persistent in-process teammate workers.
"""
from __future__ import annotations

import inspect
import queue
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Literal

from .subagent import SubAgent

QueueItemType = Literal["task", "message", "shutdown"]


@dataclass
class TeammateQueueTask:
    runtime_id: str
    task: str
    context: str = ""
    run_id: str = ""
    team_task_id: str = ""


@dataclass
class TeammateQueueItem:
    type: QueueItemType
    task: TeammateQueueTask | None = None
    sender: str = ""
    content: str = ""
    reason: str = ""
    force: bool = False


@dataclass
class TeammateState:
    name: str
    worker_id: str
    status: str = "idle"
    runtime_id: str = ""
    queue_depth: int = 0
    current_task_id: str = ""
    current_task: str = ""
    last_error: str = ""
    started_at: float = field(default_factory=time.time)
    last_active_at: float = field(default_factory=time.time)


@dataclass
class TeammateCallbacks:
    on_started: Callable[[str, TeammateQueueTask, TeammateState], None] | None = None
    on_done: Callable[[str, TeammateQueueTask, str, TeammateState], None] | None = None
    on_failed: Callable[[str, TeammateQueueTask, str, TeammateState], None] | None = None
    on_cancelled: Callable[[str, TeammateQueueTask | None, str, TeammateState], None] | None = None
    on_status: Callable[[str, TeammateState], None] | None = None
    on_shutdown: Callable[[str, TeammateState], None] | None = None


class TeammateWorker:
    def __init__(
        self,
        *,
        name: str,
        role: str,
        instructions: str,
        model: str,
        llm_client: Any,
        tool_registry: Any,
        callbacks: TeammateCallbacks,
        runner_factory: Callable[..., Any] | None = None,
    ):
        self.name = name
        self.role = role
        self.instructions = instructions
        self.model = model
        self.llm_client = llm_client
        self.tool_registry = tool_registry
        self.callbacks = callbacks
        self.runner_factory = runner_factory or SubAgent
        self.state = TeammateState(name=name, worker_id=f"worker_{uuid.uuid4().hex[:8]}")
        self._queue: queue.Queue[TeammateQueueItem] = queue.Queue()
        self._shutdown_event = threading.Event()
        self._cancel_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.RLock()
        self._pending_messages: list[str] = []
        self._runner: Any | None = None
        self._current_task: TeammateQueueTask | None = None

    def start(self) -> TeammateState:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return self.snapshot()
            self._shutdown_event.clear()
            self._cancel_event.clear()
            self.state.status = "idle"
            self.state.started_at = time.time()
            self._thread = threading.Thread(target=self._run, name=f"teammate-{self.name}", daemon=True)
            self._thread.start()
            self._emit_status()
            return self.snapshot()

    def submit_task(self, task: str, context: str = "", run_id: str = "", team_task_id: str = "") -> str:
        self.start()
        runtime_id = f"agent_{uuid.uuid4().hex[:8]}"
        queued = TeammateQueueTask(runtime_id=runtime_id, task=task, context=context, run_id=run_id, team_task_id=team_task_id)
        self._queue.put(TeammateQueueItem(type="task", task=queued))
        self._sync_queue_depth()
        self._emit_status()
        return runtime_id

    def send_message(self, sender: str, content: str) -> None:
        if not self.is_alive():
            return
        self._queue.put(TeammateQueueItem(type="message", sender=sender, content=content))
        self._sync_queue_depth()
        self._emit_status()

    def cancel(self, reason: str = "") -> bool:
        if not self.is_alive():
            return False
        self._cancel_event.set()
        with self._lock:
            self.state.status = "cancelling"
            self.state.last_error = reason
            self.state.last_active_at = time.time()
        if self._current_task is None and self.callbacks.on_cancelled:
            self.callbacks.on_cancelled(self.name, None, reason or "cancel requested", self.snapshot())
        self._emit_status()
        return True

    def shutdown(self, reason: str = "", force: bool = False) -> bool:
        if not self.is_alive():
            with self._lock:
                self.state.status = "stopped"
                self.state.last_error = reason
                self.state.last_active_at = time.time()
            if self.callbacks.on_shutdown:
                self.callbacks.on_shutdown(self.name, self.snapshot())
            return False
        self._shutdown_event.set()
        self._cancel_event.set()
        self._queue.put(TeammateQueueItem(type="shutdown", reason=reason, force=force))
        if force:
            self._clear_pending_tasks()
        with self._lock:
            self.state.status = "stopping"
            self.state.last_error = reason
            self.state.last_active_at = time.time()
        self._emit_status()
        return True

    def is_alive(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def snapshot(self) -> TeammateState:
        with self._lock:
            self.state.queue_depth = self._queue.qsize()
            return TeammateState(**self.state.__dict__)

    def _run(self) -> None:
        self._runner = self.runner_factory(
            self.role,
            self.instructions,
            model=self.model,
            llm_client=self.llm_client,
            tool_registry=self.tool_registry,
        )
        while not self._shutdown_event.is_set():
            try:
                item = self._queue.get(timeout=0.2)
            except queue.Empty:
                continue
            self._sync_queue_depth()
            if item.type == "shutdown":
                break
            if item.type == "message":
                self._pending_messages.append(f"[{item.sender or 'lead'}] {item.content}")
                continue
            if item.type == "task" and item.task is not None:
                self._run_task(item.task)
        with self._lock:
            self.state.status = "stopped"
            self.state.runtime_id = ""
            self.state.current_task = ""
            self.state.current_task_id = ""
            self.state.queue_depth = self._queue.qsize()
            self.state.last_active_at = time.time()
        if self.callbacks.on_shutdown:
            self.callbacks.on_shutdown(self.name, self.snapshot())

    def _run_task(self, task: TeammateQueueTask) -> None:
        self._cancel_event.clear()
        self._current_task = task
        with self._lock:
            self.state.status = "working"
            self.state.runtime_id = task.runtime_id
            self.state.current_task_id = task.team_task_id
            self.state.current_task = task.task
            self.state.last_error = ""
            self.state.last_active_at = time.time()
        if self.callbacks.on_started:
            self.callbacks.on_started(self.name, task, self.snapshot())
        try:
            context = self._context_with_messages(task.context)
            result = self._call_runner(task.task, context)
            if self._cancel_event.is_set():
                with self._lock:
                    self.state.status = "cancelled"
                    self.state.last_error = "cancel requested"
                if self.callbacks.on_cancelled:
                    self.callbacks.on_cancelled(self.name, task, "cancel requested", self.snapshot())
            else:
                with self._lock:
                    self.state.status = "idle"
                    self.state.current_task = ""
                    self.state.current_task_id = ""
                    self.state.last_active_at = time.time()
                if self.callbacks.on_done:
                    self.callbacks.on_done(self.name, task, result, self.snapshot())
        except Exception as exc:
            message = f"{type(exc).__name__}: {exc}"
            with self._lock:
                self.state.status = "failed"
                self.state.last_error = message
                self.state.last_active_at = time.time()
            if self.callbacks.on_failed:
                self.callbacks.on_failed(self.name, task, message, self.snapshot())
        finally:
            self._current_task = None
            if self.state.status in {"cancelled", "failed"} and not self._shutdown_event.is_set():
                with self._lock:
                    if self.state.status == "cancelled":
                        self.state.status = "idle"
                    self.state.runtime_id = ""
                    self.state.current_task = ""
                    self.state.current_task_id = ""
                    self.state.queue_depth = self._queue.qsize()
            self._emit_status()

    def _call_runner(self, task: str, context: str) -> str:
        run = self._runner.run
        try:
            params = inspect.signature(run).parameters
        except (TypeError, ValueError):
            return str(run(task, context=context))
        kwargs: dict[str, Any] = {"context": context}
        if "cancel_event" in params:
            kwargs["cancel_event"] = self._cancel_event
        if "should_cancel" in params:
            kwargs["should_cancel"] = self._cancel_event.is_set
        return str(run(task, **kwargs))

    def _context_with_messages(self, context: str) -> str:
        if not self._pending_messages:
            return context
        messages = "\n".join(self._pending_messages)
        self._pending_messages = []
        if context:
            return f"{context}\n\nInbox:\n{messages}"
        return f"Inbox:\n{messages}"

    def _clear_pending_tasks(self) -> None:
        kept: list[TeammateQueueItem] = []
        while True:
            try:
                item = self._queue.get_nowait()
            except queue.Empty:
                break
            if item.type != "task":
                kept.append(item)
        for item in kept:
            self._queue.put(item)
        self._sync_queue_depth()

    def _sync_queue_depth(self) -> None:
        with self._lock:
            self.state.queue_depth = self._queue.qsize()
            self.state.last_active_at = time.time()

    def _emit_status(self) -> None:
        if self.callbacks.on_status:
            self.callbacks.on_status(self.name, self.snapshot())


class TeammateRegistry:
    def __init__(self, callbacks: TeammateCallbacks, runner_factory: Callable[..., Any] | None = None):
        self.callbacks = callbacks
        self.runner_factory = runner_factory
        self._workers: dict[str, TeammateWorker] = {}
        self._lock = threading.Lock()

    def start(self, member: Any, *, model: str, llm_client: Any, tool_registry: Any) -> TeammateState:
        worker = self._ensure_worker(member, model=model, llm_client=llm_client, tool_registry=tool_registry)
        return worker.start()

    def submit_task(
        self,
        member: Any,
        task: str,
        *,
        context: str,
        run_id: str,
        team_task_id: str,
        model: str,
        llm_client: Any,
        tool_registry: Any,
    ) -> str:
        worker = self._ensure_worker(member, model=model, llm_client=llm_client, tool_registry=tool_registry)
        return worker.submit_task(task, context=context, run_id=run_id, team_task_id=team_task_id)

    def send_message(self, name: str, sender: str, content: str) -> None:
        worker = self._workers.get(_safe_name(name))
        if worker:
            worker.send_message(sender, content)

    def cancel(self, name: str, reason: str = "") -> bool:
        worker = self._workers.get(_safe_name(name))
        return worker.cancel(reason) if worker else False

    def shutdown(self, name: str, reason: str = "", force: bool = False) -> bool:
        worker = self._workers.get(_safe_name(name))
        return worker.shutdown(reason, force=force) if worker else False

    def state(self, name: str) -> TeammateState | None:
        worker = self._workers.get(_safe_name(name))
        return worker.snapshot() if worker else None

    def _ensure_worker(self, member: Any, *, model: str, llm_client: Any, tool_registry: Any) -> TeammateWorker:
        name = _safe_name(member.name)
        with self._lock:
            worker = self._workers.get(name)
            if worker and worker.is_alive():
                return worker
            worker = TeammateWorker(
                name=name,
                role=member.role,
                instructions=member.instructions,
                model=model,
                llm_client=llm_client,
                tool_registry=tool_registry,
                callbacks=self.callbacks,
                runner_factory=self.runner_factory,
            )
            self._workers[name] = worker
            return worker


def _safe_name(name: str) -> str:
    return "".join(ch for ch in name.strip().lower() if ch.isalnum() or ch in ("-", "_"))
