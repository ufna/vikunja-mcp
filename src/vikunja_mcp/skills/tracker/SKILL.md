---
name: tracker
description: Use when working with team tasks in the Vikunja tracker via the "tracker" MCP tools (next_task/claim/advance/...) — queue discipline, stage gates, when to call_human vs return_task
---

<!-- MANAGED — this installed file is auto-synced from the vikunja-mcp package at MCP server
     start (exactly once per session — see "Which copy of these rules you are reading"); local
     edits will be overwritten. Turn the sync off: VIKUNJA_MCP_NO_SKILL_SYNC=1.
     Update by hand: `vikunja-mcp install-skill`. Edit the SOURCE in the repo, not this copy. -->

# Working with the tracker (Vikunja)

Pipeline: `Backlog → Queue → Design → Build → Review → [human] → Done`,
plus a separate `Your Call` column (YC in shorthand) and `Icebox` — the freezer, off to the
right of Done: very minor / legacy cards nobody is expected to pick up. `next_task` never
offers one while it sits there, so the column is the gate; the `icebox` LABEL is not a gate at
all — a card a human drags into Queue is offered like any other, carrying the label as an
instruction to do the MINIMUM that is correct. File a finding there yourself with
`file_task(icebox=True)` (see `references/decompose.md`). What no tool of yours does is take a
card OUT: `return_task`, `decompose` and `transfer_task` all refuse from Icebox, like they do
from Done. A frozen card stays readable and commentable, so a finding about one goes in a
comment on it — and if you think the freeze was wrong, say so in your report and leave it. Boards created before the freezer
existed simply have no such column, and everything else keeps working; only `icebox=True`
refuses there, naming `vikunja-mcp setup`. The hard rules are wired into the MCP
tools — they refuse if something is off. These rules are about HOW to work.

## Which copy of these rules you are reading

The text the skill serves is a SNAPSHOT. The installed copy (`~/.claude/skills/tracker/SKILL.md`)
is rewritten from the package by `sync_installed_artifacts`, and it does that EXACTLY AT MCP
SERVER START, and a session's server starts once. So inside a session this text DOES NOT MOVE —
even if the rules were changed, landed and rolled out during that same session. (The
SessionStart hook's standing context is frozen by the same snapshot: its prose lives in
`setup_cmd.render_hook_script` and is synced by the same call.)

- **Working in a checkout where this file sits as the SOURCE
  (`src/vikunja_mcp/skills/tracker/SKILL.md`, one `ls` settles it) — the authoritative copy is
  the one from YOUR worktree, not this one.** Read it as a file. That does not make two
  authorities: the snapshot is an input, the source is the source, and the rule switches on
  exactly where the source physically exists; consumers have no such path, and for them it is
  a no-op.
- **A task whose DELIVERABLE is an edit to this file cannot be verified by invoking the skill —
  neither by the implementer nor by the REVIEWER.** The skill returns the pre-session text, the
  edit will look unaccepted — and the conclusion "it did not take" is true by what is visible and
  false in fact. A reviewer is especially easy to trap here: the instruction is to verify BY
  RUNNING, and the only "run" a rules edit has is precisely the skill invocation, which returns
  the frozen snapshot. Verify with `grep`/`diff` against the file in your own worktree and write
  in the report (`[worklog]` for the implementer, `[review]` for the reviewer) what exactly you
  checked.
- **An edit reaches consumers by ROLLOUT, not by a write into somebody else's `~/.claude`.**
  Landed on the main branch → CI released a patch → the `stable` channel → it installs at the
  consumer's next server start. By hand — `vikunja-mcp install-skill`. Do not do that from a
  drain tick: it is a write into `~/.claude` for an effect a running session has nothing to
  confirm with anyway.

## Queue discipline

- **Do NOT TAKE more than `wip.limit` tasks yourself** (by default, when `wip_limit` is not set
  in the project config, that is THREE; `wip.limit: 1` is exactly "one task at a time"), but do
  not stop between them — see "Continuous operation (loop)". The wording is about YOUR claim,
  not about the number of active tasks on the board: `wip.limit` is a gate on `claim`, and
  `wip.active` legitimately runs higher than it when a card was put back into Build past the
  claim (see "The drain's width"). No free slots
  (`wip.free: 0`) — do not claim a new one until one of yours has moved to Review,
  Your Call, or been handed back via return_task. Exception: a background
  independent review (see "Independent review of changes") does NOT count as
  one of your active tasks — it runs in parallel, takes no slot, and the next task
  can still be taken. How many slots there actually are is told by `wip` in the `next_task`
  response, not by habit (see "The drain's width" and "Parallel drain"). Do not raise your own
  limit.
