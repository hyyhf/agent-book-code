"""
FunHarness - Tool Registry & Core Tools

Tool registry with decorator-based registration, core local tools,
and web tools registered from the webtools package.
"""
import inspect
import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from types import UnionType
from typing import Any, TypedDict, Union, get_args, get_origin, get_type_hints, is_typeddict

from .attachments import parse_document
from .permissions import decode_process_output, format_command_output

# ----------------------------------------------------------------
#  ToolRegistry
# ----------------------------------------------------------------

_TYPE_MAP = {str: "string", int: "integer", float: "number", bool: "boolean"}


def _schema_for_type(ptype: Any) -> dict:
    origin = get_origin(ptype)
    args = get_args(ptype)

    if origin in (Union, UnionType):
        non_none = [arg for arg in args if arg is not type(None)]
        return _schema_for_type(non_none[0]) if non_none else {"type": "string"}

    if origin in (list, tuple):
        item_type = args[0] if args else str
        return {"type": "array", "items": _schema_for_type(item_type)}

    if origin is dict:
        schema = {"type": "object"}
        if len(args) == 2:
            schema["additionalProperties"] = _schema_for_type(args[1])
        return schema

    if is_typeddict(ptype):
        hints = get_type_hints(ptype)
        return {
            "type": "object",
            "properties": {name: _schema_for_type(hint) for name, hint in hints.items()},
            "required": list(getattr(ptype, "__required_keys__", [])),
        }

    return {"type": _TYPE_MAP.get(ptype, "string")}


def _parse_docstring(doc: str) -> tuple[str, dict[str, str]]:
    """Parse Google-style docstring -> (description, {param: desc})."""
    lines = doc.strip().split("\n")
    desc_lines, param_docs = [], {}
    in_args = False
    for line in lines:
        s = line.strip()
        if s.lower().startswith("args:"):
            in_args = True
            continue
        if s.lower().startswith(("returns:", "raises:", "example")):
            in_args = False
            continue
        if in_args and ":" in s:
            k, v = s.split(":", 1)
            param_docs[k.strip()] = v.strip()
        elif not in_args and s:
            desc_lines.append(s)
    return " ".join(desc_lines), param_docs


class ToolRegistry:
    """Tool registry: register, schema generation, discovery."""

    def __init__(self):
        self._tools: dict[str, dict] = {}

    def tool(self, *, category: str = "general"):
        def decorator(func):
            name = func.__name__
            doc = inspect.getdoc(func) or name
            func_desc, param_docs = _parse_docstring(doc)
            hints = get_type_hints(func)
            sig = inspect.signature(func)
            properties, required = {}, []
            for pname, param in sig.parameters.items():
                ptype = hints.get(pname, str)
                prop = _schema_for_type(ptype)
                if pname in param_docs:
                    prop["description"] = param_docs[pname]
                properties[pname] = prop
                if param.default is inspect.Parameter.empty:
                    required.append(pname)
            schema = {
                "type": "function",
                "function": {
                    "name": name,
                    "description": func_desc,
                    "parameters": {
                        "type": "object",
                        "properties": properties,
                        "required": required,
                    },
                },
            }
            self._tools[name] = {
                "function": func, "schema": schema, "category": category,
            }
            return func
        return decorator

    def get_openai_schemas(self) -> list[dict]:
        return [t["schema"] for t in self._tools.values()]

    def get_function(self, name: str):
        entry = self._tools.get(name)
        return entry["function"] if entry else None

    def get_schema(self, name: str) -> dict | None:
        entry = self._tools.get(name)
        return entry["schema"] if entry else None

    def list_tools(self, category: str | None = None) -> dict:
        if category:
            return {n: t for n, t in self._tools.items() if t["category"] == category}
        return dict(self._tools)

    def get_categories(self) -> list[str]:
        return list({t["category"] for t in self._tools.values()})

    def __len__(self):
        return len(self._tools)

    def __contains__(self, name):
        return name in self._tools

    def __repr__(self):
        return f"ToolRegistry({len(self._tools)} tools)"

    def subset(self, categories: list[str]) -> "ToolRegistry":
        """Create a new registry containing only tools from the given categories."""
        sub = ToolRegistry()
        for name, entry in self._tools.items():
            if entry["category"] in categories:
                sub._tools[name] = entry
        return sub


# ----------------------------------------------------------------
#  Global Registry & Core Tools
# ----------------------------------------------------------------

registry = ToolRegistry()


@dataclass
class ToolResult:
    """Tool return value with optional UI-only display metadata."""

    content: str
    display: dict[str, Any] | None = None

    def __str__(self) -> str:
        return self.content


