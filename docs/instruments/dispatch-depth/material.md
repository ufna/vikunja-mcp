# Raw material for the width-audit instrument

**SYNTHETIC. Nothing below describes this repository.** These artefacts were written to be the
complete evidence base for `report.md`, which is a fabricated work report carrying planted
defects. The card, the shas, the authors and every number here are invented. Do not cite any of
it as a fact about vikunja-mcp; do not copy a figure out of it into any other file. What it is
for is described in `README.md` beside it.

The audit run is given this file and `report.md` together, and nothing else. Every claim
`report.md` makes is decidable against what is here.

---

## M1 — the sweep table

Twelve runs of the sweep under test. `ignored` counts files in the tree that the porcelain
status does not report. `duration` is wall time for the whole sweep, not per tree. `result` is
what the run did with the tree it was pointed at.

| run | trees | ignored | duration (ms) | result   |
| --- | ----- | ------- | ------------- | -------- |
| 1   | 3     | 0       | 412           | released |
| 2   | 3     | 2       | 455           | released |
| 3   | 1     | 0       | 180           | released |
| 4   | 4     | 1       | 530           | kept     |
| 5   | 2     | 0       | 301           | released |
| 6   | 3     | 5       | 498           | released |
| 7   | 2     | 0       | 288           | kept     |
| 8   | 4     | 0       | 561           | released |
| 9   | 1     | 3       | 205           | released |
| 10  | 3     | 0       | 430           | released |
| 11  | 2     | 1       | 295           | kept     |
| 12  | 3     | 0       | 441           | released |

The table has no column for the refusal code a `kept` run carried.

---

## M2 — the landing window

`git log --format='%h %an %ad %s'` over the window the report calls "the window", newest first.

```
9f2a1c4 ci-bot             2026-08-14 09:12  chore: v0.4.11
7d10b83 agent-vikunja-mcp  2026-08-14 09:09  fix(workspace): hold a tree whose only writes are ignored
5e4cc02 ci-bot             2026-08-14 08:41  chore: v0.4.10
41b9d77 agent-vikunja-mcp  2026-08-14 08:38  feat(workspace): report the trees a sweep declined to inspect
2c8fa10 ci-bot             2026-08-14 07:55  chore: v0.4.9
0ab77e5 agent-vikunja-mcp  2026-08-14 07:51  docs(skill): name the two lists a sweep returns
d4e6b39 Vladimir Alyamkin  2026-08-13 22:04  chore: raise the ceiling for the split
b8c1f52 ci-bot             2026-08-13 21:30  chore: v0.4.8
a71d004 agent-vikunja-mcp  2026-08-13 21:26  fix(api): page the board until no new task arrives
6c3e8ab ci-bot             2026-08-13 20:12  chore: v0.4.7
3f90d16 agent-vikunja-mcp  2026-08-13 20:08  test(unit): pin the deferred branch
e52b7c8 ci-bot             2026-08-13 19:44  chore: v0.4.6
c19d365 agent-vikunja-mcp  2026-08-13 19:40  fix(workflow): resolve a predecessor off this board
8ae0b41 ci-bot             2026-08-13 18:20  chore: v0.4.5
14f7d29 agent-vikunja-mcp  2026-08-13 18:16  feat(config): read the siblings registry
```

---

## M3 — the predecessor card's review comment, quoted whole

```
[review] NEEDS WORK
The guard reads the porcelain status, which does not report ignored paths at all, so a tree
holding nothing but ignored files reads CLEAN. I constructed that state and the tree was
removed with exit 0. I did not test a tree whose ignored files sit under a directory the probe
cannot open — that case is untried here, and I would not guess at it.
```

---

## M4 — the function the report calls the grace window

```python
def _is_recent(path, now, window_s=120):
    """A tree written to inside the window is deferred rather than inspected."""
    try:
        age = now - path.stat().st_mtime
    except OSError:
        return False
    return age < window_s
```

---

## M5 — one sweep's own output, as the tool prints it

```json
{"released": [{"task": 41, "branch_deleted": true}],
 "kept": [{"task": 44, "code": "dirty"}],
 "expected": []}
```
