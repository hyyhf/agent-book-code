"""
9.2 - 自动化测试与自验证

实现 Build-Verify-Fix 循环:
- TimeBudget: 时间预算与资源约束管理
- VerificationResult: 验证结果的结构化表示
- BVFLoop: Build-Verify-Fix 循环的编排引擎
  1. Build: Agent 执行编码任务
  2. Verify: 自动运行测试/检查
  3. Fix: 将错误反馈给 Agent, 要求修复

运行方式:
    uv run python chapter09/build_verify_fix.py
"""
import os
import subprocess
import time
from enum import Enum
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

client = OpenAI()
MODEL = os.getenv("OPENAI_MODEL_NAME", "deepseek-v4-flash")


# =============================================================
#  9.2.2 时间预算与资源约束
# =============================================================

class TimeBudget:
    """管理 Agent 长时运行过程中的时间预算。

    防止 Agent 在单个任务上消耗过多时间。
    支持设置总预算和单任务预算, 提供剩余时间查询和超时检测。
    """

    def __init__(
        self,
        total_seconds: float = 600,
        per_task_seconds: float = 120,
    ):
        self.total_seconds = total_seconds
        self.per_task_seconds = per_task_seconds
        self._start_time = time.time()
        self._task_start: float | None = None
        self._api_calls = 0
        self._max_api_calls = 100  # 单次运行最大 API 调用数

    def start_task(self):
        """标记一个新任务开始。"""
        self._task_start = time.time()

    @property
    def elapsed(self) -> float:
        """总已用时间(秒)。"""
        return time.time() - self._start_time

    @property
    def task_elapsed(self) -> float:
        """当前任务已用时间(秒)。"""
        if self._task_start is None:
            return 0.0
        return time.time() - self._task_start

    @property
    def remaining(self) -> float:
        """总剩余时间(秒)。"""
        return max(0, self.total_seconds - self.elapsed)

    @property
    def task_remaining(self) -> float:
        """当前任务剩余时间(秒)。"""
        return max(0, self.per_task_seconds - self.task_elapsed)

    def record_api_call(self):
        """记录一次 API 调用。"""
        self._api_calls += 1

    def is_expired(self) -> bool:
        """总预算是否已耗尽。"""
        return self.elapsed >= self.total_seconds

    def is_task_expired(self) -> bool:
        """当前任务预算是否已耗尽。"""
        return self.task_elapsed >= self.per_task_seconds

    def is_api_exhausted(self) -> bool:
        """API 调用次数是否已达上限。"""
        return self._api_calls >= self._max_api_calls

    def should_warn(self) -> str | None:
        """检查是否需要发出预警, 返回预警消息或 None。"""
        if self.is_expired():
            return "Total time budget exhausted. Wrap up immediately."
        if self.is_task_expired():
            return (
                f"Task time budget exhausted ({self.per_task_seconds}s). "
                f"Mark task as failed and move to next."
            )
        if self.is_api_exhausted():
            return f"API call limit reached ({self._max_api_calls}). Stop execution."
        # 80% 预警线
        if self.remaining < self.total_seconds * 0.2:
            return f"Only {self.remaining:.0f}s remaining in total budget."
        if self._task_start and self.task_remaining < self.per_task_seconds * 0.2:
            return f"Only {self.task_remaining:.0f}s remaining for current task."
        return None

    def summary(self) -> str:
        return (
            f"Time: {self.elapsed:.0f}s/{self.total_seconds:.0f}s | "
            f"Task: {self.task_elapsed:.0f}s/{self.per_task_seconds:.0f}s | "
            f"API calls: {self._api_calls}/{self._max_api_calls}"
        )


# =============================================================
#  9.2.1 / 9.2.3 验证与 Build-Verify-Fix 循环
# =============================================================

class VerifyStatus(Enum):
    PASS = "pass"
    FAIL = "fail"
    ERROR = "error"    # 验证过程本身出错
    SKIP = "skip"      # 没有可运行的验证


class VerificationResult:
    """验证步骤的结构化结果。"""

    def __init__(
        self,
        status: VerifyStatus,
        output: str = "",
        errors: list[str] | None = None,
    ):
        self.status = status
        self.output = output
        self.errors = errors or []

    def __repr__(self):
        return f"VerificationResult({self.status.value}, errors={len(self.errors)})"


