"""
FunHarness - Agent Engine

Core agent loop as a class with callback-based integration for TUI.
"""
import json
import threading
import time
from pathlib import Path

from .core.tools import registry
from .core.llm import call_with_retry, process_stream_response, MODEL
from .core.system_prompt import build_system_prompt, build_environment_block, build_tools_guide
from .core.context import (
    CostTracker, estimate_tokens, build_context_block,
    truncate_tool_results, compact_conversation, should_compact,
)
from .core.memory import init_memory, read_memory, save_memory, search_memory
from .core.skills import SkillLoader
from .core.session import Session, SessionManager
from .core.permissions import (
    PermissionManager, PermissionMode, ApprovalFlow, SandboxExecutor, classify_risk,
)
from .core.hooks import (
    HookRegistry, HookAction, init_hooks, init_middleware, MiddlewareChain,
)
from .core.tasks import (
    TaskList, Task, TaskStatus, ProgressTracker, GitTracker,
    plan_tasks, pick_next_task, format_task_for_agent,
)
from .core.observability import (
    Tracer, SpanKind, StructuredLogger, LogLevel, CostDashboard,
    FailurePattern, FailureType,
)

MAX_ITERATIONS = 100


# ---- Extra tool functions registered on registry ----

_task_list: TaskList | None = None
_progress_tracker: ProgressTracker | None = None
_git_tracker: GitTracker | None = None


@registry.tool(category="memory")
def tool_read_memory() -> str:
    """Read all persistent memory content. Use when recalling saved knowledge."""
    return read_memory()


@registry.tool(category="memory")
def tool_save_memory(title: str, content: str) -> str:
    """Save a new persistent memory entry for future sessions.

    Args:
        title: Memory title, concise topic description
        content: Memory body, detailed content
    """
    return save_memory(title, content)


@registry.tool(category="memory")
def tool_search_memory(keyword: str) -> str:
    """Search persistent memories by keyword.

    Args:
        keyword: Search keyword
    """
    return search_memory(keyword)


@registry.tool(category="task")
def tool_view_tasks() -> str:
    """View current task list and progress."""
    if _task_list is None:
        return "(no task list loaded)"
    return _task_list.summary()


@registry.tool(category="task")
def tool_next_task() -> str:
    """Get the next pending task with full details."""
    if _task_list is None:
        return "(no task list loaded)"
    task = pick_next_task(_task_list)
    if task is None:
        return "All tasks completed or no executable task."
    task.start()
    return format_task_for_agent(task)


@registry.tool(category="task")
def tool_complete_task(task_id: str, files: str) -> str:
    """Mark a task as completed and record output files.

    Args:
        task_id: Task ID like T1, T2
        files: Output file paths, comma-separated
    """
    if _task_list is None:
        return "(no task list loaded)"
    task = _task_list.get(task_id)
    if not task:
        return f"Unknown task: {task_id}"
    artifacts = [f.strip() for f in files.split(",") if f.strip()]
    task.complete(artifacts=artifacts)
    if _progress_tracker:
        _progress_tracker.update(_task_list)
    commit_msg = ""
    if _git_tracker:
        commit_msg = _git_tracker.commit_task(task)
    done, total = _task_list.progress
    return f"Task {task_id} done. Progress: {done}/{total} ({_task_list.progress_pct:.0f}%). {commit_msg}"


@registry.tool(category="task")
def tool_fail_task(task_id: str, error: str) -> str:
    """Mark a task as failed with error reason.

    Args:
        task_id: Task ID
        error: Failure reason
    """
    if _task_list is None:
        return "(no task list loaded)"
    task = _task_list.get(task_id)
    if not task:
        return f"Unknown task: {task_id}"
    task.fail(error)
    if _progress_tracker:
        _progress_tracker.update(_task_list)
    return f"Task {task_id} failed: {error}"


