"""Conservative reading order for ordinary single- and two-column PDF text.

This helper uses the page's complete text geometry to infer column boundaries,
then returns whole paragraphs intersecting the requested viewport. It does not
perform OCR, repair formulas, or guess a reading order for overlapping layouts.
"""
from __future__ import annotations

from dataclasses import dataclass
import math


class ReadingError(ValueError):
    """The caller should display this message and offer selection translation."""


@dataclass(frozen=True)
class _Block:
    x0: float
    y0: float
    x1: float
    y1: float
    text: str

    @property
    def width(self):
        return self.x1 - self.x0

    @property
    def height(self):
        return self.y1 - self.y0


def _page_blocks(page):
    # PyMuPDF can combine two aligned columns into one block in drawing order.
    # Separate disconnected horizontal line groups before determining columns.
    result = []
    for raw in page.get_text('dict', sort=False)['blocks']:
        if raw['type'] != 0:
            continue
        lines = []
        for line in raw.get('lines', []):
            direction = line.get('dir', (1, 0))
            if abs(direction[0] - 1) > .01 or abs(direction[1]) > .01:
                raise ReadingError('当前页含旋转文字，无法可靠排序，请使用划选翻译。')
            text = ''.join(span.get('text', '') for span in line['spans']).strip()
            if text:
                lines.append(_Block(*map(float, line['bbox']), text))
        groups = []
        for line in lines:
            touching = [group for group in groups if any(min(line.x1, b.x1) - max(line.x0, b.x0) > 2 for b in group)]
            joined = [line]
            for group in touching:
                joined.extend(group)
                groups.remove(group)
            groups.append(joined)
        for group in groups:
            result.append(_Block(min(b.x0 for b in group), min(b.y0 for b in group),
                                 max(b.x1 for b in group), max(b.y1 for b in group),
                                 '\n'.join(b.text for b in sorted(group, key=lambda b: (b.y0, b.x0)))))
    return result


def _vertical_overlap(a, b):
    return min(a.y1, b.y1) - max(a.y0, b.y0)


def _prose(block):
    # A page number or equation label beside one paragraph is not a second column.
    return sum(c.isalpha() for c in block.text) >= 20


def _order(blocks, page_width):
    left_edge = min(b.x0 for b in blocks)
    right_edge = max(b.x1 for b in blocks)
    middle = (left_edge + right_edge) / 2
    left = [b for b in blocks if b.x1 <= middle]
    right = [b for b in blocks if b.x0 >= middle]
    spanning = [b for b in blocks if b.x0 < middle < b.x1]
    pairs = [(a, b) for a in left for b in right
             if _prose(a) and _prose(b) and _vertical_overlap(a, b) > 2]
    if not pairs:
        # A full-width prose block is positive evidence for a single-column page.
        # Disjoint left/right prose without that evidence is ambiguous (e.g. floats).
        full_prose = any(_prose(b) and b.width >= .7 * (right_edge - left_edge) for b in spanning)
        if any(map(_prose, left)) and any(map(_prose, right)) and not full_prose:
            raise ReadingError('无法可靠判断当前页的阅读顺序，请使用划选翻译。')
        return sorted(blocks, key=lambda b: (b.y0, b.x0))

    if min(b.x0 - a.x1 for a, b in pairs) < max(8, page_width * .015):
        raise ReadingError('当前页栏间距不明确，请使用划选翻译。')
    body_width = right_edge - left_edge
    for block in spanning:
        short_centered_heading = len(block.text.splitlines()) <= 2 and len(block.text) <= 150
        if block.width < .55 * body_width and not short_centered_heading:
            raise ReadingError('当前页可能含三栏或复杂排版，请使用划选翻译。')
        if any(_vertical_overlap(block, side) > 2 for side in left + right):
            raise ReadingError('跨栏内容与正文重叠，无法可靠排序，请使用划选翻译。')

    # Each full-width heading/paragraph separates independent reading regions.
    # Within a region read the left column top-to-bottom, then the right column.
    ordered = []
    remaining = left + right
    for divider in sorted(spanning, key=lambda b: (b.y0, b.x0)):
        above = [b for b in remaining if b.y1 <= divider.y0 + 2]
        ordered.extend(sorted(above, key=lambda b: (b.x0 >= middle, b.y0, b.x0)))
        remaining = [b for b in remaining if b not in above]
        ordered.append(divider)
    ordered.extend(sorted(remaining, key=lambda b: (b.x0 >= middle, b.y0, b.x0)))
    return ordered


def visible_paragraphs(page, rect) -> list[str]:
    """Return intersecting complete text blocks in a supported reading order.

    ``page`` is a PyMuPDF Page; ``rect`` is its unrotated PDF-space viewport.
    Layout inference always uses all page blocks, including off-screen columns.
    """
    if page.rotation:
        raise ReadingError('旋转页面请使用划选翻译。')
    coordinates = list(rect)
    if len(coordinates) != 4 or not all(isinstance(v, (int, float)) and math.isfinite(v) for v in coordinates):
        raise ReadingError('页面位置无效，请重新获取当前段落。')
    x0, y0, x1, y1 = coordinates
    if x0 >= x1 or y0 >= y1:
        raise ReadingError('页面位置无效，请重新获取当前段落。')
    bounds = page.rect
    x0, y0, x1, y1 = max(x0, bounds.x0), max(y0, bounds.y0), min(x1, bounds.x1), min(y1, bounds.y1)
    if x0 >= x1 or y0 >= y1:
        return []
    blocks = _page_blocks(page)
    if not blocks:
        raise ReadingError('当前页没有可提取文字；扫描 PDF 不支持自动翻译，请使用划选文字或文本版 PDF。')
    ordered = _order(blocks, bounds.width)
    return [b.text for b in ordered if min(b.x1, x1) > max(b.x0, x0) and min(b.y1, y1) > max(b.y0, y0)]
