"""A label spelled the way a HUMAN typed it — `Bug`, `Blocked`, `Epic` — and the gates that read
it (#1256).

THE DISAGREEMENT, in one line: `api.get_or_create_label` has always resolved a label title case-
and whitespace-INSENSITIVELY (on purpose — a bot typing `Bug`/`bug ` once forked a duplicate
label, real incident 2026-07-08), while `Workflow._has_label` asked `lb["title"] == title`,
EXACT. So a label a human typed capitalised in the web UI EXISTED as far as every WRITE in this
package was concerned and DID NOT EXIST as far as every GATE reading it was concerned. #1216
closed exactly one instance (the guard inside `_add_label`, re-keyed to the resolved label ID);
this module is the class — thirteen `_has_label` call sites plus `_remove_label`'s own
comparison, all now routed through the single `api.label_key`. (`_remove_label` is not adjacent to
`_has_label`, whatever the pairing suggests: `_add_label` and its docstring sit between them.)

REPRODUCED BEFORE ANYTHING CHANGED, on a live `Workflow` over `FakeAPI`, agent tools only, one
variable per pair — the SPELLING — and each variant against its lowercase control:
  - `advance(to='review')` with NO `root_cause`: `bug` REFUSED (the #718 gate);
    `Bug`/`BUG`/`bug ` ADVANCED, and the payload said `review_kind='change'`;
  - `next_task` over a free Queue card: `blocked` withheld; `Blocked`/`BLOCKED`/`blocked `
    OFFERED;
  - `decompose` on a board that already held an `Epic` label, NOBODY typing anything: the
    container it creates carries title `Epic` (its own write resolves to that row), after which
    `claim(container)` is ACCEPTED (control: REFUSED, "is an epic CONTAINER") and `next_task`
    OFFERS it (control: False);
  - a card a human hand-dragged Review -> Build still wearing `Reviewed`: `advance(to='review')`
    left the badge ON (control `reviewed`: cleared), i.e. a stale APPROVE riding into a fresh
    Review — the exact state `_clear_verdict_labels`' docstring forbids.

THE THIRD OF THOSE IS WHY THE FIX IS NOT PER-SITE. The card that filed this guessed the `epic`
family was the mild one — its scope note reasons that an epic container is created by `decompose`,
which writes the label itself, so a human variant there is far less likely than on `bug`/`blocked`,
which humans do type by hand. `decompose` writing it is the DEFECT: it writes through
`_add_label` ->
`get_or_create_label('epic')`, which RESOLVES to whatever `Epic` the board already holds. The
package's own write path manufactures the disagreement, so no site is safe by construction.

THE DIRECTION THAT STRANDS WORK HAS ITS OWN PIN, deliberately, rather than riding along on the
`bug` ones. Every other test here reads "a gate that should fire now fires"; the `blocked` and
`epic` ones read "a card that used to be handed out is now WITHHELD". That is the fix, and it is
also the direction in which work goes silently missing — a Queue card nobody is offered is a card
nobody notices.

THE SWEEP. Selection every round is FIVE files — this file + `test_workflow_duplicate_label.py` +
`test_workflow_gates.py` + `test_workflow_epic_marker.py` + `test_api_labels.py` — run in a clone
with `__pycache__` deleted and `PYTHONDONTWRITEBYTECODE=1`, `vikunja_mcp.__file__` printed each
round, rounds read by COUNTING lines beginning `FAILED ` with lines beginning `ERROR ` counted
separately, and `collected` cross-checked against the control's. THE FIFTH FILE IS NOT OPTIONAL
AND THIS SENTENCE USED TO OMIT IT: measured, the four-file selection collects 152 and the
five-file one 154, while every row of the table cross-checks on 154 — so a reader following the
shorter list got a `collected` that could never agree with the table. What that cross-check
actually establishes is narrower than "one tree": it says round and control COLLECTED THE SAME
SELECTION, i.e. the mutation moved no test in or out. Same-tree is carried by the other two steps
beside it, deleting `__pycache__` and printing `vikunja_mcp.__file__`. Why `test_api_labels.py` is
in the selection at all is in the table's own row for the real client.

The table lives in `test_the_sweep_is_recorded`'s docstring at the bottom of this file, so that
every round sits in the same paragraph as the control it is a delta against.
"""
import pytest

