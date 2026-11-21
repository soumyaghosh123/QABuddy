from qabuddy.db import Database
from qabuddy.desktop.inputs import Inputs
from qabuddy.desktop.meta import LAST_SYNC_ID, SharedMeta


class FakeStore:
    def __init__(self):
        self.data = {}

    def ensure_meta_collection(self, name):
        self.data.setdefault(name, {})

    def read_meta(self, name, point_id):
        return self.data.get(name, {}).get(point_id)

    def write_meta(self, name, point_id, payload):
        self.data.setdefault(name, {})[point_id] = payload


class FakeClock:
    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now


def make(clock=None):
    return SharedMeta(FakeStore(), "m", clock or FakeClock(1000))


def test_state_round_trip(tmp_path):
    meta = make()
    db1 = Database(tmp_path / "a.db"); db1.init()
    db1.upsert_file("zephyr/AUSWEB/AUSWEB-T1.json", "zephyr_ausweb", "h", 1, 0.0, ["c1"], "ok", None)
    db1.replace_entities([("auswebt1", "AUSWEB-T1", "test", "zephyr/AUSWEB/AUSWEB-T1.json")])
    db1.set_meta("index_version", "7")
    meta.push_state(db1)
    db2 = Database(tmp_path / "b.db"); db2.init()
    db2.upsert_file("stale.json", "s", "h", 1, 0.0, [], "ok", None)
    db2.replace_entities([("x", "X", "k", "stale.json")])
    db2.set_meta("stale", "1")
    meta.pull_state(db2)
    assert db2.get_file("zephyr/AUSWEB/AUSWEB-T1.json")["chunk_ids"] == ["c1"]
    assert db2.get_file("stale.json") is None
    assert db2.known_entities() == {"auswebt1"}
    assert db2.get_meta("index_version") == "7"
    assert db2.get_meta("stale") is None


def test_pull_state_absent_leaves_db(tmp_path):
    db = Database(tmp_path / "a.db"); db.init()
    db.upsert_file("p", "s", "h", 1, 0.0, [], "ok", None)
    make().pull_state(db)
    assert db.get_file("p") is not None


def test_lock_blocks_second_machine_until_expiry():
    clock = FakeClock(1000)
    meta = make(clock)
    assert meta.acquire_lock("PC-A", 7200) is None
    assert meta.acquire_lock("PC-B", 7200) == "PC-A"
    assert meta.acquire_lock("PC-A", 7200) is None
    clock.now = 1000 + 7201
    assert meta.acquire_lock("PC-B", 7200) is None


def test_release_only_by_holder():
    meta = make()
    meta.acquire_lock("PC-A", 7200)
    meta.release_lock("PC-B")
    assert meta.acquire_lock("PC-B", 7200) == "PC-A"
    meta.release_lock("PC-A")
    assert meta.acquire_lock("PC-B", 7200) is None


def test_inputs_default_when_absent():
    assert make().read_inputs() == Inputs()


def test_write_inputs_stamps_and_round_trips():
    meta = make(FakeClock(555))
    out = meta.write_inputs(Inputs(zephyr_projects=["AUSWEB"]), "PC-A")
    assert (out.updated_by, out.updated_at) == ("PC-A", 555)
    assert meta.read_inputs() == out


def test_last_sync_round_trip():
    meta = make()
    assert meta.read_last_sync() is None
    info = {"finished_at": 1.0, "machine": "PC-A", "pending": 0,
            "sources": {"s": {"ok": True, "chunks": 3, "error": ""}}}
    meta.write_last_sync(info)
    assert meta.read_last_sync() == info
    assert LAST_SYNC_ID.endswith("a4")


def test_read_connector_state_in_memory(tmp_path):
    meta = make()
    assert meta.read_connector_state() == {}
    db = Database(tmp_path / "a.db"); db.init()
    meta.push_state(db)
    assert meta.read_connector_state() == {}
    db.set_meta("connector_state", '{"jira": {"id_to_key": {"1": "VWO-1"}}}')
    meta.push_state(db)
    assert meta.read_connector_state() == {"jira": {"id_to_key": {"1": "VWO-1"}}}
