# workspace_cmd.py — worktree-дренаж, --gc, sync_main_checkout

> **Это ДОСЬЕ, а не правила.** Правило живёт в `CLAUDE.md → Архитектура → workspace_cmd.py` — там оно короткое и
> обязательное к исполнению. Здесь лежит доказательная база: измерения, построенные
> стенды, опровергнутые формулировки и номера карточек.
>
> **Читай перед тем, как менять этот код.** Этот репозиторий уже чинил гарды
> рассуждением вместо измерения — по несколько раундов подряд. Если правило кажется
> избыточным, ответ почти наверняка здесь.

- `src/vikunja_mcp/workspace_cmd.py` — `vikunja-mcp workspace`: per-task git worktrees for
  the parallel drain (`wip_limit > 1`). **The ONLY module in the package that runs git** —
  `server.py`/`workflow.py`/`api.py` stay git-free by rule, not by accident (a subprocess in
  the stdio server's path is a new class of crash). `git worktree add` refuses a branch that
  is already checked out, so each agent gets its own throwaway `task/<id>` branch and pushes
  with `git push origin HEAD:main` — "one task = one commit on main" and the CI auto-release
  survive untouched. Create (`<id>`, `--role review --at <sha>` for a detached review tree)
  and `--release <id>` need neither the tracker nor a token (create is not offline, though —
  it runs `git fetch origin`); only `--gc` reads the tracker, because
  only the board can say whether the task behind an orphaned tree is still alive (build tree
  ⇔ Design/Build assigned to me via `Workflow.active_task_ids`, review tree ⇔ card in Review
  via `review_task_ids`, one shared `liveness_board()` fetch, read-only like `claimable`).
  Every entry point canonicalises to the MAIN worktree first (`_main_worktree`), so create /
  release / gc agree on paths and config even when invoked from INSIDE a linked tree — the
  normal place for a per-task agent, and where the gitignored `.vikunja-mcp.env` does not
  exist. **`--gc` also FAST-FORWARDS that main worktree, and that is the whole of #801**
  (`sync_main_checkout`, optional `main_checkout` key). Nothing in the drain used to move it:
  a task lands with `git push origin HEAD:main` from its OWN tree, which advances the shared
  `refs/remotes/origin/<base>` and never the local branch the main checkout sits on — so the
  folder a human works in, and the one the pump was launched from, falls behind monotonically
  and never catches up (measured 2026-08-05: `5d7acdb` against `origin/main` `01b096be`, **58
  commits over ONE session**, every task green). It rides on `--gc` because that is already the
  call the pump makes every tick, already canonicalised here, already networked and already
  returning a payload SKILL.md tells the pump to read — zero new orchestrator steps, where a
  rulebook rule would have cost a step that can be forgotten. It is
  **fast-forward ONLY and refuses rather than resolves**: the
  main checkout is somebody else's working directory, so `reset --hard`, `checkout -f`, `clean`,
  `stash`, `pull`, a bare `merge` and switching branches are all deliberately absent and must
  stay absent. What protects uncommitted work is GIT, not a guard of ours — outside a live GITLINK
  `merge --ff-only`
  refuses outright when it would overwrite a modified TRACKED file or an untracked one that is
  NOT IGNORED, and that is load-bearing rather than lazy: a "refuse unless the tree is clean"
  guard would never
  have fired on the very checkout the card was filed from, which held the human's untracked
  `BOARD-ANALYSIS-2026-08-03.md`. **That sentence used to end at "or an untracked one", and #806 —
  filed by 801's own independent reviewer — measured that an IGNORED file is untracked and dies
  anyway.** Two routes, both built on real git and both live in this repo: upstream force-adds a
  path this checkout ignores (`*.png` — and a `.png` in the MAIN checkout is an ordinary state
  here, since the shared browser resolves a bare `filename` against the MCP server's cwd, which
  IS this checkout; the rulebook's own `shot-<id>.png` recipe writes into the agent's worktree,
  which is a different folder and a different instance of the same rule), or — needing no
  force-add at all, so the likelier one — a rule the human typed into their
  own UNCOMMITTED `.gitignore` plus an ordinary incoming file at that path. Both give rc=0, empty
  stderr and the human's bytes replaced; only the FIRST is also invisible to
  `git status --porcelain` (the second shows the `.gitignore` as `??` or ` M`, and still nothing
  about the file that dies). The contrast is
  what makes it a finding rather than a complaint about git: untracked-and-NOT-ignored at the same
  path is refused outright. **That contrast FAILS ON ONE DIFF SHAPE — an entry landing ON a live
  GITLINK's own path as a non-directory — which is #838 and the deepest of the three corrections
  this sentence has taken** — measured at `469db93` on git 2.50.1: that TYPECHANGE
  (`:160000 <non-gitlink> … T`, e.g. upstream replacing the submodule with an ordinary file, and
  identically with a symlink) deletes the submodule's whole working directory at rc=0 with no
  warning, and BOTH protected shapes die with it — an untracked-and-NOT-ignored file, and one that
  is MODIFIED and TRACKED by the SUBMODULE, the only index that has ever heard of it (the
  superproject holds ZERO entries under a gitlink, so from up here every file in it is untracked);
  on that branch `git status --porcelain` is empty afterwards, while through #835's half-applying
  branch the same typechange leaves ` T sub` and rides out in `half_applied`. **Say "on the
  gitlink's path", never "inside a live gitlink"** — that wider wording stood for one round and the
  independent second pass disproved it by construction: an incoming path INSIDE the gitlink
  (`sub/precious.txt`) is refused in the ordinary way, rc=1, naming that path, nothing destroyed. A
  live gitlink is not a blanket blind spot; one shape is. 806 and 835 each NARROWED the sentence;
  here the contrast is ABSENT on that shape, so no probe of ours reports the content either —
  `overwritten_ignored` is about IGNORED paths by name and by remit, and widening the ask cannot
  reach a file no rule matches. The class is bounded by SHAPE and not by hope, and the neighbours
  were built: a plain gitlink DELETE destroys nothing (git refuses to remove a non-empty directory
  and warns, the content survives), and neither does the gitlink becoming a real DIRECTORY, whose
  colliding member is refused normally — **but only a NOT-IGNORED one, and that qualifier governs
  the `sub/precious.txt` refusal above too** (tracker #877, measured on `5782538`): let the
  colliding member be IGNORED (`sub/keep.png`) and the same merge is rc=0, `updated: true`, the
  human's bytes replaced, and `overwritten_ignored` names only the ordinary neighbour outside the
  gitlink — #837's rc=128 is why nothing inside one can be asked about. So on the REPORT axis the
  contrast does not hold in there at all, which is the axis this key exists for.
  Nor is the pre-merge ` M sub` a signal
  to lean on: it is absent whenever the doomed file is ignored by the SUBMODULE's own rules (both
  statuses empty, before and after), and `submodule.<name>.ignore = all` or
  `diff.ignoreSubmodules = all` switch it off outright — #766's lesson again. What the tool should
  DO about it was a product question, and it is ANSWERED: NO to both halves (tracker #838). No
  `--no-index` sub-list under the gitlink — it widens the probe's surface and still cannot reach
  `sub/precious.txt`, which no rule matches, so an ignore-probe is wrong there by REMIT and not by
  reach; and no refusal of the ff — "report and never refuse" (#801/#806) stands, and the
  condition would have had to be "typechange onto a gitlink whose directory is non-empty", i.e.
  every populated submodule, re-refusing every sweep. So this shape is a DOCUMENTED GAP: nothing
  protects that content, not git and not us. Naming the PLACE rather than the files is the only
  option that would cover the non-ignored half too, and it is not implemented — it stays with the
  card. This repo has no
  submodules (`git ls-files -s | awk '$1=="160000"'` is empty — `.gitmodules` is the wrong
  evidence, a gitlink lives in the INDEX), so it is latent HERE and ordinary in a consumer's
  checkout. **What used to close that sentence — "so the REFUSAL branches really do
  discard nothing and that half of the claim was always sound" — was itself FALSE, and #835 (filed
  by 806's own round-2 review, reproduced twice more here) is that correction: `merge --ff-only` is
  NOT ATOMIC.** It attempts every entry and writes everything it can, so ONE path it cannot write
  leaves the rest written with HEAD unmoved — and that was the ONE branch where the
  `overwritten_ignored` probe was deliberately discarded, i.e. the branch promising safety was the
  branch that could destroy an ignored file leaving no trace at all. Two triggers measured on real
  git 2.50.1, and the second is why the card's own "rare on a developer's machine" caveat is weaker
  than it looks: `chmod 500` on a directory, and `chflags uchg` — the Finder "Locked" checkbox — on
  a tracked file THE INCOMING COMMIT HAS TO WRITE (the second pass caught that qualifier missing
  and measured its absence: lock a tracked file the update does not touch and the ff is a clean
  `updated: true`). NOT bounded by index order, which is the natural guess and is measured false: a
  tracked `zzz.txt` sorting AFTER the failing path is applied too. So the state has its own code,
  `half-applied` (`MAIN_SYNC_PARTIAL`), because the ACTION differs — something WAS written and a
  human has to look — carrying `half_applied` (the tracked paths the failed ff wrote) beside
  `overwritten_ignored`. **Everything after that splits on whether `half_applied` is THERE, and the
  two forms have OPPOSITE properties — #851, filed by 835's own review, is that correction, because
  ONE `reason` written for the tracked form was printed on both and all FOUR of its assertions are
  false on the other.** Tracked paths written: the checkout mixes two commits, `git status`
  attributes upstream's content to the human (who can commit somebody else's landed change as their
  own), and **it does not heal** — measured, those half-written paths then block the ff themselves
  as local changes, so clearing the original blocker is NOT enough and every later sweep reports
  `blocked` over a still-mixed tree; only a human committing or dropping them ends it. ONLY an
  ignored path written: `git status` says nothing about the CASUALTY before or after (an ignored file
  is invisible to it, which is what makes this shape undetectable by a tracked-diff probe), there is
  nothing of it to commit or drop, `git checkout -- <that path>` is `error: pathspec … did not match
  any file(s) known to git` rc=1 because it is not tracked locally. **Whether the ff then COMPLETES
  on the next sweep is NOT a property of that form, and saying it was is the third correction this
  same sentence has taken** — round two wrote "and the ff completes once the blocker is gone
  (`chmod 700` + one sweep → `updated: true`)", true of the stand it was measured on and false as
  soon as the incoming commit carries one more path. A merge that fails PART-WAY also writes NEW
  incoming files to disk WITHOUT putting them in the index, `git diff-index` is blind to untracked
  paths by its own docstring, and git then refuses over them for good: measured, a stand adding one
  ordinary `brandnew.txt` gives sweep 1 `half-applied` with the healing sentence and
  `?? brandnew.txt`
  in `status`, then sweeps 2-5 `blocked` on "The following untracked working tree files would be
  overwritten by merge" with the original blocker CLEARED since sweep 2 and HEAD never reaching the
  remote — unblocked only by a human deleting that file. Worse than an inherited blind spot on two
  counts its own review measured: on that input the pre-#851 sentence ("it does NOT heal … every
  later sweep reports `blocked`") was CORRECT, so round two replaced a true verdict with a false
  one; and a test asserted the false phrase, so no mutation could have killed it. The claim now
  rests on a probe taken BEFORE the merge (the incoming paths absent from this disk, minus the
  ones git ignores,
  since those get overwritten rather than refused) — it NAMES the files a human must remove, and
  where it still says the ff is expected to complete it says over what it looked. What is common
  is the
  loss itself, which the old sentence never mentioned at all: the paths now hold upstream's bytes and
  what was there is not recoverable from anything here. **Hedged, because BOTH halves of the flat
  version were disproved by construction:** `git add -f` then `git rm --cached` leaves `status` empty
  and the human's blob in the object store, so `git cat-file -p` handed the bytes back and `fsck`
  called it dangling; and an ignored file whose bytes already equalled upstream's lost nothing — the
  probe names paths that were WRITTEN, never that their content differed. **That second caveat has
  since been NARROWED by #851's own filter rather than dropped**: on the REFUSAL branch the paths it
  can prove already hold the incoming bytes are no longer named at all, so what survives is the
  shapes it cannot ask about (a symlink, a directory, an unreadable name) plus the one it gets wrong
  safely — with a SMUDGE filter configured, raw bytes differ from the blob while the file already
  equals what the merge writes. On `updated: true`, where nothing is filtered, it stands
  unnarrowed. `_partial_apply_reason` is
  the split, and it branches on what `git diff-index` says AFTERWARDS rather than on an empty
  `half_applied`, which is round two of the same card: an empty `half_applied` is THREE states, and
  besides "the probe failed" there is "the probe answered and was BLIND", because `half` is a SET
  DIFFERENCE — a tracked file the human had locally DELETED that the incoming commit also modifies
  cancels out, and the first fix then printed "Nothing TRACKED moved … this DOES heal" over a tree
  that really was mixed and really did not heal (sweeps 2 and 3 `blocked`, blocker cleared). Only an
  EMPTY `diff-index` afterwards may be read as a quiet tree; non-empty-with-nothing-new says
  UNCLEAR, which errs towards "cannot say" and never towards safety.
  `blocked` keeps its name for the refusal where both probes found nothing,
  and the three up-front refusals really are that ("Your local changes …", "The following untracked
  working tree files …", "Updating the following directories would lose untracked files in them"
  each abort before writing, witnessed by a second incoming file sorting FIRST keeping its old
  content) — but read those as three measured MESSAGES, not as the code's meaning: `blocked` is the
  FALL-THROUGH when both probes are silent, which is also what the checkout half-applied in its
  TRACKED paths reports on every later sweep, and what a half-apply whose only casualty got filtered
  as regenerable detritus reports on the first sweep — and, since #851's third round, what the
  ignored-only form reports on every sweep AFTER the one that lost the bytes. That last clause is
  the fix to a ring the fingerprint probe could not help making: it asks whether the file was
  REWRITTEN, and each failed attempt unlinks and recreates it, so the inode moved again (three
  sweeps, 212809910 → 212810669 → 212811229) over content that had been upstream's since sweep 1 —
  four messages for one loss, counting the `updated: true` sweep. A never-read field is the failure
  VMCP-68 split `kept`/`expected` to cure, silencing a repeat is the one-way reading this whole
  chain
  defends, so which to spend was parked for a human, **who took the filter**: on the REFUSAL branch
  only, drop a path this run can POSITIVELY show already holds the incoming bytes (`git cat-file` on
  `<remote>:<path>` against `git hash-object <path>`, read BEFORE the merge — they differ before the
  first attempt and match after). **It CHANGES what the key means, on that branch only, and calling
  that "removing a false positive" was the framing its own second pass sent back**: the bounds list
  above documents that this key names paths that were WRITTEN and not that their bytes DIFFERED, so
  naming a byte-identical file is behaviour, not a defect — and `updated: true` still does exactly
  that. Refusal branch: WRITTEN AND DIFFERENT. One key, two meanings, split by branch, pinned on
  both sides. **RAW bytes, `--no-filters`, and that is a correctness fix rather than a detail** —
  the first version hashed through the checkout's filters on the reasoning that the incoming side is
  git's stored form, and a `clean` filter need not be invertible, so an equal hash did NOT mean
  equal bytes: measured, a plain committed `.gitattributes` with `text eol=lf` against a CRLF
  working copy hashes to the LF blob exactly (`fbbee861…` filtered, `17f2fc0a…` with
  `--no-filters`), i.e. the filter would have swallowed a real loss. Three more properties are
  decisions, not details. Every unanswerable read still REPORTS — a doomed ANCESTOR is no blob in
  the incoming tree, so nothing is compared and the name stays (measured: a local ignored FILE `out`
  under an incoming `out/x.txt` is still named, which is right, since `out` is what dies). The
  `updated: true` branch keeps its UNFILTERED list, so one loss costs TWO messages and not one. And
  the price, accepted in those words: a first-sweep message lost to a probe failure is not re-sent
  by any later REFUSAL sweep — not "never named again", since the sweep that finally completes the
  fast-forward still names it off the unfiltered list, if that ever happens. So what `blocked`
  no longer does is assert "NOTHING was
  discarded"; it reports what the two probes FOUND, and says outright when it could not look —
  **a clause that is only as good as the predicate under it, and #860 shipped it FALSE for one
  round while fixing a neighbouring defect.** Those after-refusal probes each sat OUTSIDE the
  `try` that the three before-merge ones each have, so a `diff-index` timeout escaped
  `sync_main_checkout` entirely and took the whole state dict with it, `overwritten_ignored`
  included — a LOST report, loudly, as `MAIN_SYNC_ERROR`. Guarding them made "I took a before
  snapshot" stop implying "I compared", and round 1's `looked` did not move with them: written as
  a disjunction over the two CALLS, either half answering vouched for the other, and
  `_fingerprints(root, [])` returns `{}`, which IS "not None", so the ignored half answered TRUE
  over ZERO paths — on the ORDINARY checkout, the one with no ignored casualty. A lost report
  became a QUIET FALSE one, "which is what was CHECKED" over a tree where a tracked file really
  had gone v1→v2, which is worse and is exactly the borrowed reassurance #806 was filed for. It
  is now a CONJUNCTION over the two HALVES of "half-written", each counted by what it COMPARED,
  with an empty `doomed` counting only when the pre-merge probe returned it WITHOUT RAISING —
  **and "without raising" is NOT "computed", which is round 2's own correction from its own second
  pass.** That probe gives up in several non-raising ways as well (its docstring enumerates them),
  so the conjunct closes the RAISE the guards made reachable, never the class: a half-apply whose
  only casualty is an ignored path the regenerable-name filter dropped still prints the reassuring
  sentence over destroyed bytes, no key naming them, `git status` empty on both sides, and NO
  injection needed. That one is named rather than fixed because it PREDATES the guards — the same
  stand on the pre-guard parent `1fb0082` gives the same code, the same sentence and the same dead
  file — and closing it would mean making that probe report its own confidence instead of a bare
  list. Read the general lesson rather than the instance: a guard added to a diagnostic changes
  what the sentences ABOUT that diagnostic are entitled to say, and the round that adds one owes
  the predicate a pin — measured on the tree round 1 shipped, NEITHER disjunct of `looked` was
  load-bearing there, and neither deleting either one nor tightening it moved the suite off 0
  failed. Those
  probes are the tree, never the message (locale and git version make the text unparseable —
  though NOT because the three messages are unlike each other: two of them share the whole phrase
  "would be overwritten by merge"): a set-difference of `git diff-index --name-only HEAD` taken
  before and after — the difference, because the ordinary refusal happens BECAUSE the human has a
  tracked file modified, so an after-only read would blame this tool for their edit — and, for the
  ignored half, an `os.lstat` fingerprint of each path the probe named, INODE included (git unlinks
  and recreates, so the inode moves even where mtime granularity would not; the second pass built
  the FAT32 case that needs it). **`diff-index`, the PLUMBING, and that is a correction paid for in
  a regression**: this shipped as `git diff --name-only HEAD` for one round, and `git diff`
  REFRESHES A HUMAN'S INDEX AND WRITES IT, with `GIT_OPTIONAL_LOCKS=0` and `--no-optional-locks`
  both INERT against it — end to end the refused run moved the index under that form, and not under
  the pre-835 code, breaking a property #806 had measured. The discriminating input is a
  stat-dirty-but-content-CLEAN entry whose mtime is in the PAST; `touch` it to NOW and git calls the
  entry racily clean, declines to record the stat, and `git diff` looks innocent — which is why an
  isolated probe of mine saw nothing and the end-to-end one did. (`GIT_OPTIONAL_LOCKS=0 git status
  --porcelain` also preserves the index — there the variable IS load-bearing — and lost only on
  needing rename and untracked records parsed out.) `diff-index` costs false positives on
  stat-dirty-clean entries, which the set-difference cancels: it can hide a half-applied path, never
  invent one. The fingerprint is not belt either: the shape that decides the whole design is a
  failing merge whose ONLY casualty is the ignored file, where `git diff` and `git status` are both
  EMPTY before AND after. It is git's own behaviour — `git pull --ff-only` typed by hand loses the
  same file — and a "copy it aside first" is buildable but was NOT built: the card asked for a
  post-mortem and explicitly not a guard, so #806 fixed
  the SILENCE and not the loss: a SUCCESSFUL sync now carries `overwritten_ignored` (filtered by
  the same regenerable-name list as `removed_ignored`, capped the same way, with
  `overwritten_ignored_truncated` past the cap), computed BEFORE the merge as the incoming path
  list MAPPED onto what each entry DISPLACES on this disk, then ∩ `git check-ignore` — the diff now
  read through `--raw`, so the mode bits can drop a SUBMODULE POINTER move (an ACMT entry git
  satisfies in the index alone, displacing nothing on disk), and the `check-ignore` ask BISECTED,
  so one path git cannot resolve costs one name instead of the batch (both #837, below). **Mapping,
  not intersecting, and that is the whole of round two:** an incoming path can kill something at a
  DIFFERENT path, which is in no diff entry at all. Today the mapping asks the ANCESTORS first
  (shallowest first, walking through real directories: the first ancestor that is a symlink or a
  non-directory is the victim, since git must delete it to make room), and only if no ancestor is
  doomed does it ask about the path itself — which displaces itself, or, when it is a local
  DIRECTORY, the things inside it that can DIE — its files AND its symlinks, one name each. That
  read "the files inside it" here for one round (it APPEARED in `a0ab63e`, while the code wording it
  inherited APPEARED two commits earlier in `fcabff0`) and #836 measured the difference: `os.walk`
  puts a symlink-to-a-DIRECTORY in `dirnames`, and since the LOOP read only `filenames` it was
  named ZERO times. Not descending is NOT what hid it — `dirnames` is a list `os.walk` produces;
  the loop simply ignored it. Result: `overwritten_ignored` PRESENT and INCOMPLETE. Do not
  over-correct to "the PATHS inside it" either — measured, that is wider than what the code returns,
  which omits real subdirectories (their contents are named instead) and empty ones (no bytes to
  lose). A symlink to a FILE and a DANGLING one were always named (`os.walk` splits by `isdir`,
  which FOLLOWS), so among entries that can carry BYTES the hole was one shape wide, which is how it
  got through round one's review; round two's found it by measuring the four shapes rather than by
  reading the diff. That PRESENT-and-short state is the second reading direction spelled out further
  down this bullet, where #837's mechanism for it also lives — read the two together, they are one
  bound. What it is NOT is "the one failure this key cannot afford" — the bounds list next to the
  code names another, present under the INCOMING spelling on a case-insensitive filesystem.
  **Do not write "and that is all the shapes", in any wording.**
  Round one wrote "the ONE channel that has no path in the diff at all" and that sentence has now
  been falsified THREE times in two rounds, each time by construction and each time by a different
  reader: this card's independent reviewer (an incoming `out/x.txt` over a local ignored FILE
  `out`); its implementer, while writing the sentence meant to close the class again (a bottom-up
  walk resolves THROUGH a local ignored symlink and calls the resolved directory safe); and its
  second independent pass (`lexists` follows every component but the last, so an incoming
  `linkdir/y.txt` "exists" whenever the symlink's target already holds a `y.txt` — which took the
  present branch, named a path that displaces nothing, and, because that name is beyond a symbolic
  link, made `check-ignore` exit 128 and threw away an unrelated ignored `shot.png` dying in the
  same commit). Three falsifications, no round without one, is the argument — the count that
  matters is of REFUTATIONS, not of channels, and #837 is the FOURTH, by a fourth reader: a
  SUBMODULE working directory is a REAL directory, so the ancestor walk goes through it and the
  expansion goes INTO it, and `check-ignore` answers neither — `fatal: Pathspec 'sub/x.png' is in
  submodule 'sub'`, rc=128, a SECOND spelling of the fatal beside "beyond a symbolic link", wiping
  the batch and an unrelated `shot.png` with it. **That one refutes a claim of a different kind,
  and it is the kind to be most careful with here:** the symlink round closed its route "at the
  source" and said so — closed by an argument about the two producers, not a measurement over all
  inputs — and #837 is that qualifier coming true in one round. So the batch no longer rides on
  the argument at all, and the two producers were BOTH covered rather than the named one: the
  gitlink filter closes the everyday route, and the bisect covers `_expand_if_directory`, which
  still reaches a submodule on a TYPECHANGE. It also disproved a PREMISE rather than a shape —
  "a directory in the diff is a directory the merge REPLACES" — since a pointer bump leaves the
  submodule's files untouched (measured: ` M sub`, still the old commit), so every path named from
  inside one was a FALSE victim, masked only by the fatal. Two more measured cautions from it, both
  the sort that reads as settled and is not. The filter must test BOTH modes and not either, and
  the proof is NOT the typechange (whose victims are inside a live gitlink and unnameable either
  way, filed as #838) but an incoming submodule ADD over the human's own ignored FILE, which git
  destroys at rc=0 with `status` empty and which IS nameable. And "no submodules here" is a fact
  about this REPOSITORY, never about the code — `--gc` ships on `stable` and this runs in the
  CONSUMER's main checkout; the evidence for it is `git ls-files -s | awk '$1=="160000"'`, not
  `.gitmodules`, because a gitlink lives in the INDEX. Same one-way
  reading as `removed_ignored` — and read it in that ONE direction, because the mechanical routes
  to absent-with-a-loss (the filter, each give-up branch of the probe, the caller's `except`, a
  directory walk cut off at its bound) are not the only ones: a displacement shape the probe does
  not model reaches the same empty answer, and by EITHER road — the ordinary "nothing at risk"
  branch (measured on the ancestor shape) or a mechanical give-up (measured on the symlink one,
  where it also erased a name already found). Both look exactly like good news. **A PRESENT key is
  not a proof that its list is COMPLETE either** — a second direction, not a restatement. #837 added
  a mechanism for it (the bisect reports the askable paths and drops the unaskable ones beside them,
  strictly more information than the `[]` it replaced), but the DIRECTION is older than #837 and its
  first draft dated it wrongly: "a non-empty list at least implied no path had failed" is true of
  check-ignore failures and false of completeness. #836 measured two older roads — the
  regenerable-name filter, which drops a hand-written file under `out/.venv/` with NO companion key
  at all, and the expansion bound, which surfaces as `overwritten_ignored_truncated` while
  understating it (505 dead files report 500) — plus its own defect as a third. So the list bounds
  the loss from BELOW and never sizes it. And
  deliberately NOT the same NAME: there a file was DELETED with its tree, here a path was WRITTEN
  OVER and still exists holding somebody else's bytes. Same class as #710 either way — neither
  `git status --porcelain` nor checkout's own guards see ignored paths. The `main_checkout` key
  ITSELF is ABSENT when the checkout is current or `VIKUNJA_MCP_NO_MAIN_SYNC=1` is set
  (the `NO_SKILL_SYNC`/`NO_TRACE` idiom — env, never the
  toml: this is one clone on one machine, like `worktree_root`), so present ⇒ read it. Codes
  are `MAIN_SYNC_*` and NOT `CODE_*` on purpose: that prefix is the closed per-WORKTREE
  vocabulary `_keep_is_expected` grades and three pins guard, and these never reach the grader
  — pinned, not promised, by
  `test_main_sync_codes_are_not_part_of_the_graded_worktree_vocabulary`. It runs AFTER the
  sweep and OUTSIDE the repo flock (its `git fetch` is networked, and VMCP-72 bounded that hold
  on purpose), and it is BEST-EFFORT: anything it raises becomes an entry, never an exception,
  so the reaper gains no new way to fail.
  Safety invariant taken from hgdev-acp's reaper: push OK → remove, push FAIL → KEEP
  (dirty, unpushed, or reachable-from-no-ref ⇒ reported, never destroyed).
  Housekeeping is never how an agent's work disappears — **except for IGNORED files, and that
  exception is real, measured, and deliberately NOT closed (#710).** The hole is in the FIRST of
  those three guards only — the other two ask about commits — and it is that `dirty` is
  `git status --porcelain`, which does not report ignored paths at all,
  so a tree where everything is committed and pushed but `shot-<id>.png` or `.playwright-mcp/<id>/`
  sits on disk reads CLEAN and is destroyed with them. Untracked-but-NOT-ignored (`??`) the guard
  does see and does hold on, and since #766 that no longer depends on anyone's config. It used to:
  `status.showUntrackedFiles = no` at ANY config level made the SAME command emit neither `??` nor
  `!!` (measured on a real bare origin plus a real worktree — a tree holding an untracked
  `REAL-WORK.txt` plus an ignored `shot-766.png` returned the empty string, `release_workspace`
  answered `released: true` with no `code` and no `removed_ignored`, and the file was gone), so one
  performance knob switched the whole guard off. `_inspect_status` now forces
  `-c status.showUntrackedFiles=normal` on that single call. **That is a restoration, not the
  widening question below** — the guard already claimed `??`, and measured at the default setting
  the prefix changes no verdict, no entry count and no `removed_ignored`; a CLEAN tree still
  releases under the knob, so nobody who set it deliberately is paralysed. Otherwise the hole is
  exactly the ignored ones — and it is this repo's own rulebook that puts them there, since
  SKILL.md's browser recipes write both INTO the agent's worktree. Closing it by widening the
  guard to `--porcelain --ignored` was measured and rejected:
  the mandated gate (`uv run pytest`) creates `.venv` on its first invocation, so a build tree that
  ran the gates holds ignored paths from then on — sampled 2026-08-03, 3 of 3 live build trees did
  (7, 6 and 2 entries, every one of them `.venv/`/`__pycache__/`/a tool cache; the one review tree,
  which had run nothing, held 0) — and `--gc` would stop reaping ANYTHING: trees pile up, disk
  leaks, and the next human turns the guard off outright. A destroy-only-with-a-flag variant is
  rejected by argument rather than by measurement, and the argument is that it has only two
  settings: unset it reaps nothing, always-set it is today's behaviour with a longer argv. So the
  removal stands and the SILENCE is what was fixed: `released` entries now carry
  `removed_ignored: [paths]`, filtered against a small set of by-construction-regenerable names
  (`.venv/`, `__pycache__/`, tool caches, `*.pyc`, `node_modules/`) so that the ABSENCE of the key
  keeps meaning something — a field present on every entry is the never-read signal #516 had to
  split `kept` in two to cure. That filter is a list and a list rots, which is why it decides only
  what is REPORTED: out of date it costs one noisy line, never a stopped reaper. The dangerous
  direction is ADDING to it — and that direction is guarded by a PARAGRAPH, not by the suite: an
  independent pass measured that adding `.playwright-mcp` fails 2 tests while adding `dist`,
  `build`, `out`, `artifacts`, `screenshots` fails none. **Naming
  a loss is not preventing one, and the key reads in ONE direction only:** present ⇒ something
  unrecognised was destroyed; absent ⇒ NOT a proof that nothing was, because `--ignored` collapses
  an ignored DIRECTORY into one entry, so a file left inside `.venv/` dies unnamed (measured).
  **Whether the guard should also HOLD is ANSWERED, and the answer is NO** — a human's decision
  on #764, the card #710 filed to ask it, recorded as FINAL rather than left open for another
  round. Report, never hold. Holding on unrecognised ignored content was declined for a measured
  price (a permanently non-empty `kept` the day the filter dates, i.e. #516's disease, after which
  the next human turns the guard off outright), and so was salvaging those paths aside (a third
  refusal branch in the only module that runs git, plus a dump nothing prunes). The cost bought is
  named where the decision lives, beside the filter in `workspace_cmd.py`: the work is still
  DESTROYED and the field is a post-mortem, so what protects an agent is SKILL.md's
  carry-it-out-of-the-tree-before-`advance` rule and not this code. Re-open it on a NEW
  measurement that moves those prices, not on the argument again.
  **Only ONE of the two refusal channels is
  coded, and the split is deliberate — do not restate it as "every refusal".** A `--release`/`--gc`
  refusal is exit 0 + `released: false` + a machine-readable `code` beside the prose `reason` ("the
  tool RAN and is protecting your work"). The invariant is over `released: false`, NOT over the word
  "refusal" (#631): `--release` can still RAISE, and a raise is the create channel's shape by
  construction — `{"error"}` + exit 1, no code — because it goes through the same catch-all over the
  same open set (a non-git cwd, a malformed toml, a directory git cannot delete). That sentence is
  false before AND after #631; what #631 removed is the instance that mattered, not the class — a
  tree a HUMAN pinned with `git worktree lock`, codeable precisely because git's own porcelain NAMES
  it, so the guard recognises it before touching the tree. It is now `locked`, one guard covering
  all four spellings (with a reason, reasonless, on a review tree, and a locked entry whose
  directory is gone — that last only because the guard sits BEFORE the first git call with cwd
  inside the tree, where it used to raise a bare `FileNotFoundError`). `--gc` GRADES those codes
  into two lists
  (`_keep_is_expected`): `kept` = a human should
  look, `expected` = the two routine states that used to keep `kept` permanently non-empty — a
  parked Your Call card's unsaved work (hence `Workflow.parked_task_ids`, off the same board
  fetch) and a review tree's in-tree commit. Routine is a property of the guard AND the board AND
  the ROLE, and **BOTH rows turn on the role — a claim that was true of one of them for two
  rounds** (#547): `unreachable-head` is routine only in a REVIEW tree (the conjunct stays as a
  backstop even though #540 stopped build trees from reaching it), and `dirty`/`unpushed` only in
  a BUILD tree, because every word of the parked-card justification is about the build agent's
  own conflict while the `dirty` guard is role-agnostic — so a reviewer's stranded draft used to
  be laundered by a parked card it merely shared a task id with. The two rows now SHARE the role
  conjunct and differ in the other one — the build pair additionally needs the card parked — so
  they are near-mirrors rather than one rule, which is why "we checked the branch we were looking
  at" kept reading as "we checked it": the whole grid — every code × role (build, review, AND a
  role-less entry) × parked — is now written out above `_keep_is_expected` and pinned as a grid,
  and a new `CODE_*` fails that pin until it is graded deliberately. A BUILD tree that is not on
  its own `task/<id>` branch — what an interrupted `git rebase origin/main` leaves: CLEAN, yet
  DETACHED — is refused by BOTH `ensure` (loudly, so a resume agent is never handed a tree whose
  HEAD is not where it is told) and `--release` (`detached-build`, because the unpushed-commits
  guard cannot run on a tree that is off its branch), each naming `git rebase --continue`/
  `--abort` for the AGENT to choose: the tool never picks, since `--abort` discards replayed
  work. An unknown code lands in `kept`: noisy beats quiet. A `released`
  entry can still need action — #517's `branch_deleted: false` + `warning` (the tree went, the
  branch leaked), which is why the rulebook says read `kept` AND scan `released`.
  A CREATE refusal is the OTHER channel and carries no `code` at all — `{"error": …}` + exit 1,
  "the tool could NOT do the work" — measured over every one of them (half-created, detached-build,
  the review `--at` pin, an occupied path, each argument-combination refusal). That is a design
  decision, not an oversight to tidy up (#580 weighed making it uniform and rejected it). A `code`
  exists to feed a GRADER, and `_keep_is_expected` is the only grader there is; on create every
  refusal has the same answer — SKILL.md's «Не завелось — цикл НЕ роняем»: degrade to one slot,
  never stop the loop — so a create-side code would be a public value, spelled in SKILL.md and
  pinned by tests, with no consumer. Nor could "every" be made true there: the `{"error"}` line is
  rendered by a catch-all over an OPEN set (a non-repo, a malformed toml, a git timeout, an
  OSError), so a code could only ever be present-SOMETIMES — worse to parse than absent-always,
  since `payload["code"]` would then pass every test and `KeyError` in production. On create the
  EXIT CODE is the whole machine-readable verdict, and SKILL.md tells agents to branch on that
  split, so blurring it costs more than the uniformity buys.

## VMCP-300 (#1183) — `--gc` DEFERRED three trees and said so NOWHERE, and the silence was the defect

**The observation, and it is not the diagnosis.** On a live drain FOUR worktrees existed and all
four were registered in `.git/worktrees` (`ls` printed them): three review ones — `review-1170`,
`review-1171`, `review-1172` — plus one legitimately live build tree, `task-1179`. All three review
cards had LEFT Review (two moved to Done by the human, one moved to Build by a `needs_work`
verdict), and `vikunja-mcp workspace --gc` answered `{"released": [], "kept": [],
"expected": []}` — three empty lists, nothing removed, nothing reported anywhere. An explicit
`workspace --release <id> --role review` then removed each one cleanly, so this was not a
protective refusal that failed to be reported.

**REPRODUCED FIRST, on a constructed stand, before one line was changed** — this file exists
largely because that step keeps being skipped here. Real git in `tmp_path` plus a `FakeAPI` board
(the workspace suite's own `repo`/`tracker` fixtures): three review trees made with
`ensure_workspace(id, role="review", at=head)` for cards taken to Review and then moved out of it
by both real routes (a human's move to Done; `review_task(verdict='needs_work')`), plus one
legitimately live build tree. One sweep answered, verbatim, `{'released': [], 'kept': [],
'expected': []}` with all three trees intact — byte-identical to the live observation.

**THE CARD'S OWN HYPOTHESIS IS REFUTED, and it was labelled a hypothesis.** It proposed that
`--gc`'s liveness pass may not consider review trees at all. It does: `_read_liveness` builds
`alive["review"]` from `Workflow.review_task_ids`, and on the stand `wf.review_task_ids() == []`,
i.e. the board correctly read all three as dead. Recorded because the plausible-and-wrong diagnosis
is the expensive one here — acting on it would have touched the role-keyed liveness that
`test_gc_keeps_a_quiesced_review_tree_only_because_its_card_is_in_review` was built to defend.

**THE CAUSE is VMCP-71's grace window, reached one branch later.** A tree that is dead by the board
but whose `_last_activity` is younger than `_REAP_GRACE_SECONDS` (30 min) was `continue`d — and
that skip put it in NEITHER list, deliberately and for a reason that was sound as far as it went
(`kept` means "a human should look"; a merely-young tree is not that; #516 had already had to cure
a never-empty `kept`). What makes it bite on the REVIEW side is a property of the WINDOW and not of
liveness, and the MODULE already carried it (this file did not — checked at `HEAD`, the pre-card
dossier holds no mention of VMCP-84, the window, or a young tree): the note above
`_REAP_GRACE_SECONDS` in `workspace_cmd.py` measured
that a read-only reviewer moves neither marker `_last_activity` looks at, so for a review tree the
window runs FROM CREATION. **A review tree therefore reads young from birth** — the exception is
an all-FUTURE set of markers, which the `0 <=` bound refuses to honour — and the three on the real
machine had been created minutes before the sweep. On the stand each read as quiet for a second or
so against a 1800 s window; the figures are run-local, so what the test asserts is the PROPERTY
(`0 <= quiet_for_seconds < _REAP_GRACE_SECONDS`) rather than any of them.

**THE CONTROL, in the same round.** Age every marker past the window and the IDENTICAL sweep reaps
all three (`released` names them; nothing left on disk). So for these trees the reap was POSTPONED,
never cancelled, while the card's other half ("nothing on the board would ever say so") was exactly
true. That matters because a reader who thinks the trees were leaking reaches for the tempting fix,
which is shortening or waiving the window for review trees.

**BUT DO NOT PROMOTE THAT CONTROL INTO "REVIEW TREES NEVER ACCUMULATE" — one round of this card
did, and its own second pass refuted it by construction.** The control measured a CLEAN review
tree. A review tree holding an IN-TREE COMMIT is a different shape and it accumulates for real:
measured on the same stand, three consecutive sweeps past the window each answered
`expected: [(id, "unreachable-head")]` with the directory still on disk, and one holding a stray
untracked file answers `kept: [(id, "dirty")]` the same way. Neither is new and neither is a defect
— `references/drain.md` already says such a tree "stays forever" and that its record grades into
the do-not-look list — but it means the card's accumulation sentence is TRUE for those shapes and
false only for the clean one. The distinction is not cosmetic: it is the difference between "the
window deferred it" (expires by itself) and "a release guard refuses it" (does not), which is
exactly the line `deferred` was added to draw.

**WHAT WAS FIXED: the silence, and nothing else.** `--gc` gained an OPTIONAL fourth key,
`deferred`, present only when non-empty — the `main_checkout` idiom, so "present ⇒ read it". Each
entry names a tree that IS ours, IS dead by the board, and that the sweep chose not to INSPECT:
`{released: false, task_id, role, path, code, quiet_for_seconds, reason}`. `deferred` is to a SKIP
what `expected` is to a REFUSAL — reported, no action, expires by itself.

Why a new key and not a new member of an existing list, since both were considered:

- `kept` is out because its VMCP-68 promise is "empty means nothing to read", and a deferral
  arrives on every tick for up to half an hour per tree, and the number of trees is NOT bounded by
  `wip_limit` — a review tree takes no slot at all, so the worst case is higher than the limit. That
  is precisely #516's never-read-signal disease, and reintroducing it would end with the next human
  turning the guard off — the same price that made HOLDING on unrecognised ignored content a NO.
- `expected` is out by a boundary this module had already written down: it is for a refusal that
  WAS made and is routine, never for a tree gc declined to inspect. A deferral reaches no guard, so
  there is no verdict to grade.
- Optionality does LESS than it looks: it keeps a QUIET tick clean and nothing more — under a
  parallel drain `deferred` will be present on most busy ticks. What actually keeps it out of
  #516's disease is the bullet above — it is a separate key that is explicitly no-action, rather
  than a member of the list a human is told to read in full.

**THE CODE IS `DEFER_YOUNG`, DELIBERATELY NOT A `CODE_*`.** That prefix is the CLOSED vocabulary
`_keep_is_expected` grades cell by cell, so a new member there reddens the pins that ENUMERATE it
until it is graded — right for a refusal, wrong for something that never reaches the grader. How
many pins is TWO — the grading grid and the policy-comment enumeration — against the "three
separate ways" the neighbouring `MAIN_SYNC_*` note claims. Two independent readings, and they
agree. STRUCTURAL, checked here: exactly three tests enumerate `CODE_*` via
`startswith("CODE_")`, and one of them only asserts DISJOINTNESS with another prefix, which a new
`CODE_*` cannot violate. MEASURED, by this card's second independent pass on its own clone: one
bare ungraded `CODE_ZZ`, selection `tests/unit`, 1321 collected — control 0 failed; mutation 2
failed. The inherited number is corrected HERE rather than edited out of the note that landed with
it; what it does not change is the conclusion, which holds at two.
Same reasoning, same shape as `MAIN_SYNC_*`, and pinned the same way by
`test_defer_codes_are_not_part_of_the_graded_worktree_vocabulary` (names AND values, since the
grader keys on values).

**THE SAFETY INVARIANT IS UNTOUCHED, BY CONSTRUCTION AND NOT BY CARE.** Push OK → remove, push FAIL
→ KEEP. The branch this card changed did nothing to the tree before and does nothing now; not one
tree is reaped that was not reaped before, and not one that was kept is now destroyed. That is the
whole reason this shape was chosen over the obvious alternative. **The window is NOT shortened, and
specifically not for review trees** — that would widen the reaper into exactly the VMCP-71 race the
window exists for, on the ONE role whose agent typically writes nothing and therefore has no other
protection at all (VMCP-84 left that exposure documented and open on purpose; it is not reopened
here). A review tree is detached with no branch, so "unpushed" does not mean for it what it means
for a build tree, and that alone should stop anyone widening this without measuring first.

**WHAT STILL REPORTS NOTHING, AND WHY THAT IS CORRECT — the question the card asked and this is the
answer.** Reporting "every skip" is NOT the rule that was adopted. Three skips above the deferral
stay silent, and each is a non-event rather than a deferral: a LIVE tree (there is no news in a
tree that is working, and the ordering that gives it this branch is itself a fix — the `--gc`
card's OWN round-2 review, `66fac88`, before VMCP-68 existed, where a healthy self-tree landed in
`kept` on every sweep); a worktree outside
`worktree_root` (hand-made, not ours — and `workspace_cmd.py`'s own Minor 12a note records, with
its own constructed measurement, that the ABSENCE of a bogus entry for it is the only thing that
guard buys); and a directory under our root whose name is not
`task-<id>`/`review-<id>` (likewise not ours). The rule adopted is narrower and states its own
scope: **report a skip of a tree that is OURS, is DEAD, and that we chose not to inspect.**

**ONE SHAPE CHANGED WHAT IT COSTS, and the direction is deliberately NOT stated as a comparison.**
The `0 <=` lower bound on the window — the guard against an mtime in the FUTURE (clock skew, a
restored backup, an unpacked archive) — used to be described as protecting against "the one
combination that leaks a tree with nothing to notice". After this card that same input leaks
something else instead: a `deferred` line on EVERY tick that can never clear, i.e. #516's disease
in the one shape that does not expire by itself. An earlier round of this card wrote that the bound
is therefore "MORE load-bearing than before", and its second pass sent that back — nobody measured
the two against each other, and they are different failures rather than more and less of one. Both
are unacceptable; the bound stays for either.

**PINNED BY MUTATION.** One selection throughout — the whole of `tests/unit/test_workspace_cmd.py`,
237 collected in every round — run in a clone with `__pycache__` cleared, `PYTHONDONTWRITEBYTECODE=1`
and `vikunja_mcp.__file__` printed each round: control 0 failed / 0 errors; delete the
`deferred.append(...)` report so the skip goes silent again -> 5 failed; make the key unconditional
(`if deferred:` -> `if True:`) -> 4 failed; re-value `DEFER_YOUNG` to a graded refusal's value
(`"dirty"`) -> 1 failed; neuter the grace window itself so the reaper WIDENS -> 9 failed. The last
round is the one that matters for the invariant: a card that only adds a report still owes proof
that loosening the reaper is LOUD, and it is.

**THE RULEBOOK PIN HAS A MEASURED BLIND HALF, recorded rather than left to be inherited.** A second
sweep over `tests/unit/test_skill_contract.py`, 57 collected in every round, same conditions:
control 0 failed; delete the `deferred` bullet from the CORE rulebook alone -> 0 failed, i.e.
BLIND; delete the deferral's code citation from `references/gc-report.md` -> 1 failed; delete the
explanation from BOTH halves -> 1 failed. That is not a defect in the new pin but the SHAPE of
`_gc_section`, which is core + reference on purpose — so read it as "the section as a WHOLE still
explains the key", never as "the tick step still mentions it". The `CODE_*` pin beside it has the
same bound, and inheriting that silently is what the measurement exists to prevent.
