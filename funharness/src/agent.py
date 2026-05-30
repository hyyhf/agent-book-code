"""
FunHarness - Agent Engine

Core agent loop as a class with callback-based integration for TUI.
"""
import inspect
import json
import platform
import shlex
import threading
import time
from contextvars import ContextVar
from pathlib import Path

from .core.tools import ToolResult, registry
from .core.attachments import AttachmentManager, DEFAULT_ATTACHMENT_MAX_CHARS
from .core.llm import call_with_retry, process_stream_response, MODEL, client
from .core.system_prompt import build_system_prompt, build_environment_block, build_tools_guide
from .core.context import (
    CostTracker, estimate_tokens, build_context_block,
    truncate_tool_results, compact_conversation, should_compact,
    CONTEXT_SOFT_LIMIT,
)
from .core.memory import init_memory, read_memory, save_memory, search_memory
from .core.persona import PersonaStore
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
    plan_tasks, pick_next_task, format_task_for_agent, _parse_list,
)
from .core.runtime import RuntimeTaskManager
from .core.schedule import ScheduleManager
from .core.team import TeamManager
from .core.subagent import SubAgent
from .core.observability import (
    Tracer, SpanKind, StructuredLogger, LogLevel, CostDashboard,
    FailurePattern, FailureType,
)

MAX_ITERATIONS = 100


# ---- Extra tool functions registered on registry ----

_current_agent = ContextVar("funharness_current_agent", default=None)


def _active_agent():
    """Return the agent currently executing a tool call, if any."""
    return _current_agent.get()


def _parse_key_values(text: str) -> dict[str, str]:
    values = {}
    try:
        parts = shlex.split(text)
    except ValueError:
        parts = text.split()
    for part in parts:
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        values[key.strip().lower()] = value.strip()
    return values


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


