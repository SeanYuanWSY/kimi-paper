"""Native Kimi prompt transport for the enforced no-tool suggestion profile.

Kimi 0.41.0 accepts --agent-file in prompt mode but ignores it in ACP mode.
This adapter preserves the engine's small session interface without starting ACP.
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import sys
import uuid

from paper_tasks import TaskError, stop_group


class PromptRPC:
    def __init__(self, executable, run, env, profile, event):
        self.executable, self.run, self.env, self.profile = executable, run, env, profile
        self.event = event
        self.model = None
        self.proc = None
        self.control_fd = None
        self.closed = False

    async def request(self, method, params, timeout=3600):
        if self.closed:
            raise TaskError('Kimi 建议连接已关闭。')
        if method == 'session/new':
            return {'sessionId': uuid.uuid4().hex}
        if method == 'session/set_config_option':
            if params['configId'] == 'model':
                self.model = params['value']
            elif params['configId'] != 'mode' or params['value'] != 'default':
                raise TaskError('直接修改不支持更改执行权限。')
            return {}
        if method != 'session/prompt' or not self.model or self.proc:
            raise TaskError('直接修改请求无效。')
        blocks = params.get('prompt', [])
        if any(v.get('type') != 'text' or not isinstance(v.get('text'), str) for v in blocks):
            raise TaskError('直接修改只接受论文文字。')
        prompt = '\n'.join(v['text'] for v in blocks)
        if len(prompt.encode('utf-8')) > 180_000:
            raise TaskError('本次上下文超过原生直接修改入口上限，请选择深入处理。')
        status_path = self.run / ('prompt-exit-' + uuid.uuid4().hex)
        if status_path.exists() or status_path.is_symlink():
            raise TaskError('本次运行状态文件已存在，请重新生成建议。')
        read_fd, self.control_fd = os.pipe()
        try:
            self.proc = await asyncio.create_subprocess_exec(
                sys.executable, str(Path(__file__).with_name('paper_guard.py')), str(read_fd),
                '--exit-status', str(status_path),
                '/usr/bin/sandbox-exec', '-f', str(self.profile), str(self.executable),
                '--agent-file', str(self.run / 'proposal-agent.md'), '-m', self.model,
                '-p', prompt, '--output-format', 'stream-json', cwd=self.run / 'work', env=self.env,
                stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL, start_new_session=True,
                pass_fds=(read_fd,), limit=8 * 1024 * 1024)
        finally:
            os.close(read_fd)
        try:
            async with asyncio.timeout(timeout):
                while line := await self.proc.stdout.readline():
                    try:
                        message = json.loads(line)
                    except (ValueError, UnicodeError):
                        raise TaskError('Kimi 返回了无法识别的输出，请检查版本兼容性。') from None
                    if not isinstance(message, dict):
                        raise TaskError('Kimi 输出格式无效。')
                    if message.get('role') == 'tool' or message.get('tool_calls'):
                        raise TaskError('直接修改模式出现工具调用，已停止；请检查 Kimi 版本。')
                    if message.get('role') == 'assistant' and isinstance(message.get('content'), str):
                        # Native 0.41 emits this hook banner as an assistant
                        # message, with no origin field. Never parse it as edits.
                        if message['content'].startswith('UserPromptSubmit hook\n\n'):
                            continue
                        await self.event('session/update', {'update': {'sessionUpdate': 'agent_message_chunk',
                                                                     'content': {'text': message['content']}}})
                await self.proc.wait()
                # The supervisor intentionally kills its own process group after
                # the child exits, so its return code is not the Kimi exit code.
                if status_path.is_symlink() or not status_path.is_file() or status_path.read_text() != '0':
                    raise TaskError('Kimi 未完成本次建议，请检查模型连接或登录状态。')
        finally:
            await self.close()
        return {}

    async def close(self):
        self.closed = True
        if self.control_fd is not None:
            os.close(self.control_fd)
            self.control_fd = None
        await stop_group(self.proc)
