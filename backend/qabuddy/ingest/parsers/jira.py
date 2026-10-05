"""JIRA tickets from Jira 'printable' Markdown/text exports.

A live JIRA MCP + JQL loader can be added later behind the same JiraLoader protocol.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Protocol

from qabuddy.ingest.chunking import estimate_tokens, split_by_size
from qabuddy.ingest.context import ParseContext, tag
from qabuddy.ingest.models import Chunk, SourceFile, make_chunk_id
from qabuddy.ingest.normalize import clean_text, mask_secrets, read_text

HEADER_RE = re.compile(r"^\[([A-Z][A-Z0-9]+-\d+)\]\s+(.*?)\s+Created:\s*(\S+)\s+Updated:\s*(\S+)")
FILENAME_KEY_RE = re.compile(r"([A-Z][A-Z0-9]+)[-_](\d+)")
KEEP_FIELDS = ["Type", "Status", "Priority", "Resolution", "Project", "Components", "Labels",
               "Affects versions", "Fix versions", "Sprint", "Reporter", "Assignee"]
NOISE_LINES = ("Back to previous view", "Generated at ")


class JiraLoader(Protocol):
    def load(self) -> list[Chunk]: ...


class MarkdownExportLoader:
    def __init__(self, files: list[SourceFile], ctx: ParseContext):
        self.files, self.ctx = files, ctx

    def load(self) -> list[Chunk]:
        return [chunk for sf in self.files for chunk in parse_jira_markdown(sf, self.ctx)]


def _iso(value: str) -> str:
    try:
        return datetime.strptime(value, "%d/%b/%y").date().isoformat()
    except ValueError:
        return ""


def _parse_fields(line: str) -> dict[str, str]:
    tokens = [t.strip() for t in line.split("\t")]
    fields: dict[str, str] = {}
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token.endswith(":"):
            value = tokens[i + 1] if i + 1 < len(tokens) and not tokens[i + 1].endswith(":") else ""
            fields[token[:-1].strip()] = value
            i += 2 if value else 1
        else:
            i += 1
    return fields


def parse_jira_markdown(sf: SourceFile, ctx: ParseContext) -> list[Chunk]:
    text = clean_text(read_text(sf.abs_path))
    lines = [line for line in text.split("\n") if not line.strip().startswith(NOISE_LINES)]
    key = summary = created = updated = ""
    fields: dict[str, str] = {}
    description: list[str] = []
    comments: list[str] = []
    mode = "fields"
    for line in lines:
        stripped = line.strip()
        header = HEADER_RE.match(stripped)
        if header and not key:
            key, summary, created, updated = header.groups()
            continue
        if stripped == "Description":
            mode = "description"
            continue
        if stripped == "Comments":
            mode = "comments"
            continue
        if mode == "fields":
            if "\t" in line and ":" in line:
                fields.update(_parse_fields(line))
        elif mode == "description":
            description.append(line)
        else:
            comments.append(line)
    if not key:
        match = FILENAME_KEY_RE.search(sf.abs_path.stem)
        key = f"{match.group(1)}-{match.group(2)}" if match else sf.abs_path.stem
        if not description:
            description = lines

    def field_line(names: list[str]) -> str:
        parts = [f"{n}: {fields[n]}" for n in names if fields.get(n) and fields[n] != "None"]
        return " | ".join(parts)

    head = [f"{key}: {summary}".rstrip(": ")]
    for group in (["Type", "Status", "Priority", "Resolution"],
                  ["Project", "Components", "Labels", "Affects versions", "Fix versions", "Sprint"],
                  ["Reporter", "Assignee"]):
        line = field_line(group)
        if line:
            head.append(line)
    if created or updated:
        head.append(f"Created: {created} | Updated: {updated}")
    head_text = "\n".join(head)
    desc_text = "\n".join(description).strip()
    comment_text = "\n".join(comments).strip()
    body = head_text
    if desc_text:
        body += f"\n\nDescription:\n{desc_text}"
    if comment_text:
        body += f"\n\nComments:\n{comment_text}"
    body = mask_secrets(body, kv_lines=False)

    settings = ctx.settings.chunking
    if estimate_tokens(body) <= settings.max_tokens:
        pieces = [("full", body)]
    else:
        pieces = []
        for label, section in (("description", desc_text), ("comments", comment_text)):
            if not section:
                continue
            title = "Description" if label == "description" else "Comments"
            for i, (piece, _, _) in enumerate(split_by_size(section, settings.target_tokens,
                                                            settings.max_tokens - estimate_tokens(head_text) - 10,
                                                            settings.prose_overlap_tokens)):
                pieces.append((f"{label}-{i}", f"{head_text}\n\n{title}:\n{piece}"))

    component = fields.get("Components", "")
    chunks = []
    for locator, piece in pieces:
        chunk = Chunk(
            chunk_id=make_chunk_id(sf.rel_path, f"{key}#{locator}"),
            source=sf.source,
            kind="ticket",
            path=sf.rel_path,
            citation=key,
            text=piece,
            header=f"{ctx.source_label or 'JIRA'} › {key} › {summary}",
            location={"ticket": key},
            title=summary,
            parent_id=key,
            module="" if component in ("", "None") else component,
            date=_iso(created),
        )
        chunks.append(tag(chunk, ctx, [key]))
    return chunks
