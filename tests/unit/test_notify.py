"""#252: Slack-webhook ping when call_human parks a card in Your Call.

The human used to discover a YC card only by looking at the board; call_human now closes that
gap with ONE Slack-compatible incoming-webhook message. Tests drive Workflow through FakeAPI
with the REAL WebhookNotifier backed by an httpx.MockTransport client, so the asserted
payload/URL are exactly the bytes a real gateway would receive. Contract under pin:
  * configured -> exactly one POST to the configured URL, payload EXACTLY {"text": ...}
    (the minimal shape every Slack-compatible gateway accepts), text carrying ref, title,
    the question and the frontend deep-link; result grows notified:true;
  * unset -> no notifier, no 'notified' key: behavior bit-for-bit as before;
  * best-effort -> a 500 / timeout / refused connection costs the PING (notified:false plus
    one stderr note, NEVER stdout — a stray byte corrupts the MCP stdio protocol), never the
    parked question: the card still lands in Your Call with its [нужен человек] comment;
  * ordering -> a REFUSED call_human (empty question / wrong stage / not mine) pings nothing.
"""
import json

import httpx
import pytest

from tests.unit.fakes import FakeAPI
from vikunja_mcp.notify import WebhookNotifier
from vikunja_mcp.workflow import STAGES, Workflow, WorkflowError

WEBHOOK = "https://hooks.example.com/services/T000/B000/secret"
TRACKER = "https://tracker.zz.hgdev.com"


def make_env(handler):
    api = FakeAPI(buckets=STAGES)
    client = httpx.Client(transport=httpx.MockTransport(handler))
    notifier = WebhookNotifier(WEBHOOK, tracker_url=TRACKER, client=client)
    wf = Workflow(api, project_id=3, notifier=notifier)
    task = api.add_task("сломан деплой", "Design", assignee=api.me_user)
    return api, wf, task


def capture_into(captured):
    def handler(request):
        captured.append(request)
        return httpx.Response(200, text="ok")
    return handler


def test_call_human_posts_one_slack_message():
    captured = []
    api, wf, t = make_env(capture_into(captured))
    res = wf.call_human(t["id"], question="какой из двух вариантов деплоя выбрать?")

    assert len(captured) == 1
    req = captured[0]
    assert str(req.url) == WEBHOOK
    assert req.method == "POST"
    payload = json.loads(req.content)
    assert set(payload) == {"text"}  # the Slack-minimal shape, nothing extra
    text = payload["text"]
    assert f"({t['id']})" in text                        # the ref carries the global id
    assert "сломан деплой" in text                       # title
    assert "какой из двух вариантов деплоя выбрать?" in text  # the question itself
    assert f"{TRACKER}/tasks/{t['id']}" in text          # frontend deep-link, no /api/v1
    assert res["notified"] is True
    assert res["moved_to"] == "Your Call"
    # the pre-existing behavior is untouched
    assert api.stage_of(t["id"]) == "Your Call"
    assert any(c.startswith("[нужен человек]") for c in api.comments_text(t["id"]))


def test_no_webhook_configured_keeps_result_shape():
    """Unset URL = feature off: default Workflow has no notifier, the result has NO
    'notified' key and nothing even tries the network — zero behavior change."""
    api = FakeAPI(buckets=STAGES)
    wf = Workflow(api, project_id=3)
    t = api.add_task("job", "Design", assignee=api.me_user)
    res = wf.call_human(t["id"], question="вопрос?")
    assert wf.notifier is None
    assert "notified" not in res
    assert api.stage_of(t["id"]) == "Your Call"


@pytest.mark.parametrize("failure", [
    lambda req: httpx.Response(500, text="gateway down"),
    lambda req: (_ for _ in ()).throw(httpx.ConnectError("refused")),
    lambda req: (_ for _ in ()).throw(httpx.ReadTimeout("too slow")),
])
def test_call_human_survives_webhook_failure(failure, capsys):
    """A down/misconfigured gateway costs the PING, never the parked question: the card is
    already in Your Call with its comment, the tool result still reports the successful park
    (notified:false), and the swallowed failure leaves ONE stderr note — never stdout."""
    def handler(request):
        return failure(request)

    api, wf, t = make_env(handler)
    res = wf.call_human(t["id"], question="жив ли гейт?")
    assert res["moved_to"] == "Your Call"
    assert res["notified"] is False
    assert api.stage_of(t["id"]) == "Your Call"
    assert any(c.startswith("[нужен человек]") for c in api.comments_text(t["id"]))
    out, err = capsys.readouterr()
    assert out == ""          # stdout is the MCP protocol channel — must stay untouched
    assert "webhook" in err   # the operator-facing trace of the swallowed failure


def test_refused_call_human_pings_nothing():
    """The ping fires only AFTER a successful park: every gate refusal (empty question,
    wrong stage, not my task) must leave the webhook silent."""
    captured = []
    api, wf, t = make_env(capture_into(captured))

    with pytest.raises(WorkflowError, match="question"):
        wf.call_human(t["id"], question="   ")

    other = api.add_task("чужая", "Design", assignee={"id": 99, "username": "other"})
    with pytest.raises(WorkflowError, match="claim"):
        wf.call_human(other["id"], question="можно?")

    parked = api.add_task("уже в ревью", "Review", assignee=api.me_user)
    with pytest.raises(WorkflowError, match="Design/Build"):
        wf.call_human(parked["id"], question="можно?")

    assert captured == []


def test_task_link_strips_api_suffix_and_trailing_slash():
    """config's url may carry the API suffix and/or a trailing slash; the FRONTEND deep-link
    must carry neither (…/api/v1/tasks/N is a 404 for a human)."""
    for base in (TRACKER, TRACKER + "/", TRACKER + "/api/v1", TRACKER + "/api/v1/"):
        n = WebhookNotifier(WEBHOOK, tracker_url=base)
        assert n.task_link(252) == f"{TRACKER}/tasks/252", base
