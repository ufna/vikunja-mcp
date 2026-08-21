# Тесты — мутационные прогоны, контроль, якоря измерений

> **Это ДОСЬЕ, а не правила.** Правило живёт в `CLAUDE.md → Тесты` — там оно короткое и
> обязательное к исполнению. Здесь лежит доказательная база: измерения, построенные
> стенды, опровергнутые формулировки и номера карточек.
>
> **Читай перед тем, как менять этот код.** Этот репозиторий уже чинил гарды
> рассуждением вместо измерения — по несколько раундов подряд. Если правило кажется
> избыточным, ответ почти наверняка здесь.

**The unit count above is a FLOOR (`500+`), and must stay one — never re-pin it
to an exact figure.** Its only job is a tripwire: a mistyped path makes `pytest`
select NOTHING and print "no tests ran", which looks very much like a pass. A
floor catches that and survives every landing; an exact count is stale by
construction here (at `wip_limit = 3` up to three worktrees land tests
concurrently — the pinned number was wrong twice in one day, 69 → 520 → 528, and
had drifted again to 529 by the time card 555 removed it). It is also an
attractive nuisance in a repo that verifies by running: **capture your own count
from your own run — a figure read out of this file was only ever true at the sha
that wrote it.** Touch the floor only if the suite ever shrinks below it, which
is itself worth noticing. Where a figure genuinely needs precision, name the
SHA it was measured at, because **a DATE does not name a TREE** — card 688
shipped FOUR counts in one commit that were true when it measured them at
`6dd2803` and already false 80 minutes later at `bba4fed`, the commit that
carried them, because a sibling landed in between. Three were labelled with the
day and one with "on this tree", and neither label names a tree; the two shas
share a date. Date as well by all means (the release section's landings-per-day
snapshot is genuinely a fact about a day), but a count over a tree belongs to
the tree. Better still, where a reader will ACT on the figure, assert the
property instead of writing the number — that is the one form that cannot go
stale, and `tests/unit/test_mutation_sweep_contract.py` carries the worked
example.

**That anchor is now CHECKED — but only as a LABEL** (card 699).
`tests/unit/test_measured_figure_anchors.py` resolves every figure written as
the preposition `at` plus a backticked 7-to-40-character sha, across this repo's
`.py` and `.md` prose, and fails unless that commit both EXISTS and is an
ancestor of `HEAD`. It never re-derives the figure: a wrong number under a real
anchor still ships, and what cannot ship is a number whose tree nobody can open.
**It is PROPHYLACTIC, and that is measured rather than glossed:** all 486 commits
reachable at `8d2734c` were scanned, asking of each whether its own tree carried
an anchor failing resolve-or-ancestor at that commit, and the answer is ZERO — the
idiom first appears at `1c295cb` and has never once shipped broken. So it has
caught nothing historically; it caught its own author, twice, on the day it
landed. Do not read the neighbouring `1761` story as a catch either: that figure
was measured in an uncommitted tree and shipped with NO anchor at all, which this
gate cannot see. What it does close is the step after — the moment you DO commit
and DO anchor, since `git rebase origin/main` before pushing is mandatory here and
it orphans a sha anchored to your own un-pushed HEAD. Even there the check only
ever LOOSENS as history moves: a sha that is not an ancestor yet goes green once
it is merged, prose unchanged, so a red is a prompt to re-measure and not a latch.
It is deliberately NOT a general
stale-count detector, and that is measured rather than conceded: three wider
triggers were run over the tree and every one is red on arrival against prose
that is perfectly correct — spelled-out numbers beside a counting noun (36 hits,
almost none of them measurements), digits beside one (which matches a CARD
number), and every backticked hex token (which matches PNG/JPEG/PDF magic bytes,
and commits a design doc quotes precisely BECAUSE a rebase orphaned them). The
anchor idiom separates those by itself, with no exclusion list. Deriving the
count instead was priced, not waved off: walking all 123 commits of one window,
the count in question moved at six of them — one real landing in ten — so that
shape turns an unrelated card's docstring edit into a red suite in a hot file.
The gate needs real history, so `lint-and-unit` checks out with `fetch-depth: 0`;
on a depth-1 clone not one anchor in this repo resolves, and a second test reads
the workflow as TEXT — no git — so a shallow checkout cannot silence both.

