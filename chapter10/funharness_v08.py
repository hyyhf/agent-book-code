"""
10.4 - FunHarness v0.8: 可观测性、调试与总成

在 v0.7 的基础上引入:
- Tracer 全链路追踪: 每次 LLM 调用、工具执行都记录 Span
- StructuredLogger: 替代裸 print, 可按级别过滤
- CostDashboard: 汇总面板, 运行结束后展示成本分析
- FailurePattern: 自动检测常见失败模式
- /trace, /logs, /dashboard, /failures 调试命令

这是 FunHarness 的最终版本, 整合了第 2 章到第 10 章的所有模块。

运行方式:
    uv run python chapter10/funharness_v08.py
    uv run python chapter10/funharness_v08.py --mode auto "build a calculator CLI"
"""
import json
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI, RateLimitError, APITimeoutError, APIConnectionError

# 路径设置: 复用前几章的模块
_dir = str(Path(__file__).resolve().parent)
if _dir not in sys.path:
    sys.path.insert(0, _dir)

for chapter in ["chapter04", "chapter05", "chapter06", "chapter07", "chapter08", "chapter09"]:
    _ch_dir = str(Path(__file__).resolve().parent.parent / chapter)
    if _ch_dir not in sys.path:
        sys.path.insert(0, _ch_dir)

from tool_registry import ToolRegistry
from core_tools import (
    read_file, write_file, replace_in_file,
    run_command, list_directory, grep_search,
)
from system_prompt_builder import build_system_prompt, IDENTITY_BLOCK
from context_discovery import build_context_block
from context_compaction import (
    CostTracker, estimate_tokens,
    truncate_tool_results, compact_conversation, should_compact,
)
from persistent_memory import (
    init_memory, read_memory, save_memory, search_memory,
)
from session_manager import Session, SessionManager
from skill_loader import (
    init_skills, list_available_skills, load_skill,
)
from permission_manager import (
    PermissionManager, PermissionMode, PathPolicy, CommandPolicy,
    classify_risk,
)
from approval_flow import ApprovalFlow, SandboxExecutor
from hooks import (
    HookRegistry, HookEvent, HookAction, HookResult,
    path_normalization_hook, large_file_guard_hook,
    lint_after_write_hook, completion_checklist_hook,
)
from middleware import (
    MiddlewareChain,
    LoopDetectionMiddleware,
    SelfVerificationMiddleware,
    EnvironmentAwareMiddleware,
)
from plugin_system import PluginLoader

from task_manager import (
    TaskList, Task, TaskStatus,
    ProgressTracker, GitTracker,
    plan_tasks, pick_next_task, format_task_for_agent,
)
from build_verify_fix import (
    TimeBudget, BVFLoop, run_verification,
)
from multi_agent import (
    SubAgent, Orchestrator, BackgroundTaskManager, BackgroundTask,
)

# v0.8 新增模块
from observability import (
    Tracer, TraceSpan, SpanKind,
    StructuredLogger, LogLevel,
    CostDashboard,
)
from evaluation import (
    FailurePattern, FailureType,
    BenchmarkRunner, BenchmarkCase,
    HarnessConfig, TuningExperiment,
)

load_dotenv(Path(__file__).resolve().parent.parent / ".env")
client = OpenAI()
MODEL = os.getenv("OPENAI_MODEL_NAME", "deepseek-chat")

MAX_ITERATIONS = 25


# =============================================================
#  工具注册: 复用所有工具
# =============================================================

registry = ToolRegistry()


# --- 文件操作工具 ---
@registry.tool(category="file")
def tool_read_file(path: str) -> str:
    """读取指定文件的完整文本内容并返回。如果文件不存在或无权限, 返回明确的错误信息。

    Args:
        path: 要读取的文件路径(支持相对路径和绝对路径)
    """
    return read_file(path)


@registry.tool(category="file")
def tool_write_file(path: str, content: str) -> str:
    """将内容写入指定文件。文件已存在则覆盖, 不存在则自动创建(含必要的父目录)。

    Args:
        path: 目标文件路径
        content: 要写入的完整文本内容
    """
    return write_file(path, content)


@registry.tool(category="file")
def tool_replace_in_file(path: str, old_text: str, new_text: str) -> str:
    """在文件中查找 old_text 并替换为 new_text。要求 old_text 在文件中精确存在。

    Args:
        path: 目标文件路径
        old_text: 要查找的原始文本(必须精确匹配)
        new_text: 替换后的新文本
    """
    return replace_in_file(path, old_text, new_text)


# --- 系统工具 ---
@registry.tool(category="system")
def tool_run_command(command: str) -> str:
    """在系统 Shell 中执行命令并返回输出。包含 stdout 和 stderr, 超时 30 秒自动终止。

    Args:
        command: 要执行的 Shell 命令字符串
    """
    return run_command(command)


