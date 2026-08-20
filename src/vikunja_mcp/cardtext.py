"""The prose this tool AUTHORS onto a card, in the languages the `language` key names (#1165).

ONE MODULE, deliberately. `workflow.py` has twelve `self.api.add_comment` call sites, and SIX of
them contribute prose of the product's own — `claim`, the epic-assembled notice, the `[worklog]`
report, `decompose`, `file_task` and the `[attach]` line (that last one reaching this table across
a function boundary, through `_human_size`). The other six contribute no prose of ours and read
nothing here: four wrap a bare marker around text the agent supplied (`[spec]`, `[needs-human]`,
`[blocked]`, `[attach]`'s note), one pair adds a verdict TOKEN to it (`[review] APPROVE` /
`NEEDS WORK`), and the `comment` tool posts the agent's text with no marker of ours at all.

The alternative to one module is an `if self.language == "ru"` at each of the six: one decision
spread across a file whose gates are already dense, and "are the two languages still in step?"
becomes a question nobody can answer without reading all six. Here it is a dict comparison, which
is what `test_card_language.py` does.

WHAT IS AND IS NOT IN HERE, because the boundary is the whole design and it is not obvious.

* IN: the BODY of every comment the product itself writes. Nothing else in the package composes
  card prose.
* OUT — THE MARKER. Every value below is a body only; the `[claim]` / `[worklog]` / `[attach]`
  bracket stays a literal at its own call site in `workflow.py`. That is not tidiness, it is the
  wire format. TWO of the twelve are literally parsed: `workflow.py` matches rendered comment text
  with `startswith("[review]")` and `startswith("[worklog]")` — those are its only two reads of
  comment text — so a per-language spelling on either drops every card written under the other
  setting out of the review offering, silently. The other ten are frozen with them rather than
  for their own sake: the vocabulary is read by eye and by grep, and half of it translated is
  worse than either half alone. `test_card_language.py` asserts no value here contains a `[` at
  all, so a bracket cannot drift in later.
* OUT — `WorkflowError` text, and the `note`/`message` strings in tool payloads. Their audience is
  the AGENT, they are effectively prompt content, and they land in logs.
* OUT — `[review] APPROVE` and `[review] NEEDS WORK`. Those are verdict TOKENS, not prose: SKILL.md
  quotes both spellings to the reviewer and to anyone scanning a card by eye, so localising them
  would make the rulebook false in the other language.
* OUT — whatever the AGENT supplies (`spec`, `worklog`, `question`, an attachment note). This tool
  never rewrites it. That half of the card is governed by the SKILL.md rule and by the `language`
  key riding in `next_task`'s payload, not by anything here — and it is the LARGER half.
* OUT — `setup_cmd`'s stdout. That is operator console output, not card text.

ASCII IS A PROPERTY OF THE `en` HALF ONLY, and that is the correction #1165 makes to #1164's pin.
`tests/unit/test_card_text_is_ascii.py` used to open by claiming that EVERY string this tool
authors onto a card is ASCII; with a `ru` column that is false, and the file now says so. The half
that stayed absolute is the marker vocabulary: ASCII in every language, because a marker is
matched rather than read.
"""
from .config import DEFAULT_LANGUAGE