from tests.unit.fakes import FakeAPI
from vikunja_mcp.api import label_key
from vikunja_mcp.workflow import (
    LABEL_BLOCKED,
    LABEL_BUG,
    LABEL_EPIC,
    LABEL_EPIC_READY,
    LABEL_REVIEWED,
    LABEL_REVIEW_FAILED,
    STAGES,
    Workflow,
    WorkflowError,
)

# every spelling a human plausibly types, against the canonical one. `bug ` is in here because
# `get_or_create_label` strips as well as folds, so whitespace is the second axis and a fix that
# only lowercased would pass the first three and fail this one.
VARIANTS = ("Bug", "BUG", "bug ", " Bug")


def _hand_label(api, task_id, title):
    """A HUMAN's hand puts the label on, through the same `add_label` the code under test uses —
    so the fixture is subject to the same refusals (a duplicate is a 400 here since #1216)."""
    api.add_label(task_id, api.get_or_create_label(title)["id"])


def _titles(api, task_id):
    return [lb["title"] for lb in api.tasks[task_id]["labels"]]


def _bug_stand(spelling):
    api = FakeAPI(buckets=STAGES)
    wf = Workflow(api, project_id=3)
    t = api.add_task("a bug fix", "Build", assignee=api.me_user)
    _hand_label(api, t["id"], spelling)
    return api, wf, t


def _queue_stand(spelling):
    api = FakeAPI(buckets=STAGES)
    wf = Workflow(api, project_id=3)
    t = api.add_task("parked work", "Queue")        # free and unassigned: offerable but for this
    _hand_label(api, t["id"], spelling)
    return api, wf, t


@pytest.mark.parametrize("spelling", VARIANTS)
def test_a_variant_bug_label_still_gates_root_cause(spelling):
    """THE #718 GATE. A card a human labelled `Bug` used to advance to Review with no cause at
    all — the very state #718 exists to make impossible, and it failed OPEN, so nothing said so.

    Asserted against its own control in the same test, because "REFUSED" means nothing without
    "and the lowercase one is refused too": the gate could be refusing for an unrelated reason."""
    for title in (LABEL_BUG, spelling):
        api, wf, t = _bug_stand(title)
        with pytest.raises(WorkflowError) as exc:
            wf.advance(t["id"], to="review", worklog="did it", evidence="deadbeef")
        assert "root_cause" in str(exc.value), (
            f"a card labelled {title!r} must be refused for the CAUSE, not for something else"
        )
    # and it is a gate, not a wall: with the cause supplied the same card goes through
    api, wf, t = _bug_stand(spelling)
    assert wf.advance(
        t["id"], to="review", worklog="did it", evidence="deadbeef", root_cause="why",
    )["moved_to"] == "Review"


@pytest.mark.parametrize("spelling", VARIANTS)
def test_a_variant_bug_label_still_sets_the_bug_review_rubric(spelling):
    """`review_kind` is the reviewer's RUBRIC ('bug' — reproduce and confirm the cause is closed).
    It is computed by the same expression that guards `root_cause`, deliberately, so a variant
    used to mis-set it in the SAME payload that let the card through with no cause: measured
    `review_kind='change'` on a card labelled `Bug`."""
    api, wf, t = _bug_stand(spelling)
    pushed = wf.advance(
        t["id"], to="review", worklog="did it", evidence="deadbeef", root_cause="why",
    )
    assert pushed["review_needed"] is True
    assert pushed["review_kind"] == "bug"

    # the PULL side of the offering computes it separately (next_task, per card) — same answer
    api.add_comment(t["id"], "[worklog] the report")
    offered = Workflow(api, project_id=3).next_task()
    assert offered["review"] is True and offered["review_kind"] == "bug"


