"""
1.3 - Function Calling 机制完整演示
展示从工具定义到模型调用、到结果返回的完整流程。
包含单工具调用和并行工具调用两种场景。
"""
import json
import os
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

client = OpenAI()
MODEL = os.getenv("OPENAI_MODEL_NAME", "deepseek-v4-flash")

# --- 1. 定义工具 ---
# 工具定义使用JSON Schema描述参数，让模型知道如何调用
tools = [
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "查询指定城市的当前天气信息",
            "parameters": {
                "type": "object",
                "properties": {
                    "city": {
                        "type": "string",
                        "description": "城市名称，例如: 北京、上海",
                    },
                },
                "required": ["city"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_time",
            "description": "查询指定城市的当前时间",
            "parameters": {
                "type": "object",
                "properties": {
                    "city": {
                        "type": "string",
                        "description": "城市名称",
                    },
                },
                "required": ["city"],
            },
        },
    },
]


# --- 2. 工具的实际实现（模拟） ---
def get_weather(city: str) -> str:
    """模拟天气查询"""
    mock_data = {
        "北京": "晴，25 C，湿度40%",
        "上海": "多云，28 C，湿度65%",
        "武汉": "小雨，22 C，湿度80%",
    }
    return mock_data.get(city, f"{city}：暂无天气数据")


def get_time(city: str) -> str:
    """模拟时间查询"""
    return f"{city}当前时间: 2026-04-20 14:30:00 CST"


# 函数名 -> 函数对象 的映射表
tool_functions = {
    "get_weather": get_weather,
    "get_time": get_time,
}


def run_with_tools(user_message: str) -> str:
    """
    完整的工具调用流程：
    用户提问 -> 模型决策 -> 执行工具 -> 模型汇总 -> 最终回答
    """
    print(f"\n{'='*50}")
    print(f"用户: {user_message}")
    print(f"{'='*50}")

    messages = [
        {"role": "user", "content": user_message},
    ]

    # 第一次调用: 让模型决定是否需要调用工具
    response = client.chat.completions.create(
        model=MODEL,
        messages=messages,
        tools=tools,
    )

    assistant_message = response.choices[0].message

    # 如果模型没有调用工具，直接返回文本回复
    if not assistant_message.tool_calls:
        print(f"模型直接回复: {assistant_message.content}")
        return assistant_message.content

    # 模型选择了工具调用
    print(f"\n模型决定调用 {len(assistant_message.tool_calls)} 个工具:")
    messages.append(assistant_message)

    for tool_call in assistant_message.tool_calls:
        fn_name = tool_call.function.name
        fn_args = json.loads(tool_call.function.arguments)
        print(f"  - {fn_name}({fn_args})")

        # 执行工具
        fn = tool_functions[fn_name]
        result = fn(**fn_args)
        print(f"    结果: {result}")

        # 将工具结果添加到消息列表
        messages.append({
            "role": "tool",
            "tool_call_id": tool_call.id,
            "content": result,
        })

    # 第二次调用: 让模型根据工具结果生成最终回答
    final_response = client.chat.completions.create(
        model=MODEL,
        messages=messages,
        tools=tools,
    )

    final_reply = final_response.choices[0].message.content
    print(f"\n最终回答: {final_reply}")
    return final_reply


# --- 3. 运行演示 ---
if __name__ == "__main__":
    # 场景1: 单工具调用
    run_with_tools("北京今天天气怎么样?")

    # 场景2: 并行工具调用 - 模型可能同时调用多个工具
    run_with_tools("北京和上海的天气分别怎么样?")

    # 场景3: 跨工具调用
    run_with_tools("武汉现在几点了，天气如何?")

    # 场景4: 不需要工具的问题
    run_with_tools("1+1等于多少?")
