"""
10.1 - Agent 的可观测性

实现三个核心可观测性机制:
- TraceSpan / Tracer: 结构化跟踪, 记录 Agent 每一步的执行路径
- StructuredLogger: 结构化日志, 替代 print 的正式日志系统
- CostDashboard: Token 用量与成本分析面板, 汇总展示运行数据

运行方式:
    uv run python chapter10/observability.py
"""
import json
import os
import sys
import time
import uuid
from datetime import datetime
from enum import Enum
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")


# =============================================================
#  10.1.2 Trace 与结构化日志设计
# =============================================================

class SpanKind(Enum):
    """Trace Span 的类型, 标识 Agent 执行路径中的不同阶段。"""
    AGENT_LOOP = "agent_loop"          # 一轮完整的 Agent Loop
    LLM_CALL = "llm_call"              # 一次 LLM API 调用
    TOOL_CALL = "tool_call"            # 一次工具调用
    HOOK = "hook"                       # 一次钩子执行
    MIDDLEWARE = "middleware"            # 一次中间件执行
    COMPACTION = "compaction"           # 一次上下文压缩
    VERIFICATION = "verification"       # 一次 BVF 验证
    SUB_AGENT = "sub_agent"            # 一次子 Agent 执行


class TraceSpan:
    """一个 Trace Span: Agent 执行路径中的一个离散步骤。

    每个 Span 记录了:
    - 操作类型(SpanKind)
    - 开始/结束时间
    - 输入/输出摘要
    - 元数据(token 用量、工具名等)
    - 父 Span ID(用于构建调用树)

    Span 的设计借鉴了 OpenTelemetry 的概念,
    但做了大幅简化, 只保留 Agent 场景中最有价值的字段。
    """

    def __init__(
        self,
        kind: SpanKind,
        name: str = "",
        parent_id: str = "",
        metadata: dict | None = None,
    ):
        self.span_id = uuid.uuid4().hex[:12]
        self.kind = kind
        self.name = name or kind.value
        self.parent_id = parent_id
        self.metadata = metadata or {}
        self.start_time = time.time()
        self.end_time: float | None = None
        self.input_summary = ""
        self.output_summary = ""
        self.status = "running"  # running, ok, error
        self.error_message = ""
        self.token_usage = {"input": 0, "output": 0}

    def finish(self, status: str = "ok", error: str = ""):
        """标记 Span 完成。"""
        self.end_time = time.time()
        self.status = status
        self.error_message = error

    @property
    def duration_ms(self) -> float:
        """Span 持续时间(毫秒)。"""
        end = self.end_time or time.time()
        return (end - self.start_time) * 1000

    def to_dict(self) -> dict:
        return {
            "span_id": self.span_id,
            "kind": self.kind.value,
            "name": self.name,
            "parent_id": self.parent_id,
            "status": self.status,
            "start_time": datetime.fromtimestamp(self.start_time).isoformat(timespec="milliseconds"),
            "duration_ms": round(self.duration_ms, 1),
            "input_summary": self.input_summary[:200],
            "output_summary": self.output_summary[:200],
            "token_usage": self.token_usage,
            "metadata": self.metadata,
            "error": self.error_message,
        }

    def __repr__(self):
        status_icon = {"running": "~", "ok": "+", "error": "!"}[self.status]
        return (
            f"[{status_icon}] {self.kind.value}:{self.name} "
            f"({self.duration_ms:.0f}ms)"
        )


