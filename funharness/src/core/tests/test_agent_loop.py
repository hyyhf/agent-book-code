import json
import os
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from funharness.src.agent import FunHarnessAgent


class AgentLoopMiddlewareTests(unittest.TestCase):
    def test_guarded_file_edits_recover_and_emit_structured_status(self) -> None:
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            previous = os.getcwd()
            agent = None
            os.chdir(tmp)
            try:
                agent = FunHarnessAgent(mode="auto", llm_client=object())
                target = Path(tmp) / "error_in_name.py"
                target.write_bytes(b"first\nsecond\n")
                def call(name, **args):
                    return agent._execute_tool(name, json.dumps(args))
                output, feedback, display = call("tool_replace_in_file", path=str(target), old_text="first", new_text="FIRST")
                self.assertIn("FILE_NOT_READ", output)
                self.assertNotIn("Python file written", feedback)
                call("tool_read_file", path=str(target), start_line=1, limit=1)
                output, feedback, display = call("tool_replace_in_file", path=str(target), replacements=[
                    {"old_text": "first", "new_text": "FIRST"}, {"old_text": "second", "new_text": "SECOND"},
                ])
                self.assertTrue(display["changed"])
                self.assertIn("Python file written", feedback)
                self.assertNotIn("hunks", str(output))
                self.assertEqual(target.read_bytes(), b"FIRST\nSECOND\n")
                output, feedback, display = call("tool_replace_in_file", path=str(target), old_text="FIRST", new_text="FIRST")
                self.assertFalse(display["changed"])
                self.assertNotIn("Python file written", feedback)
                malformed, _, _ = call("tool_replace_in_file", path=str(target), replacements=[{"old_text": "FIRST"}])
                self.assertIn("INVALID_ARGUMENT", malformed)
            finally:
                if agent:
                    agent.runtime.shutdown()
                    agent.scheduler.stop()
                os.chdir(previous)

    @unittest.skipUnless(os.name == "nt", "Windows PowerShell dispatch")
    def test_powershell_selection_reaches_executor_through_agent_permissions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            old_cwd = os.getcwd()
            agent = None
            os.chdir(tmp)
            try:
                agent = FunHarnessAgent(mode="auto", llm_client=object())
                output, _, _ = agent._execute_tool("tool_run_command", json.dumps({
                    "command": "[pscustomobject]@{Name='PS-FROM-AGENT'} | Format-Table",
                    "shell": "powershell", "yield_time_ms": 10000,
                }))
                self.assertIn("status=done", output)
                self.assertIn("PS-FROM-AGENT", output)
                self.assertEqual(agent.runtime.list()[0].shell, "powershell")
                self.assertIn("shell=pwsh", agent._system_prompt)
            finally:
                if agent:
                    agent.runtime.shutdown()
                    agent.scheduler.stop()
                os.chdir(old_cwd)

    def test_yielded_command_must_finish_before_agent_completes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            old_cwd = os.getcwd()
            agent = None
            os.chdir(tmp)
            try:
                agent = FunHarnessAgent(mode="auto", llm_client=object())
                command = f'"{sys.executable}" -c "import time; time.sleep(.3); print(\'VERIFIED\')"'
                call = {"role": "assistant", "content": None, "tool_calls": [{
                    "id": "call_cmd", "type": "function", "function": {
                        "name": "tool_run_command", "arguments": json.dumps({
                            "command": command, "yield_time_ms": 0,
                        }),
                    },
                }]}
                final = {"role": "assistant", "content": "Command complete and its output was reviewed."}
                with patch.object(agent, "_process_llm_stream_with_retry", side_effect=[call, final, final]):
                    agent.run("execute and verify")
                self.assertEqual(agent.last_run_stop_reason, "completed")
                self.assertEqual(len(agent.runtime.list()), 1)
                self.assertEqual(agent.runtime.list()[0].status.value, "done")
                self.assertTrue(any("VERIFIED" in str(m.get("content")) for m in agent.messages))
            finally:
                if agent:
                    agent.runtime.shutdown()
                    agent.scheduler.stop()
                os.chdir(old_cwd)

    def test_stop_cancels_a_yielded_command_and_returns_promptly(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            old_cwd = os.getcwd()
            agent = None
            os.chdir(tmp)
            try:
                agent = FunHarnessAgent(mode="auto", llm_client=object())
                command = f'"{sys.executable}" -c "import time; time.sleep(30)"'
                result, _, _ = agent._execute_tool("tool_run_command", json.dumps({
                    "command": command, "yield_time_ms": 0, "timeout": 900,
                }))
                self.assertIn("runtime_id=", result)
                start = time.monotonic()
                agent.request_interrupt()
                self.assertLess(time.monotonic() - start, .5)
                task = agent.runtime.list()[0]
                outcome = agent.runtime.wait(task.runtime_id, 5000)
                self.assertIn("status=cancelled", outcome)
            finally:
                if agent:
                    agent.runtime.shutdown()
                    agent.scheduler.stop()
                os.chdir(old_cwd)

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
            [{"role": "user", "content": "继续当前任务", "context_kind": "request"}],
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
                    json.dumps({"command": command, "timeout": 1, "yield_time_ms": 10000}),
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
