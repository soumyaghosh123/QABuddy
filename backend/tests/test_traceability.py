from datetime import date
from io import BytesIO
from types import SimpleNamespace

import openpyxl
import pytest

from qabuddy.retrieval.search import Hit, SearchResult
from qabuddy.traceability import (
    Row,
    TraceData,
    TraceNotFound,
    build_matrix,
    load_trace_data,
    make_suggester,
    to_xlsx,
    xlsx_filename,
)


def _tc(name, folder, links, project="AUSWEB", status="Approved"):
    return {"name": name, "status": status, "priority": "Normal", "folder": folder, "project": project,
            "issue_ids": [], "linked_keys": links, "url": ""}


def _data():
    return TraceData(
        testcases={
            "AUSWEB-T1224": _tc("Close account with open position", "R76 SIT / Login", ["ORION-5510"]),
            "AUSWEB-T1300": _tc("Close account audit", "R75 SIT", ["ORION-5510"], status="Draft"),
            "AUSWEB-T2900": _tc("Orphan check", "R76 SIT", []),
            "AUSWEB-T3000": _tc("Similar closure flow", "R70", []),
            "OTHER-T1": _tc("Other project", "R76 SIT", ["ORION-99"], project="OTHER"),
        },
        stories={
            "ORION-5510": {"title": "Close account", "summary": "Close account", "page_title": "Closure page"},
            "ORION-6413": {"title": "Change address", "summary": "", "page_title": "Change address"},
            "ORION-99": {"title": "Old story", "summary": "Old story", "page_title": ""},
        },
        scenarios=[
            {"name": "Close account with open position", "tags": ["@AUSWEB", "@smoke", "@AUSWEB-T1224"],
             "repo": "CTX.Web.Automation", "url": ""},
            {"name": "Orphan scenario", "tags": ["@auswEB-t2900"], "repo": "CTX.Web.Automation", "url": ""},
        ],
    )


def test_story_scope():
    m = build_matrix(_data(), "story", "ORION-5510", None, None)
    assert m.rows == [
        Row("ORION-5510", "Close account", "AUSWEB-T1224", "Close account with open position", "Approved",
            automated="Close account with open position", gap="Linked"),
        Row("ORION-5510", "Close account", "AUSWEB-T1300", "Close account audit", "Draft",
            automated="", gap="Gap: not automated"),
    ]
    assert m.summary == {"stories": 1, "stories_covered": 1, "coverage_pct": 100.0, "testcases": 2,
                         "automated_pct": 50.0, "suggestions": 0}


def test_story_without_testcase_is_gap():
    m = build_matrix(_data(), "story", "ORION-6413", None, None)
    assert m.rows == [Row("ORION-6413", "Change address", "", "", "", automated="", gap="Gap: no test case")]
    assert m.summary["stories_covered"] == 0 and m.summary["coverage_pct"] == 0.0
    assert m.summary["automated_pct"] == 0.0


def test_story_known_only_from_testcase_link():
    d = _data()
    d.testcases["AUSWEB-T5"] = _tc("x", "R1", ["ORION-777"])
    m = build_matrix(d, "story", "ORION-777", None, None)
    assert m.rows[0].story_title == "" and m.rows[0].gap == "Gap: not automated"


def test_story_row_without_testcase_uses_story_tag():
    d = _data()
    d.scenarios.append({"name": "Address change", "tags": ["@ORION-6413"], "repo": "r", "url": ""})
    assert build_matrix(d, "story", "ORION-6413", None, None).rows[0].automated == "Address change"


def test_folder_scope_includes_unlinked_testcase():
    m = build_matrix(_data(), "folder", "R76 SIT", "AUSWEB", None)
    assert [(r.story_key, r.testcase_key, r.gap) for r in m.rows] == [
        ("ORION-5510", "AUSWEB-T1224", "Linked"),
        ("", "AUSWEB-T2900", "Gap: no story"),
    ]
    assert m.rows[1].automated == "Orphan scenario"
    assert m.summary == {"stories": 1, "stories_covered": 1, "coverage_pct": 100.0, "testcases": 2,
                         "automated_pct": 100.0, "suggestions": 0}


