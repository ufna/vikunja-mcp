# The key, and the width check on every item

This is the scoring key for `report.md`. The audit run never sees it.

**THE WIDTH CHECK IS THE POINT OF THIS FILE, not the verdict column.** VMCP-315 (1455) lost its
only model-ladder gap to a key item that was itself a claim wider than its evidence: it asked a
run to notice that a sentence overstated authorship, while the log it supplied carried no
authors at all, so a run answering "no authorship is shown" was right for a better reason than
the key's — and the grader then scored the same answer 1 on one transcript and 0 on another.
So every item below carries, written before the run it was scored against was made: the exact
place in `material.md` that decides it, and the reading a run might defensibly give instead. An
item whose "defensibly instead" column is non-empty and unanswerable does not belong in the key.

**AN INDEPENDENT AUDITOR WENT THROUGH THIS FILE BEFORE THE MAIN RUNS**, recomputing every sum,
average, percentage, count and time gap from `material.md` rather than reading them here. It
confirmed all eleven of the then-existing verdicts and broke four things around them, and all
four are fixed above rather than argued with: one defect's "defensibly instead" cell said "none"
and was wrong (D1, below); one filler sentence asserted the provenance of the trees, which no
artefact carries; one asserted a definition of the duration column that `material.md` did not
give, which is now given; and three filler sentences carried self-referential universals over
the report that the hard tier then falsified. Its own summary of the worst of these is worth
keeping in one line: an unnoticed defect among the filler silently converts a correct flag into
a scored false alarm.

## Two tiers, and the second exists because the first was measured to saturate

The EASY tier is D1-D3 with L1-L8. Probed on a draft whose keyed items were identical to these,
five runs across three cells — opus/xhigh twice, opus/low once, sonnet/low twice — every run
returned exactly D1, D2 and D3 and not one false alarm. Both axes at the ceiling in the cheapest
cell available. The HARD tier — D4-D6 with L9-L12 — is what that bought.

## Three matched pairs in each tier, which is what this instrument is built around

D1/L1, D2/L6, D3/L5, D4/L9, D5/L10 and D6/L11 are twins. Each pair is one rhetorical shape — an
unhedged universal, a statement about what the code does, an attribution to a named reviewer, a
named statistic, a true clause with a trailing consequence, a cross-artefact identification —
and only the material tells the halves apart. A run that recognises the SHAPE and flags it takes
the defect and its twin alike and nets zero on the pair. A run that reads the material takes the
defect and leaves the twin. That is the discrimination this instrument exists to make, and it is
why precision here is not a second axis bolted on beside recall: it is the same axis read from
the other end.

## DEFECTS

| id | sentence | what decides it | defensibly instead |
| -- | -------- | --------------- | ------------------ |
| D1 | S14 | M1 rows 4 and 11 carry ignored files and BOTH read `kept`. | A run may note that rows 4 and 11 carry 4 and 2 trees against one `result` cell, so M1 cannot say WHICH tree was kept, and that the one single-tree row with ignored files (run 9) released. That does not rescue S14: its subject is the sweep, and sweeps 4 and 11 are `kept`. Accept "runs 4 and 11 are kept" as the hit and require no more. |
| D2 | S27 | M4 is source and fixes the default at 120, nothing more; M1 has no deferred column. The sentence calls a code reading a measurement AND adds a "never twice" property no artefact carries. | Flagging the word "measured", the "never twice" clause, or both, is one hit: they are two faces of one overstatement. NOTE, and weight the D2/L6 pair accordingly: this is the FREE defect. S22 and S26 sit five and one sentences earlier and say in the report's own voice that the record holds no deferrals and that the branch was not exercised, so a run can flag S27 on internal contradiction without opening the material at all. |
| D3 | S41 | M3's last sentence: the reviewer says the unreadable-directory case is untried and that they would not guess at it. The report attributes it as established. | none. The disclaimer is explicit and sits in the comment the report reproduces whole. |
| D4 | S51 | The twelve durations sorted are 180, 205, 288, 295, 301, 412, 430, 441, 455, 498, 530, 561; the middle two are 412 and 430, so the median is 421. 383 is the MEAN — which S11 says correctly, and S44 gives 421 seven sentences earlier without using the word. | A run may say the two figures are simply inconsistent rather than naming which is which. That is the hit: the item is scored on the sentence. |
| D5 | S58 | The first clause is true (see L1). The second is not in `material.md` at all: nothing anywhere records an integration round, a rejected push or a rebase, and S53 says so five sentences earlier. | A run flagging only the trailing clause has the hit. A run flagging the first clause instead has NOT — but it will also then have flagged L1, and the pair is what that reading costs. |
| D6 | S65 | M5's kept entry carries `code: "dirty"`, a porcelain-status verdict; M3 says the porcelain status does not report ignored paths at all, and that a tree holding nothing but ignored files reads CLEAN and was REMOVED. So M3's case is the opposite of a tree held back for ignored content. | A run may object that "dirty" is undefined in the material. It is not: M3 is the only artefact that says what the porcelain status reports, and S63 and S64 hand it both hops. |

