"""Deterministic quality checks for swarm worker outputs."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class AuditReport:
    ok: bool
    reasons: list[str] = field(default_factory=list)

    @property
    def message(self) -> str:
        return "; ".join(self.reasons)


class QualityAuditPipeline:
    def __init__(self, min_chars: int = 30):
        self.min_chars = min_chars

    def validate(self, output: str, acceptance: list[str] | None = None) -> AuditReport:
        text = (output or "").strip()
        reasons: list[str] = []
        low = text.lower()
        if not text:
            reasons.append("empty output")
        elif text.startswith("(subagent cancelled)"):
            reasons.append("worker cancelled")
        elif text.startswith("(subagent timed out)"):
            reasons.append("worker timed out")
        elif len(text) < self.min_chars:
            reasons.append("output too short")
        if low.startswith(("# plan", "## plan", "plan:", "phase 1", "## phase 1")) and len(text) < 600:
            reasons.append("plan-only output")
        for marker in ("mock data", "fabricated data", "placeholder data"):
            if marker in low:
                reasons.append("fabricated or placeholder data")
        for rule in acceptance or []:
            if rule.startswith("contains:"):
                needle = rule.split(":", 1)[1].strip()
                if needle and needle not in text:
                    reasons.append(f"missing required text: {needle}")
        return AuditReport(ok=not reasons, reasons=reasons)

    def retry_context(self, report: AuditReport) -> str:
        return (
            "Previous attempt did not meet the output contract.\n"
            f"Failure reasons: {report.message}\n"
            "Revise the answer into a concrete final deliverable. Do not return only a plan."
        )
