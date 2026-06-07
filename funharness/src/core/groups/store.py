"""File-backed persistence for group chat agents."""
from __future__ import annotations

import json
import shutil
import threading
import time
from pathlib import Path
from typing import Any

from .models import (
    AgentGroup,
    AgentProfile,
    GroupAgentRun,
    GroupAgentSession,
    GroupArtifact,
    GroupMember,
    GroupMessage,
)


class GroupStore:
    def __init__(self, root: str | Path = ".funharness/groups") -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def list_profiles(self) -> list[AgentProfile]:
        return sorted(self._read_list(self.root / "profiles.json", AgentProfile), key=lambda item: item.created_at)

    def save_profile(self, profile: AgentProfile) -> AgentProfile:
        profile.updated_at = time.time()
        with self._lock:
            profiles = {item.id: item for item in self.list_profiles()}
            profiles[profile.id] = profile
            self._write_json(self.root / "profiles.json", [item.to_dict() for item in profiles.values()])
        return profile

    def delete_profile(self, profile_id: str) -> None:
        with self._lock:
            profiles = [item for item in self.list_profiles() if item.id != profile_id]
            self._write_json(self.root / "profiles.json", [item.to_dict() for item in profiles])

    def get_profile(self, profile_id: str) -> AgentProfile | None:
        return next((item for item in self.list_profiles() if item.id == profile_id), None)

    def list_groups(self) -> list[AgentGroup]:
        return sorted(self._read_list(self.root / "groups.json", AgentGroup), key=lambda item: item.updated_at, reverse=True)

    def save_group(self, group: AgentGroup) -> AgentGroup:
        group.updated_at = time.time()
        with self._lock:
            groups = {item.id: item for item in self.list_groups()}
            groups[group.id] = group
            self._write_json(self.root / "groups.json", [item.to_dict() for item in groups.values()])
            self.group_dir(group.id).mkdir(parents=True, exist_ok=True)
        return group

    def delete_group(self, group_id: str) -> None:
        with self._lock:
            groups = [item for item in self.list_groups() if item.id != group_id]
            self._write_json(self.root / "groups.json", [item.to_dict() for item in groups])
            group = self.group_dir(group_id)
            if group.exists():
                shutil.rmtree(group)

    def get_group(self, group_id: str) -> AgentGroup | None:
        return next((item for item in self.list_groups() if item.id == group_id), None)

    def group_dir(self, group_id: str) -> Path:
        return self.root / group_id

    def list_members(self, group_id: str) -> list[GroupMember]:
        return sorted(self._read_list(self.group_dir(group_id) / "members.json", GroupMember), key=lambda item: item.created_at)

    def save_member(self, member: GroupMember) -> GroupMember:
        member.last_active_at = time.time()
        with self._lock:
            members = {item.id: item for item in self.list_members(member.group_id)}
            members[member.id] = member
            self._write_json(self.group_dir(member.group_id) / "members.json", [item.to_dict() for item in members.values()])
        return member

    def remove_member(self, group_id: str, member_id: str) -> None:
        with self._lock:
            members = [item for item in self.list_members(group_id) if item.id != member_id]
            self._write_json(self.group_dir(group_id) / "members.json", [item.to_dict() for item in members])

    def get_member(self, group_id: str, member_id: str) -> GroupMember | None:
        return next((item for item in self.list_members(group_id) if item.id == member_id), None)

    def append_message(self, message: GroupMessage) -> GroupMessage:
        path = self.group_dir(message.group_id) / "messages.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(message.to_dict(), ensure_ascii=False) + "\n")
            group = self.get_group(message.group_id)
            if group:
                group.updated_at = time.time()
                self.save_group(group)
        return message

    def update_message(self, message: GroupMessage) -> GroupMessage:
        path = self.group_dir(message.group_id) / "messages.jsonl"
        if not path.exists():
            return self.append_message(message)
        with self._lock:
            rows: list[dict[str, Any]] = []
            found = False
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except OSError:
                lines = []
            for line in lines:
                try:
                    data = json.loads(line)
                except (json.JSONDecodeError, TypeError):
                    continue
                if isinstance(data, dict) and data.get("id") == message.id:
                    rows.append(message.to_dict())
                    found = True
                elif isinstance(data, dict):
                    rows.append(data)
            if not found:
                rows.append(message.to_dict())
            self._write_jsonl(path, rows)
        return message

    def list_messages(self, group_id: str, limit: int = 200) -> list[GroupMessage]:
        path = self.group_dir(group_id) / "messages.jsonl"
        if not path.exists():
            return []
        messages: list[GroupMessage] = []
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        for line in lines[-limit:]:
            try:
                messages.append(GroupMessage.from_dict(json.loads(line)))
            except (json.JSONDecodeError, TypeError):
                continue
        return messages

    def get_or_create_session(self, group_id: str, member: GroupMember) -> GroupAgentSession:
        path = self.group_dir(group_id) / "sessions" / f"{member.id}.json"
        if path.exists():
            try:
                return GroupAgentSession.from_dict(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError):
                pass
        session = GroupAgentSession(group_id=group_id, member_id=member.id, profile_id=member.profile_id)
        self.save_session(session)
        return session

    def save_session(self, session: GroupAgentSession) -> GroupAgentSession:
        session.updated_at = time.time()
        path = self.group_dir(session.group_id) / "sessions" / f"{session.member_id}.json"
        self._write_json(path, session.to_dict())
        return session

    def save_run(self, run: GroupAgentRun) -> GroupAgentRun:
        path = self.group_dir(run.group_id) / "runs" / f"{run.id}.json"
        self._write_json(path, run.to_dict())
        return run

    def get_run(self, run_id: str) -> GroupAgentRun | None:
        for group in self.list_groups():
            path = self.group_dir(group.id) / "runs" / f"{run_id}.json"
            if not path.exists():
                continue
            try:
                return GroupAgentRun.from_dict(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError):
                return None
        return None

    def list_runs(self, group_id: str) -> list[GroupAgentRun]:
        runs_dir = self.group_dir(group_id) / "runs"
        if not runs_dir.exists():
            return []
        runs: list[GroupAgentRun] = []
        for path in sorted(runs_dir.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True):
            try:
                runs.append(GroupAgentRun.from_dict(json.loads(path.read_text(encoding="utf-8"))))
            except (OSError, json.JSONDecodeError):
                continue
        return runs

    def save_artifact(self, artifact: GroupArtifact, content: str | None = None) -> GroupArtifact:
        if content is not None:
            target = self.root.parent.parent / artifact.path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        path = self.group_dir(artifact.group_id) / "artifacts.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(artifact.to_dict(), ensure_ascii=False) + "\n")
        return artifact

    def list_artifacts(self, group_id: str) -> list[GroupArtifact]:
        path = self.group_dir(group_id) / "artifacts.jsonl"
        if not path.exists():
            return []
        artifacts: list[GroupArtifact] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                artifacts.append(GroupArtifact.from_dict(json.loads(line)))
            except (json.JSONDecodeError, TypeError):
                continue
        return artifacts

    def snapshot(self, group_id: str | None = None) -> dict[str, Any]:
        profiles = [item.to_dict() for item in self.list_profiles()]
        groups = [self.group_summary(item).to_dict() | self.group_counts(item.id) for item in self.list_groups()]
        payload: dict[str, Any] = {"profiles": profiles, "groups": groups}
        if group_id:
            payload["group"] = self.group_detail(group_id)
        return payload

    def group_summary(self, group: AgentGroup) -> AgentGroup:
        return group

    def group_counts(self, group_id: str) -> dict[str, Any]:
        members = [item for item in self.list_members(group_id) if item.active]
        messages = self.list_messages(group_id, limit=1)
        preview = messages[-1].content if messages else ""
        return {
            "member_count": len(members),
            "last_message": preview,
            "last_message_at": messages[-1].created_at if messages else 0.0,
        }

    def group_detail(self, group_id: str) -> dict[str, Any] | None:
        group = self.get_group(group_id)
        if group is None:
            return None
        return {
            **group.to_dict(),
            **self.group_counts(group_id),
            "members": [item.to_dict() for item in self.list_members(group_id)],
            "messages": [item.to_dict() for item in self.list_messages(group_id)],
            "runs": [item.to_dict() for item in self.list_runs(group_id)],
            "artifacts": [item.to_dict() for item in self.list_artifacts(group_id)],
        }

    def _read_list(self, path: Path, cls: Any) -> list[Any]:
        if not path.exists():
            return []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        if not isinstance(data, list):
            return []
        return [cls.from_dict(item) for item in data if isinstance(item, dict)]

    def _write_json(self, path: Path, data: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def _write_jsonl(self, path: Path, rows: list[dict[str, Any]]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
            encoding="utf-8",
        )
