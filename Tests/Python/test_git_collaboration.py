import asyncio
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'Resources'))
from paper_git import PaperGit
from paper_tasks import TaskError


def raw(root,*args):
    return subprocess.check_output(['/usr/bin/git','-c','core.hooksPath=/dev/null','-c','commit.gpgsign=false',
                                    '-C',str(root),*args],stderr=subprocess.DEVNULL)


class LocalTransport(PaperGit):
    """Exercise real local Git merge/push semantics without any GitHub traffic."""
    def __init__(self,root):
        super().__init__(root);self.remote_calls=[]
    async def remote(self):return ('https://github.com/fixture/paper.git',)*2
    async def git(self,*args,**kwargs):
        if any(v in {'fetch','push'} for v in args[:3]):
            self.remote_calls.append(args)
            return await asyncio.to_thread(raw,self.root,*args)
        return await super().git(*args,**kwargs)


class CollaborationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name).resolve()
        self.origin=self.root/'origin.git';self.paper=self.root/'paper';self.other=self.root/'collaborator'
        raw(self.root,'init','--bare','--initial-branch=main',str(self.origin))
        raw(self.root,'clone',str(self.origin),str(self.paper))
        for root in [self.paper]:
            raw(root,'config','user.name','Fixture');raw(root,'config','user.email','fixture@example.invalid')
        (self.paper/'main.tex').write_text('original\n')
        raw(self.paper,'add','main.tex');raw(self.paper,'commit','-m','initial');raw(self.paper,'push','-u','origin','main')
        raw(self.root,'clone',str(self.origin),str(self.other))
        raw(self.other,'config','user.name','Collaborator');raw(self.other,'config','user.email','other@example.invalid')
        self.git=LocalTransport(self.paper)
    async def asyncTearDown(self):self.temp.cleanup()
    def remote_change(self):
        (self.other/'main.tex').write_text('remote edit\n')
        raw(self.other,'add','main.tex');raw(self.other,'commit','-m','collaborator edit');raw(self.other,'push','origin','main')

    async def test_preview_does_not_fetch_then_confirm_fast_forwards(self):
        self.remote_change()
        preview=await self.git.prepare('pull',{})
        self.assertEqual(self.git.remote_calls,[])
        self.assertEqual((self.paper/'main.tex').read_text(),'original\n')
        await self.git.execute(preview['token'])
        self.assertEqual((self.paper/'main.tex').read_text(),'remote edit\n')
        self.assertEqual(raw(self.paper,'rev-parse','HEAD'),raw(self.other,'rev-parse','HEAD'))
        self.assertEqual(self.git.remote_calls[0],('fetch','--no-tags','origin','+refs/heads/*:refs/remotes/origin/*'))

    async def test_diverged_branch_does_not_merge_or_overwrite(self):
        self.remote_change()
        (self.paper/'main.tex').write_text('local edit\n')
        raw(self.paper,'add','main.tex');raw(self.paper,'commit','-m','local edit')
        head=raw(self.paper,'rev-parse','HEAD')
        preview=await self.git.prepare('pull',{})
        with self.assertRaises(TaskError):await self.git.execute(preview['token'])
        self.assertEqual(raw(self.paper,'rev-parse','HEAD'),head)
        self.assertEqual((self.paper/'main.tex').read_text(),'local edit\n')
        state=await self.git.status();self.assertEqual((state['ahead'],state['behind']),(1,1))

    async def test_manual_push_publishes_only_explicit_same_branch(self):
        (self.paper/'main.tex').write_text('local proposal accepted\n')
        raw(self.paper,'add','main.tex');raw(self.paper,'commit','-m','accepted edit')
        old=raw(self.origin,'rev-parse','main')
        preview=await self.git.prepare('push',{})
        self.assertEqual(raw(self.origin,'rev-parse','main'),old)
        await self.git.execute(preview['token'])
        self.assertEqual(raw(self.origin,'rev-parse','main'),raw(self.paper,'rev-parse','HEAD'))
        self.assertIn(raw(self.paper,'rev-parse','HEAD').decode().strip()+':refs/heads/main',self.git.remote_calls[-1])
        self.assertNotIn('--force',self.git.remote_calls[-1])
