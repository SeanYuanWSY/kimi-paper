"""Recover real comment-store effects across the commit boundary, with fake compilation."""
import asyncio
import base64
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'Resources'))
import paper_engine
from paper_engine import Engine
from paper_service import PaperService
from paper_tasks import TaskError, materialize, atomic_json
from tex_mcp_web.comments import CommentStore, PaperAnchor


class EffectRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.paper = self.root / 'paper'; self.paper.mkdir()
        (self.paper / 'main.tex').write_bytes(b'old')
        self.service = PaperService.__new__(PaperService)
        self.service.comments = CommentStore(self.paper / '.tex-mcp-web/comments.json')
        self.service._compile_task = None
        self.service.compile_ok = True
        async def compile():
            if self.service.compile_ok:
                (self.paper / 'main.pdf').write_bytes((self.paper / 'main.tex').read_bytes())
            return SimpleNamespace(success=self.service.compile_ok)
        async def broadcast(_): pass
        self.service.do_compile = compile
        self.service.broadcast = broadcast
        self.e = self.engine()
        self.service.engine = self.e
        comment = self.service.comments.add(PaperAnchor(), 'Make this concise')
        body = comment.to_dict(); body['revision'] = self.service.comment_revision(body)
        self.tid = 'd' * 32
        self.e.store.put({'id': self.tid, 'status': 'ready', 'comment': body}, self.tid)
        materialize(self.e.run_dir(self.tid) / 'base', {'main.tex': b'old'})
        materialize(self.e.run_dir(self.tid) / 'result', {'main.tex': b'new'})
        self.cid = comment.id

    def engine(self):
        return Engine(self.paper, 'main.tex', self.root / 'state', on_applied=self.service.applied,
                      prepare_effects=self.service.prepare_effects, recover_effects=self.service.recover_effects)

    async def asyncTearDown(self):
        await self.e.close(); self.temp.cleanup()

    async def compile_candidate(self, *args): return {'success': True}

    async def test_app_effect_does_not_invalidate_sibling_but_human_edit_does(self):
        before = self.e.store.get(self.tid)['comment']['revision']
        with patch('paper_engine.compile_copy', self.compile_candidate):
            await self.e.apply(self.tid)
        current = self.service.comments.get(self.cid).to_dict()
        self.assertEqual(self.service.comment_revision(current), before)
        self.service.comments.edit_entry(self.cid, 0, 'Changed human requirement', author='human')
        current = self.service.comments.get(self.cid).to_dict()
        self.assertNotEqual(self.service.comment_revision(current), before)

    async def test_callback_success_then_journal_failure_restores_all_effects(self):
        original = paper_engine.atomic_json
        def fail_commit(path, journal):
            if journal['state'] == 'committed':
                raise OSError('injected commit failure')
            original(path, journal)
        with patch('paper_engine.compile_copy', self.compile_candidate), patch('paper_engine.atomic_json', fail_commit):
            with self.assertRaises(OSError): await self.e.apply(self.tid)
        self.assertEqual((self.paper / 'main.tex').read_bytes(), b'old')
        self.assertEqual((self.paper / 'main.pdf').read_bytes(), b'old')
        self.assertEqual(self.service.comments.get(self.cid).status, 'open')
        self.assertEqual(len(self.service.comments.get(self.cid).thread), 1)
        self.assertEqual(self.e.store.get(self.tid)['status'], 'ready')
        self.e.require_recovered()

    async def test_undo_journal_failure_restores_adopted_pdf_and_comment(self):
        with patch('paper_engine.compile_copy', self.compile_candidate):
            await self.e.apply(self.tid)
            original = paper_engine.atomic_json
            def fail_undo_commit(path, journal):
                if journal.get('operation') == 'undo' and journal['state'] == 'committed':
                    raise OSError('injected undo commit failure')
                original(path, journal)
            with patch('paper_engine.atomic_json', fail_undo_commit):
                with self.assertRaises(OSError): await self.e.undo(self.tid)
        self.assertEqual((self.paper / 'main.tex').read_bytes(), b'new')
        self.assertEqual((self.paper / 'main.pdf').read_bytes(), b'new')
        self.assertEqual(self.service.comments.get(self.cid).status, 'resolved')
        self.assertEqual(self.e.store.get(self.tid)['status'], 'applied')

    async def install_crash_snapshot(self):
        effects = self.service.prepare_effects(self.e.store.get(self.tid), False, 'transaction')
        journal = {'id': 'transaction', 'task': self.tid, 'operation': 'apply', 'state': 'writing', 'effects': effects,
                   'files': {'main.tex': {'before': base64.b64encode(b'old').decode(), 'after': base64.b64encode(b'new').decode()}}}
        atomic_json(self.root / 'state/journals/transaction.json', journal)
        self.e.update(self.tid, status='applying')
        (self.paper / 'main.tex').write_bytes(b'new')
        (self.paper / 'main.pdf').write_bytes(b'new')
        self.service.write_comment_effect(effects)
        await self.e.close(); self.e = self.engine()
        self.service.engine = self.e

    async def test_restart_after_callback_recovers_before_new_operations(self):
        await self.install_crash_snapshot()
        with self.assertRaises(TaskError): self.e.require_recovered()
        await self.e.finish_recovery()
        self.assertEqual((self.paper / 'main.tex').read_bytes(), b'old')
        self.assertEqual((self.paper / 'main.pdf').read_bytes(), b'old')
        self.assertEqual(self.service.comments.get(self.cid).status, 'open')
        self.assertEqual(self.e.store.get(self.tid)['status'], 'ready')
        self.e.require_recovered()

    async def test_external_comment_edit_is_preserved_and_blocks_adoption(self):
        await self.install_crash_snapshot()
        self.service.comments.edit_entry(self.cid, 0, 'New human instruction', author='human')
        before = self.service.comments.get(self.cid).to_dict()
        with self.assertRaises(TaskError): await self.e.finish_recovery()
        self.assertEqual(self.service.comments.get(self.cid).to_dict(), before)
        self.assertEqual((self.paper / 'main.pdf').read_bytes(), b'old')
        with self.assertRaises(TaskError): self.e.require_recovered()

    async def test_pdf_recovery_failure_is_retryable_and_blocks_adoption(self):
        await self.install_crash_snapshot()
        self.service.compile_ok = False
        with self.assertRaises(TaskError): await self.e.finish_recovery()
        with self.assertRaises(TaskError): self.e.require_recovered()
        self.service.compile_ok = True
        await self.e.finish_recovery()
        self.e.require_recovered()
        self.assertEqual(self.service.comments.get(self.cid).status, 'open')


class PDFPublicationTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_or_linked_pdf_is_not_success(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); paper = root / 'paper'; paper.mkdir()
            (paper / 'main.tex').write_text('source')
            service = PaperService.__new__(PaperService)
            service.watch_dir = paper; service.main_file = paper / 'main.tex'
            service.engine = SimpleNamespace(main='main.tex', store=SimpleNamespace(root=root / 'state'))
            async def no_pdf(*args): return {'success': True, 'errors': [], 'warnings': []}
            with patch('paper_service.compile_copy', no_pdf):
                result = await service.guarded_compile(service.main_file, 'pdflatex', paper)
            self.assertFalse(result.success)
            async def linked_pdf(folder, *args):
                (folder / 'main.pdf').symlink_to(paper / 'main.tex')
                return {'success': True, 'errors': [], 'warnings': []}
            with patch('paper_service.compile_copy', linked_pdf):
                result = await service.guarded_compile(service.main_file, 'pdflatex', paper)
            self.assertFalse(result.success)

    async def test_visible_rejects_pdf_changed_during_extraction(self):
        import pymupdf
        with tempfile.TemporaryDirectory() as d:
            pdf = Path(d) / 'main.pdf'
            with pymupdf.open() as doc:
                page = doc.new_page(); page.insert_text((30, 50), 'Visible academic passage.')
                doc.save(pdf)
            service = PaperService.__new__(PaperService)
            service.pdf_digest = 'before'
            service.last_result = SimpleNamespace(output_file=pdf)
            async def body():
                return {'digest': 'before', 'pages': [{'page': 1, 'rect': [0, 0, 500, 500]}]}
            async def racing_extract(fn):
                result = fn(); service.pdf_digest = 'after'; return result
            with patch('paper_service.asyncio.to_thread', racing_extract):
                with self.assertRaises(TaskError): await service.visible(SimpleNamespace(json=body))
