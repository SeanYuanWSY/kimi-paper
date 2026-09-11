import asyncio
import contextlib
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock
from aiohttp.test_utils import TestClient, TestServer

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'Resources'))
from paper_documents import DocumentService


class DocumentServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / 'README.md').write_text('# Fictional document')
        self.service = DocumentService(self.root, 'fictional-control')
        self.client = TestClient(TestServer(self.service.app, host='127.0.0.1'))
        await self.client.start_server()
        self.service.port = self.client.port
        self.auth = {'Authorization': 'Bearer ' + self.service.token}

    async def asyncTearDown(self):
        await self.client.close()
        self.temp.cleanup()

    async def test_document_folder_has_no_tex_initialization_or_project_writes(self):
        response = await self.client.get('/paper')
        self.assertEqual((await response.json())['watch_dir'], str(self.root))
        self.assertFalse(hasattr(self.service, 'engine'))
        self.assertFalse(hasattr(self.service, 'watcher'))
        self.assertEqual([p.name for p in self.root.iterdir()], ['README.md'])
        response = await self.client.post('/kp/recompile', headers=self.auth)
        self.assertEqual(response.status, 409)
        self.assertIn('LaTeX', (await response.json())['error'])

    async def test_git_is_scoped_and_read_only_until_reviewed_execution(self):
        response = await self.client.get('/kp/git', headers=self.auth)
        self.assertEqual(response.status, 200)
        self.assertTrue((await response.json())['canInit'])
        self.assertFalse((self.root / '.git').exists())
        response = await self.client.post('/kp/git/execute', json={'token': 'fake'}, headers=self.auth)
        self.assertEqual(response.status, 409)

    async def test_auth_origin_and_single_use_maintenance_ticket(self):
        self.assertEqual((await self.client.get('/kp/git')).status, 401)
        response = await self.client.get('/kp/git', headers={**self.auth, 'Origin': 'https://example.org'})
        self.assertEqual(response.status, 403)
        response = await self.client.post('/kp/maintenance', headers=self.auth)
        self.assertEqual(response.status, 409)
        response = await self.client.post('/kp/maintenance', headers={**self.auth, 'X-Kimi-Paper-Control': 'fictional-control'})
        ticket = (await response.json())['ticket']
        self.service.paper_git.execute = AsyncMock(return_value='fictional success')
        body = {'token': 'reviewed', 'maintenance': ticket}
        response = await self.client.post('/kp/git/execute', json=body, headers=self.auth)
        self.assertEqual(response.status, 200)
        response = await self.client.post('/kp/git/execute', json=body, headers=self.auth)
        self.assertEqual(response.status, 409)
        self.service.paper_git.execute.assert_awaited_once()

    async def test_real_lifecycle_binds_without_latex(self):
        service = DocumentService(self.root, 'fictional-control')
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                await service.setup(0)
            self.assertGreater(service.port, 0)
            self.assertEqual([p.name for p in self.root.iterdir()], ['README.md'])
        finally:
            await service.cleanup()


if __name__ == '__main__':
    unittest.main()
