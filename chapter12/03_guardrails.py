"""
第12章 - 示例03: 护栏机制(输入护栏与输出护栏)

演示 OpenAI Agents SDK 的 Guardrail 机制:
- InputGuardrail: 在Agent处理之前校验用户输入
- OutputGuardrail: 在返回给用户之前校验Agent输出
- tripwire_triggered: 当安全规则被触发时终止执行
"""

import asyncio
import os

from dotenv import load_dotenv
from openai import AsyncOpenAI

from agents import (
    Agent,
    GuardrailFunctionOutput,
    InputGuardrailTripwireTriggered,
    OutputGuardrailTripwireTriggered,
    Runner,
    RunContextWrapper,
    input_guardrail,
    output_guardrail,
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


# =========================================================
# 第一部分: 输入护栏 - 拦截与编程无关的问题
# =========================================================

topic_checker = Agent(
    name="topic_checker",
    instructions=(
        "判断用户的消息是否与编程或计算机科学相关。"
        "只回复一个词: YES 表示相关, NO 表示不相关。"
    ),
    model=model,
)


@input_guardrail
async def block_off_topic(
    ctx: RunContextWrapper, agent: Agent, input: str
) -> GuardrailFunctionOutput:
    """拦截与编程无关的输入。"""
    result = await Runner.run(topic_checker, input, context=ctx.context)
    is_off_topic = "NO" in result.final_output.upper().strip()
    return GuardrailFunctionOutput(
        output_info={"decision": result.final_output},
        tripwire_triggered=is_off_topic,
    )


# =========================================================
# 第二部分: 输出护栏 - 阻止回复中出现代码
# =========================================================

@output_guardrail
async def block_code_output(
    ctx: RunContextWrapper, agent: Agent, output: str
) -> GuardrailFunctionOutput:
    """阻止包含代码片段的回复。"""
    # 简单的启发式检测: 检查常见的代码标记
    code_markers = ["```", "def ", "import ", "class ", "function ", "var ", "const "]
    contains_code = any(marker in output for marker in code_markers)
    return GuardrailFunctionOutput(
        output_info={"contains_code": contains_code},
        tripwire_triggered=contains_code,
    )


# =========================================================
# 带双重护栏的主Agent
# =========================================================

cs_tutor = Agent(
    name="cs_tutor",
    instructions=(
        "你是一位计算机科学辅导老师。"
        "请用通俗易懂的语言解释概念，不要编写任何代码。"
        "用类比和文字描述的示意图代替代码演示。"
        "绝对不要使用代码块或编写实际代码。"
        "用中文回答。"
    ),
    input_guardrails=[block_off_topic],
    output_guardrails=[block_code_output],
    model=model,
)


# --- 运行 ---

async def main():
    # 测试1: 相关问题 -> 应通过输入护栏
    print("=== 测试1: 编程相关问题 ===")
    try:
        result = await Runner.run(
            cs_tutor,
            input="什么是哈希表？它是如何工作的？",
        )
        print(result.final_output)
    except InputGuardrailTripwireTriggered:
        print("[已拦截] 输入护栏触发: 问题与编程无关。")
    except OutputGuardrailTripwireTriggered:
        print("[已拦截] 输出护栏触发: 回复中包含了代码。")

    # 测试2: 无关问题 -> 应触发输入护栏
    print("\n=== 测试2: 无关问题 ===")
    try:
        result = await Runner.run(
            cs_tutor,
            input="巧克力蛋糕怎么做最好吃？",
        )
        print(result.final_output)
    except InputGuardrailTripwireTriggered:
        print("[已拦截] 输入护栏触发: 问题与编程无关。")
    except OutputGuardrailTripwireTriggered:
        print("[已拦截] 输出护栏触发: 回复中包含了代码。")


if __name__ == "__main__":
    asyncio.run(main())
