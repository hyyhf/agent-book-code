"""
8.2 - 中间件模式

可插拔行为增强的设计:
- Middleware 基类与链式调用
- LoopDetectionMiddleware: 循环检测中间件
- SelfVerificationMiddleware: 自验证中间件
- EnvironmentAwareMiddleware: 环境感知中间件

运行方式:
    uv run python chapter08/middleware.py
"""
from abc import ABC, abstractmethod
from collections import Counter


# =============================================================
#  8.2.1 中间件基类
# =============================================================

class Middleware(ABC):
    """Agent 中间件基类。

    中间件在 Agent Loop 的每次迭代中执行, 可以:
    - 观察当前状态(消息历史、工具调用记录等)
    - 注入额外的上下文或指令
    - 修改即将发送给模型的消息列表
    - 在检测到异常模式时强制终止循环

    所有中间件通过 MiddlewareChain 串联, 按注册顺序依次执行。
    """

    @abstractmethod
    def process(self, context: dict) -> dict:
        """处理一次迭代的上下文。

        Args:
            context: 包含当前 Agent 状态的字典:
                - messages: list, 当前消息列表
                - iteration: int, 当前迭代轮次
                - tool_calls_history: list[dict], 历史工具调用记录
                  每条记录: {"tool": str, "args": dict, "result": str}
                - should_stop: bool, 是否应该终止循环
                - injections: list[str], 要注入给模型的额外指令

        Returns:
            处理后的 context (可修改)
        """
        ...


class MiddlewareChain:
    """中间件链: 按顺序执行所有注册的中间件。"""

    def __init__(self):
        self._middlewares: list[Middleware] = []

    def add(self, middleware: Middleware):
        """添加一个中间件到链的末尾。"""
        self._middlewares.append(middleware)

    def run(self, context: dict) -> dict:
        """按顺序执行所有中间件。"""
        for mw in self._middlewares:
            context = mw.process(context)
            if context.get("should_stop"):
                break
        return context

    def list_middlewares(self) -> str:
        """列出所有中间件。"""
        if not self._middlewares:
            return "(no middlewares)"
        return "\n".join(
            f"  {i+1}. {type(mw).__name__}" for i, mw in enumerate(self._middlewares)
        )


# =============================================================
#  8.2.2 循环检测中间件
# =============================================================

class LoopDetectionMiddleware(Middleware):
    """检测 Agent 是否陷入工具调用的死循环。

    监控模式:
    1. 相同工具+参数的重复调用(完全相同的调用出现 N 次)
    2. 工具调用模式的周期性重复(A->B->C->A->B->C)
    3. 连续失败的工具调用

    检测到循环后, 注入提示让模型意识到问题并改变策略。
    达到阈值后强制终止循环。
    """

    def __init__(
        self,
        repeat_threshold: int = 3,
        cycle_length: int = 3,
        max_consecutive_errors: int = 5,
    ):
        self.repeat_threshold = repeat_threshold
        self.cycle_length = cycle_length
        self.max_consecutive_errors = max_consecutive_errors

    def _detect_repeated_calls(self, history: list[dict]) -> str | None:
        """检测完全相同的工具调用是否超过阈值。"""
        if len(history) < self.repeat_threshold:
            return None

        # 将最近的调用序列化为字符串用于比较
        recent = history[-self.repeat_threshold:]
        signatures = [f"{h['tool']}:{sorted(h['args'].items())}" for h in recent]

        if len(set(signatures)) == 1:
            tool_name = recent[0]["tool"]
            return (
                f"Detected {self.repeat_threshold} identical calls to '{tool_name}'. "
                f"You are repeating the same action. Try a different approach."
            )
        return None

    def _detect_cyclic_pattern(self, history: list[dict]) -> str | None:
        """检测工具调用是否形成周期性循环 (A->B->C->A->B->C)。"""
        if len(history) < self.cycle_length * 2:
            return None

        recent_tools = [h["tool"] for h in history[-(self.cycle_length * 2):]]

        # 检查后半段是否和前半段完全相同
        first_half = recent_tools[:self.cycle_length]
        second_half = recent_tools[self.cycle_length:]

        if first_half == second_half:
            pattern = " -> ".join(first_half)
            return (
                f"Detected cyclic pattern: {pattern}. "
                f"Break the cycle by trying a fundamentally different approach."
            )
        return None

    def _detect_consecutive_errors(self, history: list[dict]) -> str | None:
        """检测是否连续产生错误结果。"""
        if len(history) < self.max_consecutive_errors:
            return None

        recent = history[-self.max_consecutive_errors:]
        error_keywords = ["Error", "Failed", "DENIED", "failed"]

        all_errors = all(
            any(kw in h.get("result", "") for kw in error_keywords)
            for h in recent
        )

        if all_errors:
            return (
                f"Last {self.max_consecutive_errors} tool calls all produced errors. "
                f"Stop and reassess your approach. Explain the failures to the user."
            )
        return None

    def process(self, context: dict) -> dict:
        history = context.get("tool_calls_history", [])
        if not history:
            return context

        # 依次检测三种循环模式
        checks = [
            self._detect_repeated_calls,
            self._detect_cyclic_pattern,
            self._detect_consecutive_errors,
        ]

        for check in checks:
            warning = check(history)
            if warning:
                context.setdefault("injections", []).append(
                    f"[LOOP DETECTION WARNING] {warning}"
                )
                # 连续错误时强制停止
                if "all produced errors" in (warning or ""):
                    context["should_stop"] = True
                break

        return context