# --- 搜索工具 ---
@registry.tool(category="search")
def tool_list_directory(path: str) -> str:
    """列出指定目录下的文件和子目录, 显示类型和大小信息。

    Args:
        path: 要列出内容的目录路径, 使用 '.' 表示当前目录
    """
    return list_directory(path)


@registry.tool(category="search")
def tool_grep_search(pattern: str, path: str) -> str:
    """在指定文件或目录中搜索匹配的文本行。支持正则表达式, 返回匹配行及行号。

    Args:
        pattern: 搜索模式(支持正则表达式)
        path: 要搜索的文件路径或目录路径
    """
    return grep_search(pattern, path)


# --- 记忆工具 ---
@registry.tool(category="memory")
def tool_read_memory() -> str:
    """读取所有持久化记忆内容。当需要回忆之前保存的项目知识或用户偏好时使用。"""
    return read_memory()


@registry.tool(category="memory")
def tool_save_memory(title: str, content: str) -> str:
    """保存一条新的持久化记忆。用于记录重要的项目知识、用户偏好或关键发现, 以便在未来的会话中使用。

    Args:
        title: 记忆标题, 简洁描述主题
        content: 记忆正文, 详细内容
    """
    return save_memory(title, content)


@registry.tool(category="memory")
def tool_search_memory(keyword: str) -> str:
    """按关键词搜索持久化记忆。在记忆标题和正文中查找匹配项。

    Args:
        keyword: 搜索关键词
    """
    return search_memory(keyword)


# --- 技能工具 ---
@registry.tool(category="knowledge")
def tool_list_skills() -> str:
    """列出所有可用的技能及其描述。当需要了解有哪些领域知识可用时使用。"""
    return list_available_skills()


@registry.tool(category="knowledge")
def tool_load_skill(query: str) -> str:
    """根据关键词或任务描述加载相关技能的知识内容。当需要特定领域的最佳实践或参考知识来完成任务时使用。

    Args:
        query: 关键词或任务描述
    """
    return load_skill(query)


# --- 任务管理工具 ---
_task_list: TaskList | None = None
_progress_tracker: ProgressTracker | None = None
_git_tracker: GitTracker | None = None
_bg_manager: BackgroundTaskManager | None = None


@registry.tool(category="task")
def tool_view_tasks() -> str:
    """查看当前任务清单和进度。当需要了解整体进度或确定下一步工作时使用。"""
    if _task_list is None:
        return "(no task list loaded)"
    return _task_list.summary()


@registry.tool(category="task")
def tool_next_task() -> str:
    """获取下一个待执行的任务。返回任务的详细描述和验证标准。"""
    if _task_list is None:
        return "(no task list loaded)"
    task = pick_next_task(_task_list)
    if task is None:
        return "All tasks completed or no executable task (check dependencies)."
    task.start()
    return format_task_for_agent(task)


@registry.tool(category="task")
def tool_complete_task(task_id: str, files: str) -> str:
    """标记一个任务为已完成, 并记录产出的文件。

    Args:
        task_id: 任务 ID, 如 T1, T2
        files: 产出的文件路径, 多个用逗号分隔
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
    return (
        f"Task {task_id} marked as done. "
        f"Progress: {done}/{total} ({_task_list.progress_pct:.0f}%). "
        f"{commit_msg}"
    )


@registry.tool(category="task")
def tool_fail_task(task_id: str, error: str) -> str:
    """标记一个任务为失败, 并记录原因。

    Args:
        task_id: 任务 ID
        error: 失败原因
    """
    if _task_list is None:
        return "(no task list loaded)"
    task = _task_list.get(task_id)
    if not task:
        return f"Unknown task: {task_id}"
    task.fail(error)
    if _progress_tracker:
        _progress_tracker.update(_task_list)
    return f"Task {task_id} marked as failed: {error}"


@registry.tool(category="task")
def tool_read_progress() -> str:
    """读取 PROGRESS.md 进度文件。在上下文压缩后恢复工作状态时特别有用。"""
    if _progress_tracker:
        return _progress_tracker.read()
    return "(no progress tracker)"


@registry.tool(category="task")
def tool_background_status() -> str:
    """查看所有后台任务的状态。"""
    if _bg_manager:
        return _bg_manager.list_tasks()
    return "(no background tasks)"


# =============================================================
#  v0.8 全局可观测性实例
# =============================================================

# 全局 Tracer 和 Logger, 在整个 Agent 运行期间共享
_tracer: Tracer | None = None
_logger: StructuredLogger | None = None
_dashboard: CostDashboard | None = None


def init_observability(
    log_level: LogLevel = LogLevel.INFO,
    json_logs: bool = False,
) -> tuple[Tracer, StructuredLogger, CostDashboard]:
    """初始化可观测性三件套。"""
    global _tracer, _logger, _dashboard
    _tracer = Tracer()
    _logger = StructuredLogger(
        name="FunHarness",
        level=log_level,
        json_output=json_logs,
    )
    _dashboard = CostDashboard()
    _logger.info(
        "Observability initialized",
        trace_id=_tracer.trace_id,
        log_level=log_level.value,
    )
    return _tracer, _logger, _dashboard


# =============================================================
#  初始化辅助函数
# =============================================================

def init_hooks() -> HookRegistry:
    hook_registry = HookRegistry()
    hook_registry.register(
        HookEvent.PRE_TOOL_USE,
        path_normalization_hook,
        name="path_normalizer",
        priority=10,
    )
    hook_registry.register(
        HookEvent.PRE_TOOL_USE,
        large_file_guard_hook,
        name="large_file_guard",
        priority=20,
        tool_matcher="tool_write_file",
    )
    hook_registry.register(
        HookEvent.POST_TOOL_USE,
        lint_after_write_hook,
        name="lint_reminder",
        tool_matcher="tool_write_file|tool_replace_in_file",
    )
    hook_registry.register(
        HookEvent.PRE_COMPLETION,
        completion_checklist_hook,
        name="completion_checklist",
    )
    return hook_registry


def init_middleware() -> MiddlewareChain:
    chain = MiddlewareChain()
    chain.add(LoopDetectionMiddleware(repeat_threshold=3))
    chain.add(SelfVerificationMiddleware(verify_interval=3))
    chain.add(EnvironmentAwareMiddleware(max_iterations_warning=20))
    return chain


def init_plugins() -> PluginLoader:
    loader = PluginLoader()
    manifests = loader.discover()
    if manifests:
        loaded = loader.load_all()
        if loaded and _logger:
            _logger.info(f"Plugins loaded: {', '.join(loaded)}")
    return loader


# =============================================================
#  System Prompt (v0.8)
# =============================================================

IDENTITY_V08 = """\
You are FunHarness v0.8, an AI-powered programming assistant with full
observability, task management, and multi-agent collaboration capabilities.

