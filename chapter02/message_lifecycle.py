"""
2.2 - 消息流与对话历史
演示消息的角色与结构，以及工具调用消息的完整生命周期。
通过打印每一步的消息列表，让读者直观看到对话历史是如何增长的。
"""
import json
import os
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv(Path(__file__).resolve().parent.parent / ".env")
client = OpenAI()
MODEL = os.getenv("OPENAI_MODEL_NAME", "deepseek-chat")

# --- 工具定义与实现 ---
tools = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "读取指定路径的文件内容",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "文件路径",
                    }
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "列出指定目录下的所有文件和子目录",
            "parameters": {
                "type": "object",
                "properties": {
                    "directory": {
                        "type": "string",
                        "description": "目录路径",
                    }
                },
                "required": ["directory"],
            },
        },
    },
]


def read_file(path: str) -> str:
    """读取文件内容（模拟）"""
    mock_files = {
        "project/main.py": 'print("Hello, Agent!")\n',
        "project/config.json": '{"name": "demo", "version": "1.0"}',
        "project/README.md": "# Demo Project\n\nA simple demonstration.",
    }
    content = mock_files.get(path)
    if content is None:
        return f"错误: 文件 '{path}' 不存在"
    return content


def list_files(directory: str) -> str:
    """列出目录内容（模拟）"""
    mock_dirs = {
        "project": ["main.py", "config.json", "README.md"],
        "project/src": ["app.py", "utils.py"],
    }
    files = mock_dirs.get(directory)
    if files is None:
        return f"错误: 目录 '{directory}' 不存在"
    return "\n".join(files)


tool_map = {"read_file": read_file, "list_files": list_files}


def print_messages(messages: list, step: str):
    """格式化打印当前消息列表，用于教学演示"""
    print(f"\n{'='*60}")
    print(f"  [{step}] 当前消息列表 ({len(messages)} 条消息)")
    print(f"{'='*60}")
    for i, msg in enumerate(messages):
        role = msg["role"] if isinstance(msg, dict) else msg.role

        if role == "system":
            content = msg["content"] if isinstance(msg, dict) else msg.content
            print(f"  [{i}] system: {content[:50]}...")
        elif role == "user":
            content = msg["content"] if isinstance(msg, dict) else msg.content
            print(f"  [{i}] user: {content}")
        elif role == "assistant":
            # assistant消息可能包含tool_calls
            tool_calls = (
                msg.get("tool_calls") if isinstance(msg, dict) else msg.tool_calls
            )
            content = msg.get("content") if isinstance(msg, dict) else msg.content
            if tool_calls:
                print(f"  [{i}] assistant: [请求调用 {len(tool_calls)} 个工具]")
                for tc in tool_calls:
                    fn = tc["function"] if isinstance(tc, dict) else tc.function
                    name = fn["name"] if isinstance(fn, dict) else fn.name
                    args = fn["arguments"] if isinstance(fn, dict) else fn.arguments
                    print(f"       -> {name}({args})")
            else:
                text = content or "(空)"
                print(f"  [{i}] assistant: {text[:80]}")
        elif role == "tool":
            tid = (
                msg.get("tool_call_id") if isinstance(msg, dict) else msg.tool_call_id
            )
            content = msg["content"] if isinstance(msg, dict) else msg.content
            print(f"  [{i}] tool [id={tid[:12]}...]: {content[:60]}")
    print()


def agent_loop_with_trace(user_input: str) -> str:
    """带有完整消息追踪的Agent Loop"""
    messages = [
        {
            "role": "system",
            "content": "你是一个文件系统助手。你可以列出目录内容和读取文件。"
            "请根据用户的需求使用工具来完成任务。",
        },
        {"role": "user", "content": user_input},
    ]

    print_messages(messages, "初始状态")

    loop_count = 0
    while True:
        loop_count += 1
        response = client.chat.completions.create(
            model=MODEL, messages=messages, tools=tools
        )
        msg = response.choices[0].message
        messages.append(msg)

        if not msg.tool_calls:
            print_messages(messages, f"第{loop_count}轮 - 模型返回文本，循环结束")
            return msg.content

        print_messages(messages, f"第{loop_count}轮 - 模型请求工具调用")

        # 执行工具并追加结果
        for call in msg.tool_calls:
            args = json.loads(call.function.arguments)
            result = tool_map[call.function.name](**args)
            messages.append(
                {"role": "tool", "tool_call_id": call.id, "content": result}
            )

        print_messages(messages, f"第{loop_count}轮 - 工具结果已追加")


if __name__ == "__main__":
    print("=" * 60)
    print("  消息流与对话历史 - 完整生命周期演示")
    print("=" * 60)

    result = agent_loop_with_trace(
        "帮我看看project目录下有什么文件，然后读一下config.json的内容。"
    )
    print(f"\n最终回答: {result}")
