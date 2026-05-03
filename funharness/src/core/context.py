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

CONTEXT_SOFT_LIMIT = 2_400_000
TOOL_RESULT_MAX_CHARS = 80_000
KEEP_RECENT_TOOL_RESULTS = 8


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


# ---------------------------------------------------------------------------
# Model Pricing Registry (CNY per million tokens)
#
# Each entry maps a model name to a dict with three price tiers:
#   input_cache_hit  - input price when cache is hit
#   input_cache_miss - input price when cache is missed (conservative default)
#   output           - output token price
#
# To add a new model or update prices, simply add/modify an entry here.
# ---------------------------------------------------------------------------

MODEL_PRICING: dict[str, dict[str, float]] = {
    "deepseek-v4-flash": {
        "input_cache_hit":  0.2,
        "input_cache_miss": 1.0,
        "output":           2.0,
    },
    "deepseek-v4-pro": {
        "input_cache_hit":  1.0,
        "input_cache_miss": 12.0,
        "output":           24.0,
    },
}

# Fallback pricing for unknown models (uses the cheapest tier as default)
_DEFAULT_PRICING: dict[str, float] = {
    "input_cache_hit":  0.2,
    "input_cache_miss": 1.0,
    "output":           2.0,
}


def get_model_pricing(model: str) -> dict[str, float]:
    """Look up pricing for a model. Falls back to default if not found."""
    return MODEL_PRICING.get(model, _DEFAULT_PRICING)


class CostTracker:
    """Token usage and cost tracker with per-model pricing.

    Pricing is looked up from MODEL_PRICING by model name.
    Uses cache-miss input price as the conservative estimate.
    Supports per-turn tracking via mark_turn_start() / turn_summary().
    """

    def __init__(self, model: str | None = None):
        pricing = get_model_pricing(model or "")
        self.input_price_per_m = pricing["input_cache_miss"]
        self.output_price_per_m = pricing["output"]
        self.model = model or ""

        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.call_count = 0

        # Per-turn tracking
        self._turn_input_start = 0
        self._turn_output_start = 0

    def update(self, usage):
        if usage is None:
            return
        self.total_input_tokens += getattr(usage, "prompt_tokens", 0) or 0
        self.total_output_tokens += getattr(usage, "completion_tokens", 0) or 0
        self.call_count += 1

    # -- Turn-level tracking --

    def mark_turn_start(self):
        """Snapshot current totals so we can compute per-turn delta later."""
        self._turn_input_start = self.total_input_tokens
        self._turn_output_start = self.total_output_tokens

    @property
    def turn_input_tokens(self) -> int:
        return self.total_input_tokens - self._turn_input_start

    @property
    def turn_output_tokens(self) -> int:
        return self.total_output_tokens - self._turn_output_start

    @property
    def turn_tokens(self) -> int:
        return self.turn_input_tokens + self.turn_output_tokens

    @property
    def turn_cost(self) -> float:
        return (self.turn_input_tokens * self.input_price_per_m +
                self.turn_output_tokens * self.output_price_per_m) / 1_000_000

    def turn_summary(self) -> str:
        return (
            f"{self.turn_tokens:,} tokens | "
            f"\u00a5{self.turn_cost:.4f}"
        )

    # -- Session-level totals --

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
            f"{self.total_tokens:,} total | Cost: \u00a5{self.estimated_cost:.4f}"
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


def _tool_call_ids(msg: dict) -> set[str]:
    return {
        tc.get("id", "")
        for tc in msg.get("tool_calls", []) or []
        if tc.get("id")
    }


def _recent_messages_with_valid_tool_boundaries(
    conversation: list[dict],
    keep_recent: int,
) -> list[dict]:
    """Return a recent suffix without splitting assistant/tool-call groups."""
    start = max(0, len(conversation) - keep_recent)

    while start < len(conversation) and conversation[start].get("role") == "tool":
        tool_start = start
        while tool_start > 0 and conversation[tool_start - 1].get("role") == "tool":
            tool_start -= 1

        assistant_idx = tool_start - 1
        if assistant_idx < 0:
            start += 1
            continue

        assistant = conversation[assistant_idx]
        expected_ids = _tool_call_ids(assistant)
        actual_ids = {
            msg.get("tool_call_id", "")
            for msg in conversation[tool_start:start + 1]
            if msg.get("tool_call_id")
        }

        if assistant.get("role") == "assistant" and actual_ids.issubset(expected_ids):
            start = assistant_idx
            break

        start += 1

    return conversation[start:]


def compact_conversation(messages: list[dict], model: str = MODEL, llm_client=None) -> list[dict]:
    if len(messages) < 6:
        return messages
    system_msg = messages[0] if messages[0].get("role") == "system" else None
    conversation = messages[1:] if system_msg else messages
    keep_recent = 4
    if len(conversation) <= keep_recent:
        return messages
    recent_messages = _recent_messages_with_valid_tool_boundaries(conversation, keep_recent)
    old_messages = conversation[:len(conversation) - len(recent_messages)]

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
        active_client = llm_client or client
        response = active_client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "You summarize conversations concisely."},
                {"role": "user", "content": f"Summarize this conversation, preserving actionable info:\n{summary_text}"},
            ],
            max_tokens=4000,
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
