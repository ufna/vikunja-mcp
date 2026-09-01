# The report under test

**SYNTHETIC, AND IT CARRIES DELIBERATE FALSEHOODS.** This is a fabricated work report written
as the subject of an audit exercise, not a record of anything. SIX of its sentences are planted
defects and are wrong on purpose, and a seventh (S64) turned out to be one that nobody planted;
the rest are sound. Nothing below describes
vikunja-mcp, and no figure in it may be quoted anywhere else. What it is for is in `README.md`
beside it; which sentences are the plants is in `key.md`, which the audit run never sees.

Every sentence carries an identifier. The material it rests on is `material.md`, sections M1
to M5.

---

## What this card did

This card asked one question: whether the sweep's refusal to inspect a freshly written tree
costs anything a reader would act on. (S01) The apparatus is the sweep itself, run twelve
times, with each run's outcome recorded row by row rather than summarised. (S02) The table is
the whole of that record, and no section below adds a row to it. (S03)
The things the table does not carry are named where they bite. (S04) The landing window is a separate artefact and is read separately, because it
answers a different question. (S05) One quoted review comment from the predecessor card is
reproduced whole rather than paraphrased, so a reader can check what it does and does not
say. (S06) The function this report calls the grace window is reproduced whole for the same
reason. (S07) The report is written for a reader who has the material in front of them. (S08)

## The twelve sweeps

The twelve runs are M1, and they differ in how many trees a run was pointed at, in how many
ignored files those trees held, and in what the run then did. (S09) Duration is wall time for
the whole sweep rather than for one tree, and it rises with the tree count. (S10) The twelve
sweeps averaged 383 milliseconds. (S11) The spread is wide enough that the average is not much
use on its own: the shortest run is 180 ms and the longest 561. (S12) No sweep pointed at a
single tree took longer than 210 ms. (S13) Every sweep that found ignored files in a tree
still released it. (S14) Ignored files are present in five of the twelve runs. (S15) The
largest count of them in one run is five, in run 6. (S16) Three sweeps ended in a refusal, and
the table does not say which code each of them carried. (S17) A refusal is therefore not
readable from this table as protective or routine. (S18) Run 7 refused with no ignored file in the tree at all, so a refusal cannot be
read off the ignored column alone. (S19) Which code each of the three refusals carried is not in
M1, and M1 says so itself. (S20)

## The grace window

The grace window is the function in M4, and it decides whether a tree is inspected at
all. (S21) Nothing in M1 records whether any of the twelve runs deferred a tree, so the
window's effect on those runs is not in this record. (S22) A tree is deferred rather than
inspected because its most recent write falls inside the window, which is the comparison the
function returns on. (S23) The window's default is 120 seconds, written into the signature
rather than resolved from config. (S24) A stat that raises is treated as not recent, so an
unreadable path is inspected rather than deferred. (S25) That branch is a reading of the code
and was not exercised here. (S26) The 120-second window was measured to defer a tree exactly
once and never twice. (S27)

## The landing window

M2 is the fifteen landings the window holds, newest first, with the author of each. (S28)
Seven of them are the release bot's own bump commits, and each sits directly above the landing
that triggered it. (S29) Every one of the seven bumps landed within four minutes of the commit
that triggered it. (S30) The shortest of those gaps is three minutes and the longest four, so
the bot is a rival with a bounded cost. (S31) The bot authored 47 % of the fifteen landings in
the window. (S32) Only one of the fifteen landings is a human's. (S33) That one has no bump
directly above it, and the log does not say why. (S34) The remaining seven landings are agent
task commits. (S35) The window runs from 18:16 on the thirteenth to 09:12 on the fourteenth,
which is under sixteen hours. (S36)

## What the predecessor's review settled

M3 is the predecessor card's review comment, reproduced whole. (S37) The reviewer reported
constructing a tree that held nothing but ignored files and watching it be removed with exit
0. (S38) The mechanism the comment names is the porcelain status, which does not report
ignored paths at all. (S39) The comment is quoted rather than summarised, so its own wording
rather than this card's reading of it is what a reader checks. (S40) The reviewer established
that the guard is blind to ignored files under a directory the probe cannot open as
well. (S41)

## The table read a second time

A second reading of M1 asks what the twelve durations look like as a distribution rather than
as a list. (S42) The twelve values sorted run 180, 205, 288, 295, 301, 412, 430, 441, 455,
498, 530 and 561. (S43) The middle of the twelve is 421 ms, which is the average of the sixth
and the seventh. (S44) The bottom third of that list is entirely runs pointed at one or two
trees. (S45) The top third is entirely runs pointed at three or four. (S46) Exactly two of the
twelve runs held more than two ignored files. (S47) Neither of those two ended in a
refusal. (S48) The ignored count and the duration do not move together in this table, and
nothing here explains why they would. (S49) A distribution of twelve is thin, and this section
names shapes rather than fitting anything to them. (S50) The median sweep took 383
milliseconds. (S51)

## What the bot costs

M2 is read a second time here for what the bot costs rather than for who authored what. (S52)
Nothing in M2 records an integration round, a rejected push or a rebase, so what a bump costs
an agent is outside this artefact. (S53) Every bump in the log sits directly above the landing
that triggered it, so no landing in the window triggered two of them. (S54) The human landing
is the one exception to that alternation, and it has no bump of its own. (S55) Whether it
should have had one is not decidable from a log carrying only subjects and authors. (S56)
Seven bumps against fifteen landings is the rate this window shows, and it is a rate over one
window. (S57) The bot's bump lands within four minutes every time, so it can cost an agent at
most one integration round. (S58)

## The tool's own output

M5 is one sweep's printed result, and it is the only place a refusal code appears anywhere in
this material. (S59) The one tree M5 released reports its branch deleted, and M1 carries no
column that would have recorded the same thing. (S60) M5's `expected` list is empty, and this
material never says what would have gone in it. (S61) One released tree and one kept tree is
not a sample of anything, and M5 is quoted here for its shape rather than for its
numbers. (S62) The code the kept entry carries is `dirty`. (S63) M3 is the only artefact that
says anything about what the porcelain status does and does not report. (S64) M5's kept entry
is therefore a tree held back for ignored content, which is the case M3 describes. (S65)

## Boundaries

Twelve sweeps and one window are small samples, and every figure above is a reading of them
rather than an estimate of anything. (S66) Nothing in M1 says how its trees were built or where
they came from. (S67) The refusal codes are one thing M1
does not carry, and a follow-up that recorded them would answer the question S17 leaves
open. (S68) Nothing in this report licenses a change to the sweep's own rule, and none is
proposed. (S69)
