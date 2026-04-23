"""
7.2 - 多级权限模式

实现 Agent 的分级权限控制:
- 三种运行模式: auto(全自动), suggest(建议), approve(审批)
- 路径级文件访问控制(允许/拒绝目录列表)
- Shell 命令的黑白名单机制

运行方式:
    uv run python chapter07/permission_manager.py
"""
import os
import re
import shlex
from enum import Enum
from pathlib import Path


# =============================================================
#  7.2.1 三种运行模式
# =============================================================

class PermissionMode(Enum):
    """Agent 的三种权限运行模式。"""
    AUTO = "auto"          # 全自动: 所有操作直接执行
    SUGGEST = "suggest"    # 建议模式: 只读操作自动执行, 写操作需确认
    APPROVE = "approve"    # 审批模式: 所有工具调用都需用户确认


# 工具的风险等级分类
RISK_LEVELS = {
    # 只读操作, 风险低
    "read": [
        "tool_read_file",
        "tool_list_directory",
        "tool_grep_search",
        "tool_read_memory",
        "tool_search_memory",
        "tool_list_skills",
        "tool_load_skill",
    ],
    # 写操作, 风险中
    "write": [
        "tool_write_file",
        "tool_replace_in_file",
        "tool_save_memory",
    ],
    # 系统操作, 风险高
    "execute": [
        "tool_run_command",
    ],
}


def classify_risk(tool_name: str) -> str:
    """根据工具名称返回风险等级: read / write / execute。"""
    for level, tools in RISK_LEVELS.items():
        if tool_name in tools:
            return level
    return "execute"  # 未知工具按最高风险处理


def needs_approval(tool_name: str, mode: PermissionMode) -> bool:
    """判断在当前权限模式下, 某个工具调用是否需要用户审批。

    AUTO 模式: 永远不需要审批
    SUGGEST 模式: 只读操作自动放行, 写和执行需要审批
    APPROVE 模式: 所有操作都需要审批
    """
    if mode == PermissionMode.AUTO:
        return False
    if mode == PermissionMode.APPROVE:
        return True
    # SUGGEST 模式: 只有 read 级别自动放行
    risk = classify_risk(tool_name)
    return risk != "read"


# =============================================================
#  7.2.2 路径级文件访问控制
# =============================================================

class PathPolicy:
    """路径级访问控制策略。

    基于允许列表和拒绝列表来判断某个文件路径是否可被 Agent 访问。
    规则优先级: deny > allow。拒绝列表中的路径无论如何都不可访问。
    """

    def __init__(
        self,
        allowed_dirs: list[str] | None = None,
        denied_dirs: list[str] | None = None,
    ):
        # 默认允许当前工作目录及其子目录
        if allowed_dirs is None:
            allowed_dirs = [os.getcwd()]
        self.allowed = [Path(d).resolve() for d in allowed_dirs]

        # 默认拒绝敏感路径
        default_denied = [
            os.path.expanduser("~/.ssh"),
            os.path.expanduser("~/.aws"),
            os.path.expanduser("~/.gnupg"),
            os.path.expanduser("~/.config"),
        ]
        denied = denied_dirs if denied_dirs is not None else default_denied
        self.denied = [Path(d).resolve() for d in denied]

    def check(self, filepath: str) -> tuple[bool, str]:
        """检查文件路径是否允许访问。

        返回: (是否允许, 原因说明)
        """
        target = Path(filepath).resolve()

        # 优先检查拒绝列表
        for d in self.denied:
            if target == d or d in target.parents or target in d.parents:
                # target 在拒绝目录内, 或 target 就是拒绝路径
                if target == d or d in target.parents:
                    return False, f"路径 '{filepath}' 在受保护目录 '{d}' 中, 访问被拒绝"

        # 检查允许列表
        for d in self.allowed:
            if target == d or d in target.parents or target in d.parents:
                return True, "允许访问"

        return False, f"路径 '{filepath}' 不在允许的工作目录中"


# =============================================================
#  7.2.3 Shell 命令的黑白名单
# =============================================================

# 默认禁止执行的命令关键词(黑名单)
DEFAULT_COMMAND_BLACKLIST = [
    "rm -rf /",
    "rm -rf ~",
    "rm -rf /*",
    "mkfs",
    "dd if=",
    ":(){:|:&};:",     # fork bomb
    "shutdown",
    "reboot",
    "halt",
    "poweroff",
    "format",
    "del /f /s /q",
    "rd /s /q",
]

# 默认允许的命令前缀(白名单)
DEFAULT_COMMAND_WHITELIST = [
    "ls", "dir", "cat", "type", "head", "tail",
    "echo", "pwd", "cd",
    "git status", "git log", "git diff", "git branch",
    "python", "uv", "pip", "node", "npm",
    "grep", "find", "wc", "sort", "uniq",
]


