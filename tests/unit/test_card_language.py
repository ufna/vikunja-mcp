"""THE `language` KEY MOVES PROSE AND NOTHING ELSE (#1165).

The key has two halves and only one of them can be tested here. The half this file measures is
the prose the PRODUCT authors — six of `workflow.py`'s twelve `add_comment` sites, each of them
now a marker literal wrapped around a `card_text(self.language, ...)` body. The OTHER half — the
spec, the worklog and the review report, which are the bulk of a card's text — is written by the
AGENT, so all this tool can do is carry the value in `next_task`'s payload and state the rule in
SKILL.md. Only the carrying is pinnable, and it is pinned below; nothing in a unit test can check
that an agent obeyed the rule.

TWO THINGS MUST NOT MOVE, and they fail in opposite directions.

* THE MARKER. `workflow.py` reads comment text in exactly two places — `startswith("[review]")`
  and `startswith("[worklog]")`, both in the review-offering branch — so a per-language spelling
  on EITHER OF THOSE TWO drops every card written under the other setting out of the offering,
  silently. The remaining eight markers are not parsed by anything, and the sweep below records
  that directly: localising `[claim]` fires the flip pin and the no-bracket pin while leaving the
  classification pin GREEN. They are frozen anyway, because the vocabulary is read by eye and by
  grep, and a half-translated vocabulary is worse than either half. This is
  measured TWICE and deliberately so:
  `test_flipping_the_language_moves_the_body_and_never_the_marker` BUILDS both card streams and
  compares them byte-for-byte, and
  `test_a_card_written_in_one_language_is_still_classified_after_the_flip` drives the actual
  consequence through `next_task`'s real offering branch. The first would still pass if the
  markers were identical and the offering logic had been rewritten to key off something else;
  the second is the regression that matters.
* THE VERDICT TOKENS. `[review] APPROVE` / `[review] NEEDS WORK` are not in the table at all,
  and `test_the_verdict_tokens_do_not_translate` says so with a card built under `ru`. SKILL.md
  quotes both spellings to the reviewer, so localising them would make the rulebook false in
  one of the two languages.

WHY THE COMPARISON IS BUILT RATHER THAN READ. The acceptance criterion for this card was
explicit that the invariance be measured and not asserted by reading the diff, and the reason is
visible in the mechanism: a marker can acquire a Cyrillic look-alike (`а`, U+0430) that no diff
reader distinguishes, and the two languages live in one table where a body and its bracket would
sit on the same line if the bracket were ever moved in. So the runs are driven end to end
against `FakeAPI` and the comment streams are compared as bytes.

MUTATION SWEEP. Selection is this file alone (`tests/unit/test_card_language.py`) so no
collateral test can stand in for a pin. Run in a CLONE of the worktree — never in the tree being
edited — with `__pycache__` deleted and `PYTHONDONTWRITEBYTECODE=1` before every round, and
`vikunja_mcp.__file__` printed each round, resolving inside the clone every time. Rounds are read
by COUNTING lines beginning `FAILED `, with `ERROR ` counted separately, and `-q` is dropped so
the `collected` line is there to cross-check. Every patcher asserts it replaced exactly ONE
occurrence, so a round that failed to mutate fails loudly instead of coming back green. Each
round is stated beside its own control:

* opening control 0 failed, 0 errors, 12 collected.

* `[claim]` moved INTO the table and localised there (`"[claim] …"` / `"[клейм] …"`), the marker
  drift the whole design is arranged to prevent: control 0 failed, 12 collected; mutation 2
  failed — the flip pin and `test_no_card_text_entry_contains_a_marker_bracket`. NOT the
  classification pin, and that is the useful half of this round: the offering branch parses
  `[worklog]` and `[review]` and nothing else, so a `[claim]` drift is invisible to it. Two
  separate pins are needed precisely because one marker's drift is caught structurally and
  another's behaviourally.

* `Workflow.claim` reverted to a hard-coded English body, i.e. one call site silently ignoring
  `language`: control 0 failed, 12 collected; mutation 1 failed, the flip pin alone — showing
  its body half is live independently of its marker half.

* `card_text` made to ignore its `language` argument and always return the `en` row: control 0
  failed, 12 collected; mutation 2 failed (the flip pin and the `[attach]` units pin). The
  marker and classification pins stay GREEN, correctly — an all-English board classifies fine,
  which is exactly why "cards actually flip language" needs an assertion of its own.

* `config.load_config` made to fall back to `en` on an unknown value instead of raising: control
  0 failed, 12 collected; mutation 1 failed, `test_an_unknown_language_is_refused_by_name`.

* the `result["language"] = self.language` line deleted from `with_wip`: control 0 failed, 12
  collected; mutation 2 failed — the payload pin, and the classification pin THROUGH ITS PAYLOAD
  ASSERT rather than its classification assert. Recorded that way because the distinction is the
  point: the classification itself is unaffected, and this is the half the tool cannot check any
  further, since with the key gone from the payload the agent is simply never told.

* `[worklog]` given a per-language spelling in `advance` — the one marker the offering branch
  parses on the write side: control 0 failed, 12 collected; mutation 2 failed, the flip pin and
  the classification pin. THIS is the round that proves the classification pin measures what its
  name says, and the one the `[claim]` round above could not provide.

* `[review] APPROVE` given a per-language spelling — the other half of the timestamp comparison,
  and simultaneously a verdict token: control 0 failed, 12 collected; mutation 3 failed, adding
  `test_the_verdict_tokens_do_not_translate` to the previous pair.

* closing control 0 failed, 0 errors, 12 collected, every mutated source byte-identical to the
  baseline afterwards (compared as bytes, per round-trip).

THE `12 collected` ABOVE NAMES THE PRE-#1168 TREE and is left standing rather than restated: each
round was honest about the selection it ran on. #1168 added
`test_no_translated_row_leaves_an_english_word_behind` to this file and moved the driver out to
`tests/unit/cardsites.py`, so the selection is larger now. Its own rounds are recorded in that
test's docstring with their own controls. None of the round COUNTS above moves — re-run on the
larger selection rather than assumed: control 0 failed / 0 errors, 13 collected; the `[claim]`
round -> 2 failed, exactly as recorded. But do not read that as "the new test cannot see them":
that round moves `[claim]` INTO `cardtext._TABLE`, which is precisely what the new test reads, and
it holds at 2 only because the round localises the bracket in the `ru` column too and so leaves no
Latin run behind. Keeping `[claim]` in the `ru` cell would redden the new test as well — that
variant was not run, and is written here as a prediction, not a round.
"""
import re

