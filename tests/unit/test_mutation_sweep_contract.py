"""The control-round rule for mutation sweeps, made executable — tracker #656.

WHAT IS BROKEN WITHOUT THIS FILE. Mutation sweeps in this repo are hand-run: an agent edits a
source line, runs `pytest`, reads the summary line, restores, and writes the result into a
docstring. The summary line is where the arithmetic goes wrong, because `N failed` is a KILL
COUNT only if that same selection failed ZERO times before any mutation was applied — and a `-q`
summary never says whether it did. VMCP-119 (594) swept in a tree where 30 tests failed
constantly for a reason unrelated to any mutation: every row of a six-row table came out inflated
by exactly 30 and its headline conclusion was wrong by a factor of 16 (the true kill count was
2). Constant failures survive a before/after comparison INTACT, so they read as signal. An
unmutated control round costs one `pytest` invocation and turns that from undetectable into
obvious. VMCP-133 (622) removed the particular 30-failure source; this file is the other half its
description asked for — the METHODOLOGY, which no single test file's fix can close.

WHAT THIS FILE ENFORCES. One shape, stated in CLAUDE.md's Testing Philosophy and pinned below: a
sweep record that quotes a round as a NUMBER of failures must state the control round's number of
failures too. Precision has to be symmetric. `-> FAIL` is answered by `control PASS`; `-> 7
failed` is answered by `control 0 failed` and by nothing weaker, because "control PASS" in a tree
where 30 tests fail constantly is a sentence that is TRUE and USELESS at the same time — that is
exactly the sentence 594 could have written. The count that goes in the record is the FAILED
count, never the pass total: a pass total moves with every test the repo adds (CLAUDE.md keeps
its unit count as a floor for the same reason, and test_api_kanban has watched one total go stale
three times), while `control 0 failed` stays true as the suite grows. This file broke its own
rule and paid for it: the rounds taking the WHOLE scanner file as their selection quoted a
control of `0 failed, 4 passed`, which was 4 at `6dd2803`, 5 at `bba4fed` once VMCP-166 (687)
added a test here, and moved again in the commit that noticed. They now record the failed count
alone. Rounds selecting exactly ONE test keep `0 failed, 1 passed`, where the total is the
selection size and cannot move without the round itself changing.

WHAT IT CANNOT ENFORCE, said plainly because this repo prices its guards rather than rounding
them up.
  * It reads PROSE. It sees the SHAPE of a claim, never whether a control round was actually run
    — a record that says `control 0 failed` without running one satisfies it. Nothing in a repo
    of hand-run sweeps can check that; what a scanner CAN do is make the omission impossible to
    ship silently, which is the difference between this and a rule kept in prose alone. Nor can it
    tell USE from MENTION on its own, which is VMCP-259 (861): a sentence merely QUOTING the phrase
    vouched for a whole record, and what separates the two is a named list of introducing words —
    `_MENTIONED`, whose comment carries the measured price of every wider rule.
  * One control vouches for its whole PARAGRAPH, not for each number in it. That is the residual
    after VMCP-167 (688) moved the unit down from the whole RECORD, and the two are not the same
    limit: at record granularity one sentence immunised every count in a docstring, which was not
    a constructed worry but the live reason a human filed VMCP-161 (668) — that docstring's
    uncontrolled whole-file count passed on the strength of a control clause 27 lines below it
    about a DIFFERENT mutation, and deleting that one unrelated clause was what turned it red.
    Re-measured here 2026-08-02, `__pycache__` cleared and PYTHONDONTWRITEBYTECODE=1, on that
    exact pre-image rather than on a construction: control 0 failed, the pre-image installed under
    the OLD record unit 0 failed — blind — and under the paragraph unit 1 failed, naming the
    paragraph. What still passes is one PARAGRAPH reading "Round A: control 0 failed; drop guard A
    -> 2 failed. Round B, never baselined: drop guard B -> 9 failed. Round C, likewise never
    baselined: drop guard C -> 41 failed." Per-number pairing is not recoverable from prose —
    nothing in the text says which control a number belongs to — so the rule is enforced per
    PARAGRAPH and asked of the author per ROUND. Put a blank line before Round B and the last two
    are flagged.
  * The paragraph unit is AUTHOR-CONTROLLED, which is the honest way to say that a record whose
    author left no separator in it is still one chunk (a blank line, or in a comment run a bare
    `#`). This very block is the example: `WHAT IT CANNOT
    ENFORCE` runs its bullets with no blank line between them, so ALL of them are one paragraph
    and any control in any of them vouches for the rest. That is PINNED rather than dated, in the
    paragraph test below, and the reason is a mistake worth recording: it was first written with
    the measured pair "four bullets, 47 lines", and each of the next three edits to this very
    block falsified it again — 51, then 54, then 56. A number describing the text it is written
    in cannot be kept true by care, which is the same self-reference the scope comment below
    flags for `git log -S`. So the property is asserted and the count is gone, which is the move
    VMCP-167 (688)'s last round generalised into a rule this file now follows throughout: a count
    over the whole tree gets an ASSERT if a reader acts on it, or a SHA if it is history, and
    never a date — a date does not name a tree in a repo that lands several commits an hour, and
    four counts here were true at one sha and false at the next one 80 minutes later. The rule's
    EDGE is measured rather than waved at, because it does not reach every number below. What it
    converted are the counts over ALL of tests/ or all of the repo's markdown: across the seven
    trees that round walked (`889befd`, `93714d5`, `94bae3d`, `6dd2803`, `bba4fed`, `75a1e52` and
    the one it shipped from) the three over tests/ moved at EVERY one of the six steps, and the
    markdown one at two of them, both inside a single afternoon. Not every LANDING moves them,
    which is the tempting overstatement and is false: `a8573e6` landed between the sixth tree and
    the seventh and moved none of the four, being prose rewritten inside paragraphs that already
    existed. The claim is about those seven trees, not about time. What is left standing
    are counts over bounded, NAMED sets — and that half holds of TWO of the three examples it
    used to name, re-measured for VMCP-194 (724) across those same seven trees. The six runs
    above `_spelled_ref` and the thirteen rows of the pattern test are six and thirteen at every
    one of the seven. The legacy entries that sit on comment runs are NOT: three at `889befd`,
    `93714d5` and `94bae3d`, ten at `6dd2803`, `bba4fed`, `75a1e52`, `e77b0cf` and `9489de3`, and
    nine at `52d6085`, where VMCP-155 (660)'s entry left, and still nine at `eb14eb4`, this card's
    base. Those are trees actually walked rather than a range read off two endpoints. So the bare
    "ten" that stood in this sentence had moved once before it was written and moved again after,
    and the step is not prose drift: it is
    `6dd2803`, this card's OWN first commit, where the rekey turned seven record keys into
    sixteen paragraph keys. The dividing line is therefore not boundedness but WHO may move the
    set — the six runs and the thirteen rows move only when code does, while the legacy list is
    the very thing this file's ratchet edits, and the list's own comment already says its SIZE
    moves when an entry leaves. Converting the two that hold would still have bought nothing;
    the third wanted no assert either, only the shas it now carries. The unit
    is deliberately not the finest split available: splitting at BULLETS instead strands round
    counts
    INSIDE records that DO state a control, because it severs the canonical shape this file asks
    for — a control header with its rounds listed under it — from its own baseline. That is
    asserted in the tree-wide test at the end of this file, under five readings of "list marker",
    rather than quoted as a pair: the pair moved at every one of the five steps between the six
    shas `_paragraphs` names. Writing a blank line between two sweeps is what buys the finer
    check, and nothing here can make an author do it.
  * A clean control does not mean the round measured anything, and three other forms bound it.
    STALE BYTECODE (VMCP-135 (624)): re-measured here on CPython 3.12 with the `.pyc` header read
    directly, cache validity is the pair (source mtime in SECONDS, source size) — so a same-length
    rewrite replays the previous budget only when the mtime ALSO fails to advance a whole second,
    which is a scripted sweep's hazard rather than a hand edit's. The remedy needs the same
    correction: `PYTHONDONTWRITEBYTECODE=1` stops Python WRITING bytecode, not READING it — with a
    stale `.pyc` already on disk the round replayed the old value under that variable, and only
    deleting `__pycache__` moved it. THE MUTATION THAT NEVER RAN (VMCP-148 (646), whose WORKLOG
    records it — that card's own subject is a different defect, so the tree holds no trace):
    a tree copied with `cp -R` drags `.venv`, the ORIGINAL `src` lands earlier on `sys.path`, the
    mutation never reaches the interpreter, and control AND rounds come out green together — four
    false greens in a row. `vikunja_mcp.__file__` printed per round is what catches that; the
    control round is not, and this file does not claim otherwise. Re-measured on VMCP-177 (702),
    that one has a RUNNER-dependent edge worth knowing before reaching for the remedy: `cp -R`
    copies the editable install's `.pth`, which holds an ABSOLUTE path to the original `src`, so a
    bare `<copy>/.venv/bin/python` imports the original and the mutation is invisible — while
    `uv run` in the same copy re-syncs, rewrites that path, and the mutation lands. So the failure
    is intermittent by runner rather than constant, which is worse to diagnose and is the reason
    the `__file__` print is per ROUND and not once per stand.
    A CONCURRENT WRITER is the third member, and it is the one this list was missing when VMCP-160
    (667) hit it: an author sweeping a tree while its own second-pass auditor sweeps the same two
    files. Constructed on 702 (two processes, one tree, each mutating SKILL.md behind its own
    one-test pin) against a solo baseline of `control 0 failed` / `mutation 1 failed` per writer,
    it runs in BOTH directions and only one of them is a control's to catch. The foreign mutant
    landing under YOUR round is: 667 found the whole problem this way, and here the author's
    control round read `1 failed` while naming a clause the author never touched. YOUR restore
    landing under the auditor's round is NOT: its mutant is silently reverted, the round reads
    `0 failed` where solo it read `1 failed`, and the auditor records "this pin is blind to that
    mutation" — a false NEGATIVE that looks exactly like an honest green. Neither direction shows
    up in the usual guards: both scripts' own sha256 restore checks reported success and the
    tree's `git status` stayed clean, because a per-script guard sees only its own writes and so
    certifies a tree it did not solely own. The remedy is not a stronger control but a separate
    tree — SKILL.md's «ГДЕ он работает» gives the auditor its own `git clone --no-hardlinks`, and
    the same two scenarios split across two clones returned the solo numbers on both sides.
  * It is a RATCHET, not a retrofit. `LEGACY_RECORDS_WITHOUT_A_CONTROL_COUNT` names the PARAGRAPHS
    that already quote a count without one. They are deliberately NOT "fixed": the control that
    belongs beside a historical number is the one measured in THAT environment at THAT sha, and
    it is unrecoverable. Writing today's `0 failed` next to a number measured last week would be
    a fabricated measurement — precisely the defect cards 646/655/663/674 exist to remove. So the
    list records which numbers this SCANNER cannot see a baseline for — narrower than
    "uninterpretable", since five of its entries, in three records, DO state one in a form the
    pattern cannot read (the list's own comment names all three). It may only SHRINK, and there
    are TWO routes, not one: re-MEASURING a sweep and its control, which is the only route for a
    historical number whose environment is gone; or, where the record already states a control the
    pattern cannot reach, moving that sentence into the paragraph that needs it — no new
    measurement, nothing fabricated. That route reaches the THREE QUANTIFIER rows the list's own
    comment names as able to leave TODAY — and not all five of the entries
    whose records state something unreadable, because the other two would need a REWORDING rather
    than a move. Saying only the first route exists would misdescribe those three.
    It GREW once, when 688 changed the unit under it — a rekey rather than a loosening, and the
    same prose is covered either way.
"""
import ast
import itertools
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
TESTS_DIR = REPO_ROOT / "tests"
SRC_DIR = REPO_ROOT / "src"
# BOTH halves of the package's python, since VMCP-187 (712) — see the SCOPE comment below for what
# widening it cost and why the argument that kept it at `tests/` had expired.
_SCANNED_DIRS = (SRC_DIR, TESTS_DIR)

# SCOPE: `src/` AND `tests/`, and the widening is VMCP-187 (712) — the card the sentence that used
# to stand here handed the decision to. What it decided is worth keeping in front of whoever
# narrows it again, because the reasoning expired before the scope did.
# Sweep records were assumed to live in the test suite's prose, and confining the scan there was
# MEASURED rather than assumed — but measured PER SCOPE, because the two scopes answer differently
# and a single "only hit outside tests/" would be false. src/: ONE hit at `d3d9ea3`, taken at 09:22
# on 2026-08-02 — the sentence in server.py describing the claimable check's EXIT CODES, not a
# pytest tally at all, and the trigger could not tell the two apart — and the argument built on it
# was that a scanner whose sole finding in the package is a false positive has no business reading
# there. RE-RUN for VMCP-167 (688) at `6dd2803`, 16:36 the SAME DAY, it was TWO, and the second is
# REAL: `claimable_cmd`'s `_Trail._emit` docstring quotes a round as a failure count with no
# control in its paragraph, so it WAS an offender the moment src/ came into scope, and it is the
# list's one src/ entry now. It landed at 16:08 (VMCP-85 / 536), between the two runs. Note WHY
# nobody caught the rot: the count carried a DATE, and it rotted inside its own date.
# WHAT THE WIDENING COST, measured on this tree before it landed rather than argued: those same
# two hits and nothing else — one real, one the known false positive. The false positive is why
# this could not be a one-line scope change: it is cut by `_ROUND_COUNT`'s third exclusion, added
# in the same commit, so the ratchet never sees it and the list never claims an exit status is a
# number without a baseline. That is the honest shape of "a behaviour change rather than a prose
# fix", which is what the old sentence called it while leaving the scan where it stood.
# And it is LOAD-BEARING rather than cosmetic, which is a round and not a claim: same
# `__pycache__`-cleared conditions and `PYTHONDONTWRITEBYTECODE=1`, the ratchet test alone,
# control 0 failed — put `_SCANNED_DIRS` back to `(TESTS_DIR,)` and it is 1 failed, the src/ entry
# in the list below no longer being an offender. So narrowing the scope again is red until the
# entry goes with it, which is the only order that keeps the list honest.
# Repo markdown is the THIRD scope and the only one still OUTSIDE, and this line is where that was
# proved twice over.
# At `6dd2803` it was five hits, every one inside a CLAUDE.md paragraph that stated its own
# control, so markdown looked clean for the same reason src/ had. It was already false when it
# shipped: `aadde71` (VMCP-166 / 687) landed a CLAUDE.md paragraph at 17:27 — between that 16:36
# run and the 17:56 commit carrying the sentence — quoting round counts with NO control in its
# paragraph, and `75a1e52` added another count to that same paragraph 49 minutes after that.
# So repo
# markdown looked like it held a real offender too, and the sole-false-positive argument looked
# gone on BOTH scopes.
# THAT LAST STEP IS WITHDRAWN, and VMCP-210 (753) is the card that withdrew it — the sentence
# graded markdown's hit by eye where src/'s was graded by opening it. Graded the same way, the
# markdown hit is a FALSE POSITIVE ON BOTH ITS NUMBERS. One paragraph is uncontrolled today, the
# stale-figure sweep block, and the two things the trigger matches in it are a figure the block
# QUOTES in order to discuss it (a wrapped one it names by line) and one it calls illustrative in
# the same breath. Neither is a round of anything; both are prose ABOUT rounds — which is the
# identical mistake the exit-code gloss made in src/, and this comment made it while explaining
# that mistake.
# So the arithmetic nobody had run comes out the other way: widening to markdown today buys ZERO
# true catches and costs one false red. That is a STRONGER reason to leave the scope where it is
# than the behaviour-change reason it replaces, and it is the reason to record, because the weak
# one invites a future card to pay a price that would buy nothing. Neither number is durable —
# re-measure both before widening, the way src/ was measured, and note that the two false
# positives here are NOT excludable at the pattern the way `ran / ` was: `5 failed / 102 passed`
# is the exact shape of a real round this file pins a row for, so the only thing separating them
# is that one is inside a quotation, which no lexical rule this scanner has can see.
# How many hits there are is deliberately not
# written here any more: that number moved twice inside one afternoon, and what a reader needs
# from it is asserted in the tree-wide test at the end of the file. WHAT it asserts is THIS
# paragraph — the line-fed one, found by an anchor phrase — and no longer the weaker "markdown is
# NOT clean" it asserted until VMCP-194 (724), which was satisfied by any OTHER uncontrolled
# paragraph of CLAUDE.md while the one named here was clean. The sentence you are reading could
# therefore go false with the guard green, and that is the state 724 was filed against.

