"""
3.3 - Prompt Engineering -> Context Engineering -> Harness Engineering
三种工程方法的递进演示:
- Level 1 (Prompt Engineering):   精心设计提示词, 但模型看不到真实代码
- Level 2 (Context Engineering):  将真实代码注入到上下文, 模型能基于事实分析
- Level 3 (Harness Engineering):  工具 + Agent Loop + 自主读取、分析、修复

运行方式:
    uv run python chapter03/prompt_context_harness.py
"""
import json
import os
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv(Path(__file__).resolve().parent.parent / ".env")
client = OpenAI()
MODEL = os.getenv("OPENAI_MODEL_NAME", "deepseek-chat")


# ============================================================
#  准备测试数据: 一个有bug的Python文件
# ============================================================

SAMPLE_CODE = """\
def add(a, b):
    return a + b

def subtract(a, b):
    return a - b

def multiply(a, b):
    return a * b

def divide(a, b):
    return a / b
"""

SAMPLE_DIR = Path(__file__).resolve().parent / "_demo_workspace"
SAMPLE_FILE = SAMPLE_DIR / "math_utils.py"
REVIEWED_FILE = SAMPLE_DIR / "math_utils_reviewed.py"

TASK = "审查这段Python数学工具代码, 找出bug并给出修复方案"


def setup():
    """创建测试用的示例文件"""
    SAMPLE_DIR.mkdir(exist_ok=True)
    SAMPLE_FILE.write_text(SAMPLE_CODE, encoding="utf-8")
    print(f"  [准备] 已创建示例文件: {SAMPLE_FILE}")


def cleanup():
    """清理测试文件"""
    import shutil
    if SAMPLE_DIR.exists():
        shutil.rmtree(SAMPLE_DIR)
    print(f"  [清理] 已删除临时工作区: {SAMPLE_DIR}")


# ============================================================
#  Level 1: Prompt Engineering
#  只靠精心设计的提示词, 模型无法看到真实代码
# ============================================================

def level1_prompt_engineering():
    """
    Prompt Engineering: 精心设计提示词, 但不提供真实代码。
    模型只能根据函数名称猜测可能的bug。
    """
    print("=" * 60)
    print("  Level 1: Prompt Engineering")
    print("  特点: 精心设计提示词, 但不提供真实代码")
    print("=" * 60)

    prompt = f"""\
你是一位资深Python代码审查专家。
请审查一个名为math_utils.py的文件, \
其中包含四个数学运算函数: add, subtract, multiply, divide。
找出其中可能存在的bug并给出修复建议。
请特别关注边界情况和异常处理。"""

    response = client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": prompt}],
    )
    print(response.choices[0].message.content)
    print()


# ============================================================
#  Level 2: Context Engineering
#  将代码内容直接注入到上下文, 模型能基于真实代码分析
# ============================================================

def level2_context_engineering():
    """
    Context Engineering: 将真实代码内容注入到模型的上下文。
    模型能看到真正的代码, 分析更准确。
    但它仍然只能输出文本建议, 无法直接修改文件。
    """
    print("=" * 60)
    print("  Level 2: Context Engineering")
    print("  特点: 将真实代码内容注入上下文")
    print("=" * 60)

    code_content = SAMPLE_FILE.read_text(encoding="utf-8")

    prompt = f"""\
请审查以下代码, 找出其中的bug并给出修复建议。

文件: math_utils.py
```python
{code_content}```

请特别关注边界情况和异常处理。对于每个发现的问题, 给出具体的修复代码。"""

    response = client.chat.completions.create(
        model=MODEL,
        messages=[
            {
                "role": "system",
                "content": "你是一个代码审查助手, 擅长发现bug和安全隐患。",
            },
            {"role": "user", "content": prompt},
        ],
    )
    print(response.choices[0].message.content)
    print()


# ============================================================
#  Level 3: Harness Engineering
#  完整的工具 + Agent Loop + 自主读取、分析、修复
# ============================================================

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "读取指定文件的完整内容",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "文件路径"}
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "将内容写入指定文件, 如果文件不存在则创建(包括必要的父目录)",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "文件路径"},
                    "content": {"type": "string", "description": "文件内容"},
                },
                "required": ["path", "content"],
            },
        },
    },
]