import pytest

# The one run of every comment-writing path in workflow.py. It lived in this file until #1168
# needed it in `test_card_text_is_ascii.py` too, and that file cannot import it from here — the
# `_MARKERS` import just below already points the other way, so the cycle would close. It MOVED
# rather than being copied: two drifting copies of "every comment site" would leave both pins
# claiming a completeness neither of them has.
from tests.unit.cardsites import attach_line, drive_every_comment_site, marker, wf_for
from tests.unit.fakes import FakeAPI
# The marker vocabulary, imported from the pin that OWNS it rather than restated here. The
# coupling is the feature: add a marker there and this file's completeness check fails until the
# driver reaches it, which is what keeps "every marker survives the flip" from quietly meaning
# "every marker this driver happened to write".
from tests.unit.test_card_text_is_ascii import _MARKERS
from vikunja_mcp import cardtext
from vikunja_mcp.config import DEFAULT_LANGUAGE, LANGUAGES, ConfigError, load_config
from vikunja_mcp.workflow import STAGES, Workflow


# --- the acceptance pins -------------------------------------------------------------------

def test_the_driver_reaches_every_marker(tmp_path):
    """The completeness check under the flip pin: a marker the driver never writes is a marker
    the flip pin never compares, and it would go untested while looking covered."""
    api, wf = wf_for("en")
    written = {marker(c) for c in drive_every_comment_site(api, wf, tmp_path)}
    missing = sorted(set(_MARKERS) - written)
    assert not missing, (
        f"drive_every_comment_site never writes {missing}, so the invariance pin below silently "
        f"stops covering it. Add the transition that emits it, or — if the marker was genuinely "
        f"retired — drop it from _MARKERS in test_card_text_is_ascii.py, which owns that list"
    )


