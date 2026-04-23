"""
10.2 - 评估与调优

实现 Agent 的评估与调优工具:
- FailurePattern: 常见的 Agent 失败模式识别
- BenchmarkRunner: 场景化评估, 批量运行测试用例
- HarnessConfig / TuningExperiment: Harness 参数的迭代调优

运行方式:
    uv run python chapter10/evaluation.py
"""
import json
import os
import time
from datetime import datetime
from enum import Enum
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

client = OpenAI()
MODEL = os.getenv("OPENAI_MODEL_NAME", "deepseek-chat")


# =============================================================
#  10.2.1 常见的 Agent 失败模式与对策
# =============================================================

class FailureType(Enum):
    """Agent 常见的失败模式分类。"""
    LOOP = "loop"                    # 重复执行相同操作
    HALLUCINATION = "hallucination"  # 编造不存在的文件/函数
    OVER_ENGINEERING = "over_eng"    # 过度工程化, 偏离需求
    WRONG_TOOL = "wrong_tool"       # 选择错误的工具
    INCOMPLETE = "incomplete"        # 任务未完成就结束
    CONTEXT_LOST = "context_lost"    # 上下文丢失关键信息
    PERMISSION_FAIL = "perm_fail"   # 权限不足导致失败
    TIMEOUT = "timeout"             # 超时