- Work on tasks always starts with `next_task`: it hands you your active task
  first (including one that came back after a human's answer), then the queue.
- **A resumable task (resume) — re-check first, work after.**
  Do not redo it blindly and do not blindly believe it is done: read the dossier
  (`get_task`), look at git log / the state of the code — the work may have been
  done in full or in part (for example, the task was migrated from an old
  board, or somebody ran it before you). If it is solved — verify the actual
  behaviour (run a check, do not only read the code) and move it to Review
  with honest evidence; if partially — record in a comment what is already there,
  and finish the rest. **And do not treat the working directory as a continuation of the
  previous turn:** what lies in it depends on WHY the task came back — a crashed agent
  gets the same tree with unfinished work in it, while a card returned from Review
  most often gets a FRESH tree, because its predecessor's work is already on the main branch
  (see "Two returns, two trees").
- **The Queue contract:** take a task if it is free OR assigned to you
  (the human addressed it to you specifically). Assigned to somebody else — do not touch it,
  that is work "for humans" or for another agent.
- **Whether to claim is decided by `stage`, not by `resume`.** `stage` is present in EVERY
  `next_task` response that has a `task`; the rule is ONE AND THE SAME in sequential and in
  parallel mode, and there is no other:
  - `stage: "Queue"` — both a fresh one from the queue (`resume: false`) and one assigned to
    you personally by the human or left over from a partial claim (`resume: true`) → **`claim`
    IS NEEDED**: it is what carries the task into Design and heals a partial claim (the tool
    asks for it itself, in `note`: "call claim(task_id) to finish moving it into Design"). Do
    not claim and the task stays stuck in Queue, and `advance(to='build')` refuses for the
    implementer: "moving to Build is only possible from Design".
  - `stage: "Design"` or `"Build"` (always `resume: true` — rework after
    `review-failed`, a human's answer out of Your Call, a crashed agent's work) → NO claim
    needed, it is already yours, and `claim` refuses: "you can only claim from Queue".
  - `stage: "Review"` (`review: true`) → this is not work for you but an offer to review
    somebody else's task: there is nothing to claim (see "Independent review of changes").
- Backlog is not touched — that is the human's triage zone.

## Continuous operation (loop)

- **Pick the `/loop` mode by supervision: self-paced (no interval) — only when
  supervised; for unattended / overnight runs — an INTERVAL-backed `/loop <interval>`
  (e.g. `/loop 10m`).** The difference is what survives a killed turn (session limit,
  API error, crash):
  - *Self-paced* arms its next tick ONLY through an end-of-turn `ScheduleWakeup` call.
    A turn killed BEFORE that call arms nothing — no cron, no watchdog — and the loop
    quietly ceases to exist FOREVER (the only fallback, ~20 min, belongs to a CLEANLY
    finished iteration, not to a killed turn). That is exactly the reported incident:
    the limit hit right after a subagent returned — the orchestrator managed neither
    the dispatch nor the `ScheduleWakeup`.
  - *Interval-backed* stores the cadence as a persistent session cron; the harness's
    background daemon checks it every second and fires BETWEEN turns — which is why it
    SURVIVES a killed turn and carries on by itself at the next tick. Drain-inside-a-tick
    is preserved (a tick drains the whole queue), missed ticks do not pile up. The price
    is a fixed cadence: a task that arrives in the pause between ticks waits out the interval.
  - *The honest remainder:* NEITHER mode survives the exit of the session PROCESS itself
    (a full exit, not a killed turn). Recovery is then a human
    `claude --resume`/`--continue` (restores session crons within 7 days) or an
    EXTERNAL process supervisor; this repo has none — that is the level of the harness or
    of the sibling project hgdev-acp (a self-hosted agent launcher), not of vikunja-mcp.
    The SessionStart hook (`vikunja-tracker-orchestrator.sh`) is FRAMING (it injects the
    orchestrator's context at start/resume/compact), NOT ignition: it starts no turn
    and does not re-issue `/loop`, and it does NOT resurrect a dead loop.
- **The drain overrides the generic auto-loop default.** The default autonomous `/loop`
  teaches "you are a steward, not an initiator: do not start fresh work without an
  explicit go-ahead, stop when idle". That does NOT apply to the tracker orchestrator:
  the Queue is work a human has already triaged, so `claim` of a fresh task and the
  dispatch are your mandate, not "unsanctioned initiative". `next_task` returned a free
  one (`resume:false`, but there is a `task`) — you claim and drain, you do NOT stop the
  cycle and do NOT wait for separate permission. A stop comes only on an explicit
  request from the human; empty (`task:null`) — yield the turn until the next tick
  (see "The queue is empty"), never a stop.
- **Inside one tick — drain the queue.** As soon as a per-task agent has returned
  (having taken its task to Review) — `next_task` again and dispatch onto the next; do not
  end the turn while `next_task` keeps handing out tasks. (At `wip.limit > 1` you do not wait
  for a return at all: while there are free slots, you keep dispatching — see "Parallel drain".)
- **The drain's width is set by the config, not by you.** `next_task` returns
  `wip: {active, limit, free}` in EVERY response: `active` — how many tasks you already hold in
  Design/Build, `limit` — how many you are allowed (the `wip_limit` key in the project's repo
  config; NOT set — the default is **3**), `free` — how many slots are left. Do NOT invent the
  number of slots and do NOT hardcode it: read it from `wip` every time — the human sets it in
  the project config, and it differs between projects (and between sessions of one). **`limit`
  and `free` are ALWAYS numbers: `null` no longer occurs there, and "no limit" does not exist
  as a state** — the `claim` gate is always on, so branch on the NUMBER itself:
  - **`limit: 1` — the drain is SEQUENTIAL**: exactly the "one task at a time" of "Queue
    discipline". claim → dispatched a per-task agent → waited for it to take the task to
    Review → only then the next one. This is what a human turns on explicitly (`wip_limit = 1`
    or the old `enforce_single_wip = true`), not what comes out by itself.
  - **`limit > 1` — keep up to `limit` per-task agents at once** (and this is the case a
    project that never set `wip_limit` at all lands in), each in ITS OWN worktree working
    directory (see "Parallel drain"). This is not "allowed if you judge it safe": a free
    slot against a non-empty queue is idle time, and holding it back on a suspicion that the
    cards touch the same code is not allowed (see "Free slots GET FILLED" — an overlap is
    caught by integration). We NEVER keep two agents in ONE working directory: they will
    fight over files, over the index and over HEAD, and the tasks' diffs will smear together.
    (Nested subagents of ONE task are still fine, that is its own
    business: they live in ITS tree, and keeping them from colliding is its job. One case has
    already been lifted out of "its own business" into a RULE: ANY WRITING subagent — one that
    mutates sources and runs tests over them — gets ITS OWN clone. Most often that is the second
    independent pass, which is where it was measured, but the mechanism is "two writers in one
    directory", not a role: see "WHERE it works".)
- **`limit` is a gate on ONE transition (`claim`), not an invariant on `active`: `active`
  LEGITIMATELY runs HIGHER than `limit`.** The gate is held by exactly `claim` (Queue → Design)
  and by nothing else. A card re-enters Build PAST `claim`, and here are the measured paths —
  there is deliberately NO COUNT in this enumeration: what is counted is the PAIR "the stage is
  active AND the card is assigned to you", and either half of it can change, so the list is
  never closed. `review_task(verdict='needs_work')` moves it Review → Build; a human returns it
  by hand out of Your Call (and generally puts a card assigned to you into Design/Build); a
  human ADDS you to the assignees of a card that is ALREADY in Build — here nothing moves at
  all, the stage is the same and `active` grew (measured on a live `Workflow`: 3/3 → 4/3, the
  card standing in Build before and after); a human LOWERED `wip_limit`
  in the config while tasks are in flight — here, too, not one card moved, and `active` is
  already above the limit. None of these paths passes the gate, and none of them should:
  refusing rework means leaving already-reviewed work hanging. (`advance(to='build')` is NOT on
  this list, contrary to the obvious assumption: Design and Build are BOTH active, it does not
  move the counter at all — on an answer out of Your Call the overshoot appears at the moment
  the HUMAN moves the card, not when you call `advance`.) Reproduced on a live `Workflow`
  2026-07-30: at `limit: 3` one review rejection gives `{"active": 4, "limit": 3, "free": 0}`,
  two give `active: 5`.
  **Seeing `active > limit` is NOT board corruption, NOT a reason to "fix" it and NOT a reason
  to call a human: it is rework, and rework has priority over a fresh claim (see "Priority is
  your active task"). Drain it — the overshoot dissolves by itself once it moves to Review.**
  And it is visible ONLY from the `active`/`limit` pair: `free` is `max(0, limit − active)`, so
  "exactly full" and "two cards over" look identical, as `free: 0`. It does not affect the
  "claim or not" decision (you may not, in either case, and `claim` refuses with an honest
  number: "WIP limit reached (4/3)"), but it does affect the diagnosis. That is why `next_task`,
  handing out a resumable task at `active > limit`, writes it straight into the `note` ("you
  hold N active tasks against a limit of M … Drain the rework"); at `active <= limit` that
  phrase is NOT in the `note` at all — see it, and the overshoot is real.
- **`wip_saturated: true` is NOT an empty queue.** Every slot is busy, so `next_task` did not
  even LOOK at the free queue: "handed nothing out" here means "nowhere to put it", not "there
  is no work" (how much of it is there, this response does not say). Wait for an agent to return
  and call `next_task`. Do not claim (`claim` refuses on its own — "WIP limit reached") and do
  NOT yield the turn: `ScheduleWakeup` here throws away exactly the tick in which a slot would
  have come free. Tell it apart from an empty queue: an empty one does not have this field. Its
  `message` is the only place in the payload where both numbers stand side by side in prose
  ("all 3 WIP slot(s) are busy (4 active)"), so the overshoot is visible there at once; read it
  as "busy, and then some", not as a fault. And the converse is FALSE: the absence of the field
  does not by itself mean "not saturated" — the signal appears at all only if `exclude` is full
  (see "A complete `exclude` is also the VISIBILITY of signals" in "Parallel drain").
- **Priority is your active task.** Review fixes (the task came back into Build) and
  human answers out of Your Call arrive through `next_task` as "your active task"
  and go ahead of the free queue. A returned card takes a slot again — and if the slots
  were all busy, it pushes `wip.active` PAST `limit`. That is by design: this priority is
  precisely the reason the gate sits on `claim` and not on the counter (see "The drain's width").
- **The queue is empty (`next_task` returned nothing) — do not spin idle, yield the turn
  until the next tick.** Do not spam repeat calls. In INTERVAL-backed mode simply
  end the turn — the cron raises you after the interval (and do NOT call `ScheduleWakeup`:
  it doubles a tick on top of the cron). SELF-PACED, `ScheduleWakeup` (~10 min, 600s)
  is MANDATORY, otherwise the loop will not wake at all. At the next tick — `next_task` again.
- **A starving tail (`next_task` returned `starving:true`) is NOT an empty queue.**
  The free queue is not empty, but ALL its tasks are gated by unfinished predecessors
  (an epic's chain has stalled). There is nothing to claim (`task:null`), but that is NOT
  "nothing to do": tell the human about the stuck tail — `waiting`/`waiting_count` enumerate
  the waiting tasks and their blocking heads, and `needs_retriage:true` means the head was
  sent back to Backlog via `return_task` and a human must re-triage it. Then yield the turn
  until the next tick, as on an empty one (see "The queue is empty"). Do not confuse it with
  an empty queue: an empty one does not have these fields.
- **A cycle of predecessors (`next_task` returned `cycle:true`) — call the human, do NOT sleep.**
  A special case of starvation: the follows/blocked relations form a loop (e.g. A follows B,
  B follows A — enterable only by hand in the web UI; `decompose(ordered)` creates no cycles),
  so NOTHING in the cycle is claimable and it will NOT unblock itself. `cycle_tasks`
  names the tasks in the loop. This is NOT an ordinary starving tail (that one dissolves once
  the head reaches Review) and NOT an empty queue: ONLY a human can break it, by removing one
  follows/blocked relation in the web UI. So do not get away with a `ScheduleWakeup` — file the
  question via `call_human`, otherwise the chain stands forever. Then yield the turn.
## Parallel drain (when `wip.limit > 1`)

One identity, several tasks AT ONCE — each in its own git worktree, so that per-task agents do
not trample each other's working directory. This is the DEFAULT mode, not an exotic one: a
project that never set `wip_limit` gets `limit: 3` and lands here. The trees are created and
removed by the CLI of the same package that serves these tools: `vikunja-mcp workspace`. Run it
THE SAME WAY the tracker's MCP server is started in this project (look at `.mcp.json` — usually
`uvx --from git+…@stable vikunja-mcp`): a bare `vikunja-mcp` may not be on PATH. Every command
returns one line of JSON.

- **The orchestrator's tick:**
  1. `vikunja-mcp workspace --gc` — housekeeping FIRST: tear down the trees the board no longer
     has live work behind. Liveness is counted BY ROLE, and that is worth getting exactly right:
     a build tree is live while its task is in Design/Build and assigned to you; a review tree is
     live while the card is in Review. What gets swept is what the work has LEFT: the task
     reached Review or Done, went off to Backlog/Your Call, the card left Review. `--gc` does NOT
     touch the tree of a CRASHED agent — its task stayed yours in Design/Build, that is, live,
     and the resume agent comes back to exactly that tree (this is ONE of the two returns, see
     "Two returns, two trees"). It returns THREE lists:
     `{"released": [...], "kept": [...], "expected": [...]}`, and you must act on TWO of them:
     read `kept` in full and scan `released` for `branch_deleted: false` and for
     `removed_ignored`. Plus TWO OPTIONAL keys, each absent when it has nothing to say:
     `deferred` — trees the sweep DECLINED TO INSPECT because they are dead but were written in
     moments ago (no action: a later sweep inspects them) — and `main_checkout`, which
     is not about trees at all but about the MAIN checkout; both get a bullet of their own below.
     - **Reading the `--gc` RESPONSE is in `references/gc-report.md`; open it the moment the
       response is non-empty.** Every form is worked through there: `main_checkout` (the
       `MAIN_SYNC_*` codes, what to do on `updated: false`), every `code` in `kept`, the fields
       of `released`, `removed_ignored` and `expected`. Here — only what cannot be left undone:
       * **Read `kept` IN FULL** — it says "could not clean up, and this is NOT routine, look".
       * **Scan `released`** for `branch_deleted: false` (the tree went, the branch leaked) and
         for `removed_ignored` (files `git status` does not see were DESTROYED together with the
         tree — that is not a warning, it is a record of loss).
       * **The `main_checkout` key is OPTIONAL: present ⇒ read it.** Absent means there was
         nothing to fast-forward.
       * **`deferred` is OPTIONAL too, and it needs NO action** — it is to a SKIP what `expected`
         is to a refusal. Each entry is a tree that is dead by the board but inside the grace
         window, so gc did not touch it; a later sweep INSPECTS it, and removes it unless a
         release guard then refuses. It exists because three empty lists over three skipped trees
         used to be indistinguishable from "nothing to do" (#1183). Do NOT read a `deferred`
         entry as a reaper that has stopped, and do NOT go into the tree to "help".
       * **An unfamiliar `code` goes to `kept`, not to `expected`**, deliberately: better one
         look too many than one swallowed.
     `--gc` goes ALONE: it combines with neither a task id, nor `--release`, nor `--role`, nor
     `--at` (it refuses — a silently swallowed argument is worse than an error). It is also the
     only subcommand that READS the board (so it needs the config and a token, but changes
     nothing in the tracker itself); creating and releasing a tree never go to the tracker and
     need no token — the only thing that goes to the network from them is git (on create,
     `git fetch origin`).
  2. While `wip.free > 0`: `next_task(exclude=[ids of the tasks you have an agent living on
     RIGHT NOW])` → `claim` BY THE `stage` RULE (see "Queue discipline"; in parallel mode it is
     exactly the same rule, there is no separate one). This loop spins to the END — as long as
     there is a slot and `next_task` gives out a task (see "Free slots GET FILLED"). Then branch
     on the shape of the response:
     - **the task is yours** (`stage: "Queue"` — after the claim it is already in Design — or
       `stage: "Design"/"Build"` outright): `vikunja-mcp workspace <id>` → dispatch a BACKGROUND
       per-task agent, and the `path` from the response goes into its brief as the working
       directory. It is already there on its own throwaway `task/<id>` branch, cut from a fresh
       `origin/<main branch>`.
     - **a review offering** (`stage: "Review"`, `review: true`): it takes no slot, and the tree
       it needs is a review one — dispatch the reviewer as in step 3, create neither a build
       agent nor a `task/<id>` branch.
     - **`claim` REFUSED — the id goes into `exclude` until the end of the tick, and you keep
       draining.** A refusal (not your card, an unfinished predecessor, the slot gate) changes
       NOTHING on the board, so on the next iteration `next_task` will honestly offer the same
       card again — and `while wip.free > 0` will spin on it for nothing (in sequential mode this
       was just noise: there is only one iteration there). This happens routinely: a card in
       Queue assigned by the human is handed out as "your partial claim", and `claim` will not
       let it through until the predecessor has arrived. Added the id to `exclude` — off for a
       new `next_task`; nothing left to offer — behave as on an empty queue (step 4).
  3. An agent came back with its result → FIRST check the sha from its `evidence` with the same
     two commands ("Commit+push is part of the transition to Review"), but after `git fetch origin`,
     because the sha is somebody else's: `--at` checks only that the commit EXISTS, not that it
     is on the main branch, and a pre-rebase sha will quietly give the reviewer a tree nailed to
     code that never went to the main branch. **It does not check out — do NOT dispatch a review
     (there is nothing to look at), and do NOT call `call_human` here: the card is already in
     Review, and it works only from Design/Build** (you will get `call_human works only from
     Design/Build; task is in Review`). Leaving it in Review with a single comment is a dead end
     too, but since #991 a DIFFERENT one: a card without a verdict will be offered for review
     AGAIN (in a solo setup — to you), and so on every tick, because the only thing that takes it
     off the offering is a verdict. Instead of a quiet hang you get an endless re-dispatch of a
     reviewer onto a sha that does not exist. Send it back to Build with the ONE thing that works
     from Review: `review_task(<id>, verdict='needs_work', report=…)` — it requires no ownership,
     hangs `review-failed`, leaves the assignee and moves the card to Build. In `report` — what
     exactly was missing: the sha from `evidence`, the command and its return code (128 — no such
     commit even after a `fetch`; 1 — it exists, but not on the main branch). After that the card
     comes back on its own: on the next `next_task` it arrives as "your active one"
     (`resume: true`, `stage: "Build"`, the slot is taken again) → you dispatch a fresh resume
     agent onto it. This is not a dead end but a repair: the typical cause (never pushed at all;
     named a pre-rebase sha) is cured by a re-push, and only if it was not cured — `call_human`,
     which from Build is already legitimate. And this is NOT the orchestrator's right to judge
     code: `needs_work` here is a mechanical refusal to accept unverifiable evidence, not a
     verdict on the merits; `verdict='approve'` the orchestrator NEVER sets (the gate will let it
     through — the rule will not). It checks out → you dispatch the reviewer in the background
     and give it its OWN tree: `vikunja-mcp workspace <id> --role review --at <sha from
     evidence>` — it is detached exactly on that commit (a freshly created one answers
     `created: true` and that sha as `head`; a review tree has no branch — `branch: null`). The
     slot is free → back to step 2.
  4. `wip_saturated: true` → you wait for any agent to come back (you do not yield the turn, see
     "Continuous operation (loop)"). `task: null` with no `wip_saturated` and nobody at work → you yield the
     turn until the next tick.
- **The rest of the drain rules are in `references/drain.md`.** There: why free slots GET FILLED
  while overlap is caught at integration; why you do NOT see the queue; how `exclude` is kept and
  why its completeness is also a matter of signal visibility; the two returns and the two trees;
  a reviewer releasing its tree; what to do when `workspace` refused. Not subject to forgetting
  here either:
  - **`exclude` is kept by YOU, and only within the tick** — the tracker does not know whether
    your subagent is alive. Pass ALL the ids that have an agent living on them right now: an
    incomplete `exclude` is not only a risk of a double claim, it is a loss of signals.
  - **Holding a slot back "to be on the safe side", because two cards LOOK LIKE they touch one
    module, is NOT ALLOWED** — that is substituting a guess for the project's mechanism.
  - **A review takes no slot**: `wip.active` counts only Design/Build assigned to you.
  - **It would not come up — do NOT drop the loop.** Any `workspace` refusal degrades to one slot
    in this checkout, but never stops the drain. And read `released: false` in THREE readings,
    not in one: `dirty` and `unpushed` mean "the work is in place, I am keeping it safe", while
    `no-worktree` means the tree is gone anyway, that is, this is a routine success and not a
    protective refusal. Confusing them means going off to rescue what is not there.
## Shared resources: a worktree isolates FILES, and only those

These are the rules for the PER-TASK AGENT (and the reviewer), not for the pump. The parallel
drain hands you your own working directory — and NOTHING MORE. Everything else your task can
reach (the browser, ports, containers, directories outside the tree) you SHARE with your
siblings, and the default is `limit: 3`: assume two more are working right beside you right now
until you know otherwise. The rule in one line: **a name you did not derive from your own task's
id is shared.** A path, a container name, a port, a file name.

### What does NOT collide — do not serialise for nothing

Listed so that "isolate" does not degenerate into "I will wait just in case": holding a slot back
is forbidden (see "Free slots GET FILLED"), and what is listed below is verified.

- **git between trees.** Each worktree has its own index (`.git/worktrees/<name>/index`), and git
  locks the objects and the refs itself: 24 simultaneous `git fetch origin` from three trees —
  zero errors and zero output. The race for the main branch is on the REMOTE's side, and it is
  already resolved by the fetch+rebase+re-check+push loop (see "Commit+push is part of the transition to Review"), not by abstaining from parallelism.
- **`.venv` and the caches** (`__pycache__`, `.pytest_cache`, `.ruff_cache`) sit INSIDE your tree
  — they are yours. The shared `uv` cache (`~/.cache/uv`) uv locks itself: three simultaneous
  `uv run` in one project — all three succeed, one created the venv, the rest waited.
  **"Yours" here means "not a sibling's", not "nobody else's":** your NESTED subagent stands in
  THE SAME tree, so the caches, the `.venv` and the sources themselves are SHARED between you —
  and if it writes (mutates and runs), that is not hygiene but the correctness of its own
  conclusions: see "WHERE it works" in "A second independent pass over YOUR OWN text".
- **The tracker.** Each agent touches its own card; the only shared thing is the MCP server of
  the tools, and it holds no per-task state.
- **`workspace` (`<id>`, `--release`, `--gc`)** — tree mutations are serialised by a repo-wide
  flock (`.git/vikunja-mcp-worktree.lock`): in the queue you simply wait, there is nothing to do.

### What does collide: everything with a FIXED name

- **The directory for temporary files is ONE per SESSION, not per agent.** Verified on a live
  run: the scratchpad the harness handed out "for temporary files" held 179 entries (166 files),
  written over a day by DIFFERENT agents of one session, with names of the form `a.log`,
  `out.json`, `check.py`. Two agents that took one obvious name will overwrite each other
  silently and without an error. So: everything you create OUTSIDE your tree gets your own task's
  id in its name (`…/scratchpad/554-probe.log`), or give yourself a subdirectory
  `…/scratchpad/554/`. One id is NOT ENOUGH where SEVERAL agents work on one card at once (the
  author, the reviewers of the rounds, their second-pass auditors) — exactly as with the docker
  name below: append a role suffix, the way the second-pass fence does with its own clone
  (`$ID-pass2-audit`). Better still, do not create it at all: what can live inside your tree —
  let it live there. **And symmetrically — DELETE only your own too:** a recursive cleanup over
  the whole scratchpad (`find <scratchpad> -name __pycache__ -exec rm -rf`, "let me tidy up
  before the sweep") takes out files belonging to live neighbours and not only yours — and
  silently, because nobody misses somebody else's files right away. The order of magnitude,
  measured by listing (without deleting) twice during the half hour of task 702: under the shared
  scratchpad there are HUNDREDS of `__pycache__` directories, and only a HANDFUL of them are
  yours, and both figures grew noticeably over that half hour. Do not learn the figures by heart
  — they live a life of their own; re-measure with the same `find` WITHOUT `-exec` before you
  append `-exec`. The root of such a command is your subdirectory or your clone, never the
  scratchpad.
  **And delete by ENUMERATING what you created, not by a glob over your own prefix:** "the root
  is mine" does NOT guarantee that, and here is why. The two name forms this very bullet offers
  as a choice above (`<id>-something` and the subdirectory `<id>/`) are PREFIXES of each other,
  so a glob over the first captures the second, and both agents were FOLLOWING the rule.
  Constructed, not deduced: in a directory holding `702r3`, `702r3-sweep`, `702r3-pass4-audit`
  and `702r3-logs`, the glob `702r3*` expands to all FOUR, and `702r3-*` to three; the first set
  includes SOMEBODY ELSE'S directory. The incident was real, not constructed, and it is known
  from the OWN report of 702's author: he wiped round 2's reviewer's directory with the glob
  `702r3*` (no trace was left in the tree — this is his `[worklog]`, not a commit). Nothing live
  was lost that time, but `rm -rf` over a glob is silent — unlike an occupied docker name, which
  fails loudly.
- **The container name and the port from the docs are FIXED, and therefore shared.** The recipes
  in README/CLAUDE.md were written for one agent: in this repository integration means
  `--name vikunja-test -p 3456:3456`. Copy it as is while a sibling is doing the same, and you
  get `Conflict. The container name "/vikunja-test" is already in use` or
  `Bind for 0.0.0.0:3456 failed: port is already allocated` (both verified). The same goes for
  any dev server you bring up to check your own edit. Derive the name and the port from your own
  id, and clean up after yourself:

  ```sh
  ID=554                                   # the id of YOUR task. REVIEWER: it is SOMEBODY
                                           # ELSE'S — append a role suffix, or the name collides
                                           # with the container of this same card's build agent,
                                           # and docker's "delete it and retry" kills ITS work
  NAME=vikunja-test-$ID                    # instead of the fixed name from the docs
  PORT=$((20000 + ID % 10000))             # 20554 — deterministic, survives a resume
  lsof -nP -iTCP:$PORT -sTCP:LISTEN        # empty — free; occupied — take a neighbouring one
  docker run -d --name "$NAME" -p "$PORT":3456 …
  VIKUNJA_TEST_URL=http://localhost:$PORT uv run pytest tests/integration -q
  docker rm -f "$NAME"                     # MANDATORY, and before advance
  ```

  Cleaning up is mandatory: a leaked container holds the name and the port until the end of the
  day and breaks not you but the next one.

### The browser (playwright): bringing up YOUR OWN is ALLOWED — a shared one can only be noticed

**The full breakdown is in `references/browser.md`; open it before your first `browser_*` call.**
There: how to bring up your own process, what a shared profile risks, what exactly leaks and
where. Here — what must not be broken:

- **Your own browser is brought up with `--isolated`** — the shared profile is derived FROM THE
  WORKSPACE ROOT, so two sessions on one repository converge into one and the second browser does
  not start at all.
- **Write artifacts ONLY under `.playwright-mcp/<id of YOUR task>/`** — `.gitignore` covers that
  directory wholesale, independently of name and format. A bare name in `filename` resolves
  against the SERVER's cwd and lands in the ROOT of the repository.
- **Create the directory in advance (`mkdir -p`)**: an explicit `filename` does not create it.
- **The `--output-dir` of your own browser goes there too** (`.playwright-mcp/<id>`), or outside
  the repository entirely.
## Who does the work: the orchestrator-pump and the per-task agents

- **The main session is a thin orchestrator-pump, not an implementer and not a designer.**
  Its cycle: `next_task` → `claim` (when `stage: "Queue"` — by the rule "Whether to claim is
  decided by `stage`" from "Queue discipline"; NOT "only a fresh one": a card assigned by the
  human and a partial claim you claim as well) → dispatch ONE fresh per-task agent for the
  WHOLE task → wait for its short result → the next one.
  (That is at `wip.limit: 1`, i.e. when the human has NARROWED the drain down to sequential.
  At `wip.limit > 1` — and that is the default, `3` — you do not wait for the result: while
  there are free slots you keep dispatching and hold up to `limit` agents at once, each in its
  own tree — see "Parallel drain". Everything else in this section is the same for both modes.)
  The orchestrator does NOT do Design, does NOT write `[spec]`, does NOT implement, does NOT
  commit and does NOT call `advance` — all of that is inside the per-task agent. Its context
  stays light: it sees the dispatch brief and a short result, not the Design/Build reasoning.
- **The per-task agent CRASHED (a runtime/API error) instead of returning a result — the
  orchestrator RE-launches, it does not drop the task.** A dispatched agent can die mid-sentence
  from a Claude runtime error (e.g. "Agent terminated early due to an API error: API Error:
  Connection closed mid-response") — that is NOT a tracker error and NOT an error in the task's
  code. Why "it does not retry by itself": the harness does retry many transient API failures
  on its own, but a connection dropped mid-sentence terminates the subagent, and it does not
  restart itself; vikunja-mcp (the MCP server of the tools) takes no part in that loop and
  cannot revive a dead subagent. Recovery is on the ORCHESTRATOR: having received a
  notification that the agent crashed/died (rather than a short result), it does NOT abandon the
  task and does NOT stop — it calls `next_task` again (the task is still its own, in
  Design/Build) and dispatches a FRESH resume agent. That one, by the rule "A resumable task
  (resume)" (see "Queue discipline"), re-reads the dossier (`get_task`) and the git log, works
  out what has already been done, and takes the task to the end. A task idles ONLY if the
  orchestrator silently abandoned it — and that is exactly what we do not do. (Transient errors
  of the tracker itself api.py retries with backoff inside the client — that is a different,
  lower layer, invisible to the agent.)
  - **A REVIEWER crashed — since #991 the mechanism EXISTS, and a MIRRORED worry came with it.**
    One round ago this said "there is no mechanism", and that was true: the review-offering
    branch skipped cards assigned to you, and in a solo setup they are all yours, so a card
    stood quietly in Review without a verdict and there was nobody to deliver one — not on a
    single tick. Now the skip is conditional on `require_review_independence` (false by
    default), and a card without a verdict comes AGAIN, as many times as it takes: what takes it
    off the offering is exactly a verdict. A crashed reviewer reminds you of itself — like a
    crashed build agent.
    **The price is exactly the reverse of the old one: WITHIN a tick the same card will be
    offered once more, that is, you can dispatch a second reviewer onto one piece of work.** So
    put the id of a dispatched review into `exclude` — it used to be useless (the card did not
    come anyway), now it is load-bearing. Keep your own list for the tick regardless: `exclude`
    protects against a duplicate, while a reviewer that did NOT come back is still something
    only you will notice.
- **The per-task agent runs the WHOLE task itself** (a fresh one per task; the model by the
  grading rule below, which on anything that writes code keeps it senior; loads the tracker
  tools through ToolSearch). The brief from the orchestrator: the task id, the working
  directory, the readiness criteria (tests/lint) and `wip.limit` from the `next_task`
  response — the agent computes its ceiling of integration rounds from it (see "Where the
  ceiling comes from"); do not name it and it will read `wip_limit` from the repo config itself,
  but that is an extra step and an extra way to be wrong. **Name `wip.active` from that same
  response TOO** — the ceiling is computed from the `max` of the two, and the agent has nowhere
  to read `active` from: it is board state, not config. From there the agent goes on its own:
  `get_task` (the dossier — description, spec, comments) → Design and
  `advance(to='build', spec=...)` → implementation → commit+push of the task's diff →
  `advance(to='review')` with a report (worklog/evidence; for bugs — root_cause). All the rules
  below about running a task (the gates, the journal comments, the resume re-check,
  `call_human`, the tools' note hints) are about it; the orchestrator does not execute them, it
  only pumps the queue.
- **The agent MAY spawn subagents of its own.** It does the implementation either inline (by the
  narrow whitelist below) or by dispatching further — a separate implementer, or parallel agents
  on unrelated pieces. For its own task it is the same kind of orchestrator that the main
  session is for the queue.
- **The model is the per-dispatch DECISION, and the only depth knob the `Agent` CALL itself
  takes.** It accepts `opus`/`sonnet`/`haiku`/`fable` — ignored for a `fork`, which inherits —
  and NO effort or reasoning parameter. **Effort is not unreachable, it is just not HERE, and
  the difference matters:** an agent DEFINITION accepts an `effort` key, and that key is now
  MEASURED to reach the wire — the value written in the file arrives on the subagent's own API
  request, it BEATS the session's `--effort`, and an absent key INHERITS the session instead. But
  it is deleted, silently, whenever the resolved model carries no effort capability — which is
  what `haiku` is — and **the `model` you pass HERE overrides the definition's own**, BOTH ways,
  so a definition's `model:` does no work on any dispatch that names one. Deleting the effort
  needs a call site that RESOLVES to `haiku`, and the rule below never permits that: its one step
  stops at Sonnet class, which HAS the capability. So a maintained SET of definitions is a lever
  whose model half this one already overrides, and this repo defines no agent types anyway:
  today it is not a lever you HAVE, and do not write a rule that assumes one. The session-wide
  controls (`effortLevel`, `/effort`, `MAX_THINKING_TOKENS`) move the whole SESSION — and a
  subagent that names no effort inherits them, which makes them worse as a per-card lever rather
  than better (`references/dispatch-depth.md`).
- **Choose by BLAST RADIUS and REVERSIBILITY, never by file type or diff size** — and state the
  choice with its ground in one clause of the brief, because an unstated choice is the default
  and not a decision. **Senior (Opus class), non-negotiable, if ANY of these holds:** the
  dispatch writes code, or changes a gate, a guard or a rule; a wrong APPROVE would reach
  consumers through `stable`; a revert would NOT undo it because something downstream has already
  acted on it (for example, a rule already sitting in every agent's context); or
  checking the card means RE-DERIVING a measurement. **One rung down (Sonnet class) is
  permitted** only when every one of those is false — the change is inert, a revert restores it
  completely, and there is nothing to re-measure. The step is ONE rung, and it stops there: no
  rung of this ladder has been measured against any role here, so the bottom (Haiku) and the top
  (Fable) are UNMEASURED rather than free.
- **The model is price per TOKEN; the BRIEF is the only PER-DISPATCH lever on the NUMBER of
  tokens.** So the same brief says whether this dispatch may raise sub-agents of its OWN —
  nesting is the multiplier — and names the diff, the sha and the files, instead of leaving a
  reviewer to discover the scope by reading. It may narrow what is at STAKE; it may never waive
  verification by running.
  → **`references/dispatch-depth.md`**: the surface as measured, the wire-level settlement of
  the `effort` key and the four things that still override or delete it, and the 643k-token card
  that spent 337k of it on two rounds of review, the second over a `+8/-5` diff with no code.
- **Why:** a clean context per task (decisions from neighbouring tasks do not leak across), the
  orchestrator stays light and lives long, and symmetry with review — the author and the
  reviewer have their own unmixed contexts.
- **Inline vs a nested dispatch — the per-task agent decides (a narrow whitelist, not about
  size).** Inline is admissible only if the edit falls ENTIRELY into at least one item: (a)
  config/data (toml/json/yaml/env); (b) text/docs/comments; (c) a pure rename or a mechanical
  replacement WITHOUT a change of behaviour — AND it passes ALL the guards: it does not touch
  .py logic; it does NOT add or change tests; it does NOT change the behaviour of a
  tool/gate/workflow. Any guard that does not check out → dispatch a nested subagent. Size
  (lines, minutes) is NOT a criterion: a short diff can change behaviour too.
- **Self-check before going inline:** the file — config/text only? · zero changes in .py
  logic? · zero new/edited tests? · behaviour unchanged? All "yes" → inline is fine; a
  single "no" → dispatch. A trap example: a gate bugfix in `workflow.py` +
  a unit test — NOT trivial, dispatch, even if the diff is 3 lines.
- Review of the changes is always a separate subagent (see below), never the same one that
  wrote the code.
## Traces of the work (comments are the journal)

- **Write your card text in the language `next_task` names.** Every `next_task` response carries
  `language` beside `wip` (`"en"` by default, `"ru"` the other value; the project's human sets it
  in `.vikunja-mcp.toml`, where it is committed team policy like `wip_limit` — you cannot change
  it and there is no env override). It governs the text YOU author: the `spec`, the `worklog` and
  `root_cause`, a `call_human` question, a `[review]` report, and any `comment` meant for a human.
  That is the BULK of a card and the tool writes none of it: what it translates is its own
  boilerplate, a short table of fixed strings. So if you ignore this the board ends up with the
  boilerplate in one language and everything that matters in the other, which is worse than
  either language on its own.
  **Nothing in brackets translates, in either direction.** Write every marker exactly as this file
  spells it — `[spec]` `[worklog]` `[review]` `[blocked]` `[needs-human]` `[decompose]`
  `[filed-by-agent]` `[attach]` `[claim]` `[epic-ready]` — and the same for the `APPROVE` /
  `NEEDS WORK` that follows `[review]`. Two of them are literally PARSED: `next_task` decides
  whether a Review card is offered to a reviewer by matching rendered comment text with
  `startswith("[worklog]")` and `startswith("[review]")`, so a translated bracket on those two
  drops the card out of the review offering silently. The rest are frozen with them because the
  set is read by eye and by grep, and a vocabulary that is half-translated is worse than either.
  Nothing OUTSIDE the tracker is governed at all: commit messages, code, code comments and repo
  docs follow the repository's own convention, not this key.
- **Refer to a task in a human-readable way.** In comments, reports (worklog) and any text
  meant for a human, name the task by the `ref` the tools hand you
  (`next_task`/`claim`/`get_task`, and `file_task` for a card you filed yourself)
  — "VMCP-27 (82)": the project identifier +
  index, PLUS the numeric id in brackets. **The two halves do DIFFERENT things, and that is why
  both are echoed.**
  The identifier is the READABLE name: the live UI prints `TGT-3` as the h1 heading on the task
  page, so a human reads the project and the card's ordinal off the card and checks by eye that
  it is the right one. What ADDRESSES is exactly the id in brackets: `/tasks/82` opens the card,
  and that is the very link the UI itself puts in its own task lists.
  **You CANNOT SEARCH by the identifier — not in the API, not on the web** (re-measured on a live
  2.3.0, #757: `?s=TGT-3` returns ZERO hits in both REST and the web interface's quick-actions,
  while a word from the title finds the card in both; `filter=identifier` is a 400). This used to
  say the opposite ("the index a human searches for it by in the tracker"; "a bare global id is
  useless for a human to search with") — that was the entire feature's only justification, and it
  was never once measured. The practical rule does not weaken from this, it is STRENGTHENED:
  since an invented identifier cannot be checked by searching, the reader's only cheap check is
  the id beside it. (The commit trailer
  stays `… (tracker #N)` — that is a separate grep convention over the history.)
  - **A `ref` is only ever HANDED OUT by a tool; ASSEMBLING one yourself is not allowed.** The
    index (`VMCP-27`) is assigned by the server: it is PER-PROJECT and counts from one, while the
    id is global, so no arithmetic derives the index from the id (re-measured with
    `get_task`: id 732 → `VMCP-195`, id 706 → `VMCP-181` — gaps of 537 and 525,
    not even constant). An invented reference does not look broken — it leads to an
    UNRELATED LIVE card, and the reader does not notice. Exactly that shipped into a
    landed file on #660: "Filed as VMCP-181 (732)", whereas 732 is
    `VMCP-195` and `VMCP-181` is a live card, id 706, about something else entirely
    (`canonical_base_url`; both pairs were re-checked with `get_task` while working on
    #735, not taken from someone else's report). The numeric half there was CORRECT; the
    wrong one was exactly the human-readable half — the one you must take from the tool
    rather than infer. If no tool handed you one (you are referring to someone else's card
    and have no ref in hand), call `get_task` — do not guess. But `get_task` is bound to
    YOUR project, so it will not fetch a card on SOMEONE ELSE'S board at all: there, if no
    ref arrived with the card, write a bare `#<id>` and say outright that there is no index.
    An honest `#82` beats a plausible lie.
  - **Filed it with `file_task` — the ref is ALREADY in hand**, in `filed.ref`; no separate
    `get_task` for it is needed. Filed into SOMEONE ELSE'S project (`project_id`) — the prefix
    there is the TARGET project's, not yours: echo it as it is, that is the name the card is READ
    by on ITS board (not searched for — the identifier cannot be searched by, see above).
    **`decompose` now has one too (#749):** every child arrives as
    `{id, ref, title}`, so a separate `get_task` for the reference is no longer needed on
    any surface that CREATES a card. Children used to arrive as `{id, title}`,
    and this rulebook itself sent you for a `get_task` on each one — while the value was already
    in the creation response and was simply thrown away. The "a ref is only ever HANDED OUT by a
    tool" rule is not softened by this: it is about not assembling the reference yourself, not
    about how many tools have one.
- The claim tool marks the card itself; follow it with a short `comment` describing the plan.
- Record findings and decisions as you go: "chose X over Y because Z",
  "stepped on gotcha W" — both humans and the agents after you read this.
- `advance(to='build')` requires a spec — 2-5 sentences on the approach, not an essay.
- **`advance(to='review')` = the report on the work done**, and the reviewer reads it:
  - `root_cause` — MANDATORY for bug fixes: the cause of the bug (why it arose —
    "the state is not subscribed to event X"), not the symptom ("the title did not render");
  - `worklog` — what was done (the approach, the key files) and HOW it was verified
    (what you ran, what you observed — verification by RUNNING, not by reading the code);
  - `evidence` — the sha/link of this task's commit (see the next bullet).
  Run the verification BEFORE the transition. A report with no cause on a bug is grounds for
  a human to send the task back to Build. And if this card's deliverable is TEXT with measurable
  claims (docstrings, code comments, rules), or your report itself is one, then a second
  independent pass runs over it before `advance` as well —
  see "A second independent pass over YOUR OWN text": it must be run EARLY, not right
  before handing in.
- **"Review needs a report" on a report you KNOW you wrote is NOT "you forgot".**
  The refusal is disjunctive, and since #657 it NAMES both the field and HOW it arrived. There
  are THREE fields it can name, not two: since #718 `root_cause` joined `worklog` and `evidence`,
  but ONLY on a card labelled `bug` (not on an epic container: nobody reviews it, so there is
  nobody to ask for a cause). Before #718 a missing `root_cause` was a silent no-op, and a bug fix
  reached the reviewer with no cause, even though both this file and the tool's docstring called
  the field mandatory — so "mandatory" here now means a gate, not a wish. Read exactly
  that part, not the general sense of the sentence:
  - `evidence — passed, but empty or whitespace-only` (or the same about `worklog`) — the field
    arrived empty. This is the ordinary "write it and retry".
  - `worklog — arrived as null, not as a string` while you passed a LONG
    text. **DO NOT CHECK THE PARAMETER NAME: of the four former causes this is the one that
    the very fact of this refusal now EXCLUDES.** Before #720 the rule said the opposite
    ("check the name first"), and it was correct: a typo (`wroklog`) was dropped SILENTLY and gave
    exactly this same refusal. Now an unknown argument is rejected AT THE BOUNDARY and BY NAME
    (`wroklog … Extra inputs are not permitted`, `isError=True`) before the tool's body runs — that
    is, if you are READING "arrived as null", you spelled the name right (measured over real stdio).
    There is one caveat, and it is not about your call: the gate is BEST-EFFORT, and if it did not
    come up, a typo is possible again. The server TRIES to say so with one line on stderr at start —
    but only tries: with fd 2 closed it says NOTHING (measured), and nobody shows you the server's
    stderr from inside a call anyway. That is a residual risk you CANNOT CHECK from here, not a
    signal to go after. So the text did NOT REACH the tool — and since
    VMCP-279 (938) it is known WHY, which is why the advice here FLIPPED to its opposite:
    **RETRY THE CALL.** A round ago this said "a retry with the same call is not a fix", and that
    rested on the mechanism not having been found. It has been found now, and it is in YOUR OWN
    EMISSION: your tool call is tag-structured, and a parameter whose OPENING TAG is written
    without the namespace prefix is not counted as a parameter by the parser at all — so it never
    becomes a JSON key and reaches the tool as `null`, silently and indistinguishably from "it was
    not passed". The discriminator that settles this holds POSITION and LENGTH constant and varies
    ONLY the tag: the same call (a long `worklog` first, an `evidence` of 40 spaces second)
    answers `evidence — arrived as null` with the tag corrupted and `evidence — passed, but
    empty or whitespace-only` with it correct. The control without which the sentinel is
    unreadable: the same 40 spaces, sent ALONE, arrive exactly as EMPTY — so whitespace is not
    being eaten. Neither size nor order has anything to do with it: it is measured that neither
    `Workflow` (1 MiB) nor a real MCP server over a real stdio transport (4 MiB, and 8 MiB on an
    independent re-measurement, byte-for-byte) truncates anything on kilobyte-sized reports, that
    there is NO CONTENT threshold (Cyrillic, NUL, CRLF, one 8 MiB line without a single newline),
    and that the ORDER of the arguments changes nothing — ten permutations across the real
    boundary, all byte-for-byte (#938).
    **So REORDERING the arguments is USELESS, and that is a refutation, not a refinement.** Three
    cards in a row independently decided that "the argument LAST in order is the one lost" and
    treated it by reordering; the predicate is false. It looks true because tag corruption
    CORRELATES with a long PRECEDING value: the tag that gets corrupted is the one on the
    parameter you write immediately AFTER a long block. Read that as a CAUSE and not as a
    frequency — nobody measured the frequency, and none of the three earlier calls was replayed;
    what is shown is that this cause produces their symptom and their predicate does not.
    The tool cannot tell THREE cases apart — there were FOUR, and #720 took the typo away from
    it: a lost key, an argument that was never passed and an EXPLICITLY passed `null` all arrive
    the same way — as `null` (the first two are literally the same shape on the wire). That is why
    it names the STATE and not the cause.
  - **There are EXACTLY FOUR silent forms** (measured): the key absent, `null`, `""`, a string of
    nothing but whitespace. The whole remaining JSON TYPE set — integer, float, boolean, list,
    object — is caught by validation LOUDLY and by name, ahead of our guard; the enumeration is
    complete over TYPES, not a "we tried a few". And "empty" here means "zero NON-whitespace
    characters", not zero bytes: 100 non-breaking spaces (200 bytes on the wire) are rejected just
    like an empty string — whereas 50 zero-width ones (ZWSP, U+FEFF, U+2060) are NOT whitespace,
    the guard lets them through, and the card goes to Review with a report that is empty to any
    reader. Checked both ways; do not plug the report with filler.
  - **The fallback if the retry does not take** (before #938 it was the only prescribed path, and
    its price is the one the card was filed over: the full report has to be CUT UP):
    move the card with a SHORT `worklog`, and lay the full
    report out as separate `comment(task_id, "[worklog] FULL REPORT (1/N) …")` calls BEFORE
    `advance`. Put the `[worklog]` marker as a PREFIX, and "(1/N)" too — and know what it means,
    because there is ONE predicate here and it is BLIND TO THE AUTHOR. `get_task` hands the
    reviewer every comment in order and filters nothing by marker, so a report without the marker
    does not disappear — it is simply easy to miss for someone scanning by eye. And `next_task`
    offers a card for review exactly when the MOST RECENT comment STARTING with `[worklog]` is
    newer than the last `[review]` — and it does not care whether `advance` wrote it or you did by
    hand. Constructed and checked on a live `Workflow`: after a review verdict the card is not
    offered; one manual comment with `[worklog]` as its prefix and it is offered AGAIN; the same
    text with the marker NOT at the start and it is not offered. Two consequences: lay the report
    chunks down BEFORE `advance` (as written above), and do not write a `[worklog]`-prefixed
    comment onto a card that already carries a verdict — you will dispatch an extra round of
    review. Do NOT leave a placeholder like `Worklog: probe` in the `[worklog]` — in even the
    shortest worklog, write that the full report is in separate comments above, otherwise the
    card's journal will claim one thing while another was done.
  - **There is NO THRESHOLD AT ALL — do not guess about it and do not size the report to it.** A
    round ago this said "nobody knows the threshold", and that was honest exactly until the
    mechanism was found: since it is the TAG that is lost and not the size, there was nothing to
    look for. On #657 the threshold could NOT BE REPRODUCED even once: in a live probe through an
    MCP client, `advance` accepted 5807 characters / 9598 bytes of UTF-8 on the first attempt (the
    delivered argument; the extra 7 bytes on the card are our own `[spec]\n` prefix). So neither
    "longer than N always fails" nor "up to N is safe" follows from this, and sizing the report's
    length to an imagined ceiling is wasted work. Branch on the word `null` in the refusal — it is
    about the FACT, not about the size. One honest bound on this whole analysis: the mechanism was
    found in THE harness these agents run under (a tag-structured tool call). A different harness
    that serialises the call differently may drop an argument for its own reason — "arrived as
    null" is then the same, and the diagnosis has to be made afresh.
- **A visually verifiable result — attach a screenshot.** If a human confirms the change is
  right by LOOKING (UI, a rendered page/chart, a generated image, the board's layout) — attach
  a screenshot of the finished result to the card with `attach_file(task_id, path, note=...)`
  and cite it in the `worklog` as evidence beside the sha. The screenshot is the one you took
  during verification anyway (the browser tool, the run/verify skill): the card is about
  ATTACHING what was already taken, there is no separate screenshotting mechanism to invent.
  If you took it with the SHARED browser tool, first check the `Page URL` with a neighbouring
  `browser_snapshot` (the screenshot itself does not print that line) and the file path against
  "Shared resources": the browser is one per session, the screenshot may turn out to be of a
  sibling's page, and it lands not in your worktree but in `<main checkout>/.playwright-mcp/` —
  provided you gave `filename` with that prefix, as prescribed there. It is simpler to avoid
  that race — take your own screenshot with your own process (same place, "Your own browser").
  Who decides that a task is "visually verifiable" — you do, on the substance of the work (not
  a label, not a heuristic). The rule is NOT for every task: a change with no visual surface
  (a lockfile, a refactor, a config) has nothing to show — do not force it. The upload leaves
  its own trace in the comment journal — `[attach] name (mime, size)`; pass `note=` as one
  line saying WHAT is in the screenshot ("the board after reconcile"), and do NOT post a
  separate duplicate comment about the upload itself (a failed journal comment comes back as
  `journal_comment: false` — the file is already on the card, do NOT re-upload it).
  `attach_file` is a separate step, it does NOT move the task; a failed upload (e.g. a token
  without the `tasks_attachments:create` scope) returns a clear error and does not affect the
  transition to Review. The limit is 25 MB; the attachment's name is the file's basename. A
  reviewer can also attach a screenshot to someone else's task in Review (ownership is not
  required).
- **Commit+push is part of the transition to Review, not a separate step.** The per-task agent
  commits the diff of its own task as its own commit on the MAIN BRANCH
  (`type(scope): … (tracker #N)` + a `Co-Authored-By` trailer) and PUSHES it — BEFORE
  `advance(to='review')`; `evidence` = that commit's sha. (If it dispatched its own
  implementer, it accepts that work and commits itself, under its own name.)
  - **Integration is rebase + RE-RUNNING the checks + push, not just `git push`.** In a
    parallel drain you sit in your own worktree on a THROWAWAY branch `task/<id>`: a bare
    `git push` pushes that branch, the main branch is left without your work, and every tool
    reports success — the task quietly ends up outside the release pipeline. Push EXPLICITLY:

    ```sh
    git add <this task's files>
    git commit -m "type(scope): … (tracker #N)"    # + the Co-Authored-By trailer
    # ONE chain, not separate turns: `&&` will not let you push on red criteria, and it
    # shrinks the window in which the race can be lost from your thinking to machine time
    git fetch origin && git rebase origin/main \
      && <RE-RUN THIS TASK'S ACCEPTANCE CRITERIA — the ones the orchestrator gave in the brief> \
      && git push origin HEAD:main   # rejected (not fast-forward) — do not retry blindly, see below
    # REJECTED? The FIRST question is not "who won" but "did the work NOT land after all?": the
    # server may have taken the ref and died on the response (502, a dropped connection) — the
    # client sees an error, the commit is on main. `git fetch` in this chain is load-bearing:
    # on a stale tracking ref the check LIES.
    git fetch origin && git merge-base --is-ancestor HEAD origin/main
    #   0 → your commit is ALREADY on main: the push landed, the client's error was a lie. Do NOT
    #       spend a round and do NOT call a human — this HEAD's sha IS the evidence, go
    #       to the confirmations
    #   1 → your work is not on main. NOW find out WHO won the race:
    git log --oneline HEAD..origin/main
    #   empty     → no race at all (protected branch, no rights, hook) — rounds
    #               will not help: call_human
    #   non-empty → mechanics (the bot's bump, a sibling's commit) — repeat the block,
    #               up to 2 × max(wip.limit, wip.active) rounds
    git rev-parse HEAD           # evidence CANDIDATE — read AFTER a successful push, not before
    # and only now — confirm that this sha really landed (both are silent on success):
    git cat-file -e "<sha>^{commit}"                   # 0 — the commit exists; 128 — no such commit
    git merge-base --is-ancestor "<sha>" origin/main   # 0 — it is REALLY on main; 1 — it is not
    ```

    (`main` here is the repository's main branch name; if it is called something else, put that.)
    Re-running AFTER the rebase is not belt-and-braces: while you worked, a neighbour may have
    landed on the main branch, and a rebase can splice two individually correct changes into one
    incorrect one WITHOUT A CONFLICT. A cleanly merged diff ≠ a correct diff — only a run tells.

    **And "the push went through" without the last two commands is faith in the absence of an
    error message, not a fact.** `git rev-parse HEAD` only PRINTS the local HEAD: a full
    40-character sha is returned with exit code 0 by both it and `rev-parse --verify`, even if
    no such object is in the repository at all — that is, the check usually used to catch "the
    agent named a sha that never existed" catches exactly that not at all. And existence is not
    enough: a PRE-rebase sha keeps resolving (the object lives until gc collects it), while it
    is not on the main branch and never will be — in a parallel drain a rebase before the push
    is the norm, not the exception. So there are two commands, and their exit codes MEAN
    different things: `cat-file -e` → 128 "no such commit here" (invented, a typo — or you
    simply did not fetch), `merge-base --is-ancestor` → 1 "the commit exists, but it is not on
    main" (pre-rebase, orphaned, unpushed). On success both print NOTHING — read the exit code,
    not the output. The quotes around `"<sha>^{commit}"` are mandatory: in zsh with
    `extendedglob` the unquoted form dies with `no matches found` before git even runs, and that
    looks like a verdict of "bad sha". Your own push updates the local `origin/main` itself — no
    separate fetch before the check is needed; but check SOMEONE ELSE'S sha (as a reviewer, as
    the orchestrator) only after `git fetch origin`, otherwise a commit that did land on main
    gives the same 128 as an invented one. If it does not check out, the task did NOT land: fix
    it (re-push) and re-check; do not send `evidence` with an unconfirmed sha.

    A rebase conflict breaks the chain at `rebase` (there will be no push) and you resolve it
    yourself — the task's context is precisely yours; if you cannot, or the rounds have run out
    (`2 × max(wip.limit, wip.active)`, see "Where the ceiling comes from"), `call_human`.
    **And remember what happens to the worktree when you do:**
    `call_human` takes the card to **Your Call**, which means that from that moment your worktree
    is DEAD as far as `--gc` is concerned (only a task in Design/Build behind you keeps it alive).
    What holds it is not the stage but UNSAVED work: while there is anything uncommitted or
    unpushed inside — and after a conflict or a rejected push that is exactly the case — the
    protections will not let it be removed. But if you managed a `git rebase --abort` and the
    worktree became clean and fully pushed, it may be swept on any tick while you wait for an
    answer: the work will not be lost (only what is already on the main branch is swept), but the
    directory may cease to exist. So once the human has answered, call
    `workspace <id>` again rather than assuming you are still standing in your own worktree.
    In sequential mode, in the main checkout, the recipe is THE SAME minus the throwaway branch.
  - **A rejected push is the NORM, not a sign of trouble: your main rival is a machine.** If the
    repository has an auto-release (a bot that pushes its own commit after EVERY green landing),
    a fresh rebase goes stale almost immediately after ANY landing, and a rejected push becomes
    the expected outcome rather than an edge case. Measured on vikunja-mcp's first live parallel
    drain (2026-07-30): of 46 landings on the main branch in one day, **17 were made by CI**, not
    by an agent; its bump commit arrives **37 s … 2 min 55 s** after the task commit (median
    1 min 41 s), the median interval between adjacent landings is 2 min, 65 % are ≤ 3 min.
    But the rival is BOUNDED: one commit per landing, and its own push is marked
    `[skip ci]` — it does not trigger itself and does not push twice in a row. So on its own the
    machine costs at most ONE round.
  - **A rejected push does not yet mean the work did not land — ASK THAT FIRST.** The server
    may have taken the ref and died on the response (502, "the remote end hung up unexpectedly"):
    the client honestly prints an error while the commit is on main. The first command after a
    rejection is not the race analysis but `git fetch origin && git merge-base --is-ancestor HEAD
    origin/main`. **Exit 0 — the work is ON MAIN**: the push landed, there is nothing to retry and
    nobody to call — you take this HEAD's sha as evidence and go on to the two confirmations.
    **Exit 1 — the work is not there**, and only then does the race analysis below kick in; this
    branch is not softened by one word — the EXIT CODE decides, not a guess like "an empty range,
    so it probably landed after all". Why the check stands BEFORE the analysis and not inside its
    empty branch: a landed push with a sibling already sitting on top gives a NON-EMPTY range,
    i.e. it looks like honest mechanics — and the next round quietly corrupts the evidence,
    `git rebase origin/main` THROWS AWAY your commit (it is already upstream), HEAD moves onto
    someone else's tip, `git push` prints "Everything up-to-date", and `git rev-parse HEAD` hands
    back the SIBLING's sha, on which both confirming commands honestly pass. Two clarifications,
    both measured: `git fetch` here is load-bearing — on a stale remote-tracking ref the same
    check answers "it did not land" about work that did; and HEAD here is YOUR commit (the chain
    rebased it, a rejected push does not move it), and if `git log -1` shows something other than
    your `(tracker #N)`, you simply did not commit — that is a different trouble, and exit 0 says
    nothing about it.
  - **A round is spent ONLY on a lost race — once you are sure the work did not land, look at WHO
    won.** The check above returned 1 → `git log --oneline HEAD..origin/main`: HEAD is your
    commit on the OLD base, so those are exactly the ones that overtook you. **Empty — there was
    no race at all** (a protected branch, no push rights, a pre-receive hook, the wrong remote):
    the next round will lose in exactly the same way, and it costs a full run of the criteria —
    the ceiling is not spent on that, `call_human`
    IMMEDIATELY, with git's refusal text. **Non-empty — that is mechanics** (the bot's bump, a
    sibling's commit): the main branch honestly moved forward, that is exactly what a rebase
    fixes, the round is yours. Look on EVERY lost round rather than recalling at the end: that
    same list is ready-made evidence for the escalation (below), and it cannot be assembled after
    the fact.
  - **Where the ceiling comes from and why it is `2 × max(wip.limit, wip.active)` and not a
    constant.** The ceiling must be strictly above the worst PURELY MECHANICAL run, otherwise it
    calls a human on arithmetic. With N active tasks, each of the N−1 siblings that manages to
    land during your integration brings its own bump along too: 2·(N−1) rounds, plus the trailing
    bump of the landing that beat your `fetch` — 2·(N−1)+1 in all, and the ceiling = **2 × N**.
    At the default limit of 3
    the worst mechanical run equals 5 and the ceiling is **6** (this repo's measured case); at a
    limit of 1 the ceiling is 2, at 4 it is 8, at 5 it is 10. These are DIFFERENT numbers and must
    not be confused: 5 is what the mechanics can produce, 6 is what you call a human after.
    **N is how many tasks are ACTUALLY in Design/Build (`wip.active`), NOT the limit: rework
    re-enters Build past the `claim` gate, so `wip.active` legitimately exceeds `wip.limit`**
    (measured on this board: 5-7 at a limit of 3 — and VMCP-252 (851) spent all 6 rounds under
    exactly that on pure mechanics, with green gates and not a single rebase conflict, after which
    it went to Your Call with its work finished and pushed). The `max` is there to keep the
    ceiling from DROPPING when there are fewer active tasks than the limit. The numbers are
    set by the PROJECT CONFIG and the current board, not by habit: everyone's `wip_limit` is their
    own, while the rulebook is one for all and
    rewrites itself at MCP server start — a consumer at limit 4 cannot "raise the number
    locally", it can only receive a rule that computes. The orchestrator names both numbers in
    your brief (it sees `wip` in every `next_task` response). **`wip.active` is the BOARD's
    state, and there is nowhere to read it from the way you can read the limit: if it was not
    named, compute from the limit alone**, i.e. by the old `2 × wip.limit`; the error is then only
    in the safe direction — you escalate earlier than you should have. The limit is different:
    **if it was not named, do not guess,
    read it**: `wip_limit` lives in the repo config `.vikunja-mcp.toml` (walk-up from your
    directory), and you DO have it — that key is committed, so the file is laid out into a linked
    worktree too, unlike the gitignored `.vikunja-mcp.env` with the token. No such key in the
    file — the limit is the default, 3; `enforce_single_wip = true` set — the limit is 1. And only
    if no toml was found at all — **take 6**: that is not a guess but the same derivation, because
    `wip_limit` exists ONLY in the toml (never in env), so "no file" also means the default limit,
    and 2 × 3 is exactly 6. The old hard-coded six was a guess and broke from limit 4 on: there
    the worst mechanical run is already 7, i.e. a ceiling of 6 called a human on exactly the
    arithmetic the formula was introduced for. And it is an upper bound, not a tuning
    knob: the earlier "3" was exactly the length of the MOST ORDINARY bad run (neighbour A's bump
    → neighbour B's commit → B's bump), i.e. it called a human precisely when the next round would
    almost certainly have won; and without an auto-release the only rivals are siblings, the worst
    run is half as long, and you simply will not reach the ceiling — there is no point lowering it.
  - **Hit the ceiling — say WHAT kept winning, not "push it for me".** At the default limit the
    mechanics do not produce that many, so the loop is NOT CONVERGING (a conflict that keeps
    resolving into itself; a sibling stuck in its own push cycle; criteria that went flaky under
    rebase). In a wide drain — or when humans push to main as well, which this arithmetic does not
    model — pure mechanics reach the ceiling too. The two cannot be told apart by the NUMBER of
    rounds, but they can by the list of winners, which is why the question to the human IS that
    list: "N rounds in a row, and here is what landed on the main branch each time".
  - **The criteria are run EVERY round — including when all that arrived was the version bump.**
    The temptation is clear: the bump is machine-made and mechanically recognisable (a bot author,
    a subject of the form `chore: v<semver> [skip ci]`, a couple of diff lines). Do not do it —
    and not because "the diff is small", but because: (a) you rebase not onto a COMMIT but onto a
    RANGE, onto everything that arrived since your `fetch`, and at these intervals a bump
    routinely lands in there TOGETHER with a sibling's real commit — that is, the case where the
    relaxation is safe is exactly the case where it saves nothing; (b) "it is only a bump here" is
    a rule YOU execute in prose: get it wrong and it does not fail, it SILENTLY switches the
    guarantee off, and there is nothing left to catch that; (c) "inertness by eye" has already
    failed here — this bump touches not two files, as is commonly believed, but THREE: both
    version files and **the dependency lock**. The cost of an extra round is handled by the
    ceiling above and by the `&&` chain, not by a relaxation in the checking.
  - **A FIGURE claimed as a property of the TREE is measured AFTER the last rebase — right before
    the push, and not when it was convenient to obtain.** The chain above orders the CRITERIA
    re-run after the rebase, and that works. It says nothing about PROSE, and everything else
    slips through that gap: the sweep record in a docstring, the control round's `collected`, the
    "Gates on this tree: … N passed" line in the commit message. They are written BEFORE the
    rebase — and they land describing a tree that is in no history, because the last change to it
    is made not by you but by a SIBLING that landed while you worked. So the rule is not "measure
    carefully" but "measure LAST": at the default limit of 3 two others are working beside you,
    and the release bot arrives after every green landing, so what goes stale is not the edge case
    but the ordinary one.
    The measurement is on VMCP-249 (840): its commit carries "Gates on this tree: uv run pytest
    tests/unit -> 1136 passed" and the sweep record "control 0 failed / 0 errors / 200 collected"
    (both lines present — `git show` on the landed sha), while the independent reviewer's
    re-measurement on the SAME sha gave 1139 passed and 203 collected. Between the measurement and
    the push, a sibling with three tests landed. The same card's `[worklog]` contains the correct
    1139 — that is, the author re-measured for the TRACKER and did not re-measure for the PROSE,
    and that is not one agent's sloppiness but a gap in the prescribed order.
    The sweep's own deltas reproduced exactly and not one pin turned out blind: what breaks is
    precisely the control figure used to check that the round and the control measured ONE
    tree — that is, exactly what the cross-check exists for.
    In practice: as the last action before `git push`, walk your own prose and the commit
    message and re-measure every number claimed as a property of THIS tree. Cheaper still is
    not to write an absolute at all: an assertion of the PROPERTY (an assert) never goes stale.
  - **Sign a historical absolute with the TREE — `N at `<sha>``.** The anchor idiom (a number,
    the word `at`, a sha in backticks) extends to sweep records too: a figure written that way is
    SEEN by `tests/unit/test_measured_figure_anchors.py`, which requires the named commit to exist
    and to be an ancestor of HEAD. Without an anchor it does not see the figure at all —
    `collected 200` is just a number to it. It checks the LABEL, not the value: the record passes
    even when the truth is 203, because what is asked is the tree's resolvability, not the
    arithmetic. And that is enough — a reader who wants to check CAN, because the tree is NAMED.
    The bullet above is not cancelled by the anchor: a figure claimed as a property of YOUR tree
    is still measured after the rebase; the anchor is for one that is historical by construction.
    And an anchor does not live long on a branch: a sha taken before the mandatory rebase is
    orphaned by that rebase, so sign with what will actually land.
    **Do NOT build a gate that DERIVES `collected` itself and compares it against what was
    written.** In CLAUDE.md that shape has already been evaluated by measurement and rejected: it
    is red on arrival and turns a docstring edit in someone else's card into a red suite in a hot
    file; here it costs twice as much, because pytest would have to be run twice.
    **And do NOT retroactively rewrite records that have already landed in other people's cards.**
    Where an anchor exists, it is honest for its own tree; where there is none, the rule applies
    to FUTURE records.
  - **A COMMIT MESSAGE must contain no literal ci-skip marker — not in quotes, not as a
    quotation.** The gotcha this very task stepped on while writing the paragraphs above: CI
    looks for the marker across the WHOLE message text, body and code spans included — so a commit
    that merely QUOTES the release bump's subject cancels its own run. And you will see no
    refusal: the push goes through, git is silent, both sha checks are green, the task looks
    delivered — but there is no run, no auto-release, and the edit never reaches the rollout
    channel, i.e. it does not reach the rulebook's consumers at all. Writing about the release
    commit — name the marker DESCRIPTIVELY ("the ci-skip marker", "that marker in the bump's
    subject"); in a FILE the literal is harmless, it is dangerous only in a commit message. And
    there is more than one spelling: GitHub suppresses the run on a whole FAMILY
    (`[ci skip]`, `[no ci]`, `[skip actions]`, `[actions skip]` — and on the
    `skip-checks: true` trailer), so the rule is about the family, not about the single form
    this repo's bump emits (that is the one you will most likely quote — but the enumeration is
    here so that "I wrote it differently" does not read as "so it is allowed"). And that a run
    for your sha did in the end GET CREATED is checked by the next bullet, with the first of its
    two checks.
  - **BUILD THE COMMIT BODY WITH `git commit -F - <<'MSG'`, NOT with `-m "…"` — otherwise the
    shell eats part of the text silently (#773).** Mechanically it is a sibling of the trap
    above: the push goes through, git says nothing, both sha checks are green, the run is green —
    and the message is not what you wrote. Inside DOUBLE quotes a backtick is command
    substitution, and this repository's idiom is to wrap every identifier in backticks, so the
    more carefully you keep the style, the likelier you step on it. Measured on a live shell,
    four forms:

    ```sh
    git commit -m "keeps `blocked` and `epic` and $HOME"   # EATEN: "keeps  and  and /Users/…"
    git commit -m "keeps \`blocked\`"        # survives — but EVERY one must be escaped, by hand
    git commit -F - <<MSG                    # ALSO substitutes: `echo GONE` really runs
    git commit -F - <<'MSG'                  # correct: verbatim, including $HOME and $(date)
    ```

    **The quotes around `MSG` are load-bearing** — without them the heredoc fixes NOTHING, and
    that is the third line above, not a nitpick. The rule closes the whole class: a backtick,
    `$VAR` and `$(…)` break equally quietly, and the loss is not limited to omission — `$(…)`
    will INSERT foreign output into the message. That is also why "count the backticks after the
    commit" is no good as a check: it requires remembering the text you have just lost, and it
    does not catch insertion at all.
    The incident it was found on is commit `5389be0` of this repository: three words
    (`blocked`, `epic`, `claim`) vanished from the body, the sentence was left incomplete, the
    history was not rewritten (a force-push to main for the sake of a message is not the right
    price).
  - **After the push there are TWO checks, and they are about DIFFERENT things: that the run WAS
    CREATED and that it ended GREEN.** "A run exists" is not "all is well", and that is a
    measured hole, not a worry: on the night of 31.07 seven of fifteen consecutive runs on the
    main branch ended RED (identically in all seven: `lint-and-unit` success, `integration`
    failure, `release` **skipped**), each time the agent honestly reported "a run exists" — and
    each time the landing never reached the rollout channel. Seven is a LOWER bound, not a total:
    the measurement window ended on its own last red, and that same night there was at least one
    more that fell outside it (`d6195e1`, the same three jobs). The checks are separated not for
    symmetry: their DEADLINES differ, because a run is asynchronous. The commands and job names
    below are THIS repository's (GitHub Actions, `gh`), because that is where they were measured;
    in a project with a different CI those change, but the split into two checks does not, nor the
    order "`status` before `conclusion`", nor the fact that an unfinished run is "unknown" and not
    "green".
    - **EXISTENCE — right after the push.** This is the defence against a swallowed ci-skip
      marker (the bullet above), and it asks not about duration but about a fact: the run was
      either created or it never will be.
      `gh run list --commit "$(git rev-parse HEAD)" --json databaseId,status,conclusion`.
      **The sha here must be the FULL 40-character one:** measured — with an abbreviated one the
      same command returns an empty list `[]` and exit code 0, i.e. it looks exactly like "there
      is no run" and raises a false alarm about the marker. Empty on the FULL sha — that is an
      alarm; but if only seconds have passed since the push, ask a second time a little later
      before raising it: exactly how long it takes from the push being accepted to the run being
      created is NOT measured here, and a false alarm about the marker costs a human a round.
      **And even empty on the full sha is NOT yet the marker: a run is created for the push's
      TIP, not for every commit in it.** If your commit arrived non-tip (one push carried more
      than one), it will have NO run and no check-suite AT ALL — while the work did land. ONE
      step tells them apart: `git log --oneline <your FULL sha>..origin/main`, and if there is a
      commit above whose `gh run list --commit <its FULL sha>` returns a run, the marker has
      nothing to do with it. Measured on this repo: `bc960b2` has zero runs and `check-suites`
      `total_count: 0`, not one spelling of the marker in its message, and yet it is an ancestor
      of `stable`, while its descendant `b6c7502` carries a green run 31086601577; 1 of 21 task
      commits in the last 40 landings arrived that way (~5 %). Raise the alarm only when nothing
      is above OR the descendant has no run either. But even in the "good" outcome one thing
      stays true, and it must be said in the report: nobody ran the tree AT your commit — what
      was green was the neighbour's combined thread.
    - **THE OUTCOME — ONE look, as the LAST action of the turn.** Both obvious forms are wrong:
      "wait for green" blocks you for minutes and dies together with a killed turn, "ask right
      after the push" almost always lands in an in-flight run. So ask LATER, but by ORDER rather
      than by waiting: first `advance(to='review')`, the report and `--release`, and only then a
      single `gh run view <id> --json status,conclusion,jobs`. Measured over 40 runs of
      this repo, each on its FIRST attempt (two were later re-run by hand, and a re-run's
      `updatedAt` carries a HUMAN's delay — 31 min and 3 h 26 min — which is not about CI; the
      runner queue itself is far more modest: 0 s on 35 of 38 runs, 80 s at most): from appearing
      to concluding is 42–120 s, median 60 s. And the bias is in your favour but is NOT a
      separation: red runs 42–55 s (median 46), green runs 53–120 s (median 65) — the bands
      OVERLAP at 53–55 s, so duration alone cannot tell a fast green from a slow red. The bias's
      mechanics are measured per job and they are NOT "integration fails early": `integration` is
      never the critical path at all (16–29 s against `lint-and-unit`'s 38–46 s), the run's length
      is set by `lint-and-unit`, and a GREEN run additionally runs `release` (8–15 s), which a red
      one SKIPS. Hence the conclusion: by the end of the turn the answer is usually already there,
      and slightly more often in exactly the case the check exists for. **And know WHERE the
      answer will go: `advance` is already behind you, it will not make it into the
      `worklog`.** Write it as a separate `comment` on the card — that tool gates neither stage
      nor ownership, so a card in Review will accept it — and into your summary for the
      orchestrator. Take the run's id from the first check, and run the command from the MAIN
      checkout: by this point `--release` has already removed your worktree.
    - **Branch on `status`, NOT on `conclusion`.** `conclusion` is meaningful ONLY at
      `status == "completed"`. An in-flight run was caught live, here it is verbatim:
      `{"conclusion":"","databaseId":30636770459,"status":"in_progress"}` — the verdict is the
      EMPTY STRING, not `null`, so a jq fallback `.conclusion // "unknown"` does NOT fire here
      either (it catches only `null`). So "`conclusion` is not `success` ⇒ not green"
      is a broken check: it reads an in-flight run as red and teaches you to distrust your own
      alarm.
      * `completed` + `success` — say exactly that in the report.
      * `completed` + `failure` — **this is the hole; do not swallow it.** Name the run's
        id/url in a comment and WHICH job failed (`jobs` in the same response); a
        `release: skipped` beside it is the visible sign that the rollout channel did not move.
        A red `lint-and-unit` is YOUR commit, and the main branch is broken for everyone: it
        runs the same `ruff`/`pytest` you already ran, PLUS `uv sync --locked` — a check your
        criteria do not contain at all (`uv run` syncs WITHOUT `--locked`), so a lock that has
        drifted goes red only there. A red `integration` alone is the environment-failure class.
        In both cases there is a cheap action available to you without a human:
        `gh run rerun <id> --failed`. It moves nothing on the board and costs you no time — but
        it is NOT a diagnosis: measured on this very card, re-running a red run gave red again,
        and only the next one came out green. And it OVERWRITES the same run's `conclusion`
        rather than creating a new one: `8b4bfa5`, one of those seven reds, reads as `success`
        today. So "the run is green" is an answer about NOW, not evidence that it was green
        straight away. If you re-ran it, say so, and say that you did not check ITS outcome.
      * not `completed` — that is **UNKNOWN**, neither "green" nor "red". Do not wait, do not
        guess and do not write "the run is fine": name the run's id in a comment and say outright
        that you did not wait for the outcome. This branch is finished off by the reviewer — see
        "Independent review of changes": it is late BY CONSTRUCTION, and here that is a virtue,
        not a flaw.
    - **Know exactly how urgent this is, so as neither to panic nor to relax.** A red run
      does not lose the work forever: the next GREEN landing moves the rollout channel along with
      your commit (checked: the red `8fc53f8` is an ancestor of the current `stable`), and that
      night catching up took between 1 and 48 minutes. What is expensive is something else — the
      session's LAST landing: there will be no green after it, and the channel stands until the
      next session. Nobody knows in advance which landing will be the last — which is why EVERYONE
      looks.
  - **The push is mandatory.** The independent reviewer is a separate session/identity, it
    pulls the fix from the remote; without a push there is nothing for the review to look at.
  - **Check-point early.** Take the task's CORE all the way to commit+push and
    `advance(to='review')` BEFORE taking on optional extra work (polish, nice-to-haves). If the
    turn is killed
    during the extra work, the task is already safely in Review and pushed, not abandoned
    in Build with an uncommitted diff. Symmetric to the reviewer's "record the verdict at once".
    **In your own worktree the rule narrows** (otherwise it argues with "release the worktree"
    below): from the moment of `advance(to='review')` the task has left Build, so as far as
    `--gc` is concerned this worktree is already DEAD and the orchestrator may sweep it on any
    tick. Nothing will be lost (only what is clean and pushed is swept — the work is already on
    the main branch), but the directory may vanish from under you in the middle of the extra
    work. So: do extra work that needs THIS directory BEFORE `advance`; if you took it on
    afterwards, commit and push it by the same recipe, leave `--release` as the VERY last action
    and do not be surprised if the worktree has already been removed.
  - **One task = one commit.** Do not mix in other people's edits: `git add`
    only this task's files (a shared file — by hunks, not whole).
  - **Worked in your own worktree — release it after `advance(to='review')`:**
    `vikunja-mcp workspace --release <id>` (fine from inside that worktree — the CLI works from
    the main checkout itself). Success is `{"released": true, ...}`, and from that moment your
    directory IS GONE: do everything remaining (the report, any commands) from the main checkout.
    **TWO subtleties of SUCCESS, and they are the same two fields read in `--gc`'s `released`
    list.** (1) `branch_deleted: false` — the directory is gone but the `task/<id>` branch remains
    (`git branch -D` failed; the `warning` carries the reason and the command that cleans it up).
    The work is not lost and the next `workspace <id>` will reattach to that branch — but finish
    the branch off, otherwise they pile up silently.
    (2) `removed_ignored: [paths]` — ignored files were DESTROYED along with the worktree, files
    the `dirty` guard does not see at all (`git status --porcelain` does not show ignored ones).
    It is a post-mortem list, not a warning: there is nothing to get back. What lands here is
    exactly what this same file's browser recipes prescribe — `shot-<id>.png` in your worktree and
    `--output-dir .playwright-mcp/<id>`; reproducible junk (`.venv/`, `__pycache__/`, tool caches,
    `*.pyc`) is NOT included in the list, so the field being present = something unidentified was
    lost. Name the files in the report. **To stay out of it: everything you need AFTER the task,
    carry out of the worktree BEFORE `advance(to='review')`** — the screenshot via `attach_file`,
    notes as a comment in the tracker (see "Check-point early": after `advance` the worktree is
    already dead).
    **`released: false` is NOT an error, and what to read is the field, not the exit code:**
    the code is 0 either way, and what happened is told by `code` (the machine-readable key) and
    `reason` (the human text), and the reaction must DIFFER:
    - `code: "dirty"` (`"working tree is dirty (…)"`) or `code: "unpushed"`
      (`"N commit(s) not on origin/…"`) — PROTECTION: uncommitted
      or unpushed work is left. Work out WHAT is left, take it through to a push and retry. Do
      not remove the worktree by hand (`rm -rf`, `git worktree remove --force`) — that is how
      work is lost.
    - `code: "detached-build"` — your worktree is NOT ON the `task/<id>` branch, almost always
      because of an interrupted `git rebase origin/main`. The work is not lost (the commits are
      on the branch), but it can be neither released nor worked in until the rebase is played
      out: run `git rebase --continue` (play it out) or `git rebase --abort` (return to the
      branch, losing what was replayed) in THAT worktree — the exact commands with the path are
      in `reason` — and retry. The choice is yours: the tool does not make it, because `--abort`
      throws work away.
    - `code: "locked"` — the worktree was locked by a human (`git worktree lock`), and git will
      not let a locked worktree be removed. This is NOT a tool failure and NOT a loss: the work
      is in place, nothing was deleted, and the lock is an explicit human "hands off". Do NOT
      unlock it yourself and do not apply `remove -f -f`: whoever set the lock removes it
      (`git worktree unlock <path>` — the exact command is in `reason`). The tool deleted nothing
      and lost nothing, but the directory's existence does NOT follow from this: the same code
      also comes back for a locked entry whose directory has already been carried off by hand
      (`prune` does not drop it). Take the path from `path`, and name the lock and the path in
      your summary to the orchestrator — from there it is for the human.
    - `code: "populated-gitlink"` — the worktree holds a gitlink (a submodule) whose directory is
      NOT EMPTY, and removing such a worktree means destroying its contents silently. This is
      PROTECTION, like `dirty`, but the cause is different and you need to know it: `git status`
      says NOTHING AT ALL about paths under a gitlink, so the `dirty` guard is blind there — and
      blind not only to ignored files but to ANY content, including ordinary
      untracked-and-NOT-ignored content that in any other directory of the worktree it would have
      seen and held the worktree for. Such a worktree used to be removed with exit code 0, without
      `--force` and without a single field in the report (measured on a real submodule). Nothing
      was deleted and nothing was lost. The cure is yours, and it is not "commit it": a commit
      does not empty the submodule's directory. Take what you need out of it (the paths are in
      `reason`), empty the directory and retry `--release`. The pipeline NEVER populates
      submodules (neither `git submodule` nor `--recurse-submodules`), so a non-empty directory
      means that YOU or your subagent put something there.
    - `code: "no-worktree"` (`"no worktree for this task"`) — there simply is no worktree: you
      already released it, or
      `--gc` picked it up (see "Check-point early" — after `advance` it may). There is NOTHING to
      do, this is success after the fact; repeating the call is pointless.
  - This deliberately overrides the harness default "commit only when explicitly
    asked": in this flow a finished task commits and pushes itself.
    The tag and moving `stable` are NOT part of this — that is a separate release task.
## A second independent pass over YOUR OWN text

A rule about PROSE, not about code. No later than hand-off, and much earlier if you can — the
implementer before `advance(to='review')`, the reviewer before `review_task` — raise a SEPARATE
agent, give it the RAW measurements and your text, and ask it one thing: "which claim here is
wider than its evidence?". Self-checking does NOT catch everything, and that is measured from both
sides: on 582 the author caught SIX overstatements in his own new text himself, and the second pass
found FIVE MORE — including one the first pass had already marked as verified. On VMCP-111 (582),
VMCP-119 (594) and VMCP-124 (603) it fired for BOTH roles — for the author and for the reviewer
alike. The price of not having the rule is in the same place: review rounds in which the code stood
unchanged or was found correct on the first try, and what spun was the wordings alone.

- **When it is mandatory: when the prose IS the deliverable.** The marker is not size but that the
  text carries measurable claims a reader will act on: docstrings and comments in code, rules (this
  file), the `worklog` report, the `[review]` report. A hint is the share of prose in the diff:
  "53 insertions, ~45 of them prose" on 594 was exactly the grounds for saying the prose there is
  the deliverable and not decoration. On a one-line edit, on pure code with no new claims, and
  where the text merely accompanies the work — do not raise one, it is wasted spend.
- **Whom you raise, and with what.** A separate subagent with a FRESH context (senior: auditing
  a claim means RE-DERIVING a measurement, which is the fourth test in "Who does the work", so
  the one-rung downgrade never reaches this dispatch). Give it
  the raw material: run logs, commands and their output, shas, card comments — and the text
  itself. Do NOT give it your own conclusions about what is already verified: the memory "I
  measured this myself" is precisely what it must not have. It works exactly because it opens
  the file and the history instead of remembering.
- **WHERE it works — in its OWN clone, not in your tree.** A READING auditor (open the file, the
  history, `git log -S`) is fine with your tree — the bullet above describes exactly that one. But
  the moment you ask it to RE-MEASURE, the assignment becomes a WRITING one: a claim of the form "X
  is what catches Y" is re-measured, by this repo's rules, by deleting X and requiring the test to
  go RED — that is, the auditor mutates exactly the sources you are running your own rounds over in
  that same minute. And the path you have to hand is exactly one — your working tree; hand it over
  in the brief and there are TWO WRITERS in one directory. The collision was caught live on
  VMCP-160 (667) and reproduced on a constructed stand (two processes, one tree, both mutating
  `SKILL.md`). There are TWO axes, and they must not be confused:
  - **a foreign MUTANT under your round — LOUD**: a round that alone gave `control 0 failed` gives
    `1 failed`, and the failure text names a clause you never touched. It lied, but loudly. **And
    even that is not a guarantee**: measured, with NON-OVERLAPPING selections (a one-test pin each)
    the same control round is green — `0 failed` — and catches NOTHING; it went red only when the
    selection was the whole file. So the noise is audible only if the foreign mutation landed
    inside YOUR selection;
  - **a foreign RESTORE under your round — SILENT, and that is what the rule exists for**: your
    mutant is rolled back without a word, a round that alone gave `1 failed` gives `0 failed`, and
    you write down "the pin is BLIND to this mutation". Exactly the false conclusion the second
    pass is set up to prevent, and it is INDISTINGUISHABLE from an honest green.

  **The victim here is NOT tied to a role.** Both of you restore, so the silent axis lands on the
  auditor (its mutant wiped your restore) and on YOU (your mutant wiped its restore) — the second
  is worse, because your numbers ride into the commit. What is dangerous is not WHOSE restore it
  is, but ANY foreign restore under anyone's round.

  Your own restore check does not help here: EVERY script's sha256 comparison reported success, and
  `git status` showed exactly what it showed before the round. A script sees only ITS OWN writes —
  it certifies a tree it did not own alone. This card's second pass got a worse outcome still on
  its own stand: after BOTH scripts reported a successful restore, the file stayed mutated FOREVER,
  while `git status` was indistinguishable from honest uncommitted work — that is, such a mutation
  can be committed and not noticed. And the control saves you only halfway: it catches the loud
  axis (on 667 that is exactly how it was found), the silent one by construction it does not, there
  everything is green.
- **How exactly.** A clone, `uv sync`, and `vikunja_mcp.__file__` in EVERY round:

  The author/auditor boundary is drawn by the MARKER LINE `# --- the auditor's brief starts here ---` in
  the fence itself: everything ABOVE it is yours (the auditor has neither your path nor your
  uncommitted work), everything BELOW is its. Do not count lines and do not name the boundary by a
  number: any edit to the recipe shifts the number, while the marker moves with it. Hand the clone
  path to the auditor in the brief as its working directory: it will not guess it by itself.

  ```sh
  SP=<scratchpad>; ID=702                    # id of YOUR task: the scratchpad is ONE per session
  TREE=<your tree>; CLONE=$SP/$ID-pass2-audit     # role suffix: one card can have SEVERAL
  P=$SP/$ID-wip.patch                             # passes (the author's and the reviewer's)
  git clone --no-hardlinks "$TREE" "$CLONE"
  git -C "$TREE" diff HEAD --binary > "$P"        # TRACKED. --binary is mandatory
  [ ! -s "$P" ] || git -C "$CLONE" apply "$P"     # guard: an empty patch kills apply (exit 128)
  git -C "$TREE" ls-files --others --exclude-standard | sort > "$SP/$ID-untracked-tree.list"
  while IFS= read -r f; do                        # UNTRACKED: the patch does NOT carry it
    mkdir -p "$CLONE/$(dirname "$f")" && cp "$TREE/$f" "$CLONE/$f"
  done < "$SP/$ID-untracked-tree.list"
  git -C "$CLONE" ls-files --others --exclude-standard | sort > "$SP/$ID-untracked-clone.list"
  diff "$SP/$ID-untracked-tree.list" "$SP/$ID-untracked-clone.list"   # empty = it arrived
  # --- the auditor's brief starts here, with $CLONE substituted ---
  cd "$CLONE" && uv sync
  find "$CLONE" -name __pycache__ -type d -prune -exec rm -rf {} +   # root is the CLONE, not $SP
  export PYTHONDONTWRITEBYTECODE=1
  uv run python -c 'import vikunja_mcp; print(vikunja_mcp.__file__)'   # print it every round
  ```

  Removing the clone (`rm -rf "$CLONE"` — with its own `.venv` it weighs on the order of a hundred
  megabytes, and the scratchpad is shared) is the AUTHOR's job and comes AFTER hand-off, which is
  why that line is not in the fence: gluing the fence together with `&&`, as "Commit+push" teaches,
  you would have wiped the clone right after the very first `__file__` print, before the first
  round.

  The root of `find` is given EXPLICITLY rather than as a dot, deliberately: an agent's turn does
  not preserve `cd` between calls, so `find .` means "wherever I end up", and in the worst case
  that is precisely the scratchpad, which is exactly what the bullet below forbids.

  The steps; none of them cancels the others, and each is a measurement or a direct consequence of
  one:
  - **The clone carries what is COMMITTED, and that has to be topped up TWICE — with a patch and
    with a copy.** `git clone` copies the REPOSITORY, not the working directory: uncommitted work
    is NOT in the clone at all (verified by comparing a fresh clone against the tree). The auditor
    will run perfectly normally and come back with the finding "the rule you are writing about is
    not in the file" — true for what it saw and false in fact, that is, exactly the class of error
    the second pass exists for. The patch closes exactly ONE half — the TRACKED files — and it has
    two gotchas, both measured:
    - **an empty patch is NOT a no-op.** On a clean tree `git diff HEAD` gives a 0-byte file, and
      `git apply` on it gives `error: No valid patches in input`, **exit 128** (git 2.50.1; the
      same on a patch of one newline). That is the DEFAULT case, not the edge: for a REVIEWER
      auditing an already-landed commit the tree is ALWAYS clean. And recipes here are glued with
      `&&` — so without the guard the recipe stops at this step and never reaches `uv sync` at all.
      The guard is exactly `[ ! -s "$P" ] || …`, and NOT `[ -s "$P" ] && …`: on an empty patch the
      second returns 1 by itself and breaks the chain in exactly the same way (measured — both
      forms);
    - **`--binary` is mandatory.** Any STAGED binary without it yields `Binary files … differ` with
      no index line, and `git apply` drops the WHOLE patch (exit 1, NOTHING is applied) — that is,
      one such file silently cancels the entire step, text edits included. In THIS repo a
      screenshot will not get in here by itself (`*.png` is ignored — `git check-ignore`), an
      explicit `git add -f` is needed; but `--binary` is there precisely because the cost of the
      mistake is the whole patch, not that one file.
  - **The second half is the UNTRACKED files, and the patch does not carry them AT ALL.** A new
    test module is an ordinary state of a task in this repo, and `git diff HEAD` does not see it:
    measured — `git apply` returned 0, the edit to the tracked file arrived, and
    `tests/unit/test_new_pin.py` was ABSENT from the clone. That is why the recipe has a copy
    driven by `ls-files --others --exclude-standard`. It inherits your `.gitignore` and moves only
    what is untracked-and-NOT-ignored: in this repo `.venv/`, `__pycache__/` and `.playwright-mcp/`
    are ignored (verified with `git check-ignore`), so `.venv` does NOT move and the copy does not
    degenerate into the `cp -R` of the bullet below. In a repo where the venv is not ignored it
    will degenerate; the same command without `| sort` prints exactly what will move, look at it
    BEFORE copying. Names with a newline inside will not survive the loop — there are none here.
  - **Check the arrival with ANYTHING you like, only not with `git diff` on both sides.** That
    check is CIRCULAR and agrees precisely when the file is lost: what is untracked is invisible to
    `git diff` on BOTH sides. Measured on the same stand — the md5s of the tree's and the clone's
    diffs MATCHED, while the new test module was not in the clone at all. So the recipe compares
    the `ls-files --others` LISTS (an empty `diff` = it arrived), and `git status --porcelain`
    shows the difference straight away: in the tree ` M SKILL.md` + `?? test_new_pin.py`, in the
    clone only ` M SKILL.md`. This comparison has an honest boundary of its own: it is about NAMES,
    not BYTES — measured, two directories with the same names and different contents give an empty
    `diff` of the lists, so a `cp` that broke off will report "it arrived". In doubt, compare the
    md5 of the file that IS the subject of the audit.
  - **`git clone --no-hardlinks`, not `cp -R`.** git does not track `.venv`, so it does not reach
    the clone AT ALL (verified on a fresh clone) and `uv sync` builds it anew — there is nothing to
    inherit. `cp -R` DRAGS it along, and inside sits the editable install's `.pth` with an ABSOLUTE
    path to the ORIGINAL `src`. It does not bite every time, and what decides is the RUNNER: a bare
    `<copy>/.venv/bin/python` reads the stale `.pth` and imports the ORIGINAL `src` — a mutation
    applied in the copy does not reach the interpreter at all (measured: a marker appended to the
    copy is not visible, and `__file__` points into the original), and that is exactly the four
    greens in a row from VMCP-148 (646); `uv run` in that same copy re-syncs the venv, rewrites the
    `.pth` to the copy, and the mutation is visible. So `cp -R` is not "always broken" but "every
    other time, depending on what you launched it with", which is worse: silent and irreproducible.
    This is the same mechanism CLAUDE.md uses to explain the four greens in a row on VMCP-148
    (646); which runner those rounds went through was not verified here.
  - **Print `vikunja_mcp.__file__` every round — and with the SAME runner you run the rounds
    with.** It is the cheapest check of the previous point, but not the only one
    (`find_spec().origin`, `__path__`, `inspect.getsourcefile` give the same answer) and not
    runner-independent: under `uv run` it FIXES the copy rather than catching the breakage — the
    re-sync rewrites the `.pth` at the very moment of printing. If the runners diverge, the print
    speaks about one interpreter while the rounds go in another. The control round does not catch
    this and does not claim to.
  - **Delete `__pycache__` BEFORE, and `PYTHONDONTWRITEBYTECODE=1` does NOT replace that.** The
    variable forbids WRITING bytecode, not READING it. Measured from the `.pyc` header: validity is
    the pair (source mtime in SECONDS, size), so an edit of the same LENGTH whose mtime did not
    manage to cross a WHOLE second (a fast scripted sweep falls into that window) leaves the cache
    valid — and under that variable the round read the OLD value; the new one appeared only after
    deleting `__pycache__`. Do both, in that order.
  - **Run `find` over YOUR OWN clone, not over the scratchpad.** The clone exists precisely so that
    the pre-round cleanup is NARROW: the scratchpad is ONE per session, and a recursive
    `find <scratchpad> -name __pycache__ -type d -prune -exec rm -rf {} +` wipes the caches of LIVE
    neighbours in the middle of their runs — the orders of magnitude are in "The directory for temporary files is ONE per SESSION" ("What DOES collide"), so that the measurement lives in one place.

  **A clone is enough for FILES — and only for them.** The same two scenarios, separated into two
  clones, gave the right numbers on both sides (`control 0 failed` for the author, `1 failed` for
  the auditor), so the sweep itself needs no changing; on 667 the sweep was re-run in a clone too.
  But a clone is the same thing as a worktree in substance, and it separates NOTHING of "What DOES
  collide": the container name, the port, the shared scratchpad are still one apiece between you
  and the auditor. If you ask it to check something integration-shaped, derive the names from the
  id, exactly as written there. And the rule is RECURSIVE: if the auditor raises its own subagent,
  that one needs ANOTHER clone; two writers in one directory are bad regardless of who is whose
  parent.
- **Three classes where self-checking alone is not enough.** All three are measured on these cards:
  - **INHERITED** — a number or a fact from the card, the brief, someone else's report or a
    previous rejection. The author remembers what he measured himself and does not remember what he
    took on trust. On 582 the implementer inherited FROM THE TEXT OF THE REJECTION ITSELF the
    pointer "these numbers are written in the `[review]` comment of the neighbouring card" — he
    opened the comment, they are not there. On 603 "20 rows across four full pages" was presented
    as the shape a live endpoint returned; the live one returned 22 as 5,5,5,5,2 — the last page
    NOT full, that is, the case costs one request more.
  - **ATTRIBUTION** — "X says", "this comment used to read", "card N measured". On 582 not one of
    the FOUR rejections (counted from the card's comments: four `[review] NEEDS WORK`, the fifth
    verdict an APPROVE) was about a wrong MEASUREMENT — all four were about the SENTENCES about
    measurements; the reviewer summed that up as five defects, four of them attribution (the
    register of retractions in the file itself is longer — eleven lines, non-blocking ones went in
    there too). The presence of a number in the tree is not its provenance — and what caught that
    was not the reviewer himself but his own second pass: the reviewer marked a claim "verified
    TRUE" after making sure the numbers ARE in the tree and without looking at who put them there;
    he withdrew his own point himself, but already on someone else's finding.
  - **EDITS TO ALREADY-VERIFIED TEXT** — fixing the previous round breeds new overstatements. On
    603 a round rewrote into a falsehood the very claim the previous review had measured as TRUE,
    and did not re-run it; on 582 a new false universal took hold in the paragraph warning against
    universals. So the second pass looks at ALL the changed text, not only at the places the
    reviewer pointed to.
- **Its findings are CANDIDATES, not a verdict: the owner of the work judges.** The second pass has
  a characteristic error of its own: it measures the absence of a MARKER — a label, a name, a
  wording — and takes it for the absence of a FACT. Measured on 582: the auditor grepped for two
  NAMES, found them only in a phrase that hands both to a neighbouring card, and issued a BLOCKING
  finding "the pointer to this note is inaccurate"; the reviewer opened the note itself — both the
  result and a measured accounting of the costs were lying there. Part of the observation was true
  (the TABLE itself really was not in the note, a neighbour created it), what was wrong was the
  CONCLUSION. This is the same class as "the presence of a number is not its provenance". For every
  candidate open the SOURCE rather than trusting the auditor's report — and note that the two roles
  judge differently: on 594 each had its own pass, and of three flags the reviewer accepted TWO (as
  non-blocking leftovers, re-measuring each) and rejected one — the neighbouring paragraph of the
  same docstring already answers it — while the implementer accepted all five and also re-measured
  each himself. To accept wholesale is to replace one unverifiable certainty with another.
- **Launch it EARLY and IN PARALLEL — it reads the raw material, not the finished text.** Which
  means it can start as soon as the measurements exist, and its findings will make it into both the
  text and the verdict. Launched late, it arrives AFTER the decision: on 594 the most valuable
  thing — that the design is right for a STRONGER reason than the one written down — arrived as a
  separate comment after the verdict, and changed not a wording but the rework task itself. **But
  that does NOT cancel "record the verdict IMMEDIATELY":** if it has not come back by the moment
  you are sure, set the verdict and append the findings as a separate `comment`, putting the marker
  (`[review]`/`[worklog]`) in the text itself: it has no stage or ownership gates, it works from
  Review and after the verdict alike, and a second `review_task` is not needed for that. That is
  how the post-verdict notes on 582, 594 and 603 are written: the tool's verdict ALWAYS comes on
  the first line — `[review] APPROVE` or `[review] NEEDS WORK` — and these do not have it, so they
  were appended with `comment`. The converse does not hold: a `[review]` comment without that line
  is not necessarily a post-verdict note (on 582 a scope-note stands that way, arrived from a
  neighbouring card even BEFORE the work).
- **A stopping criterion is mandatory, otherwise the procedure does not converge.** Each round
  breeds roughly one new false sentence (an estimate from 582's APPROVE verdict, weighed there
  against the CARD's six rounds; its own register of retractions gives more), so the next round can
  cost more than it buys. Stop when the remaining findings (a) are not attribution, (b) change not
  one decision of the reader's and (c) are already covered by the neighbouring text; write those
  into the verdict text or into a comment instead of opening a new round with them. That is how 582
  closed (three clarifications written into the APPROVE instead of one more round) and 603 (an
  over-generalisation in one caveat recorded as a post-verdict note; that audit's report was titled
  "nothing in the new prose is FALSE", and the reviewer recorded the finding rather than acting on
  it). **Once you have stopped, decide one more thing — WHERE the finding goes: it does not change
  the reader's actions, so it is a `comment` and not a new card** (see "The THRESHOLD for filing"
  in the "Decomposition and filing findings" section; here it is conjunct (b) of three, while there
  the same question stands ALONE). This pass does NOT cancel or shorten that threshold — it is
  about where its findings are put, not about whether to call it.
- **The attribution tool is `git log -S` on the exact phrase, and it has three gotchas.** It is
  CASE-SENSITIVE: re-measured in this repo over `tests/unit/test_api_kanban.py` — the phrase
  `serves at most what /info states` gives 2 commits, the same one with `AT MOST` gives 4, and
  `--regexp-ignore-case` gives the union of 5 (control with a non-existent string: 0 lines). Once
  this already hid a real match and dated a claim to the wrong round. The second: a `git log -S`
  command WRITTEN INTO the file it interrogates changes its own answer — two of those four hits are
  precisely the commits that added the quotation. So write "the phrase APPEARED in", not "returns
  exactly one". The third is from a neighbouring row but lands exactly here: in zsh
  `git show $rev:path` parses as a parameter modifier rather than as a revision, so per-revision
  counters SILENTLY read as zeros; quote it — `git show "${rev}:path"`.

## Work that belongs to ANOTHER repo (`handoff` / `transfer_task`)

A project may be configured with SIBLINGS — neighbouring tracker projects, each with its own
repo and its own agent loop. You learn they exist from `next_task`: the response carries
`siblings`, e.g. `{"backend": 17}`. It is EMPTY for most projects, and then this whole section
does not apply. You cannot read the repo's toml, so if a neighbour is not in that mapping, it
does not exist as far as you are concerned — do not guess a project id.

Three tools touch another board, and picking the wrong one loses work. Ask what is true:

- **My card cannot continue until someone else builds something** -> `handoff(task_id,
  to="backend", title=...)`. It files that work in THEIR Backlog, links your card as blocked-by
  it, and puts your card back in Queue unassigned. Your WIP slot frees.
  **Then STOP working this card and take the next one.** Neither you nor a human has to move it
  back: it is offered again automatically once the filed card reaches Review.
  The `title` is what THEIR human triages — write what they need to build, not what you were doing.
- **This card is simply on the wrong board** -> `transfer_task(task_id, to="backend", reason=...)`.
  The card itself moves, comment history and all; nothing stays behind.
  **Its ref CHANGES** — the target re-indexes it on arrival (a card landing in a project that
  already holds `BACK-2` comes out as `BACK-3`), so refs quoted in earlier comments, worklogs and
  commit messages now name nothing. Quote `moved.ref` from then on, and do NOT rewrite refs in
  comments already written — say the card moved instead.
- **I found a bug that is theirs, but my own card is unaffected** -> `file_task(project_id=17)`,
  exactly as for a finding on your own board. Nothing of yours pauses.

Non-negotiable, and it is the same rule in all three: a card you send lands in the neighbour's
**Backlog**, never their Queue. Their human triages their own board — an agent from another repo
does not get to hand their fleet ready-to-claim work. The tools enforce it; do not look for a way
around it. And a `handoff` is not a way to shed a card you simply find hard: it says "this needs a
different REPO", and if the work is yours but too big, that is `decompose`.

The `[handoff]` and `[moved]` comment markers record both sides, so a human reading either board
can see where a card came from and why it paused.

## Decomposition, review and dead ends — in the reference files

Three phases, each of them needed by not everyone and not always, so they are split out wholesale.
Open the one you have run into:

- **`references/decompose.md`** — when the task does not fit into one go or you found something
  outside its bounds. Non-negotiable: a finding outside the task is a `file_task`, not an expansion
  of your own; do not silently expand your own task.
- **`references/review.md`** — when YOU are the REVIEWER. Non-negotiable: an `approve` verdict sets
  the `reviewed` label, which the human reads before Done, so it is not a formality; `needs_work`
  returns the card to Build. You do not review your own work FROM THE AUTHOR'S CONTEXT — in a solo
  setup a card in Review is yours by definition, and a sibling with a fresh context reviews it, not
  whoever wrote the code.
- **`references/stuck.md`** — when you cannot move any further, and what happens AFTER Review.
  Non-negotiable: the way out depends on the ROLE, and `call_human` (a card) is not a console
  question; nobody among the agents moves a task to Done, that transition is human-only.