class ReplacementSpec(TypedDict):
    old_text: str
    new_text: str


# --- File Tools ---

@registry.tool(category="file")
def tool_read_file(path: str) -> str:
    """Read file content and return it. Returns error message if file not found.

    Args:
        path: File path to read (relative or absolute)
    """
    try:
        return parse_document(Path(path))
    except FileNotFoundError:
        return f"Error: file '{path}' not found"
    except PermissionError:
        return f"Error: permission denied for '{path}'"
    except Exception as e:
        return f"Read failed: {e}"


@registry.tool(category="file")
def tool_write_file(path: str, content: str) -> str:
    """Write content to a file. Overwrites if exists, creates parent dirs if needed.

    Args:
        path: Target file path
        content: Full text content to write
    """
    try:
        p = Path(path)
        is_new = not p.exists()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        line_count = content.count("\n") + (1 if content else 0)
        action = "Created" if is_new else "Written to"
        return f"{action} {path} ({len(content)} chars, {line_count} lines)"
    except PermissionError:
        return f"Error: permission denied for '{path}'"
    except Exception as e:
        return f"Write failed: {e}"


def _text_not_found_message(path: str, old_text: str, content: str, prefix: str = "Error") -> str:
    needle = old_text.strip().split("\n")[0][:60]
    hint = ""
    if needle:
        for i, line in enumerate(content.splitlines(), 1):
            if needle[:30] in line:
                hint = f" (similar text near line {i}: {line.rstrip()[:80]})"
                break
    return f"{prefix}: text not found in '{path}'{hint}"


def _normalize_replacements(
    old_text: str,
    new_text: str,
    replacements: list[ReplacementSpec] | str | None,
) -> tuple[list[ReplacementSpec] | None, str | None]:
    if replacements is None:
        if old_text == "":
            return None, "Error: old_text is required when replacements is not provided"
        return [{"old_text": old_text, "new_text": new_text}], None

    if isinstance(replacements, str):
        try:
            replacements = json.loads(replacements)
        except json.JSONDecodeError as e:
            return None, f"Error: replacements must be a JSON array ({e})"

    if not isinstance(replacements, list) or not replacements:
        return None, "Error: replacements must be a non-empty list"

    normalized = []
    for index, item in enumerate(replacements, 1):
        if not isinstance(item, dict):
            return None, f"Error: replacement {index} must be an object"
        item_old = item.get("old_text", "")
        item_new = item.get("new_text", "")
        if not isinstance(item_old, str) or not isinstance(item_new, str):
            return None, f"Error: replacement {index} old_text and new_text must be strings"
        if item_old == "":
            return None, f"Error: replacement {index} old_text cannot be empty"
        normalized.append({"old_text": item_old, "new_text": item_new})
    return normalized, None


@registry.tool(category="file")
def tool_replace_in_file(
    path: str,
    old_text: str = "",
    new_text: str = "",
    replacements: list[ReplacementSpec] | None = None,
) -> str:
    """Find exact text in a file and replace it. For multiple edits, pass replacements.

    Args:
        path: Target file path
        old_text: Text to find for a single replacement (exact match required)
        new_text: Replacement text for a single replacement
        replacements: Optional list of {"old_text": "...", "new_text": "..."} edits to apply in one write
    """
    try:
        p = Path(path)
        content = p.read_text(encoding="utf-8")
        specs, error = _normalize_replacements(old_text, new_text, replacements)
        if error:
            return error

        matches: list[tuple[int, int, int]] = []
        counts = [0 for _ in specs]
        for spec_index, spec in enumerate(specs):
            start = 0
            while True:
                found = content.find(spec["old_text"], start)
                if found == -1:
                    break
                end = found + len(spec["old_text"])
                matches.append((found, end, spec_index))
                counts[spec_index] += 1
                start = end
            if counts[spec_index] == 0:
                prefix = f"Error: replacement {spec_index + 1}"
                return _text_not_found_message(path, spec["old_text"], content, prefix)

        matches.sort(key=lambda match: (match[0], match[1]))
        for previous, current in zip(matches, matches[1:]):
            if previous[1] > current[0]:
                return (
                    "Error: replacement texts overlap; use more specific old_text values "
                    f"near offset {current[0]}"
                )

        parts = []
        cursor = 0
        for start, end, spec_index in matches:
            parts.append(content[cursor:start])
            parts.append(specs[spec_index]["new_text"])
            cursor = end
        parts.append(content[cursor:])
        new_content = "".join(parts)
        p.write_text(new_content, encoding="utf-8")
        count = sum(counts)
        note = ""
        if len(specs) > 1:
            detail = ", ".join(f"{i + 1}:{n}" for i, n in enumerate(counts))
            note = f" across {len(specs)} replacement(s) ({detail})"
        elif count > 1:
            note = f" (warning: {count} occurrences replaced, consider using more specific text)"
        return f"Replaced {count} occurrence(s) in {path}{note}"
    except FileNotFoundError:
        return f"Error: file '{path}' not found"
    except Exception as e:
        return f"Replace failed: {e}"


