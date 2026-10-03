"""Managed commands with bounded waits, live output and explicit ownership."""
from __future__ import annotations

import atexit
import copy
import ctypes
import json
import math
import os
import re
import threading
import time
import uuid
import weakref
from contextlib import contextmanager
from contextvars import ContextVar
from enum import Enum
from pathlib import Path
from typing import Callable, Any

from .permissions import SandboxExecutor, _windows_pid_exists
from .command_shells import resolve_shell


class RuntimeStatus(Enum):
    QUEUED = "queued"
    RUNNING = "running"
    CANCELLING = "cancelling"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"
    LOST = "lost"


ACTIVE = {RuntimeStatus.QUEUED, RuntimeStatus.RUNNING, RuntimeStatus.CANCELLING}
_MANAGERS = weakref.WeakValueDictionary()
_MANAGERS_LOCK = threading.RLock()
_METADATA_LOCK = threading.RLock()
_SCOPE = ContextVar("funharness_command_scope", default=None)
_DEFAULTS = {}
_DEFAULT_LOCK = threading.Lock()


def _read_record(path):
    # Windows readers can briefly deny replacement of an open destination.
    # Serialize readers/writers in this backend; _save also retries external readers.
    with _METADATA_LOCK:
        return json.loads(path.read_text(encoding="utf-8"))


