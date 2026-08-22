"""`vikunja-mcp claimable` — the sibling-EXPORTED "is there claimable work for THIS
token, right now?" verdict.

CROSS-REPO CONTRACT (consumed by hgdev-acp's repo-agent-loop pre-launch idle check).
One JSON line on stdout, and nothing else:
  {"claimable": bool, "kind": "...", "task_id": int|null}   exit 0  (the check RAN)
  {"error": "<ExceptionClass>: <message>"}                  exit 1  (the check FAILED)
kind: queue|resume|stuck_claim|review (claimable) / empty|starving|cycle (not).
The key set and the exit-code split are public API — renaming a key or repurposing a
code breaks the hub's check. It breaks CLOSED (a failed check is exit 1, so the hub's
loops go red rather than silently idling), but it breaks.

WHY THIS EXISTS. The hub used to GUESS claimability its own way, from kanban BUCKET
PRESENCE. On 2026-07-14 this project's own board proved that wrong and expensive:
Queue/Design/Build were empty while Review held 25 tasks ALL assigned to the agent,
written up at the time as done work awaiting a HUMAN's Done move, the one transition no
agent tool can make. Bucket presence read "work!" forever, yet the gates themselves offered
nothing. Result: ~144 no-op agent boots/day ≈ $105/day for zero work.

WHICH GUARD MAKES SUCH A BOARD QUIET CHANGED IN #991, and the distinction now matters to
anyone reading this. It used to be authorship — "you never review your own work" — and
that was too wide: it also silenced cards with a report and NO verdict, which are cards
AWAITING review, so in a solo setup kind='review' could never be produced at all and the
hub never woke an agent for a pending review. Authorship is now checked only where a repo
sets require_review_independence; what keeps FINISHED own work quiet is worklog FRESHNESS,
a verdict not OLDER than the report. What the 2026-07-14 cards carried was never measured:
the same-day write-up characterised them as done work, and until #1002 this header went
further and said they all carried verdicts — which came from the fixture's shape, not from
that board (provenance in docs/dossier/claimable.md). It no longer says, and does not need
to: ruled on, freshness holds them quiet; still owed a review, that card is claimable on
purpose today, which is the #991 fix and not the regression coming back. Both shapes are
pinned in test_claimable_cmd (25 ruled-on cards ⇒ kind='empty'; the same 25 without
verdicts ⇒ one review offer each, and the lane runs dry after 25 rounds only because a
verdict is cast each round — that condition is a RULEBOOK obligation, not something this
code can enforce).

So the hub stops guessing and asks the gates themselves: this runs the
SAME Workflow.next_task() the agent runs, so the exported verdict has ZERO drift from
the agent's own by construction — one implementation of the rules, not two.

THAT PROPERTY IS ABOUT THE CONSTRUCTION AS MUCH AS ABOUT THE CALL, and it is the one part
of it a reader has to keep: the same `next_task` inside a Workflow built from DIFFERENT
Config keys is a different set of gates. So a Config key `Workflow` READS on this path is
wired at the call below as well as in `server._build_workflow` — and a third site,
`workspace_cmd._build_workflow`, wires none at all, which is safe only because `--gc` calls
no method that reads one. `require_review_independence` was not wired here; the drift that
made it matter ran from #991 to VMCP-295 (1169), and it was real and measured — see the
comment on that kwarg and docs/dossier/claimable.md.

next_task is READ-ONLY (verified call inventory: me / kanban_view / view_tasks (which itself
probes GET /info once, cached, for the page size) / get_task / comments — all GETs; pinned by
test_claimable_cmd.test_the_check_makes_no_writes), so the hub may poll this at any cadence
without mutating the tracker.

Config comes from the standard 4 layers; the hub supplies layer 1 (VIKUNJA_URL/
VIKUNJA_TOKEN/VIKUNJA_PROJECT_ID in this process's own env). stdout carries the one
JSON line and NOTHING else — uv/python/anything-else noise belongs on stderr.

STDERR CARRIES A BREADCRUMB TRAIL, AND IT IS DELIBERATELY *NOT* PART OF THE CONTRACT.
Until #521 deferred the MCP SDK import, `MCPServer.__init__` ran `logging.basicConfig(INFO)`
process-wide from module scope, so this check ACCIDENTALLY emitted one httpx INFO line per
tracker call. #521 removed that side effect and stderr went silent. That costs nothing on the
two lanes that dominate — on SUCCESS the hub discards stderr entirely (pinned hub-side by
TestUvxCheckerStderrNoiseIgnoredOnSuccess), and on this check's own failure lane (exit 1 +
`{"error"}`) it reads only `error` from STDOUT — but stderr does reach a run row, via
`detail()`, on FIVE lanes, not one: ctx timeout, spawn failure / nonzero exit without an
error line, unparseable stdout, unknown `kind`, and `claimable` contradicting its `kind`
(claimable.go, read 2026-08-02). The WEDGE is the lane that MOTIVATES this — it is the one
where the child said nothing else at all, the old trail of completed GETs was what told you
where it hung, and hgdev-acp already spent a PR (wedge-kill) on it — but it is not the only
row that will now carry breadcrumbs.

ONE ROW THIS CANNOT REACH IS THE TOO-OLD-SIBLING ONE, and the reason is a rollout order rather
than a preference. The hub's own comment documents that row as reading exactly
`loop idle-check: claimable check: bad verdict json` BECAUSE `detail()` was empty, and nothing
here can add to it: a sibling too old to know this subcommand is older than the file that
implements it, and older still than the trail. Measured with `git log` rather than assumed —
`claimable_cmd.py` was ADDED in 713bcdf (`--diff-filter=A`), first shipped in v0.2.41, and
`_Trail` first appears in 2f4f1bb (`git log -S`), first shipped in v0.2.158, with the first an
ancestor of the second (`merge-base --is-ancestor`, exit 0). So a build predating the
subcommand predates the trail too and emits no `[claimable]` marker at all. Grep for the old
exact string and it still matches.
(Take those releases from `git tag --contains --sort=version:refname`, not the bare form: the
DEFAULT sort is LEXICAL, which puts v0.2.100 ahead of v0.2.41 and answers the question wrong.)

That the row is EMPTY, rather than merely trail-free, is the HUB's dated observation and not a
claim of this change — and it is not structural. Such a build falls through argv dispatch into
the stdio server, whose `main()` runs the artifact self-heal FIRST, and that prints a
"refreshed N stale artifact(s)" note to STDERR (read at 713bcdf^). It is conditional on having
healed something, which is why an empty `detail()` is what the hub measured; a box whose
installed artifacts came from a NEWER sibling — the very state an inverted rollout produces —
is where it would not be.

READ THE INVERSE, which is worth more than the warning it replaces: a `bad verdict json` row
with NO `[claimable]` marker in it now carries information it did not carry before, when that
was the only shape the row had. What the absence proves is that the sibling is older than the
trail (< v0.2.158); on THIS lane that means the inverted rollout order the hub's comment was
written for, since a build between v0.2.41 and v0.2.158 knows the subcommand and answers it.
The signal is only as good as uv's silence, though — `snippet` keeps the HEAD, so ~200 B of
uv's own noise ahead of the trail (a cold resolve, an import-time warning from any dependency)
cuts the marker off too and makes the absence a false positive.

So the trail is back BY DESIGN instead of by accident, and it differs from the httpx lines in
the three ways that matter there. (1) A token is written BEFORE each request — httpx logged
AFTER the response (its INFO line carries the status code, so it cannot be anything else),
which makes the request that never finished visible only as an ABSENCE. (2) It is written and
flushed incrementally, so a process killed mid-flight has ALREADY said it. (3) It is SMALL,
for the reason below, which is the constraint that shaped everything else here.

    [claimable] cfg/10 info views:1 :2 tasks:1 :2 :3 :4 user tasks/628 /164 /547 /536 end/12@2.4s

One line, one token per request, appended as the run goes and terminated by a NEWLINE only
when the run finishes. Read it as: `cfg/<project>` — config loaded, we are past uvx and the
imports; then the last path segment of each request, `:N` for a page and `/N` for an id, with
a repeat of the same endpoint abbreviated to its suffix alone (`views:1 :2` is two requests);
then `end/<requests>@<elapsed>` or `fail/<requests>@<elapsed>`.

THE "IT NEVER FINISHED" SIGNAL IS THE ABSENCE OF THAT CLOSING `end/`|`fail/` TOKEN, not the
absence of the newline. The newline is the clean version of the same fact and it holds for
SIGKILL (pinned by test_a_killed_check_still_says_where_it_was, which measures 25 B on its own
fixture ending at `views:1` — exactly the request that hung) and for SIGTERM,
because neither runs any Python. SIGINT does: the traceback glues itself onto the last token
and brings its OWN newline, so a trail can end in `\\n` and still be a corpse. Read the last
TOKEN, not the last byte.

WHY SO TERSE — MEASURED IN THE CONSUMER, not guessed. hgdev-acp puts the child's stderr on a
run row through `detail()`, and that string goes through `snippet()`, which is capped at
`snippetCap = 200` BYTES and keeps the HEAD (internal/hub/vikunja/vikunja.go and
claimable.go, read 2026-08-02). The first shape of this feature, one verbose line per
request, cost 727 B on this project's live board: the hub would have shown its first four
lines and cut off exactly the tail, which is the only part that says where it hung. A trail
that overflows that cap is worse than no trail, because it looks like a diagnosis.

AND THE TRAIL DOES NOT GET THAT BUDGET TO ITSELF, which an earlier draft here got wrong by
saying "a wedge's stdout is empty, so the trail gets the whole budget". True about stdout,
irrelevant about the budget: `detail()` is `stderr + "\\n" + stdout`, and the child is spawned
through `uvx`, whose OWN stderr is written FIRST and is therefore never the part that gets
cut. Measured: `uvx --refresh-package cowsay cowsay -t hi` emits 27 B (`Installed 1 package
in 2ms`); the hub's own test models 32 B. So budget the trail against roughly 170 B, not 200.

AND STDOUT PAYS THE REST, on the lanes where stdout IS the evidence — the other half of that
same shared budget, and the honest cost of this feature rather than a free win. `detail()`
writes stderr FIRST and the cut keeps the HEAD, so every byte of trail displaces exactly one
byte of stdout. Against the 84 B this check emitted on the run measured for this paragraph
(10 requests, live board), an offending stdout keeps 115 B of the 200 rather than all 200, so
the loss begins at 116 — and that is the BEST case, because it spends uv's share on stdout:
put the 27 B of the paragraph above in front and stdout keeps 88. Both figures are arithmetic
on `snippetCap`, where pure ASCII cuts at exactly the cap, as the hub's own test pins.

It costs something on ONE lane: `bad verdict json`, where `detail()` is the only CHILD-derived
content of the row (the rest is a constant prefix — the row is not "nothing but `detail()`",
which is why an empty one reads as the bare string above) and the hub's own test
(TestUvxCheckerMalformedStdoutIsError) asks that "the offending line is shown". It costs nearly
nothing on `unknown kind` and on `claimable` contradicting its `kind`, where the discriminating
value is already in the message text OUTSIDE `detail()`. And it costs nothing in the two
situations this exists for, which is the same reason they need breadcrumbs at all: a killed
check and a child that never ran leave stdout EMPTY, so there is nothing there to displace.
Breadcrumbs on the silent lanes, bought with head-room on the loud one.

The form above cost 94 B on the live board (12 requests): 31 B of frame plus 5.25 B per step
on THAT mix. Against ~170 B that leaves about 14 more steps — not the "twenty" a previous
draft got by spending uv's share, and not the "thirty" the draft before that got by bad
arithmetic; the numbers are written out because this one line has now been wrong twice.
5.25 B is a MEAN and the spread is wide — 3 B for an abbreviated page (`" :2"`), 10 B for a
task fetch (`" tasks/628"`) — so the same room is ~35 more page-steps or ~10 more task
fetches. What eats it fastest is therefore a run heavy in TASK FETCHES, which is not the same
as a board heavy in REVIEW; see the length note at the end, which measures that one.

The `cfg` token deliberately omits the URL: the hub passed it in and its own row names the
binding, and at that per-step cost the ~28 bytes of a scheme+host buy about five more steps.
There is no per-step timing for the same reason — the hub's own ctx bound already dates a
wedge.

ON BY DEFAULT; opt out with VIKUNJA_MCP_NO_TRACE=1 (stderr is then empty again, byte for
byte). On-by-default is the design decision, not laziness, and there are two reasons rather
than one. First: a wedge is not reproducible on demand, so a flag you must set IN ADVANCE only
ever gets set by someone who already knows — i.e. never on the tick that actually hung.
Second, and it settles the question rather than weighing it: had this been default-OFF, the
hub could not have turned it ON by environment at all. Its child gets an ALLOWLISTED env —
PATH/HOME/TMPDIR/locale, the proxy and CA vars, UV_*/XDG_*, plus the three
VIKUNJA_URL/TOKEN/PROJECT_ID the hub appends itself — and everything else is dropped,
INCLUDING every inherited VIKUNJA_* name, deliberately (checkerEnv/checkerAllowedEnv in
internal/hub/vikunja/claimable.go, read 2026-08-02), which is exactly the prefix any switch
of ours would carry. A default-off trail would therefore be off in the ONE process that needs
it until somebody changed the other repo — and by the same mechanism the hub cannot turn this
one off either, which is the honest cost of the choice: the 94 B is unconditional there. The
opt-out serves humans and other callers.

DO NOT PARSE IT, and do not let it grow a consumer. The key set and the exit-code split on
stdout are public API; this trail is prose for a human reading a dead process's tail, and its
shape may change in any release with no hub-side rollout. THREE things GROW it, and only one
of them is bounded OVERALL. One is api.py's own pagination ceiling — measured against a board
that never stops paging, one line reached 545 B over 123 requests before `fail/123`. The second
is the BOARD's CONTENT, which has no ceiling at all: next_task issues one
`GET /tasks/<id>/comments` per foreign non-epic card in Review, BEFORE it checks whether that
card's verdict is stale (workflow.py, the Review offering loop), so the line grows with the
Review column and nothing here stops it. It grows SLOWLY, though, and that correction is worth
carrying: those calls repeat one endpoint, so the elision fires and each extra card costs 3 B
after the first (measured 11, 3, 3, 3, 3, 3 through the real `_Trail`) — the cheapest step
there is, not the dearest. "Bounded by pagination" is the wrong thing to remember, and so is
"a Review-heavy board eats the budget fastest": it eats it slowly and without bound, which is
the harder failure to see coming. THE THIRD ARRIVED WITH #1179 and is the NEIGHBOURS' boards, so
this enumeration read as complete through FIVE releases while it was not — 5 at `be22cb4`, the
#1199 landing that closed the window (v0.2.332 through v0.2.336 ship #1179's `b0ce2c6` beside an
enumeration of two). Both this sentence and its twin in the dossier read FOUR until #1212; the
tag-by-tag derivation, and the exact phrase to grep for — a bare word does NOT discriminate, it
hits every tag — are in `docs/dossier/claimable.md`. A free-Queue card whose
predecessor lives on another project costs a `get_task` for the predecessor plus — for each
DISTINCT neighbour project — a `kanban_view` and an EXHAUSTIVE `view_tasks` of that project, the
last being many requests on its own. #1199 is what makes "distinct project" the unit for the two
BOARD reads: measured on FakeAPI, M free-Queue cards gated on ONE neighbour cost 1, 3 and 5 reads
of that neighbour's board at M = 1, 3 and 5 before it and exactly 1 at all three after. The
`get_task` did NOT move and is still one per gated card. Nor is any of it BOUNDED — the number of
distinct neighbours in one `next_task` is bounded by nothing here, and each of those board reads
carries api.py's pagination ceiling of its own, which bounds a read and not their number. It is
plausibly the longest-lived of the three, on a mechanism rather than a measurement: a
`handoff`-parked card waits until the far card reaches Review or Done, and this command runs once
per hub poll tick. (Several things CUT the line
short without bounding how fast
it grows: a write failure disables it, VIKUNJA_MCP_NO_TRACE never starts it, and the hub's own
ctx bound kills the process. None of them is a size budget.) A pathological board
does overflow and the hub does lose the tail again — the compression buys headroom, not a
promise.

STDOUT DID NOT MOVE, and that is the claim that matters: byte-for-byte identical with the
trail on and off, in both the success and the failure lane, and the exit-code split with it.
Not "54 B and 140 B are the contract" — those are just what THIS board and THIS server said
on the day (a 3-digit task id gives 53 B, and the failure line carries the server's own
message, measured at 165 B elsewhere).

THAT IDENTITY IS MEASURED, NOT PINNED, and saying so precisely matters because it has been
mis-attributed twice. #521 asserted it in its commit MESSAGE, not in a test (fe18b4a touches
CLAUDE.md, server.py and test_server.py — no claimable test at all). Nor do the tests HERE pin
it: a mutant making stdout differ between trail-on and trail-off on BOTH lanes leaves all 48
GREEN, because they assert one JSON line whose PARSED verdict matches, and only the SUCCESS
lane is run both ways at all (the two failure-lane tests both run with the trail on). That is
deliberate rather than a gap to close: the hub parses the line, so parsed equality is the
property the contract actually needs, and a byte-level pin would fail on a key reordering the
consumer cannot see. Byte-identity is a stronger claim, established by `cmp` on real runs, and
whoever next changes what stdout prints has to re-establish it the same way.

DOSSIER: `docs/dossier/claimable.md` — the measured evidence under the rules in this
module: the dogfood regression that created this command and the
200-byte cap that forces the stderr trail to stay terse.
Read it before changing a guard here; CLAUDE.md carries only the rule.
"""
import json
import os
import sys
import time