# key -> {language -> template}. Templates use str.format fields; a key with no field is a plain
# string and is returned as-is. Keys are grouped by the call site that renders them.
#
# The `en` column carries #1164's text UNCHANGED, so this table is a MOVE plus a second column and
# not a re-translation. Checked at the level that settles it — the CARDS, not the source: one
# driver exercising all six product-prose transitions runs unmodified against a `62af682` checkout
# and against this tree at `language="en"`, and the two boards come out BYTE-IDENTICAL. Measured
# twice independently, by the author and by this card's second pass, each in its own clone. So the
# interpolation rename (`{me['username']}` -> the `str.format` field `{username}`) changes nothing
# that reaches a card. To compare by eye, open `git show 62af682:src/vikunja_mcp/workflow.py`
# rather than the commit's diff — two of these strings are split across literal continuations
# there and read as drift when there is none.
_TABLE: dict[str, dict[str, str]] = {
    # claim
    "claim": {
        "en": "{username} claimed this task",
        "ru": "{username} взял задачу в работу",
    },
    # advance(to='review') — the [worklog] body. In `en` the prefixes mirror `advance`'s own
    # parameter names (#1164's choice); the `ru` column is the spelling this block carried before
    # #1164 translated it.
    "worklog_root_cause": {
        "en": "Root cause: {root_cause}",
        "ru": "Причина: {root_cause}",
    },
    "worklog_worklog": {
        "en": "Worklog: {worklog}",
        "ru": "Сделано: {worklog}",
    },
    # the two columns are IDENTICAL here, and that is the pre-#1164 text rather than an omission:
    # the label was already `Evidence:` when the rest of this block was Russian. It stays a table
    # entry so the whole [worklog] body is composed in one place; the leading blank line that
    # separates it from the body is LAYOUT and lives at the call site.
    "worklog_evidence": {
        "en": "Evidence: {evidence}",
        "ru": "Evidence: {evidence}",
    },
    # decompose
    "decompose_created": {
        "en": "created: {listing}",
        "ru": "создано: {listing}",
    },
    "decompose_ordered": {
        "en": " (ordered: a precedes chain, only the head is claimable)",
        "ru": " (упорядочено: цепочка precedes — клеймабельна только голова)",
    },
    # file_task — the three provenance variants plus the suffix any of them may carry
    "filed_cross_project": {
        "en": "filed by an agent from project id={project_id} for human triage",
        "ru": "заведено агентом из проекта id={project_id} для триажа человеком",
    },
    "filed_queue": {
        "en": "filed by an agent straight into Queue (Backlog triage skipped)",
        "ru": "заведено агентом сразу в Queue (минуя триаж в Backlog)",
    },
    "filed_backlog": {
        "en": "filed by an agent for human triage",
        "ru": "заведено агентом для триажа человеком",
    },
    "filed_related": {
        "en": " (found while working on #{related_task_id})",
        "ru": " (по ходу работы над #{related_task_id})",
    },
    # the epic container's assembled notice
    "epic_ready": {
        "en": (
            "all {children} child(ren) of this epic reached Review-or-Done: the container is "
            "assembled and ready for your Done (only a human moves a task to Done). If you "
            "later bounce a child back out of Review you will see it in Build again, and can "
            "hold the close until it returns."
        ),
        "ru": (
            "все {children} дет(и) эпика достигли Review-или-Done — контейнер собран и готов "
            "к твоему Done (в Done двигает только человек). Если позже отобьёшь ребёнка из "
            "Review — увидишь его в Build и придержишь закрытие."
        ),
    },
    # handoff / transfer_task — a card crossing a project boundary (#1179). The AGENT's half
    # (the new card's title/description, and transfer_task's `reason`) stays out of this table
    # as everywhere else; what is here is only the provenance prose the tool itself authors.
    "handoff_parked": {
        "en": (
            "paused: the next step on this card belongs to project id={project_id}, where an "
            "agent filed #{new_id} for human triage. This card is blocked on that one and "
            "returns to the queue by itself once it reaches Review"
        ),
        "ru": (
            "пауза: следующий шаг по этой карточке относится к проекту id={project_id}, там "
            "агент завёл #{new_id} для триажа человеком. Карточка заблокирована на ней и "
            "вернётся в очередь сама, когда та дойдёт до Review"
        ),
    },
    "handoff_filed": {
        "en": (
            "filed by an agent from project id={project_id}, whose task #{blocked_task_id} "
            "is blocked on this one, for human triage"
        ),
        "ru": (
            "заведено агентом из проекта id={project_id}, чья задача #{blocked_task_id} "
            "заблокирована на этой, для триажа человеком"
        ),
    },
    "moved_from": {
        "en": (
            "moved here from project id={project_id} by an agent, with its comment history; "
            "it was re-indexed on arrival, so refs quoted in that history are the old ones"
        ),
        "ru": (
            "перенесено сюда из проекта id={project_id} агентом вместе с историей "
            "комментариев; при переезде карточка переиндексована, поэтому реф'ы в этой "
            "истории — старые"
        ),
    },
    # _human_size's units, interpolated into the [attach] journal line. Sizes, not prose — but
    # they are card text, they were translated by #1164, and a Russian board reading "1.4 MB"
    # inside an otherwise Russian line is exactly the half-delivery this key exists to avoid.
    "size_bytes": {"en": "{size} B", "ru": "{size} Б"},
    "size_kilobytes": {"en": "{size} KB", "ru": "{size} КБ"},
    "size_megabytes": {"en": "{size} MB", "ru": "{size} МБ"},
}


def card_text(language: str, key: str, **fields: object) -> str:
    """Render one card-text key in `language`.

    An unknown LANGUAGE falls back to the default rather than raising: `config.load_config`
    already refuses an unknown value by name, so reaching here with one means a caller
    constructed a `Workflow` by hand, and a card comment is the wrong place to discover that —
    `add_comment` sits mid-transition, after the board has already been moved.

    An unknown KEY raises, because that is a programming error with no runtime input behind it
    and no half-written card to protect.
    """
    try:
        row = _TABLE[key]
    except KeyError:
        raise KeyError(
            f"no card text for {key!r}; known keys: {', '.join(sorted(_TABLE))}"
        ) from None
    template = row.get(language) or row[DEFAULT_LANGUAGE]
    return template.format(**fields) if fields else template
