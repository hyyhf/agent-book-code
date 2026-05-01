from __future__ import annotations

import asyncio
import json
import os
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from funharness.src.agent import FunHarnessAgent
from funharness.src.core.llm import MODEL
from funharness.src.core.session import Session

from .events import EventBus


class AgentService:
    def __init__(self, bus: EventBus, workspace: Path, loop: asyncio.AbstractEventLoop) -> None:
        self.bus = bus
        self.workspace = workspace
        self.loop = loop
        self.agent = FunHarnessAgent(
            mode="suggest",
            on_token=self._on_token,
            on_reasoning_token=self._on_reasoning_token,
            on_reasoning_start=self._on_reasoning_start,
            on_tool_gen=self._on_tool_gen,
            on_tool_call=self._on_tool_call,
            on_tool_result=self._on_tool_result,
            on_status=self._on_status,
            on_approval=self._on_approval,
        )
        self._busy = False
        self._busy_lock = threading.Lock()
        self._pending_approvals: dict[str, dict[str, Any]] = {}
        self._approval_lock = threading.Lock()

    def snapshot(self) -> dict[str, Any]:
        info = self.agent.get_info()
        return {
            **info,
            "model": MODEL,
            "busy": self._busy,
            "workspace": str(self.workspace),
            "cwd": os.getcwd(),
        }

    def publish_threadsafe(self, event_type: str, payload: dict[str, Any] | None = None) -> None:
        future = asyncio.run_coroutine_threadsafe(
            self.bus.publish(event_type, payload or {}),
            self.loop,
        )
        try:
            future.result(timeout=2)
        except Exception:
            pass

    async def publish_info(self) -> None:
        await self.bus.publish("info_update", self.snapshot())

    def _set_busy(self, value: bool) -> None:
        with self._busy_lock:
            self._busy = value

    def _ensure_idle(self) -> None:
        with self._busy_lock:
            if self._busy:
                raise HTTPException(status_code=409, detail="Agent is already running")
            self._busy = True

    async def chat(self, message: str) -> dict[str, Any]:
        text = message.strip()
        if not text:
            raise HTTPException(status_code=400, detail="Message is required")
        if text.lower() == "clear":
            return await self.clear()
        if text.startswith("/"):
            return await self.slash(text)

        self._ensure_idle()
        await self.bus.publish("run_started", {"kind": "chat"})
        await self.bus.publish("user_message", {"content": text})
        threading.Thread(target=self._run_chat_worker, args=(text,), daemon=True).start()
        return {"accepted": True}

    def _run_chat_worker(self, message: str) -> None:
        try:
            self.agent.run(message)
        except InterruptedError:
            self.publish_threadsafe("status", {"message": "Interrupted by user."})
        except Exception as exc:
            self.publish_threadsafe("error", {"message": str(exc)})
        finally:
            self.publish_threadsafe("reasoning_done", {})
            self._set_busy(False)
            self.publish_threadsafe("run_finished", {"kind": "chat"})
            self.publish_threadsafe("info_update", self.snapshot())

    async def slash(self, command: str) -> dict[str, Any]:
        cmd = command.strip()
        if not cmd.startswith("/"):
            cmd = "/" + cmd
        self._ensure_idle()
        await self.bus.publish("run_started", {"kind": "slash", "command": cmd})
        await self.bus.publish("user_message", {"content": cmd})
        threading.Thread(target=self._run_slash_worker, args=(cmd,), daemon=True).start()
        return {"accepted": True}

    def _run_slash_worker(self, command: str) -> None:
        try:
            result = self.agent.handle_slash_command(command)
            if result is not None:
                self.publish_threadsafe("system_message", {"content": result})
        except Exception as exc:
            self.publish_threadsafe("error", {"message": str(exc)})
        finally:
            self._set_busy(False)
            self.publish_threadsafe("run_finished", {"kind": "slash", "command": command})
            self.publish_threadsafe("info_update", self.snapshot())

    async def approve(self, approval_id: str, approved: bool, choice: str) -> dict[str, Any]:
        with self._approval_lock:
            pending = self._pending_approvals.get(approval_id)
            if pending is None:
                raise HTTPException(status_code=404, detail="Approval request not found")
            pending["approved"] = approved
            pending["choice"] = choice
            pending["event"].set()
        await self.bus.publish(
            "status",
            {"message": "Approval accepted" if approved else "Approval denied"},
        )
        return {"ok": True}

    async def interrupt(self) -> dict[str, Any]:
        self.agent.request_interrupt()
        with self._approval_lock:
            pending = list(self._pending_approvals.values())
            for item in pending:
                item["approved"] = False
                item["choice"] = ""
                item["event"].set()
        await self.bus.publish("status", {"message": "Interrupt requested."})
        await self.publish_info()
        return {"ok": True}

    async def clear(self) -> dict[str, Any]:
        self.agent.clear()
        await self.bus.publish("system_message", {"content": "[conversation cleared]"})
        await self.publish_info()
        return {"ok": True}

    async def set_mode(self, mode: str) -> dict[str, Any]:
        result = self.agent.handle_slash_command(f"/mode {mode}")
        await self.bus.publish("system_message", {"content": result or ""})
        await self.publish_info()
        return {"ok": True, "message": result}

    async def new_session(self) -> dict[str, Any]:
        result = self.agent.handle_slash_command("/new")
        await self.bus.publish("system_message", {"content": result or ""})
        await self.publish_info()
        return {"ok": True, "message": result}

    async def save_session(self) -> dict[str, Any]:
        result = self.agent.handle_slash_command("/save")
        await self.bus.publish("system_message", {"content": result or ""})
        await self.publish_info()
        return {"ok": True, "message": result}

    async def delete_session(self, session_id: str) -> dict[str, Any]:
        result = self.agent.session_mgr.delete(session_id)
        if "not found" in result:
            raise HTTPException(status_code=404, detail="Session not found")
        await self.publish_info()
        return {"ok": True, "message": result}

    def skills(self) -> list[dict[str, Any]]:
        return self.agent.skill_loader.list_skills()

    def sessions(self) -> list[dict[str, Any]]:
        return self.agent.session_mgr.list_sessions()

    def get_session(self, session_id: str) -> dict[str, Any]:
        session = self.agent.session_mgr.load(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="Session not found")
        return session.to_dict()

    async def load_session(self, session_id: str) -> dict[str, Any]:
        with self._busy_lock:
            if self._busy:
                raise HTTPException(status_code=409, detail="Agent is already running")

        session = self.agent.session_mgr.load(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="Session not found")

        self.agent.current_session.messages = self.agent.messages
        self.agent.session_mgr.save(self.agent.current_session)

        self.agent._build_system_prompt()
        messages = list(session.messages)
        if messages and messages[0].get("role") == "system":
            messages[0] = {"role": "system", "content": self.agent._system_prompt}
        else:
            messages.insert(0, {"role": "system", "content": self.agent._system_prompt})

        loaded = Session(
            session_id=session.id,
            title=session.title,
            messages=messages,
            parent_id=session.parent_id,
        )
        loaded.created_at = session.created_at
        loaded.updated_at = session.updated_at
        self.agent.current_session = loaded
        self.agent.messages = messages
        self.agent.tool_calls_history.clear()

        payload = {
            "session_id": loaded.id,
            "title": loaded.title,
            "messages": self._messages_for_gui(messages),
        }
        await self.bus.publish("session_loaded", payload)
        await self.publish_info()
        return {"ok": True, **payload}

    def memory(self) -> dict[str, Any]:
        path = self.workspace / ".funharness" / "MEMORY.md"
        if not path.exists():
            return {"path": str(path), "content": "# FunHarness Memory\n\n"}
        return {"path": str(path), "content": path.read_text(encoding="utf-8")}

    async def update_memory(self, content: str) -> dict[str, Any]:
        path = self.workspace / ".funharness" / "MEMORY.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        self.agent._build_system_prompt()
        if self.agent.messages and self.agent.messages[0].get("role") == "system":
            self.agent.messages[0] = {"role": "system", "content": self.agent._system_prompt}
        else:
            self.agent.messages.insert(0, {"role": "system", "content": self.agent._system_prompt})
        saved_at = datetime.now().isoformat(timespec="seconds")
        payload = {"path": str(path), "saved_at": saved_at}
        await self.bus.publish("memory_saved", payload)
        await self.publish_info()
        return {"ok": True, **payload}

    def tasks(self) -> dict[str, Any]:
        task_list = self.agent.task_list
        return {
            "summary": task_list.summary() if task_list else "No task list. Use /plan to create one.",
            "tasks": [task.to_dict() for task in task_list.tasks] if task_list else [],
        }

    def schedules(self) -> list[dict[str, Any]]:
        return [record.to_dict() for record in self.agent.scheduler.list()]

    def runtime(self) -> list[dict[str, Any]]:
        return [task.to_dict() for task in self.agent.runtime.list()]

    def runtime_output(self, runtime_id: str) -> dict[str, Any]:
        return {"runtime_id": runtime_id, "output": self.agent.runtime.output(runtime_id)}

    def list_files(self, rel_path: str = ".") -> dict[str, Any]:
        target = self._safe_path(rel_path)
        if not target.is_dir():
            raise HTTPException(status_code=400, detail="Path is not a directory")
        entries = []
        for child in sorted(target.iterdir(), key=lambda p: (p.is_file(), p.name.lower())):
            if child.name.startswith(".") and child.name != ".funharness":
                continue
            try:
                stat = child.stat()
            except OSError:
                continue
            entries.append({
                "name": child.name,
                "path": str(child.relative_to(self.workspace)),
                "kind": "directory" if child.is_dir() else "file",
                "size": stat.st_size,
                "modified_at": stat.st_mtime,
            })
        return {"path": str(target.relative_to(self.workspace)), "entries": entries}

    def read_file(self, rel_path: str) -> dict[str, Any]:
        target = self._safe_path(rel_path)
        if not target.is_file():
            raise HTTPException(status_code=400, detail="Path is not a file")
        size = target.stat().st_size
        suffix = target.suffix.lower()
        binary_exts = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".pdf", ".ico"}
        if suffix in binary_exts:
            return {
                "path": str(target.relative_to(self.workspace)),
                "kind": "binary",
                "extension": suffix,
                "size": size,
            }
        if size > 1_000_000:
            raise HTTPException(status_code=413, detail="File is too large to preview")
        try:
            content = target.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return {
                "path": str(target.relative_to(self.workspace)),
                "kind": "binary",
                "extension": suffix,
                "size": size,
            }
        return {
            "path": str(target.relative_to(self.workspace)),
            "kind": "text",
            "extension": suffix,
            "size": size,
            "content": content,
        }

    def raw_file(self, rel_path: str) -> Path:
        target = self._safe_path(rel_path)
        if not target.is_file():
            raise HTTPException(status_code=400, detail="Path is not a file")
        return target

    def _safe_path(self, rel_path: str) -> Path:
        target = (self.workspace / rel_path).resolve()
        if target != self.workspace and self.workspace not in target.parents:
            raise HTTPException(status_code=403, detail="Path is outside workspace")
        return target

    def _messages_for_gui(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        rendered = []
        for message in messages:
            role = message.get("role")
            if role == "system":
                continue
            if role == "user":
                rendered.append({"type": "user", "content": message.get("content") or ""})
            elif role == "assistant":
                content = message.get("content") or ""
                reasoning = message.get("reasoning_content") or ""
                if reasoning:
                    rendered.append({"type": "reasoning", "content": reasoning, "done": True})
                if content:
                    rendered.append({"type": "assistant", "content": content, "streaming": False})
            elif role == "tool":
                rendered.append({"type": "system", "content": message.get("content") or ""})
        return rendered

    def _on_token(self, token: str) -> None:
        self.publish_threadsafe("assistant_delta", {"token": token})

    def _on_reasoning_token(self, token: str) -> None:
        self.publish_threadsafe("reasoning_delta", {"token": token})

    def _on_reasoning_start(self) -> None:
        self.publish_threadsafe("reasoning_start", {})

    def _on_tool_gen(self, index: int, name: str, chunk: str) -> None:
        self.publish_threadsafe("tool_gen_delta", {"index": index, "name": name, "chunk": chunk})

    def _on_tool_call(self, name: str, preview: str, risk: str) -> None:
        parsed_preview: Any = preview
        try:
            parsed_preview = json.loads(preview)
        except Exception:
            pass
        self.publish_threadsafe("tool_call", {"name": name, "preview": parsed_preview, "risk": risk})

    def _on_tool_result(self, name: str, result: str, hook_feedback: str) -> None:
        self.publish_threadsafe(
            "tool_result",
            {"name": name, "result": result, "hook_feedback": hook_feedback},
        )

    def _on_status(self, message: str) -> None:
        self.publish_threadsafe("status", {"message": message})

    def _on_approval(self, tool_name: str, arguments: dict[str, Any], reason: str) -> tuple[bool, str]:
        approval_id = f"approval_{uuid.uuid4().hex[:10]}"
        event = threading.Event()
        pending = {"event": event, "approved": False, "choice": ""}
        with self._approval_lock:
            self._pending_approvals[approval_id] = pending
        self.publish_threadsafe(
            "approval_requested",
            {
                "approval_id": approval_id,
                "tool_name": tool_name,
                "arguments": arguments,
                "reason": reason,
            },
        )
        event.wait()
        with self._approval_lock:
            self._pending_approvals.pop(approval_id, None)
            approved = bool(pending["approved"])
            choice = str(pending["choice"] or "")
        time.sleep(0.05)
        return approved, choice
