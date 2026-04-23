"""
8.1 - Hooks 机制

在 Agent 执行的关键节点植入逻辑:
- HookRegistry: 注册和管理钩子的中心注册表
- PreToolUse:  工具执行前触发, 可拦截/修改/放行
- PostToolUse: 工具执行后触发, 可对结果做后处理
- PreCompletion: 模型生成最终回复前触发, 退出前检查清单

运行方式:
    uv run python chapter08/hooks.py
"""
from enum import Enum
from typing import Callable, Any


# =============================================================
#  8.1.1 Hook 事件类型与决策
# =============================================================

class HookEvent(Enum):
    """Agent 生命周期中可被钩子介入的关键事件。"""
    PRE_TOOL_USE = "pre_tool_use"
    POST_TOOL_USE = "post_tool_use"
    PRE_COMPLETION = "pre_completion"


class HookAction(Enum):
    """PreToolUse 钩子的返回决策。"""
    ALLOW = "allow"       # 放行, 继续执行
    DENY = "deny"         # 拦截, 拒绝执行
    MODIFY = "modify"     # 修改参数后继续


class HookResult:
    """钩子执行的返回结果。

    对于 PreToolUse: action 决定工具是否执行, modified_args 是修改后的参数。
    对于 PostToolUse: feedback 是注入给模型的额外反馈。
    对于 PreCompletion: action=DENY 表示还有未完成的检查项, feedback 是原因。
    """

    def __init__(
        self,
        action: HookAction = HookAction.ALLOW,
        feedback: str = "",
        modified_args: dict | None = None,
    ):
        self.action = action
        self.feedback = feedback
        self.modified_args = modified_args


# 钩子函数签名:
#   PreToolUse:    (tool_name: str, arguments: dict) -> HookResult
#   PostToolUse:   (tool_name: str, arguments: dict, result: str) -> HookResult
#   PreCompletion: (assistant_message: str, messages: list) -> HookResult


# =============================================================
#  8.1.2 HookRegistry: 钩子注册与调度
# =============================================================