from vikunja_mcp.api import VikunjaAPI
from vikunja_mcp.config import load_config
from vikunja_mcp.workflow import Workflow

TRACE_OPT_OUT_ENV = "VIKUNJA_MCP_NO_TRACE"
_TRUTHY = frozenset({"1", "true", "yes", "on"})


def trace_enabled(environ: dict | None = None) -> bool:
    """The opt-out, spelled exactly like setup_cmd's VIKUNJA_MCP_NO_SKILL_SYNC one.

    Duplicated rather than imported ON PURPOSE: setup_cmd carries the whole install-skill
    machinery, and the hub pays this process's import cost on EVERY poll tick — which is the
    entire point of #521. Keeping claimable's import surface at api/config/workflow is worth
    three duplicated lines; the shared thing here is a CONVENTION, not code."""
    env = os.environ if environ is None else environ
    return env.get(TRACE_OPT_OUT_ENV, "").strip().lower() not in _TRUTHY


def _step(request) -> tuple[str, str]:
    """(name, suffix) for one request — the compression the 200-byte budget forces.

    Generic on purpose, so a path this module has never seen still produces something: the
    LAST path segment names the step, and when that segment is all digits it is an id, so the
    segment before it names the step and the id becomes the suffix. `?page=N` is the other
    suffix. Both sigils are kept distinct (`:` page, `/` id) because the two collide on name:
    the board read is `.../views/40/tasks?page=1` and a single task is `/tasks/628`, and
    `tasks:1` vs `tasks/628` is what tells them apart. A non-GET keeps its method (nothing on
    this path sends one — next_task is all GETs — but a token that silently dropped the verb
    would be a lie the day one appears)."""
    parts = [p for p in request.url.path.split("/") if p]
    if parts[:2] == ["api", "v1"]:
        parts = parts[2:]
    name, suffix = (parts[-1] if parts else "/"), ""
    if name.isdigit():
        name, suffix = (parts[-2] if len(parts) > 1 else "?"), "/" + name
    page = request.url.params.get("page")
    if page:
        suffix += ":" + page
    if request.method != "GET":
        name = f"{request.method} {name}"
    return name, suffix


