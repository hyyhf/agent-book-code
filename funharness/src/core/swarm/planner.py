"""Deterministic team builders for higher-level swarm modes."""
from __future__ import annotations

from dataclasses import dataclass, field

from .models import SwarmAgentSpec, SwarmTask


@dataclass
class SwarmPlan:
    agents: list[SwarmAgentSpec] = field(default_factory=list)
    tasks: list[SwarmTask] = field(default_factory=list)


class AdaptiveTeamBuilder:
    def __init__(self, learned_roles: list[str] | None = None):
        self.learned_roles = learned_roles or []

    def build(self, goal: str) -> SwarmPlan:
        text = goal.lower()
        if _looks_like_debate(text):
            return debate_plan(goal)
        if _looks_like_code(text):
            return code_plan(goal, self.learned_roles)
        return research_plan(goal, self.learned_roles)


def research_plan(goal: str, learned_roles: list[str] | None = None) -> SwarmPlan:
    learned_note = _learned_note(learned_roles)
    agents = [
        SwarmAgentSpec("lead", "Lead planner", "Plan the collaboration and spawn follow-up tasks when gaps are discovered." + learned_note, max_retries=1),
        SwarmAgentSpec("researcher", "Researcher", "Collect grounded findings and publish reusable findings to the blackboard.", max_retries=1),
        SwarmAgentSpec("critic", "Quality critic", "Find missing evidence, contradictions, and weak reasoning.", max_retries=1),
        SwarmAgentSpec("synthesizer", "Synthesizer", "Merge upstream results into a concrete final deliverable.", max_retries=1),
    ]
    tasks = [
        SwarmTask("plan", "lead", f"Plan how the swarm should handle this goal: {goal}", allow_spawn=True, acceptance=["contains: 计划"]),
        SwarmTask("research", "researcher", f"Research the goal and produce key findings: {goal}", depends_on=["plan"], input_from={"plan": "plan"}, acceptance=["contains: 关键发现"]),
        SwarmTask("critique", "critic", "Critique the research and identify risks.", depends_on=["research"], input_from={"research": "research"}, acceptance=["contains: 风险"]),
        SwarmTask("synthesize", "synthesizer", f"Create the final report for: {goal}", depends_on=["research", "critique"], input_from={"research": "research", "critique": "critique"}, acceptance=["contains: 最终报告"]),
    ]
    return SwarmPlan(agents, tasks)


def code_plan(goal: str, learned_roles: list[str] | None = None) -> SwarmPlan:
    learned_note = _learned_note(learned_roles)
    agents = [
        SwarmAgentSpec("lead", "Lead planner", "Plan implementation work and spawn follow-up tasks when useful." + learned_note, max_retries=1),
        SwarmAgentSpec("engineer", "Engineer", "Design the smallest viable implementation path.", max_retries=1),
        SwarmAgentSpec("reviewer", "Code reviewer", "Review risks, regressions, and missing tests.", max_retries=1),
        SwarmAgentSpec("tester", "Test strategist", "Define verification steps and edge cases.", max_retries=1),
        SwarmAgentSpec("synthesizer", "Delivery lead", "Merge implementation, review, and test findings into a final plan.", max_retries=1),
    ]
    tasks = [
        SwarmTask("plan", "lead", f"计划这个代码目标的协作路径：{goal}", allow_spawn=True, acceptance=["contains: 计划"]),
        SwarmTask("design", "engineer", f"给出最小可行实现方案：{goal}", depends_on=["plan"], input_from={"plan": "plan"}, acceptance=["contains: 实现"]),
        SwarmTask("review", "reviewer", "审查实现方案的风险和回归点。", depends_on=["design"], input_from={"design": "design"}, acceptance=["contains: 风险"]),
        SwarmTask("test", "tester", "设计验证方案和关键测试。", depends_on=["design"], input_from={"design": "design"}, acceptance=["contains: 验证"]),
        SwarmTask("synthesize", "synthesizer", f"输出最终执行方案：{goal}", depends_on=["review", "test"], input_from={"review": "review", "test": "test"}, acceptance=["contains: 最终方案"]),
    ]
    return SwarmPlan(agents, tasks)


def debate_plan(motion: str) -> SwarmPlan:
    agents = [
        SwarmAgentSpec("pro", "Pro advocate", "Argue the strongest case for the motion and publish claims to the blackboard.", max_retries=1),
        SwarmAgentSpec("con", "Con advocate", "Argue the strongest case against the motion and publish objections to the blackboard.", max_retries=1),
        SwarmAgentSpec("judge", "Decision judge", "Compare both sides and produce a balanced final judgment.", max_retries=1),
    ]
    tasks = [
        SwarmTask("pro_case", "pro", f"为命题提出支持观点：{motion}", acceptance=["contains: 论据"]),
        SwarmTask("con_case", "con", f"为命题提出反对观点：{motion}", acceptance=["contains: 论据"]),
        SwarmTask("judgment", "judge", f"基于正反双方，给出最终判断：{motion}", depends_on=["pro_case", "con_case"], input_from={"pro": "pro_case", "con": "con_case"}, acceptance=["contains: 最终判断"]),
    ]
    return SwarmPlan(agents, tasks)


def progressive_plan(goal: str, rounds: int = 2) -> SwarmPlan:
    rounds = max(1, min(4, int(rounds)))
    agents = [
        SwarmAgentSpec("scanner", "Scanner", "Produce a broad first-pass map of the problem.", max_retries=1),
        SwarmAgentSpec("critic", "Critic", "Identify gaps and decide what the next refinement should focus on.", max_retries=1),
        SwarmAgentSpec("refiner", "Refiner", "Deepen the answer using prior findings and critique.", max_retries=1),
        SwarmAgentSpec("synthesizer", "Synthesizer", "Produce the final refined deliverable.", max_retries=1),
    ]
    tasks = [
        SwarmTask("scan", "scanner", f"粗略扫描这个目标，列出关键点和不确定性：{goal}", acceptance=["contains: 关键点"]),
        SwarmTask("critique_1", "critic", "审查初扫结果，指出下一轮应该精化的重点。", depends_on=["scan"], input_from={"scan": "scan"}, acceptance=["contains: 精化"]),
    ]
    previous = "critique_1"
    for idx in range(1, rounds + 1):
        refine_id = f"refine_{idx}"
        critique_id = f"critique_{idx + 1}"
        tasks.append(SwarmTask(refine_id, "refiner", f"第 {idx} 轮精化目标：{goal}", depends_on=[previous], input_from={"previous": previous}, acceptance=["contains: 精化"]))
        previous = refine_id
        if idx < rounds:
            tasks.append(SwarmTask(critique_id, "critic", "审查上一轮精化，并指出下一轮重点。", depends_on=[previous], input_from={"previous": previous}, acceptance=["contains: 精化"]))
            previous = critique_id
    tasks.append(SwarmTask("synthesize", "synthesizer", f"输出渐进式精化后的最终报告：{goal}", depends_on=[previous], input_from={"refined": previous}, acceptance=["contains: 最终报告"]))
    return SwarmPlan(agents, tasks)


def _looks_like_code(text: str) -> bool:
    return any(marker in text for marker in ("code", "bug", "test", "implement", "refactor", "代码", "测试", "实现", "修复"))


def _looks_like_debate(text: str) -> bool:
    return any(marker in text for marker in ("debate", "argue", "should", "是否", "辩论", "利弊", " vs "))


def _learned_note(learned_roles: list[str] | None) -> str:
    if not learned_roles:
        return ""
    return "\nPrior successful swarm roles: " + ", ".join(learned_roles[:3])
