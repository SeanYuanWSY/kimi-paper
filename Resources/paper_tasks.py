"""Persistent candidate workspaces. Agent processes never receive the manuscript path."""
from __future__ import annotations

import asyncio
import difflib
import hashlib
import json
import os
import re
from pathlib import Path
import shutil
import signal
import sqlite3
import stat
import subprocess
import sys
import time
import uuid


SOURCE_SUFFIXES = {'.tex', '.bib', '.sty', '.cls', '.bst', '.bbx', '.cbx', '.def', '.cfg', '.fd',
                   '.png', '.jpg', '.jpeg', '.pdf', '.eps', '.svg', '.csv', '.tsv', '.dat', '.txt',
                   '.md', '.json', '.yaml', '.yml', '.xlsx', '.pptx', '.docx', '.ods', '.odt',
                   '.webp', '.gif', '.tif', '.tiff', '.py', '.r', '.jl', '.ipynb'}
EDIT_SUFFIXES = {'.tex', '.bib', '.sty', '.cls', '.bst', '.bbx', '.cbx', '.def', '.cfg', '.fd', '.txt', '.md', '.json', '.yaml', '.yml', '.csv', '.tsv', '.svg', '.py', '.r', '.jl', '.ipynb'}
EXCLUDED = {'.git', '.kimi-code', '.codex', '.agents', '.tex-mcp-web', '.runtime', '.build',
            'node_modules', '__pycache__', 'dist', 'build', 'cache', 'caches', 'venv', 'env',
            'credentials', 'oauth', 'secrets', 'config', 'configuration'}


class TaskError(Exception):
    """Safe, user-visible error with no provider response or credential text."""


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def atomic_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temp = path.with_name(path.name + '.tmp-' + uuid.uuid4().hex)
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump(value, stream, ensure_ascii=False)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temp, path)


def exclusion_reason(path: Path, directory=False):
    """Classify by name before opening a file; excluded content is never inspected."""
    if path.is_absolute() or not path.parts or '..' in path.parts:
        return 'path outside project'
    for part in path.parts:
        if part.startswith('.'):
            return 'hidden file or directory'
        if part.lower() in EXCLUDED:
            return 'configuration or runtime directory'
        stem = Path(part).stem.lower()
        if re.search(r'(^|[-_.])(auth|credentials?|secrets?|tokens?|passwords?|oauth|apikey|api_key|config|settings)([-_.]|$)', stem):
            return 'potential credential or configuration'
    if path.name.upper() in {'AGENTS.MD', 'CLAUDE.MD'}:
        return 'agent instructions'
    if not directory and path.suffix.lower() not in SOURCE_SUFFIXES:
        return 'unsupported project file'
    return None


def sensitive_project_paths(root: Path) -> list[Path]:
    """Return credential/config paths by name without opening their contents."""
    protected = []
    for directory, dirs, names in os.walk(root, followlinks=False):
        current = Path(directory)
        kept = []
        for name in sorted(dirs):
            path = current / name
            relative = path.relative_to(root)
            reason = exclusion_reason(relative, directory=True)
            if name.lower() == '.git':
                continue
            if (reason in {'configuration or runtime directory', 'potential credential or configuration'}
                    or name.lower() in {'.kimi-code', '.codex', '.agents', '.ssh', '.aws', '.gnupg'}):
                protected.append(path)
            else:
                kept.append(name)
        dirs[:] = kept
        for name in sorted(names):
            path = current / name
            relative = path.relative_to(root)
            reason = exclusion_reason(relative)
            lower = name.lower()
            if (reason in {'configuration or runtime directory', 'potential credential or configuration'}
                    or lower == '.env' or lower.startswith('.env.')
                    or lower in {'id_rsa', 'id_ed25519'}
                    or Path(lower).suffix in {'.key', '.pem', '.p12', '.pfx'}):
                protected.append(path)
    return protected


