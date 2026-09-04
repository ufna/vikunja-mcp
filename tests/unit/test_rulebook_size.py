"""The two rulebooks have a SIZE CEILING, and it is a ratchet like `line-length`.

WHY THIS EXISTS, measured rather than argued. Both files enter an agent's context before it
does any work: CLAUDE.md on every session in this checkout — the orchestrator's, every
per-task agent's and every reviewer's, so ~7 contexts per round at `wip_limit = 3` — and
SKILL.md on every invocation of the `tracker` skill, in this checkout AND at every consumer,
since it ships inside the wheel while CLAUDE.md does not.

Left alone they grow without bound, because this repo's own dogfood loop appends each card's
post-mortem to them. That is not a projection: CLAUDE.md measured 14 063 characters at
`909df13`, 73 983 at `23dffc8` and 155 270 at `f77977e` — roughly a doubling per week, over
eleven days. A rulebook that doubles weekly stops being read and starts being skimmed, which
is the failure mode it exists to prevent.

WHAT THE FIX WAS, so the next reader does not undo it by accident. The prose was split into a
RULES layer (these two files) and an EVIDENCE layer (`docs/dossier/*.md` for CLAUDE.md,
`src/vikunja_mcp/skills/tracker/references/*.md` for SKILL.md, both linked from the rule they
belong to). Nothing was deleted wholesale; the measurements, constructed stands and refuted
wordings moved. So the way to satisfy this gate is to move new evidence to the dossier, NOT
to compress a rule until it stops being followable.

WHAT THIS GATE IS NOT. It does not check that the split is correct, that a rule is still
stated, or that a dossier is still reachable — those are the prose pins in
test_mutation_sweep_contract.py, test_skill_contract.py and test_repo_browser_isolation.py,
which is why this file is deliberately dumb. It measures ONE thing, in CHARACTERS rather than
bytes. That choice was made when SKILL.md was majority Cyrillic and a byte count would have
reported roughly 1.6x its real size; #997 translated it, so bytes and characters have since
converged and the distinction no longer bites anywhere in the rules layer. The unit stayed
because the NEXT one is the one that matters.

THE UNIT IS A PROXY, AND KNOWING WHICH ONE MATTERS (#998). What this gate is defending is
CONTEXT, and context is priced in TOKENS. Characters stand in for tokens well enough while a
file stays in one language, and not at all across a change of language. Measured at `d3884bc`,
when this repo still held both: Cyrillic prose ran 0.44-0.47 tokens per character against
0.25-0.28 for Latin, each range being the observed spread of a NAMED population of this repo's
own markdown (7 Cyrillic files, 12 Latin) rather than an extrapolation from a sample. The
Cyrillic half of that is now HISTORY — after #997 there is not one Cyrillic markdown file left
above 2 000 characters — and the Latin half moved with the new text: 0.2380 to 0.2840 over 20
files, its new minimum being the freshly translated `references/gc-report.md`, below the whole
of the old band. Same content, hand-translated, cost 1.69x more tokens in Russian on one real
SKILL.md section and 1.63x on another; measured end to end the whole rulebook came out at
1.649x, and SKILL.md alone at 1.656x — the card had estimated 1.77x from a single
hand-written paragraph, so that estimate was ~7% optimistic.

TOKENS ARE THE THIRD UNIT, and the bytes-versus-characters argument above did not consider them
— it was correct for its own question, so do not "fix" it. The reason this gate is not simply
moved onto tokens is measured too: the only available tokenizer (`tiktoken`) fetches its BPE
table over the network from an OpenAI CDN (1.68 MB; 2.62 s cold against 0.11 s warm), which
would make `lint-and-unit` — the job that decides whether `release` runs at all — depend on a
third party's uptime, for a tokenizer that is not even the one counting here. So the proxy
stays and is LABELLED: every ceiling names the script it was derived in, and a test below fails
when a file stops matching it.

RATCHET DIRECTION. When a rulebook genuinely shrinks, lower its ceiling in the same commit —
that is the whole mechanism, and it is the same one `_HARD_LIMIT` uses in
test_line_length_gate.py. RAISING a ceiling is a decision, not a fix: it says the rules layer
itself grew, which is legitimate (a new subsystem, a new gate) and rare. If you are raising it
because a post-mortem would not fit, the post-mortem is in the wrong file. And if you are
raising it because a file was TRANSLATED, stop: re-derive the number from a token measurement
of the new text so it preserves the same budget, and move the declared script with it.
"""

