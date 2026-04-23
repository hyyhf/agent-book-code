"""
第12章 - 示例04: Tracing与可观测性

演示 OpenAI Agents SDK 的追踪系统:
- Trace数据会上传到 OpenAI Traces 仪表盘 (https://platform.openai.com/traces)
- 使用非OpenAI模型时, 通过 set_tracing_export_api_key() 设置
  独立的OpenAI API密钥用于追踪上传(追踪功能免费)
- 使用 trace() 上下文管理器来包裹工作流并命名
- 使用 add_trace_processor() 在默认的OpenAI导出器旁边挂载额外的处理器
- 使用 custom_span() 追踪自定义业务逻辑

Trace层级结构:
  Trace (工作流级别)
    -> AgentSpan
      -> TurnSpan
        -> GenerationSpan (LLM调用)
        -> FunctionSpan (工具调用)
      -> TurnSpan ...
    -> AgentSpan ...
"""

import asyncio
import os
from datetime import datetime

from dotenv import load_dotenv
from openai import AsyncOpenAI

from agents import (
    Agent,
    Runner,
    RunConfig,
    function_tool,
    trace,
    OpenAIChatCompletionsModel,
    set_tracing_export_api_key,
)
from agents.tracing import (
    TracingProcessor,
    add_trace_processor,
    set_trace_processors,
    custom_span,
    Trace as TraceDef,
    Span,
)

load_dotenv()

# =========================================================
# 模型配置 (DeepSeek)
# =========================================================

client = AsyncOpenAI(
    base_url=os.getenv("OPENAI_BASE_URL"),
    api_key=os.getenv("OPENAI_API_KEY"),
)
model = OpenAIChatCompletionsModel(
    model=os.getenv("OPENAI_MODEL_NAME", "gpt-4o-mini"),
    openai_client=client,
)

# =========================================================
# 自定义控制台追踪处理器
# =========================================================

class ConsoleTracingProcessor(TracingProcessor):
    """将追踪事件打印到控制台, 用于本地调试。"""

    def on_trace_start(self, trace: TraceDef) -> None:
        print(f"\n  [追踪开始] {trace.name} (id={trace.trace_id})")

    def on_trace_end(self, trace: TraceDef) -> None:
        print(f"  [追踪结束] {trace.name} (id={trace.trace_id})")

    def on_span_start(self, span: Span) -> None:
        kind = span.span_data.__class__.__name__.replace("SpanData", "")
        print(f"    [Span >>] {kind}")

    def on_span_end(self, span: Span) -> None:
        kind = span.span_data.__class__.__name__.replace("SpanData", "")
        elapsed = ""
        if span.started_at and span.ended_at:
            t0 = datetime.fromisoformat(span.started_at)
            t1 = datetime.fromisoformat(span.ended_at)
            elapsed = f" ({(t1 - t0).total_seconds():.2f}s)"
        print(f"    [Span <<] {kind}{elapsed}")

    def shutdown(self) -> None:
        pass

    def force_flush(self) -> None:
        pass


# =========================================================
# Tracing配置
# =========================================================
#
# 使用非OpenAI模型(DeepSeek, Ollama等)时,
# 追踪数据仍然可以免费上传到 OpenAI Traces 仪表盘。
# 只需要一个独立的 OpenAI API 密钥用于上传。
#
# 配置步骤:
# 1. 从 https://platform.openai.com/api-keys 获取API密钥
# 2. 在 .env 中设置 OPENAI_TRACING_API_KEY
# 3. 调用 set_tracing_export_api_key() 配置该密钥

tracing_key = os.getenv("OPENAI_TRACING_API_KEY")
if tracing_key:
    # 有OpenAI密钥 -> 启用仪表盘上传 + 同时添加控制台日志
    set_tracing_export_api_key(tracing_key)
    add_trace_processor(ConsoleTracingProcessor())
    print("[追踪] OpenAI Traces 仪表盘已启用。")
    print("[追踪] 查看追踪: https://platform.openai.com/traces")
else:
    # 无OpenAI密钥 -> 仅使用控制台处理器, 避免401错误
    set_trace_processors([ConsoleTracingProcessor()])
    print("[追踪] 未设置 OPENAI_TRACING_API_KEY, 仅使用控制台追踪。")
    print("[追踪] 在 .env 中设置 OPENAI_TRACING_API_KEY 可上传到仪表盘。")


# =========================================================
# 带工具的Agent
# =========================================================

@function_tool
def calculate(expression: str) -> str:
    """计算一个数学表达式并返回结果。"""
    try:
        result = eval(expression, {"__builtins__": {}})
        return str(result)
    except Exception as e:
        return f"计算错误: {e}"


@function_tool
def get_exchange_rate(currency: str) -> str:
    """查询指定货币对人民币的汇率。"""
    rates = {"USD": 7.24, "EUR": 8.12, "JPY": 0.048, "GBP": 9.35}
    rate = rates.get(currency.upper())
    if rate:
        return f"1 {currency.upper()} = {rate} CNY"
    return f"暂无{currency}的汇率数据。"


agent = Agent(
    name="finance_calculator",
    instructions=(
        "你是一位金融计算助手。"
        "使用工具查询汇率并进行计算。"
        "用中文简洁地回答。"
    ),
    tools=[calculate, get_exchange_rate],
    model=model,
)


# =========================================================
# 带追踪的运行
# =========================================================

async def main():
    print("=== 追踪演示 ===")

    # 使用 trace() 上下文管理器命名工作流。
    # 内部的多次 Runner.run() 调用会共享同一个追踪。
    with trace("汇率换算工作流"):
        # 第一步: 美元转人民币
        result1 = await Runner.run(
            agent,
            input="500美元等于多少人民币？",
        )
        print(f"\n  结果1: {result1.final_output}")

        # 自定义Span: 在追踪中记录业务逻辑
        with custom_span("用户满意度检查"):
            satisfied = len(result1.final_output) > 10
            print(f"  满意度检查: {'通过' if satisfied else '未通过'}")

        # 第二步: 欧元转人民币（同一个追踪内）
        result2 = await Runner.run(
            agent,
            input="1000欧元等于多少人民币？",
        )
        print(f"\n  结果2: {result2.final_output}")

    if tracing_key:
        print("\n[完成] 查看追踪: https://platform.openai.com/traces")
    else:
        print("\n[完成] 在 .env 中设置 OPENAI_TRACING_API_KEY 可在仪表盘中查看追踪。")


if __name__ == "__main__":
    asyncio.run(main())
