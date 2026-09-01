"""A prose claim that quotes a string as being IN this repo, made checkable — tracker #695.

WHAT IS BROKEN WITHOUT THIS FILE. This repo's prose quotes itself constantly, and until now
nothing anywhere checked that a quoted string a sentence asserts is IN the checkout actually is.
The failure is not hypothetical and it is not rare. VMCP-153 (656)'s own correcting commit
`889befd`, whose subject ends "correct six measured claims (tracker #656)", introduced a seventh:
a comment claiming two example strings were what this repo held, "each in test_api_kanban.py",
where the first of the two occurred nowhere outside the file making the claim. It shipped through
CI, through review, and past the sweep scanner whose own pattern is defined thirteen lines below
that comment.

THE FIRST DRAFT OF THAT SENTENCE MISQUOTED IT, and the slip is left recorded rather than quietly
fixed, because it prices the whole exercise. It read `titled "corrects six measured claims"` — one
letter, written from memory, describing a string no commit of this repository has ever carried.
Nothing here would have caught it either: a commit SUBJECT is not carried by `git ls-files`, so
the corpus this scanner searches has never held it. Two things at once, then — that the class is
easy to commit even while writing the guard against it, and that this guard's reach ends at the
working tree. (Note the tense. That misquotation is now in the tree exactly once, in THIS file,
put there by the sentence retracting it — so "a string this repo does not contain", which is what
this paragraph used to say, stopped being true the moment it was written down. The scanner cannot
see it, because its own source is out of the corpus for every claimant. A retraction reproduces
what it retracts: that is the second grandfathered entry's whole reason, met here in the prose.)

AND IT SHIPS GREEN, which is the card. Re-measured before this file existed, in a clone taken at
`d1af833` — a release bump on the main line, so it is re-checkoutable rather than a deleted wip
commit — with `__pycache__` deleted and then `PYTHONDONTWRITEBYTECODE=1`, selection `tests/unit`,
`collected 890 items` every round: control 0 failed; a fabricated repo-content quotation planted
in a COMMENT run 0 failed; the same planted in a DOCSTRING 0 failed; both at once 0 failed. Four
greens, one of them the control. This card's independent reviewer read that anchor as unreachable
and reproduced at `cb57cd3` instead, with different phrases, `collected 894 items` and the same
four greens. Both collection lines were re-run here rather than copied — 890 and 894, on clones of
the two commits — because the whole subject of this file is a figure nobody re-ran; the line moves
with every sibling landing and the result does not.

WHY THE OBVIOUS RULE IS NOT THE RULE. "Every quoted string in prose must be found in the tree" was
measured before it was rejected — through THIS file's own extraction and oracle, with the trigger,
BOTH floors and the elision exemption switched off. That last one is named because leaving it on
gives a different pair, and only this spelling is checkable against shipped code: the quotation
total it produces is the same `all_quotations` that
`test_the_claim_keyed_rule_still_looks_at_a_tiny_fraction_of_the_quotations` counts, so the ruler
is a line you can run rather than a description you have to trust. It gives 2,993 violations out
of 11,352 quotations at `3937b45`, the commit that first landed this file, and 3,068 out of 11,596
at `4c61283`, three card landings later — both anchors name the tree the figure
was taken on, which for a figure that counts this file's own prose can only ever be a COMMITTED
one, never the tree being written. Against those, the shipped rule asks about 14 on both. Only
CARD landings move these: re-measured at the release bump sitting directly on `4c61283`, every
figure in this docstring is unchanged, which is why the counting below is in landings and not in
commits.
The first round of this card published 2,975 / 11,299 / 15 for the
first of those two shas and none of the three was right there — that is the bounce this paragraph
is the fix for, and it is left recorded because a file about false quotations shipping false
figures is the cheapest possible demonstration that measuring and RE-measuring are different acts.
Those digits perish on a schedule: THREE card landings and three release bumps separate the two
shas above, and every landing moved both columns,
which is why what a reader acts on is the RATIO and is asserted rather than written down. Red on
arrival and mostly wrong, because this repo's prose quotes things that are deliberately NOT repo
strings — constructed mutants pinned at a regex's edges, hypothetical banners, error text from
docker and git, quotations of OTHER repositories and of card descriptions, and wordings quoted
BECAUSE they were just retracted. The rule cannot be about the quotation. It has to be about the
CLAIM.

WHAT THIS FILE ENFORCES. A short, named list of ASSERTIVE IDIOMS — `_CLAIM_TRIGGERS` — turns the
sentence carrying one into a claim about repo content, and every quotation in that sentence must
then occur somewhere in the tree OUTSIDE THE FILE doing the claiming. Whitespace is flattened on
both sides, and that is load-bearing rather than tidy — held by a ROUND and not by a count.
Against a control of 0 failed, searching the corpus RAW so that a line wrap in the tree is no
longer bridged is 1 failed: one true claim in this tree points at a phrase that is wrapped across
a line break at its site in test_api_kanban.py, and unbridged it reads as a fabrication. Two
neighbouring rounds say WHICH half does that work, and both are 0 failed against the same control:
making `_flat` a no-op, and dropping the inner `.split()` from both flatteners. The bridging comes
from joining STRIPPED LINES with a single space, not from collapsing runs of spaces — a
distinction worth having before anyone simplifies either function. (The history of that pair is
worth two sentences, because the CORRECTION was the error. A first draft carried a
raw-0/flattened-1 pair inherited from the card and never re-ran it; measured through this file's
own oracle, which subtracts the claiming file, that pair is RIGHT — raw 0, flattened 1. A second
draft "corrected" it by switching to an UPPER-CASE spelling and reporting raw 1, which is what a
whole-corpus grep says while counting the claim's own copy, the one thing `_occurs_elsewhere`
exists to discount. And the upper-case spelling does not exist: `git log -S --all` finds it in no
commit on any ref. So an un-re-run figure that happened to be true was replaced by a re-run one
that was false, on the wrong spelling and with the wrong oracle. Re-measuring is not enough; the
ruler has to be the one the claim is about.) The corpus is
what `git ls-files` carries, so a stray untracked file in somebody's checkout cannot vouch for a
fabrication — the same hole VMCP-194 (724) closed on the markdown side of the sibling scanner,
where any `.md` in any checkout silenced an assert.

THE FILE IS THE UNIT AND THE PARAGRAPH WAS TRIED FIRST — the sharpest thing measured here, and it
inverts what the obvious design gives you. This scanner's first shape excluded only the claiming
PARAGRAPH, and it would have shipped green through `889befd`: the fabricated phrase occurred twice
in that one file, at line 88 in the sentence asserting it and at line 336 as a constructed row of
a test, so the phantom vouched for itself and the check on the founding defect was a false green.
A file arguing about a phrase quotes the phrase — that is what arguing about it consists of — so
the file discussing a phantom is the likeliest place on earth to hold a second copy. Measured
at `4c61283`, moving the unit from paragraph to file costs exactly ONE extra ratchet
entry, and that entry is that same phantom. `_occurs_elsewhere` carries the argument; the
two-copies row of `test_a_claim_is_never_evidence_for_itself` carries the pin.

WHERE THE TRIGGER LIST STOPS, and it is a measurement rather than taste. Taken by driving this
file's own predicate with nothing swapped but `_CLAIM_TRIGGERS` — ten lines, so RE-RUN it rather
than trusting the table:
  * the containment family alone ..................... 4 sentences fire, 2 unverifiable
  * plus `verbatim in` and `word for word` .......... 19 sentences fire, 3 unverifiable
  * plus `occurs in` / `appears in` ................. 35 sentences fire, 7 unverifiable
  * plus `the exact string/phrase/wording` .......... 37 sentences fire, 8 unverifiable
This table is steadier than the naive pair above it: every one of its eight figures is IDENTICAL
at `3937b45` and at `4c61283`, three card landings apart, while the naive violation count moved
in both columns over the same span. An earlier version read 5 / 19 / 38 / 40, and where those
three came from is worth naming precisely, because the obvious answer is wrong: run with the scan
restricted the way the `SELF` bug restricted it (see `_tracked_names`) the same ladder is
2 / 7 / 20 / 21, so they are not pre-fix values either. They match no state this repository ever
committed. So re-run it, but expect the SHAPE to hold. And read it knowing WHAT it counts: this
file is inside its own scan, so the table partly measures itself. Two of row 1's four firing
sentences are this file's own mutation-round bullets; row 4's single extra unverifiable IS the
name of row 4. That is not a flaw to remove — a gate that could not see its own documentation is
the `SELF` bug — but a reader should not take these as facts about the rest of the repo.
The four extra false reds the third row buys are all one defect — the trigger is incidental
English, not an assertion: a hypothetical assert written as `"decompose" in section`, a paraphrase
of a pin that was rejected, and TWO sentences of this gate's own documentation naming the spelling
it deliberately does NOT read — one in the bullets below, one in CLAUDE.md. Those two are the
sharpest argument against widening, because they are self-inflicted: adding `appears in` would
redden the very paragraphs explaining why `appears in` is excluded. So the list stops at the
second row, and the three survivors there are grandfathered by name in
`UNVERIFIABLE_QUOTATION_CLAIMS` with a reason each. Widening it later is a decision with a price,
and the price is written down.

WHAT WIDENING DID NOT COST is measured the same way, because "we widened it" is worthless without
a number beside it. The independent second pass constructed sixteen fabrications this gate shipped
green; TEN of them are now red. The trigger grew an adverb slot, the tree and the checkout as
subjects, a passive form of the same verb, the locative twin of the verbatim idiom, and the
hyphenated spelling of the word-for-word one; the scan grew to every tracked `.py` plus this file
itself; the delimiters grew a curly pair. All of it at a measured cost of ZERO false reds — the
offender set stood at the same three entries before and after every step. SIX of the sixteen are
still green and each is named in the bullets below: a single-quoted fabrication, one written after
code on a line, one longer than the quotation cap, and THREE further spellings of the idiom, which
is where no list of spellings ever ends. That is why those were taken and single quotes were not.

AND THIS PARAGRAPH IS WHY THE LIST IS NAMED DESCRIPTIVELY RATHER THAN QUOTED. Its first draft put
the new idioms in backticks; the gate went red on itself, because a sentence that both carries a
trigger and quotes one is a claim about a string that lives only in a regex. The scanner cannot
tell an example from an assertion, so prose about the trigger list must not quote the trigger list.
Measured, not reasoned: control 0 failed, that draft 1 failed, naming this very paragraph.

WHAT IT CANNOT ENFORCE, priced rather than rounded up, because a "does not catch" section that
oversells is worse than none.
  * IT IS KEYED ON A PHRASE LIST, SO IT REPORTS ON THE PHRASE LIST. VMCP-155 (660) put that
    exactly: a sweep bounded by a regex reports on the regex, not on the class. A fabrication
    written "the phrase X appears in test_api_kanban.py" is invisible here, and that spelling is
    OUT on purpose — including THAT SPELLING costs TWO false reds at `4c61283`, and this
    sentence is one of the two: the other is CLAUDE.md saying the same thing. Do not read the
    table's row-3 figure of four here; that row adds `occurs in` as well, which brings two more
    of its own. An earlier draft of this bullet said three, and the round-2 correction of it
    first said four — one figure describing the row, borrowed for a sentence about one spelling
    of it. This
    is the whole reason CLAUDE.md now names the checked idioms: the gate is only as wide as the
    author's vocabulary overlaps it, so the vocabulary is written down where authors read. The
    independent second pass built eight further spellings that ship green, and the trigger was
    widened to catch what could be caught for free — an adverb slot, `the tree`/`the checkout`
    as subjects, and the hyphenated form — at a measured cost of ZERO false reds. What is still
    open needs no list to describe: any verb that is not `contain`, and any sentence that asserts
    the location without asserting containment.
  * THE DELIMITER SET IS FOUR, NOT ALL OF THEM, and straight single quotes are the named miss.
    Measured, reading `'…'` costs ONE false red on the spot, because an English possessive opens a
    span that closes at the next apostrophe, and the scanned prose holds 773 multi-word spans of
    that shape to draw more from — that population is what makes one-today a bad bet, and it moved
    from 767 at `3937b45`, three card landings ago. So a fabrication in single quotes is
    invisible. Constructed and confirmed by the second pass, along with the shape below. (An
    earlier draft of this bullet said TWO false reds, which OVERSTATED the cost of the option
    being rejected — the one direction an argument must never lean.)
  * A QUOTATION LONGER THAN `_QUOTATION`'s 300-CHARACTER CAP IS INVISIBLE. The cap keeps a span
    from running the length of a flattened paragraph; a 343-character fabrication was constructed
    against it and shipped green. Nothing here bounds how long a quoted phrase may be, so this is
    a hole with a number rather than a judgement.
  * IT READS COMMENT RUNS, NOT TRAILING COMMENTS. `_comment_runs` collects lines that BEGIN with
    `#`; a claim written after code on the same line is never extracted, and the scanned scope
    holds 731 such lines across 40 files — counted with `tokenize`, which is also what reading
    them would need, since a `#` inside a string literal is not a comment and the naive line test
    answers 1313 in 46 files instead. The line count moves (723 at `3937b45`); the file count 40
    has not.
    Deliberately not done here, and named because the number is large enough that "comments are
    covered" would be false.
  * IT CHECKS PRESENCE, NEVER MEANING. VMCP-194 (724)'s defect passes it untouched: a string that
    IS in CLAUDE.md, quoted accurately, and glossed as agreeing with a conclusion the sentence it
    comes from contradicts. Every character verified, the claim still false.
  * IT CHECKS PRESENCE, NEVER LOCATION. The sentence may name a file; this file does not read it.
    The `889befd` defect is caught because that phrase was nowhere at all, not because the scanner
    knew where test_api_kanban.py was. Right string, wrong file is invisible. Deliberate: parsing
    the location out of a sentence is a second trigger list with a second false-red budget, and
    the measurement that would justify it has not been made.
  * IT READS QUOTATIONS, NEVER POINTERS. A bare `:1473`, a `VMCP-N (id)` pair, a sha, a line
    number — none of them is a quoted string, so none of them is in scope. That is the class of
    VMCP-155 (660) and VMCP-198 (735); 735's remedy was to make the tool hand back the ref, which
    is a different kind of fix from a scanner. THE SENTENCE ABOUT THIS SCANNER IS UNCHANGED and
    the class no longer stays wholly open: VMCP-195 (732) added a SECOND, separate gate at the
    foot of this file, and the split is worth stating precisely because it is easy to overclaim.
    That gate never asks whether a pointer is RIGHT — a checkout has no tracker to ask — it asks
    only whether the tree is self-CONSISTENT, since one id has one ref and one ref has one id.
    A pair fabricated the SAME way at every site is still invisible, and a card mentioned exactly
    once has nothing to disagree with. What is closed is the shape #660 actually shipped, where
    the composed ref sat beside the correct one. Anchors are the third pointer kind and belong to
    test_measured_figure_anchors, which resolves them against git; line numbers remain wholly
    unguarded, and nothing here changes that.
  * THE CORPUS IS THE WORKING TREE AND NOTHING ELSE. Commit messages, card descriptions, review
    comments, another repository — a claim quoting any of them is unanswerable here, and one of
    the three grandfathered entries is exactly that (a heading out of a Vikunja card). The
    misquotation recorded at the top of this docstring is the same boundary met from inside, and
    so is the wrong triple: `3937b45`'s own message states it in prose, that message is on the
    main line and released, and NOTHING can correct it in place — not this scanner, which never
    reads it, and not an edit, which would rewrite published history. A figure written into a
    commit message is write-once. Prefer the file.
  * THREE SHAPES ARE SKIPPED BY CONSTRUCTION, and all three ARE held by a round — which this
    bullet denied until now, in the file's fifth and last stale-figure defect. A quotation
    carrying an ellipsis or an `<angle placeholder>` cannot be looked up verbatim, and removing
    that exemption is 2 failed, one of them a true elided claim in test_skill_contract.py it
    spares. Dropping `_MIN_CHARS` to 4 is 2 failed. Dropping `_MIN_WORDS` to 1 is 1 failed
    against a control of 0 failed, re-run for this correction on the file alone, `collected 14
    items`, restored to a byte-identical sha256 and back to the control: it pulls one single-word
    backticked identifier into a claim sentence of this file's own screen
    docstring, so the ratchet gains an entry. This bullet said "held by NOTHING ... 0 failed
    everywhere" while TWO sibling docstrings in this same file recorded the 1 failed and even
    explained WHY it changed — the scan widened. Under the scan as the `SELF` bug left it the
    round really is 0 failed, so this was one more figure taken before the fix and never re-run,
    and the one that survived longest because it agreed with an earlier true statement. All three
    are still holes an author could hide a fabrication in; none of them is unheld.
  * A CLAIM SPLIT FROM ITS QUOTATION BY A SENTENCE BOUNDARY IS MISSED. The scope is one sentence,
    split on a terminator followed by an opening character, which is what keeps `0.0.0.0:3456`
    and `test_api_kanban.py.` from splitting. Write the trigger in one sentence and the
    quotations in the next and nothing fires. And the split is CRUDER than "sentence": the
    terminator class holds `;`, so one English sentence carrying a semicolon between the trigger
    and its quotation is cut in half and misses. Constructed by the second pass.
  * A TRUE CLAIM ABOUT A FILE NOT YET STAGED READS AS FALSE. The corpus is `git ls-files`, i.e.
    the INDEX, so a new file written but not `git add`ed is invisible: the same bytes on disk go
    from red to green on the `add` alone. Measured. This repo's flow is write, run `pytest`,
    commit — so that red lands on the ordinary path, and the fix is to stage the file, not to
    reword the sentence. It is the price of the untracked-file hole being closed in the other
    direction, and both directions are now stated.
  * NO FILE ANYWHERE CAN CITE A STRING WHOSE ONLY HOME IS THIS SCANNER. The corpus excludes this
    file for EVERY claimant, not only for claims written in it, so CLAUDE.md quoting one of the
    assert messages below goes red. Measured. That is a structurally larger cost than the
    same-file case listed above, it is live today, and the remedy is to quote something else or
    to add a ratchet entry.
  * A CODE FENCE IS NOT A FENCE HERE. `_paragraphs` reads markdown as blank-line-separated text,
    so a fenced example showing what the gate CATCHES — the natural way to document it — is read
    as a claim and goes red. Measured on CLAUDE.md. Documenting the rule by example is therefore
    not free, which is worth knowing before someone tries.
  * A STRING THIS REPO EMITS BUT NEVER STORES CONTIGUOUSLY IS UNVERIFIABLE. Most agent-facing copy
    here is assembled from adjacent literals with escaped quotes, so the runtime message exists in
    no file as one span; quoting it word for word is red even though the quotation is exactly
    right. Measured by the second pass on `next_task`'s WIP refusal.
  * IT ANSWERS ABOUT THE TREE, NOT ABOUT HISTORY. A wording that WAS in the repo and was removed
    reads exactly like one that never existed — that is the second grandfathered entry, and
    VMCP-195 (732)'s independent check ran into the same wall from the other side.
  * A PHANTOM QUOTED IN A SECOND FILE STILL VERIFIES. The file unit fixes self-corroboration
    within one file and stops there: two files discussing the same fabricated phrase are, to this
    scanner, a repository that carries it. That is the residual of exactly the mode 732's check
    hit, moved one level up rather than closed, and nothing lexical can close it — a copy made to
    discuss a phrase and a copy that IS the phrase are the same bytes.
  * THE FILE UNIT COSTS A REAL CASE, not only a theoretical one: a claim about a string that
    genuinely lives elsewhere in its OWN file cannot verify and must either point at another file
    or be named in the ratchet. Measured at `4c61283` — the paragraph unit and the file
    unit differ by exactly one entry, and that entry is the phantom — this repo holds no such
    claim, so the cost today is zero. "Zero today" is a fact about this tree, not a property.
  * THE `git ls-files` CORPUS IS NOT PINNED BY A ROUND. Against a control of 0 failed on this
    file, widening it to include untracked files is 0 failed: nothing in the tests notices,
    because the hole it opens needs somebody's dirty checkout to be visible at all. Constructing
    that state means writing a file into the repository root while sibling agents run there,
    which costs more than the pin is worth; it is recorded here instead of asserted, and the
    reasoning for the choice lives in `_tracked_text_files`.
  * NEITHER IS THE `.py`/`.md` EXTENSION SPLIT. Prose in any other tracked text — a `.toml`, a
    `.yml`, a `.sh` — is not scanned at all, and nothing here says so by failing.
"""
import ast
import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SELF = Path(__file__).resolve()

