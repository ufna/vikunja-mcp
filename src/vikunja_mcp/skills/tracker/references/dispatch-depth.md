# How deep to dispatch: the model lever at the call site, and the effort lever one level up

Evidence for the bullet **"The model is the per-dispatch DECISION, and the only depth knob the
`Agent` CALL itself takes."** in SKILL.md's "Who does the work: the orchestrator-pump and the
per-task agents". Open this before widening that rule, and ALWAYS before writing a sentence
about "effort": the shape of that lever was got wrong TWICE on the card that created the rule —
once from a reading of documentation, once from an over-general reading of a real measurement —
and each wrong version was operationally load-bearing while it stood. VMCP-314 (1443) settled it
by constructing the check the card said was missing; the settled answer is the next section.

## The surface, as it stands on the path a dispatch actually takes

Read off the schema of the `Agent` tool this harness hands an agent, at Claude Code
**2.1.252** — the version is named rather than the date, because a date does not name a tree and
this is a file that exists precisely because the surface moves:

* it takes `subagent_type`, `prompt`, `description`, `model`, `isolation`;
* `model` is an enum — `sonnet`, `opus`, `haiku`, `fable` — chosen PER CALL, with one documented
  exception in that same schema: it is IGNORED for `subagent_type: "fork"`, which always
  inherits the parent's model;
* there is NO effort, reasoning, thinking or token-budget parameter, under any spelling.

That is the whole knob set. The orchestrator dispatching a per-task agent and the per-task agent
dispatching its own implementer or auditor are in the same position: one lever at the call site,
and it names a model.

## The `effort` key: SETTLED, and both disagreeing sources were right

This section used to record a document and a measurement that contradicted each other, and a rule
built so it would not have to choose. VMCP-314 (1443) settled it, and "settled" means one specific
thing: **the `effort` key in a `.claude/agents/*.md` definition REACHES THE WIRE** — the value
written in the file arrives as `output_config.effort` on the subagent's own API request — **and the
human's repro was a correct measurement of the one model where it cannot.** Neither source was
wrong about what it saw. What was missing from both was a CONTROL.

Two boundaries belong in the same breath as the finding, because each of them is what a reader
would otherwise over-read. TRANSMITTED is not CHANGES BEHAVIOUR — no model ran in any of these
runs. And the lever does not COMPOSE with the model lever this rule already has: a call-site
`model` overrides the definition's own on EVERY dispatch, and where it resolves to `haiku` the
definition's effort is deleted along with it, silently. That deletion is not something this
rule's permitted downgrade reaches — the step stops at Sonnet class — so read it as a constraint
on any future MENU rather than on the model choice the rule already asks for. Measured below.

### How it was settled, because a code reading is exactly what got this wrong the first time

The hard part the card named is real: effort is not observable from inside a subagent, and a
subagent's self-report about its own depth is worth nothing. So the observable used is OUTSIDE it
— the bytes the harness sends on the subagent's behalf. A local HTTP server impersonating the
Anthropic Messages API was pointed at with `ANTHROPIC_BASE_URL`, under a throwaway
`CLAUDE_CONFIG_DIR` and a throwaway project holding nothing but `.claude/agents/*.md`. It answers
the first request with a canned `tool_use` calling the `Agent` tool, so the harness really does
spawn the subagent (`subagent_stats.spawned` came back as 1 in every run counted below), and it
logs the SUBAGENT's own outgoing request. The definitions differ ONLY in `model:` and the
`effort:` line. Claude Code 2.1.252 — the build `claude` resolves to today, which is NOT
necessarily the build a running session holds: the session that ran this card is 2.1.251 and other
live sessions on this box are older still. First-party provider, ten runs:

| `model:` | `effort:` | session effort | the SUBAGENT request's `output_config` |
| --- | --- | --- | --- |
| sonnet | low | (unset) | `{"effort": "low"}` |
| sonnet | (absent) | (unset) | `{"effort": "high"}` |
| sonnet | xhigh | (unset) | `{"effort": "xhigh"}` |
| opus | low | (unset) | `{"effort": "low"}` |
| fable | low | (unset) | `{"effort": "low"}` |
| haiku | low | (unset) | **no `output_config` at all** |
| sonnet | (absent) | `--effort xhigh` | `{"effort": "xhigh"}` |
| sonnet | low | `--effort xhigh` | `{"effort": "low"}` |
| haiku | low | `--effort xhigh` | **no `output_config` at all** |
| sonnet | low | `CLAUDE_CODE_EFFORT_LEVEL=max` | `{"effort": "max"}` |

In all ten runs the PARENT's own three requests carried the SESSION value — `high` in the six
runs that set none, `xhigh` in the three that passed the flag, `max` in the one that set the
variable. That is what says the harness was configured as intended rather than ignoring the
setting, and it is also the within-run control that makes each subagent row a DIFFERENCE rather
than a reading. Of the 40 requests captured, 38 carry an effort and the same 38 carry the effort
beta; the two that carry neither are the haiku subagents.

FOUR earlier runs are not in the table, and they are worth a sentence because all four looked
clean. Two causes, one symptom: one run reached a leftover server whose request counter a smoke
test had already consumed, and three classified the PARENT's request as the subagent's because the
word the probe matched on also occurred in its own working-directory path — a check answering
"that is the subagent" without having looked, the same family as a search that answers "there is
none". Every one of the four dispatched NOTHING and every one printed a plausible effort value.
What caught them was `subagent_stats.spawned`, which read 0 in all four, and that is why the field
is quoted above rather than assumed.

A second rig varied a DIFFERENT axis — what the CALL passes, and what a malformed value does —
because the first table only ever moves the definition file. Same method, four more runs, each
with `spawned` 1:

| definition | call-site `model` | subagent wire model | the subagent's `output_config` |
| --- | --- | --- | --- |
| sonnet + `effort: low` | `haiku` | `claude-haiku-4-5-20251001` | **none, and no effort beta** |
| haiku + `effort: low` | `sonnet` | `claude-sonnet-5` | `{"effort": "low"}` |
| sonnet + `effort: 2` | — | `claude-sonnet-5` | **none, and no effort beta** |
| sonnet + `effort: bogusvalue` | — | `claude-sonnet-5` | `{"effort": "high"}` |

Eight things follow, and only these eight:

1. **The key reaches the wire.** Two levels written in definitions (`low`, `xhigh`) across three
   models (`sonnet`, `opus`, `fable`), each arriving as the file spells it.
2. **An ABSENT key means INHERIT THE SESSION, not "the default".** With no `effort:` line the
   subagent went out at `high` under an unset session and at `xhigh` under `--effort xhigh`.
3. **A definition BEATS the `--effort` FLAG.** `effort: low` under `--effort xhigh` sent `low`.
   This is the orchestrator's question from the card, and the answer is: a session running deep
   CAN dispatch a shallower subagent — but only through a definition, never at the call site.
   Name the CHANNEL and stop there: `--effort` is the one that was exercised. The
   `settings.json` `effortLevel` channel was NOT, and that is the one this machine actually uses
   — `~/.claude/settings.json` here sets `effortLevel: "xhigh"`, while every run above was
   isolated under a throwaway `CLAUDE_CONFIG_DIR` and so ran at the model default. The resolver
   reads both through the same fallback, which is why it is expected to behave alike; expected is
   not measured.
4. **On `haiku` it is dropped whole.** Not clamped, not defaulted: the request carries no
   `output_config` at all, and the effort beta is missing from that request's beta header while
   every other row carries it.
5. **An environment variable outranks the definition.** `CLAUDE_CODE_EFFORT_LEVEL=max` turned an
   `effort: low` definition into `max` on the wire.
6. **The CALL-SITE `model` beats the definition's `model:`, and where it resolves to `haiku`
   that VOIDS the definition's effort.** `effort: low` on a `model: sonnet` definition, dispatched
   with `model: "haiku"` at the call site, went out on haiku with no effort and no effort beta.
   The converse also holds: a `model: haiku` definition dispatched with `model: "sonnet"` sent
   `{"effort": "low"}` — the call site wins EITHER way, and the effort SURVIVES where it wins onto
   a model carrying the capability. So the definition route is not a lever standing BESIDE the
   model lever: the call site overrides its `model:` on every dispatch, and additionally deletes
   the effort on the subset that resolves to `haiku`. Those are two different widths, and the
   second is the narrow one — the rule below never sends a call site there (see the menu
   paragraph). Read this row as being about the MENU a definition set would need, not about a
   downgrade this rule licenses.
7. **An INTEGER effort is worse than no key at all.** `effort: 2` sent NO `output_config` and no
   effort beta, where the same definition with no `effort:` line sends `{"effort": "high"}`. So
   it does not fall back to the default — it removes it.
8. **An invalid value degrades SILENTLY to no key.** `effort: bogusvalue` sent `{"effort":
   "high"}`, i.e. the model default, and `claude -p` wrote nothing at all to stderr.

