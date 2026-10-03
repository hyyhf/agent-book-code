"""
FunHarness - Permission Management & Approval Flow

Three-mode permission system, path/command policies, sandbox executor.
"""
import os
import platform
import re
import shlex
import subprocess
import sys
import tempfile
import threading
import time
import ctypes
from enum import Enum
from pathlib import Path

from .command_shells import CommandShell, powershell_argv, resolve_shell


class PermissionMode(Enum):
    AUTO = "auto"
    SUGGEST = "suggest"
    APPROVE = "approve"


RISK_LEVELS = {
    "read": [
        "tool_read_file", "tool_find_files", "tool_list_directory", "tool_grep_search",
        "tool_read_memory", "tool_search_memory", "tool_list_skills",
        "tool_load_skill", "tool_view_tasks", "tool_next_task",
        "tool_read_progress", "tool_background_status", "tool_web_fetch",
        "tool_task_get", "tool_task_list", "tool_runtime_status",
        "tool_runtime_output", "tool_runtime_wait", "tool_schedule_list", "tool_team_list",
        "tool_team_inbox", "tool_list_attachments", "tool_read_attachment",
    ],
    "write": [
        "tool_write_file", "tool_replace_in_file", "tool_save_memory",
        "tool_complete_task", "tool_fail_task",
        "tool_task_create", "tool_task_update", "tool_schedule_create",
        "tool_schedule_delete", "tool_team_create", "tool_team_send",
        "tool_runtime_cancel",
    ],
    "execute": [
        "tool_run_command", "tool_runtime_run", "tool_subagent_run",
        "tool_team_delegate",
    ],
    "web": ["tool_web_search", "tool_web_crawl"],
}


def classify_risk(tool_name: str) -> str:
    for level, tools in RISK_LEVELS.items():
        if tool_name in tools:
            return level
    return "execute"


def needs_approval(tool_name: str, mode: PermissionMode) -> bool:
    if mode == PermissionMode.AUTO:
        return False
    if mode == PermissionMode.APPROVE:
        return True
    return classify_risk(tool_name) not in ("read", "web")


class PathPolicy:
    def __init__(self, allowed_dirs=None, denied_dirs=None):
        if allowed_dirs is None:
            allowed_dirs = [os.getcwd()]
        self.allowed = [Path(d).resolve() for d in allowed_dirs]
        default_denied = [
            os.path.expanduser(p) for p in ["~/.ssh", "~/.aws", "~/.gnupg", "~/.config"]
        ]
        self.denied = [Path(d).resolve() for d in (denied_dirs or default_denied)]

    def check(self, filepath: str) -> tuple[bool, str]:
        target = Path(filepath).resolve()
        for d in self.denied:
            if target == d or d in target.parents:
                return False, f"Path '{filepath}' is in protected directory '{d}'"
        for d in self.allowed:
            if target == d or d in target.parents:
                return True, "Allowed"
        return False, f"Path '{filepath}' is outside allowed directories"


DEFAULT_BLACKLIST = [
    "rm -rf /", "rm -rf ~", "rm -rf /*", "mkfs", "dd if=",
    ":(){:|:&};:", "shutdown", "reboot", "halt", "poweroff",
    "format", "del /f /s /q", "rd /s /q",
]
DEFAULT_WHITELIST = [
    "ls", "dir", "cat", "type", "head", "tail", "echo", "pwd", "cd",
    "git status", "git log", "git diff", "git branch",
    "python", "uv", "pip", "node", "npm", "grep", "find", "wc", "sort", "uniq",
]


class CommandPolicy:
    def __init__(self, whitelist=None, blacklist=None):
        self.whitelist = whitelist or DEFAULT_WHITELIST
        self.blacklist = blacklist or DEFAULT_BLACKLIST

    def check(self, command: str) -> tuple[str, str]:
        cmd_lower = command.lower().strip()
        for pattern in self.blacklist:
            matches = pattern.lower() in cmd_lower
            if pattern.lower() == "format":
                # Disk formatting remains blocked; PowerShell's Format-Table,
                # Format-List, etc. are ordinary output formatting commands.
                matches = bool(re.search(r'(?<![\w.-])format(?:\.exe)?(?![\w.-])', cmd_lower))
            if matches:
                return "deny", f"Command contains dangerous pattern '{pattern}'"
        for prefix in self.whitelist:
            if cmd_lower.startswith(prefix.lower()):
                return "allow", f"Command '{prefix}...' is whitelisted"
        return "ask", "Command not whitelisted, requires approval"


