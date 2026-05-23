"""YAML preset loading for FunHarness swarms."""
from __future__ import annotations

import time
import uuid
from pathlib import Path
from typing import Any

import yaml

from .graph import topological_layers, validate_dag
from .models import RunStatus, SwarmAgentSpec, SwarmRun, SwarmTask, TaskStatus

PRESETS_DIR = Path(__file__).resolve().parent / "presets"


def load_preset(name: str, presets_dir: str | Path | None = None) -> dict[str, Any]:
    root = Path(presets_dir) if presets_dir is not None else PRESETS_DIR
    path = root / f"{name}.yaml"
    if not path.exists():
        available = sorted(item.stem for item in root.glob("*.yaml")) if root.exists() else []
        raise FileNotFoundError(f"Swarm preset {name!r} not found. Available: {available}")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Swarm preset {name!r} must be a YAML mapping")
    return data


def list_presets(presets_dir: str | Path | None = None) -> list[dict[str, Any]]:
    root = Path(presets_dir) if presets_dir is not None else PRESETS_DIR
    if not root.exists():
        return []
    out: list[dict[str, Any]] = []
    for path in sorted(root.glob("*.yaml")):
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception:
            continue
        out.append({
            "name": data.get("name", path.stem),
            "title": data.get("title", ""),
            "description": data.get("description", ""),
            "agent_count": len(data.get("agents", [])),
            "task_count": len(data.get("tasks", [])),
        })
    return out


def build_run_from_preset(name: str, user_vars: dict[str, str], presets_dir: str | Path | None = None) -> SwarmRun:
    data = load_preset(name, presets_dir)
    agents = [SwarmAgentSpec.from_dict(item) for item in data.get("agents", [])]
    tasks: list[SwarmTask] = []
    for item in data.get("tasks", []):
        task = SwarmTask.from_dict(item)
        task.status = TaskStatus.blocked if task.depends_on else TaskStatus.pending
        tasks.append(task)
    validate_dag(tasks, {agent.id for agent in agents})
    return SwarmRun(
        id=f"swarm_{time.time_ns()}_{uuid.uuid4().hex[:6]}",
        preset_name=str(data.get("name", name)),
        status=RunStatus.pending,
        user_vars={str(k): str(v) for k, v in user_vars.items()},
        agents=agents,
        tasks=tasks,
    )


def inspect_preset(name: str, presets_dir: str | Path | None = None) -> dict[str, Any]:
    run = build_run_from_preset(name, {}, presets_dir)
    return {
        "name": run.preset_name,
        "valid": True,
        "agents": [agent.to_dict() for agent in run.agents],
        "tasks": [task.to_dict() for task in run.tasks],
        "layers": topological_layers(run.tasks),
    }
