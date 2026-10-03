"""Bounded text snapshots and guarded, atomic single-file edits.

Locks serialize cooperating tools in this process. Atomic publication avoids
partial files; metadata checks detect ordinary external edits but are not an
OS compare-and-swap against arbitrary external writers.
"""
from __future__ import annotations

import bisect
import ctypes
import difflib
import hashlib
import json
import os
import stat
import tempfile
import threading
import time
from collections import OrderedDict
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .content import ToolResult

MAX_FILE_BYTES = 16 * 1024 * 1024
MAX_EDITS = 256
MAX_MATCHES = 10000
MAX_DIFF_CHARS = 40000
MAX_DIFF_LINES = 1000
CACHE_BYTES = 32 * 1024 * 1024
LOCK_TIMEOUT = 30.0


class EditError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass
class FileEditSession:
    """Observation ownership is per agent, never the global snapshot cache."""
    observed: OrderedDict = field(default_factory=OrderedDict)
    owner: object = None

    def record(self, key: str, revision: str | None):
        self.observed[key] = revision
        self.observed.move_to_end(key)
        while len(self.observed) > 4096:
            self.observed.popitem(last=False)


@dataclass
class _Scope:
    session: FileEditSession
    cwd: Path
    cancelled: Callable[[], bool]


_scope: ContextVar[_Scope | None] = ContextVar("file_edit_scope", default=None)
_cache: OrderedDict[str, Snapshot] = OrderedDict()
_cache_size = 0
_cache_lock = threading.Lock()
_locks: dict[str, list] = {}
_locks_guard = threading.Lock()


@contextmanager
def file_edit_scope(session: FileEditSession, cwd=None, cancelled=None, owner=None):
    if owner is not None and session.owner != owner:
        session.observed.clear()
        session.owner = owner
    token = _scope.set(_Scope(session, Path(cwd or Path.cwd()), cancelled or (lambda: False)))
    try:
        yield
    finally:
        _scope.reset(token)


def _check_cancel():
    scope = _scope.get()
    if scope and scope.cancelled():
        raise EditError("CANCELLED", "file operation cancelled before commit")


def _target(path: str) -> tuple[Path, str]:
    if not isinstance(path, str) or not path.strip():
        raise EditError("INVALID_ARGUMENT", "path must be a non-empty string")
    p = Path(path).expanduser()
    scope = _scope.get()
    if not p.is_absolute():
        p = (scope.cwd if scope else Path.cwd()) / p
    p = p.resolve()
    logical = str(p)
    if os.name == "nt":
        # Normalize identity independently of the extended Win32 spelling.
        if logical[:8].upper() == "\\\\?\\UNC\\":
            logical = "\\\\" + logical[8:]
        elif logical.startswith("\\\\?\\"):
            logical = logical[4:]
        if len(str(p)) >= 240:
            p = Path(_windows_path(p))
    return p, os.path.normcase(logical)


def _fingerprint(info):
    # Windows path stat and CRT fstat can expose different ctime meanings.
    # Birth time is consistent across both; mtime tracks content changes.
    identity_time = getattr(info, "st_birthtime_ns", info.st_ctime_ns) if os.name == "nt" else info.st_ctime_ns
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns,
            identity_time, info.st_mode, info.st_nlink)


def _probe(p: Path):
    try:
        info = p.stat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(info.st_mode):
        raise EditError("NOT_TEXT", "target must be a regular text file")
    if info.st_size > MAX_FILE_BYTES:
        raise EditError("FILE_TOO_LARGE", f"text editing limit is {MAX_FILE_BYTES} bytes")
    return _fingerprint(info)


@dataclass(frozen=True, slots=True)
class Snapshot:
    raw: bytes
    text: str
    fingerprint: tuple
    revision: str
    bom: bytes


def _decode(raw: bytes, fingerprint: tuple) -> Snapshot:
    if b"\x00" in raw:
        raise EditError("NOT_TEXT", "binary files cannot be edited as text")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise EditError("NOT_TEXT", "file is not valid UTF-8; convert its encoding explicitly") from None
    revision = _revision(raw, fingerprint)
    return Snapshot(raw, text, fingerprint, revision, b"\xef\xbb\xbf" if raw.startswith(b"\xef\xbb\xbf") else b"")


