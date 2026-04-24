"""
FunHarness - Persistent Memory

MEMORY.md-based cross-session knowledge storage.
"""
import os
import re
from datetime import datetime
from pathlib import Path

_MEMORY_DIR = ".funharness"
_MEMORY_FILE = "MEMORY.md"


class MemoryStore:
    def __init__(self, project_dir: str | None = None):
        root = Path(project_dir or os.getcwd())
        self.memory_dir = root / _MEMORY_DIR
        self.memory_path = self.memory_dir / _MEMORY_FILE

    def _ensure_file(self):
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        if not self.memory_path.exists():
            self.memory_path.write_text("# FunHarness Memory\n\n", encoding="utf-8")

    def read_all(self) -> str:
        if not self.memory_path.exists():
            return "(no memories saved yet)"
        text = self.memory_path.read_text(encoding="utf-8")
        if len(text.strip()) <= len("# FunHarness Memory"):
            return "(no memories saved yet)"
        return text

    def add(self, title: str, content: str) -> str:
        self._ensure_file()
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        entry = f"\n## {title}\n_{timestamp}_\n\n{content}\n"
        with open(self.memory_path, "a", encoding="utf-8") as f:
            f.write(entry)
        return f"Memory saved: '{title}'"

    def search(self, keyword: str) -> str:
        if not self.memory_path.exists():
            return "(no memories to search)"
        text = self.memory_path.read_text(encoding="utf-8")
        sections = re.split(r"(?=^## )", text, flags=re.MULTILINE)
        pattern = re.compile(re.escape(keyword), re.IGNORECASE)
        matches = [s.strip() for s in sections if s.startswith("## ") and pattern.search(s)]
        if not matches:
            return f"No memories matching '{keyword}'"
        return f"Found {len(matches)} matching memories:\n\n" + "\n\n".join(matches)

    def list_titles(self) -> list[str]:
        if not self.memory_path.exists():
            return []
        text = self.memory_path.read_text(encoding="utf-8")
        return re.findall(r"^## (.+)$", text, re.MULTILINE)


_store: MemoryStore | None = None


def init_memory(project_dir: str | None = None):
    global _store
    _store = MemoryStore(project_dir)


def _get_store() -> MemoryStore:
    global _store
    if _store is None:
        _store = MemoryStore()
    return _store


def read_memory() -> str:
    return _get_store().read_all()


def save_memory(title: str, content: str) -> str:
    return _get_store().add(title, content)


def search_memory(keyword: str) -> str:
    return _get_store().search(keyword)
