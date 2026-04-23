"""
4.4 - 工具执行的生命周期
演示工具调用经历的三个阶段:
  阶段1: 参数解析与校验  (JSON解析 + 必填参数检查)
  阶段2: 执行与结果封装  (调用函数 + 返回结果)
  阶段3: 异常捕获与优雅降级 (所有异常转为可读信息)

运行方式:
    uv run python chapter04/tool_lifecycle.py
"""
import json
import sys
from pathlib import Path

_dir = str(Path(__file__).resolve().parent)
if _dir not in sys.path:
    sys.path.insert(0, _dir)

from tool_registry import ToolRegistry
from core_tools import registry


def execute_tool(reg: ToolRegistry, tool_name: str, arguments_json: str) -> str:
    """
    工具执行的完整生命周期。

    三个阶段中任何一个出错, 都会返回可读的错误信息,
    而非抛出异常。这样模型可以根据错误内容调整策略,
    比如修正参数后重试, 或者换一个工具。

    阶段1 - 参数解析与校验:
      将JSON字符串解析为Python字典, 检查工具是否存在, 验证必填参数。
    阶段2 - 执行与结果封装:
      调用对应的Python函数, 将返回值转为字符串。
    阶段3 - 异常捕获与优雅降级:
      函数内部的异常被捕获, 返回错误描述而非堆栈跟踪。
    """

    # --- 阶段1: 参数解析与校验 ---
    try:
        args = json.loads(arguments_json)
    except json.JSONDecodeError as e:
        return f"[参数解析失败] JSON 格式错误: {e}"

    func = reg.get_function(tool_name)
    if func is None:
        return f"[未知工具] '{tool_name}' 未在注册表中"

    schema = reg.get_schema(tool_name)
    if schema:
        required = schema["function"]["parameters"].get("required", [])
        missing = [p for p in required if p not in args]
        if missing:
            return f"[参数缺失] 缺少必填参数: {', '.join(missing)}"

    # --- 阶段2: 执行 ---
    try:
        result = func(**args)
        return str(result)
    except TypeError as e:
        return f"[参数类型错误] {e}"
    except Exception as e:
        # --- 阶段3: 优雅降级 ---
        return f"[执行异常] {tool_name}: {type(e).__name__}: {e}"


if __name__ == "__main__":
    print("=== 工具执行生命周期演示 ===\n")

    scenarios = [
        (
            "正常执行",
            "read_file",
            '{"path": "pyproject.toml"}',
        ),
        (
            "JSON 格式错误",
            "read_file",
            "{path: bad json}",
        ),
        (
            "未知工具",
            "delete_database",
            "{}",
        ),
        (
            "参数缺失",
            "write_file",
            '{"path": "test.txt"}',
        ),
        (
            "文件不存在(工具内部优雅降级)",
            "read_file",
            '{"path": "nonexistent_file_xyz.txt"}',
        ),
    ]

    for title, tool_name, args_json in scenarios:
        print(f"--- {title} ---")
        print(f"  工具: {tool_name}")
        print(f"  参数: {args_json}")
        result = execute_tool(registry, tool_name, args_json)
        # 截断过长的输出
        display = result if len(result) <= 150 else result[:150] + "..."
        print(f"  结果: {display}")
        print()

    # 额外演示: replace_in_file 的完整流程
    print("--- 精确替换(写入 -> 替换 -> 读取验证) ---")
    test_file = "lifecycle_test_tmp.txt"
    print(f"  1. 写入测试文件:")
    r = execute_tool(registry, "write_file", json.dumps({
        "path": test_file,
        "content": "hello world\nfoo bar\nhello again",
    }))
    print(f"     {r}")

    print(f"  2. 替换 'hello' -> 'hi':")
    r = execute_tool(registry, "replace_in_file", json.dumps({
        "path": test_file,
        "old_text": "hello",
        "new_text": "hi",
    }))
    print(f"     {r}")

    print(f"  3. 读取验证:")
    r = execute_tool(registry, "read_file", json.dumps({"path": test_file}))
    print(f"     {r}")

    # 清理
    Path(test_file).unlink(missing_ok=True)