### Why `haiku` is the exception, which is what makes the human's measurement the right one

The harness's baked-in model catalog gives each model a `capabilities` list. The entry for
`claude-haiku-4-5` carries exactly one capability, `context_management`, and neither a default
effort nor an effort cost index; `claude-sonnet-5`, `claude-opus-5` and `claude-fable-5` each
carry `effort`, `max_effort` and `xhigh_effort`. The request builder asks that capability question
FIRST and, when the answer is no, deletes any effort from the outgoing config and returns before
it can be set — which is exactly the two haiku rows, and the `effort-2025-11-24` beta is missing
from those two requests' headers while all 38 others carry it. That the alias `haiku` reaches
`claude-haiku-4-5` is not an inference from the catalog either: those two requests name the
model themselves, as `claude-haiku-4-5-20251001`. And the `Agent` tool's `model` enum offers no
other haiku to reach instead.

So the human built the single definition shape in which the key provably cannot do anything, and
reported what they saw. Their measurement was sound; the generalisation drawn from it was wider
than it. Keep the claim at that width and no wider: what is shown here is that `model: haiku` plus
an effort key REPRODUCES their outcome and carries a mechanism for it. Their build was not
recorded and nothing was captured, so "this is what they saw" is an inference, not a reading.
One competing story IS ruled out rather than argued away — that the key was simply wired later.
Every installed build on this box from 2.1.229 to 2.1.252 — seven of them — carries the spawn
layer that attaches the effort, and the same effort ALLOWLIST that haiku is absent from. Each
count is written with its NEEDLE beside it, because a count whose needle is not named cannot be
re-run by anyone: `!==void 0?[{kind:"effort",effort:` occurs exactly once in each of the seven
builds, and `capabilities:["effort"` exactly seven times in each. And 2.1.229's catalog entry for
haiku already reads `context_management` with no effort capability, exactly as 2.1.252's does. So
the feature is not new. (A previous round wrote "the same haiku denylist (seven each)" and named
no needle at all. The seven is real; the description of it was not. The stable seven is the
ALLOWLIST just named — the models that HAVE effort, haiku not among them — rather than a list of
exclusions, and the needles that do NOT give seven include the model id `claude-haiku-4-5`, which
gives 78/93/46/46/46/40/40 across the seven builds. Corrected rather than carried, and the lesson
is the needle and not the number.)

And this is no correction of the human anyway: the discriminator their repro lacked is a
control on another model, and neither VMCP-313 nor this file had one either until it was run.

The chain the value travels, named by the property names that survive minification so a later
reader can re-find it in a later build: the frontmatter `effort` is normalised against
`low`/`medium`/`high`/`xhigh`/`max` (or an integer) and stored on the agent definition; the
`Agent` tool resolves `subagent_type` to that definition; the subagent runner turns it into a
`kind: "effort"` entry in the child context's permission layers; the query reads the LAST such
entry and falls back to the session effort when there is none; the request builder writes it to
`output_config.effort` and adds the effort beta. Every link was read in the shipped binary, and
every link is also pinned by a row of the table — the reading and the wire agree.

### Four things that silently change or remove the value — one measured, three only read

* **`CLAUDE_CODE_EFFORT_LEVEL` outranks the definition.** MEASURED, last row of the table: `max`
  in the environment turned an `effort: low` definition into `max` on the wire. A machine-level
  variable that silently overrides every agent definition on the box is worth knowing before
  anyone builds a menu. Its two magic values, `unset` and `auto`, take the resolver down a branch
  that yields no level at all — READ IN CODE, NOT MEASURED, and not the same as absence.
* **A launch pin on the `opus-4-7`, `opus-4-8` and `fable-5` families puts the MODEL's default
  effort AHEAD of the definition's** until that pin is cleared. READ IN CODE, NOT OBSERVED: the
  `fable` row above honoured `low`, so the pin was not biting on this machine, and this file does
  not claim to know when it does.
* **An organisation ceiling clamps the level down**, and `max`/`xhigh` fall back to `high` on a
  model lacking those capabilities. READ IN CODE, NOT EXERCISED — the probe ran on a dummy API key
  with no organisation behind it, so no ceiling could apply.
* **An INTEGER effort is validated and then never sent, and a BOGUS one is dropped in silence.**
  MEASURED, the last two rows of the second table. The validation message offers an integer and
  the normaliser accepts one, but the request builder writes the field only for a string, so
  `effort: 2` ships NO effort — strictly worse than writing no key, which ships the model default.
  A misspelt level does fall back to that default, and `claude -p` wrote nothing to STDERR —
  which is the one channel that was WATCHED, so read "silently" as being about stderr rather
  than about everywhere. A message does exist: the agent-file parser normalises the frontmatter
  value and, when the value is present and the normaliser yields nothing, hands a line naming
  the file and the bad effort to the log helper. That helper defaults its level to debug, and
  the logger's own guard returns false when the process is neither the vendor's own nor in
  debug mode — so outside a debug run the line is dropped before it reaches any stream or file.
  READ IN CODE, NOT OBSERVED: no debug-mode run was made, so where it WOULD surface is a
  reading; the only thing measured is the silent stderr.

### Where this evidence stops

It shows what the harness SENDS. It does not show what the model then does with it: the probe's
server was a stub that never ran a model, and whether the effort field changes the depth of the
reasoning is a property of Anthropic's service, not of anything this repo can measure. The stub
also never returns an error, so a service that REJECTED the field or the beta would look exactly
like a service that accepted them — that blindness is structural to the method, not an oversight
in this run. The
harness's own catalog does price the levels — an effort cost index per model, which its UI turns
into a "~Nx" label relative to that model's default — and those numbers say the levels are
EXPECTED to differ, which is not the same as this board having observed that they do. With `high`
normalised to 1: `claude-sonnet-5` reads low 0.47 / medium 0.74 / xhigh 2.41 / max 5.59;
`claude-opus-5` low 0.67 / medium 0.76 / xhigh 1.6 / max 1.7; `claude-fable-5` low 0.6 /
medium 0.77 / xhigh 1.74 / max 1.91. Read them the way the price table below is read — the shape
of a ladder, never this repo's bill.

Two further boundaries on the probe, so a reader can judge it rather than trust it. It
authenticated with an API key against a local base URL, not with this repo's usual subscription
session, and provider-dependent behaviour is therefore untested. And it isolated
`CLAUDE_CONFIG_DIR` and ran in a scratch project, so no user-level settings, agents or hooks were
in play — which is what makes the rows comparable to each other and also what stops them from
describing any particular real session.

**One surface exposes `effort` at the CALL, and it is not this one.** An `effort` option on
`agent()` inside a **Workflow** script — recorded by the human, and confirmed in the binary by
VMCP-313's reviewer. That is a different surface from the `Agent` tool, and no dispatch described
in this rulebook goes through it, so it neither rescues the orchestrator's per-card decision nor
makes "there is no effort knob at the call site" wrong. It is recorded because a reader who finds
it will otherwise think this file missed it.

The session-wide controls are real, and ONE of them now has a measured relationship to a dispatch
rather than a suspected one. `effortLevel` and `modelSettings` in `settings.json`, the `/effort`
command, the `--effort` launch flag and `MAX_THINKING_TOKENS` all move the whole SESSION — and a
subagent whose definition names no effort INHERITS that value, which the two `(absent)` rows of
the first table show end to end for the `--effort` FLAG. A round ago this file said the
propagation was not confirmed here; for that one channel it now is, and for `settings.effortLevel`
it still is not. Either way it makes them worse as a per-card lever, not better: lowering the
session floor lowers it for the cards that most need the depth, and it is now measured to lower it
for their subagents as well.

**Recorded as a defect class, not as trivia — and the history is now THREE steps, not two.** The
brief that launched VMCP-313 asserted the asymmetry "model is per-dispatch and dynamic, effort is
per-agent-type and static" and sourced it to documentation. The human's measurement retracted it
mid-tick. That retraction was then itself too wide, and what narrowed it was neither reading nor
reasoning but a control on a second model. Each step was believed at the time and each was
operationally load-bearing. The rule the class supports has not moved: an `effort:` key a future
reader finds in the docs is not evidence it is wired, an `effort:` key that did nothing once is
not evidence it never does, and the way out of both is a constructed check with a control in it.

**Why this repo still ships no `.claude/agents/` definitions — now a DECISION on settled evidence,
where it used to be one on unsettled evidence.** The premise that used to carry this paragraph is
gone: the `effort` key is no longer disputed, and a maintained set of definitions IS a depth lever
that reaches a per-dispatch choice. Three things nonetheless keep it unbuilt, and the FIRST is new
with the measurement rather than inherited from the old argument.

