import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'Resources'))
from paper_git import PaperGit,github_url
from paper_tasks import TaskError


class GitTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name).resolve()
        self.paper=self.root/'paper';self.paper.mkdir();self.git=PaperGit(self.paper)

    async def asyncTearDown(self):self.temp.cleanup()

    async def execute(self,action,**body):
        preview=await self.git.prepare(action,body)
        return await self.git.execute(preview['token'])

    async def initialize(self):
        await self.execute('init')
        await self.git.git('config','user.name','Fixture Author')
        await self.git.git('config','user.email','fixture@example.invalid')

    async def test_init_stage_commit_and_manual_branch(self):
        await self.initialize()
        (self.paper/'main.tex').write_text('paper')
        (self.paper/'unselected.tex').write_text('not staged')
        await self.execute('stage',files=['main.tex'])
        names,tree=await self.git.staged();self.assertEqual(names,['main.tex'])
        preview=await self.git.prepare('commit',{'message':'Initial paper'})
        self.assertEqual(preview['files'],['main.tex'])
        await self.git.execute(preview['token'])
        self.assertEqual((await self.git.git('show','HEAD:main.tex')).decode(),'paper')
        with self.assertRaises(TaskError):await self.git.prepare('branch',{'branch':'paper/review'})
        (self.paper/'unselected.tex').unlink()
        await self.execute('branch',branch='paper/review')
        self.assertEqual((await self.git.status())['branch'],'paper/review')

    async def test_ancestor_and_nested_repo_boundaries(self):
        subprocess.run(['/usr/bin/git','init','-q','--template=',str(self.root)],check=True)
        status=await self.git.status();self.assertFalse(status['available']);self.assertTrue(status['canInit'])
        await self.initialize()
        child=self.paper/'nested';child.mkdir()
        subprocess.run(['/usr/bin/git','init','-q','--template=',str(child)],check=True)
        self.assertFalse((await self.git.status())['available'])

    async def test_changed_file_requires_new_preview_and_secret_is_not_staged(self):
        await self.initialize();file=self.paper/'main.tex';file.write_text('old')
        p=await self.git.prepare('stage',{'files':['main.tex']});file.write_text('new')
        with self.assertRaises(TaskError):await self.git.execute(p['token'])
        self.assertEqual(await self.git.git('diff','--cached','--name-only'),b'')
        file.write_text('sk-'+'A'*30)
        with self.assertRaises(TaskError):await self.git.prepare('stage',{'files':['main.tex']})

    async def test_existing_staged_files_are_preserved_and_previewed(self):
        await self.initialize()
        for name in ['a.tex','b.tex']:(self.paper/name).write_text(name)
        await self.execute('stage',files=['a.tex'])
        a=await self.git.git('show',':a.tex')
        await self.execute('stage',files=['b.tex'])
        self.assertEqual(await self.git.git('show',':a.tex'),a)
        p=await self.git.prepare('commit',{'message':'Both explicitly previewed'})
        self.assertEqual(set(p['files']),{'a.tex','b.tex'})

    async def test_filter_cannot_execute_and_filtered_stage_is_refused(self):
        await self.initialize()
        marker=self.root/'FILTER_EXECUTED'
        await self.git.git('config','filter.fixture.clean','touch '+str(marker))
        (self.paper/'.gitattributes').write_text('*.tex filter=fixture\n')
        (self.paper/'main.tex').write_text('paper')
        await self.git.status()
        with self.assertRaises(TaskError):await self.git.prepare('stage',{'files':['main.tex']})
        self.assertFalse(marker.exists())

    async def test_literal_paths_and_single_use_confirmation(self):
        await self.initialize();name=':(glob)main.tex';(self.paper/name).write_text('literal')
        p=await self.git.prepare('stage',{'files':[name]});await self.git.execute(p['token'])
        self.assertEqual(await self.git.git('show',':'+name),b'literal')
        with self.assertRaises(TaskError):await self.git.execute(p['token'])

    async def test_history_scans_deleted_secret_file(self):
        await self.initialize();(self.paper/'main.tex').write_text('sk-'+'F'*30)
        await self.git.git('add','main.tex');await self.git.git('commit','-m','fixture secret')
        (self.paper/'main.tex').write_text('safe now')
        await self.git.git('add','main.tex');await self.git.git('commit','-m','remove from working copy')
        head=(await self.git.git('rev-parse','HEAD')).decode().strip()
        with self.assertRaises(TaskError):await self.git.scan_history(head)

    async def test_replace_object_cannot_hide_secret_from_history_scan(self):
        await self.initialize();(self.paper/'main.tex').write_text('sk-'+'Q'*30)
        await self.git.git('add','main.tex');await self.git.git('commit','-m','fixture')
        head=(await self.git.git('rev-parse','HEAD')).decode().strip()
        old=(await self.git.git('rev-parse','HEAD:main.tex')).decode().strip()
        safe=(await self.git.git('hash-object','-w','--stdin',data=b'safe replacement')).decode().strip()
        await self.git.git('replace',old,safe)
        with self.assertRaises(TaskError):await self.git.scan_history(head)

    async def test_index_symlink_and_git_metadata_symlink_are_rejected(self):
        await self.initialize();(self.paper/'link.tex').symlink_to('../outside')
        await self.git.git('add','link.tex')
        with self.assertRaises(TaskError):await self.git.staged()
        (self.paper/'.git').rename(self.root/'metadata')
        (self.paper/'.git').symlink_to(self.root/'metadata',target_is_directory=True)
        with self.assertRaises(TaskError):await self.git.status()

    async def test_attribute_change_between_preview_and_stage_is_rechecked(self):
        await self.initialize();(self.paper/'main.tex').write_text('paper')
        attributes=self.paper/'.gitattributes';attributes.write_text('*.tex -filter\n')
        p=await self.git.prepare('stage',{'files':['main.tex']})
        attributes.write_text('*.tex filter=unexpected\n')
        with self.assertRaises(TaskError):await self.git.execute(p['token'])
        self.assertEqual(await self.git.git('diff','--cached','--name-only'),b'')


class RemoteTests(unittest.TestCase):
    def test_accepts_github_only_without_credentials(self):
        for url in ['https://github.com/user/paper.git','git@github.com:user/paper.git','ssh://git@github.com/user/paper']:
            self.assertEqual(github_url(url),'https://github.com/user/paper')
        for url in ['https://token@github.com/user/paper','https://github.com.evil/user/paper','file:///tmp/repo',
                    'ssh://user@github.com/user/paper','ssh://git@github.com:22/user/paper','https://github.com/user/paper?key=secret']:
            with self.assertRaises(TaskError):github_url(url)
