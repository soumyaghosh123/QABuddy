"""Shared state handed to every parser."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from qabuddy.config import Settings
from qabuddy.ingest.models import Chunk
from qabuddy.ingest.normalize import extract_ids


@dataclass
class ParseContext:
    settings: Settings
    source_label: str
    known_entities: set[str]
    glossary: dict
    test_runs: list = field(default_factory=list)  # filled by the Jenkins parser


def tag(chunk: Chunk, ctx: ParseContext, extra_ids: Iterable[str] = ()) -> Chunk:
    """Set chunk.ids_norm from explicit IDs plus IDs found in its header and text."""
    ids: list[str] = []
    for value in [*extra_ids, *extract_ids(f"{chunk.header}\n{chunk.text}", ctx.known_entities)]:
        value = value.lower()
        if value and value not in ids:
            ids.append(value)
    chunk.ids_norm = ids
    return chunk
