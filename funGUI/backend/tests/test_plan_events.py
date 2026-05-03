from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("OPENAI_API_KEY", "test-key")

from funGUI.backend.events import EventBus
from funGUI.backend.service import AgentService
from funharness.src.core.tasks import Task, TaskList


class PlanEventTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._old_cwd = Path.cwd()
        self._tmp = tempfile.TemporaryDirectory()
        self.workspace = Path(self._tmp.name)
        os.chdir(self.workspace)
        self.bus = EventBus()
        self.queue = await self.bus.connect("plan-test")
        await self.queue.get()
        self.service = AgentService(
            bus=self.bus,
            workspace=self.workspace,
            loop=asyncio.get_running_loop(),
        )

    async def asyncTearDown(self) -> None:
        self.service.agent.scheduler.stop()
        await self.bus.disconnect("plan-test")
        os.chdir(self._old_cwd)
        self._tmp.cleanup()

    async def test_plan_token_publishes_plan_delta(self) -> None:
        await asyncio.to_thread(self.service._on_plan_token, "[")

        event = await asyncio.wait_for(self.queue.get(), timeout=1)
        self.assertEqual(event.type, "plan_delta")
        self.assertEqual(event.payload["token"], "[")

    async def test_task_tool_result_publishes_tasks_updated(self) -> None:
        task_list = TaskList(project_name="demo")
        task_list.add(Task("T1", "Sketch the flow"))
        self.service.agent.task_list = task_list

        await asyncio.to_thread(self.service._on_tool_result, "tool_task_update", "ok", "")

        tool_event = await asyncio.wait_for(self.queue.get(), timeout=1)
        tasks_event = await asyncio.wait_for(self.queue.get(), timeout=1)
        self.assertEqual(tool_event.type, "tool_result")
        self.assertEqual(tasks_event.type, "tasks_updated")
        self.assertEqual(tasks_event.payload["tasks"][0]["task_id"], "T1")
        self.assertEqual(tasks_event.payload["summary"], task_list.summary())

    async def test_complete_task_tool_publishes_completion_card_event(self) -> None:
        task_list = TaskList(project_name="demo")
        task_list.add(Task("T1", "Sketch the flow"))
        task_list.update("T1", status="done", artifacts=["demo.py"])
        self.service.agent.task_list = task_list

        await asyncio.to_thread(self.service._on_tool_result, "tool_complete_task", "Task T1 done. Progress: 1/1 (100%).", "")

        await asyncio.wait_for(self.queue.get(), timeout=1)
        await asyncio.wait_for(self.queue.get(), timeout=1)
        completed = await asyncio.wait_for(self.queue.get(), timeout=1)
        self.assertEqual(completed.type, "task_completed")
        self.assertEqual(completed.payload["task"]["task_id"], "T1")
        self.assertEqual(completed.payload["progress"]["done"], 1)
        self.assertEqual(completed.payload["progress"]["percent"], 100)


if __name__ == "__main__":
    unittest.main()
