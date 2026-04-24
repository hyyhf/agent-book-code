"""
FunHarness - Hooks & Middleware

Lifecycle hooks (pre/post tool, pre-completion) + pluggable middleware chain.
"""
import os
from abc import ABC, abstractmethod
from collections import Counter
from enum import Enum
from typing import Callable, Any


# ================================================================
#  Hooks
# ================================================================

class HookEvent(Enum):
    PRE_TOOL_USE = "pre_tool_use"
    POST_TOOL_USE = "post_tool_use"
    PRE_COMPLETION = "pre_completion"


class HookAction(Enum):
    ALLOW = "allow"
    DENY = "deny"
    MODIFY = "modify"


class HookResult:
    def __init__(self, action=HookAction.ALLOW, feedback="", modified_args=None):
        self.action = action
        self.feedback = feedback
        self.modified_args = modified_args


class HookRegistry:
    def __init__(self):
        self._hooks: dict[HookEvent, list[dict]] = {e: [] for e in HookEvent}

    def register(self, event, handler, name="", priority=100, tool_matcher=None):
        self._hooks[event].append({
            "handler": handler, "name": name or handler.__name__,
            "priority": priority, "tool_matcher": tool_matcher,
        })
        self._hooks[event].sort(key=lambda h: h["priority"])

    def _matches(self, matcher, tool_name):
        if matcher is None:
            return True
        return tool_name in [p.strip() for p in matcher.split("|")]

    def dispatch_pre_tool(self, tool_name, arguments):
        current_args = arguments.copy()
        feedbacks = []
        for entry in self._hooks[HookEvent.PRE_TOOL_USE]:
            if not self._matches(entry["tool_matcher"], tool_name):
                continue
            result = entry["handler"](tool_name, current_args)
            if result.action == HookAction.DENY:
                return result
            if result.action == HookAction.MODIFY and result.modified_args:
                current_args = result.modified_args
            if result.feedback:
                feedbacks.append(result.feedback)
        return HookResult(
            action=HookAction.ALLOW, feedback="\n".join(feedbacks),
            modified_args=current_args if current_args != arguments else None,
        )

    def dispatch_post_tool(self, tool_name, arguments, result):
        feedbacks = []
        for entry in self._hooks[HookEvent.POST_TOOL_USE]:
            if not self._matches(entry["tool_matcher"], tool_name):
                continue
            hr = entry["handler"](tool_name, arguments, result)
            if hr.feedback:
                feedbacks.append(hr.feedback)
        return HookResult(action=HookAction.ALLOW, feedback="\n".join(feedbacks))

    def dispatch_pre_completion(self, assistant_message, messages):
        feedbacks = []
        for entry in self._hooks[HookEvent.PRE_COMPLETION]:
            result = entry["handler"](assistant_message, messages)
            if result.action == HookAction.DENY:
                return result
            if result.feedback:
                feedbacks.append(result.feedback)
        return HookResult(action=HookAction.ALLOW, feedback="\n".join(feedbacks))

    def list_hooks(self) -> str:
        lines = []
        for event in HookEvent:
            hooks = self._hooks[event]
            if hooks:
                lines.append(f"\n{event.value}:")
                for h in hooks:
                    m = f" [matcher: {h['tool_matcher']}]" if h["tool_matcher"] else ""
                    lines.append(f"  - {h['name']} (priority={h['priority']}){m}")
        return "\n".join(lines) if lines else "(no hooks registered)"


# ---- Built-in Hooks ----

def path_normalization_hook(tool_name, arguments):
    modified = arguments.copy()
    changed = False
    if "path" in modified and "~" in str(modified["path"]):
        modified["path"] = os.path.expanduser(modified["path"])
        changed = True
    if changed:
        return HookResult(action=HookAction.MODIFY, feedback="Path normalized (expanded ~)", modified_args=modified)
    return HookResult()


def large_file_guard_hook(tool_name, arguments):
    if tool_name != "tool_write_file":
        return HookResult()
    content = arguments.get("content", "")
    if len(content) > 50000:
        return HookResult(action=HookAction.DENY,
                          feedback=f"Write blocked: {len(content)} chars (limit 50000). Split into smaller writes.")
    return HookResult()


def lint_after_write_hook(tool_name, arguments, result):
    filepath = arguments.get("path", "")
    if filepath.endswith(".py") and "Error" not in result:
        return HookResult(feedback=f"[hook] Python file written: '{filepath}'. Consider running tests.")
    return HookResult()


def completion_checklist_hook(assistant_message, messages):
    if assistant_message and len(assistant_message.strip()) < 20:
        return HookResult(action=HookAction.DENY, feedback="Reply too brief. Provide a summary.")
    return HookResult()


