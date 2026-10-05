from qabuddy.config import load_settings
from qabuddy.ingest.chunking import Section, estimate_tokens, merge_small, split_by_size, split_markdown


def test_estimate_tokens():
    assert estimate_tokens("abcd" * 10) == 10
    assert estimate_tokens("abc") == 1
    assert estimate_tokens("") == 0


def test_split_markdown_breadcrumbs():
    text = "# A\n## B\ntext\n### C\nmore"
    sections = split_markdown(text, title="Doc")
    assert [s.breadcrumb for s in sections] == [["A"], ["A", "B"], ["A", "B", "C"]]
    assert [(s.line_start, s.line_end) for s in sections] == [(1, 1), (2, 3), (4, 5)]
    assert "text" in sections[1].text


def test_split_markdown_preamble_uses_title():
    sections = split_markdown("intro line\n# A\nbody", title="Doc")
    assert sections[0].breadcrumb == ["Doc"]
    assert sections[0].text == "intro line"


def test_merge_small_heading_only_section_merges_forward():
    sections = split_markdown("# A\n## B\n" + "word " * 100, title="Doc")
    merged = merge_small(sections, min_tokens=40, target=400)
    assert len(merged) == 1
    assert merged[0].breadcrumb == ["A", "B"]
    assert merged[0].line_start == 1
    assert merged[0].text.startswith("# A")


def test_split_by_size_overlap():
    text = "\n".join(f"Sentence number {i} explains explicit waits and locators." for i in range(80))
    pieces = split_by_size(text, target=400, max_tokens=800, overlap=60)
    assert len(pieces) > 1
    for piece, start, end in pieces:
        assert estimate_tokens(piece) <= 800
        assert 1 <= start <= end
    for (a, _, _), (b, _, _) in zip(pieces, pieces[1:]):
        shared = max(k for k in range(len(b) + 1) if a.endswith(b[:k]))
        assert 30 <= estimate_tokens(b[:shared]) <= 65


def test_split_by_size_hard_cuts_long_lines():
    text = "x" * 20000
    pieces = split_by_size(text, target=400, max_tokens=800, overlap=60)
    assert len(pieces) > 1
    assert all(estimate_tokens(p) <= 800 for p, _, _ in pieces)
    assert all(start == 1 and end == 1 for _, start, end in pieces)


def test_split_by_size_small_text_single_piece():
    pieces = split_by_size("line one\nline two", target=400, max_tokens=800, overlap=60)
    assert pieces == [("line one\nline two", 1, 2)]


def test_settings_env_override(monkeypatch):
    monkeypatch.setenv("QABUDDY_RETRIEVAL__TOP_K", "4")
    settings = load_settings()
    assert settings.retrieval.top_k == 4
    assert settings.data_path.name == "data"
    assert isinstance(Section(["a"], "t", 1, 1), Section)
