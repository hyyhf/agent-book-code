"""
4.5 - FunHarness v0.2: 工具注册表 + 六个核心工具 + Agent Loop

在 v0.1 的基础上, 引入:
- 基于装饰器的工具注册表(替代硬编码的工具列表)
- 六个核心工具: read_file, write_file, replace_in_file,
  run_command, list_directory, grep_search
- 三阶段执行生命周期: 解析 -> 校验 -> 执行

运行方式:
    uv run python chapter04/funharness_v02.py
    uv run python chapter04/funharness_v02.py "列出当前目录的文件"
"""
import json
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI, RateLimitError, APITimeoutError, APIConnectionError

_dir = str(Path(__file__).resolve().parent)
if _dir not in sys.path:
    sys.path.insert(0, _dir)

from core_tools import registry

load_dotenv(Path(__file__).resolve().parent.parent / ".env")
client = OpenAI()
MODEL = os.getenv("OPENAI_MODEL_NAME", "deepseek-chat")

# =============================================================
#  System Prompt
# =============================================================

SYSTEM_PROMPT = """\
你是 FunHarness v0.2, 一个由 AI 驱动的编程助手。

你拥有以下工具能力:
- 文件操作: 读取、写入、精确替换文件内容
- 命令执行: 在系统 Shell 中运行命令
- 搜索检索: 列出目录结构、按模式搜索文件内容

工作准则:
- 在执行操作前先说明你打算做什么
- 每次操作后检查结果, 确认是否成功
- 如果遇到错误, 分析原因并尝试解决
- 完成任务后给出简洁的总结"""

MAX_ITERATIONS = 20

# =============================================================
#  工具执行(三阶段生命周期)
# =============================================================


def execute_tool(tool_name: str, arguments_json: str) -> str:
    """解析参数 -> 校验 -> 执行, 任何阶段的错误都转为可读信息。"""
    # 阶段1: 解析
    try:
        args = json.loads(arguments_json)
    except json.JSONDecodeError as e:
        return f"参数解析失败: {e}"

    # 阶段1: 校验
    func = registry.get_function(tool_name)
    if not func:
        return f"未知工具: {tool_name}"

    schema = registry.get_schema(tool_name)
    if schema:
        required = schema["function"]["parameters"].get("required", [])
        missing = [p for p in required if p not in args]
        if missing:
            return f"缺少必填参数: {', '.join(missing)}"

    # 阶段2+3: 执行 + 异常捕获
    try:
        return str(func(**args))
    except Exception as e:
        return f"工具执行失败 ({tool_name}): {e}"


# =============================================================
#  Agent Loop 核心
# =============================================================


def call_with_retry(messages, stream=False, max_retries=3):
    """带指数退避的 API 调用"""
    for attempt in range(max_retries):
        try:
            return client.chat.completions.create(
                model=MODEL,
                messages=messages,
                tools=registry.get_openai_schemas(),
                stream=stream,
            )
        except (RateLimitError, APITimeoutError, APIConnectionError) as e:
            if attempt == max_retries - 1:
                raise
            wait = 2 ** attempt
            print(f"\n  [重试] {wait}秒后重试...", flush=True)
            time.sleep(wait)


def process_stream_response(stream):
    """处理流式响应, 收集完整的 assistant 消息"""
    content_parts = []
    tool_calls_data = {}

    for chunk in stream:
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


def agent_loop(user_input, messages=None):
    """
    FunHarness v0.2 的核心 Agent Loop。
    相比 v0.1, 工具由 ToolRegistry 统一管理,
    执行经过三阶段生命周期(解析->校验->执行)。
    """
    if messages is None:
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]

    messages.append({"role": "user", "content": user_input})

    for iteration in range(1, MAX_ITERATIONS + 1):
        stream = call_with_retry(messages, stream=True)
        msg = process_stream_response(stream)
        messages.append(msg)

        # 退出条件: 模型返回纯文本, 没有工具调用
        if "tool_calls" not in msg or not msg["tool_calls"]:
            print()
            return messages

        print()

        # 执行所有工具调用
        for tc in msg["tool_calls"]:
            name = tc["function"]["name"]
            args_str = tc["function"]["arguments"]

            # 简洁的工具调用日志
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
                result if len(result) <= 200 else result[:200] + "...(已截断)"
            )
            print(f"  -> {display}")

            messages.append({
                "role": "tool",
                "tool_call_id": tc["id"],
                "content": result,
            })

    print("\n[FunHarness] 达到最大迭代次数, 停止执行。")
    return messages


# =============================================================
#  交互式 CLI
# =============================================================


def main():
    """FunHarness v0.2 交互式命令行"""
    tools = registry.get_openai_schemas()
    tool_names = [t["function"]["name"] for t in tools]

    print("=" * 50)
    print("  FunHarness v0.2")
    print(f"  工具: {', '.join(tool_names)}")
    print("  输入 quit 退出, clear 清空对话")
    print("=" * 50)

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]

    while True:
        try:
            user_input = input("\n> ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\n再见!")
            break

        if not user_input:
            continue
        if user_input.lower() == "quit":
            print("再见!")
            break
        if user_input.lower() == "clear":
            messages = [{"role": "system", "content": SYSTEM_PROMPT}]
            print("[对话已清空]")
            continue

        print()
        messages = agent_loop(user_input, messages)
        token_count = sum(
            len(str(m.get("content", "") if isinstance(m, dict) else m.content or ""))
            for m in messages
        )
        print(f"  [对话: {len(messages)} 条消息, 约 {token_count} 字符]")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        task = " ".join(sys.argv[1:])
        print(f"任务: {task}\n")
        agent_loop(task)
    else:
        main()
