from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from urllib.error import URLError
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
    def test_anysearch_is_primary_search_provider(self) -> None:
        captured = {}
        response = {
            "results": [
                {
                    "title": "Title",
                    "url": "https://example.com/page",
                    "description": "Short summary",
                    "content": "Longer body",
                    "score": 0.91,
                    "quality_score": 0.98,
                    "source": "web",
                    "raw_content": "Raw body",
                }
            ],
            "metadata": {"request_id": "req_test"},
        }

        def fake_urlopen(request, timeout=0):
            captured["url"] = request.full_url
            captured["payload"] = json.loads(request.data.decode("utf-8"))
            captured["authorization"] = request.get_header("Authorization")
            return FakeResponse(response)

        with patch.dict(os.environ, {"ANYSEARCH_API_KEY": "any-key"}, clear=False):
            with patch("funharness.src.core.webtools.search.urlopen", fake_urlopen):
                result = tool_web_search("jay")

        self.assertIsInstance(result, ToolResult)
        self.assertEqual(captured["url"], "https://api.anysearch.com/v1/search")
        self.assertEqual(captured["payload"], {"query": "jay", "max_results": 6})
        self.assertEqual(captured["authorization"], "Bearer any-key")
        self.assertIn("1. [Title](https://example.com/page)", result.content)
        self.assertIn("Short summary", result.content)
        self.assertEqual(result.display["provider"], "anysearch")
        self.assertEqual(result.display["images"], [])
        self.assertNotIn("raw_content", result.display["results"][0])
        self.assertNotIn("source", result.display["results"][0])

    def test_web_search_accepts_custom_max_results(self) -> None:
        captured = {}
        response = {"results": []}

        def fake_urlopen(request, timeout=0):
            captured["payload"] = json.loads(request.data.decode("utf-8"))
            return FakeResponse(response)

        with patch.dict(os.environ, {"ANYSEARCH_API_KEY": "any-key"}, clear=False):
            with patch("funharness.src.core.webtools.search.urlopen", fake_urlopen):
                result = tool_web_search("jay", max_results=12)

        self.assertIsInstance(result, ToolResult)
        self.assertEqual(captured["payload"]["max_results"], 12)

    def test_web_search_uses_saved_gui_search_key_before_env(self) -> None:
        captured = {}

        def fake_urlopen(request, timeout=0):
            captured["authorization"] = request.get_header("Authorization")
            return FakeResponse({"results": []})

        original_cwd = os.getcwd()
        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp) / ".funharness"
            config_dir.mkdir()
            (config_dir / "search_providers.json").write_text(
                json.dumps({"anysearch_api_key": "settings-key"}),
                encoding="utf-8",
            )
            os.chdir(tmp)
            try:
                with patch.dict(os.environ, {"ANYSEARCH_API_KEY": "env-key"}, clear=False):
                    with patch("funharness.src.core.webtools.search.urlopen", fake_urlopen):
                        result = tool_web_search("jay")
            finally:
                os.chdir(original_cwd)

        self.assertIsInstance(result, ToolResult)
        self.assertEqual(captured["authorization"], "Bearer settings-key")

    def test_web_search_schema_marks_max_results_as_optional_default(self) -> None:
        schema = registry.get_schema("tool_web_search")
        params = schema["function"]["parameters"]
        max_results = params["properties"]["max_results"]

        self.assertEqual(params["required"], ["query"])
        self.assertEqual(max_results["default"], 6)
        self.assertIn("omit unless the user asks", max_results["description"])

    def test_tavily_fallback_payload_omits_images_and_favicons_for_speed(self) -> None:
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
            if request.full_url == "https://api.anysearch.com/v1/search":
                raise URLError("anysearch unavailable")
            captured["payload"] = json.loads(request.data.decode("utf-8"))
            return FakeResponse(response)

        with patch.dict(os.environ, {"ANYSEARCH_API_KEY": "any-key", "TAVILY_API_KEY": "test-key"}):
            with patch("funharness.src.core.webtools.search.urlopen", fake_urlopen):
                result = tool_web_search("jay")

        self.assertIsInstance(result, ToolResult)
        self.assertEqual(captured["payload"]["api_key"], "test-key")
        self.assertEqual(captured["payload"]["max_results"], 6)
        self.assertNotIn("include_images", captured["payload"])
        self.assertNotIn("include_favicon", captured["payload"])
        self.assertIn("1. [Title](https://example.com/page)", result.content)
        self.assertIn("Snippet", result.content)
        self.assertEqual(result.display["provider"], "tavily")
        self.assertEqual(result.display["fallbacks"], [{"provider": "anysearch", "error": "anysearch unavailable"}])
        self.assertEqual(result.display["images"], [])
        self.assertNotIn("favicon", result.display["results"][0])
        self.assertNotIn("images", result.display["results"][0])
        self.assertIn("Search provider fallback used", result.content)
        self.assertIn("AnySearch: anysearch unavailable", result.content)

    def test_anysearch_unexpected_response_reports_error_fields(self) -> None:
        response_by_url = {
            "https://api.anysearch.com/v1/search": {
                "symbol": "invalid_api_key",
                "code": 40101,
                "message": "Invalid API key",
                "request_id": "req_test",
            },
            "https://api.tavily.com/search": {
                "query": "jay",
                "results": [{"title": "Fallback", "url": "https://example.com", "content": "Snippet"}],
            },
        }

        def fake_urlopen(request, timeout=0):
            return FakeResponse(response_by_url[request.full_url])

        with patch.dict(os.environ, {"ANYSEARCH_API_KEY": "bad-key", "TAVILY_API_KEY": "test-key"}):
            with patch("funharness.src.core.webtools.search.urlopen", fake_urlopen):
                result = tool_web_search("jay")

        self.assertEqual(result.display["provider"], "tavily")
        self.assertIn("invalid_api_key", result.display["fallbacks"][0]["error"])
        self.assertIn("Invalid API key", result.display["fallbacks"][0]["error"])
        self.assertIn("req_test", result.display["fallbacks"][0]["error"])

    def test_web_search_reports_both_provider_failures(self) -> None:
        def fake_urlopen(request, timeout=0):
            raise URLError("network down")

        with patch.dict(os.environ, {"ANYSEARCH_API_KEY": "any-key", "TAVILY_API_KEY": "test-key"}):
            with patch("funharness.src.core.webtools.search.urlopen", fake_urlopen):
                result = tool_web_search("jay")

        self.assertIn("Web search failed:", result)
        self.assertIn("AnySearch: network down", result)
        self.assertIn("Tavily: network down", result)

    def test_web_search_timeout_message_points_to_network_configuration(self) -> None:
        captured = {}

        def fake_urlopen(request, timeout=0):
            captured["timeout"] = timeout
            raise TimeoutError("_ssl.c:993: The handshake operation timed out")

        with patch.dict(os.environ, {"FUNHARNESS_WEB_SEARCH_TIMEOUT": "45", "ANYSEARCH_API_KEY": "any-key"}):
            with patch("funharness.src.core.webtools.search.urlopen", fake_urlopen):
                result = tool_web_search("jay")

        self.assertEqual(captured["timeout"], 45.0)
        self.assertIn("network timeout after 45s", result)
        self.assertIn("HTTPS_PROXY/HTTP_PROXY", result)

    def test_web_search_rejects_invalid_max_results(self) -> None:
        result = tool_web_search("jay", max_results=0)

        self.assertIn("max_results must be from 1 to 100", result)

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
