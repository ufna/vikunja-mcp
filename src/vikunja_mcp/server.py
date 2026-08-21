"""stdio MCP server. Gates live in Workflow; this is thin wiring and clear errors."""
import sys
from functools import wraps

import httpx

from vikunja_mcp import __version__
from vikunja_mcp.api import VikunjaAPI, VikunjaError, canonical_base_url
from vikunja_mcp.config import ConfigError, load_config
from vikunja_mcp.notify import WebhookNotifier
from vikunja_mcp.workflow import Workflow, WorkflowError

# The MCP SDK is imported LAZILY — inside _server(), never at module scope (tracker #521).
# EVERY subcommand routes through main(), but only ONE of them speaks MCP: `claimable`,
# `workspace`, `setup`, `install-skill` and `--version` used to pay the whole SDK import
# (107 mcp-rooted modules, dragging in pydantic / opentelemetry / httpx2 / truststore) for
# a protocol they never touch. It hurts `claimable` most: hgdev-acp's repo-agent loop spawns
# it on EVERY poll tick as its pre-launch idle check, and it does nothing but
# Workflow.next_task(). Measured best-of-10 on one machine (mcp 2.0.0, py3.12):
#   python -c "import vikunja_mcp.server"   0.511s -> 0.077s   (-0.43s, 6.6x)
#   vikunja-mcp --version                   0.509s -> 0.076s
#   vikunja-mcp workspace (usage)           0.520s -> 0.082s
#   vikunja-mcp claimable (end-to-end)      1.867s -> 1.598s   (the rest is tracker round-trips)
# A second, initially-unnoticed win: MCPServer.__init__ calls logging.basicConfig(level=INFO) —
# a PROCESS-WIDE side effect the old module-level construction inflicted on every subcommand, so
# `claimable` used to emit one httpx "HTTP Request: ..." INFO line per tracker call to stderr.
# Building the server only on the stdio path drops those (stdout was and stays the ONE JSON line;
# verified byte-for-byte). The stdio server still configures logging exactly as before, just at
# build time instead of import time — nothing in between logs through `logging`.
# The price of laziness: `@mcp.tool()` cannot decorate anything at import time, so tool
# REGISTRATION is deferred too — _mcp_tool remembers the function, _server() registers the
# collected list onto the server it builds. Definition order is preserved, so the wire order
# of tools/list is unchanged.
_DEFERRED_TOOLS: list = []


def _mcp_tool(fn):
    """Mark a function as an MCP tool WITHOUT importing the SDK — a drop-in for the old
    `@mcp.tool()`, registered later by _server(). The SAME object reaches the SDK as before
    (the _tool wrapper, carrying its functools.wraps metadata), so the generated schema,
    name and description are byte-identical to the eager-registration ones."""
    _DEFERRED_TOOLS.append(fn)
    return fn


_mcp_server = None


def _server():
    """The stdio MCPServer — built ON FIRST USE and cached. The lone import site of the MCP
    SDK, which is precisely what keeps it off every non-MCP CLI path (see above)."""
    global _mcp_server
    if _mcp_server is None:
        from mcp.server import MCPServer

        # version= is not cosmetic: MCPServer defaults it to "", where FastMCP used to report
        # the SDK's own version — so a client's server list would show a blank. Report OURS,
        # what a human debugging a rollout of the moving `stable` channel needs to see.
        _mcp_server = MCPServer("vikunja-tracker", version=__version__)
        for fn in _DEFERRED_TOOLS:
            _mcp_server.tool()(fn)
        _forbid_unknown_tool_arguments(_mcp_server)
    return _mcp_server


