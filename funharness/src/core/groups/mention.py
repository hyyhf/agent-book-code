"""Mention parsing for group messages."""
from __future__ import annotations

import re

from .models import GroupMember

ALL_ALIASES = {"全部", "all", "所有人", "everyone"}
MENTION_RE = re.compile(r"@([^\s@,，:：;；]+)")


class GroupMentionRouter:
    def parse(self, text: str, members: list[GroupMember]) -> tuple[list[str], list[str]]:
        tokens = [match.group(1).strip() for match in MENTION_RE.finditer(text or "")]
        if not tokens:
            return [], []
        active = [member for member in members if member.active]
        unknown: list[str] = []
        targets: list[str] = []
        lower_to_member = {member.display_name.lower(): member for member in active}
        id_to_member = {member.id.lower(): member for member in active}

        if any(token.lower() in ALL_ALIASES for token in tokens):
            return [member.id for member in active], []

        for token in tokens:
            key = token.lower()
            member = lower_to_member.get(key) or id_to_member.get(key)
            if member is None:
                unknown.append(token)
                continue
            if member.id not in targets:
                targets.append(member.id)
        return targets, unknown
