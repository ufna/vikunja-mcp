# Push-dispatch of independent bug review + verdict labels

Date: 2026-07-08
Status: approved (brainstorming) → ready for implementation plan

## Problem

The "independent bug review" layer only fires when a *second identity* pulls
the bug: `next_task` offers a bug in Review to a free agent, but the offer is
gated on `my_id not in assignees` (`workflow.py:117`). In the real deployment
there is **one orchestrator on one scoped token** — and it is always the
assignee of the bug it just fixed (`claim` assigns `me()`; `advance` never
unassigns). All sub-agents it spawns share that token → same `me()` → same
identity. So `next_task` never surfaces the orchestrator its own bug, and no
other identity exists to pull it. Result: in a solo setup bug fixes reach the
human with **no independent agent review at all** — the `bug` label's promised
second pair of eyes never materialises.

We fix this with a **push** model (the author-orchestrator dispatches a fresh
review sub-agent itself, running it in parallel with the next task), and we make
review verdicts visible on the board as **labels**.

## Goals

- Independent bug review actually runs in a solo/single-token setup.
- Review runs **in parallel** with work on the next task (non-blocking).
- Review verdict is visible on the board as a label with a defined lifecycle.
- Push is reliable, not just documented — a code-level nudge at the exact moment.

## Non-goals

- No second Vikunja identity/token (explicitly not wanted).
- Do **not** remove the pull path in `next_task` — it stays as a dormant
  fallback for a future multi-identity world (its `:117` gate is simply silent
  in solo, so there is no double-review).
- Human stays the only actor who moves a task to Done (unchanged).
- Labels do **not** replace the comment-timestamp freshness logic in the pull
  path (`verdict vs worklog`); they are additive status markers.

## Design

### 1. Verdict labels and their lifecycle — `workflow.py`

Two new constants, lowercase to match `blocked`/`epic`/`bug`, kept mutually
exclusive:

```python
LABEL_REVIEWED      = "reviewed"       # passed independent agent review
LABEL_REVIEW_FAILED = "review-failed"  # bounced back, currently reworking
```

Label helpers on `Workflow` (resolve title→id from the board snapshot, no extra
round-trip; add via the existing `get_or_create_label` path):

```python
def _add_label(self, task_id, title):
    label = self.api.get_or_create_label(title)
    self.api.add_label(task_id, label["id"])

def _remove_label(self, task, title):
    lb = next((l for l in task.get("labels") or [] if l.get("title") == title), None)
    if lb:
        self.api.remove_label(task["id"], lb["id"])
```

`_remove_label` is gated on the snapshot actually carrying the label, so we
never issue a `DELETE` for a non-existent association (avoids a 404).

Transitions:

| Trigger | Label effect |
|---|---|
| `review_task(approve)` | `+reviewed`, `−review-failed` (+ existing `[review] APPROVE` comment; task stays in Review for the human) |
| `review_task(needs_work)` | `+review-failed`, `−reviewed` (+ comment, move to Build — unchanged) |
| `advance(to='review')` | `−review-failed` (resubmit reset; on the first submit the tag is absent → no-op) |

Because `advance(to='review')` strips `review-failed`, by the time a re-reviewed
bug is approved the tag is already gone — so the `−review-failed` inside
`approve` is almost always a no-op, kept only as belt-and-suspenders for mutual
exclusivity.

### 2. `remove_label` — `api.py` + `tests/unit/fakes.py`

