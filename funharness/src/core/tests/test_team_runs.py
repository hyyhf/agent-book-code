from __future__ import annotations

import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path

from funharness.src.core.team import TeamManager
from funharness.src.core.team_runs import TeamRunManager


@dataclass
class Member:
    name: str
    role: str = "generalist"
    status: str = "idle"
    last_active_at: float = 0.0
    worker_id: str = ""
    queue_depth: int = 0
    current_task_id: str = ""
    last_error: str = ""


class TeamRunManagerTests(unittest.TestCase):
    def test_done_member_does_not_finish_run_while_other_member_is_idle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runs = TeamRunManager(Path(tmp))
            run = runs.start("ship it", [Member("writer"), Member("reviewer")])
            task_id = runs.assign_task(run.run_id, "writer", "draft")

            updated = runs.update_agent(run.run_id, "writer", status="done", output="drafted")

            self.assertIsNotNone(updated)
            assert updated is not None
            self.assertEqual(task_id, "TR1")
            self.assertEqual(updated.status, "running")
            self.assertEqual(updated.finished_at, 0.0)

    def test_task_and_message_ids_are_monotonic_after_deletions_and_truncation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runs = TeamRunManager(Path(tmp))
            run = runs.start("ship it", [Member("writer")])
            first_task = runs.assign_task(run.run_id, "writer", "draft")
            runs.remove_agent(run.run_id, "writer")
            runs.add_agent(run.run_id, Member("writer"))
            second_task = runs.assign_task(run.run_id, "writer", "revise")

            self.assertEqual(first_task, "TR1")
            self.assertEqual(second_task, "TR2")

            for idx in range(205):
                runs.record_message(run.run_id, "lead", "writer", f"note {idx}")
            snapshot = runs.get(run.run_id)

            self.assertIsNotNone(snapshot)
            assert snapshot is not None
            message_ids = [item.message_id for item in snapshot.messages or []]
            self.assertEqual(len(message_ids), 200)
            self.assertEqual(len(set(message_ids)), 200)
            self.assertEqual(message_ids[-1], "msg_208")

    def test_finish_run_marks_lead_and_timeline_done(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runs = TeamRunManager(Path(tmp))
            run = runs.start("ship it", [Member("writer")])

            finished = runs.finish_run(run.run_id)

            self.assertIsNotNone(finished)
            assert finished is not None
            self.assertEqual(finished.status, "done")
            lead = next(agent for agent in finished.agents or [] if agent.name == "lead")
            self.assertEqual(lead.status, "done")
            self.assertEqual(lead.progress, 100)


class TeamManagerCreateTests(unittest.TestCase):
    def test_create_same_name_updates_metadata_without_dropping_runtime_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manager = TeamManager(Path(tmp))
            member = manager.create("Writer", "writer", "old instructions")
            member.status = "working"
            member.worker_id = "worker_123"
            member.runtime_id = "agent_123"
            member.current_task_id = "TR1"
            created_at = member.created_at

            updated = manager.create("writer", "editor", "new instructions")

            self.assertEqual(updated.created_at, created_at)
            self.assertEqual(updated.role, "editor")
            self.assertEqual(updated.instructions, "new instructions")
            self.assertEqual(updated.status, "working")
            self.assertEqual(updated.worker_id, "worker_123")
            self.assertEqual(updated.runtime_id, "agent_123")
            self.assertEqual(updated.current_task_id, "TR1")


if __name__ == "__main__":
    unittest.main()
