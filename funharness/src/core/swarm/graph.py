"""DAG helpers for swarm task orchestration."""
from __future__ import annotations

from .models import SwarmTask, TaskStatus


def validate_dag(tasks: list[SwarmTask], agent_ids: set[str] | None = None) -> None:
    by_id = {task.id: task for task in tasks}
    if len(by_id) != len(tasks):
        raise ValueError("Duplicate swarm task id")
    if agent_ids is not None:
        for task in tasks:
            if task.agent_id not in agent_ids:
                raise ValueError(f"Task '{task.id}' references unknown agent '{task.agent_id}'")
    for task in tasks:
        for dep in task.depends_on:
            if dep not in by_id:
                raise ValueError(f"Task '{task.id}' depends on unknown task '{dep}'")

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(task_id: str) -> None:
        if task_id in visited:
            return
        if task_id in visiting:
            raise ValueError(f"Cycle detected at task '{task_id}'")
        visiting.add(task_id)
        for dep in by_id[task_id].depends_on:
            visit(dep)
        visiting.remove(task_id)
        visited.add(task_id)

    for task in tasks:
        visit(task.id)


def topological_layers(tasks: list[SwarmTask]) -> list[list[str]]:
    remaining = {task.id: set(task.depends_on) for task in tasks}
    done: set[str] = set()
    layers: list[list[str]] = []
    while remaining:
        ready = sorted(
            task_id
            for task_id, deps in remaining.items()
            if deps <= done
        )
        if not ready:
            raise ValueError("Cycle detected in swarm tasks")
        layers.append(ready)
        done.update(ready)
        for task_id in ready:
            remaining.pop(task_id)
    return layers


def terminal(status: TaskStatus) -> bool:
    return status in {TaskStatus.completed, TaskStatus.failed, TaskStatus.skipped, TaskStatus.cancelled}