class _Trail:
    """The stderr breadcrumbs — and also httpx's request event hook, because it IS the callable
    passed as one. See the module docstring for the shape, the byte budget behind it, and why
    it is diagnostics rather than a contract."""

    def __init__(self, enabled: bool = True):
        self.enabled = enabled
        self.requests = 0
        self._t0 = time.monotonic()
        self._last: str | None = None
        self._open = False

    def _write(self, token: str) -> None:
        """One token, written and flushed NOW, on the ONE line the whole run shares.

        Written straight through rather than accumulated and printed at exit: the lane this
        serves is a process that never reaches its exit, so a buffered trail would be lost in
        exactly the case it exists for. Here `flush=True` is load-bearing rather than
        belt-and-braces, and that is a consequence of the single line: CPython line-buffers
        sys.stderr, and this never emits a newline until the run ENDS — so without the flush a
        killed process would say nothing at all. Both halves are pinned by the SIGKILL test,
        which goes RED under either mutation."""
        # Collapse ANY whitespace inside the token, so "one token per request" is true by
        # CONSTRUCTION rather than by the shapes _step happens to produce today. It was not:
        # a non-GET yields `POST tasks` + `/628`, which is two whitespace-separated tokens for
        # one request — false prose and a broken test invariant the day a non-GET appears. A
        # `page` value carrying a newline (unreachable from api.py, reachable by construction)
        # would likewise have split the ONE line the 200-byte budget is built on.
        token = "_".join(token.split())
        if not self._open:
            self._emit("[claimable]")
            self._open = True
        self._emit(f" {token}")

    def _emit(self, text: str) -> None:
        """The ONE place this class touches stderr — so the guard below cannot be half-applied.

        TWO INDEPENDENT PROPERTIES LIVE HERE, and a previous draft fused them into one and got
        both halves wrong. Keep them apart.

        (a) `sys.stderr` is resolved per WRITE, so a caller may replace the stream after a
        _Trail already exists. That IS load-bearing, and the draft that retracted it as "not
        the real reason" was itself the error: binding the stream in __init__ instead was
        mutated in and the file went RED, 1 failed / 47 passed, on
        test_every_token_is_one_whitespace_free_word — which builds a _Trail and THEN swaps
        sys.stderr. "Every test swaps the stream before a _Trail exists" was simply false.

        (b) The `is None` guard, which does NOT rest on (a): under that same binding mutation
        test_a_closed_stderr_never_pushes_the_trail_onto_stdout still PASSED, because a process
        whose fd 2 was closed has `sys.stderr is None` already at construction — so a bound
        stream reaches the guard just as well. What the guard answers for is that
        `print(..., file=None)` writes to sys.STDOUT by documented default, with no exception
        for a try/except to catch. On the single line that splices the trail INTO the verdict.
        Run with the guard removed, the pinning test's own fixture prints
        `[claimable] cfg/3{"claimable": true, "kind": "queue", "task_id": 812}` — and since the
        consumer reads the LAST stdout line, what it reads is the next one, ` end/1@0.0s`: bad
        verdict json, fail-CLOSED, at exit 0. hgdev-acp always sets
        cmd.Stderr so it never saw this, but `2>&-` or a daemonized caller reproduces it in one
        line. Deleting the guard turns that one test RED and, measured, nothing else.

        THE TRAIL MAY NEVER BREAK THE CHECK. It runs inside an httpx event hook, so anything
        raised here leaves through `client.send` -> `_req` -> `next_task` and lands in
        run_claimable's catch-all — turning a working check into `{"error": ...}` + exit 1,
        i.e. a fail-CLOSED hub loop, over a closed pipe or a full disk. A diagnostic that can
        do that is worse than no diagnostic, so a write failure disables the trail for the
        rest of the process and the check goes on. "Retrying would pay the same cost N times"
        is the reason, and it is only true of a PERMANENT failure: a single transient write
        error (a non-blocking pipe returning EAGAIN) costs the whole trail from that point,
        which is the accepted price of not carrying retry state into a diagnostic. Genuine
        EINTR never arrives here at all — PEP 475 retries it inside the interpreter.

        Funnelling every write through here is the FIX for a hole this card found in itself:
        close()'s terminating newline used to be printed directly, outside any guard, and
        passed the first test only because close() calls _write first and that earlier failure
        had already flipped `enabled` — correct by accident, and wrong for a stream that fails
        on THAT write alone, which would have raised AFTER the verdict was on stdout. Pinned
        by test_a_broken_trail_cannot_break_the_check, all three cases."""
        if not self.enabled:
            return
        stream = sys.stderr
        if stream is None:                 # see above: file=None means STDOUT, not "nowhere"
            self.enabled = False
            return
        try:
            print(text, end="", file=stream, flush=True)
        except Exception:  # noqa: BLE001 — see _write: the verdict outranks its own breadcrumbs
            self.enabled = False

    def note(self, token: str) -> None:
        """A step that is not a request (`cfg/<project>`); never abbreviated against a neighbour."""
        self._last = None
        self._write(token)

    def close(self, token: str) -> None:
        """The last step, plus the NEWLINE that is the whole difference between a run that
        finished and a run that was killed on its last token."""
        self.note(token)
        self._emit("\n")

    def __call__(self, request) -> None:
        """httpx's `request` event hook: fires BEFORE the bytes go out — that is the whole
        difference from the httpx INFO lines this replaces — and once per RETRY too (api.py
        retries by re-entering client.request, verified: three attempts, three tokens), so a
        retry storm reads as repeats rather than as a hang. Only the compressed path and, for
        a non-GET, the method are printed: the token lives in the Authorization HEADER and no
        call here builds a URL out of it (pinned by test_the_trail_never_leaks_the_token).

        `_step` is guarded for the same reason `_emit` is (see there): it is generic, but
        "generic" is a claim about inputs nobody has seen yet, and the cost of being wrong
        inside a hook is the whole check failing closed. An unnameable request still counts
        and still leaves a mark — `!` — rather than taking the verdict down with it. (`!` and
        not `?`: `_step` already spells an unnameable PARENT `?`, as in `/api/v1/7` -> `?/7`,
        and a marker that reads like a near-miss of another marker is a bad marker.)"""
        self.requests += 1
        try:
            name, suffix = _step(request)
        except Exception:  # noqa: BLE001 — a token we cannot build is not worth the verdict
            name, suffix = "!", ""
        self._write(suffix if name == self._last and suffix else name + suffix)
        self._last = name

    def elapsed(self) -> str:
        return f"{time.monotonic() - self._t0:.1f}s"


