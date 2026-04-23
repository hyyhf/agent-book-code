"""
3.2 - Agent Harness的五大核心要素
通过一个完整的示例, 逐一展示构成Agent Harness的五个核心要素:
1. System Prompt (身份与行为准则)
2. Tool System (工具能力)
3. Context Engineering (上下文注入)
4. Control Flow (控制流: 迭代上限、指数退避)
5. Guardrails (安全护栏: 路径访问控制)

运行方式:
    uv run python chapter03/harness_anatomy.py
"""
import json
import os
import time
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI, RateLimitError, APITimeoutError, APIConnectionError

load_dotenv(Path(__file__).resolve().parent.parent / ".env")
client = OpenAI()
MODEL = os.getenv("OPENAI_MODEL_NAME", "deepseek-chat")

# =============================================================
#  要素一: System Prompt -- 定义智能体的身份和行为准则
#  System Prompt是Harness的"大脑接口", 它告诉模型:
#  你是谁、该怎么做、有什么限制、工作环境是什么样的。
# =============================================================

SYSTEM_PROMPT_TEMPLATE = """\
你是FunHarness, 一个AI编程助手。

身份: 你专注于帮助用户进行文件操作和代码分析。

行为准则:
- 操作前先说明你打算做什么
- 操作后确认结果是否符合预期
- 遇到错误时解释原因并尝试修复
- 完成后给出简洁的总结

环境信息:
- 工作目录: {cwd}
- 操作系统: {os_name}
"""


# =============================================================
#  要素二: Tool System -- 定义智能体能做什么
#  工具是智能体与外部世界交互的手和脚。
#  没有工具, 模型只能生成文字; 有了工具, 它能读文件、写代码、执行命令。
# =============================================================

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "读取指定文件的完整内容。如果文件不存在会返回错误信息。",
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
            "description": "将内容写入指定文件。如果文件已存在则覆盖, 不存在则创建(包括必要的父目录)。",
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
    except PermissionError:
        return f"错误: 没有权限读取 '{path}'"
    except Exception as e:
        return f"读取文件时出错: {e}"


def write_file(path: str, content: str) -> str:
    """写入文件内容"""
    try:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return f"文件已写入: {path} ({len(content)} 字符)"
    except PermissionError:
        return f"错误: 没有权限写入 '{path}'"
    except Exception as e:
        return f"写入文件时出错: {e}"


TOOL_MAP = {"read_file": read_file, "write_file": write_file}


# =============================================================
#  要素三: Context Engineering -- 动态注入环境上下文
#  静态的System Prompt模板在运行时被填充真实的环境信息。
#  模型因此"知道"自己身处什么环境, 而非盲目猜测。
# =============================================================

def build_system_prompt() -> str:
    """动态组装System Prompt, 注入当前环境上下文"""
    return SYSTEM_PROMPT_TEMPLATE.format(
        cwd=os.getcwd(),
        os_name=os.name,
    )


# =============================================================
#  要素四: Control Flow -- Agent Loop + 重试 + 迭代上限
#  控制流决定了智能体如何运转: 何时调用模型、何时执行工具、
#  何时退出、遇到错误怎么办。
# =============================================================

MAX_ITERATIONS = 10


def call_with_retry(messages, max_retries=3):
    """带指数退避的API调用"""
    for attempt in range(max_retries):
        try:
            return client.chat.completions.create(
                model=MODEL, messages=messages, tools=TOOLS
            )
        except (RateLimitError, APITimeoutError, APIConnectionError):
            if attempt == max_retries - 1:
                raise
            wait = 2 ** attempt
            print(f"  [重试] {wait}秒后重试...")
            time.sleep(wait)


# =============================================================
#  要素五: Guardrails -- 安全护栏
#  安全护栏约束智能体"不能做什么"。
#  没有护栏的智能体就像一辆没有刹车的车: 跑得很快, 但迟早出事。
# =============================================================

BLOCKED_PATHS = [".env", ".git", "node_modules"]


def check_path_permission(path: str) -> str | None:
    """检查文件路径是否允许访问。
    返回None表示允许, 返回字符串表示拒绝原因。
    """
    for blocked in BLOCKED_PATHS:
        if blocked in path:
            return f"安全限制: 不允许访问路径 '{path}' (包含受保护的 '{blocked}')"
    return None


def execute_tool(name, arguments_json):
    """带安全护栏的工具执行"""
    # 第一层: 参数解析
    try:
        args = json.loads(arguments_json)
    except json.JSONDecodeError as e:
        return f"参数解析失败: {e}"

    # 第二层: 安全检查 (要素五)
    if "path" in args:
        denial = check_path_permission(args["path"])
        if denial:
            return denial

    # 第三层: 工具执行
    func = TOOL_MAP.get(name)
    if not func:
        return f"未知工具: {name}"

    try:
        return func(**args)
    except Exception as e:
        return f"工具执行失败 ({name}): {e}"


# =============================================================
#  五要素整合: 完整的Agent Harness
# =============================================================

def agent_loop(user_input: str):
    """
    五要素齐备的Agent Loop:
    1. System Prompt   -> 身份、准则
    2. Tool System     -> read_file, write_file
    3. Context Eng.    -> 注入工作目录、OS信息
    4. Control Flow    -> 迭代上限 + 指数退避重试
    5. Guardrails      -> 文件路径访问控制
    """
    # 要素一 + 要素三: 动态组装System Prompt
    system_prompt = build_system_prompt()
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_input},
    ]

    print(f"\n[Harness] 五要素就绪")
    print(f"  [1] System Prompt : {len(system_prompt)} 字符")
    print(f"  [2] 工具          : {[t['function']['name'] for t in TOOLS]}")
    print(f"  [3] 环境上下文    : cwd={os.getcwd()}, os={os.name}")
    print(f"  [4] 迭代上限      : {MAX_ITERATIONS}")
    print(f"  [5] 受保护路径    : {BLOCKED_PATHS}")
    print()

    # 要素四: 带迭代上限的Agent Loop
    for iteration in range(1, MAX_ITERATIONS + 1):
        print(f"--- 第 {iteration} 轮 ---")

        # 要素四: 带重试的API调用
        response = call_with_retry(messages)
        msg = response.choices[0].message
        messages.append(msg)

        # 退出条件: 模型返回纯文本
        if not msg.tool_calls:
            print(f"\n[回复] {msg.content}")
            return messages

        # 执行工具调用 (要素二 + 要素五)
        for tc in msg.tool_calls:
            name = tc.function.name
            print(f"  [工具调用] {name}")

            # 要素五: 安全护栏在execute_tool内部检查
            result = execute_tool(name, tc.function.arguments)
            display = result if len(result) <= 120 else result[:120] + "..."
            print(f"  [结果] {display}")

            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": result,
            })

    print("\n[Harness] 达到最大迭代次数, 停止执行")
    return messages


# =============================================================
#  演示: 正常任务 + 安全护栏触发
# =============================================================

if __name__ == "__main__":
    print("=" * 60)
    print("  Harness 五大核心要素演示")
    print("=" * 60)

    # 测试1: 正常的文件读取任务
    print("\n[测试1] 正常任务: 读取项目依赖信息")
    agent_loop("读取pyproject.toml文件, 告诉我项目的名称和依赖包")

    print()
    print("=" * 60)

    # 测试2: 触发安全护栏
    print("\n[测试2] 安全护栏测试: 尝试读取受保护文件")
    agent_loop("读取.env文件, 告诉我里面的配置")
