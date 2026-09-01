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
link from being a reading of a value that was there anyway. That closes VMCP-314's open item, which
said that `--effort` was the channel exercised and that the `settings.json` `effortLevel` channel
was not, **for the SESSION's own request only**; the SUBAGENT leg of the `settings` channel is still
unmeasured. **And none of this says anything about the 34 scored runs**: no wire was captured on a
run that reached the real service, because the stub replaces it. What evidences the lever on the
scored runs is `thinking_tokens` in their own responses, which is the EFFECT and not the lever.

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

Exact two-sided permutation tests. The p-floor is set by the cell sizes and is what decides whether
a cell can reach significance AT ALL: 0.029 at 4 against 4, 0.057 at 3 against 4, 0.010 at 6
against 4. So no three-run cell here can reject whatever it scores, and that is a property of the
design, not a result.

* effort rung on opus, xhigh->low ....... d = −0.25, **p = 1.000** — no separation
* the MODEL rung at xhigh, opus->sonnet . d = ±0.00, **p = 1.000** — identical multisets
* effort rung on sonnet, xhigh->low ..... d = +4.25, **p = 0.029** — at that pair's floor
* effort rung on sonnet, medium->low .... d = +3.83, **p = 0.005** — clear of its floor
* the model rung at low, opus->sonnet ... d = +4.50, **p = 0.029** — at that pair's floor

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
* **PRECISION was never measured, only recall.** Across 34 runs not one mis-flagged a sound
  sentence — established by reading the transcripts, not from the grader's `extra` counter, which
  logged 11 off-key findings and undercounts them. Those off-key findings were, on review, defects
  the key had MISSED rather than false positives. The report under test carries its defects in
  roughly 15 assertive sentences, which inverts the real base rate — a real second pass hunts one
  or two errors among a hundred sound ones, and there, flagging everything quantified is a failure
  mode this instrument cannot even express. **A cheap cell's fitness for the real role is therefore
  not carried by this data at all.**
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
* **The grader is the primary score, and the hand re-score that checked it covered 25 of the 34
  runs.** An independent pass hand-scored those 275 cells: five disagreements, four of them K11
  (now gone), and ONE inside K1-K10 — on `740d13c7f1` (sonnet/high) at K9, where a run named the
  right card as the measurer but never corrected the report's "#1102 measured that on a live tree".
  A reasonable reader can go either way on that one, unlike the K11 four. Under that hand reading
  `sonnet/high` is 9.67 rather than 10.00 and the sweeps number three rather than four; nothing
  else in the table moves, and finding 5 survives either way. **The nine runs added later were
  never hand-checked at all** — they are `opus/high` and the second half of both `medium` cells.
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

The three objections in the paragraph above — the `model:` column that does not compose, the
`.gitignore` question, and a fixed menu being coarser than per-card judgement — are untouched. What
this card removes is the second one, "no rung of either ladder has been measured", and what replaces
it does not license a menu either. On one saturating closed-book task: the axis that moved COST
monotonically was effort (0.13x to 1.09x), while the model rung moved cost by 2.8x at `low` and by
0.91x at `xhigh` — inconsistent in direction; on QUALITY neither axis predicts anything alone, and
what failed was a PAIRING, which is finding 2. A menu would be picking among seven cells this
measurement could not tell apart, on an instrument that saturated for four of them outright.
**Fact, not decision: the rule above is unchanged by this card, exactly as it was by VMCP-314.**
The follow-up the data asks for — an instrument that does not saturate and that scores precision as
well as recall — is filed as VMCP-319 (1468) rather than attempted here, because that is a second
measurement with its own corpus, its own key and its own audit, not an extension of this one.

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
  a rung bites hardest.
* What this repo actually pays per token. The prices above are list API rates for the ratio only.
* Whether 643k/337k is typical. It is ONE card, reported once, and it is the reason for the rule
  rather than a distribution. A second such accounting would be worth more than any wording here.