def classify_next(result: dict) -> dict:
    """Map a next_task() result onto the flat verdict.

    Claimable == a task is offered at all; `kind` names WHICH of next_task's branches
    offered it, so the hub can log/branch without re-deriving the rules. The flag
    precedence mirrors next_task's own result shapes: a review offer carries review:true,
    a resume carries resume:true, the free queue carries resume:false + task.

    ⚠ `stage` is on EVERY task-bearing result now (all four: free queue and stuck claim both
    say 'Queue', an active task says Design/Build, a review offer says 'Review' — SKILL.md's
    tick branches on it uniformly). So `stage` alone does NOT identify a shape, and the check
    below must stay INSIDE the resume-truthy branch: it separates a stuck claim from an active
    task and NOTHING else. Hoisted out, a free-queue result (resume:false, stage 'Queue')
    would classify as "stuck_claim" — a hub-visible kind change on the most common branch
    there is. Pinned from both sides: test_claimable_cmd (this shape keeps kind "queue") and
    workflow.py's own comment at the free-queue return.
    task:null is the not-claimable family, where the additive discriminators cycle/
    starving distinguish a wedged board from a genuinely empty queue (all three are
    'don't launch an agent', but they are NOT the same thing to a human).

    ⚠ ADDING A KIND HERE IS A BREAKING CHANGE — AND ITS ROLLOUT ORDER IS INVERTED.
    The hgdev-acp hub validates `kind` against a CLOSED enum of exactly these seven
    (internal/hub/vikunja/claimable.go `kindIsClaimable`) and fail-CLOSES on anything
    it does not know — deliberately, because a hub that silently accepted an unknown
    verdict could idle its loops forever, invisibly. Every push to main here
    auto-releases and moves `stable`, and the hub re-resolves `@stable` on EVERY
    check — so a new kind reaches every hub within MINUTES and turns all its loops red
    (loud idle-check rows, no launches) until the hub's enum learns it. Ship the hub's
    enum FIRST, then this. (Renaming a KEY is the same class, ordered the usual way:
    the hub reads them, so it must learn the new shape first too.)"""
    task = result.get("task")
    if task is not None:
        if result.get("review"):
            kind = "review"
        elif result.get("resume"):
            kind = "stuck_claim" if result.get("stage") == "Queue" else "resume"
        else:
            kind = "queue"
        return {"claimable": True, "kind": kind, "task_id": task["id"]}
    if result.get("cycle"):
        kind = "cycle"
    elif result.get("starving"):
        kind = "starving"
    else:
        kind = "empty"
    return {"claimable": False, "kind": kind, "task_id": None}


