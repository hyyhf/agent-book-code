from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("OPENAI_API_KEY", "test-key")
os.environ.setdefault("OPENAI_MODEL_NAME", "env-model")

from funGUI.backend.events import EventBus
from funGUI.backend.service import AgentService


class ModelProfileTests(unittest.IsolatedAsyncioTestCase):
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

    async def test_save_masks_key_and_keeps_key_when_blank(self) -> None:
        first = await self.service.save_model_profiles(
            [
                {
                    "id": "deepseek",
                    "name": "DeepSeek",
                    "base_url": "https://api.deepseek.com/v1",
                    "api_key": "sk-original-1234",
                    "model": "deepseek-v4-flash",
                    "enabled": True,
                }
            ],
            "deepseek",
        )
        profile = next(item for item in first["profiles"] if item["id"] == "deepseek")
        self.assertEqual(profile["api_key_masked"], "sk-******1234")
        self.assertNotIn("api_key", profile)

        second = await self.service.save_model_profiles(
            [
                {
                    "id": "deepseek",
                    "name": "DeepSeek Flash",
                    "base_url": "https://api.deepseek.com/v1",
                    "api_key": "",
                    "model": "deepseek-v4-flash",
                    "enabled": True,
                }
            ],
            "deepseek",
        )
        profile = next(item for item in second["profiles"] if item["id"] == "deepseek")
        self.assertEqual(profile["api_key_masked"], "sk-******1234")
        self.assertEqual(profile["name"], "DeepSeek Flash")

    async def test_select_profile_updates_agent_model(self) -> None:
        await self.service.save_model_profiles(
            [
                {
                    "id": "moonshot",
                    "name": "Moonshot",
                    "base_url": "https://api.moonshot.cn/v1",
                    "api_key": "sk-moonshot",
                    "model": "moonshot-v1-8k",
                    "enabled": True,
                }
            ],
            "__env__",
        )

        result = await self.service.set_model_profile("moonshot")
        snapshot = self.service.snapshot()

        self.assertTrue(result["ok"])
        self.assertEqual(result["model"], "moonshot-v1-8k")
        self.assertEqual(self.service.agent.model, "moonshot-v1-8k")
        self.assertEqual(snapshot["model_profile_id"], "moonshot")


if __name__ == "__main__":
    unittest.main()
