import json

from noty.comments import comments_file, is_safe_segment, read_comments


def write_comments(data_dir, owner, board_uuid, payload):
    path = data_dir / "checklists" / owner / ".comments" / f"{board_uuid}.json"
    path.parent.mkdir(parents=True)
    path.write_text(payload if isinstance(payload, str) else json.dumps(payload), encoding="utf-8")


def test_reads_comments_sorted_by_time(tmp_path):
    write_comments(tmp_path, "alice", "b1", {
        "items": {
            "c1": [
                {"id": "m2", "author": "bob", "text": "second", "createdAt": "2026-10-06T11:00:00Z", "parentId": "m1"},
                {"id": "m1", "author": "carol", "text": "first", "createdAt": "2026-10-06T10:00:00Z", "parentId": None},
            ],
            "c2": [{"id": "m3", "author": "bob", "text": "other", "createdAt": "2026-10-06T10:30:00Z"}],
        }
    })

    comments = read_comments(tmp_path, "alice", "b1")

    assert [(c.id, c.card_id, c.author) for c in comments] == [("m1", "c1", "carol"), ("m3", "c2", "bob"), ("m2", "c1", "bob")]


def test_missing_file_means_no_comments(tmp_path):
    (tmp_path / "checklists" / "alice").mkdir(parents=True)
    assert read_comments(tmp_path, "alice", "b1") == []


def test_missing_owner_directory_means_unknown(tmp_path):
    # An empty or wrong mount must not look like "no comments", or the next fix would flood old ones.
    assert read_comments(tmp_path, "alice", "b1") is None


def test_unreadable_file_returns_none(tmp_path):
    write_comments(tmp_path, "alice", "b1", "{broken")
    assert read_comments(tmp_path, "alice", "b1") is None


def test_unexpected_shapes_are_skipped(tmp_path):
    write_comments(tmp_path, "alice", "b1", {"items": {"c1": "nope", "c2": [{"text": "no id"}, "junk"]}})
    assert read_comments(tmp_path, "alice", "b1") == []


def test_unknown_owner_returns_none(tmp_path):
    assert read_comments(tmp_path, None, "b1") is None


def test_path_traversal_is_refused(tmp_path):
    assert comments_file(tmp_path, "../etc", "b1") is None
    assert comments_file(tmp_path, "alice", "a/b") is None
    assert read_comments(tmp_path, "..", "b1") is None


def test_safe_segment_allows_email_usernames():
    assert is_safe_segment("john.doe@example.com")
    assert not is_safe_segment("")
    assert not is_safe_segment(".")
    assert not is_safe_segment("a\\b")
