# How deep to dispatch: the model lever, and the effort lever that is not there

Evidence for the bullet **"The model is a per-dispatch DECISION, and it is the ONLY depth lever
the call site has"** in SKILL.md's "Who does the work: the orchestrator-pump and the per-task
agents". Open this before widening that rule, and ALWAYS before writing a sentence about
"effort": on the card that created the rule the shape of that lever was got wrong at least
once, from a reading of documentation, and the wrong version was operationally load-bearing
while it stood. A SECOND overstatement about it was caught inside this very file by its own
second pass — see the end of the next section.

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

## The `effort` key: a document and a measurement that disagree, and a rule that survives either

Both of these were produced during one tick of the card that wrote this file, and they do not
agree. Neither is re-derived here; each is recorded with the provenance it has.

* **The documentation says the key exists.** A subagent-definition file
  (`.claude/agents/*.md`) is documented as accepting `effort`, valued `low` / `medium` / `high`
  / `xhigh` / `max` or a number, alongside `model`, `tools`, `permissionMode` and the rest. This
  came back from an agent that fetched `code.claude.com/docs/en/sub-agents.md` and
  `.../model-config.md` — a DOC READING, not a run.
* **The human measured that setting it does nothing.** In this session they built a definition
  carrying `model: haiku` plus an attempt to lower the effort, and reported that the frontmatter
  honours `model` while the effort key is IGNORED. Their upstream request for the missing lever
  is open: `https://github.com/anthropics/claude-code/issues/26102`.
* **The SHIPPED BINARY parses and validates it.** Found by this card's second pass and then
  re-checked by hand with `grep -a` over `~/.local/share/claude/versions/2.1.252`, the build
  `claude` resolves to: it carries the agent-file validation message ``Agent file ${e} has
  invalid effort '${me}'. Valid options: ${$h.join(", ")} or an integer``, the level list
  ``"low","medium","high","xhigh","max"``, and a spawn-override entry ``kind:"effort"``. So the
  key is READ, VALIDATED and CARRIED. That is a code reading, not a run — neither the auditor
  nor this card ran two agents differing only in that key and compared them, and PARSED is not
  the same claim as CHANGES BEHAVIOUR.

**What the rule can safely rest on is NARROWER than "there is no effort knob", and the first
draft of this file got that wrong.** It argued that the key is static per agent TYPE and
therefore blind to the card — which does not follow, because `subagent_type` is chosen PER CALL.
Under the documentation-plus-binary reading, a maintained SET of definitions differing only in
`effort`, selected per dispatch, IS a working lever. It is a COARSE one — a fixed menu rather
than a value fitted to the card — but it is not nothing, and calling it nothing was an
overstatement caught by this card's own second pass.

So the honest position is: **there is no effort knob at the CALL, and the definition route is
UNBUILT here** — this repo defines no agent types at all — **while one measurement says the key
would not fire anyway.** That is enough to carry the rule as written and not one word more.
Settling it is filed as VMCP-314 (1443); if the key is confirmed to work, the right answer is
probably a small set of definitions, and this section and the rule both change.

**One surface really does expose `effort`, and it is not this one.** The human reports an
`effort` parameter on `agent()` inside a **Workflow** script. That is a different surface from
the `Agent` tool, and no dispatch described in this rulebook goes through it — so it neither
rescues the orchestrator's per-card decision nor makes "there is no effort knob at the call
site" wrong. It is recorded because a reader who finds it will otherwise think this file missed
it.

The session-wide controls are real and are the wrong shape for the same reason: `effortLevel` and
`modelSettings` in `settings.json`, the `/effort` command, the `--effort` launch flag and
`MAX_THINKING_TOKENS` all move the whole SESSION — that is what their own documentation and the
binary's strings say; whether the setting propagates into every subagent was NOT confirmed
end to end here. As a per-dispatch lever they do not exist; as a blunt one they are worse than
nothing, because lowering the session floor lowers it for the cards that most need the depth. Do not
reach for them to implement a per-card rule.

**Recorded as a defect class, not as trivia.** The brief that launched this card asserted the
asymmetry "model is per-dispatch and dynamic, effort is per-agent-type and static" and sourced it
to the Agent tool's own documentation. It was retracted mid-tick by the human's measurement. That
is exactly the INHERITED class SKILL.md's "A second independent pass over YOUR OWN text" names —
a fact that arrived from a brief, carried no measurement of its own, and would have been written
down as one. The pass caught it here because a human ran the check, not because anyone reasoned
harder. An `effort:` key that a future reader finds in the docs is not evidence it is wired;
construct the check.

**Why this repo ships no `.claude/agents/` definitions — and why that is a DECISION, not a
finding.** A definition's `model` is beaten by the per-dispatch `model` the `Agent` tool already
takes, so a type buys nothing on that axis; the one thing it could add is exactly the `effort`
key, which is the disputed one. Against that: `.gitignore` here excludes `.claude/*` with a
single re-inclusion for `settings.json`, so a definition would be untracked local state or a new
exception; and a menu of types is a COARSER instrument than the per-card judgement this rule
asks for, so it would have to earn its place against the rule rather than beside it. That is a
judgement call on unsettled evidence, and it is the FIRST thing to revisit if VMCP-314 (1443)
confirms the key works.

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
already records — a search that answers "there is none" when it never looked.

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

* Whether the `effort` frontmatter key works. The doc and the measurement disagree, above; this
  file does not re-derive either, and the rule is built so it does not have to.
* Whether a downgrade costs verdict quality on this board, and by how much. Unmeasured.
* What this repo actually pays per token. The prices above are list API rates for the ratio only.
* Whether 643k/337k is typical. It is ONE card, reported once, and it is the reason for the rule
  rather than a distribution. A second such accounting would be worth more than any wording here.
