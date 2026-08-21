"""`handoff`/`transfer_task` refused the very shape their docstrings advertised (#1200).

THE DEFECT, and why it could only be seen HERE. `server.py` declared `to: str`, so the MCP
SDK built a pydantic model with a string field and `handoff(to=17)` died at the boundary with
`Input should be a valid string` — before `_resolve_sibling`'s careful refusals ran, before
the tool body existed. `Workflow.handoff` has taken `str | int` all along, and the suite agreed
the int worked because nothing in it validates: test_done_is_human_only.py passes `handoff` a
bare `{"to": 999}` and is green, calling `server.handoff(...)` as an ordinary Python function —
an annotation validates nothing there, since pydantic runs inside the SDK's tool wrapper and a
direct call never reaches it. Everything else enters `Workflow` itself, one layer further down
still. So the int worked everywhere except the one place an agent stands. The generated schema
said `{"type": "string"}` while the description said "or a bare project id", and the ids an
agent holds arrive as JSON NUMBERS: `siblings` rides in every `next_task` payload as
`{"backend": 17}`.

MEASURED at the real boundary (real MCPServer, real stdio transport, the echo Workflow in
_stdio_arg_probe_server.py), before and after, with `file_task`'s already-existing
cross-project door as the CONTROL in the same run:

    before: handoff(to=17) -> is_error, string_type | to="17" -> str | to=True -> is_error
            schema: {"type": "string"}
            control file_task(project_id=17) -> int 17 | project_id="17" -> int 17
    after:  handoff(to=17) -> int 17 | to="17" -> str "17" | to="backend" -> str
            schema: {"anyOf": [{"type": "string"}, {"type": "integer"}]}
            control unchanged

The control is what settles the DECISION rather than the diff: `file_task` accepts both a
number and a quoted number today, so widening `to` makes all three cross-project doors take an id
the same way, where narrowing the docstrings to "quote it" would have made this pair the odd one
out. Their SCHEMAS still differ — `file_task.project_id` advertises integer-only and refuses a
sibling NAME — so this is a claim about behaviour, not about shape.

MUTATION SWEEP — see the docstring of test_the_wire_schema_and_the_docstring_promise_agree.
"""
import asyncio

import pytest

from vikunja_mcp import server

# Deliberate reuse of the sibling file's driver rather than a second copy: it spawns the same
# probe server over the same real stdio transport, and the coupling is LOUD — a rename there is
# an ImportError here at collection, never a quietly-skipped check.
from tests.unit.test_advance_report_arguments import _drive_probe_server

_CROSS_PROJECT_TOOLS = ("handoff", "transfer_task")


def _tools():
    return {t.name: t for t in asyncio.run(server.mcp.list_tools())}


def _accepted_types(schema: dict) -> set[str]:
    """The JSON types a property admits, reading `anyOf` and the bare `type` alike — an agent
    reads the schema, not our annotation, and those two spellings are the same fact to it."""
    if "anyOf" in schema:
        return {member["type"] for member in schema["anyOf"] if "type" in member}
    return {schema["type"]} if "type" in schema else set()


