from qabuddy.answer.prompt import SYSTEM_PROMPT, build_messages, check_citations
from qabuddy.ingest.chunking import estimate_tokens
from qabuddy.retrieval.search import Hit


def _hit(n, tokens=600):
    return Hit(n=n, chunk_id=str(n), citation=f"Doc{n}.md", source="company_docs", kind="doc_section", path="p",
               location={}, header=f"Docs › Doc{n}", text="x" * (tokens * 4), snippet="", score=1.0)


def test_build_messages_budget():
    hits = [_hit(i) for i in range(1, 11)]
    messages, used = build_messages("q?", hits, [], max_source_tokens=2800, history_turns=2)
    assert [h.n for h in used] == list(range(1, len(used) + 1))
    assert 1 <= len(used) < 10
    source_block = messages[-1]["content"].split("Question:")[0]
    assert estimate_tokens(source_block) <= 2800
    assert "[1] Doc1.md" in source_block and f"[{len(used) + 1}]" not in source_block


def test_build_messages_structure_and_history():
    history = [{"role": "user", "content": "first?"}, {"role": "assistant", "content": "a" * 5000},
               {"role": "user", "content": "second?"}, {"role": "assistant", "content": "ok"},
               {"role": "user", "content": "third?"}, {"role": "assistant", "content": "fine"}]
    messages, _ = build_messages("q?", [_hit(1, 50)], history, max_source_tokens=2800, history_turns=2)
    assert messages[0] == {"role": "system", "content": SYSTEM_PROMPT}
    assert [m["content"] for m in messages[1:-1] if m["role"] == "user"] == ["second?", "third?"]
    assert all(estimate_tokens(m["content"]) <= 310 for m in messages[1:-1])
    assert messages[-1]["role"] == "user" and messages[-1]["content"].rstrip().endswith("Question: q?")


def test_check_citations():
    assert check_citations("a [1] b [3] c [9]", k=6) == ([1, 3], False)
    assert check_citations("no cites", k=6) == ([], True)
    assert check_citations("both [1, 2] and [2][4]", k=6) == ([1, 2, 4], False)


def test_system_prompt_rules():
    for phrase in ("[n]", "only", "not", "proposed"):
        assert phrase in SYSTEM_PROMPT
