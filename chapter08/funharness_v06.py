"""
8.4 - FunHarness v0.6: 钩子、中间件与生命周期控制

在 v0.5 的基础上引入:
- 钩子注册与调度系统: PreToolUse / PostToolUse / PreCompletion
- 中间件链: 循环检测 / 自验证 / 环境感知
- 插件加载器: 从 .funharness/plugins 加载第三方扩展

运行方式:
    uv run python chapter08/funharness_v06.py
    uv run python chapter08/funharness_v06.py --mode auto "list current directory"
    uv run python chapter08/funharness_v06.py --mode suggest "read README.md"
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

_ch04_dir = str(Path(__file__).resolve().parent.parent / "chapter04")
if _ch04_dir not in sys.path:
    sys.path.insert(0, _ch04_dir)

_ch05_dir = str(Path(__file__).resolve().parent.parent / "chapter05")
if _ch05_dir not in sys.path:
    sys.path.insert(0, _ch05_dir)

_ch06_dir = str(Path(__file__).resolve().parent.parent / "chapter06")
if _ch06_dir not in sys.path:
    sys.path.insert(0, _ch06_dir)

_ch07_dir = str(Path(__file__).resolve().parent.parent / "chapter07")
if _ch07_dir not in sys.path:
    sys.path.insert(0, _ch07_dir)

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
from plugin_system import PluginLoader, TimestampPlugin, StatsPlugin

load_dotenv(Path(__file__).resolve().parent.parent / ".env")
client = OpenAI()
MODEL = os.getenv("OPENAI_MODEL_NAME", "deepseek-v4-flash")

MAX_ITERATIONS = 20

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


# =============================================================
#  v0.6 初始化: 钩子 + 中间件 + 插件
# =============================================================

def init_hooks() -> HookRegistry:
    """初始化并注册所有内置钩子。"""
    hook_registry = HookRegistry()

    # PreToolUse 钩子
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

    # PostToolUse 钩子
    hook_registry.register(
        HookEvent.POST_TOOL_USE,
        lint_after_write_hook,
        name="lint_reminder",
        tool_matcher="tool_write_file|tool_replace_in_file",
    )

    # PreCompletion 钩子
    hook_registry.register(
        HookEvent.PRE_COMPLETION,
        completion_checklist_hook,
        name="completion_checklist",
    )

    return hook_registry


def init_middleware() -> MiddlewareChain:
    """初始化中间件链。"""
    chain = MiddlewareChain()
    chain.add(LoopDetectionMiddleware(repeat_threshold=3))
    chain.add(SelfVerificationMiddleware(verify_interval=3))
    chain.add(EnvironmentAwareMiddleware(max_iterations_warning=15))
    return chain


def init_plugins() -> PluginLoader:
    """初始化插件加载器, 发现并加载所有可用插件。"""
    loader = PluginLoader()
    manifests = loader.discover()
    if manifests:
        loaded = loader.load_all()
        if loaded:
            print(f"  [plugins] Loaded: {', '.join(loaded)}")
    return loader


# =============================================================
#  System Prompt (v0.6)
# =============================================================

IDENTITY_V06 = """\
You are FunHarness v0.6, an AI-powered programming assistant with lifecycle control.

Your core behaviors:
- Explain your plan before taking action.
- Verify the result after each operation.
- If an error occurs, analyze the cause and attempt to fix it.
- Provide a concise summary when the task is complete.
- When uncertain, ask the user for clarification instead of guessing.
- Prefer precise file edits over full-file rewrites.

Security awareness:
- You operate under a permission mode ({mode}) that governs what you can do.
- Some operations require user approval before execution. Respect all denials.
- Never attempt to bypass security restrictions or access protected paths.

Lifecycle awareness:
- Hooks run automatically at key points: before/after tool use, before completion.
- If a hook blocks an action, you will receive feedback explaining why.
- Middleware monitors your behavior: loop detection, self-verification, env changes.
- If middleware injects a warning, adjust your strategy accordingly.

