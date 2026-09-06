import asyncio
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'Resources'))
from paper_service import PaperService


class Request:
    async def json(self):
        return {'token': 'test-token'}


class DirectGitCoordinationTests(unittest.IsolatedAsyncioTestCase):
    async def test_pull_stops_native_kimi_before_worktree_change_and_restores_session(self):
        service = object.__new__(PaperService)
        service.git_active = False
        service.paper_git = SimpleNamespace(
            previews={'test-token': {'action': 'pull'}},
            execute=AsyncMock(return_value='已拉取'),
        )
        service.studio = SimpleNamespace(
            direct=True, busy=AsyncMock(return_value=False), close=AsyncMock(),
            start=AsyncMock(), accept_external_baseline=Mock(), error=None,
        )
        service.engine = SimpleNamespace(apply_lock=asyncio.Lock(), require_recovered=Mock())
        service.do_compile = AsyncMock(return_value=SimpleNamespace(success=True))

        await PaperService.git_execute(service, Request())

        service.studio.close.assert_awaited_once()
        service.paper_git.execute.assert_awaited_once_with('test-token')
        service.studio.accept_external_baseline.assert_called_once()
        service.do_compile.assert_awaited_once()
        service.studio.start.assert_awaited_once()
        self.assertFalse(service.git_active)

    async def test_new_branch_uses_same_stop_and_new_baseline_boundary(self):
        service = object.__new__(PaperService)
        service.git_active = False
        service.paper_git = SimpleNamespace(
            previews={'test-token': {'action': 'branch'}},
            execute=AsyncMock(return_value='已新建分支'),
        )
        service.studio = SimpleNamespace(
            direct=True, busy=AsyncMock(return_value=False), close=AsyncMock(),
            start=AsyncMock(), accept_external_baseline=Mock(), error=None,
        )
        service.engine = SimpleNamespace(apply_lock=asyncio.Lock(), require_recovered=Mock())
        service.do_compile = AsyncMock(return_value=SimpleNamespace(success=False))

        response = await PaperService.git_execute(service, Request())

        service.studio.close.assert_awaited_once()
        service.studio.accept_external_baseline.assert_called_once()
        service.studio.start.assert_awaited_once()
        self.assertIn('Git 已更新', json.loads(response.text)['message'])


if __name__ == '__main__':
    unittest.main()