class FailurePattern:
    """Agent 失败模式的检测器。

    分析 Agent 的执行轨迹(Trace), 识别常见的失败模式。
    每种失败模式对应一个检测函数和一组推荐的对策。
    """

    # 每种失败模式的对策建议
    COUNTERMEASURES = {
        FailureType.LOOP: [
            "增加 LoopDetectionMiddleware 的灵敏度(降低 repeat_threshold)",
            "在 System Prompt 中明确禁止连续重试相同操作",
            "增加 PostToolUse 钩子, 检测连续相同工具调用",
        ],
        FailureType.HALLUCINATION: [
            "在工具执行前增加文件存在性校验",
            "减小 temperature 参数(建议 0.1-0.3)",
            "添加 PreToolUse 钩子, 强制校验路径参数",
        ],
        FailureType.OVER_ENGINEERING: [
            "在 System Prompt 中强调 KISS 原则和最小实现",
            "添加 SelfVerificationMiddleware, 定期要求 Agent 确认方向",
            "减少 MAX_ITERATIONS, 迫使 Agent 聚焦",
        ],
        FailureType.WRONG_TOOL: [
            "改进工具描述, 明确使用场景和限制",
            "减少可用工具数量, 只注册任务相关工具",
            "在 System Prompt 中添加工具选择指南",
        ],
        FailureType.INCOMPLETE: [
            "增加 PreCompletion 钩子的检查项",
            "添加 completeness checklist 到 System Prompt",
            "使用 BVF 循环确保每步验证通过",
        ],
        FailureType.CONTEXT_LOST: [
            "降低上下文压缩的阈值, 减少压缩频率",
            "使用 PROGRESS.md 做外部记忆锚点",
            "压缩后自动注入 tool_read_progress 提示",
        ],
        FailureType.PERMISSION_FAIL: [
            "切换到更宽松的权限模式(suggest -> auto)",
            "预配置任务所需的路径白名单",
            "添加 auto-approve 规则处理低风险操作",
        ],
        FailureType.TIMEOUT: [
            "增加时间预算(total_seconds, per_task_seconds)",
            "将任务拆分为更小的子任务",
            "优化工具实现, 减少单次调用耗时",
        ],
    }

    @staticmethod
    def detect_loop(tool_calls: list[dict], threshold: int = 3) -> dict | None:
        """检测工具调用循环。

        如果连续 threshold 次调用同一工具且参数相似, 判定为循环。
        """
        if len(tool_calls) < threshold:
            return None

        for i in range(len(tool_calls) - threshold + 1):
            window = tool_calls[i:i + threshold]
            names = [tc.get("tool", "") for tc in window]
            if len(set(names)) == 1:
                # 检查参数是否相似
                args_list = [json.dumps(tc.get("args", {}), sort_keys=True) for tc in window]
                if len(set(args_list)) <= 1:
                    return {
                        "type": FailureType.LOOP,
                        "detail": f"Tool '{names[0]}' called {threshold}x with same args",
                        "at_index": i,
                    }
        return None

    @staticmethod
    def detect_hallucination(tool_calls: list[dict]) -> dict | None:
        """检测幻觉(操作不存在的文件)。

        如果工具返回 "not found" 或 "no such file" 类错误,
        且 Agent 没有先列出目录或搜索过文件, 可能是路径幻觉。
        """
        not_found_keywords = ["not found", "no such file", "does not exist", "not exist"]

        for i, tc in enumerate(tool_calls):
            result = tc.get("result", "").lower()
            if any(kw in result for kw in not_found_keywords):
                # 检查之前是否有 list_directory 或 grep_search
                prior_tools = [t.get("tool", "") for t in tool_calls[:i]]
                if "tool_list_directory" not in prior_tools and "tool_grep_search" not in prior_tools:
                    return {
                        "type": FailureType.HALLUCINATION,
                        "detail": f"Operated on non-existent target without prior discovery",
                        "at_index": i,
                        "tool": tc.get("tool", ""),
                    }
        return None

    @staticmethod
    def detect_incomplete(messages: list[dict], tool_calls: list[dict]) -> dict | None:
        """检测任务未完成。

        如果 Agent 的最后一条消息没有明确的完成总结,
        且与用户最初的请求主题不相关, 可能是任务未完成。
        """
        if not messages:
            return None

        # 获取最后一条 assistant 消息
        last_assistant = ""
        for msg in reversed(messages):
            if msg.get("role") == "assistant" and msg.get("content"):
                last_assistant = msg["content"]
                break

        if not last_assistant:
            return {
                "type": FailureType.INCOMPLETE,
                "detail": "No final assistant message found",
            }

        # 简单的完成度检查: 最终回复长度是否足够
        if len(last_assistant) < 30:
            return {
                "type": FailureType.INCOMPLETE,
                "detail": f"Final response too brief ({len(last_assistant)} chars)",
            }

        return None

    @classmethod
    def analyze(
        cls,
        messages: list[dict],
        tool_calls: list[dict],
    ) -> list[dict]:
        """对一次 Agent 运行执行全面的失败模式分析。

        Returns:
            检测到的失败模式列表, 每项包含 type, detail, countermeasures
        """
        findings = []

        # 循环检测
        loop = cls.detect_loop(tool_calls)
        if loop:
            loop["countermeasures"] = cls.COUNTERMEASURES[FailureType.LOOP]
            findings.append(loop)

        # 幻觉检测
        hallucination = cls.detect_hallucination(tool_calls)
        if hallucination:
            hallucination["countermeasures"] = cls.COUNTERMEASURES[FailureType.HALLUCINATION]
            findings.append(hallucination)

        # 完成度检测
        incomplete = cls.detect_incomplete(messages, tool_calls)
        if incomplete:
            incomplete["countermeasures"] = cls.COUNTERMEASURES[FailureType.INCOMPLETE]
            findings.append(incomplete)

        return findings

    @classmethod
    def report(cls, findings: list[dict]) -> str:
        """将分析结果格式化为可读报告。"""
        if not findings:
            return "No failure patterns detected."

        lines = [f"Detected {len(findings)} failure pattern(s):\n"]
        for i, f in enumerate(findings, 1):
            ftype = f.get("type", "unknown")
            if isinstance(ftype, FailureType):
                ftype = ftype.value
            lines.append(f"  {i}. [{ftype}] {f.get('detail', '')}")
            for cm in f.get("countermeasures", []):
                lines.append(f"     -> {cm}")
            lines.append("")

        return "\n".join(lines)


# =============================================================
#  10.2.2 Benchmark 测试与场景化评估
# =============================================================

class BenchmarkCase:
    """一个 Benchmark 测试用例。

    定义了:
    - 输入: 用户请求
    - 预期: 用于判定成功的检查条件
    - 约束: 最大轮次、时间限制等
    """

    def __init__(
        self,
        case_id: str,
        prompt: str,
        expected_files: list[str] | None = None,
        expected_keywords: list[str] | None = None,
        verify_command: str = "",
        max_iterations: int = 10,
        timeout_seconds: float = 120,
        category: str = "general",
    ):
        self.case_id = case_id
        self.prompt = prompt
        self.expected_files = expected_files or []
        self.expected_keywords = expected_keywords or []
        self.verify_command = verify_command
        self.max_iterations = max_iterations
        self.timeout_seconds = timeout_seconds
        self.category = category

    def to_dict(self) -> dict:
        return {
            "case_id": self.case_id,
            "prompt": self.prompt,
            "expected_files": self.expected_files,
            "expected_keywords": self.expected_keywords,
            "verify_command": self.verify_command,
            "max_iterations": self.max_iterations,
            "timeout_seconds": self.timeout_seconds,
            "category": self.category,
        }