@pytest.mark.parametrize("tool", _CROSS_PROJECT_TOOLS)
def test_the_wire_schema_and_the_docstring_promise_agree(tool):
    """The card's actual requirement: the two must not disagree, whichever way they are fixed.

    Both halves are asserted together on purpose. Narrowing the annotation back to `str` reddens
    this while the promise still stands; deleting the promise from the description reddens it
    while the schema still admits an integer. A future author who genuinely wants "quote the id"
    has to change BOTH and will see this test asking.

    MUTATION SWEEP, run in a CLONE of this tree with `__pycache__` deleted and
    PYTHONDONTWRITEBYTECODE=1 per round, `vikunja_mcp.__file__` printed and confirmed to resolve
    inside the clone every round. Selection: this file plus tests/unit/test_server.py, 60
    collected in the control and in every round; rounds read by COUNTING lines that begin
    `FAILED `, with `ERROR ` counted separately and 0 throughout.

      control 0 failed   round: `to: str` back on both server sites        -> 4 failed
      control 0 failed   round: `to: str` on handoff only                  -> 3 failed
      control 0 failed   round: drop "or a bare project id" from handoff's
                                docstring (annotation left widened)        -> 1 failed
      control 0 failed   round: `to: int` on both (number only, no names)  -> 3 failed
      control after restore 0 failed

    The docstring round is the one worth reading twice: it is the only round in which the WIRE
    still does everything the card asked for, and it dies anyway — which is the point, since the
    defect being closed was the DISAGREEMENT and not either half alone. Round 1's first attempt
    measured NOTHING and is recorded because the failure is invisible: the two selection paths
    were passed through one unquoted zsh variable, zsh does not word-split, pytest got them as a
    single argument and printed `collected 0 items` with 0 failures — which reads exactly like a
    clean round.
    """
    schema = _tools()[tool].input_schema["properties"]["to"]
    description = _tools()[tool].description or ""
    assert "project id" in description, description
    assert _accepted_types(schema) == {"string", "integer"}, schema


def test_a_bare_project_id_reaches_the_tool_body_as_an_int_over_real_stdio():
    """THE round-trip the card is about. `17` used to be refused at the boundary; a name and a
    quoted id must keep arriving exactly as they did, so all three are in one process."""
    results = _drive_probe_server([
        ("handoff", {"task_id": 1200, "to": 17, "title": "add the endpoint"}),
        ("handoff", {"task_id": 1200, "to": "17", "title": "add the endpoint"}),
        ("handoff", {"task_id": 1200, "to": "backend", "title": "add the endpoint"}),
        ("transfer_task", {"task_id": 1200, "to": 17, "reason": "wrong board"}),
        ("transfer_task", {"task_id": 1200, "to": "backend", "reason": "wrong board"}),
    ])
    assert [err for err, _ in results] == [False] * 5, results
    payloads = [payload for _, payload in results]
    assert [(p["to"], p["to_type"]) for p in payloads] == [
        (17, "int"), ("17", "str"), ("backend", "str"), (17, "int"), ("backend", "str"),
    ], payloads


def test_a_float_target_is_still_refused_at_the_boundary():
    """The union is `str | int`, not "anything": a float belongs to neither member, so it is
    refused BEFORE the tool body — which is where a wrong-typed target should die."""
    (is_error, payload), = _drive_probe_server([
        ("handoff", {"task_id": 1200, "to": 1.5, "title": "x"}),
    ])
    assert is_error, payload
    assert "valid string" in str(payload) or "valid integer" in str(payload), payload


def test_a_json_boolean_becomes_project_1_on_BOTH_cross_project_doors():
    """A CHARACTERISATION, not an endorsement — and the reason it asserts SAMENESS.

    Lax pydantic renders `true` as the integer 1, so `_resolve_sibling`'s explicit bool guard
    (`isinstance(to, bool)` -> refusal) is unreachable from the wire: the tool body is handed a
    plain 1 and cannot tell. That is not a hole this card opened: `file_task(project_id=true)`
    arrives as project 1 in the same run, on a door annotated `int | None` since `f0e7aef` —
    nothing is claimed about the years in between, since the boundary machinery was swapped at
    `0543463`. Closing it while KEEPING the int shape means strict pydantic types at server.py
    MODULE scope — a NEW import on the path #521 cleared (measured: importing that module today
    loads no pydantic at all) — so it is filed as VMCP-307 (1207) rather than smuggled in here.

    Asserting that the two doors agree is what makes this useful: fix one and this goes red,
    asking whether the other was meant to stay behind."""
    results = _drive_probe_server([
        ("handoff", {"task_id": 1200, "to": True, "title": "x"}),
        ("file_task", {"title": "x", "project_id": True}),
    ])
    assert [err for err, _ in results] == [False, False], results
    handoff_payload, file_task_payload = (payload for _, payload in results)
    assert (handoff_payload["to"], handoff_payload["to_type"]) == (1, "int"), handoff_payload
    assert (
        file_task_payload["project_id"], file_task_payload["project_id_type"]
    ) == (1, "int"), file_task_payload
