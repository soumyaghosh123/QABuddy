"""Question preparation: ID extraction, follow-up handling, glossary and identifier expansion."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from qabuddy.ingest.normalize import expand_with_glossary, extract_ids, split_identifiers

HYPHEN_KEY_RE = re.compile(r"\b([A-Za-z][A-Za-z0-9]{1,9})-(\d+)\b")
SPACED_KEY_RE = re.compile(r"\b([A-Za-z][A-Za-z0-9]{1,9})\s+(\d+)\b")
FOLLOW_UP_RE = re.compile(r"\b(it|its|this|that|they|them|those|these|same)\b", re.IGNORECASE)


@dataclass
class PreparedQuery:
    retrieval_text: str
    bm25_text: str
    ids: list[str]


def extract_query_ids(text: str, known: set[str]) -> list[str]:
    ids: list[str] = []

    def add(value: str) -> None:
        if value not in ids:
            ids.append(value)

    for prefix, number in HYPHEN_KEY_RE.findall(text):
        if not prefix.lower().startswith("build"):
            add(f"{prefix}-{number}".lower())
    for prefix, number in SPACED_KEY_RE.findall(text):
        candidate = f"{prefix}-{number}".lower()
        if candidate in known:
            add(candidate)
    for value in extract_ids(text, known):
        add(value)
    return ids


TESTCASE_INTENT_RE = re.compile(r"\btest ?cases?\b|\btc\b", re.IGNORECASE)
STORY_INTENT_RE = re.compile(r"\buser ?stor(y|ies)\b|\bstor(y|ies)\b|\brequirements?\b", re.IGNORECASE)


def detect_intent(question: str) -> Literal["find_testcases", "find_stories"] | None:
    if TESTCASE_INTENT_RE.search(question):
        return "find_testcases"
    if STORY_INTENT_RE.search(question):
        return "find_stories"
    return None


def _is_follow_up(question: str) -> bool:
    return len(question.split()) < 6 or bool(FOLLOW_UP_RE.search(question))


def prepare_query(question: str, history: list[dict], known: set[str], glossary: dict) -> PreparedQuery:
    question = question.strip()
    previous = next((m["content"] for m in reversed(history or []) if m.get("role") == "user"), None)
    retrieval_text = f"{previous} {question}" if previous and _is_follow_up(question) else question
    bm25_text = expand_with_glossary(retrieval_text, glossary)
    parts = split_identifiers(retrieval_text)
    if parts:
        bm25_text = f"{bm25_text} {parts}"
    return PreparedQuery(retrieval_text, bm25_text, extract_query_ids(retrieval_text, known))