class BenchmarkResult:
    """一个 Benchmark 测试用例的运行结果。"""

    def __init__(self, case: BenchmarkCase):
        self.case = case
        self.passed = False
        self.duration_ms = 0.0
        self.iterations = 0
        self.tool_calls_count = 0
        self.total_tokens = 0
        self.files_created: list[str] = []
        self.error = ""
        self.checks: dict[str, bool] = {}  # 各项检查的结果

    def to_dict(self) -> dict:
        return {
            "case_id": self.case.case_id,
            "category": self.case.category,
            "passed": self.passed,
            "duration_ms": round(self.duration_ms, 1),
            "iterations": self.iterations,
            "tool_calls": self.tool_calls_count,
            "total_tokens": self.total_tokens,
            "checks": self.checks,
            "error": self.error,
        }


class BenchmarkRunner:
    """Benchmark 测试运行器。

    批量运行测试用例, 收集结果, 生成评估报告。

    使用方式:
        runner = BenchmarkRunner()
        runner.add_case(BenchmarkCase("t1", "create a hello.py"))
        results = runner.run_all(agent_fn)
        print(runner.report(results))
    """

    def __init__(self):
        self.cases: list[BenchmarkCase] = []

    def add_case(self, case: BenchmarkCase):
        """添加测试用例。"""
        self.cases.append(case)

    def add_cases_from_file(self, path: str | Path):
        """从 JSON 文件批量加载测试用例。"""
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        for item in data:
            self.cases.append(BenchmarkCase(**item))

    def evaluate_result(
        self,
        case: BenchmarkCase,
        messages: list[dict],
        tool_calls: list[dict],
    ) -> BenchmarkResult:
        """评估一次运行是否通过。"""
        result = BenchmarkResult(case)
        result.iterations = sum(1 for m in messages if m.get("role") == "assistant")
        result.tool_calls_count = len(tool_calls)

        all_passed = True

        # 检查预期文件是否存在
        if case.expected_files:
            for f in case.expected_files:
                exists = Path(f).exists()
                result.checks[f"file:{f}"] = exists
                if not exists:
                    all_passed = False

        # 检查关键词是否出现在 Agent 输出中
        if case.expected_keywords:
            all_content = " ".join(
                str(m.get("content", "")) for m in messages
                if m.get("role") == "assistant"
            ).lower()
            for kw in case.expected_keywords:
                found = kw.lower() in all_content
                result.checks[f"keyword:{kw}"] = found
                if not found:
                    all_passed = False

        result.passed = all_passed
        return result

    def run_all(self, agent_fn, work_dir: str = ".") -> list[BenchmarkResult]:
        """批量运行所有测试用例。

        Args:
            agent_fn: Agent 执行函数, 签名: (prompt: str) -> (messages, tool_calls)
            work_dir: 工作目录

        Returns:
            所有测试用例的结果列表
        """
        results = []
        for case in self.cases:
            print(f"  Running: {case.case_id} - {case.prompt[:50]}...")
            start = time.time()
            try:
                messages, tool_calls = agent_fn(case.prompt)
                result = self.evaluate_result(case, messages, tool_calls)
                result.duration_ms = (time.time() - start) * 1000
            except Exception as e:
                result = BenchmarkResult(case)
                result.error = str(e)
                result.duration_ms = (time.time() - start) * 1000

            results.append(result)
            status = "PASS" if result.passed else "FAIL"
            print(f"    [{status}] {result.duration_ms:.0f}ms, "
                  f"{result.iterations} iters, {result.tool_calls_count} tools")

        return results

    @staticmethod
    def report(results: list[BenchmarkResult]) -> str:
        """生成 Benchmark 报告。"""
        if not results:
            return "(no results)"

        total = len(results)
        passed = sum(1 for r in results if r.passed)
        failed = total - passed

        lines = [
            "=" * 55,
            "  Benchmark Report",
            "=" * 55,
            f"\n  Total: {total} | Passed: {passed} | Failed: {failed} "
            f"| Pass rate: {passed/total*100:.0f}%",
        ]

        # 按类别汇总
        categories: dict[str, dict] = {}
        for r in results:
            cat = r.case.category
            if cat not in categories:
                categories[cat] = {"total": 0, "passed": 0, "duration": 0}
            categories[cat]["total"] += 1
            if r.passed:
                categories[cat]["passed"] += 1
            categories[cat]["duration"] += r.duration_ms

        if len(categories) > 1:
            lines.append("\n  By Category:")
            for cat, data in sorted(categories.items()):
                rate = data["passed"] / data["total"] * 100 if data["total"] > 0 else 0
                avg_dur = data["duration"] / data["total"] if data["total"] > 0 else 0
                lines.append(
                    f"    {cat}: {data['passed']}/{data['total']} "
                    f"({rate:.0f}%), avg {avg_dur:.0f}ms"
                )

        # 失败用例详情
        failed_cases = [r for r in results if not r.passed]
        if failed_cases:
            lines.append("\n  Failed Cases:")
            for r in failed_cases:
                lines.append(f"    - {r.case.case_id}: {r.error or 'checks failed'}")
                for check, ok in r.checks.items():
                    if not ok:
                        lines.append(f"        [FAIL] {check}")

        # 效率统计
        total_duration = sum(r.duration_ms for r in results)
        total_iters = sum(r.iterations for r in results)
        total_tools = sum(r.tool_calls_count for r in results)
        lines.append(f"\n  Total time: {total_duration/1000:.1f}s")
        lines.append(f"  Total iterations: {total_iters}")
        lines.append(f"  Total tool calls: {total_tools}")

        lines.append("=" * 55)
        return "\n".join(lines)