class PermissionManager:
    def __init__(self, mode=PermissionMode.SUGGEST, path_policy=None, command_policy=None):
        self.mode = mode
        self.path_policy = path_policy or PathPolicy()
        self.command_policy = command_policy or CommandPolicy()

    def check_tool_call(self, tool_name: str, arguments: dict) -> tuple[str, str]:
        file_tools = {
            "tool_read_file": "path", "tool_write_file": "path",
            "tool_find_files": "path",
            "tool_replace_in_file": "path", "tool_list_directory": "path",
            "tool_grep_search": "path",
        }
        if tool_name in file_tools:
            filepath = arguments.get(file_tools[tool_name]) or "."
            if filepath:
                allowed, reason = self.path_policy.check(filepath)
                if not allowed:
                    return "deny", reason

        if tool_name in ("tool_run_command", "tool_runtime_run"):
            cmd = arguments.get("command", "")
            level, reason = self.command_policy.check(cmd)
            if level == "deny":
                return "deny", reason
            if level == "allow" and self.mode == PermissionMode.AUTO:
                return "allow", reason

        if needs_approval(tool_name, self.mode):
            risk = classify_risk(tool_name)
            return "ask", f"Tool '{tool_name}' risk='{risk}', mode='{self.mode.value}' requires approval"
        return "allow", "Authorized"


# ---- Danger Detection ----

DANGEROUS_FILE_PATTERNS = [".env", ".gitignore", "id_rsa", ".bashrc", ".zshrc", ".profile", "passwd", "shadow"]
DANGEROUS_COMMAND_PATTERNS = [
    "| sh", "| bash", "| python", "&& chmod", "eval(", "exec(",
    "base64 -d", "base64 --decode", "chmod 777", "sudo ",
]


def detect_danger(tool_name: str, arguments: dict) -> tuple[bool, str]:
    if tool_name in ("tool_write_file", "tool_replace_in_file"):
        filename = Path(arguments.get("path", "")).name.lower()
        for pattern in DANGEROUS_FILE_PATTERNS:
            if pattern in filename:
                return True, f"Target file '{filename}' is sensitive"
    if tool_name == "tool_write_file":
        content = arguments.get("content", "")
        if any(kw in content.lower() for kw in ["api_key", "secret", "password", "token", "private_key"]):
            return True, "Content may contain credentials"
    if tool_name in ("tool_run_command", "tool_runtime_run"):
        cmd = arguments.get("command", "").lower()
        for pattern in DANGEROUS_COMMAND_PATTERNS:
            if pattern in cmd:
                return True, f"Command contains suspicious pattern '{pattern}'"
    return False, ""


# ---- Sandbox Executor ----

class _BoundedBytes:
    """Bound memory while retaining both startup diagnostics and recent output."""
    def __init__(self, limit: int):
        self.limit = max(2, limit)
        self.total = 0
        self.head = bytearray()
        self.tail = bytearray()

    def append(self, chunk: bytes) -> None:
        self.total += len(chunk)
        head_size = self.limit // 2
        take = min(len(chunk), head_size - len(self.head))
        self.head.extend(chunk[:take])
        self.tail.extend(chunk[take:])
        del self.tail[:max(0, len(self.tail) - (self.limit - head_size))]

    def data(self) -> bytes:
        marker = b"\n...(truncated middle of output)...\n" if self.truncated else b""
        return bytes(self.head) + marker + bytes(self.tail)

    @property
    def truncated(self) -> bool:
        return self.total > self.limit


