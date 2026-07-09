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

# A 401/403 from Vikunja is a CREDENTIAL problem, not a transient one — but a bare
# "Vikunja API 401" reads like a session hiccup and invites a pointless /mcp reconnect
# or server restart. Real incident: a token missing the `projects:views_buckets` group
# let reads + comment through but 401'd every stage transition (advance/claim/
# review_task move kanban buckets); the agent misdiagnosed it as a "stuck credential"
# and told the operator to restart the server — which CANNOT help, since a token's
# scopes are fixed at mint time. Spell out the scope and the real remedy here (server.py
# owns "clear errors") so this can't be misread again. The raw server text is appended.
_AUTH_GUIDANCE = {
    401: (
        "Vikunja API 401 (unauthorized) — a token PERMISSION/SCOPE problem, not a "
        "transient or session error. The token authenticates (reads/comment may still "
        "work) but is not authorized for THIS endpoint. This server needs the token "
        "permission groups `other:user` and `projects:views_buckets` — the latter gates "
        "every stage transition (advance/claim/review_task/… move kanban buckets). A "
        "/mcp reconnect or a full server RESTART will NOT help: a token's scopes are "
        "fixed when it is minted. Remedy: re-mint the token with those groups and "
        "repoint the config"
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


def _wf() -> Workflow:
    global _workflow
    if _workflow is None:
        cfg = load_config()
        _workflow = Workflow(
            VikunjaAPI(cfg.url, cfg.token), cfg.project_id,
            enforce_single_wip=cfg.enforce_single_wip,
        )
    return _workflow


def _tool(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except (WorkflowError, ConfigError) as e:
            return {"error": str(e)}
        except VikunjaError as e:
            guidance = _AUTH_GUIDANCE.get(e.status)
            if guidance:
                return {"error": f"{guidance} [server said: {e.message}]"}
            return {"error": f"Vikunja API: {e.status} {e.message}"}
        except httpx.HTTPError as e:
            return {
                "error": f"tracker unreachable ({e.__class__.__name__}): "
                f"check the url in .vikunja-mcp.toml and the VPN"
            }

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
    (linked tasks by relation kind) and all comments."""
    return _wf().get_task(task_id)


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
