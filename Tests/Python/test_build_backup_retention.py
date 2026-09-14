import importlib.util, tempfile, unittest, sys
from pathlib import Path
from unittest.mock import patch
from subprocess import CompletedProcess
SOURCE=Path(__file__).resolve().parents[2] / 'scripts/prune_build_backups.py'
spec=importlib.util.spec_from_file_location('prune',SOURCE);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
class Tests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.dist=self.root/'dist';self.dist.mkdir();(self.root/'scripts').mkdir()
  self.fp=patch.object(m,'__file__',str(self.root/'scripts/prune_build_backups.py'));self.fp.start()
 def tearDown(self):self.fp.stop();self.temp.cleanup()
 def bundle(self,name):
  p=self.dist/name;app=p/'Kimi Paper.app/Contents';(app/'MacOS').mkdir(parents=True);(app/'Info.plist').write_text('test');(app/'MacOS/KimiPaper').write_text('test');return p
 def run_prune(self,*args,opened=False):
  with patch.object(sys,'argv',['prune',*args]),patch.object(m.subprocess,'run',return_value=CompletedProcess([],0 if opened else 1,'123' if opened else '','')):
   m.main()
 def test_keep_two_and_current_manual_installed(self):
  for n in ['previous-000001','previous-000002','previous-000003']:self.bundle(n)
  old=self.bundle('installed-backup-20260906-2055');(self.dist/'Kimi Paper.app').mkdir();keep,remove=m.candidates(self.dist,True)
  self.run_prune('--apply','--include-installed')
  self.assertTrue(all(p.exists() for p in keep));self.assertTrue(all(not p.exists() for p in remove));self.assertTrue((self.dist/'Kimi Paper.app').exists())
 def test_dry_run_and_default_preserve_installed(self):
  for n in ['previous-000001','previous-000002','previous-000003']:self.bundle(n)
  old=self.bundle('installed-backup-20260906-2055');self.run_prune();self.assertEqual(len(list(self.dist.iterdir())),4)
  self.run_prune('--apply');self.assertTrue(old.exists())
 def test_open_backup_blocks_all(self):
  for n in ['previous-000001','previous-000002','previous-000003']:self.bundle(n)
  with self.assertRaises(RuntimeError):self.run_prune('--apply',opened=True)
  self.assertEqual(len(list(self.dist.iterdir())),3)
 def test_symlink_and_unexpected_data_rejected(self):
  self.bundle('previous-000001');p=self.bundle('previous-000002');(p/'important.txt').write_text('keep')
  with self.assertRaises(RuntimeError):self.run_prune('--apply')
  outside=self.root/'outside';outside.mkdir();(self.dist/'previous-000003').symlink_to(outside)
  with self.assertRaises(RuntimeError):self.run_prune('--apply')
if __name__ == '__main__':
 unittest.main()