* **Its MODEL column does not compose with the lever this rule already uses.** Point 6 above:
  the call-site `model` beats the definition's, in both directions. So a menu entry that PAIRS a
  model with an effort is already half-overridden by the per-card model decision — the PAIRING is
  what fails to compose, it fails on EVERY model, and nothing has to be voided for it to fail.
  **The voiding case is real but sits outside this rule, and an earlier round of this file said
  otherwise.** It claimed the rule's permitted downgrade lands on the model that deletes the
  effort. It does not: deleting the effort needs the resolved model to be `haiku`, while the rule
  below permits ONE step, names its destination as Sonnet class — which carries the capability,
  and which point 6 measured KEEPING an effort — and names the bottom rung UNMEASURED rather than
  free. Nothing reaches `haiku` by another door either: the baseline is stated absolutely rather
  than relative to the dispatching agent's own model, so a downgraded agent does not step again;
  `model` is ignored for a `fork`; and the auditor dispatch is excluded by name. So the deletion
  bites a DELIBERATE haiku dispatch, which this rule does not license. What remains is a hazard of
  the MENU rather than of the rule, and it is worth designing around: a menu that itself wrote
  `model: haiku` for some cheap role would carry a DEAD `effort:` key, with nothing announcing it
  on any channel that was watched (points 4 and 6). Note the width: no logging site for the
  DELETION path was located at all, so that is unverified in both directions rather than
  established — a wire capture cannot see a local log line. That is a design problem, not a
  wording problem,
  and it has to be solved before a menu is worth writing.
* **No rung of either ladder has been measured on this board.** Nothing here has measured whether
  a shallower agent costs verdict quality on any role — the ladder section below says the same
  about models. Building a menu today is picking levels by taste and shipping the taste to every
  consumer through `stable`.
* **The mechanical objections stand.** `.gitignore` here excludes `.claude/*` with a single
  re-inclusion for `settings.json`, so a definition is untracked local state or a new exception;
  and a fixed menu is coarser than the per-card judgement this rule asks for.

So the rule below is UNCHANGED by this card, deliberately: what changed is a fact, not a decision.
Building the definition set is filed as VMCP-315 (1455) — a card about measuring a rung and about
the composition problem, not about writing a menu down.

## What each lever moves, which is where the assumption goes wrong

`model` changes the PRICE PER TOKEN. What it does NOT do is shrink the work: a cheaper agent
given the same vague brief still opens the same files and the same history, and may take MORE
turns to get there — the bundled `claude-api` skill, this file's own price source, says exactly
that ("a cheaper request that needs more turns or retries to finish the job isn't cheaper").
Unmeasured here, and stated as a caution rather than a mechanism.

The only per-dispatch lever on the NUMBER of tokens is the BRIEF. Nothing gates it, nothing has
to ship for it to work, and it is available on every dispatch this rulebook describes.

List API prices, for the ratio and not the absolutes — from the bundled `claude-api` skill's
model table, cached 2026-06-24, quoted per million tokens as input/output: Fable 5 `$10/$50`,
Opus 5 `$5/$25`, Sonnet 5 `$2/$10`, Haiku 4.5 `$1/$5`. So one rung down is roughly 0.4x per
token and one rung up is 2x. **These are the API list rates and NOT necessarily what a Claude
Code session is billed** — read them as the shape of the ladder, never as this repo's bill.

## The card that created the rule

Reported by the human from this session's own fleet, and it is the whole reasoned justification
the card asked for. ONE tracker card — deleting two unbuildable binaries and editing two lines of
comment, ZERO behaviour change — cost **643k subagent tokens**, of which **337k** were two rounds
of independent review on the senior model. The second of those rounds reviewed a diff of two
files, `+8/-5`, without one line of code in it.

That is 52% of the card's subagent spend on reviewing something a revert undoes completely. The
finding is not "review is too expensive"; it is that **the spend was not distributed by risk at
all**. The same 337k would have been cheap on a guard in `workspace_cmd.py` — `docs/README.md`
records that a guard here would get "fixed" by reasoning instead of measuring and the fix would
open a new hole, four rounds running, on one file. (That count is in the docs INDEX; CLAUDE.md's
own opening says "several rounds running" and names no file, and the workspace dossier gives no
count at all. Cite the index for the four.)

The human's request, translated: use a shallower reasoning depth for review, and think about
what to optimise — model, reasoning? — because minor tasks are burning a lot of tokens. Note
what they did NOT ask for: review is not being weakened, and no card is being taken out of the
offering. What is asked is that depth track what is at stake.

**Both absolutes are wrong for one reason.** "Senior model always" — the sentence this rule
replaced, which read that a downgrade to Sonnet/Haiku class was forbidden without the card's own
permission — and "cheapest always" fail identically: neither looks at the card. A flat rule
cannot distribute anything by risk, because it has not asked what the risk is.

## The ladder, and how much of it this repo has actually measured

**Until VMCP-315 (1455), none of it — and what that card added is one role, one task and one
sharply separated cell, so read this paragraph as the state it CORRECTED and not as a state that
is gone.** There is still no measurement here of Haiku-class or Fable-class output on any role in
this pipeline; what the section below adds is Opus-class against Sonnet-class, and the effort
ladder on both, on a single closed-book auditing task. Before it, what existed was the token
accounting above and the list prices, and not one A/B of verdict quality by model on this board.
Checked rather than assumed, and the check took two attempts. `git grep -i -l` for each of the
four names over the tree at the commit this landed on returns: `Sonnet` and `Haiku` only in
SKILL.md — the policy sentence this rule replaces; `Opus` there plus a plan document and a
`settings.json` FIXTURE in `tests/unit/test_install_skill.py` that has nothing to do with
dispatch; `Fable` only in a plan document, inside quoted `Co-Authored-By` trailer strings. So a
reader who greps and finds a model name has found a POLICY, an AUTHOR or a fixture — never a
measurement. **That last clause expired with VMCP-315**, whose section below carries all four
names inside a measurement in this very file: re-run the grep, do not quote its old result. The
two attempts are the point: the first used `git grep -E "\b(sonnet|...)\b"`
and returned NOTHING, because `\b` is not a POSIX ERE word boundary and the pattern silently
matched nothing at all. That is the same family as the `git log -S` case-sensitivity trap SKILL.md
already records — a search that answers "there is none" when it never looked. VMCP-314 added a
third member while reading the harness bundle, and its REWORK round had to re-measure it, because
the first wording named the wrong tool and the wrong single cause. The `grep` an agent gets in
this harness is a SHELL FUNCTION, not `/usr/bin/grep`: it runs the harness binary as ugrep 7.8.4
with `-G` and `-I`, and each of those two flags answers "no hits" for a reason of its own.
`-I` skips a BINARY haystack whatever the needle — on the bundle even a brace-free needle returns
exit 1 and prints no count at all, where `/usr/bin/grep` finds it. `-G` then stops a `${...}`
placeholder from matching as literal text: with `-a` lifting the first cause, a braced needle
still comes back 0 hits, exit 1, while `-aF` on that identical string returns 1. The brace is the
discriminator — a brace-free needle matches under both tools, and `-F` on the braced one matches —
but note that the 0 hits are what was MEASURED; reading it as a BRE interval quantifier is the
natural explanation, not an observation of the parser. And `/usr/bin/grep`, the real BSD grep
2.6.0-FreeBSD, does NEITHER of these, with or without `-F`: the tool the first wording blamed is
the one on this box that gets it right, which is why a reader who reproduced on `/usr/bin/grep`
would have read the whole record as false. Two lessons rather than one: reach for `-F` when the
needle is code, and for `-a` when the haystack is a binary. On a binary, `-F` alone does NOT
suffice — it takes `-aF`, and it was reporting `-F` as sufficient that hid the second cause. Say
WHICH grep you ran, too: "grep" names two different programs on this box, and the record is not
reproducible without that word.

Two consequences, and they are why the rule steps one rung and stops:

* a permitted downgrade is ONE rung, because one rung is the smallest change that can be
  observed and undone, not because two was measured to be worse;
* the bottom rung and the top rung are named as UNMEASURED rather than given invented criteria.
  A rule that assigns Haiku a task class it has never been run on is not a cheaper rule, it is an
  unmeasured claim wearing a procedure's costume.

If someone measures a rung, that measurement belongs here, with the card it was run on.

## One rung, measured — VMCP-315 (1455): one role, one task, 34 scored runs

The first A/B of output quality by depth on this board. **What it separated is ONE cell; the other
seven sit at or beside the instrument's ceiling and no comparison among them rejects.** Read that
before any number below, because the useful result here is a boundary and a mechanism, not a ladder.