# =============================================================
#  10.2.3 Harness 参数的迭代调优
# =============================================================

class HarnessConfig:
    """可调优的 Harness 参数集合。

    将所有影响 Agent 行为的关键参数集中到一个配置对象中,
    便于进行系统化的参数调优实验。
    """

    def __init__(
        self,
        model: str = "deepseek-chat",
        temperature: float = 0.3,
        max_iterations: int = 25,
        context_soft_limit: int = 80000,
        tool_result_max_chars: int = 3000,
        keep_recent_tool_results: int = 3,
        loop_threshold: int = 3,
        verify_interval: int = 3,
        max_fix_attempts: int = 3,
        total_time_budget: float = 600,
        per_task_time_budget: float = 120,
        max_api_calls: int = 100,
    ):
        self.model = model
        self.temperature = temperature
        self.max_iterations = max_iterations
        self.context_soft_limit = context_soft_limit
        self.tool_result_max_chars = tool_result_max_chars
        self.keep_recent_tool_results = keep_recent_tool_results
        self.loop_threshold = loop_threshold
        self.verify_interval = verify_interval
        self.max_fix_attempts = max_fix_attempts
        self.total_time_budget = total_time_budget
        self.per_task_time_budget = per_task_time_budget
        self.max_api_calls = max_api_calls

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items()}

    @classmethod
    def from_dict(cls, data: dict) -> "HarnessConfig":
        return cls(**{k: v for k, v in data.items() if k in cls.__init__.__code__.co_varnames})

    def diff(self, other: "HarnessConfig") -> dict:
        """比较两个配置的差异。"""
        diffs = {}
        for key in self.__dict__:
            v1 = getattr(self, key)
            v2 = getattr(other, key)
            if v1 != v2:
                diffs[key] = {"from": v1, "to": v2}
        return diffs


class TuningExperiment:
    """Harness 参数调优实验。

    记录一次调优实验的配置、结果和度量指标,
    支持多次实验的横向对比, 帮助找到最优参数组合。
    """

    def __init__(
        self,
        experiment_id: str,
        config: HarnessConfig,
        description: str = "",
    ):
        self.experiment_id = experiment_id
        self.config = config
        self.description = description
        self.created_at = datetime.now().isoformat(timespec="seconds")
        self.metrics: dict = {}
        self.benchmark_results: list[dict] = []

    def record_metrics(
        self,
        pass_rate: float,
        avg_duration_ms: float,
        avg_iterations: float,
        avg_tool_calls: float,
        total_tokens: int,
        total_cost: float,
    ):
        """记录实验的汇总指标。"""
        self.metrics = {
            "pass_rate": pass_rate,
            "avg_duration_ms": avg_duration_ms,
            "avg_iterations": avg_iterations,
            "avg_tool_calls": avg_tool_calls,
            "total_tokens": total_tokens,
            "total_cost": total_cost,
        }

    def to_dict(self) -> dict:
        return {
            "experiment_id": self.experiment_id,
            "description": self.description,
            "created_at": self.created_at,
            "config": self.config.to_dict(),
            "metrics": self.metrics,
            "benchmark_results": self.benchmark_results,
        }

    @staticmethod
    def compare(experiments: list["TuningExperiment"]) -> str:
        """对比多次实验的结果。"""
        if not experiments:
            return "(no experiments)"

        lines = [
            "=" * 65,
            "  Tuning Experiment Comparison",
            "=" * 65,
        ]

        # 表头
        header = f"  {'ID':<12} {'Pass%':>7} {'Dur(ms)':>9} {'Iters':>7} {'Tools':>7} {'Cost':>9}"
        lines.append(header)
        lines.append("  " + "-" * 55)

        for exp in experiments:
            m = exp.metrics
            lines.append(
                f"  {exp.experiment_id:<12} "
                f"{m.get('pass_rate', 0)*100:>6.0f}% "
                f"{m.get('avg_duration_ms', 0):>9.0f} "
                f"{m.get('avg_iterations', 0):>7.1f} "
                f"{m.get('avg_tool_calls', 0):>7.1f} "
                f"${m.get('total_cost', 0):>8.4f}"
            )

        # 配置差异
        if len(experiments) >= 2:
            lines.append("\n  Config Changes (vs first):")
            baseline = experiments[0].config
            for exp in experiments[1:]:
                diffs = baseline.diff(exp.config)
                if diffs:
                    diff_parts = [f"{k}: {v['from']}->{v['to']}" for k, v in diffs.items()]
                    lines.append(f"    {exp.experiment_id}: {', '.join(diff_parts)}")

        lines.append("=" * 65)
        return "\n".join(lines)


