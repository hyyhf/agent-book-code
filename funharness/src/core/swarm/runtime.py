"""Swarm runtime: static DAG execution plus shared blackboard collaboration."""
from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable

from ..subagent import SubAgent
from .audit import QualityAuditPipeline
from .graph import validate_dag
from .learning import SwarmLearningMemory
from .models import RunStatus, SwarmAgentSpec, SwarmEvent, SwarmRun, SwarmTask, TaskStatus, WorkerResult, WorkerStatus
from .planner import AdaptiveTeamBuilder, debate_plan, progressive_plan
from .presets import build_run_from_preset
from .store import SwarmStore
from .worker import run_swarm_worker


class SwarmRuntime:
    def __init__(
        self,
        store: SwarmStore | None = None,
        *,
        tool_registry: Any = None,
        llm_client: Any = None,
        runner_factory: Any = None,
        grounding_provider: Any = None,
        max_workers: int = 4,
        presets_dir: str | None = None,
    ) -> None:
        self.store = store or SwarmStore()
        self.tool_registry = tool_registry
        self.llm_client = llm_client
        self.runner_factory = runner_factory or SubAgent
        self.grounding_provider = grounding_provider
        self.max_workers = max(1, int(max_workers))
        self.presets_dir = presets_dir
        self.audit = QualityAuditPipeline()
        self.learning = SwarmLearningMemory(self.store.root / "learning.json")
        self._threads: dict[str, threading.Thread] = {}
        self._cancel_events: dict[str, threading.Event] = {}
        self._lock = threading.RLock()

    def start_preset(
        self,
        preset_name: str,
        variables: dict[str, str] | None = None,
        *,
        wait: bool = False,
        live_callback: Callable[[SwarmEvent], None] | None = None,
    ) -> SwarmRun:
        run = build_run_from_preset(preset_name, variables or {}, self.presets_dir)
        return self.start_run(run, wait=wait, live_callback=live_callback)

    def start_custom(
        self,
        *,
        agents: list[SwarmAgentSpec],
        tasks: list[SwarmTask],
        variables: dict[str, str] | None = None,
        wait: bool = False,
        live_callback: Callable[[SwarmEvent], None] | None = None,
    ) -> SwarmRun:
        validate_dag(tasks, {agent.id for agent in agents})
        run = SwarmRun(
            id=f"swarm_{time.time_ns()}",
            preset_name="custom",
            user_vars=variables or {},
            agents=agents,
            tasks=tasks,
        )
        return self.start_run(run, wait=wait, live_callback=live_callback)

    def start_adaptive(
        self,
        goal: str,
        *,
        wait: bool = False,
        live_callback: Callable[[SwarmEvent], None] | None = None,
    ) -> SwarmRun:
        builder = AdaptiveTeamBuilder(self.learning.recommend_roles())
        plan = builder.build(goal)
        validate_dag(plan.tasks, {agent.id for agent in plan.agents})
        run = SwarmRun(
            id=f"swarm_{time.time_ns()}",
            preset_name="adaptive_team",
            user_vars={"goal": goal, "topic": goal, "motion": goal},
            agents=plan.agents,
            tasks=plan.tasks,
        )
        return self.start_run(run, wait=wait, live_callback=live_callback)

    def start_debate(
        self,
        motion: str,
        *,
        wait: bool = False,
        live_callback: Callable[[SwarmEvent], None] | None = None,
    ) -> SwarmRun:
        plan = debate_plan(motion)
        return self.start_custom(
            agents=plan.agents,
            tasks=plan.tasks,
            variables={"motion": motion, "goal": motion},
            wait=wait,
            live_callback=live_callback,
        )

    def start_progressive(
        self,
        goal: str,
        *,
        rounds: int = 2,
        wait: bool = False,
        live_callback: Callable[[SwarmEvent], None] | None = None,
    ) -> SwarmRun:
        plan = progressive_plan(goal, rounds=rounds)
        return self.start_custom(
            agents=plan.agents,
            tasks=plan.tasks,
            variables={"goal": goal, "topic": goal},
            wait=wait,
            live_callback=live_callback,
        )

    def learning_snapshot(self) -> dict[str, Any]:
        return self.learning.load()

    def start_run(
        self,
        run: SwarmRun,
        *,
        wait: bool = False,
        live_callback: Callable[[SwarmEvent], None] | None = None,
    ) -> SwarmRun:
        validate_dag(run.tasks, {agent.id for agent in run.agents})
        self.store.create_run(run)
        cancel_event = threading.Event()
        with self._lock:
            self._cancel_events[run.id] = cancel_event
        thread = threading.Thread(
            target=self._execute_run,
            args=(run.id, cancel_event, live_callback),
            name=f"swarm-{run.id}",
            daemon=True,
        )
        with self._lock:
            self._threads[run.id] = thread
        thread.start()
        if wait:
            thread.join()
            return self.get_run(run.id) or run
        return run

    def wait(self, run_id: str, timeout: float | None = None) -> SwarmRun | None:
        thread = self._threads.get(run_id)
        if thread:
            thread.join(timeout)
        return self.get_run(run_id)

    def get_run(self, run_id: str) -> SwarmRun | None:
        return self.store.load_run(run_id)

    def cancel_run(self, run_id: str) -> bool:
        event = self._cancel_events.get(run_id)
        if event is None:
            return False
        event.set()
        return True

    def spawn_task(self, run_id: str, parent_task_id: str, task: SwarmTask) -> str:
        """Add a task to a running or pending swarm.

        The scheduler reloads run state between layers, so spawned tasks become
        eligible once their dependencies are completed.
        """
        with self._lock:
            run = self.store.load_run(run_id)
            if run is None:
                raise KeyError(run_id)
            if any(existing.id == task.id for existing in run.tasks):
                raise ValueError(f"Swarm task already exists: {task.id}")
            if parent_task_id and parent_task_id not in {existing.id for existing in run.tasks}:
                raise ValueError(f"Parent task not found: {parent_task_id}")
            if parent_task_id and parent_task_id not in task.depends_on:
                task.depends_on.append(parent_task_id)
            task.status = TaskStatus.blocked if task.depends_on else TaskStatus.pending
            run.tasks.append(task)
            validate_dag(run.tasks, {agent.id for agent in run.agents})
            self.store.save_task(run.id, task)
            self.store.save_run(run)
            self._emit(run.id, "task_spawned", agent_id=task.agent_id, task_id=task.id, data={"parent": parent_task_id})
            return task.id

    def skip_task(self, run_id: str, task_id: str, reason: str = "") -> bool:
        with self._lock:
            run = self.store.load_run(run_id)
            if run is None:
                return False
            changed = False
            for task in run.tasks:
                if task.id == task_id and task.status in {TaskStatus.pending, TaskStatus.blocked}:
                    task.status = TaskStatus.skipped
                    task.error = reason
                    task.completed_at = time.time()
                    self.store.save_task(run.id, task)
                    changed = True
                    break
            if changed:
                self.store.save_run(run)
                self._emit(run.id, "task_skipped", task_id=task_id, data={"reason": reason})
            return changed

    def _execute_run(
        self,
        run_id: str,
        cancel_event: threading.Event,
        live_callback: Callable[[SwarmEvent], None] | None,
    ) -> None:
        run = self.store.load_run(run_id)
        if run is None:
            return
        run.status = RunStatus.running
        self.store.save_run(run)
        self._emit(run.id, "run_started", live_callback=live_callback)
        summaries: dict[str, str] = {}
        completion_order: list[str] = []
        ok = True
        layer_index = 0

        while True:
            run = self.store.load_run(run_id)
            if run is None:
                return
            agent_map = {agent.id: agent for agent in run.agents}
            task_map = {task.id: task for task in run.tasks}
            if cancel_event.is_set():
                ok = False
                for task in run.tasks:
                    if task.status in {TaskStatus.pending, TaskStatus.blocked, TaskStatus.in_progress}:
                        self._mark_task(run, task, TaskStatus.cancelled)
                break

            task_ids = _ready_task_ids(run.tasks)
            if not task_ids:
                unfinished = [task for task in run.tasks if task.status in {TaskStatus.pending, TaskStatus.blocked, TaskStatus.in_progress}]
                if unfinished:
                    ok = False
                    for task in unfinished:
                        task.status = TaskStatus.failed
                        task.error = "No executable tasks remain; dependencies may have failed or been skipped incorrectly."
                        task.completed_at = time.time()
                        self.store.save_task(run.id, task)
                    self.store.save_run(run)
                break

            self._emit(run.id, "layer_started", data={"layer": layer_index, "tasks": task_ids}, live_callback=live_callback)
            layer_results = self._execute_layer(run, task_ids, task_map, agent_map, summaries, live_callback)
            for task_id, result in layer_results.items():
                task = task_map[task_id]
                task.summary = result.summary
                task.artifacts = result.artifacts
                task.error = result.error
                task.worker_iterations = result.iterations
                task.completed_at = time.time()
                if result.status.value == "completed":
                    task.status = TaskStatus.completed
                    summaries[task_id] = result.summary
                    completion_order.append(task_id)
                    self._emit(run.id, "task_completed", task_id=task_id, data=result.to_dict(), live_callback=live_callback)
                else:
                    ok = False
                    task.status = TaskStatus.failed
                    self._emit(run.id, "task_failed", task_id=task_id, data=result.to_dict(), live_callback=live_callback)
                self.store.save_task(run.id, task)
            latest = self.store.load_run(run.id)
            if latest is not None:
                latest_by_id = {task.id: task for task in latest.tasks}
                for task_id in layer_results:
                    if task_id in latest_by_id:
                        latest_by_id[task_id] = task_map[task_id]
                latest.tasks = [latest_by_id.get(task.id, task) for task in latest.tasks]
                run = latest
            self.store.save_run(run)
            if not ok:
                break
            layer_index += 1

        run = self.store.load_run(run_id) or run
        run.status = RunStatus.cancelled if cancel_event.is_set() else RunStatus.completed if ok else RunStatus.failed
        run.completed_at = time.time()
        run.final_report = summaries.get(completion_order[-1], "") if completion_order else ""
        self.store.save_run(run)
        self.learning.record_run(run)
        self._emit(run.id, "run_completed", data={"status": run.status.value}, live_callback=live_callback)
        with self._lock:
            self._cancel_events.pop(run.id, None)

    def _execute_layer(
        self,
        run: SwarmRun,
        task_ids: list[str],
        task_map: dict[str, SwarmTask],
        agent_map: dict[str, SwarmAgentSpec],
        summaries: dict[str, str],
        live_callback: Callable[[SwarmEvent], None] | None,
    ) -> dict[str, WorkerResult]:
        results: dict[str, WorkerResult] = {}
        blackboard = self.store.blackboard(run.id)
        with ThreadPoolExecutor(max_workers=min(self.max_workers, max(1, len(task_ids)))) as executor:
            futures = {}
            for task_id in task_ids:
                task = task_map[task_id]
                task.status = TaskStatus.in_progress
                task.started_at = time.time()
                task.summary = _progress_message(task, 0)
                self.store.save_task(run.id, task)
                self.store.save_run(run)
                upstream = {
                    key: summaries[source_id]
                    for key, source_id in task.input_from.items()
                    if source_id in summaries
                }
                self._emit(
                    run.id,
                    "task_started",
                    agent_id=task.agent_id,
                    task_id=task.id,
                    data={"message": task.summary, "upstream": sorted(upstream)},
                    live_callback=live_callback,
                )
                self._emit(
                    run.id,
                    "agent_message",
                    agent_id=task.agent_id,
                    task_id=task.id,
                    data={
                        "kind": "started",
                        "message": f"{task.agent_id} 开始处理 {task.id}",
                        "upstream": sorted(upstream),
                    },
                    live_callback=live_callback,
                )
                progress_stop = threading.Event()
                progress_thread = threading.Thread(
                    target=self._progress_loop,
                    args=(run.id, task.id, task.agent_id, progress_stop, live_callback),
                    daemon=True,
                )
                progress_thread.start()
                future = executor.submit(
                    self._run_with_retries,
                    run,
                    task,
                    agent_map[task.agent_id],
                    upstream,
                    blackboard,
                    live_callback,
                )
                futures[future] = (task_id, progress_stop)
            for future in as_completed(futures):
                task_id, progress_stop = futures[future]
                progress_stop.set()
                try:
                    results[task_id] = future.result()
                except Exception as exc:
                    results[task_id] = WorkerResult(
                        status=WorkerStatus.failed,
                        summary="",
                        error=f"{type(exc).__name__}: {exc}",
                    )
        return results

    def _run_with_retries(
        self,
        run: SwarmRun,
        task: SwarmTask,
        agent: SwarmAgentSpec,
        upstream: dict[str, str],
        blackboard,
        live_callback: Callable[[SwarmEvent], None] | None = None,
    ) -> WorkerResult:
        last: WorkerResult | None = None
        max_attempts = max(1, agent.max_retries + 1)
        for attempt in range(max_attempts):
            if attempt and last is not None:
                upstream = dict(upstream)
                upstream["retry_guidance"] = (
                    "Previous attempt did not meet the output contract.\n"
                    f"Failure reasons: {last.error}\n"
                    "Revise the answer into a concrete final deliverable. Do not return only a plan."
                )
                task.retry_count = attempt
            last = run_swarm_worker(
                agent_spec=agent,
                task=task,
                upstream_summaries=upstream,
                user_vars=run.user_vars,
                blackboard=blackboard,
                artifact_dir=self.store.artifact_dir(run.id, agent.id),
                grounding_text=self._grounding_for(task, run.user_vars),
                spawn_callback=self._spawn_callback(run.id, agent.id) if task.allow_spawn else None,
                tool_registry=self.tool_registry,
                llm_client=self.llm_client,
                runner_factory=self.runner_factory,
                audit=self.audit,
                blackboard_callback=self._blackboard_callback(run.id, live_callback),
            )
            if last.status.value == "completed":
                return last
        assert last is not None
        return last

    def _progress_loop(
        self,
        run_id: str,
        task_id: str,
        agent_id: str,
        stop_event: threading.Event,
        live_callback: Callable[[SwarmEvent], None] | None,
    ) -> None:
        iteration = 1
        while not stop_event.wait(2.5):
            task = self.store.load_task(run_id, task_id)
            if task is None or task.status is not TaskStatus.in_progress:
                return
            task.worker_iterations = iteration
            task.summary = _progress_message(task, iteration)
            self.store.save_task(run_id, task)
            self._emit(
                run_id,
                "task_progress",
                agent_id=agent_id,
                task_id=task_id,
                data={"message": task.summary, "iteration": iteration},
                live_callback=live_callback,
            )
            iteration += 1

    def _blackboard_callback(
        self,
        run_id: str,
        live_callback: Callable[[SwarmEvent], None] | None,
    ):
        def callback(entry) -> None:
            data = entry.to_dict()
            self._emit(
                run_id,
                "blackboard_publish",
                agent_id=entry.author,
                task_id=entry.task_id,
                data=data,
                live_callback=live_callback,
            )
            self._emit(
                run_id,
                "agent_message",
                agent_id=entry.author,
                task_id=entry.task_id,
                data={
                    "kind": "blackboard",
                    "message": f"{entry.author} 向共享黑板发布了 {entry.key}",
                    "key": entry.key,
                },
                live_callback=live_callback,
            )

        return callback

    def _grounding_for(self, task: SwarmTask, variables: dict[str, str]) -> str:
        if self.grounding_provider is None:
            return ""
        ground = getattr(self.grounding_provider, "ground", None)
        if ground is None:
            return ""
        try:
            return str(ground(task, variables) or "")
        except Exception:
            return ""

    def _spawn_callback(self, run_id: str, current_agent_id: str):
        def spawn(
            *,
            parent_task_id: str,
            title: str,
            prompt_template: str = "",
            agent_id: str = "",
            role: str = "",
            acceptance: str = "",
        ) -> str:
            safe_title = title.strip() or "Follow-up task"
            target_agent = _safe_id(agent_id or current_agent_id)
            with self._lock:
                run = self.store.load_run(run_id)
                if run is None:
                    raise KeyError(run_id)
                if target_agent not in {agent.id for agent in run.agents}:
                    run.agents.append(SwarmAgentSpec(target_agent, role or "Follow-up specialist"))
                task = SwarmTask(
                    id=_unique_task_id(run.tasks, safe_title),
                    agent_id=target_agent,
                    prompt_template=prompt_template.strip() or safe_title,
                    depends_on=[parent_task_id] if parent_task_id else [],
                    input_from={"parent": parent_task_id} if parent_task_id else {},
                    acceptance=_parse_acceptance(acceptance),
                    context_policy="both",
                    priority=10,
                )
                run.tasks.append(task)
                validate_dag(run.tasks, {agent.id for agent in run.agents})
                self.store.save_task(run.id, task)
                self.store.save_run(run)
            self._emit(
                run_id,
                "task_spawned",
                agent_id=target_agent,
                task_id=task.id,
                data={"parent": parent_task_id, "tool": "swarm_spawn_task"},
            )
            return task.id

        return spawn

    def _mark_task(self, run: SwarmRun, task: SwarmTask, status: TaskStatus) -> None:
        task.status = status
        task.completed_at = time.time()
        self.store.save_task(run.id, task)

    def _emit(
        self,
        run_id: str,
        event_type: str,
        *,
        agent_id: str = "",
        task_id: str = "",
        data: dict | None = None,
        live_callback: Callable[[SwarmEvent], None] | None = None,
    ) -> None:
        event = SwarmEvent(type=event_type, run_id=run_id, agent_id=agent_id, task_id=task_id, data=data or {})
        self.store.append_event(event)
        if live_callback is not None:
            live_callback(event)


