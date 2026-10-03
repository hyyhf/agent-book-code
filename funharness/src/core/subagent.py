"""
FunHarness - One-shot isolated subagents.
"""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from threading import Event
from typing import Callable

from .llm import MODEL, client, sanitize_messages_for_api, validate_request_size
from .runtime import RuntimeTaskManager, command_scope, current_commands
from .file_editing import FileEditSession, file_edit_scope
from .content import tool_content
from .context import ContextManager, truncate_tool_results


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
        self.context_state = {}

    def _system_prompt(self) -> str:
        extra = f"\n\nRole instructions:\n{self.instructions}" if self.instructions else ""
        return (
            f"You are a focused subagent with role '{self.role}'. "
            "Work in an isolated context. Return concise, actionable results. "
            "Do not claim to have edited files unless a tool result or task text proves it."
            " Read existing files with tool_read_file before editing; prefer start_line/limit and batch replacements."
            " On FILE_CHANGED re-read before retrying; do not bypass conflicts with shell writes."
            f"{extra}"
        )

    def run(self, task: str, context: str = "", cancel_event: Event | None = None,
            should_cancel: Callable[[], bool] | None = None,
            timeout_seconds: int | float | None = None) -> str:
        duration = self._MAX_RUNTIME_SECONDS if timeout_seconds is None else timeout_seconds
        deadline = time.monotonic() + float(duration)
        runtime = RuntimeTaskManager(root=Path(".funharness/runtime/subagents") / uuid.uuid4().hex)
        def stopped():
            return self._should_stop(cancel_event, should_cancel) or time.monotonic() >= deadline
        try:
            with command_scope(runtime, stopped), file_edit_scope(FileEditSession(), runtime.work_dir, stopped):
                return self._run(task, context, cancel_event, should_cancel, timeout_seconds)
        finally:
            runtime.shutdown()

    def _run(
        self,
        task: str,
        context: str = "",
        cancel_event: Event | None = None,
        should_cancel: Callable[[], bool] | None = None,
        timeout_seconds: int | float | None = None,
    ) -> str:
        if context:
            self.messages.append({"role": "user", "context_kind": "background", "content": f"Context:\n{context}"})
        self.messages.append({"role": "user", "content": task, "context_kind": "request"})
        deadline = time.time() + float(self._MAX_RUNTIME_SECONDS if timeout_seconds is None else timeout_seconds)

        tools = None
        if self.tool_registry is not None:
            tools = self.tool_registry.get_openai_schemas() or None

        context_manager = ContextManager(self.model, self.llm_client, self.context_state)

        def check_cancel():
            if self._should_stop(cancel_event, should_cancel) or time.time() >= deadline:
                raise InterruptedError("Subagent cancelled or timed out")

        def invoke(max_tokens):
            check_cancel()
            kwargs = {
                "model": self.model,
                "messages": sanitize_messages_for_api(self.messages),
                "temperature": 0.3,
                "reasoning_effort": "high",
                "extra_body": {"thinking": {"type": "enabled"}},
                "timeout": min(60, max(.1, deadline - time.time())),
                "max_tokens": max_tokens,
            }
            if tools:
                kwargs["tools"] = tools
            validate_request_size(kwargs)
            try:
                return self.llm_client.chat.completions.create(**kwargs)
            except TypeError as exc:
                if "timeout" not in str(exc):
                    raise
                kwargs.pop("timeout", None)
                return self.llm_client.chat.completions.create(**kwargs)

        for _ in range(self._MAX_ITERATIONS):
            if self._should_stop(cancel_event, should_cancel):
                return "(subagent cancelled)"
            remaining = deadline - time.time()
            if remaining <= 0:
                return "(subagent timed out)"
            try:
                response = context_manager.run(self.messages, tools, invoke, check_cancel=check_cancel)
            except InterruptedError:
                return "(subagent timed out)" if time.time() >= deadline else "(subagent cancelled)"
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
                runtime, stopped = current_commands()
                pending = runtime.pending_commands()
                if pending:
                    observation = runtime.wait(pending[0].runtime_id, 30000, stopped)
                    self.messages.append({"role": "user", "context_kind": "background",
                                          "content": "[COMMAND RUNTIME] Review before completion.\n" + observation})
                    continue
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
                        tool_result = tool_content(raw)
                    except Exception as exc:
                        tool_result = f"Tool error ({tool_name}): {type(exc).__name__}: {exc}"

                self.messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": tool_result,
                })

            self.messages = truncate_tool_results(self.messages)

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