class HookRegistry:
    """钩子注册表: 管理 Agent 生命周期中的所有钩子。

    支持按事件类型注册多个钩子, 按注册顺序依次执行。
    钩子可以附带名称和优先级(数字越小优先级越高)。
    """

    def __init__(self):
        self._hooks: dict[HookEvent, list[dict]] = {
            event: [] for event in HookEvent
        }

    def register(
        self,
        event: HookEvent,
        handler: Callable,
        name: str = "",
        priority: int = 100,
        tool_matcher: str | None = None,
    ):
        """注册一个钩子。

        Args:
            event: 触发事件类型
            handler: 钩子处理函数
            name: 钩子名称(用于调试和日志)
            priority: 优先级, 数字越小越先执行
            tool_matcher: 工具名匹配模式, 仅对 PreToolUse/PostToolUse 生效。
                          支持 '|' 分隔多个工具名, 如 'tool_write_file|tool_replace_in_file'。
                          为 None 时匹配所有工具。
        """
        entry = {
            "handler": handler,
            "name": name or handler.__name__,
            "priority": priority,
            "tool_matcher": tool_matcher,
        }
        self._hooks[event].append(entry)
        # 按优先级排序
        self._hooks[event].sort(key=lambda h: h["priority"])

    def _matches_tool(self, matcher: str | None, tool_name: str) -> bool:
        """检查工具名是否匹配 matcher 模式。"""
        if matcher is None:
            return True
        patterns = [p.strip() for p in matcher.split("|")]
        return tool_name in patterns

    def dispatch_pre_tool(
        self, tool_name: str, arguments: dict
    ) -> HookResult:
        """调度所有 PreToolUse 钩子, 返回最终决策。

        执行逻辑:
        - 按优先级依次执行每个钩子
        - 任何一个钩子返回 DENY, 立即终止并拒绝
        - 钩子返回 MODIFY 时, 将修改后的参数传递给后续钩子
        - 所有钩子都返回 ALLOW 时, 最终放行
        """
        current_args = arguments.copy()
        feedbacks = []

        for entry in self._hooks[HookEvent.PRE_TOOL_USE]:
            if not self._matches_tool(entry["tool_matcher"], tool_name):
                continue

            result = entry["handler"](tool_name, current_args)

            if result.action == HookAction.DENY:
                return result

            if result.action == HookAction.MODIFY and result.modified_args:
                current_args = result.modified_args

            if result.feedback:
                feedbacks.append(result.feedback)

        return HookResult(
            action=HookAction.ALLOW,
            feedback="\n".join(feedbacks),
            modified_args=current_args if current_args != arguments else None,
        )

    def dispatch_post_tool(
        self, tool_name: str, arguments: dict, result: str
    ) -> HookResult:
        """调度所有 PostToolUse 钩子, 收集反馈。

        PostToolUse 钩子不能阻止执行(工具已经执行完了),
        但可以收集反馈信息注入到模型的上下文中。
        """
        feedbacks = []

        for entry in self._hooks[HookEvent.POST_TOOL_USE]:
            if not self._matches_tool(entry["tool_matcher"], tool_name):
                continue

            hook_result = entry["handler"](tool_name, arguments, result)
            if hook_result.feedback:
                feedbacks.append(hook_result.feedback)

        return HookResult(
            action=HookAction.ALLOW,
            feedback="\n".join(feedbacks),
        )

    def dispatch_pre_completion(
        self, assistant_message: str, messages: list
    ) -> HookResult:
        """调度所有 PreCompletion 钩子, 执行退出前检查。

        如果任何一个检查项不通过, 返回 DENY 并附带原因,
        Agent Loop 应将原因反馈给模型, 让模型继续工作。
        """
        feedbacks = []

        for entry in self._hooks[HookEvent.PRE_COMPLETION]:
            result = entry["handler"](assistant_message, messages)

            if result.action == HookAction.DENY:
                return result

            if result.feedback:
                feedbacks.append(result.feedback)

        return HookResult(
            action=HookAction.ALLOW,
            feedback="\n".join(feedbacks),
        )

    def list_hooks(self) -> str:
        """列出所有已注册的钩子, 用于调试。"""
        lines = []
        for event in HookEvent:
            hooks = self._hooks[event]
            if hooks:
                lines.append(f"\n{event.value}:")
                for h in hooks:
                    matcher_info = f" [matcher: {h['tool_matcher']}]" if h["tool_matcher"] else ""
                    lines.append(
                        f"  - {h['name']} (priority={h['priority']}){matcher_info}"
                    )
        return "\n".join(lines) if lines else "(no hooks registered)"


# =============================================================
#  8.1.3 内置钩子示例
# =============================================================

def path_normalization_hook(tool_name: str, arguments: dict) -> HookResult:
    """PreToolUse 钩子: 将文件路径中的 '~' 展开为绝对路径。

    防止 Agent 使用 '~' 开头的路径绕过路径检查。
    """
    import os

    path_params = ["path"]
    modified = arguments.copy()
    changed = False

    for param in path_params:
        if param in modified and "~" in str(modified[param]):
            original = modified[param]
            modified[param] = os.path.expanduser(original)
            changed = True

    if changed:
        return HookResult(
            action=HookAction.MODIFY,
            feedback="Path normalized (expanded ~)",
            modified_args=modified,
        )
    return HookResult(action=HookAction.ALLOW)


def large_file_guard_hook(tool_name: str, arguments: dict) -> HookResult:
    """PreToolUse 钩子: 写入过大内容时发出警告。

    如果写入内容超过 50000 字符, 拦截并要求分批写入。
    """
    if tool_name != "tool_write_file":
        return HookResult(action=HookAction.ALLOW)

    content = arguments.get("content", "")
    if len(content) > 50000:
        return HookResult(
            action=HookAction.DENY,
            feedback=(
                f"Write blocked: content is {len(content)} chars "
                f"(limit 50000). Split into smaller writes."
            ),
        )
    return HookResult(action=HookAction.ALLOW)


