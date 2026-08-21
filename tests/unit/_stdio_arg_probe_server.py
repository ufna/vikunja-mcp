"""Probe server for test_advance_report_arguments.py and test_sibling_target_argument.py — NOT
a test module (the leading underscore keeps pytest from collecting it); it is spawned as a
SUBPROCESS.

It builds the REAL MCPServer with the REAL tool roster — no number here on purpose, it moved
from twelve to fourteen while this line still said twelve; the count is asserted in
test_server.py, where it is checked rather than remembered — and runs it over the REAL stdio
transport, replacing only the Workflow behind them with a stub that reports the length of
the argument that ARRIVED.

That is the whole point — stated the way the sibling test file had to restate its own version
of it, because "every other unit test in this repo drives Workflow through FakeAPI and never
crosses the MCP boundary" is what this line said first and is FALSE: plenty of files under
tests/unit never mention FakeAPI at all (test_api.py, test_config.py, test_line_length_gate.py,
test_repo_browser_isolation.py and more), and test_server.py builds the real MCPServer to read
`list_tools()`. Nor is "through FakeAPI" the fix — that was this docstring's SECOND try and it
is false too: test_workspace_cmd.py's `_workflow_on` drives the real Workflow through the real
api.py over an httpx MockTransport, and tests/integration drives it against a container. The
true statement is not about the CLIENT under Workflow at all but about the MCP boundary ABOVE
it: every one of those harnesses enters Workflow directly, so before this file nothing
round-tripped a tool ARGUMENT across the wire — measured at 2ae1aa27^, `list_tools()` was
read for tool NAMES and a count, never for `required` or an input schema, and no test spun up a
stdio client. So nothing else here could tell "the agent passed no worklog" from "the worklog
was lost in serialisation" — exactly the ambiguity tracker #657 was filed about.
"""
import sys

from vikunja_mcp import server as srv


class _ReportingWorkflow:
    """Answers with what reached the tool body instead of talking to Vikunja."""

    def advance(self, task_id, to, spec=None, worklog=None, evidence=None, root_cause=None):
        return {
            "task_id": task_id,
            "to": to,
            # -1 marks "arrived as None" so it is distinguishable from an empty string.
            "worklog_len": -1 if worklog is None else len(worklog),
            "spec_len": -1 if spec is None else len(spec),
            "evidence_len": -1 if evidence is None else len(evidence),
            "root_cause_len": -1 if root_cause is None else len(root_cause),
            "worklog_head": (worklog or "")[:24],
            "worklog_tail": (worklog or "")[-24:],
        }

    # #1200 drives these two from test_sibling_target_argument.py. They echo the TYPE as well as
    # the value because that is the whole question there: `to` crosses the boundary through a
    # pydantic model, and "17 arrived" and "17 arrived AS AN INT" are different facts.
    def handoff(self, task_id, to, title, description="", priority=0):
        return {"task_id": task_id, "to": to, "to_type": type(to).__name__, "title": title}

    def transfer_task(self, task_id, to, reason):
        return {"task_id": task_id, "to": to, "to_type": type(to).__name__, "reason": reason}

    def file_task(self, title, description="", priority=0, related_task_id=None,
                  project_id=None, queue=False):
        # The CONTROL for the two above: the cross-project door that already existed.
        return {"project_id": project_id, "project_id_type": type(project_id).__name__}

    def review_task(self, task_id, verdict, report):
        return {
            "task_id": task_id,
            "verdict": verdict,
            "report_len": len(report),
            "report_head": report[:24],
            "report_tail": report[-24:],
        }


def main() -> None:
    srv._wf = lambda: _ReportingWorkflow()
    srv._server().run()


if __name__ == "__main__":
    sys.exit(main())
