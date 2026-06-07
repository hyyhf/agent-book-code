from __future__ import annotations

import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from funharness.src.core.groups import AgentGroup, AgentProfile, GroupAgentRun, GroupAgentSession, GroupMember, GroupMessage, GroupOrchestrator, GroupRuntimePool, GroupStore
from funharness.src.core.groups.mention import GroupMentionRouter
from funharness.src.core.groups.runner import GroupAgentRunner
from funharness.src.core.skills import SkillLoader


class _Runner:
    active = 0
    max_active = 0
    lock = threading.Lock()

    def __init__(self, delay: float = 0.0) -> None:
        self.delay = delay

    def run(self, *, group, member, profile, session, run, trigger, cancel_event):
        with self.lock:
            type(self).active += 1
            type(self).max_active = max(type(self).max_active, type(self).active)
        try:
            if self.delay:
                time.sleep(self.delay)
            return f"{member.display_name}: done"
        finally:
            with self.lock:
                type(self).active -= 1


def wait_until(predicate, timeout: float = 3.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition was not met before timeout")


class GroupCoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.tmp.name)
        self.store = GroupStore(self.workspace / ".funharness" / "groups")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_mention_router_supports_single_multiple_and_all(self) -> None:
        members = [
            GroupMember(id="a", display_name="架构师", active=True),
            GroupMember(id="b", display_name="测试工程师", active=True),
            GroupMember(id="c", display_name="文档助手", active=False),
        ]
        router = GroupMentionRouter()

        self.assertEqual(router.parse("@架构师 看看", members), (["a"], []))
        self.assertEqual(router.parse("@架构师 @测试工程师 看看", members), (["a", "b"], []))
        self.assertEqual(router.parse("@全部 看看", members), (["a", "b"], []))
        self.assertEqual(router.parse("没有 mention", members), ([], []))
        self.assertEqual(router.parse("@不存在 看看", members), ([], ["不存在"]))

    def test_store_persists_group_state(self) -> None:
        profile = self.store.save_profile(AgentProfile(name="架构师", role="architect"))
        group = self.store.save_group(AgentGroup(name="多智能体设计组"))
        member = self.store.save_member(GroupMember(group_id=group.id, profile_id=profile.id, display_name="架构师"))
        session = self.store.get_or_create_session(group.id, member)

        reloaded = GroupStore(self.workspace / ".funharness" / "groups")
        self.assertEqual(reloaded.list_profiles()[0].name, "架构师")
        self.assertEqual(reloaded.list_groups()[0].name, "多智能体设计组")
        self.assertEqual(reloaded.list_members(group.id)[0].id, member.id)
        self.assertEqual(reloaded.get_or_create_session(group.id, member).id, session.id)

    def test_delete_group_removes_group_directory_without_tombstone(self) -> None:
        group = self.store.save_group(AgentGroup(name="临时群"))
        group_dir = self.store.group_dir(group.id)
        (group_dir / "messages.jsonl").write_text("{}", encoding="utf-8")

        self.store.delete_group(group.id)

        self.assertFalse(group_dir.exists())
        self.assertFalse((self.store.root / f"{group.id}.deleted").exists())
        self.assertIsNone(self.store.get_group(group.id))

    def test_orchestrator_creates_runs_for_mentions(self) -> None:
        profile = self.store.save_profile(AgentProfile(name="架构师"))
        group = self.store.save_group(AgentGroup(name="设计组"))
        member = self.store.save_member(GroupMember(group_id=group.id, profile_id=profile.id, display_name="架构师"))
        pool = GroupRuntimePool(
            store=self.store,
            runner_factory=lambda **_: _Runner(),
            max_workers=2,
        )
        orchestrator = GroupOrchestrator(store=self.store, runtime_pool=pool)

        result = orchestrator.handle_user_message(group.id, "@架构师 做评估")

        self.assertEqual(result["target_member_ids"], [member.id])
        self.assertEqual(len(result["runs"]), 1)
        wait_until(lambda: any(run.status == "done" for run in self.store.list_runs(group.id)))
        pool.shutdown()
        self.assertTrue(any(message.sender_type == "agent" for message in self.store.list_messages(group.id)))

    def test_runtime_pool_serializes_same_member_and_parallelizes_different_members(self) -> None:
        _Runner.active = 0
        _Runner.max_active = 0
        profiles = [self.store.save_profile(AgentProfile(name=f"agent{i}")) for i in range(2)]
        group = self.store.save_group(AgentGroup(name="设计组"))
        members = [
            self.store.save_member(GroupMember(group_id=group.id, profile_id=profiles[0].id, display_name="架构师")),
            self.store.save_member(GroupMember(group_id=group.id, profile_id=profiles[1].id, display_name="测试工程师")),
        ]
        pool = GroupRuntimePool(store=self.store, runner_factory=lambda **_: _Runner(delay=0.15), max_workers=2)
        orchestrator = GroupOrchestrator(store=self.store, runtime_pool=pool)

        orchestrator.handle_user_message(group.id, "@架构师 A")
        orchestrator.handle_user_message(group.id, "@架构师 B")
        orchestrator.handle_user_message(group.id, "@测试工程师 C")

        wait_until(lambda: all(run.status == "done" for run in self.store.list_runs(group.id)), timeout=5)
        pool.shutdown()
        self.assertGreaterEqual(_Runner.max_active, 2)
        first_member_runs = [run for run in self.store.list_runs(group.id) if run.member_id == members[0].id]
        self.assertEqual(len(first_member_runs), 2)
        self.assertLess(first_member_runs[1].started_at, first_member_runs[0].started_at)

    def test_group_runner_uses_group_scoped_workspace_tools(self) -> None:
        runner = GroupAgentRunner(store=self.store, workspace=self.workspace)
        group = self.store.save_group(AgentGroup(name="设计组"))
        member = self.store.save_member(GroupMember(group_id=group.id, profile_id="p", display_name="搜索员"))
        profile = AgentProfile(id="p", name="搜索员")
        run = self.store.save_run(GroupAgentRun(
            group_id=group.id,
            member_id=member.id,
            profile_id=profile.id,
        ))

        tools = runner._tool_registry(group, member, profile, run).list_tools()

        self.assertIn("tool_web_search", tools)
        self.assertIn("tool_web_fetch", tools)
        self.assertIn("tool_web_crawl", tools)
        self.assertIn("tool_load_skill", tools)
        self.assertIn("group_list_workspace", tools)
        self.assertIn("group_read_workspace", tools)
        self.assertIn("group_search_workspace", tools)
        self.assertIn("group_write_artifact", tools)
        self.assertIn("tool_grep_search", tools)
        self.assertIn("tool_replace_in_file", tools)
        self.assertIn("tool_run_command", tools)
        self.assertNotIn("tool_read_file", tools)
        self.assertNotIn("tool_write_file", tools)

    def test_group_runner_loads_only_enabled_skills(self) -> None:
        skill_dir = self.workspace / ".funharness" / "skills" / "named"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            """---
name: named-skill
description: Loaded in group chat.
---

# Group Skill Body
""",
            encoding="utf-8",
        )
        runner = GroupAgentRunner(
            store=self.store,
            workspace=self.workspace,
            enabled_skill_names=["named-skill"],
            skill_loader=SkillLoader(self.workspace),
        )
        group = self.store.save_group(AgentGroup(name="scope"))
        member = self.store.save_member(GroupMember(group_id=group.id, profile_id="p", display_name="writer"))
        profile = AgentProfile(id="p", name="writer", enabled_skills=["named-skill"])
        run = self.store.save_run(GroupAgentRun(group_id=group.id, member_id=member.id, profile_id=profile.id))
        registry = runner._tool_registry(group, member, profile, run)

        loaded = registry.get_function("tool_load_skill")("named-skill")
        denied = registry.get_function("tool_load_skill")("other-skill")

        self.assertIn("Path:", loaded)
        self.assertIn("# Group Skill Body", loaded)
        self.assertIn("Skill not enabled", denied)

    def test_group_write_artifact_writes_inside_group_artifacts(self) -> None:
        runner = GroupAgentRunner(store=self.store, workspace=self.workspace)
        group = self.store.save_group(AgentGroup(name="设计组"))
        member = self.store.save_member(GroupMember(group_id=group.id, profile_id="p", display_name="写手"))
        profile = AgentProfile(id="p", name="写手")
        run = self.store.save_run(GroupAgentRun(
            group_id=group.id,
            member_id=member.id,
            profile_id=profile.id,
        ))
        registry = runner._tool_registry(group, member, profile, run)

        result = registry.get_function("group_write_artifact")("report.md", "报告", "hello")
        target = self.workspace / ".funharness" / "groups" / group.id / "artifacts" / member.id / "report.md"

        self.assertIn("Saved artifact", result)
        self.assertEqual(target.read_text(encoding="utf-8"), "hello")
        self.assertFalse((self.workspace / "report.md").exists())
        self.assertEqual(len(self.store.list_artifacts(group.id)), 1)

    def test_group_workspace_tools_are_limited_to_group_directory(self) -> None:
        runner = GroupAgentRunner(store=self.store, workspace=self.workspace)
        group = self.store.save_group(AgentGroup(name="scope"))
        member = self.store.save_member(GroupMember(group_id=group.id, profile_id="p", display_name="searcher"))
        profile = AgentProfile(id="p", name="searcher")
        run = self.store.save_run(GroupAgentRun(group_id=group.id, member_id=member.id, profile_id=profile.id))
        registry = runner._tool_registry(group, member, profile, run)
        group_note = self.store.group_dir(group.id) / "notes.md"
        group_note.write_text("inside-only-needle", encoding="utf-8")
        outside = self.workspace / "outside.md"
        outside.write_text("outside-only-needle", encoding="utf-8")

        listing = runner._call_tool(registry, "group_list_workspace", {"path": "."})
        inside_search = runner._call_tool(registry, "group_search_workspace", {"pattern": "inside-only-needle"})
        outside_search = runner._call_tool(registry, "group_search_workspace", {"pattern": "outside-only-needle"})
        grep_outside = runner._call_tool(registry, "tool_grep_search", {"pattern": "outside-only-needle", "path": "."})
        read_outside = runner._call_tool(registry, "group_read_workspace", {"path": str(outside)})

        self.assertIn("notes.md", listing)
        self.assertNotIn("outside.md", listing)
        self.assertIn("notes.md", inside_search)
        self.assertEqual(outside_search, "(no matches)")
        self.assertIn("No matches", grep_outside)
        self.assertIn("Path escapes group chat folder", read_outside)

    def test_group_session_compaction_drops_tool_call_only_assistant_messages(self) -> None:
        compacted = GroupAgentRunner._compact_messages([
            {"role": "user", "content": "first"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "group_list_workspace", "arguments": "{}"},
                    },
                ],
            },
            {"role": "tool", "tool_call_id": "call_1", "content": "notes.md"},
            {"role": "assistant", "content": "done"},
        ])

        self.assertEqual(compacted, [
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": "done"},
        ])

    def test_group_tool_call_event_keeps_running_snapshot(self) -> None:
        events = []
        runner = GroupAgentRunner(store=self.store, workspace=self.workspace, event_sink=lambda event_type, payload: events.append((event_type, payload)))
        tool_record = {
            "name": "group_write_artifact",
            "arguments": {"filename": "report.md", "tags": []},
            "result": "",
            "timestamp": time.time(),
            "status": "running",
        }

        runner._emit("group_tool_call", {"tool_call": GroupAgentRunner._tool_record_snapshot(tool_record)})
        tool_record["status"] = "done"
        tool_record["arguments"]["tags"].append("mutated")

        self.assertEqual(events[0][0], "group_tool_call")
        self.assertEqual(events[0][1]["tool_call"]["status"], "running")
        self.assertEqual(events[0][1]["tool_call"]["arguments"]["tags"], [])

    def test_group_runner_emits_tool_argument_generation_events(self) -> None:
        events = []
        runner = GroupAgentRunner(store=self.store, workspace=self.workspace, event_sink=lambda event_type, payload: events.append((event_type, payload)))
        group = self.store.save_group(AgentGroup(name="scope"))
        member = self.store.save_member(GroupMember(group_id=group.id, profile_id="p", display_name="writer"))
        profile = AgentProfile(id="p", name="writer")
        session = GroupAgentSession(group_id=group.id, member_id=member.id, profile_id=profile.id)
        run = self.store.save_run(GroupAgentRun(group_id=group.id, member_id=member.id, profile_id=profile.id))
        trigger = GroupMessage(group_id=group.id, content="@writer write")

        def fake_process_stream_response(_stream, on_token=None, on_tool_gen=None, **_kwargs):
            on_tool_gen(0, "group_write_artifact", '{"filename":"report.md",')
            on_tool_gen(0, "group_write_artifact", '"content":"hello"}')
            return {"role": "assistant", "content": "done"}

        with patch("funharness.src.core.groups.runner.call_with_retry", return_value=[]), \
             patch("funharness.src.core.groups.runner.process_stream_response", fake_process_stream_response):
            output = runner.run(
                group=group,
                member=member,
                profile=profile,
                session=session,
                run=run,
                trigger=trigger,
                cancel_event=threading.Event(),
            )

        generation_events = [payload for event_type, payload in events if event_type == "group_tool_gen_delta"]
        self.assertEqual(output, "done")
        self.assertEqual(len(generation_events), 2)
        self.assertEqual(generation_events[-1]["tool_call"]["status"], "generating")
        self.assertEqual(generation_events[-1]["tool_call"]["name"], "group_write_artifact")
        self.assertIn('"content":"hello"}', generation_events[-1]["tool_call"]["arguments"])

    def test_group_replace_in_file_is_limited_to_group_folder(self) -> None:
        runner = GroupAgentRunner(store=self.store, workspace=self.workspace)
        group = self.store.save_group(AgentGroup(name="设计组"))
        member = self.store.save_member(GroupMember(group_id=group.id, profile_id="p", display_name="写手"))
        profile = AgentProfile(id="p", name="写手")
        run = self.store.save_run(GroupAgentRun(group_id=group.id, member_id=member.id, profile_id=profile.id))
        registry = runner._tool_registry(group, member, profile, run)
        registry.get_function("group_write_artifact")("report.md", "报告", "hello")

        result = registry.get_function("tool_replace_in_file")(
            f".funharness/groups/{group.id}/artifacts/{member.id}/report.md",
            "hello",
            "updated",
        )
        outside = runner._call_tool(
            registry,
            "tool_replace_in_file",
            {"path": str(self.workspace / "outside.md"), "old_text": "x", "new_text": "y"},
        )

        self.assertIn("Replaced", result)
        target = self.workspace / ".funharness" / "groups" / group.id / "artifacts" / member.id / "report.md"
        self.assertEqual(target.read_text(encoding="utf-8"), "updated")
        self.assertIn("Path escapes group chat folder", outside)


if __name__ == "__main__":
    unittest.main()
