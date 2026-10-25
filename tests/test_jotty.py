import httpx
import pytest

from noty.jotty import BoardGone, JottyClient, JottyError, Unauthorized, build_board_state, same_version

from tests.helpers import api_board, api_checklist, api_item, kanban_item


def client_with(handler) -> JottyClient:
    return JottyClient("http://jotty:3000/", "ck_test", transport=httpx.MockTransport(handler))


def test_list_boards_queries_kanban_type_with_key():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.path, dict(request.url.params), request.headers.get("x-api-key")))
        return httpx.Response(200, json={"checklists": [api_checklist("b1"), api_checklist("b2")], "total": 2})

    with client_with(handler) as client:
        boards = client.list_boards()

    assert [b["id"] for b in boards] == ["b1", "b2"]
    assert seen == [("/api/checklists", {"type": "kanban"}, "ck_test")]


def test_current_username():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/user"
        return httpx.Response(200, json={"user": {"username": "alice", "isAdmin": False}})

    with client_with(handler) as client:
        assert client.current_username() == "alice"


def test_current_username_missing():
    with client_with(lambda request: httpx.Response(200, json={"user": {}})) as client:
        with pytest.raises(JottyError):
            client.current_username()


def test_401_is_unauthorized():
    with client_with(lambda request: httpx.Response(401, json={"error": "Unauthorized"})) as client:
        with pytest.raises(Unauthorized):
            client.list_boards()


def test_get_board_returns_board():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/kanban/b1"
        return httpx.Response(200, json={"board": api_board("b1")})

    with client_with(handler) as client:
        assert client.get_board("b1")["id"] == "b1"


def test_get_board_404_is_board_gone():
    with client_with(lambda request: httpx.Response(404, json={"error": "Not found"})) as client:
        with pytest.raises(BoardGone):
            client.get_board("b1")


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(403, json={"error": "Forbidden"}),
        httpx.Response(500, text="boom"),
        httpx.Response(200, text="not json"),
        httpx.Response(200, json=[1, 2]),
    ],
)
def test_bad_responses_raise(response):
    with client_with(lambda request: response) as client:
        with pytest.raises(JottyError):
            client.list_boards()


def test_network_error_raises():
    def handler(request):
        raise httpx.ConnectError("refused")

    with client_with(handler) as client:
        with pytest.raises(JottyError, match="ConnectError"):
            client.list_boards()


def test_build_board_state_merges_both_endpoints():
    checklist = api_checklist(
        "b1",
        items=[
            api_item(
                "c1",
                description="desc",
                priority="high",
                targetDate="2026-10-10",
                lastModifiedBy="bob",
                lastModifiedAt="2026-10-06T10:00:00.000Z",
                children=[api_item("c2", text="Write tests", createdBy="bob")],
            )
        ],
    )
    kanban = api_board("b1", items=[kanban_item("c1", children=[kanban_item("c2", assignee="alice")])])

    state = build_board_state(checklist, kanban)

    assert state.uuid == "b1"
    assert state.owner == "alice"
    assert state.statuses["in_progress"] == "In Progress"
    parent, child = state.cards["c1"], state.cards["c2"]
    assert (parent.priority, parent.target_date, parent.description) == ("high", "2026-10-10", "desc")
    assert (parent.last_modified_by, parent.last_modified_at) == ("bob", "2026-10-06T10:00:00.000Z")
    assert parent.assignee is None and parent.parents == () and parent.parent_id is None
    assert child.assignee == "alice"
    assert child.created_by == "bob"
    assert child.parents == ("Fix login bug",)
    assert child.parent_id == "c1"
    assert state.root_of(child) is parent
    assert state.comment_ids is None


def test_same_version_compares_updated_at():
    assert same_version(api_checklist("b1"), api_board("b1"))
    assert not same_version(api_checklist("b1"), api_board("b1", updatedAt="2026-10-06T09:00:01.000Z"))


def test_same_version_tolerates_one_millisecond():
    # GNU find in the list call truncates the mtime, fs.stat in the detail call rounds it.
    listed = api_checklist("b1", updatedAt="2026-10-06T09:00:00.122Z")
    assert same_version(listed, api_board("b1", updatedAt="2026-10-06T09:00:00.123Z"))
    assert not same_version(listed, api_board("b1", updatedAt="2026-10-06T09:00:00.124Z"))
    assert not same_version(listed, api_board("b1", updatedAt="not a date"))
    assert not same_version(listed, api_board("b1", updatedAt=None))