def _revision(raw, fingerprint):
    digest = hashlib.sha256(raw).hexdigest()
    return hashlib.sha256((repr(fingerprint) + digest).encode()).hexdigest()[:24]


def _cache_put(key, snapshot):
    global _cache_size
    # Account conservatively for Python Unicode storage as well as raw bytes.
    weight = len(snapshot.raw) + len(snapshot.text) * 4 + 256 if snapshot else 0
    with _cache_lock:
        old = _cache.pop(key, None)
        if old:
            _cache_size -= len(old.raw) + len(old.text) * 4 + 256
        if snapshot and weight <= CACHE_BYTES:
            _cache[key] = snapshot
            _cache_size += weight
        while _cache and (_cache_size > CACHE_BYTES or len(_cache) > 128):
            _, old = _cache.popitem(last=False)
            _cache_size -= len(old.raw) + len(old.text) * 4 + 256


def _snapshot(p, key) -> Snapshot | None:
    _check_cancel()
    fingerprint = _probe(p)
    if fingerprint is None:
        _cache_put(key, None)
        return None
    with _cache_lock:
        cached = _cache.get(key)
        if cached and cached.fingerprint == fingerprint:
            _cache.move_to_end(key)
            return cached
    # Check the opened file and path, including changes during the read.
    with p.open("rb") as handle:
        opened = _fingerprint(os.fstat(handle.fileno()))
        raw = handle.read(MAX_FILE_BYTES + 1)
        after = _fingerprint(os.fstat(handle.fileno()))
    if len(raw) > MAX_FILE_BYTES:
        raise EditError("FILE_TOO_LARGE", f"text editing limit is {MAX_FILE_BYTES} bytes")
    if fingerprint != opened or opened != after or after != _probe(p):
        raise EditError("FILE_CHANGED", "file changed while reading; read it again")
    result = _decode(raw, after)
    _cache_put(key, result)
    return result


@contextmanager
def _locked(key):
    with _locks_guard:
        entry = _locks.setdefault(key, [threading.Lock(), 0])
        entry[1] += 1
    acquired = False
    deadline = time.monotonic() + LOCK_TIMEOUT
    try:
        while not acquired:
            _check_cancel()
            acquired = entry[0].acquire(timeout=.05)
            if not acquired and time.monotonic() >= deadline:
                raise EditError("FILE_BUSY", "another edit is still running; retry this file later")
        yield
    finally:
        if acquired:
            entry[0].release()
        with _locks_guard:
            entry[1] -= 1
            if not entry[1]:
                _locks.pop(key, None)


def _record(key, snapshot):
    scope = _scope.get()
    if scope:
        scope.session.record(key, snapshot.revision if snapshot else None)


def record_missing(path):
    p, key = _target(path)
    with _locked(key):
        if _probe(p) is None:
            _record(key, None)
            _cache_put(key, None)


def _guard(key, snapshot, expected_revision):
    scope = _scope.get()
    revision = snapshot.revision if snapshot else None
    if expected_revision is not None:
        if revision != expected_revision:
            raise EditError("FILE_CHANGED", "expected_revision is stale; read the file again")
    if scope and snapshot:
        if key not in scope.session.observed:
            raise EditError("FILE_NOT_READ", "read this file with tool_read_file (or group_read_workspace) before editing")
        if scope.session.observed[key] != revision:
            raise EditError("FILE_CHANGED", "file changed since this agent read it; read the affected lines again")
    elif scope and key in scope.session.observed and scope.session.observed[key] is not None:
        raise EditError("FILE_CHANGED", "previously read file was deleted; read again before recreating")


