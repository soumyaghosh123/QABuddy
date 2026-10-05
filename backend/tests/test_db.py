from qabuddy.db import Database
from qabuddy.ingest.parsers.jenkins import TestRun


def test_db_roundtrip(tmp_path):
    db = Database(tmp_path / "q.db")
    db.init()
    assert db.bump_index_version() == 1
    assert db.bump_index_version() == 2
    assert db.get_meta("index_version") == "2"

    db.upsert_file("a/b.md", "company_docs", "h1", 10, 1.0, ["c1", "c2"], "ok", None)
    assert db.get_file("a/b.md")["chunk_ids"] == ["c1", "c2"]
    assert [f["path"] for f in db.all_files()] == ["a/b.md"]
    db.delete_file("a/b.md")
    assert db.get_file("a/b.md") is None

    db.cache_put("k", 2, "q?", "", "answer", [{"n": 1}])
    assert db.cache_get("k", 2)["answer"] == "answer"
    assert db.cache_get("k", 3) is None

    db.replace_entities([("vwo-26", "VWO-26", "jira_key", "01_JIRA_Tickets/Bug_VWO_26.md")])
    assert db.known_entities() == {"vwo-26"}
    assert db.entities_in("see VWO-26 please") == ["vwo-26"]


def test_db_test_runs(tmp_path):
    db = Database(tmp_path / "q.db")
    db.init()
    run = TestRun(job="j", build=1, test_id="C.m", class_name="C", method="m", title="m", file=None,
                  status="passed", attempt=1, duration_s=1.5, error_type=None, error_message=None, source_path="x.log")
    db.replace_test_runs([run])
    rows = db.all_test_runs()
    assert len(rows) == 1 and rows[0].status == "passed" and rows[0].duration_s == 1.5
