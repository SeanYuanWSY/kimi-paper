"""Persistent native Kimi workspace; immutable previews cross the reviewed apply boundary."""
import asyncio
import base64
import json
import os
from pathlib import Path
import re
import secrets
import socket
import sys
import time
import uuid

import aiohttp
from aiohttp import web
from paper_tasks import (TaskError, atomic_json, source_files, materialize, fingerprint,
                         sandbox_profile, agent_environment, stop_group, safe_path, candidate_diff,
                         sensitive_project_paths, direct_project_write_denials)
from paper_engine import paper_files, compile_copy


class Studio:
    def __init__(self, service, direct=False):
        self.service = service
        self.engine = service.engine
        self.direct = direct
        self.root = self.engine.store.root / 'studio'
        if self.root.is_symlink(): raise TaskError('工作台路径不安全。')
        self.root.mkdir(mode=0o700, exist_ok=True)
        self.manifest_path = self.root / ('direct-manifest.json' if direct else 'manifest.json')
        expected = {'project': str(self.engine.manuscript.resolve()), 'main': self.engine.main,
                    'mode': 'direct' if direct else 'reviewed'}
        if self.manifest_path.exists():
            if self.manifest_path.is_symlink():
                raise TaskError('工作台状态路径不安全。')
            self.manifest = json.loads(self.manifest_path.read_text())
            if not isinstance(self.manifest,dict) or not re.fullmatch(r'[0-9a-f]{32}',str(self.manifest.get('id',''))) or any(self.manifest.get(k) != v for k,v in expected.items()):
                raise TaskError('工作台与当前项目不匹配，已停止恢复。')
        else:
            self.manifest = {**expected, 'id': uuid.uuid4().hex, 'session': None, 'pending': [], 'preview': None}
            self.persist()
        self.run = self.root / self.manifest['id']
        self.work = self.engine.manuscript if direct else self.run / 'work'
        self.base = self.run / 'base'
        if self.run.is_symlink():raise TaskError('工作台路径不安全。')
        if self.run.exists():self.recover_swap()
        if not self.run.exists():
            self.run.mkdir(mode=0o700)
            original = paper_files(self.engine.manuscript, self.engine.main, allow_oversize=direct)
            materialize(self.base, original)
            if not direct:
                materialize(self.work, original)
        checked = (self.root, self.run, self.base) if direct else (self.root, self.run, self.work, self.base)
        for path in checked:
            if path.is_symlink() or not path.is_dir():
                raise TaskError('草稿路径不安全，已停止。')
        self.proc = self.http = self.monitor = self.drain = None
        self.control = None
        self.port = None
        self.token = None
        self.error = None
        self.lock = asyncio.Lock()
        self.send_lock = asyncio.Lock()
        self.last_busy = False
        if self.direct:
            self.reconcile_direct_checkpoint()
        self.last_frozen = self.manifest.get('frozen')
        if self.direct and self.last_frozen is None:
            self.last_frozen = fingerprint(self.project_files())
            self.manifest['frozen'] = self.last_frozen
            self.persist()
        if not self.direct:
            self.install_workspace_guidance()

    def install_workspace_guidance(self):
        path=self.work/'AGENTS.md'
        if path.is_symlink():raise TaskError('草稿说明路径不安全。')
        if not path.exists():
            path.write_text('# Kimi Paper 工作台\n\n当前目录是持久的论文草稿项目。LaTeX 主文件为 `'+self.engine.main+'`。\n请在这里起草、修改正文、参考文献与图片素材；保留当前会话上下文。\n应用会编译冻结的 PDF 预览，用户确认采纳后才同步到正式项目。\n不要访问正式项目或修改全局配置；不要自行替用户执行 GitHub 推送。\n需要中文排版时请说明编译引擎要求，不要把阅读翻译直接写进英文论文。\n')

    def persist(self):
        atomic_json(self.manifest_path, self.manifest)

    def project_files(self, excluded=None):
        return paper_files(self.work, self.engine.main, allow_oversize=self.direct, excluded=excluded,
                           allow_unsupported=self.direct)

    def replace_base(self, files):
        staged = self.run / ('base-next-' + uuid.uuid4().hex)
        materialize(staged, files)
        self.exchange({'base': staged})

    def reconcile_direct_checkpoint(self):
        """Make the private recovery base agree with durable direct journals or current disk."""
        if not self.direct:
            return
        current = self.project_files()
        current_fingerprint = fingerprint(current)
        base = paper_files(self.base, self.engine.main, allow_oversize=True,
                           allow_unsupported=True)
        barrier = float(self.manifest.get('external_baseline', 0) or 0)

        committed = []
        for path in self.engine.transactions():
            try:
                candidate = json.loads(path.read_text())
                candidate_task = self.engine.store.get(candidate.get('task'))
            except (KeyError, OSError, ValueError, TypeError):
                continue
            if (candidate.get('operation') == 'direct' and candidate.get('state') == 'committed'
                    and candidate_task.get('direct') and candidate_task.get('status') == 'applied'
                    and candidate_task.get('journal') == candidate.get('id')
                    and float(candidate.get('created', 0) or 0) > barrier):
                committed.append((float(candidate.get('created', 0) or 0), candidate_task, candidate))
        if committed:
            _, task, journal = max(committed, key=lambda row: row[0])
            try:
                after_matches = all(
                    current.get(name) == (base64.b64decode(values['after'])
                                          if values.get('after') is not None else None)
                    for name, values in journal.get('files', {}).items())
            except (ValueError, TypeError):
                after_matches = False
            if after_matches and fingerprint(base) == current_fingerprint:
                self.manifest['last_direct'] = task['id']
                self.manifest['frozen'] = current_fingerprint
                self.persist()
                return
            expected = dict(base)
            valid = task.get('baseline') == fingerprint(base)
            try:
                for name, values in journal.get('files', {}).items():
                    before = base64.b64decode(values['before']) if values.get('before') is not None else None
                    after = base64.b64decode(values['after']) if values.get('after') is not None else None
                    valid = valid and base.get(name) == before
                    if after is None:
                        expected.pop(name, None)
                    else:
                        expected[name] = after
            except (ValueError, TypeError):
                valid = False
            if valid and fingerprint(expected) == current_fingerprint:
                self.replace_base(expected)
                self.manifest['last_direct'] = task['id']
                self.manifest['frozen'] = current_fingerprint
                self.persist()
                return

        # A manual edit or interrupted Git update cannot safely inherit an older undo boundary.
        if fingerprint(base) != current_fingerprint:
            self.replace_base(current)
        self.manifest['last_direct'] = None
        self.manifest['external_baseline'] = time.time()
        self.manifest['frozen'] = current_fingerprint
        self.persist()

    def accept_external_baseline(self):
        """Start a fresh recovery boundary after Git changes the original worktree."""
        if not self.direct:
            return
        # Persist the boundary first: a crash may lose the new base, but can never
        # resurrect an undo entry from before a Git worktree operation.
        self.manifest['last_direct'] = None
        self.manifest['external_baseline'] = time.time()
        self.persist()
        files = self.project_files()
        self.replace_base(files)
        self.last_frozen = fingerprint(files)
        self.manifest['frozen'] = self.last_frozen
        self.persist()

    async def request(self, path, body=None, method=None):
        if not self.http or not self.token:
            raise TaskError('Kimi 工作台尚未连接，请稍后重试。')
        async with self.http.request(method or ('POST' if body is not None else 'GET'),
                f'http://127.0.0.1:{self.port}/api/v1/{path}', json=body,
                headers={'Authorization': 'Bearer '+self.token}, allow_redirects=False) as r:
            data = await r.json()
            if r.status not in (200,201) or data.get('code') != 0:
                raise TaskError(f"Kimi 工作台请求未完成（{data.get('code', r.status)}）。请在左侧检查登录或权限。")
            return data.get('data', {})

    async def start(self):
        self.token=None
        env, protected = agent_environment(self.run, 'kimi')
        with socket.socket() as s:
            s.bind(('127.0.0.1',0)); self.port=s.getsockname()[1]
        if self.direct:
            excluded = []
            self.project_files(excluded)
            sensitive = sensitive_project_paths(self.work)
            nonrecoverable = [self.work / item['path'] for item in excluded]
            protected = [*protected, self.work / '.git', self.work / '.tex-mcp-web.yaml',
                         *sensitive, *nonrecoverable]
            boundary = sandbox_profile(self.work, self.engine.manuscript,
                        [self.work, self.run/'home', self.run/'tmp'], protected,
                        unreadable=sensitive,
                        deny_manuscript_read=False)
            boundary += '\n' + direct_project_write_denials(self.work)
        else:
            boundary = sandbox_profile(self.work, self.engine.manuscript,
                        [self.work, self.run/'home', self.run/'tmp'], protected,
                        read_scope=(self.engine.store.root, self.run))
        # Server and descendants may listen only on this dedicated local Web port.
        boundary += f'\n(allow network-bind (local tcp "localhost:{self.port}"))\n(allow network-inbound (local tcp "localhost:{self.port}"))'
        profile = self.run/'web.sb'; profile.write_text(boundary)
        rd,self.control=os.pipe()
        try:
            self.proc=await asyncio.create_subprocess_exec(sys.executable,str(Path(__file__).with_name('paper_guard.py')),str(rd),
                '/usr/bin/sandbox-exec','-f',str(profile),str(Path.home()/'.kimi-code/bin/kimi'),
                'web','--port',str(self.port),'--host','127.0.0.1','--no-open',
                cwd=self.work,env=env,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.DEVNULL,
                start_new_session=True,pass_fds=(rd,))
        finally: os.close(rd)
        async with asyncio.timeout(90):
            while line:=await self.proc.stdout.readline():
                # Credentials are consumed in memory, never logged or stored in the manifest.
                match=re.search(rb'http://127\.0\.0\.1:\d+/[^\s]*#token=([^\s\x1b]+)',line)
                if match:
                    self.token=match.group(1).decode();break
            if not self.token: raise TaskError('原生 Kimi Web 未能启动，请检查当前 Kimi 版本。')
        self.drain=asyncio.create_task(self.discard_output())
        self.http=aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20),trust_env=False)
        sid=self.manifest.get('session')
        if sid:
            await self.select(sid)
        else:
            data=await self.request('sessions',{'title':'论文起草与修改','metadata':{'cwd':str(self.work)},
                                               'agent_config':{'model':'kimi-for-coding/k3-256k'}})
            await self.select(data['id'])
        selected=await self.request('sessions/'+self.manifest['session'])
        if not selected.get('agent_config',{}).get('model'):
            await self.request('sessions/'+self.manifest['session']+'/profile',{'agent_config':{'model':'kimi-for-coding/k3-256k','thinking':'high'}})
        self.monitor=asyncio.create_task(self.watch())

    async def discard_output(self):
        while await self.proc.stdout.read(65536): pass

    async def new_session(self):
        async with self.send_lock:
            data=await self.request('sessions',{'title':'论文起草与修改','metadata':{'cwd':str(self.work)}})
            await self.select(data['id'])
            await self.request('sessions/'+data['id']+'/profile',{'agent_config':{'model':'kimi-for-coding/k3-256k','thinking':'high'}})

    async def select(self, sid):
        if not isinstance(sid,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,150}',sid):
            raise TaskError('会话标识无效。')
        data=await self.request('sessions/'+sid)
        if Path(data.get('metadata',{}).get('cwd','')).resolve()!=self.work.resolve():
            raise TaskError('请选择此项目草稿目录中的会话；切换项目请使用顶部项目按钮。')
        self.manifest['session']=sid;self.persist()
        return data

    async def busy(self):
        data=await self.request('sessions?include_archive=true&page_size=100')
        rows=data.get('items',data.get('sessions'))
        if not isinstance(rows,list) or len(rows)>=100: raise TaskError('无法确认全部会话状态，请减少会话或重试。')
        if any(not isinstance(row,dict) or not any(k in row for k in ('busy','main_turn_active')) or any(k in row and not isinstance(row[k],bool) for k in ('busy','main_turn_active')) for row in rows): raise TaskError('Kimi 会话状态格式不兼容。')
        return any(row.get('busy') or row.get('main_turn_active') for row in rows)

    async def watch(self):
        try:
            while True:
                await asyncio.sleep(2)
                try:
                    busy=await self.busy()
                    if self.last_busy and not busy:
                        current = fingerprint(self.project_files())
                        baseline = self.last_frozen if self.direct else fingerprint(paper_files(self.base,self.engine.main))
                        if current != baseline:
                            await self.freeze()
                    self.last_busy=busy
                except (TaskError,aiohttp.ClientError,asyncio.TimeoutError) as error:
                    self.error=str(error) if isinstance(error,TaskError) else '工作台连接暂时中断，请重新连接。'
        except asyncio.CancelledError: pass

    async def freeze(self):
        async with self.lock:
            if await self.busy(): raise TaskError('Kimi 仍在工作，请完成或停止后生成预览。')
            if self.direct:
                async with self.engine.apply_lock:
                    current = self.project_files()
                    current_fingerprint = fingerprint(current)
                    if self.engine.main not in current:
                        raise TaskError('当前目录还没有 LaTeX 主文件，请先在左侧起草。')
                    result = await self.service.do_compile()
                    if not result.success:
                        self.error = '论文编译未通过，请把右侧错误交给左侧 Kimi 修复。'
                        raise TaskError(self.error)
                    files = self.project_files()
                    if await self.busy() or fingerprint(files) != current_fingerprint:
                        raise TaskError('源文件在编译期间继续变化，未将这次 PDF 标记为最新版。')
                    before = paper_files(self.base, self.engine.main)
                    changed = {name for name in set(before) | set(files) if before.get(name) != files.get(name)}
                    if changed:
                        task_id = uuid.uuid4().hex
                        journal_id = uuid.uuid4().hex
                        task = {
                            'id': task_id, 'created': time.time(), 'tool': 'kimi',
                            'model': 'native-session', 'studio': True, 'direct': True,
                            'status': 'applying', 'baseline': fingerprint(before),
                            'comment': {'id': task_id, 'text': '直接修改', 'thread': []},
                            'output': '原始回复保留在左侧 Kimi 会话中。',
                            'compile': {'success': True, 'errors': [], 'warnings': []},
                            'diff': candidate_diff(before, files),
                            'message': '正在保存直接修改的恢复点。',
                        }
                        self.engine.store.put(task, 'direct:' + task_id)
                        try:
                            versions = await asyncio.to_thread(
                                self.engine.history.record, before, files, journal_id)
                            if await self.busy() or fingerprint(self.project_files()) != current_fingerprint:
                                raise TaskError('保存恢复点时项目又发生了变化，请等 Kimi 完成后重试。')
                            journal = {
                                'id': journal_id, 'task': task_id, 'operation': 'direct',
                                'state': 'committed', 'created': time.time(), 'effects': {'studio': True},
                                'files': {
                                    name: {
                                        'before': base64.b64encode(before[name]).decode() if name in before else None,
                                        'after': base64.b64encode(files[name]).decode() if name in files else None,
                                    } for name in changed
                                },
                                'versions': versions,
                            }
                            atomic_json(self.engine.store.root / 'journals' / (journal_id + '.json'), journal)
                        except BaseException:
                            self.engine.update(task_id, status='needs_review',
                                               message='直接修改已在项目中，但自动恢复点未完整保存。')
                            raise
                        self.engine.update(task_id, status='applied', journal=journal_id,
                                           message='已直接更新项目，可撤销最近一次 Kimi 修改。')
                        self.manifest['last_direct'] = task_id
                    self.last_frozen = fingerprint(files)
                    self.manifest['frozen'] = self.last_frozen
                    self.error = None
                    self.replace_base(files)
                    self.persist()
                    await self.service.broadcast({'type':'studio_preview','id':None})
                    return
            files=paper_files(self.work,self.engine.main)
            fp=fingerprint(files)
            if fp==self.last_frozen:
                latest=next((t for t in self.engine.store.all() if t.get('studio') and t.get('compile',{}).get('success')),None)
                if latest:self.manifest['preview']=latest['id'];self.persist()
                return
            if self.engine.main not in files:
                raise TaskError('草稿还没有 LaTeX 主文件，请在左侧起草，或选择正确主文件。')
            ident=uuid.uuid4().hex
            run=self.engine.run_dir(ident);run.mkdir(mode=0o700,parents=True)
            materialize(run/'base',paper_files(self.base,self.engine.main))
            materialize(run/'result',files)
            result=await compile_copy(run/'result',self.engine.main,self.engine.compile_engine,self.engine.manuscript)
            # A later write never mutates this snapshot. Refuse to label it as the latest draft.
            changed=fp!=fingerprint(paper_files(self.work,self.engine.main))
            task={'id':ident,'created':time.time(),'tool':'kimi','model':'native-session','studio':True,
                  'session':self.manifest['session'],'status':'ready' if result['success'] else 'compile_failed',
                  'comment':{'id':ident,'text':'会话草稿预览','thread':[]},'baseline':fingerprint(paper_files(self.base,self.engine.main)),
                  'output':'原始回复保留在左侧 Kimi 会话中。','compile':result,
                  'diff':candidate_diff(paper_files(self.base,self.engine.main),files),
                  'message':'预览期间草稿继续变化；此处保留冻结版本。' if changed else '草稿已冻结，可查看后采纳。'}
            self.engine.store.put(task, 'studio:'+ident)
            self.last_frozen=fp;self.manifest['frozen']=fp
            if result['success']: self.manifest['preview']=ident;self.error=None
            else: self.error='草稿编译未通过。请查看修改记录中的具体错误，并在原会话修复。'
            self.persist()
            await self.service.broadcast({'type':'studio_preview','id':ident})

    async def send(self, text, notes):
        if self.direct and getattr(self.service, 'git_active', False):
            raise TaskError('Git 操作正在进行，请完成后再发送修改。')
        sid=self.manifest['session'];selected=await self.select(sid)
        body=text.strip()
        if notes:
            instruction = ('论文批注。请直接在当前项目目录中定位原文、修改源文件并保存：\n'
                           if self.direct else '论文批注，请延续当前会话讨论或修改：\n')
            body+=('\n\n' if body else '')+instruction
            for number,note in enumerate(notes,1):
                body+='\n'+str(number)+'. 选中的原文：\n> '+str(note.get('quote','')).replace('\n','\n> ')+'\n\n我的要求：'+str(note.get('text',''))+'\n'
        if not body:raise TaskError('还没有待发送的批注。')
        config=selected.get('agent_config',{})
        payload={'content':[{'type':'text','text':body}],'prompt_id':str(uuid.uuid4()),
                 'model':config.get('model') or 'kimi-for-coding/k3-256k'}
        if config.get('thinking'):payload['thinking']=config['thinking']
        elif payload['model'].endswith('k3-256k'):payload['thinking']='high'
        return await self.request('sessions/'+sid+'/prompts',payload)

    def recover_swap(self):
        journal=self.run/'swap.json'
        if not journal.exists():return
        if journal.is_symlink():raise TaskError('草稿恢复记录不安全。')
        data=json.loads(journal.read_text())
        if not isinstance(data,dict) or not re.fullmatch('[0-9a-f]{32}',str(data.get('id',''))):raise TaskError('草稿恢复记录无效。')
        if data.get('names') not in [['base'],['work','base']]:raise TaskError('草稿恢复范围无效。')
        if not data.get('committed'):
            for name in data['names']:
                backup=self.run/('saved-'+data['id'])/name
                target=self.run/name
                if backup.is_symlink() or target.is_symlink():raise TaskError('恢复路径不安全。')
                if backup.exists():
                    if target.exists():target.rename(self.run/(name+'-interrupted-'+uuid.uuid4().hex))
                    backup.rename(target)
        journal.unlink()

    def exchange(self, replacements):
        ident=uuid.uuid4().hex
        backup=self.run/('saved-'+ident);backup.mkdir(mode=0o700)
        journal=self.run/'swap.json'
        data={'id':ident,'names':list(replacements),'committed':False}
        atomic_json(journal,data)
        try:
            for name,staged in replacements.items():
                (self.run/name).rename(backup/name)
                staged.rename(self.run/name)
            data['committed']=True;atomic_json(journal,data)
        except BaseException:
            self.recover_swap();raise
        self.recover_swap()

    def adopted(self, ident):
        # Advance only the merge ancestor to the adopted immutable snapshot.
        # Live work may contain later edits and is never touched here.
        files=paper_files(self.engine.run_dir(ident)/'result',self.engine.main)
        staged=self.run/('base-next-'+uuid.uuid4().hex)
        materialize(staged,files)
        self.exchange({'base':staged})
        self.manifest['adopted']=ident;self.persist()

    async def sync(self):
        if self.direct:
            raise TaskError('Kimi 已经在原项目目录中工作，无需同步草稿。')
        async with self.lock:
            async with self.engine.apply_lock:
                if await self.busy():raise TaskError('请先停止 Kimi，再更新草稿。')
                expected=self.last_frozen or fingerprint(paper_files(self.base,self.engine.main))
                if fingerprint(paper_files(self.work,self.engine.main))!=expected:
                    raise TaskError('草稿有尚未冻结的新修改，请先生成预览并检查。')
                await self.close()
                if fingerprint(paper_files(self.work,self.engine.main))!=expected:
                    raise TaskError('停止时草稿发生变化，已完整保留；请重新连接检查。')
                files=paper_files(self.engine.manuscript,self.engine.main)
                next_work=self.run/('next-work-'+uuid.uuid4().hex)
                next_base=self.run/('next-base-'+uuid.uuid4().hex)
                materialize(next_work,files);materialize(next_base,files)
                self.exchange({'work':next_work,'base':next_base})
                self.install_workspace_guidance()
                self.manifest['frozen']=None;self.last_frozen=None;self.manifest['preview']=None
                self.persist()
                await self.start()

    async def undo_direct(self):
        if not self.direct:
            raise TaskError('当前不是直接编辑模式。')
        async with self.lock:
            if await self.busy():
                raise TaskError('请先停止 Kimi，再撤销最近修改。')
            task_id = self.manifest.get('last_direct')
            if not task_id:
                raise TaskError('还没有可撤销的 Kimi 修改。')
            await self.close()
            try:
                await self.engine.undo(task_id)
                files = self.project_files()
                self.replace_base(files)
                self.last_frozen = fingerprint(files)
                self.manifest['frozen'] = self.last_frozen
                self.manifest['last_direct'] = None
                self.persist()
            finally:
                await self.start()

    async def send_pending(self, text=''):
        async with self.send_lock:
            notes=list(self.manifest.get('pending',[]))
            await self.send(text,notes)
            ids={n['id'] for n in notes}
            self.manifest['pending']=[n for n in self.manifest.get('pending',[]) if n['id'] not in ids]
            self.persist()

    def preview_pdf(self):
        if self.direct:return None
        ident=self.manifest.get('preview')
        if not ident:return None
        task=self.engine.store.get(ident)
        if not task.get('studio') or not task.get('compile',{}).get('success'):return None
        path=self.engine.run_dir(ident)/'result'/Path(self.engine.main).with_suffix('.pdf')
        return path if path.is_file() and not path.is_symlink() else None

    async def state(self, include_files=True):
        excluded=[]
        files=(await asyncio.to_thread(source_files, self.engine.manuscript, excluded,
                                       self.direct, self.direct) if include_files else {})
        return {'project':str(self.engine.manuscript),'main':self.engine.main,'session':self.manifest.get('session'),
                'chatURL':f'http://127.0.0.1:{self.port}/sessions/{self.manifest["session"]}#token={self.token}' if self.token else None,
                'busy':await self.busy() if self.token else False,'error':self.error,
                'preview':None if self.direct else self.manifest.get('preview'),'pending':self.manifest.get('pending',[]),
                'direct':self.direct,'cwd':str(self.work),
                'files':[{'path':k,'bytes':len(v)} for k,v in files.items()],'excluded':excluded}

    async def handle(self, request):
        action=request.match_info['action']
        if request.method=='GET':
            if action=='state':return web.json_response(await self.state())
            if action=='connection':return web.json_response(await self.state(False))
            if action=='status':return web.json_response({'preview':None if self.direct else self.manifest.get('preview'),'digest':self.service.pdf_digest if self.direct else (self.manifest.get('preview') if self.preview_pdf() else self.service.pdf_digest),'error':self.error})
            if action=='pdf':
                pdf=self.preview_pdf()
                if not pdf:raise TaskError('没有可预览的 PDF。')
                return web.FileResponse(pdf,headers={'Cache-Control':'no-store'})
            if action=='files':
                name=request.query.get('path','');files=source_files(self.engine.manuscript)
                if name not in files:raise TaskError('文件不在项目预览范围。')
                return web.Response(body=files[name],content_type='application/octet-stream')
            raise TaskError('未知工作台操作。')
        body=await request.json()
        if action=='select':await self.select(body['session'])
        elif action=='new':await self.new_session()
        elif action=='freeze':await self.freeze()
        elif action=='sync':await self.sync()
        elif action=='view':
            if self.direct:return web.json_response({'ok':True})
            ident=body.get('id')
            if ident:
                task=self.engine.store.get(ident)
                if not task.get('studio') or not task.get('compile',{}).get('success'):raise TaskError('此版本没有可用的 PDF。')
            self.manifest['preview']=ident;self.persist()
        elif action=='note':
            note={'id':uuid.uuid4().hex,'quote':str(body.get('quote',''))[:16000],'text':str(body.get('text',''))[:16000],
                  'digest':body.get('digest'),'preview':self.manifest.get('preview')}
            self.manifest.setdefault('pending',[]).append(note);self.persist()
            if body.get('send'):
                await self.send_pending()
        elif action=='send':
            await self.send_pending(str(body.get('text',''))[:32000])
        elif action=='undo':
            await self.undo_direct()
        elif action=='archive':
            task=self.engine.store.get(body['id'])
            if not task.get('studio'):raise TaskError('此记录不属于工作台。')
            task['archived']=bool(body.get('archived'));self.engine.store.put(task)
        else:raise TaskError('未知工作台操作。')
        return web.json_response({'ok':True})

    async def close(self):
        if self.monitor:self.monitor.cancel();await asyncio.gather(self.monitor,return_exceptions=True)
        if self.control is not None:os.close(self.control);self.control=None
        await stop_group(self.proc)
        if self.drain:self.drain.cancel();await asyncio.gather(self.drain,return_exceptions=True)
        if self.http:await self.http.close()
