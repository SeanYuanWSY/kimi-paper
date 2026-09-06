"""Fictional PDF fixtures only: no private paper text is accessed."""
from pathlib import Path
import sys
import unittest

import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'Resources'))
from paper_reading import ReadingError, visible_paragraphs


class ReadingTests(unittest.TestCase):
    def setUp(self):
        self.doc = pymupdf.open()
        self.page = self.doc.new_page(width=600, height=800)
        self.addCleanup(self.doc.close)

    def paragraph(self, text, x, y, width=225):
        space = self.page.insert_textbox(pymupdf.Rect(x, y, x + width, y + 65), text, fontsize=11)
        self.assertGreaterEqual(space, 0, 'Fixture text must fit')

    def two_columns(self):
        # Intentionally draw right first: insertion order must not determine reading order.
        self.paragraph('Right first paragraph with several fictional words.', 325, 120)
        self.paragraph('Left first paragraph with several fictional words.', 50, 100)
        self.paragraph('Right second paragraph with several fictional words.', 325, 210)
        self.paragraph('Left second paragraph with several fictional words.', 50, 200)

    def test_single_column_keeps_paragraphs_and_symbols(self):
        self.paragraph('First passage preserves Eq. (1) and citation [2].', 50, 80, 500)
        self.paragraph('Second passage contains another fictional sentence.', 50, 180, 500)
        result = visible_paragraphs(self.page, self.page.rect)
        self.assertEqual(len(result), 2)
        self.assertIn('Eq. (1)', result[0]); self.assertIn('[2]', result[0])
        self.assertTrue(result[1].startswith('Second'))

    def test_two_columns_read_left_then_right(self):
        self.two_columns()
        result = visible_paragraphs(self.page, self.page.rect)
        self.assertEqual([s.split()[:2] for s in result],
                         [['Left', 'first'], ['Left', 'second'], ['Right', 'first'], ['Right', 'second']])

    def test_full_width_title_precedes_columns(self):
        self.paragraph('A fictional heading spanning the entire width of the paper', 50, 30, 500)
        self.two_columns()
        result = visible_paragraphs(self.page, self.page.rect)
        self.assertTrue(result[0].startswith('A fictional heading'))
        self.assertTrue(result[1].startswith('Left first'))

    def test_full_width_abstract_precedes_both_columns(self):
        self.paragraph('Abstract: This fictional summary extends across the page and describes the example without using private manuscript content.', 50, 25, 500)
        self.two_columns()
        result = visible_paragraphs(self.page, self.page.rect)
        self.assertTrue(result[0].startswith('Abstract:'))
        self.assertTrue(result[1].startswith('Left first'))
        self.assertTrue(result[-1].startswith('Right second'))

    def test_overlapping_full_width_content_requires_selection(self):
        self.two_columns()
        self.paragraph('A full width overlapping annotation cannot establish a reliable reading region.', 50, 112, 500)
        with self.assertRaises(ReadingError): visible_paragraphs(self.page, self.page.rect)

    def test_rotated_text_inside_normal_page_requires_selection(self):
        self.two_columns()
        self.page.insert_text((25, 250), 'Rotated annotation', rotate=90)
        with self.assertRaises(ReadingError): visible_paragraphs(self.page, self.page.rect)

    def test_middle_full_width_block_separates_column_regions(self):
        self.two_columns()
        self.paragraph('An intervening full width discussion separates the two reading regions.', 50, 310, 500)
        self.paragraph('Lower right paragraph with several fictional words.', 325, 420)
        self.paragraph('Lower left paragraph with several fictional words.', 50, 420)
        result = visible_paragraphs(self.page, self.page.rect)
        self.assertTrue(result[4].startswith('An intervening'))
        self.assertTrue(result[5].startswith('Lower left'))
        self.assertTrue(result[6].startswith('Lower right'))

    def test_viewport_returns_complete_intersecting_blocks_only(self):
        self.two_columns()
        full = visible_paragraphs(self.page, self.page.rect)
        clipped = visible_paragraphs(self.page, [50, 110, 280, 121])
        self.assertEqual(clipped, [full[0]])
        right_only = visible_paragraphs(self.page, [320, 80, 580, 280])
        self.assertEqual(right_only, full[2:])

    def test_rotated_page_requires_selection(self):
        self.two_columns(); self.page.set_rotation(90)
        with self.assertRaises(ReadingError): visible_paragraphs(self.page, self.page.rect)

    def test_three_columns_are_not_silently_interleaved(self):
        for x in (30, 230, 430):
            self.paragraph('Several fictional words form this long narrow column of text, requiring a reliable reading order.', x, 100, 140)
        with self.assertRaises(ReadingError): visible_paragraphs(self.page, self.page.rect)

    def test_no_text_and_invalid_viewport_have_explicit_errors(self):
        with self.assertRaises(ReadingError): visible_paragraphs(self.page, self.page.rect)
        for rect in ([0, 0, float('nan'), 50], [20, 20, 10, 10]):
            with self.assertRaises(ReadingError): visible_paragraphs(self.page, rect)