def run_verification(command: str, cwd: str = ".") -> VerificationResult:
    """执行一条验证命令(通常是测试或 lint), 返回结构化结果。

    这是 BVF 循环中 Verify 步骤的核心。
    将 shell 命令的执行结果解析为成功/失败, 并提取错误信息。
    """
    try:
        result = subprocess.run(
            command,
            shell=True,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=60,
        )
        output = result.stdout + result.stderr
        # 截断过长输出
        if len(output) > 5000:
            output = output[:2500] + "\n...(truncated)...\n" + output[-2500:]

        if result.returncode == 0:
            return VerificationResult(
                status=VerifyStatus.PASS,
                output=output,
            )

        # 提取错误行
        error_lines = []
        for line in output.split("\n"):
            lower = line.lower()
            if any(kw in lower for kw in ["error", "fail", "assert", "exception"]):
                error_lines.append(line.strip())

        return VerificationResult(
            status=VerifyStatus.FAIL,
            output=output,
            errors=error_lines[:10],  # 最多保留 10 条
        )

    except subprocess.TimeoutExpired:
        return VerificationResult(
            status=VerifyStatus.ERROR,
            output="Verification timed out (60s)",
            errors=["timeout"],
        )
    except Exception as e:
        return VerificationResult(
            status=VerifyStatus.ERROR,
            output=str(e),
            errors=[str(e)],
        )


class BVFLoop:
    """Build-Verify-Fix 循环编排器。

    核心理念: "验证比实现更重要"。
    Agent 完成一步编码(Build)后, 自动运行验证(Verify),
    若失败则将错误信息反馈给 Agent 进行修复(Fix)。
    循环直到验证通过或达到最大修复次数。
    """

    def __init__(
        self,
        verify_command: str = "",
        max_fix_attempts: int = 3,
        time_budget: TimeBudget | None = None,
    ):
        self.verify_command = verify_command
        self.max_fix_attempts = max_fix_attempts
        self.time_budget = time_budget or TimeBudget()
        self._history: list[dict] = []

    def build_prompt(self, task_description: str, fix_context: str = "") -> str:
        """构造 Agent 的编码指令。

        如果有 fix_context, 说明是修复轮次, 附带之前的错误信息。
        """
        if fix_context:
            return (
                f"The previous implementation failed verification.\n\n"
                f"Errors:\n{fix_context}\n\n"
                f"Fix the issues. Original task:\n{task_description}"
            )
        return task_description

    def verify(self, cwd: str = ".") -> VerificationResult:
        """执行验证步骤。"""
        if not self.verify_command:
            return VerificationResult(status=VerifyStatus.SKIP, output="No verify command")
        return run_verification(self.verify_command, cwd=cwd)

    def format_errors_for_fix(self, result: VerificationResult) -> str:
        """将验证失败的信息格式化为 Agent 可理解的修复指令。"""
        parts = [f"Verification FAILED (command: {self.verify_command})"]
        if result.errors:
            parts.append("Key errors:")
            for e in result.errors:
                parts.append(f"  - {e}")
        if result.output:
            # 只保留输出的最后部分(通常包含错误摘要)
            tail = result.output[-1500:]
            parts.append(f"\nOutput tail:\n{tail}")
        return "\n".join(parts)

    def run_cycle(
        self,
        task_description: str,
        build_fn,
        cwd: str = ".",
    ) -> dict:
        """执行一轮完整的 BVF 循环。

        Args:
            task_description: 任务描述
            build_fn: 构建函数, 接受 (prompt: str) -> str, 返回 Agent 的执行结果
            cwd: 验证命令的工作目录

        Returns:
            {"success": bool, "attempts": int, "history": list}
        """
        self.time_budget.start_task()

        for attempt in range(1, self.max_fix_attempts + 1):
            # 检查时间预算
            warning = self.time_budget.should_warn()
            if warning and (self.time_budget.is_expired() or self.time_budget.is_task_expired()):
                self._history.append({
                    "attempt": attempt,
                    "phase": "budget",
                    "result": warning,
                })
                return {
                    "success": False,
                    "attempts": attempt,
                    "reason": warning,
                    "history": self._history,
                }

            # Build: 让 Agent 执行编码
            fix_context = ""
            if attempt > 1 and self._history:
                last = self._history[-1]
                if last.get("phase") == "verify" and last.get("errors"):
                    fix_context = last["errors"]

            prompt = self.build_prompt(task_description, fix_context)
            build_result = build_fn(prompt)
            self.time_budget.record_api_call()

            self._history.append({
                "attempt": attempt,
                "phase": "build",
                "result": build_result[:500],
            })

            # Verify: 运行验证
            vr = self.verify(cwd=cwd)
            self._history.append({
                "attempt": attempt,
                "phase": "verify",
                "status": vr.status.value,
                "errors": self.format_errors_for_fix(vr) if vr.status == VerifyStatus.FAIL else "",
            })

            if vr.status in (VerifyStatus.PASS, VerifyStatus.SKIP):
                return {
                    "success": True,
                    "attempts": attempt,
                    "history": self._history,
                }

            if vr.status == VerifyStatus.ERROR:
                return {
                    "success": False,
                    "attempts": attempt,
                    "reason": f"Verification error: {vr.output}",
                    "history": self._history,
                }

            # Fix: 进入下一轮循环, fix_context 会在下一轮的 build_prompt 中生成

        return {
            "success": False,
            "attempts": self.max_fix_attempts,
            "reason": f"Max fix attempts ({self.max_fix_attempts}) reached",
            "history": self._history,
        }


