"""
FunHarness - Permission Management & Approval Flow

Three-mode permission system, path/command policies, sandbox executor.
"""
import os
import platform
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
        "tool_team_inbox",
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
                "text": True,
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
                        stdout, stderr = proc.communicate(timeout=0.2)
                        break
                    except subprocess.TimeoutExpired:
                        continue
            except subprocess.TimeoutExpired:
                self._kill_tree(proc)
                proc.wait(timeout=5)
                return f"Error: command timed out ({self.timeout}s)"

            parts = []
            if stdout:
                parts.append(stdout)
            if stderr:
                parts.append(f"[stderr]\n{stderr}")
            output = "\n".join(parts) if parts else "(no output)"
            if len(output) > self.max_output:
                output = output[:self.max_output] + f"\n...(truncated, total {len(output)} chars)"
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