# The probe is a FILESYSTEM fact, exactly as `test_repo_browser_isolation` argues at length: a
# missing `.git` means "not a checkout, the property does not apply", while git EXITING NON-ZERO
# inside a real checkout is a failure this file reports rather than skips. Reading the exit code
# for the skip is what would turn the skip into an off-switch, so it is never read for that.
_IS_GIT_CHECKOUT = (REPO_ROOT / ".git").exists()

requires_git_checkout = pytest.mark.skipif(
    not _IS_GIT_CHECKOUT,
    reason=(
        f"{REPO_ROOT} has no .git, so the set of files this repository CARRIES is unknowable "
        "here (a `git archive` extraction, a copied tree). 'Is this string in the repo' has no "
        "answer without it: the property is NOT APPLICABLE, not broken"
    ),
)


def _ls_files_failed(stderr: str) -> str:
    """The message an agent actually reads when `git ls-files` dies, branched on WHICH death.

    It used to open `failed inside a checkout that has a .git` in both cases, and that clause is
    false in exactly the stand where this fires most often (#1462): CLAUDE.md prescribed
    `git archive` for sweep trees until that card, an extraction carries no `.git` at all, and
    the reader was told the opposite of its own problem while the fix — build the stand
    differently — was named nowhere. Eleven items in this module report through here, so the
    wording is the whole diagnosis for the agent that hits it.

    The branch reads `_IS_GIT_CHECKOUT`, i.e. the same FILESYSTEM probe the skip above uses, and
    never git's exit code. That is the same separation the marker argues for one comment up: if
    the exit code decided, "no repository" and "git is broken" would collapse into one answer
    again, and this message exists to keep them apart.

    Message text only — no test changes its verdict because of this, and NOTHING here is
    decorated with the marker that would make a repo-less tree quiet. That is deliberate and is
    the point of the card: a repo-less stand must stay LOUD, because the alternative is 11 more
    items moving into the skip column, where `collected` cannot see them.
    """
    if not _IS_GIT_CHECKOUT:
        return (
            f"`git ls-files` failed and {REPO_ROOT} has no .git ({stderr}), so this tree is not "
            "a repository — a `git archive` extraction, an unpacked sdist, a copied tree. That "
            "is a broken STAND, not a prose defect: build a sweep tree with "
            "`git clone --no-hardlinks`, which CLAUDE.md's testing section prescribes and which "
            "keeps `.venv` out for free"
        )
    return (
        f"`git ls-files` failed inside a checkout that has a .git ({stderr}). The corpus this "
        "scanner searches is undefined, so every claim would read as unverifiable — that is a "
        "broken checkout, not a prose defect"
    )

