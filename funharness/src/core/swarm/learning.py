"""Cross-run learning memory for swarm orchestration."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .models import SwarmRun, TaskStatus


@dataclass
class RoleStats:
    role: str
    runs: int = 0
    completed_tasks: int = 0
    failed_tasks: int = 0
    total_duration_seconds: float = 0.0
    last_summary: str = ""

    @property
    def success_rate(self) -> float:
        total = self.completed_tasks + self.failed_tasks
        return self.completed_tasks / total if total else 0.0

    def to_dict(self) -> dict[str, Any]:
        data = self.__dict__.copy()
        data["success_rate"] = self.success_rate
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RoleStats":
        return cls(
            role=str(data.get("role", "")),
            runs=int(data.get("runs", 0)),
            completed_tasks=int(data.get("completed_tasks", 0)),
            failed_tasks=int(data.get("failed_tasks", 0)),
            total_duration_seconds=float(data.get("total_duration_seconds", 0.0)),
            last_summary=str(data.get("last_summary", "")),
        )


class SwarmLearningMemory:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def record_run(self, run: SwarmRun) -> None:
        data = self.load()
        roles = {item["role"]: RoleStats.from_dict(item) for item in data.get("roles", [])}
        agents = {agent.id: agent for agent in run.agents}
        for task in run.tasks:
            agent = agents.get(task.agent_id)
            role = agent.role if agent else task.agent_id
            stats = roles.setdefault(role, RoleStats(role=role))
            stats.runs += 1
            if task.status == TaskStatus.completed:
                stats.completed_tasks += 1
            elif task.status == TaskStatus.failed:
                stats.failed_tasks += 1
            if task.started_at and task.completed_at and task.completed_at >= task.started_at:
                stats.total_duration_seconds += task.completed_at - task.started_at
            if task.summary:
                stats.last_summary = task.summary[:500]
        data["roles"] = [stats.to_dict() for stats in sorted(roles.values(), key=lambda item: item.role)]
        data["runs_recorded"] = int(data.get("runs_recorded", 0)) + 1
        data["last_run_id"] = run.id
        data["last_run_status"] = run.status.value
        data["updated_at"] = time.time()
        self._save(data)

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"roles": [], "runs_recorded": 0}
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"roles": [], "runs_recorded": 0}

    def recommend_roles(self, limit: int = 3) -> list[str]:
        stats = [RoleStats.from_dict(item) for item in self.load().get("roles", [])]
        stats.sort(key=lambda item: (item.success_rate, item.completed_tasks), reverse=True)
        return [item.role for item in stats[:limit] if item.completed_tasks]

    def _save(self, data: dict[str, Any]) -> None:
        tmp = self.path.with_name(f"{self.path.name}.{time.time_ns()}.tmp")
        try:
            tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self.path)
        finally:
            if tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass
