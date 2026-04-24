"""
FunHarness - Permission Management & Approval Flow

Three-mode permission system, path/command policies, sandbox executor.
"""
import os
import subprocess
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
    ],
    "write": [
        "tool_write_file", "tool_replace_in_file", "tool_save_memory",
        "tool_complete_task", "tool_fail_task",
    ],
    "execute": ["tool_run_command"],
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
            if target == d or d in target.parents or target in d.parents:
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

    def execute(self, command: str) -> str:
        try:
            result = subprocess.run(
                command, shell=True, capture_output=True, text=True,
                timeout=self.timeout, cwd=self.work_dir, env=self._build_safe_env(),
            )
            parts = []
            if result.stdout:
                parts.append(result.stdout)
            if result.stderr:
                parts.append(f"[stderr]\n{result.stderr}")
            output = "\n".join(parts) if parts else "(no output)"
            if len(output) > self.max_output:
                output = output[:self.max_output] + f"\n...(truncated, total {len(output)} chars)"
            return f"[exit={result.returncode}]\n{output}"
        except subprocess.TimeoutExpired:
            return f"Error: command timed out ({self.timeout}s)"
        except Exception as e:
            return f"Execution failed: {e}"


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