@registry.tool(category="skill")
def tool_list_skills() -> str:
    """List currently enabled skills with names, descriptions, paths, and diagnostics."""
    agent = _active_agent()
    if agent is None:
        return "(no active agent)"
    payload = {
        "skills": agent.skill_loader.list_skills(),
        "diagnostics": agent.skill_loader.diagnostics(),
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


@registry.tool(category="skill")
def tool_load_skill(name: str) -> str:
    """Load the full SKILL.md content for an enabled skill by name.

    Args:
        name: Skill name from the skills summary or tool_list_skills
    """
    agent = _active_agent()
    if agent is None:
        return "(no active agent)"
    skill = agent.skill_loader.get(name)
    if skill is None:
        return f"Skill not found or disabled: {name}"
    return (
        f"# Skill: {skill.name}\n"
        f"Description: {skill.description}\n"
        f"Path: {skill.path}\n"
        f"Source: {skill.source}\n\n"
        f"{skill.raw_content}"
    )


@registry.tool(category="file")
def tool_list_attachments() -> str:
    """List files attached to the current conversation session."""
    agent = _active_agent()
    if agent is None:
        return "(no active agent)"
    return agent.attachments.summary(include_preview=False)


@registry.tool(category="file")
def tool_read_attachment(attachment_id: str, max_chars: int = DEFAULT_ATTACHMENT_MAX_CHARS) -> str:
    """Read an attached file by attachment id, extracting text from supported formats.

    Args:
        attachment_id: Attachment id from tool_list_attachments or the attached files block
        max_chars: Maximum characters to return before truncating
    """
    agent = _active_agent()
    if agent is None:
        return "(no active agent)"
    return agent.attachments.read(attachment_id, max_chars=max_chars)


@registry.tool(category="task")
def tool_view_tasks() -> str:
    """View current task list and progress."""
    agent = _active_agent()
    if agent is None or agent.task_list is None:
        return "(no task list loaded)"
    return agent.task_list.summary()


@registry.tool(category="task")
def tool_next_task() -> str:
    """Get the next pending task with full details."""
    agent = _active_agent()
    if agent is None or agent.task_list is None:
        return "(no task list loaded)"
    task = pick_next_task(agent.task_list)
    if task is None:
        return "All tasks completed or no executable task."
    task.start()
    agent.progress_tracker.update(agent.task_list)
    agent.task_list.save(".funharness/tasks.json")
    return format_task_for_agent(task)


@registry.tool(category="task")
def tool_complete_task(task_id: str, files: str) -> str:
    """Mark a task as completed and record output files.

    Args:
        task_id: Task ID like T1, T2
        files: Output file paths, comma-separated
    """
    agent = _active_agent()
    if agent is None:
        return "(no active agent)"
    if agent.task_list is None:
        return "(no task list loaded)"
    return agent._complete_task(task_id, files)


@registry.tool(category="task")
def tool_fail_task(task_id: str, error: str) -> str:
    """Mark a task as failed with error reason.

    Args:
        task_id: Task ID
        error: Failure reason
    """
    agent = _active_agent()
    if agent is None:
        return "(no active agent)"
    if agent.task_list is None:
        return "(no task list loaded)"
    return agent._fail_task(task_id, error)


@registry.tool(category="task")
def tool_task_create(title: str, description: str = "", depends_on: str = "",
                     owner: str = "", verify: str = "") -> str:
    """Create a durable task in the current task graph.

    Args:
        title: Short task title
        description: Details and acceptance notes
        depends_on: Comma-separated dependency task IDs
        owner: Optional teammate or role name
        verify: How to verify completion
    """
    agent = _active_agent()
    if agent is None:
        return "(no active agent)"
    return agent._create_task(title, description, depends_on, owner, verify)


@registry.tool(category="task")
def tool_task_get(task_id: str) -> str:
    """Read one durable task by ID.

    Args:
        task_id: Task ID like T1
    """
    agent = _active_agent()
    if agent is None or agent.task_list is None:
        return "(no task list loaded)"
    task = agent.task_list.get(task_id)
    return json.dumps(task.to_dict(), ensure_ascii=False, indent=2) if task else f"Unknown task: {task_id}"


@registry.tool(category="task")
def tool_task_list(status: str = "") -> str:
    """List durable tasks, optionally filtered by status.

    Args:
        status: Optional status filter: pending/in_progress/done/failed/skipped
    """
    agent = _active_agent()
    if agent is None or agent.task_list is None:
        return "(no task list loaded)"
    if not status:
        return agent.task_list.summary()
    try:
        wanted = TaskStatus(status)
    except ValueError:
        return f"Unknown task status: {status}"
    lines = [repr(t) for t in agent.task_list.tasks if t.status == wanted]
    return "\n".join(lines) if lines else f"(no {status} tasks)"


@registry.tool(category="task")
def tool_task_update(task_id: str, status: str = "", owner: str = "",
                     notes: str = "", artifacts: str = "", error: str = "") -> str:
    """Update task status, owner, notes, artifacts, or error.

    Args:
        task_id: Task ID like T1
        status: Optional status: pending/in_progress/done/failed/skipped
        owner: Optional teammate or role name
        notes: Optional notes
        artifacts: Comma-separated output files
        error: Failure or skip reason
    """
    agent = _active_agent()
    if agent is None:
        return "(no active agent)"
    return agent._update_task(task_id, status, owner, notes, artifacts, error)


@registry.tool(category="task")
def tool_runtime_run(command: str, description: str = "", timeout: int = 300) -> str:
    """Run a slow shell command in the background runtime lane.

    Args:
        command: Shell command to execute
        description: Short description
        timeout: Timeout in seconds
    """
    agent = _active_agent()
    if agent is None:
        return "(no active agent)"
    runtime_id = agent.runtime.submit_command(command, description, timeout)
    return f"Runtime task started: {runtime_id}"


@registry.tool(category="task")
def tool_runtime_status(runtime_id: str = "") -> str:
    """Show background runtime task status.

    Args:
        runtime_id: Optional runtime task ID
    """
    agent = _active_agent()
    if agent is None:
        return "(no active agent)"
    if not runtime_id:
        return agent.runtime.summary()
    task = agent.runtime.get(runtime_id)
    return json.dumps(task.to_dict(), ensure_ascii=False, indent=2) if task else f"Unknown runtime task: {runtime_id}"


@registry.tool(category="task")
def tool_runtime_output(runtime_id: str) -> str:
    """Read full output for a runtime task.

    Args:
        runtime_id: Runtime task ID
    """
    agent = _active_agent()
    if agent is None:
        return "(no active agent)"
    return agent.runtime.output(runtime_id)


@registry.tool(category="task")
def tool_runtime_cancel(runtime_id: str) -> str:
    """Cancel a running background runtime task.

    Args:
        runtime_id: Runtime task ID
    """
    agent = _active_agent()
    if agent is None:
        return "(no active agent)"
    return agent.runtime.cancel(runtime_id)


@registry.tool(category="schedule")
def tool_schedule_create(name: str, when: str, prompt: str, recurring: bool = False) -> str:
    """Create a scheduled prompt that runs in a background runtime lane when due.

    Args:
        name: Human-readable schedule name
        when: Time rule, e.g. 'in 10m', ISO datetime, or cron '*/5 * * * *'
        prompt: Prompt injected when the schedule fires
        recurring: Whether non-cron rules should repeat
    """
    agent = _active_agent()
    if agent is None:
        return "(no active agent)"
    record = agent.scheduler.create(name, when, prompt, recurring)
    return (
        f"Schedule created: {record.schedule_id} [{record.when}] {record.name}\n"
        "When it fires, it will run in the background. Use /schedule to see the "
        "runtime id and /bg output <runtime_id> to read the result."
    )


@registry.tool(category="schedule")
def tool_schedule_list() -> str:
    """List scheduled prompts."""
    agent = _active_agent()
    return agent.scheduler.summary() if agent else "(no active agent)"


@registry.tool(category="schedule")
def tool_schedule_delete(schedule_id: str) -> str:
    """Delete a scheduled prompt.

    Args:
        schedule_id: Schedule ID like job_abcd1234
    """
    agent = _active_agent()
    if agent is None:
        return "(no active agent)"
    return f"Deleted {schedule_id}" if agent.scheduler.delete(schedule_id) else f"Unknown schedule: {schedule_id}"


@registry.tool(category="schedule")
def tool_schedule_run(schedule_id: str) -> str:
    """Run a scheduled prompt immediately in the background runtime lane.

    Args:
        schedule_id: Schedule ID like job_abcd1234
    """
    agent = _active_agent()
    if agent is None:
        return "(no active agent)"
    record = agent.scheduler.trigger(schedule_id)
    if record is None:
        return f"Unknown schedule: {schedule_id}"
    runtime = f" Runtime task: {record.last_runtime_id}" if record.last_runtime_id else ""
    error = f" Error: {record.last_run_error}" if record.last_run_error else ""
    return f"Schedule started: {record.schedule_id}.{runtime}{error}"


@registry.tool(category="agent")
def tool_subagent_run(role: str, task: str, context: str = "") -> str:
    """Run a one-shot isolated subagent and return its result.

    Args:
        role: Specialist role
        task: Task for the subagent
        context: Optional short context
    """
    agent = _active_agent()
    sub_tools = registry.subset(["file", "system", "search", "web", "memory"])
    if agent is not None:
        subagent = SubAgent(role, model=agent.model, llm_client=agent.llm_client,
                            tool_registry=sub_tools)
    else:
        subagent = SubAgent(role, tool_registry=sub_tools)
    return subagent.run(task, context)


@registry.tool(category="agent")
def tool_team_create(name: str, role: str, instructions: str = "") -> str:
    """Create or update a persistent teammate.

    Args:
        name: Teammate name
        role: Teammate role
        instructions: Standing instructions
    """
    agent = _active_agent()
    if agent is None:
        return "(no active agent)"
    member = agent.team.create(name, role, instructions)
    return f"Teammate ready: {member.name} [{member.role}]"


@registry.tool(category="agent")
def tool_team_rename(name: str, new_name: str) -> str:
    """Rename a persistent teammate and update the current team run.

    Args:
        name: Current teammate name
        new_name: New teammate name
    """
    agent = _active_agent()
    if agent is None:
        return "(no active agent)"
    try:
        member = agent.team.rename(name, new_name)
    except KeyError:
        return f"Unknown teammate: {name}"
    except ValueError as exc:
        return f"Could not rename teammate: {exc}"
    return f"Renamed teammate to {member.name}"


@registry.tool(category="agent")
def tool_team_list() -> str:
    """List persistent teammates."""
    agent = _active_agent()
    return agent.team.summary() if agent else "(no active agent)"


@registry.tool(category="agent")
def tool_team_tasks(run_id: str = "") -> str:
    """List tasks for the current or specified team run.

    Args:
        run_id: Optional team run ID. Uses the current run when omitted.
    """
    agent = _active_agent()
    if agent is None:
        return "(no active agent)"
    run = agent.team.get_run(run_id) if run_id else agent.team.current_run()
    if not run:
        return "(no active team run)"
    tasks = run.get("tasks", [])
    if not tasks:
        return "(no team tasks)"
    return json.dumps(tasks, ensure_ascii=False, indent=2)


@registry.tool(category="agent")
def tool_team_task_update(task_id: str, status: str = "", owner: str = "", output: str = "", run_id: str = "") -> str:
    """Update a task in the current team run.

    Args:
        task_id: Team task ID like TR1
        status: Optional status: pending/in_progress/done/failed/cancelled/completed
        owner: Optional teammate owner
        output: Optional result or failure summary
        run_id: Optional team run ID. Uses the current run when omitted.
    """
    agent = _active_agent()
    if agent is None:
        return "(no active agent)"
    run = agent.team.get_run(run_id) if run_id else agent.team.current_run()
    if not run:
        return "(no active team run)"
    target_run_id = run["run_id"]
    try:
        updated = agent.team.update_run_task(target_run_id, task_id, status=status, owner=owner, output=output)
    except KeyError:
        return f"Unknown team task: {task_id}"
    except ValueError as exc:
        return str(exc)
    if updated is None:
        return f"Unknown team run: {target_run_id}"
    task = next((item for item in updated.get("tasks", []) if item.get("task_id") == task_id), None)
    if not task:
        return f"Updated team task: {task_id}"
    return f"Updated {task_id}: {task.get('status', '')}"


@registry.tool(category="agent")
def tool_team_send(to: str, message: str) -> str:
    """Send a message to a teammate inbox.

    Args:
        to: Teammate name
        message: Message content
    """
    agent = _active_agent()
    if agent is None:
        return "(no active agent)"
    run = agent.team.current_run()
    if run:
        try:
            agent.team.send_run_message(run["run_id"], "lead", to, message)
        except KeyError:
            return f"Unknown teammate: {to}"
        return f"Message sent to {to} and recorded in team run {run['run_id']}"
    try:
        return agent.team.send("lead", to, message)
    except KeyError:
        return f"Unknown teammate: {to}"


@registry.tool(category="agent")
def tool_team_inbox(name: str) -> str:
    """Peek a teammate inbox without clearing it.

    Args:
        name: Teammate name
    """
    agent = _active_agent()
    if agent is None:
        return "(no active agent)"
    items = agent.team.peek_inbox(name)
    return json.dumps(items, ensure_ascii=False, indent=2) if items else "(inbox empty)"


@registry.tool(category="agent")
def tool_team_delegate(to: str, task: str, context: str = "") -> str:
    """Delegate work to a persistent teammate in the background.

    Args:
        to: Teammate name
        task: Work request
        context: Optional short context
    """
    agent = _active_agent()
    if agent is None:
        return "(no active agent)"
    try:
        runtime_id = agent.team.delegate(to, task, context)
    except KeyError:
        return f"Unknown teammate: {to}"
    except ValueError as exc:
        return str(exc)
    return f"Delegated to {to}. Runtime task: {runtime_id}"


@registry.tool(category="agent")
def tool_team_start(name: str) -> str:
    """Start a persistent teammate worker.

    Args:
        name: Teammate name
    """
    agent = _active_agent()
    if agent is None:
        return "(no active agent)"
    try:
        agent.team.start_member(name)
    except KeyError:
        return f"Unknown teammate: {name}"
    return f"Started teammate: {name}"


@registry.tool(category="agent")
def tool_team_cancel(name: str, reason: str = "") -> str:
    """Cancel a teammate's current task while keeping the worker alive.

    Args:
        name: Teammate name
        reason: Optional cancel reason
    """
    agent = _active_agent()
    if agent is None:
        return "(no active agent)"
    try:
        agent.team.cancel_member(name, reason=reason)
    except KeyError:
        return f"Unknown teammate: {name}"
    return f"Cancel requested for teammate: {name}"


@registry.tool(category="agent")
def tool_team_shutdown(name: str, reason: str = "", force: bool = False) -> str:
    """Shutdown a persistent teammate worker.

    Args:
        name: Teammate name
        reason: Optional shutdown reason
        force: Whether to drop queued tasks before stopping
    """
    agent = _active_agent()
    if agent is None:
        return "(no active agent)"
    try:
        agent.team.shutdown_member(name, reason=reason, force=force)
    except KeyError:
        return f"Unknown teammate: {name}"
    return f"Shutdown requested for teammate: {name}"


class FunHarnessAgent:
    """The core FunHarness agent with callback-based integration.

    Callbacks:
        on_token(str): Called for each streaming token
        on_reasoning_token(str): Called for each thinking/reasoning token
        on_reasoning_start(): Called when reasoning output begins
        on_reasoning_done(): Called when reasoning output ends
        on_tool_gen(index, name, chunk): Called for each tool argument token
        on_tool_call(name, args_preview, risk): Called when a tool is invoked
        on_tool_result(name, result, hook_feedback, display): Called with tool result
        on_plan_token(str): Called while slash /plan is generating tasks
        on_status(str): Called with status messages
        on_approval(tool_name, arguments, reason) -> (bool, str): Called for approval
    """

    def __init__(self, mode="suggest", on_token=None, on_reasoning_token=None,
                 on_reasoning_start=None, on_reasoning_done=None,
                 on_tool_gen=None, on_tool_call=None, on_tool_result=None,
                 on_plan_token=None, on_status=None, on_approval=None,
                 model=MODEL, llm_client=None):
        self.mode = PermissionMode(mode)
        self.model = model
        self.llm_client = llm_client or client
        self.on_token = on_token
        self.on_reasoning_token = on_reasoning_token
        self.on_reasoning_start = on_reasoning_start
        self.on_reasoning_done = on_reasoning_done
        self.on_tool_gen = on_tool_gen
        self.on_tool_call = on_tool_call
        self.on_tool_result = on_tool_result
        self.on_plan_token = on_plan_token
        self.on_status = on_status

        # Initialize subsystems
        init_memory()
        self.pm = PermissionManager(mode=self.mode)
        self.approval_flow = ApprovalFlow(self.pm, approval_callback=on_approval)
        self.session_mgr = SessionManager()
        self.cost_tracker = CostTracker(model=self.model)
        self.hook_registry = init_hooks()
        self.middleware_chain = init_middleware()
        self.skill_loader = SkillLoader()
        self.persona_store = PersonaStore()
        self.active_persona_id: str | None = self.persona_store.get_active_id()

        # Observability
        self.tracer = Tracer()
        self.logger = StructuredLogger(level=LogLevel.INFO)
        self.dashboard = CostDashboard()

        # Task management
        self.task_list: TaskList | None = None
        self.progress_tracker = ProgressTracker(".")
        self.git_tracker = GitTracker(".")
        self.runtime = RuntimeTaskManager(work_dir=".")
        self.scheduler = ScheduleManager(on_fire=self._run_scheduled_prompt)
        self.team = TeamManager(runtime=self.runtime, model=self.model, llm_client=self.llm_client,
                                tool_registry=registry.subset(["file", "system", "search", "web", "memory"]))
        self.scheduler.start()

        tasks_path = Path(".funharness/tasks.json")
        if tasks_path.exists():
            self.task_list = TaskList.load(tasks_path)

        # Session state
        self._build_system_prompt()
        self.messages = [{"role": "system", "content": self._system_prompt}]
        # Inject begin_dialogs for persisted active persona
        self._inject_begin_dialogs_for_active_persona()
        self.current_session = Session()
        self.current_session.messages = self.messages
        self.attachments = AttachmentManager(self.current_session.id)
        self.tool_calls_history = []
        self._interrupt_event = threading.Event()

    def _build_system_prompt(self):
        memory_text = read_memory()
        summaries = []
        if self.task_list:
            summaries.append(self.task_list.summary())
        summaries.append(self.runtime.summary())
        summaries.append(self.scheduler.summary())
        summaries.append(self.team.summary())
        task_summary = "\n\n".join(s for s in summaries if s)
        skills_summary = self.skill_loader.skills_summary()
        extra_context = build_context_block()
        persona_prompt = ""
        if self.active_persona_id:
            persona = self.persona_store.get(self.active_persona_id)
            if persona:
                persona_prompt = persona.system_prompt
        self._system_prompt = build_system_prompt(
            registry, mode=self.mode.value, extra_context=extra_context,
            memory_text=memory_text, task_summary=task_summary,
            skills_summary=skills_summary, persona_prompt=persona_prompt,
        )

    def _emit_status(self, msg):
        if self.on_status:
            self.on_status(msg)

    def _run_scheduled_prompt(self, record) -> str:
        def _work():
            context_parts = []
            if self.task_list:
                context_parts.append(self.task_list.summary())
            context_parts.append(self.team.summary())
            context = "\n\n".join(part for part in context_parts if part)
            subagent = SubAgent(
                "scheduled-worker",
                "Execute the scheduled prompt and return the useful result. "
                "Be concise, concrete, and include any follow-up actions if needed.",
                model=self.model,
                llm_client=self.llm_client,
                tool_registry=registry.subset(["file", "system", "search", "web", "memory"]),
            )
            return subagent.run(record.prompt, context=context)

        runtime_id = self.runtime.submit_callable(
            "schedule",
            f"{record.name}: {record.prompt[:80]}",
            _work,
        )
        self._emit_status(f"Scheduled prompt started: {record.schedule_id} -> {runtime_id}")
        return runtime_id

    def _drain_external_events(self, inject: bool = True) -> str:
        events = []
        events.extend(self.runtime.drain_notifications())
        events.extend(self.scheduler.drain_notifications())
        if not events:
            return ""

        lines = []
        for event in events:
            if event.get("type") == "runtime_completed":
                lines.append(
                    f"[runtime:{event['runtime_id']}] {event['status']}\n"
                    f"{event.get('preview', '')}\n"
                    f"Full output: {event.get('output_file', '')}"
                )
            elif event.get("type") == "scheduled_prompt":
                runtime = f"\nRuntime task: {event.get('runtime_id')}" if event.get("runtime_id") else ""
                error = f"\nError: {event.get('error')}" if event.get("error") else ""
                lines.append(
                    f"[scheduled:{event['schedule_id']}] {event.get('name', '')}\n"
                    f"{event.get('prompt', '')}{runtime}{error}"
                )

        text = "\n\n".join(lines)
        if inject:
            self.messages.append({
                "role": "user",
                "content": f"[FUNHARNESS EVENTS]\n{text}",
            })
        self._emit_status(f"Received {len(events)} runtime/schedule event(s)")
        return text

    def get_info(self) -> dict:
        """Return current agent state info."""
        active_persona = None
        if self.active_persona_id:
            active_persona = self.persona_store.get(self.active_persona_id)
        return {
            "mode": self.mode.value,
            "tools": len(registry),
            "trace_id": self.tracer.trace_id,
            "messages": len(self.messages),
            "tokens": estimate_tokens(self.messages),
            "cost": self.cost_tracker.summary(),
            "tasks_ready": len(self.task_list.ready()) if self.task_list else 0,
            "teammates": len(self.team.list()),
            "runtime_tasks": len(self.runtime.list()),
            "schedules": len(self.scheduler.list()),
            "active_persona_id": self.active_persona_id,
            "active_persona_name": active_persona.name if active_persona else None,
        }

    def set_model_client(self, model: str, llm_client) -> None:
        self.model = model
        self.llm_client = llm_client
        pricing_tracker = CostTracker(model=model)
        pricing_tracker.total_input_tokens = self.cost_tracker.total_input_tokens
        pricing_tracker.total_output_tokens = self.cost_tracker.total_output_tokens
        pricing_tracker.call_count = self.cost_tracker.call_count
        self.cost_tracker = pricing_tracker
        self.team.model = model
        self.team.llm_client = llm_client

    def handle_slash_command(self, cmd: str) -> str | None:
        """Handle slash commands. Returns response string or None if not a command."""
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
                f"Messages: {len(self.messages)}\n"
                f"Soft limit: {CONTEXT_SOFT_LIMIT:,} chars"
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
            "/tasks": lambda: (self.task_list.summary() if self.task_list else "No task list. Use /plan to create one."),
            "/next": lambda: self._handle_next(),
            "/progress": lambda: self.progress_tracker.read(),
            "/team": lambda: self._handle_team(arg),
            "/bg": lambda: self._handle_runtime(arg),
            "/schedule": lambda: self._handle_schedule(arg),
            "/events": lambda: self._drain_external_events(inject=False) or "(no pending events)",
            "/trace": lambda: f"{self.tracer.timeline()}\n\n{self.tracer.summary()}",
            "/logs": lambda: self.logger.tail(15),
            "/dashboard": lambda: (self.dashboard.report() if self.dashboard._records else "(no data yet)"),
            "/failures": lambda: self._handle_failures(),
            "/attachments": lambda: self.attachments.summary(include_preview=True),
            "/persona": lambda: self._handle_persona(arg),
        }

        if command in handlers:
            return handlers[command]()

        if command == "/plan" and arg:
            return self._handle_plan(arg)

        if command == "/done" and arg:
            return self._handle_done(arg)

        if command == "/task":
            return self._handle_task(arg)

        if command == "/attach":
            if not arg:
                return "Usage: /attach <path1> [path2 ...]"
            return self._handle_attach(arg)

        if command == "/detach":
            if not arg:
                return "Usage: /detach <id|all>"
            return self._handle_detach(arg)

        if command == "/delegate" and arg:
            return self._handle_delegate(arg)

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
            "  /attach <p> - Attach one or more files to this session\n"
            "  /attachments - List current session attachments\n"
            "  /detach <id|all> - Remove attachment references\n"
            "  /mode [m]   - Show/change permission mode (auto/suggest/approve)\n"
            "  /perms      - Show permission settings\n"
            "  /hooks      - List registered hooks\n"
            "  /skills     - List available skills\n"
            "  /middleware  - List middleware chain\n"
            "  /plan <req> - Generate task list from requirement\n"
            "  /task ...   - Create/update/read a durable task\n"
            "  /tasks      - View task list\n"
            "  /next       - Get next pending task\n"
            "  /done <id>  - Mark task as done\n"
            "  /progress   - Show progress file\n"
            "  /team       - List/create teammates\n"
            "  /delegate   - Delegate work to a teammate\n"
            "  /bg         - Run/check background runtime tasks\n"
            "  /schedule   - Create/list/run/delete scheduled prompts\n"
            "  /events     - Drain runtime/schedule notifications\n"
            "  /trace      - Show trace timeline\n"
            "  /logs       - Show recent logs\n"
            "  /dashboard  - Show cost dashboard\n"
            "  /failures   - Analyze failure patterns\n"
            "  /export     - Export observability data\n"
            "  /persona    - List/use/create/delete personas\n"
            "  clear       - Clear conversation\n"
            "  quit        - Exit FunHarness"
        )

    def _save_session(self):
        self.current_session.messages = self.messages
        self.attachments.save()
        return self.session_mgr.save(self.current_session)

    def _handle_new_session(self):
        """Start a new conversation session, saving the current one."""
        self.current_session.messages = self.messages
        self.session_mgr.save(self.current_session)
        # Reset conversation
        self.current_session = Session()
        self.attachments = AttachmentManager(self.current_session.id)
        self._build_system_prompt()
        self.messages = [{"role": "system", "content": self._system_prompt}]
        # Re-inject begin_dialogs for active persona
        self._inject_begin_dialogs_for_active_persona()
        self.current_session.messages = self.messages
        self.tool_calls_history.clear()
        # Reset tracer for new session
        self.tracer = Tracer()
        return (
            f"[New Session] Previous session saved.\n"
            f"Trace: {self.tracer.trace_id}"
        )

    def _handle_attach(self, arg: str) -> str:
        try:
            paths = shlex.split(arg, posix=(platform.system() != "Windows"))
        except ValueError as exc:
            return f"Attach failed: {exc}"
        paths = [p.strip().strip('"').strip("'") for p in paths if p.strip()]
        if not paths:
            return "Usage: /attach <path1> [path2 ...]"

        lines = ["Attached files:"]
        for path in paths:
            try:
                record = self.attachments.add(path)
            except Exception as exc:
                lines.append(f"- {path}: failed ({exc})")
                continue
            lines.append(
                f"- {record.id} {record.original_name} "
                f"({record.extension or record.mime_type}, {record.size} bytes, {record.parse_status})"
            )
        return "\n".join(lines)

    def _handle_detach(self, arg: str) -> str:
        target = arg.strip().split(maxsplit=1)[0] if arg.strip() else ""
        if not target:
            return "Usage: /detach <id|all>"
        return self.attachments.detach(target)

    def _attachment_context(self) -> str:
        if not self.attachments.list():
            return ""
        return (
            "The user has attached files to this session. Use "
            "tool_list_attachments and tool_read_attachment to inspect them "
            "when needed; do not assume the preview is complete.\n\n"
            f"{self.attachments.summary(include_preview=True)}"
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

    def _handle_persona(self, arg: str) -> str:
        """Handle /persona slash command family."""
        parts = arg.strip().split(maxsplit=2) if arg.strip() else []
        sub = parts[0].lower() if parts else ""

        if not sub:
            # List all personas with active indicator
            personas = self.persona_store.list_all()
            if not personas:
                return (
                    "No personas defined.\n"
                    "Usage:\n"
                    "  /persona create <id> <name>  - Create a new persona\n"
                    "  /persona use <id>            - Activate a persona\n"
                    "  /persona off                 - Deactivate current persona\n"
                    "  /persona show <id>           - Show persona details\n"
                    "  /persona delete <id>         - Delete a persona"
                )
            lines = ["Personas:"]
            for p in personas:
                active = " [ACTIVE]" if p.persona_id == self.active_persona_id else ""
                lines.append(f"  - {p.persona_id}: {p.name}{active}")
            lines.append("")
            lines.append(f"Active: {self.active_persona_id or '(none / default)'}")
            return "\n".join(lines)

        if sub == "use":
            if len(parts) < 2:
                return "Usage: /persona use <persona_id>"
            return self.set_persona(parts[1])

        if sub == "off":
            return self.clear_persona()

        if sub == "create":
            if len(parts) < 3:
                return "Usage: /persona create <id> <name>"
            persona_id = parts[1]
            name = parts[2]
            try:
                persona = self.persona_store.create(
                    persona_id=persona_id,
                    name=name,
                    system_prompt=f"You are {name}. Follow user instructions carefully.",
                )
                return (
                    f"Persona '{persona_id}' created: {name}\n"
                    f"Edit its system_prompt via the GUI or by editing "
                    f".funharness/personas.json directly."
                )
            except ValueError as exc:
                return str(exc)

        if sub == "delete":
            if len(parts) < 2:
                return "Usage: /persona delete <persona_id>"
            persona_id = parts[1]
            if persona_id == self.active_persona_id:
                self.clear_persona()
            try:
                self.persona_store.delete(persona_id)
                return f"Persona '{persona_id}' deleted."
            except ValueError as exc:
                return str(exc)

        if sub == "show":
            if len(parts) < 2:
                return "Usage: /persona show <persona_id>"
            persona = self.persona_store.get(parts[1])
            if persona is None:
                return f"Unknown persona: {parts[1]}"
            active = " [ACTIVE]" if persona.persona_id == self.active_persona_id else ""
            lines = [
                f"Persona: {persona.persona_id}{active}",
                f"Name: {persona.name}",
                f"Created: {persona.created_at}",
                f"Updated: {persona.updated_at}",
                f"Begin dialogs: {len(persona.begin_dialogs)} entries",
                f"System prompt ({len(persona.system_prompt)} chars):",
                persona.system_prompt[:500],
            ]
            if len(persona.system_prompt) > 500:
                lines.append("...(truncated)")
            return "\n".join(lines)

        return (
            "Unknown /persona sub-command. Usage:\n"
            "  /persona                    - List all personas\n"
            "  /persona use <id>           - Activate a persona\n"
            "  /persona off                - Deactivate current persona\n"
            "  /persona create <id> <name> - Create a new persona\n"
            "  /persona show <id>          - Show persona details\n"
            "  /persona delete <id>        - Delete a persona"
        )

    def set_persona(self, persona_id: str) -> str:
        """Activate a persona by ID. Rebuilds system prompt."""
        persona = self.persona_store.get(persona_id)
        if persona is None:
            return f"Unknown persona: {persona_id}"
        self.active_persona_id = persona_id
        self.persona_store.set_active_id(persona_id)
        self._build_system_prompt()
        if self.messages and self.messages[0].get("role") == "system":
            self.messages[0] = {"role": "system", "content": self._system_prompt}
        else:
            self.messages.insert(0, {"role": "system", "content": self._system_prompt})
        self._inject_begin_dialogs(persona)
        return f"Persona activated: {persona.name} ({persona_id})"

    def clear_persona(self) -> str:
        """Deactivate the current persona. Reverts to default identity."""
        self.active_persona_id = None
        self.persona_store.set_active_id(None)
        self._build_system_prompt()
        if self.messages and self.messages[0].get("role") == "system":
            self.messages[0] = {"role": "system", "content": self._system_prompt}
        else:
            self.messages.insert(0, {"role": "system", "content": self._system_prompt})
        return "Persona deactivated. Using default identity."

    def _inject_begin_dialogs(self, persona) -> None:
        """Inject begin_dialogs from a persona into messages (only when conversation is fresh)."""
        if not persona.begin_dialogs:
            return
        # Only inject when conversation has just the system prompt
        if len(self.messages) > 1:
            return
        for dialog in persona.begin_dialogs:
            role = dialog.get("role", "user")
            content = dialog.get("content", "")
            if role in ("user", "assistant") and content:
                self.messages.append({"role": role, "content": content})

    def _inject_begin_dialogs_for_active_persona(self) -> None:
        """Re-inject begin_dialogs for the currently active persona, if any."""
        if not self.active_persona_id:
            return
        persona = self.persona_store.get(self.active_persona_id)
        if persona:
            self._inject_begin_dialogs(persona)

    def _handle_next(self):
        if not self.task_list:
            return "No task list. Use /plan to create one."
        task = pick_next_task(self.task_list)
        if task:
            return format_task_for_agent(task)
        return "No executable tasks remaining."

    def _handle_plan(self, requirement):
        self._emit_status(f"Planning tasks for: {requirement[:60]}...")
        reasoning_started = False

        def _on_plan_reasoning(token: str):
            nonlocal reasoning_started
            if not reasoning_started:
                reasoning_started = True
                if self.on_reasoning_start:
                    self.on_reasoning_start()
            if self.on_reasoning_token:
                self.on_reasoning_token(token)

        self.task_list = plan_tasks(
            requirement,
            model=self.model,
            llm_client=self.llm_client,
            on_token=self.on_plan_token,
            on_reasoning_token=_on_plan_reasoning,
            on_reasoning_done=self.on_reasoning_done,
        )
        self.task_list.save(".funharness/tasks.json")
        self.progress_tracker.update(self.task_list)
        self._build_system_prompt()
        self.messages[0] = {"role": "system", "content": self._system_prompt}
        return self.task_list.summary()

    def _handle_done(self, arg):
        parts = arg.split(maxsplit=1)
        if self.task_list:
            task_id = parts[0]
            files = parts[1] if len(parts) > 1 else ""
            return self._complete_task(task_id, files)
        return "No task list loaded."

    def _handle_task(self, arg: str) -> str:
        parts = arg.split(maxsplit=2)
        if not parts:
            return (
                "Usage:\n"
                "  /task create <title>\n"
                "  /task get <id>\n"
                "  /task update <id> status=<status> owner=<name> notes=<text>"
            )
        action = parts[0].lower()
        if action == "create" and len(parts) >= 2:
            title = arg.split(maxsplit=1)[1]
            return self._create_task(title)
        if action == "get" and len(parts) >= 2:
            if not self.task_list:
                return "No task list loaded."
            task = self.task_list.get(parts[1])
            return json.dumps(task.to_dict(), ensure_ascii=False, indent=2) if task else f"Unknown task: {parts[1]}"
        if action == "update" and len(parts) >= 2:
            task_id = parts[1]
            rest = parts[2] if len(parts) > 2 else ""
            updates = _parse_key_values(rest)
            return self._update_task(
                task_id,
                status=updates.get("status", ""),
                owner=updates.get("owner", ""),
                notes=updates.get("notes", ""),
                artifacts=updates.get("artifacts", ""),
                error=updates.get("error", ""),
            )
        return f"Unknown /task action: {action}"

    def _complete_task(self, task_id: str, files: str) -> str:
        if self.task_list is None:
            return "(no task list loaded)"
        task = self.task_list.get(task_id)
        if not task:
            return f"Unknown task: {task_id}"
        artifacts = [f.strip() for f in files.split(",") if f.strip()]
        self.task_list.update(task_id, status=TaskStatus.DONE.value, artifacts=artifacts)
        self.progress_tracker.update(self.task_list)
        commit_msg = self.git_tracker.commit_task(task)
        self.task_list.save(".funharness/tasks.json")
        done, total = self.task_list.progress
        return f"Task {task_id} done. Progress: {done}/{total} ({self.task_list.progress_pct:.0f}%). {commit_msg}"

    def _fail_task(self, task_id: str, error: str) -> str:
        if self.task_list is None:
            return "(no task list loaded)"
        task = self.task_list.get(task_id)
        if not task:
            return f"Unknown task: {task_id}"
        task.fail(error)
        self.progress_tracker.update(self.task_list)
        self.task_list.save(".funharness/tasks.json")
        return f"Task {task_id} failed: {error}"

    def _create_task(self, title: str, description: str = "", depends_on: str = "",
                     owner: str = "", verify: str = "") -> str:
        if self.task_list is None:
            self.task_list = TaskList(project_name="FunHarness Tasks")
        task = Task(self.task_list.next_id(), title, description, verify, _parse_list(depends_on), owner)
        self.task_list.add(task)
        self.task_list.save(".funharness/tasks.json")
        self.progress_tracker.update(self.task_list)
        self._build_system_prompt()
        self.messages[0] = {"role": "system", "content": self._system_prompt}
        return f"Created task {task.task_id}: {task.title}"

    def _update_task(self, task_id: str, status: str = "", owner: str = "",
                     notes: str = "", artifacts: str = "", error: str = "") -> str:
        if self.task_list is None:
            return "(no task list loaded)"
        try:
            task = self.task_list.update(
                task_id, status=status, owner=owner, notes=notes,
                artifacts=_parse_list(artifacts), error=error,
            )
        except KeyError:
            return f"Unknown task: {task_id}"
        except ValueError:
            return f"Unknown task status: {status}"
        self.task_list.save(".funharness/tasks.json")
        self.progress_tracker.update(self.task_list)
        return f"Updated {task.task_id}: {task.status.value}"

    def _handle_team(self, arg: str) -> str:
        if not arg:
            return self.team.summary()
        parts = arg.split(maxsplit=3)
        action = parts[0].lower()
        if action == "create" and len(parts) >= 3:
            instructions = parts[3] if len(parts) > 3 else ""
            member = self.team.create(parts[1], parts[2], instructions)
            return f"Teammate ready: {member.name} [{member.role}]"
        if action == "rename" and len(parts) >= 3:
            try:
                member = self.team.rename(parts[1], parts[2])
            except KeyError:
                return f"Unknown teammate: {parts[1]}"
            except ValueError as exc:
                return f"Could not rename teammate: {exc}"
            return f"Renamed teammate to {member.name}"
        if action == "inbox" and len(parts) >= 2:
            items = self.team.peek_inbox(parts[1])
            return json.dumps(items, ensure_ascii=False, indent=2) if items else "(inbox empty)"
        if action == "send" and len(parts) >= 3:
            return self.team.send("lead", parts[1], parts[2])
        if action == "tasks":
            run = self.team.current_run()
            if not run:
                return "(no active team run)"
            return json.dumps(run.get("tasks", []), ensure_ascii=False, indent=2)
        if action == "task" and len(parts) >= 3:
            run = self.team.current_run()
            if not run:
                return "(no active team run)"
            try:
                updated = self.team.update_run_task(run["run_id"], parts[1], status=parts[2])
            except KeyError:
                return f"Unknown team task: {parts[1]}"
            except ValueError as exc:
                return str(exc)
            return f"Updated team task: {parts[1]}" if updated else "(no active team run)"
        if action == "start" and len(parts) >= 2:
            try:
                self.team.start_member(parts[1])
            except KeyError:
                return f"Unknown teammate: {parts[1]}"
            return f"Started teammate: {parts[1]}"
        if action == "cancel" and len(parts) >= 2:
            reason = " ".join(parts[2:]) if len(parts) > 2 else ""
            try:
                self.team.cancel_member(parts[1], reason=reason)
            except KeyError:
                return f"Unknown teammate: {parts[1]}"
            return f"Cancel requested for teammate: {parts[1]}"
        if action == "shutdown" and len(parts) >= 2:
            reason = " ".join(parts[2:]) if len(parts) > 2 else ""
            try:
                self.team.shutdown_member(parts[1], reason=reason)
            except KeyError:
                return f"Unknown teammate: {parts[1]}"
            return f"Shutdown requested for teammate: {parts[1]}"
        if action == "delete" and len(parts) >= 2:
            try:
                result = self.team.delete(parts[1])
            except KeyError:
                return f"Unknown teammate: {parts[1]}"
            return result
        return (
            "Usage: /team | /team create <name> <role> [instructions] | "
            "/team rename <name> <new_name> | /team delete <name> | /team inbox <name> | "
            "/team send <name> <message> | /team tasks | /team task <task_id> <status> | "
            "/team start <name> | /team cancel <name> [reason] | /team shutdown <name> [reason]"
        )

    def _handle_delegate(self, arg: str) -> str:
        parts = arg.split(maxsplit=1)
        if len(parts) < 2:
            return "Usage: /delegate <teammate> <task>"
        try:
            runtime_id = self.team.delegate(parts[0], parts[1])
        except KeyError:
            return f"Unknown teammate: {parts[0]}"
        return f"Delegated to {parts[0]}. Runtime task: {runtime_id}"

    def _handle_runtime(self, arg: str) -> str:
        if not arg:
            return self.runtime.summary()
        parts = arg.split(maxsplit=1)
        action = parts[0].lower()
        if action == "run" and len(parts) == 2:
            runtime_id = self.runtime.submit_command(parts[1], parts[1])
            return f"Runtime task started: {runtime_id}"
        if action == "output" and len(parts) == 2:
            return self.runtime.output(parts[1])
        if action == "status" and len(parts) == 2:
            task = self.runtime.get(parts[1])
            return json.dumps(task.to_dict(), ensure_ascii=False, indent=2) if task else f"Unknown runtime task: {parts[1]}"
        return "Usage: /bg | /bg run <command> | /bg status <id> | /bg output <id>"

    def _handle_schedule(self, arg: str) -> str:
        if not arg:
            return self.scheduler.summary()
        tokens = arg.split()
        action = tokens[0].lower()
        if action == "delete" and len(tokens) >= 2:
            return f"Deleted {tokens[1]}" if self.scheduler.delete(tokens[1]) else f"Unknown schedule: {tokens[1]}"
        if action == "run" and len(tokens) >= 2:
            record = self.scheduler.trigger(tokens[1])
            if record is None:
                return f"Unknown schedule: {tokens[1]}"
            runtime = f" Runtime task: {record.last_runtime_id}" if record.last_runtime_id else ""
            error = f" Error: {record.last_run_error}" if record.last_run_error else ""
            return f"Schedule started: {record.schedule_id}.{runtime}{error}"
        if action == "create" and len(tokens) >= 4:
            name = tokens[1]
            if tokens[2].lower() == "in" and len(tokens) >= 5:
                when = f"in {tokens[3]}"
                prompt = " ".join(tokens[4:])
            elif len(tokens) >= 8 and len(tokens[2:7]) == 5:
                when = " ".join(tokens[2:7])
                prompt = " ".join(tokens[7:])
            else:
                when = tokens[2]
                prompt = " ".join(tokens[3:])
            record = self.scheduler.create(name, when, prompt)
            return (
                f"Schedule created: {record.schedule_id} [{record.when}] {record.name}\n"
                "When it fires, it will run in the background. Use /schedule to see the "
                "runtime id and /bg output <runtime_id> to read the result."
            )
        return "Usage: /schedule | /schedule create <name> <when> <prompt> | /schedule run <id> | /schedule delete <id>"

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
        # Re-inject begin_dialogs for active persona
        self._inject_begin_dialogs_for_active_persona()
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
        attachment_context = self._attachment_context()
        message_content = user_input
        if attachment_context:
            message_content = f"{user_input}\n\n{attachment_context}"
        self.messages.append({"role": "user", "content": message_content})
        tools = registry.get_openai_schemas()
        turn_history_start = len(self.tool_calls_history)

        loop_span = self.tracer.start_span(SpanKind.AGENT_LOOP, "agent_loop",
                                           metadata={"input": user_input[:100]})

        for iteration in range(1, MAX_ITERATIONS + 1):
            self._raise_if_interrupted()
            self._drain_external_events(inject=True)
            # Middleware chain
            mw_context = {
                "messages": self.messages, "iteration": iteration,
                "tool_calls_history": self.tool_calls_history[turn_history_start:],
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
                stream = call_with_retry(
                    self.messages, tools, stream=True, model=self.model, llm_client=self.llm_client
                )
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
                self.messages = compact_conversation(
                    self.messages, model=self.model, llm_client=self.llm_client
                )
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

            stream = call_with_retry(
                self.messages, tools, stream=True, model=self.model, llm_client=self.llm_client
            )
            msg = process_stream_response(
                stream, on_token=self.on_token,
                on_reasoning_token=_on_reasoning_wrapper,
                on_reasoning_done=self.on_reasoning_done,
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

                result, hook_feedback, display = self._execute_tool(name, args_str)
                self._raise_if_interrupted()

                self._emit_tool_result(name, result, hook_feedback, display)

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

    def _emit_tool_result(self, name, result, hook_feedback, display):
        if not self.on_tool_result:
            return
        try:
            parameters = inspect.signature(self.on_tool_result).parameters.values()
        except (TypeError, ValueError):
            self.on_tool_result(name, result, hook_feedback, display)
            return

        accepts_varargs = any(p.kind == inspect.Parameter.VAR_POSITIONAL for p in parameters)
        positional_count = sum(
            1 for p in parameters
            if p.kind in {inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD}
        )
        if accepts_varargs or positional_count >= 4:
            self.on_tool_result(name, result, hook_feedback, display)
        else:
            self.on_tool_result(name, result, hook_feedback)

    def _execute_tool(self, tool_name, arguments_json):
        """Execute a tool with hooks and permission checks."""
        try:
            args = json.loads(arguments_json)
        except json.JSONDecodeError as e:
            return f"Argument parse error: {e}", "", None

        self._raise_if_interrupted()

        func = registry.get_function(tool_name)
        if not func:
            return f"Unknown tool: {tool_name}", "", None

        schema = registry.get_schema(tool_name)
        if schema:
            required = schema["function"]["parameters"].get("required", [])
            missing = [p for p in required if p not in args]
            if missing:
                return f"Missing required parameters: {', '.join(missing)}", "", None

        # PreToolUse hook
        pre_result = self.hook_registry.dispatch_pre_tool(tool_name, args)
        if pre_result.action == HookAction.DENY:
            return f"[HOOK DENIED] {pre_result.feedback}", "", None
        if pre_result.modified_args:
            args = pre_result.modified_args

        # Permission check
        allowed, reason = self.approval_flow.pre_tool_check(tool_name, args)
        if not allowed:
            return f"[DENIED] {reason}", "", None

        # Execute
        tool_start = time.time()
        tool_span = self.tracer.start_span(SpanKind.TOOL_CALL, tool_name)

        display = None
        if tool_name == "tool_run_command":
            result = self.approval_flow.sandbox.execute(
                args.get("command", ""),
                should_interrupt=self.is_interrupted,
            )
        else:
            try:
                self._raise_if_interrupted()
                token = _current_agent.set(self)
                try:
                    raw_result = func(**args)
                    if isinstance(raw_result, ToolResult):
                        result = raw_result.content
                        display = raw_result.display
                    else:
                        result = str(raw_result)
                finally:
                    _current_agent.reset(token)
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

        return result, hook_feedback, display
