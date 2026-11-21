"""Traceability matrix (story → Zephyr test cases → Gherkin scenarios → gaps) built from the index, plus Excel export."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from io import BytesIO
from typing import Literal

HEADERS = ["Story key", "Story title", "Test case key", "Test case name", "Status", "Automated scenario",
           "Gap / link type"]
LINKED, SUGGESTED = "Linked", "Suggested"
NO_TESTCASE, NO_STORY, NOT_AUTOMATED = "Gap: no test case", "Gap: no story", "Gap: not automated"
MAX_SUGGESTIONS = 5
SUGGESTED_FILL, GAP_FONT = "FFF2CC", "C00000"

Suggester = Callable[[str, str], list[str]]


class TraceNotFound(LookupError):
    pass


@dataclass
class TraceData:
    testcases: dict[str, dict]  # TC key -> zephyr meta with resolved linked_keys
    stories: dict[str, dict]  # story key -> {title, summary, page_title}
    scenarios: list[dict] = field(default_factory=list)  # {name, tags, repo, url}


@dataclass
class Row:
    story_key: str
    story_title: str
    testcase_key: str
    testcase_name: str
    status: str
    automated: str
    gap: str


@dataclass
class Matrix:
    rows: list[Row]
    summary: dict


def load_trace_data(store, id_to_key: dict[str, str]) -> TraceData:
    testcases: dict[str, dict] = {}
    summaries: dict[str, str] = {}
    page_titles: dict[str, str] = {}
    scenarios: dict[tuple, dict] = {}
    for p in store.scroll_sources(["zephyr_", "jira_linked", "confluence", "gh_"]):
        source, kind, meta = p.get("source", ""), p.get("kind", ""), p.get("meta") or {}
        if source.startswith("zephyr_") and kind == "testcase":
            key = p.get("parent_id") or p.get("citation", "")
            if key and key not in testcases:
                resolved = [id_to_key[str(i)] for i in meta.get("issue_ids") or [] if str(i) in id_to_key]
                links = list(dict.fromkeys([*(meta.get("linked_keys") or []), *resolved]))
                testcases[key] = {**meta, "linked_keys": links}
        elif source == "jira_linked":
            summaries.setdefault(p.get("citation", ""), meta.get("summary") or "")
        elif source == "confluence":
            for key in meta.get("jira_keys") or []:
                page_titles.setdefault(key, meta.get("title") or "")
        elif kind == "scenario":
            # Split pieces of one scenario share its line; same-named scenarios in one file do not.
            ident = (source, p.get("parent_id") or p.get("citation", ""), (p.get("location") or {}).get("line"))
            scenarios.setdefault(ident, {"name": p.get("citation", ""), "tags": list(meta.get("tags") or []),
                                         "repo": (p.get("header") or "").split(" › ")[0], "url": meta.get("url", "")})
    stories = {}
    for key in dict.fromkeys([*summaries, *page_titles]):
        summary, page_title = summaries.get(key, ""), page_titles.get(key, "")
        stories[key] = {"title": summary or page_title, "summary": summary, "page_title": page_title}
    return TraceData(testcases, stories, list(scenarios.values()))


def _natural(key: str) -> list:
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", key)]


def _norm_tag(tag: str) -> str:
    return tag.lstrip("@").lower()


def build_matrix(data: TraceData, scope: Literal["story", "folder", "project"], value: str, project: str | None,
                 suggest: Suggester | None) -> Matrix:
    not_found = TraceNotFound(f"No {scope} named {value} in the index — run Sync now if it is new.")
    if scope == "story":
        in_scope = {k: tc for k, tc in data.testcases.items() if value in tc.get("linked_keys", [])}
        if value not in data.stories and not in_scope:
            raise not_found
        story_keys = [value]
    else:
        if scope == "folder":
            if not project:
                raise ValueError("Folder scope needs a project")
            in_scope = {k: tc for k, tc in data.testcases.items() if tc.get("project") == project
                        and (tc.get("folder") == value or (tc.get("folder") or "").startswith(value + " / "))}
        elif scope == "project":
            in_scope = {k: tc for k, tc in data.testcases.items() if tc.get("project") == value}
        else:
            raise ValueError(f"Unknown scope {scope}")
        if not in_scope:
            raise not_found
        story_keys = list(dict.fromkeys(s for tc in in_scope.values() for s in tc.get("linked_keys", [])))

    by_tag: dict[str, list[str]] = {}
    for sc in data.scenarios:
        for tag in dict.fromkeys(_norm_tag(t) for t in sc.get("tags", [])):
            by_tag.setdefault(tag, []).append(sc["name"])

    def automated(key: str) -> str:
        return ", ".join(by_tag.get(_norm_tag(key), []))

    def tc_row(story_key: str, title: str, tc_key: str, gap: str | None) -> Row:
        tc = data.testcases.get(tc_key, {})
        auto = automated(tc_key)
        return Row(story_key, title, tc_key, tc.get("name", ""), tc.get("status", ""), auto,
                   gap or (LINKED if auto else NOT_AUTOMATED))

    story_rows: list[Row] = []
    covered = 0
    for story_key in story_keys:
        story = data.stories.get(story_key, {})
        title = story.get("title", "")
        linked = [k for k, tc in in_scope.items() if story_key in tc.get("linked_keys", [])]
        if linked:
            covered += 1
            story_rows.extend(tc_row(story_key, title, k, None) for k in linked)
        else:
            story_rows.append(Row(story_key, title, "", "", "", automated(story_key), NO_TESTCASE))
        if suggest:
            text = " ".join(dict.fromkeys(t for t in (title, story.get("summary"), story.get("page_title")) if t))
            already = {k for k, tc in data.testcases.items() if story_key in tc.get("linked_keys", [])}
            picks = [k for k in dict.fromkeys(suggest(story_key, text)) if k in data.testcases and k not in already]
            story_rows.extend(tc_row(story_key, title, k, SUGGESTED) for k in picks[:MAX_SUGGESTIONS])
    story_rows.sort(key=lambda r: (_natural(r.story_key), _natural(r.testcase_key)))

    orphans = [tc_row("", "", k, NO_STORY) for k, tc in in_scope.items() if not tc.get("linked_keys")]
    orphans.sort(key=lambda r: _natural(r.testcase_key))

    automated_count = sum(1 for k in in_scope if automated(k))
    summary = {
        "stories": len(story_keys),
        "stories_covered": covered,
        "coverage_pct": round(100 * covered / len(story_keys), 1) if story_keys else 0.0,
        "testcases": len(in_scope),
        "automated_pct": round(100 * automated_count / len(in_scope), 1) if in_scope else 0.0,
        "suggestions": sum(1 for r in story_rows if r.gap == SUGGESTED),
    }
    return Matrix(story_rows + orphans, summary)


def make_suggester(searcher, zephyr_sources: list[str]) -> Suggester:
    threshold = searcher.settings.retrieval.min_rerank_score

    def suggest(story_key: str, text: str) -> list[str]:
        result = searcher.search(text, sources=zephyr_sources)
        return list(dict.fromkeys(h.citation for h in result.hits if h.score >= threshold))

    return suggest


def to_xlsx(matrix: Matrix, title: str) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill

    wb = Workbook()
    ws = wb.active
    ws.title = "Matrix"
    ws.append(HEADERS)
    for cell in ws[1]:
        cell.font = Font(bold=True)
    fill = PatternFill(start_color=SUGGESTED_FILL, end_color=SUGGESTED_FILL, fill_type="solid")
    red = Font(color=GAP_FONT)
    for r in matrix.rows:
        ws.append([r.story_key, r.story_title, r.testcase_key, r.testcase_name, r.status, r.automated, r.gap])
        if r.gap == SUGGESTED:
            for cell in ws[ws.max_row]:
                cell.fill = fill
        elif r.gap.startswith("Gap:"):
            for cell in ws[ws.max_row]:
                cell.font = red
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:G{len(matrix.rows) + 1}"
    for column, width in zip("ABCDEFG", (14, 40, 16, 48, 12, 48, 20)):
        ws.column_dimensions[column].width = width

    s = wb.create_sheet("Summary")
    s.append([title])
    s["A1"].font = Font(bold=True)
    m = matrix.summary
    for label, key in (("Stories in scope", "stories"), ("Stories covered", "stories_covered"),
                       ("Coverage %", "coverage_pct"), ("Test cases", "testcases"),
                       ("Test cases automated %", "automated_pct"), ("Suggestions", "suggestions")):
        s.append([label, m[key]])
    s.column_dimensions["A"].width = 26

    out = BytesIO()
    wb.save(out)
    return out.getvalue()


def xlsx_filename(scope: str, value: str, today: date) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", f"{scope}_{value}").strip("_")
    return f"Traceability_{safe}_{today.isoformat()}.xlsx"