**The role and the instrument.** The second-pass prose auditor, picked because a wrong verdict there
is cheap and reversible. The task is CLOSED-BOOK: a synthetic report in this repo's house style,
supplied together with the raw material it claims to rest on, with defects planted in the report —
each one a contradiction of that inline material, so scoring needs no repo knowledge and no
judgement about what is really true. The prompt is the second pass's own question, "which claim here
is wider than its evidence?". A deny list withheld the fourteen tools an auditor would reach for,
and every one of the 34 runs came back `num_turns: 1` — which is what evidences the closed-book
condition. Say the runs USED no tools rather than that they HAD none: the deny list names tools, and
naming a list is never the same as closing a surface.

**The rig.** `claude -p "<task>" --model M --effort E --output-format json --disallowed-tools …`
from a scratch cwd, Claude Code 2.1.252. Two observables, both OUTSIDE the agent: the run's own
`usage` block — output tokens, `output_tokens_details.thinking_tokens`, `total_cost_usd` — and
recall against the key. Grading was a SEPARATE `claude -p` run per transcript that never saw which
cell produced it, joined to the cell labels only afterwards. That blinding is load-bearing: the
runner printed cell labels to its own log, so the author's own reading was NOT blind, and the
grader's is the primary score.

**What the `--effort` flag does on the wire, measured with a control.** VMCP-314's stub method
rebuilt — a local HTTP server impersonating the Messages API under `ANTHROPIC_BASE_URL` — but with
this box's REAL `CLAUDE_CONFIG_DIR` in play, which is what this leg adds to that card's isolated
runs. Six captures, each a one-turn `claude -p` making exactly one request, so the pairs and not a
within-run counter are what make each row a difference; every row carried the `effort-2025-11-24`
beta and the model it named:

| what varied | `output_config` |
| --- | --- |
| `--effort low` | `{"effort": "low"}` |
| `--effort xhigh` | `{"effort": "xhigh"}` |
| no flag, this box's `settings.json` | `{"effort": "xhigh"}` |
| `CLAUDE_CODE_EFFORT_LEVEL=low` **plus** `--effort xhigh` | `{"effort": "low"}` |
| `--effort low`, `--model opus` | `{"effort": "low"}` |
| CONTROL: throwaway `CLAUDE_CONFIG_DIR`, no flag | `{"effort": "high"}` |

So the precedence on this build is **env > `--effort` > `settings.effortLevel` > model default**,
and each link is a pair of rows differing in one thing — the control row being what stops the third
link from being a reading of a value that was there anyway. Each of the six rows has exactly ONE
surviving stub capture, and for two of them — `--effort xhigh`, and the env row that carries the
first link — that capture is a LATER re-run from the same rig: the originals were not kept, so those
two rows rest on a reproduction of the recorded value rather than on the recording of it. Two
further limits, both read off the files. The stub logs the REQUEST and never the INVOCATION, so no
capture names its own row and the mapping is by filename and mtime. And the pairs carrying the
second and third links do not literally differ in one thing: the no-flag row's capture records its
model as the unresolved two-word string `sonnet NOFLAG` — the zsh signature described below — while
the control's is a real `claude-sonnet-5`. Neither disturbs the effort values, which are all those
rows are read for, but "differing in one thing" is the design and not the record. That closes
VMCP-314's open item, which said that `--effort` was the channel exercised and that the
`settings.json` `effortLevel` channel was not, **for the SESSION's own request only**; the SUBAGENT
leg of the `settings` channel is still unmeasured. **And none of this says anything about the 34
scored runs**: no wire was captured on a run that reached the real service, because the stub
replaces it. What evidences the lever on the scored runs is `thinking_tokens` in their own
responses, which is the EFFECT and not the lever.

A shell bug is recorded because it produced a plausible false finding: a first pass through these
arms used `set -- $SPEC` in **zsh**, which does not word-split an unquoted parameter, so every arm
launched with a malformed `--model`/`--effort` pair and every one came back `xhigh`. Read straight,
that says "`settings.effortLevel` silently overrides `--effort`" — striking, publishable and false.
Use an array or `${=SPEC}`, and re-run anything surprising before writing it down. Those captures
were discarded and the table above is a clean re-run, not a repair of them.

### The numbers

Baseline is **opus/xhigh**, because subagents here carry no `.claude/agents` definition and so
INHERIT the session effort (VMCP-314 point 2), and this box's `settings.json` sets
`effortLevel: "xhigh"`. Recall is out of TEN: an eleventh key item was planted, found defective
during review and dropped whole — see the boundaries. Cost is not quoted as a run mean, because
`input_tokens` is **2** in all 34 runs (the prompt is entirely cache-read) and cost is therefore an
exact function of output tokens: a fixed cached-input term of $0.0098 (opus) / $0.0050 (sonnet) plus
output at the list rate. 13 of the 34 runs additionally paid `cache_creation` and cost 1.84x the
other 21, which is noise about the CACHE and not about the arms, so the column below is the
cache-warm figure derived from the cell's output tokens. `costBasis` in these records reads `list`;
read the ratio, never this repo's bill.

| cell | n | recall /10 | mean | thinking tok | output tok | $ warm | x base | wall |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| opus/xhigh (baseline) | 4 | 9,10,10,10 | 9.75 | 2016 | 3172 | 0.0891 | 1.00 | 42s |
| opus/high | 3 | 10,10,10 | 10.00 | 1015 | 2008 | 0.0600 | 0.67 | 27s |
| opus/medium | 6 | 10 x6 | 10.00 | 833 | 1850 | 0.0561 | 0.63 | 29s |
| opus/low | 4 | 10,10,10,10 | 10.00 | **0** | 862 | 0.0314 | 0.35 | 17s |
| sonnet/xhigh | 4 | 9,10,10,10 | 9.75 | 8179 | 9247 | 0.0975 | 1.09 | 93s |
| sonnet/high | 3 | 10,10,10 | 10.00 | 3372 | 4557 | 0.0506 | 0.57 | 55s |
| sonnet/medium | 6 | 8,9,9,10,10,10 | 9.33 | 2009 | 3091 | 0.0359 | 0.40 | 38s |
| sonnet/low | 4 | 5,5,6,6 | **5.50** | **0** | 634 | 0.0113 | 0.13 | 14s |

Exact two-sided permutation tests: `count(|d_perm| >= |d_obs|) / C(N, n)`, enumerated over every way
of choosing which n of the pooled N scores form the first group. **The floor that test can return
turns on whether the two groups are the SAME SIZE, and an earlier draft of this paragraph applied
the equal-size formula `2/C` to all three shapes it quoted — right for 4v4, and double the truth for
the other two.** At equal n the complement of every split is itself a split of the same size with d
exactly negated, so the extreme is always hit twice and the floor is `2/C`: 0.100 at 3v3, 0.029 at
4v4, 0.002 at 6v6. At UNEQUAL n the complement has the wrong size and is never enumerated at all, so
the floor is `1/C`: 0.029 at 3v4, 0.005 at 6v4 — and this data ATTAINS that floor four times, all
four against `sonnet/low`. (3v6 is 0.012, computed from a constructed maximally separated pair: no
pair of that shape reaches its floor in this data, and nor does any 3v3 or 6v6 pair.) Note the
direction of the old error: doubling a floor makes the instrument look LESS able to reject, not
more, so what it flattered was not the design but the excuse the earlier draft drew from it.

Two things follow. **Every significant result below sits AT its floor**, none clear of one — so
they are not ranked by robustness, they are each the single most extreme assignment their shape
admits. And **the only pair SHAPE in this design that cannot reject at 0.05 whatever it scores is
3-against-3**, of which there is exactly one pair: `opus/high` against `sonnet/high`. A three-run
cell is NOT blind here, and the table refutes the claim that it is: `opus/high` [10,10,10] against
`sonnet/low` [5,5,6,6] returns 1/35 = 0.029, and `sonnet/high` against `sonnet/low` returns the
same.

* effort rung on opus, xhigh->low ....... d = −0.25, **p = 1.000** — no separation
* the MODEL rung at xhigh, opus->sonnet . d = ±0.00, **p = 1.000** — identical multisets
* effort rung on sonnet, xhigh->low ..... d = +4.25, **p = 0.029** — 2/70, that pair's floor
* effort rung on sonnet, medium->low .... d = +3.83, **p = 0.005** — 1/210, that pair's floor
* the model rung at low, opus->sonnet ... d = +4.50, **p = 0.029** — 2/70, that pair's floor

Among the seven cells that are not `sonnet/low` there are twenty-one pairs. **Thirteen return
p = 1.000 and none of the other eight rejects**; those eight run 0.182 to 0.690. Six of the eight
involve `sonnet/medium` — the smallest is `sonnet/medium` against `opus/medium`, d = −0.67,
p = 0.182 — so it holds the only near-signal among the seven, and after doubling it to n=6 it is
still not one. The other two are `opus/medium` against each `xhigh` cell at p = 0.400, on d = −0.25;
note that the SAME d = −0.25 gives p = 1.000 for `opus/xhigh` against `opus/low`, so these p-values
track cell size as much as effect, which is one more reason to read the whole block as unresolved
rather than as a ranking.

