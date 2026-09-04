# Stuck, and what happens after Review

> **A reference for SKILL.md, not rules of its own.** Read it **when you cannot move any further, or the card has already gone to Review**.
> What is binding lives in SKILL.md itself — what is worked through here is the shapes of the
> answers, the measured pitfalls, and the reasons a rule is written exactly the way it is.

## Stuck? The way out depends on your ROLE

- **`call_human` is the only channel for questions to the human.** You need a decision or
  an input (a choice between options, access to a secret, a product decision) — file a
  card via `call_human`, do NOT ask at the console. The orchestrator lives under `/loop`
  and the human is not at the console: a question into the chat, `AskUserQuestion`, a plan
  submitted for approval (`ExitPlanMode`) or "I'll ask and wait" simply hangs unanswered.
  Every question to the human goes into a card and nowhere else. In the question: what you
  need, which options you considered, what you recommend. The task stays yours; it moves
  to Your Call.
  - **Do not block after `call_human`.** The question has gone into a card — do not wait
    for an answer within this same tick: go to `next_task` for the next task (Your Call =
    parked, not your active one); on an empty queue, yield the turn until the next tick
    (see "The queue is empty").
  - **The answer comes back on its own.** The human answers with a comment and moves the
    card back to Design/Build BY HAND; on the next tick `next_task` hands it to you as
    "your active one" with the answer on it — you read the comment (`get_task`) and carry
    on. You neither need to move the card out of Your Call nor have anything to move it
    with — the human does that.
  - **YC = Your Call** — the column `call_human` leads to. When the human asks you to
    "move (throw) the task into YC / into Your Call", do it through `call_human` and
    nothing else (with the question as context). There is no separate `advance` route into
    that column.