def test_flipping_the_language_moves_the_body_and_never_the_marker(tmp_path):
    """BUILT AND MEASURED: two full boards, one per language, compared position for position.

    The two runs drive the same sequence of transitions, so the streams line up. Two things are
    then asserted, and they fail in opposite directions. Every pair's MARKER must be byte-
    identical — a marker moving with the language means the key did too much. And the set of
    comments left UNCHANGED must be exactly the four markers whose whole body is agent-supplied —
    a comment joining that set means a call site stopped consulting the key, i.e. it did too
    little. The unchanged SET is asserted rather than a count of changed ones, because it is the
    sharper statement: it says which comments are the tool's own prose and which are not.
    """
    en_api, en_wf = wf_for("en")
    ru_api, ru_wf = wf_for("ru")
    en = drive_every_comment_site(en_api, en_wf, tmp_path / "en")
    ru = drive_every_comment_site(ru_api, ru_wf, tmp_path / "ru")

    assert len(en) == len(ru) > 10, (
        f"the two runs produced {len(en)} and {len(ru)} comments; they must take the same path "
        f"for a positional comparison to mean anything, and there must be enough of them to be "
        f"worth comparing"
    )
    for en_comment, ru_comment in zip(en, ru):
        assert marker(en_comment) == marker(ru_comment), (
            f"the marker moved with the language: {marker(en_comment)!r} under en against "
            f"{marker(ru_comment)!r} under ru. Markers are a WIRE FORMAT — workflow.py matches "
            f"rendered comment text with startswith() — so a per-language spelling silently "
            f"re-routes the review offering on every card written under the other setting. "
            f"Translate the BODY; never the bracket"
        )

    # WHICH comments moved is a sharper statement than how many, and it is the second half of
    # the feature stated as a pin: a comment survives the flip UNCHANGED exactly when its body is
    # the agent's own text, which this tool never rewrites in any language. `[worklog]` is on the
    # changed side because the tool contributes the `Root cause:`/`Worklog:` prefixes around the
    # agent's words; `[review]` is on the unchanged side because the verdict token is not prose.
    unchanged = sorted({marker(a) for a, b in zip(en, ru) if a == b})
    assert unchanged == ["[blocked]", "[needs-human]", "[review]", "[spec]"], (
        f"the flip left {unchanged} unchanged. The tool translates the prose IT authors and "
        f"nothing else, so the unchanged set is exactly the markers whose whole body is the "
        f"agent's own text. A marker leaving that list means a call site stopped consulting "
        f"`language`; a marker joining it means agent text started being rewritten"
    )
    # the ASCII half, measured here rather than reasoned: en stays ASCII, ru does not, and that
    # asymmetry is exactly what #1164's pin had to be re-derived for.
    assert all(c.isascii() for c in en), "the en board is no longer ASCII"
    assert any(not c.isascii() for c in ru), "the ru board came out ASCII — nothing translated"


