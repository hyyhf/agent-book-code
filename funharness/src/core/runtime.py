"""
FunHarness - Runtime Task Lanes

Runtime tasks are active execution slots. They are not durable work goals; they
only answer "what is running, where is the output, and what completed?"
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from enum import Enum
from pathlib import Path
from typing import Callable, Any

from .permissions import SandboxExecutor


class RuntimeStatus(Enum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


class RuntimeTask:
    def __init__(self, runtime_id: str, kind: str, description: str):
        self.runtime_id = runtime_id
        self.kind = kind
        self.description = description
        self.status = RuntimeStatus.QUEUED
        self.created_at = time.time()
        self.started_at = 0.0
        self.finished_at = 0.0
        self.result_preview = ""
        self.output_file = ""
        self.error = ""

    def to_dict(self) -> dict:
        return {
            "runtime_id": self.runtime_id,
            "kind": self.kind,
            "description": self.description,
            "status": self.status.value,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "result_preview": self.result_preview,
            "output_file": self.output_file,
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "RuntimeTask":
        task = cls(data["runtime_id"], data.get("kind", "command"), data.get("description", ""))
        task.status = RuntimeStatus(data.get("status", "queued"))
        task.created_at = data.get("created_at", time.time())
        task.started_at = data.get("started_at", 0.0)
        task.finished_at = data.get("finished_at", 0.0)
        task.result_preview = data.get("result_preview", "")
        task.output_file = data.get("output_file", "")
        task.error = data.get("error", "")
        return task


class RuntimeTaskManager:
    def __init__(self, root: str | Path = ".funharness/runtime", work_dir: str | Path = "."):
        self.root = Path(root)
        self.work_dir = Path(work_dir)
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._tasks: dict[str, RuntimeTask] = {}
        self._cancel_events: dict[str, threading.Event] = {}
        self._notifications: list[dict] = []
        self._load_existing()

    def _load_existing(self):
        for path in self.root.glob("*.json"):
            try:
                task = RuntimeTask.from_dict(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError, ValueError):
                continue
            self._tasks[task.runtime_id] = task

    def _new_id(self, prefix: str) -> str:
        return f"{prefix}_{uuid.uuid4().hex[:8]}"

    def _save(self, task: RuntimeTask):
        path = self.root / f"{task.runtime_id}.json"
        path.write_text(json.dumps(task.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")

    def _finish(self, task: RuntimeTask, status: RuntimeStatus, output: str):
        task.status = status
        task.finished_at = time.time()
        task.output_file = str(self.root / f"{task.runtime_id}.log")
        Path(task.output_file).write_text(output, encoding="utf-8")
        task.result_preview = output[:1200]
        if status == RuntimeStatus.FAILED:
            task.error = task.result_preview
        self._save(task)
        self._notifications.append({
            "type": "runtime_completed",
            "runtime_id": task.runtime_id,
            "status": status.value,
            "preview": task.result_preview,
            "output_file": task.output_file,
        })

    def submit_command(self, command: str, description: str = "", timeout: int = 300) -> str:
        runtime_id = self._new_id("run")
        task = RuntimeTask(runtime_id, "command", description or command)
        cancel_event = threading.Event()
        with self._lock:
            self._tasks[runtime_id] = task
            self._cancel_events[runtime_id] = cancel_event
            self._save(task)

        def _run():
            with self._lock:
                task.status = RuntimeStatus.RUNNING
                task.started_at = time.time()
                self._save(task)
            executor = SandboxExecutor(work_dir=str(self.work_dir), timeout=timeout, max_output=50000)
            output = executor.execute(command, should_interrupt=cancel_event.is_set)
            if output.startswith("Interrupted:"):
                status = RuntimeStatus.CANCELLED
            else:
                status = RuntimeStatus.DONE if output.startswith("[exit=0]") else RuntimeStatus.FAILED
            with self._lock:
                self._finish(task, status, output)
                self._cancel_events.pop(runtime_id, None)

        threading.Thread(target=_run, daemon=True).start()
        return runtime_id

    def cancel(self, runtime_id: str) -> str:
        with self._lock:
            task = self._tasks.get(runtime_id)
            if not task:
                return f"Unknown runtime task: {runtime_id}"
            if task.status in {RuntimeStatus.DONE, RuntimeStatus.FAILED, RuntimeStatus.CANCELLED}:
                return f"Runtime task {runtime_id} is already {task.status.value}."
            cancel_event = self._cancel_events.get(runtime_id)
            if cancel_event is None:
                return f"Runtime task {runtime_id} cannot be cancelled by this process."
            cancel_event.set()
            return f"Cancellation requested for runtime task: {runtime_id}"

    def submit_callable(self, kind: str, description: str, fn: Callable[[], Any]) -> str:
        runtime_id = self._new_id(kind)
        task = RuntimeTask(runtime_id, kind, description)
        with self._lock:
            self._tasks[runtime_id] = task
            self._save(task)

        def _run():
            with self._lock:
                task.status = RuntimeStatus.RUNNING
                task.started_at = time.time()
                self._save(task)
            try:
                output = str(fn())
                status = RuntimeStatus.DONE
            except Exception as exc:
                output = f"{type(exc).__name__}: {exc}"
                status = RuntimeStatus.FAILED
            with self._lock:
                self._finish(task, status, output)

        threading.Thread(target=_run, daemon=True).start()
        return runtime_id

    def get(self, runtime_id: str) -> RuntimeTask | None:
        with self._lock:
            return self._tasks.get(runtime_id)

    def list(self) -> list[RuntimeTask]:
        with self._lock:
            return sorted(self._tasks.values(), key=lambda t: t.created_at, reverse=True)

    def output(self, runtime_id: str) -> str:
        task = self.get(runtime_id)
        if not task:
            return f"Unknown runtime task: {runtime_id}"
        if not task.output_file:
            return f"Runtime task {runtime_id} is {task.status.value}; no output yet."
        try:
            return Path(task.output_file).read_text(encoding="utf-8")
        except OSError as exc:
            return f"Failed to read output: {exc}"

    def drain_notifications(self) -> list[dict]:
        with self._lock:
            items = list(self._notifications)
            self._notifications.clear()
            return items

    def summary(self) -> str:
        tasks = self.list()
        if not tasks:
            return "(no runtime tasks)"
        lines = ["Runtime tasks:"]
        for task in tasks[:20]:
            elapsed_end = task.finished_at or time.time()
            elapsed = max(0.0, elapsed_end - (task.started_at or task.created_at))
            lines.append(
                f"  {task.runtime_id} [{task.status.value}] {task.kind}: "
                f"{task.description[:80]} ({elapsed:.1f}s)"
            )
        return "\n".join(lines)