def read_file(path: str) -> str:
    """读取文件内容"""
    try:
        return Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        return f"错误: 文件 '{path}' 不存在"
    except Exception as e:
        return f"读取出错: {e}"


def write_file(path: str, content: str) -> str:
    """写入文件内容"""
    try:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return f"文件已写入: {path} ({len(content)} 字符)"
    except Exception as e:
        return f"写入出错: {e}"


TOOL_MAP = {"read_file": read_file, "write_file": write_file}


def level3_harness_engineering():
    """
    Harness Engineering: 工具 + Agent Loop + 自主执行。
    模型不仅能看到代码, 还能自己读取文件、分析bug、写入修复后的版本。
    整个过程完全自主, 无需人工拷贝粘贴。
    """
    print("=" * 60)
    print("  Level 3: Harness Engineering")
    print("  特点: 工具 + Agent Loop + 自主读取、分析、修复")
    print("=" * 60)

    system = f"""\
你是一个代码审查助手。你可以:
1. 使用read_file读取源代码文件
2. 分析代码中的bug和安全隐患
3. 使用write_file写入修复后的版本

工作流程:
- 先读取原始文件
- 仔细分析代码中的bug
- 将修复后的完整代码写入新文件(原文件名加_reviewed后缀)
- 总结发现的问题和修复内容"""

    file_path = str(SAMPLE_FILE)
    messages = [
        {"role": "system", "content": system},
        {
            "role": "user",
            "content": f"请审查 {file_path} 中的代码, 找出bug并创建修复后的版本。"
                       f"修复后的文件请保存为 {REVIEWED_FILE}",
        },
    ]

    for iteration in range(1, 11):
        response = client.chat.completions.create(
            model=MODEL, messages=messages, tools=TOOLS
        )
        msg = response.choices[0].message
        messages.append(msg)

        if not msg.tool_calls:
            print(f"\n[最终回复]\n{msg.content}")
            break

        for tc in msg.tool_calls:
            name = tc.function.name
            args = json.loads(tc.function.arguments)

            if name == "read_file":
                print(f"  [读取] {args['path']}")
            elif name == "write_file":
                print(f"  [写入] {args['path']} ({len(args.get('content', ''))} 字符)")

            try:
                result = TOOL_MAP[name](**args)
            except Exception as e:
                result = f"错误: {e}"

            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": result,
            })

    # 验证结果: 检查是否生成了修复文件
    print()
    if REVIEWED_FILE.exists():
        content = REVIEWED_FILE.read_text(encoding="utf-8")
        print(f"  [验证通过] 修复后的文件已生成: {REVIEWED_FILE.name}")
        print(f"  [文件内容]")
        print(content)
    else:
        print(f"  [验证失败] 修复后的文件未生成")
    print()


# ============================================================
#  主程序: 三个Level依次执行
# ============================================================

if __name__ == "__main__":
    print()
    print("Prompt Engineering -> Context Engineering -> Harness Engineering")
    print("三种工程方法对同一任务的处理对比")
    print()
    print(f"任务: {TASK}")
    print(f"目标文件: {SAMPLE_FILE.name}")
    print()

    setup()
    print()

    try:
        level1_prompt_engineering()
        print("-" * 60)
        print()

        level2_context_engineering()
        print("-" * 60)
        print()

        level3_harness_engineering()
    finally:
        cleanup()

    print("=" * 60)
    print("对比总结:")
    print("  Level 1 (Prompt):   模型猜测代码内容, 给出泛泛的建议")
    print("  Level 2 (Context):  模型看到真实代码, 分析更精准, 但只能给出文字建议")
    print("  Level 3 (Harness):  模型自主读取、分析、修复, 直接生成修复后的文件")
    print()
    print("每一级都包含上一级的能力, 同时增加新的维度:")
    print("  Prompt Eng.   = 提示词设计")
    print("  Context Eng.  = 提示词设计 + 上下文注入")
    print("  Harness Eng.  = 提示词设计 + 上下文注入 + 工具 + 控制流 + 安全")
    print("=" * 60)
