from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

DEFAULT_SEARCH_TIMEOUT_SECONDS = 30


def tool_web_search(query: str, max_results: int = 6):
    """Search the web using AnySearch, with Tavily as a fallback. Use for finding information online.

    Args:
        query: Search query string
        max_results: Optional number of search results to return, from 1 to 100. Defaults to 6; omit unless the user asks for a specific count
    """
    normalized_max_results, error = _normalize_max_results(max_results)
    if error:
        return error

    failures = []
    for provider_name, search_func in (
        ("AnySearch", _search_anysearch),
        ("Tavily", _search_tavily),
    ):
        try:
            data = search_func(query, normalized_max_results)
            content = _web_search_content(data, provider_name)
            if failures:
                content = _fallback_note(failures) + "\n\n" + content
            from ..tools import ToolResult

            return ToolResult(content=content, display=_web_search_display(data, query, provider_name, failures))
        except _SearchProviderError as e:
            failures.append(f"{provider_name}: {e}")
        except Exception as e:
            failures.append(f"{provider_name}: {e}")

    return "Web search failed: " + "; ".join(failures)


class _SearchProviderError(Exception):
    pass


def _normalize_max_results(max_results: int) -> tuple[int, str | None]:
    try:
        value = int(max_results)
    except (TypeError, ValueError):
        return 0, "Error: max_results must be an integer from 1 to 100"
    if value < 1 or value > 100:
        return 0, "Error: max_results must be from 1 to 100"
    return value, None


def _request_timeout() -> float:
    raw_timeout = os.getenv("FUNHARNESS_WEB_SEARCH_TIMEOUT", "")
    if not raw_timeout:
        return DEFAULT_SEARCH_TIMEOUT_SECONDS
    try:
        timeout = float(raw_timeout)
    except ValueError:
        return DEFAULT_SEARCH_TIMEOUT_SECONDS
    if timeout <= 0:
        return DEFAULT_SEARCH_TIMEOUT_SECONDS
    return timeout


def _search_anysearch(query: str, max_results: int) -> dict[str, Any]:
    api_key = _search_api_key("anysearch_api_key", "ANYSEARCH_API_KEY")
    payload = json.dumps({
        "query": query,
        "max_results": max_results,
    }).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "FunHarness/0.8",
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    data = _post_json("https://api.anysearch.com/v1/search", payload, headers)
    data = _normalize_anysearch_response(data)
    return data


def _normalize_anysearch_response(data: dict[str, Any]) -> dict[str, Any]:
    results = data.get("results")
    if not isinstance(results, list):
        nested_data = data.get("data")
        if isinstance(nested_data, dict) and isinstance(nested_data.get("results"), list):
            normalized = dict(nested_data)
            metadata = data.get("metadata") if isinstance(data.get("metadata"), dict) else nested_data.get("metadata", {})
            normalized["metadata"] = metadata if isinstance(metadata, dict) else {}
            return normalized
        raise _SearchProviderError(_unexpected_anysearch_response(data))
    return data


def _unexpected_anysearch_response(data: dict[str, Any]) -> str:
    details = []
    for key in ("symbol", "code", "message", "request_id"):
        value = data.get(key)
        if isinstance(value, (str, int, float)) and value != "":
            details.append(f"{key}={value}")
    keys = ", ".join(sorted(str(key) for key in data.keys())) or "none"
    if details:
        return f"response missing results array ({'; '.join(details)}; keys: {keys})"
    return f"response missing results array (keys: {keys})"


def _search_tavily(query: str, max_results: int) -> dict[str, Any]:
    api_key = _search_api_key("tavily_api_key", "TAVILY_API_KEY")
    if not api_key:
        raise _SearchProviderError("TAVILY_API_KEY not set in environment")

    payload = json.dumps({
        "api_key": api_key,
        "query": query,
        "max_results": max_results,
        "include_answer": True,
        "search_depth": "basic",
    }).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "FunHarness/0.8",
    }
    data = _post_json("https://api.tavily.com/search", payload, headers)
    results = data.get("results")
    if results is not None and not isinstance(results, list):
        raise _SearchProviderError("response results field is not an array")
    return data


