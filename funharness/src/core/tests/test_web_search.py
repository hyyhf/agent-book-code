from __future__ import annotations

import json
import os
import unittest
from unittest.mock import patch

os.environ.setdefault("OPENAI_API_KEY", "test-key")

from funharness.src.agent import FunHarnessAgent
from funharness.src.core.tools import ToolResult, tool_web_search


class FakeResponse:
    def __init__(self, payload: dict):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return json.dumps(self._payload).encode("utf-8")


class WebSearchToolTests(unittest.TestCase):
    def test_tavily_payload_omits_images_and_favicons_for_speed(self) -> None:
        captured = {}
        response = {
            "query": "jay",
            "images": ["https://example.com/top.jpg"],
            "results": [
                {
                    "title": "Title",
                    "url": "https://example.com/page",
                    "content": "Snippet",
                    "score": 0.98,
                    "favicon": "https://example.com/favicon.ico",
                    "images": ["https://example.com/result-only.jpg"],
                }
            ],
        }

        def fake_urlopen(request, timeout=0):
            captured["payload"] = json.loads(request.data.decode("utf-8"))
            return FakeResponse(response)

        with patch.dict(os.environ, {"TAVILY_API_KEY": "test-key"}):
            with patch("funharness.src.core.tools.urlopen", fake_urlopen):
                result = tool_web_search("jay")

        self.assertIsInstance(result, ToolResult)
        self.assertNotIn("include_images", captured["payload"])
        self.assertNotIn("include_favicon", captured["payload"])
        self.assertIn("1. [Title](https://example.com/page)", result.content)
        self.assertIn("Snippet", result.content)
        self.assertEqual(result.display["images"], [])
        self.assertNotIn("favicon", result.display["results"][0])
        self.assertNotIn("images", result.display["results"][0])

    def test_agent_keeps_web_search_display_out_of_tool_context(self) -> None:
        agent = FunHarnessAgent(mode="auto")
        try:
            tool_result = ToolResult(
                content="1. [Title](https://example.com/page)\n   Snippet",
                display={
                    "kind": "web_search",
                    "query": "jay",
                    "images": ["https://example.com/top.jpg"],
                    "results": [{"title": "Title", "url": "https://example.com/page", "favicon": "https://example.com/favicon.ico"}],
                },
            )

            with patch("funharness.src.agent.registry.get_function", return_value=lambda query: tool_result):
                with patch("funharness.src.agent.registry.get_schema", return_value={"function": {"parameters": {"required": ["query"]}}}):
                    content, hook_feedback, display = agent._execute_tool("tool_web_search", '{"query": "jay"}')
        finally:
            agent.scheduler.stop()

        tool_message = {"role": "tool", "tool_call_id": "call_1", "content": content}
        self.assertEqual(hook_feedback, "")
        self.assertEqual(display, tool_result.display)
        self.assertEqual(tool_message["content"], tool_result.content)
        self.assertNotIn("favicon", tool_message["content"])
        self.assertNotIn("https://example.com/top.jpg", tool_message["content"])


if __name__ == "__main__":
    unittest.main()
