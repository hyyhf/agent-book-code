"""Isolated group agent runner."""
from __future__ import annotations

import json
import re
import time
from copy import deepcopy
from pathlib import Path
from threading import Event
from typing import Any, Callable

from ..llm import MODEL, call_with_retry, client, process_stream_response
from ..permissions import SandboxExecutor
from ..tools import ToolRegistry, registry as global_tool_registry
from .context_builder import GroupContextBuilder
from .models import AgentGroup, AgentProfile, GroupAgentRun, GroupAgentSession, GroupArtifact, GroupMember, GroupMessage
from .store import GroupStore


class GroupAgentRunner:
    _MAX_ITERATIONS = 40

    def __init__(
        self,
        *,
        store: GroupStore,
        workspace: str | Path,
        llm_client: Any = None,
        model: str = MODEL,
        skills_summary: str = "",
        enabled_skill_names: list[str] | None = None,
        skill_loader: Any = None,
        event_sink: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> None:
        self.store = store
        self.workspace = Path(workspace)
        self.llm_client = llm_client or client
        self.model = model
        self.skills_summary = skills_summary
        self.enabled_skill_names = set(enabled_skill_names or [])
        self.skill_loader = skill_loader
        self.event_sink = event_sink

    def run(
        self,
        *,
        group: AgentGroup,
        member: GroupMember,
        profile: AgentProfile,
        session: GroupAgentSession,
        run: GroupAgentRun,
        trigger: GroupMessage,
        cancel_event: Event,
    ) -> str:
        builder = GroupContextBuilder(self.store, self.workspace, self.skills_summary)
        context = builder.build(group=group, member=member, profile=profile, session=session, run=run, trigger=trigger)
        system = (
            "You are a careful, practical FunHarness group agent. "
            "Keep your public reply concise and useful. "
            "Use tools when they help inspect the workspace, search the web, recall memory, or save concrete deliverables."
        )
        messages = [
            {"role": "system", "content": system},
            *session.messages[-8:],
            {"role": "user", "content": context},
        ]
        registry = self._tool_registry(group, member, profile, run)
        tools = registry.get_openai_schemas() or None

        for _ in range(self._MAX_ITERATIONS):
            if cancel_event.is_set():
                return "(cancelled)"
            stream_message: GroupMessage | None = None

            def on_token(token: str) -> None:
                nonlocal stream_message
                if stream_message is None:
                    stream_message = GroupMessage(
                        group_id=group.id,
                        sender_type="agent",
                        sender_id=member.id,
                        sender_name=member.display_name,
                        content="",
                        mentions=[],
                        run_id=run.id,
                    )
                    self.store.append_message(stream_message)
                    self._emit("group_message_created", stream_message.to_dict() | {"streaming": True})
                stream_message.content += token
                self.store.update_message(stream_message)
                self._emit("group_message_delta", {
                    "group_id": group.id,
                    "member_id": member.id,
                    "run_id": run.id,
                    "message_id": stream_message.id,
                    "delta": token,
                    "content": stream_message.content,
                    "message": stream_message.to_dict(),
                })

            stream = call_with_retry(
                messages,
                tools or [],
                stream=True,
                model=self.model,
                llm_client=self.llm_client,
            )
            tool_generation_base = len(run.tool_calls)
            tool_gen_records: dict[int, dict[str, Any]] = {}

            def on_tool_gen(index: int, name: str, chunk: str) -> None:
                tool_index = tool_generation_base + index
                record = tool_gen_records.setdefault(tool_index, {
                    "index": tool_index,
                    "name": name or "",
                    "arguments": "",
                    "result": "",
                    "timestamp": time.time(),
                    "status": "generating",
                })
                if name:
                    record["name"] = name
                record["arguments"] = str(record.get("arguments") or "") + chunk
                self._emit("group_tool_gen_delta", {
                    "group_id": group.id,
                    "member_id": member.id,
                    "run_id": run.id,
                    "index": tool_index,
                    "name": record["name"],
                    "chunk": chunk,
                    "tool_call": self._tool_record_snapshot(record),
                })

            msg = process_stream_response(
                stream,
                on_token=on_token,
                on_tool_gen=on_tool_gen,
                should_interrupt=cancel_event.is_set,
            )
            messages.append(msg)
            if not msg.get("tool_calls"):
                output = msg.get("content") or ""
                session.messages = self._compact_messages(messages)
                session.private_context_summary = self._next_summary(session.private_context_summary, trigger.content, output)
                return output

            generated_indices = sorted(tool_gen_records)
            for index, tool_call in enumerate(msg.get("tool_calls", [])):
                if cancel_event.is_set():
                    return "(cancelled)"
                name = tool_call["function"]["name"]
                try:
                    arguments = json.loads(tool_call["function"].get("arguments") or "{}")
                except json.JSONDecodeError:
                    arguments = {}
                tool_index = generated_indices[index] if index < len(generated_indices) else tool_generation_base + index
                tool_record = {
                    "index": tool_index,
                    "name": name,
                    "arguments": arguments,
                    "result": "",
                    "timestamp": time.time(),
                    "status": "running",
                }
                run.tool_calls.append(tool_record)
                self.store.save_run(run)
                self._emit("group_tool_call", {
                    "group_id": group.id,
                    "member_id": member.id,
                    "run_id": run.id,
                    "name": name,
                    "arguments": arguments,
                    "tool_call": self._tool_record_snapshot(tool_record),
                })
                result = self._call_tool(registry, name, arguments)
                tool_record["result"] = result
                tool_record["status"] = "done"
                tool_record["finished_at"] = time.time()
                self.store.save_run(run)
                self._emit("group_tool_result", {
                    "group_id": group.id,
                    "member_id": member.id,
                    "run_id": run.id,
                    "name": name,
                    "result": result[:4000],
                    "tool_call": self._tool_record_snapshot(tool_record),
                })
                messages.append({"role": "tool", "tool_call_id": tool_call["id"], "content": result})
        return "(reached max group agent iterations)"

    def _tool_registry(self, group: AgentGroup, member: GroupMember, profile: AgentProfile, run: GroupAgentRun) -> ToolRegistry:
        registry = self._safe_inherited_registry(profile, group, member, run)
        return registry

    def _safe_inherited_registry(
        self,
        profile: AgentProfile,
        group: AgentGroup,
        member: GroupMember,
        run: GroupAgentRun,
    ) -> ToolRegistry:
        inherited = ToolRegistry()
        base_tools = {
            "tool_grep_search",
            "tool_replace_in_file",
            "tool_run_command",
            "tool_list_skills",
            "tool_load_skill",
            "tool_web_search",
            "tool_web_fetch",
            "tool_web_crawl",
        }
        optional_categories = set(profile.enabled_tools or [])

        for name, entry in global_tool_registry.list_tools().items():
            category = str(entry.get("category") or "")
            if name not in base_tools and category not in optional_categories:
                continue
            inherited._tools[name] = dict(entry)

        if "tool_list_skills" not in inherited:
            @inherited.tool(category="skill")
            def tool_list_skills() -> str:
                """List skills enabled for this group agent."""
                return self._list_selected_skills()

        if "tool_load_skill" not in inherited:
            @inherited.tool(category="skill")
            def tool_load_skill(name: str) -> str:
                """Load a skill enabled for this group agent.

                Args:
                    name: Skill name from tool_list_skills
                """
                if name not in self.enabled_skill_names:
                    return f"Skill not enabled for this group agent: {name}"
                return "Skill loading is unavailable in this runtime."

        self._scope_tool(inherited, "tool_list_skills", lambda _func: lambda: self._list_selected_skills())
        self._scope_tool(inherited, "tool_load_skill", lambda _func: lambda name: self._load_selected_skill(name))
        scratch = self._group_scratch_path(group.id, member.id)
        self._scope_tool(
            inherited,
            "tool_grep_search",
            lambda func: lambda pattern, path=".", glob="**/*", ignore_case=True, literal=False, max_results=80: func(
                pattern=pattern,
                path=str(self._safe_group_workspace_path(group.id, path)),
                glob=glob,
                ignore_case=ignore_case,
                literal=literal,
                max_results=max_results,
            ),
        )
        self._scope_tool(
            inherited,
            "tool_replace_in_file",
            lambda func: lambda path, old_text="", new_text="", replacements=None: func(
                str(self._safe_group_path(group.id, member.id, path)),
                old_text=old_text,
                new_text=new_text,
                replacements=replacements,
            ),
        )
        self._scope_tool(
            inherited,
            "tool_run_command",
            lambda _func: lambda command: SandboxExecutor(work_dir=scratch).execute(command),
        )

        store = self.store

        @inherited.tool(category="group")
        def group_list_workspace(path: str = ".") -> str:
            """List files in this group chat workspace without modifying them."""
            base = self._safe_group_workspace_path(group.id, path)
            if not base.exists() or not base.is_dir():
                return f"Not a directory: {path}"
            entries = []
            group_root = store.group_dir(group.id).resolve()
            for item in sorted(base.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))[:200]:
                if item.name.startswith("."):
                    continue
                kind = "dir" if item.is_dir() else "file"
                entries.append(f"{kind}: {item.relative_to(group_root).as_posix()}")
            return "\n".join(entries) or "(empty)"

        @inherited.tool(category="group")
        def group_read_workspace(path: str, max_chars: int = 12000) -> str:
            """Read a text file from this group chat workspace."""
            target = self._safe_group_workspace_path(group.id, path)
            if not target.exists() or not target.is_file():
                return f"File not found: {path}"
            try:
                return target.read_text(encoding="utf-8", errors="replace")[:max(1000, min(max_chars, 50000))]
            except OSError as exc:
                return f"Read failed: {exc}"

        @inherited.tool(category="group")
        def group_search_workspace(pattern: str, path: str = ".", max_results: int = 60) -> str:
            """Search text files in this group chat workspace."""
            root = self._safe_group_workspace_path(group.id, path)
            if not root.exists():
                return f"Path not found: {path}"
            regex = re.compile(pattern, re.IGNORECASE)
            results: list[str] = []
            files = [root] if root.is_file() else root.rglob("*")
            group_root = store.group_dir(group.id).resolve()
            for candidate in files:
                if len(results) >= max(1, min(max_results, 200)):
                    break
                if not candidate.is_file() or any(part.startswith(".") for part in candidate.relative_to(group_root).parts):
                    continue
                try:
                    text = candidate.read_text(encoding="utf-8", errors="ignore")
                except OSError:
                    continue
                for line_no, line in enumerate(text.splitlines(), 1):
                    if regex.search(line):
                        results.append(f"{candidate.relative_to(group_root).as_posix()}:{line_no}: {line[:160]}")
                        break
            return "\n".join(results) or "(no matches)"

        @inherited.tool(category="group")
        def group_write_artifact(filename: str, title: str, content: str) -> str:
            """Write a group deliverable to this agent's artifact area."""
            safe_name = _safe_filename(filename or f"{title or 'artifact'}.md")
            rel = Path(".funharness") / "groups" / group.id / "artifacts" / member.id / safe_name
            artifact = GroupArtifact(
                group_id=group.id,
                member_id=member.id,
                run_id=run.id,
                title=title or safe_name,
                path=rel.as_posix(),
                preview=content[:240],
            )
            store.save_artifact(artifact, content)
            run.artifacts.append(artifact.to_dict())
            store.save_run(run)
            self._emit("group_artifact_created", artifact.to_dict())
            return f"Saved artifact: {artifact.path}"

        return inherited

    @staticmethod
    def _scope_tool(registry: ToolRegistry, name: str, wrapper_factory: Callable[[Callable[..., Any]], Callable[..., Any]]) -> None:
        entry = registry._tools.get(name)
        if not entry:
            return
        entry["function"] = wrapper_factory(entry["function"])

    def _safe_group_workspace_path(self, group_id: str, path: str) -> Path:
        group_root = self.store.group_dir(group_id).resolve()
        raw = Path(path or ".")
        parts = raw.parts
        if raw.is_absolute():
            candidate = raw
        elif len(parts) >= 3 and parts[0] == ".funharness" and parts[1] == "groups" and parts[2] == group_id:
            candidate = self.workspace / raw
        else:
            candidate = group_root / raw
        resolved = candidate.resolve()
        if resolved != group_root and group_root not in resolved.parents:
            raise ValueError("Path escapes group chat folder")
        return resolved

    def _group_scratch_path(self, group_id: str, member_id: str) -> Path:
        scratch = self.store.group_dir(group_id) / "scratch" / member_id
        scratch.mkdir(parents=True, exist_ok=True)
        return scratch.resolve()

    def _safe_group_path(self, group_id: str, member_id: str, path: str) -> Path:
        group_root = self.store.group_dir(group_id).resolve()
        scratch = self._group_scratch_path(group_id, member_id)
        raw = Path(path or ".")
        parts = raw.parts
        if raw.is_absolute():
            candidate = raw
        elif len(parts) >= 3 and parts[0] == ".funharness" and parts[1] == "groups" and parts[2] == group_id:
            candidate = self.workspace / raw
        else:
            candidate = scratch / raw
        resolved = candidate.resolve()
        if resolved != group_root and group_root not in resolved.parents:
            raise ValueError("Path escapes group chat folder")
        return resolved

    def _list_selected_skills(self) -> str:
        if not self.enabled_skill_names:
            return json.dumps({"skills": [], "diagnostics": []}, ensure_ascii=False, indent=2)
        return self.skills_summary or "(no selected skills found)"

    def _load_selected_skill(self, name: str) -> str:
        if name not in self.enabled_skill_names:
            return f"Skill not enabled for this group agent: {name}"
        if self.skill_loader is None:
            return "Skill loading is unavailable in this runtime."
        skill = self.skill_loader.get(name)
        if skill is None:
            return f"Skill not found or disabled: {name}"
        return (
            f"# Skill: {skill.name}\n"
            f"Description: {skill.description}\n"
            f"Path: {skill.path}\n"
            f"Source: {skill.source}\n\n"
            f"{skill.raw_content}"
        )

    def _call_tool(self, registry: ToolRegistry, name: str, arguments: dict[str, Any]) -> str:
        func = registry.get_function(name)
        if func is None:
            return f"Unknown tool: {name}"
        try:
            return str(func(**arguments))
        except Exception as exc:
            return f"Tool error ({name}): {type(exc).__name__}: {exc}"

    @staticmethod
    def _compact_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        compact = []
        for message in messages[-10:]:
            role = message.get("role")
            if role in {"system", "tool"}:
                continue
            content = message.get("content")
            if role == "assistant" and content is None:
                continue
            if role not in {"user", "assistant"}:
                continue
            compact.append({"role": role, "content": content or ""})
        return compact[-8:]

    @staticmethod
    def _next_summary(previous: str, user_message: str, output: str) -> str:
        text = f"{previous}\nLatest user request: {user_message}\nLatest answer: {output[:700]}".strip()
        return text[-3000:]

    def _emit(self, event_type: str, payload: dict[str, Any]) -> None:
        if self.event_sink:
            self.event_sink(event_type, deepcopy(payload))

    @staticmethod
    def _tool_record_snapshot(record: dict[str, Any]) -> dict[str, Any]:
        snapshot = deepcopy(record)
        result = snapshot.get("result")
        if isinstance(result, str) and len(result) > 4000:
            snapshot["result"] = result[:4000]
        return snapshot


def _safe_filename(value: str) -> str:
    name = Path(value.strip().replace("\\", "/")).name or "artifact.md"
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", name)
    if "." not in name:
        name += ".md"
    return name[:120]
