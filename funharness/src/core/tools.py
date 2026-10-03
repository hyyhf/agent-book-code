"""
FunHarness - Tool Registry & Core Tools

Tool registry with decorator-based registration, core local tools,
and web tools registered from the webtools package.
"""
import fnmatch
import inspect
import json
import os
import re
from pathlib import Path
from types import UnionType
from typing import Any, Literal, NotRequired, TypedDict, Union, get_args, get_origin, get_type_hints, is_typeddict

from .attachments import DEFAULT_ATTACHMENT_MAX_CHARS, read_document
from .content import ToolResult
from .file_editing import EditError, edit_file, normalize_edits, read_text_file, record_missing, _error
from .media import is_image_file
from .runtime import current_commands, command_timeout, wait_seconds
from .command_shells import ShellName

# ----------------------------------------------------------------
#  ToolRegistry
# ----------------------------------------------------------------

_TYPE_MAP = {str: "string", int: "integer", float: "number", bool: "boolean"}


def _schema_for_type(ptype: Any) -> dict:
    origin = get_origin(ptype)
    args = get_args(ptype)

    if origin is Literal:
        return {"type": _TYPE_MAP.get(type(args[0]), "string"), "enum": list(args)}

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
                if param.default is not inspect.Parameter.empty:
                    prop["default"] = param.default
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


class ReplacementSpec(TypedDict):
    old_text: str
    new_text: str
    replace_all: NotRequired[bool]
    expected_count: NotRequired[int]


# --- File Tools ---

@registry.tool(category="file")
def tool_read_file(
    path: str,
    max_chars: int = DEFAULT_ATTACHMENT_MAX_CHARS,
    detail: str = "auto",
    start_line: int = 1,
    limit: int | None = None,
) -> str | ToolResult:
    """Read text, documents or actual image pixels. For code, prefer a small line window.

    Args:
        path: File path to read (relative or absolute)
        max_chars: Maximum text characters, defaults to 300000; does not truncate images
        detail: Image detail: auto, low, high, or original; ignored for text
        start_line: First text line to read, 1-based; ignored for images/documents
        limit: Maximum text lines to return; omit to read to the character limit
    """
    try:
        # Structured documents retain extraction; source/CSV text must stay literal
        # so a displayed excerpt can be used directly as an edit anchor.
        from .file_editing import _target
        target, _ = _target(path)
        if is_image_file(target) or target.suffix.lower() in {".pdf", ".docx", ".xlsx", ".doc", ".xls"}:
            return read_document(target, max_chars=max_chars, detail=detail)
        return read_text_file(path, max_chars, start_line, limit)
    except FileNotFoundError:
        record_missing(path)
        return f"Error: file '{path}' not found"
    except PermissionError:
        return f"Error: permission denied for '{path}'"
    except Exception as e:
        return f"Read failed: {e}"


@registry.tool(category="file")
def tool_write_file(path: str, content: str, expected_revision: str | None = None) -> ToolResult:
    """Atomically create or overwrite UTF-8 text. Read existing files first; prefer batch edits.

    Args:
        path: Target file path
        content: Full text content; use tool_replace_in_file for localized changes
        expected_revision: Optional revision from a prior read; stale revisions are rejected
    """
    if not isinstance(content, str):
        return _error(EditError("INVALID_ARGUMENT", "content must be a string"), path)
    return edit_file(path, [], expected_revision, content=content)


@registry.tool(category="file")
def tool_replace_in_file(
    path: str,
    old_text: str | None = None,
    new_text: str | None = None,
    replacements: list[ReplacementSpec] | None = None,
    replace_all: bool = False,
    expected_count: int | None = None,
    expected_revision: str | None = None,
) -> ToolResult:
    """Edit UTF-8 text in one atomic batch. Read first. Each old_text must be unique.

    Match all entries against the original file; reject overlaps and commit once.
    Keep anchors short but unique. LF/CRLF differences are handled automatically.
    No other whitespace or Unicode fuzzing is performed. Re-read on FILE_CHANGED.

    Args:
        path: Target file path
        old_text: Exact non-empty anchor for a single edit; omit when using replacements
        new_text: Required for a single edit; explicitly use an empty string to delete
        replacements: Batch of old_text/new_text objects; optional per-edit replace_all and expected_count
        replace_all: Single edit only: explicitly replace every occurrence; default false
        expected_count: Single edit only: require this exact match count; counts above 1 require replace_all
        expected_revision: Optional read revision; the agent's observed version is also checked automatically
    """
    try:
        specs = normalize_edits(old_text, new_text, replacements, replace_all, expected_count)
        return edit_file(path, specs, expected_revision)
    except EditError as exc:
        return _error(exc, path)


