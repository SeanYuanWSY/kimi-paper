import asyncio
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'Resources'))
from paper_engine import Engine
from paper_studio import Studio
from paper_tasks import TaskError, materialize, atomic_json


class StudioTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.paper = self.root / 'paper'
        self.paper.mkdir()
        (self.paper / 'main.tex').write_bytes(b'original\n')
        self.engine = Engine(self.paper, 'main.tex', self.root / 'state')
        self.service = SimpleNamespace(engine=self.engine, broadcast=AsyncMock())
        self.studio = Studio(self.service)

    async def asyncTearDown(self):
        await self.studio.close()
        await self.engine.close()
        self.temp.cleanup()

    async def test_freeze_inserts_immutable_task_and_adoption_never_rewrites_live_draft(self):
        self.studio.busy = AsyncMock(return_value=False)
        (self.studio.work / 'main.tex').write_bytes(b'first proposal\n')
        async def compiler(folder, *args):
            (folder / 'main.pdf').write_bytes(b'%PDF-fictional')
            (self.studio.work / 'main.tex').write_bytes(b'later unsaved proposal\n')
            return {'success': True, 'errors': [], 'warnings': []}
        with patch('paper_studio.compile_copy', compiler):
            await self.studio.freeze()
        ident = self.studio.manifest['preview']
        task = self.engine.store.get(ident)
        self.assertTrue(task['studio'])
        self.assertEqual(task['status'], 'ready')
        self.assertEqual((self.engine.run_dir(ident) / 'result/main.tex').read_bytes(), b'first proposal\n')
        self.assertEqual((self.paper / 'main.tex').read_bytes(), b'original\n')
        self.assertIsNotNone(self.studio.preview_pdf())
        with patch('paper_engine.compile_copy', AsyncMock(return_value={'success': True})):
            await self.engine.apply(ident)
        self.assertEqual((self.paper / 'main.tex').read_bytes(), b'first proposal\n')
        self.assertEqual((self.studio.work / 'main.tex').read_bytes(), b'later unsaved proposal\n')
        self.assertEqual((self.studio.base / 'main.tex').read_bytes(), b'original\n')

    async def test_adopted_advances_ancestor_for_two_successive_same_line_edits(self):
        self.studio.busy = AsyncMock(return_value=False)
        compiler = AsyncMock(return_value={'success': True, 'errors': [], 'warnings': []})
        with patch('paper_studio.compile_copy', compiler), patch('paper_engine.compile_copy', compiler):
            for revision in (b'first revision\n', b'second revision\n'):
                (self.studio.work / 'main.tex').write_bytes(revision)
                await self.studio.freeze()
                ident = self.studio.manifest['preview']
                await self.engine.apply(ident)
                self.studio.adopted(ident)
                self.assertEqual((self.paper / 'main.tex').read_bytes(), revision)
                self.assertEqual((self.studio.base / 'main.tex').read_bytes(), revision)
                self.assertEqual((self.studio.work / 'main.tex').read_bytes(), revision)
        self.assertEqual(len(self.engine.store.all()), 2)

    async def test_sync_rejects_new_live_changes_before_stopping_web(self):
        self.studio.busy = AsyncMock(return_value=False)
        (self.studio.work / 'main.tex').write_bytes(b'unfrozen draft')
        with patch.object(self.studio, 'close', AsyncMock()) as close:
            with self.assertRaises(TaskError):
                await self.studio.sync()
            close.assert_not_awaited()
        self.assertEqual((self.studio.work / 'main.tex').read_bytes(), b'unfrozen draft')
        self.assertEqual((self.paper / 'main.tex').read_bytes(), b'original\n')

    async def test_sync_rechecks_after_stop_and_preserves_racing_write(self):
        self.studio.busy = AsyncMock(return_value=False)
        async def closing():
            (self.studio.work / 'main.tex').write_bytes(b'write during shutdown')
        with patch.object(self.studio, 'close', closing), patch.object(self.studio, 'start', AsyncMock()) as start:
            with self.assertRaises(TaskError):
                await self.studio.sync()
            start.assert_not_awaited()
        self.assertEqual((self.studio.work / 'main.tex').read_bytes(), b'write during shutdown')
        self.assertEqual((self.studio.base / 'main.tex').read_bytes(), b'original\n')
        self.assertEqual((self.paper / 'main.tex').read_bytes(), b'original\n')

    async def test_sync_preserves_old_work_and_base_and_does_not_change_formal(self):
        self.studio.busy = AsyncMock(return_value=False)
        (self.paper / 'main.tex').write_bytes(b'collaborator update')
        with patch.object(self.studio, 'close', AsyncMock()), patch.object(self.studio, 'start', AsyncMock()) as start:
            await self.studio.sync()
            start.assert_awaited_once()
        self.assertEqual((self.studio.work / 'main.tex').read_bytes(), b'collaborator update')
        self.assertEqual((self.studio.base / 'main.tex').read_bytes(), b'collaborator update')
        self.assertEqual((self.paper / 'main.tex').read_bytes(), b'collaborator update')
        backup = list(self.studio.run.glob('saved-*'))
        self.assertEqual(len(backup), 1)
        self.assertEqual((backup[0] / 'work/main.tex').read_bytes(), b'original\n')
        self.assertEqual((backup[0] / 'base/main.tex').read_bytes(), b'original\n')

    async def test_exchange_rename_failure_restores_both_old_directories(self):
        next_work = self.studio.run / 'next-work-test'
        next_base = self.studio.run / 'next-base-test'
        materialize(next_work, {'main.tex': b'new work'})
        materialize(next_base, {'main.tex': b'new base'})
        original_rename = Path.rename
        def failing_rename(source, target):
            if source == next_base:
                raise OSError('fictional disk error')
            return original_rename(source, target)
        with patch.object(Path, 'rename', failing_rename):
            with self.assertRaises(OSError):
                self.studio.exchange({'work': next_work, 'base': next_base})
        self.assertEqual((self.studio.work / 'main.tex').read_bytes(), b'original\n')
        self.assertEqual((self.studio.base / 'main.tex').read_bytes(), b'original\n')
        self.assertEqual((next_base / 'main.tex').read_bytes(), b'new base')
        self.assertFalse((self.studio.run / 'swap.json').exists())
        self.assertTrue(any((p / 'main.tex').read_bytes() == b'new work'
                            for p in self.studio.run.glob('work-interrupted-*')))

    async def test_restart_recovers_partial_uncommitted_exchange(self):
        ident = 'a' * 32
        backup = self.studio.run / ('saved-' + ident)
        backup.mkdir()
        self.studio.work.rename(backup / 'work')
        materialize(self.studio.work, {'main.tex': b'partially installed'})
        self.studio.base.rename(backup / 'base')
        atomic_json(self.studio.run / 'swap.json', {'id': ident, 'names': ['work', 'base'], 'committed': False})
        recovered = Studio(self.service)
        self.assertEqual((recovered.work / 'main.tex').read_bytes(), b'original\n')
        self.assertEqual((recovered.base / 'main.tex').read_bytes(), b'original\n')
        self.assertFalse((self.studio.run / 'swap.json').exists())
        await recovered.close()

    async def test_repeated_freeze_selects_latest_successful_version(self):
        self.studio.busy = AsyncMock(return_value=False)
        compiler = AsyncMock(return_value={'success': True, 'errors': [], 'warnings': []})
        with patch('paper_studio.compile_copy', compiler):
            (self.studio.work / 'main.tex').write_bytes(b'first preview')
            await self.studio.freeze()
            first = self.studio.manifest['preview']
            (self.studio.work / 'main.tex').write_bytes(b'second preview')
            await self.studio.freeze()
            second = self.studio.manifest['preview']
            self.assertNotEqual(first, second)
            self.studio.manifest['preview'] = first
            await self.studio.freeze()
            self.assertEqual(self.studio.manifest['preview'], second)
            self.assertEqual(compiler.await_count, 2)
            self.assertEqual(len(self.engine.store.all()), 2)

    async def test_busy_blocks_freeze_without_creating_task(self):
        self.studio.busy = AsyncMock(return_value=True)
        with self.assertRaises(TaskError):
            await self.studio.freeze()
        self.assertEqual(self.engine.store.all(), [])

    async def test_failed_compile_does_not_publish_preview_or_formal_files(self):
        self.studio.busy = AsyncMock(return_value=False)
        (self.studio.work / 'main.tex').write_bytes(b'broken proposal')
        with patch('paper_studio.compile_copy', AsyncMock(return_value={'success': False, 'errors': [], 'warnings': []})):
            await self.studio.freeze()
        self.assertIsNone(self.studio.preview_pdf())
        self.assertEqual(self.engine.store.all()[0]['status'], 'compile_failed')
        self.assertEqual((self.paper / 'main.tex').read_bytes(), b'original\n')

    async def test_restoration_rejects_other_project_and_escape_identifier(self):
        original = dict(self.studio.manifest)
        for change in ({'project': str(self.root / 'different')}, {'id': '../escape'}, {'id': '/tmp/escape'}):
            self.studio.manifest_path.write_text(json.dumps({**original, **change}))
            with self.assertRaises(TaskError):
                Studio(self.service)
        self.assertFalse((self.root / 'escape').exists())
        self.studio.manifest_path.write_text(json.dumps(original))
        restored = Studio(self.service)
        self.assertEqual(restored.work, self.studio.work)
        await restored.close()

    async def test_session_metadata_must_match_draft_not_formal_directory(self):
        self.studio.request = AsyncMock(return_value={'metadata': {'cwd': str(self.paper)}})
        with self.assertRaises(TaskError):
            await self.studio.select('fictional-session')
        self.assertIsNone(self.studio.manifest['session'])
        self.studio.request.return_value = {'metadata': {'cwd': str(self.studio.work)}}
        await self.studio.select('fictional-session')
        self.assertEqual(self.studio.manifest['session'], 'fictional-session')
        with self.assertRaises(TaskError):
            await self.studio.select('../other-session')

    async def test_pending_notes_added_during_send_are_retained_and_failure_keeps_batch(self):
        self.studio.manifest['pending'] = [{'id': 'old', 'text': 'first'}]
        async def sending(text, notes):
            self.assertEqual([n['id'] for n in notes], ['old'])
            self.studio.manifest['pending'].append({'id': 'new', 'text': 'second'})
        self.studio.send = sending
        await self.studio.send_pending()
        self.assertEqual([n['id'] for n in self.studio.manifest['pending']], ['new'])
        self.studio.send = AsyncMock(side_effect=TaskError('fictional failure'))
        with self.assertRaises(TaskError):
            await self.studio.send_pending()
        self.assertEqual([n['id'] for n in self.studio.manifest['pending']], ['new'])

    async def test_unknown_or_truncated_session_state_fails_closed(self):
        for response in ({}, {'items': [{}]}, {'items': [{'busy': False}] * 100}):
            self.studio.request = AsyncMock(return_value=response)
            with self.assertRaises(TaskError):
                await self.studio.busy()
        self.studio.request = AsyncMock(return_value={'items': [{'busy': False}, {'busy': True}]})
        self.assertTrue(await self.studio.busy())

    async def test_stdout_drain_consumes_all_remaining_bytes(self):
        reader = asyncio.StreamReader()
        reader.feed_data(b'fictional output\n' * 20000)
        reader.feed_eof()
        self.studio.proc = SimpleNamespace(stdout=reader)
        await self.studio.discard_output()
        self.assertTrue(reader.at_eof())
        self.studio.proc = None


class DirectStudioTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.paper = self.root / 'paper'
        self.paper.mkdir()
        (self.paper / 'main.tex').write_bytes(b'original\n')
        self.engine = Engine(self.paper, 'main.tex', self.root / 'state')
        self.service = SimpleNamespace(
            engine=self.engine, broadcast=AsyncMock(), do_compile=AsyncMock(), pdf_digest='pdf-1')
        self.studio = Studio(self.service, direct=True)

    async def asyncTearDown(self):
        await self.studio.close()
        await self.engine.close()
        self.temp.cleanup()

    async def test_direct_workspace_is_original_project_and_has_separate_manifest(self):
        self.assertEqual(self.studio.work, self.paper)
        self.assertEqual(self.studio.manifest_path.name, 'direct-manifest.json')
        self.assertFalse((self.studio.root / 'manifest.json').exists())
        self.studio.request = AsyncMock(return_value={'metadata': {'cwd': str(self.paper)}})
        await self.studio.select('direct-session')
        self.assertEqual(self.studio.manifest['session'], 'direct-session')
        self.studio.request.return_value = {'metadata': {'cwd': str(self.studio.run / 'work')}}
        with self.assertRaises(TaskError):
            await self.studio.select('old-draft-session')

    async def test_direct_compile_records_recoverable_change_and_undo_restores_source(self):
        self.studio.busy = AsyncMock(return_value=False)
        self.service.do_compile.return_value = SimpleNamespace(success=True)
        (self.paper / 'main.tex').write_bytes(b'direct change\n')
        await self.studio.freeze()
        task_id = self.studio.manifest['last_direct']
        task = self.engine.store.get(task_id)
        self.assertTrue(task['direct'])
        self.assertEqual(task['status'], 'applied')
        self.assertEqual((self.studio.base / 'main.tex').read_bytes(), b'direct change\n')
        with patch('paper_engine.compile_copy', AsyncMock(return_value={'success': True})):
            await self.studio.undo_direct()
        self.assertEqual((self.paper / 'main.tex').read_bytes(), b'original\n')
        self.assertIsNone(self.studio.manifest['last_direct'])
        self.assertEqual(self.engine.store.get(task_id)['status'], 'undone')

    async def test_direct_annotation_explicitly_requests_saved_project_edit(self):
        self.studio.manifest['session'] = 'direct-session'
        selected = {'metadata': {'cwd': str(self.paper)},
                    'agent_config': {'model': 'kimi-for-coding/k3-256k', 'thinking': 'high'}}
        self.studio.select = AsyncMock(return_value=selected)
        self.studio.request = AsyncMock(return_value={})
        await self.studio.send('', [{'quote': 'Original sentence.', 'text': 'Make it precise.'}])
        payload = self.studio.request.await_args.args[1]
        prompt = payload['content'][0]['text']
        self.assertIn('直接在当前项目目录中', prompt)
        self.assertIn('Original sentence.', prompt)
        self.assertIn('Make it precise.', prompt)

    async def test_direct_compile_rejects_files_changed_after_success(self):
        self.studio.busy = AsyncMock(return_value=False)
        async def racing_compile():
            (self.paper / 'main.tex').write_bytes(b'changed during compile\n')
            return SimpleNamespace(success=True)
        self.service.do_compile.side_effect = racing_compile
        with self.assertRaises(TaskError):
            await self.studio.freeze()
        self.assertEqual(self.engine.store.all(), [])
        self.assertIsNone(self.studio.manifest.get('last_direct'))

    async def test_direct_compile_ignores_its_own_synctex_output(self):
        self.studio.busy = AsyncMock(return_value=False)
        async def compiling():
            (self.paper / 'main.synctex.gz').write_bytes(b'generated navigation')
            return SimpleNamespace(success=True)
        self.service.do_compile.side_effect = compiling
        (self.paper / 'main.tex').write_bytes(b'direct change\n')
        await self.studio.freeze()
        self.assertIsNotNone(self.studio.manifest.get('last_direct'))
        self.assertNotIn('main.synctex.gz', self.studio.project_files())

    async def test_direct_snapshot_failure_has_task_before_any_journal(self):
        self.studio.busy = AsyncMock(return_value=False)
        self.service.do_compile.return_value = SimpleNamespace(success=True)
        (self.paper / 'main.tex').write_bytes(b'direct change\n')
        with patch.object(self.engine.history, 'record', side_effect=TaskError('fictional snapshot failure')):
            with self.assertRaises(TaskError):
                await self.studio.freeze()
        tasks = self.engine.store.all()
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]['status'], 'needs_review')
        self.assertEqual(self.engine.transactions(), [])

    async def test_restart_reconciles_committed_journal_before_base_swap(self):
        self.studio.busy = AsyncMock(return_value=False)
        self.service.do_compile.return_value = SimpleNamespace(success=True)
        (self.paper / 'main.tex').write_bytes(b'direct change\n')
        original_exchange = self.studio.exchange
        with patch.object(self.studio, 'exchange', side_effect=RuntimeError('crash after journal')):
            with self.assertRaises(RuntimeError):
                await self.studio.freeze()
        task = self.engine.store.all()[0]
        self.assertEqual(task['status'], 'applied')
        self.assertEqual((self.studio.base / 'main.tex').read_bytes(), b'original\n')
        self.studio.exchange = original_exchange

        recovered = Studio(self.service, direct=True)
        self.assertEqual(recovered.manifest['last_direct'], task['id'])
        self.assertEqual((recovered.base / 'main.tex').read_bytes(), b'direct change\n')
        await recovered.close()

    async def test_restart_recovers_latest_of_two_nonoverlapping_direct_changes(self):
        self.studio.busy = AsyncMock(return_value=False)
        self.service.do_compile.return_value = SimpleNamespace(success=True)
        (self.paper / 'main.tex').write_bytes(b'first direct change\n')
        await self.studio.freeze()
        first = self.studio.manifest['last_direct']
        (self.paper / 'refs.bib').write_bytes(b'@article{new}\n')
        original_exchange = self.studio.exchange
        with patch.object(self.studio, 'exchange', side_effect=RuntimeError('crash before manifest')):
            with self.assertRaises(RuntimeError):
                await self.studio.freeze()
        latest = max(self.engine.store.all(), key=lambda task: task['created'])
        self.assertNotEqual(first, latest['id'])
        self.studio.exchange = original_exchange

        recovered = Studio(self.service, direct=True)
        self.assertEqual(recovered.manifest['last_direct'], latest['id'])
        self.assertEqual((recovered.base / 'refs.bib').read_bytes(), b'@article{new}\n')
        await recovered.close()

    async def test_restart_recovers_last_direct_after_base_swap_before_manifest(self):
        self.studio.busy = AsyncMock(return_value=False)
        self.service.do_compile.return_value = SimpleNamespace(success=True)
        (self.paper / 'main.tex').write_bytes(b'direct change\n')
        original_persist = self.studio.persist
        with patch.object(self.studio, 'persist', side_effect=RuntimeError('crash after base swap')):
            with self.assertRaises(RuntimeError):
                await self.studio.freeze()
        latest = self.engine.store.all()[0]
        self.assertEqual((self.studio.base / 'main.tex').read_bytes(), b'direct change\n')
        self.studio.persist = original_persist

        recovered = Studio(self.service, direct=True)
        self.assertEqual(recovered.manifest['last_direct'], latest['id'])
        await recovered.close()

    async def test_git_barrier_is_durable_before_base_replacement(self):
        self.studio.busy = AsyncMock(return_value=False)
        self.service.do_compile.return_value = SimpleNamespace(success=True)
        (self.paper / 'main.tex').write_bytes(b'direct change\n')
        await self.studio.freeze()
        (self.paper / 'refs.bib').write_bytes(b'collaborator update\n')
        original_replace = self.studio.replace_base
        with patch.object(self.studio, 'replace_base', side_effect=RuntimeError('crash after git')):
            with self.assertRaises(RuntimeError):
                self.studio.accept_external_baseline()
        self.studio.replace_base = original_replace

        recovered = Studio(self.service, direct=True)
        self.assertIsNone(recovered.manifest.get('last_direct'))
        self.assertEqual((recovered.base / 'refs.bib').read_bytes(), b'collaborator update\n')
        await recovered.close()

    async def test_external_edit_on_restart_becomes_new_baseline_without_old_undo(self):
        self.studio.busy = AsyncMock(return_value=False)
        self.service.do_compile.return_value = SimpleNamespace(success=True)
        (self.paper / 'main.tex').write_bytes(b'direct change\n')
        await self.studio.freeze()
        self.assertIsNotNone(self.studio.manifest.get('last_direct'))
        (self.paper / 'main.tex').write_bytes(b'collaborator update\n')

        recovered = Studio(self.service, direct=True)
        self.assertIsNone(recovered.manifest.get('last_direct'))
        self.assertEqual((recovered.base / 'main.tex').read_bytes(), b'collaborator update\n')
        await recovered.close()


if __name__ == '__main__':
    unittest.main()