def source_files(root: Path, excluded=None, allow_oversize=False,
                 allow_unsupported=False) -> dict[str, bytes]:
    """Copy bytes, never links. Optional exclusion report contains names, never content."""
    result = {}
    size = 0
    if root.is_symlink() or not root.is_dir():
        raise TaskError('项目目录无效或为符号链接。')
    for directory, dirs, names in os.walk(root, followlinks=False):
        current = Path(directory)
        included_dirs = []
        for name in sorted(dirs):
            path = current / name
            relative = path.relative_to(root)
            reason = exclusion_reason(relative, directory=True)
            if reason:
                if excluded is not None:
                    excluded.append({'path': str(relative), 'reason': reason})
                continue
            if path.is_symlink():
                if allow_oversize:
                    if excluded is not None:
                        excluded.append({'path': str(relative), 'reason': 'symbolic link not included in automatic recovery'})
                    continue
                raise TaskError('论文包含符号链接目录，请先将依赖放入论文目录。')
            included_dirs.append(name)
        dirs[:] = included_dirs
        for name in sorted(names):
            p = current / name
            relative = p.relative_to(root)
            reason = exclusion_reason(relative)
            if allow_unsupported and reason == 'unsupported project file':
                reason = None
            if reason:
                if excluded is not None:
                    excluded.append({'path': str(relative), 'reason': reason})
                continue
            # Open without following a substituted symlink and validate the opened inode.
            try:
                fd = os.open(p, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            except OSError:
                if allow_oversize and p.is_symlink():
                    if excluded is not None:
                        excluded.append({'path': str(relative), 'reason': 'symbolic link not included in automatic recovery'})
                    continue
                raise TaskError('论文依赖无法安全读取，请检查链接和文件权限。') from None
            with os.fdopen(fd, 'rb') as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode):
                    raise TaskError('论文依赖包含符号链接或特殊文件，无法建立独立候选。')
                if info.st_size > 50 * 1024 * 1024:
                    if allow_oversize:
                        if excluded is not None:
                            excluded.append({'path': str(relative), 'reason': 'too large for automatic recovery'})
                        continue
                    raise TaskError('单个论文依赖超过 50 MB，暂无法创建工作副本。')
                if allow_oversize and size + info.st_size > 250 * 1024 * 1024:
                    if excluded is not None:
                        excluded.append({'path': str(relative), 'reason': 'automatic recovery size limit'})
                    continue
                data = stream.read(50 * 1024 * 1024 + 1)
                if len(data) > 50 * 1024 * 1024:
                    if allow_oversize:
                        if excluded is not None:
                            excluded.append({'path': str(relative), 'reason': 'too large for automatic recovery'})
                        continue
                    raise TaskError('单个论文依赖超过 50 MB，暂无法创建工作副本。')
            size += len(data)
            if size > 250 * 1024 * 1024:
                raise TaskError('论文依赖超过 250 MB，请将当前论文单独放入文件夹。')
            result[str(relative)] = data
    return result


def fingerprint(files: dict[str, bytes]) -> str:
    return digest(json.dumps({k: digest(v) for k, v in sorted(files.items())}).encode())


class StatusFingerprint:
    """Metadata cache for UI hints only; adoption always reads and verifies all bytes."""
    def __init__(self):
        self.files = {}

    def get(self, root, excluded):
        hashes, next_files = {}, {}
        for directory, dirs, names in os.walk(root, followlinks=False):
            current = Path(directory)
            dirs[:] = [d for d in dirs if not exclusion_reason((current / d).relative_to(root), directory=True)]
            for name in names:
                path = current / name
                relative = str(path.relative_to(root))
                if exclusion_reason(Path(relative)) or relative == excluded:
                    continue
                info = path.lstat()
                if not stat.S_ISREG(info.st_mode):
                    raise TaskError('论文依赖发生变化，请检查符号链接或特殊文件。')
                signature = (info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
                previous = self.files.get(relative)
                value = previous[1] if previous and previous[0] == signature else digest(path.read_bytes())
                next_files[relative] = (signature, value)
                hashes[relative] = value
        self.files = next_files
        return digest(json.dumps(dict(sorted(hashes.items()))).encode())


def materialize(root: Path, files: dict[str, bytes]):
    root.mkdir(parents=True, exist_ok=False, mode=0o700)
    for name, data in files.items():
        p = safe_path(root, name)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)