def test_a_card_written_in_one_language_is_still_classified_after_the_flip():
    """THE REGRESSION THAT MATTERS: the review offering reads a card the OTHER setting wrote.

    Not a restatement of the marker pin next door. That one compares bytes; this one drives the
    consequence — `next_task`'s real offering branch, which decides whether a Review card is
    handed to a reviewer by comparing the timestamps of the last comment starting `[worklog]`
    against the last starting `[review]`. A localized marker passes neither, but a localized
    marker is not the only way to break this: anything that made the offering key off body text
    would fail here and pass there.

    Driven in BOTH directions, because the two are not symmetric in the code: `en` writing and
    `ru` reading exercises a reader whose own table is Russian, and the reverse exercises a
    Russian card read by an English reader — the shape a project gets the day its human edits
    the toml.
    """
    for writer_language, reader_language in (("en", "ru"), ("ru", "en")):
        api = FakeAPI(buckets=STAGES)
        writer = Workflow(api, project_id=3, language=writer_language)
        task = api.add_task("a card", "Queue")
        writer.claim(task["id"])
        writer.advance(task["id"], to="build", spec="the approach")
        writer.advance(task["id"], to="review", worklog="what was done", evidence="deadbeef")

        # the human flips .vikunja-mcp.toml; the next session's Workflow reads the OTHER language
        reader = Workflow(api, project_id=3, language=reader_language)
        offer = reader.next_task()
        assert offer.get("review") is True and offer["task"]["id"] == task["id"], (
            f"a card whose [worklog] was written under {writer_language!r} is no longer offered "
            f"for review by a workflow reading {reader_language!r}: got {offer!r}. The offering "
            f"branch matches the RENDERED marker with startswith(), so this is what a localized "
            f"marker costs — every card written before the flip stops being reviewable"
        )
        assert offer["language"] == reader_language, (
            "the payload must report the CURRENT setting, not the one the card was written under"
        )

        # ...and the verdict still takes it back off the offering, from the other side of the flip
        reader.review_task(task["id"], verdict="approve", report="fine")
        assert reader.next_task().get("review") is not True, (
            "a verdict written under the reader's language no longer suppresses the offering, so "
            "the same card would be dispatched to a reviewer on every tick"
        )


def test_the_default_is_en_with_no_toml_key(tmp_path):
    """No `language` in the toml -> `en`, and that is resolved in `load_config`, not guessed by a
    reader. Asserted through a real file rather than by constructing a Config, because the
    default belongs to the READ: a Config built by hand would pass on the dataclass default even
    if `load_config` had stopped consulting the toml at all."""
    (tmp_path / ".vikunja-mcp.toml").write_text(
        '[tracker]\nurl = "https://tracker.example"\nproject_id = 10\n', encoding="utf-8"
    )
    cfg = load_config(cwd=tmp_path, environ={"VIKUNJA_TOKEN": "t"})
    assert cfg.language == "en" == DEFAULT_LANGUAGE

    # and it reaches a card: an unconfigured Workflow writes the English body
    api, wf = wf_for(cfg.language)
    task = api.add_task("a card", "Queue")
    wf.claim(task["id"])
    assert api.comments_text(task["id"])[0].startswith("[claim] agent-infra claimed this task")


def test_an_unknown_language_is_refused_by_name(tmp_path):
    """An un-honourable option is un-expressible LOUDLY — the `wip_limit = 0` precedent.

    Falling back to the default would be the worst outcome available: the key's larger half is
    an INSTRUCTION to the agent, so a silent fallback tells it to write in the wrong language
    and leaves no signal anywhere. The refusal names the accepted set, since the whole point is
    that the reader can fix it without opening the source.
    """
    (tmp_path / ".vikunja-mcp.toml").write_text(
        '[tracker]\nurl = "https://tracker.example"\nproject_id = 10\nlanguage = "de"\n',
        encoding="utf-8",
    )
    with pytest.raises(ConfigError) as excinfo:
        load_config(cwd=tmp_path, environ={"VIKUNJA_TOKEN": "t"})
    message = str(excinfo.value)
    assert "de" in message
    for known in LANGUAGES:
        assert known in message, f"the refusal does not name {known!r} as an accepted value"