Memory & Knowledge:
- Use save_memory to record important discoveries for future sessions.
- Use read_memory or search_memory to recall previously saved knowledge.
- Use load_skill to access domain-specific knowledge when needed."""


def build_v06_system_prompt(mode: PermissionMode) -> str:
    """组装 v0.6 的 System Prompt。"""
    from system_prompt_builder import build_environment_block, build_tools_guide

    identity = IDENTITY_V06.format(mode=mode.value)

    sections = [
        identity,
        build_environment_block(),
        build_tools_guide(registry),
    ]

    # 权限说明
    mode_desc = {
        PermissionMode.AUTO: "All operations execute automatically. Dangerous commands are still blocked.",
        PermissionMode.SUGGEST: "Read operations are automatic. Write and execute operations require user approval.",
        PermissionMode.APPROVE: "All operations require explicit user approval before execution.",
    }
    sections.append(
        f"# Current Permission Mode: {mode.value}\n{mode_desc[mode]}"
    )

    # 项目上下文
    context_block = build_context_block()
    if context_block:
        sections.append(context_block)

    # 记忆摘要
    memory_text = read_memory()
    if memory_text and memory_text != "(no memories saved yet)":
        summary = memory_text[:2000]
        if len(memory_text) > 2000:
            summary += "\n...(use read_memory for full content)"
        sections.append(f"# Saved Memories\n{summary}")

    return "\n\n".join(sections)


# =============================================================
#  工具执行(v0.6: 带钩子 + 权限检查)
# =============================================================

def execute_tool_with_hooks(
    tool_name: str,
    arguments_json: str,
    approval_flow: ApprovalFlow,
    hook_registry: HookRegistry,
) -> tuple[str, str]:
    """解析参数 -> 钩子前置检查 -> 权限检查 -> 执行 -> 钩子后置反馈。

    返回: (工具执行结果, 钩子反馈)
    """
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

    # v0.6 新增: PreToolUse 钩子
    pre_result = hook_registry.dispatch_pre_tool(tool_name, args)
    if pre_result.action == HookAction.DENY:
        return f"[HOOK DENIED] {pre_result.feedback}", ""
    if pre_result.modified_args:
        args = pre_result.modified_args

    # 权限检查(来自 v0.5)
    allowed, reason = approval_flow.pre_tool_check(tool_name, args)
    if not allowed:
        return f"[DENIED] {reason}", ""

    # 执行工具
    if tool_name == "tool_run_command":
        result = approval_flow.sandbox.execute(args.get("command", ""))
    else:
        try:
            result = str(func(**args))
        except Exception as e:
            result = f"工具执行失败 ({tool_name}): {e}"

    # v0.6 新增: PostToolUse 钩子
    post_result = hook_registry.dispatch_post_tool(tool_name, args, result)
    hook_feedback = post_result.feedback

    # 合并 PreToolUse 的反馈
    if pre_result.feedback:
        hook_feedback = (
            pre_result.feedback + "\n" + hook_feedback
            if hook_feedback else pre_result.feedback
        )

    return result, hook_feedback


# =============================================================
#  Agent Loop (v0.6)
# =============================================================

def call_with_retry(messages, tools, stream=False, max_retries=3):
    """带指数退避的 API 调用。"""
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
            print(f"\n  [retry] {wait}s...", flush=True)
            time.sleep(wait)


def process_stream_response(stream, cost_tracker: CostTracker):
    """处理流式响应, 收集完整的 assistant 消息。"""
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
):
    """FunHarness v0.6 的核心 Agent Loop。

    相比 v0.5 的增强:
    1. 工具调用前后分别执行 PreToolUse / PostToolUse 钩子
    2. 每次迭代前运行中间件链(循环检测、自验证、环境感知)
    3. 模型生成最终回复前执行 PreCompletion 钩子
    4. 钩子和中间件的反馈注入到模型的上下文中
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

    if messages is None:
        system_prompt = build_v06_system_prompt(mode)
        messages = [{"role": "system", "content": system_prompt}]

    messages.append({"role": "user", "content": user_input})

    tools = registry.get_openai_schemas()

    # 工具调用历史(供中间件使用)
    tool_calls_history = []

    for iteration in range(1, MAX_ITERATIONS + 1):
        # v0.6 新增: 运行中间件链
        mw_context = {
            "messages": messages,
            "iteration": iteration,
            "tool_calls_history": tool_calls_history,
            "should_stop": False,
            "injections": [],
        }
        mw_context = middleware_chain.run(mw_context)

        # 中间件注入的指令添加到消息中
        if mw_context["injections"]:
            injection_text = "\n".join(mw_context["injections"])
            messages.append({
                "role": "user",
                "content": f"[SYSTEM MIDDLEWARE]\n{injection_text}",
            })

        if mw_context["should_stop"]:
            print("\n  [middleware] Force stopping due to detected issues.")
            messages.append({
                "role": "user",
                "content": (
                    "[SYSTEM] The middleware has detected critical issues "
                    "(e.g., repeated failures). Summarize what you attempted "
                    "and what went wrong, then stop."
                ),
            })
            # 让模型做最后一次回复
            stream = call_with_retry(messages, tools, stream=True)
            msg = process_stream_response(stream, cost_tracker)
            messages.append(msg)
            print()
            return messages

        if should_compact(messages):
            print("\n  [compact] Context too large, compacting...", flush=True)
            before = len(messages)
            messages = compact_conversation(messages)
            print(f"  [compact] {before} -> {len(messages)} messages", flush=True)

        stream = call_with_retry(messages, tools, stream=True)
        msg = process_stream_response(stream, cost_tracker)
        messages.append(msg)

        if "tool_calls" not in msg or not msg["tool_calls"]:
            # v0.6 新增: PreCompletion 钩子
            assistant_text = msg.get("content", "") or ""
            pre_completion = hook_registry.dispatch_pre_completion(
                assistant_text, messages
            )
            if pre_completion.action == HookAction.DENY:
                # 回复被钩子拒绝, 要求模型补充
                messages.append({
                    "role": "user",
                    "content": (
                        f"[HOOK FEEDBACK] {pre_completion.feedback}\n"
                        f"Please revise your response."
                    ),
                })
                continue

            print()
            return messages

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

            # v0.6: 带钩子的工具执行
            result, hook_feedback = execute_tool_with_hooks(
                name, args_str, approval_flow, hook_registry
            )

            display = (
                result if len(result) <= 200 else result[:200] + "...(truncated)"
            )
            print(f"  -> {display}")

            if hook_feedback:
                print(f"  [hook] {hook_feedback}")

            # 记录到工具调用历史(供中间件使用)
            try:
                parsed_args = json.loads(args_str)
            except Exception:
                parsed_args = {}
            tool_calls_history.append({
                "tool": name,
                "args": parsed_args,
                "result": result[:500],
            })

            # 工具结果消息
            tool_content = result
            if hook_feedback:
                tool_content += f"\n\n[Hook Feedback] {hook_feedback}"

            messages.append({
                "role": "tool",
                "tool_call_id": tc["id"],
                "content": tool_content,
            })

        messages = truncate_tool_results(messages)

    print("\n[FunHarness] Max iterations reached.")
    return messages


