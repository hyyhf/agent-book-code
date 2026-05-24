"""
FunHarness - LLM Client

OpenAI-compatible client with streaming, retry, and callback support.
Supports DeepSeek thinking mode with reasoning_content passthrough.
"""
import os
import sys
import time
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI, RateLimitError, APITimeoutError, APIConnectionError


def _find_env():
    """Walk up to find .env file."""
    candidates = [Path.cwd()]
    workspace = os.getenv("FUNGUI_WORKSPACE", "").strip()
    if workspace:
        candidates.append(Path(workspace))
    explicit_env = os.getenv("FUNGUI_ENV_FILE", "").strip()
    if explicit_env:
        env = Path(explicit_env).expanduser()
        if env.exists():
            return env
    if getattr(sys, "frozen", False):
        candidates.append(Path(sys.executable).resolve().parent)
    candidates.append(Path(__file__).resolve().parent)
    seen = set()
    for start in candidates:
        d = start
        for _ in range(5):
            if d in seen:
                break
            seen.add(d)
            env = d / ".env"
            if env.exists():
                return env
            d = d.parent
    return None


_env = _find_env()
if _env:
    load_dotenv(_env, encoding="utf-8-sig")

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY") or "missing-api-key")
MODEL = os.getenv("OPENAI_MODEL_NAME", "deepseek-v4-flash")


