import asyncio
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'Resources'))
from paper_engine import Engine


class BatchQueueTests(unittest.IsolatedAsyncioTestCase):
    async def test_saved_annotations_wait_and_cancelled_middle_cannot_break_serial_order(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paper = root / 'paper'; paper.mkdir()
            (paper / 'main.tex').write_text('original')
            engine = Engine(paper, 'main.tex', root / 'state')
            engine.models = [{'tool': 'kimi', 'model': 'test'}]
            hold = asyncio.Event()
            entered = asyncio.Event()
            prompts = []

            async def connect(tool, run, event, permission):
                class Stub:
                    async def request(self, method, params, **kwargs):
                        if method == 'session/new':
                            return {'sessionId': run.name}
                        if method == 'session/prompt':
                            prompts.append(params['prompt'][0]['text'])
                            entered.set()
                            await hold.wait()
                            text = json.dumps({'edits':[{'file':'main.tex','old':'original','new':'revised'}],
                                               'explanation':'Keep shared terminology.'})
                            await event('session/update', {'update':{'sessionUpdate':'agent_message_chunk',
                                                                    'content':{'text':text}}})
                        return {}
                    async def close(self): pass
                return Stub()
            engine.rpc = connect
            try:
                ids = []
                for index in range(3):
                    ids += await engine.submit({'id':str(index),'text':f'Annotation {index}'},
                                               [{'tool':'kimi','model':'test'}], f'draft-submission-{index}', deferred=True)
                self.assertEqual(engine.jobs, {})
                self.assertTrue(all(t['status']=='draft' for t in engine.store.all()))
                await engine.start_batch()
                await asyncio.wait_for(entered.wait(), 1)
                await engine.cancel(ids[1])
                await asyncio.sleep(.02)
                self.assertEqual(len(prompts), 1)
                self.assertEqual(engine.store.get(ids[2])['status'], 'queued')
                hold.set()
                await asyncio.gather(*list(engine.jobs.values()))
                self.assertEqual(len(prompts), 2)
                self.assertIn('Annotation 2', prompts[0])
                self.assertIn('Keep shared terminology.', prompts[1])
                self.assertEqual(engine.store.get(ids[0])['status'], 'ready')
                self.assertEqual(engine.store.get(ids[1])['status'], 'cancelled')
                self.assertEqual(engine.store.get(ids[2])['status'], 'ready')
                self.assertEqual((paper/'main.tex').read_text(), 'original')
            finally:
                await engine.close()
