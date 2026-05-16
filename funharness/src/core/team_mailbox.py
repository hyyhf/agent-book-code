"""
FunHarness - Agent team JSONL mailboxes.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

MailboxType = Literal["message", "task", "cancel", "shutdown", "status", "result"]


@dataclass
class TeamMailboxEnvelope:
    type: MailboxType
    from_name: str
    to_name: str
    content: str = ""
    timestamp: float = 0.0
    task_id: str = ""
    run_id: str = ""
    runtime_id: str = ""
    context: str = ""
    reason: str = ""
    status: str = ""
    result: str = ""

    def to_dict(self) -> dict[str, Any]:
        data = {
            "type": self.type,
            "from": self.from_name,
            "to": self.to_name,
            "content": self.content,
            "timestamp": self.timestamp or time.time(),
        }
        for key in ("task_id", "run_id", "runtime_id", "context", "reason", "status", "result"):
            value = getattr(self, key)
            if value:
                data[key] = value
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TeamMailboxEnvelope":
        return cls(
            type=data.get("type", "message"),
            from_name=data.get("from") or data.get("from_name", ""),
            to_name=data.get("to") or data.get("to_name", ""),
            content=data.get("content", ""),
            timestamp=float(data.get("timestamp", time.time())),
            task_id=data.get("task_id", ""),
            run_id=data.get("run_id", ""),
            runtime_id=data.get("runtime_id", ""),
            context=data.get("context", ""),
            reason=data.get("reason", ""),
            status=data.get("status", ""),
            result=data.get("result", ""),
        )


class TeamMailbox:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.inbox_dir = self.root / "inbox"
        self.history_dir = self.root / "history"
        self.inbox_dir.mkdir(parents=True, exist_ok=True)
        self.history_dir.mkdir(parents=True, exist_ok=True)

    def write(self, envelope: TeamMailboxEnvelope) -> None:
        path = self.inbox_path(envelope.to_name)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(envelope.to_dict(), ensure_ascii=False) + "\n")

    def send_message(self, sender: str, to: str, content: str) -> TeamMailboxEnvelope:
        envelope = TeamMailboxEnvelope(
            type="message",
            from_name=sender or "lead",
            to_name=_safe_name(to),
            content=content,
            timestamp=time.time(),
        )
        self.write(envelope)
        return envelope

    def write_task(
        self,
        sender: str,
        to: str,
        content: str,
        *,
        context: str = "",
        task_id: str = "",
        run_id: str = "",
        runtime_id: str = "",
    ) -> TeamMailboxEnvelope:
        envelope = TeamMailboxEnvelope(
            type="task",
            from_name=sender or "lead",
            to_name=_safe_name(to),
            content=content,
            context=context,
            task_id=task_id,
            run_id=run_id,
            runtime_id=runtime_id,
            timestamp=time.time(),
        )
        self.write(envelope)
        return envelope

    def peek(self, name: str) -> list[dict[str, Any]]:
        path = self.inbox_path(name)
        if not path.exists():
            return []
        return [item.to_dict() for item in self._read(path)]

    def drain(self, name: str) -> list[dict[str, Any]]:
        path = self.inbox_path(name)
        if not path.exists():
            return []
        items = [item.to_dict() for item in self._read(path)]
        path.write_text("", encoding="utf-8")
        return items

    def append_history(self, name: str, task: str, result: str) -> None:
        record = {"task": task, "result": result, "timestamp": time.time()}
        path = self.history_path(name)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    def inbox_path(self, name: str) -> Path:
        return self.inbox_dir / f"{_safe_name(name)}.jsonl"

    def history_path(self, name: str) -> Path:
        return self.history_dir / f"{_safe_name(name)}.jsonl"

    def clear(self, name: str) -> None:
        """Remove all inbox and history files for a teammate."""
        for path in (self.inbox_path(name), self.history_path(name)):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass

    def _read(self, path: Path) -> list[TeamMailboxEnvelope]:
        messages: list[TeamMailboxEnvelope] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                messages.append(TeamMailboxEnvelope.from_dict(json.loads(line)))
            except (json.JSONDecodeError, TypeError, ValueError):
                continue
        return messages


def _safe_name(name: str) -> str:
    return "".join(ch for ch in name.strip().lower() if ch.isalnum() or ch in ("-", "_"))