# =============================================================
#  演示
# =============================================================

if __name__ == "__main__":
    print("=== Build-Verify-Fix 循环演示 ===\n")

    # 1. 时间预算
    print("--- 时间预算 ---")
    budget = TimeBudget(total_seconds=300, per_task_seconds=60)
    budget.start_task()
    print(f"  {budget.summary()}")
    budget.record_api_call()
    budget.record_api_call()
    print(f"  {budget.summary()}")
    print(f"  Warning: {budget.should_warn()}")

    # 2. 单独运行验证
    print("\n--- 验证命令 ---")
    # 用一个必定成功的命令测试
    vr = run_verification("echo all tests passed", cwd=".")
    print(f"  Status: {vr.status.value}")
    print(f"  Output: {vr.output.strip()}")

    # 用一个必定失败的命令测试
    vr_fail = run_verification("echo 'Error: assertion failed' && exit 1", cwd=".")
    print(f"  Fail status: {vr_fail.status.value}")
    print(f"  Errors: {vr_fail.errors}")

    # 3. 完整 BVF 循环(使用模拟的 build 函数)
    print("\n--- BVF 循环(模拟) ---")
    call_count = [0]

    def mock_build(prompt: str) -> str:
        call_count[0] += 1
        if call_count[0] <= 2:
            return f"[Attempt {call_count[0]}] Wrote code with intentional bug"
        return f"[Attempt {call_count[0]}] Fixed the bug, code is correct now"

    bvf = BVFLoop(
        verify_command="echo success",  # 总是通过
        max_fix_attempts=3,
        time_budget=TimeBudget(total_seconds=60, per_task_seconds=30),
    )
    result = bvf.run_cycle(
        task_description="Implement a calculator add function",
        build_fn=mock_build,
    )
    print(f"  Success: {result['success']}")
    print(f"  Attempts: {result['attempts']}")
    for entry in result["history"]:
        phase = entry.get("phase", "?")
        if phase == "build":
            print(f"    [{phase}] {entry['result'][:60]}")
        elif phase == "verify":
            print(f"    [{phase}] {entry['status']}")

    # 4. BVF 循环(使用真实模型)
    print("\n--- BVF 循环(使用模型) ---")

    def model_build(prompt: str) -> str:
        """用模型执行一次编码任务(仅生成代码, 不执行)。"""
        response = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": "You are a Python programmer. Write code only, no explanation."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.2,
            max_tokens=500,
        )
        return response.choices[0].message.content or ""

    bvf2 = BVFLoop(
        verify_command="echo ok",
        max_fix_attempts=2,
        time_budget=TimeBudget(total_seconds=30, per_task_seconds=15),
    )
    result2 = bvf2.run_cycle(
        task_description="Write a Python function 'add(a, b)' that returns a + b",
        build_fn=model_build,
    )
    print(f"  Success: {result2['success']}, Attempts: {result2['attempts']}")
    for entry in result2["history"]:
        if entry.get("phase") == "build":
            preview = entry["result"][:80].replace("\n", " ")
            print(f"    [build] {preview}...")
