"""The construction-site rule, made mechanical — tracker #1179.

WHAT WAS BROKEN WITHOUT THIS FILE, and it is not a bug report but a pattern one. `claimable`'s
whole stated property is a verdict with ZERO drift from the agent's own, and that property is
carried by CONSTRUCTION as much as by the call: both paths run the real `Workflow.next_task()`,
so a `Config` key wired at one `Workflow(...)` and not the other IS the drift. #1169 landed that
rule after `require_review_independence` — absent here from the day the flag existed, on a
justification that was true then — became a live divergence at #991, when `next_task` started
reading the flag, and stayed one silently until that card. The rule was then written down in
four places (CLAUDE.md, both call sites, and the dossier) and enforced by NOTHING. #1179 is the
second instance of the same class in two consecutive cards: it added `siblings`, wired it at
`server._build_workflow` and not at `claimable_cmd.run_claimable`, and left both sites' explicit
accountings of their asymmetries naming exactly ONE while TWO keys were absent. Neither
sentence went false — both quantify over legitimate/deliberate absences, and an accidental
omission is neither — which is precisely the trouble: prose that enumerates a set does not
announce that it has stopped describing the tree. A set comparison does.

WHAT THIS FILE ENFORCES. One shape: the keyword names at `claimable_cmd`'s `Workflow(...)` equal
the keyword names at `server`'s, minus a small set declared right here with a reason each. Both
directions, because a kwarg present only on the CHEAP side is the same defect mirrored — a key
the server never passes reaches `next_task` on one path alone, which is drift whichever side
carries it. The exception set is itself a ratchet: a name in it that is no longer an asymmetry is
a red, so wiring `notifier` here later cannot leave a stale sentence behind, which is the exact
failure mode this card is about.

WHY `workspace_cmd` IS DELIBERATELY OUT OF SCOPE — do not "fix" this gate by widening it to the
third site. `workspace_cmd._build_workflow` builds `Workflow(api, cfg.project_id)` and wires NO
Config keys at all, on purpose: `--gc` uses it for `liveness_board` / `active_task_ids` /
`review_task_ids` / `parked_task_ids`, none of which reads a keyword setting, and it never calls
`next_task`. So it has no config keys to AGREE about, and a parity rule applied to it would
demand wiring that is dead by construction. That is a fact about what `--gc` happens to call
rather than a guarantee (docs/dossier/claimable.md says so in as many words), so the day that
site starts calling `next_task` it joins the comparison — by a human's decision, recorded here.

WHAT IT CANNOT ENFORCE, priced rather than rounded up. It reads keyword NAMES off the syntax
tree, so it is blind to VALUES: `siblings=cfg.language` at one site would sail through, and so
would a key wired from a hand-rolled literal instead of the `Config`. It is blind to a key that
is absent from BOTH sites while `Workflow` reads it on the path — the #1169 shape one level up,
which needs a reader of `workflow.py`, not of these two calls. And it says nothing about the API
object handed in as the first positional: the two differ legitimately (this path hangs the
stderr trail on `event_hooks`), so only the ARITY and the `project_id` positional are checked.

MUTATION SWEEP, selection `tests/unit/test_workflow_construction_parity.py
tests/unit/test_claimable_cmd.py`, `__pycache__` deleted and PYTHONDONTWRITEBYTECODE=1 before
every round, `vikunja_mcp.__file__` printed in every round and inside this clone every time,
every restore verified back to a recorded baseline before the next round began, rounds read by
COUNTING lines that open `FAILED ` and, separately, `ERROR `, never by pytest's summary line,
and every round collected the same 58 items as the control.
* CONTROL, unmutated: control 0 failed, 0 errors, 58 collected. A second control after the last
  restore: 0 failed, 0 errors, 58 collected.
* `siblings=cfg.siblings` deleted from `claimable_cmd`'s call — the #1179 defect itself: control
  0 failed; mutation 1 failed, 0 errors, 58 collected, the FAILED line naming
  test_claimable_cmd_wires_every_config_key_the_server_wires. NOTHING ELSE MOVED, and that is
  the measurement this file rests on: the shipped suite cannot see a construction-site kwarg
  whose absence the exported verdict does not depend on.
* `language=cfg.language` deleted from the same call: control 0 failed; mutation 1 failed, 0
  errors, 58 collected, again this gate ALONE. A second key, the same single kill — the rule is
  what is pinned, not the one key that motivated the card.
* `require_review_independence` deleted, i.e. #1169 re-enacted: control 0 failed; mutation 2
  failed, 0 errors, 58 collected — this gate AND #1169's own end-to-end pin
  (test_the_toml_review_independence_flag_reaches_the_exported_verdict). That round is here to
  show the selection CAN move by more than one, so the single kills above are a fact about the
  suite rather than about a blind selection.
* `siblings` deleted from the SERVER instead, leaving the key on the cheap side only: control 0
  failed; mutation 1 failed, 0 errors, 58 collected. The comparison is symmetric.
* A kwarg the server never passes added to `claimable_cmd`: control 0 failed; mutation 11
  failed, 0 errors, 58 collected — this gate plus ten runtime kills, since a name `Workflow`
  does not take raises on the spot. Read that 11 as ten tests catching a BROKEN CALL and one
  catching the drift; the ten say nothing about a bogus name that `Workflow` would accept.
* `notifier` renamed out of `_DECLARED_ABSENCES`, the undeclared-asymmetry case: control 0
  failed; mutation 1 failed, 0 errors, 58 collected.
* `notifier=notifier` deleted from the SERVER call, so the declaration stops describing the
  tree: control 0 failed; mutation 1 failed, 0 errors, 58 collected — the ratchet half, which is
  what keeps this file from becoming the stale sentence it replaces.
* THE OUT-OF-SCOPE CONTROL, which had to stay GREEN: `workspace_cmd`'s
  `Workflow(api, cfg.project_id)` given a `wip_limit=cfg.wip_limit` it does not need: control 0
  failed; mutation 0 failed, 0 errors, 58 collected. The third site is outside the comparison,
  and this round is what says so mechanically instead of in prose.
"""
import ast
import pathlib

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

