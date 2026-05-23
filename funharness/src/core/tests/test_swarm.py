from __future__ import annotations

import tempfile
import threading
import time
import unittest
from pathlib import Path

from funharness.src.core.swarm import (
    RunStatus,
    SwarmAgentSpec,
    SwarmRun,
    SwarmStore,
    SwarmTask,
    SwarmRuntime,
    TaskStatus,
    StaticGroundingProvider,
)
from funharness.src.core.swarm.graph import topological_layers, validate_dag
from funharness.src.core.swarm.presets import build_run_from_preset, list_presets


class _RunnerFactory:
    def __init__(self, outputs: list[str] | None = None, block_event: threading.Event | None = None) -> None:
        self.outputs = list(outputs or [])
        self.calls: list[dict] = []
        self.block_event = block_event

    def __call__(self, role, instructions="", model="", llm_client=None, tool_registry=None):
        return _Runner(self, role, instructions, tool_registry)


class _Runner:
    def __init__(self, factory: _RunnerFactory, role: str, instructions: str, tool_registry) -> None:
        self.factory = factory
        self.role = role
        self.instructions = instructions
        self.tool_registry = tool_registry

    def run(self, task: str, context: str = "", timeout_seconds=None) -> str:
        self.factory.calls.append({
            "role": self.role,
            "task": task,
            "context": context,
            "tools": [schema["function"]["name"] for schema in self.tool_registry.get_openai_schemas()],
        })
        if "use spawn tool" in task:
            spawn = self.tool_registry.get_function("swarm_spawn_task")
            assert spawn is not None
            spawn(title="spawned followup", prompt_template="最终 spawned task", acceptance="contains: 最终报告")
            return "计划：我已经动态生成了后续任务，并说明了下一步协作安排。这是一段足够长的计划。"
        if self.factory.block_event is not None:
            self.factory.block_event.wait(2)
        if self.factory.outputs:
            return self.factory.outputs.pop(0)
        if "Plan how" in task or "计划" in task:
            return "计划：先拆解问题，再分派研究、审查和综合任务。这是一段足够长的协作计划。"
        if "支持观点" in task or "反对观点" in task:
            return "论据：给出三个强论据，同时说明证据边界。这是一段足够长的辩论材料。"
        if "最终判断" in task:
            return "最终判断：综合正反双方后，给出带条件的平衡结论。这是一段足够长的裁判结论。"
        if "最终执行方案" in task:
            return "最终方案：合并实现、风险和验证建议，形成可执行步骤。这是一段足够长的最终方案。"
        if "最终" in task or "合并" in task:
            return "最终报告：综合上游和黑板信息后，建议按证据优先级推进。这是一段足够长的最终交付。"
        if "粗略扫描" in task:
            return "关键点：先列出范围、约束和最不确定的问题。这是一段足够长的初扫结果。"
        if "精化" in task:
            return "精化：聚焦关键不确定点，补充更细的推理和下一步建议。这是一段足够长的精化结果。"
        if "审查" in task:
            return "风险：上游内容存在证据不足，需要补充来源。这是一段足够长的质检结果。"
        if "实现" in task or "implementation" in task:
            return "实现：采用最小可行路径，先完成核心接口，再补验证。这是一段足够长的实现方案。"
        if "验证" in task or "测试" in task:
            return "验证：覆盖成功路径、失败路径和回归风险。这是一段足够长的验证方案。"
        return "关键发现：这个主题有明确的核心事实、约束和后续问题。这是一段足够长的研究结果。"


class SwarmGraphTests(unittest.TestCase):
    def test_topological_layers_and_cycle_validation(self) -> None:
        tasks = [
            SwarmTask("a", "agent", "A"),
            SwarmTask("b", "agent", "B", depends_on=["a"]),
        ]

        self.assertEqual(topological_layers(tasks), [["a"], ["b"]])

        tasks[0].depends_on = ["b"]
        with self.assertRaises(ValueError):
            validate_dag(tasks)


class SwarmPresetTests(unittest.TestCase):
    def test_builtin_presets_load(self) -> None:
        preset_items = list_presets()
        presets = {item["name"] for item in preset_items}

        self.assertEqual(len(preset_items), 9)
        self.assertIn("research_team", presets)
        self.assertIn("product_discovery_team", presets)
        self.assertIn("implementation_delivery_team", presets)
        self.assertIn("incident_response_team", presets)
        self.assertIn("data_analysis_team", presets)
        self.assertIn("writing_editorial_team", presets)
        self.assertIn("learning_tutor_team", presets)
        for item in preset_items:
            run = build_run_from_preset(item["name"], {"topic": "Agent", "goal": "Improve onboarding", "target": "Service incident"})
            self.assertGreaterEqual(len(run.agents), 3)
            self.assertGreaterEqual(len(run.tasks), 3)
        self.assertTrue(any(item["agent_count"] > 3 for item in preset_items))

        run = build_run_from_preset("research_team", {"topic": "Agent"})

        self.assertEqual(run.preset_name, "research_team")
        self.assertEqual(len(run.agents), 3)
        self.assertEqual(len(run.tasks), 3)


