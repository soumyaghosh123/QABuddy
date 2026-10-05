"""Lucid chart exports (JSON nodes/edges, Lucid CSV shape export, plain text) as flow text."""

from __future__ import annotations

import csv
import io
import json

from qabuddy.ingest.chunking import estimate_tokens, split_by_size
from qabuddy.ingest.context import ParseContext, tag
from qabuddy.ingest.models import Chunk, SourceFile, make_chunk_id
from qabuddy.ingest.normalize import clean_text, mask_secrets, read_text
from qabuddy.ingest.parsers.documents import title_from_path

ARROW = "→"


def _flow_text(title: str, nodes: list[tuple[str, str, str]], edges: list[tuple[str, str, str]],
               extra: list[str]) -> str:
    """nodes: (id, label, note); edges: (from_id, to_id, label)."""
    labels = {node_id: label for node_id, label, _ in nodes}
    lines = [f"Diagram: {title}", *extra, "Steps:"]
    for _, label, note in nodes:
        lines.append(f"- {label}" + (f" (note: {note})" if note else ""))
    if edges:
        lines.append("Flow:")
        for src, dst, label in edges:
            line = f"- {labels.get(src, src)} {ARROW} {labels.get(dst, dst)}"
            lines.append(line + (f" ({label})" if label else ""))
    return "\n".join(lines)


def _from_json(raw: str, fallback_title: str) -> tuple[str, str]:
    data = json.loads(raw)
    title = data.get("title") or fallback_title
    nodes = [(str(n.get("id")), str(n.get("label") or n.get("text") or n.get("id")), str(n.get("note") or ""))
             for n in data.get("nodes", [])]
    edges = [(str(e.get("from")), str(e.get("to")), str(e.get("label") or "")) for e in data.get("edges", [])]
    extra = [f"{k.title()}: {v}" for k, v in data.items()
             if k not in ("title", "nodes", "edges") and isinstance(v, (str, int, float))]
    return title, _flow_text(title, nodes, edges, extra)


def _from_csv(raw: str, fallback_title: str) -> tuple[str, str]:
    rows = list(csv.DictReader(io.StringIO(raw)))
    title = fallback_title
    nodes: list[tuple[str, str, str]] = []
    edges: list[tuple[str, str, str]] = []
    for row in rows:
        name = (row.get("Name") or "").strip()
        text = (row.get("Text Area 1") or "").strip()
        if name == "Page":
            title = text or title
        elif name == "Line" or row.get("Line Source"):
            edges.append(((row.get("Line Source") or "").strip(), (row.get("Line Destination") or "").strip(), text))
        elif text:
            nodes.append(((row.get("Id") or "").strip(), text, (row.get("Comments") or "").strip()))
    return title, _flow_text(title, nodes, edges, [])


def parse_lucid(sf: SourceFile, ctx: ParseContext) -> list[Chunk]:
    raw = read_text(sf.abs_path).lstrip("﻿")
    fallback = title_from_path(sf.abs_path)
    suffix = sf.abs_path.suffix.lower()
    if suffix == ".json":
        title, text = _from_json(raw, fallback)
    elif suffix == ".csv":
        title, text = _from_csv(raw, fallback)
    else:
        text = clean_text(raw)
        first = next((line.strip() for line in text.split("\n") if line.strip()), fallback)
        title = first.split("(")[0].strip() or fallback
    text = mask_secrets(clean_text(text), kv_lines=False)

    settings = ctx.settings.chunking
    if estimate_tokens(text) <= settings.max_tokens:
        pieces = [(text, 1, text.count("\n") + 1)]
    else:
        pieces = split_by_size(text, settings.target_tokens, settings.max_tokens, 0)
    label = ctx.source_label or "Lucid charts"
    chunks = []
    for index, (piece, _, _) in enumerate(pieces):
        body = piece if index == 0 else f"Diagram: {title} (continued)\n{piece}"
        chunk = Chunk(
            chunk_id=make_chunk_id(sf.rel_path, str(index)),
            source=sf.source,
            kind="diagram",
            path=sf.rel_path,
            citation=title if len(pieces) == 1 else f"{title} (part {index + 1})",
            text=body,
            header=f"{label} › {title}",
            location={"diagram": title, "part": index + 1},
            title=title,
            parent_id=sf.rel_path,
        )
        chunks.append(tag(chunk, ctx))
    return chunks