def command_timeout(value, *, background=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("timeout must be a finite number of seconds")
    if value == 0 and background:
        return None
    if not 0 < value <= 86400:
        raise ValueError("timeout must be 1..86400 seconds; 0 is only allowed for explicit background services")
    return value


def wait_seconds(milliseconds, maximum=30000):
    if isinstance(milliseconds, bool) or not isinstance(milliseconds, (int, float)) or not math.isfinite(milliseconds):
        raise ValueError("yield_time_ms must be a finite number")
    if not 0 <= milliseconds <= maximum:
        raise ValueError(f"yield_time_ms must be between 0 and {maximum}")
    return milliseconds / 1000


@contextmanager
def command_scope(manager, should_interrupt=None):
    token = _SCOPE.set((manager, should_interrupt))
    try:
        yield
    finally:
        _SCOPE.reset(token)


def current_commands():
    scope = _SCOPE.get()
    if scope is not None:
        return scope
    # Direct tool users get the same managed lifecycle and follow-up tools.
    cwd = str(Path.cwd().resolve())
    with _DEFAULT_LOCK:
        if cwd not in _DEFAULTS or _DEFAULTS[cwd]._closed:
            _DEFAULTS[cwd] = RuntimeTaskManager(work_dir=cwd, root=Path(cwd) / ".funharness/runtime")
        return _DEFAULTS[cwd], None


def _pid_alive(pid):
    if not isinstance(pid, int) or pid <= 0:
        return False
    if os.name == "nt":
        return _windows_pid_exists(pid)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def _process_identity(pid):
    """Creation identity supplements PID liveness when recovering saved records."""
    if os.name == "nt":
        api = ctypes.WinDLL("kernel32", use_last_error=True)
        api.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
        api.OpenProcess.restype = ctypes.c_void_p
        api.GetProcessTimes.argtypes = [ctypes.c_void_p] + [ctypes.POINTER(ctypes.c_uint64)] * 4
        api.GetProcessTimes.restype = ctypes.c_int
        api.CloseHandle.argtypes = [ctypes.c_void_p]
        api.CloseHandle.restype = ctypes.c_int
        handle = api.OpenProcess(0x1000, False, pid)
        if not handle:
            return None
        try:
            times = [ctypes.c_uint64() for _ in range(4)]
            if api.GetProcessTimes(handle, *(ctypes.byref(t) for t in times)):
                return str(times[0].value)
        finally:
            api.CloseHandle(handle)
    else:
        try:
            stat = Path(f"/proc/{pid}/stat").read_text()
            return stat[stat.rfind(")") + 2:].split()[19]
        except (OSError, IndexError):
            return None
    return None


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
        self.command = ""
        self.shell = "default"
        self.shell_executable = ""
        self.cwd = ""
        self.timeout = None
        self.background = False
        self.pid = None
        self.exit_code = None
        self.last_output_at = 0.0
        self.output_bytes = 0
        self.owner_pid = os.getpid()
        self.owner_start = _process_identity(self.owner_pid)
        self.owner_id = ""

    def to_dict(self) -> dict:
        data = dict(vars(self))
        data["status"] = self.status.value
        data["elapsed_seconds"] = round(max(0, (self.finished_at or time.time()) - (self.started_at or self.created_at)), 2)
        return data

    @classmethod
    def from_dict(cls, data):
        task = cls(data["runtime_id"], data.get("kind", "command"), data.get("description", ""))
        for key in vars(task):
            if key in data and key != "status":
                setattr(task, key, data[key])
        task.status = RuntimeStatus(data.get("status", "queued"))
        if "owner_pid" not in data:
            task.owner_pid = 0
        if "owner_start" not in data:
            task.owner_start = None
        return task


class RuntimeTaskManager:
    def __init__(self, root=".funharness/runtime", work_dir=".", max_commands=8):
        self.root = Path(root).resolve()
        self.work_dir = Path(work_dir).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._tasks = {}
        self._cancel_events = {}
        self._done_events = {}
        self._outputs = {}
        self._notifications = []
        self._closed = False
        self.max_commands = max_commands
        self.owner_id = uuid.uuid4().hex
        with _MANAGERS_LOCK:
            _MANAGERS[self.owner_id] = self
        self._load_existing()

    def _load_existing(self):
        for path in self.root.glob("*.json"):
            if path.stem in self._tasks:
                continue
            try:
                task = RuntimeTask.from_dict(_read_record(path))
                if path.stem != task.runtime_id:
                    continue
                self._recover_owner(task)
                self._tasks[task.runtime_id] = task
            except (OSError, KeyError, TypeError, json.JSONDecodeError, ValueError):
                continue

    def _recover_owner(self, task):
        if task.status not in ACTIVE:
            return
        identity = _process_identity(task.owner_pid) if task.owner_pid else None
        if (not _pid_alive(task.owner_pid) or
                (task.owner_start and identity and identity != task.owner_start) or
                (task.owner_pid == os.getpid() and task.owner_id not in _MANAGERS)):
            task.status = RuntimeStatus.LOST
            task.finished_at = time.time()
            task.error = "Runtime owner is unavailable; command was not resumed. Inspect artifacts before retrying."
            self._save(task)

    def _save(self, task):
        path = self.root / f"{task.runtime_id}.json"
        tmp = path.with_suffix(f".{self.owner_id}.tmp")
        with _METADATA_LOCK:
            try:
                tmp.write_text(json.dumps(task.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
                for attempt in range(6):
                    try:
                        tmp.replace(path)
                        break
                    except PermissionError as exc:
                        if os.name != "nt" or exc.winerror not in (5, 32, 33) or attempt == 5:
                            raise
                        time.sleep(.01)
            finally:
                tmp.unlink(missing_ok=True)

    def _store_output(self, task, output):
        # Bounded head/tail snapshot, refreshed during execution as well as on exit.
        unchanged = self._outputs.get(task.runtime_id) == output
        self._outputs[task.runtime_id] = output
        task.result_preview = output[-1200:]
        if not unchanged:
            Path(task.output_file).write_text(output, encoding="utf-8")

    def _finish(self, task, status, output):
        task.status = status
        task.finished_at = time.time()
        if status in {RuntimeStatus.FAILED, RuntimeStatus.TIMED_OUT}:
            task.error = output[:1200]
        try:
            self._store_output(task, output)
            self._save(task)
            self._outputs.pop(task.runtime_id, None)
        except OSError as exc:
            task.error = f"Runtime persistence failed: {exc}"
            if task.status == RuntimeStatus.DONE:
                task.status = RuntimeStatus.FAILED
        self._notifications.append({
            "type": "runtime_completed", "runtime_id": task.runtime_id,
            "status": task.status.value, "preview": task.result_preview, "output_file": task.output_file,
        })
        self._cancel_events.pop(task.runtime_id, None)
        self._done_events[task.runtime_id].set()

    def _register(self, kind, description):
        if self._closed:
            raise RuntimeError("Command runtime has been shut down")
        task = RuntimeTask(f"{kind}_{uuid.uuid4().hex[:12]}", kind, description)
        task.owner_id = self.owner_id
        task.output_file = str(self.root / f"{task.runtime_id}.log")
        self._tasks[task.runtime_id] = task
        self._done_events[task.runtime_id] = threading.Event()
        return task

    def submit_command(self, command, description="", timeout=300, *, background=True,
                       work_dir=None, should_interrupt=None, shell="default"):
        budget = command_timeout(timeout, background=background)
        selected_shell = resolve_shell(shell)
        if not isinstance(command, str) or not command.strip():
            raise ValueError("command must be a non-empty string")
        cancel_event = threading.Event()
        with self._lock:
            if len(self._cancel_events) >= self.max_commands:
                raise RuntimeError(f"At most {self.max_commands} commands may run at once; wait for or cancel an existing task")
            task = self._register("command", description or command)
            task.command, task.timeout, task.background = command, budget, background
            task.shell, task.shell_executable = selected_shell.name, selected_shell.executable
            task.cwd = str(Path(work_dir or self.work_dir).resolve())
            try:
                self._save(task)
            except OSError:
                self._tasks.pop(task.runtime_id, None)
                self._done_events.pop(task.runtime_id, None)
                raise
            self._cancel_events[task.runtime_id] = cancel_event

        def cancelled():
            return cancel_event.is_set() or bool(should_interrupt and should_interrupt())

        def progress(output, executor):
            with self._lock:
                if task.pid == executor.pid and task.output_bytes == executor.output_bytes:
                    return
                task.pid = executor.pid
                task.last_output_at = executor.last_output_at
                task.output_bytes = executor.output_bytes
                self._store_output(task, output)
                self._save(task)

        def run():
            executor = SandboxExecutor(work_dir=task.cwd, timeout=budget, max_output=50000, shell=selected_shell)
            try:
                with self._lock:
                    task.status = RuntimeStatus.CANCELLING if cancelled() else RuntimeStatus.RUNNING
                    task.started_at = time.time()
                    self._save(task)
                output = executor.execute(command, should_interrupt=cancelled, on_progress=progress)
                status = RuntimeStatus(executor.outcome)
            except BaseException as exc:
                output = f"Execution failed: {type(exc).__name__}: {exc}"
                status = RuntimeStatus.FAILED
            finally:
                with self._lock:
                    task.pid, task.exit_code = executor.pid, executor.exit_code
                    task.last_output_at, task.output_bytes = executor.last_output_at, executor.output_bytes
                    self._finish(task, status, output)

        try:
            threading.Thread(target=run, name=f"fh-command-{task.runtime_id}", daemon=True).start()
        except BaseException as exc:
            with self._lock:
                self._finish(task, RuntimeStatus.FAILED, f"Execution failed to start: {exc}")
            raise
        return task.runtime_id

    def cancel(self, runtime_id):
        owner = None
        with self._lock:
            task = self.get(runtime_id)
            if not task:
                return f"Unknown runtime task: {runtime_id}"
            if task.status not in ACTIVE:
                return f"Runtime task {runtime_id} is already {task.status.value}."
            event = self._cancel_events.get(runtime_id)
            if event is None:
                owner = _MANAGERS.get(task.owner_id)
                if owner is None or owner is self or owner.root != self.root:
                    return f"Runtime task {runtime_id} cannot be cancelled by this process."
            else:
                event.set()
                self._tasks[runtime_id].status = RuntimeStatus.CANCELLING
        # A new GUI session can control a still-owned command in this backend.
        # Never kill a PID read from a persisted record, or hold two manager locks.
        if owner:
            return owner.cancel(runtime_id)
        return f"Cancellation requested for runtime task: {runtime_id}"

    def cancel_commands(self, *, include_background=True):
        with self._lock:
            for runtime_id, event in self._cancel_events.items():
                task = self._tasks[runtime_id]
                if include_background or not task.background:
                    event.set()
                    task.status = RuntimeStatus.CANCELLING

    def shutdown(self, timeout=3):
        with self._lock:
            self._closed = True
            events = [self._done_events[rid] for rid in self._cancel_events]
            self.cancel_commands()
        deadline = time.monotonic() + timeout
        for event in events:
            event.wait(max(0, deadline - time.monotonic()))

    def submit_callable(self, kind: str, description: str, fn: Callable[[], Any]) -> str:
        with self._lock:
            task = self._register(kind, description)
            self._save(task)

        def run():
            try:
                with self._lock:
                    task.status = RuntimeStatus.RUNNING
                    task.started_at = time.time()
                    self._save(task)
                output, status = str(fn()), RuntimeStatus.DONE
            except BaseException as exc:
                output, status = f"{type(exc).__name__}: {exc}", RuntimeStatus.FAILED
            with self._lock:
                self._finish(task, status, output)
        threading.Thread(target=run, daemon=True).start()
        return task.runtime_id

    def get(self, runtime_id):
        with self._lock:
            task = self._tasks.get(runtime_id)
            if (task is None or task.owner_id != self.owner_id) and re.fullmatch(r"[A-Za-z0-9_-]+", runtime_id):
                try:
                    loaded = RuntimeTask.from_dict(_read_record(self.root / f"{runtime_id}.json"))
                    if loaded.runtime_id != runtime_id:
                        return None
                    task = loaded
                    self._recover_owner(task)
                    self._tasks[runtime_id] = task
                except (OSError, ValueError, KeyError, TypeError):
                    pass
            return copy.copy(task) if task else None

    def list(self):
        with self._lock:
            self._load_existing()
            return sorted((self.get(rid) for rid in self._tasks), key=lambda t: t.created_at, reverse=True)

    def pending_commands(self):
        with self._lock:
            return [copy.copy(t) for t in self._tasks.values()
                    if t.owner_id == self.owner_id and t.kind == "command"
                    and not t.background and t.status in ACTIVE]

    def output(self, runtime_id):
        with self._lock:
            if runtime_id in self._outputs:
                return self._outputs[runtime_id] or "(no output yet)"
            task = self.get(runtime_id)
        if not task:
            return f"Unknown runtime task: {runtime_id}"
        try:
            with Path(task.output_file).open(encoding="utf-8") as stream:
                return stream.read(100100) or "(no output yet)"
        except OSError:
            return f"Runtime task {runtime_id} is {task.status.value}; no output yet."

    def wait(self, runtime_id, yield_time_ms=10000, should_interrupt=None):
        duration = wait_seconds(yield_time_ms)
        deadline = time.monotonic() + duration
        while True:
            task = self.get(runtime_id)
            if not task:
                return f"Unknown runtime task: {runtime_id}"
            if should_interrupt and should_interrupt():
                self.cancel(runtime_id)
                return f"Interrupted: command cancellation requested; runtime_id={runtime_id}"
            if task.status not in ACTIVE or time.monotonic() >= deadline:
                break
            event = self._done_events.get(runtime_id)
            remaining = min(.05, max(0, deadline - time.monotonic()))
            if event:
                event.wait(remaining)
            else:
                time.sleep(remaining)
        data = task.to_dict()
        header = (f"[runtime_id={runtime_id} status={task.status.value} "
                  f"shell={task.shell} pid={task.pid} elapsed={data['elapsed_seconds']}s timeout={task.timeout}]\n")
        hint = ""
        if task.status in ACTIVE:
            hint = ("\nCommand is still running, not completed. Do not rerun it. "
                    "Use tool_runtime_wait(runtime_id, yield_time_ms=30000) before dependent work, "
                    "tool_runtime_output for current output, or tool_runtime_cancel to stop it.")
        return header + self.output(runtime_id) + ("\n" + task.error if task.error else "") + hint

    def drain_notifications(self):
        with self._lock:
            items = list(self._notifications)
            self._notifications.clear()
            return items

    def summary(self):
        tasks = self.list()
        if not tasks:
            return "(no runtime tasks)"
        return "Runtime tasks:\n" + "\n".join(
            f"  {t.runtime_id} [{t.status.value}] {t.kind}: {t.description[:80]} "
            f"({t.to_dict()['elapsed_seconds']:.1f}s)" for t in tasks[:20]
        )


def shutdown_all_commands():
    with _MANAGERS_LOCK:
        managers = list(_MANAGERS.values())
    for manager in managers:
        manager.shutdown()


atexit.register(shutdown_all_commands)