def read_text_file(path, max_chars=300000, start_line=1, limit=None) -> str:
    for name, value in (("max_chars", max_chars), ("start_line", start_line)):
        if type(value) is not int or value < 1:
            raise EditError("INVALID_ARGUMENT", f"{name} must be a positive integer")
    if limit is not None and (type(limit) is not int or limit < 1):
        raise EditError("INVALID_ARGUMENT", "limit must be a positive integer")
    p, key = _target(path)
    with _locked(key):
        snapshot = _snapshot(p, key)
        _record(key, snapshot)
    if snapshot is None:
        raise FileNotFoundError(str(p))
    text = snapshot.text
    total = text.count("\n") + bool(text and not text.endswith("\n"))
    if start_line > max(total, 1):
        raise EditError("INVALID_ARGUMENT", f"start_line exceeds total lines ({total})")
    start = 0
    for _ in range(start_line - 1):
        start = text.find("\n", start) + 1
    end = len(text)
    if limit is not None:
        end = start
        for _ in range(limit):
            found = text.find("\n", end)
            end = len(text) if found < 0 else found + 1
            if end == len(text):
                break
    header = f"[{total} lines; start_line={start_line}; revision={snapshot.revision}]\n"
    budget = max(0, max_chars - len(header))
    excerpt = text[start:min(end, start + budget)]
    # Models see normalized newlines; the snapshot retains original bytes.
    result = header[:max_chars] + excerpt.replace("\r\n", "\n")
    if start + len(excerpt) < len(text):
        next_line = start_line + excerpt.count("\n")
        if excerpt.endswith("\n"):
            hint = f"start_line={next_line}"
        else:
            hint = f"start_line={next_line} (partially shown line; increase max_chars if needed)"
        result += f"\n...(truncated; continue with {hint}, limit=100)"
    return result


def normalize_edits(old_text, new_text, replacements, replace_all=False, expected_count=None):
    if type(replace_all) is not bool:
        raise EditError("INVALID_ARGUMENT", "replace_all must be boolean")
    if expected_count is not None and (type(expected_count) is not int or expected_count < 1):
        raise EditError("INVALID_ARGUMENT", "expected_count must be a positive integer")
    if replacements is not None:
        if old_text is not None or new_text is not None or replace_all or expected_count is not None:
            raise EditError("INVALID_ARGUMENT", "do not mix single-edit arguments with replacements")
        if isinstance(replacements, str):
            try:
                replacements = json.loads(replacements)
            except ValueError:
                raise EditError("INVALID_ARGUMENT", "replacements must be a JSON array") from None
    else:
        replacements = [dict(old_text=old_text, new_text=new_text, replace_all=replace_all, expected_count=expected_count)]
    if not isinstance(replacements, list) or not 1 <= len(replacements) <= MAX_EDITS:
        raise EditError("INVALID_ARGUMENT", f"replacements must contain 1..{MAX_EDITS} edits")
    result = []
    size = 0
    for i, item in enumerate(replacements, 1):
        if not isinstance(item, dict) or set(item) - {"old_text", "new_text", "replace_all", "expected_count"}:
            raise EditError("INVALID_ARGUMENT", f"replacement {i}: unknown fields or invalid object")
        old, new = item.get("old_text"), item.get("new_text")
        if not isinstance(old, str) or not old or not isinstance(new, str):
            raise EditError("INVALID_ARGUMENT", f"replacement {i}: non-empty old_text and explicit new_text are required; use new_text='' to delete")
        all_matches = item.get("replace_all", False)
        count = item.get("expected_count")
        if type(all_matches) is not bool or (count is not None and (type(count) is not int or count < 1)):
            raise EditError("INVALID_ARGUMENT", f"replacement {i}: invalid replace_all or expected_count")
        if count is not None and count > 1 and not all_matches:
            raise EditError("INVALID_ARGUMENT", f"replacement {i}: expected_count > 1 requires replace_all=true")
        size += len(old) + len(new)
        if size > MAX_FILE_BYTES:
            raise EditError("FILE_TOO_LARGE", "combined replacement text exceeds editing limit")
        result.append((old.replace("\r\n", "\n"), new.replace("\r\n", "\n"), all_matches, count))
    return result


