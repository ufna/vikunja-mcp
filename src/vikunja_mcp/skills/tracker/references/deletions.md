# Deleting your own scratch files: the one refusal no setting of yours lifts

Evidence for SKILL.md's `$VAR/` rule, which lives in the scratchpad bullet of "What does
collide" — *"And the FORM matters"*. The rule is five lines because it has to be acted on,
not studied; this is what it was cut down from, and what a later editor needs before rewording it.

Cards: VMCP-328 (1739) measured the refusal and filed it in the second-pass stand recipe;
VMCP-330 (1777) moved it to the bullet that actually governs teardown — prompted by a live
incident, but carried by the section argument set out later, not by that one report. Everything
below was measured against Claude Code 2.1.270 as installed at
`~/.local/share/claude/versions/2.1.270`.

## What happened

A per-task agent tearing down a sweep stand wrote the obvious thing —

```sh
rm -f $D/*_test.go
```

— and the harness answered with a permission prompt: *"Dangerous rm operation on possibly-empty
variable path: $D/*_test.go"*. The session was running with bypass permissions already on.

## Bypass does not reach it, and neither does anything else you can configure

Read out of the installed binary, the check returns an `ask` decision carrying a named circuit
breaker (fields other than the two that matter elided):

```js
{behavior:"ask", message:…, decisionReason:{type:"safetyCheck", reason:…,
                                            classifierApprovable:false,
                                            circuitBreaker:"dangerousRemoval"}, suggestions:[]}
```

and the table those breakers are graded against settles the question by name:

```js
Zt = { dangerousRemoval:  {bypassImmune:true,  classifierRouted:true,  ...},
       backgroundOperator:{bypassImmune:false, classifierRouted:true,  ...}, ... }
```

`bypassImmune` is the whole answer, and the neighbour is what makes it evidence rather than a
suggestive name: the `&`-operator breaker one row below is `false`, so the field is read and the
two breakers behave differently. It is read right there — a predicate over that table asks whether
any of a decision's breakers is immune, and the permission pipeline returns the `ask` on that
predicate BEFORE the branch that would have turned bypass mode into an allow.

Three consequences follow, all from the same binary:

- **An allow rule does not lift it.** In the removal-detector's own call path the refusal is
  returned before the code ever consults an allow rule; the order is deny rules, then
  `dangerousRemoval`, then allow rules. The prompt says as much itself — *"cannot be auto-allowed
  by permission rules"*.
- **A `PreToolUse` hook answering `allow` is downgraded back to the prompt**, logged as *"Hook
  returned 'allow' for Bash, but ask rule/safety check requires full permission pipeline"*, and
  falls through to the full permission call.
- **No setting or environment variable turns it off.** Honest limit on that negative: the
  detector's call site is ungated and the detector reads no config, and nothing matching the usual
  opt-out spellings is there. A negative over a 207 MB binary is not provable by grep, so read it
  as "none at the call sites, none found", not as a proof.

## Measured end to end, not just at the regex

The regex below is only ONE gate of several in the permission pipeline, so clearing it is not the
same as running. The independent reviewer on this card closed that gap with nine nested
`claude -p --output-format stream-json` runs, each in a fresh directory seeded with
`aaa_only_test.go`, `bbb_test.go`, `keep.txt` and `tests/inner.txt`, and read two ways — the result
line's `permission_denials`, and which files actually survived on disk.

Under `--dangerously-skip-permissions`:

| command | outcome |
| --- | --- |
| `rm -f $D/*_test.go` | 1 denial, files intact |
| `rm -f "$D"/*_test.go` | 1 denial, files intact |
| `rm -f "$D/*_test.go"` | 1 denial, files intact |
| `rm -f $D/*` | 1 denial, files intact |
| `rm -f "${D:?}"/*_test.go` | 0 denials, exit 0, targets gone |
| `find "$D" -maxdepth 1 -name '*_test.go' -delete` | 0 denials, exit 0, targets gone |
| `rm -f $D/tests/*` | 0 denials, exit 0, `tests/inner.txt` gone |

Without bypass, with an explicit `--allowedTools 'Bash(rm:*)'`: `rm -f $D/keep.txt` ran (exit 0) —
the control proving the allow rule is effective — while `rm -f $D/*_test.go` was still refused with
the same safety-check text. So the allow-rule claim is closed by measurement and not only by
reading, and BOTH prescribed rewrites clear the entire pipeline: no later gate (the
statically-unresolvable-target family, the cd-before-a-relative-glob branch) stops them.

## The trigger, narrow enough to write around

The detector gives up unless the command holds both a `$` and the word `rm`/`rmdir`. Then it tests
each argument of the removal against

```js
/^[\uE020"']*\$(?:\{[A-Za-z_][A-Za-z0-9_]*(?::?-(?:["']{2}|"?\$\{?[A-Za-z_][A-Za-z0-9_]*\}?"?)?)?\}|[A-Za-z_][A-Za-z0-9_]*)[\uE020"']*\\?\/(?:[*?[{]|\$|\/|["']|\uE020|$)/
```

**Write `\uE020` as that six-character escape and never as the codepoint it denotes, and check
the bytes afterwards.** The binary is read with `strings`, which emits the escape — but the
authoring step silently converts it: the version of this evidence that shipped in
`docs/dossier/testing.md` stored three real U+E000-block characters (the only three in the tracked
tree), and so did the first draft of this file, written by hand with the escape typed out. Since
they render as nothing, the line reads as an empty alternative — `…|["']||$)` — which is both
wrong and executable, and executed in that form it returns "fires" for two rows the table below
calls clean. Two independent readers drew that false conclusion before anyone compared bytes. The
check is one line: fail on any character in the U+E000 block.

So: a `$VAR` / `${VAR}` / `${VAR:-…}` followed by `/` and then one of six things — a glob character
(`*`, `?`, `[`, `{`), a `$`, another `/`, a quote, the end of the token, or `\uE020`, which is the
placeholder the parser substitutes for a backtick or `$(…)` span. That last one is why
`rm -f $D/$(cmd)` fires too.

Run over candidates — the regex lifted out and executed, not read:

| argument | verdict |
| --- | --- |
| `$D/*_test.go`, `"$D"/*_test.go`, `"$D/*_test.go"` | fires |
| `${D}/*`, `${D:-}/*`, `$D/`, `$D/$F` | fires |
| `$D/aaa_only_test.go`, `$D/tests/*` | clean |
| `${D:?}/*_test.go`, `"${D:?}"/*_test.go` | clean |

**Quoting is not a fix, and the reason is not the one it looks like.** The quotes are not stripped
before the match — the pattern absorbs them itself, in the two `["']*` classes it carries, one
before the `$` and one between the variable and the `/`. That is precisely what those classes are
for. The harness's own output shows the quotes surviving into the reported target
(`Dangerous rm operation detected: '"$D"/*_test.go'`), which is the direct disproof of the
stripping story. The conclusion stands either way; the mechanism behind it does not, and this
paragraph exists because the first draft of the rule shipped the wrong one — reasoning substituted
for measurement, in a repository whose whole rulebook is about not doing that.

**A literal path component after the slash is enough to clear it**, which is why `$D/tests/*`
passes while `$D/*` does not.

## `${VAR:?}` is prescribed because it is CORRECT, not because it is clean

The brace alternative in that pattern admits only the `:-` and `-` default forms, so `:?` falls
outside it — but that is not the reason to write it. `:?` makes bash abort on an unset or empty
variable, which is the exact accident the check exists for: `rm -rf $UNSET/*` expanding to
`rm -rf /*`, in the harness's own wording. A rewrite that dodged the detector while keeping the
hazard would be worth nothing, and would deserve to stop working.

The other prescribed form, `find "$D" -maxdepth 1 -name '*_test.go' -delete`, wins differently:
with no `rm` word in the command the detector never starts at all.

## What the refusal COSTS, and where it does not mean a hang

Scope it, because the two agent shapes this repo runs differ. In an INTERACTIVE session — the
`/loop` orchestrator and the per-task agents it dispatches, which is who the rule addresses — the
prompt waits for a human who is not there, and the round is dead. Under `claude -p`, as the sibling
hgdev-acp repo-agent runs, the same `ask` resolves to an automatic DENY: the run completes normally
and the refusal arrives as a `permission_denials` entry. Fatal to the command in both, a hang in
only one.

## Where the rule is filed, and why it moved

VMCP-328 (1739) put the rule in SKILL.md's second-pass stand recipe, on the reasoning that the
refusal is a fact about the STAND. That reasoning was wrong. What SHOWS it is the regex re-run
over teardown forms below — measured, and independent of any one incident; the incident only
PROMPTED the move, and it reached this repo the SAME DAY: #1739's first commit is timestamped
12:49:16 and its last 16:02:47 on 2026-09-14, and #1777 was filed that evening. The true
interval may well be ZERO — the installed copy of SKILL.md refreshes once per session at MCP
server start, so that consumer session may never have carried the rule at all. A per-task agent
in a sibling repo (dogiators back-end), reported on this same Claude Code 2.1.270, tearing down
its own scratchpad after finishing work, wrote

```sh
rm -rf $SP/stmt $SP/f-*.go $SP/player.go.ORIG $SP/test.ORIG $SP/b-*.txt \
       $SP/p_*.txt $SP/p_*.n $SP/r_*.txt $SP/*.py $SP/*.sh $SP/status.before
```

and stopped on *"Dangerous rm operation on possibly-empty variable path: $SP/*.py"*.

Read against the trigger table above — through its regex, run rather than read off it — the model
predicted the SET exactly: of the eleven arguments only `$SP/*.py` and `$SP/*.sh` fire, every
other one carrying a literal character after the slash, which the table calls clean. The harness
named `$SP/*.py`; WHICH of a firing pair it names is predicted by nothing here, and the nine-run
table never exercised a multi-argument command at all.

So the defect was never the rule's CONTENT. It was that this agent was not building a stand. It
was doing ordinary teardown, which SKILL.md governs in "What does collide", and that bullet was
thorough about WHICH files to delete while saying nothing about the FORM.

**One correction to the card's own account, which said the agent had followed that bullet
correctly.** It had not, quite. `$SP/*.py` and `$SP/*.sh` are unqualified globs over the SHARED
scratchpad root, and the same bullet forbids those for an unrelated reason: they would have taken
a live neighbour's files along with its own. On this command the two rules happen to agree, and
the stop was right on the merits.

That does not rescue the filing, and being exact about why is the whole justification for the
move. The FORM rule fires on `$VAR/` + glob whether or not the variable holds a directory that is
yours alone — #1739's own originating command, `rm -f $D/*_test.go` over an agent's private stand,
is the case where it stops a delete nothing else objects to. And the scratchpad bullet's OWN
prescription leads straight into it. Re-running the regex above over teardown forms: `$SP/1777`,
`"$SP/1777"`, `$SP/1777/*` and `$SP/1777-*.log` are all clean, while `D=$SP/1777; rm -f $D/*.log`
FIRES. So an agent that takes the bullet's advice to give itself a subdirectory, and then holds
that subdirectory in a variable — the obvious next step — meets the refusal on a delete that is
correctly scoped and harms nobody. That is why the rule belongs in that bullet, and why its
example there is rooted at `$D` rather than at the shared `$SP` the incident used: prescribing a
rewrite of `rm -f $SP/*.py` would make a neighbour-destroying delete RUN.

The rule now lives in that bullet; the stand recipe keeps a one-line pointer, because a sweep
round's own mutate-and-restore deletions are exactly where #1739's originating incident happened.
The general lesson, and the reason this section is worth its space: a rule's SECTION is part of
the rule. One that only a specialist reader reaches is not filed, it is hidden.

## What could not be verified

- The negative about settings and environment variables, as qualified above.
- The hook-downgrade half is read from the binary only; nobody constructed a `PreToolUse` hook
  returning `allow` and watched it be downgraded.
- The originating incident (a stalled `/loop` round) is the author's own observation, not replayed.
- **#1777's incident likewise.** It is a human's report from a live consumer session plus a
  screenshot; nobody replayed it. What WAS re-run is the regex — over the eleven arguments, and
  over the teardown forms in the relocation section.
- **That the FILING was the defect is consistent with that incident but not ISOLATED by it.** The
  consumer session's installed SKILL.md may have predated #1739 outright, in which case no filing
  would have reached it and the incident says nothing about where the rule sat. What carries the
  relocation is the section argument — that teardown is governed by a bullet the rule was not in,
  and that the bullet's own prescription walks into the refusal — not this one report.

## What the rule cost in the rules layer, and the arithmetic that was wrong twice

SKILL.md 125 934 -> 126 406 characters, i.e. +472 for the rule, its pointer to this file and the
complete list of what may follow the slash; its ceiling in `test_rulebook_size.py` moved by the same
+472, 125 998 -> 126 470, so headroom is 64 characters before and after. That is the RAISE case the
gate's own header allows rather than the ratchet: the rules layer genuinely grew.

**The sizing story shipped wrong TWICE, and the second way is the one worth learning from.** First
version: it claimed the ratio assert capped the ceiling at 126 427 and that the rule had consumed
the slack. Under the assert that actually ships — `abs(ratio - 3.11) < 0.01`, band 3.10 to 3.12 —
the cap is `3.12 x 40 652` = **126 834**, and 126 427 is `3.11 x 40 652`: the cap under the assert
the same commit REPLACED.

| assert | ceiling band | max ceiling | max rule |
| --- | --- | --- | --- |
| `abs(r-3.10)<0.01` (replaced) | 125 615 - 126 427 | 126 427 | 429 characters |
| `abs(r-3.11)<0.01` (shipped) | 126 022 - 126 834 | 126 834 | 836 characters |

Second version — the rework that corrected the cap — then carried every figure DERIVED from it
across unchanged, while its own edits added 28 characters to the file. Each of those figures moved.
That is the same defect one layer down: a headline re-measured, its consequences copied. A third
round re-derived them all and cost 19 more, so the +472 above is three steps and not two:
125 934 -> 126 359 (+425, the rule) -> 126 387 (+28, the pointer) -> 126 406 (+19, the complete
list of what may follow the slash). All three are recorded because the second is the likeliest to
repeat — and naming only two of them is how this very paragraph read until a third reviewer added
the arithmetic up and found the last round costing nothing.

The figures, re-derived from the shipped tree rather than carried: **364 characters** of ceiling
slack remain. The 950-character first draft would have put the ceiling at 126 948, past either
cap, so cutting it was necessary either way.

**RE-CENTRING IS NOW LOAD-BEARING, WHICH IS THE OPPOSITE OF WHAT THE FIRST CORRECTION SAID.** At
the ceiling that ships, 126 470 / 40 652 = 3.111040, so `abs(r - 3.10) < 0.01` FAILS: reverting the
assert to 3.10 turns this gate RED. It was defensible as truth-maintenance when the rule cost 425
characters and the ceiling was 126 423 — that ceiling did pass the old assert — but the rule now
costs 472, past the old band's 429-character maximum, so the move has become necessary rather
than tidy. Do not read the earlier "it was not forced" wording; it described a tree that is no
longer this one.

**What re-centring costs, which no version stated until now.** It raised the band's FLOOR from
125 615 to 126 022. The ratchet's stated preferred direction is DOWN, and a future shrink now
meets the ratio assert 384 characters below today's file instead of 791. Cite #1640 for the
token half being a recomputation rather than a fresh tokenizer run; do NOT cite it as precedent for
re-centring, because #1640's move was forced by a band that had already gone red.

One more piece of bookkeeping, since the point of that comment is that a reader must be able to tell
a recomputation from a re-measurement: the figure it replaced was itself slightly wrong —
`126 000 x 0.2534` is 31 928, not the 31 934 the old header carried — and it was corrected silently
while the edit was presented as a pure recomputation. Today's pair is 126 470 x 0.2534 = 32 047
against 40 652 x 0.2608 = 10 602, i.e. 3.02x in tokens beside 3.11x in characters.

**VMCP-330 (1777) RELOCATED the rule, and the ceiling did not move in either direction.** Moving
it into the scratchpad bullet and leaving a one-line pointer at the stand recipe took SKILL.md
126 406 -> 126 450 characters, i.e. **+44**: a 77-character pointer, less the 33 the rule shed
being reworded for its new home (471 -> 438). Headroom therefore falls 64 -> 20 and `_CEILINGS`
is untouched, which is the point — a relocation teaches nothing new, so it may not buy new
budget, and it should not cost more than the cross-reference it leaves behind.

**Why it was not made to SHRINK instead, when DOWN is this gate's preferred direction.** The
obvious way to pay for the move was to send the glob-over-your-own-prefix evidence in that same
bullet down here too, and that edit was built: SKILL.md 126 406 -> 126 141, i.e. -265, with the
ceiling following to 126 205. It was REVERTED, and nothing of it survives to inspect — redo the
arithmetic rather than checking a measurement. Every ceiling below **126 428** — the point at
which the live ratio falls under the pinned CENTRE itself, `3.11 x 40 652` = 126 427.72, which is
well inside the band and nowhere near its floor of 126 022 — silently stops two mutation rounds
recorded in `test_rulebook_size.py` from reproducing: `_PINNED_RATIO` put back to 3.10 goes GREEN
where VMCP-328 (1739) measured it red, and the 3.12 mutant that VMCP-329 (1753)'s sweep calls
INSIDE the band falls outside it. Nothing in the gate goes red; the records simply become false,
and a shrink whose cost is rewriting somebody else's evidence is not bookkeeping. Read it as a
bound on the next ratchet step rather than as a reason never to take one: below 126 428, re-run
those rounds and re-state them in the same commit, or leave the ceiling alone.

## VMCP-333 (1852): the rule was READ, applied three times, and still missed the LOOP form

The refusal fired a third time, and this round is the one that says the earlier diagnoses were
about the wrong thing. #1739 read it as a fact about the STAND; #1777 refuted that and moved the
rule to the bullet that governs teardown. Both filings asked, implicitly, whether the agent HAD the
rule. This time it demonstrably did.

Measured on a live consumer session — dogiators back-end, session `a22ebd06`, per-task agent
`agent-a2bddb6b9ccd44e4c` (`tracker-build`, its card DOGEBACK-474 / 1841), whose dispatch brief
tells it to invoke the `tracker` skill at the start. Its transcript shows SKILL.md's own heading
and the string `BYPASS-IMMUNE` once each, so the rule was in its context. Every removal it issued,
in order:

| # | jsonl line | argument | outcome |
| --- | --- | --- | --- |
| 1 | 122 | `rm -f "$SP/main.go.orig"` | ran (literal after the slash) |
| 2 | 161 | `CK=$SP/roundtrip; rm -rf "$CK"` | ran (bare variable) |
| 3 | 166 | `rm -rf "${SP:?}/roundtrip"` | ran — rule applied |
| 4 | 183 | `CK=$SP/recount; rm -rf "$CK"` | ran (bare variable) |
| 5 | 197 | `rm -rf "${SP:?}/recount"` + `rm -f "${SP:?}/empty.md" "${SP:?}/picture.png"` | rule applied |
| 6 | 325 | `rm -f "$SP/main.go.orig2"` | ran (literal after the slash) |
| 7 | 364 | `for f in base_main.go … master_test.go; do rm -f "$SP/$f"; done` | **STALLED on the prompt** |

Three of those seven calls spell `${SP:?}`, all correctly, and the rule was in context for every
one of them. So neither delivery nor filing failed. What failed is that BOTH recipes the rule
offered were GLOB-shaped (`find … -delete`, `rm -f "${D:?}"/*.log`), while the commonest teardown
is "N files I made, by name" — and its natural spelling puts a second variable where the literal
had been. Rows 1 and 6 are what makes that a trap rather than an obvious sin: TWICE in this one
session a literal first segment cleared the check, which teaches that naming the file is what
makes a deletion safe.

**Row 7's outcome is INFERRED, and the count in the paragraph above is the reason to say so
plainly.** No refusal string occurs in either transcript — `Dangerous rm operation` and
`possibly-empty variable path` are both absent — so what is measured is the SHAPE of a block: the
`tool_use` is stamped `06:46:06.970Z` and its `tool_result` `06:48:54.353Z`, a **2 m 47 s** stall
against sub-second neighbours, with a `queued_command` attachment at `06:47:47` (the human typed
while it sat there). The result is `is_error: false` and the files are gone, so the deletion DID
eventually run: a human answered the prompt. "Refused" would be the wrong word — the command was
not rejected, the DRAIN was stopped, which is the cost this rule exists to avoid. An earlier draft
of this section wrote REFUSED and gave only six rows, dropping row 2 to a 500-character truncation
in the script that read the transcript, and then drew "five times over" off the row count; an
independent review caught both. Re-derive from the jsonl lines named above rather than from this
table.

The trigger table above already predicted row 6 — `$D/$F` is in its **fires** column. Re-run here
against the same regex, in python and in node independently, agreeing row for row:
`"$SP/$f"` fires; `"$D/one.log"`, `"${D:?}/${f:?}"`, `"${D:?}/$f"` and `"${D:?}"/*.log` are clean.

### The FILENAME half needs `:?` too, and the detector will not tell you

New here, and it is why the rule now says EVERY variable rather than the first one. Put `:?` on
the directory half alone and the form is detector-clean — which is exactly the problem. Two arms,
same stand, `f` set to the empty string:

| form | detector | result |
| --- | --- | --- |
| `rm -rf "${D:?}/$f"` | clean | **rc=0, the whole directory gone** |
| `rm -rf "${D:?}/${f:?}"` | clean | aborts (`f: parameter null or not set`), directory intact |

The quoted wording is bash 5.3's; zsh 5.9 refuses the same form as `f: parameter not set`. This
is the harness's own
accident one level down — its prompt names `rm -rf $UNSET/*` becoming `rm -rf /*` — and the check
does not see it, because the check only ever looks at what follows the FIRST variable.

End to end, the prescribed loop form was run in a seeded directory holding `a_main.go`,
`b_main.go`, `c_test.go` and `keep.txt`: `for f in a_main.go b_main.go c_test.go; do rm -f
"${D:?}/${f:?}"; done` produced no prompt and left exactly `keep.txt`.

### Re-read against a later binary

Everything above was measured against Claude Code **2.1.272**, two patch versions past the 2.1.270
this file's opening names, and nothing moved: the builder still returns
`{behavior:"ask", decisionReason:{type:"safetyCheck", classifierApprovable:false,
circuitBreaker:"dangerousRemoval"}}`. The `classifierApprovable:false` half is worth stating
separately from `bypassImmune`, because it closes a mode this file had not addressed: the `auto`
permission mode routes prompts to a model classifier, and this one is marked as not approvable by
it, so `auto` is no more an escape than bypass is.

**The ceiling moved with the rule, by exactly what the rule cost.** SKILL.md went
126 460 -> 126 801 characters (+341) and `_CEILINGS` 126 470 -> 126 811, so headroom is 10
characters before and after. That leaves 23 characters below the ratio cap of
`3.12 x 40 652` = 126 834, down from 364: the next addition to SKILL.md pays by shrinking
something, and the bound on that direction is the 126 428 floor argued above.

**One diagnostic was traded away at that ceiling, and it is named here so nobody restores it
blind.** The old bullet said the check fires "quotes and braces included"; the new one says
"quoting is no fix", which covers `"$D"/…` but no longer states that `${D}/…` — braces with no
`:?` — fires as well. The behaviour is still prescribed ("`:?` on EVERY variable"), only the
diagnostic is gone, and the trigger table above still carries the `${D}/*` row. Restoring the
clause costs about 11 characters against the 23 that remain.

**And "the regex" above is one of TWO.** Beside it in the same binary sits a sibling for
POSITIONAL and special parameters — `$1`, `$@`, `$*`, `$!` and their `${…}` forms — with the same
tail, so `$1/$f` fires exactly as `$D/$f` does. Nothing here was measured against it and no filed
verdict depends on it (every candidate in the tables uses a named variable), but a reader
extending those tables should test both.