# The TWO sites the rule is about, by repo-relative path — read as TEXT, never imported. Importing
# would give back the CALL's effect (a `Workflow` instance) and this gate is about the call's
# SHAPE, which no runtime object remembers.
SERVER = "src/vikunja_mcp/server.py"
CLAIMABLE = "src/vikunja_mcp/claimable_cmd.py"

# The declared asymmetries, and the ONLY way a key legitimately sits at one site and not the
# other. Adding a name here is a decision with a sentence attached, and removing one is checked
# too — see the ratchet test, which refuses a declaration that has stopped describing the tree.
_DECLARED_ABSENCES = {
    "notifier": (
        "`call_human` alone touches the notifier and this path calls `next_task` alone, "
        "so no board state can reach it (docs/dossier/claimable.md)"
    ),
}

# What a reader is told to do about a red, in the message itself. The failure TEXT is most of this
# file's value: a bare set-inequality assert tells an agent that two sets differ and leaves the
# decision — wire it, or declare it — to be rediscovered from four prose sites, which is the
# rediscovery that did not happen at #1179.
_REMEDIES = (
    "Two ways out, and they are a decision rather than a coin flip: (1) wire the kwarg at the "
    "missing site, which is right whenever the path can READ it; or (2) name it in "
    "_DECLARED_ABSENCES in tests/unit/test_workflow_construction_parity.py with the reason it "
    "can never be read there. Silence is not one of them."
)


def _source(relative: str) -> str:
    return (REPO_ROOT / relative).read_text(encoding="utf-8")


