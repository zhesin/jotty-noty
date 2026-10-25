import json

from noty.state import HEARTBEAT_FILE, STATE_VERSION, StateStore

from tests.helpers import board, card


def test_roundtrip(tmp_path):
    store = StateStore(tmp_path / "state")
    boards = {"b1": board(card(parents=("Parent",), parent_id="p", assignee="bob"), comment_ids={"m1", "m2"})}
    boards["b1"].missing_since = "2026-10-01T00:00:00+00:00"

    store.save("alice", boards)
    loaded = store.load("alice")

    assert loaded["b1"].cards == boards["b1"].cards
    assert loaded["b1"].comment_ids == {"m1", "m2"}
    assert loaded["b1"].statuses == boards["b1"].statuses
    assert loaded["b1"].missing_since == "2026-10-01T00:00:00+00:00"
    assert list((tmp_path / "state").iterdir()) == [tmp_path / "state" / "alice.json"]


def test_unknown_comment_ids_survive_roundtrip(tmp_path):
    store = StateStore(tmp_path)
    store.save("alice", {"b1": board(comment_ids=None)})
    assert store.load("alice")["b1"].comment_ids is None


def test_missing_state_is_none(tmp_path):
    assert StateStore(tmp_path).load("alice") is None


def test_corrupt_state_is_none(tmp_path):
    (tmp_path / "alice.json").write_text("{not json")
    assert StateStore(tmp_path).load("alice") is None
    (tmp_path / "alice.json").write_text(json.dumps({"version": STATE_VERSION, "boards": {"b1": {"title": "no uuid"}}}))
    assert StateStore(tmp_path).load("alice") is None
    (tmp_path / "alice.json").write_text("[]")
    assert StateStore(tmp_path).load("alice") is None


def test_other_version_is_none(tmp_path):
    (tmp_path / "alice.json").write_text(json.dumps({"version": 99, "boards": {}}))
    assert StateStore(tmp_path).load("alice") is None


def test_failed_write_keeps_the_old_file(tmp_path, monkeypatch):
    store = StateStore(tmp_path)
    store.save("alice", {"b1": board(card())})

    def broken_dump(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr("noty.state.json.dump", broken_dump)
    try:
        store.save("alice", {})
    except OSError:
        pass
    assert list(store.load("alice")) == ["b1"]
    assert sorted(p.name for p in tmp_path.iterdir()) == ["alice.json"]


def test_heartbeat(tmp_path):
    StateStore(tmp_path / "s").touch_heartbeat()
    assert (tmp_path / "s" / HEARTBEAT_FILE).exists()