def test_language_is_toml_only_and_never_read_from_the_environment(tmp_path):
    """Committed TEAM POLICY, exactly like `wip_limit` and `require_review_independence`.

    Which language a project's cards are written in is a property of the PROJECT, reviewed by
    the whole team in a committed file — not of the machine that happens to be running an agent.
    So the env layers are not consulted at all. The check sets the value in BOTH env layers a test
    can reach — the process environment and the repo-local `.vikunja-mcp.env` beside the toml —
    under a `VIKUNJA_`-prefixed and a bare spelling each, so whichever name a future reader might
    join in is already there to lose. The third env layer, `~/.config/vikunja-mcp/env`, is a real
    machine path and is deliberately left alone rather than pretended at; "all three at once" was
    written here and is not what this test does.
    """
    (tmp_path / ".vikunja-mcp.toml").write_text(
        '[tracker]\nurl = "https://tracker.example"\nproject_id = 10\nlanguage = "ru"\n',
        encoding="utf-8",
    )
    (tmp_path / ".vikunja-mcp.env").write_text(
        "VIKUNJA_TOKEN=t\nVIKUNJA_LANGUAGE=en\nlanguage=en\n", encoding="utf-8"
    )
    cfg = load_config(
        cwd=tmp_path,
        environ={"VIKUNJA_TOKEN": "t", "VIKUNJA_LANGUAGE": "en", "language": "en"},
    )
    assert cfg.language == "ru", (
        "an env layer overrode the committed toml. `language` is team policy on wip_limit's side "
        "of the config split, not machine-local state like worktree_root"
    )


def test_next_task_carries_the_language_beside_wip():
    """The half of the feature this tool cannot enforce: the agent has to be TOLD.

    The spec, worklog and review report are the bulk of a card's text and `workflow.py` does not
    write a character of them, so the key reaches that text only as an instruction — the payload
    below, plus the SKILL.md rule itself, which
    `test_skill_contract.py::test_the_language_rule_names_a_key_the_code_actually_emits_and_reads`
    holds against the very line asserted here. Checked on the empty-queue
    signal as well as on a task-bearing one because `with_wip` wraps both, and a payload the
    agent reads only when there is work would miss the tick where it reads the rules.
    """
    for language in LANGUAGES:
        api, wf = wf_for(language)
        assert wf.next_task()["language"] == language          # empty queue
        api.add_task("free work", "Queue")
        offered = wf.next_task()
        assert offered["task"] is not None
        assert offered["language"] == language
        assert set(offered["wip"]) == {"active", "limit", "free"}, (
            "language must ride BESIDE wip, not inside it — the hub and the rulebook both read "
            "`wip` as a fixed three-key shape"
        )


def test_the_verdict_tokens_do_not_translate():
    """`[review] APPROVE` / `[review] NEEDS WORK` are tokens, not prose, and stay out of the table.

    SKILL.md tells a reviewer that the verdict is on the first line in exactly those spellings
    (its "record the verdict IMMEDIATELY" bullet), so localising either would make the rulebook
    false for one of the two languages, and the same spellings are what anyone scanning a card by
    eye reads. It does NOT ask the orchestrator to grep for them — that claim was written here and
    withdrawn: SKILL.md routes the orchestrator's verdict signal through `review_task`'s result and
    the `reviewed`/`review-failed` labels, and the only other occurrences of `APPROVE` in that file
    are narrative about past cards. So a `ru` board still carries the English tokens — verified by
    building one, since the table is where a body would otherwise be tempted to absorb them.
    """
    api, wf = wf_for("ru")
    task = api.add_task("a card", "Queue")
    wf.claim(task["id"])
    wf.advance(task["id"], to="build", spec="подход")
    wf.advance(task["id"], to="review", worklog="сделано", evidence="deadbeef")
    wf.review_task(task["id"], verdict="needs_work", report="ещё нет")
    wf.advance(task["id"], to="review", worklog="переделано", evidence="c0ffee")
    wf.review_task(task["id"], verdict="approve", report="теперь хорошо")
    verdicts = [c for c in api.comments_text(task["id"]) if c.startswith("[review]")]
    assert [v.split("\n")[0] for v in verdicts] == ["[review] NEEDS WORK", "[review] APPROVE"]


# --- the table's own shape ------------------------------------------------------------------

def test_every_key_carries_every_language():
    """A half-filled row is the failure mode a two-column table has: `card_text` falls back to the
    default when a row is missing the requested language, which is the right runtime behaviour
    (a card comment is the wrong place to discover a typo) and exactly why the gap has to be
    caught here instead."""
    for key, row in cardtext._TABLE.items():
        assert set(row) == set(LANGUAGES), (
            f"card text {key!r} carries {sorted(row)} but the accepted set is {list(LANGUAGES)}. "
            f"card_text falls back to {DEFAULT_LANGUAGE!r} on a missing row, so a gap here ships "
            f"as one untranslated line on an otherwise translated board rather than as an error"
        )