def _forbid_unknown_tool_arguments(server) -> None:
    """Make a MISSPELLED parameter name a loud, named refusal instead of a silent drop (#720).

    WHAT IS BROKEN WITHOUT THIS. Measured on the real `MCPServer` over real stdio with a raw
    JSON-RPC client (so a key can be genuinely ABSENT rather than `None`):
    `advance(to='review', wroklog=<7000 chars>, evidence=<40>)` came back `isError=False` with
    "Review needs a report … worklog — arrived as null" — BYTE-IDENTICAL to the same call with
    the key omitted and to one passing `worklog=None`. The 7 KB the agent wrote was dropped by
    pydantic before the function ran, and the agent was told to write a report it had already
    written. Contrast, from the same run: a wrong TYPE (`worklog=12345`) and a missing REQUIRED
    key (`to`) are both caught loudly and BY NAME. Only an unknown key was silent.

    That matters beyond one typo. A dropped key and a lost argument arrive identically, and #657
    established the loss is NOT ours to fix (nothing truncates below 4-8 MiB anywhere in this
    server or its transport) — but the typo IS ours, and closing it shrinks that ambiguous class
    to the half we cannot control.

    WHY HERE AND NOT IN `_tool`. Not a preference: the SDK calls `fn(**validated.model_dump…)`,
    so pydantic discards the unknown key BEFORE the function body exists to see it. Measured —
    inside the tool, `worklog` is simply None and `wroklog` leaves no trace at all. A decorator
    cannot catch what never reaches it.

    WHY BOTH LINES. `extra="forbid"` changes what the SERVER accepts; `tool.parameters` is the
    schema the server PUBLISHES, and it is frozen at registration. Measured: before the mutation
    the two agree exactly, after it they differ by the single key `additionalProperties: false`,
    and the reassignment does reach `list_tools()`. Setting only the first would refuse calls
    that the advertised schema still says are legal.

    WHAT A PART-WAY FAILURE ACTUALLY LEAVES BEHIND (#778), because the loop is not atomic and an
    earlier draft of the stderr line below described a state that CANNOT HAPPEN. Measured by
    injecting the failure into the 3rd tool of a freshly registered 12-tool server and reading
    BOTH objects rather than one — `test_a_part_way_forbid_failure_leaves_a_REACHABLE_state`
    constructs both modes against THIS function, so the rows below are pinned, not recorded:

      failed at `model_rebuild`    the tool it was ON still ACCEPTS extras, and its
                                   `tool.parameters` has no `additionalProperties` at all — so
                                   the published schema still ALLOWS them too. Both ends
                                   permissive: consistent, and exactly pre-#720 for that tool.
      failed at `tool.parameters`  the tool it was ON already REFUSES extras while its
                                   `tool.parameters` still ALLOWS them — the MIRROR of the
                                   sentence that used to stand here, and the asymmetry WHY BOTH
                                   LINES warns about, pointing the other way.

    Neither mode produces the retired state — accepting extras while ADVERTISING a refusal —
    and the reason is structural rather than lucky: the assignment is the LAST statement, so
    `tool.parameters` can only ever lag the validator, never lead it. Tools the loop had already
    FINISHED keep refusing (measured); tools it never reached are fully pre-#720 on both ends.

    AND THE ROOT OF THE RETRACTED CLAIM IS A REFERENT SWAP WORTH NAMING, since three readings in
    a row went the same way. On the tool that failed at `model_rebuild`,
    `arg_model.model_json_schema()` ALREADY reports `additionalProperties: false` — step 1 set
    `model_config`, and schema generation reads it — while `tool.parameters`, the schema this
    server PUBLISHES, is untouched. So the retired clause was true of the object nobody publishes
    and false of the one that is. Measure `tool.parameters`; `model_json_schema()` answers a
    different question.

    BEST-EFFORT BY DESIGN. `_tool_manager` is private and `mcp` is pinned `>=2,<3`, while the
    `stable` channel re-resolves dependencies and ignores the lock — so a minor SDK release can
    move this. A failure here degrades to the OLD behaviour (a silent drop) and writes one line
    to stderr; it never raises, because a stdio server that refuses to start is worse for every
    consumer than one that keeps the ambiguity. stderr ONLY, and `sys.stderr is None` (fd 2
    closed at exec) is checked EXPLICITLY rather than caught, because `print(file=None)` writes
    to STDOUT and raises nothing — the same trap CLAUDE.md records for `claimable_cmd`, where no
    `except` can see it. What that costs was measured, and the measurement SPLITS — which is the
    part an earlier pass here got backwards, nearly retiring a true sentence as an overstatement.
    A complete LINE is survivable: the real mcp 2.0 client logs `Failed to parse JSONRPC message
    from server` and keeps going, `initialize` and a `call_tool` after it both succeeding. A
    single BYTE with no newline is NOT: `initialize` never returns and the session hangs. So "a
    byte on stdout corrupts the protocol" is TRUE of its own subject, and only the LINE case is
    the mild one. `print` appends the newline, so this code can emit only the mild case — a
    property of the call, not a licence to write there. Both measured AT STARTUP, where this one
    lands (`_server()` runs before `.run()`); mid-session was not tried, and the log line is no
    help in telling the two apart — it appears in the hung case too.
    That the gate is actually ON is pinned by tests rather than assumed.
    """
    try:
        tools = getattr(server._tool_manager, "_tools", None)
        if not tools:
            raise RuntimeError("no tool registry on the tool manager")
        for tool in tools.values():
            arg_model = tool.fn_metadata.arg_model
            arg_model.model_config["extra"] = "forbid"
            arg_model.model_rebuild(force=True)
            tool.parameters = arg_model.model_json_schema()
    except Exception as exc:                      # noqa: BLE001 — see BEST-EFFORT above
        if sys.stderr is None:                    # print(file=None) would go to STDOUT
            return
        try:
            print(
                "vikunja-mcp: could not forbid unknown tool arguments "
                f"({exc.__class__.__name__}: {exc}); a misspelled parameter name will be "
                "dropped silently, as before #720 — on every tool, or, if this failed "
                "part-way, on every tool it had not FINISHED. The one it was ON is left in "
                "one of two states (#778, measured): it still accepts extras AND its "
                "published schema still allows them, or it already REFUSES them while its "
                "published schema still allows them. Never the reverse — the published "
                "schema is assigned last, so it cannot deny ahead of validation",
                file=sys.stderr,
            )
        except Exception:
            pass


def __getattr__(name: str):
    """PEP 562: keep `server.mcp` working, now as a lazily-built attribute — tests and any
    consumer doing `from vikunja_mcp.server import mcp` still get the real server, built on
    the spot. Code INSIDE this module must call _server() instead: a module-level __getattr__
    is not consulted for a plain global-name lookup, only for attribute access on the module."""
    if name == "mcp":
        return _server()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# A 401 from Vikunja is a CREDENTIAL problem, not a transient one. TWO traps here, both learned
# the expensive way (tracker #140):
#  1. Vikunja returns the SAME 401 for an invalid/expired/malformed token AND for a valid token
#     missing a required permission group — body {"code":11,"message":"missing, malformed,
#     expired or otherwise invalid token provided"} in BOTH cases (verified against real 2.3.0:
#     a scoped token lacking `other:user`/`projects` 401s those endpoints BYTE-FOR-BYTE like a
#     garbage token, same code 11, same headers). So the body's `code` CANNOT tell "expired"
#     from "scope gap" — do NOT branch on it; a message that confidently names one cause is
#     wrong half the time. The guidance below OWNS BOTH possibilities.
#  2. The old text asserted "a RESTART will NOT help: a token's scopes are fixed at mint" — the
#     exact OPPOSITE of the truth when the token was merely ROTATED (a re-mint invalidates the
#     old value, which this long-lived server had cached). That confidently-wrong advice
#     stranded a real task mid-Build. _tool now reloads .vikunja-mcp.env and retries once on a
#     401 (rotation self-heals); a 401 that still surfaces means the on-disk token is genuinely
#     rejected, and the fix is the token in the FILE — a restart only re-reads what we reloaded.
_AUTH_GUIDANCE = {
    401: (
        "Vikunja API 401 (unauthorized) — the token is REJECTED. Vikunja sends this same "
        "`code 11` body for an invalid/expired/malformed token AND for a valid token that is "
        "MISSING a required permission group, so the two cannot be told apart from the response. "
        "On a 401 this server re-reads .vikunja-mcp.env and, if the token there was ROTATED, "
        "retries once with it — so a rotation self-heals; seeing this means the token in "
        ".vikunja-mcp.env is STILL rejected (unchanged, or the new value is also bad). "
        "Remedy: put a current, valid token in .vikunja-mcp.env, minted WITH the permission "
        "groups `other:user` and `projects:views_buckets` (the latter gates every stage "
        "transition — advance/claim/review_task move kanban buckets); if you just re-minted, "
        "confirm the new value actually landed in the file. A /mcp reconnect or full RESTART "
        "only re-reads the same file the server already reloaded, so it will NOT help until the "
        "token in that file is valid"
    ),
    403: (
        "Vikunja API 403 (forbidden) — the token authenticates but its user lacks "
        "permission on this project/resource (e.g. a read-only share). Not a scope or "
        "restart problem: grant the user write access, or use an agent-owned / "
        "admin-shared project"
    ),
}

