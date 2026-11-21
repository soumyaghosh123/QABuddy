"""Group retrieved chunks into distinct items (test case / story / page / scenario) for the Matches list."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from qabuddy.retrieval.search import Hit

MatchType = Literal["testcase", "story", "page", "scenario", "code", "other"]


@dataclass
class Match:
    type: MatchType
    key: str
    name: str
    where: str
    status: str
    url: str
    n: list[int] = field(default_factory=list)


def _match_type(hit: Hit) -> MatchType:
    if hit.kind == "testcase":
        return "testcase"
    if hit.kind in ("jira", "ticket"):
        return "story"
    if hit.source == "confluence":
        return "page"
    if hit.kind == "scenario":
        return "scenario"
    if hit.source.startswith("gh_"):
        return "code"
    return "other"


def _first_header_segment(hit: Hit) -> str:
    return hit.header.split(" › ")[0]


def _space(hit: Hit) -> str:
    parts = hit.path.split("/")
    return parts[1] if len(parts) > 1 else ""


def _make_match(hit: Hit) -> Match:
    kind, meta = _match_type(hit), hit.meta or {}
    key, name, where, status = "", hit.citation, "", ""
    if kind == "testcase":
        key, name, where, status = hit.citation, meta.get("name", ""), meta.get("folder", ""), meta.get("status", "")
    elif kind == "story":
        key, name = hit.citation, meta.get("summary") or hit.citation
        where, status = meta.get("type", ""), meta.get("status", "")
    elif kind == "page":
        jira_keys = meta.get("jira_keys") or []
        key, name, where = (jira_keys[0] if jira_keys else ""), meta.get("title") or hit.citation, _space(hit)
    elif kind in ("scenario", "code"):
        where = _first_header_segment(hit)
    return Match(kind, key, name, where, status, meta.get("url") or "", [hit.n])


def group_matches(hits: list[Hit]) -> list[Match]:
    """One Match per parent (falling back to chunk id), ordered by first hit; `n` lists the grouped hit numbers."""
    grouped: dict[str, Match] = {}
    for hit in hits:
        group = hit.parent_id or hit.chunk_id
        if group in grouped:
            grouped[group].n.append(hit.n)
        else:
            grouped[group] = _make_match(hit)
    return list(grouped.values())