# =============================================================
#  8.2.3 自验证中间件
# =============================================================

class SelfVerificationMiddleware(Middleware):
    """在关键操作后提醒 Agent 验证结果。

    当 Agent 执行了写文件或运行命令后, 注入验证提示,
    鼓励模型主动检查操作是否成功。
    """

    VERIFY_TOOLS = {"tool_write_file", "tool_replace_in_file", "tool_run_command"}

    def __init__(self, verify_interval: int = 3):
        """
        Args:
            verify_interval: 每隔多少次写操作提醒一次验证
        """
        self.verify_interval = verify_interval
        self._write_count = 0

    def process(self, context: dict) -> dict:
        history = context.get("tool_calls_history", [])
        if not history:
            return context

        last_call = history[-1]
        tool = last_call["tool"]

        if tool in self.VERIFY_TOOLS:
            self._write_count += 1

            if self._write_count % self.verify_interval == 0:
                context.setdefault("injections", []).append(
                    "[SELF-VERIFY] You have made several modifications. "
                    "Pause and verify: read back the changed files or "
                    "run tests to confirm everything works correctly."
                )

        return context


# =============================================================
#  8.2.4 环境感知中间件
# =============================================================

class EnvironmentAwareMiddleware(Middleware):
    """根据运行环境注入上下文信息。

    在 Agent 执行过程中, 动态检测环境变化并注入提示:
    - 工作目录变化检测
    - 迭代次数预警
    - 工具调用频率统计
    """

    def __init__(self, max_iterations_warning: int = 15):
        self.max_iterations_warning = max_iterations_warning
        self._last_cwd = None

    def process(self, context: dict) -> dict:
        import os

        injections = context.setdefault("injections", [])
        iteration = context.get("iteration", 0)

        # 迭代次数预警
        if iteration == self.max_iterations_warning:
            injections.append(
                f"[ENV] You have used {iteration} iterations. "
                f"Consider wrapping up soon or ask the user if you should continue."
            )

        # 工作目录变化检测
        current_cwd = os.getcwd()
        if self._last_cwd and current_cwd != self._last_cwd:
            injections.append(
                f"[ENV] Working directory changed: {self._last_cwd} -> {current_cwd}"
            )
        self._last_cwd = current_cwd

        # 工具使用频率统计(每 10 次迭代报告一次)
        if iteration > 0 and iteration % 10 == 0:
            history = context.get("tool_calls_history", [])
            if history:
                counter = Counter(h["tool"] for h in history)
                top3 = counter.most_common(3)
                stats = ", ".join(f"{name}={count}" for name, count in top3)
                injections.append(f"[ENV] Tool usage (top 3): {stats}")

        return context