# --- System Tools ---

@registry.tool(category="system")
def tool_run_command(command: str, timeout: int = 300, yield_time_ms: int = 1000,
                     background: bool = False, shell: ShellName = "default") -> str:
    """Run a managed non-interactive shell command; return output or a running task ID.

    Unknown duration is fine: unfinished commands keep running after the short
    wait. Never rerun a running command; use tool_runtime_wait/output/cancel.
    For long-lived services use background=true, timeout=0 and keep the server
    process in the foreground (no start, nohup, background suffix, or detached terminal).
    Select shell=powershell for Windows PowerShell 5.1 or shell=pwsh for
    PowerShell 7; send native script text, including multiline code. stdin is closed.

    Args:
        command: Shell command string to execute
        timeout: Total lifetime in seconds, default 300; 1..86400, or 0 only with background=true
        yield_time_ms: Wait up to 0..10000 ms for initial output, independently of timeout
        background: Explicit background service/job; return immediately and retain until cancelled or backend shutdown
        shell: default preserves Windows cmd / POSIX sh; cmd, powershell (Windows 5.1), pwsh (7+), sh, or bash. Do not mix shell syntaxes. PowerShell cmdlet errors stop the script; last native exit code is preserved.
    """
    command_timeout(timeout, background=background)
    wait_seconds(yield_time_ms, maximum=10000)
    runtime, interrupted = current_commands()
    if interrupted and interrupted():
        return "Interrupted: command not started"
    runtime_id = runtime.submit_command(command, timeout=timeout, background=background,
                                        should_interrupt=None if background else interrupted, shell=shell)
    return runtime.wait(runtime_id, 0 if background else yield_time_ms, should_interrupt=interrupted)


@registry.tool(category="system")
def tool_runtime_run(command: str, description: str = "", timeout: int = 300,
                     shell: ShellName = "default") -> str:
    """Start a managed background command and immediately return its runtime ID.

    Args:
        command: Non-interactive shell command; keep services in foreground, do not detach
        description: Short description
        timeout: Lifetime seconds (1..86400); 0 for a service stopped explicitly or on backend shutdown
        shell: default (Windows cmd / POSIX sh), cmd, powershell (Windows 5.1), pwsh (7+), sh, or bash
    """
    runtime, interrupted = current_commands()
    if interrupted and interrupted():
        return "Interrupted: command not started"
    runtime_id = runtime.submit_command(command, description, timeout, background=True, shell=shell)
    return runtime.wait(runtime_id, 0, should_interrupt=interrupted)


@registry.tool(category="system")
def tool_runtime_status(runtime_id: str = "") -> str:
    """Show managed commands, elapsed time, process IDs and completion status.

    Args:
        runtime_id: Optional runtime task ID; omit to list tasks
    """
    runtime, _ = current_commands()
    if not runtime_id:
        return runtime.summary()
    task = runtime.get(runtime_id)
    return json.dumps(task.to_dict(), ensure_ascii=False, indent=2) if task else f"Unknown runtime task: {runtime_id}"


@registry.tool(category="system")
def tool_runtime_output(runtime_id: str) -> str:
    """Read the current bounded output snapshot, including output from running tasks.

    Args:
        runtime_id: Runtime task ID
    """
    runtime, _ = current_commands()
    return runtime.wait(runtime_id, 0)


