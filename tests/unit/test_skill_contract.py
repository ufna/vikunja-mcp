"""The packaged SKILL.md ↔ workflow.py contract — a cheap mechanical net under the rulebook.

SKILL.md is the agent RULEBOOK, not documentation. Since #88 the server refreshes every
consumer's installed copy on start (sync_installed_artifacts), so it auto-propagates over the
moving `stable` branch with NO per-consumer pin, NO test, and NO review gate of its own. That
inverts the old silent-drift risk (#116): a rule naming a stage / label / marker / next_task
signal the tools no longer have would now reach every agent, everywhere, with nothing to catch
it. These tests pin the MECHANICAL subset of the contract — every code token the rulebook cites
must still resolve in workflow.py, and every real stage must be documented. They deliberately do
NOT check semantic correctness (whether a rule is right) — that is what independent review,
widened to every change in #117, is for; this is only the net that catches a cited token going
stale on either side. One test below also reads the repo's own CLAUDE.md, because the integration
retry ceiling is DERIVED in both files and two independent copies of one derivation are exactly
what drifts apart.
"""
import ast
import inspect
import os
import re
import shutil
import subprocess
import textwrap
import time
from importlib.resources import files
from pathlib import Path

import httpx
import pytest

from tests.unit.fakes import FakeAPI
from vikunja_mcp import config, notify, server, setup_cmd, workflow, workspace_cmd


def _skill_core() -> str:
    # the packaged CORE — the file `install-skill` copies and `sync_installed_artifacts` heals.
    # Kept separate from `_skill_text` because ONE pin below is about that file's identity with
    # its in-repo source, and a bundle could never satisfy it.
    return files("vikunja_mcp").joinpath("skills/tracker/SKILL.md").read_text(encoding="utf-8")


def _skill_references() -> list[tuple[str, str]]:
    """(name, text) for every packaged `references/*.md`, sorted so a bundle is deterministic."""
    directory = files("vikunja_mcp").joinpath("skills/tracker/references")
    return sorted(
        (entry.name, entry.read_text(encoding="utf-8"))
        for entry in directory.iterdir()
        if entry.name.endswith(".md")
    )


def _skill_text() -> str:
    """The WHOLE rulebook: the core plus every reference it routes to.

    The rulebook used to be one file and is now a core plus `references/*.md` — the core carries
    what an agent must do, the references carry the payload shapes and the measured reasons, and
    the core names the file to open at each phase. Every content pin in this module asks "does the
    rulebook still say X", and that question is about the rulebook, not about which of its files
    happens to hold the sentence today — so they read the bundle. The pins that are about WHERE a
    rule sits slice a named section out of this text, and those sections moved whole, so their
    anchors are unchanged.

    Deliberately NOT the in-repo copy: this reads what is PACKAGED, i.e. what actually ships in
    the wheel and self-heals onto consumers (#88).
    """
    return "\n".join([_skill_core(), *(text for _, text in _skill_references())])


def _reference(name: str) -> str:
    """One packaged `references/<name>` — the file that now OWNS a section the pins below slice.

    A section that moved out of the core whole is still sliced by its own heading; what changed is
    which file to slice it OUT of. Reading the named file rather than the bundle matters where the
    core kept a POINTER stub carrying the same heading (the browser one does): a search over the
    bundle would find the stub first and assert against a summary instead of the rule.
    """
    return files("vikunja_mcp").joinpath(f"skills/tracker/references/{name}").read_text(
        encoding="utf-8"
    )


def _workflow_src() -> str:
    return inspect.getsource(workflow)


SKILL_SOURCE_PATH = "src/vikunja_mcp/skills/tracker/SKILL.md"   # the copy the rulebook cites


def _calls_in(func) -> set[str]:
    """The plain names a function actually CALLS — parsed, not grepped.

    A substring pin cannot carry the premise below, and this is MEASURED, not feared: `main`
    names `_self_heal_installed_artifacts()` in an explanatory COMMENT as well as calling it, so
    deleting the call left `"_self_heal_installed_artifacts()" in getsource(main)` green; and the
    heal's own body IMPORTS `sync_installed_artifacts` on the line above the call, so replacing
    the call left that assertion green too. Both mutations are the drift the pin exists to catch,
    and both walked straight through it. An AST call-set survives neither."""
    tree = ast.parse(textwrap.dedent(inspect.getsource(func)))
    return {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }


def _freshness_section(text: str) -> str:
    """The section that tells an agent WHICH copy of this rulebook is authoritative.

    Scoped to its own section, like `_gc_section`: `install-skill` and the sync's opt-out env are
    named in the MANAGED header too, so a whole-file substring could not tell "the rule is still
    stated" from "the header still mentions the sync"."""
    start = text.find("\n## Which copy of these rules you are reading\n")
    assert start != -1, "SKILL.md no longer states which copy of itself is authoritative"
    end = text.find("\n## ", start + 1)
    assert end != -1, "the freshness section no longer ends where the next section begins"
    section = text[start:end]
    # `0 < len(...)` really is dead — VMCP-109 (579) is right about that half, and it is right at
    # every site of this idiom (30 today, not the 9 the card counted): both bounds are asserted
    # `!= -1` above and `end > start`, so the slice cannot be empty. But the card's conclusion —
    # that the whole assert is structurally dead and can go — is DISPROVED by measurement, and the
    # measurement is the reason this comment exists instead of a deletion. Control round 0 failed;
    # make this slicer return `text` itself and the round is **2 failed**, both of them THIS
    # assertion by its own message. So `< len(text)` is live protection against exactly the bug
    # the message names, and the round the card's premise invites — delete it, see green — proves
    # nothing at all: deleting an assert from a green tree is green whatever the assert did.
    assert 0 < len(section) < len(text), "the freshness slice is not a proper subset of SKILL.md"
    return section


def test_the_rulebook_says_which_copy_of_itself_is_authoritative():
    """VMCP-96 (552): the self-heal this whole module rests on fires ONCE — from `server.main`, at
    server start — and a session's server starts once. So the installed copy every agent reads is a
    SNAPSHOT taken before the session's first landing, while the repo copy moves with each one. On
    2026-07-30 eight tasks edited SKILL.md inside one session and every agent dispatched during it
    read the pre-session text; nothing broke only because the orchestrator's briefs happened to
    carry the current rules. The sharp case is not staleness but a wrong CONCLUSION: a task whose
    deliverable IS a SKILL.md edit cannot verify itself through the skill — it gets the old text
    back and reads "my edit did not take", correctly for what it can see and wrongly in fact.

    The fix is a rule, not a code path (a mid-session rewrite of `~/.claude` would be a write whose
    effect the running session cannot confirm; a per-call sync was rejected outright — filesystem
    writes on a stdio server's hot path for a problem that only bites during self-modification).
    So what needs a net is the rule's PREMISE and its REDIRECT TARGET, both of which are code facts
    that can drift out from under prose that self-heals onto every consumer with no review gate:

    1. Premise — the refresh is a server-START event. Move the sync anywhere else (per tool call, a
       timer) and the section's "this text does not move inside a session" becomes a lie shipped to
       everyone. Anchored on the CALL GRAPH (`_calls_in`, which see): the obvious substring form of
       this pin was written first and measured green through both mutations it claims to catch.
    2. Redirect target — the rule sends an agent to a PATH. If the skill source moves in the tree,
       an agent following the rule finds nothing and silently falls back to the stale copy it was
       told not to trust. Anchored on the file existing AND being byte-identical to the packaged
       rulebook, so the pin fails on a move and on a copy that stopped being the source.

    Deliberately NOT pinned: the wording of the self-verification and rollout bullets — prose is
    review's job (see this module's docstring); the slice above only holds the section itself open.

    MUTATION-CHECKED (`__pycache__` cleared between rounds, each run confirmed to select exactly 1
    test): control PASS; drop the heal call from `server.main` -> FAIL; make the heal call
    something other than `sync_installed_artifacts` -> FAIL; re-value `SKILL_SYNC_OPT_OUT_ENV` ->
    FAIL; delete the cited path from the section -> FAIL; rename the section heading so the slice
    cannot find it -> FAIL (loudly, with its own message). The first two rounds are why `_calls_in`
    exists: under the substring pin they came back GREEN."""
    text = _skill_text()
    section = _freshness_section(text)

    # 1. the premise: the installed copy really is refreshed at server START, once
    assert "_self_heal_installed_artifacts" in _calls_in(server.main), \
        "SKILL.md says the installed copy is refreshed at server start, but main no longer heals"
    assert "sync_installed_artifacts" in _calls_in(server._self_heal_installed_artifacts), \
        "the server's start-time heal no longer calls sync_installed_artifacts"
    assert setup_cmd.SKILL_SYNC_OPT_OUT_ENV in text, \
        "SKILL.md names a sync opt-out env var that setup_cmd no longer defines under that name"

    # 2. the redirect target: the path the rule sends agents to IS the packaged rulebook
    assert SKILL_SOURCE_PATH in section, \
        "the rule no longer names the in-repo source copy it redirects agents to"
    source = Path(__file__).resolve().parents[2] / SKILL_SOURCE_PATH
    assert source.is_file(), f"the rulebook redirects agents to {SKILL_SOURCE_PATH}, which is gone"
    # core against core, not against the bundle: `text` above is core + references, and this
    # assert is about the identity of the FILE agents are redirected to with the file that ships
    assert source.read_text(encoding="utf-8") == _skill_core(), \
        f"{SKILL_SOURCE_PATH} is no longer the source the packaged rulebook is built from"
    for name, packaged in _skill_references():
        beside = source.parent / "references" / name
        assert beside.is_file(), (
            f"references/{name} ships in the wheel but is missing beside {SKILL_SOURCE_PATH}, so "
            f"an agent redirected to the in-repo copy cannot reach a reference the packaged one has"
        )
        assert beside.read_text(encoding="utf-8") == packaged, (
            f"references/{name} differs between the in-repo copy and the packaged one — the "
            f"redirect above would hand an agent prose that is not what ships"
        )


def test_every_workflow_stage_is_documented_in_the_skill():
    """A stage rename in workflow.STAGES (e.g. #54 'Call to Human' → 'Your Call') must reach the
    rulebook: every real pipeline stage is named in the skill, so a code-only rename fails here."""
    text = _skill_text()
    for stage in workflow.STAGES:
        assert stage in text, f"stage {stage!r} (workflow.STAGES) is not documented in SKILL.md"


def test_board_labels_the_skill_names_match_the_workflow_constants():
    """The verdict/epic labels agents and humans act on are pinned to their code constants: change
    LABEL_REVIEWED's value and the skill (still naming the old label) fails until synced. LABEL_BUG
    / LABEL_BLOCKED are intentionally excluded — the skill surfaces those by behaviour (review_kind,
    return_task), not by their literal label name, so asserting them would be a false pin."""
    text = _skill_text()
    for const in (
        workflow.LABEL_EPIC, workflow.LABEL_EPIC_READY,
        workflow.LABEL_REVIEWED, workflow.LABEL_REVIEW_FAILED,
    ):
        assert const in text, f"label {const!r} is no longer named in SKILL.md"


def test_next_task_and_advance_signal_keys_are_grounded_in_the_code():
    """The result keys the orchestrator branches on — the #102/#105/#117 additions — must exist on
    BOTH sides. Rename one in workflow.py and the pump silently mis-branches, so the skill that
    tells it to key off the old name must move in lockstep. This is the exact drift #116 asked
    about: the hardcoded list here forces a code rename to drag both the test and the skill along."""
    text = _skill_text()
    src = _workflow_src()
    for key in (
        "review_needed", "review_kind",          # #117 — independent review of every change
        "starving", "waiting", "waiting_count",  # #102 — starving-tail signal
        "needs_retriage",                        # #102 — a chain head returned to Backlog
        "cycle", "cycle_tasks",                  # #105 — predecessor-cycle signal
        "resume",                                # active-task vs free-queue discriminator
    ):
        assert key in src, f"signal {key!r} is no longer produced by workflow.py"
        assert key in text, f"signal {key!r} is no longer documented in SKILL.md"


def test_comment_markers_the_skill_cites_are_still_emitted():
    """Grep-convention markers the skill points humans/agents at must still be the ones the code
    writes. Curated to the markers the skill shows in bracket form; the others the code emits
    ([claim]/[worklog]/[blocked]/[decompose]/[needs-human]) the skill doesn't cite verbatim, so
    they are out of this contract by design (add one here only once the skill starts citing it)."""
    text = _skill_text()
    src = _workflow_src()
    for marker in ("[review]", "[spec]", "[filed-by-agent]", "[handoff]", "[moved]"):
        assert marker in src, f"marker {marker!r} is no longer emitted by workflow.py"
        assert marker in text, f"marker {marker!r} is no longer cited in SKILL.md"


def test_attachment_upload_rule_names_the_tool_that_backs_it():
    """#137: the rulebook's 'attach a screenshot of visually-verifiable work' rule must name the
    tool that performs it, and that tool must still exist in workflow.py — so renaming attach_file
    drags the skill along (the same skill<->code net as the signal keys). The behaviour rule is
    worthless if it points at a tool the code no longer exposes."""
    assert "attach_file" in _workflow_src(), "workflow.py no longer defines attach_file"
    assert "attach_file" in _skill_text(), "SKILL.md no longer names the attach_file tool"


def test_the_parallel_drain_rules_cite_real_signals():
    """The parallel drain (wip_limit > 1) is the first feature where the rulebook tells the pump to
    BRANCH on a payload key AND to shell out to a CLI — and the rulebook reaches every consumer by
    itself (see this module's docstring), with no per-consumer pin and no review gate. So pin both
    directions of the three tokens the whole mode hangs off: named in SKILL.md, and still real in
    the code. `wip_saturated` is the "wait, don't idle" discriminator, `exclude` the
    caller-maintained liveness set next_task cannot infer, `wip_limit` the config key that turns
    the mode on at all.

    Round-1 review: the code-side anchors must be RENAME-SENSITIVE, or this pin is theatre. A bare
    `"exclude" in workflow_src` is satisfied by an unrelated comment ("parenttask is deliberately
    excluded"), and a bare `"wip_limit" in workflow_src` is satisfied by the method name
    `_effective_wip_limit` — while the thing SKILL.md actually cites, the repo-toml KEY, lives in
    config.py, which this test never read. Both would have stayed green through the very rename
    they claim to catch. Anchor each on the exact construct whose name the rulebook depends on:
    next_task's parameter, and config.py's lookup of the toml key.

    Round-2 review: `exclude` is anchored in BOTH modules. The pump does not call
    Workflow.next_task — it calls the MCP TOOL (server.py), so a rename there alone would leave a
    workflow-only pin green while every agent's `exclude=[…]` silently became an unknown kwarg."""
    text = _skill_text()
    src = _workflow_src()
    config_src = inspect.getsource(config)
    server_src = inspect.getsource(server)
    for token in ("wip_saturated", "exclude", "wip_limit"):
        assert token in text, f"{token!r} is not documented in SKILL.md"
    assert "wip_saturated" in src, "SKILL.md keys off wip_saturated but workflow.py stopped emitting it"
    assert "exclude: list[int]" in src, \
        "SKILL.md tells the pump to pass exclude=… but Workflow.next_task lost that parameter"
    assert "exclude: list[int]" in server_src, \
        "SKILL.md tells the pump to pass exclude=… but the next_task TOOL lost that parameter"
    assert 'repo.get("wip_limit")' in config_src, \
        "SKILL.md names wip_limit as the repo-config key but config.py no longer reads that key"


def test_the_language_rule_names_a_key_the_code_actually_emits_and_reads():
    """The `language` rule is the half of #1165 that no unit test can enforce, so pin its WIRING.

    The tool translates its own boilerplate; the spec, the worklog and the review report are the
    bulk of a card and only the RULE makes those follow the key. The rule reaches every consumer
    by itself over `stable`, with no per-consumer pin — so if the payload key were renamed, every
    agent everywhere would keep being told to read a key that is no longer sent, and nothing else
    would notice.

    Anchored RENAME-SENSITIVELY on both sides, following the drain pin above rather than grepping
    for the bare word: `language` is ordinary English and occurs all over SKILL.md's prose, so a
    bare containment check would be satisfied by a sentence about natural language. The code-side
    anchors are the assignment that puts the key in the payload and config.py's lookup of the
    repo-toml key — the two constructs the rule's two claims depend on.
    """
    text = _skill_text()
    assert 'result["language"] = self.language' in _workflow_src(), (
        "SKILL.md tells the agent to read `language` off the next_task response, but "
        "workflow.py no longer puts that key in the payload"
    )
    assert 'repo.get("language"' in inspect.getsource(config), (
        "SKILL.md says the human sets `language` in .vikunja-mcp.toml, but config.py no longer "
        "reads that key out of the toml"
    )
    assert "Write your card text in the language `next_task` names" in text, (
        "the rulebook no longer states the language rule. The tool translates its own "
        "boilerplate and nothing else; without this rule a card gets English boilerplate around "
        "a Russian spec, or the reverse"
    )
    assert "Nothing in brackets translates" in text, (
        "the rulebook no longer tells the agent to leave the markers alone. Two of them are "
        "matched with startswith() in next_task's review offering, so a translated bracket "
        "drops a card out of review silently"
    )


def _exclude_completeness_bullet(text: str) -> str:
    """The bullet that tells the pump what an INCOMPLETE `exclude` costs it (#527).

    Sliced to the bullet rather than matched over the whole file for the reason the sibling
    slices exist: `exclude` and `wip_saturated` are named a dozen times in this rulebook, so a
    whole-file substring could not tell "the rule is still stated" from "the words still occur
    somewhere". Deleting this bullet must fail the pin even though every word in it survives
    elsewhere."""
    start = text.find("\n- **A complete `exclude` is also the VISIBILITY of signals")
    assert start != -1, \
        "SKILL.md no longer tells the pump what an incomplete `exclude` costs it (#527)"
    end = text.find("\n- **", start + 1)
    assert end != -1, "the exclude-completeness bullet no longer ends where the next one begins"
    bullet = text[start:end]
    assert 0 < len(bullet) < len(text), "the exclude-completeness slice is not a proper subset"
    return bullet


def test_the_rulebook_says_wip_saturated_needs_a_complete_exclude_and_the_code_agrees():
    """#527: the rule and the code property it describes, pinned together so they cannot drift
    apart in either direction.

    The gap this closes was observed live: the same board in the same minute answers
    wip_saturated:true to a complete `exclude` and a resume at free:0 — with no wip_saturated key
    at all — to an incomplete one, because branch 1 (your active tasks) returns before the slot
    guard. SKILL.md justified `exclude` ONLY as double-dispatch avoidance, so a pump that had
    lost its in-flight set was reading a payload the rulebook did not explain.

    Three things are pinned as INSTRUCTIONS, not as vocabulary — each assertion names the action
    or the claim, so deleting a sentence while leaving its keywords in the bullet still fails:
    (a) saturation is conditional on a complete `exclude`, (b) the imperative for the confusing
    state — check your own set, not the board, (c) the order is deliberate and stays. (c) matters
    most: without it the next reader files this as a bug, and "fixing" it would make
    `vikunja-mcp claimable` — which passes NO exclude — report "no work" on a board holding
    resumable work, silently idling every hub loop that trusts it.

    The behavioural half drives the real Workflow to both outcomes, so a future reordering of
    next_task's branches fails HERE too, not only in some distant hub."""
    bullet = _exclude_completeness_bullet(_skill_text())
    assert "ONLY if `exclude` is complete" in _flat(bullet), \
        "SKILL.md no longer says wip_saturated requires a COMPLETE exclude"
    assert "check YOUR `exclude`, not the board" in bullet, \
        "SKILL.md no longer tells the pump where to look when a resume arrives at free:0"
    assert "we do NOT touch the branch order" in bullet, \
        "SKILL.md no longer says the branch order is deliberate — the next reader will 'fix' it"
    assert "vikunja-mcp claimable" in bullet, \
        "SKILL.md no longer names the contract that the branch order protects"

    api = FakeAPI(buckets=workflow.STAGES)
    wf = workflow.Workflow(api, project_id=3, wip_limit=2)
    held = [api.add_task(t, "Queue") for t in ("first", "second")]
    for task in held:
        wf.claim(task["id"])
    api.add_task("free work nobody can take", "Queue")

    complete = wf.next_task(exclude=[t["id"] for t in held])
    assert complete["task"] is None and complete["wip_saturated"] is True, \
        "a COMPLETE exclude no longer produces the saturation signal the rulebook promises"

    incomplete = wf.next_task(exclude=[held[0]["id"]])
    assert incomplete["task"] is not None and incomplete["resume"] is True
    assert incomplete["wip"]["free"] == 0
    assert "wip_saturated" not in incomplete, \
        "saturation became reachable with an incomplete exclude — SKILL.md's rule is now wrong, " \
        "and `vikunja-mcp claimable` (empty exclude) may no longer report resumable work"


def test_the_integration_recipe_pushes_to_the_main_branch_and_names_gc():
    """Under the parallel drain a per-task agent sits in its own worktree on a THROWAWAY task/<id>
    branch, so a bare `git push` pushes that branch and leaves the main branch — and therefore the
    release pipeline — without the work, while every tool still reports success. The explicit
    refspec is the whole point of the integration recipe, so pin it verbatim. `workspace --gc` is
    pinned for the mirror-image reason: nothing else reaps a tree whose work has LEFT the board —
    a task that reached Review/Done or went back to Backlog/Your Call, a card that left Review —
    so without it those trees accumulate forever. (Round-2: NOT "crashed agents' trees", the
    inversion this docstring used to state. A crashed agent's task stays in Design/Build assigned
    to it, so liveness deliberately SPARES that tree — it is what the resume agent comes back to.)
    The orchestrator's tick in this rulebook is the only place that rule can live."""
    text = _skill_text()
    assert "git push origin HEAD:main" in text, "the explicit push-to-main refspec vanished"
    assert "workspace --gc" in text, "the tick no longer reaps dead worktrees (workspace --gc)"


def _integration_recipe(text: str) -> str:
    """The FENCED integration recipe — the block an agent copies, not the prose that explains it.

    Scoped to the fence on purpose, and not by a whole-file substring: `git push origin HEAD:main`
    alone appears twice in the rulebook (the fence, plus the parallel-drain bullet that summarises
    it in prose), so a file-wide search cannot tell "the recipe still says it" from "some paragraph
    mentions it" — exactly the weakness `_gc_section`'s docstring records having MEASURED. Exactly
    one such fence must exist: two would mean the recipe was duplicated, which is the drift this
    module exists to catch, not a state to tolerate."""
    blocks = [
        b for b in re.findall(r"```sh\n(.*?)```", text, re.S) if "git push origin HEAD:main" in b
    ]
    assert len(blocks) == 1, f"expected exactly 1 fenced integration recipe, found {len(blocks)}"
    recipe = blocks[0]
    assert 0 < len(recipe) < len(text), "the recipe slice is not a proper subset of SKILL.md"
    return recipe


def test_the_recipe_verifies_the_evidence_sha_actually_landed_on_main():
    """VMCP-77 (526): the recipe used to end at `git push origin HEAD:main` + `git rev-parse HEAD`,
    so "the push landed" rested on the absence of an error message. `rev-parse` cannot carry that
    weight — it (and `rev-parse --verify`) echoes back a full 40-hex sha with exit 0 whether or not
    the object exists — and existence is not ancestry either: a PRE-REBASE sha resolves fine while
    never reaching main, which under the parallel drain is the normal case, not the exotic one.
    Both commands were measured in a throwaway repo before being written into the rulebook.

    Pinned for the same reason the push refspec above is: SKILL.md self-heals onto every consumer
    on server start over the moving `stable` branch, with no per-consumer pin and no review gate,
    so an edit that "simplifies" this back to `rev-parse` ships to everyone silently. There is no
    code-side anchor to pin against — these are shell commands, not workflow symbols — so this is
    the `600`-interval kind of pin: a value that lives only in the rulebook.

    The literals are asserted WITH their quoting, which is load-bearing rather than stylistic:
    under zsh's `extendedglob` a bare `<sha>^{commit}` dies with `no matches found` before git
    runs, and that failure looks exactly like a bad sha. MUTATION-CHECKED (both directions, and
    with `__pycache__` cleared): delete either command line from the fence and this test fails;
    leave the fence alone but delete the surrounding explanation and it stays green — by design,
    prose wording is review's job, the copyable step is this net's."""
    recipe = _integration_recipe(_skill_text())
    assert 'git cat-file -e "<sha>^{commit}"' in recipe, \
        "the recipe no longer proves the evidence sha EXISTS (git cat-file -e)"
    assert 'git merge-base --is-ancestor "<sha>" origin/main' in recipe, \
        "the recipe no longer proves the evidence sha is ON the main branch (merge-base)"


def _tick_step_3(text: str) -> str:
    """The orchestrator tick's step 3 — the step that verifies a returning agent's evidence sha.

    Scoped to its own list item, like `_integration_recipe` and `_gc_section`: `review_task` and
    `call_human` are named all over the rulebook, so a whole-file substring could not tell "step 3
    still prescribes this" from "some other section mentions the tool"."""
    m = re.search(r"\n  3\. An agent came back with its result(.*?)\n  4\. ", text, re.S)
    assert m, "the orchestrator tick's step 3 is no longer where this pin can find it"
    return m.group(1)


def test_the_evidence_mismatch_escalation_is_one_the_orchestrator_can_execute():
    """VMCP-77 (526), rework — the pin the FIRST pass of this card needed and did not have.

    That pass told the orchestrator to call `call_human` when a returning agent's evidence sha
    fails verification. At that moment the card is in Review (the returning agent's contract is
    that it already called `advance(to='review')`), and `call_human` is gated to ACTIVE_STAGES =
    Design/Build — so the escalation for the failure branch the card itself created could not run.
    A rule naming behaviour the tools do not have is exactly what this module exists to catch, and
    it went out anyway because the advice was WRITTEN rather than RUN.

    So this pin does not compare strings alone: it drives the real `Workflow` through the state
    step 3 is actually in and executes the prescribed escalation end to end — the refusal that
    motivates the rule, the `review_task` bounce that replaces it, the pump picking the card back
    up, and the fallback that only becomes legal once the card is in Build. Change either gate and
    the rulebook's clause stops being executable; this goes red before it ships to every consumer.

    MUTATION-CHECKED (`__pycache__` cleared between rounds, each run confirmed to select exactly
    1 test): control PASS; add "Review" to ACTIVE_STAGES -> FAIL; make review_task move the card
    anywhere but Build -> FAIL; drop `review_task`/`needs_work` from step 3 -> FAIL; revert the
    clause to the `call_human` wording this card was returned for -> FAIL; rename step 3's opening
    words so the slice cannot find it -> FAIL (loudly, with its own message, never silently green).

    Deliberately NOT pinned: the wording of the reasoning around the clause, and the two rules no
    gate can carry (never `verdict='approve'` from the pump; the bounced card re-occupies a WIP
    slot). `review_task(approve)` from here executes fine — it is forbidden by the rulebook, not
    by the code, and pinning prose is review's job, per this module's docstring."""
    step3 = _tick_step_3(_skill_text())
    assert "review_task(" in step3 and "needs_work" in step3, \
        "tick step 3 no longer names the escalation that works from Review (review_task/needs_work)"
    assert hasattr(workflow.Workflow, "review_task"), "the escalation names a tool that is gone"

    api = FakeAPI(buckets=workflow.STAGES)
    wf = workflow.Workflow(api, project_id=3)
    task_id = api.add_task("its evidence sha never landed", "Queue")["id"]
    wf.claim(task_id)
    wf.advance(task_id, to="build", spec="…")
    wf.advance(task_id, to="review", worklog="…", evidence="0" * 40)
    assert api.stage_of(task_id) == "Review", "step 3 sees a card in Review — precondition"

    # 1. why the rule cannot say `call_human` here
    with pytest.raises(workflow.WorkflowError, match="Design/Build"):
        wf.call_human(task_id, "the reported evidence sha is not an ancestor of origin/main")

    # 2. what it says instead — and it moves the card somewhere an agent can work again
    bounced = wf.review_task(
        task_id, verdict="needs_work",
        report="evidence sha not on origin/main (merge-base --is-ancestor -> 1); not reviewed",
    )
    assert bounced["moved_to"] == "Build" and api.stage_of(task_id) == "Build"
    assert workflow.LABEL_REVIEW_FAILED in [
        label["title"] for label in api.get_task(task_id).get("labels") or []
    ]

    # 3. the pump gets it back on its own — no human needed to un-strand it
    nxt = wf.next_task()
    assert nxt["resume"] is True and nxt["stage"] == "Build"
    assert nxt["task"]["id"] == task_id

    # 4. and only NOW is the human channel open, for the agent that cannot re-push
    assert wf.call_human(task_id, "cannot re-push")["moved_to"] == "Your Call"


def _gc_section(text: str) -> str:
    """The `--gc` rule as a UNIT: the tick step in the core plus the report breakdown that moved
    to `references/gc-report.md`. Every refusal code and payload key an agent must recognise is in
    the second half now; slicing only the first would ask the core to explain shapes it points at.
    """
    return _gc_step(text) + "\n" + _reference("gc-report.md")


def _gc_step(text: str) -> str:
    """Just the `--gc` step of the orchestrator's tick, sliced out of the rulebook.

    ROUND-2 REVIEW, Minor: the assertions below used to be `code in text` — a WHOLE-FILE substring
    — and the rulebook explains these codes in TWO places, the `--gc` report and the `--release`
    recipe. MEASURED: delete the `dirty`/`unpushed` explanation out of the gc section entirely and
    the pin stayed green, because the release recipe's own prose still contains both words.
    Re-valuing a constant did fail, as claimed, but a pin that catches a rename and not a deletion
    guards the cheaper half of the risk. Scope it to the section that has to do the explaining.

    Both anchors are asserted rather than assumed: a slice that silently becomes empty would turn
    every assertion below red (loud, fine), but a slice that silently WIDENS to the whole file
    would restore exactly the weakness this exists to remove — so the width is checked too.
    """
    start = text.find("  1. `vikunja-mcp workspace --gc`")
    assert start != -1, "the orchestrator's tick no longer opens with the `workspace --gc` step"
    end = text.find("\n  2. ", start)
    assert end != -1, "the `--gc` step no longer ends where step 2 of the tick begins"
    section = text[start:end]
    assert 0 < len(section) < len(text), "the --gc slice is not a proper subset of SKILL.md"
    assert "workspace --release <id>" not in section, \
        "the slice swallowed the --release recipe — the very prose it exists to exclude"
    return section


def test_the_gc_report_split_the_skill_teaches_is_the_one_the_code_produces():
    """VMCP-68: `--gc` reports its refusals in TWO lists — `kept` ("a human should look") and
    `expected` ("routine, no action") — and the rulebook is what tells the pump which one to read.
    That makes the list name and every `code` it cites part of the same auto-propagating contract as
    the signal keys above: rename or re-value one in workspace_cmd.py and every agent keeps reading
    a list, or matching a code, that no longer exists — silently, since a missing key just reads as
    "nothing to look at".

    Anchored on the CONSTANTS rather than on literals repeated here, so a changed VALUE fails until
    the rulebook is updated with it — and scoped to the `--gc` section (see `_gc_section`), so
    DELETING an explanation fails too instead of coasting on the `--release` recipe's prose.
    `expected` is anchored inside gc_workspaces' own source, not the module's: the module-level
    word appears in comments and helper names, so a bare module-wide substring would stay green
    through the very rename it claims to catch."""
    text = _skill_text()
    section = _gc_section(text)
    gc_src = inspect.getsource(workspace_cmd.gc_workspaces)
    assert '"expected": expected' in gc_src, \
        "SKILL.md tells the pump to read `expected` but gc_workspaces stopped returning that list"
    assert "`expected`" in section, "SKILL.md's --gc rule no longer names the `expected` list"
    for code in (
        workspace_cmd.CODE_DIRTY,             # kept, or expected in a BUILD tree under a parked
        workspace_cmd.CODE_UNPUSHED,          #   card — VMCP-91; the state that made `kept`
                                              #   never-empty, and never routine for a REVIEWER
        workspace_cmd.CODE_UNREACHABLE_HEAD,  # routine in a REVIEW tree, an alarm in a build one
        workspace_cmd.CODE_DETACHED_BUILD,    # VMCP-86: a build tree off its own task/<id> branch
        workspace_cmd.CODE_HALF_CREATED,      # never expected: only a human can clear it
        workspace_cmd.CODE_LOCKED,            # VMCP-142: a human `git worktree lock`, also never
        workspace_cmd.CODE_SELF_TREE,         #   expected — see the grading note in workspace_cmd
        workspace_cmd.CODE_RELEASE_ERROR,
    ):
        assert code in section, \
            f"refusal code {code!r} is no longer explained in SKILL.md's --gc report rule"
    # CODE_LOCKED alone gets a second, tighter anchor: its VALUE is an ordinary English word, and a
    # bare substring is satisfiable by prose that has nothing to do with the code — SKILL.md
    # already contains `locked` inside `blocked` (the follows/blocked gate) and inside `uv sync
    # --locked`, neither of which would tell an agent anything about a pinned worktree. The
    # backticked form is how the rulebook cites every other code, so requiring it costs nothing and
    # is not satisfiable by a stray word.
    assert f"`{workspace_cmd.CODE_LOCKED}`" in section, \
        "SKILL.md's --gc rule no longer cites the `locked` refusal as a code"
    assert workspace_cmd.CODE_NO_WORKTREE in text, \
        "the --release recipe no longer explains the no-worktree refusal"


def _standing_record_bullet(text: str) -> str:
    """The bullet that tells the pump HOW OFTEN a standing `--gc` refusal is reported.

    Its own bullet rather than `_gc_section`, for that helper's own measured reason: "grace-окно"
    and "каждый свип" are words the section uses elsewhere (the `expected` notes above it), so a
    section-wide substring could not tell "the cadence rule is still stated" from "the words
    survive nearby"."""
    start = text.find("     - **A standing record arrives on EVERY sweep")
    assert start != -1, "SKILL.md no longer states the cadence of a standing --gc record"
    end = text.find("\n     `--gc` goes ALONE", start)
    if end == -1:
        # The rulebook split put this bullet in `references/gc-report.md` and left the `--gc`
        # argument rule in the core, so the anchor that used to follow it now PRECEDES it in the
        # concatenation. The bullet runs to the end of the reference instead.
        end = len(text)
    assert end != -1, "the cadence bullet no longer ends where the --gc argument rule begins"
    bullet = text[start:end]
    assert 0 < len(bullet) < len(text), "the cadence slice is not a proper subset of SKILL.md"
    assert "An unfamiliar `code`" not in bullet, "the slice swallowed the preceding bullet"
    return bullet


def _unreachable_head_note(text: str) -> str:
    """The `expected` (2) note — the reviewer's in-tree commit, and the ONE record on this board
    that no pipeline step ever clears. Sliced to the note itself so that restoring the old
    unconditional wording ("запись вечная") fails here rather than hiding inside the section."""
    start = text.find("       (2) `unreachable-head` on a REVIEW tree")
    assert start != -1, "SKILL.md no longer explains the review tree's unreachable-head record"
    end = text.find("\n     - ", start)
    assert end != -1, "the unreachable-head note no longer ends where the next bullet begins"
    note = text[start:end]
    assert 0 < len(note) < len(text), "the unreachable-head slice is not a proper subset"
    assert "(1) `unpushed`" not in note, "the slice swallowed the sibling `expected` note"
    return note


def _quiesce_for_gc(tree: Path) -> None:
    """Age every marker the grace window reads, so a DEAD tree is eligible for the sweep NOW.

    A local copy of `test_workspace_cmd`'s helper, for `git_repo`'s own stated reason: this module
    proves the RULEBOOK against the code and must not go red when another test module reshuffles
    its helpers. Derived from production's reader, and checked against it, so a marker this stops
    covering fails loudly instead of turning the assertions below into silent skips."""
    old = time.time() - workspace_cmd._REAP_GRACE_SECONDS - 60
    index = Path(_git(tree, "rev-parse", "--git-path", "index"))
    for marker in (tree, index if index.is_absolute() else tree / index):
        if marker.exists():
            os.utime(marker, (old, old))
    quiet_for = time.time() - workspace_cmd._last_activity(tree)
    assert quiet_for >= workspace_cmd._REAP_GRACE_SECONDS, \
        f"{tree} still reads as active ({quiet_for:.0f}s) — this helper is missing a marker"


def _codes_for(sweep: dict, task_id: int) -> dict[str, list[str]]:
    """Just this task's refusal codes, per list — every sweep below carries other trees too."""
    return {
        name: [e["code"] for e in sweep[name] if e["task_id"] == task_id]
        for name in ("kept", "expected", "released")
    }


def test_the_standing_gc_record_is_reported_under_the_conditions_the_rulebook_names(git_repo):
    """VMCP-83 (533): the rulebook used to state the cadence of a standing `--gc` refusal as a
    FREQUENCY — "запись будет на КАЖДОМ тике, пока человек не ответит" and, for a reviewer's
    in-tree commit, "запись вечная". Both are counters with their conditions filed off, and both
    drifted the moment VMCP-71 gave the sweep a grace window: a frequency cannot be re-derived by
    a reader, so when the code moved underneath it nothing went red and every consumer kept
    reading it. This pins the CONDITIONS instead, on both sides — the code that produces them and
    the prose that promises them.

    MEASURED here, by running consecutive sweeps rather than reading the guards (git 2.50.1, real
    worktrees, the fake board):

      * a dead tree whose last write is YOUNGER than `_REAP_GRACE_SECONDS` appears in NO list at
        all — the sweep declines to inspect it, so there is no refusal to grade;
      * past the window the same tree reports on EVERY consecutive sweep, because gc's own
        inspection takes no optional locks (VMCP-90) and therefore does not renew the window;
      * the window runs from the last WRITE, not from the death of the task, and "write" is
        exactly the two markers `_last_activity` stats. Editing a file in a SUBDIRECTORY — an
        agent's commonest act — moves NEITHER, so the record keeps coming; a new entry at the TOP
        LEVEL moves one and the tree goes quiet again. Both directions are asserted, because the
        second pass found the first draft of this rule claiming "любая новая запись" and that is
        a safety claim a reader would rely on;
      * a REVIEW tree holding an in-tree commit reports NOTHING while its card sits in Review —
        the tree is alive by role — and starts reporting only once the card leaves; put the card
        back in Review and the record disappears again, which is precisely what "вечная" denied.
        That same fixture carries the corollary: it is quiesced WHILE its card is still in Review,
        so the first sweep after the card leaves reports it with no waiting at all — "умерло
        только что" and "invisible for a window" are independent properties.

    So the two ends of the record differ, and that is the distinction the prose now has to carry:
    the parked build tree's record is CLOSED by the human's answer, while the reviewer's is closed
    by nothing this pipeline does.

    ROUND 2 added the pair below, because the first draft of that note offered TWO commands for
    the one exit it names and called them "тот же эффект" without running the second. Measured on
    two identical dead review trees, sweeping IMMEDIATELY after each command — quiescing between
    the command and the sweep would age away the very marker the command just wrote, which is the
    review's standing HYPOTHESIS for how the claim survived writing (not measured, and it cannot
    be: how the earlier round ran is not a property of anything here):

      * `git branch <имя> <sha>` moves NEITHER marker (byte-identical mtimes before and after,
        from the main repo and from inside the tree alike), so the NEXT sweep reaps: `released`,
        directory gone. That is the exit, and it is the only one of the two that is;
      * `git reset --hard HEAD~1` writes the INDEX, i.e. it renews the very window it was
        supposed to end — four consecutive sweeps after it did nothing at all, and the tree went
        only once the window had expired AGAIN. The index is the half that always moves and the
        half that suffices: measured both shapes, a notes commit touching a file in the tree's
        ROOT moves the directory marker too, one touching only a SUBDIRECTORY moves the index
        alone, and the window is renewed identically. ("BOTH markers" was this docstring's own
        first draft, inherited from the review comment rather than run — the subdirectory case
        disproves it while leaving the conclusion standing.) Reset also does not make the notes
        commit reachable in the first place: `git branch --contains` comes back empty and
        `git fsck --unreachable --no-reflogs` lists it. The flag is load-bearing — PLAIN
        `git fsck --unreachable` does NOT list the commit while the worktree directory still
        stands, because its per-worktree reflog anchors it. Reset moves HEAD onto the parent and
        orphans the notes; it passes the guard only because the guard inspects HEAD.

    The two are pinned as a CONTRAST — same sweep, same tree shape, different command — so the
    mutation that matters is an INPUT swap rather than a code edit, and both directions were run
    (control PASS before and after each, `__pycache__` cleared, exactly 1 test selected):

      * put `reset --hard` where the branch route runs -> FAIL, "the NEAREST sweep after
        `git branch` did not reap" (`assert 109 in []`);
      * put `git branch` where the reset route runs -> FAIL, "the nearest sweep reaped after
        reset --hard" (`assert 108 not in [108]`). That assertion checks the released IDS FIRST
        and deliberately: routed through `_codes_for` it died as a `KeyError: 'code'` three frames
        away — a released entry carries no code — which is a red test that never states the claim;
      * restore the old prose ("тот же эффект") -> FAIL on the note's warning; delete the
        `git branch` command name from the note -> FAIL on the other;
      * downcase the bullet's `ПОДКАТАЛОГЕ` warning after this round rewrote that sentence ->
        still FAIL, so the widened признак did not cost the pin that was already there;
      * grace window off (`if False and …`) -> FAIL, but at the YOUNG-sweep assertion far above
        rather than at either of these: the run stops there, so what the two routes do under that
        mutation was NOT observed and is not claimed here. Recorded because it is the obvious
        code-level mutation to reach for and it does NOT isolate this contrast — the input swap
        above is what guards it.

    THE SECOND INDEPENDENT PASS then found that the fixed sentence had grown TWO new overclaims
    of its own, both re-measured here before being accepted, and both about the promise a reader
    acts on — that after `git branch` the NEAREST sweep reaps:

      * not if the window has not expired yet. A tree whose card died moments ago is skipped as
        young no matter what you do to its refs (measured: nothing on two sweeps, reaped only
        after the window);
      * not if anything was left in the tree. A forgotten untracked file is `dirty`, and `dirty`
        keeps the tree with `git branch` having no bearing on it at all — which the SAME note
        already says ten lines further down, so the unconditional contradicted its own paragraph;
      * and the trap in between: the `git status` a human types to LOOK at the tree before
        deciding writes the index itself, so the window restarts and the next sweep does nothing.

    The stated MECHANISM was wrong too, in the safe direction but still wrong as a rule to reason
    from: "появление или исчезновение записи" does not cover a rename OVER an existing name.
    Measured with the temp file in a SUBDIRECTORY — the top-level name set is byte-identical
    before and after, and the directory marker moves anyway.

    Four sentences of this round were then found to be INVERTIBLE with the whole suite green, so
    each got a positive pin and each inversion was re-run (control PASS before and after):
    strip `--no-reflogs` -> FAIL; drop the "окно УЖЕ истекло" condition -> FAIL; drop "ДОЛЬШЕ
    окна" -> FAIL; flip the ignored-files claim -> FAIL. Before the pins all four were green,
    which is this card's own defect reproduced inside its own fix.

    The bullet's own reach widened with it. "Правка файла в ПОДКАТАЛОГЕ не двигает метки" is
    true but reads as though editing in the ROOT would hold the window; measured, editing an
    EXISTING top-level file moves neither marker either (edit, append and chmod all checked), and
    what moves one is the SET of entries changing — create, remove, or an atomic save over an
    existing file (tmp + `os.replace`), which is how many editors save. And the sentence promising
    that no work is lost was measured to be wider than its guard: `dirty` is `git status
    --porcelain`, which does not show IGNORED files, so a clean and fully pushed tree was reaped
    with `secrets.env` and `scratch/notes.txt` still inside it.

    MUTATION-CHECKED both ways (`__pycache__` cleared between rounds, every run confirmed to
    select exactly 1 test — "1 passed/failed, 45 deselected" — and both files restored from copies
    kept aside, never `git checkout --`), control PASS before and after each:

      * delete the grace-window skip from `gc_workspaces` (`if False:`) -> FAIL on the YOUNG
        sweeps, which is the half a reader would otherwise have to take on trust;
      * undo VMCP-90 by letting gc's own inspection take optional locks again -> FAIL on the
        consecutive quiet sweeps. Mutate `_git_inspect`'s BODY (drop its `GIT_OPTIONAL_LOCKS=0`),
        NOT its call sites: swapping the seven `_git_inspect(` calls for `_git(` raises TypeError
        and goes red for the wrong reason entirely — a mutation that "fails" without exercising
        the claim proves nothing;
      * give `_last_activity` a RECURSIVE marker (max mtime under the tree) -> FAIL, though on
        `_quiesce_for_gc`'s own self-check rather than on the subdirectory assertion: a reader
        that looks at more than the two markers can no longer be quiesced by ageing them, which
        is exactly what that self-check is there to say out loud. Drop the TOP-LEVEL directory
        marker instead (`candidates = []`) -> FAIL on the top-level assertion, so the two
        directions are pinned by two different rounds;
      * restore the old unconditional note ("не сметается НИКОГДА, запись вечная") -> FAIL on the
        two missing conditions. That assertion started life as its MIRROR — `"вечн" not in note`
        — and it fired on the new text's own denial ("ЗАПИСЬ вечной НЕ бывает"), i.e. a
        word-level negative pin cannot tell a claim from its retraction. Positive conditions
        catch the same restoration and nothing else;
      * delete the window from the cadence bullet -> FAIL; halve `_REAP_GRACE_SECONDS` in the
        code alone -> FAIL too, since the bullet's minutes are derived from the constant here
        rather than compared with a number typed twice; downcase the bullet's `ПОДКАТАЛОГЕ`
        warning -> FAIL, so the prose half cannot be deleted while the behaviour still holds;
      * drop `_keep_is_expected`'s review-role conjunct -> still PASS here, deliberately: that
        cell belongs to test_workspace_cmd, and the attribution was CHECKED rather than assumed —
        against that whole file the mutation turns
        test_keep_grading_of_unreachable_head_still_turns_on_the_role and
        test_the_grading_grid_is_all_kept_outside_the_four_named_cells red (control 0 failed,
        119 passed; mutated 2 failed, 117 passed)."""
    text = _skill_text()
    bullet = _standing_record_bullet(text)
    note = _unreachable_head_note(text)
    api = FakeAPI(buckets=workflow.STAGES)
    wf = workflow.Workflow(api, project_id=3)

    def sweep():
        return workspace_cmd.gc_workspaces(cwd=git_repo, workflow=wf)

    # --- a BUILD tree under a card parked in Your Call: the routine, no-action state
    parked = api.add_task("waiting on a human", "Your Call")["id"]
    tree = Path(workspace_cmd.ensure_workspace(parked, cwd=git_repo)["path"])
    (tree / "sub").mkdir()
    (tree / "sub" / "code.py").write_text("x = 1\n")
    (tree / "wip.txt").write_text("the push that got rejected\n")
    _git(tree, "add", "wip.txt", "sub/code.py")
    _git(tree, "commit", "-m", "wip")

    young = [_codes_for(sweep(), parked) for _ in range(2)]
    assert young == [{"kept": [], "expected": [], "released": []}] * 2, \
        "a just-written dead tree is reported — the rulebook says the window skips it silently"

    _quiesce_for_gc(tree)
    quiet = [_codes_for(sweep(), parked) for _ in range(3)]
    assert [s["expected"] for s in quiet] == [[workspace_cmd.CODE_UNPUSHED]] * 3, \
        "the standing record does not survive consecutive sweeps — gc is renewing the window"
    assert tree.exists(), "the sweep removed a tree holding unpushed work"

    # …and the window's markers are the TWO the rulebook names, pinned from both sides: editing a
    # file in a SUBDIRECTORY (an agent's commonest act) moves neither, so the record keeps coming;
    # a new entry at the TOP LEVEL moves one, and the tree goes quiet again for a whole window.
    (tree / "sub" / "code.py").write_text("x = 2   # the agent is back, editing source\n")
    assert _codes_for(sweep(), parked)["expected"] == [workspace_cmd.CODE_DIRTY], \
        "a write in a subdirectory now renews the window — the rulebook says it does not"
    (tree / "scratch.txt").write_text("a new entry at the top level\n")
    assert _codes_for(sweep(), parked) == {"kept": [], "expected": [], "released": []}, \
        "a top-level write no longer renews the window — the rulebook says it does"

    # --- a REVIEW tree the reviewer committed its notes into
    reviewed = api.add_task("under review", "Review")["id"]
    head = _git(git_repo, "rev-parse", "HEAD")
    rtree = Path(workspace_cmd.ensure_workspace(
        reviewed, role="review", at=head, cwd=git_repo)["path"])
    (rtree / "notes.md").write_text("verdict draft\n")
    _git(rtree, "add", "notes.md")
    _git(rtree, "commit", "-m", "reviewer notes")
    _quiesce_for_gc(rtree)

    assert _codes_for(sweep(), reviewed) == {"kept": [], "expected": [], "released": []}, \
        "a review tree reports while its card is in Review — the rule says the board keeps it"

    api.task_bucket[reviewed] = api.bucket_id("Build")          # the card leaves Review
    dead = [_codes_for(sweep(), reviewed) for _ in range(3)]
    assert [s["expected"] for s in dead] == [[workspace_cmd.CODE_UNREACHABLE_HEAD]] * 3, \
        "the reviewer's in-tree commit stopped reporting once its card left Review"
    assert rtree.exists(), "the sweep removed the review tree its own guards refuse to remove"

    api.task_bucket[reviewed] = api.bucket_id("Review")         # …and comes back
    assert _codes_for(sweep(), reviewed) == {"kept": [], "expected": [], "released": []}, \
        "the record survived the card returning to Review — then it really would be unconditional"

    # --- the note offers ONE reachability route, and the two candidates are NOT interchangeable.
    # Review round 2 measured what the first draft asserted from the armchair: `git reset --hard
    # HEAD~1` writes the INDEX, so it renews the window it was supposed to end, and it does not
    # make the notes commit reachable at all. Pinned by running, because that is exactly the shape
    # of claim ("X — тот же эффект") a reader acts on and cannot re-derive.
    api.task_bucket[reviewed] = api.bucket_id("Build")          # the card leaves Review again
    notes_sha = _git(rtree, "rev-parse", "HEAD")
    _git(rtree, "reset", "--hard", "HEAD~1")
    # the released-id check goes FIRST and by id: `_codes_for` reads a `code` off every entry, and
    # a `released` one carries none — so the reap this pins against would surface as a KeyError
    # three frames away instead of as this sentence.
    after_reset = sweep()
    assert reviewed not in [e["task_id"] for e in after_reset["released"]], \
        "the nearest sweep reaped after reset --hard — the note says reset renews the window"
    assert _codes_for(after_reset, reviewed) == {"kept": [], "expected": [], "released": []}, \
        "reset --hard no longer renews the window — the note tells agents that it does"
    assert rtree.exists(), "the nearest sweep reaped after reset --hard; the note says it does not"
    assert _git(git_repo, "branch", "--all", "--contains", notes_sha) == "", \
        "reset --hard now leaves the notes commit on a branch — the note says it orphans it"
    _quiesce_for_gc(rtree)                                      # …only a LATER sweep reaps it
    assert reviewed in [e["task_id"] for e in sweep()["released"]], \
        "reset --hard never gets the tree reaped at all — the note says a later sweep does"

    # …while `git branch <имя> <sha>` touches no marker, so the NEXT sweep reaps: the route the
    # note actually tells a human to take.
    branched = api.add_task("also under review", "Review")["id"]
    btree = Path(workspace_cmd.ensure_workspace(
        branched, role="review", at=head, cwd=git_repo)["path"])
    (btree / "notes.md").write_text("verdict draft\n")
    _git(btree, "add", "notes.md")
    _git(btree, "commit", "-m", "reviewer notes")
    bnotes = _git(btree, "rev-parse", "HEAD")
    _quiesce_for_gc(btree)
    api.task_bucket[branched] = api.bucket_id("Build")
    assert _codes_for(sweep(), branched)["expected"] == [workspace_cmd.CODE_UNREACHABLE_HEAD], \
        "the second review tree is not in the state whose two exits this pins"
    _git(git_repo, "branch", f"keep-{branched}", bnotes)
    assert branched in [e["task_id"] for e in sweep()["released"]], \
        "the NEAREST sweep after `git branch` did not reap — the note promises that it does"
    assert not btree.exists(), "the tree survived a sweep that reported it released"

    # --- and the prose states those conditions rather than a bare frequency
    # `_flat` for every prose pin: these phrases are long enough that a re-wrap lands inside one
    # (the first draft of this test pinned "НЕ продлевает" raw and went red on a line break alone).
    bullet, note = _flat(bullet), _flat(note)
    minutes = workspace_cmd._REAP_GRACE_SECONDS // 60
    assert f"grace window ({minutes} min)" in bullet, \
        "the cadence bullet no longer names the window, or names a length the code disagrees with"
    assert "does NOT extend the window" in bullet, \
        "the bullet no longer says the sweep itself does not renew the window (VMCP-90's fix)"
    assert "not in a SUBDIRECTORY" in bullet, \
        "the bullet stopped warning that a write below the top level does not renew the window"
    assert "on the VERY FIRST sweep" in bullet, \
        "the bullet stopped saying a tree that was already quiet is reported the instant it dies"
    assert "stands OUTSIDE Review" in note and "older than the grace window" in note, \
        "the unreachable-head note lost one of the two conditions its record actually has"
    assert "there is no step in the pipeline" in note, \
        "the note no longer says what IS permanent here — that nothing in the pipeline clears it"
    assert "`git branch <name> <sha of the notes>`" in note, \
        "the note stopped naming the one command that actually makes the notes commit reachable"
    assert "is NOT that way out" in note, \
        "the note dropped its warning that reset --hard is not the same exit — it measurably is not"
    # Three more positive pins, added because the second pass INVERTED each of these sentences and
    # the whole suite stayed green — the exact drift this card exists to stop. Each guards a claim
    # whose negation is what a reader would otherwise act on.
    assert "--no-reflogs" in note, \
        "the note dropped the fsck flag — bare `git fsck --unreachable` stays SILENT while the " \
        "worktree stands, so without it the command shown proves the opposite of what is claimed"
    assert "the window has ALREADY expired" in note, \
        "the note went back to promising that the NEAREST sweep reaps after `git branch` — " \
        "measured false for a young tree and for one holding a forgotten file"
    assert "stood quiet LONGER than the window" in bullet, \
        "the bullet dropped the condition again: a tree written to just before its card died is " \
        "NOT reported on the first sweep, which is what the unconditional version promised"
    assert "does not show IGNORED paths" in bullet, \
        "the bullet stopped saying `dirty` cannot see ignored files — the one measured way a " \
        "sweep destroys something the 'no work is lost' promise sounds like it covers"


def test_the_released_entrys_branch_leak_is_documented_where_agents_will_read_it():
    """VMCP-… (542), the hole VMCP-68's own reading rule opened. #517 made the one failure mode of
    a SUCCESSFUL release report itself honestly: `worktree remove` succeeded but `git branch -D`
    did not, so the entry is `released: true` PLUS `branch_deleted: false` and a `warning` naming
    the leaked branch. That entry therefore rides in `released` — the list VMCP-68's rule called
    the one nobody needs to read — so a rule of "read `kept`, skip the rest" hides it and
    `task/<id>` branches accumulate with nothing to notice.

    Pinned on both sides for the usual reason (the rulebook self-heals onto every consumer with no
    review gate): drop the keys in the code and the rulebook still teaches them; drop the prose and
    the pump goes back to skipping the list they arrive in."""
    text = _skill_text()
    release_src = inspect.getsource(workspace_cmd._release_locked)
    for key in ("branch_deleted", "warning"):
        assert f'result["{key}"]' in release_src, \
            f"_release_locked no longer reports {key!r} when the branch delete fails"
        assert key in text, f"SKILL.md no longer tells agents about the {key!r} key"
    assert "branch_deleted" in _gc_section(text), \
        "the --gc reading rule stopped covering `branch_deleted` — a leaked branch is invisible"


def _two_returns_rule(text: str) -> str:
    """The «Два возврата, два дерева» bullet — the rule that splits the two ways a task
    comes back to an agent.

    Sliced to its own top-level bullet, like `_gc_section` / `_tick_step_3`: `created`,
    `--release` and `task/<id>` are named all over the parallel-drain section, so a whole-file
    substring could not tell "the split is still stated" from "the words survive somewhere"."""
    start = text.find("- **Two returns, two trees.**")
    assert start != -1, "SKILL.md no longer splits the two ways a task comes back to an agent"
    end = text.find("\n- **", start + 1)
    assert end != -1, "the two-returns rule no longer ends where the next top-level bullet begins"
    section = text[start:end]
    assert 0 < len(section) < len(text), "the two-returns slice is not a proper subset of SKILL.md"
    assert "A review takes no slot" not in section, "the slice swallowed the following bullet"
    return section


def _git(cwd, *args) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def git_repo(tmp_path, monkeypatch):
    """A clone on `main` with a local bare origin it has already pushed to — enough to run
    both return paths for real, with no network.

    A local copy rather than an import of `test_workspace_cmd`'s `repo` fixture: this module
    proves the RULEBOOK against the code, and a pin that goes red when an unrelated test module
    reshuffles its fixtures is a pin nobody trusts. `ENV_WORKTREE_ROOT` is cleared for that
    module's own measured reason — the pump exports it machine-wide, so an agent running this
    suite inside its own worktree would otherwise steer these trees at the AMBIENT root."""
    monkeypatch.delenv(config.ENV_WORKTREE_ROOT, raising=False)
    workspace_cmd._main_worktree.cache_clear()
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(origin)],
                   check=True, capture_output=True)
    work = tmp_path / "work"
    work.mkdir()
    _git(work, "init", "-b", "main")
    _git(work, "config", "user.email", "t@example.com")
    _git(work, "config", "user.name", "Tester")
    (work / "README.md").write_text("hi\n")
    _git(work, "add", "README.md")
    _git(work, "commit", "-m", "init")
    _git(work, "remote", "add", "origin", str(origin))
    _git(work, "push", "-u", "origin", "main")
    return work


def test_the_two_ways_a_task_comes_back_hand_back_the_trees_the_rulebook_promises(git_repo):
    """VMCP-82 (532): the rulebook used to describe ONE way a task comes back — "you return to
    the same worktree with your unfinished work" — while the code has two, so a rework agent
    could hunt for uncommitted work that was never there.

    * CRASH: nothing was released, so `_ensure_locked`'s early-return hands the same tree back
      (`created: false`) with everything in it, committed and not.
    * BOUNCE after review: the predecessor pushed and called `--release`, which removed the tree
      AND deleted `task/<id>`. There is nothing to reattach to, so a fresh tree is cut from the
      CURRENT `origin/main` — several commits ahead of the original base, clean, and already
      carrying the predecessor's work because a bounce can only follow a successful push.

    Pinned on BEHAVIOUR, not on prose, because the failure this card fixes is a rulebook that
    states one thing while the code does another — and only running the code can tell those
    apart. So both paths are executed against real git, and the rulebook's two payload tokens
    plus its two-command check ride along: change either side alone and this goes red.

    The load-bearing assertion is the sibling commit's presence in the reworked tree. It is what
    distinguishes "cut fresh from current main" from "reattached to a surviving branch" —
    `created: true` alone cannot, since a tree whose DIRECTORY was removed by hand takes the
    same value while reattaching to its old branch.

    MUTATION-CHECKED (`__pycache__` cleared between rounds, each run confirmed to select exactly
    1 test, SKILL.md and workspace_cmd.py restored from copies kept aside — never `git checkout
    --`): control PASS; delete the rule from SKILL.md -> FAIL on the slice; drop `created: false`
    out of the rule -> FAIL; make `_release_locked` keep the branch (the change this card
    explicitly does NOT propose) -> FAIL on the leftover `task/22`, and again on the missing
    sibling commit when that first assertion is neutralised, so both halves of the bounce claim
    are load-bearing rather than one covering for the other."""
    rule = _two_returns_rule(_skill_text())
    repo = git_repo

    # --- return 1: the agent crashed. Nothing was released, so the tree comes back as it was.
    tree = Path(workspace_cmd.ensure_workspace(11, cwd=repo)["path"])
    (tree / "half.txt").write_text("committed, unfinished\n")
    _git(tree, "add", "half.txt")
    _git(tree, "commit", "-m", "wip")
    (tree / "scratch.txt").write_text("never committed\n")

    resumed = workspace_cmd.ensure_workspace(11, cwd=repo)
    assert resumed["created"] is False and Path(resumed["path"]) == tree, \
        "the crash path no longer hands back the SAME tree — SKILL.md promises `created: false`"
    assert (tree / "scratch.txt").exists(), "the resumed tree lost its uncommitted work"
    assert _git(tree, "log", "--oneline", "origin/main..HEAD"), \
        "the resumed tree lost the unfinished commits the rule tells the agent to expect"

    # --- return 2: the agent pushed, released its tree, and a reviewer bounced the card.
    done = Path(workspace_cmd.ensure_workspace(22, cwd=repo)["path"])
    (done / "shipped.txt").write_text("landed\n")
    _git(done, "add", "shipped.txt")
    _git(done, "commit", "-m", "done")
    _git(done, "push", "origin", "HEAD:main")
    shipped = _git(done, "rev-parse", "HEAD")
    assert workspace_cmd.release_workspace(22, cwd=repo)["released"] is True
    assert _git(repo, "branch", "--list", "task/22") == "", \
        "--release no longer deletes task/<id> — the bounce would reattach, not cut fresh"

    # a sibling lands on main while the card waits in Review
    _git(repo, "fetch", "origin")
    _git(repo, "merge", "--ff-only", "origin/main")
    (repo / "sibling.txt").write_text("someone else's task\n")
    _git(repo, "add", "sibling.txt")
    _git(repo, "commit", "-m", "sibling")
    _git(repo, "push", "origin", "main")

    rework = workspace_cmd.ensure_workspace(22, cwd=repo)
    fresh = Path(rework["path"])
    assert rework["created"] is True, "the bounced task no longer gets a freshly created tree"
    assert _git(fresh, "status", "--porcelain") == "", \
        "SKILL.md tells the rework agent there is no uncommitted work here — there now is"
    assert _git(fresh, "log", "--oneline", "origin/main..HEAD") == "", \
        "SKILL.md tells the rework agent nothing unpushed is here — there now is"
    assert (fresh / "sibling.txt").exists(), \
        "the reworked tree is NOT cut from the current origin/main (the sibling commit is absent)"
    assert (fresh / "shipped.txt").exists(), \
        "the predecessor's own work is missing — the rule says the bounce follows a landed push"
    landed = subprocess.run(["git", "merge-base", "--is-ancestor", shipped, "origin/main"],
                            cwd=fresh, capture_output=True)
    assert landed.returncode == 0, \
        "the predecessor's pushed commit is not on the main branch the fresh tree was cut from"

    # and the rulebook says both outcomes in the payload's own vocabulary, plus the two-command
    # check that answers "is there unfinished work here?" without the agent having to guess
    assert "`created: false`" in rule, "the rule no longer names the crash path's `created: false`"
    # NOT a bare "`created: true`": the crash bullet ALSO carries that token (the crudely removed
    # directory that is cut afresh and reattached), so the bare form is satisfied by a clause that
    # is not the bounce — measured, deleting the bounce path's own token left it GREEN.
    assert "cuts a FRESH tree from the CURRENT `origin/<main branch>` (`created: true`)" \
        in _flat(rule), "the rule no longer names the bounce path's `created: true`"
    assert "git status --porcelain" in rule and \
        "git log --oneline origin/<main branch>..HEAD" in _flat(rule), \
        "the rule lost the two commands that settle it without guessing"


def test_empty_queue_wakeup_interval_is_pinned():
    """The idle-loop wakeup interval is a hand-set human decision (#80: 20→10 min = 600s) with no
    code counterpart to anchor it — it lives only in the rulebook. Pin the value so an unrelated
    skill edit can't silently revert it; a deliberate change updates this one line on purpose."""
    assert "600" in _skill_text(), "the empty-queue ScheduleWakeup interval (600s, #80) vanished"


def _flat(text: str) -> str:
    """SKILL.md with every run of whitespace collapsed to one space — for pinning PROSE phrases.

    A markdown paragraph's line breaks are cosmetic: re-wrapping one is a meaning-preserving edit
    that must not turn a pin red, and the wrap can fall anywhere inside the phrase being pinned
    (the parallel-drain sentence below already breaks mid-clause). Fenced recipes are matched RAW
    instead (see `_integration_recipe`) — inside a fence a line break separates two commands, so
    flattening one would let a pin match text that is no longer a runnable step."""
    return re.sub(r"\s+", " ", text)


def test_the_integration_retry_ceiling_is_pinned():
    """VMCP-81, generalised by VMCP-94 (550): how many `fetch → rebase → re-verify → push` rounds a
    per-task agent runs before escalating via `call_human` is — like the wakeup interval above — a
    hand-set human number with NO code counterpart: nothing in workflow.py counts rounds, so this
    test is the only thing that can hold it. And it is DERIVED, not preferred. CI's auto-release
    pushes a `chore: vX.Y.Z [skip ci]` bump after every green landing (measured 2026-07-30 on this
    repo's first live parallel drain: 17 of the 46 commits that reached main that day were the
    bot's, arriving 37 s–2 m 55 s behind the task commit, median 1 m 41 s), so a losing push is the
    EXPECTED outcome, not an edge case — but that racer is BOUNDED: `[skip ci]` + GITHUB_TOKEN means
    it never triggers itself, so it never pushes twice in a row and costs at most one round on its
    own. The ceiling must exceed the worst purely MECHANICAL run, which at `wip_limit = N` is 2·(N−1)
    sibling+bump losses plus the trailing bump of the landing that beat you to the `fetch`, i.e.
    2·(N−1)+1 — so the ceiling is **2 × N**, the smallest value strictly above it. 6 is only that
    formula's N=3 instance (the default limit, which is this repo's own case), not the rule.

    Why a formula and not "6, plus advice to raise it": the rulebook self-heals onto every consumer
    over the moving `stable` branch, OVERWRITING local edits, with no per-consumer pin and no review
    gate (see this module's docstring) — and there is no config key for a retry ceiling. So "raise
    it if your limit is wider" is unactionable by construction: a consumer at `wip_limit = 4` (worst
    mechanical run 7) cannot edit a pinned 6, it can only be shipped a rule that COMPUTES. The
    variable therefore has to be one the agent already receives: `wip.limit`, from `next_task`'s
    `wip` payload, which `with_wip` attaches to EVERY branch of the result — which is why the
    rulebook can tell the orchestrator to carry the limit in its dispatch brief at all. That payload
    is the one part of this rule that DOES have a code counterpart, so it is pinned on both sides:
    rename the key or its `limit` field and this goes red, instead of leaving every agent computing
    a ceiling from a field that no longer arrives.

    The likely bad edit has moved. It used to be the walk-back to 3 (exactly the length of the
    commonest bad run, bump(A) → commit(B) → bump(B), so it reads as a sane-looking number to anyone
    re-tidying this prose without the derivation in hand, while calling a human onto pure arithmetic
    at the moment the next round would almost certainly have won). Now it is RE-COLLAPSING the
    formula into a constant — "it is always 6 here anyway" — which stays silently correct on this
    repo, at the default limit, and is silently wrong everywhere the drain is wider: at 4 a pinned 6
    escalates to a human on arithmetic alone. The three positive site pins below are all spelled
    `2 × max(wip.limit, wip.active)`, so a re-collapse cannot pass them.

    VMCP-280 (939) moved those three spellings off `wip.limit` alone, and the reason is that the
    limit was the WRONG variable rather than a coarse one: rivals on the push are whoever is really
    in Design/Build, and `wip.active` legitimately exceeds the limit because rework re-enters Build
    past the `claim` gate. Measured on this board — active 5–7 against a limit of 3, and card 851
    spent all 6 rounds on pure mechanics, green gates, no rebase conflict, then parked finished
    pushed work in Your Call, i.e. the ceiling fired on exactly the arithmetic it exists to
    prevent. `max` keeps it from ever DROPPING below the old value. A fourth positive pin follows,
    on the dispatch brief carrying `wip.active`: unlike the limit that number is BOARD state with
    no config to read it from, so losing it from the brief silently collapses the formula back.
    MUTATION-CHECKED for 939 (both rounds `collected 55 items`, the same selection as the control;
    SKILL.md restored sha256-identical afterwards; `FAILED `/`ERROR ` lines counted separately):
    control 0 failed; drop `wip.active` out of the dispatch brief -> 1 failed; re-collapse the
    recipe's round count to a bare `2 × wip.limit` -> 1 failed.

    Pinned in all three places that carry the RULE, not once against the whole file (see
    `_gc_section` on why a whole-file substring is the weak form of this): the parallel-drain
    paragraph, the shell recipe's round count (scoped to the fence, so a deletion cannot coast on
    the prose that summarises it), and the escalation sentence that spends the ceiling on
    `call_human`. Deleting any ONE of the three then fails instead of coasting on the others — a
    recipe with no escalation sentence, or an escalation with no round count, is exactly the
    half-stated rule an agent would fill in with its own guess.

    Three further halves are pinned because without any one of them the rule stops being EXECUTABLE,
    which is this module's actual subject:
      * the DIAGNOSIS (`git log --oneline HEAD..origin/main`) and the `call_human` verdict it feeds.
        A round is owed only for a race that was LOST; an empty range means there was no race at all
        (protected branch, no push rights, pre-receive hook, wrong remote), where every further
        round re-loses identically at the cost of a full criteria run. The COUNT cannot tell those
        apart.
      * what the escalation SAYS once the ceiling is spent: the question carries the LIST of what
        won each round («N кругов подряд, вот что …»), not the count. This is pinned because the
        sentence it replaced — "hitting 6 means the loop is NOT converging" — is short, confident,
        and now FALSE above the default limit (under a wider drain, or with humans also pushing,
        pure mechanics reaches the ceiling), which makes it exactly what a tidying editor restores.
        With it back, an agent at `wip_limit >= 4` tells a human "the loop is broken" about pure
        arithmetic, and hands over a (wrong) diagnosis instead of the evidence to make one. The `N`
        is also the half the old spelling («шесть кругов подряд») cannot satisfy, so a PARTIAL
        revert — new ceiling, old escalation — fails here rather than shipping.
      * the brief-less FALLBACK to 6. `2 × wip.limit` with no limit in hand is an unfillable
        variable; drop the fallback and an agent dispatched by a pump that did not name the limit
        has no ceiling at all. Its mirror — the dispatch brief being told to carry the limit — is
        pinned too: lose that and every agent silently falls back to the default forever, i.e. the
        generalisation ships dead. VMCP-102 (559) put a READ of the repo toml in front of that
        constant, so the pin below is now on the constant alone (`— **бери 6**`) rather than on the
        whole sentence around it; what the read must say is pinned by
        `test_the_brief_less_ceiling_reads_the_repo_toml_before_it_falls_back`, which also
        re-derives the 6 instead of matching it.

    The negative half stays exactly as it was: the EXACT old 3-spellings a revert brings back. A
    bare `"3" not in text` would be vacuous (`wip_limit` defaults to 3, the measurements quote
    3 min) and would forbid the derivation prose that has to name the number it replaced — and for
    the same reason there is deliberately NO blanket `"6" not in text`: 6 is still legitimately the
    default instance and the fallback.

    MUTATION-CHECKED (`__pycache__` cleared between rounds, each run confirmed to select exactly 1
    test, SKILL.md restored to a clean `git diff` after): control PASS; re-collapse each of the
    three `2 × wip.limit` sites to a bare 6 -> FAIL, one at a time, each on its own message; delete
    the diagnosis command line from the fence -> FAIL; delete the fence's empty-range `call_human`
    branch -> FAIL; revert the escalation to the count-only «шесть кругов подряд» spelling -> FAIL;
    delete the `бери 6` fallback -> FAIL; drop `wip.limit` out of the dispatch brief -> FAIL; rename
    the `wip` payload key in workflow.py -> FAIL. (559 re-ran the two rounds its edits touch: delete
    the reworded fallback constant -> FAIL; delete the fence's empty-range `call_human` branch,
    which now sits one step further down the diagnosis -> FAIL.)"""
    text = _skill_text()
    flat = _flat(text)
    recipe = _integration_recipe(text)
    src = _workflow_src()

    # the three sites that carry the ceiling itself
    assert "another round, up to `2 × max(wip.limit, wip.active)`)" in flat, \
        "the parallel-drain rule no longer states the `2 × max(...)` integration retry ceiling"
    assert "up to 2 × max(wip.limit, wip.active) rounds" in recipe, \
        "the integration recipe's push step no longer states the `2 × max(...)` retry ceiling"
    assert 'the rounds have run out (`2 × max(wip.limit, wip.active)`, see "Where the ceiling ' \
        'comes from")' in flat, \
        "the escalation sentence no longer spends the `2 × max(...)` rounds before call_human"
    assert "Name `wip.active` from that same response" in flat, \
        "the dispatch brief no longer carries wip.active — the ceiling collapses back to the limit"

    # the diagnosis: a round is owed only for a race that was LOST, and the count cannot say
    assert "git log --oneline HEAD..origin/main" in recipe, \
        "the recipe no longer diagnoses WHO won the race before spending a round"
    assert "call_human" in recipe, \
        "the recipe no longer escalates straight away when the range is empty (no race at all)"
    assert '"N rounds in a row, and here is what landed' in flat, \
        "the escalation asks with a COUNT again — the human needs the LIST of what won each round"

    # the variable the formula reads, and the fallback for when the brief does not carry it
    assert "`wip.limit` from the `next_task` response" in flat, \
        "the dispatch brief no longer carries wip.limit — every agent falls back to the default"
    assert "— **take 6**" in flat, \
        "the brief-less fallback is gone — `2 × wip.limit` is then an unfillable variable"
    assert 'result["wip"] = wip' in src, \
        "SKILL.md computes the ceiling from next_task's `wip`, but with_wip stopped attaching it"
    assert '"limit": limit' in src, \
        "SKILL.md computes the ceiling from `wip.limit`, but the payload lost its `limit` field"

    for old in ("another round, up to 3)", "up to 3 rounds", "3 rounds in a row"):
        assert old not in text, \
            f"the reverted 3-round ceiling is back in SKILL.md ({old!r}) — see this test's docstring"


def _claude_md_text() -> str:
    """The repo's own CLAUDE.md — the SECOND, independent copy of the retry-ceiling derivation.

    Read off the working tree via `parents[2]`, the same way the freshness pin above reaches
    `SKILL_SOURCE_PATH`, and NOT through `importlib.resources`: CLAUDE.md is not packaged, it is a
    repo file, so its absence means the checkout is not what this suite assumes rather than a
    packaging change — asserted, so that case says so instead of surfacing as an OSError."""
    path = Path(__file__).resolve().parents[2] / "CLAUDE.md"
    assert path.is_file(), "CLAUDE.md is gone from the repo root — this pin has nothing to read"
    return path.read_text(encoding="utf-8")


def _claude_ceiling_paragraph(text: str) -> str:
    """CLAUDE.md's one paragraph that derives the integration retry ceiling.

    Sliced to that paragraph like `_gc_section` / `_drain_width_section` slice SKILL.md, and for
    the same measured reason: `wip_limit`, the default 3 and the words `2 ×` all appear elsewhere
    in this file (the config bullet states the default and its precedence, the dogfood section
    states this repo's own limit), so a whole-file scan could not tell "the derivation is still
    stated" from "those numbers survive somewhere". Width is asserted, not assumed — a slice that
    silently widened to the whole file would restore exactly the weakness it exists to remove."""
    start = text.find("**That bump commit is also a racer")
    assert start != -1, (
        "CLAUDE.md no longer opens its retry-ceiling paragraph where this pin can find it. If the "
        "paragraph was legitimately reworded, move this anchor — do not delete the check"
    )
    end = text.find("\n\n", start)
    assert end != -1, "the ceiling paragraph no longer ends where the next paragraph begins"
    paragraph = text[start:end]
    assert 0 < len(paragraph) < len(text), "the ceiling slice is not a proper subset of CLAUDE.md"
    assert "ci-skip marker" not in paragraph, \
        "the slice swallowed the following paragraph — the prose it exists to exclude"
    return paragraph


def _ceiling_derivation_section(text: str) -> str:
    """SKILL.md's «Откуда потолок» bullet — the rulebook's own copy of the same derivation.

    Scoped to the one bullet, like `_two_returns_rule`: the neighbouring bullets talk about the
    ceiling too (the escalation bullet spends it, the race-diagnosis bullet decides whether a
    round is owed at all), and the brief-less `**бери 6**` puts a bare 6 in a THIRD place, so a
    whole-file number hunt would mix the derivation's numbers with numbers that are not it.

    That third place lives INSIDE this slice, so anything added to the fallback sentence is read by
    the table regexes below. VMCP-102 (559) rewrote it and deliberately kept the `при <n> — <m>`
    shape out of the new prose; a future edit must do the same or move the regexes."""
    start = text.find("- **Where the ceiling comes from and why it is")
    assert start != -1, (
        "SKILL.md no longer opens its «Откуда потолок» derivation where this pin can find it. If "
        "the bullet was legitimately reworded, move this anchor — do not delete the check"
    )
    end = text.find("\n  - **", start + 1)
    assert end != -1, "the derivation bullet no longer ends where the next bullet begins"
    section = text[start:end]
    assert 0 < len(section) < len(text), "the derivation slice is not a proper subset of SKILL.md"
    assert "Hit the ceiling" not in section, "the slice swallowed the escalation bullet after it"
    return section


def test_the_ceiling_numbers_in_both_files_re_derive_from_their_own_formula():
    """VMCP-99 (556): the test above pins the rulebook's three OPERATIVE `2 × wip.limit` sites as
    STRINGS, which is all a string can do — it never evaluates them. Two gaps follow from that, and
    card 556 was filed for one of them: CLAUDE.md carries a SECOND, independent write-up of the same
    derivation (the release section's "that bump commit is also a racer" paragraph), and nothing in
    this repo reads CLAUDE.md at all — measured while writing this test, `grep -rn "CLAUDE.md"
    tests/ scripts/` returned only prose mentions inside docstrings. So the copy that actually
    DRIFTED had no mechanical net whatever, and the numbers each file states about its own formula
    (5 vs 6 at the default, 2 / 8 / 10 at limits 1 / 4 / 5) had none either.

    What drifted is worth stating precisely, because it is subtle and it will recur: the paragraph
    quoted the WORST MECHANICAL RUN where the CEILING belongs. Those are two different quantities of
    one derivation — at `wip_limit = N` the worst purely mechanical run is 2·(N−1)+1 rounds
    (2·(N−1) sibling+bump losses, plus the trailing bump of the landing that beat you to the
    `fetch`), and the ceiling must sit STRICTLY ABOVE it or it fires on arithmetic, giving 2 × N. At
    the default 3 they read 5 and 6 — adjacent, both plausible, and indistinguishable by eye. That
    is why this pin RE-DERIVES rather than matches: a substring pin on "6" would be satisfied by the
    very confusion the card is about, since 6 is also a correct number elsewhere in the same
    sentence. A number you cannot reproduce from the formula printed next to it must fail.

    So each file is parsed for what it claims about itself and the claims are recomputed in Python:
    every stated (limit → ceiling) pair against `2 × limit`, the stated worst run against
    `2 × (default − 1) + 1`, and the strictly-above step BETWEEN them — the step whose absence was
    the defect. The default the prose reasons about is anchored on `config.DEFAULT_WIP_LIMIT`, the
    one code fact in this rule (tracker #524 made an unset `wip_limit` mean 3, not "no gate"), so
    re-valuing it drags both documents along instead of leaving them quietly describing a default
    the code stopped having. Finally the two tables are compared AS WHOLE MAPPINGS — deliberately
    not "where they overlap", the form review MEASURED useless: the per-file re-derivations already
    force each table to {n: 2n} on its own, so agreeing VALUES at a shared limit are implied, and
    an overlap-scoped comparison therefore cannot fail for any input while missing the divergence
    that IS possible — one file's table narrowing. Dropping `10 at 5` from CLAUDE.md while the
    shipped rulebook kept it passed green under that weaker form. Neither file's internal
    consistency can see the other going its own way; only the whole-mapping comparison can.

    The parsed pair COUNT is asserted before anything loops over it. Without that, a regex that
    stopped matching — a re-wrap, a reworded table — would make "every stated ceiling is correct"
    pass over an EMPTY set, i.e. go green precisely when the prose moved out from under the pin.
    That is the vacuous-pin failure mode this module has measured before (see `_calls_in`), and it
    is the one a numeric pin is most exposed to.

    Deliberately OUT of scope, and NOT pinned here: the brief-less fallback sentence and the
    empty-range race diagnosis. Card 559 (since landed) rewrote both, and a pin laid over prose
    another card is about to rewrite is a merge conflict dressed as a test. That reasoning survives
    the landing on its own merits — the fallback's bare 6 is not a step of THIS derivation but the
    default's instance quoted for an agent whose brief carried no limit, so re-deriving it here
    would assert a different rule. 559 re-derives it in its own test, against
    `2 × config.DEFAULT_WIP_LIMIT`; `test_the_integration_retry_ceiling_is_pinned` above keeps a
    string pin on the constant surviving at all. This test stays on the (limit -> ceiling) table.

    MUTATION-CHECKED (`__pycache__` cleared between rounds, each run confirmed to select exactly 1
    test, both files restored to a clean `git diff` after): control PASS; CLAUDE.md's ceiling at the
    default 6 -> 5, i.e. card 556's original defect re-committed -> FAIL; CLAUDE.md's `8 at 4` ->
    `6 at 4`, the wider-drain case card 550 exists for -> FAIL; SKILL.md's `при 4 — 8` -> `при 4 —
    6` -> FAIL; SKILL.md's worst run at the default 5 -> 6, collapsing the two distinct numbers into
    one -> FAIL on the worst-run derivation; delete CLAUDE.md's ceiling paragraph outright -> FAIL
    from the slicer, with its own message, never a confusing crash or a silent green. Added in
    review, by CONSTRUCTING the divergence rather than reading the diff: drop `, 10 at 5` from
    CLAUDE.md's table and leave SKILL.md's `при 5 — 10` -> FAIL on the cross-file mapping. That
    round is the reason the cross-file half is shaped the way it is — it was GREEN before."""
    def re_derive(where: str, default: int, worst: int, ceilings: dict[int, int]) -> None:
        assert default == config.DEFAULT_WIP_LIMIT, (
            f"{where} derives the ceiling at a default wip_limit of {default}, but "
            f"config.DEFAULT_WIP_LIMIT is {config.DEFAULT_WIP_LIMIT} — the prose reasons about a "
            f"default the code no longer has"
        )
        assert len(ceilings) >= 3, (
            f"{where}: only {len(ceilings)} (limit -> ceiling) pair(s) parsed out of the table it "
            f"states, so every arithmetic check below would be near-vacuous. A legitimate reword "
            f"means updating this pin's regex, not deleting the check"
        )
        assert worst == 2 * (default - 1) + 1, (
            f"{where} states a worst purely mechanical run of {worst} at wip_limit {default}, but "
            f"its own cited formula 2·(N−1)+1 gives {2 * (default - 1) + 1}"
        )
        assert default in ceilings, (
            f"{where} states a worst run at wip_limit {default} but no ceiling for that limit, so "
            f"the two numbers this card is about can no longer be compared at all"
        )
        # ordered BEFORE the table loop on purpose: both fire on a bad ceiling at the default, and
        # this one names what actually went wrong the first time (see the docstring) instead of
        # reporting it as an arbitrary arithmetic slip
        assert worst < ceilings[default], (
            f"{where}: the ceiling at wip_limit {default} is {ceilings[default]}, which is NOT "
            f"strictly above the worst mechanical run of {worst}. That is card 556 exactly — the "
            f"worst run quoted where the ceiling belongs sends an agent to a human on arithmetic"
        )
        for limit, ceiling in sorted(ceilings.items()):
            assert ceiling == 2 * limit, (
                f"{where} states a ceiling of {ceiling} at wip_limit {limit}, but the formula it "
                f"cites in the same breath, 2 × wip_limit, gives {2 * limit}. Fix the number; if "
                f"the FORMULA itself changed, change it in BOTH files and here"
            )

    # --- CLAUDE.md: the release section's racer paragraph
    claude = _flat(_claude_ceiling_paragraph(_claude_md_text()))
    worst_match = re.search(r"\*\*(\d+)\*\* at the default (\d+)", claude)
    assert worst_match, (
        "CLAUDE.md's ceiling paragraph no longer states the worst MECHANICAL run at the default "
        "limit in a shape this pin can read. Reword freely — but update this regex, do not drop it"
    )
    claude_worst, claude_default = int(worst_match.group(1)), int(worst_match.group(2))
    table = re.search(r"the ceiling is \*\*`2 × wip_limit`\*\*:(.*?)\.", claude)
    assert table, (
        "CLAUDE.md no longer follows its ceiling formula with the (limit -> ceiling) table this "
        "pin re-derives. Reword freely — but update this regex, do not delete the check"
    )
    claude_ceilings: dict[int, int] = {}
    for entry in table.group(1).split(","):
        numbers = re.findall(r"\d+", entry)
        assert len(numbers) == 2, (
            f"CLAUDE.md's ceiling-table entry {entry.strip()!r} is not the '<ceiling> at <limit>' "
            f"shape this pin parses; update the regex rather than removing the arithmetic check"
        )
        claude_ceilings[int(numbers[1])] = int(numbers[0])
    re_derive("CLAUDE.md's ceiling paragraph", claude_default, claude_worst, claude_ceilings)

    # --- SKILL.md: «Откуда потолок», the same derivation written for agents
    skill = _flat(_ceiling_derivation_section(_skill_text()))
    default_match = re.search(
        r"At the default limit of (\d+) the worst mechanical run equals (\d+) "
        r"and the ceiling is \*\*(\d+)\*\*",
        skill,
    )
    assert default_match, (
        "SKILL.md's «Откуда потолок» no longer states the worst run AND the ceiling at the default "
        "limit in a shape this pin can read. Reword freely — but update this regex, do not drop it"
    )
    skill_default, skill_worst = int(default_match.group(1)), int(default_match.group(2))
    skill_ceilings = {skill_default: int(default_match.group(3))}
    narrow = re.search(r"at a limit of (\d+) the ceiling is (\d+)", skill)
    assert narrow, (
        "SKILL.md's «Откуда потолок» no longer states the sequential case (limit 1), the instance "
        "that proves the rule is a formula and not the default's constant; update this regex"
    )
    skill_ceilings[int(narrow.group(1))] = int(narrow.group(2))
    for limit, ceiling in re.findall(r"at (\d+) it is (\d+)", skill):
        skill_ceilings[int(limit)] = int(ceiling)
    re_derive("SKILL.md's «Откуда потолок»", skill_default, skill_worst, skill_ceilings)

    # --- and the two copies of one derivation must tabulate the SAME rule.
    # Compared as WHOLE mappings, not "where they overlap", which is the form review measured
    # useless: both re_derive calls above force each dict to {n: 2n} INDEPENDENTLY, so equal
    # VALUES at a shared limit are already implied and a per-limit value loop could not fail for
    # any input. What is NOT implied is the KEY SET — one file's table narrowing away from the
    # other — and that is a real divergence: dropping `10 at 5` from CLAUDE.md while the shipped
    # rulebook keeps it passed GREEN under the overlap-only comparison this replaced. Hence one
    # assertion that can actually fire, instead of a loop that cannot.
    assert claude_ceilings == skill_ceilings, (
        f"CLAUDE.md and SKILL.md no longer state the same (limit -> ceiling) table: CLAUDE.md has "
        f"{sorted(claude_ceilings.items())}, SKILL.md has {sorted(skill_ceilings.items())} — "
        f"limits only one of them tabulates: {sorted(set(claude_ceilings) ^ set(skill_ceilings))}. "
        f"Only SKILL.md self-heals onto every consumer, so a table that narrows on one side leaves "
        f"agents and maintainers reading different rules; restore the missing rows, or move BOTH "
        f"copies together (and this pin's regexes with them)"
    )


def _shared_resources_section(text: str) -> str:
    """The shared-resource section, plus the browser rules that moved out of it.

    The section itself stayed in the core; only its browser subsection became
    `references/browser.md`, so the pins about shared resources still see both.
    """
    start = text.find("## Shared resources: a worktree isolates FILES")
    assert start != -1, "the shared-resource section is no longer where this pin can find it"
    end = text.find("\n## ", start + 1)
    assert end != -1, "the shared-resource section no longer ends at the next top-level heading"
    section = text[start:end]
    assert 0 < len(section) < len(text), "the slice is not a proper subset of SKILL.md"
    assert "## Who does the work" not in section, "the slice swallowed the following section"
    # The browser subsection used to close this section and now lives in `references/browser.md`.
    # APPENDED rather than merged, because one pin below asserts ORDER — shared-resource rules
    # first, browser rules after — which is the layout the section always had.
    return section + "\n" + _reference("browser.md")


def test_the_shared_browser_rule_stays_detectable_rather_than_wishful():
    """VMCP-97 (554): the worktree isolates the working copy and NOTHING else, so a per-task agent
    under the parallel drain shares the browser, the scratch dir and any fixed port/container name
    with its siblings. Measured while writing the card: one `@playwright/mcp` per `claude` process
    (siblings are subagents of that one session, so one browser / one profile / one current page
    for all of them); no isolation parameter on any browser tool; `--isolated` / `--user-data-dir`
    are SERVER LAUNCH args, i.e. out of an agent's reach — and even there they isolate per MCP
    client, not per subagent. Interference therefore cannot be PREVENTED from inside an agent,
    only DETECTED, and every browser response prints `Page URL:` to detect it with.

    That asymmetry is what this pins, because it is the clause a later tidy-up would drop as
    belt-and-braces while leaving the reassuring half ("work in one burst") in place — turning a
    detectable failure into a silent one. And it is not merely inconvenient: the rulebook tells
    agents to ATTACH a verification screenshot as evidence, so a stolen page becomes a sibling's
    screenshot approved as this card's proof. `attach_file` is the two-sided anchor (the rule
    hands it a path in the MAIN checkout, since artifacts land in the MCP server's cwd, not in the
    agent's worktree) — rename it in workflow.py and `test_attachment_upload_rule_names_the_tool`
    goes red alongside this.

    MUTATION-CHECKED (`__pycache__` cleared between rounds, each round confirmed to select exactly
    1 test, and the section restored from a COPY rather than `git checkout` — the section is
    uncommitted while it is being written, so a git restore silently deletes the very thing under
    test and every later round then "fails" from the slicer instead of from its mutation, which is
    how the first attempt at this list produced three worthless greens-in-red):
    control PASS; soften the `Page URL` check to "compare the address" -> FAIL; drop the
    `attach_file` clause -> FAIL; drop the "не зови вовсе" ban on browser_close/browser_resize ->
    FAIL; drop the `browser_tabs` "a tab is not isolation" clause -> FAIL; rename the section
    heading -> FAIL loudly from the slicer, never silently green; reword the surrounding prose
    without touching the pinned clauses -> PASS (by design: wording is review's job, per this
    module's docstring). Each failure was read for its MESSAGE, not just its colour — a round that
    fails from the slicer proves nothing about the clause it claims to pin.

    REWORK (review of the first attempt): the check as first shipped FAILED OPEN. It claimed
    "every browser response prints `Page URL:`", which is false for `browser_take_screenshot` —
    the one call the evidence rule names. Measured on an own isolated server across four server
    configurations (default, `--snapshot-mode none`, `--output-dir`, no tab navigated yet):
    screenshot NEVER prints the line, snapshot ALWAYS does. The cause is in playwright-core's
    response renderer: the `Page` section renders when `_includeSnapshot !== "none" ||
    tabHeaders.some(h => h.changed)`; screenshot leaves `_includeSnapshot` at its "none" default
    and so depends on `changed` — a flag ANY previously serialized response consumes, a sibling's
    included — while `browser_snapshot` calls `setIncludeFullSnapshot()` ("explicit"), which
    satisfies the first disjunct unconditionally, config-independently. So an agent looking for
    the line in a screenshot response finds nothing and reads it as "no mismatch": absence of
    evidence taken for evidence of absence, the same shape as a `git rev-parse` that merely echoes
    its argument back. Hence the two pins added here: the rule must name `browser_snapshot` as
    what it checks WITH, and must say outright that a missing line is not confirmation.

    And the pin itself was the nit: it held the TOKEN `Page URL`, so a mutation that kept the
    token while deleting the imperative ("сверь / перейди заново / вывод не делай") passed. The
    branch an agent executes on a MISMATCH is the rule; the token is only where it reads it. Both
    are pinned now, which is what makes the mutation below bite.

    The same weakness then bit the INHERITED `attach_file` pin, and only a mutation round found
    it: this rework added a second mention of `attach_file` (the verify-before-attach clause), so
    `"attach_file" in section` was satisfied even after the clause naming WHICH path — absolute,
    in the main checkout — was deleted. Adding prose can silently defang a pin that was honest
    when it was written; the pin now holds that clause, not the word. Rounds added for both:

    T1, on top of the list above: delete the "зови `browser_snapshot` и сверяй `Page URL`"
    instruction while LEAVING the token in the section -> FAIL (this is the nit's round); soften
    "не печатает `Page URL` НИКОГДА" -> FAIL; drop "Нет строки — нет подтверждения" -> FAIL; gut
    the mismatch branch ("перейди заново, пересними, вывод не делай") -> FAIL; drop only "вывод
    не делай" -> FAIL; put the disproved "В каждом ответе печатается `Page URL:`" claim back ->
    FAIL on the negative pin; delete the attach_file PATH clause while the bare token survives
    elsewhere in the section -> FAIL.

    #703 moved the `attach_file` path and added the two clauses under it. Its residual is the one
    #629 recorded and could not close: four tools write the page's own TEXT under a caller-chosen
    name, and neither an extension list nor a magic-byte scan reaches plain text. The remedy is a
    place rather than a rule — the artifact goes into the already-ignored output dir — which makes
    it a change to what the RULEBOOK says, and therefore this pin's business. What the pin holds is
    the INSTRUCTION and its measured precondition; the VALUE it prescribes is checked against this
    repo's actual ignore rules by `test_every_filename_skill_md_prescribes_is_excluded_by_this_
    repos_gitignore`, and the split matters because the sweep showed each half green while the
    other was gutted.

    MUTATION-CHECKED for those three, in a `git clone --no-hardlinks` of this branch (never
    `cp -R`), `vikunja_mcp.__file__` confirmed to be the clone's each round, `__pycache__` deleted
    per round plus `PYTHONDONTWRITEBYTECODE=1`, sources restored from a COPY, selection this file
    plus `test_repo_browser_isolation.py` (112 tests): control 0 failed; gut the prefix
    INSTRUCTION ("`filename` ВСЕГДА давай с префиксом") while leaving both prescribed VALUES
    intact -> 1 failed, this test alone — the round that shows the two pins do not cover for each
    other, since the git-backed one stays green on unchanged values; drop the mkdir/ENOENT
    precondition -> 1 failed, this test; revert the `attach_file` clause to its pre-#703 root
    wording -> 1 failed, this test; delete the fenced `mkdir` RECIPE while keeping both of its
    prose clauses -> 1 failed, this test; delete the "Граница этого правила" bullet -> 1 failed,
    this test. The last two rounds exist because an independent pass whose brief was to defeat
    these pins performed exactly those deletions against the first version and measured 0 failed
    each: the recipe is the only part that gets the directory right from a linked worktree, and
    the boundary clause is what stops a rule being read as a lock."""
    section = _shared_resources_section(_skill_text())
    flat = _flat(section)
    # WHAT to read the page identity from — pinned as the INSTRUCTION, not as the token:
    # `Page URL` and `browser_snapshot` each occur elsewhere in this very section (the
    # no-isolation-parameter bullet names both tools), so a bare-token pin is satisfied by
    # prose that instructs nothing. That is the nit review raised, and this is its fix.
    assert "call `browser_snapshot` and cross-check the `Page URL`" in flat, \
        "the rule no longer tells the agent to verify WITH browser_snapshot — `Page URL` is the " \
        "one line browser_take_screenshot never prints, so the check would be looking at nothing"
    assert "`browser_take_screenshot` NEVER prints `Page URL`" in flat, \
        "the rule no longer states that browser_take_screenshot never prints `Page URL` — an " \
        "agent that looks for it there finds nothing and reads that as 'no mismatch'"
    assert "No line — no confirmation" in flat, \
        "the rule no longer says a MISSING line is not confirmation — that is the fail-open " \
        "this rework exists to close (absence of evidence read as evidence of absence)"
    # WHAT TO DO about a mismatch: the branch IS the rule
    assert "navigate again, re-shoot" in flat, \
        "the rule may still carry the `Page URL` token but no longer says what to DO when it " \
        "does not match (re-navigate and re-shoot) — a token is a word, not an instruction"
    assert "draw no conclusion" in flat, \
        "the rule no longer forbids CONCLUDING from a page that may be a sibling's — " \
        "detect-don't-prevent is worthless if the agent may still use what it saw"
    assert "Give `attach_file` an ABSOLUTE path `<main checkout>/.playwright-mcp/<name>`" in flat, \
        "the browser rule no longer says WHICH path attach_file must be given (absolute, in " \
        "the MAIN checkout's `.playwright-mcp/`) — the bare token now also occurs in the " \
        "verify-before-attach clause, so pinning the word alone would survive deleting the path " \
        "rule. #703 moved this path INTO the output dir, and the two halves are one instruction: " \
        "an agent told to FETCH the artifact from the checkout root is being told, implicitly, to " \
        "have WRITTEN it there. That the screenshot in particular would then be caught by `*.png` " \
        "is luck of the format, and the same sentence covers the text dumps, which nothing catches"
    # #703: WHERE the artifact must be written in the first place, and the precondition that
    # makes the instruction executable. The value itself is checked against this repo's ignore
    # rules next door, by `test_every_filename_skill_md_prescribes_is_excluded_by_this_repos_
    # gitignore` — that pin holds the PATH, these hold the INSTRUCTION and its measured gotcha.
    assert "ALWAYS give `filename` with the `.playwright-mcp/` prefix" in flat, \
        "the rule no longer tells the agent WHERE a caller-chosen `filename` must point. That " \
        "directory is the only axis covering the four tools that write the page's TEXT " \
        "(snapshot/console/network/evaluate): their names are `.md`/`.txt`/`.json`/none and " \
        "their bytes have no signature, so #629's two layers reach neither"
    assert "CREATE the directory before that" in flat and "ENOENT" in flat, \
        "the rule no longer states that the directory must EXIST first. Measured on " \
        "@playwright/mcp 0.0.78: a caller-chosen filename is resolved by a function that does " \
        "not mkdir (unlike the auto-named path, which does), so on a missing `.playwright-mcp/` " \
        "every such call fails with ENOENT. Without this clause the prescription above reads as " \
        "working and silently depends on some earlier call having created the directory"
    assert 'mkdir -p "$MAIN/.playwright-mcp"' in section and "git worktree list" in section, \
        "the mkdir clause kept its sentence and lost its RECIPE. The sentence says the directory " \
        "must exist; the two lines say WHERE, and from a linked worktree — where every per-task " \
        "agent stands — that is the one part an agent cannot reconstruct, because the artifact " \
        "goes to the MCP server's workspace and not to the tree it is standing in. An attack pass " \
        "deleted this fenced block with both prose pins intact and the whole suite stayed green"
    assert "The bound of this rule" in flat and "a RULE, not a lock" in flat, \
        "the rule lost the clause saying what it does NOT do — that a bare name is still accepted " \
        "and the spill is not root-confined. That is not decoration: this whole card exists " \
        "because #629's first draft claimed a completeness it did not have, and a rule read as a " \
        "lock is one nobody double-checks. Measured deletable with everything else green"
    for tool in ("browser_close", "browser_resize"):
        assert tool in section, f"the rule no longer bans {tool} — it destroys a sibling's state"
    assert "browser_tabs" in section, \
        "the rule no longer explains that a tab is not isolation (global, shifting indices)"
    # the disproved claim this rework removed must not come back
    assert "response prints `Page URL" not in flat, \
        "the disproved claim that EVERY browser response prints `Page URL:` is back — it is " \
        "false for browser_take_screenshot, and it is what made the check fail open"


def test_the_shared_resource_rules_name_a_knob_the_agent_can_actually_reach():
    """The sibling failure this card was warned about: a rule that names a knob the agent cannot
    reach is worse than no rule. So the two collisions that ARE fixable from inside must keep
    their concrete recipe, not a platitude — a fixed container name and a fixed host port were
    both reproduced (`Conflict. The container name … is already in use`, `Bind for 0.0.0.0:3456
    failed: port is already allocated`), and the scratch dir was measured shared (179 entries
    written by different agents of one session in a day).

    The fenced recipe is checked to be a DIFFERENT fence from the integration recipe: `sh` blocks
    are what agents copy, and `_integration_recipe` asserts there is exactly one containing the
    push refspec. Adding a second `sh` fence to this file is safe only while it stays clear of
    that string, and this makes the two invariants fail independently instead of one silently
    invalidating the other's slice.

    MUTATION-CHECKED alongside the test above, same discipline: control PASS; replace `docker rm
    -f` with a "clean up afterwards" comment -> FAIL; replace the id-derived name and port with
    fixed ones (`vikunja-test-agent`, `PORT=23456`) -> FAIL; paste the push refspec into this
    section's fence -> FAIL here AND in
    `test_the_recipe_verifies_the_evidence_sha_actually_landed_on_main` ("expected exactly 1
    fenced integration recipe, found 2"), which is precisely the cross-invariant collision this
    assertion exists to make loud."""
    section = _shared_resources_section(_skill_text())
    # The two DERIVATIONS, not the tokens: `id` is a substring of ordinary English prose and `$ID`
    # survives elsewhere in the section (the scratchpad bullet's role suffix), so the token form is
    # satisfied by a recipe that has gone back to the fixed name and port — measured, that exact
    # mutation left the token pin GREEN.
    assert "NAME=vikunja-test-$ID" in section and "PORT=$((20000 + ID" in section, \
        "the isolate-by-task-id recipe lost the task id it derives every shared name from"
    assert "docker rm -f" in section, \
        "the recipe no longer cleans up — a leaked container holds its name and port all day"
    assert "git push origin HEAD:main" not in section, \
        "this section's sh fence must not contain the push refspec — it would break the " \
        "exactly-one-integration-recipe invariant (_integration_recipe)"
    _integration_recipe(_skill_text())  # still exactly one, and still not this one


def _drain_width_section(text: str) -> str:
    """The «Ширина дренажа» bullet that explains what `limit` gates — sliced to that one item.

    Scoped like `_gc_section` / `_tick_step_3`, and for the same measured reason: `wip.limit`,
    `claim` and `active` appear all over the rulebook (the queue-discipline bullet, the parallel
    drain, the retry ceiling), so a whole-file substring could not tell "the rule is still stated
    where the pump reads the payload" from "some other section happens to use the words"."""
    start = text.find("- **`limit` is a gate on ONE transition (`claim`)")
    assert start != -1, \
        "the rulebook no longer states that `limit` gates ONE transition, not the active count"
    end = text.find("\n- **`wip_saturated", start)
    assert end != -1, "the drain-width slice no longer ends where the wip_saturated bullet begins"
    section = text[start:end]
    assert 0 < len(section) < len(text), "the drain-width slice is not a proper subset of SKILL.md"
    assert "wip_saturated" not in section, \
        "the slice swallowed the next bullet — the prose it exists to exclude"
    return section


def test_the_wip_overshoot_the_rulebook_describes_is_one_the_code_produces():
    """VMCP-80 (529): `wip_limit` reads as an invariant on the active count everywhere it is
    written, but it is a gate on ONE transition — `claim`. `review_task(verdict='needs_work')`
    moves a card Review→Build without passing it, so `next_task` can honestly report an `active`
    of 4 against a `limit` of 3. Described, not quoted: those two live inside the NESTED `wip`
    sub-dict, never at the top level, and the third key there is `free` — which this sentence used
    to drop while rendering the pair as a payload literal (VMCP-170 (694)). Dropping `free` is the
    worst key to drop here, since `free = max(0, limit - active)` is exactly what collapses "full"
    and "over budget" into one 0. Measured: `{"active": 4, "limit": 3, "free": 0}` under
    `res["wip"]`. That behaviour is correct and deliberately unchanged (rework must
    be receivable at the limit or reviewed work strands); what shipped wrong was the documentation.

    So this pin does not compare strings alone — it DRIVES the real `Workflow` into the overshoot
    and checks the rulebook's claims against what came out. That is the point: the four sentences
    this card added to SKILL.md, `claim`'s tool docstring, CLAUDE.md and the drain design spec all
    assert a runtime state, and SKILL.md self-heals onto every consumer with no review gate of its
    own. If a later change makes the overshoot impossible (a second gate on the bounce, a clamped
    count), the rulebook does not go stale quietly — this goes red first.

    MUTATION-CHECKED (`__pycache__` cleared between rounds, each run confirmed to select exactly
    1 test): control PASS; clamp `active` to the limit in the wip payload -> FAIL; gate
    `review_task`'s needs_work bounce on the WIP limit -> FAIL; drop the over-budget clause from
    next_task's resume note -> FAIL; delete the rule from the drain-width bullet -> FAIL; delete
    the paragraph from claim's tool docstring -> FAIL; rename the bullet's opening words so the
    slice cannot find it -> FAIL (loudly, with its own message, never silently green).

    Deliberately NOT pinned: the two OTHER paths into the overshoot (a human moving a card out of
    Your Call, a lowered `wip_limit`) — neither is a tool call, so neither is expressible as a
    contract between the rulebook and workflow.py. They are covered behaviourally in
    tests/unit/test_workflow_wip.py, which is also where the "advance(to='build') is NOT such a
    path" correction lives."""
    section = _drain_width_section(_skill_text())
    assert "gate on ONE transition (`claim`)" in _flat(section), \
        "the drain-width rule no longer says WHICH transition the limit gates"
    assert "`active` LEGITIMATELY runs HIGHER than `limit`" in _flat(section), \
        "the rulebook no longer states that active may legitimately exceed limit"
    assert "NOT board corruption" in section, \
        "the rulebook no longer tells the pump that an overshoot is not board corruption"
    assert "max(0, limit − active)" in _flat(section), \
        "the rulebook no longer explains why `free` cannot show the overshoot"

    api = FakeAPI(buckets=workflow.STAGES)
    wf = workflow.Workflow(api, project_id=3, wip_limit=3)

    def claim_fresh(title):
        task_id = api.add_task(title, "Queue")["id"]
        wf.claim(task_id)
        return task_id

    bounced = claim_fresh("reviewed, then bounced")
    wf.advance(bounced, to="build", spec="…")
    wf.advance(bounced, to="review", worklog="…", evidence="0" * 40)
    for n in range(3):                       # the pump refills the freed slot, as the tick does
        claim_fresh(f"held {n}")
    assert wf.next_task()["wip"] == {"active": 3, "limit": 3, "free": 0}, "precondition: full"

    # the bounce goes AROUND the gate — no ownership, no claim, no slot check
    wf.review_task(bounced, verdict="needs_work", report="not yet")
    over = wf.next_task()
    assert over["wip"] == {"active": 4, "limit": 3, "free": 0}, \
        "the rulebook documents active > limit, but the code no longer produces it"
    assert over["wip"]["free"] == 0, "free saturates at 0 — the reason the rule has to exist"

    # and the payload says so where the pump reads it, exactly as the rulebook promises
    assert "against a limit of 3" in over["note"], \
        "SKILL.md promises next_task's note discloses the overshoot, but the note dropped it"

    # documenting it is not permission: the gate still refuses, with the TRUE count
    with pytest.raises(workflow.WorkflowError, match=r"WIP limit reached \(4/3\)"):
        wf.claim(api.add_task("one too many", "Queue")["id"])

    # the same rule must reach an agent reading the TOOL, not just the rulebook
    claim_doc = inspect.getdoc(server.claim) or ""
    assert "NOT an invariant on the active count" in claim_doc and "review_task" in claim_doc, \
        "claim's tool docstring no longer says the WIP gate guards one transition, not the count"


def test_the_browser_answer_leads_with_the_isolation_an_agent_can_launch_itself():
    """The human's card asked for parallel agents to PARALLELISE their tools — "playwright should
    launch so it does not disturb the others". The first attempt answered "cannot be done": true
    of the SHARED MCP browser (no isolation parameter on any tool; `--isolated`/`--user-data-dir`
    isolate per MCP client, and all siblings are one client), but false of the request, which
    review disproved by simply doing it. Reproduced here before documenting: `npx -y
    @playwright/mcp@latest --isolated --headless` from an own cwd is ready in ~1s and gives REAL
    per-agent isolation — two servers run at once, the first's page survived two `navigate`s by
    the second, and `browser_close` on the first did nothing to the second. For the common case
    (a screenshot as evidence) there is no MCP at all: `npx -y playwright@latest screenshot
    --channel=chrome URL file.png` takes ~2s, writes into the agent's OWN worktree and exits by
    itself; two concurrent runs from different worktrees both returned rc=0 with both files
    intact, and no browser process leaked.

    So the deliverable must LEAD with the launchable answer and keep detect-don't-prevent as the
    fallback for agents that use the shared server anyway. Order is the assertion: a later edit
    that demotes "launch your own" back to a conditional footnote — which is exactly how the
    first attempt buried it — restores the wrong answer to the card while every keyword still
    appears somewhere in the section. `--channel=chrome` is pinned because it is load-bearing,
    not decoration: without it the CLI refuses with "npx playwright install" (that refusal was
    reproduced), and a recipe an agent cannot run is worse than none — this section's other test
    exists for that same reason.

    MUTATION-CHECKED, same discipline as the two tests above (`__pycache__` cleared, exactly 1
    test selected per round, section restored from a COPY — never `git checkout`, which deletes
    the subject under test — and every failure read by its MESSAGE): control PASS; swap the two
    subsections so the shared-browser rules come first -> FAIL on the ordering assert; strip
    `--channel=chrome` from the screenshot RECIPE -> FAIL; strip `--isolated` from the launch
    RECIPE -> FAIL; strip `--headless` -> FAIL; restore the old "изнутри его изолировать НЕЛЬЗЯ"
    framing -> FAIL on the negative pin; reword the costs prose without touching the recipes ->
    PASS.

    Two of those rounds are why the flags are matched inside the fence. Written first as
    section-wide substrings, they stayed GREEN while the flag was stripped from the command an
    agent copies — `--channel=chrome` survives in the sentence explaining why it is required, and
    `--isolated` survives in THREE places including the bullet that says the flag is out of reach
    on the shared server. A pin satisfied by the prose about a flag, while the runnable line has
    lost it, is the same defect this card was returned for: a check that reports success from the
    wrong evidence."""
    section = _shared_resources_section(_skill_text())
    own = section.find("#### Your own browser")
    shared = section.find("#### The shared browser")
    assert own != -1, \
        "the section no longer has a 'свой браузер' subsection — the card's answer (an agent " \
        "CAN have its own browser) is gone, leaving only the disproved 'cannot be done'"
    assert shared != -1, \
        "the shared-browser subsection is gone — agents that use the session browser anyway " \
        "still need the detect-don't-prevent rules"
    assert own < shared, \
        "the own-browser answer no longer LEADS — demoting it below the shared-browser rules " \
        "is how the first attempt buried the one thing that actually answers the card"
    # The flags are pinned INSIDE the fenced recipes, not merely "somewhere in the section":
    # both `--isolated` and `--channel=chrome` also occur in the surrounding prose (and
    # `--isolated` occurs in the bullet explaining it is out of reach on the SHARED server —
    # the worst possible satisfier), so a section-wide substring stayed green in the mutation
    # round that stripped the flag from the runnable command. An agent copies the fence.
    fences = re.findall(r"```sh\n(.*?)```", section, re.S)
    cli = [f for f in fences if "playwright@latest screenshot" in f]
    assert len(cli) == 1, \
        "expected exactly 1 fenced one-line screenshot recipe in the shared-resources section, " \
        f"found {len(cli)} — it is the cheapest own-browser answer and the most-used one"
    assert "--channel=chrome" in cli[0], \
        "the screenshot RECIPE lost `--channel=chrome` — without it the CLI refuses with " \
        "'npx playwright install' (reproduced), so the line an agent copies does not run"
    srv = [f for f in fences if "@playwright/mcp@latest" in f]
    assert len(srv) == 1, \
        "expected exactly 1 fenced launch line for an agent's OWN playwright MCP server, " \
        f"found {len(srv)}"
    assert "--isolated" in srv[0], \
        "the own-server RECIPE lost `--isolated` — that flag is what keeps the profile in " \
        "memory and off the shared browser's disk profile; prose about it is not a command"
    assert "--headless" in srv[0], \
        "the own-server RECIPE lost `--headless` — the browser is headed by default, so a " \
        "window pops up on the human's screen"
    # #736. Same reason the two flags above are matched INSIDE the fence, measured the same way:
    # the sweep round that deleted `--output-dir` from this line — leaving the several prose
    # mentions of the flag untouched — was GREEN across both rulebook files, because the pin next
    # door (`test_every_filename_skill_md_prescribes_is_excluded_by_this_repos_gitignore`) asks
    # whether git would publish each `--output-dir` VALUE the file prints, and a prose restatement
    # is still a value.
    #
    # WHAT THIS ASSERTION IS NOT, and the first version of it said the opposite: deleting the flag
    # does NOT spill. An independent attack pass disproved that by running the server with no
    # `--output-dir` at all, and re-measured here — the DEFAULT output dir is `<cwd>/.playwright
    # -mcp/`, the ignored one. All three auto-named artifacts landed there, `git status` came back
    # empty and `git add -A --dry-run` staged nothing. (The tool says so itself: its access-denied
    # error prints `Allowed roots: <cwd>/.playwright-mcp, <cwd>`, and #585's note in the sibling
    # file recorded the same default years of cards ago.) The claim had also borrowed its evidence
    # from a round where the flag was PRESENT and pointed somewhere bad.
    #
    # So this pin guards a CONVENTION, not a leak: the recipe must NAME the directory, with the
    # per-task `<id>` in it, because the ambiguity is what #736 is about — a rulebook that says
    # nothing about where artifacts go is how `<каталог с id задачи>` got there, and a rulebook
    # that names one place keeps the own browser and the shared one on the same answer. Where it
    # POINTS is the other pin's question, not this one's.
    assert "--output-dir" in srv[0], \
        "the own-server RECIPE lost `--output-dir` — the line an agent copies no longer says " \
        "WHERE the auto-named artifacts go (`page-*.yml` is the page's aria TEXT), and #736 " \
        "exists because a recipe that leaves that to the reader gets read the unsafe way. This " \
        "is not a leak on its own: measured, the default is `<cwd>/.playwright-mcp/`, which this " \
        "repo ignores. It is the naming rule. Prose about the flag is not a command — this round " \
        "measured green on the value-checking pin next door with the flag gone from the fence"
    assert "is achievable, and it is the default answer" in _flat(section), \
        "the section no longer opens by saying the card's request — a browser that does not " \
        "disturb the siblings — IS achievable and is the default answer. That sentence is what " \
        "replaced the disproved 'cannot be done'; see this test's docstring"


def _landed_check() -> str:
    """The one command that tells "my push landed after all" from "it really did not"."""
    return "git merge-base --is-ancestor HEAD origin/main"


def _race_check() -> str:
    """550's diagnosis: WHO won the race, once the work is known not to be on main."""
    return "git log --oneline HEAD..origin/main"


def test_a_rejected_push_asks_whether_the_work_landed_before_it_escalates():
    """VMCP-102 (559): 550 taught the rulebook that an EMPTY `HEAD..origin/main` after a rejected
    push means there was no race at all — protected branch, no push rights, a hook — so retrying is
    futile and the agent should escalate at once. Correct, and its reviewer constructed every one of
    those cases. It is one state short: an empty range ALSO means the push LANDED and the client
    reported failure anyway (a 502, a dropped connection). Constructed against real local repos two
    independent ways — a multi-ref push where `main` is accepted while a second ref is declined, and
    a successful push whose remote-tracking ref is then rewound (what a client holds when the
    response is lost) — both produce an empty range, indistinguishable from the genuine `pre-receive`
    refusal used as a control. So the rule as written woke a human about finished work, in an
    unattended loop: the precise failure 550 exists to remove, one state to the left.

    `git merge-base --is-ancestor HEAD origin/main` separates them — 0 in both landed constructions,
    1 in the control — and this test pins the two properties of HOW that got written down, both of
    which were measured rather than reasoned:

    * **ORDER.** The check runs BEFORE the range is looked at, not inside its empty branch. A push
      that landed and then had a sibling land on top shows a NON-EMPTY range, reads as honest
      mechanics, and is sent round again — and that retry silently corrupts the evidence: `git
      rebase origin/main` DROPS the already-upstream commit, HEAD moves to the sibling's tip, the
      push prints "Everything up-to-date", and `git rev-parse HEAD` then reports the SIBLING's sha,
      on which both of the recipe's landing checks pass. (This is also why the card's premise that
      the old blind-retry rule "self-healed" the case is wrong: it mis-attributed evidence.) Asking
      the landed question first answers both range shapes with one command. The order assertion is
      the load-bearing one here and it is not a tautology — the two positions come from two
      different substrings and swapping the lines makes it fail, which was measured.
    * **The fetch travels WITH it.** On a stale remote-tracking ref the same command answers 1 about
      work that is on main. Staleness can only produce a false 1, never a false 0 (an old value of
      main cannot contain an unpushed commit), so it is fail-safe — but it defeats the fix, so the
      pin is on the chained `git fetch origin && …`, not on the bare command.

    The exit-1 branch is pinned UNCHANGED by `test_the_integration_retry_ceiling_is_pinned` above,
    and that is deliberate: the wording risk here is the mirror of the bug. "An empty range is
    ambiguous, check before escalating" would teach an agent to read emptiness optimistically and
    stop escalating when it should. It is not ambiguous once fetched — the control gives empty AND
    exit 1 — so the decision is the exit code, and 550's branch keeps its wording verbatim.

    CLAUDE.md is checked for the same ORDER because it carries the second write-up of this rule and
    two copies of one rule drift (the lesson of 556, one card earlier).

    MUTATION-CHECKED (`__pycache__` cleared between rounds, each run confirmed to select exactly 1
    test, both files restored to a clean `git diff` after): control PASS; delete the landed check
    from the fence -> FAIL; SWAP the two commands in the fence so the range is read first -> FAIL on
    the order assertion, which is the round that proves the two sides can disagree; strip `git fetch
    origin && ` off the landed check -> FAIL; delete the fence's exit-0 "spend no round, wake no
    one" branch -> FAIL; delete the prose bullet's exit-0 verdict -> FAIL; swap the two commands in
    CLAUDE.md's paragraph -> FAIL on the cross-file order."""
    text = _skill_text()
    flat = _flat(text)
    recipe = _integration_recipe(text)

    assert f"git fetch origin && {_landed_check()}" in recipe, (
        "the recipe no longer asks whether the push LANDED before diagnosing the race (or it "
        "dropped the fetch that makes the answer true — a stale tracking ref reports 'not landed'"
    )
    assert _race_check() in recipe, "the recipe lost 550's race diagnosis entirely"
    assert recipe.index(_landed_check()) < recipe.index(_race_check()), (
        "the recipe reads the race range BEFORE asking whether the work landed. A landed push with "
        "a sibling on top has a NON-empty range, so that order sends it round again — and the "
        "retry rebases the already-upstream commit away and mis-attributes the evidence sha"
    )
    assert "do NOT call a human" in recipe, (
        "the recipe no longer says that a landed push spends no round and wakes nobody — the exit-0 "
        "branch without its verdict is the half-stated rule an agent fills in with a guess"
    )

    assert "**Exit 0 — the work is ON MAIN**" in flat, \
        "the prose lost the exit-0 verdict: a landed push is evidence, not an escalation"
    assert "**Exit 1 — the work is not there**" in flat, \
        "the prose lost the exit-1 verdict, which is the branch that must still escalate"
    assert "THROWS AWAY your commit" in flat, (
        "the prose no longer says WHY the landed check comes first — without the dropped-commit "
        "measurement the order reads as arbitrary and gets tidied back"
    )

    claude = _flat(_claude_ceiling_paragraph(_claude_md_text()))
    assert _landed_check() in claude and _race_check() in claude, (
        "CLAUDE.md's racer paragraph no longer states both steps of the rejected-push diagnosis"
    )
    assert claude.index(_landed_check()) < claude.index(_race_check()), (
        "CLAUDE.md states the two diagnosis steps in the opposite order to the shipped rulebook. "
        "Only SKILL.md reaches agents, so the copies must not drift; move BOTH or neither"
    )


def test_the_brief_less_ceiling_reads_the_repo_toml_before_it_falls_back():
    """VMCP-102 (559), the second half: `2 × wip.limit` needs the limit, and a per-task agent does
    not call `next_task`, so 550 told an agent whose brief omitted it to assume 6. That constant is
    safe at limits 1-3 (worst mechanical runs 1, 3, 5, all below it) and breaks from 4 up, where the
    worst run is 7 — so at `wip_limit = 4` the fallback calls a human onto the pure arithmetic the
    formula was introduced to stop. 4 is the flip point, not the only bad value.

    It does not need fixing so much as demoting: `wip_limit` is repo-toml-ONLY by design (config.py
    — never env, because it is committed team policy), and the toml is COMMITTED, so git materialises
    it into every linked worktree. Verified by looking rather than assuming, since that is exactly
    where a per-task agent stands: in `…worktrees/task-<id>` the toml is present and the gitignored
    `.vikunja-mcp.env` is not. So an agent with no limit in its brief READS one, and the constant
    survives only for "there is no toml at all".

    On that remaining domain the constant is no longer a guess but the derivation evaluated: no toml
    implies no `wip_limit` (it cannot live anywhere else) implies the documented default, whose
    ceiling is 2 × it. That is what this test asserts, and it asserts it ACROSS SOURCES rather than
    within one — the number SKILL.md prints versus `config.DEFAULT_WIP_LIMIT` doubled in Python, and
    the filename SKILL.md sends the agent to versus `config.REPO_FILE`. Both pairs can move
    independently, which is the property the same-source assertion this repo keeps re-inventing
    (two sides computed from one origin, therefore unable to disagree) does not have. Re-value
    `DEFAULT_WIP_LIMIT` and the prose goes red; rename the config file and the rulebook stops
    pointing agents at a file that exists.

    The ORDER inside the sentence is asserted too, for the same reason as the sibling test above: a
    fallback quoted before the read is a fallback that gets taken.

    MUTATION-CHECKED (`__pycache__` cleared between rounds, each run confirmed to select exactly 1
    test, all files restored to a clean `git diff` after): control PASS; `config.DEFAULT_WIP_LIMIT`
    3 -> 4 with the prose untouched -> FAIL (this is the round proving the two sides are independent
    and can disagree); SKILL.md's `бери 6` -> `бери 8` -> FAIL, the same assertion from the other
    side; `config.REPO_FILE` renamed -> FAIL; delete the toml-read clause, leaving the bare constant
    -> FAIL; move the constant ahead of the read -> FAIL on the order."""
    text = _skill_text()
    section = _flat(_ceiling_derivation_section(text))

    assert config.REPO_FILE in section, (
        f"SKILL.md's brief-less ceiling no longer names {config.REPO_FILE} as the place to READ "
        f"`wip_limit` from — either the clause was dropped, or config.py renamed the file and the "
        f"rulebook now sends every agent to one that does not exist"
    )
    assert "wip_limit" in section, "the derivation no longer names the key an agent must read"
    assert config.REPO_ENV_FILE in section, (
        f"the derivation no longer contrasts {config.REPO_FILE} with the gitignored "
        f"{config.REPO_ENV_FILE}. That contrast is the whole reason the read WORKS from a linked "
        f"worktree — the toml is committed and materialised there, the env file is not — and "
        f"without it the instruction reads like a guess about a file that might be absent"
    )

    m = re.search(r"— \*\*take (\d+)\*\*", section)
    assert m, (
        "SKILL.md's last-resort ceiling is no longer stated in a shape this pin can read. Reword "
        "freely — but update this regex, do not drop the check"
    )
    fallback = int(m.group(1))
    expected = 2 * config.DEFAULT_WIP_LIMIT
    assert fallback == expected, (
        f"SKILL.md tells a brief-less agent with no repo toml to use a ceiling of {fallback}, but "
        f"'no toml' means no `wip_limit` at all, i.e. config.DEFAULT_WIP_LIMIT = "
        f"{config.DEFAULT_WIP_LIMIT}, whose ceiling by the formula both files state is {expected}. "
        f"The constant is only legitimate while it EQUALS the derivation on that one domain"
    )

    assert section.index(config.REPO_FILE) < section.index("**take "), (
        "the brief-less rule quotes its constant before it tells the agent to read the real limit. "
        "A fallback offered first is a fallback taken first — which is how a consumer at "
        "wip_limit = 4 ends up escalating on arithmetic despite having the number on disk"
    )

    assert "it will read `wip_limit` from the repo config itself" in _flat(text), (
        "the orchestrator's dispatch brief still promises the agent a bare default when the limit "
        "is not named. Both halves have to agree, or the brief keeps teaching the old behaviour"
    )
def _rule_boundary_bullet(text: str) -> str:
    """The «Граница правила» bullet — what happens when the OTHER session is not a sibling.

    Sliced out of `_shared_resources_section` rather than out of the whole file, for the reason
    every slicer here records: `.claude/settings.json` is named TWICE inside this one bullet
    (the remedy, then the note that a project-scoped file really does reach the MCP server's
    env), so even a bullet-scoped substring has to be chosen with care — and a file-wide one
    could not tell "the rule is still stated" from "the path is mentioned somewhere".

    This bullet is currently the LAST item of its section, so the slice runs to the section's
    end — which `_shared_resources_section` already bounds at the next `##` heading, and which
    is asserted to be a proper subset there. If a later bullet is appended after it, `\\n- **`
    ends the slice earlier; both shapes are correct, neither can silently widen to the file.
    """
    section = _shared_resources_section(text)
    # `- **The bound of this rule.**` is a DIFFERENT bullet in the same section (the one about
    # `.playwright-mcp/` being ignored only here), and it sits ABOVE this one — so the anchor is
    # the exact article: "the rule", not "this rule". Measured on the shipped text: one match each.
    start = section.find("- **The bound of the rule.**")
    assert start != -1, \
        "SKILL.md no longer draws the boundary of the shared-browser rules (one session's " \
        "subagents) — an agent meeting a SECOND `claude` session has nothing to read"
    end = section.find("\n- **", start + 1)
    bullet = section[start:] if end == -1 else section[start:end]
    assert 0 < len(bullet) < len(section), \
        "the rule-boundary slice is not a proper subset of the shared-resources section"
    assert "#### The shared browser" not in bullet, "the slice swallowed the subsection heading"
    return bullet


def test_the_cross_session_boundary_names_the_fix_and_not_only_the_symptom():
    """VMCP-… (558): the cross-session case is the ONE browser collision an agent cannot detect
    its way out of, and it is also the one that has a real fix — so the bullet has to carry the
    fix, not just the diagnosis.

    Measured while doing the card: `@playwright/mcp` derives its on-disk profile as
    `mcp-<channel>-<sha256(first MCP root path)[:7]>`, so two sessions collide only when they
    share a workspace ROOT; different repositories never do. The collision is loud (the second
    browser refuses to start after ~7 s of lock wait) and the remedy is one committed line —
    `PLAYWRIGHT_MCP_ISOLATED` in `.claude/settings.json`, the env equivalent of `--isolated`.

    Three clauses are pinned, each as an INSTRUCTION rather than as a token, because the failure
    mode of this bullet is not deletion but EROSION into a symptom report — "your second browser
    will not start, that is normal" — which reads as complete, ships to every consumer over the
    self-healing `stable` copy with no review gate (see this module's docstring), and leaves the
    reader believing there is nothing to do:

      * the DERIVATION (the profile formula) and the scope claim it supports. Without it the
        no-collision guarantee is an assertion an agent has no reason to trust, and the first
        person who hits an unrelated browser failure in a different repo will "generalise" the
        rule to cover it.
      * the remedy's KEY AND VALUE together. `"true"` is not decoration: playwright-core's
        `envToBoolean` accepts only `"true"`/`"1"` and silently ignores everything else, so a
        rulebook that names the variable without its value invites `"yes"` — a setting that
        looks configured and does nothing. (The repo's own settings file is pinned against the
        same fact in tests/unit/test_repo_browser_isolation.py.)
      * WHERE it goes. The bare path cannot carry this: it appears twice in this bullet, so
        pinning the token alone stays green while the sentence that says "the `env` block of
        that file" is deleted — the same defect the `attach_file` pin above was reworked for.

    Deliberately NOT pinned: the "do not add it to someone else's project silently" advice and
    the in-memory-profile cost. Both are prose judgements, which is review's job per this
    module's docstring; and pinning them would make a re-wording of the surrounding paragraph
    fail a test that is supposed to hold the RULE.

    MUTATION-CHECKED (`__pycache__` cleared between rounds, each round confirmed to select
    exactly 1 test, SKILL.md restored from a COPY — never `git checkout --`, since this card's
    edits are uncommitted and a git restore would delete the subject under test): control PASS;
    delete the `PLAYWRIGHT_MCP_ISOLATED` sentence while leaving the surrounding prose intact ->
    FAIL; keep the variable but replace "в блоке `env` файла `.claude/settings.json`" with a
    vaguer "somewhere in the project config" -> FAIL, which is the round that proves the WHERE
    clause is pinned and not just the twice-occurring path; drop the profile formula -> FAIL;
    drop the "different repositories never collide" claim -> FAIL; replace `"true"` with
    `"yes"` -> FAIL; rename the bullet's opening words so the slice cannot find it -> FAIL
    loudly from the slicer; re-wrap the paragraph ACROSS two of the pinned phrases and rewrite
    the cost sentence -> PASS, by design (`_flat` is what makes a reflow a non-event)."""
    flat = _flat(_rule_boundary_bullet(_skill_text()))
    assert "`mcp-<channel>-<sha256(workspace root)[:7]>`" in flat, \
        "the bullet no longer derives the browser profile from the workspace root — the " \
        "scope claim below it becomes an assertion the reader has no reason to believe"
    assert "DIFFERENT repositories never collide" in flat, \
        "the bullet no longer says different workspace roots never collide — an agent will " \
        "read every unrelated browser failure as this one"
    assert '`PLAYWRIGHT_MCP_ISOLATED` = `"true"`' in flat, \
        "the bullet no longer names the fix with its VALUE — envToBoolean accepts only " \
        '"true"/"1" and IGNORES anything else, so the value is the fix, not decoration'
    assert "in the `env` block of the file `.claude/settings.json`" in flat, \
        "the bullet no longer says WHERE the variable goes (the `env` block of a project " \
        "`.claude/settings.json`) — the bare path also occurs in the sentence after it"


def test_the_cross_session_boundary_forecloses_the_storage_state_non_fix():
    """VMCP-113 (585): the bullet above names a fix AND its cost, and the cost has an obvious,
    upstream-documented-looking remedy that does not work. This pins the foreclosure.

    The bullet ends by telling an agent the price of `--isolated`: an in-memory profile, so
    browser logins stop surviving a restart. Upstream's README then describes `--storage-state`
    (env: `PLAYWRIGHT_MCP_STORAGE_STATE`) as the way to load cookies and localStorage INTO an
    isolated context — which reads exactly like the missing half, and is how this card came to
    be filed in the first place. Measured on the installed 0.0.78, it is half true and the
    wrong half: the file IS read when the browser context is created (cookies restored,
    confirmed by what the browser then sent to the origin), and it is NEVER written — after a
    login, `browser_close` and a clean shutdown the file was byte-identical, and the next
    session read back the seed rather than the login. A path to a not-yet-existing file makes
    EVERY `browser_*` call fail outright.

    Why a rulebook clause and not just a repo note: this bullet is the one place that tells an
    agent working in SOMEONE ELSE'S project what to do about a cross-session browser collision,
    and it already instructs it to report rather than edit. An agent that reads only "logins no
    longer persist" has been handed a problem with a plausible published solution, and the
    self-healing `stable` copy puts this text in front of every consumer with no review gate
    (see this module's docstring) — so the "do not propose it" has to travel with the cost.

    Pinned as the INSTRUCTION plus the MEASUREMENT that justifies it, never the variable name:
    the name alone would stay green through a rewrite that mentioned the variable while
    dropping the verdict, and could equally be satisfied by some future paragraph elsewhere.

    MUTATION-CHECKED (`__pycache__` cleared between rounds, each round confirmed to select
    exactly 1 test, SKILL.md restored from a COPY — never `git checkout --`, since the subject
    is uncommitted while the card is in Build): control PASS; delete the whole clause while
    ADDING a mention of `PLAYWRIGHT_MCP_STORAGE_STATE` in a different section of the file ->
    FAIL, the round that proves this is not a keyword grep; delete only "не предлагай его как
    починку", keeping the measurement -> FAIL; delete only the never-written measurement,
    keeping the instruction -> FAIL; soften "НЕ ПИШЕТСЯ обратно НИКОГДА" to "пишется редко"
    -> FAIL; re-wrap the paragraph across every pinned phrase -> PASS."""
    flat = _flat(_rule_boundary_bullet(_skill_text()))
    assert "`PLAYWRIGHT_MCP_STORAGE_STATE` does NOT cancel that cost" in flat, \
        "the bullet states the cost of `--isolated` (logins stop persisting) without the " \
        "measured verdict on the remedy upstream's README appears to offer for it — the " \
        "reader is left one search away from re-deriving tracker #585"
    assert "do not offer it as a fix" in flat, \
        "the bullet no longer INSTRUCTS an agent not to propose PLAYWRIGHT_MCP_STORAGE_STATE " \
        "as the fix — and this is the bullet an agent reads while standing in someone else's " \
        "project, where a confident wrong suggestion is the whole risk"
    assert "is NEVER written back" in flat, \
        "the bullet no longer says WHY the remedy is not one (the file is only ever read, " \
        "never written back, so a login does not reach the next session). An instruction " \
        "without its reason is the first thing a later agent overrules"


def _claude_workspace_bullet(text: str) -> str:
    """CLAUDE.md's `workspace_cmd.py` architecture bullet — where the refusal-channel split lives.

    Scoped to the one bullet for the reason every slicer in this module records: `code`,
    `--release`, `--gc` and `exit 1` all occur elsewhere in CLAUDE.md (the `claimable_cmd.py`
    bullet is entirely about a JSON line and an exit-code split; the dogfood section drives
    `workspace <id>`), so a whole-file scan could not tell "the split is still stated HERE, where
    an author editing this module will meet it" from "those tokens survive somewhere in the file".

    The end anchor is the next TOP-LEVEL bullet (`\\n- \\``), not the name of the bullet that
    happens to follow today: continuation lines are indented two spaces, so only a real sibling
    bullet can end the slice, and reordering the architecture list cannot silently widen it.
    """
    start = text.find("- `src/vikunja_mcp/workspace_cmd.py`")
    assert start != -1, (
        "CLAUDE.md no longer opens its `workspace_cmd.py` bullet where this pin can find it. If "
        "the bullet was legitimately renamed, move this anchor — do not delete the check"
    )
    end = text.find("\n- `", start + 1)
    assert end != -1, "the workspace bullet no longer ends where the next architecture bullet begins"
    bullet = text[start:end]
    assert 0 < len(bullet) < len(text), \
        "the workspace slice is not a proper subset of CLAUDE.md"
    assert "claimable_cmd.py" not in bullet, "the slice swallowed the bullet BEFORE it"
    assert "process rules for agents" not in bullet, "the slice swallowed the bullet AFTER it"
    return bullet


# CANDIDATE shape only — "every … refusal … code" inside one sentence. Whether a candidate is a
# VIOLATION is decided by `_unscoped_code_universal` below, which reads the scope window; this
# pattern on its own says nothing about scoping, and must not be used as if it did. The `code`
# clause is what makes it usable at all — MEASURED, the correct bullet says "every refusal" twice
# on purpose (once quoted, to forbid the phrase; once as "on create every refusal has the same
# answer", which is the scoped truth), so a bare every/refusal scan would be red on the text it is
# meant to bless. The quantifier is a SET, not just "every" — MEASURED on the shipped bullet:
# with `\bevery\b` alone, adding "Each refusal carries a machine-readable `code`." beside the
# intact attribution PASSED, i.e. one synonym re-generalised straight past the pin. `[^.!?]` keeps
# every part of a candidate inside one sentence.
_CODE_UNIVERSAL_CANDIDATE = re.compile(
    r"(?i)\b(?:every|each|all|any)\b[^.!?]{0,32}?\b(?P<noun>refusals?)\b[^.!?]{0,72}?\bcode\b"
)
# Names one of the two channels, i.e. narrows what a quantifier ranges over. The release side is
# required in its FLAG spelling (`--release`/`--gc`), so prose that merely contains the word
# "release" cannot bless itself; a bare `create` DOES count, because the bullet legitimately
# quantifies over create refusals ("on create every refusal has the same answer").
_NAMES_A_CHANNEL = re.compile(r"(?i)--release|--gc|\bcreate\b")
# A `.`/`!`/`?` that actually ENDS a sentence: followed by whitespace or the end of the text, and
# not the dot of a one-letter abbreviation (`i.e.`, `e.g.`). Both halves are load-bearing and were
# measured SEPARATELY — see `_unscoped_code_universal`. This primitive is where the previous round
# went wrong: it located the sentence start with a bare `rfind(".")`, which counts the dot inside
# `SKILL.md`, so the window below began mid-clause and flagged true, correctly-scoped prose.
_SENTENCE_END = re.compile(r"(?<![.\s][A-Za-z])[.!?](?=\s|$)")
# the same claim, correctly scoped: the `code` is attributed to the release/gc channel
_SCOPED_CODE_CLAIM = re.compile(r"`--release`/`--gc`[^.!?]{0,160}?\bcode\b")


def _sentence_start(flat: str, pos: int) -> int:
    """Index just past the last real sentence terminator before `pos` (0 when there is none).

    Deliberately NOT `_SENTENCE_END.finditer(flat, 0, pos)`: an `endpos` makes `$` match there, so
    a dot sitting immediately before the candidate would count as a terminator on the strength of a
    boundary this function invented. Scanning the whole string and stopping evaluates every
    lookahead against the real neighbouring character.
    """
    start = 0
    for terminator in _SENTENCE_END.finditer(flat):
        if terminator.start() >= pos:
            break
        start = terminator.end()
    return start


def _unscoped_code_universal(flat: str):
    """The first `code` universal in `flat` that scopes itself to NEITHER refusal channel.

    A candidate is scoped when its SCOPE WINDOW — from the start of its sentence through the end of
    the `code` clause that closes the candidate — names a channel. All three legitimate shapes land
    inside that window, which is why it has those two edges and not narrower ones:
      * "Every `--release`/`--gc` refusal …" narrows at the noun phrase (this is the `gap` the
        original pattern captured and never read);
      * "on create every refusal …" narrows at the clause BEFORE the quantifier — so the window
        must extend LEFT past the quantifier, not merely cover the gap;
      * "Every refusal on the `--release`/`--gc` path carries a `code`" narrows AFTER the noun — so
        the window must also extend RIGHT, through the `code` clause.

    WHY THE RIGHT EDGE IS THERE, correcting a claim this function used to make. It previously
    stopped at the noun, justified by "a tail-inclusive window would bless the very sentence 580
    deleted". MEASURED, that is false as stated: the deleted sentence is "Every refusal carries a
    machine-readable `code`, and `--gc` GRADES them into two lists", and its `--gc` sits AFTER the
    word `code`, i.e. OUTSIDE a window that ends there — a tail-up-to-`code` window still FLAGS it.
    Only a window widened to the SENTENCE END blesses it, and that is the boundary the earlier
    measurement actually compared against. So the right edge costs nothing (the deleted sentence,
    the quantifier-synonym round and every other round are unchanged by it) and buys the
    post-nominal shape above, which the noun boundary rejected as a false positive.

    WHY THE LEFT EDGE NEEDS A REAL TERMINATOR. `_SENTENCE_END` replaces the bare `rfind(".")` this
    used to do, and both of its clauses were measured on their own, on prose whose TAIL names no
    channel so that only the left edge can rescue it:
      * `(?=\\s|$)` — "On create (see `SKILL.md`) every refusal gets the same treatment, so adding
        a machine-readable `code` would be pointless." True, scoped, and flagged under `rfind`,
        because the dot in `SKILL.md` cut the lead off before the word "create". That is the same
        accident that used to make this bullet's own create clause pass, so the fix had made a
        known-broken primitive load-bearing.
      * the abbreviation lookbehind — the same sentence with "i.e." in place of the code span is
        flagged even WITH `(?=\\s|$)`, because "i.e." really is a dot followed by a space. Left
        unhandled it looks fixed by accident: the reported wording of that case happens to say
        "create-side" in its tail, so the right edge rescues it and the false positive only
        resurfaces on the next rewording.

    NOT A PARSER, and here is everything it does not catch. Bounds, so a later reader does not
    trust it further than it goes:
      * a channel named ANYWHERE before `code` blesses the rest of the sentence, including a
        sentence that scopes itself and then over-generalises anyway ("a create refusal is
        `{\"error\"}` + exit 1, and every refusal carries a machine-readable `code`");
      * the claim split across two sentences ("Every refusal is uniform. Each one carries a
        machine-readable `code`.") — each half is harmless alone;
      * the quantifier AFTER the noun ("A refusal, every single one, carries a `code`");
      * the 32/72-character caps in the candidate pattern, which keep it inside one clause: a long
        qualifier that pushes `code` past 72 characters walks past ("Every refusal, whichever
        channel produced it and whatever the underlying reason turns out to be, carries a
        machine-readable `code`.");
      * a synonym for the NOUN ("Every rejection/failure carries a machine-readable `code`") and
        quantifier-free phrasings ("Refusals carry a `code`", "Both refusal channels carry a
        `code`");
      * in the other direction it is deliberately strict about the release side's spelling — the
        FLAG form is required, so "Every release/gc refusal …" is flagged, as is "On creation every
        refusal …"; and a sentence ending in a one-letter word ("… option A. Every refusal …")
        reads as one sentence too far left, the abbreviation lookbehind's own price.
    So: the drift this catches is the measured one — an unqualified universal — in every
    QUANTIFIER wording of it, which is the axis that actually re-generalised in review. The rest is
    review's job, and review is what caught every bound listed above.
    """
    for match in _CODE_UNIVERSAL_CANDIDATE.finditer(flat):
        window = flat[_sentence_start(flat, match.start()):match.end()]
        if not _NAMES_A_CHANNEL.search(window):
            return match
    return None


# Prose whose verdict is FIXED, so the window above is exercised by a GREEN run and not only under
# mutation. MEASURED and load-bearing: the shipped bullet yields ZERO candidates (the dot in
# `SKILL.md` cuts its create clause before `code` reaches the pattern), so the pin below would pass
# whatever this function did — every defect found in review so far was invisible until someone
# reflowed that clause, which is precisely the edit this pin exists to police. Every window
# variant considered and rejected disagrees with at least one row here: sentence-start..noun on
# three, gap-only on four, gap+tail on two, `rfind` for the left edge on two, no-abbreviation-guard
# on one, sentence-wide on three.
_SCOPE_WINDOW_EXAMPLES = (
    # (what the row is for, prose, is it a violation)
    ("the universal 580 deleted — a channel named only AFTER `code` does not scope it",
     "Every refusal carries a machine-readable `code`, and `--gc` GRADES them into two lists.",
     True),
    ("a quantifier synonym re-generalises just as well",
     "Each refusal carries a machine-readable `code`.", True),
    ("a channel named in the PREVIOUS sentence does not carry over",
     "`--gc` grades the codes it gets. Every refusal carries a machine-readable `code`.", True),
    ("scoped at the noun phrase — the wording this pin's failure message dictates",
     "Every `--release`/`--gc` refusal carries a machine-readable `code`.", False),
    ("scoped AFTER the noun — ordinary English, and why the window keeps its tail",
     "Every refusal on the `--release`/`--gc` path carries a machine-readable `code`.", False),
    ("scoped by the clause BEFORE the quantifier — why the window extends left",
     "On create every refusal has the same answer, so a `code` there would be a public value.",
     False),
    ("…and that clause survives a dot inside a code span",
     "On create (see `SKILL.md`) every refusal gets the same treatment, so adding a "
     "machine-readable `code` would be pointless.", False),
    ("…and an abbreviation",
     "On create, i.e. when the tree cannot be made, every refusal gets the same treatment, so "
     "adding a machine-readable `code` would be pointless.", False),
)


# --- VMCP-179 (704): a rulebook count of an EXTERNAL package must carry that package's version --

# Any "<number> тул(ов)" claim. The number may be digits or a Russian numeral, because the
# rulebook writes small counts as words ("семь тулов") and larger ones as digits.
_TOOL_COUNT_CLAIM = re.compile(
    r"(?:\d+|одному|одном|один|одна|одну|две|два|три|четыре|пять|шесть|семь|восемь|девять|"
    r"десять|одиннадцать|двенадцать)\s+тул(?:ов|а|ы|зы|з)?\b",
    re.IGNORECASE,
)
# The SAME claim in English, for the two files outside the rulebook that make it — VMCP-222 (765).
# A hyphen counts (`25-tool roster`), because that is how this repo writes an attributive one.
_TOOL_COUNT_CLAIM_EN = re.compile(
    r"\b(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|"
    r"fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty)[- ]tools?\b",
    re.IGNORECASE,
)
# What puts an English count IN SCOPE: the package has to be named near it. `.gitignore` and
# CLAUDE.md are not the rulebook — they count other things too — so the scope states the property
# that actually distinguishes an in-scope count: it is ABOUT the external package.
# IT KILLS NOTHING MEASURABLE TODAY, and saying so is the point rather than an apology: control
# 0 failed, and dropping it so that language alone decides is 0 failed too. An
# earlier version of this comment claimed CLAUDE.md's own `12 agent tools` as the false positive
# it excludes, and that claim is FALSE for a reason worth knowing before loosening the pattern —
# `_TOOL_COUNT_CLAIM_EN` requires the number ADJACENT to the noun, so `12 agent tools` is not
# matched at all and the scope never gets asked about it. Widen the pattern to allow an
# intervening word and the tracker's own count arrives immediately; this is the guard that would
# then be doing the work, which is why it stays.
# 600 characters rather than the stamp's 220, measured over the five in-scope counts these two
# files hold: the farthest is `.gitignore`'s `(+11 tools)`, whose nearest `browser_` is 336
# characters back (358 forward), so 220 misses it and 600 reaches every one with headroom. The
# tracker's count is thousands of characters from any playwright mention, so nothing between 600
# and 2000 would change a verdict even once the pattern could see it.
_PACKAGE_NAMED = re.compile(r"playwright|browser_", re.IGNORECASE)
_PACKAGE_SCOPE_WINDOW = 600
# A released version of the npm package, e.g. `0.0.78`. `@latest` deliberately does NOT match:
# a floating tag is the PROBLEM this pin exists for, not a stamp that answers it.
_PACKAGE_VERSION = re.compile(r"\b\d+\.\d+\.\d+\b")

# How far from the count the version may sit. Wide enough to survive re-wrapping the sentence,
# narrow enough that the stamp has to be IN the claim's own clause rather than anywhere in a
# 190-line section. Measured BOTH WAYS on purpose: the first version of this pin looked only
# FORWARD, which would have rejected the equally correct "измерено на 0.0.78: ... принимают
# семь тулов" — a mutation round meant to isolate the window is what surfaced it.
_VERSION_WINDOW = 220


def _browser_section(text: str) -> str:
    """The browser rules, sliced out of the file that owns them now.

    The core keeps a pointer stub under the SAME heading, so this reads
    `references/browser.md` rather than the bundle: a bundle search finds the stub.
    """
    text = _reference("browser.md")
    start = text.index("### Browser (playwright)")
    # `references/browser.md` IS this section, so there is no following `## ` to stop at any
    # more; before the split the section ended at the next top-level heading of the one big file.
    rest = text.find("\n## ", start)
    if rest == -1:
        rest = len(text)
    return text[start:rest]


def test_a_playwright_tool_count_in_the_rulebook_names_the_version_it_was_measured_on():
    """VMCP-179 (704): the rulebook may quote a tool count of `@playwright/mcp`, but not a
    BARE one — the number has to carry the package version it was measured on.

    Why this file and not a number check. The count is a property of an external npm package,
    so no unit test can assert the VALUE without spawning `npx` (measured: reproducing it costs
    a server spawn plus the `initialize` / `initialized` / `tools/list` handshake, and 0.0.78's
    `--help` prints no tool list, so there is no one-command form either). What a test CAN
    assert is the SHAPE — that the figure is stamped — which is the repo's standing rule for a
    measured number a reader acts on: an assert where one is possible, a version where the
    number is history, never a date.

    And here it is the version specifically, because the same section PRESCRIBES the floating
    tag: `npx -y @playwright/mcp@latest --isolated --headless`. So the rulebook tells the agent
    to install whatever ships today and then quotes a count taken on 0.0.78 — the two drift
    apart silently, in the one file that self-rolls-out to every consumer and outlives the
    measurement by design. Measured on 0.0.78 while writing this card: default 24 tools, 7 of
    them taking `filename`; all twelve declared `ToolCapability` members 69 and 11. The
    seven is the load-bearing one, because the rule around it argues from EXHAUSTIVENESS
    ("голое имя закрыть нечем ... `filename` принимают семь тулов") — an eighth text writer in
    a later release does not make the sentence merely stale, it makes its reasoning wrong,
    while the prefix rule it supports is about the DIRECTORY and survives any count.

    The floor matters as much as the stamp check: without it a rulebook that dropped the
    sentence, or a regex that stopped matching it, would leave this test scanning nothing and
    passing — the "no tests ran looks like a pass" shape. Scoped to the browser section so the
    tracker's own "один тул" claims (`exactly ONE agent tool walks a card out of Review`) are
    not swept in; the section is the unit because that is where a playwright count can appear.

    MUTATION-CHECKED with an UNMUTATED CONTROL round on the same selection, `__pycache__`
    deleted before each round and `PYTHONDONTWRITEBYTECODE=1` set, every round's `collected`
    read and every failure read by its MESSAGE. Rounds are recorded in the card's `[worklog]`.
    """
    section = _browser_section(_skill_text())

    claims = list(_TOOL_COUNT_CLAIM_EN.finditer(section))
    assert claims, (
        "no '<number> tools' claim found in the browser section of SKILL.md — either the "
        "rulebook stopped making one (then delete this pin) or the pattern stopped matching "
        "it (then fix the pattern). A pin that scans nothing passes for the wrong reason."
    )

    unstamped = []
    for m in claims:
        window = section[max(0, m.start() - _VERSION_WINDOW): m.end() + _VERSION_WINDOW]
        if not _PACKAGE_VERSION.search(window):
            unstamped.append(m.group(0).strip())

    assert not unstamped, (
        f"SKILL.md quotes a playwright tool count without naming the package version it was "
        f"measured on: {unstamped}. The same section prescribes `@playwright/mcp@latest`, so "
        f"an unstamped count silently becomes false on the next release of a package this "
        f"repo does not control — and the rulebook self-rolls-out to consumers. Name the "
        f"version beside the number (e.g. '`@playwright/mcp` 0.0.78')."
    )


@pytest.mark.parametrize("name", ["docs/dossier/browser.md", ".gitignore"])
def test_a_playwright_tool_count_outside_the_rulebook_names_its_version_too(name):
    """VMCP-222 (765): the same rule, in the two other files that make the same claim.

    The pin next door was SKILL.md-only and Russian-only, and the count it was written for is
    not: `SEVEN tools taking a `filename`` is asserted in `.gitignore`'s browser block and again
    in CLAUDE.md, in English, in prose an agent reads before it ever loads the skill. Measured on
    the tree this landed on, both of those sat more than 2000 characters from the nearest
    `0.0.78` — i.e. unstamped by the sibling's own 220-character yardstick — so the class the
    sibling closes was fully open two files over. `.gitignore` matters as much as CLAUDE.md here
    and not less: its comment block is the ARGUMENT for which extension rules exist, and that
    argument reasons from exhaustiveness («six of which WRITE»), so a count going stale there
    does not merely misinform, it turns a live rule's justification false.

    WHY A PACKAGE-PROXIMITY SCOPE and not a section slicer. Neither file has a heading structure
    to slice on — `.gitignore` is comment blocks, CLAUDE.md's playwright material is spread over
    three sections — and the thing that actually distinguishes an in-scope count is that it is
    ABOUT the external package. Asking the surrounding window to name it is that property, stated
    directly. What it is NOT is a measured false-positive filter; the round below says 0, and the
    comment beside `_PACKAGE_NAMED` says what it is guarding instead.

    MUTATION-CHECKED, `__pycache__` deleted per round then `PYTHONDONTWRITEBYTECODE=1`, this test
    as the selection, every round restored from a COPY with the restore confirmed by sha256 and
    by returning to the control. Control round: 0 failed.
      * strip the version from beside `.gitignore`'s `SEVEN tools` -> 1 failed
      * strip it from beside CLAUDE.md's -> 1 failed
      * drop `_PACKAGE_NAMED` from the scope test, so language alone decides -> **0 failed**, and
        this is NOT a kill. It is written down because a round that kills nothing is the one
        worth writing down — the same shape the sibling sweep's `(?<!/ )` row records. All five
        in-scope counts these two files hold are playwright counts, and the one English count in
        either file that is NOT (`12 agent tools`) is invisible to the pattern for an unrelated
        reason: the number is not adjacent to the noun. So the scope's price today is zero and
        its value is conditional on the pattern, which is where the argument for keeping it is
      * delete every English tool count from a file -> 1 failed on the floor assert, the
        "no tests ran looks like a pass" shape the sibling names
    """
    path = Path(__file__).resolve().parents[2] / name
    assert path.is_file(), f"{name} is gone from the repo root — this pin has nothing to read"
    text = path.read_text(encoding="utf-8")
    claims = [
        m for m in _TOOL_COUNT_CLAIM_EN.finditer(text)
        if _PACKAGE_NAMED.search(
            text[max(0, m.start() - _PACKAGE_SCOPE_WINDOW): m.end() + _PACKAGE_SCOPE_WINDOW]
        )
    ]
    assert claims, (
        f"no playwright tool count found in {name} — either it stopped making one (then narrow "
        f"this pin to the file that still does) or the pattern stopped matching it (then fix "
        f"the pattern). A pin that scans nothing passes for the wrong reason."
    )
    unstamped = [
        m.group(0).strip() for m in claims
        if not _PACKAGE_VERSION.search(
            text[max(0, m.start() - _VERSION_WINDOW): m.end() + _VERSION_WINDOW]
        )
    ]
    assert not unstamped, (
        f"{name} quotes a count of `@playwright/mcp`'s tool surface without naming the version "
        f"it was measured on: {unstamped}. This repo prescribes `@playwright/mcp@latest`, so the "
        f"number is a fact about whatever shipped the day it was taken. Name the version within "
        f"{_VERSION_WINDOW} characters of the count, as SKILL.md's browser section does."
    )


def test_the_code_universal_scope_window_agrees_with_its_worked_examples():
    """VMCP-110 (580): the scope window of `_unscoped_code_universal`, pinned on fixed prose.

    This exists because the pin below it is CURRENTLY VACUOUS as a check of the window: measured,
    CLAUDE.md's workspace bullet produces zero candidates, so the window never runs on a green
    suite and two rounds of review found false positives in it that no run would ever have shown.
    A prose pin that only exercises its own predicate under mutation is a predicate nobody is
    testing; these rows run it every time.

    The rows are not decoration — each is a wording that a real author might write about THIS
    module, and between them they discriminate every window boundary that was proposed and rejected
    while getting this right (see the comment on `_SCOPE_WINDOW_EXAMPLES` for which variant each
    row kills). They pin the PREDICATE, not CLAUDE.md; the document itself is pinned below.
    """
    for reason, prose, is_violation in _SCOPE_WINDOW_EXAMPLES:
        violation = _unscoped_code_universal(_flat(prose))
        assert (violation is not None) is is_violation, (
            f"the `code` universal's scope window disagrees with a worked example ({reason}): "
            f"{prose!r} should {'be flagged' if is_violation else 'pass'}, and it "
            f"{'passed' if violation is None else 'was flagged'}. If the window was changed on "
            f"purpose, re-measure this table — do not delete the row that disagrees"
        )


def test_the_claude_md_workspace_bullet_keeps_the_code_claim_scoped():
    """VMCP-110 (580): CLAUDE.md's workspace bullet used to state "Every refusal carries a
    machine-readable `code`" — a universal that is FALSE of the create channel, which refuses by
    raising and comes back as `{"error": …}` + exit 1 with no `code` at all. The behaviour is pinned
    in tests/unit/test_workspace_cmd.py::test_the_two_refusal_channels_are_not_interchangeable; this
    is the PROSE half, and it is the half that actually propagated: two later documents copied the
    universal out of this bullet (workspace_cmd.py's own CODE_* header and the plan doc), and a
    third would have.

    Why prose needs its own net here rather than review alone: this bullet is the standing brief
    every agent working in this repo reads before touching the module, so a false universal in it
    does not just sit there — it gets IMPLEMENTED. The plausible "fix" for a reader who believes it
    is to add a `code` to the create path, which is precisely the change 580 weighed and rejected
    (a code exists to feed `_keep_is_expected`, the only grader in the package; on create every
    refusal has the same answer, and the catch-all covers an OPEN set so a create-side code could
    only ever be present-SOMETIMES — worse to parse than absent-always).

    Three clauses are pinned, deliberately as ANCHORS rather than as sentences, because the failure
    mode is not deletion but re-wording that quietly re-generalises:
      * NO unscoped universal survives — the claim is caught by its SHAPE ("every … refusal …
        code" inside one sentence) whenever nothing from that sentence's start through its `code`
        clause names a channel, so a reflow, a synonym for "carries" or a synonym for "every"
        cannot walk past it. What counts as scoping, where the window's two edges are and what it
        does NOT catch are all in `_unscoped_code_universal`; the window itself is exercised on a
        green run by test_the_code_universal_scope_window_agrees_with_its_worked_examples, since
        this bullet yields no candidates of its own.
      * the CREATE channel is stated at all: its literal payload token and its exit code. A bullet
        that merely stops saying "every" would satisfy the first clause while leaving the reader
        with no idea what a create refusal looks like — which is the state that produced the drift.
      * the `code` is ATTRIBUTED to `--release`/`--gc`, not left floating. `--release`/`--gc` as a
        PAIR occurs exactly once in this file (measured), so this cannot pass on an unrelated
        mention.

    NOT pinned: the justification prose (the open-set argument, the "no consumer" argument). Those
    are review's job per this module's docstring, and pinning them would turn a legitimate
    re-wording of the rationale into a red test.

    MUTATION-CHECKED (`__pycache__` cleared between rounds, each round confirmed to select exactly
    1 test, CLAUDE.md restored from a COPY — never `git checkout --`): control PASS; restore the old
    "Every refusal carries a machine-readable `code`, and `--gc` GRADES them…" sentence in place of
    the scoped one -> FAIL, quoting the match; delete the whole create-channel paragraph -> FAIL on
    the `{"error"` clause; rename the bullet so the slicer's anchor misses -> FAIL loudly from the
    slicer, not a vacuous pass; widen the end anchor to the next `##` -> FAIL from the swallow
    guard.

    And mutation-checked in the other direction too, twice over, because BOTH earlier versions of
    this pin were RED ON CORRECT PROSE. The first captured the gap and never read it, so "scoped"
    was never actually tested; the second read a window that stopped at the noun and located that
    window's left edge with a bare `rfind(".")`. Five cases measured, on this bullet: the wording
    the failure message itself dictates ("Every `--release`/`--gc` refusal carries a
    machine-readable `code`, and `--gc` GRADES them…", create paragraph intact) -> PASS, where it
    used to fail and leave an author who OBEYED the error message with no way out but to weaken the
    pin; the bullet's own create clause reworded without its "SKILL.md" citation -> PASS; a create
    clause that KEEPS a citation ("on create (see `SKILL.md`) every refusal …") or uses an
    abbreviation ("on create, i.e. …") -> PASS, where the dot in each used to truncate the window's
    lead; post-nominal scoping ("Every refusal on the `--release`/`--gc` path carries a
    machine-readable `code`.") -> PASS, where the noun boundary rejected ordinary scoped English;
    and "Each refusal carries a machine-readable `code`." added beside the intact attribution ->
    FAIL, where the "every"-only pattern let it through.

    The vacuity question was then asked PROPERLY, because "the slicer can't miss" is not the same
    claim as "a miss can't pass": with the slice replaced by the WHOLE file AND the subset/swallow
    guards deleted AND the create paragraph gone, this test PASSES. That is not hypothetical —
    measured in CLAUDE.md, `{"error"` also occurs in the `server.py` bullet and `exit 1` in a later
    section, so both positive clauses have somewhere else to land. The `0 < len(bullet) < len(text)`
    guard is what stands between this test and that vacuous pass; it is load-bearing, not ceremony.
    """
    bullet = _claude_workspace_bullet(_claude_md_text())
    flat = _flat(bullet)

    violation = _unscoped_code_universal(flat)
    # the message is evaluated only when the assert FAILS, so .group() is safe here
    assert violation is None, (
        f"CLAUDE.md's workspace bullet states the UNSCOPED universal again: "
        f"{violation.group(0)!r}. Only a `--release`/`--gc` refusal carries a `code`; a CREATE "
        f'refusal is `{{"error": …}}` + exit 1 and carries none. Name the channel anywhere from '
        f"the start of that sentence through the `code` clause itself — \"every `--release`/`--gc` "
        f'refusal …", "every refusal on the `--release`/`--gc` path carries a `code`", or "on '
        f'create every refusal …" for a claim about the other side. What does NOT count is naming '
        f"it only AFTER `code`: the sentence this pin exists to forbid did exactly that (\"…carries "
        f"a machine-readable `code`, and `--gc` GRADES them…\"). Or, if the CODE really did change, "
        f"change the code and tests/unit/test_workspace_cmd.py's channel pin FIRST, then this prose"
    )
    assert '{"error"' in bullet, (
        'CLAUDE.md\'s workspace bullet no longer shows what a CREATE refusal looks like '
        '(`{"error": …}`). Without it the bullet says only what `--release`/`--gc` do, and the '
        'next reader re-generalises that to both channels — which is how 580 happened'
    )
    assert "exit 1" in flat, (
        "CLAUDE.md's workspace bullet no longer states the create channel's exit code. On create "
        "the EXIT CODE is the whole machine-readable verdict — dropping it is dropping the "
        "contract, not a detail"
    )
    assert _SCOPED_CODE_CLAIM.search(flat), (
        "CLAUDE.md's workspace bullet no longer attributes the machine-readable `code` to the "
        "`--release`/`--gc` channel. An unattributed `code` sentence reads as universal again"
    )


# The two in-repo DESIGN RECORDS of the parallel drain. Both already carry a scoped copy of the
# `code` claim, and both are read by agents arriving from a grep rather than from the top of the
# file — which is the whole reason the claim keeps regrowing here.
_DRAIN_DESIGN_DOCS = (
    "docs/superpowers/specs/2026-07-29-parallel-worktree-drain-design.md",
    "docs/superpowers/plans/2026-07-29-parallel-worktree-drain.md",
)


def _design_doc_flat(relpath: str) -> str:
    """A tracked design document, blockquote-stripped and flattened for the prose predicates.

    Read from the CHECKOUT by path, like `_claude_md_text` and unlike `_skill_text`: these are
    repo documents, not packaged resources, so `importlib.resources` cannot see them.

    The blockquote strip is not cosmetic and was MEASURED, not assumed. Every marker in the design
    record is a `>` block, and `_CODE_UNIVERSAL_CANDIDATE` caps the quantifier→`code` gap at 72
    characters to stay inside one clause; a `> ` leader adds two characters per WRAPPED LINE, which
    spends that budget on punctuation. Constructed the boundary case: "Every refusal <41 chars>
    carries a machine-readable `code` beside the reason", wrapped as a blockquote, is MISSED with
    the leaders left in and FLAGGED with them stripped. So stripping makes this pin strictly
    stricter, and a violation cannot hide behind the wrap it happens to fall on. (On today's text
    both spellings agree — the strip buys nothing yet, which is exactly when it is cheap to add.)
    """
    path = Path(__file__).resolve().parents[2] / relpath
    assert path.is_file(), (
        f"{relpath} is gone from the repo — this pin has nothing to read. If the design record "
        f"was moved or retired, move this path; do not delete the check"
    )
    return _flat(re.sub(r"(?m)^[ \t]*>+[ \t]?", " ", path.read_text(encoding="utf-8")))


def test_the_drain_design_records_keep_the_code_claim_scoped():
    """VMCP-122 (597): the same `code` universal, in the DESIGN RECORDS — its fourth regrowth.

    580 scoped this claim in three places and pinned exactly one of them (CLAUDE.md, above). The
    seed then turned up a FOURTH time, in the spec doc's `--release` marker: "Every refusal now
    also carries a machine-readable `code`". That copy was *contextually* scoped — its host
    paragraph is `--release` and its opening clause says "the POLICY above" — which is precisely
    why it survived three sweeps that were looking for the obvious shape.

    WHY IT WAS STILL WORTH FIXING, and therefore worth pinning: MEASURED, `git grep
    machine-readable` over that document prints the marker's line beside §3's "no machine-readable
    key at all", and NEITHER line names a channel. A reader who lands out of context — a grep, a
    diff hunk, a deep link — sees one document flatly contradicting itself and has only the
    document's own banner to break the tie, which tells them a MARKED passage is the stronger
    claim. The marker was the wrong branch to trust.

    WHY THE WHOLE FILE AND NOT A SLICE. Every other prose pin in this module slices first, because
    a bare token scan cannot tell "the rule is still stated HERE" from "the token survives
    somewhere". This one needs no slice: `_unscoped_code_universal` does not scan for tokens, it
    decides violation-vs-scoped per candidate, so correct prose in the rest of the file is simply
    not a candidate. MEASURED on both documents: the spec doc yields exactly ONE violation before
    the fix and NONE after; the plan doc yields none either way — but NOT because it is scoped,
    which matters enough to have its own paragraph below; and the spec doc's own CORRECT §3
    sentence, "every create-path refusal is codeless", is clean, since `codeless` is not
    `\\bcode\\b`. A slicer here would only add a second thing to drift.

    NOT VACUOUS, and that is measured rather than argued: run this predicate over the spec doc as
    it stood at the parent commit and it FAILS, naming the sentence. Contrast the CLAUDE.md pin
    above, whose bullet yields ZERO candidates on a green run — this one exercises the window on
    real prose in both directions.

    NOT PINNED: anything else in either document. They are design records whose own banner says
    the markers are not exhaustive and that an unmarked passage means "nobody has checked it".
    This pin makes exactly one claim — that neither record restates the `code` payload as a
    universal over both refusal channels — and deliberately leaves the rest free to age, which is
    what a design record is for.

    MUTATION-CHECKED (`__pycache__` cleared between rounds, each round confirmed to select exactly
    1 test, documents restored from a COPY — never `git checkout --`, since they are uncommitted
    while the card is in Build): control PASS; restore the unscoped "Every refusal now also carries
    a machine-readable `code`" to the spec doc's `--release` marker -> FAIL, quoting the match; the
    same universal added to the PLAN doc -> FAIL naming the plan doc, i.e. the loop really does
    read both; point `_DRAIN_DESIGN_DOCS` at a renamed path -> FAIL loudly from the `is_file`
    guard, not a vacuous pass.

    ONE ROUND CAME BACK GREEN, and it is recorded as a BOUND rather than quietly dropped, because
    it narrows what this pin may be SAID to protect. Re-generalising the plan doc's OWN scoped
    clause — "the `code:` key on every `--release`/`--gc` refusal" back to "every refusal" — does
    NOT fail; nor does restoring 580's EXACT deleted wording there ("the `code:` key on every
    refusal (#516)"). Both yield ZERO CANDIDATES, and the cause is structural, not semantic:
    `_CODE_UNIVERSAL_CANDIDATE` requires quantifier → refusal → `code` IN THAT ORDER, and the plan
    doc puts `code` FIRST. Measured on that sentence — the first `\\bcode\\b` sits 14 characters
    BEFORE the quantifier, and the next one after it is 86 to 105 characters later, past the
    72-character cap as well. The plan doc is invisible to this predicate in both directions.

    SO READ THE COVERAGE HONESTLY. This pin protects the plan doc against a NEW compact universal
    added to it (the round above proves that much) and NOT against its existing clause being
    re-generalised: the plan doc's correctness rests on 580's WORDING, not on this test. The spec
    doc is the file this pin actually guards, and even there it guards ONE SHAPE — an independent
    review of this card measured nine real wordings of the same claim and found this predicate
    flags one of them. See `_unscoped_code_universal`'s own bounds list for the misses it already
    documents. That is why 580's ruling stands unchanged: review catches the rest, and this is a
    net under the copy-paste form of the drift, not a proof that the claim cannot regrow.
    """
    for relpath in _DRAIN_DESIGN_DOCS:
        violation = _unscoped_code_universal(_design_doc_flat(relpath))
        # the message is evaluated only when the assert FAILS, so .group() is safe here
        assert violation is None, (
            f"{relpath} states the `code` payload as an UNSCOPED universal: "
            f"{violation.group(0)!r}. Only a `--release`/`--gc` refusal carries a `code`; a CREATE "
            f'refusal is `{{"error": …}}` + exit 1 and carries none. In a design record this is '
            f"worse than in prose that describes today's code, because a MARKED passage claims "
            f"someone CHECKED it — see this document's own banner. Name the channel anywhere from "
            f"the start of that sentence through the `code` clause itself. Being inside a marker "
            f"attached to the `--release` paragraph does NOT count: that is exactly the copy "
            f"tracker #597 had to fix, and a grep prints the line without its host"
        )


def _reviewer_tree_rule(text: str) -> str:
    """The «Ревьюер, вынеся вердикт, освобождает своё дерево» bullet — the ONLY place a REVIEWER
    reads about its own worktree.

    Sliced, and the slice IS the point of VMCP-104 (563). Both phrases pinned below already occur
    in this file, in the BUILD agent's sections: «не считай, что ты всё ещё стоишь в своём дереве»
    lives in the `call_human` paragraph of the push recipe, and the `no-worktree` refusal is
    explained in the `--release` breakdown. A whole-file substring therefore cannot tell "the
    reviewer is told" from "the build agent is told" — which was exactly the state this card found:
    the rule was stated twice for build and only IMPLIED for review («дерево будет жить, пока
    карточка не уйдёт из Review» — while `needs_work` IS that departure).
    """
    start = text.find("- **Having cast a verdict, the reviewer releases its own tree:**")
    assert start != -1, "SKILL.md no longer has a rule about the reviewer's own worktree"
    end = text.find("\n- **", start + 1)
    assert end != -1, "the reviewer's tree rule no longer ends where the next top-level bullet does"
    bullet = text[start:end]
    assert 0 < len(bullet) < len(text), "the reviewer-tree slice is not a proper subset of SKILL.md"
    assert "It didn't start" not in bullet, "the slice swallowed the following bullet"
    return bullet


def test_the_reviewers_tree_rule_says_its_own_verdict_can_take_the_directory_away():
    """VMCP-104 (563): a review tree is alive BY ROLE — while the card sits in Review the sweep
    skips it whatever its age — so the reviewer's exposure is exactly its OWN `needs_work`, which
    moves the card to Build and kills the tree that same second. The rulebook stated the standing
    "re-`ensure`, do not assume your cwd survived" rule twice for the BUILD agent and never for the
    reviewer; workspace_cmd's own grace-window comment meanwhile asserts it "is a rule for BOTH
    roles", so the code was documenting a rule the rulebook only half-carried.

    MEASURED on this code before writing the prose (throwaway probe, real git repo + FakeAPI):
    a review tree quiesced past `_REAP_GRACE_SECONDS` with its card in Review -> `{released: [],
    kept: [], expected: []}`; after `approve` -> same, card still in Review; after `needs_work` ->
    the very next sweep returns it in `released` and the directory is gone; `--release --role
    review` on it -> exit 0, `{released: false, code: "no-worktree"}`.

    Pinned on both sides, since SKILL.md self-heals onto every consumer with no review gate:
      * the two board-facing CLAIMS are anchored in workflow.review_task itself — approve must
        keep the card in Review (no `_move` in that branch) and needs_work must take it out. Make
        approve move the card and this rulebook paragraph becomes false; the test fails first.
      * the ORDERING claim ("grace-окно до него даже не доходит") is anchored in gc_workspaces:
        the by-role liveness check has to run BEFORE `_last_activity` is ever consulted.
      * the two PROSE imperatives are pinned inside the slice, not the file, for the reason
        `_reviewer_tree_rule` records.

    MUTATION-CHECKED (`__pycache__` cleared between rounds, each round confirmed to select exactly
    1 test, SKILL.md restored from a COPY — never `git checkout --`, the edits are uncommitted):
    control PASS; delete the whole «Из Review карточку двигает не только человек» sub-bullet while
    LEAVING both phrases in their build-side homes -> FAIL on the imperative; delete only the
    imperative sentence and keep the rest of the sub-bullet -> FAIL; delete the `no-worktree`
    sub-bullet while the `--release` breakdown still explains that code -> FAIL; make
    `review_task`'s approve branch call `_move` -> FAIL; move the by-role check below
    `_last_activity` in gc_workspaces -> FAIL; re-wrap the paragraph across the pinned phrases ->
    PASS by design (`_flat`)."""
    text = _skill_text()
    bullet = _reviewer_tree_rule(text)
    flat = _flat(bullet)

    review_src = inspect.getsource(workflow.Workflow.review_task)
    approve_at = review_src.index('if verdict == "approve":')
    needs_work_at = review_src.index('[review] NEEDS WORK')
    assert "_move(" not in review_src[approve_at:needs_work_at], \
        "review_task's approve branch now MOVES the card — SKILL.md tells the reviewer the " \
        "tree survives an approve, which would become false"
    assert 'self._move(task_id, "Build")' in review_src[needs_work_at:], \
        "needs_work no longer takes the card out of Review — the whole hazard this rule " \
        "documents (your own verdict kills your tree) would be gone"
    assert "`approve` does NOT move the card" in flat, \
        "the reviewer's rule no longer says approve leaves the card (and the tree) alone"
    assert "needs_work" in flat, \
        "the reviewer's rule no longer names the verdict that takes its own directory away"

    gc_src = inspect.getsource(workspace_cmd.gc_workspaces)
    assert gc_src.index("alive[role]") < gc_src.index("_last_activity("), \
        "gc now consults the grace window BEFORE by-role liveness — SKILL.md tells the " \
        "reviewer the window is never even reached while the card sits in Review"

    assert "do not assume you are still standing in your own tree" in flat, \
        "the reviewer is no longer told not to assume its cwd survived the verdict — the " \
        "phrase still occurs in the BUILD agent's sections, which is why this pin is scoped"
    assert "call `workspace <id> --role review --at <sha>` again" in flat, \
        "the reviewer is no longer told HOW to get a directory back (re-ensure it), only that " \
        "it may be gone"


def _degraded_workspace_bullet(text: str) -> str:
    """The «Не завелось — цикл НЕ роняем» bullet — where the pump learns to tell a `workspace`
    FAILURE (exit 1, `error`) from a `--release` that simply declined (exit 0, `released: false`).

    Sliced for the same reason as `_reviewer_tree_rule`: every refusal code it contrasts is also
    explained, at length, in the `--release` breakdown further down, so a file-wide substring
    cannot tell "this bullet still distinguishes them" from "the words exist somewhere"."""
    start = text.find("- **It didn't start — do NOT drop the loop.**")
    assert start != -1, \
        "SKILL.md no longer tells the pump that a failed `workspace` is not a reason to stop"
    end = text.find("\n- **", start + 1)
    assert end != -1, "the «it didn't start» bullet no longer ends where the next bullet does"
    bullet = text[start:end]
    assert 0 < len(bullet) < len(text), "the «it didn't start» slice is not a subset of SKILL.md"
    assert "Having cast a verdict" not in bullet, "the slice swallowed the preceding bullet"
    return bullet


def test_the_released_false_shorthand_never_teaches_only_the_protective_reading():
    """VMCP-104 (563), the second half: `released: false` is taught in TWO places, and the short
    one used to collapse it to a single meaning — «это не сбой инструмента, а „у тебя осталась
    несохранённая работа"». That reading is wrong for `no-worktree`, whose meaning is the
    opposite (the tree is already gone and nothing is owed), and `no-worktree` is precisely the
    reviewer's routine outcome after a `needs_work` that outlived the grace window.

    Both sites are pinned to name the code, anchored on the CONSTANT so a re-value fails here
    rather than silently in the field. Scoped to each bullet: `no-worktree` appears in the
    `--release` breakdown too, so a file-wide substring would stay green with either shorthand
    collapsed back.

    MUTATION-CHECKED (same protocol as above): control PASS; restore the old one-meaning wording
    in «Не завелось» while the breakdown still explains all three codes -> FAIL; delete the
    reviewer's third-reading sub-bullet -> FAIL; re-value CODE_NO_WORKTREE -> FAIL on both."""
    text = _skill_text()
    for name, section in (
        ("«Не завелось — цикл НЕ роняем»", _degraded_workspace_bullet(text)),
        ("the reviewer's tree rule", _reviewer_tree_rule(text)),
    ):
        assert workspace_cmd.CODE_NO_WORKTREE in section, \
            f"{name} teaches `released: false` without its third reading — an agent whose tree " \
            f"is already gone reads a routine success as a protective refusal"
    degraded = _flat(_degraded_workspace_bullet(text))
    for code in (workspace_cmd.CODE_DIRTY, workspace_cmd.CODE_UNPUSHED):
        assert code in degraded, \
            f"the shorthand no longer says which codes DO mean unsaved work ({code}) — " \
            f"'read the `code`' without the codes sends the reader to the wrong half"


def _wip_saturated_bullet(text: str) -> str:
    """The «`wip_saturated: true` — это НЕ пустая очередь» bullet — sliced to that one item.

    Scoped like `_drain_width_section` / `_exclude_completeness_bullet`, for the same measured
    reason: `wip_saturated`, `ScheduleWakeup` and «пустая очередь» each occur many times in this
    rulebook, so a whole-file substring could not tell "the rule is still stated where the pump
    reads the payload" from "the words survive somewhere else"."""
    start = text.find("\n- **`wip_saturated: true` is NOT an empty queue.")
    assert start != -1, \
        "SKILL.md no longer tells the pump that wip_saturated is not an empty queue"
    end = text.find("\n- **", start + 1)
    assert end != -1, "the wip_saturated bullet no longer ends where the next one begins"
    bullet = text[start:end]
    assert 0 < len(bullet) < len(text), "the wip_saturated slice is not a proper subset of SKILL.md"
    return bullet


# The rulebook QUOTES this rendered phrase verbatim, numbers and all, and calls it the only place
# in the payload where both numbers stand side by side in prose. Owned here as a literal and
# asserted against BOTH sides below, so the three copies (this test, SKILL.md, workflow.py) cannot
# drift apart in any direction — and none of the three can be edited into agreement with itself.
_SATURATED_NUMBERS_4_OF_3 = "all 3 WIP slot(s) are busy (4 active)"


def test_the_rulebook_quotes_the_saturated_message_and_the_payload_still_renders_it():
    """SKILL.md makes two promises about the `wip_saturated` payload; nothing held either.

    #586 measured the gap on both. (1) The message interpolates `limit` and `active`, and the
    rulebook quotes the RENDERED pair — «all 3 WIP slot(s) are busy (4 active)» — as the one place
    a pump can see an overshoot in prose (`free` saturates at 0 and cannot show it). Swapping the
    two interpolations, so the payload reads "all 4 … (3 active)" and inverts the diagnosis, passed
    the whole suite: 596 passed. (2) The same bullet's operative instruction is "do NOT
    ScheduleWakeup — this is not an empty queue"; replacing the note with the string "no work right
    now" also passed, 596 passed. The message's only guard was `"empty" not in message`
    (test_workflow_wip), which pins a hazard word rather than a value, and the note had none at all.

    Pinned as the VALUES and the IMPERATIVE, not as prose: the rest of both strings stays free to be
    reworded, which is why this is not one of the byte-exact pins #586 deliberately refused to add
    to the nine remaining static payload strings. Reaching wip_saturated at 4-of-3 also needs a
    COMPLETE `exclude` — the resume branch is offered first — so this env is the exact state the
    rulebook describes, not a convenient one."""
    text = _skill_text()
    bullet = _wip_saturated_bullet(text)
    assert _SATURATED_NUMBERS_4_OF_3 in _flat(bullet), \
        "SKILL.md no longer quotes the rendered number pair it calls the payload's only prose view"
    # `_flat` on the imperative, not on the raw bullet: the shipped sentence wraps between "do"
    # and "NOT yield the turn", and a re-wrap of a paragraph must not turn a rule pin red.
    assert "ScheduleWakeup" in bullet and "do NOT yield the turn" in _flat(bullet), \
        "the rulebook no longer forbids idling the tick on a saturated board"

    api = FakeAPI(buckets=workflow.STAGES)
    wf = workflow.Workflow(api, project_id=3, wip_limit=3)

    def claim_fresh(title):
        task_id = api.add_task(title, "Queue")["id"]
        wf.claim(task_id)
        return task_id

    bounced = claim_fresh("reviewed, then bounced")
    wf.advance(bounced, to="build", spec="…")
    wf.advance(bounced, to="review", worklog="…", evidence="0" * 40)
    for n in range(3):                       # the pump refills the freed slot, as the tick does
        claim_fresh(f"held {n}")
    wf.review_task(bounced, verdict="needs_work", report="not yet")   # around the gate -> 4 of 3

    res = wf.next_task(exclude=wf.active_task_ids())
    assert res["task"] is None and res["wip_saturated"] is True, \
        "a COMPLETE exclude no longer produces the saturation signal the rulebook promises"
    assert res["wip"] == {"active": 4, "limit": 3, "free": 0}, \
        "precondition: this must be the 4-of-3 state SKILL.md spells the quote with"

    assert _SATURATED_NUMBERS_4_OF_3 in res["message"], res["message"]
    assert "ScheduleWakeup" in res["note"] and "Do NOT claim" in res["note"], res["note"]


def _stuck_section(text: str) -> str:
    """Sliced out of `references/stuck.md`, which owns this section now."""
    text = _reference("stuck.md")
    start = text.find("\n## Stuck? The way out depends on your ROLE\n")
    assert start != -1, "SKILL.md no longer has the section that routes a stuck agent by role"
    end = text.find("\n## ", start + 1)
    assert end != -1, "the stuck section no longer ends where the next section begins"
    section = text[start:end]
    assert 0 < len(section) < len(text), "the stuck slice is not a proper subset of SKILL.md"
    return section


def test_the_rulebook_routes_a_stuck_REVIEWER_to_the_only_door_that_is_open_to_it():
    """#590: the rulebook offered a stuck agent exactly two doors, `call_human` and `return_task`,
    and a REVIEWER has neither. It works exclusively from Review, where `call_human` is gated to
    Design/Build and (as of this card) `return_task` refuses too — and in multi-identity the card
    isn't even theirs. Measured before the gate landed: `return_task` from Review passed with no
    refusal and walked reviewed work to Backlog, unassigned and labeled `blocked` (the journal and
    any `reviewed` label SURVIVED it — it was the STAGE and the assignment that were walked back,
    and `next_task` stops offering the card). #693 has since made `return_task` clear the verdict
    on its way out, so re-running that measurement today leaves only the journal — the parenthesis
    is kept in the past tense because it is what THIS card measured, not what the tool does now.
    That was the rulebook's own "stuck?" advice quietly
    resetting the pipeline state it never mentioned.

    So the prose now names the reviewer's ONE channel — `review_task(verdict='needs_work')`, which
    hands the card back to its implementer in Build, who owns it and may `call_human` from there —
    plus `file_task` for a finding outside the card's slice.

    Pinned against the TOOLS, not just as words: both refusals are exercised through the real
    Workflow below, so if a future change reopens either door the rulebook's "оба выхода
    нерабочие" cannot keep shipping to every consumer as truth. (The reverse drift — code gates
    that the prose stops mentioning — is what the sliced substring half catches.)

    #672 added `decompose` to the same bullet. Attribution measured with `git log -S`, not
    assumed: the gate landed in `efa4b60` (#663) while the bullet's «оба выхода выше нерабочие»
    has not been touched since `51ab50d` (#590). So the bullet went on listing two tools — a
    sentence that stayed TRUE, since «оба выхода выше» names the two bullets above it and
    `decompose` is not one of them, while the reader it is written for lost a route.

    MUTATION-CHECKED (`__pycache__` cleared between rounds, selection confirmed at exactly 1 test):
    control PASS; delete the reviewer bullet from the section while LEAVING `review_task` /
    `needs_work` / `file_task` everywhere else in the file -> FAIL (and the whole-file substring
    this slice replaces was measured GREEN on that same mutation); drop return_task's Review gate
    -> FAIL; drop call_human's Review pointer -> FAIL; rename the heading -> FAIL loudly.

    Rounds added by #672, same procedure but restored from FILE COPIES rather than `git checkout`
    — the SKILL.md edit under test was uncommitted, and a checkout-based restore silently ate it
    once mid-sweep. Control PASS before AND after. Each round names the assertion read out of
    `--tb=line`: revert the reviewer bullet to its pre-#672 wording, `decompose` left everywhere
    else in the file and in this SECTION -> FAIL at the bullet-slice assert, while the section-wide
    substring that slice replaces was measured GREEN on that same mutation; drop decompose's Review
    gate -> FAIL at `DID NOT RAISE`; append a FOURTH top-level bullet naming `decompose` to the
    section, on top of the reverted wording -> FAIL, and the SAME file measured PASS against a
    one-edge `_reviewer_bullet` — which is what shows that helper's second edge is load-bearing
    and not decoration."""
    text = _skill_text()
    section = _stuck_section(text)

    # the prose: the reviewer's door, and the two it is NOT
    assert "review_task" in section and "needs_work" in section, \
        "the stuck section no longer names the reviewer's only working channel"
    assert "file_task" in section, \
        "the stuck section no longer routes an out-of-slice finding to file_task"
    assert "return_task" in section and "call_human" in section, \
        "the stuck section no longer contrasts the reviewer's door with the implementer's two"
    assert "decompose" in _reviewer_bullet(text), \
        "the reviewer's own bullet no longer names decompose among what is shut from Review (#663)"

    # the code: all three doors really are shut from Review, which is what the prose asserts
    api = FakeAPI(buckets=workflow.STAGES)
    wf = workflow.Workflow(api, project_id=3)
    card = api.add_task("under review", "Review", assignee=api.me_user)

    with pytest.raises(workflow.WorkflowError) as returned:
        wf.return_task(card["id"], reason="не понимаю задачу")
    assert "review_task" in str(returned.value), \
        "SKILL.md says return_task refuses from Review and points at review_task; it no longer does"
    assert api.stage_of(card["id"]) == "Review", "the refusal moved the card anyway"

    with pytest.raises(workflow.WorkflowError) as called:
        wf.call_human(card["id"], question="какой из двух вариантов правильный?")
    assert "review_task" in str(called.value), \
        "SKILL.md says call_human refuses from Review and points at review_task; it no longer does"

    with pytest.raises(workflow.WorkflowError) as split:
        wf.decompose(card["id"], [{"title": "часть A"}, {"title": "часть B"}])
    assert "review_task" in str(split.value), \
        "SKILL.md says decompose refuses from Review and points at review_task; it no longer does"
    assert api.stage_of(card["id"]) == "Review", "the refused decompose moved the card anyway"

    # ...and the door the prose sends them to is genuinely open
    assert wf.review_task(
        card["id"], verdict="needs_work", report="вопрос человеку: какой из двух вариантов?"
    )["moved_to"] == "Build"


def _reviewer_bullet(text: str) -> str:
    """The reviewer's bullet inside the stuck section — the only bullet in THAT section written
    for a reviewer, sub-bullets included (it runs to the end of the section). Not the only place
    in the rulebook that addresses the role, and the prose no longer says so: grep finds the
    «Независимое ревью изменений» section and the worktree-release bullet too.

    Sliced to the BULLET, not the section, for the same MEASURED reason as `_return_task_bullet`:
    `decompose` already occurs in this section's `return_task` bullet (the Done sub-bullet naming
    #649's gate), so a SECTION-wide `"decompose" in section` stays green with this bullet reverted
    to its pre-#672 wording. Verified by running exactly that mutation both ways.

    BOTH edges are guarded, like `_return_task_bullet` and unlike this helper's first version:
    ending the slice at the section heading alone assumes this stays the LAST top-level bullet,
    and appending a fourth would make the slice swallow it — after which `"decompose" in
    _reviewer_bullet(text)` could pass on text that is not the reviewer's bullet at all. So the
    end is whichever comes first, the next top-level bullet or the next heading (sub-bullets are
    indented and do not match), and the guard below still names the bullet above."""
    start = text.find("\n- **For the REVIEWER both ways out above are dead")
    assert start != -1, "SKILL.md no longer has the bullet written for a stuck REVIEWER"
    end = text.find("\n## ", start + 1)
    assert end != -1, "the reviewer bullet no longer ends where the next section begins"
    following = text.find("\n- **", start + 1)
    if following != -1:
        end = min(end, following)
    bullet = text[start:end]
    assert 0 < len(bullet) < len(text), "the reviewer slice is not a proper subset of SKILL.md"
    assert "an external blocker" not in bullet, "the slice swallowed the return_task bullet above it"
    return bullet


def _review_sweep(tmp_path, *, mine: bool = True) -> tuple[dict, dict]:
    """Run every agent tool against a card standing in Review, one on a FRESH board each, and
    report {label: refusal-or-None} plus {label: stage the card ended in}.

    `advance` is swept in all THREE forms and `review_task` in BOTH verdicts, because a per-TOOL
    sweep would hide the split that matters here: `review_task` is at once the only tool that
    walks the card out and — on its approve branch — one that leaves it exactly where it was.

    `mine=False` builds the MULTI-IDENTITY card — in Review and owned by its implementer, which
    is the state a reviewer is actually looking at. Worth sweeping separately because ownership
    and stage are checked in different orders by different tools, so the refusal REASONS differ
    even where the refusal itself does not."""
    def board():
        api = FakeAPI(buckets=workflow.STAGES)
        # a neighbour project, so the two cross-project tools have somewhere to aim; inert for
        # every other tool in the table
        neighbour = api.add_project("neighbour", buckets=workflow.STAGES, identifier="NB")
        wf = workflow.Workflow(api, project_id=3, siblings={"neighbour": neighbour["id"]})
        card = api.add_task("under review", "Review", assignee=api.me_user if mine else None)
        if not mine:
            api.tasks[card["id"]]["assignees"] = [{"id": 77, "username": "agent-impl"}]
        return api, wf, card

    probe = tmp_path / "shot-672.txt"
    probe.write_text("evidence")

    calls = {
        "next_task": lambda wf, c: wf.next_task(),
        "claim": lambda wf, c: wf.claim(c["id"]),
        "get_task": lambda wf, c: wf.get_task(c["id"]),
        "comment": lambda wf, c: wf.comment(c["id"], "заметка ревьюера"),
        "advance(to='build')": lambda wf, c: wf.advance(c["id"], to="build", spec="s"),
        "advance(to='review')": lambda wf, c: wf.advance(
            c["id"], to="review", worklog="w", evidence="abc123"),
        "advance(to='done')": lambda wf, c: wf.advance(c["id"], to="done"),
        "call_human": lambda wf, c: wf.call_human(c["id"], question="какой из двух вариантов?"),
        "return_task": lambda wf, c: wf.return_task(c["id"], reason="не понимаю задачу"),
        "decompose": lambda wf, c: wf.decompose(c["id"], [{"title": "A"}, {"title": "B"}]),
        "review_task(approve)": lambda wf, c: wf.review_task(
            c["id"], verdict="approve", report="ок"),
        "review_task(needs_work)": lambda wf, c: wf.review_task(
            c["id"], verdict="needs_work", report="вопрос человеку"),
        "file_task": lambda wf, c: wf.file_task("находка", related_task_id=c["id"]),
        "handoff": lambda wf, c: wf.handoff(c["id"], to="neighbour", title="другая половина"),
        "transfer_task": lambda wf, c: wf.transfer_task(
            c["id"], to="neighbour", reason="не та доска"),
        "attach_file": lambda wf, c: wf.attach_file(c["id"], str(probe), note="скрин"),
        # an attachment must EXIST first, or the refusal is "no such attachment" — nothing to do
        # with the stage, and counting it as one would inflate the refusal set by a tool
        "download_attachment": None,
    }
    refusals, ended_in = {}, {}
    for label, call in calls.items():
        api, wf, card = board()
        if label == "download_attachment":
            att = wf.attach_file(card["id"], str(probe), note="скрин")["attachment_id"]
            call = lambda wf, c, a=att: wf.download_attachment(c["id"], a)   # noqa: E731
        try:
            call(wf, card)
            refusals[label] = None
        except workflow.WorkflowError as exc:
            refusals[label] = str(exc)
        ended_in[label] = api.stage_of(card["id"])
    return refusals, ended_in


def test_exactly_ONE_agent_tool_walks_a_card_out_of_Review(tmp_path):
    """#672: the reviewer's bullet enumerated what is shut from Review, and #663's `decompose`
    gate made the enumeration stale without making one word of it false. A COUNT is what aged, so
    the bullet now leans on an invariant a new gate cannot age — out of Review a card is walked by
    exactly ONE agent tool, `review_task(verdict='needs_work')` — and this test is that sweep,
    made permanent. It also pins the one number the bullet still quotes (FIVE tools refuse), on
    purpose: a number that a test reddens goes stale LOUDLY, which is the only way it may be
    written down here at all.

    Driven off `server._DEFERRED_TOOLS` rather than a hand-written list, so a 13th agent tool
    cannot join the surface and quietly go unswept. Said in the right ORDER, because the first
    version of this sentence had it backwards: the sweep runs FIRST (it is the opening statement
    of the body — all 15 calls complete), and only then does the coverage block compare the
    exposed set against the swept labels. The SIZE assert is what an added tool trips; the
    `unswept` assert catches the other direction, a tool present in both places but missing a
    sweep entry. That coverage check is the half a hand-written sweep cannot have.

    Note what the sweep deliberately does NOT claim: `review_task(approve)` is a tool call that
    SUCCEEDS from Review and leaves the card exactly where it stood, so "one tool walks it out" is
    a statement about MOVEMENT, never about which calls are permitted. The refusal set and the
    mover set are asserted separately for that reason.

    MUTATION-CHECKED (`__pycache__` cleared between rounds, restored from file copies rather than
    `git checkout` — the tree's SKILL.md edit was uncommitted, and a checkout-based restore ate it
    once; control PASS before AND after the sweep; selection confirmed at exactly 1 test for this
    name). Each round names the assertion read out of `--tb=line`, not the one it seemed obvious it
    would hit: drop decompose's Review gate -> FAIL in the REFUSAL-SET assert (measured separately:
    the ungated tool returns `parent.moved_to == 'Backlog'` with two children created, so the mover
    assert would have fired too — the refusal set is simply reached first); drop return_task's
    Review gate -> FAIL the same way; make `review_task`'s needs_work branch leave the card in
    Review -> FAIL in the MOVER assert, measured `{}`; add a 13th tool to `server._DEFERRED_TOOLS`
    -> FAIL in the coverage block's SIZE assert (`13 == 12`), which names the intruder — the
    `unswept` assert below it is never reached on that mutant, and saying otherwise would
    miscredit it; move `advance`'s `_require_mine` to AFTER its stage check -> FAIL in the
    multi-identity REASONS assert, which is the round that shows that block measures the foreign
    card and not a second copy of the first."""
    swept, ended_in = _review_sweep(tmp_path)

    # COVERAGE: every tool the server really exposes is in the table above
    exposed = {fn.__name__ for fn in server._DEFERRED_TOOLS}
    assert len(exposed) == 14, f"the agent tool surface changed size: {sorted(exposed)}"
    unswept = exposed - {label.split("(")[0] for label in swept}
    assert not unswept, f"agent tools added to the server but not swept from Review: {unswept}"

    # THE REFUSAL SET — the number the rulebook quotes, spelled as the tools themselves
    refused = {label.split("(")[0] for label, err in swept.items() if err is not None}
    assert refused == {
        "claim", "advance", "call_human", "return_task", "decompose",
        "handoff", "transfer_task",
    }, f"SKILL.md's reviewer bullet quotes exactly these seven as refusing from Review: {refused}"
    assert all(swept[form] is not None
               for form in ("advance(to='build')", "advance(to='review')", "advance(to='done')")), \
        "SKILL.md says advance refuses from Review in ALL THREE forms; one of them now passes"

    # THE INVARIANT — one mover, and it is the door the reviewer is sent to
    movers = {label: stage for label, stage in ended_in.items() if stage != "Review"}
    assert movers == {"review_task(needs_work)": "Build"}, \
        f"SKILL.md says exactly one agent tool walks a card out of Review; measured: {movers}"

    # MULTI-IDENTITY: the card a reviewer really looks at is the IMPLEMENTER's. The bullet claims
    # the same five refuse there, only for different reasons — so sweep it rather than assume it.
    theirs, theirs_ended = _review_sweep(tmp_path, mine=False)
    assert {label.split("(")[0] for label, err in theirs.items() if err is not None} == refused, \
        "SKILL.md says the five refusals hold for a card owned by its implementer too"
    assert {lb: st for lb, st in theirs_ended.items() if st != "Review"} == \
        {"review_task(needs_work)": "Build"}, \
        "the one-mover invariant does not survive the card being someone else's"
    assert "claim it first" in theirs["advance(to='build')"], \
        "the reasons were supposed to be what differs: advance no longer answers on OWNERSHIP first"
    assert "claim it first" not in swept["advance(to='build')"], \
        "the two sweeps stopped differing at all — one of them is not building the state it claims"

    # the prose that rests on it
    bullet = _flat(_reviewer_bullet(_skill_text()))
    assert "EXACTLY ONE agent tool walks a card out of Review" in bullet, \
        "the reviewer's bullet no longer states the one-mover invariant it was rewritten around"


_SHUT_STAGES = ("Review", "Done")


def _top_level_parens(region: str) -> list[str]:
    """Every OUTERMOST parenthesised span of `region`, brackets included."""
    spans, depth, start = [], 0, 0
    for i, ch in enumerate(region):
        if ch == "(":
            if depth == 0:
                start = i
            depth += 1
        elif ch == ")" and depth:
            depth -= 1
            if depth == 0:
                spans.append(region[start:i + 1])
    return spans


def _open_stage_promise(region: str) -> str:
    """The ONE parenthesised span that enumerates the stages a gated tool still works from.

    VMCP-216 (759). Both open-stage pins used to read the CONCATENATION of every parenthesised
    span in their search region and ask only that each open stage APPEAR somewhere in it. That is
    one direction, and the missing one is not symmetry for its own sake: a promise list that also
    named a SHUT stage — «работает из пяти остальных (Backlog, Queue, Design, Build, Review)» —
    passed green, i.e. the pin accepted a rulebook telling agents to call `return_task` from
    exactly the stage the gate refuses. Measured before the fix, on the concatenation both pins
    then used: «Review» and «Done» are absent from return_task's (so the naive reverse assert
    would have been green there and proved nothing) and PRESENT in decompose's, because that
    bullet has no `\\n  - **` sub-bullets, `head_end` comes back -1, and the search region becomes
    the WHOLE bullet — eleven spans, among them `(#663)`, `(метка `reviewed`, ждёт человеческого
    Done)` and `(пуш — часть перевода в Review)`. So the reverse direction is not addable to the
    slice the pins had; it needs THIS one.

    Selecting by CONTENT rather than by position is what makes that safe, and it is the same trap
    the return_task comment already priced: a span ending at the first «)» after the rule reddens
    on any bracket landing between the anchor and the list — a card ref, an inline-code aside, a
    gloss on one stage, or the list simply being written first. Asking for the span that names all
    five open stages survives every one of those, and its failure mode is the one worth having:
    drop a stage from the promise and no span qualifies, which reddens with the missing stage
    named by the caller's own per-stage assert rather than with a slicing error.
    """
    open_stages = [s for s in workflow.STAGES if s not in _SHUT_STAGES]
    promise = [s for s in _top_level_parens(region) if all(st in s for st in open_stages)]
    assert len(promise) == 1, (
        f"the rulebook's promise that a gated tool still works from {open_stages} is no longer "
        f"exactly ONE parenthesised list: {len(promise)} spans qualify. Either a stage was "
        "dropped from it (then the per-stage assert next door names which) or the enumeration "
        "was split, in which case the SHUT-stage check below has nothing to read"
    )
    return promise[0]


def _return_task_bullet(text: str) -> str:
    """The `return_task` bullet inside the stuck section — where its shut stages are spelled out.

    Sliced to the BULLET, and #667 re-derived WHY, because the reason standing here until then
    cited a sentence of SKILL.md that never existed. Not stale, and not an elision either: in
    EVERY revision of SKILL.md this history holds the sentence is absent, and so is the bare word
    «ждущей» — zero hits read RAW and zero read whitespace-FLATTENED alike. The load-bearing
    figure is that ZERO, not the denominator, which is why the denominator is dated rather than
    pinned: 71 revisions when this was run on 2026-08-02, and it grows with every rulebook edit —
    it went 69 -> 71 during this very round, on the rebase before the push. (67 at this card's
    own first round, 72c6879; the four SKILL.md landings since — #547, #631 and two from #628 —
    are what made it 71, the count of COMMITS since that round being a larger and different
    number.) The PRESENT control is NAMED so it can be re-derived instead of trusted — this
    section's own heading, «Застрял? Выход зависит от РОЛИ», hits 12 of those 71; the absent
    control is any string the file never contains, and hits 0. Both readings are reported because
    only the SENTENCE could ever have wrapped: `git log -S` matches raw bytes and this file
    line-wraps, which is not hypothetical — the nearest live text, «ждёт человеческого Done» in
    the `decompose` bullet, wraps between its first two words and answers the pickaxe only as
    «человеческого Done». Repo-wide, with the full sentence as the needle, it APPEARED in exactly
    one commit: 6ac1454, which added the docstring line that quoted it — a line 72c6879 then
    deleted, so nothing in the text you are reading contains it. Named that way because the needle
    decides the caveat: the sentence now also names that DELETING commit, while the bare word
    never will, its count being unchanged across that edit and across this one. Nothing to
    re-quote, so the rationale is now the mutants themselves, each run at three scopes: this
    bullet as shipped, `_stuck_section(text)`, and all of `text`.

      * delete the bold rule sentence and its parenthesised open list -> RED at bullet AND at
        section scope, GREEN file-wide: the very string this pin asserts also stands in the
        `decompose` bullet two sections up, where only a whole-file read can reach it.
      * delete the sentence routing unusable Done work to `file_task` -> RED at bullet scope
        ONLY, `file_task` recurring inside this same section, in the reviewer bullet's last
        sub-bullet. This is the one half of the superseded rationale that was true.
      * delete the `#649` ref the caveat rests on, which the decompose test reads from HERE ->
        RED at bullet AND at section scope, GREEN file-wide, `#649` sitting in that bullet too.

    So the slice is load-bearing at BOTH steps of the narrowing, and the two steps are held by
    DIFFERENT rounds. File scope is blind to all three rounds above, and to a FOURTH assertion:
    the loop requiring each open stage. That loop takes a round PER STAGE, and measuring it with
    ONE stage is how the sentence 72c6879 left standing here came out false — it named
    «Queue» alone among the stage-drops and concluded that exactly one round justified the last
    step. (Named, which is what the record shows; whether only that one was RUN is not
    observable.) All five stages, RE-MEASURED on 2026-08-03 when #700 rebuilt that loop's window,
    alongside the count of that token's twins inside the SECTION but outside this bullet — and,
    now that the window is built from BRACKETS, the count of those twins that stand inside a
    parenthesised group, which is the number that actually decides the row:

        drop Backlog    RED / RED   / n-a    0 section twins, 0 of them bracketed
        drop Queue      RED / RED   / n-a    0                0
        drop Design     RED / RED   / n-a    1                0
        drop Build      RED / RED   / n-a    5                0
        drop Your Call  RED / GREEN / n-a    8                1

    The FILE column is «n-a», not GREEN, and the reason is worth more than the row would have
    been: after #700 the test also asserts that the rule string occurs exactly ONCE in its slice,
    and file-wide it occurs TWICE — line 1585 in the `decompose` bullet and line 1770 here — so
    the file-scope probe fails its own control. Every file-scope figure would be a delta against
    a dirty baseline, which is the one thing this repo's sweep contract forbids writing down. The
    duplicate anchor is the same fact the pre-#700 record reported as a file-scope GREEN; it has
    simply stopped being expressible as a colour.

    Twin counts in PROSE used to be the mechanism here and are not any more. Before #700 the loop
    read the whole bullet: «Backlog» scored GREEN/GREEN/GREEN, blind at every scope because three
    copies of the word survive in this bullet's own prose; Design, Build and Your Call went
    RED/GREEN/GREEN on their section twins; «Queue», twinless section-wide, went RED/RED/GREEN.
    Those pre-#700 rows were RE-RUN at 5539ebae in a clone of their own rather than carried over
    from the record they replace — an inherited figure is the thing this docstring has been wrong
    about before. The loop now unions the parenthesised GROUPS above this bullet's sub-bullets, so
    a twin only counts if it is bracketed, and that is exactly what splits the column: Design's
    one section twin and Build's five sit in running prose and no longer reach the window, while
    «Your Call» has a bracketed one — the `call_human` bullet's «(Your Call = припаркована, не
    твоя активная)», printed rather than assumed — and is the only stage that still goes GREEN a
    scope out. So the last step of the narrowing, section down to bullet, is held by TWO of the
    rounds run here: `drop Your Call` and the `file_task` sentence. An enumeration of the rounds
    RUN, not a proof that no other mutant holds it; and scored with BOTH consumers in the
    selection, which matters, because a round can hold this step for one test and not for the
    suite (the wider «Из Done» reading below is exactly that). The FIFTH assertion read from
    here, the caveat's «следующий мутирующий тул», has no twin anywhere in SKILL.md and reddens
    at every scope: it shows that assertion is live, not that the slice is.

    The superseded claim is written down so it is not re-derived. Beside the quotation dealt with
    above, its MECHANISM is wrong too: it said `Done` recurs in this section's reviewer
    sub-bullet. It does not — that bullet contains `Done` zero times, and all ten occurrences in
    the section sit inside THIS one. WHICH round it recorded is a THIRD question, and the record
    cannot settle it. #626's wording — «delete the Done sentence from the bullet while leaving
    «Done» and `file_task` elsewhere in the section» — fits the SECOND round bulleted above, the
    lone sentence carrying `file_task`, and fits a WIDER reading just as well: delete the whole
    «- **Из Done** (#626)» sub-bullet. Both readings were built and compared here, and the two
    obvious discriminators both fail on measurement.

      * `file_task` goes 1 -> 0 in the bullet and 2 -> 1 in the section on EITHER reading, and
        the survivor is the SAME line both times, in the reviewer bullet's last sub-bullet. So
        "leaves `file_task` outside the bullet but inside the section" — 308ef14's stated reason
        for picking the narrow reading — is true of both and picks neither.
      * Neither does #626's recorded «section-wide GREEN», and that is measured AT 6ac1454 rather
        than argued from today's tree. Its own docstring says «selection confirmed at exactly 1
        test», and at that commit this helper HAD exactly one consumer: the decompose test's
        `stuck = _return_task_bullet(text)` arrived later, with #649's ca05756. Checked out there
        and run under that one test, BOTH readings score RED at bullet and GREEN section-wide,
        failing the same `file_task` assertion.

    So this card named the wider reading in its first round and the narrow one in its second, and
    the record supports neither over the other. Two things still point at the narrow one, and
    both are weaker than a measurement of what was RUN: #626 wrote «the Done SENTENCE», singular,
    where the wider reading deletes a whole sub-bullet — five full stops of it at 6ac1454, six
    today, that being the figure to quote against a claim made then; and only the narrow reading
    still reproduces that outcome on TODAY's suite. Read what FOLLOWS as being about the narrow
    MUTANT, then, not as a finding about which round #626 ran.

    Scored with BOTH consumers in the selection, that mutant is RED at bullet and GREEN
    section-wide, `Done` moving 10 -> 9 in the bullet and in the section alike. What reddens is
    the `file_task` assertion and not a `Done` check: a bare `Done` substring stays GREEN at
    EVERY scope there, and still does with the rule line deleted too (8 left in the bullet). On
    the WIDER mutant it runs the other way — delete that sub-bullet AND the rule line and `Done`
    is 0 in the bullet, 0 in the section and 12 file-wide, so a bare `Done` check DOES separate
    file scope there. Which token demonstrates a slice is a property of the mutant, never of the
    token, and that now rests on a positive case as well as a negative one.

    The wider reading is also where today's suite and #626's selection come apart, which is worth
    writing down because the two readings look equivalent. Under the test below ALONE it scores
    RED at bullet, GREEN section-wide — 6ac1454's answer, above. Add the decompose test and it
    reddens at bullet, section AND file, `#649` and the caveat both sitting inside the deleted
    text, so the suite scores it RED at every scope. That difference is a property of the SECOND
    consumer, which #626 did not have.

    Scope OUTSIDE the bullet is the only thing this helper decides. Whether each assertion is
    then sliced tightly enough INSIDE it is a SEPARATE question, and it was open here until #700
    closed it for the stage loop: dropping «Backlog» from the promise list was measured GREEN at
    all three scopes. The two questions are COUPLED, which is why the table above moved when #700
    landed — it is re-measured there, not carried over. The other rounds were re-run alongside it
    rather than assumed unaffected, and TWICE, because the window was rebuilt once more after
    review: the `file_task` sentence still scores RED at bullet and GREEN at section, rule+list
    RED at both, `#649` RED at both. Closing it for the loop does not close it for the
    file, and the size of what is left is measured rather than guessed at — ANCHORED, because a
    count over this file is a count over a file other cards edit, and this one has already gone
    stale twice. At `6a3e644`, of the 157 `assert "LIT" in <slice>` assertions here, 133 hold a
    needle that occurs EXACTLY ONCE in the slice the helper actually returned and 3 test a non-str
    container, so 136 cannot have this defect at all; the remaining 21 are the class #758 screened
    (now carried by the umbrella VMCP-227 (771)), with 11 of them measured blind to the removal of
    any single occurrence. Re-run rather than quote: an AST transformer rewrites each such
    assertion into a recording call that counts the needle in the container at RUNTIME, driven by
    pytest so the fixture-taking tests execute too. Do not screen with a source regex — at the same
    commit it says 169, over-counting inside docstrings — and do not screen without running: 16 of
    the 157 sit in the six tests that take a fixture, so a bare `exec` of the module reaches 141.
    The trajectory is the argument for the anchor, and it is ATTRIBUTED rather than re-derived:
    154/130/133 is what #758's card reports for its own run at `5539eba`, and 155/131/134 what
    VMCP-228 (772) reports at `dd81dde`; both trees are ancestors of this one.
    NONE of the 21 is read from THIS helper: its three surviving needles — `file_task`
    in the test below, `#649` and «следующий мутирующий тул» in the decompose test — each occur
    EXACTLY ONCE in the bullet, so the loop was the only twin-blind assertion reading it. The
    nearest two belong to neighbours: `file_task` twice in `_decompose_bullet`, and `file_task`
    twice in `_stuck_section` at the reviewer-routing test.

    No new code assertion was added here, and the reason first given for it — in #667's own first
    commit message, 72c6879: "would redden on any legitimate rewording of a neighbouring section"
    — does not survive measurement. What is pinnable is the REDUNDANCY behind two of the rounds
    above: the rule string and `#649` each occur twice in SKILL.md, the OTHER time in the
    `decompose` bullet — which comes FIRST in the file, two sections above this one. That side is
    ALREADY pinned, and with THIS file untouched: rewording that bullet's rule line reddens
    `test_..._decompose_refuses_from` at its «no longer states WHICH stages decompose refuses
    from» assertion, and deleting `#649` from it reddens the same test at «no longer explains WHY
    Done is shut». So the very rewording said to make a redundancy pin noisy already fails a pin
    that exists. On those two edits a third assertion adds no alarm that is not raised today: the
    objection to it is redundancy, not noise. (Which is not the same as "it would never fire
    spuriously" — a THIRD copy of the rule string appearing anywhere would trip a count-based pin
    and nothing else. That case was not measured, and is not what the superseded reason claimed.)

    (Every round named above was re-run for THIS revision in an isolated `git clone
    --no-hardlinks`: the five-stage table, the three bulleted rounds, the caveat round, both
    readings of #626's round — suite-wide AND per consumer — and their `file_task` and `Done`
    counts, the revision scan, the repo-wide pickaxe, the two decompose probes, and both halves
    of the #700 coupling. The 6ac1454 comparison took a SECOND clone, checked out at that commit
    and run against its own single consumer. `vikunja_mcp.__file__` confirmed inside each clone,
    `__pycache__` cleared between rounds, restores sha256-verified, an unmutated control PASS at
    both ends, and the counts re-run once more after the final rebase, since these figures
    are counts over a file other cards edit. The clone is not ceremony: run in the live worktree,
    an earlier sweep here raced a second agent's on the same two files, and an unmutated control
    is what caught it — #702.)"""
    start = text.find("\n- **`return_task`** — an external blocker")
    assert start != -1, "SKILL.md no longer describes return_task in the stuck section"
    end = text.find("\n- **", start + 1)
    assert end != -1, "the return_task bullet no longer ends where the next top-level bullet does"
    bullet = text[start:end]
    assert 0 < len(bullet) < len(text), "the return_task slice is not a proper subset of SKILL.md"
    assert "For the REVIEWER" not in bullet, "the slice swallowed the reviewer's bullet"
    return bullet


def test_the_rulebook_names_BOTH_stages_return_task_refuses_from():
    """#626: after #590 this bullet said «Из Review он ОТКАЗЫВАЕТ … Из остальных стадий он
    по-прежнему работает» — a sentence that POSITIVELY described the Done path as normal, and it
    self-heals onto every consumer. Measured at the time: `return_task` really did walk a card out
    of Done (the transition CLAUDE.md calls human-only) — one of SEVERAL agent tools that could,
    never the only one, so the rulebook was advertising an agent bypass of that invariant as
    supported. `decompose` was the other known one — measured on the same card, untouched by THIS
    diff, gated separately by #649 — so shutting this door did not shut them all, and the class
    (no single rule anywhere) is still open by construction.

    Both halves are pinned against the TOOL, not just as words, because prose and gate drifting
    apart is the failure this card is about: the gate must refuse from Done AND the bullet must say
    so, and the five stages the bullet still promises must genuinely stay open.

    Token presence is NOT enough here, and that is measured rather than assumed: an earlier version
    of this pin asserted only that «Review», «Done» and `file_task` occur in the bullet, and a
    mutant that INVERTED the rule — «отказывает только из Review, а из Done … работает штатно» —
    kept all three words and sailed through GREEN. That mutant is precisely the sentence this card
    exists to delete, so the pin now asserts the RULE (the enumeration of shut stages, verbatim)
    and derives the open list from `workflow.STAGES`, which also ties the prose to the code: add a
    stage, or shut another one, and this fails until the rulebook is updated too. Reword the bullet
    freely — but the rule has to still be spelled out, and then this string moves with it.

    MUTATION-CHECKED (`__pycache__` cleared between rounds, selection confirmed at exactly 1 test):
    control PASS; delete the Done sentence from the bullet while leaving «Done» and `file_task`
    elsewhere in the section -> FAIL (and a SECTION-wide substring was measured GREEN on that same
    mutation); INVERT the rule keeping every token -> FAIL; drop `Your Call` from the open list ->
    FAIL; drop return_task's Done gate -> FAIL; drop its Review gate -> FAIL.

    #700 then found that the open-stage loop did not pin the one stage this bullet is ABOUT. It
    read the WHOLE bullet, and «Backlog» — the stage `return_task`'s entire job moves a card TO —
    occurs three more times in that bullet's own prose: the opening «уходит в Backlog на
    ре-триаж», #626's «уводил … в Backlog без ассайни», and the decompose caveat's «уводил
    родителя в Backlog». All three sentences are load-bearing, so the redundancy stays in
    SKILL.md and the PIN narrows. Measured in an isolated `git clone --no-hardlinks`
    (`vikunja_mcp.__file__` confirmed to resolve INSIDE the clone, `__pycache__` cleared per
    round, `PYTHONDONTWRITEBYTECODE=1`, every restore sha256-verified, whole file as the
    selection, `collected 50 items` every round), one stage removed from the parenthesised list
    at a time:

        control                                                          0 failed
        unsliced loop: drop Backlog                                      0 failed  <-- blind
        unsliced loop: drop Queue / Design / Build / Your Call           1 failed each
        sliced loop:   drop Backlog                                      1 failed
        sliced loop:   drop Queue / Design / Build / Your Call           1 failed each

    «Backlog» is the only mutant here that can demonstrate the slice: the other four have no twin
    in the bullet and redden either way.

    THE WINDOW IS BUILT FROM BRACKETS, not from a span anchored on the rule, and that is the
    second design this card shipped — the first one was measured brittle by an adversarial second
    pass and replaced. A span `bullet[rule_at:bullet.find(")", rule_at) + 1]`, which is what the
    `decompose` sibling still uses, reddens on ANY bracket that lands between the anchor and the
    end of the list; against control 0 failed, four ordinary rewordings scored 1 failed each — a
    card ref on the rule («— Review и Done (#590, #626), — а работает…»), the same in inline code
    («(`workflow.STAGES` минус эти две)»), a gloss on ONE stage inside the list («Design (спека
    ещё не написана)»), and the list simply written BEFORE the rule sentence. This file is full
    of `(#NNN)` and `(измерено …)`, so that is a live cost, not a hypothetical one. Unioning the
    parenthesised groups above the sub-bullets scores 0 failed on all four and still kills every
    stage-drop, because the only bracket in this head that is not the promise — the opening
    «(чужой сервис лежит, нет зависимости, задача потеряла смысл)» — names no stage.

    ELEVEN benign edits now measure 0 failed against control 0 failed, one round each: the four
    above, plus reordering the stages inside the list, reflowing the line break between the rule
    and the list, rewording the clarifier that follows the list, rewording the bullet's opening
    sentence (which kills one «Backlog» twin), adding «— все пять» inside the list, rewording the
    whole «Из Done» sub-bullet holding the other two twins, and un-bolding the rule. NO false red
    is known — which is a statement about eleven constructed edits, not a proof that none exists.

    The loose alternative was BUILT and measured too, against control 0 failed, and is still
    rejected: ending the window at the first sub-bullet without restricting to brackets survives
    the same four (0 failed) but
    goes QUIET on a mutant this one catches — drop `Build` from the list while the clarifier
    below it gains «(скажем, из Build)», 0 failed loose against 1 failed here. A bracket union
    keeps that kill because the clarifier's gloss is a bracket the loop reads, and the stage is
    then genuinely still promised somewhere in the head.

    A UNIQUENESS GUARD rides along, and it exists because of a measured attack rather than a
    worry: quote the pre-#700 formulation of the gate one sentence above the live rule — the kind
    of sentence this bullet's own meta-prose invites — and then drop «Backlog» from the LIVE
    list. `find` locks onto the quotation, the window is the QUOTED list, and the exact bug this
    card exists to kill is back. Against control 0 failed that mutant scored 0 failed; with
    `bullet.count(<rule>) == 1` asserted it is 1
    failed. (First reproduction attempt scored it 1 failed and was INVALID: the quote had been
    line-wrapped, so the anchor never matched inside it. The round measured the wrapping, not the
    attack. Re-run unwrapped, it reproduced.)

    WHAT THIS PIN DOES NOT CATCH, measured on the shipped form, each against control 0 failed —
    and stated at its measured size, because an earlier draft of this paragraph described the
    residual hole as merely ADDITIVE and the second pass disproved that by construction:
      * the words AROUND the names are not read at all, so the promise can be INVERTED or
        retracted stage by stage while every name stays in the brackets. «— и из остальных пяти
        тоже НЕ работает (Backlog, Queue, Design, Build, Your Call)» scores 0 failed; so do
        «работает ТОЛЬКО из Build», a per-stage carve-out, a `force=True` qualifier, and
        demoting the list to an unimplemented PLAN.
      * a list that also names a SHUT stage passed: «, Review» scored 0 failed, and so did
        naming all seven. **CLOSED by VMCP-216 (759)** — re-run against the same control of
        0 failed, «, Review» appended to the promise is now 1 failed, and so is «, Done» on the
        decompose sibling. What made it addable is not a second `in` but a second SLICE: the
        `_open_stage_promise` helper picks the ONE bracket group naming all five open stages, so
        the question "is a shut stage in the promise" can be asked of the promise instead of of
        every bracket in the head — which on the sibling would have read `(#663)` and
        `(пуш — часть перевода в Review)` and answered yes about prose.
      * the live rule and promise can be DELETED outright if a historical quotation is left
        behind to satisfy both the anchor and the names: 0 failed. The uniqueness guard catches
        the variant that keeps the live rule, not this one.
      * deleting the promise list with nothing left in its place IS caught: 1 failed.
    What is LEFT of that class after 759 is the first bullet and only the first: the pin reads
    NAMES, never CLAIMS, so the verb in front of the enumeration is still unread. That half is
    not being widened by a substring test, and the reason is measured rather than deferred — the
    enumeration is bare stage names, identical under «работает из» and «НЕ работает из», so
    distinguishing them means pinning a wording in the one file whose whole design is that it
    gets rewritten and self-heals onto every consumer. The SHUT-stage half needed no such
    wording, which is why it could be closed and this cannot."""
    text = _skill_text()
    bullet = _return_task_bullet(text)

    # the RULE, not its vocabulary: which stages are shut, spelled out
    assert "It REFUSES from TWO stages — Review and Done" in bullet, \
        "the bullet no longer states WHICH stages return_task refuses from (#590 Review, #626 Done)"
    # ...and it must be stated ONCE. A second copy — a card quoting an older formulation of the
    # gate, which this bullet's meta-prose invites — would leave the pin unable to tell the live
    # rule from the quotation, and measurably lets the promise be gutted next to a quote that
    # still reads correctly (#700, «quoted anchor» in the docstring).
    assert bullet.count("It REFUSES from TWO stages — Review and Done") == 1, \
        "the rule is spelled out TWICE in this bullet; the pin can no longer tell which is live"
    assert "file_task" in bullet, \
        "the bullet no longer routes unusable Done work to file_task, the one channel left"
    # ...and every stage that is NOT shut must be named in the promise, the complement coming
    # straight out of the code — and, since VMCP-216 (759), no stage that IS shut may be named in
    # it. The second direction is the one that was missing: it is what refuses a promise reading
    # «работает из пяти остальных (Backlog, Queue, Design, Build, Review)», i.e. a rulebook
    # sending an agent to the exact stage the gate rejects. What is STILL only one direction is
    # the third finding of #759 — that the words AROUND the enumeration still promise something —
    # and it stays a finding: the enumeration is bare stage names, so nothing in the span itself
    # distinguishes «работает из» from «отказывает из», and pinning the verb means pinning a
    # wording in a file whose whole point is that it gets rewritten.
    # Scoped to the parenthesised ENUMERATIONS above
    # the first sub-bullet, NOT the whole bullet (#700): «Backlog» occurs three more times in this
    # bullet's own prose — the opening «уходит в Backlog на ре-триаж» and, inside the «Из Done»
    # sub-bullet, #626's «уводил … в Backlog без ассайни» and the decompose caveat's «уводил
    # родителя в Backlog» — so an unsliced `in` stayed GREEN with «Backlog» quietly dropped from
    # the promise, and «Backlog» is the ONE stage the whole bullet is about. The other four have
    # no twin in the bullet and redden at either scope, so none of them can demonstrate this.
    # Parenthesised rather than a span anchored on the rule: the promise IS an enumeration in
    # brackets, and a span ending at the first «)» reddens on any bracket that lands between the
    # anchor and the list — a card ref, an inline-code aside, a gloss on one stage inside the
    # list, or the list simply being written before the rule. All four measured; see the docstring.
    head_end = bullet.find("\n  - **", bullet.find("It REFUSES from TWO stages"))
    assert head_end != -1, "the return_task bullet no longer has the sub-bullets naming its gates"
    open_list = "\n".join(_top_level_parens(bullet[:head_end]))
    for stage in workflow.STAGES:
        if stage in _SHUT_STAGES:
            continue
        assert stage in open_list, \
            f"the bullet promises the OTHER stages keep working but never names {stage!r}"
    promise = _open_stage_promise(bullet[:head_end])
    for stage in _SHUT_STAGES:
        assert stage not in promise, (
            f"the bullet's promise of the stages return_task still works from names {stage!r}, "
            f"which is one of the two the same sentence has just said it REFUSES from: {promise}. "
            "One direction alone accepted that (VMCP-216 / 759) — an agent reading the promise "
            "would call return_task from a stage the gate rejects, and the pin said nothing"
        )

    # the code: both doors really are shut
    api = FakeAPI(buckets=workflow.STAGES)
    wf = workflow.Workflow(api, project_id=3)

    accepted = api.add_task("accepted by a human", "Done", assignee=api.me_user)
    with pytest.raises(workflow.WorkflowError) as done:
        wf.return_task(accepted["id"], reason="внешний блок")
    assert "file_task" in str(done.value), \
        "SKILL.md says return_task refuses from Done and points at file_task; it no longer does"
    assert api.stage_of(accepted["id"]) == "Done", "the refusal walked the accepted card back anyway"

    under_review = api.add_task("under review", "Review", assignee=api.me_user)
    with pytest.raises(workflow.WorkflowError):
        wf.return_task(under_review["id"], reason="внешний блок")
    assert api.stage_of(under_review["id"]) == "Review"

    # ...and the five stages the same bullet still promises are genuinely open
    for stage in ("Backlog", "Queue", "Design", "Build", "Your Call"):
        card = api.add_task(f"blocked in {stage}", stage, assignee=api.me_user)
        assert wf.return_task(card["id"], reason="чужой сервис лежит")["moved_to"] == "Backlog", \
            f"the bullet promises return_task still works from {stage}; it does not"


def _decompose_bullet(text: str) -> str:
    """The `decompose` bullet in «Декомпозиция и файлинг находок» — where an agent looks the tool
    up, and therefore where its shut stage has to be written.

    Sliced to the BULLET rather than the section, and the difference was measured on the mutant
    that deletes this bullet's Done sentences outright: at SECTION scope «Done» survives in the
    epic-lifecycle bullet («весь набор … в Done уводит ЧЕЛОВЕК») and `file_task` survives in its
    own bullet a few lines below, so a section-wide TOKEN check stays GREEN on exactly the
    deletion this pin exists to catch. Stated precisely, because the honest half matters too: the
    verbatim RULE string is gone at either scope, so it is the token assertions — «Done», the open
    stage list — that the bullet slice makes meaningful, not every assertion here."""
    start = text.find("\n- **`decompose` is about YOUR task.**")
    assert start != -1, "SKILL.md no longer describes decompose in the decomposition section"
    end = text.find("\n- **", start + 1)
    assert end != -1, "the decompose bullet no longer ends where the next top-level bullet does"
    bullet = text[start:end]
    assert 0 < len(bullet) < len(text), "the decompose slice is not a proper subset of SKILL.md"
    assert "The life cycle of an epic" not in bullet, "the slice swallowed the epic-lifecycle bullet"
    return bullet


def test_the_rulebook_names_BOTH_stages_decompose_refuses_from():
    """#649 shut Done here; #663 shut Review, and this pin now carries both. Until #649 landed,
    `decompose` walked a card a human had ACCEPTED out of Done — to Backlog, unassigned, carrying
    `reviewed` and `epic` at once, with fresh children in Queue — while the rulebook said nothing
    about it in the place an agent reads decompose. #649 then wrote «ОТКАЗЫВАЕТ из ОДНОЙ стадии —
    Done, — а работает из шести остальных (… Review …)», true of the code and false as advice:
    `decompose` did the same thing to a card in REVIEW — the shape #590 had already gated for
    `return_task` — so the rulebook was POSITIVELY promising that path, the exact failure mode
    #626's own bullet had before it. Measured for #663 on a card driven the normal way to Review:
    the parent left for Backlog with `epic` and no assignee, two children in Queue; on an APPROVED
    card (label `reviewed`, waiting only for a human's Done) with `reviewed` AND `epic` at once.
    The gate and the sentence land together because prose and gate drifting apart is the failure
    this whole file exists to catch.

    Pinned against the TOOL as well as the words. The rule (WHICH stages are shut) is asserted
    verbatim, because token presence is measurably not enough — that was proven on #626's pin,
    where a mutant inverting the rule kept every token and sailed through green. The open list is
    derived from `workflow.STAGES`, so adding a stage or shutting another one fails here until the
    rulebook is updated too; reword the bullet freely, but the rule has to still be spelled out.

    The `return_task` bullet's caveat is checked from here too, and it is the reason this pin is
    not just about decompose: that caveat is what stops a reader concluding «из Done теперь не
    уводит ничто» from a clean sweep. #626 wrote it naming decompose as the live counter-example;
    #649 removed that counter-example, so the caveat carries the CLASS instead (the rule is nowhere
    written once — the next mutating tool reopens the hole). #663 does not change that: it shuts a
    REVIEW door, and the Done class the caveat describes is untouched. A caveat that decayed into
    «all doors are shut» would be worse than none, so it is pinned, not trusted.

    MUTATION-CHECKED (`__pycache__` cleared between rounds, restore sha256-verified): control
    PASS; drop decompose's Done gate -> FAIL on the code half (DID NOT RAISE); drop its Review gate
    -> FAIL on the Review half of the code (DID NOT RAISE); delete the whole rule+stages block from
    the decompose bullet -> FAIL on the rule assertion (that one reddens at either scope, so it is
    the TOKEN assertions the bullet slice protects — the section-vs-bullet measurement behind that
    belongs to #649 and is recorded in `_decompose_bullet`, on ITS mutant, and is NOT re-derived
    here); delete ONLY the «Из Done (#649) …» explanation, leaving the bold rule ->
    FAIL on the `#649` assertion, and that assertion exists BECAUSE the same mutant was measured
    GREEN first: adding the Review half put `file_task` in both halves, so #649's inherited
    `file_task in bullet` check silently stopped covering the Done one; delete ONLY the «Из Review
    (#663) …» explanation -> FAIL on `needs_work`, the door that half names; INVERT the rule
    keeping every token -> FAIL; soften the return_task caveat into "nothing walks a card out of
    Done any more" -> FAIL.

    The mutant that shows the open list must be SLICED is `drop Build`, and picking it took a
    measurement rather than a guess — an earlier draft named `Your Call`, which cannot show it.
    Both scopes were run for all five open stages (stage removed from the parenthesised promise,
    and `open_list = bullet` to simulate the unsliced pin). Sliced: all five FAIL. Bullet-wide:
    dropping Backlog, Queue or Build still PASSES — each of those words occurs three more times in
    this bullet's prose — while dropping Design or `Your Call` FAILS anyway, because neither
    appears anywhere outside the list. So those two redden at any scope and prove nothing about
    scope; the three blind ones are the only usable mutants here. One of the three is this diff's
    own doing and is worth knowing before editing the prose again: before the Review half was
    written «Build» occurred 0 times outside the list, and the sentences routing a reviewer back to
    Build («принимается в Build», «вернётся ИМПЛЕМЕНТЕРУ в Build», «вернуть карточку в Build») put
    it at 3.

    THE OTHER DIRECTION LANDED WITH VMCP-216 (759): a promise list may not name a stage the same
    sentence has just called shut. It was the sibling pin that recorded this hole («a list that
    also names a SHUT stage passes»), but the hole was HERE too and here it is the harder one,
    which is why the fix is a helper and not a line. MUTATION-CHECKED, `__pycache__` deleted per
    round then `PYTHONDONTWRITEBYTECODE=1`, this file as the selection, each round restored from
    a COPY and the restore confirmed by sha256 and by returning to the control. Control round:
    0 failed. Append «, Done» to this bullet's promise -> 1 failed; append «, Review» to the
    return_task sibling's -> 1 failed. The naive form of the same assert — ask the SEARCH REGION
    the forward loop reads — could not have been landed here at all: this bullet has no
    `\\n  - **` sub-bullets, so that region is the whole bullet, and measured it holds eleven
    top-level bracket groups including `(#663)`, `(пуш — часть перевода в Review)` and
    `(метка `reviewed`, ждёт человеческого Done)`. Asking THOSE whether a shut stage is named
    answers yes about prose and would have been red on arrival."""
    text = _skill_text()
    bullet = _decompose_bullet(text)

    # the RULE, not its vocabulary: which stages are shut, spelled out
    rule_at = bullet.find("**It REFUSES from TWO stages — Review and Done")
    assert rule_at != -1, \
        "the decompose bullet no longer states WHICH stages decompose refuses from " \
        "(#663 Review, #649 Done)"
    assert "file_task" in bullet, \
        "the bullet no longer routes work an accepted card revealed to file_task"
    assert "needs_work" in bullet, \
        "the bullet no longer routes a reviewer who wants the card split back to Build"
    # each half must still say WHY, and the card refs are what anchor them. Not decoration: with
    # the Review half added, `file_task` occurs in BOTH halves, so the assertion above stopped
    # covering the Done half — measured, deleting the whole «Из Done (#649) …» explanation while
    # leaving the bold rule intact was GREEN until these two lines existed.
    assert "#663" in bullet, "the bullet no longer explains WHY Review is shut (#663)"
    assert "#649" in bullet, "the bullet no longer explains WHY Done is shut (#649)"
    # ...and the open list must be exactly the complement, straight out of the code. Scoped to the
    # parenthesised list, NOT the whole bullet: «Backlog», «Queue» and «Build» each occur three
    # more times in this bullet's prose ("подзадачи встанут в Queue", "вернётся ИМПЛЕМЕНТЕРУ в
    # Build"), so an unsliced `in` stays GREEN with any of those three quietly dropped from the
    # promise — measured at both scopes for all five open stages. «Design» and «Your Call» occur
    # nowhere else and would redden either way, so neither can demonstrate this.
    open_list = bullet[rule_at:bullet.find(")", rule_at) + 1]
    for stage in workflow.STAGES:
        if stage in _SHUT_STAGES:
            continue
        assert stage in open_list, \
            f"the bullet promises the OTHER stages keep working but never names {stage!r}"
    # ...and no SHUT stage may be named in it — VMCP-216 (759), the direction both open-stage
    # pins were missing. Read from `_open_stage_promise` over the WHOLE bullet rather than from
    # `open_list` above, and the difference is measured rather than stylistic: this bullet has no
    # `\n  - **` sub-bullets, so a search region cut the way the return_task pin cuts its own
    # would be the whole bullet, whose eleven parenthesised spans include `(#663)` and
    # `(пуш — часть перевода в Review)`. Selecting the span that names all five open stages is
    # what makes the reverse question answerable at all here.
    promise = _open_stage_promise(bullet)
    for stage in _SHUT_STAGES:
        assert stage not in promise, (
            f"the bullet's promise of the stages decompose still works from names {stage!r}, "
            f"one of the two the same sentence has just said it REFUSES from: {promise}. An "
            "agent reading it would decompose from a stage the gate rejects (VMCP-216 / 759)"
        )

    # the caveat next door must still carry the CLASS, not a counter-example #649 removed
    stuck = _flat(_return_task_bullet(text))
    assert "#649" in stuck, \
        "the return_task caveat still names decompose as an OPEN bypass, or stopped naming it"
    assert "the next mutating tool that moves a card and does not check the stage" in stuck, \
        "the caveat decayed into 'every door is shut' — the class is still open by construction"

    # the code: both doors really are shut, and the five others really are open
    api = FakeAPI(buckets=workflow.STAGES)
    wf = workflow.Workflow(api, project_id=3)

    accepted = api.add_task("accepted by a human", "Done", assignee=api.me_user)
    with pytest.raises(workflow.WorkflowError) as done:
        wf.decompose(accepted["id"], [{"title": "A"}, {"title": "B"}])
    assert "file_task" in str(done.value), \
        "SKILL.md says decompose refuses from Done and points at file_task; it no longer does"
    assert api.stage_of(accepted["id"]) == "Done", "the refusal split the accepted card anyway"

    under_review = api.add_task("under review", "Review", assignee=api.me_user)
    with pytest.raises(workflow.WorkflowError) as review:
        wf.decompose(under_review["id"], [{"title": "A"}, {"title": "B"}])
    assert "needs_work" in str(review.value), \
        "SKILL.md says decompose refuses from Review and points at review_task; it no longer does"
    assert api.stage_of(under_review["id"]) == "Review", "the refusal split the card under review"

    for stage in ("Backlog", "Queue", "Design", "Build", "Your Call"):
        card = api.add_task(f"big job in {stage}", stage, assignee=api.me_user)
        assert wf.decompose(card["id"], [{"title": "A"}, {"title": "B"}])["parent"]["moved_to"] \
            == "Backlog", f"the bullet promises decompose still works from {stage}; it does not"


def _crashed_agent_bullet(text: str) -> str:
    """The «Пер-таск-агент УПАЛ» bullet — the pump's whole restart rule, both roles.

    Sliced, and the slice is the ONLY form that can carry VMCP-118 (591). The sentence this pin
    is about — «ветка предложения ревью … пропускает карточки, назначенные на тебя» — ALREADY
    occurs verbatim in the drain tick's step 3, where it explains a DIFFERENT situation (an
    orchestrator that cannot verify an evidence sha). So a whole-file substring cannot tell
    "the restart rule now covers a dead reviewer" from "step 3 still explains the same mechanism
    for its own purpose", which was exactly the gap: the restart rule promised a mechanism
    (`next_task` hands the task back) that exists for build and NOT for review."""
    start = text.find("- **The per-task agent CRASHED (a runtime/API error)")
    assert start != -1, "SKILL.md no longer tells the pump to restart a crashed per-task agent"
    end = text.find("\n- **", start + 1)
    assert end != -1, "the crashed-agent bullet no longer ends where the next top-level bullet does"
    bullet = text[start:end]
    assert 0 < len(bullet) < len(text), "the crashed-agent slice is not a proper subset of SKILL.md"
    assert "The per-task agent runs the WHOLE task itself" not in bullet, \
        "the slice swallowed the following bullet"
    return bullet


def _independent_review_section(text: str) -> str:
    """The «Независимое ревью изменений» section — the reviewer's OWN rubric, and the one place
    in this file that is addressed to the reviewer and nobody else.

    Sliced for the same measured reason as `_gc_section` / `_reviewer_tree_rule`: every token the
    pins below name already lives elsewhere in this file, in BUILD-side prose — `git rev-parse
    HEAD` is in the integration recipe, `git show <sha из evidence>` is in «Два возврата, два
    дерева», and `[review]` appears throughout. A whole-file substring would stay green with the
    reviewer's own rule deleted."""
    start = text.find("\n## Independent review of changes")
    assert start != -1, "SKILL.md no longer has a section on independent review"
    end = text.find("\n## ", start + 1)
    assert end != -1, "the independent-review section no longer ends where the next section begins"
    section = text[start:end]
    assert 0 < len(section) < len(text), "the review-section slice is not a proper subset"
    # The guard names the following section's HEADING, not its NAME. It used to match the bare
    # name, which made it fire on any prose that merely CROSS-REFERENCES that section — and #628
    # added exactly such a reference inside this section («см. «Застрял? Выход зависит от РОЛИ»»),
    # turning a slice guard into a ban on pointing at a neighbour. A cross-reference by name is
    # legitimate rulebook prose; a swallowed HEADING is the thing that would break the slice.
    assert "\n## Stuck?" not in section, "the slice swallowed the following section"
    return section


def test_the_restart_rule_covers_a_dead_reviewer_and_the_double_dispatch_it_costs():
    """VMCP-118 (591), INVERTED BY #991 — and the inversion is why this test kept its slice and
    its code anchor while both assertions changed. 591's finding was that the pump's restart rule
    rests on «снова зовёт `next_task` (задача всё ещё за ним)», a premise TRUE for a build agent
    and FALSE for a reviewer, because the review-offer branch skipped cards assigned to the caller
    and in a solo setup every card in Review is the caller's. The rulebook therefore had to say
    that a dead reviewer is never handed back.

    #991 made that skip conditional on `require_review_independence` (default false), so the
    premise now holds for BOTH roles: a card with no verdict is re-offered on every tick, since
    only a verdict removes it. The rulebook must no longer say the mechanism is missing.

    WHAT REPLACES THE OLD PIN IS THE NEW COST, not nothing. The same re-offer that resurrects a
    dead reviewer also hands the SAME card out twice WITHIN a tick, so the pump can dispatch two
    reviewers onto one piece of work. That is what `exclude` now prevents and what the rulebook
    now has to say — under the old behaviour `exclude` was inert here, and the rulebook said so
    in as many words. A translation of this bullet that keeps the reassuring half («механизм
    есть») and drops the obligation would leave the pump double-dispatching quietly.

    MEASURED before the prose was written (real `Workflow` over FakeAPI, solo setup — the dogfood
    one), and DESCRIBED rather than transcribed, because none of the three is a payload any call
    returns: a card driven to Review and still assigned to me makes `next_task()` answer with
    `task` at None and the queue-is-empty message, on every call; the same identity with a card in
    Build gets a resume result — `resume` true, `stage` "Build" — whose `task` is the `_summary`
    DICT (`{id, ref, title, priority, description}`), never the bare id; a SECOND token sees the
    Review card as a review offer, `review` true beside `review_kind`. So the blindness is a
    property of the solo setup, which is the setup this rulebook is written for. Every one of the
    three also carries `wip`, and the two task-bearing ones a `note` — the elision that made the
    first spelling of this paragraph read as a transcript, VMCP-170 (694). Read the payloads off
    the code or off a run, never off this docstring.

    Anchored in the branch that causes it, and the anchor is now the CONDITION rather than the
    skip: make the review-offer skip unconditional again and the re-offer disappears, so both
    halves of the new bullet — the mechanism AND the double-dispatch it costs — go false at once.
    The resume path that hands build work back must still exist, since the bullet still draws the
    two roles as parallel rather than opposite.

    MUTATION SWEEP, one selection throughout (this file + test_review_independence.py +
    test_claimable_cmd.py + test_workflow_gates.py), `__pycache__` cleared and
    PYTHONDONTWRITEBYTECODE=1 each round, `-q` dropped so `collected` is printed, and each round
    read by COUNTING lines beginning `FAILED ` and `ERROR ` separately rather than by the first
    `N failed` in stdout: control (opening) 0 failed, 0 errors, collected 223; drop the
    `require_review_independence` conjunct from the offer skip, back to the pre-#991
    unconditional form -> 6 failed, 0 errors, collected 223; delete the `exclude` obligation
    sentence from this bullet -> 1 failed, 0 errors, collected 223; delete the «механизм ЕСТЬ»
    claim -> 1 failed, 0 errors, collected 223; control (closing, restored) 0 failed, 0 errors,
    collected 223. Collected is equal in every round, so each number is a delta against the
    control and not a different selection. Re-wrapping the paragraph is a PASS by construction
    rather than a measured round — `_flat` normalises the wrapping before any of these match."""
    flat = _flat(_crashed_agent_bullet(_skill_text()))
    assert "A REVIEWER crashed" in flat, \
        "the restart rule no longer says anything about a reviewer that died"
    assert "since #991 the mechanism EXISTS" in flat, \
        "the restart rule no longer says a dead reviewer IS handed back — #991 made it true, " \
        "and a rulebook still claiming otherwise sends the pump chasing a phantom"
    assert "put the id of a dispatched review into `exclude`" in flat, \
        "the restart rule no longer names the cost the re-offer brings: without exclude the " \
        "same card is offered twice inside one tick and two reviewers land on one piece of " \
        "work. A bare `exclude` substring is NOT enough here — the word occurs twice in this " \
        "bullet, so matching it would stay green with the obligation itself deleted"

    src = inspect.getsource(workflow.Workflow.next_task)
    review_at = src.index('for t in sorted(board.get("Review", [])')
    assert "self.require_review_independence and my_id in self._assignee_ids(t)" \
        in src[review_at:], \
        "the review-offer branch skips cards assigned to the caller UNCONDITIONALLY again — " \
        "SKILL.md tells the pump a dead reviewer IS reminded, which would become false"
    assert "_my_active_tasks" in src, \
        "next_task no longer has the resume path for active work — the build half of the " \
        "parallel the restart rule draws would be gone"


def test_the_reviewer_is_told_to_establish_it_is_looking_at_the_reviewed_code():
    """VMCP-118 (591), three catalogue entries closed by one rule: «проверяй, а не предполагай»
    was never said to the reviewer; «без пути в брифе работай там, где стоишь» is harmless for a
    build agent and means "review the main branch" for a reviewer; and the reviewer's isolation at
    `wip.limit: 1` was not described at all, though background review runs at ANY limit.

    MEASURED on this code (throwaway repo, real git): `ensure_workspace(id, role="review",
    at=None)` against an EXISTING tree pinned at a stale sha returns `{"created": false, "head":
    <the OLD sha>}` with no refusal of any kind, while the same call WITH `--at <new sha>` raises
    («review tree for task N is pinned at X but --at asked for Y»). So the loud guard is the one
    the caller can forget to ask for, and the reviewer's own `git rev-parse HEAD` is the check
    that does not depend on the pump getting its flags right.

    Anchored in the two facts the prose asserts about the tool: the pinned-at check really is
    conditional on `--at`, and creating a review tree really does read neither the board nor the
    limit (only `--gc` builds a Workflow) — which is what makes "нужно при ЛЮБОМ `wip.limit`" a
    statement about the code rather than a preference.

    MUTATION-CHECKED: delete the added bullet while `git rev-parse HEAD` and `git show <sha из
    evidence>` stay in their build-side homes -> FAIL (whole-file substrings on BOTH stay GREEN on
    that mutant); drop the round-2 `[review]` clause from the dossier list -> FAIL; make the
    pinned-at check unconditional -> FAIL; re-wrap -> PASS by design."""
    flat = _flat(_independent_review_section(_skill_text()))
    assert "your own tree at ANY `wip.limit`, not only in a parallel drain" in flat, \
        "the reviewer is no longer told its own worktree is not a parallel-drain-only affair"
    assert "git rev-parse HEAD" in flat, \
        "the reviewer is no longer told to verify its tree holds the sha under review"
    assert "git show <sha from evidence>" in flat, \
        "the reviewer with no tree is no longer given the fallback that reads the RIGHT code"
    assert "the previous `[review]`" in flat, \
        "the round-2 reviewer is no longer told to read the previous verdict"
    # the placement residue this card rules on: the reviewer's tree rules are WRITTEN, but they
    # live inside a section headed `wip.limit > 1`, so at limit 1 nothing routes the reviewer to
    # them. Fixed by POINTING from the rubric (always read) rather than by moving the text.
    assert "Having cast a verdict, the reviewer releases its own tree" in flat, \
        "the rubric no longer points at the bullet holding the rest of the reviewer's tree " \
        "rules — at wip.limit 1 the reviewer never reaches that section on its own"
    assert "Parallel drain (when" in flat, \
        "the rubric no longer warns that the pointed-at bullet sits behind a wip.limit > 1 " \
        "heading that does not apply to the reviewer"
    # ...and the pointer must keep resolving: a renamed target would leave a dangling reference
    assert "- **Having cast a verdict, the reviewer releases its own tree:**" in _skill_text(), \
        "the bullet the rubric points at no longer exists under that name"

    ensure_src = inspect.getsource(workspace_cmd._ensure_locked)
    assert "if at is not None and" in ensure_src, \
        "the pinned-at guard is no longer conditional on --at — SKILL.md tells the reviewer a " \
        "tree can come back stale in SILENCE, which is only true while this check can be skipped"
    assert '"created": False,' in ensure_src, \
        "an existing worktree is no longer handed back as created: false — the stale-tree hazard " \
        "the rule is about would not arise"
    assert "wip_limit" not in ensure_src and "_build_workflow" not in ensure_src, \
        "creating a workspace now reads the board/limit — 'нужно при ЛЮБОМ wip.limit' rests on " \
        "this path needing neither"
    assert "_build_workflow" in inspect.getsource(workspace_cmd.gc_workspaces), \
        "--gc no longer builds the Workflow — the read/no-read split the rule cites is gone"


def test_the_reviewers_release_rule_carries_the_refusal_its_own_cure_cannot_answer(git_repo):
    """VMCP-118 (591), the ONE entry the catalogue had confirmed by running it: the `dirty` guard
    in `_release_locked` is ROLE-AGNOSTIC, but its cure («доведи до пуша и повтори») is written on
    the build side and is FORBIDDEN to a reviewer — its tree is detached, has no branch, and a
    commit inside it is `unreachable-head` forever. So the role got the refusal and a recipe it
    may not run.

    MEASURED (throwaway repo, real git): a review tree holding ONE untracked file ->
    `{"released": false, "code": "dirty", "reason": "working tree is dirty (1 entries)"}`; the same
    tree after deleting that file -> `{"released": true}`. And the cost of not clearing it is
    measured here rather than argued: `_keep_is_expected` grades that refusal `kept` — the list a
    human is told to read in full — on every tick. Unconditionally, since VMCP-91: this test used
    to carry "unless the card happens to be parked" and a CONTROL that asserted it, which is how a
    pin came to hold the defect 547 was filed against. The parked card excuses the BUILD agent's
    unsaved work; the control below now says so with a build tree.

    Anchored BEHAVIOURALLY rather than by reading the guard: the claim "роли НЕ РАЗЛИЧАЕТ" is
    about what a REVIEW tree does when it holds a stray file, so this builds exactly that state
    and runs `release_workspace` on it — then removes the file and runs it again, so the cure the
    prose prescribes is measured to work rather than asserted to exist. An index or substring pin
    over `_release_locked` would go green for `if dirty and role == "build":`, which is precisely
    the mutation that would make this paragraph false.

    MUTATION-CHECKED: delete the added paragraph while the build side keeps its full `dirty`
    breakdown -> FAIL (a whole-file substring on `dirty` stays GREEN); make the dirty guard
    role-conditional -> FAIL; add CODE_DIRTY to `_EXPECTED_IN_A_REVIEW_TREE` -> FAIL; re-wrap ->
    PASS by design."""
    flat = _flat(_reviewer_tree_rule(_skill_text()))
    assert "`dirty` does NOT TELL the roles apart" in flat, \
        "the reviewer is no longer told the dirty refusal is aimed at it too"
    assert "take the file out of the tree" in flat, \
        "the reviewer is no longer given the ONE cure it is allowed to run"

    # the state the prose is about: a REVIEW tree the reviewer left one file in
    tree = Path(workspace_cmd.ensure_workspace(7, role="review", cwd=git_repo)["path"])
    stray = tree / "reviewer-scratch.md"
    stray.write_text("probe\n")
    refused = workspace_cmd.release_workspace(7, role="review", cwd=git_repo)
    assert refused["released"] is False and refused["code"] == workspace_cmd.CODE_DIRTY, \
        f"a review tree holding a stray file no longer refuses release as dirty: {refused} — " \
        f"SKILL.md tells the reviewer this refusal is aimed at it too"
    assert tree.is_dir(), "the refusal removed the directory anyway"

    # ...and the ONE cure the reviewer is allowed to run really does clear it
    stray.unlink()
    assert workspace_cmd.release_workspace(7, role="review", cwd=git_repo)["released"] is True, \
        "removing the stray file no longer releases the tree — the rulebook prescribes exactly " \
        "that as the reviewer's only available cure"

    entry = {"code": workspace_cmd.CODE_DIRTY, "role": "review", "task_id": 7}
    assert not workspace_cmd._keep_is_expected(entry, set()), \
        "a dirty review tree is now graded `expected` — SKILL.md tells the reviewer an uncleared " \
        "file shouts at a human every tick, which is the reason the cure matters"
    assert not workspace_cmd._keep_is_expected(entry, {7}), \
        "a dirty REVIEW tree is graded `expected` again because a card sharing its task id sits " \
        "in Your Call — VMCP-91 removed exactly that laundering; the parked card excuses the " \
        "BUILD agent's unsaved work, not a reviewer's"
    assert workspace_cmd._keep_is_expected(
        {"code": workspace_cmd.CODE_DIRTY, "role": "build", "task_id": 7}, {7}), \
        "control: a PARKED card's BUILD tree must still be `expected`, else the assertions above " \
        "would pass merely because nothing is ever graded routine"


def test_the_skill_verification_trap_is_addressed_to_the_reviewer_as_well():
    """VMCP-118 (591): the trap «правку этого файла нельзя проверять вызовом скилла» was written
    in build lexicon — it ended «в `[worklog]` пиши, чем именно проверял», a marker only the
    implementer posts. The role that actually walks into it is the REVIEWER, whose own rubric
    orders it to verify BY RUNNING, and whose only available "run" for a rules change is the skill
    call that returns the frozen snapshot. Same rule, one word wider.

    Pinned inside the freshness section for `_freshness_section`'s own recorded reason, and
    MUTATION-CHECKED by putting the build-only wording back while `[review]` stays everywhere else
    in the file -> FAIL."""
    flat = _flat(_freshness_section(_skill_text()))
    assert "nor by the REVIEWER" in flat, \
        "the snapshot trap no longer names the role that most often walks into it"
    assert "`[review]` for the reviewer" in flat, \
        "the trap still tells only the implementer where to record what it checked"


def test_the_container_name_recipe_says_the_reviewers_id_is_not_its_own():
    """VMCP-118 (591): the shared-resource recipe derives a container NAME from the task id
    (`NAME=vikunja-test-$ID`) — and for a REVIEWER that id belongs to somebody else's card, so it
    collides with the container of that card's own build agent, which «Чек-пойнть рано» expressly
    allows to still be working after `advance`.

    Dropped from this card's plan at first, on the ground that the collision is LOUD (docker exits
    125, and the rulebook quotes both refusals verbatim). Independent adjudication measured that
    the loudness does not bound the cost, and the measurement stands: the name refusal reads «You
    have to remove (or rename) that container», the recipe's own cleanup line is `docker rm -f
    "$NAME"  # ОБЯЗАТЕЛЬНО`, and `docker rm -f` against a sibling's RUNNING container exits 0 with
    no warning. So the loud error routes an obedient reader straight into destroying a sibling's
    work. The neighbouring-value escape hatch does not cover it either: «возьми соседний» occurs
    once, on the `lsof` PORT line, and a name has no lsof.

    Kept word-level on purpose (this card's whole ruling is that attention is the scarce
    resource): the fix is a comment on the existing `ID=` line, not a new bullet.

    MUTATION-CHECKED: delete the added comment while the section keeps `vikunja-test-$ID`, the
    quoted docker refusals and the mandatory `docker rm -f` -> FAIL."""
    section = _shared_resources_section(_skill_text())
    flat = _flat(section)
    assert "REVIEWER: it is SOMEBODY # ELSE'S" in flat, \
        "the id-derived naming recipe no longer warns the reviewer that the id is not its own"
    assert 'docker\'s "delete it and retry" kills ITS work' in flat, \
        "the recipe no longer names the destructive outcome the loud refusal routes an obedient " \
        "reader towards"
    # controls: the collision the warning is about must still be constructible from this recipe
    assert "NAME=vikunja-test-$ID" in section, \
        "the recipe no longer derives the container name from the task id — the hazard the " \
        "warning describes would not exist"
    assert 'docker rm -f "$NAME"' in section, \
        "the recipe no longer prescribes the removal that makes the collision destructive"


def _second_pass_section(text: str) -> str:
    """The «Второй независимый проход по СВОЕМУ тексту» section — the prose rule, and the only
    place that says how a finding arriving AFTER the verdict is recorded.

    Sliced like `_stuck_section` / `_independent_review_section`, and here the slicing is MEASURED
    rather than stylistic: `[review] APPROVE` occurs a SECOND time in this file, in the reviewer's
    rubric («человек увидит `[review] APPROVE` и примет решение о Done»), so a whole-file
    substring cannot tell "the discriminator is still taught here" from "the words survive in the
    rubric". The heading is anchored WITH its `## ` prefix because the section's title is also
    cited from two other places (the implementer's `advance(to='review')` bullet and the reviewer's
    `review_kind` rubric) — a bare title match would land on one of those pointers instead."""
    start = text.find("\n## A second independent pass over YOUR OWN text\n")
    assert start != -1, \
        "SKILL.md no longer has the section on a second independent pass over one's own prose"
    end = text.find("\n## ", start + 1)
    assert end != -1, "the second-pass section no longer ends where the next section begins"
    section = text[start:end]
    assert 0 < len(section) < len(text), "the second-pass slice is not a proper subset of SKILL.md"
    # The guard names the following section's HEADING, not its NAME — the same correction #628
    # already made to `_independent_review_section`, arrived at here by the same input. Matching
    # the bare name fired on prose that merely CROSS-REFERENCES that section, and #902 added
    # exactly such a reference (the filing threshold's pointer, which cites the section by the
    # nominative title every other cross-reference in this file uses). It does NOT weaken the
    # swallow check: a slice that really ran past the boundary necessarily contains the heading
    # LINE, so anchoring on `\n## ` still catches it, and stops catching legitimate citations.
    assert "\n## Decomposition, review and dead ends" not in section, \
        "the slice swallowed the following section"
    return section


def test_the_post_verdict_note_rides_on_a_comment_tool_with_no_stage_or_ownership_gate():
    """#618: the second-pass rule ends by telling an agent NOT to hold a late finding for a second
    `review_task` — fix the verdict as soon as you are sure, then append the finding with a plain
    `comment`, because «гейтов по стадии и владению у него нет, он работает и из Review, и после
    вердикта». That clause is a claim about CODE, and it is the half the whole instruction rests
    on: grow a stage or an ownership gate on `Workflow.comment` and the rulebook keeps teaching a
    flow that now raises — to every consumer, since SKILL.md self-heals onto them at server start
    with no per-consumer pin and no review gate (see this module's docstring).

    Today `comment` checks exactly two things — the text is not blank, and the task is on this
    project's board — and neither is a stage or an owner. So the pin is BEHAVIOURAL: it builds the
    state the rule is actually about (a card sitting in Review, assigned to somebody ELSE, whose
    verdict is already recorded) and appends the note through the real Workflow. A substring pin
    over `comment`'s source could not carry this — an added gate is new code, not a missing token,
    so the assertion would stay green through the very drift it claims to catch.

    The prose half is deliberately thin (short substrings, read from the flattened section): the
    wording of this section is still being polished, and pinning sentences is review's job — this
    module only holds the section open and checks it still promises the property.

    MUTATION-CHECKED (`__pycache__` cleared between rounds, selection confirmed at exactly 1 test):
    control PASS; add a stage gate to `Workflow.comment` (raise when the task is in Review) ->
    FAIL; delete the clause from SKILL.md -> FAIL; rename the heading -> FAIL loudly, with its own
    message. Re-wrapping the paragraph -> PASS by design (`_flat`)."""
    flat = _flat(_second_pass_section(_skill_text()))
    assert "`comment`" in flat, \
        "the second-pass rule no longer names the tool a post-verdict finding is appended with"
    assert "no stage or ownership gates" in flat, \
        "the rule no longer says the comment tool is free of stage/ownership gates — the reason " \
        "it can be used at all once the verdict is in"
    assert "works from Review and after the verdict alike" in flat, \
        "the rule no longer says the note may be written AFTER the verdict is recorded"

    # the state the rule is about: someone ELSE's card, in Review, already judged
    api = FakeAPI(buckets=workflow.STAGES)
    wf = workflow.Workflow(api, project_id=3)
    implementer = {"id": 99, "username": "agent-implementer"}
    assert implementer["id"] != api.me_user["id"], "control: the card must not be the reviewer's"
    card = api.add_task("prose deliverable, reviewed", "Review", assignee=implementer)
    wf.review_task(card["id"], verdict="approve", report="прогнал tests/unit -q, зелено")

    note = "[review] post-verdict: второй проход вернулся, одна находка — атрибуция"
    assert wf.comment(card["id"], note) == {"commented": card["id"]}, \
        "SKILL.md tells an agent to append a post-verdict finding with `comment`; it no longer " \
        "accepts a card in Review that belongs to someone else"
    assert api.comments_text(card["id"])[-1] == note, \
        "the post-verdict note did not reach the card's comment stream verbatim"
    assert api.stage_of(card["id"]) == "Review", "appending a note moved the card"

    # ...and the second `review_task` the rule says is unnecessary is genuinely not what happened
    assert sum(c.startswith("[review] APPROVE") for c in api.comments_text(card["id"])) == 1, \
        "control: the note must be an extra comment, not a second verdict"


def test_only_the_review_tool_writes_a_comment_that_opens_with_its_verdict_line():
    """#618: the same rule tells an agent HOW to tell a tool verdict from a note appended by hand.
    A verdict written by the tool always opens with its own line — `[review] APPROVE` or `[review]
    NEEDS WORK`, `первой строкой` — and the post-verdict notes the rule points at have no such
    line, which is how a reader knows they were appended with `comment` instead. That is the
    reader's only discriminator, and it is grounded entirely in two f-strings inside `review_task`.
    Reword either one and every agent (and every human reading the journal) keeps applying a test
    that no longer separates anything, silently — a hand-written note and a recorded verdict would
    look alike.

    Deliberately a PARAPHRASE, and the claim it makes is narrow: the only spans above quoted FROM
    SKILL.md are the three the assertions below pin (`[review] APPROVE`, `[review] NEEDS WORK`,
    `первой строкой`) — the remaining backticked tokens are this codebase's own identifiers and a
    shell command, not citations. VMCP-148 (646)'s ruling, and not a style choice: this paragraph
    opened with a «…» citation of a phrase SKILL.md does not contain (`grep -c` = 0), which sent
    the next reader hunting for text that was not there. Its history is worth stating because git
    cannot tell it — 618's second pass flagged the draft wording, the implementer reworded
    SKILL.md BEFORE committing, and only this docstring's copy of the pre-edit phrasing landed, so
    `git log -S` finds that phrase in this file and in SKILL.md at NO commit. Re-pinning the quote
    to today's wording would only restart the same clock.

    What the paraphrase buys, and what it does not: the three quoted spans are read by an
    assertion, so THOSE cannot go stale quietly. The prose around them is NOT pinned — reword the
    rule's clause about post-verdict notes into its own opposite while leaving those three tokens
    standing, and this test stays green (measured on this card). So a re-wrap or a meaning-
    preserving re-wording will not break this docstring, and a meaning-CHANGING one will not flag
    it either: re-read this paragraph against SKILL.md whenever that section moves. Do not
    "helpfully" restore a citation here — the citation is the part that rotted last time.

    Pinned on the comment the tool actually WRITES, not on the source that formats it: the claim is
    about the FIRST LINE a reader sees, which is a property of the stored comment after the
    text->HTML->text round trip every agent comment makes (#85), so it is read back through
    `comments_text` exactly as an agent would. Both verdicts are driven, because they are separate
    f-strings and a mutation of one is invisible in the other.

    The other half of the discriminator is that `comment` writes the agent's text through
    UNCHANGED — it prepends no marker of its own — so a note carrying the `[review]` marker in its
    body still does not open with a verdict line. That is asserted here too: without it "the tool
    prints X first" would be a fact about one tool rather than a test a reader can apply.

    MUTATION-CHECKED (`__pycache__` cleared between rounds, selection confirmed at exactly 1 test):
    control PASS; re-spell the approve line `[review] approved` -> FAIL; re-spell the needs_work
    line `[review] NEEDS-WORK` -> FAIL (each verdict is its own f-string, and the two rounds are
    what proves neither is covering for the other); delete the clause from SKILL.md -> FAIL, with
    the reviewer's rubric still citing `[review] APPROVE`, so a whole-file substring on THAT token
    was measured GREEN on the same mutant — which is what `_second_pass_section` slices for (the
    `первой строкой` half would have gone red either way); rename the heading -> FAIL loudly."""
    section = _second_pass_section(_skill_text())
    flat = _flat(section)
    assert "`[review] APPROVE`" in flat and "`[review] NEEDS WORK`" in flat, \
        "the rule no longer quotes the two verdict lines a reader tells the tool's comment by"
    assert "the tool's verdict ALWAYS comes on the first line" in flat, \
        "the rule no longer says the verdict line is the FIRST line — the discriminator is gone"

    api = FakeAPI(buckets=workflow.STAGES)
    wf = workflow.Workflow(api, project_id=3)
    implementer = {"id": 99, "username": "agent-implementer"}

    approved = api.add_task("verdict: approve", "Review", assignee=implementer)
    wf.review_task(approved["id"], verdict="approve", report="перепрогнал замеры, сходится")
    bounced = api.add_task("verdict: needs_work", "Review", assignee=implementer)
    wf.review_task(bounced["id"], verdict="needs_work", report="утверждение шире своего замера")

    assert api.comments_text(approved["id"])[-1].splitlines()[0] == "[review] APPROVE", \
        "the approve verdict no longer opens with the line SKILL.md tells readers to look for"
    assert api.comments_text(bounced["id"])[-1].splitlines()[0] == "[review] NEEDS WORK", \
        "the needs_work verdict no longer opens with the line SKILL.md tells readers to look for"

    # ...and a hand-written note, marker and all, is still distinguishable from both
    note = "[review] post-verdict: находка приехала после вердикта, решения не меняет"
    wf.comment(approved["id"], note)
    written = api.comments_text(approved["id"])[-1]
    assert written.splitlines()[0] == note, \
        "`comment` no longer writes the agent's first line through unchanged — SKILL.md's " \
        "discriminator assumes a hand-written note opens with whatever the agent typed"
    assert not written.startswith(("[review] APPROVE", "[review] NEEDS WORK")), \
        "a hand-written note now opens with a verdict line — the rulebook's way of telling a " \
        "post-verdict note from a recorded verdict no longer separates them"


def _second_pass_clone_recipe(text: str) -> str:
    """The FENCED recipe that stands the second pass up in its OWN clone.

    Scoped to the fence and matched RAW for `_integration_recipe`'s measured reason: inside a fence
    a line break separates two COMMANDS, so flattening would let a pin match text that is no longer
    a runnable step. Exactly one such fence must exist; two would mean the recipe was duplicated,
    which is drift rather than a state to tolerate."""
    blocks = [b for b in re.findall(r"```sh\n(.*?)```", text, re.S) if "--no-hardlinks" in b]
    assert len(blocks) == 1, f"expected exactly 1 fenced second-pass clone recipe, got {len(blocks)}"
    recipe = blocks[0]
    assert 0 < len(recipe) < len(text), "the clone-recipe slice is not a proper subset of SKILL.md"
    return recipe


def _second_pass_prose(section: str) -> str:
    """The second-pass section with every FENCED block removed, flattened for prose pinning.

    Separated from `_flat(section)` because measured, on this card's own round-2 review: an assert
    reading the flattened WHOLE section is satisfied by the fence, so a claim whose message says
    "the rule no longer PAIRS the bytecode variable with deleting the caches first" was in fact
    held by the two fence lines `find … __pycache__ …` and `export PYTHONDONTWRITEBYTECODE=1`.
    Deleting the entire measured prose sub-bullet — the `.pyc`-header measurement, the ordering,
    «Делай оба, в этом порядке» — left the suite GREEN (`control 0 failed; mutation 0 failed`).
    The same held for the `vikunja_mcp.__file__` sub-bullet. A fenced command and the prose that
    explains WHY it is there are different deliverables: the fence is pinned by
    `_second_pass_clone_recipe`, the explanation by this slice, and neither stands in for the
    other."""
    prose = re.sub(r"```sh\n.*?```", " ", section, flags=re.S)
    assert "```" not in prose, "the fence-stripping left a fence marker behind"
    assert 0 < len(prose) < len(section), "the prose slice is not a proper subset of the section"
    return _flat(prose)


def test_the_second_pass_runs_in_its_own_clone_and_the_recipe_carries_the_working_tree():
    """VMCP-177 (702): the second pass is MANDATORY when prose is the deliverable, and re-measuring
    a claim of the form "X is what catches Y" means DELETING X and requiring the pin to go red — so
    the auditor writes to the very sources its author is sweeping at the same moment. Until this
    card the rulebook said nothing about WHERE it runs, and the only path an author has to hand is
    its own worktree, so the natural dispatch put two WRITERS in one directory.

    The collision is not hypothetical and it is not symmetric, which is why the rule names both
    directions. Constructed on this card (two processes, one tree, each mutating SKILL.md behind
    its own one-test pin), against a solo baseline of `control 0 failed` / `mutation 1 failed` for
    each writer: the author's control round with the auditor's mutant on disk read `1 failed`,
    naming a clause the author never touched — loud, therefore survivable; the auditor's mutation
    round with the author's restore landing under it read `0 failed`, which the auditor records as
    "this pin is blind to that mutation" — the exact false conclusion the audit exists to prevent,
    and INDISTINGUISHABLE from an honest green. Both scripts' own sha256 restore checks reported
    success in both directions and `git status` stayed clean throughout, because a per-script guard
    sees only its OWN writes. Split across two clones, the same two scenarios returned the solo
    numbers on both sides.

    WHY THIS TEST DRIVES GIT rather than only reading prose. The recipe's commands make claims a
    reader ACTS on: `git clone` copies the REPOSITORY, so a dirty tree's uncommitted work — which
    is exactly the text under audit — is absent from the clone, and it takes TWO steps to rebuild,
    each measured in the next test. Forget them and the auditor still finishes, and reports "the
    rule you describe is not in the file": true for what it saw, false in fact, and the same defect
    class the pass exists to catch. A substring pin over the fence cannot notice that behaviour
    changing, so it is measured on a throwaway repo (one file, no origin, no network) the same way
    the ancestry commands above were.

    The prose half stays thin — short substrings over the flattened section — because pinning
    sentences is review's job; this only holds the rule open and checks it still promises the
    property. The `.venv` half deliberately pins `cp -R` being NAMED as wrong rather than the
    mechanism: measured on this card, whether it bites depends on the RUNNER (a bare
    `<copy>/.venv/bin/python` reads the copied editable `.pth`, whose path is absolute, and imports
    the ORIGINAL src — the mutation never reaches the interpreter, VMCP-148 (646)'s four false
    greens; `uv run` in the same copy re-syncs and rewrites it, and the mutation lands), and a pin
    on a runner-dependent mechanism would be pinning today's uv.

    WHAT THIS TEST DOES NOT PIN, so nobody reads it as more than it is. `_step` is a
    LINE-START-ANCHORED PREFIX match that reads nothing past its own fragment, so what IT pins is
    the opening TEXT of seven fence lines and, across those seven, a PARTIAL order of SEVEN pairs
    — never that a step RUNS, never anything to the right of a prefix, and not the other TEN
    lines at all (17 non-empty, 7 pinned; counted, not estimated). Pairs and not "their ORDER":
    that phrasing is the third draft's, retracted below. The seven say three things — nothing
    writes into the clone before the clone exists (`clone` above `apply` and above the copy loop),
    nothing consumes a file before it is produced (`diff` above `apply`, `ls-files` above the copy
    loop), and each of the FIVE author-side steps it pins stays above the auditor's boundary
    marker, which in turn sits above the seventh and last, `uv sync` — the auditor's own first
    command, and the one pinned step that belongs BELOW the boundary (asserted for `apply` and for
    the copy loop; `clone`, `diff` and `ls-files` reach it by transitivity, measured rather than
    deduced — push any one of them alone below the marker -> 1 failed each). Author-side lines it
    does NOT pin are not held at all: the fence's two verification lines slide below the marker at
    0 failed. PARTIAL is the operative word, and free does not mean harmless — every round in
    this paragraph run with control 0 failed at both ends, `collected 1 item` throughout.
    Free and harmless, each round green and each one BUILT: swap `clone` with
    `diff`, lift the `ls-files` line above the clone, slide the patch pair below the whole
    untracked block -> 0 failed each, and on a throwaway repo all three still hand over a clone
    carrying the tracked edit AND the untracked module. Free and HARMFUL — four built so far, and
    that is a list rather than a census: the untracked block slid down to just above the marker,
    which lands it below those two verification lines, whose `diff` then reads a list that does
    not exist yet (exit 2, module never copied); the loop's one working line, `mkdir … && cp …`,
    deleted while `while`/`done` stay, which sh and bash refuse as a syntax error at exit 2 while
    ZSH — what this repo's agents run — accepts the empty loop and copies nothing (exit 1, the
    verification naming the lost file); either ASSIGNMENT displaced below the step that reads it —
    `TREE=`/`CLONE=` under the clone line (built: exit 128) or `P=` under the `diff` line (exit 1,
    and the clone keeps the UNEDITED file, which a verification over untracked NAMES cannot see);
    and `cd "$CLONE" && uv sync` slid below the `__file__` print, its only pin being
    marker < sync. Three of those four are the second pass's, not the author's, and the last is
    RUNNER-dependent in a way that is the point rather than a caveat: run from inside the clone —
    the working directory the brief hands the auditor — the print still names the clone and
    nothing is wrong; run from anywhere else, as the AUTHOR does when checking the whole fence,
    and `uv` walks up to the ORIGINAL project and the print names the wrong `src` at exit 0. None
    of the four is pinned, deliberately, and they do NOT report alike: three halt the chain with a
    non-zero exit, while that one is exit 0 and speaks only as a PATH somebody has to COMPARE with
    their own — which is what the per-round `__file__` print is there for.

    Two things that list is NOT, both learned by measuring it line by line, control 0 failed at
    both ends. It is not "any pinned line moved above the assignments": lifting `clone`, `diff` or
    `ls-files` to the top of the fence -> 0 failed, but lifting `apply`, the copy loop, the marker
    or `uv sync` -> 1 failed (`assert 367 < 0`, `585 < 0`, `651 < 0`, `992 < 0`). Three of seven,
    not seven — and the marker case is one MUTATION-CHECKED already records as caught, so the
    wider phrasing had this docstring calling one mutation red and green at once. Nor is the list
    a census; naming what a pin does not reach is this paragraph's job, and pinning the fence line
    by line is not. Re-measured this round, control
    0 failed at both ends and `collected 1 item` every round: point the clone at a different repo
    -> 0 failed; redirect the patch to a different file -> 0 failed; delete
    `export PYTHONDONTWRITEBYTECODE=1` from the fence -> 0 failed (its prose sub-bullet holds the
    RULE, never the line); and drop a bare
    NEWLINE immediately after a pinned prefix -> 0 failed on each of the SIX lines that continue
    past theirs — the five commands (`--no-hardlinks`, `diff HEAD --binary`, `… apply`, `ls-files
    --others --exclude-standard`, `while IFS= read -r f; do`) plus the boundary marker, whose line
    continues `, с подставленным $CLONE ---`. Only `cd "$CLONE" && uv sync` is consumed WHOLE by
    its prefix. The right-hand side is not a free-for-all even so: the assertions that read the
    WHOLE fence still reach it — append `&& rm -rf "$CLONE"` to the `uv sync` step -> 1 failed,
    drop the `--no-hardlinks` flag -> 1 failed from the slicer, which then finds no fence to pin
    at all.

    The SHELL is not a backstop for that, and the way it fails is uneven — which is the half worth
    writing down. Built on a throwaway repo rather than derived. Two of the four severed commands
    fail loudly: bare `git clone --no-hardlinks` exits 129 `fatal: You must specify a repository
    to clone.` and no clone appears; bare `git -C … apply` reads STDIN instead of the patch (fed
    there, it applies), so at `/dev/null` it exits 128 `error: No valid patches in input` and the
    tracked half never arrives — a `set -e` chain stops at both. The third fails loudly but
    elsewhere: severed `ls-files` exits 0 and it is the ORPHANED tail the shell refuses (`syntax
    error near unexpected token '|'`, sh 2 / zsh 1), after the earlier lines have already run. The
    fourth is SILENT, and is the reason this bound is worth stating at all: severed `git … diff
    HEAD --binary` exits 0 and prints the diff to STDOUT, the orphaned `> "$P"` leaves an EMPTY
    patch, and the recipe's OWN `[ ! -s "$P" ] ||` guard then skips the apply — the chain runs to
    its end at exit 0 and hands the auditor a clone WITHOUT the text under audit. That is exactly
    the "the rule you describe is not in the file" defect this card exists to prevent, produced
    with every exit code zero, and neither this test nor the shell says a word about it.

    What DOES go red is a break INSIDE a prefix — same selection, same control 0 failed as the
    rounds above; the clone split before `--no-hardlinks`, the guard split after its `||` -> 1
    failed each — by `_step`'s OWN raw match ("the recipe no longer RUNS `…`", `assert None`). One
    measured exception, to the mechanism and not to the result: a break inside `--no-hardlinks`
    ITSELF -> 1 failed from the SLICER (`expected exactly 1 fenced second-pass clone recipe, got
    0`), because that flag is also what SELECTS the fence, so `_step` never runs at all. What is
    never the answer is the ordering assertions below — and they are not "re-ordering only"
    either, since `_step` returns the FIRST line-start occurrence: a pure ADDITION trips them too.
    A second `cd "$CLONE" && uv sync` line at the fence's own two-space indent, inserted directly
    above the `[ ! -s "$P" ] ||` guard with nothing moved, -> 1 failed (`assert 992 < 404`),
    beside the re-ordering they are named for — the clone moved under the apply -> 1 failed
    (`assert 454 < 360`, "the recipe now patches before it clones"). A break creates no
    earlier occurrence while every pinned fragment is unique in the fence, so ordering cannot see
    one today; and where a break does bite, `_step` has already failed above it.

    Four drafts of this paragraph now, which is why it carries its numbers, and the fourth is
    here because the third failed in a direction the first two did not — true about TEXT and
    false about ORDER inside one sentence. `d7eabf8` said "a break inserted inside one of those
    commands turns this red on purpose" — DERIVED, never run, and false for a break AFTER the
    prefix. `f107e81` replaced it with the same break after `apply` going red "not by the raw
    match but by the `_step` ordering assertions", which invents an asymmetry between two calls
    of one function; its mechanism clause is RIGHT for the re-ordering named in the same breath
    and wrong for the break, which is not red at all. `f2eb1be` then wrote "the opening TEXT of
    six fence lines plus their ORDER" while TWO of those six `_step` calls threw their result
    away, so only four indices ever reached a comparison. Measured against that sha, control 0
    failed at both ends: move the `diff` step to the bottom of the fence -> 0 failed; move it
    below the `apply` step that consumes its output -> 0 failed; move `ls-files` to the bottom ->
    0 failed; move it below the `while` loop that reads its list -> 0 failed. Narrowing the
    sentence was the cheap fix and is NOT what happened, because building the second of those on
    a throwaway repo showed it is the SILENT kind — exit 0, the chain printing its last line, and
    a clone without the text under audit. So the two discarded results were named and asserted,
    and the same four rounds re-run against the fix — again control 0 failed at both ends — read
    1 failed each (`assert 1187 < 317`, `411 < 317`, `1181 < 498`, `691 < 498`). Two further
    pairs went in beside them once the remaining free placements were BUILT rather than argued
    about: the copy loop above the clone is exit 128 (`git clone` refuses a destination that
    exists and is not empty), and the copy loop below the marker leaves the author's clone short
    the untracked files while the auditor's own `git -C "$TREE"` degrades rather than errors,
    `-C ""` being a documented no-op. Retracted with that draft as well: its `assert 1003 < 404`,
    which reproduces as `assert 992 < 404` — an operand pair quoted in a paragraph and false at
    the sha that wrote it, which is the shape the marker assertion's own message warns about
    («it said three, the marker sat after five»). All three are quoted from the sha that WROTE
    them
    because `git log -S` settles only some of them: the first sentence WRAPS a line in the blob,
    so a one-line search for it returns nothing — indistinguishable from the control string,
    which returns nothing too — while a search for `f107e81`'s rendering of it lands on
    `f107e81`, never on `d7eabf8` where the claim was actually made, and `plus their ORDER` does
    land on `f2eb1be` — with no count beside it, because quoting the phrase HERE puts a second
    commit into the same search, and a sentence counting its own occurrences is the same defect
    this card removed from SKILL.md. (It was written with the count. The second pass ran the
    search again after the quote had landed, and got two.) Commenting a step out no longer
    escapes — `_step` anchors
    every command to a line start.

    One more thing that pass caught, worth its own note because it is a way to be WRONG WITH
    NUMBERS IN HAND: a stand can lie by being SHORT. The untracked-half orderings were first built
    on a stand that stopped before the recipe's own two verification lines, and reported exit 0
    with the loss unremarked — from which "silent outright" went into an assert message. Re-built
    on the FULL recipe, all four combinations of (`&&` chain | `set -e`) × (stale list | none)
    exit 1, so that half is LOUD and only the patch ordering is silent. The shortened stand was
    not the only axis it got wrong, which is the part worth keeping: WHICH line reports varies by
    shell and by chaining form — `zsh -e` with no list halts at the loop's redirect and never
    reaches the verification, where `sh -e` and `bash -e` run both. Same family as VMCP-148
    (646)'s four false greens: what was measured was not what runs.

    MUTATION-CHECKED (`__pycache__` deleted first, THEN PYTHONDONTWRITEBYTECODE=1 — the variable
    stops Python writing bytecode, not reading a stale `.pyc`; each selection confirmed at exactly
    1 test; `vikunja_mcp.__file__` printed every round and confirmed to point inside the working
    tree under test — a clone for the rounds run from one, this worktree for the rest).
    On this test's selection: control 0 failed; then ONE round per assertion, each deleting
    exactly the text its own message names and nothing wider — the bullet title «ГДЕ он работает»,
    «ШУМНО», «ТИХО», «НЕ закреплена за ролью», «селекцию», the `cp -R` WARNING, «exit 128»,
    «НЕОТСЛЕЖИВАЕМЫЕ файлы», «ЦИРКУЛЯРНА», «Делай оба, в этом порядке», «ТЕМ ЖЕ раннером», and the
    whole prose sub-bullet behind each of them with the FENCE left untouched -> 1 failed every
    time. On the fence: drop the clone line, drop `--binary`, invert the guard to `[ -s "$P" ] &&
    …`, drop the untracked listing, drop `uv sync`, delete the boundary marker, MOVE the marker to
    the top so it divides nothing, move the clone below the apply, move the `diff` step below the
    apply, move the tree `ls-files` step below the `while` loop that reads its list, lift the
    whole untracked block above the clone, drop that block below the marker, drop the loop alone
    below the marker, DELETE the loop outright (0 failed until this round pinned its opening
    line), comment a step out while leaving its text, and re-add a `rm -rf` cleanup line -> 1
    failed each. Re-wrap the prose paragraph -> 0 failed, green BY DESIGN (`_flat`).

    Rounds that were GREEN first are the reason the assertions look the way they do, and they are
    listed rather than counted because a count here would describe the very text it stands in —
    the defect this card removed from SKILL.md. `cp -R`, `__pycache__`, `vikunja_mcp.__file__` and
    `ls-files --others` each occur more than once in this section — one duplicate introduced by
    this very card, as a cross-reference — so deleting the warning, the measurement or the claim
    left a bare token pin satisfied. The marker pin held PRESENCE where its message promised a
    SPLIT. And every command pin survived its step being commented out. Each now pins wording
    unique to the sub-bullet it speaks for, or a position, or a line start. Rounds are per-clause
    rather than per-bullet because deleting a bullet kills every assertion at once and so cannot
    tell which is doing work.

    On the NEXT test's selection, run as its own round with its own baseline: control 0 failed;
    delete the `git apply` STEP from its body -> 1 failed; dirty the tree before it measures the
    "empty" patch -> 1 failed; never create the untracked module -> 1 failed; drop `.venv/` from
    the fixture's `.gitignore` -> 1 failed. Those last three perturb the WORLD rather than delete
    an assertion, deliberately: deleting a case from a test body is vacuously green (measured — 0
    failed both times), so it proves nothing about whether the case was live.

    Two honest bounds on that test, both raised by this card's second pass. Its `.venv` assertion
    is DOMINATED: the mutation that would trip it (dropping `--exclude-standard`) trips the list
    assertion above it first, so it only ever speaks if that one is weakened too — kept as a named
    backstop, not claimed as an independent round. And it copies with `shutil.copyfile` where the
    recipe uses `mkdir -p … && cp` in a `while` loop: what is measured is WHICH FILES git names,
    which is the part the recipe's correctness rests on, not the shell that moves them."""
    section = _second_pass_section(_skill_text())
    prose = _second_pass_prose(section)

    assert "WHERE it works" in prose, \
        "the second-pass rule no longer says WHERE the auditor works — the gap 702 closed is back"
    assert "a foreign MUTANT under your round — LOUD" in prose \
        and "a foreign RESTORE under your round — SILENT" in prose, \
        "the rule no longer names BOTH axes of the collision; the silent one is the reason it " \
        "exists, and a rule that names only the loud one leaves the false green uncovered"
    assert "is NOT tied to a role" in prose, \
        "the rule no longer says the victim is not fixed to a role — both writers restore, so " \
        "the silent axis lands on the AUTHOR too, and those are the numbers that reach the commit"
    assert "NON-OVERLAPPING selections" in prose, \
        "the rule no longer qualifies the loud axis with the selection overlap it depends on — " \
        "measured, with disjoint one-test pins the control round is green and catches nothing"
    assert "`git clone --no-hardlinks`, not `cp -R`" in prose \
        and 'is not "always broken"' in prose, \
        "the rule no longer WARNS against `cp -R` — the copy that drags .venv and can leave the " \
        "mutation never reaching the interpreter. Pinned by the warning's own wording, not by " \
        "the token: this card added a CROSS-REFERENCE to `cp -R` elsewhere in the section, and " \
        "measured, that reference alone kept a bare `\"cp -R\" in prose` green with the entire " \
        "warning deleted"
    assert "Print `vikunja_mcp.__file__` every round" in prose \
        and "with the SAME runner you run the rounds" in prose, \
        "the rule no longer EXPLAINS printing which src it actually imports — the fence line " \
        "alone is a command with no reason attached, and the reason is runner-dependent. The " \
        "runner clause is pinned SEPARATELY because the token alone is also spoken by the " \
        "bullet's own heading: deleting the whole explanation left it satisfied, measured"
    assert "PYTHONDONTWRITEBYTECODE" in prose and "Do both, in that order" in prose, \
        "the rule no longer PAIRS the bytecode variable with deleting the caches first — the " \
        "variable stops Python WRITING bytecode, not READING a stale .pyc. The pairing is " \
        "pinned by the sentence that states it, not by `__pycache__`: that token also appears " \
        "in this section's ignore list and in its `find` bullet, and measured, either kept the " \
        "old conjunct green with the whole measured sub-bullet deleted"
    assert "exit 128" in prose \
        and "The second half is the UNTRACKED files, and the patch does not carry them AT ALL" \
        in prose, \
        "the rule no longer states the two ways the clone comes up SHORT — an empty patch aborts " \
        "`git apply` (exit 128, the reviewer's default case) and untracked files never travel. " \
        "The untracked half is pinned by its CLAIM, not by `ls-files --others`, which the " \
        "circularity bullet below also says: deleting the whole claim left that satisfied"
    assert "That check is CIRCULAR" in prose, \
        "the rule no longer warns that comparing `git diff` on both sides is circular — it " \
        "agrees precisely when an untracked file was lost"

    recipe = _second_pass_clone_recipe(section)

    def _step(fragment: str) -> int:
        """Index of `fragment` where it starts a LINE — i.e. where it is a command the shell
        would run, not text surviving inside a comment. Measured: commenting a whole step out
        (`# [ ! -s "$P" ] || git …`) leaves every plain `in recipe` pin green, which is the
        fictitious-pin shape this card exists to remove."""
        m = re.search(rf"^\s*{re.escape(fragment)}", recipe, re.M)
        assert m, f"the recipe no longer RUNS `{fragment}` — commented out, reworded or deleted"
        return m.start()

    clone_at = _step("git clone --no-hardlinks")
    # --binary is part of the pinned prefix: one staged binary without it and `apply` rejects all
    diff_at = _step('git -C "$TREE" diff HEAD --binary')
    apply_at = _step('[ ! -s "$P" ] || git -C "$CLONE" apply')
    list_at = _step('git -C "$TREE" ls-files --others --exclude-standard')
    copy_at = _step("while IFS= read -r f; do")
    sync_at = _step("cd \"$CLONE\" && uv sync")
    marker_at = _step("# --- the auditor's brief starts here")

    assert clone_at < apply_at, \
        "the recipe now patches before it clones — `git apply` would run against a clone that " \
        "does not exist yet"
    assert diff_at < apply_at, (
        "the recipe now applies the patch before it PRODUCES it — built rather than reasoned, and "
        "the ONLY silent one of the four orderings added here: `$P` does not exist yet, so the "
        "recipe's own `[ ! -s \"$P\" ] ||` guard skips the apply, the diff is written a line too "
        "late, and the chain runs to its end at exit 0 under `set -e` and under the `&&` form "
        "SKILL.md prescribes alike. Nothing downstream notices either, because the recipe's own "
        "verification compares untracked NAMES and a missing TRACKED edit is invisible to it — so "
        "the auditor gets a clone without the text under audit, the defect this card exists to "
        "prevent"
    )
    assert list_at < copy_at, (
        "the recipe now copies the untracked files before it LISTS them, so the loop reads a list "
        "that is missing or STALE, copies nothing, and the clone is short exactly them — a new "
        "test module being the ordinary state of a task here. Unlike the patch ordering above "
        "this one is LOUD, measured on the FULL recipe rather than on a shortened stand: all four "
        "combinations of (`&&` chain | `set -e`) × (stale list | none) exit 1. WHICH line speaks "
        "is not constant, so do not read one mechanism into all four: with a stale list the "
        "recipe's own verification `diff` names the lost file, while with no list the loop's "
        "redirect error names the MISSING LIST instead and, under `&&` and under zsh's `set -e`, "
        "halts before that `diff` runs at all. Asserted anyway, because the verification is "
        "UNPINNED — its two lines slide below the marker at 0 failed — and asserted against the "
        "LOOP rather than the marker, which a stale-list placement clears"
    )
    assert clone_at < copy_at, (
        "the recipe now copies untracked files into the clone before it clones — and `git clone` "
        "refuses a destination that already exists and is not empty, which is what the loop "
        "leaves behind the moment the tree has one untracked file (built: exit 128, and the "
        "tracked half never arrives either). The copy loop belongs below the clone for the same "
        "reason `git apply` does"
    )
    assert copy_at < marker_at, (
        "the untracked-copy loop is now BELOW the boundary marker, i.e. in the auditor's half — "
        "but it reads `$TREE`, and the auditor's brief carries only `$CLONE` substituted, so the "
        "author's clone never receives the untracked files. Loud on the author's side (built: the "
        "verification `diff` names the lost file, exit 1) and not one behaviour on the auditor's: "
        "`git -C \"\"` is a DOCUMENTED no-op rather than an error, so his listing quietly "
        "describes whatever directory he happens to stand in"
    )
    assert apply_at < marker_at < sync_at, (
        "the marker line no longer SPLITS the author's commands from the auditor's — it must sit "
        "below the steps needing $TREE and above the ones the auditor runs in $CLONE. Presence "
        "alone is not the property: measured, moving the marker to the top of the fence (so it "
        "divides nothing) left a `in recipe` pin green. This marker replaced a COUNT of the "
        "author's lines, false on the very sha that wrote it — it said three, the marker sat "
        "after five — so the boundary has to MOVE with the recipe, never be a number describing it"
    )
    assert 'rm -rf "$CLONE"' not in recipe, (
        "the fence deletes the clone again. SKILL.md teaches chaining steps with `&&`, so a "
        "cleanup line inside the fence removes the clone right after the first `__file__` print "
        "— before a single round runs. Cleanup is the AUTHOR's, after handing the card in, and "
        "the rule states it in prose for exactly that reason"
    )


def test_a_clone_does_not_carry_uncommitted_work_and_the_patch_alone_does_not_finish_the_job(
    tmp_path,
):
    """The behavioural half of 702's rule: the git facts its recipe rests on, measured rather than
    asserted in prose. Kept a separate test so a failure says WHICH half moved — the rulebook
    saying the wrong thing, or git doing something else than the rulebook says.

    Round 1 measured only the happy path (dirty tree, one tracked file) and the round-2 review
    disproved BOTH of the claims it had licensed:

    * `git apply` on the EMPTY patch a clean tree produces is not a no-op — it exits 128
      (`error: No valid patches in input`), and SKILL.md teaches chaining with `&&`, so the recipe
      stopped at that step. That is the REVIEWER's default state, not an edge: a reviewer audits a
      landed commit with nothing uncommitted. Round 1's prose called the step "harmless" and no
      test touched an empty patch, so the sentence and the pin agreed with each other and both were
      wrong. The guard also has an inverted form that fails the same way — `[ -s "$P" ] && …`
      itself returns 1 on an empty patch — so the recipe's `[ ! -s "$P" ] || …` is pinned by shape
      in the test above, and the exit code is measured here.
    * `git diff HEAD` never sees UNTRACKED files, so the patch silently leaves them behind — a new
      test module, the routine shape of a task in this repo. Worse, round 1's stated verification
      ("the diffs of tree and clone matched") is CIRCULAR on exactly that axis: untracked files are
      invisible to `git diff` on BOTH sides, so it reports success precisely when the file is lost.
      This test therefore compares what is actually PRESENT in the clone, never two `git diff`s."""
    tree = tmp_path / "tree"
    tree.mkdir()
    _git(tree, "init", "-b", "main")
    _git(tree, "config", "user.email", "t@example.com")
    _git(tree, "config", "user.name", "Tester")
    ruleb = tree / "SKILL.md"
    ruleb.write_text("старый текст\n", encoding="utf-8")
    (tree / ".gitignore").write_text(".venv/\n", encoding="utf-8")
    _git(tree, "add", "SKILL.md", ".gitignore")
    _git(tree, "commit", "-m", "init")

    # THE REVIEWER'S DEFAULT: a clean tree. The patch is empty and `git apply` REFUSES it.
    empty_patch = tmp_path / "702-empty.patch"
    empty_patch.write_text(_git(tree, "diff", "HEAD", "--binary"), encoding="utf-8")
    assert empty_patch.stat().st_size == 0, "a clean tree no longer produces an EMPTY patch"
    refused = subprocess.run(
        ["git", "apply", str(empty_patch)], cwd=tree, capture_output=True, text=True
    )
    assert refused.returncode != 0, (
        "`git apply` now accepts an empty patch, so SKILL.md's recipe no longer needs its guard — "
        "but the guard is what keeps a clean-tree run (every reviewer's) from stopping mid-recipe"
    )

    ruleb.write_text("старый текст\nГДЕ он работает — в СВОЁМ клоне\n", encoding="utf-8")  # the WIP
    newpin = tree / "tests" / "unit" / "test_new_pin.py"                      # UNTRACKED, in a subdir
    newpin.parent.mkdir(parents=True)
    newpin.write_text("def test_new_pin():\n    assert True\n", encoding="utf-8")
    (tree / ".venv").mkdir()
    (tree / ".venv" / "marker").write_text("must not travel\n", encoding="utf-8")  # IGNORED

    clone = tmp_path / "702-pass2-audit"
    _git(tmp_path, "clone", "--no-hardlinks", "-q", str(tree), str(clone))
    assert "ГДЕ он работает" not in (clone / "SKILL.md").read_text(encoding="utf-8"), \
        "a clone now carries uncommitted work — SKILL.md's recipe spends its patch step on a " \
        "claim whose whole justification is that it does not"

    patch = tmp_path / "702-wip.patch"
    patch.write_text(_git(tree, "diff", "HEAD", "--binary") + "\n", encoding="utf-8")
    _git(clone, "apply", str(patch))
    assert (clone / "SKILL.md").read_text(encoding="utf-8") == ruleb.read_text(encoding="utf-8"), \
        "`git diff HEAD` + `git apply` no longer reproduces the TRACKED half of the working tree " \
        "in the clone — the recipe's way of handing the auditor the text actually under audit"
    assert not (clone / "tests" / "unit" / "test_new_pin.py").exists(), (
        "the patch now carries UNTRACKED files too, so the recipe's separate copy step would be "
        "redundant — re-measure before deleting it, because round 1 lost a whole test module here"
    )

    # ...which is why the recipe copies them, listed the way it lists them.
    untracked = _git(tree, "ls-files", "--others", "--exclude-standard").splitlines()
    assert untracked == ["tests/unit/test_new_pin.py"], (
        "`ls-files --others --exclude-standard` no longer names exactly the untracked, "
        f"NON-ignored files: {untracked}. The recipe copies what this prints, so an ignored "
        ".venv appearing here is the `cp -R` hazard the rule warns about, arriving by the back door"
    )
    for rel in untracked:
        (clone / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(tree / rel, clone / rel)
    assert (clone / "tests" / "unit" / "test_new_pin.py").read_text(encoding="utf-8") == \
        newpin.read_text(encoding="utf-8"), \
        "copying what `ls-files --others --exclude-standard` names no longer reproduces the " \
        "untracked half of the working tree in the clone"
    assert not (clone / ".venv").exists(), \
        "the copy step now drags an IGNORED .venv into the clone — that is `cp -R`'s failure " \
        "mode, where the clone's own interpreter can import the ORIGINAL src"


def _post_push_ci_bullet(text: str) -> str:
    """SKILL.md's bullet on what to check AFTER the push — existence and outcome, in that order.

    Sliced, not scanned whole-file, for the reason `_gc_section` records having MEASURED: every
    token below occurs elsewhere in this file. `gh run list` and the run's `status`/`conclusion`
    are named a second time in the REVIEWER's own backstop bullet (deliberately — it re-reads the
    same run later), `[skip ci]` and its family live in the marker bullet above, and «прогон» is
    everywhere. A file-wide substring could not tell "the build-side rule is still stated" from
    "the reviewer's copy of it survives", which is exactly the drift these pins exist to catch."""
    start = text.find("  - **After the push there are TWO checks")
    assert start != -1, (
        "SKILL.md no longer opens its post-push bullet where this pin can find it. If the bullet "
        "was legitimately reworded, move this anchor — do not delete the check"
    )
    end = text.find("\n  - **The push is mandatory", start)
    assert end != -1, "the post-push bullet no longer ends where the «Пуш обязателен» bullet begins"
    bullet = text[start:end]
    assert 0 < len(bullet) < len(text), "the post-push slice is not a proper subset of SKILL.md"
    assert "The push is mandatory" not in bullet, "the slice swallowed the following bullet"
    return bullet


def _claude_ci_outcome_paragraph(text: str) -> str:
    """CLAUDE.md's paragraph carrying the SECOND copy of the same two-check rule.

    Scoped like `_claude_ceiling_paragraph`, and for its measured reason: `stable`, `integration`
    and `release` all appear elsewhere in this file (the whole Releases section is about them), so
    a whole-file scan could not tell "the outcome rule is still stated" from "those words survive
    somewhere"."""
    start = text.find("**A run that EXISTS is not a run that PASSED")
    assert start != -1, (
        "CLAUDE.md no longer opens its CI-outcome paragraph where this pin can find it. If the "
        "paragraph was legitimately reworded, move this anchor — do not delete the check"
    )
    end = text.find("\n\n", start)
    assert end != -1, "the CI-outcome paragraph no longer ends where the next paragraph begins"
    paragraph = text[start:end]
    assert 0 < len(paragraph) < len(text), "the outcome slice is not a proper subset of CLAUDE.md"
    assert "Manual procedure" not in paragraph, "the slice swallowed the following section"
    return paragraph


def test_the_post_push_check_reads_the_runs_OUTCOME_and_not_only_its_existence():
    """VMCP-128 (614): the post-push rule required a CI run to EXIST for your sha — the right guard
    against a swallowed ci-skip marker, and it says nothing about whether the run PASSED. Measured
    hole, not a feared one: overnight into 2026-07-31, 7 of 15 consecutive runs on `main` ended red
    (all seven `lint-and-unit` success + `integration` failure + `release` skipped, so `stable`
    never moved), and on every one of them an agent truthfully reported "a run exists".

    Naively strengthening it breaks both ways, because the run is ASYNCHRONOUS: "wait for green"
    blocks an agent for minutes and dies with a killed turn, while "read `conclusion` right after
    the push" reads a run that has not started answering. The measurements that decide the shape,
    taken on this repo's 40 most recent `main` runs and timed on each run's FIRST attempt (two
    were later re-run by hand; a re-run's `updatedAt` measures how long a HUMAN took to press
    `gh run rerun` — 31 min and 3 h 26 min — not CI, while the runner queue itself was 0 s on 35
    of 38 and never above 80 s):

    * EXISTENCE asks about a fact that does not ripen — the run is created or it never will be —
      so it stays where it was, right after the push. How long GitHub takes to CREATE the run was
      NOT measured here (a committer-date proxy is polluted by the agent's own criteria re-run in
      between), so the rule says to ask twice before raising the marker alarm rather than pretend
      a number it does not have;
    * the OUTCOME does ripen: a run concludes 42–120 s after it appears, median 60 s — but an
      agent's own tail (`advance(to='review')`, the report, `--release`) costs about that long,
      so reading it LAST costs nothing and usually answers;
    * red runs lean fast but do NOT separate: 42–55 s (n=9, median 46) against 53–120 s (n=31,
      median 65) for green — the bands OVERLAP at 53–55 s, so duration alone never tells a slow
      red from a fast green. The first version of this test claimed a clean 42–48 vs 53–120 split;
      that was an artifact of dropping the two re-run runs entirely, and `8b4bfa5`'s FIRST attempt
      is an ordinary push-triggered red at 55 s (`run_started_at == created_at`, no queue) sitting
      inside the green band. The MECHANISM was wrong too, and per-job timing says so: `integration`
      is never the critical path (16–29 s against `lint-and-unit`'s 38–46 s), so it cannot make a
      run shorter by failing early. A run's length is set by `lint-and-unit`; a GREEN run then also
      runs `release` (8–15 s), which a red one skips. Both corrections came from the second
      independent pass over this prose, which is why the lean is now stated as a lean;
    * `gh run list --commit <SHORT sha>` returns `[]` with exit code 0 — indistinguishable from
      "no run", i.e. a false ci-skip alarm. The full 40-char sha is load-bearing, so the rule
      quotes `"$(git rev-parse HEAD)"` rather than a bare sha;
    * an in-flight run renders `conclusion` as the EMPTY STRING, caught live:
      `{"conclusion":"","databaseId":30636770459,"status":"in_progress"}`. Empty is not `null`, so
      a jq `// "unknown"` fallback silently does not fire — which is the second reason the rule
      branches on `status` rather than dressing up `conclusion`;
    * urgency is bounded but not zero: a later green landing moves `stable` with the red commit
      already in it (verified — red `8fc53f8` is an ancestor of today's `stable`; that night the
      catch-up ran 1–48 min), so the lasting cost is the LAST landing of a session.

    What this pins is the SHAPE of the answer, in both files, because two copies of one rule drift
    (the lesson of 556):

    * both checks are stated, and the existence one is not weakened into the outcome one — the
      marker bullet it guards must still be there;
    * the branch is on `status` FIRST. This is the load-bearing bit and it is not decoration: a
      running run's `conclusion` carries neither verdict, so `conclusion != "success" ⇒ not green`
      reads every in-flight run as red, and a rule that cries wolf on the common case is a rule
      agents learn to ignore;
    * the third state has a name that is NEITHER verdict (`НЕИЗВЕСТНО`), and the rule says not to
      wait for it — otherwise it collapses back into one of the two broken naive forms;
    * the deferral has a real addressee. The build side hands the unknown case to the card's
      independent reviewer, who is late BY CONSTRUCTION (starts later, works for minutes, against a
      run that concludes in ≤2 min) — so the reviewer's own bullet must exist, or the hand-off
      dangles. It is not invented ceremony: the implementer AND the reviewer of VMCP-129 (615) both
      checked CI this way unprompted.

    MUTATION-CHECKED — 13 rounds, `__pycache__` cleared between them, every round confirmed to
    select exactly 1 test, both files restored from copies afterwards with `git diff` clean.
    Controls before and after: PASS. Each of these turns it RED: delete SKILL.md's
    `status == "completed"` premise; delete the not-completed branch head; reword that branch to
    «считай, что прогон в порядке»; drop the full-sha caveat; delete the reviewer's backstop bullet
    head; drop the reviewer's «САМ ПО СЕБЕ ещё не `needs_work`» grading; delete CLAUDE.md's
    outcome-paragraph anchor; drift CLAUDE.md's window to 42–130 s while SKILL.md keeps 42–120;
    delete the ci-skip marker bullet head; drop CLAUDE.md's `status` FIRST ordering; drift
    CLAUDE.md's median to 95 s alone; replace CLAUDE.md's per-job `16–29 s` with a vague phrase;
    regress SKILL.md's red band to the falsified 42–48.

    Three of those thirteen exist BECAUSE the round found the pin missing, not to confirm it. The
    median drift and the per-job mechanism were gaps the second independent pass demonstrated on
    the green suite; the «прогон в порядке» rewrite was found by this matrix itself — naming the
    third state «НЕИЗВЕСТНО» and then telling the agent to treat it as fine satisfied every other
    assertion here, which is the precise failure the whole card exists to remove."""
    text = _skill_text()
    bullet = _flat(_post_push_ci_bullet(text))

    # 1. the existing marker guard is DEEPENED, not replaced: its bullet must still be there
    assert "**A COMMIT MESSAGE must contain no literal ci-skip marker" in text, (
        "the ci-skip marker bullet is gone. The outcome check was added ALONGSIDE it, not instead "
        "of it — a green-looking task with no run at all is still the louder failure"
    )

    # 2. both checks are named, and the sha-precise form carries its measured caveat
    assert "**EXISTENCE — right after the push.**" in bullet, \
        "the post-push bullet no longer states the existence check — the ci-skip guard lost its home"
    assert "**THE OUTCOME — ONE look, as the LAST action of the turn.**" in bullet, (
        "the post-push bullet no longer states the OUTCOME check, which is the whole of 614: "
        "'a run exists' was true on all seven red runs nobody noticed"
    )
    assert 'gh run list --commit "$(git rev-parse HEAD)"' in bullet, \
        "the existence check no longer quotes a form that produces the FULL sha"
    assert "FULL 40-character one" in bullet and "`[]`" in bullet, (
        "the full-sha caveat is gone. `gh run list --commit <short sha>` returns [] with exit 0 — "
        "measured — so without it the recipe manufactures a false 'no run' ci-skip alarm"
    )

    # 3. THE load-bearing property: `status` decides, `conclusion` only means something after it
    assert "`conclusion` is meaningful ONLY at `status == \"completed\"`" in bullet, (
        "the rule no longer says `conclusion` is meaningful only once `status` is completed. "
        "Without that premise an agent branches on `conclusion` and reads every in-flight run as "
        "not-green — the naive form this card exists to rule out"
    )
    assert "\"`conclusion` is not `success` ⇒ not green\" is a broken check" in bullet, (
        "the bullet no longer NAMES the broken form (`conclusion` != success ⇒ not green). "
        "Stating the right rule without the wrong one is what gets tidied back"
    )

    # 4. three states, and the third is neither verdict and is not waited on
    for branch in ("`completed` + `success`", "`completed` + `failure`", "not `completed`"):
        assert branch in bullet, f"the post-push bullet no longer answers the {branch} state"
    assert "not `completed` — that is **UNKNOWN**, neither \"green\" nor \"red\"" in bullet, (
        "the in-flight state lost its own name. Calling it green hides the measured hole; calling "
        "it red cries wolf on the common case — it has to be reported as unknown"
    )
    assert "Do not wait" in bullet, (
        "the in-flight branch no longer forbids waiting — 'wait for green' is the other naive form, "
        "and it blocks an agent for minutes and dies with a killed turn"
    )
    assert "do not write \"the run is fine\"" in bullet, (
        "the in-flight branch no longer FORBIDS reporting the run as fine. Naming the state "
        "«НЕИЗВЕСТНО» and then telling the agent to treat it as fine passes every other pin here "
        "— measured: that exact rewrite kept this test green until this assertion was added"
    )

    # 5. the deferral needs a real addressee: the reviewer's backstop must exist
    review = _flat(_independent_review_section(text))
    assert "you are the only one who by construction is LATE" in review, (
        "the reviewer's CI-outcome backstop is gone, so the build side's «не дождался» branch now "
        "hands the unknown case to nobody — which is the original hole with an extra step"
    )
    assert "--commit <FULL sha from evidence>" in review, \
        "the reviewer's backstop no longer names a sha-precise command it can actually run"
    assert "A red run BY ITSELF is not yet `needs_work`" in review, (
        "the reviewer's backstop lost the grading. A red run bounced without reading `jobs` turns "
        "an environment failure into a round trip through the implementer"
    )

    # 5b. VMCP-278 (937): "no run" has TWO causes, and only one of them is the swallowed marker.
    # GitHub attaches one run to a push's TIP, so a commit that arrived non-tip inside a
    # multi-commit push has no run and no check-suite while the work itself lands — measured on
    # `bc960b2` (no run, `check-suites total_count: 0`, no marker in any spelling, ancestor of
    # `origin/stable`, child `b6c7502` green), 1 of 21 task commits in 40 landings. Both agent
    # surfaces have to carry the descendant step, or a reviewer bounces a landed card on a false
    # diagnosis — which is exactly what the rule used to prescribe «без разговоров».
    # MUTATION-CHECKED for 937 (every round `collected 55 items`, the same selection as the
    # control; both files restored sha256-identical; `FAILED `/`ERROR ` lines counted separately):
    # control 0 failed; revert the reviewer sentence to the bare «без разговоров» -> 1 failed;
    # delete CLAUDE.md's «"No run" has a SECOND cause» paragraph -> 1 failed.
    # #997 translated the two surfaces INDEPENDENTLY, and they came out with different wordings
    # of the same rule — their only common substring is the bare `TIP`, which is far too short to
    # pin (it occurs in ordinary prose). So each site carries its own phrase rather than one
    # shared literal; both directions were measured red under deletion.
    tip_sites = (
        (bullet, "the post-push existence check", "a run is created for the push's TIP"),
        (review, "the reviewer", "a run is started on the TIP of a push"),
    )
    for site, where, phrase in tip_sites:
        assert phrase in site, (
            f"{where} no longer says a run belongs to the push's TIP, so 'no run on the full sha' "
            f"reads as a swallowed ci-skip marker again — false on ~5% of landings"
        )
        assert "..origin/main" in site, (
            f"{where} no longer names the command that separates the two causes "
            f"(`git log --oneline <full sha>..origin/main` — is there a descendant with a run?)"
        )
    assert "nobody ran the tree AT your commit" in bullet, (
        "the existence check no longer says what stays true in the BENIGN branch. Without it the "
        "new step reads as 'all clear' — but nothing ever gated the tree at that commit"
    )

    # 6. CLAUDE.md carries the same rule, with the same numbers (two copies of one rule drift)
    claude = _flat(_claude_ci_outcome_paragraph(_claude_md_text()))
    assert 'status == "completed"' in claude and "`status` FIRST" in claude, (
        "CLAUDE.md's copy no longer states the status-before-conclusion order that SKILL.md ships. "
        "Only SKILL.md reaches agents, so the copies must not drift; move BOTH or neither"
    )
    assert "UNKNOWN, never as green" in claude, \
        "CLAUDE.md's copy no longer says what an in-flight run is reported as"
    for window in ("42–120", "42–55", "53–120"):
        assert window in claude and window in bullet, (
            f"the measured window {window} s is missing from one of the two files. These are one "
            f"measurement written down twice — re-measure BOTH or neither"
        )
    # The MEDIAN gets its own re-derivation rather than a place in that loop, and it is here
    # because the second independent pass over this card's prose FOUND IT UNGUARDED: it set
    # CLAUDE.md's median to 95 s, left SKILL.md's at 60, and the suite stayed green — while this
    # test's own message promised "one measurement written down twice". A bare "60" substring
    # would not fix that either (it matches any number containing 60), so each file is read
    # through its own phrasing and the two values are compared.
    skill_median = re.search(r"median (\d+) s", bullet)
    claude_median = re.search(r"median (\d+) s", claude)
    assert skill_median, "SKILL.md's outcome bullet no longer states a median run duration"
    assert claude_median, "CLAUDE.md's outcome paragraph no longer states a median run duration"
    assert skill_median.group(1) == claude_median.group(1), (
        f"the two files disagree on the median run duration: SKILL.md says "
        f"{skill_median.group(1)} s, CLAUDE.md says {claude_median.group(1)} s. One measurement, "
        f"two write-ups — re-measure BOTH or neither"
    )
    assert "16–29" in claude and "16–29" in bullet, (
        "the per-job timing that explains WHY red runs lean fast is missing from one of the two "
        "files. Without it the lean reads as `integration` failing early — which is measurably "
        "false, and was this card's own first defect"
    )
    # 937's half of CLAUDE.md, deliberately read off the WHOLE file rather than a slice: the
    # correction belongs beside the EXISTENCE check, which lives in the ci-skip marker paragraph
    # and not in the outcome paragraph sliced above. The phrase is long enough to be its own
    # anchor, which is what a slice would otherwise be buying.
    assert '**"No run" has a SECOND cause' in _claude_md_text(), (
        "CLAUDE.md no longer records that a non-tip commit gets no run at all, so its copy of the "
        "existence check reads every empty result as a swallowed marker again"
    )


def _after_review_section(text: str) -> str:
    """Sliced out of `references/stuck.md`, which owns this section now."""
    text = _reference("stuck.md")
    start = text.find("\n## After Review\n")
    assert start != -1, "SKILL.md no longer has the section written to a post-review implementer"
    end = text.find("\n## ", start + 1)
    section = text[start:] if end == -1 else text[start:end]
    assert 0 < len(section) < len(text), "the После-Review slice is not a proper subset of SKILL.md"
    return section


def _needs_work_cycle_bullet(text: str) -> str:
    """The «Цикл needs_work» bullet — where BOTH sides of a bounce are told what to expect: its
    first clause is the implementer's move (rework, then advance), its last is the orchestrator's
    (push a fresh reviewer), and #628 added the branches where neither applies.

    Sliced to the BULLET, not to its section, for the same MEASURED reason `_reviewer_bullet`
    records: move the question branch into any OTHER bullet of «Независимое ревью изменений» and
    a section-wide `"call_human" in section` goes green while this bullet still teaches exactly
    one outcome — which is the defect. Verified by running exactly that mutation both ways.

    Both edges are guarded: the end is whichever comes first, the next top-level bullet or the
    next heading (sub-bullets are indented and do not match). The guard below names the bullet
    BELOW this one, which is the edge that can actually be lost — the slice runs forward, so text
    above the anchor can never enter it. Note that `_reviewer_bullet`'s twin guard names the
    bullet ABOVE and therefore cannot fire; that is known dead weight there, not a pattern to
    copy, and saying so here is cheaper than letting the next reader mirror it."""
    start = text.find("\n- **The needs_work cycle")
    assert start != -1, "SKILL.md no longer has the bullet describing the needs_work cycle"
    end = text.find("\n## ", start + 1)
    assert end != -1, "the needs_work-cycle bullet no longer sits inside a section"
    following = text.find("\n- **", start + 1)
    if following != -1:
        end = min(end, following)
    bullet = text[start:end]
    assert 0 < len(bullet) < len(text), "the needs_work-cycle slice is not a proper subset"
    assert "Multi-identity" not in bullet, "the slice swallowed the bullet below it"
    return bullet


def test_the_implementer_RECEIVING_a_needs_work_report_is_told_it_may_be_a_QUESTION():
    """#628: the reviewer's escalation channel (#590) does not reach the human on its own, and
    the receiving end of it was unwritten.

    The channel is three hops — review_task(needs_work) → next_task → call_human — and the code
    half below MEASURES it rather than quoting the card: hop1 pages NOBODY (zero webhook
    requests, and its result has no `notified` key at all), hop2 pages nobody, and only the
    implementer's hop3 reaches the human. So the whole PUSH channel «reviewer → human» hangs on
    one call made by the RECEIVING agent — while until this card every mention of a *question*
    beside `needs_work` sat in the reviewer's bullet, i.e. on the SENDING side. The prose that
    speaks to the RECEIVER framed the bounce as rework wherever it spoke at all: «Правки по
    ревью» (twice — the active-task priority and «После Review»), «Доработанная задача снова
    уходит в Review» (this cycle bullet), and the queue-discipline resume rule's «доработка
    после `review-failed`» — that last one WRAPPED across a line, which is why `git log -S` and
    `grep` are both silent on it as a contiguous string. No count is quoted for that set on
    purpose: its membership is a judgement about who a paragraph addresses (the cycle bullet
    speaks to both sides), which is exactly the kind of number that goes stale silently. A
    question could therefore land in Build unremarked, looking on the board like ordinary rework.

    A needs_work bounce carries OTHER kinds of report too, and this test pins their ROUTES
    rather than their NUMBER — deliberately, because in this paragraph the NUMBER is what keeps
    going stale, the chronicle of it included. No count is given for the closed forms on record
    below, for that reason; they are listed, each with its own measurement, and the list is not
    claimed to be all of them. Every one was measurably incomplete, every one was caught by a
    READER rather than by a test, no two by the SAME reader, and not one of them by a human —
    this card carries no human comment at all.
      * a pre-push DRAFT saying «исходов ДВА», caught by this card's own second independent pass,
        which measured a third branch («this should be SPLIT» — `decompose` is shut from Review by
        #663, so it rides back as a bounce and the owner runs it from Build, landing the parent in
        Backlog as an `epic`). Rewritten before the push, so it never entered the RULE at all;
        only these docstrings quote it.
      * «Три случая» in «После Review», which SHIPPED in `3e7a923`, caught by the independent
        AGENT reviewer of that landing, who measured a FOURTH branch («потеряла смысл» / external
        block — `return_task` is shut from Review by #590 and open to the owner from Build,
        landing the card in Backlog with `blocked` and no assignee).
      * «В обоих случаях» in the needs_work-cycle bullet: the SAME enumeration's orchestrator
        half, closed at TWO branches, shipped in that SAME commit and missed by both readers
        above. It was the round-2 implementer who found and removed it while repairing the other
        half — which is the reason this docstring does not present the pass and the reviewer as
        the only two nets there are.
    MEASURED with the pickaxe SCOPED to the rulebook's own path, because a bare `--all` counts the
    commits that edit these very quotations — gotcha two of the three the rulebook lists for it,
    and it answered "one" while this paragraph was being drafted and "two" the moment it was
    committed. On that path: `git log -S "исходов ДВА"` names NO commit; «Три случая» names
    `3e7a923` (added) and the round-2 commit (removed); «В обоих случаях» names those two plus one
    older, unrelated commit; a nonexistent string names none.
    One copy is beyond correcting, and a reader who greps history should know it: the round-2
    commit's own MESSAGE still states the disproved version ("the list had already shipped closed
    as «исходов ДВА»"). `-S` cannot see commit messages and messages cannot be edited in place.
    Nothing in the suite could go red on any of this. Round 1's pins asserted that destinations
    were NAMED — presence, not closure — so a further member would have been neither required nor
    forbidden by them; measured on the round-1 tree, adding the fourth bullet leaves that test
    green. It is measured now instead: `_sweep_card_movers` re-derives the membership on every
    run, and the prose is pinned for its catch-all instead of for a count. A binary — or ternary —
    "which is it?" rule sends the branch it lacks to rework, which is the receiver's documented
    default bias and the whole failure mode this card is about.

    What "measured" covers, exactly, since this is the claim that was too wide on the first
    attempt: a branch is a (TOOL, FORM) pair, so the sweep re-derives BOTH — the tools from
    `server._DEFERRED_TOOLS`, and the forms of the two multi-form tools from `AGENT_ADVANCE` and
    from review_task's own verdict tuple. Deriving only the tools left a real hole: a new
    `advance` transition is a fifth branch that adds no tool, and the suite stayed green on
    exactly that mutant until the forms were derived too.

    The prose half and the code half are both here on purpose: the code half is what makes this
    card's RULING falsifiable. The alternative weighed and rejected was pinging the human on hop1
    (optionally flagged) — rejected because a hop1 ping pages about a card that is NOT parked
    (asserted below: after hop1 the card is live BUILD work and next_task hands it straight back
    for dispatch), so the ping would race the resume agent, and because the flag would only MOVE
    the page-or-not judgement from one LLM to another. If a hop1 ping is ever added, the prose
    above («до твоего `call_human` человека о вопросе никто не УВЕДОМЛЯЕТ») becomes false and
    this test is what says so."""
    text = _skill_text()

    # ── prose: the RECEIVER is told to recognise a question and where to forward it
    receiver = _after_review_section(text)
    assert "A QUESTION TO THE HUMAN" in receiver, (
        "«После Review» no longer tells the implementer a [review] NEEDS WORK report may be a "
        "QUESTION rather than a defect — the recognition step the whole channel hangs on"
    )
    assert "call_human" in receiver, (
        "«После Review» no longer names the tool that forwards the reviewer's question. The "
        "recognition without the route leaves the agent to guess the human's answer itself"
    )
    assert "`review_task(verdict='needs_work')` pings" in receiver \
            and "NOBODY (zero requests" in receiver, (
        "«После Review» no longer states the measured fact that makes forwarding URGENT: hop1 "
        "pages nobody, so the human learns of the question only from the implementer's call"
    )
    # NOT a bare `"decompose" in receiver`: the recognition paragraph above already names the
    # tool while explaining WHY the reviewer cannot use it, so the bare token stays green with
    # the split BRANCH deleted — measured, that mutation passed until this assert named the
    # route instead of the word.
    assert "`decompose` from Build" in receiver, (
        "«После Review» no longer routes the split branch of a needs_work report. A binary "
        "defect-or-question rule sends «this should be split» to rework — measured below"
    )
    assert "`return_task` from Build" in receiver, (
        "«После Review» no longer routes the external-block branch — the fourth thing a "
        "needs_work report can be, and the one a ternary defect/question/split rule sends to "
        "rework. `return_task` is shut from Review (#590) and open to the owner from Build, so "
        "the reviewer has to package it as a bounce like the rest — measured below"
    )
    # The paragraph's PREMISE — «сдвинул её ровно один» — is measured at the end of this test.
    # What a measurement cannot police is the ADJECTIVE beside it: the sentence used to say «обе
    # формы `to=`», a count hidden in a word, true only while AGENT_ADVANCE holds two keys and
    # silently wrong on a third. Pinned in the derivation-shaped wording instead, because the
    # sweep DOES re-derive the forms and would go red only if a new one also moved the card.
    assert "every `to=` form of `advance`" in receiver \
            and "every verdict of `review_task`" in receiver, (
        "«После Review» is back to COUNTING the forms its Review-side sweep covered. The sweep "
        "derives them, so the number in the prose can go stale while the suite stays green — say "
        "which forms are covered, not how many there were on the day somebody looked"
    )
    # the catch-all is what keeps the list from reading CLOSED, which is the defect this card
    # shipped once and drafted once more. Pinned by the instruction it gives, not by the hedge
    # around it: a reader acts on «спроси человека», and a hedge with no action would leave the
    # guessing in place.
    assert "NOTHING in the list fits" in receiver and "do NOT GUESS" in receiver, (
        "«После Review» lost the catch-all branch, so its enumeration reads as closed again. "
        "Every closed version of this list has so far been measurably incomplete, and a missing "
        "member routes to rework by default — name the fallback, not a count"
    )
    # The no-assignee caveat has to generalise over the routes, not name one of them: naming a
    # single refusal invites trying the next route down the list, and all of them refuse
    # identically (measured at the end of this test).
    assert "ALL the routes above refuse" in receiver, (
        "«После Review»'s no-assignee caveat is back to naming one refusing tool. With several "
        "routes listed above it, a reader who is told only that `call_human` refuses will try "
        "`return_task` next — and get the same refusal, having burned a round to learn it"
    )
    assert "UNFINISHED predecessor" in receiver, (
        "«После Review» lost the second measured edge. Both edges are pinned as prose because "
        "the measurement below only proves the BEHAVIOUR — an agent that is not told a branch "
        "can be gated away reads its refusal as the rulebook being wrong about the branch"
    )

    # ── prose: the cycle bullet no longer teaches Review as the only destination
    cycle = _needs_work_cycle_bullet(text)
    assert "call_human" in cycle and "Your Call" in cycle, (
        "the needs_work-cycle bullet is back to promising Review as the only destination. In the "
        "question branch the card goes to Your Call instead, and there is no reviewer to push"
    )
    assert "the parent leaves for **Backlog** with the `epic` label" in cycle, (
        "the needs_work-cycle bullet no longer names the split branch's DESTINATION. A bare "
        "`decompose` token would not do here either — the same bullet names the tool while "
        "explaining why the reviewer cannot call it, so only the destination is branch-specific"
    )
    # the LABEL, not the column: Backlog is shared with the split branch, so `epic` vs `blocked`
    # is what tells the two apart. Deliberately not a phrase spanning the two — «**Backlog** с
    # меткой `blocked`» wraps in the file, and this test's own docstring records that a wrapped
    # phrase is invisible to a contiguous search, which would make the assert reflow-fragile.
    assert "the `blocked` label" in cycle, (
        "the needs_work-cycle bullet no longer names the external-block branch's DESTINATION. "
        "The orchestrator reading this bullet needs to know a `blocked` card went to Backlog "
        "for human re-triage and is not coming back to Review on its own"
    )
    assert "do not read the list" in cycle, (
        "the needs_work-cycle bullet promises its branch list is complete again. It is the "
        "orchestrator's half of the same enumeration «После Review» keeps open, and the two "
        "going out of step leaves the orchestrator dispatching on a closed list after the "
        "implementer's half was reopened — the shape that has so far been measurably incomplete "
        "every time it was written down"
    )

    # ── code: the three hops, and which one of them actually pages a human
    calls = []
    api = FakeAPI(buckets=workflow.STAGES)
    client = httpx.Client(transport=httpx.MockTransport(
        lambda request: calls.append(request) or httpx.Response(200, text="ok")))
    wf = workflow.Workflow(api, project_id=3, notifier=notify.WebhookNotifier(
        "https://hooks.test/x", tracker_url="https://tracker.test", client=client))
    card = api.add_task("починить дренаж", "Review", assignee=api.me_user)

    hop1 = wf.review_task(card["id"], verdict="needs_work",
                          report="ВОПРОС ЧЕЛОВЕКУ: какой из двух вариантов конфига верный?")
    assert hop1["moved_to"] == "Build"
    assert calls == [], (
        "review_task(needs_work) now pings the human. That may be an improvement, but it makes "
        "SKILL.md's «до твоего call_human человека о вопросе никто не УВЕДОМЛЯЕТ» false and it "
        "pages about a card that is not parked — move the prose and the ruling with it"
    )
    assert "notified" not in hop1, (
        "review_task's result grew a `notified` key. With a webhook configured — as here — that "
        "key is call_human's alone, and an agent reading notified=false on a bounce would "
        "conclude the reviewer's question had been sent to the human when nothing was sent"
    )

    hop2 = wf.next_task()
    assert hop2["task"]["id"] == card["id"] and hop2["stage"] == "Build" and hop2["resume"]
    assert calls == [], "next_task pings the human now — the rulebook says the ping is hop3's"

    # the measured cost of pinging on hop1 instead: the card is not parked, it is live build work
    board = wf.liveness_board()
    assert wf.active_task_ids(board) == [card["id"]] and wf.parked_task_ids(board) == [], (
        "after a bounce the card is supposed to be LIVE build work being dispatched, which is "
        "exactly why the human is paged only once the implementer parks it in Your Call"
    )
    assert "review-failed" in [lb["title"] for lb in api.tasks[card["id"]]["labels"]]

    hop3 = wf.call_human(card["id"], question="какой из двух вариантов конфига верный?")
    assert hop3["moved_to"] == "Your Call" and hop3["notified"] is True, (
        "forwarding the reviewer's question from Build no longer parks it and reports delivery — "
        "the rulebook tells the implementer this call is the whole channel"
    )
    assert len(calls) == 1, f"expected exactly one page for the whole channel, got {len(calls)}"
    board = wf.liveness_board()
    assert wf.parked_task_ids(board) == [card["id"]] and wf.active_task_ids(board) == []

    # ── code: the SPLIT kind of needs_work report, on a fresh board — «this should be SPLIT».
    # It rides the same bounce because decompose is shut from Review (#663), and it lands the
    # card somewhere else again: Backlog, as an epic. This is what makes a binary
    # defect-or-question rule wrong rather than merely incomplete.
    api2 = FakeAPI(buckets=workflow.STAGES)
    wf2 = workflow.Workflow(api2, project_id=3)
    split = api2.add_task("слишком крупная задача", "Review", assignee=api2.me_user)
    with pytest.raises(workflow.WorkflowError):
        wf2.decompose(split["id"], [{"title": "часть A"}, {"title": "часть B"}])
    wf2.review_task(split["id"], verdict="needs_work", report="это надо разбить на две")
    parent = wf2.decompose(split["id"], [{"title": "часть A"}, {"title": "часть B"}])["parent"]
    assert parent["moved_to"] == "Backlog" and parent["labeled"] == "epic", (
        "the split branch no longer ends in Backlog as an epic. SKILL.md tells the receiving "
        f"implementer exactly that destination, and the orchestrator not to wait for Review: "
        f"got {parent}"
    )
    assert api2.stage_of(split["id"]) == "Backlog"
    # the two Backlog branches are told apart by WHICH label, never by how MANY: since #693 both
    # CLEAR the verdict, so on THESE two routes both land carrying exactly one — `epic` here,
    # `blocked` below. Only the VERDICT is cleared, so "exactly one" is a property of these two
    # cards and not of the tools: a card also carrying `bug` keeps it and lands with two. This
    # comment used to claim the opposite ("decompose CLEARS the verdict, return_task (below) adds
    # to it" — quoted with its "(below)", since a sentence whose job is to record what the text
    # used to say is the last place to paraphrase it), and that was true only while `return_task`
    # kept a verdict it had no business keeping; the assert at the end of this function records
    # the inversion in full. Both measured, both pinned, because side-by-side bullets invite the
    # reader to assume the halves match.
    assert [lb["title"] for lb in api2.tasks[split["id"]]["labels"]] == ["epic"], (
        "the split branch no longer leaves the parent carrying `epic` alone. SKILL.md contrasts "
        "it with the external-block branch precisely on the labels — if that changed, the "
        "contrast now teaches the wrong board state"
    )

    # ── code: the EXTERNAL-BLOCK kind — «потеряла смысл». Structurally the split branch again
    # (shut to the reviewer from Review by #590, open to the owner from Build) but with its own
    # destination, and it is the member the shipped ternary lacked. The DROPPED assignee is the
    # part worth pinning: it is what makes this a hand-off to a human rather than a move.
    api3 = FakeAPI(buckets=workflow.STAGES)
    wf3 = workflow.Workflow(api3, project_id=3)
    stale = api3.add_task("задача потеряла смысл", "Review", assignee=api3.me_user)
    with pytest.raises(workflow.WorkflowError):
        wf3.return_task(stale["id"], reason="зависимость выпилена")
    assert api3.stage_of(stale["id"]) == "Review", (
        "the refused return_task moved the card anyway — #590's gate is what forces this branch "
        "through a needs_work bounce, and the whole paragraph rests on it"
    )
    wf3.review_task(stale["id"], verdict="needs_work", report="зависимость выпилена, работа мертва")
    returned = wf3.return_task(stale["id"], reason="зависимость выпилена")
    assert returned["moved_to"] == "Backlog" and returned["labeled"] == "blocked", (
        f"the external-block branch no longer ends in Backlog as `blocked`. SKILL.md routes the "
        f"receiving implementer to exactly that destination, and distinguishes it from the split "
        f"branch by the LABEL, since both land in Backlog: got {returned}"
    )
    assert api3.stage_of(stale["id"]) == "Backlog"
    assert not api3.tasks[stale["id"]]["assignees"], (
        "return_task no longer clears the assignee. SKILL.md says this branch hands the card to a "
        "human for re-triage — a card still assigned in Backlog is a different promise entirely"
    )
    # #693 INVERTED this assert, and the inversion is the point rather than a maintenance edit.
    # It used to require `['blocked', 'review-failed']`, because SKILL.md distinguished the two
    # Backlog branches by the NUMBER of labels. That distinction rested on `return_task` keeping a
    # verdict it had no business keeping: on the strong route (an APPROVED card a human hand-drags
    # back to Build) the same code produced `['blocked', 'reviewed']` — the board claiming accepted
    # AND blocked at once, which this very tool's Done refusal USED TO forbid in so many words. It
    # does not any more, and the same call is the reason: with the verdict cleared first that pair
    # is unreachable — lifting the Done gate today lands `['blocked']` alone (measured) — so the
    # refusal now names the ERASED acceptance instead. Do not read the clause as a live citation.
    # So the branches are now told apart by WHICH label, not by how many, and SKILL.md says so.
    # Asserting the exact list, not `"review-failed" not in`, so that a future edit restoring the
    # pair reddens this rather than passing.
    assert sorted(lb["title"] for lb in api3.tasks[stale["id"]]["labels"]) == ["blocked"], (
        "the external-block branch left something beside `blocked`. Since #693 both Backlog "
        "branches clear the verdict and carry exactly ONE label — `blocked` here, `epic` on the "
        "split branch — and SKILL.md tells the reader to tell them apart by which label it is"
    )


def _review_task_verdicts() -> tuple[str, ...]:
    """review_task's accepted verdicts, READ from workflow.py's own validation tuple rather than
    copied here. A third verdict would be a new FORM, and a form is how a new branch arrives with
    no new tool in sight — see _bounced_card_tool_forms."""
    match = re.search(r"if verdict not in (\([^)]*\))", _workflow_src())
    assert match, (
        "review_task no longer validates its verdicts as a literal tuple. The bounce sweep DERIVES "
        "its forms from that tuple so a new verdict cannot slip past it — re-point it at whatever "
        "declares the verdicts now, do not paste the list back in"
    )
    return ast.literal_eval(match.group(1))


def _bounced_card_tool_forms() -> dict[str, list[tuple[str, dict]]]:
    """One plausible call per agent tool FORM, each enough to reach that form's GATE rather than
    its argument validation — a call that dies on a missing argument measures nothing and reads as
    "does not move the card". Placeholders are filled in per call by the sweep below.

    The tools that have SEVERAL forms today are DERIVED from their source of truth and deliberately
    NOT listed here, because a branch of a needs_work bounce is a (tool, FORM) pair: a new `advance`
    transition is a new candidate branch with no new tool in sight, so a guard that only watches
    tool NAMES is the wrong dimension. That is measured, not hypothetical — the second independent
    pass added {"backlog": ("Build", "Backlog")} to AGENT_ADVANCE, which hands the receiving
    implementer a genuine fifth destination (measured: the card lands in Backlog with the assignee
    KEPT, unlike `return_task`), and while these forms were a hand-written literal the entire suite,
    this sweep included, stayed GREEN on it. What that does NOT reach: the one-form tools below are
    written out by hand, so a new PARAMETER on one of them — `return_task(..., hard=True)` with its
    own destination — is the same hole one step sideways, and nothing re-derives it. Known and
    named, not covered. `to='done'` rides along as a deliberate NON-member: the sweep should measure
    a refusal it expects rather than assume the accepted set has no edges."""
    advance_extra = {"spec": "подход", "worklog": "сделано", "evidence": "abc1234",
                     "root_cause": "причина"}
    return {
        "next_task": [("next_task", {})],
        "claim": [("claim", {"task_id": None})],
        "get_task": [("get_task", {"task_id": None})],
        "comment": [("comment", {"task_id": None, "text": "заметка"})],
        "advance": [
            (f"advance(to={to!r})", {"task_id": None, "to": to, **advance_extra})
            for to in (*workflow.AGENT_ADVANCE, "done")
        ],
        "review_task": [
            (f"review_task({verdict!r})", {"task_id": None, "verdict": verdict, "report": "отчёт"})
            for verdict in _review_task_verdicts()
        ],
        "call_human": [("call_human", {"task_id": None, "question": "какой из вариантов?"})],
        "return_task": [("return_task", {"task_id": None, "reason": "зависимость выпилена"})],
        "decompose": [("decompose", {"task_id": None,
                                     "subtasks": [{"title": "часть A"}, {"title": "часть B"}]})],
        "file_task": [("file_task", {"title": "находка", "related_task_id": None})],
        # both aim at the neighbour the sweep registers on every board (see below): with no
        # sibling to aim at they would refuse, and a refusal here reads as "does not move the
        # card" — which is precisely the false negative this table exists to prevent.
        "handoff": [("handoff", {"task_id": None, "to": "neighbour",
                                 "title": "другая половина"})],
        "transfer_task": [("transfer_task", {"task_id": None, "to": "neighbour",
                                             "reason": "не та доска"})],
        "attach_file": [("attach_file", {"task_id": None, "path": None, "note": "проба"})],
        "download_attachment": [("download_attachment", {"task_id": None,
                                                         "attachment_id": None})],
    }


def _sweep_card_movers(monkeypatch, tmp_path, assignee: bool = True,
                       predecessor: bool = False, bounce: bool = True) -> dict[str, str]:
    """Call EVERY agent tool against ONE card and report which ones move it off the stage it
    starts on, and to where. Each call gets its OWN board, so one tool's move cannot mask the next.

    This exists so that «После Review»'s branch list is MEASURED on every run instead of being a
    number somebody wrote down. That distinction is not theoretical here: every closed form of
    this list that is on record was measurably incomplete — a two-branch pre-push draft, the
    three-branch «Три случая» that shipped in `3e7a923`, and the two-branch orchestrator half that
    shipped beside it in that same commit. Nothing re-derived any of them, and no two were caught
    by the same reader (the chronicle, with its measurements, is in the test that calls this).
    A tool that is shut from Review and open to the owner from Build IS a branch of a needs_work
    bounce, whether or not the rulebook mentions it.

    `bounce=True` (the default) bounces the card out of Review first and sweeps it from BUILD —
    that is the branch list. `bounce=False` sweeps the SAME forms against the card still sitting
    in REVIEW, which measures the paragraph's PREMISE rather than its list: that exactly one tool
    can take a card out of Review, which is why the reviewer has to package everything else as a
    bounce. Same machine, because they are the same closed universal pointed two ways — and the
    Review direction is deliberately a SECOND opinion, not a first: #672 sweeps it too, from the
    same tool list but through a hand-written per-form table, so it answers at the tool dimension
    and this one answers at the (tool, FORM) dimension the build side already had to fix.

    The default board is PLAIN — one card, no relations — because that is the state the branch
    list describes. Other gates can take a branch away without removing it from the list, and the
    rulebook names the two measured ones as EDGES rather than as branches; each gets its own
    sweep here rather than muddying the main one, which would then measure the gate instead of
    the enumeration. `assignee=False` is the card a human left unassigned in Review: since #705 it
    bounces to Queue rather than Build, so the only tool that moves it is `claim` — the rescue
    path. That is also why `home` is MEASURED after the bounce instead of written down as
    "Build": the destination is now a function of the card, and a hardcoded home reports every
    tool that leaves an ownerless card alone as a mover. Where the bounce LANDS is pinned
    separately by the caller, so making home measured here does not hide a change in it.
    `predecessor=True` gives it an unfinished `follows` head: that costs it the
    defect branch specifically, since `advance(to='review')` latches until the predecessor
    reaches Review. Both edges apply to the bounced sweep; they are not swept from Review, where
    the single exit is the reviewer's own verdict and neither gate touches it."""
    forms = _bounced_card_tool_forms()
    unknown = [f.__name__ for f in server._DEFERRED_TOOLS if f.__name__ not in forms]
    assert not unknown, (
        f"new agent tool(s) {unknown} are not in this sweep. Add a plausible call for each and "
        f"see whether it moves a card that was bounced out of Review: if it does, it is a new "
        f"branch of a needs_work report and «После Review» has to name it. This assert guards the "
        f"tool NAMES. The FORMS are derived only for the two tools that have several TODAY "
        f"(`advance` from AGENT_ADVANCE, `review_task` from its verdict tuple) — a new PARAMETER "
        f"on a one-form tool is a branch nothing here re-derives, and that blind spot is named "
        f"rather than implied away"
    )
    movers: dict[str, str] = {}
    for fn in server._DEFERRED_TOOLS:
        for label, form in forms[fn.__name__]:
            api = FakeAPI(buckets=workflow.STAGES)
            # every board carries a neighbour so the cross-project forms reach their GATE
            # rather than a "no such sibling" refusal; inert for every other tool
            neighbour = api.add_project("neighbour", buckets=workflow.STAGES, identifier="NB")
            wf = workflow.Workflow(
                api, project_id=3, siblings={"neighbour": neighbour["id"]},
            )
            card = api.add_task("починить дренаж", "Review",
                                assignee=api.me_user if assignee else None)
            if predecessor:
                head = api.add_task("предшественник", "Build", assignee=api.me_user)
                api.add_relation(card["id"], head["id"], "follows")
            if bounce:
                wf.review_task(card["id"], verdict="needs_work", report="отбой")
            home = api.stage_of(card["id"])
            monkeypatch.setattr(server, "_wf", lambda wf=wf: wf)
            kwargs = dict(form)
            for key in ("task_id", "related_task_id"):
                if key in kwargs:
                    kwargs[key] = card["id"]
            if "path" in kwargs or "attachment_id" in kwargs:
                probe = tmp_path / "probe-628.txt"
                probe.write_text("проба", encoding="utf-8")
                if "path" in kwargs:
                    kwargs["path"] = str(probe)
                else:   # download needs something to download, or it dies before its gate
                    kwargs["attachment_id"] = wf.attach_file(
                        card["id"], str(probe))["attachment_id"]
            result = fn(**kwargs)
            # "moved AND did not report an error". The conjunction has a known blind spot: a tool
            # that moves the card and THEN fails would be counted a non-mover. No agent tool does
            # that today (`_tool` turns a WorkflowError into {"error"} and the gates all run before
            # the writes), and the alternative — counting any move — would report a half-finished
            # refusal as a branch. Named rather than left for the next reader to rediscover.
            refused = isinstance(result, dict) and "error" in result
            landed = api.stage_of(card["id"])
            if landed != home and not refused:
                movers[label] = landed
    return movers


def test_the_needs_work_branch_list_is_MEASURED_not_a_number_somebody_wrote_down(
        monkeypatch, tmp_path):
    """#628: «После Review» enumerates what a `[review] NEEDS WORK` report can be, and every closed
    form of that enumeration ON RECORD was measurably incomplete — «исходов ДВА» missed the split
    branch, «Три случая» missed the external block, «В обоих случаях» closed the orchestrator's
    half of the same list at two. Where a pin existed it AGREED with the prose instead of measuring
    it (round 1's asserted that destinations were NAMED, which a missing member cannot violate), so
    nothing in the suite could go red; every time it was a READER who caught it, never the same one
    twice, and not once a human. The chronicle with its measurements is in the sibling docstring —
    read it for calibration, not for reassurance: the nets here have fired repeatedly AND leaked
    repeatedly, which is why membership is re-derived below instead of remembered.

    The repair is to stop asserting a count anywhere. The prose keeps a catch-all instead (pinned
    above), and membership is re-derived HERE, from `server._DEFERRED_TOOLS`, every run. What the
    branches have in common is structural, not thematic: shut to the reviewer from Review, open
    to the owner from Build, own destination — so the sweep asks exactly that question of every
    tool rather than trusting a list.

    Note what this does and does not guarantee. It pins the four branches that exist TODAY and
    fails loudly on a new tool (the `unknown` assert in the sweep), which is what makes the
    rulebook's «список НЕ обещан закрытым» honest rather than decorative. It does not make
    SKILL.md self-updating: a fifth branch turns this test red, and a human or agent still has to
    write the branch down. That is the intended cost — red is what none of those readings had to
    work with. It also does not speak for a MUTATING tool that never moves a card
    (`comment`, `attach_file`): those are not branches because the receiver has nothing to
    route."""
    movers = _sweep_card_movers(monkeypatch, tmp_path)
    assert movers == {
        "advance(to='review')": "Review",       # the defect branch — rework, then back to Review
        "call_human": "Your Call",              # the question branch — the reviewer's escalation
        "return_task": "Backlog",               # the external-block branch — `blocked`, no assignee
        "decompose": "Backlog",                 # the split branch — `epic`, children into Queue
        "handoff": "Queue",                     # the dependency branch — parked, blocked, no assignee
        "transfer_task": "Backlog",             # the misfile branch — into the NEIGHBOUR's Backlog
    }, (
        f"the set of tools that can move a bounced card off Build changed: {movers}. Every member "
        f"is a branch a needs_work report can be, and «После Review» routes the receiving "
        f"implementer by name — a member missing from that list is routed to rework by default, "
        f"which is the exact failure this card was bounced for, twice"
    )

    # The rulebook's caveat about a card bounced with NO assignee. Until #705 that caveat was
    # "every one of those routes refuses, identically, and only a human can rescue it", and this
    # assert measured the closed universal instead of trusting it — it read {} because the card
    # landed in Build ownerless, where nothing could touch it and the reviewer's question died.
    # It now reads the OTHER half of the same measurement: the bounce puts an ownerless card in
    # Queue, so exactly one tool moves it, and it is `claim` — the card is rescuable by the
    # ordinary pump, no human required. Still written as a full sweep rather than "claim works":
    # a SECOND mover appearing here would mean an ownerless card can be walked somewhere else
    # before anyone owns it, which is the shape of the bug this replaced.
    # #1179 adds the second member deliberately, and it does not weaken the shape above:
    # `transfer_task` requires no ownership BY DESIGN — a card filed on the wrong board is a
    # misfile anyone can see, exactly as `file_task` needs no ownership — and it does not walk
    # the card FURTHER DOWN this project's pipeline, it takes it off this board entirely, into
    # a neighbour's Backlog where their human triages it. The bug this assert guards is an
    # ownerless card being advanced past a stage nobody owns it through; leaving the board is
    # the opposite of that. `handoff` is absent because it does require ownership.
    first = _sweep_card_movers(monkeypatch, tmp_path, assignee=False)
    assert first == {"claim": "Design", "transfer_task": "Backlog"}, (
        f"the tools that move an ownerless bounced card changed: {first}. Since #705 it lands in "
        f"Queue and `claim` is its way back into the pipeline — an empty result means it is "
        f"stranded again (the #705 bug), and an extra member means something moves a card that "
        f"nobody owns"
    )
    # ...which is only true because of WHERE it lands, so pin that separately — `home` inside the
    # sweep is measured, so a regression in the destination alone would slip past the assert above
    # (every tool would simply be compared against the new home). Both halves of the split, since
    # the ASSIGNED destination is what must not move: that card has an implementer to go back to.
    for assignee, expected in ((False, "Queue"), (True, "Build")):
        api = FakeAPI(buckets=workflow.STAGES)
        wf = workflow.Workflow(api, project_id=3)
        card = api.add_task("починить дренаж", "Review",
                            assignee=api.me_user if assignee else None)
        result = wf.review_task(card["id"], verdict="needs_work", report="отбой")
        assert (api.stage_of(card["id"]), result["moved_to"]) == (expected, expected), (
            f"a needs_work bounce of a card with assignee={assignee} landed in "
            f"{api.stage_of(card['id'])} (reported {result['moved_to']}), not {expected}"
        )

    # The other measured edge, and the other closed universal in that caveat: an unfinished
    # predecessor costs the card the DEFECT branch and only that one. Pinned for the same reason
    # as the line above — the rulebook says «отпадает ровно ветка „дефект“», and "ровно" is a
    # claim about the whole set, so it is re-derived here instead of remembered.
    gated = _sweep_card_movers(monkeypatch, tmp_path, predecessor=True)
    assert gated == {"call_human": "Your Call", "return_task": "Backlog",
                     "decompose": "Backlog", "handoff": "Queue",
                     "transfer_task": "Backlog"}, (
        f"an unfinished predecessor no longer costs exactly the defect branch: {gated}. SKILL.md "
        f"tells the implementer the other routes still work and not to start guessing — if the "
        f"gate widened, that advice now strands them"
    )

    # And the PREMISE the whole recognition step rests on, which is the same closed universal
    # pointed the other way: the rulebook says a card in Review is moved by exactly one agent
    # tool, and that is WHY a reviewer's question, split or block has to ride back as a bounce.
    # NOT a fresh claim — #672's test_exactly_ONE_agent_tool_walks_a_card_out_of_Review already
    # pins it, off the same `server._DEFERRED_TOOLS`. What it does NOT pin is the FORM dimension:
    # its per-form calls are a hand-written lambda table, which is precisely the shape this card
    # had to replace on the Build side. MEASURED, and it is why this is here rather than trusted:
    # add {"queue": ("Review", "Queue")} to AGENT_ADVANCE and the whole suite reports ONE failure,
    # this one — the #672 sweep never calls the new form, so it stays green on a second exit.
    # Two overlapping asserts on purpose, then, and the overlap is only at the tool dimension.
    from_review = _sweep_card_movers(monkeypatch, tmp_path, bounce=False)
    assert from_review == {"review_task('needs_work')": "Build"}, (
        f"a card sitting in Review can now be moved by more than the reviewer's own verdict: "
        f"{from_review}. «После Review» derives the receiving implementer's whole recognition "
        f"step from there being exactly one exit — with a second one, a reviewer would no longer "
        f"have to package everything as a needs_work report, and the branch list stops being the "
        f"right question to ask"
    )


def test_both_texts_that_teach_the_ownerless_bounce_name_its_MEASURED_destination():
    """#705 ships a behaviour change in TWO prose surfaces — SKILL.md's «После Review» caveat and
    the `review_task` tool docstring — and this card's second pass showed both were unpinned:
    rewriting either to say the ownerless bounce lands in Build (or Backlog) left the whole
    860-test suite green. That is the exact drift this repo pins elsewhere (the skill contract
    reads `_skill_text()`, and test_advance_report_arguments reads `server.advance.__doc__`);
    it just had not been applied here.

    So the destinations are MEASURED first and the texts are required to name what was measured
    — not to contain a hardcoded word. Change the behaviour and the first assert fires; change
    only a text and the ones after it do. The tool docstring matters as much as the rulebook:
    it is what an agent whose session never loaded the skill reads."""
    measured = {}
    for assignee in (False, True):
        api = FakeAPI(buckets=workflow.STAGES)
        wf = workflow.Workflow(api, project_id=3)
        card = api.add_task("карточка", "Review",
                            assignee=api.me_user if assignee else None)
        measured[assignee] = wf.review_task(
            card["id"], verdict="needs_work", report="отбой")["moved_to"]
    assert measured == {False: "Queue", True: "Build"}, (
        f"the needs_work destinations changed to {measured}; both texts below teach the old ones"
    )
    ownerless, assigned = measured[False], measured[True]

    doc = server.review_task.__doc__ or ""
    assert ownerless.upper() in doc and assigned in doc, (
        f"review_task's docstring must name BOTH measured destinations "
        f"({ownerless} for an ownerless card, {assigned} for an assigned one): {doc}"
    )

    # Every place the rulebook cites this card must name the destination IN BOLD right at the
    # citation. A ±8-line window was the first draft and it was a FICTITIOUS pin — measured:
    # rewriting the caveat to «**Build** (#705), и она НЕ становится обычной свободной задачей»
    # left the suite fully green, because the surrounding paragraph mentions Queue several times
    # for other reasons (the ordinary Queue gates, the review-failed note) and the second
    # citation still said Queue. The window asked "is the word nearby", which a paragraph about
    # queues answers yes to no matter what it claims. So the assert is on the CLAIM's own shape:
    # the bold destination adjacent to the card number, and — the half that actually kills the
    # drift — the assigned destination must never appear there instead.
    lines = _skill_text().splitlines()
    cited = [i for i, ln in enumerate(lines) if "#705" in ln]
    assert cited, "SKILL.md no longer cites #705 anywhere — the caveat it fixed is unexplained"
    for i in cited:
        near = "\n".join(lines[max(0, i - 1):i + 1])
        assert f"**{ownerless}**" in near, (
            f"SKILL.md cites #705 at line {i + 1} without naming the measured ownerless "
            f"destination as **{ownerless}** at the citation:\n{near}"
        )
        assert f"**{assigned}**" not in near, (
            f"SKILL.md line {i + 1} names **{assigned}** at the #705 citation — that is the "
            f"ASSIGNED destination; an ownerless bounce measurably lands in {ownerless}:\n{near}"
        )


def test_skill_and_tool_docstring_tell_agents_to_echo_the_filed_ref_not_build_one():
    """#735: the CODE half of this fix (file_task returning `filed.ref`) cannot stop a fabricated
    reference on its own — nothing forces an agent to use the value. The rule that does is prose,
    in two places an agent actually reads: SKILL.md's «Ссылайся на задачу человекочитаемо» and
    the `file_task` tool docstring. Both were shipped unpinned, so deleting either left the whole
    suite green while the behaviour that #660 shipped — inventing `VMCP-181` for a card that is
    really `VMCP-195` — became allowed again by the only text that ever forbade it.

    Pinned here as three claims, each the reason the fix works rather than a wording:
      * the rulebook names `filed.ref` — i.e. tells the agent the value EXISTS, which is what
        makes "don't invent one" an instruction rather than a scolding;
      * the rulebook forbids composing a ref, and says what a composed one costs (it resolves to
        a live UNRELATED card, the property that makes it worse than a broken link);
      * the tool docstring, which agents read without the skill loaded, carries the same rule.

    Measured RED for each: delete the `filed.ref` sentence from SKILL.md -> first assert; delete
    the «СОБИРАТЬ его самому нельзя» bullet -> second; delete the NAMING paragraph from
    server.file_task's docstring -> third.

    RE-SCOPED BY VMCP-213 (756): the first of those three was asked of the WHOLE FILE while the
    other two were asked of a window, which is a pin narrower than its siblings in the direction
    that matters — file-wide accepts any copy anywhere. MUTATION-CHECKED, `__pycache__` deleted
    per round then `PYTHONDONTWRITEBYTECODE=1`, this file as the selection, restored from a COPY
    with the restore confirmed by sha256 and by returning to the control. Control round: 0 failed.
      * replace `filed.ref` inside the «Ссылайся» bullet with a paraphrase, leaving the token
        nowhere in SKILL.md -> 1 failed. That is the round the file-wide form also caught
      * what it did NOT catch is the reason for the count assert rather than a second `in`: a
        SECOND copy of the token anywhere in the file satisfies `in text` while the live bullet
        has lost it, which is #700's shape one file over. The bullet slice plus `count == 1`
        refuses both directions at once"""
    text = _skill_text()
    # The three literals below are one rule, so they are read from ONE slice — VMCP-213 (756).
    # `filed.ref` used to be asked of the WHOLE FILE while its two siblings were asked of a
    # 2500-character window, which is the mismatch that card names: measured, the value sits 2697
    # characters past the anchor, so it was OUTSIDE its siblings' window and could only ever have
    # been a file-wide check. File-wide is the shape #700 was filed against — any copy anywhere,
    # including a card quoting an older rule, satisfies it while the live bullet loses the value.
    # The slice is the «Ссылайся» BULLET, from its heading to the next top-level `- `, chosen over
    # widening the window because a character count is exactly what went stale here: measured,
    # the bullet is 3504 characters today, holds all three literals, and holds `filed.ref` once —
    # which is also the file's only occurrence, so the count assert costs nothing and refuses the
    # second copy that would make the pin unable to tell the live rule from a quotation of it.
    ref_rule = text[text.index("Refer to a task in a human-readable way"):]
    ref_rule = ref_rule[:ref_rule.index("\n- ")]
    assert ref_rule.count("filed.ref") == 1, \
        "SKILL.md's «Ссылайся» bullet no longer names `filed.ref` exactly once (#735/#756) — " \
        "at zero, 'do not invent one' asks for a value the agent believes it lacks; above one, " \
        "the pin can no longer tell the live rule from a quotation of an older one"
    assert "ASSEMBLING one yourself is not allowed" in ref_rule, \
        "SKILL.md no longer forbids composing a ref by hand — the #660 failure mode"
    assert "UNRELATED LIVE card" in ref_rule, \
        "SKILL.md no longer says WHY a composed ref is worse than none: it resolves to a live " \
        "unrelated card rather than announcing itself as broken"

    doc = inspect.getdoc(server.file_task) or ""
    assert "filed.ref" in doc, \
        "the file_task tool docstring no longer names filed.ref — agents reading the tool " \
        "schema alone (no skill loaded) lose the rule entirely"
    assert "NEVER assemble" in doc, \
        "the file_task docstring no longer forbids assembling a ref from the id (#735/#660)"


def _commit_recipe_slice(text: str) -> str:
    """The rulebook's paragraph on how to BUILD a commit message — VMCP-229 (773)."""
    start = text.find("BUILD THE COMMIT BODY WITH")
    assert start != -1, (
        "SKILL.md no longer tells agents how to build a commit message. That paragraph is the "
        "whole of #773: without it the default is `git commit -m \"…\"`, where an unescaped "
        "backtick is command substitution and the house style puts one around every identifier"
    )
    end = text.find("\n  - **", start)
    assert start < end < len(text), "the commit-recipe slice is not a proper subset of SKILL.md"
    return text[start:end]


def test_the_rulebook_prescribes_a_commit_form_the_shell_cannot_rewrite():
    """#773: `git commit -m "…"` silently ate three words out of this repo's own `5389be0`.

    Two assertions, because they fail apart. The first asks that the prescribed form is there;
    the second asks that the QUOTED delimiter is, and that one is not a restatement — measured on
    a live shell, `<<MSG` without quotes substitutes exactly like `-m` does (a planted
    `echo GONE` ran, `$HOME` expanded), so a recipe that said "use a heredoc" and stopped would
    fix nothing while looking like a fix.
    """
    recipe = _commit_recipe_slice(_skill_text())
    fence = [ln.strip() for ln in recipe.splitlines() if ln.strip().startswith("git commit")]
    assert any(ln.startswith("git commit -F - <<'MSG'") for ln in fence), (
        f"no line of the recipe's fence prescribes the QUOTED heredoc: {fence}. Asserted on the "
        "FENCE LINE, not on the substring anywhere in the slice — the paragraph's own opening "
        "sentence also contains `<<'MSG'`, so a substring check stayed GREEN when the working "
        "example was mutated to an unquoted delimiter. Measured: that mutation was 53 passed"
    )
    assert any(ln.startswith("git commit -F - <<MSG") for ln in fence), (
        f"the recipe stopped SHOWING the unquoted heredoc as a trap: {fence}. It is the row a "
        "reader is most likely to write by accident, because it looks like the fix"
    )


def test_the_rulebook_shows_the_form_that_FAILS_beside_the_one_that_works():
    """A rule that only shows the right answer is obeyed until someone is in a hurry. This one
    carries the measured counter-example — the `-m` form losing its words — because an agent who
    has just watched a message come back wrong needs to recognise WHICH shape did it, and the
    two look alike at a glance.

    MUTATION-CHECKED for both tests here, selection `tests/unit/test_skill_contract.py`,
    `__pycache__` deleted and then PYTHONDONTWRITEBYTECODE=1, restored from a byte copy and the
    file confirmed sha256-identical. Control round: 0 failed. Unquote the working heredoc -> 1
    failed; delete the broken `-m` row -> 1 failed; drop the incident sha -> 1 failed.
    THE FIRST TWO OF THOSE WERE 53 PASSED before the asserts were sharpened, and that is worth
    recording rather than quietly fixing: both checks were substring lookups that the SURROUNDING
    prose satisfied — the paragraph's opening sentence also names `<<'MSG'`, and the fence carries
    a SECOND `-m` line (the escaped form that survives). Each pin looked exactly as green as it
    does now while measuring nothing about the row it was named for."""
    recipe = _commit_recipe_slice(_skill_text())
    assert "keeps  and  and" in recipe, (
        "the recipe no longer shows what the failing form ACTUALLY LANDED — the collapsed "
        "`keeps  and  and /Users/…`. Asserted on that output rather than on the string "
        "`commit -m \"`, because the fence carries TWO `-m` lines (the broken one and the "
        "escaped one that survives): a check for the flag alone stayed GREEN with the broken "
        "row deleted. Measured: that mutation was 53 passed"
    )
    assert "5389be0" in recipe, (
        "the recipe stopped naming the commit this was measured on. The sha is what makes it an "
        "incident report rather than a warning someone imagined"
    )
