from __future__ import annotations

import threading
import unittest

from funharness.src.core.subagent import SubAgent


class _Completions:
    def __init__(self) -> None:
        self.calls = 0

    def create(self, **kwargs):
        self.calls += 1
        raise AssertionError("LLM should not be called")


class _Chat:
    def __init__(self) -> None:
        self.completions = _Completions()


class _Client:
    def __init__(self) -> None:
        self.chat = _Chat()


class SubAgentCancellationTests(unittest.TestCase):
    def test_cancel_event_prevents_llm_call(self) -> None:
        client = _Client()
        cancel_event = threading.Event()
        cancel_event.set()
        subagent = SubAgent("tester", llm_client=client)

        result = subagent.run("work", cancel_event=cancel_event)

        self.assertEqual(result, "(subagent cancelled)")
        self.assertEqual(client.chat.completions.calls, 0)

    def test_zero_timeout_prevents_llm_call(self) -> None:
        client = _Client()
        subagent = SubAgent("tester", llm_client=client)

        result = subagent.run("work", timeout_seconds=0)

        self.assertEqual(result, "(subagent timed out)")
        self.assertEqual(client.chat.completions.calls, 0)


if __name__ == "__main__":
    unittest.main()
