from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'Resources'))
from paper_history import History
from paper_engine import Engine
from paper_tasks import TaskError, materialize


class HistoryTests(unittest.TestCase):
    def test_complete_snapshots_leave_user_index_and_head_untouched(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paper = root/'paper'; paper.mkdir()
            subprocess.run(['/usr/bin/git','init','-q','--template=',str(paper)],check=True)
            (paper/'main.tex').write_bytes(b'user staged content')
            subprocess.run(['/usr/bin/git','-C',str(paper),'add','main.tex'],check=True)
            index = (paper/'.git/index').read_bytes()
            head = (paper/'.git/HEAD').read_bytes()
            history = History(root/'state'); history.root.parent.mkdir()
            commits = history.record({'main.tex':b'old','sections/a b.tex':b'nested'},
                                     {'main.tex':b'new','sections/a b.tex':b'nested'},'a'*32)
            self.assertEqual(history.git('show', commits['before']+':main.tex'),b'old')
            self.assertEqual(history.git('show', commits['after']+':sections/a b.tex'),b'nested')
            self.assertEqual((paper/'.git/index').read_bytes(),index)
            self.assertEqual((paper/'.git/HEAD').read_bytes(),head)
            self.assertEqual((paper/'main.tex').read_bytes(),b'user staged content')

    def test_rejects_traversal_and_hidden_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            history = History(Path(directory))
            for name in ['../main.tex','.env','a/.git/config','/tmp/file','a//b']:
                with self.subTest(name=name), self.assertRaises(TaskError):
                    history.snapshot({name:b'data'},'b'*32+'/before')


class HistoryFailureTests(unittest.IsolatedAsyncioTestCase):
    async def test_snapshot_failure_cannot_publish_manuscript_or_successful_journal(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);paper=root/'paper';paper.mkdir();(paper/'main.tex').write_bytes(b'old')
            engine=Engine(paper,'main.tex',root/'state');tid='c'*32
            engine.store.put({'id':tid,'status':'ready','comment':{'id':'c'}},'fixture')
            run=engine.run_dir(tid)
            materialize(run/'base',{'main.tex':b'old'});materialize(run/'result',{'main.tex':b'new'})
            async def compile(*args):return {'success':True}
            try:
                with patch('paper_engine.compile_copy',compile), patch.object(engine.history,'record',side_effect=TaskError('disk full')):
                    with self.assertRaises(TaskError):await engine.apply(tid)
                self.assertEqual((paper/'main.tex').read_bytes(),b'old')
                self.assertEqual(engine.transactions(),[])
            finally:await engine.close()
