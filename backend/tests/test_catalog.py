import pytest

from qabuddy.catalog import (MANIFEST_ID, ManifestCatalog, SqliteCatalog, build_manifest, file_stats, make_catalog,
                             read_manifest, write_manifest)
from qabuddy.config import load_settings
from qabuddy.db import Database

EMPTY_STATS = {"by_source": {}, "files": 0, "failed_files": []}


@pytest.fixture()
def db(tmp_path):
    database = Database(tmp_path / "q.db")
    database.init()
    database.upsert_file("a.md", "jira", "h1", 10, 1.0, ["c1", "c2"], "ok", None)
    database.upsert_file("b.pdf", "requirements", "h2", 10, 1.0, [], "failed", "bad pdf")
    database.upsert_file("__entities__", "jira", "h3", 0, 1.0, ["c3"], "ok", None)
    database.bump_index_version()
    database.set_meta("last_ingest_at", "12.5")
    database.replace_entities([("vwo-26", "VWO-26", "jira_key", "01_JIRA_Tickets/Bug_VWO_26.md")])
    return database


class FakeMetaStore:
    def __init__(self, manifest=None):
        self.meta = {} if manifest is None else {("meta", MANIFEST_ID): manifest}
        self.reads = 0

    def read_meta(self, name, point_id):
        self.reads += 1
        return self.meta.get((name, point_id))

    def ensure_meta_collection(self, name):
        pass

    def write_meta(self, name, point_id, payload):
        self.meta[(name, point_id)] = payload


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def test_file_stats(db):
    assert file_stats(db) == {"by_source": {"jira": 3, "requirements": 0}, "files": 2,
                              "failed_files": [{"path": "b.pdf", "error": "bad pdf"}]}


def test_sqlite_catalog_reads_database(db):
    catalog = SqliteCatalog(db)
    assert catalog.index_version() == 1
    assert catalog.known_entities() == {"vwo-26"}
    assert catalog.last_ingest_at() == 12.5
    assert catalog.stats() == file_stats(db)
    assert catalog.hint == "qabuddy ingest"


def test_build_manifest_from_database(db):
    manifest = build_manifest(db, 1162, 3, "@cf/qwen/qwen3-embedding-0.6b", 99.0)
    assert manifest == {"index_version": 3, "published_at": 99.0, "last_ingest_at": 12.5,
                        "embed_model": "@cf/qwen/qwen3-embedding-0.6b", "chunks": 1162, **file_stats(db),
                        "entities": ["vwo-26"]}


def test_manifest_round_trip_through_store(db):
    store = FakeMetaStore()
    manifest = build_manifest(db, 5, 1, "m", 1.0)
    write_manifest(store, "meta", manifest)
    assert read_manifest(store, "meta") == manifest


def test_manifest_catalog_caches_for_ttl(db):
    store, clock = FakeMetaStore(build_manifest(db, 5, 4, "m", 1.0)), Clock()
    catalog = ManifestCatalog(store, "meta", ttl_s=300.0, clock=clock)
    assert catalog.index_version() == 4 and catalog.index_version() == 4
    assert store.reads == 1
    clock.now = 301.0
    assert catalog.known_entities() == {"vwo-26"}
    assert store.reads == 2
    assert catalog.hint == "qabuddy publish"


def test_manifest_catalog_missing_manifest():
    store = FakeMetaStore()
    catalog = ManifestCatalog(store, "meta", clock=Clock())
    assert catalog.index_version() == 0
    assert catalog.known_entities() == set()
    assert catalog.stats() == EMPTY_STATS
    assert catalog.last_ingest_at() is None
    assert store.reads == 4  # a missing manifest is not cached


def test_make_catalog_by_provider(db):
    assert isinstance(make_catalog(load_settings(env={}), db=db), SqliteCatalog)
    cloud = load_settings(env={"QABUDDY_PROFILE": "cloud"})
    assert isinstance(make_catalog(cloud, store=FakeMetaStore()), ManifestCatalog)
    with pytest.raises(ValueError, match="catalog.provider"):
        make_catalog(load_settings(env={"QABUDDY_CATALOG__PROVIDER": "nope"}), db=db)
