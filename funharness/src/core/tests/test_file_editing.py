from __future__ import annotations

import json
import os
import stat
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from funharness.src.core import file_editing as editing
from funharness.src.core.tools import tool_read_file, tool_replace_in_file, tool_write_file


class FileEditingTests(unittest.TestCase):
    def test_high_frequency_edits_are_not_mistaken_for_failed_loops(self):
        from funharness.src.core.hooks import LoopDetectionMiddleware, SelfVerificationMiddleware
        from funharness.src.core.observability import FailurePattern
        history = [{"tool": "tool_replace_in_file", "args": {"path": f"Error{i}.py"},
                    "result": f"Replaced in Error{i}.py", "is_error": False, "changed": True}
                   for i in range(6)]
        context = LoopDetectionMiddleware().process({"tool_calls_history": history})
        self.assertFalse(context.get("should_stop"))
        self.assertEqual(context.get("injections", []), [])
        self.assertEqual(FailurePattern.analyze([], history), [])
        middleware = SelfVerificationMiddleware(verify_interval=1)
        history[-1]["changed"] = False
        self.assertNotIn("injections", middleware.process({"tool_calls_history": history}))

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.path = self.root / "中文 error in file.txt"
        self.path.write_bytes(b"first\nsecond\nthird\n")

    def edit(self, old="first", new="FIRST", **kwargs):
        return tool_replace_in_file(str(self.path), old, new, **kwargs)

    def test_ambiguous_match_never_writes(self):
        self.path.write_bytes(b"same same")
        result = self.edit("same", "new")
        self.assertTrue(result.is_error)
        self.assertEqual(result.display["code"], "AMBIGUOUS_MATCH")
        self.assertEqual(self.path.read_bytes(), b"same same")

    def test_overlapping_occurrences_are_ambiguous(self):
        self.path.write_bytes(b"aaa")
        self.assertEqual(self.edit("aa", "b").display["code"], "AMBIGUOUS_MATCH")

    def test_explicit_all_and_expected_count(self):
        self.path.write_bytes(b"x x x")
        self.assertTrue(self.edit("x", "y", replace_all=True, expected_count=2).is_error)
        self.assertEqual(self.path.read_bytes(), b"x x x")
        self.assertFalse(self.edit("x", "y", replace_all=True, expected_count=3).is_error)
        self.assertEqual(self.path.read_bytes(), b"y y y")

    def test_parameter_errors_never_delete_or_ignore_edits(self):
        invalid = [
            {"old_text": "first"},
            {"replacements": [{"old_text": "first"}]},
            {"replacements": [{"old_text": "first", "new_text": None}]},
            {"replacements": [{"old_text": "first", "new_text": "x", "typo": True}]},
            {"old_text": "first", "new_text": "x", "replacements": [{"old_text": "second", "new_text": "y"}]},
            {"old_text": "first", "new_text": "x", "replace_all": "false"},
            {"old_text": "first", "new_text": "x", "expected_count": True},
            {"old_text": "first", "new_text": "x", "expected_count": 2},
            {"replacements": []},
        ]
        before = self.path.read_bytes()
        for args in invalid:
            with self.subTest(args=args):
                self.assertTrue(tool_replace_in_file(str(self.path), **args).is_error)
                self.assertEqual(self.path.read_bytes(), before)
        self.assertFalse(self.edit("first\n", "").is_error)
        self.assertEqual(self.path.read_bytes(), b"second\nthird\n")

    def test_batch_non_cascading_and_one_commit(self):
        self.path.write_bytes(b"a b")
        with patch.object(editing, "_publish", wraps=editing._publish) as publish:
            result = tool_replace_in_file(str(self.path), replacements=[
                {"old_text": "a", "new_text": "b"}, {"old_text": "b", "new_text": "c"},
            ])
        self.assertFalse(result.is_error)
        self.assertEqual(publish.call_count, 1)
        self.assertEqual(self.path.read_bytes(), b"b c")

    def test_batch_overlap_or_missing_is_all_or_nothing(self):
        original = self.path.read_bytes()
        for old in ("fir", "missing"):
            result = tool_replace_in_file(str(self.path), replacements=[
                {"old_text": "first", "new_text": "changed"}, {"old_text": old, "new_text": "x"},
            ])
            self.assertTrue(result.is_error)
            self.assertEqual(self.path.read_bytes(), original)

    def test_newlines_bom_and_unicode_are_preserved(self):
        for raw, old, new, expected in [
            (b"a\nb\n", "a", "A", b"A\nb\n"),
            (b"a\r\nb\r\n", "a\nb", "A\r\nB", b"A\r\nB\r\n"),
            (b"a\r\nb\nc\r\n", "b\nc", "B\nC", b"a\r\nB\nC\r\n"),
            (b"\xef\xbb\xbfa\r\nb", "a", "A", b"\xef\xbb\xbfA\r\nb"),
            ("中文 ’ 原样\nnext".encode(), "next", "后续", "中文 ’ 原样\n后续".encode()),
        ]:
            with self.subTest(raw=raw):
                self.path.write_bytes(raw)
                self.assertFalse(self.edit(old, new).is_error)
                self.assertEqual(self.path.read_bytes(), expected)

    def test_noop_never_publishes_or_changes_timestamp(self):
        self.path.write_bytes(b"a\r\nb\nc\r\n")
        before = self.path.stat().st_mtime_ns
        with patch.object(editing, "_publish", side_effect=AssertionError("should not write")):
            result = self.edit("a\nb\nc", "a\nb\nc")
        self.assertFalse(result.is_error)
        self.assertFalse(result.display["changed"])
        self.assertEqual(self.path.stat().st_mtime_ns, before)
        self.assertEqual(self.path.read_bytes(), b"a\r\nb\nc\r\n")

    def test_line_window_is_literal_and_records_version(self):
        session = editing.FileEditSession()
        with editing.file_edit_scope(session, self.root):
            text = tool_read_file(str(self.path), start_line=2, limit=1)
            self.assertIn("\nsecond\n", text)
            self.assertNotIn("first", text)
            self.assertIn("start_line=3", text)
            self.assertFalse(self.edit("second", "SECOND").is_error)
        self.assertEqual(self.path.read_bytes(), b"first\nSECOND\nthird\n")

    def test_read_requirements_and_stale_recovery(self):
        with editing.file_edit_scope(editing.FileEditSession(), self.root):
            self.assertEqual(self.edit().display["code"], "FILE_NOT_READ")
            tool_read_file(str(self.path), limit=1)
            self.path.write_bytes(b"first\nexternal\n")
            self.assertEqual(self.edit().display["code"], "FILE_CHANGED")
            self.assertEqual(tool_write_file(str(self.path), "overwrite").display["code"], "FILE_CHANGED")
            tool_read_file(str(self.path), limit=1)
            self.assertFalse(self.edit().is_error)
            self.assertEqual(self.path.read_bytes(), b"FIRST\nexternal\n")

    def test_observation_is_not_shared_between_agents(self):
        with editing.file_edit_scope(editing.FileEditSession(), self.root):
            tool_read_file(str(self.path))
        with editing.file_edit_scope(editing.FileEditSession(), self.root):
            self.assertEqual(self.edit().display["code"], "FILE_NOT_READ")

    def test_session_owner_change_invalidates_observation(self):
        session = editing.FileEditSession()
        with editing.file_edit_scope(session, self.root, owner="old"):
            tool_read_file(str(self.path))
        with editing.file_edit_scope(session, self.root, owner="new"):
            self.assertEqual(self.edit().display["code"], "FILE_NOT_READ")

    def test_successful_edit_updates_observation(self):
        with editing.file_edit_scope(editing.FileEditSession(), self.root):
            tool_read_file(str(self.path))
            first = self.edit()
            second = self.edit("second", "SECOND")
            self.assertFalse(first.is_error)
            self.assertFalse(second.is_error)
            self.assertEqual(first.display["revision_after"], second.display["revision_before"])

    def test_optional_revision_rejects_stale_direct_call(self):
        revision = self.edit().display["revision_after"]
        self.path.write_bytes(b"first\nchanged\n")
        self.assertEqual(self.edit(expected_revision=revision).display["code"], "FILE_CHANGED")

    def test_deleted_file_can_be_reread_and_recreated(self):
        with editing.file_edit_scope(editing.FileEditSession(), self.root):
            tool_read_file(str(self.path))
            self.path.unlink()
            self.assertEqual(tool_write_file(str(self.path), "new").display["code"], "FILE_CHANGED")
            self.assertIn("not found", tool_read_file(str(self.path)))
            self.assertFalse(tool_write_file(str(self.path), "new").is_error)
            self.assertFalse(self.edit("new", "NEW").is_error)

    def test_concurrent_agents_have_one_winner(self):
        barrier = threading.Barrier(2)
        def worker(new):
            with editing.file_edit_scope(editing.FileEditSession(), self.root):
                tool_read_file(str(self.path))
                barrier.wait(timeout=5)
                return self.edit(new=new)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(worker, ["ONE", "TWO"]))
        self.assertEqual(sum(not result.is_error for result in results), 1)
        self.assertEqual([r.display["code"] for r in results if r.is_error], ["FILE_CHANGED"])

    def test_direct_concurrent_disjoint_edits_are_serialized(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda pair: self.edit(*pair), [("first", "FIRST"), ("second", "SECOND")]))
        self.assertTrue(all(not r.is_error for r in results))
        self.assertEqual(self.path.read_bytes(), b"FIRST\nSECOND\nthird\n")

    def test_external_change_before_commit_is_not_overwritten(self):
        commit = editing._commit
        def external(p, before, raw):
            p.write_bytes(b"external change")
            return commit(p, before, raw)
        with patch.object(editing, "_commit", side_effect=external):
            result = self.edit()
        self.assertEqual(result.display["code"], "FILE_CHANGED")
        self.assertEqual(self.path.read_bytes(), b"external change")
        self.assertEqual(list(self.root.glob(".fh-edit-*")), [])

    def test_publish_failure_preserves_original_and_cleans_temp(self):
        original = self.path.read_bytes()
        with patch.object(editing, "_publish", side_effect=OSError("disk fault")):
            result = self.edit()
        self.assertTrue(result.is_error)
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(list(self.root.glob(".fh-edit-*")), [])

    def test_create_race_never_overwrites(self):
        target = self.root / "new.txt"
        publish = editing._publish
        def racer(temp, p, existing):
            p.write_bytes(b"racing creator")
            return publish(temp, p, existing)
        with patch.object(editing, "_publish", side_effect=racer):
            result = tool_write_file(str(target), "ours")
        self.assertTrue(result.is_error)
        self.assertEqual(target.read_bytes(), b"racing creator")

    def test_cancellation_before_commit_and_after_commit(self):
        cancelled = threading.Event()
        publish = editing._publish
        with editing.file_edit_scope(editing.FileEditSession(), self.root, cancelled.is_set):
            tool_read_file(str(self.path))
            cancelled.set()
            self.assertEqual(self.edit().display["code"], "CANCELLED")
            cancelled.clear()
            def complete_then_cancel(*args):
                publish(*args)
                cancelled.set()
            with patch.object(editing, "_publish", side_effect=complete_then_cancel):
                result = self.edit()
        self.assertFalse(result.is_error)
        self.assertTrue(self.path.read_bytes().startswith(b"FIRST"))

    def test_lock_wait_is_bounded(self):
        _, key = editing._target(str(self.path))
        with editing._locked(key), patch.object(editing, "LOCK_TIMEOUT", .01):
            with ThreadPoolExecutor(max_workers=1) as pool:
                result = pool.submit(self.edit).result(timeout=2)
        self.assertEqual(result.display["code"], "FILE_BUSY")

    def test_cached_edits_do_not_reread_the_source(self):
        tool_read_file(str(self.path))
        real_open = Path.open
        reads = []
        def tracked(path, *args, **kwargs):
            if path == self.path and args and args[0] == "rb":
                reads.append(path)
            return real_open(path, *args, **kwargs)
        with patch.object(Path, "open", tracked):
            self.assertFalse(self.edit().is_error)
            self.assertFalse(self.edit("second", "SECOND").is_error)
        self.assertEqual(reads, [])

    def test_external_change_invalidates_cache(self):
        tool_read_file(str(self.path))
        self.path.write_bytes(b"different")
        self.assertIn("different", tool_read_file(str(self.path)))

    def test_hunks_are_compact_and_have_correct_line_offsets(self):
        original = "".join(f"line {i}\n" for i in range(1, 10001))
        self.path.write_bytes(original.encode())
        result = tool_replace_in_file(str(self.path), replacements=[
            {"old_text": "line 10\n", "new_text": "changed 10\nextra\n"},
            {"old_text": "line 9000\n", "new_text": "changed 9000\n"},
        ])
        self.assertFalse(result.is_error)
        self.assertLess(len(json.dumps(result.display)), 2000)
        self.assertEqual(result.display["lines_added"], 3)
        self.assertEqual(result.display["lines_removed"], 2)
        self.assertEqual(result.display["hunks"][1]["lines"][1]["newNum"], 9001)

    def test_two_edits_on_same_line_produce_one_hunk(self):
        self.path.write_bytes(b"a b c\n")
        result = tool_replace_in_file(str(self.path), replacements=[
            {"old_text": "a", "new_text": "A"}, {"old_text": "c", "new_text": "C"},
        ])
        self.assertEqual(result.display["lines_added"], 1)
        self.assertEqual(result.display["lines_removed"], 1)
        self.assertEqual(len(result.display["hunks"]), 1)

    def test_large_diff_is_bounded(self):
        result = tool_write_file(str(self.path), "large line\n" * 10000)
        self.assertFalse(result.is_error)
        self.assertTrue(result.display["diff_truncated"])
        self.assertLessEqual(sum(len(h["lines"]) for h in result.display["hunks"]), editing.MAX_DIFF_LINES)
        self.assertEqual(result.display["lines_added"], 10000)

    def test_very_long_line_preview_is_bounded(self):
        result = tool_write_file(str(self.path), "x" * 200000)
        self.assertTrue(result.display["diff_truncated"])
        self.assertLess(len(json.dumps(result.display)), 50000)
        self.assertEqual(result.display["lines_added"], 1)

    def test_final_newline_change_is_visible(self):
        self.path.write_bytes(b"abc")
        result = self.edit("abc", "abc\n")
        self.assertFalse(result.is_error)
        self.assertIn("final newline changed", json.dumps(result.display))

    @unittest.skipUnless(os.name == "nt", "Windows native path handling")
    def test_windows_long_unicode_path(self):
        target = self.root / ("long-directory-" * 8) / ("more-directory-" * 8) / "中文.py"
        native = Path(editing._windows_path(target))
        def clean_fixture():
            native.unlink(missing_ok=True)
            if native.parent.exists():
                native.parent.rmdir()
            if native.parent.parent.exists():
                native.parent.parent.rmdir()
        self.addCleanup(clean_fixture)
        native.parent.mkdir(parents=True)
        native.write_bytes(b"before\r\n")
        result = tool_replace_in_file(str(target), "before", "after")
        self.assertFalse(result.is_error, str(result))
        self.assertEqual(native.read_bytes(), b"after\r\n")

    def test_binary_invalid_utf8_and_limits(self):
        for raw in (b"first\x00rest", b"first\xff"):
            self.path.write_bytes(raw)
            self.assertEqual(self.edit().display["code"], "NOT_TEXT")
            self.assertEqual(self.path.read_bytes(), raw)
        self.path.write_bytes(b"first")
        with patch.object(editing, "MAX_FILE_BYTES", 4):
            self.assertTrue(self.edit().is_error)

    def test_readonly_and_hardlink_files(self):
        self.path.chmod(stat.S_IREAD)
        try:
            self.assertTrue(self.edit().is_error)
        finally:
            self.path.chmod(stat.S_IREAD | stat.S_IWRITE)
        link = self.root / "hardlink.txt"
        try:
            os.link(self.path, link)
        except OSError:
            return
        self.assertEqual(self.edit().display["code"], "HARD_LINK")

    def test_symlink_updates_target_and_preserves_link(self):
        link = self.root / "alias.txt"
        try:
            link.symlink_to(self.path)
        except OSError:
            self.skipTest("symlink privilege unavailable")
        with editing.file_edit_scope(editing.FileEditSession(), self.root):
            tool_read_file(str(link))
            result = self.edit()
        self.assertFalse(result.is_error)
        self.assertTrue(link.is_symlink())
        self.assertEqual(link.read_bytes(), self.path.read_bytes())


if __name__ == "__main__":
    unittest.main()
