import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'Resources'))
from paper_tasks import source_files, merged_files, candidate_diff, TaskError, StatusFingerprint, fingerprint


class ProjectAssetsTests(unittest.TestCase):
    def test_complete_assets_and_exclusions_without_reading(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            expected = {'script.py': b'print(1)', 'main.tex': b'paper', 'refs/papers.bib': b'refs', 'figures/plot.png': b'\x89PNG',
                        'tables/results.xlsx': b'PK\x00', 'README.md': b'notes', 'data/results.json': b'{}',
                        'data/results.yaml': b'a: 1', 'supplement.docx': b'PK\x00', 'slides.pptx': b'PK\x00'}
            omitted = ['credentials.json', 'api_key.yaml', 'config.yaml', 'settings.json', '.env',
                       'cache/results.json', 'AGENTS.md', '.git/config', 'program.exe']
            for name, content in {**expected, **dict.fromkeys(omitted, b'fictional')}.items():
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)
            original = os.open
            def guarded(path, *args, **kwargs):
                self.assertNotIn(str(Path(path).relative_to(root)), omitted)
                return original(path, *args, **kwargs)
            report = []
            with patch('paper_tasks.os.open', side_effect=guarded):
                self.assertEqual(source_files(root, report), expected)
            self.assertTrue({'credentials.json', 'cache', '.git', 'program.exe'} <= {item['path'] for item in report})
            self.assertEqual(StatusFingerprint().get(root, 'none'), fingerprint(expected))

    def test_binary_add_replace_delete_only_against_unchanged_baseline(self):
        for base, proposed, expected in [({}, {'f.png': b'new'}, {'f.png': b'new'}),
                                         ({'f.png': b'old'}, {'f.png': b'new'}, {'f.png': b'new'}),
                                         ({'f.png': b'old'}, {}, {})]:
            with self.subTest(base=base, proposed=proposed):
                result, changes = merged_files(base, proposed, base)
                self.assertEqual(result, expected)
                self.assertEqual(changes, ['f.png'])
                with self.assertRaises(TaskError):
                    merged_files(base, proposed, {'f.png': b'collaborator'})

    def test_binary_disguised_as_text_does_not_three_way_merge(self):
        with self.assertRaises(TaskError):
            merged_files({'x.txt': b'\x00old'}, {'x.txt': b'\x00new'}, {'x.txt': b'\x00other'})
        self.assertTrue(candidate_diff({'x.txt': b'\xff'}, {'x.txt': b'\xfe'})[0]['binary'])

    def test_binary_diff_is_reviewable_and_unchanged_assets_preserved(self):
        result, _ = merged_files({'x.tex': b'old', 'f.png': b'base'},
                                  {'x.tex': b'new', 'f.png': b'base'},
                                  {'x.tex': b'old', 'f.png': b'external'})
        self.assertEqual(result['f.png'], b'external')
        diff = candidate_diff({'f.png': b'old'}, {'f.png': b'new'})[0]
        self.assertIn('SHA-256', diff['after'])
        self.assertNotEqual(diff['before'], diff['after'])

    def test_sensitive_candidate_rejected_and_symlink_not_copied(self):
        for name in ('credentials.json', '../escape.tex', '/tmp/escape.tex'):
            with self.subTest(name=name), self.assertRaises(TaskError):
                merged_files({}, {name: b'fictional'}, {})
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'figure.png').symlink_to(root / 'missing')
            with self.assertRaises(TaskError):
                source_files(root)


if __name__ == '__main__':
    unittest.main()
