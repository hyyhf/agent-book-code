"""
第12章 - 示例02: Handoff接力机制

演示 OpenAI Agents SDK 的 Agent 接力(Handoff)机制:
- 定义多个专业化Agent
- 使用 handoffs 在Agent之间转移控制权
- Runner 自动管理接力执行过程
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
def search_flights(departure: str, destination: str, date: str) -> str:
    """根据出发地、目的地和日期搜索可用航班。"""
    return (
        f"找到2个从{departure}到{destination}的航班({date}): "
        f"1) CA101 08:00-11:30, 票价1280元; "
        f"2) MU502 14:00-17:20, 票价960元。"
    )


@function_tool
def search_hotels(city: str, checkin: str, nights: int) -> str:
    """在指定城市搜索可用酒店。"""
    return (
        f"在{city}找到2家酒店(入住{checkin}, 共{nights}晚): "
        f"1) 假日酒店, 450元/晚; "
        f"2) 希尔顿酒店, 880元/晚。"
    )


# --- 专业Agent定义 ---

flight_agent = Agent(
    name="flight_booking_agent",
    handoff_description="处理航班搜索和预订请求。",
    instructions=(
        "你是一位航班预订专员。"
        "使用 search_flights 工具为用户查找航班信息，"
        "并用中文清晰地展示搜索结果。"
    ),
    tools=[search_flights],
    model=model,
)

hotel_agent = Agent(
    name="hotel_booking_agent",
    handoff_description="处理酒店搜索和预订请求。",
    instructions=(
        "你是一位酒店预订专员。"
        "使用 search_hotels 工具为用户查找酒店信息，"
        "并用中文清晰地展示搜索结果。"
    ),
    tools=[search_hotels],
    model=model,
)

# --- 分诊Agent(带Handoff) ---

triage_agent = Agent(
    name="travel_assistant",
    instructions=(
        "你是一位旅行规划助手。"
        "根据用户的需求，将请求转接给对应的专员: "
        "机票相关的问题转接给 flight_booking_agent，"
        "酒店相关的问题转接给 hotel_booking_agent。"
        "如果用户同时需要机票和酒店，先处理机票再处理酒店。"
        "用中文回复。"
    ),
    handoffs=[flight_agent, hotel_agent],
    model=model,
)


# --- 运行 ---

async def main():
    # 测试1: 机票查询 -> 应转接给 flight_agent
    print("=== 测试1: 机票查询 ===")
    result = await Runner.run(
        triage_agent,
        input="我想订7月15日从北京到上海的机票。",
    )
    print(result.final_output)

    # 测试2: 酒店查询 -> 应转接给 hotel_agent
    print("\n=== 测试2: 酒店查询 ===")
    result = await Runner.run(
        triage_agent,
        input="我需要在上海订一家酒店，7月15日入住，住3晚。",
    )
    print(result.final_output)


if __name__ == "__main__":
    asyncio.run(main())
