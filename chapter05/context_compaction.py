"""
5.3 - 上下文压缩

实现三个上下文管理机制:
  - Token 计数与成本追踪
  - 上下文窗口溢出的应对策略
  - 对话历史摘要与 Compaction 机制(调用模型生成摘要)

运行方式:
    uv run python chapter05/context_compaction.py
"""
import json
import os
import time
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

client = OpenAI()
MODEL = os.getenv("OPENAI_MODEL_NAME", "deepseek-v4-flash")

# 上下文窗口的软上限(字符数)。到达此阈值触发压缩。
# 真实场景中会用 tiktoken 精确计数; 这里用字符数近似, 比例约为 1 token ~ 4 字符。
CONTEXT_SOFT_LIMIT = 80000  # 约 20000 tokens
# 每个工具结果的最大保留长度
TOOL_RESULT_MAX_CHARS = 3000
# 保留最近 N 条工具结果不压缩(它们可能还有用)
KEEP_RECENT_TOOL_RESULTS = 3


# =============================================================
#  5.3.1 Token 计数与成本追踪
# =============================================================

def estimate_tokens(messages: list[dict]) -> int:
    """估算消息列表的 Token 数量。

    使用字符数 / 4 的粗略估算。生产环境应使用
    tiktoken 库或 API 返回的 usage 字段进行精确计数。
    """
    total_chars = 0
    for msg in messages:
        content = msg.get("content", "")
        if content:
            total_chars += len(str(content))
        # 工具调用的参数也占 Token
        tool_calls = msg.get("tool_calls", [])
        if tool_calls:
            total_chars += len(json.dumps(tool_calls, ensure_ascii=False))
    return total_chars // 4


class CostTracker:
    """追踪 Token 用量和估算成本。

    在每次 API 调用后更新, 累计 prompt_tokens 和 completion_tokens。
    成本估算基于 GPT-4o 的定价(可配置), 仅作参考。
    """

    def __init__(self, input_price: float = 2.5, output_price: float = 10.0):
        """
        Args:
            input_price: 每百万 input tokens 的价格(美元)
            output_price: 每百万 output tokens 的价格(美元)
        """
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.input_price_per_m = input_price
        self.output_price_per_m = output_price
        self.call_count = 0

    def update(self, usage):
        """从 API 响应的 usage 对象中提取 Token 数量。"""
        if usage is None:
            return
        self.total_input_tokens += getattr(usage, "prompt_tokens", 0) or 0
        self.total_output_tokens += getattr(usage, "completion_tokens", 0) or 0
        self.call_count += 1

    @property
    def total_tokens(self) -> int:
        return self.total_input_tokens + self.total_output_tokens

    @property
    def estimated_cost(self) -> float:
        """估算累计成本(美元)。"""
        input_cost = self.total_input_tokens * self.input_price_per_m / 1_000_000
        output_cost = self.total_output_tokens * self.output_price_per_m / 1_000_000
        return input_cost + output_cost

    def summary(self) -> str:
        return (
            f"API calls: {self.call_count} | "
            f"Tokens: {self.total_input_tokens:,} in + "
            f"{self.total_output_tokens:,} out = "
            f"{self.total_tokens:,} total | "
            f"Cost: ${self.estimated_cost:.4f}"
        )


# =============================================================
#  5.3.2 上下文窗口溢出的应对: 工具结果截断
# =============================================================

def truncate_tool_results(messages: list[dict]) -> list[dict]:
    """截断过长的工具返回结果。

    策略: 保留最近 KEEP_RECENT_TOOL_RESULTS 条工具结果完整,
    更早的结果如果超过 TOOL_RESULT_MAX_CHARS 则截断。

    这是一种无损不丢消息的轻量压缩, 每轮都可以执行。
    """
    # 收集所有 tool 消息的索引
    tool_indices = [
        i for i, msg in enumerate(messages)
        if msg.get("role") == "tool"
    ]

    if len(tool_indices) <= KEEP_RECENT_TOOL_RESULTS:
        return messages

    # 截断较早的工具结果
    old_indices = tool_indices[:-KEEP_RECENT_TOOL_RESULTS]
    for idx in old_indices:
        content = messages[idx].get("content", "")
        if len(content) > TOOL_RESULT_MAX_CHARS:
            messages[idx]["content"] = (
                content[:TOOL_RESULT_MAX_CHARS]
                + f"\n...(truncated, was {len(content)} chars)"
            )

    return messages


# =============================================================
#  5.3.3 对话历史摘要与 Compaction 机制
# =============================================================

