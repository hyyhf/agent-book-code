"""Compare original Path/stat listing with paged scandir on a disposable tree."""
from pathlib import Path
import json
import tempfile
import time

from funharness.src.core.workspace_files import WorkspaceFiles


def original(root):
    entries = []
    for child in sorted(root.iterdir(), key=lambda p: (p.is_file(), p.name.lower())):
        stat = child.stat()
        entries.append({'name': child.name, 'path': str(child.relative_to(root)),
                        'kind': 'directory' if child.is_dir() else 'file',
                        'size': stat.st_size, 'modified_at': stat.st_mtime})
    return entries


def measure(fn):
    start = time.perf_counter()
    value = fn()
    return round((time.perf_counter() - start) * 1000, 2), len(json.dumps(value).encode())


if __name__ == '__main__':
    with tempfile.TemporaryDirectory(prefix='fh-workspace-bench-') as directory:
        root = Path(directory)
        for i in range(5000):
            (root / f'file-{i:05d}.txt').touch()
        files = WorkspaceFiles(root)
        before = measure(lambda: original(root))
        after = measure(lambda: files.list_directory(limit=200))
        cached = measure(lambda: files.list_directory(offset=200, limit=200))
        print(json.dumps({'files': 5000, 'original_ms_bytes': before,
                          'first_page_ms_bytes': after, 'next_page_ms_bytes': cached}))
        files.invalidate()
