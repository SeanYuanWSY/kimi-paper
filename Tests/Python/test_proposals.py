import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'Resources'))
from paper_proposals import parse_proposal
from paper_tasks import TaskError


class ProposalTests(unittest.TestCase):
    def proposal(self, edits):
        return json.dumps({'edits': edits, 'explanation': 'Shorter wording.'})

    def test_candidate_is_separate_and_edits_use_original_offsets(self):
        base = {'main.tex': b'First sentence. Second sentence.', 'image.pdf': b'unchanged'}
        result, _ = parse_proposal(self.proposal([
            {'file': 'main.tex', 'old': 'First sentence.', 'new': 'One.'},
            {'file': 'main.tex', 'old': 'Second sentence.', 'new': 'Two.'},
        ]), base, {'main.tex'})
        self.assertEqual(result['main.tex'], b'One. Two.')
        self.assertEqual(base['main.tex'], b'First sentence. Second sentence.')
        self.assertEqual(result['image.pdf'], base['image.pdf'])

    def test_rejects_ambiguous_overlapping_and_cascading_edits(self):
        for original, edits in [
            (b'repeat repeat', [('repeat', 'new')]),
            (b'abcdef', [('abc', 'x'), ('bcd', 'y')]),
            (b'original', [('original', 'intermediate'), ('intermediate', 'final')]),
        ]:
            with self.subTest(original=original), self.assertRaises(TaskError):
                parse_proposal(self.proposal([{'file': 'main.tex', 'old': old, 'new': new} for old, new in edits]),
                               {'main.tex': original}, {'main.tex'})

    def test_rejects_paths_and_files_not_explicitly_shared(self):
        for name in ['../main.tex', '/tmp/main.tex', '.private.tex', 'a/../main.tex', 'a//main.tex', 'image.pdf', 'other.tex', 'a\\main.tex']:
            with self.subTest(name=name), self.assertRaises(TaskError):
                parse_proposal(self.proposal([{'file': name, 'old': 'old', 'new': 'new'}]),
                               {name: b'old', 'main.tex': b'old'}, {'main.tex'})

    def test_rejects_incomplete_duplicate_and_nonobject_json(self):
        for text in ['[]', '{', '{"edits":[],"edits":[],"explanation":""}',
                     self.proposal([{'file': 'main.tex', 'old': '', 'new': 'new'}])]:
            with self.subTest(text=text), self.assertRaises(TaskError):
                parse_proposal(text, {'main.tex': b'old'}, {'main.tex'})

    def test_accepts_json_fence_but_not_surrounding_unstructured_output(self):
        text = self.proposal([{'file': 'main.tex', 'old': 'old', 'new': 'new'}])
        self.assertEqual(parse_proposal('```json\n' + text + '\n```', {'main.tex': b'old'}, {'main.tex'})[0]['main.tex'], b'new')
        with self.assertRaises(TaskError):
            parse_proposal('Here is an answer: ' + text, {'main.tex': b'old'}, {'main.tex'})
