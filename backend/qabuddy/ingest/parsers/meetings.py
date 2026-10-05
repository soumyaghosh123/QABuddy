"""Meeting notes and transcripts (.vtt, .srt, .txt, .md)."""

from __future__ import annotations

import re
from dataclasses import dataclass

from qabuddy.ingest.chunking import estimate_tokens
from qabuddy.ingest.context import ParseContext, tag
from qabuddy.ingest.models import Chunk, SourceFile, make_chunk_id
from qabuddy.ingest.normalize import clean_text, mask_secrets, read_text
from qabuddy.ingest.parsers.documents import markdown_chunks, title_from_path

DATE_PREFIX_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})")
CUE_TIME_RE = re.compile(r"^(\d{1,2}:)?\d{2}:\d{2}[.,]\d{3}\s+-->")
VOICE_RE = re.compile(r"^<v\s+([^>]+)>(.*?)(</v>)?$")
TXT_TURN_RE = re.compile(r"^\[(\d{1,2}:\d{2}(?::\d{2})?)\]\s*([^:]{1,60}):\s*(.*)$")
META_RE = re.compile(r"^\**(Meeting|Date|Attendees)\**:\**\s*(.*)$", re.IGNORECASE)


@dataclass
class Turn:
    time: str
    speaker: str
    text: str

    def line(self) -> str:
        return f"[{self.time}] {self.speaker}: {self.text}"


def _hms(value: str) -> str:
    value = value.split(".")[0].split(",")[0]
    parts = value.split(":")
    while len(parts) < 3:
        parts.insert(0, "00")
    return ":".join(p.zfill(2) for p in parts)


def parse_vtt(text: str) -> list[Turn]:
    turns: list[Turn] = []
    for block in re.split(r"\n\s*\n", text):
        lines = [l.strip() for l in block.split("\n") if l.strip()]
        timing = next((i for i, l in enumerate(lines) if CUE_TIME_RE.match(l)), None)
        if timing is None:
            continue
        start = _hms(lines[timing].split("-->")[0].strip())
        for line in lines[timing + 1:]:
            voice = VOICE_RE.match(line)
            speaker, content = (voice.group(1).strip(), voice.group(2).strip()) if voice else ("", line)
            content = re.sub(r"<[^>]+>", "", content).strip()
            if turns and not speaker and turns[-1].time == start:
                turns[-1].text += f" {content}"
            else:
                turns.append(Turn(start, speaker or "Speaker", content))
    return turns


def parse_txt_transcript(text: str) -> tuple[list[Turn], dict[str, str]]:
    turns: list[Turn] = []
    meta: dict[str, str] = {}
    for line in text.split("\n"):
        stripped = line.strip()
        if not stripped:
            continue
        turn = TXT_TURN_RE.match(stripped)
        if turn:
            turns.append(Turn(_hms(turn.group(1)), turn.group(2).strip(), turn.group(3).strip()))
            continue
        meta_match = META_RE.match(stripped)
        if meta_match and not turns:
            meta[meta_match.group(1).lower()] = meta_match.group(2).strip()
        elif turns:
            turns[-1].text += f" {stripped}"
        else:
            meta.setdefault("notes", "")
            meta["notes"] += f" {stripped}"
    return turns, meta


def group_turns(turns: list[Turn], target: int = 350, overlap_turns: int = 1) -> list[list[Turn]]:
    groups: list[list[Turn]] = []
    current: list[Turn] = []
    fresh = 0
    for turn in turns:
        size = estimate_tokens("\n".join(t.line() for t in current + [turn]))
        if current and fresh and size > target:
            groups.append(current)
            current = current[-overlap_turns:] if overlap_turns else []
            fresh = 0
        current.append(turn)
        fresh += 1
    if current and (fresh or not groups):
        groups.append(current)
    return groups


def parse_meeting(sf: SourceFile, ctx: ParseContext) -> list[Chunk]:
    title = title_from_path(sf.abs_path)
    date_match = DATE_PREFIX_RE.match(sf.abs_path.stem)
    date = date_match.group(1) if date_match else ""
    suffix = sf.abs_path.suffix.lower()
    raw = mask_secrets(clean_text(read_text(sf.abs_path)), kv_lines=False)
    if suffix == ".md":
        if not date:
            found = re.search(r"Date:\**\s*(\d{4}-\d{2}-\d{2})", raw)
            date = found.group(1) if found else ""
        return markdown_chunks(sf, ctx, "transcript", title=title, date=date)

    if suffix in (".vtt", ".srt"):
        turns, meta = parse_vtt(raw), {}
    else:
        turns, meta = parse_txt_transcript(raw)
    date = date or re.sub(r"[^\d-]", "", meta.get("date", ""))[:10]
    attendees = meta.get("attendees") or ", ".join(dict.fromkeys(t.speaker for t in turns))
    header_line = f"Meeting: {meta.get('meeting', title)} | Date: {date or 'unknown'} | Attendees: {attendees}"
    if not turns:
        turns = [Turn("00:00:00", "Notes", raw)]

    chunks: list[Chunk] = []
    label = ctx.source_label or "Meeting notes"
    for group in group_turns(turns, target=350, overlap_turns=1):
        start = group[0].time
        text = header_line + "\n" + "\n".join(t.line() for t in group)
        chunk = Chunk(
            chunk_id=make_chunk_id(sf.rel_path, start),
            source=sf.source,
            kind="transcript",
            path=sf.rel_path,
            citation=f"{title} {date} @{start}".replace("  ", " "),
            text=text,
            header=f"{label} › {title} › {date} @{start}",
            location={"time": start},
            title=title,
            parent_id=sf.rel_path,
            date=date,
        )
        chunks.append(tag(chunk, ctx))
    return chunks
