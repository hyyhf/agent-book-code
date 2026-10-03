from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import httpx
from openai import BadRequestError

from funharness.src.agent import FunHarnessAgent
from funharness.src.core.context import (
    ContextBudgetError, ContextManager, compact_conversation, estimate_tokens,
    is_context_overflow, should_compact,
)
from funharness.src.core.content import content_text
from funharness.src.core.llm import sanitize_messages_for_api
from funharness.src.core.session import Session, SessionManager
from funharness.src.core.subagent import SubAgent
from funharness.src.core.groups.models import (
    AgentGroup, AgentProfile, GroupAgentRun, GroupAgentSession, GroupMember, GroupMessage,
)
from funharness.src.core.groups.runner import GroupAgentRunner
from funharness.src.core.groups.store import GroupStore
from funharness.src.core.tools import ToolRegistry


def overflow(message="This model's maximum context length is 8192 tokens", code="context_length_exceeded"):
    return BadRequestError(message, response=httpx.Response(400, request=httpx.Request("POST", "https://example.test")),
                           body={"error": {"code": code, "message": message}})


def call(identifier="read", args=None):
    return {"id": identifier, "type": "function", "function": {
        "name": "probe", "arguments": json.dumps(args or {}),
    }}


def history(size=30000):
    return [{"role": "system", "content": "Preserve user instructions."},
            {"role": "user", "content": "Original goal: repair report.txt; preserve source files."},
            {"role": "assistant", "content": "Earlier progress " + "x" * size},
            {"role": "user", "content": "继续"}]


class Client:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.requests = []
        self.chat = SimpleNamespace(completions=self)
        self.base_url = "https://example.test/v1"

    def create(self, **kwargs):
        self.requests.append(deepcopy(kwargs))
        result = next(self.responses)
        if isinstance(result, Exception):
            raise result
        calls = [SimpleNamespace(index=i, id=tc["id"], function=SimpleNamespace(**tc["function"]))
                 for i, tc in enumerate(result.get("tool_calls", []))]
        msg = SimpleNamespace(content=result.get("content"), reasoning_content=result.get("reasoning_content"), tool_calls=calls)
        if kwargs.get("stream"):
            return iter([SimpleNamespace(choices=[SimpleNamespace(delta=msg)])])
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)])


class ContextTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {"FUNHARNESS_CONTEXT_WINDOW": "128000", "FUNHARNESS_MAX_OUTPUT_TOKENS": "8192"})
        self.env.start()

    def tearDown(self):
        self.env.stop()

    def test_budget_includes_schema_reasoning_cjk_and_output_reserve(self):
        basic = [{"role": "user", "content": "a" * 1000}]
        chinese = [{"role": "user", "content": "中" * 1000}]
        self.assertGreater(estimate_tokens(chinese), estimate_tokens(basic) * 3)
        self.assertGreater(estimate_tokens(basic, [{"description": "tool" * 1000}]), estimate_tokens(basic) + 1000)
        self.assertGreater(estimate_tokens([{"role": "assistant", "reasoning_content": "thought" * 1000}]), 2000)
        manager = ContextManager("unknown")
        self.assertLess(manager.input_budget + manager.max_tokens, manager.window)
        self.assertEqual(manager.max_tokens, 8192)

    def test_short_history_with_giant_fresh_parallel_tool_outputs_fits(self):
        messages = [{"role": "system", "content": "rules"}, {"role": "user", "content": "inspect both files"},
                    {"role": "assistant", "content": None, "reasoning_content": "Read files", "tool_calls": [call("a"), call("b")]},
                    {"role": "tool", "tool_call_id": "a", "content": "a" * 100000},
                    {"role": "tool", "tool_call_id": "b", "content": "b" * 100000}]
        result = compact_conversation(messages, target_tokens=4000, summarize=False)
        self.assertLessEqual(estimate_tokens(result), 4000)
        self.assertEqual(result[2], messages[2])
        self.assertEqual([m["tool_call_id"] for m in result if m["role"] == "tool"], ["a", "b"])
        self.assertIn("omitted", result[-1]["content"])
        self.assertEqual(len(messages[-1]["content"]), 100000)
        sanitize_messages_for_api(result)

    def test_large_call_arguments_collapse_as_observations_not_broken_json(self):
        messages = [{"role": "user", "content": "save then check"},
                    {"role": "assistant", "content": None, "reasoning_content": "r" * 30000,
                     "tool_calls": [call("write", {"content": "x" * 100000})]},
                    {"role": "tool", "tool_call_id": "write", "content": "Successfully wrote output.txt"}]
        result = compact_conversation(messages, target_tokens=1000, summarize=False)
        self.assertLessEqual(estimate_tokens(result), 1000)
        self.assertFalse(any(m.get("tool_calls") for m in result))
        self.assertIn("Successfully wrote output.txt", content_text(result[-1]["content"]))
        self.assertIn("Past tool observations", content_text(result[-1]["content"]))
        self.assertIn("x" * 100000, messages[1]["tool_calls"][0]["function"]["arguments"])

    def test_failed_or_empty_summary_falls_back_and_summary_request_is_bounded(self):
        messages = [history()[0]] + [m for _ in range(100) for m in history()[1:-1]] + [{"role": "user", "content": "continue"}]
        for response in (RuntimeError("summary unavailable"), {"content": ""}):
            llm = Client([response])
            result = compact_conversation(messages, target_tokens=10000, llm_client=llm)
            self.assertLessEqual(estimate_tokens(result), 10000)
            self.assertIn("report.txt", json.dumps(result))
            self.assertEqual(result[-1]["content"], "continue")
            self.assertLess(estimate_tokens(llm.requests[0]["messages"]) + llm.requests[0]["max_tokens"], 10000)

    def test_latest_real_request_survives_middleware_and_many_tool_batches(self):
        request = {"role": "user", "content": "Do not delete any files. Fix result.csv."}
        messages = [{"role": "system", "content": "rules"}, request]
        for i in range(12):
            messages.extend([{"role": "assistant", "content": None, "tool_calls": [call(str(i))]},
                             {"role": "tool", "tool_call_id": str(i), "content": "x" * 4000}])
        messages.append({"role": "user", "content": "[SYSTEM MIDDLEWARE]\nCheck your work."})
        result = compact_conversation(messages, target_tokens=3000, summarize=False)
        self.assertIn(request, result)
        self.assertLessEqual(estimate_tokens(result), 3000)
        sanitize_messages_for_api(result)

    def test_huge_current_request_gives_actionable_error_then_continue_can_recover(self):
        messages = [{"role": "system", "content": "rules"}, {"role": "user", "content": "x" * 60000}]
        original = deepcopy(messages)
        with self.assertRaisesRegex(ContextBudgetError, "本次用户输入"):
            compact_conversation(messages, target_tokens=4000, summarize=False)
        self.assertEqual(messages, original)
        messages.append({"role": "user", "content": "继续，先概括此前输入"})
        result = compact_conversation(messages, target_tokens=4000, summarize=False)
        self.assertEqual(result[-1], messages[-1])
        self.assertLessEqual(estimate_tokens(result), 4000)

    def test_overflow_detection_does_not_retry_unrelated_errors(self):
        self.assertTrue(is_context_overflow(overflow()))
        self.assertTrue(is_context_overflow(ValueError("Model request exceeds 48 MiB; compact")))
        for error in (ValueError("Invalid image MIME type"), overflow("Invalid API key", "invalid_api_key"),
                      overflow("Invalid tool_call_id", "invalid_request_error"), RuntimeError("connection reset")):
            self.assertFalse(is_context_overflow(error))
            invoke = Mock(side_effect=error)
            with self.assertRaises(type(error)):
                ContextManager().run(history(1), [], invoke)
            self.assertEqual(invoke.call_count, 1)

    def test_reactive_recovery_learns_window_and_persists_for_next_turn(self):
        messages = history()
        state = {}
        manager = ContextManager("unknown", state=state)
        invoke = Mock(side_effect=[overflow(), "recovered"])
        self.assertEqual(manager.run(messages, [], invoke), "recovered")
        self.assertEqual(invoke.call_count, 2)
        restored = ContextManager("unknown", state=json.loads(json.dumps(state)))
        self.assertEqual(restored.window, 8192)
        self.assertEqual(restored.max_tokens, 2048)
        self.assertLessEqual(estimate_tokens(messages), restored.input_budget)
        messages.append({"role": "user", "content": "继续"})
        self.assertEqual(restored.run(messages, [], lambda _: "next turn"), "next turn")
        self.assertEqual(ContextManager("different-model", state=state).window, 128000)

    def test_context_retries_are_bounded_and_every_attempt_reduces_input(self):
        messages = history(200000)
        sizes = []
        def invoke(_):
            sizes.append(estimate_tokens(messages))
            raise overflow("prompt is too long")
        with self.assertRaises(ContextBudgetError):
            ContextManager().run(messages, [], invoke)
        self.assertLessEqual(len(sizes), 4)
        self.assertGreaterEqual(len(sizes), 2)
        self.assertTrue(all(a > b for a, b in zip(sizes, sizes[1:])), sizes)

    def test_cancelled_recovery_does_not_send_next_request(self):
        messages = history()
        def invoke(_):
            raise overflow()
        check = Mock(side_effect=[None, InterruptedError("cancelled")])
        with self.assertRaises(InterruptedError):
            ContextManager().run(messages, [], invoke, check_cancel=check)
        self.assertEqual(messages, history())

    def test_reported_usage_calibrates_future_budget(self):
        manager = ContextManager()
        budget = manager.input_budget
        messages = history(1)
        manager.run(messages, [], lambda _: {"response_metadata": {"usage": {"prompt_tokens": estimate_tokens(messages) * 2}}})
        self.assertLess(manager.input_budget, budget // 2)

    def test_explicit_user_request_is_not_confused_with_injected_prefixes(self):
        request = {"role": "user", "content": "[SYSTEM] This literal text is part of my request.", "context_kind": "request"}
        messages = history()[:-1] + [request, {"role": "user", "content": "[SYSTEM MIDDLEWARE] finish"}]
        result = compact_conversation(messages, target_tokens=1500, summarize=False)
        self.assertIn(request, result)
        self.assertNotIn("context_kind", json.dumps(sanitize_messages_for_api(result)))

    def test_window_error_with_k_suffix_is_parsed_without_treating_it_as_eight_tokens(self):
        manager = ContextManager()
        manager.run(history(), [], Mock(side_effect=[overflow("maximum context length is 8k tokens"), "ok"]))
        self.assertEqual(manager.window, 8192)

    def test_undocumented_smaller_proxy_window_also_reduces_large_output_reservation(self):
        with patch.dict(os.environ, {"FUNHARNESS_CONTEXT_WINDOW": "1048576", "FUNHARNESS_MAX_OUTPUT_TOKENS": "65536"}):
            manager = ContextManager("deepseek-flash")
            invoke = Mock(side_effect=[overflow("prompt is too long"), "ok"])
            manager.run(history(), [], invoke)
            self.assertEqual(invoke.call_args_list[0].args[0], 65536)
            self.assertEqual(invoke.call_args_list[1].args[0], 8192)

    def test_summary_cancellation_is_not_swallowed_by_local_fallback(self):
        messages = history()[:-1] + [{"role": "assistant", "content": "progress"}] * 6 + [history()[-1]]
        with self.assertRaises(InterruptedError):
            compact_conversation(messages, target_tokens=3000, llm_client=Client([InterruptedError("cancelled")]))

    def test_image_payload_overflow_evicts_old_media_without_slicing_new_media(self):
        image = lambda data, path: {"type": "image", "data": data, "source_path": path, "width": 16, "height": 16, "detail": "low", "mime_type": "image/png"}
        first, second = image("a" * 4000, "first.png"), image("b" * 4000, "second.png")
        messages = [{"role": "user", "content": "compare"},
                    {"role": "assistant", "tool_calls": [call("a"), call("b")], "content": None},
                    {"role": "tool", "tool_call_id": "a", "content": [first]},
                    {"role": "tool", "tool_call_id": "b", "content": [second]}]
        result = compact_conversation(messages, target_tokens=3000, max_bytes=6000, summarize=False)
        self.assertLessEqual(len(json.dumps(result).encode()), 6000)
        blocks = [b for m in result if isinstance(m.get("content"), list) for b in m["content"]]
        self.assertIn(second, blocks)
        self.assertNotIn(first, blocks)
        self.assertIn("first.png", json.dumps(result))
        self.assertEqual(messages[-2]["content"], [first])

    def test_session_roundtrip_branch_and_archive_preserve_state_and_original(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager = SessionManager(tmp)
            session = Session(messages=history(), context_state={"key": "example", "window": 8000})
            archive = manager.archive_context(session, session.messages)
            session.messages = compact_conversation(session.messages, target_tokens=4000, summarize=False)
            manager.save(session)
            restored = manager.load(session.id)
            branch = manager.branch(session.id)
            self.assertEqual(restored.context_state, session.context_state)
            self.assertEqual(branch.context_state, session.context_state)
            self.assertEqual(json.loads(archive.read_text(encoding="utf-8"))["messages"], history())
            branch.context_state["window"] = 1
            self.assertEqual(session.context_state["window"], 8000)


class ContextIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.previous = Path.cwd()
        os.chdir(self.tmp.name)
        self.env = patch.dict(os.environ, {"FUNHARNESS_CONTEXT_WINDOW": "128000", "FUNHARNESS_MAX_OUTPUT_TOKENS": "8192"})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        os.chdir(self.previous)
        self.tmp.cleanup()

    def test_main_recovery_retries_only_inference_and_saves_compacted_history(self):
        executed = []
        registry = ToolRegistry()
        @registry.tool(category="file")
        def probe():
            """Inspect test data."""
            executed.append(True)
            return "result " * 30000
        llm = Client([{"tool_calls": [call()]}, overflow("prompt is too long"),
                      {"content": "Finished inspecting the result; the requested report is complete and consistent."}])
        agent = FunHarnessAgent(mode="auto", llm_client=llm, tool_registry=registry)
        try:
            agent.run("Inspect the report.")
            self.assertEqual(executed, [True])
            self.assertEqual(len(llm.requests), 3)
            self.assertLess(estimate_tokens(llm.requests[-1]["messages"]), estimate_tokens(llm.requests[-2]["messages"]))
            self.assertEqual(sum(m.get("content") == "Inspect the report." for m in agent.messages), 1)
            restored = agent.session_mgr.load(agent.current_session.id)
            self.assertTrue(restored.context_state.get("input_budget"))
            self.assertTrue(list(agent.session_mgr.sessions_dir.glob("context_archives/*/*.json")))
            self.assertIs(agent.current_session.messages, agent.messages)
        finally:
            agent.scheduler.stop()

    def test_main_stream_consumption_overflow_closes_stream_and_recovers(self):
        agent = FunHarnessAgent(llm_client=object())
        stream1, stream2 = Mock(), Mock()
        try:
            agent.messages.extend(history(200000)[1:])
            with patch("funharness.src.agent.call_with_retry", side_effect=[stream1, stream2]), patch(
                "funharness.src.agent.process_stream_response", side_effect=[overflow("prompt is too long"), {"role": "assistant", "content": "recovered"}]
            ):
                result = agent._process_llm_stream_with_retry([])
            self.assertEqual(result["content"], "recovered")
            stream1.close.assert_called_once()
            stream2.close.assert_called_once()
        finally:
            agent.scheduler.stop()

    def test_loaded_overflowed_session_continue_compacts_before_first_request(self):
        llm = Client([{"content": "The previous report has been resumed and the remaining work is now complete."}])
        agent = FunHarnessAgent(llm_client=llm)
        try:
            session = Session(messages=[agent.messages[0], *history(450000)[1:]])
            agent.session_mgr.save(session)
            agent.current_session = agent.session_mgr.load(session.id)
            agent.messages = agent.current_session.messages
            agent.run("继续")
            inference = [request for request in llm.requests if request.get("stream")]
            self.assertEqual(len(inference), 1)
            self.assertLessEqual(estimate_tokens(inference[0]["messages"], inference[0]["tools"]), agent._context_manager().input_budget)
        finally:
            agent.scheduler.stop()

    def test_subagent_recovers_and_preserves_task_when_background_is_huge(self):
        llm = Client([overflow("prompt is too long"), {"content": "done"}])
        agent = SubAgent("review", llm_client=llm)
        self.assertEqual(agent.run("inspect report.txt", context="x" * 90000), "done")
        self.assertEqual(len(llm.requests), 2)
        self.assertEqual(llm.requests[-1]["messages"][-1]["content"], "inspect report.txt")
        self.assertLess(estimate_tokens(llm.requests[-1]["messages"]), estimate_tokens(llm.requests[0]["messages"]))

    def test_group_recovers_with_saved_limits_and_streamed_answer(self):
        root = Path.cwd()
        store = GroupStore(root / ".funharness" / "groups")
        group = store.save_group(AgentGroup(name="test"))
        member = store.save_member(GroupMember(group_id=group.id, profile_id="p"))
        session = GroupAgentSession(group_id=group.id, member_id=member.id, messages=history(100000)[1:])
        llm = Client([overflow("prompt is too long"), {"content": "done"}])
        runner = GroupAgentRunner(store=store, workspace=root, llm_client=llm)
        output = runner.run(group=group, member=member, profile=AgentProfile(id="p", name="review"),
            session=session, run=GroupAgentRun(group_id=group.id, member_id=member.id),
            trigger=GroupMessage(content="continue"), cancel_event=threading.Event())
        self.assertEqual(output, "done")
        self.assertEqual(len(llm.requests), 2)
        self.assertTrue(session.context_state.get("input_budget"))
        restored = store.get_or_create_session(group.id, member)
        self.assertEqual(restored.context_state, session.context_state)


if __name__ == "__main__":
    unittest.main()
