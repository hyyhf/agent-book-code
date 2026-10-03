"""Compare the pre-upgrade batch algorithm with the guarded editor, locally.

Only temporary fixtures are modified. No model calls. --baseline-source can
load the exact saved pre-upgrade tools.py; otherwise use the equivalent batch
algorithm below. GUI timings include successful diff production (not the old
batch-path parsing bug which accidentally skipped it).
"""
from __future__ import annotations

import argparse
import ast
import difflib
import json
import statistics
import sys
import tempfile
import time
from pathlib import Path
from typing import TypedDict

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from funharness.src.core.file_editing import FileEditSession, file_edit_scope
from funharness.src.core.tools import tool_read_file, tool_replace_in_file


def baseline(path, *, replacements):
    p = Path(path)
    content = p.read_text(encoding="utf-8")
    matches = []
    for index, spec in enumerate(replacements):
        start = 0
        while True:
            found = content.find(spec["old_text"], start)
            if found < 0:
                break
            start = found + len(spec["old_text"])
            matches.append((found, start, index))
    matches.sort()
    for a, b in zip(matches, matches[1:]):
        assert a[1] <= b[0]
    parts, cursor = [], 0
    for start, end, index in matches:
        parts.extend((content[cursor:start], replacements[index]["new_text"]))
        cursor = end
    parts.append(content[cursor:])
    p.write_text("".join(parts), encoding="utf-8")
    return f"Replaced {len(matches)} occurrence(s) in {path}"


def load_baseline(path):
    class ReplacementSpec(TypedDict):
        old_text: str
        new_text: str
    tree = ast.parse(Path(path).read_text(encoding="utf-8-sig"))
    nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in {
        "_normalize_replacements", "_text_not_found_message", "tool_replace_in_file"}]
    for node in nodes:
        node.decorator_list = []
    namespace = {"Path": Path, "json": json, "ReplacementSpec": ReplacementSpec}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), path, "exec"), namespace)
    return namespace["tool_replace_in_file"]


def measure_once(fn, path, specs, gui=False):
    start = time.perf_counter()
    before = path.read_text(encoding="utf-8") if gui and fn is not tool_replace_in_file else None
    result = fn(str(path), replacements=specs)
    if fn is tool_replace_in_file:
        if result.is_error:
            raise RuntimeError(str(result))
        payload = json.dumps(result.display, ensure_ascii=False) if gui else ""
    elif gui:
        after = path.read_text(encoding="utf-8")
        list(difflib.unified_diff(before.splitlines(keepends=True), after.splitlines(keepends=True), n=0))
        payload = json.dumps({"path": str(path), "old_content": before, "new_content": after})
    else:
        payload = ""
    return (time.perf_counter() - start) * 1000, len(payload.encode())


def measure_pair(old, root, text, count, rounds, gui, noop):
    paths = {name: root / f"{name}.txt" for name in ("before", "after")}
    samples = {name: [] for name in paths}
    payloads = {name: [] for name in paths}
    for path in paths.values():
        path.write_bytes(text.encode())
    with file_edit_scope(FileEditSession(), root):
        tool_read_file(str(paths["after"]), limit=1)
        for index in range(rounds + 4):
            a, b = ("A", "B") if index % 2 == 0 else ("B", "A")
            if noop:
                a = b = "A"
            specs = [{"old_text": f"item{i * 997:06d}={a}", "new_text": f"item{i * 997:06d}={b}"} for i in range(count)]
            # Alternate order each pair to reduce load/cache ordering bias.
            order = [("before", old), ("after", tool_replace_in_file)]
            if index % 2:
                order.reverse()
            for name, fn in order:
                elapsed, size = measure_once(fn, paths[name], specs, gui)
                if index >= 4:
                    samples[name].append(elapsed)
                    payloads[name].append(size)
    return {name: {"median_ms": round(statistics.median(values), 3),
                   "min_ms": round(min(values), 3), "payload_bytes": round(statistics.median(payloads[name]))}
            for name, values in samples.items()}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline-source")
    parser.add_argument("--rounds", type=int, default=15)
    parser.add_argument("--output")
    args = parser.parse_args()
    old = load_baseline(args.baseline_source) if args.baseline_source else baseline
    results = []
    with tempfile.TemporaryDirectory(prefix="fh-edit-bench-") as temp:
        for lines in (1000, 12000, 60000):
            text = "".join(f"item{i:06d}=A " + "x" * 70 + "\r\n" for i in range(lines))
            for scenario, count, gui, noop in (("batch", min(12, 1 + (lines-1)//997), False, False),
                                                 ("gui_single_edit", 1, True, False), ("noop", 1, False, True)):
                row = {"scenario": scenario, "file_bytes": len(text.encode()), "edits": count}
                row.update(measure_pair(old, Path(temp), text, count, args.rounds, gui, noop))
                row["speedup"] = round(row["before"]["median_ms"] / row["after"]["median_ms"], 2)
                results.append(row)
                print(json.dumps(row), flush=True)
    if args.output:
        Path(args.output).write_text(json.dumps({"rounds": args.rounds, "results": results}, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
