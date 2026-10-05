"""Text cleanup, secret masking, identifier splitting, ID extraction and glossary expansion."""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path

import yaml

ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
SECRET_KEY_RE = re.compile(r"(?i)(pass(word)?|pwd|secret|token|api[_-]?key|auth|credential|user(_?name)?|e-?mail)")
# key=value / key: value lines (properties, .env, YAML)
KV_LINE_RE = re.compile(r"^(\s*(?:export\s+)?)([A-Za-z0-9_.\-]+)(\s*[=:]\s*)(.*?)\s*$")
# "key": "value" anywhere (JSON)
QUOTED_KV_RE = re.compile(r"""(["'])([A-Za-z0-9_.\-]+)\1(\s*:\s*)(["'])(.*?)\4""")
# key = "value" / key: 'value' anywhere (code literals)
ASSIGN_LITERAL_RE = re.compile(r"""\b([A-Za-z_][A-Za-z0-9_]*)(\s*[=:]\s*)(["'])([^"'\n]+)\3""")
COMMENT_PREFIXES = ("#", "//", ";", "!")
MASK = "***"

TICKET_RE = re.compile(r"\b([A-Z][A-Z0-9]{1,9})-(\d+)\b")
BUILD_RE = re.compile(r"(?:\bbuild\s*#?\s*(\d{1,6})\b|#(\d{2,6})\b)", re.IGNORECASE)
IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*")
WORD_PART_RE = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+")
TOKEN_RE = re.compile(r"[A-Za-z0-9_\-]+")


def read_text(path: Path) -> str:
    data = path.read_bytes()
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1")


def clean_text(text: str) -> str:
    text = unicodedata.normalize("NFC", text).replace("﻿", "")
    text = ANSI_RE.sub("", text).replace("\r\n", "\n").replace("\r", "\n")
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    text = re.sub(r"\n{4,}", "\n\n\n", text)
    return text.strip("\n")


def _is_secret(key: str) -> bool:
    return bool(SECRET_KEY_RE.search(key))


def mask_secrets(text: str, kv_lines: bool = True) -> str:
    """Replace values of secret-looking keys with ***.

    kv_lines=True also masks unquoted `key=value` / `key: value` lines (config files);
    quoted JSON pairs and string literals assigned to secret-named keys are masked everywhere.
    """
    text = QUOTED_KV_RE.sub(
        lambda m: f"{m.group(1)}{m.group(2)}{m.group(1)}{m.group(3)}{m.group(4)}{MASK}{m.group(4)}"
        if _is_secret(m.group(2)) and m.group(5) else m.group(0),
        text,
    )
    text = ASSIGN_LITERAL_RE.sub(
        lambda m: f"{m.group(1)}{m.group(2)}{m.group(3)}{MASK}{m.group(3)}" if _is_secret(m.group(1)) else m.group(0),
        text,
    )
    if not kv_lines:
        return text
    out = []
    for line in text.split("\n"):
        m = KV_LINE_RE.match(line)
        if m and not line.lstrip().startswith(COMMENT_PREFIXES) and m.group(4) and _is_secret(m.group(2)):
            line = f"{m.group(1)}{m.group(2)}{m.group(3)}{MASK}"
        out.append(line)
    return "\n".join(out)


def _parts(token: str) -> list[str]:
    parts: list[str] = []
    for piece in re.split(r"[._]", token):
        parts.extend(WORD_PART_RE.findall(piece))
    return [p.lower() for p in parts if p]


def split_identifiers(text: str) -> str:
    """Space-joined word parts of every camelCase / PascalCase / snake_case / dotted identifier."""
    seen: list[str] = []
    for token in IDENT_RE.findall(text):
        parts = _parts(token)
        if len(parts) > 1:
            expansion = " ".join(parts)
            if expansion not in seen:
                seen.append(expansion)
    return " ".join(seen)


def extract_ids(text: str, known: set[str]) -> list[str]:
    ids: list[str] = []

    def add(value: str) -> None:
        if value not in ids:
            ids.append(value)

    for prefix, number in TICKET_RE.findall(text):
        add(f"{prefix}-{number}".lower())
    for a, b in BUILD_RE.findall(text):
        add(f"build-{a or b}")
    if known:
        for token in TOKEN_RE.findall(text):
            low = token.lower()
            if low in known:
                add(low)
        for token in IDENT_RE.findall(text):
            for piece in token.split("."):
                low = piece.lower()
                if low in known:
                    add(low)
    return ids


def load_glossary(path: Path) -> dict[str, list[str]]:
    if not path.exists():
        return {}
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return {str(k).lower(): [str(v) for v in values] for k, values in raw.items()}


def expand_with_glossary(text: str, glossary: dict[str, list[str]]) -> str:
    extra: list[str] = []
    for word in re.findall(r"[A-Za-z0-9/]+", text):
        for expansion in glossary.get(word.lower(), []):
            if expansion not in extra:
                extra.append(expansion)
    return f"{text} {' '.join(extra)}" if extra else text
