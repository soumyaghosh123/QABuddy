import pytest

from qabuddy.ingest.chunking import estimate_tokens
from qabuddy.ingest.context import ParseContext
from qabuddy.ingest.discover import discover
from qabuddy.ingest.parsers.documents import parse_document
from qabuddy.ingest.parsers.lucid import parse_lucid
from qabuddy.ingest.parsers.meetings import parse_meeting


@pytest.fixture(scope="module")
def ctx(settings):
    return ParseContext(settings=settings, source_label="", known_entities=set(), glossary={})


@pytest.fixture(scope="module")
def files(settings, data_dir):
    return {f.rel_path.rsplit("/", 1)[-1]: f for f in discover(settings)}


def test_markdown_doc_waits_section(files, ctx):
    chunks = parse_document(files["Automation_Coding_Standards.md"], ctx)
    waits = [c for c in chunks if "1.3 Waits" in " › ".join(c.breadcrumb)]
    assert waits and "waitJVM" in waits[0].text
    assert all(c.kind == "doc_section" and c.source == "company_docs" for c in chunks)
    assert all(estimate_tokens(c.text) <= 800 for c in chunks)
    assert waits[0].citation.startswith("Automation Coding Standards › ")


def test_pdf_prd_has_pages(files, ctx):
    chunks = parse_document(files["Product Requirements Document (PRD) VWO.com (1).pdf"], ctx)
    assert chunks
    assert all(isinstance(c.location.get("page"), int) and c.location["page"] >= 1 for c in chunks)
    assert all(" p." in c.citation for c in chunks)
    assert all(c.source == "requirements" for c in chunks)


def test_vtt_turns(files, ctx):
    chunks = parse_meeting(files["2026-04-14_Daily_Standup.vtt"], ctx)
    assert chunks
    first = chunks[0]
    assert "Ananya Rao" in first.text
    assert first.citation == "Daily Standup 2026-04-14 @00:00:01"
    assert first.kind == "transcript" and first.date == "2026-04-14"
    assert "-->" not in first.text and "WEBVTT" not in first.text


def test_txt_meeting_date(files, ctx):
    chunks = parse_meeting(files["2026-04-07_Login_Failure_Triage_Meeting.txt"], ctx)
    assert chunks and all(c.date == "2026-04-07" for c in chunks)
    assert any("Cloud NAT" in c.text for c in chunks)
    assert "vwo-26" in {i for c in chunks for i in c.ids_norm}


def test_meeting_chunks_overlap_one_turn(files, ctx):
    chunks = parse_meeting(files["2026-04-07_Login_Failure_Triage_Meeting.txt"], ctx)
    if len(chunks) > 1:
        last_line = chunks[0].text.rstrip().splitlines()[-1]
        assert last_line in chunks[1].text


def test_lucid_json_flow(files, ctx):
    [chunk] = parse_lucid(files["AB_Test_Experiment_Lifecycle.json"], ctx)
    assert chunk.kind == "diagram"
    assert "→" in chunk.text and "(approved)" in chunk.text
    assert chunk.title == "A/B Test Experiment Lifecycle"


def test_lucid_csv_flow(files, ctx):
    chunks = parse_lucid(files["Login_Authentication_Flow.csv"], ctx)
    text = "\n".join(c.text for c in chunks)
    assert "Click Sign in" in text and "→" in text
    assert chunks[0].title == "Login Authentication Flow"


def test_lucid_txt(files, ctx):
    chunks = parse_lucid(files["CI_Test_Pipeline.txt"], ctx)
    assert any("Publish Allure" in c.text for c in chunks)
