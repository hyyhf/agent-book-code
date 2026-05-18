"""
FunHarness - One-shot isolated subagents.
"""
from __future__ import annotations

import json
import time
from threading import Event
from typing import Callable

from .llm import MODEL, client


class SubAgent:
    """One-shot isolated subagent with optional tool-calling support.

    When a tool_registry is provided, the subagent can invoke tools in a loop
    just like the main agent. Without it, the subagent is text-only.
    """

    _MAX_ITERATIONS = 60
    _MAX_RUNTIME_SECONDS = 600

    def __init__(
        self,
        role: str,
        instructions: str = "",
        model: str = MODEL,
        llm_client=None,
        tool_registry=None,
    ):
        self.role = role
        self.instructions = instructions
        self.model = model
        self.llm_client = llm_client or client
        self.tool_registry = tool_registry
        self.messages = [{"role": "system", "content": self._system_prompt()}]

    def _system_prompt(self) -> str:
        extra = f"\n\nRole instructions:\n{self.instructions}" if self.instructions else ""
        return (
            f"You are a focused subagent with role '{self.role}'. "
            "Work in an isolated context. Return concise, actionable results. "
            "Do not claim to have edited files unless a tool result or task text proves it."
            f"{extra}"
        )

    def run(
        self,
        task: str,
        context: str = "",
        cancel_event: Event | None = None,
        should_cancel: Callable[[], bool] | None = None,
        timeout_seconds: int | float | None = None,
    ) -> str:
        content = task if not context else f"Context:\n{context}\n\nTask:\n{task}"
        self.messages.append({"role": "user", "content": content})
        deadline = time.time() + float(self._MAX_RUNTIME_SECONDS if timeout_seconds is None else timeout_seconds)

        tools = None
        if self.tool_registry is not None:
            tools = self.tool_registry.get_openai_schemas() or None

        for _ in range(self._MAX_ITERATIONS):
            if self._should_stop(cancel_event, should_cancel):
                return "(subagent cancelled)"
            remaining = deadline - time.time()
            if remaining <= 0:
                return "(subagent timed out)"
            kwargs = {
                "model": self.model,
                "messages": self.messages,
                "temperature": 0.3,
                "reasoning_effort": "high",
                "extra_body": {"thinking": {"type": "enabled"}},
                "timeout": min(60, remaining),
            }
            if tools:
                kwargs["tools"] = tools
            try:
                response = self.llm_client.chat.completions.create(**kwargs)
            except TypeError:
                kwargs.pop("timeout", None)
                response = self.llm_client.chat.completions.create(**kwargs)
            if self._should_stop(cancel_event, should_cancel):
                return "(subagent cancelled)"
            choice = response.choices[0]
            msg = choice.message

            assistant_msg = {"role": "assistant", "content": msg.content or ""}
            reasoning = getattr(msg, "reasoning_content", None)
            if reasoning:
                assistant_msg["reasoning_content"] = reasoning
            if msg.tool_calls:
                assistant_msg["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in msg.tool_calls
                ]
            self.messages.append(assistant_msg)

            if not msg.tool_calls:
                return msg.content or ""

            for tc in msg.tool_calls:
                if self._should_stop(cancel_event, should_cancel):
                    return "(subagent cancelled)"
                if time.time() >= deadline:
                    return "(subagent timed out)"
                tool_name = tc.function.name
                try:
                    args = json.loads(tc.function.arguments)
                except (json.JSONDecodeError, TypeError):
                    args = {}

                func = self.tool_registry.get_function(tool_name)
                if func is None:
                    tool_result = f"Unknown tool: {tool_name}"
                else:
                    try:
                        raw = func(**args)
                        tool_result = str(raw)
                    except Exception as exc:
                        tool_result = f"Tool error ({tool_name}): {type(exc).__name__}: {exc}"

                self.messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": tool_result,
                })

        last_content = ""
        for message in reversed(self.messages):
            if message.get("role") == "assistant" and message.get("content"):
                last_content = message["content"]
                break
        return last_content or "(subagent reached max iterations)"

    @staticmethod
    def _should_stop(cancel_event: Event | None, should_cancel: Callable[[], bool] | None) -> bool:
        if cancel_event is not None and cancel_event.is_set():
            return True
        return bool(should_cancel and should_cancel())