# Latin word runs the `ru` column carries ON PURPOSE, with the reason each one is there. This is
# a RATCHET, not a description: the pin below rejects any Latin run that is NOT on this list, so a
# Russian row carrying one fails until somebody either translates it or writes it here with a
# reason. Five of the eight are board columns and stages, which the Vikunja UI itself shows
# untranslated, so a Russian card line naming them in Russian would point at something the human
# cannot find on their own board.
# WHAT THAT IS NOT, since this comment promised it until #1171: it is not "no English left
# behind". The allowlisted tokens COMPOSE into ordinary English, so a `ru` body rebuilt out of
# them alone sails through. Measured, selection `tests/unit/test_card_language.py` alone,
# `__pycache__` cleared and PYTHONDONTWRITEBYTECODE=1 per round, rounds read by counting lines
# beginning `FAILED `, control 0 failed / 0 errors / 13 collected each round: the `worklog_worklog`
# body rewritten to `Review: {worklog}` -> 0 failed; for contrast a leftover Latin `KB` unit -> 3
# failed. The first is contrived to trigger and is NOT a reason to change the allowlist, whose
# composition is honest — its eight tokens are exactly the Latin runs the `ru` column carries
# today, with no padding and no dead entries. What it is a reason to change is the sentence: the
# mechanism delivers "no Latin run outside this list", which catches text LEFT BEHIND in ordinary
# words, and that is the failure that actually happens.
_LATIN_KEPT_IN_RU = {
    "Backlog", "Build", "Done", "Queue", "Review",   # board columns / stages, shown as-is by the UI
    "Evidence",   # pre-#1164 text: this label was already English while the block around it was not
    "id",         # a field name, rendered as `id={project_id}`
    "precedes",   # the relation kind, a wire term rather than a word
}


def test_no_translated_row_leaves_an_english_word_behind():
    """A HALF-translated `ru` row is the failure that actually happens, and #1168 measured that
    nothing saw it.

    Nobody adds a key and forgets the whole Russian body — an empty column is obvious by eye, and
    the neighbouring `test_the_default_language_card_text_is_ascii` asserts the column is
    non-ASCII SOMEWHERE. What happens is a row translated except for a clause, a unit or an
    interpolated fragment, and the card then reads half-English to its human with every gate
    green.

    THE PREDICATE THE FLIP PIN ACTUALLY HAS, which is not the one it looks like it has. That pin
    compares two rendered boards, so it fires only when an untranslated row makes some whole
    comment BYTE-IDENTICAL across the flip. Measured, selection `test_card_language.py` +
    `test_card_text_is_ascii.py`, control 0 failed / 0 errors, 16 collected each round, before
    this test existed: `claim` left in English -> 1 failed, caught, because that row IS the whole
    body of its comment; `worklog_worklog` left in English -> 1 failed, caught, via the rework
    `[worklog]`, which carries no root-cause line and so becomes identical; `decompose_ordered`
    left in English ENTIRELY -> 0 failed, blind, because it is a suffix on a comment whose main
    body is still translated; `claim` left HALF translated -> 0 failed, blind. So "whole rows are
    caught, partial ones are not" is the wrong summary — a fragment row is blind either way, and
    that is what this test replaces with a per-ROW question.

    WHAT IT CATCHES NOW, same selection (`test_card_language.py` alone, which is this file's own
    sweep rule), control 0 failed / 0 errors, 13 collected each round: `claim` half-translated ->
    1 failed, this test alone; `decompose_ordered` left in English entirely -> 1 failed, this test
    alone; `claim` left in English entirely -> 2 failed, this test and the flip pin, which is the
    one shape both see. The two rounds that were blind before are the two this test is now the
    only thing catching.

    HOW, and what it costs. Strip the `{fields}`, find maximal runs of Latin letters, and require
    each to be allowlisted. It is a script check, so it says nothing about a row translated into
    fluent nonsense — it catches text LEFT BEHIND, which is the measured failure. It also assumes
    the non-default language is written in a non-Latin script; that holds for `ru` and for nothing
    else, which is why `LANGUAGES` is asserted below rather than iterated. A future Latin-script
    language needs a different pin, and should fail here first so that someone decides what.
    """
    assert set(LANGUAGES) == {"en", "ru"}, (
        f"LANGUAGES is now {list(LANGUAGES)}. This pin reads Latin letters in a translated row as "
        f"text left untranslated, which is only meaningful for a NON-LATIN-script language. A "
        f"Latin-script language needs its own completeness check — decide what it is rather than "
        f"widening the loop below, which would pass vacuously for it"
    )
    for key, row in cardtext._TABLE.items():
        body = re.sub(r"\{[^}]*\}", " ", row["ru"])
        leftover = sorted({w for w in re.findall(r"[A-Za-z]+", body)} - _LATIN_KEPT_IN_RU)
        assert not leftover, (
            f"cardtext._TABLE[{key!r}]['ru'] still carries the Latin word(s) {leftover}: "
            f"{row['ru']!r}. A row translated except for a clause ships a half-English line to a "
            f"human who set `language = \"ru\"`. The flip pin next door fires only when an "
            f"untranslated row makes a whole comment come out byte-identical across the flip, so "
            f"on a row that is one FRAGMENT of its comment this assert is the only one that "
            f"looks. Translate it, or add the word to _LATIN_KEPT_IN_RU with the reason it stays"
        )


