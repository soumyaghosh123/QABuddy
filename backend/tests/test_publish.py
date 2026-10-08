import pytest
from qdrant_client import models

from qabuddy.catalog import MANIFEST_ID
from qabuddy.db import Database
from qabuddy.inference.errors import InferenceUnavailable
from qabuddy.publish import PublishError, diff_points, run_publish

META = "qabuddy_meta"


class FakeStore:
    def __init__(self, points=None, exists=True):
        self.points = dict(points or {})  # id -> (payload, vectors)
        self.meta: dict = {}
        self.ops: list[str] = []
        self._exists = exists

    def exists(self):
        return self._exists

    def ensure_collection(self, dim):
        self.ops.append("ensure_collection")
        self._exists = True

    def scroll_all(self, with_vectors=False, batch=256):
        for pid, (payload, vectors) in self.points.items():
            yield pid, payload, vectors if with_vectors else {}

    def upsert_raw(self, points, batch_size=64):
        self.ops.append("upsert_raw")
        for p in points:
            self.points[str(p.id)] = (p.payload, p.vector)

    def delete(self, ids):
        self.ops.append("delete")
        for pid in ids:
            self.points.pop(pid, None)

    def ensure_meta_collection(self, name):
        self.ops.append("ensure_meta_collection")

    def read_meta(self, name, point_id):
        return self.meta.get((name, point_id))

    def write_meta(self, name, point_id, payload):
        self.ops.append("write_meta")
        self.meta[(name, point_id)] = payload


class FakeEmbedder:
    dimension = 2

    def __init__(self, fail_on_call=None):
        self.texts: list[str] = []
        self.calls = 0
        self.fail_on_call = fail_on_call

    def embed_documents(self, texts, progress=None):
        self.calls += 1
        if self.calls == self.fail_on_call:
            raise InferenceUnavailable("cf down")
        self.texts.extend(texts)
        return [[float(len(t)), 0.0] for t in texts]


@pytest.fixture()
def db(tmp_path):
    database = Database(tmp_path / "q.db")
    database.init()
    return database


def _pid(n: int) -> str:
    return f"00000000-0000-0000-0000-{n:012d}"


def _bm25(i: int) -> models.SparseVector:
    return models.SparseVector(indices=[i + 1], values=[1.0])


def _local(n: int) -> FakeStore:
    return FakeStore({_pid(i): ({"header": f"h{i}", "text": f"t{i}"}, {"dense": [9.9], "bm25": _bm25(i)})
                      for i in range(n)})


def _publish(local, cloud, db, embedder=None, dry_run=False):
    return run_publish(local, cloud, embedder or FakeEmbedder(), db, META, "@cf/qwen/qwen3-embedding-0.6b",
                       dry_run=dry_run, clock=lambda: 42.0)


def test_diff_points_buckets():
    local = {"a": {"x": 1}, "b": {"x": 1}, "c": {"x": 1}}
    cloud = {"b": {"x": 1}, "c": {"x": 2}, "d": {"x": 1}}
    assert diff_points(local, cloud) == (["a"], ["c"], ["d"], 1)


def test_first_publish_uploads_everything_and_writes_manifest(db):
    local, cloud, embedder = _local(3), FakeStore(exists=False), FakeEmbedder()
    report = _publish(local, cloud, db, embedder)
    assert (report.new, report.changed, report.deleted, report.unchanged) == (3, 0, 0, 0)
    assert report.texts_embedded == 3 and report.index_version == 1
    assert embedder.texts == ["h0\nt0", "h1\nt1", "h2\nt2"]
    for i in range(3):
        payload, vectors = cloud.points[_pid(i)]
        assert payload == {"header": f"h{i}", "text": f"t{i}"}
        assert vectors == {"dense": [5.0, 0.0], "bm25": _bm25(i)}
    manifest = cloud.meta[(META, MANIFEST_ID)]
    assert manifest["index_version"] == 1 and manifest["chunks"] == 3 and manifest["published_at"] == 42.0


def test_second_publish_without_changes_keeps_version(db):
    local, cloud = _local(3), FakeStore(exists=False)
    _publish(local, cloud, db)
    embedder = FakeEmbedder()
    report = _publish(local, cloud, db, embedder)
    assert (report.new, report.changed, report.deleted, report.unchanged) == (0, 0, 0, 3)
    assert embedder.calls == 0 and report.index_version == 1


def test_changed_and_deleted_points(db):
    local, cloud = _local(3), FakeStore(exists=False)
    _publish(local, cloud, db)
    local.points[_pid(0)] = ({"header": "h0", "text": "edited"}, local.points[_pid(0)][1])
    del local.points[_pid(2)]
    report = _publish(local, cloud, db)
    assert (report.changed, report.deleted, report.index_version) == (1, 1, 2)
    assert _pid(2) not in cloud.points and cloud.points[_pid(0)][0]["text"] == "edited"


def test_manifest_written_last(db):
    cloud = FakeStore(exists=False)
    _publish(_local(3), cloud, db)
    assert cloud.ops[-1] == "write_meta"


def test_dry_run_writes_nothing(db):
    cloud = FakeStore(exists=False)
    report = _publish(_local(3), cloud, db, dry_run=True)
    assert cloud.ops == [] and report.dry_run
    assert (report.new, report.texts_embedded) == (3, 0)


def test_refuses_when_local_index_empty(db):
    cloud = FakeStore({_pid(1): ({"text": "keep"}, {})})
    with pytest.raises(PublishError, match="Local index is empty"):
        _publish(FakeStore(), cloud, db)
    assert cloud.ops == [] and _pid(1) in cloud.points


def test_failure_mid_way_leaves_manifest_unchanged(db):
    cloud = FakeStore(exists=False)
    with pytest.raises(InferenceUnavailable):
        _publish(_local(100), cloud, db, FakeEmbedder(fail_on_call=2))
    assert "write_meta" not in cloud.ops and (META, MANIFEST_ID) not in cloud.meta