import pathlib
import unicodedata

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

# path -> (ceiling in CHARACTERS, script it was derived in, what it is)
#
# The script is load-bearing, not a label: characters are a PROXY for the tokens this gate
# actually cares about, and the rate differs by language (see the script test at the bottom).
# BOTH ARE LATIN SINCE #997, so the two ceilings are finally in one currency: 3.10x apart in
# characters against 3.01x in tokens (126 000 x 0.2534 = 31 934 against 40 652 x 0.2608 =
# 10 602). They used to disagree by nearly half — 2.88x in characters was 5.12x in tokens at
# `d3884bc`, when SKILL.md was 85.6% Cyrillic by letter and CLAUDE.md 0.0%.
# BOTH HALVES MOVED TOGETHER when #1640 raised CLAUDE.md 40 000 -> 40 652, which is what this
# pair is for. The token half is arithmetic over the SAME anchored per-character rates, not a
# fresh tokenizer run: nothing about the text's script changed, only the ceiling, so re-deriving
# 40 652 x 0.2608 is exactly as valid as the 40 000 x 0.2608 it replaces — and stating that is
# the point, since a reader must be able to tell a recomputation from a re-measurement.
#
# SKILL.md's ceiling ROSE from 115 000 to 126 000 in this unit while the budget it stands for
# FELL from 53 419 tokens to 31 934, a 40.2% ratchet down in the unit that matters. That is the
# case this gate was labelled for, resolved the way it prescribes: the ceiling was derived from
# the character headroom the file always had (10 161) rather than bumped until the file fitted.
# The headroom it actually ships with is 9 959 — the translated text kept moving under later
# fixes, and this figure is the one measured LAST, immediately before the push, which is the
# only figure a reader can check. All figures here measured at `7bc02c9`+.
#
# Headroom is deliberately modest — a few thousand characters, i.e. a rule or two — because a
# generous ceiling is the same as no ceiling.
# Measured at the split: CLAUDE.md 34 574 characters (down from 155 270 at `f77977e`) and
# SKILL.md 104 596 (down from 202 619 at the same commit). SKILL.md's ceiling has more headroom
# than CLAUDE.md's for a stated reason rather than a generous one: its two universal sections —
# "Traces of the work" and "A second independent pass", needed by every agent on every task —
# were deliberately NOT moved to references, so its rules layer is genuinely larger and its next
# ratchet step is condensing those two in place, not relocating them.
_CEILINGS = {
    "CLAUDE.md": (
        # RAISED 40 000 -> 40 652 by #1640, and the increment is EXACTLY what the rule cost:
        # the file went 39 876 -> 40 528, i.e. +652, and the ceiling moved by the same 652, so
        # the headroom is 124 characters before and after. That arithmetic is the whole
        # justification, and stating it that way is this entry's own correction: the first
        # version raised the ceiling by 500 against a file that grew 461, GRANTING 39 characters
        # of budget, and then described the result as headroom "LESS than before" while writing
        # the measured pair `124 -> 163` in the same sentence — 163 is more than 124. An
        # independent review caught it. A ceiling that moves by more than the rule cost is the
        # "bumped until the file fitted" move this gate's own header rejects.
        # WHY THE RULE HAD TO GO IN AT ALL: Icebox is a new canonical stage, and the one fact a
        # reader needs BEFORE touching `_bucket` is that its presence check runs over
        # REQUIRED_STAGES — widening it back to STAGES fails every tool on every un-migrated
        # board at the next `stable` resolve. The evidence went where this gate prescribes;
        # `docs/dossier/workflow.md` gained ~14 KB on this card.
        # The honest next ratchet step is NOT this bullet: measured at the landing, the
        # Architecture bullets are `workspace_cmd.py` 4 319, `config.py` 3 624, `workflow.py`
        # 2 947 — so the workflow bullet is the THIRD longest, and the first version of this
        # comment pointed the next card at the wrong one.
        40_652, "latin",
        "the repo rulebook — read by every session in this checkout",
    ),
    "src/vikunja_mcp/skills/tracker/SKILL.md": (
        126_000, "latin",
        "the agent rulebook — ships in the wheel, so every consumer pays for it too",
    ),
}


