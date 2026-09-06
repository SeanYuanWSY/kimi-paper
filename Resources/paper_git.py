"""Explicit manuscript Git actions; never invoked by background task polling."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import re
import time
import uuid
import tempfile
import sys

from paper_tasks import TaskError, digest, stop_group, safe_path, SOURCE_SUFFIXES

SECRET = re.compile(rb'sk-[A-Za-z0-9_.-]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{30,}|'
                    rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|AKIA[A-Z0-9]{16}|'
                    rb'(?i:bearer)[ \t]+[A-Za-z0-9._-]{25,}')


def publish_path(name):
    path = Path(name)
    special = name in {'.gitignore','.gitattributes','LICENSE','COPYING'}
    if (not name or path.is_absolute() or str(path) != name or '\\' in name or '\x00' in name
            or (not special and (any(p.startswith('.') for p in path.parts) or path.suffix.lower() not in SOURCE_SUFFIXES | {'.md'}))
            or path.name in {'AGENTS.md','CLAUDE.md'}
            or any(p in {'credentials','oauth','node_modules','dist','__pycache__'} for p in path.parts)):
        raise TaskError('发布清单包含论文范围以外的文件，请先在其他 Git 工具中处理。')
    return name


def scan(content):
    if SECRET.search(content):
        raise TaskError('检测到可能的密钥，已停止本次 Git 操作；请先从论文及待推送历史中移除。')


def github_url(value):
    match = re.fullmatch(r'(?:https://github\.com/|git@github\.com:|ssh://git@github\.com/)([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?', value)
    if not match or any(part in {'.', '..'} for part in match.groups()):
        raise TaskError('仅支持标准 GitHub HTTPS 或 git@github.com 仓库地址，不接受内嵌密码、参数或自定义端口。')
    return 'https://github.com/' + '/'.join(match.groups())


class PaperGit:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.previews = {}

    async def git(self, *args, check=True, data=None):
        if (self.root/'.git').is_symlink():
            raise TaskError('仓库元数据是符号链接，不能从应用操作。')
        # Do not inherit GIT_DIR, alternate indexes, shell overrides or askpass.
        env = {k: os.environ[k] for k in ('PATH','HOME','LANG','USER','SSH_AUTH_SOCK') if k in os.environ}
        env.update({'GIT_TERMINAL_PROMPT':'0','GCM_INTERACTIVE':'never',
                    'GIT_SSH_COMMAND':'/usr/bin/ssh -o BatchMode=yes -o StrictHostKeyChecking=yes',
                    'GIT_EDITOR':'/usr/bin/true'})
        lookup = await asyncio.create_subprocess_exec('/usr/bin/git','--no-replace-objects','-C',str(self.root),'config','--null','--name-only',
            '--get-regexp',r'^filter\..*\.(clean|smudge|process|required)$',env=env,
            stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.DEVNULL,start_new_session=True)
        try:
            keys,_=await asyncio.wait_for(lookup.communicate(),10)
        except asyncio.TimeoutError:
            raise TaskError('读取 Git 配置超时，请稍后重试。') from None
        finally:
            await stop_group(lookup)
        filters=[]
        for key in keys.decode('utf-8','replace').split('\0'):
            if re.fullmatch(r'filter\.[A-Za-z0-9_.-]+\.(clean|smudge|process|required)',key,re.I):
                filters+=['-c',key+('=false' if key.endswith('.required') else '=')]
            elif key:
                raise TaskError('Git 过滤器配置暂不受支持，请使用外部 Git 工具处理。')
        command = ['/usr/bin/git','--no-replace-objects', '--no-optional-locks','--literal-pathspecs', '-C', str(self.root),
            '-c','core.hooksPath=/dev/null','-c','core.fsmonitor=false','-c','commit.gpgsign=false',
            *filters,
            '-c','protocol.allow=never','-c','protocol.https.allow=always','-c','protocol.ssh.allow=always',
            '-c','protocol.ext.allow=never','-c','protocol.file.allow=never', *args,
        ]
        guarded = args[0] in {'fetch','push','merge','switch','add','init','branch'} or any(v in {'commit','push'} for v in args[:3]) or (args[0]=='remote' and args[1]=='add')
        control=None;temp=None;status=None
        try:
            if guarded:
                temp=tempfile.TemporaryDirectory(prefix='kimi-paper-git-')
                status=Path(temp.name)/'exit'
                read_fd,control=os.pipe()
                command=[sys.executable,str(Path(__file__).with_name('paper_guard.py')),str(read_fd),'--exit-status',str(status),*command]
                try:
                    proc=await asyncio.create_subprocess_exec(*command,env=env,
                        stdin=asyncio.subprocess.PIPE if data is not None else asyncio.subprocess.DEVNULL,
                        stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.DEVNULL,start_new_session=True,pass_fds=(read_fd,))
                finally:os.close(read_fd)
            else:
                proc=await asyncio.create_subprocess_exec(*command,env=env,
                    stdin=asyncio.subprocess.PIPE if data is not None else asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.DEVNULL,start_new_session=True)
        except BaseException:
            if control is not None:os.close(control)
            if temp:temp.cleanup()
            raise
        try:
            output,_ = await asyncio.wait_for(proc.communicate(data),90)
            code=int(status.read_text()) if status and status.is_file() else (1 if guarded else proc.returncode)
            if check and code:
                raise TaskError('Git 操作未完成。请检查仓库状态、登录或网络；应用不会强行覆盖或解决冲突。')
            return output if check else (code,output)
        except asyncio.TimeoutError:
            raise TaskError('Git 操作超过 90 秒，已停止；请刷新状态后检查。') from None
        finally:
            if control is not None:os.close(control)
            await stop_group(proc)
            if temp:temp.cleanup()

    async def boundary(self):
        code,top = await self.git('rev-parse','--show-toplevel',check=False)
        if code:
            return {'available':False,'canInit':not (self.root/'.git').exists(),'reason':'这篇论文尚未建立 Git 仓库。'}
        if Path(os.fsdecode(top.strip())).resolve() != self.root:
            return {'available':False,'canInit':True,'reason':'论文位于上级仓库内。为避免提交其他项目，请为论文建立独立仓库。'}
        for directory,dirs,files in os.walk(self.root,followlinks=False):
            current=Path(directory)
            if current != self.root and ('.git' in dirs or '.git' in files):
                return {'available':False,'reason':'论文包含嵌套 Git 仓库，请先明确各仓库边界。'}
            if '.gitmodules' in files:
                return {'available':False,'reason':'论文包含子模块，需要先在 Git 工具中处理。'}
            dirs[:] = [d for d in dirs if not d.startswith('.') and not (current/d).is_symlink()]
        return {'available':True}

    async def status(self):
        boundary = await self.boundary()
        if not boundary['available']:
            return boundary
        _,branch=await self.git('symbolic-ref','--quiet','--short','HEAD',check=False)
        _,head=await self.git('rev-parse','--verify','HEAD',check=False)
        _,upstream=await self.git('rev-parse','--abbrev-ref','--symbolic-full-name','@{upstream}',check=False)
        raw=await self.git('status','--porcelain=v1','-z','--untracked-files=all')
        entries=raw.split(b'\0'); files=[]; index=0
        while index < len(entries):
            item=entries[index];index+=1
            if not item:continue
            code=item[:2].decode('ascii');name=os.fsdecode(item[3:])
            row={'path':name,'index':code[0],'worktree':code[1]}
            if 'R' in code or 'C' in code:
                row['previous']=os.fsdecode(entries[index]);index+=1
            files.append(row)
        ahead=behind=0
        if upstream.strip():
            code,count=await self.git('rev-list','--left-right','--count','HEAD...@{upstream}',check=False)
            if not code:ahead,behind=map(int,count.split())
        _,remote=await self.git('remote','get-url','origin',check=False)
        remote_ok=False;url=None
        if remote.strip():
            try:url=github_url(remote.decode().strip());remote_ok=True
            except (TaskError,UnicodeError):pass
        gitdir=Path(os.fsdecode((await self.git('rev-parse','--absolute-git-dir')).strip()))
        operations=[p for p in ('MERGE_HEAD','CHERRY_PICK_HEAD','REVERT_HEAD','rebase-merge','rebase-apply','index.lock') if (gitdir/p).exists()]
        result={'available':True,'branch':branch.decode().strip() or None,'head':head.decode().strip() if not head.startswith(b'HEAD') else '',
                'upstream':upstream.decode().strip() if upstream.strip()!=b'@{upstream}' else '',
                'ahead':ahead,'behind':behind,'files':files,'operations':operations,'githubURL':url,'remoteOK':remote_ok}
        result['branches']=(await self.git('for-each-ref','--format=%(refname:short)','refs/heads/')).decode().splitlines()
        result['revision']=digest(json.dumps(result,sort_keys=True).encode())
        return result

    async def require(self, clean=False):
        state=await self.status()
        if not state['available']:
            raise TaskError(state['reason'])
        if not state['branch'] or state['operations']:
            raise TaskError('当前分支未确定，或已有 Git 操作/锁未完成，请先处理后再继续。')
        if clean and state['files']:
            raise TaskError('论文还有未提交或未跟踪文件，请先检查并提交，再切换分支或拉取。')
        return state

    async def selected_bytes(self, names):
        if not isinstance(names,list) or not 1 <= len(names) <= 300 or len(set(names)) != len(names):
            raise TaskError('请选择 1 至 300 个不同的论文文件。')
        fingerprints={}
        total=0
        for name in names:
            publish_path(name)
            path=safe_path(self.root,name)
            if path.exists():
                if not path.is_file() or path.stat().st_size > 50*1024*1024:
                    raise TaskError('文件类型或大小不适合从应用发布。')
                content=path.read_bytes();total+=len(content)
                if total>250*1024*1024:raise TaskError('本次文件超过 250 MB，请缩小发布范围。')
                scan(content);fingerprints[name]=digest(content)
            else:
                fingerprints[name]=None
        return fingerprints

    async def staged(self):
        tree=(await self.git('write-tree')).decode().strip()
        entries=await self.tree_entries(tree)
        code,head=await self.git('rev-parse','--verify','HEAD',check=False)
        base=head.decode().strip() if code==0 else (await self.git('hash-object','-w','-t','tree','--stdin',data=b'')).decode().strip()
        names=[os.fsdecode(p) for p in (await self.git('diff','--no-ext-diff','--no-textconv','--name-only','-z',base,tree)).split(b'\0') if p]
        if not names:raise TaskError('暂存区为空，请先选择文件加入暂存。')
        for name in names:
            publish_path(name)
            if name in entries:scan(await self.git('cat-file','blob',entries[name]))
        return names,tree

    async def tree_entries(self, tree):
        entries={}
        for item in (await self.git('ls-tree','-r','-z',tree)).split(b'\0'):
            if not item:continue
            metadata,name=item.split(b'\t',1);mode,kind,oid=metadata.split()
            if mode not in {b'100644',b'100755'} or kind!=b'blob':
                raise TaskError('仓库包含符号链接或子模块对象，请使用外部 Git 工具核对。')
            entries[os.fsdecode(name)]=oid.decode('ascii')
        return entries

    async def check_filters(self,names):
        attrs=await self.git('check-attr','-z','--stdin','filter',data=b'\0'.join(os.fsencode(n) for n in names)+b'\0')
        if any(v not in {b'unspecified',b'unset'} for v in attrs.split(b'\0')[2::3]):
            raise TaskError('所选文件使用 Git 内容过滤器（例如 LFS），请在外部 Git 工具中暂存，避免改变存储格式。')

    async def remote(self):
        fetches=(await self.git('remote','get-url','--all','origin')).decode().splitlines()
        pushes=(await self.git('remote','get-url','--push','--all','origin')).decode().splitlines()
        if len(fetches)!=1 or len(pushes)!=1:raise TaskError('origin 配置了多个地址，请先核对后再同步。')
        fetch,push=fetches[0],pushes[0]
        if github_url(fetch) != github_url(push):
            raise TaskError('读取与推送地址不同，请先核对 origin 配置。')
        for key in ('remote.origin.vcs','remote.origin.uploadpack','remote.origin.receivepack'):
            code,value=await self.git('config','--get',key,check=False)
            if not code and value.strip():raise TaskError('origin 含自定义传输命令，请使用外部 Git 工具处理。')
        code,value=await self.git('config','--bool','--get','remote.origin.mirror',check=False)
        if not code and value.strip()==b'true':raise TaskError('不支持镜像推送配置。')
        return fetch,push

    async def prepare(self, action, body):
        if action not in {'init','ignore','connect','stage','commit','fetch','pull','push','branch','switch'}:
            raise TaskError('不支持的 Git 操作。')
        state=await self.status()
        preview={'action':action,'created':time.monotonic(),'revision':state.get('revision'),'body':{}}
        if action=='init':
            if (self.root/'.git').exists() or (self.root/'.git').is_symlink():
                raise TaskError('论文目录已存在 Git 元数据，不能重复初始化。')
            preview['summary']='在此论文文件夹建立独立本地仓库（main 分支），不会上传文件。'
        else:
            state=await self.require(clean=action in {'pull','branch','switch'})
            preview['revision']=state['revision']
            if action=='ignore':
                preview['summary']='仅在本地忽略 Kimi Paper 配置、批注缓存和 LaTeX 临时文件，不删除文件，也不修改已跟踪内容。'
            elif action=='connect':
                code,_=await self.git('remote','get-url','origin',check=False)
                if not code:raise TaskError('已配置 origin；为避免换错仓库，请在 Git 工具中修改已有地址。')
                url=github_url(str(body.get('url','')))
                preview['body']['url']=url+'.git';preview['summary']='连接 GitHub 仓库：'+url
            elif action=='stage':
                names=body.get('files',[])
                preview['fingerprints']=await self.selected_bytes(names)
                await self.check_filters(names)
                preview['body']['files']=names;preview['summary']='将以下文件的当前内容加入暂存区，包含所选删除。不会提交或上传。'
            elif action=='commit':
                names,tree=await self.staged()
                message=body.get('message','')
                if not isinstance(message,str) or not message.strip() or len(message)>2000:
                    raise TaskError('请填写简短的版本说明（最多 2000 字）。')
                preview['body']={'message':message.strip(),'files':names};preview['tree']=tree
                preview['summary']='提交当前暂存区到分支 '+state['branch']+'；未暂存内容不会提交。'
            elif action in {'branch','switch'}:
                branch=body.get('branch','')
                if not isinstance(branch,str) or branch.startswith('-') or len(branch)>120:
                    raise TaskError('分支名称无效。')
                await self.git('check-ref-format','--branch',branch)
                preview['body']['branch']=branch
                preview['summary']=('建立并切换到协作分支：' if action=='branch' else '切换分支：')+branch
            else:
                await self.remote()
                if action=='push' and not state['head']:raise TaskError('请先提交论文，再推送分支。')
                preview['summary']={'fetch':'获取 origin 的最新状态，不修改正文。',
                                    'pull':'获取远端更新，检查后仅快进当前分支。不会自动合并冲突。',
                                    'push':'检查待推送历史，并将当前本地分支推送到 origin 同名分支。不会强推。'}[action]
        token=uuid.uuid4().hex
        self.previews={k:v for k,v in self.previews.items() if time.monotonic()-v['created']<300}
        self.previews[token]=preview
        return {'token':token,'summary':preview['summary'],'files':preview['body'].get('files',[]),
                'branch':state.get('branch'),'githubURL':state.get('githubURL')}

    async def scan_history(self,head):
        if not re.fullmatch('[0-9a-f]{40,64}',head):raise TaskError('待推送提交无效。')
        if (await self.git('rev-parse','--is-shallow-repository')).strip()==b'true':
            raise TaskError('浅仓库的历史不完整，请先在外部 Git 工具中补齐历史再发布。')
        path=Path(os.fsdecode((await self.git('rev-parse','--git-path','info/grafts')).strip()))
        if not path.is_absolute():path=self.root/path
        if path.exists() or path.is_symlink():raise TaskError('仓库配置了历史 graft，无法完整审计推送内容。')
        commits=(await self.git('rev-list',head)).splitlines()
        if len(commits)>1000:raise TaskError('历史提交较多，请先使用外部 Git 工具进行发布检查。')
        for commit in commits:
            entries=await self.tree_entries(commit.decode('ascii'))
            for name in entries:publish_path(name)
        objects=(await self.git('rev-list','--objects',head)).splitlines()
        if len(objects)>10000:raise TaskError('历史对象较多，请先使用外部 Git 工具进行发布检查。')
        for entry in objects:
            fields=entry.split(b' ',1)
            if len(fields)<2:continue
            oid=fields[0].decode('ascii')
            kind=(await self.git('cat-file','-t',oid)).strip()
            if kind!=b'blob':continue
            publish_path(os.fsdecode(fields[1]))
            size=int(await self.git('cat-file','-s',oid))
            if size>50*1024*1024:raise TaskError('历史中有超过 50 MB 的文件，请先检查后使用外部 Git 工具发布。')
            scan(await self.git('cat-file','blob',oid))

    async def execute(self, token):
        preview=self.previews.pop(token,None)
        if not preview or time.monotonic()-preview['created']>300:
            raise TaskError('确认已过期，请重新预览。')
        action=preview['action'];body=preview['body']
        if action=='init':
            if (self.root/'.git').exists() or (self.root/'.git').is_symlink():
                raise TaskError('仓库状态已变化，请刷新。')
            await self.git('init','--template=','--initial-branch=main','.')
            await self.ignore_runtime()
            return '已建立本地仓库；请先选择文件加入暂存。'
        state=await self.require(clean=action in {'pull','branch','switch'})
        if state['revision']!=preview['revision']:
            raise TaskError('仓库状态在预览后变化，请重新检查后确认。')
        if action=='ignore':await self.ignore_runtime()
        elif action=='connect':await self.git('remote','add','origin',body['url'])
        elif action=='stage':
            if await self.selected_bytes(body['files'])!=preview['fingerprints']:
                raise TaskError('文件在预览后变化，请重新检查。')
            await self.check_filters(body['files'])
            paths=b'\0'.join(os.fsencode(p) for p in body['files'])+b'\0'
            await self.git('add','--pathspec-from-file=-','--pathspec-file-nul',data=paths)
            for name,expected in preview['fingerprints'].items():
                code,content=await self.git('show',':'+name,check=False)
                if (digest(content) if code==0 else None)!=expected:
                    raise TaskError('暂存期间文件发生变化；尚未提交，请重新查看暂存区。')
        elif action=='commit':
            _,tree=await self.staged()
            if tree!=preview['tree']:raise TaskError('暂存区已经变化，请重新预览。')
            await self.git('-c','user.useConfigOnly=true','commit','--no-gpg-sign','-F','-',data=body['message'].encode())
            actual=(await self.git('rev-parse','HEAD^{tree}')).decode().strip()
            if actual!=tree:raise TaskError('已产生提交，但内容与预览不同；请核对后再推送，应用不会自动回退。')
        elif action in {'branch','switch'}:
            await self.git('switch',*(['-c'] if action=='branch' else []),body['branch'])
        else:
            await self.remote()
            if action in {'fetch','pull'}:
                await self.git('fetch','--no-tags','origin','+refs/heads/*:refs/remotes/origin/*')
                if action=='pull':
                    latest=await self.require(clean=True)
                    if latest['head']!=state['head'] or latest['branch']!=state['branch']:
                        raise TaskError('获取期间本地分支变化，已停止拉取。')
                    full=(await self.git('rev-parse','--symbolic-full-name','@{upstream}')).decode().strip()
                    if not full.startswith('refs/remotes/origin/'):
                        raise TaskError('当前分支尚未关联 origin 分支，请先推送建立关联。')
                    await self.git('merge-base','--is-ancestor','HEAD','@{upstream}')
                    await self.git('merge','--ff-only','--no-edit','@{upstream}')
            else:
                await self.scan_history(state['head'])
                latest=await self.require()
                if latest['head']!=state['head'] or latest['branch']!=state['branch']:
                    raise TaskError('检查历史期间分支变化，请重新预览。')
                await self.git('-c','push.followTags=false','push','origin',state['head']+':refs/heads/'+state['branch'])
                code,_=await self.git('branch','--set-upstream-to=origin/'+state['branch'],state['branch'],check=False)
                if code:return '已推送检查过的提交，但上游关联未建立；请检查远端更新后再关联分支。'
        return '操作完成，请查看最新仓库状态。'

    async def ignore_runtime(self):
        raw=await self.git('rev-parse','--git-path','info/exclude')
        path=Path(os.fsdecode(raw.strip()))
        if not path.is_absolute():path=self.root/path
        if path.is_symlink() or path.parent.is_symlink():
            raise TaskError('Git 忽略文件是符号链接，未修改。')
        lines=['# Kimi Paper local runtime','.kimi-code/','.kimi-paper.json','.tex-mcp-web.yaml','.tex-mcp-web/',
               '*.aux','*.log','*.out','*.toc','*.bbl','*.blg','*.fls','*.fdb_latexmk','*.synctex.gz','.DS_Store']
        existing=path.read_text() if path.exists() else ''
        missing=[line for line in lines if line not in existing.splitlines()]
        if missing:
            path.parent.mkdir(parents=True,exist_ok=True)
            with path.open('a') as out:out.write('\n'+'\n'.join(missing)+'\n')