class Tracer:
    """Agent 执行路径的跟踪器。

    Tracer 管理一棵 Span 树, 从 Agent Loop 的启动到结束,
    记录每一次 LLM 调用、工具执行、钩子触发等操作。

    使用方式:
        tracer = Tracer()
        span = tracer.start_span(SpanKind.LLM_CALL, "gpt-4o")
        # ... 执行操作 ...
        span.finish()

    也可以用上下文管理器:
        with tracer.span(SpanKind.TOOL_CALL, "read_file") as s:
            result = read_file(path)
            s.output_summary = result[:100]
    """

    def __init__(self, trace_id: str = ""):
        self.trace_id = trace_id or uuid.uuid4().hex[:16]
        self.spans: list[TraceSpan] = []
        self._span_stack: list[str] = []  # 当前的 Span ID 栈
        self.created_at = datetime.now().isoformat(timespec="seconds")

    def start_span(
        self,
        kind: SpanKind,
        name: str = "",
        metadata: dict | None = None,
    ) -> TraceSpan:
        """开始一个新的 Span。

        自动将当前栈顶的 Span 设为父 Span。
        """
        parent_id = self._span_stack[-1] if self._span_stack else ""
        span = TraceSpan(
            kind=kind,
            name=name,
            parent_id=parent_id,
            metadata=metadata,
        )
        self.spans.append(span)
        self._span_stack.append(span.span_id)
        return span

    def finish_span(self, span: TraceSpan, status: str = "ok", error: str = ""):
        """结束一个 Span 并从栈中弹出。"""
        span.finish(status=status, error=error)
        if self._span_stack and self._span_stack[-1] == span.span_id:
            self._span_stack.pop()

    class _SpanContext:
        """上下文管理器, 自动管理 Span 的生命周期。"""
        def __init__(self, tracer: "Tracer", span: TraceSpan):
            self._tracer = tracer
            self._span = span

        def __enter__(self) -> TraceSpan:
            return self._span

        def __exit__(self, exc_type, exc_val, exc_tb):
            if exc_type:
                self._tracer.finish_span(
                    self._span, status="error", error=str(exc_val)
                )
            else:
                self._tracer.finish_span(self._span)
            return False

    def span(
        self,
        kind: SpanKind,
        name: str = "",
        metadata: dict | None = None,
    ) -> _SpanContext:
        """创建一个 Span 的上下文管理器。"""
        s = self.start_span(kind, name, metadata)
        return self._SpanContext(self, s)

    # ----- 查询与统计 -----

    @property
    def total_duration_ms(self) -> float:
        """整个 Trace 的总时长。"""
        if not self.spans:
            return 0.0
        start = min(s.start_time for s in self.spans)
        end = max(s.end_time or time.time() for s in self.spans)
        return (end - start) * 1000

    @property
    def total_tokens(self) -> dict:
        """汇总所有 Span 的 Token 用量。"""
        total_in = sum(s.token_usage.get("input", 0) for s in self.spans)
        total_out = sum(s.token_usage.get("output", 0) for s in self.spans)
        return {"input": total_in, "output": total_out, "total": total_in + total_out}

    def get_spans_by_kind(self, kind: SpanKind) -> list[TraceSpan]:
        """按类型筛选 Span。"""
        return [s for s in self.spans if s.kind == kind]

    def get_error_spans(self) -> list[TraceSpan]:
        """获取所有出错的 Span。"""
        return [s for s in self.spans if s.status == "error"]

    def summary(self) -> str:
        """生成 Trace 的文本摘要。"""
        lines = [
            f"Trace: {self.trace_id}",
            f"Duration: {self.total_duration_ms:.0f}ms",
            f"Spans: {len(self.spans)}",
        ]

        # 按类型统计
        kind_counts: dict[str, int] = {}
        kind_durations: dict[str, float] = {}
        for s in self.spans:
            k = s.kind.value
            kind_counts[k] = kind_counts.get(k, 0) + 1
            kind_durations[k] = kind_durations.get(k, 0) + s.duration_ms

        lines.append("Breakdown:")
        for k, count in sorted(kind_counts.items()):
            dur = kind_durations[k]
            lines.append(f"  {k}: {count}x, {dur:.0f}ms total")

        tokens = self.total_tokens
        if tokens["total"] > 0:
            lines.append(
                f"Tokens: {tokens['input']:,} in + "
                f"{tokens['output']:,} out = {tokens['total']:,}"
            )

        errors = self.get_error_spans()
        if errors:
            lines.append(f"Errors: {len(errors)}")
            for e in errors[:3]:
                lines.append(f"  - [{e.kind.value}] {e.error_message[:80]}")

        return "\n".join(lines)

    def timeline(self) -> str:
        """生成时间线视图: 按时间顺序展示所有 Span。"""
        if not self.spans:
            return "(empty trace)"

        base_time = self.spans[0].start_time
        lines = [f"Timeline (Trace: {self.trace_id})"]
        lines.append("-" * 60)

        for s in self.spans:
            offset_ms = (s.start_time - base_time) * 1000
            depth = 0
            pid = s.parent_id
            while pid:
                depth += 1
                parent = next((p for p in self.spans if p.span_id == pid), None)
                pid = parent.parent_id if parent else ""
            indent = "  " * depth
            status_icon = {"running": "~", "ok": "+", "error": "!"}[s.status]
            lines.append(
                f"  {offset_ms:7.0f}ms {indent}[{status_icon}] "
                f"{s.kind.value}:{s.name} ({s.duration_ms:.0f}ms)"
            )

        return "\n".join(lines)

    def to_dict(self) -> dict:
        """导出为可序列化的字典。"""
        return {
            "trace_id": self.trace_id,
            "created_at": self.created_at,
            "total_duration_ms": round(self.total_duration_ms, 1),
            "total_tokens": self.total_tokens,
            "span_count": len(self.spans),
            "spans": [s.to_dict() for s in self.spans],
        }

    def save(self, path: str | Path):
        """将 Trace 保存为 JSON 文件。"""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    @classmethod
    def load(cls, path: str | Path) -> "Tracer":
        """从 JSON 文件加载 Trace。"""
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        tracer = cls(trace_id=data["trace_id"])
        tracer.created_at = data.get("created_at", "")
        for sd in data.get("spans", []):
            span = TraceSpan(
                kind=SpanKind(sd["kind"]),
                name=sd.get("name", ""),
                parent_id=sd.get("parent_id", ""),
                metadata=sd.get("metadata", {}),
            )
            span.span_id = sd["span_id"]
            span.status = sd.get("status", "ok")
            span.error_message = sd.get("error", "")
            span.input_summary = sd.get("input_summary", "")
            span.output_summary = sd.get("output_summary", "")
            span.token_usage = sd.get("token_usage", {"input": 0, "output": 0})
            # 从 duration_ms 还原时间
            span.start_time = 0
            span.end_time = sd.get("duration_ms", 0) / 1000
            tracer.spans.append(span)
        return tracer


