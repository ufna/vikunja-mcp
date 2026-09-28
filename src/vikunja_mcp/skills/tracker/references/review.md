# Independent review of changes — the push model

> **A reference for SKILL.md, not rules of its own.** Read it **when you are the REVIEWER and are recording a verdict**.
> What is BINDING lives in SKILL.md itself — what is worked out here is the response shapes,
> the measured gotchas and the reasons a rule is written exactly the way it is.

## Independent review of changes — the push model

No change goes to the human without independent review — not just a bug fix.
ANY task brought to Review is reviewed by a separate agent. The ONLY
exception is an epic container (the `epic` label): it has no code of its own (the evidence lies
in the children, each child reviewed on its own move into Review), there is nothing to review.
The exception hangs on the `epic` label, NEVER on the presence of subtasks.

In a solo setup (one token for everything — the orchestrator and its per-task agents travel under
one assignee) review is initiated by the same side that wrote the change — by push:

- **The push comes right after Review.** A per-task agent that has brought a task to Review gets
  `review_needed: True` and `review_kind` back from `advance` and reports that in its summary;
  the orchestrator immediately dispatches a FRESH review subagent **in the background** (the
  reviewer is a sibling of the orchestrator, so author ≠ reviewer).
- **The first thing a reviewer does is establish that it is looking at EXACTLY that code.** A
  verdict on someone else's code is the worst outcome a review can have, and after the fact it is
  indistinguishable from an honest one. You need your own tree at ANY `wip.limit`, not only in a
  parallel drain: `vikunja-mcp workspace <id> --role review --at <sha from evidence>` reads
  neither the board, nor the token, nor the limit. At `limit: 1` you need it all the more: while
  you review in the main checkout, the pump is already ENTITLED to dispatch the next task's agent
  into that very place. And a tree is not yet a guarantee: the same call WITHOUT `--at` silently
  hands you the EXISTING tree, nailed to the OLD sha (`created: false`, the old sha in `head`, not
  a word of refusal). So check it yourself: `git rev-parse HEAD` in your tree = the sha from
  `evidence`. No tree at all (`workspace` refused, no path was given in the brief) — do NOT review
  what you are standing in: that is the main branch's code, not that commit, and on top of that a
  sibling may be working in it right now; read `git show <sha from evidence>` and name in the
  verdict what you looked with.
  And know WHERE everything else about your tree lives — how to release it, the four readings of
  `released: false`, the `dirty` refusal: in the bullet "Having cast a verdict, the reviewer releases its own tree", and that one stands INSIDE the section "Parallel drain (when
  `wip.limit > 1`)". The section heading does not apply to you: that bullet is yours at ANY
  limit — it is just that at `limit: 1` you would otherwise never get to it at all.
