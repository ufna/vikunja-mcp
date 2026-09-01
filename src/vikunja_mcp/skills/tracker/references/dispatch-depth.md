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

**None of it.** There is no measurement here of Sonnet-class or Haiku-class output on any role in
this pipeline, and none of Fable-class either. What exists is the token accounting above and the
list prices; what does not exist is a single A/B of verdict quality by model on this board.
Checked rather than assumed, and the check took two attempts. `git grep -i -l` for each of the
four names over the tree at the commit this landed on returns: `Sonnet` and `Haiku` only in
SKILL.md — the policy sentence this rule replaces; `Opus` there plus a plan document and a
`settings.json` FIXTURE in `tests/unit/test_install_skill.py` that has nothing to do with
dispatch; `Fable` only in a plan document, inside quoted `Co-Authored-By` trailer strings. So a
reader who greps and finds a model name has found a POLICY, an AUTHOR or a fixture — never a
measurement. The two attempts are the point: the first used `git grep -E "\b(sonnet|...)\b"`
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
  is measured above — and the levels stop being observable there. What the wire cannot see, this
  file does not claim.
* Whether a downgrade costs verdict quality on this board, and by how much. Unmeasured, on BOTH
  ladders now: no rung of the model ladder and no effort level has ever been run against a role
  here.
* What this repo actually pays per token. The prices above are list API rates for the ratio only.
* Whether 643k/337k is typical. It is ONE card, reported once, and it is the reason for the rule
  rather than a distribution. A second such accounting would be worth more than any wording here.
