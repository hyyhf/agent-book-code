import json
import os
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from funharness.src.agent import FunHarnessAgent


class AgentLoopMiddlewareTests(unittest.TestCase):
    def test_incomplete_chunked_stream_is_retried_in_the_same_agent_turn(self) -> None:
        statuses = []
        completed = {
            "role": "assistant",
            "content": (
                "The model connection recovered and the agent completed the requested task "
                "without restarting or duplicating the user's turn."
            ),
        }

        with tempfile.TemporaryDirectory() as tmp:
            old_cwd = os.getcwd()
            agent = None
            os.chdir(tmp)
            try:
                agent = FunHarnessAgent(on_status=statuses.append, llm_client=object())
                with patch(
                    "funharness.src.agent.call_with_retry",
                    side_effect=[object(), object()],
                ) as call, patch(
                    "funharness.src.agent.process_stream_response",
                    side_effect=[
                        RuntimeError(
                            "peer closed connection without sending complete message body "
                            "(incomplete chunked read)"
                        ),
                        completed,
                    ],
                ), patch.object(agent._interrupt_event, "wait", return_value=False):
                    agent.run("继续当前任务")
            finally:
                if agent is not None:
                    agent.scheduler.stop()
                os.chdir(old_cwd)

        self.assertEqual(call.call_count, 2)
        self.assertEqual(
            [item for item in agent.messages if item.get("role") == "user"],
            [{"role": "user", "content": "继续当前任务"}],
        )
        self.assertEqual(agent.messages[-1], completed)
        self.assertTrue(any("正在自动重连" in item for item in statuses))
        self.assertFalse(any("正在等待模型响应" in item for item in statuses))

    def test_new_turn_does_not_force_stop_from_previous_tool_errors(self) -> None:
        statuses = []

        with tempfile.TemporaryDirectory() as tmp:
            old_cwd = os.getcwd()
            agent = None
            os.chdir(tmp)
            try:
                agent = FunHarnessAgent(on_status=statuses.append, llm_client=object())
                agent.tool_calls_history.extend(
                    {"tool": "tool_write_file", "args": {"path": f"bad-{idx}.txt"}, "result": "Error: failed"}
                    for idx in range(5)
                )

                with patch("funharness.src.agent.call_with_retry", return_value=[]), patch(
                    "funharness.src.agent.process_stream_response",
                    return_value={
                        "role": "assistant",
                        "content": "This turn continues normally without old tool errors stopping it.",
                    },
                ):
                    agent.run("continue")
            finally:
                if agent is not None:
                    agent.scheduler.stop()
                os.chdir(old_cwd)

        self.assertNotIn("Middleware force stop", statuses)

    def test_run_command_tool_honors_timeout_argument(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            old_cwd = os.getcwd()
            agent = None
            os.chdir(tmp)
            try:
                agent = FunHarnessAgent(mode="auto", llm_client=object())
                command = f'"{sys.executable}" -c "import time; time.sleep(2)"'

                result, _, _ = agent._execute_tool(
                    "tool_run_command",
                    json.dumps({"command": command, "timeout": 1}),
                )
            finally:
                if agent is not None:
                    agent.scheduler.stop()
                os.chdir(old_cwd)

        self.assertIn("timed out (1s)", result)

    def test_request_interrupt_closes_active_stream(self) -> None:
        class CloseableStream:
            def __init__(self) -> None:
                self.closed = False

            def close(self) -> None:
                self.closed = True

        agent = FunHarnessAgent(llm_client=object())
        stream = CloseableStream()
        try:
            agent._set_active_stream(stream)

            agent.request_interrupt()
        finally:
            agent.scheduler.stop()

        self.assertTrue(stream.closed)
        self.assertTrue(agent.is_interrupted())

    def test_interruptible_call_returns_when_agent_is_interrupted(self) -> None:
        entered = threading.Event()
        release = threading.Event()
        errors: list[BaseException] = []
        agent = FunHarnessAgent(llm_client=object())

        def blocking_call() -> str:
            entered.set()
            release.wait(timeout=5)
            return "late"

        def run_call() -> None:
            try:
                agent._run_interruptible_call(blocking_call)
            except BaseException as exc:
                errors.append(exc)

        thread = threading.Thread(target=run_call)
        try:
            thread.start()
            self.assertTrue(entered.wait(timeout=1))

            agent.request_interrupt()
            thread.join(timeout=1)
        finally:
            release.set()
            agent.scheduler.stop()
            thread.join(timeout=1)

        self.assertFalse(thread.is_alive())
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], InterruptedError)

    def test_interrupted_tool_result_is_emitted_before_turn_stops(self) -> None:
        tool_results = []
        assistant_msg = {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": "tool_run_command",
                        "arguments": json.dumps({"command": "sleep"}),
                    },
                }
            ],
        }

        def execute_and_interrupt(_name, _args):
            agent.request_interrupt()
            return "Interrupted: command stopped by user", "", None

        with tempfile.TemporaryDirectory() as tmp:
            old_cwd = os.getcwd()
            agent = None
            os.chdir(tmp)
            try:
                agent = FunHarnessAgent(
                    mode="auto",
                    llm_client=object(),
                    on_tool_result=lambda *args: tool_results.append(args),
                )
                with patch("funharness.src.agent.call_with_retry", return_value=[]), patch(
                    "funharness.src.agent.process_stream_response",
                    return_value=assistant_msg,
                ), patch.object(agent, "_execute_tool", side_effect=execute_and_interrupt):
                    with self.assertRaises(InterruptedError):
                        agent.run("run it")
            finally:
                if agent is not None:
                    agent.scheduler.stop()
                os.chdir(old_cwd)

        self.assertEqual(len(tool_results), 1)
        self.assertEqual(tool_results[0][0], "tool_run_command")
        self.assertEqual(tool_results[0][1], "Interrupted: command stopped by user")


if __name__ == "__main__":
    unittest.main()
