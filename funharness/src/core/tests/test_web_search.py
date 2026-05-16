from __future__ import annotations

import json
import os
import unittest
from unittest.mock import patch

os.environ.setdefault("OPENAI_API_KEY", "test-key")

from funharness.src.agent import FunHarnessAgent
from funharness.src.core.tools import ToolResult, registry, tool_web_crawl, tool_web_fetch, tool_web_search


class FakeResponse:
    def __init__(self, payload: dict):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return json.dumps(self._payload).encode("utf-8")


class FakeFetchResponse:
    status = 200
    headers = {"Content-Type": "text/html"}

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def geturl(self):
        return "https://example.com/final"

    def read(self, size=-1):
        html = (
            b"<html><head><meta charset='windows-1252'><script>hide()</script></head>"
            b"<body><h1>Title&nbsp;&amp;&nbsp;More</h1><p>caf\xe9</p></body></html>"
        )
        return html if size < 0 else html[:size]


class FakeSelection:
    def __init__(self, values):
        self._values = values

    def getall(self):
        return self._values


class FakeScraplingResponse:
    def __init__(self, url, status=200, text="", css=None, xpath=None):
        self.url = url
        self.status = status
        self.reason = "OK"
        self.text = text
        self._css = css or {}
        self._xpath = xpath or {}
        self.body = text.encode("utf-8")
        self.encoding = "utf-8"

    def css(self, selector, **kwargs):
        return FakeSelection(self._css.get(selector, []))

    def xpath(self, selector, **kwargs):
        return FakeSelection(self._xpath.get(selector, []))


class FakeStaticFetcher:
    calls = []

    @classmethod
    def get(cls, url, **kwargs):
        cls.calls.append((url, kwargs))
        return FakeScraplingResponse(url, status=403, text="Just a moment Cloudflare")


class FakeDynamicFetcher:
    calls = []

    @classmethod
    def fetch(cls, url, **kwargs):
        cls.calls.append((url, kwargs))
        if url.endswith("/next"):
            return FakeScraplingResponse(
                url,
                text=(
                    "Second page loaded with JavaScript and enough useful content for extraction. "
                    "This body is intentionally long enough to avoid the crawler's challenge-page heuristic."
                ),
                css={"h1::text": ["Next"]},
            )
        return FakeScraplingResponse(
            url,
            text=(
                "Dynamic page loaded with JavaScript and enough useful content for extraction. "
                "This body is intentionally long enough to avoid the crawler's challenge-page heuristic."
            ),
            css={
                "h1::text": ["Home"],
                "a::attr(href)": ["/next", "https://other.example/offsite"],
            },
            xpath={"//title/text()": ["Example"]},
        )


class FakeStealthyFetcher:
    calls = []

    @classmethod
    def fetch(cls, url, **kwargs):
        cls.calls.append((url, kwargs))
        return FakeScraplingResponse(url, text="Stealth page")


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
            with patch("funharness.src.core.webtools.search.urlopen", fake_urlopen):
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

    def test_web_fetch_cleans_html_and_detects_meta_charset(self) -> None:
        with patch("funharness.src.core.webtools.fetch.urlopen", return_value=FakeFetchResponse()):
            result = tool_web_fetch("https://example.com/page")

        self.assertIn("URL: https://example.com/page", result)
        self.assertIn("Final-URL: https://example.com/final", result)
        self.assertIn("Encoding: windows-1252", result)
        self.assertIn("Title & More", result)
        self.assertIn("café", result)
        self.assertNotIn("hide()", result)

    def test_web_fetch_rejects_non_http_urls(self) -> None:
        result = tool_web_fetch("file:///tmp/secret.txt")

        self.assertIn("only http and https URLs are supported", result)

    def test_web_crawl_auto_escalates_and_extracts_selectors(self) -> None:
        from funharness.src.core.webtools.crawl import _FetcherBundle

        bundle = _FetcherBundle(FakeStaticFetcher, FakeDynamicFetcher, FakeStealthyFetcher)
        FakeStaticFetcher.calls = []
        FakeDynamicFetcher.calls = []
        FakeStealthyFetcher.calls = []

        with patch("funharness.src.core.webtools.crawl._load_scrapling", return_value=bundle):
            result = tool_web_crawl(
                "https://example.com",
                strategy="auto",
                max_pages=2,
                max_depth=1,
                css_selectors={"title": "h1::text"},
                xpath_selectors={"page_title": "//title/text()"},
                headers={"X-Test": "1"},
                proxy="http://proxy.local:8080",
            )

        self.assertIsInstance(result, ToolResult)
        data = json.loads(result.content.split("\n\n", 1)[1])
        self.assertEqual(data["pages_crawled"], 2)
        self.assertIn("dynamic", data["strategies_used"])
        self.assertEqual(data["pages"][0]["css"]["title"], ["Home"])
        self.assertEqual(data["pages"][0]["xpath"]["page_title"], ["Example"])
        self.assertEqual(FakeDynamicFetcher.calls[0][1]["extra_headers"], {"X-Test": "1"})
        self.assertEqual(FakeDynamicFetcher.calls[0][1]["proxy"], "http://proxy.local:8080")
        self.assertFalse(FakeStealthyFetcher.calls)

    def test_web_crawl_reports_missing_scrapling(self) -> None:
        with patch("funharness.src.core.webtools.crawl._load_scrapling", side_effect=ImportError):
            result = tool_web_crawl("https://example.com")

        self.assertIn("Scrapling is not installed", result)
        self.assertIn("scrapling[fetchers]", result)

    def test_web_crawl_is_registered_as_web_tool(self) -> None:
        self.assertIn("tool_web_crawl", registry.list_tools("web"))


if __name__ == "__main__":
    unittest.main()