Five things follow, and only these five.

1. **`low` is not a position on a dial, it is thinking OFF.** All 8 runs at `low` reported
   `thinking_tokens: 0`; all 26 at every other level reported a non-zero count. Both directions, no
   exceptions, across both models. That count is also the within-run control that the lever fired.
   **NARROWED BY VMCP-319 (1468) — the COUNT stands, the generalisation above it does not.** On a
   harder prompt, `--effort low` produced 789-2 773 thinking tokens over SIXTEEN runs on the same
   box and build, while a one-word prompt at that level produced 0 in five runs out of five. So
   the level sets a
   small BUDGET the model spends when the work calls for it, and the zeros in this row are a fact
   about THIS card's task rather than about `low`. The section below carries the runs.
2. **Only ONE cell separated from anything, and it is the one a menu would write first.** Seven
   cells score 9.33-10.00 of 10 — four of them a clean sweep; `sonnet/low` scores 5.50. Pairing the
   cheaper MODEL with the shallower EFFORT — the obvious "cheap role" menu entry — is the single
   measured configuration that fails, and **neither of its halves fails alone**: `opus/low` and
   `sonnet/high` both swept. `opus/low` is also both CHEAPER and higher-scoring than
   `sonnet/medium` (0.35x against 0.40x, 10.00 against 9.33), so the cheap corner of this grid is
   not where the model ladder points.