@pytest.mark.parametrize("spelling", ("Blocked", "BLOCKED", "blocked ", " Blocked"))
def test_a_variant_blocked_label_keeps_the_card_out_of_the_offering(spelling):
    """THE DIRECTION THAT STRANDS WORK, pinned on purpose.

    `blocked` is how a human PARKS a Queue card — `next_task`'s free-queue branch drops a
    `blocked`-labelled card outright — and a card labelled `Blocked` was handed out anyway:
    measured OFFERED against a withheld lowercase control. Fixing that makes a gate FIRE where it
    did not, and for THIS label firing means a card disappearing from the pump's view: the failure
    mode of the fix is silent, so it gets an assertion of its own rather than being inferred from
    the `bug` pins.

    The control below is the load-bearing half — an empty offering proves nothing on its own
    (a stand with no claimable work at all looks identical), so the same board with the label
    REMOVED must hand the card back."""
    api, wf, t = _queue_stand(spelling)
    assert wf.next_task()["task"] is None, (
        f"a Queue card labelled {spelling!r} must NOT be offered"
    )

    # control: the same board, the same card, label gone -> it IS offered. Without this the
    # assertion above is satisfied by any broken next_task.
    api.tasks[t["id"]]["labels"] = []
    back = Workflow(api, project_id=3).next_task()
    assert back["task"] is not None and back["task"]["id"] == t["id"]


def test_a_variant_epic_container_is_unclaimable_and_unoffered():
    """THE SITE THE CARD EXPECTED TO BE MILD, and the measurement that says it is not.

    Nobody types anything here. The board merely already holds a label spelled `Epic` — the web
    UI mints labels freely — and `decompose`'s own `get_or_create_label('epic')` RESOLVES to that
    row, so the container this package just created carries the title `Epic`. Before #1256 every
    downstream `epic` gate then read False on it: `claim` ACCEPTED the container as if it were a
    unit of work, and `next_task` OFFERED it.

    Driven through the agent tools, with the pre-seeded label as the only difference from the
    lowercase world."""
    api = FakeAPI(buckets=STAGES)
    wf = Workflow(api, project_id=3)
    api.create_label("Epic")                      # minted earlier by a human in the web UI
    parent = api.add_task("big job", "Design", assignee=api.me_user)
    wf.decompose(parent["id"], [{"title": "part one"}, {"title": "part two"}])

    assert _titles(api, parent["id"]) == ["Epic"], (
        "premise: decompose's own write resolves to the board's existing row, so the container "
        "carries the HUMAN's spelling — nobody typed it"
    )

    # a human triages the container out of Backlog into Queue by hand (no tool does this)
    api.task_bucket[parent["id"]] = api.bucket_id("Queue")
    with pytest.raises(WorkflowError) as exc:
        wf.claim(parent["id"])
    assert "epic CONTAINER" in str(exc.value)

    # ...and the free-queue branch withholds it too. The children are parked out of the way so
    # that an offering of SOMETHING cannot be mistaken for an offering of the container.
    for tid in list(api.tasks):
        if tid != parent["id"]:
            api.task_bucket[tid] = api.bucket_id("Backlog")
    assert Workflow(api, project_id=3).next_task()["task"] is None


def test_a_variant_epic_container_is_not_nudged_for_review_and_is_not_offered_for_one():
    """The other two `epic` skips: `advance`'s push-nudge and `next_task`'s review offering. An
    epic container has no code of its own — its evidence lives in its children — so nudging one
    into independent review dispatches a reviewer at a card with nothing to read."""
    api = FakeAPI(buckets=STAGES)
    wf = Workflow(api, project_id=3)
    t = api.add_task("container", "Build", assignee=api.me_user)
    _hand_label(api, t["id"], "Epic")

    pushed = wf.advance(t["id"], to="review", worklog="w", evidence="e")
    assert "review_needed" not in pushed and "review_kind" not in pushed

    api.add_comment(t["id"], "[worklog] a report someone left on the container")
    assert Workflow(api, project_id=3).next_task()["task"] is None


