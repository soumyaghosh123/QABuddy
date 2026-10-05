import pytest

from qabuddy.ingest.context import ParseContext
from qabuddy.ingest.discover import discover
from qabuddy.ingest.parsers.jira import parse_jira_markdown
from qabuddy.ingest.parsers.testcases import parse_testcases, entities_from_testcases


@pytest.fixture(scope="module")
def ctx(settings):
    return ParseContext(settings=settings, source_label="", known_entities=set(), glossary={})


@pytest.fixture(scope="module")
def files(settings, data_dir):
    return {f.rel_path.rsplit("/", 1)[-1]: f for f in discover(settings)}


@pytest.fixture(scope="module")
def testcases(files, ctx):
    return parse_testcases(files["VWO_500_Test_Cases.csv"], ctx)


def test_testcases_count(testcases):
    assert len(testcases) == 500


def test_testcase_login_001(testcases):
    tc = next(c for c in testcases if c.citation == "LOGIN-001")
    assert tc.kind == "testcase" and tc.source == "testcases"
    assert tc.module == "Login"
    assert tc.parent_id == "LOGIN-001"
    assert tc.location == {"row": 1}
    assert "Steps:\n1. Open the VWO Login area" in tc.text
    assert tc.text.count("Expected:") == 1
    assert "Priority: Medium" in tc.text and "Automated: Yes" in tc.text
    assert "login-001" in tc.ids_norm


def test_testcase_entities(testcases):
    entities = entities_from_testcases(testcases)
    assert len(entities) == 500
    assert "login-001" in entities


def test_jira_vwo_26(files, ctx):
    [chunk] = parse_jira_markdown(files["Bug_VWO_26.md"], ctx)
    assert chunk.citation == "VWO-26" and chunk.kind == "ticket" and chunk.parent_id == "VWO-26"
    assert "Login failure on VWO app" in chunk.text
    assert "Status: To Do" in chunk.text
    assert "Back to previous view" not in chunk.text
    assert "Generated at" not in chunk.text
    assert "Rank" not in chunk.text
    assert "vwo-26" in chunk.ids_norm


def test_jira_key_from_header_not_filename(files, ctx):
    [chunk] = parse_jira_markdown(files["Bug_VWO_32.md"], ctx)
    assert chunk.citation == "VWO-33"


def test_jira_qab_102_comments(files, ctx):
    chunks = parse_jira_markdown(files["QAB-102_flaky_login_retry.md"], ctx)
    text = "\n".join(c.text for c in chunks)
    assert "Comment by Pramod" in text
    assert "Labels: flaky, selenium, wait" in text
    assert all(c.citation == "QAB-102" for c in chunks)
