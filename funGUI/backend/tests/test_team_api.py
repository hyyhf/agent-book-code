from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("OPENAI_API_KEY", "test-key")

from funGUI.backend.events import EventBus
from funGUI.backend.service import AgentService


class TeamApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._old_cwd = Path.cwd()
        self._tmp = tempfile.TemporaryDirectory()
        self.workspace = Path(self._tmp.name)
        os.chdir(self.workspace)
        self.service = AgentService(
            bus=EventBus(),
            workspace=self.workspace,
            loop=asyncio.get_running_loop(),
        )

    async def asyncTearDown(self) -> None:
        self.service.agent.scheduler.stop()
        os.chdir(self._old_cwd)
        self._tmp.cleanup()

    async def test_team_empty_state(self) -> None:
        result = self.service.team()

        self.assertEqual(result["members"], [])
        self.assertEqual(result["summary"], "(no teammates)")

    async def test_team_lists_member_and_inbox(self) -> None:
        self.service.agent.team.create("teacher", "teaching-assistant", "Focus on beginner clarity.")
        self.service.agent.team.send("lead", "teacher", "Review the current task plan.")

        result = self.service.team()
        inbox = self.service.team_inbox("teacher")

        self.assertEqual(len(result["members"]), 1)
        self.assertEqual(result["members"][0]["name"], "teacher")
        self.assertEqual(result["members"][0]["role"], "teaching-assistant")
        self.assertEqual(result["members"][0]["inbox_count"], 2)
        self.assertEqual(inbox["name"], "teacher")
        self.assertEqual(len(inbox["items"]), 2)


if __name__ == "__main__":
    unittest.main()
