"""
FunHarness - Subagents and Agent Teams

Subagents are one-shot isolated model calls. Teammates are persistent identities
with a roster and inbox; delegation runs through the runtime task lane.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

from .llm import client, MODEL
from .runtime import RuntimeTaskManager


@dataclass
class TeamMember:
    name: str
    role: str
    instructions: str = ""
    status: str = "idle"
    created_at: float = 0.0
    last_active_at: float = 0.0

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
        )


class SubAgent:
    def __init__(self, role: str, instructions: str = "", model: str = MODEL, llm_client=None):
        self.role = role
        self.instructions = instructions
        self.model = model
        self.llm_client = llm_client or client
        self.messages = [{"role": "system", "content": self._system_prompt()}]

    def _system_prompt(self) -> str:
        extra = f"\n\nRole instructions:\n{self.instructions}" if self.instructions else ""
        return (
            f"You are a focused subagent with role '{self.role}'. "
            "Work in an isolated context. Return concise, actionable results. "
            "Do not claim to have edited files unless a tool result or task text proves it."
            f"{extra}"
        )

    def run(self, task: str, context: str = "") -> str:
        content = task if not context else f"Context:\n{context}\n\nTask:\n{task}"
        self.messages.append({"role": "user", "content": content})
        response = self.llm_client.chat.completions.create(
            model=self.model,
            messages=self.messages,
            temperature=0.3,
            max_tokens=2000,
        )
        result = response.choices[0].message.content or ""
        self.messages.append({"role": "assistant", "content": result})
        return result


class TeamManager:
    def __init__(
        self,
        root: str | Path = ".funharness/team",
        runtime: RuntimeTaskManager | None = None,
        model: str = MODEL,
        llm_client=None,
    ):
        self.root = Path(root)
        self.inbox_dir = self.root / "inbox"
        self.history_dir = self.root / "history"
        self.config_path = self.root / "config.json"
        self.runtime = runtime
        self.model = model
        self.llm_client = llm_client or client
        self.root.mkdir(parents=True, exist_ok=True)
        self.inbox_dir.mkdir(parents=True, exist_ok=True)
        self.history_dir.mkdir(parents=True, exist_ok=True)
        self._members: dict[str, TeamMember] = {}
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
        return member

    def get(self, name: str) -> TeamMember | None:
        return self._members.get(_safe_name(name))

    def list(self) -> list[TeamMember]:
        return sorted(self._members.values(), key=lambda m: m.name)

    def send(self, sender: str, to: str, content: str) -> str:
        to = _safe_name(to)
        if to not in self._members:
            raise KeyError(to)
        envelope = {
            "type": "message",
            "from": sender or "lead",
            "to": to,
            "content": content,
            "timestamp": time.time(),
        }
        with self._inbox_path(to).open("a", encoding="utf-8") as f:
            f.write(json.dumps(envelope, ensure_ascii=False) + "\n")
        return f"Message sent to {to}"

    def drain_inbox(self, name: str) -> list[dict]:
        path = self._inbox_path(name)
        if not path.exists():
            return []
        lines = path.read_text(encoding="utf-8").splitlines()
        path.write_text("", encoding="utf-8")
        messages = []
        for line in lines:
            try:
                messages.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return messages

    def peek_inbox(self, name: str) -> list[dict]:
        path = self._inbox_path(name)
        if not path.exists():
            return []
        messages = []
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                messages.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return messages

    def delegate(self, to: str, task: str, context: str = "") -> str:
        if self.runtime is None:
            raise RuntimeError("TeamManager needs a RuntimeTaskManager for delegation")
        member = self.get(to)
        if member is None:
            raise KeyError(to)
        self.send("lead", member.name, task)
        member.status = "working"
        member.last_active_at = time.time()
        self._save()

        def _work():
            inbox = self.drain_inbox(member.name)
            inbox_text = "\n".join(
                f"[{item.get('from', '?')}] {item.get('content', '')}" for item in inbox
            )
            subagent = SubAgent(member.role, member.instructions, model=self.model, llm_client=self.llm_client)
            result = subagent.run(task, context=f"{context}\n\nInbox:\n{inbox_text}".strip())
            self._append_history(member.name, task, result)
            member.status = "idle"
            member.last_active_at = time.time()
            self._save()
            return f"[{member.name} / {member.role}]\n{result}"

        return self.runtime.submit_callable("agent", f"{member.name}: {task[:80]}", _work)

    def _append_history(self, name: str, task: str, result: str):
        record = {"task": task, "result": result, "timestamp": time.time()}
        path = self.history_dir / f"{_safe_name(name)}.jsonl"
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def _inbox_path(self, name: str) -> Path:
        return self.inbox_dir / f"{_safe_name(name)}.jsonl"

    def summary(self) -> str:
        members = self.list()
        if not members:
            return "(no teammates)"
        lines = ["Team:"]
        for member in members:
            inbox_count = len(self.peek_inbox(member.name))
            lines.append(
                f"  {member.name} [{member.role}] {member.status} "
                f"(inbox: {inbox_count})"
            )
        return "\n".join(lines)


def _safe_name(name: str) -> str:
    return "".join(ch for ch in name.strip().lower() if ch.isalnum() or ch in ("-", "_"))