# A ROUND COUNT: a number of failing tests. THREE forms are excluded, every one of them measured
# in this repo and every one a false positive. `(?<![:.\w])` drops a number that is part of an
# address or a longer token — `Bind for 0.0.0.0:3456 failed` in test_skill_contract quotes a
# docker error, not a round. `(?<!of )` drops a fraction-of-total — tests/integration/conftest's
# "containers: 5 of 9 failed" counts containers. `(?<!ran / )` drops an EXIT-CODE gloss, and it is
# the one VMCP-187 (712) had to add before `src/` could come into scope: server.py describes the
# claimable check as "(one JSON line on stdout, exit 0 ran / 1 failed)", where the number is a
# process exit status. That sentence is the FALSE POSITIVE the scope comment below has named since
# `d3d9ea3`, so widening the scan without excluding it would have moved a known non-round into the
# ratchet and called it a number without a baseline. Measured: `ran / <digits> failed` occurs
# exactly twice in the whole tree — that site and the pattern row pinning it — so the exclusion is
# as narrow as the two above it and reaches no shape this repo writes for a real round (`1 failed
# / 47 passed`, `control 0 failed; mutation 2 failed` and the rest are untouched, which the row
# added beside the flipped one asserts).
_ROUND_COUNT = re.compile(r"(?<![:.\w])(?<!of )(?<!ran / )\d+\s+failed\b", re.IGNORECASE)

# WHY THERE IS NO SIBLING RATCHET ON PASS TOTALS, priced rather than shrugged at — VMCP-189 (714).
# CLAUDE.md's rule is "record the FAILED count, never the pass total", and this scanner enforces
# only the control half of it. A `\d{2,} passed` trigger over the publishable tree, read WHOLE and
# whitespace-flattened so wrapped ones are reached, finds 47 sites. Twenty-one sit within sixty
# characters of a `failed` count, i.e. they are rounds stated as a pair and perfectly correct.
# Of the 26 that are bare — the class the card is actually about — a large share are legitimate on
# inspection: a total QUOTED inside a retraction of itself («stated its controls as PASS TOTALS
# "at baseline 716 passed"»), a total inside an assertion MESSAGE reporting a measured mutation,
# and a genuine `11 passed, 30 skipped` observation about a skip branch. None of those is
# separable from a live stale total by any lexical rule, which is the same wall VMCP-210 (753)
# hits one comment up and the quotation ratchet hits in its own file.
# So the gate is buildable and its ratchet would have to list over half its own hits on arrival.
# That ratio is what this card's discipline refuses to land: a gate whose false reds outnumber
# its catches is switched off by the next human, and then the rule has nothing behind it at all.
# Re-measure both numbers before revisiting — they move with every landing, and the 47/21/26 split
# is a fact about the tree that carried this comment, not about the rule.

# A CONTROL COUNT: the word `control` and a TALLY (`N failed` / `N passed`) close enough together
# to be one statement, in either order — "control 0 failed", "control 2 passed", "2 failed
# against an unmutated control round of 0". The tally is the load-bearing half. What it is chosen
# against is THIS pattern with the tally requirement replaced by a bare digit —
#     r"control\b[^.;]{0,60}?\d|\d[^.;]{0,60}?\bcontrol\b"
# — which prose using the word in a non-sweep sense can satisfy. That spelling and the four other
# readings of it are CODE, in `_WEAK_CONTROL_READINGS` near the tree-wide test, so run them rather
# than retype them; read `WHAT THE WEAKENING COSTS` BEFORE you conclude
# anything from the result: what the weakening costs is not what this comment used to say, because
# VMCP-167 (688) moved the unit under it. Measurements below are from 2026-08-02, `__pycache__`
# cleared and PYTHONDONTWRITEBYTECODE=1, and the two whose answer DEPENDS on the record unit each
# name the unit they were taken under, rather than leaving a reader to infer it.
#
# On the two "other sense" rows of the pattern test further down, that weak form accepts BOTH and
# this one refuses BOTH — that is what requiring a tally buys. Those two rows are ADAPTATIONS of
# test_api_kanban.py prose, NOT strings this repo contains; the docstring above them owns that
# measurement and quotes the real wording (one of the two is wrapped across a line break at its
# site, so it is found only with whitespace flattened — which is how both predicates here read
# prose anyway). This comment said the OPPOSITE until the round that fixed it: 889befd corrected
# "the exact strings this repo contains" THERE and asserted it HERE in the same breath, and "the
# control at the same call site" occurred 0 times outside this file at `93714d5` and still
# does at `75a1e52` (every file in the checkout bar
# `.git`, counted raw and flattened). No test caught that — a reviewer did — and the ratchet below
# would not have: it reads this comment run, but only for the SHAPE it refuses. Over `tests/unit`:
# control 0 failed; a false repo-content quotation planted in this comment -> 0 failed; another
# planted in a docstring under tests/ -> 0 failed. VMCP-171 (695) carries that class.
#
# WHAT THE WEAKENING COSTS, said flatly because this is the paragraph a later agent calibrates
# on: under the shipped unit, NOTHING THE RATCHET CAN SEE. The weak form vouches for chunks under
# tests/ that this one refuses, and NOT ONE of them quotes a round count, so both patterns yield
# the SAME offender set and no entry leaves the list. That is not a property of the one spelling
# above — five readings of "a digit near `control`" say the same thing (clause-bounded window or
# any characters, one direction or both, and the loosest "the word anywhere plus any digit
# anywhere"), and all five are asserted, not described: they live in `_WEAK_CONTROL_READINGS` and
# the tree-wide test requires EACH to give that same set AND to be genuinely looser than the
# strong form. The second half is there because the first passes trivially if a reading is ever
# tightened into the strong pattern — constructed and measured, that mutation was silent. So an
# agent who re-runs this and sees the weakening MOVE something has changed something else, and
# should find out what. HOW MANY chunks each vouches for used to stand here as a number, and it is
# gone rather than re-measured: the set includes paragraphs of this same file, so writing the
# number moved it, and it shipped stale once already.
#
# UNDER THE RECORD UNIT it cost something, and this paragraph asserted that in the PRESENT tense
# until 688's follow-up round corrected it: there, at `bba4fed`, the weak form vouched for 22
# records, exactly ONE of which quoted a round count — test_api_kanban's honest-server-paginates,
# a LEGACY entry, which therefore left the list with nobody re-measuring it — while the loosest of
# the five readings released three. That 22 carries a SHA and not merely the unit for a reason
# measured on the round after: naming the UNIT is not naming the TREE either, and the very commit
# that added this clause moved the number by adding prose to this file. The halves that did NOT
# move across `6dd2803`, `bba4fed` and `75a1e52` are the load-bearing ones — exactly one such
# record quotes a round count, and exactly one entry leaves the list. Kept as history rather than
# deleted, because the ratchet round
# below states the same fact in the past tense, and this block still stating it in the present is
# what made the correction necessary: two blocks of one file answered a measurable question
# differently, and the one calling itself the calibration point held the stale answer.
#
# `[^.;]` NARROWS the window; it does not bound it to a clause, and saying so would oversell it.
# Constructed and measured, all five accepted by the pattern below: "control PASS! ... says 0
# failed", the same with "?", with ",", with " - ", and with a LINE BREAK — because the scanner
# flattens whitespace before matching, so a bullet list is one string to it. What the exclusion
# really buys is 60 characters and a stop at `.`/`;`; a control and an unrelated number that sit
# closer than that, in the same record, still vouch for each other.
# Those five are pattern-ACCEPTANCE constructions, not mutation rounds, and the rounds that own
# this pattern live in the pattern test below against a control of 0 failed, 1 passed — stated
# here rather than left to be inferred because this paragraph is where VMCP-220 (763) landed: its
# quoted `0 failed` wraps between the `0` and the word, and until `_flat` stripped the `#` the
# scanner read it as `0 # failed` and saw no round here at all. Widening the reader made this the
# one paragraph in the whole suite that changed answer, so it is also the demonstration.
_CONTROL_COUNT = re.compile(
    r"control\b[^.;]{0,60}?\b\d+\s+(?:failed|passed)\b"
    r"|\b\d+\s+(?:failed|passed)\b[^.;]{0,60}?\bcontrol\b",
    re.IGNORECASE,
)

# A MENTION: a backticked span introduced as a QUOTATION OF TEXT rather than as a statement about a
# round — `the literal` and its siblings `the string`, `the phrase`, `the word(s)`, each followed by
# backticks. It is blanked out of BOTH predicates before either matches, and VMCP-259 (861) is the
# card. What it found is that the largest sweep record in this repo balanced on one: the comment run
# above `test_a_path_whose_FIRST_COMPONENT_looks_like_a_merge_stage_is_not_a_silent_miss` has no
# blank line and no bare `#` in it, so it is ONE paragraph, and of its three `_CONTROL_COUNT` hits
# the load-bearing one was the sentence WARNING a reader not to read a docstring's prose as a round
# — «the docstrings here contain the literal `control 0 failed`». Cross out either of that
# paragraph's two REAL controls, or both, and the ratchet stayed green; only crossing out all three
# turned it red. So a paragraph whose only control is that warning passes, which is VMCP-167 (688)'s
# own defect one level down: the immuniser is not a statement about a round at all.
#
# MASKING BOTH PREDICATES IS THE FIX AND NOT A TIDY SYMMETRY. The mention carries a tally, so
# `_ROUND_COUNT` reads it as a round just as `_CONTROL_COUNT` reads it as a baseline; blank it on
# the control side only and any paragraph that merely REPEATS the warning becomes an offender —
# and repeating it is what CLAUDE.md and this repo's sweep records prescribe. Blanked on both
# sides, a mention is neither. That is a ROUND rather than an argument: the control-side-only
# variant reddens the pattern row below AND the ratchet, which then names the paragraph you are
# reading, because it repeats the very warning it documents. Its counts sit in the pattern test's
# own record, beside that record's control.
#
# WHY NOT THE OBVIOUS RULE — two candidates, both priced rather than waved off, and both rejected
# by their price. "Do not read a control inside backticks at all" floods the ratchet, and the
# sharpest of what it floods is a real sweep record: test_workflow_gates' per-stage-ownerless-exits
# declares its baseline as «CONTROL ROUND FIRST and repeated between batches: `control 0 failed`
# every time» — a DECLARATION that happens to wear backticks, which nothing lexical separates from
# a mention except the words introducing it. Doing that to both predicates instead still costs that
# record and additionally BLINDS the scanner to rounds this repo writes inside backticks, so legacy
# entries would leave the list having gained no baseline at all — a shrink for the wrong reason,
# which is worse than the hole it closes. Both are RUN, with their counts and the paragraphs they
# name, in the pattern test's own record below, beside that record's control. The narrow form costs
# nothing measurable THROUGH THE OFFENDER-SET LENS, which is the lens it was measured through: at
# `1fb0082` the offender set is identical with the mask and without it, and at `cbc3816` it still
# is, both differences empty. Through ONE predicate the cost is not zero — the mask blanks a real
# round that sits inside a mention — but at that sha the only paragraph whose round verdict moves
# is this comment's own `A MENTION` block, where round and control move together, so the offender
# verdict does not. The two lenses agree today; that is not a reason they must. Which
# is why this exclusion is the third of its kind here rather than a new idea — `_ROUND_COUNT` above
# carries three, each cutting one named false positive and each as narrow as its name.
#
# ITS BOUNDS, all real and none closable from here — and read the list as OPEN. It said "ITS TWO
# BOUNDS" until VMCP-272 (893) built a third of a different KIND, so what was wrong is the count
# rather than either entry: both of these are UNDER-firing. The vocabulary is a LIST and a list
# rots: a mention spelled any other way is invisible to it, so the hole is narrowed rather than
# shut. And it needs the BACKTICKS — a mention written without them is not separable by anything
# this scanner can see from an author declaring a baseline in plain words. OVER-firing is the one
# that closed count omitted, and the paragraph above already names its mechanism without drawing
# the consequence: an honest record DECLARING its baseline in one of those introducing words has
# it blanked and becomes an offender. Measured at `cbc3816` on the module, a CLAUDE.md-shaped
# record with one delta row under it: baseline stated with the introducing words -> round yes,
# control no; the same baseline declared plainly -> control yes. That red is LOUD and
# self-correcting, since its author watches the ratchet redden on their own new prose, where a
# missed mention is SILENT — so this is about the list being complete, not about priority.
_MENTIONED = re.compile(r"\bthe (?:literal|string|phrase|words?)\s+`[^`]*`", re.IGNORECASE)

# How much of a record's opening text goes into its key. ONE constant for both halves of the key
# — the comment run's first line and the paragraph's first characters — because it is one
# decision: long enough that two chunks of the same record do not collide, short enough to read in
# an assertion message. Collisions are not silent either way; `_records` suffixes them loudly.
_KEY_HEAD = 48

