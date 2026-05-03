from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from io import BytesIO
from pathlib import Path

os.environ.setdefault("OPENAI_API_KEY", "test-key")

from fastapi import UploadFile

from funGUI.backend.events import EventBus
from funGUI.backend.service import AgentService


class AttachmentApiTests(unittest.IsolatedAsyncioTestCase):
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

    async def test_upload_lists_and_detaches_attachment(self) -> None:
        upload = UploadFile(filename="notes.txt", file=BytesIO(b"alpha\nbeta\n"))

        result = await self.service.upload_attachments([upload])
        attachments = self.service.attachments()
        attachment_id = attachments[0]["id"]
        stored = Path(attachments[0]["stored_path"])
        detach = await self.service.detach_attachment(attachment_id)

        self.assertTrue(result["ok"])
        self.assertEqual(len(attachments), 1)
        self.assertEqual(attachments[0]["original_name"], "notes.txt")
        self.assertTrue(stored.exists())
        self.assertEqual(detach["attachments"], [])
        self.assertTrue(stored.exists())

    async def test_upload_multiple_files_gets_unique_ids(self) -> None:
        files = [
            UploadFile(filename="a.txt", file=BytesIO(b"one")),
            UploadFile(filename="b.txt", file=BytesIO(b"two")),
        ]

        await self.service.upload_attachments(files)
        attachments = self.service.attachments()
        ids = {item["id"] for item in attachments}

        self.assertEqual(len(attachments), 2)
        self.assertEqual(len(ids), 2)

    async def test_new_and_loaded_sessions_have_separate_attachments(self) -> None:
        await self.service.upload_attachments([
            UploadFile(filename="session-a.txt", file=BytesIO(b"one")),
        ])
        first_session_id = self.service.agent.current_session.id
        self.service.agent._save_session()

        await self.service.new_session()
        self.assertEqual(self.service.attachments(), [])

        await self.service.upload_attachments([
            UploadFile(filename="session-b.txt", file=BytesIO(b"two")),
        ])
        self.assertEqual(len(self.service.attachments()), 1)

        loaded = await self.service.load_session(first_session_id)

        self.assertEqual(loaded["session_id"], first_session_id)
        self.assertEqual(len(loaded["attachments"]), 1)
        self.assertEqual(loaded["attachments"][0]["original_name"], "session-a.txt")


if __name__ == "__main__":
    unittest.main()