_workflow: Workflow | None = None
# The credential + TARGET baked into the cached _workflow, captured when the server first built it.
#  * _workflow_token — the #140-rework write-safety gate: on a 401 the retry fires ONLY if the token
#    freshly read from .vikunja-mcp.env DIFFERS from this (see _tool / _reload_workflow_from_disk).
#  * _workflow_url / _workflow_project_id — the #148 REPOINT gate: a rotation may swap the credential,
#    but it must NOT silently adopt a changed host/project mid-session (that would hand the agent
#    another project's queue); a reload that finds either changed REFUSES instead of repointing.
_workflow_token: str | None = None
_workflow_url: str | None = None
_workflow_project_id: int | None = None


def _reset_workflow_cache() -> None:
    global _workflow, _workflow_token, _workflow_url, _workflow_project_id
    _workflow = None
    _workflow_token = None
    _workflow_url = None
    _workflow_project_id = None


def _build_workflow(cfg) -> Workflow:
    # Your-Call webhook ping (#252): built only when VIKUNJA_NOTIFY_WEBHOOK is configured —
    # unset means call_human carries no notifier at all (feature off, zero behavior change).
    notifier = (
        WebhookNotifier(cfg.notify_webhook, tracker_url=cfg.url)
        if cfg.notify_webhook else None
    )
    # require_review_independence is wired HERE and — since VMCP-295 (1169) — in claimable_cmd's
    # Workflow too. It used to be wired here ONLY, justified by "that one runs `next_task` and
    # nothing else, so the flag could never be consulted there — passing it would be dead wiring
    # on the one path that must stay read-only and cheap". TRUE at #37, STALE from #991 on, and
    # refuted on BOTH halves. Not dead: the authorship skip in next_task's review-offering branch
    # was already there and UNCONDITIONAL, and #991 made it conditional on the flag — so from that
    # card on, `next_task` is exactly a caller of it. Measured on an identical FakeAPI board (one
    # card driven claim -> build -> review by ONE identity), `classify_next(wf.next_task())`
    # answers {"claimable": true, "kind": "review"} with the flag false and {"claimable": false,
    # "kind": "empty"} with it true — the two sides DISAGREE, which is the defect; what the
    # flag-on answer is instead depends on the board (empty, queue and starving all measured).
    # Not a cost either, about the GUARD: next_task resolves `my_id` whatever the flag says, so
    # no request is added, and when it FIRES it skips that card's `comments()` fetch — the total
    # is the board's business, not the guard's. THE RULE THAT REPLACES THAT SENTENCE: a Config
    # key `Workflow` READS on a given path is wired at EVERY site that builds one (three here —
    # this one, claimable_cmd, and workspace_cmd's), because `claimable`'s stated property is a
    # verdict with ZERO drift from the agent's own, and a kwarg present on one side only IS that
    # drift. Between THIS site and claimable_cmd's the one legitimate asymmetry left is
    # `notify_webhook` -> `notifier`: `call_human` alone touches it, and that path calls
    # `next_task` alone. That sentence stopped DESCRIBING the tree within two cards — #1179
    # wired `siblings` here and not there — without going false, since it quantifies over
    # LEGITIMATE asymmetries and an accidental omission is not one; it simply left a reader
    # counting one exception where two keys were absent. Which is why it is no longer prose
    # alone: the gate comparing the two keyword sets, permitting only what it declares, is
    # tests/unit/test_workflow_construction_parity.py.
    # Full accounting in docs/dossier/claimable.md and config.md.
    return Workflow(
        VikunjaAPI(cfg.url, cfg.token), cfg.project_id,
        enforce_single_wip=cfg.enforce_single_wip,
        wip_limit=cfg.wip_limit,
        notifier=notifier,
        require_review_independence=cfg.require_review_independence,
        language=cfg.language,
        siblings=cfg.siblings,
    )


def _remember_session(cfg) -> None:
    """Record the credential + target of the currently-cached Workflow — the baseline a later 401
    reload compares a fresh config against (token change = rotation; url/project change = repoint)."""
    global _workflow_token, _workflow_url, _workflow_project_id
    _workflow_token = cfg.token
    _workflow_url = cfg.url
    _workflow_project_id = cfg.project_id


def _wf() -> Workflow:
    global _workflow
    if _workflow is None:
        cfg = load_config()
        _workflow = _build_workflow(cfg)
        _remember_session(cfg)
    return _workflow


def _reload_workflow_from_disk() -> bool:
    """Rebuild the cached Workflow from a FRESH read of config to pick up a token ROTATED in
    .vikunja-mcp.env while the server runs — but ONLY when that token actually CHANGED, and ONLY
    when the rotation does not also move the host/project. Returns True (and swaps in the new
    Workflow) on a clean rotation; returns False when the token is unchanged, or when config is now
    missing / unreadable / malformed (the cached Workflow is left untouched either way).

    Two gates on the fresh config:
      * changed-token (#140 rework): _tool retries the WHOLE tool on a 401, and a tool is several
        HTTP requests. On a scope-gap 401 (a valid token lacking one permission group) the EARLIER
        requests already wrote before a LATER one 401'd, so a blind retry duplicates them (the
        reviewer saw a [worklog] comment and a filed card land twice on real 2.3.0). A scope gap
        never changes the on-disk token, so gating the retry on a token change skips it for a scope
        gap (no duplicate) yet still fires it for a real rotation (recovery lives — a rotation
        replaces the whole dead token, so the tool's FIRST request 401'd with nothing written yet).
      * changed-target (#148): load_config() returns the WHOLE Config, so a rotation that ALSO moved
        url or project_id would otherwise rebuild onto a DIFFERENT host/project with no error — the
        next next_task would hand back another project's queue (four agent identities share this
        config shape on one tracker, so a mass re-mint mixing up project_id is a realistic human
        slip, and the failure is SILENT). So when the token changed but url or project_id no longer
        matches the running session, REFUSE: raise ConfigError with an actionable "restart the
        server" message (caught by _tool, surfaced, NOT retried) rather than silently repoint. The
        url is compared CANONICALLY (canonical_base_url, #154) so a rotation whose url differs only
        cosmetically — trailing slash, scheme/host case — is NOT a repoint and self-heals; only a
        genuinely different scheme value/host/port/path is refused.

    Never raises for a config-read or Workflow-construction failure — those degrade to "no reload"
    (return False) rather than crashing the stdio server (same best-effort posture as
    _self_heal_installed_artifacts). The ONLY deliberate raise is the #148 repoint refusal above."""
    global _workflow
    try:
        cfg = load_config()
    except Exception:
        return False
    if cfg.token == _workflow_token:
        return False                # same credential -> a scope gap, not a rotation -> no retry
    if _workflow_token is not None and (
        canonical_base_url(cfg.url) != canonical_base_url(_workflow_url)
        or cfg.project_id != _workflow_project_id
    ):
        # #148: the token rotated, but so did the host/project. The url is compared CANONICALLY
        # (canonical_base_url — the client's own normalizer, #154) so a cosmetic-only difference
        # (trailing slash, scheme/host case) is NOT a repoint and self-heals; only a genuinely
        # different scheme value / host / port / path is refused. A rotation reloads the CREDENTIAL;
        # it must not silently REPOINT the session onto another project/host. Refuse loudly.
        raise ConfigError(
            "Vikunja config changed the project/host MID-SESSION and the server will NOT silently "
            f"repoint: it started on project {_workflow_project_id} at {_workflow_url}, but the "
            f"config now reads project {cfg.project_id} at {cfg.url}. A token rotation reloads only "
            "the credential; adopting a different project or host would hand you another project's "
            "queue. If the change is intended, RESTART the MCP server to adopt it; if not, revert "
            "project_id/url in .vikunja-mcp.toml / .vikunja-mcp.env. The failing call was NOT "
            "retried."
        )
    try:
        _workflow = _build_workflow(cfg)
    except Exception:
        return False                # construction failure degrades to "no reload", never a crash
    _remember_session(cfg)
    return True