def _workflow_call(source: str, label: str) -> ast.Call:
    """THE call under test, or a LOUD failure. A gate that stops finding its subject must go RED,
    never quietly green: renaming the class, aliasing the import or growing a second construction
    site all land here rather than in a silently empty comparison."""
    calls = [
        node for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "Workflow"
    ]
    assert len(calls) == 1, (
        f"{label}: expected EXACTLY ONE `Workflow(...)` call, found {len(calls)}. This gate "
        f"compares the two construction sites of the same class, so it cannot guess which call "
        f"it is about. If the site moved or split, teach this file where it went; if the class "
        f"is now built through an alias or a factory, that is a change to the rule, not to the "
        f"gate. Do not delete the assertion to get green."
    )
    return calls[0]


def _keywords(call: ast.Call, label: str) -> frozenset:
    """The keyword NAMES at one site. A `**kwargs` unpacking is refused rather than skipped: it
    hides the very set this gate compares, so accepting it would turn the check off silently."""
    starred = [kw for kw in call.keywords if kw.arg is None]
    assert not starred, (
        f"{label}: `Workflow(...)` is called with a `**` unpacking. The keyword set is then not "
        f"readable from the syntax tree, and a gate that cannot read its subject must not pass. "
        f"Spell the keywords out at both sites, or replace this gate with one that can see "
        f"through the mapping."
    )
    return frozenset(kw.arg for kw in call.keywords)


def _parity_complaints(server_kw, claimable_kw, absences) -> list:
    """Every way the two sites disagree, each as a finished sentence naming the key. Factored out
    of the gate so the MESSAGE can be driven directly by the tests below — the text is the
    deliverable here, and a message nothing exercises rots exactly like prose."""
    complaints = []
    for key in sorted(server_kw - claimable_kw - set(absences)):
        complaints.append(
            f"`{key}` is passed to Workflow() in {SERVER} but NOT in {CLAIMABLE}. Both paths run "
            f"the real next_task(), so a Config key wired on one side only is precisely the "
            f"drift `claimable` promises not to have (#1169, #1179). {_REMEDIES}"
        )
    for key in sorted(claimable_kw - server_kw):
        complaints.append(
            f"`{key}` is passed to Workflow() in {CLAIMABLE} but NOT in {SERVER}. The rule is "
            f"symmetric: a key reaching next_task on the cheap path alone is the same drift "
            f"mirrored, and the exported verdict is the side the hub steers on. {_REMEDIES}"
        )
    for key in sorted(set(absences) - (server_kw - claimable_kw)):
        complaints.append(
            f"`{key}` is named in _DECLARED_ABSENCES but is no longer an asymmetry between "
            f"{SERVER} and {CLAIMABLE}. A declaration that has stopped describing the tree is "
            f"the #1179 defect itself, one level up: delete the entry in the same commit that "
            f"made it false."
        )
    return complaints


def test_claimable_cmd_wires_every_config_key_the_server_wires():
    """THE gate. Set equality between the two construction sites, minus the declared exceptions —
    in both directions, and with the exception list ratcheted."""
    server_kw = _keywords(_workflow_call(_source(SERVER), SERVER), SERVER)
    claimable_kw = _keywords(_workflow_call(_source(CLAIMABLE), CLAIMABLE), CLAIMABLE)
    complaints = _parity_complaints(server_kw, claimable_kw, _DECLARED_ABSENCES)
    assert not complaints, "\n\n".join(complaints)


