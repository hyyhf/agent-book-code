"""Optional grounding providers for swarm workers."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from .models import SwarmTask


class GroundingProvider(Protocol):
    def ground(self, task: SwarmTask, variables: dict[str, str]) -> str:
        """Return task-specific grounding text, or an empty string."""


@dataclass
class StaticGroundingProvider:
    """Simple provider useful for tests, demos, and pre-fetched context."""

    entries: dict[str, str] = field(default_factory=dict)

    def ground(self, task: SwarmTask, variables: dict[str, str]) -> str:
        parts = []
        if "*" in self.entries:
            parts.append(self.entries["*"])
        if task.id in self.entries:
            parts.append(self.entries[task.id])
        return "\n\n".join(part for part in parts if part.strip())