class SwarmRuntimeTests(unittest.TestCase):
    def test_runs_dag_with_upstream_and_blackboard(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            factory = _RunnerFactory()
            runtime = SwarmRuntime(
                SwarmStore(Path(tmp)),
                runner_factory=factory,
                max_workers=2,
                grounding_provider=StaticGroundingProvider({"*": "Grounded fact: use only verified context."}),
            )

            run = runtime.start_preset("research_team", {"topic": "Agent 协作"}, wait=True)

            self.assertEqual(run.status, RunStatus.completed)
            self.assertTrue(run.final_report.startswith("最终报告"))
            self.assertEqual([task.status for task in run.tasks], [TaskStatus.completed] * 3)
            self.assertGreaterEqual(len(runtime.store.blackboard(run.id).read("result.*")), 3)
            synth_call = factory.calls[-1]
            self.assertIn("Grounding", factory.calls[0]["context"])
            self.assertIn("Upstream Results", synth_call["context"])
            self.assertIn("Shared Blackboard", synth_call["context"])
            self.assertIn("blackboard_publish", synth_call["tools"])

    def test_retries_incomplete_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            factory = _RunnerFactory([
                "# Plan\n- think about it",
                "关键发现：第二次尝试给出具体结论，满足质量要求并且足够长，可以直接交付。",
            ])
            runtime = SwarmRuntime(SwarmStore(Path(tmp)), runner_factory=factory)
            run = runtime.start_custom(
                agents=[SwarmAgentSpec("researcher", "研究员", max_retries=1)],
                tasks=[SwarmTask("research", "researcher", "研究 {topic}", acceptance=["contains: 关键发现"])],
                variables={"topic": "Swarm"},
                wait=True,
            )

            self.assertEqual(run.status, RunStatus.completed)
            self.assertEqual(len(factory.calls), 2)
            self.assertIn("retry_guidance", factory.calls[1]["context"])

    def test_spawned_task_runs_after_parent_completes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            release = threading.Event()
            factory = _RunnerFactory(block_event=release)
            runtime = SwarmRuntime(SwarmStore(Path(tmp)), runner_factory=factory)
            run = runtime.start_custom(
                agents=[SwarmAgentSpec("a", "worker")],
                tasks=[SwarmTask("first", "a", "first task", acceptance=["contains: 关键发现"])],
            )

            deadline = time.time() + 2
            while not factory.calls and time.time() < deadline:
                time.sleep(0.01)
            self.assertTrue(factory.calls)
            runtime.spawn_task(
                run.id,
                "first",
                SwarmTask("second", "a", "最终 spawned task", acceptance=["contains: 最终报告"]),
            )
            release.set()
            finished = runtime.wait(run.id, timeout=5)

            self.assertIsNotNone(finished)
            assert finished is not None
            self.assertEqual(finished.status, RunStatus.completed)
            self.assertEqual([task.id for task in finished.tasks], ["first", "second"])
            self.assertEqual([task.status for task in finished.tasks], [TaskStatus.completed, TaskStatus.completed])

    def test_worker_can_spawn_followup_task_with_tool(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            factory = _RunnerFactory()
            runtime = SwarmRuntime(SwarmStore(Path(tmp)), runner_factory=factory)

            run = runtime.start_custom(
                agents=[SwarmAgentSpec("lead", "Lead")],
                tasks=[SwarmTask("plan", "lead", "use spawn tool", allow_spawn=True, acceptance=["contains: 计划"])],
                wait=True,
            )

            self.assertEqual(run.status, RunStatus.completed)
            self.assertEqual([task.id for task in run.tasks], ["plan", "spawned_followup"])
            self.assertEqual([task.status for task in run.tasks], [TaskStatus.completed, TaskStatus.completed])
            events = [event.type for event in runtime.store.read_events(run.id)]
            self.assertIn("task_spawned", events)

    def test_adaptive_code_team_records_learning(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            factory = _RunnerFactory()
            runtime = SwarmRuntime(SwarmStore(Path(tmp)), runner_factory=factory)

            run = runtime.start_adaptive("实现 swarm 代码并补测试", wait=True)

            self.assertEqual(run.status, RunStatus.completed)
            self.assertIn("engineer", {agent.id for agent in run.agents})
            self.assertIn("tester", {agent.id for agent in run.agents})
            snapshot = runtime.learning_snapshot()
            self.assertEqual(snapshot["runs_recorded"], 1)
            self.assertTrue(any(item["completed_tasks"] for item in snapshot["roles"]))

    def test_debate_mode_uses_judge(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            factory = _RunnerFactory()
            runtime = SwarmRuntime(SwarmStore(Path(tmp)), runner_factory=factory)

            run = runtime.start_debate("是否应该使用动态 DAG", wait=True)

            self.assertEqual(run.status, RunStatus.completed)
            self.assertIn("judge", {agent.id for agent in run.agents})
            self.assertTrue(run.final_report.startswith("最终判断"))

    def test_progressive_mode_refines_in_rounds(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            factory = _RunnerFactory()
            runtime = SwarmRuntime(SwarmStore(Path(tmp)), runner_factory=factory)

            run = runtime.start_progressive("设计真正协作团队", rounds=2, wait=True)

            self.assertEqual(run.status, RunStatus.completed)
            self.assertIn("refine_1", {task.id for task in run.tasks})
            self.assertIn("refine_2", {task.id for task in run.tasks})
            self.assertTrue(run.final_report.startswith("最终报告"))


if __name__ == "__main__":
    unittest.main()
