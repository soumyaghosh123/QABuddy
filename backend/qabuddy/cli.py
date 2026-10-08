"""Command line: qabuddy ingest | search | eval | serve."""

from __future__ import annotations

import logging
import os
import sys

import typer

os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

from qabuddy.config import load_settings  # noqa: E402

app = typer.Typer(add_completion=False, help="QABuddy.ai — local hybrid RAG for QA engineers")


@app.callback()
def main() -> None:
    """QABuddy.ai — local hybrid RAG for QA engineers."""


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(level=logging.INFO if verbose else logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")


@app.command()
def ingest(full: bool = typer.Option(False, "--full", help="Drop the index and rebuild everything"),
           source: list[str] = typer.Option(None, "--source", help="Only these source ids (repeatable)"),
           verbose: bool = typer.Option(False, "--verbose", "-v")) -> None:
    """Index the files in data/ (only new or changed files unless --full)."""
    _setup_logging(verbose)
    from qabuddy.index.embedder import OllamaUnavailable
    from qabuddy.index.store import IndexUnavailable
    from qabuddy.ingest.pipeline import run_ingest

    settings = load_settings()
    try:
        report = run_ingest(settings, full=full, sources=source or None, progress=typer.echo)
    except (OllamaUnavailable, IndexUnavailable) as exc:
        typer.secho(f"ERROR: {exc}", fg="red", err=True)
        raise typer.Exit(2)
    typer.echo(f"Done in {report.seconds}s: {report.files_total} files, {report.files_changed} changed, "
               f"{report.files_removed} removed, {report.chunks_upserted} chunks upserted, "
               f"{report.chunks_deleted} deleted, {report.chunks_total} chunks in index.")
    for path, error in report.files_failed:
        typer.secho(f"FAILED {path}: {error}", fg="yellow")
    if report.files_failed:
        raise typer.Exit(1)


@app.command()
def search(question: str,
           source: list[str] = typer.Option(None, "--source", help="Only these source ids (repeatable)"),
           show_text: bool = typer.Option(False, "--text", help="Print each hit's full text")) -> None:
    """Debug retrieval: print the ranked sources for a question (no LLM)."""
    _setup_logging(False)
    from qabuddy.retrieval.search import build_searcher

    result = build_searcher(load_settings()).search(question, sources=source or None)
    typer.echo(f"no_answer={result.no_answer} timings={result.timings}")
    for hit in result.hits:
        typer.echo(f"[{hit.n}] {hit.score:7.3f}  {hit.source:<12} {hit.kind:<14} {hit.citation}")
        if show_text:
            typer.echo(f"     {hit.header}\n     {hit.text[:600]}\n")


@app.command("eval")
def eval_command(reranker: str = typer.Option(None, help="Cross-encoder model to evaluate (default from settings)"),
                 k: int = typer.Option(6, help="Top-k cut-off"),
                 write: bool = typer.Option(False, "--write", help="Save reranker and calibrated threshold to settings.yaml")) -> None:
    """Run the retrieval eval set: hit@k, MRR, no-answer accuracy, calibrated threshold."""
    _setup_logging(False)
    from qabuddy.config import REPO_ROOT
    from qabuddy.evaluate import calibrate_threshold, load_questions, run_eval, write_settings
    from qabuddy.retrieval.search import build_searcher

    settings = load_settings()
    if reranker:
        settings.reranker.model = reranker
    settings.retrieval.min_rerank_score = float("-inf")  # measure raw scores
    report = run_eval(build_searcher(settings), load_questions(REPO_ROOT / "eval" / "questions.yaml"), k=k)
    threshold = calibrate_threshold(report)
    for r in report.per_question:
        mark = "PASS" if r["passed"] else "MISS"
        rank = r["rank"] if r["answerable"] else "-"
        typer.echo(f"{mark} rank={rank!s:<4} top={r['top_score']:7.3f} {r['ms']:>6}ms  {r['q'][:70]}  → {r['top_citation'][:50]}")
    unanswerable = sum(1 for r in report.per_question if not r["answerable"])
    rejected = round(report.no_answer_acc * unanswerable)
    gate = report.hit_at_k >= 0.85 and report.no_answer_acc == 1.0
    typer.echo(f"\nreranker={settings.reranker.model}  hit@{k}={report.hit_at_k:.2%}  MRR={report.mrr:.3f}  "
               f"no-answer={rejected}/{unanswerable}  threshold={threshold}  gate={'PASS' if gate else 'FAIL'}")
    if write and threshold is not None:
        write_settings(settings.config_path / "settings.yaml", settings.reranker.model, threshold)
        typer.echo("settings.yaml updated")


@app.command()
def publish(dry_run: bool = typer.Option(False, "--dry-run", help="Show what would change; write nothing"),
            verbose: bool = typer.Option(False, "--verbose", "-v")) -> None:
    """Mirror the local index to Qdrant Cloud (cloud profile) and update the manifest."""
    _setup_logging(verbose)
    from qabuddy.components import active_embed_model, make_embedder
    from qabuddy.db import Database
    from qabuddy.index.store import IndexUnavailable, make_store
    from qabuddy.inference.errors import InferenceUnavailable
    from qabuddy.publish import PublishError, run_publish

    local, cloud = load_settings(profile="local"), load_settings(profile="cloud")
    try:
        report = run_publish(make_store(local), make_store(cloud), make_embedder(cloud), Database(local.db_path),
                             cloud.qdrant.meta_collection, active_embed_model(cloud), dry_run=dry_run,
                             progress=typer.echo)
    except (InferenceUnavailable, IndexUnavailable, PublishError) as exc:
        typer.secho(f"ERROR: {exc}", fg="red", err=True)
        raise typer.Exit(2)
    prefix = "Dry run — nothing written. " if report.dry_run else ""
    typer.echo(f"{prefix}Published: {report.new} new, {report.changed} changed, {report.deleted} deleted, "
               f"{report.unchanged} unchanged · {report.texts_embedded} chunks embedded · "
               f"index version {report.index_version} · {report.seconds}s")


@app.command()
def serve(host: str = typer.Option(None, help="Bind address (default from settings)"),
          port: int = typer.Option(None, help="Port (default from settings)")) -> None:
    """Run the web app and API."""
    _setup_logging(True)
    import uvicorn

    from qabuddy.api import create_app

    settings = load_settings()
    uvicorn.run(create_app(settings), host=host or settings.api.host, port=port or settings.api.port)


if __name__ == "__main__":
    app()
