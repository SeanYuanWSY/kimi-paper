import asyncio
import base64
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'Resources'))
from paper_engine import Engine
from paper_tasks import TaskError,materialize

class TransactionTests(unittest.IsolatedAsyncioTestCase):
 async def asyncSetUp(self):
  self.temp=tempfile.TemporaryDirectory(); self.root=Path(self.temp.name).resolve()
  self.paper=self.root/'paper';self.paper.mkdir();(self.paper/'main.tex').write_bytes(b'old\n')
  self.e=Engine(self.paper,'main.tex',self.root/'state');self.id='a'*32
  run=self.e.run_dir(self.id);materialize(run/'base',{'main.tex':b'old\n'});materialize(run/'result',{'main.tex':b'new\n'})
  self.e.store.put({'id':self.id,'status':'ready','comment':{'id':'c'},'baseline':'test'},'unique')
 async def asyncTearDown(self):
  await self.e.close();self.temp.cleanup()
 async def test_cancel_during_apply_cannot_change_state(self):
  entered=asyncio.Event();release=asyncio.Event()
  async def compile(*args):entered.set();await release.wait();return {'success':True}
  with patch('paper_engine.compile_copy',compile):
   action=asyncio.create_task(self.e.apply(self.id));await entered.wait()
   self.assertEqual(self.e.store.get(self.id)['status'],'applying')
   with self.assertRaises(TaskError):await self.e.cancel(self.id)
   release.set();await action
  self.assertEqual((self.paper/'main.tex').read_bytes(),b'new\n')
 async def test_undo_callback_failure_rolls_back_and_can_retry(self):
  async def compile(*args):return {'success':True}
  with patch('paper_engine.compile_copy',compile):
   await self.e.apply(self.id)
   async def fail(*args):raise TaskError('PDF failed')
   self.e.on_applied=fail
   with self.assertRaises(TaskError):await self.e.undo(self.id)
   self.assertEqual((self.paper/'main.tex').read_bytes(),b'new\n')
   self.assertEqual(self.e.store.get(self.id)['status'],'applied')
   self.e.on_applied=None;await self.e.undo(self.id)
   self.assertEqual((self.paper/'main.tex').read_bytes(),b'old\n')
   self.assertEqual(self.e.store.get(self.id)['status'],'undone')
 async def test_external_edit_during_compile_is_not_overwritten(self):
  async def compile(*args):
   (self.paper/'main.tex').write_bytes(b'user edit\n');return {'success':True}
  with patch('paper_engine.compile_copy',compile):
   with self.assertRaises(TaskError):await self.e.apply(self.id)
  self.assertEqual((self.paper/'main.tex').read_bytes(),b'user edit\n')
 async def test_edited_comment_cannot_be_applied(self):
  self.e.update(self.id,commentChanged=True)
  with self.assertRaises(TaskError):await self.e.apply(self.id)
  self.assertEqual((self.paper/'main.tex').read_bytes(),b'old\n')

class RecoveryTests(unittest.TestCase):
 def test_committed_undo_wins_over_older_apply_journal(self):
  from paper_tasks import atomic_json
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);paper=root/'paper';paper.mkdir();(paper/'main.tex').write_bytes(b'old')
   e=Engine(paper,'main.tex',root/'state');tid='b'*32
   e.store.put({'id':tid,'status':'applying','journal':'original'},'recovery')
   files={'main.tex':{'before':base64.b64encode(b'old').decode(),'after':base64.b64encode(b'new').decode()}}
   atomic_json(root/'state/journals/original.json',{'id':'original','task':tid,'operation':'apply','state':'committed','files':files})
   atomic_json(root/'state/journals/reverse.json',{'id':'reverse','task':tid,'operation':'undo','state':'committed','files':files})
   e.store.db.close();e=Engine(paper,'main.tex',root/'state')
   self.assertEqual(e.store.get(tid)['status'],'undone');e.store.db.close()
 def test_incomplete_undo_recovers_original_adoption(self):
  from paper_tasks import atomic_json
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);paper=root/'paper';paper.mkdir();(paper/'main.tex').write_bytes(b'old')
   e=Engine(paper,'main.tex',root/'state');tid='c'*32
   e.store.put({'id':tid,'status':'applying','journal':'original'},'recovery')
   files={'main.tex':{'before':base64.b64encode(b'new').decode(),'after':base64.b64encode(b'old').decode()}}
   atomic_json(root/'state/journals/reverse.json',{'id':'reverse','task':tid,'operation':'undo','state':'writing','files':files})
   e.store.db.close();e=Engine(paper,'main.tex',root/'state')
   self.assertEqual(e.store.get(tid)['status'],'applied');self.assertEqual(e.store.get(tid)['journal'],'original')
   self.assertEqual((paper/'main.tex').read_bytes(),b'new');e.store.db.close()
