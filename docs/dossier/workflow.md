# workflow.py — стадии, гейты, маркеры, push-ревью

> **Это ДОСЬЕ, а не правила.** Правило живёт в `CLAUDE.md → Архитектура → workflow.py` — там оно короткое и
> обязательное к исполнению. Здесь лежит доказательная база: измерения, построенные
> стенды, опровергнутые формулировки и номера карточек.
>
> **Читай перед тем, как менять этот код.** Этот репозиторий уже чинил гарды
> рассуждением вместо измерения — по несколько раундов подряд. Если правило кажется
> избыточным, ответ почти наверняка здесь.

- `src/vikunja_mcp/workflow.py` — the product rules: stages, gates,
  assign-then-verify claim (with self-heal), review offering (verdict vs
  worklog timestamps), comment markers `[claim] [spec] [worklog] [needs-human]
  [blocked] [decompose] [review] [attach]` plus mutually-exclusive verdict
  labels `reviewed`/`review-failed` (push-review of EVERY task, not just bug fixes —
  tracker #117: `advance(to='review')` nudges `review_needed` + `review_kind`
  (`'bug'`|`'change'`, the reviewer's rubric) for any card WITHOUT the `epic` label,
  and resets a stale `reviewed`/`review-failed`). An epic container is the lone
  exception: its code lives in its children, each reviewed on its own advance.
  Behavior changes belong here, with a unit test per gate.

## `file_task`'s Backlog contract and the four cards that "escaped" it (#1167)

**The report.** On 2026-08-19 the orchestrator observed that VMCP-292 (1166) was physically in the
**Queue** bucket while its only journal line was the Backlog variant of the marker —
`[filed-by-agent] заведено агентом для триажа человеком (по ходу работы над #1164)`, the `ru` column
of `cardtext.py`'s `filed_backlog` row and what the server was writing at v0.2.321. (This board is
on the `en` default now, so the string to grep for today is `filed by an agent for human triage`;
the observation is historical and correct for its moment.) It filed #1167 without diagnosing, which
was right: the pair really does look impossible from the source. `workflow.file_task` picks the
destination and the marker off the SAME flag (`stage = "Queue" if queue else "Backlog"`, then `elif
queue:` / `else:` for the text), in one function, with nothing between them. Over the next three
hours the same thing happened to three more cards — 1167, 1168 and 1169. All four went into bucket
44, which is what the log below shows directly; for 1167 and 1168 the orchestrator also recorded the
tool's own `"stage": "Backlog"` payload on the card. All four were later found in Queue, and all
four still carry the Backlog marker, read back off the cards. The card's own journal escalated it
from "one odd card" to "the Backlog contract is not holding", and named the worry precisely:
`file_task`'s whole point is that a finding an agent discovered ITSELF waits for a human to
prioritise it, so a card reaching Queue anyway is claimable by `next_task` and countable by the
hub's `claimable` poll, with the triage step skipped silently.

**Why the board could not answer it.** Both candidate actors write the SAME endpoint —
`POST /projects/10/views/40/buckets/{bucket}/tasks` — so bucket membership, `created`/`updated`
timestamps and the view's `default_bucket_id` are all blind to who called it. The orchestrator's
own read-only rounds ruled out a task-row overwrite (`created == updated` on cards that had
moved) and the "no placement falls into the default bucket" artifact (the default bucket IS
Backlog, so a card with no placement would surface THERE), and made a pure elapsed-time trigger
unlikely — its own comment is careful that a LONGER timer was not ruled out. None of it named a
mover. `GET /tasks/<id>` carries no history field, and nothing else this investigation reached
through the API named one either, so that was the end of what the board could say.

**What settled it: the server's own HTTP log.** Vikunja logs every request, and the container on
the tracker box held 153 743 lines reaching back to its 2026-07-24 startup line — the whole
window and three weeks either side of it. Read with `docker logs vikunja`, which changes nothing
on the server. Two signatures, and they do not overlap.

Filing is identical on all four cards and is exactly `file_task`'s call order — create, move,
relation, marker (this one is 1169, at 10:27:37Z; 1166, 1167 and 1168 differ only in the id and
the timestamp):

```
PUT  /api/v1/projects/10/tasks                        201
POST /api/v1/projects/10/views/40/buckets/44/tasks         <- 44 = Backlog
PUT  /api/v1/tasks/1169/relations                     201
PUT  /api/v1/tasks/1169/comments                      201
```

