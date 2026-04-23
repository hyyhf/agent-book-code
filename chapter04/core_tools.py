"""
4.3 - 核心工具实现
六个核心工具的完整实现, 涵盖文件操作、命令执行和搜索检索。
所有工具使用 ToolRegistry 装饰器注册, Schema 自动生成。

运行方式:
    uv run python chapter04/core_tools.py
"""
import os
import re
import subprocess
import sys
from pathlib import Path

_dir = str(Path(__file__).resolve().parent)
if _dir not in sys.path:
    sys.path.insert(0, _dir)

from tool_registry import ToolRegistry

registry = ToolRegistry()


# =============================================================
#  4.3.1 文件操作工具: 读取、写入、搜索、替换
# =============================================================

@registry.tool(category="file")
def read_file(path: str) -> str:
    """读取指定文件的完整文本内容并返回。如果文件不存在或无权限, 返回明确的错误信息。

    Args:
        path: 要读取的文件路径(支持相对路径和绝对路径)
    """
    try:
        text = Path(path).read_text(encoding="utf-8")
        line_count = text.count("\n") + (1 if text else 0)
        return f"[{line_count} 行]\n{text}"
    except FileNotFoundError:
        return f"错误: 文件 '{path}' 不存在"
    except PermissionError:
        return f"错误: 没有权限读取 '{path}'"
    except UnicodeDecodeError:
        return f"错误: '{path}' 不是文本文件或编码不支持"
    except Exception as e:
        return f"读取失败: {e}"


@registry.tool(category="file")
def write_file(path: str, content: str) -> str:
    """将内容写入指定文件。文件已存在则覆盖, 不存在则自动创建(含必要的父目录)。

    Args:
        path: 目标文件路径
        content: 要写入的完整文本内容
    """
    try:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        line_count = content.count("\n") + (1 if content else 0)
        return f"已写入 {path} ({len(content)} 字符, {line_count} 行)"
    except PermissionError:
        return f"错误: 没有权限写入 '{path}'"
    except Exception as e:
        return f"写入失败: {e}"


@registry.tool(category="file")
def replace_in_file(path: str, old_text: str, new_text: str) -> str:
    """在文件中查找 old_text 并替换为 new_text。要求 old_text 在文件中精确存在。

    Args:
        path: 目标文件路径
        old_text: 要查找的原始文本(必须精确匹配)
        new_text: 替换后的新文本
    """
    try:
        p = Path(path)
        content = p.read_text(encoding="utf-8")
        count = content.count(old_text)
        if count == 0:
            return f"错误: 在 '{path}' 中未找到指定文本"
        new_content = content.replace(old_text, new_text)
        p.write_text(new_content, encoding="utf-8")
        return f"已替换 {count} 处 ({path})"
    except FileNotFoundError:
        return f"错误: 文件 '{path}' 不存在"
    except Exception as e:
        return f"替换失败: {e}"


# =============================================================
#  4.3.2 Shell 命令执行工具
# =============================================================

@registry.tool(category="system")
def run_command(command: str) -> str:
    """在系统 Shell 中执行命令并返回输出。包含 stdout 和 stderr, 超时 30 秒自动终止。

    Args:
        command: 要执行的 Shell 命令字符串
    """
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=30,
            cwd=os.getcwd(),
        )
        parts = []
        if result.stdout:
            parts.append(result.stdout)
        if result.stderr:
            parts.append(f"[stderr]\n{result.stderr}")
        output = "\n".join(parts) if parts else "(无输出)"
        if len(output) > 10000:
            output = output[:10000] + f"\n...(已截断, 共 {len(output)} 字符)"
        return f"[exit={result.returncode}]\n{output}"
    except subprocess.TimeoutExpired:
        return "错误: 命令执行超时(30秒)"
    except Exception as e:
        return f"命令执行失败: {e}"


# =============================================================
#  4.3.3 搜索与检索工具
# =============================================================

@registry.tool(category="search")
def list_directory(path: str) -> str:
    """列出指定目录下的文件和子目录, 显示类型和大小信息。

    Args:
        path: 要列出内容的目录路径, 使用 '.' 表示当前目录
    """
    try:
        p = Path(path)
        if not p.is_dir():
            return f"错误: '{path}' 不是一个目录"
        entries = sorted(p.iterdir(), key=lambda x: (x.is_file(), x.name))
        # 跳过隐藏文件
        entries = [e for e in entries if not e.name.startswith(".")]
        if not entries:
            return f"目录 '{path}' 为空"

        lines = []
        for entry in entries:
            if entry.is_dir():
                lines.append(f"  [目录] {entry.name}/")
            else:
                size = entry.stat().st_size
                if size < 1024:
                    size_str = f"{size}B"
                elif size < 1024 * 1024:
                    size_str = f"{size / 1024:.1f}KB"
                else:
                    size_str = f"{size / 1024 / 1024:.1f}MB"
                lines.append(f"  [文件] {entry.name} ({size_str})")
        return f"目录 {path} ({len(lines)} 项):\n" + "\n".join(lines)
    except PermissionError:
        return f"错误: 没有权限访问 '{path}'"
    except Exception as e:
        return f"列出目录失败: {e}"


@registry.tool(category="search")
def grep_search(pattern: str, path: str) -> str:
    """在指定文件或目录中搜索匹配的文本行。支持正则表达式, 返回匹配行及行号。

    Args:
        pattern: 搜索模式(支持正则表达式)
        path: 要搜索的文件路径或目录路径
    """
    try:
        regex = re.compile(pattern, re.IGNORECASE)
    except re.error as e:
        return f"错误: 无效的正则表达式 '{pattern}': {e}"

    p = Path(path)
    results = []
    max_results = 50

    def _search_one_file(fp: Path):
        try:
            text = fp.read_text(encoding="utf-8")
        except (UnicodeDecodeError, PermissionError):
            return
        for i, line in enumerate(text.splitlines(), 1):
            if regex.search(line):
                results.append(f"  {fp}:{i}: {line.rstrip()}")
                if len(results) >= max_results:
                    return

    if p.is_file():
        _search_one_file(p)
    elif p.is_dir():
        for fp in sorted(p.rglob("*")):
            if fp.is_file() and not any(
                part.startswith(".") for part in fp.parts
            ):
                _search_one_file(fp)
                if len(results) >= max_results:
                    break
    else:
        return f"错误: '{path}' 不存在"

    if not results:
        return f"未找到匹配 '{pattern}' 的内容"

    header = f"找到 {len(results)} 个匹配"
    if len(results) >= max_results:
        header += f" (已达上限 {max_results})"
    return header + ":\n" + "\n".join(results)


# =============================================================
#  演示
# =============================================================

if __name__ == "__main__":
    print("=== 核心工具集 ===\n")
    print(f"已注册 {len(registry)} 个工具")
    print(f"类别: {registry.get_categories()}\n")

    for schema in registry.get_openai_schemas():
        fi = schema["function"]
        params = fi["parameters"]["properties"]
        param_str = ", ".join(f"{k}: {v['type']}" for k, v in params.items())
        print(f"  {fi['name']}({param_str})")
        desc = fi["description"]
        print(f"    {desc[:70]}{'...' if len(desc) > 70 else ''}")
        print()

    print("--- 功能测试 ---\n")

    print("[read_file] pyproject.toml:")
    result = read_file("pyproject.toml")
    print(result[:200] + ("..." if len(result) > 200 else ""))
    print()

    print("[list_directory] 当前目录:")
    print(list_directory("."))
    print()

    print("[grep_search] 搜索 'openai' in pyproject.toml:")
    print(grep_search("openai", "pyproject.toml"))