# THE ASSERTIVE IDIOMS. A sentence containing one of these is read as claiming that the strings it
# quotes are in this checkout. Each is here because it is an ASSERTION about content rather than
# ordinary English about location — see the measurement in the module docstring for what the next
# two candidates cost. `verbatim in` is deliberately the two-word form, and the reason is now
# priced in the SAME unit as every other row of that table instead of in a ratio: dropping to the
# bare word costs FOUR false reds, measured at `4c61283` by widening this pattern and re-running
# the predicate. They are a markdown blockquote whose `>` markers make it unfindable, a git
# command fragment, a wording quoted because it was REJECTED, and — self-inflicted again — a
# scare-quoted phrase in `_tracked_text_files` right below. Four is the same price the
# `occurs in` / `appears in` row carries, which is what makes the two decisions consistent rather
# than a matter of taste.
# THE SENTENCE COUNTS ARE A DESCRIPTION, NOT THE ARGUMENT, and this is where an earlier version of
# this comment went wrong twice over. It read "45 sentences against this form's 3 ... the RATIO is
# the argument, and it is an order of magnitude". Measured, the pair is 57 against 10 at `4c61283`
# and 56 against 10 at `3937b45` — 5.7x, not an order of magnitude — while 45-against-3 is the
# EXACT output of the scan before it could read this file (see `_tracked_names`), i.e. a figure
# taken before a fix and never re-run after it. Note which half of the pair moves: the bare word
# drifts with every landing, the two-word form has not moved at all.
# Both halves were wrong, and the ratio was never the right quantity anyway: of the 45 sentences
# the bare word adds that nothing else here matches, only FIVE carry a quotation both floors keep.
# A sentence with no quotation in it costs nothing either way, so counting sentences counts the
# wrong things — the four reds above are what a widening would actually adjudicate.
_CLAIM_TRIGGERS = re.compile(
    r"(?:this|the)\s+(?:repo(?:sitory)?|tree|checkout)\s+(?:\w+\s+){0,2}contains"
    r"|\bis contained in\b"
    r"|\bverbatim\s+(?:in|at)\b"
    r"|\bword[-\s]for[-\s]word\b",
    re.IGNORECASE,
)

# A SENTENCE BREAK on already-flattened prose: a terminator, whitespace, then something that opens
# a sentence. The lookahead is what keeps two very common shapes from splitting mid-claim —
# `0.0.0.0:3456` has no whitespace after its dots, and `test_api_kanban.py, the phrase` continues
# in lower case. `e.g. the` survives for the same reason. The cost is the opposite error: a
# sentence that genuinely ends before a lower-case word is not split, so a trigger reaches further
# than it should. That direction only ever ADDS candidates, and every added candidate that is not
# a repo string is visible as a red rather than as a silent miss.
_SENTENCE_BREAK = re.compile(r"(?<=[.;!?])\s+(?=[A-Z(«`\"—*\-]|\d)")

# A QUOTATION, in FOUR delimiters — and the fifth is left out on a measurement, not an oversight.
# Backticks are included even though they mostly hold identifiers, because a claim about a phrase
# is written with them often enough; `_MIN_CHARS`/`_MIN_WORDS` keep bare identifiers out rather
# than dropping the delimiter. Curly `“…”` was added after the independent second pass built a
# fabrication in them: measured over the whole scan it costs ZERO false reds, and the repo holds
# two such phrases. STRAIGHT SINGLE QUOTES ARE DELIBERATELY NOT READ, and the reason is
# apostrophes: an English possessive opens a span that closes at the next one, so `'s own first
# probe DID re-see 10 and 11 independently — recorded verbatim in the DESCRIPTION of` is where
# such a span STARTS. It is shown truncated for width and the real capture runs further, to the
# next apostrophe two clauses later inside a card reference — an earlier draft presented the
# short form as the whole span. Measured, adding it is ONE false red immediately — that span, at
# `4c61283` — and the scanned prose holds 773 multi-word single-quoted spans to draw more from,
# which is the number that decides it rather than the one. So a fabrication written in
# single quotes is invisible here — a real hole, taken knowingly, and named in the module
# docstring rather than left for the next audit to find.
_QUOTATION = re.compile(
    r'"([^"\n]{3,300})"' r"|«([^»\n]{3,300})»" r"|`([^`\n]{3,300})`" r"|“([^”\n]{3,300})”"
)