def _post_json(url: str, payload: bytes, headers: dict[str, str]) -> dict[str, Any]:
    timeout = _request_timeout()
    try:
        req = Request(url, data=payload, headers=headers, method="POST")
        with urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except HTTPError as e:
        raise _SearchProviderError(f"HTTP {e.code} {e.reason}") from e
    except URLError as e:
        raise _SearchProviderError(_format_network_error(e.reason, timeout)) from e
    except TimeoutError as e:
        raise _SearchProviderError(_format_network_error(e, timeout)) from e
    except OSError as e:
        raise _SearchProviderError(_format_network_error(e, timeout)) from e
    except json.JSONDecodeError as e:
        raise _SearchProviderError(f"invalid JSON response ({e})") from e

    if not isinstance(data, dict):
        raise _SearchProviderError("response is not a JSON object")
    return data


def _search_api_key(config_key: str, env_key: str) -> str:
    configured = _search_provider_config().get(config_key)
    if isinstance(configured, str) and configured.strip():
        return configured.strip()
    return os.getenv(env_key, "").strip()


def _search_provider_config() -> dict[str, Any]:
    path = Path.cwd() / ".funharness" / "search_providers.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _format_network_error(error: Any, timeout: float) -> str:
    message = str(error)
    if "timed out" in message.lower():
        return (
            f"network timeout after {timeout:g}s ({message}); "
            "check HTTPS_PROXY/HTTP_PROXY or set FUNHARNESS_WEB_SEARCH_TIMEOUT"
        )
    return message


def _fallback_note(failures: list[str]) -> str:
    return "Search provider fallback used. Previous provider failure(s): " + "; ".join(failures)


def _web_search_content(data: dict[str, Any], provider_name: str) -> str:
    lines = []
    if provider_name == "Tavily":
        answer = data.get("answer", "")
        if answer:
            lines.append(f"**Answer:** {answer}\n")

    results = data.get("results", [])
    if not isinstance(results, list):
        return "No results found"

    for i, r in enumerate(results, 1):
        if not isinstance(r, dict):
            continue
        title = r.get("title", "")
        url = r.get("url", "")
        snippet = _result_snippet(r)
        lines.append(f"{i}. [{title}]({url})")
        lines.append(f"   {snippet}")

    return "\n".join(lines) if lines else "No results found"


def _result_snippet(item: dict[str, Any]) -> str:
    for key in ("description", "content", "raw_content"):
        value = item.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def _web_search_display(
    data: dict[str, Any],
    fallback_query: str,
    provider_name: str,
    failures: list[str] | None = None,
) -> dict[str, Any]:
    """Normalize search data for GUI display without leaking raw provider fields."""
    normalized_results = []
    for item in data.get("results", []):
        if not isinstance(item, dict):
            continue
        score = item.get("score")
        if not isinstance(score, (int, float)):
            score = item.get("quality_score")
        normalized_results.append({
            "title": item.get("title") if isinstance(item.get("title"), str) else "",
            "url": item.get("url") if isinstance(item.get("url"), str) else "",
            "content": _result_snippet(item),
            "score": score if isinstance(score, (int, float)) else None,
        })

    query = data.get("query")
    return {
        "kind": "web_search",
        "provider": provider_name.lower(),
        "fallbacks": _display_fallbacks(failures or []),
        "query": query if isinstance(query, str) and query else fallback_query,
        "images": [],
        "results": normalized_results,
    }


def _display_fallbacks(failures: list[str]) -> list[dict[str, str]]:
    normalized = []
    for failure in failures:
        provider, separator, error = failure.partition(": ")
        normalized.append({
            "provider": provider.lower() if separator else "",
            "error": error if separator else failure,
        })
    return normalized