def safe_path(root: Path, name: str) -> Path:
    rel = Path(name)
    if rel.is_absolute() or not rel.parts or any(p in {'..', '.'} or p.startswith('.') for p in rel.parts):
        raise TaskError('修改路径不在论文范围内。')
    target = root.joinpath(rel)
    for p in [root, *[root.joinpath(*rel.parts[:i]) for i in range(1, len(rel.parts) + 1)]]:
        if p.is_symlink():
            raise TaskError('检测到路径被替换为符号链接，已停止写入。')
    return target


def merged_files(base: dict[str, bytes], candidate: dict[str, bytes], current: dict[str, bytes]):
    """Use Git's three-way text merge, accepting only a zero-conflict result."""
    import tempfile
    result = dict(current)
    changes = []
    for name in sorted(base.keys() | candidate.keys()):
        old, new = base.get(name), candidate.get(name)
        if old == new:
            continue
        if exclusion_reason(Path(name)):
            raise TaskError('候选包含不支持或受保护的项目文件。')
        now = current.get(name)
        if now == old:
            final = new
        elif old is not None and new is not None and now is not None and is_text_asset(name, old, new, now):
            with tempfile.TemporaryDirectory(prefix='kimi-paper-merge-') as folder:
                paths = [Path(folder) / n for n in ('current', 'base', 'candidate')]
                for p, data in zip(paths, (now, old, new)):
                    p.write_bytes(data)
                proc = subprocess.run(['/usr/bin/git', 'merge-file', '-p', *map(str, paths)], capture_output=True)
                if proc.returncode != 0:
                    raise TaskError(f'{name} 已有重叠修改，请重新生成候选。')
                final = proc.stdout
        else:
            raise TaskError(f'{name} 已有新增、删除或二进制修改冲突，请重新生成候选。')
        if final is None:
            result.pop(name, None)
        else:
            result[name] = final
        changes.append(name)
    if not changes:
        raise TaskError('候选没有正文修改，不能采纳。')
    return result, changes


def is_text_asset(name, *contents):
    if Path(name).suffix.lower() not in EDIT_SUFFIXES:
        return False
    try:
        for content in contents:
            if b'\x00' in content:
                return False
            content.decode('utf-8')
    except UnicodeDecodeError:
        return False
    return True


def candidate_diff(base: dict[str, bytes], candidate: dict[str, bytes]):
    items = []
    for name in sorted(base.keys() | candidate.keys()):
        a, b = base.get(name, b''), candidate.get(name, b'')
        if a == b:
            continue
        if not is_text_asset(name, a, b):
            describe = lambda data: f'[文件：{len(data)} bytes · SHA-256 {digest(data)}]'
            items.append({'file': name, 'before': describe(a) if name in base else '[新增]',
                          'after': describe(b) if name in candidate else '[删除]', 'diff': '', 'binary': True})
            continue
        old, new = a.decode('utf-8', errors='replace'), b.decode('utf-8', errors='replace')
        items.append({'file': name, 'before': old, 'after': new,
                      'diff': ''.join(difflib.unified_diff(old.splitlines(True), new.splitlines(True),
                                                         fromfile='原文/' + name, tofile='候选/' + name))})
    return items


