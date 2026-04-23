"""
2.4 - FunHarness v0.1: 核心Agent Loop + 文件读写工具
这是FunHarness的第一个版本，包含：
- 一个完整的Agent Loop（带错误处理、流式输出、迭代上限）
- 两个基础工具：读取文件、写入文件
- 一个交互式命令行界面
"""
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI, RateLimitError, APITimeoutError, APIConnectionError

load_dotenv(Path(__file__).resolve().parent.parent / ".env")
client = OpenAI()
MODEL = os.getenv("OPENAI_MODEL_NAME", "deepseek-chat")

# =============================================================
#  工具定义
# =============================================================

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "读取指定文件的完整内容。如果文件不存在会返回错误信息。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "要读取的文件路径（相对路径或绝对路径）",
                    }
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "将内容写入指定文件。如果文件已存在则覆盖，不存在则创建（包括必要的父目录）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "要写入的文件路径",
                    },
                    "content": {
                        "type": "string",
                        "description": "要写入的文件内容",
                    },
                },
                "required": ["path", "content"],
            },
        },
    },
]

# =============================================================
#  工具实现
# =============================================================


def read_file(path: str) -> str:
    """读取文件内容"""
    try:
        return Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        return f"错误: 文件 '{path}' 不存在"
    except PermissionError:
        return f"错误: 没有权限读取 '{path}'"
    except Exception as e:
        return f"读取文件时出错: {e}"


def write_file(path: str, content: str) -> str:
    """写入文件内容"""
    try:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return f"文件已写入: {path} ({len(content)} 字符)"
    except PermissionError:
        return f"错误: 没有权限写入 '{path}'"
    except Exception as e:
        return f"写入文件时出错: {e}"


TOOL_MAP = {
    "read_file": read_file,
    "write_file": write_file,
}

# =============================================================
#  Agent Loop 核心
# =============================================================

SYSTEM_PROMPT = """你是FunHarness，一个由AI驱动的编程助手。
你可以读取和写入文件来帮助用户完成任务。

工作准则：
- 在执行操作前先说明你打算做什么
- 每次操作后检查结果，确认是否成功
- 如果遇到错误，分析原因并尝试解决
- 完成任务后给出简洁的总结"""

MAX_ITERATIONS = 20


def call_with_retry(messages, stream=False, max_retries=3):
    """带指数退避的API调用"""
    import time

    for attempt in range(max_retries):
        try:
            return client.chat.completions.create(
                model=MODEL,
                messages=messages,
                tools=TOOLS,
                stream=stream,
            )
        except (RateLimitError, APITimeoutError, APIConnectionError) as e:
            if attempt == max_retries - 1:
                raise
            wait = 2**attempt
            print(f"\n  [重试] {wait}秒后重试...", flush=True)
            time.sleep(wait)


def execute_tool(name, arguments_json):
    """安全执行工具调用"""
    try:
        args = json.loads(arguments_json)
    except json.JSONDecodeError as e:
        return f"参数解析失败: {e}"

    func = TOOL_MAP.get(name)
    if not func:
        return f"未知工具: {name}"

    try:
        return func(**args)
    except Exception as e:
        return f"工具执行失败 ({name}): {e}"


def process_stream_response(stream):
    """处理流式响应，收集完整的assistant消息"""
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
    FunHarness v0.1 的核心Agent Loop。
    接收用户输入，在循环中不断调用模型和工具，直到任务完成。
    """
    if messages is None:
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]

    messages.append({"role": "user", "content": user_input})

    for iteration in range(1, MAX_ITERATIONS + 1):
        # 调用模型（流式）
        stream = call_with_retry(messages, stream=True)
        msg = process_stream_response(stream)
        messages.append(msg)

        # 退出条件：模型返回纯文本，没有工具调用
        if "tool_calls" not in msg or not msg["tool_calls"]:
            print()  # 换行
            return messages

        print()  # 换行

        # 执行所有工具调用
        for tc in msg["tool_calls"]:
            name = tc["function"]["name"]
            args_str = tc["function"]["arguments"]
            # 简洁的工具调用日志
            try:
                args_preview = json.loads(args_str)
                if "content" in args_preview:
                    args_preview["content"] = (
                        args_preview["content"][:40] + "..."
                        if len(args_preview["content"]) > 40
                        else args_preview["content"]
                    )
                preview = json.dumps(args_preview, ensure_ascii=False)
            except Exception:
                preview = args_str[:60]

            print(f"  [{name}] {preview}")
            result = execute_tool(name, args_str)
            # 截断过长的工具结果展示
            display = result if len(result) <= 200 else result[:200] + "...(已截断)"
            print(f"  -> {display}")
            messages.append(
                {"role": "tool", "tool_call_id": tc["id"], "content": result}
            )

    print("\n[FunHarness] 达到最大迭代次数，停止执行。")
    return messages


# =============================================================
#  交互式CLI
# =============================================================


def main():
    """FunHarness v0.1 交互式命令行"""
    print("=" * 50)
    print("  FunHarness v0.1")
    print("  一个拥有文件读写能力的AI编程助手")
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
    # 如果传入了命令行参数，作为一次性任务执行
    if len(sys.argv) > 1:
        task = " ".join(sys.argv[1:])
        print(f"任务: {task}\n")
        agent_loop(task)
    else:
        main()
