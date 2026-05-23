"""File-system persistence for FunHarness swarm runs."""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from .blackboard import SwarmBlackboard
from .models import SwarmEvent, SwarmRun, SwarmTask


class SwarmStore:
    def __init__(self, root: str | Path = ".funharness/swarm"):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def run_dir(self, run_id: str) -> Path:
        return self.root / _safe_name(run_id)

    def create_run(self, run: SwarmRun) -> None:
        rd = self.run_dir(run.id)
        rd.mkdir(parents=True, exist_ok=False)
        (rd / "tasks").mkdir()
        (rd / "artifacts").mkdir()
        self.save_run(run)
        for task in run.tasks:
            self.save_task(run.id, task)

    def save_run(self, run: SwarmRun) -> None:
        self._write_json_atomic(self.run_dir(run.id) / "run.json", run.to_dict())

    def load_run(self, run_id: str) -> SwarmRun | None:
        path = self.run_dir(run_id) / "run.json"
        if not path.exists():
            return None
        for attempt in range(5):
            try:
                return SwarmRun.from_dict(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
                if attempt == 4:
                    return None
                time.sleep(0.025 * (attempt + 1))

    def save_task(self, run_id: str, task: SwarmTask) -> None:
        self._write_json_atomic(self.run_dir(run_id) / "tasks" / f"{_safe_name(task.id)}.json", task.to_dict())

    def load_task(self, run_id: str, task_id: str) -> SwarmTask | None:
        path = self.run_dir(run_id) / "tasks" / f"{_safe_name(task_id)}.json"
        if not path.exists():
            return None
        try:
            return SwarmTask.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
            return None

    def append_event(self, event: SwarmEvent) -> None:
        path = self.run_dir(event.run_id) / "events.jsonl"
        with self._lock:
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(event.to_dict(), ensure_ascii=False) + "\n")

    def read_events(self, run_id: str) -> list[SwarmEvent]:
        path = self.run_dir(run_id) / "events.jsonl"
        if not path.exists():
            return []
        events: list[SwarmEvent] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                events.append(SwarmEvent.from_dict(json.loads(line)))
            except (json.JSONDecodeError, TypeError, ValueError):
                continue
        return events

    def blackboard(self, run_id: str) -> SwarmBlackboard:
        return SwarmBlackboard(self.run_dir(run_id) / "blackboard.jsonl")

    def artifact_dir(self, run_id: str, agent_id: str) -> Path:
        path = self.run_dir(run_id) / "artifacts" / _safe_name(agent_id)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _write_json_atomic(self, path: Path, data: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(data, indent=2, ensure_ascii=False)
        tmp_path = path.with_name(f"{path.name}.{time.time_ns()}.tmp")
        with self._lock:
            try:
                tmp_path.write_text(text, encoding="utf-8")
                _replace_with_retry(tmp_path, path)
            finally:
                if tmp_path.exists():
                    try:
                        tmp_path.unlink()
                    except OSError:
                        pass


def _safe_name(name: str) -> str:
    safe = "".join(ch for ch in name.strip().lower() if ch.isalnum() or ch in ("-", "_"))
    return safe or "unnamed"


def _replace_with_retry(tmp_path: Path, path: Path) -> None:
    delays = (0.025, 0.05, 0.1, 0.2, 0.4)
    for attempt in range(len(delays) + 1):
        try:
            tmp_path.replace(path)
            return
        except OSError:
            if attempt == len(delays):
                raise
            time.sleep(delays[attempt])
