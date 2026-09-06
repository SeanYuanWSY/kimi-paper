"""Private local Git snapshots, independent of the manuscript's repository/index."""
from __future__ import annotations

import os
from pathlib import Path, PurePosixPath
import re
import subprocess

from paper_tasks import TaskError


class History:
    def __init__(self, state):
        self.root = Path(state) / 'history.git'

    def git(self, *args, data=None):
        env = {'PATH': '/usr/bin:/bin', 'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': '/dev/null',
               'GIT_TERMINAL_PROMPT': '0', 'GIT_AUTHOR_NAME': 'Kimi Paper', 'GIT_COMMITTER_NAME': 'Kimi Paper',
               'GIT_AUTHOR_EMAIL': 'local@kimi-paper.invalid', 'GIT_COMMITTER_EMAIL': 'local@kimi-paper.invalid'}
        try:
            result = subprocess.run(['/usr/bin/git', '--git-dir=' + str(self.root), '-c', 'core.hooksPath=/dev/null',
                                     *args], input=data, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                    env=env, timeout=30, check=True)
        except (OSError, subprocess.SubprocessError):
            raise TaskError('本地版本保存未完成，正文尚未写入，请检查磁盘空间后重试。') from None
        return result.stdout

    def ensure(self):
        if self.root.is_symlink():
            raise TaskError('本地版本目录不能是符号链接。')
        if not self.root.exists():
            self.root.mkdir(mode=0o700)
            self.git('init', '--bare', '--template=', '--object-format=sha1', str(self.root))

    def snapshot(self, files, reference, parent=None):
        if not re.fullmatch(r'[0-9a-f]{32}/(before|after)', reference):
            raise TaskError('本地版本标识无效。')
        if parent is not None and not re.fullmatch(r'[0-9a-f]{40}', parent):
            raise TaskError('本地版本引用无效。')
        self.ensure()
        tree = {}
        for name, content in files.items():
            path = PurePosixPath(name)
            if (not name or path.is_absolute() or str(path) != name or '\x00' in name or '\\' in name
                    or any(p.startswith('.') for p in path.parts) or not isinstance(content, bytes)):
                raise TaskError('无法为此论文路径保存本地版本。')
            node = tree
            for part in path.parts[:-1]:
                node = node.setdefault(part, {})
                if not isinstance(node, dict):
                    raise TaskError('本地版本路径冲突。')
            if path.name in node:
                raise TaskError('本地版本路径冲突。')
            node[path.name] = self.git('hash-object', '-w', '--stdin', data=content).strip()

        def write_tree(node):
            entries = []
            for name, value in sorted(node.items()):
                directory = isinstance(value, dict)
                oid = write_tree(value) if directory else value
                entries.append((b'040000 tree ' if directory else b'100644 blob ') + oid + b'\t' + name.encode() + b'\x00')
            return self.git('mktree', '-z', data=b''.join(entries)).strip()
        tree_id = write_tree(tree).decode('ascii')
        args = ['commit-tree', tree_id]
        if parent:
            args += ['-p', parent]
        commit = self.git(*args, data=('Kimi Paper local snapshot ' + reference + '\n').encode()).strip().decode('ascii')
        self.git('update-ref', 'refs/checkpoints/' + reference, commit)
        return commit

    def record(self, current, final, transaction):
        before = self.snapshot(current, transaction + '/before')
        after = self.snapshot(final, transaction + '/after', parent=before)
        return {'before': before, 'after': after}
