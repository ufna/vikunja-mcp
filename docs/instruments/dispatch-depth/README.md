# The width-audit instrument — how to run it, and what it can and cannot separate

Built on VMCP-319 (1468), which was filed by VMCP-315 (1455) against its own instrument. The
measurement this one produced lives in `src/vikunja_mcp/skills/tracker/references/dispatch-depth.md`,
under the section naming this card; what lives HERE is the apparatus, so that a later card can
re-run it instead of building a third one.

**Read the boundary first: this is a CLOSED-BOOK instrument.** The run is handed the report and
the whole of the material it rests on, and asked to judge the width of each claim against what is
in front of it. That is one half of a real second pass. The other half — deciding what to go and
look up — is not exercised at all, so a null result here bounds a claim to the judging half and
says nothing about the looking-up half. VMCP-315 named an open-book arm as the thing most worth
adding, and this card did not add one either; it is still the obvious next step.

## The files

* `material.md` — five synthetic artefacts: a table of twelve runs, a fifteen-line git log, a
  quoted review comment, a python function, a JSON blob. Invented. Nothing in it describes this
  repository or any other.
* `report.md` — a synthetic work report, 69 sentences, each tagged `(Snn)`. Six sentences are
  planted defects and a SEVENTH turned out to be one: S64 was written as filler, is false against
  the material, and the runs found it. Twelve sentences are LOOKALIKES: sound, and engineered to
  look like defects. The other 50 are filler.
* `key.md` — the verdicts, and a written width check on every keyed item. **This file is the
  instrument's actual contribution**; see below.
* `prompt.md` — the exact instruction, plus the reason it does not say how many defects there are.
* `score.py` — the grader. Set arithmetic over sentence identifiers.

## What is different from the instrument this replaces

Three things, one per defect VMCP-315's own second pass found in its own apparatus.

**It scores PRECISION, not only recall — and the way it does that is the design.** Twelve of the
sound sentences are LOOKALIKES: each wears the rhetorical shape of a defect and is sound anyway.
Six of them are lexical TWINS of the six defects — an unhedged universal, a statement about what
the code does, an attribution to a named reviewer, a named statistic, a true clause with a
trailing consequence, a cross-artefact identification. A run that recognises the SHAPE takes the
defect and its twin alike and nets zero on the pair; a run that reads the material takes the
defect and leaves the twin. So precision is not a second axis bolted on beside recall: it is the
same axis read from the other end, and the pair is the unit that carries the information.

**The key is width-checked before the runs, item by item.** Every keyed item names the exact
place in `material.md` that decides it AND the reading a run might defensibly give instead. An
item whose "defensibly instead" cell is non-empty and unanswerable does not belong in the key.
That is VMCP-315's third defect turned into a procedure: its only model-ladder gap came from a
key item that was itself a claim wider than its evidence.

**Grading is set arithmetic.** The run answers with a fenced JSON list of sentence identifiers,
and `score.py` intersects that with four fixed sets. VMCP-315's grader scored the same answer 1
on one transcript and 0 on another, twice in the same cell, so its grading noise was the size of
the effect it was claiming; there is no grader noise here at all. The price is that the run is
told the report is numbered, which cues the sentence as the unit of a finding. That price is paid
equally by every cell, so it moves no comparison between them.

## Running it

```sh
D=docs/instruments/dispatch-depth
SP=<a scratch directory>                    # never the repo: the runs must not see it
mkdir -p "$SP/cwd" "$SP/runs"
# Assemble the prompt by cutting each file at its FIRST `---` rule. That is what keeps the
# synthetic-warning preamble — and the pointer to the key — out of the run's sight.
{ sed -n '/^---$/,$p' "$D/prompt.md"   | tail -n +2
  printf '\n\n===== THE REPORT =====\n'
  sed -n '/^---$/,$p' "$D/report.md"   | tail -n +2
  printf '\n\n===== THE RAW MATERIAL =====\n'
  sed -n '/^---$/,$p' "$D/material.md" | tail -n +2 ; } > "$SP/prompt.txt"
# Then, from "$SP/cwd" so that no project rules are in scope:
claude -p --model <m> --effort <e> --output-format json --allowed-tools "" \
  < "$SP/prompt.txt" > "$SP/runs/<cell>-<i>.json"
python3 "$D/score.py" "$SP"/runs/*.json
```

**The committed corpus reassembles the exact prompt the recorded runs used.** Checked with
`cmp`, not asserted: the assembly above run against these files is byte-identical to the prompt
file the nineteen scored runs were fed. That is why the report is not "cleaned up" — a rewrap
would change no meaning and would break the one property that lets a later reader re-run the
same measurement rather than a similar one.

The leak check is not optional and takes one line — if any of these words reaches the assembled
prompt, the run has been told there are plants and the measurement is void:

```sh
grep -niE "synthetic|planted|deliberate falsehood|defect|key\.md|fabricat|on purpose" "$SP/prompt.txt"
```

## The observable is OUTSIDE the run, on both halves

A subagent cannot report its own effort, and a run's own account of how hard it thought is worth
nothing. Cost and `usage.output_tokens_details.thinking_tokens` come from the harness's own JSON;
quality comes from `score.py`. The thinking-token count is also the within-run control that the
`--effort` flag bit at all.

## Deliberate deviations from this repo's house style, and why

The report imitates the house style closely enough to be a fair test and stops short in two
places, both because a gate reads them. It writes no historical anchor of the form a figure
followed by a backticked sha, because `tests/unit/test_measured_figure_anchors.py` requires such
a sha to be a real ancestor of HEAD and every sha here is invented. And it avoids the assertive
idioms `test_repo_quotation_claims.py` reads, because a synthetic report asserting that a phrase
is in this checkout would be asserting something false about a real tree. Neither deviation
touches a keyed item.
