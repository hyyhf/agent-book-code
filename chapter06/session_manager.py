"""
6.2 - 会话管理

实现完整的会话管理系统:
  - 会话的保存与恢复: 将对话历史序列化为 JSON 文件
  - 会话分支: 从历史中的某个点创建分支
  - 会话列表与元数据: 用轻量的 index.json 跟踪所有会话

运行方式:
    uv run python chapter06/session_manager.py
"""
import json
import os
import uuid
from datetime import datetime
from pathlib import Path

# 会话文件存储位置
_SESSIONS_DIR = ".funharness/sessions"


class Session:
    """表示一个对话会话。

    每个 Session 包含:
    - id: 唯一标识符
    - title: 可读标题(取自首条用户消息)
    - messages: 完整的消息历史
    - created_at / updated_at: 时间戳
    - parent_id: 如果是分支会话, 记录父会话 ID
    """

    def __init__(
        self,
        session_id: str | None = None,
        title: str = "",
        messages: list[dict] | None = None,
        parent_id: str | None = None,
    ):
        self.id = session_id or uuid.uuid4().hex[:12]
        self.title = title
        self.messages = messages or []
        self.created_at = datetime.now().isoformat()
        self.updated_at = self.created_at
        self.parent_id = parent_id

    def to_dict(self) -> dict:
        """序列化为字典。"""
        return {
            "id": self.id,
            "title": self.title,
            "messages": self.messages,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "parent_id": self.parent_id,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Session":
        """从字典反序列化。"""
        session = cls(
            session_id=data["id"],
            title=data.get("title", ""),
            messages=data.get("messages", []),
            parent_id=data.get("parent_id"),
        )
        session.created_at = data.get("created_at", session.created_at)
        session.updated_at = data.get("updated_at", session.updated_at)
        return session


class SessionManager:
    """管理多个会话的生命周期: 创建、保存、恢复、分支、列表。

    存储结构:
        .funharness/sessions/
            index.json          # 会话索引(ID -> 元信息)
            <session_id>.json   # 各会话的完整消息历史

    index.json 只保存轻量元信息(标题、时间、消息数),
    具体的消息历史在需要恢复时才从会话文件中加载。
    """

    def __init__(self, project_dir: str | None = None):
        root = Path(project_dir or os.getcwd())
        self.sessions_dir = root / _SESSIONS_DIR
        self.index_path = self.sessions_dir / "index.json"

    def _ensure_dir(self):
        self.sessions_dir.mkdir(parents=True, exist_ok=True)

    def _load_index(self) -> dict:
        """加载会话索引。"""
        if not self.index_path.exists():
            return {}
        return json.loads(self.index_path.read_text(encoding="utf-8"))

    def _save_index(self, index: dict):
        """保存会话索引。"""
        self._ensure_dir()
        self.index_path.write_text(
            json.dumps(index, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _session_path(self, session_id: str) -> Path:
        return self.sessions_dir / f"{session_id}.json"

    def save(self, session: Session) -> str:
        """保存会话到磁盘。

        同时更新索引文件和会话文件。

        Args:
            session: 要保存的 Session 对象

        Returns:
            操作结果信息
        """
        self._ensure_dir()
        session.updated_at = datetime.now().isoformat()

        # 自动从首条用户消息提取标题
        if not session.title:
            for msg in session.messages:
                if msg.get("role") == "user":
                    text = msg.get("content", "")
                    session.title = text[:50] + ("..." if len(text) > 50 else "")
                    break

        # 保存完整会话文件
        session_file = self._session_path(session.id)
        session_file.write_text(
            json.dumps(session.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        # 更新索引(只存元信息)
        index = self._load_index()
        index[session.id] = {
            "title": session.title,
            "created_at": session.created_at,
            "updated_at": session.updated_at,
            "message_count": len(session.messages),
            "parent_id": session.parent_id,
        }
        self._save_index(index)

        return f"Session saved: {session.id} ({len(session.messages)} messages)"

    def load(self, session_id: str) -> Session | None:
        """从磁盘加载一个会话。

        Args:
            session_id: 会话 ID

        Returns:
            Session 对象, 不存在则返回 None
        """
        path = self._session_path(session_id)
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return Session.from_dict(data)

    def list_sessions(self) -> list[dict]:
        """列出所有已保存的会话(元信息)。

        Returns:
            按更新时间倒序排列的会话元信息列表
        """
        index = self._load_index()
        sessions = []
        for sid, meta in index.items():
            sessions.append({"id": sid, **meta})
        sessions.sort(key=lambda x: x.get("updated_at", ""), reverse=True)
        return sessions

    def delete(self, session_id: str) -> str:
        """删除一个会话。

        Args:
            session_id: 要删除的会话 ID

        Returns:
            操作结果信息
        """
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
        """从已有会话创建分支。

        从指定会话的某个消息位置创建一个新分支,
        保留到该位置为止的所有消息历史。

        Args:
            session_id: 源会话 ID
            at_message: 从第几条消息处分支(None 表示当前位置)

        Returns:
            新创建的分支 Session, 源会话不存在则返回 None
        """
        source = self.load(session_id)
        if not source:
            return None

        msgs = source.messages
        if at_message is not None:
            msgs = msgs[:at_message]

        branch_session = Session(
            title=f"(branch) {source.title}",
            messages=list(msgs),  # 深拷贝消息列表
            parent_id=source.id,
        )
        self.save(branch_session)
        return branch_session


# =============================================================
#  演示
# =============================================================

if __name__ == "__main__":
    import tempfile

    print("=== 会话管理演示 ===\n")

    with tempfile.TemporaryDirectory() as tmpdir:
        mgr = SessionManager(tmpdir)

        # 1. 创建并保存会话
        print("--- 创建会话 ---")
        s1 = Session()
        s1.messages = [
            {"role": "system", "content": "You are FunHarness."},
            {"role": "user", "content": "Read pyproject.toml"},
            {"role": "assistant", "content": "Let me read that file for you."},
            {"role": "user", "content": "Now add a dependency"},
            {"role": "assistant", "content": "I'll add openai to the dependencies."},
        ]
        print(mgr.save(s1))

        s2 = Session()
        s2.messages = [
            {"role": "system", "content": "You are FunHarness."},
            {"role": "user", "content": "Search for TODO in the codebase"},
            {"role": "assistant", "content": "Found 3 TODOs."},
        ]
        print(mgr.save(s2))

        # 2. 列出会话
        print("\n--- 会话列表 ---")
        for s in mgr.list_sessions():
            print(f"  [{s['id']}] {s['title']} ({s['message_count']} msgs)")

        # 3. 恢复会话
        print("\n--- 恢复会话 ---")
        restored = mgr.load(s1.id)
        if restored:
            print(f"  Restored: {restored.title}")
            print(f"  Messages: {len(restored.messages)}")
            last_msg = restored.messages[-1]
            print(f"  Last: [{last_msg['role']}] {last_msg['content'][:60]}")

        # 4. 分支
        print("\n--- 创建分支 ---")
        branch = mgr.branch(s1.id, at_message=3)
        if branch:
            print(f"  Branch ID: {branch.id}")
            print(f"  Parent: {branch.parent_id}")
            print(f"  Messages: {len(branch.messages)} (branched at msg 3)")

        # 5. 最终会话列表
        print("\n--- 最终会话列表 ---")
        for s in mgr.list_sessions():
            parent = f" (branch of {s['parent_id']})" if s.get("parent_id") else ""
            print(f"  [{s['id']}] {s['title']}{parent}")

        # 6. 删除
        print("\n--- 删除会话 ---")
        print(mgr.delete(s2.id))
        print(f"  Remaining: {len(mgr.list_sessions())} sessions")