def run_claimable() -> int:
    """The CLI body: print the verdict line, return the exit code."""
    trail = _Trail(enabled=trace_enabled())
    # The two closing tokens below sit in their branches instead of in one `finally`: `finally`
    # ALSO runs on KeyboardInterrupt (verified by running it), so an INTERRUPTED check would
    # sign off with a terminating token AND the newline — telling the reader this trail exists
    # for that the run completed. (A hub wedge-kill is SIGKILL — verified in the consumer:
    # exec.CommandContext cancels with Process.Kill and its own comment expects "signal:
    # killed" — where no Python cleanup runs at all, so there nothing is printed either way.)
    try:
        cfg = load_config()
        trail.note(f"cfg/{cfg.project_id}")
        # event_hooks is api.py's own pass-through to httpx (the same door `workspace --gc`
        # hangs its read deadline on), so the trail needs no new surface there at all. It is
        # ignored when a `client` is supplied — which is why the end-to-end trail test patches
        # httpx.Client's transport instead of handing VikunjaAPI a prebuilt client.
        wf = Workflow(
            VikunjaAPI(cfg.url, cfg.token, event_hooks={"request": [trail]}), cfg.project_id,
            enforce_single_wip=cfg.enforce_single_wip,
            wip_limit=cfg.wip_limit,
            # THE CONSTRUCTION SITE IS PART OF THE NO-DRIFT PROPERTY (VMCP-295, 1169). This
            # kwarg was absent here from the day the flag existed (#37), on a justification that
            # was TRUE then — `review_task` was its only reader — and that quietly stopped being
            # true at #991, which made next_task's already-present, until-then UNCONDITIONAL
            # authorship skip conditional on the flag. Nobody revisited the sentence, so the
            # drift ran from #991 to this card. Measured on an identical FakeAPI board (one card
            # driven claim -> build -> review by ONE identity), `classify_next(wf.next_task())`
            # answered {"claimable": true, "kind": "review"} with the flag false and
            # {"claimable": false, "kind": "empty"} with it true: the two sides DISAGREED, and
            # on that board the disagreement is the $105/day no-op-boot shape above, narrowed to
            # repos that set the flag. On a board with free Queue work it is instead a WRONG
            # `kind`/`task_id` on a hub run row — the constant is the disagreement, not any one
            # shape. Wiring it is not a cost either, about the GUARD: next_task resolves `my_id`
            # whatever the flag says, so no request is added, and when the guard FIRES it skips
            # that card's `comments()` fetch. The TOTAL is the board's business — measured, a
            # board of gated Queue cards costs 7 calls with the flag on against 2 with it off.
            # Pinned end-to-end (a classifier unit test cannot see a construction site) by
            # test_claimable_cmd.test_the_toml_review_independence_flag_reaches_the_exported_verdict
            # and its flag-absent control. `notify_webhook` -> `notifier` is the one Config key
            # `Workflow` takes that is still deliberately absent here: `call_human` alone touches
            # the notifier, and this path calls `next_task` alone.
            # THAT SET IS NOW MECHANICAL rather than a sentence: #1179 added a key here and not
            # there, and this accounting — which counts DELIBERATE absences, so an accidental
            # one leaves it true and misleading rather than false — stopped describing the tree
            # the same day. So
            # tests/unit/test_workflow_construction_parity.py compares the keyword names at the
            # two sites and treats its own `_DECLARED_ABSENCES` as the WHOLE of the permitted
            # difference — in both directions, and with a stale declaration a red of its own.
            require_review_independence=cfg.require_review_independence,
            # `language` is emitted by next_task itself — the one call this command makes — so
            # without this line the payload would report a `ru` project as `en`. classify_next
            # reduces it to the three-key hub contract and never reads it, so the VERDICT cannot
            # change either way; this is about the payload not lying. (#1165 first reached for
            # the contrast "unlike `require_review_independence`, which is deliberately dead
            # here" and its own second pass refuted it; that retracted wording, and the
            # measurement that killed it, are kept verbatim in docs/dossier/config.md.)
            language=cfg.language,
            # `siblings` rides in that same next_task payload, so omitting it would report a
            # repo that HAS neighbours as having none — the `language` case exactly, wired for
            # the same "the payload must not lie" reason and for the construction-site rule
            # above. It cannot move the VERDICT: measured, `classify_next(wf.next_task())`
            # compared EQUAL with the registry populated and with it empty, on a free-Queue
            # board and on one gated by a cross-project blocker. Nor is it #1169 returning —
            # the cross-project predecessor gate resolves a blocker off THAT task's own
            # `project_id`, never off the registry, so the gate reads the same either way.
            siblings=cfg.siblings,
        )
        verdict = classify_next(wf.next_task())
    except Exception as e:  # noqa: BLE001 — a CLI check: ANY failure is exit 1, never a crash
        # A FAILED check must never be mistaken for a clean "no work" (that would idle the
        # hub's loop on a broken tracker forever), so it takes the OTHER exit code and its
        # own key. Token-free by the same discipline as the server's errors: ConfigError
        # names files and env VAR NAMES, VikunjaError carries the server's body, httpx errors
        # carry the URL — never the Authorization header. The hub logs this line verbatim.
        print(json.dumps({"error": f"{e.__class__.__name__}: {e}"}))
        trail.close(f"fail/{trail.requests}@{trail.elapsed()}")
        return 1
    print(json.dumps(verdict))
    trail.close(f"end/{trail.requests}@{trail.elapsed()}")
    return 0