def _to_jsonable(value):
    """Convert OpenAI SDK objects into JSON-serializable plain data."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, list):
        return [_to_jsonable(item) for item in value]
    if isinstance(value, tuple):
        return [_to_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(k): _to_jsonable(v) for k, v in value.items()}
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return _to_jsonable(model_dump(exclude_none=True))
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        return _to_jsonable(to_dict())
    return str(value)


def sanitize_messages_for_api(messages):
    """Strip session-only metadata before sending chat messages to a model."""
    sanitized = []
    for message in messages:
        role = message.get("role")
        if not role:
            continue

        clean = {"role": role}
        if "content" in message:
            clean["content"] = message.get("content")

        if role == "assistant":
            if "reasoning_content" in message:
                clean["reasoning_content"] = message.get("reasoning_content")
            if "tool_calls" in message:
                clean["tool_calls"] = message.get("tool_calls")
        elif role == "tool":
            if "tool_call_id" in message:
                clean["tool_call_id"] = message.get("tool_call_id")
        elif role == "function":
            if "name" in message:
                clean["name"] = message.get("name")

        sanitized.append(clean)
    return _repair_tool_call_boundaries(sanitized)


def _repair_tool_call_boundaries(messages):
    """Ensure assistant tool calls are followed by matching tool messages."""
    repaired = []
    i = 0
    while i < len(messages):
        message = messages[i]
        role = message.get("role")

        repaired.append(message)
        i += 1

        if role != "assistant" or not message.get("tool_calls"):
            continue

        expected_ids = [
            tc.get("id")
            for tc in message.get("tool_calls", []) or []
            if tc.get("id")
        ]
        seen_ids = set()

        while i < len(messages) and messages[i].get("role") == "tool":
            tool_message = messages[i]
            tool_call_id = tool_message.get("tool_call_id")
            if tool_call_id in expected_ids and tool_call_id not in seen_ids:
                repaired.append(tool_message)
                seen_ids.add(tool_call_id)
            i += 1

        for tool_call_id in expected_ids:
            if tool_call_id not in seen_ids:
                repaired.append({
                    "role": "tool",
                    "tool_call_id": tool_call_id,
                    "content": "[INTERRUPTED] Tool call did not complete before the previous turn was interrupted.",
                })

    return repaired


def call_with_retry(messages, tools, stream=False, max_retries=3, model=None, llm_client=None):
    """Call OpenAI API with exponential backoff retry.

    Enables DeepSeek thinking mode by default with reasoning_effort="high".
    """
    active_client = llm_client or client
    active_model = model or MODEL
    request = {
        "model": active_model,
        "messages": sanitize_messages_for_api(messages),
        "tools": tools or None,
        "stream": stream,
        "reasoning_effort": "high",
        "extra_body": {"thinking": {"type": "enabled"}},
    }
    if stream:
        request["stream_options"] = {"include_usage": True}

    for attempt in range(max_retries):
        try:
            return active_client.chat.completions.create(**request)
        except (RateLimitError, APITimeoutError, APIConnectionError) as e:
            if attempt == max_retries - 1:
                raise
            time.sleep(2 ** attempt)


def process_stream_response(stream, on_token=None, on_reasoning_token=None,
                            on_tool_gen=None, cost_tracker=None,
                            should_interrupt=None):
    """Process streaming response, call on_token for each text chunk.

    Args:
        stream: OpenAI streaming response
        on_token: callback(str) for each content token
        on_reasoning_token: callback(str) for each reasoning/thinking token
        on_tool_gen: callback(index, name, chunk) for each tool argument token
        cost_tracker: optional CostTracker to update usage

    Returns:
        Assembled message dict with role, content, reasoning_content,
        and optional tool_calls
    """
    content_parts = []
    reasoning_parts = []
    tool_calls_data = {}
    response_metadata = {}

    for chunk in stream:
        if should_interrupt and should_interrupt():
            close = getattr(stream, "close", None)
            if close:
                close()
            raise InterruptedError("Agent run interrupted")

        for attr in ("id", "object", "created", "model", "system_fingerprint"):
            value = getattr(chunk, attr, None)
            if value is not None and attr not in response_metadata:
                response_metadata[attr] = value

        usage = getattr(chunk, "usage", None)
        if usage:
            response_metadata["usage"] = _to_jsonable(usage)
            if cost_tracker:
                cost_tracker.update(usage)

        choices = getattr(chunk, "choices", None) or []
        if not choices:
            continue

        choice = choices[0]
        choice_index = getattr(choice, "index", None)
        if choice_index is not None:
            response_metadata["choice_index"] = choice_index
        finish_reason = getattr(choice, "finish_reason", None)
        if finish_reason is not None:
            response_metadata["finish_reason"] = finish_reason
        logprobs = getattr(choice, "logprobs", None)
        if logprobs is not None:
            response_metadata["logprobs"] = _to_jsonable(logprobs)

        delta = getattr(choice, "delta", None)
        if delta is None:
            continue

        # Capture reasoning_content (thinking chain) from delta
        reasoning_text = getattr(delta, "reasoning_content", None)
        if reasoning_text:
            if on_reasoning_token:
                on_reasoning_token(reasoning_text)
            reasoning_parts.append(reasoning_text)

        content_text = getattr(delta, "content", None)
        if content_text:
            if on_token:
                on_token(content_text)
            content_parts.append(content_text)

        tool_calls = getattr(delta, "tool_calls", None)
        if tool_calls:
            for tc in tool_calls:
                idx = tc.index
                if idx not in tool_calls_data:
                    tool_calls_data[idx] = {"id": "", "name": "", "arguments": ""}
                if tc.id:
                    tool_calls_data[idx]["id"] = tc.id
                if tc.function:
                    if tc.function.name:
                        tool_calls_data[idx]["name"] = tc.function.name
                    if tc.function.arguments:
                        tool_calls_data[idx]["arguments"] += tc.function.arguments
                        # Stream tool argument generation to UI
                        if on_tool_gen:
                            on_tool_gen(
                                idx,
                                tool_calls_data[idx]["name"],
                                tc.function.arguments,
                            )

    content = "".join(content_parts) if content_parts else None
    reasoning_content = "".join(reasoning_parts) if reasoning_parts else None

    msg = {"role": "assistant", "content": content}

    # CRITICAL: reasoning_content must be passed back to the API
    # for tool-calling rounds in DeepSeek thinking mode
    if reasoning_content is not None:
        msg["reasoning_content"] = reasoning_content

    if tool_calls_data:
        tc_list = []
        for idx in sorted(tool_calls_data):
            d = tool_calls_data[idx]
            tc_list.append({
                "id": d["id"],
                "type": "function",
                "function": {"name": d["name"], "arguments": d["arguments"]},
            })
        msg["tool_calls"] = tc_list

    if response_metadata:
        response_metadata["received_at"] = datetime.now().astimezone().isoformat()
        msg["response_metadata"] = response_metadata

    return msg
