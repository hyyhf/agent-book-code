"""
2.3.3 - 流式输出(Streaming)在Agent Loop中的应用
演示如何在Agent Loop中使用流式输出，
让用户在模型生成回答的过程中就能看到实时内容。
同时处理流式模式下的工具调用。
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
            "name": "get_weather",
            "description": "查询指定城市的当前天气信息",
            "parameters": {
                "type": "object",
                "properties": {
                    "city": {
                        "type": "string",
                        "description": "城市名称",
                    }
                },
                "required": ["city"],
            },
        },
    },
]


def get_weather(city: str) -> str:
    mock_data = {
        "北京": "晴，气温28度，微风",
        "上海": "多云转阴，气温25度，东南风3级",
        "武汉": "小雨，气温22度，湿度85%",
    }
    return mock_data.get(city, f"{city}: 暂无天气数据")


tool_map = {"get_weather": get_weather}


def process_stream(stream):
    """
    处理流式响应，逐块收集内容和工具调用信息。
    返回完整的assistant消息（包含content或tool_calls）。
    """
    content_parts = []
    tool_calls_data = {}  # index -> {id, name, arguments}

    for chunk in stream:
        delta = chunk.choices[0].delta

        # 收集文本内容并实时打印
        if delta.content:
            print(delta.content, end="", flush=True)
            content_parts.append(delta.content)

        # 收集工具调用（流式模式下，工具调用信息分散在多个chunk中）
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

    # 构造完整的assistant消息
    full_content = "".join(content_parts) if content_parts else None

    if tool_calls_data:
        # 构造OpenAI格式的tool_calls列表
        assembled_tool_calls = []
        for idx in sorted(tool_calls_data.keys()):
            tc = tool_calls_data[idx]
            assembled_tool_calls.append({
                "id": tc["id"],
                "type": "function",
                "function": {"name": tc["name"], "arguments": tc["arguments"]},
            })
        return {
            "role": "assistant",
            "content": full_content,
            "tool_calls": assembled_tool_calls,
        }

    return {"role": "assistant", "content": full_content}


def streaming_agent_loop(user_input: str) -> str:
    """带流式输出的Agent Loop"""
    messages = [
        {"role": "system", "content": "你是一个天气助手。用简洁友好的语气回答问题。"},
        {"role": "user", "content": user_input},
    ]

    max_iterations = 10
    for i in range(max_iterations):
        stream = client.chat.completions.create(
            model=MODEL, messages=messages, tools=tools, stream=True
        )

        print(f"\n[助手] ", end="")
        msg = process_stream(stream)
        messages.append(msg)

        # 退出条件：无工具调用
        if "tool_calls" not in msg or not msg["tool_calls"]:
            print()  # 换行
            return msg["content"]

        # 执行工具调用
        print()  # 换行
        for tc in msg["tool_calls"]:
            name = tc["function"]["name"]
            args = json.loads(tc["function"]["arguments"])
            result = tool_map[name](**args)
            print(f"  [工具] {name}({args}) -> {result}")
            messages.append(
                {"role": "tool", "tool_call_id": tc["id"], "content": result}
            )

    return "[达到最大迭代次数]"


if __name__ == "__main__":
    print("=" * 50)
    print("  流式Agent Loop 演示")
    print("=" * 50)

    # 场景1: 需要工具调用，最终流式输出回答
    print("\n--- 场景1: 查询天气 ---")
    streaming_agent_loop("北京今天天气怎么样?")

    # 场景2: 多城市查询
    print("\n--- 场景2: 多城市查询 ---")
    streaming_agent_loop("帮我看看北京和武汉的天气，哪个更适合出行?")

    # 场景3: 不需要工具，直接流式回答
    print("\n--- 场景3: 直接回答 ---")
    streaming_agent_loop("你好，你能做什么?")
