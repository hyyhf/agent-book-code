"""
4.1 - 工具设计原则
对比"坏工具"与"好工具"的设计差异,
展示原子性、自描述性如何影响模型的工具选择行为。

运行方式:
    uv run python chapter04/tool_design_principles.py
"""
import json
import os
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv(Path(__file__).resolve().parent.parent / ".env")
client = OpenAI()
MODEL = os.getenv("OPENAI_MODEL_NAME", "deepseek-chat")

# =============================================================
#  反面教材: 模糊描述 + 大而全的单一工具
#  模型拿到这样的工具定义, 就像面对一份只写了"处理事务"的岗位说明,
#  它不确定该传什么参数, 只能靠猜。
# =============================================================

bad_tools = [
    {
        "type": "function",
        "function": {
            "name": "file_manager",
            "description": "处理文件",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "description": "操作"},
                    "path": {"type": "string", "description": "路径"},
                    "data": {"type": "string", "description": "数据"},
                },
                "required": ["action", "path"],
            },
        },
    },
]

# =============================================================
#  正面示范: 精确描述 + 原子化拆分
#  每个工具只做一件事, 名字就是动词+名词, 参数含义一目了然。
#  模型不需要猜测, 只需匹配意图。
# =============================================================

good_tools = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": (
                "读取指定文件的完整文本内容并返回。"
                "如果文件不存在, 返回明确的错误信息。"
                "适用于查看代码、配置文件、文档等文本文件。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "要读取的文件路径(相对路径或绝对路径)",
                    }
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": (
                "将文本内容写入指定文件。"
                "如果文件已存在则覆盖其内容, "
                "如果文件不存在则自动创建(包括必要的父目录)。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "要写入的目标文件路径",
                    },
                    "content": {
                        "type": "string",
                        "description": "要写入文件的完整文本内容",
                    },
                },
                "required": ["path", "content"],
            },
        },
    },
]


def test_tool_selection(tools, label, task):
    """用同一个任务测试不同工具集下模型的选择行为"""
    response = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": "你是一个编程助手。"},
            {"role": "user", "content": task},
        ],
        tools=tools,
    )
    msg = response.choices[0].message

    print(f"\n{'=' * 50}")
    print(f"  {label}")
    print(f"{'=' * 50}")
    print(f"  任务: {task}")

    if msg.tool_calls:
        for tc in msg.tool_calls:
            print(f"  模型选择: {tc.function.name}")
            args = json.loads(tc.function.arguments)
            print(f"  参数: {json.dumps(args, ensure_ascii=False)}")
    else:
        preview = (msg.content or "")[:80]
        print(f"  模型未调用工具, 直接回复: {preview}")


if __name__ == "__main__":
    print("4.1 工具设计原则: 原子性与自描述性\n")

    task = "帮我看一下 pyproject.toml 文件里写了什么"

    # 对比测试: 相同任务, 不同工具定义
    test_tool_selection(bad_tools, "反面: 模糊描述 + 大而全工具", task)
    test_tool_selection(good_tools, "正面: 精确描述 + 原子化工具", task)

    print("\n" + "=" * 50)
    print("  观察要点:")
    print("  - 好的工具描述让模型的参数填写更准确")
    print("  - 原子化工具让模型的意图匹配更直接")
    print("  - 工具名称本身就是最重要的描述信息")
    print("=" * 50)
