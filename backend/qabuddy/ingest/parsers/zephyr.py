"""Zephyr Scale test cases (one JSON file per case, written by the Zephyr connector): one case = one chunk."""

from __future__ import annotations

import json
import re

from qabuddy.ingest.chunking import estimate_tokens, split_by_size
from qabuddy.ingest.context import ParseContext, tag
from qabuddy.ingest.models import Chunk, SourceFile, make_chunk_id
from qabuddy.ingest.normalize import clean_html, clean_text, read_text
from qabuddy.ingest.parsers.testcases import format_testcase


_HTML_RE = re.compile(r"(?i)<\s*/?\s*(?:p|br|div|li|ul|ol|span|strong|em|table|b|i|a|h[1-6])(?![A-Za-z0-9])[^>]*>")


def _text(value: str | None) -> str:
    """HTML fields are converted to text; plain text (Gherkin <placeholders>, `x < 5`) is left alone."""
    value = value or ""
    return clean_html(value) if _HTML_RE.search(value) else clean_text(value)


def _step_line(step: dict) -> str:
    inline = step.get("inline")
    if not inline:
        call = step.get("testCase") or {}
        called = call.get("testCaseKey") or call.get("key") or ""
        return f"Call test case {called}".strip()
    parts = [_text(inline.get("description") or "")]
    if data := _text(inline.get("testData") or ""):
        parts.append(f"Data: {data}")
    if expected := _text(inline.get("expectedResult") or ""):
        parts.append(f"Expected: {expected}")
    return " — ".join(p for p in parts if p)


def _steps_text(data: dict) -> str:
    lines = [f"{i}. {_step_line(s)}" for i, s in enumerate(data.get("steps") or [], 1)]
    if lines:
        return "\n".join(lines)
    return _text((data.get("script") or {}).get("text") or "")


def parse_zephyr(sf: SourceFile, ctx: ParseContext) -> list[Chunk]:
    data = json.loads(read_text(sf.abs_path))
    tc = data["testcase"]
    key = tc["key"]
    project = sf.rel_path.split("/")[1] if sf.rel_path.startswith("zephyr/") else ""
    folder = data.get("folder_path") or ""
    module = folder.split(" / ")[0] if folder else ""
    title = _text(tc.get("name") or "")
    extras: list[tuple[str, str]] = []
    if objective := _text(tc.get("objective") or ""):
        extras.append(("Objective", objective))
    if folder:
        extras.append(("Folder", folder))
    if tc.get("labels"):
        extras.append(("Labels", ", ".join(tc["labels"])))
    record = {"id": key, "title": title, "module": module, "precondition": _text(tc.get("precondition") or ""),
              "steps": _steps_text(data), "status": data.get("status_name") or "",
              "priority": data.get("priority_name") or ""}
    text = format_testcase(record, extras)

    settings = ctx.settings.chunking
    pieces = [(text, 0, 0)] if estimate_tokens(text) <= settings.max_tokens else split_by_size(
        text, settings.target_tokens, settings.max_tokens, settings.prose_overlap_tokens)
    meta = {"name": title, "status": record["status"], "priority": record["priority"], "folder": folder,
            "project": project, "issue_ids": data.get("issue_ids") or [], "linked_keys": [],
            "url": ctx.settings.desktop.zephyr_ui_url.format(project=project, key=key)}
    chunks: list[Chunk] = []
    for index, (piece, _, _) in enumerate(pieces):
        chunk = Chunk(
            chunk_id=make_chunk_id(sf.rel_path, key if index == 0 else f"{key}#{index}"),
            source=sf.source,
            kind="testcase",
            path=sf.rel_path,
            citation=key,
            text=piece,
            header=f"{ctx.source_label or 'Zephyr'} › {key}",
            title=title,
            parent_id=key,
            module=module,
            meta=dict(meta),
        )
        chunks.append(tag(chunk, ctx, [key]))
    return chunks
