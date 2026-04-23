"""
第12章 - 示例01: Agent基本定义与Runner执行

演示 OpenAI Agents SDK 的核心概念:
- Agent: 封装模型 + 指令 + 工具
- Runner: 执行Agent循环
- function_tool: 将Python函数注册为工具
- OpenAIChatCompletionsModel: 接入任意OpenAI兼容API
"""

import asyncio
import os

from dotenv import load_dotenv
from openai import AsyncOpenAI

from agents import (
    Agent,
    Runner,
    function_tool,
    OpenAIChatCompletionsModel,
    set_tracing_export_api_key,
)
from agents.tracing import set_trace_processors

load_dotenv()

# --- 模型配置 ---
# 使用 OpenAIChatCompletionsModel 接入任意 OpenAI 兼容的 API

client = AsyncOpenAI(
    base_url=os.getenv("OPENAI_BASE_URL"),
    api_key=os.getenv("OPENAI_API_KEY"),
)
model = OpenAIChatCompletionsModel(
    model=os.getenv("OPENAI_MODEL_NAME", "gpt-4o-mini"),
    openai_client=client,
)

# --- Tracing配置 ---
tracing_key = os.getenv("OPENAI_TRACING_API_KEY")
if tracing_key:
    set_tracing_export_api_key(tracing_key)
else:
    set_trace_processors([])


# --- 工具定义 ---

@function_tool
def get_population(country: str) -> str:
    """查询一个国家的大致人口数量。"""
    data = {
        "中国": "约14.1亿",
        "印度": "约14.4亿",
        "美国": "约3.3亿",
        "法国": "约6,910万",
    }
    return data.get(country, f"暂无{country}的人口数据。")


@function_tool
def get_capital(country: str) -> str:
    """查询一个国家的首都。"""
    data = {
        "中国": "北京",
        "印度": "新德里",
        "美国": "华盛顿",
        "法国": "巴黎",
    }
    return data.get(country, f"暂无{country}的首都数据。")


# --- Agent定义 ---

geography_agent = Agent(
    name="geography_expert",
    instructions=(
        "你是一位地理知识专家。当用户询问国家相关信息时，"
        "请使用可用的工具查询准确数据，然后用中文简洁地回答。"
    ),
    tools=[get_population, get_capital],
    model=model,
)


# --- 运行 ---

async def main():
    result = await Runner.run(
        geography_agent,
        input="请告诉我法国的首都和人口。",
    )
    print("Agent输出:")
    print(result.final_output)


if __name__ == "__main__":
    asyncio.run(main())