def _plan(text: str, specs):
    # Single-line anchors need no EOL conversion at all. Uniform CRLF files
    # convert only the small needles; only mixed-EOL multiline searches need maps.
    has_multiline = any("\n" in spec[0] for spec in specs)
    has_crlf = has_multiline and "\r\n" in text
    uniform_crlf = has_crlf and text.count("\r\n") == text.count("\n")
    normalized = text.replace("\r\n", "\n") if has_crlf and not uniform_crlf else text
    cr_positions = []
    if normalized is not text:
        offset = 0
        while True:
            pos = text.find("\r\n", offset)
            if pos < 0:
                break
            cr_positions.append(pos - len(cr_positions))
            offset = pos + 2
    matches, counts = [], []
    default_ending = "\r\n" if text.find("\r\n") == text.find("\n") - 1 and "\n" in text else "\n"
    output_size = len(text)
    for i, (old, new, replace_all, expected) in enumerate(specs, 1):
        _check_cancel()
        needle = old.replace("\n", "\r\n") if uniform_crlf else old
        first = normalized.find(needle)
        if first < 0:
            raise EditError("TEXT_NOT_FOUND", f"replacement {i}: text not found; read the target lines and retry with exact text")
        # Stop immediately on ambiguity; never enumerate thousands of unwanted matches.
        second = normalized.find(needle, first + 1)
        if not replace_all and second >= 0:
            line1 = normalized.count("\n", 0, first) + 1
            line2 = normalized.count("\n", 0, second) + 1
            raise EditError("AMBIGUOUS_MATCH", f"replacement {i}: multiple matches near lines {line1}, {line2}; add context or explicitly set replace_all=true")
        pos, count = first, 0
        while pos >= 0:
            end = pos + len(needle)
            raw_start = pos + bisect.bisect_left(cr_positions, pos) if cr_positions else pos
            raw_end = end + bisect.bisect_left(cr_positions, end) if cr_positions else end
            raw_old = text[raw_start:raw_end]
            ending = "\r\n" if "\r\n" in raw_old else ("\n" if "\n" in raw_old else default_ending)
            raw_new = new.replace("\n", ending) if ending != "\n" else new
            # A semantic no-op also preserves mixed line endings exactly.
            if old == new:
                raw_new = raw_old
            matches.append((raw_start, raw_end, raw_new))
            output_size += len(raw_new) - (raw_end - raw_start)
            count += 1
            if len(matches) > MAX_MATCHES or output_size > MAX_FILE_BYTES:
                raise EditError("EDIT_LIMIT", "too many matches or resulting file too large; narrow the edit")
            if not replace_all:
                break
            pos = normalized.find(needle, end)
        if expected is not None and count != expected:
            raise EditError("MATCH_COUNT", f"replacement {i}: expected {expected} matches, found {count}; no changes made")
        counts.append(count)
    matches.sort(key=lambda item: item[0])
    for left, right in zip(matches, matches[1:]):
        if left[1] > right[0]:
            raise EditError("OVERLAPPING_EDITS", "replacement texts overlap; merge them into one edit")
    parts, cursor, changed = [], 0, []
    for start, end, new in matches:
        parts.extend((text[cursor:start], new))
        cursor = end
        if text[start:end] != new:
            changed.append((start, end, new))
    if not changed:
        return text, [], counts
    parts.append(text[cursor:])
    return "".join(parts), changed, counts


