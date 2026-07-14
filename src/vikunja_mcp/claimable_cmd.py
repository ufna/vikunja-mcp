"""`vikunja-mcp claimable` — the sibling-EXPORTED "is there claimable work for THIS
token, right now?" verdict.

CROSS-REPO CONTRACT (consumed by hgdev-acp's repo-agent-loop pre-launch idle check).
One JSON line on stdout, and nothing else:
  {"claimable": bool, "kind": "...", "task_id": int|null}   exit 0  (the check RAN)
  {"error": "<ExceptionClass>: <message>"}                  exit 1  (the check FAILED)
kind: queue|resume|stuck_claim|review (claimable) / empty|starving|cycle (not).
The key set and the exit-code split are public API — renaming a key or repurposing a
code breaks the hub's check. It breaks CLOSED (a failed check is exit 1, so the hub's
loops go red rather than silently idling), but it breaks.

WHY THIS EXISTS. The hub used to GUESS claimability its own way, from kanban BUCKET
PRESENCE. On 2026-07-14 this project's own board proved that wrong and expensive:
Queue/Design/Build were empty while Review held 25 tasks ALL assigned to the agent
(done work awaiting a HUMAN's Done move). Bucket presence read "work!" forever, yet
next_task correctly offers nothing — you never independently review your own work
(workflow.py, the Review pull path). Result: ~144 no-op agent boots/day ≈ $105/day
for zero work. So the hub stops guessing and asks the gates themselves: this runs the
SAME Workflow.next_task() the agent runs, so the exported verdict has ZERO drift from
the agent's own by construction — one implementation of the rules, not two.

next_task is READ-ONLY (verified call inventory: me/kanban_view/view_tasks/get_task/
comments — all GETs; pinned by test_claimable_cmd.test_the_check_makes_no_writes), so
the hub may poll this at any cadence without mutating the tracker.

Config comes from the standard 4 layers; the hub supplies layer 1 (VIKUNJA_URL/
VIKUNJA_TOKEN/VIKUNJA_PROJECT_ID in this process's own env). stdout carries the one
JSON line and NOTHING else — uv/python/anything-else noise belongs on stderr.
"""
import json

from vikunja_mcp.api import VikunjaAPI
from vikunja_mcp.config import load_config
from vikunja_mcp.workflow import Workflow


def classify_next(result: dict) -> dict:
    """Map a next_task() result onto the flat verdict.

    Claimable == a task is offered at all; `kind` names WHICH of next_task's branches
    offered it, so the hub can log/branch without re-deriving the rules. The flag
    precedence mirrors next_task's own result shapes: a review offer carries review:true,
    a resume carries resume:true + stage (stage 'Queue' means a STUCK claim — assigned
    but never moved — not an active task), the free queue carries resume:false + task.
    task:null is the not-claimable family, where the additive discriminators cycle/
    starving distinguish a wedged board from a genuinely empty queue (all three are
    'don't launch an agent', but they are NOT the same thing to a human)."""
    task = result.get("task")
    if task is not None:
        if result.get("review"):
            kind = "review"
        elif result.get("resume"):
            kind = "stuck_claim" if result.get("stage") == "Queue" else "resume"
        else:
            kind = "queue"
        return {"claimable": True, "kind": kind, "task_id": task["id"]}
    if result.get("cycle"):
        kind = "cycle"
    elif result.get("starving"):
        kind = "starving"
    else:
        kind = "empty"
    return {"claimable": False, "kind": kind, "task_id": None}


def run_claimable() -> int:
    """The CLI body: print the verdict line, return the exit code."""
    try:
        cfg = load_config()
        wf = Workflow(
            VikunjaAPI(cfg.url, cfg.token), cfg.project_id,
            enforce_single_wip=cfg.enforce_single_wip,
        )
        verdict = classify_next(wf.next_task())
    except Exception as e:  # noqa: BLE001 — a CLI check: ANY failure is exit 1, never a crash
        # A FAILED check must never be mistaken for a clean "no work" (that would idle the
        # hub's loop on a broken tracker forever), so it takes the OTHER exit code and its
        # own key. Token-free by the same discipline as the server's errors: ConfigError
        # names files and env VAR NAMES, VikunjaError carries the server's body, httpx errors
        # carry the URL — never the Authorization header. The hub logs this line verbatim.
        print(json.dumps({"error": f"{e.__class__.__name__}: {e}"}))
        return 1
    print(json.dumps(verdict))
    return 0
