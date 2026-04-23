"""
5.4 - FunHarness v0.3: 动态 System Prompt + 上下文发现 + 上下文压缩

在 v0.2 的基础上引入:
- 分层组装的动态 System Prompt(身份 + 环境 + 工具指南)
- 项目上下文自动发现与注入(配置文件、目录结构)
- 上下文压缩机制(工具结果截断 + 对话摘要)
- Token 用量与成本追踪

运行方式:
    uv run python chapter05/funharness_v03.py
    uv run python chapter05/funharness_v03.py "列出当前目录并总结项目结构"
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

_ch04_dir = str(Path(__file__).resolve().parent.parent / "chapter04")
if _ch04_dir not in sys.path:
    sys.path.insert(0, _ch04_dir)

from core_tools import registry
from system_prompt_builder import build_system_prompt
from context_discovery import build_context_block
from context_compaction import (
    CostTracker,
    estimate_tokens,
    truncate_tool_results,
    compact_conversation,
    should_compact,
)

load_dotenv(Path(__file__).resolve().parent.parent / ".env")
client = OpenAI()
MODEL = os.getenv("OPENAI_MODEL_NAME", "deepseek-chat")

MAX_ITERATIONS = 20

# =============================================================
#  工具执行(复用 v0.2 的三阶段生命周期)
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
#  Agent Loop 核心(v0.3 增强版)
# =============================================================


def call_with_retry(messages, tools, stream=False, max_retries=3):
    """带指数退避的 API 调用, 返回响应和 usage。"""
    for attempt in range(max_retries):
        try:
            response = client.chat.completions.create(
                model=MODEL,
                messages=messages,
                tools=tools,
                stream=stream,
            )
            return response
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
        # 从最后一个 chunk 提取 usage(如果有)
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
    """
    FunHarness v0.3 的核心 Agent Loop。

    相比 v0.2 的三项增强:
    1. System Prompt 由 build_system_prompt() 动态组装
    2. 每轮循环后执行工具结果截断(轻量压缩)
    3. 当上下文超过阈值时触发完整压缩(对话摘要)
    """
    if cost_tracker is None:
        cost_tracker = CostTracker()

    if messages is None:
        # 动态组装 System Prompt: 包含身份、环境、工具指南、项目上下文
        context_block = build_context_block()
        system_prompt = build_system_prompt(
            registry,
            extra_context=context_block,
        )
        messages = [{"role": "system", "content": system_prompt}]

    messages.append({"role": "user", "content": user_input})

    tools = registry.get_openai_schemas()

    for iteration in range(1, MAX_ITERATIONS + 1):
        # 压缩检查: 上下文过大时触发 compaction
        if should_compact(messages):
            print("\n  [compact] Context too large, compacting...", flush=True)
            before = len(messages)
            messages = compact_conversation(messages)
            print(f"  [compact] {before} -> {len(messages)} messages", flush=True)

        stream = call_with_retry(messages, tools, stream=True)
        msg = process_stream_response(stream, cost_tracker)
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
                result if len(result) <= 200 else result[:200] + "...(truncated)"
            )
            print(f"  -> {display}")

            messages.append({
                "role": "tool",
                "tool_call_id": tc["id"],
                "content": result,
            })

        # 轻量压缩: 截断较早的工具结果
        messages = truncate_tool_results(messages)

    print("\n[FunHarness] Max iterations reached.")
    return messages


# =============================================================
#  交互式 CLI
# =============================================================


def main():
    """FunHarness v0.3 交互式命令行"""
    tools = registry.get_openai_schemas()
    tool_names = [t["function"]["name"] for t in tools]

    print("=" * 50)
    print("  FunHarness v0.3")
    print(f"  Tools: {', '.join(tool_names)}")
    print("  New: dynamic prompt, context discovery, compaction")
    print("  Commands: quit, clear, /cost, /context")
    print("=" * 50)

    cost_tracker = CostTracker()

    # 动态组装 System Prompt
    context_block = build_context_block()
    system_prompt = build_system_prompt(
        registry,
        extra_context=context_block,
    )
    messages = [{"role": "system", "content": system_prompt}]

    prompt_tokens = estimate_tokens(messages)
    print(f"  [System prompt: ~{prompt_tokens} tokens]")

    while True:
        try:
            user_input = input("\n> ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nBye!")
            break

        if not user_input:
            continue
        if user_input.lower() == "quit":
            print("Bye!")
            break
        if user_input.lower() == "clear":
            messages = [{"role": "system", "content": system_prompt}]
            print("[conversation cleared]")
            continue
        if user_input.lower() == "/cost":
            print(f"  {cost_tracker.summary()}")
            continue
        if user_input.lower() == "/context":
            ctx_chars = len(system_prompt)
            msg_tokens = estimate_tokens(messages)
            print(f"  System prompt: {ctx_chars} chars")
            print(f"  Total context: ~{msg_tokens} tokens")
            print(f"  Messages: {len(messages)}")
            continue

        print()
        messages = agent_loop(user_input, messages, cost_tracker)
        tokens = estimate_tokens(messages)
        print(f"  [messages: {len(messages)} | ~{tokens} tokens | {cost_tracker.summary()}]")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        task = " ".join(sys.argv[1:])
        print(f"Task: {task}\n")
        agent_loop(task)
    else:
        main()