def test_folder_scope_needs_project_and_ignores_prefix_lookalikes():
    with pytest.raises(ValueError):
        build_matrix(_data(), "folder", "R76 SIT", None, None)
    d = _data()
    d.testcases["AUSWEB-T4000"] = _tc("lookalike", "R76 SITX", [])
    keys = [r.testcase_key for r in build_matrix(d, "folder", "R76 SIT", "AUSWEB", None).rows]
    assert "AUSWEB-T4000" not in keys and "OTHER-T1" not in keys


def test_project_scope_orders_rows_naturally():
    d = _data()
    d.testcases["AUSWEB-T99"] = _tc("early", "R1", ["ORION-99"])
    m = build_matrix(d, "project", "AUSWEB", None, None)
    assert [(r.story_key, r.testcase_key) for r in m.rows] == [
        ("ORION-99", "AUSWEB-T99"),
        ("ORION-5510", "AUSWEB-T1224"),
        ("ORION-5510", "AUSWEB-T1300"),
        ("", "AUSWEB-T2900"),
        ("", "AUSWEB-T3000"),
    ]
    assert m.summary["stories"] == 2 and m.summary["testcases"] == 5


def test_not_automated_gap():
    m = build_matrix(_data(), "project", "AUSWEB", None, None)
    row = next(r for r in m.rows if r.testcase_key == "AUSWEB-T1300")
    assert row.gap == "Gap: not automated" and row.automated == ""


def test_multiple_scenarios_comma_separated():
    d = _data()
    d.scenarios.append({"name": "Second closure", "tags": ["AUSWEB-T1224"], "repo": "r", "url": ""})
    row = build_matrix(d, "story", "ORION-5510", None, None).rows[0]
    assert row.automated == "Close account with open position, Second closure"


def test_suggestions_excluded_from_coverage():
    calls = []

    def suggest(story_key, text):
        calls.append((story_key, text))
        return ["AUSWEB-T1224", "AUSWEB-T3000", "AUSWEB-T2900", "AUSWEB-T3000", "OTHER-T1", "AUSWEB-T1300",
                "UNKNOWN-T1", "AUSWEB-T5", "AUSWEB-T6", "AUSWEB-T7", "AUSWEB-T8"]

    d = _data()
    for k in ("AUSWEB-T5", "AUSWEB-T6", "AUSWEB-T7", "AUSWEB-T8"):
        d.testcases[k] = _tc(k, "R1", [])
    plain = build_matrix(d, "story", "ORION-6413", None, None)
    m = build_matrix(d, "story", "ORION-6413", None, suggest)
    assert calls == [("ORION-6413", "Change address")]
    suggested = [r for r in m.rows if r.gap == "Suggested"]
    assert [r.testcase_key for r in suggested] == ["AUSWEB-T1224", "AUSWEB-T1300", "AUSWEB-T2900",
                                                   "AUSWEB-T3000", "OTHER-T1"]
    assert suggested[0].automated == "Close account with open position"
    assert m.rows[0].gap == "Gap: no test case"
    assert m.summary["stories_covered"] == plain.summary["stories_covered"] == 0
    assert m.summary["testcases"] == 0 and m.summary["suggestions"] == 5


def test_suggestions_skip_already_linked_and_join_title_with_summary():
    calls = []
    m = build_matrix(_data(), "story", "ORION-5510", None,
                     lambda k, t: calls.append(t) or ["AUSWEB-T1224", "AUSWEB-T3000"])
    assert calls == ["Close account Closure page"]
    assert [(r.testcase_key, r.gap) for r in m.rows] == [
        ("AUSWEB-T1224", "Linked"), ("AUSWEB-T1300", "Gap: not automated"), ("AUSWEB-T3000", "Suggested")]


@pytest.mark.parametrize("scope,value,project", [
    ("story", "ORION-1", None), ("folder", "R99", "AUSWEB"), ("project", "NOPE", None)])
