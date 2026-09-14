# Deleting inside a scratch stand: the one refusal no setting of yours lifts

Evidence for SKILL.md's rule in the second-pass stand recipe — *"A DELETION THROUGH A VARIABLE
stops the round"*. The rule is five lines because it has to be acted on, not studied; this is what
it was cut down from, and what a later editor needs before rewording it.

Card: VMCP-328 (1739). Everything below was measured against Claude Code 2.1.270 as installed at
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

## What "stops the round" means, and where it does not mean "hangs"

Scope it, because the two agent shapes this repo runs differ. In an INTERACTIVE session — the
`/loop` orchestrator and the per-task agents it dispatches, which is who the rule addresses — the
prompt waits for a human who is not there, and the round is dead. Under `claude -p`, as the sibling
hgdev-acp repo-agent runs, the same `ask` resolves to an automatic DENY: the run completes normally
and the refusal arrives as a `permission_denials` entry. Fatal to the command in both, a hang in
only one.

## What could not be verified

- The negative about settings and environment variables, as qualified above.
- The hook-downgrade half is read from the binary only; nobody constructed a `PreToolUse` hook
  returning `allow` and watched it be downgraded.
- The originating incident (a stalled `/loop` round) is the author's own observation, not replayed.

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