3. **WHICH items the failing cell loses is the finding, and the aggregate hides it.** Across the 30
   runs outside that cell, every one of the ten items was found 28-30 times. `sonnet/low` found K5,
   K7, K8 and K10 4/4 — the items a single comparison settles: a key absent from a JSON blob, 60
   against a quoted 90, a bare universal quantifier, an outlier under an "every time". It found K1,
   K2 and K4 **0/4** — subtract the control round before calling a kill count, notice that a ZERO
   delta means the pin is BLIND, sum one field across two responses — and K6 1/4, K9 2/4, which are
   attribution rather than arithmetic (a cause invented; "read a constant" against "measured
   live"). So the honest form is **it keeps what one comparison settles and loses what needs a
   second step — completely on the three arithmetic items, most of the time on the two attributional
   ones.** Not "it keeps the scepticism": it loses that too, just less reliably. K3 is the
   counterexample that fixes the shape — 3/4 in this cell despite needing the same control round,
   because the report's own arithmetic there is checkable without it. K1 and K2 are precisely this
   repo's mutation-sweep discipline, which is what makes this cell's failure mode the expensive one
   here.
4. **The rung this rule already permits — Opus to Sonnet class — separated NOTHING at full depth, in
   either direction.** The two cells returned identical recall multisets (`9,10,10,10` each,
   p = 1.000) and the cheaper model cost 1.09x the baseline rather than less, because it spent 8179
   thinking tokens against 2016 to reach the same place. Do NOT read that as "Sonnet costs more": a
   1.09x ratio on n=4 is not distinguishable from 1.0 here. The measured claim is smaller and still
   worth having — **on this task the permitted rung produced no saving anyone can demonstrate.** And
   it is NOT an instance of the caution one section above about a cheaper agent taking MORE TURNS:
   every one of these 34 runs is `num_turns: 1`. The mechanism observed is a different one — more
   thinking tokens inside a single turn.
5. **The two models have different SHAPES on the effort axis, and only one of them has a knee.**
   Opus reads 10.00 / 10.00 / 10.00 / 9.75 across low/medium/high/xhigh — flat within noise all the
   way down, at 0.35x the baseline cost at the bottom; five of its six internal pairs sit at
   p = 1.000 and the sixth, `xhigh` against `medium`, at p = 0.400.
   Sonnet reads 5.50 / 9.33 / 10.00 / 9.75 across the same four: the step from `low` to `medium`
   carries +3.83 at p = 0.005, and nothing above `medium` separates. **A rung is therefore not one
   quantity — the same nominal step costs Sonnet most of the task and costs Opus nothing
   measurable.** The `opus/high` cell was run specifically to test whether Opus had a knife-edge
   hiding between the levels either side of it; it does not. Set against the harness's own effort
   cost index quoted above (sonnet low 0.47 / medium 0.74 / xhigh 2.41, high = 1), the measured
   output-token ratios are 0.14 / 0.68 / 2.03 — close at the top, over-predicting `low` by about
   3.4x, because at `low` thinking collapses to zero.

### What this does NOT settle, which is most of it

* **The instrument SATURATES, and that is the largest boundary.** FOUR of the eight cells swept
  10/10 on the grader's scores — `opus/low`, `opus/medium`, `opus/high`, `sonnet/high` — and the
  only pair the design resolves is the one involving `sonnet/low`. So "`opus/low` matches the
  baseline" must not be restated as "`low` costs Opus nothing" — it is a statement about a task
  neither cell struggles with. Everything in the 0.35x-1.09x cost band is measured as
  INDISTINGUISHABLE, which is not EQUAL, and a failure to reject at n=3-6 is weak evidence of
  sameness.
* **PRECISION was never measured, only recall.** No run was seen to mis-flag a sound sentence — but
  note how far that is checked, and what the record even is. It is a re-score built to measure
  RECALL, whose off-key list is a by-product; it nowhere claims to be exhaustive. The RECORDED
  transcript reading covers 16 runs, the same 16 as the hand re-score above. For the other 18 the
  evidence is the grader's `extra` counter, and that counter demonstrably UNDERCOUNTS: it logged 11
  off-key findings across 9 runs, 5 of those 9 outside the 16, while the hand artifact credits six
  FURTHER runs with off-key findings the grader scored `extra: 0`. The off-key findings that were
  actually reviewed — the classes that artifact names, on runs inside the 16 — were defects the key
  had MISSED rather than false positives; the rest were never reviewed one by one. So the claim is a
  16-run reading plus an undercounting counter over the rest, not a checked universal over 34. The
  report under test carries its defects in roughly 15 assertive sentences, which inverts the real
  base rate — a real second pass hunts one or two errors among a hundred sound ones, and there,
  flagging everything quantified is a failure mode this instrument cannot even express. **A cheap
  cell's fitness for the real role is therefore not carried by this data at all.** VMCP-319 (1468)
  built the instrument that CAN express it — twelve sound sentences engineered to be flagged, six of
  them lexical twins of its defects — and measured zero false alarms in nineteen runs across the
  same model and effort corners. So the failure mode is now measured rather than merely unexpressed;
  the sentence above stands for THIS card's data.
* **The live range is smaller than the eleven items planted.** K5, K8 and K10 were found by 34 of
  34 runs and discriminate nothing; K1 fuses two independent mistakes into one item. An eleventh
  item was DROPPED whole after review, and the reason is the same defect class the instrument
  tests for: it asked a run to notice that "three of the last five landings were made by agents"
  is two, not three — but the supplied `git log --oneline` carries no AUTHORS at all, so a run
  answering "no authorship is shown" was right for a BETTER reason than the key's, and the key was
  itself a claim wider than its evidence. The automated grader then scored that same answer 1 on
  one transcript and 0 on another, twice in one cell, manufacturing the only model-ladder gap the
  first reading contained. **Grading noise was the size of the effect being claimed**, which is why
  the item is gone rather than patched.
* **The grader is the primary score, and the hand re-score that checked it covered 16 of the 34
  runs.** An independent pass hand-scored those 176 marks, over four cells only — `opus/xhigh`,
  `opus/low`, `sonnet/xhigh`, `sonnet/low`. **18 runs were never hand-checked at all**: `opus/high`,
  `sonnet/high`, and both `medium` cells. Read under the artifact's own stated convention — its
  partial marks `p` and `w` BOTH count as FOUND, which is the only one of the four readings of those
  two marks that reproduces the per-key totals it prints (require a bare `1` and K3, K9, K11 come
  out 12, 13, 15 against a printed 15, 14, 16) — it disagrees with the grader THREE times, all three
  on K11, and NOT ONCE inside K1-K10. Four marks inside K1-K10 are partial rather than plain, all
  four in `sonnet/xhigh`: three `p` on K3, and on `b00a0c4302` one `w` at K9 that the legend never
  defines. Read that single undefined mark as a miss and `sonnet/xhigh` goes 9.75 -> 9.50; nothing
  else in the table moves, the four sweeps stay four, and finding 5 survives either way. So does
  finding 4's VERDICT — both readings give p = 1.000 — but not the REASON it states: the two `xhigh`
  multisets are identical on the grader's scores and differ by one mark under the hand reading. The
  21-pair block below moves under it as well — eleven at p = 1.000 rather than thirteen, ten others
  running 0.133 to 0.690 — but still none of the 21 rejects, which is all that paragraph concludes.
  An earlier draft of this bullet put that disagreement on `740d13c7f1` (`sonnet/high`) and claimed
  25 runs / 275 cells. That run is in no hand-score artifact and could not be: the hand-score file
  was written at 23:42:05 and the earliest record of the run is 23:44:01.
* **Closed-book measures half the role.** With no tools used, this scores judging the width of a
  claim against evidence PUT IN FRONT of the agent, never deciding what to go and look up — which
  is where a real second pass spends its tokens, and plausibly where a rung bites hardest.
* **The cells are 3, 4 or 6 runs, and every "n=4" is two batches of two.** Batch is unmodelled.
* **The runs were not environment-isolated.** Only `cwd` was scratch; `CLAUDE_CONFIG_DIR` was the
  real one, so every run loaded this box's settings, memory and skills. Fixed across arms, so not a
  bias between cells — but these are not clean-default runs, unlike VMCP-314's probe, and the cost
  ratios belong to a ~20k cached prompt with a 1-10k answer, not to a real per-task dispatch of
  hundreds of thousands of tokens. **The cost ratios do not transfer to a real dispatch.**
* **$2.4669 is the 34 SCORED runs and nothing else.** It excludes the 34 grading runs, whose cost
  the scorer never captured, the wire probes, and the discarded zsh round. The instrument's own
  cost is unmeasured.

### So: still no `.claude/agents/` set, and the reason has changed

That paragraph lists THREE objections: the `model:` column that does not compose; "No rung of either
ladder has been measured on this board"; and the mechanical pair, which is one bullet holding two
things — the `.gitignore` question, and a fixed menu being coarser than per-card judgement. **This
card answers the SECOND of the three, and only that one** — a rung has now been measured, so that
bullet no longer holds as written, though it is left standing above as the record of why the rule
was made rather than quietly edited out from under it. The first and third are untouched, and what
answers the second does not license a menu either. On one saturating closed-book task: the
axis that moved COST monotonically was effort (0.13x to 1.09x), while the model rung moved cost by
2.8x at `low` and by 0.91x at `xhigh` — inconsistent in direction; on QUALITY neither axis predicts
anything alone, and what failed was a PAIRING, which is finding 2. A menu would be picking among
seven cells this measurement could not tell apart, on an instrument that saturated for four of them
outright.
**Fact, not decision: the rule above is unchanged by this card, exactly as it was by VMCP-314.**
The follow-up the data asks for — an instrument that does not saturate and that scores precision as
well as recall — is filed as VMCP-319 (1468) rather than attempted here, because that is a second
measurement with its own corpus, its own key and its own audit, not an extension of this one.

## A second instrument — VMCP-319 (1468): matched pairs, and a NULL on precision

VMCP-315's own boundaries asked for exactly this, and named it as a second measurement rather
than an extension: an instrument that does not saturate and that scores PRECISION as well as
recall. This card built one and ran it. **The headline is a NULL on the axis it was built to
measure**, and a null there is worth more than the rung answer beside it. The apparatus is
committed under `docs/instruments/dispatch-depth/` — material, report, key, prompt and grader —
so the next card re-runs it instead of building a third one.

**Read what the previous instrument's limit actually was, because the obvious reading is wrong
and the card filing this one carried the wrong one.** It is not that VMCP-315 could not see: it
separated `sonnet/low` cleanly. It is that it has a CEILING — seven of its eight cells pile up at
9.33-10.00 and none of their twenty-one pairs rejects. So the job here was to raise the ceiling
until cells stuck at the top come apart, not to build something that can see at all. That
distinction decided the whole design: a first tier of three defects in a 45-sentence report was
PROBED before anything else was written, and five runs across three cells — opus/xhigh twice,
opus/low, sonnet/low twice — every one returned exactly those three and not one false alarm. It
had reproduced the ceiling, in the cheapest cell available, so a harder tier was built on top of
it.

### What is different from VMCP-315's instrument, and why each difference is there

**MATCHED PAIRS are how precision gets measured at a realistic base rate.** The report is 69
sentences; six are planted defects and twelve are LOOKALIKES — sound, and engineered to wear a
defect's shape. Six lookalikes are lexical TWINS of the six defects: an unhedged universal, a
statement about what the code does, an attribution to a named reviewer, a named statistic, a
true clause with a trailing consequence, a cross-artefact identification. A run that recognises
the SHAPE takes the defect and its twin alike and nets zero on the pair; a run that reads the
material takes the defect and leaves the twin. VMCP-315 could not express that failure mode at
all: its sound sentences were incidental, so "no false positives" there was consistent with
nothing ever having been tempting. **Say what the pairs then DID, because it is not what this
paragraph promises:** not one lookalike was flagged in 228 exposures, so no pair ever netted
anything and no pair-level analysis appears below. The mechanism was BUILT AND NOT ACTIVATED.
That is itself the result — nothing shape-matched — and it is why the precision half of this card
is a null rather than a table.

**The key is width-checked item by item, before the runs.** Every keyed item names the place in
the material that decides it AND the reading a run might defensibly give instead. That is
VMCP-315's third defect turned into a procedure, and it fired: an independent auditor recomputed
every sum, average, percentage, count and gap from the material before any scored run, confirmed
all eleven then-existing verdicts, and broke four things around them — one defect whose
"defensibly instead" cell said "none" and should not have, one filler sentence asserting the
provenance of the test trees that no artefact records, one asserting a definition of a column
the material did not give, and three carrying self-referential universals over the whole report.
All four were fixed before a scored run was made.

**Grading is set intersection over four fixed sets, so there is no grader JUDGEMENT and therefore
no grader noise.** There is still a grader — `score.py` — and it has two tie-breaks the sentence
above would deny it: the last flag block wins, and an identifier outside S01-S69 is dropped. All
26 runs emitted exactly one block and no out-of-range identifier, so neither tie-break ever fired.
VMCP-315's grader, by contrast, scored one and the same answer 1 on one transcript and 0 on
another, twice in one cell, so its grading noise was the size of the effect it was claiming. The
price here is that the run is told the report is numbered, which cues the sentence as the unit of
a finding. That price is paid equally by every cell, which makes it unlikely to move a comparison
between them — but a constant is not thereby non-interacting, and nothing here tests that.

**Defect density is 6 in 69, against ten defects in roughly fifteen assertive sentences.** Still
not the one-or-two-in-a-hundred of a real second pass, and that remaining gap is deliberate: at
one or two in a hundred the same six defects need a report of three hundred to six hundred
sentences — four to nine times this one — for no gain in resolution, and the base-rate realism it
would buy is not what the precision measurement rests on. The lookalikes are.

### The numbers

Nineteen scored runs, four cells at n=4 plus a three-run prompt variant, `claude -p --model M
--effort E --output-format json --allowed-tools ""` from a scratch cwd, Claude Code 2.1.252.
Baseline is **opus/xhigh** for VMCP-315's reason: subagents here carry no `.claude/agents`
definition, so they inherit the session effort, and this box's `settings.json` sets
`effortLevel: "xhigh"`. Recall is out of SEVEN — the six planted defects plus S64, which was
planted as filler and is a real defect the RUNS found (see below); every figure counting it is
post-hoc and is marked — **including in the false-alarm column, which is where it bites hardest**.
Under the key AS PRE-REGISTERED, S64 was filler and a flag on it was a false alarm, so that column
reads 3, 1, 4, 0, 0 rather than five zeros. Every one of those eight flags is on S64 and S64 is a
real defect, which is why the corrected column is the one to act on and the pre-registered one is
printed beside it rather than replaced.

Two cost columns, because one of them is nearly a fiction. `$/run` is the cell mean over all its
runs; 6 of the 19 paid `cache_creation`, unevenly — one such run inflates the opus/low mean by
54% on its own — so `x warm` is the ratio over the 13 runs that paid none, and it is the honest
one.

| cell | n | recall /7 | FA corrected | FA as pre-reg | think | out tok | $/run | x warm |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| opus/xhigh (baseline) | 4 | 7, 7, 7, 6 | 0 | 3 | 5 448 | 5 905 | 0.2069 | 1.00 |
| opus/low | 4 | 7, 6, 6, 6 | 0 | 1 | 1 018 | 1 356 | 0.0687 | **0.26** |
| sonnet/xhigh | 4 | 7, 7, 7, 7 | 0 | 4 | 19 520 | 20 036 | 0.2219 | **1.20** |
| sonnet/low | 4 | 4, 5, 5, 5 | 0 | 0 | 2 063 | 2 398 | 0.0406 | **0.17** |
| sonnet/low, warning removed | 3 | 3, 5, 4 | 0 | 0 | 1 630 | 1 931 | 0.0385 | 0.18 |

**On the SIX pre-registered defects, the top three cells all sweep 6/6 in all twelve runs.**
Only the post-hoc seventh item tells them apart at all, and it does not tell them apart
significantly. So the ceiling was raised — `sonnet/low` now sits at 4-5 rather than at the top —
and it was not raised far enough to separate opus/xhigh, opus/low and sonnet/xhigh from each
other. That is this card's honest boundary and it is the same one VMCP-315 hit, one rung higher up.

### The finding: WHERE an item's refutation lives may decide whether it measures anything

The per-item table is where the information is, and it splits the seven items along an axis this
card did not design and did not expect.

* **INTERNAL** — refutable from the report's own other sentences. S27 (S22 and S26 say in the
  report's own voice that no deferral was recorded), S51 (S44 gives the middle of the twelve as
  421 seven sentences earlier), S58 (S53 says nothing in the log records an integration round),
  S65 (S38 says the tree in the quoted comment was REMOVED, which is what kills it, with S39
  supplying the mechanism).