def test_unknown_value_raises(scope, value, project):
    with pytest.raises(TraceNotFound, match=f"No {scope} named {value} in the index — run Sync now if it is new."):
        build_matrix(_data(), scope, value, project, None)


def test_xlsx_sheets_and_styles():
    m = build_matrix(_data(), "project", "AUSWEB", None, lambda k, t: ["AUSWEB-T3000"])
    wb = openpyxl.load_workbook(BytesIO(to_xlsx(m, "t")))
    assert wb.sheetnames == ["Matrix", "Summary"]
    ws = wb["Matrix"]
    assert [c.value for c in ws[1]] == ["Story key", "Story title", "Test case key", "Test case name", "Status",
                                        "Automated scenario", "Gap / link type"]
    assert ws.freeze_panes == "A2" and ws.auto_filter.ref == f"A1:G{len(m.rows) + 1}"
    by_gap = {ws.cell(row=i, column=7).value: i for i in range(2, ws.max_row + 1)}
    assert ws.cell(row=by_gap["Suggested"], column=1).fill.fgColor.rgb.endswith("FFF2CC")
    assert ws.cell(row=by_gap["Gap: no story"], column=1).font.color.rgb.endswith("C00000")
    assert ws.cell(row=by_gap["Gap: not automated"], column=3).font.color.rgb.endswith("C00000")
    linked = ws.cell(row=by_gap["Linked"], column=1)
    assert linked.font.color is None or not str(linked.font.color.rgb).endswith("C00000")
    summary = {r[0]: r[1] for r in wb["Summary"].iter_rows(min_row=2, values_only=True)}
    assert wb["Summary"]["A1"].value == "t"
    assert summary["Suggestions"] == m.summary["suggestions"] and summary["Stories in scope"] == 1


def test_filename_sanitised():
    assert xlsx_filename("folder", "R76 SIT/Login", date(2026, 10, 13)) == \
        "Traceability_folder_R76_SIT_Login_2026-10-13.xlsx"


class FakeStore:
    def __init__(self, payloads):
        self.payloads, self.prefixes = payloads, None

    def scroll_sources(self, prefixes):
        self.prefixes = prefixes
        yield from (p for p in self.payloads if p["source"].startswith(tuple(prefixes)))


