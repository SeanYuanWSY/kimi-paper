"""Exercise the macOS service watcher with real file changes."""
import asyncio
import os
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'Resources'))
import paper_service  # installs the application's platform adapter
from tex_mcp_web.watcher import Watcher


@unittest.skipUnless(sys.platform == 'darwin', 'macOS watcher compatibility')
class WatcherStartupTests(unittest.IsolatedAsyncioTestCase):
    async def test_start_nested_file_event_and_stop(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            (root / 'sections').mkdir()
            source = root / 'sections' / 'intro.tex'
            source.write_text('before')
            changed = asyncio.Event()
            async def on_change(path):
                if Path(path) == source:
                    changed.set()
            watcher = Watcher(root, ['*.tex', '*.bib'], [], on_change)
            watcher.start(asyncio.get_running_loop())
            try:
                self.assertTrue(watcher.is_running)
                source.write_text('after: detect this nested source change')
                await asyncio.wait_for(changed.wait(), 5)
                changed.clear()
                replacement = root / 'sections' / 'replacement.tmp'
                replacement.write_text('atomic save')
                os.replace(replacement, source)
                await asyncio.wait_for(changed.wait(), 5)
                changed.clear()
                source = root / 'sections' / 'references.bib'
                source.write_text('@article{sample,title={Fixture}}')
                await asyncio.wait_for(changed.wait(), 5)
            finally:
                watcher.stop()
            self.assertFalse(watcher.is_running)