The move into Queue is a different animal. The delay from filing to drag is not a constant and
not a timer — 33 min for 1166, 19 min for 1167, 7.5 min for 1168, 1 h 53 min for 1169. That much
is arithmetic and it holds; the gloss this paragraph carried on top of it — "the pace of a person
working through a column" — does NOT, and #1172 took it out. There are THREE drag moments here,
not four: the position calls for 1167 and 1168 are 580 ms apart (10:05:54.182Z and 10:05:54.762Z,
both listed two paragraphs below), so two of the four "delays" are ONE moment measured from two
different FILING times, and most of the spread is an artifact of when each card happened to be
filed rather than of any pace. This is 1166, at 09:31:19Z:

```
POST /api/v1/user/token/refresh
GET  /api/v1/avatar/ufna?size=40
GET  /api/v1/notifications?page=1
GET  /api/v1/projects/10/views/40/tasks?filter=&...&expand%5B%5D=comment_count&per_page=25
POST /api/v1/tasks/1166/position                           <- 178 ms before the bucket write
POST /api/v1/projects/10/views/40/buckets/45/tasks         <- 45 = Queue
GET  /api/v1/notifications?page=1                          <- and every 10 s after that
```

**The discriminator is the position endpoint.** `POST /tasks/<id>/position` is a request this
package cannot emit: `grep -rn "/position" src/` prints nothing at all, and the only thing here
that writes a card's COLUMN is `api.move_task`, which POSTs `{"task_id": ...}` to the bucket
endpoint and nothing else. On 2026-08-19 it was called exactly FOUR times, on exactly the four
cards — 1166 at 09:31:19.123Z, 1167 at 10:05:54.182Z, 1168 at 10:05:54.762Z, 1169 at 12:20:16.291Z
— and in every one of the four the VERY NEXT request in the log is that card's move into bucket
45, 173–178 ms later. It is not a rare endpoint, which is the other half of the reading: 681 calls
to it across the whole log, i.e. an ordinary thing a human does to this board.

**What is NOT the discriminator, said out loud, because it is the tempting half.** A browser was
logged into this board all day, so `/user/token/refresh`, `/notifications` polling every 10 s and
avatar fetches sit around EVERYTHING, the agent's own filing sequences included — the 1164 filing
(the control, two paragraphs down) has a `token/refresh` 23 ms before its `PUT /projects/10/tasks`
and an `avatar/ufna` in the middle of it. Ambient browser noise proves a browser was open; it does
not attach to the write beside it. What does attach is the position call, because it names the
task id in its own path.

The control that makes the discriminator mean something is in the same log: 1164 and 1165 landed
in bucket **45** at 08:37 through the filing signature above and with NO `/position` call anywhere
near them. Same destination bucket, same endpoint, and exactly ONE element different. That they
were `queue=True` filings is not read off the log, which cannot see a flag: 1164's journal carries
the Queue variant of the marker, read off the card, and the filing signature itself rules out a
hand-made card (a human does not `PUT` a relation and a comment within 350 ms of creating one).

One inference is marked rather than hidden: a `buckets/45/tasks` line carries the task id in its
BODY, not its path, so the log does not name the card. For 1166 and 1169 the pairing is
unambiguous by isolation. For 1167 and 1168 both writes land inside the same second (10:05:54.356
and 10:05:54.935) and each is assigned to its card by interleaving order with the two position
calls that name them. That is an inference, a tight one, and not a reading.

**Diagnosis.** Hypothesis 1 — something moved the card after it was filed — with the mover
placed: OUTSIDE this package, in a browser session driving the web frontend. `file_task`'s
contract held on all four cards, and the triage step the card feared was being skipped is the
very thing the log records happening: each card was moved by hand from the board. (Two limits on
that sentence, both #1172's. The discriminator separates this PACKAGE from a BROWSER FRONTEND; it
cannot separate a person's hand from browser automation, and this repo runs the latter routinely
— `PLAYWRIGHT_MCP_ISOLATED=true` is committed for exactly that reason. A human is the reading and
it is a good one; it is not the measurement, and "outside `file_task`" is the whole of what the
diagnosis needs. The second: this entry used to add that "a human touching each card individually
is exactly what the Backlog contract asks for, and it is more than a bypass would leave behind" —
that is a VALUE JUDGEMENT stated as a measurement, it is not derivable from a request log, and
the 580 ms between two of the four drags makes even "individually" the weakest of readings.)
And the fourth card is the one that closes it: 1169 was the
orchestrator's own CONTROL for "not everything drifts", still in Backlog after 94 minutes — it
was dragged at 12:20:16Z like the rest, so the control was never a control, only a card the human
had not reached yet.