# NOT LOOKABLE UP VERBATIM, so not asked about: an elision, or an `<angle placeholder>` standing in
# for a value. Measured at `4c61283`, this exemption spares exactly one true claim —
# test_skill_contract.py quoting a SKILL.md sentence with its middle elided — and it is a hole, in
# that a fabrication written with `…` in it is invisible.
_UNVERIFIABLE_BY_CONSTRUCTION = re.compile(r"…|\.\.\.|<[^>]{1,40}>")

# A quotation shorter than this is an identifier, a flag or a fragment, not a phrase somebody is
# asserting the presence of. Both floors apply: `_busy_timeout=5000` clears the character floor
# and fails the word floor, which is the shape that motivates having two.
_MIN_CHARS = 12
_MIN_WORDS = 2

# CLAIMS WHOSE QUOTATION IS NOT A TREE STRING AND IS NOT MEANT TO BE — the ratchet, compared for
# EQUALITY like the sibling scanner's, so an entry that stops being unverifiable has to leave in
# the same commit. THREE entries, covering the three classes the card filing this named as the
# hard part of the whole idea. None of them can be told from a fabrication lexically:
#   * test_api_kanban.py quotes a heading out of a Vikunja CARD's description, and the sentence
#     itself says so ("verbatim in the DESCRIPTION of VMCP-127 (608)"). The tracker is not the
#     tree; nothing in the checkout can confirm or deny it.
#   * test_mutation_sweep_contract.py quotes the wording `889befd` used BEFORE it was corrected.
#     A retraction necessarily reproduces a string that is no longer there, so a correction is
#     lexically identical to the defect it corrects.
#   * the same file quotes the PHANTOM ITSELF — the fabricated half of that claim. It is here
#     rather than absent because the FILE unit is what makes this scanner work at all (see
#     `_occurs_elsewhere`), and under that unit a phrase whose only two copies are the sentence
#     discussing it and a constructed row in the same file reads as absent. Which it is: the
#     surrounding prose says so in its own words. This entry is the founding defect of the card,
#     kept visible on purpose rather than tuned out.
# Adding an entry is allowed and is not a defeat: what it must carry is the REASON, in this
# comment, in the author's own words. Deleting the check's teeth is what the equality stops.
UNVERIFIABLE_QUOTATION_CLAIMS = frozenset({
    "tests/unit/test_api_kanban.py::comments-above"
    ":test_a_server_serving_MORE_than_it_stated_still_reads_the_board_whole"
    "::¶not cited from VMCP-108",
    "tests/unit/test_mutation_sweep_contract.py::comments-above:_docstrings"
    "::¶the exact strings this repo contains",
    "tests/unit/test_mutation_sweep_contract.py::comments-above:_docstrings"
    "::¶the control at the same call site",
})


def _flat(text: str) -> str:
    """Whitespace-flattened, which is how a line-wrapped repo string becomes findable at all."""
    return " ".join(text.split())


def _uncommented(text: str) -> str:
    """Flattened with each line's leading `#` dropped — a comment run's own wrapping removed.

    Two flattenings rather than one because a phrase wrapped inside a COMMENT flattens to
    `... control at # the same call site` under `_flat` and is then unfindable. Both are searched;
    neither alone covers docstrings and comment runs together.
    """
    return " ".join(
        " ".join(line.strip().lstrip("#").strip().split()) for line in text.splitlines()
    ).strip()


def _tracked_text_files():
    """(path, body) for every UTF-8 file `git ls-files` carries, minus this scanner's own source.

    TRACKED, not walked: "is this string in the repo" is a question about what a clone gets, and a
    filesystem walk answers it with whatever happens to be lying in the working directory. A
    scratch file holding the fabricated phrase would otherwise make the claim verify.

    THIS FILE IS EXCLUDED FROM THE CORPUS, AND ONLY FROM THE CORPUS.
    `UNVERIFIABLE_QUOTATION_CLAIMS` holds the quotations verbatim; with this file in the corpus
    each of those literals would be found "elsewhere in the tree" — in the ratchet list itself —
    and every entry would stop being an offender, emptying the very list that names them. So the
    scanner's own bookkeeping is not evidence.

    THE SCAN IS A DIFFERENT LIST, `_tracked_names`, and it was not always. This function used to
    feed both, so excluding `SELF` here silently excluded this file from being READ — while the
    paragraph you are reading claimed, in bold, that it was read. The independent second pass
    planted a fabricated repo-content claim in this module's own docstring and measured it GREEN.
    The claim was false, the file it was written in was the one file the scanner could not see,
    and nothing in the suite noticed. Two lists now, two reasons, and neither borrows the other's.

    The remaining cost of the corpus exclusion is real and is NOT the one that sentence described:
    no file anywhere can cite a string whose only home is this scanner, because for every claimant
    this file is missing from the corpus. CLAUDE.md documenting this gate cannot quote its own
    error messages back. That is priced in the module docstring rather than discovered later.
    """
    listed = subprocess.run(
        ["git", "ls-files", "-z"], cwd=REPO_ROOT, capture_output=True, text=True
    )
    assert listed.returncode == 0, _ls_files_failed(listed.stderr.strip())
    for name in listed.stdout.split("\0"):
        if not name:
            continue
        path = REPO_ROOT / name
        if path.resolve() == SELF or not path.is_file():
            continue
        try:
            yield name, path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue


def _tracked_names():
    """Every path `git ls-files` carries, INCLUDING this scanner's own source.

    Separate from `_tracked_text_files` on purpose, and the separation is the fix for the worst
    thing the independent second pass found. That function filters `SELF` because the CORPUS must
    not contain the ratchet list's own literals; the SCAN was built on top of it and inherited the
    filter, so this file was never read — while its docstring claimed the opposite in bold. A
    fabricated claim planted in the scanner's own module docstring was measured GREEN. Two lists,
    two reasons, and neither borrows the other's.
    """
    listed = subprocess.run(
        ["git", "ls-files", "-z"], cwd=REPO_ROOT, capture_output=True, text=True
    )
    assert listed.returncode == 0, _ls_files_failed(listed.stderr.strip())
    return [name for name in listed.stdout.split("\0") if name and (REPO_ROOT / name).is_file()]


def _corpus():
    return [(name, _flat(body), _uncommented(body)) for name, body in _tracked_text_files()]


def _occurrences(needle: str, corpus) -> int:
    """How many times the tree carries this phrase, counting each file under its BEST flattening.

    `max` and not a sum, which is not a detail: a phrase inside a docstring is found by both
    flattenings, so summing would report every ordinary occurrence twice and the number would not
    be a count of anything. `max` reports 1 for the ordinary case and 1 for the comment-wrapped
    case that only `_uncommented` can see, which is what makes the subtraction below arithmetic
    rather than a coincidence that happens to stay positive.
    """
    flat = _flat(needle)
    if not flat:
        return 0
    return sum(max(body.count(flat), uncommented.count(flat)) for _, body, uncommented in corpus)


def _occurs_elsewhere(needle: str, own_file: str, corpus) -> int:
    """Occurrences in the tree OUTSIDE the file making the claim.

    Some exclusion is mandatory or the check is vacuous by construction: a claim quotes the
    string, the claim is in the tree, so every string every claim quotes is "in the tree".

    THE UNIT IS THE FILE, AND THE PARAGRAPH WAS MEASURED AND REJECTED — on the very defect this
    card was filed for. At `889befd` the fabricated half of that claim, `the control at the same
    call site`, occurred TWICE in test_mutation_sweep_contract.py and nowhere else: at line 88 in
    the comment asserting it, and at line 336 as a constructed row of the pattern test.
    Subtracting only the claiming PARAGRAPH leaves one occurrence standing, so the phantom
    verifies itself and this whole file would have shipped green through the case it exists for.
    That is not an edge: a discussion of a phrase quotes the phrase, so the file arguing about a
    phantom is exactly the file most likely to hold a second copy of it. VMCP-195 (732)'s
    independent check met the same mode from the other side and called it going SILENT on a
    phantom quoted more than once.

    WHAT THE FILE UNIT COSTS, measured at `4c61283` rather than estimated: exactly ONE
    additional entry against the paragraph unit — and it is that same `the control at the same call site`,
    now correctly named as a string this checkout does not carry outside the file discussing it.
    A claim about a phrase that genuinely lives elsewhere in its OWN file can no longer verify;
    there is no such claim in this tree today, and the remedy for one is to point at the other
    file or to name it in the ratchet.
    """
    flat = _flat(needle)
    return sum(
        max(body.count(flat), uncommented.count(flat))
        for name, body, uncommented in corpus
        if name != own_file
    )