class SandboxExecutor:
    FILTERED_ENV_VARS = [
        "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "GITHUB_TOKEN",
        "OPENAI_API_KEY", "DATABASE_URL", "SECRET_KEY",
    ]

    def __init__(self, work_dir=None, timeout=30, max_output=10000, shell="default"):
        self.work_dir = work_dir or os.getcwd()
        self.timeout = timeout
        self.max_output = max_output
        self.shell = shell
        self._resolved_shell = None
        self._script_path = None
        self._process: subprocess.Popen | None = None
        self._process_lock = threading.Lock()
        self._interrupted = threading.Event()
        self.outcome = "queued"
        self.exit_code = None
        self.pid = None
        self.output_bytes = 0
        self.last_output_at = 0.0

    def _build_safe_env(self) -> dict:
        env = os.environ.copy()
        for var in self.FILTERED_ENV_VARS:
            env.pop(var, None)
        env.setdefault("PYTHONUNBUFFERED", "1")
        env.setdefault("GIT_TERMINAL_PROMPT", "0")
        return env

    def _build_popen_args(self, command: str) -> tuple[str | list[str], dict, Path]:
        effective_cwd = Path(self.work_dir)
        shell = self.shell if isinstance(self.shell, CommandShell) else resolve_shell(self.shell)
        self._resolved_shell = shell
        command_to_run = command
        if shell.is_powershell:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8-sig", suffix=".ps1",
                                             prefix="funharness-command-", delete=False) as script:
                self._script_path = Path(script.name)
                script.write(command)
            command_to_run = powershell_argv(shell, self._script_path)
        elif shell.name == "cmd":
            effective_cwd, command_to_run = _extract_windows_leading_cd(command, effective_cwd)
            command_to_run = (_windows_mkdir_argv(command_to_run)
                              or _windows_python_c_argv(command_to_run) or command_to_run)
        else:
            command_to_run = [shell.executable, "-c", command]
        return command_to_run, {
            "shell": isinstance(command_to_run, str),
            **({"executable": shell.executable} if isinstance(command_to_run, str) else {}),
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "bufsize": 0,
            "cwd": str(effective_cwd),
            "env": self._build_safe_env(),
        }, effective_cwd

    def interrupt(self) -> None:
        # Only signal here: the execution thread owns all process handles. This
        # makes GUI cancellation immediate and avoids concurrent close/kill races.
        self._interrupted.set()

    def execute(self, command: str, should_interrupt=None, on_progress=None) -> str:
        from .process_tree import ProcessTree
        import math

        tree = None
        proc = None
        stdout = _BoundedBytes(max(4096, self.max_output * 4))
        stderr = _BoundedBytes(max(4096, self.max_output * 4))
        started = time.monotonic()
        self.outcome = "running"

        def cancelled():
            return self._interrupted.is_set() or bool(should_interrupt and should_interrupt())

        def text_output():
            budget = self.max_output // 2 if stdout.total and stderr.total else self.max_output
            output = _limit_command_text(decode_process_output(stdout.data()), budget)
            if stderr.total:
                output += "\n[stderr]\n" + _limit_command_text(decode_process_output(stderr.data()), budget)
            return output

        def drain():
            # Both streams are nonblocking. Limit work per tick so flooding one
            # stream cannot starve stderr, deadline checks, or cancellation.
            for stream, sink in ((proc.stdout, stdout), (proc.stderr, stderr)):
                for _ in range(16):
                    try:
                        chunk = os.read(stream.fileno(), 65536)
                    except BlockingIOError:
                        break
                    except OSError as exc:
                        if getattr(exc, "winerror", None) in (109, 232):
                            break  # broken/empty Windows pipe
                        raise
                    if not chunk:
                        break
                    sink.append(chunk)
                    self.last_output_at = time.time()
            self.output_bytes = stdout.total + stderr.total

        try:
            if not isinstance(command, str) or not command.strip():
                raise ValueError("command must be a non-empty string")
            if self.timeout is not None and (not math.isfinite(self.timeout) or self.timeout <= 0):
                raise ValueError("timeout must be positive or None for an explicitly managed service")
            if cancelled():
                self.outcome = "cancelled"
                return "Interrupted: command stopped by user"
            popen_command, popen_kwargs, effective_cwd = self._build_popen_args(command)
            tree = ProcessTree()
            proc = tree.start(popen_command, popen_kwargs)
            self.pid = proc.pid
            with self._process_lock:
                self._process = proc
            for stream in (proc.stdout, proc.stderr):
                os.set_blocking(stream.fileno(), False)
            next_progress = 0.0
            while True:
                drain()
                now = time.monotonic()
                if cancelled():
                    self.outcome = "cancelled"
                    break
                if proc.poll() is not None:
                    self.exit_code = proc.returncode
                    self.outcome = "done" if proc.returncode == 0 else "failed"
                    break
                if self.timeout is not None and now - started >= self.timeout:
                    self.outcome = "timed_out"
                    break
                if on_progress and now >= next_progress:
                    on_progress(text_output(), self)
                    next_progress = now + 0.5
                self._interrupted.wait(0.05)

            # A shell exiting does not transfer ownership of descendants. They
            # are cleaned up here; services must keep their top-level process alive.
            tree.close()
            tree = None
            drain()
            self.exit_code = proc.returncode
            output = text_output()
            if self.outcome == "cancelled":
                return "Interrupted: command stopped by user" + ("\n" + output if output else "")
            if self.outcome == "timed_out":
                return f"Error: command timed out ({self.timeout:g}s)" + ("\n" + output if output else "")
            if not output and not self._resolved_shell.is_powershell:
                output = format_command_output(command, effective_cwd, "", "", self.max_output)
            output = output or "(no output)"
            return f"[exit={self.exit_code}]\n{output}"
        except Exception as exc:
            self.outcome = "failed"
            return f"Execution failed: {exc}\n{text_output()}".rstrip()
        finally:
            try:
                if tree:
                    tree.close()
            finally:
                try:
                    if proc:
                        # Unbuffered pipes owned by this thread: close never
                        # waits for a blocked reader or a descendant's EOF.
                        for stream in (proc.stdout, proc.stderr):
                            if stream:
                                stream.close()
                finally:
                    with self._process_lock:
                        self._process = None
                    if self._script_path:
                        self._script_path.unlink(missing_ok=True)
                        self._script_path = None