class CommandPolicy:
    """Shell 命令的安全策略。

    通过黑名单拦截危险命令, 通过白名单快速放行安全命令。
    不在白名单中的命令需要根据权限模式决定是否需要审批。
    """

    def __init__(
        self,
        whitelist: list[str] | None = None,
        blacklist: list[str] | None = None,
    ):
        self.whitelist = whitelist or DEFAULT_COMMAND_WHITELIST
        self.blacklist = blacklist or DEFAULT_COMMAND_BLACKLIST

    def check(self, command: str) -> tuple[str, str]:
        """检查命令的安全级别。

        返回: (级别, 原因)
        - "deny": 命令被黑名单拦截, 必须拒绝
        - "allow": 命令在白名单中, 可以直接执行
        - "ask": 命令需要根据权限模式决定
        """
        cmd_lower = command.lower().strip()

        # 检查黑名单
        for pattern in self.blacklist:
            if pattern.lower() in cmd_lower:
                return "deny", f"命令包含危险操作 '{pattern}', 已被拦截"

        # 检查白名单
        for prefix in self.whitelist:
            if cmd_lower.startswith(prefix.lower()):
                return "allow", f"命令 '{prefix}...' 在白名单中"

        return "ask", "命令不在白名单中, 需要根据权限模式判断"


# =============================================================
#  权限管理器: 整合三层检查
# =============================================================

class PermissionManager:
    """统一的权限管理器, 整合运行模式、路径策略和命令策略。"""

    def __init__(
        self,
        mode: PermissionMode = PermissionMode.SUGGEST,
        path_policy: PathPolicy | None = None,
        command_policy: CommandPolicy | None = None,
    ):
        self.mode = mode
        self.path_policy = path_policy or PathPolicy()
        self.command_policy = command_policy or CommandPolicy()

    def check_tool_call(
        self, tool_name: str, arguments: dict
    ) -> tuple[str, str]:
        """综合检查一次工具调用是否允许执行。

        返回: (决策, 原因)
        - "allow": 允许直接执行
        - "deny": 拒绝执行
        - "ask": 需要用户确认
        """
        # 第一层: 路径级检查(针对文件操作工具)
        file_tools = {
            "tool_read_file": "path",
            "tool_write_file": "path",
            "tool_replace_in_file": "path",
            "tool_list_directory": "path",
            "tool_grep_search": "path",
        }
        if tool_name in file_tools:
            path_param = file_tools[tool_name]
            filepath = arguments.get(path_param, "")
            if filepath:
                allowed, reason = self.path_policy.check(filepath)
                if not allowed:
                    return "deny", reason

        # 第二层: 命令级检查(针对 Shell 执行工具)
        if tool_name == "tool_run_command":
            cmd = arguments.get("command", "")
            level, reason = self.command_policy.check(cmd)
            if level == "deny":
                return "deny", reason
            if level == "allow" and self.mode == PermissionMode.AUTO:
                return "allow", reason

        # 第三层: 权限模式检查
        if needs_approval(tool_name, self.mode):
            risk = classify_risk(tool_name)
            return "ask", f"工具 '{tool_name}' 的风险等级为 '{risk}', 当前模式 '{self.mode.value}' 需要审批"

        return "allow", "操作已授权"


# =============================================================
#  演示
# =============================================================

if __name__ == "__main__":
    print("=== 权限管理器演示 ===\n")

    # 创建权限管理器(suggest 模式)
    pm = PermissionManager(mode=PermissionMode.SUGGEST)

    # 测试 1: 读操作在 suggest 模式下自动放行
    decision, reason = pm.check_tool_call(
        "tool_read_file", {"path": "README.md"}
    )
    print(f"[读文件] decision={decision}, reason={reason}")

    # 测试 2: 写操作在 suggest 模式下需要审批
    decision, reason = pm.check_tool_call(
        "tool_write_file", {"path": "test.py", "content": "print('hi')"}
    )
    print(f"[写文件] decision={decision}, reason={reason}")

    # 测试 3: 危险命令被拦截
    decision, reason = pm.check_tool_call(
        "tool_run_command", {"command": "rm -rf /"}
    )
    print(f"[危险命令] decision={decision}, reason={reason}")

    # 测试 4: 白名单命令
    decision, reason = pm.check_tool_call(
        "tool_run_command", {"command": "git status"}
    )
    print(f"[安全命令] decision={decision}, reason={reason}")

    # 测试 5: 路径拒绝
    decision, reason = pm.check_tool_call(
        "tool_read_file", {"path": os.path.expanduser("~/.ssh/id_rsa")}
    )
    print(f"[敏感路径] decision={decision}, reason={reason}")

    # 测试 6: AUTO 模式下所有操作放行
    print("\n--- AUTO 模式 ---")
    pm_auto = PermissionManager(mode=PermissionMode.AUTO)
    decision, reason = pm_auto.check_tool_call(
        "tool_write_file", {"path": "test.py", "content": "x=1"}
    )
    print(f"[AUTO写文件] decision={decision}, reason={reason}")

    # 但即使 AUTO 模式, 危险命令依然被拦截
    decision, reason = pm_auto.check_tool_call(
        "tool_run_command", {"command": "rm -rf /"}
    )
    print(f"[AUTO危险命令] decision={decision}, reason={reason}")

    # 测试 7: APPROVE 模式下所有操作需审批
    print("\n--- APPROVE 模式 ---")
    pm_approve = PermissionManager(mode=PermissionMode.APPROVE)
    decision, reason = pm_approve.check_tool_call(
        "tool_read_file", {"path": "README.md"}
    )
    print(f"[APPROVE读文件] decision={decision}, reason={reason}")