- **Having checked the sha, look at the OUTCOME of the CI run on it — you are the only one who by
  construction is LATE.** The implementer must look at the outcome as the last action of its turn,
  but for them that is ONE look without waiting, and on an unfinished run their honest answer is
  "did not wait" (see "After the push there are TWO checks" in "Commit+push is part of the transition to Review"; the commands, the requirement of the FULL sha and the analysis of
  `status`/`conclusion` are there too). You do not have that problem: you start later and work in
  minutes, while a run completes in at most 120 s (40 measurements on the first attempt; the
  runner queue was 0 s for 35 of 38 and at most 80 s) — so by the time of your verdict
  `conclusion` usually already exists. There is exactly one caveat: a run RESTARTED by hand waits
  for a human, not for a runner (measured 31 min and 3 h 26 min from creation to the second
  attempt), so "120 s" is about an ordinary push run, not about any run.
  So: `gh run list --commit <FULL sha from evidence> --json databaseId,status,conclusion`
  — and name the outcome in `[review]` beside what you looked at the code with. This is not a new
  duty out of nowhere: both the implementer and the reviewer of VMCP-129 (615) checked CI exactly
  this way on their own initiative, the rule merely stops hoping for initiative. If it has not
  finished for you either — do NOT hold the verdict back because of it ("record the verdict
  IMMEDIATELY" is stronger): write in `[review]` that at the moment of the verdict the run was
  still going, and name its id.
  A red run BY ITSELF is not yet `needs_work` — first look at
  `jobs`: `lint-and-unit` failed (the same `ruff`/`pytest` as in the readiness criteria) —
  the main branch is broken by this commit, and that is a verdict; a lone `integration` failed —
  an environment refusal, and that is a finding for the report, not a reason to drive the card
  back for rework. If the run did not start at all on the full sha — here there is first ONE step
  of diagnosis, not `needs_work` straight away: a run is started on the TIP of a push, so a
  non-tip commit is left without a run and without a check-suite even though the work arrived
  (measured — 1 of 21 task commits, ~5 %; the analysis and the commands are in "After the push
  there are TWO checks"). `git log --oneline <sha from evidence>..origin/main`: there is a commit
  with a run above — the marker has nothing to do with it, this is a finding for `[review]`
  (nobody ran the tree at exactly this sha), not a bounce. Empty above OR the descendant has no
  run either — then `needs_work` without discussion: a swallowed ci-skip marker, the work did not
  reach the consumers.
- **`review_kind` sets the reviewer's rubric.** The general brief: read the dossier (`get_task` —
  description, spec, worklog, and on a second round the previous `[review]` as well: the card came
  back from Review for a reason), verify BY RUNNING (not by reading the code), look for obvious
  regressions nearby and record the verdict `review_task(task_id, verdict='approve'|
  'needs_work', report=...)`. **Record the `review_task` verdict IMMEDIATELY, as soon as you are
  sure** — do not put it off to the very end after optional extra checks: a turn killed by a
  limit or an error BEFORE the call loses the verdict entirely and the review has to be
  repeated from scratch. A second independent pass over your report is NOT required (#1987);
  see "Second pass over your own text — optional" in SKILL.md. Then, by the rubric:
  - `review_kind: 'bug'` — reproduce the bug (or explain why that is impossible) and
    make sure the fix closes the CAUSE from the report (root_cause), not the symptom.
  - `review_kind: 'change'` (feat/chore/docs/refactor/…) — make sure exactly what is in the
    spec/description was done; that the tests are REAL (they check behaviour, not a
    tautology); that the change stayed in its slice (no scope creep and no stray edits).
    root_cause is NOT required here — it is mandatory only for bugs.
  - **A card whose whole diff is TEXT (comments, docstrings, rules, docs) gets ONE light pass**
    (#1987). The only question: would this text make an agent or a human do the wrong thing —
    run a wrong command, take a wrong branch, draw a wrong conclusion? Yes — `needs_work`, naming
    the wrong action. Anything else (a wording, a figure you would have measured differently, a
    claim a little wider than its evidence) is NOT a verdict, NOT a card and NOT a comment:
    approve and drop it. No mutation rounds, no measurement stands, no second pass.
- **Reviewer ≠ implementer.** These are different subagents with unmixed contexts;
  whoever wrote the change in this session does not review it.
- **In parallel, without blocking.** Having dispatched the review in the background, do NOT wait
  for it — go straight to `next_task`/`claim` for the next task. A background review does not
  count as your active task (see "Queue discipline").
- **Verdict → a label on the board.** approve hangs `reviewed` (the human will see
  `[review] APPROVE` and take the decision about Done); needs_work hangs `review-failed`
  and returns the task to the implementer in Build with a report — and a card WITHOUT an
  assignee has no implementer, so it leaves for **Queue** as free work (#705; see "After Review",
  where what to do with it once claimed is worked out). The labels are mutually exclusive; a
  resubmit into the active pipeline through `advance` (to='build' or to='review') itself
  removes ANY previous verdict — both `review-failed` and `reviewed`: resuming
  work invalidates the old assessment. This also closes the case where a human pulled an
  approved card out of Review for rework BY HAND (no tool fired, and the
  `reviewed` label would otherwise have travelled with the task into a new review).
- **The needs_work cycle — and NOT every outcome of it leads back into Review.** The ordinary
  one: the task is reworked and goes to Review again through `advance` (and will return
  `review_needed` again) — push a fresh reviewer once more. But `needs_work` is the ONLY way to
  return a card to its owner (no agent tool takes it out of Review any more), so a reviewer uses
  it to file what is not "rework" at all, and from the shape of the bounce that is NOT VISIBLE —
  you have to read the text of the report:
  - **A QUESTION FOR THE HUMAN** — the reviewer has no door of its own to the human on this card
    (`call_human` from Review refuses, see "Stuck? The way out depends on your ROLE"). The
    implementer forwards the question through `call_human` from Build → the card leaves for
    **Your Call**.
  - **"IT HAS TO BE SPLIT"** — `decompose` from Review refuses too (gate #663), so it is split by
    the owner from Build → the parent leaves for **Backlog** with the `epic` label, the children
    go into Queue (measured; the details are in the `decompose` bullet of the section
    "Decomposition and filing findings").
  - **"IT LOST ITS POINT" / an external block** — `return_task` from Review refuses by the same
    gate (#590), so it is returned by the owner from Build → the card leaves for **Backlog** with
    the `blocked` label and WITHOUT an assignee, for the human to re-triage (measured).
  In any of these branches there is NOTHING to push a reviewer at — the card is not in Review —
  and no `advance` will follow the bounce either. Do not wait for either. And do not read the list
  as closed: the full analysis, together with the "nothing fitted" branch, is in "After Review".
- **Multi-identity (for the future).** If a second free agent with a DIFFERENT token appears in
  the setup, it will be able to pick a task up for review by itself — through
  `next_task` (branch 3: any non-epic task in Review without a fresh verdict, not its
  own; the dormant pull path stays alive). In solo there is no second one, so the mechanism is push.

## A second independent pass over YOUR OWN text

**STATUS SINCE #1987: OPTIONAL, AND NEVER ON A PROSE-ONLY CARD.** The binding rule is the short
section "Second pass over your own text — optional" in SKILL.md. What follows is the procedure for
the rare case it is worth raising, kept with its evidence. Where the text below says "mandatory",
read "when you raise one"; where it says a finding goes into a comment or a card, read: fixed in
your diff or dropped.

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
  history, `git log -S`) is fine with your tree — the bullet above describes exactly that one.
  **Fine against MUTATION — the collision this bullet is entirely about — and NOT against your own
  `--release`, which destroys the tree under a reader just the same** (see "Worked in your own
  worktree"). But the moment you ask it to RE-MEASURE, the assignment becomes a WRITING one: a
  claim of the form "X is what catches Y" is re-measured, by this repo's rules, by deleting X and
  requiring the test to go RED — that is, the auditor mutates exactly the sources you are running
  your own rounds over in that same minute. And the path you have to hand is exactly one — your
  working tree; hand it over in the brief and there are TWO WRITERS in one directory. The
  collision was caught live on
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

  Deleting inside the stand goes by the `$VAR/` rule in "What does collide".

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
  Review and after the verdict alike, and a second `review_task` is not needed for that.
  **The verdict kills YOUR tree too — for a `--gc` no ordering of yours reaches**: ANY auditor
  still there, reading or not, may be swept; give it its own clone (`references/drain.md`).
  That COMMENT is how the post-verdict notes on 582, 594 and 603 are written: the tool's verdict
  ALWAYS comes on the first line — `[review] APPROVE` or `[review] NEEDS WORK` — and these do not
  have it, so they were appended with `comment`. The converse does not hold: a `[review]` comment
  without that line is not necessarily a post-verdict note (on 582 a scope-note stands that way,
  arrived from a neighbouring card even BEFORE the work).
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
