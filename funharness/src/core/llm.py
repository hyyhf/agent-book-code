"""
FunHarness - LLM Client

OpenAI-compatible client with streaming, retry, and callback support.
Supports DeepSeek thinking mode with reasoning_content passthrough.
"""
import os
import time
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI, RateLimitError, APITimeoutError, APIConnectionError


def _find_env():
    """Walk up to find .env file."""
    d = Path(__file__).resolve().parent
    for _ in range(5):
        env = d / ".env"
        if env.exists():
            return env
        d = d.parent
    return None


_env = _find_env()
if _env:
    load_dotenv(_env)

client = OpenAI()
MODEL = os.getenv("OPENAI_MODEL_NAME", "deepseek-v4-flash")


def call_with_retry(messages, tools, stream=False, max_retries=3):
    """Call OpenAI API with exponential backoff retry.

    Enables DeepSeek thinking mode by default with reasoning_effort="high".
    """
    for attempt in range(max_retries):
        try:
            return client.chat.completions.create(
                model=MODEL,
                messages=messages,
                tools=tools or None,
                stream=stream,
                reasoning_effort="high",
                extra_body={"thinking": {"type": "enabled"}},
            )
        except (RateLimitError, APITimeoutError, APIConnectionError) as e:
            if attempt == max_retries - 1:
                raise
            time.sleep(2 ** attempt)


def process_stream_response(stream, on_token=None, on_reasoning_token=None,
                            cost_tracker=None):
    """Process streaming response, call on_token for each text chunk.

    Args:
        stream: OpenAI streaming response
        on_token: callback(str) for each content token
        on_reasoning_token: callback(str) for each reasoning/thinking token
        cost_tracker: optional CostTracker to update usage

    Returns:
        Assembled message dict with role, content, reasoning_content,
        and optional tool_calls
    """
    content_parts = []
    reasoning_parts = []
    tool_calls_data = {}

    for chunk in stream:
        if hasattr(chunk, "usage") and chunk.usage and cost_tracker:
            cost_tracker.update(chunk.usage)

        delta = chunk.choices[0].delta

        # Capture reasoning_content (thinking chain) from delta
        reasoning_text = getattr(delta, "reasoning_content", None)
        if reasoning_text:
            if on_reasoning_token:
                on_reasoning_token(reasoning_text)
            reasoning_parts.append(reasoning_text)

        if delta.content:
            if on_token:
                on_token(delta.content)
            content_parts.append(delta.content)

        if delta.tool_calls:
            for tc in delta.tool_calls:
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

    return msg
