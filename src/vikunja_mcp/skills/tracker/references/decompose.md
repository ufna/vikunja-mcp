# Decomposition and filing findings

> **A reference for SKILL.md, not rules of its own.** Read it **when a task does not fit into one go or you found something outside it**.
> What is BINDING lives in SKILL.md itself — what is worked out here is the response shapes,
> the measured gotchas and the reasons a rule is written exactly the way it is.

## Decomposition and filing findings

- **`decompose` is about YOUR task.** A task bigger than about half a day of work, or made of
  several unrelated themes — `decompose` it into subtasks (each one an independently verifiable
  result). The subtasks go into Queue, the parent leaves for Backlog as an epic.
  **`ordered` is not cosmetics, it is a decision about parallelism.** `ordered=False` (the default)
  does not mean "the order does not matter", it means "these subtasks can be built AT THE SAME
  TIME, by different agents in different trees" — at `wip.limit > 1` the orchestrator will do
  exactly that, and two agents will start editing one file off one base. If you are unsure whether
  the subtasks touch the same code (or whether the second one leans on an interface from the
  first) — set `ordered=True`: the `precedes` chain releases the next subtask exactly when the
  previous one has reached Review, and by that moment its commit is ALREADY in the main branch
  (the push is part of the move to Review), so the next one will take its own tree off a base where
  the predecessor exists.
  **It REFUSES from THREE stages — Review, Done and Icebox — and works from the other five**
  (Backlog, Queue, Design, Build, Your Call). "Works" here is about the STAGE: the ownership
  guard is in place there too, `decompose` will not break up someone else's card from any of the
  five. From Icebox (#1640) because splitting a frozen card puts its CHILDREN IN QUEUE, which
  hands work a human deliberately froze straight back to the fleet — measured, and `next_task`
  offered the first child on the very next call. Do not read "the card had an assignee, so
  somebody must have meant me to work it": dragging a card in Vikunja does not clear assignees,
  so a card frozen mid-Build still carries yours. From
  Review (#663) — because the decision "this work has to be split" is taken in Build, not over a
  card that is already being reviewed. Measured: before the gate `decompose` took a card standing
  under review off to Backlog with no assignee, with the `epic` label and two new children in
  Queue, and an APPROVED one (the `reviewed` label, waiting for a human Done) — straight away with
  `reviewed` and `epic` AT THE SAME TIME; that is the same shape #590 closed at `return_task`. If
  you saw in review that a task has to be split — send it back through
  `review_task(task_id, verdict='needs_work', report=<why it must be split>)`: the card returns to
  the IMPLEMENTER in Build, and `decompose` is done by them, its owner (a human can also return a
  card to Build by hand). A finding outside the card's slice — `file_task`, but not automatically:
  see "the THRESHOLD for filing" below in this same section. From Done (#649) — because the card
  was put there by a HUMAN, and the way back out of Done is theirs too: "only a human moves things
  into Done" holds in BOTH directions. This is the second half of the same bypass #626 closed at
  `return_task`, and it is measured that before the gate `decompose` took a card the human had
  accepted off to Backlog with no assignee, with `reviewed` and `epic` AT THE SAME TIME and two new
  children in Queue — the board claimed that accepted work had become a half-assembled container.
  Work that an accepted card revealed is NEW work, not a split of the current one: file a
  `file_task` (`related_task_id` pointing at it) for the human to triage; `call_human` from Done
  refuses too, and only a human can put the card itself back into work by hand.
- **The life cycle of an epic (a container, not work).** A parent with the `epic` label is a
  container: `next_task` does NOT offer it, `claim` refuses (work on the children, not on the
  container). An agent cannot and must not move an epic through the stages. When the LAST child of
  an epic reaches Review, that child's `advance` itself hangs the `epic-ready` label on the epic
  and an `[epic-ready]` comment (a best-effort side effect: it adds nothing to your payload and
  does not fail your advance, even if the write to the epic falls over) — so a human sees an
  assembled container at a glance. From there the whole set (children + epic) is taken to Done by
  the HUMAN — only they move things into Done. If you bounced a child back out of Review, the
  marker can go stale, and the human will see that.
- **`file_task` is about a FINDING outside your task.** If along the way you run into a bug or
  tech debt that does not belong to the current task — do not fix it silently and do not drag it
  into your diff: file `file_task(title, description?, priority?, related_task_id?, queue?)`.
  The task lands in Backlog (NOT Queue — a human prioritises) with a `[filed-by-agent]` marker;
  pass the `related_task_id` of your current task to tie the finding to its context.
  This is orthogonal to decompose: decompose splits YOUR big task into subtasks in
  Queue, `file_task` parks a finding that belongs elsewhere in Backlog for a human to triage.
- **`icebox=True` when the finding is REAL but nobody will ever prioritise it** (#1640) — cosmetic
  legacy, wording, a nit in code nobody maintains. The card goes to the `Icebox` column with the
  `icebox` label instead of Backlog. The point is what it protects: Backlog means "a human still
  owes this a decision", and a stream of findings nobody will ever pick makes that promise false,
  so the freezer is where you put the ones you would otherwise be filing into oblivion.
  Two ways to get this wrong, and they pull in opposite directions. Do NOT freeze work you simply
  did not want to do — the test is whether a reasonable human WOULD prioritise it, not whether you
  would enjoy it. And do NOT treat filing there as having dealt with the finding: say in your
  report that you froze it and why, so the human can disagree while it is still cheap.
  It is refused together with `queue=True` (opposite instructions), and it IS allowed
  cross-project where `queue` is not — their Queue injects work their human never sanctioned,
  their Icebox wakes nobody. On a board created before the freezer existed the call refuses with
  NOTHING created and names `vikunja-mcp setup`; file without `icebox=True` to reach their Backlog.
- **The THRESHOLD for filing: a finding about PROSE becomes a CARD only if it changes what the
  reader WILL DO. Otherwise — a COMMENT on the card whose text is under discussion.** The QUESTION
  itself is not new — "change not a single decision of the reader" already stands in the stopping
  criterion of the second pass (the section "A second independent pass over YOUR OWN text"). But do
  NOT carry that rule over here wholesale: there this question is ONE OF THREE conjuncts, next to
  "not attribution" and "already covered by the neighbouring text", and it decides whether to turn
  another round; here it stands ALONE and decides whether to file a card. Ask it literally: having
  read the corrected text, will an agent do SOMETHING DIFFERENT — a different command, a different
  branch, a different conclusion out of a tool's answer? Yes — a card. No (the wording is more
  precise, the example more vivid, two neighbouring paragraphs argue with each other but the action
  out of both is one) — `comment` on the card whose text you are discussing: `get_task` returns all
  comments, so the finding will be seen by the next one who opens THAT CARD. The road "text → card"
  is exactly one and implicit — `git blame` down to the commit and the `(tracker #N)` trailer in
  it; the file itself does not promise it.
  - **SCOPE: the rule is about a finding you are ABOUT TO FILE, that is, one OUTSIDE your slice.**
    A finding in YOUR OWN not-yet-delivered text you fix IN THE SAME diff — "I will comment
    instead of fixing" is not something the threshold permits, and the second pass is not
    shortened by it. From the same session: the blocking finding of the second pass over #874 left
    as a SECOND COMMIT on the same card (`ad2a77a` → `a44c4c7`), not as a comment and not as a new
    card. And a card on SOMEONE ELSE'S board is unreachable by comment — `comment` only travels
    within your own project, so there it is still `file_task(project_id=…)`.
  - **Why there is a threshold — one drain session's accounting of the HUMAN (2026-08-06), and it
    is about the DYNAMICS, not about quality.** 13 cards in Backlog at the start, 11 substantive
    landings, 10 cards filed, BACKLOG became 17 — the work DOES NOT CONVERGE. In the landed diffs
    1095 added lines, of them 640 (58 %) prose (comments and docstrings). Five of the ten filed
    are pure prose and pins. Not one of THOSE findings was false: the chain works, what is bad is
    the DYNAMICS — the class "text A contradicts text B" and "a claim wider than its measurement"
    REPRODUCES ITSELF, because a fix is new text, and the next careful pass measures that one.
    **The numbers are NOT re-derivable, neither from git nor from the board** — this is manual
    accounting for that session, not a slice of the tree: that day 20 commits landed in the main
    branch (bumps excluded), and no window of 11 in a row gives 1095. Do not "refine" them by
    recounting — they move only together with a new measurement by the human.
  - **BOUNDARY: the rule is about PROSE. A finding about BEHAVIOUR is filed as a card as before,
    regardless of size.** A gate that does not refuse; a tool that moves a card to the wrong place;
    the order of branches in `next_task` — that is behaviour, and a one-line diff softens nothing
    here. The threshold touches the class that produced it, and that class is DOUBLE: both "a claim
    wider than its measurement" and "text A contradicts text B" — that is, not only the declared
    measurement, but also the text's consistency with the text beside it.
  - **What the threshold does NOT cancel — and that is part of the decision, not a caveat.**
    Neither the second independent pass, nor the independent review. Both paid off in the SAME
    session the threshold was counted on: the second pass caught the blocking defect of #874
    EARLIER than review, and review caught, in #860, a loud loss of the report being replaced by a
    quiet false report. What is being cut is the SOURCE of cards, not the checks: run the checks
    exactly as before, and put their findings into a comment if they do not change what the reader
    does. If you read this threshold as permission not to call a second pass or not to review —
    you read the wrong thing.
- **`queue=True` — ONLY when a human explicitly asked for a task to be filed into work**
  (an answer on a Your Call card, a direct "file a task for X" in chat or in comments): their
  instruction IS the triage, the card will land straight in YOUR project's Queue — unassigned,
  immediately claimable by any agent. NEVER file your OWN findings into Queue — their road is
  Backlog (the default), a human prioritises. It does not combine with a cross-project `project_id`
  (refusal, nothing created): someone else's Queue is not yours to fill — their Backlog is triaged
  by their human.
- **The finding lives in SOMEONE ELSE'S project/repo — file it straight into their Backlog.** If
  the fix is needed on another project's side (its repo, its agent), pass
  `file_task(..., project_id=<id of the target project>)`: the card will land in the TARGET
  project's Backlog (their human triages it), the `[filed-by-agent]` marker will name your project,
  and `related_task_id` will tie it to your current task across the project boundary — this is the
  agent→agent coordination channel. Do not fix someone else's repo in your diff and do not park
  someone else's work in YOUR Backlog. Take the target project's id from the task context or from
  the human; if you do not know it — `call_human`, do not guess. If the token has no access — you
  get a clear refusal (the boundary is the scoped token itself), the card is not created. Your
  `get_task`/`comment` will not see the card you filed (it is on someone else's board) — the trace
  that stays with you is the `related` link.