# PARAGRAPHS whose prose has the SHAPE this scanner refuses — a round quoted as a number, with no
# control count in the SAME PARAGRAPH — as they stood when VMCP-167 (688) made the paragraph the
# unit. A SHAPE list is all it is, and the difference matters: FIVE of these entries, belonging to
# THREE records, do state a baseline in a form this pattern cannot read (test_the_degraded_stop_rule
# quotes a set-wise
# negative AND positive control; test_the_checkout_probe runs a fully controlled qualitative sweep
# and lands here only for an unrelated tally describing a CONSTRUCTED broken tree, not a mutation
# round; and the three QUANTIFIER rows below state one control for the whole section, in that
# section's SECOND paragraph — its first is the bare `--- … ---` header, which carries neither a
# count nor a control — reading "Every round quoted in this section is a FAILURE count against that
# same control of 0 failed", which is true, and which no per-count rule can carry across a
# paragraph boundary). Read it as "cannot confirm a baseline here", never as "these numbers are
# uninterpretable". See the module docstring: it may only SHRINK — and those three could leave
# TODAY, without any re-measurement, by repeating that sentence's control inside each of their own
# paragraphs. Deliberately not done here: it edits the measured prose of another card (VMCP-158 /
# 664) to satisfy a rule this card introduced, and the entry is honest as long as this comment
# names the case. Whoever wants the list shorter has that route and it fabricates nothing.
#
# It went from 7 record keys to 16 paragraph keys when the unit changed, and that recount IS 688's
# result rather than its cost. The split, COUNTED rather than reasoned — the first draft of this
# comment did the arithmetic in its head and got 8-and-1: the seven records already listed hold 13
# of the 16 paragraphs, so SIX of the nine additions sit inside them, where one grandfathered key
# was vouching for every paragraph under it, which is this card's own bug one level down. The
# other THREE are a single record that had been green outright.
# It has SHRUNK since, by the first of the two routes above: VMCP-155 (660) re-measured the
# starving base's rounds under its own control, so the `DELIBERATELY NOT PINNED` entry left. And
# since, by the SECOND: VMCP-173 (698) took the two `--- the prose's INTERPOLATED VALUES` entries
# off it WITHOUT RE-RUNNING A SINGLE MUTATION ROUND, because that record stated its controls as
# PASS TOTALS («at baseline 716 passed», «every restore returns to 721») — unreadable to the
# pattern, and the same summary lines a `control 0 failed` names. Described exactly rather than
# loosely, that was route two's MOVE for the two paragraphs holding no baseline of their own —
# the section's headline control carried down into each — plus a REWORDING of the headline itself,
# which stated its baseline as a pass total. So it is the first worked example of the rewording
# the MODULE DOCSTRING says two other entries would need, and it is NOT evidence that a rewording
# belongs to route two, which that docstring defines as a move. (698 did re-run the CONTROL at the
# base those rounds name and got the same 716 back — corroboration, not the ground the removal
# stands on: had that base been gone, the re-reading would have been just as sound.) It also means
# the enumeration here and in the module docstring — five entries in three records that state a
# baseline this pattern cannot read — was INCOMPLETE when it was written, because this record
# stated one too. Both sentences stay arithmetically true now that these two entries are gone;
# read them as a list of what was NOTICED, not of what exists. No new
# total is written here on purpose — a size stated beside the list it counts goes stale the next
# time the list does its job, which is the self-reference the scope comment above flags. What that
# landing falsified ELSEWHERE in this file was ONE sentence, and it is FIXED here rather than
# pointed at: the module docstring's «a fifth of sixteen» now names no total at all, which is this
# paragraph's own rule applied one line up instead of quoted at it. The size itself carries a SHA
# and no assert, for the reason two lines up rather than a fresh one: it moves when an ENTRY LEAVES,
# which is the list doing its job. NOT by being written about — measured, none of the entries quotes
# prose from this file. At `52d6085` it held 15 keys, against 16 at `9489de3`. That shrink
# reached NO other sixteen here, none of the rest being this list's SIZE — at `52d6085` they were
# 688's rekey (seven record keys becoming sixteen paragraph keys, a fact about that landing),
# `_paragraphs`'s «16 in 8» pinned at `6dd2803` and counting offending CHUNKS, 594's «factor of
# 16», and a grep's match count. That is a claim about the SHRINK, so it stays true however many
# sixteens land here afterwards; for the file as it stands in YOUR checkout, run
# `grep -niE 'sixteen|(^|[^0-9:.])16([^0-9:%]|$)'` over it rather than trusting the sentence you
# are reading — its first spelling trusted a list, and the list was wrong.
#
# HOW it was wrong outlives the correction, because nothing in the suite holds it. MEASURED on a
# clone of `62a8fe2` carrying this commit's edits, whole `tests/unit`, control 0 failed: putting
# the stale total back is -> 0 failed. That is why the close is a FIX and not a guard; what a guard
# here could and could not reach is the NEXT paragraph. The first spelling of this note
# named FOUR addresses and TWO of them were not in this file at all: «16-key offender set» in the
# `_CONTROL_COUNT` block, and a sixteen in the ratchet's weakening row. VMCP-167 (688) deleted both
# at `e77b0cf`, the landing immediately before this card's — only the release bot's bump `9489de3`
# sits between them — so they were carried over from a tree that still had them, and the grep was
# never re-run in the tree the note shipped in. SKILL.md calls that the INHERITED class. Be exact
# about the universal in front of that list, because it was not a plain overclaim: «every SIXTEEN …
# one too high» held of exactly ONE of the four, the docstring's, and was false of the other address
# that really exists, `_paragraphs`'s «16 in 8» — a SHA-pinned round does not go stale when a list
# it never counted shrinks. The note's own NEXT sentence said so («the last three are ROUNDS, each
# true of the tree it ran on»), so what shipped was a paragraph disagreeing with itself one clause
# later, which is a thing a reader can believe half of.
#
# THAT is why the paragraph above spends a COMMAND where this repo's rule asks for an ASSERT when a
# reader acts on a count — but NOT because no check is constructible. An independent pass built one
# and it is recorded rather than waved away: require every «…»-quoted phrase to occur MORE THAN
# ONCE in this file. Re-measured here, it is GREEN at `75a1e52`, `e77b0cf` and `9489de3` —
# VACUOUSLY, those three revisions carrying no «» at all (0, 0, 0, measured), so the greens show an
# EMPTY INPUT and not specificity — and RED at `52d6085`, naming «16-key offender set» as the
# phantom, the exact defect caught lexically. It is PARTIAL, and an earlier spelling of this
# sentence COUNTED the ways ("twice over") and undercounted — the same self-tally this file refuses
# elsewhere. It misses the other phantom of that note, which was plain prose and never quoted. It
# fires on legitimate quotes too — a phrase quoted BECAUSE it was just deleted is lexically
# identical to a phantom, and this very paragraph writes several. (No count of them here: it would
# be a tally of the file stating it, moved by the next word typed.) And it goes SILENT on a phantom
# quoted MORE than once — the mode that already cost something, since a note discussing a phantom
# quotes it again by nature: at `6dfd68b` this note carried «16-key offender set» in TWO places, so
# the check run there does not name that phrase among its violations at all, and every later
# spelling of the note — this one included — only adds occurrences. The card filed below first
# listed it among what the check flags, and carries a correction comment saying otherwise. So
# shipping it needs a retraction set of its own, the idiom this file already runs on, and it is
# filed as VMCP-195 (732) — the ref read back from `get_task(732)`, not composed: an earlier
# spelling wrote `VMCP-181`, which is a live unrelated card (id 706), because at that landing
# `file_task` returned no `ref` to echo. That cause is now CLOSED for this tool — #735 added
# `filed.ref`, so a card you FILE names itself — which is why this sentence is in the past tense.
# Not folded in here. THAT MISS IS A CLASS RATHER THAN A SLIP, and the useful
# part is its SHAPE, because the count below rots and the shape does not. In every instance found
# the NUMERIC id was RIGHT and only the human-readable half was wrong, which is not chance: that
# half is the one you have to fetch ON PURPOSE — for a card you are merely CITING it costs a
# `get_task` you have no other reason to make — so it is the half supplied from memory, and memory
# returns a plausible NEIGHBOURING index rather than a self-announcing blank. (#735 removed one
# source of that cost, not the class: a card you FILE now names itself in `filed.ref`, but a card
# you CITE, and a `decompose` child, still cost the deliberate fetch — VMCP-206 (749).)
# Swept at this landing by resolving ids through the same call the tool makes
# (`api.get_task(id)` -> `Workflow._ref`): of the 62 DISTINCT `VMCP-N (id)` pairs this repo held
# at `da3640d`, the parent of the commit that swept them, THREE disagreed. That 62 is pinned to a
# NAMED tree on purpose — citing the filed card below adds a 63rd, so a total stated here without
# its tree would be falsified by the sentence that states it, which is the self-tally this file
# refuses. It first said "BEFORE this commit", which names a tree only while "this commit" is
# unambiguous, i.e. only until the next landing (VMCP-212 (755)); a sha survives that. Re-run it
# DISTINCT, as the word now says: the raw OCCURRENCE count on that same tree is 187, not 62.
# Two of the three are this
# file's and are corrected here — `VMCP-181` -> VMCP-195 above, and VMCP-172 -> VMCP-187 at both of
# its sites. THE THIRD WAS LEFT STANDING here, deliberately: it was in test_skill_contract.py,
# another card's slice at the time, and was filed as VMCP-203 (745) — its index was one off, so it
# named a live UNRELATED card rather than reading as broken. VMCP-228 (772) has since corrected it
# in that file. That does NOT make this paragraph "the repo is clean": the class it reports on is
# bounded by the spelling its grep matched, which is the point the next paragraph makes and the
# over-reading a swept-and-reported class always invites.
# AND THE SWEEP COVERED ONE SPELLING, not the class. `VMCP-N (id)` is not the only form: this file
# also writes `VMCP-N / id`, which that grep does not match, and a fourth instance was sitting FOUR
# LINES above the first correction — the scope comment, which paired id 536 with `VMCP-91` where
# 536 is VMCP-85, the breadcrumb card it is actually about, while VMCP-91 is live and unrelated
# (id 547). Corrected too, and named here because it is the measurement that keeps the framing
# honest: a sweep bounded by a regex reports on the regex, not on the class. (The composed form is
# described rather than reproduced, so the re-run below does not rediscover this sentence.)
# Re-run both forms rather than trusting either count, which the next landing
# moves:
# `git grep -ohE 'VMCP-[0-9]+ \([0-9]+\)' | sort -u` and `git grep -nE 'VMCP-[0-9]+ ?/ ?#?[0-9]{3,}'`,
# then resolve each id. How such a re-run lies if done carelessly, met here rather than imagined:
# from a LINKED worktree every fetch 401s, because the gitignored env file sits beside the toml in
# the MAIN checkout only, and a swallowed exception then renders those 401s as "nothing found" —
# true by the numbers, false in meaning, which is the shape this whole note is about.
# What stays genuinely unassertable is the SIZE question:
# «is this sixteen the list's SIZE?» is semantic, not lexical — the same token spells an inflation
# factor, a grep's match
# count and a chunk tally in this very file — so a regex could only pin HOW MANY `16`s exist, which
# says nothing about staleness and which any neighbouring landing moves. Line numbers are worse:
# this note sat at line 258 at `52d6085` and at 279 by `62a8fe2`. And either would be a counter
# counting the file it is written in, the self-reference this file refuses by name in several places
# already. Fixing the one stale sentence leaves a reader nothing to ACT on, so the branch of the
# rule that applies is the other one — history, with a sha — and the command is there for the reader
# who wants to check rather than to act.
#
# Nothing here moves what any of those rounds CONCLUDED: no entry leaves under the weakened
# pattern, and the entries this list's own comment names can still go without any new measurement —
# deliberately not re-counted here, since that count is the comment's to state and to move. The
# sentences
# about what 688's rekey PRODUCED are history, and stay true whatever the list's size does
# afterwards.
LEGACY_RECORDS_WITHOUT_A_CONTROL_COUNT = frozenset({
    "tests/unit/test_api_kanban.py::_serving_lengths"
    "::¶sweep 2's own page draw widened to -> 1 failed /",
    "tests/unit/test_api_kanban.py::test_the_degraded_stop_rule_does_not_depend_on_bucket_order"
    "::¶WHAT IT PINS NOW — MEASURED, not argued (VMCP-12",
    "tests/unit/test_api_kanban.py::test_neither_read_loses_a_task_an_honest_server_paginates"
    "::¶THE COMPLETENESS LINE IS VMCP-130 (616)'s, AND W",
    "tests/unit/test_api_kanban.py::test_neither_read_loses_a_task_an_honest_server_paginates"
    "::¶keep_going = False -> 1 failed. `assert healthy ",
    "tests/unit/test_api_kanban.py::test_neither_read_loses_a_task_an_honest_server_paginates"
    "::¶`_offset_pages` over-serving by +3 on EVERY page",
    "tests/unit/test_repo_browser_isolation.py"
    "::test_the_checkout_probe_is_not_an_off_switch_for_a_broken_git"
    "::¶The last two assertions need a broken TREE rathe",
    "tests/unit/test_workflow_sequence_gate.py::comments-above:_blocker_moved_to_backlog"
    ":--- the starving-tail message is the plain tail "
    "::¶WHY THIS STRING AND NOT THE OTHER TEN. #586 meas",
    "tests/unit/test_workflow_sequence_gate.py::comments-above:_blocker_moved_to_backlog"
    ":--- the starving-tail message is the plain tail "
    "::¶AND WHY THE PIN SPANS TWO ENVS (VMCP-146 / #635)",
    "tests/unit/test_workflow_sequence_gate.py::comments-above:_blocker_moved_to_backlog"
    ":--- the starving-tail message is the plain tail "
    "::¶WHAT THE SECOND ROW DOES NOT BUY, so it is not o",
    "tests/unit/test_workflow_sequence_gate.py::comments-above"
    ":test_a_tail_needs_retriage_when_ANY_of_its_blockers_sits_in_backlog_not_when_all_do"
    ":--- the QUANTIFIER over a tail's blockers (VMCP-"
    "::¶WITH THE ENV, the same `all` mutant is **2 faile",
    "tests/unit/test_workflow_sequence_gate.py::comments-above"
    ":test_a_tail_needs_retriage_when_ANY_of_its_blockers_sits_in_backlog_not_when_all_do"
    ":--- the QUANTIFIER over a tail's blockers (VMCP-"
    "::¶WHAT THE ENV BUYS BEYOND THE QUANTIFIER, as a bo",
    "tests/unit/test_workflow_sequence_gate.py::comments-above"
    ":test_a_tail_needs_retriage_when_ANY_of_its_blockers_sits_in_backlog_not_when_all_do"
    ":--- the QUANTIFIER over a tail's blockers (VMCP-"
    "::¶AND WHAT THESE ROWS ARE NOT EXCLUSIVE ABOUT, sta",
    "tests/unit/test_workflow_wip.py::comments-above:_clause_free_base"
    ":--- the free == 0 note is the base plus the ENUM"
    "::¶The base is READ from the other env rather than ",
    # The list's ONE `src/` entry, and the whole cost of VMCP-187 (712) bringing that half of the
    # package into scope. `_Trail._emit` records a real round — a binding mutation that turned the
    # file RED — as a bare tally with no baseline in its paragraph, which is exactly what the scope
    # comment at the top of this file has said about it since `6dd2803` while the scanner could not
    # reach it. Grandfathered rather than "fixed" for the module docstring's standing reason: the
    # control that belongs beside it is the one measured in THAT environment, and writing today's
    # number there would fabricate a measurement. The OTHER src/ hit the scope comment counted is
    # not here and never will be — it is server.py's exit-code gloss, a false positive that
    # `_ROUND_COUNT`'s third exclusion now cuts before the ratchet ever sees it.
    "src/vikunja_mcp/claimable_cmd.py::_Trail._emit"
    "::¶(a) `sys.stderr` is resolved per WRITE, so a cal",
})


def _docstrings(path: Path, source: str):
    """Every docstring in a module, keyed by its dotted qualname.

    A bare `node.name` would collide between a nested helper and its host, and a collision here is
    worse than a miss: two records would share one key and the ratchet could not name either.
    """
    def walk(node, prefix):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                qualname = f"{prefix}{child.name}"
                doc = ast.get_docstring(child, clean=False)
                if doc:
                    yield qualname, doc
                yield from walk(child, f"{qualname}.")

    module_doc = ast.get_docstring(ast.parse(source), clean=False)
    if module_doc:
        yield "<module>", module_doc
    yield from walk(ast.parse(source), "")


def _comment_runs(source: str):
    """Every maximal run of consecutive `#` lines, keyed by the `def` it stands above.

    Sweep records are not only docstrings: three of the biggest in this repo are banner comments
    above a group of tests (test_workflow_sequence_gate, test_workflow_wip). Keying by LINE NUMBER
    would make the ratchet break on any edit above it; keying by the following definition survives
    the file moving under it, which is the whole point of a list that must stay accurate.

    The run's own opening line is the second half of the key, and it is not decoration — it closes
    a HOLE the first half had alone, found by construction rather than by reading. Several runs
    stand above the SAME definition, and keying on the definition alone collapsed them: at
    `889befd`, 961 records shared 790 keys, 72 of them colliding, and all three banner entries
    in the legacy list sat on colliding keys (`comments-above:_spelled_ref` covered SIX runs).
    Because the offender set is a set of KEYS, a grandfathered key vouched for every run beneath
    it. Measured from a control round of 0 failed, 1 passed, on the ratchet test below: the
    IDENTICAL new uncontrolled banner `# MUTATION-CHECKED: drop the guard -> 2 failed` gave
    1 passed inserted above `_spelled_ref`, and 1 failed inserted above a definition with no legacy
    entry. That is the regression this file exists to stop, shipping green in exactly the places
    most likely to grow another banner. `_docstrings` above calls a collision worse than a miss;
    this is what that looks like when it happens. With the opening line in the key both
    constructions give 1 failed, and reverting the key to the definition alone turns the ratchet
    red — so the second half is load-bearing, not belt-and-braces. The 961 / 790 / 72 above belong
    to a SHA rather than to a date, and the sentence that first said so proved it by failing at it:
    it re-ran the three on "688's follow-up tree the SAME day", wrote 1098 / 870 / 83, and shipped
    inside a commit where they were already 1111 / 881 / 85, because VMCP-166 (687) landed between
    the run and the push. Naming the day is not naming the tree. The three belong to `6dd2803`;
    they are 1111 / 881 / 85 at `bba4fed` and 1120 / 889 / 86 at `75a1e52`. What a reader acts on
    is not any of those numbers but that def-only keying STILL collides, and that is asserted in
    the tree-wide test at the end of this file. The SIX did not move, and neither did the
    conclusion resting on it, which is the half that was load-bearing.

    EVERY NUMBER ABOVE IS UNDER THE RECORD UNIT, which VMCP-167 (688) replaced with the paragraph,
    so the conclusion survives and its REASON does not. Re-measured against a control of 0 failed
    over the whole scanner file: that same banner above `_spelled_ref` is 1 failed WITH
    the opening line in the key and 1 failed WITHOUT it, and is NAMED both times — the paragraph
    half of the key now does the disambiguating that the opening line used to do alone, so the
    "1 passed" above is history rather than current behaviour. Dropping the opening line is still
    1 failed, but because the legacy entries sitting on comment runs change key at once — TEN of
    them on the tree that round ran on, `e77b0cf`, and nine at `eb14eb4` after VMCP-155 (660)'s
    entry left — not because a regression is swallowed. What the opening line alone still separates
    is narrower than the round above measured: two runs over ONE definition whose corresponding
    PARAGRAPHS agree. And that shape has
    NO instance on this tree — dropping the opening line and keying by definition plus paragraph
    head still leaves ZERO collisions, which the tree-wide test at the end of this file asserts —
    so what it separates today is nothing, which is the honest price of keeping it. The key COUNT
    that stood beside that zero is gone rather than re-measured, and it is the sharper half of the
    lesson above: it read 1761, and 1761 is the count of no committed tree in the window it could
    have been taken in. Run at EVERY commit from `94bae3d` to `75a1e52` it goes 1742, 1760, 1789,
    1793, 1794, 1812, 1815, 1817 — never 1761 — because it was taken in the uncommitted working
    tree the round ran in. No SHA could have saved that number; there is none to name. The ZERO
    beside it held at every one of those same commits, which is why the property is the half worth
    keeping and is asserted rather than counted. Kept anyway, because it costs one string and
    re-keying the list a second time in one commit buys less than it risks.
    """
    lines = source.splitlines()
    defs = [
        (n.lineno, n.name)
        for n in ast.walk(ast.parse(source))
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    ]
    defs.sort()

    def following(line_no: int) -> str:
        for lineno, name in defs:
            if lineno > line_no:
                return name
        return "<end of file>"

    def key(run: list[str], start: int) -> str:
        opening = " ".join(run[0].lstrip().lstrip("#").strip().split())[:_KEY_HEAD] or "<blank>"
        return f"comments-above:{following(start)}:{opening}"

    run: list[str] = []
    start = 0
    for i, line in enumerate(lines, 1):
        if line.lstrip().startswith("#"):
            if not run:
                start = i
            run.append(line)
            continue
        if run:
            yield key(run, start), "\n".join(run)
            run = []
    if run:
        yield key(run, start), "\n".join(run)


