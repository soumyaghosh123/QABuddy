"""Shared chunking helpers: token estimates, Markdown sections, size-limited splitting."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

HEADING_RE = re.compile(r"^(#{1,3})\s+(.+?)\s*#*\s*$")
FENCE_RE = re.compile(r"^\s*(```|~~~)")


def estimate_tokens(text: str) -> int:
    return math.ceil(len(text) / 4)


@dataclass
class Section:
    breadcrumb: list[str]
    text: str
    line_start: int
    line_end: int


def split_markdown(text: str, title: str) -> list[Section]:
    """Split Markdown on H1-H3 headings, keeping heading paths and 1-based line numbers."""
    lines = text.split("\n")
    sections: list[Section] = []
    stack: list[tuple[int, str]] = []
    current: list[str] = []
    start = 1
    crumb: list[str] = [title]
    in_fence = False

    def flush(end: int) -> None:
        body = "\n".join(current).strip("\n")
        if body.strip():
            sections.append(Section(list(crumb), body, start, end))

    for number, line in enumerate(lines, 1):
        if FENCE_RE.match(line):
            in_fence = not in_fence
        heading = None if in_fence else HEADING_RE.match(line)
        if heading:
            flush(number - 1)
            level = len(heading.group(1))
            stack = [(lvl, name) for lvl, name in stack if lvl < level] + [(level, heading.group(2).strip())]
            crumb = [name for _, name in stack]
            current = [line]
            start = number
        else:
            current.append(line)
    flush(len(lines))
    return sections


def merge_small(sections: list[Section], min_tokens: int, target: int) -> list[Section]:
    """Merge sections under min_tokens into the next section (or the previous one at the end)."""
    result: list[Section] = []
    carry: Section | None = None
    for section in sections:
        if carry is not None:
            combined = f"{carry.text}\n{section.text}"
            if estimate_tokens(combined) <= target:
                section = Section(section.breadcrumb, combined, carry.line_start, section.line_end)
            else:
                result.append(carry)
            carry = None
        if estimate_tokens(section.text) < min_tokens:
            carry = section
        else:
            result.append(section)
    if carry is not None:
        if result and estimate_tokens(f"{result[-1].text}\n{carry.text}") <= target:
            prev = result[-1]
            result[-1] = Section(prev.breadcrumb, f"{prev.text}\n{carry.text}", prev.line_start, carry.line_end)
        else:
            result.append(carry)
    return result


@dataclass
class _Unit:
    text: str
    line: int
    continues: bool  # True when this unit is a slice continuing the previous unit's line


def _slice_line(line: str, number: int, slice_chars: int) -> list[_Unit]:
    words: list[str] = []
    for word in line.split(" "):
        while len(word) > slice_chars:
            words.append(word[:slice_chars])
            word = word[slice_chars:]
        words.append(word)
    units: list[_Unit] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}" if current else word
        if current and len(candidate) > slice_chars:
            units.append(_Unit(current, number, bool(units)))
            current = word
        else:
            current = candidate
    units.append(_Unit(current, number, bool(units)))
    return units


def _join(units: list[_Unit]) -> str:
    parts: list[str] = []
    for i, unit in enumerate(units):
        if i:
            parts.append(" " if unit.continues else "\n")
        parts.append(unit.text)
    return "".join(parts)


def split_by_size(text: str, target: int, max_tokens: int, overlap: int) -> list[tuple[str, int, int]]:
    """Split text into pieces of about `target` tokens (never above `max_tokens`).

    Pieces break on line boundaries; lines longer than a slice are cut at spaces (or hard-cut).
    Consecutive pieces share up to `overlap` tokens of trailing units. Returns
    (piece, line_start, line_end) with 1-based line numbers.
    """
    target = min(target, max_tokens)
    slice_tokens = max(16, overlap) if overlap else max(16, target // 2)
    slice_chars = slice_tokens * 4
    units: list[_Unit] = []
    for number, line in enumerate(text.split("\n"), 1):
        if len(line) <= slice_chars:
            units.append(_Unit(line, number, False))
        else:
            units.extend(_slice_line(line, number, slice_chars))

    pieces: list[list[_Unit]] = []
    current: list[_Unit] = []
    fresh = 0  # units added since the last piece was emitted (excludes carried overlap)
    for unit in units:
        if current and fresh and estimate_tokens(_join(current + [unit])) > target:
            pieces.append(current)
            tail: list[_Unit] = []
            for prev in reversed(current):
                if estimate_tokens(_join([prev] + tail)) > overlap:
                    break
                tail.insert(0, prev)
            current = tail if overlap else []
            fresh = 0
        current.append(unit)
        fresh += 1
    if current and (fresh or not pieces):
        pieces.append(current)

    result: list[tuple[str, int, int]] = []
    for piece in pieces:
        while piece and not piece[0].text.strip():
            piece = piece[1:]
        while piece and not piece[-1].text.strip():
            piece = piece[:-1]
        if piece:
            result.append((_join(piece), piece[0].line, piece[-1].line))
    return result
