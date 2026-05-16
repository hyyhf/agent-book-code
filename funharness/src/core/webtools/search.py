from __future__ import annotations

import json
import os
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def tool_web_search(query: str):
    """Search the web using Tavily API and return results. Use for finding information online.

    Args:
        query: Search query string
    """
    api_key = os.getenv("TAVILY_API_KEY", "")
    if not api_key:
        return "Error: TAVILY_API_KEY not set in environment"

    try:
        payload = json.dumps({
            "api_key": api_key,
            "query": query,
            "max_results": 5,
            "include_answer": True,
            "search_depth": "basic",
        }).encode("utf-8")

        req = Request(
            "https://api.tavily.com/search",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "User-Agent": "FunHarness/0.8",
            },
            method="POST",
        )
        with urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        lines = []
        answer = data.get("answer", "")
        if answer:
            lines.append(f"**Answer:** {answer}\n")

        results = data.get("results", [])
        for i, r in enumerate(results, 1):
            title = r.get("title", "")
            url = r.get("url", "")
            snippet = r.get("content", "")
            lines.append(f"{i}. [{title}]({url})")
            lines.append(f"   {snippet}")

        content = "\n".join(lines) if lines else "No results found"
        from ..tools import ToolResult

        return ToolResult(content=content, display=_web_search_display(data, query))
    except HTTPError as e:
        return f"Web search failed: HTTP {e.code} {e.reason}"
    except URLError as e:
        return f"Web search failed: {e.reason}"
    except json.JSONDecodeError as e:
        return f"Web search failed: invalid JSON response ({e})"
    except Exception as e:
        return f"Web search failed: {e}"


def _web_search_display(data: dict[str, Any], fallback_query: str) -> dict[str, Any]:
    """Normalize Tavily data for GUI display without leaking raw provider fields."""
    normalized_results = []
    for item in data.get("results", []):
        if not isinstance(item, dict):
            continue
        normalized_results.append({
            "title": item.get("title") if isinstance(item.get("title"), str) else "",
            "url": item.get("url") if isinstance(item.get("url"), str) else "",
            "content": item.get("content") if isinstance(item.get("content"), str) else "",
            "score": item.get("score") if isinstance(item.get("score"), (int, float)) else None,
        })

    query = data.get("query")
    return {
        "kind": "web_search",
        "query": query if isinstance(query, str) and query else fallback_query,
        "images": [],
        "results": normalized_results,
    }
