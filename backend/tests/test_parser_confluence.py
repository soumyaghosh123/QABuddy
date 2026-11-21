import json

import pytest

from qabuddy.config import Settings
from qabuddy.ingest.chunking import estimate_tokens
from qabuddy.ingest.context import ParseContext
from qabuddy.ingest.models import SourceFile
from qabuddy.ingest.parsers import get_parser
from qabuddy.ingest.parsers.confluence import parse_confluence_attachment, parse_confluence_page, storage_to_markdown

CTX = ParseContext(settings=Settings(), source_label="Confluence", known_entities=set(), glossary={})
TITLE = ("[ORION - 5510] [MTP] [B2B Origination] Ability to retrieve Client/Adviser file (INGD), "
         "perform validations on the Client/Adviser file.")
URL = "https://x.atlassian.net/wiki/spaces/AUSIEX/pages/555"


def macro(name, body="", **params):
    ps = "".join(f'<ac:parameter ac:name="{k}">{v}</ac:parameter>' for k, v in params.items())
    inner = f"<ac:rich-text-body>{body}</ac:rich-text-body>" if body else ""
    return f'<ac:structured-macro ac:name="{name}" ac:schema-version="1">{ps}{inner}</ac:structured-macro>'


STORY = (
    macro("panel", "<p>As a CoreTX User</p><p>I want to retrieve the Client/Adviser file from INGD and validate it</p>"
          "<p>So that only valid Client/Adviser records are loaded into the origination platform</p>",
          title="User Story Statement")
    + macro("panel", "<p>INGD sends a daily Client/Adviser file to the B2B gateway.</p>"
            "<p>The file must be validated before onboarding continues.</p>"
            "<p>Invalid rows are reported back to the sender on the same day without blocking valid rows.</p>",
            title="Background")
    + "<table><tbody>"
    + f"<tr><th>Link to JIRA</th><td>{macro('jira', key='ORION-5510')} {macro('jira', key='ORION-6413')}</td></tr>"
    + f"<tr><th>Requirements Finalised?</th><td>{macro('status', title='READY FOR REVIEW', colour='Yellow')}</td></tr>"
    + "<tr><th>Functional Requirements?</th><td><p>See the validation rules in the attached spec.</p></td></tr>"
    + "</tbody></table>")


def page_sf(tmp, **over):
    data = {"id": "555", "title": TITLE, "version": 3, "parent_id": "1", "ancestors_titles": ["Home"],
            "web_url": URL, "body": STORY, "comments": [], "jira_keys": ["ORION-5510", "ORION-6413"],
            "attachments": []}
    data.update(over)
    path = tmp / "confluence" / "AUSIEX" / "pages" / f"{data['id']}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    return SourceFile(abs_path=path, rel_path=f"confluence/AUSIEX/pages/{data['id']}.json", source="confluence",
                      parser="confluence", file_hash="h")


def test_user_story_sections(tmp_path):
    chunks = parse_confluence_page(page_sf(tmp_path), CTX)
    by_last = {c.breadcrumb[-1]: c for c in chunks if c.kind == "confluence"}
    assert "User Story Statement" in by_last and "Background" in by_last
    story = by_last["User Story Statement"]
    assert story.header == f"Confluence › AUSIEX › {TITLE} › User Story Statement"
    assert "As a CoreTX User" in story.text and "So that only valid" in story.text
    assert story.citation == TITLE and story.parent_id == "555"
    assert story.meta == {"page_id": "555", "title": TITLE, "url": URL, "jira_keys": ["ORION-5510", "ORION-6413"]}
    assert all("orion-5510" in c.ids_norm and "orion-6413" in c.ids_norm for c in chunks)


def test_table_rows_flattened():
    md = storage_to_markdown(STORY)
    assert "Link to JIRA: ORION-5510 ORION-6413" in md
    assert "Requirements Finalised?: READY FOR REVIEW" in md
    assert "Functional Requirements?: See the validation rules in the attached spec." in md
    table = "<table><tr><th>ID</th><th>Name</th></tr><tr><td>1</td><td>A</td></tr><tr><td>2</td><td>B</td></tr></table>"
    assert storage_to_markdown(table).splitlines() == ["ID: 1 | Name: A", "ID: 2 | Name: B"]