**The reading error underneath, which is the part worth keeping.** The marker is a DATED
PROVENANCE STAMP about the filing. The bucket is LIVE STATE. A comment cannot go stale because a
card moved, so the two disagreeing is the normal condition of any card a human has triaged — and
the more faithfully `file_task` does its job, the more such cards there will be. What made it
read as a contradiction was treating the journal as a statement of current placement.

**What shipped, and what deliberately did not.** No code fix — there was no defect. The prose
correction went where the filing agent reads it (`server.file_task`'s docstring), and the two
halves of the diagnosis that ARE properties of this code are pinned in
`tests/unit/test_backlog_placement.py`: the marker distinguishes its two destinations in both
languages, and no registered tool pointed at a Backlog card moves it into Queue — with that
second pin's reach measured and written into its own docstring, since 10 of the 20 rows it drives
are refusals and only two reach a move at all. Read "pinned" as a DELTA and not as a first, which
is #1172's correction to this paragraph and to that docstring alike: `test_workflow_gates.py`
pre-exists, is untouched by the #1167 commit, and already reddens on the same mutations —
measured on that file alone, control 0 failed / 0 errors / 102 collected, `file_task`'s stage
flipped to a constant -> 2 failed, `decompose`'s parent move retargeted -> 6 failed, closing
control 0 failed / 0 errors / 102 collected. What the new file adds is a roster DERIVED
fail-closed from `server._DEFERRED_TOOLS` in both directions rather than hand-written, the `ru`
column of the marker property (the pre-existing pin reads only `en`, and `ru` is the spelling the
card observed), and the ownership dimension driven systematically. Rewriting the
Backlog marker to name its column was considered and dropped: the reader here had already read
the marker correctly, so a clearer destination would not have helped, and it would have churned
`cardtext.py`'s two-language table for nothing.

**What is still blind, and stays blind.** Nothing in this package can say who moved a card. The
board API surfaced no history to this token, and the only thing that answered the question was a
log on the server, reachable by ssh and gone whenever that container's log rotates. So the next
Backlog-to-Queue surprise is answerable the same way or not at all — which is the reason this
entry records the request signatures rather than only the conclusion.

## What a TOOL CALL returns is English — and a green test held the Russian in place (#1166)

**The rule.** `workflow.py` writes for two different readers and only one of them has a language
setting. What goes onto a CARD is `cardtext.py`'s two-column table, keyed by `language`. What a
tool call RETURNS — a `WorkflowError` message, the `message`/`note` keys of a `next_task` payload
— is prompt content: it lands in an orchestrator's log and in a per-task agent's context, it is
deliberately OUT of that table (#1165 put it there, and `cardtext.py`'s own docstring says so in
the bullet naming `WorkflowError` text and the `note`/`message` strings in tool payloads), and it
is therefore ONE language for every consumer whatever their toml says. That language is English,
the one the README, CLAUDE.md and SKILL.md are written in. Pinned by
`tests/unit/test_agent_facing_text_is_english.py`.

**What was actually there.** #1164's rule 2 is an INSTRUCTION — "Leave it English; do not fold
it into any later localization" — and this card enforces it rather than overturning it. The claim
that the text ALREADY was English is not in that rule at all: it appears as "(it already is)" in
#1164's own build note and `[worklog]`, paraphrasing its own card, and is quoted from there by
#1166's description. Two strings said otherwise: `_cycle_signal`'s five-line `message`,
sitting in the same returned dict as a fully English `note` — one payload, one field in each
language — and `claim`'s epic-container fallback, which rendered `его подзадачами` at the end of
an otherwise English sentence whenever the epic had no subtasks to name. Both were STRINGS
returned to an agent, not code comments, so #1164's own ASCII gate neither did nor should have
covered them.

**Why translating and not retracting the parenthetical.** The card offered both. The second is
not actionable in this repo: `grep` over everything `git ls-files` carries finds that parenthetical
in no tracked file except the two this card adds, which quote it in order to place it. It lives in
a card's COMMENTS, so "correct it" would edit a landed card's journal — which this tool cannot do,
comments being append-only — and change nothing a reader of this code can see. The first option is
what the shipped design already says, and the sharpest evidence is inside the one return dict.

**THE FINDING WORTH KEEPING: a green test was holding the Russian in place.** The reason to look
for a pin here is not that nothing tested these strings — it is that something did, in the wrong
direction. `test_workflow_sequence_gate.py` asserted `"цикл" in res["message"].lower()`, i.e. a
translation of that message was a RED test — measured on the shipped tree, selection
`test_workflow_sequence_gate.py -k two_cycle` (67 collected at `454b298`, 1 selected), control 0
failed / 0
errors, that assert put back under the English message 1 failed — and the two other pins over the
same message read its interpolated values through contiguous literals, one of which spelled
`задач(и)`. So the tree carried TWO asserts a translation had to defeat outright and two more
constraining the message's punctuation, and none at all that would have noticed the field was
Russian in the first place. The epic fallback is the mirror case:
TWO tests in `test_workflow_epic_skip.py` drive that exact branch —
`test_claim_refuses_childless_epic_gracefully` and `test_claim_refuses_epic_container`, whose epic
has no subtasks either — and both match on the word "container", so the Russian tail rode through
a green suite for as long as it existed — the second pass restored the Russian and measured that
file at 13 collected, control 0 failed and the mutation 0 failed, a round in which nothing moved.
The translation therefore kept the message's rendered STRUCTURE — closed loop,
count, `Tasks in the cycle: <detail>` — and moved the pins with it, rather than rewriting prose
whose shape two tests (one of them parametrized over three cycle sizes) depend on.

**The unit is CYRILLIC, not ASCII, and that is measured.** The card-text gates next door assert
ASCII, which is right for them: a marker is a wire format. This population is English prose full
of em dashes and arrows, so an ASCII pin over it is red on arrival — dozens of offenders in this
module alone. The exact count is not written down: the file asserts a FLOOR instead
(`test_an_ascii_unit_would_be_red_on_arrival`), because the number moves with every refusal
anyone adds and a stale figure is exactly the argument for "just use ASCII here too". A floor is
not the property — only a count of ZERO would make the ASCII unit available here — and the assert
says so where it fires. What the
narrower unit costs is shown rather than claimed — a sweep round translates the same fallback into
GREEK and the Cyrillic scan stays green while the runtime pin catches it.

**The sweep**, selection `tests/unit/test_agent_facing_text_is_english.py` alone, in a clone,
`__pycache__` cleared and `PYTHONDONTWRITEBYTECODE=1` per round, rounds read by counting lines
beginning `FAILED `: control 0 failed / 0 errors / 4 collected before every round; the `message`
reverted to its pre-#1166 Russian 2 failed; the epic fallback reverted 2 failed; a Cyrillic
literal that reaches no agent at all (assigned to an unused local) 1 failed, the static scan
alone; the fallback translated into Greek 1 failed, the runtime pin alone. Separately, on
`test_workflow_sequence_gate.py -k cycle` (67 collected at `454b298`, 11 selected): control 0
failed / 0
errors, and the stage-for-ref mutation that section was built around still gives its recorded 3
failed after the translation, now rendering `Tasks in the cycle: Queue in 'Queue'; …`.

**What is left, measured across the whole package rather than assumed.** An AST audit of every
non-docstring string literal in `src/vikunja_mcp` leaves, outside `cardtext.py`'s deliberate `ru`
column, exactly one: `api.py`'s `AssertionError("unreachable: …")`, on a path asserted not to
exist — and `server._tool` converts only `WorkflowError`/`ConfigError`/`VikunjaError`/
`httpx.HTTPError`, so even if it fired it would not render as a tool `{"error": …}` at all.
In DOCSTRINGS the interesting survivors are `server.py`'s two tool
descriptions, which the MCP SDK ships to the agent — unlike `workflow.py`'s docstrings, which a
human reads in the source and which this gate deliberately exempts. Both are filed as
VMCP-296 (1170) rather than fixed here, because whether a Russian EXAMPLE inside an English tool
description is a leftover or an illustration that the value is free-form is a triage question.

## A predecessor in another project (#1179)

**The defect, and how it was measured.** `_unfinished_predecessors` built `stage_by_id` from
THIS project's board and treated anything missing from it as deleted —
`continue  # genuinely gone (absent even from the full board)`. #126 had already hardened
that ruling against one false positive (a card sitting in an unpaged Backlog/Your Call/Done
bucket, disambiguated via the memoised `resolve_full`), but both boards it consults are the
OWN project's. Vikunja relations are task-to-task and cross projects freely, so a card can
legitimately be blocked by a card on another board, and that case fell into the "gone"
branch.

Measured on `FakeAPI` with a same-project CONTROL in the same round, before any fix, with
`vikunja_mcp.__file__` printed in each round to prove the tree under test:

    control (blocker in Build, SAME project):     claim REFUSED, next_task withheld
    round   (blocker in Build, SIBLING project):  claim ALLOWED, next_task OFFERED

The control refusing is what says the probe measured anything. After the fix the same probe
prints REFUSED/withheld for both rows.

**Why this was worth fixing rather than documenting.** The cross-project `blocked` relation
was already CREATABLE before the fix — verified against a real 2.3.0, where a task moved
between projects kept its `blocked` link to a task left behind in the source. So the gate
was not merely unaware of a hypothetical shape; it silently ignored a link the server
happily stores, and would have made `handoff` a lie: a card parked "blocked on" a neighbour
would have been offered again immediately, with its blocker untouched.

**The resolution order, cheapest first.** Off-board predecessors go through
`_offboard_predecessor`, which answers without a board read wherever it can: a 404 on the
task means deleted (a narrow race — deleting a card takes its relation rows with it, so the
window is between the successor's relation read and this one); `done` is ready by
definition; and a predecessor claiming OUR project id while absent from our exhaustive board
is self-contradictory and keeps the pre-#1179 answer rather than inventing a blocking state
out of a contradiction. Only what survives that reaches `_foreign_stages`, which is memoised
per call, so N predecessors in one neighbour cost ONE board read and the common
no-off-board-predecessor path costs nothing.

**UNKNOWN IS NOT GONE — the rule the whole card turns on.** Three outcomes cannot establish
a stage: 403 on the task, 403/404 on the neighbour's board, and a task that is on no bucket
of it. All three return a BLOCKING pair with the reason spelled into the stage string, which
the refusal then shows the human (`… in 'unknown — the token cannot read project 107's board
(403)…'`). Fail-closed is the whole point: rendering an unknown as "gone" is the defect this
card exists to remove, and re-introducing it one level down would be worse, because there it
would look like a considered decision.

## `handoff` / `transfer_task` (#1179)

**Two tools, because there are two questions.** `handoff` is the DEPENDENCY ("my card cannot
continue until another repo builds something") — a new card over there, this one parked and
blocked on it. `transfer_task` is the MISFILE ("this card is on the wrong board") — the card
itself moves and nothing stays behind. Collapsing them loses one of the answers.

**Why the parked card goes to Queue and carries NO `blocked` label.** The obvious move is to
mirror `return_task`: Backlog plus the `blocked` label. It is wrong here, and measurably so
— `blocked` means "externally blocked, a human must look" and next_task does not offer such
a card, so the pause would never end without a human. What ends it instead is the relation
itself: the (now fixed) predecessor gate withholds the card while the blocker is below
Review and offers it the moment it gets there. Queue plus a relation is self-clearing; the
label would defeat exactly that.

**What the real 2.3.0 does on a move, all measured on a throwaway container.** There is no
dedicated endpoint — `/tasks/{id}` has only `get/post/delete` — but `project_id` is not
`readOnly` in `models.Task`, and `POST /tasks/{id}` with it changed returns 200 and moves
the card. Then:

- **the card is RE-INDEXED and its identifier CHANGES**: `FRNT-2` arrived as `BACK-3` in a
  project already holding `BACK-2`. No collision — the target's own counter assigns the next
  free index, and the next new card there came out `BACK-4`. So every ref quoted in an
  earlier comment, worklog or commit message is dead, which is why `transfer_task` returns
  the new ref and says so in its `note` rather than leaving the agent to discover it.
- **labels, assignees and relations all SURVIVE**, including a relation whose far end stayed
  on the source board. Surviving is right for relations and wrong for the other two, so the
  tool clears assignees and the claim/verdict labels itself.
- **it lands in the target's DEFAULT bucket** and disappears from the source board entirely,
  which is why the explicit `move_task` into Backlog is not optional.

**Shut from Review and Your Call.** Both stages hold something PENDING that belongs to this
board — a verdict not yet cast, a question a human here was asked — and carrying the card
away strands it with nobody watching. Closing Review also keeps #672's invariant intact
("out of Review a card is walked by exactly ONE agent tool"), which a second mover would
have quietly falsified; `tests/unit/test_skill_contract.py` reddened on exactly that during
this card, which is how the gate came to be written.

**What the contract tests forced, and it is worth recording as a pattern.** Three separate
pins in `test_skill_contract.py` had to be re-derived rather than edited to agree: the
Review sweep (both new tools refuse — the refusal set went from five to seven), the bounced
card sweep (both MOVE a card in Build, so «After Review» has to route them by name), and the
ownerless-card sweep (`transfer_task` moves an unowned card, deliberately — a misfile needs
no ownership, exactly as `file_task` does not). None of those were anticipated when the
tools were written; each was surfaced by a test that re-derives its universe instead of
remembering it.
