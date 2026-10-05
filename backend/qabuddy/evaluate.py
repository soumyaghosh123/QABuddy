"""Retrieval evaluation: hit@k, MRR, no-answer accuracy, and rerank-threshold calibration (spec §7, §13)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from qabuddy.retrieval.search import Hit


@dataclass
class EvalQuestion:
    q: str
    expect: list[str]
    answerable: bool = True
    example: bool = False


@dataclass
class EvalReport:
    hit_at_k: float
    mrr: float
    no_answer_acc: float
    per_question: list[dict] = field(default_factory=list)
    answerable_top_scores: list[float] = field(default_factory=list)
    unanswerable_top_scores: list[float] = field(default_factory=list)


def load_questions(path: Path) -> list[EvalQuestion]:
    items = yaml.safe_load(path.read_text(encoding="utf-8")) or []
    return [EvalQuestion(q=i["q"], expect=list(i.get("expect", [])), answerable=i.get("answerable", True),
                         example=bool(i.get("example", False))) for i in items]


def first_match_rank(hits: list[Hit], expect: list[str]) -> int | None:
    needles = [e.lower() for e in expect]
    for rank, hit in enumerate(hits, 1):
        haystack = f"{hit.citation}\n{hit.path}".lower()
        if any(n in haystack for n in needles):
            return rank
    return None


def summarize(ranks: list[int | None], k: int) -> tuple[float, float]:
    if not ranks:
        return 0.0, 0.0
    hits = sum(1 for r in ranks if r is not None and r <= k)
    mrr = sum(1 / r for r in ranks if r is not None and r <= k) / len(ranks)
    return hits / len(ranks), mrr


def run_eval(searcher, questions: list[EvalQuestion], k: int = 6, threshold: float | None = None) -> EvalReport:
    ranks: list[int | None] = []
    per_question: list[dict] = []
    answerable_scores: list[float] = []
    unanswerable_scores: list[float] = []
    for question in questions:
        result = searcher.search(question.q)
        top = result.hits[0].score if result.hits else float("-inf")
        record = {"q": question.q, "answerable": question.answerable, "top_score": top,
                  "top_citation": result.hits[0].citation if result.hits else "",
                  "ms": sum(result.timings.values())}
        if question.answerable:
            rank = first_match_rank(result.hits[:k], question.expect)
            ranks.append(rank)
            answerable_scores.append(top)
            record.update(rank=rank, passed=rank is not None)
        else:
            unanswerable_scores.append(top)
            record.update(rank=None, passed=None)
        per_question.append(record)
    hit_at_k, mrr = summarize(ranks, k)
    report = EvalReport(hit_at_k, mrr, 0.0, per_question, answerable_scores, unanswerable_scores)
    cut = calibrate_threshold(report) if threshold is None else threshold
    rejected = sum(1 for s in unanswerable_scores if cut is not None and s < cut)
    report.no_answer_acc = rejected / len(unanswerable_scores) if unanswerable_scores else 1.0
    for record in per_question:
        if not record["answerable"]:
            record["passed"] = cut is not None and record["top_score"] < cut
    return report


def calibrate_threshold(report: EvalReport) -> float | None:
    """Midpoint between the best unanswerable and the worst answerable top score.

    If they overlap, keep every answerable question (threshold just below the worst answerable score).
    """
    if not report.answerable_top_scores:
        return None
    low_answerable = min(report.answerable_top_scores)
    if not report.unanswerable_top_scores:
        return round(low_answerable - 0.01, 4)
    high_unanswerable = max(report.unanswerable_top_scores)
    if high_unanswerable < low_answerable:
        return round((high_unanswerable + low_answerable) / 2, 4)
    return round(low_answerable - 0.01, 4)


def write_settings(settings_path: Path, reranker: str, threshold: float) -> None:
    """Update reranker model and min_rerank_score in settings.yaml, keeping comments."""
    text = settings_path.read_text(encoding="utf-8")
    text = re.sub(r'(^\s*model:\s*)"[^"]*"(.*)$', lambda m: f'{m.group(1)}"{reranker}"{m.group(2)}',
                  text, count=1, flags=re.M) if "reranker:" in text else text
    text = re.sub(r"(^\s*min_rerank_score:\s*)[-\d.]+", lambda m: f"{m.group(1)}{threshold}", text, flags=re.M)
    settings_path.write_text(text, encoding="utf-8")
