import json

import httpx
import pytest

from vikunja_mcp.api import VikunjaAPI, VikunjaError


def make_api(handler):
    transport = httpx.MockTransport(handler)
    client = httpx.Client(
        base_url="https://t.example/api/v1",
        headers={"Authorization": "Bearer tk"},
        transport=transport,
    )
    return VikunjaAPI("https://t.example", "tk", client=client)


def test_url_normalization_appends_api_v1():
    api = VikunjaAPI("https://t.example/", "tk")
    assert str(api._client.base_url).rstrip("/") == "https://t.example/api/v1"
    api2 = VikunjaAPI("https://t.example/api/v1", "tk")
    assert str(api2._client.base_url).rstrip("/") == "https://t.example/api/v1"


def test_error_raises_vikunja_error():
    def handler(request):
        return httpx.Response(403, json={"message": "no access"})

    api = make_api(handler)
    with pytest.raises(VikunjaError) as exc:
        api.get_task(1)
    assert exc.value.status == 403 and "no access" in str(exc.value)


def test_update_task_is_read_modify_write():
    """POST = полная перезапись: update обязан слать ВСЕ поля задачи, не только изменённые."""
    calls = []

    def handler(request):
        calls.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={
                "id": 5, "title": "t", "description": "keep me", "priority": 3, "done": False,
            })
        return httpx.Response(200, json=json.loads(request.content))

    api = make_api(handler)
    result = api.update_task(5, priority=5)
    sent = json.loads(calls[1].content)
    assert calls[1].method == "POST" and calls[1].url.path.endswith("/tasks/5")
    assert sent["description"] == "keep me"      # старое поле не потеряно
    assert sent["priority"] == 5
    assert result["priority"] == 5


def test_create_task_uses_put():
    def handler(request):
        assert request.method == "PUT" and request.url.path.endswith("/projects/3/tasks")
        body = json.loads(request.content)
        return httpx.Response(201, json={"id": 9, **body})

    api = make_api(handler)
    t = api.create_task(3, "new task", description="d", priority=2)
    assert t["id"] == 9 and t["title"] == "new task"


def test_comments_and_assignees_endpoints():
    seen = []

    def handler(request):
        seen.append((request.method, request.url.path))
        return httpx.Response(200, json={})

    api = make_api(handler)
    api.comments(7)
    api.add_comment(7, "note")
    api.add_assignee(7, 2)
    api.remove_assignee(7, 2)
    api.add_relation(7, 1, "parenttask")
    assert seen == [
        ("GET", "/api/v1/tasks/7/comments"),
        ("PUT", "/api/v1/tasks/7/comments"),
        ("PUT", "/api/v1/tasks/7/assignees"),
        ("DELETE", "/api/v1/tasks/7/assignees/2"),
        ("PUT", "/api/v1/tasks/7/relations"),
    ]


def test_add_comment_sends_html_not_raw_plain_text():
    # #85: the comment field is HTML — add_comment must convert agent plain text to
    # structure-preserving, escaped HTML on the wire, not ship raw newlines/'<'.
    sent = {}

    def handler(request):
        sent["body"] = json.loads(request.content)
        return httpx.Response(200, json={})

    api = make_api(handler)
    api.add_comment(7, "[worklog]\nfixed a < b bug\n\nEvidence: abc")
    comment = sent["body"]["comment"]
    assert comment.startswith("<p>[worklog]")   # marker intact at the front
    assert "<br>" in comment                     # single newline -> line break
    assert comment.count("<p>") == 2             # blank line -> new paragraph
    assert "&lt; b" in comment                   # literal '<' escaped, markup safe
    assert "\n" not in comment                   # no raw newline leaked into the field
