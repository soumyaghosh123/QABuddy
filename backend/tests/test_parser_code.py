import pytest

from qabuddy.ingest.chunking import estimate_tokens
from qabuddy.ingest.context import ParseContext
from qabuddy.ingest.discover import discover
from qabuddy.ingest.parsers.code import code_entities, parse_code

DASHBOARD = "pages/pageObjectModel/vwo/improved_POM/DashBoardPage.java"


@pytest.fixture(scope="module")
def code_files(settings, data_dir):
    return [f for f in discover(settings) if f.source in ("selenium", "playwright")]


@pytest.fixture(scope="module")
def all_chunks(code_files, settings):
    chunks = []
    for f in code_files:
        label = "Selenium framework" if f.source == "selenium" else "Playwright framework"
        chunks += parse_code(f, ParseContext(settings=settings, source_label=label, known_entities=set(), glossary={}))
    return chunks


def _from(chunks, suffix):
    return [c for c in chunks if c.path.endswith(suffix)]


def test_java_method_chunk(all_chunks):
    chunks = [c for c in _from(all_chunks, DASHBOARD) if c.kind == "method"]
    target = [c for c in chunks if "loggedInUserName" in c.header]
    assert target, [c.header for c in chunks]
    chunk = target[0]
    assert chunk.location["line_start"] <= 20 <= chunk.location["line_end"]
    assert chunk.citation == f"DashBoardPage.java:{chunk.location['line_start']}-{chunk.location['line_end']}"
    assert "WaitHelpers.visibilityOfElement(userNameOnDashboard);" in chunk.text
    assert chunk.header.startswith("Selenium framework › ")
    assert "class DashBoardPage" in chunk.header
    assert chunk.parent_id == chunk.path


def test_java_class_context(all_chunks):
    ctx_chunks = [c for c in _from(all_chunks, DASHBOARD) if c.kind == "class_context"]
    assert len(ctx_chunks) == 1
    text = ctx_chunks[0].text
    assert 'private By userNameOnDashboard = By.xpath("//h6");' in text
    assert "package com.thetestingacademy.pages.pageObjectModel.vwo.improved_POM;" in text


def test_playwright_test_blocks(all_chunks):
    chunks = _from(all_chunks, "src/tests/checkout.spec.ts")
    target = [c for c in chunks if "should complete checkout successfully" in c.header]
    assert target, [c.header for c in chunks]
    assert "Checkout Flow" in target[0].header
    assert target[0].location["line_start"] == 51
    assert target[0].kind == "test"


def test_ts_class_methods(all_chunks):
    chunks = _from(all_chunks, "src/modules/LoginModule.ts")
    assert any("class LoginModule" in c.header and "doLogin" in c.header for c in chunks)


def test_code_max_tokens(all_chunks):
    too_big = [(c.citation, estimate_tokens(c.text)) for c in all_chunks if estimate_tokens(c.text) > 800]
    assert not too_big


def test_properties_masked(all_chunks):
    [props] = _from(all_chunks, "src/main/resources/data.properties")
    assert props.kind == "config"
    assert "password=***" in props.text.replace(" ", "")


def test_markdown_in_repo(all_chunks):
    assert any(c.kind == "md_section" and c.path.endswith("README.md") for c in all_chunks)


def test_code_entities(all_chunks):
    entities = code_entities(all_chunks)
    assert {"dashboardpage", "loggedinusername", "loginmodule", "dologin"} <= entities
