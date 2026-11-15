import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from funharness.src.agent import FunHarnessAgent
from funharness.src.core.hooks import EnvironmentAwareMiddleware
from funharness.src.core.loop_limits import iteration_limit
from funharness.src.core.subagent import SubAgent
from funharness.src.core.tools import ToolRegistry
from funharness.src.core.groups import AgentGroup, AgentProfile, GroupAgentRun, GroupMember, GroupMessage, GroupStore
from funharness.src.core.groups.runner import GroupAgentRunner


def step(index):
    return {"role": "assistant", "content": "", "tool_calls": [{
        "id": f"step_{index}", "type": "function", "function": {
            "name": "progress", "arguments": json.dumps({"index": index}),
        },
    }]}


class LoopLimitTests(unittest.TestCase):
    def test_runtime_settings_and_invalid_fallback(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(iteration_limit(), 1000)
            os.environ["FUNHARNESS_MAX_ITERATIONS"] = "2500"
            self.assertEqual(iteration_limit(), 2500)
            self.assertEqual(iteration_limit("subagent"), 2500)
            os.environ["FUNHARNESS_SUBAGENT_MAX_ITERATIONS"] = "3500"
            self.assertEqual(iteration_limit("subagent"), 3500)
            self.assertEqual(iteration_limit("group"), 2500)
            for invalid in ["0", "-5", "bad", "2.5"]:
                os.environ["FUNHARNESS_SUBAGENT_MAX_ITERATIONS"] = invalid
                with self.assertLogs("funharness.src.core.loop_limits", level="WARNING"):
                    self.assertEqual(iteration_limit("subagent"), 2500)

    def test_warning_uses_actual_budget(self):
        middleware = EnvironmentAwareMiddleware()
        for iteration in [40, 100, 899]:
            context = middleware.process({"iteration": iteration, "max_iterations": 1000})
            self.assertEqual(context["injections"], [])
        self.assertIn("100 remain", middleware.process({"iteration": 900, "max_iterations": 1000})["injections"][0])

    def test_main_agent_passes_old_limit_and_can_continue_after_configured_limit(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"FUNHARNESS_MAX_ITERATIONS": "1000"}):
            old_cwd = os.getcwd()
            agent = None
            os.chdir(tmp)
            try:
                statuses = []
                agent = FunHarnessAgent(mode="auto", llm_client=object(), on_status=statuses.append)
                done = {"role": "assistant", "content": "Completed all requested steps and verified the result successfully."}
                with patch.object(agent, "_execute_tool", return_value=("ok", "", None)), patch.object(
                    agent, "_process_llm_stream_with_retry", side_effect=[*(step(i) for i in range(105)), done],
                ) as invoke:
                    agent.run("Complete the long task")
                self.assertEqual(invoke.call_count, 106)
                self.assertEqual(agent.last_run_stop_reason, "completed")
                os.environ["FUNHARNESS_MAX_ITERATIONS"] = "2"
                with patch.object(agent, "_execute_tool", return_value=("ok", "", None)), patch.object(
                    agent, "_process_llm_stream_with_retry", side_effect=[step(200), step(201)],
                ) as invoke:
                    agent.run("Another task")
                self.assertEqual(invoke.call_count, 2)
                self.assertEqual(agent.last_run_stop_reason, "max_iterations")
                self.assertTrue(any("(2)" in status for status in statuses))
                with patch.object(agent, "_process_llm_stream_with_retry", return_value=done):
                    agent.run("Continue")
                self.assertEqual(agent.last_run_stop_reason, "completed")
            finally:
                if agent:
                    agent.runtime.shutdown()
                    agent.scheduler.stop()
                os.chdir(old_cwd)

    def test_subagent_passes_old_limit(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"FUNHARNESS_SUBAGENT_MAX_ITERATIONS": "1000"}):
            old_cwd = os.getcwd()
            os.chdir(tmp)
            try:
                registry = ToolRegistry()
                registry.tool()(lambda index: str(index))
                messages = [SimpleNamespace(content="", reasoning_content=None, tool_calls=[
                    SimpleNamespace(id=f"step_{i}", function=SimpleNamespace(name="<lambda>", arguments=json.dumps({"index": i}))),
                ]) for i in range(65)]
                messages.append(SimpleNamespace(content="done", reasoning_content=None, tool_calls=[]))
                responses = [SimpleNamespace(choices=[SimpleNamespace(message=m)]) for m in messages]
                with patch("funharness.src.core.subagent.ContextManager.run", side_effect=responses) as invoke:
                    result = SubAgent("tester", llm_client=object(), tool_registry=registry).run("Long task")
                self.assertEqual(result, "done")
                self.assertEqual(invoke.call_count, 66)
            finally:
                os.chdir(old_cwd)

    def test_group_agent_passes_old_limit(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"FUNHARNESS_GROUP_MAX_ITERATIONS": "1000"}):
            workspace = Path(tmp)
            store = GroupStore(workspace / ".funharness/groups")
            group = store.save_group(AgentGroup(name="test"))
            profile = AgentProfile(name="tester")
            member = store.save_member(GroupMember(group_id=group.id, profile_id=profile.id, display_name="tester"))
            session = store.get_or_create_session(group.id, member)
            run = GroupAgentRun(group_id=group.id, member_id=member.id, profile_id=profile.id)
            trigger = GroupMessage(group_id=group.id, sender_type="user", sender_id="user", sender_name="user", content="Long task")
            runner = GroupAgentRunner(store=store, workspace=workspace, llm_client=object())
            with patch("funharness.src.core.groups.runner.ContextManager.run", side_effect=[
                *(step(i) for i in range(45)), {"role": "assistant", "content": "done"},
            ]) as invoke, patch.object(runner, "_call_tool", return_value="ok"):
                result = runner.run(group=group, member=member, profile=profile, session=session,
                                    run=run, trigger=trigger, cancel_event=threading.Event())
            self.assertEqual(result, "done")
            self.assertEqual(invoke.call_count, 46)
