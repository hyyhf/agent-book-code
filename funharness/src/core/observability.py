"""
FunHarness - Observability

Tracing, structured logging, cost dashboard, failure pattern analysis.
"""
import json
import time
import uuid
from datetime import datetime
from enum import Enum
from pathlib import Path


# ================================================================
#  Tracing
# ================================================================

class SpanKind(Enum):
    LLM_CALL = "llm_call"
    TOOL_CALL = "tool_call"
    HOOK = "hook"
    MIDDLEWARE = "middleware"
    AGENT_LOOP = "agent_loop"
    COMPACTION = "compaction"


class TraceSpan:
    def __init__(self, kind, name, metadata=None):
        self.span_id = uuid.uuid4().hex[:8]
        self.kind = kind
        self.name = name
        self.metadata = metadata or {}
        self.start_time = time.time()
        self.end_time = None
        self.duration_ms = 0
        self.status = "running"
        self.error = ""
        self.input_summary = ""
        self.output_summary = ""
        self.token_usage = {}

    def finish(self, status="ok", error=""):
        self.end_time = time.time()
        self.duration_ms = (self.end_time - self.start_time) * 1000
        self.status = status
        self.error = error


class Tracer:
    def __init__(self):
        self.trace_id = uuid.uuid4().hex[:12]
        self.spans: list[TraceSpan] = []
        self._active_stack: list[TraceSpan] = []

    def start_span(self, kind, name, metadata=None):
        span = TraceSpan(kind, name, metadata)
        self.spans.append(span)
        self._active_stack.append(span)
        return span

    def finish_span(self, span, status="ok", error=""):
        span.finish(status, error)
        if self._active_stack and self._active_stack[-1] is span:
            self._active_stack.pop()

    class _SpanContext:
        def __init__(self, tracer, span):
            self._tracer = tracer
            self._span = span
        def __enter__(self):
            return self._span
        def __exit__(self, *args):
            status = "error" if args[0] else "ok"
            self._tracer.finish_span(self._span, status)

    def span(self, kind, name, metadata=None):
        s = self.start_span(kind, name, metadata)
        return self._SpanContext(self, s)

    def timeline(self) -> str:
        if not self.spans:
            return "(no spans)"
        lines = [f"Trace: {self.trace_id}"]
        for s in self.spans:
            dur = f"{s.duration_ms:.0f}ms" if s.end_time else "running"
            status_icon = {"ok": "[ok]", "error": "[ERR]", "running": "[...]"}.get(s.status, "[?]")
            lines.append(f"  {status_icon} {s.kind.value}:{s.name} ({dur})")
        return "\n".join(lines)

    def summary(self) -> str:
        total = len(self.spans)
        errors = sum(1 for s in self.spans if s.status == "error")
        kinds = {}
        for s in self.spans:
            kinds[s.kind.value] = kinds.get(s.kind.value, 0) + 1
        breakdown = ", ".join(f"{k}={v}" for k, v in sorted(kinds.items()))
        return f"Spans: {total} (errors: {errors}) | {breakdown}"

    def save(self, path):
        data = {
            "trace_id": self.trace_id,
            "spans": [{
                "span_id": s.span_id, "kind": s.kind.value, "name": s.name,
                "duration_ms": s.duration_ms, "status": s.status,
                "error": s.error, "metadata": s.metadata,
            } for s in self.spans],
        }
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


# ================================================================
#  Structured Logger
# ================================================================

class LogLevel(Enum):
    DEBUG = "debug"
    INFO = "info"
    WARN = "warn"
    ERROR = "error"

_LEVEL_ORDER = {LogLevel.DEBUG: 0, LogLevel.INFO: 1, LogLevel.WARN: 2, LogLevel.ERROR: 3}


