"""Data models for FunHarness swarm runs."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class TaskStatus(str, Enum):
    pending = "pending"
    blocked = "blocked"
    in_progress = "in_progress"
    completed = "completed"
    failed = "failed"
    skipped = "skipped"
    cancelled = "cancelled"


class RunStatus(str, Enum):
    pending = "pending"
    running = "running"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"


class WorkerStatus(str, Enum):
    completed = "completed"
    failed = "failed"
    timeout = "timeout"
    incomplete = "incomplete"
    cancelled = "cancelled"


@dataclass
class SwarmAgentSpec:
    id: str
    role: str = "generalist"
    system_prompt: str = ""
    tools: list[str] = field(default_factory=list)
    model_name: str = ""
    max_iterations: int = 25
    timeout_seconds: int = 300
    max_retries: int = 1

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SwarmAgentSpec":
        return cls(
            id=str(data["id"]),
            role=str(data.get("role", "generalist")),
            system_prompt=str(data.get("system_prompt", data.get("instructions", ""))),
            tools=[str(item) for item in data.get("tools", [])],
            model_name=str(data.get("model_name", "")),
            max_iterations=int(data.get("max_iterations", 25)),
            timeout_seconds=int(data.get("timeout_seconds", 300)),
            max_retries=int(data.get("max_retries", 1)),
        )


@dataclass
class SwarmTask:
    id: str
    agent_id: str
    prompt_template: str
    depends_on: list[str] = field(default_factory=list)
    input_from: dict[str, str] = field(default_factory=dict)
    acceptance: list[str] = field(default_factory=list)
    context_policy: str = "both"
    allow_spawn: bool = False
    priority: int = 0
    status: TaskStatus = TaskStatus.pending
    summary: str = ""
    artifacts: list[str] = field(default_factory=list)
    error: str = ""
    started_at: float = 0.0
    completed_at: float = 0.0
    worker_iterations: int = 0
    retry_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        data = self.__dict__.copy()
        data["status"] = self.status.value
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SwarmTask":
        return cls(
            id=str(data["id"]),
            agent_id=str(data["agent_id"]),
            prompt_template=str(data.get("prompt_template", "")),
            depends_on=[str(item) for item in data.get("depends_on", [])],
            input_from={str(k): str(v) for k, v in data.get("input_from", {}).items()},
            acceptance=[str(item) for item in data.get("acceptance", [])],
            context_policy=str(data.get("context_policy", "both")),
            allow_spawn=bool(data.get("allow_spawn", False)),
            priority=int(data.get("priority", 0)),
            status=TaskStatus(data.get("status", "pending")),
            summary=str(data.get("summary", "")),
            artifacts=[str(item) for item in data.get("artifacts", [])],
            error=str(data.get("error", "")),
            started_at=float(data.get("started_at", 0.0)),
            completed_at=float(data.get("completed_at", 0.0)),
            worker_iterations=int(data.get("worker_iterations", 0)),
            retry_count=int(data.get("retry_count", 0)),
        )


@dataclass
class WorkerResult:
    status: WorkerStatus
    summary: str
    artifacts: list[str] = field(default_factory=list)
    iterations: int = 1
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        data = self.__dict__.copy()
        data["status"] = self.status.value
        return data


@dataclass
class SwarmEvent:
    type: str
    run_id: str
    agent_id: str = ""
    task_id: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SwarmEvent":
        return cls(
            type=str(data.get("type", "")),
            run_id=str(data.get("run_id", "")),
            agent_id=str(data.get("agent_id", "")),
            task_id=str(data.get("task_id", "")),
            data=dict(data.get("data", {})),
            timestamp=float(data.get("timestamp", time.time())),
        )


@dataclass
class SwarmRun:
    id: str
    preset_name: str = ""
    status: RunStatus = RunStatus.pending
    user_vars: dict[str, str] = field(default_factory=dict)
    agents: list[SwarmAgentSpec] = field(default_factory=list)
    tasks: list[SwarmTask] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    completed_at: float = 0.0
    final_report: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "preset_name": self.preset_name,
            "status": self.status.value,
            "user_vars": dict(self.user_vars),
            "agents": [item.to_dict() for item in self.agents],
            "tasks": [item.to_dict() for item in self.tasks],
            "created_at": self.created_at,
            "completed_at": self.completed_at,
            "final_report": self.final_report,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SwarmRun":
        return cls(
            id=str(data["id"]),
            preset_name=str(data.get("preset_name", "")),
            status=RunStatus(data.get("status", "pending")),
            user_vars={str(k): str(v) for k, v in data.get("user_vars", {}).items()},
            agents=[SwarmAgentSpec.from_dict(item) for item in data.get("agents", [])],
            tasks=[SwarmTask.from_dict(item) for item in data.get("tasks", [])],
            created_at=float(data.get("created_at", time.time())),
            completed_at=float(data.get("completed_at", 0.0)),
            final_report=str(data.get("final_report", "")),
        )
