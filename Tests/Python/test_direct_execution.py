import asyncio
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'Resources'))
from paper_engine import Engine
from paper_tasks import TaskError


class DirectExecutionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.paper = self.root / 'paper'
        self.paper.mkdir()
        (self.paper / 'main.tex').write_text('original')
        self.engine = Engine(self.paper, 'main.tex', self.root / 'state')
        self.engine.models = [{'tool': 'kimi', 'model': 'test'}]

    async def asyncTearDown(self):
        await self.engine.close()
        self.temp.cleanup()

    async def test_proposal_ready_without_compile_but_failed_apply_does_not_write(self):
        async def connect(tool, run, event, permission):
            self.assertIn('tools: []', (run / 'proposal-agent.md').read_text())
            class Stub:
                async def request(stub, method, params, **kwargs):
                    if method == 'session/new':
                        return {'sessionId': 'test'}
                    if method == 'session/prompt':
                        await event('session/update', {'update': {'sessionUpdate': 'agent_message_chunk',
                            'content': {'text': json.dumps({'edits': [{'file': 'main.tex', 'old': 'original',
                                                                     'new': 'revised'}], 'explanation': 'A clearer sentence.'})}}})
                    return {}
                async def close(stub):
                    pass
            return Stub()
        self.engine.rpc = connect
        compiler = AsyncMock(return_value={'success': False})
        with patch('paper_engine.compile_copy', compiler):
            ids = await self.engine.submit({'id': 'c', 'text': 'improve'},
                                           [{'tool': 'kimi', 'model': 'test'}], 'direct-submission')
            await asyncio.gather(*list(self.engine.jobs.values()))
            task = self.engine.store.get(ids[0])
            self.assertEqual(task['status'], 'ready')
            self.assertLessEqual(task['started'], task['firstOutput'])
            self.assertLessEqual(task['firstOutput'], task['readyAt'])
            compiler.assert_not_awaited()
            self.assertEqual((self.paper / 'main.tex').read_text(), 'original')
            with self.assertRaises(TaskError):
                await self.engine.apply(ids[0])
            compiler.assert_awaited_once()
            self.assertEqual((self.paper / 'main.tex').read_text(), 'original')

    async def test_direct_mode_rejects_permission_without_pending_prompt(self):
        self.engine.store.put({'id': 'a' * 32, 'processing': 'direct', 'status': 'running'}, 'test')
        with self.assertRaises(TaskError):
            await self.engine.permission('a' * 32, 'session/request_permission', {'options': []})
        self.assertEqual(self.engine.approvals, {})

    async def test_legacy_codex_catalog_entry_cannot_authorize_new_submission(self):
        self.engine.models.append({'tool': 'codex', 'model': 'test'})
        with self.assertRaises(TaskError):
            await self.engine.submit({'id': 'c'}, [{'tool': 'codex', 'model': 'test'}], 'rejected-submission')
        self.assertEqual(self.engine.store.all(), [])