def _diff(text: str, edits):
    """Diff only touched line windows, with bounded work and presentation size."""
    groups = []
    for start, end, new in edits:
        left = text.rfind("\n", 0, start) + 1
        right = end if end > start and text[end - 1:end] == "\n" else text.find("\n", end)
        if right < 0:
            right = len(text)
        elif right != end or text[end - 1:end] != "\n":
            right += 1
        if groups and left <= groups[-1][1]:
            groups[-1][1] = max(groups[-1][1], right)
            groups[-1][2].append((start, end, new))
        else:
            groups.append([left, right, [(start, end, new)]])
    hunks, added, removed, used_chars, used_lines = [], 0, 0, 0, 0
    last_offset, old_line, delta = 0, 1, 0
    truncated = False
    for left, right, changes in groups:
        _check_cancel()
        old_line += text.count("\n", last_offset, left)
        last_offset = left
        before = text[left:right]
        pieces, cursor = [], left
        for start, end, new in changes:
            pieces.extend((text[cursor:start], new))
            cursor = end
        pieces.append(text[cursor:right])
        after = "".join(pieces)
        old_count = before.count("\n") + bool(before and not before.endswith("\n"))
        new_count = after.count("\n") + bool(after and not after.endswith("\n"))
        lines = []
        def append_line(kind, value, old_num, new_num):
            nonlocal used_chars, used_lines, truncated
            if used_chars + len(value) > MAX_DIFF_CHARS or used_lines >= MAX_DIFF_LINES:
                truncated = True
                return False
            lines.append({"type": kind, "oldNum": old_num, "newNum": new_num, "text": value})
            used_chars += len(value)
            used_lines += 1
            return True

        # Never split an arbitrarily large file into millions of Python strings.
        # Exact diff is limited to small touched windows; large rewrites get a
        # bounded preview and whole-window line counts in linear time.
        if old_count + new_count < 400 and len(before) + len(after) < 20000:
            def line_values(value):
                values = value.replace("\r\n", "\n").split("\n")
                if values[-1] == "":
                    values.pop()
                return values
            a, b = line_values(before), line_values(after)
            for tag, i, j, k, l in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
                if tag != "equal":
                    removed += j - i
                    added += l - k
                for index in range(i, j):
                    if not append_line("context" if tag == "equal" else "remove", a[index],
                                       old_line + index, old_line + delta + k + index - i if tag == "equal" else None):
                        break
                if tag != "equal":
                    for index in range(k, l):
                        if not append_line("add", b[index], None, old_line + delta + index):
                            break
        else:
            removed += old_count
            added += new_count
            for kind, value, base in (("remove", before, old_line), ("add", after, old_line + delta)):
                offset, index = 0, 0
                while offset < len(value):
                    if used_chars >= MAX_DIFF_CHARS or used_lines >= MAX_DIFF_LINES:
                        truncated = True
                        break
                    end = value.find("\n", offset)
                    if end < 0:
                        end = len(value)
                    clipped = min(end, offset + MAX_DIFF_CHARS - used_chars)
                    snippet = value[offset:clipped].removesuffix("\r")
                    if not append_line(kind, snippet, base + index if kind == "remove" else None,
                                       base + index if kind == "add" else None):
                        break
                    if clipped < end:
                        truncated = True
                        break
                    index += 1
                    offset = end + 1
        # Surface a final-newline-only edit, which a line diff alone cannot show.
        if before.endswith("\n") != after.endswith("\n"):
            append_line("context", "[final newline changed]", None, None)
        if lines:
            hunks.append({"header": f"@@ -{old_line},{old_count} +{old_line + delta},{new_count} @@", "lines": lines})
        delta += after.count("\n") - before.count("\n")
    return {"hunks": hunks, "lines_added": added, "lines_removed": removed, "diff_truncated": truncated}


def _windows_path(path):
    value = str(path)
    if value.startswith("\\\\?\\"):
        return value
    return "\\\\?\\UNC\\" + value[2:] if value.startswith("\\\\") else "\\\\?\\" + value


def _copy_windows_acl(source, destination):
    api = ctypes.WinDLL("advapi32", use_last_error=True)
    get = api.GetFileSecurityW
    get.argtypes = [ctypes.c_wchar_p, ctypes.c_ulong, ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong)]
    get.restype = ctypes.c_int
    set_acl = api.SetFileSecurityW
    set_acl.argtypes = [ctypes.c_wchar_p, ctypes.c_ulong, ctypes.c_void_p]
    set_acl.restype = ctypes.c_int
    size = ctypes.c_ulong()
    get(_windows_path(source), 4, None, 0, ctypes.byref(size))
    if not size.value:
        raise ctypes.WinError(ctypes.get_last_error())
    buffer = ctypes.create_string_buffer(size.value)
    if not get(_windows_path(source), 4, buffer, size.value, ctypes.byref(size)):
        raise ctypes.WinError(ctypes.get_last_error())
    if not set_acl(_windows_path(destination), 4 | 0x80000000, buffer):
        raise ctypes.WinError(ctypes.get_last_error())


def _publish(temp, target, existing):
    if not existing:
        os.link(temp, target)  # Atomic create-if-absent; never overwrite a racing creator.
    elif os.name == "nt":
        api = ctypes.WinDLL("kernel32", use_last_error=True)
        replace = api.ReplaceFileW
        replace.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_ulong, ctypes.c_void_p, ctypes.c_void_p]
        replace.restype = ctypes.c_int
        if not replace(_windows_path(target), _windows_path(temp), None, 0, None, None):
            raise ctypes.WinError(ctypes.get_last_error())
    else:
        os.replace(temp, target)


