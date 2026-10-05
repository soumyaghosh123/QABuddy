from qabuddy.config import REPO_ROOT
from qabuddy.evaluate import EvalReport, calibrate_threshold, first_match_rank, load_questions, summarize
from qabuddy.retrieval.search import Hit


def _hit(citation, path="x"):
    return Hit(n=1, chunk_id="1", citation=citation, source="s", kind="k", path=path, location={}, header="",
               text="", snippet="", score=1.0)


def test_hit_and_mrr_math():
    hit_at_k, mrr = summarize([1, None, 2], k=6)
    assert hit_at_k == 2 / 3
    assert mrr == 0.5


def test_first_match_rank_uses_citation_or_path_case_insensitive():
    hits = [_hit("Foo"), _hit("VWO-26"), _hit("Bar", path="02_Company_Docs/Automation_Coding_Standards.md")]
    assert first_match_rank(hits, ["vwo-26"]) == 2
    assert first_match_rank(hits, ["automation_coding_standards"]) == 3
    assert first_match_rank(hits, ["nothing"]) is None


def test_calibrate_threshold():
    report = EvalReport(hit_at_k=1.0, mrr=1.0, no_answer_acc=0.0, per_question=[],
                        answerable_top_scores=[3.1, 5.0], unanswerable_top_scores=[-2.0, 0.5])
    threshold = calibrate_threshold(report)
    assert 0.5 < threshold <= 3.1


def test_calibrate_threshold_overlap_keeps_answerable():
    report = EvalReport(hit_at_k=1.0, mrr=1.0, no_answer_acc=0.0, per_question=[],
                        answerable_top_scores=[1.0, 5.0], unanswerable_top_scores=[2.0])
    assert calibrate_threshold(report) <= 1.0


def test_questions_file_is_valid():
    questions = load_questions(REPO_ROOT / "eval" / "questions.yaml")
    answerable = [q for q in questions if q.answerable]
    assert len(answerable) >= 18
    assert len([q for q in questions if not q.answerable]) >= 3
    assert all(q.expect for q in answerable)
    assert sum(q.example for q in questions) == 6