def _ready_task_ids(tasks: list[SwarmTask]) -> list[str]:
    completed = {task.id for task in tasks if task.status in {TaskStatus.completed, TaskStatus.skipped}}
    ready: list[SwarmTask] = []
    for task in tasks:
        if task.status not in {TaskStatus.pending, TaskStatus.blocked}:
            continue
        if all(dep in completed for dep in task.depends_on):
            task.status = TaskStatus.pending
            ready.append(task)
    ready.sort(key=lambda item: (-item.priority, item.id))
    return [task.id for task in ready]


def _progress_message(task: SwarmTask, iteration: int) -> str:
    phases = (
        "正在读取上游结果与共享黑板",
        "正在推理当前子任务并提取关键发现",
        "正在整理可复用结论，准备写入共享黑板",
        "仍在等待模型返回，任务没有卡住",
    )
    phase = phases[iteration % len(phases)]
    return f"{phase}。Agent `{task.agent_id}` 正在处理 `{task.id}`，任务没有卡住。"


def _safe_id(value: str) -> str:
    safe = "".join(ch for ch in value.strip().lower().replace(" ", "_") if ch.isalnum() or ch in ("-", "_"))
    return safe or "agent"


def _unique_task_id(tasks: list[SwarmTask], title: str) -> str:
    base = _safe_id(title)[:40] or "task"
    existing = {task.id for task in tasks}
    if base not in existing:
        return base
    idx = 2
    while f"{base}_{idx}" in existing:
        idx += 1
    return f"{base}_{idx}"


def _parse_acceptance(value: str) -> list[str]:
    return [line.strip() for line in value.replace(",", "\n").splitlines() if line.strip()]
