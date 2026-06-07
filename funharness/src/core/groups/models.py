"""Data models for group chat agents."""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any


def new_id(prefix: str) -> str:
    return f"{prefix}_{time.time_ns()}_{uuid.uuid4().hex[:6]}"


@dataclass
class AgentProfile:
    id: str = field(default_factory=lambda: new_id("profile"))
    name: str = "架构师"
    role: str = "generalist"
    instructions: str = ""
    model_profile_id: str = ""
    enabled_skills: list[str] = field(default_factory=list)
    enabled_tools: list[str] = field(default_factory=list)
    permission_mode: str = "suggest"
    avatar: str = ""
    color: str = "#c49a5a"
    archived: bool = False
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AgentProfile":
        return cls(
            id=str(data.get("id") or new_id("profile")),
            name=str(data.get("name") or "架构师"),
            role=str(data.get("role") or "generalist"),
            instructions=str(data.get("instructions") or ""),
            model_profile_id=str(data.get("model_profile_id") or ""),
            enabled_skills=[str(item) for item in data.get("enabled_skills", [])],
            enabled_tools=[str(item) for item in data.get("enabled_tools", [])],
            permission_mode=str(data.get("permission_mode") or "suggest"),
            avatar=str(data.get("avatar") or ""),
            color=str(data.get("color") or "#c49a5a"),
            archived=bool(data.get("archived", False)),
            created_at=float(data.get("created_at") or time.time()),
            updated_at=float(data.get("updated_at") or time.time()),
        )


@dataclass
class AgentGroup:
    id: str = field(default_factory=lambda: new_id("group"))
    name: str = "多智能体设计组"
    description: str = ""
    archived: bool = False
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AgentGroup":
        return cls(
            id=str(data.get("id") or new_id("group")),
            name=str(data.get("name") or "多智能体设计组"),
            description=str(data.get("description") or ""),
            archived=bool(data.get("archived", False)),
            created_at=float(data.get("created_at") or time.time()),
            updated_at=float(data.get("updated_at") or time.time()),
        )


@dataclass
class GroupMember:
    id: str = field(default_factory=lambda: new_id("member"))
    group_id: str = ""
    profile_id: str = ""
    display_name: str = ""
    role: str = ""
    status: str = "idle"
    queue_depth: int = 0
    current_run_id: str = ""
    current_task: str = ""
    last_error: str = ""
    active: bool = True
    created_at: float = field(default_factory=time.time)
    last_active_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "GroupMember":
        return cls(
            id=str(data.get("id") or new_id("member")),
            group_id=str(data.get("group_id") or ""),
            profile_id=str(data.get("profile_id") or ""),
            display_name=str(data.get("display_name") or data.get("name") or ""),
            role=str(data.get("role") or ""),
            status=str(data.get("status") or "idle"),
            queue_depth=int(data.get("queue_depth") or 0),
            current_run_id=str(data.get("current_run_id") or ""),
            current_task=str(data.get("current_task") or ""),
            last_error=str(data.get("last_error") or ""),
            active=bool(data.get("active", True)),
            created_at=float(data.get("created_at") or time.time()),
            last_active_at=float(data.get("last_active_at") or time.time()),
        )


@dataclass
class GroupMessage:
    id: str = field(default_factory=lambda: new_id("msg"))
    group_id: str = ""
    sender_type: str = "user"
    sender_id: str = "user"
    sender_name: str = "你"
    content: str = ""
    mentions: list[str] = field(default_factory=list)
    run_id: str = ""
    reply_to: str = ""
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "GroupMessage":
        return cls(
            id=str(data.get("id") or new_id("msg")),
            group_id=str(data.get("group_id") or ""),
            sender_type=str(data.get("sender_type") or "user"),
            sender_id=str(data.get("sender_id") or "user"),
            sender_name=str(data.get("sender_name") or ""),
            content=str(data.get("content") or ""),
            mentions=[str(item) for item in data.get("mentions", [])],
            run_id=str(data.get("run_id") or ""),
            reply_to=str(data.get("reply_to") or ""),
            created_at=float(data.get("created_at") or time.time()),
        )


@dataclass
class GroupAgentSession:
    id: str = field(default_factory=lambda: new_id("session"))
    group_id: str = ""
    member_id: str = ""
    profile_id: str = ""
    private_context_summary: str = ""
    messages: list[dict[str, Any]] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "GroupAgentSession":
        return cls(
            id=str(data.get("id") or new_id("session")),
            group_id=str(data.get("group_id") or ""),
            member_id=str(data.get("member_id") or ""),
            profile_id=str(data.get("profile_id") or ""),
            private_context_summary=str(data.get("private_context_summary") or ""),
            messages=list(data.get("messages", [])),
            created_at=float(data.get("created_at") or time.time()),
            updated_at=float(data.get("updated_at") or time.time()),
        )


@dataclass
class GroupAgentRun:
    id: str = field(default_factory=lambda: new_id("grouprun"))
    session_id: str = ""
    group_id: str = ""
    member_id: str = ""
    profile_id: str = ""
    trigger_message_id: str = ""
    status: str = "queued"
    input_text: str = ""
    output_text: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    error: str = ""
    started_at: float = 0.0
    finished_at: float = 0.0
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "GroupAgentRun":
        return cls(
            id=str(data.get("id") or new_id("grouprun")),
            session_id=str(data.get("session_id") or ""),
            group_id=str(data.get("group_id") or ""),
            member_id=str(data.get("member_id") or ""),
            profile_id=str(data.get("profile_id") or ""),
            trigger_message_id=str(data.get("trigger_message_id") or ""),
            status=str(data.get("status") or "queued"),
            input_text=str(data.get("input_text") or ""),
            output_text=str(data.get("output_text") or ""),
            tool_calls=list(data.get("tool_calls", [])),
            artifacts=list(data.get("artifacts", [])),
            error=str(data.get("error") or ""),
            started_at=float(data.get("started_at") or 0.0),
            finished_at=float(data.get("finished_at") or 0.0),
            created_at=float(data.get("created_at") or time.time()),
        )


@dataclass
class GroupArtifact:
    id: str = field(default_factory=lambda: new_id("artifact"))
    group_id: str = ""
    member_id: str = ""
    run_id: str = ""
    title: str = ""
    path: str = ""
    kind: str = "document"
    preview: str = ""
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "GroupArtifact":
        return cls(
            id=str(data.get("id") or new_id("artifact")),
            group_id=str(data.get("group_id") or ""),
            member_id=str(data.get("member_id") or ""),
            run_id=str(data.get("run_id") or ""),
            title=str(data.get("title") or ""),
            path=str(data.get("path") or ""),
            kind=str(data.get("kind") or "document"),
            preview=str(data.get("preview") or ""),
            created_at=float(data.get("created_at") or time.time()),
        )
