"""
6.4 - FunHarness v0.4: 记忆 + 会话管理 + 技能系统

在 v0.3 的基础上引入:
- 持久化记忆: MEMORY.md 模式, 跨会话知识沉淀
- 会话管理: 保存、恢复、分支对话历史
- 技能系统: 按需加载领域知识到上下文中

运行方式:
    uv run python chapter06/funharness_v04.py
    uv run python chapter06/funharness_v04.py "读取 MEMORY.md 中的记忆"
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

load_dotenv(Path(__file__).resolve().parent.parent / ".env")
client = OpenAI()
MODEL = os.getenv("OPENAI_MODEL_NAME", "deepseek-v4-flash")

MAX_ITERATIONS = 20

# =============================================================
#  工具注册: 复用 chapter04 的工具 + 新增记忆/技能工具
# =============================================================

# 创建新的注册表, 重新注册所有工具
registry = ToolRegistry()


# --- 文件操作工具 (复用 chapter04) ---
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


# --- 记忆工具 (chapter06 新增) ---
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


# --- 技能工具 (chapter06 新增) ---
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
#  增强的 System Prompt: 注入记忆摘要
# =============================================================

# 更新身份块, 体现 v0.4 的新能力
IDENTITY_V04 = """\
You are FunHarness v0.4, an AI-powered programming assistant.

Your core behaviors:
- Explain your plan before taking action.
- Verify the result after each operation.
- If an error occurs, analyze the cause and attempt to fix it.
- Provide a concise summary when the task is complete.
- When uncertain, ask the user for clarification instead of guessing.
- Prefer precise file edits over full-file rewrites.
- Never execute destructive commands without explicit user confirmation.

Memory & Knowledge:
- Use save_memory to record important discoveries for future sessions.
- Use read_memory or search_memory to recall previously saved knowledge.
- Use load_skill to access domain-specific knowledge when needed.
- Proactively save useful findings that might help in future tasks."""


def build_v04_system_prompt() -> str:
    """组装 v0.4 的 System Prompt, 包含记忆摘要。"""
    from system_prompt_builder import build_environment_block, build_tools_guide

    sections = [
        IDENTITY_V04,
        build_environment_block(),
        build_tools_guide(registry),
    ]

    # 注入项目上下文
    context_block = build_context_block()
    if context_block:
        sections.append(context_block)

    # 注入记忆摘要(仅前 2000 字符, 避免占用过多上下文)
    memory_text = read_memory()
    if memory_text and memory_text != "(no memories saved yet)":
        summary = memory_text[:2000]
        if len(memory_text) > 2000:
            summary += "\n...(use read_memory for full content)"
        sections.append(f"# Saved Memories\n{summary}")

    return "\n\n".join(sections)


# =============================================================
#  工具执行 (复用 v0.3 逻辑)
# =============================================================

def execute_tool(tool_name: str, arguments_json: str) -> str:
    """解析参数 -> 校验 -> 执行, 任何阶段的错误都转为可读信息。"""
    try:
        args = json.loads(arguments_json)
    except json.JSONDecodeError as e:
        return f"参数解析失败: {e}"

    func = registry.get_function(tool_name)
    if not func:
        return f"未知工具: {tool_name}"

    schema = registry.get_schema(tool_name)
    if schema:
        required = schema["function"]["parameters"].get("required", [])
        missing = [p for p in required if p not in args]
        if missing:
            return f"缺少必填参数: {', '.join(missing)}"

    try:
        return str(func(**args))
    except Exception as e:
        return f"工具执行失败 ({tool_name}): {e}"


# =============================================================
#  Agent Loop (v0.4)
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


def agent_loop(user_input, messages=None, cost_tracker=None):
    """FunHarness v0.4 的核心 Agent Loop。

    相比 v0.3 的增强:
    1. 记忆摘要注入 System Prompt
    2. 新增记忆读写和技能加载工具
    3. 会话保存由外层 CLI 在退出时调用
    """
    if cost_tracker is None:
        cost_tracker = CostTracker()

    if messages is None:
        system_prompt = build_v04_system_prompt()
        messages = [{"role": "system", "content": system_prompt}]

    messages.append({"role": "user", "content": user_input})

    tools = registry.get_openai_schemas()

    for iteration in range(1, MAX_ITERATIONS + 1):
        if should_compact(messages):
            print("\n  [compact] Context too large, compacting...", flush=True)
            before = len(messages)
            messages = compact_conversation(messages)
            print(f"  [compact] {before} -> {len(messages)} messages", flush=True)

        stream = call_with_retry(messages, tools, stream=True)
        msg = process_stream_response(stream, cost_tracker)
        messages.append(msg)

        if "tool_calls" not in msg or not msg["tool_calls"]:
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

            print(f"  [{name}] {preview}")
            result = execute_tool(name, args_str)

            display = (
                result if len(result) <= 200 else result[:200] + "...(truncated)"
            )
            print(f"  -> {display}")

            messages.append({
                "role": "tool",
                "tool_call_id": tc["id"],
                "content": result,
            })

        messages = truncate_tool_results(messages)

    print("\n[FunHarness] Max iterations reached.")
    return messages


# =============================================================
#  交互式 CLI (v0.4 增强)
# =============================================================

def main():
    """FunHarness v0.4 交互式命令行

    新增命令:
    - /save     保存当前会话
    - /load     列出并恢复会话
    - /branch   从当前会话创建分支
    - /memory   查看记忆摘要
    - /skills   列出可用技能
    """
    # 初始化记忆和技能系统
    init_memory()
    init_skills()

    session_mgr = SessionManager()
    cost_tracker = CostTracker()

    tools = registry.get_openai_schemas()
    tool_names = [t["function"]["name"] for t in tools]

    print("=" * 55)
    print("  FunHarness v0.4")
    print(f"  Tools: {', '.join(tool_names)}")
    print("  New: memory, sessions, skills")
    print("  Commands: quit, clear, /cost, /context,")
    print("            /save, /load, /branch, /memory, /skills")
    print("=" * 55)

    # 组装 System Prompt
    system_prompt = build_v04_system_prompt()
    messages = [{"role": "system", "content": system_prompt}]

    prompt_tokens = estimate_tokens(messages)
    print(f"  [System prompt: ~{prompt_tokens} tokens]")

    # 当前会话对象
    current_session = Session()
    current_session.messages = messages

    while True:
        try:
            user_input = input("\n> ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nBye!")
            break

        if not user_input:
            continue

        if user_input.lower() == "quit":
            # 退出前自动保存会话
            current_session.messages = messages
            session_mgr.save(current_session)
            print(f"  [session auto-saved: {current_session.id}]")
            print("Bye!")
            break

        if user_input.lower() == "clear":
            # 保存旧会话, 开始新会话
            current_session.messages = messages
            session_mgr.save(current_session)
            current_session = Session()
            messages = [{"role": "system", "content": system_prompt}]
            current_session.messages = messages
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

        print()
        messages = agent_loop(user_input, messages, cost_tracker)
        current_session.messages = messages
        tokens = estimate_tokens(messages)
        print(f"  [messages: {len(messages)} | ~{tokens} tokens | {cost_tracker.summary()}]")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        init_memory()
        init_skills()
        task = " ".join(sys.argv[1:])
        print(f"Task: {task}\n")
        agent_loop(task)
    else:
        main()
