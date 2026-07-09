import json
import time

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


def test_download_attachment_returns_raw_bytes_not_json():
    """#139: the download endpoint streams the file itself, not JSON — download_attachment
    returns the bytes verbatim (r.json() would blow up on a binary body). `attachment_id`
    is the attachment's own id, so the URL is /tasks/{id}/attachments/{attachment_id}."""
    def handler(request):
        assert request.method == "GET"
        assert request.url.path.endswith("/tasks/5/attachments/7")
        return httpx.Response(200, content=b"\x89PNG\r\n\x1a\nrawbytes")

    api = make_api(handler)
    data = api.download_attachment(5, 7)
    assert data == b"\x89PNG\r\n\x1a\nrawbytes"
    assert isinstance(data, bytes)


def test_download_attachment_404_raises_vikunja_error():
    def handler(request):
        return httpx.Response(
            404, json={"code": 4011, "message": "This task attachment does not exist."}
        )

    api = make_api(handler)
    with pytest.raises(VikunjaError) as exc:
        api.download_attachment(5, 999)
    assert exc.value.status == 404


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


# --- транзиентные ретраи (#86) ---------------------------------------------------------


@pytest.fixture
def no_sleep(monkeypatch):
    # backoff -> no real waiting, tests stay instant (api.py imports the module `time`)
    monkeypatch.setattr(time, "sleep", lambda _s: None)


def test_transient_5xx_on_get_is_retried_then_succeeds(no_sleep):
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(503, json={"message": "unavailable"})
        return httpx.Response(200, json={"id": 1, "title": "ok"})

    api = make_api(handler)
    assert api.get_task(1)["title"] == "ok"
    assert calls["n"] == 3   # 2 transient failures retried, 3rd succeeded


def test_transient_retries_are_bounded_then_raise(no_sleep):
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(503, json={"message": "down"})

    api = make_api(handler)
    with pytest.raises(VikunjaError) as exc:
        api.get_task(1)
    assert exc.value.status == 503
    assert calls["n"] == VikunjaAPI._MAX_RETRIES + 1   # bounded, then the last error surfaces


def test_permanent_4xx_is_not_retried(no_sleep):
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(404, json={"message": "nope"})

    api = make_api(handler)
    with pytest.raises(VikunjaError):
        api.get_task(1)
    assert calls["n"] == 1   # a permanent error surfaces immediately, no retry


def test_put_create_is_not_retried_on_5xx(no_sleep):
    # PUT = create: a 5xx may have applied server-side; retrying would duplicate -> no retry.
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(502, json={"message": "bad gateway"})

    api = make_api(handler)
    with pytest.raises(VikunjaError) as exc:
        api.create_task(3, "t")
    assert exc.value.status == 502
    assert calls["n"] == 1   # non-idempotent create not retried on an ambiguous 5xx


def test_429_is_retried_even_for_put_create(no_sleep):
    # 429 = rejected before applying -> safe to retry even a create; it lands exactly once.
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] < 2:
            return httpx.Response(429, json={"message": "slow down"})
        return httpx.Response(201, json={"id": 9, "title": "t"})

    api = make_api(handler)
    assert api.create_task(3, "t")["id"] == 9
    assert calls["n"] == 2   # 429 retried once, then created exactly once


def test_raw_download_inherits_get_retry_on_transient_5xx(no_sleep):
    """#139: the raw download goes through _req(raw=True), so it inherits the #86 GET
    retry/backoff — a transient 5xx is retried, then the bytes come back."""
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] < 2:
            return httpx.Response(503, json={"message": "unavailable"})
        return httpx.Response(200, content=b"filebytes")

    api = make_api(handler)
    assert api.download_attachment(1, 1) == b"filebytes"
    assert calls["n"] == 2   # one transient failure retried, then success


def test_upload_attachment_sends_multipart_put_not_json():
    """#137: an upload goes out as multipart/form-data (field `files`) via PUT — api.py's JSON
    body helper doesn't fit, so _req(files=...) is used. Verified on real 2.3.0: PUT (POST->405),
    response {"errors":..., "success":[...]}; the filename and raw bytes ride in the multipart."""
    seen = {}

    def handler(request):
        seen["method"] = request.method
        seen["path"] = request.url.path
        seen["ctype"] = request.headers.get("content-type", "")
        seen["body"] = request.content
        return httpx.Response(
            200, json={"errors": None, "success": [{"id": 3, "file": {"name": "shot.png"}}]}
        )

    api = make_api(handler)
    resp = api.upload_attachment(9, "shot.png", b"\x89PNGdata", mime="image/png")
    assert seen["method"] == "PUT"
    assert seen["path"].endswith("/tasks/9/attachments")
    assert seen["ctype"].startswith("multipart/form-data")   # not application/json
    assert b"shot.png" in seen["body"] and b"\x89PNGdata" in seen["body"]
    assert resp["success"][0]["id"] == 3


def test_upload_attachment_put_is_not_retried_on_5xx(no_sleep):
    """PUT=create: an ambiguous 5xx may have stored the file server-side, so retrying would
    duplicate the attachment -> not retried (same rule as create_task)."""
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(502, json={"message": "bad gateway"})

    api = make_api(handler)
    with pytest.raises(VikunjaError) as exc:
        api.upload_attachment(1, "a.png", b"x", mime="image/png")
    assert exc.value.status == 502
    assert calls["n"] == 1   # non-idempotent upload not retried on an ambiguous 5xx


def test_upload_attachment_429_retried_with_body_reencoded(no_sleep):
    """429 = rejected before applying -> safe to retry even a create; because the body is passed as
    BYTES (not a consumed stream), the retry re-encodes the SAME multipart, not an empty one."""
    calls = {"n": 0}
    bodies = []

    def handler(request):
        calls["n"] += 1
        bodies.append(request.content)
        if calls["n"] < 2:
            return httpx.Response(429, json={"message": "slow down"})
        return httpx.Response(200, json={"errors": None, "success": [{"id": 7}]})

    api = make_api(handler)
    out = api.upload_attachment(1, "a.png", b"payload", mime="image/png")
    assert out["success"][0]["id"] == 7
    assert calls["n"] == 2                                    # retried exactly once
    assert b"payload" in bodies[0] and b"payload" in bodies[1]   # same body re-sent, never empty


def test_connection_drop_retried_for_get_not_for_put(no_sleep):
    # "Connection closed mid-response": retry the idempotent GET, never the PUT create.
    calls = {"GET": 0, "PUT": 0}

    def handler(request):
        calls[request.method] += 1
        raise httpx.ReadError("Connection closed mid-response")

    api = make_api(handler)
    with pytest.raises(httpx.TransportError):
        api.get_task(1)
    with pytest.raises(httpx.TransportError):
        api.create_task(3, "t")
    assert calls["GET"] == VikunjaAPI._MAX_RETRIES + 1   # idempotent -> retried to exhaustion
    assert calls["PUT"] == 1                             # create -> raised immediately
