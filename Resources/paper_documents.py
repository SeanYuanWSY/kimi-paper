"""Git/settings service for a document folder; no LaTeX engine or project writes."""
import asyncio
from pathlib import Path
import secrets
from aiohttp import web
from paper_git import PaperGit
from paper_tasks import TaskError


class DocumentService:
    def __init__(self, root, control_token):
        self.root = Path(root)
        self.token = secrets.token_urlsafe(32)
        self.control_token = control_token
        self.port = 0
        self.tickets = {}
        self.git_lock = asyncio.Lock()
        self.paper_git = PaperGit(self.root)
        self._runner = None

        @web.middleware
        async def boundary(request, handler):
            if request.headers.get('Origin') not in (None, f'http://127.0.0.1:{self.port}'):
                raise web.HTTPForbidden()
            if request.path.startswith('/kp/') or request.method not in {'GET', 'HEAD'}:
                if not secrets.compare_digest(request.headers.get('Authorization', '').removeprefix('Bearer '), self.token):
                    raise web.HTTPUnauthorized()
            try:
                return await handler(request)
            except TaskError as error:
                return web.json_response({'error': str(error)}, status=409)
            except (ValueError, KeyError, TypeError):
                return web.json_response({'error': '请求内容无效。'}, status=400)

        self.app = web.Application(middlewares=[boundary], client_max_size=1024 * 1024)
        self.app.router.add_get('/paper', self.identity)
        self.app.router.add_get('/studio-panel', self.panel)
        self.app.router.add_get('/kp/git', self.git_status)
        self.app.router.add_post('/kp/git/preview', self.git_preview)
        self.app.router.add_post('/kp/git/execute', self.git_execute)
        self.app.router.add_post('/kp/maintenance', self.maintenance)
        self.app.router.add_post('/kp/recompile', self.recompile)

    async def identity(self, request):
        return web.json_response({'watch_dir': str(self.root), 'documents_only': True})

    async def panel(self, request):
        return web.Response(text=Path(__file__).with_name('paper_studio_panel.html').read_text(), content_type='text/html')

    async def git_status(self, request):
        return web.json_response(await self.paper_git.status())

    async def git_preview(self, request):
        body = await request.json()
        return web.json_response(await self.paper_git.prepare(body['action'], body))

    async def maintenance(self, request):
        if not self.control_token or not secrets.compare_digest(request.headers.get('X-Kimi-Paper-Control', ''), self.control_token):
            raise TaskError('版本操作协调失败，请重新连接。')
        now = asyncio.get_running_loop().time()
        self.tickets = {k: v for k, v in self.tickets.items() if v > now}
        ticket = secrets.token_urlsafe(24)
        self.tickets[ticket] = now + 30
        return web.json_response({'ticket': ticket})

    async def git_execute(self, request):
        body = await request.json()
        if self.tickets.pop(str(body.get('maintenance', '')), 0) < asyncio.get_running_loop().time():
            raise TaskError('Git 操作没有获得当前维护锁，请重新检查后执行。')
        if self.git_lock.locked():
            raise TaskError('另一项 Git 操作正在进行。')
        async with self.git_lock:
            return web.json_response({'message': await self.paper_git.execute(body['token'])})

    async def recompile(self, request):
        raise TaskError('当前是文档文件夹，请先打开 LaTeX 主文件再编译。')

    async def setup(self, port):
        self._runner = web.AppRunner(self.app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, '127.0.0.1', port)
        await site.start()
        self.port = site._server.sockets[0].getsockname()[1]
        print(f'Kimi Paper service: http://127.0.0.1:{self.port}/#token={self.token}', flush=True)

    async def cleanup(self):
        if self._runner:
            await self._runner.cleanup()
