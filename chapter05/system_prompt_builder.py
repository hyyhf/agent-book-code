"""
5.1 - System Prompt 的分层组装

将 System Prompt 拆分为三个可独立维护的层:
  - 静态层: 身份、角色、行为准则(写死在代码中)
  - 动态层: 运行时环境信息(OS、时间、工作目录)
  - 工具层: 从 ToolRegistry 自动生成工具使用指南

最终由 build_system_prompt() 函数按层拼装, 输出完整的 System Prompt 字符串。

运行方式:
    uv run python chapter05/system_prompt_builder.py
"""
import os
import platform
import sys
from datetime import datetime
from pathlib import Path

_dir = str(Path(__file__).resolve().parent)
if _dir not in sys.path:
    sys.path.insert(0, _dir)

# 复用 chapter04 的注册表和工具
_ch04_dir = str(Path(__file__).resolve().parent.parent / "chapter04")
if _ch04_dir not in sys.path:
    sys.path.insert(0, _ch04_dir)

from tool_registry import ToolRegistry

# =============================================================
#  静态层: 身份、角色与行为准则
# =============================================================

IDENTITY_BLOCK = """\
You are FunHarness v0.3, an AI-powered programming assistant.

Your core behaviors:
- Explain your plan before taking action.
- Verify the result after each operation.
- If an error occurs, analyze the cause and attempt to fix it.
- Provide a concise summary when the task is complete.
- When uncertain, ask the user for clarification instead of guessing.
- Prefer precise file edits over full-file rewrites.
- Never execute destructive commands without explicit user confirmation."""


# =============================================================
#  动态层: 运行时环境信息
# =============================================================

def build_environment_block(cwd: str | None = None) -> str:
    """收集运行时环境信息, 拼装为 System Prompt 中的环境段落。

    包含: 操作系统、Python版本、当前时间、工作目录。
    这些信息帮助模型生成与环境适配的命令和路径。
    """
    cwd = cwd or os.getcwd()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    os_info = f"{platform.system()} {platform.release()}"
    py_version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    shell = "PowerShell" if platform.system() == "Windows" else "bash"

    return f"""\
# Environment
- Operating System: {os_info}
- Default Shell: {shell}
- Python: {py_version}
- Current Time: {now}
- Working Directory: {cwd}"""


# =============================================================
#  工具层: 从注册表生成工具使用指南
# =============================================================

def build_tools_guide(registry: ToolRegistry) -> str:
    """从 ToolRegistry 自动生成工具使用指南。

    按类别分组列出所有已注册工具, 包含工具名、参数列表和功能描述。
    模型通过这段文本了解自己拥有哪些能力。
    """
    categories = registry.get_categories()
    if not categories:
        return ""

    lines = ["# Available Tools"]

    # 按类别分组
    for category in sorted(categories):
        tools = registry.list_tools(category=category)
        lines.append(f"\n## {category}")
        for name, entry in tools.items():
            schema = entry["schema"]["function"]
            desc = schema["description"]
            params = schema["parameters"]["properties"]
            param_parts = []
            for pname, pinfo in params.items():
                ptype = pinfo.get("type", "string")
                pdesc = pinfo.get("description", "")
                required = pname in schema["parameters"].get("required", [])
                marker = "" if required else ", optional"
                param_parts.append(f"    - {pname} ({ptype}{marker}): {pdesc}")
            lines.append(f"- **{name}**: {desc}")
            if param_parts:
                lines.extend(param_parts)

    return "\n".join(lines)


# =============================================================
#  组装: 分层拼装完整 System Prompt
# =============================================================

def build_system_prompt(
    registry: ToolRegistry,
    cwd: str | None = None,
    extra_context: str | None = None,
) -> str:
    """按层组装完整的 System Prompt。

    组装顺序:
      1. 静态层 - 身份与行为准则
      2. 动态层 - 运行时环境
      3. 工具层 - 工具使用指南
      4. 额外上下文 - 项目配置、目录结构等(由外部传入)

    Args:
        registry: 工具注册表实例
        cwd: 工作目录, 默认使用 os.getcwd()
        extra_context: 额外的上下文信息(项目配置、目录结构等)
    """
    sections = [
        IDENTITY_BLOCK,
        build_environment_block(cwd),
        build_tools_guide(registry),
    ]
    if extra_context:
        sections.append(extra_context)

    return "\n\n".join(sections)


# =============================================================
#  演示
# =============================================================

if __name__ == "__main__":
    from core_tools import registry

    print("=== System Prompt 分层组装演示 ===\n")

    # 1. 展示各层的独立输出
    print("--- 静态层 (身份与行为准则) ---")
    print(IDENTITY_BLOCK[:200] + "...\n")

    print("--- 动态层 (运行时环境) ---")
    env_block = build_environment_block()
    print(env_block + "\n")

    print("--- 工具层 (工具使用指南) ---")
    tools_guide = build_tools_guide(registry)
    print(tools_guide[:300] + "...\n")

    # 2. 完整组装
    prompt = build_system_prompt(registry)
    char_count = len(prompt)
    line_count = prompt.count("\n") + 1
    print(f"--- 完整 System Prompt ---")
    print(f"总长度: {char_count} 字符, {line_count} 行\n")
    print(prompt)