Core behaviors:
- Explain your plan before taking action.
- Verify the result after each operation.
- If an error occurs, analyze the cause and attempt to fix it.
- Provide a concise summary when the task is complete.
- When uncertain, ask the user for clarification instead of guessing.
- Prefer precise file edits over full-file rewrites.

Task management:
- Use tool_view_tasks to see the current task list and progress.
- Use tool_next_task to get the next pending task.
- After completing a task, use tool_complete_task to mark it done.
- Use tool_read_progress after context compaction to restore work state.

Security awareness:
- You operate under permission mode ({mode}).
- Some operations require user approval. Respect all denials.

Lifecycle awareness:
- Hooks run at key points: before/after tool use, before completion.
- Middleware monitors behavior: loop detection, self-verification.
- All operations are traced for observability.

Memory & Knowledge:
- Use save_memory to record important discoveries.
- Use load_skill to access domain-specific knowledge."""


def build_v08_system_prompt(mode: PermissionMode) -> str:
    """组装 v0.8 的 System Prompt。"""
    from system_prompt_builder import build_environment_block, build_tools_guide

    identity = IDENTITY_V08.format(mode=mode.value)
    sections = [
        identity,
        build_environment_block(),
        build_tools_guide(registry),
    ]

    mode_desc = {
        PermissionMode.AUTO: "All operations execute automatically.",
        PermissionMode.SUGGEST: "Read operations are automatic. Write/execute require approval.",
        PermissionMode.APPROVE: "All operations require explicit user approval.",
    }
    sections.append(
        f"# Current Permission Mode: {mode.value}\n{mode_desc[mode]}"
    )

    context_block = build_context_block()
    if context_block:
        sections.append(context_block)

    memory_text = read_memory()
    if memory_text and memory_text != "(no memories saved yet)":
        summary = memory_text[:2000]
        if len(memory_text) > 2000:
            summary += "\n...(use read_memory for full content)"
        sections.append(f"# Saved Memories\n{summary}")

    if _task_list and _task_list.tasks:
        sections.append(f"# Task Progress\n{_task_list.summary()}")

    return "\n\n".join(sections)


# =============================================================
#  工具执行(v0.8: 带 Trace + 钩子 + 权限)
# =============================================================

def execute_tool_with_hooks(
    tool_name: str,
    arguments_json: str,
    approval_flow: ApprovalFlow,
    hook_registry: HookRegistry,
) -> tuple[str, str]:
    """解析参数 -> 钩子前置检查 -> 权限检查 -> 执行(带 Trace) -> 钩子后置反馈。"""
    try:
        args = json.loads(arguments_json)
    except json.JSONDecodeError as e:
        return f"参数解析失败: {e}", ""

    func = registry.get_function(tool_name)
    if not func:
        return f"未知工具: {tool_name}", ""

    schema = registry.get_schema(tool_name)
    if schema:
        required = schema["function"]["parameters"].get("required", [])
        missing = [p for p in required if p not in args]
        if missing:
            return f"缺少必填参数: {', '.join(missing)}", ""

    # PreToolUse 钩子 (带 Trace)
    if _tracer:
        with _tracer.span(SpanKind.HOOK, f"pre_{tool_name}") as hook_span:
            pre_result = hook_registry.dispatch_pre_tool(tool_name, args)
            hook_span.metadata["action"] = pre_result.action.value
    else:
        pre_result = hook_registry.dispatch_pre_tool(tool_name, args)

    if _logger and pre_result.action != HookAction.ALLOW:
        _logger.log_hook("pre_tool", tool_name, pre_result.action.value)

    if pre_result.action == HookAction.DENY:
        return f"[HOOK DENIED] {pre_result.feedback}", ""
    if pre_result.modified_args:
        args = pre_result.modified_args

    # 权限检查
    allowed, reason = approval_flow.pre_tool_check(tool_name, args)
    if not allowed:
        if _logger:
            _logger.warn(f"Permission denied for {tool_name}", reason=reason)
        return f"[DENIED] {reason}", ""

    # 执行工具 (带 Trace)
    tool_start = time.time()
    if _tracer:
        tool_span = _tracer.start_span(
            SpanKind.TOOL_CALL, tool_name,
            metadata={"tool_name": tool_name},
        )
        tool_span.input_summary = json.dumps(args, ensure_ascii=False)[:200]

    if tool_name == "tool_run_command":
        result = approval_flow.sandbox.execute(args.get("command", ""))
    else:
        try:
            result = str(func(**args))
        except Exception as e:
            result = f"工具执行失败 ({tool_name}): {e}"

    tool_duration = (time.time() - tool_start) * 1000
    success = "失败" not in result and "Error" not in result

    if _tracer:
        tool_span.output_summary = result[:200]
        _tracer.finish_span(
            tool_span,
            status="ok" if success else "error",
            error="" if success else result[:100],
        )

    if _logger:
        _logger.log_tool_call(tool_name, tool_duration, success, len(result))

    # PostToolUse 钩子
    post_result = hook_registry.dispatch_post_tool(tool_name, args, result)
    hook_feedback = post_result.feedback

    if pre_result.feedback:
        hook_feedback = (
            pre_result.feedback + "\n" + hook_feedback
            if hook_feedback else pre_result.feedback
        )

    return result, hook_feedback


# =============================================================
#  Agent Loop (v0.8)
# =============================================================

def call_with_retry(messages, tools, stream=False, max_retries=3):
    for attempt in range(max_retries):
        try:
            return client.chat.completions.create(
                model=MODEL,
                messages=messages,
                tools=tools,
                stream=stream,
            )
        except (RateLimitError, APITimeoutError, APIConnectionError) as e:
            if attempt == max_retries - 1:
                raise
            wait = 2 ** attempt
            if _logger:
                _logger.warn(f"API retry in {wait}s", attempt=attempt + 1, error=str(e))
            time.sleep(wait)


def process_stream_response(stream, cost_tracker: CostTracker):
    content_parts = []
    tool_calls_data = {}

    for chunk in stream:
        if hasattr(chunk, "usage") and chunk.usage:
            cost_tracker.update(chunk.usage)

        delta = chunk.choices[0].delta

        if delta.content:
            print(delta.content, end="", flush=True)
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

    if tool_calls_data:
        tc_list = []
        for idx in sorted(tool_calls_data):
            d = tool_calls_data[idx]
            tc_list.append({
                "id": d["id"],
                "type": "function",
                "function": {"name": d["name"], "arguments": d["arguments"]},
            })
        return {"role": "assistant", "content": content, "tool_calls": tc_list}

    return {"role": "assistant", "content": content}


def agent_loop(
    user_input,
    messages=None,
    cost_tracker=None,
    approval_flow=None,
    hook_registry=None,
    middleware_chain=None,
    mode=PermissionMode.SUGGEST,
    time_budget=None,
):
    """FunHarness v0.8 的核心 Agent Loop。

    相比 v0.7 的增强:
    1. 全链路 Trace: 每次 LLM 调用和工具执行都记录 Span
    2. 结构化日志: 关键操作都有日志记录
    3. 成本面板数据收集: LLM 调用的 Token 消耗自动记录到 Dashboard
    4. 失败模式检测: 运行结束后自动分析工具调用历史
    """
    if cost_tracker is None:
        cost_tracker = CostTracker()
    if approval_flow is None:
        pm = PermissionManager(mode=mode)
        approval_flow = ApprovalFlow(pm)
    if hook_registry is None:
        hook_registry = init_hooks()
    if middleware_chain is None:
        middleware_chain = init_middleware()
    if time_budget is None:
        time_budget = TimeBudget(total_seconds=600, per_task_seconds=120)

    if messages is None:
        system_prompt = build_v08_system_prompt(mode)
        messages = [{"role": "system", "content": system_prompt}]

    messages.append({"role": "user", "content": user_input})

    tools = registry.get_openai_schemas()
    tool_calls_history = []

    # v0.8: 开始 Agent Loop 的 Trace Span
    loop_span = None
    if _tracer:
        loop_span = _tracer.start_span(
            SpanKind.AGENT_LOOP, "agent_loop",
            metadata={"input": user_input[:100]},
        )

    for iteration in range(1, MAX_ITERATIONS + 1):
        # 时间预算检查
        budget_warning = time_budget.should_warn()
        if budget_warning and (time_budget.is_expired() or time_budget.is_api_exhausted()):
            if _logger:
                _logger.warn("Budget exhausted", warning=budget_warning)
            messages.append({
                "role": "user",
                "content": (
                    f"[SYSTEM] {budget_warning} "
                    f"Summarize what you have done and stop."
                ),
            })
            stream = call_with_retry(messages, tools, stream=True)
            msg = process_stream_response(stream, cost_tracker)
            messages.append(msg)
            print()
            break

        # 中间件链
        mw_context = {
            "messages": messages,
            "iteration": iteration,
            "tool_calls_history": tool_calls_history,
            "should_stop": False,
            "injections": [],
        }

        if _tracer:
            with _tracer.span(SpanKind.MIDDLEWARE, "middleware_chain"):
                mw_context = middleware_chain.run(mw_context)
        else:
            mw_context = middleware_chain.run(mw_context)

        if mw_context["injections"]:
            injection_text = "\n".join(mw_context["injections"])
            if budget_warning:
                injection_text += f"\n[TIME BUDGET] {budget_warning}"
            messages.append({
                "role": "user",
                "content": f"[SYSTEM MIDDLEWARE]\n{injection_text}",
            })
        elif budget_warning:
            messages.append({
                "role": "user",
                "content": f"[SYSTEM] [TIME BUDGET] {budget_warning}",
            })

        if mw_context["should_stop"]:
            if _logger:
                _logger.warn("Middleware force stop")
            messages.append({
                "role": "user",
                "content": (
                    "[SYSTEM] Middleware detected critical issues. "
                    "Summarize what you attempted and stop."
                ),
            })
            stream = call_with_retry(messages, tools, stream=True)
            msg = process_stream_response(stream, cost_tracker)
            messages.append(msg)
            print()
            break

        if should_compact(messages):
            if _logger:
                _logger.info("Compacting context", messages_before=len(messages))
            if _tracer:
                with _tracer.span(SpanKind.COMPACTION, "compact") as cs:
                    before = len(messages)
                    messages = compact_conversation(messages)
                    cs.metadata["before"] = before
                    cs.metadata["after"] = len(messages)
            else:
                before = len(messages)
                messages = compact_conversation(messages)

            if _logger:
                _logger.log_compaction(before, len(messages), 0)
            print(f"\n  [compact] {before} -> {len(messages)} messages", flush=True)
            if _progress_tracker:
                messages.append({
                    "role": "user",
                    "content": (
                        "[SYSTEM] Context was compacted. Use tool_read_progress "
                        "to restore your understanding of current work state."
                    ),
                })

        # LLM 调用 (带 Trace)
        llm_start = time.time()
        if _tracer:
            llm_span = _tracer.start_span(
                SpanKind.LLM_CALL, MODEL,
                metadata={"category": "agent_loop", "iteration": iteration},
            )

        stream = call_with_retry(messages, tools, stream=True)
        msg = process_stream_response(stream, cost_tracker)
        time_budget.record_api_call()
        messages.append(msg)

        llm_duration = (time.time() - llm_start) * 1000

        # 记录 LLM Trace
        if _tracer:
            input_tokens = cost_tracker.total_input_tokens
            output_tokens = cost_tracker.total_output_tokens
            llm_span.token_usage = {
                "input": getattr(cost_tracker, '_last_input', 0),
                "output": getattr(cost_tracker, '_last_output', 0),
            }
            _tracer.finish_span(llm_span)

        if _logger:
            _logger.log_llm_call(
                MODEL,
                cost_tracker.total_input_tokens,
                cost_tracker.total_output_tokens,
                llm_duration,
                stream=True,
            )

        # 记录到成本面板
        if _dashboard:
            _dashboard.record(
                "agent_loop",
                cost_tracker.total_input_tokens,
                cost_tracker.total_output_tokens,
                MODEL,
                llm_duration,
            )

        if "tool_calls" not in msg or not msg["tool_calls"]:
            # PreCompletion 钩子
            assistant_text = msg.get("content", "") or ""
            pre_completion = hook_registry.dispatch_pre_completion(
                assistant_text, messages
            )
            if pre_completion.action == HookAction.DENY:
                messages.append({
                    "role": "user",
                    "content": (
                        f"[HOOK FEEDBACK] {pre_completion.feedback}\n"
                        f"Please revise your response."
                    ),
                })
                continue

            print()
            break

        print()

        for tc in msg["tool_calls"]:
            name = tc["function"]["name"]
            args_str = tc["function"]["arguments"]

            try:
                args_preview = json.loads(args_str)
                if "content" in args_preview:
                    c = args_preview["content"]
                    args_preview["content"] = (
                        c[:40] + "..." if len(c) > 40 else c
                    )
                preview = json.dumps(args_preview, ensure_ascii=False)
            except Exception:
                preview = args_str[:60]

            risk = classify_risk(name)
            risk_badge = {"read": "R", "write": "W", "execute": "X"}.get(risk, "?")
            print(f"  [{risk_badge}][{name}] {preview}")

            result, hook_feedback = execute_tool_with_hooks(
                name, args_str, approval_flow, hook_registry
            )

            display = (
                result if len(result) <= 200 else result[:200] + "...(truncated)"
            )
            print(f"  -> {display}")

            if hook_feedback:
                print(f"  [hook] {hook_feedback}")

            try:
                parsed_args = json.loads(args_str)
            except Exception:
                parsed_args = {}
            tool_calls_history.append({
                "tool": name,
                "args": parsed_args,
                "result": result[:500],
            })

            tool_content = result
            if hook_feedback:
                tool_content += f"\n\n[Hook Feedback] {hook_feedback}"

            messages.append({
                "role": "tool",
                "tool_call_id": tc["id"],
                "content": tool_content,
            })

        messages = truncate_tool_results(messages)

    else:
        if _logger:
            _logger.warn("Max iterations reached", max=MAX_ITERATIONS)
        print("\n[FunHarness] Max iterations reached.")

    # v0.8: 结束 Agent Loop Span
    if _tracer and loop_span:
        _tracer.finish_span(loop_span)

    # v0.8: 自动进行失败模式分析
    if tool_calls_history:
        findings = FailurePattern.analyze(messages, tool_calls_history)
        if findings and _logger:
            _logger.warn(
                f"Failure patterns detected: {len(findings)}",
                types=[f.get("type", "").value if isinstance(f.get("type"), FailureType) else str(f.get("type", "")) for f in findings],
            )

    return messages


# =============================================================
#  交互式 CLI (v0.8)
# =============================================================

def main():
    """FunHarness v0.8 交互式命令行。

    v0.8 新增命令:
    - /trace    查看当前 Trace 的时间线
    - /logs     查看最近日志
    - /dashboard 查看成本分析面板
    - /failures 分析失败模式
    - /export   导出可观测性数据
    """
    global _task_list, _progress_tracker, _git_tracker, _bg_manager

    # 解析命令行参数
    mode = PermissionMode.SUGGEST
    for i, arg in enumerate(sys.argv):
        if arg == "--mode" and i + 1 < len(sys.argv):
            mode_str = sys.argv[i + 1].lower()
            mode = {
                "auto": PermissionMode.AUTO,
                "suggest": PermissionMode.SUGGEST,
                "approve": PermissionMode.APPROVE,
            }.get(mode_str, PermissionMode.SUGGEST)

    # 初始化所有子系统
    init_memory()
    init_skills()

    # v0.8: 初始化可观测性
    tracer, logger, dashboard = init_observability(log_level=LogLevel.INFO)

    pm = PermissionManager(mode=mode)
    approval_flow = ApprovalFlow(pm)
    session_mgr = SessionManager()
    cost_tracker = CostTracker()
    time_budget = TimeBudget(total_seconds=600, per_task_seconds=120)

    hook_registry = init_hooks()
    middleware_chain = init_middleware()
    plugin_loader = init_plugins()

    _progress_tracker = ProgressTracker(".")
    _git_tracker = GitTracker(".")
    _bg_manager = BackgroundTaskManager(max_workers=2)

    # 尝试加载已有的任务清单
    tasks_path = Path(".funharness/tasks.json")
    if tasks_path.exists():
        _task_list = TaskList.load(tasks_path)
        logger.info(f"Loaded {len(_task_list.tasks)} tasks from {tasks_path}")

    # 注册插件钩子
    for hook_def in plugin_loader.get_all_hooks():
        hook_registry.register(
            event=hook_def["event"],
            handler=hook_def["handler"],
            name=hook_def.get("name", "plugin_hook"),
            priority=hook_def.get("priority", 200),
        )
    plugin_commands = plugin_loader.get_all_commands()

    tools = registry.get_openai_schemas()
    tool_names = [t["function"]["name"] for t in tools]

    print("=" * 55)
    print("  FunHarness v0.8 (with Observability)")
    print(f"  Mode: {mode.value}")
    print(f"  Tools: {len(tool_names)}")
    print(f"  Trace: {tracer.trace_id}")
    print("  Commands: quit, clear, /cost, /context,")
    print("            /save, /load, /branch, /memory,")
    print("            /skills, /mode, /perms,")
    print("            /hooks, /middleware, /plugins,")
    print("            /plan, /tasks, /next, /done, /progress,")
    print("            /dual, /bg,")
    print("            /trace, /logs, /dashboard, /failures, /export")
    if plugin_commands:
        print(f"  Plugin commands: {', '.join('/' + c for c in plugin_commands)}")
    print("=" * 55)

    system_prompt = build_v08_system_prompt(mode)
    messages = [{"role": "system", "content": system_prompt}]

    prompt_tokens = estimate_tokens(messages)
    print(f"  [System prompt: ~{prompt_tokens} tokens | mode: {mode.value}]")

    current_session = Session()
    current_session.messages = messages
    tool_calls_history = []

    while True:
        try:
            user_input = input("\n> ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nBye!")
            break

        if not user_input:
            continue

        if user_input.lower() == "quit":
            current_session.messages = messages
            session_mgr.save(current_session)
            print(f"  [session auto-saved: {current_session.id}]")
            if _bg_manager:
                _bg_manager.shutdown()
            # v0.8: 退出时显示成本面板
            if dashboard and dashboard._records:
                print(dashboard.report())
            print("Bye!")
            break

        if user_input.lower() == "clear":
            current_session.messages = messages
            session_mgr.save(current_session)
            current_session = Session()
            messages = [{"role": "system", "content": system_prompt}]
            current_session.messages = messages
            tool_calls_history.clear()
            print("[conversation cleared, previous session saved]")
            continue

        # ----- v0.8 新增: 可观测性命令 -----
        if user_input.lower() == "/trace":
            print(tracer.timeline())
            print()
            print(tracer.summary())
            continue

        if user_input.lower() == "/logs":
            print(logger.tail(15))
            continue

        if user_input.lower() == "/dashboard":
            if dashboard._records:
                print(dashboard.report())
            else:
                print("  (no data yet)")
            continue

        if user_input.lower() == "/failures":
            findings = FailurePattern.analyze(messages, tool_calls_history)
            print(FailurePattern.report(findings))
            continue

        if user_input.lower() == "/export":
            export_dir = Path(".funharness/traces")
            export_dir.mkdir(parents=True, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            tracer.save(export_dir / f"trace_{ts}.json")
            logger.export(export_dir / f"logs_{ts}.json")
            dashboard.export(export_dir / f"cost_{ts}.json")
            print(f"  Exported to {export_dir}/")
            continue

        # ----- 前版本的所有命令 -----
        if user_input.lower() == "/cost":
            print(f"  {cost_tracker.summary()}")
            print(f"  {time_budget.summary()}")
            continue

        if user_input.lower() == "/context":
            msg_tokens = estimate_tokens(messages)
            print(f"  System prompt: {len(system_prompt)} chars")
            print(f"  Total context: ~{msg_tokens} tokens")
            print(f"  Messages: {len(messages)}")
            continue

        if user_input.lower() == "/save":
            current_session.messages = messages
            print(session_mgr.save(current_session))
            continue

        if user_input.lower() == "/load":
            sessions = session_mgr.list_sessions()
            if not sessions:
                print("  No saved sessions")
                continue
            print("  Saved sessions:")
            for i, s in enumerate(sessions):
                print(f"    {i+1}. [{s['id']}] {s['title']} ({s['message_count']} msgs)")
            try:
                choice = input("  Enter number to load (or press Enter to cancel): ").strip()
                if choice and choice.isdigit():
                    idx = int(choice) - 1
                    if 0 <= idx < len(sessions):
                        loaded = session_mgr.load(sessions[idx]["id"])
                        if loaded:
                            current_session = loaded
                            messages = loaded.messages
                            print(f"  Loaded session: {loaded.title}")
                    else:
                        print("  Invalid choice")
            except (KeyboardInterrupt, EOFError):
                pass
            continue

        if user_input.lower() == "/branch":
            current_session.messages = messages
            session_mgr.save(current_session)
            branch = session_mgr.branch(current_session.id)
            if branch:
                current_session = branch
                messages = branch.messages
                print(f"  Branch created: {branch.id}")
            continue

        if user_input.lower() == "/memory":
            mem = read_memory()
            print(f"  {mem[:500]}" + ("..." if len(mem) > 500 else ""))
            continue

        if user_input.lower() == "/skills":
            print(f"  {list_available_skills()}")
            continue

        if user_input.lower().startswith("/mode"):
            parts = user_input.split()
            if len(parts) == 2:
                new_mode_str = parts[1].lower()
                new_mode = {
                    "auto": PermissionMode.AUTO,
                    "suggest": PermissionMode.SUGGEST,
                    "approve": PermissionMode.APPROVE,
                }.get(new_mode_str)
                if new_mode:
                    mode = new_mode
                    pm.mode = mode
                    system_prompt = build_v08_system_prompt(mode)
                    messages[0] = {"role": "system", "content": system_prompt}
                    print(f"  Mode switched to: {mode.value}")
                else:
                    print(f"  Unknown mode: {new_mode_str}")
            else:
                print(f"  Current mode: {mode.value}")
            continue

        if user_input.lower() == "/perms":
            print(f"  Mode: {mode.value}")
            print(f"  Allowed dirs: {[str(d) for d in pm.path_policy.allowed]}")
            print(f"  Denied dirs: {[str(d) for d in pm.path_policy.denied]}")
            print(f"  Command whitelist: {len(pm.command_policy.whitelist)} entries")
            print(f"  Command blacklist: {len(pm.command_policy.blacklist)} entries")
            continue

        if user_input.lower() == "/hooks":
            print(hook_registry.list_hooks())
            continue

        if user_input.lower() == "/middleware":
            print(middleware_chain.list_middlewares())
            continue

        if user_input.lower() == "/plugins":
            print(plugin_loader.list_plugins())
            continue

        if user_input.lower().startswith("/plan "):
            requirement = user_input[6:].strip()
            if requirement:
                print(f"  Planning tasks for: {requirement[:60]}...")
                _task_list = plan_tasks(requirement)
                print(f"  {_task_list.summary()}")
                _task_list.save(".funharness/tasks.json")
                _progress_tracker.update(_task_list)
                print("  Tasks saved to .funharness/tasks.json")
                system_prompt = build_v08_system_prompt(mode)
                messages[0] = {"role": "system", "content": system_prompt}
            else:
                print("  Usage: /plan <requirement description>")
            continue

        if user_input.lower() == "/tasks":
            if _task_list:
                print(f"  {_task_list.summary()}")
            else:
                print("  No task list. Use /plan to create one.")
            continue

        if user_input.lower() == "/next":
            if _task_list:
                task = pick_next_task(_task_list)
                if task:
                    print(f"  {format_task_for_agent(task)}")
                else:
                    print("  No executable tasks remaining.")
            else:
                print("  No task list. Use /plan to create one.")
            continue

        if user_input.lower().startswith("/done "):
            parts = user_input.split(maxsplit=2)
            if len(parts) >= 2 and _task_list:
                task_id = parts[1]
                files = parts[2] if len(parts) > 2 else ""
                result = tool_complete_task(task_id, files)
                print(f"  {result}")
            else:
                print("  Usage: /done <task_id> [file1,file2,...]")
            continue

        if user_input.lower() == "/progress":
            if _progress_tracker:
                print(_progress_tracker.read())
            else:
                print("  No progress tracker.")
            continue

        if user_input.lower() == "/bg":
            if _bg_manager:
                print(f"  {_bg_manager.list_tasks()}")
            else:
                print("  (no background task manager)")
            continue

        # 检查插件命令
        if user_input.startswith("/"):
            cmd_parts = user_input[1:].split(maxsplit=1)
            cmd_name = cmd_parts[0].lower()
            cmd_args = cmd_parts[1] if len(cmd_parts) > 1 else ""
            if cmd_name in plugin_commands:
                plugin_ctx = {"tool_calls_history": tool_calls_history}
                result = plugin_commands[cmd_name](cmd_args, plugin_ctx)
                print(f"  {result}")
                continue

        print()
        messages = agent_loop(
            user_input, messages, cost_tracker,
            approval_flow, hook_registry, middleware_chain, mode,
            time_budget,
        )
        current_session.messages = messages
        tokens = estimate_tokens(messages)
        print(
            f"  [messages: {len(messages)} | ~{tokens} tokens "
            f"| {cost_tracker.summary()} | mode: {mode.value}]"
        )


if __name__ == "__main__":
    from datetime import datetime

    # 单次任务模式
    if len(sys.argv) > 1 and sys.argv[1] not in ("--mode", "--plan", "--dual"):
        init_memory()
        init_skills()
        init_observability()
        task = " ".join(sys.argv[1:])
        print(f"Task: {task}\n")
        agent_loop(task)
        if _dashboard and _dashboard._records:
            print(_dashboard.report())
    elif len(sys.argv) > 3 and sys.argv[1] == "--mode":
        init_memory()
        init_skills()
        init_observability()
        mode_str = sys.argv[2].lower()
        mode = {
            "auto": PermissionMode.AUTO,
            "suggest": PermissionMode.SUGGEST,
            "approve": PermissionMode.APPROVE,
        }.get(mode_str, PermissionMode.SUGGEST)
        task = " ".join(sys.argv[3:])
        print(f"Task: {task} (mode: {mode.value})\n")
        agent_loop(task, mode=mode)
        if _dashboard and _dashboard._records:
            print(_dashboard.report())
    else:
        main()
