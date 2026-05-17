"""
FunHarness - Agent team run snapshots.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class TeamRunAgent:
    name: str
    role: str
    kind: str = "member"
    avatar_id: str = ""
    status: str = "idle"
    progress: int = 0
    current_task: str = ""
    input: str = ""
    output: str = ""
    runtime_id: str = ""
    started_at: float = 0.0
    finished_at: float = 0.0
    last_active_at: float = 0.0
    worker_id: str = ""
    queue_depth: int = 0
    current_task_id: str = ""
    last_error: str = ""

    def to_dict(self) -> dict:
        return self.__dict__.copy()

    @classmethod
    def from_dict(cls, data: dict) -> "TeamRunAgent":
        return cls(
            name=data["name"],
            role=data.get("role", "generalist"),
            kind=data.get("kind", "member"),
            avatar_id=data.get("avatar_id", ""),
            status=data.get("status", "idle"),
            progress=int(data.get("progress", 0)),
            current_task=data.get("current_task", ""),
            input=data.get("input", ""),
            output=data.get("output", ""),
            runtime_id=data.get("runtime_id", ""),
            started_at=float(data.get("started_at", 0.0)),
            finished_at=float(data.get("finished_at", 0.0)),
            last_active_at=float(data.get("last_active_at", 0.0)),
            worker_id=data.get("worker_id", ""),
            queue_depth=int(data.get("queue_depth", 0)),
            current_task_id=data.get("current_task_id", ""),
            last_error=data.get("last_error", ""),
        )


@dataclass
class TeamRunTask:
    task_id: str
    title: str
    description: str = ""
    owner: str = ""
    status: str = "pending"
    input: str = ""
    output: str = ""
    runtime_id: str = ""
    created_at: float = 0.0
    started_at: float = 0.0
    finished_at: float = 0.0

    def to_dict(self) -> dict:
        return self.__dict__.copy()

    @classmethod
    def from_dict(cls, data: dict) -> "TeamRunTask":
        return cls(
            task_id=data["task_id"],
            title=data.get("title", ""),
            description=data.get("description", ""),
            owner=data.get("owner", ""),
            status=data.get("status", "pending"),
            input=data.get("input", ""),
            output=data.get("output", ""),
            runtime_id=data.get("runtime_id", ""),
            created_at=float(data.get("created_at", 0.0)),
            started_at=float(data.get("started_at", 0.0)),
            finished_at=float(data.get("finished_at", 0.0)),
        )


@dataclass
class TeamRunMessage:
    message_id: str
    from_name: str
    to_name: str
    kind: str
    content: str
    edge_type: str = "message"
    timestamp: float = 0.0

    def to_dict(self) -> dict:
        data = self.__dict__.copy()
        data["from"] = data.pop("from_name")
        data["to"] = data.pop("to_name")
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "TeamRunMessage":
        return cls(
            message_id=data["message_id"],
            from_name=data.get("from") or data.get("from_name", ""),
            to_name=data.get("to") or data.get("to_name", ""),
            kind=data.get("kind", "message"),
            content=data.get("content", ""),
            edge_type=data.get("edge_type", "message"),
            timestamp=float(data.get("timestamp", 0.0)),
        )


@dataclass
class TeamRunEdge:
    source: str
    target: str
    type: str = "message"
    status: str = "idle"
    last_message: str = ""
    updated_at: float = 0.0

    @property
    def edge_id(self) -> str:
        return f"{self.source}->{self.target}:{self.type}"

    def to_dict(self) -> dict:
        data = self.__dict__.copy()
        data["edge_id"] = self.edge_id
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "TeamRunEdge":
        return cls(
            source=data["source"],
            target=data["target"],
            type=data.get("type", "message"),
            status=data.get("status", "idle"),
            last_message=data.get("last_message", ""),
            updated_at=float(data.get("updated_at", 0.0)),
        )


@dataclass
class TeamRunArtifact:
    artifact_id: str
    agent: str
    title: str
    content_preview: str
    runtime_id: str = ""
    created_at: float = 0.0

    def to_dict(self) -> dict:
        return self.__dict__.copy()

    @classmethod
    def from_dict(cls, data: dict) -> "TeamRunArtifact":
        return cls(
            artifact_id=data["artifact_id"],
            agent=data.get("agent", ""),
            title=data.get("title", ""),
            content_preview=data.get("content_preview", ""),
            runtime_id=data.get("runtime_id", ""),
            created_at=float(data.get("created_at", 0.0)),
        )


@dataclass
class TeamRun:
    run_id: str
    goal: str
    status: str = "idle"
    created_at: float = 0.0
    started_at: float = 0.0
    finished_at: float = 0.0
    agents: list[TeamRunAgent] | None = None
    tasks: list[TeamRunTask] | None = None
    edges: list[TeamRunEdge] | None = None
    messages: list[TeamRunMessage] | None = None
    artifacts: list[TeamRunArtifact] | None = None
    timeline: list[dict[str, Any]] | None = None

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "goal": self.goal,
            "status": self.status,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "agents": [item.to_dict() for item in self.agents or []],
            "tasks": [item.to_dict() for item in self.tasks or []],
            "edges": [item.to_dict() for item in self.edges or []],
            "messages": [item.to_dict() for item in self.messages or []],
            "artifacts": [item.to_dict() for item in self.artifacts or []],
            "timeline": list(self.timeline or []),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "TeamRun":
        return cls(
            run_id=data["run_id"],
            goal=data.get("goal", ""),
            status=data.get("status", "idle"),
            created_at=float(data.get("created_at", 0.0)),
            started_at=float(data.get("started_at", 0.0)),
            finished_at=float(data.get("finished_at", 0.0)),
            agents=[TeamRunAgent.from_dict(item) for item in data.get("agents", [])],
            tasks=[TeamRunTask.from_dict(item) for item in data.get("tasks", [])],
            edges=[TeamRunEdge.from_dict(item) for item in data.get("edges", [])],
            messages=[TeamRunMessage.from_dict(item) for item in data.get("messages", [])],
            artifacts=[TeamRunArtifact.from_dict(item) for item in data.get("artifacts", [])],
            timeline=list(data.get("timeline", [])),
        )


class TeamRunManager:
    def __init__(self, root: str | Path = ".funharness/team_runs"):
        self.root = Path(root)
        self.index_path = self.root / "index.json"
        self.root.mkdir(parents=True, exist_ok=True)

    def start(self, goal: str, members: list[Any]) -> TeamRun:
        now = time.time()
        run = TeamRun(
            run_id=f"teamrun_{time.time_ns()}",
            goal=goal.strip() or "Team collaboration run",
            status="running",
            created_at=now,
            started_at=now,
            agents=[TeamRunAgent(
                name="lead",
                role="lead",
                kind="leader",
                avatar_id="leader_rb",
                status="running",
                progress=12,
                current_task="Plan and coordinate team work",
                input=goal.strip(),
                started_at=now,
                last_active_at=now,
            )],
            tasks=[],
            edges=[],
            messages=[],
            artifacts=[],
            timeline=_default_timeline(now),
        )
        for member in members:
            self.add_agent_to_run(run, member)
        self._save(run)
        self._save_index(run.run_id)
        self.record_message(run.run_id, "lead", "team", "Team run started.", "status")
        return self.get(run.run_id) or run

    def current(self) -> TeamRun | None:
        try:
            data = json.loads(self.index_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        run_id = data.get("current_run_id", "")
        return self.get(run_id) if run_id else None

    def get(self, run_id: str) -> TeamRun | None:
        path = self.root / f"{_safe_name(run_id)}.json"
        if not path.exists():
            return None
        try:
            return TeamRun.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
            return None

    def snapshot(self, run_id: str | None = None) -> dict | None:
        run = self.get(run_id) if run_id else self.current()
        return run.to_dict() if run else None

    def add_agent(self, run_id: str, member: Any) -> TeamRun | None:
        run = self.get(run_id)
        if run is None:
            return None
        self.add_agent_to_run(run, member)
        self._save(run)
        return run

    def remove_agent(self, run_id: str, name: str) -> TeamRun | None:
        run = self.get(run_id)
        if run is None:
            return None
        safe_name = _safe_name(name)
        if safe_name == "lead":
            raise ValueError("Cannot remove lead from a team run")
        run.agents = [agent for agent in run.agents or [] if agent.name != safe_name]
        run.tasks = [task for task in run.tasks or [] if task.owner != safe_name]
        run.edges = [
            edge for edge in run.edges or []
            if edge.source != safe_name and edge.target != safe_name
        ]
        self._maybe_finish(run)
        self._save(run)
        return run

    def rename_agent(self, run_id: str, old_name: str, new_name: str) -> TeamRun | None:
        run = self.get(run_id)
        if run is None:
            return None
        old_safe = _safe_name(old_name)
        new_safe = _safe_name(new_name)
        if not old_safe or not new_safe:
            raise ValueError("Agent names are required")
        if old_safe == "lead" or new_safe == "lead":
            raise ValueError("Cannot rename the team lead")
        if any(agent.name == new_safe for agent in run.agents or []):
            raise ValueError(f"Agent already exists in this run: {new_safe}")

        changed = False
        for agent in run.agents or []:
            if agent.name == old_safe:
                agent.name = new_safe
                changed = True
        if not changed:
            return run
        for task in run.tasks or []:
            if task.owner == old_safe:
                task.owner = new_safe
        for edge in run.edges or []:
            if edge.source == old_safe:
                edge.source = new_safe
            if edge.target == old_safe:
                edge.target = new_safe
        for message in run.messages or []:
            if message.from_name == old_safe:
                message.from_name = new_safe
            if message.to_name == old_safe:
                message.to_name = new_safe
        for artifact in run.artifacts or []:
            if artifact.agent == old_safe:
                artifact.agent = new_safe
        self._save(run)
        return run

    def add_agent_to_run(self, run: TeamRun, member: Any) -> None:
        agents = run.agents or []
        if any(agent.name == member.name for agent in agents):
            return
        now = time.time()
        member_index = sum(1 for a in agents if a.kind != "leader")
        agents.append(TeamRunAgent(
            name=member.name,
            role=member.role,
            avatar_id=_avatar_id(member_index),
            status=member.status,
            progress=0,
            started_at=0.0,
            last_active_at=member.last_active_at or now,
            worker_id=getattr(member, "worker_id", ""),
            queue_depth=getattr(member, "queue_depth", 0),
            current_task_id=getattr(member, "current_task_id", ""),
            last_error=getattr(member, "last_error", ""),
        ))
        run.agents = agents
        self._upsert_edge(run, "lead", member.name, "assignment", "idle", "")

    def assign_task(self, run_id: str, owner: str, title: str, description: str = "") -> str:
        run = self.get(run_id)
        if run is None:
            return ""
        now = time.time()
        run.status = "running"
        run.finished_at = 0.0
        for item in run.timeline or []:
            if item.get("id") == "finish":
                item["status"] = "pending"
                item["timestamp"] = 0.0
        tasks = run.tasks or []
        task_id = f"TR{len(tasks) + 1}"
        tasks.append(TeamRunTask(
            task_id=task_id,
            title=title[:120],
            description=description,
            owner=owner,
            status="in_progress",
            input=title,
            created_at=now,
            started_at=now,
        ))
        run.tasks = tasks
        self._update_agent(run, "lead", status="running", progress=35, current_task=f"Coordinating {owner}: {title}")
        self._update_agent(run, owner, status="working", progress=18, current_task=title, input=title)
        self._upsert_edge(run, "lead", owner, "assignment", "active", title)
        self._advance_timeline(run, "dispatch", now)
        self._save(run)
        self.record_message(run_id, "lead", owner, title, "assignment")
        return task_id

    def set_task_runtime(self, run_id: str, task_id: str, runtime_id: str) -> None:
        run = self.get(run_id)
        if run is None:
            return
        for task in run.tasks or []:
            if task.task_id == task_id:
                task.runtime_id = runtime_id
        owner = next((task.owner for task in run.tasks or [] if task.task_id == task_id), "")
        if owner:
            self._update_agent(run, owner, runtime_id=runtime_id)
        self._save(run)

    def update_task(
        self,
        run_id: str,
        task_id: str,
        *,
        status: str = "",
        owner: str = "",
        output: str = "",
    ) -> TeamRun | None:
        run = self.get(run_id)
        if run is None:
            return None
        normalized = _normalize_task_status(status)
        if status and not normalized:
            raise ValueError(f"Unknown team task status: {status}")
        target = next((task for task in run.tasks or [] if task.task_id == task_id), None)
        if target is None:
            raise KeyError(task_id)

        if owner:
            target.owner = _safe_name(owner)
        if output:
            target.output = output
        if normalized:
            now = time.time()
            target.status = normalized
            if normalized == "in_progress" and not target.started_at:
                target.started_at = now
            if normalized in {"done", "failed", "cancelled"}:
                target.finished_at = now

        if target.owner:
            agent_status = {
                "pending": "idle",
                "in_progress": "working",
                "done": "done",
                "failed": "failed",
                "cancelled": "cancelled",
            }.get(target.status)
            if agent_status:
                progress = 100 if agent_status == "done" else 35 if agent_status == "working" else None
                self._update_agent(run, target.owner, status=agent_status, progress=progress, output=target.output)
            if target.status in {"done", "failed", "cancelled"}:
                self._settle_assignment_edge(run, target.owner, target.status, target.output)

        self._maybe_finish(run)
        self._save(run)
        return run

    def update_agent(self, run_id: str, name: str, **updates: Any) -> TeamRun | None:
        run = self.get(run_id)
        if run is None:
            return None
        self._update_agent(run, name, **updates)
        if updates.get("status") in {"done", "failed", "cancelled"}:
            self._finish_task_for_agent(run, name, updates.get("status"), updates.get("output", ""))
        self._maybe_finish(run)
        self._save(run)
        return run

    def record_message(self, run_id: str, sender: str, to: str, content: str, edge_type: str = "message") -> TeamRun | None:
        run = self.get(run_id)
        if run is None:
            return None
        now = time.time()
        messages = run.messages or []
        messages.append(TeamRunMessage(
            message_id=f"msg_{len(messages) + 1}",
            from_name=sender,
            to_name=to,
            kind=edge_type,
            content=content,
            edge_type=edge_type,
            timestamp=now,
        ))
        run.messages = messages[-200:]
        if sender and to and to != "team":
            self._upsert_edge(run, sender, to, edge_type, "active", content)
        self._advance_timeline(run, "exchange", now)
        self._save(run)
        return run

    def record_artifact(self, run_id: str, agent: str, title: str, content: str, runtime_id: str = "") -> TeamRun | None:
        run = self.get(run_id)
        if run is None:
            return None
        now = time.time()
        artifacts = run.artifacts or []
        artifacts.append(TeamRunArtifact(
            artifact_id=f"artifact_{len(artifacts) + 1}",
            agent=agent,
            title=title,
            content_preview=content[:1200],
            runtime_id=runtime_id,
            created_at=now,
        ))
        run.artifacts = artifacts
        self._update_agent(run, agent, output=content)
        self._advance_timeline(run, "report", now)
        self._save(run)
        return run

    def record_lead_feedback(self, run_id: str, agent: str, task: str, result: str) -> TeamRun | None:
        run = self.get(run_id)
        if run is None:
            return None
        now = time.time()
        result_text = result.strip()
        task_text = task.strip() or "(no recorded task)"
        if result_text:
            review_note = (
                f"Review {agent}'s result for \"{task_text[:80]}\". "
                "The auto lead turn should either delegate a concrete follow-up or synthesize the final answer."
            )
        else:
            review_note = f"{agent} returned no usable content. The auto lead turn should reassign \"{task_text[:80]}\" with clearer acceptance criteria."
        feedback = (
            f"Lead received {agent}'s result.\n\n"
            f"Task:\n{task_text}\n\n"
            f"Member result:\n{result_text or '(no output)'}\n\n"
            f"Review note:\n{review_note}"
        )
        artifacts = run.artifacts or []
        artifacts.append(TeamRunArtifact(
            artifact_id=f"artifact_{len(artifacts) + 1}",
            agent="lead",
            title=f"Lead review for {agent}",
            content_preview=feedback[:1200],
            created_at=now,
        ))
        run.artifacts = artifacts
        lead_status = "done" if run.status in {"done", "failed", "cancelled"} else "running"
        lead_progress = 100 if lead_status == "done" else max(45, next((item.progress for item in run.agents or [] if item.name == "lead"), 0))
        self._update_agent(run, "lead", status=lead_status, progress=lead_progress, current_task=f"Reviewed {agent} result", output=feedback)
        messages = run.messages or []
        messages.append(TeamRunMessage(
            message_id=f"msg_{len(messages) + 1}",
            from_name="lead",
            to_name="team",
            kind="lead_feedback",
            content=feedback,
            edge_type="status",
            timestamp=now,
        ))
        run.messages = messages[-200:]
        self._upsert_edge(run, agent, "lead", "report", "active", f"{agent} reported result")
        self._advance_timeline(run, "report", now)
        self._save(run)
        return run
    def _save_index(self, run_id: str) -> None:
        _write_text_atomic(self.index_path, json.dumps({"current_run_id": run_id}, indent=2))

    def _save(self, run: TeamRun) -> None:
        path = self.root / f"{_safe_name(run.run_id)}.json"
        _write_text_atomic(path, json.dumps(run.to_dict(), indent=2, ensure_ascii=False))

    def _update_agent(self, run: TeamRun, name: str, **updates: Any) -> None:
        now = time.time()
        if updates.get("status") == "working":
            self._advance_timeline(run, "execute", now)
        for agent in run.agents or []:
            if agent.name != name:
                continue
            for key, value in updates.items():
                if hasattr(agent, key) and value is not None:
                    setattr(agent, key, value)
            if updates.get("status") in {"running", "working"}:
                agent.started_at = now
                agent.finished_at = 0.0
            if updates.get("status") in {"done", "failed", "cancelled", "stopped"}:
                agent.finished_at = now
                if updates.get("status") == "done":
                    agent.progress = max(agent.progress, 100)
            agent.last_active_at = now
            break

    def _finish_task_for_agent(self, run: TeamRun, name: str, status: str, output: str) -> None:
        now = time.time()
        for task in reversed(run.tasks or []):
            if task.owner == name and task.status == "in_progress":
                task.status = status
                task.output = output
                task.finished_at = now
                break
        self._settle_assignment_edge(run, name, status, output)

    def _settle_assignment_edge(self, run: TeamRun, name: str, status: str, message: str = "") -> None:
        for edge in run.edges or []:
            if edge.source == "lead" and edge.target == name and edge.type == "assignment":
                edge.status = status
                if message:
                    edge.last_message = message[:240]
                edge.updated_at = time.time()

    def _upsert_edge(self, run: TeamRun, source: str, target: str, edge_type: str, status: str, message: str) -> None:
        edges = run.edges or []
        for edge in edges:
            if edge.source == source and edge.target == target and edge.type == edge_type:
                edge.status = status
                edge.last_message = message[:240]
                edge.updated_at = time.time()
                run.edges = edges
                return
        edges.append(TeamRunEdge(source, target, edge_type, status, message[:240], time.time()))
        run.edges = edges

    def _advance_timeline(self, run: TeamRun, stage_id: str, timestamp: float) -> None:
        for item in run.timeline or []:
            if item.get("id") == stage_id:
                item["status"] = "active"
                item["timestamp"] = timestamp
            elif item.get("status") == "active":
                item["status"] = "done"

    def _maybe_finish(self, run: TeamRun) -> None:
        agents = [agent for agent in run.agents or [] if agent.kind != "leader"]
        active_statuses = {"working", "running", "cancelling", "queued"}
        if not agents or any(agent.status in active_statuses for agent in agents):
            return
        if any(agent.status == "failed" for agent in agents):
            run.status = "failed"
        elif any(agent.status == "cancelled" for agent in agents):
            run.status = "cancelled"
        elif any(agent.status == "done" for agent in agents):
            run.status = "done"
        else:
            return
        run.finished_at = time.time()
        for item in run.timeline or []:
            if item.get("id") == "finish":
                item["status"] = "done"
                item["timestamp"] = run.finished_at
            elif item.get("status") == "active":
                item["status"] = "done"


def _safe_name(name: str) -> str:
    return "".join(ch for ch in name.strip().lower() if ch.isalnum() or ch in ("-", "_"))


def _write_text_atomic(path: Path, text: str) -> None:
    tmp_path = path.with_name(f"{path.name}.{time.time_ns()}.tmp")
    try:
        tmp_path.write_text(text, encoding="utf-8")
        tmp_path.replace(path)
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass


def _normalize_task_status(status: str) -> str:
    value = status.strip().lower()
    if value in {"", "none"}:
        return ""
    if value == "completed":
        return "done"
    if value in {"pending", "in_progress", "done", "failed", "cancelled"}:
        return value
    return ""


def _avatar_id(index: int) -> str:
    return f"avatar-{(index % 8) + 1:02d}_rb"


def _default_timeline(started_at: float) -> list[dict[str, Any]]:
    return [
        {"id": "receive", "label": "Receive main task", "status": "done", "timestamp": started_at},
        {"id": "dispatch", "label": "Dispatch tasks", "status": "pending", "timestamp": 0.0},
        {"id": "execute", "label": "Parallel execution", "status": "pending", "timestamp": 0.0},
        {"id": "exchange", "label": "Exchange information", "status": "pending", "timestamp": 0.0},
        {"id": "report", "label": "Report results", "status": "pending", "timestamp": 0.0},
        {"id": "finish", "label": "Finish", "status": "pending", "timestamp": 0.0},
    ]

