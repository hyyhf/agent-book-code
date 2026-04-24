"""
FunHarness - System Prompt Builder

Layered system prompt: identity + environment + tools guide + context.
"""
import os
import platform
import sys
from datetime import datetime

from .tools import ToolRegistry


IDENTITY_BLOCK = """\
You are FunHarness, an AI-powered programming assistant with full
observability, task management, and web search capabilities.

Core behaviors:
- Explain your plan before taking action.
- Verify the result after each operation.
- If an error occurs, analyze the cause and attempt to fix it.
- Provide a concise summary when the task is complete.
- When uncertain, ask the user for clarification instead of guessing.
- Prefer precise file edits over full-file rewrites.

Task management:
- Use tool_view_tasks to see the current task list and progress.
- Use tool_next_task to get the next pending task.
- After completing a task, use tool_complete_task to mark it done.

Security awareness:
- You operate under permission mode ({mode}).
- Some operations require user approval. Respect all denials.

Memory & Knowledge:
- Use tool_save_memory to record important discoveries.
- Use tool_web_search to find information online.
- Use tool_web_fetch to read web page content."""


def build_environment_block(cwd: str | None = None) -> str:
    """Build runtime environment section."""
    cwd = cwd or os.getcwd()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    os_info = f"{platform.system()} {platform.release()}"
    py_ver = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    shell = "PowerShell" if platform.system() == "Windows" else "bash"
    return f"""\
# Environment
- Operating System: {os_info}
- Default Shell: {shell}
- Python: {py_ver}
- Current Time: {now}
- Working Directory: {cwd}"""


def build_tools_guide(registry: ToolRegistry) -> str:
    """Generate tools usage guide from registry."""
    categories = registry.get_categories()
    if not categories:
        return ""
    lines = ["# Available Tools"]
    for category in sorted(categories):
        tools = registry.list_tools(category=category)
        lines.append(f"\n## {category}")
        for name, entry in tools.items():
            schema = entry["schema"]["function"]
            desc = schema["description"]
            params = schema["parameters"]["properties"]
            lines.append(f"- **{name}**: {desc}")
            for pname, pinfo in params.items():
                ptype = pinfo.get("type", "string")
                pdesc = pinfo.get("description", "")
                req = pname in schema["parameters"].get("required", [])
                marker = "" if req else ", optional"
                lines.append(f"    - {pname} ({ptype}{marker}): {pdesc}")
    return "\n".join(lines)


def build_system_prompt(
    registry: ToolRegistry,
    mode: str = "suggest",
    extra_context: str = "",
    memory_text: str = "",
    task_summary: str = "",
) -> str:
    """Assemble the full system prompt."""
    identity = IDENTITY_BLOCK.format(mode=mode)
    sections = [
        identity,
        build_environment_block(),
        build_tools_guide(registry),
    ]

    mode_desc = {
        "auto": "All operations execute automatically.",
        "suggest": "Read operations are automatic. Write/execute require approval.",
        "approve": "All operations require explicit user approval.",
    }
    sections.append(
        f"# Current Permission Mode: {mode}\n{mode_desc.get(mode, '')}"
    )

    if extra_context:
        sections.append(extra_context)

    if memory_text and memory_text != "(no memories saved yet)":
        summary = memory_text[:2000]
        if len(memory_text) > 2000:
            summary += "\n...(use read_memory for full content)"
        sections.append(f"# Saved Memories\n{summary}")

    if task_summary:
        sections.append(f"# Task Progress\n{task_summary}")

    return "\n\n".join(sections)
