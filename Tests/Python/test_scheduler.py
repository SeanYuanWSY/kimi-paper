import asyncio
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'Resources'))
from paper_engine import Engine
from paper_tasks import StatusFingerprint, fingerprint


class SchedulerTests(unittest.IsolatedAsyncioTestCase):
    async def test_batch_dedup_parallel_limit_and_cancellation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paper = root / 'paper'; paper.mkdir()
            (paper / 'main.tex').write_text('original')
            engine = Engine(paper, 'main.tex', root / 'state')
            engine.models = [{'tool': 'kimi', 'model': 'test'}]
            started = []
            hold = asyncio.Event()

            class StubRPC:
                async def request(self, method, params, **kwargs):
                    if method == 'session/new':
                        return {'sessionId': 'test-' + str(len(started))}
                    if method == 'session/prompt':
                        started.append(params['sessionId'])
                        await hold.wait()
                    return {}

                async def close(self):
                    pass

            async def connect(*args):
                return StubRPC()
            engine.rpc = connect
            try:
                choices = [{'tool': 'kimi', 'model': 'test'}] * 4
                ids = await engine.submit({'id': 'comment', 'text': 'fictional'}, choices, 'stable-submission')
                duplicate = await engine.submit({'id': 'comment', 'text': 'fictional'}, choices, 'stable-submission')
                self.assertEqual(ids, duplicate)
                await asyncio.sleep(.05)
                self.assertEqual(len(started), 3)
                self.assertEqual(len(engine.store.all()), 4)
                self.assertEqual(engine.store.get(ids[3])['status'], 'queued')
                self.assertEqual((paper / 'main.tex').read_text(), 'original')
                await engine.cancel(ids[0])
                await asyncio.sleep(.05)
                self.assertEqual(engine.store.get(ids[0])['status'], 'cancelled')
                self.assertEqual(engine.store.get(ids[3])['status'], 'running')
                self.assertEqual(len(started), 4)
            finally:
                await engine.close()


class StatusCacheTests(unittest.TestCase):
    def test_status_hash_matches_authoritative_bytes_after_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            file = root / 'main.tex'; file.write_bytes(b'old')
            (root / 'main.pdf').write_bytes(b'compiled output')
            cache = StatusFingerprint()
            self.assertEqual(cache.get(root, 'main.pdf'), fingerprint({'main.tex': b'old'}))
            file.write_bytes(b'new')
            self.assertEqual(cache.get(root, 'main.pdf'), fingerprint({'main.tex': b'new'}))
            file.unlink()
            self.assertEqual(cache.get(root, 'main.pdf'), fingerprint({}))