def _limit_command_text(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    half = max(1, limit // 2)
    return text[:half] + f"\n...(truncated, total {len(text)} chars)...\n" + text[-half:]


def _windows_pid_exists(pid: int) -> bool:
    if platform.system() != "Windows" or pid <= 0:
        return False
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    STILL_ACTIVE = 259
    kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
    kernel32.GetExitCodeProcess.restype = ctypes.c_int
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = ctypes.c_int
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not handle:
        return False
    try:
        exit_code = ctypes.c_uint32()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return False
        return exit_code.value == STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def _windows_mkdir_argv(command: str) -> list[str] | None:
    # Preserve mkdir -p/-Force compatibility, but do the filesystem work inside
    # the owned subprocess so cancellation and timeout apply to it as well.
    if _has_unquoted_shell_operator(command):
        return None
    try:
        argv = shlex.split(command, posix=False)
    except ValueError:
        return None
    if not argv or Path(argv[0].strip('"')).name.lower() not in {"mkdir", "md"}:
        return None
    targets = [token.strip('"').strip("'") for token in argv[1:]
               if token.lower() not in {"-p", "--parents", "-force"}]
    if not targets:
        return None
    script = ("import sys\nfrom pathlib import Path\n"
              "for target in sys.argv[1:]:\n"
              " p=Path(target); p.mkdir(parents=True, exist_ok=True); print('Created directory',p.resolve())\n")
    return [sys.executable, "-c", script, *targets]


def _extract_windows_leading_cd(command: str, work_dir: Path) -> tuple[Path, str]:
    left, right = _split_unquoted_operator(command, "&&")
    if right is None:
        return work_dir, command

    match = re.match(r'^\s*cd(?:\s+/d)?\s+(.+?)\s*$', left, flags=re.IGNORECASE)
    if not match:
        return work_dir, command

    raw_path = match.group(1).strip().strip('"').strip("'")
    if not raw_path:
        return work_dir, command

    path = Path(raw_path)
    if not path.is_absolute():
        path = work_dir / path
    if not path.is_dir():
        return work_dir, command

    return path.resolve(), right.lstrip()


def _windows_python_c_argv(command: str) -> list[str] | None:
    if _has_unquoted_shell_operator(command):
        return None
    try:
        argv = shlex.split(command, posix=True)
    except ValueError:
        return None
    if not argv or "-c" not in argv:
        return None

    executable = Path(argv[0].strip('"')).name.lower()
    if executable not in {"python", "python.exe", "py", "py.exe"}:
        return None

    c_index = argv.index("-c")
    if c_index + 1 >= len(argv):
        return None
    return argv


def _has_unquoted_shell_operator(command: str) -> bool:
    return any(
        _split_unquoted_operator(command, operator)[1] is not None
        for operator in ("&&", "||", "|", ">", "<")
    )


def _split_unquoted_operator(command: str, operator: str) -> tuple[str, str | None]:
    in_single = False
    in_double = False
    i = 0
    while i <= len(command) - len(operator):
        ch = command[i]
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            backslashes = 0
            j = i - 1
            while j >= 0 and command[j] == "\\":
                backslashes += 1
                j -= 1
            if backslashes % 2 == 0:
                in_double = not in_double

        if not in_single and not in_double and command.startswith(operator, i):
            return command[:i], command[i + len(operator):]
        i += 1
    return command, None


def decode_process_output(data: bytes | str | None) -> str:
    """Decode subprocess output without losing UTF-8 output on GBK Windows shells."""
    if not data:
        return ""
    if isinstance(data, str):
        return data
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        encoding = "mbcs" if platform.system() == "Windows" else "utf-8"
        try:
            return data.decode(encoding, errors="replace")
        except LookupError:
            return data.decode("utf-8", errors="replace")


def format_command_output(command: str, work_dir: str | Path,
                          stdout: str | None, stderr: str | None,
                          max_output: int) -> str:
    """Format captured command output, including simple redirected stdout files."""
    parts = []
    if stdout:
        parts.append(stdout)
    if stderr:
        parts.append(f"[stderr]\n{stderr}")

    if not parts:
        parts.extend(_redirected_output_previews(command, work_dir, max_output))

    output = "\n".join(parts) if parts else "(no output)"
    if len(output) > max_output:
        output = output[:max_output] + f"\n...(truncated, total {len(output)} chars)"
    return output


def _redirected_output_previews(command: str, work_dir: str | Path,
                                max_output: int) -> list[str]:
    previews = []
    for stream_name, path in _find_redirected_output_paths(command, work_dir):
        if not path.is_file():
            continue
        try:
            size = path.stat().st_size
            with path.open("rb") as stream:
                text = decode_process_output(stream.read(max_output * 4))
        except OSError:
            continue
        if not text:
            continue
        if len(text) > max_output:
            text = text[:max_output] + f"\n...(redirected output truncated, total {len(text)} chars)"
        previews.append(
            f"(no {stream_name} captured; shell redirected it to {path.name}, {size} bytes)\n{text}"
        )
    return previews


def _find_redirected_output_paths(command: str, work_dir: str | Path) -> list[tuple[str, Path]]:
    try:
        tokens = shlex.split(command, posix=(platform.system() != "Windows"))
    except ValueError:
        tokens = command.split()

    paths: list[tuple[str, Path]] = []
    operators = {
        ">": "stdout",
        "1>": "stdout",
        ">>": "stdout",
        "1>>": "stdout",
        "2>": "stderr",
        "2>>": "stderr",
    }
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token in operators and i + 1 < len(tokens):
            _append_redirect_path(paths, operators[token], tokens[i + 1], work_dir)
            i += 2
            continue

        matched = False
        for op, stream_name in sorted(operators.items(), key=lambda item: -len(item[0])):
            if token.startswith(op) and len(token) > len(op):
                _append_redirect_path(paths, stream_name, token[len(op):], work_dir)
                matched = True
                break
        i += 1

    return paths


def _append_redirect_path(paths: list[tuple[str, Path]], stream_name: str,
                          raw_path: str, work_dir: str | Path) -> None:
    if not raw_path or raw_path.startswith("&"):
        return
    cleaned = raw_path.strip().strip('"').strip("'")
    if not cleaned:
        return
    path = Path(cleaned)
    if not path.is_absolute():
        path = Path(work_dir) / path
    paths.append((stream_name, path))


# ---- Approval Flow ----

class ApprovalFlow:
    """Approval flow controller - in TUI mode, approval is handled by callbacks."""

    def __init__(self, permission_manager: PermissionManager, approval_callback=None):
        self.pm = permission_manager
        self.sandbox = SandboxExecutor()
        self._always_allowed: set[str] = set()
        self._approval_callback = approval_callback

    def pre_tool_check(self, tool_name: str, arguments: dict) -> tuple[bool, str]:
        if tool_name in self._always_allowed:
            return True, "Permanently authorized"

        decision, reason = self.pm.check_tool_call(tool_name, arguments)
        if decision == "deny":
            return False, reason

        if decision == "allow":
            is_dangerous, danger_reason = detect_danger(tool_name, arguments)
            if is_dangerous:
                decision = "ask"
                reason = danger_reason

        if decision == "ask":
            if self._approval_callback:
                approved, choice = self._approval_callback(tool_name, arguments, reason)
            else:
                # Auto-approve if no callback (headless mode)
                approved, choice = True, "once"
            if not approved:
                return False, "User denied"
            if choice == "always":
                self._always_allowed.add(tool_name)
            return True, f"User approved ({choice})"

        return True, reason