**And the sweep that HUNTS stale figures must not be LINE-FED — but do not
"fix" that by writing a cleverer grep: which lever reaches a wrapped figure
depends on WHICH grep, and the two on this machine need OPPOSITE ones.** Test
prose here is hand-wrapped near 100 columns, and that is the repo's wrap
TARGET (`line-length`) rather than a checked limit — the enforced ceiling is
`max-line-length = 110`, set at 120 by #669 and ratcheted down by #711, so
where a line actually breaks is a
convention, and a reflow can push a figure across a break without touching a
digit. Measured on `e86b2c9^`, where `test_api_kanban.py` carried a real one
at :1473-1474 ("… 5 failed / 102" ending one line, "passed for the whole
file" opening the next): read PER LINE, both greps return the SAME 15 hits
and miss it — with 665's literal space and with a `[[:space:]]\+` class alike
— so loosening the regex by itself recovers nothing. What DOES reach it
splits by implementation, and neither half transfers to the other. BSD grep
2.6.0-FreeBSD needs the FLAG *and* the class TOGETHER — and needs `-o` to
count at all: `grep -zo` with `[[:space:]]\+` yields 16 MATCHES and finds it,
against 15 for 665's literal space. Count matches, not lines: a bare `-z`
makes the file ONE record, so `-zc` answers 1 for either pattern, and
`-zo | wc -l` answers 17, because the wrapped match itself spans two lines.
`grep -zn` then numbers every match line **1**, trading the blind spot for an
inability to say WHERE. ugrep 7.5.0 is the mirror image: there `-z` is
`--decompress`, and its real null-data (`-00`) does not recover the figure
either — 15 matches, still blind. What works there is the PATTERN, and
specifically an explicit `\n` inside it:
`ugrep -n -o '[0-9]{2,}\n\s+passed'` prints `1473:102` and `1474|    passed`
on the default matcher, with `-E` or `-P` alike — while that same `-P` with
`\s+` in place of the `\n` falls back to 15 and misses it. And this BSD grep
has no `-P` at all. So the portable move
is to stop using grep as the READER. Read each file WHOLE, collapse every
whitespace run to one space, then match — and report the
DIFF against the per-line hits, never the raw list: the raw one is dominated
by what the old sweep already found. Price it, since a pattern loose enough to
cross a wrap also catches `<number> failed` in prose: with
`\d+ (?:passed|failed)`, `e86b2c9^` had THREE spanning-only hits — one
genuine, :1473, and two false, a docker port (`Bind for 0.0.0.0:3456 failed`)
and an illustrative `-> 7 failed` in the contract test named below — and from
`94bae3d` on only those two false ones remain, while the narrower
`[0-9]\{2,\} passed` finds no wrapped hit at all today. That WRAPPED count is
the durable half and the total is not: over `94bae3d` → `aadde71` →
`7718e6c` the total ran 245 → 319 → 330 while wrapped stayed 2 — and since
the sweep counts the file it is written in, the pin below moved that total
itself. A small footprint is the argument for fixing the METHOD rather than
the sites: what a sweep is FOR is its NEGATIVE answer, and 665's sweep
reported `test_api_kanban.py` clean at the exact site 668 was later filed
against. Coda, because it cuts the other way — 668's implementer and its
reviewer both re-measured that figure RIGHT, so what the sweep could not see
there was a missing ATTRIBUTION, not a stale total.

**A mutation sweep opens with an UNMUTATED CONTROL round on the SAME selection,
and every round count is a DELTA against it.** Sweeps here are hand-run — edit
the source, `pytest`, read the summary line, restore — and that summary line is
where the arithmetic goes wrong: `N failed` is a kill count only if the same
selection failed ZERO times before a single mutation was applied, and nothing in
a `-q` summary says whether it did. Not hypothetical: card 594 swept in a tree
where 30 tests failed constantly for an unrelated reason, so every row of a
six-row table came out inflated by exactly 30 and its headline was wrong by a
factor of 16 (true kill count 2). Constant failures survive a before/after
comparison intact and read as signal; a control round is the cheapest thing that
tells them apart. So run it FIRST and WRITE ITS FAILED COUNT beside the round's:
`control 0 failed; mutation 2 failed` still means something a month later,
whereas `control PASS` is a sentence that can be true and useless at the same
time. Record the FAILED count, never the pass total — the total moves with every
test the repo adds (the floor above), the failed count does not.
`tests/unit/test_mutation_sweep_contract.py` enforces that shape on every record
written from here on, and names the pre-existing ones it cannot fix. **"Beside"
is enforced IN THE SAME PARAGRAPH** (card 688): the scanner's unit is the
paragraph, not the whole docstring, so a control declared once at the top of a
long section stops vouching for the rounds below the next blank line — repeat it
there, or leave no blank line between the header and its rounds. It used to read
whole records, and then one clause about an unrelated mutation immunised every
other count in the docstring — which is not a hypothetical either: that is how
the record card 668 was filed against passed.

**And a control only helps if the ROUND was read right — so READ A ROUND BY COUNTING `FAILED`
LINES, never by the first `N failed` in pytest's output.** This is the step before the arithmetic
and it fails silently in the direction nobody checks: DOWNWARD. `pytest` prints a failing test's
own DOCSTRING inside the traceback, and in this repo those docstrings are sweep records, so they
say `control 0 failed` — which means the first `(\d+) failed` a naive parser finds in stdout is
the MUTANT'S OWN PROSE, not the summary line. Measured on this card's own stand, over the sweep
that landed tracker #763/#712/#754/#756/#759: control 0 failed, and in EVERY round that really
went red — 1 failed, 1 failed, 2 failed, 1 failed — that naive read returned **0**. Card #716 hit
the same parser on rounds that really failed 7, 2, 1 and 5 and shipped every one of them into a
table as "0 failed". A sweep table that lies in MINUS is worse than one that lies in plus: it
reads a live pin as BLIND, which is an invitation to delete the pin. What to read instead, both
halves, because either alone is defeatable: count the lines beginning `FAILED ` (pytest prints one
per failing test, in the short summary) and count the lines beginning `ERROR ` separately, since a
collection error is not a failure and a round that could not even import is not a kill. Then
CROSS-CHECK the selection size — pytest's `collected` line — against the control's: a round whose
selection differs from the control's did not measure a delta at all, it measured two different
things (tracker #767, which asked for exactly this and had no gate to hand it to). One gotcha,
measured here rather than assumed: `-q` prints NO `collected` line, so a script that asks for it
under `-q` gets nothing back and the cross-check quietly never runs — the same shape as the naive
parser it is meant to catch. Drop `-q` in a scripted sweep and read the summary yourself.

**And that cross-check has now caught something a control could not, in a shape worth naming: a
RESTORE step that reverted the FIX along with the mutation** (tracker #1168). The sweep ran in a
`git clone --no-hardlinks` of the worktree with the author's uncommitted work applied on top by
`git diff HEAD --binary` / `git apply` — the arrangement SKILL.md prescribes — and the per-round
restore was `git checkout -- src tests`. That clone's HEAD predates the fix, so the first restore
put the tree back to `origin/main` and the three rounds after it measured a tree with NO fix in it
while looking like ordinary rounds. Nothing in their `FAILED` counts said so, and neither did any
of the usual detectors: `vikunja_mcp.__file__` resolved inside the clone every round (it is the
right clone — just the wrong CONTENT), `git status` was clean, and the reverted tree has a
perfectly clean control OF ITS OWN, so a control could not fire either. What fired was `collected`:
5 on the opening control and 4 on every round after the first, because the fix adds a test. The
remedy is one line — commit the working tree INSIDE the clone before sweeping, so `git checkout --`
restores to the state under test. Read as a general lesson: `__file__` answers "which tree", the
control answers "was this tree already red", and only the selection size answers "is this the SAME
tree the control ran on". A sweep whose subject is a NEW test is exactly where the third question
has a different answer from the first two.

**That `collected` cross-check survives the sweep and dies on the way to `main` — and what kills
it is the workflow this file mandates** (tracker #888). The record is written on the tree the sweep
ran on; the push then goes through `git fetch origin && git rebase origin/main && <re-run the
gates> && git push`, and the re-run covers the GATES and not the PROSE. So the absolute lands
describing a tree that is in no history, and it does so through the one step nobody can skip. The
"last change" the paragraph above tells you to measure after is not YOURS — at `wip_limit = 3` two
siblings are landing beside you and the release bot bumps after every green one, so staleness is
the ordinary case, not the edge. Measured on card 840's landing at `04c126b`: the commit message
says `1136 passed` and the docstring record says `200 collected`, while that card's own reviewer
re-ran both on that very sha and got 1139 and 203, a sibling with three tests having landed in
between — and the same card's `[worklog]` carries the right 1139, so the author DID re-measure, for
the tracker and not for the prose. The sweep's own deltas reproduced exactly and no pin was blind:
what breaks is only the figure that certifies round and control measured the same tree, which is
the whole point of the cross-check. Two remedies, and the second is not the one that suggests
itself. The rule — measure a tree-property figure AFTER the last rebase, immediately before the
push — now lives in SKILL.md beside that chain, where the re-run is already prescribed. And the
ANCHOR: `N at `<sha>`` extends to sweep records, so `test_measured_figure_anchors.py` can resolve
the tree; it checks the LABEL and not the value, which is enough, because a named tree is one a
reader can open. **A gate that DERIVES `collected` and compares it to the record is deliberately
NOT built** — the form is already priced and rejected two paragraphs down (red on arrival, and it
turns an unrelated card's docstring edit into a red suite in a hot file), and here it would cost a
second full pytest run on top. Already-landed records in other cards are left alone: where they
carry an anchor it is honest for its own tree, and where they do not, the rule is for FUTURE
records.

**And inflation is the friendlier half.** That stand was rebuilt on 2026-08-02:
the same pre-622 sha exported twice, once with `.git` and once without, one
mutation (drop `.playwright-mcp/` from `.gitignore`), one selection
(`tests/unit/test_repo_browser_isolation.py`). The healthy tree read `control 0
failed` → `mutation 1 failed`; the corrupt one read `30 failed` BOTH times,
because the very test that mutation kills was already one of the 30. Read as an
absolute, that round overstates the kill 30×; read as a delta, it calls the
mutation UNCAUGHT. The same round lies in both directions at once, and the
control is what tells you so before you write either number down.

**A clean control does not mean the round MEASURED anything.** It is the cheapest
detector, not a complete one, and four forms bound it. CAUGHT: a
constant background failure (594/622 above), and stale bytecode — though that one
is narrower than card 624's summary of it, and its remedy weaker. Re-measured
2026-08-02 by reading the `.pyc` header: cache validity is the pair (source mtime
in SECONDS, source size), so a same-length rewrite replays the PREVIOUS budget
only when the mtime ALSO fails to advance a whole second — a scripted sweep's
hazard, not a hand edit's. And `PYTHONDONTWRITEBYTECODE=1` stops Python WRITING
bytecode, not READING it: with a stale `.pyc` already on disk, the same round
replayed the old value under that variable, and only deleting `__pycache__` moved
it. So do both — delete the caches, then set the variable so new ones do not
appear. NOT caught: a mutation that never reached the
interpreter — a tree copied with `cp -R` drags `.venv` along, which puts the
ORIGINAL `src` earlier on `sys.path`, after which control and rounds are all
green and four false greens in a row read as "nothing kills this mutation" (card
646). Copy a tree with `git archive` or `rsync -a --exclude .venv`, and print
`vikunja_mcp.__file__` in every round — that, and not the control, is what
catches this one. Re-measured 2026-08-02 on card 702, the `cp -R` failure is
RUNNER-dependent rather than constant, which is worse: the copied editable
`.pth` holds an ABSOLUTE path to the original `src`, so a bare
`<copy>/.venv/bin/python` imports the original and the mutation is invisible,
while `uv run` in that same copy re-syncs, rewrites the path, and the mutation
lands. HALF-CAUGHT, the fourth: a CONCURRENT WRITER — the second independent
pass mutating the same files in the same tree at the same time (card 667, rebuilt
on 702). Its foreign mutant landing under YOUR round is caught, and loudly, which
is how 667 found it; YOUR restore landing under ITS round is NOT — that one
silently reverts the mutant, the round reads green, and the auditor concludes the
pin is blind. Neither shows up in the per-script sha256 restore checks or in `git
status`, both of which stayed clean, because a per-script guard sees only its own
writes. The remedy is a separate tree, not a stronger control: SKILL.md's «ГДЕ он
работает» gives the auditor its own `git clone --no-hardlinks` plus a `git diff
HEAD`/`git apply` pair, since a clone carries only COMMITTED work and the text
under audit is usually uncommitted.

**A prose claim that quotes a string as being IN this repository is checked now,
in a small NAMED set of spellings — and the naming is the part you have to act
on.** Writing the tree from memory is how `889befd`, a commit titled for
correcting six measured claims, shipped a seventh: two example phrases asserted
to be here "each in test_api_kanban.py", one of which occurred nowhere in the
checkout. Nothing caught it — not CI, not review, and not the sweep scanner
whose own pattern is defined thirteen lines below that comment. Measured before the gate existed, whole `tests/unit` in an isolated
clone with the caches cleared: control 0 failed; a fabricated repo-content
quotation planted in a COMMENT 0 failed; the same planted in a DOCSTRING
0 failed. `tests/unit/test_repo_quotation_claims.py` closes the part a scanner
can close. It reads the SENTENCE around one of the assertive idioms its
`_CLAIM_TRIGGERS` names — read the SYMBOL, since the prose beside it only
paraphrases the list — and requires
every phrase quoted in that sentence to occur, whitespace-flattened, somewhere
in what `git ls-files` carries OUTSIDE THE FILE making the claim. That unit is
the sharp part and the obvious one is wrong: excluding only the claiming
PARAGRAPH lets the founding defect through, because at `889befd` the fabricated
phrase sat twice in one file — at line 88 in the sentence asserting it, and at
line 336 as a constructed row of a test — so the phantom vouched for itself. A
file arguing about a phrase quotes the phrase. Two consequences for whoever
writes such a sentence. **Use one of those idioms when you mean it**: the gate
is exactly as wide as the vocabulary it names, so a fabrication phrased "the
phrase X appears in Y.py" is invisible, and that spelling is outside the list
because including that spelling costs TWO false reds on this repo's real prose,
and BOTH are self-inflicted — this sentence and the one in the file saying the
same thing. **And when the quotation is NOT meant to be a repo string** —
another repository, a card description, a tool's output, a wording quoted
BECAUSE it was retracted — expect to name it in that file's ratchet with your
reason beside it; three entries are there already, one per class. The naive rule
was measured before it was rejected, not argued away: "every quoted string in
prose must be found in the tree" is 3,068 violations out of 11,596 quotations
against the 14 the shipped rule asks about (2,993 of 11,352 three card
landings earlier, at `3937b45`), and the first two of those move with every landing — so
the file asserts the RATIO and says how to re-run the digits.
This paragraph shipped that triple wrong once, which is the point of the rule
above and not a footnote to it: the digits were read off a working tree while
the code was still moving, and NO committed tree in this repository reproduces
them. Measure last, after the last change, or write an assert instead. An independent adversarial pass then built sixteen fabrications the gate
shipped green; the trigger, the scan and the delimiter set were widened to close
TEN of them at a measured cost of ZERO false reds, and the six still open are
named in the file rather than left for the next audit to find.
What the gate does NOT reach is written where it lives rather than promised here
— it checks PRESENCE, never meaning and never location; a bare pointer
(`:1473`, a card ref, a sha) is not a quotation at all; and the corpus is the
working tree, so a commit message or a card description is outside it. This
paragraph's own author committed the class while writing the guard, misquoting
that commit subject by one letter, and the file records it.

**A fake that diverges from the client makes a production branch untestable — and FIXING one
can DELETE coverage without a single red test (#1200).** Two 1:1 gaps were closed in
`tests/unit/fakes.py`: `update_task` raised a bare `KeyError` on an unknown id where the real
client 404s, and `get_task` never consulted `_forbidden`, so the fake could not produce a 403 on
a TASK at all — which is why #1179 shipped that branch with no fixture of any kind and #1190
reached it only by hand-rolling a wrapper around `api.get_task` in the test file
(`git log -S'_forbid_task'` names `5f26333` and nothing earlier). Both are one guard now,
because the client has one:
`VikunjaAPI.update_task` is read-modify-write and its FIRST statement is `self.get_task(...)`,
so on the real client an unknown id and an unreadable one both raise from the READ, before any
POST is built. A fake that answers 403 on the read and succeeds on the write is describing a
server nobody has.

**The interesting half is the second-order effect, and it is the reason this is written down.**
NINE tests in `test_workflow_cross_project_predecessor.py` drove the UNREADABLE-BOARD branch
through the fake's `forbidden` set — eight via the helper's flag, one inline — which, because of
the very gap being fixed, left the far TASK readable. Teaching the fake to 403 the task moves
every one of them to the 403-ON-THE-TASK branch, one branch EARLIER. Measured on that file
alone, 18 collected in both rounds at `bd4b5b5`: control
(pristine pre-#1200 fake, pre-#1200 fixtures) 0 failed; round (#1200 fake, pre-#1200 fixtures)
-> 6 failed; control after restore 0 failed. The THREE SURVIVORS are the finding: they would have
kept passing while measuring a different branch than their names and docstrings claim, and the
unreadable-board branch would have lost that much of its coverage without one red test to say so.
Read the two counts as SETS rather than arithmetic — the six reds include the inline fixture, so
of the eight `forbidden=True` tests five went red and three survived. So
the fake also learned the route #1198 measured live — `DELETE /projects/<pid>/views/<kanban>`
-> 200, no permission change, after which the board read 404s while the project, the far card and
the embedded relation all stay readable — and those tests now sit on `drop_kanban_view()`, which
is the only route into that branch anyone has measured.

**A fidelity fix is a scope decision, not a free win.** The same card declined a THIRD divergence
it could see: real 2.3.0 filters `related_tasks` by the reader's permission (two readers, same
card, same moment: the owner reads `{'blocked': [4]}`, an agent without access to the far project
reads `{}`), while the fake embeds unconditionally. Teaching it that would make the 403-on-the-
task branch unreachable by any permission route and need a third, race-shaped knob beside
`vanish()`; worse, pinning "the blocker silently vanishes" as expected fake behaviour would
pre-empt the human decision parked on VMCP-302 (1198) about whether that production gap is worth
closing. It is named as an open divergence in `_read_task`'s docstring instead — a fake may be
less capable than the server, never more generous, and "less capable AND says so" is the third
option worth taking.