def _docstrings(source: str):
    """Every docstring in a module, keyed by its dotted qualname.

    The recursion descends through EVERY node and not only through definitions, which is a fix
    the independent second pass earned by construction: a `def` nested inside a `with` (or an
    `if`, or a `try`) is not a child of the module, so a definition-only walk never reaches its
    docstring and a fabricated claim parked there shipped green.
    """
    def walk(node, prefix):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                qualname = f"{prefix}{child.name}"
                doc = ast.get_docstring(child, clean=False)
                if doc:
                    yield qualname, doc
                yield from walk(child, f"{qualname}.")
            else:
                yield from walk(child, prefix)

    tree = ast.parse(source)
    module_doc = ast.get_docstring(tree, clean=False)
    if module_doc:
        yield "<module>", module_doc
    yield from walk(tree, "")


def _comment_runs(source: str):
    """Every maximal run of `#` lines, keyed by the definition below it — the sibling's idiom.

    Keyed by the following definition and not by a line number for the reason
    `test_mutation_sweep_contract._comment_runs` measured: a line-number key breaks the ratchet on
    any edit above the run, which is every edit.
    """
    lines = source.splitlines()
    defs = sorted(
        (n.lineno, n.name)
        for n in ast.walk(ast.parse(source))
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    )

    def following(line_no: int) -> str:
        for lineno, name in defs:
            if lineno > line_no:
                return name
        return "<end of file>"

    run: list[str] = []
    start = 0
    for i, line in enumerate(lines, 1):
        if line.lstrip().startswith("#"):
            if not run:
                start = i
            run.append(line)
            continue
        if run:
            yield f"comments-above:{following(start)}", "\n".join(run)
            run = []
    if run:
        yield f"comments-above:{following(start)}", "\n".join(run)


def _paragraphs(prose: str):
    """A maximal run of non-blank lines, where a bare `#` counts as blank — the sibling's unit."""
    chunk: list[str] = []
    for line in prose.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            stripped = stripped.lstrip("#").strip()
        if stripped:
            chunk.append(line)
            continue
        if chunk:
            yield "\n".join(chunk)
            chunk = []
    if chunk:
        yield "\n".join(chunk)


def _prose_paragraphs():
    """(site, paragraph) over python prose in tests/ and src/, plus every tracked markdown file.

    The scan is WIDER than the sibling scanner's tests/-only scope, and that is affordable rather
    than brave: at `4c61283` src/ and markdown contribute ZERO unverifiable claims under
    this trigger list — every entry of the ratchet is under tests/ — so the width costs nothing
    today and covers the two places a repo-content claim is most likely to be written next, a tool
    docstring and CLAUDE.md. It is not free of teeth either: planting a fabrication in CLAUDE.md is
    1 failed against a control of 0 failed, which the sibling scanner's tests/-only scope misses.

    ONE COST OF THE WIDTH IS NOT PAID TODAY BUT IS REAL. `docs/superpowers/` is VENDORED prose — it
    talks about other repositories — and it is the largest markdown scope here. The trigger fires
    in five files, none of them under `docs/`, so the vendored text costs nothing now; a future
    vendor update could land a sentence that fires, and the answer then is a ratchet entry naming
    it, not a narrowing. Said here so that red is not a surprise. (An earlier version of this
    sentence said FOUR, which is exactly what the count was while the scan could not read
    `tests/unit/test_repo_quotation_claims.py` — the fifth file is this one.)
    """
    for name in sorted(_tracked_names()):
        path = REPO_ROOT / name
        try:
            body = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if name.endswith(".py"):
            try:
                records = list(_docstrings(body)) + list(_comment_runs(body))
            except SyntaxError:
                continue
            for key, text in records:
                for paragraph in _paragraphs(text):
                    yield f"{name}::{key}", paragraph
        elif name.endswith(".md"):
            for paragraph in _paragraphs(body):
                yield name, paragraph


def _quotations_a_claim_makes(paragraph: str):
    """Every quotation inside a sentence of this paragraph that carries an assertive idiom."""
    for sentence in _SENTENCE_BREAK.split(_uncommented(paragraph)):
        if not _CLAIM_TRIGGERS.search(sentence):
            continue
        for match in _QUOTATION.finditer(sentence):
            quoted = next(g for g in match.groups() if g is not None).strip()
            if len(quoted) < _MIN_CHARS or len(quoted.split()) < _MIN_WORDS:
                continue
            if _UNVERIFIABLE_BY_CONSTRUCTION.search(quoted):
                continue
            yield quoted


def _unverifiable_claims(corpus):
    """`site::¶quotation` for every claimed quotation this checkout does not carry."""
    for site, paragraph in _prose_paragraphs():
        own_file = site.split("::")[0]
        for quoted in _quotations_a_claim_makes(paragraph):
            if _occurs_elsewhere(quoted, own_file, corpus) <= 0:
                yield f"{site}::¶{quoted}"


@requires_git_checkout
def test_a_string_this_repo_says_it_contains_is_a_string_this_repo_contains():
    """The ratchet: every quoted string asserted to be here is here, or is named and explained.

    EQUALITY, not containment, and both directions carry weight — the sibling scanner's argument
    applies unchanged. A NEW unverifiable claim is the regression this card exists to stop. An
    entry that becomes verifiable (the prose is corrected, the string lands, the sentence is
    rewritten) must leave in the same commit, because this list is a public statement about which
    quotations a reader should not trust.

    MUTATION-CHECKED in an isolated `git clone --no-hardlinks`, module path confirmed inside the
    clone each round, `__pycache__` deleted and then `PYTHONDONTWRITEBYTECODE=1`, `--tb=no` so no
    traceback can feed a docstring's own numbers back to the reader, every round restored from a
    pristine COPY and refused unless its target matched exactly once. Selection: this file alone,
    `collected 14 items` every round. Control round: 0 failed. Each row gives the ROUND's failed
    count and then names which test died, because on this selection several rounds kill more than
    one and the count alone would hide which.
      * plant a fabricated claim in a COMMENT run of test_mutation_sweep_contract.py -> 2 failed,
        here and on the screen test's matching row. Plant the same shape in a DOCSTRING of
        test_api_kanban.py -> 2 failed, likewise. The SECOND death in each is an artefact worth
        naming rather than hiding: the screen rows assert their fabrications are absent from the
        checkout, so planting one into the tree falsifies the row for a different reason than it
        falsifies this list. Both plants were 0 failed over the WHOLE of tests/unit before this
        file existed, which is the card
      * plant one in CLAUDE.md, a markdown paragraph -> 1 failed, here: what the widened scan buys
      * drop `SELF` from the corpus exclusion, so this file's own ratchet list counts as evidence
        -> 4 failed: here, on the scope test, and on both fabrication rows of the screen test,
        which then find their own literals. Every grandfathered entry stops being an offender at
        once. The self-reference this repo keeps stepping on, as a round rather than a worry
      * drop the exclusion in `_occurs_elsewhere`, so a claim vouches for itself -> 2 failed, here
        and on `test_a_claim_is_never_evidence_for_itself`
      * empty `UNVERIFIABLE_QUOTATION_CLAIMS` -> 1 failed, here; add a non-existent entry
        -> 1 failed, here
      * PAIRED, because each half alone looks innocent: drop the exclusion AND empty the list
        -> 1 failed, and NOT here — this assert goes green, because the scan is vacuous and the
        list agrees with it. The one death is `test_a_claim_is_never_evidence_for_itself`, which
        is the reason that test is separate from this one
      * revert the tree scan to the claiming PARAGRAPH as the unit -> 1 failed, HERE and here
        alone. The third ratchet entry stops being an offender, because that phantom's second
        copy sits in the same file. The oracle pin does not see it: it asks `_occurs_elsewhere`
        directly and that function was not touched. Two tests, two different halves of the unit
      * drop `this repo contains` from `_CLAIM_TRIGGERS` -> 2 failed, here and on one row of the
        screen test. Drop `verbatim in` -> 1 failed, here ALONE, the screen test surviving because
        its fabrication rows carry a second trigger each. Drop `word for word` -> 1 failed, on the
        screen test ALONE, this assert surviving because no ratchet entry rests on that idiom.
        The three triggers are held by different tests and no one test holds all three
      * make `_UNVERIFIABLE_BY_CONSTRUCTION` match nothing -> 2 failed, here and on the ELISION
        row only: the exemption spares a real elided claim in test_skill_contract.py, so removing
        it adds a fourth offender to this list. The placeholder row survives, because
        `git show <rev>:<path>` is a string this checkout really carries once it is looked up
      * `_MIN_CHARS = 4` -> 2 failed, here and on the screen test's short-quotation row: lowering
        the floor pulls a fragment into the tree scan as well
      * make `_SENTENCE_BREAK` never split, so a trigger reaches its whole paragraph -> 1 failed,
        here. The sentence unit is load-bearing on today's prose and not only in the false-red
        budget the module docstring measures
      * `_MIN_WORDS = 1` -> 1 failed, here — a knob that was 0 failed everywhere until the scan
        widened, which is why the widening is a round of its own and not a tidy-up
      * make the SCAN reuse the corpus builder, so it inherits the `SELF` exclusion -> 1 failed,
        on the scope test. That is the shape the independent second pass found shipped: a
        fabricated claim in this file's own docstring, green, while the docstring said otherwise
      * read only `tests/` python instead of every tracked `.py` -> 1 failed, on the scope test
      * `_occurrences` searching only the raw flattening -> 1 failed, and NOT here: on the oracle
        pin alone. No claim in this tree needs the comment flattening to verify, so the tree scan
        is blind to that half — which is why the oracle pin asserts it directly
      * widen the corpus AND the scan to untracked files -> 0 failed anywhere; that choice is
        argued in the module docstring and pinned by nothing, which is said there too
    """
    unverifiable = sorted(_unverifiable_claims(_corpus()))
    assert unverifiable == sorted(UNVERIFIABLE_QUOTATION_CLAIMS), (
        "the set of quotations this repo's prose ASSERTS it contains, but does not, has moved.\n"
        f"  found:   {unverifiable}\n"
        f"  grandfathered: {sorted(UNVERIFIABLE_QUOTATION_CLAIMS)}\n"
        "A NEW entry means a sentence claims a string is in this checkout and it is not — either "
        "the quotation is wrong (fix the prose: `git grep -F` the flattened phrase) or the "
        "quotation was never meant to be a repo string (a card description, another repo, a tool's "
        "output, a wording quoted because it was retracted), in which case add it here WITH ITS "
        "REASON in the comment above.\n"
        "A MISSING entry has TWO causes and only one of them means delete it. The claim may have "
        "become verifiable — the prose was corrected, or the string landed — and then the entry "
        "misinforms and goes. But a phantom also stops being an offender the moment ANY OTHER "
        "tracked file quotes it, including a file merely discussing the mistake, because this "
        "scanner reads bytes and cannot tell a copy that IS the phrase from a copy ABOUT it. "
        "`git grep -F` the phrase first: if its only new home is prose describing the defect, the "
        "entry is still true and what you have found is this scanner's limit, not a fix. Do not "
        "delete the record of a defect because someone wrote about it."
    )