# =============================================================
#  交互式 CLI (v0.6)
# =============================================================

def main():
    """FunHarness v0.6 交互式命令行

    新增命令:
    - /hooks      查看已注册的钩子
    - /middleware  查看中间件链
    - /plugins    查看已加载的插件
    """
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

    pm = PermissionManager(mode=mode)
    approval_flow = ApprovalFlow(pm)
    session_mgr = SessionManager()
    cost_tracker = CostTracker()

    # v0.6 新增子系统
    hook_registry = init_hooks()
    middleware_chain = init_middleware()
    plugin_loader = init_plugins()

    # 注册插件提供的钩子
    for hook_def in plugin_loader.get_all_hooks():
        hook_registry.register(
            event=hook_def["event"],
            handler=hook_def["handler"],
            name=hook_def.get("name", "plugin_hook"),
            priority=hook_def.get("priority", 200),
        )

    # 收集插件命令
    plugin_commands = plugin_loader.get_all_commands()

    tools = registry.get_openai_schemas()
    tool_names = [t["function"]["name"] for t in tools]

    print("=" * 55)
    print("  FunHarness v0.6")
    print(f"  Mode: {mode.value}")
    print(f"  Tools: {', '.join(tool_names)}")
    print(f"  Hooks: {sum(len(v) for v in hook_registry._hooks.values())}")
    print(f"  Middleware: {len(middleware_chain._middlewares)}")
    print("  Commands: quit, clear, /cost, /context,")
    print("            /save, /load, /branch, /memory,")
    print("            /skills, /mode, /perms,")
    print("            /hooks, /middleware, /plugins")
    if plugin_commands:
        print(f"  Plugin commands: {', '.join('/' + c for c in plugin_commands)}")
    print("=" * 55)

    # 组装 System Prompt
    system_prompt = build_v06_system_prompt(mode)
    messages = [{"role": "system", "content": system_prompt}]

    prompt_tokens = estimate_tokens(messages)
    print(f"  [System prompt: ~{prompt_tokens} tokens | mode: {mode.value}]")

    current_session = Session()
    current_session.messages = messages

    # 工具调用历史(跨轮次共享)
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

        if user_input.lower() == "/cost":
            print(f"  {cost_tracker.summary()}")
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
                            print(f"  Messages: {len(messages)}")
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
                print(f"  Parent: {branch.parent_id}")
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
                    system_prompt = build_v06_system_prompt(mode)
                    messages[0] = {"role": "system", "content": system_prompt}
                    print(f"  Mode switched to: {mode.value}")
                else:
                    print(f"  Unknown mode: {new_mode_str}")
                    print("  Available: auto, suggest, approve")
            else:
                print(f"  Current mode: {mode.value}")
                print("  Usage: /mode [auto|suggest|approve]")
            continue

        if user_input.lower() == "/perms":
            print(f"  Mode: {mode.value}")
            print(f"  Allowed dirs: {[str(d) for d in pm.path_policy.allowed]}")
            print(f"  Denied dirs: {[str(d) for d in pm.path_policy.denied]}")
            print(f"  Command whitelist: {len(pm.command_policy.whitelist)} entries")
            print(f"  Command blacklist: {len(pm.command_policy.blacklist)} entries")
            if approval_flow._always_allowed:
                print(f"  Always-allowed tools: {approval_flow._always_allowed}")
            continue

        # v0.6 新增命令
        if user_input.lower() == "/hooks":
            print(hook_registry.list_hooks())
            continue

        if user_input.lower() == "/middleware":
            print(middleware_chain.list_middlewares())
            continue

        if user_input.lower() == "/plugins":
            print(plugin_loader.list_plugins())
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
        )
        current_session.messages = messages
        tokens = estimate_tokens(messages)
        print(
            f"  [messages: {len(messages)} | ~{tokens} tokens "
            f"| {cost_tracker.summary()} | mode: {mode.value}]"
        )


if __name__ == "__main__":
    # 支持单次任务模式
    if len(sys.argv) > 1 and sys.argv[1] != "--mode":
        init_memory()
        init_skills()
        task = " ".join(sys.argv[1:])
        print(f"Task: {task}\n")
        agent_loop(task)
    elif len(sys.argv) > 3 and sys.argv[1] == "--mode":
        init_memory()
        init_skills()
        mode_str = sys.argv[2].lower()
        mode = {
            "auto": PermissionMode.AUTO,
            "suggest": PermissionMode.SUGGEST,
            "approve": PermissionMode.APPROVE,
        }.get(mode_str, PermissionMode.SUGGEST)
        task = " ".join(sys.argv[3:])
        print(f"Task: {task} (mode: {mode.value})\n")
        agent_loop(task, mode=mode)
    else:
        main()
