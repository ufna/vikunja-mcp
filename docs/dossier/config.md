# config.py — 4-слойный конфиг, wip_limit, независимость ревью

> **Это ДОСЬЕ, а не правила.** Правило живёт в `CLAUDE.md → Архитектура → config.py` — там оно короткое и
> обязательное к исполнению. Здесь лежит доказательная база: измерения, построенные
> стенды, опровергнутые формулировки и номера карточек.
>
> **Читай перед тем, как менять этот код.** Этот репозиторий уже чинил гарды
> рассуждением вместо измерения — по несколько раундов подряд. Если правило кажется
> избыточным, ответ почти наверняка здесь.

- `src/vikunja_mcp/config.py` — 4-layer config: env (`VIKUNJA_URL/TOKEN/PROJECT_ID`)
  > repo-local `.vikunja-mcp.env` (same dir as the toml, found by the same walk-up,
  gitignored) > repo `.vikunja-mcp.toml` (walk-up from cwd) > `~/.config/vikunja-mcp/env`.
  Token is NEVER read from the repo toml (so it can't be committed and used); optional
  `VIKUNJA_NOTIFY_WEBHOOK` (`notify.py` — best-effort Slack-shaped ping when `call_human`
  parks a card in Your Call) is a secret of the same class: env layers only, never the toml.
  Two parallel-drain keys sit on opposite sides of that split: `wip_limit` (how many
  Design/Build tasks one token may CLAIM into at once — not how many it may HOLD, and what the
  difference costs is spelled out further down this same bullet, at "a gate on ONE transition";
  generalises `enforce_single_wip`, which is
  exactly 1) is committed TEAM POLICY — repo toml ONLY, never env. **Unset means
  `DEFAULT_WIP_LIMIT` = 3, not "no gate"** (human decision, tracker #524 — the gate is always
  on, so every project drains 3-wide without a toml edit); precedence is explicit `wip_limit` →
  else 1 when `enforce_single_wip = true` → else 3, resolved in `workflow._effective_wip_limit`
  (which returns `int`, never `None`) while `Config.wip_limit is None` keeps meaning only "the
  key is absent". `wip_limit = 0` is a `ConfigError`, NOT the unbounded spelling: "no limit" is
  deliberately not expressible any more. **It is a gate on ONE transition (`claim`), not an
  invariant on the active count** (tracker #529): a card re-enters Build without passing it —
  `review_task(verdict='needs_work')` bounces it Review→Build, a human moves it out of Your Call
  or hand-places an assigned card, or the toml lowers the number while work is in flight — so
  `wip.active` legitimately EXCEEDS `wip.limit` (4/3 observed live), and that is correct, because
  rework must be receivable at the limit. `next_task`'s `free` is `max(0, limit - active)`, so the
  overshoot is invisible there and readable only from `active`/`limit`; `claim` keeps refusing and
  reports the true count. Making it impossible, or gating the second path, is deliberately NOT
  done — both would strand reviewed work. `worktree_root` /
  `VIKUNJA_WORKTREE_ROOT` (where per-task worktrees materialise, default a `<repo>.worktrees`
  sibling) is MACHINE-local, so unlike `wip_limit` the env layers DO win over the toml.
  **`require_review_independence` is a THIRD key on `wip_limit`'s side of that split — repo toml
  ONLY, never env, default FALSE** (tracker #37, the human's own answer picking a flag over an
  unconditional gate). On, `review_task` refuses a verdict from anyone in the card's own
  assignees; off, it does not resolve `me()` at all, so the behaviour and the request trail are
  what they were before the gate existed. **The default is the feature, not a soft rollout, and
  reading it as a hole gets the setup backwards.** In a SOLO setup one scoped token is the whole
  fleet — the orchestrator and every per-task agent it dispatches, reviewers included,
  authenticate as ONE assignee — so the ABSENCE of an authorship check is the CONDITION OF
  OPERATION; independence there is carried by the agents' separated CONTEXTS (push model, a
  sibling reviewer with a fresh context), which nothing server-side can observe. Turn it on
  without a second identity and NOBODY can review anything, here or at any consumer on `stable`,
  which is why this repo's own toml deliberately does NOT set it and why the refusal names the
  way back out. What it closes is the MULTI-IDENTITY hole, measured by BEHAVIOUR rather than
  read off the call graph: before the gate, a verdict from the card's own assignee was ACCEPTED
  on both verdicts, `approve` landing the `reviewed` label a human reads for Done — because
  `review_task` is the ONE mutating tool that never calls `_require_mine`, its `_assignee_ids`
  read being #705's ownerless ROUTING and never an authorship check. So "you don't review your
  own work" rested ENTIRELY on `next_task`'s OFFER filter, and an offer filter is a hint, not a
  gate: it is not consulted by a direct call, and #885's kanban blackout DELETES it outright.
  That last shape is why the gate reads assignees through `_kanban_assignees_may_be_stale`
  rather than raw — judged off the board copy it would find nobody and pass precisely the card
  whose other protection is already gone. A genuinely ownerless card still passes (no author to
  exclude) and its `needs_work` still routes to Queue.
  **Wired at BOTH construction sites — `server._build_workflow` AND `claimable_cmd` — since
  VMCP-295 (1169).** Until then it was wired in the server only, justified by "that one runs
  `next_task` and nothing else, so the flag could never be consulted there and passing it would be
  dead wiring on the one path that must stay read-only and cheap". TRUE at #37, stale from #991
  on, and refuted on BOTH halves. **Not dead:** the authorship skip in `next_task`'s
  review-offering branch was already there and UNCONDITIONAL, and #991 made it conditional on the
  flag (`workflow.py`, the `continue` guarded by `self.require_review_independence and my_id in
  self._assignee_ids(t)`) — so from that card on `next_task` is exactly a caller of the flag — measured on an identical `FakeAPI` board, one card driven claim →
  build → review by ONE identity, `classify_next(wf.next_task())` answers `{"claimable": true,
  "kind": "review"}` with the flag false and `{"claimable": false, "kind": "empty"}` with it true.
  **Not a cost either — about the GUARD, which is as far as the claim goes:** `next_task`
  resolves `my_id = self._me()["id"]` unconditionally at its top, so the guard issues no request
  of its own, and when it FIRES it `continue`s BEFORE that card's `comments()` fetch. It does NOT
  follow that the whole call is cheaper: the `continue` sends the loop on to the queue branches,
  which fetch per candidate, and on a board of gated Queue cards that is 7 api calls against 2.
  The three measured boards are in `docs/dossier/claimable.md`.
  **What generalises, and is the part to keep: a `Config` key that `Workflow` READS is wired at
  BOTH sites** — `claimable`'s whole stated property is a verdict with ZERO drift from the
  agent's own, and a kwarg present on one side only IS that drift. `notify_webhook` →
  `notifier` is the only remaining asymmetry between these TWO sites and is a real one:
  `call_human` alone touches the notifier, and `claimable` calls `next_task` alone. (There is a
  THIRD site — `workspace_cmd._build_workflow`, which wires no Config key at all; why that is
  currently safe is in `docs/dossier/claimable.md`, and it is a fact about what `--gc` calls, not
  a guarantee.) Shipping the fix was inert where it could be checked — no `.vikunja-mcp.toml`
  found on the author's disk sets the flag — and the answer moves only for a repo that does.

## `language` (tracker #1165) — a FOURTH key on `wip_limit`'s side of the split

`language = "en" | "ru"` in the repo toml, default `en`, repo toml ONLY. It sits with `wip_limit`
and `require_review_independence` rather than with `worktree_root` for the reason that separates
those two groups: which language a project's cards are written in is a property of the PROJECT,
reviewed by whoever reviews the committed file, not of the machine an agent happens to run on.
Pinned by `tests/unit/test_card_language.py::test_language_is_toml_only_and_never_read_from_the_environment`,
which sets the value in both env layers a test can reach — the process environment and the
repo-local `.vikunja-mcp.env` beside the toml — under a `VIKUNJA_`-prefixed and a bare spelling
each, and asserts the toml still wins. The third env layer, `~/.config/vikunja-mcp/env`, is a
real machine path and is left alone rather than pretended at.

An unknown value is a `ConfigError` naming the accepted set, on the `wip_limit = 0` precedent.
The reason is sharper here than there, and it is worth stating rather than inheriting: this key's
LARGER half is an INSTRUCTION to the agent, so a silent fallback to `en` would not merely pick a
default — it would tell the agent to write in a language its human did not choose, with no signal
on any surface. An option that cannot be honoured is made un-expressible loudly.

**THE FINDING THAT SHAPES THE DESIGN: "the language of a card" is three populations of string
with different audiences, and a key that localises only OUR strings half-delivers.** The spec,
the worklog and the review report are the bulk of a card's text, and this tool does not write a
character of them — the AGENT does. Measured on VMCP-290 (1164) by this card's independent second
pass: three short product-authored body lines there, against roughly 30 KB of agent-authored
spec, worklog and review report. So the key is FIRST an instruction (it rides
in every `next_task` payload beside `wip`, and SKILL.md's "Traces of the work" section states the
rule) and only SECOND a translation table for the dozen lines the product authors. A key that
skipped the first half would produce cards with Russian boilerplate around an English spec, which
is worse than no key at all.

The three populations, and where each one is decided:

| population | who writes it | what `language` does |
| --- | --- | --- |
| the product's own prose — the `[claim]` line, the `[worklog]` prefixes, `[decompose] created:`, the three `[filed-by-agent]` variants, the `[epic-ready]` body, `_human_size`'s units | `workflow.py`, via `cardtext.py` | translates it |
| the agent's own text — `spec`, `worklog`, `root_cause`, a `call_human` question, a `[review]` report, an `attach_file` note | the agent | instructs it, and nothing more |
| the wire format — the ten markers, and the `APPROVE`/`NEEDS WORK` verdict tokens | `workflow.py` | nothing, in either direction |

**The third row is the one that breaks things, and it breaks silently.** `workflow.py` decides
whether a Review card is offered to a reviewer by comparing the timestamp of the last comment
whose rendered text starts `[worklog]` against the last starting `[review]`. A localised marker
therefore does not fail loudly on the card being written — it fails later, on every card written
under the OTHER setting, by dropping out of the offering. That is why the table holds BODIES only
and the bracket stays a literal at its `add_comment` call site, and why the invariance is measured
from two directions rather than reviewed: byte-for-byte over two fully driven boards, and again
through `next_task`'s real offering branch with the setting flipped between writing and reading.
The verdict tokens stay English for a smaller but real reason: SKILL.md quotes both spellings to
the reviewer, so localising them would make the rulebook false in one of the two languages.

**Wired in `claimable_cmd` as well as in `server._build_workflow`**, because `language` is
emitted by `next_task` itself — the one call that path makes — so omitting it would make the
payload report a `ru` project as `en`. The hub's three-key contract (`claimable`/`kind`/`task_id`)
never reads it, so the verdict cannot change either way.

**AND A REFUTATION FOUND WHILE JUSTIFYING THAT.** The obvious justification is "unlike
`require_review_independence`, which is deliberately dead here". It was written, and measuring it
refuted it. That flag is NOT consulted only by `review_task`: since #991 it is read inside
`next_task`'s own review-offering branch (`workflow.py`, the `continue` guarded by
`self.require_review_independence and my_id in self._assignee_ids(t)`). Measured on an identical
`FakeAPI` board, claim -> build -> review, `classify_next(wf.next_task())` answers
`{"claimable": true, "kind": "review"}` with the flag false and
`{"claimable": false, "kind": "empty"}` with it true. So its absence from `claimable_cmd` WAS a
real divergence from the MCP server for any repo that sets it, and the sentence justifying the
omission — in `server._build_workflow`'s comment and in the `require_review_independence` bullet
above — had been stale since #991. Filed as VMCP-295 (1169); NOT fixed in THAT card, because
changing what `claimable` answers changes a cross-repo contract and belonged in a card of its
own. 1169 then wired it, so the flag is now passed at both sites — the resolution and its cost
measurement are in the `require_review_independence` bullet above. It was inert in this repo
throughout, which sets no flag.