def test_headings_lists_and_panels():
    xhtml = ("<h2>Rules</h2><ul><li>one</li><li>two <strong>bold</strong></li></ul>"
             + macro("info", "<p>careful</p>", title="Heads up")
             + '<ac:structured-macro ac:name="code"><ac:plain-text-body><![CDATA[a < b]]></ac:plain-text-body>'
             "</ac:structured-macro>")
    md = storage_to_markdown(xhtml)
    assert "## Rules" in md and "- one" in md and "- two bold" in md
    assert "## Heads up\n\ncareful" in md and "```\na < b\n```" in md


def test_unknown_macro_text_kept_markup_dropped():
    xhtml = ('<p>Intro</p><ac:structured-macro ac:name="mystery"><ac:parameter ac:name="x">PARAM</ac:parameter>'
             "<ac:rich-text-body><p>kept <em>text</em></p></ac:rich-text-body></ac:structured-macro>"
             '<ac:image><ri:attachment ri:filename="a.png"/></ac:image><p>unbalanced <b>tail')
    md = storage_to_markdown(xhtml)
    assert "kept text" in md and "PARAM" not in md and "ac:" not in md and "ri:" not in md and "<" not in md
    assert "unbalanced tail" in md


def test_page_without_headings_is_one_section_titled_with_page(tmp_path):
    chunks = parse_confluence_page(page_sf(tmp_path, body="<p>" + "Plain text sentence. " * 20 + "</p>"), CTX)
    assert len(chunks) == 1 and chunks[0].breadcrumb == [TITLE]
    assert chunks[0].header == f"Confluence › AUSIEX › {TITLE}"


def test_huge_table_split(tmp_path):
    rows = "".join(f"<tr><td>R{i}</td><td>Validation rule number {i} must hold for every row</td></tr>"
                   for i in range(600))
    body = "<h2>Rules</h2><table><tr><th>Id</th><th>Rule</th></tr>" + rows + "</table>"
    chunks = parse_confluence_page(page_sf(tmp_path, body=body), CTX)
    assert len(chunks) > 3
    assert all(estimate_tokens(c.text) <= 800 for c in chunks)
    assert len({c.chunk_id for c in chunks}) == len(chunks)
    assert "Id: R599 | Rule: Validation rule number 599" in "\n".join(c.text for c in chunks)


def test_comments_chunk(tmp_path):
    comments = [{"author": "Ann", "created": "2026-01-02", "body": "<p>Please confirm <b>scope</b></p>"},
                {"author": "Bob", "created": "2026-01-03", "body": "<p>Confirmed</p>"}]
    chunks = parse_confluence_page(page_sf(tmp_path, comments=comments), CTX)
    [chunk] = [c for c in chunks if c.kind == "comments"]
    assert chunk.text == ("Comment by Ann on 2026-01-02: Please confirm scope\n"
                          "Comment by Bob on 2026-01-03: Confirmed")
    assert chunk.header == f"Confluence › AUSIEX › {TITLE} › Comments"
    assert chunk.parent_id == "555" and "orion-5510" in chunk.ids_norm


def test_registry_names():
    assert get_parser("confluence") is parse_confluence_page
    assert get_parser("confluence_attachment") is parse_confluence_attachment


@pytest.mark.parametrize("with_page", [True, False])
def test_docx_attachment_parsed(tmp_path, with_page):
    docx = pytest.importorskip("docx")
    pytest.importorskip("docling")
    if with_page:
        page_sf(tmp_path)
    path = tmp_path / "confluence" / "AUSIEX" / "attachments" / "555" / "spec.docx"
    path.parent.mkdir(parents=True, exist_ok=True)
    document = docx.Document()
    document.add_heading("Validation rules", 1)
    document.add_paragraph("The Adviser file must contain a valid AFSL number on every row. " * 3)
    document.save(path)
    sf = SourceFile(abs_path=path, rel_path="confluence/AUSIEX/attachments/555/spec.docx", source="confluence",
                    parser="confluence_attachment", file_hash="h2")
    chunks = parse_confluence_attachment(sf, CTX)
    assert chunks and "AFSL number" in " ".join(c.text for c in chunks)
    title, url = (TITLE, URL) if with_page else ("spec.docx", "")
    for c in chunks:
        assert c.header == f"Confluence › AUSIEX › {title} › spec.docx"
        assert c.citation == title and c.parent_id == "555"
        assert c.meta == {"page_id": "555", "title": title, "url": url, "file": "spec.docx"}
