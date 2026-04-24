"""
FunHarness - Context Management

Project config detection, directory tree mapping, token estimation,
cost tracking, context compaction.
"""
import json
import os
from datetime import datetime
from pathlib import Path

from .llm import client, MODEL

_CONFIG_FILES = [
    "pyproject.toml", "package.json", "Cargo.toml", "go.mod",
    "pom.xml", "Makefile", "Dockerfile", "docker-compose.yml", "README.md",
]

_SKIP_DIRS = {
    ".git", ".svn", ".hg", "__pycache__", ".mypy_cache", ".pytest_cache",
    ".ruff_cache", "node_modules", ".venv", "venv", ".tox",
    ".next", "dist", "build", "target", ".idea", ".vscode",
}

CONTEXT_SOFT_LIMIT = 80000
TOOL_RESULT_MAX_CHARS = 3000
KEEP_RECENT_TOOL_RESULTS = 3


def detect_project_configs(cwd: str | None = None) -> dict[str, str]:
    cwd = Path(cwd or os.getcwd())
    configs = {}
    for name in _CONFIG_FILES:
        path = cwd / name
        if path.is_file():
            try:
                text = path.read_text(encoding="utf-8")
                if len(text) > 2000:
                    text = text[:2000] + "\n...(truncated)"
                configs[name] = text
            except (UnicodeDecodeError, PermissionError):
                continue
    return configs


def map_directory_structure(cwd: str | None = None, max_depth: int = 3, max_entries: int = 80) -> str:
    root = Path(cwd or os.getcwd())
    lines = [f"{root.name}/"]
    count = [0]
    truncated = [False]

    def _walk(directory: Path, prefix: str, depth: int):
        if depth > max_depth or truncated[0]:
            return
        try:
            entries = sorted(directory.iterdir(), key=lambda x: (x.is_file(), x.name.lower()))
        except PermissionError:
            return
        entries = [e for e in entries if not e.name.startswith(".") and e.name not in _SKIP_DIRS]
        for i, entry in enumerate(entries):
            if count[0] >= max_entries:
                truncated[0] = True
                lines.append(f"{prefix}... ({count[0]}+ entries, truncated)")
                return
            is_last = i == len(entries) - 1
            connector = "--- " if is_last else "|-- "
            next_prefix = prefix + ("    " if is_last else "|   ")
            if entry.is_dir():
                lines.append(f"{prefix}{connector}{entry.name}/")
                count[0] += 1
                _walk(entry, next_prefix, depth + 1)
            else:
                try:
                    size = entry.stat().st_size
                    sz = f"{size}B" if size < 1024 else (f"{size/1024:.1f}KB" if size < 1048576 else f"{size/1048576:.1f}MB")
                except OSError:
                    sz = "?"
                lines.append(f"{prefix}{connector}{entry.name} ({sz})")
                count[0] += 1

    _walk(root, "", 0)
    return "\n".join(lines)


def build_context_block(cwd: str | None = None) -> str:
    cwd = cwd or os.getcwd()
    sections = ["# Project Context"]
    configs = detect_project_configs(cwd)
    if configs:
        sections.append("\n## Project Configuration")
        for name, content in configs.items():
            sections.append(f"\n### {name}\n```\n{content}\n```")
    tree = map_directory_structure(cwd)
    sections.append(f"\n## Directory Structure\n```\n{tree}\n```")
    return "\n".join(sections)


# ---- Token Estimation & Cost Tracking ----

def estimate_tokens(messages: list[dict]) -> int:
    total_chars = 0
    for msg in messages:
        content = msg.get("content", "")
        if content:
            total_chars += len(str(content))
        tc = msg.get("tool_calls", [])
        if tc:
            total_chars += len(json.dumps(tc, ensure_ascii=False))
    return total_chars // 4


class CostTracker:
    def __init__(self, input_price: float = 2.5, output_price: float = 10.0):
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.input_price_per_m = input_price
        self.output_price_per_m = output_price
        self.call_count = 0

    def update(self, usage):
        if usage is None:
            return
        self.total_input_tokens += getattr(usage, "prompt_tokens", 0) or 0
        self.total_output_tokens += getattr(usage, "completion_tokens", 0) or 0
        self.call_count += 1

    @property
    def total_tokens(self) -> int:
        return self.total_input_tokens + self.total_output_tokens

    @property
    def estimated_cost(self) -> float:
        return (self.total_input_tokens * self.input_price_per_m +
                self.total_output_tokens * self.output_price_per_m) / 1_000_000

    def summary(self) -> str:
        return (
            f"API calls: {self.call_count} | "
            f"Tokens: {self.total_input_tokens:,} in + {self.total_output_tokens:,} out = "
            f"{self.total_tokens:,} total | Cost: ${self.estimated_cost:.4f}"
        )


# ---- Context Compaction ----

def truncate_tool_results(messages: list[dict]) -> list[dict]:
    tool_indices = [i for i, m in enumerate(messages) if m.get("role") == "tool"]
    if len(tool_indices) <= KEEP_RECENT_TOOL_RESULTS:
        return messages
    for idx in tool_indices[:-KEEP_RECENT_TOOL_RESULTS]:
        content = messages[idx].get("content", "")
        if len(content) > TOOL_RESULT_MAX_CHARS:
            messages[idx]["content"] = content[:TOOL_RESULT_MAX_CHARS] + f"\n...(truncated, was {len(content)} chars)"
    return messages


def compact_conversation(messages: list[dict], model: str = MODEL) -> list[dict]:
    if len(messages) < 6:
        return messages
    system_msg = messages[0] if messages[0].get("role") == "system" else None
    conversation = messages[1:] if system_msg else messages
    keep_recent = 4
    if len(conversation) <= keep_recent:
        return messages
    old_messages = conversation[:-keep_recent]
    recent_messages = conversation[-keep_recent:]

    lines = []
    for msg in old_messages:
        role = msg.get("role", "?")
        content = msg.get("content", "")
        if not content:
            tc = msg.get("tool_calls", [])
            if tc:
                names = [t.get("function", {}).get("name", "?") for t in tc]
                content = f"[Called tools: {', '.join(names)}]"
            else:
                content = "[no content]"
        if len(content) > 500:
            content = content[:500] + "..."
        lines.append(f"[{role}] {content}")
    summary_text = "\n".join(lines)

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "You summarize conversations concisely."},
                {"role": "user", "content": f"Summarize this conversation, preserving actionable info:\n{summary_text}"},
            ],
            max_tokens=1000,
        )
        summary = response.choices[0].message.content or "(summary unavailable)"
    except Exception as e:
        summary = f"(summary failed: {e})"

    compacted = []
    if system_msg:
        compacted.append(system_msg)
    compacted.append({
        "role": "user",
        "content": f"[Conversation Summary]\n{summary}\n\n(Summarizes {len(old_messages)} earlier messages. Continue from here.)",
    })
    compacted.extend(recent_messages)
    return compacted


def should_compact(messages: list[dict]) -> bool:
    return sum(len(str(m.get("content", ""))) for m in messages) > CONTEXT_SOFT_LIMIT