def _paragraphs(prose: str):
    """Every paragraph of a record: a maximal run of lines that are not blank.

    THE UNIT OF THE WHOLE SCANNER, and VMCP-167 (688) is the card that moved it here from the
    whole record. A predicate that runs over an entire docstring answers "is there a control
    SOMEWHERE in this", so one qualifying sentence immunises every other count in it — and the
    proof was not constructed, it was the live record that made a human file 668: its whole-file
    kill count (quoted there as a failure tally over 102 passing tests, and deliberately not
    reproduced here, because it is the number with no baseline) had none in its own paragraph, and
    the docstring passed anyway on the strength of a control clause 27 lines below about a
    DIFFERENT mutation. The failure mode concentrated exactly where the payoff was meant to be,
    since the long multi-mutation records are both the ones most likely to hold one qualifying
    sentence and the ones most worth reading.

    A BARE `#` LINE IS BLANK, and that conjunct is what makes this work on comment runs, which are
    half of this repo's biggest sweep records (test_workflow_sequence_gate, test_workflow_wip). A
    run has no blank lines at all — `#` alone is its paragraph separator, and this file's own
    comment above `_CONTROL_COUNT` is written that way, its paragraphs divided by nothing else.
    That sentence used to carry the COUNT of them, and the count is gone for the reason the module
    docstring's AUTHOR-CONTROLLED bullet gives: it described text in this same file, so the very
    edit that corrected that comment falsified it — four became five in the commit that noticed.
    Without the conjunct every banner keeps the broken granularity: at `6dd2803`, splitting
    docstrings only gives 9 offending chunks in 7 records, and counting `#` too gives 16 in 8,
    the extra record being one that was green outright. That pair carries a SHA and not a date
    for the reason the module docstring now states as a rule; the PROPERTY under it — that a bare
    `#` separates paragraphs at all — is asserted in the paragraph test below, where dropping the
    conjunct is a mutation round rather than a number.

    THE CONJUNCT IS NOT SCOPED TO COMMENT RUNS, though it is only USEFUL there, and the difference
    is a false-positive channel rather than a tidy detail. This function does not know which
    extractor produced its argument, so a DOCSTRING whose line is nothing but `#` splits there
    too. Measured both halves: zero docstrings under tests/ contained such a line at `6dd2803`,
    and none do at `75a1e52` either, so nothing is currently affected; and a constructed one does
    split, its controlled half reading True and its severed tail False. Scoping the rule would
    need the record kind threaded in — deliberately
    not done for a channel with no occurrences, but it is a channel, and "a record written without
    blank lines is one chunk" is false in exactly this corner.

    NOT BULLETS, and that is a measurement rather than a preference. Splitting at list markers as
    well strands round counts INSIDE records that DO state a control, because the canonical shape
    this file asks authors for is a control header with its rounds listed under it —
    `Control round: 0 failed, 1 passed.` then `* drop the guard -> 1 failed` — and a bullet split
    severs every one of those from its baseline. The decision is pinned twice in tests rather than
    resting on the pair it used to be quoted as: by the canonical-shape assert in the paragraph
    test below, and by the tree-wide test at the end of the file, which requires a bullet split to
    strand STRICTLY MORE than this unit does under every one of five readings of "list marker".
    The pair itself survives only as a trajectory with a SHA against each point, because it is
    self-referential in the way the scope comment above warns about — this file's own bulleted
    MUTATION-CHECKED blocks are counted by it, so writing the sentence moves it. On one reading
    throughout: `889befd` (656's THIRD commit) 25/12, `93714d5` (its FOURTH) 27/14, `94bae3d` (the
    tree this card started from) 40/27, `6dd2803` (this card's first commit) 49/36, `bba4fed` (its
    second) 53/40, `75a1e52` 54/41 — every one of the five steps moved it. An ORDINAL is not a
    SHA: the version of this sentence that said "656's second commit" and "its third" was wrong by
    one. `d3d9ea3` gives 23/10 on the four readings and 24/11 on the fifth, so NO reading of it
    yields the 25/12 the pointer promised, and a reader following that pointer gets a mismatch he
    cannot diagnose — he cannot tell a wrong ruler from a wrong tree. The 20/11
    this sentence once labelled "before this card" was the FILING agent's figure, taken at another
    sha and, by that agent's own worklog, probably under another marker definition; it reproduces
    at none of these, so it is dropped rather than re-dated — a trajectory is only a trajectory if
    every point is on one ruler. And the readings COLLIDE ACROSS SHAS, which is the concrete reason
    a date cannot stand in for a sha here: four readings give 53 at `bba4fed`, which is exactly
    what the FIFTH reading gives at `6dd2803`, so a reader who re-runs, gets 53 and has only the
    day cannot tell which of the two he reproduced. That fifth reading, which needs no space after
    the marker, runs 26/13, 31/18, 43/30, 53/40, 57/44, 58/45 over the same six shas.

    THE PARAGRAPH KEEPS THAT BLOCK WHOLE, which is why it is the unit — but the honest version of
    that sentence is narrower than "the coarsest split that separates two sweeps", which is what
    it said first and which this file's own module docstring refutes: `WHAT IT CANNOT ENFORCE` is
    ONE paragraph holding several distinct sweeps. A blank line is what separates two sweeps, and
    only an author puts one there. This unit is the coarsest split that separates them WHEN THE
    AUTHOR HAS, and the finest that does not sever a control header from its own rounds.

    THE COST, priced rather than waved through: a control now has to sit in the SAME paragraph as
    the count it vouches for, so a section-wide control header followed by a blank line no longer
    reaches the rounds below it. That shape exists in this repo — one record, three paragraphs,
    listed in the ratchet with its wording quoted — and CLAUDE.md now asks for the control beside
    the count for exactly this reason.
    """
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


def _paragraph_head(paragraph: str) -> str:
    """The paragraph's opening text, for its key — flattened, `#` markers dropped, truncated.

    A POSITION would have been the obvious key and is the wrong one for the same reason
    `_comment_runs` refuses a line number: inserting one paragraph would renumber every paragraph
    below it, so the ratchet would break on edits that changed nothing it is about. The opening
    text survives the record moving under it and, unlike an index, says in the failure message
    WHICH paragraph has no baseline.
    """
    flat = " ".join(line.strip().lstrip("#").strip() for line in paragraph.splitlines())
    return " ".join(flat.split())[:_KEY_HEAD] or "<blank>"