def lint_after_write_hook(
    tool_name: str, arguments: dict, result: str
) -> HookResult:
    """PostToolUse 钩子: 文件写入后检查是否为 Python 文件, 提醒运行测试。

    实际生产环境中, 这里可以调用 linter 或 formatter。
    """
    filepath = arguments.get("path", "")
    if filepath.endswith(".py") and "Error" not in result:
        return HookResult(
            action=HookAction.ALLOW,
            feedback=f"[hook] Python file written: '{filepath}'. Consider running tests.",
        )
    return HookResult(action=HookAction.ALLOW)


def completion_checklist_hook(
    assistant_message: str, messages: list
) -> HookResult:
    """PreCompletion 钩子: 检查 Agent 是否在回复中做了基本总结。

    如果 Agent 的最终回复过短(不足 20 字符), 要求它补充。
    """
    if assistant_message and len(assistant_message.strip()) < 20:
        return HookResult(
            action=HookAction.DENY,
            feedback="Your reply is too brief. Provide a summary of what was done.",
        )
    return HookResult(action=HookAction.ALLOW)


# =============================================================
#  演示
# =============================================================

if __name__ == "__main__":
    print("=== Hooks 机制演示 ===\n")

    # 创建钩子注册表
    hook_registry = HookRegistry()

    # 注册内置钩子
    hook_registry.register(
        HookEvent.PRE_TOOL_USE,
        path_normalization_hook,
        name="path_normalizer",
        priority=10,
    )
    hook_registry.register(
        HookEvent.PRE_TOOL_USE,
        large_file_guard_hook,
        name="large_file_guard",
        priority=20,
        tool_matcher="tool_write_file",
    )
    hook_registry.register(
        HookEvent.POST_TOOL_USE,
        lint_after_write_hook,
        name="lint_reminder",
        tool_matcher="tool_write_file|tool_replace_in_file",
    )
    hook_registry.register(
        HookEvent.PRE_COMPLETION,
        completion_checklist_hook,
        name="completion_checklist",
    )

    # 打印已注册的钩子
    print("Registered hooks:")
    print(hook_registry.list_hooks())

    # 测试 1: 路径规范化
    print("\n--- Test 1: Path normalization ---")
    result = hook_registry.dispatch_pre_tool(
        "tool_read_file", {"path": "~/Documents/test.txt"}
    )
    print(f"  Action: {result.action.value}")
    print(f"  Modified args: {result.modified_args}")
    print(f"  Feedback: {result.feedback}")

    # 测试 2: 大文件拦截
    print("\n--- Test 2: Large file guard ---")
    result = hook_registry.dispatch_pre_tool(
        "tool_write_file", {"path": "big.py", "content": "x" * 60000}
    )
    print(f"  Action: {result.action.value}")
    print(f"  Feedback: {result.feedback}")

    # 测试 3: 正常写入
    print("\n--- Test 3: Normal write ---")
    result = hook_registry.dispatch_pre_tool(
        "tool_write_file", {"path": "hello.py", "content": "print('hi')"}
    )
    print(f"  Action: {result.action.value}")

    # 测试 4: PostToolUse 反馈
    print("\n--- Test 4: Post-tool lint reminder ---")
    result = hook_registry.dispatch_post_tool(
        "tool_write_file",
        {"path": "main.py", "content": "x=1"},
        "File written successfully",
    )
    print(f"  Feedback: {result.feedback}")

    # 测试 5: PreCompletion 检查
    print("\n--- Test 5: Completion checklist ---")
    result = hook_registry.dispatch_pre_completion("OK", [])
    print(f"  Action: {result.action.value}")
    print(f"  Feedback: {result.feedback}")

    result = hook_registry.dispatch_pre_completion(
        "I have completed all requested changes and verified the results.", []
    )
    print(f"  Action: {result.action.value}")
