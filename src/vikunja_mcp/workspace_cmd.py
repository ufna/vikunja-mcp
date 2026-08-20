"""`vikunja-mcp workspace` — per-task git worktrees for the parallel drain.

THE ONLY MODULE IN THIS PACKAGE THAT RUNS GIT. server.py / workflow.py / api.py stay git-free
on purpose: the MCP server's job is HTTP to Vikunja, and a subprocess in that path would be a
new class of failure on a stdio server that must never crash.

WHY IT LIVES HERE AT ALL. `git worktree add` refuses to check out a branch that is already
checked out elsewhere, so two parallel agents cannot both sit on `main` — each needs its own
tree on its own throwaway `task/<id>` branch, and pushes with `git push origin HEAD:main` so
the "one task = one commit on main" rule and the CI auto-release survive untouched. Creating
that is trivial; REAPING it is not, and reaping is the part only the tracker can do — nothing
else knows whether the task behind an orphaned tree is still alive (see gc_workspaces).

SAFETY INVARIANT, taken verbatim from hgdev-acp's reaper: push OK -> remove, push FAIL -> KEEP.
Housekeeping must never be how an agent's work disappears.

DOSSIER: `docs/dossier/workspace.md` — the measured evidence under the rules in this
module: `half-applied`, the gitlink typechange gap, why the probes
read the TREE rather than git's messages, and why `git diff` had to become `diff-index`.
Read it before changing a guard here; CLAUDE.md carries only the rule.
"""
import argparse
import fcntl
import functools
import json
import os
import re
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path

BUILD_NAME = "task-{task_id}"
REVIEW_NAME = "review-{task_id}"
BUILD_BRANCH = "task/{task_id}"
_NAME_RE = re.compile(r"^(task|review)-(\d+)$")
_ROLE_BY_PREFIX = {"task": "build", "review": "review"}

# WHY A `--release`/`--gc` REFUSAL CARRIES A CODE (VMCP-68), AND A CREATE REFUSAL DOES NOT
# (VMCP-110). This header used to read "WHY EVERY REFUSAL CARRIES A CODE" and was false of the
# create path in the same breath as its own body, which scopes the justification to `--gc`. Say the
# scope in the title: two later documents copied the universal out of here and had to be corrected.
# The codes below are produced ONLY by `_release_locked` and `gc_workspaces`. `_ensure_locked`
# refuses by RAISING `WorkspaceError`, which `run_workspace`'s catch-all renders as `{"error": …}`
# + exit 1 — no `code` key, on purpose: a code exists to feed a GRADER (`_keep_is_expected`, the
# only one in this package), and an orchestrator's answer to EVERY create refusal is the same
# single branch (SKILL.md's «Не завелось — цикл НЕ роняем»: degrade to one slot, keep draining).
# And it could not be made universal anyway — that catch-all covers an OPEN set (a non-repo, a
# malformed toml, a `ReadDeadlineExceeded`, an OSError), so a code there would be present-SOMETIMES,
# which is worse to parse than absent-always. Pinned both ways by
# test_the_two_refusal_channels_are_not_interchangeable; change the split and the docs move with it.
#
# THE PREDICATE THAT CARRIES A CODE IS `released: false`, NOT THE WORD "REFUSAL" (VMCP-142). A
# `--release` can still RAISE, and then it wears the create channel's shape by construction —
# `{"error": …}` + exit 1, no code — because it went through the same catch-all: a non-git cwd, a
# malformed toml, anything in that open set. What VMCP-142 closed is the state git's OWN `worktree
# list --porcelain` already names, so the module can recognise it BEFORE touching the tree: a tree
# pinned by `git worktree lock` (four spellings measured — with a reason, reasonless, on a review
# tree, and a locked entry whose directory is gone). Not every raise with intact work is like that
# — a worktree directory git cannot delete (mode 0500) still raises, and the gc isolation test
# depends on it doing so; what makes the lock codeable is that it is a NAMED git state, not an
# OS-level surprise. Quote the invariant over `released: false`; "every release-side refusal is
# coded" is the sentence that keeps drifting back into the docs, and it is FALSE both before and
# after 142 — 142 removed the instance that mattered, not the class.
#
# `--gc` has to grade its own refusals — routine vs
# "a human should look" (see _keep_is_expected) — and the only other thing a refusal carries is
# `reason`, which is PROSE: human-facing, deliberately reworded whenever a message turns out to
# mislead (the half-created diagnosis was reworded exactly that way). Grading on a substring of it
# would make every future rewording a silent reclassification. So the classification keys on these,
# and the prose stays free to change. Public, unprefixed, and asserted against SKILL.md by
# tests/unit/test_skill_contract.py: they are part of the CLI's JSON line, which the rulebook tells
# agents to read, so a value change here must drag the rulebook along.
CODE_NO_WORKTREE = "no-worktree"
CODE_HALF_CREATED = "half-created"
CODE_LOCKED = "locked"                # a human `git worktree lock` — see _release_locked's guard
CODE_DIRTY = "dirty"
CODE_UNPUSHED = "unpushed"
CODE_UNREACHABLE_HEAD = "unreachable-head"
CODE_DETACHED_BUILD = "detached-build"
CODE_POPULATED_GITLINK = "populated-gitlink"   # VMCP-266 — see _populated_gitlinks
CODE_SELF_TREE = "self-tree"          # --gc only: the tree gc itself is standing in
CODE_RELEASE_ERROR = "release-error"  # --gc only: _release_locked raised, sweep continued

# A DEFERRAL IS NOT A REFUSAL, AND IT GETS ITS OWN PREFIX FOR THE SAME REASON `MAIN_SYNC_*` DOES
# (VMCP-300, tracker #1183). Everything above is a verdict `_release_locked` REACHED on a tree it
# inspected, and `_keep_is_expected` grades every one of them cell by cell. What is below is the
# other thing a sweep can do: decline to inspect a tree at all. It never reaches the grader, so it
# must never wear the grader's prefix — a `CODE_*` with no cell in that grid reddens the pins that
# ENUMERATE that vocabulary, which is exactly right for a refusal and exactly wrong for this. Same
# boundary, same pin shape: test_defer_codes_are_not_part_of_the_graded_worktree_vocabulary.
# HOW MANY pins is TWO and not the three the `MAIN_SYNC_*` note below says, and the number is
# checked rather than inherited. Structurally: exactly three tests enumerate `CODE_*`
# (`startswith("CODE_")`), and one of them — test_main_sync_codes_are_not_part_of_the_graded_
# worktree_vocabulary — only asserts DISJOINTNESS with another prefix, which a new `CODE_*` cannot
# violate; so a new ungraded constant can only redden the grading grid and the policy-comment
# enumeration. Measured to the same number by this card's second independent pass (one bare
# ungraded `CODE_ZZ`, selection `tests/unit`, 1321 collected: control 0 failed; mutation 2
# failed), and this file's own older records agree, quoting 1 and 2 for narrower attacks. The
# neighbouring note is left as it landed rather than silently corrected — the CONCLUSION both
# notes draw survives at two.
DEFER_YOUNG = "young"                 # --gc only: dead on the board, but inside the grace window

# Review Important 2: EVERY git call in this module can run while `_repo_lock` is HELD (the
# network one — `git fetch origin` in _ensure_locked — provably does, before the idempotency
# early-return), so a call that blocks forever does not merely hang ITS caller: it wedges every
# other agent's ensure/--release/--gc on this repo, permanently, with no diagnostic. The things
# that block forever are closed here, in the ONE helper, so no call site can forget:
#   * an https credential prompt (no helper configured) -> GIT_TERMINAL_PROMPT=0;
#   * anything reading the inherited stdin -> stdin=DEVNULL, which is EOF and never a wait;
#   * an ssh host-key/passphrase prompt (ssh reads /dev/tty DIRECTLY, so neither of the two
#     above touches it) and a black-holed TCP connection -> the timeout below, the only
#     backstop that also covers what we cannot name in advance. Deliberately NOT closed by
#     exporting GIT_SSH_COMMAND="ssh -o BatchMode=yes": that env var OVERRIDES the user's
#     `core.sshCommand`, so injecting it would silently discard a configured identity
#     (`ssh -i ~/.ssh/id_rsa_…`, exactly how some of our own boxes are reached) and break
#     fetch outright for setups that work today — a new failure traded for a bounded stall.
# TWO bounds, because a timeout is itself a kill and the two kinds of call differ in what a kill
# COSTS. The network one (`fetch`) is the only call that can hang on something outside this
# machine, and killing it costs nothing — a half-fetched pack is discarded — so it gets the tight
# bound: 120s, an eternity for an incremental fetch on an already-cloned repo, and short enough
# that a wedged one frees the lock well inside an orchestrator tick instead of stacking ticks.
# Everything else is local disk and can only be SLOW, never hung on a peer — but killing a
# `worktree add` mid-checkout is destructive in a quiet way: git registers the admin dir with a
# "locked / initializing" marker BEFORE checking out, so a kill leaves an entry that `prune`
# refuses to drop and `_find` happily hands back as `created: false` — an agent dispatched into a
# half-populated tree. So local calls get a ceiling that exists only to catch a genuine hang
# (600s), never to police a big checkout on a slow disk.
_GIT_TIMEOUT = 600.0
_GIT_NET_TIMEOUT = 120.0

# git's OWN lock reason, written into `.git/worktrees/<n>/locked` by `worktree add` BEFORE it
# checks anything out and removed once the checkout finishes. A surviving one therefore means
# exactly one thing: that add never got to the end. Constructed and measured on git 2.50.1 (a
# smudge filter that sleeps + the _GIT_TIMEOUT kill above, no external killer needed): the entry
# stays listed as `locked initializing`, `git worktree prune` exits 0 and REFUSES to drop it, and
# the directory holds nothing but `.git` — every tracked file missing, the index all staged
# deletions. Which is why `_release_locked` used to call it "working tree is dirty (N entries)".
#
# MEASURED AND COUNTER-INTUITIVE, so do not "simplify" the guard on the assumption of a missing
# file: the state does NOT stay half-populated. `git worktree add` does the checkout in a CHILD
# (`git reset --hard --no-recurse-submodules`), and SIGKILLing the parent orphans that child onto
# PID 1, where it keeps going — files appeared 30s and 60s after the kill, one sleeping smudge
# each, until the tree was COMPLETE. What never happens is the marker being cleared, because the
# process that clears it is the parent we killed. So there are two phases and only the second is
# stable: a tree that may look perfectly fine and is locked FOREVER — which means git will refuse
# `--release`/`--gc` removal for good, and it leaks until a human intervenes. Hence a guard keyed
# on the lock's PRESENCE (any file-content heuristic would pass in phase two) and a message that
# does not promise which phase the reader is looking at.
_LOCK_INITIALIZING = "initializing"

# THE GRACE WINDOW (VMCP-71). `--gc` runs at tick start from the MAIN checkout, and a build tree is
# alive only while its task sits in Design/Build assigned to me — so the tree reads DEAD the instant
# its agent calls `advance(to='review')`, while that agent is still standing in it and has not yet
# called `--release`. Nothing else catches that overlap: the self-guard in gc_workspaces only covers
# a `--gc` invoked from INSIDE the tree, and `git push origin HEAD:main` moves the LOCAL
# `origin/main` ref, so the unpushed guard passes and the tree is removed with its branch. Under a
# parallel drain the overlap is the NORM — background agents outlive the orchestrator's turn — and
# the review side has the mirror case: `review_task(verdict='needs_work')` moves the card Review->
# Build, so a reviewer's tree dies the moment it files that verdict. Nothing is DESTROYED (only
# clean, fully-pushed trees are removed, and that work is already on main); the cost is a working
# directory vanishing under a running turn, which surfaces as confusing tool errors while an agent
# composes its report.
#
# HOW MUCH OF THAT MIRROR CASE THE WINDOW ACTUALLY COVERS (VMCP-84): CONDITIONALLY, and the
# condition is not the one the sentence above suggests. This window is measured from a WRITE, and a
# review tree can live its whole life without one — a reviewer that only READS (the Read tool,
# `git log`, `git show`) moves neither marker `_last_activity` looks at. What such a tree has
# instead is its BIRTH: `git worktree add` sets both, so the protection runs for
# `_REAP_GRACE_SECONDS` FROM CREATION rather than from the verdict. A review that fits inside the
# window is covered exactly like a build tree (and most do — which is why this reads as free); a
# LONGER read-only review is covered by nothing at all, and the first sweep after its `needs_work`
# may take the directory out from under it. Reviewers that run the suite or a `git diff` in their
# tree bump a marker and rejoin the covered case; ONLY the long, purely-read-only review is exposed.
# LEFT AS IS, deliberately, and this paragraph is the decision: the exposure is bounded to a
# vanishing cwd (a review tree is detached and clean, an in-tree commit is refused by the
# reachability guard, so there is nothing there to destroy), while every fix reintroduces something
# already rejected above — a FACT-based signal (nothing holds a process across an LLM's tool calls),
# or keeping a tree alive on a card the reaper must not be made to wait for. Touching a marker just
# to be seen would be gc's own bug (VMCP-90) rebuilt on the other side. SKILL.md's standing rule —
# never assume your tree survived `advance`/a verdict, re-`ensure` it — is what covers the rest, and
# it is a rule for BOTH roles precisely because this window is not a promise to either.
#
# WHY A CLOCK AND NOT A FACT. The semantically exact signal would be a held flock, and an LLM
# sub-agent holds no process across tool calls, so there is nothing to hold it. REJECTED (recorded
# so it is not re-proposed): treating a build tree as alive while its card sits in Review — a card
# waits in Review until a HUMAN moves it to Done, which would suspend the reaper indefinitely and
# defeat the module's purpose.
#
# WHY 30 MINUTES — derived from the window it must cover, not picked. That window is (last
# filesystem write inside the tree) -> (`--release`). By SKILL.md's own integration recipe the last
# write is the rebase / re-run of the done criteria just before `git push origin HEAD:main`; after
# it come the push, `git rev-parse HEAD`, the model turn that composes and calls
# `advance(to='review')` with the full work report (the largest single term: a long report with
# extended thinking, possibly a harness-retried API error), and the model turn that calls
# `--release`. Three to four LLM tool-call turns: 1-3 min typical, ~10-15 min pathological. 30 min is
# ~2x that pathological estimate and ~3 ticks of a `/loop 10m`. Rounded UP on purpose, because the
# costs are asymmetric: a dead tree lingering extra sweeps costs one directory on disk for at most
# this window plus a tick, blocks nothing (tree names are per task+role and `_find` reuses an
# existing one) and cannot delay a CRASHED agent's tree (that task is still in Design/Build, i.e.
# still alive, so this window never sees it) — while a window too short reintroduces the race
# itself. Deliberately a constant and not a config key: it is a bound on agent latency, not a
# per-repo preference, and SKILL.md keeps telling agents not to rely on their tree surviving
# `advance` at all — this is a backstop, not a promise.
_REAP_GRACE_SECONDS = 30 * 60

# THE SWEEP-READ BOUNDS (VMCP-72). Two numbers, because gc's liveness read is SEVERAL requests
# and the lock it holds is repo-wide: `_READ_TIMEOUT_SECONDS` bounds ONE request,
# `_READ_DEADLINE_SECONDS` bounds the WHOLE read. The per-request bound alone cannot bound the
# hold, because the request COUNT is not a constant.
#
# MEASURED against the real tracker (public https, 3 rounds, read-only, board as VMCP-68 left
# it): the read is FOUR requests — GET /projects/<p>/views, GET /info, GET
# /projects/<p>/views/<v>/tasks?page=1, GET /user — totalling 0.89-1.10 s. (`buckets` is NOT in
# this path; workflow._bucket() is only used for MOVES.) `liveness_board` passes
# require_titles={Design,Build,Review,"Your Call"}, so paging stops once those buckets stop
# returning full pages: requests = 3 fixed + floor(max(|Design|,|Build|,|Review|,|Your Call|) /
# page_size) + 1, where page_size is the server's max_items_per_page (50 here).
#
# WHY THAT GROWS. TWO of those four columns are drained by a HUMAN, not by the pump: a card
# waits in Review until someone moves it to Done, and in Your Call until someone answers it.
# Review held 41 cards when this was written — nine short of the 50 that adds a page, and it
# gained one during the session that measured it. So the request count rises by one per 50 cards
# in EITHER column and has no upper bound, and with it the hold: MEASURED in a lab (a
# slow-but-correct fake Vikunja at 3 s/request, real httpx + real api.py), today's shape =
# 4 requests = 12.03 s held; 140 in Review = 6 requests = 18.03 s; 140 in Your Call and an EMPTY
# Review = 6 requests = 18.04 s, i.e. the newer column drives it exactly as hard. Exactly
# requests x latency. At the per-request ceiling that is 40 s of held lock today, 60 s at 140
# cards, unbounded upward. Everything queued behind it — every agent's `--release`, every
# `ensure` for a dispatch — waits that long, and at wip_limit = 3 those are precisely the agents
# trying to clean up after themselves.
#
# WHY 30 s. It must never fire on a tracker that is merely SLOW (a false abandon costs a skipped
# sweep), and must be small next to the tick it delays. 30 s is: >= 3x the per-request bound, so
# a single legitimately slow request is never truncated by the TOTAL; ~26-33x the measured
# healthy read; enough for 15 requests at a degraded 2 s each (~600 cards in Review) before a
# working read is abandoned; and 5% of a `/loop 10m` tick. Like `_REAP_GRACE_SECONDS` it is a
# constant and not a config key — a bound on housekeeping latency, not a per-repo preference.
#
# REJECTED, on the measurement above: (a) a CHEAPER liveness query — 3 of the 4 requests are
# FIXED overhead, so it could at best remove the ONE board page and would still leave the hold
# proportional to nothing it controls; and none exists anyway, since a task's stage is knowable
# only from the kanban board (Vikunja 2.3's task JSON carries no per-view bucket) so the
# alternative is a per-tree get_task — up to 2 x wip_limit requests, MORE than the one it
# replaces — and since VMCP-68 that one fetch has THREE consumers (active/review/parked), so a
# cheaper query has to answer all three or the saving is imaginary. (b) ACCEPTING the current
# bound and documenting the worst case — a worst case that grows by 10 s per 50 cards in a
# column only a human drains is not a bound. (c) A NON-BLOCKING lock (recorded in the dossier
# before this task existed) — it bounds how long gc WAITS, and the cost is how long it HOLDS.
#
# WHAT IS AND IS NOT BOUNDED. This covers the tracker READ. The rest of the hold is local git,
# already ceilinged per call by `_GIT_TIMEOUT` and in practice milliseconds per tree; the total
# hold is therefore (<= 30 s of read) + (local git per tree on disk).
_READ_TIMEOUT_SECONDS = 10.0
_READ_DEADLINE_SECONDS = 30.0


class WorkspaceError(Exception):
    """The message is printed as the CLI's JSON error line."""


class ReadDeadlineExceeded(WorkspaceError):
    """The sweep's liveness read ran out of its OVERALL budget — reported, nothing reaped.

    A `WorkspaceError` SUBCLASS, and that inheritance is a safety decision, not tidiness. Two
    layers of api.py would eat this if it were an httpx exception instead:
      * `_fetch_page_size` catches `(VikunjaError, httpx.HTTPError)` and resolves the page size
        to UNKNOWN — a spent budget would be SWALLOWED there and the read would carry on past its
        own deadline (and, VMCP-89, on an unknown page size it carries on paging EXHAUSTIVELY,
        so a swallowed deadline would cost more requests, not fewer);
      * `_req` retries `httpx.TransportError` on idempotent methods, so with retries ever
        re-enabled a deadline would be re-attempted rather than obeyed.
    Being a WorkspaceError, it propagates straight out of the read — before `gc_workspaces`
    enters its reap loop — and lands on the CLI's own `except Exception` as one JSON error line
    with exit 1, the same shape a `--gc` that cannot reach the tracker already has (SKILL.md: an
    erroring `--gc` degrades the pump, it does not stop it). MEASURED end to end through the CLI:
    exit 1 at 30.26 s on a read that would have taken 36 s, every tree still on disk — including
    one that was clean, pushed and otherwise due to be reaped — and the very next sweep against a
    healthy tracker succeeding in 0.74 s, i.e. the abandon released the lock rather than leaking
    it.

    Public (no leading underscore) on purpose: the class name IS the CLI's error string, and
    `{"error": "ReadDeadlineExceeded: ..."}` tells a human reading the pump's log that the
    tracker was too slow, not that the worktrees are broken.
    """


class _ReadDeadline:
    """An overall budget for gc's liveness read, enforced as an httpx REQUEST event hook.

    Enforced at the hook rather than around the call because the read's cost is spread over
    several requests inside `liveness_board()`/`active_task_ids()`, and there is no safe way to
    abandon a call from the outside: a thread that times out does not stop the socket read it is
    blocked in, so the lock would be released while a request was still in flight.

    Each request does two things:
      * REFUSE when the budget is spent — raising BEFORE the request is sent, so the abandon
        costs nothing and, crucially, happens before any liveness set exists to act on;
      * CLAMP that request's own timeout to what is LEFT, so the last request cannot overshoot
        the budget by a whole `_READ_TIMEOUT_SECONDS`. `request.extensions["timeout"]` is httpx's
        documented per-request override and is read by httpcore at send time; MEASURED honoured
        on httpx 0.28.1 against a slow server — a 10 s budget on a read that needs 18 s returned
        at 9.96 s, not 12 s.
    The clamp is why the budget's failure does not always name itself — a clamped request dies as
    httpx's own `ReadTimeout` — and why `_read_liveness` relabels one that fires with the budget
    already spent. Read that note before changing either half.

    ARMED EXPLICITLY, by `_read_liveness`, once its caller holds the lock. It is also
    armed at construction, so a caller that forgets still gets a bounded read rather than an
    unbounded one; the failure of forgetting is then a budget that started slightly early, never
    one that never starts. Deliberately NOT disarmed after the board fetch: `active_task_ids()`
    still issues the `/user` request, and any request a future sweep adds is covered by
    construction rather than by remembering to extend the window.

    `now` is injectable so the behaviour can be tested without sleeping; the default is
    `time.monotonic` (a DURATION must not move when NTP steps the wall clock — unlike
    `_last_activity`, which compares against file mtimes and therefore must use `time.time`).
    """

    def __init__(self, budget: float, now=time.monotonic) -> None:
        self.budget = budget
        self._now = now
        self.arm()

    def arm(self) -> None:
        self._expires_at = self._now() + self.budget

    def spent(self) -> bool:
        """Is the budget gone? Asked by `_read_liveness` to tell the budget's own doing from an
        unrelated failure that merely happened while it was running."""
        return self._now() >= self._expires_at

    def __call__(self, request) -> None:
        remaining = self._expires_at - self._now()
        if remaining <= 0:
            raise ReadDeadlineExceeded(
                f"the liveness read exceeded its {self.budget:.0f}s overall budget at "
                f"{request.method} {request.url.path} — the sweep was abandoned with the repo "
                f"lock released and NOTHING inspected or removed; the next tick sweeps again"
            )
        # every key explicitly, not just the ones already present: httpx always populates all
        # four (connect/read/write/pool) from the client's Timeout, but a missing mapping must
        # clamp rather than silently leave the request unbounded.
        current = request.extensions.get("timeout") or {}
        request.extensions = {
            **request.extensions,
            "timeout": {
                key: remaining if current.get(key) is None else min(current[key], remaining)
                for key in ("connect", "read", "write", "pool")
            },
        }


def _run_git(
    args: tuple[str, ...], cwd: Path | None, timeout: float | None,
    env_extra: dict[str, str] | None = None, stdin_text: str | None = None,
) -> subprocess.CompletedProcess:
    r"""`stdin_text` REPLACES the standing `stdin=DEVNULL`, and few callers want it.

    "Only one caller" is what this line said until VMCP-252 (851) added two more — `git cat-file
    --batch-check` and `git hash-object --stdin-paths`, both in
    `_paths_already_holding_incoming_bytes` — so it is three, and the count is left out rather than
    corrected upward, since the next one would stale it again. What all three share is the shape
    the paragraph below is really about: the INPUT is a path list.

    DEVNULL is the default for a reason worth keeping: a git subcommand that decides to ask a
    question would otherwise inherit this process's stdin, which for the MCP server is the
    TRANSPORT. `git check-ignore --stdin` is the call this was built for, and
    it is fed this way rather than through argv because the alternative has two edges this one
    does not — ARG_MAX on a large incoming diff, and `-z`, which git refuses without `--stdin`
    (measured: `fatal: -z only makes sense with --stdin`). What `-z` buys is narrower than an
    earlier draft of this sentence claimed, and has now been narrowed TWICE. Round one: a SPACE
    in a filename survives without it. Round two, because the sentence that replaced it — "only
    a NEWLINE has no unquoted spelling at all" — was still wider than its proof, and the second
    pass narrowed it in two different directions at once.

    On OUTPUT, measured at this call site with five files (`ta<TAB>b.q`, `q"uote.q`,
    `back\slash.q`, `new<NEWLINE>line.q`, `кир.q`): a TAB, a `"` and a `\` come back C-quoted
    just as a non-ASCII byte does, and `core.quotePath=false` un-quotes ONLY the non-ASCII one.
    So `-z` earns its place for four spellings, not one. Say `core.quotePath` and not "any
    quoting setting" — the config space was not exhausted and cannot be from here; what was
    measured is that the one knob anybody reaches for rescues none of the four.

    On INPUT the newline is a different problem entirely, and the more serious one: without `-z`
    the batch is newline-separated, so `new<NEWLINE>line.q` is TORN IN TWO and git answers, rc=0
    and unquoted, about `line.q` — a path nobody asked about. That is why `"new\nline.q"` does
    not appear in the output above at all: it never arrived. None of this reaches the code, which
    passes `-z` always.
    """
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", **(env_extra or {})}
    limit = _GIT_TIMEOUT if timeout is None else timeout
    try:
        return subprocess.run(
            ["git", *args], cwd=cwd, capture_output=True, text=True,
            **({"input": stdin_text} if stdin_text is not None
               else {"stdin": subprocess.DEVNULL}),
            env=env, timeout=limit,
        )
    except subprocess.TimeoutExpired:
        # convert here rather than let TimeoutExpired escape: the module's whole error
        # vocabulary is WorkspaceError (the CLI prints it, gc's per-tree handler reports it),
        # and "git … timed out" is the one message that names the actual failure.
        raise WorkspaceError(f"git {' '.join(args)} timed out after {limit:.0f}s") from None


def _git(
    *args: str, cwd: Path | None = None, timeout: float | None = None,
    env_extra: dict[str, str] | None = None,
) -> str:
    proc = _run_git(args, cwd, timeout, env_extra)
    if proc.returncode != 0:
        detail = proc.stderr.strip() or proc.stdout.strip()
        raise WorkspaceError(f"git {' '.join(args)} failed: {detail}")
    return proc.stdout.strip()


def _git_ok(*args: str, cwd: Path | None = None) -> bool:
    return _run_git(args, cwd, None).returncode == 0


# THE ONE WAY THIS MODULE LOOKS INSIDE A WORKTREE IT MAY NOT WRITE TO (VMCP-90), and what it buys
# is the ENVIRONMENT, not tidiness. `git status --porcelain` REWRITES the index every time, even in
# a clean tree (measured, git 2.50.1): it writes the refreshed stat cache back. `_last_activity`
# reads that index mtime, so the sweep's own inspection used to be indistinguishable from an
# agent's footprint — the tree gc had just refused read as freshly touched on the NEXT sweep and
# was skipped by the grace window, silently, in NEITHER list. MEASURED over consecutive real
# sweeps: sweep 1 reported `kept=[unreachable-head, unpushed, half-created]`, sweeps 2 and 3
# reported only `half-created` (the refusals decided BEFORE any git call in the tree). So a
# standing alarm — the list that means "a human should look", which the pump reads EVERY tick —
# was absent from ~29 of every 30 minutes. Nothing was lost (re-ageing the markers brought every
# entry straight back); the signal was merely unreliable exactly when it mattered.
#
# GIT_OPTIONAL_LOCKS=0 is git's own switch for this ("prevent `git status` from refreshing the
# index as a side effect"), and it is the right SHAPE because it never has to tell gc's writes from
# anyone else's: gc simply stops writing. An agent's or a human's `git status`/`add`/`commit` in
# the tree does not set it and still bumps the index (measured), so the window keeps reading the
# one thing it exists to read — somebody may still be standing in this tree. The alternatives all
# had to draw that distinction after the fact: restoring the mtimes after a refusal blindly rewinds
# a commit an agent made DURING the inspection (a linked worktree's commit takes no lock of ours),
# and dropping the index from `_last_activity` deletes the only fresh marker an hour-old tree has.
#
# THE ENV VAR AND NOT `--no-optional-locks`, which git documents as equivalent: a git older than
# 2.15 does not know the FLAG and would fail the inspection outright — every dead tree turning into
# a `release-error`, i.e. a broken reaper traded for a delayed alarm — while it simply ignores an
# env var it never learned and degrades to the old cadence. Fail toward the old bug, never toward a
# new failure.
#
# ONE HELPER, so the rule is "gc never writes inside a tree it is inspecting" rather than "remember
# the flag at each call site". `status` is the only call that writes TODAY (`log`, `rev-parse` and
# `rev-parse --git-path` measured clean), but `git diff` refreshes the index the same way, so the
# next guard someone adds is covered by construction. COST: none measurable — the skipped
# write-back IS the difference. A 4000-file tree, clean and with 400 files modified: 21.8-21.9 ms
# without the write-back vs 22.0-22.2 ms with it.
def _git_inspect(*args: str, cwd: Path) -> str:
    """`_git` for a call that merely LOOKS at a worktree — read the note above before adding one."""
    return _git(*args, cwd=cwd, env_extra={"GIT_OPTIONAL_LOCKS": "0"})


def repo_root(cwd: Path | None = None) -> Path:
    return Path(_git("rev-parse", "--show-toplevel", cwd=cwd))


@functools.lru_cache(maxsize=None)
def _main_worktree(root: Path) -> Path:
    """Resolve `root` (the toplevel of ANY worktree — main OR linked) to the repo's MAIN
    worktree. Task 4 correction: `git rev-parse --show-toplevel`, run from INSIDE a linked
    worktree (the normal place for a per-task agent to be sitting, per SKILL.md), returns
    THAT worktree's own toplevel — not the main repo's. `worktree_root` derives its default
    sibling directory from the repo's name (`<repo>.worktrees`), so feeding it an unresolved
    linked-worktree root would compute a NESTED, wrong path — every real tree would then fail
    the "is this one of ours" parent check, and `--gc` would silently reap nothing while still
    reporting success. `git worktree list --porcelain` always lists the main worktree FIRST,
    from any linked tree (verified against real git), so it is the single source of truth.
    `.resolve()` because git already prints realpaths (Task 3 round-1 fix) and callers compare
    Path equality, not strings.

    MEMOISED (tracker #517), and the cache is on THIS function rather than on `worktree_root`
    deliberately. What costs anything here is the `git worktree list` SUBPROCESS, and the answer
    it computes — which worktree of this repo is the main one — cannot change while a process
    runs: `git worktree add/remove` append and drop LINKED entries, never the first one. What
    `worktree_root` adds on top (VIKUNJA_WORKTREE_ROOT, then the repo toml) is exactly the part
    that CAN change under a caller — the unit suite monkeypatches that env var per test — so
    caching the composite would freeze an override and turn a passing suite into a lying one.
    Unbounded because the key set is "toplevels this process has been handed", i.e. a handful.
    `cache_clear()` is part of the surface a test may use; nothing in the product calls it."""
    return list_worktrees(root)[0]["path"].resolve()


def worktree_root(root: Path) -> Path:
    """Where per-task trees live. Default: a SIBLING of the repo, never inside it — inside,
    pytest collection, ruff and `git add -A` would all sweep them up.

    `root` is canonicalised to the MAIN worktree first (see `_main_worktree`) so create,
    release and gc can never disagree about where trees live just because one of them happened
    to be invoked from inside a linked tree."""
    from vikunja_mcp.config import ENV_WORKTREE_ROOT, ConfigError, load_config

    root = _main_worktree(root)
    # env FIRST, on purpose: create/release need no tracker config at all, and load_config
    # RAISES without url/project_id — reading it first would throw away a perfectly good
    # VIKUNJA_WORKTREE_ROOT in any repo that is not tracker-configured.
    configured = os.environ.get(ENV_WORKTREE_ROOT)
    if not configured:
        try:
            configured = load_config(cwd=root).worktree_root
        except ConfigError:
            # ConfigError ONLY (review Minor 9): "this repo has no tracker config" is the
            # expected, fine case — create/release need none. A blanket `except Exception`
            # also swallowed the genuinely broken ones (malformed toml -> TOMLDecodeError,
            # an unreadable file -> OSError) and silently relocated every tree to the default
            # sibling directory, so a typo'd worktree_root would strand a live tree somewhere
            # the next `--release`/`--gc` no longer looks. Those must surface, not be guessed at.
            configured = None
    if configured:
        # .resolve() ALWAYS, even when `configured` is already absolute: `_find` compares
        # this path against `git worktree list --porcelain`, which prints the REALPATH. A
        # symlinked root (the macOS `/tmp` class of path, or a `/srv`→`/mnt` layout) would
        # otherwise never match — the resume-after-crash path breaks (a live tree reads as
        # "not registered" and gets refused-as-clobber) and release reports a false
        # "no worktree", leaking the tree forever.
        return (root / configured).resolve()
    return (root.parent / f"{root.name}.worktrees").resolve()


def default_base(root: Path) -> str:
    """The remote's default branch name. origin/HEAD is often absent in a fresh clone."""
    try:
        ref = _git("symbolic-ref", "--quiet", "refs/remotes/origin/HEAD", cwd=root)
        return ref.removeprefix("refs/remotes/origin/")
    except WorkspaceError:
        return "main"


