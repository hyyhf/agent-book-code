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
- Durable tasks are work goals; runtime tasks are active execution slots.

Agent teams:
- Use tool_team_create for persistent teammates with a role and inbox.
- Use tool_team_list and tool_team_tasks to inspect team state before assigning work.
- Use tool_team_delegate for asynchronous teammate work and tool_team_task_update
  to keep team-run task status current.
- Use tool_team_send for coordination messages; when a team run is active, it
  records the message in the run snapshot as well as the teammate inbox.
- Use tool_team_rename or tool_team_shutdown when the user asks to adjust the lineup.
- Leader workflow: for broad work, propose a teammate lineup first and wait for
  explicit user confirmation before creating new teammates, unless the user
  already named the teammates to create immediately.
- Dependent work must be dispatched sequentially. Do not tell a teammate to
  keep a live turn open while waiting for another teammate; wait for the
  prerequisite result, then assign the dependent task.
- When a teammate reports back, review the result, update the team task, decide
  whether follow-up is needed, and synthesize the final answer for the user.
- Use tool_subagent_run for one-shot isolated analysis.

Scheduling:
- Use tool_schedule_create for future prompts. Schedule notifications return
  to the main loop before model calls.

Security awareness:
- You operate under permission mode ({mode}).
- Some operations require user approval. Respect all denials.

Memory & Knowledge:
- Use tool_save_memory to record important discoveries.
- Use tool_list_skills to inspect enabled skills and diagnostics when needed.
- When a task matches a listed skill, call tool_load_skill with the skill name
  before following that skill's workflow.
- Use tool_web_search to find information online.
- Use tool_web_fetch to read web page content.
- Use tool_web_crawl for advanced scraping, JavaScript-heavy pages,
  multi-page crawls, selector extraction, or stronger anti-bot handling.
- When users attach files, use tool_list_attachments and tool_read_attachment
  to inspect them as needed instead of relying only on attachment previews."""


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
    skills_summary: str = "",
    persona_prompt: str = "",
) -> str:
    """Assemble the full system prompt."""
    if persona_prompt:
        identity = persona_prompt
    else:
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

    if skills_summary:
        sections.append(f"# Skills\n{skills_summary}")

    if memory_text and memory_text != "(no memories saved yet)":
        summary = memory_text[:2000]
        if len(memory_text) > 2000:
            summary += "\n...(use read_memory for full content)"
        sections.append(f"# Saved Memories\n{summary}")

    if task_summary:
        sections.append(f"# Task Progress\n{task_summary}")

    return "\n\n".join(sections)