@pytest.mark.parametrize(
    "sentence, flagged, why",
    [
        # THE REPRODUCTION, in the two shapes measured green before this file existed.
        (
            'verbatim in test_api_kanban.py, the phrase "a wholly invented baseline clause" is '
            "one of the strings this repo contains",
            True,
            "the planted comment-run fabrication",
        ),
        (
            'the phrase "an utterly imaginary paging preamble" occurs word for word in '
            "test_workflow_gates.py",
            True,
            "the planted docstring fabrication, under a different trigger",
        ),
        (
            'both examples are strings this repo really contains, "a fabricated baseline clause '
            'nobody wrote" and "control: page 1"',
            True,
            "889befd's own shape: two quotations, one real, one not",
        ),
        # THE OTHER SIDE. Ordinary prose that quotes without asserting must stay green.
        (
            'the drain refuses with "Bind for 0.0.0.0:3456 failed" when a sibling holds the port',
            False,
            "a quoted error message, no trigger",
        ),
        (
            'a reviewer might write "this looks fine to me" and mean nothing by it',
            False,
            "a hypothetical utterance, no trigger",
        ),
        (
            'the mutant "отказывает только из Review" is constructed, not quoted',
            False,
            "a constructed mutant, no trigger",
        ),
        (
            "`git show <rev>:<path>` settles it word for word",
            False,
            "a trigger, but the quotation is an angle placeholder",
        ),
        (
            'the sentence «ветка предложения … назначенные на тебя» occurs verbatim in step 3',
            False,
            "a trigger, but the quotation is elided",
        ),
        (
            "the flag `--isolated` appears word for word in the launch line",
            False,
            "a trigger, but the quotation is one short identifier",
        ),
        (
            'the note says "a fake bit" word for word',
            False,
            "a trigger, but the quotation is under the character floor",
        ),
    ],
)
def test_the_screen_reads_an_assertion_and_not_merely_a_quotation(sentence, flagged, why):
    """The predicate itself, driven by construction — the half a tree scan can never hold.

    A scan over the tree passes trivially when nothing in the tree fires, so on its own it cannot
    tell a working screen from a broken one. These rows are the screen's behaviour stated
    independently of what the repo happens to contain today: three fabrications it must flag,
    including the exact two plants that shipped green before this file, and SEVEN pieces of
    ordinary prose it must not. The negative rows are not decoration: run each of their quotations
    through the oracle and THREE of the seven come back at zero occurrences, which is to say the
    naive rule — 3,068 violations on this repo's real prose — would redden them, and the screen
    must not. The other four are found in the tree and hold the opposite half: prose the naive rule
    also passes, kept so a broken trigger cannot go unnoticed just because every negative row
    happened to be a phantom. (Three figures in this paragraph were wrong until this commit. SIX
    for the seven rows a reader can count in the source below. 2,160 for the violation count — a
    PROBE number taken under the paragraph unit this design went on to REJECT, corrected twice in
    the module docstring while this sentence, quoting the same quantity, was corrected neither
    time. And "every one of these shapes is drawn from that set", which is true of three of them.)

    The corpus here is the REAL one and the claiming file is a name no tracked file has, so a row
    asserting `flagged` is asserting the quotation is absent from the whole checkout as well. That
    is deliberate: a row that only exercised a regex would go stale the moment the tree gained the
    phrase, and would say nothing about the oracle it is paired with.

    MUTATION-CHECKED, same discipline and stand as the ratchet, this file alone, `collected 14
    items` every round, control 0 failed. Rows are numbered as listed above.
      * drop `this repo contains` from `_CLAIM_TRIGGERS` -> 2 failed, ONE of them here: row 3
        only. Row 1 survives because it carries `verbatim in` as well, which is why the round is
        not the two rows a reader would predict from the trigger's spelling
      * drop `word for word` -> 1 failed, here: row 2
      * drop `verbatim in` -> 1 failed, and NOT here. Every fabrication row carries a second
        trigger, so this test cannot see that idiom go; the ratchet can, because its
        `not cited from VMCP-108` entry is the tree's only `verbatim in` claim
      * make `_UNVERIFIABLE_BY_CONSTRUCTION` match nothing -> 2 failed, ONE of them here: the
        ELISION row. The placeholder row survives, and the reason is worth knowing before trusting
        that exemption — `git show <rev>:<path>` is a phrase this checkout really carries, so with
        the exemption gone it simply verifies. That row pins the exemption's INTENT, not its
        necessity, and only the elision row kills it
      * `_MIN_CHARS = 4` -> 2 failed, ONE of them here: the short-quotation row. The identifier row
        survives on the word floor alone
      * `_MIN_WORDS = 1` -> 1 failed, and NOT here: on the ratchet. It was 0 failed everywhere
        until the scan widened to every tracked `.py` and to this file, which is worth recording
        as its own small lesson — a knob can look unpinned only because the scan was too narrow to
        reach anything it holds. Its own row here is still `--isolated`, which the CHARACTER floor
        also stops, so this test never sees the word floor go
      * `_SENTENCE_BREAK` never splits -> 1 failed, and not here: every row is one sentence, so
        this test is blind to the unit by construction and the ratchet is what holds it
    """
    quotations = list(_quotations_a_claim_makes(sentence))
    corpus = _corpus()
    unverifiable = [q for q in quotations if _occurs_elsewhere(q, "<constructed>", corpus) <= 0]
    assert bool(unverifiable) is flagged, (
        f"{why}: expected flagged={flagged}, got {unverifiable or 'nothing'} "
        f"(quotations the screen read as claimed: {quotations})"
    )


def test_a_claim_is_never_evidence_for_itself():
    """The exclusion, pinned on a synthetic corpus so no change to the tree or the list moves it.

    This is the test that kills the paired mutation the ratchet cannot see. Drop the exclusion and
    every claim verifies against its own text; empty the ratchet list in the same commit and the
    tree scan is green with a scanner that measures nothing — measured against a control of
    0 failed on this file, that pair is 1 failed and this is the one that dies. Here the corpus is
    supplied, so neither the tree nor the list can absorb the change.

    THE SECOND HALF IS THE ONE THE CARD IS ABOUT. A phantom under discussion gets quoted twice in
    the file discussing it — the sentence asserting it, and a constructed row demonstrating it —
    and at `889befd` that is exactly the shape the founding defect had. Two copies in ONE file are
    not two witnesses, and the row below is what forbids reading them as such.

    MUTATION-CHECKED, this file alone, `collected 14 items`, control 0 failed:
      * `_occurs_elsewhere` drops its `name != own_file` filter -> 2 failed, here and on the
        ratchet. PAIRED with emptying the ratchet list -> 1 failed, here ALONE: that is the whole
        reason this test is not folded into the ratchet
      * the tree scan reverts to the claiming PARAGRAPH as its unit -> 1 failed, on the RATCHET
        alone. This test does not see it, because it pins `_occurs_elsewhere` and that function
        was not touched — the two tests hold different halves of the same decision, and neither
        is redundant
      * `_occurrences` searches only the raw flattening -> 1 failed, HERE alone, on the
        comment-wrapped row. The ratchet is green: no claim in this tree needs that flattening to
        verify today, so without this row that half of `_occurrences` would be unheld
    """
    phrase = "a phrase that lives in exactly one file"
    claim = f'the string "{phrase}" occurs word for word in this repository'
    corpus = [("only.py", _flat(claim), _uncommented(claim))]

    assert _occurrences(phrase, corpus) == 1, "the corpus must hold the claim itself"
    assert _occurs_elsewhere(phrase, "only.py", corpus) == 0, (
        "a claim quoting a string is not evidence that the string is anywhere else. Without the "
        "exclusion every claim in this repo verifies against its own text and the scan is vacuous"
    )
    assert _occurs_elsewhere(phrase, "somewhere-else.py", corpus) == 1, (
        "asked from another file the same phrase IS found — so the zero above is the exclusion "
        "working, not the corpus being empty"
    )

    discussed = f"{claim}\n\nand a constructed row quoting '{phrase}' to show the shape"
    twice = [("discussion.py", _flat(discussed), _uncommented(discussed))]
    assert _occurrences(phrase, twice) == 2, "the discussion must hold two copies"
    assert _occurs_elsewhere(phrase, "discussion.py", twice) == 0, (
        "two copies inside ONE file are not two witnesses. A file arguing about a phantom quotes "
        "it more than once by nature, so a rule that subtracts only the claiming PARAGRAPH leaves "
        "the second copy standing and the phantom verifies itself — measured on `889befd`, where "
        "that is exactly how the defect this file exists for would have shipped green"
    )

    wrapped = "# the string spans\n# a comment line break here"
    needle = "spans a comment line break"
    wrapped_corpus = [("wrapped.py", _flat(wrapped), _uncommented(wrapped))]
    assert _occurrences(needle, wrapped_corpus) == 1, (
        "a phrase wrapped across a COMMENT's line break is findable only once the `#` markers are "
        "dropped — `_flat` alone leaves a `#` in the middle of it and reports the phrase absent"
    )
    assert _occurs_elsewhere(needle, "elsewhere.py", wrapped_corpus) == 1, (
        "and it must stay findable when the question comes from another file, or every claim "
        "about a phrase wrapped inside a comment run reads as a fabrication"
    )