def save_experiments(experiments: list[TuningExperiment], path: str | Path):
    """保存实验结果到 JSON 文件。"""
    data = [exp.to_dict() for exp in experiments]
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


# =============================================================
#  演示
# =============================================================

if __name__ == "__main__":
    print("=== 评估与调优演示 ===\n")

    # 1. 失败模式检测
    print("--- 失败模式检测 ---")
    # 模拟工具调用历史
    mock_tool_calls = [
        {"tool": "tool_read_file", "args": {"path": "main.py"}, "result": "def main(): pass"},
        {"tool": "tool_write_file", "args": {"path": "out.py", "content": "x=1"}, "result": "ok"},
        {"tool": "tool_write_file", "args": {"path": "out.py", "content": "x=1"}, "result": "ok"},
        {"tool": "tool_write_file", "args": {"path": "out.py", "content": "x=1"}, "result": "ok"},
        {"tool": "tool_read_file", "args": {"path": "ghost.py"}, "result": "Error: file not found"},
    ]
    mock_messages = [
        {"role": "system", "content": "You are FunHarness."},
        {"role": "user", "content": "Fix the bug in main.py"},
        {"role": "assistant", "content": "OK"},
    ]

    findings = FailurePattern.analyze(mock_messages, mock_tool_calls)
    print(FailurePattern.report(findings))

    # 2. Benchmark 系统
    print("--- Benchmark 系统 ---")
    runner = BenchmarkRunner()
    runner.add_case(BenchmarkCase(
        case_id="file_create",
        prompt="Create a file called hello.py with print('hello')",
        expected_keywords=["hello.py", "created"],
        category="file_ops",
    ))
    runner.add_case(BenchmarkCase(
        case_id="code_review",
        prompt="Review the code quality of main.py",
        expected_keywords=["review", "code"],
        category="analysis",
    ))

    # 模拟运行(不需要真正的 Agent)
    def mock_agent(prompt):
        messages = [
            {"role": "user", "content": prompt},
            {"role": "assistant", "content": f"I created hello.py and reviewed the code quality."},
        ]
        tool_calls = [
            {"tool": "tool_write_file", "args": {"path": "hello.py"}, "result": "ok"},
        ]
        return messages, tool_calls

    results = runner.run_all(mock_agent)
    print(BenchmarkRunner.report(results))

    # 3. Harness 参数调优
    print("\n--- Harness 参数调优 ---")
    config1 = HarnessConfig(temperature=0.3, max_iterations=25)
    config2 = HarnessConfig(temperature=0.1, max_iterations=15)

    exp1 = TuningExperiment("baseline", config1, "Default settings")
    exp1.record_metrics(
        pass_rate=0.75,
        avg_duration_ms=5200,
        avg_iterations=8.3,
        avg_tool_calls=12.1,
        total_tokens=45000,
        total_cost=0.1250,
    )

    exp2 = TuningExperiment("low_temp", config2, "Lower temperature, fewer iterations")
    exp2.record_metrics(
        pass_rate=0.85,
        avg_duration_ms=3800,
        avg_iterations=6.1,
        avg_tool_calls=9.4,
        total_tokens=32000,
        total_cost=0.0890,
    )

    print(TuningExperiment.compare([exp1, exp2]))

    # 配置差异
    print("\nConfig diff:")
    diffs = config1.diff(config2)
    for k, v in diffs.items():
        print(f"  {k}: {v['from']} -> {v['to']}")
