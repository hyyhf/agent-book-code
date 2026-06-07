"""Group message orchestration."""
from __future__ import annotations

from typing import Any, Callable

from .mention import GroupMentionRouter
from .models import GroupAgentRun, GroupMember, GroupMessage
from .runtime_pool import GroupRuntimePool
from .store import GroupStore


class GroupOrchestrator:
    def __init__(
        self,
        *,
        store: GroupStore,
        runtime_pool: GroupRuntimePool,
        event_sink: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> None:
        self.store = store
        self.runtime_pool = runtime_pool
        self.event_sink = event_sink
        self.mentions = GroupMentionRouter()

    def handle_user_message(self, group_id: str, content: str) -> dict[str, Any]:
        group = self.store.get_group(group_id)
        if group is None:
            raise KeyError(group_id)
        text = (content or "").strip()
        if not text:
            raise ValueError("Message is required")
        members = self.store.list_members(group_id)
        target_ids, unknown = self.mentions.parse(text, members)
        message = GroupMessage(
            group_id=group_id,
            sender_type="user",
            sender_id="user",
            sender_name="你",
            content=text,
            mentions=target_ids,
        )
        self.store.append_message(message)
        self._emit("group_message_created", message.to_dict())

        runs: list[dict[str, Any]] = []
        for member in self._target_members(members, target_ids):
            session = self.store.get_or_create_session(group_id, member)
            run = GroupAgentRun(
                session_id=session.id,
                group_id=group_id,
                member_id=member.id,
                profile_id=member.profile_id,
                trigger_message_id=message.id,
                input_text=text,
            )
            self.store.save_run(run)
            runs.append(run.to_dict())
            self.runtime_pool.submit(run)
        payload = {
            "message": message.to_dict(),
            "target_member_ids": target_ids,
            "unknown_mentions": unknown,
            "runs": runs,
        }
        self._emit("group_snapshot_updated", self.store.snapshot(group_id))
        return payload

    @staticmethod
    def _target_members(members: list[GroupMember], target_ids: list[str]) -> list[GroupMember]:
        target_set = set(target_ids)
        return [member for member in members if member.active and member.id in target_set]

    def _emit(self, event_type: str, payload: dict[str, Any]) -> None:
        if self.event_sink:
            self.event_sink(event_type, payload)