def test_a_variant_epic_container_is_not_transferable():
    """`transfer_task`'s epic guard, same label read. Moving a container alone splits the set
    across two boards and leaves the children pointing at a parent nobody there can open."""
    api = FakeAPI(buckets=STAGES)
    neighbour = api.add_project("backend", buckets=STAGES)
    wf = Workflow(api, project_id=3, siblings={"backend": neighbour["id"]})
    t = api.add_task("container", "Build", assignee=api.me_user)
    _hand_label(api, t["id"], "Epic")
    with pytest.raises(WorkflowError) as exc:
        wf.transfer_task(t["id"], to="backend", reason="wrong board")
    assert "epic container" in str(exc.value)


@pytest.mark.parametrize("spelling", ("Reviewed", "REVIEWED", "reviewed "))
def test_a_variant_verdict_badge_is_cleared_on_the_way_back_into_review(spelling):
    """`_remove_label` is the SAME exact-title comparison as `_has_label` was, and it
    leaked the same way — measured, not inferred by symmetry: a card a human hand-dragged out of
    Review into Build still wearing `Reviewed` kept that badge through `advance(to='review')`,
    so a stale APPROVE rode into a fresh Review. `_clear_verdict_labels`' own docstring is about
    exactly that ("ложный бейдж всё равно не должен оставаться") — it was true of `reviewed` and
    false of `Reviewed`.

    Counted case-insensitively on purpose: counting exact titles would call a surviving
    `Reviewed` an absence and pass."""
    api = FakeAPI(buckets=STAGES)
    wf = Workflow(api, project_id=3)
    t = api.add_task("job", "Build", assignee=api.me_user)
    _hand_label(api, t["id"], spelling)

    wf.advance(t["id"], to="review", worklog="w", evidence="e")

    left = [label_key(x) for x in _titles(api, t["id"])]
    assert LABEL_REVIEWED not in left, (
        f"the stale badge {spelling!r} survived the transition back into Review"
    )


def test_the_read_and_the_write_agree_for_every_variant_of_every_label():
    """THE INVARIANT ITSELF, rather than an instance of it: for every label this package knows and
    every spelling of it, `_has_label` (the READ) answers exactly what `get_or_create_label` (the
    WRITE) resolves to. That equality is what #1256 is; the pins above are the consequences a
    reader can act on, this one is the property that generates them.

    Both sides are driven, not asserted about: the card is labelled through `add_label` with what
    `get_or_create_label` returns, so if the two ever disagree again this goes red no matter which
    of them moved."""
    labels = (
        LABEL_BUG, LABEL_BLOCKED, LABEL_EPIC, LABEL_EPIC_READY,
        LABEL_REVIEWED, LABEL_REVIEW_FAILED,
    )
    for canonical in labels:
        for spelling in (
            canonical, canonical.upper(), canonical.capitalize(),
            f" {canonical}", f"{canonical} ", f"  {canonical.upper()}  ",
        ):
            api = FakeAPI(buckets=STAGES)
            t = api.add_task("card", "Build")
            _hand_label(api, t["id"], spelling)
            resolved = api.get_or_create_label(canonical)
            carried = any(lb["id"] == resolved["id"] for lb in api.tasks[t["id"]]["labels"])
            assert Workflow._has_label(api.tasks[t["id"]], canonical) is carried, (
                f"_has_label disagrees with get_or_create_label about {spelling!r} vs "
                f"{canonical!r} — that disagreement IS #1256"
            )

    # and the negative half, so the pin is not satisfied by a `_has_label` that says True to
    # everything: a label that is genuinely a different word stays a different label.
    api = FakeAPI(buckets=STAGES)
    t = api.add_task("card", "Build")
    _hand_label(api, t["id"], "bugbear")
    assert not Workflow._has_label(api.tasks[t["id"]], LABEL_BUG)