def test_no_card_text_entry_contains_a_marker_bracket():
    """The structural half of "the marker never translates".

    The behavioural half is the flip pin above; this one removes the way the bracket could get
    in. Every value here is a BODY — the marker is a literal at its own `add_comment` call site
    in `workflow.py`, where #1164's derived scan and its `_MARKERS` list can both see it. A
    bracket appearing in this table would move a marker out of that scan's reach in the same
    edit that gave it a per-language spelling.
    """
    for key, row in cardtext._TABLE.items():
        for language, template in row.items():
            assert "[" not in template, (
                f"card text {key!r} ({language}) contains a '[': {template!r}. Markers stay as "
                f"literals at their add_comment call site in workflow.py — that is what keeps "
                f"them inside test_card_text_is_ascii.py's derived scan and its _MARKERS list, "
                f"and what keeps them from acquiring a per-language spelling here"
            )


def test_card_text_refuses_an_unknown_key_and_tolerates_an_unknown_language():
    """The two failure modes are deliberately opposite, so both are pinned.

    An unknown KEY is a programming error with no runtime input behind it — raise. An unknown
    LANGUAGE cannot originate from a config file (`load_config` refuses it by name) so it means
    a hand-built `Workflow`, and `card_text` is called mid-transition, after the board has
    already been moved: raising there would leave a card moved with no journal entry.
    """
    with pytest.raises(KeyError):
        cardtext.card_text("en", "no_such_key")
    assert cardtext.card_text("de", "filed_backlog") == cardtext.card_text("en", "filed_backlog")


def test_human_size_units_follow_the_language(tmp_path):
    """`_human_size` renders INSIDE the `[attach]` line from another function, so its units are
    card text and follow the key like every other body. Driven through `attach_file` rather than
    called directly: what makes the units card text is that they reach a comment, and a direct
    call would not measure that."""
    en_api, en_wf = wf_for("en")
    ru_api, ru_wf = wf_for("ru")
    for size in (512, 2048, 5 * 1024 * 1024):
        en_line = attach_line(en_wf, en_api, tmp_path, size)
        ru_line = attach_line(ru_wf, ru_api, tmp_path, size)
        assert marker(en_line) == marker(ru_line) == "[attach]"
        assert en_line.isascii() and not ru_line.isascii(), (
            f"the [attach] size units did not follow the language at {size} bytes: "
            f"{en_line!r} against {ru_line!r}"
        )
