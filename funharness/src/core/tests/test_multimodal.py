from __future__ import annotations

import base64
from copy import deepcopy
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PIL import Image

from funharness.src.agent import FunHarnessAgent
from funharness.src.core.attachments import AttachmentManager, DEFAULT_ATTACHMENT_MAX_CHARS
from funharness.src.core.content import ToolResult, content_text, estimate_content_tokens, tool_content
from funharness.src.core.context import compact_conversation, should_compact, truncate_tool_results
from funharness.src.core.groups.models import AgentGroup, AgentProfile, GroupAgentRun, GroupAgentSession, GroupMember, GroupMessage
from funharness.src.core.groups.runner import GroupAgentRunner
from funharness.src.core.groups.store import GroupStore
from funharness.src.core.llm import sanitize_messages_for_api, validate_request_size
from funharness.src.core.permissions import PathPolicy, PermissionManager, PermissionMode, classify_risk
from funharness.src.core.session import Session, SessionManager
from funharness.src.core.subagent import SubAgent
from funharness.src.core.tools import registry, tool_read_file


def call(name="tool_read_file", arguments=None, call_id="vision_1"):
    return {"id": call_id, "type": "function", "function": {
        "name": name, "arguments": json.dumps(arguments or {"path": "sample.png"}),
    }}


class ScriptedClient:
    """Exercises the real API adapter and streaming assembly without a network."""

    def __init__(self, messages):
        self.responses = iter(messages)
        self.requests = []
        self.chat = SimpleNamespace(completions=self)

    def create(self, **kwargs):
        self.requests.append(deepcopy(kwargs))
        msg = next(self.responses)
        calls = [SimpleNamespace(
            index=i, id=tc["id"], type="function", function=SimpleNamespace(**tc["function"]),
        ) for i, tc in enumerate(msg.get("tool_calls", []))]
        response = SimpleNamespace(content=msg.get("content"), tool_calls=calls,
                                   reasoning_content=msg.get("reasoning_content"))
        if kwargs.get("stream"):
            return iter([SimpleNamespace(choices=[SimpleNamespace(delta=response)])])
        return SimpleNamespace(choices=[SimpleNamespace(message=response)])


class MultimodalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.old_cwd = Path.cwd()
        os.chdir(self.root)
        self.path = self.root / "sample.png"
        Image.new("RGB", (320, 180), "blue").save(self.path)
        self.result = tool_read_file(str(self.path))
        self.blocks = self.result.content

    def tearDown(self):
        os.chdir(self.old_cwd)
        self.tmp.cleanup()

    def test_supported_formats_detect_actual_bytes_and_snapshot(self):
        for format, mime in (("PNG", "image/png"), ("JPEG", "image/jpeg"), ("GIF", "image/gif"), ("WEBP", "image/webp")):
            with self.subTest(format=format):
                path = self.root / "misnamed.dat"
                Image.new("RGB", (64, 48), "red").save(path, format=format)
                result = tool_read_file(str(path))
                self.assertIsInstance(result, ToolResult)
                image = result.content[1]
                self.assertEqual(image["mime_type"], mime)
                self.assertEqual(base64.b64decode(image["data"]), path.read_bytes())
                path.unlink()
                with Image.open(io.BytesIO(base64.b64decode(image["data"]))) as snapshot:
                    self.assertEqual(snapshot.size, (64, 48))

    def test_invalid_images_details_and_resource_limits_return_errors(self):
        broken = self.root / "broken.png"
        broken.write_bytes(b"not an image")
        self.assertIn("failed", tool_read_file(str(broken)))
        self.assertRegex(tool_read_file(str(self.root)), r"Error:|Read failed:")
        self.assertIn("not found", tool_read_file("missing.png"))
        self.assertIn("detail must", tool_read_file(str(self.path), detail="invalid"))
        with patch("funharness.src.core.media.MAX_IMAGE_BYTES", 4):
            self.assertIn("exceeds", tool_read_file(str(self.path)))
        with patch("funharness.src.core.media.MAX_IMAGE_PIXELS", 4):
            self.assertIn("pixels", tool_read_file(str(self.path)))
        jpeg = self.root / "truncated.jpg"
        Image.new("RGB", (100, 100)).save(jpeg)
        jpeg.write_bytes(jpeg.read_bytes()[:-30])
        self.assertIn("failed", tool_read_file(str(jpeg)))

    def test_unified_file_reader_exposes_detail_without_a_separate_image_tool(self):
        self.assertNotIn("tool_view_image", registry)
        params = registry.get_schema("tool_read_file")["function"]["parameters"]
        self.assertEqual(params["required"], ["path"])
        self.assertEqual(params["properties"]["detail"]["default"], "auto")
        for detail in ("auto", "low", "high", "original"):
            with self.subTest(detail=detail):
                result = tool_read_file(str(self.path), max_chars=1, detail=detail)
                self.assertEqual(result.content[1]["detail"], detail)
                self.assertEqual(result.content[1]["data"], self.blocks[1]["data"])
                wire = sanitize_messages_for_api([{"role": "user", "content": result.content}])
                self.assertEqual(wire[0]["content"][1]["image_url"]["detail"], "high" if detail == "original" else detail)

        text = self.root / "notes.txt"
        text.write_text("plain text", encoding="utf-8")
        self.assertEqual(tool_read_file(str(text), 300000), tool_read_file(str(text), detail="high"))

    def test_default_text_read_is_300000_and_can_be_overridden(self):
        path = self.root / "long.txt"
        path.write_text("x" * 310000, encoding="utf-8")
        self.assertEqual(DEFAULT_ATTACHMENT_MAX_CHARS, 300000)
        result = tool_read_file(str(path))
        self.assertIn("x" * 299000, result)
        self.assertIn("truncated", result)
        self.assertNotIn("truncated", tool_read_file(str(path), max_chars=320000))
        self.assertIn("positive integer", tool_read_file(str(path), max_chars=-1))
        self.assertEqual(registry.get_schema("tool_read_file")["function"]["parameters"]["properties"]["max_chars"]["default"], 300000)

    def test_attachment_preview_is_text_but_read_is_visual(self):
        manager = AttachmentManager("session", root=self.root / "uploads")
        record = manager.add(self.path)
        self.assertEqual(record.parse_status, "ok")
        self.assertEqual(record.mime_type, "image/png")
        self.assertNotIn(self.blocks[1]["data"], record.preview)
        self.path.unlink()
        restored = AttachmentManager("session", root=self.root / "uploads")
        result = restored.read(record.id)
        self.assertEqual(result.content[1]["data"], self.blocks[1]["data"])
        self.assertEqual(restored.read(record.id, detail="high").content[1]["detail"], "high")
        restored.records[0].stored_path = str(self.root / "outside.png")
        self.assertIn("outside", restored.read(record.id))

    def test_adapter_groups_multiple_results_before_visual_observations(self):
        messages = [
            {"role": "assistant", "content": None, "reasoning_content": "reasoning", "tool_calls": [call(), call(call_id="text_2")]},
            {"role": "tool", "tool_call_id": "vision_1", "content": self.blocks},
            {"role": "tool", "tool_call_id": "text_2", "content": "text result"},
            {"role": "assistant", "content": "I saw it"},
        ]
        original = deepcopy(messages)
        adapted = sanitize_messages_for_api(messages)
        self.assertEqual([m["role"] for m in adapted], ["assistant", "tool", "tool", "user", "assistant"])
        self.assertEqual(adapted[0]["reasoning_content"], "reasoning")
        self.assertIn("vision_1", adapted[3]["content"][0]["text"])
        self.assertEqual(adapted[3]["content"][-1]["image_url"]["url"], "data:image/png;base64," + self.blocks[1]["data"])
        self.assertEqual(messages, original)
        self.assertNotIn(self.blocks[1]["data"], adapted[1]["content"])

    def test_missing_parallel_result_repaired_before_image(self):
        messages = [
            {"role": "assistant", "content": None, "tool_calls": [call(), call(call_id="missing")]},
            {"role": "tool", "tool_call_id": "vision_1", "content": self.blocks},
            {"role": "user", "content": "continue"},
        ]
        result = sanitize_messages_for_api(messages)
        self.assertEqual(result[2]["tool_call_id"], "missing")
        self.assertEqual(result[3]["role"], "user")
        self.assertEqual(result[3]["content"][-1]["type"], "image_url")
        self.assertEqual(result[4]["content"], "continue")

    def test_user_images_and_unsupported_modalities(self):
        result = sanitize_messages_for_api([{"role": "user", "content": self.blocks}])
        self.assertEqual(result[0]["content"][-1]["type"], "image_url")
        for role in ("system", "assistant"):
            with self.assertRaisesRegex(ValueError, "Images are not supported"):
                sanitize_messages_for_api([{"role": role, "content": self.blocks}])
        for block in ({"type": "audio", "data": "private"}, {**self.blocks[1], "data": "%%%"}):
            with self.assertRaises(ValueError):
                sanitize_messages_for_api([{"role": "user", "content": [block]}])
        with self.assertRaisesRegex(ValueError, "48 MiB"):
            validate_request_size({"messages": "x" * (48 * 1024 * 1024)})

    def test_text_projection_and_token_estimation_never_use_base64_length(self):
        larger_data = deepcopy(self.blocks)
        larger_data[1]["data"] *= 10000
        self.assertEqual(estimate_content_tokens(self.blocks), estimate_content_tokens(larger_data))
        self.assertFalse(should_compact([{"role": "tool", "content": larger_data}]))
        self.assertNotIn(self.blocks[1]["data"], str(self.result))
        self.assertEqual(tool_content(["a.txt", "b.txt"]), "['a.txt', 'b.txt']")

    def test_old_media_eviction_is_explicit_and_recent_bytes_intact(self):
        messages = [{"role": "tool", "content": deepcopy(self.blocks)} for _ in range(9)]
        messages[0]["content"].insert(0, {"type": "text", "text": "z" * 90000})
        result = truncate_tool_results(messages)
        self.assertIn("truncated", result[0]["content"][0]["text"])
        self.assertIn("Image omitted", content_text(result[0]["content"]))
        self.assertEqual(result[-1]["content"][-1], self.blocks[-1])
        with patch("funharness.src.core.context.MAX_IMAGE_BYTES", len(base64.b64decode(self.blocks[1]["data"]))):
            result = truncate_tool_results(deepcopy(messages[-2:]))
        self.assertIn("Image omitted", content_text(result[0]["content"]))
        self.assertEqual(result[-1]["content"][-1]["type"], "image")

    def test_compaction_does_not_send_base64_as_text_or_split_recent_group(self):
        messages = [
            {"role": "system", "content": "system"},
            {"role": "user", "content": self.blocks},
            {"role": "assistant", "content": "earlier visual finding"},
            {"role": "user", "content": "continue"},
            {"role": "assistant", "content": None, "tool_calls": [call(), call(call_id="second")]},
            {"role": "tool", "tool_call_id": "vision_1", "content": self.blocks},
            {"role": "tool", "tool_call_id": "second", "content": "ok"},
            {"role": "assistant", "content": "done"},
            {"role": "user", "content": "again"},
        ]
        client = ScriptedClient([{"content": "summary"}])
        result = compact_conversation(messages, llm_client=client)
        self.assertNotIn(self.blocks[1]["data"], json.dumps(client.requests))
        self.assertEqual(result[2:], messages[4:])
        self.assertEqual(result[3]["content"][1]["data"], self.blocks[1]["data"])
        for failing in (ScriptedClient([]), ScriptedClient([{"content": ""}])):
            fallback = compact_conversation(messages, llm_client=failing)
            self.assertEqual(fallback[2:], messages[4:])
            self.assertIn("earlier visual finding", fallback[1]["content"])
            self.assertNotIn(self.blocks[1]["data"], fallback[1]["content"])

    def test_large_fresh_tool_batch_is_not_evicted_before_first_inference(self):
        messages = [
            {"role": "assistant", "content": None, "tool_calls": [call(call_id=f"call_{i}") for i in range(12)]},
            *[{"role": "tool", "tool_call_id": f"call_{i}", "content": deepcopy(self.blocks)} for i in range(12)],
        ]
        with patch("funharness.src.core.context.MAX_IMAGE_BYTES", 1):
            result = truncate_tool_results(messages)
        self.assertEqual(sum(any(b.get("type") == "image" for b in m["content"]) for m in result[1:]), 12)

    def test_agent_can_execute_a_program_then_view_its_new_image(self):
        generated = self.root / "generated.png"
        program = self.root / "render.py"
        program.write_text("from PIL import Image\nImage.new('RGB', (100, 80), 'red').save('generated.png')\n", encoding="utf-8")
        client = ScriptedClient([
            {"content": None, "tool_calls": [call("tool_run_command", {"command": f'"{sys.executable}" render.py'})]},
            {"content": None, "tool_calls": [call(arguments={"path": "generated.png"}, call_id="read_generated")]},
            {"content": "The generated image was inspected visually and contains a solid red rectangle."},
        ])
        agent = FunHarnessAgent(mode="auto", llm_client=client)
        try:
            self.assertFalse(generated.exists())
            agent.run("Run render.py, then inspect the generated image.")
            self.assertTrue(generated.exists())
            self.assertEqual(client.requests[2]["messages"][-1]["content"][-1]["type"], "image_url")
        finally:
            agent.scheduler.stop()

    def test_session_save_restore_branch_preserve_pixels_and_text_title(self):
        manager = SessionManager(str(self.root))
        session = Session(messages=[{"role": "user", "content": self.blocks}])
        manager.save(session)
        loaded = manager.load(session.id)
        branch = manager.branch(session.id)
        self.path.unlink()
        self.assertIsInstance(loaded.title, str)
        self.assertEqual(loaded.messages, session.messages)
        self.assertEqual(branch.messages, session.messages)
        wire = sanitize_messages_for_api(loaded.messages)
        self.assertTrue(wire[0]["content"][-1]["image_url"]["url"].endswith(self.blocks[1]["data"]))

    def test_image_tools_obey_path_permissions(self):
        manager = PermissionManager(mode=PermissionMode.SUGGEST, path_policy=PathPolicy(allowed_dirs=[str(self.root)]))
        for name in ("tool_read_file", "tool_find_files"):
            self.assertEqual(classify_risk(name), "read")
            self.assertEqual(manager.check_tool_call(name, {"path": str(self.path)})[0], "allow")
            self.assertEqual(manager.check_tool_call(name, {"path": str(self.root.parent / "outside.png")})[0], "deny")

    def test_main_loop_keeps_media_and_hooks_callbacks_are_text_only(self):
        client = ScriptedClient([
            {"content": None, "tool_calls": [call(arguments={"path": "sample.png", "detail": "high"})]},
            {"content": "I inspected the provided image pixels and completed the requested visual review."},
        ])
        events = []
        agent = FunHarnessAgent(mode="auto", llm_client=client, on_tool_result=lambda *args: events.append(args))
        try:
            with patch.object(agent.hook_registry, "dispatch_post_tool", return_value=SimpleNamespace(feedback="checked")) as hook:
                agent.run("Inspect sample.png")
            tool = next(m for m in agent.messages if m["role"] == "tool")
            self.assertEqual(tool["content"][1]["type"], "image")
            self.assertIn("checked", tool["content"][-1]["text"])
            self.assertEqual(client.requests[1]["messages"][-1]["content"][-2]["type"], "image_url")
            self.assertEqual(client.requests[1]["messages"][-1]["content"][-2]["image_url"]["detail"], "high")
            self.assertIsInstance(hook.call_args.args[2], str)
            self.assertIsInstance(events[0][1], str)
            self.assertNotIn(self.blocks[1]["data"], json.dumps(events))
            self.assertNotIn(self.blocks[1]["data"], json.dumps(agent.tool_calls_history))
        finally:
            agent.scheduler.stop()

    def test_main_loop_accepts_direct_multimodal_user_content(self):
        client = ScriptedClient([{"content": "The image is a blue rectangle, verified by reading the supplied visual input."}])
        agent = FunHarnessAgent(mode="auto", llm_client=client)
        try:
            agent.run(self.blocks)
            self.assertEqual(client.requests[0]["messages"][-1]["content"][-1]["type"], "image_url")
        finally:
            agent.scheduler.stop()

    def test_subagent_uses_same_visual_adapter(self):
        client = ScriptedClient([
            {"content": None, "tool_calls": [call()]}, {"content": "blue"},
        ])
        agent = SubAgent("reviewer", llm_client=client, tool_registry=registry.subset(["file"]))
        self.assertEqual(agent.run("inspect sample.png"), "blue")
        self.assertEqual(agent.messages[-2]["content"][1]["type"], "image")
        self.assertEqual(client.requests[1]["messages"][-1]["content"][-1]["type"], "image_url")

    def test_attachment_tool_forwards_detail_through_main_loop(self):
        agent = FunHarnessAgent(mode="auto", llm_client=object())
        try:
            record = agent.attachments.add(self.path)
            client = ScriptedClient([
                {"content": None, "tool_calls": [call("tool_read_attachment", {"attachment_id": record.id, "detail": "high"})]},
                {"content": "The attached image has been inspected with high detail and shows a blue rectangle."},
            ])
            agent.llm_client = client
            agent.run("Inspect the attached image in high detail.")
            self.assertEqual(client.requests[1]["messages"][-1]["content"][-1]["image_url"]["detail"], "high")
        finally:
            agent.scheduler.stop()

    def test_group_tools_scope_images_and_preserve_them_through_run_and_restore(self):
        store = GroupStore(self.root / ".funharness" / "groups")
        group = store.save_group(AgentGroup(name="vision"))
        member = store.save_member(GroupMember(group_id=group.id, profile_id="p"))
        profile = AgentProfile(id="p", name="reviewer", enabled_tools=["file", "search"])
        run = store.save_run(GroupAgentRun(group_id=group.id, member_id=member.id, profile_id="p"))
        target = store.group_dir(group.id) / "sample.png"
        target.write_bytes(self.path.read_bytes())
        client = ScriptedClient([
            {"content": None, "tool_calls": [call()]}, {"content": "blue"},
        ])
        runner = GroupAgentRunner(store=store, workspace=self.root, llm_client=client)
        tools = runner._tool_registry(group, member, profile, run)
        for name in ("tool_read_file", "group_read_workspace", "tool_find_files"):
            self.assertIn("Path escapes", runner._call_tool(tools, name, {"path": str(self.path)}))
        for name in ("tool_read_file", "group_read_workspace"):
            result = runner._call_tool(tools, name, {"path": "sample.png", "detail": "high"})
            self.assertEqual(result[1]["type"], "image")
            self.assertEqual(result[1]["detail"], "high")
        default_tools = runner._tool_registry(group, member, AgentProfile(id="p", name="reviewer"), run)
        self.assertEqual(runner._call_tool(default_tools, "group_read_workspace", {"path": "sample.png", "detail": "original"})[1]["detail"], "original")
        session = GroupAgentSession(group_id=group.id, member_id=member.id)
        result = runner.run(group=group, member=member, profile=profile, session=session, run=run,
                            trigger=GroupMessage(group_id=group.id, content="inspect sample.png"), cancel_event=threading.Event())
        self.assertEqual(result, "blue")
        self.assertEqual(client.requests[1]["messages"][-1]["content"][-1]["type"], "image_url")
        self.assertNotIn(self.blocks[1]["data"], json.dumps(run.tool_calls))
        self.assertTrue(any(isinstance(m["content"], list) for m in session.messages))


if __name__ == "__main__":
    unittest.main()