- **`return_task`** — an external blocker (someone else's service is down, a dependency is
  missing, the task has lost its point). The claim is dropped and the task goes to Backlog
  for re-triage. **It REFUSES from TWO stages — Review and Done — and works from the other
  six** (Backlog, Queue, Design, Build, Your Call, Icebox). "Works" here is about the STAGE: the
  ownership guard is in place from an open stage too, so it will not hand over someone
  else's card from any of the six. On Icebox specifically, see the same note in
  `references/decompose.md`: it is open rather than gated because a card there is ownerless by
  definition, so you can only reach it if a human deliberately assigned it to you.
  - **From Review** (a stage gate, #590): dragging a card that is standing under review
    off to re-triage is not "stuck", it is carrying someone else's finished work out of
    the pipeline.
  - **From Done** (#626): a card in Done was put there by the HUMAN, and the move back out
    of Done is the human's too, not yours: "only the human moves a card to Done" holds in
    BOTH directions. Measured: before this gate `return_task` dragged a human-accepted card
    into Backlog with no assignee and with TWO labels at once (`reviewed` + `blocked`),
    while `advance` in all three forms, `call_human`, `claim` and `review_task` in both
    branches all refused. That pair is measurement #626 and NOT what you get today: since
    #693 the verdict is cleared BEFORE `blocked`, and with the gate removed the card
    arrives with the single label `blocked` (measured). The gate is no weaker for it — the
    human's acceptance would now not "contradict" the board, it would be ERASED from it,
    and that is exactly what the refusal says. The work in Done turned out to be no good —
    file a `file_task` (the next card, `related_task_id` pointing at this one) for the
    human to triage; `call_human` from Done refuses too, and only the human can bring the
    card itself back by hand.
    **Since #662 this is ONE RULE, not a habit of each individual tool, and something
    practical follows from that.** Human-only Done used to be written down nowhere as a
    single statement: four tools derived it from their own starting stage, and
    `return_task` and `decompose` each carried a personal gate (#626/#649) — so the next
    mutating tool that moves a card and does not check the stage would open the hole again,
    and there was nothing to catch it with. Now the guard sits at the shared point
    (`_find_task`), and the two personal gates that had gone dead are deleted. What YOU
    see: from Done **every** tool refuses with the SAME TEXT — it names the rule itself and
    the door that does work (`file_task`) — so working through the tools after the first
    refusal is pointless, the answer will be the same. READING an accepted card is still
    possible: `get_task`, `comment`, `attach_file` and `download_attachment` work from Done
    deliberately, and that is pinned.
    **What this does NOT mean:** the hole has not become inexpressible — the guard can be
    removed — and the rule is still exactly about Done, not about "the stages a particular
    tool does not move a card out of" in general (`decompose` has TWO such stages: Done and
    Review, and the second is held by its own gate #663).
- **The card is YOURS and a tool says "not assigned to you" — that is NOT your mistake.
  Since #885 the shape usually heals itself; if it did not, just RETRY first, and only then
  read about re-filing.** The shape was measured live (#885, project 10, 2026-08-06):
  `claim` reported success, `get_task` showed `stage: Design` and you among the assignees,
  but the copy of the task IN THE KANBAN VIEW arrived with an EMPTY `assignees` — and that
  is the copy every ownership gate judges by. BACK THEN, before the fix, not one tool could
  MOVE the card: `advance`, `call_human`, `return_task` and `decompose` refused alike — that
  is, there was nothing left even to ask about it with, because `call_human` ON THAT VERY
  CARD is one of the refusers. **But "unworkable by ANY tool" is an overstatement, and a
  round ago that is exactly what stood here:** `get_task`, `comment`, `attach_file` and
  `file_task` require no ownership and always work on such a card (measured), and `get_task`
  even shows the real assignee — that is what the `file_task` workaround below rests on. And
  the "retry" in the heading is there because the re-read is best-effort: it gives up on any
  network error, and that state is TRANSIENT (measured: the first call refused, a plain
  retry gave `Build`, and nothing had to be re-filed).
  **It does NOT hold a WIP slot meanwhile — it LOSES one, and a round ago the exact
  opposite stood here.** `_my_active_tasks` counts off that same board copy, so for the
  counter such a card does not exist at all. Measured at `wip_limit = 3` (three cards
  claimed, ONE collapsed): the gate returns `{'active': 2, 'limit': 3, 'free': 1}`, whereas
  you really have THREE in Design/Build — and a FOURTH `claim` PASSES, leaving four against
  a limit of three (the healthy control turns it away: "WIP limit reached (3/3)"). **And in
  Design/Build `next_task` does NOT hand such a card back** (`task=None`; on the healthy
  control it is `task=<id>`, `resume=True`), so nobody will replace a per-task agent that
  died on it ON THEIR OWN: the rule "a fallen build agent reminds you of itself" does not
  work here — name the id of such a card to the human in your final report. **In Review that
  skew is GONE, and a round ago the opposite stood here.** Back then the review-offering
  branch skipped cards assigned to you, judged that off the same board copy — and a
  collapsed card WAS OFFERED to its own author, whereas the healthy control answered
  `task=None` (measured). Since #991 the skip is conditional on
  `require_review_independence`, which is false by default: BOTH are offered, and there is
  nothing to tell apart here. The skew survived exactly where the flag is ON — the collapsed
  copy hides authorship from the offering branch, the card arrives at its author, and
  `review_task` will not let a verdict be delivered (it re-reads `/tasks/<id>`). The first
  two leftovers are PRE-EXISTING and outside #885's mandate: they do not need fixing, what
  needs doing is not describing them backwards.
  - **"Your own review is not a review" no longer means "do not deliver a verdict".** With
    the flag off, being offered your own card is the normal solo mode, not a symptom:
    independence there is carried by FRESH CONTEXT, not by a separate identity
    (`review_task` accepts such a verdict). Dispatch a separate sibling reviewer — never the
    one who wrote the code — and deliver the verdict. Staying silent here is WORSE than
    being wrong: a card without a verdict comes back on EVERY tick, and an external
    supervisor will keep booting an agent for it as long as there is no verdict.
  - **First check that you are in this dead end at all and not the one next door.** Since
    #885 the gates re-read the task via `/tasks/<id>` when the board copy is empty, so TODAY
    this shape usually heals itself and you never see it. The sign that it happened: `claim`
    returned the key `kanban_assignee_divergence` — read it and name it in the `[worklog]`.
    A "not assigned to you" refusal WITHOUT that key is most likely the other edge: an
    ownerless card the human put there by hand (see "After Review"), or somebody else's
    work.
  - **If a tool still refuses on a card that `get_task` says is yours** — you cannot fix it
    yourself: the state is measured to be DURABLE rather than a race, and it is cleared
    neither by re-assigning the assignee, nor by moving the card between columns, nor by a
    full rewrite of the task itself. The only known cure is to **file a NEW card with the
    same content (`file_task`, `related_task_id` pointing at the stuck one) and say in it
    that the original is stuck**; the original is taken to Backlog by the human. Why
    `file_task` and not `call_human`: the latter works from Design/Build and requires
    ownership — on a stuck card it refuses with exactly the same text. That is what was done
    with the live #854 → VMCP-270 (886).
  - **Why this is not fixed "more properly":** it is the server itself that loses the
    assignee in the copy, and why is a question for Vikunja, not for these tools. What was
    done here is to make the gates robust to the shape; the cause does not reproduce. The
    divergence is rare: the same day, checking the board copy against `GET /tasks/<id>`
    across all 31 cards outside Done gave EXACTLY ONE.
- **For the REVIEWER both ways out above are dead — the reviewer has one of their own, and
  only one.** The reviewer is the only role that works EXCLUSIVELY from Review, and from
  there `call_human` refuses, `return_task` refuses — and so does `decompose` (gate #663).
  The last one is not among "the ways out above" (it is not about "stuck" but about "this
  needs splitting"), but it is named here because in THIS section this bullet is the only
  thing written for the reviewer — and this is where the reviewer will look. (Your ROLE is
  covered in other places in the file too: "Independent review of changes" and "Having cast a verdict, the reviewer releases its own tree".) There is deliberately NO
  number in that enumeration: exactly as it stands here it has already LAGGED behind the
  gates — gate #663 arrived and the enumeration stayed as it was. Not one word became false
  in the process (what was said was "both ways out above", and `decompose` was never among
  them) — the reviewer simply acquired a route this text was silent about, and that was
  enough. And "how many tools refuse" is a question with no ONE answer at all: a sweep of
  all 14 agent tools on a card in Review gives SEVEN (`claim`, `advance` in all three forms,
  `call_human`, `return_task`, `decompose`, `handoff`, `transfer_task`), but `claim` and
  `advance` are not doors for the
  reviewer, so "three" is a narrowing too, just one nobody has spelled out. Hold on, instead
  of a counter, to the measured ONE, which the new gate does not make stale: EXACTLY ONE
  agent tool walks a card out of Review —
  `review_task(verdict='needs_work')`; `approve` does not move it at all, and every other
  tool either refuses or leaves the card where it is. (Both numbers, SEVEN and EXACTLY ONE,
  are held by `test_exactly_ONE_agent_tool_walks_a_card_out_of_Review` — they will not go
  stale silently; that is why they are written here. Under multi-identity the card is not
  yours either — no need to claim it, and there are still seven refusals: only their reasons
  change.)
  You need a decision from the human in the middle of a review — the question goes into
  `review_task(task_id, verdict='needs_work', report=<the question>)`: the card goes back to
  the IMPLEMENTER in Build, and being its owner they call `call_human` from Build
  legitimately — Your Call (and a ping to the human, if a webhook is configured for the
  project). "This needs splitting" is the SAME call, only `report=<why it needs splitting>`:
  `decompose` is called by the IMPLEMENTER from Build, its owner (details in the `decompose`
  bullet of the section "Decomposition and filing findings"). Two steps, but the pipeline is
  intact — at the price of a `review-failed` label: the question is dressed as "needs work",
  because there is no other way to return the card to its owner.
  - **Do not shorten them into one by parking the card yourself.** `call_human` from Review
    is not gated "for tidiness": its body moves the card into Your Call, and from Your Call
    `review_task` refuses ("only tasks in Review can be reviewed") — both branches, approve
    and needs_work alike — that is, your verdict would die together with your question. On
    top of that the card would leave Review, and the liveness of your review worktree is
    counted by exactly "the card is in Review" (see "Having cast a verdict, the reviewer releases its own tree") — the TREE would become dead to `--gc` that same second
    (the directory does not disappear instantly and not always: there are a grace window and
    guards of its own there). And `call_human` PRESERVES the assignee, which means DIFFERENT
    things in the two setups: under MULTI-IDENTITY the assignee in Review is the
    IMPLEMENTER, and `next_task` would hand the human's answer to THEM, and to you — never;
    under SOLO there is one token, so the card would come back to you as "your active one"
    in Build and would take a slot — and there would still be nowhere left to write the
    verdict.
  - **A finding outside the slice of the card under review is a `file_task`, not a verdict
    and not a return.** The verdict is about this card; a `needs_work` carrying an unrelated
    finding sends the implementer off to fix somebody else's thing. **But `file_task` is not
    automatic here: a finding about PROSE becomes a card only if it changes what the reader
    does, otherwise it goes as a `comment` on the card whose text is under discussion** (see
    "The THRESHOLD for filing" in the section "Decomposition and filing findings"); a finding
    about BEHAVIOUR is a card, as before.

## After Review

- Do not touch YOUR OWN task in Review (someone else's tasks in Review — the opposite,
  review them, see above). Review fixes = the task came back to Build with a comment
  (from the human or from the reviewer agent) — you will see it through next_task,
  read the comments through get_task.
  - **You have read the comment — now decide WHAT exactly arrived. `[review] NEEDS WORK`
    does NOT mean "defect":** the `review-failed` label, the `[review] NEEDS WORK` prefix
    and the card sitting in Build are identical in every case — only the TEXT of the report
    differs, which means the only one who can tell the difference is the one who reads it,
    that is you. It comes out that way because NOT ONE other agent tool walks a card out of
    Review (ALL agent tools were swept on a card in Review — every `to=` form of `advance`,
    every verdict of `review_task`: exactly one moved it, `review_task(needs_work)`;
    `call_human`, `return_task`, `decompose`, `handoff` and `transfer_task` refused on the
    stage gate — as did `claim` and `advance`, see the reviewer's bullet about SEVEN
    refusals), so the reviewer is forced
    to dress everything the OWNER has to do from Build as that same verdict. What can be
    behind a bounce is below; the list is NOT promised to be closed, and the last item is
    precisely about that:
    - **a defect** — do the work and `advance(to='review')`, as usual;
    - **A QUESTION TO THE HUMAN** (a choice between options, a product decision, access) —
      FIRST thing, forward it via `call_human` from Build, and do not invent an answer of
      your own in place of the human's: this is exactly the case the reviewer was calling
      for;
    - **"this needs splitting"** — `decompose` from Build (see "Decomposition and filing
      findings");
    - **"this belongs to another repo"** — two shapes, and they are NOT the same call (see
      "Work that belongs to ANOTHER repo"). If the CARD is on the wrong board, `transfer_task`
      from Build moves it into that project's **Backlog**, and its ref CHANGES — quote the new
      one from then on. If the card is yours but cannot continue until a neighbour builds
      something, `handoff` from Build files that half in their Backlog and parks yours in
      **Queue**, unassigned and blocked on it; nobody has to move it back, it is offered again
      by itself once the filed card reaches Review. Both refuse from Review, so a reviewer who
      spots either dresses it as this same `needs_work` report;
    - **"it has lost its point" / an external blocker** — `return_task` from Build (see
      "Stuck? The way out depends on your ROLE"): the card goes to **Backlog** with the
      `blocked` label and WITHOUT an assignee, for the human to re-triage. A reviewer who
      sees that the dependency has been ripped out or that the work has gone stale must
      dress that as the same `needs_work`: `return_task` from Review refuses on the same gate
      (#590) as `call_human` and `decompose`. Together with the splitting branch they look
      like a pair, and since #693 they finally ARE one: both tools CLEAR the verdict, so the
      human sees exactly one label in Backlog — `blocked` here and `epic` there (both
      measured). The labels used to differ, because `return_task` did not clear the verdict
      and the pair `blocked` + `review-failed` arrived in Backlog; on the strong form of the
      same route (the card had been APPROVED and the human returned it by hand) it was the
      pair `blocked` + `reviewed`, that is, the board asserted "accepted" and "blocked" at
      the same time — exactly what the `return_task`-from-Done refusal forbade IN ITS OWN
      WORDS back then. It does not say those words now, and the reason is that same
      clearing: the pair has become UNREACHABLE (with the Done gate removed the card today
      arrives with the single label `blocked` — measured), so the refusal names a different
      consequence — the human's acceptance would be ERASED from the board. Telling the
      branches apart by the NUMBER of labels is no longer possible, and there is no need:
      the label itself tells them apart;
    - **NOTHING in the list fits — do NOT GUESS**, ask the human via `call_human` from
      Build. The enumeration above is what has been MEASURED against today's set of tools,
      not a closed list: a new mutating tool, open from Build and closed from Review, will
      add a branch to the bounce that is not here, and this text will stay silent about it.
      The default bias of this whole section is "a bounce = a defect", so an unclear report
      is safer handed to the human than worked on.
    A caveat about an edge, measured on a live `Workflow`: all of this works while the card
    is ASSIGNED TO YOU. A card with NO assignee (the human removed it in Review, or put the
    card there by hand) simply has no owner to return it to — so the bounce takes such a card
    not to Build but to **Queue** (#705), and it becomes an ordinary free task: `next_task`
    will offer it, `claim` will claim it, and every route above opens up for the NEW owner.
    "Ordinary" literally, with all the ordinary Queue gates, and not "guaranteed to be picked
    up": slots taken — it waits for a free one (`claim` refuses with "WIP limit reached", and
    that is NOT a reason to fix it), an unfinished predecessor — it waits for that, and with
    an `epic` or `blocked` label `next_task` will not offer it at all (measured; that filter
    is old and has nothing to do with the bounce). Nothing got worse in any of these cases:
    in Build nobody saw it at all. The `review-failed` label and the text of the verdict stay
    on the card — once you have claimed it, read the `[review]` comment: if there is a
    QUESTION in it, forwarding it to the human via `call_human` is still on you. **Do not be
    scared of the label and do not go "fixing" it:** in Queue `review-failed` does not mean
    "is being reworked right now" but "was bounced and is waiting for someone to take it";
    it is cleared by your own `claim` (measured after #693: `Queue[review-failed]` → claim →
    `Design` with no labels). Before #693 only the next `advance(to='build')` cleared it, and
    the verdict made it as far as Design — a narrow window, but it was the same lie on the
    board as in `return_task`, just shorter-lived. That does NOT get in the way of reading
    the `[review]` comment: what is cleared is the label, and the comment log is append-only.
    The assigned bounce does not change by a single byte: the card goes to Build to its own
    implementer, and a card assigned to SOMEONE ELSE does not become claimable by you.
    One edge remains that the bounce does not govern: an unassigned card the human put
    straight into Design/Build BY HAND. From there ALL the routes above refuse — `advance`,
    `call_human`, `return_task` and `decompose` alike — and with the SAME TEXT, so trying the
    next one on the list after the first refusal is pointless (measured with a sweep of all
    12 tools on such a card in both Design and Build: NOT ONE moves it — reading and
    commenting are possible, making it yours or moving it is not). The refusal opens with the
    familiar "not assigned to you — claim it first", but now it also says itself that this
    advice cannot be followed (`claim` works only from Queue) and that only the human can
    bring the card back; `next_task` will not offer it at all. It cannot be fixed under your
    own power: say so in your final report.
    A second edge, also measured: for a card with an UNFINISHED predecessor (`follows`/
    `blocked`) exactly the "defect" branch drops out — `advance(to='review')` refuses while
    the predecessor is below Review, and the other routes work. That is no reason to guess:
    do the work and wait for the predecessor, whose card is named in the refusal text.
  - **And it is URGENT, because until your `call_human` nobody NOTIFIES the human about the
    question.** Measured with a spy on the webhook: `review_task(verdict='needs_work')` pings
    NOBODY (zero requests, and there is no `notified` key in its response at all),
    `next_task` is zero too, and only `call_human` gives one ping and `notified: true`. That
    is, the whole push channel "reviewer → human" rests on ONE call of yours. (The ping
    exists at all only if the `VIKUNJA_NOTIFY_WEBHOOK` webhook is configured for the project;
    not configured — "notifies" comes down to the parking in Your Call itself. But that too
    is your own `call_human`.) If you did not forward it, the question stays as just a
    comment on a card in Build: the human will have to notice it unaided, while the card
    looks like an ordinary rework — a `review-failed` label, standing with you in Build, the
    question hidden in the text of the report. That is precisely the quiet outcome the rule
    is written for.
- Only the human moves a card to Done. Never try to work around that through the API.