@requires_git_checkout
def test_the_claim_keyed_rule_still_looks_at_a_tiny_fraction_of_the_quotations():
    """The design's central figure, as a PROPERTY — because as a number it rots every landing.

    The module docstring rejects "every quoted string must be in the tree" on a violation count,
    and that count is honest but perishable — 2,993 of 11,352 at `3937b45`, 3,068 of 11,596 at
    `4c61283`, three card landings apart, both taken with the ruler that docstring names. What a
    reader ACTS on is not the digit, it is the RATIO — the claim-keyed rule adjudicates a tiny
    fraction of what the naive one would — so that is asserted here and the digits stay in the
    prose as history, each against a sha you can check out. An earlier version of this sentence
    quoted a pair with no sha at all, which is the shape VMCP-167 (688) rules out: history gets an
    anchor, a working figure gets an assert, and neither gets a date.

    The violation count itself is deliberately NOT asserted, on a cost measured rather than
    guessed at — and measured three times, because the first run was the reminder that a
    wall-clock second is the one figure no sha can pin. Timing both back to back: 28 s, 43 s and
    48 s for the count against 0.15 s, 0.15 s and 0.17 s for this, as the machine's load varied
    from idle to three concurrent agents. The absolute nearly doubles; the ratio stays between
    190x and 290x, so the ratio is what this paragraph carries and the seconds are only its
    scale. `lint-and-unit` is what sets a run's length — 38-46 s, a figure this file
    takes from CLAUDE.md's release section rather than re-measuring — so even the quiet-machine
    figure is the same order as the entire job, paid on every landing to restate what the ratio
    already says. (An earlier version quoted 26 s and 0.7 s as though they were properties.)

    MUTATION-CHECKED, this file alone, `collected 14 items`, control 0 failed:
      * `_CLAIM_TRIGGERS` matches every sentence -> 3 failed: here, on the ratchet (whose offender
        set becomes the naive one, hundreds of entries) and on ONE row of the screen test — only
        one, because the other negative rows quote strings this checkout really carries
      * the threshold raised past the measured ratio -> 1 failed, here. Two rounds, because the
        first attempt used the measured value itself and was 0 failed, because quotations over
        claimed does not divide evenly and `> floor(ratio)` is still true by one. At the next
        integer up, and at 5000, it is 1 failed.
        The headroom is real and the off-by-one is recorded rather than smoothed over — a round
        that reads 0 failed for an arithmetic reason looks exactly like a threshold with slack
    """
    all_quotations, claimed_quotations = 0, 0
    for _site, paragraph in _prose_paragraphs():
        all_quotations += sum(1 for _ in _QUOTATION.finditer(_uncommented(paragraph)))
        claimed_quotations += sum(1 for _ in _quotations_a_claim_makes(paragraph))
    assert all_quotations > 100 * claimed_quotations, (
        f"this repo's prose holds {all_quotations} quotations and the claim-keyed rule asks about "
        f"{claimed_quotations} of them — no longer two orders of magnitude apart. The module "
        "docstring rejects the naive rule on exactly that gap, so either the trigger list has "
        "widened far past what was measured, or this repo has stopped quoting things it does not "
        "contain. Both are worth knowing before trusting the WHY THE OBVIOUS RULE section"
    )


@requires_git_checkout
def test_the_scan_reaches_every_scope_it_says_it_covers():
    """The scan's REACH, asserted — because on today's tree nothing else notices it shrinking.

    This exists because two mutations were measured and BOTH were silent. From a control of
    0 failed on this file, before this test was written: making the scan skip markdown entirely
    -> 0 failed, and making it skip src/ -> 0 failed. Neither scope holds an unverifiable claim
    today, so the ratchet is identical with them and without them, and a narrowing would ship
    green — the same shape VMCP-194 (724) caught on the sibling scanner, where a guard was
    satisfied by material other than the material it named. A scan that silently stops reading a
    scope is worse than one that never read it, because the module docstring says the scope is
    covered.

    It also pins the corpus exclusion from the other side: the scanner's own source must NOT be
    in the corpus, which is what stops its ratchet list from vouching for itself.

    IT ASKS ABOUT PYTHON PROSE BY EXTENSION, and the first spelling did not — it asked only that
    some site start with `src/`, and from a control of 0 failed the round meant to kill it came
    back 0 failed. The reason is a file this repo really has:
    `src/vikunja_mcp/skills/tracker/SKILL.md` is tracked markdown living under `src/`, so it
    satisfied a `src/` prefix while every docstring and comment in the package had gone. A guard
    answered by material other than the material it names is the exact shape VMCP-194 (724) caught
    next door, met here by construction rather than by reading.

    MUTATION-CHECKED, this file alone, `collected 14 items`, control 0 failed:
      * the scan skips markdown -> 1 failed, here (0 failed before this test existed)
      * the scan skips PYTHON prose under src/ -> 1 failed, here. Under the prefix-only spelling
        the identical mutation was 0 failed, SKILL.md standing in for the whole package
      * `SELF` is not excluded from the corpus -> 4 failed, here, on the ratchet and on both
        fabrication rows of the screen test, which then find their own literals in the corpus
    """
    sites = [site for site, _ in _prose_paragraphs()]
    files = {site.split("::")[0] for site in sites}
    for prefix, what in (
        ("tests/", "the test suite's own prose"),
        ("src/", "the package's docstrings and comments"),
        ("scripts/", "the release helpers, which are tracked python outside both"),
    ):
        assert any(
            site.startswith(prefix) and site.split("::")[0].endswith(".py") for site in sites
        ), (
            f"the scan reaches no PYTHON paragraph under {prefix} ({what}), which the module "
            "docstring says it covers. The `.py` conjunct is load-bearing: SKILL.md is tracked "
            "markdown under `src/`, and without it this assert passes with the whole package's "
            "prose unread. Nothing else in this file notices either — no scope here holds an "
            "unverifiable claim today, so the ratchet is identical with the scope and without it"
        )
    assert "CLAUDE.md" in files, (
        "the scan reaches no CLAUDE.md paragraph, so the markdown half of the scope is off. That "
        "half is where the rule itself is written and where the next repo-content claim is most "
        "likely to be made"
    )
    assert SELF.relative_to(REPO_ROOT).as_posix() in files, (
        "THIS FILE IS NOT BEING SCANNED, which is how it shipped once: the scan was built on the "
        "corpus builder and inherited its `SELF` exclusion, so a fabricated repo-content claim "
        "planted in this very module's docstring was measured green while the docstring claimed "
        "in bold that it was read. The corpus excludes this file; the scan must not"
    )

    corpus_names = {name for name, _, _ in _corpus()}
    assert corpus_names, "the corpus is empty; every claim would read as unverifiable"
    assert SELF.relative_to(REPO_ROOT).as_posix() not in corpus_names, (
        "this scanner's own source is in the corpus, so the quotations its ratchet list holds "
        "verbatim count as evidence that the tree carries them — the list would then vouch for "
        "itself and empty out. See `_tracked_text_files`"
    )