class StructuredLogger:
    def __init__(self, name="FunHarness", level=LogLevel.INFO, json_output=False):
        self.name = name
        self.level = level
        self.json_output = json_output
        self._entries: list[dict] = []

    def _should_log(self, level):
        return _LEVEL_ORDER.get(level, 0) >= _LEVEL_ORDER.get(self.level, 0)

    def _log(self, level, message, **kwargs):
        entry = {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "level": level.value, "message": message, **kwargs,
        }
        self._entries.append(entry)

    def debug(self, msg, **kw): self._should_log(LogLevel.DEBUG) and self._log(LogLevel.DEBUG, msg, **kw)
    def info(self, msg, **kw): self._should_log(LogLevel.INFO) and self._log(LogLevel.INFO, msg, **kw)
    def warn(self, msg, **kw): self._should_log(LogLevel.WARN) and self._log(LogLevel.WARN, msg, **kw)
    def error(self, msg, **kw): self._should_log(LogLevel.ERROR) and self._log(LogLevel.ERROR, msg, **kw)

    def log_llm_call(self, model, input_tokens, output_tokens, duration_ms, **kw):
        self.info(f"LLM call: {model}", input_tokens=input_tokens,
                  output_tokens=output_tokens, duration_ms=f"{duration_ms:.0f}", **kw)

    def log_tool_call(self, tool_name, duration_ms, success, result_len):
        level = LogLevel.INFO if success else LogLevel.WARN
        self._log(level, f"Tool: {tool_name}", duration_ms=f"{duration_ms:.0f}",
                  success=success, result_len=result_len)

    def log_hook(self, hook_type, tool_name, action):
        self.info(f"Hook: {hook_type}", tool=tool_name, action=action)

    def log_compaction(self, before, after, saved):
        self.info(f"Compaction: {before} -> {after} messages", saved=saved)

    def tail(self, n=10) -> str:
        entries = self._entries[-n:]
        if not entries:
            return "(no logs)"
        lines = []
        for e in entries:
            ts = e["timestamp"].split("T")[-1]
            lvl = e["level"].upper()[:4]
            msg = e["message"]
            extra = {k: v for k, v in e.items() if k not in ("timestamp", "level", "message")}
            extra_str = f" | {extra}" if extra else ""
            lines.append(f"  [{ts}] {lvl} {msg}{extra_str}")
        return "\n".join(lines)

    def export(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps(self._entries, indent=2, ensure_ascii=False), encoding="utf-8")


# ================================================================
#  Cost Dashboard
# ================================================================

class CostDashboard:
    def __init__(self):
        self._records: list[dict] = []

    def record(self, category, input_tokens, output_tokens, model="", duration_ms=0):
        self._records.append({
            "category": category, "input_tokens": input_tokens,
            "output_tokens": output_tokens, "model": model,
            "duration_ms": duration_ms, "timestamp": datetime.now().isoformat(),
        })

    def report(self) -> str:
        if not self._records:
            return "(no data)"
        total_in = sum(r["input_tokens"] for r in self._records)
        total_out = sum(r["output_tokens"] for r in self._records)
        lines = [
            "=== Cost Dashboard ===",
            f"  Total calls: {len(self._records)}",
            f"  Input tokens: {total_in:,}",
            f"  Output tokens: {total_out:,}",
            f"  Total tokens: {total_in + total_out:,}",
        ]
        categories = {}
        for r in self._records:
            cat = r["category"]
            categories[cat] = categories.get(cat, 0) + 1
        lines.append("  By category: " + ", ".join(f"{k}={v}" for k, v in categories.items()))
        return "\n".join(lines)

    def export(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps(self._records, indent=2, ensure_ascii=False), encoding="utf-8")


# ================================================================
#  Failure Pattern
# ================================================================

class FailureType(Enum):
    REPEATED_FAILURE = "repeated_failure"
    WRONG_TOOL = "wrong_tool"
    INFINITE_LOOP = "infinite_loop"
    PERMISSION_DENIED = "permission_denied"


class FailurePattern:
    @staticmethod
    def analyze(messages, tool_calls_history):
        findings = []
        # Check for repeated failures
        error_tools = {}
        for h in tool_calls_history:
            result = h.get("result", "")
            if any(kw in result for kw in ["Error", "Failed", "DENIED"]):
                tool = h["tool"]
                error_tools[tool] = error_tools.get(tool, 0) + 1
        for tool, count in error_tools.items():
            if count >= 3:
                findings.append({"type": FailureType.REPEATED_FAILURE,
                                 "tool": tool, "count": count,
                                 "suggestion": f"Tool '{tool}' failed {count} times. Review usage."})
        # Check permission denials
        denials = [h for h in tool_calls_history if "DENIED" in h.get("result", "")]
        if len(denials) >= 2:
            findings.append({"type": FailureType.PERMISSION_DENIED,
                             "count": len(denials),
                             "suggestion": "Multiple permission denials. Check mode or path policy."})
        return findings

    @staticmethod
    def report(findings):
        if not findings:
            return "  No failure patterns detected."
        lines = [f"  Found {len(findings)} pattern(s):"]
        for f in findings:
            ftype = f["type"].value if isinstance(f["type"], FailureType) else str(f["type"])
            lines.append(f"  - [{ftype}] {f.get('suggestion', '')}")
        return "\n".join(lines)
