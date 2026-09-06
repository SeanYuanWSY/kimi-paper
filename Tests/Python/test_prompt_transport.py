import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'Resources'))
from paper_prompt import PromptRPC
from paper_tasks import TaskError


class PromptTransportTests(unittest.IsolatedAsyncioTestCase):
    async def run_fixture(self, messages, exit_code=0):
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory).resolve()
            (run/'work').mkdir()
            fixture=run/'fake-kimi'
            fixture.write_text('#!/usr/bin/python3\nimport sys\n' +
                               '\n'.join('print(' + repr(json.dumps(m)) + ', flush=True)' for m in messages) +
                               '\nsys.exit(' + str(exit_code) + ')\n')
            fixture.chmod(0o700)
            boundary=run/'boundary.sb';boundary.write_text('(version 1)\n(allow default)\n')
            events=[]
            async def event(method,params):events.append(params['update']['content']['text'])
            rpc=PromptRPC(fixture,run,{'PATH':'/usr/bin:/bin'},boundary,event)
            await rpc.request('session/new',{})
            await rpc.request('session/set_config_option',{'configId':'model','value':'fixture'})
            try:
                await rpc.request('session/prompt',{'prompt':[{'type':'text','text':'fixture'}]},timeout=5)
                self.assertTrue(rpc.closed)
                self.assertIsNone(rpc.control_fd)
                self.assertEqual(next(run.glob('prompt-exit-*')).read_text(),str(exit_code))
                return events
            finally:await rpc.close()

    async def test_child_success_is_not_supervisor_kill_status(self):
        events=await self.run_fixture([{'role':'meta','type':'system.version','version':'fixture'},
                                      {'role':'assistant','content':'UserPromptSubmit hook\n\n{}'},
                                      {'role':'assistant','content':'valid suggestion'}])
        self.assertEqual(events,['valid suggestion'])

    async def test_nonzero_child_exit_cannot_publish_output(self):
        with self.assertRaises(TaskError):
            await self.run_fixture([{'role':'assistant','content':'partial suggestion'}],exit_code=1)

    async def test_unexpected_tool_call_is_rejected(self):
        for message in [{'role':'tool','content':'not found'},
                        {'role':'assistant','tool_calls':[{'name':'Read'}]}]:
            with self.subTest(message=message),self.assertRaises(TaskError):
                await self.run_fixture([message])
