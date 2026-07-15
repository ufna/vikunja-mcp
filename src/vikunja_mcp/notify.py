"""Best-effort Slack-webhook ping for cards parked in Your Call (#252).

A card lands in Your Call when an agent needs a human decision (call_human) — but the human
used to discover it only by looking at the board. This closes the gap: call_human, having
parked the card, POSTs ONE Slack-compatible incoming-webhook message — {"text": ...}, the
minimal shape every Slack-compatible gateway accepts — to the URL from config
(VIKUNJA_NOTIFY_WEBHOOK; a secret of the token's class: env layers only, never the committed
toml — whoever holds the URL can post into the humans' channel). No URL configured -> no
notifier is built at all and call_human behaves bit-for-bit as before.

Delivery is BEST-EFFORT BY CONTRACT, split across two layers on purpose:
  * this notifier RAISES on any failure (non-2xx, timeout, DNS, refused connection) — it never
    guesses what a failure means;
  * call_human — the single best-effort boundary — swallows it with a one-line stderr note
    (the #134/#135 contract), so a down/misconfigured gateway costs the PING, never the parked
    question, never the tool result, and never a byte on stdout (the MCP protocol channel).
One attempt, short timeout, no retries: a human ping is stale the moment it is late, and
call_human's result must not stall behind a dead gateway (api.py's retry ladder is for the
tracker itself, deliberately NOT mirrored here)."""
import httpx

# A ping, not a conversation: long enough for a healthy gateway, short enough that a dead one
# can't meaningfully delay call_human (which has already parked the card when this fires).
_TIMEOUT_SECONDS = 5.0


class WebhookNotifier:
    def __init__(self, webhook_url: str, tracker_url: str, client: httpx.Client | None = None):
        # `client` is an injection seam for tests (httpx.MockTransport), same pattern as
        # VikunjaAPI; production builds its own. The client lives as long as the notifier —
        # the server process — mirroring the API client's lifecycle.
        self.webhook_url = webhook_url
        self.tracker_url = tracker_url
        self._client = client or httpx.Client(timeout=_TIMEOUT_SECONDS)

    def task_link(self, task_id: int) -> str:
        """Frontend deep-link to the card. config's url is also the API base, so it may carry
        a trailing slash or the /api/v1 suffix — the FRONTEND link must carry neither
        (…/api/v1/tasks/N is a 404 for a human)."""
        base = self.tracker_url.rstrip("/")
        if base.endswith("/api/v1"):
            base = base[: -len("/api/v1")]
        return f"{base}/tasks/{task_id}"

    def your_call(self, ref: str, title: str, question: str, task_id: int) -> None:
        """POST the one-message ping: what a human needs at a glance — the searchable ref,
        the title, the question itself, and the deep-link to answer on. Raises on any
        failure; the CALLER (call_human) is the best-effort boundary that swallows it."""
        text = f"[Your Call] {ref} — {title}\n{question}\n{self.task_link(task_id)}"
        self._client.post(self.webhook_url, json={"text": text}).raise_for_status()