* **CONSULTATION** — refutable only by reading a particular artefact. S14 (two rows of the table),
  S41 (the last sentence of the quoted comment), S64 (the table's own caption).

Every run is `num_turns: 1` with no tool use and the whole material inline, so "consultation"
never means fetching anything. It means holding two artefacts against each other rather than two
sentences.

| cell | INTERNAL items | CONSULTATION items |
| --- | --- | --- |
| opus/xhigh | 16/16 | 11/12 |
| opus/low | 16/16 | 9/12 |
| sonnet/xhigh | 16/16 | 12/12 |
| sonnet/low | 16/16 | **3/12** |
| sonnet/low, warning removed | 11/12 | **1/9** |

**Every cell but one takes every internal item, and ALL the variance is on the other three.**
Seventy-five of the seventy-six internal exposures were taken — the single miss is one run of the
variant arm, which is why that row reads 11/12 — against thirty-six of fifty-seven consultation
exposures.

**The split survives dropping the post-hoc item, and gets cleaner.** On S14 and S41 alone — both
width-checked before any run — the three upper cells are IDENTICAL at 2/2 in every run
(8/8 each), `sonnet/low` is [0,1,1,1] and the variant arm [0,1,0]: twenty-eight of thirty-eight.
Exact two-sided permutation tests on that S64-free subscore: every upper cell against `sonnet/low`
is d = +1.25 at **p = 0.029**, the shape's floor; every pair among the three upper cells is
d = 0.00 at p = 1.000. So the finding does not rest on the item the runs handed the key.

**Read the two cheap arms separately, because they are not the same manipulation.** `sonnet/low`
differs from the baseline in model and effort. The variant arm differs in model, effort AND
prompt, so its three comparisons carry a confound the other three do not; it is reported here as
a prompt variant that happens also to be cheap, not as a second depth cell.

Four consequences, and the first is the one worth carrying off this board.

1. **An item whose refutation sits elsewhere in the text under test may measure nothing about
   depth.** Every cell but one took every such item here. That is a CANDIDATE explanation for
   saturation in instruments of this class and not a demonstrated one: the split is post-hoc over
   seven items, nothing was manipulated, and the internal/consultation distinction is confounded
   with everything else that separates those two groups. What it does license is a cheap pre-run
   CHECK, and that is the part worth carrying off this board: for each keyed item, ask whether the
   report's own other sentences already contradict it. Four of this card's seven items do.
2. **This card's own EASY/HARD tiers did not predict difficulty, and were wrong in the direction
   that matters.** `sonnet/low` scored 12/12 on the "hard" tier and 7/12 on the "easy" one. The
   labels were the author's judgement of how intricate an item looked; what actually decided it
   was whether the answer was already in the report. Recorded rather than quietly relabelled.
3. **An item's difficulty is not a property of the item.** The three easy-tier defects are the
   SAME three sentences the probe used, and in the 45-sentence probe report `sonnet/low` took all
   three in both runs, while in the 69-sentence report it took S14 once in four and S41 twice in
   four. Nothing about those sentences changed; what changed is how much else was on the page.
   Two runs against four is a thin comparison and the direction is the whole claim — but it means
   an item calibrated on a short corpus does not keep its difficulty when the corpus grows, and
   the ceiling of an instrument is therefore not fixed by its items alone.
4. **The cheap corner's misses are concentrated, and it is NOT that it failed to look.**
   `sonnet/low` flagged 4.8 sentences per run against the baseline's 6.8, and every one of the 4.8
   was correct. An earlier draft of this bullet said it "fails by not looking"; that is refuted by
   its own transcripts, which cite the material four to seven times per run, as often as the
   baseline's do — and it is impossible by construction anyway, since the material is inline and
   there is nothing to open. What is measured is narrower: its misses sit on the items needing two
   ARTEFACTS held against each other rather than two sentences. Whether that is a shorter search, a
   weaker cross-reference or something else, this instrument does not say. It is consistent with
   VMCP-315's "keeps what one comparison settles" without being evidence for that card's
   mechanism.

### The precision axis: a NULL, and the confound was measured rather than argued about

**Not one false alarm in nineteen runs, ONCE THE KEY WAS CORRECTED.** Zero flags on the twelve
engineered lookalikes across 228 exposures, and zero on the fifty remaining filler sentences
across 950. The only off-key flags any run made — eight of them — were all on S64, which turned
out to be a real defect; under the key as pre-registered those eight scored as false alarms and
the headline would read 8 in 19. Both readings are printed in the table above, and the corrected
one is the one that means anything, because a flag on a true defect is not a false alarm whatever
the key said first. Six of the twelve lookalikes are lexical twins of the six defects, so the
failure mode this instrument was built to catch — a run that recognises the SHAPE of a defect and
flags the sound twin with it — did not occur once, in any cell, including the cheapest.

**And the obvious objection to that null was measured rather than argued about.** The prompt
tells the run that a wrong flag costs the author a rework round, which is the operational truth
and is also a perfectly good explanation of a zero all by itself. So a three-run arm removed that
block and re-ran the cheapest cell. **Be exact about the size of that intervention**: it deletes
three sentences, not one clause — the cost warning AND the flagging threshold itself, the
instruction to flag only what you would send the card back over. Removing the threshold is the
bigger half and the half likelier to raise flag counts. It produced **zero false alarms as well**,
and its recall went DOWN rather than up (1/9 on the consultation items against 3/12, d = +0.42,
p = 0.486 — not a separation).

**That BOUNDS the instruction's effect; it does not exclude it, and the difference is the whole
honesty of the paragraph.** Both arms are zero, so there is no variance in either. Three runs
across 62 non-defect sentences is 186 exposures, which by the rule of three puts a 95% upper
bound near 1.6% per sentence — about one false alarm per run still not ruled out. What the arm
says is that the warning is not doing all the work, not that it is doing none.

What the null licenses is narrow and worth stating exactly: **on this task, at this base rate, a
cheap cell's failure mode is missing defects and not inventing them.** The worry that a shallow
reviewer bounces cards on non-defects, which is what makes precision the sharper axis on this
board, was not exhibited by any cell tested.

### The precision channel fired ONCE, and what it caught was the KEY

Every off-key flag any run made, in every cell, was the same sentence: **S64**, planted as
filler, which says one artefact is the only one describing the porcelain status while another
artefact's own caption describes it too. The sentence is false and the runs were right. It was
promoted to a seventh defect in the key AFTER the runs and is labelled post-hoc there; the
report file was deliberately NOT fixed, so the committed corpus stays the one these numbers were
taken on.

Three things follow and they are worth more than the promotion.

* **A false-alarm channel that only ever fires on the key is not a false-alarm channel at all.**
  Zero flags landed on the twelve engineered lookalikes, and zero on the other fifty filler
  sentences. That is the null, and it is a stronger null than VMCP-315's because these traps were
  built to be taken.
* **The pre-run audit's gap is visible in exactly where S64 sits.** The auditor read the easy
  tier's thirty-four filler sentences; the hard tier was written afterwards and never got a pass
  of its own. The procedure is only as wide as the last time it was run.
* **This is the same phenomenon VMCP-315 recorded and could not act on.** Its off-key findings —
  eleven by the count of a counter its own text says undercounts them — were, on review, defects
  its key had MISSED rather than false positives. Two
  instruments, two corpora, one result: what looks like a model's false positive on this task is
  overwhelmingly the key being wrong.

### VMCP-315's finding 1 is NARROWER than it was written — said loudly, because it is a rule-shaped sentence

That card's finding 1 reads that `low` is not a position on a dial but thinking OFF, on 8 of 8
runs at `low` reporting zero thinking tokens against 26 of 26 non-zero elsewhere. **The COUNT is
not in dispute and is not being corrected — it is a true report of those 34 runs.** What does not
survive is the generalisation above it, and the discriminator is a within-level pair on this
card's rig, at `--effort low` throughout:

* the audit prompt, sonnet at `low`, ELEVEN runs: 789, 867, 1 423, 1 571, 1 747, 1 843, 2 005,
  2 228, 2 451, 2 596, 2 773 thinking tokens. Not one zero.
* the audit prompt, opus at `low`, FIVE runs: 792, 948, 948, 1 061, 1 113. Not one zero.
* a ONE-WORD prompt ("Reply with exactly the word OK and nothing else."), same flag, FIVE runs
  across BOTH models — three sonnet, two opus: **0, 0, 0, 0, 0**. Not one non-zero.

Same flag, same box, same build — **sixteen runs at `low` that think and five that do not**, and
what separates the two groups is the TASK and nothing else. The five zeros were run deliberately
as this contrast's control rather than found lying about, except one, which was a smoke test made
before the instrument existed and is counted here because excluding it would improve the picture.

So `low` sets a small BUDGET the model spends when the work calls for
it, and zero thinking tokens at `low` is a fact about a prompt, not about the level. Three
environment variables this session carries that could each have been the cause were controlled
rather than argued about — `_CLAUDE_CODE_EFFORT_LEVEL`, `CLAUDE_EFFORT` and
`CLAUDE_CODE_DISABLE_ADAPTIVE_THINKING` — by unsetting all three for a repeat run and then
unsetting only the last: 789 and 1 843 thinking tokens, so none of the three is what enables it.
One run each, and the response records no environment at all, so that pair is a weak control that
rules the three variables out as a SUFFICIENT cause and nothing more.

Why this matters beyond bookkeeping: "thinking OFF" invites a reader to treat `low` as a
different KIND of thing from the other levels and to reason about a menu from that. It is the
same level as the others with less budget, and the lever's effect is still large and visible —
opus ran 4 029-6 859 thinking tokens at `xhigh` against 948-1 113 at `low` on this same prompt.

### What it cost, stated the way the card asked

**$3.2158 for 31 runs**, of which **$2.2678** is the 19 SCORED runs and $0.9480 is everything
around them: the pilot, the probes that showed the easy tier could not stand alone, the three
environment controls and the five one-word thinking controls. VMCP-315's
comparable figure is $2.4669 for its 34 scored runs — so on scored runs this card came in under
its neighbour and all-in it came in $0.75 over. Neither number covers the instrument's own
construction, the two audit subagents, or this prose; as VMCP-315 already said of itself, the
instrument's own cost is unmeasured, and saying it again is cheaper than pretending otherwise.

The budget declared before the runs was "about $2.2" for the scored set. That one landed
($2.2678). The declared TOTAL of "near $2.9" did not, by $0.32, and every dollar of the overrun
is controls that were not planned because the things they close were not known when the budget
was written — an environment confound found mid-run, and a one-word contrast the independent
second pass asked for. Hard stop was $5 and was not approached.

### What this does NOT settle

* **The precision result is a NULL, and a null at this n is not "these cells never false-flag".**
  It says that on this task, at this base rate, with twelve traps built to be taken, none was
  taken by any cell. It does not say the traps are as attractive as a real report's worst
  sentences, and it cannot: how attractive they are is exactly what nobody has a scale for.
* **The instrument was still built by one author.** The lookalikes are sound because a second
  reader recomputed them, not because a procedure guarantees it — and S64 is the standing proof
  that the procedure has a seam, at the boundary of whatever the auditor was last shown.
* **Closed-book, again.** VMCP-315 named an open-book arm as the thing most worth adding and this
  card did not add one either; the budget went on the precision axis instead. So both
  measurements on this board now bound their claims to the judging half of the second-pass role
  and say nothing about the deciding-what-to-look-up half. That is now the oldest open item here.
* **The seventh item is post-hoc and every figure counting it says so.** The precision claim is
  NOT post-hoc — it rests on the twelve lookalikes, which were width-checked before a single
  scored run and did not move. Read the two differently.
* **The grader's composite weights one false alarm equal to one miss**, which is defensible on
  this board because a wrong bounce costs a rework round, and is still a weight somebody picked.
  Nothing above rests on it: the tables report recall and false alarms separately so a reader can
  reweigh without re-running anything.
* **The cells are four runs each, three in the variant arm.** Complete separation reaches
  p = 0.029 at both shapes and nothing below it, so a cell that fails to reject has not been
  shown equal to anything — measured by running the test on a maximally separated pair rather
  than derived from a formula, which is where the neighbouring card's floors went wrong.
* **Ten pairwise tests are reported and NONE is corrected for multiplicity.** At a floor of
  0.029, no comparison here survives any correction over ten tests. Read each p as a description
  of its own pair and not as a family-wise claim.
* **The run transcripts are not committed.** The corpus, key, prompt and grader are; the 27 JSON
  responses live only in this card's scratchpad, so every figure above is re-derivable only by
  re-running the instrument, not by re-reading the same bytes. And the JSON records neither the
  harness version nor the flags, so `2.1.252`, `--effort`, `--allowed-tools ""` and the scratch
  cwd are asserted here rather than shown; what the JSON does carry, and what evidences the
  closed-book condition, is `num_turns: 1` with zero `server_tool_use` on all 26 runs.
* **These are `claude -p` runs from a scratch cwd with this box's real config dir**, on a ~2 000
  word prompt with a short answer. VMCP-315's warning transfers unchanged: the cost ratios belong
  to that shape and not to a real per-task dispatch of hundreds of thousands of tokens.
* **The rule is UNCHANGED by this card**, deliberately and for the third card running. What is
  added is a fact — the false-positive failure mode this rulebook worries about did not appear on
  a task built to elicit it — and one narrowing of a neighbouring card's wording. Neither
  licenses a menu, and neither was allowed to move the ladder.

## What the brief does that the model cannot

Three things, all free, all per-dispatch. NONE of the three is measured: only the first is even
traceable to the incident above, and the order is this card's judgement of what would have bitten
hardest there, not a ranking anyone ran:

1. **Say whether this dispatch may raise sub-agents of its own.** The incident's 337k was TWO
   ROUNDS of review, and the report does NOT say what raised the second — SKILL.md's own
   re-offer mechanism is a competing and entirely routine cause, since a card without a verdict
   is offered again. Nesting is a multiplier of the same shape rather than the one measured:
   a reviewer that raises its own second pass pays for that review more than once. SKILL.md's
   second-pass rule already carries a threshold for it, and it is a THREE-case list, not two
   ("On a one-line edit, on pure code with no new claims, and where the text merely accompanies
   the work — do not raise one, it is wasted spend"); a brief that is SILENT leaves the agent to
   apply it alone.
2. **Name the diff.** A reviewer told to "review card N" discovers the scope by reading; a
   reviewer given the sha, the files and the ranges reads those. The orchestrator already holds
   the sha — it verified it before dispatching.
3. **Say what is at stake, in one clause.** It is what lets the agent size its own verification,
   and it is the same clause the rule requires be stated anyway.

**What a brief may NEVER do is waive verification.** Narrowing the SCOPE of what is checked is
the lever; "take the report's word for it" is not, and would defeat every gate in this rulebook.
Verification by RUNNING is not the expensive part of a review — re-deriving the whole card is.

## What is not known here

* Whether an effort level changes what a model actually DOES. The key reaches the request — that
  is measured above — and the MECHANISM stops being observable there. What VMCP-315 added is not
  the mechanism but an OUTCOME: on one closed-book auditing task the level moved the token spend on
  every cell and the recall on one, so the field is not inert. What the wire cannot see, this file
  still does not claim.
* Whether a downgrade costs verdict quality on this board, and by how much — still open on almost
  all of both ladders. VMCP-315 measured Opus-class against Sonnet-class over four effort levels
  each, on ONE role and ONE synthetic closed-book task, and seven of its eight cells sat at or
  beside that task's ceiling with no comparison among the twenty-one pairs rejecting, so it
  separated a single cell and nothing else. Haiku-class and Fable-class have still never been
  run against a role here, and neither has any rung against code review, implementation, or the
  per-task agent.
* How much of a REAL second pass that closed-book task stands for. Its runs used no tools, so it
  scored judging the width of a claim against evidence put in front of the agent, and not deciding
  what to go and look up — which is where a real second pass spends its tokens, and plausibly where
  a rung bites hardest. **BOTH measurements on this board are closed-book**: VMCP-319 named an
  open-book arm as the thing most worth adding and spent its budget on precision instead, so this
  is now the oldest open item here rather than a fresh one.
* Whether a cheap cell ever produces a FALSE positive on this role. VMCP-319 measured zero across
  nineteen runs against traps built to be taken, which is a null and not a proof of absence — what
  nobody has a scale for is how attractive those traps are next to a real report's worst sentences.
* What this repo actually pays per token. The prices above are list API rates for the ratio only.
* Whether 643k/337k is typical. It is ONE card, reported once, and it is the reason for the rule
  rather than a distribution. A second such accounting would be worth more than any wording here.
