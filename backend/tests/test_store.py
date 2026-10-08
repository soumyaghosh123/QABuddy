import uuid

import httpx
import pytest
from qdrant_client import models
from qdrant_client.http.exceptions import UnexpectedResponse

from qabuddy.index.store import IndexUnavailable, VectorStore

LOCAL = "http://localhost:6333"


def _unexpected(status: int) -> UnexpectedResponse:
    return UnexpectedResponse(status, "x", b"{}", httpx.Headers())


def _raise(exc):
    def fn(*args, **kwargs):
        raise exc
    return fn


def test_auth_error_maps_to_index_unavailable(monkeypatch):
    store = VectorStore("https://x.cloud.qdrant.io", "c", api_key="k")
    monkeypatch.setattr(store.client, "collection_exists", lambda *a, **k: True)
    monkeypatch.setattr(store.client, "count", _raise(_unexpected(401)))
    with pytest.raises(IndexUnavailable, match="QDRANT_API_KEY"):
        store.count()


def test_missing_collection_in_hybrid(monkeypatch):
    store = VectorStore(LOCAL, "c")
    monkeypatch.setattr(store.client, "query_points", _raise(_unexpected(404)))
    with pytest.raises(IndexUnavailable, match="qabuddy publish"):
        store.hybrid([0.0], models.SparseVector(indices=[], values=[]), [], None, 1, 1, 1, 1)


def test_cloud_hint_in_connection_error(monkeypatch):
    store = VectorStore("https://x.cloud.qdrant.io", "c")
    monkeypatch.setattr(store.client, "collection_exists", lambda *a, **k: True)
    monkeypatch.setattr(store.client, "count", _raise(httpx.ConnectError("x")))
    with pytest.raises(IndexUnavailable, match="QDRANT_URL"):
        store.count()


def test_client_is_created_lazily():
    store = VectorStore("", "c")  # an unset QDRANT_URL must not break building the app
    assert store._client is None


@pytest.mark.integration
def test_meta_and_scroll_round_trip():
    try:
        httpx.get(f"{LOCAL}/collections", timeout=2).raise_for_status()
    except httpx.HTTPError as exc:
        pytest.skip(f"local Qdrant not reachable: {exc}")
    suffix = uuid.uuid4().hex[:8]
    meta, pts = f"pytest_meta_{suffix}", f"pytest_pts_{suffix}"
    store = VectorStore(LOCAL, pts)
    try:
        store.ensure_meta_collection(meta)
        point_id = "00000000-0000-0000-0000-000000000001"
        store.write_meta(meta, point_id, {"index_version": 1})
        assert store.read_meta(meta, point_id) == {"index_version": 1}
        assert store.read_meta(meta, "00000000-0000-0000-0000-000000000002") is None

        store.ensure_collection(3)
        ids = [str(uuid.uuid4()), str(uuid.uuid4())]
        sparse = models.SparseVector(indices=[7, 9], values=[1.0, 1.0])
        store.upsert_raw([models.PointStruct(id=i, vector={"dense": [0.1, 0.2, 0.3], "bm25": sparse},
                                             payload={"text": i}) for i in ids])
        rows = {pid: (payload, vectors) for pid, payload, vectors in store.scroll_all(with_vectors=True)}
        assert set(rows) == set(ids)
        assert all(rows[i][0] == {"text": i} for i in ids)
        assert all(rows[i][1]["bm25"].indices == [7, 9] for i in ids)
        assert all(vectors == {} for _, _, vectors in store.scroll_all())
    finally:
        store.drop()
        store.client.delete_collection(meta)
