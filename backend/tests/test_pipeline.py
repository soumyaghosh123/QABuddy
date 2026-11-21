from qabuddy.ingest.pipeline import needs_history


def test_history_rebuilt_when_jenkins_changed_or_full():
    assert needs_history(full=True, jenkins_changed=False, jenkins_removed=False,
                         jenkins_indexed=False, history_recorded=False)
    assert needs_history(full=False, jenkins_changed=True, jenkins_removed=False,
                         jenkins_indexed=True, history_recorded=True)
    assert needs_history(full=False, jenkins_changed=False, jenkins_removed=True,
                         jenkins_indexed=True, history_recorded=True)


def test_history_rebuilt_when_previous_run_was_interrupted():
    # Jenkins files were stored, but the run stopped before the history chunks were indexed.
    assert needs_history(full=False, jenkins_changed=False, jenkins_removed=False,
                         jenkins_indexed=True, history_recorded=False)


def test_history_skipped_when_nothing_jenkins_changed():
    assert not needs_history(full=False, jenkins_changed=False, jenkins_removed=False,
                             jenkins_indexed=True, history_recorded=True)
    assert not needs_history(full=False, jenkins_changed=False, jenkins_removed=False,
                             jenkins_indexed=False, history_recorded=False)


def test_quota_stop_keeps_progress_and_marks_pending(tmp_path, monkeypatch):
    from qabuddy.config import Settings
    from qabuddy.inference.errors import QuotaExceeded
    from qabuddy.ingest import pipeline
    from qabuddy.ingest.models import Chunk, SourceFile

    files = []
    for name in ("a.txt", "b.txt"):
        p = tmp_path / name
        p.write_text(name, encoding="utf-8")
        files.append(SourceFile(p, name, "s", "fake", name))

    class Store:
        def drop(self): pass
        def ensure_collection(self, dim, quantized=False): pass
        def upsert(self, *a, **k): pass
        def delete(self, ids): pass
        def count(self): return 1

    class Dense:
        dimension = 4
        calls = 0

        def embed_documents(self, texts):
            Dense.calls += 1
            if Dense.calls == 2:
                raise QuotaExceeded("quota")
            return [[0.0] * 4 for _ in texts]

    class Sparse:
        def embed_documents(self, texts): return [None for _ in texts]

    monkeypatch.setattr(pipeline, "make_store", lambda s: Store())
    monkeypatch.setattr(pipeline, "make_embedder", lambda s: Dense())
    monkeypatch.setattr(pipeline, "SparseEmbedder", Sparse)
    monkeypatch.setattr(pipeline, "discover", lambda s, only=None: files)
    monkeypatch.setattr(pipeline, "source_labels", lambda s: {})
    monkeypatch.setattr(pipeline, "get_parser", lambda name: lambda sf, ctx: [
        Chunk(sf.rel_path, "s", "k", sf.rel_path, "c", sf.rel_path)])
    settings = Settings(data_dir=str(tmp_path), storage_dir=str(tmp_path / "st"))
    report = pipeline.run_ingest(settings)
    db = pipeline.Database(settings.db_path)
    assert report.pending and report.chunks_upserted == 1
    assert db.get_file("a.txt")["status"] == "ok" and db.get_file("b.txt") is None
    assert db.index_version() > 0
