"""File-backed shared blackboard for swarm workers."""
from __future__ import annotations

import fnmatch
import json
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class SwarmBlackboardEntry:
    key: str
    value: Any
    author: str
    task_id: str = ""
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SwarmBlackboardEntry":
        return cls(
            key=str(data.get("key", "")),
            value=data.get("value", ""),
            author=str(data.get("author", "")),
            task_id=str(data.get("task_id", "")),
            timestamp=float(data.get("timestamp", time.time())),
        )


class SwarmBlackboard:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def publish(self, key: str, value: Any, author: str, task_id: str = "") -> SwarmBlackboardEntry:
        entry = SwarmBlackboardEntry(key=key, value=value, author=author, task_id=task_id)
        with self._lock:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry.to_dict(), ensure_ascii=False) + "\n")
        return entry

    def read(self, pattern: str = "*") -> list[SwarmBlackboardEntry]:
        if not self.path.exists():
            return []
        entries: list[SwarmBlackboardEntry] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            try:
                entry = SwarmBlackboardEntry.from_dict(json.loads(line))
            except (json.JSONDecodeError, TypeError, ValueError):
                continue
            if fnmatch.fnmatch(entry.key, pattern):
                entries.append(entry)
        return entries

    def digest(self, pattern: str = "*", limit: int = 20) -> str:
        entries = self.read(pattern)[-limit:]
        if not entries:
            return ""
        lines = ["## Shared Blackboard"]
        for entry in entries:
            text = entry.value if isinstance(entry.value, str) else json.dumps(entry.value, ensure_ascii=False)
            lines.append(f"- `{entry.key}` by {entry.author}: {text}")
        return "\n".join(lines)