# --- System Tools ---

@registry.tool(category="system")
def tool_run_command(command: str) -> str:
    """Execute a shell command and return output. Timeout 30s.

    Args:
        command: Shell command string to execute
    """
    import platform

    try:
        popen_kwargs = {
            "shell": True,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "cwd": os.getcwd(),
        }
        if platform.system() != "Windows":
            popen_kwargs["start_new_session"] = True

        proc = subprocess.Popen(command, **popen_kwargs)
        try:
            stdout_bytes, stderr_bytes = proc.communicate(timeout=30)
        except subprocess.TimeoutExpired:
            # Kill entire process tree on Windows
            try:
                if platform.system() == "Windows":
                    subprocess.run(
                        ["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                        capture_output=True, timeout=5,
                    )
                else:
                    import signal
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except Exception:
                proc.kill()
            proc.wait(timeout=5)
            return "Error: command timed out (30s)"

        stdout = decode_process_output(stdout_bytes)
        stderr = decode_process_output(stderr_bytes)
        output = format_command_output(
            command, os.getcwd(), stdout, stderr, 10000
        )
        return f"[exit={proc.returncode}]\n{output}"
    except Exception as e:
        return f"Command failed: {e}"


# --- Search Tools ---

@registry.tool(category="search")
def tool_list_directory(path: str) -> str:
    """List files and subdirectories in the given directory.

    Args:
        path: Directory path to list, use '.' for current directory
    """
    try:
        p = Path(path)
        if not p.is_dir():
            return f"Error: '{path}' is not a directory"
        entries = sorted(p.iterdir(), key=lambda x: (x.is_file(), x.name))
        entries = [e for e in entries if not e.name.startswith(".")]
        if not entries:
            return f"Directory '{path}' is empty"
        lines = []
        for entry in entries:
            if entry.is_dir():
                lines.append(f"  [DIR]  {entry.name}/")
            else:
                size = entry.stat().st_size
                if size < 1024:
                    sz = f"{size}B"
                elif size < 1024 * 1024:
                    sz = f"{size / 1024:.1f}KB"
                else:
                    sz = f"{size / 1024 / 1024:.1f}MB"
                lines.append(f"  [FILE] {entry.name} ({sz})")
        return f"Directory {path} ({len(lines)} items):\n" + "\n".join(lines)
    except PermissionError:
        return f"Error: permission denied for '{path}'"
    except Exception as e:
        return f"List directory failed: {e}"


@registry.tool(category="search")
def tool_grep_search(pattern: str, path: str) -> str:
    """Search for text pattern in file or directory. Supports regex.

    Args:
        pattern: Search pattern (regex supported)
        path: File or directory path to search
    """
    try:
        regex = re.compile(pattern, re.IGNORECASE)
    except re.error as e:
        return f"Error: invalid regex '{pattern}': {e}"

    p = Path(path)
    results = []
    max_results = 50

    def _search_file(fp: Path):
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
        _search_file(p)
    elif p.is_dir():
        for fp in sorted(p.rglob("*")):
            if fp.is_file() and not any(part.startswith(".") for part in fp.parts):
                _search_file(fp)
                if len(results) >= max_results:
                    break
    else:
        return f"Error: '{path}' does not exist"

    if not results:
        return f"No matches for '{pattern}'"
    header = f"Found {len(results)} match(es)"
    if len(results) >= max_results:
        header += f" (limit {max_results})"
    return header + ":\n" + "\n".join(results)


# Import web tool implementations after registry exists, then register them
# here so the webtools package can also be imported on its own.
from .webtools import tool_web_fetch as _tool_web_fetch  # noqa: E402
from .webtools import tool_web_search as _tool_web_search  # noqa: E402
from .webtools import tool_web_crawl as _tool_web_crawl  # noqa: E402

tool_web_crawl = registry.tool(category="web")(_tool_web_crawl)
tool_web_fetch = registry.tool(category="web")(_tool_web_fetch)
tool_web_search = registry.tool(category="web")(_tool_web_search)