@registry.tool(category="system")
def tool_runtime_wait(runtime_id: str, yield_time_ms: int = 10000) -> str:
    """Wait for an existing command without restarting it; return status and output.

    Running is not success. Wait before dependent work or reporting completion.
    Repeated waits never extend the command's total timeout.

    Args:
        runtime_id: Runtime task ID returned by a command tool
        yield_time_ms: Wait between 0 and 30000 ms; default 10000
    """
    runtime, interrupted = current_commands()
    return runtime.wait(runtime_id, yield_time_ms, should_interrupt=interrupted)


@registry.tool(category="system")
def tool_runtime_cancel(runtime_id: str) -> str:
    """Request termination of a command and its descendants; wait to confirm exit.

    Args:
        runtime_id: Runtime task ID
    """
    runtime, _ = current_commands()
    return runtime.cancel(runtime_id)


# --- Search Tools ---

_IGNORED_SEARCH_DIRS = {
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    "node_modules",
    "dist",
    "build",
    ".funharness",
}


def _is_ignored_search_part(part: str) -> bool:
    return part in _IGNORED_SEARCH_DIRS or part.startswith(".")


def _matches_glob(path: Path, pattern: str) -> bool:
    rel = path.as_posix()
    return fnmatch.fnmatch(rel, pattern) or (
        pattern.startswith("**/") and fnmatch.fnmatch(rel, pattern[3:])
    )


def _iter_search_files(root: Path, pattern: str):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(
            name for name in dirnames
            if not _is_ignored_search_part(name)
        )

        base = Path(dirpath)
        for filename in sorted(filenames):
            if _is_ignored_search_part(filename):
                continue
            fp = base / filename
            try:
                rel = fp.relative_to(root)
            except ValueError:
                continue
            if _matches_glob(rel, pattern):
                yield fp, rel


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
def tool_find_files(
    pattern: str = "**/*",
    path: str = ".",
    max_results: int = 200,
) -> str:
    """Find files by glob pattern under a directory.

    Args:
        pattern: Glob pattern, for example '**/*.py', '**/*test*.py', or 'README*'
        path: Root directory to search from, use '.' for current directory
        max_results: Maximum number of matched files to return
    """
    try:
        root = Path(path)
        if not root.exists():
            return f"Error: '{path}' does not exist"
        if not root.is_dir():
            return f"Error: '{path}' is not a directory"
        if max_results < 1:
            return "Error: max_results must be at least 1"

        matches = []
        for _fp, rel in _iter_search_files(root, pattern):
            matches.append(rel.as_posix())
            if len(matches) >= max_results:
                break

        if not matches:
            return f"No files matched pattern '{pattern}' under '{path}'"

        header = f"Found {len(matches)} file(s)"
        if len(matches) >= max_results:
            header += f" (limit {max_results})"
        return header + ":\n" + "\n".join(matches)
    except Exception as e:
        return f"Find files failed: {e}"


@registry.tool(category="search")
def tool_grep_search(
    pattern: str,
    path: str = ".",
    glob: str = "**/*",
    ignore_case: bool = True,
    literal: bool = False,
    max_results: int = 80,
) -> str:
    """Search for text pattern in file or directory. Supports regex.

    Args:
        pattern: Search pattern, regex by default or plain text if literal=True
        path: File or directory path to search
        glob: Glob filter for files, for example '**/*.py'
        ignore_case: Whether to ignore case
        literal: Treat pattern as plain text instead of regex
        max_results: Maximum number of matched lines to return
    """
    try:
        flags = re.IGNORECASE if ignore_case else 0
        regex = re.compile(re.escape(pattern) if literal else pattern, flags)
    except re.error as e:
        return f"Error: invalid regex '{pattern}': {e}"

    p = Path(path)
    results = []
    if max_results < 1:
        return "Error: max_results must be at least 1"

    def _search_file(fp: Path):
        try:
            text = fp.read_text(encoding="utf-8")
        except (UnicodeDecodeError, PermissionError, OSError):
            return
        for i, line in enumerate(text.splitlines(), 1):
            if regex.search(line):
                results.append(f"  {fp}:{i}: {line.rstrip()}")
                if len(results) >= max_results:
                    return

    if p.is_file():
        _search_file(p)
    elif p.is_dir():
        for fp, _rel in _iter_search_files(p, glob):
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
