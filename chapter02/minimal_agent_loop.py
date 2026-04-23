"""
2.1.3 - 用30行代码实现最小Agent Loop
演示Agent Loop的核心思想：模型在循环中不断推理和调用工具，
直到它认为任务完成、不再需要工具时自动停止。
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
            "name": "calculate",
            "description": "计算一个数学表达式并返回结果，例如: '2 + 3 * 4'",
            "parameters": {
                "type": "object",
                "properties": {
                    "expression": {
                        "type": "string",
                        "description": "要计算的数学表达式",
                    }
                },
                "required": ["expression"],
            },
        },
    },
]


def calculate(expression: str) -> str:
    """安全地计算数学表达式"""
    try:
        result = eval(expression, {"__builtins__": {}})
        return str(result)
    except Exception as e:
        return f"计算错误: {e}"


tool_map = {"calculate": calculate}


# --- 最小Agent Loop ---
def agent_loop(user_input: str) -> str:
    """
    最小Agent Loop：不到30行核心代码。
    模型在循环中自主决策，直到返回纯文本回复时退出。
    """
    messages = [
        {"role": "system", "content": "你是一个有用的助手，可以使用计算器工具来帮助用户。"},
        {"role": "user", "content": user_input},
    ]

    while True:
        response = client.chat.completions.create(
            model=MODEL, messages=messages, tools=tools
        )
        msg = response.choices[0].message
        messages.append(msg)

        # 退出条件：模型没有请求调用任何工具
        if not msg.tool_calls:
            return msg.content

        # 执行每个工具调用，将结果追加到消息历史
        for call in msg.tool_calls:
            args = json.loads(call.function.arguments)
            result = tool_map[call.function.name](**args)
            messages.append(
                {"role": "tool", "tool_call_id": call.id, "content": result}
            )


if __name__ == "__main__":
    # 简单计算: 一次工具调用就能完成
    print("--- 场景1: 简单计算 ---")
    answer = agent_loop("请帮我算一下 123 * 456 等于多少?")
    print(f"回答: {answer}\n")

    # 多步计算: 模型可能需要多轮工具调用
    print("--- 场景2: 多步计算 ---")
    answer = agent_loop(
        "一个圆的半径是7厘米，请先算出它的面积（用3.14159作为pi），"
        "再算出面积的平方根，最后把结果四舍五入到小数点后两位。"
    )
    print(f"回答: {answer}\n")

    # 不需要工具: 模型直接回答
    print("--- 场景3: 不需要工具 ---")
    answer = agent_loop("什么是Agent Loop?")
    print(f"回答: {answer}")
