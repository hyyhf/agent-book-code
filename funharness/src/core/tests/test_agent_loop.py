import os
import tempfile
import unittest
from unittest.mock import patch

from funharness.src.agent import FunHarnessAgent


class AgentLoopMiddlewareTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