class Store:
    def __init__(self, root: Path):
        self.root = root
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.db = sqlite3.connect(root / 'tasks.sqlite3')
        os.chmod(root / 'tasks.sqlite3', 0o600)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('CREATE TABLE IF NOT EXISTS tasks (id TEXT PRIMARY KEY, dedup TEXT UNIQUE, body TEXT NOT NULL)')
        self.db.execute('CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, body TEXT NOT NULL)')
        self.db.commit()
        for task in self.all():
            if task['status'] in {'queued', 'running', 'waiting', 'applying'}:
                task['status'] = 'interrupted'
                task['message'] = '上次运行已中断，确认后可重新生成。'
                task.pop('permission', None)
                self.put(task)

    def all(self):
        return [json.loads(row[0]) for row in self.db.execute('SELECT body FROM tasks ORDER BY rowid DESC')]

    def get(self, task_id):
        row = self.db.execute('SELECT body FROM tasks WHERE id=?', (task_id,)).fetchone()
        if not row:
            raise TaskError('任务不存在。')
        return json.loads(row[0])

    def put(self, task, dedup=None):
        if dedup is not None:
            self.db.execute('INSERT INTO tasks VALUES(?,?,?)', (task['id'], dedup, json.dumps(task, ensure_ascii=False)))
        else:
            self.db.execute('UPDATE tasks SET body=? WHERE id=?', (json.dumps(task, ensure_ascii=False), task['id']))
        self.db.commit()

    def setting(self, key, default=None):
        row = self.db.execute('SELECT body FROM settings WHERE key=?', (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set_setting(self, key, value):
        self.db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)', (key, json.dumps(value, ensure_ascii=False)))
        self.db.commit()


def sandbox_profile(work: Path, manuscript: Path, writable: list[Path], protected: list[Path] = (),
                    unreadable: list[Path] = (), read_scope: tuple[Path, Path] | None = None,
                    gateway: tuple[str, int] | None = None,
                    deny_manuscript_read: bool = True) -> str:
    # Seatbelt strings do not interpret JSON's \uXXXX escapes.
    quote = lambda p: json.dumps(str(p.resolve()), ensure_ascii=False)
    # Default-deny also blocks Apple Events, task ports and Unix-domain delegation.
    lines = ['(version 1)', '(deny default)', '(allow process-exec process-fork)',
             '(allow signal (target self))', '(allow sysctl-read)', '(allow file-read*)',
             '(allow mach-lookup (global-name "com.apple.system.logger") (global-name "com.apple.system.opendirectoryd.libinfo") (global-name "com.apple.FSEvents"))',
             '(allow network-outbound (literal "/private/var/run/mDNSResponder"))',
             '(allow file-write* (literal "/dev/null"))']
    if gateway:
        host, port = gateway
        if host != '127.0.0.1' or not isinstance(port, int) or not 1 <= port <= 65535:
            raise TaskError('候选网络端口无效。')
        # With a proxy, all TCP is default-denied except this candidate's authenticated gateway.
        lines.append(f'(allow network-outbound (remote tcp "localhost:{port}"))')
    else:
        lines += ['(allow network-outbound (remote tcp))', '(deny network-outbound (remote ip "localhost:*"))']
    for p in writable:
        lines.append(f'(allow file-write* (subpath {quote(p)}))')
    if deny_manuscript_read:
        lines.append(f'(deny file-read* (subpath {quote(manuscript)}))')
    for p in protected:
        for path in {str(p.absolute()), str(p.resolve())}:
            lines.append(f'(deny file-write* (subpath {json.dumps(path, ensure_ascii=False)}))')
    for p in unreadable:
        lines.append(f'(deny file-read* (subpath {quote(p)}))')
    if read_scope:
        root, own = read_scope
        lines.append(f'(deny file-read-data (require-all (subpath {quote(root)}) (require-not (subpath {quote(own)}))))')
    return '\n'.join(lines)


def direct_project_write_denials(root: Path) -> str:
    """Deny sensitive names even when an agent tries to create them after startup."""
    prefix = re.escape(str(root.resolve()))
    names = ('[Aa][Uu][Tt][Hh]', '[Cc][Rr][Ee][Dd][Ee][Nn][Tt][Ii][Aa][Ll]',
             '[Cc][Rr][Ee][Dd][Ee][Nn][Tt][Ii][Aa][Ll][Ss]',
             '[Ss][Ee][Cc][Rr][Ee][Tt]', '[Ss][Ee][Cc][Rr][Ee][Tt][Ss]',
             '[Tt][Oo][Kk][Ee][Nn]', '[Tt][Oo][Kk][Ee][Nn][Ss]',
             '[Pp][Aa][Ss][Ss][Ww][Oo][Rr][Dd]', '[Pp][Aa][Ss][Ss][Ww][Oo][Rr][Dd][Ss]',
             '[Oo][Aa][Uu][Tt][Hh]',
             '[Aa][Pp][Ii][Kk][Ee][Yy]', '[Aa][Pp][Ii]_[Kk][Ee][Yy]')
    lines = [f'(deny file-write* (regex #"{prefix}/(?:[^/]+/)*\\.[^/]+(?:/.*)?$"))',
             f'(deny file-write* (regex #"{prefix}/(?:[^/]+/)*(?:[Aa][Gg][Ee][Nn][Tt][Ss]\\.[Mm][Dd]|[Cc][Ll][Aa][Uu][Dd][Ee]\\.[Mm][Dd])$"))']
    for name in names:
        for parent in (f'{prefix}/', f'{prefix}/.*/'):
            lines.extend((f'(deny file-write* (regex #"{parent}{name}$"))',
                          f'(deny file-write* (regex #"{parent}{name}[-_.].*"))',
                          f'(deny file-write* (regex #"{parent}{name}/.*"))',
                          f'(deny file-write* (regex #"{parent}.*[-_.]{name}$"))',
                          f'(deny file-write* (regex #"{parent}.*[-_.]{name}[-_.].*"))',
                          f'(deny file-write* (regex #"{parent}.*[-_.]{name}/.*"))'))
    return '\n'.join(lines)


def agent_environment(run: Path, tool: str):
    """Reuse auth by reference, with OAuth locking denied before a refresh starts."""
    home = run / 'home'
    home.mkdir(parents=True, exist_ok=True, mode=0o700)
    temp = run / 'tmp'
    temp.mkdir(exist_ok=True, mode=0o700)
    original = Path.home() / ('.kimi-code' if tool == 'kimi' else '.codex')
    links = ('config.toml', 'credentials', 'skills', 'AGENTS.md') if tool == 'kimi' else ('auth.json',)
    for name in links:
        dest = home / name
        if not dest.exists() and not dest.is_symlink() and (original / name).exists():
            dest.symlink_to(original / name, target_is_directory=(original / name).is_dir())
    (home / 'oauth').mkdir(exist_ok=True)
    # A clean environment prevents inherited API keys, proxies or service credentials.
    env = {k: os.environ[k] for k in ('PATH', 'HOME', 'LANG', 'LC_ALL', 'USER', 'LOGNAME', 'SHELL') if k in os.environ}
    env.update({'TMPDIR': str(temp), 'PYTHONUNBUFFERED': '1',
                'KIMI_CODE_HOME' if tool == 'kimi' else 'CODEX_HOME': str(home)})
    if tool == 'codex' and Path('/etc/ssl/cert.pem').is_file():
        # Public system CA bundle avoids granting access to user Keychain services.
        env['SSL_CERT_FILE'] = '/etc/ssl/cert.pem'
    protected = [home / 'oauth', home / 'config.toml', home / 'credentials', home / 'auth.json',
                 home / 'AGENTS.md', original]
    if tool == 'kimi':
        protected.append(home / 'skills')
    return env, protected


async def stop_group(proc):
    if proc is None:
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        await asyncio.wait_for(proc.wait(), 2)
    except asyncio.TimeoutError:
        pass
    # Descendants may outlive the leader; always target the original owned group.
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


class RPC:
    def __init__(self, proc, event, permission, control_fd=None, gateway=None):
        self.proc = proc
        self.event = event
        self.permission = permission
        self.control_fd = control_fd
        self.gateway = gateway
        self.pending = {}
        self.sequence = 0
        self.closed = False
        self.reader = asyncio.create_task(self.read())
        self.requests = set()

    async def send(self, obj):
        self.proc.stdin.write((json.dumps(obj) + '\n').encode())
        await self.proc.stdin.drain()

    async def request(self, method, params, timeout=60):
        if self.closed:
            raise TaskError('Agent 连接已关闭。')
        self.sequence += 1
        key = self.sequence
        fut = asyncio.get_running_loop().create_future()
        self.pending[key] = fut
        try:
            await self.send({'jsonrpc': '2.0', 'id': key, 'method': method, 'params': params})
            return await asyncio.wait_for(fut, timeout)
        finally:
            self.pending.pop(key, None)

    async def answer(self, obj):
        try:
            result = await self.permission(obj['method'], obj.get('params', {}))
            await self.send({'jsonrpc': '2.0', 'id': obj['id'], 'result': result})
        except asyncio.CancelledError:
            raise
        except Exception:
            await self.send({'jsonrpc': '2.0', 'id': obj['id'], 'error': {'code': -32601, 'message': 'Unsupported client operation'}})

    async def read(self):
        try:
            while line := await self.proc.stdout.readline():
                try:
                    obj = json.loads(line)
                except (ValueError, UnicodeError):
                    continue
                if not isinstance(obj, dict):
                    continue
                if 'method' in obj:
                    if 'id' in obj:
                        task = asyncio.create_task(self.answer(obj))
                        self.requests.add(task)
                        task.add_done_callback(self.requests.discard)
                    else:
                        await self.event(obj['method'], obj.get('params', {}))
                elif obj.get('id') in self.pending:
                    fut = self.pending[obj['id']]
                    if not fut.done():
                        if 'error' in obj:
                            fut.set_exception(TaskError('Agent 请求失败，请检查登录、模型或权限后重试。'))
                        else:
                            fut.set_result(obj.get('result', {}))
        finally:
            self.closed = True
            for fut in list(self.pending.values()):
                if not fut.done():
                    fut.set_exception(TaskError('Agent 连接已中断，请检查登录及运行环境。'))

    async def close(self):
        self.closed = True
        if self.control_fd is not None:
            os.close(self.control_fd)
            self.control_fd = None
        for task in list(self.requests):
            task.cancel()
        await stop_group(self.proc)
        self.reader.cancel()
        await asyncio.gather(self.reader, *self.requests, return_exceptions=True)
        if self.gateway:
            await self.gateway.close()
            self.gateway = None


async def start_agent(tool, run, manuscript, event, permission, unreadable=(), state_root=None):
    from paper_proxy import CodexProxy, ProxyError
    cwd = run / 'work'
    env, protected = agent_environment(run, tool)
    gateway = None
    try:
        if tool == 'codex':
            gateway = await CodexProxy.start()
            if gateway:
                env.update(gateway.environment)
    except ProxyError as error:
        raise TaskError(str(error)) from None
    profile = run / 'boundary.sb'
    if tool == 'kimi':
        executable = Path.home() / '.kimi-code/bin/kimi'
        agent_file = run / 'proposal-agent.md'
        args = [str(executable)]
        if agent_file.is_file():
            args += ['--agent-file', str(agent_file)]
        args += ['acp']
    elif tool == 'codex':
        executable = shutil.which('codex') or str(Path.home() / '.npm-global/bin/codex')
        args = [str(executable), 'app-server', '--stdio']
    else:
        raise TaskError('不支持的 Agent。')
    if not os.access(executable, os.X_OK):
        if gateway:
            await gateway.close()
        raise TaskError(f'{tool} 尚未安装。')
    if tool == 'kimi' and agent_file.is_file():
        from paper_prompt import PromptRPC
        profile.write_text(sandbox_profile(cwd, manuscript, [cwd, run / 'home', run / 'tmp'], protected, unreadable,
                                           (state_root, run) if state_root else None))
        return PromptRPC(executable, run, env, profile, event)
    control_read, control_write = os.pipe()
    try:
        profile.write_text(sandbox_profile(cwd, manuscript, [cwd, run / 'home', run / 'tmp'], protected, unreadable,
                                           (state_root, run) if state_root else None,
                                           gateway.endpoint if gateway else None))
        proc = await asyncio.create_subprocess_exec(sys.executable, str(Path(__file__).with_name('paper_guard.py')),
                                              str(control_read), '/usr/bin/sandbox-exec', '-f', str(profile), *args,
                                              cwd=cwd, env=env, stdin=asyncio.subprocess.PIPE,
                                              stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
                                              start_new_session=True, pass_fds=(control_read,), limit=8 * 1024 * 1024)
    except BaseException:
        os.close(control_write)
        if gateway:
            await gateway.close()
        raise
    finally:
        os.close(control_read)
    rpc = RPC(proc, event, permission, control_write, gateway)
    try:
        if tool == 'kimi':
            await rpc.request('initialize', {'protocolVersion': 1, 'clientCapabilities': {},
                                            'clientInfo': {'name': 'kimi-paper', 'version': '0.2.0'}})
        else:
            await rpc.request('initialize', {'clientInfo': {'name': 'kimi-paper', 'version': '0.2.0'},
                                            'capabilities': {'experimentalApi': True}})
            await rpc.send({'jsonrpc': '2.0', 'method': 'initialized', 'params': {}})
        return rpc
    except BaseException:
        await rpc.close()
        raise
