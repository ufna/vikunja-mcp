"""stdio MCP server. Gates live in Workflow; this is thin wiring and clear errors."""
import sys
from functools import wraps

import httpx
from mcp.server.fastmcp import FastMCP

from vikunja_mcp import __version__
from vikunja_mcp.api import VikunjaAPI, VikunjaError
from vikunja_mcp.config import ConfigError, load_config
from vikunja_mcp.workflow import Workflow, WorkflowError

mcp = FastMCP("vikunja-tracker")

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
        "On a 401 this server re-reads .vikunja-mcp.env and retries once, so a freshly ROTATED "
        "token self-heals — seeing this means the token in .vikunja-mcp.env is STILL rejected. "
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


def _reset_workflow_cache() -> None:
    global _workflow
    _workflow = None


def _build_workflow() -> Workflow:
    cfg = load_config()
    return Workflow(
        VikunjaAPI(cfg.url, cfg.token), cfg.project_id,
        enforce_single_wip=cfg.enforce_single_wip,
    )


def _wf() -> Workflow:
    global _workflow
    if _workflow is None:
        _workflow = _build_workflow()
    return _workflow


def _reload_workflow_from_disk() -> bool:
    """Rebuild the cached Workflow from a FRESH read of config — picking up a token ROTATED in
    .vikunja-mcp.env while the server was running. Returns True if a new Workflow was built,
    False if config is now missing / unreadable / malformed (the previously cached Workflow is
    then left untouched). Total by contract: it runs inside _tool's except block, so it must
    NEVER raise — any config-read failure degrades to "no reload" rather than crashing the stdio
    server (same best-effort posture as _self_heal_installed_artifacts)."""
    global _workflow
    try:
        rebuilt = _build_workflow()
    except Exception:
        return False
    _workflow = rebuilt
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
            # value) and rewrite .vikunja-mcp.env. Reload config and retry the SAME call ONCE with
            # the fresh token, so /loop survives a rotation without a restart (tracker #140).
            # Write-safe: Vikunja rejects a 401 at its auth layer BEFORE the route handler runs,
            # so nothing was mutated server-side (verified on 2.3.0 — an unscoped token 401s a
            # write untouched). Same reasoning api.py uses to retry 429 for ANY method, unlike an
            # ambiguous 5xx it retries only for idempotent ones. Guard hard: only status 401,
            # exactly ONE retry, and the retry's outcome is FINAL — a second 401 surfaces the
            # guidance, it never recurses into another reload.
            if isinstance(e, VikunjaError) and e.status == 401 and _reload_workflow_from_disk():
                try:
                    return fn(*args, **kwargs)
                except (WorkflowError, ConfigError, VikunjaError, httpx.HTTPError) as retry_err:
                    return _error_result(retry_err)
            return _error_result(e)

    return wrapper


@mcp.tool()
@_tool
def next_task() -> dict:
    """What to do next, in order: (1) YOUR active task (Design/Build, incl. one bounced
    back from Your Call), (2) a task in Queue assigned to you, (3) a task in Review
    awaiting independent review — ANY card except an epic container, with no fresh verdict
    and not your own (review_kind names the rubric: 'bug' or 'change'), (4) the top FREE
    task in Queue. Never hands out a task assigned to someone else — those are "for humans".
    Leaves Backlog, blocked, and epic containers (label epic — a container, not a unit of
    work) alone. One task at a time.
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
    with 'the queue is empty'."""
    return _wf().next_task()


@mcp.tool()
@_tool
def claim(task_id: int) -> dict:
    """Take a task from Queue: assigns you and moves it to Design. You may take free
    tasks or ones already assigned to you; one assigned to someone else is "for humans"
    and claim won't hand it over. Also refused outside Queue and on a lost race (call
    next_task then). An epic container (label epic) is refused too — it's a container, not a
    unit of work; its evidence lives in its children, so work on those. If the single-WIP
    policy is enabled (enforce_single_wip in the repo toml, off by default), claim also
    refuses while you already have an active Design/Build task — finish it or return_task
    it first."""
    return _wf().claim(task_id)


@mcp.tool()
@_tool
def get_task(task_id: int) -> dict:
    """Task dossier: full (untruncated) description, stage, assignees, labels, related
    (linked tasks by relation kind), attachments (metadata only — {id, name, mime, size};
    a card may be nothing but a screenshot, so CHECK this and download_attachment it rather
    than guessing from an empty description) and all comments."""
    return _wf().get_task(task_id)


@mcp.tool()
@_tool
def download_attachment(task_id: int, attachment_id: int) -> dict:
    """Download a task attachment to a temp file and return its PATH — then Read the path to
    view it (a PNG/JPG renders visually; text/PDF opens as text). The path is returned instead
    of base64 so the file never bloats the context. attachment_id is the `id` from get_task's
    attachments[] (not the filename). Errors are actionable: a wrong id lists the task's real
    attachments; an oversized file is refused with its size before downloading."""
    return _wf().download_attachment(task_id, attachment_id)