**What the `en` column is, exactly:** #1164's text unchanged, and checked at the only level that
settles it — the CARDS, not the source. One driver script exercising all six product-prose
transitions runs unmodified against a `62af682` checkout and against this tree with
`language="en"`; the two 16-comment boards come out BYTE-IDENTICAL (`diff` empty). Measured twice
independently, by the author and by this card's second pass, each in its own clone. So the table
is a MOVE plus a second column and not a re-translation, and the interpolation rename
(`{me['username']}` became the `str.format` field `{username}`) changes nothing that reaches a
card. A reader who wants to compare by eye should open `git show
62af682:src/vikunja_mcp/workflow.py` rather than the commit's diff, where two of the strings are
split across literal continuations.

**What #1164's ASCII pin became.** Its title claimed every string the tool authors onto a card is
ASCII; with a `ru` column that sentence is false, and the file says so now. The claim split in
two: bodies are ASCII in the DEFAULT language (asserted over `cardtext._TABLE`'s `en` column, and
the `ru` column is asserted to be non-ASCII somewhere — a `ru` column that came out ASCII would
mean nothing was translated, which no other assert in that file can see); markers are ASCII in
every language, unchanged, because two of them are parsed and the rest are frozen alongside those
two. The source scan over `workflow.py`'s
`add_comment` sites survived the move but now resolves 52 literals (against 51 at `62af682`) that
are markers, layout and
`card_text` KEY names rather than prose — the count held steady only because each key name
replaced roughly the phrase it fetches, so the count stopped being evidence about the prose. What
that scan still catches alone is non-ASCII typed straight into an `add_comment` argument, which
is the shape a new card line takes before anyone thinks about the table.