def compact_conversation(
    messages: list[dict],
    model: str = MODEL,
) -> list[dict]:
    """压缩对话历史: 保留 system prompt, 将中间消息替换为摘要。

    流程:
    1. 提取 system prompt(第一条消息)
    2. 将中间的所有消息发送给模型, 请求生成摘要
    3. 用一条 user 消息(包含摘要)替换所有中间消息
    4. 保留最近 2 轮对话(最新的交互上下文)

    这是一个有损压缩操作, 会丢失历史细节,
    但能让对话在有限的上下文窗口内无限持续。
    """
    if len(messages) < 6:
        # 对话太短, 没必要压缩
        return messages

    system_msg = messages[0] if messages[0].get("role") == "system" else None
    conversation = messages[1:] if system_msg else messages

    # 保留最近的 4 条消息(约 2 轮对话)
    keep_recent = 4
    if len(conversation) <= keep_recent:
        return messages

    old_messages = conversation[:-keep_recent]
    recent_messages = conversation[-keep_recent:]

    # 构造摘要请求
    summary_text = _format_messages_for_summary(old_messages)

    summary_prompt = f"""\
Summarize the following conversation between a user and an AI coding assistant.
Focus on:
- What tasks were requested and completed
- What files were read, modified, or created
- Key decisions made and their reasoning
- Any errors encountered and how they were resolved
- Current state of the work

Be concise but preserve all actionable information.

Conversation:
{summary_text}"""

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "You summarize conversations concisely."},
                {"role": "user", "content": summary_prompt},
            ],
            max_tokens=1000,
        )
        summary = response.choices[0].message.content or "(summary unavailable)"
    except Exception as e:
        summary = f"(summary generation failed: {e})"

    # 重新组装消息列表
    compacted = []
    if system_msg:
        compacted.append(system_msg)
    compacted.append({
        "role": "user",
        "content": f"[Conversation Summary]\n{summary}\n\n"
                   f"(The above summarizes {len(old_messages)} earlier messages. "
                   f"Continue from here.)",
    })
    compacted.extend(recent_messages)

    return compacted


def _format_messages_for_summary(messages: list[dict]) -> str:
    """将消息列表格式化为可读的文本, 供摘要模型使用。"""
    lines = []
    for msg in messages:
        role = msg.get("role", "?")
        content = msg.get("content", "")
        if not content:
            # 工具调用消息
            tool_calls = msg.get("tool_calls", [])
            if tool_calls:
                names = [tc.get("function", {}).get("name", "?") for tc in tool_calls]
                content = f"[Called tools: {', '.join(names)}]"
            else:
                content = "[no content]"
        # 截断过长内容
        if len(content) > 500:
            content = content[:500] + "..."
        lines.append(f"[{role}] {content}")
    return "\n".join(lines)


def should_compact(messages: list[dict]) -> bool:
    """判断是否需要触发压缩。"""
    char_count = sum(
        len(str(msg.get("content", "")))
        for msg in messages
    )
    return char_count > CONTEXT_SOFT_LIMIT


# =============================================================
#  演示
# =============================================================

if __name__ == "__main__":
    print("=== 上下文压缩演示 ===\n")

    # 1. Token 估算
    print("--- Token 估算 ---")
    sample_messages = [
        {"role": "system", "content": "You are a helpful assistant." * 10},
        {"role": "user", "content": "Read the file pyproject.toml"},
        {"role": "assistant", "content": None,
         "tool_calls": [{"id": "tc1", "type": "function",
                        "function": {"name": "read_file", "arguments": '{"path":"pyproject.toml"}'}}]},
        {"role": "tool", "tool_call_id": "tc1",
         "content": "[project]\nname = 'funharness'\n" + "x" * 5000},
        {"role": "assistant", "content": "The pyproject.toml contains..." + "y" * 1000},
        {"role": "user", "content": "Now search for all TODO comments"},
    ]
    tokens = estimate_tokens(sample_messages)
    print(f"  {len(sample_messages)} messages, ~{tokens} tokens\n")

    # 2. 成本追踪
    print("--- 成本追踪 ---")
    tracker = CostTracker()

    class MockUsage:
        def __init__(self, pt, ct):
            self.prompt_tokens = pt
            self.completion_tokens = ct

    tracker.update(MockUsage(1500, 300))
    tracker.update(MockUsage(2000, 500))
    print(f"  {tracker.summary()}\n")

    # 3. 工具结果截断
    print("--- 工具结果截断 ---")
    before_chars = sum(len(str(m.get("content", ""))) for m in sample_messages)
    truncated = truncate_tool_results(sample_messages)
    after_chars = sum(len(str(m.get("content", ""))) for m in truncated)
    print(f"  Before: {before_chars} chars")
    print(f"  After:  {after_chars} chars")
    print(f"  Saved:  {before_chars - after_chars} chars\n")

    # 4. 压缩判断
    print("--- 压缩判断 ---")
    print(f"  Current context: {before_chars} chars")
    print(f"  Soft limit: {CONTEXT_SOFT_LIMIT} chars")
    print(f"  Should compact: {should_compact(sample_messages)}")
    print()

    # 5. 完整压缩流程(需要 API 调用)
    print("--- Compaction (needs API) ---")
    big_conversation = [
        {"role": "system", "content": "You are FunHarness."},
    ]
    for i in range(10):
        big_conversation.append({"role": "user", "content": f"Task {i}: do something with file{i}.py"})
        big_conversation.append({"role": "assistant", "content": f"I'll work on file{i}.py. Done with task {i}."})

    print(f"  Before: {len(big_conversation)} messages")
    compacted = compact_conversation(big_conversation)
    print(f"  After:  {len(compacted)} messages")
    # 打印摘要内容
    for msg in compacted:
        if "[Conversation Summary]" in str(msg.get("content", "")):
            summary = msg["content"][:200]
            print(f"  Summary preview: {summary}...")