def _error_result(e: Exception) -> dict:
    """Turn a caught tool exception into an {"error": ...} result — never re-raise, so the stdio
    server can't crash. Shared by the first attempt and the single post-401 retry."""
    if isinstance(e, (WorkflowError, ConfigError)):
        return {"error": str(e)}
    if isinstance(e, VikunjaError):
        guidance = _AUTH_GUIDANCE.get(e.status)
        if guidance:
            return {"error": f"{guidance} [server said: {e.message}]"}
        return {"error": f"Vikunja API: {e.status} {e.message}"}
    return {
        "error": f"tracker unreachable ({e.__class__.__name__}): "
        f"check the url in .vikunja-mcp.toml and the VPN"
    }


def _tool(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except (WorkflowError, ConfigError, VikunjaError, httpx.HTTPError) as e:
            # A 401 may be a ROTATED token, not a permanent fault: this long-lived server caches
            # the token from first use, but a human can re-mint it (which INVALIDATES the old
            # value) and rewrite .vikunja-mcp.env. Reload config and retry the SAME call ONCE, so
            # /loop survives a rotation without a restart (tracker #140).
            # Retry ONLY when the reloaded token CHANGED (the gate is in _reload_workflow_from_disk).
            # Why not always: _tool retries the WHOLE tool, and a tool is several HTTP requests. A
            # 401 is rejected at auth before ITS OWN handler runs — but on a scope gap (a valid
            # token lacking one group) an EARLIER request already wrote before a LATER one 401'd,
            # so a blind whole-tool retry re-runs that write (the #140 review saw a [worklog]
            # comment and a filed card duplicated on a real container). A scope gap leaves the
            # token unchanged; a rotation replaces the whole dead token, so its FIRST request 401s
            # with nothing written yet — so gating on a token change retries the safe case and
            # skips the duplicating one. (Residual, accepted by the review: a token rotated
            # MID-tool — alive for an early write, then replaced before a later request 401s —
            # would still re-run the early write; that needs a human re-mint inside the sub-second
            # gap between two requests of one call. Fully closing it means per-request retry in
            # api.py, deferred as the bigger change.) Guard hard: only status 401, exactly ONE
            # retry, outcome FINAL — a second 401 surfaces the guidance, never recursing. And a
            # rotation that ALSO moved host/project (#148) is REFUSED, not retried: the reload raises
            # ConfigError, which we surface as-is rather than repointing onto another project's queue.
            if isinstance(e, VikunjaError) and e.status == 401:
                try:
                    reloaded = _reload_workflow_from_disk()
                except ConfigError as repoint:
                    return _error_result(repoint)     # #148: mid-session repoint refusal, no retry
                if reloaded:
                    try:
                        return fn(*args, **kwargs)
                    except (WorkflowError, ConfigError, VikunjaError, httpx.HTTPError) as retry_err:
                        return _error_result(retry_err)
            return _error_result(e)

    return wrapper


@_mcp_tool
@_tool
def next_task(exclude: list[int] | None = None) -> dict:
    """What to do next, in order: (1) YOUR active task (Design/Build, incl. one bounced
    back from Your Call), (2) a task in Queue assigned to you, (3) a task in Review
    awaiting independent review — ANY card except an epic container, with no fresh verdict,
    YOUR OWN INCLUDED (review_kind names the rubric: 'bug' or 'change'), (4) the top FREE
    task in Queue. Your own is included because this is NOT an authorship check: in a solo
    setup one token is the whole fleet, so every card in Review is yours and a filter would
    offer nothing at all. Independence rests on reviewing from a FRESH CONTEXT — a sibling
    agent, never the one that wrote the code — which nothing server-side can observe. Only
    a repo setting require_review_independence = true skips your own here, and there it
    matches review_task, which would refuse your verdict anyway. Cards that are NOT yours
    are offered first while any remain. A card leaves this lane only when a verdict lands,
    so a dispatched review belongs in `exclude` for the rest of the tick — otherwise the
    same card is handed out again and two reviewers land on one piece of work.
    Never hands out a task assigned to someone else — those are "for humans".
    Leaves Backlog, blocked, and epic containers (label epic — a container, not a unit of
    work) alone. Do not CLAIM more than wip.limit at once — the repo toml's wip_limit where it
    sets one (or 1 for the legacy enforce_single_wip = true), else THREE by default; the `wip`
    payload, never a guess, says how many, and it is always a number (see PARALLEL DRAIN below).
    "Claim", not "hold": that number gates the ONE transition `claim` and is NOT an invariant on
    the active count, so wip.active may legitimately EXCEED wip.limit and this very call reports
    it (4/3 is a real, correct state — rework re-enters Build without passing the gate). Read it
    as rework to drain, never as board corruption; `claim`'s docstring has the paths.
    Among your active tasks, one that is a predecessor of another of your active tasks is
    handed back first (finish the unblocking rework before its successor), overriding priority.
    A free task whose predecessor
    (a follows/blocked link, e.g. an ordered-epic step) is still unfinished (below Review)
    is skipped, not offered. If the Queue is non-empty but EVERY free task is so gated, the
    result is a DISTINGUISHABLE starving-tail signal (task:null PLUS starving:true,
    waiting_count, waiting[], needs_retriage) — NOT the empty-queue result: don't idle on it,
    surface the stalled chain to the human (needs_retriage means a head was sent back to
    Backlog and must be re-triaged). If those gated tasks form a predecessor CYCLE (a hand-made
    follows/blocked loop, e.g. A follows B and B follows A — nothing claimable and it can't
    self-unblock), the result is instead a distinct cycle signal (task:null PLUS cycle:true and
    cycle_tasks naming the loop): this is NOT sleepable — surface it via call_human so a human
    breaks the cycle (removes one link in the web UI). A genuinely empty queue is still task:null
    with 'the queue is empty'.

    Every result that carries a task also carries `stage` — the stage it was found in. THAT is
    what decides whether to claim, not `resume`: stage 'Queue' needs claim(task_id) (it moves it
    into Design, and finishes a partial claim) whether resume is false (fresh) or true (assigned
    to you but never moved); stage 'Design'/'Build' is already yours, so claim would refuse;
    stage 'Review' is a review offer, not work to claim.

    PARALLEL DRAIN: pass `exclude` = the ids of tasks you ALREADY have a live agent on, so
    they are not handed back and dispatched twice. They still occupy their WIP slot. Every
    result carries wip: {active, limit, free} — limit and free are always numbers, never null.
    free == 0 comes back as task:null PLUS wip_saturated:true — that means WAIT for an agent to
    return, NOT that the queue is empty. But that signal exists only once `exclude` is COMPLETE:
    your own active tasks are offered (branch 1) BEFORE the slot check, so an unexpected resume
    at free:0 with no wip_saturated means your exclude is short, not that the board changed —
    check your exclude, not the board (the resume's own note says so at the moment it happens).
    """
    return _wf().next_task(exclude=exclude)


@_mcp_tool
@_tool
def claim(task_id: int) -> dict:
    """Take a task from Queue: assigns you and moves it to Design. You may take free
    tasks or ones already assigned to you; one assigned to someone else is "for humans"
    and claim won't hand it over. Also refused outside Queue and on a lost race (call
    next_task then). An epic container (label epic) is refused too — it's a container, not a
    unit of work; its evidence lives in its children, so work on those. The WIP gate is ALWAYS
    on: N is the repo toml's wip_limit if it sets one, else 1 for the legacy
    enforce_single_wip = true, else the default of 3. Claim refuses once you already hold N
    active Design/Build tasks — "WIP limit reached (N/N) — you already hold #… Finish one
    (advance to Review) or return_task it before claiming another". next_task's `wip` payload
    reports the same limit and how many slots are free.
    The gate guards THIS transition, and is NOT an invariant on the active count: a card
    re-enters Build WITHOUT passing it when review_task(verdict='needs_work') bounces it back
    from Review, or when a human moves it out of Your Call (or hand-places an assigned card in
    Design/Build), or when the toml's wip_limit is lowered while tasks are in flight. So
    wip.active may legitimately EXCEED wip.limit — 4/3 is a real, correct state, not board
    corruption: it is rework, which by design outranks a fresh claim (refusing it would strand
    reviewed work). Claim keeps refusing while over budget and reports the true count
    ("WIP limit reached (4/3)"), and the overshoot clears itself when the rework reaches
    Review."""
    return _wf().claim(task_id)


@_mcp_tool
@_tool
def get_task(task_id: int) -> dict:
    """Task dossier: full (untruncated) description, stage, assignees, labels, related
    (linked tasks by relation kind), attachments (metadata only — {id, name, mime, size};
    a card may be nothing but a screenshot, so CHECK this and download_attachment it rather
    than guessing from an empty description) and all comments."""
    return _wf().get_task(task_id)


@_mcp_tool
@_tool
def download_attachment(task_id: int, attachment_id: int) -> dict:
    """Download a task attachment to a temp file and return its PATH — then Read the path to
    view it (a PNG/JPG renders visually; text/PDF opens as text). The path is returned instead
    of base64 so the file never bloats the context. attachment_id is the `id` from get_task's
    attachments[] (not the filename). Errors are actionable: a wrong id lists the task's real
    attachments; an oversized file is refused with its size before downloading."""
    return _wf().download_attachment(task_id, attachment_id)


@_mcp_tool
@_tool
def attach_file(task_id: int, path: str, note: str | None = None) -> dict:
    """Attach a LOCAL file — typically a SCREENSHOT of the finished work — to a task, so a human
    and the independent reviewer can SEE a visually-verifiable result instead of trusting 'done'.
    WHEN: your change is visually verifiable (a UI, a rendered page/chart, a generated image, a
    board layout) and you already have a screenshot from verifying it — attach it, then cite it in
    your advance(to='review') worklog as evidence beside the commit sha. NOT for every task: a
    change with no visual surface (a lockfile, a refactor, config) has nothing to show, so don't
    force it. `path` is a local file (the screenshot you produced); its basename becomes the
    attachment name, the MIME is inferred from the extension. Every successful upload JOURNALS
    itself into the task's comments as `[attach] <name> (<mime>, <size>)` — pass `note` (one line
    on WHAT the file shows, e.g. 'the board after reconcile') so the human reading the journal
    sees why it's there, and do NOT post a separate comment about the upload (it would duplicate
    the journal). The note is YOUR sentence, not the product's: nothing here rewrites it and the
    project's `language` key only INSTRUCTS what to write it in — the example is English because
    this description is. This is standalone — it does NOT move the task; a failed upload
    never affects a stage transition, and a failed journal comment never fails the upload
    (the result then has journal_comment=false — the file IS on the card, don't
    re-upload). Actionable errors: a missing path, a directory, or an oversized file
    (>25MB) is refused with the reason; a 401 means the token lacks the
    tasks_attachments:create scope and a human must add that op."""
    return _wf().attach_file(task_id, path, note=note)


@_mcp_tool
@_tool
def comment(task_id: int, text: str) -> dict:
    """A progress note: findings, decisions ('picked X over Y because Z')."""
    return _wf().comment(task_id, text)


@_mcp_tool
@_tool
def advance(
    task_id: int, to: str,
    spec: str | None = None, worklog: str | None = None, evidence: str | None = None,
    root_cause: str | None = None,
) -> dict:
    """Advance YOUR task. to='build' requires spec (approach/design). to='review'
    requires a WORK REPORT: worklog (what was done and how it was verified — by running,
    not by reading code) + evidence (commit/PR/verification output); for bug fixes
    root_cause is MANDATORY — the cause of the bug (why it happened), not the symptom;
    since #718 that word means a GATE, not a wish: a card labelled `bug` is refused without
    it, by the same disjunctive check that guards worklog and evidence (an epic container is
    exempt — nobody reviews it, so there is no one to owe a cause).
    The report is posted as a comment for the reviewer to read. There is no transition
    to Done — a human moves it to Done after review.
    EVERY task reaching Review returns review_needed=True + review_kind ('bug'|'change')
    so the orchestrator dispatches an independent reviewer — EXCEPT an epic container
    (label epic), which has no code of its own (its evidence lives in its children).
    to='review' is also LATCHED while any predecessor (a follows/blocked link, e.g. an
    ordered-epic step) is still below Review: if a predecessor was bounced Review→Build,
    finish its rework back to Review before this successor may advance (the refusal names it).
    IF A REFUSAL SAYS YOUR REPORT IS MISSING WHEN YOU DID WRITE ONE, read which STATE it
    names. 'arrived as null' means no text reached this tool, and since #938 the fix is to
    RE-ISSUE THE CALL: measured (#657) this server carries a 4 MiB argument byte-exact over its
    own stdio transport, and (#938) argument ORDER changes nothing either — ten permutations
    across the real boundary, all byte-exact — so a kilobyte report hits no limit here and the
    loss is above it, in YOUR OWN EMISSION. A parameter whose opening TAG is malformed (the
    namespace prefix dropped) is never recognised as a parameter, so it never becomes a JSON
    key; hold position and length fixed, vary only that tag, and the same call flips between
    this refusal and a delivered value. It correlates with a long PRECEDING value, which is why
    three cards read it as 'the trailing argument is dropped' and reordered the call —
    reordering is not the fix, writing the tag correctly is. A MISSPELLED parameter name is
    no longer one of the possibilities: since #720 an unknown argument is refused at the boundary
    BY NAME, before this tool runs, so reading 'arrived as null' rules that cause out instead of
    leaving it first in line — best-effort, like the gate itself, which reaches into the SDK and
    restores the old silent drop on the tools it did NOT FINISH. That last clause used to read
    'when it cannot be installed', and #778 measured why the narrower one is the true one: after
    a part-way failure the tools already done keep refusing, so the drop returns on the tail and
    not across the whole surface (of that tail, the single tool it died ON may already be
    refusing too, and nothing here tells you which: the server's log line sets those two states
    side by side as a disjunction and stops there. Read it as a residual risk, not as a stream
    to go looking in — it is the SERVER's startup stderr, which no tool here shows you). What
    still arrives as null — an
    explicit null, a dropped key and an omitted argument — the tool can report as a STATE but
    never tell apart as a cause (a malformed tag is how the dropped key actually happens).
    If re-issuing keeps failing, the older workaround still stands: advance
    with a SHORT worklog and post the full
    text as separate comment() calls marked [worklog] (say so in the short one, so the
    journal does not read as a placeholder)."""
    return _wf().advance(
        task_id, to, spec=spec, worklog=worklog, evidence=evidence, root_cause=root_cause
    )


@_mcp_tool
@_tool
def review_task(task_id: int, verdict: str, report: str) -> dict:
    """Independent review of a task in Review (offered via next_task with review_kind). You
    must NOT be the author of the code under review — a separate session reviews it. Check
    for real by RUNNING it, not just reading: review_kind='bug' — reproduce the bug and
    confirm the fix closes the root cause (not the symptom); review_kind='change'
    (feat/chore/docs/refactor) — confirm it does what the spec/description said, the tests
    are real, it stayed in its slice, and look for obvious regressions nearby.
    verdict='approve' — a verdict comment, a human moves it to Done next;
    verdict='needs_work' — a verdict comment and the task returns to Build to the
    implementer, EXCEPT when the card has no assignee at all (a human cleared it or
    hand-placed it in Review): then there is no implementer to return it to, so it goes
    to QUEUE as free work — in Design/Build an ownerless card can be read and commented
    on but no agent tool can MOVE it or make it anyone's, so your report would sit there
    unanswered. Either way the report stays on the card for whoever picks it up. report
    is required: what you checked, what you observed, why this verdict.
    "You must NOT be the author" is a RULE here and, in most projects, only a rule: by
    default this tool does not check who you are, because in a solo setup one token is the
    whole fleet — author and reviewer authenticate as the SAME assignee, and independence
    comes from a sibling agent reviewing with its own fresh context. A project that has
    provisioned a SECOND reviewer identity can promote the rule to a GATE by setting
    require_review_independence = true in its .vikunja-mcp.toml (tracker #37); then a
    verdict from anyone listed in the card's assignees is refused outright, and the refusal
    tells you to review under the reviewer token instead. Do not set that flag before a
    second identity exists — with one token nobody would be able to review anything."""
    return _wf().review_task(task_id, verdict, report)


@_mcp_tool
@_tool
def call_human(task_id: int, question: str) -> dict:
    """A question for the human — the ONLY channel (don't ask in the console: the
    orchestrator runs under /loop, no human is at the console — chat/AskUserQuestion/a
    plan awaiting approval would hang). The question is posted as a comment, the task
    moves to the 'Your Call' column (abbreviated YC), the assignee is kept. After
    calling, don't wait for an answer: take the next task; the human replies with a
    comment and moves the card back to Design/Build themselves, and next_task hands it
    back as "your active" task. This is NOT review and NOT an external block.
    Works ONLY from Design/Build: if you are REVIEWING a card, put your question in
    review_task(task_id, verdict='needs_work', report=<the question>) instead — the card
    goes back to its implementer in Build, who owns it and calls call_human from there.
    When a notification webhook is configured (VIKUNJA_NOTIFY_WEBHOOK), the human is also
    pinged about the parked card — best-effort: the result's `notified` key reports
    delivery, and notified=false only means the PING was lost (the question IS parked;
    don't retry the call — mention in your report that the human must check the board)."""
    return _wf().call_human(task_id, question)


@_mcp_tool
@_tool
def return_task(task_id: int, reason: str) -> dict:
    """Return a task because of an EXTERNAL block (no access/dependency/someone else's
    service): unassigns you, adds label 'blocked', CLEARS any VERDICT label the card was
    carrying (#693 — a card leaving the pipeline unassigned has no live assessment; only
    'reviewed'/'review-failed' go, other labels like 'bug' stay) and moves it to Backlog for
    human re-triage. REFUSES from TWO stages. From Review — never use it to get rid of a card you
    are reviewing; a reviewer's block or question goes in review_task(verdict='needs_work'),
    and a finding outside that card's slice goes in file_task. From Done — a human accepted
    that card, and moving accepted work back out is the human's call too (the Done transition
    is human-only in BOTH directions); Done work that needs redoing goes in file_task as a
    follow-up card. Since #662 that Done half is ONE rule shared by every tool, not this one's
    own: from Done you get the same refusal whatever you call, so trying the next tool after it
    is wasted. It still works from Backlog/Queue/Design/Build/Your Call."""
    return _wf().return_task(task_id, reason)


@_mcp_tool
@_tool
def decompose(task_id: int, subtasks: list[dict], ordered: bool = False) -> dict:
    """Break up YOUR large task (>~half a day of work) into >=2 subtasks:
    [{'title': ..., 'description'?: ..., 'priority'?: 0-5}]. Subtasks go into Queue with
    a relation to the parent; the parent moves to Backlog with label 'epic', and any VERDICT
    label it was carrying is CLEARED (#673 — a container holds no code of its own, so an
    assessment of the work it used to be no longer applies; only 'reviewed'/'review-failed'
    go, other labels like 'bug' stay).
    Pass ordered=True when the subtasks MUST run in sequence (each builds on the previous):
    they are chained in ARRAY ORDER so only the head is claimable immediately and each later
    child unlocks when its predecessor reaches Review. Leave ordered=False (default) when the
    subtasks are independent and may be worked in parallel.
    REFUSES from TWO stages. From Review (#663) — never split a card that is under review (or
    already approved): it would unassign it, CLEAR the verdict label and drop children into
    Queue, i.e. pull work out of the pipeline before anyone ruled on it — and on an approved
    card erase the ruling somebody already made. Splitting is a Build-time call, so the card
    goes back to Build first — a reviewer sends it there with
    review_task(verdict='needs_work', report=<why it should be split>) and its implementer
    decomposes from there; a finding outside that card's slice goes in file_task. From Done
    (#649, now the shared #662 rule every tool answers with — so from Done the next tool you try
    says the same thing) — a human accepted that card, and splitting accepted work back out to
    Backlog is the human's transition too; file_task the follow-ups instead (they are NEW work,
    not a split of this one). It still works from Backlog/Queue/Design/Build/Your Call, where
    the ownership guard applies as usual."""
    return _wf().decompose(task_id, subtasks, ordered)


@_mcp_tool
@_tool
def file_task(
    title: str, description: str = "", priority: int = 0,
    related_task_id: int | None = None, project_id: int | None = None,
    queue: bool = False,
) -> dict:
    """File a task DISCOVERED mid-work (a bug/tech-debt OUTSIDE your current task) into
    Backlog for human triage. WHEN: you hit a problem unrelated to the current task with
    nowhere to put it — park it here, do NOT fix it silently and do NOT drag it into your
    diff. This is NOT splitting your own large task — use decompose for that (it puts
    subtasks in Queue with a parenttask). Files into Backlog (NOT Queue — a human
    prioritizes), marks it with a [filed-by-agent] comment and, if related_task_id is
    given, adds a 'related' relation to the task it was found during. No ownership needed
    — this is a new card.
    THE MARKER RECORDS THE FILING, NOT WHERE THE CARD IS NOW (#1167). It is a dated
    provenance stamp; the column is live state, and the human this tool asks to triage
    moves cards out of Backlog by hand. So a Backlog-marked card sitting in Queue is the
    ORDINARY shape of a card that has been triaged, not a contradiction — on the four
    cards that raised #1167 the server's own request log named the WEB FRONTEND, not this
    package, four times out of four. Do not read a card's column off its journal. And note
    what the board cannot tell you: it records no mover, so neither can this tool — that
    answer came from a log on the server, not from anything here, and even there it reads
    only as far as "outside this package".
    QUEUE OPT-IN: pass queue=True ONLY when a human explicitly asked you to file this
    task as work to do (their instruction IS the triage — e.g. an answer on a Your Call
    card, or a direct "file a task for X" in chat/comments, in whatever language they
    happen to say it in): the card lands in YOUR project's Queue, unassigned and
    immediately claimable by any agent. NEVER queue=True for findings you discovered
    yourself — those keep the default (Backlog) so the human prioritizes them. Not
    combinable with a cross-project project_id (refused, nothing created): another
    project's Queue is not yours to fill — their human triages.
    CROSS-PROJECT (agent-to-agent coordination): pass project_id — a numeric Vikunja
    project id — to file into ANOTHER project's Backlog, e.g. when your work needs a
    change owned by that project's repo/agent. Take the id from the task/human context;
    if you don't know it, ask via call_human — do NOT guess. Access is the API token's
    call: no access to the target means a clear refusal with NOTHING created. The card
    lands in the TARGET's Backlog for THAT project's human to triage; the marker names
    your project, and related_task_id still links it back to your current task across the
    project boundary. The filed card lives on the target board — your get_task/comment
    won't see it afterwards. Omit project_id (default) to file into your own project.
    NAMING THE CARD YOU JUST FILED: the result carries filed.ref — the readable
    reference ("VMCP-195 (732)"). ECHO IT VERBATIM in comments/worklogs; NEVER assemble
    one yourself from the id, and never carry over an index you saw elsewhere. A guessed
    identifier does not read as broken — it resolves to a DIFFERENT live card and sends
    the reader there (#660 shipped "VMCP-181 (732)": 732 is VMCP-195, and VMCP-181 is an
    unrelated card), and NOTHING downstream catches it: measured on a real 2.3.0 (#757),
    the identifier is not a search key on either surface — REST and the web UI both
    return zero hits for it — so the id in parens is the reader's only cheap check. No
    extra call is needed: the ref comes back with the card. Filed
    cross-project, it carries the TARGET project's prefix — that is correct, echo it as
    is. A project with NO identifier prefix is not a failure and not the fallback: the
    server sends "#<index>" and the ref reads "#1 (107)" — echo that too. Only when the
    identifier is absent or blank does ref degrade to bare "#<id>"; that honest fallback
    is still the string to echo. One case the ref cannot cover: if the call RAISES after
    the card was created (a scope gap on the move/relation/marker write), the card exists
    but you got no id and no ref — say so plainly rather than reconstructing either."""
    return _wf().file_task(
        title, description=description, priority=priority,
        related_task_id=related_task_id, project_id=project_id, queue=queue,
    )


# `to: str | int` on the two tools below is not decoration — the annotation IS the wire
# contract (#1200). The MCP SDK builds a pydantic model from these signatures, so `to: str`
# made `handoff(to=17)` a hard ValidationError at the boundary, BEFORE `_resolve_sibling`'s
# careful refusals could run, while both docstrings advertised "a bare project id" and the
# generated schema said `{"type": "string"}`. And the ids an agent has in hand arrive as JSON
# NUMBERS: `siblings` rides in every next_task payload as `{"backend": 17}`. Measured at the
# real boundary with the sibling of `file_task`'s door as control — `file_task(project_id=17)`
# and `project_id="17"` BOTH land — so widening makes all three cross-project doors take an id
# the same way rather than inventing a fourth. (Their SCHEMAS still differ: `file_task` advertises
# integer-only. The behaviour is what an agent trips over.) `str | int` and not
# `StrictInt | StrictStr`: strict would also refuse a JSON boolean, which lax pydantic renders as
# project 1 — but it needs pydantic at MODULE scope, a NEW import on the path #521 cleared
# (measured: importing this module today loads no pydantic at all). Filed as #1207.
@_mcp_tool
@_tool
def handoff(
    task_id: int, to: str | int, title: str, description: str = "", priority: int = 0,
) -> dict:
    """Park YOUR active card and file the work it is waiting for onto a NEIGHBOUR project's
    board. WHEN: mid-task you find that the next step belongs to a DIFFERENT repo — the
    frontend card needs an endpoint the backend has not built yet. You cannot do that work
    (wrong repo) and must not silently drop the card.
    `to` is a sibling NAME from this repo's config (they arrive in every next_task response
    under `siblings`, e.g. {"backend": 17}) or a bare project id — pass the id exactly as you
    were handed it, as a number (17) or quoted ("17"); both work. Unknown name -> refusal
    that LISTS the configured ones; nothing is changed.
    WHAT HAPPENS: a new card is created in the NEIGHBOUR's Backlog (never their Queue —
    their human triages their own board), YOUR card is linked to it as blocked-by, then
    goes back to Queue unassigned. Your WIP slot frees.
    YOU DO NOT NEED TO DO ANYTHING ELSE, AND NEITHER DOES A HUMAN: your card is offered
    again automatically once the filed card reaches Review — the predecessor gate holds it
    until then. Do NOT keep working the card after a handoff; go take the next one.
    NOT for a bug you found outside your task (that is file_task), NOT for splitting your
    own task (decompose), NOT for a card that is simply on the wrong board (transfer_task).
    The `title` is what the neighbour's human triages: say what THEY need to build, not
    what you were doing. Echo filed.ref verbatim; never assemble a ref from the id."""
    return _wf().handoff(
        task_id, to=to, title=title, description=description, priority=priority,
    )


@_mcp_tool
@_tool
def transfer_task(task_id: int, to: str | int, reason: str) -> dict:
    """Move a card, with its whole comment history, onto a NEIGHBOUR project's board.
    WHEN: the card was filed on the WRONG board — it is pure backend work sitting in the
    frontend project. Nothing stays behind and nothing new is created.
    NOT for a dependency: if YOUR card needs someone else's work first, that is handoff.
    `to` takes a sibling name from next_task's `siblings` (or a project id, as a number or
    quoted — both work), same as handoff. `reason` is required — it is the only context the
    people over there will have for a card arriving with a stranger's comment history attached.
    THE CARD'S REF CHANGES. The target project re-indexes it on arrival (a card moved into
    a project already holding BACK-2 comes out as BACK-3), so every ref quoted in earlier
    comments, worklogs or commit messages now names nothing. Use moved.ref from the result
    from that point on; do NOT reuse the old one and do NOT go back and rewrite refs in
    comments already written — say plainly that the card moved.
    The card lands in the target's Backlog, unassigned, with its labels for claim state
    and review verdicts cleared: a verdict earned on your board does not travel. Relations
    are kept. Refused for a card in Done (human territory) and for an epic container with
    children — its children would be left pointing at a parent nobody there can open."""
    return _wf().transfer_task(task_id, to=to, reason=reason)


def main(argv: list[str] | None = None) -> None:
    # Dispatch order is LOAD-BEARING and stays exactly as it was: every non-MCP subcommand
    # returns/exits BEFORE _self_heal_installed_artifacts() and before _server(), which is
    # what keeps them off ~/.claude AND (since #521 deferred the SDK import) off the MCP SDK.
    args = sys.argv[1:] if argv is None else argv
    if args and args[0] == "--version":
        print(f"vikunja-mcp {__version__}")
        return
    if args and args[0] == "setup":
        from vikunja_mcp.setup_cmd import run_setup

        raise SystemExit(run_setup(args[1:]))
    if args and args[0] == "install-skill":
        from vikunja_mcp.setup_cmd import install_skill

        install_skill()
        return
    # `claimable` — the exported next_task verdict for hgdev-acp's loop idle check (one JSON
    # line on stdout, exit 0 ran / 1 failed). Dispatched BEFORE the self-heal deliberately: the
    # hub spawns this per poll tick, so it must not touch ~/.claude, must not risk heal noise,
    # and must start fast. It is READ-ONLY (see claimable_cmd / Workflow.next_task).
    if args and args[0] == "claimable":
        from vikunja_mcp.claimable_cmd import run_claimable

        raise SystemExit(run_claimable())
    # `workspace` — per-task git worktrees for the parallel drain. Dispatched before the
    # self-heal for the same reasons as `claimable`: it is called per task by the pump and
    # must start fast, and it must not touch ~/.claude.
    if args and args[0] == "workspace":
        from vikunja_mcp.workspace_cmd import run_workspace

        raise SystemExit(run_workspace(args[1:]))
    _self_heal_installed_artifacts()
    _server().run()          # _server(), not the module global: builds + registers on demand


def _self_heal_installed_artifacts() -> None:
    """On server start, refresh installed agent artifacts (SKILL.md + hook) from the packaged
    source so a moving-`stable` rollout reaches them as automatically as the server code itself.
    Wholly best-effort: a heal failure must never crash or delay the stdio server, and this must
    never write to stdout (the MCP protocol channel) — a healed-something note goes to stderr."""
    try:
        from vikunja_mcp.setup_cmd import sync_installed_artifacts

        healed = sync_installed_artifacts()
        if healed:
            print(
                f"vikunja-mcp: refreshed {len(healed)} stale agent artifact(s) from the package: "
                + ", ".join(str(p) for p in healed),
                file=sys.stderr,
            )
    except Exception:
        pass
