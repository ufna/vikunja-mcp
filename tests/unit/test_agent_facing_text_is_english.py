"""THE TEXT THIS PACKAGE HANDS BACK TO AN AGENT IS ENGLISH — the string populations the
`language` key deliberately does not reach (tracker #1166, widened to the whole package and to
the shipped tool descriptions by #1170).

THREE POPULATIONS, AND THIS FILE OWNS THE THIRD — this file's triple, which is NOT the one
`docs/dossier/config.md` draws (its third row is the wire format, the markers), so do not read the
two as the same table. `cardtext.py` holds what the tool writes onto a CARD, keyed by language,
and `tests/unit/test_card_text_is_ascii.py` plus `tests/unit/test_card_language.py` pin it. An
agent's own `spec`/`worklog`/`question` is unconstrained by design. What is left is what a TOOL
CALL returns to its caller — a
`WorkflowError` message, and the `message`/`note` keys of a `next_task` payload. #1165 put that
population OUT of the table on purpose, and `cardtext.py`'s own docstring says so in the bullet
naming `WorkflowError` text and the `note`/`message` strings in tool payloads: their audience is
the AGENT, they are effectively prompt content, and they land in logs. So it is ONE language for
every consumer whatever their `language` key says, and that language is the one this repo's
README, CLAUDE.md and SKILL.md are written in.

AND A FOURTH SURFACE, WHICH IS NOT A POPULATION OF THE SAME KIND — a TOOL's docstring. The MCP
SDK ships it to the agent as that tool's description, so it is agent-facing text that never
passes through a return value at all: it is read before the first call and on every listing.
#1170 brought it here because the rule is the same one (ONE language for every consumer) reaching
a surface none of the three rows describes, and because the two descriptions that carried Russian
carried it as EXAMPLES of values this tool deliberately leaves free-form — a defensible reading
right up until a consumer whose team writes English opens their tool list. The examples are
English now and what they were illustrating is stated in words, which is the fix that survives
either reading.

WHY THIS FILE EXISTS. #1164 translated the card text and left this population alone, on a rule
that reads "Leave it English; do not fold it into any later localization" — an INSTRUCTION, which
this file now enforces. What went wrong is the sentence that grew beside it: that card's own build
note and worklog paraphrase the rule as "leave it English (it already is)", and the parenthetical
was untrue. Two strings said otherwise and shipped: `_cycle_signal`'s five-line
`message`, sitting in the same returned dict as a fully English `note` — one payload, one
field in each language — and `claim`'s epic-container fallback, which rendered `его
подзадачами` at the end of an otherwise English sentence whenever the epic had no subtasks to
name. Neither is card text, so neither ASCII gate could see them — and what the rest of the suite
held was worse than nothing on one of the two: `test_workflow_sequence_gate.py` asserted
`"цикл" in res["message"].lower()`, so TRANSLATING that message was the red test, and two further
pins over the same message read its interpolated values through contiguous literals, one of them
spelling `задач(и)`. The fallback is the mirror case, and TWO tests drove it rather than one:
`test_claim_refuses_childless_epic_gracefully` and `test_claim_refuses_epic_container`, both in
`test_workflow_epic_skip.py`, since the latter's epic has no subtasks either. Both match on the
word "container" and neither looks at the tail of the sentence — measured by this card's
independent second pass, which restored the Russian in `claim` and got 13 collected from that
file, control 0 failed and that mutation 0 failed, i.e. a round in which nothing moved at all.
That is the shape this file is against — a green suite that renders the string and does not read
it.

THE UNIT IS CYRILLIC, NOT ASCII, AND THAT IS MEASURED RATHER THAN CHOSEN. The card-text gates next
door assert ASCII, which is right for them: a marker is a wire format and a card body is prose
this repo keeps typographically plain. This population is different — it is English prose full of
em dashes and arrows, so an ASCII pin over it is red on arrival. The test below named for that
asserts a FLOOR rather than the exact count — the closest to a property this gets, since the exact
number moves with every refusal anyone adds, while "there are still plenty" does not. What the
Cyrillic unit costs is stated where it is felt: a lookalike inside a Latin word (`а` for `a`) is
caught, a Greek or Hebrew string would not be, and
neither would an English sentence translated into a language written in Latin script. The defect
it is pinned against is the one that happened, twice, in a repo whose other language is Russian.

WHAT THE STATIC SCAN CAN AND CANNOT SEE, AND WHAT #1170 CHANGED ABOUT IT. It reads every string
literal in `src/vikunja_mcp` that is not a docstring, minus `cardtext._TABLE`'s `ru` column —
broader than the call sites that actually return text, and deliberately so: an agent-facing
string is not marked as one, so the cheap complete rule is "this package's own literals are
English", and the per-language prose lives in `cardtext.py`, which is exactly where a translation
belongs. Docstrings stay exempt because #1164 left the Russian code documentation alone on
purpose, and a docstring in `workflow.py` or `api.py` is read by a human in the source. A TOOL
docstring is the one that IS shipped, and it gets its own pin rather than an exception here.

THE CROSS-MODULE BLIND SPOT THIS PARAGRAPH USED TO RECORD IS CLOSED, and the old round is kept
because closing it is what the widening bought. #1166's second pass built the case rather than
arguing it — a module-level constant in `api.py` interpolated into `claim`'s already-taken
refusal, i.e. genuinely agent-facing text in exactly this population — and measured control 0
failed / 0 errors / 4 collected, that mutation 0 failed / 0 errors / 4 collected here, with the
card-text gates next door 16 passed on the same mutant. Rebuilt and re-run against the widened
scan on this card's own tree, same selection: control 0 failed / 0 errors / 6 collected; the same
construction -> 1 failed, this file's package scan; and the SAME construction with the scan
narrowed back to `workflow.py` alone -> 0 failed. That pair is what says the widening is
load-bearing rather than decorative, and it is the isolating shape
`tests/unit/test_card_text_is_ascii.py` uses for its own module hop.

WHAT IS STILL NOT SEEN, since replacing a named blind spot with an unnamed one is not progress:
text composed OUTSIDE this package and interpolated in (an `httpx` message, a value read off the
server), and text that is not a literal at all — a message assembled from `chr()` calls is
nothing a literal scan can judge, in this file or in the card-text gates next door. Neither is
claimed covered. The two runtime tests below close two rendered PATHS by reading the result; a
third path would need its own.

MUTATION SWEEP. Selection is this file alone, so no collateral test can stand in for the pin. Run
in a CLONE of the worktree, `__pycache__` deleted and `PYTHONDONTWRITEBYTECODE=1` before every
round, `vikunja_mcp.__file__` printed each round and resolving inside the clone; `-q` dropped so
`collected` prints, and every round read by COUNTING lines beginning `FAILED ` with lines
beginning `ERROR ` counted separately. Each round states its own control:

* the `message` of `_cycle_signal` reverted to its pre-#1166 Russian, byte for byte: control 0
  failed / 0 errors / 4 collected; mutation 2 failed / 0 errors / 4 collected
  (`test_no_cyrillic_string_literal_in_workflow` and
  `test_the_predecessor_cycle_payload_is_all_one_language`).

* `claim`'s epic fallback reverted to its pre-#1166 Russian: control 0 failed / 0 errors / 4
  collected; mutation 2 failed / 0 errors / 4 collected
  (`test_no_cyrillic_string_literal_in_workflow` and
  `test_the_childless_epic_refusal_is_english_to_its_last_word`).

* the fallback replaced by a Cyrillic string that no longer reaches an agent at all — assigned to
  an unused local instead of interpolated — which is what separates the static half from the
  runtime half: control 0 failed / 0 errors / 4 collected; mutation 1 failed / 0 errors / 4
  collected, the static scan alone.

* the mirror of that one: the fallback translated into GREEK, non-ASCII and non-Cyrillic,
  reaching the agent exactly as before. Control 0 failed / 0 errors / 4
  collected; mutation 1 failed / 0 errors / 4 collected — the runtime pin alone, on its
  `its subtasks` literal, with the Cyrillic scan green. That is the blindness this file's unit
  buys, shown rather than asserted.

* closing control 0 failed / 0 errors / 4 collected, with `workflow.py` compared BYTE for byte
  against the pre-sweep baseline after every single restore, not only at the end — a patcher that
  matches nothing is the failure that looks exactly like a blind pin, so it asserts it replaced
  exactly one occurrence and the round is never reached if it did not.

#1166'S ROUNDS ABOVE NAME A TEST THAT HAS SINCE BEEN RENAMED, and they are left standing rather
than rewritten, because each was honest about the tree it ran on. What they call
`test_no_cyrillic_string_literal_in_workflow` is `test_no_cyrillic_string_literal_in_the_package`
since #1170. Both mutations those rounds apply are in `workflow.py`, so the widening moves none
of their counts — but the SELECTION is 6 collected now rather than 4, because #1170 adds two
tests to this file.

#1170'S SWEEP, run in a clone of its own worktree with the working tree COMMITTED INSIDE the
clone first — the #1168 lesson, where a `git checkout --` restore in a clone whose HEAD predated
the fix reverted the fix along with the mutation and only `collected` said so. Selection this
file alone, `__pycache__` cleared and `PYTHONDONTWRITEBYTECODE=1` per round,
`vikunja_mcp.__file__` printed every round and resolving inside the clone, `-q` dropped so
`collected` cross-checks the selection, rounds read by COUNTING lines beginning `FAILED ` with
lines beginning `ERROR ` counted separately, every patcher asserting it replaced exactly one
occurrence, and `git status` read after every restore.

* `api.py`'s unreachable `AssertionError` message reverted to its pre-#1170 Russian: control 0
  failed / 0 errors / 6 collected; mutation 1 failed, the package scan ALONE — the tool-
  description pin stays green through it, which is one half of the pair saying the two pins do
  not stand in for each other.

* the mirror: `attach_file`'s note EXAMPLE reverted to its pre-#1170 Russian. Control 0 failed /
  0 errors / 6 collected; mutation 1 failed, the tool-description pin ALONE — the package scan
  stays green, because a docstring is not a literal it reads.

* the blind spot this card was filed on: a Russian `ConfigError` message in `config.py`, which
  `server._tool` turns into the same `{"error": ...}` result a `WorkflowError` becomes. Control 0
  failed / 0 errors / 6 collected; mutation 1 failed. The SAME mutation with the scan narrowed
  back to `workflow.py` alone gives 0 failed, which is the isolating pair for the widening —
  and #1166's pass measured that narrow form directly, at control 0 failed / 0 errors / 4
  collected and mutation 0 failed.

* the carve-out is the `ru` COLUMN and not the FILE: a Cyrillic `К` put into `cardtext._TABLE`'s
  `en` cell for kilobytes, which is the invisible form because it looks like a Latin K. Control 0
  failed / 0 errors / 6 collected; mutation 1 failed, the package scan. So exempting `ru` did not
  quietly exempt everything that lives beside it.
"""
import ast
import pathlib
import re