# =============================================================
#  10.1.2 结构化日志
# =============================================================

class LogLevel(Enum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARN = "WARN"
    ERROR = "ERROR"


class StructuredLogger:
    """Agent 专用的结构化日志系统。

    与 print 调试相比, 结构化日志的优势:
    1. 每条日志带时间戳和来源标识
    2. 按级别过滤, 避免信息过载
    3. 可输出为 JSON 格式, 便于后续分析
    4. 支持附带上下文字段(如 tool_name, token_count)

    不依赖 logging 标准库, 保持最小实现。
    """

    def __init__(
        self,
        name: str = "FunHarness",
        level: LogLevel = LogLevel.INFO,
        json_output: bool = False,
    ):
        self.name = name
        self.level = level
        self.json_output = json_output
        self._entries: list[dict] = []
        self._level_order = {
            LogLevel.DEBUG: 0,
            LogLevel.INFO: 1,
            LogLevel.WARN: 2,
            LogLevel.ERROR: 3,
        }

    def _should_log(self, level: LogLevel) -> bool:
        return self._level_order[level] >= self._level_order[self.level]

    def _emit(self, level: LogLevel, message: str, **fields):
        """记录一条日志。"""
        if not self._should_log(level):
            return

        entry = {
            "timestamp": datetime.now().isoformat(timespec="milliseconds"),
            "level": level.value,
            "source": self.name,
            "message": message,
        }
        if fields:
            entry["fields"] = fields

        self._entries.append(entry)

        # 同时输出到标准错误
        if self.json_output:
            print(json.dumps(entry, ensure_ascii=False), file=sys.stderr)
        else:
            field_str = ""
            if fields:
                parts = [f"{k}={v}" for k, v in fields.items()]
                field_str = " | " + ", ".join(parts)
            ts = entry["timestamp"].split("T")[1]
            print(
                f"  [{ts}] [{level.value:5s}] {message}{field_str}",
                file=sys.stderr,
            )

    def debug(self, message: str, **fields):
        self._emit(LogLevel.DEBUG, message, **fields)

    def info(self, message: str, **fields):
        self._emit(LogLevel.INFO, message, **fields)

    def warn(self, message: str, **fields):
        self._emit(LogLevel.WARN, message, **fields)

    def error(self, message: str, **fields):
        self._emit(LogLevel.ERROR, message, **fields)

    # ----- Agent 专用的便捷方法 -----

    def log_llm_call(
        self,
        model: str,
        input_tokens: int,
        output_tokens: int,
        duration_ms: float,
        stream: bool = False,
    ):
        """记录一次 LLM API 调用。"""
        self.info(
            "LLM call completed",
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            duration_ms=round(duration_ms, 1),
            stream=stream,
        )

    def log_tool_call(
        self,
        tool_name: str,
        duration_ms: float,
        success: bool,
        result_length: int = 0,
    ):
        """记录一次工具调用。"""
        level = LogLevel.INFO if success else LogLevel.WARN
        self._emit(
            level,
            f"Tool: {tool_name}",
            duration_ms=round(duration_ms, 1),
            success=success,
            result_chars=result_length,
        )

    def log_hook(self, hook_name: str, event: str, action: str):
        """记录一次钩子执行。"""
        self.debug(
            f"Hook: {hook_name}",
            event=event,
            action=action,
        )

    def log_compaction(self, before_msgs: int, after_msgs: int, saved_tokens: int):
        """记录一次上下文压缩。"""
        self.info(
            "Context compacted",
            before_messages=before_msgs,
            after_messages=after_msgs,
            saved_tokens=saved_tokens,
        )

    # ----- 查询与导出 -----

    def get_entries(
        self,
        level: LogLevel | None = None,
        source: str = "",
    ) -> list[dict]:
        """查询日志条目。"""
        results = self._entries
        if level:
            results = [e for e in results if e["level"] == level.value]
        if source:
            results = [e for e in results if e["source"] == source]
        return results

    def export(self, path: str | Path):
        """导出所有日志到 JSON 文件。"""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self._entries, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def tail(self, n: int = 10) -> str:
        """获取最近 n 条日志的文本。"""
        recent = self._entries[-n:]
        lines = []
        for entry in recent:
            fields = entry.get("fields", {})
            field_str = ""
            if fields:
                parts = [f"{k}={v}" for k, v in fields.items()]
                field_str = " | " + ", ".join(parts)
            ts = entry["timestamp"].split("T")[1]
            lines.append(
                f"[{ts}] [{entry['level']:5s}] {entry['message']}{field_str}"
            )
        return "\n".join(lines)


# =============================================================
#  10.1.3 Token 用量与成本分析面板
# =============================================================

class CostDashboard:
    """Token 用量与成本分析面板。

    汇总整次 Agent 运行的 Token 消耗, 按调用类型分类统计,
    生成一个人类可读的成本分析报告。

    数据来源:
    - Tracer 中每个 LLM_CALL Span 的 token_usage
    - CostTracker 的累计数据(如果有)
    """

    def __init__(
        self,
        input_price: float = 2.5,
        output_price: float = 10.0,
    ):
        """
        Args:
            input_price: 每百万 input tokens 的价格(美元)
            output_price: 每百万 output tokens 的价格(美元)
        """
        self.input_price_per_m = input_price
        self.output_price_per_m = output_price
        self._records: list[dict] = []

    def record(
        self,
        category: str,
        input_tokens: int,
        output_tokens: int,
        model: str = "",
        duration_ms: float = 0,
    ):
        """记录一条 Token 消耗明细。

        Args:
            category: 调用类别, 如 "agent_loop", "compaction", "sub_agent", "planning"
            input_tokens: 输入 Token 数
            output_tokens: 输出 Token 数
            model: 模型名称
            duration_ms: 调用耗时
        """
        self._records.append({
            "category": category,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "model": model,
            "duration_ms": duration_ms,
            "timestamp": datetime.now().isoformat(timespec="seconds"),
        })

    def from_tracer(self, tracer: Tracer):
        """从 Tracer 中导入所有 LLM 调用的 Token 数据。"""
        for span in tracer.get_spans_by_kind(SpanKind.LLM_CALL):
            self.record(
                category=span.metadata.get("category", "agent_loop"),
                input_tokens=span.token_usage.get("input", 0),
                output_tokens=span.token_usage.get("output", 0),
                model=span.name,
                duration_ms=span.duration_ms,
            )

    def _cost(self, input_tokens: int, output_tokens: int) -> float:
        """计算成本(美元)。"""
        return (
            input_tokens * self.input_price_per_m / 1_000_000
            + output_tokens * self.output_price_per_m / 1_000_000
        )

    @property
    def total_input_tokens(self) -> int:
        return sum(r["input_tokens"] for r in self._records)

    @property
    def total_output_tokens(self) -> int:
        return sum(r["output_tokens"] for r in self._records)

    @property
    def total_cost(self) -> float:
        return self._cost(self.total_input_tokens, self.total_output_tokens)

    def report(self) -> str:
        """生成成本分析报告。"""
        if not self._records:
            return "(no data recorded)"

        lines = [
            "=" * 50,
            "  Token Usage & Cost Report",
            "=" * 50,
        ]

        # 总览
        total_in = self.total_input_tokens
        total_out = self.total_output_tokens
        lines.append(
            f"\n  Total:  {total_in + total_out:>10,} tokens "
            f"(in: {total_in:,} | out: {total_out:,})"
        )
        lines.append(f"  Cost:   ${self.total_cost:>10.4f}")
        lines.append(f"  Calls:  {len(self._records):>10}")

        # 按类别分组
        categories: dict[str, dict] = {}
        for r in self._records:
            cat = r["category"]
            if cat not in categories:
                categories[cat] = {"count": 0, "input": 0, "output": 0, "duration": 0}
            categories[cat]["count"] += 1
            categories[cat]["input"] += r["input_tokens"]
            categories[cat]["output"] += r["output_tokens"]
            categories[cat]["duration"] += r["duration_ms"]

        if len(categories) > 1:
            lines.append("\n  By Category:")
            lines.append(f"  {'Category':<18} {'Calls':>6} {'Input':>10} {'Output':>10} {'Cost':>10}")
            lines.append("  " + "-" * 58)
            for cat, data in sorted(categories.items()):
                cost = self._cost(data["input"], data["output"])
                lines.append(
                    f"  {cat:<18} {data['count']:>6} "
                    f"{data['input']:>10,} {data['output']:>10,} "
                    f"${cost:>9.4f}"
                )

        # 按模型分组
        models: dict[str, dict] = {}
        for r in self._records:
            m = r.get("model", "unknown") or "unknown"
            if m not in models:
                models[m] = {"count": 0, "input": 0, "output": 0}
            models[m]["count"] += 1
            models[m]["input"] += r["input_tokens"]
            models[m]["output"] += r["output_tokens"]

        if len(models) > 1:
            lines.append("\n  By Model:")
            for m, data in sorted(models.items()):
                cost = self._cost(data["input"], data["output"])
                lines.append(
                    f"    {m}: {data['count']} calls, "
                    f"{data['input'] + data['output']:,} tokens, "
                    f"${cost:.4f}"
                )

        # 效率指标
        total_duration = sum(r["duration_ms"] for r in self._records)
        if total_duration > 0:
            tokens_per_sec = (total_in + total_out) / (total_duration / 1000)
            lines.append(f"\n  Avg throughput: {tokens_per_sec:.0f} tokens/sec")
        if len(self._records) > 0:
            avg_in = total_in / len(self._records)
            avg_out = total_out / len(self._records)
            lines.append(
                f"  Avg per call: {avg_in:.0f} in + {avg_out:.0f} out"
            )

        lines.append("=" * 50)
        return "\n".join(lines)

    def export(self, path: str | Path):
        """导出原始数据到 JSON。"""
        data = {
            "summary": {
                "total_input_tokens": self.total_input_tokens,
                "total_output_tokens": self.total_output_tokens,
                "total_cost": round(self.total_cost, 6),
                "total_calls": len(self._records),
            },
            "records": self._records,
        }
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
    print("=== Agent 可观测性演示 ===\n")

    # 1. Trace 系统
    print("--- Trace 系统 ---")
    tracer = Tracer()

    # 模拟一次完整的 Agent Loop
    with tracer.span(SpanKind.AGENT_LOOP, "main_loop") as loop_span:
        loop_span.input_summary = "user: Read and summarize README.md"

        # 模拟 LLM 调用
        with tracer.span(SpanKind.LLM_CALL, "deepseek-v4-flash") as llm_span:
            time.sleep(0.05)  # 模拟延迟
            llm_span.token_usage = {"input": 1500, "output": 200}
            llm_span.output_summary = "I'll read the file..."
            llm_span.metadata["category"] = "agent_loop"

        # 模拟工具调用
        with tracer.span(SpanKind.TOOL_CALL, "tool_read_file") as tool_span:
            time.sleep(0.02)
            tool_span.input_summary = "path=README.md"
            tool_span.output_summary = "# FunHarness\nA minimal AI agent..."
            tool_span.metadata["tool_name"] = "tool_read_file"

        # 模拟第二次 LLM 调用
        with tracer.span(SpanKind.LLM_CALL, "deepseek-v4-flash") as llm_span2:
            time.sleep(0.05)
            llm_span2.token_usage = {"input": 2000, "output": 500}
            llm_span2.output_summary = "The README describes FunHarness..."
            llm_span2.metadata["category"] = "agent_loop"

        loop_span.output_summary = "Completed: read and summarized README.md"

    print(tracer.summary())
    print()
    print(tracer.timeline())

    # 2. 结构化日志
    print("\n\n--- 结构化日志 ---")
    logger = StructuredLogger(level=LogLevel.DEBUG)

    logger.info("Agent started", version="v0.8", mode="suggest")
    logger.log_llm_call("deepseek-v4-flash", 1500, 200, 523.4)
    logger.log_tool_call("tool_read_file", 12.3, True, result_length=4500)
    logger.log_tool_call("tool_run_command", 1023.7, False, result_length=0)
    logger.log_hook("path_normalizer", "pre_tool_use", "modify")
    logger.log_compaction(24, 6, 15000)
    logger.warn("Token budget running low", remaining_pct=18)
    logger.error("Tool execution failed", tool="tool_run_command", error="timeout")

    print(f"\nLog entries: {len(logger._entries)}")
    print(f"\nRecent logs:\n{logger.tail(5)}")

    # 3. 成本分析面板
    print("\n\n--- 成本分析面板 ---")
    dashboard = CostDashboard(input_price=2.5, output_price=10.0)

    # 模拟多次调用
    dashboard.record("agent_loop", 1500, 200, "deepseek-v4-flash", 523.4)
    dashboard.record("agent_loop", 2000, 500, "deepseek-v4-flash", 812.1)
    dashboard.record("agent_loop", 1800, 300, "deepseek-v4-flash", 634.5)
    dashboard.record("compaction", 3000, 800, "deepseek-v4-flash", 1200.0)
    dashboard.record("planning", 500, 400, "deepseek-v4-flash", 350.2)

    print(dashboard.report())

    # 从 Tracer 导入
    dashboard2 = CostDashboard()
    dashboard2.from_tracer(tracer)
    print(f"\nFrom tracer: {dashboard2.total_input_tokens} in + "
          f"{dashboard2.total_output_tokens} out")

    # 4. 保存
    import tempfile
    with tempfile.TemporaryDirectory() as tmpdir:
        tracer.save(os.path.join(tmpdir, "trace.json"))
        logger.export(os.path.join(tmpdir, "logs.json"))
        dashboard.export(os.path.join(tmpdir, "cost.json"))
        print(f"\nFiles saved to {tmpdir}")

        # 验证加载
        loaded_tracer = Tracer.load(os.path.join(tmpdir, "trace.json"))
        print(f"Loaded trace: {loaded_tracer.trace_id}, {len(loaded_tracer.spans)} spans")