New client method (mirrors Vikunja's endpoint; verify shape in integration):

```python
# api.py
def remove_label(self, task_id: int, label_id: int) -> None:
    self._req("DELETE", f"/tasks/{task_id}/labels/{label_id}")
```

1:1 mirror in `FakeAPI`, idempotent (filter by id — absent id is a no-op):

```python
# fakes.py
def remove_label(self, task_id, label_id):
    t = self.tasks[task_id]
    t["labels"] = [l for l in t["labels"] if l["id"] != label_id]
```

### 3. `advance()` push nudge — `workflow.py`

In the `to == 'review'` branch: the reset (`_remove_label`) runs for **every**
review-advance (harmless no-op when the tag is absent); the nudge is added to
the return payload **only for `bug` tasks**, in the style of existing
`next`/`note` hints, so the orchestrator is prompted to push a reviewer at
exactly the right moment. The nudge fires on every submit and resubmit:

```python
# ... inside the `to == 'review'` branch, after the [worklog] comment:
self._remove_label(task, LABEL_REVIEW_FAILED)   # resubmit reset (step 1), unconditional
self._move(task_id, to_stage)
result = {"moved_to": to_stage, "task_id": task_id}
if to == "review" and self._has_label(task, LABEL_BUG):
    result["review_needed"] = True
    result["note"] = (
        "это баг — сразу задиспатчь свежий review-саб-агент в фоне "
        "(он вынесет review_task), и параллельно бери следующую задачу"
    )
return result
```

### 4. Push dispatch + parallelism — `SKILL.md` (orchestrator discipline)

Rewrite the "Независимое ревью багфиксов" section from pull to push:

- After `advance(to='review')` on a bug, the orchestrator immediately dispatches
  a **fresh** review sub-agent **in the background**. Brief: read the dossier
  (`get_task`), reproduce the bug, verify the fix hits the *root cause* from the
  report, run verification **by execution**, then `review_task(verdict, report)`.
- Reviewer ≠ implementer — distinct sub-agents, unmixed contexts.
- **Parallel:** do not wait. Having dispatched the review in the background, go
  straight to `next_task`/`claim` for the next task.
- needs_work round-trip: the reworked bug re-enters Review via `advance` → push
  a fresh reviewer again.

Amend "Дисциплина очереди": a **background review in flight does not count as
your active task** (carve-out to "one task at a time").

Note in the section that in a multi-identity setup a free *other* agent may also
pick the bug up via `next_task` (the dormant pull path); in solo, push is the
mechanism.

### 5. Docs — `CLAUDE.md` (minor)

Extend the `workflow.py` bullet to mention the two verdict labels alongside the
comment markers, so the architecture note stays accurate.

## Testing (TDD, one test per gate) — `tests/unit/test_workflow_gates.py`

- `approve` adds `reviewed` and strips `review-failed`.
- `needs_work` adds `review-failed` and strips `reviewed`.
- `advance(to='review')` on a resubmit (after needs_work) strips `review-failed`
  — extend the existing `test_review_reoffered_after_needs_work_rework` cycle.
- `advance(to='review')` on the first submit: no `review-failed` present, no
  crash (idempotent no-op), label not added.
- `advance(to='review')` on a `bug` task returns `review_needed=True` + note; a
  non-bug task does not.
- `FakeAPI.remove_label` is idempotent and mirrors the client (direct fake test
  or via the gate tests).

Integration (`tests/integration`, skipped without a container): `remove_label`
round-trips against real Vikunja `DELETE /tasks/{id}/labels/{label_id}` and the
board reflects the removal.

## Files touched

- `src/vikunja_mcp/workflow.py` — constants, `_add_label`/`_remove_label`,
  `review_task` label transitions, `advance` resubmit-reset + push nudge.
- `src/vikunja_mcp/api.py` — `remove_label`.
- `tests/unit/fakes.py` — `remove_label` mirror.
- `src/vikunja_mcp/skills/tracker/SKILL.md` — pull→push rewrite, queue carve-out.
- `tests/unit/test_workflow_gates.py` — gate tests above.
- `CLAUDE.md` — mention verdict labels.

## Risks / edge cases

- **Idempotent remove:** we gate `DELETE` on the snapshot carrying the label, so
  we only remove existing associations. A concurrently-removed label could still
  404 (rare); acceptable, not handled.
- **Race, background review vs `next_task`:** the reviewer operates on the bug;
  `next_task`/`claim` operate on a different task; the board is re-fetched per
  call → safe. A `needs_work` verdict moves the bug back to Build, where it
  reappears as the orchestrator's `mine` on the next `next_task` (correct).
- **No double-review in solo:** only a second identity could also pull; none
  exists. The `:117` gate stays silent.
- **Snapshot staleness for label ids:** `_remove_label` reads the id from the
  board snapshot; low risk given per-call re-fetch.
