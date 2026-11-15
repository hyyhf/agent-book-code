"""Bounded workspace browsing and incremental filename search (no file contents).

Call from a worker thread. Search advances a shared, time-sliced index rather
than walking the entire workspace again for every keystroke.
"""
from __future__ import annotations

import heapq
import os
import stat
import threading
import time
from collections import OrderedDict, deque
from pathlib import Path
from typing import Iterator


# These trees can contain millions of generated/internal entries. They remain
# browsable, and an explicit directory prefix opts into searching them.
SEARCH_SKIP_DIRS = frozenset({".git", ".hg", ".svn", ".venv", "venv", "node_modules", "__pycache__", ".funharness"})


class WorkspaceFiles:
    def __init__(self, root: Path, *, search_slice_seconds: float = 0.04, max_index_entries: int = 200_000):
        self.root = root.resolve()
        self.search_slice_seconds = search_slice_seconds
        self.max_index_entries = max_index_entries
        self._lock = threading.Lock()
        self._directories: OrderedDict[str, tuple[float, list]] = OrderedDict()
        self._indexes: OrderedDict[str, dict] = OrderedDict()

    def safe_path(self, path: str) -> Path:
        target = (self.root / path.replace("\\", "/")).resolve()
        if not target.is_relative_to(self.root):
            raise ValueError("Path is outside workspace")
        return target

    def invalidate(self) -> None:
        with self._lock:
            self._directories.clear()
            for index in self._indexes.values():
                index["walk"].close()
            self._indexes.clear()

    def list_directory(self, path: str = ".", offset: int = 0, limit: int | None = None, refresh: bool = False) -> dict:
        target = self.safe_path(path)
        if not target.is_dir():
            raise NotADirectoryError(path)
        key = target.relative_to(self.root).as_posix()
        with self._lock:
            cached = self._directories.get(key)
            if refresh or cached is None or time.monotonic() - cached[0] > 2:
                with os.scandir(target) as scan:
                    children = []
                    for child in scan:
                        if child.name.startswith(".") and child.name != ".funharness":
                            continue
                        try:
                            children.append((not child.is_dir(), child.name.casefold(), child))
                        except OSError:
                            continue
                children.sort(key=lambda item: (item[0], item[1], item[2].name))
                self._directories[key] = (time.monotonic(), children)
            else:
                children = cached[1]
            self._directories.move_to_end(key)
            while len(self._directories) > 16:
                self._directories.popitem(last=False)
            end = len(children) if limit is None else offset + limit
            entries = []
            for is_file, _, child in children[offset:end]:
                try:
                    stat = child.stat()
                except OSError:
                    continue
                entries.append({
                    "name": child.name,
                    "path": (target / child.name).relative_to(self.root).as_posix(),
                    "kind": "file" if is_file else "directory",
                    "size": stat.st_size,
                    "modified_at": stat.st_mtime,
                })
            return {"path": key, "entries": entries, "total": len(children),
                    "next_offset": end if end < len(children) else None}

    def _walk(self, scope: Path) -> Iterator[tuple[str, str, str] | None]:
        pending = deque([scope])
        while pending:
            directory = pending.popleft()
            try:
                with os.scandir(directory) as scan:
                    for child in scan:
                        # Yield even skipped entries so a giant ignored directory
                        # cannot defeat the time budget. Never follow links/junctions.
                        try:
                            is_dir = child.is_dir(follow_symlinks=False)
                            is_junction = is_dir and os.name == 'nt' and bool(
                                child.stat(follow_symlinks=False).st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT
                            )
                            if child.is_symlink() or is_junction:
                                yield None
                                continue
                            rel = Path(child.path).relative_to(self.root).as_posix()
                            if is_dir and child.name not in SEARCH_SKIP_DIRS:
                                pending.append(Path(child.path))
                            yield (child.name, rel, "directory" if is_dir else "file")
                        except OSError:
                            yield None
            except OSError:
                yield None
            # Empty directories must also yield control to the slice budget.
            yield None

    def search(self, query: str, limit: int = 80) -> dict:
        normalized = query.strip().replace("\\", "/")
        while normalized.startswith('./'):
            normalized = normalized[2:]
        needle = normalized.casefold()
        scope = self.root
        # Explicit paths narrow the search, including normally skipped trees.
        if "/" in needle:
            prefix = normalized.rsplit("/", 1)[0]
            candidate = self.safe_path(prefix)
            if candidate.is_dir():
                scope = candidate
        key = scope.relative_to(self.root).as_posix()
        with self._lock:
            now = time.monotonic()
            index = self._indexes.get(key)
            if index is None or (index["done"] and now - index["finished"] > 10):
                if index:
                    index["walk"].close()
                index = {"walk": self._walk(scope), "entries": [], "done": False, "finished": now, "truncated": False}
                self._indexes[key] = index
            self._indexes.move_to_end(key)
            while len(self._indexes) > 4:
                _, removed = self._indexes.popitem(last=False)
                removed["walk"].close()
            deadline = now + self.search_slice_seconds
            while not index["done"]:
                try:
                    entry = next(index["walk"])
                except StopIteration:
                    index["done"] = True
                    index["finished"] = time.monotonic()
                    break
                if entry:
                    index["entries"].append((*entry, entry[0].casefold(), entry[1].casefold()))
                if len(index["entries"]) >= self.max_index_entries:
                    index["truncated"] = True
                    index["done"] = True
                    index["finished"] = time.monotonic()
                    index["walk"].close()
                if time.monotonic() >= deadline:
                    break
            def rank(entry):
                name, path = entry[3:]
                return (name != needle, not name.startswith(needle), needle not in name,
                        path.count("/"), entry[2] != "directory", path)
            matches = (entry for entry in index["entries"] if not needle or needle in entry[4])
            best = heapq.nsmallest(limit + 1, matches, key=rank)
            return {"path": key,
                    "entries": [{"name": e[0], "path": e[1], "kind": e[2], "size": 0, "modified_at": 0} for e in best[:limit]],
                    "indexing": not index["done"], "has_more": len(best) > limit,
                    "truncated": index["truncated"]}