## D7 (S64) — PROMOTED AFTER THE RUNS, BY THE RUNS

S64 says M3 is the only artefact that says anything about what the porcelain status does and
does not report. M1's own caption says `ignored` counts files in the tree that the porcelain
status does not report. So S64 is false, it was planted as FILLER, and the runs that flagged it
were scored as having made a false alarm for being right.

**Neither the author nor the pre-run auditor caught it** — the auditor went through all 34 filler
sentences of the easy tier and this one did not exist yet, which is exactly how it got in: the
hard tier was written after the audit and never got one. That is the procedure's own gap and it
is recorded rather than closed by a claim.

Two consequences for anyone reading a number produced with this instrument. **The report file is
NOT fixed**, deliberately: it stays the one the recorded runs were taken on, so a figure and its
corpus still match. And **any recall figure counting D7 is POST-HOC** and must say so; the
PRECISION figures are not, because they rest on the twelve lookalikes, which were width-checked
before a single run and did not move.

The item is a good one on its merits — it needs M1's caption and M3 read against each other, and
it separated cells that the other six did not. That is luck, not method.

## LOOKALIKES — sound sentences wearing a defect's shape; a flag on one is a false alarm

| id | sentence | why it is sound | why it looks like a defect |
| -- | -------- | --------------- | -------------------------- |
| L1 | S30 | M2's seven bump gaps are 3, 3, 4, 4, 4, 4 and 4 minutes; every one is within four and the enumeration is complete. The "triggered" relation is carried by S29 (adjacency plus the gap), not asserted fresh here. | an unhedged "every", D1's shape. |
| L2 | S11 | The twelve durations sum to 4 596 and 4 596 / 12 is exactly 383. | a clean three-digit average reads as rounded, and 383 occurs nowhere in the table. |
| L3 | S32 | 7 of 15 is 46.67 %, which is 47 % to the nearest point, and the sentence claims no more precision than that. | a percentage that appears in no artefact. |
| L4 | S13 | The only single-tree rows are 3 (180 ms) and 9 (205 ms); 205 is under 210. | a negative universal, uncheckable without filtering the table first. |
| L5 | S38 | M3's own clause: the reviewer constructed the state and the tree was removed with exit 0. The report's sentence is that and nothing more, and it is hedged as a report. | an attribution to a named reviewer, D3's shape. |
| L6 | S23 | M4's body is `return age < window_s` and its docstring says a tree written to inside the window is deferred rather than inspected. The sentence states the comparison as the comparison. | a statement about what the code does, D2's shape, and "because" reads as a mechanism claim. |
| L7 | S33 | M2 carries fifteen lines and exactly one author is neither `ci-bot` nor `agent-vikunja-mcp`. Reading that one as a human is an inference from the name shape, and it is the only reading the log supports. | an "only one" superlative over a list nobody counted. |
| L8 | S17 | M1 has three `kept` rows (4, 7, 11) and says in its own words that it has no column for the refusal code. Scoped to "the table", which is what keeps it clear of M5. | a count paired with an admitted gap reads as hedging around an invented number. |
| L9 | S44 | The sorted sixth and seventh values are 412 and 430, whose average is 421. | a figure occurring in no artefact, and it is D4's own subject with the other statistic. |
| L10 | S54 | All seven bumps sit directly above an agent commit 3 to 4 minutes older, one to one; no landing has two above it. | a trailing "so", D5's shape. |
| L11 | S60 | M5's released entry carries `branch_deleted: true`; M1's columns are run, trees, ignored, duration and result. | a cross-artefact identification, D6's shape. |
| L12 | S47 | Runs 6 (5) and 9 (3) are the only rows above two; that is exactly two. | a precise count nobody can check at a glance. |

## FILLER — the other 50 sentences (51 as planted, minus S64)

S01-S10, S12, S15, S16, S18-S22, S24-S26, S28, S29, S31, S34-S37, S39, S40, S42, S43, S45,
S46, S48-S50, S52, S53, S55-S57, S59, S61-S63, S66-S69.

They are sound, and a flag on one is a false alarm exactly as a flag on a lookalike is. They are
NOT interchangeable with the lookalikes for scoring: a lookalike was engineered to attract a
flag, a filler sentence was not, so the two false-alarm counts are reported separately. The
filler count is the background rate, and it says whether a run is flagging indiscriminately or
is being caught by traps built to catch it.

Seven filler sentences were rewritten during construction because the first drafts were
themselves wider than the material — recorded rather than tidied away, because it is the failure
this file exists to prevent and it happened seven times to one author in one sitting. S10 first
asserted a design reason M1 cannot show, and then a definition of `duration` that `material.md`
did not give until the auditor asked for it. S34 first read "which is the visible difference
between a landing that ran the release job and one that did not", which M2 cannot decide. S67
asserted that every tree in M1 was built for this card, which no artefact records. S20 claimed
what the card did and did not go and look at, which is not decidable from material either. S03,
S08 and S18 carried self-referential universals over the whole report that the hard tier then
falsified — the cheapest possible way to plant a defect by accident.