def test_each_rulebook_stays_under_its_ceiling():
    """The gate itself. One assert per file so a red names the file that grew."""
    for relative, (ceiling, _script, what) in _CEILINGS.items():
        path = REPO_ROOT / relative
        assert path.is_file(), (
            f"{relative} is gone from the repo — this gate has nothing to measure. If it moved, "
            f"move this entry; do not delete the check"
        )
        size = len(path.read_text(encoding="utf-8"))
        assert size <= ceiling, (
            f"{relative} is {size} characters, over its ceiling of {ceiling} ({what}). This file "
            f"is in an agent's context before it does any work, so growth here is paid on every "
            f"session. Move the new prose to its evidence layer — docs/dossier/ for CLAUDE.md, "
            f"skills/tracker/references/ for SKILL.md — and link it from the rule it belongs to. "
            f"Raise the ceiling only if the RULES layer itself genuinely grew, and say so in the "
            f"commit message"
        )


def test_the_two_rulebooks_are_in_the_same_script_so_their_ceilings_compare():
    """WHAT THIS TEST USED TO BE, and why it changed rather than went away (#997).

    It used to assert the OPPOSITE fact: that SKILL.md was majority Cyrillic, where UTF-8
    spends two bytes per character, so bytes reported ~1.6x its real size and a gate written in
    bytes would have been far tighter on SKILL.md than on CLAUDE.md for no reason anyone chose.
    That argument was correct and is now HISTORY: #997 translated SKILL.md and every reference,
    so the byte/character distinction no longer bites anywhere in the rules layer. Its failure
    message said "either the file was translated — then rewrite that paragraph". It was, so
    this is that rewrite.

    WHAT REPLACES IT IS THE FACT THE TRANSLATION CREATED. The two ceilings used to be quoted in
    different currencies — measured at `d3884bc`, 2.88x apart in characters and 5.12x in
    tokens, because Cyrillic costs roughly 0.46 tokens per character against 0.25 for Latin.
    Now both files are Latin, the two units nearly agree (3.10x in characters against ~3.01x in
    tokens), and the character ceiling finally means what it appears to mean. That is worth a
    gate rather than a sentence: if one rulebook drifts back into another script, the pair stops
    being comparable and the neighbouring assert on their ratio silently starts measuring
    nothing. The script test below catches the drift per file; this one catches the pair.
    """
    for relative, (_ceiling, script, _what) in _CEILINGS.items():
        assert script == "latin", (
            f"{relative} declares script {script!r} while the other rulebook is Latin. The "
            f"ceilings are then quoted in different currencies again — 2.88x apart in "
            f"characters was 5.12x in tokens the last time that was true — and the ratio "
            f"asserted next door stops meaning anything. Re-derive both from token "
            f"measurements before letting them diverge"
        )

    skill = REPO_ROOT / "src/vikunja_mcp/skills/tracker/SKILL.md"
    text = skill.read_text(encoding="utf-8")
    assert len(text.encode("utf-8")) < len(text) * 1.05, (
        "SKILL.md has drifted back to majority non-ASCII while its entry still says `latin`. "
        "Bytes and characters have separated again, which means its per-character token rate "
        "has too — and the ceiling above was derived at the Latin rate, so it is now quoting a "
        "budget the file no longer costs. Re-derive it from a token measurement of the new text "
        "and move the declared script with it; never bump the number to fit"
    )


def test_every_rulebook_points_at_an_evidence_layer_that_exists():
    """A ceiling only works if there is somewhere for the evidence to go.

    Pinned because the failure is silent in exactly the wrong direction: with the dossier
    directory gone or renamed, every `→ Dossier: …` pointer in CLAUDE.md becomes a dead end, the
    next agent writes its post-mortem back into the rulebook because that is the only place left,
    and the gate above then reads as an obstacle rather than as a routing rule. Checks the
    DIRECTORIES rather than each link, because the links themselves are prose and move with the
    text; what must not vanish is the destination.
    """
    for relative in ("docs/dossier", "src/vikunja_mcp/skills/tracker/references"):
        directory = REPO_ROOT / relative
        assert directory.is_dir(), (
            f"{relative} does not exist, so the evidence layer this repo's rulebooks route their "
            f"post-mortems into is gone. The size ceiling next door assumes it is there: without "
            f"it there is nowhere to move prose to, and the ceiling becomes pressure to delete "
            f"measurements instead of relocating them"
        )
        assert any(directory.glob("*.md")), (
            f"{relative} exists but holds no markdown, which is the same failure one step later"
        )