import pytest

from tests.unit.fakes import FakeAPI
from vikunja_mcp import cardtext, server
from vikunja_mcp.workflow import STAGES, Workflow, WorkflowError

_SRC = pathlib.Path(__file__).resolve().parents[2] / "src/vikunja_mcp"
_WORKFLOW = _SRC / "workflow.py"
_CARDTEXT = _SRC / "cardtext.py"

# Cyrillic + the Cyrillic Supplement, i.e. the script this repo's other language is written in.
_CYRILLIC = re.compile(r"[Ѐ-ԯ]")


@pytest.fixture
def env():
    api = FakeAPI(buckets=STAGES)
    return api, Workflow(api, project_id=3)


def _non_docstring_literal_nodes(tree: ast.AST) -> list[ast.Constant]:
    """Every string-literal NODE in one parsed module that is not a docstring.

    A docstring is the first statement of a module, class or function and nothing else — an
    attribute "docstring" (a bare string after an assignment) is not one, and is read here like
    any other literal. Nodes rather than values, because the `ru` carve-out below has to identify
    PARTICULAR literals and two rows of a table may hold the same string.
    """
    docstrings: set[int] = set()
    for node in ast.walk(tree):
        holders = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        if not isinstance(node, holders):
            continue
        first = node.body[0] if node.body else None
        if (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)):
            docstrings.add(id(first.value))
    return [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
        and id(node) not in docstrings
    ]


