"""Prompt assembly with a source-token budget, and citation checking."""

from __future__ import annotations

import re

from qabuddy.ingest.chunking import estimate_tokens
from qabuddy.retrieval.search import Hit

SYSTEM_PROMPT = """You are QABuddy, an assistant for the QA engineering team.
Answer the question using only the numbered sources provided in the user message.

Rules:
- Cite every claim with the source number in square brackets, like [1] or [2]. Use the form [n].
- If the sources do not contain the answer, say that you could not find it in the knowledge base and suggest where to look. Do not guess.
- Never invent test case IDs, ticket keys, file names, build numbers or results that are not in the sources.
- Be concise: short paragraphs or bullet points. Use numbered steps for procedures.
- When showing code, copy it from the sources inside a fenced code block with the language name.
- For test design requests (new test cases, test plans, gap analysis), base them on the cited requirements and existing tests, and label anything new as proposed.
"""
HISTORY_ANSWER_TOKENS = 300
CITATION_RE = re.compile(r"\[(\d+(?:\s*,\s*\d+)*)\]")


def _source_block(hit: Hit) -> str:
    return f"[{hit.n}] {hit.citation} — {hit.source}\n{hit.header}\n{hit.text}"


def _truncate(text: str, tokens: int) -> str:
    limit = tokens * 4
    return text if len(text) <= limit else text[:limit].rsplit(" ", 1)[0] + " …"


def build_messages(question: str, hits: list[Hit], history: list[dict], max_source_tokens: int,
                   history_turns: int) -> tuple[list[dict], list[Hit]]:
    used: list[Hit] = []
    blocks: list[str] = []
    total = estimate_tokens("Sources:\n\n")
    for hit in hits:
        block = _source_block(hit)
        size = estimate_tokens(block + "\n\n")
        if total + size > max_source_tokens:
            break
        blocks.append(block)
        used.append(hit)
        total += size

    pairs: list[tuple[str, str]] = []
    pending_user: str | None = None
    for message in history or []:
        if message.get("role") == "user":
            pending_user = message.get("content", "")
        elif message.get("role") == "assistant" and pending_user is not None:
            pairs.append((pending_user, message.get("content", "")))
            pending_user = None
    history_messages: list[dict] = []
    for user, assistant in pairs[-history_turns:] if history_turns else []:
        history_messages.append({"role": "user", "content": _truncate(user, HISTORY_ANSWER_TOKENS)})
        history_messages.append({"role": "assistant", "content": _truncate(assistant, HISTORY_ANSWER_TOKENS)})

    content = "Sources:\n\n" + "\n\n".join(blocks) + f"\n\nQuestion: {question.strip()}"
    return [{"role": "system", "content": SYSTEM_PROMPT}, *history_messages, {"role": "user", "content": content}], used


def check_citations(answer: str, k: int) -> tuple[list[int], bool]:
    used: set[int] = set()
    for group in CITATION_RE.findall(answer):
        for value in group.split(","):
            number = int(value.strip())
            if 1 <= number <= k:
                used.add(number)
    return sorted(used), not used