@contextmanager
def _repo_lock(root: Path):
    """Serialise worktree mutations: the pump dispatches agents concurrently, and two
    `worktree add` calls on one repo race. Same shape as hgdev-acp's per-mirror mutex.

    NOT reentrant — flock on a second fd in the same process would deadlock. Anything that
    needs the lock while holding it must call the _locked cores, never the public wrappers.
    """
    common = Path(_git("rev-parse", "--git-common-dir", cwd=root))
    if not common.is_absolute():
        common = (root / common).resolve()
    lock_path = common / "vikunja-mcp-worktree.lock"
    with open(lock_path, "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def list_worktrees(root: Path) -> list[dict]:
    entries: list[dict] = []
    current: dict | None = None
    for line in _git("worktree", "list", "--porcelain", cwd=root).splitlines():
        if not line.strip():
            if current:
                entries.append(current)
                current = None
            continue
        key, _, value = line.partition(" ")
        if key == "worktree":
            if current:
                entries.append(current)
            current = {"path": Path(value), "branch": None, "detached": False, "head": None,
                       "locked": False, "lock_reason": None}
        elif key == "HEAD" and current is not None:
            # the checked-out COMMIT sha, same meaning as ensure_workspace's "head" (never the
            # "detached" BOOL below — one key, one meaning, per round 1's Finding 4). Taken from
            # the porcelain rather than a `rev-parse HEAD` with cwd=<tree>, on purpose: a
            # worktree whose directory is gone but which `prune` cannot drop (git refuses to
            # prune a LOCKED entry) is still listed here with its HEAD, while running git with
            # cwd inside it raises a bare FileNotFoundError that _git cannot convert.
            current["head"] = value
        elif key == "branch" and current is not None:
            # removeprefix, NOT rsplit("/") — refs/heads/task/42 must stay "task/42"
            current["branch"] = value.removeprefix("refs/heads/")
        elif key == "detached" and current is not None:
            current["detached"] = True
        elif key == "locked" and current is not None:
            # The key this parser used to DROP on the floor, and the whole finding: an entry can
            # be listed and unprunable and still be unusable. Two porcelain shapes, both measured:
            # a reason-less `git worktree lock` emits the bare line `locked` (partition gives an
            # empty value -> lock_reason None), a lock with a reason emits `locked <reason>`.
            # `locked` is the BOOL callers must gate on; `lock_reason` only refines the message
            # (see _locked_refusal). Deliberately NOT unescaped: git c-quotes a reason containing
            # newlines/control chars, and nothing here parses the reason — it is human-facing text,
            # while the only reason we ever COMPARE (git's own `initializing`) is always bare.
            current["locked"] = True
            current["lock_reason"] = value or None
    if current:
        entries.append(current)
    return entries


def _locked_refusal(task_id: int, role: str, wt: dict) -> str:
    """The message for a worktree that is registered, unprunable, and must NOT be handed back.

    Two wordings because the two causes need different things from the human. Note which one the
    CALLER gates on, though: `_ensure_locked` refuses on `wt["locked"]` alone and only asks this
    helper how to phrase it. Being wrong about the marker TEXT then costs a less specific message;
    being wrong about whether to refuse at all costs an agent working in a tree with no files in
    it. So the string comparison lives here, in the message, and never in the guard.
    """
    path = wt["path"]
    if wt["lock_reason"] == _LOCK_INITIALIZING:
        return (
            f"{path} is a HALF-CREATED worktree — git's own `locked {_LOCK_INITIALIZING}` marker, "
            f"left when a `git worktree add` is killed mid-checkout (a timeout, SIGKILL, ^C). Its "
            f"checkout may be incomplete (files missing, the index full of staged deletions) and "
            f"the marker is PERMANENT either way, so `git worktree prune` will not drop it and "
            f"`--release`/`--gc` can never reap it. Refusing to hand it back for task {task_id} "
            f"({role}). Nothing was removed: inspect it, then "
            f"`git worktree unlock {path} && git worktree remove -f -f {path}`"
        )
    reason = wt["lock_reason"] or "no reason given"
    return (
        f"{path} is a LOCKED worktree ({reason}) — refusing to hand it back for task {task_id} "
        f"({role}). A lock is a deliberate hands-off marker and git will not let `--release`/"
        f"`--gc` remove it either, so working in it would leave a tree nothing can reap. "
        f"`git worktree unlock {path}` to make it usable again"
    )


def _rebase_in_progress(wt_path: Path) -> bool:
    """Is a `git rebase` stopped mid-flight in this worktree?

    MESSAGE-ONLY, exactly like `_locked_refusal`'s marker comparison and for the same reason: the
    guards key on `branch is None` — the fact that makes a build tree unusable — and only ask this
    to choose WHICH recovery to name. So being wrong here costs wording, never a wrong refusal, and
    it must not be able to raise into a guard: an unreadable tree simply reports "no rebase" and
    gets the generic wording.

    BOTH backend directories, measured on git 2.50.1 — the same pair git's own `git status` checks.
    The default merge backend leaves `rebase-merge` (constructed with `git rebase origin/main
    --exec false`, i.e. this project's integration recipe stopped between replayed commits);
    `git rebase --apply` leaves `rebase-apply` (constructed with a first-commit conflict). Asked for
    BY NAME via `rev-parse --git-path` rather than assembled from `.git/worktrees/<n>/`, the same
    way `_last_activity` asks for the index: in a LINKED worktree those live per-tree and the
    mapping is git's to know. MEASURED both shapes — absolute inside a linked worktree
    (`…/.git/worktrees/task-540/rebase-merge`), relative in the main one (`.git/rebase-merge`) — so
    both are resolved against the tree rather than assumed.
    """
    for name in ("rebase-merge", "rebase-apply"):
        try:
            path = Path(_git_inspect("rev-parse", "--git-path", name, cwd=wt_path))
            if (path if path.is_absolute() else wt_path / path).exists():
                return True
        except (WorkspaceError, OSError):
            # OSError as well as WorkspaceError, for `_last_activity`'s reason: with cwd pointing
            # at a directory that is gone, subprocess.run raises a bare FileNotFoundError that
            # `_git` cannot convert (it only ever inspects `returncode`).
            return False
    return False


def _detached_build_refusal(root: Path, task_id: int, wt: dict, refusal: str) -> str:
    """The message for a BUILD worktree that is not standing on its own `task/<id>` branch.

    THE STATE (VMCP-86, constructed and measured in a throwaway repo, not reasoned about). A
    per-task agent runs SKILL.md's integration recipe — `git fetch origin && git rebase
    origin/main` — inside `task-<id>` and the turn running it is killed (session limit, API error).
    git detaches to `onto` BEFORE replaying anything and re-attaches `task/<id>` only at the END, so
    an interrupted rebase leaves the tree `git status`-CLEAN, DETACHED, with git's rebase state
    still on disk. Nothing in the tree's own shape says so, which is the whole bug: `_find` returned
    it, `created: false` said "here is your workspace", and the rulebook told the agent it was
    standing on its disposable branch.

    ONE message for BOTH refusals (`_ensure_locked` and `_release_locked`), with only the clause
    naming what was refused passed in. The diagnosis and the recovery are the same fact in both
    places, and two copies of a recovery drift — SKILL.md and this module have already had to be
    dragged back into agreement twice.

    WHY IT REFUSES RATHER THAN RECOVERS, and this is the load-bearing half. The recovery is `git
    rebase --continue` or `git rebase --abort`, and CHOOSING between them is not the tool's to make:
    `--abort` discards every commit the rebase had already replayed. That is the module's governing
    invariant ("housekeeping must never be how an agent's work disappears") applied to setup, the
    same call 514 made for a `locked initializing` tree — refuse loudly, name the two commands, let
    the agent that owns the work decide. It is also why this is not a "report it in the payload"
    warning: an agent that does not read the extra key commits onto a HEAD reachable from no ref and
    pushes it, and under-refusing there is silent while over-refusing is one legible error the pump
    already knows how to degrade around.
    """
    path = wt["path"]
    head = wt["head"]
    branch = BUILD_BRANCH.format(task_id=task_id)
    if _git_ok("show-ref", "--verify", "--quiet", f"refs/heads/{branch}", cwd=root):
        tip = _git("rev-parse", f"refs/heads/{branch}", cwd=root)
        where = (
            f"`{branch}` still points at {tip}, so the task's own commits are NOT lost — they are "
            f"on the branch, not on this HEAD"
        )
        back = (
            f"`git -C {path} log --oneline {branch}..HEAD` shows what only this HEAD names, and "
            f"`git -C {path} checkout {branch}` puts the tree back on its branch"
        )
    else:
        # the branch was deleted out from under the tree (a hand `branch -D`, or #517's release
        # that removed a tree and then failed to delete... in reverse). Then this HEAD is the ONLY
        # name for whatever was replayed, and "just check out the branch" would be advice that
        # destroys it.
        where = (
            f"`{branch}` does not exist any more, so this detached HEAD is the ONLY name for "
            f"whatever it holds — do not discard it before looking"
        )
        back = (
            f"`git -C {path} log --oneline` shows what it holds, and `git -C {path} checkout -b "
            f"{branch}` re-creates the branch on it"
        )
    if _rebase_in_progress(path):
        return (
            f"{path} is a build worktree stopped MID-REBASE — DETACHED at {head}, with git's own "
            f"rebase state still in place. That is what SKILL.md's integration recipe (`git fetch "
            f"origin && git rebase origin/main`) leaves behind when the turn running it is killed "
            f"between replayed commits. {where}. {refusal} Finish or undo the rebase IN THAT TREE "
            f"first, then ask again: `git -C {path} rebase --continue` (replay the rest) or `git "
            f"-C {path} rebase --abort` (back onto {branch}, discarding what was replayed). "
            f"Deliberately not chosen for you — `--abort` throws away replayed work"
        )
    return (
        f"{path} is a build worktree with a DETACHED HEAD ({head}) and no rebase in progress — a "
        f"build tree is CREATED on {branch} and is only ever taken off it by something that "
        f"stopped halfway (an interrupted rebase or bisect, a hand `checkout --detach`). {where}. "
        f"{refusal} Put it back on its branch before using it: {back}"
    )


def _find(root: Path, task_id: int, role: str) -> dict | None:
    name = (BUILD_NAME if role == "build" else REVIEW_NAME).format(task_id=task_id)
    target = worktree_root(root) / name
    for wt in list_worktrees(root):
        if wt["path"] == target:
            return wt
    return None


def _check_role(role: str) -> None:
    """The CLI is protected by argparse `choices`; the Python API the pump (and Task 4's
    gc_workspaces) calls directly is not — an unchecked role would silently branch build/
    review logic on `role == "build"` with anything else falling into "review"."""
    if role not in ("build", "review"):
        raise WorkspaceError(f"unknown role {role!r} — must be 'build' or 'review'")


def _ensure_locked(root: Path, task_id: int, role: str, at: str | None) -> dict:
    _check_role(role)
    _git("worktree", "prune", cwd=root)
    # the ONE network call in this module, and it runs with the repo lock already held — hence
    # the tight bound rather than the local ceiling (see _GIT_NET_TIMEOUT)
    _git("fetch", "origin", cwd=root, timeout=_GIT_NET_TIMEOUT)
    wt_root = worktree_root(root)
    wt_root.mkdir(parents=True, exist_ok=True)
    base = f"origin/{default_base(root)}"

    existing = _find(root, task_id, role)
    if existing is not None:
        if existing["locked"]:
            # REFUSE, never reuse and never remove — same shape as the review-pinning refusal
            # below, for the same reason. `git worktree add` killed mid-checkout leaves an entry
            # that IS listed (so `_find` returns it) and that `prune` will NOT drop, so this
            # early-return used to hand back a directory containing nothing but `.git` as
            # `created: false`; the agent dispatched into it stands in a tree whose files are
            # missing and whose index is all staged deletions, and it commits from there.
            # Since `_GIT_TIMEOUT` landed, our OWN timeout can manufacture that state — no
            # external killer required — so this is a reachable path, not a theoretical one.
            #
            # Gated on the BOOL, not on the reason: a lock we cannot explain is still a tree we
            # cannot vouch for, and over-refusing degrades the pump to one slot with a legible
            # error (SKILL.md's "не завелось — цикл НЕ роняем"), while under-refusing silently
            # produces work built on an absent tree. Asymmetric, so fail toward the refusal.
            # And do NOT self-heal it by unlocking + force-removing: the partial checkout may be
            # the only trace of what killed the add, and "housekeeping must never be how work
            # disappears" applies to setup exactly as it does to reaping.
            raise WorkspaceError(_locked_refusal(task_id, role, existing))
        if role == "build" and existing["branch"] is None:
            # VMCP-86, and the information was ALREADY HERE: `list_worktrees` has always parsed
            # the porcelain's `detached` and left `branch` at None — this early-return simply
            # copied that None into the payload and called it a workspace. A build tree is created
            # on `task/<id>` and nothing in this module ever takes it off; detached therefore means
            # something stopped halfway, and the commonest something is this project's own
            # integration recipe interrupted mid-rebase (see _detached_build_refusal).
            #
            # Gated on `branch is None`, NOT on the rebase probe — same split as the lock guard
            # above, for the same asymmetry. What makes the tree unusable is that it is off its
            # branch; whether a rebase is still in progress only refines the message. Refuse on the
            # fact, phrase from the probe.
            #
            # `role == "build"` is the whole condition on the other side: a REVIEW tree is detached
            # BY DESIGN (`worktree add --detach`), so this must never fire there — the payload just
            # below deliberately reports `branch: None` for it.
            raise WorkspaceError(_detached_build_refusal(
                root, task_id, existing,
                refusal=f"Refusing to hand it back for task {task_id} (build): a caller that is "
                        f"told it stands on its disposable branch would commit onto a HEAD "
                        f"reachable from no ref, and its `git push origin HEAD:main` would push "
                        f"the replayed commit rather than the branch's work.",
            ))
        payload = {
            "role": role, "task_id": task_id, "path": str(existing["path"]),
            "branch": existing["branch"], "created": False,
        }
        if role == "review":
            # Review Critical 1 — the only bug on this branch that produced a WRONG VERDICT
            # rather than noise. This early-return fires before the role branch below, so `at`
            # used to be discarded in silence AND the payload carried no "head" (the created
            # one does): round 2 of a review asked for the fix's sha, got a tree still pinned
            # at the PRE-FIX sha, and nothing in the response said so — the reviewer read the
            # old code and approved it. The trigger is a state this module deliberately
            # preserves: a reviewer that commits notes inside its detached tree can never
            # release it (the reachability guard below refuses, correctly) and --gc cannot reap
            # it either, so review-<id> persists and poisons every later round for that task.
            #
            # REFUSE, never re-point: moving a detached HEAD (`checkout --detach <at>`) is
            # itself a destruction path — it would orphan exactly the in-tree commit the
            # reachability guard exists to protect. "push OK -> remove, push FAIL -> KEEP"
            # says housekeeping must never be how work disappears; the same holds for setup.
            payload["head"] = existing["head"]
            # `at^{commit}` and not a bare `at`: rev-parse ECHOES BACK a full 40-hex sha with
            # exit 0 without checking the object exists, so a bare comparison would silently
            # pass on garbage; the peel also makes an annotated tag comparable to a commit sha.
            if at is not None and _git("rev-parse", f"{at}^{{commit}}", cwd=root) != (
                existing["head"]
            ):
                raise WorkspaceError(
                    f"review tree for task {task_id} is pinned at {existing['head']} but --at "
                    f"asked for {at} — release it first ({existing['path']}); if --release "
                    f"refuses, it holds an in-tree commit that only a human should resolve"
                )
        return payload

    name = (BUILD_NAME if role == "build" else REVIEW_NAME).format(task_id=task_id)
    path = wt_root / name
    if path.exists():
        raise WorkspaceError(
            f"{path} exists but is not a registered worktree — refusing to clobber it"
        )

    if role == "review":
        _git("worktree", "add", "--detach", str(path), at or base, cwd=root)
        return {
            "role": "review", "task_id": task_id, "path": str(path),
            # "head", NOT "detached" — list_worktrees's "detached" is a BOOL (git's own
            # porcelain vocabulary); this is a SHA. Two producers must never reuse one key
            # for two different meanings (it "worked" only because a hex string is truthy).
            "branch": None, "head": _git("rev-parse", "HEAD", cwd=path), "created": True,
        }

    branch = BUILD_BRANCH.format(task_id=task_id)
    if _git_ok("show-ref", "--verify", "--quiet", f"refs/heads/{branch}", cwd=root):
        # the branch outlived its tree (crashed agent) — reattach, NEVER recreate: it carries
        # the unfinished commits the resume agent is coming back for
        _git("worktree", "add", str(path), branch, cwd=root)
    else:
        _git("worktree", "add", "-b", branch, str(path), base, cwd=root)
    return {
        "role": "build", "task_id": task_id, "path": str(path),
        "branch": branch, "base": base, "created": True,
    }


def _last_activity(wt_path: Path) -> float | None:
    """Newest NON-FUTURE mtime of the two footprints a WORKING agent leaves in a worktree — the
    input to the grace window (`_REAP_GRACE_SECONDS`). None when NEITHER can be read, which the
    caller must treat as "no opinion" and fall through to the ordinary guards: a directory that is
    already gone has nobody standing in it, and a silent skip that can never expire would leak a
    tree with no report at all.

      * the worktree DIRECTORY — entries created or removed at its top level (a new file, the
        `.pytest_cache` a verification run drops) and `git worktree add` itself, so a
        just-created tree is young by construction. Nothing gc does touches it.
      * its INDEX — every `git add`/`commit`/`rebase`, which is the footprint that matters here:
        a task cannot reach Review without a commit, so this is what is fresh at the moment the
        tree starts reading dead. Asked for BY NAME (`rev-parse --git-path index`) rather than
        assembled from the basename: git derives `.git/worktrees/<n>` from the directory name and
        disambiguates collisions itself, so that mapping is git's to know, not ours to guess.
        MEASURED: it may not EXIST — a half-created tree (`locked initializing`) is killed before
        git writes one, which is also why `git status` there reports every tracked file as a staged
        deletion — so each marker is stat'ed independently and a missing one simply does not vote.

    MEASURED on git 2.50.1, and the reason every git call the sweep makes inside a tree goes
    through `_git_inspect`: `git status --porcelain` REWRITES the index every single time, even in
    a clean tree. Until VMCP-90 that made gc's own inspection indistinguishable from an agent's
    footprint here — a tree gc had just refused read as freshly touched on the next sweep and was
    skipped by the window, silently, so a standing `kept` line surfaced about once per window
    instead of every tick. gc now takes no optional locks, so a fresh mtime on either marker means
    what it says: somebody OTHER than the sweep wrote here. Both markers stay — the fix is that gc
    stopped writing, NOT that this function stopped looking (dropping the index would blind it to
    exactly the hour-old tree whose only fresh footprint is the commit it just made).

    WHY THE MAX IS TAKEN OVER NON-FUTURE MARKERS ONLY (VMCP-84). An mtime in the FUTURE — clock
    skew, a restored backup, an unpacked archive — is not evidence of anything, and the caller
    deliberately refuses to honour one (its `0 <=` bound; a future value would otherwise read as
    "younger than N" on every sweep forever, and that skip is silent). But that refusal is decided
    on the value THIS function returns, so a plain max let one skewed marker MASK the other:
    constructed and measured on this code, a tree with a future directory mtime and an index the
    agent had just written was reaped — and so was the mirror case (future index, fresh directory).
    Two stats, and the useless one won. Dropping future markers before the max means a bad clock
    reading can no longer suppress a good one; nothing else moves, because a future value never
    survived to mean "young" in the first place.

    WHEN EVERY MARKER IS FUTURE there is no good one left, and this returns the future value ANYWAY
    rather than `None`. Both fall through to the ordinary guards, so the sweep behaves identically
    — but `None` means "no opinion" and would make the caller's `0 <=` bound unreachable, i.e.
    deletable with the whole suite still green. The bound stays the thing that decides that case;
    this function only stops letting it decide the OTHER case too.

    `now` is sampled AFTER the stats, never before: a marker an agent writes DURING this read would
    otherwise be compared against a `now` from before it existed, land in the future, and be
    discarded — throwing away the freshest evidence there is. Sampled last, every write that really
    happened precedes it.

    COST, since the sweep holds the repo-wide flock throughout: two stats and one local `rev-parse`
    per DEAD tree — live trees short-circuit before this is ever called — against a board read the
    same lock already covers.
    """
    candidates = [wt_path]
    try:
        index = Path(_git_inspect("rev-parse", "--git-path", "index", cwd=wt_path))
        candidates.append(index if index.is_absolute() else wt_path / index)
    except (WorkspaceError, OSError):
        # OSError as well as WorkspaceError: with cwd pointing at a directory that no longer
        # exists, subprocess.run raises a bare FileNotFoundError that `_git` cannot convert (it
        # only ever inspects `returncode`). Such an entry IS still listed — git refuses to prune a
        # LOCKED one — so this is reachable, and the directory mtime below fails the same way.
        pass
    mtimes: list[float] = []
    for candidate in candidates:
        try:
            mtimes.append(candidate.stat().st_mtime)
        except OSError:
            continue
    if not mtimes:
        return None
    now = time.time()
    real = [mtime for mtime in mtimes if mtime <= now]
    return max(real) if real else max(mtimes)


# WHAT `git status --porcelain` CANNOT SEE, AND WHY IT IS A HOLE IN THIS MODULE'S OWN INVARIANT
# (VMCP-185). The header of this file promises "push OK -> remove, push FAIL -> KEEP … housekeeping
# must never be how an agent's work disappears", and the dirty guard below is half of how that is
# kept. But plain `--porcelain` does not report IGNORED paths at all, so for that guard a tree
# holding nothing but ignored files is CLEAN. MEASURED (real git 2.50.1, a bare origin, a throwaway
# tree): a dead build tree with everything committed and pushed (`status --porcelain` empty,
# `origin/main..HEAD` empty) plus `secrets.env` and `scratch/notes.txt` on disk was released by
# BOTH paths — `--release` returned `{"released": true}` and `--gc` put it in `released` — the
# directory and both files gone, and NOTHING in `kept`, `expected` or `warning` said so. Untracked
# but NON-ignored files (`??`) the guard does see and does hold on, so the hole is exactly the
# ignored ones.
#
# IT IS NOT HYPOTHETICAL, AND THE REAL EXPOSURE IS NOT THE ONE IT LOOKS LIKE. `.vikunja-mcp.env`
# (this repo's token) and `.playwright-mcp/` both live in the MAIN checkout, which nothing here
# ever removes — measured across the four live worktrees on this machine, all four had neither.
# What IS at risk sits in the per-task tree, and SKILL.md PRESCRIBES writing it there: its browser
# recipes produce `shot-<id>.png` in the agent's own worktree (ignored by this repo's `*.png`) and
# `--output-dir .playwright-mcp/<id>` under it (ignored wholesale). Measured on a stand carrying
# this repo's real ignore rules: both were destroyed by a `released: true`, silently.
#
# WHY THIS ONLY REPORTS AND NEVER HOLDS — the alternative was measured and rejected. Making the
# guard `--porcelain --ignored` refuses a tree that holds ANY ignored path, and the mandated gate
# (`uv run pytest`) CREATES `.venv` on its first invocation, so every build tree that ran the gates
# is permanently "dirty": measured live, 3 of 3 build trees (7, 6 and 2 ignored entries — all of
# them `.venv/`, `__pycache__/`, `.ruff_cache/`, `.pytest_cache/`) and 0 for the one review tree
# that never ran anything. `--gc` would stop reaping ANYTHING, trees would pile up, and the next
# human would turn the guard off outright. A destroy-only-with-a-flag variant collapses into the
# same two ends: unset, nothing is ever reaped; always set, it is today's behaviour with a longer
# argv. So the tree is still removed, and what changes is that the removal STOPS BEING SILENT.
#
# SAY THAT PLAINLY RATHER THAN OVERSELL IT: naming a loss is not preventing one. The work is gone
# either way; only the silence is fixed.
#
# WHETHER THE GUARD SHOULD ALSO HOLD IS ANSWERED, AND THE ANSWER IS NO. That is a HUMAN's decision
# on VMCP-221 (764), the card 710 filed to ask it, recorded here 2026-08-10 as FINAL so it stops
# being re-opened — it had already been restated as an open question in four places. Report, never
# hold. Both alternatives were put to that human with their prices and both were declined. HOLDING
# on unrecognised ignored content pays with a permanently non-empty `kept` the day the filter below
# goes out of date, which is the never-read-signal disease #516 had to split `kept` in two to cure,
# and the human who meets it turns the guard off outright. SALVAGING — copy the unrecognised paths
# aside, then reap as today — escapes that paralysis, but adds a third refusal branch to the one
# module in this package that runs git at all, and leaves a dump nothing prunes. Re-open this on a
# NEW measurement that moves those prices, and on nothing else.
#
# THE PRICE IS ACCEPTED WITH ITS NAME, because a decision recorded without its cost is one that
# gets re-litigated. Two things are bought. Agent work that is ignored and unrecognised is still
# DESTROYED: the field is a post-mortem, and the paths in it are gone rather than saved. And that
# report does not even bound the loss from BELOW — `--ignored` collapses an ignored DIRECTORY into
# one entry, so a file left inside `.venv/` dies with no mention of its own (measured). The
# one-way reading of the field, spelled out above, is a consequence of this accepted price and not
# a separate caveat. What stands between an agent and this loss is the RULE and not this code:
# SKILL.md's standing instruction to carry anything needed AFTER the task out of the tree BEFORE
# `advance`. Paraphrased and not quoted, deliberately — the rulebook says it in Russian, and an
# English sentence in quotation marks would send a reader looking for a string that is not there.
#
# THE FILTER BELOW IS A LIST, AND A LIST ROTS — SO IT WAS PUT WHERE ROT IS CHEAP. It decides only
# what gets REPORTED, never what gets removed. A build tool this set has never heard of appears ->
# its directory is not recognised -> it is named in `removed_ignored` -> one noisy line in a
# `released` entry, and the reaper keeps reaping. That is the whole cost of it being out of date.
# The DANGEROUS direction is the other one: a name ADDED here is a class of file this module will
# destroy without a word again, so add only what is reproducible by construction (a virtualenv, a
# bytecode or tool cache, an npm install) and never something an agent AUTHORS. `.playwright-mcp/`
# and `.vikunja-mcp.env` are deliberately absent and pinned absent by test_the_detritus_filter_
# does_not_cover_what_an_agent_authors. Same fail-toward-shouting direction as `_keep_is_expected`:
# unrecognised means REPORTED.
#
# **THAT PIN IS EXACTLY TWO NAMES WIDE — do not read it as a guard on the direction.** Measured by
# an independent second pass: adding `.playwright-mcp` to this set fails 2 tests, while adding
# `dist`, `build`, `out`, `artifacts`, `screenshots` in one go fails NONE — and those are precisely
# the names an agent parks authored output under. A test that pinned the whole direction would have
# to enumerate the complement of this set, which is not a thing; so what stands between a future
# widening and a silent loss is this paragraph, not the suite. Two further measured qualifications.
# The matching is CASE-SENSITIVE (`A.PYC`, `.ds_store` are NOT recognised), which fails open — they
# get reported — and so is only noise, but on a `core.ignorecase=true` checkout they are the same
# files this set recognises in lower case. And `.claude/*` is NOT here although this repo ignores
# it: measured, a worktree session writing `settings.local.json` or `mailbox/` would then put the
# field on EVERY released entry, which is the never-read signal this filter exists to avoid — none
# of the four live trees held one on 2026-08-03, so it is a risk rather than a defect, and the
# cheap failure (a noisy line) is the one this design deliberately buys.
_REPRODUCIBLE_IGNORED_DIRS = frozenset({
    ".venv", "venv",                 # measured: `uv run` creates it on the first gate command
    "__pycache__",                   # measured in 3 of 3 live build trees
    ".pytest_cache", ".ruff_cache", ".mypy_cache", ".tox",
    "node_modules",
})
_REPRODUCIBLE_IGNORED_LEAVES = frozenset({".DS_Store"})
_REPRODUCIBLE_IGNORED_SUFFIXES = (".pyc", ".pyo")
# The report is BOUNDED, and the bound is about the CONSUMER, not about tidiness: `--gc` runs
# unattended and its one JSON line is read by a hub process. Measured by the second pass — 3000
# loose ignored files at a tree root produce 3000 entries and a 50,133-byte line, and a sibling
# project has already lost a daemon session to an oversized read (hgdev-acp, a >24.5 KiB log pull).
# **What the cap bounds is the NUMBER of names and NOT the size of the line, so it does not by
# itself hold the payload under that read** — measured for VMCP-249 (840): 50 names, the cap's own
# maximum, of 800 characters each are 40,000 characters of NAMES, a 40,200-byte JSON array on their
# own, against the 25,088 bytes that ">24.5 KiB" is the FLOOR of — that read's true size is not
# recorded here, so this comparison holds a fortiori and 25,088 is a threshold, not the read. **The
# NAMES are the figure on
# purpose, and round 1 of that card wrote the WHOLE payload's total instead (40,440 bytes), which is
# not a property of this code at all**: the payload carries `path`, so the total moves with the
# length of whatever temp directory the stand used — measured, the same content under two temp roots
# differing by 41 characters gave totals differing by exactly 41 bytes — and the card's review
# records three stands producing three different totals. Reachable as well as arithmetical: git
# prints an 800-character entry whole (measured, one entry of exactly 800 characters, with a tracked
# anchor at each level so git enumerates instead of collapsing — see the collapse paragraph below).
# Whether a BYTE budget belongs beside the entry budget is filed as VMCP-260 (862) rather than
# guessed at here; what is fixed is that the justification above no longer overstates its reach.
#
# Past the cap the entry also carries `removed_ignored_truncated`, so the COUNT survives even when
# the names do not — and what that count IS is `len(destroyed)`: the length of the list AFTER the
# filter above and AFTER whatever collapse git applied (a directory THIS CALL's `git status`
# reported WHOLE is ONE entry, and which those are is the paragraph below), taken before the
# cap. **It is NOT the size of the loss, and calling it the "TRUE total" here was wrong** (VMCP-249
# again, which settled it by construction rather than by preferring a wording): a tree holding
# `.venv/` with 100 files (ONE entry, filtered away), `.playwright-mcp/840/` with 30 (ONE entry, and
# the entry git prints is the PARENT `.playwright-mcp/`) and 57 loose `*.png` destroys 187 ignored
# files and reports 58. So it inherits exactly the blindness of the key it sits beside, which is
# what `_add_capped` already says of `overwritten_ignored_truncated`.
#
# AND IT IS A BOUND IN NEITHER DIRECTION. The tempting repair — "then read it as a LOWER bound on
# the loss" — is false as well, and an independent pass refuted it the same way: 51 sibling ignored
# directories each holding one symlink pointing OUT of the tree report 51 with ZERO ignored regular
# files destroyed, the targets surviving intact. An entry is a POSITION git chose to print, not a
# file. Nor is the coincidence about directories being absent: one ignored directory holding exactly
# one file plus 50 loose files reports 51 against 51 files (measured).
#
# **NO EXACT CONDITION FOR COINCIDING IS STATED HERE, and the absence is a decision: round 1 of
# VMCP-249 (840) stated one and it was neither half of a criterion.** It said no printed entry
# stands for more than one destroyed file AND no filtered entry stood for any. NOT SUFFICIENT: an
# entry may stand for ZERO destroyed files, and zero satisfies "no more than one" — 3 ignored
# symlinks pointing OUT of the tree plus 2 ignored regular files under `*.png` give 5 entries with
# nothing filtered, a count of 5 against a loss of 2 files (measured, all 3 targets alive
# afterwards), which is the symlink fact the same paragraph states above it — so that round refuted
# its own condition without leaving the paragraph. NOT NECESSARY either, because the errors
# CANCEL: a filtered `.venv/` holding one hand-authored file — a filtered entry that DID stand for
# a loss — plus one ignored symlink give 2 entries, 1 of them filtered, a count of 1 against a loss
# of 1 file (measured). Tightening "more than one" to EXACTLY one removes the FIRST of those two and
# leaves the cancellation standing. It is deliberately NOT called sufficient either, because
# sufficiency needs a COVERAGE LEMMA nothing here establishes — that every destroyed ignored regular
# file falls under exactly one entry of the post-filter list — and an independent pass broke that
# lemma on round 1's own set in one construction: 50 loose `*.png` at the root plus an untracked
# ignored directory of 30 sorting past the cap, where all 50 PRINTED entries stand for exactly one
# file each, nothing is filtered, and the count is 51 against a loss of 80. Two further shapes break
# the lemma in `git status` outright, and **round 2 wrote that `release_workspace` RAISES on BOTH so
# that neither reaches this key — which is true of only ONE of them** (VMCP-249 round 3, built end
# to end rather than inherited). A directory git cannot read: `status` warns and omits it, then
# `git worktree remove` fails "Directory not empty" and this function RAISES, so nothing is
# reported. A submodule: true only while it is POPULATED, where `remove` refuses outright ("working
# trees containing submodules cannot be moved or removed"). UNINITIALISED it did not raise at all —
# and uninitialised is exactly what `ensure_workspace` leaves, since `git worktree add` does not
# init submodules. Measured then: a `sub/` gitlink whose directory the new worktree leaves EMPTY,
# one ignored `sub/EVIDENCE.png` written into it and 60 loose ignored `*.png` beside it — the
# superproject's `status --porcelain --ignored` says NOTHING about `sub/`, the release returned
# `released: True` with `removed_ignored_truncated` 60 and 50 names, and `EVIDENCE.png` was in
# neither: 61 ignored files destroyed, 60 counted, one of them unnamed, uncounted and unraised.
# **THE SUBMODULE HALF OF THAT PAIR NO LONGER REACHES THIS KEY, and the past tense above is
# VMCP-266 rather than a re-measurement of the same code**: a gitlink whose directory holds
# anything — `EVIDENCE.png` included — is now refused before the removal with
# CODE_POPULATED_GITLINK, so that input returns `released: false` and destroys nothing. What was
# a lemma-breaking shape is a guarded one, in BOTH submodule states (populated and not), and it
# is the only one of the two that moved: a directory git cannot READ still breaks the lemma
# exactly as described above, and still by raising. The gitlink was never the interesting half
# for this key anyway — its casualty there was IGNORED, while what forced the refusal is that the
# same blindness swallows untracked-and-NOT-ignored content the dirty guard promises to hold.
# Latent HERE (this repo has no gitlink) and ordinary in the consumer checkout `--gc` runs in. So no
# "iff" is on offer and this comment does not offer one; a criterion would be unusable anyway, since
# checking either conjunct needs the tree, which is gone by the time anything reads this key.
# "PRINTED entry" was the wrong SET as well: the count is `len(destroyed)`, the POST-FILTER list,
# while the printed names stop at 50, and on the 187-file
# input above those two are 58 against 50. What the card established needs none of it — this is a
# count of ENTRIES and it bounds the loss in NEITHER direction. (What that round cited for the
# ordinary case, VMCP-185's live sample, does not support it either: that sample counted ENTRIES —
# 7, 6 and 2 — not files inside `.venv/`. No sample is needed, but note what the replacement rests
# on: not the mechanism, which allows an ignored directory entry standing for ZERO destroyed regular
# files (contents only a symlink out), but what `uv` actually writes. An entry `.venv/` appears only
# when the directory is non-empty (measured: an ignored EMPTY directory yields no entry at all) and
# is then filtered away, and a `uv` venv here is on the order of 1.6-1.8 THOUSAND regular files:
# 1625, twice, in two independent checkouts at `1fb0082`, against the 1811 and 1624 round 2
# recorded without naming any tree. So the digits belong to the venv rather than to the tree and
# the load-bearing part is only that it exceeds one — hence a build tree that ran the gates has a
# count BELOW its loss. (Round 2 called that "thousands", which 1.6k is not, and gave its pair no
# sha; both are corrected here, the second because this repo's own rule is that a figure over a
# tree carries the tree — and note the correction does NOT retro-anchor round 2's two numbers,
# which were taken on a tree nobody recorded.))
#
# Absence of the key means the LIST was not truncated, and NEVER that nothing beyond the names was
# destroyed: under the cap the same collapse is still in force (measured, 2 entries named against 32
# ignored files destroyed), so the ONE-DIRECTION reading below survives the cap unchanged in both of
# its halves. Tripping the truncation KEY takes 51 non-reproducible ENTRIES, and only entries —
# re-measured, 50 entries leave the key ABSENT and 51 make it 51. (Said of the KEY on purpose: at 50
# the printed LIST is already at its cap, so "reaching the cap" would name the wrong threshold.)
#
# **Whether a directory becomes ONE entry is decided PER DIRECTORY, and what those criteria decide
# is the REPORT: git ENUMERATES one the index holds a path under, and one holding anything it must
# report separately (an untracked file that is not ignored). Being ignored does not by itself
# collapse a directory, and neither does being wholly untracked.** Read that as a property of THIS
# CALL and not of git — the third lever is the one `_inspect_status` pins below, and round 2
# asserted the two above as if they were the whole of it. Measured on ignored `d/` with 60 files and
# NEITHER of them present: `--porcelain --ignored` gives ONE entry, and `-uall` — equivalently
# `status.showUntrackedFiles=all`, which is config anyone can set — gives 60. What holds the
# condition true here is `_inspect_status` forcing `status.showUntrackedFiles=normal` on this single
# command, added by #766 for exactly that reason. All of that is measured, and the shape that
# kills the tidier phrasings is a directory whose own files are listed INDIVIDUALLY beside a
# subdirectory reported WHOLE: ignored `d/` with a TRACKED `d/anchor.txt` gives `['d/f0.txt',
# 'd/f1.txt', 'd/f2.txt', 'd/y/']` — the files at that level one by one, the subdirectory holding no
# indexed path still ONE entry — and moving the anchor DEEPER, to `d/x/anchor.txt`, prints exactly
# the same four. **That said "a walked directory with an UNWALKED child" until VMCP-274 (897): the
# entry COUNT was never the wrong part, the mechanism under it was.** Re-measured under this same
# call on git 2.50.1 (Apple Git-155), on that same shape: an EMPTY `d/y/` prints no entry at all; a
# file two levels down at `d/y/z/q` still prints exactly the one `!! d/y/`; and `chmod 000 d/y`
# makes that entry VANISH behind `warning: could not open directory 'd/y/'` at rc 0 (`chmod 700`
# brings it back). What the entry tracks is whether git REACHED A FILE down there, NOT whether it
# opened the child: at `chmod 400 d/y` — readable, not traversable — the entry is gone while the
# warning names the GRANDCHILD `d/y/z/`, which git could only learn by reading `d/y`, and putting a
# FILE directly into `d/y` at that same mode brings the entry back with no warning at all. With
# nothing indexed under `d/`, the collapsed `!! d/` goes the same way when its only child is
# unreadable. **Do NOT invert any of that into "the walk does not enter into it" — that was this
# card's own first draft, and a nested REPOSITORY refutes it:** `d/nested/` carrying its own `.git`
# is ONE entry while `chmod 000 d/nested/sub` draws NO warning, where the identical tree without
# that `.git` warns. In THAT cell the retracted wording is literally true; what stays false is
# reading it off the entry count on the shape above.
# `test_gitignore_still_lets_the_settings_file_through` in `test_repo_browser_isolation.py` made
# this same correction FIRST and from these same cells, and SKILL.md's Russian twin is narrowed in
# the same commit as this paragraph — which is what the DEFINITIONAL split below asks for. That
# docstring's own closing generalises further than the nested-repository cell allows, and is left
# for its own card rather than widened into this one. "Wholly untracked" is too weak
# for a second reason: an untracked, non-ignored file inside gives `?? notig/` BESIDE the individual
# `!! notig/a.png`. (That shape cannot reach a release — a `??` line is what makes the tree dirty
# and the release refuse — so what bites in practice is the indexed path.) Round 1 of the same card
# had it as one ignored directory contributing ONE entry however full, so that no amount of content
# inside a single directory gets there; both halves are false. Give ignored `d/` one TRACKED file
# (`git add -f`) and its 60 ignored files sitting AT THAT LEVEL print one by one, so ONE ignored
# directory trips the key by itself: measured, 60 entries and the key at 60, against ONE entry `d/`
# and NO key for the same 60 files without the anchor. That shape is this repo's own `.gitignore`
# rather than a contrivance: `.claude/*` beside a re-included `!.claude/settings.json`, which is
# what lets that file be TRACKED without a force-add, and the tracked file is then what makes git
# ENUMERATE the directory — measured on those real rules, 3 entries out of that prefix instead of 1.
# Attribute it to the TRACKED file and not to the `/*` spelling — but only so far, and **round 2's
# "the two spellings are INDISTINGUISHABLE for entry counts" is false in one of its two arms**
# (VMCP-249 round 3, the full 2x3 matrix, same content in every cell). TRACKED: `.claude/*` with the
# `!` line gives 3 and blanket `.claude/` with the same file force-added also gives 3, so there the
# spellings really do agree. ABSENT from disk: both give 1. But ON DISK AND UNTRACKED they DIVERGE —
# `.claude/*` gives 3 (plus `?? .claude/`) against blanket `.claude/`'s 1 — because the `!` line
# un-ignores `settings.json`, which is the SECOND enumeration trigger named above, the `notig/`
# shape verbatim. Round 2 laid exactly that charge at round 1 four paragraphs up ("refuted its own
# condition without leaving the paragraph") and then did it. Nothing behaves wrongly: that cell is
# `??`, so it makes the tree dirty and the release refuses. What must not be repeated is the
# UNIVERSAL — the spellings agree when the re-included file is tracked or absent, not always. The
# tree ROOT is never collapsible at all,
# and **not because it holds the tracked files** — that reason was this round's own first draft and
# a measurement killed it: on a worktree whose HEAD tree is EMPTY, 3 ignored files at the root still
# print as 3 entries while an ignored SUBDIRECTORY beside them prints as one, so what protects the
# root is that git's scan STARTS there. Hence 51 loose ignored files at the root are 51 entries and
# trip the key (measured; the same 51 inside an untracked directory are ONE entry). WHICH of these
# refutes round 1 depends on how "a single directory" there is read, and both readings are covered
# rather than one being picked: read broadly, as any one directory, the root does it — and that is
# the input of `test_the_report_is_capped_but_the_count_is_not` next door, so a PASSING test in the
# file that round edited was already the counterexample; read narrowly, with the antecedent being an
# ignored directory that collapses to ONE entry, the root is not such a directory at all and what
# refutes it is the tracked-anchor case above. 51 entries is therefore reachable by anything — loose
# files, 51 SIBLING directories (measured), or a mix; "it needs thousands of loose FILES" was too
# narrow and "no amount of content inside a single directory" too strong.
#
# **THE OTHER SITES SPLIT IN TWO, and an earlier draft of this very paragraph got the split wrong by
# asserting they were all of one kind.** The census is `git grep -E 'collaps|схлопн|схлопыва'` — and
# note the Russian needs BOTH stems, a one-stem grep being what hid the twin below. DEFINITIONAL
# uses, saying what the NUMBER IS, carry the universal: the paragraph above and its Russian twin in
# SKILL.md, both narrowed rather than one being left to disagree with the other, which is the whole
# point of the card. The rest are EXISTENTIAL — they say only that a loss CAN be hidden, and their
# CONCLUSIONS survive whatever git walks into: `_is_reproducible_ignored` and the emit site below,
# `_ignored_paths_the_ff_will_overwrite` further down, sites in `test_workspace_cmd.py`, CLAUDE.md,
# and SKILL.md's «ЧЕГО ЭТО ПОЛЕ НЕ ЛОВИТ». **Their conclusions, NOT their sentences — round 2 wrote
# that they "stay true whatever git walks into" and several of them state the refuted universal
# verbatim, `.venv/MEASUREMENTS.md` arriving as the entry `.venv/`.** Measured: give `.venv/` a
# TRACKED `pyvenv.cfg` and the entries are `!! .venv/MEASUREMENTS.md`, `!! .venv/lib0.py`, … — per
# file, no `.venv/` entry at all — while `removed_ignored` is still absent, because
# `_is_reproducible_ignored` matches on any `.venv` COMPONENT and drops each one. The file still
# dies unnamed, so every one of those sites is right about what happens; the MECHANISM they name is
# the filter, not a collapse. That is the same standard round 2 applied to the two sites below, and
# did not apply to these. Counting them is also not a census to lean on: the grep returns five hits
# in `test_workspace_cmd.py`, not three, and one of them is definitional without being false. What
# is NOT touched, and is filed instead of widened: `.gitignore` and
# `test_repo_browser_isolation.py` EXPLAINED this repo's `.claude/*` spelling with "git does not
# descend into an excluded directory" until VMCP-264 (874) corrected the reason in BOTH. Measured
# here, that quotation is now absent from both of those files — read WHOLE and whitespace-flattened,
# because where it does survive it is line-broken and a per-line grep returns nothing — so it lives
# on only in this sentence quoting it, and a reader who greps for it cannot tell a stale pointer
# from a bad grep. The measurements above contradicted it as a universal and their CONCLUSION
# always survived: the re-include is blocked at the PATTERN level. Under blanket `d/` plus
# `!d/keep.txt`, `check-ignore --no-index -v` attributes the path to `.gitignore:1:d/` and a plain
# `git add` refuses it, while under `d/*` the same `!` line wins and the add succeeds; `--no-index`
# because on a TRACKED path the bare form is rc 1 printing NOTHING, which reads like "not ignored".
# So the SPELLING rule there was right and only its stated reason was too strong. What 874 put in
# its place keeps THREE questions apart — whether git OPENS the directory, how many ENTRIES it
# reports, and which pattern WINS — and only the third decides that file; neither universal about
# the walk may be promoted out of it. `.gitignore` is the source for the OPENING cells by its own
# division of labour, and records them as joint with the CALL and the SPELLING — that one is cited,
# not re-measured here. What IS measured here is the other side: the collapse paragraph above has
# the nested REPOSITORY git really does not descend into.
_MAX_REPORTED_IGNORED = 50
# And a SECOND budget, in BYTES, because the first one does not bound what the consumer reads —
# VMCP-260 (862), the card the paragraph above filed, answered by a human choosing a byte budget
# over narrowing the justification. Re-measured here at `1a26130` with the real
# `release_workspace` on the card's own construction (a tracked anchor at each level so git
# ENUMERATES, 60 ignored `*.png` at 800-character paths): 50 names, the entry cap's own maximum,
# came out as a 40,200-byte array on a 40,376-byte line — 1.6x the 25,088 bytes that ">24.5 KiB"
# is the FLOOR of. Nothing was violated; the entry cap was simply never what stood between that
# line and the hub.
#
# 1,568 is 25,088 // 16, and the division IS the justification — a round number picked by eye is
# what the card refused. The budget is PER KEY and one payload carries the shape several times
# (one `removed_ignored` per released worktree, up to two more under `main_checkout`), so EIGHT
# of them spend 12,544 bytes: half the floor, with the other half left for the frame and for
# whatever key comes next. Eight is twice the four trees this repo's `wip_limit = 3` drain
# produces.
#
# **SO IT BOUNDS ONE KEY AND NOT THE PAYLOAD, and stating that is the point rather than a
# caveat**: a per-list cut cannot bound a line assembled from lists that never see each other,
# and reading it as though it could is exactly the overstatement 862 was filed against. What it
# buys is that `N x B + frame` is an arithmetic bound with a SMALL B, where before B had no
# bound at all.
#
# The cost counted is the SERIALIZED one, `len(json.dumps(p))`, and not `len(p)`: the line is
# printed by `json.dumps` at its default `ensure_ascii=True`, so a Cyrillic path costs SIX bytes
# per character — measured, 100 Cyrillic characters are 100 chars, 200 UTF-8 bytes and 602
# serialized. Counting characters would understate by 6x, and counting UTF-8 bytes by 3x, the
# very thing the budget exists to hold down. The `+ 1` per entry is the separating comma; it
# over-counts the whole array by one byte, which is the safe direction.
_MAX_REPORTED_IGNORED_BYTES = 1568


def _cap_reported(paths: list[str]) -> list[str]:
    """`paths` cut to BOTH budgets: at most `_MAX_REPORTED_IGNORED` entries AND at most
    `_MAX_REPORTED_IGNORED_BYTES` of serialized names.

    THE FIRST ENTRY IS ALWAYS KEPT, even where it alone busts the byte budget. The alternative
    is a key PRESENT with an EMPTY list, which is the never-read field VMCP-68 had to split
    `kept` in two to cure and which would strip the one-way reading — key present ⇒ something
    unrecognised died — of the only thing it says. What that costs is bounded by the filesystem
    rather than by hope: one entry is one path, so the overshoot cannot exceed `PATH_MAX`
    (measured 1024 here), a twenty-fifth of the floor above.

    The CALLER decides what a short answer means. `len(out) < len(paths)` is the truncation
    signal, so a BYTE cut raises the same `<key>_truncated` sibling an ENTRY cut does — which is
    what the human's answer on 862 asked for, and it keeps that sibling's documented meaning
    (`len(paths)`, the length before either cut) unchanged.
    """
    kept: list[str] = []
    spent = 0
    for p in paths[:_MAX_REPORTED_IGNORED]:
        spent += len(json.dumps(p)) + 1
        if spent > _MAX_REPORTED_IGNORED_BYTES and kept:
            break
        kept.append(p)
    return kept


def _inspect_status(path: Path) -> tuple[list[str], list[str]]:
    """One `git status` call, split into (what the dirty guard counts, what is merely IGNORED).

    ONE call, not two: `--ignored` only ADDS `!! ` lines and leaves every other line byte-identical
    — measured on a tree carrying both kinds at once (`['M README.md', '?? plain.txt']` before and
    after, with `!! .playwright-mcp/` and `!! shot-8001.png` alongside). So the dirty guard keeps
    seeing exactly what it saw before, including its entry COUNT, which is user-visible in the
    refusal text. Cost of the wider walk on a real tree with a real `.venv`, 5 runs each: 17.6-25.6
    ms plain against 26.6-36.5 ms with `--ignored`; no extra git invocation at all.

    `_git_inspect`, like the call it replaces: this looks inside a tree we may end up refusing to
    touch, and it must not leave a footprint the grace window later mistakes for an agent's
    (VMCP-90). Re-measured for the wider walk: with `core.untrackedCache=true` and files under an
    ignored `.venv/`, both grace markers stay byte-identical, while the same command WITHOUT
    `GIT_OPTIONAL_LOCKS=0` moves the index mtime.

    BOTH HALVES WENT BLIND UNDER ONE GIT SETTING, and VMCP-223 (766) closes it here with the
    `-c status.showUntrackedFiles=normal` prefix below. With `status.showUntrackedFiles = no`
    (config, ANY level — repo, global, system; a linked worktree shares `.git/config` with the
    main checkout) the command prints NEITHER `??` NOR `!!` lines. Measured on a real bare origin
    plus a real worktree, BEFORE the prefix: a tree holding an untracked-and-NOT-ignored
    `REAL-WORK.txt` and an ignored `shot-766.png` returned the EMPTY STRING, the dirty guard
    passed, `release_workspace` answered `{"released": true}` with no `code`, no `warning` and no
    `removed_ignored` — and the file was gone. That is the module's own invariant ("push OK ->
    remove, push FAIL -> KEEP … housekeeping is never how an agent's work disappears") failing
    whole rather than at an edge, and `--gc` does it unattended, on every tick.

    WHY THIS IS A FIX AND NOT THE PRODUCT DECISION 710 WAS TOLD TO LEAVE ALONE, because the two
    look alike and the difference is the whole justification. VMCP-221 (764) asked whether `dirty`
    should be WIDENED to hold a tree for IGNORED files; a human ANSWERED it — no, report and never
    hold — so it is a settled decision rather than an open question, and the price that settled it
    is recorded with the filter near the top of this module. It deliberately does not widen, and
    widening would have held a tree that passed every gate by its own `.venv`. That decision is
    untouched here, in the sense that matters: this fix does not lean on it and does not revisit
    it. This one is the opposite direction: the guard
    already CLAIMS untracked-and-not-ignored, that claim is its entire reason to exist, and a
    performance knob was silently taking it away. Restoring a claimed scope is not widening one.
    Measured rather than argued: with the setting at its DEFAULT the prefix changes nothing —
    same refusals, same entry counts, same `removed_ignored` — so no tree that used to be
    released is held now. What changed is that the answer stopped depending on someone else's
    config.

    AND BOTH HALVES GO BLIND A SECOND WAY, UNDER A POPULATED GITLINK — where the conclusion #766
    left here ("the guard sees `??`, so the hole is exactly the paths this repo IGNORES") is not
    merely narrow, it is the wrong axis (VMCP-261 (863)). Rebuilt here on real git 2.50.1 rather
    than inherited: a superproject with a real submodule whose OWN `.gitignore` hides `build-out/`,
    a linked worktree, an explicit `git submodule update --init` inside it, and
    `sub/build-out/artifact.txt` on disk. All THREE reads from the worktree — `status --porcelain`,
    the same with `-c status.showUntrackedFiles=normal`, and `--ignored` — return the EMPTY STRING,
    so this function answers `([], [])` and the tree reads CLEAN; run `status --porcelain
    --ignored` inside `sub` itself and it says `!! build-out/`. The file is perfectly visible, to
    the only index that has ever heard of it. So under a gitlink the loss is NOT bounded by what
    THIS repo ignores: a file the SUBMODULE's rules hide dies the same way, and `removed_ignored`
    cannot name it either, because it reads this same call. Nor is ` M sub` a fallback — two config
    keys switch it off (`submodule.<name>.ignore = all`, `diff.ignoreSubmodules = all`, measured on
    VMCP-247 (838)), which is #766's dependency-on-someone-else's-config all over again.

    WHAT ACTUALLY HOLDS SUCH A TREE IS NOT THIS FUNCTION, and that is the practical half: since
    VMCP-266 (878) `_populated_gitlinks` refuses it with CODE_POPULATED_GITLINK before the single
    `git worktree remove` is reached — measured on the same stand, `released: false` and the file
    intact, verified BY CONTENT and not by `lexists`, which answers False on EACCES. That guard
    asks the INDEX (mode 160000) and not `git status`, which is exactly why it can answer where
    this call cannot. Do NOT read an empty answer from here as "there is nothing in that tree".
    Two things were declined rather than overlooked. A recursive `git submodule foreach … status`
    is a human's NO: extra processes on every sweep tick for a state the drain never creates
    (`git worktree add` does not populate a submodule — measured on this stand, the directory
    comes up EMPTY), and defeated by those same two config keys anyway. And do not generalise "git
    refuses, so the work is safe": that holds only on an INITIALISED submodule, i.e. the one
    configuration the pipeline never produces — 878 measured the other side, rc=0 and the content
    destroyed.

    A per-invocation `-c` deliberately, never `git config`: the user's setting is not rewritten,
    only what THIS inspection sees. Someone who set it for speed keeps it everywhere else, and a
    CLEAN tree under that setting still releases normally (measured) — which is the objection
    that ruled out refusing outright while the setting is in force.
    """
    dirty: list[str] = []
    ignored: list[str] = []
    for line in _git_inspect(
        "-c", "status.showUntrackedFiles=normal",
        "status", "--porcelain", "--ignored", cwd=path,
    ).splitlines():
        if line.startswith("!! "):
            ignored.append(line[3:])
        else:
            dirty.append(line)
    return dirty, ignored


def _is_reproducible_ignored(entry: str) -> bool:
    """Is this ignored path recognisably regenerable build output (-> not worth reporting)?

    Fails toward REPORTING, in every uncertain case. A path git had to QUOTE (a newline, a tab, a
    non-ASCII byte under `core.quotePath`) is never called routine: it arrives escaped, so matching
    it component-wise would be matching the escape rather than the name.

    KNOWN BOUND, deliberate: `--ignored` collapses an ignored DIRECTORY into one entry, so a file
    an agent hid INSIDE `.venv/` is covered by that entry and goes unreported. Working inside a
    directory the repo declares regenerable is not a case this can serve.
    """
    if entry.startswith('"'):
        return False
    parts = [component for component in entry.rstrip("/").split("/") if component]
    if not parts:
        return False
    if any(component in _REPRODUCIBLE_IGNORED_DIRS for component in parts):
        return True
    leaf = parts[-1]
    return leaf in _REPRODUCIBLE_IGNORED_LEAVES or leaf.endswith(_REPRODUCIBLE_IGNORED_SUFFIXES)


def _populated_gitlinks(path: Path) -> list[str]:
    """Every gitlink in THIS worktree's index whose directory on disk is not empty (VMCP-266).

    THE GUARD BELOW IS BLIND UNDER A GITLINK, AND NOT ONLY FOR IGNORED FILES — which is what
    separates this from the `removed_ignored` chain and is the whole warrant for REFUSING here
    rather than reporting. `git status` does not answer about paths under a gitlink at all, so
    `_inspect_status` returns `([], [])` over a file the guard elsewhere PROMISES to hold: measured
    on a real submodule, a worktree the module itself created, an untracked-and-NOT-ignored
    `sub/precious.txt` written into it — `dirty` empty, `release_workspace` `{"released": true}` with
    no `code` and no `removed_ignored`, the file gone, rc 0, no `--force`. POSITIVE CONTROL on the
    same probe in the same tree: an untracked `control-at-root.txt` at the ROOT gives
    `['?? control-at-root.txt']` and the tree is held. So the probe is sound and the blindness is
    exactly the gitlink's shadow. That is the shape of VMCP-223 (766) — one mechanism silently
    switching off a claimed guarantee — and it gets the same answer: make the guard work, do not
    narrow the promise. VMCP-221 (764)'s "report, never hold" is untouched, because that decision is
    about IGNORED files, which the guard never claimed.

    WHY AN EMPTY-DIRECTORY TEST IS ENOUGH, and why it needs no `--no-index`, no `check-ignore` and no
    walk INSIDE: this pipeline never populates a submodule. There is no `git submodule` call in the
    package and no `--recurse-submodules` on any of the three `worktree add` forms, so `git worktree
    add` leaves a gitlink's directory EMPTY (measured: exists YES, contents `[]`, `sub/.git` NO).
    A non-empty one therefore means somebody put something there, and that IS the signal. Cost
    accepted deliberately: a directory holding only regenerable debris is refused too. The refusal
    falls the safe way — an unreleased tree is recoverable, a removed one is not.

    THE TEST IS THE INDEX, never `.gitmodules` and never `.git` on disk. A gitlink lives in the
    index (`.gitmodules` is ordinary tracked content and can disagree with it), and a DEINITIALISED
    submodule has no `.git` anywhere while still being a gitlink — which is precisely the state this
    guard exists for. Same reading `_index_gitlink_paths` takes, for the same reason.

    FAIL-CLOSED IN BOTH DIRECTIONS, since this decides whether work is destroyed rather than how it
    is reported. A directory that cannot be READ counts as populated: `os.scandir` raising EACCES
    means there is something there we cannot see, and `os.path.lexists`-style probing answers False
    on exactly that case. A failed `ls-files` RAISES (`_git_inspect`, like every other read in
    `_release_locked`), which `--gc` records as `release-error` -> `kept`; it must never degrade to
    an empty list, because an empty list here means "remove the tree".

    Not populated, deliberately: a gitlink path that is absent, or is not a directory at all (a
    typechange left a file or a symlink there). Neither can hide an agent's files, and treating them
    as populated would hold ordinary trees for nothing.

    THE WHOLE INDEX, where `_index_gitlink_paths` scopes itself to pathspecs, and the difference is
    the call SITE rather than a disagreement: that one runs per sweep tick over a diff's paths, this
    one runs once per tree that has already passed every other guard, i.e. only for trees about to
    be removed. Cost is one git process, not a walk — measured 15.8-16.8 ms over 5 runs on this
    checkout (74 tracked files), which is process spawn rather than index size.

    `_git_inspect` because this reads INSIDE a tree it may end up refusing, so VMCP-90's rule
    applies: measured on a real worktree, neither grace marker moves across this call (tree
    directory and index mtimes byte-identical before and after), while the POSITIVE CONTROL — a
    plain `git status --porcelain` in the same tree straight afterwards — does move the index. So
    the probe is footprint-free by measurement, not merely by inheriting the helper.
    """
    found: list[str] = []
    for record in _git_inspect("ls-files", "-s", "-z", cwd=path).split("\0"):
        # `<mode> <object> <stage>\t<path>`, and `-z` means git does NOT quote the path — the TAB
        # separates them and a path may contain spaces, so partition rather than split.
        meta, tab, rel = record.partition("\t")
        if not (tab and rel and meta.startswith(_GITLINK_MODE + " ")):
            continue
        try:
            with os.scandir(path / rel) as entries:
                populated = any(True for _ in entries)
        except (FileNotFoundError, NotADirectoryError):
            continue
        except OSError:
            populated = True
        if populated:
            found.append(rel)
    return found


def _release_locked(root: Path, task_id: int, role: str) -> dict:
    _check_role(role)
    _git("worktree", "prune", cwd=root)
    wt = _find(root, task_id, role)
    if wt is None:
        # Review Minor: every OTHER refusal below carries "path" — a human reading `kept`
        # needs a location to act on even when there is nothing to remove. The expected (but
        # absent) path is still informative: it says WHERE a worktree for this task would be.
        name = (BUILD_NAME if role == "build" else REVIEW_NAME).format(task_id=task_id)
        return {"released": False, "task_id": task_id, "role": role,
                "path": str(worktree_root(root) / name), "code": CODE_NO_WORKTREE,
                "reason": "no worktree for this task"}
    path = wt["path"]
    if wt["lock_reason"] == _LOCK_INITIALIZING:
        # The OUTCOME here is unchanged — a half-created tree was already kept, on every tick,
        # forever. What was wrong is the DIAGNOSIS: `git status` inside it reports the staged
        # deletions of every missing file, so the guard below called it "working tree is dirty
        # (N entries)" and sent a human looking for uncommitted work that does not exist.
        # Say what it actually is, once, in a line `--gc`'s `kept` can be acted on.
        #
        # Keyed on the marker TEXT (unlike _ensure_locked's guard, which keys on the bool): both
        # branches KEEP the tree, so a miss costs only wording — and since VMCP-142 the fall-
        # through is a coded verdict too, so a miss no longer changes the CHANNEL either.
        return {"released": False, "task_id": task_id, "role": role, "path": str(path),
                "code": CODE_HALF_CREATED,
                "reason": f"half-created worktree (git's own `locked {_LOCK_INITIALIZING}` "
                          f"marker from a killed `worktree add`) — needs a human: "
                          f"`git worktree unlock {path} && git worktree remove -f -f {path}`"}
    if wt["locked"]:
        # VMCP-142, and it REVERSES the note that used to sit above: a human `git worktree lock`
        # was deliberately left to fall through to `git worktree remove`'s own refusal ("cannot
        # remove a locked working tree … use 'remove -f -f' to override or unlock first"), on the
        # ground that git's message is already correct and specific and a synthesised reason would
        # replace git's report with our guess. The MESSAGE was never the problem; the CHANNEL was.
        # That refusal arrives as a raise, which `run_workspace` renders as `{"error": …}` + exit 1
        # — the CREATE channel — and SKILL.md reads that shape as "the tool could NOT do the work":
        # its «Не завелось — цикл НЕ роняем» branch is written for a failed CREATE, and it is the
        # only rule an agent has for an `{"error"}` + rc 1 line (degrade to one slot, keep
        # draining), while this tree is the other kind entirely, the kind rc 0 + `released: false`
        # + `code` exists for. The work is intact and a HUMAN pinned it; an agent that reads that
        # as a broken tool and moves on is the one outcome nobody wants.
        #
        # Keyed on the `locked` BOOL, like _ensure_locked's guard and for its reason: a tree we
        # cannot vouch for is refused whatever the lock SAYS, and a reasonless lock (`lock_reason
        # is None`, a real porcelain shape) must not slip past a text comparison. Its own prose
        # rather than `_locked_refusal`'s, for the same reason the half-created branch above has
        # its own: that helper is create-side ("refusing to hand it back", "working in it would
        # leave a tree nothing can reap"), and neither clause describes what happened here.
        #
        # PLACED BEFORE THE FIRST GIT CALL WITH CWD INSIDE THE TREE (the `git status` inspect
        # below), which is load-bearing rather than tidy: a locked entry survives `git worktree
        # prune` (measured), so an entry whose DIRECTORY a human moved or deleted is still handed
        # back by `_find`, and `_git_inspect(cwd=<gone>)` raises a bare FileNotFoundError that
        # `_git` cannot convert into anything. Same root, a different mechanism, and only an
        # ordering ahead of that call answers both with one guard. It also decides which code a
        # locked-AND-dirty tree reports — the lock, because it is the fact that makes the tree
        # unremovable, and "commit and retry" would be advice that cannot work. See the grading
        # note by `_EXPECTED_IN_A_PARKED_BUILD_TREE` for what that changes in `--gc`.
        #
        # `-f -f` is deliberately NOT offered here, unlike the half-created message above: there
        # the tree is unusable debris and force is the recovery, here the lock IS the human's
        # instruction and the tool must not teach agents how to override it. Grading: neither
        # `_EXPECTED_*` set holds this code, so `--gc` files it under `kept` by the fail-toward-
        # shouting default — see the policy note there for why a lock is never routine.
        reason = wt["lock_reason"] or "no reason given"
        return {"released": False, "task_id": task_id, "role": role, "path": str(path),
                "code": CODE_LOCKED,
                "reason": f"worktree is LOCKED ({reason}) — a deliberate hands-off marker, and "
                          f"git refuses to remove a locked tree. Nothing was removed and nothing "
                          f"was lost: `git worktree unlock {path}`, then release it again"}
    # Both halves of one status call — see _inspect_status. `ignored` is not a guard input: it is
    # read HERE, before anything is removed, because it is the last moment at which the payload
    # this removal is about to destroy can still be named (VMCP-185).
    dirty, ignored = _inspect_status(path)
    if dirty:
        return {"released": False, "task_id": task_id, "role": role, "path": str(path),
                "code": CODE_DIRTY,
                "reason": f"working tree is dirty ({len(dirty)} entries)"}
    if wt["branch"] is not None:
        # a task/<id> BRANCH's unique history is only safe once it's on origin — the
        # unpushed-commits guard.
        base = f"origin/{default_base(root)}"
        unpushed = _git_inspect("log", "--oneline", f"{base}..HEAD", cwd=path)
        if unpushed:
            return {"released": False, "task_id": task_id, "role": role, "path": str(path),
                    "code": CODE_UNPUSHED,
                    "reason": f"{len(unpushed.splitlines())} commit(s) not on {base}"}
    elif role == "build":
        # VMCP-86: the branch below assumes "detached ⇒ this is a review tree", and a build tree
        # left detached by an interrupted rebase falls straight into it — with the guard ABOVE
        # skipped, because that one keys on `wt["branch"]` and a detached tree has none. So the
        # unpushed history of `task/<id>` — which still exists and still holds the agent's commits
        # — goes UNCHECKED, and whether the tree is destroyed comes down to the unrelated question
        # of whether its HEAD happens to be reachable.
        #
        # MEASURED, in a throwaway repo, on the code as it stood: a rebase interrupted with HEAD
        # still on `onto` (git detaches there BEFORE replaying anything, so a turn killed at the
        # start lands exactly there; also reachable via a first-commit conflict resolved in the
        # sibling's favour, which leaves the tree clean) gave `{"released": true}` — the directory
        # DELETED, `task/541` left behind holding one commit that was not on origin/main, and
        # nothing in the report saying so. `--gc` does that unattended, every tick.
        #
        # Refuse instead, FIRST in the detached branch: which of HEAD and `task/<id>` is "the work"
        # is exactly the question this module cannot answer for the agent, and it is the more
        # specific statement about the same tree than "reachable from no ref" (the ordering
        # argument gc's self-guard-before-grace-window makes). `unreachable-head` is left untouched
        # for the review trees it was written about; `_keep_is_expected`'s `role` conjunct STAYS as
        # a backstop should anything reach it with a build tree again.
        return {"released": False, "task_id": task_id, "role": role, "path": str(path),
                "code": CODE_DETACHED_BUILD,
                "reason": _detached_build_refusal(
                    root, task_id, wt,
                    refusal="Refusing to release it: the unpushed-commits guard that protects a "
                            "build tree cannot run on a tree that is not on its branch, so "
                            "removing it here would report success while the branch's own commits "
                            "went unchecked.",
                )}
    else:
        # a review tree is DETACHED — it holds no branch, so the guard above cannot apply —
        # but its HEAD is NOT automatically safe either: anyone can commit INSIDE a detached
        # tree (the dirty check above only catches UNCOMMITTED changes; a fresh commit makes
        # the tree clean again). Verified against real git: nothing else protects that commit
        # — `git worktree remove` has no unpushed-commit check for a detached HEAD, and a
        # later `gc` would prune the object outright once the worktree admin dir (and its
        # reflog) is gone. The one thing that DOES make removal safe is the commit being
        # reachable from some other ref — a review pinned at a build branch's tip is exactly
        # that (task/<id> still names it, BY DEFINITION not yet on origin/main, which is why
        # the branch-history guard above must not run here); a commit made only inside this
        # detached tree is reachable from nothing and must be kept.
        #
        # KNOWN, DELIBERATE bound: this only inspects HEAD. A commit made and then moved off
        # HEAD (`reset --hard HEAD~1`, `checkout --detach <older>`) is released and destroyed
        # unseen. NOT a gap specific to this branch — the task/<id> path above has the exact
        # same shape (`origin/base..HEAD` also only ever looks at HEAD, and `branch -D`
        # finishes off whatever the branch no longer points at): "HEAD is the work" is a bound
        # of the whole module, not an oversight in this guard alone.
        head = _git_inspect("rev-parse", "HEAD", cwd=path)
        reachable = _git("for-each-ref", "--contains", head, "--format=%(refname)", cwd=root)
        if not reachable:
            return {"released": False, "task_id": task_id, "role": role, "path": str(path),
                    "code": CODE_UNREACHABLE_HEAD,
                    "reason": f"detached HEAD {head} is reachable from no ref"}
    # VMCP-266, and the PLACEMENT is the decision: LAST, immediately before the removal, rather
    # than beside `dirty` where the other content guard sits. Three things follow, and only this
    # position gives all three. It fires on exactly the trees that would otherwise be DESTROYED, so
    # it costs nothing anywhere else. It cannot be bypassed by any of the branches above (branch /
    # detached-build / review HEAD), which each `return` on their own and would each need their own
    # copy of it. And it never displaces a code that already has its own cure — a `dirty` tree is
    # still reported `dirty`, which matters because "commit and retry" would be advice that does not
    # empty a submodule directory, and the retry lands here anyway.
    gitlinks = _populated_gitlinks(path)
    if gitlinks:
        named = ", ".join(gitlinks[:_MAX_REPORTED_IGNORED])
        more = (f" (and {len(gitlinks) - _MAX_REPORTED_IGNORED} more)"
                if len(gitlinks) > _MAX_REPORTED_IGNORED else "")
        return {"released": False, "task_id": task_id, "role": role, "path": str(path),
                "code": CODE_POPULATED_GITLINK,
                "reason": f"gitlink director{'y is' if len(gitlinks) == 1 else 'ies are'} not "
                          f"empty ({named}{more}) — `git status` says nothing about paths under a "
                          f"gitlink, so the dirty guard cannot see what is in there and removing "
                          f"the tree would destroy it unnamed. Nothing was removed and nothing was "
                          f"lost. `git worktree add` never populates a submodule, so something put "
                          f"that content there: save what is wanted out of {path}, empty the "
                          f"director{'y' if len(gitlinks) == 1 else 'ies'}, then release again"}
    _git("worktree", "remove", str(path), cwd=root)
    result = {"released": True, "task_id": task_id, "role": role,
              "path": str(path), "branch": wt["branch"]}
    destroyed = [entry for entry in ignored if not _is_reproducible_ignored(entry)]
    if destroyed:
        # ADDED ONLY WHEN NON-EMPTY, exactly like `branch_deleted`/`warning` above and for the same
        # reason: the absence of the key is the "nothing to see" signal. A field present on every
        # released entry (which it would be, unfiltered — every build tree carries `.venv/`) is the
        # never-read signal VMCP-68 had to split `kept` in two to cure, reintroduced in `released`.
        # SKILL.md's rule is therefore "read `kept`, and scan `released` for `branch_deleted:
        # false` AND `removed_ignored`".
        #
        # ONE DIRECTION ONLY, and the rulebook must say so: the key's PRESENCE proves something
        # unrecognised was destroyed, but its ABSENCE does not prove nothing was. `--ignored`
        # collapses an ignored DIRECTORY into a single entry, so a file an agent left inside
        # `.venv/` is covered by `.venv/`, filtered as routine, and destroyed unnamed (measured).
        result["removed_ignored"] = _cap_reported(destroyed)
        if len(result["removed_ignored"]) < len(destroyed):
            result["removed_ignored_truncated"] = len(destroyed)
    if wt["branch"]:
        try:
            _git("branch", "-D", wt["branch"], cwd=root)
        except WorkspaceError as e:
            # tracker #517. The ONE window in which this function can fail with the tree ALREADY
            # GONE, and letting it raise made both callers lie about it in opposite directions:
            # `--gc`'s except-handler recorded `released: False` with a `path` that no longer
            # exists, and `--release` reported `{"error"}` + exit 1 for an operation that had
            # already succeeded. `released: false` is not a neutral "it didn't work" either —
            # SKILL.md teaches it as "PROTECTION: you still have unsaved work in there", and
            # sending an agent to rescue work from a directory git just deleted is the worst of
            # the available wrong answers.
            #
            # So report what actually happened: the tree IS released (every guard above passed,
            # meaning it was clean and pushed — nothing was lost), and the BRANCH leaked. That
            # leak is recoverable by construction, not a silent corruption: `_ensure_locked`
            # reattaches a surviving `task/<id>` instead of recreating it, which is the same
            # path a hand-deleted tree takes. Reported anyway, because a `branch -D` that fails
            # here means something unexpected about the repo, and a human should get the one
            # command that finishes the job.
            #
            # Keys added ONLY on failure, so their absence is the success signal and no existing
            # consumer of the released entry has to learn a new field. WorkspaceError only: a
            # bare OSError from a vanished cwd is a different bug and must not be laundered into
            # "released with a warning".
            result["branch_deleted"] = False
            result["warning"] = (
                f"worktree removed, but `git branch -D {wt['branch']}` failed ({e}) — the "
                f"branch leaked. Nothing was lost (the tree was clean and pushed) and a later "
                f"workspace call for task {task_id} will reattach to it; to finish the cleanup "
                f"by hand: `git branch -D {wt['branch']}`"
            )
    return result


def ensure_workspace(
    task_id: int, role: str = "build", at: str | None = None, cwd: Path | None = None
) -> dict:
    # Review Critical 1: canonicalise to the MAIN worktree HERE, once, so every `cwd=root` git
    # call inside _ensure_locked/_release_locked runs against a directory that is never itself
    # a tree being created/removed — see _main_worktree and release_workspace below for why.
    root = _main_worktree(repo_root(cwd))
    with _repo_lock(root):
        return _ensure_locked(root, task_id, role, at)


def release_workspace(task_id: int, role: str = "build", cwd: Path | None = None) -> dict:
    # Review Critical 1: an agent's own "I'm done, release me" call runs with cwd INSIDE the
    # very tree being released (the normal case per SKILL.md). Left uncanonicalised, `root`
    # would equal the tree's own path, and `_release_locked`'s `git worktree remove` (which
    # SUCCEEDS even when its subprocess cwd is the directory being removed — verified against
    # real git) would be immediately followed by `git branch -D ... cwd=root`, whose Python
    # subprocess.run(cwd=root) needs `root` to still EXIST — root having just been deleted
    # raises a bare FileNotFoundError that `_git` cannot convert (it only inspects
    # `returncode`), the branch leaks, and the CLI reports exit 1 for an operation that had
    # actually already succeeded. Canonicalising to the MAIN worktree (which is never the tree
    # being released) makes `root` a stable directory for the whole call.
    root = _main_worktree(repo_root(cwd))
    with _repo_lock(root):
        return _release_locked(root, task_id, role)


def _parse_workspace_name(name: str) -> tuple[str, int] | None:
    match = _NAME_RE.match(name)
    if match is None:
        return None
    return _ROLE_BY_PREFIX[match.group(1)], int(match.group(2))


def _build_workflow(root: Path) -> tuple:
    """(workflow, deadline) for one sweep. The deadline is returned rather than kept private
    because only the CALLER knows when the clock should start: it must be armed once the repo
    lock is HELD (see gc_workspaces), never here — a budget started before the flock would be
    spent WAITING for it, so every sweep under contention would abandon itself.

    Review Minor: `cwd=root` (the MAIN worktree — see gc_workspaces) is load-bearing, not
    decorative. `.vikunja-mcp.env` (the token) sits BESIDE `.vikunja-mcp.toml` in the repo,
    found by config.py's own walk-up from `cwd` — a linked worktree has neither file, so
    `load_config()` with no cwd would silently miss them whenever gc runs from inside one
    (the normal invocation site per SKILL.md), and fall through to env/user config or raise.
    """
    from vikunja_mcp.api import VikunjaAPI
    from vikunja_mcp.config import load_config
    from vikunja_mcp.workflow import Workflow

    cfg = load_config(cwd=root)
    # Review Important 3: BOUND the read, because it happens INSIDE the repo lock (Important 5
    # put it there deliberately, to close the race where a tree created between the read and the
    # reap is destroyed under a just-dispatched agent — do not move it back out). With api.py's
    # defaults an unreachable tracker costs 30s x 4 attempts + backoff ~= 2 MINUTES of held
    # lock per request, and everything queued behind it — every agent's `--release` — waits;
    # at wip_limit = 2 those are precisely the agents trying to clean up after themselves.
    #   * timeout 10s: generous for a JSON API that normally answers in tens of ms, and a
    #     black hole (the pathological case) now costs 10s of lock, not 120s.
    #   * no retries: this is HOUSEKEEPING on a loop. A transient 429/5xx costs one skipped
    #     sweep and the next tick retries anyway — sleeping through a backoff while holding a
    #     lock other agents need is strictly worse than trying again in ten minutes.
    # NOT the alternative (take the lock non-blocking, skip the sweep on contention): that
    # bounds how long gc WAITS, and the problem is how long gc HOLDS — it would not shorten
    # the wait of a single `--release` queued behind a hung board read by one second.
    #
    # VMCP-72: the per-request bound above is NOT the hold. The read is several requests and the
    # count grows with the board (see `_READ_DEADLINE_SECONDS`), so the deadline hook bounds the
    # TOTAL — the only one of the two that is invariant to page count.
    deadline = _ReadDeadline(_READ_DEADLINE_SECONDS)
    api = VikunjaAPI(
        cfg.url, cfg.token, timeout=_READ_TIMEOUT_SECONDS, max_retries=0,
        event_hooks={"request": [deadline]},
    )
    return Workflow(api, cfg.project_id), deadline


# THE GRADING POLICY (VMCP-68), and the shape is the point: expectedness is a property of the
# guard AND of the board AND of the ROLE, never of the guard alone. Both sets below now name the
# role they are about, and they arrived at it by the same route a card apart — see VMCP-91.
#   * _EXPECTED_IN_A_PARKED_BUILD_TREE — the two guards that protect ORDINARY in-progress work.
#     Both are routine in a BUILD tree while the task's card waits in Your Call: `call_human` parks
#     the card the moment a rebase conflicts (dirty) or a push is rejected (unpushed), which is
#     exactly when the tree holds unsaved work, and it stays that way for HOURS until a human
#     answers. The card is already the human's signal; a `kept` line every tick adds nothing. The
#     SAME two refusals on a card that is NOT parked mean work nobody is coming back for — that one
#     has to shout.
#
#     VMCP-91 ADDED THE `role` CONJUNCT, and it is the exact mirror of the one the review set got
#     in VMCP-68's round 2: every word of the justification above is about the BUILD agent — its
#     conflict, its rejected push, its `call_human` — while `dirty` is emitted by a guard that
#     "роли НЕ РАЗЛИЧАЕТ" (SKILL.md tells the reviewer so, and test_skill_contract builds the
#     state), so a REVIEW tree was laundered by a parked card it merely shares a task id with.
#     MEASURED before the fix, one sweep, three quiesced dead trees, all three cards in Your Call:
#     `kept=[]`, `expected=[(107,'review','dirty'), (110,'build','dirty'),
#     (113,'review','unpushed')]` — i.e. a human saw NOTHING. Reachable on the ordinary path for
#     `dirty`: the reviewer files `needs_work` without `--release`, the card goes back to Build,
#     the build agent hits a conflict and calls `call_human`. `unpushed` in a review tree needs a
#     hand-made branch (the tool only ever creates them detached), so it is constructible rather
#     than routine — both are in the set because the set is graded per CODE, and a conjunct that
#     held for only the reachable half would be a second thing to keep true.
#
#     A reviewer's tree is not excused by ANY board state, which is why the conjunct is on the role
#     rather than a wider parked set: the reviewer's contract is a verdict as a tracker COMMENT, so
#     a draft left in its tree is precisely the thing SKILL.md tells it to clear, and the parked
#     card belongs to someone else's unsaved work. The build side is untouched — deliberately, it
#     is the whole reason the set exists.
#   * _EXPECTED_IN_A_REVIEW_TREE — a detached REVIEW tree holding an in-tree commit. Permanent by
#     construction (the reachability guard rightly refuses to release it and `--gc` cannot reap
#     it), so it is the entry that would otherwise make `kept` non-empty FOREVER. SKILL.md's
#     answer is the fix, and it is a rule for the reviewer, not a chore for the human: write the
#     verdict as a tracker comment, never as a commit in the tree.
#
#     ROUND-2 REVIEW, and the reason this one is keyed on the ROLE and not on the code alone: the
#     whole justification above is about a REVIEW tree, but `unreachable-head` is emitted by the
#     detached branch of `_release_locked`, which a BUILD tree also reaches — the review's
#     counter-case came straight out of this project's own integration recipe. CONSTRUCTED AND
#     MEASURED: `git fetch origin && git rebase origin/main` interrupted mid-replay (a killed
#     turn, this project's documented failure mode) leaves the build tree `git status`-CLEAN,
#     DETACHED (`branch: None`), with the replayed commit reachable from no ref. Once the card
#     leaves Build the tree is dead, so the sweep grades it — and graded on the code alone it was
#     `expected`, i.e. filed under "do not look", forever, for a state ONLY a human can clear.
#     That is the shape of CODE_HALF_CREATED, which is correctly never routine. So: routine in a
#     review tree, an alarm in a build tree. This does NOT bring back the never-empty `kept` the
#     split exists to fix — an interrupted rebase is an incident, not a state the pipeline
#     produces on the happy path.
#
#     VMCP-86 KEPT THIS CONJUNCT AS A BACKSTOP, deliberately, and it is no longer the ONLY thing
#     standing between that build tree and a routine grading: `_release_locked` now refuses a
#     DETACHED BUILD tree upstream, with its own CODE_DETACHED_BUILD, so today nothing reaches
#     here with `unreachable-head` on a build tree. Dropping the conjunct on that ground would be
#     the same mistake in reverse — it would make the grading depend on a guard three hundred
#     lines away staying exactly as it is, and the whole policy is "fail toward shouting". Pinned
#     directly (test_keep_grading_of_unreachable_head_still_turns_on_the_role) rather than through
#     a sweep, precisely because no sweep can construct it any more.
# Neither set contains CODE_DETACHED_BUILD, CODE_HALF_CREATED, CODE_LOCKED, CODE_NO_WORKTREE,
# CODE_POPULATED_GITLINK, CODE_RELEASE_ERROR or CODE_SELF_TREE — all seven, and that membership is
# DERIVED rather than restated: test_the_policy_comment_enumerations_are_derived_from_the_code
# reads the RUN OF NAMES above — the list itself, not this paragraph, because prose down here
# mentions some of them again — and fails unless it is exactly the codes in neither set. Derived
# because as a hand-kept copy it rotted twice, silently and in two different ways. It opened
# (VMCP-68) naming four and closing cleanly on "the other three"; VMCP-142 inserted CODE_LOCKED
# at position two and rewrote that to "the LAST three", which slid the referent past the new
# member and left it in a list with no bin at all. The other rot is older and simpler: VMCP-86
# declared CODE_DETACHED_BUILD and never added it here, so the list was already short by one
# when VMCP-142 arrived. Neither rot was caught by the
# card that caused it; both were caught by VMCP-91, which was rewriting a DIFFERENT closed
# enumeration further down this block (the four bins under the grid), so what this needs is not
# more care but an assert.
#
# A parked card must not launder any of the seven, and each has its own reason to shout. Read the
# bins as WHY-NOT-ROUTINE, not as a taxonomy: CODE_LOCKED also happens to be cleared by a git
# command (`git worktree unlock`), so "needs a git command" is not what separates the groups.
# CODE_HALF_CREATED and CODE_DETACHED_BUILD are the two whose refusal is a HANDOFF of specific
# commands against the tree — the first to a human, the second to the AGENT (`git rebase
# --continue`/`--abort`, which is why that refusal spells both out, since only the agent can know
# which) — and a parked card neither runs them nor makes them unnecessary.
# CODE_LOCKED has the paragraph directly below to itself, and that pointer is the repair for the
# orphaning: it was never unexplained, only unreferenced. CODE_NO_WORKTREE, CODE_RELEASE_ERROR and
# CODE_SELF_TREE describe gc itself, not the work in the tree.
# CODE_POPULATED_GITLINK (VMCP-266) is `kept` for the reason the human who chose this refusal gave:
# a human has to look, and the entry disappears the moment they have. No BOARD state can clear it —
# a parked card excuses the agent's OWN unsaved work, and says nothing whatever about content in a
# submodule directory this pipeline never populated — so no cell of its row may be routine. It is
# also the one code here protecting content that `git status` cannot describe to anybody, which is
# why leaving it out of `kept` would be leaving it out of every signal there is.
#
# CODE_LOCKED (VMCP-142) is the one whose grading was an actual decision rather than a reading, so
# say why it is `kept`. A human `git worktree lock` IS an explicit human action, which sounds like
# the definition of "expected" — but expectedness here means "the pipeline produces this on the
# happy path AND the human already has a signal for it". Neither holds: nothing on the board says a
# tree is pinned (that is exactly what the parked card does for `dirty`/`unpushed`), and the lock
# makes the tree unreapable for as long as it stands, so filed under "do not look" it would leak in
# silence. That is the shape of CODE_HALF_CREATED, which is correctly never routine.
#
# WHAT VMCP-142 DID AND DID NOT MOVE in `--gc`, measured on both sides rather than assumed (the
# first wording here claimed "the alarm did not move" and a second pass disproved it). A locked tree
# that is otherwise CLEAN and PUSHED — the one that used to reach `git worktree remove` — kept its
# list: `release-error`/`kept` before, `locked`/`kept` now. But a locked BUILD tree that is ALSO
# dirty or unpushed never reached the remove at all: the guards above answered first, so under a
# PARKED card it graded `expected`, and now the lock answers first and it grades `kept`. That IS a
# move, in the safe direction: the parked card excuses unsaved work because the human will come back
# to it, and a lock is not unsaved work — nothing on that card says the tree cannot be reaped at
# all. It moved the REVIEW tree the SAME way at the time (`dirty`/`expected` -> `locked`/`kept`, in
# BOTH roles); what VMCP-91 changed is that the review half is no longer a move at all, because
# `dirty`/`unpushed` there are `kept` with or without the lock. A second pass caught this paragraph
# narrowing itself to "a locked BUILD tree" and then claiming the review tree "was never a move" —
# the third wording of a paragraph whose whole subject is not overstating a measurement.
#
# THE WHOLE GRID, since the absence of one is what let VMCP-91's hole live through two rounds of
# review of the very function it is in. Every code this module can emit, against both roles and
# both board states — `E` = expected, `K` = kept:
#
#     code               build+parked  build  review+parked  review
#     dirty                   E          K          K           K
#     unpushed                E          K          K           K
#     unreachable-head        K          K          E           E
#     detached-build          K          K          K           K
#     populated-gitlink       K          K          K           K
#     half-created            K          K          K           K
#     locked                  K          K          K           K
#     no-worktree             K          K          K           K
#     self-tree               K          K          K           K
#     release-error           K          K          K           K
#     <unknown / absent>      K          K          K           K
#
# A THIRD COLUMN the fix closed, which the card never named and only running the grid surfaced: an
# entry whose `role` key is ABSENT ALTOGETHER. Before, `dirty`/`unpushed` on a role-less entry under
# a parked card graded `expected`. The old docstring did NOT overclaim here — it said the `.get`
# made a role-less entry "fail the review conjunct", which was exactly true, because the review
# branch was the only one that read `role` at all; the build branch never asked. Now both do, so a
# role-less entry is `kept` in every cell. CONTRACT HARDENING, NOT A LIVE BUG, and worth saying so
# rather than counting it as a third defect: every `_release_locked` return and both entries `--gc`
# synthesises carry a `role`, so nothing in production reaches that column today.
#
# THREE of the eleven rows above are not all-`K` — the two codes excused in a parked BUILD tree, plus
# `unreachable-head` in a review tree — and they come from TWO sets because the first two share
# theirs. The conjuncts differ in NUMBER, not just in content, and that asymmetry is the policy
# rather than an accident: the build pair needs THREE conditions (code, role, and the card in Your
# Call) because the card is the human's second signal, while `unreachable-head` needs TWO (code and
# role) because a reviewer's in-tree commit has a rule — write the verdict as a comment — rather
# than a chore, so no board state ever clears or excuses it. Every other row is `kept` in all four
# columns, and a parked card must never launder one: a broken tool state, a statement about gc
# itself, a standing human lock (CODE_LOCKED — its own paragraph above says why an explicit human
# action is still not routine), or a code this module does not know at all. Pinned as a grid by
# test_the_grading_grid_is_all_kept_outside_the_four_named_cells, so a new code lands in `kept` by
# default and a widened set has to argue with a test.
_EXPECTED_IN_A_PARKED_BUILD_TREE = frozenset({CODE_DIRTY, CODE_UNPUSHED})
_EXPECTED_IN_A_REVIEW_TREE = frozenset({CODE_UNREACHABLE_HEAD})


def _keep_is_expected(entry: dict, parked: set[int]) -> bool:
    """Is this refusal the routine state of a healthy board (-> `expected`), or something a human
    should look at (-> `kept`)?

    Fails toward SHOUTING, and that direction is deliberate: a code that is unknown here — a new
    guard, a renamed constant, a reason produced by something that never learned to set `code` —
    is UNEXPECTED, so it lands in `kept`. Wrong-and-noisy costs a human one glance; wrong-and-quiet
    is how the never-read signal this split exists to fix comes back in a new guise.

    Same direction on `role`, in BOTH branches (VMCP-68 for the review one, VMCP-91 for the build
    one): read with `.get`, so an entry that somehow carries no role fails its conjunct and shouts
    rather than KeyError-ing the sweep or being waved through. `task_id` stays a subscript because
    a refusal without one cannot be graded against the board at all and a KeyError beats a guess.
    """
    code = entry.get("code")
    if code in _EXPECTED_IN_A_REVIEW_TREE:
        return entry.get("role") == "review"
    return (code in _EXPECTED_IN_A_PARKED_BUILD_TREE
            and entry.get("role") == "build"
            and entry["task_id"] in parked)


def _read_liveness(wf, deadline) -> tuple[dict, set]:
    """ONE sweep's entire tracker read — the board plus every set derived from it — bounded as a
    whole (VMCP-72). Lifted out of `gc_workspaces` because "the thing the budget covers" is a
    unit worth naming: the budget must not stop at the board fetch, since `active_task_ids` still
    costs the `/user` request after it.

    ARMED HERE, which is to say once the CALLER holds the lock — never at construction. The
    budget exists to bound the HOLD and `_repo_lock` BLOCKS: started before the flock it would be
    spent waiting for another agent's sweep, and every contended tick would abandon a read it
    never got to start.

    WHY THE RELABELLING, which is the part that came out of running it rather than reasoning
    about it. The budget can end a read two ways and only one of them names itself: a request
    REFUSED before it is sent raises `ReadDeadlineExceeded`, but a request the budget CLAMPED
    mid-flight dies as httpx's own `ReadTimeout`. MEASURED end-to-end through the CLI against a
    slow tracker (6 s per request, a three-page board): the sweep stopped dead on the budget at
    30.27 s — correct — and reported `{"error": "ReadTimeout: timed out"}`, which a human cannot
    tell from ONE request timing out at 10 s. Right behaviour, unreadable report. So a failure
    raised with the budget already spent is re-raised as what it actually is, keeping the
    original as its `__cause__` and in its text.

    Narrow on purpose: `deadline.spent()` is the whole condition, so a failure with budget still
    on the clock — a 500, a refused connection, a bad token — propagates untouched rather than
    being laundered into "the tracker was slow". EVERY branch here raises or returns; none
    swallows. That is the KEEP invariant: `gc_workspaces` reaps nothing it has not read.
    """
    if deadline is not None:
        deadline.arm()
    try:
        board = wf.liveness_board()
        alive = {"build": set(wf.active_task_ids(board=board)),
                 "review": set(wf.review_task_ids(board=board))}
        # NOT a liveness set (a parked card's tree is dead, deliberately) — it only GRADES the
        # refusals below, off the same single fetch. See _keep_is_expected.
        parked = set(wf.parked_task_ids(board=board))
        return alive, parked
    except ReadDeadlineExceeded:
        raise                                       # already says what it is
    except Exception as exc:                        # noqa: BLE001 — re-raised either way
        if deadline is None or not deadline.spent():
            raise
        raise ReadDeadlineExceeded(
            f"the liveness read exceeded its {deadline.budget:.0f}s overall budget "
            f"({exc.__class__.__name__}: {exc}) — the sweep was abandoned with the repo lock "
            f"released and NOTHING inspected or removed; the next tick sweeps again"
        ) from exc


# --- VMCP-238 (801): the MAIN checkout drifts, and no step of the drain ever moved it ---
#
# Every task lands from its OWN worktree with `git push origin HEAD:main`. That advances the
# SHARED remote-tracking ref `refs/remotes/origin/<base>` and NOT the local branch the main
# checkout has checked out — nothing in create / release / gc / the rulebook's push recipe
# touches it. So the folder a human works in, and the one the orchestrator was launched from,
# falls behind monotonically and never catches up on its own. Measured on this repo 2026-08-05:
# `HEAD` at `5d7acdb` against `origin/main` at `01b096be`, 58 commits, accumulated over ONE
# session in which every task landed green. That is the whole defect — not a failure anywhere,
# just a ref nobody was moving.
#
# WHY THE CODES ARE `MAIN_SYNC_*` AND DELIBERATELY NOT `CODE_*`. That other prefix is a CLOSED
# vocabulary of per-WORKTREE refusals, graded cell by cell by `_keep_is_expected` and pinned
# three separate ways, so a new `CODE_*` reddens those pins until somebody grades it — which is
# exactly right for a code that can land in `kept`. These cannot: they describe the shared
# checkout, never appear on a worktree entry, and never reach the grader. Naming them apart
# keeps that enumeration closed AND true; the separation is pinned by
# test_main_sync_codes_are_not_part_of_the_graded_worktree_vocabulary, not left to this note.
MAIN_SYNC_FETCH_FAILED = "fetch-failed"
MAIN_SYNC_NO_REMOTE = "no-remote-branch"
MAIN_SYNC_DETACHED = "detached"
MAIN_SYNC_OFF_BRANCH = "off-branch"
MAIN_SYNC_DIVERGED = "diverged"
MAIN_SYNC_BLOCKED = "blocked"
# VMCP-244 (835): `blocked` used to cover this state too, and asserted "NOTHING was discarded"
# while doing so. `merge --ff-only` is NOT ATOMIC: it attempts every entry and writes everything
# it can, so ONE path it cannot write leaves the checkout holding PART of the update with HEAD
# still on the old commit. Named apart from `blocked` because the ACTION differs — `blocked` is
# "nothing happened, the next sweep retries", this is "something WAS written, a human has to look".
# WHAT got written decides everything after that, and the two forms have OPPOSITE properties, which
# is VMCP-252 (851): this comment used to end "your checkout now mixes two commits and `git status`
# attributes upstream's content to you", and that is the TRACKED form only. When the only casualty
# is an IGNORED path, `git status` says nothing at all and there is nothing to commit or drop —
# while the human's bytes at that path are already unrecoverable. Whether the ff then completes on
# its own is NOT a property of the form, which is what this comment claimed for one round and what
# the independent review falsified: a part-way merge also drops NEW incoming files on the disk
# without tracking them, and git refuses over those forever after. So the report asks, per run.
# One code, two reports, and a healing claim that is conditional: see `_partial_apply_reason`.
MAIN_SYNC_PARTIAL = "half-applied"
MAIN_SYNC_ERROR = "error"          # the best-effort wrapper in gc: anything unforeseen

# Same idiom as VIKUNJA_MCP_NO_SKILL_SYNC and VIKUNJA_MCP_NO_TRACE — a thing that happens to a
# human's machine by default gets an env escape, not a config key. Repo toml would be wrong
# twice over: this is a property of ONE clone on ONE machine (like `worktree_root`, not like
# `wip_limit`), and a committed opt-out would turn one person's preference into the team's.
_MAIN_SYNC_OPT_OUT = "VIKUNJA_MCP_NO_MAIN_SYNC"


# How many files one incoming path may contribute when it turns out to be a local DIRECTORY.
# Separate from `_MAX_REPORTED_IGNORED`, which caps the REPORT: this caps the QUESTION, so that a
# commit replacing a directory holding a `node_modules` never turns one sweep into a filesystem
# walk. Hitting it means the answer is short, which is a bound named in the docstring rather than
# a refusal — the alternative, giving up on the whole directory, reports less rather than more.
_MAX_DIR_EXPANSION = 500


def _index_gitlink_paths(root: Path, pathspecs: list[str]) -> frozenset[str]:
    """Every path the INDEX holds as a GITLINK under `pathspecs` — the subtrees not to walk into.

    `check-ignore` refuses every path inside a live gitlink, so expanding a submodule yields ONE
    UNASKABLE PATH PER FILE IN IT and buys nothing at all. That is not free once the ask bisects:
    measured by the independent second pass on real git, a submodule of THIRTY files in a batch
    whose only other members were two ignored files spent the entire `_MAX_CHECK_IGNORE_CALLS`
    budget isolating them and lost `z.png` — an askable path, a real casualty of that merge — while
    a submodule of 25 files did not. So the cost was a real one and its threshold was small.

    Pruning them is the fix at the source, and it cannot lose a name: every path it removes is one
    `check-ignore` could never have answered about anyway. Naming those paths is a different
    question, filed as VMCP-247 (838), and it needs `--no-index` rather than a wider walk.

    THE TEST IS THE INDEX, NOT `.git` ON DISK, and that distinction is the whole reason this asks
    git instead of calling `os.path.isdir(d / ".git")`. The fatal is driven by the index — measured,
    a path inside a DEINITIALISED submodule (an empty directory, no `.git` anywhere) still fatals —
    while a stray nested clone that no gitlink points at is answered perfectly normally, and its
    files really do die with the directory that holds them. A `.git` test would prune exactly the
    wrong set on both counts.

    Scoped to `pathspecs` rather than the whole index on purpose: a consumer's checkout can hold
    tens of thousands of tracked files, and this runs on every sweep tick. Best-effort like
    everything on this path — a failed read returns an empty set, i.e. the pre-838 behaviour.
    """
    if not pathspecs:
        return frozenset()
    proc = _run_git(("ls-files", "-s", "-z", "--", *pathspecs), root, None,
                    env_extra={"GIT_OPTIONAL_LOCKS": "0"})
    if proc.returncode != 0:
        return frozenset()
    found: set[str] = set()
    for record in proc.stdout.split("\0"):
        # `<mode> <object> <stage>\t<path>`; the TAB is what separates them, and a path may
        # itself contain spaces, so partition on the tab rather than splitting on whitespace.
        meta, tab, path = record.partition("\t")
        if tab and path and meta.startswith(_GITLINK_MODE + " "):
            found.add(path)
    return frozenset(found)


def _holds_a_dot_git(path: str) -> bool:
    """Does this directory hold a `.git` of ANY form — the mark of a repository ROOT?

    VMCP-256 (858). Deliberately `lexists` and not `isdir`: a working clone has a `.git`
    DIRECTORY, a linked worktree and a populated submodule have a `.git` FILE holding
    `gitdir: …`, and a symlinked one is a third spelling. All three mean the same thing to the
    caller — everything below this point belongs to another repository — so all three answer
    True, and asking about the PATH is what covers them without three branches.

    NOT A SUBSTITUTE FOR THE INDEX-BASED GITLINK PRUNE, and the card that asked for this said so
    in as many words. That prune asks "can `check-ignore` be asked about paths in here at all?"
    and its answer comes from the INDEX, because VMCP-246 (837) measured `.git`-on-disk failing
    at exactly that job in BOTH directions: a DEINITIALISED submodule has no `.git` and still
    makes `check-ignore` exit 128, while a stray clone has one and answers rc=0 perfectly well.
    This function asks a different question — "is this one foreign repository rather than N
    loose files?" — and `.git` is the right evidence for THAT. Keep them two calls; merging them
    would re-introduce the check 837 rejected under a new name.

    Best-effort like its neighbours: on EACCES `lexists` answers False and the walk descends as
    it did before, which costs noise and never a wrong name.
    """
    return os.path.lexists(os.path.join(path, ".git"))


def _expand_if_directory(root: Path, rel: str, gitlinks: frozenset[str] = frozenset(),
                         unreadable: list[str] | None = None) -> list[str]:
    # `r"""` is load-bearing, not style: the measurement below quotes a NUL-separated `printf`, and
    # in a plain docstring `\0` is a real NUL character — measured, two of them landed in
    # `__doc__` before this prefix went on, invisible in the file and in any diff of it.
    r"""`rel` for an ordinary path; when the checkout has a DIRECTORY there, what can DIE inside it.

    THE CASE THIS EXISTS FOR was built by the independent second pass and disproved a sentence
    this function's own docstring used to carry ("git removes only TRACKED files"). Upstream
    replaces a tracked DIRECTORY with a FILE of the same name; the checkout holds its own ignored
    file inside that directory. Measured: rc=0, the directory and everything in it is gone, and
    the probe said nothing at all — the incoming diff names `out` (added) and `out/bar.txt`
    (deleted, so filtered), while the path that actually died, `out/shot.png`, is in neither.
    Asking `check-ignore` about `out` answers rc=1, because the DIRECTORY is not ignored.

    So a present path that is a DIRECTORY is replaced by what can DIE inside it, which is also what
    makes the report finer-grained than `removed_ignored`'s in the mirror case (a locally IGNORED
    directory replaced by an incoming file): that used to yield the single entry `out` for any
    number of dead files inside it. "The FILES inside it" was the wording for two rounds and
    VMCP-245 (836) disproved it, since one kind of entry is neither a file nor walked into. Do NOT
    over-correct that to "the PATHS inside it", which 836's own second pass measured as wider than
    the return value in the other direction: of five paths git calls ignored under `out/`, this
    answers three, omitting the REAL subdirectory `out/inside_real` — whose content
    `out/inside_real/f.txt` is named instead, and that is the finer answer, not a worse one — and
    the EMPTY `out/emptydir`, named nowhere. An empty directory carries no bytes, so that second
    omission is a gap in the LISTING and not in the loss.

    SYMLINKS ARE NAMED, NEVER FOLLOWED, AND THAT IS TWO GUARDS RATHER THAN ONE. `islink` before
    `isdir` at the top covers `rel` ITSELF: a symlinked directory is one path to git and one path
    to delete, and walking through it would name files that live outside the checkout. NESTED ones
    need their own line in the loop, and for two rounds they did not have it — `os.walk` classifies
    a symlink-to-a-DIRECTORY under `dirnames`, which is correct, and does not descend into it
    (`followlinks` defaults to False, and that default is what keeps the target's files out of this
    answer). NOT DESCENDING IS NOT WHAT HID IT, and the distinction is worth keeping straight:
    `dirnames` IS a list `os.walk` produces, and what left the path unnamed is that the LOOP read
    only `filenames`. So it was named ZERO times rather than once.

    TWO STANDS, AND THEY MUST NOT BE WELDED — an earlier draft of this docstring welded them and
    836's second pass caught it. The shape that ISOLATES the defect has FOUR entries under `out/`
    (`a.txt`, `to_dir` -> a directory, `to_file` -> a file, and a DANGLING one): `git check-ignore`
    (2.50.1) answers rc=0 and echoes all four, while the walk returned three, omitting `to_dir`
    alone. The END-TO-END run is a SEPARATE, TWO-entry stand (`a.txt` plus `to_dir`) — there the
    pre-fix probe answered `['out/a.txt']`, `merge --ff-only` was rc=0, and `out/to_dir` died
    unnamed. `['out/a.txt']` is the TWO-entry answer; the four-entry stand's pre-fix answer is
    three entries, so quoting the one beside the other describes a tree that never existed.

    A symlink-to-a-FILE and a DANGLING one were already named — a FIFO too — because `os.walk`
    splits by `isdir`, which FOLLOWS. So among entries that can carry BYTES the symlink-to-a-
    directory was the only one unnamed, and say it THAT way rather than "exactly and only the
    hole": the sample above is four hand-picked entries, and widening it to fifteen leaves a
    residue this fix still does not name — real subdirectories, whose contents are named instead,
    and empty ones, which have no bytes to lose. That made `overwritten_ignored` PRESENT AND
    INCOMPLETE, which is the failure THIS card is about; it is NOT "the one way this key must not
    fail", a universal the BOUNDS list below contradicts twice over — present-and-FILTERED under
    `.venv/` with no companion key at all, and present under the INCOMING spelling on a
    case-insensitive filesystem. Its ABSENCE is documented everywhere as proving nothing, which is
    the CARD's reason for expecting a reader to take the printed list for the size of the loss;
    that last step is about readers and was measured on neither side.

    NAMING IS SAFE AND DESCENDING WOULD BE WORSE THAN NOISY, which is a sharper reason than the
    "files outside the checkout" one above and was measured rather than reasoned. `check-ignore` is
    rc=0 on the symlink ITSELF (`out/to_dir`) and rc=0 on one nested under a real subdirectory
    (`out/sub/deep_link`) — the symlink is the LEAF, and only a symlinked ANCESTOR is fatal. Feed it
    a path BEYOND the symlink and it is `fatal: pathspec 'out/to_dir/inner.txt' is beyond a symbolic
    link`, rc=128 — the same fatal the SUBMODULE paragraphs below are about, reached by the other
    road. Measured, `printf 'out/a.txt\0out/to_dir/inner.txt\0'` exits 128 with `out/a.txt` already
    on stdout. So walking in would not merely name paths the merge never touches, it would feed
    `_ignored_of` paths it must throw away. Checked in the OTHER direction too, by 836's second
    pass: every symlink this now names is a LEAF, so no path it produces carries a symlinked
    ANCESTOR, and `check-ignore` over fifteen shapes — including symlink-to-symlink-to-directory, a
    `-> .` self-loop and a dangling one — is rc=0 with empty stderr and all fifteen echoed.

    ITS PREMISE — that a directory named by the incoming diff is a directory the merge REPLACES —
    IS FALSE FOR A SUBMODULE, and it is handled at TWO different places, neither of them this
    walk's own body. A pointer-bump entry is satisfied in the index alone (measured: ` M sub`, the
    submodule still at its old commit, its files untouched — with the ONE exception
    `_incoming_displacing_paths` names, a human's plain file at the gitlink path, which the bump
    does destroy), so `_incoming_displacing_paths` drops those entries before they arrive.

    OTHER SHAPES DO STILL ARRIVE WITH A SUBMODULE ON DISK, and an earlier draft of this paragraph
    claimed the typechange was the only one. The independent second pass refuted that by building
    another: an ordinary tracked directory `vendor/` that merely CONTAINS a submodule, replaced
    upstream by a file — a plain ADD with no gitlink on either side, so the mode filter rightly
    keeps it, and the walk goes straight into `vendor/sub`. The general statement is that ANY
    incoming path landing on an ancestor of a gitlink reaches here. So the walk PRUNES gitlink
    subtrees (`gitlinks`, from `_index_gitlink_paths`) rather than pretending they cannot occur —
    and pruning is not a guess about what dies, it is the observation that no path inside a live
    gitlink can be put to `check-ignore` in the form this code must use. What the pruning does NOT
    do is stop those files from dying: on a typechange they really are deleted, and naming them is
    VMCP-247 (838).

    SO `dirnames` IS READ TWICE: the gitlink PRUNE (837) mutates the list in place, then the
    `islink` pick (836) reads what SURVIVED. The order matters in exactly ONE shape, and saying
    which is the difference between a rule and a slogan — measured both ways rather than argued.
    For an ORDINARY submodule the order is IRRELEVANT: the gitlink is a real directory, so the
    `islink` pick skips it whichever side of the prune it runs on, and both orders answer
    `['vendor']`. It becomes load-bearing when the gitlink PATH ITSELF is a symlink-to-a-directory
    on disk — a deinitialised submodule someone replaced by a link — where prune-then-pick answers
    `['vendor']` and pick-then-prune answers `['vendor/sub']`, naming a path inside a live gitlink,
    which is the one thing 837 established must not be handed on. Pinned by a test that passes
    `gitlinks` directly, so it needs no submodule. The pick is
    also a FILTER rather than a wholesale read: a REAL subdirectory is not a path that dies, its
    files are, and they are named individually by this same walk. Both properties have their own
    test, the filter's added because the sweep round that removed it killed nothing. Note WHY that
    round was killable at all: naming every `dirname` ADDS a name, and the caller de-dupes only
    exact repeats (`if candidate not in seen`), so a genuine DUPLICATE from in here would be
    invisible end to end — measured by the second pass — while an EXTRA one is not.

    THIS GAP IS A LOSS CHANNEL, AND ROUND 2's HEADING FOR IT — "NOT A LOSS CHANNEL" — IS
    WITHDRAWN RATHER THAN QUALIFIED. That heading named the MECHANISM, `onerror=None`, while it
    was measured only on permission shapes, and the mechanism swallows EVERY `OSError`. What
    varies across the gap is whether the casualty is NAMED, and no axis measured so far answers
    that with a clean yes. On the PERMISSION axis two different things do the saving on the rows
    below — which is the whole of VMCP-253 (852) and the part any one-line summary gets wrong —
    but they are not everything, and the last paragraph here names what gets past them. `os.walk`
    USED TO default to `onerror=None` here — VMCP-281 (940) replaced it with a recording callback,
    so a subdirectory the walk cannot DESCEND into is still SKIPPED but no longer skipped in
    SILENCE: `out/` holding `a.txt` plus a `chmod 000` `locked/` expands to
    `['out/a.txt']`, naming neither `out/locked` nor its content, and the caller now reports
    `overwritten_ignored_incomplete`. The names below are unchanged by that fix — it buys the
    reader a signal, never a name and never the bytes. An earlier draft called THAT
    tree a second route to a silent present-and-incomplete report; the second pass refuted it and
    the refutation reproduces — `out` is still a directory afterwards, the victim is alive and no
    `overwritten_ignored` key is emitted (what the sync CALLS it is the paragraph below).

    THE OTHER AXIS IS WHERE THE BYTES GO UNNAMED, AND IT NEEDS NO `chmod` AT ALL. This walk joins
    `root` to `rel`, so every syscall it makes carries the ABSOLUTE path, while git does not fail
    on the same tree — so there is a band of lengths where git deletes the file and `os.walk` takes
    ENAMETOOLONG on it and skips it in silence. WHERE git's immunity comes from is not established
    here, and do not write that `_run_git`'s cwd is what buys it: that was this paragraph's first
    guess, and the second pass refuted it by running git from `/` with an absolute `-C`, rc=0.
    TWO STANDS, and do not weld them. The MECHANISM one, no git in it: 24 nested directories built
    by descending with `chdir` so every name stays short, `PATH_MAX` 1024, deepest path 939
    relative and 1062 absolute — `os.scandir` on the absolute form raises errno 63, `os.listdir` on
    the relative one from the checkout succeeds, `os.walk` rooted AT that overlong
    directory names nothing at the default and reports exactly one error when given an
    error-RECORDING `onerror`. Read that silence as being about the ERROR and not about names:
    rooted at `out/`, which is what this function actually walks, the same default still names the
    shallower files and loses only what lies past the limit. The END-TO-END one
    is a separate tree with no `chmod` anywhere, checkout root 122 characters, deepest path 947
    relative and 1070 absolute: the merge GOES, `updated: True`, `out` becomes a plain file, all 25
    files holding bytes are destroyed, `overwritten_ignored` names 24 and
    `overwritten_ignored_truncated` is ABSENT — one file with bytes died unnamed. Git starts
    failing too once the RELATIVE path passes 1024: at 26 levels the answer is `half-applied` with
    `out` still a directory. Do NOT read the far side of that as safe — this stand did not check
    survival there, and the second pass did and reports unnamed byte losses continuing beyond it
    and growing with depth. So `overwritten_ignored`
    used to come back PRESENT and INCOMPLETE with nothing saying so, which is a loss of the
    #710/#806 class. Measured on FOUR different trees by four readers — both of this card's
    reviewers, the implementer of VMCP-281 (940) and again here — with a different file count each
    time (15/14, 22/21, 25/24, 28/27) and the same shape: one casualty per run reaching NEITHER
    key. Read this as a THIRD built road to present-and-incomplete, sister to
    VMCP-245 (836)'s symlink in `dirnames`, and not as a census — roads are counted by who has
    built one.

    VMCP-281 (940) CLOSED THE SILENCE AND NOT THE LOSS, which is the human's choice on that card
    (variant B of four) and not a partial job. `overwritten_ignored_incomplete` now counts the
    PLACES this walk was denied, so present-and-incomplete is an expressible state instead of one
    that reads as a complete account. The bytes still die: git destroys them, a hand-typed
    `git pull --ff-only` does the same, and VMCP-240 (806) settled that this whole feature is a
    post-mortem and explicitly not a guard. NAMING the deep casualty needs the walk to descend by
    `dir_fd` instead of by absolute path — measured on that card as working (25 of 25, zero
    errors) and declined for now, because it replaces `os.walk` wholesale and four behaviours hang
    off its semantics: the symlink-in-`dirnames` classification, the nested-gitlink prune, the
    `_MAX_DIR_EXPANSION` cap and the empty-directory branch.

    TWO OMISSIONS COMPOUND THERE, and naming only one of them is where the round-1 text went
    wrong. The CONTENT is lost to the failing descent — counted since 940, still unnamed. The
    directory ENTRY `out/locked` is lost to the
    deliberate real-subdirectory filter above, which omits it because its contents are named
    INSTEAD — sound whenever the descent works, and exactly wrong when it does not, since then
    there are no contents to stand in for it. "An UNREADABLE subdirectory" is also the wrong axis
    for the failing descent, because the bits that stop it need not sit on the directory that goes
    unnamed: measured, `out/mid` at `600` (readable, NOT traversable) has its own `scandir` SUCCEED
    and enumerate `locked`, which reaches `dirnames` from `d_type` with no stat at all, while the
    descent into `out/mid/locked` fails on the PARENT's missing `+x`. That is VMCP-245 (836)'s
    `dirnames` road reached a second way, and the card named the shape as unmeasured. A SYMLINK
    under that same parent is still named, which is measured rather than assumed and came out
    against the guess. The asymmetry is `d_type`: asked about the two entries under that `600`
    parent, `is_dir()` answers True for the real directory straight from the readdir record, while
    for the symlink it must resolve, and that stat is DENIED — it RAISES EACCES rather than
    returning False, and `os.walk` files the entry as not-a-directory. So `out/mid/to_dir` lands
    in `filenames` and is named from THERE instead of through the `links` pick: the classification
    moves, the name survives.

    THE HEDGE THAT USED TO CLOSE THIS PARAGRAPH — whether EVERY permission shape refuses — IS
    SPENT. Shapes built end to end through `sync_main_checkout` against a real bare origin, git
    2.50.1 on macOS/APFS, victims re-checked with the modes RESTORED (`lexists` answers False on
    EACCES, so the obvious probe reports a live file as dead):
      * a DIRECTORY HOLDING A FILE AS A DIRECT CHILD at `000`, `100`, `400` or `500`, a `000`
        such directory two levels down, and one whose PARENT is `600`: the merge REFUSES,
        `updated: False`, `out` is still a directory, that child is ALIVE and no
        `overwritten_ignored` key comes back for it. Never shorten that last part to "no key",
        which the last paragraph makes into a self-contradiction: on a multi-path incoming commit
        these same rows DO carry `half_applied`. BOTH qualifiers in the opening clause are part
        of the row and not scene-setting, each measured by removing it. Strip the CONTENT and
        `400`/`500` stop refusing at all. Move the bytes one level DEEPER, into a `700`
        subdirectory of the restricted one, and at `500` git unlinks them: `updated: False` and
        `out` still a directory hold as before and the direct child is untouched, but the code is
        `half-applied` on a SINGLE-path commit and `overwritten_ignored` names the deep casualty.
        The expansion is short in all of them EXCEPT `400` and `500`, which name
        `out/locked/precious.txt` perfectly well;
      * an unreadable FILE inside a READABLE directory: the merge GOES, `updated: True`, the file
        is DESTROYED — and `overwritten_ignored` NAMES it. Measured at `000`, `400` and `100`
        alike, so it is the SHAPE and not the bits.
    So the answer is NO, not every shape refuses — and "nothing carrying BYTES dies unnamed"
    survives anyway ON THIS AXIS, by a DIFFERENT mechanism on that one row: the walk NAMES the
    dead file, because permissions on a FILE do not affect the enumeration of its directory ENTRY
    while permissions on a DIRECTORY do.
    Keep the two apart. The file row is the one the conclusion rests on and the only row IN THE
    LIST ABOVE where anything dies, so it is pinned by
    `test_an_unreadable_FILE_is_destroyed_by_the_merge_and_named_anyway`. Do not drop that
    qualifier to "the only shape where anything dies": the DEEP variant of the first row kills
    bytes too — and names them — and the empty-directory row kills a directory that holds none.

    WHY EACH DIRECTORY SHAPE REFUSES IS NOT ONE REASON, AND "GIT CANNOT DELETE WHAT IT CANNOT
    READ" REACHES TWO OF THE FOUR MODES — three of the six rows above. That sentence stood over
    all of them for one round while the same paragraph listed three different git messages, i.e.
    it carried its own refutation; keep the denominator explicit, because "two" and "three" are
    both right here for different readings and a bare count invites the wrong one. Read off the
    syscall that fails, modes left in place. READ refusal, `cannot opendir`: `000` and `100`, and
    the two-level row, which is the same `000` one level deeper — the denied operation is READDIR,
    the very one the walk loses, so there the short expansion and the refusal have ONE cause.
    TRAVERSE refusal, `cannot lstat`: `400`, where readdir SUCCEEDS and `os.listdir` returns
    `precious.txt` and so does git's message — and ALSO the `600`-PARENT row, whose bits are not
    on the doomed directory at all, which is why it needed naming here rather than being left to
    the reader. WRITE refusal at `500`, where git reads all of it (`listdir`, `stat` and `cat`
    inside all succeed) and dies on the write bit — `cannot unlink` when the entry it must remove
    is a file or a symlink, and `cannot rmdir` when that entry is itself a directory, which is a
    FOURTH message and one bucket, since the denied bit is the same. Even for the read pair
    "cannot read" wants care: `100` denies the LISTING while leaving a known name readable.

    WHY MOST SHAPES ON THIS AXIS DO NOT GO THE OTHER WAY — an ARGUMENT, flagged as one because it
    is not a measurement, and narrowed TWICE: round 2 gave it the whole axis, and its premise
    understated what the walk itself needs. The walk needs `r` on the directory to LIST it and
    `x` to DESCEND, which is measured rather than reasoned — at `400` the expansion names a direct
    child FILE and never reaches a file inside a `700` subdirectory of it, while at `500` it names
    that deep file too. Say FILE: a direct child that is a DIRECTORY is named nowhere at either
    mode, by the real-subdirectory omission rather than by any bit. Git must unlink the entries
    before it can rmdir the directory, so
    for a directory with CONTENT it wants `r`, `w` and `x` there plus `x` on the ancestors, which
    is still a superset, and that is what those rows refuse on. It does NOT extend to an EMPTY
    directory — and NOT because git needs less there, which is the tempting way to write this and
    is false. Measured with `os.rmdir` alone, an empty directory at `000`, `100`, `400` or `500` is
    removed FINE under a `700` parent and refuses EACCES under a `500` one, while under that same
    `700` parent a non-empty one is ENOTEMPTY at all four until its entries go. Two parent modes
    were put to it and not a survey of them: so the extra bits are bits for the
    ENTRIES, and the bare SYSCALL wants `w` and `x` on the PARENT and nothing on the directory. GIT
    is not the bare syscall — it opendirs first, which is exactly why the empty rows at `000` and
    `100` still refuse with `cannot opendir`. What the argument misses sits on the WALK's side
    instead: at `400` and `500` the walk reads that directory perfectly well and still names
    nothing, because it is EMPTY and an empty real subdirectory is what the filter drops. The two
    permission sets never diverge there at all — the short expansion has no permission cause.

    AND THE EMPTY DIRECTORY IS A ROW MEASURED THE OTHER WAY, which round 2 recorded backwards. It
    wrote that the second pass had tried four further shapes and broken none; rebuilt here on the
    same end-to-end stand over the four modes above, three refuse in every one of them and the
    fourth refuses in two and not in the other two — so read 3/1 as a count of SHAPES and never of
    cells, and the modes put to it are those four. Refusing at all four modes: one holding
    only an EMPTY SUBDIRECTORY (`cannot lstat` at `400`, `cannot rmdir` at `500`), one holding only
    a SYMLINK (`cannot lstat`, then `cannot unlink`), and a SECOND ignored directory sorting after
    the blocker, whose own bytes come through untouched. The EMPTY unreadable directory refuses at
    `000` and `100` with `cannot opendir` — and at `400` and `500` the merge GOES: `updated: True`,
    `out` becomes a file, `out/locked` is destroyed and `overwritten_ignored` holds `['out/a.txt']`
    alone, so that directory is named NOWHERE. Its expansion is short for a reason that is NOT
    `onerror`: `scandir` on it SUCCEEDS and returns nothing, and the name is dropped by the
    EMPTY-real-subdirectory omission this docstring already records near the top. No BYTES go with
    it, an empty directory having none — so what this row corrects is a recorded MEASUREMENT, not
    the byte reading of the row. What the argument does NOT cover is TOCTOU — modes changing
    between the probe and the merge — and that gap belongs to no key in particular.

    HONEST BOUND, AND ITS FIRST WORD IS THE AXIS RATHER THAN THE MODE LIST — round 2's version
    listed only other spellings of PERMISSIONS, which reads as "the unexplored part is more
    permissions". It is not: `onerror=None` is indifferent to which `OSError` it eats, and the
    `PATH_MAX` band above is one non-permission errno already measured going the other way, so
    the list here bounds the AXIS and not the gap. Within the axis: those are `chmod` shapes on
    APFS, one owner; ACLs (`chmod +a`), `chflags`, and running as another user or as root are NOT
    measured and their semantics differ, so this is "none of these" and never "none at all".
    What to REPORT for a directory nobody can read stays
    a product question and is deliberately not answered here — but do NOT restate the reason for
    that as "the human already sees `blocked` carrying git's own message". THAT IS A PROPERTY OF
    THE STAND rather than of the permission shape: those rows were built with an incoming commit
    touching the ONE path `out`, and giving it two more ordinary paths — what a real main-checkout
    ff looks like — makes every refusing shape above answer `half-applied` instead, with
    `half_applied: ['README.md', 'other.txt']` already written, a code SKILL.md routes differently
    because it means a human must go look. Git's message survives into THAT reason and then stops:
    sweeps 2 and 3 over the same checkout answer `blocked` again carrying "Your local changes …
    would be overwritten", which says nothing about the unreadable directory at all, while the
    single-path stand repeats `blocked` with the message intact. So the two flavours differ in
    what a human is told on every tick after the first.

    AND "NO KEY EMITTED" IS A PROPERTY OF THE STAND TOO — do not close this on "the cost of doing
    nothing is about legibility rather than loss", which is where round 2's own draft landed one
    rung below the correction it was making. Add ONE more incoming path that displaces IGNORED
    content and sorts BEFORE the blocker, and the same refusing shapes answer `half-applied`
    carrying `overwritten_ignored` with a human's bytes already replaced. Measured twice, and the
    second is #806's own shape rather than a contrivance: upstream force-adds `img.png` into a
    checkout that ignores `*.png` and holds its own file there, `git status` empty beforehand,
    afterwards `half_applied: ['README.md', 'other.txt']`, `overwritten_ignored: ['img.png']`, the
    human's bytes gone — while `out` is still a directory and the locked directory's DIRECT CHILD
    is still alive (its own DEEPER content is a separate row above). So
    a tick that REFUSES on this directory can still carry a loss somewhere else in the same
    commit; that loss is #835's half-apply and not this gap, and it is NAMED, which is why the
    conclusion above survives it. Round 2 closed on a universal over "every shape and every
    flavour measured" — `updated: False`, `out` still a directory, this directory's victim alive
    — and TWO rows above break it in different clauses, so read it by rows rather than closing on
    one sentence. The EMPTY directory at `400`/`500` breaks the first two: the ff COMPLETES and
    takes a directory carrying no bytes. The DEEP victim at `500` breaks the third: the bytes go
    while `updated: False` and `out` still a directory both hold, which is why "the victim is
    alive" needed the word DIRECT CHILD rather than a mode.

    AND THERE IS NO SURVIVING UNIVERSAL TO PUT IN ITS PLACE. "Nothing holding bytes dies unnamed"
    fails ON this axis and not only off it, and the input is a RENAME rather than a mode: call the
    deep victim `precious.pyc` and the same `500` row destroys it with NO `overwritten_ignored` key
    at all, because the regenerable-name filter drops it before the probe can report it — the bound
    this docstring already records as present-and-FILTERED under `.venv/`, reached from a second
    direction. The filter moves the CODE too, not just the name: `half-applied` with a `.txt`
    victim, `blocked` with the `.pyc` one, the file equally dead either way. So read the rows
    rather than a summary, and read `blocked` as "both probes stayed silent", never as "nothing was
    destroyed". The cost of doing nothing is about the legibility of THIS gap — not a promise
    about the tick.
    """
    full = os.path.join(root, rel)
    if os.path.islink(full) or not os.path.isdir(full) or rel in gitlinks:
        return [rel]
    if _holds_a_dot_git(full):
        return [rel]
    inside: list[str] = []
    # VMCP-281 (940), variant B, decided by the human on that card. `os.walk` defaults to
    # `onerror=None`, which swallows EVERY `OSError` and not just permission ones — so a place the
    # walk cannot descend contributed no name AND no signal, and the caller's key came back
    # PRESENT, NON-EMPTY and SHORT with nothing to distinguish it from a complete answer. The
    # information already existed here; only the default callback threw it away.
    #
    # RECORDS PLACES, NEVER FILES, and the difference is measured rather than assumed: one denied
    # directory hides its whole subtree, so on the ENAMETOOLONG stand depth 28 lost ONE file and
    # depth 30 lost THREE while both reported exactly ONE error. Anything that turns this into a
    # loss estimate is wrong in the unsafe direction.
    #
    # It cannot fail the sync: the callback only appends, and a caller that passes nothing keeps
    # the previous behaviour exactly. The repo rule stands — a diagnostic may cost the REPORT,
    # never the fast-forward.
    def _record(err: OSError) -> None:
        if unreadable is not None:
            unreadable.append(str(getattr(err, "filename", "") or ""))

    for dirpath, dirnames, filenames in os.walk(full, onerror=_record):
        # Prune nested GITLINKS in place: nothing inside one can be put to `check-ignore`, so
        # walking in costs bisect calls and yields no name (`_index_gitlink_paths`).
        dirnames[:] = [d for d in dirnames
                       if os.path.relpath(os.path.join(dirpath, d), root) not in gitlinks]
        # THEN a STRAY NESTED CLONE, and the ORDER of these two is what keeps them separate
        # questions rather than one merged guess (VMCP-256 (858)). The gitlink prune above asks
        # the INDEX and means "do not ask about what cannot be answered"; this asks the DISK and
        # means "a foreign repository is ONE casualty, not several hundred". A populated
        # submodule matches both, and it is already gone from `dirnames` by the time we get
        # here, so nothing is classified twice. Symlinks are excluded because the `links` pick
        # below already names them once — including a symlink that points AT a clone.
        clones = [d for d in dirnames
                  if not os.path.islink(os.path.join(dirpath, d))
                  and _holds_a_dot_git(os.path.join(dirpath, d))]
        dirnames[:] = [d for d in dirnames if d not in set(clones)]
        # THEN read what survived: a symlink-to-a-directory is in `dirnames` and in no other list,
        # so it is named here or nowhere. One name per symlink, never its contents. AFTER the prune
        # on purpose — a pruned gitlink must stay unnamed (its files are VMCP-247 (838)'s).
        links = [d for d in dirnames if os.path.islink(os.path.join(dirpath, d))]
        for name in filenames + links + clones:
            inside.append(os.path.relpath(os.path.join(dirpath, name), root))
            if len(inside) >= _MAX_DIR_EXPANSION:
                return inside
    # An EMPTY directory keeps its own name: nothing is lost with it, but a caller that gets an
    # empty list back for a path that exists would read it as "this path is not at risk". Nothing
    # in the suite constructs that state — measured, `return inside` alone is control 0 failed /
    # 0 failed — so this branch is defensive and is named rather than pretended to be pinned.
    return inside or [rel]


def _doomed_ancestor(root: Path, rel: str) -> str | None:
    """For an incoming path that is NOT on this disk, the ANCESTOR the merge must delete, or None.

    THE MIRROR of `_expand_if_directory`, and the input that disproved a sentence this module
    shipped one round earlier — "a path that is not on this disk has nothing to lose". Built by
    the independent review of VMCP-240 (806), reproduced here before it was fixed. Upstream turns
    a NAME into a DIRECTORY (`out/x.txt` arrives) while the checkout holds its own IGNORED FILE
    at `out`. Measured on real git 2.50.1: `git status --porcelain` empty before AND after, the
    incoming diff naming ONLY `out/x.txt` — the path that dies, `out`, appears in no entry of it
    — `lexists` dropping that incoming child, the probe answering `[]`, and the sync reporting
    `updated: true` with no key while the human's bytes were gone. Depth is not the point: a
    local ignored file `deep` under an incoming `deep/a/b/y.txt` dies the same way three levels
    up, which is why this WALKS instead of asking about one parent.

    WHAT IT ANSWERS, every branch run rather than reasoned: the SHALLOWEST ancestor that is not a
    real directory. A real DIRECTORY is walked THROUGH — the incoming path merely does not exist
    inside it yet, and nothing is displaced. Anything else is what git removes to make room: a
    file, or a SYMLINK, INCLUDING one that points at a directory. That last is what `isdir` alone
    gets wrong, so `islink` comes first for the same reason it does in `_expand_if_directory`:
    measured, a local ignored `linkdir -> realdir` under an incoming `linkdir/y.txt` leaves
    `realdir/inside.txt` untouched and replaces the SYMLINK with a real directory, i.e. `isdir`
    (which follows) answers True about a path that dies.

    SHALLOWEST-FIRST AND WALK-THROUGH ARE TWO SEPARATE PROPERTIES, and the first draft of this
    walk had NEITHER — it went bottom-up and stopped at the first ancestor that existed. Both
    come from `os.path.lexists` following every component EXCEPT the last, so asking about `a/b`
    when `a` is a symlink silently answers about `realdir/b`. The mutation sweep is what forced
    this paragraph apart: a round that only flipped the ORDER killed no test, because on the
    input that started this the other property already covers it. Measured over all four
    combinations on three shapes — `A` = ignored symlink `a -> realdir` with `realdir/b` a
    DIRECTORY, `B` = the same with `realdir/b` a FILE, `C` = a real directory `keep` holding an
    ignored FILE `keep/sub`, incoming `a/b/c.txt`, `a/b/c.txt` and `keep/sub/new.txt`:
        shape   truth      top+through   top+STOP   bottom+through   bottom+STOP
        A       a          a             a          a                None
        B       a          a             a          a/b              a/b
        C       keep/sub   keep/sub      None       keep/sub         keep/sub
    So `top+through` is the only column right on all three, and each defect owns a shape: STOP
    misses `C` outright, bottom-up mis-NAMES `B` (it reports a path inside the symlink's target,
    which the merge does not touch), and `A` — the shape actually built on real git, where the
    merge is rc=0, `git status --porcelain` says `?? realdir/` and nothing else before AND after,
    `realdir/b/keep.txt` survives and the ignored symlink `a` is replaced by a real directory —
    is missed only by the combination the first draft shipped. Do not "simplify" this back: two
    of those three columns look correct on whichever single shape you happen to try.

    At most ONE name comes back per incoming path, so this adds nothing unbounded to the sweep;
    several incoming paths under one dead ancestor name it repeatedly, and the caller de-dupes.
    """
    parts = rel.split("/")
    for cut in range(1, len(parts)):
        ancestor = "/".join(parts[:cut])
        full = os.path.join(root, ancestor)
        if not os.path.lexists(full):
            continue
        if os.path.islink(full) or not os.path.isdir(full):
            return ancestor
    # Nothing displaced, and this is the ORDINARY answer rather than an edge — most incoming
    # paths are simply new files. Two ways to arrive: every ancestor is a real directory (the
    # walk fell through), or `rel` has no "/" at all, so there was no ancestor to ask about.
    return None


_GITLINK_MODE = "160000"

# A bisect over `check-ignore`, so an unaskable path costs itself rather than the batch. The bound
# is real and its arithmetic is worth writing down, because the second pass measured this cap
# BITING. Isolating `k` unaskable paths among `n` costs O(k·log n) calls: 13 at n=100, 15 at 231 and
# 17 at 500 for a single one (replayed), but `2k−1` when the whole batch is unaskable, so 64 covers
# only about 32 such paths. `_MAX_DIR_EXPANSION` = 500 admits batches needing far more, and those
# two numbers are deliberately NOT reconciled — what closed the gap is `_index_gitlink_paths`
# pruning the one producer that generated unaskable paths in BULK (a submodule, one per file:
# measured, 30 files inside one exhausted this budget and lost an askable `z.png`, while 25 did
# not). Hitting the bound returns FEWER names, never a wrong one — measured, the short answer is a
# SUBSET — which is the same one-way reading the key already has.
_MAX_CHECK_IGNORE_CALLS = 64


def _incoming_displacing_paths(root: Path, remote: str) -> list[str] | None:
    """The incoming path set, minus the entries that DISPLACE NOTHING in the working tree.

    WHY THIS IS NOT JUST `--name-only`, and it is VMCP-246 (837): a SUBMODULE pointer move is an
    ACMT diff entry (`M` at the submodule's path) that git satisfies entirely in the INDEX. The
    working directory is left alone — measured on real git 2.50.1 after such a merge:
    `git status --porcelain` says ` M sub`, `git submodule status` still names the OLD commit, and
    a file inside the submodule still holds its old bytes. So the premise `_expand_if_directory`
    rests on — a directory in the diff is a directory the merge REPLACES — is FALSE for a gitlink,
    and every path it hands back from inside one is a FALSE VICTIM. `--raw` carries the mode bits
    that say so at no extra cost: one call, same filters, `:<srcmode> <dstmode> <src> <dst> <st>`
    then the path, both NUL-terminated.

    NO CONFIG KNOB TURNS THAT INTO A LOSS, which is asked because #766 is this repo's standing
    lesson that one performance setting can switch a whole guard off. The knob to suspect is
    `submodule.recurse`, and it was MEASURED rather than reasoned about: with `submodule.recurse =
    true` set in the checkout, the same pointer bump still leaves the submodule at its OLD commit,
    an ignored file inside it alive, and `git status` at ` M sub`. That is a fact about
    `merge --ff-only`, which is the only command this module runs here — it never types `pull`.

    THE TEST IS BOTH MODES, NOT EITHER, and the shape that proves it is an incoming submodule ADD
    (`:000000 160000 … A`) over the human's own IGNORED FILE at that name: `git status --porcelain`
    empty beforehand, `merge --ff-only` rc=0, the file replaced by an empty directory — and the
    local path is an ordinary file, so `check-ignore` answers about it and the report NAMES it.
    Dropping that entry because its DESTINATION is a gitlink would swallow the exact loss this
    feature exists for.
    THE TYPECHANGE IS NOT THAT PROOF, and this draft's first version said it was. `160000` on the
    source side only (upstream replacing the submodule with a file) does destroy far more — rc=0,
    the whole submodule working directory, ignored and NOT-ignored content alike — but every one of
    those victims lives INSIDE a live gitlink, where `check-ignore` cannot be asked at all, so the
    report is silent about them with this filter and without it. Keeping the entry is still right
    (the walk under it is correct, and a future answer for those paths would need it), but it buys
    no name today. Filed as VMCP-247 (838).

    THE ONE INPUT WHERE A PURE POINTER MOVE DOES DESTROY SOMETHING was built before this filter
    was trusted, and it is outside this probe's remit for a reason that predates the filter: if the
    human has replaced the submodule's directory with a plain file, the pointer bump wipes it
    (measured — rc=0, an empty directory in its place). But that path is TRACKED (index mode
    `160000`), so `check-ignore` never reports it with or without this filter — the index filtering
    the next step relies on drops it — and unlike an ignored file it is VISIBLE to the human's own
    `git status`, as ` T sub`. Nothing that used to be reportable stopped being reportable here.

    UNRECOGNISED OUTPUT IS KEPT, NOT DROPPED. The pair-wise walk is what `--raw -z` promises, and
    a field that does not open with `:` where metadata is due is passed through as a path instead
    of being discarded. That direction is deliberate for a diagnostic: an extra candidate costs
    one `check-ignore` answer ("not ignored") and a dropped one costs a silence.

    A FAILED READ IS `None`, NOT `[]` (VMCP-252, round four). The two used to be the same value, so
    a caller could not tell "the incoming range displaces nothing" from "git would not tell me".
    `_ignored_paths_the_ff_will_overwrite` folds `None` back to `[]` on purpose — an empty answer is
    exactly its documented give-up, and the key it feeds is read one-way for precisely this reason —
    while `_incoming_paths_absent_here` propagates it, because the sentence IT feeds makes a
    positive claim and must be able to withhold it.
    """
    changed = _run_git(
        ("diff", "--raw", "-z", "--no-renames", "--diff-filter=ACMT", f"HEAD..{remote}"),
        root, None, env_extra={"GIT_OPTIONAL_LOCKS": "0"},
    )
    if changed.returncode != 0:
        return None
    fields = [field for field in changed.stdout.split("\0") if field]
    paths: list[str] = []
    at = 0
    while at < len(fields):
        meta = fields[at]
        if not meta.startswith(":") or at + 1 >= len(fields):
            paths.append(meta)
            at += 1
            continue
        path = fields[at + 1]
        at += 2
        if meta[1:].split(" ")[:2] == [_GITLINK_MODE, _GITLINK_MODE]:
            continue
        paths.append(path)
    return paths


def _ignored_of(root: Path, paths: list[str]) -> list[str]:
    """Which of `paths` this checkout IGNORES — asked so an unaskable path costs itself, not the batch.

    `git check-ignore` does not answer path-by-path: a path it cannot resolve is a FATAL for the
    whole invocation, and it exits 128 having printed only the answers it had already reached. So
    a single bad path used to discard the report for every path beside it — VMCP-240 (806) paid
    that for a path beyond a symlink, and VMCP-246 (837) for a path inside a submodule
    (`fatal: Pathspec 'sub/x.png' is in submodule 'sub'`). Both were then closed at their
    producers, and BOTH TIMES the closure was an argument about the producers rather than a
    measurement over all inputs. This makes the give-up LOCAL instead, so the next unknown
    spelling costs one name.

    KEEPING THE FATAL CALL'S OWN STDOUT WOULD NOT DO, and that is why this bisects rather than
    salvages: git prints a complete, NUL-terminated record for each answer it reached, so the
    prefix IS trustworthy as far as it goes — measured, `printf 'a.png\\0sub/x.png\\0'` in a
    checkout ignoring `*.png` leaves `a.png\\0` on stdout at rc=128. But it stops at the first
    bad path, so everything after it is simply never examined; asking the halves separately is
    what reaches those. The order of the report is preserved because the halves are concatenated
    in order.

    rc=1 IS NOT AN ERROR — it means "none of these are ignored", the ordinary case — so only
    `not in (0, 1)` triggers the split. Grading it `== 0` instead changes no LIST at all, only the
    cost (measured: 31 calls against 1 on a 16-path batch), which is why no test can see it.

    TWO LOSSES REMAIN, and the second is the one the title understates — named here because the
    second pass measured it rather than deduced it. A single path that still cannot be answered
    yields nothing. And exhausting `_MAX_CHECK_IGNORE_CALLS` drops whatever the split had not
    reached yet, which CAN include askable paths: an unaskable path then does cost the paths around
    it. `_index_gitlink_paths` is what keeps that from arriving in bulk, and it is why the honest
    unit is "an unaskable path" and not "one name". Both are why the key's one-way reading now
    covers a PRESENT key as well as an absent one.
    """
    budget = _MAX_CHECK_IGNORE_CALLS

    def ask(batch: list[str]) -> list[str]:
        nonlocal budget
        if not batch or budget <= 0:
            return []
        budget -= 1
        proc = _run_git(("check-ignore", "-z", "--stdin"), root, None,
                        env_extra={"GIT_OPTIONAL_LOCKS": "0"},
                        stdin_text="\0".join(batch) + "\0")
        if proc.returncode in (0, 1):
            return [path for path in proc.stdout.split("\0") if path]
        if len(batch) == 1:
            return []
        half = len(batch) // 2
        return ask(batch[:half]) + ask(batch[half:])

    return ask(paths)


def _ignored_paths_the_ff_will_overwrite(root: Path, remote: str,
                                         unreadable: list[str] | None = None) -> list[str]:
    r"""Name the IGNORED files a fast-forward onto `remote` is about to overwrite — VMCP-240 (806).

    THE HOLE THIS EXISTS FOR, measured on real git (2.50.1) rather than reasoned about, three
    branches on one stand — a bare origin, a main checkout, a sibling landing with `git push
    origin HEAD:main`:
      * upstream tracks `shot.png` (it took a `git add -f` there), the main checkout holds the
        HUMAN's own `shot.png` under this repo's `*.png` rule -> `git status --porcelain` prints
        NOTHING, `merge --ff-only` returns rc=0, and the human's file is gone;
      * the same loss WITHOUT any upstream force-add: an UNCOMMITTED rule in the human's own
        `.gitignore` plus an ordinary incoming file at that path -> rc=0, gone. This is the more
        reachable of the two and the reason the check must read the working tree's rules. Its
        status is NOT empty, and the difference is worth keeping: git reports the `.gitignore`
        (`??` when it is new, ` M` when the rule was appended to a committed one) and still says
        nothing whatever about the file that dies;
      * the contrast that makes it a finding at all, and it fails on exactly ONE diff shape:
        untracked-and-NOT-ignored at an incoming path and git refuses outright (`The following
        untracked working tree files would be overwritten by merge`), which is the `blocked`
        branch, where nothing is destroyed. The shape is an entry landing ON a live GITLINK's own
        path as a non-directory — VMCP-247 (838), measured at `469db93`: that TYPECHANGE deletes
        the submodule's whole working directory at rc=0, and untracked-and-NOT-ignored content goes
        with it, as does a file the SUBMODULE tracks and the human has modified. NOT "inside a live
        gitlink", which this bullet said for one round: an incoming path INSIDE one is refused in
        the ordinary way (measured, rc=1, that path named). So for that content there is no refusal
        to contrast with and no key here to name it either: this probe reports IGNORED paths, and
        that is a remit rather than an oversight to widen quietly.
    Same class as VMCP-185 (710): `git status --porcelain` and checkout's own protections do not
    see ignored paths. It is a property of GIT, not a decision of this module — a human typing
    `git pull --ff-only` loses the same file — so this REPORTS and never refuses, by the same
    argument #801 used to reject "only update when the tree is clean": a guard that refuses on
    ignored paths would refuse in a repo whose rulebook TELLS agents to write `shot-<id>.png` and
    `.playwright-mcp/<id>/`, i.e. would stop the sync from ever firing.

    HOW, and why each step is the cheap one. `_incoming_displacing_paths` reads `HEAD..remote` and
    is where the diff's own flags live: it uses `--raw` so the mode bits can drop a SUBMODULE
    POINTER move, which is an ACMT entry that displaces nothing on disk (VMCP-246 (837); the
    measurements and the both-modes test are there). `--no-renames` is LOAD-BEARING BESIDE
    `--diff-filter=ACMT` and not tidiness
    — measured: a rename is status `R`, which `ACMT` filters OUT, so with detection left on the
    pair answers NOTHING for a commit that renamed a file INTO a path this checkout ignores; with
    `--no-renames` the same commit arrives as an add plus a delete and the destination is kept.
    The `D` half of that is belt rather than braces, and the honest reading is that it pins
    nothing: a deleted path is TRACKED here, so `check-ignore` would drop it two steps later
    anyway. Do NOT restate that as "a deletion is not a loss channel" — this docstring did, and
    the second pass disproved it by building one (a directory replaced by a file takes the
    ignored files inside it with it, rc=0); what makes THAT case invisible is not the `D` filter
    but the path never being in the diff at all, and it is `_expand_if_directory` that answers
    it. Then each incoming path is mapped, in python, onto what it DISPLACES on this disk, which
    is two questions and not one. Present (`lexists`, so a broken symlink still counts as
    something being written over — pinned since VMCP-268 (884) by
    `test_a_BROKEN_symlink_at_the_incoming_path_is_still_named`, and for two cards before that it
    was pinned by NOTHING: swapping in `exists` left the whole file green, so this parenthesis
    stated an intention no round could tell from its opposite) — it displaces itself, or, when it
    is a local directory, the
    things inside it that can DIE — its files AND its symlinks, one name each. Not "the FILES
    inside it", the wording that stood for two rounds and lost every nested symlink-to-a-directory,
    and not "the PATHS inside it" either, which over-corrects: see `_expand_if_directory`, where
    both bounds are measured. ABSENT — `_doomed_ancestor`, because "not on this disk has nothing to
    lose"
    is FALSE and was shipped here for one round: an incoming `out/x.txt` over a local ignored
    FILE `out` kills `out`, which is in no diff entry at all.

    Then `_ignored_of`, whose default does the index
    filtering for free — measured, without `--no-index` it does NOT report a TRACKED path
    (`tracked.png` absent from its output while `untracked.png` is there), which is exactly the
    split that matters, since a tracked file is protected by git's own refusal. It also names the
    FULL path inside an ignored directory (`out/deep/file.txt`), where `status --ignored`
    collapses the directory into one entry.

    THE BOUNDS, and the list is the point: a key that means one thing needs its silences written
    down. Do not read the list as a CATALOGUE OF EVERY SILENCE, and do not put a count on it —
    both mistakes have been made here already. An earlier version called itself complete at
    three; the version after it named four MECHANICAL give-ups and was read, by its own author,
    as covering the ground — while the silence that mattered was of a different kind entirely
    (see the last entry).
      * it says these paths were WRITTEN, not that their bytes differed — an incoming file
        byte-identical to the local ignored one is still named;
      * `_is_reproducible_ignored` filters it, exactly as on `released`, so this can be silent
        about a file parked under `.venv/`. The noise argument is WEAKER here than there (an
        incoming commit would have to add a path under `.venv/` for it to fire at all), so the
        filter is inherited for one-word-one-meaning rather than because it is load-bearing;
      * `rc == 1` IS NOT AN ERROR — it means "none of these are ignored", the ordinary case — so
        the grade `not in (0, 1)` is written out to stop anyone "fixing" it into a failure. What
        it does with a fatal has MOVED: it used to `return []` for the whole batch, and since
        VMCP-246 (837) it splits the batch instead (`_ignored_of`), so a path git cannot answer
        costs ONE name and not the report. **It still pins nothing, and saying otherwise was a
        round-two overclaim of this very bullet** — re-measured by the second pass on the shipped
        code: grading `== 0` instead is control 0 failed; that round 0 failed, and the LISTS are
        identical on every shape tried (rc=1 means no path is ignored, so every half is rc=1 and
        every leaf returns nothing). What the bisect changed is the COST, not the answer: `== 0`
        turns the ordinary "nothing here is ignored" reply into a full subdivision, measured at
        31 calls against 1 on a 16-path batch. That is a better reason to keep the grade than the
        one this bullet briefly claimed, and it is also why no test can see the difference;
      * THAT GIVE-UP IS NOW LOCAL, AND IT WAS NOT — this is the one bound in the list that got
        better rather than better-described, and both cards that paid for it are worth naming
        because they are the same defect twice. One unreadable path used to return `[]` for the
        WHOLE batch, discarding names already found and genuinely dying. TWO spellings of the
        fatal are measured on real git, and the second is why the first's fix was not enough:
        `fatal: pathspec '<p>' is beyond a symbolic link` (VMCP-240 (806)) and
        `fatal: Pathspec '<p>' is in submodule '<s>'` (VMCP-246 (837)). Each cost an unrelated
        ignored `shot.png` landing in the SAME commit its entire report. Each was then closed at
        its producer — the symlink one by asking `_doomed_ancestor` FIRST, the submodule one by
        dropping pure gitlink entries in `_incoming_displacing_paths` — and the second one is
        the standing proof of what this file said about the first: "closed is not the same as
        impossible — that is an argument about the two producers, not a measurement over all
        inputs". A third spelling would land the same way, so the batch no longer rides on the
        argument. What DOES still ride on it, and must be read as the residue rather than as
        nothing: the unaskable path ITSELF is still dropped, so where such a path is the victim
        the report names its neighbours and not it. Measured live — upstream replacing a
        submodule with a file merges rc=0, deletes the submodule's working directory, and the
        ignored file inside it cannot be asked about IN THE FORM THIS CODE MUST USE, so it dies
        unnamed while the ignored files beside it are now reported. Say it that way and not
        "cannot be asked at all", which is what an earlier draft said in three places and which
        the second pass refuted with one command: `--no-index` answers about those paths quite
        happily, rc=0. It is unusable HERE because it also throws away the tracked-path filtering
        this report depends on — a REASON, not an impossibility, and the difference matters
        because it leaves VMCP-247 (838) a route instead of declaring the residue closed.
        How many names that costs is bounded by pruning rather than by hope: `_index_gitlink_paths`
        keeps a submodule from contributing one unaskable path per file, which is what used to
        exhaust `_MAX_CHECK_IGNORE_CALLS` and take an askable neighbour down with it (measured at
        30 files inside one submodule, and not at 25). Losing a name stays the right trade, for
        the reason losing the batch was: a diagnostic must never be what breaks the operation it
        describes;
      * AND THAT SILENCE IS NOT ONLY ABOUT IGNORED PATHS — the one entry in this list that is
        outside the key's REMIT rather than inside its give-ups, so no widening of the ask reaches
        it (VMCP-247 (838), measured at `469db93`). The same typechange takes untracked-and-NOT-
        ignored content and a file the SUBMODULE tracks and the human has modified, at rc=0, with
        `git status --porcelain` empty afterwards — that last on the rc=0 branch ONLY: through
        #835's half-applying branch the same typechange leaves ` T sub` in status and rides out in
        `half_applied` instead, so THERE the loss does surface. What BOUNDS the class is the shape
        rather than a promise, and the neighbouring shapes were built rather than reasoned about: a
        plain gitlink DELETE (`:160000 000000 … D`) destroys nothing at all — git refuses to remove
        a non-empty directory and says so (`warning: unable to rmdir 'sub': Directory not empty`),
        the content survives — and neither does the gitlink becoming a real DIRECTORY, whose
        colliding member is refused in the ordinary way. THAT LAST CLAUSE IS ABOUT A NOT-IGNORED
        MEMBER ONLY, and VMCP-265 (877) measured the other half on `5782538`: collide with an
        IGNORED member and the merge is rc=0 with the human's bytes replaced, this key naming only
        the ordinary neighbour outside the gitlink. So the DIRECTORY row bounds the class on git's
        behaviour and not on this report — inside a live gitlink the contrast is absent either
        way, for 837's reason. So this is the TYPECHANGE, not "any
        incoming change to a submodule";
      * the name reported is the one on THIS disk only where the two can differ and git agrees
        they are the same path. On a case-insensitive filesystem (measured on macOS with
        `core.ignorecase=true`) a local ignored `out.png` under an incoming `out.PNG` IS
        reported — but under the INCOMING spelling, so the string handed to the human is not the
        name their file had. That same filesystem used to make this key OVERSTATE, which is the
        one direction everything else here errs against: an incoming commit carrying BOTH
        spellings of a directory name had every dying object inside it named once per spelling
        (measured, three objects came back as five names). VMCP-257 (859) closed it in
        `_same_object_key` — read that docstring before touching the de-dup, because the two
        obvious keys are each measured wrong in their own way;
      * A PRESENT KEY IS NOT A PROOF THAT THE LIST IS COMPLETE, and that is a SECOND reading
        direction rather than a restatement of the next entry. VMCP-246 (837) added a mechanism for
        it — the bisect above reports the askable paths and drops the unaskable ones beside them, so
        partial is a state the key can be in, which is strictly more information than the `[]` it
        replaced. But do NOT date the DIRECTION to 837, which its own first draft did ("before it, a
        non-empty list at least meant no path had failed"): true of check-ignore FAILURES, false of
        completeness, and VMCP-245 (836) measured two older roads to a present-and-short list. The
        `_is_reproducible_ignored` filter above is one, and it is the worst kind — a hand-written
        file under `out/.venv/` is dropped, the key is present naming only its neighbour, and NO
        companion key appears, so there is no signal whatever. `_MAX_DIR_EXPANSION` is the other,
        and it at least surfaces as `overwritten_ignored_truncated`, though understating: measured,
        505 dead files report 500. 836's own defect was a third, present and short with no signal.
        So the list bounds the loss from BELOW and never sizes it;
      * and so AN ABSENT KEY IS NEVER A PROOF THAT NOTHING DIED — the same one-way reading
        `removed_ignored` has. The mechanical routes to absent-with-a-loss are the filter above,
        each `return []` in this function and in the two it calls, the caller's `except`, a
        directory walk stopped at `_MAX_DIR_EXPANSION`, and `_ignored_of` exhausting
        `_MAX_CHECK_IGNORE_CALLS`. The route that is NOT mechanical is a displacement SHAPE this
        function does not model, and it can arrive by EITHER road — do not restate it as "the
        ordinary branch", which an earlier draft did on the strength of the one instance it had.
        Both are built: the shape `_doomed_ancestor` answers reached `[]` through the ordinary
        "nothing at risk" branch, while the shape the walk-first ordering answers reached it
        through the rc grade above, a MECHANICAL give-up, and took an unrelated name with it.
        Two shapes, two roads, and that is why the count came off this list — both were live in
        shipped code, and neither had a place to be written down here.
        The rulebook states the one-way direction to agents rather than leaving it here. Both
        directions are now stated, and 836 merged what used to be a second present-key bullet here
        into the one above rather than leave the tree asserting two provenances for one bound.
    """
    # `or []` restores this function's own contract: an unreadable diff is one of its documented
    # give-ups, and its key is read one-way so that an empty answer proves nothing (VMCP-252).
    changed = _incoming_displacing_paths(root, remote) or []
    # GIT_OPTIONAL_LOCKS=0 there is the module's standing rule (see `_git_inspect`) applied by
    # habit, and it is BELT — say so rather than let a reader think it is doing the work. This
    # probe runs BEFORE the merge, including on the run where the merge is then REFUSED, where
    # nothing of ours should have written in a human's checkout at all; what actually keeps that
    # true is the SHAPE of the calls. Measured: `git diff HEAD..<remote>` is a TREE-TO-TREE
    # comparison and leaves the index mtime alone with or without the variable, as does
    # `check-ignore`, while `git status --porcelain` moves it. The sweep agrees — dropping this
    # env_extra kills no test (see the section header in tests/unit/test_workspace_cmd.py).
    # Asked ONCE, and only about the incoming paths that are local directories — the only ones the
    # expansion can walk into. Empty when there are none, which is the ordinary case.
    gitlinks = _index_gitlink_paths(root, [
        p for p in changed
        if os.path.isdir(os.path.join(root, p)) and not os.path.islink(os.path.join(root, p))
    ])
    present: list[str] = []
    seen: set[tuple | str] = set()
    for p in changed:
        # THE ANCESTOR QUESTION COMES FIRST, and asking it only when `lexists` said "absent" was
        # a bug — the independent second pass built it. `os.path.lexists` follows every component
        # but the last, so an incoming `linkdir/y.txt` "exists" whenever the local ignored symlink
        # `linkdir -> realdir` has a `realdir/y.txt` in it, taking the PRESENT branch and naming a
        # path that displaces nothing while the symlink itself dies unnamed. Worse, that name is
        # then beyond a symbolic link, which makes `check-ignore` exit 128 and discards the WHOLE
        # batch: measured, an ordinary ignored `shot.png` in the same commit died unreported too.
        # Asking the walk first cannot regress the present case — it answers None unless some
        # ancestor is a symlink or a non-directory, and then that ancestor is the real victim.
        ancestor = _doomed_ancestor(root, p)
        if ancestor is not None:
            candidates = [ancestor]
        elif os.path.lexists(os.path.join(root, p)):
            candidates = _expand_if_directory(root, p, gitlinks, unreadable)
        else:
            candidates = []
        # De-duplicated because `_doomed_ancestor` MAKES collisions by construction: every
        # incoming path under one dead ancestor names that same ancestor. Without this a commit
        # adding twenty files under `out/` would report `out` twenty times.
        for candidate in candidates:
            key = _same_object_key(root, candidate)
            if key not in seen:
                seen.add(key)
                present.append(candidate)
    return [p for p in _ignored_of(root, present) if not _is_reproducible_ignored(p)]


def _paths_already_holding_incoming_bytes(root: Path, remote: str,
                                          paths: list[str]) -> set[str]:
    """Of `paths`, the ones whose bytes on disk ALREADY equal the incoming blob — VMCP-252 (851).

    THE REPEAT THIS EXISTS TO STOP, measured on a real stand before it was written: while the
    blocker stands, `--gc` runs every tick and `overwritten_ignored` named `shot.png` on sweep 1,
    sweep 2 AND sweep 3 (inodes 212809910 -> 212810669 -> 212811229), because each failed
    `merge --ff-only` unlinks and recreates that path, so the FINGERPRINT moves again over content
    that has been upstream's since sweep 1. Four messages for one loss, counting the `updated: true`
    sweep that finally heals it. That is the never-read failure VMCP-68 had to split `kept`/
    `expected` to cure. A human chose this fix over living with the noise (851's `call_human`), and
    the reason it is not merely "quieter" is that the same read also stops naming a path where
    nothing died: an ignored file whose bytes already equalled upstream's was named before this
    (measured, stand D). Read that as the key's meaning CHANGING on this branch, not as a false
    positive being removed — the paragraph below says why the difference matters.

    THE READ HAS TO HAPPEN BEFORE THE MERGE, which is the whole discriminator and is easy to get
    backwards: `git rev-parse <remote>:<path>` and `git hash-object <path>` DIFFER before the first
    attempt and MATCH after it. Ask afterwards and every sweep looks like sweep 2, including the one
    where the bytes really died. The caller therefore takes this snapshot beside the fingerprints,
    on the same "afterwards the answer is unrecoverable" reasoning.

    IT FILTERS ONLY THE REFUSAL BRANCH. `updated: true` keeps its UNFILTERED list, which its own
    docstring defends (the merge completed, so everything incoming was written) — so the fourth
    message survives this fix by decision rather than by oversight, and the count goes 4 -> 2, not
    4 -> 1. Widening it there was explicitly not asked for.

    EVERY UNANSWERABLE READ FALLS TOWARDS REPORTING, and that direction is the reason the shape is
    a positive set rather than a filter predicate: a path only leaves the report when this run can
    POSITIVELY show it already held the incoming bytes. Measured routes to "cannot say", each
    keeping the name: a doomed ANCESTOR is not a blob in the incoming tree at all (stand E's local
    ignored FILE `out` under an incoming `out/x.txt` — `cat-file` answers `tree`, so nothing is
    compared and the ancestor is reported, which is right, because it is exactly the path about to
    die); a path git cannot read; a batch whose reply does not line up one-for-one with the ask;
    a symlink or a directory on this disk; and `git hash-object` failing, which costs the WHOLE
    batch rather than one name — the opposite trade from `_ignored_of`'s bisect, and affordable
    only because failing here means reporting MORE.

    "ALREADY HOLDS THE INCOMING BYTES" MEANS RAW BYTES, WHICH IS WHY THE HASH IS TAKEN WITH
    `--no-filters`, and the first draft had this exactly backwards. It hashed the way `git
    hash-object <path>` does — through this checkout's filters — reasoning that the incoming side
    is git's STORED form and only a filtered hash compares like with like. The independent second
    pass built two inputs where that DROPS A PATH WHOSE BYTES REALLY DIED, because a `clean` filter
    need not be invertible: a lossy one (`filter.redact.clean` reducing `SECRET-hunter2` to
    `SECRET`) hashed EQUAL while the file on disk changed, and — needing no filter configuration at
    all, just a committed `.gitattributes` — `notes.txt text eol=lf` against a CRLF working copy did
    the same. Re-measured directly on git 2.50.1: the CRLF file hashes to `fbbee861…` with filters,
    byte-identical to the LF blob, and to `17f2fc0a…` with `--no-filters`. So the comparison is now
    raw, and the residual error runs the SAFE way: where a SMUDGE filter is configured, a file that
    already equals what the merge will write hashes differently and is reported anyway — noise
    rather than silence. Git still does the hashing, so a non-sha1 object format needs no care here.

    IT ALSO CHANGES WHAT THE KEY MEANS, ON THIS BRANCH ONLY, and that must not be filed under
    "false positive removed" without saying so: `_ignored_paths_the_ff_will_overwrite`'s own bounds
    list documents that the key names paths that were WRITTEN and not that their bytes differed, so
    a byte-identical incoming file being named there is the documented behaviour, not a defect. It
    still is on `updated: true`. On the refusal branch the key now means WRITTEN AND DIFFERENT.
    One key, two meanings, split by branch — deliberate, and pinned on both sides.

    ONE COST, ACCEPTED KNOWINGLY BY THE HUMAN WHO CHOSE THIS: if the sweep-1 message is lost to a
    probe failure, no later REFUSAL sweep names it — today's repeat was its only second chance on
    that branch. Not "never named again", which the second pass refuted with the obvious neighbour:
    the sweep that finally completes the fast-forward still names it off the unfiltered list, so
    the name returns there, and only if the fast-forward ever happens.
    """
    if not paths:
        return set()
    askable = [p for p in paths
               if "\n" not in p
               and os.path.isfile(os.path.join(root, p))
               and not os.path.islink(os.path.join(root, p))]
    if not askable:
        return set()
    listing = _run_git(("cat-file", "--batch-check=%(objectname) %(objecttype)"), root, None,
                       env_extra={"GIT_OPTIONAL_LOCKS": "0"},
                       stdin_text="".join(f"{remote}:{p}\n" for p in askable))
    lines = listing.stdout.splitlines()
    if listing.returncode != 0 or len(lines) != len(askable):
        return set()
    # A missing or non-blob entry answers `<input> missing` / `<oid> tree`, so it simply never
    # enters this dict and its path keeps its place in the report.
    incoming = {path: line.split()[0]
                for path, line in zip(askable, lines)
                if len(line.split()) == 2 and line.split()[1] == "blob"}
    if not incoming:
        return set()
    named = list(incoming)
    # `--no-filters` IS THE CORRECTION, and it went in the opposite direction to the first draft's
    # reasoning ("only git's own filtered answer compares like with like"). A `clean` filter need
    # not be invertible, so a filtered hash matching the incoming blob does NOT mean the bytes on
    # disk survive. Both counterexamples were built by the independent second pass and are in this
    # function's docstring; the second needs no filter configuration at all. Git's own hashing is
    # still what answers, so a repo on a non-sha1 object format needs no special case here.
    hashed = _run_git(("hash-object", "--no-filters", "--stdin-paths"), root, None,
                      env_extra={"GIT_OPTIONAL_LOCKS": "0"},
                      stdin_text="".join(f"{p}\n" for p in named))
    local = hashed.stdout.split()
    if hashed.returncode != 0 or len(local) != len(named):
        return set()
    return {path for path, sha in zip(named, local) if sha == incoming[path]}


def _incoming_paths_absent_here(root: Path, remote: str) -> list[str] | None:
    """Incoming paths this disk does NOT have yet — the ones a part-way ff can leave behind.

    VMCP-252 (851), round three, and it exists because the round-two fix shipped a fresh overclaim
    that the independent review built: the quiet-tracked-tree branch told a human that clearing the
    blocker was enough and the next sweep would finish the fast-forward. `_tracked_changes` reads
    `git diff-index`, which is blind to UNTRACKED paths, and a merge that fails PART-WAY writes new
    incoming files to disk without moving the index — so those files sit there untracked, and git
    then refuses every later merge over them. Measured (stand C: incoming `ro/bbb.txt` + a
    force-added `shot.png` + a NEW `brandnew.txt`, `chmod 500 ro`): sweep 1 `half-applied` with the
    healing sentence, `status` `?? brandnew.txt`, `ls-files` empty; then with the blocker CLEARED,
    sweeps 2-5 all `blocked` on `The following untracked working tree files would be overwritten by
    merge: brandnew.txt`, HEAD never reaching the remote. Removing that file is what unblocks it,
    and the report named neither the file nor the need.

    ABSENT-BEFORE is the cheap half of the discriminator and it is pure `lstat` at compare time:
    whatever of this list EXISTS after a refused merge was put there by that merge. Paths already
    on disk are not asked about, because git's up-front check already refuses over those, which is
    why they cannot be the residue of a PART-WAY failure.

    NOT "paths already on disk cannot be the residue of a part-way failure", which this docstring
    said for one round and the second pass refuted with this card's OWN stand A: the human's ignored
    `shot.png` was on disk, git's up-front check does not refuse over an ignored path, and the
    part-way merge wrote it — residue by any reading. The true reason for asking only about absent
    paths is narrower: a path already on disk can only be left in a BLOCKING state by being
    tracked-and-modified, and `_tracked_changes` covers that half; git overwrites the ignored ones
    silently and never refuses over them.

    NONE, NOT `[]`, WHEN THE READ FAILS — the whole point of the round it belongs to. An empty list
    and an unanswered probe reach the same caller, and letting them look alike is how the sentence
    this fixes came to promise healing it had not checked. The neighbouring `blocked` branch has
    carried that distinction for two cards (`looked = tracked_after is not None or
    prints_answered`).

    ITS OWN GIT CALL RATHER THAN A SHARED ONE, deliberately: threading `_incoming_displacing_paths`
    through `_ignored_paths_the_ff_will_overwrite` would save one tree-to-tree diff and couple two
    probes whose whole design is that either may fail without costing the other (the caller's three
    separate `try` blocks say the same thing). `git diff --raw HEAD..<remote>` compares two TREES
    and leaves a human's index alone, which is the property this module never spends — re-measured
    on the whole refusal branch, with a control proving the stand can see an index move at all.
    """
    changed = _incoming_displacing_paths(root, remote)
    if changed is None:
        return None
    return [p for p in changed if not os.path.lexists(os.path.join(root, p))]


def _untracked_left_behind(root: Path, absent_before: list[str] | None) -> list[str] | None:
    """Of the paths that were absent before the merge, the ones now here that git will NOT ignore.

    These are what a later `merge --ff-only` refuses over, so they are the difference between "this
    heals once the blocker is cleared" and "a human has to remove something first" — VMCP-252 (851).
    The ignored ones are dropped because git overwrites those silently instead of refusing; that is
    the whole subject of `_ignored_paths_the_ff_will_overwrite` and not a blocker.

    `_ignored_of` is bounded and bisects, and where it gives up a path is simply absent from its
    answer — which lands that path HERE, in the warn list. Same direction as everything else on this
    branch: an unanswerable read must not talk a human out of looking. Measured by the second pass:
    a path beyond a symlink makes `check-ignore` exit 128, `_ignored_of` answers `[]`, and the path
    is reported as a blocker.

    `None` in, `None` out, for the reason the other probe returns it: the caller's reassurance may
    only be printed when somebody actually looked.

    "Put there by that merge" is the ordinary reading and not a proof — a human or an agent saving
    a file into the main checkout during those milliseconds is a race this cannot distinguish, the
    same caveat `_tracked_changes` and `_fingerprints` both carry about themselves."""
    if absent_before is None:
        return None
    appeared = sorted(p for p in absent_before if os.path.lexists(os.path.join(root, p)))
    if not appeared:
        return []
    ignored = set(_ignored_of(root, appeared))
    return [p for p in appeared if p not in ignored]


def _same_object_key(root: Path, rel: str) -> tuple | str:
    """A de-dup key for candidate paths that survives a CASE-INSENSITIVE checkout.

    VMCP-257 (859). An incoming commit can carry TWO SPELLINGS of one name — `out` and `OUT` as
    two blobs in one tree, which `git add` on a case-insensitive checkout would collapse but
    `update-index --cacheinfo` plants happily. On this disk both spellings answer `os.path.isdir`,
    `os.walk` walks each as its own directory, `os.path.relpath` is lexical and keeps whichever
    spelling it was handed, and the caller used to de-dupe on the EXACT string — so every dying
    object was named once PER SPELLING. Measured on macOS/APFS with `core.ignorecase = true`:
    three objects on disk (a file, a symlink, and an unrelated `shot.png`) came back as FIVE names.

    THAT DIRECTION IS THE WHOLE POINT. `overwritten_ignored` is read as a lower bound on the loss
    — the filter and `_MAX_DIR_EXPANSION` both make it UNDERSTATE — and this was the one known road
    on which it OVERSTATED. Two errors in opposite directions in one key are worse than one, and it
    also undercut `_expand_if_directory`'s own reason for filtering out real subdirectories
    ("a REAL subdirectory is not a path that dies, its files are, and they are named individually
    by this same walk"), since the same bytes were double-counted
    anyway by a road that filter never touched. That parenthesis used to read "Naming both would
    double-count the same bytes", which is nowhere in that function — it is the REAL-subdirectory
    TEST's docstring, one file over, landed there by VMCP-245 (836). `git log --all -S` over `src/`
    returns exactly one commit for it, `97c2c5c`, the 859 landing that wrote the misattribution:
    before that the phrase had never been in `src/` at all (VMCP-275 (898)). The SUBSTANCE was
    attributed rightly; only the WORDS were somebody else's, and no gate here can see that — the
    phrase IS in the tree, just not where the sentence said it was.

    THE KEY IS COMPOSITE, and neither half would do on its own — both alternatives are measured
    rather than argued. `os.path.normcase` is what the card proposed and it is a NO-OP on POSIX
    (measured: it hands `OUT/a.txt` straight back), so it collapses nothing here. A bare
    `(st_dev, st_ino)` does collapse the duplicate, and costs a name: two ignored HARDLINKS dying
    in one merge came back as ONE name instead of two. Adding `rel.casefold()` keeps them apart
    while still collapsing the case-duplicate, and buys a property neither half has — this key can
    NEVER merge two DISTINCT objects on disk, because the inode forbids it. So on a case-SENSITIVE
    filesystem, where `out/a.txt` and `OUT/a.txt` really are different files, it collapses no
    case-DUPLICATE — there are none to collapse — which is why the fix needs no platform test.
    NOT "collapses nothing at all", the flat version this shipped with: the paragraph below already
    names the counterexample (two hardlinks whose names differ only in case), and VMCP-275 (898)
    BUILT it on a case-sensitive APFS image — `ln a.txt A.txt`, both names keying to one
    `(dev, ino, 'a.txt')`. So the two sentences contradicted each other four lines apart, and the
    one that survives is the narrow one; the platform-test conclusion stands on its own reason,
    which is that the only collapse left there is the residue the next paragraph declares.

    WHICH SPELLING SURVIVES is the first one the walk reached, exactly as it already was for exact
    repeats, and it is an INCOMING spelling — the bound the CALLER's bounds list already states
    ("the string handed to the human is not the name their file had"). What is NOT closed: two
    hardlinks whose names differ only in case would still collapse, which needs a case-sensitive
    filesystem to build and errs in the direction the key errs in everywhere else.

    Falls back to the exact string when `os.lstat` fails. Candidates reach here either from
    `_doomed_ancestor` (which answers about a path it just stat-ed) or from a walk of this disk, so
    a failure means a race, and behaving like the old code on it is the smallest possible answer.
    `lstat`, not `stat`: a symlink must be keyed as ITSELF, and a DANGLING one — which `stat`
    cannot see at all — is a real casualty with a real inode. That choice is UNPINNED and said so
    rather than assumed: swapping it kills no test (control 0 failed; that round 0 failed), and it
    is hard to kill by construction — on a dangling link `stat` raises and the fallback returns the
    exact string, which is the pre-859 answer for that one path, while everywhere `stat` WOULD
    resolve onto a target's inode the `casefold` conjunct still keeps the names apart.
    """
    try:
        st = os.lstat(os.path.join(root, rel))
    except OSError:
        return rel
    return (st.st_dev, st.st_ino, rel.casefold())


def _tracked_changes(root: Path) -> set[str] | None:
    """The TRACKED paths where the working tree differs from HEAD, or None if git could not say.

    HALF of VMCP-244 (835)'s partial-apply detector: taken once BEFORE the merge and once after a
    refusal, the set DIFFERENCE is the best available reading of what the failed fast-forward
    managed to write — "best available" and not "is", because this runs in somebody ELSE's working
    directory and a human or an agent saving a file during those milliseconds is a race it cannot
    distinguish, exactly as `_fingerprints` says of its own half. The difference is
    the point — the most ordinary refusal there is happens BECAUSE the human has a tracked file
    modified, so an after-only reading would report their own in-flight edit as something this tool
    wrote (`test_the_humans_own_pre_existing_edit_is_never_called_half_applied`).

    PLUMBING (`diff-index`) AND NOT `git diff`, because `git diff HEAD` REFRESHES A HUMAN'S INDEX
    AND WRITES IT — and this ran in their checkout on a run where nothing of ours may write at all.
    That was this function's first shipped shape and the independent second pass caught it, so the
    reasoning is written out rather than left as a flag nobody dares remove. Measured, git 2.50.1,
    on a stat-dirty-but-content-CLEAN entry whose mtime is in the PAST (git can then trust its own
    re-read and record the fresh stat): `git diff --name-only HEAD` moves the index mtime, and
    `GIT_OPTIONAL_LOCKS=0` DOES NOT STOP IT — nor does `git --no-optional-locks`. The variable is
    INERT here, which is the opposite of what this docstring first claimed; it is not belt, it is
    decoration, and it is kept only for uniformity with its neighbours. End to end, the same input
    through `sync_main_checkout` moved the index under the `git diff` form and left it alone under
    HEAD's pre-835 code, i.e. that shape was a REGRESSION against a property #806 had measured.
    THE RACE CONDITION IS WHY AN EARLIER PROBE OF MINE SAW NOTHING: with the file merely `touch`ed
    to NOW, git treats the entry as racily clean and deliberately does not record the stat, so the
    index stays put and `git diff` looks innocent. The discriminating stand needs an mtime in the
    past — which is the ordinary state of a checkout somebody stopped editing an hour ago.
    `GIT_OPTIONAL_LOCKS=0 git status --porcelain` also preserves the index (plain `status` does
    not — there the variable IS load-bearing) and was the other candidate; it lost on OUTPUT, not
    on writes, because it needs rename records and untracked filtering parsed out of porcelain,
    where `--name-only -z` is already the list this function returns.

    WHAT `diff-index` COSTS, and why it is harmless HERE specifically: without a refresh it reports
    a stat-dirty-but-content-clean entry as changed — measured, both `a.txt` and `b.txt` above.
    Those are FALSE POSITIVES, and the SET DIFFERENCE is what disarms them: such an entry is in the
    before set as well, so it cancels. The direction is what matters and it holds both ways — an
    entry the merge refreshed out of the after set only SHRINKS the answer, and an entry that
    became stat-dirty DURING the merge is one git wrote, which is a true positive. So the
    imprecision can never invent a half-applied path; at worst it hides one.

    WHAT IT CANNOT SEE: a path that is UNTRACKED locally — which is every ignored casualty, and the
    whole reason the ignored half is fingerprinted separately rather than derived from this. A
    MODE-only incoming change is NOT in that list: an earlier draft named it and the second pass
    measured the opposite, `half_applied == ['m.txt']` for a 644->755 change. None means "no
    answer", never "no changes": the caller must not read a failed read as a clean tree.
    """
    proc = _run_git(("diff-index", "--name-only", "-z", "--no-renames", "HEAD"), root, None,
                    env_extra={"GIT_OPTIONAL_LOCKS": "0"})
    if proc.returncode != 0:
        return None
    return {p for p in proc.stdout.split("\0") if p}


def _fingerprints(root: Path, rels: list[str]) -> dict[str, tuple | None]:
    """`(mode, size, mtime_ns, inode)` per path, `None` for one that is not there.

    THE OTHER HALF of the partial-apply detector, and the half without which the whole thing is
    blind to its most important case: a failing merge whose ONLY casualty is an IGNORED file
    changes nothing git will report — measured, `git diff --name-only HEAD` and `git status
    --porcelain` are both EMPTY before and after — so `_tracked_changes` returns the same set
    twice while the human's bytes are gone.

    The INODE is in there on purpose and it is what makes the fingerprint strong: git UNLINKS and
    recreates, so overwriting an ignored file moves its inode as well as its size and `mtime_ns`
    (measured; the exact numbers are a per-run artifact and are deliberately not written down here,
    the PROPERTY is what re-measures). That matters because mtime resolution belongs to the
    FILESYSTEM, not to git — on a one-second-granularity filesystem a same-size overwrite inside
    one second moves neither size nor mtime. The independent second pass built exactly that case on
    a FAT32 image and the inode moved in both halves of it, so this is measured rather than
    reasoned. The direction it is NOT measured in, and cannot be from here: a filesystem that
    REUSES an inode number immediately after an unlink.

    Read one-way, like everything else on this path. A CHANGE here is evidence; equality is not
    proof of survival (a rewrite that reproduced mode, size, mtime_ns AND inode), and a change is
    not proof that the MERGE made it — a human or an agent writing that same ignored path during
    the merge is a race this cannot distinguish and does not claim to. `os.lstat`, so a symlink is
    fingerprinted as itself: `_doomed_ancestor` names symlinks, and following one would fingerprint
    a file outside the checkout.
    """
    out: dict[str, tuple | None] = {}
    for rel in rels:
        try:
            st = os.lstat(os.path.join(root, rel))
        except OSError:
            out[rel] = None
        else:
            out[rel] = (st.st_mode, st.st_size, st.st_mtime_ns, st.st_ino)
    return out


def _add_capped(state: dict, key: str, paths: list[str]) -> None:
    """Attach `paths` under `key`, capped, with a `<key>_truncated` sibling — or attach NOTHING.

    ABSENCE IS THE LOAD-BEARING PART, and it is why this is a function rather than three copies of
    two lines: every report on this path is read one-way, so a key present with an empty list is
    the never-read field VMCP-68 had to split `kept` in two to rescue.

    `<key>_truncated` is the length of the list BEFORE the cap and AFTER every filter and give-up
    that produced it, so it inherits exactly the blindness of the key it sits beside — it is not a
    measure of the loss. Read the way SKILL.md states it to agents.

    "Capped" is TWO budgets since VMCP-260 (862), entries and bytes, and this key raises the same
    `_truncated` sibling whichever one cut it — see `_cap_reported`, which both this and
    `removed_ignored` go through so the two cannot drift apart again.
    """
    if not paths:
        return
    state[key] = _cap_reported(paths)
    if len(state[key]) < len(paths):
        state[f"{key}_truncated"] = len(paths)


def _note_probe_was_denied(state: dict, unreadable: list[str]) -> None:
    """Attach `overwritten_ignored_incomplete` — or attach NOTHING. VMCP-281 (940), variant B.

    THE NAME IS DELIBERATELY BROAD, and that was the human's call on the card rather than a
    preference here. The channel measured on 940 is ENAMETOOLONG, but any `OSError` reaches the
    same callback — permissions, a vanished directory, a filesystem saying no for its own reasons
    — and every one of them means the same thing to a reader: this list is short by an unknown
    amount. A key named after one errno would have had to be widened later, and widening is a
    second visible change to a payload the hgdev-acp hub reads.

    THE VALUE COUNTS PLACES, NOT FILES. One denied directory hides its whole subtree: measured on
    the ENAMETOOLONG stand, depth 28 lost one file and depth 30 lost three, and BOTH reported
    exactly one denial. It is a count of what the probe could not look at, and it supports no
    arithmetic about how much died.

    EMITTED EVEN WITH NO `overwritten_ignored`, which is the case that looks safest and is not:
    a probe denied everywhere finds nothing, and an absent key alone reads as "nothing at risk".
    Absence of the names has always been documented as proving nothing; this makes the difference
    between "looked and found none" and "could not look" VISIBLE rather than doctrinal.

    Absent when the probe reached everywhere, for `_add_capped`'s reason: a key present on every
    sync is the never-read field VMCP-68 had to split `kept` in two to rescue.
    """
    if unreadable:
        state["overwritten_ignored_incomplete"] = len(unreadable)


def _partial_apply_reason(root: Path, remote: str, half: list[str], over: list[str],
                          tracked_after: set[str] | None, blockers: list[str] | None,
                          message: str) -> str:
    """The `half-applied` prose, SPLIT by what the run actually established — VMCP-252 (851).

    ONE sentence used to be emitted on both forms of this state, written for the TRACKED one, and
    all FOUR of its assertions are false when the only casualty is an IGNORED file. Measured at
    `6231c850` on real git 2.50.1, on the stand `_ignored_only_stand` builds: the tracked tree is
    entirely at HEAD (`ro/bbb.txt` still v1, `diff-index` empty), `git status --porcelain` is `''`
    about the casualty — an ignored file is invisible to it, which is the property that makes this
    shape undetectable by a tracked-diff probe at all — `chmod 700` on the blocking directory plus
    ONE sweep gives `{'updated': True, 'commits': 1}`, and the `git checkout -- <path>` the report
    prescribed answers `error: pathspec 'shot.png' did not match any file(s) known to git`, rc=1,
    because in the half-applied state that path is not tracked locally. Meanwhile the one thing
    that HAD happened — the human's bytes replaced by upstream's — went unsaid.

    IT BRANCHES ON `tracked_after`, NOT ON `not half`, AND THAT IS ROUND TWO OF THIS CARD — the
    first version branched on `not half` and shipped a FRESH overclaim of the very class it was
    fixing, caught by the independent second pass and reproduced here. An empty `half` does NOT
    mean the tracked tree is untouched, for TWO different reasons: the probe may have FAILED (then
    `tracked_after is None`), and — the one that is easy to miss — the probe may have ANSWERED AND
    BEEN BLIND, because `half` is a SET DIFFERENCE. Built: the human has locally DELETED a tracked
    file that the incoming commit also modifies, so that path is in the before set AND the after
    set and cancels out. Measured on that input, with the old wording: the report said "Nothing
    TRACKED moved … `git status` is silent … this DOES heal" while `git status` said ` M aaa.txt`,
    the file on disk held upstream's v2 against HEAD's v1, and sweeps 2 and 3 with the blocker
    CLEARED both answered `blocked` with HEAD never reaching the remote. `_tracked_changes` states
    that bound about itself ("at worst it hides one"); this is what reading it as proof costs.

    So the only branch that may claim a quiet tree is the one where `tracked_after` is EMPTY —
    nothing tracked differs from HEAD at all, which is a direct reading rather than an inference,
    and it is already computed. When it is non-empty with nothing NEW in it, the honest answer is
    that this run cannot separate the human's own edits from what the merge wrote over them, which
    errs towards "cannot say" and never towards safety: it also fires when the human simply had an
    unrelated file modified, where nothing tracked was written at all.

    AND THE QUIET BRANCH'S OWN HEALING CLAIM WAS ROUND THREE, filed against this function by the
    independent review of the round-two fix and reproduced here on stand C before anything moved.
    It read "nothing is left here to block the merge again: clearing whatever stopped it is enough,
    and the next sweep completes the fast-forward (measured)" — and "(measured)" was doing the
    damage, since it was measured on a stand whose incoming commit touches EXACTLY two paths. Add a
    third, a NEW non-ignored file, and the part-way merge writes it to disk without touching the
    index: `diff-index` stays empty (so this is still the branch), `git status` says
    `?? brandnew.txt`, and git then refuses EVERY later merge over it — sweeps 2-5 `blocked`, HEAD
    never reaching the remote, with the blocker long since cleared. So on that input the round-two
    fix REPLACED A TRUE VERDICT WITH A FALSE ONE: the pre-851 sentence happened to be right about
    healing there. The claim now rests on a probe (`_incoming_paths_absent_here` plus
    `_untracked_left_behind`) instead of on the shape of one stand, it NAMES the files a human has
    to remove, and where it still says "expected to complete" it says over what it looked.

    ROUND FOUR IS THAT SAME MISTAKE ONE LAYER IN, and it is the reason this branch has THREE arms
    rather than two. The round-three probe returned `[]` on failure, so an unanswerable read printed
    the full reassurance — the second pass built all three silent routes (both `except` wrappers and
    an unreadable `git diff`) and got "no incoming path was left behind untracked either … that is
    what was CHECKED" over a checkout that then answered `blocked` forever. A probe that cannot
    distinguish "looked, found none" from "could not look" is exactly what the round-two fix was
    bounced for at the TRACKED half, where `looked` has guarded that distinction since #835. So the
    probes answer `None`, and `None` prints a refusal to claim. Two smaller narrowings from the same
    pass: "no incoming path left behind UNTRACKED" was literally false whenever the merge left an
    ignored one (true but irrelevant — git overwrites those), and "every later sweep will report
    `blocked`" ignored an upstream commit that later drops the path, which was built and heals with
    no human at all.

    Not split further, on purpose. `over` non-empty rides along with the tracked form as its own
    sentence (the `checkout --` advice cannot reach an untracked path, so it is named as
    inapplicable rather than left to be misread), and the tracked form keeps every word of what
    #835 measured about it — narrowing a sentence must not cost the form it was true of. The
    left-behind blockers are named ONLY on the quiet branch: it is the only one that ever claimed
    healing, while the tracked form already says it does not heal and the other two already refuse
    to say. They ride in the prose rather than in a payload key of their own, because what a reader
    does with them is a command, not a list to grade — `half_applied` and `overwritten_ignored` are
    keys because they are capped, one-way-read inventories of LOSS, and this is neither.
    """
    opening = (f"`git merge --ff-only {remote}` FAILED PART-WAY: HEAD did not move, but part of "
               f"the update was already written")
    # WHY THIS IS HEDGED AND THE FIRST VERSION WAS NOT: it said "the human's own are GONE and
    # NOTHING can recover them — no git object ever held them", and the second pass built both
    # counterexamples, each reproduced here and each INDISTINGUISHABLE from the plain stand by
    # anything this branch can see. `git add -f <path>` followed by `git rm --cached <path>` leaves
    # `git status --porcelain` empty and the human's blob in the object store, so after that
    # sentence `git cat-file -p` handed the bytes straight back and `git fsck` called it a dangling
    # blob. The SECOND caveat has since been NARROWED by this card's own filter rather than
    # deleted, which is the honest move and not a tidy-up: "an ignored file whose bytes already
    # equalled upstream's lost nothing" used to be an ordinary state here, and
    # `_paths_already_holding_incoming_bytes` now drops exactly the paths it can PROVE are in it.
    # What is left is the shapes that probe cannot ask about — a symlink, a directory, a name it
    # could not read — plus the one it asks and gets wrong in the safe direction: with a SMUDGE
    # filter configured, raw bytes differ from the blob while the file already equals what the
    # merge writes. Rare, still reachable, so still said.
    lost = ("The ignored path(s) in `overwritten_ignored` now hold upstream's bytes, and what was "
            "there before is not recoverable from anything HERE: this tool keeps no copy, and git "
            "keeps none of a file it was never asked to track. Two states this cannot tell apart, "
            "both measured: if that path was ever staged, the old blob may still be in the object "
            "store (`git fsck --lost-found`), and a path whose bytes this run could not compare "
            "against the incoming blob may have equalled it already, in which case nothing was "
            "lost there at all.")
    if half:
        parts = [f"{opening}, so this checkout now mixes two commits and `git status` shows the "
                 f"incoming content as the human's own uncommitted work (`half_applied` names "
                 f"those paths). It does NOT heal itself, and clearing whatever stopped the merge "
                 f"is not enough (measured on THIS form: those same half-written paths then block "
                 f"the merge as local changes, and every later sweep reports `blocked`). A HUMAN "
                 f"has to commit them or drop them (`git -C {root} checkout -- <paths>`, which "
                 f"DISCARDS them); nothing here will."]
        if over:
            parts.append(f"{lost} `checkout --` does not reach them — they are untracked here.")
    elif tracked_after is None:
        parts = [f"{opening}. Whether anything TRACKED was written too could NOT be checked on "
                 f"this run, so nothing is claimed here about the rest of the checkout.", lost]
    elif not tracked_after:
        parts = [f"{opening}. Nothing tracked differs from HEAD at all afterwards (`git "
                 f"diff-index` came back empty), so no TRACKED path was half-written and `git "
                 f"status` has no modification of this to show, commit or drop.", lost]
        if blockers is None:
            parts.append("Whether the failed merge ALSO left an incoming path here untracked — "
                         "which would refuse every later merge until a human removes it — could "
                         "NOT be checked on this run, so nothing is claimed about whether this "
                         "heals once the blocker is cleared. Read `git status` in that checkout "
                         "for `??` entries.")
        elif blockers:
            named = ", ".join(blockers[:_MAX_REPORTED_IGNORED])
            more = (f" (and {len(blockers) - _MAX_REPORTED_IGNORED} more)"
                    if len(blockers) > _MAX_REPORTED_IGNORED else "")
            parts.append(f"This will NOT heal on its own: the failed merge also left "
                         f"{len(blockers)} incoming path(s) on this disk WITHOUT tracking them "
                         f"— {named}{more} — and git refuses to merge over an untracked file, so "
                         f"later sweeps will report `blocked` even after whatever stopped the "
                         f"merge is cleared, for as long as that path is here AND the incoming "
                         f"range still carries it. A HUMAN moving or removing them is the way out "
                         f"that is in anybody's hands here; an upstream commit that drops the "
                         f"path would also do it.")
        else:
            parts.append("Nothing TRACKED is left here to block the merge again, and no incoming "
                         "path was left behind in a state git would refuse to merge over — "
                         "ignored ones are excluded, since git overwrites those silently instead "
                         "of refusing. So once whatever stopped the merge is cleared the next "
                         "sweep is expected to complete the fast-forward: that is what was "
                         "CHECKED on this run, over tracked paths and over the incoming path "
                         "list, and not a promise about the checkout.")
    else:
        parts = [f"{opening}. What ELSE it wrote is UNCLEAR rather than nothing: "
                 f"{len(tracked_after)} tracked path(s) differ from HEAD and none of them appeared "
                 f"during the merge, so this run cannot separate the human's own edits from what "
                 f"the failed merge wrote over them — a path already modified before the merge "
                 f"cancels out of the comparison. Read `git status` in that checkout before "
                 f"concluding anything, and do not assume this heals on its own.", lost]
    return " ".join(parts) + f" git's message: {message}"


def sync_main_checkout(root: Path) -> dict | None:
    """Fast-forward the MAIN checkout onto `origin/<default branch>`, or REFUSE and say why.

    THE ONE RULE THIS IS BUILT AROUND: the main checkout is somebody ELSE's working directory.
    A human works there, the session's shared browser/MCP roots resolve there, and it can hold
    uncommitted work at any moment (it held an untracked `BOARD-ANALYSIS-2026-08-03.md` while
    this card was being written). So the update is FAST-FORWARD ONLY and REFUSES rather than
    resolves. Not on this ladder, and never to be added: `reset --hard`, `checkout -f`, `clean`,
    `stash`, `pull` (which may merge or rebase), a `merge` without `--ff-only`, or switching the
    branch. Every one of those either discards a human's work or invents a commit in their tree;
    a stale checkout is a nuisance, and losing an afternoon of uncommitted edits is not.

    WHAT PROTECTS THE UNCOMMITTED WORK IS GIT ITSELF, not a guard written here, and that is the
    point rather than an omission: `merge --ff-only` refuses outright when the incoming commits
    would overwrite a modified TRACKED file, or an untracked one that is NOT IGNORED, and leaves
    everything untouched when it does. So the tool never has to decide which local edits are
    precious. A guard of our own that merely required a clean tree would be WORSE than useless
    here — the very checkout this card is about has an untracked file in it, so "refuse unless
    clean" would mean the fix never fires in the one case it was filed for. Edits to files the
    incoming commits do not touch survive the fast-forward, which is the desirable half of the
    same property.

    THAT SENTENCE USED TO SAY "OR AN UNTRACKED ONE", FULL STOP, AND IT WAS WIDER THAN ITS PROOF —
    VMCP-240 (806), found by the independent review of the card that shipped it. An IGNORED file
    IS untracked, and git overwrites it silently: rc=0, empty stderr, `updated: true`. Two routes,
    both measured, both live in this repo, whose own rulebook tells agents to write
    `shot-<id>.png` into a checkout that ignores `*.png`. Only the FIRST of them is invisible to
    `git status --porcelain` (empty before and after) — the second needs a local ignore rule, so
    status shows the `.gitignore` itself as `??` or ` M` while saying nothing about the file that
    dies. See `_ignored_paths_the_ff_will_overwrite`, which is what now NAMES the loss in
    `overwritten_ignored`. Naming is not preventing. The same overclaim rode out in the commit body
    of 27666c2a, which cannot be rewritten (a force-push to `main` over a message is not worth
    it), so the correction lives here, in CLAUDE.md and in SKILL.md instead.

    AND THE OTHER HALF OF THAT SENTENCE — "the REFUSAL branches still discard nothing (that half of
    the claim was always sound)" — WAS FALSE TOO, which is VMCP-244 (835), filed by the round-2
    review of the card that wrote it. `merge --ff-only` is NOT ATOMIC. It attempts every entry and
    writes everything it can, so ONE path it cannot write leaves the rest written and HEAD where it
    was: measured on real git 2.50.1, `chmod 500` on a directory and `chflags uchg` (Finder's
    "Locked" checkbox) on a tracked file both give `error: unable to unlink old '<p>'` with
    `aaa.txt` gone v1->v2 and an ignored `shot.png` replaced by upstream's bytes. That was the ONE
    branch where the probe's answer was deliberately thrown away — so the branch promising safety
    was the branch that could destroy an ignored file leaving no trace whatever.
    NOT bounded by index order, which is the natural guess and was measured false: a tracked
    `zzz.txt` sorting AFTER the failing path is written too, so what survives is exactly what git
    could not write. Hence `MAIN_SYNC_PARTIAL`, and hence the detector asks the WORKING TREE what
    moved (`_tracked_changes`, `_fingerprints`) rather than deriving a prefix of the index.
    `blocked` keeps its name for the refusal where the two probes FOUND nothing, and the three
    up-front refusals really are that: measured, "Your local changes …", "The following untracked
    working tree files …" and "Updating the following directories would lose untracked files in
    them" each abort before writing, witnessed by a second incoming file sorting FIRST keeping its
    old content. **Read that as three measured MESSAGES and not as the code's meaning**, which is
    the correction the second pass forced here: `blocked` is the FALL-THROUGH whenever both probes
    come up empty, so it is also what an already-half-applied checkout reports on every LATER sweep
    WHEN THE HALF-WRITE WAS TRACKED (measured: sweep 1 `half-applied`, sweeps 2 and 3 `blocked`,
    tree still mixed), and what a half-apply whose only casualty was filtered as regenerable
    detritus reports on the FIRST one. That first clause is narrower than it read for one round and
    VMCP-252 (851) is the correction: on the form whose only casualty is an IGNORED path, later
    sweeps report `half-applied` AGAIN as long as nothing else in the checkout changes, because each
    failed attempt unlinks and recreates that file, so its fingerprint moves again over content that
    has been upstream's since sweep 1 (measured, three sweeps, three inodes). "As long as" is not
    filler — let the human DELETE that now-foreign file and the next sweep is `blocked`, since the
    path leaves the probe's list and both probes go silent again (measured). So what `blocked` no
    longer says is "NOTHING was
    discarded": the branch reports what it FOUND, one-way, like every other report on this path —
    and says outright when it could not look.

    AND THE THIRD CORRECTION TO THAT SAME SENTENCE IS ONE DIFF SHAPE — an incoming entry landing ON
    a live GITLINK's path as a non-directory — VMCP-247 (838), filed by the implementer of 837 and
    measured at `469db93` on real git 2.50.1. Such a TYPECHANGE (`:160000 <non-gitlink> … T`;
    measured with `100644` and with `120000`, which destroy identically) deletes that submodule's
    whole WORKING DIRECTORY at rc=0 with no warning, and the two shapes the sentence above names as
    protected die with it: an untracked-and-NOT-ignored file, and a file that is MODIFIED and
    TRACKED — tracked by the SUBMODULE, which is the only index that has ever heard of it (the
    superproject holds ZERO entries under a gitlink, so from up here every one of those files is
    untracked). On that branch `git status --porcelain` is empty afterwards.
    SAY "ON THE GITLINK'S PATH" AND NOT "INSIDE A LIVE GITLINK", which is what this paragraph said
    for one round until the second pass built the counterexample: when the incoming path is one
    INSIDE the gitlink (`sub/precious.txt`) the contrast HOLDS in there exactly as it does
    anywhere, measured twice — rc=1 and "The following untracked working tree files would be
    overwritten by merge" naming that very path, nothing destroyed. A live gitlink is not a blanket
    blind spot; one diff shape is. Two consequences, and the second is why this is the deepest of
    the three corrections: no probe of ours reports that content — `overwritten_ignored` is about
    IGNORED paths by name and by remit — and, unlike 806 and 835, the CONTRAST is absent rather
    than narrowed, so "the tool never has to decide which local edits are precious" has one shape
    where git decides nothing either. BOTH QUESTIONS THAT PARKED ARE NOW ANSWERED, and both
    answers are NO (tracker #838), so read this shape as a documented gap and not as an open
    design. Asking the sub-list under a gitlink with `--no-index` is declined: it widens the
    probe's surface, and it could not reach `sub/precious.txt` in any case — measured rc=1, no
    rule matches that file, so an ignore-probe is the wrong instrument by REMIT rather than by
    reach. Refusing the ff is declined too: the chain's standing "report and never refuse"
    (#801/#806) stands, and the condition would have had to be "typechange onto a gitlink whose
    directory is non-empty" — every populated submodule, including one with nothing to lose, and
    re-refusing on every sweep until a human intervened. What that leaves is worth stating
    plainly rather than softening: on THIS shape nothing protects the content, ours or git's. The
    806 contrast (untracked-and-NOT-ignored is refused by git itself) does not hold here, and
    `overwritten_ignored` is about IGNORED paths by name and by remit, so it would not name these
    files even with the ask widened. Naming the PLACE instead of the files — a key off the `--raw`
    modes this probe already parses, costing no extra git call — is the one option that would
    cover the whole class including the non-ignored half; it is deliberately NOT implemented here,
    because this pass was scoped to prose, and it stays with the card rather than being decided in
    a docstring. The measurements, including the shapes that are NOT losses, live in this
    card's section of `tests/unit/test_workspace_cmd.py`.

    A DIFFERENT CLAUSE OF THAT SAME PARAGRAPH TOOK ITS OWN CORRECTION, and it is NOT a fourth link
    in the 806/835/838 chain above — those three narrow what the CONTRAST promises, this one is
    about what `blocked` is entitled to SAY. "And says outright when it could not look" is only as
    good as the predicate under it, and VMCP-258 (860) shipped that predicate false for one round.
    `looked` decides the clause, and it has to mean A COMPARISON HAPPENED — never "a call
    returned". Round 1 wrote it as a disjunction over the two CALLS, so either half answering
    vouched for the other, and `_fingerprints(root, [])` returns `{}`, which IS "not None", so the
    ignored half answered TRUE having compared ZERO paths. On the ORDINARY checkout — the one with
    no ignored casualty — that made the disjunction unconditionally true, and the reassuring
    sentence printed over a tree where a tracked file really had gone v1->v2. That is the borrowed
    reassurance #806 was filed for, reached by the fix for a LOST report: a loud nothing traded for
    a quiet falsehood, which is worse than the defect the card was opened on. It is now a
    CONJUNCTION over the two HALVES of "half-written", each counted by what it COMPARED, with an
    empty `doomed` counting only when the pre-merge probe returned it WITHOUT RAISING. Read that
    last clause exactly: NOT "computed it". That probe gives up in several non-raising ways too
    (its own docstring lists them), so the conjunct closes the RAISE the guards made reachable and
    not the whole class — a half-apply whose only casualty the regenerable-name filter dropped
    still prints the reassuring sentence, measured, with no injection. That residue is NAMED rather
    than fixed because it predates the guards: the same stand on the pre-guard parent `1fb0082`
    gives the same code, sentence and dead file. The general lesson outlives the instance: a guard
    added to a diagnostic changes what the sentences ABOUT that diagnostic are entitled to claim,
    and the round that adds one owes the predicate a pin.

    RETURNS None WHEN THERE IS NOTHING TO SAY — already current, or opted out. The key it feeds
    is therefore ABSENT on the boring path and PRESENT whenever a reader has something to read.
    That direction is deliberate and is the lesson `removed_ignored` and VMCP-68's `kept`/
    `expected` split both paid for: a field that is present on every tick stops being read.
    Present means either "the shared checkout moved, here is how far" or "it is stale and here
    is what is holding it" — both worth a line in the orchestrator's report, neither an alarm.

    `overwritten_ignored` obeys that same one-way reading and is deliberately NOT spelled
    `removed_ignored`, though it is the same kind of post-mortem: there a file was DELETED with
    its worktree, here a path was written over and still exists holding somebody else's bytes.
    One name per verb keeps `released`'s scan and this one from being confused for each other,
    and keeps a grep for either landing on one implementation. It rides on `updated: true` AND on
    `half-applied`, with one asymmetry that is deliberate rather than sloppy: on `updated: true`
    the list is the probe's, UNFILTERED, because the merge completed and therefore wrote every
    incoming path; on `half-applied` only SOME were written, so there it is filtered down to the
    paths whose fingerprint actually moved. Same verb, same key, and the branch that cannot know
    is the one that checks.

    THAT ONE-WAY READING USED TO COST A RING ON EVERY TICK, AND A HUMAN CHOSE WHAT TO SPEND
    (VMCP-252, 851). `--gc` runs every tick, so while the blocker stood the key named the same path
    on EVERY sweep for a loss that happened on the FIRST — measured, three consecutive sweeps naming
    `shot.png` (inodes 212809910 -> 212810669 -> 212811229, because each failed attempt unlinks and
    recreates it), plus a FOURTH on the `updated: true` sweep that finally heals it. That is the
    never-read failure VMCP-68 had to split `kept`/`expected` to cure, while quietly suppressing a
    repeat is the one-way reading the whole #710 -> #806 -> #835 chain defends — so it was parked
    for a human, who took the filter. `_paths_already_holding_incoming_bytes` drops a path this run
    can POSITIVELY show already held the incoming bytes, read BEFORE the merge, on the REFUSAL
    branch only. Three things about it that are decisions rather than details: it makes the report
    truer and not merely quieter, because a path where nothing died was a false positive against
    this key's own documented meaning; every unanswerable read still REPORTS (a doomed ANCESTOR is
    no blob in the incoming tree at all, so nothing is compared and it keeps its name); and the
    `updated: true` sweep still names the path, so one loss costs TWO messages, not one. What it
    buys is a message per LOSS rather than per TICK, and what it costs is the second chance: if the
    first sweep's message is lost to a probe failure, nothing names it later.

    THE HEALING CLAIM IS PER RUN, NOT PER FORM, and that is round three of the same card — the
    round-two fix asserted that the ignored-only form heals once the blocker is cleared, its
    independent review falsified it by adding ONE path to the stand, and the correction rests on
    `_incoming_paths_absent_here` + `_untracked_left_behind` rather than on a stand's shape. A merge
    that fails part-way writes NEW incoming files to disk without moving the index, `diff-index` is
    blind to them, and git refuses over them for good; the report now names them and says a human
    has to remove them. Where it still says the ff is expected to complete, it says over what it
    looked.
    """
    if os.environ.get(_MAIN_SYNC_OPT_OUT):
        return None
    base = default_base(root)
    remote = f"origin/{base}"

    proc = _run_git(("fetch", "origin"), root, _GIT_NET_TIMEOUT)
    if proc.returncode != 0:
        return {"updated": False, "code": MAIN_SYNC_FETCH_FAILED, "branch": base,
                "path": str(root),
                "reason": f"`git fetch origin` failed in the main checkout: "
                          f"{(proc.stderr or proc.stdout).strip()}"}
    if not _git_ok("rev-parse", "--verify", "--quiet", f"{remote}^{{commit}}", cwd=root):
        return {"updated": False, "code": MAIN_SYNC_NO_REMOTE, "branch": base,
                "path": str(root),
                "reason": f"there is no {remote} to fast-forward onto — the remote's default "
                          f"branch is not what `default_base` resolved to"}

    head = _run_git(("symbolic-ref", "--quiet", "--short", "HEAD"), root, None)
    if head.returncode != 0:
        return {"updated": False, "code": MAIN_SYNC_DETACHED, "branch": None, "path": str(root),
                "reason": "the main checkout is in DETACHED HEAD, so there is no branch to "
                          f"fast-forward; a human puts it back with `git -C {root} switch {base}`"}
    branch = head.stdout.strip()
    if branch != base:
        return {"updated": False, "code": MAIN_SYNC_OFF_BRANCH, "branch": branch,
                "path": str(root),
                "reason": f"the main checkout is on `{branch}`, not `{base}` — left alone on "
                          f"purpose: switching branches under someone who is working is not "
                          f"housekeeping"}

    before = _git("rev-parse", "HEAD", cwd=root)
    target = _git("rev-parse", remote, cwd=root)
    if before == target:
        return None                                    # already current: nothing to say
    # `--is-ancestor` is what makes this a FAST-FORWARD and not a merge: 1 means the checkout
    # holds commits that `origin/<base>` does not, i.e. an unpushed human commit or a remote
    # rolled backwards. Either way this tool is the wrong actor — `merge --ff-only` would refuse
    # anyway, but refusing HERE lets the reason name the real situation instead of quoting git.
    if not _git_ok("merge-base", "--is-ancestor", before, target, cwd=root):
        ahead = _run_git(("rev-list", "--count", f"{remote}..HEAD"), root, None)
        return {"updated": False, "code": MAIN_SYNC_DIVERGED, "branch": branch, "path": str(root),
                "reason": f"the main checkout has diverged from {remote} "
                          f"({ahead.stdout.strip() or '?'} local commit(s) not on the remote) — "
                          f"a fast-forward would discard them, so nothing was done"}

    behind = _run_git(("rev-list", "--count", f"HEAD..{remote}"), root, None)
    # BEFORE the merge, because afterwards the answer is unrecoverable: the paths are tracked by
    # then and the human's bytes are already gone. Best-effort in the strongest sense — anything
    # this raises must cost the REPORT and never the fast-forward, which is the whole point of
    # the feature and was working before the report existed.
    # VMCP-281 (940): the places the probe's walk was DENIED. Declared out here so the `except`
    # below keeps whatever was recorded before the raise — a probe that skipped three directories
    # and then died has strictly more to say than one that only died.
    unreadable: list[str] = []
    try:
        doomed = _ignored_paths_the_ff_will_overwrite(root, remote, unreadable)
        doomed_answered = True
    except Exception:                               # noqa: BLE001 — a diagnostic, never a gate
        doomed = []
        # This flag says the probe RAISED, and that is ALL it says. Reading it as "computed an
        # answer" is the overclaim VMCP-258 (860) round 2 was very nearly bounced a second time
        # for: an empty `doomed` is MANY states, not two — this `except`, plus every NON-raising
        # give-up the probe's own docstring enumerates (the `_is_reproducible_ignored` filter,
        # each `return []` in it and in the two functions it calls, a walk stopped at
        # `_MAX_DIR_EXPANSION`, `_ignored_of` exhausting `_MAX_CHECK_IGNORE_CALLS`). Only the RAISE
        # is new, because only the RAISE became reachable when the after-probes were guarded; the
        # rest predate this card and are unchanged by it. So this conjunct closes the raise and not
        # the class, and nothing downstream may say otherwise — see `looked`, which carries the
        # measurement.
        doomed_answered = False
    # The two partial-apply snapshots (VMCP-244), taken here for the same reason as the probe
    # above — afterwards the answer is unrecoverable — and caught SEPARATELY on purpose: one of
    # them failing must not discard what the other already knows. The mistake that shape avoids
    # used to have a live example one function over, where a single unreadable path returned `[]`
    # for the WHOLE batch; VMCP-246 (837) closed that by splitting the batch instead, so the
    # example is now HISTORY rather than a thing to point at — the reasoning it taught is why
    # these are two `try` blocks and not one. Both stay best-effort: a diagnostic may cost the
    # report, never the fast-forward.
    try:
        tracked_before = _tracked_changes(root)
    except Exception:                               # noqa: BLE001 — a diagnostic, never a gate
        tracked_before = None
    try:
        prints_before = _fingerprints(root, doomed)
    except Exception:                               # noqa: BLE001 — a diagnostic, never a gate
        prints_before = None
    # BEFORE the merge because that is the entire discriminator, not because it is convenient:
    # a path the last failed sweep already rewrote holds the incoming bytes ALREADY, and only a
    # read taken now can tell that from a path whose bytes are about to die (VMCP-252). Its own
    # `try` for the reason the three above have theirs, and the failure direction is REPORT.
    try:
        already_upstream = _paths_already_holding_incoming_bytes(root, remote, doomed)
    except Exception:                               # noqa: BLE001 — a diagnostic, never a gate
        already_upstream = set()
    # Also before, and for the mirror reason: what a PART-WAY failure leaves behind untracked is
    # exactly the incoming paths that were not on this disk yet, and afterwards they are
    # indistinguishable from files the human had all along.
    try:
        absent_before = _incoming_paths_absent_here(root, remote)
    except Exception:                               # noqa: BLE001 — a diagnostic, never a gate
        absent_before = None
    merged = _run_git(("merge", "--ff-only", remote), root, None)
    if merged.returncode != 0:
        # git refused. Which of TWO states that leaves is measured off the TREE and never read out
        # of the message: locale and git version make the text unparseable in principle. (Not
        # because the three up-front messages are unlike each other — an earlier draft said they
        # "share their vocabulary with nothing" and the second pass disproved it: two of the three
        # share the whole phrase "would be overwritten by merge".) And note WHICH files git names
        # here — the one it could NOT write. The ones it DID write are the report's job, which is
        # the inversion card 835 opens with, so the message is passed through verbatim AND the
        # paths ride in their own keys.
        half: list[str] = []
        # `tracked_after` is carried into the report as well as differenced, because an empty `half`
        # is THREE states, not one, and only one of them may be reported as a quiet tree — see
        # `_partial_apply_reason`, whose first version conflated them (VMCP-252). None here means
        # "no answer" from either snapshot; the set itself is what git says NOW.
        tracked_after: set[str] | None = None
        # GUARDED EACH ON ITS OWN, exactly like the three before-merge snapshots above and for the
        # reason stated there — one of them failing must not discard what the other already knows.
        # Here it is worth MORE, not less: an escape from this branch takes the whole state dict
        # with it, `overwritten_ignored` included, and that was computed BEFORE the merge on the
        # one branch where a human most needs to know what got written halfway (VMCP-258, 860).
        # Reachable rather than theoretical: `_tracked_changes` runs `git diff-index` through
        # `_run_git`, which RAISES `WorkspaceError` on `_GIT_TIMEOUT`. Same rule, same words: a
        # diagnostic may cost the report, never the fast-forward.
        if tracked_before is not None:
            try:
                tracked_after = _tracked_changes(root)
            except Exception:                       # noqa: BLE001 — a diagnostic, never a gate
                tracked_after = None
            if tracked_after is not None:
                half = sorted(tracked_after - tracked_before)
        over: list[str] = []
        # `prints_answered`, not `prints_before is not None`, and the distinction is what the guard
        # COSTS if it is added carelessly: with the after-call able to give up, "I took a before
        # snapshot" stops implying "I compared". `_fingerprints` swallows `OSError` per path, so
        # this branch is far less reachable than its neighbour — it is here because the rule is
        # per-CALL, and because a guard on one of two symmetrical calls invites the next reader to
        # conclude the other was left bare deliberately. Note what this flag does NOT say: it is
        # true when `doomed` was EMPTY and nothing was compared at all, `{}` being "not None". Round
        # 1 of VMCP-258 (860) read it as a comparison and that is the whole of what round 2 fixed —
        # see `looked` below, which is where the difference is finally carried.
        prints_answered = False
        if prints_before is not None:
            try:
                prints_after: dict[str, tuple | None] | None = _fingerprints(root, doomed)
            except Exception:                       # noqa: BLE001 — a diagnostic, never a gate
                prints_after = None
            if prints_after is not None:
                prints_answered = True
                # The fingerprint says the file was REWRITTEN; `already_upstream` says it was
                # rewritten with the bytes it already had, i.e. nobody's content died here. Both
                # halves are needed and neither implies the other (VMCP-252).
                over = [p for p in doomed
                        if prints_after.get(p) != prints_before.get(p)
                        and p not in already_upstream]
        if not half and not over:
            # "Found nothing" is not "nothing happened", and when a probe was unavailable it is not
            # even that. Saying so is the whole subject of this card: a report that borrows the
            # reassurance of a check it did not run is how #806 shipped its own overclaim.
            #
            # `looked` must mean A COMPARISON HAPPENED — never "a call returned", and never "one of
            # them returned". Round 1 of VMCP-258 (860) wrote `tracked_after is not None or
            # prints_answered` and shipped BOTH of those errors at once. "or" is wrong because this
            # one sentence speaks for the two INDEPENDENT halves of "half-written", tracked and
            # ignored: let the ignored probe answer while `diff-index` times out and the report
            # vouched for a tracked half nobody looked at after the merge. And `prints_answered`
            # alone is wrong because `_fingerprints(root, [])` returns `{}`, which IS "not None", so
            # over an empty `doomed` it answered TRUE having compared ZERO paths — which made the
            # whole disjunction unconditionally true on the ORDINARY checkout, the one with no
            # ignored casualty, leaving `looked = False` reachable only when the TRACKED half was
            # dead AS WELL and one of the two `_fingerprints` calls had failed — either one, since
            # the guarded before-call leaving `prints_before is None` skips the after-call outright.
            # Both states are pinned next door, and they differ in a way worth keeping straight:
            # the first printed the reassurance over a genuinely HALF-APPLIED tree, the second over
            # an INTACT one, that refusal being an ordinary up-front abort where nothing is written
            # — so there the claim was unearned rather than false about the disk.
            #
            # Hence a conjunction, and each half counted by what it COMPARED. The tracked half:
            # `tracked_after is not None`, which already implies its own before-snapshot, being
            # assigned only inside that branch. The ignored half: paths compared when there were
            # paths to compare, and otherwise the pre-merge probe having returned an empty list
            # WITHOUT RAISING.
            #
            # Say "without raising", never "having computed one" — the second is false, and it is
            # the correction this round took from its own independent pass. That probe gives up in
            # several NON-raising ways, which its docstring enumerates, and every one of them
            # arrives here as a plain `[]` this conjunct reads as an answer. So `looked` closes the
            # RAISE and NOT the class. The residue is real and was MEASURED rather than conceded: a
            # half-apply whose only casualty is an ignored path the regenerable-name FILTER dropped
            # still prints the reassuring sentence over destroyed bytes, no key naming them and
            # `git status` empty on both sides — reachable with no injection at all. It is named
            # here instead of fixed because it PREDATES this card: on the pre-guard parent
            # `1fb0082` the same stand gives the same code, the same sentence and the same dead
            # file, so the guards neither introduced it nor widened it. Closing it needs the probe
            # to report its own confidence instead of a bare list — that probe's contract, and
            # another card's slice. Nor does a NON-empty `doomed` make this half complete: that
            # list is a lower bound by the probe's own docstring (same filter, `_MAX_DIR_EXPANSION`,
            # the `check-ignore` bisect all produce present-and-SHORT lists), and `_fingerprints`
            # swallows `OSError` per path, so a path whose `lstat` fails on both sides compares
            # equal. Neither is new here; both are why this sentence says what was CHECKED and
            # never what SURVIVED. One-way either way: an unanswered read must read as "cannot
            # say", never as "checked and clean".
            tracked_compared = tracked_after is not None
            ignored_compared = ((prints_answered and bool(doomed))
                                or (doomed_answered and not doomed))
            looked = tracked_compared and ignored_compared
            found = ("nothing half-written was found afterwards — which is what was CHECKED, not a "
                     "promise about the checkout"
                     if looked else
                     "and whether it had already written PART of the update could NOT be checked "
                     "on this run")
            return {"updated": False, "code": MAIN_SYNC_BLOCKED, "branch": branch,
                    "path": str(root),
                    "reason": f"`git merge --ff-only {remote}` refused; {found}: "
                              f"{(merged.stderr or merged.stdout).strip()}"}
        # Asked only once the state IS half-applied — a SCOPE decision, and the comment here used
        # to justify it with something false: "on the `blocked` fall-through nothing was written".
        # `blocked` is the fall-through where both probes were SILENT, which is not the same thing,
        # and the second pass built the difference — a part-way failure whose only residue is a new
        # untracked path grades `blocked` while having written that path. Naming a residue there
        # was not what this card was asked for, and that branch already says outright that it
        # reports only what it found; from sweep 2 on, git's own message names the path anyway.
        try:
            blockers = _untracked_left_behind(root, absent_before)
        except Exception:                           # noqa: BLE001 — a diagnostic, never a gate
            blockers = None
        state = {"updated": False, "code": MAIN_SYNC_PARTIAL, "branch": branch, "path": str(root),
                 "reason": _partial_apply_reason(root, remote, half, over, tracked_after, blockers,
                                                 (merged.stderr or merged.stdout).strip())}
        _add_capped(state, "half_applied", half)
        _add_capped(state, "overwritten_ignored", over)
        _note_probe_was_denied(state, unreadable)
        return state
    result = {"updated": True, "branch": branch, "path": str(root),
              "from": before, "to": target, "commits": int(behind.stdout.strip() or 0)}
    # Only when non-empty: a key present on every successful sync is the never-read field the
    # paragraph above is about. Unfiltered here, unlike on the refusal branch — this merge
    # COMPLETED, so every incoming path was written and there is nothing to filter down to.
    _add_capped(result, "overwritten_ignored", doomed)
    _note_probe_was_denied(result, unreadable)
    return result


def gc_workspaces(cwd: Path | None = None, workflow=None) -> dict:
    """Reap worktrees whose task is no longer alive on the board.

    THE tracker-aware operation, and the reason this module ships with the tracker: a crashed
    agent leaves a tree behind, and nothing but the board can say whether the task behind it is
    still being worked. Liveness differs by role and must not be conflated — a BUILD tree is
    alive while its task is in Design/Build assigned to me, a REVIEW tree while its card is in
    Review (any assignee — a reviewer works on someone ELSE's card, so filtering by ownership
    would reap the tree out from under a running review). Read-only against the tracker, same
    class as `claimable`.

    The safety guards of release still apply: a dead task whose tree holds unpushed commits or
    a dirty working tree is KEPT and REPORTED, never destroyed — `--gc` runs on every
    orchestrator tick, unattended, so this is the one place a mistake is not a red test but an
    agent's work silently destroyed while nobody is watching.

    `cwd` may be INSIDE a linked worktree (the normal place for a per-task agent to run this
    from, per SKILL.md) — `here` below is exactly that tree's toplevel (or the main repo's, if
    invoked from there), and `root` canonicalises it to the MAIN worktree so every path
    derivation (`worktree_root`, `_build_workflow`'s config lookup) agrees with create/release
    regardless of where --gc itself was invoked (review Critical 1's fix, applied here too).

    Review Critical 2: `here` is ALSO never reaped once its task reads as DEAD — a task leaves
    Build the instant its agent calls `advance(to='review')`, and that agent is very often
    still sitting in the tree afterwards (about to release it itself, or simply running its
    next --gc tick before it gets there). Destroying the directory a live process is standing
    in is not "a red test", it is that process's shell cwd vanishing underneath it. Round 2,
    Minor 1: this guard runs AFTER the liveness check, not before — a LIVE self-tree (the
    mainline: --gc runs every tick from inside the agent's own tree) is just another live tree
    and produces no entry in either list; only a self-tree that is ALSO dead reaches this
    guard and gets refused-and-reported, which is the one case a human actually needs to see.

    VMCP-71: the self-guard above covers only a --gc invoked from INSIDE the tree, and the pump
    invokes it from the MAIN checkout — so a dead tree that was TOUCHED moments ago is skipped
    too, and a later sweep is what inspects it (`_REAP_GRACE_SECONDS`, `_last_activity`). That
    skip used to be SILENT and in neither list; since VMCP-300 it is reported in `deferred` —
    see that paragraph below, and do not read this one as the current contract. That skip is the
    same overlap seen from the other side: a task leaves Build at
    `advance(to='review')` and a card leaves Review at a `needs_work` verdict, both while the
    agent that did it is still standing in the tree.

    VMCP-68: the refusals are reported in TWO lists, because "a human should look" and "expected,
    no action" were one list and the routine states never let it be empty — a Your Call card's
    unsaved work (every tick for hours) and a review tree's in-tree commit (forever). `kept` is
    now only the first kind, so EMPTY means nothing to read; `expected` is the second kind, kept
    and reported (nothing is hidden, and nothing is removed either — every entry still carries
    `released: false`) but not worth a look. The grading is `_keep_is_expected`, keyed on each
    refusal's `code`, its `role` and the board's parked set, and it fails toward `kept`. Round 2's
    fix to the live self-tree was this same failure in an earlier guise: whatever is added here
    later, the test to write is "on a healthy board, `kept` is empty".

    The two compose in one direction only, and it is the right one: a tree skipped as YOUNG never
    reaches a release guard, so it produces no refusal to GRADE — `expected` is for a refusal that
    WAS made and is routine, never for a tree gc declined to inspect. That boundary stands; what
    changed under VMCP-300 is where such a tree goes INSTEAD of nowhere.

    VMCP-300 (#1183) — THE `deferred` KEY, AND WHAT IT IS FOR. The sentence above used to
    end "and appears in NEITHER list", and that was the defect, not an aside. A sweep that
    declines to inspect three trees and answers `{"released": [], "kept": [], "expected": []}` is
    byte-identical to a sweep with nothing to do, and this payload is the pump's only window onto
    its own housekeeping — so the deferral was unobservable from the outside, on the one command
    the orchestrator runs every tick. It cost a card: three review trees, all dead by the board
    (two cards moved to Done, one bounced to Build by a `needs_work` verdict) and all created
    minutes earlier, produced those three empty lists on a live drain, and the observer — reading
    SKILL.md's own statement of the rule, correctly — filed it as a reaper that had stopped
    reaping. REPRODUCED on a constructed stand before anything was changed, and the CONTROL in the
    same round is what settles it: age those same trees past the window and the identical sweep
    reaps all three. The reap was postponed, never cancelled, and nothing said so.

    Why it was OBSERVED on the review side — and that is "observed", not "only bites there",
    which nothing measured. The role's LIVENESS works (`_read_liveness` keys `alive["review"]`
    off `Workflow.review_task_ids`, and on the stand it correctly reported all three as dead);
    what differs is where the window is measured FROM. VMCP-84 measured that a read-only reviewer
    moves neither marker `_last_activity` looks at, so for a review tree the count runs from
    CREATION, and a review tree therefore reads young from birth — unless every marker reads in
    the FUTURE, which the `0 <=` bound below refuses to honour. A BUILD tree is not exempt and
    was never claimed to be: its count runs from its LAST WRITE, which is moments before
    `advance(to='review')`, so it is deferred just as silently and for longer. The silence was
    never role-specific; the three trees in front of the observer happened to be review ones.

    `deferred` is to a SKIP what `expected` is to a REFUSAL: reported, no action needed, expires
    by itself. It is OPTIONAL and absent when empty (the `main_checkout` idiom), so `kept`'s
    VMCP-68 promise — empty means nothing to read — is untouched, and a tick that deferred
    nothing carries no `deferred` key at all. It changes NOTHING about what is removed: the
    branch it reports did nothing to the tree before and does nothing now.

    WHAT STAYS SILENT, deliberately, so the next reader does not "finish the job". Three skips
    above this one report nothing and should not: a LIVE tree (there is no news in a tree that is
    working), a worktree outside `worktree_root` (hand-made, not ours — and the ABSENCE of a bogus
    entry for it is the only thing that guard buys, see its own note), and a directory under our
    root whose name is not `task-<id>`/`review-<id>` (likewise not ours). The rule this card
    settles is narrower than "report every skip": report a skip of a tree that IS ours and IS dead
    and that we chose not to inspect. Everything else is not a deferral, it is a non-event.

    THE CADENCE THAT COMES OUT OF THAT COMPOSITION, measured across consecutive sweeps rather than
    reasoned about, because a report is read tick by tick: a standing refusal is reported on EVERY
    tick, so an empty `kept` means what VMCP-68 built it to mean. It did NOT use to: inspecting a
    tree means running `git status` inside it, that rewrites the index, and the next sweep then
    read the tree as freshly touched and skipped it as young — so `dirty` / `unpushed` /
    `unreachable-head` surfaced about once per `_REAP_GRACE_SECONDS` while refusals decided BEFORE
    any git call in the tree (`half-created`, `self-tree`) came every tick. VMCP-90 closed that at
    the source: gc's own inspection takes no optional locks (`_git_inspect`), so it is not a write
    and cannot pass for activity.

    AND IT CANNOT WIDEN THE REAPER, which is the direction that would have mattered: gc reaches
    `git status` only inside `_release_locked`, which then either REMOVES the tree (nothing
    survives to carry a taint) or REFUSES it — so the taint only ever lived on a tree some guard
    was already keeping, and no refusal depends on age. Dropping it therefore changes what is
    REPORTED, never what is removed: a tree gc now re-inspects every tick is one that has been
    quiet for a full window of SOMEBODY ELSE's activity, which is exactly the window's own
    promise. Both directions are pinned —
    test_gc_reports_a_standing_alarm_on_every_consecutive_sweep and
    test_gc_still_defers_to_a_real_write_in_a_tree_it_has_already_inspected.

    VMCP-72: the read under the lock is bounded OVERALL, not just per request
    (`_READ_DEADLINE_SECONDS`) — its request count grows with the board, so a per-request bound
    could not bound the hold. Past the budget the read RAISES, here, before a single tree has
    been inspected: the sweep is skipped whole and the next tick does it again. That direction is
    the invariant — a truncated or failed `alive` set must never reach the loop below, where a
    live tree missing from it would read as dead. It applies to the WHOLE read, so VMCP-68's
    `parked` set — a third consumer of the same fetch, and the one that made "Your Call" drive
    pagination too — is inside the budget rather than beside it.

    VMCP-238 (801) adds a further key, `main_checkout` (it was the FOURTH when it landed; VMCP-300
    later inserted `deferred` ahead of it, so in payload order it is now the fifth), and it is
    about the shared checkout
    rather than about any worktree. This command is where it belongs because it is the one call
    the pump already makes every tick, already canonicalises to the main worktree, already goes
    to the network and already returns a payload the rulebook tells the pump to READ — so the
    behaviour costs the orchestrator zero new steps, where a rule in SKILL.md would have cost a
    step that can be forgotten. It is OPTIONAL in the payload: absent means the checkout is
    current (or the operator opted out), present means it moved or is stuck. See
    `sync_main_checkout` for the fast-forward-only contract and for what it will never do.
    """
    here = repo_root(cwd).resolve()
    root = _main_worktree(here)
    # an injected workflow (tests) brings no client and therefore no deadline — the bound is a
    # property of the client gc BUILDS, so there is nothing to arm on a caller-supplied one.
    wf, deadline = (workflow, None) if workflow is not None else _build_workflow(root)
    wt_root = worktree_root(root)

    released, kept, expected, deferred = [], [], [], []
    # ONE lock for the whole sweep: _repo_lock is not reentrant, so call the _locked core, never
    # the public release_workspace wrapper (that would deadlock on its own flock).
    with _repo_lock(root):
        # Review Important 5: the liveness READ must happen INSIDE the lock. Taken before it,
        # a task could be claimed and its tree created between the read and the reap (that
        # `ensure_workspace` call serialises against the SWEEP via the same flock, but not
        # against a liveness snapshot taken before the flock was even acquired) — the fresh
        # tree is clean and pushed, so every guard below passes and it is destroyed out from
        # under a just-dispatched agent. One board fetch serves every set (Important 4), and
        # VMCP-72 bounds that whole read — arming the budget with the lock already held.
        alive, parked = _read_liveness(wf, deadline)
        for wt in list_worktrees(root):
            if wt["path"].parent != wt_root:
                # not ours — skip a hand-made worktree. Review Minor 12a: this guard is NOT
                # what protects that worktree, and a future refactor must not believe it is.
                # Constructed and measured: with this line deleted, a hand-made `task-77`
                # worktree outside the root is STILL untouched — `_release_locked` re-derives
                # the canonical path from `worktree_root` and never trusts the enumerated one,
                # so it simply finds nothing there (`released: []`, `kept: [77] "no worktree
                # for this task"`, tree and branch intact). Re-measured on this wave: all 59
                # workspace tests stay green with the guard deleted, so nothing here pins it —
                # the comment IS the pin. What the guard actually buys is the ABSENCE of that
                # bogus `kept` entry — real value (the `kept` signal discipline in SKILL.md
                # depends on it staying quiet), but not safety. Let `_release_locked` trust the
                # enumerated path and this line becomes load-bearing overnight, silently.
                continue
            parsed = _parse_workspace_name(wt["path"].name)
            if parsed is None:
                continue                       # under our root but not task-<id>/review-<id>
            role, task_id = parsed
            if task_id in alive[role]:
                # Review round 2, Minor 1: the alive check runs BEFORE the self-guard below.
                # --gc runs on every tick from inside the agent's OWN tree (the docstring's
                # own mainline), so a healthy self-tree used to fall through to the self-guard
                # and get reported under `kept` on every single sweep — a signal that is never
                # empty is a signal nobody reads. A live self-tree now takes this branch like
                # any other live tree: no entry in EITHER list. A DEAD self-tree still reaches
                # the guard below and is still refused and reported — exactly the case a human
                # needs to see.
                continue
            if wt["path"] == here:
                # Critical 2's guard: never reap the tree gc itself is running from — reached
                # only once the tree is ALREADY known dead (see above), and BEFORE the grace
                # window below (see its own note on why that order is the deliberate one).
                #
                # Straight into `kept`, never graded (VMCP-68): this refusal is about gc's own
                # invocation site, so no board state can make it routine — CODE_SELF_TREE is in
                # neither expected set. Same for the exception below.
                kept.append({
                    "released": False, "task_id": task_id, "role": role,
                    "path": str(wt["path"]), "code": CODE_SELF_TREE,
                    "reason": "gc was invoked from inside this worktree — refusing to remove it",
                })
                continue
            last = _last_activity(wt["path"])
            quiet_for = time.time() - last if last is not None else None
            if quiet_for is not None and 0 <= quiet_for < _REAP_GRACE_SECONDS:
                # VMCP-71's grace window: this tree is dead but was touched moments ago, so its
                # agent may still be standing in it between `advance(to='review')` and
                # `--release`. Defer to a later sweep — the reap is postponed, never cancelled.
                #
                # IN `deferred`, ITS OWN OPTIONAL KEY, AND NOT IN `kept` — VMCP-300 (#1183), and
                # the distinction is the whole of that card. This branch used to `continue`
                # SILENTLY, in NEITHER list, and the reasoning was sound as far as it went: `kept`
                # means "a human should look", a merely-YOUNG tree is not that, and a previous
                # round had already had to fix `kept` becoming never-empty. All of that still
                # holds — which is why the fix is a FOURTH key and not a fourth kind of `kept`
                # entry. What the old reasoning missed is that a sweep which declines to inspect
                # N trees and answers `{"released": [], "kept": [], "expected": []}` is
                # INDISTINGUISHABLE from a sweep that had nothing to do, and the payload is the
                # pump's only window onto its own housekeeping. Measured cost of that ambiguity:
                # this card exists because three review trees, all dead by the board and all
                # minutes old, produced three empty lists — and the observer, correctly reading
                # the rulebook's own statement of the rule, filed it as a reaper that had stopped
                # working. It had not; it had postponed. `deferred` says so.
                #
                # WHY IT DOES NOT REBUILD #516's DISEASE. The key is OPTIONAL and absent when
                # empty (the `main_checkout` idiom: present ⇒ read it), so `kept`'s "empty means
                # nothing to read" is untouched and a tick that deferred nothing carries no
                # `deferred` key at all — the payload it reads is the one it always read.
                # `expected` is untouched too, and deliberately NOT reused: this docstring's
                # own boundary is that `expected` is for a refusal that WAS made and is routine,
                # never for a tree gc declined to inspect. `deferred` is to a SKIP what `expected`
                # is to a REFUSAL — reported, no action, expires by itself.
                #
                # WHAT IT DOES NOT CHANGE: anything about what is REMOVED. This branch did
                # nothing to the tree before and does nothing now; the sweep's reaping behaviour
                # is byte-for-byte what it was. The window is NOT shortened, and specifically not
                # for review trees — see the note above `_REAP_GRACE_SECONDS`, where VMCP-84
                # measured that a read-only reviewer moves neither marker, so the window runs
                # from CREATION there and is that role's ONLY protection. Widening the reaper to
                # cure a reporting defect would trade a silent no-op for a vanished cwd.
                #
                # AFTER the self-guard above, also deliberately: "gc was invoked from inside this
                # worktree" is the stronger and more specific statement about the same tree (that
                # one KNOWS a process is there, this one only suspects it), and being young must
                # not silence a report a human can act on. Pinned by
                # test_gc_from_inside_a_dead_tree_completes_the_whole_sweep, whose self-tree is
                # left young precisely so this ordering cannot be flipped unnoticed.
                #
                # `0 <=` bounds the window BELOW as well as above: an mtime in the FUTURE (clock
                # skew, a restored backup, an unpacked archive) would otherwise read as young on
                # every sweep FOREVER. VMCP-300 CHANGED WHAT THAT COSTS rather than removing
                # it, and the direction is not a comparison anyone measured — it is two different
                # failures: before, an unbounded skip leaked a tree with nothing anywhere to
                # notice; now it would leak a `deferred` line on every tick that can never clear,
                # which is #516's never-read-signal disease in the one shape that does not expire
                # by itself. Neither is acceptable, so the bound stays either way. Out-of-window
                # in either direction falls through to the release guards, which still refuse to
                # destroy anything that holds work.
                #
                # It decides only the case where EVERY marker is future (VMCP-84). While one real
                # marker survives, `_last_activity` no longer offers the future one at all — this
                # bound used to be reached with a MAX taken over both, so a skewed mtime masked a
                # fresh one and the tree was reaped mid-turn. The two now split the job cleanly:
                # which markers count is the reader's, what an all-bad reading means is this line's.
                deferred.append({
                    "released": False, "task_id": task_id, "role": role,
                    "path": str(wt["path"]), "code": DEFER_YOUNG,
                    "quiet_for_seconds": int(quiet_for),
                    "reason": (
                        f"dead on the board, but something wrote here {int(quiet_for)}s ago — "
                        f"inside the {int(_REAP_GRACE_SECONDS)}s grace window, so its agent may "
                        f"still be standing in it. NOT inspected and NOT removed; a later "
                        f"sweep inspects it, and removes it unless a release guard refuses"
                    ),
                })
                continue
            try:
                result = _release_locked(root, task_id, role)
            except Exception as e:  # noqa: BLE001 — Important 3: one bad tree (locked, a race,
                # a permission error — anything _git surfaces as WorkspaceError, or worse) must
                # never abort the sweep and discard every verdict already decided for the OTHER
                # trees. Report it exactly like any other refusal and keep going.
                kept.append({
                    "released": False, "task_id": task_id, "role": role,
                    "path": str(wt["path"]), "code": CODE_RELEASE_ERROR,
                    "reason": f"{e.__class__.__name__}: {e}",
                })
                continue
            if result["released"]:
                released.append(result)
            else:
                (expected if _keep_is_expected(result, parked) else kept).append(result)
    # VMCP-238 (801): the shared checkout, AFTER the sweep and OUTSIDE the lock — both on
    # purpose. After, because reaping is this command's job and a bolted-on courtesy must not
    # delay or endanger it. Outside, because the fast-forward starts with a network `git fetch`,
    # and VMCP-72 bounded how long the flock may be held precisely so a slow network cannot wedge
    # every other agent's ensure/--release: nothing else in this module writes to the main
    # checkout's index, working tree or `refs/heads/<base>`, so it needs no lock of ours.
    #
    # BEST-EFFORT, in the same sense as the epic marker and the Your Call ping: the reaper must
    # not acquire a new way to fail. Anything this raises becomes an entry, never an exception —
    # a wedged fetch (WorkspaceError from the timeout), an unreadable repo, a git that is not
    # there. The one thing it must never do is cost a verdict already decided above.
    result = {"released": released, "kept": kept, "expected": expected}
    if deferred:
        # OPTIONAL, exactly like `main_checkout` below: absent means the sweep declined nothing,
        # so a quiet tick's payload is unchanged and "present ⇒ read it" is the whole rule an
        # agent has to learn. Placed BEFORE `main_checkout` so the per-worktree keys stay
        # together — this one is about trees, that one is about the shared checkout.
        result["deferred"] = deferred
    try:
        main_state = sync_main_checkout(root)
    except Exception as e:  # noqa: BLE001 — see above: report it, never raise past the sweep
        main_state = {"updated": False, "code": MAIN_SYNC_ERROR, "path": str(root),
                      "reason": f"{e.__class__.__name__}: {e}"}
    if main_state is not None:
        result["main_checkout"] = main_state
    return result


def run_workspace(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="vikunja-mcp workspace")
    parser.add_argument("task_id", nargs="?", type=int, help="create a workspace for this task")
    # default=None, NOT "build": every guard below has to answer "did the caller ASK for a
    # role?", and an eager default makes that unanswerable — `--gc --role review` would be
    # indistinguishable from a plain `--gc`. The default is applied once, just below.
    parser.add_argument("--role", choices=("build", "review"), default=None)
    parser.add_argument("--at", help="review role: the ref to check out (default origin/<main>)")
    parser.add_argument("--release", type=int, metavar="TASK_ID")
    parser.add_argument("--gc", action="store_true",
                         help="reap worktrees whose task is no longer alive on the board")
    try:
        args = parser.parse_args(argv)
        role = args.role or "build"
        # Review Important 6 + Minor 7: argparse lets every one of these combinations through,
        # and each USED to be accepted with one of the arguments silently dropped — `42
        # --release 9` acted on 9 and forgot 42; `--gc --at <sha>` swept anyway; `42 --at <sha>`
        # (no --role review) ignored the sha even though --help says it is review-only. A
        # silently ignored argument on a CLI a pump drives unattended is how a reviewer ends up
        # somewhere it never asked to be. Refuse instead; the caller can always say it again.
        if args.gc:
            if args.task_id is not None or args.release is not None:
                raise WorkspaceError("--gc cannot be combined with a task id or --release")
            if args.role is not None or args.at is not None:
                raise WorkspaceError("--gc takes no --role/--at: it sweeps both roles, at no ref")
            result = gc_workspaces()
        elif args.release is not None:
            if args.task_id is not None:
                raise WorkspaceError(
                    f"--release {args.release} already names the task — drop the positional "
                    f"{args.task_id}, or drop --release to CREATE a workspace for it"
                )
            if args.at is not None:
                raise WorkspaceError("--at is for creating a review tree, not for --release")
            result = release_workspace(args.release, role=role)
        elif args.task_id is not None:
            if args.at is not None and role != "review":
                raise WorkspaceError("--at applies only to --role review")
            result = ensure_workspace(args.task_id, role=role, at=args.at)
        else:
            raise WorkspaceError("give a task id to create, or --release <task id>")
    # NO `except SystemExit: raise` here (review Minor 10): SystemExit derives from
    # BaseException, so the `except Exception` below never caught it in the first place —
    # argparse's own exits (`--role bogus`, `--help`) pass straight through either way. The
    # clause read as load-bearing and was not.
    except Exception as e:      # noqa: BLE001 — a CLI: ANY failure is one JSON line + exit 1
        print(json.dumps({"error": f"{e.__class__.__name__}: {e}"}))
        return 1
    print(json.dumps(result))
    return 0