def _records():
    """(key, prose) for every PARAGRAPH of every docstring and comment run under tests/.

    Distinctness is load-bearing rather than tidy: the offender set is a set of keys and the legacy
    list suppresses BY key, so two records under one key mean a grandfathered entry vouching for
    the other — measured, and written up in `_comment_runs`. Each extractor avoids its own
    collisions (dotted qualnames there, the opening line here) and `_paragraph_head` separates the
    paragraphs within one record; this counter is the backstop for what none of them rules out, a
    redefined function name, or two chunks under one record whose first 48 characters agree. It
    counts the FINAL key deliberately, so a collision BETWEEN PARAGRAPHS of one record is named
    rather than merged. What that is worth was measured, and it is narrower than either of this
    sentence's two earlier drafts — one said the alternative left the split "unguarded", the other
    said it goes red; both were written from reasoning. On today's tree the placement is
    INVISIBLE: from a control round of 0 failed, moving the counter up to the record key
    is 0 failed, because no record here has two paragraphs whose first 48 characters agree.
    CONSTRUCT one — a docstring carrying the same uncontrolled paragraph twice — and the
    difference shows up without changing the verdict: both placements are 1 failed, but the
    final-key counter names TWO offenders, the second wearing the loud `#2`, while the record-key
    counter names ONE. That is what it buys, and it is not nothing: the ratchet suppresses BY KEY,
    so a merged pair is a grandfathered entry vouching for a paragraph nobody listed — this
    card's own bug one level further down.
    """
    for path in sorted(p for d in _SCANNED_DIRS for p in d.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        relative = path.relative_to(REPO_ROOT).as_posix()
        seen: dict[str, int] = {}
        for key, text in itertools.chain(_docstrings(path, source), _comment_runs(source)):
            for paragraph in _paragraphs(text):
                full = f"{relative}::{key}::¶{_paragraph_head(paragraph)}"
                seen[full] = seen.get(full, 0) + 1
                yield (full if seen[full] == 1 else f"{full}#{seen[full]}"), paragraph


def _flat(prose: str) -> str:
    """A paragraph as ONE line, each line's leading `#` dropped BEFORE the join — VMCP-220 (763).

    The `#` strip is the whole of 763 and it is not cosmetic. `_paragraph_head` three functions
    down has always stripped it; the two predicates below did not, so they joined a comment run's
    lines with the marker still on them and a round whose digits and the word `failed` fell on
    either side of a line break read as `102 # failed` — which `_ROUND_COUNT` cannot match. The
    scanner was therefore blind to exactly the shape CLAUDE.md warns the OTHER sweep about one
    section over ("a sweep must not be LINE-FED"), in the file that owns that warning's pin.

    Measured over all of `tests/` before the change: 13 offending paragraphs with the old
    flattener, 14 with this one, the difference being ONE paragraph — the `[^.;] NARROWS the
    window` block of this file's own `_CONTROL_COUNT` comment, whose constructed example wraps
    between its `0` and its `failed`. So the hole was real, was one wrap away from any comment
    run in the suite, and its only live instance was here. That paragraph now names the control
    those constructions belong to, which is why widening the reader cost no red.

    Whitespace is collapsed AFTER the join for the reason test_repo_quotation_claims records
    about its own pair: the bridging comes from joining STRIPPED lines with a single space, not
    from collapsing runs of spaces, and the two are separable.

    MUTATION-CHECKED, `__pycache__` deleted per round then `PYTHONDONTWRITEBYTECODE=1`, the
    ratchet test alone as the selection, every round restored from a COPY and the restore
    confirmed by sha256 and by returning to the control. Control round: 0 failed.
      * REVERTING this function to the old `" ".join(prose.split())` -> 0 failed, and that is the
        round to read first, because it says the widening has no live target left: the one
        paragraph it newly reached now states its control, so both flatteners agree on today's
        tree. A widening proved only by its own leftover offender proves nothing
      * so the proof is CONSTRUCTED, and it is the pair that matters. Plant an uncontrolled round
        in a comment run of test_workflow_wip.py with the digits and the word on either side of a
        line break (`-> 2` ending one line, `failed` opening the next) -> **1 failed** with this
        flattener and **0 failed** with the old one, same plant, same selection, same control.
        That is the class becoming inexpressible: before 763 an author could wrap a comment run
        anywhere and the ratchet went quiet
    """
    return " ".join(
        " ".join(line.strip().lstrip("#").strip() for line in prose.splitlines()).split()
    )


def _without_mentions(flat: str) -> str:
    """Flattened prose with every MENTIONED span blanked — VMCP-259 (861).

    Blanked to filler of the SAME LENGTH rather than deleted, because both patterns are
    distance-bounded (`[^.;]{0,60}`): removing characters would pull a control and an unrelated
    tally closer together and could vouch where the unmasked text does not, which is the one
    direction a fix for an over-vouching defect must not travel. The filler is a non-digit,
    non-word character, so it neither spells a tally nor bridges a `\\b`.

    That is now a RUN and not only a reason. Deleting instead of filling shipped GREEN over the
    whole suite until VMCP-273 (894); it now reddens
    `test_a_mention_is_blanked_to_ITS_OWN_LENGTH_and_never_deleted`, whose record carries both
    pairs. Do not simplify this line back without reading it.
    """
    return _MENTIONED.sub(lambda m: "·" * len(m.group(0)), flat)


def _quotes_a_round_count(prose: str) -> bool:
    return bool(_ROUND_COUNT.search(_without_mentions(_flat(prose))))


def _states_a_control_count(prose: str) -> bool:
    return bool(_CONTROL_COUNT.search(_without_mentions(_flat(prose))))


def test_a_sweep_record_that_quotes_a_failure_count_states_its_control_count():
    """The rule with teeth: a number of failures is a kill count only against a measured baseline.

    The ratchet is compared for EQUALITY, not containment, and both directions are load-bearing. A
    NEW record without a control is the regression this card exists to stop. A LEGACY record that
    grew a control (or was deleted, or renamed) must leave the list in the same commit — a
    grandfather list nobody prunes is how a guard turns into decoration, and this one names the
    records whose numbers a reader should not trust, so a stale entry misinforms.

    MUTATION-CHECKED, `PYTHONDONTWRITEBYTECODE=1`, exactly 1 test selected per round, every round
    restored with `git checkout --` and the restore confirmed by re-running to the control. Control
    round: 0 failed, 1 passed.
      * append a NEW record quoting `-> 2 failed` with no control (a docstring in this file, then
        a `#` banner above a test in test_workflow_wip.py, to exercise both extractors)
        -> 1 failed each, the message naming the new key
      * append a new record quoting `-> 2 failed` WITH `control 0 failed` -> 0 failed, 1 passed:
        the rule accepts what it asks for, so it is not a ban on numbers
      * append a new record quoting `-> FAIL; control PASS` (no digits at all)
        -> 0 failed, 1 passed: qualitative rounds keep their qualitative control
      * append a new record quoting `-> 2 failed` with `control PASS` -> 1 failed, which is the
        card's whole thesis: the weak form must not satisfy the numeric one
      * drop one entry from LEGACY_RECORDS_WITHOUT_A_CONTROL_COUNT -> 1 failed, and add a
        non-existent one -> 1 failed, so the list cannot rot in either direction
      * weaken `_CONTROL_COUNT` to "a digit near the word control" -> 0 failed HERE, and that is
        a redundancy VMCP-167 (688) COST rather than a round that passed. Under the record unit
        it was 1 failed on the SECOND assert, because one legacy entry (test_api_kanban's
        honest-server-paginates) stopped being an offender under the looser regex. Re-measured at
        paragraph granularity, same selection of one test: the weak and the strong pattern give
        the IDENTICAL offender set, so the ratchet cannot see the weakening at all. It
        still vouches for paragraphs this pattern refuses, none of which quote a round count,
        which is exactly why none of them reach the list. Their COUNT is not written down anywhere
        any more, here or in the `_CONTROL_COUNT` comment: it counts paragraphs of this file, so
        writing it moves it, and it shipped stale once — 29 against a measured 30, beside a comment
        that still described the RECORD unit in the present tense. The identity of the two sets is
        asserted in the tree-wide test at the end of this file, which is what this row was ever
        about. So the tally
        requirement is now held by ONE test instead of two, the pattern test below, which is its
        proper owner: re-measured for this correction against a control of 0 failed, 1 passed on
        each selection alone, the same weakening is 0 failed on the ratchet and 1 failed there, on
        the two rows that use the word in another sense. Written
        down rather than dropped, because a redundancy that vanishes quietly is how a guard turns
        into decoration
      * key a comment run by its definition ALONE, dropping the opening line -> 1 failed, every
        legacy entry that sits on a comment run changing key at once (ten of them on the tree that
        round ran on; the count is `_comment_runs`'s to carry, with a sha, and VMCP-194 (724) put
        one there after finding it stale in two places). The SECOND half of this row no longer holds, and is
        corrected here rather than left standing: under the record unit the same new uncontrolled
        banner above `_spelled_ref` was 1 failed WITH the opening line in the key and 1 passed
        without it, swallowed by a grandfathered key. Re-measured at paragraph granularity it is
        1 failed BOTH ways and NAMED both times, because the paragraph head now does that
        disambiguation. The opening line still earns its place for the shape it alone separates —
        two runs above ONE definition whose corresponding paragraphs agree — but it is no longer
        what catches this construction

    RE-MEASURED FOR VMCP-167 (688), which moved the unit from the whole record to the PARAGRAPH.
    A new selection means a new control, so these rounds do not share the one above: the whole
    scanner file as the selection, `__pycache__` deleted per round, `PYTHONDONTWRITEBYTECODE=1`,
    every round restored from a COPY and the restore confirmed by returning to the control.
    Control round: 0 failed.
      * install the pre-image of the docstring VMCP-161 (668) was filed against -> 1 failed,
        naming its `IT GOES THROUGH _flat FOR ITS HARNESS CAP` paragraph. The SAME file under the
        pre-688 record unit, against that scanner's own control of 0 failed, is 0 failed. That
        pair is the card: not an argument about granularity but a record the scanner could not
        see, and the human who filed 668 could
      * add a NEW uncontrolled round to a comment run ALREADY in the ratchet (`_spelled_ref`'s)
        -> 1 failed, naming the new paragraph; the identical construction under the pre-688
        record unit is 0 failed, because one grandfathered RECORD key vouched for every paragraph
        beneath it. Being on the list used to buy silence for prose written afterwards
      * `_records` stops splitting, i.e. back to the record unit -> 1 failed on the FIRST assert:
        every key reverts to a record-shaped one the list does not name
      * a bare `#` line stops counting as blank, so comment runs stay whole -> 2 failed, here and
        on the paragraph pin below. Without that conjunct the split reaches docstrings only, and
        this repo's biggest sweep records are banner comments
      * `_paragraph_head` collapsed to a constant -> 2 failed, the message carrying the loud
        `#2`/`#5` suffixes `_records` appends rather than merging the paragraphs silently
      * the four appended-record rounds above were re-run under this unit and are unchanged:
        1 failed uncontrolled, 0 failed with `control 0 failed`, 0 failed for `-> FAIL; control
        PASS`, 1 failed for `control PASS` beside a number
    """
    offenders = sorted(
        key for key, prose in _records()
        if _quotes_a_round_count(prose) and not _states_a_control_count(prose)
    )
    new = sorted(set(offenders) - LEGACY_RECORDS_WITHOUT_A_CONTROL_COUNT)
    fixed = sorted(LEGACY_RECORDS_WITHOUT_A_CONTROL_COUNT - set(offenders))

    assert not new, (
        f"{new} quote a mutation round as a NUMBER of failures without stating the CONTROL "
        "round's number of failures IN THE SAME PARAGRAPH. `N failed` is a kill count only if the "
        "SAME selection failed zero times unmutated, and a -q summary line cannot tell you that: "
        "card 594 read six rows straight off one in a tree with 30 constant failures and was "
        "wrong by 16x. Run the unmutated round first and write its FAILED count into the record "
        "next to the mutation's — `control 0 failed; <mutation> -> 2 failed`. Not the pass total: "
        "that moves with every test the repo adds. `control PASS` is fine for a round reported as "
        "`-> FAIL`, and not for one reported as a number. The key names the paragraph, and the "
        "paragraph is the unit (VMCP-167 / 688): a control stated once for a whole section does "
        "NOT reach the rounds below the next blank line, so repeat it or close up the blank line. "
        "The rule is CLAUDE.md's Testing Philosophy"
    )
    assert not fixed, (
        f"{fixed} are listed in LEGACY_RECORDS_WITHOUT_A_CONTROL_COUNT but are no longer "
        "offenders — they gained a control count, or were renamed, moved or deleted, or their "
        "paragraph was re-wrapped so its first 48 characters changed. Take them out of the list "
        "in the same commit. The list is not a suppression file: it is the standing statement of "
        "which numbers in this suite have no measured baseline, so an entry that no longer "
        "applies misleads the next reader"
    )


def test_the_scanner_tells_a_pytest_tally_from_the_other_numbers_in_this_repo():
    """What the two patterns accept and refuse, pinned on prose rather than on the live suite.

    Without this the scanner's precision is invisible: the test above passes on today's tree for
    any regex that happens to reproduce today's offender set, including a `\\d+ failed` with no
    exclusions at all (which would then report a docker error message and an exit code as sweep
    records) and including one that never matches (which would report nothing, forever). The rows
    below are of two kinds, and saying which is the correction of an earlier version of this
    sentence that called them all repo strings. Some occur in this repo word for word, two more
    are ADAPTATIONS of real ones (test_api_kanban's "a positive control at the same call site" and
    "The control: page 1 serving EXACTLY the stated 5"), and the rest are constructed to pin the
    pattern at its edges, which is what a pattern test is for. HOW MANY of each is deliberately no
    longer written down, and that is this file's own rule rather than a shrug: the number counts
    the rows of the table it sits above, so every card that pins one more edge falsifies it —
    which is what VMCP-187 (712) did to the "5 of the 13" that stood here, adding a fourteenth row
    without touching a word of the sentence counting them.

    MUTATION-CHECKED, `PYTHONDONTWRITEBYTECODE=1`, exactly 1 test selected per round, restores
    confirmed by re-running to the control. Control round: 0 failed, 1 passed.
      * drop `(?<![:.\\w])` from `_ROUND_COUNT` -> 1 failed on the docker-port row
      * drop `(?<!of )` -> 1 failed on the containers row
      * make `_CONTROL_COUNT` accept any digit near `control` -> 1 failed on the two rows that
        use the word in another sense. Those two rows exist BECAUSE of this round: the first
        version of this test claimed the weakening was caught by the `control PASS; -> 2 failed`
        row, ran it, and got 0 failed — that row is refused by the weak pattern too. The claim
        was true about the ratchet test above and false here, and only running it said so
      * widen `[^.;]` to `.` in `_CONTROL_COUNT` -> 1 failed, reported on the `control PASS; drop
        the rule -> 2 failed` row and NOT on the different-sentences row: both flip, and the loop
        asserts in order, so it stops at the first. Re-measured after an earlier version of this
        line named the later one of the same record
      * widen the 60-character window to 200 -> 1 failed on the long-clause row
      * delete either direction of the `_CONTROL_COUNT` alternation -> 1 failed, so both the
        "control first" and "count first" phrasings this repo actually uses stay covered

    RE-MEASURED FOR VMCP-187 (712), which brought `src/` into the scan and had to add a third
    exclusion to do it. A new selection means a new control, so these rounds do not share the one
    above: exactly this test, `__pycache__` deleted per round and `PYTHONDONTWRITEBYTECODE=1`,
    restores confirmed by returning to the control. Control round: 0 failed, 1 passed.
      * drop `(?<!ran / )` from `_ROUND_COUNT` -> 1 failed on the exit-code row, which is the row
        that flipped: it used to assert the trigger CANNOT tell a process exit status from a
        round, and now asserts it can for this one spelling
      * the same drop with the WHOLE FILE as the selection (its own control of 0 failed)
        -> 2 failed — this row AND the ratchet, which then names server.py's claimable comment,
        the false positive that widening the scope would otherwise have pushed into the legacy
        list. Recorded as 2 because it was first written as 1 from reasoning and the round said
        otherwise: the two tests fail on the same mutation for different reasons
      * generalising `(?<!ran / )` into the plain `(?<!/ )` it invites -> **0 failed**, and this
        is NOT a kill. It is recorded because a round that kills nothing is the one worth
        writing down: no round count in this tree is preceded by `/ `, so the loose form would
        have cost nothing MEASURABLE today and the reason to keep the narrow one is an argument
        rather than a number — an exit-code gloss is a named shape, `/ ` is not. The row added
        beside the flipped one (`1 failed / 47 passed`) pins the shape the loose form would
        eventually reach, since this repo writes a round with its pass total AFTER the slash

    RE-MEASURED FOR VMCP-259 (861), which added `_MENTIONED` and the last two rows. Both of those
    rows are repo strings taken VERBATIM rather than adapted — the first from the comment run above
    `test_a_path_whose_FIRST_COMPONENT_looks_like_a_merge_stage_is_not_a_silent_miss`, the second
    from test_workflow_gates' per-stage-ownerless-exits record — and they are a PAIR on purpose:
    one is a mention of the phrase, the other a declaration wearing the same backticks, and the
    whole of 861's design is that nothing but the introducing words tells them apart. A new
    selection means a new control: this file alone, `__pycache__` deleted per round and then
    `PYTHONDONTWRITEBYTECODE=1`, `-q` dropped and rounds read by counting lines beginning `FAILED `
    and, separately, `ERROR `, `collected 6` cross-checked against the control in every round, each
    round restored from a byte copy and the file confirmed sha256-identical. Control round: 0
    failed, 0 errors.
      * `_without_mentions` returns its argument unchanged, i.e. the fix removed -> 1 failed, and
        on the MENTION row of this test ALONE. One and not two because the paragraph 861 was filed
        about still holds two real controls, which is the card's own point: the ratchet cannot see
        this defect on the tree it was found in, only a constructed reader can
      * the mask applied to `_states_a_control_count` ALONE, leaving the round predicate reading
        the mention -> 3 failed: the MENTION row here, plus the ratchet, which names the
        `A MENTION` paragraph of THIS file because that paragraph repeats the warning it documents
        — the predicted false red arriving live rather than as a claim — plus the tree-wide test,
        whose weak readings then disagree with the strong form about it. So this round is the
        whole argument for masking both predicates rather than only the one whose defect began the
        card
      * `_MENTIONED` narrowed to `the literal` alone -> 0 failed. NOT a kill, and recorded for the
        reason the `(?<!/ )` round above is: the siblings cost nothing measurable today, so keeping
        them is an argument about how a mention gets spelled rather than a number
      * `_MENTIONED` widened to ANY backticked span, both sides — the second candidate the comment
        above `_MENTIONED` rejects -> 3 failed, and the sharp one is not the ratchet (which names
        test_workflow_gates' record, one paragraph) but THIS test's PRE-EXISTING
        `control 2 passed; …` row: its `1 failed, 1 passed` is a real round written in backticks
        and the widened mask blinds the scanner to it. That is the "shrinks the list for the wrong
        reason" half of the rejection, arriving as a red on a row nobody wrote for it
      * the same widening on the CONTROL SIDE ONLY, which is the first rejected candidate exactly
        -> 3 failed, the ratchet naming FOURTEEN paragraphs at once. Twelve at `1fb0082`, before
        this card's own prose existed; the other two are the `A MENTION` comment's, so the digit is
        a round here rather than a claim up there — a count that moves when the paragraph stating
        it is edited is the self-tally this file refuses everywhere else
    """
    # (prose, quotes a round count, states a control count)
    rows = [
        ("`Bind for 0.0.0.0:3456 failed: port is already allocated`", False, False),
        ("full-suite runs against FRESH containers: 5 of 9 failed", False, False),
        ("the hub's idle check (exit 0 ran / 1 failed)", False, False),
        ("the whole file went RED, 1 failed / 47 passed, on that one test", True, False),
        ("control PASS; drop the rule from SKILL.md -> FAIL", False, False),
        ("the control at the same call site (the threshold made 1 request)", False, False),
        ("control: page 1 of 2 served in full", False, False),
        ("control PASS; drop the rule -> 2 failed", True, False),
        ("control 0 failed; drop the rule -> 2 failed", True, True),
        ("control 2 passed; drop the `.playwright-mcp/` line -> `1 failed, 1 passed`", True, True),
        ("Whole file 2 failed against an unmutated control round of 0", True, True),
        ("the unmutated control round is 0 failed and the mutation is 2 failed", True, True),
        ("control PASS. An unrelated paragraph of the same record says 0 failed", True, False),
        ("control PASS on a clause long enough to run past sixty characters of prose before it "
         "reaches 0 failed", True, False),
        ("traceback and the docstrings here contain the literal `control 0 failed`", False, False),
        ("CONTROL ROUND FIRST and repeated between batches: `control 0 failed` every time",
         True, True),
    ]
    for prose, expected_round, expected_control in rows:
        assert _quotes_a_round_count(prose) is expected_round, \
            f"_ROUND_COUNT read {prose!r} as {not expected_round}"
        assert _states_a_control_count(prose) is expected_control, \
            f"_CONTROL_COUNT read {prose!r} as {not expected_control}"


def test_a_mention_is_blanked_to_ITS_OWN_LENGTH_and_never_deleted():
    """`_without_mentions` fills a mention rather than removing it, and nothing held that.

    Both patterns are distance-bounded (`[^.;]{0,60}`), so DELETING a mention pulls whatever sat on
    either side of it closer together: a control then reaches a tally it could not reach in the
    unmasked text and vouches where the text itself does not — the one direction a fix for an
    over-vouching defect must not travel. That reasoning was written in the function's docstring
    and held by nothing else, so `return _MENTIONED.sub("", flat)` shipped GREEN over the whole
    suite. VMCP-273 (894) is that gap, and this is the round it was missing.

    The construction is the smallest kind of prose that separates the two readings: the word
    control, a mention long enough to hold it past the sixty-character window, and a stray tally.
    Its width is measured rather than picked — forty-five filler characters do not separate the
    readings at all (the unmasked text already vouches, so there is nothing for a mask to
    preserve) and forty-six are the first that do. Sixty are used, so the pin is not balanced on
    the window's own edge.

    MUTATION-CHECKED, `__pycache__` deleted then `PYTHONDONTWRITEBYTECODE=1`, this file as the
    selection, `-q` dropped so the `collected` line prints, and each round read by counting lines
    beginning `FAILED ` and lines beginning `ERROR ` separately rather than the first tally in
    stdout. At `194c6c4` plus this test, collected 7 on every round: control 0 failed and 0 errors;
    `_without_mentions` returning `_MENTIONED.sub("", flat)` -> 1 failed and 0 errors, the failure
    being this test; restored to a byte-identical file (sha256) and the closing control 0 failed
    again. What says the pin was MISSING rather than merely present is a SECOND pair, on `194c6c4`
    alone, where the selection is collected 6 on both sides: control 0 failed, and the same
    deletion 0 failed — green, which is why this test exists.
    """
    prose = "control the phrase `" + "q" * 60 + "` 3 failed"
    flat = _flat(prose)

    assert _quotes_a_round_count(prose), (
        "the construction has to carry a tally, or neither reading is asked anything at all"
    )
    assert not _CONTROL_COUNT.search(flat), (
        "the UNMASKED text must state no control here, because that is the reading the mask has "
        "to preserve; if it vouches on its own this round proves nothing"
    )
    assert _CONTROL_COUNT.search(_MENTIONED.sub("", flat)), (
        "deleting the mention must close the window on this construction, or the test no longer "
        "separates the two readings and would be green for the wrong reason"
    )

    assert len(_without_mentions(flat)) == len(flat), (
        "`_without_mentions` shortened the text: a mention is replaced by filler of ITS OWN "
        "length, never removed, because both patterns are distance-bounded"
    )
    assert not _states_a_control_count(prose), (
        "the mask made this paragraph vouch for itself where the unmasked text does not — exactly "
        "what deleting a mention buys, and the one direction this fix must not travel"
    )


def test_a_control_in_one_paragraph_does_not_vouch_for_the_next():
    """The UNIT itself, pinned on constructed prose instead of on today's offender set.

    The ratchet above passes for ANY splitter that happens to reproduce today's offenders —
    including one that never splits at all, which is precisely the state VMCP-167 (688) found and
    which shipped green for as long as it existed. So the split is pinned here directly, in both
    extractors' shapes and in both directions: a control vouches for its own paragraph, does NOT
    vouch for the next one, and the same text read WHOLE still reads as controlled. That last
    assert is what keeps this a test of the UNIT rather than of the two regexes — it states the
    pre-688 answer as an assertion, so the two granularities stay comparable and a future reader
    can see what changed rather than take it on trust.

    MUTATION-CHECKED, `PYTHONDONTWRITEBYTECODE=1`, `__pycache__` deleted per round, the whole
    scanner file as the selection, every round restored from a COPY (never `git checkout --`:
    this card's edits are uncommitted while it is in Build) and the restore confirmed by
    returning to the control. Control round: 0 failed.
      * `_paragraphs` treats no line as blank, so every record is one chunk -> 2 failed, here and
        on the ratchet
      * a bare `#` line stops counting as blank -> 2 failed, and the comment-run half of this
        test is the half that goes: a run has no blank line, `#` alone is its separator
      * `_paragraph_head` collapsed to a constant -> 2 failed
      * `_records` stops splitting -> 1 failed on the RATCHET ONLY, not here, because these
        asserts call `_paragraphs` directly. That is deliberate: this test pins the splitter, the
        ratchet pins its use, and a mutation that hits only one of them says which is which
      * insert a blank line before the module docstring's last `WHAT IT CANNOT ENFORCE` bullet
        -> 2 failed, which is the round that made the last assert here worth writing. The bullet
        it guards says that block is ONE paragraph, and that is a claim about the text it is
        written in: it first shipped as a measured pair, "four bullets, 47 lines", and the next
        three edits to that same block falsified it three times running (51, 54, 56) while every
        test stayed green. Asserted, it cannot rot; dated, it rotted within the hour
      * insert that blank line INSIDE the last bullet instead, past both literal anchors
        -> 1 failed WITH the extent assert and **0 failed** with it removed, same plant, same
        selection, same control. That pair is VMCP-211 (754): the two literals are satisfied by
        the block's HEAD, so every split after `It is a RATCHET` was invisible, and the bullet
        went on claiming the block was one paragraph while the scanner had already stopped
        treating it as one. Extent is what has no head to hide behind
    """
    docstring = (
        "Round A: control 0 failed; drop guard A -> 2 failed.\n"
        "\n"
        "Round B, never baselined: drop guard B -> 9 failed.\n"
    )
    comment_run = (
        "# Round A: control 0 failed; drop guard A -> 2 failed.\n"
        "#\n"
        "# Round B, never baselined: drop guard B -> 9 failed.\n"
    )
    for label, record in (("docstring", docstring), ("comment run", comment_run)):
        chunks = list(_paragraphs(record))
        assert len(chunks) == 2, \
            f"the {label} split into {len(chunks)} paragraphs, not 2 — a comment run's separator " \
            "is a bare `#`, a docstring's is a blank line, and both must count"
        first, second = chunks
        assert _quotes_a_round_count(first) and _states_a_control_count(first), \
            f"the {label}'s controlled paragraph stopped reading as controlled"
        assert _quotes_a_round_count(second), f"the {label}'s second round count went unseen"
        assert not _states_a_control_count(second), \
            f"the {label}'s Round B paragraph is vouched for by Round A's control — that is the " \
            "whole of VMCP-167 (688): one clause immunising a count it says nothing about"
        assert _states_a_control_count(record), \
            f"the {label} read WHOLE still states a control, which is exactly why the record " \
            "was the wrong unit; if this ever fails the two halves are no longer comparable"
        assert _paragraph_head(second) != _paragraph_head(first), \
            f"the {label}'s two paragraphs collapsed onto one key, so the ratchet could name " \
            "neither — the failure `_records` suffixes loudly rather than swallows"

    # The canonical shape this file ASKS authors for must survive the split: a control header and
    # its rounds are ONE paragraph. This is the cost of the unit, priced rather than asserted —
    # only a blank line separates the header from the rounds it vouches for.
    canonical = (
        "Control round: 0 failed, 1 passed.\n"
        "  * drop the guard -> 1 failed\n"
        "  * drop the other guard -> 2 failed\n"
    )
    assert [_states_a_control_count(p) for p in _paragraphs(canonical)] == [True], \
        "a control header with its rounds listed under it must stay ONE controlled paragraph — " \
        "it is the shape this file's own MUTATION-CHECKED blocks are written in"
    opened_up = canonical.replace("1 passed.\n", "1 passed.\n\n")
    assert [_states_a_control_count(p) for p in _paragraphs(opened_up)] == [True, False], \
        "the cost of the paragraph unit is not what this file says it is: a control header cut " \
        "off from its rounds by a blank line must stop vouching for them"

    # This module's own `WHAT IT CANNOT ENFORCE` block claims to be ONE paragraph, and that claim
    # is about the text it is written in — so it is asserted, not dated. Three successive edits
    # falsified the line count that used to stand here before it was replaced by this assert.
    module_doc = ast.get_docstring(ast.parse(Path(__file__).read_text(encoding="utf-8")),
                                   clean=False)
    hosting = [p for p in _paragraphs(module_doc) if "WHAT IT CANNOT ENFORCE" in p]
    assert len(hosting) == 1, \
        "the module docstring's `WHAT IT CANNOT ENFORCE` bullets are no longer ONE paragraph, so " \
        "the bullet above saying they are is now false — either restore the blank-line-free " \
        "block or rewrite that bullet, because it is this file's own example of the limit"
    assert "It is a RATCHET, not a retrofit." in hosting[0], \
        "the LAST bullet of `WHAT IT CANNOT ENFORCE` is no longer in the SAME paragraph as its " \
        "heading, so a blank line appeared inside the block — which is exactly what the bullet " \
        "claiming they are all one paragraph denies. Anchored on the bullet's opening sentence " \
        "rather than on the block's last words, because the last words are edited far more often"
    # ...and neither of those two anchors sees a blank line INSIDE the last bullet, which is the
    # hole VMCP-211 (754) named: both are satisfied by the block's HEAD, so a split anywhere after
    # `It is a RATCHET` leaves the claim false and the pin green (constructed: control 0 failed,
    # a blank line inserted mid-bullet 0 failed). What has no head to hide behind is the block's
    # EXTENT — it runs to the end of the docstring, so the paragraph hosting the heading must BE
    # the last paragraph. Anchoring on extent rather than on one more literal is deliberate: a
    # third literal would move the blind spot one sentence further down, and the bullet's own
    # claim is about the whole block being one chunk, not about which sentences survive in it.
    assert hosting[0] == list(_paragraphs(module_doc))[-1], \
        "the `WHAT IT CANNOT ENFORCE` block no longer reaches the END of the module docstring, " \
        "so a blank line appeared somewhere inside it — most likely inside its LAST bullet, " \
        "where the two literal anchors above cannot see one. The bullet says the whole block is " \
        "ONE paragraph and therefore that any control in any of it vouches for the rest; split " \
        "it and that sentence is false while every other assert here stays green (VMCP-211 / 754)"


# The measured counter-example of the line-fed rule, pinned as a CO-OCCURRENCE rather than as a
# token — VMCP-194 (724), found by sweeping this file's OWN asserts with the rule the card was
# filed under. `[[:space:]]` alone occurs TWICE in that CLAUDE.md paragraph, in two different
# sentences that say OPPOSITE things: the counter-example (the class was tried per line and changed
# nothing) and BSD grep's lever (the class is half of what DOES work). So the bare token pinned the
# PARAGRAPH, not the counter-example the assert's message names. Constructed and measured on the
# whole scanner file, `__pycache__` deleted then `PYTHONDONTWRITEBYTECODE=1`, control 0 failed:
# delete only the counter-example clause and leave the lever sentence -> 0 failed under the bare
# token, 1 failed under this pattern. It is `_CONTROL_COUNT`'s idiom one file over — a token and
# the claim it belongs to, close enough together to be one statement — and it carries the same
# caveat: `[^.;]` narrows the window, it does not bound it to a clause.
# THE BUDGET IS 200 RATHER THAN `_CONTROL_COUNT`'s 60, and the difference was measured rather than
# picked: the two sit about 48 characters apart today, and a good-faith rewrite of the paragraph
# that inserts one clause between them ran past 120. Same control of 0 failed: that rewrite,
# keeping every literal BOTH pins ask for, is 1 failed at a 120-character budget and 0 failed at
# 200, while deleting the counter-example clause and leaving the lever sentence stays 1 failed at
# both — what separates those two sentences is the `.` stop, not the budget, so widening it buys
# room for prose without giving the hole back.
# WHAT IT COSTS is a reword of `recovers nothing`, which fires it — measured, a rewrite that keeps
# the class and drops that finding is 1 failed; that is the ratchet's trade, and the message names
# the phrase so a rewriter knows what to restate.
# WHAT IT STILL DOES NOT HOLD is POLARITY, and both instances were built by the independent second
# pass rather than reasoned about, from the same control of 0 failed: delete the counter-example
# clause and reword the LEVER sentence to end `recovers nothing` -> 0 failed, the pin matching the
# sentence that says the class WORKS; and keep both tokens where they are while flipping the
# finding into `LOOSEN the regex: a [[:space:]] class is what reaches it` -> 0 failed, CLAUDE.md now
# prescribing what the paragraph exists to refute. So this pins adjacency, not the claim: it kills
# the plain deletion the bare token missed, and a rewrite that carries the phrase somewhere else
# defeats it. It also cannot say the measurement was re-run, nor reach a counter-example rewritten
# around a DIFFERENT pattern than the class.
_MEASURED_COUNTER_EXAMPLE = re.compile(
    r"\[\[:space:\]\][^.;]{0,200}?recovers nothing"
    r"|recovers nothing[^.;]{0,200}?\[\[:space:\]\]",
    re.IGNORECASE,
)


def _testing_philosophy() -> str:
    """CLAUDE.md's Testing Philosophy section, sliced like every other prose pin in this repo.

    Scoped to the section rather than matched over the whole file for the reason `_gc_section` in
    test_skill_contract gives: `PYTHONDONTWRITEBYTECODE` and `.venv` are named elsewhere in
    CLAUDE.md too, so a whole-file substring could not tell "the rule is still stated" from "some
    other paragraph still mentions the words".
    """
    text = (REPO_ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    start = text.find("\n## Testing Philosophy\n")
    assert start != -1, "CLAUDE.md no longer has a Testing Philosophy section to pin"
    end = text.find("\n## ", start + 1)
    assert end != -1, "the Testing Philosophy section no longer ends where the next one begins"
    section = text[start:end]
    assert 0 < len(section) < len(text), "the slice is not a proper subset of CLAUDE.md"
    return section


def test_claude_md_states_the_control_round_rule_and_its_limit():
    """The rule an agent READS, pinned next to the rule a scanner ENFORCES.

    Both halves are needed and neither substitutes for the other. CLAUDE.md is in every agent's
    context before it sweeps, so that is where behaviour changes; the scanner above is only met
    when it goes red, which is after the sweep was already reported. And the LIMIT is pinned as
    hard as the rule: a control round is the cheapest detector of a lying sweep, not a complete
    one, and #646's form — `.venv` dragged in by `cp -R`, the original `src` earlier on
    `sys.path`, mutation never applied, everything green — walks straight past it. A rule that
    left that unsaid would teach the next agent that a clean control means the round measured
    something, which is the same over-claim in a new place.

    MUTATION-CHECKED, `PYTHONDONTWRITEBYTECODE=1`, exactly 1 test selected per round, CLAUDE.md
    restored from a COPY between rounds (never `git checkout --`: this card's own edit is
    uncommitted while the card is in Build). Control round: 0 failed, 1 passed.
      * delete the control-round paragraph -> 1 failed naming the missing rule
      * delete the limits paragraph, keeping the rule -> 1 failed naming the missing limit
      * soften `WRITE ITS FAILED COUNT` to "note the control" -> 1 failed
      * flip `never the pass total` to "and the pass total" -> 1 failed
      * move both paragraphs OUT of Testing Philosophy into the Releases section -> 1 failed, so
        the slicer is load-bearing and not decoration
      * delete the `## Testing Philosophy` heading itself -> 1 failed from the slicer, with its
        own message rather than a confusing IndexError
      * delete the sentence saying only removing `__pycache__` moved the round -> 1 failed: the
        variable alone reads as the whole remedy otherwise, and measurement says it is not
      * delete the same-paragraph clause VMCP-167 (688) added, keeping the rest -> 1 failed
        (re-measured with the whole scanner file as the selection, against its own control of
        0 failed). The scanner now REFUSES a shape this section's older wording
        permitted — one control header for a long section — so an agent who reads "beside" as
        "somewhere in this docstring" writes prose the suite rejects, and the rulebook has to
        say where the control goes, not just that there is one

    VMCP-318 (1462) RE-KEYED ONE PIN AND ADDED ONE, and the re-keying is the more interesting
    half because the pin was GREEN throughout. The `.venv` pin used to name
    `rsync -a --exclude .venv`, one of the two extraction methods CLAUDE.md prescribed; that
    card withdrew both, and the string went on occurring in the section only inside the
    sentence that retires it. Nothing went red, and that is measured rather than assumed: the
    gate run made after the prose edit and BEFORE this re-keying passed, i.e. the old pin
    accepted a mention with the opposite meaning. It would then have gone red later, on a
    tidy-up that changed nothing about #646. So the proxy moved to `git clone --no-hardlinks`,
    which is what the rule now prescribes and what actually keeps `.venv` out. Read it as the
    general shape: a prose pin keyed to a QUOTED PRESCRIPTION survives that prescription's
    withdrawal, silently, and its message is then the only thing that still says what it meant.
    Same discipline as the rounds above, `__pycache__` deleted then PYTHONDONTWRITEBYTECODE=1,
    the selection being this file, run in a clone with the working tree committed inside it so
    a restore returns to the state under test; control 0 failed, 0 errors, 7 collected.
      * `Record SKIPPED beside FAILED` replaced in CLAUDE.md -> 1 failed, here, on the new pin,
        7 collected
      * `git clone --no-hardlinks` replaced in CLAUDE.md -> 1 failed, here, on the re-keyed pin,
        7 collected
      * both restored -> control (closing) 0 failed, 0 errors, 7 collected
    """
    section = _testing_philosophy()

    assert "UNMUTATED CONTROL round on the SAME selection" in section, \
        "CLAUDE.md no longer tells a sweep to open with an unmutated control round on the same " \
        "selection — the one check that would have caught #594's 16x inflation"
    assert "WRITE ITS FAILED COUNT beside the round's" in section, \
        "CLAUDE.md no longer asks for the control's COUNT. The word 'control' alone is what the " \
        "sweeps already had: 'control PASS' in a tree with 30 constant failures is true and useless"
    assert "Record the FAILED count, never the pass total" in section, \
        "CLAUDE.md no longer says WHICH number to record. A pass total goes stale with every " \
        "test the repo adds — the same reason the unit count above it is a floor"
    assert "is enforced IN THE SAME PARAGRAPH" in section, \
        "CLAUDE.md no longer says WHERE the control has to sit. The scanner's unit is the " \
        "paragraph (#688), so a section-wide control header is refused — an agent who reads " \
        "'beside' as 'somewhere in this docstring' writes the shape that made #668 filable"

    assert "A clean control does not mean the round MEASURED anything" in section, \
        "CLAUDE.md no longer bounds the control round. Sold as complete, it teaches that a green " \
        "control means the mutation applied — and #646's four false greens each had one"
    assert "PYTHONDONTWRITEBYTECODE=1" in section, \
        "CLAUDE.md no longer names the stale-bytecode form (#624): a constant rewritten to the " \
        "SAME LENGTH replays the previous budget when the mtime does not advance a whole second"
    assert "only deleting `__pycache__` moved" in section, \
        "CLAUDE.md no longer says the variable alone is not the remedy. Measured: it stops " \
        "Python WRITING bytecode, not READING it, so a stale .pyc already on disk still replays"
    assert "git clone --no-hardlinks" in section and "vikunja_mcp.__file__" in section, \
        "CLAUDE.md no longer names the way to build a tree .venv cannot follow into (#646), or " \
        "the check that catches it anyway: print where the package was actually imported from"
    assert "Record SKIPPED beside FAILED" in section, \
        "CLAUDE.md no longer asks a round to record its SKIP count. `collected` counts SKIPPED " \
        "items, so it reads the same on a sound stand and on one where the tests never ran — " \
        "measured at 1401 on both a clone and a `git archive` extraction of one tree, the " \
        "extraction silently skipping 61 of them (#1462). Without this the cross-check asserted " \
        "just above certifies a blind round as a clean one"

    # ...and the step BEFORE the arithmetic: how a round is READ. VMCP-205 (748) and VMCP-224
    # (767). A control cannot rescue a mis-read round, and this repo's own docstrings are what
    # make the obvious read wrong — pytest echoes a failing test's docstring in the traceback, so
    # the first `N failed` in stdout is a sweep record's `control 0 failed`, not the summary.
    assert "READ A ROUND BY COUNTING `FAILED`" in section, \
        "CLAUDE.md no longer says HOW to read a round. The naive first-match read is measured " \
        "to fail DOWNWARD in this repo specifically — every red round of #771's own sweep read " \
        "as 0 — and a sweep table that lies in minus reads a live pin as blind (#748)"
    assert "collected" in section, \
        "CLAUDE.md no longer asks a round to cross-check its SELECTION SIZE against the " \
        "control's. Without it a round that selected different tests is still written down as " \
        "a delta against a control it never shared a selection with (#767)"
    assert "prints NO `collected` line" in section, \
        "CLAUDE.md no longer records that `-q` suppresses the very line the cross-check reads, " \
        "so a scripted sweep asking for it under `-q` gets nothing and the check silently never " \
        "runs — the same silent-downward shape as the parser it was added to catch"


def _testing_dossier() -> str:
    """`docs/dossier/testing.md` — the evidence layer under CLAUDE.md's Testing Philosophy.

    The rulebook was split into a rules layer (CLAUDE.md, in every session's context) and
    per-subsystem dossiers (read when you touch that subsystem). The line-fed material below is
    evidence, not a rule, so it lives here; `_testing_philosophy` still slices CLAUDE.md for the
    rules that must stay in front of every agent. Sliced whole rather than by section: this file
    IS the one section.
    """
    path = REPO_ROOT / "docs" / "dossier" / "testing.md"
    assert path.is_file(), (
        "docs/dossier/testing.md is gone — the Testing Philosophy dossier is where the measured "
        "prose under CLAUDE.md's rules lives. If it moved, move this anchor; do not delete the "
        "check"
    )
    return path.read_text(encoding="utf-8")


def test_the_testing_dossier_says_a_stale_figure_sweep_is_not_line_fed():
    """The OTHER sweep in this section — the text one that hunts stale figures — and its trap.

    Pinned in four pieces, and three of them are the counter-intuitive half rather than the rule.
    The rule alone ("do not be line-fed") invites the obvious fix, and the obvious fix is measured
    wrong: on `e86b2c9^` a whitespace CLASS in place of the literal space returns the same 15 hits
    in test_api_kanban.py and still misses the wrapped figure at :1473 — on BOTH greps here.

    What DOES reach it was re-measured per engine, calling each by NAME, because this shell wraps
    `grep` in a function that routes `-[Zz]*` -- and `--null-data` -- to `command grep` while a
    bare `grep` goes to ugrep: every "I checked both greps" that reaches for -z runs BSD TWICE.
    That is not hypothetical, it is how the round-1 claims survived an author pass AND a review.
    Called explicitly, the two disagree in OPPOSITE directions, which is why neither lever is
    written down as the fix:
      * /usr/bin/grep, BSD 2.6.0-FreeBSD — the FLAG works, but only WITH the loosened class,
        and only if you count with `-o`: `grep -zo` (its -z IS --null-data) plus `[[:space:]]\\+`
        gives 16 MATCHES and finds :1473, against 15 for 665's literal space. Count matches, not
        lines — a bare `-z` makes the file one record, so `-zc` says 1 for either pattern and
        `-zo | wc -l` says 17, the wrapped match spanning two lines. `-zn` then numbers every
        match line 1, so it moves the blind spot instead of closing it. No `-P` on this grep.
      * ugrep 7.5.0 — no FLAG helps: `-z` there is `--decompress`, and its real null-data
        (`-00`) leaves it at 15 and blind too. The PATTERN does it, specifically an explicit
        `\\n` inside it: `ugrep -n -o '[0-9]{2,}\\n\\s+passed'` prints `1473:102` and
        `1474|    passed` on the default matcher, with `-E` or `-P` alike — while `-P` with
        `\\s+` in place of that `\\n` drops back to 15. So `-P` is not the lever; the `\\n` is.

    And a spanning matcher reported without the DIFF against the per-line hits is unusable rather
    than merely noisy: of the hits `\\d+ (?:passed|failed)` returns over tests/, all but a couple
    are ones the line-anchored sweep already found. The total is deliberately NOT pinned and the
    WRAPPED count is, because the total counts the file it is written in: over
    94bae3d -> aadde71 -> 7718e6c it ran 245 -> 319 -> 330 while wrapped stayed 2. Not "it moves
    with every landing", which is the tempting shortcut and is false — measured over those same
    11 landings, 5 moved it by ZERO, every one of them a `chore: vX.Y.Z` release bump that
    touches no file under tests/, and 7718e6c, one of the three shas anchored above, is itself
    one of them. Which is the point restated: a total is a fact about a TREE, not about time.

    Why a pin at all, when the class has no live instance — the whole value of these sweeps is the
    NEGATIVE answer, so the rule is read exactly once per sweep, by an agent about to write "this
    file is clean" into a card. A paragraph nobody re-runs is the first thing a later edit drops.

    MUTATION-CHECKED, `__pycache__` deleted and then `PYTHONDONTWRITEBYTECODE=1`, exactly 1 test
    selected per round, CLAUDE.md restored from a COPY between rounds (never `git checkout --`:
    this card's own edit is uncommitted while the card is in Build). Control round: 0 failed,
    1 passed.
      * delete the paragraph entirely -> 1 failed, on the rule assert
      * keep the rule, delete the measured `[[:space:]]` counter-example -> 1 failed: without it
        the paragraph reads as "write a better regex", which is the fix that does not work
      * keep both, drop the requirement to report the DIFF -> 1 failed
      * keep those, drop ugrep's `--decompress` -> 1 failed: without it the flag reads as the
        portable answer, and on one of the two greps NAMED in the paragraph it does nothing
      * move the paragraph out of Testing Philosophy into Releases -> 1 failed, so the section
        slicer is load-bearing for this pin too and not just inherited from the one above.
        Read the FIRST attempt at this round as the warning it is: inserting the paragraph
        before the `## Releases` HEADER scored 1 passed, and the honest reading of that is not
        "the slicer is decoration" but "the mutation never happened" — Releases is the section
        immediately after this one, so the text landed inside the slice it was supposed to
        leave. Verify a move by re-slicing and asserting the literal is GONE from the slice and
        still present in the file, which is the cheap version of #646's `vikunja_mcp.__file__`.
      * restore -> 0 failed, 1 passed, back to the control, CLAUDE.md byte-identical to the copy
    """
    section = _testing_dossier()

    assert "must not be LINE-FED" in section, \
        "the testing dossier no longer says a sweep for stale figures must not be line-fed. Test prose " \
        "here is hand-wrapped near 100 columns — a convention, not the gate, which sits at " \
        "`max-line-length = 110` since #711 ratcheted #669's 120 down — so `grep -rn` reports " \
        "a file CLEAN at a wrapped figure, which is the one answer a sweep exists to give"
    assert _MEASURED_COUNTER_EXAMPLE.search(" ".join(section.split())), \
        "the testing dossier no longer carries the measured counter-example, and the rule is worth less " \
        "without it: read PER LINE, loosening the PATTERN does not help — the same 15 hits on " \
        "both greps. Without this the next reader fixes the regex and stays blind. What is pinned " \
        "is the class token NEXT TO its negative finding (`recovers nothing`) in one sentence — " \
        "VMCP-194 (724), because the token alone occurs twice in that paragraph and the second is " \
        "BSD grep's lever, so it pinned the paragraph and not the counter-example. Reworded the " \
        "finding? Restate it beside the class, or update `_MEASURED_COUNTER_EXAMPLE` to match"
    assert "DIFF against the per-line hits" in section, \
        "the testing dossier no longer says WHAT to report. A raw spanning hit list is dominated by what " \
        "the line-anchored sweep already found, so the difference is the only useful output — " \
        "and the noise a wrap-crossing pattern adds has to be eyeballed, not counted"
    assert "--decompress" in section, \
        "the testing dossier no longer records that the two greps need OPPOSITE levers: on ugrep 7.5.0 " \
        "`-z` is --decompress, not --null-data, so the flag that works on BSD grep finds " \
        "nothing there, while a pattern carrying an explicit \\n finds it AND says where. " \
        "Without this the paragraph reads as 'add -z', wrong on one of the two greps it names"


# Five readings of "list marker", for the one refusal this file makes on a tree-wide measurement.
# They are five because the count DEPENDS on the reading and the file used to quote it without
# saying which — see `_paragraphs`. Nothing below is part of the scanner: the unit stays the
# paragraph, and these exist so the refusal can be RE-RUN instead of trusted.
_LIST_MARKER_READINGS = (
    r"^\*\s",              # a star and a space
    r"^[*-]\s",            # star or dash
    r"^[*\-+]\s",          # star, dash or plus
    r"^([*\-+]|\d+\.)\s",  # those three plus an enumerator
    r"^[*\-+]",            # any of the three, no following space required
)

# ALL FIVE readings of "a digit near the word `control`" that the `_CONTROL_COUNT` comment
# describes, as code rather than as a parenthetical, for the reason the list markers above are
# five: the claim there is about all of them, and a claim that ships one spelling and describes
# four is not re-runnable. Deliberately NOT used by `_states_a_control_count` — the scanner keeps
# the strong form; these exist so the calibration paragraph can be RUN. The loosest is a PAIR
# because "the word anywhere and a digit anywhere" is a conjunction, not a distance.
_WEAK_CONTROL_READINGS = (
    re.compile(r"control\b[^.;]{0,60}?\d|\d[^.;]{0,60}?\bcontrol\b", re.IGNORECASE),
    re.compile(r"control\b[^.;]{0,60}?\d", re.IGNORECASE),
    re.compile(r"control\b.{0,60}?\d|\d.{0,60}?\bcontrol\b", re.IGNORECASE),
    re.compile(r"control\b.{0,60}?\d", re.IGNORECASE),
    (re.compile(r"\bcontrol\b", re.IGNORECASE), re.compile(r"\d")),
)


def _reads_as_a_control(prose: str, reading) -> bool:
    """Whether one of the weak readings accepts this prose — a pair meaning both halves must hit.

    It reads through `_without_mentions` for the same reason the strong form does, and that keeps
    the tree-wide comparison about PATTERN STRENGTH alone WHERE THE MENTION IS NOT WRAPPED across
    a line break: without it the two predicates would differ in a second dimension, and the day a
    paragraph's only control is a mention the identity assert below would fire about calibration
    when what actually happened is the ratchet catching exactly what VMCP-259 (861) added it for.
    The flattening stays this function's own — adding the `#` strip here is a separate change with
    its own price, and this one is not it.

    THAT QUALIFIER IS VMCP-272 (893) and names a measured hole, not a hedge. `_flat` strips each
    line's leading `#` BEFORE the join and this flattener does not, while `_MENTIONED` wants
    whitespace between the introducing word and the backtick — so a `#` landing in that gap masks
    on the strong path only, and the second dimension survives there. Measured at `cbc3816` on the
    module: a comment-run paragraph whose only baseline is an introducing word ending one line and
    the backticked control opening the next quotes a round, reads False strongly and True on all
    five weak readings — a STRONG offender and a weak one on none of the five, which is exactly
    the difference the identity assert reports, blaming the WEAKENING and sending its reader to
    the calibration comment. Unwrapped, the same sentence is an offender on both sides and only
    the ratchet fires. A docstring carries no `#`, so only a mention inside a COMMENT RUN can
    diverge; at that sha the scan sees 16 spans, 9 of them in comment runs, and no paragraph
    diverges — latent, and one hand re-wrap of one of those nine arms it.
    """
    flat = _without_mentions(" ".join(prose.split()))
    if isinstance(reading, tuple):
        return all(half.search(flat) for half in reading)
    return bool(reading.search(flat))


def _run_key_without_its_opening_line(key: str) -> str:
    """A comment run's key as VMCP-153 (656) first wrote it: the following definition ALONE.

    Docstring keys are dotted qualnames and pass through untouched. The split is on the second
    colon because `_comment_runs` builds the key as `comments-above:<def>:<opening line>` and a
    Python definition name cannot contain a colon, while an opening line very much can.
    """
    return ":".join(key.split(":")[:2]) if key.startswith("comments-above:") else key


def _bullet_chunks(paragraph: str, marker: re.Pattern):
    """A paragraph split AGAIN at list markers — the finer unit this file refuses, kept runnable."""
    chunk: list[str] = []
    for line in paragraph.splitlines():
        if marker.match(line.strip().lstrip("#").strip()) and chunk:
            yield "\n".join(chunk)
            chunk = [line]
        else:
            chunk.append(line)
    if chunk:
        yield "\n".join(chunk)


def _tree_records():
    """(path, record key, prose) per docstring and comment RUN under tests/ — the pre-688 unit.

    `_records` yields paragraphs, which is the scanner's unit. The test below needs the record
    around a paragraph as well, because "a bullet split severs a control" is a question about
    whether the control was anywhere in the record the chunk came from.
    """
    for path in sorted(p for d in _SCANNED_DIRS for p in d.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        relative = path.relative_to(REPO_ROOT).as_posix()
        for key, text in itertools.chain(_docstrings(path, source), _comment_runs(source)):
            yield relative, key, text


def _repo_markdown():
    """Every markdown file of the repo, minus dot-directories — `.venv` alone would swamp it."""
    for path in sorted(REPO_ROOT.rglob("*.md")):
        relative = path.relative_to(REPO_ROOT)
        if any(part.startswith(".") for part in relative.parts):
            continue
        yield relative.as_posix(), path.read_text(encoding="utf-8")


# The material the SCOPE comment at the top of this file points at, identified by two FINGERPRINTS
# rather than by its key, its prose or its paragraph boundary — VMCP-194 (724). The markdown assert
# below used to ask only that SOME paragraph of CLAUDE.md quote a round count without a control,
# while the scope comment names a specific thing: the line-fed paragraph `aadde71` landed and
# `75a1e52` added a second count to. So destroying exactly the named material left the guard GREEN,
# which is the shape this whole file exists to refuse. Measured on the whole scanner file,
# `__pycache__` deleted per round and then `PYTHONDONTWRITEBYTECODE=1`, control 0 failed — and EVERY
# NUMBER IN THIS PARAGRAPH IS OF THE FORM THIS REPLACED, so re-running them here answers
# differently on purpose: add `control 0 failed` to the named paragraph AND plant an unrelated
# uncontrolled paragraph elsewhere in CLAUDE.md -> 0 failed; replace the named paragraph's body
# outright with the plant in place -> 1 failed, and that one was the line-fed pin firing, never this
# assert. The documented kill round did work ALONE — `control 0 failed` into the named paragraph
# with nothing planted -> 1 failed — which is how it survived three review rounds, and it worked
# only because that paragraph happened to be the ONLY uncontrolled markdown paragraph in the repo
# (measured: the offender list held exactly one entry). The plant alone is 0 failed, so neither
# mutation is individually suspicious. It is the SAME defect one level down from the one this test's
# own round list already records — 688 moved the hole from "any stray `.md` in a checkout" to "any
# stray PARAGRAPH of CLAUDE.md".
#
# WHY FINGERPRINTS AND NOT THE PROSE, THE KEY, OR THE PARAGRAPH. Pinning the wording would fire on
# any rewrite of a file several cards a day edit. Pinning the KEY is what the first spelling of this
# fix did and it was defeated in review: `_paragraph_head` truncates at `_KEY_HEAD`, the anchor
# starts past that cut, so a decoy paragraph agreeing in its first 48 characters put the SAME string
# in the offender list while the named paragraph was clean — control 0 failed, that construction
# 0 failed. Asking about the PARAGRAPH instead answers it, and asking about a paragraph BOUNDARY is
# the next trap: splitting this 46-line paragraph at its own natural seam is ordinary housekeeping,
# and it strands the round counts in the half that does not carry the anchor — control 0 failed,
# that split 1 failed with a message that said the offender was gone when it had merely moved. So
# the filter is a disjunction of two marks that TRAVEL with the counts: the anchor phrase, and
# `e86b2c9`, the sha the measurement was taken on. Both occurrences of that sha sit in the same
# paragraph as a round count, and a sha cannot be reworded — it is the mark that survives both a
# split and a copyedit.
#
# THE ANCHOR HALF COSTS NOTHING THIS REPO IS NOT ALREADY CARRYING:
# `test_the_testing_dossier_says_a_stale_figure_sweep_is_not_line_fed` asserts that phrase already,
# so breaking it is red there whether or not this assert exists. From the same control of 0 failed:
# rewrapping the line so `must not be` ends it and `LINE-FED` opens the next -> 1 failed, on that
# sibling ALONE, because the sha half keeps this one green; rewriting the paragraph's opening and
# body while keeping what both pins ask for -> 0 failed.
#
# WHAT IT DOES NOT CATCH, priced rather than rounded up. VMCP-194 (724)'s SECOND round REWROTE
# three of these four bullets — each true about its number and wrong about the noun, the figure it
# identified, or the source it credited — so read the diff of this block rather than trusting that
# a limitation here was ever measured the way it is worded. Every round below is over the whole
# scanner file unless it names its own selection, `__pycache__` deleted and then
# `PYTHONDONTWRITEBYTECODE=1`, control 0 failed; every count OF THE TREE was taken at `c1c2619`.
# Those rounds are vouched for by the one control in this header, so keep this block ONE paragraph:
# put a bare `#` between the header and the bullets and it becomes an offender of its own ratchet
# -> 2 failed. A provenance line stood here too — "two of these came from the independent second
# pass" — and it is DROPPED, not corrected, because nothing in the tree records which finding came
# from where: it was the one sentence in this block a reader could not check, in the block whose
# whole job is to be checkable.
#   * It holds the material's SHAPE — a round count THIS pattern reads, no control beside it — and
#     nothing of what the material SAYS beyond the two marks that find it: replacing the named
#     paragraph's 46 lines with 8 of wholly different prose, carrying the sha, one round count and
#     all FOUR literals the sibling test pins -> 0 failed. Four, not one: keep only the anchor of
#     them and it is 1 failed THERE, with this assert still green. And NOT "any `N failed`", which
#     is the tempting way to write it and is false — the same rewrite carrying `Bind for
#     0.0.0.0:3456 failed` as its only tally -> 1 failed, and carrying `containers: 5 of 9 failed`
#     -> 1 failed, both on the first assert below. `_ROUND_COUNT`'s two exclusions are the
#     difference, and the first of them sits INSIDE the paragraph this block is describing.
#   * A DELIBERATE decoy carrying a fingerprint satisfies it while the real material is clean, and
#     that is measured now rather than predicted: give the named paragraph a control AND plant a
#     paragraph carrying `e86b2c9` and a round count -> 0 failed. The unmarked plant does not
#     satisfy it (it carries neither mark) -> 1 failed, and that gap is the difference between this
#     and the form it replaced — same tree, byte-identical CLAUDE.md, only the pre-fix
#     `any(key.startswith("CLAUDE.md::"))` assert restored -> 0 failed. A decoy written to carry a
#     mark is a different, adversarial thing from that plant.
#   * It certifies that the trigger FIRES on that material, not that the hit is a real sweep round.
#     Measured with `_ROUND_COUNT` itself, the paragraph holds exactly two matches, and both are
#     QUOTATIONS of figures it discusses rather than rounds it ran: `5 failed`, copied out of
#     `e86b2c9^:test_api_kanban.py:1473`, where it is the un-wrapped NEIGHBOUR of the wrapped
#     `102 passed` that paragraph is actually about — a figure this pattern could never match,
#     reading only `N failed` — and `-> 7 failed`, quoted there as an illustrative hit in this
#     scanner. Do not cite the CLAUDE.md sentence they sit in as agreeing, which an earlier version
#     of this bullet did: it grades a DIFFERENT ruler (spanning-only hits of
#     `\d+ (?:passed|failed)` at `e86b2c9^`), it calls :1473 GENUINE in that word, and one of the
#     two it does call false — the docker port `Bind for 0.0.0.0:3456 failed` — is not a
#     `_ROUND_COUNT` match at all: `(?<![:.\w])` cuts it, and dropping that lookbehind alone
#     -> 2 failed here, one of them that very row (the pattern test records the same round as
#     1 failed, on its own single-test selection). The scope comment calls this material a REAL
#     offender, and whether a quoted figure can be one is VMCP-210 (753) rather than something this
#     assert settles. Do not restate that as "src/ is in the same position and its hit was
#     dismissed": the same comment re-runs src/ at `6dd2803`, finds a second hit that IS real, and
#     says in its own words that "the sole-false-positive argument is gone on BOTH scopes".
#   * `uncontrolled_markdown` keys markdown paragraphs by file plus a 48-character head with none of
#     the collision defence `_records` gives the tests/ side — measured: 840 markdown paragraphs
#     collapse to 789 distinct keys, and the 20 keys shared by more than one paragraph cover 71 of
#     them, all under `docs/superpowers/`. The 51 this bullet shipped is 840 - 789, the EXCESS over
#     one paragraph per key, which is a different noun: a reader re-running the obvious ruler gets
#     71 and, in this file's own words, cannot tell a wrong ruler from a wrong tree. Nothing here
#     depends on those 20 being distinct: the assert above reads paragraphs, and
#     the one below only asks that the scan reach what the hand read found.
_LINE_FED_ANCHOR = "must not be LINE-FED"
_LINE_FED_MEASUREMENT = "e86b2c9"
# WHERE that material lives. It was a CLAUDE.md paragraph until the rulebook was split into a
# rules layer plus per-subsystem dossiers: CLAUDE.md now carries the RULE (do not be line-fed)
# and `docs/dossier/testing.md` carries the measured prose this pin reads — the round counts
# quoted without a control beside them, which is the shape the scope comment names. The pin
# follows the material rather than the filename, which is what its own message asks for.
_LINE_FED_HOME = "docs/dossier/testing.md"


def test_the_tree_wide_claims_in_this_file_are_asserted_rather_than_counted():
    """The claims this file makes about the whole tree, RUN instead of quoted — VMCP-167 (688).

    WHY THIS TEST EXISTS, and it is a measured failure rather than a tidiness argument. Four
    tree-wide counts this file quoted went stale inside 80 minutes, because `aadde71` landed
    between the tree they were taken on and `bba4fed`, the commit that shipped them: records
    1098 / 870 / 83 -> 1111 / 881 / 85, distinct keys without the run's opening line 1760 -> 1794,
    the bullet-split pair 49/36 -> 53/40, markdown hits five -> six. TWO CLAIMS IN THAT SENTENCE
    ARE CORRECTED HERE rather than left standing, re-measured for VMCP-194 (724). The distinct-key
    row is not an instance of "correct at `6dd2803`, stale at `bba4fed`" at all: no distinct-key
    count stood in this file at `6dd2803` — that tree carries no such sentence — and the number
    that shipped at `bba4fed` was 1761, which `_comment_runs` above then established belongs to no
    committed tree in the window it could have been taken in. So `1760 -> 1794` names what that
    ruler WOULD have given, not what a reader found here. Nor did every one carry a DATE: three
    did — the bullet pair "DATED 2026-08-02", the markdown hits "so it is dated", the records
    triple "the SAME day" — while the 1761 sentence carried neither a date nor a sha, only the
    word "today". The count wearing no label at all is the one that rotted hardest, which sharpens
    the rule rather than softening it. Three review rounds of one card each ended on an instance
    of that, so the answer here is not a fourth re-measurement: a count over the tree gets an
    ASSERT if a reader acts on it, a SHA if it is history, and never a date.

    WHY AN ASSERT AND NOT A SHA, for these five. A sha is right forever but says nothing about
    TODAY, and each of these is a live claim some paragraph above rests a decision on. The
    trajectories that are pure history keep their shas instead, in `_paragraphs` and
    `_comment_runs`. And one number could have neither: 1761 was measured in an uncommitted working
    tree, so no sha owns it — a property was the only thing left that could be true.

    WHAT IT COSTS is a false alarm when the tree legitimately changes shape — a comment run stops
    colliding, or CLAUDE.md's uncontrolled paragraph gains a control. That is the ratchet's own
    trade: each message below says what to REWRITE, because the paragraph it defends would then be
    stating something no longer true, which is the failure this whole file is against.

    MUTATION-CHECKED, `__pycache__` deleted per round and then `PYTHONDONTWRITEBYTECODE=1`, the
    whole scanner file as the selection, every round restored from a COPY (never `git checkout --`)
    and the restore confirmed by returning to the control. Control round: 0 failed.
      * `_run_key_without_its_opening_line` returns its argument unchanged -> 1 failed on the
        collision assert: with the opening line still in the key nothing collides, which is the
        whole reason 656 put it there
      * drop `_paragraph_head` from the finer key, leaving definition plus opening line -> 1 failed
        on the zero-collision assert
      * `_bullet_chunks` yields the paragraph whole -> 1 failed: a split that does not split
        strands exactly what the paragraph unit already strands, and the assert asks for MORE
      * `_records` stops splitting at paragraphs, i.e. back to the pre-688 record unit -> 2 failed,
        here and on the ratchet: under that unit the weak and strong patterns do NOT pick the same
        offenders, which is the redundancy 688 cost and this assert now watches
      * add `control 0 failed` to CLAUDE.md's line-fed paragraph, so repo markdown is clean again
        -> 1 failed, naming the paragraph that had been the offender
      * delete the sha clause from CLAUDE.md's Testing Philosophy -> 1 failed
      * the same CLAUDE.md round AND an untracked `scratch-notes.md` at the repo root reading
        `drop the guard -> 3 failed` -> 1 failed. The first version of the markdown assert asked
        only for a NON-EMPTY offender list and this construction was 0 failed: any stray `.md` in
        anyone's checkout silenced it. It now names CLAUDE.md. Found by the independent second
        pass, reproduced here before it was changed
      * and naming CLAUDE.md MOVED that hole rather than closing it, which is VMCP-194 (724) and
        the reason this assert now goes by fingerprints. Under the `any(... "CLAUDE.md::")` form it
        replaced, with an unrelated uncontrolled paragraph planted at the END of CLAUDE.md: the
        plant alone -> 0 failed, `control 0 failed` added to the line-fed paragraph alone
        -> 1 failed, BOTH TOGETHER -> 0 failed — the named material clean, its scope sentence
        false, the guard green. Gutting the named paragraph with the plant in place was 1 failed
        there, and that one was the line-fed pin, never this assert
      * the fingerprint form, whole battery from one control of 0 failed, every mutation applied by
        a script that refuses to run unless its target matches exactly once: control into the named
        paragraph -> 1 failed; that plus the plant -> 1 failed; that plus a DECOY whose first 48
        characters equal the named paragraph's -> 1 failed, which the KEY form of this fix did not
        catch (0 failed, found in review — `_KEY_HEAD` cuts before the anchor, so a colliding head
        put the same string in the offender list); gut plus plant -> 2 failed; the scan made to
        skip CLAUDE.md -> 1 failed on the second assert alone
      * the other direction, because a pin that fires on prose would be worse than the hole. Same
        control: SPLITTING that 46-line paragraph at its own natural seam -> 0 failed, where the
        paragraph-shaped version of this fix went red and told the reader the offender was gone
        when it had only moved into the half without the anchor — which is why the sha `e86b2c9` is
        the second fingerprint. Rewrapping the anchor across a line break -> 1 failed, on the
        SIBLING alone, because that test pins the phrase verbatim already and the sha keeps this
        one green. A full rewrite of the paragraph keeping what both pins ask for -> 0 failed
      * tighten one of `_WEAK_CONTROL_READINGS` into `_CONTROL_COUNT` itself -> 1 failed. The
        first version compared the strong pattern against a single weak constant and this was
        0 failed: an identity check between a pattern and itself. The loosest-reading assert is
        what catches it, and it is the reason there is one
    """
    records_by_definition: dict[str, int] = {}
    paragraphs_by_definition: dict[str, int] = {}
    stranded = dict.fromkeys(_LIST_MARKER_READINGS, 0)
    stranded_by_the_paragraph_unit = 0
    for relative, key, text in _tree_records():
        record_key = f"{relative}::{_run_key_without_its_opening_line(key)}"
        records_by_definition[record_key] = records_by_definition.get(record_key, 0) + 1
        record_states_a_control = _states_a_control_count(text)
        for paragraph in _paragraphs(text):
            finer = f"{record_key}::¶{_paragraph_head(paragraph)}"
            paragraphs_by_definition[finer] = paragraphs_by_definition.get(finer, 0) + 1
            if not record_states_a_control:
                continue
            if _quotes_a_round_count(paragraph) and not _states_a_control_count(paragraph):
                stranded_by_the_paragraph_unit += 1
            for reading in _LIST_MARKER_READINGS:
                for chunk in _bullet_chunks(paragraph, re.compile(reading)):
                    if _quotes_a_round_count(chunk) and not _states_a_control_count(chunk):
                        stranded[reading] += 1

    colliding = sorted(k for k, n in records_by_definition.items() if n > 1)
    assert colliding, (
        "keying a comment RUN by the definition below it alone no longer collides anywhere under "
        "tests/, so `_comment_runs` putting the run's opening line in the key defends nothing on "
        "this tree. That is not a bug, but the paragraph in `_comment_runs` explaining WHY the "
        "opening line is there rests on it: rewrite that paragraph, or drop the opening line and "
        "re-key the legacy list. The counts it quotes belong to named shas and are history"
    )

    merged = sorted(k for k, n in paragraphs_by_definition.items() if n > 1)
    assert not merged, (
        f"{merged} collide when a comment run is keyed by its definition plus its paragraph head. "
        "`_comment_runs` says the opening line separates NOTHING today and is kept only because "
        "re-keying the list costs more than the string — this is the shape that would make it "
        "load-bearing again, so that paragraph now understates the price and needs rewriting"
    )

    for reading in _LIST_MARKER_READINGS:
        assert stranded[reading] > stranded_by_the_paragraph_unit, (
            f"splitting at list markers ({reading}) strands {stranded[reading]} round counts "
            f"inside records that DO state a control, against {stranded_by_the_paragraph_unit} "
            "for the paragraph unit — so it no longer costs more than the unit this file chose, "
            "and `_paragraphs`'s NOT BULLETS paragraph is claiming a price that is not being "
            "paid. The canonical shape is a control header with its rounds listed under it; the "
            "reason bullets are refused is that a split there severs the two"
        )

    strong_offenders = sorted(
        key for key, prose in _records()
        if _quotes_a_round_count(prose) and not _states_a_control_count(prose)
    )
    for reading in _WEAK_CONTROL_READINGS:
        weak_offenders, vouched_for_beyond_the_strong_form = [], 0
        for key, prose in _records():
            reads_as_a_control = _reads_as_a_control(prose, reading)
            if _quotes_a_round_count(prose) and not reads_as_a_control:
                weak_offenders.append(key)
            if reads_as_a_control and not _states_a_control_count(prose):
                vouched_for_beyond_the_strong_form += 1
        assert vouched_for_beyond_the_strong_form, (
            f"the weak reading {reading} accepts nothing the strong `_CONTROL_COUNT` refuses, so "
            "it is not a weaker form at all and the comparison below is between a pattern and "
            "itself. Whatever was meant to be measured here is not being measured — check that "
            "the reading still spells a DIGIT near the word rather than the tally the scanner asks "
            "for. This assert exists because the identity check underneath it passes trivially "
            "once the two patterns coincide"
        )
        assert sorted(weak_offenders) == strong_offenders, (
            f"weakening `_CONTROL_COUNT` to {reading} moves the offender set: "
            f"{sorted(set(strong_offenders) ^ set(weak_offenders))}. The comment above "
            "`_CONTROL_COUNT` calls itself the paragraph a later agent calibrates on and says the "
            "weakening moves NOTHING the ratchet can see, on any of five readings — under the "
            "paragraph unit that was measured true, and it is what makes the tally requirement "
            "rest on the pattern test alone. If this is red the ratchet has become a second owner "
            "of that requirement, which is worth saying there rather than discovering later"
        )

    uncontrolled_markdown = sorted(
        f"{name}::¶{_paragraph_head(paragraph)}"
        for name, text in _repo_markdown()
        for paragraph in _paragraphs(text)
        if _quotes_a_round_count(paragraph) and not _states_a_control_count(paragraph)
    )
    line_fed_offenders = sorted(
        f"{_LINE_FED_HOME}::¶{_paragraph_head(paragraph)}"
        for paragraph in _paragraphs((REPO_ROOT / _LINE_FED_HOME).read_text(encoding="utf-8"))
        if (_LINE_FED_ANCHOR in paragraph or _LINE_FED_MEASUREMENT in paragraph)
        and _quotes_a_round_count(paragraph) and not _states_a_control_count(paragraph)
    )
    assert line_fed_offenders, (
        "the material the SCOPE comment at the top NAMES — the line-fed rule — no longer "
        "quotes a round count without a control beside it, so that half of the scope comment is "
        f"false (uncontrolled markdown paragraphs found anywhere: {uncontrolled_markdown}). "
        "Rewrite it; the argument the scope actually rests on — that widening the scan is a "
        "behaviour change, VMCP-187 (712) — is untouched either way. This asks about THAT material, "
        "found by the anchor phrase or by the sha of its measurement, rather than about any "
        "`CLAUDE.md::` key: the looser form is satisfied by ANY other uncontrolled paragraph while "
        "the named one is clean, and a key form by any paragraph agreeing in its first 48 "
        "characters — both constructed and measured, VMCP-194 (724)"
    )
    assert set(line_fed_offenders) <= set(uncontrolled_markdown), (
        f"the scan over repo markdown does not reach "
        f"{sorted(set(line_fed_offenders) - set(uncontrolled_markdown))}, "
        "which the assert above just read out of CLAUDE.md by hand. The two are separate asserts "
        "because they fail apart: the one above reads the file directly and says nothing about "
        "`_repo_markdown`, so a scope narrowed to exclude CLAUDE.md — a dot-directory rule that "
        "grew, a rename, a filter added for speed — would leave it green while the markdown half "
        "of this test measured nothing at all. That is the WHOLE of what this asks. It used to "
        "name a second mode, `a _paragraphs that splits the file differently from the hand read`, "
        "and VMCP-218 (761) is the card that measured it CANNOT HAPPEN: both sides call the same "
        "`_paragraphs` on the same bytes of the same file, so they cannot disagree about the "
        "split — an assertion message naming a failure the code forbids teaches the next reader "
        "to look for the wrong thing when it finally goes red"
    )

    assert "a DATE does not name a TREE" in _testing_philosophy(), (
        "CLAUDE.md no longer says WHICH label a figure needs. It used to prescribe dating one, "
        "and this card measured that a date does not separate two trees 80 minutes apart in a "
        "repo landing several commits an hour: four counts here were true at one sha, false at "
        "the next, and honestly carrying the same date at both"
    )
