"""
FunHarness - Permission Management & Approval Flow

Three-mode permission system, path/command policies, sandbox executor.
"""
import os
import platform
import shlex
import subprocess
import time
from enum import Enum
from pathlib import Path


class PermissionMode(Enum):
    AUTO = "auto"
    SUGGEST = "suggest"
    APPROVE = "approve"


RISK_LEVELS = {
    "read": [
        "tool_read_file", "tool_list_directory", "tool_grep_search",
        "tool_read_memory", "tool_search_memory", "tool_list_skills",
        "tool_load_skill", "tool_view_tasks", "tool_next_task",
        "tool_read_progress", "tool_background_status", "tool_web_fetch",
        "tool_task_get", "tool_task_list", "tool_runtime_status",
        "tool_runtime_output", "tool_schedule_list", "tool_team_list",
        "tool_team_inbox", "tool_list_attachments", "tool_read_attachment",
    ],
    "write": [
        "tool_write_file", "tool_replace_in_file", "tool_save_memory",
        "tool_complete_task", "tool_fail_task",
        "tool_task_create", "tool_task_update", "tool_schedule_create",
        "tool_schedule_delete", "tool_team_create", "tool_team_send",
    ],
    "execute": [
        "tool_run_command", "tool_runtime_run", "tool_subagent_run",
        "tool_team_delegate",
    ],
    "web": ["tool_web_search"],
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
            if pattern.lower() in cmd_lower:
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
            "tool_replace_in_file": "path", "tool_list_directory": "path",
            "tool_grep_search": "path",
        }
        if tool_name in file_tools:
            filepath = arguments.get(file_tools[tool_name], "")
            if filepath:
                allowed, reason = self.path_policy.check(filepath)
                if not allowed:
                    return "deny", reason

        if tool_name == "tool_run_command":
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
    if tool_name == "tool_run_command":
        cmd = arguments.get("command", "").lower()
        for pattern in DANGEROUS_COMMAND_PATTERNS:
            if pattern in cmd:
                return True, f"Command contains suspicious pattern '{pattern}'"
    return False, ""


# ---- Sandbox Executor ----

class SandboxExecutor:
    FILTERED_ENV_VARS = [
        "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "GITHUB_TOKEN",
        "OPENAI_API_KEY", "DATABASE_URL", "SECRET_KEY",
    ]

    def __init__(self, work_dir=None, timeout=30, max_output=10000):
        self.work_dir = work_dir or os.getcwd()
        self.timeout = timeout
        self.max_output = max_output

    def _build_safe_env(self) -> dict:
        env = os.environ.copy()
        for var in self.FILTERED_ENV_VARS:
            env.pop(var, None)
        return env

    def execute(self, command: str, should_interrupt=None) -> str:
        try:
            popen_kwargs = {
                "shell": True,
                "stdout": subprocess.PIPE,
                "stderr": subprocess.PIPE,
                "cwd": self.work_dir,
                "env": self._build_safe_env(),
            }
            if platform.system() != "Windows":
                popen_kwargs["start_new_session"] = True

            proc = subprocess.Popen(command, **popen_kwargs)
            deadline = None if self.timeout is None else time.monotonic() + self.timeout
            try:
                while True:
                    if should_interrupt and should_interrupt():
                        self._kill_tree(proc)
                        proc.wait(timeout=5)
                        return "Interrupted: command stopped by user"
                    if deadline is not None and time.monotonic() >= deadline:
                        raise subprocess.TimeoutExpired(command, self.timeout)
                    try:
                        stdout_bytes, stderr_bytes = proc.communicate(timeout=0.2)
                        break
                    except subprocess.TimeoutExpired:
                        continue
            except subprocess.TimeoutExpired:
                self._kill_tree(proc)
                proc.wait(timeout=5)
                return f"Error: command timed out ({self.timeout}s)"

            stdout = decode_process_output(stdout_bytes)
            stderr = decode_process_output(stderr_bytes)
            output = format_command_output(
                command, self.work_dir, stdout, stderr, self.max_output
            )
            return f"[exit={proc.returncode}]\n{output}"
        except Exception as e:
            return f"Execution failed: {e}"

    @staticmethod
    def _kill_tree(proc: subprocess.Popen):
        """Kill the entire process tree (not just the shell parent)."""
        try:
            if platform.system() == "Windows":
                # taskkill /T kills the whole tree, /F forces termination
                subprocess.run(
                    ["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                    capture_output=True, timeout=5,
                )
            else:
                import signal
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass


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
            text = path.read_text(encoding="utf-8", errors="replace")
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