def test_a_label_title_is_read_only_through_label_key():
    """THE ANTI-DRIFT PIN. Read its scope before trusting it — an earlier draft of this docstring
    claimed to close the CLASS and did not, and #1256's own second independent pass proved it by
    construction: it added a `_label_matches(a, b)` helper spelled with `.strip().lower()` BESIDE
    the existing `label_key` calls, wired it into `_has_label`, and the test stayed GREEN with the
    rule stated twice. `.lower()` is not a hypothetical alternative spelling either — `workflow`
    already normalises `to` and `verdict` with it, so it is the idiom the next author reaches for.

    WHAT IT ASSERTS, in three parts, and only the first is a class:
      1. inside `_has_label` and `_remove_label`, a label title is read ONLY inside a
         `label_key(...)` call — so a second normalisation written INTO those two functions, in
         ANY spelling, reddens this;
      2. `.casefold()` appears nowhere in `workflow` and exactly once in `api`, inside
         `label_key` — narrower than (1), and what catches the copy being made SOMEWHERE ELSE in
         those modules and called from these two;
      3. `fakes.py` holds no copy of its own. That clause is here because the second pass found
         one still sitting there after the production side had been unified, and a fake that can
         drift on THIS rule is precisely what made the #1216 leak untestable.

    WHAT IT DOES NOT CATCH, stated so nobody reads a green run as more than it is: a helper
    spelled some third way (`str.translate`, a regex, `locale`) living in a module this test does
    not read, or sitting in `api` outside `label_key` while `_has_label` still routes through
    `label_key`.

    READ WITH `ast`, NOT WITH A SUBSTRING SEARCH: the first draft grepped the text and went red on
    its own subject matter, because `label_key`'s docstring says `.strip().casefold()` out loud in
    prose. A gate that fires on prose ABOUT the rule teaches the next person to stop writing the
    prose. (`workflow` says no such thing — it says "case- and whitespace-insensitively" and names
    `api.label_key`; `api` alone is enough to redden a grep.)"""
    import ast
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[2]
    src = root / "src" / "vikunja_mcp"

    def tree(path):
        return ast.parse(path.read_text(encoding="utf-8"))

    def casefold_calls(node) -> list[int]:
        return sorted(
            n.lineno for n in ast.walk(node)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr == "casefold"
        )

    wf_tree = tree(src / "workflow.py")
    api_tree = tree(src / "api.py")
    fake_tree = tree(root / "tests" / "unit" / "fakes.py")

    # (1) the class: in the two readers, a title is only ever touched inside label_key(...)
    for name in ("_has_label", "_remove_label"):
        fn = next(
            n for n in ast.walk(wf_tree)
            if isinstance(n, ast.FunctionDef) and n.name == name
        )
        inside = set()
        for n in ast.walk(fn):
            if (isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                    and n.func.id == "label_key"):
                inside.update(id(c) for c in ast.walk(n))
        stray = [
            n.lineno for n in ast.walk(fn)
            if isinstance(n, ast.Constant) and n.value == "title" and id(n) not in inside
        ]
        assert not stray, (
            f"{name} reads a label title outside label_key() at line(s) {stray} — that is a "
            f"second statement of the resolution rule, which is what #1256 is about"
        )

    # (2) .casefold() lives in exactly one place, and that place is label_key
    assert casefold_calls(wf_tree) == [], (
        "workflow.py normalises a title itself; route it through api.label_key instead"
    )
    key_fn = next(
        n for n in ast.walk(api_tree)
        if isinstance(n, ast.FunctionDef) and n.name == "label_key"
    )
    calls = casefold_calls(api_tree)
    assert len(calls) == 1, f"api.py states the label-title rule in {len(calls)} places: {calls}"
    assert key_fn.lineno <= calls[0] <= (key_fn.end_lineno or calls[0]), (
        "the one casefold in api.py is not inside label_key"
    )

    # (3) the fake borrows the rule instead of restating it
    assert casefold_calls(fake_tree) == [], (
        "tests/unit/fakes.py restates the label-title rule; call api.label_key instead — a fake "
        "that can drift on THIS rule is a fake that can hide THIS defect"
    )

    # and it is actually USED by the read path, not merely defined
    uses = [
        n for n in ast.walk(wf_tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "label_key"
    ]
    assert len(uses) >= 4, f"workflow.py calls label_key only {len(uses)} time(s)"


def test_the_sweep_is_recorded():
    """The mutation sweep for this module. Selection every round: this file +
    `test_workflow_duplicate_label.py` + `test_workflow_gates.py` + `test_workflow_epic_marker.py`
    + `test_api_labels.py`, in a fresh clone with `__pycache__` deleted and
    `PYTHONDONTWRITEBYTECODE=1`, `vikunja_mcp.__file__` printed each round and naming the CLONE,
    rounds read by COUNTING lines beginning `FAILED ` with lines beginning `ERROR ` counted
    separately, and `collected` cross-checked against the control's. **Control ran FIRST and LAST,
    both 0 failed / 0 errors / 154 collected**, and every round below reports that same 154
    collected and 0 errors — so each figure is a delta against 0, not against noise.

      * revert `_has_label` to `lb.get("title") == title` -> **19 failed** (control 0): all six
        consequence pins, the invariant pin, the anti-drift pin, and BOTH of #1216's variant pins
        in `test_workflow_duplicate_label.py` — their premises come back when this comes back
      * revert `_remove_label` to `x.get("title") == title` -> **4 failed** (control 0): the
        verdict-badge pin at all three spellings, plus the anti-drift pin
      * revert the REAL client's `get_or_create_label` to an exact match -> **1 failed** (control
        0): `test_get_or_create_label_reuses_case_insensitively`, which drives the real
        `VikunjaAPI` over MockTransport. That file is in the selection deliberately: without it
        this round reads 0, and 0 there says "the write half is pinned only by the integration
        suite", which is false
      * `label_key` -> `.lower()`, i.e. fold but do not strip -> **8 failed** (control 0): every
        whitespace-axis variant. A fix that only lowercased would pass the case pins and fail here
      * `label_key` -> `""`, i.e. every label matches every title -> **36 failed** (control 0).
        The direction the other rounds cannot see: a normalisation that merges too much
      * `FakeAPI.get_or_create_label` back to an exact match -> **3 failed** (control 0). It calls
        `api.label_key` now rather than restating it, so this round has to rewrite the method
      * key `_add_label`'s guard on the TITLE (via `_has_label`) instead of the resolved label ID
        — #1216's own sweep row, RE-MEASURED because #1256 moves it -> **0 failed** (control 0),
        where it was 2 at #1216. Re-run WIDE to be sure the 0 is not a selection artefact: 0
        failed against a clean control of 0 failed / 0 errors / 1399 collected over the whole of
        `tests/unit`. That guard is no longer pinned against THIS mutation — and the qualifier is
        load-bearing: a byte-exact `lb.get("title") == title` guard is a DIFFERENT mutation since
        this card, and it still kills 1 (the standalone variant pin) against a control of 0 failed
        / 0 errors / 131 collected on #1216's three-file selection. `_add_label`'s docstring
        carries both numbers and says why the guard is kept anyway
      * hand the epic-ready site the hollowed `parent` sub-dict — #1216's other row, also
        re-measured -> **7 failed** (control 0), against 1 at #1216. It kills more now because the
        hollowed dict makes the `epic` check itself miss, so the whole marker stops firing
      * add a SECOND spelling of the rule BESIDE `label_key` — `.strip().lower()` wired into
        `_has_label` alongside the existing calls, the mutation #1256's second independent pass
        used to show the first draft of the anti-drift pin closed nothing -> **1 failed**
        (control 0): the anti-drift pin, which read 0 against that mutation before it was widened.
        The one round in this table that is a delta against an EARLIER round rather than only
        against the control
    """
