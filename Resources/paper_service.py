"""Kimi Paper integration layer over the pinned, unmodified PDF viewer package."""
import asyncio
import base64
from datetime import datetime, timezone
import gzip
import json
import math
import os
from pathlib import Path
import secrets
import signal
import sys
import uuid

from aiohttp import web
from tex_mcp_web.config import load_config
from tex_mcp_web.server import TexMcpWebServer, STATIC_DIR
from tex_mcp_web.compiler import CompileResult, CompileMessage
from paper_tasks import TaskError, digest, materialize, fingerprint, StatusFingerprint, candidate_diff
from paper_engine import Engine, paper_files, compile_copy
from paper_reading import ReadingError, visible_paragraphs
from paper_git import PaperGit
from paper_studio import Studio

RESOURCES = Path(__file__).resolve().parent


class PaperService(TexMcpWebServer):
    def __init__(self, config):
        self.token = secrets.token_urlsafe(32)
        self.engine = None
        self.studio = None
        super().__init__(config)
        identity = digest(str(self.main_file.resolve()).encode())[:24]
        state = Path.home() / 'Library/Application Support/Kimi Paper/projects' / identity
        self.engine = Engine(self.watch_dir, str(self.main_file.relative_to(self.watch_dir)), state,
                             config.compiler, self.applied, self.prepare_effects, self.recover_effects)
        self.studio = Studio(self) if os.environ.get("KIMI_PAPER_STUDIO") == "1" else None
        self.catalog_task = None
        self.focused_task = None
        self.paper_git = PaperGit(self.watch_dir)
        self.git_active = False
        self.status_fingerprint = StatusFingerprint()
        # The upstream server refreshes anchors/SyncTeX after this guarded compiler returns.
        import tex_mcp_web.server as upstream
        upstream.compile_tex = self.guarded_compile

    async def guarded_compile(self, main_file, compiler, work_dir):
        files = await asyncio.to_thread(paper_files, self.watch_dir, self.engine.main)
        folder = self.engine.store.root / 'builds' / uuid.uuid4().hex
        materialize(folder, files)
        data = await compile_copy(folder, self.engine.main, compiler, self.watch_dir)
        errors = [CompileMessage(**e) for e in data['errors']]
        warnings = [CompileMessage(**e) for e in data['warnings']]
        output = None
        if data['success']:
            if fingerprint(files) != fingerprint(paper_files(self.watch_dir, self.engine.main)):
                return CompileResult(success=False, errors=[CompileMessage(self.engine.main, None, '正文在编译期间变化，请重新编译。', 'error')])
            for suffix in ('.pdf', '.synctex.gz'):
                source = folder / Path(self.engine.main).with_suffix(suffix)
                target = self.main_file.with_suffix(suffix)
                if not source.is_file() or source.is_symlink() or target.is_symlink():
                    if suffix == '.pdf':
                        return CompileResult(success=False, errors=[CompileMessage(self.engine.main, None, '编译没有生成安全可用的 PDF，正文未发布。', 'error')])
                    continue
                content = source.read_bytes()
                if suffix == '.synctex.gz':
                    content = gzip.compress(gzip.decompress(content).replace(str(folder).encode(), str(self.watch_dir).encode()))
                temp = target.with_name('.kimi-paper-build-' + uuid.uuid4().hex)
                temp.write_bytes(content)
                os.replace(temp, target)
            output = self.main_file.with_suffix('.pdf')
        return CompileResult(success=data['success'] and output is not None, errors=errors, warnings=warnings, output_file=output)

    def _build_app(self):
        app = super()._build_app()

        @web.middleware
        async def integration(request, handler):
            try:
                origin = request.headers.get('Origin')
                if origin and origin != f'http://127.0.0.1:{self.config.port}':
                    raise web.HTTPForbidden(text='Unexpected origin')
                if request.path.startswith('/kp/') or request.method not in {'GET', 'HEAD'}:
                    supplied = request.headers.get('Authorization', '').removeprefix('Bearer ')
                    if not secrets.compare_digest(supplied, self.token):
                        raise web.HTTPUnauthorized(text='Authentication required')
                if request.path == '/static/viewer.js':
                    source = (STATIC_DIR / 'viewer.js').read_text()
                    extra = '\n'+(RESOURCES/'paper_studio_viewer.js').read_text() if self.studio else ''
                    return web.Response(text=('window.KP_STUDIO=true;\n' if self.studio else '')+source + '\n' + (RESOURCES / 'paper_viewer.js').read_text()+extra, content_type='text/javascript')
                return await handler(request)
            except TaskError as error:
                return web.json_response({'error': str(error)}, status=409)
            except (ValueError, KeyError, TypeError):
                return web.json_response({'error': '请求内容无效，请刷新后重试。'}, status=400)

        app.middlewares.append(integration)
        app.router.add_get('/workbench', self.workbench)
        app.router.add_route('*','/kp/studio/{action}',self.studio_action)
        app.router.add_get('/studio-panel',self.studio_panel)
        app.router.add_get('/kp/models', self.models)
        app.router.add_post('/kp/models/refresh', self.refresh_models)
        app.router.add_get('/kp/tasks', self.tasks)
        app.router.add_post('/kp/batch', self.batch)
        app.router.add_post('/kp/focus', self.focus)
        app.router.add_get('/kp/versions', self.versions)
        app.router.add_get('/kp/versions/{id}', self.version_detail)
        app.router.add_get('/kp/git', self.git_status)
        app.router.add_post('/kp/git/preview', self.git_preview)
        app.router.add_post('/kp/git/execute', self.git_execute)
        app.router.add_post('/kp/tasks/{id}/{action}', self.action)
        app.router.add_post('/kp/permission/{id}', self.permission)
        app.router.add_post('/kp/visible', self.visible)
        app.router.add_get('/kp/preferences', self.preferences)
        app.router.add_put('/kp/preferences', self.preferences)
        return app

    async def studio_action(self, request):
        if not self.studio: raise TaskError('工作台未启用。')
        return await self.studio.handle(request)

    async def studio_panel(self, request):
        return web.Response(text=(RESOURCES/'paper_studio_panel.html').read_text(),content_type='text/html')

    async def _handle_pdf(self, request):
        pdf=self.studio.preview_pdf() if self.studio else None
        if pdf:return web.FileResponse(pdf,headers={'Cache-Control':'no-store'})
        return await super()._handle_pdf(request)

    async def _handle_paper(self, request):
        response=await super()._handle_paper(request)
        if self.studio and self.studio.preview_pdf():
            data=json.loads(response.body)
            data['pdf_digest']=self.studio.manifest['preview']
            data['last_compile']={'success':True,'errors':[],'warnings':[]}
            return web.json_response(data)
        return response

    async def workbench(self, request):
        return web.Response(text=(RESOURCES / 'workbench.html').read_text(), content_type='text/html')

    async def models(self, request):
        return web.json_response({'models': self.engine.models, 'errors': self.engine.catalog_errors,
                                  'loading': self.catalog_task is not None and not self.catalog_task.done()})

    async def refresh_models(self, request):
        if self.catalog_task is None or self.catalog_task.done():
            self.catalog_task = asyncio.create_task(self.engine.catalog())
        return web.json_response({'ok': True})

    async def preferences(self, request):
        if request.method == 'PUT':
            data = await request.json()
            self.engine.store.set_setting('selections', data.get('selections', [])[:6])
            self.engine.store.set_setting('selection_schema', 2)
        choices = self.engine.store.setting('selections', []) if self.engine.store.setting('selection_schema', 0) == 2 else []
        return web.json_response({'selections': choices})

    async def tasks(self, request):
        await self.sync_comment_revisions()
        rows = self.engine.store.all()
        # Cache unchanged image dependencies during frequent UI polling. Adoption still rereads all bytes.
        current = await asyncio.to_thread(self.status_fingerprint.get, self.watch_dir,
                                          str(Path(self.engine.main).with_suffix('.pdf')))
        for row in rows:
            row['baselineChanged'] = row['baseline'] != current
        return web.json_response({'tasks': rows, 'focus': self.focused_task, 'gitBusy': self.git_active,
                                  'running': int(self.git_active)+sum(t['status'] in {'queued', 'running', 'waiting', 'applying'} for t in rows)})

    async def focus(self, request):
        body = await request.json()
        task = self.engine.store.get(body['id'])
        self.focused_task = {'id': task['id'], 'revision': uuid.uuid4().hex}
        return web.json_response({'ok': True})

    async def versions(self, request):
        result = []
        for path in self.engine.transactions():
            journal = json.loads(path.read_text())
            if journal['state'] == 'committed' and journal.get('versions'):
                result.append({'id': journal['id'], 'task': journal['task'], 'operation': journal['operation'],
                               'created': journal.get('created', 0), 'files': list(journal['files']),
                               'versions': journal['versions']})
        return web.json_response({'versions': sorted(result, key=lambda v: v['created'], reverse=True)})

    async def git_status(self, request):
        return web.json_response(await self.paper_git.status())

    async def version_detail(self, request):
        identity=request.match_info['id']
        self.engine.run_dir(identity)  # Same strict 32-hex identifier boundary.
        path=self.engine.store.root/'journals'/(identity+'.json')
        if not path.is_file():raise TaskError('本地版本不存在。')
        journal=json.loads(path.read_text())
        if journal['state']!='committed':raise TaskError('此事务尚未完成，不能作为已保存版本展示。')
        before={n:base64.b64decode(v['before']) for n,v in journal['files'].items() if v['before'] is not None}
        after={n:base64.b64decode(v['after']) for n,v in journal['files'].items() if v['after'] is not None}
        return web.json_response({'changes':candidate_diff(before,after)})

    async def git_preview(self, request):
        body=await request.json()
        return web.json_response(await self.paper_git.prepare(body['action'],body))

    async def git_execute(self, request):
        body=await request.json()
        if self.git_active:raise TaskError('另一项 Git 操作正在进行，请等待完成。')
        self.git_active=True
        try:
            async with self.engine.apply_lock:
                self.engine.require_recovered()
                action=self.paper_git.previews.get(body['token'],{}).get('action')
                message=await self.paper_git.execute(body['token'])
                if action in {'pull','switch'}:
                    result=await self.do_compile()
                    if not result.success:
                        message+=' Git 已更新，但论文编译未通过，请检查右侧错误；未自动回退 Git。'
                    if self.studio:message+=' 正式项目已更新；持久草稿已保留，请在修改记录中使用从正式稿更新草稿。'
        finally:self.git_active=False
        return web.json_response({'message':message})

    async def batch(self, request):
        await self.sync_comment_revisions()
        return web.json_response({'task_ids': await self.engine.start_batch()})

    async def _handle_create_comment(self, request):
        data = await request.json()
        settings = data.get('kimi_paper')
        if not settings:
            raise TaskError('请在批注框选择 Agent 后提交任务。')
        submission = settings['submission']
        saved = self.engine.store.setting('submission:' + submission)
        if saved:
            comment = saved
        else:
            # Validate choices before creating a comment; upstream validates PDF anchors.
            allowed = {(v['tool'], v['model']) for v in self.engine.models}
            choices = settings['selections']
            if not choices or len(choices) > 6 or any((v['tool'], v['model']) not in allowed for v in choices):
                raise TaskError('请选择当前可用的 Agent 与模型。')
            response = await super()._handle_create_comment(request)
            if response.status != 201:
                return response
            comment = json.loads(response.body)
            comment['revision'] = self.comment_revision(comment)
            comment['text'] = '\n'.join(v.get('text', '') for v in comment.get('thread', []))
            self.engine.store.set_setting('submission:' + submission, comment)
        ids = await self.engine.submit(comment, settings['selections'], submission, deferred=settings.get('deferred') is True)
        self.engine.store.set_setting('selections', settings['selections'])
        self.engine.store.set_setting('selection_schema', 2)
        return web.json_response({**comment, 'task_ids': ids}, status=201)

    async def action(self, request):
        await self.sync_comment_revisions()
        task_id, action = request.match_info['id'], request.match_info['action']
        if action == 'apply':
            if self.studio:
                async with self.studio.lock:
                    if await self.studio.busy():raise TaskError('请等待 Kimi 完成或停止后，再采纳此冻结版本。')
                    await self.engine.apply(task_id)
                    if self.engine.store.get(task_id).get('studio'):
                        try:self.studio.adopted(task_id)
                        except (OSError,TaskError):raise TaskError('正式稿已采纳并保存版本，但工作台基线更新失败；请重新连接后从正式稿更新草稿。')
            else: await self.engine.apply(task_id)
        elif action == 'undo':
            await self.engine.undo(task_id)
        elif action == 'cancel':
            await self.engine.cancel(task_id)
        elif action == 'retry':
            body = await request.json()
            task = self.engine.store.get(task_id)
            current = self.comments.get(task['comment']['id'])
            if current is None:
                raise TaskError('该批注已删除，请重新添加批注。')
            comment = current.to_dict()
            comment['revision'] = self.comment_revision(comment)
            comment['text'] = '\n'.join(v.get('text', '') for v in comment.get('thread', []))
            if body.get('instruction'):
                comment['text'] += '\n追加要求：' + str(body['instruction'])[:8000]
            await self.engine.submit(comment, [{'tool': 'kimi', 'model': task['model'],
                                               'processing': task.get('processing', 'research')}], uuid.uuid4().hex,
                                     parent=task_id)
        else:
            raise TaskError('无法识别任务操作。')
        return web.json_response({'ok': True})

    def comment_revision(self, comment):
        # Match exact entries recorded in durable app transactions, not a broad author/text filter.
        # Human edits to one of these entries stop matching and therefore invalidate candidates.
        own_entries = []
        engine = getattr(self, 'engine', None)
        if engine:
            for path in engine.transactions():
                effects = json.loads(path.read_text()).get('effects')
                if effects and not effects.get('studio') and effects['comment'] == comment['id']:
                    own_entries.append(effects['after']['thread'][-1])
        thread = [entry for entry in comment.get('thread', []) if entry not in own_entries]
        return digest(json.dumps(thread, sort_keys=True, ensure_ascii=False).encode())

    async def sync_comment_revisions(self):
        for task in self.engine.store.all():
            if task.get('studio') or task['status'] in {'applied', 'undone', 'cancelled'} or task.get('commentChanged'):
                continue
            current = self.comments.get(task['comment']['id'])
            revision = self.comment_revision(current.to_dict()) if current else None
            if revision != task['comment'].get('revision'):
                if task['id'] in self.engine.jobs:
                    await self.engine.cancel(task['id'])
                self.engine.update(task['id'], commentChanged=True, message='批注已编辑或删除，请按最新意见重新生成候选。')

    async def _handle_edit_comment_entry(self, request):
        response = await super()._handle_edit_comment_entry(request)
        await self.sync_comment_revisions()
        return response

    async def permission(self, request):
        body = await request.json()
        self.engine.approve(request.match_info['id'], body['option'])
        return web.json_response({'ok': True})

    @staticmethod
    def effect_state(comment):
        # Anchor refreshes are independent of the human thread and must be preserved.
        return {key: comment.get(key) for key in ('thread', 'status')}

    def prepare_effects(self, task, undo, transaction_id):
        if task.get('studio'):return {'studio':True}
        current = self.comments.get(task['comment']['id'])
        if current is None:
            raise TaskError('批注已删除，不能应用或撤销。')
        before = self.effect_state(current.to_dict())
        if not undo and self.comment_revision(current.to_dict()) != task['comment'].get('revision'):
            raise TaskError('批注已变化，请重新比较。')
        after = json.loads(json.dumps(before))
        after['thread'].append({'author': 'human', 'at': datetime.now(timezone.utc).isoformat(),
                                'text': '已撤销本次采纳。' if undo else '已采纳候选并通过编译。'})
        after['status'] = 'open' if undo else 'resolved'
        return {'comment': current.id, 'transaction': transaction_id, 'before': before, 'after': after}

    def write_comment_effect(self, effects, rollback=False):
        # Use the same lock as upstream edits. Only this comment's thread/status is touched.
        with self.comments._locked():
            data = self.comments._read()
            current = next((c for c in data['comments'] if c['id'] == effects['comment']), None)
            if current is None:
                raise TaskError('批注已被删除，无法自动恢复；请检查恢复记录。')
            expected, desired = (effects['after'], effects['before']) if rollback else (effects['before'], effects['after'])
            now = self.effect_state(current)
            if rollback and now == desired:
                return  # The callback never wrote, or recovery already finished this part.
            if now != expected:
                raise TaskError('批注已有后续修改，已保留新意见；请检查恢复记录。')
            current.update(json.loads(json.dumps(desired)))
            current['updated'] = datetime.now(timezone.utc).isoformat()
            if rollback:
                current.pop('kimi_paper_transaction', None)
            else:
                current['kimi_paper_transaction'] = effects['transaction']
            self.comments._write(data)

    async def recover_effects(self, journal):
        # An overlapping watcher compile may still represent the failed transaction.
        active = self._compile_task
        if active is not None and not active.done():
            await asyncio.shield(active)
        result = await self.do_compile()
        if not result.success:
            raise TaskError('恢复正文后 PDF 编译失败，请修复论文后重新连接。')
        if not journal['effects'].get('studio'):self.write_comment_effect(journal['effects'], rollback=True)
        await self.broadcast({'type': 'comments_changed'})

    async def applied(self, task, undo):
        result = await self.do_compile()
        if not result.success:
            raise TaskError('正式 PDF 未更新成功，请检查编译结果。')
        if not task.get('studio'):self.write_comment_effect(task['_transaction']['effects'])
        await self.broadcast({'type': 'comments_changed'})

    async def visible(self, request):
        import pymupdf
        body = await request.json()
        if body.get('digest') != self.pdf_digest or not self.last_result or not self.last_result.output_file:
            raise TaskError('PDF 已更新，请重新获取当前段落。')
        pages = body.get('pages', [])[:3]
        def extract():
            chunks = []
            with pymupdf.open(self.last_result.output_file) as doc:
                for spec in pages:
                    page_number = int(spec['page'])
                    coords = [float(v) for v in spec['rect']]
                    if len(coords) != 4 or not all(math.isfinite(v) for v in coords) or not 1 <= page_number <= len(doc):
                        raise TaskError('页面位置无效。')
                    page = doc[page_number - 1]
                    try:
                        chunks.extend(visible_paragraphs(page, coords))
                    except ReadingError as error:
                        raise TaskError(str(error)) from None
            return '\n\n'.join(chunks)[:16000]
        expected_digest = self.pdf_digest
        text = await asyncio.to_thread(extract)
        if expected_digest != self.pdf_digest or body.get('digest') != self.pdf_digest:
            raise TaskError('PDF 在提取期间更新，请重新获取当前段落。')
        if not text:
            raise TaskError('当前页面没有可提取的文字，请划选文字或检查是否为扫描 PDF。')
        return web.json_response({'text': text, 'digest': self.pdf_digest})

    async def setup(self, port):
        # No HTTP operations or file watcher can race unfinished transaction recovery.
        await self.engine.finish_recovery()
        await super().setup(port)
        if self.studio:await self.studio.start()
        else:self.catalog_task = asyncio.create_task(self.engine.catalog())
        print(f'Kimi Paper service: http://127.0.0.1:{port}/#token={self.token}', flush=True)

    async def cleanup(self):
        if self.studio:await self.studio.close()
        if self.catalog_task:
            self.catalog_task.cancel()
            await asyncio.gather(self.catalog_task, return_exceptions=True)
        if self.engine:
            await self.engine.close()
        await super().cleanup()


async def main():
    config = load_config()
    server = PaperService(config)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    try:
        await server.setup(config.port)
        await stop.wait()
    finally:
        await server.cleanup()


if __name__ == '__main__':
    asyncio.run(main())