def test_both_sites_agree_on_their_positional_shape():
    """The two positionals, checked because they carry the two arguments a keyword set cannot see:
    the API object and the project. Only the SHAPE is asserted — two positionals, the second an
    attribute named `project_id` — and that is deliberate rather than shy. The API objects differ
    legitimately (this path hangs the stderr trail on `event_hooks`), so comparing them would pin
    a difference the design wants; #992's guard is about `cfg.project_id` reaching the second
    slot, and an attribute of that name is exactly what it asks for."""
    for relative in (SERVER, CLAIMABLE):
        call = _workflow_call(_source(relative), relative)
        assert len(call.args) == 2, (
            f"{relative}: Workflow() takes the api object and the project id positionally; "
            f"found {len(call.args)} positional argument(s). If the signature moved, move this "
            f"gate with it."
        )
        api_arg, project_arg = call.args
        assert isinstance(api_arg, ast.Call) and isinstance(api_arg.func, ast.Name), (
            f"{relative}: the first positional is no longer a constructor call. Both sites build "
            f"their own client — that is where the trail's event_hooks live — so a shared or "
            f"cached one is a change worth a human's attention."
        )
        assert isinstance(project_arg, ast.Attribute) and project_arg.attr == "project_id", (
            f"{relative}: the second positional is not an attribute named `project_id`. "
            f"Workflow's #992 guard exists because a whole Config was passed here once and "
            f"surfaced two layers down as a 404; keep passing cfg.project_id."
        )


def test_the_finder_goes_RED_when_it_cannot_find_exactly_one_call():
    """A gate whose subject vanished must fail, not pass on an empty set. Constructed sources
    rather than the real files, because the failure being pinned is one the tree does not have."""
    for source, found in (("x = 1\n", 0), ("Workflow(a, b)\nWorkflow(c, d)\n", 2)):
        with pytest.raises(AssertionError) as excinfo:
            _workflow_call(source, "a constructed stand")
        assert f"found {found}" in str(excinfo.value)
    # and an aliased/attribute call is NOT counted, which is the same red by a different route:
    # `mod.Workflow(...)` is an ast.Attribute, so the finder sees zero and says so.
    with pytest.raises(AssertionError) as excinfo:
        _workflow_call("mod.Workflow(a, b)\n", "a constructed stand")
    assert "found 0" in str(excinfo.value)


def test_a_star_unpacking_is_refused_rather_than_silently_skipped():
    """`Workflow(api, pid, **kw)` would leave the comparison reading whatever is left, and pass."""
    call = _workflow_call("Workflow(a, b, wip_limit=1, **kw)\n", "a constructed stand")
    with pytest.raises(AssertionError) as excinfo:
        _keywords(call, "a constructed stand")
    assert "unpacking" in str(excinfo.value)


def test_the_failure_message_names_the_key_and_both_remedies():
    """The whole value of this file is the sentence a reader gets, so it is asserted rather than
    hoped for. Driven through the comparison helper on constructed sets — the real tree is
    parity-clean, which is exactly why the message needs its own stand."""
    missing = _parity_complaints({"language", "wip_limit"}, {"wip_limit"}, {})
    assert len(missing) == 1
    assert "`language`" in missing[0]
    assert SERVER in missing[0] and CLAIMABLE in missing[0]
    assert "_DECLARED_ABSENCES" in missing[0]

    extra = _parity_complaints({"wip_limit"}, {"wip_limit", "language"}, {})
    assert len(extra) == 1 and "`language`" in extra[0] and "symmetric" in extra[0]

    stale = _parity_complaints({"wip_limit"}, {"wip_limit"}, {"notifier": "a reason"})
    assert len(stale) == 1 and "`notifier`" in stale[0] and "_DECLARED_ABSENCES" in stale[0]

    assert _parity_complaints({"a", "b"}, {"a"}, {"b": "declared"}) == []


def test_every_declared_absence_carries_a_reason():
    """A one-word exception list is how a ratchet becomes a shrug. Each entry states why the key
    can NEVER be read on the claimable path — the shape of justification #1169 found had gone
    stale, so the bar is a sentence, not a name."""
    for key, reason in _DECLARED_ABSENCES.items():
        assert isinstance(reason, str) and len(reason.split()) >= 8, (
            f"_DECLARED_ABSENCES[{key!r}] needs a reason a later reader can re-check, not a "
            f"placeholder. Say what makes the key unreadable on the claimable path."
        )
