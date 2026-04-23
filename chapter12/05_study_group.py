"""
第12章 - 示例05: 智能学习小组(综合实战项目)

一个完整的多Agent应用, 使用 OpenAI Agents SDK:
- 导师Agent: 管理学习流程, 讲解概念
- 出题官Agent: 就当前主题生成测试题
- 答疑师Agent: 回答深入问题, 提供详细解释

综合演示:
- 多Agent Handoff 实现自然的对话流转
- InputGuardrail 保持讨论不跑题
- trace() 上下文管理器将整个学习会话归入一个追踪
- function_tool 实现知识库检索
"""

import asyncio
import os

from dotenv import load_dotenv
from openai import AsyncOpenAI

from agents import (
    Agent,
    Runner,
    GuardrailFunctionOutput,
    InputGuardrailTripwireTriggered,
    RunContextWrapper,
    function_tool,
    input_guardrail,
    trace,
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
# 工具: 知识库
# =========================================================

KNOWLEDGE_BASE = {
    "hash_table": (
        "哈希表是一种通过哈希函数将键映射到值的数据结构。"
        "哈希函数将键转换为数组索引。"
        "冲突解决策略包括链地址法(每个桶挂一个链表)"
        "和开放地址法(探测下一个空位)。"
        "平均时间复杂度: 插入、查找、删除均为O(1)。"
        "最坏情况: 所有键冲突时退化为O(n)。"
    ),
    "binary_tree": (
        "二叉树是每个节点最多有两个子节点的树形数据结构。"
        "二叉搜索树(BST)维护有序性: 左子节点 < 父节点 < 右子节点。"
        "操作复杂度: 查找平均O(log n), 最坏O(n)。"
        "平衡变体: AVL树、红黑树保证O(log n)的树高。"
    ),
    "sorting": (
        "常见排序算法: "
        "冒泡排序O(n^2), 简单但低效。"
        "快速排序平均O(n log n), 原地排序, 采用分治策略。"
        "归并排序O(n log n)稳定保证, 但需要额外空间。"
        "堆排序O(n log n), 原地但不稳定。"
    ),
    "graph": (
        "图由顶点(节点)和边(连接)组成。"
        "表示方式: 邻接矩阵(O(V^2)空间)和邻接表(O(V+E)空间)。"
        "关键算法: BFS(广度优先搜索)用于无权图最短路径, "
        "DFS(深度优先搜索)用于拓扑排序和环检测, "
        "Dijkstra算法用于带权最短路径。"
    ),
}


@function_tool
def lookup_topic(topic: str) -> str:
    """查询计算机科学知识库。可用主题: hash_table, binary_tree, sorting, graph。"""
    content = KNOWLEDGE_BASE.get(topic.lower().replace(" ", "_"))
    if content:
        return content
    available = ", ".join(KNOWLEDGE_BASE.keys())
    return f"未找到主题'{topic}'。可用主题: {available}"


# =========================================================
# 护栏: 限制讨论范围为计算机科学
# =========================================================

topic_filter = Agent(
    name="topic_filter",
    instructions=(
        "判断用户的消息是否与计算机科学、编程、"
        "数据结构或算法相关。"
        "只回复一个词: YES 表示相关, NO 表示不相关。"
    ),
    model=model,
)


@input_guardrail
async def cs_topic_guard(
    ctx: RunContextWrapper, agent: Agent, input: str
) -> GuardrailFunctionOutput:
    """仅允许计算机科学相关的讨论。"""
    result = await Runner.run(topic_filter, input, context=ctx.context)
    is_off_topic = "NO" in result.final_output.upper().strip()
    return GuardrailFunctionOutput(
        output_info={"decision": result.final_output},
        tripwire_triggered=is_off_topic,
    )


# =========================================================
# Agent定义
# =========================================================

# 出题官: 生成测试题
quiz_master = Agent(
    name="quiz_master",
    handoff_description=(
        "生成计算机科学主题的测试题。"
        "当学生准备好接受测试时, 转接到此Agent。"
    ),
    instructions=(
        "你是一位计算机科学出题官。"
        "被激活后, 就当前讨论的主题出2-3道选择题。"
        "在最后附上正确答案。"
        "如果需要参考资料, 使用 lookup_topic 工具。"
        "用中文出题和回答。"
    ),
    tools=[lookup_topic],
    model=model,
)

# 答疑师: 提供深入解释
qa_agent = Agent(
    name="qa_agent",
    handoff_description=(
        "提供详细解释, 回答深入的追问。"
        "当学生需要更深层理解时, 转接到此Agent。"
    ),
    instructions=(
        "你是一位耐心的计算机科学助教。"
        "提供详细的、循序渐进的解释。"
        "善用类比和具体例子帮助理解。"
        "需要时使用 lookup_topic 工具获取参考资料。"
        "用中文回答。"
    ),
    tools=[lookup_topic],
    model=model,
)

# 导师: 主协调者
tutor_agent = Agent(
    name="tutor",
    instructions=(
        "你是智能学习小组的导师。你的职责是: "
        "1. 使用 lookup_topic 工具引入和讲解计算机科学概念。"
        "2. 当学生理解了基础知识后, 转接给 quiz_master 进行测试。"
        "3. 当学生有深入的问题时, 转接给 qa_agent 进行详细解答。"
        "始终鼓励学生, 引导学习过程。"
        "可用主题: hash_table, binary_tree, sorting, graph。"
        "用中文回答。"
    ),
    tools=[lookup_topic],
    handoffs=[quiz_master, qa_agent],
    input_guardrails=[cs_topic_guard],
    model=model,
)


# =========================================================
# 运行: 一次完整的学习会话
# =========================================================

async def main():
    print("=" * 60)
    print("  智能学习小组")
    print("=" * 60)

    # 所有会话共享同一个追踪, 在仪表盘中显示为一个完整的工作流
    with trace("智能学习小组会话"):
        # 环节1: 学习哈希表
        print("\n--- 环节1: 学习哈希表 ---")
        result = await Runner.run(
            tutor_agent,
            input="我想学习哈希表, 你能教我吗？",
        )
        print(f"\n[导师]: {result.final_output}")

        # 环节2: 测试环节
        print("\n--- 环节2: 出题测试 ---")
        result = await Runner.run(
            tutor_agent,
            input="我觉得我已经理解哈希表了, 能考考我吗？",
        )
        print(f"\n[学习小组]: {result.final_output}")

        # 环节3: 深入探讨
        print("\n--- 环节3: 深入探讨 ---")
        result = await Runner.run(
            tutor_agent,
            input=(
                "我对哈希冲突还不太理解, 能详细解释一下"
                "链地址法和开放地址法的区别吗？"
            ),
        )
        print(f"\n[学习小组]: {result.final_output}")

        # 环节4: 跑题测试（应被拦截）
        print("\n--- 环节4: 跑题测试 ---")
        try:
            result = await Runner.run(
                tutor_agent,
                input="今天天气怎么样？",
            )
            print(f"\n[导师]: {result.final_output}")
        except InputGuardrailTripwireTriggered:
            print("\n[系统]: 这个问题与课程无关。学习小组只讨论计算机科学话题。")


if __name__ == "__main__":
    asyncio.run(main())
