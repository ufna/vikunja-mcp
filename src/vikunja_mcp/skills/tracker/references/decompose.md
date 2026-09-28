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
  into your diff: file `file_task(title, breaks, description?, priority?, related_task_id?,
  queue?)`. **`breaks` is required (#1987)**: one sentence naming what goes wrong — for a user,
  an agent or a tool run — if the finding is never fixed; it becomes the first line of the card,
  and the call refuses without it, nothing created. Cannot name one? Then it is not a finding
  worth a card — see the THRESHOLD below. The task lands in Backlog (NOT Queue — a human
  prioritises) with a `[filed-by-agent]` marker; pass the `related_task_id` of your current task to tie the finding to its context.
  This is orthogonal to decompose: decompose splits YOUR big task into subtasks in
  Queue, `file_task` parks a finding that belongs elsewhere in Backlog for a human to triage.
- **`icebox=True` when a BEHAVIOUR defect is real but nobody will ever prioritise it** (#1640) —
  a minor misbehaviour in legacy code nobody maintains. The card goes to the `Icebox` column with
  the `icebox` label instead of Backlog, so Backlog keeps meaning "a human still owes this a
  decision". `breaks` is required here too. **Icebox is NOT for text** — a wording, a stale
  figure, a nit in a comment is dropped, not frozen (#1987): a freezer that accepts everything
  only moved the flood one column to the right. Do not freeze work you simply did not want to
  do, and say in your report that you froze something and why.
  It is refused together with `queue=True` (opposite instructions), and it IS allowed
  cross-project where `queue` is not — their Queue injects work their human never sanctioned,
  their Icebox wakes nobody. On a board created before the freezer existed the call refuses with
  NOTHING created and names `vikunja-mcp setup`; file without `icebox=True` to reach their Backlog.
- **The THRESHOLD for filing: a finding about TEXT is DROPPED — no card, no Icebox, no comment**
  (#1987, replacing #902's "comment instead of a card"). Text means prose anywhere: comments,
  docstrings, SKILL.md, CLAUDE.md, dossiers, reports — a wording, a stale figure, a claim wider
  than its measurement, two paragraphs that disagree, a blind spot in a gate that checks prose.
  The one exception is text that would make an agent or a human DO THE WRONG THING — run a wrong
  command, take a wrong branch, draw a wrong conclusion from a tool's answer. That is a behaviour
  defect in disguise: fix it in your own diff if it is in your slice, or file it with a `breaks`
  that names the wrong action.
  - **Why dropping, not recording.** Every recording channel was tried and each one only moved
    the flood: a card per finding made Backlog not converge (13 cards -> 11 landings, 10 filed ->
    17), the comment threshold (#902) and the freezer (#1640) kept the stream and changed its
    address. The class reproduces itself — a fix is new text, and the next careful pass measures
    that one — so the only cut that works is at the source.
  - **SCOPE: a finding in YOUR OWN not-yet-delivered text you fix in the same diff**, as before;
    this rule is about findings outside your slice.
  - **BOUNDARY: behaviour is filed as before, regardless of size** — a gate that does not refuse,
    a tool that moves a card to the wrong place, the wrong branch in `next_task`.
  - **What it does NOT cancel:** independent review of behaviour changes. What changed is where
    a prose finding goes (nowhere), not whether real defects get checked.
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
