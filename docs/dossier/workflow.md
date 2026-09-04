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
control 0 failed / 0 errors / 102 collected. What the new file adds is the BACKLOG question
asked over a DERIVED roster, and #1172 needed TWO tries to state even that — its first landing
claimed the derivation itself was new, its first rework claimed the Backlog question was asked
nowhere else, and both are false. The pointable-roster derivation pre-exists in
`tests/unit/test_done_is_human_only.py` (#662), whose `_pointable_tools()` reads the same
`server._DEFERRED_TOOLS` by signature and asserts both directions; `test_workflow_gates.py`
derives `_VERDICT_POLICY`'s roster the same way over ALL tools. And the Backlog question
pre-exists in that file's
`test_the_per_stage_ownerless_exits_state_only_what_the_board_really_does`, which loops a
HAND-WRITTEN 8-form `movers` tuple over every stage but Queue, Backlog included — ownerless
only. Putting the two together is what is new. Measured under the same discipline in a
clone taken for #1172's rework, with one `@_mcp_tool`-decorated `snooze_task(task_id, days=1)`
added to `server.py`: `tests/unit/test_workflow_gates.py` goes from control 0 failed / 0 errors /
102 collected to 1 failed / 0 errors / 102 collected — caught by the DERIVED roster
(`test_every_agent_tool_is_graded_for_what_it_does_to_a_stale_verdict`, naming `['snooze_task']`)
while the hand-written `movers` test stays green; the new file goes from control 0 failed /
0 errors / 5 collected to 2 failed / 0 errors / 5 collected, and those two rows are NOT the tool
being swept — they are the self-check and a `KeyError` on `_OTHER_ARGS` before the call. What the
derivation buys is redness until a human classifies the tool, and a sweep once they do. Plus
the `ru` column of the marker property (the pre-existing pin reads only `en`, and `ru` is the
spelling the card observed), and the ownership dimension driven systematically. Rewriting the
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
out of a contradiction. Only what survives that reaches `_foreign_stages`, and the common
no-off-board-predecessor path costs nothing at all.

**THE MEMO'S SCOPE IS THE WHOLE COST, AND IT WAS ONE LEVEL TOO NARROW (#1199).** It used to be a
local of `_unfinished_predecessors` — "memoised per call, so N predecessors in one neighbour cost
ONE board read", which is literally true and reads as though it covered `next_task`. It does not:
`next_task` calls that helper once per free-Queue CANDIDATE, so the memo spanned predecessors
within a candidate and never candidates within a call. Measured on FakeAPI at `048d1f9`, one
`next_task`, M free-Queue cards each blocked on a card in ONE neighbour project, with
`vikunja_mcp.__file__` printed in every round (re-run at `5f26333`, this change's actual base,
with identical figures — `048d1f9` is the version bump one commit below it):

    M=0 -> view_tasks 1 (of them neighbour 0)      M=3 -> view_tasks 5 (neighbour 3)
    M=1 -> view_tasks 3 (neighbour 1)              M=5 -> view_tasks 7 (neighbour 5)

The neighbour column tracking M is the defect. The memo is now owned by the CALLER: `next_task`
hands in ONE dict for the whole call and the same rows come out 1/3/3/3, the neighbour read once
wherever there is a gated candidate at all (M=0 still reads it zero times); `claim`/`advance`
hand in nothing, get a per-call dict and are unchanged, which is right — they resolve a single
card and have nothing to share.

**The staleness surface does not widen in KIND, and saying "not at all" would be false.** Nothing
is cached ACROSS calls — that half is pinned. But the WINDOW does get longer, from one candidate
to one call, and that is not a quibble: constructed, two free-Queue candidates on one neighbour
with the second's predecessor moving to the neighbour's Review immediately after the first
neighbour read returns (an ordinary shape under a parallel drain), the pre-change tree reads the
board twice and OFFERS candidate 2, this one reads it once and reports `starving`. That is the
same trade the per-candidate memo already made, one scope wider, inside a call that is READ-ONLY
BY CONTRACT — and the card resurfaces on the next tick. It is not a new kind of staleness; it is
more of the kind already accepted.

**Why it was worth doing at all, since it is a cost and not a wrong answer.** That neighbour read
is EXHAUSTIVE (`view_tasks` with no `require_titles`), so it pages the neighbour's unbounded Done
— the very shape #43 removed from our own board — and `next_task` is what `vikunja-mcp claimable`
runs on every hub poll tick. A `handoff`-parked card is parked precisely until the far card
reaches Review, which can be days, so before this each parked card cost one exhaustive
neighbour-board read PER POLL for its whole parked lifetime, in the command whose own dossier
carries the $105/day dogfood story.

**And the obvious cheaper read is a WRONG ANSWER, which is worth writing down so nobody
re-derives it.** `require_titles` cannot simply be `NEXT_TASK_STAGES`: a predecessor sitting in
the neighbour's Done BEYOND THE PAGES that narrowed read would still fetch is absent from the
returned board, and `_offboard_predecessor` renders it as "not in any bucket" — that is, BLOCKING
— turning a cheap read into a card that never becomes claimable. That is not a deduction: it was
CONSTRUCTED on `FakeAPI`, control and round in one script, `vikunja_mcp.__file__` printed. The
qualifier is load-bearing, and the second shape below is why:

    Done holds page_size + 1 cards, the predecessor LAST
      CONTROL (exhaustive neighbour read):    claim ALLOWED
      ROUND   (narrowed to NEXT_TASK_STAGES): claim REFUSED — "… in 'unknown — not in any
                                              bucket of project 107's board'"
    Done holds ONE card, the predecessor
      CONTROL: claim ALLOWED                  ROUND: claim ALLOWED

A narrowed read does not DROP a non-required bucket — `api.py` merges every bucket on every page
it fetches, and the required set drives only when the paging LOOP stops. So the wrong answer needs
a Done deep enough to fall outside the pages the required buckets already forced, which is exactly
the Done #43 exists because of. `FakeAPI` models that truncation as `tasks[:page_size]`, so page
one is the floor there rather than the rule; the shape of the defect is the same and its threshold
is the server's, not the fake's.

`done` IS checked before the board read, but that is the task's `done` FLAG and not its bucket:
a card moved into a Done bucket keeps `done` false, and what releases it is `READY_STAGES`
matching the bucket TITLE off the very board the cheap read would stop returning. Measured in
#1190's sweep in this same tree: with the `done` check deleted outright, the Done-bucket test
still passes and only the flag test fails — control 0 failed, that round 2 failed, 85 collected
in both. Narrowing the read is therefore a separate design question, not a smaller version of
this one.

**UNKNOWN IS NOT GONE — the rule the whole card turns on, AND IT COVERS ONLY WHAT REACHES THE
GUARD (#1198).** Three outcomes cannot establish a stage: 403 on the task, 403/404 on the
neighbour's board, and a task that is on no bucket of it. All three return a BLOCKING triple with
the reason spelled into the stage string, which the refusal then shows the human — with the
project id substituted, `unknown — project 107 has no readable tracker board for this token
(403/404), so whether it is finished cannot be established`. Fail-closed is the whole point:
rendering an unknown as "gone" is the defect this card exists to remove, and re-introducing it one
level down would be worse, because there it would look like a considered decision.

*(That quotation was wrong in this file through three landings — it arrived with #1179 and
survived #1190 and #1199, both of which edited this section. It read `… in 'unknown — the token
cannot read project 107's board (403)…'`, which occurs nowhere else in the tree and nowhere in
the code at all: a paraphrase wearing quotation marks.
`tests/unit/test_repo_quotation_claims.py` never saw it, because the sentence carried none of the
assertive idioms that gate reads — that gate as scoped today cannot see this class of quotation,
which is why it was fixed by hand.)*

**THE UNIVERSAL WAS WIDER THAN THE GUARANTEE, and its counterexample sits one layer ABOVE this
code (#1198).** CLAUDE.md said "every unresolvable one BLOCKS rather than vanishes", full stop,
and this file said the same in its own words under the heading above — the quoted form was only
ever CLAUDE.md's. Either way it is true of `_offboard_predecessor`'s three BLOCKING returns and
false of the predecessor a reader most naturally pictures — one whose project the token cannot
read — because that predecessor never reaches them. Measured by #1179's independent reviewer on
a throwaway vikunja/vikunja:2.3.0, with a two-reader control in the same moment: home project
and neighbour both shared with the agent token, `handoff` creates the cross-project `blocked`
relation, then the neighbour is unshared (`DELETE /projects/6/users/agent1` -> 200).

    SHARED:    agent sees `blocked: [4]`; claim REFUSES; next_task withholds with starving: true
    UNSHARED:  agent's get_task(home_card)["related_tasks"] == {} ; claim ALLOWED; card OFFERED
    CONTROL:   same card, same moment — owner reads {'blocked': [4]}, agent reads {}

The row that closes the "the unshare deleted the relation row" alternative is the third: the row
is intact, the READ is filtered. So state the guarantee at its real width, which takes two clauses
and not one: unknown is never spelled "gone" for a predecessor that REACHES the guard carrying an
integer `project_id` that is not ours — the two fail-OPEN escapes below are the other exceptions
and they are inside the guard, not above it — and a predecessor whose whole project is invisible
to the token does not reach the guard at all, because the server removes it from the relation
payload before our code sees it. The first clause is what this section used to leave out; the
second is what this card is about. `workflow.py`'s own heading states the necessary condition
("COVERS ONLY WHAT REACHES THIS METHOD") and is right as it stands, and CLAUDE.md's parenthetical
(403, no kanban view, not in any bucket) is what scopes "unresolvable" there to the three
BLOCKING returns.

**AND ONE FAIL-CLOSED BRANCH IS NOT UNREACHABLE — the opposite worry, also measured, and about
ONE branch rather than three.** #1179 asked whether those three branches could manufacture
never-claimable cards. The unshare route above says they cannot be reached THAT way, and that is
one route rather than all of them. The route that reaches the UNREADABLE-BOARD branch needs no
permission change at all: DELETE the neighbour's kanban view. Measured by the same reviewer on a
throwaway 2.3.0 with a control in the same round:

    CONTROL (view intact): relation visible | far task readable | far board readable
                           claim REFUSED -> "#1 (94) in 'Backlog (project 78)'"
    ROUND   (view DELETEd, 200): relation visible | far task readable | far board 404
                           claim REFUSED -> "… in 'unknown — project 78 has no readable
                           tracker board for this token (403/404) …'"

The card is then unclaimable until somebody acts OFF this board, with its relation fully
visible — not permanently, since #1190's own measurement shows `update_task(pred, done=True)`
releases it, which is exactly why the refusal now names that escape. `api.py`'s own 404 text for
this case tells the reader to run `vikunja-mcp setup`, a hint that a project WITHOUT a canonical
board is an expected state here rather than an exotic one. None of it argues for softening the
branch, since releasing the card is the defect it exists to prevent; it is why #1190, about the
refusal's ADVICE, is the load-bearing follow-up rather than a nicety. The other two branches'
live reachability is UNMEASURED: the 403-on-the-task branch was left open by #1179's reviewer
(no permission change was landed between the relation read and the board read) and the no-bucket
branch has never been exercised against a server at all.

**TWO fail-OPEN escapes, not one, and the asymmetry is deliberate.** `_offboard_predecessor`
returns None — not a blocker — both when `project_id` is not an int and when it EQUALS our own
project id. The second means the identical physical situation, a task in project P that is absent
from P's board, is fail-CLOSED when P is a neighbour and fail-OPEN when P is ours. That is right
and stays: on OUR board the exhaustive read is the same one `claim` and `advance` judge by, so
"absent from it while claiming to be on it" is a contradiction rather than an unknown, and #126
already fixed the answer to it. Both escapes are pinned in
`tests/unit/test_workflow_cross_project_predecessor.py` with the neighbour case as the CONTROL in
the same round, which is what makes the asymmetry a measurement rather than a reading of the code.

**THE RESIDUAL GAP IS ACCEPTED AND DOCUMENTED, NOT CLOSED.** A card whose blocker lives on a
project this token cannot read is released with its blocker untouched, and nothing THE GATE READS
says so. (Something on the BOARD may: `handoff` writes a `[handoff]` comment naming the far card
and its project, and #1179's reviewer measured that comment as the only board-visible signal —
but no gate reads comments.) Closing the gap means the gate can no longer key off `related_tasks`
alone, since the only thing the token can read is its own side of a relation it can no longer
see. The options are then a durable MARKER on the card at `handoff` time — whose write half
already exists as that comment, so what is missing is a reader, and it would only ever cover
relations WE created — or a mirror of the relation kept somewhere the server will not filter.
Neither was built, on #1198's own recommendation.

**Do not read the failure mode as narrow — read it as SILENT and one-directional.** The condition
is simply "this token cannot read the blocker's project at read time", and unshare-after-`handoff`
is the route that was MEASURED, not the definition. Two others need no removal and no "after": a
human links a card here to a card in a project this token was never shared (relations are made by
whoever can see both ends, and the human can), and a human moves the predecessor INTO an
unreadable project — #1179 measured that relations survive a project move. Neither of those was
measured here, and neither was the frequency of any of them. What IS one-directional is the
outcome: the gap fails toward work continuing in the wrong order rather than toward work
stalling.

**THE ADVICE UNDER THAT STRING HAD TO BECOME BRANCH-CONDITIONAL (#1190), and the blocking
decision did not move an inch.** Both REFUSALS that render a blocker list used to end in one
generic tail — `A predecessor becomes ready only at Review or Done; finish that one first` on
`claim`, and `Finish that predecessor's rework and get it back to Review first` on the
`advance(to='review')` latch. (`_starving_tail` renders a blocker list too and ended with its
last waiting line and nothing else; it is the third site the clause had to reach, not a fourth
copy of this tail.) That generic tail is right for an ordinary blocker in Build on a readable
board, and on a card that will not become claimable again without a human it was the only thing
printed.

**It is NOT unactionable on all three fail-closed branches, and getting that wrong was this
card's own first draft.** The three differ, and each difference is measured on a live `Workflow`
over `FakeAPI`:

- **403 on the task** — no form of finishing releases the card. `get_task` raises before `done`
  is ever read, so even setting the flag leaves the refusal exactly where it was. SHARING the
  project does not release it either: the stage becomes KNOWABLE and the refusal then names the
  far card's real stage (`Build (project 107)`), after which finishing it works. The escape said
  "Share its project with this token" flat until the #1190 review, which is incomplete in exactly
  the way this card was filed about.
- **Unreadable board** — HALF of "Review or Done" works. `_foreign_stages` answers None whatever
  the far card's stage, so moving the predecessor to Review does NOT release the card; marking
  it `done` DOES, because `done` is read before any board read. Both halves are one test, one
  round: the same card refuses at Review over there and claims after the update.
- **No bucket** — the board READS. Moving the predecessor into Review releases the card, so
  "finish that one first" is CORRECT advice there and the escape is ADDITIVE, not a replacement.

So the switch is a `finishable` flag per blocker, not "was anything unresolvable". Keying it off
the mere presence of an escape — which the first draft did — replaces true advice with an escape
on exactly the third branch, and nothing in the suite would have said so.

**AND THE FLAG ITSELF SHIPPED UNPINNED ON ONE BRANCH — found by this card's independent
reviewer, not by its sweep.** `finishable: False` on the 403-on-the-task branch was held by
nothing: the sweep row for that branch pins the escape TEXT, which is a different property.
Flipping the flag to `True` makes the refusal print "finish that one first" and then "NOTHING
done to the predecessor releases this card" — the card's own defect, restored — and against a
control of 0 failed at 92 collected the flip came back 0 failed. The fix is one assertion, the
one its SIBLING branch already carried a screen above (`assert _GENERIC not in msg`), and the
isolating round is the point: with it, control 0 failed at 94 collected and the same flip is
1 failed, naming that test alone. Two lessons worth keeping. A sweep row that kills something
does not tell you WHICH property it pinned. And when two branches are written as siblings, the
assertions should be siblings too — the asymmetry between them was the whole tell.

**What carries it is two optional keys on the blocker dict, `escape` and `finishable`, ABSENT
whenever the stage resolved normally.** That absence is what keeps the ordinary refusal what it
was, and "byte-for-byte" here is a measurement rather than a manner of speaking: the same
ordinary blocker driven through `048d1f9` and through this tree, `vikunja_mcp.__file__` printed
in both, prints identical bytes for the `claim` refusal AND the `advance` latch. It is also
exactly the thing a draft got wrong — the shared constant briefly carried a sentence-final period
the old f-string did not, and nothing in the repo pinned that text, so the suite stayed green
while the message had moved. The pin is now `endswith`, not `in`. `_predecessor_advice` then has
three shapes: no escape → the generic tail; some blocker still finishable → generic first, then
the escapes; NOTHING finishable → the escapes alone.

**The escapes name what gets the card MOVING, which is not the same as what RELEASES it**, and
the two are not interchangeable: making the neighbour's board readable turns the unknown into a
knowable stage — measured, the same card then refuses with `Build (project 107)` — while
`update_task(pred, done=True)` releases it outright. The `done` half of that comes from #1179's
independent reviewer, measured on a live 2.3.0 for the unreadable-BOARD branch; the no-bucket
branch has been driven on `FakeAPI` only.

**The third rendering site is the one that is easy to miss, and it is the one an autonomous drain
actually hits.** `next_task` SKIPS a gated card rather than refusing it, so a card parked by
`handoff` behind an unresolvable predecessor produces NO refusal at all under an ordinary /loop
tick — `_starving_tail`'s message is the only place its human is ever told anything. The clause
there is conditional and sits beside `needs_retriage`, which is the same shape for the same
reason (a tail that does not self-clear), and the conditionality is load-bearing rather than
tidy: `tests/unit/test_workflow_sequence_gate.py` pins that message WHOLESALE — headline,
lead-in and waiting lines and nothing else — and an unconditional clause reddens it.

**The two board branches word their escape around the PROJECT, not the task, and the dedup is
why.** Two predecessors on one unreadable board is the ordinary shape of a handoff-heavy chain;
naming the task in the escape would print the same actions once per predecessor, while the ref of
every blocker is already printed above the clause. Deduping is by exact text in first-seen order,
so a per-task wording collapses nothing — the first draft did exactly that and its dedup test
measured 2 where it asserted 1. The 403-on-the-task branch keeps a per-task wording and is right
to: its escape is about that one task.

**And the lead says nothing about WHO can act, deliberately.** A draft read "that half will NEVER
clear by itself and no agent can unblock it — a human has to act", which is true of the two
fail-closed-forever branches and false of the no-bucket one, where an agent moving the
predecessor into Review clears the card. It now says only that nothing on THIS board changes the
unknown, which holds on all three.

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

## `to` was refused at the boundary its own docstring advertised (#1200)

**The defect in one line: the two tools' promise and their wire contract disagreed, and the
wire won.** `server.py` declared `def handoff(task_id: int, to: str, ...)`, the MCP SDK builds
a pydantic model from that signature, and so `handoff(to=17)` died with
`Input should be a valid string` before the tool body existed — while the docstring said `to`
takes "a sibling NAME … or a bare project id" and `Workflow.handoff` had accepted `str | int`
all along. `_resolve_sibling`'s careful refusals (unknown name lists the configured ones;
non-positive id; this project's own id) never ran FOR AN INT TARGET — for a STRING they were
reachable all along, measured on a live `Workflow`: `'nope'` lists the configured siblings, `'0'`
refuses as non-positive, `'3'` refuses as this project. So the one shape the docstring advertised
was precisely the shape no refusal could ever explain.

**Why no test saw it, and the answer is sharper than "no coverage".** There WAS coverage of an
int target: `tests/unit/test_done_is_human_only.py` passes `handoff` a bare `{"to": 999}` and is
green. It calls `server.handoff(...)` as an ordinary Python function, and an annotation validates
nothing on that path — pydantic only runs inside the SDK's tool wrapper, which a direct call never
reaches. The rest of the suite is one layer further down still, entering `Workflow` itself. So the
whole suite agreed the int worked, and it does work everywhere except the one place an agent
stands. The single test that does touch the wire side, `test_server.py`'s roster check, reads
`list_tools()` for NAMES — never for an input schema. This is the LAYER gap #657 named — before
that card nothing here round-tripped a tool
argument across the wire — and the fix is the same shape: the new
`tests/unit/test_sibling_target_argument.py` drives the REAL `MCPServer` over REAL stdio against
the echo Workflow in `_stdio_arg_probe_server.py`.

**Widening beat correcting the prose, and the CONTROL is what decided it.** Measured in one run
at the real boundary: `file_task(project_id=17)` lands as an int and `file_task(project_id="17")`
lands as an int too — the cross-project door that already existed takes BOTH shapes. So
`to: str | int` makes all three doors take an id either way, where "quote the id" would have
made this pair the odd one out. Not one SHAPE, and the distinction is worth keeping: read off
`list_tools()`, `file_task.project_id` still advertises `{"anyOf": [{"type": "integer"},
{"type": "null"}]}` and refuses a sibling NAME outright, accepting `"17"` only through lax
coercion. The BEHAVIOUR agrees; the schemas do not. The shape an agent actually holds settles
it further: `siblings` rides in
every `next_task` payload as JSON NUMBERS (`{"backend": 17}`), so copying the id you were just
handed is the natural motion, and a docstring asking for a quote is a quirk that will be got
wrong repeatedly. The generated schema is now `{"anyOf": [{"type": "string"}, {"type":
"integer"}]}` — and the schema, not our annotation, is what an agent reads.

**What the union does NOT buy, measured rather than assumed.** Lax pydantic renders a JSON
`true` as the integer 1, so `_resolve_sibling`'s `isinstance(to, bool)` guard is unreachable
from the wire — the body is handed a plain 1. A float is still refused (it belongs to neither
member). This is NOT new and not this card's doing: `file_task(project_id=true)` has arrived as
project 1 on today's SDK, measured in the same run as its control, and its door has carried
`project_id: int | None` since `f0e7aef` — no claim is made about the years in between, because
the boundary machinery underneath was swapped wholesale at `0543463` (FastMCP -> MCPServer) and
nobody measured the old one. The annotation that closes the hole WHILE KEEPING the int shape is
`StrictInt | StrictStr` — measured in raw pydantic, NOT at the boundary, which is the one claim
in this section not taken where the section itself insists claims must be taken. (`to: str`
closes the bool hole too, by refusing everything non-string; that is what the card started
from.) It needs pydantic at server.py MODULE scope, and that is a NEW import on the path #521
cleared rather than the same one: measured, `import vikunja_mcp.server` today leaves `pydantic`
out of `sys.modules` altogether, and it is a far cheaper import than the SDK's
(~26-80 ms against ~0.43 s). Filed as
VMCP-307 (1207) with the trade written out; the test asserts that both doors behave the SAME, so
fixing one alone goes red and asks about the other.

## Duplicate `add_label` — four routes, and the cause was the BYPASS (#1216)

**The defect in one line: real 2.3.0 refuses a label the task already carries, and `workflow`
had no single place where "put a label on a card" happens, so four routes reached that refusal.**
Measured through this package's own client on a throwaway container: `api.add_label` on a
duplicate raises `VikunjaError status=400 message='{"code":8001,"message":"This label already
exists on the task."}'`, and a third add answers the same — a STATE, not a once-off. A duplicate
assignee answers the same class one endpoint over, `400 code 4021`; that one is already guarded
(`claim`'s `self_heal` skips the PUT when I am the only assignee) and was measured in the same
round as the precedent for the shape chosen here.

**The four routes, driven end to end through the real `Workflow` over `FakeAPI` with agent tools
only, then re-driven against the real server at the pre-fix commit `f7de8d7`.** A second
`review_task(id,'approve')` — the only one needing NO human step, and reachable exactly as
SKILL.md warns since #991, an orchestrator dispatching two reviewers onto one card within a tick.
A `needs_work` on a card a human hand-dragged back to Review still wearing `review-failed`
(`advance` clears verdict labels; a hand-drag fires no tool). A `return_task` on a card a human
labelled `blocked`. A `decompose` on one already labelled `epic`. The card that filed this named
two and said `_add_label` had three call sites; the real numbers are four and five.

**Why a guard per site was the wrong fix, and this is the whole post-mortem.** `return_task` and
`decompose` did not call `_add_label` at all — each INLINED its two lines, `get_or_create_label`
+ `api.add_label` — and the one guard that existed (epic-ready) was that site's own `continue`,
not the helper. So the state everyone reads as "one site remembered and the others forgot" was
really "there is no write path where the invariant COULD be stated", and a per-site guard would
have reproduced it one round later. `_add_label` is now the single write path and carries the
guard, and `test_api_add_label_has_exactly_one_caller_in_the_package` (in
`tests/unit/test_workflow_duplicate_label.py`) reads the package source and goes red on the next
inline copy, naming the file that did it.
It takes the task SNAPSHOT (dict) rather than an id, mirroring `_remove_label` beside it — every
call site already holds a fresh task dict, so idempotence costs ZERO extra requests, and two
matched signatures are what makes the next bypass unlikely. Swallowing the 400 inside
`api.add_label` was rejected for the LAYERING reason — a workflow idempotency decision does not
belong in the REST client — and because a sniff on the message would swallow a genuinely
different 400. This sentence used to LEAD with truncation (`VikunjaError` carries `r.text[:300]`),
which is real in general but does not bite for THIS body: measured, it is 64 characters, so a
sniff would never meet the cut. Reordered on the reviewer's non-blocking finding, #1216 rework.

**What the guard closes is the STATE, not the RACE.** A label added by someone else between the
board read and the PUT still 400s — the same residual `_remove_label` documents. What makes
the snapshot trustworthy here was measured and not assumed: `api.view_tasks` against real 2.3.0
returns the kanban copy with `labels` POPULATED (`["reviewed","blocked"]`), so this is not the
#125 hollowing mode, where labels read as None off a `related_tasks` sub-dict and a check on them
silently no-op'd in production while the too-generous fake stayed green.

**The ORDER in `review_task` changed, and the measurement is what decided it.** It used to write
the verdict COMMENT first and the label second, so a failed label write left the report on the
card and the label absent. Constructed on the real server at `f7de8d7`: after the refused second
approve the card carried 2 `[review]` comments — the second reviewer's report present — while
the caller saw only a failure; and the `needs_work` route left `[review] NEEDS WORK` in the
journal with the card STILL IN REVIEW, never moved. Which orphan is recoverable was then driven
through the real `next_task`: a card with a `[review]` comment and no label is NOT offered again
(the offering compares the last `[worklog]` against the last `[review]` COMMENT and never reads a
verdict label), so nothing routes a reviewer back to it automatically — which is a narrower claim
than "unrecoverable", since `review_task` gates on stage alone and a human handing someone the id
still lands a verdict. A card with the label and no comment is offered exactly as it was before
the failure. So labels go first
and the comment last on both branches. The new failure mode is exactly that second state — a
verdict label with no report — and it self-heals on the next tick. `_add_label` stays BEFORE
`_remove_label` in the pair: if the add fails, the prior verdict label survives rather than being
cleared for a verdict that never landed. `_mark_epic_if_children_complete` already wrote
label-then-comment, for a neighbouring reason of its own: there the label IS the idempotency key,
so a partial failure must leave the epic consistently marked. Same direction, different argument.

**The fake was more generous than the server, and that is why a whole green unit suite (1367 at
`f7de8d7`) coexisted with four live routes.** `FakeAPI.add_label` appended a second copy. It now
raises the measured 400. Two rounds price that mirror honestly, against a control of 0 failed / 0
errors / 131 collected on the same selection: remove the mirror and leave the guard -> 1 failed,
only the mirror's own pin, because the route tests assert the label COUNT and see a duplicate
append as readily as an exception; remove BOTH -> 7 failed. So the mirror is a 1:1 rule this repo
keeps, not the thing holding the route pins up. The full ten-round table lives in the test
module's own docstring.

**THE ROUND THAT WAS WRITTEN UP AS A BLIND SPOT WAS NOT ONE, AND THE REASON IS THIS CARD'S OWN
DEFECT ONE LEVEL UP.** Handing the epic-ready site the hollowed `parent` sub-dict instead of the
re-fetched `full_parent` measured 0 failed, and that was read here as "There is nothing observable
to catch — that site reaches `_add_label` only after its own `continue` has established the label
is absent". The `continue` established no such thing: it asked `_has_label`, which THEN compared
titles EXACTLY (#1256 routed it through `api.label_key`), while the guard THEN resolved through
`get_or_create_label` and asked by label ID (#1456 returned it to `_has_label`, so the two ask one
question now) — the very disagreement `root_cause` above is built on.
On a parent a human marked `Epic-ready` they differ,
and the difference is observable. Constructed and driven through the real `advance` over
`FakeAPI`: as shipped the guard FIRES, the PUT is skipped, the `[epic-ready]` comment lands and
nothing raises; with the hollowed `parent` the guard is blind, the PUT answers `400 code 8001`,
and because the marker is best-effort the whole thing dies as ONE swallowed stderr line. With a
single parent the LABEL is byte-identical in both worlds and only the comment is lost; with TWO
epic parents the 400 aborts the `for parent in parents` loop, so the SECOND parent loses the
label as well — the half a human reads off the board. The pin is
`test_epic_ready_on_parents_where_a_human_typed_the_marker_CAPITALISED`, and with it in the
selection that same swap is 1 failed against a control of 0 failed / 0 errors / 131 collected.
The isolating pair, same selection with the pin DELETED: pristine 0 failed / 0 errors / 130
collected, hollowed `parent` 0 failed / 0 errors / 130 collected — the old 0 reproduces exactly,
and nothing else in the selection sees the swap. **The lesson is the one CLAUDE.md already
states and this file briefly stopped following: a round returning 0 says the SELECTION held no
case that distinguishes, never that no such case exists — and prose that upgrades the first into
the second forecloses the pin nobody then writes.**

**THE GUARD'S FIRST DRAFT LEAKED, AND THE SECOND INDEPENDENT PASS IS WHAT CAUGHT IT.** It read
`if self._has_label(task, title): return`, which THEN compared titles EXACTLY, while
`api.get_or_create_label` resolves case- and whitespace-INSENSITIVELY on purpose (a bot typing
`Bug`/`bug ` once forked a duplicate label; api.py records the date). The two therefore disagree
about what "this label" means, and the gap is the whole defect again. Constructed on the real
container: a card carrying `Vari906071` with no lowercase twin anywhere gave
`_has_label(card,'vari906071') -> False`, `get_or_create_label('vari906071') -> that same label`,
and the PUT `400 code 8001`. The guard now resolves FIRST and asks whether THAT LABEL ID is on
the snapshot — the same question the server asks — after which the same construction is a clean
no-op, re-measured on the same container. Two things went with it. `FakeAPI.get_or_create_label`
was EXACT-match and is now 1:1 with the client, without which no unit test could see this at all
(and it is not merely invisible under the old fake: the round that restores exact matching turns
the new pin RED, because such a fake mints a second label where the server refuses). And the
sentence in `_add_label` claiming the STATE was closed was narrowed, because it was not: what was
closed was the exact-title state.

## The READS disagreed with the WRITES about what a label is — thirteen more sites (#1256)

**The defect in one line: `api.get_or_create_label` has always resolved a label title case- and
whitespace-INSENSITIVELY, on purpose, while every gate in `workflow` asked `lb["title"] == title`,
EXACT — so a label a human typed capitalised in the web UI EXISTED as far as every WRITE in this
package was concerned and DID NOT EXIST as far as every GATE reading it was concerned.** This is
the section above's own root cause, one layer wider: #1216 closed exactly one instance (the guard
inside `_add_label`, re-keyed to the resolved label ID) and its reviewer scoped the reads out.

**Reproduced before anything changed, on a live `Workflow` over `FakeAPI` with the agent tools,
one variable per pair — the SPELLING — each variant against its lowercase control.**
`advance(to='review')` with NO `root_cause`: `bug` REFUSED (the #718 gate); `Bug`, `BUG` and
`bug ` all ADVANCED, and the payload said `review_kind: 'change'` in the same breath. So a bug fix
reached its reviewer with no cause, which is precisely the state #718 exists to make impossible,
and it failed OPEN — nothing said so. `next_task` over a free Queue card: `blocked` withheld;
`Blocked`, `BLOCKED` and `blocked ` OFFERED, i.e. the way a human PARKS a card did not park it.

**THE CARD GUESSED THE `epic` FAMILY WAS THE MILD ONE AND THAT WAS THE ONE THING WORTH
RE-MEASURING.** Its scope note reasons that an epic container is created by `decompose`,
which writes the label itself, so a human variant there is far less likely than on `bug`/`blocked`,
which humans do type by hand. That has the causation backwards.
`decompose` writes through `_add_label` -> `get_or_create_label('epic')`, which RESOLVES to
whatever `Epic` row the board already holds, so the container the PACKAGE creates carries the
HUMAN's spelling. Driven end to end with a pre-seeded `Epic` and nobody typing anything: the
container comes out labelled `Epic`, `claim(container)` is then ACCEPTED (control, no pre-seed:
REFUSED, "is an epic CONTAINER") and `next_task` OFFERS it (control: False). The write path
manufactures the disagreement; no site was safe by construction.

**A FOURTEENTH COMPARISON THE CARD DID NOT NAME, measured rather than added by symmetry.**
`_remove_label` is the same `x.get("title") == title`, two helpers along from `_has_label`
(`_add_label` and its docstring sit between them). A card a human
hand-dragged Review -> Build still wearing `Reviewed` kept that badge through `advance(to='review')`
(control `reviewed`: cleared) — a stale APPROVE riding into a fresh Review, which is exactly what
`_clear_verdict_labels`' own docstring forbids. `transfer_task`'s cleanup of
`blocked`/`reviewed`/`review-failed`/`epic-ready` reads through the same comparison.

**THE CENSUS, verified rather than inherited: 13 `_has_label` CALL EXPRESSIONS on 12 SOURCE LINES**
(`advance`'s `root_cause` line carries two), in five methods — `next_task` 5, `claim` 1,
`_mark_epic_if_children_complete` 2, `advance` 4, `transfer_task` 1 — plus `_remove_label`'s own.
The card's "twelve more read sites" is right on the LINES reading. Two things in its SCOPE
paragraph are not: it names "the `epic` checks in `decompose`/`advance`/`return_task`", and
`decompose` and `return_task` contain no `_has_label` at all — they only WRITE labels — and it
omits `_remove_label`.

**THE FIX IS A SHARED KEY, NOT A SHARED ROUTE, and the difference is the whole design.** A new
module-level `api.label_key(title) -> (title or "").strip().casefold()` states the rule once;
`get_or_create_label`, `_has_label` and `_remove_label` all read with it. The obvious alternative —
give the reads `_add_label`'s shape, resolve through `get_or_create_label` and compare label IDs —
was rejected for a reason that is not cost: **`get_or_create_label` CREATES the label when absent,
so a READ gate would MINT labels**, and `vikunja-mcp claimable` is READ-ONLY BY CONTRACT while the
hgdev-acp hub polls it per loop tick through the real `next_task`. That is a per-poll tracker
mutation. (The cost is real too — a paged `labels()` read per call, on `next_task`'s hot path, per
card.) Casefolding INLINE in `_has_label` was rejected as the card's own worry, "a SECOND spelling
of what this label is, and this repo has just paid for having two", and because it leaves
`_remove_label` leaking beside it; #1216's post-mortem is literally that a per-site guard
"would have reproduced it one round later". BUCKET titles (`bucket["title"] == "Review"`) stay
EXACT and are untouched — those are canonical names this package's own `setup` writes.

**WHAT IT COST #1216's TWO VARIANT PINS, said here because the alternative is a reader believing
they still measure what they were written to measure.** Both asserted `not _has_label(...)` as
their PREMISE, and #1256 inverts it, so both had to be re-premised. The epic-ready one changed
BEHAVIOUR, for the better: on a parent a human marked `Epic-ready` the site's `continue` now SEES
the mark and the site never reaches `_add_label` at all, so the parent is left as the human left
it — measured `(1 label, 0 comments)` where it used to be `(1, 1)`, the second being a
re-announcement of a mark already there. That is what the `continue`'s own comment always claimed
("already marked — idempotent") and it was false for every spelling but one. **And it cost
`_add_label`'s ID-keyed guard its pin: keying that guard on the title instead now kills NOTHING —
0 failed against a clean control of 0 failed / 0 errors / 1399 collected over the WHOLE of
`tests/unit`, and 0 again on the narrower sweep selection, where #1216 had that same row at 2.**
The two agree on every state this package's ORDINARY
write path creates: every `_add_label`/`get_or_create_label` call site passes a lowercase `LABEL_*`
constant (walked with `ast`), so that path mints one row per normalised title. What tells the two
guards apart is a board carrying TWO rows of the same normalised title — and there are TWO such
boards, which this file used to collapse into one: two SPELLINGS (`blocked` AND `Blocked`), and the
same spelling TWICE (`blocked` and `blocked`).
**Of the first this file said, flat, that only an outside actor mints it — and the retraction
belongs on the SECOND, which is why writing it here as a blanket correction would have been a
second error.** `api.get_or_create_label` is read-`labels()`-then-`create_label` with nothing
atomic between the two, so at `wip_limit > 1` two agents adding the same absent label both miss and
both create; and because every call site passes the same constant, the two rows they mint carry the
SAME spelling. That is a divergent board the package reaches with nobody outside it. For the
two-SPELLINGS board the old sentence was right about this package's own writes — nothing here ever
passes `Blocked`. `_add_label`'s docstring names BOTH boards, which is exactly why its correction
is sound and this file's sentence was not. (It offers a second route as well — `GET /labels`
surfacing only what the caller can READ — and that half was pinned by nothing at #1256: api.py
records the visibility in the opposite direction, "not just its own", and the docstring's
cross-reference to this module points at no sentence in it. **RETRACTED BY #1456, which measured
the exclusion with a control — see its section below.** The sentence is left standing rather than
swapped because it is #1256's own record of what it found.)
#1256's own second pass raised this and its author accepted it, after which the correction landed
in one of the FIVE copies of the rule and in none of the other four, of which this was one. A rule
stated five times and drifted in four is #1256's subject matter one layer up, which is why the
retraction is written out here instead of the sentence being quietly swapped.
MEASURED over `FakeAPI` and the real `_add_label`, with `_has_label` as the alternative guard —
which is the alternative VMCP-316 (1456) actually asks about: rows `[blocked, Blocked]`, card
carrying `Blocked`, the ID guard sends the PUT and the card comes out with BOTH
(`['Blocked', 'blocked']`) where the `_has_label` guard skips and leaves the one; rows
`[blocked, blocked]`, card carrying the second, `['blocked', 'blocked']` against `['blocked']`.
THREE of the six arrangements diverge, not one, and the ID guard is the duplicating one in every
one of them. (**Both figures are #1256's and both were UNDERSTATED — #1456 drove the full space
and it is FOUR of EIGHT**, the omitted board being `[Blocked, Blocked]`. Left as measured here,
corrected in that card's section below, because the direction of the error is the safe one: every
row above reproduces.) Neither raises — but a BYTE-EXACT title comparison, a third reading of
"title guard",
does: rows `[Blocked, blocked]` with the card carrying `Blocked` sends a PUT that answers
`400 code 8001`, which is #1216's leak exactly.
The guard was kept AS IS at #1256, because #1216 measured it
against a real 2.3.0 and trading a measured decision for an unmeasured one is the wrong direction;
the question was FILED, not answered — VMCP-316 (1456), which ran the probe and answered it the
other way; see its section below. The neighbouring residual is VMCP-317
(1457): `_remove_label` takes only the FIRST matching row, so a card wearing BOTH `reviewed` and
`Reviewed` keeps one — measured `['reviewed', 'Reviewed']` before `advance`, `['Reviewed']` after.

**THE DIRECTION THAT STRANDS WORK GETS ITS OWN PIN.** Normalising makes a gate FIRE where it did
not, and for `blocked` and `epic` that means cards DISAPPEARING from the offering — the fix, and
also the direction in which work goes missing without anyone noticing. So
`test_a_variant_blocked_label_keeps_the_card_out_of_the_offering` asserts the withholding AND its
control (the same board with the label removed hands the card back), because an empty offering
proves nothing on its own: a stand with no claimable work looks identical.

**Where the pins live:** `tests/unit/test_workflow_label_variants.py` (the consequences, the
invariant itself, and the anti-drift source pin that `label_key` is the only spelling of the rule
in `api.py`/`workflow.py` — read with `ast`, because the first draft grepped the text and went red
on its own subject matter: `label_key`'s docstring says `.strip().casefold()` out loud). The sweep
table is in that module's own docstring.

**THE REWORK ROUND WAS ENTIRELY PROSE, AND ITS LESSON IS THE CARD'S OWN ONE LAYER UP.** The
independent review re-derived the whole behaviour change — the census, both named consequences on
the parent and at `57762ef`, the over-match direction across a wide variant set, every sweep row —
and would have approved the six lines unchanged. What it sent back were three sentences. The
rework's OWN second pass then widened the first of the three from three sites to four, refuted one
figure the review had handed over, and found two further sentences that measurement contradicts —
so the round that was about claims outrunning their evidence needed a fresh context twice more to
stop doing it.

**A retraction that reaches ONE copy of a rule stated FIVE times leaves four live, and accepting
the finding is not the same as landing it.** #1256's own second pass raised "only an outside actor
mints two rows", the author accepted it and said so in the `[worklog]` — and the correction then
landed in `_add_label`'s docstring alone. Four copies kept the narrow reading, all of them `+`
lines of the SAME commit as the correction: this file, the
`test_a_title_VARIANT_does_not_slip_past_the_guard` docstring, the Russian comment in
`_remove_label`, and — the one the rework's own second pass had to find — a clause SEVEN LINES
ABOVE the correction, inside the very docstring that carries it. The missing step is cheap and
mechanical: when a claim is retracted, `git grep` the retracted PHRASE before writing the fix,
because "I accepted that finding" is a memory of a decision and not a record of an edit. That the
fourth copy sat in the same docstring as its own correction is the sharpest form of the lesson
available: proximity is not coverage. The same reflex is what the card's `label_key` is for at the
code layer — one statement of a rule, so there is no second copy to drift.

**AND ANNOTATING SOME ROWS OF A TABLE IS WORSE THAN ANNOTATING NONE.** #1256 moved six of the ten
rows in #1216's sweep table and annotated exactly two, which manufactures the reading that the
other eight are current; FOUR of those eight were not, and the two it DID annotate are the
remaining two moved rows — both annotated wrongly. The fix is NOT to re-measure someone else's
landed record — SKILL.md forbids retroactively rewriting one — but to give the table the TREE it
was measured on, `131 collected at `d80c174``, which settles every row at once. That anchor was
then checked rather than asserted: at `d80c174`, extracted with `git archive`, the control is 0
failed / 0 errors / 131 collected and ALL TEN rows reproduce their stated figure exactly. What an
anchor does NOT cure is a wrong claim written by the ANNOTATING card, and this card wrote two. Its
note on the hollowed-`parent` row named a 7 belonging to a BROADER mutation and blamed
`_has_label` resolving, when that 7 is 7 on `d80c174` and `edbb8e4` too — `_has_label` iterates
`task.get("labels") or []`, so a hollowed `labels: None` misses however titles are compared. And
its note on the exact-TITLE row gave one number for a mutation that #1256 itself SPLIT IN TWO:
"the exact TITLE" and "the guard's own first draft" named one mutation before this card and two
after it, and they measure 1 and 0. Both are corrected at their rows.

## The guard the last two cards left unpinned, decided on the server rather than on the fake (#1456)

**The decision in one line: `Workflow._add_label`'s idempotency guard is `if self._has_label(task,
title): return` again, and the resolved-ID keying #1216 put there is gone — because the probe both
earlier cards left open says the boards the two forms disagree on are boards a real 2.3.0 PERMITS,
and on every one of them the ID form is the one that writes a second row for one concept.**

**WHAT THE CARD INHERITED, and it was NOT the card's own opening claim.** #1456 was filed saying
the ID guard "is now pinned by nothing" and that it has ONE divergent state. #1256's rework then
posted a constructed six-row table that narrows both halves, and that table is where this card
starts: "pinned by nothing" holds only where "title guard" means `_has_label`, since a BYTE-EXACT
title comparison is still CAUGHT and additionally raises `400 code 8001` on rows
`[Blocked, blocked]` with the card carrying `Blocked` — #1216's leak exactly, and re-measured HERE
on the real container rather than only over `FakeAPI`: in one run against it the byte-exact form
raised that 400 while both `_has_label` and the resolved-ID form skipped; and — the table's own
wording — THREE of the six two-row arrangements diverge, not one. Both narrowings were
re-derived here rather than taken on
trust, and both hold — though one of them turned out to be UNDERSTATED, which is the more useful
outcome. #1256's six ROWS reproduce exactly when driven through both forms over `FakeAPI` from
scratch, byte-exact `RAISES` row included; its SPACE does not, and this card inherited the space
while re-deriving the rows. Over two spellings there are four ORDERED two-row boards and two
carriers each — EIGHT — and `[Blocked, Blocked]` appears in neither that table nor this card's
first draft. Driving all eight: **4 of 8 diverge**, the ID form duplicating in all four, and the
missing board confirmed server-reachable too (two rows spelled alike, the card wearing the second,
the PUT of the first ACCEPTED on a real container). The rule underneath is simpler than any
census: `get_or_create_label` returns the FIRST matching row, so the forms differ exactly when the
card wears the SECOND. Found by this card's own second independent pass, which is the point of
having one — the verdict got STRONGER, the count was wrong in the safe direction.
The byte-exact narrowing's FIGURE moved and its claim did not: #1256 measured that
mutation at 1 failed on its narrower three-file selection, and it is 2 on the five-file selection
here, the second failure being one of the pins this card adds.

**THE PROBE, run on a throwaway real 2.3.0 through this package's own client — the one question
#1216 and #1256 both left open.** Three answers, all ACCEPTED. (1) `PUT /labels` with a title that
already exists BYTE-IDENTICALLY is accepted, yielding two rows of one spelling; (2) with a CASE
variant, likewise; (3) `PUT /tasks/{id}/labels` with the SECOND such row, onto a card already
wearing the first, is accepted too — probed in all three arrangements (the same-spelling pair, the
card wearing the capitalised row, the card wearing the lowercase row) — and the card comes out
wearing BOTH. So the refusal tracks the `label_id` and not the title — a two-point boundary (same
id refused, a different id of the same normalised key accepted), which is what the guard needs and
is not the same as having enumerated everything the server keys on — and two rows for one concept
on one card is a state it allows rather than an artefact of a too-generous fake.
Both facts are now pinned where only they can be, in
`tests/integration/test_duplicate_label.py`, beside `test_duplicate_add_label_is_a_400` whose exact
boundary they draw.

**WHY THAT DECIDES IT AGAINST THE ID KEYING, in descending strength.** FIRST, neither form can 400
ON A FAITHFUL SNAPSHOT, and that much is provable rather than measured: `_has_label` and
`get_or_create_label` read with the SAME `api.label_key` since #1256, so "no row on the card
normalises to `title`" and "the row `get_or_create_label` returns is not on the card" are one
statement, and the PUT goes out only where the server accepts it.
**THE TWO RESIDUALS ARE NOT EQUAL, THOUGH — this text said they were, and its second pass refuted
it.** The ID form needs only the snapshot's label IDS to be faithful; the shipped form needs its
TITLES too. Measured over `FakeAPI`, three constructions with the same answer each: a row RENAMED
between the board read and the PUT, a snapshot label with no `title` key, one whose title is
whitespace — the shipped form raises `400 code 8001`, the ID form skips. That is the ONE real
argument for the ID keying and it is written here rather than buried, together with what bounds it:
no server payload measured here produces the title-blind shapes (`get_task` and the kanban
`view_tasks` copy both return full label objects with titles; `related_tasks`' `labels: null`
blinds both forms alike), and this package has NO label-rename path at all — `api.py` carries
`create_label`, `add_label` and `remove_label` and nothing else — so the live window needs a human
renaming a row in the web UI between one read and one write. The shapes decide it, not the odds:
this residual fails LOUD, as the `400` #1216 was about, in a window nothing here opens; the ID
form's failure is a SILENT extra row on a settled board, for every card that passes through.

SECOND, on the boards where
they differ the ID form resolves to the row the card does NOT wear, sees a different id, sends the
PUT, and leaves the card wearing both — and the probe says the server takes it, so that outcome is
real and not a fake's generosity. THIRD, the divergent board is one this package reaches ALONE:
`get_or_create_label` is read-`labels()`-then-`create_label` with nothing atomic between, so at
`wip_limit > 1` two agents adding the same absent label both miss and both create — and the probe's
first answer is what upgrades that route from argued to REAL, since the server forks a row instead
of refusing the duplicate title. What the ID form did with such a board was to SPREAD it onto every
card passing through the helper, which is the proliferation `get_or_create_label` exists to prevent,
one level down, and the feedstock for VMCP-317 (1457) — whose own description asserts that this
package cannot create its two-row state ("`_add_label` can only ever attach one"), a claim that was
FALSE under the ID keying and is true under this form. NARROWED, not closed: the snapshot race
`_add_label`'s RESIDUAL names still lets two agents land two rows on one card.

**WHAT THE CHOICE COSTS, because it is a trade and not a cleanup.** A human who typed `Blocked` by
hand and then filters the board by the `blocked` row will not find a card this helper skipped; the
ID form would have put both rows on it and both filters would hit. That is reasoning about the web
UI's filter, NOT a measurement — no probe here read the UI. It is written down because it is the
one argument on the other side, and it loses to the duplication above: the card already reads as
carrying the label at every `_has_label` gate, the anomaly is in the label LIST rather than on the
card, and copying it onto every card touched makes the human's cleanup larger.

**THE PIN, which is the half the card's TITLE was about.** `_add_label`'s guard is pinned by
`test_the_guard_SKIPS_rather_than_minting_a_second_row_on_a_two_row_board` in
`tests/unit/test_workflow_duplicate_label.py`, which builds all four divergent arrangements and
asserts the skip, plus `test_the_no_op_path_does_not_read_the_label_list_at_all` on the order (the
skip is decided BEFORE the resolution, so the no-op path costs zero requests). Sweep, five rounds on
#1256's five-file selection against ONE control of 0 failed / 0 errors / 156 collected: restore the
ID keying -> 2 failed (both new pins); a byte-exact title guard -> 2 failed (the variant pin plus
the two-row pin); delete the guard entirely -> 7 failed. **The isolating pair is what makes the
first row mean anything**: the ID keying with the two-row pin deleted -> 1 failed / 0 errors / 155
collected, and with BOTH new pins deleted -> 0 failed / 0 errors / 154 collected, reproducing
#1256's "kills nothing" figure exactly on this tree; the pair's other half, UNMUTATED with the same
two pins deleted, is 0 failed / 0 errors / 154 collected — the same selection size, so the 0 above
is the mutation going unseen and not the selection shrinking. So the two new pins are the only
things in the selection that see the swap. Against a real container the same shape holds for the
server-side pin: control 12 passed / 0 failed, ID keying restored **1 failed**, and that one is
`test_the_guard_skips_on_a_REAL_two_row_board_and_leaves_one_row`.

**THE SECOND ROUTE TO THE DIVERGENT BOARD WAS UNPINNED AND IS NOT ANY MORE — a finding this card
made while building its own fixture, and fixed here rather than filed.** `_add_label`'s docstring
offered `GET /labels` surfacing only what the caller can READ as a second route to a two-row board,
and both #1216 and #1256 marked that half pinned by nothing: api.py stated the visibility in the
WIDENING direction only ("not just its own"), and the exclusion was an inference on top of it. It
surfaced by itself: the first draft of this card's own server-side two-row pin FAILED ITS OWN
PREMISE, because the row it created first was invisible to the agent's scoped token and the agent
resolved to the other one. Measured then, with a control and one variable: a row owned by the boss
and used on no task the agent can read is ABSENT from the agent's `GET /labels`; the SAME row
APPEARS once it is put on a task in the shared project; the boss sees both throughout, so the
absence is visibility and not existence. The sharp consequence is not the mint the docstring named
but a DISAGREEMENT: at that moment the two callers resolve one title to DIFFERENT rows on the same
board, i.e. this card's divergence arriving with no race at all. The MINT still follows from
`get_or_create_label`'s own two lines rather than from a probe and is not claimed as measured.
Pinned by `test_a_label_on_no_readable_task_is_INVISIBLE_to_another_caller`, and api.py's comment
now states both directions.

**AND THE SERVER-SIDE PIN ALMOST SHIPPED MEASURING NOTHING, which is the lesson worth more than
the pin.** Its first draft created the CAPITALISED row second and had the card wear it — an
arrangement where the resolution returns the row the card already wears, so both forms skip. It
PASSED, and it passed under the ID keying too: 12 passed either way. Only the mutation round caught
it, which is this repo's own rule doing its job — a pin is proved by deleting the guard it names
and requiring RED, never by watching it go green. Rebuilt on the divergent arrangement it is
control 12 passed / 0 failed, ID keying restored **1 failed**, and that one is
`test_the_guard_skips_on_a_REAL_two_row_board_and_leaves_one_row`.

**`FakeAPI` NEEDED NO CHANGE, and #1456 is where that stopped being an assumption.**
`FakeAPI.create_label` appends unconditionally and `FakeAPI.add_label` refuses on `label_id` alone —
both already 1:1 with what the container answered. That is worth recording rather than passing over,
because "the fake was more generous than the server" is the mode that hid #1216, and here the fake
turned out to be exactly as generous as the server on both facts. That table rests on a THIRD
server behaviour the sentence here used to leave uncounted, which its second pass caught: `GET
/labels` returning rows in CREATION order, which is what makes `get_or_create_label` resolve to the
first-created row and therefore what makes any given arrangement the divergent one. It is asserted
by the new integration tests rather than merely assumed (`== same[0]["id"]`, and the ordered list
comparison in the visibility pin), so it is pinned — but nothing documents it as a server
guarantee, and if a later Vikunja orders that endpoint differently those pins are where it will
show.

## `_remove_label` took only the FIRST matching row (#1457)

**The defect in one line: `_remove_label` resolved the title the way the SERVER does
(`api.label_key`, #1256) and then picked the first match with `next(...)`, sending ONE DELETE — so
on a card carrying two board rows that normalise to one label, one row survived every clearing
path in the package.** #1256 measured it, named it, and left it open on purpose: closing it is a
behaviour change outside that card's slice.

**WHAT THE SURVIVOR COSTS IS NARROWER THAN THE OBVIOUS SENTENCE, and the obvious sentence is what
this section said until its own second independent pass measured it.** It said the survivor "is
READ as the label by every `_has_label` gate". NO `_has_label` GATE READS `reviewed` AT ALL —
censused on the landing tree, the reads name `epic` (8), `bug` (3), `blocked` (1) and `epic-ready`
(1) and nothing else — and `_clear_verdict_labels`' own docstring says so in as many words: the
review offering keys on `[worklog]`/`[review]` comment freshness, so a stale `reviewed` would NOT
suppress a re-review. The harm is the one that docstring actually names, `ложь на доске`: the badge
a HUMAN reads off the card before moving it to Done, plus the broken mutual exclusion (a
`needs_work` leaves the card wearing both verdicts). The `blocked` family is NOT symmetric with it
and the flattened sentence hid that: there a gate really does act — `next_task` withholds a
`blocked`-labelled card — so case E below is a gate consequence and case D is a board-lie one. The
general lesson is this repo's usual one: a consequence measured on one label family was
generalised onto another without being asked.

**Reproduced before anything changed, on a live `Workflow` over `FakeAPI` with agent tools only,
each case against its one-row control.** Both rows minted with `api.create_label`:

    A  advance(to='review'), card wearing `reviewed` + `Reviewed`
         before ['reviewed', 'Reviewed']            after ['Reviewed']
    B  CONTROL, one row                 before ['reviewed']  after []
    C  three rows, `reviewed`/`Reviewed`/`REVIEWED `         after ['Reviewed', 'REVIEWED ']
    D  review_task(verdict='needs_work') on the two-row card after ['Reviewed', 'review-failed']
    E  transfer_task, card wearing `blocked` + `Blocked`     after ['Blocked']

D and E are the two the card's own description reaches for and neither had been driven: D leaves
BOTH mutually-exclusive verdict labels on one card, so it reads as approved AND rejected to the
human who moves it to Done; E carries a block about a board the neighbour cannot see onto the
neighbour's board, where their human triages it — and there the surviving row does hit a gate.

**THERE ARE TWO TWO-ROW BOARDS AND THIS FILE HAS ALREADY RETRACTED THE COLLAPSED VERSION ONCE, so
the new section states them apart rather than repeating the mistake ~250 lines below its own
retraction.** Two SPELLINGS (`reviewed` + `Reviewed`) come from a HUMAN typing one in the web UI;
this package never writes them, because its single production `get_or_create_label` call site is
inside `_add_label` and every caller passes a lowercase `LABEL_*` constant. The SAME spelling twice
(`reviewed` + `reviewed`) is the board the package reaches UNAIDED: `get_or_create_label` is
read-`labels()`-then-`create_label` with nothing atomic between the two, so at `wip_limit > 1` two
agents adding the same absent label both miss and both create — and because both pass that same
constant, the rows come out spelled alike. The first draft of this section attached the race to the
two-SPELLING board, which is exactly the collapse #1256's section retracted; the consequence pin now
drives both boards, and until this card nothing exercised the one the package can build by itself.

**THE 403 QUESTION IS ANSWERED BY THE LOOP'S SHAPE, NOT BY TOLERATING THE FAILURE — and that is
the whole design decision.** Real 2.3.0 answers `DELETE /tasks/{id}/labels/{label_id}` with 403
`Forbidden` when the label is not on the task (measured in #1211; recorded in
`FakeAPI._read_task`), so the naive reading of "delete every match" — N chances at a benign
refusal — would turn a clearing into an error. It does not arise: every DELETE the loop sends
names a DISTINCT `label_id` that WAS on the caller's snapshot, because two rows are two different
board rows and a duplicate ADD of one id is refused by the server (400 code 8001) and by the fake
alike, so a snapshot the SERVER filled does not carry one id twice. The residual is the same
snapshot-staleness RACE that was already there, which `FakeAPI._read_task` grades as "a race, not a
route" — and that grading is what licenses the fake's idempotent-no-op divergence on this endpoint,
so it is load-bearing that the loop does not disturb it. The way the loop itself could manufacture
that 403 is by sending one id twice, so the match list is DE-DUPLICATED BY ID, with its own pin —
and the GUARD is what carries the safety while the server's 400 only supports it, because the loop
iterates a CLIENT-SIDE dict and not server storage. Two neighbouring properties have no pin at all
and the sweep table says so: the match list is MATERIALISED before the first DELETE, and `x["id"]`
is read only on a row that already matched (a preservation of where `next(...)` read it, whose one
measured consequence needs a snapshot no server produces — a matching row with no `id` raises
`KeyError` out of the match loop, so on a two-row card one malformed row now aborts the clearing
where the old code would have cleared the other, and `KeyError` is not in `server.py`'s `_tool`
catch list).

**THE 403 WAS RE-MEASURED FIRST-HAND RATHER THAN INHERITED, on a throwaway real 2.3.0 raised for
this card (`vikunja/vikunja:2.3.0`, port derived from the task id), because the whole design rests
on it and every statement of it in this repo traced back to #1211.** Six probes, one full JWT:

    PUT    /tasks/{id}/labels, label_id the card ALREADY carries   400 {"code":8001,"message":
                                                                       "This label already exists
                                                                       on the task."}
    PUT    /tasks/{id}/labels, a SECOND row of the same key        201, card then wears
                                                                       ['reviewed', 'Reviewed']
    DELETE /tasks/{id}/labels/{id}, link PRESENT                   200 Successfully deleted.
    DELETE the same id again, link now gone                        403 {"message":"Forbidden"}
    DELETE a label that exists but was NEVER attached              403 {"message":"Forbidden"}
    DELETE a label_id that does not exist at all                   403 {"message":"Forbidden"}

Three things follow, and only the first was the point. #1211's 403 reproduces exactly, and 404 —
raised as a competing hypothesis for what an absent link answers — is not what this server says in
any of the three absent-link shapes. The 400 on a REPEATED id says that THIS endpoint refuses a
repeat of one id, which is why a snapshot the server filled carries each id at most once; it is the
belt and the de-duplication guard is the brace, and the first draft of this paragraph called it "the
load-bearing half", which over-reads one route into a claim about card state over all of them. And
the two-row card this whole card is about is constructible on a REAL server and not only on the
fake: the second row is ACCEPTED onto a card already wearing the first, which is independently what
VMCP-316 (1456) measured in the same week.

**A HAND MEASUREMENT DECAYS SILENTLY, so the three 403 rows are now a GATE and not only prose** —
`tests/integration/test_remove_label_absent.py`, which drives all three absent-link shapes plus the
200 happy-path control (a 403 proves nothing without a DELETE that does succeed: a caller that
cannot delete answers alike on every call, and `_read_task` records exactly that trap) and rebuilds
the two-row card on the server. It runs with a full JWT for that reason. Without it this card would
have left its own central measurement in precisely the condition it distrusted #1211's for.

**Containment, not tolerance: every match is attempted, the FIRST `VikunjaError` is remembered and
re-raised after the loop.** On a one-row card — every ordinary board — that is the old behaviour
unchanged, one DELETE with the same exception propagating, so no call site moved: FIVE
`_remove_label` calls in THREE methods (`_clear_verdict_labels` twice, `review_task`'s two
branches, `transfer_task`'s loop over four labels — the earlier "four call sites" here matched
neither convention, and its own parenthetical summed to five). On a two-row card a refusal on one
row can no longer shield the other's stale badge, which is the card's whole point. It is
deliberately NOT swallowed, and the reason is that a 403 on this endpoint is not always the benign
absent-link one: a task in a project the token cannot see answers 403 on a DELETE that would
otherwise have succeeded (`FakeAPI._read_task`'s probe table), so swallowing would fail OPEN and
silently, the exact mode #1256 is about. A token merely missing `tasks_labels: delete` answers 401
there rather than 403 — the same table records that the endpoint was never reached at all — and an
earlier draft of this paragraph put 403 in that sentence's headline while citing the 401 two
clauses later. Sniffing the body to swallow only a "benign" 403 is the shape #1216 rejected for
`add_label`'s 400, on the transferable half of its reasoning: a sniff swallows a genuinely
different error of the same status. (#1216's other reason, layering, was about putting the decision
in the REST client and does not transfer to a sniff written here.)

**The sweep's most useful row is the one that stays GREEN.** Restoring `next(...)` kills 5 of the
8 behavioural pins in the module (it collects 9; the ninth is the sweep record itself) against a
control of 0 failed / 0 errors / 145 collected — and leaves the one-row pin, the
no-op pin and the de-duplication pin green, because `next(...)` satisfies all three. A suite
holding only those three would have called this defect fixed. The full table is in
`tests/unit/test_workflow_remove_label_rows.py::test_the_sweep_is_recorded`, control first and
last, and it carries one honest 0 as well: iterating lazily instead of materialising the match
list measures 0 failed, because `FakeAPI.remove_label` rebinds `t["labels"]` rather than mutating
it in place and the production client never touches the caller's dict at all. That guard is
defence against a future in-place mutator and it is UNPINNED — written down so the next reader
does not delete it on the strength of a green round.

## Icebox — восьмая стадия, и почему колонка гейтит, а лейбл нет (#1640)

Человек завёл на досках `dogiators-front`/`dogiators-back` колонку `Icebox` — «беклог
беклога»: очень мелкое, лирика, вечное легаси, на которое не надо тратить токены и
упарываться в починку. В Backlog лежит то, что НАДО делать; в Icebox — то, чего делать
не планируется. Задача просила «завести это в пайплайн».

### Решение первое: колонка — гейт, лейбл — подсказка

Гейт достался **даром**, и это стоит сказать явно, потому что соблазн написать его руками
велик. `next_task` читает только `NEXT_TASK_STAGES` (Queue/Design/Build/Review), `claim`
работает только из Queue, `AGENT_ADVANCE` знает лишь `Design→Build→Review`. Карточка в
колонке `Icebox` невидима для пампа без единой новой строчки.

Лейбл `icebox` **сознательно НЕ добавлен** в фильтр `offerable_queue`, где стоят
`LABEL_BLOCKED` и `LABEL_EPIC`. Две причины, и первая — измеренное свойство кода, а не
вкус. **Тот фильтр роняет карточку МОЛЧА**: двумя строками ниже `withheld` собирается
исключительно из `excluded`, поэтому у карточки, отсеянной по лейблу, нет никакого следа
в выдаче. Это ровно та ловушка, которую этот репозиторий уже знает по `blocked` — памп
тихо никогда не дренит карточку, и по ответу инструмента этого не видно. Третий лейбл
там тиражировал бы немоту.

Вторая причина — про человека. Карточка попадает в Queue из Icebox только руками, и это
ЯВНОЕ решение «да, вот это теперь делаем». Гейт на лейбле отменял бы его молча, причём
тем вернее, чем аккуратнее человек пометил карточку.

Что лейбл делает вместо гейта: едет в payload как `ICEBOX_HINT` — «сделай минимально
достаточное, не рефактори, не золоти». Именно это закрывает «не тратить токены»: бюджет
усилия для карточки, которую ВЗЯЛИ, а не невидимость, от которой очередь голодает.
`_summary` (то есть `next_task`/`claim`) и `get_task` отдают ключ `icebox` только когда
лейбл есть, так что форма ответа для обычной карточки не сдвинулась.

Пин на это решение — `test_a_queue_card_carrying_the_icebox_label_is_still_offered`.
Он негативный, и проверять его надо по домашнему правилу: дописать
`and not self._has_label(t, LABEL_ICEBOX)` в `offerable_queue` и потребовать, чтобы тест
покраснел.

### Решение второе: колонка ОПЦИОНАЛЬНА, и это про радиус поражения

`_bucket` проверяет наличие колонок по `REQUIRED_STAGES`, а не по `STAGES`. Причина не в
аккуратности, а в канале раскатки: `stable` — движущаяся ветка, которую `--refresh-package`
перерезолвит на старте КАЖДОЙ сессии у КАЖДОГО потребителя. Релиз с восьмой стадией
приезжает на доски `birdman`, `hgdev-acp` и самого `vikunja-mcp` раньше, чем человек
успеет прогнать там `vikunja-mcp setup`. Будь проверка по `STAGES` — первый же вызов
ЛЮБОГО инструмента на такой доске отвечал бы «run `vikunja-mcp setup`»: весь флот стоит,
пока каждую доску не мигрируют руками.

Вторая половина того же решения — `_bucket` на отсутствующей опциональной колонке.
Раньше строка была `return self._buckets_cache[title]`, то есть **`KeyError`**. Это не
отказ, а падение: `server._tool` конвертирует `WorkflowError/ConfigError/VikunjaError/
httpx.HTTPError` и больше ничего, так что `KeyError` ушёл бы мимо декоратора и уронил
stdio-сервер посреди сессии. Теперь там явный `WorkflowError`, и он достижим ТОЛЬКО для
опциональной стадии — все обязательные проверены пятью строками выше.

### `file_task(icebox=True)` — единственный агентский вход

`return_task(to='icebox')` человек НЕ заказывал, и это осознанно: классификация «этого
никогда не сделают» — человеческое решение, как и Done. Агент может только завести
находку сразу замороженной.

Три свойства двери:

- **Не композится с `queue=True`** — противоположные инструкции («человек велел делать
  сейчас» против «делать не планируется»). Отказ до создания карточки.
- **Разрешён кросс-проектно, в отличие от `queue`.** Асимметрия и есть смысл: чужая Queue
  инжектит работу, которую тамошний человек не санкционировал, и будит их флот; чужой
  Icebox не будит никого и ничего у них не занимает.
- **Fail-fast с обеих сторон.** Кросс резолвит колонку через `_target_bucket` до
  `create_task` — так было и раньше. Свой проект резолвил бы её в `_move` уже ПОСЛЕ
  создания, поэтому там добавлен явный `self._bucket("Icebox")` перед `create_task`:
  без него отказ на не мигрированной доске оставлял бы карточку сиротой в дефолт-бакете.
  Фоллбэка в Backlog нет намеренно: молча положить не туда, о чём попросили, и отчитаться
  успехом — хуже отказа, потому что никто не узнает, что доска отстала.

`_target_backlog` при этом переименован в `_target_bucket(project_id, stage="Backlog")`:
старое имя перестало быть правдой в тот момент, когда функция научилась резолвить вторую
колонку. Дефолт сохраняет поведение `handoff`/`transfer_task` байт-в-байт.

Провенанс — две НОВЫЕ базы в `cardtext` (`filed_icebox`, `filed_cross_icebox`), а не
суффикс к существующим. Причина текстовая и жёсткая: `filed_backlog` обещает «for human
triage», и весь смысл Backlog в том, что решение человеком ЕЩЁ НЕ принято, а весь смысл
Icebox — в том, что оно УЖЕ принято. Приписать замороженной карточке обещание триажа
значило бы сказать читателю неправду о состоянии доски.

### Замороженный предшественник — цена того, что Icebox не в `READY_STAGES`

`READY_STAGES` остались `{Review, Done}`, и это значит, что предшественник в Icebox
блокирует преемника НАВСЕГДА. Альтернатива — считать Icebox «готовым» — молча освобождала
бы преемника, а «этого никогда не сделают» не то же самое, что «это сделано».

Но у консервативного выбора есть острый край, и его надо было закрыть отдельно: обычный
хвост совета — «A predecessor becomes ready only at Review or Done; finish that one first»
— здесь называет ЕДИНСТВЕННОЕ действие, которое никто выполнить не может. Ни один
агентский инструмент не двигает карточку из Icebox.

Поэтому такой блокер помечается `finishable: False` (ключ из #1190), из-за чего общий
хвост отбрасывается, и печатается отдельная клауза `_predecessor_frozen`.
**Отдельная, а не `escape` из #1190** — и это не вкусовщина: escape-клаузы печатаются под
лидом «At least one of those stages could NOT be established», а стадия замороженного
предшественника установлена прекрасно. Переиспользование сделало бы отказ лживым. Две
клаузы композятся: карточка может ждать одного блокера каждого вида.

Клауза печатается в ТРЁХ местах, и важнее всего третье: отказ `claim` видит только агент,
который спросил карточку по id, а обычный `/loop`-дренаж гейтнутую карточку не клеймит —
он её ПРОПУСКАЕТ. Без клаузы в `_starving_tail` цепочка, замороженная за замороженной головой,
была бы очередью, которая просто тихо не движется.

### `return_task` и `decompose` из Icebox НЕ закрыты, и это измеренная асимметрия с Done

Контракт-гейт SKILL.md (`test_the_rulebook_names_BOTH_stages_*`) выводит список открытых стадий
из `workflow.STAGES`, поэтому восьмая стадия сразу заставила ответить: закрывать ли из неё
`return_task` и `decompose`, как #626 и #649 закрыли их из Done. Ответ — НЕТ, и причина не в
экономии.

`decompose` из Icebox создал бы детей в Queue, то есть превратил бы замороженную работу в
клеймабельную мимо человека. Это ровно тот класс, который репозиторий уже закрывал дважды. Но
обе двери требуют `_require_mine`, а карточка в Icebox по определению без ассайни — значит
дотянуться до них можно только если человек РУКАМИ назначил агента на замороженную карточку.

Вот здесь и лежит асимметрия. #626 понадобился именно потому, что **обычный жизненный цикл
паркует в Done НАЗНАЧЕННУЮ карточку** — человек, двигающий карточку в Done, не снимает ассайни,
поэтому `_require_mine` проходит ровно на той карточке, которую трогать нельзя, и владение не
могло подменить стадийный гейт. В Icebox обычный поток назначенных карточек не создаёт вовсе.
Опасное состояние там требует отдельного человеческого действия, которое само по себе значит
противоположное: человек, назначивший агента на замороженную карточку, говорит «эту всё-таки
сделай».

Поэтому Icebox стоит в ОТКРЫТЫХ списках обоих буллетов, а не в закрытых, и оба пина проверяют
это против кода — цикл «эти стадии действительно открыты» получил шестым элементом `Icebox`.
Побочный эффект решения: формулировка `_OWNERLESS_EXITS["Icebox"]` пришлось сузить. Первый
черновик писал «only a human moves a card out of Icebox» — плоское утверждение, которое ЛОЖНО
ровно на том случае, что разбирается выше. Живой текст говорит «while it has no owner», и это
правда в той ветке, где он печатается.

### Потолок CLAUDE.md пришлось поднять, 40 000 -> 40 500

Файл стоял на 39 876 при потолке 40 000 — 124 символа запаса, а правило заняло 461. Подъём
записан в `tests/unit/test_rulebook_size.py` с причиной, и вместе с ним пересчитана ОБЕ
половины пары «символы/токены» (3.15x/3.06x -> 3.11x/3.02x): токенная половина — арифметика по
тем же заякоренным per-character rates, а не новый прогон токенизатора, потому что скрипт
текста не менялся, менялся только потолок. Запас после посадки — 163 символа, то есть МЕНЬШЕ
прежнего; следующей карточке, которой понадобится строка в CLAUDE.md, честнее ужать сам буллет
`workflow.py` (он теперь самый длинный в разделе), чем двигать число ещё раз.

### Что НЕ делалось

- `setup_cmd.py` не правился вовсе: колонка приезжает из `STAGES`, позиция — из
  `enumerate(STAGES)`, от сноса лишних пустых бакетов защищена тем, что стала
  канонической. Записи в `RENAMES` не нужно: на досках dogiators колонка называется
  буква-в-букву `Icebox` (подтверждено человеком; токен этого репозитория те доски не
  видит — он скоупед на project 10, так что проверить самостоятельно было нечем).
- Позиция — самая правая, ПОСЛЕ `Done`. Выбор человека из трёх предложенных. Семантика
  при этом слегка кривая (из Icebox карточка МОЖЕТ вернуться в Backlog, из Done — нет), и
  это принято сознательно в обмен на то, что мёртвая колонка не встречает человека первой
  каждый раз.