def init_hooks() -> HookRegistry:
    hr = HookRegistry()
    hr.register(HookEvent.PRE_TOOL_USE, path_normalization_hook, name="path_normalizer", priority=10)
    hr.register(HookEvent.PRE_TOOL_USE, large_file_guard_hook, name="large_file_guard", priority=20,
                tool_matcher="tool_write_file")
    hr.register(HookEvent.POST_TOOL_USE, lint_after_write_hook, name="lint_reminder",
                tool_matcher="tool_write_file|tool_replace_in_file")
    hr.register(HookEvent.PRE_COMPLETION, completion_checklist_hook, name="completion_checklist")
    return hr


# ================================================================
#  Middleware
# ================================================================

class Middleware(ABC):
    @abstractmethod
    def process(self, context: dict) -> dict:
        ...


class MiddlewareChain:
    def __init__(self):
        self._middlewares: list[Middleware] = []

    def add(self, mw: Middleware):
        self._middlewares.append(mw)

    def run(self, context: dict) -> dict:
        for mw in self._middlewares:
            context = mw.process(context)
            if context.get("should_stop"):
                break
        return context

    def list_middlewares(self) -> str:
        if not self._middlewares:
            return "(no middlewares)"
        return "\n".join(f"  {i+1}. {type(m).__name__}" for i, m in enumerate(self._middlewares))


class LoopDetectionMiddleware(Middleware):
    def __init__(self, repeat_threshold=3, cycle_length=3, max_consecutive_errors=5):
        self.repeat_threshold = repeat_threshold
        self.cycle_length = cycle_length
        self.max_consecutive_errors = max_consecutive_errors

    def process(self, context):
        history = context.get("tool_calls_history", [])
        if not history:
            return context

        # Repeated calls
        if len(history) >= self.repeat_threshold:
            recent = history[-self.repeat_threshold:]
            sigs = [f"{h['tool']}:{sorted(h['args'].items())}" for h in recent]
            if len(set(sigs)) == 1:
                context.setdefault("injections", []).append(
                    f"[LOOP WARNING] {self.repeat_threshold} identical calls to '{recent[0]['tool']}'. Try different approach.")

        # Cyclic pattern
        if len(history) >= self.cycle_length * 2:
            tools = [h["tool"] for h in history[-(self.cycle_length * 2):]]
            if tools[:self.cycle_length] == tools[self.cycle_length:]:
                context.setdefault("injections", []).append(
                    f"[LOOP WARNING] Cyclic pattern detected. Break the cycle.")

        # Consecutive errors
        if len(history) >= self.max_consecutive_errors:
            recent = history[-self.max_consecutive_errors:]
            errs = ["Error", "Failed", "DENIED", "failed"]
            if all(any(kw in h.get("result", "") for kw in errs) for h in recent):
                context.setdefault("injections", []).append(
                    f"[LOOP WARNING] {self.max_consecutive_errors} consecutive errors. Stop and reassess.")
                context["should_stop"] = True

        return context


class SelfVerificationMiddleware(Middleware):
    VERIFY_TOOLS = {"tool_write_file", "tool_replace_in_file", "tool_run_command"}

    def __init__(self, verify_interval=3):
        self.verify_interval = verify_interval
        self._write_count = 0

    def process(self, context):
        history = context.get("tool_calls_history", [])
        if history and history[-1]["tool"] in self.VERIFY_TOOLS:
            self._write_count += 1
            if self._write_count % self.verify_interval == 0:
                context.setdefault("injections", []).append(
                    "[SELF-VERIFY] Several modifications made. Verify by reading files or running tests.")
        return context


class EnvironmentAwareMiddleware(Middleware):
    def __init__(self, max_iterations_warning=20):
        self.max_iterations_warning = max_iterations_warning
        self._last_cwd = None

    def process(self, context):
        injections = context.setdefault("injections", [])
        iteration = context.get("iteration", 0)
        if iteration == self.max_iterations_warning:
            injections.append(f"[ENV] Used {iteration} iterations. Consider wrapping up.")
        current_cwd = os.getcwd()
        if self._last_cwd and current_cwd != self._last_cwd:
            injections.append(f"[ENV] Working directory changed: {self._last_cwd} -> {current_cwd}")
        self._last_cwd = current_cwd
        if iteration > 0 and iteration % 10 == 0:
            history = context.get("tool_calls_history", [])
            if history:
                counter = Counter(h["tool"] for h in history)
                top3 = counter.most_common(3)
                stats = ", ".join(f"{n}={c}" for n, c in top3)
                injections.append(f"[ENV] Tool usage (top 3): {stats}")
        return context


def init_middleware() -> MiddlewareChain:
    chain = MiddlewareChain()
    chain.add(LoopDetectionMiddleware(repeat_threshold=3))
    chain.add(SelfVerificationMiddleware(verify_interval=3))
    chain.add(EnvironmentAwareMiddleware(max_iterations_warning=20))
    return chain