# =============================================================
#  演示
# =============================================================

if __name__ == "__main__":
    print("=== 中间件模式演示 ===\n")

    # 构建中间件链
    chain = MiddlewareChain()
    chain.add(LoopDetectionMiddleware(repeat_threshold=3))
    chain.add(SelfVerificationMiddleware(verify_interval=2))
    chain.add(EnvironmentAwareMiddleware(max_iterations_warning=5))

    print("Middleware chain:")
    print(chain.list_middlewares())

    # 模拟正常的工具调用序列
    print("\n--- Test 1: Normal execution ---")
    context = {
        "messages": [],
        "iteration": 1,
        "tool_calls_history": [
            {"tool": "tool_read_file", "args": {"path": "a.py"}, "result": "ok"},
            {"tool": "tool_write_file", "args": {"path": "b.py"}, "result": "ok"},
        ],
        "should_stop": False,
        "injections": [],
    }
    result = chain.run(context)
    print(f"  should_stop: {result['should_stop']}")
    print(f"  injections: {result['injections']}")

    # 模拟重复调用(循环检测)
    print("\n--- Test 2: Repeated calls detected ---")
    context = {
        "messages": [],
        "iteration": 5,
        "tool_calls_history": [
            {"tool": "tool_read_file", "args": {"path": "x.py"}, "result": "ok"},
            {"tool": "tool_read_file", "args": {"path": "x.py"}, "result": "ok"},
            {"tool": "tool_read_file", "args": {"path": "x.py"}, "result": "ok"},
        ],
        "should_stop": False,
        "injections": [],
    }
    result = chain.run(context)
    print(f"  should_stop: {result['should_stop']}")
    for inj in result["injections"]:
        print(f"  injection: {inj}")

    # 模拟自验证触发
    print("\n--- Test 3: Self-verification trigger ---")
    sv_chain = MiddlewareChain()
    sv_mw = SelfVerificationMiddleware(verify_interval=2)
    sv_chain.add(sv_mw)

    for i in range(1, 4):
        ctx = {
            "messages": [],
            "iteration": i,
            "tool_calls_history": [
                {"tool": "tool_write_file", "args": {"path": f"f{i}.py"}, "result": "ok"},
            ],
            "should_stop": False,
            "injections": [],
        }
        res = sv_chain.run(ctx)
        injections = res["injections"]
        print(f"  Iteration {i}: {injections if injections else '(no injection)'}")

    # 模拟连续错误
    print("\n--- Test 4: Consecutive errors -> force stop ---")
    context = {
        "messages": [],
        "iteration": 8,
        "tool_calls_history": [
            {"tool": "tool_run_command", "args": {"command": "make"}, "result": "Error: build failed"},
            {"tool": "tool_run_command", "args": {"command": "make clean"}, "result": "Error: no makefile"},
            {"tool": "tool_run_command", "args": {"command": "cmake ."}, "result": "Error: cmake failed"},
            {"tool": "tool_write_file", "args": {"path": "fix.py"}, "result": "Failed to write"},
            {"tool": "tool_run_command", "args": {"command": "make"}, "result": "Error: still broken"},
        ],
        "should_stop": False,
        "injections": [],
    }
    result = chain.run(context)
    print(f"  should_stop: {result['should_stop']}")
    for inj in result["injections"]:
        print(f"  injection: {inj}")

    # 模拟迭代次数预警
    print("\n--- Test 5: Iteration warning ---")
    env_chain = MiddlewareChain()
    env_chain.add(EnvironmentAwareMiddleware(max_iterations_warning=5))
    ctx = {
        "messages": [],
        "iteration": 5,
        "tool_calls_history": [],
        "should_stop": False,
        "injections": [],
    }
    res = env_chain.run(ctx)
    for inj in res["injections"]:
        print(f"  injection: {inj}")