def _non_docstring_literals(path: pathlib.Path = _WORKFLOW) -> list[tuple[int, str]]:
    """(line, value) for every string literal in one module that is not a docstring."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [(node.lineno, node.value) for node in _non_docstring_literal_nodes(tree)]


def _ru_column_literal_ids(tree: ast.Module) -> set[int]:
    """The ids of the literals making up `cardtext._TABLE`'s `ru` column — the ONE carve-out.

    Exactly the `ru` VALUES, never the whole table: an `en` template is read by the package scan
    like any other literal, so Cyrillic landing in the DEFAULT column is caught here as well as
    by the stricter ASCII pin in `test_card_text_is_ascii.py`. Written against the table's shape
    (`{key: {language: template}}`) rather than by importing it, because this scan's unit is the
    SOURCE FILE and a value assembled at import time has no line number to report.
    """
    for node in tree.body:
        target = node.target if isinstance(node, ast.AnnAssign) else None
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
        if not (isinstance(target, ast.Name) and target.id == "_TABLE"):
            continue
        if not isinstance(node.value, ast.Dict):
            return set()
        exempt: set[int] = set()
        for row in node.value.values:
            if not isinstance(row, ast.Dict):
                continue
            for language, template in zip(row.keys, row.values):
                if isinstance(language, ast.Constant) and language.value == "ru":
                    exempt |= {
                        id(c) for c in ast.walk(template)
                        if isinstance(c, ast.Constant) and isinstance(c.value, str)
                    }
        return exempt
    return set()


def _package_cyrillic_literals() -> list[tuple[str, int, str]]:
    """(module, line, value) for every Cyrillic non-docstring literal in `src/vikunja_mcp`.

    The `ru` column of `cardtext._TABLE` is subtracted, and nothing else is.
    """
    out: list[tuple[str, int, str]] = []
    for path in sorted(_SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        exempt = _ru_column_literal_ids(tree) if path == _CARDTEXT else set()
        for node in _non_docstring_literal_nodes(tree):
            if id(node) not in exempt and _CYRILLIC.search(node.value):
                out.append((path.relative_to(_SRC).as_posix(), node.lineno, node.value))
    return out


def test_no_cyrillic_string_literal_in_the_package():
    """EVERY module in `src/vikunja_mcp` writes English literals — one carve-out, named below.

    THE UNIT IS A FILE SET AND NOT A POPULATION, decided by #1170 rather than inherited. The
    population this file is about — what a TOOL CALL returns to its caller — is not statically
    identifiable: nothing marks a string as agent-facing, and the same literal is agent-facing or
    not depending on where it is interpolated. So a scanner cannot take the population as its
    unit; what it can be complete about is a FILE. The rule that follows, and it is the same one
    `test_card_text_is_ascii.py` applies from the other side: pick file boundaries that CONTAIN
    the population, cover each file from inside its own gate, and close what crosses a file
    boundary from the OTHER side rather than letting one scanner walk into another module.

    WHY THE SET IS THE WHOLE PACKAGE AND NOT `workflow.py`. Until #1170 this scan read
    `workflow.py` alone, which is a strict SUBSET of the population: `server._tool` turns a
    `ConfigError` into the same `{"error": ...}` tool result a `WorkflowError` becomes, and
    `config.py` was read by nothing. Measured on the tree at `5ef7cf3` by #1166's second pass and
    reproduced here: a Russian `ConfigError` message planted in `config.py` gave control 0 failed
    / 0 errors / 4 collected and mutation 0 failed / 0 errors / 4 collected at this file's own
    selection, and 0 failed against the whole suite at 1272 collected. The package is the
    smallest boundary a scanner can be complete about that contains the population.

    THE ONE CARVE-OUT is `cardtext._TABLE`'s `ru` column, which is deliberately Russian and is
    the whole point of the `language` key. It is subtracted by VALUE and not by FILE: the `en`
    column of the same table is read here like any other literal, so the carve-out cannot grow
    into "cardtext.py is exempt". `test_the_carve_out_is_the_ru_column_and_not_the_table` is
    what holds that.

    WHAT IS STILL DELIBERATELY NOT READ: comments and docstrings. #1164 left this repo's Russian
    CODE DOCUMENTATION alone on purpose, and a docstring in `workflow.py` or `api.py` is read by
    a human in the source, never shipped. The exception is a TOOL docstring, which the MCP SDK
    ships verbatim as the tool's description — that is a different population with a different
    unit, and it has its own pin below.
    """
    offenders = _package_cyrillic_literals()
    assert not offenders, (
        f"src/vikunja_mcp holds {len(offenders)} Cyrillic string literal(s) outside its "
        f"docstrings and outside cardtext._TABLE's `ru` column: {offenders}. What a tool call "
        f"RETURNS to its caller — a WorkflowError or ConfigError message, a payload "
        f"`message`/`note` — is agent-facing prompt content: it lands in an orchestrator's log "
        f"and in a per-task agent's context, is NOT reached by the `language` key, and stays "
        f"English for every consumer. Prose that a consumer's `language` should translate goes "
        f"in cardtext.py's table, never in a literal here. Russian code comments and docstrings "
        f"are deliberately untouched and are not read by this scan"
    )


def test_the_carve_out_is_the_ru_column_and_not_the_table():
    """The subtraction above is exactly `cardtext._TABLE`'s `ru` values — asserted both ways.

    Both directions matter and they fail differently. If the carve-out resolved to NOTHING (the
    table renamed, its shape changed), the scan above would report thirteen offenders that are
    all deliberate, and a reader would most likely widen the carve-out to the whole file to make
    them go away — which is the failure this test is here to make loud and NAMED instead. If it
    resolved to too MUCH, Cyrillic in the DEFAULT column would stop being read by this file; that
    half is not a disaster, since `test_card_text_is_ascii.py` asserts a stricter ASCII property
    over the same `en` values, but a gate should not quietly stop looking where it says it looks.

    The "too much" half is asserted by SUBTRACTING the exempt values from the `ru` column rather
    than by asking whether any `ru` template is still scanned, and that is a measured detail, not
    a stylistic one: one row is IDENTICAL in both languages (`worklog_evidence`, whose body was
    already English before #1164 while the block around it was not), so its `en` copy makes the
    obvious value-based check report a leak that is not there. The first draft of this test did
    exactly that and went red on a correct carve-out.
    """
    tree = ast.parse(_CARDTEXT.read_text(encoding="utf-8"))
    exempt = _ru_column_literal_ids(tree)
    assert len(exempt) >= 10, (
        f"the `ru` carve-out resolved to {len(exempt)} literal(s), against the 13 rows the table "
        f"carries. It reads the module-level `_TABLE` assignment by shape, so a rename or a "
        f"restructure empties it silently — and the package scan next door would then report the "
        f"whole Russian column as offenders. Re-point _ru_column_literal_ids at the new shape "
        f"rather than widening the exemption to the file"
    )

    scanned = {node.value for node in _non_docstring_literal_nodes(tree) if id(node) not in exempt}
    unscanned_en = sorted(k for k, row in cardtext._TABLE.items() if row["en"] not in scanned)
    assert not unscanned_en, (
        f"the carve-out swallowed the `en` template(s) of {unscanned_en}, so the DEFAULT column "
        f"is no longer read by the package scan. The exemption is the `ru` VALUES, never the "
        f"table and never the file"
    )
    ru_values = {row["ru"] for row in cardtext._TABLE.values()}
    exempt_values = {n.value for n in _non_docstring_literal_nodes(tree) if id(n) in exempt}
    stray = sorted(exempt_values - ru_values)
    assert not stray, (
        f"the carve-out exempts {stray}, which is not a `ru` template. Anything outside that one "
        f"column is ordinary package prose and must be read by the scan next door"
    )


def test_no_shipped_tool_description_carries_cyrillic():
    """A TOOL docstring is not code documentation — the SDK ships it to the agent as the tool's
    description, so it is the surface with a live reader on every call (#1170).

    THE ROSTER IS DERIVED, FAIL-CLOSED, from `server._DEFERRED_TOOLS` rather than from a list
    here: a function decorated `@_mcp_tool` joins it by itself, so a tool added tomorrow is
    scanned without anyone remembering to add it. That is the same derivation idiom
    `test_backlog_placement.py` uses on the same roster, and for the same reason — a hand-written
    list turns a NEW tool into a silent skip, which is the failure that looks exactly like a pass.

    WHAT WAS THERE. Driven over the real registry, 12 tools ship a description and exactly TWO
    carried Cyrillic before this card: `attach_file`'s note example and `file_task`'s chat-
    instruction example, each one Russian inside otherwise English prose. Both were EXAMPLES of
    values this tool deliberately does not constrain (`docs/dossier/config.md`'s middle row: the
    agent's own text, which `language` instructs and nothing rewrites), so the fix keeps what
    they were illustrating and says it in words instead of leaving it to the example's script.

    WHY IT IS A SEPARATE PIN and not a widening of the scan above: that one exempts docstrings on
    purpose, because #1164 left this repo's Russian code documentation alone. The two populations
    happen to live in the same syntax and have opposite rules, so they get one gate each. Measured
    at this file's own selection, `__pycache__` cleared and PYTHONDONTWRITEBYTECODE=1 per round,
    `-q` dropped, rounds read by counting lines beginning `FAILED `: control 0 failed / 0 errors /
    6 collected; `attach_file`'s note example reverted to its pre-#1170 Russian -> 1 failed, THIS
    test alone, with the package literal scan green beside it; the same revert in `api.py`'s
    unreachable `AssertionError` -> 1 failed, the package scan alone, with this test green. The
    pair is what says neither pin stands in for the other.
    """
    tools = server._DEFERRED_TOOLS
    assert len(tools) >= 12, (
        f"only {len(tools)} tool(s) are registered, against the 12 this server ships. The roster "
        f"is what makes this pin fail-closed, so a scan over an empty or half-built one passes "
        f"vacuously — this is a tripwire for the registry, not a count of tools"
    )
    for fn in tools:
        doc = fn.__doc__ or ""
        assert doc.strip(), (
            f"tool {fn.__name__!r} ships no description at all. A tool docstring is agent-facing "
            f"UX copy — the SDK sends it as the description, and an empty one leaves the agent "
            f"guessing when to call it"
        )
        assert not _CYRILLIC.search(doc), (
            f"tool {fn.__name__!r} ships a description carrying Cyrillic: "
            f"{_CYRILLIC.search(doc).group()!r} in {doc[:120]!r}... The MCP SDK sends this "
            f"docstring to the agent verbatim as the tool's description, so unlike code "
            f"documentation elsewhere in this package it is READ by every consumer on every "
            f"call, whatever their `language` key says. Values the agent supplies THROUGH a tool "
            f"are free-form and this says nothing about them; the description itself is not"
        )


def test_an_ascii_unit_would_be_red_on_arrival():
    """WHY this file's unit is Cyrillic while the card-text gates next door assert ASCII.

    Asserted as a property rather than recorded as a count: the number of em dashes in this
    module's refusals moves with every refusal anyone writes, and a stale figure in a docstring
    would be the argument for "just use ASCII here too" the next time someone reads it.
    """
    plain_but_not_ascii = [
        lit for _line, lit in _non_docstring_literals()
        if not lit.isascii() and not _CYRILLIC.search(lit)
    ]
    assert len(plain_but_not_ascii) >= 40, (
        f"only {len(plain_but_not_ascii)} non-ASCII, non-Cyrillic literal(s) left in workflow.py. "
        f"This is a DRIFT RATCHET, not the property: an ASCII unit here is red at any count above "
        f"ZERO, so a genuine zero — and nothing short of it — is what would make the card-text "
        f"gates' ASCII unit available to this file too. Anything between is a signal to re-measure "
        f"before concluding either way; the em dashes and arrows this module's English prose is "
        f"written with are what the count is made of"
    )


def test_the_predecessor_cycle_payload_is_all_one_language(env):
    """The defect exactly as filed: a Russian `message` beside an English `note`, one dict.

    Renders the real payload rather than reading the source, so a message assembled from a helper
    or a module constant is covered on this path whatever its literals look like. Those are the
    two shapes VMCP-294 (1168) measured the neighbouring card-text resolver blind to; it landed
    just under this commit and closed them there, one statically and one by a runtime driver.
    Here the runtime form is the whole answer for this path, and the static scan next door is
    what reaches the sites no test drives.
    """
    api, wf = env
    a = api.add_task("A", "Queue")
    b = api.add_task("B", "Queue")
    api.add_relation(a["id"], b["id"], "follows")
    api.add_relation(b["id"], a["id"], "follows")

    res = wf.next_task()
    assert res["cycle"] is True
    for key in ("message", "note"):
        assert not _CYRILLIC.search(res[key]), (
            f"next_task's cycle payload renders a Cyrillic `{key}`: {res[key]!r}. Both keys of "
            f"this dict are read by the same orchestrator in the same breath — they are one "
            f"population and must be one language"
        )
    # Both sides absolute: the lead-in is spelled here, not imported, so the pin and the code can
    # genuinely disagree. It is also the phrase a human greps for when a chain has stalled.
    assert "PREDECESSOR CYCLE" in res["message"], res["message"]
    assert "Tasks in the cycle: " in res["message"], res["message"]


def test_the_childless_epic_refusal_is_english_to_its_last_word(env):
    """The tail of a sentence is where a translation gets forgotten, and nothing read this one.

    Two tests in `test_workflow_epic_skip.py` drive this exact branch —
    `test_claim_refuses_childless_epic_gracefully` and `test_claim_refuses_epic_container`, whose
    epic has no subtasks either — and both match on "container", so `его подзадачами` rode the
    last two words of an English refusal through a green suite for as long as it existed.
    """
    api, wf = env
    epic = api.add_task("empty epic", "Queue", labels=("epic",))
    with pytest.raises(WorkflowError) as exc:
        wf.claim(epic["id"])

    msg = str(exc.value)
    assert not _CYRILLIC.search(msg), (
        f"claim's epic-container refusal renders Cyrillic: {msg!r}. This is the fallback taken "
        f"when the epic has no subtasks to name, so it is the branch a reader reaches least "
        f"often and the one a translation misses first"
    )
    assert msg.endswith("work on those instead: its subtasks"), msg
