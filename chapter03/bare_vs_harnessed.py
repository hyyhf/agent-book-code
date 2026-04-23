"""
3.1 - 裸模型 vs Harness加持的模型
演示同一个任务在两种方式下的差异:
- 裸调用: 直接向模型提问, 模型只能生成文本, 无法与真实世界交互
- Harness加持: 配备System Prompt、工具系统和Agent Loop, 模型能自主完成任务

运行方式:
    uv run python chapter03/bare_vs_harnessed.py
"""
import json
import os
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv(Path(__file__).resolve().parent.parent / ".env")
client = OpenAI()
MODEL = os.getenv("OPENAI_MODEL_NAME", "deepseek-chat")

TASK = "请读取当前目录下的pyproject.toml文件, 告诉我这个项目的名称和依赖包有哪些"


# ============================================================
#  方式一: 裸调用 -- 没有工具, 没有Agent Loop
# ============================================================

def bare_call(task: str) -> str:
    """直接向模型提问, 不提供任何工具。
    模型无法访问文件系统, 只能根据训练数据猜测。
    """
    print("=" * 60)
    print("  方式一: 裸调用 (无工具, 无Harness)")
    print("=" * 60)

    response = client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": task}],
    )
    answer = response.choices[0].message.content
    print(answer)
    print()
    return answer


# ============================================================
#  方式二: Harness加持 -- System Prompt + 工具 + Agent Loop
# ============================================================

SYSTEM_PROMPT = """你是一个文件分析助手。你可以使用read_file工具来读取文件内容。
请先读取文件, 然后基于真实内容回答用户的问题。"""

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "读取指定文件的完整内容",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "要读取的文件路径",
                    }
                },
                "required": ["path"],
            },
        },
    },
]


def read_file(path: str) -> str:
    """读取文件内容, 带错误处理"""
    try:
        return Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        return f"错误: 文件 '{path}' 不存在"
    except Exception as e:
        return f"读取文件时出错: {e}"


TOOL_MAP = {"read_file": read_file}


def harnessed_call(task: str) -> str:
    """配备了Harness的Agent调用:
    - System Prompt定义身份和行为
    - 工具系统提供文件读取能力
    - Agent Loop驱动多轮交互
    """
    print("=" * 60)
    print("  方式二: Harness加持 (System Prompt + 工具 + Agent Loop)")
    print("=" * 60)

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": task},
    ]

    max_iterations = 5
    for i in range(1, max_iterations + 1):
        response = client.chat.completions.create(
            model=MODEL, messages=messages, tools=TOOLS
        )
        msg = response.choices[0].message
        messages.append(msg)

        # 模型返回纯文本, 任务完成
        if not msg.tool_calls:
            print(msg.content)
            print()
            return msg.content

        # 执行工具调用
        for tc in msg.tool_calls:
            name = tc.function.name
            args = json.loads(tc.function.arguments)
            print(f"  [调用工具] {name}({json.dumps(args, ensure_ascii=False)})")
            result = TOOL_MAP[name](**args)
            display = result if len(result) <= 120 else result[:120] + "..."
            print(f"  [工具返回] {display}")
            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": result,
            })

    return "[达到最大迭代次数]"


# ============================================================
#  主程序: 对比两种方式
# ============================================================

if __name__ == "__main__":
    print()
    print("同一个任务, 两种方式的对比")
    print(f"任务: {TASK}")
    print()

    print("[1/2] 裸调用...")
    print()
    bare_call(TASK)

    print("-" * 60)
    print()

    print("[2/2] Harness加持...")
    print()
    harnessed_call(TASK)

    print("=" * 60)
    print("对比总结:")
    print("  裸调用  -> 模型无法访问文件, 只能猜测, 容易产生幻觉")
    print("  Harness -> 模型通过工具读取真实文件, 回答基于事实")
    print("=" * 60)
