"""Swarm worker wrapper around the existing SubAgent."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from ..llm import MODEL
from ..subagent import SubAgent
from .audit import QualityAuditPipeline
from .blackboard import SwarmBlackboard, SwarmBlackboardEntry
from .models import SwarmAgentSpec, SwarmTask, WorkerResult, WorkerStatus


def run_swarm_worker(
    *,
    agent_spec: SwarmAgentSpec,
    task: SwarmTask,
    upstream_summaries: dict[str, str],
    user_vars: dict[str, str],
    blackboard: SwarmBlackboard,
    artifact_dir: Path,
    grounding_text: str = "",
    spawn_callback: Any = None,
    tool_registry: Any = None,
    llm_client: Any = None,
    runner_factory: Any = SubAgent,
    audit: QualityAuditPipeline | None = None,
    blackboard_callback: Callable[[SwarmBlackboardEntry], None] | None = None,
) -> WorkerResult:
    audit = audit or QualityAuditPipeline()
    context = _build_context(task, upstream_summaries, blackboard, grounding_text)
    prompt = _render_template(task.prompt_template, user_vars)
    tools = _SwarmToolRegistry(
        tool_registry,
        agent_spec.tools,
        blackboard,
        agent_spec.id,
        task.id,
        spawn_callback,
        blackboard_callback,
    )
    instructions = _build_instructions(agent_spec, task, artifact_dir)
    model = agent_spec.model_name or MODEL
    runner = runner_factory(
        agent_spec.role,
        instructions,
        model=model,
        llm_client=llm_client,
        tool_registry=tools,
    )

    result_text = str(runner.run(prompt, context=context, timeout_seconds=task_timeout(agent_spec)))
    report = audit.validate(result_text, task.acceptance)
    if not report.ok:
        return WorkerResult(
            status=WorkerStatus.incomplete,
            summary=result_text,
            error=report.message,
        )

    artifact_path = artifact_dir / "summary.md"
    artifact_path.write_text(result_text, encoding="utf-8")
    entry = blackboard.publish(f"result.{task.id}", result_text, agent_spec.id, task.id)
    if blackboard_callback is not None:
        blackboard_callback(entry)
    return WorkerResult(
        status=WorkerStatus.completed,
        summary=result_text,
        artifacts=[str(artifact_path)],
        iterations=1,
    )


def task_timeout(agent_spec: SwarmAgentSpec) -> int:
    return max(1, int(agent_spec.timeout_seconds or 300))


def _build_context(
    task: SwarmTask,
    upstream_summaries: dict[str, str],
    blackboard: SwarmBlackboard,
    grounding_text: str = "",
) -> str:
    parts: list[str] = []
    if grounding_text.strip():
        parts.append("## Grounding\n" + grounding_text.strip())
    if task.context_policy in {"upstream", "both"} and upstream_summaries:
        parts.append("## Upstream Results")
        for key, value in upstream_summaries.items():
            parts.append(f"### {key}\n{value}")
    if task.context_policy in {"blackboard", "both"}:
        digest = blackboard.digest()
        if digest:
            parts.append(digest)
    if task.acceptance:
        parts.append("## Acceptance Criteria\n" + "\n".join(f"- {item}" for item in task.acceptance))
    return "\n\n".join(parts)


def _build_instructions(agent_spec: SwarmAgentSpec, task: SwarmTask, artifact_dir: Path) -> str:
    parts = []
    if agent_spec.system_prompt:
        parts.append(agent_spec.system_prompt)
    parts.append(
        "You are participating in a FunHarness swarm run. "
        "Return a concrete final deliverable, not only a plan. "
        "Use blackboard_publish to share important findings and blackboard_read to inspect shared findings when useful."
    )
    parts.append(f"Artifact directory for this task: {artifact_dir}")
    if task.allow_spawn:
        parts.append("This task may request follow-up work by clearly listing proposed child tasks in the final answer.")
    return "\n\n".join(parts)


def _render_template(template: str, variables: dict[str, str]) -> str:
    class _Fallback(dict):
        def __missing__(self, key: str) -> str:
            return "{" + key + "}"

    try:
        return template.format_map(_Fallback(variables))
    except ValueError:
        return template


class _SwarmToolRegistry:
    def __init__(
        self,
        base_registry: Any,
        allowed_tools: list[str],
        blackboard: SwarmBlackboard,
        agent_id: str,
        task_id: str,
        spawn_callback: Any = None,
        blackboard_callback: Callable[[SwarmBlackboardEntry], None] | None = None,
    ) -> None:
        self.base_registry = base_registry
        self.allowed_tools = set(allowed_tools)
        self.blackboard = blackboard
        self.agent_id = agent_id
        self.task_id = task_id
        self.spawn_callback = spawn_callback
        self.blackboard_callback = blackboard_callback

    def get_openai_schemas(self) -> list[dict]:
        schemas: list[dict] = []
        if self.base_registry is not None:
            for name in self.allowed_tools:
                schema = getattr(self.base_registry, "get_schema", lambda _name: None)(name)
                if schema:
                    schemas.append(schema)
        schemas.extend([_publish_schema(), _read_schema()])
        if self.spawn_callback is not None:
            schemas.append(_spawn_schema())
        return schemas

    def get_function(self, name: str):
        if name == "blackboard_publish":
            return self.blackboard_publish
        if name == "blackboard_read":
            return self.blackboard_read
        if name == "swarm_spawn_task" and self.spawn_callback is not None:
            return self.swarm_spawn_task
        if name in self.allowed_tools and self.base_registry is not None:
            return self.base_registry.get_function(name)
        return None

    def blackboard_publish(self, key: str, value: str) -> str:
        entry = self.blackboard.publish(key, value, self.agent_id, self.task_id)
        if self.blackboard_callback is not None:
            self.blackboard_callback(entry)
        return f"published {entry.key}"

    def blackboard_read(self, pattern: str = "*") -> str:
        return self.blackboard.digest(pattern) or "(blackboard empty)"

    def swarm_spawn_task(
        self,
        title: str,
        prompt_template: str = "",
        agent_id: str = "",
        role: str = "",
        acceptance: str = "",
    ) -> str:
        task_id = self.spawn_callback(
            parent_task_id=self.task_id,
            title=title,
            prompt_template=prompt_template,
            agent_id=agent_id,
            role=role,
            acceptance=acceptance,
        )
        return f"spawned {task_id}"


def _publish_schema() -> dict:
    return {
        "type": "function",
        "function": {
            "name": "blackboard_publish",
            "description": "Publish a reusable finding to the shared swarm blackboard.",
            "parameters": {
                "type": "object",
                "properties": {
                    "key": {"type": "string", "description": "Short dotted key, e.g. finding.risk"},
                    "value": {"type": "string", "description": "Finding text to share"},
                },
                "required": ["key", "value"],
            },
        },
    }


def _read_schema() -> dict:
    return {
        "type": "function",
        "function": {
            "name": "blackboard_read",
            "description": "Read shared swarm blackboard entries matching a glob pattern.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Glob pattern, defaults to *"},
                },
                "required": [],
            },
        },
    }


def _spawn_schema() -> dict:
    return {
        "type": "function",
        "function": {
            "name": "swarm_spawn_task",
            "description": "Spawn a follow-up task in this swarm when a genuine gap needs more work.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Short task title"},
                    "prompt_template": {"type": "string", "description": "Detailed task prompt. Defaults to title."},
                    "agent_id": {"type": "string", "description": "Existing or new agent id. Defaults to current agent."},
                    "role": {"type": "string", "description": "Role for a new agent id, if needed."},
                    "acceptance": {"type": "string", "description": "Optional newline-separated acceptance checks."},
                },
                "required": ["title"],
            },
        },
    }
