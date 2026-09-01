"""Grade one audit transcript against the key. Set arithmetic, no judgement, no LLM grader.

The whole reason this file is twenty lines is VMCP-315 (1455): its grader scored one and the
same answer 1 on one transcript and 0 on another, twice in the same cell, so its grading noise
was the size of the effect it was claiming. Here a run answers with sentence identifiers and
grading is intersection and difference over four fixed sets. The price is that the run is told
the report is numbered, which cues the sentence as the unit; that cost is paid equally by every
cell, so it moves no comparison between them.

Usage: python3 score.py <run.json> [<run.json> ...]  -- prints one TSV row per run.
"""

import json
import re
import sys

DEFECTS_EASY = ("S14", "S27", "S41")
DEFECTS_HARD = ("S51", "S58", "S65")
# S64 was PLANTED AS FILLER and is a real defect: M1's own caption also says what the porcelain
# status does not report, so M3 is not the only artefact that does. Neither the author nor the
# pre-run auditor caught it; the RUNS did, and they were scored as false alarms for being right.
# It is promoted here rather than fixed in the report so that the committed instrument stays the
# one the recorded numbers were taken on. Post-hoc: any figure resting on it says so.
DEFECTS_FOUND_BY_THE_RUNS = ("S64",)
LOOKALIKES_EASY = ("S11", "S13", "S17", "S23", "S30", "S32", "S33", "S38")
LOOKALIKES_HARD = ("S44", "S47", "S54", "S60")
ALL_IDS = tuple(f"S{n:02d}" for n in range(1, 70))
KEYED = set(DEFECTS_EASY + DEFECTS_HARD + DEFECTS_FOUND_BY_THE_RUNS
            + LOOKALIKES_EASY + LOOKALIKES_HARD)
FILLER = tuple(i for i in ALL_IDS if i not in KEYED)

_FLAG_BLOCK = re.compile(r'"flag"\s*:\s*\[([^\]]*)\]', re.S)
_ID = re.compile(r"\bS\d{2}\b")


def flagged(transcript: str) -> set[str]:
    """The LAST flag list in the answer, as a set of identifiers the report actually carries."""
    blocks = _FLAG_BLOCK.findall(transcript)
    if not blocks:
        return set()
    return {i for i in _ID.findall(blocks[-1]) if i in set(ALL_IDS)}


def score(run: dict) -> dict:
    got = flagged(run.get("result", ""))
    usage = run.get("usage", {})
    return {
        "hit_easy": len(got & set(DEFECTS_EASY)),
        "hit_hard": len(got & set(DEFECTS_HARD)),
        "hit_post": len(got & set(DEFECTS_FOUND_BY_THE_RUNS)),
        "fa_look_easy": len(got & set(LOOKALIKES_EASY)),
        "fa_look_hard": len(got & set(LOOKALIKES_HARD)),
        "fa_filler": len(got & set(FILLER)),
        "flagged": len(got),
        "cost": round(run.get("total_cost_usd", 0.0), 4),
        "think": usage.get("output_tokens_details", {}).get("thinking_tokens"),
        "ids": ",".join(sorted(got)),
    }


def main(paths: list[str]) -> int:
    cols = ["hit_easy", "hit_hard", "hit_post", "fa_look_easy", "fa_look_hard", "fa_filler",
            "flagged", "cost", "think", "ids"]
    print("\t".join(["run"] + cols))
    for path in paths:
        with open(path, encoding="utf-8") as handle:
            row = score(json.load(handle))
        print("\t".join([path] + [str(row[c]) for c in cols]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
