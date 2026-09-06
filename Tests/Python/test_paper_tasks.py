import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import unittest
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'Resources'))
from paper_tasks import Store, TaskError, source_files, materialize, merged_files, sandbox_profile


class CandidateTests(unittest.TestCase):
    def test_source_has_no_shared_inode_or_project_tools(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / 'paper'; root.mkdir()
            (root / 'main.tex').write_text('original')
            (root / '.kimi-code').mkdir(); (root / '.kimi-code/mcp.json').write_text('private')
            target = Path(d) / 'copy'; materialize(target, source_files(root))
            self.assertFalse((target / '.kimi-code').exists())
            self.assertNotEqual((root / 'main.tex').stat().st_ino, (target / 'main.tex').stat().st_ino)

    def test_symlink_dependency_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / 'main.tex').symlink_to('/etc/hosts')
            with self.assertRaises(TaskError): source_files(Path(d))

    def test_nonoverlap_merge_and_overlap_conflict(self):
        base = {'main.tex': b'one\ntwo\nthree\nfour\nfive\nsix\nseven\n'}
        candidate = {'main.tex': base['main.tex'].replace(b'one', b'ONE')}
        current = {'main.tex': base['main.tex'].replace(b'seven', b'SEVEN')}
        merged, changed = merged_files(base, candidate, current)
        self.assertIn(b'ONE', merged['main.tex']); self.assertIn(b'SEVEN', merged['main.tex'])
        with self.assertRaises(TaskError):
            merged_files(base, candidate, {'main.tex': base['main.tex'].replace(b'one', b'OTHER')})

    def test_restart_does_not_repeat_running_task(self):
        with tempfile.TemporaryDirectory() as d:
            s = Store(Path(d)); s.put({'id': 'x', 'status': 'running'}, 'unique')
            s.db.close(); s = Store(Path(d))
            self.assertEqual(s.get('x')['status'], 'interrupted')
            self.assertEqual(len(s.all()), 1)
            s.db.close()

    @unittest.skipUnless(sys.platform == 'darwin', 'macOS sandbox')
    def test_os_boundary_blocks_manuscript_and_loopback(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d).resolve() / '论文 空格'; root.mkdir(); manuscript = root / 'main'; manuscript.mkdir()
            secret = manuscript / 'main.tex'; secret.write_text('original')
            work = root / 'candidate'; work.mkdir()
            profile = root / 'boundary.sb'; profile.write_text(sandbox_profile(work, manuscript, [work]))
            with socket.socket() as server:
                server.bind(('127.0.0.1', 0)); server.listen()
                port = server.getsockname()[1]
                code = '''import pathlib,socket,sys
work,secret,port=sys.argv[1:]
pathlib.Path(work,'allowed').write_text('ok')
for op in [lambda:pathlib.Path(secret).read_text(),lambda:pathlib.Path(secret).write_text('bad'),lambda:socket.create_connection(('127.0.0.1',int(port)),timeout=1)]:
 try: op()
 except OSError: pass
 else: raise SystemExit('boundary failed')
'''
                p = subprocess.run(['/usr/bin/sandbox-exec','-f',str(profile),sys.executable,'-c',code,str(work),str(secret),str(port)],capture_output=True,text=True)
                self.assertEqual(p.returncode, 0, p.stderr)
                self.assertEqual(secret.read_text(), 'original')


if __name__ == '__main__': unittest.main()

class CredentialBoundaryTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform == 'darwin', 'macOS sandbox')
    def test_config_reference_oauth_lock_and_other_candidates_protected(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d).resolve(); work = root/'run'; work.mkdir()
            original=root/'config.toml'; original.write_text('fictional')
            link=work/'config.toml'; link.symlink_to(original)
            oauth=work/'oauth'; oauth.mkdir()
            sibling=root/'sibling'; sibling.mkdir(); (sibling/'proposal.tex').write_text('private')
            profile=sandbox_profile(work,root/'main',[work],[link,oauth],[sibling])
            code='''from pathlib import Path
import sys
link,oauth,sibling=sys.argv[1:]
for action in [lambda:Path(link).unlink(),lambda:Path(oauth,'refresh.lock').mkdir(),lambda:Path(sibling,'proposal.tex').read_text()]:
 try:action()
 except OSError:pass
 else:raise SystemExit('protection failed')
'''
            p=subprocess.run(['/usr/bin/sandbox-exec','-p',profile,sys.executable,'-c',code,str(link),str(oauth),str(sibling)],capture_output=True,text=True)
            self.assertEqual(p.returncode,0,p.stderr)
            self.assertTrue(link.is_symlink())
            self.assertEqual(original.read_text(),'fictional')