def test_load_trace_data():
    tc_meta = {"name": "Close", "status": "Approved", "priority": "High", "folder": "R76 SIT", "project": "AUSWEB",
               "issue_ids": [10001, 10002, 404], "linked_keys": ["ORION-1"], "url": "u"}
    payloads = [
        {"source": "zephyr_AUSWEB", "kind": "testcase", "citation": "AUSWEB-T1224", "parent_id": "AUSWEB-T1224",
         "header": "Zephyr › AUSWEB-T1224", "meta": tc_meta},
        {"source": "zephyr_AUSWEB", "kind": "testcase", "citation": "AUSWEB-T1224", "parent_id": "AUSWEB-T1224",
         "header": "Zephyr › AUSWEB-T1224", "meta": {**tc_meta, "name": "second chunk"}},
        {"source": "jira_linked", "kind": "jira", "citation": "ORION-5510", "parent_id": "ORION-5510",
         "header": "", "meta": {"summary": "Close account", "status": "Done", "type": "Story", "url": ""}},
        {"source": "confluence", "kind": "confluence", "citation": "Closure page", "parent_id": "1",
         "header": "", "meta": {"page_id": "1", "title": "Closure page", "url": "", "jira_keys": ["ORION-5510",
                                                                                                  "ORION-6413"]}},
        {"source": "confluence", "kind": "comments", "citation": "Later page", "parent_id": "2",
         "header": "", "meta": {"page_id": "2", "title": "Later page", "url": "", "jira_keys": ["ORION-6413"]}},
        {"source": "confluence", "kind": "attachment", "citation": "Doc", "parent_id": "3",
         "header": "", "meta": {"page_id": "3", "title": "Doc", "url": "", "file": "a.docx"}},
        {"source": "gh_ctx", "kind": "scenario", "citation": "Close it", "parent_id": "f.feature::Close it",
         "header": "CTX.Web.Automation › f.feature › Scenario: Close it",
         "meta": {"tags": ["@AUSWEB-T1224"], "feature": "F", "url": "s"}},
        {"source": "gh_ctx", "kind": "scenario", "citation": "Close it", "parent_id": "f.feature::Close it",
         "header": "CTX.Web.Automation › f.feature › Scenario: Close it",
         "meta": {"tags": ["@AUSWEB-T1224"], "feature": "F", "url": "s"}},
        {"source": "gh_ctx", "kind": "method", "citation": "doIt", "parent_id": "x", "header": "", "meta": {}},
    ]
    store = FakeStore(payloads)
    d = load_trace_data(store, {"10001": "ORION-5510", "10002": "ORION-1"})
    assert sorted(store.prefixes) == ["confluence", "gh_", "jira_linked", "zephyr_"]
    assert list(d.testcases) == ["AUSWEB-T1224"]
    assert d.testcases["AUSWEB-T1224"]["name"] == "Close"
    assert d.testcases["AUSWEB-T1224"]["linked_keys"] == ["ORION-1", "ORION-5510"]
    assert d.stories["ORION-5510"] == {"title": "Close account", "summary": "Close account",
                                       "page_title": "Closure page"}
    assert d.stories["ORION-6413"] == {"title": "Closure page", "summary": "", "page_title": "Closure page"}
    assert d.scenarios == [{"name": "Close it", "tags": ["@AUSWEB-T1224"], "repo": "CTX.Web.Automation",
                            "url": "s"}]


def _hit(citation, score):
    return Hit(n=0, chunk_id="c", citation=citation, source="zephyr_AUSWEB", kind="testcase", path="",
               location={}, header="", text="", snippet="", score=score)


def test_make_suggester_filters_by_threshold():
    calls = []

    class Searcher:
        settings = SimpleNamespace(retrieval=SimpleNamespace(min_rerank_score=0.5))

        def search(self, question, history=None, sources=None):
            calls.append((question, sources))
            return SearchResult([_hit("AUSWEB-T1", 0.9), _hit("AUSWEB-T1", 0.8), _hit("AUSWEB-T2", 0.5),
                                 _hit("AUSWEB-T3", 0.49)], False)

    suggest = make_suggester(Searcher(), ["zephyr_AUSWEB"])
    assert suggest("ORION-5510", "Close account") == ["AUSWEB-T1", "AUSWEB-T2"]
    assert calls == [("Close account", ["zephyr_AUSWEB"])]


def test_load_trace_data_keeps_same_named_scenarios_on_different_lines():
    def scen(line, tag):
        return {"source": "gh_ctx", "kind": "scenario", "citation": "Close it", "parent_id": "f.feature::Close it",
                "header": "CTX › f.feature › Scenario: Close it", "location": {"line": line},
                "meta": {"tags": [tag], "feature": "F", "url": ""}}

    tc = {"name": "n", "status": "", "priority": "", "folder": "R1", "project": "AUSWEB", "issue_ids": [],
          "linked_keys": ["ORION-1"], "url": ""}
    payloads = [scen(3, "@AUSWEB-T1224"), scen(3, "@AUSWEB-T1224"), scen(9, "@AUSWEB-T1225")]
    for key in ("AUSWEB-T1224", "AUSWEB-T1225"):
        payloads.append({"source": "zephyr_AUSWEB", "kind": "testcase", "citation": key, "parent_id": key,
                         "header": "", "meta": tc})
    d = load_trace_data(FakeStore(payloads), {})
    assert len(d.scenarios) == 2
    rows = build_matrix(d, "project", "AUSWEB", None, None).rows
    assert [(r.testcase_key, r.gap) for r in rows] == [("AUSWEB-T1224", "Linked"), ("AUSWEB-T1225", "Linked")]