@mcp.tool()
@_tool
def attach_file(task_id: int, path: str) -> dict:
    """Attach a LOCAL file — typically a SCREENSHOT of the finished work — to a task, so a human
    and the independent reviewer can SEE a visually-verifiable result instead of trusting 'done'.
    WHEN: your change is visually verifiable (a UI, a rendered page/chart, a generated image, a
    board layout) and you already have a screenshot from verifying it — attach it, then cite it in
    your advance(to='review') worklog as evidence beside the commit sha. NOT for every task: a
    change with no visual surface (a lockfile, a refactor, config) has nothing to show, so don't
    force it. `path` is a local file (the screenshot you produced); its basename becomes the
    attachment name, the MIME is inferred from the extension. This is standalone — it does NOT move
    the task; a failed upload never affects a stage transition. Actionable errors: a missing path,
    a directory, or an oversized file (>25MB) is refused with the reason; a 401 means the token
    lacks the tasks_attachments:create scope and a human must add that op."""
    return _wf().attach_file(task_id, path)


@mcp.tool()
@_tool
def comment(task_id: int, text: str) -> dict:
    """A progress note: findings, decisions ('picked X over Y because Z')."""
    return _wf().comment(task_id, text)


@mcp.tool()
@_tool
def advance(
    task_id: int, to: str,
    spec: str | None = None, worklog: str | None = None, evidence: str | None = None,
    root_cause: str | None = None,
) -> dict:
    """Advance YOUR task. to='build' requires spec (approach/design). to='review'
    requires a WORK REPORT: worklog (what was done and how it was verified — by running,
    not by reading code) + evidence (commit/PR/verification output); for bug fixes
    root_cause is MANDATORY — the cause of the bug (why it happened), not the symptom.
    The report is posted as a comment for the reviewer to read. There is no transition
    to Done — a human moves it to Done after review.
    EVERY task reaching Review returns review_needed=True + review_kind ('bug'|'change')
    so the orchestrator dispatches an independent reviewer — EXCEPT an epic container
    (label epic), which has no code of its own (its evidence lives in its children).
    to='review' is also LATCHED while any predecessor (a follows/blocked link, e.g. an
    ordered-epic step) is still below Review: if a predecessor was bounced Review→Build,
    finish its rework back to Review before this successor may advance (the refusal names it)."""
    return _wf().advance(
        task_id, to, spec=spec, worklog=worklog, evidence=evidence, root_cause=root_cause
    )


@mcp.tool()
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
    implementer. report is required: what you checked, what you observed, why this
    verdict."""
    return _wf().review_task(task_id, verdict, report)


@mcp.tool()
@_tool
def call_human(task_id: int, question: str) -> dict:
    """A question for the human — the ONLY channel (don't ask in the console: the
    orchestrator runs under /loop, no human is at the console — chat/AskUserQuestion/a
    plan awaiting approval would hang). The question is posted as a comment, the task
    moves to the 'Your Call' column (abbreviated YC), the assignee is kept. After
    calling, don't wait for an answer: take the next task; the human replies with a
    comment and moves the card back to Design/Build themselves, and next_task hands it
    back as "your active" task. This is NOT review and NOT an external block."""
    return _wf().call_human(task_id, question)


@mcp.tool()
@_tool
def return_task(task_id: int, reason: str) -> dict:
    """Return a task because of an EXTERNAL block (no access/dependency/someone else's
    service): unassigns you, adds label 'blocked', moves it to Backlog for human
    re-triage."""
    return _wf().return_task(task_id, reason)


@mcp.tool()
@_tool
def decompose(task_id: int, subtasks: list[dict], ordered: bool = False) -> dict:
    """Break up YOUR large task (>~half a day of work) into >=2 subtasks:
    [{'title': ..., 'description'?: ..., 'priority'?: 0-5}]. Subtasks go into Queue with
    a relation to the parent; the parent moves to Backlog with label 'epic'.
    Pass ordered=True when the subtasks MUST run in sequence (each builds on the previous):
    they are chained in ARRAY ORDER so only the head is claimable immediately and each later
    child unlocks when its predecessor reaches Review. Leave ordered=False (default) when the
    subtasks are independent and may be worked in parallel."""
    return _wf().decompose(task_id, subtasks, ordered)


@mcp.tool()
@_tool
def file_task(
    title: str, description: str = "", priority: int = 0,
    related_task_id: int | None = None,
) -> dict:
    """File a task DISCOVERED mid-work (a bug/tech-debt OUTSIDE your current task) into
    Backlog for human triage. WHEN: you hit a problem unrelated to the current task with
    nowhere to put it — park it here, do NOT fix it silently and do NOT drag it into your
    diff. This is NOT splitting your own large task — use decompose for that (it puts
    subtasks in Queue with a parenttask). Files into Backlog (NOT Queue — a human
    prioritizes), marks it with a [filed-by-agent] comment and, if related_task_id is
    given, adds a 'related' relation to the task it was found during. No ownership needed
    — this is a new card."""
    return _wf().file_task(
        title, description=description, priority=priority, related_task_id=related_task_id
    )


def main(argv: list[str] | None = None) -> None:
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
    _self_heal_installed_artifacts()
    mcp.run()


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
