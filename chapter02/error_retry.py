"""
2.3.2 - 错误处理与指数退避重试
演示Agent Loop中的三类错误处理策略：
1. API调用失败的指数退避重试
2. 工具执行错误的优雅处理
3. 循环次数上限保护
"""
import json
import os
import time
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI, APIError, RateLimitError, APITimeoutError, APIConnectionError

load_dotenv(Path(__file__).resolve().parent.parent / ".env")
client = OpenAI()
MODEL = os.getenv("OPENAI_MODEL_NAME", "deepseek-chat")


def call_model_with_retry(client, max_retries=3, **kwargs):
    """
    带指数退避的API调用。
    遇到速率限制或临时网络错误时，自动等待并重试。
    """
    for attempt in range(max_retries):
        try:
            return client.chat.completions.create(**kwargs)
        except (RateLimitError, APITimeoutError, APIConnectionError) as e:
            if attempt == max_retries - 1:
                raise
            wait_time = 2 ** attempt  # 1秒, 2秒, 4秒...
            print(f"  [重试] {type(e).__name__}, {wait_time}秒后重试 "
                  f"(第{attempt + 1}/{max_retries}次)")
            time.sleep(wait_time)
        except APIError as e:
            # 不可恢复的API错误，立即抛出
            raise


def execute_tool_safely(tool_map, name, args_json):
    """
    安全地执行工具调用。
    捕获所有异常，返回错误信息而不是让程序崩溃。
    """
    try:
        args = json.loads(args_json)
    except json.JSONDecodeError as e:
        return f"参数解析失败: {e}"

    func = tool_map.get(name)
    if func is None:
        return f"未知工具: {name}"

    try:
        return func(**args)
    except Exception as e:
        return f"工具执行错误 ({name}): {type(e).__name__}: {e}"


# --- 示例工具 ---
tools = [
    {
        "type": "function",
        "function": {
            "name": "divide",
            "description": "计算两个数的除法，返回结果",
            "parameters": {
                "type": "object",
                "properties": {
                    "a": {"type": "number", "description": "被除数"},
                    "b": {"type": "number", "description": "除数"},
                },
                "required": ["a", "b"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "读取文件内容",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "文件路径"},
                },
                "required": ["path"],
            },
        },
    },
]


def divide(a: float, b: float) -> str:
    """除法 - 可能抛出ZeroDivisionError"""
    return str(a / b)


def read_file(path: str) -> str:
    """读取文件 - 可能抛出FileNotFoundError"""
    return Path(path).read_text(encoding="utf-8")


tool_map = {"divide": divide, "read_file": read_file}

MAX_ITERATIONS = 10  # 循环次数上限


def robust_agent_loop(user_input: str) -> str:
    """
    具备完整错误处理能力的Agent Loop。
    三层防护: API重试 + 工具异常捕获 + 循环上限。
    """
    messages = [
        {"role": "system", "content": "你是一个有用的助手，可以做除法运算和读取文件。"
         "如果工具返回了错误信息，请根据错误内容向用户解释情况。"},
        {"role": "user", "content": user_input},
    ]

    for iteration in range(1, MAX_ITERATIONS + 1):
        print(f"  [Loop] 第{iteration}轮迭代")

        # 带重试的API调用
        response = call_model_with_retry(
            client, model=MODEL, messages=messages, tools=tools
        )
        msg = response.choices[0].message
        messages.append(msg)

        # 退出条件
        if not msg.tool_calls:
            return msg.content

        # 安全执行每个工具
        for call in msg.tool_calls:
            result = execute_tool_safely(
                tool_map, call.function.name, call.function.arguments
            )
            print(f"    -> {call.function.name}: {result[:60]}")
            messages.append(
                {"role": "tool", "tool_call_id": call.id, "content": result}
            )

    return "[Agent Loop] 已达到最大迭代次数，任务未能完成。"


if __name__ == "__main__":
    # 场景1: 正常除法
    print("\n--- 场景1: 正常计算 ---")
    result = robust_agent_loop("请帮我计算 100 除以 7")
    print(f"结果: {result}\n")

    # 场景2: 除以零 - 工具会抛出异常，但Agent能优雅处理
    print("--- 场景2: 除以零（工具异常） ---")
    result = robust_agent_loop("请帮我算 42 除以 0")
    print(f"结果: {result}\n")

    # 场景3: 读取不存在的文件 - 工具会返回错误信息
    print("--- 场景3: 读取不存在的文件 ---")
    result = robust_agent_loop("帮我读取 /nonexistent/file.txt 的内容")
    print(f"结果: {result}")