# --- VMCP-195 (732): the POINTER half, closed as far as a checkout can close it ----------------
#
# The boundary bullet above says this scanner reads quotations and never pointers, and that a
# `VMCP-N (id)` pair is therefore out of scope. That stays TRUE of the scanner; what follows is a
# SECOND, much narrower gate beside it, and the difference between them is the whole design.
#   The class is #660's: an agent COMPOSED a ref instead of echoing the one a tool handed back,
# and shipped `VMCP-181 (732)` into a landed file. A composed ref does not look broken — it points
# at a DIFFERENT LIVE CARD, so the reader follows it and never notices. `VMCP-N` is assigned
# per-project by the server and `id` is global, so no arithmetic recovers one from the other; the
# only authority is the tracker, which a unit test deliberately cannot reach (no token, no
# network). That is why the quotation scanner could not take this on and why the remedy #735
# reached for was to make the tool hand the ref back.
#   WHAT A CHECKOUT CAN STILL DECIDE IS INTERNAL CONSISTENCY. The pairing is a bijection: one id
# has one ref and one ref has one id. So if the tree writes the same id under two different refs,
# or the same ref against two different ids, one of those sites is a fabrication — and THAT is
# answerable with no tracker at all. It is strictly weaker than correctness (a pair that is wrong
# the SAME way everywhere stays invisible, and a repo mentioning a card exactly once has nothing
# to compare), and strictly stronger than nothing, which is what was here before.
#   IT CAUGHT A LIVE ONE ON ARRIVAL, which is the argument for it. Measured over the 297 pairs in
# the publishable tree: exactly two conflicts. `VMCP-141` was written against BOTH 629 and 630 —
# and `get_task` says 629 is VMCP-140, so `test_measured_figure_anchors.py` carried a phantom of
# precisely #660's shape, landed, reviewed and unnoticed. It is corrected in the same commit as
# this gate. The other conflict is the ratchet entry below.
_REF_PAIR = re.compile(r"VMCP-(\d+)\s*\((\d+)\)")

# A RETRACTION reproduces the string it retracts, so the founding defect is lexically identical to
# every correction of it — the same reason the quotation ratchet above holds a phantom on purpose.
# `VMCP-181 (732)` is #660's fabricated ref, quoted in four places (SKILL.md, server.py,
# workflow.py, test_workflow_gates.py), every one of them immediately saying it is wrong.
#   KEYED BY THE PAIR SINCE VMCP-236 (797), which is what the name always said and what the value
# was not. A retraction is not a fact about an id, nor about a ref: it is a fact about ONE PAIRING,
# and keying it by the id alone cost a hole and a false red at once. Both were MEASURED on this
# tree against a control of 0 failed, not read off the diff, and they point in opposite directions:
#   * SILENT — the id side excused the whole CARD, so a SECOND phantom on 732 was invisible.
#     `VMCP-777 (732)` planted in a tracked file measured 0 failed, while the sentence that used to
#     stand here promised a new phantom on 732 would still have to be added by hand. It would not.
#   * LOUD — the ref side excused a group only when EVERY id in it was ratcheted, so writing the
#     CORRECT pair `VMCP-181 (706)`, which is the string `get_task(706)` hands back, measured
#     1 failed under an assert saying one of those sites was composed. Neither of them was.
# Pair-keyed, the retracted pairing is excised from the corpus before either question is asked, so
# both directions then answer about the rest of the tree and neither needs a clause of its own.
# Adding an entry is allowed; what it must carry is the reason, in this comment, in your words.
KNOWN_RETRACTED_REF_PAIRS = frozenset({("181", "732")})


def test_no_card_is_written_under_two_different_refs():
    """A composed `VMCP-N (id)` pair points at a LIVE unrelated card — #660's defect (VMCP-195).

    Not the scanner above: that one reads QUOTED STRINGS, and a pointer is not a quotation. This
    asks the one question about a pointer that needs no tracker — is the tree self-consistent? —
    and it is deliberately the weaker half of the class, because the stronger half is unreachable
    from a checkout by construction.

    THREE HONEST BOUNDS AND ONE SHARP INSTANCE OF ONE OF THEM — counted that way rather than as
    four, because the instance is not independent and calling it a fourth bound would be this
    file's own class of defect. A pair wrong the SAME way everywhere stays invisible; a card
    mentioned exactly once has nothing to compare; a NEW site quoting the retracted pairing in
    earnest reads as the retraction. The instance belongs to the second, VMCP-236 (797) turned it
    from a false RED into a bound, and it is the price of no longer reddening on the correct pair:
    a fabricated id written under a ref whose only other appearance
    is the retracted pairing leaves that ref with a single claimant once the retraction is excised,
    so it goes green. `VMCP-181 (500)` is that shape. A checkout cannot tell it from
    `VMCP-181 (706)`, which is correct — the two differ only in a fact the tracker holds — so the
    choice was which error to take, and a gate that reddens on correct prose is the one that gets
    switched off. Recording ref 181's true owner in the ratchet WOULD separate them and is refused
    for a reason this file cannot make an exception to: that would be a tracker fact, and the
    paragraph above the regex says the tracker is unreachable from a checkout by construction.

    MUTATION-CHECKED, `__pycache__` deleted per round then `PYTHONDONTWRITEBYTECODE=1`, this test
    as the selection, every plant asserted to be present before its round ran and every restore
    diffed against a pristine copy. A plant goes into a TRACKED file OTHER than this one, and that
    is measured rather than deduced from `_tracked_text_files` excluding SELF: the SAME string that
    reddens the suite from another file measures 0 failed planted here, so a sweep run inside this
    module would report a blind gate. Control round: 0 failed.
      * the live phantom this gate arrived with, put back (`VMCP-141 (629)` in
        test_measured_figure_anchors) -> 1 failed, naming both refs and the file of each
      * plant `VMCP-999 (630)` anywhere in the tree -> 1 failed, the id-side direction
      * plant `VMCP-141 (99999)` -> 1 failed, the ref-side direction, which is the one a bare
        id-keyed map would miss: 99999 is mentioned nowhere else, so nothing conflicts on the id
      * empty `KNOWN_RETRACTED_REF_PAIRS` -> 1 failed on `732`, i.e. the ratchet is load-bearing
        rather than decorative, and its one entry is the retraction it says it is
      * plant `VMCP-777 (732)`, a SECOND phantom on the ratcheted id -> 1 failed. Keyed by the id
        this was 0 failed, so pair-keying STRENGTHENED the id side; it did not only relax the ref
        side, and that asymmetry is why the round is recorded next to its opposite below
      * plant the CORRECT pair `VMCP-181 (706)` -> 0 failed. Keyed by the id this was 1 failed,
        on the one string `get_task(706)` hands back — the false red 797 exists to remove
      * plant `VMCP-181 (500)` -> 0 failed, the bound named above, measured rather than argued.
        Keyed by the id this was 1 failed, so the round pins the cost as well as the fix

    FALSE-RED PRICE, re-derived on this tree rather than inherited from the landing that added the
    gate: 303 pairs in the publishable tree at `8239512`, of which the ratchet excises 4 — the one
    retracted pairing, in the four files the comment above names, re-derived here rather than
    carried over — leaving ZERO conflicts in both directions. Keyed by the id it was zero too, so
    the number did not move; what moved is what it costs to keep. Zero used to hold only while no
    correct pair naming ref 181 was written down, and `VMCP-181 (706)` is the shape the rulebook
    asks every agent to write. The same figure is now zero for a reason that does not depend on
    what nobody has said yet.
    """
    by_id: dict[str, dict[str, list[str]]] = {}
    for name, body in _tracked_text_files():
        for match in _REF_PAIR.finditer(body):
            ref, card = match.group(1), match.group(2)
            if (ref, card) in KNOWN_RETRACTED_REF_PAIRS:
                continue
            by_id.setdefault(card, {}).setdefault(ref, []).append(name)

    assert by_id, (
        "no `VMCP-N (id)` pair found anywhere in the tree, so this gate scanned nothing and "
        "passed for the wrong reason — the `no tests ran looks like a pass` shape. Either the "
        "corpus broke or the citation idiom changed; neither is a reason to be green"
    )

    conflicts = {card: refs for card, refs in by_id.items() if len(refs) > 1}
    assert not conflicts, (
        f"the same card id is written under two different `VMCP-N` refs: {conflicts}. The pairing "
        "is a bijection, so at least one of those sites is a COMPOSED ref — #660's defect, which "
        "does not look broken because it points at a different LIVE card. Ask `get_task(<id>)` "
        "which ref is real and fix the other; if the wrong one is being quoted deliberately as a "
        "retraction, add that PAIR — `(ref, id)`, not the bare id — to KNOWN_RETRACTED_REF_PAIRS "
        "with your reason in its comment, which excises exactly the one site and leaves every "
        "other mention of both halves under this gate"
    )

    by_ref: dict[str, dict[str, list[str]]] = {}
    for card, refs in by_id.items():
        for ref, names in refs.items():
            by_ref.setdefault(ref, {})[card] = names
    reused = {ref: cards for ref, cards in by_ref.items() if len(cards) > 1}
    assert not reused, (
        f"the same `VMCP-N` ref is written against two different card ids: {reused}. This is the "
        "direction an id-keyed map alone cannot see — a fabricated pair whose ID appears nowhere "
        "else conflicts with nothing on the id side, and only the ref side notices. Retracted "
        "pairings are already excised, so at least one of these sites is a composed ref and "
        "`get_task` on each id says which"
    )
