"""Context construction for group agent runs."""
from __future__ import annotations

from pathlib import Path

from .models import AgentGroup, AgentProfile, GroupAgentRun, GroupAgentSession, GroupMember, GroupMessage
from .store import GroupStore


class GroupContextBuilder:
    def __init__(self, store: GroupStore, workspace: str | Path, skills_summary: str = "") -> None:
        self.store = store
        self.workspace = Path(workspace)
        self.skills_summary = skills_summary

    def build(
        self,
        *,
        group: AgentGroup,
        member: GroupMember,
        profile: AgentProfile,
        session: GroupAgentSession,
        run: GroupAgentRun,
        trigger: GroupMessage,
    ) -> str:
        recent = self.store.list_messages(group.id, limit=24)
        public_log = "\n".join(self._format_message(item) for item in recent[-16:])
        group_workspace = self.store.group_dir(group.id).relative_to(self.workspace)
        scratch = self.store.group_dir(group.id).relative_to(self.workspace / ".funharness" / "groups")
        return "\n\n".join(
            part
            for part in [
                "You are participating in a multi-agent group chat. Answer only as yourself.",
                f"Group: {group.name}\nDescription: {group.description or '(none)'}",
                f"Your display name: {member.display_name}\nYour role: {profile.role}\nRole instructions:\n{profile.instructions or '(none)'}",
                f"Private session summary:\n{session.private_context_summary or '(no private summary yet)'}",
                f"Recent public group messages:\n{public_log or '(no previous messages)'}",
                f"Group workspace root: {group_workspace.as_posix()}\nGroup scratch namespace: .funharness/groups/{scratch.as_posix()}/scratch/{member.id}",
                "Workspace rule: group_list_workspace, group_read_workspace, group_search_workspace, and tool_grep_search can only inspect files inside this group chat folder. Use group_write_artifact for deliverables. tool_replace_in_file can only modify files inside this group chat folder. tool_run_command runs from your group scratch namespace.",
                f"Enabled skills summary:\n{self.skills_summary or '(no skills selected)'}",
                f"User message that mentioned you:\n{trigger.content}",
                f"Run id: {run.id}",
            ]
            if part
        )

    @staticmethod
    def _format_message(message: GroupMessage) -> str:
        sender = message.sender_name or message.sender_id or message.sender_type
        return f"[{sender}] {message.content}"