class FunHarnessAgent:
    """The core FunHarness agent with callback-based integration.

    Callbacks:
        on_token(str): Called for each streaming token
        on_reasoning_token(str): Called for each thinking/reasoning token
        on_reasoning_start(): Called when reasoning output begins
        on_tool_gen(index, name, chunk): Called for each tool argument token
        on_tool_call(name, args_preview, risk): Called when a tool is invoked
        on_tool_result(name, result, hook_feedback): Called with tool result
        on_status(str): Called with status messages
        on_approval(tool_name, arguments, reason) -> (bool, str): Called for approval
    """

    def __init__(self, mode="suggest", on_token=None, on_reasoning_token=None,
                 on_reasoning_start=None, on_tool_gen=None, on_tool_call=None,
                 on_tool_result=None, on_status=None, on_approval=None):
        global _task_list, _progress_tracker, _git_tracker

        self.mode = PermissionMode(mode)
        self.on_token = on_token
        self.on_reasoning_token = on_reasoning_token
        self.on_reasoning_start = on_reasoning_start
        self.on_tool_gen = on_tool_gen
        self.on_tool_call = on_tool_call
        self.on_tool_result = on_tool_result
        self.on_status = on_status

        # Initialize subsystems
        init_memory()
        self.pm = PermissionManager(mode=self.mode)
        self.approval_flow = ApprovalFlow(self.pm, approval_callback=on_approval)
        self.session_mgr = SessionManager()
        self.cost_tracker = CostTracker(model=MODEL)
        self.hook_registry = init_hooks()
        self.middleware_chain = init_middleware()
        self.skill_loader = SkillLoader()

        # Observability
        self.tracer = Tracer()
        self.logger = StructuredLogger(level=LogLevel.INFO)
        self.dashboard = CostDashboard()

        # Task management
        _progress_tracker = ProgressTracker(".")
        _git_tracker = GitTracker(".")

        tasks_path = Path(".funharness/tasks.json")
        if tasks_path.exists():
            _task_list = TaskList.load(tasks_path)

        # Session state
        self._build_system_prompt()
        self.messages = [{"role": "system", "content": self._system_prompt}]
        self.current_session = Session()
        self.current_session.messages = self.messages
        self.tool_calls_history = []
        self._interrupt_event = threading.Event()

    def _build_system_prompt(self):
        memory_text = read_memory()
        task_summary = _task_list.summary() if _task_list else ""
        skills_summary = self.skill_loader.skills_summary()
        extra_context = build_context_block()
        self._system_prompt = build_system_prompt(
            registry, mode=self.mode.value, extra_context=extra_context,
            memory_text=memory_text, task_summary=task_summary,
            skills_summary=skills_summary,
        )

    def _emit_status(self, msg):
        if self.on_status:
            self.on_status(msg)

    def get_info(self) -> dict:
        """Return current agent state info."""
        return {
            "mode": self.mode.value,
            "tools": len(registry),
            "trace_id": self.tracer.trace_id,
            "messages": len(self.messages),
            "tokens": estimate_tokens(self.messages),
            "cost": self.cost_tracker.summary(),
        }

    def handle_slash_command(self, cmd: str) -> str | None:
        """Handle slash commands. Returns response string or None if not a command."""
        global _task_list

        parts = cmd.strip().split(maxsplit=1)
        command = parts[0].lower()
        arg = parts[1] if len(parts) > 1 else ""

        handlers = {
            "/help": lambda: self._help_text(),
            "/new": lambda: self._handle_new_session(),
            "/cost": lambda: f"{self.cost_tracker.summary()}",
            "/context": lambda: (
                f"System prompt: {len(self._system_prompt)} chars\n"
                f"Total context: ~{estimate_tokens(self.messages)} tokens\n"
                f"Messages: {len(self.messages)}"
            ),
            "/save": lambda: self._save_session(),
            "/memory": lambda: read_memory()[:800],
            "/mode": lambda: self._handle_mode(arg),
            "/perms": lambda: (
                f"Mode: {self.mode.value}\n"
                f"Allowed dirs: {[str(d) for d in self.pm.path_policy.allowed]}\n"
                f"Denied dirs: {[str(d) for d in self.pm.path_policy.denied]}"
            ),
            "/hooks": lambda: self.hook_registry.list_hooks(),
            "/middleware": lambda: self.middleware_chain.list_middlewares(),
            "/skills": lambda: self.skill_loader.skills_summary() or "No skills found.",
            "/tasks": lambda: (_task_list.summary() if _task_list else "No task list. Use /plan to create one."),
            "/next": lambda: self._handle_next(),
            "/progress": lambda: (_progress_tracker.read() if _progress_tracker else "(no progress tracker)"),
            "/trace": lambda: f"{self.tracer.timeline()}\n\n{self.tracer.summary()}",
            "/logs": lambda: self.logger.tail(15),
            "/dashboard": lambda: (self.dashboard.report() if self.dashboard._records else "(no data yet)"),
            "/failures": lambda: self._handle_failures(),
        }

        if command in handlers:
            return handlers[command]()

        if command == "/plan" and arg:
            return self._handle_plan(arg)

        if command == "/done" and arg:
            return self._handle_done(arg)

        if command == "/export":
            return self._handle_export()

        if command.startswith("/"):
            return f"Unknown command: {command}. Type /help for available commands."

        return None  # Not a slash command

    def _help_text(self):
        return (
            "Available commands:\n"
            "  /help       - Show this help\n"
            "  /new        - Start a new conversation session\n"
            "  /cost       - Show token usage and cost\n"
            "  /context    - Show context window info\n"
            "  /save       - Save current session\n"
            "  /memory     - Show saved memories\n"
            "  /mode [m]   - Show/change permission mode (auto/suggest/approve)\n"
            "  /perms      - Show permission settings\n"
            "  /hooks      - List registered hooks\n"
            "  /skills     - List available skills\n"
            "  /middleware  - List middleware chain\n"
            "  /plan <req> - Generate task list from requirement\n"
            "  /tasks      - View task list\n"
            "  /next       - Get next pending task\n"
            "  /done <id>  - Mark task as done\n"
            "  /progress   - Show progress file\n"
            "  /trace      - Show trace timeline\n"
            "  /logs       - Show recent logs\n"
            "  /dashboard  - Show cost dashboard\n"
            "  /failures   - Analyze failure patterns\n"
            "  /export     - Export observability data\n"
            "  clear       - Clear conversation\n"
            "  quit        - Exit FunHarness"
        )

    def _save_session(self):
        self.current_session.messages = self.messages
        return self.session_mgr.save(self.current_session)

    def _handle_new_session(self):
        """Start a new conversation session, saving the current one."""
        self.current_session.messages = self.messages
        self.session_mgr.save(self.current_session)
        # Reset conversation
        self.current_session = Session()
        self._build_system_prompt()
        self.messages = [{"role": "system", "content": self._system_prompt}]
        self.current_session.messages = self.messages
        self.tool_calls_history.clear()
        # Reset tracer for new session
        self.tracer = Tracer()
        return (
            f"[New Session] Previous session saved.\n"
            f"Trace: {self.tracer.trace_id}"
        )

    def _handle_mode(self, arg):
        if not arg:
            return f"Current mode: {self.mode.value}"
        new_mode = {"auto": PermissionMode.AUTO, "suggest": PermissionMode.SUGGEST,
                    "approve": PermissionMode.APPROVE}.get(arg.lower())
        if new_mode:
            self.mode = new_mode
            self.pm.mode = new_mode
            self._build_system_prompt()
            self.messages[0] = {"role": "system", "content": self._system_prompt}
            return f"Mode switched to: {self.mode.value}"
        return f"Unknown mode: {arg}. Use auto/suggest/approve."

    def _handle_next(self):
        if not _task_list:
            return "No task list. Use /plan to create one."
        task = pick_next_task(_task_list)
        if task:
            return format_task_for_agent(task)
        return "No executable tasks remaining."

    def _handle_plan(self, requirement):
        global _task_list
        self._emit_status(f"Planning tasks for: {requirement[:60]}...")
        _task_list = plan_tasks(requirement)
        _task_list.save(".funharness/tasks.json")
        if _progress_tracker:
            _progress_tracker.update(_task_list)
        self._build_system_prompt()
        self.messages[0] = {"role": "system", "content": self._system_prompt}
        return _task_list.summary()

    def _handle_done(self, arg):
        parts = arg.split(maxsplit=1)
        if _task_list:
            task_id = parts[0]
            files = parts[1] if len(parts) > 1 else ""
            return tool_complete_task(task_id, files)
        return "No task list loaded."

    def _handle_failures(self):
        findings = FailurePattern.analyze(self.messages, self.tool_calls_history)
        return FailurePattern.report(findings)

    def _handle_export(self):
        from datetime import datetime as dt
        export_dir = Path(".funharness/traces")
        export_dir.mkdir(parents=True, exist_ok=True)
        ts = dt.now().strftime("%Y%m%d_%H%M%S")
        self.tracer.save(export_dir / f"trace_{ts}.json")
        self.logger.export(export_dir / f"logs_{ts}.json")
        self.dashboard.export(export_dir / f"cost_{ts}.json")
        return f"Exported to {export_dir}/"

    def clear(self):
        """Clear conversation, save previous session."""
        self.current_session.messages = self.messages
        self.session_mgr.save(self.current_session)
        self.current_session = Session()
        self.messages = [{"role": "system", "content": self._system_prompt}]
        self.current_session.messages = self.messages
        self.tool_calls_history.clear()

    def request_interrupt(self):
        """Ask the current agent turn and any running command tool to stop."""
        self._interrupt_event.set()

    def clear_interrupt(self):
        """Reset interrupt state before a new turn."""
        self._interrupt_event.clear()

    def is_interrupted(self) -> bool:
        return self._interrupt_event.is_set()

    def _raise_if_interrupted(self):
        if self.is_interrupted():
            raise InterruptedError("Agent run interrupted by user")

    def run(self, user_input: str):
        """Execute one agent turn with the given user input.

        This is an async-compatible generator that yields events:
        Uses callbacks (on_token, on_tool_call, on_tool_result, on_status).
        """
        self.clear_interrupt()
        self.cost_tracker.mark_turn_start()
        self.messages.append({"role": "user", "content": user_input})
        tools = registry.get_openai_schemas()

        loop_span = self.tracer.start_span(SpanKind.AGENT_LOOP, "agent_loop",
                                           metadata={"input": user_input[:100]})

        for iteration in range(1, MAX_ITERATIONS + 1):
            self._raise_if_interrupted()
            # Middleware chain
            mw_context = {
                "messages": self.messages, "iteration": iteration,
                "tool_calls_history": self.tool_calls_history,
                "should_stop": False, "injections": [],
            }
            mw_context = self.middleware_chain.run(mw_context)

            if mw_context["injections"]:
                self.messages.append({
                    "role": "user",
                    "content": f"[SYSTEM MIDDLEWARE]\n" + "\n".join(mw_context["injections"]),
                })

            if mw_context["should_stop"]:
                self._emit_status("Middleware force stop")
                self.messages.append({
                    "role": "user",
                    "content": "[SYSTEM] Middleware detected issues. Summarize and stop.",
                })
                stream = call_with_retry(self.messages, tools, stream=True)
                msg = process_stream_response(
                    stream, on_token=self.on_token,
                    on_reasoning_token=self.on_reasoning_token,
                    on_tool_gen=self.on_tool_gen,
                    cost_tracker=self.cost_tracker,
                    should_interrupt=self.is_interrupted,
                )
                self.messages.append(msg)
                break

            # Context compaction
            if should_compact(self.messages):
                before = len(self.messages)
                self.messages = compact_conversation(self.messages)
                self._emit_status(f"Context compacted: {before} -> {len(self.messages)} messages")

            # LLM call with tracing
            llm_start = time.time()
            llm_span = self.tracer.start_span(SpanKind.LLM_CALL, "llm",
                                              metadata={"iteration": iteration})

            # Signal reasoning start for TUI thinking indicator
            self._reasoning_started = False
            original_on_reasoning = self.on_reasoning_token

            def _on_reasoning_wrapper(token):
                if not self._reasoning_started:
                    self._reasoning_started = True
                    if self.on_reasoning_start:
                        self.on_reasoning_start()
                if original_on_reasoning:
                    original_on_reasoning(token)

            stream = call_with_retry(self.messages, tools, stream=True)
            msg = process_stream_response(
                stream, on_token=self.on_token,
                on_reasoning_token=_on_reasoning_wrapper,
                on_tool_gen=self.on_tool_gen,
                cost_tracker=self.cost_tracker,
                should_interrupt=self.is_interrupted,
            )
            self.messages.append(msg)

            llm_duration = (time.time() - llm_start) * 1000
            self.tracer.finish_span(llm_span)
            self.logger.log_llm_call("llm", self.cost_tracker.total_input_tokens,
                                     self.cost_tracker.total_output_tokens, llm_duration)
            self.dashboard.record("agent_loop", self.cost_tracker.total_input_tokens,
                                  self.cost_tracker.total_output_tokens, duration_ms=llm_duration)

            # Check if model wants to use tools
            if "tool_calls" not in msg or not msg["tool_calls"]:
                # PreCompletion hook
                assistant_text = msg.get("content", "") or ""
                pre_completion = self.hook_registry.dispatch_pre_completion(assistant_text, self.messages)
                if pre_completion.action == HookAction.DENY:
                    self.messages.append({
                        "role": "user",
                        "content": f"[HOOK FEEDBACK] {pre_completion.feedback}\nPlease revise.",
                    })
                    continue
                break

            # Process tool calls
            for tc in msg["tool_calls"]:
                self._raise_if_interrupted()
                name = tc["function"]["name"]
                args_str = tc["function"]["arguments"]

                try:
                    args_preview = json.loads(args_str)
                    if "content" in args_preview:
                        c = args_preview["content"]
                        args_preview["content"] = c[:40] + "..." if len(c) > 40 else c
                    preview = json.dumps(args_preview, ensure_ascii=False)
                except Exception:
                    preview = args_str[:60]

                risk = classify_risk(name)
                if self.on_tool_call:
                    self.on_tool_call(name, preview, risk)

                result, hook_feedback = self._execute_tool(name, args_str)
                self._raise_if_interrupted()

                display = result if len(result) <= 200 else result[:200] + "...(truncated)"
                if self.on_tool_result:
                    self.on_tool_result(name, display, hook_feedback)

                try:
                    parsed_args = json.loads(args_str)
                except Exception:
                    parsed_args = {}
                self.tool_calls_history.append({
                    "tool": name, "args": parsed_args, "result": result[:500],
                })

                tool_content = result
                if hook_feedback:
                    tool_content += f"\n\n[Hook Feedback] {hook_feedback}"

                self.messages.append({
                    "role": "tool", "tool_call_id": tc["id"], "content": tool_content,
                })

            self.messages = truncate_tool_results(self.messages)

        else:
            self._emit_status("Max iterations reached.")

        self.tracer.finish_span(loop_span)

        # Auto failure analysis
        if self.tool_calls_history:
            findings = FailurePattern.analyze(self.messages, self.tool_calls_history)
            if findings:
                self.logger.warn(f"Failure patterns: {len(findings)}")

        self.current_session.messages = self.messages

    def _execute_tool(self, tool_name, arguments_json):
        """Execute a tool with hooks and permission checks."""
        try:
            args = json.loads(arguments_json)
        except json.JSONDecodeError as e:
            return f"Argument parse error: {e}", ""

        self._raise_if_interrupted()

        func = registry.get_function(tool_name)
        if not func:
            return f"Unknown tool: {tool_name}", ""

        schema = registry.get_schema(tool_name)
        if schema:
            required = schema["function"]["parameters"].get("required", [])
            missing = [p for p in required if p not in args]
            if missing:
                return f"Missing required parameters: {', '.join(missing)}", ""

        # PreToolUse hook
        pre_result = self.hook_registry.dispatch_pre_tool(tool_name, args)
        if pre_result.action == HookAction.DENY:
            return f"[HOOK DENIED] {pre_result.feedback}", ""
        if pre_result.modified_args:
            args = pre_result.modified_args

        # Permission check
        allowed, reason = self.approval_flow.pre_tool_check(tool_name, args)
        if not allowed:
            return f"[DENIED] {reason}", ""

        # Execute
        tool_start = time.time()
        tool_span = self.tracer.start_span(SpanKind.TOOL_CALL, tool_name)

        if tool_name == "tool_run_command":
            result = self.approval_flow.sandbox.execute(
                args.get("command", ""),
                should_interrupt=self.is_interrupted,
            )
        else:
            try:
                self._raise_if_interrupted()
                result = str(func(**args))
                self._raise_if_interrupted()
            except InterruptedError:
                raise
            except Exception as e:
                result = f"Tool execution failed ({tool_name}): {e}"

        tool_duration = (time.time() - tool_start) * 1000
        success = "Error" not in result and "Failed" not in result
        self.tracer.finish_span(tool_span, status="ok" if success else "error")
        self.logger.log_tool_call(tool_name, tool_duration, success, len(result))

        # PostToolUse hook
        post_result = self.hook_registry.dispatch_post_tool(tool_name, args, result)
        hook_feedback = post_result.feedback
        if pre_result.feedback:
            hook_feedback = (pre_result.feedback + "\n" + hook_feedback) if hook_feedback else pre_result.feedback

        return result, hook_feedback
