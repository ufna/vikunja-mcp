# How to read the `vikunja-mcp workspace --gc` report

> **A REFERENCE to SKILL.md, not rules of its own.** Read it **when `--gc` returned a non-empty `kept`, a `released` carrying fields, `main_checkout` or an unfamiliar `code`**.
> What is binding lives in SKILL.md itself — what is laid out here are the shapes of the
> payloads, the measured gotchas and the reasons a rule is written exactly the way it is.

     - **`main_checkout` — the same `--gc` FAST-FORWARDS the MAIN checkout, and the key is ABSENT
       when there is nothing to fast-forward.** Why at all: every task lands out of its OWN tree
       via `git push origin HEAD:main`, which moves the shared `origin/<main>` and does NOT move
       the local branch the main checkout sits on — so the folder you were launched from, the one
       the human works in, falls behind monotonically and never catches up (measured on this
       repository: 58 commits over ONE session). The sweep now does a `git fetch` and
       **fast-forward ONLY**. The key reads in ONE direction: **no key — nothing to do** (the
       checkout is current, or the sync is off via `VIKUNJA_MCP_NO_MAIN_SYNC=1`); **key present —
       READ it**, and there are exactly two outcomes:
       * `updated: true` — it moved; `commits` says by how much, `from`/`to` says from where to
         where. It needs NO action, but put a line in your report to the human: files in their
         folder changed.
         **And a LOSS is possible exactly here — read `overwritten_ignored` if it is there.** The
         ff silently overwrites IGNORED files, and `git status --porcelain` shows them neither
         before nor after. In the main checkout that is not exotic: the shared browser resolves a
         bare `filename` against its own MCP server's cwd, which IS the main checkout, so a
         screenshot lands right there under the `*.png` rule — and a rule the human typed into
         their own `.gitignore` and never committed is enough on its own. This is git's own
         behaviour (`git pull --ff-only` by hand does the same), and the decision here was NOT to
         build a guard — so the key is not a refusal but a post-mortem trace: **present ⇒ those
         paths were overwritten by the incoming ones and the human's content is not recoverable
         (two measured caveats — in the `half-applied` breakdown below; the text that names them
         is the `reason` of THAT branch, and the `updated: true` branch's payload has no `reason`
         key at all, so read the caveats here and do not look for them in your own); absent ⇒ NOT
         a proof that nothing was**. **And "present" does not mean COMPLETE — that is the SECOND
         reading direction, not a restatement of the first (VMCP-245 (836): the key was in place
         and was INCOMPLETE, because a nested symlink-to-a-directory sat in `dirnames` while the
         loop read only `filenames` — and it was named NOT ONCE). That case is closed, but a short
         list stays reachable WITHOUT it: the regenerable-name filter drops, for example, a
         hand-written file under `out/.venv/`, and NO field appears beside it at all — there is no
         signal whatsoever; while a walk bounded from above is at least marked by
         `overwritten_ignored_truncated` (understating it: measured, 505 dead files give 500).
         Read the list as a bound on the loss from BELOW: what is listed died for certain, and the
         size of the loss it is not.**
         **Since VMCP-281 (940) this reading has a THIRD key — `overwritten_ignored_incomplete`,
         and it is the only one that says outright "the list is shorter than the truth".** Present
         ⇒ the walk ran into places it could not look into, and everything behind them is named
         neither here nor anywhere else. The number in it is a COUNT OF PLACES, not of files, and
         confusing the two is expensive: one closed directory hides a whole subtree (measured on
         an ENAMETOOLONG stand: at depth 28 one file died, at depth 30 three did, and the walk
         error is EXACTLY ONE in both cases). No arithmetic about the size of the loss follows
         from it. The channel it exists for needs no `chmod` at all: git addresses paths RELATIVE
         to the checkout while the probe walked them ABSOLUTE, so there is a band of lengths where
         git sees a file and destroys it while `os.scandir` gets ENAMETOOLONG. The key arrives
         WITHOUT an `overwritten_ignored` beside it too — and that is precisely the most deceptive
         case: a probe blinded everywhere finds nothing, and the emptiness alone reads as "nothing
         is at risk". It saves no bytes and is not meant to: the human's decision on the card was
         to make the incompleteness EXPRESSIBLE, not to build a guard. There will be NO COUNT of
         the causes here, and that is not laziness: a round ago this said "four" while three were
         listed — and that is TWO different troubles, not one. A miscount in a listing is caught
         by eye; but the cause the card was bounced over was not in the list at all and could not
         have been, because it is of a different kind (see "the NON-mechanical one" below). A
         number here gives a false sense of completeness about both.
         The MECHANICAL give-ups — among others: recognisably regenerable names like `.venv/` are
         filtered out (as with `removed_ignored`); the probe is best-effort and on any failure
         silently returns an empty list rather than taking the sync down with it; and the
         directory walk is bounded from above. **This used to say that it gives up WHOLESALE,
         losing the names already found — with this give-up that is NO longer so, and it is the
         only bound in the list that got NARROWER rather than better documented.** A path git
         refused to answer about (TWO spellings measured: a path beyond a symlink and a path
         inside a SUBMODULE) now costs ONE name instead of the whole report. The price is exactly
         one, and it runs against the habitual reading: **the key being THERE is also not a proof
         that the list is COMPLETE.** Before this, a non-empty list meant "no path failed"; now the
         askable paths are named and the unaskable ones are thrown away beside them — and on very
         large batches the bisect has a call ceiling of its own, and once it hits that it returns
         FEWER names (never extra ones). That does NOT change your action (you named the paths to
         the human, and that is that — there is nothing to bring back either way), but it does
         change what you are entitled to conclude from the key.
         And the NON-mechanical one is a shape of loss the probe does not know; it reaches the same
         emptiness by EITHER of two roads: the ordinary "nothing at risk" branch, and the
         mechanical give-up above. Both look like good news. These are measurements, not a scare
         story: the first road carried, for one round, the case "upstream turns a NAME into a
         DIRECTORY while the human has their own ignored FILE there", the second the case of an
         ignored SYMLINK, where an unrelated file dying in the same commit silently went with it.
         `overwritten_ignored_truncated` has nothing to do with the emptiness at all — on a
         truncation the key IS there; read it as the length of the list BEFORE truncation and NOT
         as the size of the loss: by that point the list has already been through the filter and
         every give-up above, so the number inherits exactly the same blindness as the key itself
         (its neighbour `removed_ignored_truncated` is built the same way). And do not be surprised
         that a named path may match no incoming one: the probe names what DIES, and that can be
         an ANCESTOR of an incoming path, and files inside your own directory. Name such paths to
         the human on a line of their own — it is the only trace there is.
       * `updated: false` — it did not move; `code` says what is in the way. **And there are TWO
         different codes about loss here, not one: a round ago this said "NOTHING was lost", and
         that was FALSE.** `git merge --ff-only` is NOT ATOMIC: it attempts EVERY entry and writes
         everything it can — so one path git could not write (no permission on a directory; the
         Finder "Locked" checkbox, i.e. `chflags uchg`; a full disk) leaves the rest WRITTEN with
         HEAD where it was. It is NOT bounded by order: measured, a file sorting AFTER the failing
         one is written too — what survives is exactly what git could not write.
         `blocked` — git refused, AND THIS RUN FOUND NOTHING half-written. Read it exactly that
         way and not as "the checkout is untouched": it is the FALL-THROUGH branch, and everything
         both probes stayed silent about lands in it. TWO inputs measured on which the checkout is
         nonetheless BROKEN: a checkout half-applied in its TRACKED paths reports `blocked` on
         EVERY later sweep (sweep 1 `half-applied`, sweeps 2 and 3 `blocked`, the tree still just
         as mixed), and a half-apply whose only casualty is filtered out as regenerable detritus
         reports `blocked` from the first sweep.
         **The word "TRACKED" is load-bearing there, and a round ago it was not in this sentence
         (VMCP-252): on the form whose only casualty is an IGNORED file, the later sweeps report
         `half-applied` AGAIN, not `blocked`** — each failed ff attempt unlinks and recreates that
         file, so the fingerprint moves again even though the content there has been upstream's
         since the first sweep. And that holds "while nothing else in the checkout changes", not
         "never": the human deletes the file that has become somebody else's — the path drops out
         of the probe's list, both probes go silent, and the next sweep is `blocked` again
         (measured).
         The typical case — yes, the checkout is intact: the human has uncommitted work in exactly
         the files that came in (the `reason` names them), and on three such refusals git checks
         BEFORE writing (measured). Do not relay that to the human as a guarantee.
         **And `blocked` itself has TWO different `reason`s, and those are what to read, not the
         code (VMCP-258):** "whether it had already written PART of the update could NOT be
         checked on this run" means that at least one of the two halves (the tracked one, the
         ignored one) could not be looked at — and that is NOT "the checkout is intact" but "I do
         not know", exactly like `half-applied`'s `UNCLEAR` below: tell the human that this run did
         not establish the state of the checkout, and let them look at `git status` by hand. The
         difference is not cosmetic: a round ago the predicate was written so that this phrase was
         almost unreachable, and over a genuinely half-applied checkout the FIRST one was printed.
         **And the FIRST one — "nothing half-written was found afterwards — which is what was
         CHECKED" — do NOT read as "both halves were compared and both are empty".** It means
         exactly "both probes were ASKED and both returned empty", and an empty answer from the
         ignored probe is one-way in exactly the way everything else on this path is: it has
         NON-raising give-ups (the regenerable-name filter, the walk and call ceilings), and each
         of them arrives here indistinguishable from "there was no risk". Measured, with no
         injection at all: a half-apply whose only casualty is an ignored file under `.venv/`
         prints precisely this, the reassuring phrase, the file holds somebody else's bytes, there
         is NO key naming the casualty, and `git status` is empty both before and after. This is
         NOT new and not a regression — on the parent `1fb0082` the same stand gives the same
         thing — but it means the first phrase is not a promise about the checkout. It is the same
         input that five lines above is named as a state in which the checkout is BROKEN.
         `half-applied` — THIS one is a loss, and it reads like `updated: true` above and NOT like
         `blocked`. **It has TWO forms, their properties are OPPOSITE, and what tells them apart is
         the `reason` — NOT the absence of the `half_applied` key (VMCP-252: a round ago what stood
         here were the properties of the FIRST form only, served up as properties of the code; and
         the first round of the fix proposed reading the forms off the presence of the key — which
         is also wrong, built and measured).** `half_applied` PRESENT — the failed ff got as far as
         writing TRACKED paths (the key itself lists them, with `half_applied_truncated` on a
         truncation, exactly like the key below): the checkout MIXES two commits, `git status`
         shows the incoming content as the HUMAN's uncommitted work (so they can commit somebody
         else's as their own), this does NOT resolve itself, and only a human cures it — by
         committing or dropping those paths. The key ABSENT — and that is THREE DIFFERENT states,
         which the `reason` names out loud: (1) "Nothing tracked differs from HEAD at all" —
         nothing tracked was touched at all, there is nothing to commit and nothing to drop.
         **Whether the checkout catches up ON ITS OWN — read THAT SAME line to its end, do not
         infer it from the branch (VMCP-252, round 3: this used to say "it catches up BY ITSELF on
         the next sweep", and that is disproved by construction).** An ff that fails PART-WAY also
         puts NEW incoming files on disk without entering them in the index, and this branch's
         probe (`git diff-index`) does not see untracked paths at all — and git then refuses to
         merge over such a file FOREVER. So the `reason` has exactly THREE continuations after
         that, and those are what to read: "This will NOT heal on its own …" — then it LISTS the
         paths, and the human has to remove them (`git status` shows them as `??`), otherwise the
         sweeps will keep reporting `blocked` even after the cause of the refusal is gone
         (measured: sweeps 2-5 `blocked`, HEAD never arrived, deleting the file is what helped);
         "Whether the failed merge ALSO left an incoming path here untracked … could NOT be
         checked" — the probe did not answer, and then promise the human neither healing nor
         breakage, but say to look at `git status` for `??`; or "Nothing TRACKED is left here … no
         incoming path was left behind in a state git would refuse to merge over" — that is when
         the next sweep is expected to finish the ff, and the report itself says WHAT it looked at
         to check that. The middle branch is no formality: the second independent pass created it,
         having built all three paths on which the probe stays silent — before that, a run that
         COULD NOT look printed full reassurance. **And do not branch on a bare "could NOT be
         checked on this run": that substring is also in state (3) below, and their questions are
         DIFFERENT** — there the tracked side was not checked, here what was left untracked was
         not; (2) "UNCLEAR" — the tracked side differs from HEAD, but nothing NEW appeared over the
         merge, and this run CANNOT separate the human's work from what the failed merge wrote over
         (which happens when the human themselves deleted or edited the same path: the set
         difference cuts it out); (3) "could NOT be checked" — the probe did not answer at all. On
         (2) and (3) promise the human neither that the checkout is intact nor that it will catch
         up on its own: say that `git status` has to be looked at by hand. The forms have exactly
         one thing in common: `overwritten_ignored` — the same post-mortem ignored paths as on the
         successful branch, but here additionally FILTERED by the fact of the write (comparing the
         file's fingerprint before and after), because the failed ff did not write everything. The
         one-way reading is the same (present ⇒ overwritten; absent ⇒ NOT a proof that nothing
         was). Both keys appear ONLY non-empty. **On the ignored form this key used to RING ON
         EVERY TICK (four messages for one loss), and now it does not — and a human took that
         decision (VMCP-252).** Each failed ff attempt unlinked and recreated the file, the
         fingerprint moved, and the probe honestly reported an "overwrite" over content that had
         been upstream's since the first sweep. Now, on the REFUSAL branch, a path this run CAN
         prove already holds the incoming bytes is dropped. For you that changes exactly one thing:
         **on the refusal branch the key now names a LOSS and not "something was written here", and
         it arrives ONCE** — you saw it, you named it to the human, and you do not wait for a
         repeat; if the human restores their file and it dies again, a fresh message comes. Three
         things that did NOT change, and the first is the one that gives the key a DIFFERENT
         meaning on the two branches: the `updated: true` branch does NOT know the filter, by
         design, and there it still means "written" and not "written and differed" — so the sweep
         that finally completes the ff will name the path once more (two messages in total, not
         one, and that is not a new loss). Next: an unanswerable read still REPORTS, so a doomed
         ancestor (in the incoming tree that path is not a blob but a directory) is named as
         before. And RAW bytes are compared, not "how git stores them" — otherwise an attribute
         like `text eol=lf`, or a non-invertible clean filter, would have given a match over a
         genuinely changed file (measured, second pass).
         `diverged` — there is a local commit that is not on the remote; `off-branch` /
         `detached` — the human is not on the main branch; `fetch-failed` / `no-remote-branch` /
         `error` — technical.
         **Do NOT fix it yourself**: no `reset`, no `stash`, no `checkout` in the main checkout —
         it is somebody else's working directory, and the human's unsaved work may be sitting
         there right now. That covers `half-applied` too, though the temptation is strongest
         there: the `reason` names the command that would drop the half-written state — but it is
         the HUMAN who runs it, not you. Name the `code` and the `reason` in your report and drain
         on; the sweep will try again on the next tick, and while it refuses the checkout simply
         stays old — an inconvenience, not a failure.
         **There is exactly one exception, and it is `half-applied`:** "stays old" MAY be wrong
         there — the checkout can be BROKEN, and then the sweep will NEVER cure it, even once the
         cause is gone. And TWO different things can break it, not one: half-written TRACKED paths,
         which then block the ff themselves as local changes (sweeps 2 and 3 `blocked`, the tree
         mixed, cured by a human committing or dropping them), AND an incoming UNTRACKED file the
         same failed merge left behind, which git refuses to merge over (sweeps 2-5 `blocked`,
         cured by a human deleting it). Both measured. Say it explicitly, not as a line in a
         general list.
         **WHEN exactly — read the `reason` IN FULL, not the presence of the key and not its first
         phrase (VMCP-252; a round ago this said "not the presence of the key", and that turned out
         not to be enough — the healing phrase itself was wider than its own measurement):**
         "Nothing tracked differs from HEAD at all" only means that the tracked side is intact, and
         whether the ff will arrive is said by the CONTINUATION of that same line — "This will NOT
         heal on its own …" with the paths listed, against "no incoming path was left behind
         untracked either"; "UNCLEAR" and "could NOT be checked" mean that nobody knows that — then
         say exactly that to the human. And the human's ignored bytes are unrecoverable in every
         one of these cases (with the two caveats the `reason` names itself), and THAT is what to
         tell them always.
     - **`kept` — "could not remove it, and that is NOT routine: look".** Take EVERY entry apart by
       `code` (the machine-readable key) and `reason` (the prose): `dirty` (uncommitted work in a
       dead tree), `unpushed` (unpushed commits; in a BUILD tree — on a task that is NOT parked in
       Your Call, i.e. work nobody will come back for, and in a REVIEW tree — whatever the board
       says, see `expected` below), `detached-build` (a build tree that is NOT ON its own
       `task/<id>` branch: most often an interrupted `git rebase origin/main` — a killed turn
       breaks it off exactly like that and leaves the tree CLEAN, but detached; it neither resolves
       itself nor gets swept, and is fixed by the TWO COMMANDS FROM the `reason` inside the tree
       itself — `git rebase --continue` or `--abort` — and that is work for an AGENT, not for the
       human: hand the diagnosis to this task's per-task agent), `half-created` (a half-created
       tree from a killed `worktree add` — only a human fixes it, with the two commands from the
       `reason`), `locked` (the tree was locked by a HUMAN with `git worktree lock` — the work is
       intact and nothing was deleted, but while the lock stands neither `--release` nor `--gc`
       will take the tree down; whoever set the lock removes it: `git worktree unlock <path>` from
       the `reason`. Do not un-hex it yourself and do not reach for `-f -f` — a lock IS "hands
       off"), `populated-gitlink` (the tree holds a SUBMODULE whose directory is NOT EMPTY — the
       breakdown is below, in the `--release` code list), `self-tree` (the tree `--gc` itself was
       launched from), `release-error` (the release attempt failed). A LIVE tree of your own lands
       in neither list — only an already-dead one does.
     - **`released` — the trees that were removed, and TWO fields in them need action:**
       `branch_deleted: false` means that the directory went and the `task/<id>` branch STAYED (the
       `git branch -D` itself failed); the `warning` says what happened and the command that cleans
       up. No work is lost (only a clean and pushed tree is removed), but miss it and the branches
       pile up silently. The second is `removed_ignored`, and it is about a loss that has ALREADY
       HAPPENED (see the next bullet). Success with no loose ends is `released: true` WITHOUT
       either field, **but that reads in ONE direction: the field is THERE ⇒ something was
       destroyed; the field is ABSENT ⇒ NOT a proof that nothing was** (why — in the next bullet,
       "what this field does NOT catch").
     - **`removed_ignored` — what was DESTROYED along with the tree, and it is NOT a warning up
       front but a post-mortem list.** The "uncommitted work holds the tree" guard is `git status
       --porcelain`, and it DOES NOT SEE IGNORED PATHS AT ALL. So a tree where everything is
       committed and pushed but ignored files sit on disk reads CLEAN and is removed together with
       them. Untracked-but-NOT-ignored (`??`) the guard does see AT THE DEFAULT SETTINGS and does
       hold the tree — so the hole is exactly on the ignored ones. **Except for paths under a
       gitlink (a submodule): there `git status` does not answer AT ALL, so the guard sees nothing
       — neither ignored paths nor an ordinary `??`. That no longer ends in destruction (such a
       tree is held by a refusal of its own, `populated-gitlink`, see the code lists), but "the
       guard sees it" is wrong about that area.**
       In this repository what falls into the hole is exactly what this same file prescribes doing:
       `shot-<id>.png` in your tree (the `*.png` rule) and `--output-dir .playwright-mcp/<id>` (the
       `.playwright-mcp/` rule) — measured, both vanish without a trace.
       Now there is a trace: the list of paths in the entry itself. Regenerable build detritus
       (`.venv/`, `__pycache__/`, `.pytest_cache/`, `.ruff_cache/`, `.mypy_cache/`, `.tox/`,
       `node_modules/`, `*.pyc`) does NOT go into it — otherwise the field would hang off every
       tree that passed the gates (and the `uv run pytest` gate creates `.venv` itself: measured —
       3 of 3 live build trees did, and 0 for the single review tree, which had run nothing), and
       people would stop reading it; so the field being THERE = something this list did not
       recognise was destroyed.
       **You have exactly one action, and it is for the future: nothing can be brought back.** If
       you see the field, name the files in your report to the human. The list is TRUNCATED by TWO
       budgets — 50 ENTRIES and 1568 BYTES of serialised names — and either can fire, so a list
       shorter than 50 entries is not "that is how many died" (VMCP-260 (862): entries alone were
       not enough, 50 names of 800 characters each gave a 40 376-byte string — 1.6 times more than
       the read on which a sibling was already losing its daemon session). Truncated by either of
       them — `removed_ignored_truncated` sits beside it — and that is the length of the LIST
       before truncation, NOT the size of the loss. **This used to say "with the TRUE number", and
       that was FALSE (VMCP-249 (840)).** The number is taken AFTER the regenerable-name filter and
       AFTER git has collapsed into one entry a directory whose REPORT is folded up whole
       (**folded ≠ NOT WALKED, and this used to say "one it did NOT enter" — VMCP-274 (897)**: what
       is printed per file is whatever the index holds at least one path under — an ignored `d/`
       with a single TRACKED file inside is printed per file, measured — while git does walk the
       folded directory: `chmod 000` on its only child removes the folded entry itself as well,
       printing `warning: could not open directory`), i.e. it inherits exactly the same blindness
       as the key itself (as with its neighbour `overwritten_ignored_truncated`): measured — a tree
       with a 100-file `.venv/`, a 30-file `.playwright-mcp/840/` directory and 57 separate `*.png`
       destroys 187 ignored files and reports 58. **And it is NOT a bound on the loss from BELOW
       either — "well, at least that many" does not follow from it:** measured, 51 ignored
       directories, each holding one symlink pointing OUTSIDE the tree, gives 51 with ZERO ignored
       files destroyed (the link's target is intact). An entry is what git decided to print, not a
       file. So neither "that many died" nor "no fewer than that" follows from the number; it says
       exactly one thing — how many ENTRIES survived the filter.
       **And "this field is absent" only means "the list was not truncated", NOT "nothing beyond
       what is named died":** under the cap the same collapsing works as before (measured — 2 named
       entries against 32 destroyed files), so truncation does not move the one-way reading from
       the previous bullet in either of its two directions.
       **WHAT THIS FIELD DOES NOT CATCH — know it BEFORE, not after.** (1) A file INSIDE an ignored
       directory: `.venv/MEASUREMENTS.md` dies WITHOUT being mentioned — measured. Usually because
       git collapsed the directory into ONE `.venv/` entry and the detritus filter removed that;
       but **it rests on the FILTER, not on the collapsing** (VMCP-249 (840), round 3 — this used
       to name only the collapsing, and as a universal that is wrong): give `.venv/` one TRACKED
       `pyvenv.cfg` and git prints per file (`!! .venv/MEASUREMENTS.md`, `!! .venv/lib0.py`, …),
       there is no `.venv/` entry at all — and there is still no `removed_ignored` field, because
       the filter looks at the `.venv` COMPONENT in each path. The outcome for you is the same, the
       mechanism is a different one. (2) CLOSED, #766, and kept here because it is the only known
       hole that ever switched the guard off WHOLESALE: the setting `status.showUntrackedFiles
       = no` (at any config layer; a linked tree shares `.git/config` with the main checkout)
       killed BOTH `??` and `!!` IN THE SAME COMMAND — measured on real git, a tree with an
       untracked `REAL-WORK.txt` and an ignored `shot-766.png` returned the empty string, was
       removed as clean, and NEITHER of the two signals fired. The inspection now forces
       `-c status.showUntrackedFiles=normal` on that single call, so the answer no longer depends
       on somebody else's config; at the default setting nothing changed (measured: the same
       refusals, the same counts, the same `removed_ignored`), and a CLEAN tree under that setting
       is still removed as normal. None of that makes the rule below optional: **everything you
       need AFTER the task, carry out of the tree BEFORE `advance(to='review')`** — the screenshot
       onto the card via `attach_file`, the notes as a tracker comment. From that moment the tree
       is dead (see "Check-point early"), and neither `--release` nor `--gc` holds on for ignored
       content.
     - **`expected` — also NOT removed** (every entry is `released: false`, the work is in place),
       **but these are ROUTINE states: do not go into them.** There are exactly two, and both used
       to live in `kept`, which is why it was never empty — and a signal that is never empty stops
       being read. **And both rest on the tree's ROLE, not on the code alone** — each on a role of
       its own, and mixing them up means reading the list backwards. (1) `unpushed` on a BUILD tree
       whose card IS parked in Your Call: `call_human` is called precisely on a rebase conflict or
       a rejected push, and it will hang for hours until the human answers — the card itself is the
       signal; the same goes for such a tree's `dirty`. In a REVIEW tree those same two codes are
       always `kept`, whatever the board says: the justification above is entirely about the build
       agent, while a reviewer's contract is a different one (the verdict as a tracker comment),
       and a parked card excuses SOMEBODY ELSE's unsaved work, not the reviewer's draft.
       (2) `unreachable-head` on a REVIEW tree: the reviewer committed their notes INSIDE their own
       detached tree. THE PIPELINE ITSELF never takes such a tree down — neither `--release` nor
       `--gc` (five sweeps in a row, the directory still there); it ends only with a human stepping
       in, and here are the two measured ways out: remove the tree (`git worktree remove --force` —
       code 0, the directory gone) OR make the commit reachable — `git branch <name> <sha of the
       notes>`: that command writes NEITHER window mark (both the same before and after, whether
       run from the main repo or from the tree itself), so the sweep takes the tree down BY ITSELF,
       into `released`, and the directory is gone — you get the notes out of the COMMIT on the
       branch, not out of the tree. But "the next" sweep — only if the window has ALREADY expired
       AND nothing was forgotten in the tree; both exceptions are measured, and the second is
       disproved by this same note ten lines below. A young tree the sweep skips (it took it down
       only after the window), and a file forgotten in the tree is `dirty`, which keeps holding the
       tree: `git branch` does not affect it at all. Hence a trap almost everybody will fall into:
       the `git status` you look at the tree with beforehand WRITES the index and starts the window
       AGAIN — so right after it the next sweep will do nothing.
       `git reset --hard HEAD~1` is NOT that way out, though it looks like one, and it is wrong on
       BOTH counts at once. It does not make the commit reachable: after it `git branch --contains`
       is empty while `git fsck --unreachable --no-reflogs` lists that commit — the reset merely
       moves HEAD onto the parent, orphaning the notes (the flag is load-bearing here: a bare `git
       fsck --unreachable` does NOT show that commit while the tree's directory stands and anchors
       it with its own reflog). And it writes the INDEX — one of the window's two marks — i.e. it
       extends the window FOR ITSELF: four sweeps in a row straight after it did nothing, and the
       tree came down only when the window had expired AGAIN. One mark is enough, so the shape of
       the notes changes nothing: committing a file from the tree's ROOT moves both marks,
       committing from a SUBDIRECTORY moves the index only, and the window is extended the same
       either way (both measured; what those marks are is in the bullet below).
       And a record is NEVER permanent: it comes only when the card stands OUTSIDE Review (while it
       is IN Review the tree is alive BY ROLE and the sweep does not touch it at all — checked both
       on a fresh tree and on one that had been standing; put the card back into Review and the
       record vanished again) AND the last write in the tree is older than the grace window (see
       the bullet below). Both true — the record comes on every sweep launched FROM OUTSIDE that
       tree (a sweep from inside it reports `self-tree` about it, and a file forgotten in the tree
       reports `dirty`; either of those overrides it, in `kept`), and by itself it will not end:
       there is no step in the pipeline that would clear it. In (1) such a step does exist — the
       human's answer — but not just any: what closes the record is only a card that came back to
       Design/Build WITH THE SAME ASSIGNEE (then the tree is alive again and there is no record at
       all); an answer after which the card is NOT active behind you (the assignee was removed —
       measured; it was taken to Done/Backlog) does not close the record but moves it from
       `expected` into `kept`, because the grading requires the card to be in Your Call
       specifically. What cures this is not the human but the reviewer's rule — the verdict as a
       tracker comment, not as a commit in the tree. Routineness here rests on the ROLE, not on the
       code: the "these are the reviewer's notes" justification does not apply to a build tree, so
       `unreachable-head` on a build tree would go to `kept`. In practice a build tree no longer
       reports that code — a detached build tree is cut off earlier and arrives as `detached-build`
       (see `kept` above) — but the role stayed in the grading as a backstop.
     - **`deferred` — an OPTIONAL key, and it is NOT a list of trees the sweep failed on.** Present
       means the sweep DECLINED TO INSPECT those trees: each one is dead by the board but was
       written in less than a grace window (30 min) ago, so gc left it alone in case its agent is
       still standing in it. Nothing was inspected, nothing was refused, nothing was removed — no
       guard ran at all, which is exactly why these are not in `kept` or `expected` (`expected` is
       for a refusal that WAS made and is routine). **NO ACTION: a later sweep INSPECTS the tree**,
       and removes it unless a release guard then refuses (a stray file -> `dirty` in `kept`, an
       in-tree commit -> `unreachable-head` in `expected`). Entries carry the code `young`,
       `quiet_for_seconds` and the usual `task_id`/`role`/`path`. The `DEFER_*` codes are a
       separate vocabulary from the `CODE_*` refusals for that reason and never reach the grading.
       Why it exists at all (#1183): this used to be a SILENT skip, so a sweep that declined three
       trees answered `{"released": [], "kept": [], "expected": []}` — byte-identical to a sweep
       with nothing to do. That was observed live on three review trees whose cards had all left
       Review, and read, reasonably, as a reaper that had stopped working. It had not. It is NOT a
       review-only effect — a build tree dead at `advance(to='review')` is deferred in exactly the
       same way — but a review tree reaches this state from BIRTH, because a reviewer who only
       READS moves neither mark the window is measured from, so its count runs from creation.
       Do NOT "fix" a `deferred` entry by removing the tree by hand, and do not read a run of them
       across consecutive ticks as an accumulation: measured, a CLEAN tree is reaped by the first
       sweep after the window expires. A tree that turns out to hold work is a different story and
       gets a different key — `kept`/`expected`, and THOSE do stand indefinitely (a review tree
       with an in-tree commit is refused forever; see `unreachable-head` above).
     - An unfamiliar `code` lands in `kept` and not in `expected` — deliberately: better to call the
       human once too often than to lose a record quietly.
     - **A standing record arrives on EVERY sweep (a sweep = one `--gc` per tick) while nobody is
       writing in the tree.** The count runs from the LAST WRITE in the tree, not from the task's
       death: a tree the sweep already considers dead but written to less than a grace window
       (30 min) ago it does not inspect at all, and reports it in `deferred` rather than in either
       verdict list (before #1183 it was skipped SILENTLY, in no list at all); the rest of the
       time `dirty`,
       `unpushed`, `unreachable-head` and any other code arrive on every consecutive sweep, and the
       sweep itself does NOT extend the window (`--gc` does its own inspection without
       optional-locks and does not touch the index; it used to — `git status` rewrote the index,
       and the record was visible roughly once per window). Two non-obvious facts follow from
       "since the last write", both measured. First: a tree that stood quiet LONGER than the window
       WHILE the task was alive lands in the list on the VERY FIRST sweep after its death — there
       is nothing to wait for, the window had already expired by then. "Longer than the window" is
       load-bearing here, not a turn of phrase: a tree written to seconds before the card died was
       not looked into at all by the first sweeps after it — the record came only once the window
       had expired (both cases measured). Second: "a write in the tree" is not just any edit. The
       window is moved by exactly two marks, the ones `--gc` looks at: THE DIRECTORY ITSELF at the
       tree's root — not the contents of the files lying in it, but its own table of names (a new
       file beside README, a `.pytest_cache` directory, a deletion, a rename — including ONTO an
       existing name) — and the index (`git add`/`commit`/`rebase` — and ONE `git status
       --porcelain`, even in a clean tree). So the window's silence is not an alarm but a
       protection against the directory being swept out from under a live agent — an INCOMPLETE
       protection, though, and the hole is WIDER than it looks: editing an EXISTING file moves
       neither mark — not in a SUBDIRECTORY (the most common thing an agent does) and not in the
       root itself (an edit, an append and a chmod were measured) — so "I am editing files in the
       root" does not hold the window for you, and the record about such a tree arrives on the very
       next sweep (measured on a standing record: edit a file in the root, and it is still there on
       both following sweeps). The converse also misses intuition: saving OVER a file in the root
       through a temporary file (the atomic write many editors do) does move the mark — a rename
       rewrites the directory entry even when the name stays the same, and by the SET of names that
       is not visible at all (measured with the temporary file in a SUBDIRECTORY: in the root not
       one name appeared or disappeared, and the mark moved). This loses no work, but exactly to
       the extent that git sees it: `dirty` is computed from `git status --porcelain`, which does
       not show IGNORED paths — measured, the sweep took down a clean and pushed tree together with
       the `secrets.env` and `scratch/notes.txt` lying in it. Whatever is not under git and you
       need afterwards — carry it out of the tree BEFORE `advance` (with the task id in the name,
       see "Shared resources"): "let it live in your own tree" is a rule about a LIVE tree, and
       this one is already dead. And the directory can be taken out from under you — so after
       `advance(to='review')` do not assume you are still standing in it, however much you keep
       typing in it.
