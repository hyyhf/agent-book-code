"""
FunHarness - Session Management

Save, restore, branch, list, delete conversation sessions.
"""
import json
import os
import uuid
from datetime import datetime
from pathlib import Path

_SESSIONS_DIR = ".funharness/sessions"


class Session:
    def __init__(self, session_id=None, title="", messages=None, parent_id=None):
        self.id = session_id or uuid.uuid4().hex[:12]
        self.title = title
        self.messages = messages or []
        self.created_at = datetime.now().isoformat()
        self.updated_at = self.created_at
        self.parent_id = parent_id

    def to_dict(self) -> dict:
        return {
            "id": self.id, "title": self.title, "messages": self.messages,
            "created_at": self.created_at, "updated_at": self.updated_at,
            "parent_id": self.parent_id,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Session":
        s = cls(session_id=data["id"], title=data.get("title", ""),
                messages=data.get("messages", []), parent_id=data.get("parent_id"))
        s.created_at = data.get("created_at", s.created_at)
        s.updated_at = data.get("updated_at", s.updated_at)
        return s


class SessionManager:
    def __init__(self, project_dir: str | None = None):
        root = Path(project_dir or os.getcwd())
        self.sessions_dir = root / _SESSIONS_DIR
        self.index_path = self.sessions_dir / "index.json"

    def _ensure_dir(self):
        self.sessions_dir.mkdir(parents=True, exist_ok=True)

    def _load_index(self) -> dict:
        if not self.index_path.exists():
            return {}
        return json.loads(self.index_path.read_text(encoding="utf-8"))

    def _save_index(self, index: dict):
        self._ensure_dir()
        self.index_path.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")

    def _session_path(self, sid: str) -> Path:
        return self.sessions_dir / f"{sid}.json"

    def save(self, session: Session) -> str:
        self._ensure_dir()
        session.updated_at = datetime.now().isoformat()
        if not session.title:
            for msg in session.messages:
                if msg.get("role") == "user":
                    text = msg.get("content", "")
                    session.title = text[:50] + ("..." if len(text) > 50 else "")
                    break
        self._session_path(session.id).write_text(
            json.dumps(session.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        index = self._load_index()
        index[session.id] = {
            "title": session.title, "created_at": session.created_at,
            "updated_at": session.updated_at, "message_count": len(session.messages),
            "parent_id": session.parent_id,
        }
        self._save_index(index)
        return f"Session saved: {session.id} ({len(session.messages)} messages)"

    def load(self, session_id: str) -> Session | None:
        path = self._session_path(session_id)
        if not path.exists():
            return None
        return Session.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def list_sessions(self) -> list[dict]:
        index = self._load_index()
        sessions = [{"id": sid, **meta} for sid, meta in index.items()]
        sessions.sort(key=lambda x: x.get("updated_at", ""), reverse=True)
        return sessions

    def delete(self, session_id: str) -> str:
        path = self._session_path(session_id)
        if path.exists():
            path.unlink()
        index = self._load_index()
        if session_id in index:
            del index[session_id]
            self._save_index(index)
            return f"Session {session_id} deleted"
        return f"Session {session_id} not found"

    def branch(self, session_id: str, at_message: int | None = None) -> Session | None:
        source = self.load(session_id)
        if not source:
            return None
        msgs = source.messages[:at_message] if at_message is not None else source.messages
        branch = Session(title=f"(branch) {source.title}", messages=list(msgs), parent_id=source.id)
        self.save(branch)
        return branch
