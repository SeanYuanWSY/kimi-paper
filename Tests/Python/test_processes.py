import asyncio
import os
from pathlib import Path
import signal
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'Resources'))
from paper_tasks import RPC, TaskError, stop_group


class ProcessTests(unittest.IsolatedAsyncioTestCase):
    async def test_guard_eof_stops_child_without_stdin_closure(self):
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / 'child.pid'
            reader, writer = os.pipe()
            guard = Path(__file__).resolve().parents[2] / 'Resources/paper_guard.py'
            child = "import os,time,pathlib,sys;pathlib.Path(sys.argv[1]).write_text(str(os.getpid()));time.sleep(30)"
            process = await asyncio.create_subprocess_exec(
                sys.executable, str(guard), str(reader), sys.executable, '-c', child, str(marker),
                pass_fds=(reader,), start_new_session=True, stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
            os.close(reader)
            try:
                for _ in range(100):
                    if marker.exists():
                        break
                    await asyncio.sleep(.02)
                self.assertTrue(marker.exists())
                child_pid = int(marker.read_text())
                os.close(writer); writer = None
                await asyncio.wait_for(process.wait(), 4)
                with self.assertRaises(ProcessLookupError):
                    os.kill(child_pid, 0)
                self.assertNotIn(b'ValueError', await process.stderr.read())
            finally:
                if writer is not None:
                    os.close(writer)
                await stop_group(process)

    async def test_disconnect_fails_pending_request_promptly(self):
        process = await asyncio.create_subprocess_exec(
            sys.executable, '-c', 'import sys;sys.stdin.readline()',
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, start_new_session=True)
        async def ignore(*args):
            pass
        rpc = RPC(process, ignore, ignore)
        try:
            with self.assertRaises(TaskError):
                await asyncio.wait_for(rpc.request('test', {}), 3)
            self.assertFalse(rpc.pending)
        finally:
            await rpc.close()

    async def test_close_cancels_pending_permission(self):
        code = '''import sys,json,time
sys.stdin.readline()
print(json.dumps({'jsonrpc':'2.0','id':44,'method':'permission','params':{}}),flush=True)
time.sleep(30)
'''
        process = await asyncio.create_subprocess_exec(
            sys.executable, '-c', code, stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, start_new_session=True)
        waiting, cancelled = asyncio.Event(), asyncio.Event()
        async def ignore(*args):
            pass
        async def permission(*args):
            waiting.set()
            try:
                await asyncio.Future()
            finally:
                cancelled.set()
        rpc = RPC(process, ignore, permission)
        pending = asyncio.create_task(rpc.request('test', {}))
        try:
            await asyncio.wait_for(waiting.wait(), 3)
            await rpc.close()
            await asyncio.wait_for(cancelled.wait(), 3)
            with self.assertRaises(TaskError):
                await pending
        finally:
            await rpc.close()


if __name__ == '__main__':
    unittest.main()
