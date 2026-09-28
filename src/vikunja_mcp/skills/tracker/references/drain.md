# The parallel drain: slots, `exclude`, returns, `workspace` refusals

> **A reference for SKILL.md, not rules of its own.** Read it **when you land a task (the commit+push recipe, at ANY limit) or run several agents
> at once (`wip.limit > 1`)**.
> What is binding lives in SKILL.md itself — what is here is the shapes of the replies,
> the measured gotchas and the reasons a rule is written exactly the way it is.

- **Free slots GET FILLED; overlap is caught at integration, not predicted from the board.**
  While `wip.free > 0` and `next_task` hands back a task — claim and dispatch, up to the limit.
  Holding a slot back because two cards LOOK like they touch the same module ("I'll serialise
  them to be safe") is FORBIDDEN: that substitutes your guess for the project's mechanism. The
  scheme does not even try to predict an undeclared file overlap in advance — it DETECTS it at
  the moment of integration: every per-task agent runs `git fetch origin && git rebase
  origin/main` before pushing, re-runs the acceptance criteria AFRESH on the rebased tree and
  only then pushes (`git push origin HEAD:main`; rejected — it first looks at WHO won the race,
  then takes another round, up to `2 × max(wip.limit, wip.active)`), resolves the conflict
  itself, and when that fails — `call_human` (see "Commit+push is part of the transition to Review").
  The price of a real overlap is one rebase for a sibling; the price of "I'd rather serialise"
  is idle slots on EVERY task, always. The only legitimate narrowing of the drain is a technical
  one: `workspace` could not create a tree (see "It didn't start — do NOT drop the loop"),
  because two agents in one directory is something we never keep. A hunch that "these tasks look
  like they'll overlap" is NOT such a ground.
- **And the main thing: you do NOT SEE the queue — don't reason as if you do.** `next_task` hands
  back EXACTLY ONE card per call and never a listing of the free queue: how many tasks are in it
  and what they are is unknown from its reply (the one list there is, `waiting` under
  `starving:true`, enumerates precisely the UNclaimable, gated tasks). `exclude` is not a queue
  filter either: it strikes a card out of the "your active task / partial claim / review
  offering" branches and does not narrow the free queue at all — it is not needed there, because
  free means unassigned, and what you exclude is what you already hold, i.e. what is assigned to
  you. So "I looked at the queue and decided these tasks conflict" is an illusion: there can be
  more cards invisible to you than visible ones, and among them independent ones that your guess
  simply never started. And do not take the cards you filed yourself (`decompose`,
  `file_task(queue=True)`) for the queue: that is only YOUR contribution to it — beside them
  lies what the human and other agents put there.
- **Declared dependencies are a different matter, and the tools hold them, not you.** Everything
  above is ONLY about UNdeclared overlap. `follows`/`blocked` are gated hard and without your
  involvement: `claim` refuses, and `next_task` does not even offer such a card until the
  predecessor has reached Review. And saying "these run strictly in order" is the FILER's right,
  through `decompose(ordered=True)` (see "Decomposition and filing findings"), not the pump's by
  holding a slot back.
- **YOU maintain `exclude`, and only within the tick.** The tracker does not know whether your
  subagent is alive — that is a fact of the harness, not of the board, so it is the pump that
  must name the busy tasks. Forget, and `next_task` will honestly hand back the task your agent
  is already working on as "your active task", and you will dispatch a second one onto it. A
  killed turn loses that set — and that is FINE: on the next tick an empty `exclude` returns the
  task as "your active task", and the ordinary rule kicks in, "the agent died → dispatch a fresh
  resume agent" (see "Who does the work"). That one comes back to THE SAME tree and its
  unfinished work: `workspace <id>` on an existing tree creates nothing and hands that same tree
  back (`created: false`). But that holds for exactly THIS return path (the agent died, the task
  stayed in Design/Build) — see the next item.
- **A complete `exclude` is also the VISIBILITY of signals, not just protection from a double
  dispatch.** The branch order in `next_task` is rigid: your active tasks → a partial claim in
  Queue → a review offering → the slot check (`wip_saturated`) → the free queue. The slot check
  sits AFTER the first three, so an active task you failed to name is handed back to you as
  "your active task" BEFORE the turn ever reaches that check. The same board in the same minute
  answers `wip_saturated: true` to a complete `exclude` — and "your active task" with
  `wip.free: 0`, with no `wip_saturated` field at all, to an incomplete one. Which means
  `wip_saturated` is a signal you get ONLY if `exclude` is complete.
  - **Saw a resume at `wip.free == 0` — check YOUR `exclude`, not the board.** If an agent of
    yours is ALREADY live on that task, the set is incomplete: add the id to `exclude` and call
    `next_task` again (that is when `wip_saturated` arrives), and do NOT dispatch a second agent
    onto it — that is exactly what `exclude` prevents. No live agent on it — this is the
    ordinary resume after a dead agent, dispatch a fresh one. `next_task`'s reply at
    `wip.free == 0` reminds you of this in its `note` — but only you know the set itself, so
    only you can check it.
  - **What you go blind to is not only saturation: review offerings get overridden too.** The
    review branch sits ABOVE the slot check and takes no slot, so a saturated pump with a
    COMPLETE `exclude` still gets its reviews; with an incomplete one a resume overrides them.
    (Since #991 that holds in a SOLO setup too, and it did not before: the branch skipped cards
    assigned to you, and there every card is yours, so a review never came at all. Now it does —
    and a complete `exclude` starts deciding here as well, because the only thing that takes a
    card off the offering is a verdict.)
  - **This is NOT a bug, and we do NOT touch the branch order.** `vikunja-mcp claimable` rests
    on it — the outward-EXPORTED check "is there work for this token?", by which an external
    supervisor decides whether to boot an agent at all: it calls `next_task` with an EMPTY
    `exclude`, and so NEVER reaches the slot check and answers "there is work: resume". Move the
    slot check higher and a saturated board holding an abandoned, perfectly resumable task
    starts answering "no work", and nobody will send a resume agent for it any more.
- **Two returns, two trees.** There are TWO different ways to come back to a task, and their
  trees differ; the rule above describes one of them. An agent that expects its own tree ALWAYS
  will hunt in it for unfinished work that was never there. And what decides here is not WHY the
  card came back, but one fact: whether the tree was torn down through `--release`. And that
  removes the directory and the `task/<id>` branch ONLY when the tree is clean and everything is
  pushed — so "there is no tree" and "the work is already on the main branch" are one statement,
  not two.
  - **The agent died** (the task still stands in Design/Build behind you, nobody called
    `--release`): `workspace <id>` hands back THE SAME tree (`created: false`) — commits and
    UNcommitted work both in place. Two exceptions: a tree taken off its branch by an
    interrupted rebase comes back as a REFUSAL ("build worktree … DETACHED", see "A separate
    case") — that gets fixed first; and a directory removed CRUDELY (around `--release`) is cut
    AFRESH (`created: true`) and reattached to the surviving `task/<id>` branch — the commits
    come back, the uncommitted work does not.
  - **The card was returned from Review** (`needs_work` from a reviewer, or a human by hand)
    AFTER a successful push and `--release`: neither directory nor branch — there is nothing to
    reattach to. `workspace <id>` cuts a FRESH tree from the CURRENT `origin/<main branch>`
    (`created: true`), which has moved ahead by the siblings' commits. That is neither a loss
    nor a regression: the push succeeded, so the predecessor's change is ALREADY on the main
    branch, and a fresh base is the best one there is. Read what was done from the `[review]`
    comment and the pushed diff (`git show <sha from evidence>`), NOT from the working
    directory.
  - **A return WITHOUT a successful push looks like the first case, not the second** (typically
    the orchestrator refusing an unverifiable evidence sha, step 3 of the tick): `--release`
    refuses there ("unpushed commits"), and the tree with the work stays where it is.
  - **So check, do not assume.** Two commands in the tree: `git status --porcelain` and
    `git log --oneline origin/<main branch>..HEAD`. Both empty — there is NO unfinished work
    here and nothing to look for; non-empty — there it is. The answer is honest on every path
    above, including the rare "the branch leaked" (`branch_deleted: false`): there the tree is
    reattached to it and the base will be older than the current main branch, which the ordinary
    `git fetch origin && git rebase origin/main` before the push straightens out.
  - **Deliberately NOT done:** letting the `task/<id>` branch live on after `--release` so that
    a return could reattach to it. That would break the release's own protection and would pile
    up one branch per EVERY finished task; a fresh tree from the current main branch is the
    better behaviour — it just had to be said out loud.
- **A review takes no slot.** `wip.active` counts only Design/Build assigned to you; a card in
  Review is not your active task (see "Queue discipline"), so background reviews do not narrow
  the drain, however many of them there are.
- **Having cast a verdict, the reviewer releases its own tree:**
  `vikunja-mcp workspace --release <id> --role review`. `--role review` is MANDATORY here — by
  default `--release` takes down the build tree, i.e. somebody else's. Fail to release it and
  the tree lives until the card leaves Review (who moves it out of there — see below), and
  `--gc` will not take it before that: a review tree's liveness is counted BY ROLE, not by
  freshness, so while the card is in Review the sweep does not touch it, however long it has
  stood without a single write — the grace window never even reaches it. **And do NOT commit
  INSIDE a review tree** (notes, a draft verdict): it is detached, a commit in it is reachable
  from no branch — `--release` will refuse to remove it, `--gc` too, and the tree stays forever.
  A second round of review on that task runs into it:
  `workspace <id> --role review --at <new sha>` REFUSES ("pinned at …"), and that is right —
  otherwise you would silently get a tree with the OLD code and cast a verdict on that. The
  verdict goes as a comment to the tracker (`review_task`), not as a commit into the tree.
  - **It is not only the human who moves a card out of Review — YOUR verdict moves it too, and
    the tree dies along with it.** `approve` does NOT move the card (only the labels), so after
    it the tree lives until a human takes the card away. But `review_task(verdict='needs_work')`
    sends the card to Build — and from that second on your tree is DEAD to `--gc`, exactly like
    a build tree after `advance(to='review')`. After that only the grace window holds it, and
    that is counted from the last WRITE in the tree: a purely reading review (Read, `git log`,
    `git show`) has no writes at all, so the window ticks from the tree's CREATION and not from
    the verdict, and a review longer than the window falls outside it before you have even cast
    the verdict. Verified: the very next sweep hands a quiesced tree back in `released`, and the
    directory is gone. No work is lost by this (the tree is detached and clean), the cost is
    bounded by a vanished cwd — but the rule is therefore exactly the build side's after
    `advance`/`call_human` (see "Check-point early" and "Commit+push is part of the transition to
    Review"): **once you have cast the verdict, do not assume you are still standing in your own
    tree**; needed the directory — call `workspace <id> --role review --at <sha>` again rather
    than walking into the old path. Do NOT hold the verdict back for that: "record the verdict
    at once" is the stronger rule (a lost verdict is a whole review again from scratch, a
    vanished directory is one `workspace` call); simply do whatever needs THIS directory BEFORE
    the verdict.
  - **And the tree does not die only under YOU — you can also kill it under an agent YOU
    DISPATCHED.** The bullet above, and the build side's "Worked in your own worktree", are both
    addressed to YOU, singular: they teach you not to assume YOU are still standing in your own
    tree. Neither is about the second-pass auditor, the nested implementer or any other
    subagent of yours that is still working in there when you call `--release`. That is not an
    exotic state — SKILL.md's second-pass rule (NOT this file: both phrases are its, at "Launch it
    EARLY and IN PARALLEL" and "WHERE it works") orders the auditor launched early and in parallel,
    i.e. deliberately still running while you finish, and tells you a READING auditor is fine in
    your tree. So the rule is: **`--release` is the LAST action with respect to every
    agent you dispatched, not only to you.** Let them return first, or give any auditor that RUNS
    anything its own clone.
    - **The incident, live and not constructed.** On VMCP-323 (1685) the reviewer dispatched a
      second-pass auditor, then called `workspace --release 1685 --role review` after recording
      its verdict, while the auditor's `pytest` was still running inside that review worktree.
      As the reviewer recorded it, quoting its auditor: `collected 1435`, then a run of `F`s in
      `test_workspace_cmd.py`. It correctly reported nothing from that run, and the reviewer
      correctly reported nothing from it either. **Nothing was lost from the repository** — the
      work was already on `main`, the review tree is detached and clean. What was lost is a
      MEASUREMENT and a dispatch. Do not let that drift into "work was destroyed": this is not
      the `removed_ignored` family at all, and a tree destroyed under a live reader leaves no
      entry in that list.
    - **Why `--release` cannot notice, and why no guard is coming.** Constructed stand (a throwaway
      repo, a worktree, a `sleep` with its cwd inside): `git worktree remove` returns 0, the
      directory is GONE, and the process keeps running — `ps` still lists it, `lsof` still reports
      the same unlinked cwd. POSIX lets a directory be unlinked out from under a process; nothing
      fails, on either side. No guard is being added: the PREDICATE one would need — "is a process
      cwd'd in this tree" — was measured and found blind, and the guard rejected on that. The
      arithmetic is in `docs/dossier/workspace.md`, and the one number an agent
      needs is that a probe deep enough to see a cwd anywhere under a live agent's own worktree
      returned ZERO over that tree at a moment when the agent had nothing executing — against a
      positive control of 1 for a planted process. An agent is not a process in its tree, it is a
      sequence of short-lived ones, and between two tool calls there is nothing there to find.
      1685 is the case such a guard WOULD have caught (a 13-minute `pytest` is one long-lived
      process), which is exactly the trap: it would be trusted, and then silent for the auditor
      sitting between two of its own rounds. **Nothing in the code will catch THIS for you** — the
      one thing that does fire is the ordinary `dirty` refusal, if your subagent happened to leave
      a non-ignored file behind, and a running suite leaves only ignored ones. **The ordering is
      the protection.**
    - **`--release` is NOT the only caller — `--gc` opens the same door, and the ordering rule
      above does not reach it (VMCP-325, #1700).** The rule you have just read is something YOU
      execute: let your agents return, THEN release. A reviewer can obey it perfectly — never call
      `--release` at all — and still lose its auditor, because the removal is somebody else's
      call. Compose three rules that are all in force and none of which is being broken: your
      `needs_work` verdict moves the card Review -> Build, so your tree is DEAD to the reaper from
      that second (the bullet above); SKILL.md's second-pass rule tells you to record that verdict
      IMMEDIATELY and append the auditor's findings afterwards as a `comment`, i.e. deliberately
      with the auditor still running; and the orchestrator runs `--gc` FIRST on every tick. Nobody
      deviated, and a tick can sweep the tree out from under a live auditor. Same shape as the
      1685 incident, different caller — and a lost measurement again, never lost code.
      **What bounds it is the grace window, not a rule, and it is worth knowing exactly what that
      buys.** `_REAP_GRACE_SECONDS` is 30 minutes in `workspace_cmd.py` (read there, not quoted
      from a card), counted from the newest mtime of two markers — the worktree DIRECTORY and its
      INDEX — so the sweep has to land after the window while the auditor is still running. That
      is narrow, and it is NOT nothing: a purely reading review moves neither marker, so for it
      the window ticks from the tree's BIRTH and can be long gone before you even cast the
      verdict; and an auditor's `pytest` bumps the directory mtime only when it CREATES a
      top-level entry, so a second round over an existing `.pytest_cache` need not refresh
      anything.
      **The remedy is the clone, and this is its SECOND independent reason.** SKILL.md already
      says to give an auditor that RUNS anything its own clone — argued there from two writers in
      one directory, and extended by VMCP-324 to your own `--release`. A clone lives OUTSIDE the
      worktree, so the reaper cannot reach it either; a reading auditor in your tree, which both
      of those rules still permit, is exposed here with nothing of yours to reorder. Holding the
      verdict back is NOT the fix: "record the verdict at once" is the stronger rule.
    - **What a round actually looks like when its tree vanishes — three measured shapes that do
      NOT share a failure mode.** (a) A shell reader looping over the tree ran to COMPLETION, exit
      0, reporting zero files and zero bytes — not because the calls succeeded (`ls` from an
      unlinked cwd exits 1 with "No such file or directory") but because that stand discarded
      stderr and counted empty output. That is the dangerous one, and the danger is the REPORTING:
      a round that drops stderr or ignores exit codes turns this into a clean-looking all-zero
      round. (b) A real `pytest` whose tree was
      removed two seconds in printed its dots to `[100%]` and then died at session teardown with
      `FileNotFoundError` on the tree path, exit 1 — and NEVER printed its summary line, so the
      `N passed` a sweep greps for simply is not there (control, same suite, tree intact:
      `6 passed in 6.08s`). (c) The live 1685 case above: `collected`, then `F`s — loud. So "we
      would notice" is NOT available as a reason to skip the ordering: only (c) announces itself.
      What all three share is that the RESULT is gone. Read a round only from output you PROVED
      exists.
  - **`released: false` has FOUR readings, and your ordinary one is the last.**
    `--release --role review` over an already-removed tree returns exit code 0 and
    `code: "no-worktree"`: that is not a refusal of the PROTECTION ("unsaved work is left"), not
    an interrupted rebase and not a human's lock, but a success after the fact — there is
    NOTHING to do and repeating it is pointless. The fourth reading was filed by #631:
    `code: "locked"` — a HUMAN locked the tree (`git worktree lock`), the work is intact and
    nothing was deleted, but taking the lock
    off is not yours to do (`git worktree unlock` belongs to whoever set it); name the path and
    the lock in your verdict and do not touch the directory. `released: false` is never, ever a
    tool failure: a failure has exit code 1 and an `error` field. `--release`'s breakdown of the
    codes is one for both roles and lives on the build side ("Worked in your own worktree", in
    "Traces of the work") — read it there rather than growing a second one of your own.
    **But "one for both roles" holds in the other direction too: `dirty` does NOT TELL the roles
    apart.** One file left in the tree — a probe, a draft, exactly what "whatever can live in
    your tree, let it live there" calls for — gives `{"released": false, "code": "dirty"}`. And
    the CURE written there is the build one, "take it through to a push and repeat", and it is
    FORBIDDEN to you: the tree is detached, there is no branch, and a commit into it is an
    `unreachable-head` forever — and QUIETLY at that: such an entry grades into `expected`, i.e.
    into the "do not look" list, and nobody will see it. Your cure is one: take the file out of
    the tree (need it later — carry it outside, with the task id in the name) and repeat
    `--release`. Fail to, and when the card leaves Review the entry lands in `kept` (`dirty`
    reaches `expected` only for a BUILD tree and only under a parked card; nothing whitens YOUR
    tree), and the human will see it on every sweep — from the moment the tree has stood without
    a single write for longer than the grace window — without knowing whose it is.
    **And it is exactly the other way round with an IGNORED file: it does not give `dirty` at
    all.** Took a `shot-<id>.png` in your own review tree (the `*.png` rule) or put the
    browser's output into `.playwright-mcp/` — the guard does not see it, the tree goes with an
    ordinary `released: true`, and the files go with it. One trace is left: `removed_ignored` in
    that same entry (the breakdown is in the same place, "Worked in your own worktree"). So
    everything you need AFTER the verdict, carry outside BEFORE it.
- **It didn't start — do NOT drop the loop.** When `workspace` could not do the work (not a git
  repo, no `origin`, the path taken by a foreign directory, no permissions, `--gc` could not
  reach the tracker, a tree half-created by a killed `worktree add` — "HALF-CREATED", which a
  human fixes with the two commands from the error text), it prints `{"error": ...}` and returns
  exit code 1. That is NOT a reason to stop and NOT a reason to declare the queue empty: you
  work in ONE slot in the main checkout — exactly as in sequential mode — and keep draining. A
  per-task agent with no path in its brief is a normal case too: it works where it stands. The
  error repeats tick after tick — file a `file_task` so a human sees it, rather than degrading
  silently forever. **Do not confuse this with `--release`'s `released: false`:** that one comes
  with exit code 0, and is NEVER EVER a tool failure — but it does not carry one single meaning
  either: `dirty`/`unpushed` mean "unsaved work is left", while `no-worktree` means "the tree is
  already gone, and that is fine". Read the `code`, not the bare fact of `false` (the breakdown
  is in "Traces of the work").
- **A separate case: `workspace <id>` refused with the words "build worktree … DETACHED".** This
  is NOT a broken tool and NOT a reason to narrow the drain: the tree is alive, the work is
  intact on the `task/<id>` branch, but the tree is not standing on it — typically an
  interrupted `git rebase origin/main` (a killed turn breaks it off exactly like that and leaves
  the tree CLEAN, so nothing shows from the outside). `ensure` used to hand such a tree back
  silently as an ordinary one, and the resume agent committed into a detached HEAD, while its
  `git push origin HEAD:main` pushed the replayed commit instead of the branch's work. Action:
  dispatch the resume agent AS USUAL (the path is in the error text) and hand it the whole
  diagnostic — the first thing it does in that tree is `git rebase --continue` or
  `git rebase --abort` (which one is its call, knowing its own work; `--abort` throws away what
  was replayed), after which `workspace <id>` hands the tree back normally again. The tool itself
  does not make that choice and fixes nothing silently.

## Landing: the full commit+push recipe

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
  - **A FIGURE OR A QUOTATION claimed as a property of the TREE is measured AFTER the last rebase
    — right before the push, not when it was convenient to obtain.** The chain above re-runs the
    CRITERIA after the rebase and not the PROSE, so everything else slips through: the sweep
    record in a docstring, the control round's `collected`, a quoted phrase, the "Gates on this
    tree: … N passed" commit-message line. Written BEFORE the rebase, they land describing a tree
    in no history: it moved after you wrote — a SIBLING landed, or YOU edited it again. So the
    rule is not "measure carefully" but "measure LAST": siblings land beside you and the release
    bot after every green landing, so staleness is ordinary.
    The measurement is VMCP-249 (840): its commit carries "Gates on this tree: uv run pytest
    tests/unit -> 1136 passed" and the sweep record "control 0 failed / 0 errors / 200 collected"
    (both on the landed sha), while its independent reviewer's re-measurement on the SAME sha gave
    1139 passed and 203 collected — a sibling with three tests landed in between. Its `[worklog]`
    carries the correct 1139: the author re-measured for the TRACKER and not for the PROSE — a gap
    in the prescribed order, not one agent's slip. The sweep's own deltas reproduced exactly and no
    pin was blind; what breaks is only the figure certifying round and control measured ONE tree —
    the whole point of the cross-check.
    In practice: last thing before `git push`, re-derive every number and quotation your prose and
    commit message claim of THIS tree. A quotation is the worse half: it reads as authoritative
    forever, where a number disagrees with a re-run. `git grep -F` a ONE-LINE fragment of each
    span, requiring a hit OUTSIDE the claiming file; a MISS is a PROMPT, not a verdict — a card's
    description, a commit message, a tool's output and a retracted wording are no tree strings.
    Do not wait for a gate. Cheaper still is not to write an absolute at all: an assertion of the
    PROPERTY (an assert) never goes stale.
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
    **"Everything remaining" includes every agent YOU DISPATCHED: `--release` is the last action
    with respect to THEM too, not only to you.** One still standing in that tree is not a state
    `--release` can see — the removal succeeds, the directory goes, and it runs on against an
    unlinked cwd. What dies is its RESULT, never TRACKED work, which a successful release proves
    is committed and pushed (ignored files are the separate subtlety below). So let every agent of
    yours return BEFORE you release, and give any auditor that RUNS anything its own clone rather
    than your tree. Measured live on VMCP-323 (1685); the evidence is in `references/drain.md`.
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
