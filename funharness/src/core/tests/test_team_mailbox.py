from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from funharness.src.core.team_mailbox import TeamMailbox, TeamMailboxEnvelope


class TeamMailboxTests(unittest.TestCase):
    def test_writes_reads_and_ignores_bad_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            mailbox = TeamMailbox(Path(tmp))
            mailbox.send_message("lead", "writer", "hello")
            mailbox.write_task("lead", "writer", "draft", context="ctx", task_id="TR1", run_id="run1", runtime_id="agent_1")
            mailbox.write(TeamMailboxEnvelope("cancel", "lead", "writer", "stop", reason="not needed"))
            mailbox.write(TeamMailboxEnvelope("shutdown", "lead", "writer", "bye", reason="done"))

            path = mailbox.inbox_path("writer")
            path.write_text(path.read_text(encoding="utf-8") + "{bad json\n", encoding="utf-8")

            items = mailbox.peek("writer")

            self.assertEqual([item["type"] for item in items], ["message", "task", "cancel", "shutdown"])
            self.assertEqual(items[1]["task_id"], "TR1")
            self.assertEqual(items[1]["runtime_id"], "agent_1")
            self.assertEqual(len(mailbox.drain("writer")), 4)
            self.assertEqual(mailbox.peek("writer"), [])


if __name__ == "__main__":
    unittest.main()