def _cyrillic_share_of_letters(text: str) -> float:
    """What fraction of the letters are Cyrillic. Letters only, because markdown is full of
    punctuation, code spans and backticks that belong to no language."""
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return 0.0
    cyrillic = sum(1 for c in letters if "CYRILLIC" in unicodedata.name(c, ""))
    return cyrillic / len(letters)


# Which script each ceiling was derived in. NOT decoration: see the test below.
_EXPECTED_SHARE = {"cyrillic": (0.5, 1.0), "latin": (0.0, 0.1)}


def test_each_ceiling_declares_the_script_it_was_derived_in():
    """THE UNIT IS A PROXY, AND A PROXY IS ONLY VALID FOR THE TEXT IT WAS CALIBRATED ON.

    This gate exists to bound what these files cost in an agent's CONTEXT, and that cost is
    counted in TOKENS. It counts CHARACTERS. While a file stays in one language the two move
    together and nothing is lost — but the conversion rate between them is language-dependent,
    so a ceiling means something different the moment the language changes.

    Measured (tiktoken cl100k_base, which is OpenAI's tokenizer — a PROXY for the one that
    actually counts here, so read the RATIO and not the absolutes): Cyrillic prose runs about
    0.44-0.47 tokens per character (0.4431 for skills/tracker/references/gc-report.md to 0.4709
    for references/review.md, over the 7 Cyrillic markdown files this repo tracks); Latin
    0.25-0.28 (0.2487 for docs/dossier/browser.md to 0.2834 for docs/dossier/config.md, over
    all 12). EACH RANGE IS THE OBSERVED SPREAD OF A NAMED POPULATION — that phrasing is the
    correction itself, see below — and every tree-derived figure here was measured at
    `d3884bc`. Same content translated by hand: a real SKILL.md section came out
    1.69x cheaper in English, and an independent reviewer's translation of a different section
    1.63x. Those two are properties of a SAMPLE, not of the tree, so they carry no anchor and
    should not be treated as constants. English is therefore slightly LONGER in characters
    (1.07x on that section) and distinctly cheaper in tokens.

    THE TOKEN FIGURES ARE NOT GATED — and that is narrower than "none of them is", which is what
    round 2 wrote. What genuinely cannot be checked here is anything needing a tokenizer: the
    per-character rates and the 5.12x that follows from them, because the only available
    tokenizer fetches over the network and this file refuses to put that in CI (see the module
    docstring). For those, the sha anchor is the substitute the repo already has — it does not
    re-derive the number, it guarantees the tree is named and reachable
    (test_measured_figure_anchors.py). The rest is checked rather than trusted: the Cyrillic
    SHARES are what this test computes itself, and 2.88x is plain arithmetic over `_CEILINGS`,
    so it is asserted below instead of quoted — CLAUDE.md's rule that a figure a reader will ACT
    on should be an assert, applied to the one figure here that can be.

    NAME THE POPULATION OR DO NOT WRITE THE RANGE. This took THREE rounds, each failing the same
    way, which is why the rule is stated rather than the numbers merely fixed:

    * Round 1 wrote 0.46 and 0.23 and derived "5.8x" from them while claiming to use "each
      file's own measured rate" — self-contradictory, since those rates are 0.4645 and 0.2608
      and give 5.12x. Worse, 0.23 came from ONE hand-written paragraph and sits BELOW every
      Latin file here.
    * Round 2 fixed Latin against all 12 files and then invented "0.46-0.48" for Cyrillic out
      of two samples rounded outward. Measured over the 7 real files it is 0.4431-0.4709: 0.48
      is above every one of them and 0.46 cuts the minimum off. Committed two paragraphs after
      the words DO NOT ROUND THESE.

    Both errors point the SAME way — a low Latin rate or a high Cyrillic one both inflate the
    budget, and this test's refusal PRESCRIBES re-deriving a ceiling from them, so each would
    have handed the reader a procedure that quietly loosened the gate (7-13% and 3.3%). A range
    invented from samples is not a conservative estimate; it is an unmeasured claim wearing the
    costume of one.

    Two consequences, and the second is why this test exists rather than a paragraph:

    * The two ceilings below are not comparable to each other. In characters SKILL.md's is 2.88x
      CLAUDE.md's; converted at each file's own measured rate (0.4645, 0.2608) it is 5.12x — so
      the gate understates, by nearly half, how much more context SKILL.md is allowed to cost.
    * Translating a rulebook would push it AGAINST its ceiling while cutting the cost the
      ceiling exists to control. A ceiling raised at that moment "because we hit it" would be
      the gate defeating its own purpose. Re-DERIVE it from a token measurement of the new
      text instead.

    So each entry names its script, and this test fails when the file stops matching it.

    WHY THIS IS NOT THE NEIGHBOURING TEST WITH A LONGER MESSAGE. On a COMPLETE translation both
    go red, so the question is what each catches alone; built and measured on this file rather
    than reasoned. Translating SKILL.md 45% of the way — the realistic shape, since a 105 000-
    character rulebook gets translated section by section — leaves bytes/characters at 1.36,
    still above the 1.3 the neighbour tests, so ONLY this test fires; at 70% (ratio 1.20) both
    do. And turning CLAUDE.md Cyrillic fires only this one, because the neighbour reads SKILL.md
    and nothing else. The window where a rulebook has changed language enough to invalidate its
    ceiling but not enough to move a byte ratio is exactly where a ceiling gets bumped "to fit".

    MUTATION SWEEP, this file as the whole selection so no collateral can stand in for it,
    `__pycache__` cleared and PYTHONDONTWRITEBYTECODE=1 each round, rounds read by COUNTING
    lines beginning `FAILED ` and naming which: control (opening) 0 failed, 0 errors, collected
    4; SKILL.md fully translated with the band intact -> 2 failed (this test AND the byte one);
    the same translation with the cyrillic band widened to 0..1 -> 1 failed (only the byte one),
    which is the pair proving the BAND is what catches it and not something else in the file;
    the band widened with no translation -> 0 failed, correctly, since there is nothing to
    catch; the declared script dropped from the entries -> 2 failed; control (closing, restored)
    0 failed, 0 errors, collected 4.

    The 2.88x assert was swept separately, same discipline, same control: control 0 failed,
    collected 4; CLAUDE.md's ceiling raised 40 000 -> 50 000 -> 1 failed, collected 4; control
    0 failed. Deleting the assert itself was also run and gives 0 failed — recorded because it
    is DEGENERATE rather than informative: removing an assertion cannot fail the test that
    holds it, so that round is not evidence of anything and the raise above is.
    """
    # The one figure in the prose above that needs no tokenizer, so it is derived rather than
    # quoted: if the ceilings move, this recomputes instead of going stale, and the docstring's
    # "2.88x in characters against 5.12x in tokens" cannot silently drift on its character half.
    ratio = (
        _CEILINGS["src/vikunja_mcp/skills/tracker/SKILL.md"][0] / _CEILINGS["CLAUDE.md"][0]
    )
    assert abs(ratio - 3.10) < 0.01, (
        f"the ceilings are now {ratio:.2f}x apart in characters, not the 3.10x this test's "
        f"docstring quotes beside its ~3.01x in tokens. Re-measure the token side before "
        f"editing the prose: the whole point of the pair is that the two units disagree, and "
        f"updating only the half that needs no tokenizer would hide exactly that"
    )

    for relative, entry in _CEILINGS.items():
        assert len(entry) == 3, (
            f"the ceiling for {relative} does not declare the script it was derived in. "
            f"Characters are a PROXY for tokens and the rate is language-dependent, so a bare "
            f"number cannot say what it is worth. Entries are (ceiling, script, what)"
        )
        ceiling, script, _what = entry
        assert script in _EXPECTED_SHARE, (
            f"{relative} declares an unknown script {script!r}; known: {sorted(_EXPECTED_SHARE)}"
        )
        low, high = _EXPECTED_SHARE[script]
        share = _cyrillic_share_of_letters(
            (REPO_ROOT / relative).read_text(encoding="utf-8")
        )
        assert low <= share <= high, (
            f"{relative} is {share:.0%} Cyrillic by letter, which is outside the {low:.0%}-"
            f"{high:.0%} band its declared script {script!r} implies — the file changed "
            f"language while its ceiling of {ceiling} characters did not. DO NOT simply raise "
            f"or lower that number to fit. The ceiling bounds CONTEXT COST, which is counted "
            f"in tokens, and characters only stand in for tokens at a rate that depends on the "
            f"language: 0.44-0.47 tokens/character for Cyrillic against 0.25-0.28 for Latin "
            f"(the observed spread over this repo's own markdown, 7 Cyrillic files and 12 "
            f"Latin — measure your new text, do not widen these outward from a sample: a low "
            f"Latin rate or a high Cyrillic one both yield a more generous ceiling). "
            f"Re-derive the ceiling from a token measurement of the NEW text so it preserves "
            f"the same budget, then update the declared script in the same commit"
        )