## `siblings` — the neighbour registry (#1179)

**What created it.** The dogiators setup is two repos on ONE scoped token —
`fight.dogiators.com-front` against project 4 (`dogiators-front`) and
`fight.dogiators.com-back` against project 17 (`dogiators-backend`), both children of a
project 16 the token cannot even read (403 on `/projects/16`, measured). The token was
never the limitation: it returns BOTH projects from `/projects` and 200s on each. The
limitation was `project_id: int` in the config plus the fact that **neither toml named the
other project**, so an agent in front had no way to learn a backend existed, let alone that
it was 17. `file_task(project_id=…)` had been able to write across the boundary since #125;
what was missing was any way for the agent to know what to put in that argument.

**Why it is not a gate, said once so it is not re-litigated.** The obvious next thought is
to narrow `file_task`'s free-form `project_id` to the registry. It is deliberately not
done. The security boundary here is the scoped token — that is a standing rule of this repo
and it is the thing Vikunja actually enforces (403 with nothing created, wrapped by
`_target_backlog` into an actionable refusal). A name list in a committed toml enforces
nothing an attacker or a confused agent could not step around by passing the id, so calling
it a guard would be a false statement about what protects the boards. It is an ADDRESS
BOOK. Narrowing `file_task` on top of it would break every existing caller to buy nothing.

**The refusals, and why each one exists rather than being coerced.** All are `ConfigError`
naming the offending entry, on the `wip_limit = 0` precedent — the reader's next act is
editing one line of a committed file, so the message names the line.

- a non-table value (`siblings = 17`) — the plausible typo for a single sibling. Refused by
  SHAPE: a registry with no names is not one an agent can address.
- a blank name — the name is what an agent types; unspellable means unusable.
- a **bool** id, checked BEFORE the int check. TOML has real booleans and `int(True)` is 1,
  a live project id, so `siblings = { backend = true }` would silently address project 1.
- a non-positive id — 0 is no project; negative ids are Vikunja pseudo-projects (favorites).
- **this project's own id.** A self-sibling is not merely useless: `handoff` would file a
  card into its own project's Backlog and then block the current card on it, and no gate in
  the package can break that cycle.
- **two names for one id.** The registry is read in BOTH directions — name→id when a tool is
  called, id→name when provenance is written onto a card — and the second direction has no
  answer when two names collide.

**The load-bearing half is `next_task`.** The key rides in every payload beside `wip` and
`language`, for the same reason those do: it is project policy the agent cannot read off the
board. Without it the two new tools are addressable only by a number nobody can discover,
which is indistinguishable from not shipping them.