def _commit(p, before, raw):
    if before and before.fingerprint[-1] > 1:
        raise EditError("HARD_LINK", "editing a file with multiple hard links is unsupported; choose an explicit copy")
    if before and not before.fingerprint[-2] & stat.S_IWUSR:
        raise PermissionError("target is read-only")
    if before is None:
        p.parent.mkdir(parents=True, exist_ok=True)
    descriptor, filename = tempfile.mkstemp(prefix=".fh-edit-", dir=p.parent)
    temp = Path(filename)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            if before:
                if os.name == "nt":
                    _copy_windows_acl(p, temp)
                else:
                    os.fchmod(handle.fileno(), stat.S_IMODE(before.fingerprint[-2]))
            _check_cancel()
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        staged = temp.stat()
        deadline = time.monotonic() + .5
        while True:
            _check_cancel()
            if _probe(p) != (before.fingerprint if before else None):
                raise EditError("FILE_CHANGED", "file changed before commit; read it again, no edit applied")
            try:
                _publish(temp, p, before is not None)
                break
            except OSError as exc:
                if os.name != "nt" or getattr(exc, "winerror", None) not in (32, 33) or time.monotonic() >= deadline:
                    raise
                time.sleep(.025)
        # Once published, do not report a failed edit on cancellation/cleanup.
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass
        try:
            info = p.stat()
            return _fingerprint(info) if (info.st_ino == staged.st_ino and info.st_size == len(raw)
                                          and info.st_mtime_ns == staged.st_mtime_ns) else None
        except OSError:
            return None
    finally:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass


def _error(exc, path):
    if isinstance(exc, EditError):
        code = exc.code
    elif isinstance(exc, FileNotFoundError):
        code = "FILE_NOT_FOUND"
    elif isinstance(exc, PermissionError):
        code = "PERMISSION_DENIED"
    else:
        code = "WRITE_FAILED"
    return ToolResult(f"Error [{code}]: {exc}", display={"kind": "file_edit", "path": str(path), "changed": False, "code": code, "is_error": True}, is_error=True)


def edit_file(path, specs, expected_revision=None, *, content=None):
    """Apply a prepared batch, or write content, through the same guarded commit."""
    try:
        if expected_revision is not None and (not isinstance(expected_revision, str) or not expected_revision):
            raise EditError("INVALID_ARGUMENT", "expected_revision must be a non-empty string")
        p, key = _target(path)
        with _locked(key):
            before = _snapshot(p, key)
            _guard(key, before, expected_revision)
            if content is None:
                if before is None:
                    _record(key, None)
                    raise FileNotFoundError(f"file '{path}' not found")
                text, edits, counts = _plan(before.text, specs)
            else:
                if not isinstance(content, str):
                    raise EditError("INVALID_ARGUMENT", "content must be a string")
                text, counts = content, []
                edits = [(0, len(before.text) if before else 0, text)]
            unchanged_edit = content is None and not edits
            raw = before.raw if unchanged_edit else (before.bom if before else b"") + text.encode("utf-8")
            if not unchanged_edit and b"\x00" in raw:
                raise EditError("NOT_TEXT", "NUL bytes are not valid text edits")
            if len(raw) > MAX_FILE_BYTES:
                raise EditError("FILE_TOO_LARGE", f"result exceeds {MAX_FILE_BYTES} bytes")
            changed = before is None or raw != before.raw
            display = {"kind": "file_edit", "path": str(p), "changed": changed, "is_new": before is None, "is_error": False,
                       "revision_before": before.revision if before else None, "match_counts": counts}
            # Prepare bounded presentation before publication so formatting cannot
            # turn a committed edit into a failure and trigger model retries.
            display.update(_diff(before.text if before else "", edits) if changed else
                           {"hunks": [], "lines_added": 0, "lines_removed": 0, "diff_truncated": False})
            if changed:
                fingerprint = _commit(p, before, raw)
                # Text is already validated/decoded; do not scan and decode it again.
                bom = before.bom if before else b""
                if raw.startswith(b"\xef\xbb\xbf") and not bom:
                    bom, text = b"\xef\xbb\xbf", text.removeprefix("\ufeff")
                after = Snapshot(raw, text, fingerprint, _revision(raw, fingerprint), bom) if fingerprint else None
                _cache_put(key, after)
            else:
                after = before
            _record(key, after)
            display["revision_after"] = after.revision if after else None
            if not changed:
                message = f"No changes needed in {path} (file not rewritten)"
            elif content is None:
                message = f"Replaced {sum(counts)} occurrence(s) in {path} across {len(specs)} edit(s)"
            else:
                message = f"{'Created' if before is None else 'Written to'} {path} ({len(text)} chars)"
            return ToolResult(message, display=display, is_error=False)
    except (EditError, OSError, UnicodeError, ValueError) as exc:
        return _error(exc, path)
