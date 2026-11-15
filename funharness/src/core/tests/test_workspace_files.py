from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from funharness.src.core.workspace_files import WorkspaceFiles


class WorkspaceFilesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.files = WorkspaceFiles(self.root)
        self.addCleanup(self.files.invalidate)

    def write(self, path):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text('fixture', encoding='utf-8')

    def complete_search(self, query, **kwargs):
        for _ in range(1000):
            result = self.files.search(query, **kwargs)
            if not result['indexing']:
                return result
        self.fail('Index failed to complete')

    def test_pages_are_sorted_and_include_all_entries_once(self):
        for name in ['z.txt', 'a.txt', 'B.txt', 'folder/z.txt', '.hidden', '.funharness/data']:
            self.write(name)
        first = self.files.list_directory(limit=2)
        second = self.files.list_directory(offset=first['next_offset'], limit=2)
        third = self.files.list_directory(offset=second['next_offset'], limit=2)
        self.assertEqual([e['name'] for page in [first, second, third] for e in page['entries']],
                         ['.funharness', 'folder', 'a.txt', 'B.txt', 'z.txt'])
        self.assertIsNone(third['next_offset'])
        self.assertEqual(first['total'], 5)

    def test_recursive_search_distinguishes_same_names_and_normalizes_paths(self):
        self.write('app/src/index.ts')
        self.write('tools/deep/src/index.ts')
        self.write('资料/笔记.md')
        result = self.complete_search('INDEX.TS')
        self.assertEqual([e['path'] for e in result['entries']], ['app/src/index.ts', 'tools/deep/src/index.ts'])
        self.assertEqual(self.complete_search('tools\\deep\\src\\index')['entries'][0]['path'], 'tools/deep/src/index.ts')
        self.assertEqual(self.complete_search('./app/src/index')['entries'][0]['path'], 'app/src/index.ts')
        self.assertEqual(self.complete_search('笔记')['entries'][0]['path'], '资料/笔记.md')

    def test_generated_trees_are_opt_in_and_hidden_source_files_searchable(self):
        self.write('project/node_modules/pkg/index.js')
        self.write('project/.config/settings.json')
        self.assertEqual(self.complete_search('index.js')['entries'], [])
        result = self.complete_search('project/node_modules/pkg/index')
        self.assertEqual(result['entries'][0]['path'], 'project/node_modules/pkg/index.js')
        self.assertEqual(self.complete_search('settings.json')['entries'][0]['path'], 'project/.config/settings.json')

    def test_search_resumes_and_reuses_index_across_keystrokes(self):
        for i in range(12):
            self.write(f'p{i}/nested/target.txt')
        self.files.search_slice_seconds = 0
        first = self.files.search('tar')
        self.assertTrue(first['indexing'])
        complete = self.complete_search('target')
        self.assertEqual(len(complete['entries']), 12)
        with patch('funharness.src.core.workspace_files.os.scandir', side_effect=AssertionError('rescanned')):
            self.assertEqual(len(self.files.search('target.txt')['entries']), 12)

    def test_limits_and_invalidation_are_explicit(self):
        for i in range(10):
            self.write(f'{i}.txt')
        self.assertTrue(self.complete_search('.txt', limit=3)['has_more'])
        self.write('new.txt')
        self.files.invalidate()
        self.assertEqual(self.complete_search('new.txt')['entries'][0]['name'], 'new.txt')
        self.files.invalidate()
        self.files.max_index_entries = 2
        result = self.complete_search('')
        self.assertTrue(result['truncated'])

    def test_path_escape_rejected(self):
        with self.assertRaises(ValueError):
            self.files.list_directory('../')
        with self.assertRaises(ValueError):
            self.files.search('../private/secret')

    def test_symlink_cycles_and_outside_targets_are_not_followed(self):
        self.write('dir/target.txt')
        try:
            (self.root / 'dir' / 'loop').symlink_to(self.root, target_is_directory=True)
        except OSError:
            self.skipTest('Symlink creation unavailable')
        result = self.complete_search('target')
        self.assertEqual(len(result['entries']), 1)


if __name__ == '__main__':
    unittest.main()
