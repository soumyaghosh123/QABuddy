import json

from qabuddy.config import Settings
from qabuddy.ingest.context import ParseContext
from qabuddy.ingest.models import SourceFile
from qabuddy.ingest.normalize import clean_html
from qabuddy.ingest.parsers import get_parser
from qabuddy.ingest.parsers.zephyr import parse_zephyr

CTX = ParseContext(settings=Settings(), source_label="Zephyr · AUSWEB", known_entities=set(), glossary={})
PATH = "zephyr/AUSWEB/AUSWEB-T1224.json"


def record(**over):
    base = {"listing_hash": "h", "folder_path": "R76 SIT / Login", "status_name": "Draft", "priority_name": "High",
            "issue_ids": [10001], "web_links": [],
            "steps": [{"inline": {"description": "Open page", "testData": "user=a", "expectedResult": "Loads"}},
                      {"inline": {"description": "Click", "testData": "", "expectedResult": ""}},
                      {"testCase": {"self": "https://x/AUSWEB-T9", "testCaseKey": "AUSWEB-T9", "version": 1}}],
            "testcase": {"key": "AUSWEB-T1224", "name": "Account Closure - Close an account with Open Position",
                         "objective": "<p>Close it</p>", "precondition": "<p>Logged in</p>", "labels": ["smoke", "web"]}}
    base.update(over)
    return base


def sf(tmp, data):
    path = tmp / "x.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return SourceFile(abs_path=path, rel_path=PATH, source="zephyr_ausweb", parser="zephyr", file_hash="h")


def test_parser_layout_matches_csv_parser(tmp_path):
    [chunk] = parse_zephyr(sf(tmp_path, record()), CTX)
    assert chunk.text.startswith("Test case AUSWEB-T1224 — Account Closure - Close an account with Open Position")
    assert "Folder: R76 SIT / Login" in chunk.text and chunk.meta["status"] == "Draft"
    assert "Module: R76 SIT | Priority: High | Status: Draft" in chunk.text
    assert "Precondition: Logged in" in chunk.text and "Labels: smoke, web" in chunk.text
    assert ("Steps:\n1. Open page — Data: user=a — Expected: Loads\n2. Click\n3. Call test case AUSWEB-T9"
            in chunk.text)
    assert chunk.kind == "testcase" and chunk.citation == chunk.parent_id == "AUSWEB-T1224"
    assert chunk.module == "R76 SIT"
    assert chunk.meta["project"] == "AUSWEB" and chunk.meta["issue_ids"] == [10001]
    assert chunk.meta["linked_keys"] == [] and chunk.meta["folder"] == "R76 SIT / Login"
    assert chunk.meta["priority"] == "High" and chunk.meta["name"].startswith("Account Closure")
    assert chunk.meta["url"].startswith("https://ausiex.atlassian.net/projects/AUSWEB?")
    assert chunk.meta["url"].endswith("#!/v2/testCase/AUSWEB-T1224")
    assert "ausweb-t1224" in chunk.ids_norm


def test_html_fields_and_empty_steps(tmp_path):
    rec = record(steps=[])
    rec["testcase"]["objective"] = "<p>Line&nbsp;1<br>Line 2</p>"
    [chunk] = parse_zephyr(sf(tmp_path, rec), CTX)
    assert "Line 1\nLine 2" in chunk.text and "Steps:" not in chunk.text


def test_script_used_when_no_steps(tmp_path):
    [chunk] = parse_zephyr(sf(tmp_path, record(steps=[], script={"type": "bdd", "text": "Given x"})), CTX)
    assert "Steps:\nGiven x" in chunk.text


def test_no_folder(tmp_path):
    [chunk] = parse_zephyr(sf(tmp_path, record(folder_path="")), CTX)
    assert "Folder:" not in chunk.text and chunk.module == ""


def test_registered():
    assert get_parser("zephyr") is parse_zephyr


def test_clean_html():
    assert clean_html("<p>a&nbsp;b</p><ul><li>x</li><li>y &amp; z</li></ul><div>q</div>\n\n\n\n\nz") \
        == "a b\nx\ny & z\nq\n\nz"


def test_plain_text_angle_brackets_preserved(tmp_path):
    rec = record(steps=[], script={"type": "bdd", "text": "Given I enter <username>"})
    rec["testcase"]["objective"] = "x < 5 and y > 3"
    [chunk] = parse_zephyr(sf(tmp_path, rec), CTX)
    assert "Given I enter <username>" in chunk.text and "x < 5 and y > 3" in chunk.text


def test_html_objective_still_cleaned(tmp_path):
    rec = record()
    rec["testcase"]["objective"] = "<div>A<br/>B</div>"
    [chunk] = parse_zephyr(sf(tmp_path, rec), CTX)
    assert "Objective: A\nB" in chunk.text and "<div>" not in chunk.text


def test_call_step_reads_test_case_key(tmp_path):
    step = {"testCase": {"self": "https://x/testcases/AUSWEB-T12/versions/1", "testCaseKey": "AUSWEB-T12", "version": 1}}
    [chunk] = parse_zephyr(sf(tmp_path, record(steps=[step])), CTX)
    assert "1. Call test case AUSWEB-T12" in chunk.text
