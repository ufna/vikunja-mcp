"""Stages and gates of the agent flow. The rules are baked in here, not in prompts.

DOSSIER: `docs/dossier/workflow.md` — the measured evidence under the rules in this
module: the stage/gate rules and the push-review decision.
Read it before changing a guard here; CLAUDE.md carries only the rule.
"""
import mimetypes
import os
import shutil
import sys
import tempfile
import time
from collections.abc import Callable
from typing import Any

import httpx

from .api import VikunjaError, label_key
from .cardtext import card_text
from .config import DEFAULT_LANGUAGE, DEFAULT_WIP_LIMIT
from .formatting import html_to_text
from .notify import WebhookNotifier

STAGES = ["Backlog", "Queue", "Design", "Build", "Review", "Your Call", "Done", "Icebox"]
# Icebox (#1640) is the "backlog of the backlog": very minor work, lyricism, permanent legacy
# — cards nobody is expected to pick up and nobody should burn tokens gold-plating. It is the
# ONE canonical column a board may legitimately lack, and that is not a nicety: `stable` is a
# moving channel re-resolved at every session start, so the release carrying this stage reaches
# every consumer board before any human runs `vikunja-mcp setup` on it. Were the presence check
# below run over STAGES, the FIRST call of ANY tool on such a board would answer "run
# `vikunja-mcp setup`" — the whole fleet down until each board is migrated by hand.
# Note where it does NOT appear: not in NEXT_TASK_STAGES (so the column gates the pump for
# free), not in ACTIVE_STAGES, and not in READY_STAGES — see the frozen-predecessor clause in
# `_predecessor_frozen` for what that last one costs and how the cost is paid.
REQUIRED_STAGES = [s for s in STAGES if s != "Icebox"]
ACTIVE_STAGES = ("Design", "Build")
# The only stages next_task ever inspects (Queue for free/stuck tasks, Design/Build for my
# active ones, Review for bug re-review). It never reads Done/Backlog/Your Call, so its board
# fetch passes these as view_tasks(require_titles=...) — the unboundedly-growing Done is no
# longer paged exhaustively on every next_task, which is the #43 latency fix.
NEXT_TASK_STAGES = frozenset({"Queue", *ACTIVE_STAGES, "Review"})
LABEL_BLOCKED = "blocked"
LABEL_EPIC = "epic"
# маркер: все дети эпика в Review/Done — контейнер собран, ждёт Done человека
LABEL_EPIC_READY = "epic-ready"
LABEL_BUG = "bug"
LABEL_REVIEWED = "reviewed"            # прошёл независимое агентское ревью
LABEL_REVIEW_FAILED = "review-failed"  # отбит на доработку, сейчас переделывается
# #1640: the freezer's marker. English like the other twelve — `language` governs card PROSE,
# never a marker. IT IS NOT A GATE, and that is the card's central decision: it is deliberately
# absent from `offerable_queue`, where LABEL_BLOCKED and LABEL_EPIC sit, because that filter
# drops a card SILENTLY (two lines below it, `withheld` is built exclusively from `excluded`).
# The COLUMN gates; the label is what survives a move out of it, and what rides in the payload
# as ICEBOX_HINT so an agent working such a card knows to spend the minimum.
LABEL_ICEBOX = "icebox"
ICEBOX_HINT = (
    "this card is iceboxed (label `icebox`): legacy/very-minor work. Do the MINIMUM that is "
    "correct — no refactor, no adjacent cleanup, no gold-plating. If it turns out to need real "
    "work, say so in your report and let a human re-prioritise it rather than doing it here."
)

# Hard sequence gate (option C, epic #94). A predecessor is "ready" — no longer blocks its
# successor — only at Review or Done. The human chose REVIEW (not Done) as the bar so a chain
# can drain autonomously: only a human moves a task to Done, so gating on Done would wedge a
# human between every step. NB: "Your Call" sorts AFTER Review in STAGES yet is NOT ready (a
# parked question), so readiness is explicit set membership, never a positional comparison.
READY_STAGES = frozenset({"Review", "Done"})
# Relation kinds that make the OTHER task a PREDECESSOR of this one. Vikunja auto-inverts:
# "P precedes S" surfaces as "follows: P" on S; "P blocking S" surfaces as "blocked: P" on S.
# The gate keys off THESE kinds only — never parenttask — so old unordered epics whose children
# carry just a parenttask link stay claimable exactly as before (the migration guard).
PREDECESSOR_RELATION_KINDS = ("follows", "blocked")

# advance: to -> (откуда, куда)
AGENT_ADVANCE = {"build": ("Design", "Build"), "review": ("Build", "Review")}

# `_require_mine`'s ownerless-card clause (#705, widened by #734): the sentence that names the
# REAL exit, keyed by stage. Per-stage and not one shared text, because ONE text is measurably
# FALSE in at least one stage — see the sweep recorded in `_require_mine`. A stage absent from
# this map keeps the bare "claim it first": that is QUEUE only, and it is absent BY DESIGN,
# because there the advice is simply correct (measured: claim on an ownerless Queue card
# succeeds and moves it to Design). Design/Build carry #705's wording byte for byte.
#
# Note where the split falls. The shared prefix stops at "claim() works only from Queue"; the
# NEXT clause — "so no call of yours can make it yours" — lives in the tails, because it is FALSE
# in Review and this card's own second pass caught it there. Measured: review_task(needs_work)
# then claim(), two of the agent's own calls, leave an ownerless Review card in Design assigned to
# me. Leaving that clause shared would have had the Review entry contradict its own first half.
_ACTIVE_OWNERLESS_EXIT = (
    ", so no call of yours can make it yours (advance, call_human, return_task and decompose all "
    "refuse it identically — don't work down the list). Only a human can move it back into the "
    "pipeline: say so in your report"
)
_OWNERLESS_EXITS: dict[str, str] = {
    "Design": _ACTIVE_OWNERLESS_EXIT,
    "Build": _ACTIVE_OWNERLESS_EXIT,
    # Backlog is the REACHABLE one: return_task parks a card here AND clears the assignee, so an
    # ownerless Backlog card is the everyday outcome of a tool an agent calls itself — not the
    # rare hand-placement the Design/Build branch guards. So the exit says "this is normal",
    # not "this is broken": there is nothing to report and nothing to fix.
    "Backlog": (
        ", so no call of yours can make it yours. That is not damage and not yours to fix: "
        "Backlog is the human's triage zone, and return_task parks a card here unassigned BY "
        "DESIGN (so do decompose on a parent and file_task) — an ownerless card in Backlog is the "
        "everyday state, not a stranding. A human triages it into Queue; whether it is claimable "
        "from THERE is the ordinary queue's business, not a promise this refusal can make. Leave "
        "it and take the next task"
    ),
    # ICEBOX HAS NO ENTRY, and its absence is Done's, not Queue's (#1640). This card's first
    # pass DID write one here, on the argument that `return_task`/`decompose` stayed ungated on
    # Icebox so an agent could still meet an ownerless card there. Review disproved the premise
    # and the gate went into `_find_task` beside Done's, after which the row became DEAD DATA by
    # the same mechanism #662 recorded one paragraph up — measured on this tree with an
    # ownerless Icebox card and all seven ownership-gated tools: every one answers with the
    # frozen guard, none reaches `_require_mine` at all. Deleting it rather than leaving it is
    # the honesty #662 chose for its own dead rows: a stale row in a table a reader trusts is
    # worse than no row, and nothing is lost from the message, since the frozen refusal says
    # what this row said and says it for an OWNED card too, which this row never covered.
    # Review is the ONE non-Queue stage an agent can move this card out of (measured), so the
    # shared "only a human can move it back" would be a LIE here — and the reviewer's own tool
    # never needs ownership in the first place. Reached by `advance` only: call_human,
    # return_task and decompose each refuse from Review with their own stage gate, first.
    "Review": (
        " — but you do not need to OWN a card to review it: review_task(task_id, "
        "verdict='approve'|'needs_work', report=…) takes no ownership. And this is the one stage "
        "where a call of yours CAN make the card yours, in two steps rather than one: needs_work "
        "sends an ownerless card to Queue, and claim() takes it from there — subject to the "
        "ordinary Queue gates, which still refuse an `epic` container, a card with an unfinished "
        "predecessor, and any claim at a full WIP limit. Review is the only non-Queue stage an "
        "agent can move this card out of, so don't report it as stuck"
    ),
    # Your Call is the ANOMALOUS one: call_human KEEPS the assignee, so a parked card is not
    # supposed to be ownerless at all. Nothing for the agent to do — but unlike Backlog it is
    # worth reporting, because the human's answer moves the card back to Design/Build, where an
    # ownerless card is exactly the #705 dead end and next_task offers it to nobody (measured).
    "Your Call": (
        ", so no call of yours can make it yours. Only a human moves a card out of Your Call, so "
        "there is nothing here for you to do — "
        "but DO report it: call_human KEEPS the assignee, so a parked card is not supposed to be "
        "ownerless, and when the human answers and moves this one back to Design/Build it will "
        "still have no owner, where next_task offers it to nobody"
    ),
    # DONE HAS NO ENTRY, and its absence means something different from Queue's (#662). Queue is
    # absent because the bare advice is CORRECT there. Done is absent because this map can no
    # longer be consulted from Done at all: the shared guard in `_find_task` refuses before
    # `_require_mine` is ever reached, so the row #734 wrote here became DEAD DATA — measured,
    # every one of `_require_mine`'s four callers takes the default `allow_done=False`, and the
    # four that pass True never ask about ownership. Deleting it is the same honesty the two
    # personal gates got: a stale row in a table a reader trusts is worse than no row. Nothing is
    # lost from the message — the shared refusal says what this row said, and says it for EVERY
    # tool and for an OWNED card too, which this row never covered.
}

# #742: what a card owned by SOMEBODY ELSE gets outside Queue. ONE text rather than a per-stage
# map like `_OWNERLESS_EXITS` above, and the asymmetry is measured rather than lazy: for an
# OWNERLESS card the true exit really does differ by stage (an agent can walk one out of Review
# and out of none of the others), while for a card with an OWNER the stage axis COLLAPSES —
# `claim` refuses from all eight (#1640 added Icebox), and "leave it to its owner" is the same
# right action in each.
#
# This REVERSES a decision #705 and #734 each took deliberately — keep the bare message here,
# since "not assigned to you" is already an accurate diagnosis and "leave it alone" an unchanged
# right action. The reversal is the HUMAN's, on their `[ответ человека]` answer to VMCP-202 (742)
# (landed 2026-08-10), taken against a recommendation its own author called WEAK and with the
# price named out loud: two pins on STRING EQUALITY had to be rewritten for it. What moved the
# balance is exactly two measurements neither earlier card had, and no third argument:
# (1) `claim` refuses a card that HAS an owner from QUEUE TOO (`already taken (…) — grab the next
# one via next_task`), so "claim it first" is unfollowable for a foreign card in all eight stages
# rather than in the seven outside Queue; and (2) the refusal an agent gets when it follows the
# advice outside Queue — `task is in '<stage>', you can only claim from Queue` — says nothing
# about the owner at all. Correct, and not about the thing the agent needs to know.
#
# Queue is excluded, like it is from `_OWNERLESS_EXITS`, but NOT for that map's reason. There the
# bare advice is CORRECT (claim on an ownerless Queue card succeeds); here it is not correct at
# all — it is merely already ANSWERED, because claim's own Queue refusal names the owner and the
# next move. A second sentence would only repeat it. Done is excluded vacuously: `_find_task`'s
# human-only guard (#662) refuses before `_require_mine` runs, so no foreign card in Done ever
# reaches this text — measured on this tree, all five ownership-gated forms and `claim` answer
# with the Done rule instead, which is also why the sweep this card is titled from finds the
# foreign-card message in SIX stages and not seven. ICEBOX joined Done in that exclusion at
# #1640, by the same guard and the same vacuum, so the SIX is unchanged while the total it is
# measured against is now EIGHT — the count to re-derive if a stage is ever added again.
#
# The last sentence is load-bearing and is NOT padding: #734 deliberately refused to promise a
# card will not become claimable, because a human can clear the assignee. This text stops at
# "a human's call" for that reason and must keep doing so.
_OTHER_OWNER_EXIT = (
    " — and claim() would refuse here anyway: it works only from Queue, and this card already "
    "has an owner, so claim() refuses it from Queue too (`already taken`). Leave it to its owner "
    "and take the next task; a finding about it goes in file_task(…, related_task_id=…). Whether "
    "it ever becomes claimable is a human's call, not a promise this refusal can make."
)

# human-only Done, said ONCE (#662). Before this, six tools refused a Done card and each said so
# in its own words — four by deriving it from their own starting stage, two (`return_task` #626,
# `decompose` #649) from a personal `if stage == "Done"`. The class stayed open because nothing
# expressed the RULE: the next mutating tool that moved a card without checking its stage would
# reopen it and no test would notice. The guard now sits in `_find_task`, so this is what all six
# say, and the collapse is the price the card's own analysis named. Two things it therefore has
# to carry, because the six carried them between them and the pins on #626/#649 assert them as
# TOKENS rather than as whole strings — by construction, not by luck: the word DONE (the
# transition is the human's in BOTH directions, which is what makes it a rule and not a
# preference) and `file_task`, the door that does work from here. Anything a Done card revealed
# is NEW work; this card is finished and not the agent's to reopen.
_DONE_IS_THE_HUMANS = (
    "task {task_id} is in Done, and a card only gets there because a HUMAN put it there — the "
    "Done transition is human-only in BOTH directions, so no agent tool moves it, changes it or "
    "takes it back out. That is one rule rather than a habit of each tool: whatever you were "
    "about to call, the answer from Done is the same. Work that a Done card revealed is NEW "
    "work, not a change to this one — file_task(…, related_task_id={task_id}) puts it in front "
    "of a human for triage. If this card itself is wrong, only a human can move it back. "
    "Reading it is still open: get_task, comment, attach_file and download_attachment all work "
    "on an accepted card"
)

# --- вложения: временные файлы (download_attachment, #139) ---
# Скачанные вложения кладём в один выделенный temp-каталог, КАЖДОЕ скачивание — в свой
# mkdtemp-подкаталог, чтобы файл сохранял ТОЧНОЕ исходное имя (рендерер образов у агента
# ключуется на расширении .png/.jpg), и два файла с одним именем из разных задач не затирали
# друг друга. Никто не удаляет файл сразу после записи — агент читает его Read-ом секундами
# позже, — поэтому чистка это best-effort TTL-подметание на КАЖДОМ вызове: подкаталоги старше
# _ATTACHMENT_TTL сносятся (только что записанный всегда свежий, под нож не попадёт). Так течь
# ограничена ~одним TTL скачиваний БЕЗ фонового потока и БЕЗ atexit (который на долгоживущем
# stdio-сервере не срабатывает до его остановки). Размер режем ДО скачивания по метаданным.
# #1640 second pass. The freezer's counterpart to `_DONE_IS_THE_HUMANS`, and it exists because
# the card's FIRST pass argued it did not need to — "a card in Icebox is ownerless by
# definition, so reaching a mutating tool takes a human hand-assigning an agent to it, which
# means do this one after all". An independent review built the state and the premise is false:
# dragging a card in Vikunja does not touch its assignees (which is the whole reason #626 was
# needed for Done), so the ORDINARY lifecycle parks an ASSIGNED card here by two routes, and
# both mean the opposite of "do this one after all" — a human freezing a card mid-Build because
# the agent is burning tokens on legacy, and a human answering a `call_human` question with
# "freeze it" (call_human KEEPS the assignee, and Icebox is one drag from Your Call).
# Measured from that state, with Done as the control in the same round: `decompose` SUCCEEDED,
# parking the parent in Backlog and TWO CHILDREN IN QUEUE, which `next_task` then offered on the
# very next call — verbatim the #649 shape this repo already closed once; `return_task`
# SUCCEEDED, reverting the freeze to Backlog + `blocked`; `transfer_task` SUCCEEDED, walking the
# frozen card off the board into a neighbour's Backlog. Done refused all three.
# It lives at the `_find_task` chokepoint rather than in those three tools for #662's reason,
# which the review's third door demonstrates: `transfer_task` was on nobody's list, and the
# next mutating tool would not be either.
_ICEBOX_IS_FROZEN = (
    "task {task_id} is in Icebox — the freezer, and moving a card OUT of it is the human's "
    "call, not an agent's. A card lands here when somebody decides the work is legacy or too "
    "minor to be worth doing; it can still be READ, COMMENTED on and have files attached, so "
    "if you have found something worth saying about it, say it there. What no tool of yours "
    "does is take it out: not return_task, not decompose (which would put children in Queue "
    "and hand frozen work straight back to the fleet), not transfer_task. If you believe this "
    "one genuinely must be done, say so in your report and leave the card where it is — a "
    "human drags it back to Backlog or Queue, and then it is ordinary work again."
)


_ATTACHMENT_ROOT = os.path.join(tempfile.gettempdir(), "vikunja-mcp-attachments")
_ATTACHMENT_TTL = 3600  # сек: подкаталоги скачиваний старше этого best-effort сносятся
_MAX_ATTACHMENT_BYTES = 25 * 1024 * 1024  # 25 МБ: щедро для скринов/доков, отсекает рантаймы
# Байтовый бюджет имени temp-файла: open() кидает OSError ("File name too long") на именах
# ~255+ байт, а сервер-контролируемое имя вложения может быть любой длины -> режем до этого.
_MAX_ATTACHMENT_NAME_BYTES = 200

# #657: what `advance` says when a required report field is unusable. The old refusals ran
# the value through `(x or "").strip()`, which COLLAPSES two different states into one — an
# argument that never arrived (None) and one that arrived blank ("") — and then named BOTH
# fields whatever was actually wrong. Two facts are recoverable here and both were thrown
# away: WHICH field is unusable, and HOW it arrived. Neither proves anything about the cause
# (an agent who simply omits the argument also produces None), but "did not arrive" is the
# reading the old text made unavailable, and it is the one that changes what an agent does
# next. The card that filed this retried the identical ~7 KB call THREE times against a
# message that only ever said "you owe a report".
# `_ARG_STATE_ABSENT` says only what this tool can SEE. An earlier spelling opened with "not
# passed at all", which is literally false for the one client that sends `worklog: null`
# EXPLICITLY: that key IS passed, and (measured) arrives indistinguishable from an omitted one.
# The state is null; the CAUSE is what _LOST_ARGUMENT_HINT refuses to guess.
_ARG_STATE_ABSENT = "arrived as null, not as a string"
_ARG_STATE_BLANK = "passed, but empty or whitespace-only"
# Measured 2026-08-02 on #657, at this repo's mcp 2.0.0 / Python 3.12.13, on ONE machine over
# stdio: Workflow.advance itself carries a 1 MiB worklog byte-exact through FakeAPI, and the
# real MCPServer over the real stdio transport delivers a 4 MiB argument byte-exact — an
# independent re-measure using a raw JSON-RPC client and the REAL Workflow got the same result
# to 8 MiB; the contents tried in THAT re-measure all cross intact (Cyrillic, NUL, CRLF, one
# 8 MiB line with no newline at all), and one that does NOT is named in the fourth limit
# below — so read this as a list, never as "no content fails". A kilobyte-sized report is
# nowhere near anything measured to fail
# below this line, and an identical retry does not address a report that arrived as null.
# FOUR limits on that, the first three of which an earlier draft of this comment overstated:
#  * These are ceilings that were TESTED on one transport, not proof that none exists above.
#  * "advance behaves like review_task" holds only for a PRESENT, non-empty argument. For a
#    MISSING one they are opposite, and that opposition is the whole point below.
#  * There is NO THRESHOLD, because there is no size mechanism — VMCP-279 (938) closed this
#    bullet, which until then read "never reproduced … WHICH KIND of failure was never
#    established". It is reproduced now, and the cause is in the CALLER's emission: in a
#    tag-structured tool call an opening tag written without its namespace prefix is not
#    recognised as a parameter, so the value never becomes a JSON key and the tool sees None.
#    The discriminator that settles it holds POSITION and LENGTH constant and varies only the
#    tag: the identical call (long `worklog` first, a 40-space `evidence` second) answers
#    `evidence — arrived as null` with the tag malformed and `evidence — passed, but empty or
#    whitespace-only` with it correct. The control that makes the sentinel readable at all:
#    the same 40 spaces sent alone arrive as BLANK, so whitespace is not what is being eaten.
#    Order was closed separately and below this line — ten permutations over the real stdio
#    boundary, all byte-exact, pinned by test_argument_ORDER_does_not_change_what_arrives.
#  * Read that as a cause, NOT as a frequency. The correlation with a long preceding value is
#    an inference from three of this repo's own cards plus two landed artifacts (a literal
#    `</root_cause>` inside 862's root_cause VALUE, a literal `</text>` closing 938's own
#    `[состояние]` comment) — no rate was measured, and no prior card's call was re-run, so
#    what is shown is that this cause PRODUCES their symptom and that their stated predicate
#    (order) does not. "Three refusals then a success" still proves nothing about retrying:
#    that success came from replacing the ~7 KB worklog with `worklog="probe"`, not from
#    repeating the call. What DID change is that a re-issue now addresses the cause.
#  * "No content fails" is the one an earlier draft actually got WRONG rather than merely
#    overstated. Constructed on this probe server, controls in the SAME run: Cyrillic, NUL and
#    CRLF cross byte-exact, and a LONE SURROGATE (a truncated astral pair) does not — the call
#    raises client-side and never arrives. Precisely: what refuses it is pydantic-core's JSON
#    serializer, not UTF-8 as such — stdlib `json.dumps` escapes the surrogate and encodes
#    fine, measured — and it is loud at SESSION scope: `call_tool` itself surfaces a bare
#    CancelledError and the real cause appears on teardown, taking the stdio session with it.
#    Loud either way, so it is NOT this card's silent symptom — but it is a content that
#    fails, which is what the sentence above had denied.
_LOST_ARGUMENT_HINT = (
    "If you DID pass a long value and still read this, it did not reach this tool — and since "
    "#938 the cause is MEASURED rather than open, so the advice has CHANGED: RE-ISSUE THE "
    "CALL. What dropped the value was your own emission, not its size and not its position, "
    "so the content you already wrote is fine and re-sending it is the fix rather than a "
    "gamble. DO NOT GO HUNTING A TYPO IN THE PARAMETER NAME — that used to be the "
    "first thing to check, and of the four causes this text used to list it is the one that "
    "reading this now RULES OUT: an "
    "unknown argument is now refused AT THE BOUNDARY, by name ('wroklog … Extra inputs are "
    "not permitted'), before this tool runs at all, so a misspelling cannot reach this text "
    "(measured over real stdio). Caveat, because that gate is best-effort: if it could not be "
    "installed, the old silent drop is back and a typo IS possible again. The server TRIES to "
    "say so in one line on stderr at startup — only tries: with fd 2 closed it says nothing at "
    "all (measured), and that is the SERVER's startup stream, which no tool here shows you. So "
    "read it as a residual risk you cannot check from inside a call, not as a signal to go "
    "looking for. WHERE IT ACTUALLY GOES: measured (#657), this server takes a 4 MiB argument "
    "byte-exact over its own stdio transport, and argument ORDER changes nothing here either "
    "— ten permutations across the real boundary, every one byte-exact (#938) — so a "
    "kilobyte-sized report is nowhere near any limit here, and a value you did pass that "
    "arrives as null was dropped ABOVE this server. In a tag-structured tool call, a parameter "
    "whose OPENING TAG is malformed (namespace prefix dropped) is not recognised as a "
    "parameter at all, so it never becomes a JSON key. Held at one position and one length, "
    "varying only that tag, the same call flips between this refusal and a delivered value "
    "(#938). It CORRELATES with a long PRECEDING value — the parameter written just after a "
    "long block is the one whose tag gets malformed — which is why three cards read this as "
    "'the trailing argument is dropped' and reordered the call. Reordering is not the fix; "
    "writing the tag correctly is. (Null still does not name a cause, but the list is now "
    "THREE and no longer four: a key dropped in transit, an argument you never passed and an "
    "EXPLICIT null you did pass all arrive here as null — measured; the first two are one and "
    "the same on the wire, and a malformed tag is how the first one actually happens.) "
    "Still available if re-issuing keeps failing: advance with a SHORT value, then post the "
    "full text as separate comment() calls marked [worklog]."
)


def _unusable_report_fields(*fields: tuple[str, str | None]) -> list[tuple[str, str]]:
    """For each (name, value) that cannot serve as a report field, return (name, state) where
    state says HOW it is unusable — see _ARG_STATE_*.

    An empty list means no field is BLANK BY THIS TEST, which is narrower than "usable" (the
    word this docstring used first). The test is `str.strip()`, i.e. zero NON-whitespace
    characters: measured, 100 NBSP are refused because `\\xa0` is whitespace, while 50 ZWSP —
    or a word joiner, or a BOM — are NOT whitespace, so they pass here and advance a card whose
    report is empty to every reader. Deliberate rather than missed: widening the test to
    "visible characters" is a guess about an open set of code points, and the states this card
    is about (null vs blank) do not depend on it."""
    return [
        (name, _ARG_STATE_ABSENT if value is None else _ARG_STATE_BLANK)
        for name, value in fields
        if not (value or "").strip()
    ]


def _sweep_old_attachments(now: float) -> None:
    """Best-effort: снести подкаталоги скачиваний старше _ATTACHMENT_TTL. Полностью
    защищено — чистка временных файлов не имеет права уронить вызов тулзы."""
    try:
        entries = os.listdir(_ATTACHMENT_ROOT)
    except OSError:
        return
    for entry in entries:
        path = os.path.join(_ATTACHMENT_ROOT, entry)
        try:
            if now - os.path.getmtime(path) > _ATTACHMENT_TTL:
                shutil.rmtree(path, ignore_errors=True)
        except OSError:
            pass


def _truncate_preserving_ext(name: str, max_bytes: int) -> str:
    """Урезать имя файла до max_bytes БАЙТ (не символов), сохранив расширение и НЕ разрубив
    многобайтовый символ пополам. splitext даёт stem+ext; если само расширение уже >= max_bytes
    — это не расширение, а длинный «хвост» с точкой, дропаем его. Иначе бюджет = max_bytes минус
    длина ext в байтах, режем utf-8 stem'а по границе байта, decode(errors='ignore') сносит
    повисший обрубок символа. Кодируем surrogatepass (имя могло прийти с суррогатами), декодим
    ignore (обрубок символа/битый суррогат просто исчезает)."""
    stem, ext = os.path.splitext(name)
    ext_bytes = ext.encode("utf-8", "surrogatepass")
    if len(ext_bytes) >= max_bytes:            # «расширение» само не влезает -> это не расширение
        ext, ext_bytes = "", b""
    budget = max_bytes - len(ext_bytes)
    stem_bytes = stem.encode("utf-8", "surrogatepass")[:budget]
    return stem_bytes.decode("utf-8", "ignore") + ext


def _safe_attachment_name(name: str, fallback: str) -> str:
    """Имя файла от сервера НЕ должно ни уводить запись за пределы temp-каталога (path traversal),
    ни уронить сам open(). Оставляем только basename (нормализовав и обратные слэши — на POSIX
    os.path.basename их не режет); вырезаем управляющие байты (ord < 0x20 или == 0x7F: NUL + C0 +
    DEL — иначе open() кидает ValueError на NUL); пустое или всё из точек ('', '.', '..') ->
    fallback (перепроверяем ПОСЛЕ вырезания: "\\x00" схлопывается в пустоту); режем до
    _MAX_ATTACHMENT_NAME_BYTES байт с сохранением расширения (иначе open() кидает OSError
    'File name too long' на ~255+ байтах). Общий для download_attachment и attach_file."""
    base = os.path.basename((name or "").replace("\\", "/").strip().rstrip("/"))
    base = "".join(ch for ch in base if ord(ch) >= 0x20 and ord(ch) != 0x7F)
    if not base or set(base) <= {"."}:
        return fallback
    return _truncate_preserving_ext(base, _MAX_ATTACHMENT_NAME_BYTES)


def _write_attachment_to_temp(name: str, data: bytes, fallback: str) -> str:
    """Записать байты вложения во СВЕЖИЙ per-download подкаталог под _ATTACHMENT_ROOT,
    сохранив исходное имя, и вернуть путь. Попутно best-effort подметает старые скачивания."""
    os.makedirs(_ATTACHMENT_ROOT, exist_ok=True)
    _sweep_old_attachments(time.time())
    dest_dir = tempfile.mkdtemp(dir=_ATTACHMENT_ROOT)
    path = os.path.join(dest_dir, _safe_attachment_name(name, fallback))
    with open(path, "wb") as fh:
        fh.write(data)
    return path


def _human_size(n: int, language: str = DEFAULT_LANGUAGE) -> str:
    """Человекочитаемый размер для журнального коммента [attach] (#184): человек в ленте читает
    «1.4 MB», а не 1468006. Кап вложений — 25 MB, поэтому MB — верхняя единица.

    THE UNITS ARE CARD TEXT, so since #1165 they come from `cardtext` like every other body the
    product authors, and they follow the project's `language`. In the DEFAULT language they are
    ASCII and must stay so (#1164) — gated by tests/unit/test_card_text_is_ascii.py, and
    specifically by its RUNTIME assert, since that file's source scan cannot follow a value
    across a function boundary and so does not see this one. `language` defaults rather than
    being required because this is a module-level helper with one caller: the default keeps a
    hand-written call honest instead of making it a TypeError mid-transition."""
    if n < 1024:
        return card_text(language, "size_bytes", size=n)
    if n < 1024 * 1024:
        return card_text(language, "size_kilobytes", size=f"{n / 1024:.1f}")
    return card_text(language, "size_megabytes", size=f"{n / (1024 * 1024):.1f}")


def _stderr_note_best_effort(prefix: str, exc: Exception) -> None:
    """One guarded line on STDERR for a swallowed best-effort failure — the #134/#135 contract
    factored out for reuse: never stdout (a stray byte corrupts the MCP stdio protocol), the
    exception CLASS is formatted unconditionally, a str(exc) that itself raises degrades to
    '<unprintable>' instead of escaping (the diagnostic survives the pathological case), and
    the print is wrapped so nothing on this logging path can ever propagate into the caller's
    (already succeeded) result."""
    try:
        detail = str(exc)
    except Exception:
        detail = "<unprintable>"
    try:
        print(f"{prefix}: {exc.__class__.__name__}: {detail}", file=sys.stderr)
    except Exception:
        pass


class WorkflowError(Exception):
    """The message is shown to the agent as the tool result."""


class Workflow:
    def __init__(
        self, api: Any, project_id: int, enforce_single_wip: bool = False,
        notifier: WebhookNotifier | None = None, wip_limit: int | None = None,
        require_review_independence: bool = False, language: str = DEFAULT_LANGUAGE,
        siblings: dict[str, int] | None = None,
    ):
        # #992: refuse a non-int HERE. Passed a Config (the measured mistake — `Workflow(api,
        # cfg)` instead of `Workflow(api, cfg.project_id)`), the object used to be stored
        # silently and then interpolated into a URL, so the first sign of it was
        # `VikunjaError: Vikunja API 404: {"message":"Not Found"}` two layers down — a message
        # that points at a missing project or a token without access, never at this line.
        #
        # `bool` is excluded EXPLICITLY because `isinstance(True, int)` is True in Python: a
        # bare isinstance check would pass a bool straight through to the same 404 this guard
        # exists to remove. The VALUE is never shown — a Config carries the API token, a secret
        # of the same class as `.vikunja-mcp.env`, and exception text reaches stderr, logs,
        # worklogs and tracker comments. TypeError rather than WorkflowError on purpose: every
        # production site passes `cfg.project_id`, which `load_config` has already put through
        # `int()`, so this cannot reach the stdio server — while `_tool` would have turned a
        # WorkflowError into an `{"error": ...}` result, dressing a programming bug up as a
        # gate refusing politely. See tests/unit/test_workflow_project_id_guard.py.
        if isinstance(project_id, bool) or not isinstance(project_id, int):
            raise TypeError(
                f"project_id must be an int, got {type(project_id).__name__}. Pass "
                f"cfg.project_id, not the Config itself. The value is deliberately not shown "
                f"here: a Config carries the API token. Without this check the wrong type "
                f"reaches the request URL and surfaces later as 'Vikunja API 404: Not Found', "
                f"which reads as a missing project rather than as a bad argument."
            )
        self.api = api
        self.project_id = project_id
        # optional WIP gate: when true, claim() refuses a new task while you already
        # have an active one. Off by default -> the gate does zero extra work.
        self.enforce_single_wip = enforce_single_wip
        # optional Your-Call webhook ping (#252): built from VIKUNJA_NOTIFY_WEBHOOK by the
        # server; None (default, URL unset) -> call_human behaves bit-for-bit as before.
        # Called strictly best-effort — see call_human.
        self.notifier = notifier
        # parallel drain: how many tasks may be CLAIMED into Design/Build. A gate on that one
        # transition, not an invariant — the active count legitimately goes over it (#529). None
        # means the repo toml set no wip_limit — NOT "no gate": the fallback is the legacy flag (1)
        # or DEFAULT_WIP_LIMIT. See _effective_wip_limit for the precedence.
        self.wip_limit = wip_limit
        # committed team policy (#37): when true, review_task refuses a verdict from a caller
        # listed in the card's own assignees. DEFAULT FALSE and inert — see review_task's gate
        # and config.Config for why "no authorship check" is the CONDITION OF OPERATION in a
        # solo setup rather than a hole in one. Off, review_task does not even resolve `me()`.
        self.require_review_independence = require_review_independence
        # committed team policy (#1165), repo toml ONLY like the two flags above: which language
        # this project's cards are written in. It governs the prose THIS tool authors (every
        # `card_text` call below) and, through next_task's payload plus a SKILL.md rule, the
        # spec/worklog/review report the AGENT authors — which is the bulk of a card's text and
        # is the half no code here can reach. It NEVER governs a marker — and for TWO of the twelve
        # that is not style but mechanism: next_task's offering branch is the only place in this
        # package that reads comment text, and it does so with startswith("[worklog]") and
        # startswith("[review]"), so a per-language spelling on either drops every card written
        # under the other setting out of the review offering, silently. See cardtext.py.
        self.language = language
        # committed team policy (#1179), repo toml ONLY like the flags above: {name: project_id}
        # for the OTHER projects this repo may hand work to (handoff / transfer_task). NOT a
        # security boundary — the scoped token decides what a cross-project write may touch, and
        # `file_task`'s free-form project_id is deliberately left un-narrowed by it. What it buys
        # is that the agent can LEARN a neighbour exists and address it by name; before it, an
        # agent in dogiators-front had no way to know a dogiators-backend was there at all.
        self.siblings = dict(siblings or {})
        self._me_cache: dict | None = None
        self._view_cache: dict | None = None
        self._buckets_cache: dict[str, dict] | None = None

    # --- кэшируемые справочники ---
    def _me(self) -> dict:
        if self._me_cache is None:
            self._me_cache = self.api.me()
        return self._me_cache

    def _view(self) -> dict:
        if self._view_cache is None:
            self._view_cache = self.api.kanban_view(self.project_id)
        return self._view_cache

    def _bucket(self, title: str) -> dict:
        if self._buckets_cache is None:
            found = self.api.buckets(self.project_id, self._view()["id"])
            self._buckets_cache = {b["title"]: b for b in found}
            # REQUIRED_STAGES, not STAGES (#1640): Icebox is optional, so an un-migrated board
            # must keep answering every OTHER call. Widening this back to STAGES takes the whole
            # fleet down at the next `stable` resolve — see the constant's own comment.
            missing = [s for s in REQUIRED_STAGES if s not in self._buckets_cache]
            if missing:
                raise WorkflowError(
                    f"the project board has no columns {missing} — run `vikunja-mcp setup`"
                )
        try:
            return self._buckets_cache[title]
        except KeyError:
            # Reachable ONLY for an optional stage — every required one was just checked. A bare
            # KeyError here would not be a refusal but a CRASH: `server._tool` converts
            # WorkflowError/ConfigError/VikunjaError/httpx.HTTPError and nothing else, so it
            # would escape the decorator and take the stdio server down mid-session.
            raise WorkflowError(
                f"this project's board has no '{title}' column — run `vikunja-mcp setup` to "
                f"add it (it is the one canonical column a board may lack, so nothing else on "
                f"this board is wrong). Nothing was changed."
            ) from None

    # --- поиск и проверки ---
    def _board(self, require_titles: set[str] | None = None) -> list[dict]:
        # require_titles is forwarded to view_tasks: None (default) = full exhaustive board
        # (for _find_task/claim which must see every bucket incl. Done); next_task passes
        # NEXT_TASK_STAGES to skip exhaustively paging the unbounded Done (#43 latency fix).
        return self.api.view_tasks(
            self.project_id, self._view()["id"], require_titles=require_titles
        )

    def _my_active_tasks(self, board: list[dict] | None = None) -> list[tuple[str, dict]]:
        """(stage, task) for tasks in an ACTIVE stage (Design/Build) assigned to the
        caller — the 'one task at a time' set. Shared by next_task's resume branch and
        claim's optional WIP gate. Pass a pre-fetched board (the raw _board() list) to
        skip a second fetch; a stuck claim still sitting in Queue is deliberately NOT
        active (finishing it isn't starting a second task)."""
        raw = self._board() if board is None else board
        by_stage = {b["title"]: (b.get("tasks") or []) for b in raw}
        my_id = self._me()["id"]
        return [
            (stage, t)
            for stage in ACTIVE_STAGES
            for t in by_stage.get(stage, [])
            if my_id in self._assignee_ids(t)
        ]

    def liveness_board(self) -> list[dict]:
        """The one board read that covers every set `workspace --gc` needs.

        Review finding (Important 4): active_task_ids/review_task_ids used to each call _board
        separately — two exhaustive-adjacent fetches per gc sweep, on a path that runs on
        EVERY orchestrator tick, more often than next_task's own #43-fixed single fetch. Pass
        this board's result into both accessors (their `board` param) so one call to
        view_tasks serves the whole sweep, same discipline as next_task's raw/resolve_full.

        "Your Call" is in the required titles for `parked_task_ids` (VMCP-68) — NOT for liveness:
        a parked card's tree is dead by design, and that is the whole point. It has to be
        EXHAUSTIVE and not just "whatever the first page happened to carry", because a parked id
        that pagination truncated away reads as not-parked, i.e. gc grades a routine refusal as
        an alarm — the exact never-empty-signal failure this set exists to fix. The cost is one
        extra page fetch only on a board whose Your Call is itself full (page_size cards a human
        has yet to answer), unlike the unbounded Done/Backlog #43 deliberately leaves out."""
        return self._board(require_titles=frozenset({*ACTIVE_STAGES, "Review", "Your Call"}))

    def active_task_ids(self, board: list[dict] | None = None) -> list[int]:
        """Ids of tasks in an ACTIVE stage (Design/Build) assigned to me — the live BUILD set.

        Public on purpose: `vikunja-mcp workspace --gc` needs it to tell a crashed agent's
        orphaned worktree from a live one, and that boundary deserves a real interface rather
        than a CLI reaching into _my_active_tasks. Pass a pre-fetched board (`liveness_board()`)
        to share one fetch with `review_task_ids`; omit it to fetch on its own."""
        raw = self.liveness_board() if board is None else board
        return [t["id"] for _stage, t in self._my_active_tasks(raw)]

    def review_task_ids(self, board: list[dict] | None = None) -> list[int]:
        """Ids of every task sitting in Review — the live REVIEW set.

        Deliberately NOT filtered by assignee: a reviewer works on someone ELSE's card, so
        ownership would reap the tree out from under a running review. Pass a pre-fetched
        board (`liveness_board()`) to share one fetch with `active_task_ids`; omit it to fetch
        on its own."""
        raw = self.liveness_board() if board is None else board
        return [
            t["id"] for bucket in raw if bucket["title"] == "Review"
            for t in (bucket.get("tasks") or [])
        ]

    def parked_task_ids(self, board: list[dict] | None = None) -> list[int]:
        """Ids of every task parked in Your Call. NOT a liveness set — the opposite of one.

        `workspace --gc` reads it to GRADE its own report (VMCP-68), never to spare a tree: a
        dead build tree that still holds uncommitted or unpushed work is the routine, no-action
        state while its card waits for a human (call_human parks the card and keeps the assignee,
        so the human already has the signal, and it clears when they answer), and the very same
        refusal on a card that is anywhere else is work nobody is coming back for. One refusal,
        two meanings, and only the board can tell them apart.

        Deliberately NOT filtered by assignee, like review_task_ids: a `task-<id>` worktree only
        ever exists for a task we worked on, so ownership would buy no precision and cost a
        `_me()` fetch. Pass a pre-fetched board (`liveness_board()`) to share its one fetch."""
        raw = self.liveness_board() if board is None else board
        return [
            t["id"] for bucket in raw if bucket["title"] == "Your Call"
            for t in (bucket.get("tasks") or [])
        ]

    def _wip_limit_with_origin(self) -> tuple[int, str]:
        """The slot count AND the breadcrumb saying which knob produced it, resolved by ONE
        branch structure so the two can never disagree (tracker #517).

        Precedence: an explicit wip_limit is the truth; otherwise the legacy #38 flag means
        exactly 1; otherwise DEFAULT_WIP_LIMIT. Keeping both keys alive means an existing
        consumer that committed enforce_single_wip = true needs no edit.

        There is deliberately no "unlimited" (tracker #524): an unset key used to return None
        = no gate, which contradicted the rulebook's «unset ⇒ SERIAL drain» and let a pump
        claim the whole Queue. Returning int, not int | None, is what makes that structural —
        callers cannot reintroduce an unbounded branch by forgetting a None check.

        The origin string exists because the refusal message lost its breadcrumb when #38's
        `enforce_single_wip` stopped being the only knob: an agent hitting a surprising "WIP
        limit reached" could no longer tell whether a human committed the number, whether the
        legacy flag was still on, or whether nothing was configured at all — three different
        next actions, and the third is not even a toml edit. Computed HERE rather than in a
        sibling helper on purpose: a second copy of this if/elif is a lie waiting to happen,
        and a message that names the wrong knob is worse than one that names none."""
        if self.wip_limit is not None:
            return self.wip_limit, "the `wip_limit` key in the repo's .vikunja-mcp.toml"
        if self.enforce_single_wip:
            return 1, "`enforce_single_wip = true` in the repo's .vikunja-mcp.toml"
        return DEFAULT_WIP_LIMIT, (
            "the built-in default — the repo's .vikunja-mcp.toml sets no `wip_limit`"
        )

    def _effective_wip_limit(self) -> int:
        """How many active tasks this token may CLAIM its way into. ALWAYS a number — the gate is
        never off. It bounds the `claim` transition and NOT the active count, which legitimately
        exceeds it when a card re-enters Build past the gate (#529).
        Thin view over _wip_limit_with_origin, which owns the precedence."""
        return self._wip_limit_with_origin()[0]

    def _find_task(
        self, task_id: int, board: list[dict] | None = None, *,
        allow_done: bool = False, allow_icebox: bool = False,
    ) -> tuple[dict, str]:
        """Locate a task on the board and answer (task, stage) — and, unless the caller opts out,
        REFUSE a card in Done (#662).

        This is the one chokepoint every card-touching tool shares, which is why the human-only
        Done rule lives here now instead of in a per-tool `if stage == "Done"`. Before #662 it was
        an ENUMERATION: four tools happened to refuse because Done is not their starting stage
        (`claim` wants Queue, `advance` its own `from_stage`, `call_human` Design/Build,
        `review_task` Review) and two carried a personal gate (`return_task` #626, `decompose`
        #649). A sweep of all 12 registered tools was clean — but the next mutating tool that
        moves a card without checking its stage reopened the hole, and NO test asked the question,
        because every pin checked its own tool. FAIL-CLOSED replaces that: a new tool that says
        nothing refuses from Done by default, so a new author's omission becomes a loud refusal
        rather than a silent hole. What that does NOT buy is inexpressibility — this guard can
        still be deleted or opted out of; it is one place instead of six, not a law.

        `allow_done=True` is the READ paths, and they are the reason a bare guard here was
        rejected in #649: an accepted card must stay READABLE and COMMENTABLE. Four callers pass
        it — get_task, comment, attach_file, download_attachment — and that list is the fail-closed
        rule's price, paid in the other direction: the author of a new READING tool gets a
        surprising refusal until they opt in. That is the better of the two errors, not the
        absence of one.

        The cost was measured rather than waved through: six per-tool Done refusals collapse into
        the ONE message below, which is the "flattened prescriptive routing" #662's description
        held against the rejected `_refuse_if_done` helper. It is cheaper than it looked (the
        two dead gates go with it, so the landing is net NEGATIVE lines), but it is real, so the
        message has to carry what the six carried between them: that the transition is the
        human's in BOTH directions, and the door that does work. `advance(to='done')` is NOT in
        the collapse — it refuses before this method is ever called and keeps its own wording."""
        for bucket in (board if board is not None else self._board()):
            for task in bucket.get("tasks") or []:
                if task["id"] == task_id:
                    stage = bucket["title"]
                    if stage == "Done" and not allow_done:
                        raise WorkflowError(_DONE_IS_THE_HUMANS.format(task_id=task_id))
                    # #1640: a SECOND branch rather than a widened condition above, and the
                    # separation is load-bearing twice over. `test_done_is_human_only` reads the
                    # AST of that BoolOp, so folding Icebox into it would silently change what
                    # that pin measures; and the two stages refuse for different reasons, which
                    # a shared message could not say — Done is finished work, Icebox is work
                    # deliberately not started.
                    if stage == "Icebox" and not allow_icebox:
                        raise WorkflowError(_ICEBOX_IS_FROZEN.format(task_id=task_id))
                    return task, stage
        raise WorkflowError(f"task {task_id} not found on the board of project {self.project_id}")

    def _unfinished_predecessors(
        self, task_id: int, board: list[dict] | None = None,
        resolve_full: Callable[[], list[dict]] | None = None,
        foreign_boards: dict[int, dict[int, str] | None] | None = None,
    ) -> list[dict]:
        """Predecessors of `task_id` that are NOT yet ready (still below Review) and so must
        reach Review/Done before this task may be started. A predecessor is any task linked from
        this one by a `follows` (this follows P) or `blocked` (this blocked-by P) relation;
        parenttask is deliberately excluded, so an old epic whose children carry only a parenttask
        link yields [] and stays claimable (the migration guard). Each entry: {id, ref, title,
        stage}, deduped by id. A task with no follows/blocked relation returns [] without arming
        the gate. Pass a pre-fetched board (raw _board()) to reuse one snapshot for stages.

        resolve_full (#126): a memoised getter for the EXHAUSTIVE board, supplied by next_task,
        which resolves stages against its LIGHT board (require_titles=NEXT_TASK_STAGES — Backlog/
        Your Call/Done are not exhaustively paged, #43). On that light board a predecessor that is
        simply absent is NOT provably deleted: it may sit in an unpaged Backlog/Your Call/Done
        bucket. So before ruling "gone -> not a blocker" we consult resolve_full() — the same full
        board claim/advance read — and treat the predecessor as gone only if it is missing there
        too. resolve_full is memoised by the caller, so the full board is fetched AT MOST ONCE per
        next_task (a 1->2 view_tasks escalation, and only when a predecessor is genuinely off the
        light board — never per candidate); the common no-off-board-predecessor path never calls
        it, preserving the #43/#105 single fetch. claim/advance pass the full board and OMIT
        resolve_full, so their verdict is unchanged — this makes next_task agree with them by
        construction instead of by keeping three bucket-sets in sync by hand.

        foreign_boards (#1199) is the same idea one scope wider, for the NEIGHBOUR boards
        `_offboard_predecessor` reads. Passed in, the memo belongs to the CALLER and spans every
        candidate of one next_task; omitted, a fresh one lives for this call alone, which is what
        claim/advance want — they resolve a single card and have nothing to share. It matters
        because next_task calls this once per free-Queue candidate, so M cards parked behind one
        neighbour used to cost M EXHAUSTIVE reads of that neighbour's board (Done included, the
        very shape #43 removed from our own) on EVERY `vikunja-mcp claimable` poll, for the whole
        parked lifetime of the cards. The staleness surface does not widen in kind: this is the
        same within-one-read snapshot the per-candidate memo already accepted, just held for the
        length of a call that is READ-ONLY BY CONTRACT anyway."""
        base = self._board() if board is None else board
        stage_by_id = {
            t["id"]: (t, bucket["title"])
            for bucket in base for t in (bucket.get("tasks") or [])
        }
        full_stage_by_id: dict[int, tuple[dict, str]] | None = None
        # project_id -> {task_id: stage} for NEIGHBOUR boards, or None when that board could
        # not be read. Memoised for as long as its OWNER lives (#1199): next_task hands in one
        # dict for the whole call, so M gated candidates on one neighbour cost ONE board read
        # instead of M; claim/advance hand in nothing and get a per-call dict, which is all a
        # single-card verdict can use. The common case (no off-board predecessor) never
        # touches it either way.
        if foreign_boards is None:
            foreign_boards = {}
        related = self.api.get_task(task_id).get("related_tasks") or {}
        unfinished: list[dict] = []
        seen: set[int] = set()
        for kind in PREDECESSOR_RELATION_KINDS:
            for pred in related.get(kind) or []:
                pid = pred["id"]
                if pid in seen:
                    continue
                seen.add(pid)
                found = stage_by_id.get(pid)
                if found is None and resolve_full is not None:
                    # light-board absence is NOT deletion — disambiguate against the exhaustive
                    # board (fetched at most once via the memoised resolve_full) before ruling gone
                    if full_stage_by_id is None:
                        full_stage_by_id = {
                            t["id"]: (t, bucket["title"])
                            for bucket in resolve_full() for t in (bucket.get("tasks") or [])
                        }
                    found = full_stage_by_id.get(pid)
                advice: dict = {}
                if found is None:
                    # #1179: absence from OUR board is not deletion either. Vikunja relations are
                    # task-to-task and cross projects freely (measured: a task moved to another
                    # project kept its `blocked` link to one left behind), so a predecessor can
                    # simply live on a NEIGHBOUR's board. Before this, that case fell into the
                    # "gone" branch below and the gate released the card with its blocker
                    # untouched — silently, which is the whole defect.
                    offboard = self._offboard_predecessor(pid, foreign_boards)
                    if offboard is not None:
                        found, advice = (offboard[0], offboard[1]), offboard[2] or {}
                if found is None or found[1] in READY_STAGES:
                    continue  # genuinely gone (absent even from the full board) or already ready
                pred_task, pred_stage = found
                # #1640: a predecessor in the freezer RESOLVED fine — its stage is known — but
                # it is not finishable, because no agent tool moves a card out of Icebox (claim
                # takes only Queue). Marking it here is what drops the generic "finish that one
                # first" tail, which is the single action nobody can take. It rides the same
                # `finishable` key #1190 introduced, but NOT that card's `escape` key: those
                # escapes print under a lead that says the stage "could NOT be established",
                # which is false of this one and would make the refusal lie. Its clause is
                # `_predecessor_frozen`, kept separate for exactly that reason. The merge (not
                # an overwrite) leaves an unresolvable blocker's advice untouched.
                if pred_stage == "Icebox":
                    advice = {**advice, "frozen": True, "finishable": False}
                entry = {
                    "id": pid, "ref": self._ref(pred_task),
                    "title": pred_task["title"], "stage": pred_stage,
                }
                # #1190: `escape`/`finishable` are ABSENT whenever the stage resolved normally,
                # so every caller that renders a blocker keeps its old shape and its old prose on
                # the ordinary path; only an unresolvable one carries them. `finishable` defaults
                # to True where it is absent, which is what makes that silence mean "the generic
                # advice still applies". See _predecessor_advice.
                entry.update(advice)
                unfinished.append(entry)
        return unfinished

    def _foreign_stages(self, project_id: int) -> dict[int, str] | None:
        """{task_id: stage} for ANOTHER project's kanban board, or None when it cannot be read.

        Deliberately separate from _board/_view/_bucket and their caches, for the same reason
        _target_bucket is: those are pinned to self.project_id and feed every hot gate, while
        this is a rare coordination read. No cache -> no new staleness surface.

        None is "UNKNOWN", never "empty": 403 (the token was never shared this project), 404
        (no such project) and a board with no kanban view all land here, and the caller turns
        that into a BLOCKING verdict rather than a passing one. Any other status propagates —
        a 401 in particular must reach server._tool for the token-reload retry (#140)."""
        try:
            view = self.api.kanban_view(project_id)
            board = self.api.view_tasks(project_id, view["id"])
        except VikunjaError as exc:
            if exc.status in (403, 404):
                return None
            raise
        return {
            t["id"]: bucket["title"]
            for bucket in board for t in (bucket.get("tasks") or [])
        }

    def _offboard_predecessor(
        self, pid: int, foreign_boards: dict[int, dict[int, str] | None],
    ) -> tuple[dict, str, dict | None] | None:
        """Resolve a predecessor that is on no bucket of THIS project's board (#1179).

        Returns (task, stage, advice) when it still BLOCKS, or None when it does not. PAST the
        `project_id` check below, exactly ONE outcome stops the predecessor blocking, and it is
        the far board reporting a stage for it that is READY; an ambiguous one either returns a
        blocking triple with the reason spelled into the stage string or PROPAGATES (measured: a
        board read that 500s or 401s does, by `_foreign_stages`, and that path has no triple to
        return rather than a releasing one). The failure this closes is precisely an unknown being
        rendered as "gone": noisy beats quiet, and the card's human reads that string in the
        refusal. Scoped that way since #1198 in three other places — the dossier and this method's
        own tests key it on the same `project_id` check, CLAUDE.md by enumerating the three
        BLOCKING returns. TWO things sit outside that scope: the check itself, where the two
        fail-OPEN returns live, and, in the STEADY state, a predecessor whose whole PROJECT the
        token cannot read, which does not reach this method at all (last paragraph). That
        qualifier is doing work: lose access BETWEEN the successor's relation read and this one
        and the far card is still embedded, the guard IS reached and its 403 branch fires — the
        race `tests/unit/fakes.py` models deliberately, and whose LIVE reachability the dossier
        records as UNMEASURED. That 403 branch is also the one BLOCKING return answering BEFORE
        the check, and the ordering is measured rather than read off the code — holding the far
        card's `project_id` at OURS, the value that otherwise releases, and varying only the read:
        read OK -> claim ALLOWED, read 403 -> claim REFUSED.

        `advice` is None on the one branch that resolved a real stage; on each of the three
        that could not it carries two keys merged onto the blocker dict (#1190) —

          `escape`     a sentence naming what can get this card MOVING again. Only the
                       `done=True` action releases it outright; making the board readable turns
                       the unknown into a knowable stage (measured: the same card then refuses
                       with `Build (project N)`), and removing the relation drops the gate.
          `finishable` whether the generic "a predecessor is ready at Review or Done; finish
                       that one first" tail is still TRUE of this blocker.

        `finishable` is the whole reason the advice is per-branch rather than one sentence
        appended to all three, and it is measured on each. NO-BUCKET is finishable: that board
        READS, so moving the predecessor into Review releases the card and the generic tail is
        correct advice — the escape is additive there. UNREADABLE BOARD is not: `_foreign_stages`
        answers None whatever the far card's stage, so Review does NOT release it and only the
        `done` half of "Review or Done" works — `done` is read here BEFORE any board read.
        403-ON-THE-TASK is not either, and there no form of finishing works at all: `get_task`
        raises before `done` is looked at. SHARING the project does not release that card either
        — it makes the stage knowable and the refusal then names the far card's real stage, after
        which finishing it does work. The escape says so since the #1190 review; it used to read
        "Share its project with this token" flat, which is incomplete in the same way the generic
        tail was.

        Three cheap answers come before any board read. A 404 on the task itself means it was
        deleted between the successor's relation read and this one — a narrow race, since
        deleting a card takes its relation rows with it — and there "gone" is simply true.
        `done` is ready by definition. And a predecessor claiming OUR project id while being
        absent from our exhaustive board is self-contradictory, so it keeps the pre-#1179
        answer rather than inventing a blocking state out of a contradiction.

        TWO of the early returns are fail-OPEN, and together they are the whole asymmetry (#1198):
        `proj` not being an int, and `proj == self.project_id`. Only the second is in the
        enumeration above; the non-int one is not, and that is the pair, not a subset of those
        three. What they share is the `project_id` check — between them they are the whole of what
        this method lets through on the strength of that field ALONE — and WHY each releases is
        where they part (#1214): the non-int one IS an escape from an unknown, the our-project one
        an ANSWER to a contradiction. That second reading is the tree's rather than this
        paragraph's: the enumeration above carries it as one of the three cheap answers, and
        `docs/dossier/workflow.md` and the asymmetry test's own docstring both put it on #126. The
        second means the IDENTICAL physical situation — a task in project P, absent from P's board
        — is fail-CLOSED when P is a neighbour and fail-OPEN when P is ours. Deliberate and pinned
        with the neighbour case as its control: our exhaustive board is the same read
        claim/advance judge by, so absence from it is a contradiction rather than an unknown.

        AND THE FAIL-CLOSED GUARANTEE COVERS ONLY WHAT REACHES THIS METHOD (#1198). Measured on a
        live 2.3.0 with a two-reader control: when the token loses access to the neighbour
        PROJECT, the server strips the far card out of `related_tasks` altogether — the owner
        reads `{'blocked': [4]}` and the agent reads `{}` at the same moment — so
        `_unfinished_predecessors` iterates nothing and, in that steady state, this method is
        never called at all. That card is released with its blocker untouched. It is an ACCEPTED
        limit rather than an oversight: what the gate can read is this token's own side of a
        relation the server will not show it. Do NOT read the three blocking returns below as
        covering it."""
        try:
            pred = self.api.get_task(pid)
        except VikunjaError as exc:
            if exc.status == 404:
                return None
            if exc.status == 403:
                return (
                    {"id": pid, "title": f"task {pid}"},
                    f"unknown — the token got 403 reading task {pid}, so whether it is "
                    f"finished cannot be established",
                    {
                        "escape": (
                            f"task {pid} is unreadable by this token, so NOTHING done to the "
                            f"predecessor releases this card while that holds — not even "
                            f"marking it done, since the read fails BEFORE `done` is looked "
                            f"at. Sharing its project with this token does not release the "
                            f"card either: it makes the stage KNOWABLE, after which finishing "
                            f"the predecessor does. Removing the follows/blocked relation on "
                            f"this card clears the gate outright"
                        ),
                        "finishable": False,
                    },
                )
            raise
        if pred.get("done"):
            return None
        proj = pred.get("project_id")
        if not isinstance(proj, int) or proj == self.project_id:
            return None
        if proj not in foreign_boards:
            foreign_boards[proj] = self._foreign_stages(proj)
        stages = foreign_boards[proj]
        if stages is None:
            return (
                pred,
                f"unknown — project {proj} has no readable tracker board for this token "
                f"(403/404), so whether it is finished cannot be established",
                {
                    "escape": (
                        f"moving the predecessor to Review will NOT release this card while "
                        f"project {proj}'s board is unreadable — marking it done WILL, because "
                        f"`done` is read before the board. Otherwise give this token a readable "
                        f"board (share project {proj}, or run `vikunja-mcp setup` against it if "
                        f"its kanban view is missing), which makes the stage knowable rather "
                        f"than releasing the card, or remove the follows/blocked relation"
                    ),
                    "finishable": False,
                },
            )
        stage = stages.get(pid)
        if stage is None:
            return (
                pred,
                f"unknown — not in any bucket of project {proj}'s board",
                {
                    "escape": (
                        f"project {proj}'s board READS — the predecessor is simply in none of "
                        f"its buckets, so put it in one (Review or Done releases this card) or "
                        f"mark it done, which is checked before the board read either way"
                    ),
                    "finishable": True,
                },
            )
        if stage in READY_STAGES:
            return None
        # #1640 second pass. This branch DECORATES the stage — `Icebox (project 108)`, not
        # `Icebox` — so a caller comparing against the bare literal never matches here, and that
        # is exactly the defect an independent review measured: through the shipped `handoff`
        # flow (our card parked on a `blocked` link into the neighbour's Backlog, their human
        # freezing it) the refusal printed "finish that one first" about a card in another
        # team's freezer, while the same-project control printed the frozen clause. The FIX is
        # to decide it HERE, where the stage is still raw, and hand the verdict out as a key —
        # the shape `escape`/`finishable` already use. Deciding it downstream off the rendered
        # string would work today and break the next time this f-string is reworded.
        # `finishable` rides along for the reason the three branches above set it: no tool of
        # ours moves a card out of a freezer on any board, so the generic "finish that one
        # first" tail is FALSE of this blocker and must be dropped rather than printed.
        if stage == "Icebox":
            return (pred, f"{stage} (project {proj})", {"frozen": True, "finishable": False})
        return (pred, f"{stage} (project {proj})", None)

    @staticmethod
    def _predecessor_advice(blockers: list[dict], generic: str) -> str:
        """The advice tail under a rendered blocker list (#1190).

        The generic tail ("finish that one first", "get it back to Review first") is right for
        an ordinary blocker sitting in Build on a readable board and UNACTIONABLE for the three
        fail-closed branches of `_offboard_predecessor`: nobody can finish a card on a board
        they cannot read, and those are exactly the branches that never clear by themselves. So
        the tail is keyed off WHICH branch produced each stage, via the optional `escape` key
        `_unfinished_predecessors` carries on a blocker it could not resolve.

        The switch is `finishable`, NOT the mere presence of an escape, and that distinction is
        measured rather than tidy: the no-bucket branch reads a board that WORKS, so moving its
        predecessor to Review really does release the card and the generic tail is correct
        advice there. Dropping the generic tail whenever anything was unresolvable would have
        replaced true advice with an escape on exactly that branch.

        So: no escape at all -> the generic tail, byte for byte, so the ordinary refusal did not
        move (the separator below is what keeps that literally true — the generic sentences carry
        their own terminal punctuation, or not, exactly as they did before this). Some blocker
        still finishable -> generic first, then the escapes, because the finishable half
        genuinely does want finishing. NOTHING finishable -> the escapes ALONE, which is the case
        the card is about: there the generic sentence is the one action that cannot be taken and
        it was the only one printed."""
        unresolvable = Workflow._predecessor_escapes(blockers)
        # #1640 adds a SECOND unactionable-blocker clause beside #1190's. Two clauses and not
        # one because the two are unactionable for different reasons and only one of them is
        # about the stage being unknown — `_predecessor_escapes` prints under a lead that says
        # so out loud, and a frozen predecessor's stage is perfectly well known. Composition,
        # not replacement: a card can wait on one of each.
        frozen = Workflow._predecessor_frozen(blockers)
        # The clauses are joined the SAME way `generic` is joined to them below — by asking each
        # whether it already ends in terminal punctuation — and not with a bare space. Review
        # found the bare space: `_predecessor_escapes` ends its clause without a full stop, so a
        # card waiting on one unreadable AND one frozen predecessor read "...clears the gate
        # outright 1 of those sit(s) in Icebox...", two sentences run together at the seam. Every
        # clause here is author-written prose whose punctuation is nobody's contract, so the join
        # asks rather than assumes; `_sentence_join` is that question, used at both seams.
        tail = Workflow._sentence_join(clause for clause in (unresolvable, frozen) if clause)
        if not tail:
            return generic
        if not any(blocker.get("finishable", True) for blocker in blockers):
            return tail
        return Workflow._sentence_join((generic, tail))

    @staticmethod
    def _sentence_join(clauses) -> str:
        """Join advice clauses so the seam between two of them is a sentence break (#1640).

        Pulled out of the inline `separator` expression `_predecessor_advice` used for its
        generic/tail seam, because review measured a SECOND seam — escape against frozen — that
        the inline form did not cover, and a third would have been written the same way. It adds
        the stop only where the left clause does not already carry one, so every wording pinned
        byte-for-byte elsewhere is untouched: those clauses end in a full stop and get nothing."""
        joined = ""
        for clause in clauses:
            if not clause:
                continue
            if joined:
                joined += "" if joined.rstrip().endswith((".", "!", "?")) else "."
                joined += " "
            joined += clause
        return joined

    @staticmethod
    def _predecessor_frozen(blockers: list[dict]) -> str:
        """The clause for a predecessor parked in Icebox, or "" when none is (#1640).

        KEYED OFF THE `frozen` KEY, AND THE FIRST DRAFT KEYED OFF THE STAGE STRING INSTEAD —
        which is this method's own post-mortem, not a preference. That draft argued the stage
        needed no new key because "a frozen predecessor reads the same whether it was resolved
        on this board or on a neighbour's". It does not: `_offboard_predecessor`'s resolved
        branch renders `Icebox (project 108)`, so `== "Icebox"` matched only same-project
        blockers and the cross-project case printed the very tail this clause exists to remove.
        Measured through the shipped `handoff` flow with a same-project control in the round.
        Note WHERE the sentence went wrong: it was a claim about a method it never read, written
        into the docstring as settled — the failure this repo's rulebook names first.

        ONE sentence however many blockers are frozen, like `_predecessor_escapes` dedupes: the
        refs are printed above this clause by every caller, so repeating them here would say
        the same thing twice. What it must NOT do is imply waiting helps — Icebox is not a
        queue, and the successor of a frozen card is blocked until a person acts."""
        frozen = [b for b in blockers if b.get("frozen")]
        if not frozen:
            return ""
        return (
            f"{len(frozen)} of those sit(s) in Icebox — the freezer for legacy and very-minor "
            f"work nobody has undertaken. That does NOT clear by being worked and no tool of "
            f"yours moves a card out of Icebox, so waiting will not help: either a human pulls "
            f"it back into Backlog/Queue, or the follows/blocked link to it is the thing to "
            f"drop. Report it rather than sitting behind it."
        )

    @staticmethod
    def _predecessor_escapes(blockers: list[dict]) -> str:
        """The escape clause alone, or "" when every blocker resolved to a real stage (#1190).

        Split out of _predecessor_advice because `_starving_tail` needs the clause WITHOUT any
        generic tail to append it to — next_task skips a gated card rather than refusing it, so
        there is no refusal sentence there — and the wording must not be written twice. Escapes
        are deduped in first-seen order: two predecessors on the same unreadable board yield one
        sentence, not two — the two board branches word their escape around the PROJECT, not
        the task, precisely so that dedup has something to collapse (the ref of every blocker
        is already printed ABOVE this clause; in `_starving_tail` that is all the waiting lines,
        not the one line before it). The 403-on-the-task branch is the exception and is right to
        be: its escape is about that one task.

        The lead says nothing about who can act, deliberately. An earlier wording claimed the
        blocker "will NEVER clear by itself and no agent can unblock it", which is true of the
        two fail-CLOSED-forever branches and FALSE of the no-bucket one, where an agent moving
        the predecessor into Review clears it."""
        escapes: list[str] = []
        for blocker in blockers:
            escape = blocker.get("escape")
            if escape and escape not in escapes:
                escapes.append(escape)
        if not escapes:
            return ""
        return (
            "At least one of those stages could NOT be established, and nothing on THIS board "
            "changes that. What does: " + "; ".join(escapes)
        )

    @staticmethod
    def _assignee_ids(task: dict) -> list[int]:
        return [a["id"] for a in task.get("assignees") or []]

    @staticmethod
    def _has_label(task: dict, title: str) -> bool:
        """Does the board say this card carries THIS label — resolved the way the SERVER resolves
        it, through `api.label_key` (#1256). It used to be `lb["title"] == title`, EXACT, and that
        is the whole of #1256: `get_or_create_label` has always matched case- and
        whitespace-insensitively, so a label a human typed capitalised in the web UI EXISTS as far
        as every write in this package is concerned and DOES NOT EXIST as far as every gate reading
        it is concerned. #1216 closed one instance of that (the guard inside `_add_label`, re-keyed
        to the resolved label ID); this is the same disagreement at the thirteen call sites that
        read.

        MEASURED on a live `Workflow` over `FakeAPI` with agent tools only, one variable — the
        SPELLING — and each pair against its lowercase control. `Bug`/`BUG`/`bug ` on a card:
        `advance(to='review')` with NO `root_cause` SUCCEEDED, `review_kind='change'` (control
        `bug`: REFUSED, the #718 gate) — a bug fix reaching its reviewer with no cause, which is
        precisely the state #718 exists to make impossible. `Blocked`/`BLOCKED`/`blocked ` on a
        free Queue card: `next_task` OFFERED it (control `blocked`: withheld).

        THE `epic` SITES ARE NOT THE MILD ONES, and the card that filed this guessed the
        opposite — its scope note reasons that an epic container is created by `decompose`, which
        writes the label itself, so a human variant there is far less likely than on
        `bug`/`blocked`, which humans do type by hand. `decompose` writing it is the DEFECT
        rather than the protection: it writes through `_add_label` ->
        `get_or_create_label('epic')`, which RESOLVES to whatever `Epic` the board already holds.
        Measured end to end — board pre-seeded with an `Epic` label, nobody typing anything: the
        container `decompose` just created carries title `Epic`, after which `claim(container)` is
        ACCEPTED (control: REFUSED, "is an epic CONTAINER") and `next_task` OFFERS it (control:
        False). So the package's own write path manufactures the disagreement.

        WHY THIS AND NOT THE RESOLVED-ID SHAPE `_add_label` USED FROM #1216 TO #1456 — and note
        the tense, because this sentence said "USES" and stayed present-tense through the very card
        that removed the shape it names. `_add_label` asks `_has_label` now, i.e. this question, so
        the two agree by construction rather than by argument; what follows is still why a READ
        gate could never have gone the other way. Asking `get_or_create_label`
        here would be the same question by the same route — and it CREATES the label when absent,
        so a READ gate would MINT labels on a board that has none. `vikunja-mcp claimable` is
        READ-ONLY BY CONTRACT and the hgdev-acp hub polls it per loop tick through this very
        method, so that is a per-poll tracker mutation, not a cost question (it is also a paged
        `labels()` read per call, on `next_task`'s hot path — MEASURED at up to TWO per card, not
        the five its call SITES suggest: the assignee conjuncts short-circuit, so a Queue card
        costs 1 assigned or 2 free, never both branches). Sharing the KEY
        instead of the ROUTE buys the agreement at zero requests, and `label_key` being the single
        statement of the rule is what keeps this from becoming the second spelling the card warned
        about.

        NOT applied to BUCKET titles (`bucket["title"] == "Review"`): those are canonical names
        this package's own `setup` writes, not free text a human types."""
        want = label_key(title)
        return any(label_key(lb.get("title")) == want for lb in task.get("labels") or [])

    def _add_label(self, task: dict, title: str) -> None:
        """Put a label on a card — IDEMPOTENTLY, and this is the ONLY label write path in the
        package.

        Both halves of that sentence are the #1216 fix, and the second one is the cause. Real
        2.3.0 answers `PUT /tasks/{id}/labels` with a label the task ALREADY carries as
        `400 {"code":8001,"message":"This label already exists on the task."}` — measured through
        this package's own client on a throwaway container, not inferred. Before #1216 FOUR routes
        reached that 400, and in each of them the call that FAILS is an agent tool — but only one
        needs no human step at all: a second `review_task(...,'approve')`. The other three run off
        a state a human's hand left on the card — a `needs_work` on one hand-dragged back to Review
        still carrying `review-failed`, a `return_task` on one already labelled `blocked`, and a
        `decompose` on one already labelled `epic`.

        THE FOUR ROUTES HAD TWO DIFFERENT CAUSES, and the fix needs both halves — which is why it
        is a single write path AND a check on it, rather than either alone. TWO of them
        (`review_task`'s approve and needs_work branches) did call this helper, so a check written
        here would have closed them; the OTHER TWO never reached it — `return_task` and
        `decompose` each INLINED its two lines, `get_or_create_label` + `api.add_label` — so no
        check written here could have. And the one guard that did exist (epic-ready) was that
        site's own `continue`, not this function, so there was no place the invariant was stated
        at all. `api.add_label` now has exactly one caller, as `api.remove_label` already had.

        TAKES THE SNAPSHOT, NOT AN ID — an exact mirror of `_remove_label` below, deliberately.
        Every call site already holds a fresh task dict (`_find_task` for review_task/return_task/
        decompose, a fresh `get_task` for epic-ready), so the idempotency costs ZERO extra
        requests. The alternative shape considered on the card — re-reading the task inside this
        helper — would have bought a NARROWER race window (one GET immediately before the PUT
        rather than a snapshot taken at the top of the method) at one extra GET per label write.
        That is the same trade this repo already declines for `_remove_label`, which reads the
        caller's snapshot for exactly the same reason. Two matched
        signatures are also what makes the next bypass unlikely: a reader reaching for "add a
        label" now finds the same shape as the remove beside it, instead of an id-taking helper
        that is easier to re-inline than to call. Skipping `get_or_create_label` on the no-op path
        is a side benefit, not the point: that call is a PAGED list read of every label. #1216's
        guard did NOT in fact skip it — it had to resolve before it could ask its question — so
        that sentence described an aspiration until #1456 made it true; it is pinned by
        `test_the_no_op_path_does_not_read_the_label_list_at_all`.

        THE GUARD ASKS `_has_label`, AND FROM #1216 UNTIL #1456 IT ASKED BY RESOLVED LABEL ID —
        both forms are recorded because the reason for the second is a leak this repo paid for.
        #1216's own first draft read `if self._has_label(task, title): return` and LEAKED:
        `_has_label` THEN compared titles EXACTLY (`lb["title"] == title`) while
        `api.get_or_create_label` resolves case- and whitespace-INSENSITIVELY, on purpose (api.py:
        a bot typing `Bug`/`bug ` once forked a duplicate label, real incident 2026-07-08). The two
        disagreed about what "this label" means, and on a real 2.3.0 that gap was the whole defect
        again: a card carrying `Vari906071`, no lowercase twin anywhere,
        `_has_label(card,'vari906071')` False, `get_or_create_label('vari906071')` returning that
        very label — and the PUT answering `400 code 8001`. So #1216 resolved FIRST and asked
        whether THAT LABEL ID was on the snapshot, which is the same question the server asks.

        #1256 CLOSED THAT LEAK AT ITS SOURCE — `_has_label` reads through `api.label_key` now, so
        it and `get_or_create_label` ask ONE question — and with it went the ID keying's reason and
        its PIN: keying the guard on the title killed NOTHING. #1256 reported that as 0 failed
        against a clean control of 0 failed / 0 errors / 1399 collected over the whole of
        `tests/unit`, where #1216 had the same row at 2; #1456 REPRODUCED it rather than inheriting
        it — 0 failed / 0 errors / 154 collected on #1256's five-file selection with this card's
        two new pins removed, against that round's own control of 0 failed / 0 errors / 156
        collected. #1456 then measured what was left and returned the guard to the `_has_label`
        form, on three grounds in descending strength.

        FIRST, NEITHER FORM CAN 400 ON A FAITHFUL SNAPSHOT, and that much is provable rather than
        measured: both `_has_label` and `get_or_create_label` read with the SAME `label_key`, so
        "no row on the card normalises to `title`" and "the row `get_or_create_label` returns is
        not on the card" are one statement, and the PUT goes out only where the server accepts it.
        **BUT THE TWO RESIDUALS ARE NOT EQUAL, AND THIS ONE IS THE LARGER — the one real argument
        for the ID keying, found by this card's second independent pass and kept here because
        burying it would make the decision look cheaper than it is.** The ID form needs the
        snapshot's label IDS to be faithful; this form needs its TITLES to be faithful too.
        Measured over `FakeAPI` on both forms, three constructions, same answer each time: a row
        RENAMED to `title` between the board read and the PUT (the card wears it under its old
        name), a snapshot label with no `title` key, and one whose title is whitespace — this form
        RAISES `400 code 8001`, the ID form SKIPS. Two things bound that, and they are why the
        trade still goes this way. The title-blind shapes are not reachable from any server payload
        measured here: real 2.3.0 returns full label objects WITH titles from `get_task` and from
        the kanban `view_tasks` copy, and the `related_tasks` sub-dict's `labels: null` blinds both
        forms equally. And nothing in this package RENAMES a label — `api.py` has `create_label`,
        `add_label`, `remove_label` and no update path at all — so the live window needs a human
        renaming a row in the web UI between one read and one write. Weigh the shapes, not just the
        odds: this form's failure is the LOUD one #1216 was about, a `400` surfacing as a failed
        agent tool in a window nothing here opens, while the ID form's is a SILENT extra row, on a
        settled board, repeated for every card that passes through.

        A BYTE-EXACT title comparison is a THIRD
        reading and not this one: it still raises #1216's `400 code 8001` on rows
        `[Blocked, blocked]` with the card carrying `Blocked` — re-measured on the REAL container
        by #1456, not only over `FakeAPI`, and in the same run both this form and the ID form
        SKIPPED that arrangement without raising — and it is still caught — 2 failed
        (`test_a_title_VARIANT_does_not_slip_past_the_guard` plus the two-row pin below) against a
        control of 0 failed / 0 errors / 156 collected on #1256's five-file selection. #1256
        measured that mutation at 1 on #1216's narrower three-file selection; the second failure
        is the pin this card adds, not a change of behaviour.

        SECOND, WHERE THE TWO FORMS DIVERGE THE ID FORM IS THE ONE THAT WRITES A DUPLICATE, and
        the boards they diverge on are ones a REAL SERVER PERMITS — which is #1456's probe, the
        question #1216 and #1256 both left open, run on a throwaway 2.3.0 through this package's
        own client. `PUT /labels` with a title that already exists BYTE-IDENTICALLY is ACCEPTED,
        yielding two rows of one spelling; with a CASE variant, likewise. And `PUT
        /tasks/{id}/labels` with the SECOND such row, on a card already carrying the first, is
        ACCEPTED TOO — all three arrangements probed (the same-spelling pair, the card carrying the
        capitalised row, the card carrying the lowercase row) — leaving the card wearing BOTH. So
        the refusal tracks the `label_id` and not the title — a two-point boundary (same id
        refused, different id of the same normalised key accepted), which is what the guard needs
        and is not the same as having enumerated everything the server keys on — and two rows for
        one concept on one card is a state it allows rather than one only a fake would show.
        On those boards the ID
        form sent the PUT and produced `['Blocked', 'blocked']` where this form skips and leaves
        the one; FOUR of the EIGHT two-row arrangements diverge, and the ID form is the
        duplicating one in every one of them. #1256's six-row table was re-derived from scratch
        here and reproduces exactly, the byte-exact `RAISES` row included — but its SPACE was
        inherited and is short: over two spellings there are four ORDERED two-row boards and two
        carriers each, and `[Blocked, Blocked]` is in neither that table nor this card's first
        draft. Driving all eight gives a rule simpler than any census: `get_or_create_label`
        returns the FIRST matching row, so the two forms diverge exactly when the card wears the
        SECOND — four boards, four divergences, and the ID form duplicates in all four. Found by
        this card's second independent pass, which drove the eight and confirmed the missing board
        on a real container as well: two rows spelled `Blk153941`, the card wearing the second, the
        PUT of the first ACCEPTED.

        THIRD, THE DIVERGENT BOARD IS ONE THIS PACKAGE REACHES BY ITSELF, so the choice is not
        about somebody else's mess. `get_or_create_label` is read-`labels()`-then-`create_label`
        with nothing atomic between, so at `wip_limit > 1` two agents adding the same absent label
        both miss and both create — and the probe's first answer is what makes that route REAL
        rather than argued: the server accepts the duplicate title instead of refusing it, so the
        race forks a row rather than 400ing. That probe created the two rows as ONE user in
        SEQUENCE, which is the shape this fleet has (one scoped token is the whole fleet, so both
        racing agents ARE one user); it drove no concurrent pair, and did not need to — what it
        settles is that the server holds no uniqueness rule on the title for the race to lose to.
        Both rows then carry the SAME spelling, since every `_add_label`/`get_or_create_label` call
        site in this module passes a lowercase `LABEL_*` constant — re-walked with `ast` here, five
        such sites plus this method's own forwarding of its `title` parameter, and no other; the
        two-SPELLINGS board still wants a human typing `Blocked` in the web UI.
        A SECOND ROUTE IS `GET /labels` SURFACING ONLY WHAT THE CALLER CAN READ, AND #1456
        MEASURED IT — this sentence said "read THAT half as UNPINNED" until this card, because
        api.py stated the visibility in the WIDENING direction only ("not just its own") and the
        exclusion on top of it was an inference. Measured on a real 2.3.0 with a CONTROL: a row
        owned by another user and used on no task the agent can read is ABSENT from the agent's
        `GET /labels`, and the SAME row APPEARS once it is put on a task the agent can read, the
        owner seeing both throughout — so the absence is visibility, not existence. The sharp
        consequence is not the mint but the DISAGREEMENT: at that moment the two callers resolve
        one title to DIFFERENT rows on the same board, which is this divergence arriving without
        any race at all. The MINT ("an invisible row is minted again") still follows from
        `get_or_create_label`'s own two lines rather than from a probe, and is not claimed as
        measured. What the ID form did with such a board was to SPREAD
        it: every card passing through this helper picked up the second row — the proliferation
        `get_or_create_label` exists to prevent, one level down, on the TASK rather than on the
        label list — and it feeds VMCP-317 (1457), where `_remove_label` takes only the FIRST
        matching row, so a card wearing both keeps a stale badge. That card's own description
        asserts this package cannot create its state, "`_add_label` can only ever attach one",
        which was FALSE while this guard was ID-keyed and is true with this form. NARROWED, not
        closed: the snapshot race in RESIDUAL below still lets two agents whose snapshots each
        predate the other's write land two rows on one card.

        WHAT THIS FORM COSTS, said plainly because it is a real trade and not nothing — and it is
        REASONING about the web UI's filter rather than a measurement, nothing here read that UI: a
        human who typed `Blocked` by hand and then filters the board by the `blocked` row would not
        find a card this helper skipped, where the ID form would have put both rows on it and both
        filters would hit. Weighed against the duplication above it loses — the card already reads
        as carrying the label at every `_has_label` gate, the anomaly is in the label LIST rather
        than on the card, and a helper that copies the anomaly onto every card it touches makes the
        human's cleanup larger, not smaller — but it is the reason this is a decision and not a
        cleanup.

        WHAT PINS IT, since "keyed on the title" is exactly the mutation that used to kill nothing:
        `test_the_guard_SKIPS_rather_than_minting_a_second_row_on_a_two_row_board` in
        `tests/unit/test_workflow_duplicate_label.py` builds all FOUR divergent arrangements and
        asserts the skip, and restoring the ID keying turns it RED — 2 failed with
        `test_the_no_op_path_does_not_read_the_label_list_at_all` beside it, against a control of
        0 failed / 0 errors / 156 collected, and 0 failed with both of those pins deleted.
        The two SERVER facts the fake mirrors — a duplicate title accepted, and both rows
        accepted on one card — are pinned where only they
        can be, in `tests/integration/test_duplicate_label.py`. `FakeAPI` needed no change for any
        of this, and #1456 is where that stopped being an assumption: `create_label` appends
        unconditionally and `add_label` refuses on `label_id` alone, which is exactly what the
        container answered.

        RESIDUAL, and what is closed is the STATE, not the RACE: a label added by somebody else
        between the board read and this PUT still 400s. That residual is written up in
        `FakeAPI._read_task`'s docstring, which is also where `_remove_label`'s (closer to
        unreachable) half lives. What makes the snapshot trustworthy HERE was measured rather than
        assumed — on real 2.3.0 the kanban copy `api.view_tasks` returns carries `labels`
        POPULATED (`["reviewed","blocked"]`), so this is not the #125 hollowing mode, where labels
        read as None off a `related_tasks` sub-dict and a check on them silently no-op'd in
        production while the too-generous fake stayed green. That is ONE container measured, not a
        law: #885 measured a live board copy coming back with EMPTY `assignees` on an assigned
        card, so the same could happen to `labels`. The direction of that failure is the reason it
        is survivable — a label-less copy makes the guard say "not there" and degrades to exactly
        the pre-#1216 400, never to a wrong write. Precedent for the shape: `claim` decides its
        `add_assignee` off the same kind of snapshot — `self_heal` ("I am already the only
        assignee") skips the PUT, and anyone else among them is refused before it — so only the
        same staleness race reaches the server's refusal there. And the server does refuse:
        measured in the same round, `400 {"code":4021,"message":"This user is already assigned to
        that task."}`."""
        if self._has_label(task, title):
            return
        self.api.add_label(task["id"], self.api.get_or_create_label(title)["id"])

    def _remove_label(self, task: dict, title: str) -> None:
        # снимаем только реально висящую на снапшоте метку — иначе DELETE по несуществующей
        # связи 403-ит (`Forbidden`), НЕ 404: измерено на живой 2.3.0 по ходу #1211, где эта
        # строчка ещё утверждала 404. Ветка от этого не меняется — DELETE просто не уходит.
        # ...и ищем её так же, как ищет СЕРВЕР — через `api.label_key` (#1256). До этого здесь
        # стояло `x.get("title") == title`, точное сравнение — тот же разлад, что и в `_has_label`
        # строкой выше, и он ИЗМЕРЕН, а не выведен по симметрии: карточка, которую человек руками
        # вытащил из Review в Build с меткой `Reviewed`, проходила `advance(to='review')` и
        # оставалась с этой меткой (контроль `reviewed` — снимается), то есть протухший APPROVE
        # уезжал в свежий Review. Ровно это докстринг `_clear_verdict_labels` ниже и запрещает.
        # Снимаем ПЕРВУЮ подходящую, как и раньше — и вот ОСТАТОК, который это НЕ закрывает:
        # если на карточке висят ОБЕ строки (`reviewed` и `Reviewed`), уйдёт одна, вторая
        # останется. Измерено: до advance `['reviewed', 'Reviewed']`, после — `['Reviewed']`.
        # И НЕ только рукой человека: здесь стояло «сам пакет такого состояния не создаёт», и это
        # неверно — `get_or_create_label` устроен как read-`labels()`-then-`create_label`, без
        # атомарности между ними, так что при `wip_limit > 1` два агента, добавляющих одну и ту же
        # отсутствующую метку, оба промахиваются и оба создают; а `GET /labels` показывает только
        # метки, висящие на задаче, которую вызывающий может ЧИТАТЬ, — невидимая строка создаётся
        # заново. Полное изложение — в докстринге `_add_label` выше, в том же коммите; сюда оно
        # тогда не доехало.
        # СНИМАЕМ ВСЕ ПОДХОДЯЩИЕ СТРОКИ, А НЕ ПЕРВУЮ (#1457). До этого здесь стоял `next(...)` и
        # ровно один DELETE, поэтому на карточке, несущей ОБЕ строки одного нормализованного
        # ключа, одна оставалась. Измерено на живом `Workflow` над `FakeAPI` агентскими тулзами,
        # обе строки заведены `create_label`: до `advance(to='review')`
        # `['reviewed', 'Reviewed']`, после — `['Reviewed']`, при контроле на одной строке —
        # пусто. На трёх строках выживали две. `review_task(needs_work)` оставлял
        # `['Reviewed', 'review-failed']`, то есть ОБА взаимоисключающих вердикта разом, а
        # `transfer_task` увозил `Blocked` на доску соседа.
        # ДВУСТРОЧНЫХ ДОСОК ДВЕ, И ЭТО НЕ ОДНО И ТО ЖЕ — абзац выше (#1256) уже один раз схлопывал
        # их в одну и это пришлось отзывать, поэтому здесь они разведены. Два НАПИСАНИЯ
        # (`reviewed` + `Reviewed`) пишет РУКА человека в веб-UI: сам пакет их не пишет никогда —
        # его единственная продовая точка вызова `get_or_create_label` сидит внутри `_add_label`,
        # а туда всегда приходит строчная константа `LABEL_*`. ОДНО И ТО ЖЕ написание дважды
        # (`reviewed` + `reviewed`) — вот доска, до которой пакет добирается САМ: гонка двух
        # `get_or_create_label` при `wip_limit > 1` (read-`labels()`-then-`create_label`, без
        # атомарности), и раз обе стороны передают ту же константу, обе строки выходят одинаково
        # написанными. Утекало на обеих, поэтому пин гоняет обе.
        # ЦИКЛ НЕ ОТКРЫВАЕТ НОВОГО МАРШРУТА В ИЗМЕРЕННЫЙ 403 — это свойство цикла, а не
        # терпимость к отказу. Каждый DELETE идёт по РАЗНОМУ `label_id`, и каждый из них висел на
        # снапшоте вызывающего: две строки — это два разных ряда доски, а повторное добавление
        # одного id отвергают и сервер (400 code 8001), и фейк, так что один id дважды на
        # карточке не окажется. Остаётся та же гонка со снапшотом, что была и раньше, — она
        # разобрана в докстринге `FakeAPI._read_task` и там же квалифицирована как «гонка, а не
        # маршрут». Единственный способ для самого цикла получить тот 403 — послать один и тот
        # же `label_id` дважды, поэтому список ДЕДУПЛИЦИРУЕТСЯ ПО id — и вот ЭТО и несёт
        # безопасность, а 400 лишь подпирает: цикл ходит по КЛИЕНТСКОМУ дикту, а не по хранилищу
        # сервера. У дедупликации есть свой пин. А ВОТ У ДВУХ СОСЕДНИХ ЕГО НЕТ, и сказано это
        # затем, чтобы следующий читатель не снёс их на основании зелёного раунда: список
        # МАТЕРИАЛИЗУЕТСЯ до первого DELETE (генератор поверх `task["labels"]` — классический
        # способ вернуть ровно этот баг), и `x["id"]` читается только у ПОДОШЕДШЕЙ строки — ровно
        # там, где его читал `next(...)`, то есть это сохранение прежнего, а не новый гард. Обе
        # мутации дают 0 failed при control 0 failed / 0 errors / 145 collected — таблица свипа
        # это пишет. Единственное измеренное следствие
        # второго нужно снапшота, которого сервер не выдаёт: у ПОДОШЕДШЕЙ строки без ключа `id`
        # прилетит `KeyError` из цикла отбора, до первого DELETE, — то есть на двухстрочной
        # карточке одна битая строка теперь срывает всю чистку, тогда как раньше вторая бы
        # снялась; а `KeyError` не входит в список перехвата `_tool` в `server.py`.
        # ОБА ОТВЕТА СЕРВЕРА ПЕРЕМЕРЕНЫ ДЛЯ #1457 НА ЖИВОЙ 2.3.0, а не унаследованы: DELETE по
        # уже снятой связи — 403 `Forbidden`, по метке, которую на задачу вообще не вешали, — 403,
        # по несуществующему `label_id` — тоже 403 (404 не отвечает ни один из трёх), а PUT с уже
        # висящим id — 400 code 8001. Тот же контейнер собирает и саму двустрочную карточку
        # (`['reviewed', 'Reviewed']`), так что состояние не артефакт фейка. Таблица — в
        # `docs/dossier/workflow.md`.
        # ОТКАЗ НА ОДНОЙ СТРОКЕ НЕ ОТМЕНЯЕТ ОСТАЛЬНЫХ И НЕ ГЛОТАЕТСЯ: пробуем каждую, запоминаем
        # ПЕРВУЮ `VikunjaError` и поднимаем её после цикла. На одной строке — то есть на любой
        # обычной доске — поведение прежнее: один DELETE, наверх уходит то же исключение, так что
        # контракт не сдвинулся ни у одной точки вызова, а их ПЯТЬ в ТРЁХ методах —
        # `_clear_verdict_labels` x2, обе ветки `review_task` и цикл `transfer_task` по
        # `blocked`/`reviewed`/`review-failed`/`epic-ready`. На двух строках падение одной больше
        # не прикрывает протухший бейдж на другой, ради чего всё и делается. Глотать нельзя,
        # потому что 403 здесь не всегда доброкачественный «связи нет»: задача в проекте, который
        # токену не виден, отвечает 403 на DELETE, который иначе прошёл бы (таблица зондов в
        # `FakeAPI._read_task`), — глушение отказало бы OPEN и молча, ровно тем режимом, про
        # который #1256. А токен, которому просто не выдали `tasks_labels: delete`, отвечает там
        # 401, НЕ 403 — та же таблица пишет, что до эндпойнта не доходило вовсе; статус другой и
        # глотать его тоже нечего. Сниффить тело ради «доброкачественного» 403 — та форма,
        # которую #1216 отверг для 400 в `add_label`: сниф глотает и чужую ошибку того же статуса.
        want = label_key(title)
        ids: list[int] = []
        for x in task.get("labels") or []:
            if label_key(x.get("title")) != want:
                continue
            if x["id"] not in ids:
                ids.append(x["id"])
        failed: list[VikunjaError] = []
        for lid in ids:
            try:
                self.api.remove_label(task["id"], lid)
            except VikunjaError as exc:
                failed.append(exc)
        if failed:
            raise failed[0]

    def _clear_verdict_labels(self, task: dict) -> None:
        """Снять ОБЕ взаимоисключающие вердикт-метки (`reviewed` / `review-failed`). Задача,
        (пере)входящая в активный пайплайн — агент начинает (пере)сборку или ресабмитит в
        Review, — НЕ несёт действующего вердикта: любой прошлый инвалидируется в момент
        возобновления работы. #119: когда человек РУКАМИ вытаскивает одобренную карточку из
        Review на доработку, ни одна тулза не срабатывает, поэтому `reviewed` переживает
        возврат; снятие здесь, на следующем forward-переходе агента, не даёт несвежему APPROVE
        уехать обратно в свежий Review (ложь на доске). Оффер ревью в next_task при этом
        цепляется за свежесть коммента [worklog]/[review], а НЕ за эту метку, так что стале-
        `reviewed` не подавлял бы re-ревью — но ложный бейдж всё равно не должен оставаться.
        Идемпотентно по каждой метке — _remove_label шлёт DELETE только по реально висящей на
        снапшоте связи, поэтому на задаче без вердикт-меток (свежий клейм) это no-op.
        #673 добавил ТРЕТИЙ вызов, и он про обратное направление: `decompose` зовёт это не на
        входе в пайплайн, а на ВЫХОДЕ из него — карточка перестаёт быть работой и становится
        эпиком-контейнером, чья работа переезжает в детей. Общее у всех трёх — не стадия, а то,
        что прошлая оценка перестала описывать карточку; у эпика она вдобавок НЕПРИМЕНИМА —
        обе точки, где карточку предлагают на независимое ревью (push-нудж в advance и pull-ветка
        в next_task), эпик пропускают, так что штатный поток к этому вердикту уже не вернётся."""
        self._remove_label(task, LABEL_REVIEW_FAILED)
        self._remove_label(task, LABEL_REVIEWED)

    def _kanban_assignees_may_be_stale(self, task: dict) -> list[int]:
        """Assignee ids for an ownership decision, re-read from `/tasks/<id>` when the copy in
        hand carries NONE (#885).

        The copy every ownership gate judges by comes off the KANBAN BOARD (`_find_task` searches
        `_board()`), and MEASURED on the live tracker (project 10, 2026-08-06) that copy can come
        back with an EMPTY `assignees` while the task itself is assigned: `GET /api/v1/tasks/854`
        answered `[(7, 'agent-vikunja-mcp')]` and the board copy of the same card answered `[]`.
        Every ownership gate then saw an ownerless card, and #705's clause — correct for a really
        ownerless card — told the agent to `claim` it, which refuses outside Queue. The card sat
        in Design with `advance`, `call_human`, `return_task` and `decompose` all refusing
        identically, so no tool could MOVE it or make it anyone's, and the agent could not even
        ask a human ABOUT IT (`call_human` needs the same ownership). Precisely that, not
        "workable by no tool": `get_task`, `comment`, `attach_file` and `file_task` need no
        ownership and all still work on such a card (measured), and `get_task` reports the real
        assignee — which is what makes the `file_task` workaround below reachable at all.

        What such a card does NOT do is HOLD a WIP slot — it LOSES one, and this docstring said
        the opposite for a round. `_my_active_tasks` counts off that same board copy, so the card
        is invisible to the counter: measured at `wip_limit = 3` with three cards claimed and ONE
        blacked out, the gate reads `{'active': 2, 'limit': 3, 'free': 1}` while THREE really are
        assigned to me in Design/Build, so a FOURTH claim is ACCEPTED and leaves four against a
        limit of three (the healthy control refuses it, `WIP limit reached (3/3)`).

        In Design/Build `next_task` does not hand such a card back either — `task=None`, against
        `task=<id>`/`resume=True` on that control — so nothing re-offers it and a per-task agent
        that dies on one is not replaced automatically. What is gone is the OFFER, not the card:
        an orchestrator still holding the id can read it, and `claim` named the divergence when it
        happened.

        Do NOT widen that to "never offered again", because IN REVIEW the same blackout INVERTS
        it. The review branch skips cards whose assignees — read off that same board copy — are
        me, so a blacked-out card in Review IS offered, to its own AUTHOR (`review=True`), where
        the healthy control correctly answers `task=None`. Measured on both sides.

        All of this is PRE-EXISTING and stays unfixed here (the human's answer scoped this card to
        the gates); what may not stand is describing it backwards.

        RARE, and that is measured rather than assumed: the same day, the board copy was compared
        against `GET /tasks/<id>` for all 31 cards outside Done and exactly ONE diverged. It is
        also DURABLE rather than a post-claim race — re-assigning (DELETE + PUT on
        `/tasks/854/assignees`), moving the card between columns and a full read-modify-write
        `POST /tasks/854` were each tried on the live card and the board copy stayed empty. So
        nothing here can heal the server's copy; the gates are made resilient to it instead.
        WHY the server drops them is a Vikunja question and deliberately out of scope.

        ONLY on an empty list, never unconditionally: an ownership gate that re-read on every call
        would pay a GET per call for a shape seen once in 31 cards. And only from `_require_mine`,
        never from `_find_task` — `_find_task` also serves the read paths and `claim`, where an
        empty `assignees` is the ORDINARY state of every free card in Queue/Backlog, so the branch
        would stop being rare at all. Here it sits on the path that is about to RAISE.

        Best-effort by the same reasoning that makes it cheap: if the re-read fails, fall back to
        the copy in hand rather than turning a clear ownership refusal into a network error."""
        assignees = self._assignee_ids(task)
        if assignees:
            return assignees
        try:
            return self._assignee_ids(self.api.get_task(task["id"]))
        except (VikunjaError, httpx.HTTPError) as exc:
            _stderr_note_best_effort(
                f"vikunja-mcp: assignee re-read skipped for #{task['id']}", exc
            )
            return assignees

    def _require_review_independence(self, task: dict) -> None:
        """Refuse a verdict from the card's OWN assignee — but only where a repo asked for it
        (`require_review_independence` in the repo toml, tracker #37). Default OFF and INERT.

        WHY IT IS A FLAG AND NOT THE RULE. Measured on the fake before this gate existed, both
        verdicts, two identities: `review_task` accepted a verdict from the card's own assignee
        (`approve` -> the `reviewed` label lands, card stays in Review for the human), and it is
        the ONLY mutating tool that never calls `_require_mine` at all — the `_assignee_ids` it
        does read routes an OWNERLESS bounce to Queue (#705), it has never been an authorship
        check. Two OPPOSITE readings follow, and shipping either one alone would be wrong:

        * In a SOLO setup that absence is the CONDITION OF OPERATION, not a hole. One scoped
          token is the whole fleet, so the orchestrator and every per-task agent it dispatches —
          reviewers included — authenticate as the SAME assignee. Independence there is carried
          by the agents' separated CONTEXTS (push model: a sibling reviewer with a fresh context
          reviews from the same identity), which nothing server-side can observe. Make this
          unconditional and solo review stops working outright, for this repo and for every
          consumer on `stable` — the reason the human chose the flag (option B) over a hard gate.
        * In MULTI-IDENTITY it IS the hole this card was filed for. There "you don't review your
          own work" rests ENTIRELY on next_task's OFFER, and since #991 that rests less: with the
          flag off the offer no longer WITHHOLDS your own card, it only ranks it behind everyone
          else's and hands it over once none are left. An offer is a hint either way, never a
          gate: neither form sees a direct `review_task` call. And a self-approval is not
          cosmetic — `approve` writes `reviewed`,
          the label a human reads when deciding Done, so afterwards it is INDISTINGUISHABLE from
          an independently accepted card.

        THE ASSIGNEE READ IS THE STALE-TOLERANT ONE, deliberately. Ownership is judged off the
        KANBAN copy, and #885 measured that copy coming back with an EMPTY `assignees` while the
        card really is assigned. Read raw, this gate would then find nobody in the list and PASS
        precisely the card whose blackout also DELETES next_task's offer filter — i.e. it would
        be absent on the one shape where both other protections are already gone. Reusing
        `_kanban_assignees_may_be_stale` re-reads `/tasks/<id>` only when the copy is empty, so
        the cost is one GET on a shape seen once in 31 cards, and only when the flag is on.

        A GENUINELY OWNERLESS CARD STILL PASSES, and that is correct rather than a leak: with no
        assignee there is no author to exclude, so the card stays reviewable and its `needs_work`
        still routes to Queue (#705). What the gate excludes is a caller who is ON the card.

        WHAT IT COSTS WHERE IT IS ON, named rather than hidden: in a solo setup the only token
        IS the assignee, so with the flag on NOBODY can review anything. That is the flag doing
        its job, not a defect — it is why the default is off and why the human's answer ties
        turning it on to the step that provisions a second identity. The refusal says so."""
        if not self.require_review_independence:
            # Off: not even `me()` is resolved, so the request trail and behaviour are what they
            # were before this gate existed, byte for byte.
            return
        assignees = self._kanban_assignees_may_be_stale(task)
        if self._me()["id"] not in assignees:
            return
        raise WorkflowError(
            f"you are an assignee of task {task['id']}, so you cannot review it: this project "
            f"sets require_review_independence = true in its .vikunja-mcp.toml, which makes "
            f"independent review a GATE rather than a convention. A verdict has to come from an "
            f"identity that is not on the card — dispatch the reviewer against a second token "
            f"(its own `tracker-reviewer` entry in .mcp.json, overriding VIKUNJA_TOKEN via that "
            f"entry's env block). If this project has no separate reviewer identity yet, the "
            f"flag is premature: with one token the assignee is always the caller, so nobody "
            f"can review anything — remove the key (the default is off, and review independence "
            f"then rests on dispatching a sibling agent with its own context, as before)."
        )

    def _require_mine(self, task: dict, stage: str | None = None) -> None:
        assignees = self._kanban_assignees_may_be_stale(task)
        if self._me()["id"] in assignees:
            return
        msg = f"task {task['id']} is not assigned to you — claim it first"
        # #705, residual half: "claim it first" is UNFOLLOWABLE for an OWNERLESS card outside
        # Queue — claim only works from Queue, so the advice names the one call that is guaranteed
        # to refuse. Both conditions carried weight, and ONE of the two still does: in Queue
        # "claim it first" is simply correct for an ownerless card (measured: claim from Queue on
        # such a card SUCCEEDS), so that refusal stays byte for byte what it was and is pinned
        # that way. The OTHER — somebody else's card keeping the bare message because "not
        # assigned to you" is already an accurate diagnosis — was reversed by the human on #742
        # and now has its own clause, `_OTHER_OWNER_EXIT`; the reasoning and the two measurements
        # that bought the reversal are recorded above that constant. `stage` is optional so a
        # caller without one in hand keeps the plain message rather than guessing — which is why
        # BOTH clauses require a known stage, neither guesses one, and a stage-less caller still
        # gets exactly the bare sentence.
        #
        # #734 widened it from Design/Build to EVERY stage claim refuses from — that is all six
        # non-Queue stages — but NOT with one shared text, because one text is measurably false.
        # The table below is the BEFORE picture — measured at 7121dcf, the tip this work forked
        # from, with #705 already in it — on the real Workflow over FakeAPI: ownerless card, all
        # 7 stages x the 5 ownership-gated forms (advance x2, call_human, return_task, decompose),
        # plus a control round on a card owned by SOMEBODY ELSE and a mover round over all 8
        # card-moving calls. It is what MOTIVATES the map, not what the code does now, and saying
        # so is this card's own second-pass finding: read undated under a "#734 widened it"
        # heading, 12 of its 35 cells contradict the module they sit in. AFTER the change every
        # `bare` below outside the Queue row reads `clause`; nothing else moved THEN. #742 later
        # moved the one thing #734 left alone — the CONTROL round, the foreign card, which now
        # carries `_OTHER_OWNER_EXIT` in the same six rows — so read the table for the ownerless
        # card it tabulates and not as a picture of the module.
        #
        #   Backlog    bare bare STAGE-GATE bare bare      claim REFUSED  movable-by-agent: none
        #   Queue      bare bare STAGE-GATE bare bare      claim OK       claim -> Design
        #   Design     clause x5                           claim REFUSED  none
        #   Build      clause x5                           claim REFUSED  none
        #   Review     bare bare STAGE-GATE x3             claim REFUSED  review_task(needs_work)
        #                                                                  -> Queue
        #   Your Call  bare bare STAGE-GATE bare bare      claim REFUSED  none
        #   Done       bare bare STAGE-GATE x3             claim REFUSED  none
        #
        # Three things in that table decide the wording, and each kills a tempting shortcut:
        # (1) #705's own clause CANNOT be copied outward — it says "advance, call_human,
        # return_task and decompose all refuse it identically", which is true ONLY in
        # Design/Build; elsewhere call_human (and in Review/Done also return_task and decompose)
        # answers with its own stage gate instead. (2) "Only a human can move it back" is true in
        # Backlog/Design/Build/Your Call/Done and FALSE in Review, the one non-Queue stage an
        # agent moves an ownerless card out of. (3) The three stages this card is titled for are
        # not one case: Backlog is NORMAL (return_task produces it every day), Your Call is
        # ANOMALOUS (call_human keeps the assignee, so a parked card should have one) and Done is
        # TERMINAL (human-only both ways, same door #626/#649 already point at). So the exit
        # sentence is per stage — `_OWNERLESS_EXITS`, above.
        #
        # "No assignee at all" is asked of the RE-READ list, not of the board copy (#885): a card
        # whose kanban copy lost its assignees is not ownerless, and giving it the ownerless exit
        # would be the wrong advice twice over — it would name a human hand-placement that never
        # happened, and it would fire on a card this method has just decided is somebody's.
        #
        # What is NOT claimed: that unfollowable advice is now impossible. What used to close this
        # paragraph — "somebody ELSE's card keeps the bare message in every stage, deliberately" —
        # is no longer true and no longer the decision: #742 gave the foreign card its own clause
        # outside Queue, so the ONE refusal left bare is a Queue card, where the advice works
        # (ownerless) or is answered by claim itself (owned). And the table is today's tool set,
        # not a law (#649/#662).
        #
        # REACHABILITY is not uniform across the six, which is why Backlog's exit reads "this is
        # normal" and Design/Build's reads "tell a human". Reaching the DESIGN/BUILD branch takes
        # a human hand-placing an unassigned card there: review_task(needs_work) used to be the
        # tool that produced it and now bounces such a card to Queue — routing on a re-read, so
        # the mid-call window is closed too — and claim's vanish-window guard refuses before its
        # own move. SWEPT rather than reasoned (#705): all 12 registered tools (14 forms) run from
        # each of the 7 stages with the card assigned and unassigned, and no landing leaves a card
        # ownerless in Design/Build. BACKLOG is the opposite: `return_task` parks a card there and
        # clears the assignee in the same call, so an agent produces that state itself, daily —
        # driven through the real tool in the test, not argued from the source. Your Call and Done
        # are hand-placements like Design/Build; Review takes the same human hand, and clearing
        # the assignee on a card under review is the route #705's own race test already exercises.
        # None of that was re-swept per stage here. All of it is a measurement of today's set, not a
        # law: a future tool that moves a card without checking assignees produces the state
        # again, and nothing here would catch it (the open-class caveat #649 records, #662).
        exit_advice = _OWNERLESS_EXITS.get(stage or "")
        if exit_advice and not assignees:
            msg += (
                f" — except that is UNFOLLOWABLE here: this card has NO assignee at all and is "
                f"already in {stage}, and claim() works only from Queue"
            ) + exit_advice
        elif assignees and stage and stage != "Queue":
            msg += _OTHER_OWNER_EXIT
        raise WorkflowError(msg)

    @staticmethod
    def _ref(task: dict) -> str:
        """Readable task reference for agents to echo: the Vikunja identifier
        (project prefix + per-project index, e.g. 'VMCP-27') plus the global id in
        parens -> 'VMCP-27 (82)'.

        NEITHER HALF IS A SEARCH KEY. The wording that used to stand here — "a human
        searches the tracker by the identifier; the bare global id (#82) is not
        searchable" — was the whole justification for this feature and for SKILL.md's
        "echo the ref, never fabricate one", and it was never measured; #735 then copied
        it into three more surfaces. Measured on a real 2.3.0 (#757) on BOTH surfaces,
        because the API is not the one the claim is about: `?s=TGT-3` returns 0 hits
        from REST *and* from the web UI's quick-action search, while a word from the
        title returns hits in both — same session, same technique — and
        `filter=identifier` is a 400, "The task field 'identifier' is invalid". What
        `s=` does honour is the INDEX — and NOT project-scoped, which is what makes it
        useless as a reference: measured on two projects holding three tasks each, `s=#3`
        returns TWO cards, one per project ('TGT-3' and 'SEC-3'). Scoping it needs a
        second term the reader does not have (`filter=index = 3 && project = 4`).

        So the two halves earn their places in DIFFERENT ways, which is why the ref is
        echoed as a pair. The identifier is READABLE: the UI prints 'TGT-3' as the task
        page's h1, so a human reads the project and the ordinal off the card itself and
        can eyeball whether a quoted ref matches the card in front of them. The global
        id is ADDRESSABLE: `/tasks/82` opens the card, and that is the link the UI's own
        task lists carry. That also SHARPENS the anti-fabrication rule rather than
        weakening it — a made-up identifier cannot be checked by searching for it, so
        the id beside it is the reader's only cheap check.

        Vikunja already returns `identifier` on every task read (a project with no
        prefix yields '#<index>', which we keep); falls back to '#<id>' if absent."""
        ident = (task.get("identifier") or "").strip()
        return f"{ident} ({task['id']})" if ident else f"#{task['id']}"

    @staticmethod
    def _summary(task: dict) -> dict:
        summary = {
            "id": task["id"],
            "ref": Workflow._ref(task),
            "title": task["title"],
            "priority": task.get("priority", 0),
            "description": (task.get("description") or "")[:500],
        }
        # #1640: the `icebox` label's ONLY job — it is not a gate (see LABEL_ICEBOX), it is an
        # effort budget delivered to whoever ends up working the card. Present only when the
        # label is, so every ordinary card's payload is byte-for-byte what it was; the value is
        # the instruction itself rather than a bare True, because the reader is an agent and a
        # flag it has to look up somewhere else is a flag it will not honour.
        if Workflow._has_label(task, LABEL_ICEBOX):
            summary["icebox"] = ICEBOX_HINT
        return summary

    # --- тулзы ---
    def next_task(self, exclude: list[int] | None = None) -> dict:
        # READ-ONLY BY CONTRACT: next_task never writes (no comments, moves, labels, assigns) —
        # its whole call inventory is GETs: me / kanban_view / view_tasks (which itself probes
        # GET /info once, cached, for the page size) / get_task / comments — pinned by
        # test_claimable_cmd.test_the_check_makes_no_writes. The hgdev-acp hub polls it
        # per loop tick via `vikunja-mcp claimable` (see claimable_cmd.py) as its pre-launch idle
        # check, so a side effect added here becomes a per-poll tracker mutation on every repo in
        # the fleet. If one is ever genuinely needed, decouple the claimable verdict first.
        #
        # light board: only the stages next_task reads need be complete — don't page the
        # unbounded Done exhaustively on every call (#43). _my_active_tasks(raw) reuses this
        # same fetch (Design/Build are in NEXT_TASK_STAGES, so they're complete).
        raw = self._board(require_titles=NEXT_TASK_STAGES)
        board = {b["title"]: (b.get("tasks") or []) for b in raw}
        my_id = self._me()["id"]
        # ONE neighbour-board memo for the WHOLE call (#1199). Every _unfinished_predecessors
        # IN THIS METHOD shares it (claim/advance sit lower in the file and deliberately do not —
        # they resolve one card), so M gated candidates blocked on one sibling project pay ONE
        # exhaustive read of that project's board per next_task rather than one EACH — and this
        # is the tool `vikunja-mcp claimable` runs on every hub poll tick, against cards that
        # stay parked for days. Owned here rather than inside the helper because that helper is
        # called once per candidate; a memo scoped to it spans predecessors within a candidate
        # and never candidates within a call, which was exactly the gap.
        foreign_boards: dict[int, dict[int, str] | None] = {}

        mine = self._my_active_tasks(raw)
        # parallel drain: `exclude` names the tasks the CALLER already has a live
        # agent on. The tracker cannot know sub-agent liveness — that is a fact of the harness,
        # not of the board — so the pump states it. ALL FOUR task-bearing branches consult it —
        # resume, stuck-in-Queue, the review offer, and (since #1202) the free queue — so an
        # excluded id is never OFFERED by any of them, while still OCCUPYING its slot: it is real
        # work in progress. On a fresh tick after a killed turn the set is empty and the
        # abandoned task correctly resurfaces as resume (the crash-recovery path).
        #
        # THE FREE-QUEUE FILTER IS THE ONE THAT WILL LOOK REDUNDANT — do not re-derive it away.
        # From #522 until #1202 this very paragraph argued that branch "never reads it and does
        # not need to", because an excluded id is assigned to the caller and the assignee filter
        # drops it anyway. That premise is REFUTED, and refuted BY CONSTRUCTION, one stand per
        # branch (the measurement itself is at the split): `exclude` states SUB-AGENT LIVENESS,
        # not assignment — SKILL.md instructs putting an UNASSIGNED Queue id into it ("claim
        # REFUSED — the id goes into exclude until the end of the tick"), and a human can clear
        # an assignee while an agent is live.
        #
        # SO `exclude` IS A QUEUE FILTER — a property #1202 BOUGHT knowingly, and the reverse of
        # what this paragraph used to claim ("it never narrows WHICH free work is offered, and a
        # caller learns nothing about the rest of the queue"). It does narrow it, and a caller
        # can therefore ENUMERATE the free queue by excluding what it has already seen — which is
        # exactly what #1202's reporter was doing when they hit the bug. Written HERE because it
        # is written nowhere else: `docs/dossier/workflow.md` never mentions `exclude`, and
        # server.py's `next_task` docstring addresses the AGENT, not whoever edits these filters.
        excluded = set(exclude or [])
        limit = self._effective_wip_limit()
        wip = {
            "active": len(mine),
            "limit": limit,
            "free": max(0, limit - len(mine)),
        }

        def with_wip(result: dict) -> dict:
            result["wip"] = wip
            # `language` rides beside `wip` (#1165) and for the same reason: both are project
            # policy the agent cannot read off the board. This one is the LARGER half of the
            # feature — the spec, worklog and review report are the bulk of a card's text and
            # this tool does not write them, so the key has to reach the agent as an instruction.
            # Here rather than at each return for the same reason `wip` is here: every
            # task-bearing branch and every empty/starving/cycle signal goes through this wrapper.
            result["language"] = self.language
            # `siblings` rides here for the third time on the same reasoning (#1179): project
            # policy the agent cannot read off the board. It is the LOAD-BEARING half of the
            # registry — an agent in dogiators-front had no way to learn a dogiators-backend
            # existed, let alone that it was id 17, because its own toml named neither. Without
            # this key `handoff`/`transfer_task` are addressable only by a number nobody can
            # discover, which is indistinguishable from not shipping them.
            result["siblings"] = dict(self.siblings)
            return result

        offerable = [st for st in mine if st[1]["id"] not in excluded]
        if offerable:
            # rework-first ordering (option C, epic #94, mechanism 3): when I hold TWO+ active
            # tasks from one chain, hand back the one that is a PREDECESSOR of another of my
            # active tasks BEFORE its successor — even when the successor outranks it by priority
            # — so I finish the unblocking rework, not the shinier successor (whose advance→review
            # is latched anyway, mechanism 2). Both tasks being active ⇒ both below Review ⇒ the
            # predecessor surfaces in _unfinished_predecessors; keys off follows/blocked only,
            # never parenttask. Computed only for 2+ active tasks — the common 0/1-active path
            # keeps a plain -priority sort and makes zero extra get_task calls. active_ids is
            # built from ALL of `mine`, not `offerable`: if I hold both a predecessor (excluded —
            # another agent is live on it) and its successor, the successor must still rank as
            # rework-first-blocked-by-that-predecessor; filtering active_ids to offerable would
            # silently lose that ordering.
            rework_first: set[int] = set()
            if len(mine) > 1:
                active_ids = {t["id"] for _s, t in mine}
                for _s, t in mine:
                    for pred in self._unfinished_predecessors(
                        t["id"], board=raw, foreign_boards=foreign_boards,
                    ):
                        if pred["id"] in active_ids:
                            rework_first.add(pred["id"])
            offerable.sort(key=lambda st: (
                0 if st[1]["id"] in rework_first else 1, -st[1].get("priority", 0)
            ))
            stage, task = offerable[0]
            note = (
                "this is your active task — don't claim a new one. First reconcile "
                "the actual state: read the dossier (get_task) and check the "
                "code/repo — the work may already be done in full or in part. "
                "Done — verify it and advance(to='review') with honest evidence; "
                "not — continue from where it left off"
            )
            # over-budget disclosure (tracker #529). The WIP limit gates claim(), it is not an
            # invariant on the active count: review_task(verdict='needs_work') moves a card
            # Review->Build, and a human moves one out of Your Call (or hand-places an assigned
            # card), both WITHOUT passing the gate — deliberately, since rework must be
            # receivable at the limit or reviewed work strands. So active > limit is a correct
            # state, and this branch is exactly where it surfaces: the card being handed back is
            # typically the rework that caused it. `free` is max(0, limit - active) and so cannot
            # show it — "exactly full" and "over budget by two" are both free: 0 — while the
            # rulebook teaches the pump to branch on `free`. Appended ONLY when active > limit,
            # so the common case is byte-for-byte the old note (no noise), mirroring the
            # wip_saturated message, which already puts both numbers side by side in prose.
            # Pure string building: next_task stays READ-ONLY BY CONTRACT (see the top of this
            # method) — nothing here touches the tracker.
            if wip["active"] > limit:
                note += (
                    f". NOTE — you hold {wip['active']} active tasks against a limit of "
                    f"{limit}: that is legitimate, NOT board corruption. The limit gates "
                    f"claim(); a card bounced back by review_task(verdict='needs_work') or "
                    f"moved out of Your Call by a human re-enters Build without passing it, "
                    f"and rework outranks a fresh claim. Drain the rework — the overshoot "
                    f"clears when it reaches Review. Don't 'fix' the board and don't "
                    f"call_human about it"
                )
            if wip["free"] == 0:
                # #527: THIS branch returns before the free == 0 slot guard below, so a caller
                # whose `exclude` misses even one in-flight task gets a resume here and never
                # sees wip_saturated — the same board in the same minute answers
                # wip_saturated:true to a complete exclude and "your active task" at free:0 to an
                # incomplete one. That order is DELIBERATE and stays: `vikunja-mcp claimable`
                # calls next_task with an EMPTY exclude, and free == 0 implies
                # len(mine) >= limit >= 1, so the guard is structurally unreachable there — which
                # is exactly what keeps the hub's CLOSED seven-kind enum whole (see
                # claimable_cmd.classify_next: a saturated payload would classify as "empty" and
                # idle every hub loop on a board that still has resumable work). So the fix is
                # not to move the guard but to say HERE what the pump is looking at — the payload
                # is what it reads at the moment of confusion, and a rule in a file it loaded
                # hours ago is weaker. Conditional on free == 0 so the common resume (a free slot,
                # nothing surprising) keeps a byte-identical note.
                note += (
                    ". NOTE: wip.free == 0 AND a resume, with no wip_saturated — saturation is "
                    "only reported once `exclude` names every task you already have a live agent "
                    "on, because your active tasks are offered BEFORE the slot check. So check "
                    "your exclude, not the board: if an agent IS live on this task your exclude "
                    "is incomplete — add this id and call next_task again (that is how the "
                    "saturation signal appears), and do NOT dispatch a second agent onto it. If "
                    "no agent is live on it, this is the ordinary crash-recovery resume"
                )
            return with_wip({
                "resume": True, "stage": stage, "task": self._summary(task),
                "note": note,
            })

        # skip an epic here too: an epic container assigned to me in Queue (only ever a human's
        # doing — decompose parks epics in Backlog with the assignee cleared) is NOT claimable
        # (claim refuses epics below), and this stuck branch outranks the free queue, so handing
        # it back as a "call claim to finish" instruction would LIVELOCK the pump on an
        # unclaimable card and starve real work. Keys off the epic LABEL, never subtask structure;
        # this is not a false-skip of "really my active work" — an epic container is never one.
        stuck = [
            t for t in board.get("Queue", [])
            if my_id in self._assignee_ids(t)
            and not self._has_label(t, LABEL_EPIC)
            and t["id"] not in excluded
        ]
        if stuck:
            stuck.sort(key=lambda t: -t.get("priority", 0))
            note = (
                "this task in Queue is assigned to you (by a human or an unfinished "
                "claim) — call claim(task_id) to finish moving it into Design"
            )
            # #571: this branch, like the resume above, returns BEFORE the free == 0 slot guard,
            # so at zero free slots it hands back an instruction the pump cannot carry out —
            # claim() is exactly what the WIP gate refuses ("WIP limit reached"). Deliberately a
            # VARIANT of #527's clause and not that text: reaching this branch PROVES `offerable`
            # was empty, i.e. every active task of the caller is ALREADY named in `exclude`, so
            # "your exclude may be incomplete" — the ambiguity #527 answers on the resume branch —
            # cannot arise here. The useful fact is the other one: the instruction above is
            # un-followable right now, no wip_saturated came with it (this branch outranks the slot
            # check), and the way to surface saturation is to exclude THIS id for the rest of the
            # tick and ask again — the same "claim ОТКАЗАЛ — id в exclude до конца тика" move
            # SKILL.md already teaches. Nothing is claimed, so the card must NOT be dispatched onto:
            # it stays claimable once a slot frees. The branch ORDER is again NOT what gets fixed,
            # for #527's reason — `vikunja-mcp claimable` calls next_task with an EMPTY exclude and
            # its closed kind enum depends on this order. The over-budget clause stays on the resume
            # branch (#529's slice): the card here is not the rework that caused an overshoot.
            # Pure string building — next_task stays READ-ONLY BY CONTRACT (see the top of this
            # method). Conditional on free == 0 so the ordinary stuck claim keeps a byte-identical
            # note.
            if wip["free"] == 0:
                note += (
                    ". NOTE: wip.free == 0, so claim(task_id) will be REFUSED right now (\"WIP "
                    "limit reached\") — the slot gate stands between this instruction and Design. "
                    "And no wip_saturated is reported because this branch is offered BEFORE the "
                    "slot check, so the state is read from your own set, not the board: put this "
                    "id in `exclude` for the rest of the tick and call next_task again — that is "
                    "how the saturation signal appears. Do NOT dispatch an agent onto it: nothing "
                    "has been claimed, and the card stays claimable once a slot frees"
                )
            return with_wip({
                "resume": True, "stage": "Queue", "task": self._summary(stuck[0]),
                "note": note,
            })

        # independent-review pull path (#117): offer ANY task in Review awaiting review —
        # not just bug fixes — EXCEPT an epic container (label epic), whose code lives in its
        # children (each reviewed on its own advance), so there is nothing to review here. The
        # epic skip keys off the LABEL, never the presence of subtasks (same migration-guard
        # principle as the sequence gate). Two guards keep the pump safe: skip a task whose
        # verdict is fresher than its last report (else an already-reviewed card is handed back
        # forever and the queue never advances — the freshness check just below), and, WHERE THE
        # REPO ASKED FOR IT, skip one assigned to the caller.
        #
        # AUTHORSHIP IS READ OFF `require_review_independence`, THE SAME FLAG `review_task`
        # GATES ON (#991). Until then this skip was UNCONDITIONAL, and the two tools disagreed:
        # next_task refused to OFFER a card whose verdict `review_task` would accept without a
        # murmur (pinned by test_off_by_default_a_self_verdict_is_still_accepted). In a SOLO
        # setup — one scoped token for the whole fleet, which CLAUDE.md calls the condition of
        # operation, not a degradation — every card in Review is the caller's, so the branch
        # offered NOTHING, ever: measured, three ticks on one such card answered "the queue is
        # empty" three times, and `claimable`'s kind='review' was unreachable in principle, so
        # an external supervisor never woke an agent for a pending review. What the filter could
        # not do there is supply independence: with one token the server cannot tell two
        # contexts apart, so it only blinded the pump. With the flag ON authorship IS a gate, so
        # the skip stays — offering a card `review_task` is about to refuse is worse than
        # silence.
        #
        # THE SORT CARRIES WHAT THE FLAG NO LONGER DOES. `require_review_independence = false`
        # does not mean "solo"; it also means a MULTI-IDENTITY repo that never set the key, and
        # there this filter WAS the only thing keeping an author off their own card (see
        # `_require_review_independence`). So not-mine sorts ahead of mine: someone else's card
        # is offered while any is left, and your own only when none is. A hint, like the filter
        # it replaces — a direct `review_task` call never consulted either — and the repo that
        # wants a gate turns the flag on.
        def _offer_rank(t: dict) -> tuple[bool, int]:
            return (my_id in self._assignee_ids(t), -t.get("priority", 0))

        for t in sorted(board.get("Review", []), key=_offer_rank):
            if t["id"] in excluded:
                continue
            if self._has_label(t, LABEL_EPIC):
                continue
            if self.require_review_independence and my_id in self._assignee_ids(t):
                continue
            # вердикт актуален, только если он свежее последнего отчёта: после цикла
            # needs_work -> доработка -> Review задача должна снова попасть к ревьюеру
            comments = self.api.comments(t["id"])
            # comments are stored as HTML (#85); render back to plain text before matching
            # the leading marker, else "[review]" hides behind a "<p>" wrapper.
            last_review = max(
                (c.get("created") or "" for c in comments
                 if html_to_text(c.get("comment") or "").startswith("[review]")),
                default=None,
            )
            last_worklog = max(
                (c.get("created") or "" for c in comments
                 if html_to_text(c.get("comment") or "").startswith("[worklog]")),
                default="",
            )
            # nothing to review until a work report exists: advance→review always posts a
            # [worklog], so a Review card WITHOUT one was placed there by hand — not a review
            # candidate. This also keeps the sequence gate's bare "predecessor ready at Review"
            # tasks (and any hand-parked card) out of the widened #117 net.
            if not last_worklog:
                continue
            if last_review is not None and last_review >= last_worklog:
                continue
            review_kind = "bug" if self._has_label(t, LABEL_BUG) else "change"
            return with_wip({
                # "stage" on every task-bearing result (see the free-queue branch below): the
                # stage the task was FOUND in, which for a review offer is always Review.
                # classify_next checks `review` BEFORE `resume`/`stage`, so this stays kind
                # "review" — pinned in test_claimable_cmd.
                "review": True, "review_kind": review_kind, "stage": "Review",
                "task": self._summary(t),
                "note": (
                    "this task is waiting for independent review — run it and cast a verdict "
                    "via review_task(task_id, verdict=..., report=...). review_kind='bug': "
                    "reproduce it and confirm the fix closes the CAUSE (not the symptom); "
                    "review_kind='change' (feat/chore/docs/refactor): confirm it does what "
                    "the spec/description said, the tests are real, it stayed in its slice, "
                    "and look for obvious regressions nearby. Do NOT review it if you wrote "
                    "this code in this session"
                ),
            })

        # no free slot -> do not even look at the free queue. This is NOT an empty queue: the
        # pump must WAIT for a dispatched agent to return, not idle the tick. Reported alone —
        # `starving` describes a chain that cannot start, which is not the actionable fact when
        # there is nowhere to put a task anyway (and computing it can cost a board escalation).
        if wip["free"] == 0:
            return with_wip({
                "task": None,
                "wip_saturated": True,
                "message": (
                    f"all {limit} WIP slot(s) are busy ({wip['active']} active) — "
                    f"nothing can be claimed until one finishes"
                ),
                "note": (
                    "NOT an empty queue: wait for a dispatched agent to return, then call "
                    "next_task again. Do NOT claim, and do NOT end the tick / ScheduleWakeup "
                    "as if there were no work"
                ),
            })

        # #126: exhaustive-board escalation for the sequence gate, memoised to AT MOST ONE fetch
        # per next_task. The board above is LIGHT (NEXT_TASK_STAGES omits Backlog/Your Call/Done,
        # #43), so a predecessor absent from it is not provably gone — it may sit in an unpaged
        # bucket. resolve_full lets _unfinished_predecessors consult the full board (the same one
        # claim/advance read) before ruling "not a blocker", so next_task's verdict matches claim's
        # BY CONSTRUCTION, not by keeping bucket-sets in sync by hand. Fetched lazily: when every
        # predecessor is already on the light board (the common case — a ready head sits at Review,
        # which IS in NEXT_TASK_STAGES) it is never called, so next_task still issues exactly one
        # view_tasks (the #43 latency win and the #105 single-fetch measurement both hold).
        full_board: dict[str, list[dict]] = {}

        def resolve_full() -> list[dict]:
            if "board" not in full_board:
                full_board["board"] = self._board()  # exhaustive: all buckets, incl Backlog/YC/Done
            return full_board["board"]

        # Queue-контракт: свободные берём, назначенные на другого НЕ трогаем — это «для людей».
        # epic-контейнер тоже пропускаем (по аналогии с blocked): родитель с меткой epic и живыми
        # детьми — это контейнер, а не работа, клеймить его бессмысленно (ровно баг из #94, где
        # next_task предложил epic-родителя как свободную задачу Queue). Скип цепляется за метку
        # epic, НИКОГДА за наличие подзадач (тот же миграционный принцип, что у гейта
        # последовательности): у обычной задачи тоже может быть подзадача, и она обязана остаться
        # клеймабельной.
        # `excluded` IS read here (#1202), and the comment that used to say it need not be was
        # the defect. It argued: an excluded id is by definition a task the caller already holds,
        # i.e. ASSIGNED, so the assignee filter below drops it anyway. The premise is false —
        # `exclude` states SUB-AGENT LIVENESS, not assignment, and SKILL.md itself instructs
        # putting an UNASSIGNED Queue id into it ("claim REFUSED — the id goes into exclude until
        # the end of the tick"); a human can also clear an assignee while an agent is live.
        # MEASURED BY CONSTRUCTION over FakeAPI, one stand per branch, before anything changed: a
        # free unassigned Queue card came BACK though it was excluded, while the SAME card
        # claimed (Design), assigned-but-still-in-Queue, and in Review was withheld — so the
        # discriminator is the BRANCH, never the id, the list length or its order.
        offerable_queue = [
            t for t in board.get("Queue", [])
            if not self._assignee_ids(t)
            and not self._has_label(t, LABEL_BLOCKED)
            and not self._has_label(t, LABEL_EPIC)
        ]
        # split rather than filtered in one pass: `withheld` must be exactly the candidates
        # dropped BY `exclude` and by nothing else, or the signal below would report an assigned
        # or blocked card as "you already have this one".
        withheld = [t for t in offerable_queue if t["id"] in excluded]
        queue = [t for t in offerable_queue if t["id"] not in excluded]
        queue.sort(key=lambda t: -t.get("priority", 0))
        # hard sequence gate (option C, epic #94) — free-queue half: a free task whose
        # predecessor is still unfinished (below Review) is NOT yet claimable; skip it and offer
        # the next one. Keys off follows/blocked only (never parenttask), so an old unordered
        # epic's child stays offered (migration guard, C1). Reuse the ONE board snapshot (raw)
        # already fetched above — never refetch it per candidate (the board fetch isn't cheap).
        # A head returned to Backlog sits on the light board's page-1, so it's seen here; claim's
        # full-board gate backstops the rare Backlog-beyond-page-1 case (never a silent pass).
        gated: list[tuple[dict, list[dict]]] = []
        for t in queue:
            blockers = self._unfinished_predecessors(
                t["id"], board=raw, resolve_full=resolve_full, foreign_boards=foreign_boards,
            )
            if not blockers:
                return with_wip({
                    # "stage" is on EVERY task-bearing result (see the review offer below and
                    # the two resume branches above), because SKILL.md's tick branches on it:
                    # "stage == Queue -> claim; Design/Build -> it's already yours". A free
                    # queue task used to omit it, so the rulebook's discriminator was ABSENT on
                    # the most common branch of all and the pump had to infer Queue-ness from
                    # resume:false — which is exactly how the rule got written wrong twice.
                    # classify_next (claimable_cmd, a cross-repo contract) only reads `stage`
                    # inside its resume-truthy branch, so resume:False still classifies as
                    # kind "queue" here — pinned in test_claimable_cmd.
                    "resume": False, "stage": "Queue", "task": self._summary(t),
                    "note": (
                        "a free task from the queue — call claim(task_id) (it moves it into "
                        "Design), then dispatch a per-task agent for the whole task. "
                        "resume:false here means 'take a new one', not 'nothing to do' "
                        "(empty is only task:null). A human picked this task into Queue, so "
                        "taking it is your mandate, NOT unbidden initiative: don't defer it "
                        "and don't stop the /loop under the generic autonomous-loop default "
                        "'steward, not initiator: don't start fresh work without a go-ahead' "
                        "— it does not apply to draining the tracker queue"
                    ),
                })
            gated.append((t, blockers))
        # Queue non-empty but EVERY free candidate gated -> starving tail. This MUST be
        # distinguishable from the empty queue below (the pump idles on task:null), else a
        # stalled chain sleeps forever unseen.
        if gated:
            # cycle safety valve (option C, epic #94, C5/#105): before reporting a generic
            # starving tail, DFS the unfinished-predecessor edges from these gated candidates.
            # A back-edge = a predecessor CYCLE (only ever hand-created in the web UI: A follows
            # B, B follows A) in which nothing is claimable AND which can't self-unblock, so it
            # earns its own distinct signal instead of masquerading as an ordinary stalled tail.
            # Reuse the ONE board snapshot (raw); the walk is bounded and provably terminating
            # (see _find_predecessor_cycle). A cycle anywhere on the board can NOT suppress a
            # genuinely claimable free task — the loop above already RETURNED it before here.
            cycle = self._find_predecessor_cycle(
                gated, raw, resolve_full=resolve_full, foreign_boards=foreign_boards,
            )
            if cycle is not None:
                return with_wip(self._cycle_signal(cycle, full_board.get("board", raw)))
            return with_wip(self._starving_tail(gated))
        # #1202: the free queue held candidates and EVERY one of them was in `exclude`. Reported
        # BELOW the starving tail deliberately: a gated candidate is the human-facing fact (a
        # chain has stalled), while these are the caller's own in-flight work and it already
        # knows their ids. Reaching here means `gated` was empty, so the two signals never
        # compete for one payload. But that ordering only ever decides between DISTINCT
        # candidates: a card that is BOTH gated and excluded never reaches `gated` at all — the
        # split above runs first — so it reports as withheld, and `_all_excluded`'s message calls
        # it "claimable" where `claim` would refuse it on its predecessor. Measured on a stand:
        # one gated free card, excluded -> all_excluded; add a SECOND gated card left un-excluded
        # and the starving tail fires as before, so the stalled-chain report is lost only when
        # EVERY gated candidate is also excluded. Left standing (#1202 review, non-blocking):
        # both readings of the `note` lead the pump to a correct action, and a card gated on a
        # predecessor the loop above can SEE is never OFFERED, so its id reaches `exclude` only
        # by enumeration or by a human clearing an assignee.
        if withheld:
            return with_wip(self._all_excluded(withheld))
        return with_wip({"task": None, "message": "the queue is empty — no work for the agent"})

    def _all_excluded(self, withheld: list[dict]) -> dict:
        """The free queue held only cards the CALLER itself named — NOT an empty queue (#1202).

        WHAT WAS BROKEN. Three of the four task-bearing branches consulted `excluded`; the
        free-queue one did not, on the reasoning quoted at its filter, so an excluded id came
        back as a fresh offer. Measured by construction over `FakeAPI`, one stand per branch:
        the same card came BACK when free and unassigned in Queue, and was WITHHELD when
        claimed into Design, when assigned but still in Queue, and when in Review.

        WHY A DISCRIMINATOR AND NOT THE EMPTY-QUEUE RESULT. Spelling this "the queue is empty"
        would be the same class of lie the card is about — the queue is not empty, it is full of
        work this caller already has in hand. The additive shape on a `task: null` payload is
        what `wip_saturated`, `starving` and `cycle` already established here.

        WHAT THE CALLER MUST DECIDE, because this tool cannot. Both readings are legitimate and
        they differ in what the pump should DO: if an agent is live on each withheld id, this is
        "wait for one to return", exactly like `wip_saturated`; if these are ids excluded because
        `claim` REFUSED them, the tick is genuinely done and yielding is right. The tracker
        cannot tell sub-agent liveness — that is the whole reason `exclude` is passed IN — so the
        `note` states both and the caller reads its own set.

        SAFE FOR THE CROSS-REPO CONTRACT. `claimable_cmd.classify_next` branches on `cycle` and
        `starving` and otherwise answers kind "empty", so this adds a KEY and no KIND — and
        `vikunja-mcp claimable` calls `next_task` with an EMPTY exclude, which makes this branch
        structurally unreachable there. Both pinned rather than reasoned."""
        return {
            "task": None,
            "all_excluded": True,
            "withheld": [self._summary(t) for t in withheld],
            "message": (
                f"the free queue holds {len(withheld)} claimable task(s) and EVERY one of them "
                f"is in your `exclude` — nothing left to offer. This is NOT an empty queue"
            ),
            "note": (
                "you excluded all of it, so only YOU can say what this means. If a live agent "
                "holds each of these ids: treat it like wip_saturated — wait for one to return "
                "and call next_task again, do NOT end the tick. If they are ids you excluded "
                "because claim REFUSED them, nothing here will change this tick: go on to the "
                "rest of your tick and yield as on an empty queue. Do NOT dispatch onto a "
                "withheld id, and do NOT drop ids from `exclude` to make work appear"
            ),
        }

    def _starving_tail(self, gated: list[tuple[dict, list[dict]]]) -> dict:
        """The distinguishable "everything is blocked" signal — NOT the empty queue.

        Returned only when the free Queue is NON-empty yet EVERY candidate is gated by an
        unfinished predecessor. It must NOT look like the empty-queue result ({task:None +
        "the queue is empty"}): the pump's /loop treats a bare empty queue as "ScheduleWakeup
        and idle", so a starved tail reported as empty would sleep forever and nobody would
        learn the chain stalled. `task` stays None (nothing to claim), but the additive
        discriminators — starving/waiting_count/needs_retriage — let a caller BRANCH, and
        `waiting` names each blocked task with the predecessor holding it. Special case: a
        predecessor sitting in Backlog is a chain HEAD sent back by return_task (label blocked,
        assignee cleared); its whole tail stalls until a human re-triages it — flagged
        needs_retriage and spelled out in the message, never left a mystery. (A predecessor
        CYCLE among these same gated candidates is caught earlier, by _find_predecessor_cycle
        (C5/#105), which returns its own distinct signal — so reaching here means the gate is
        acyclic: an honest starving tail, not a loop.)"""
        waiting = [
            {
                "task": self._summary(task),
                "blocked_by": blockers,
                "needs_retriage": any(b["stage"] == "Backlog" for b in blockers),
            }
            for task, blockers in gated
        ]
        retriage = [w for w in waiting if w["needs_retriage"]]
        lines = [
            f"{w['task']['ref']} ← "
            + "; ".join(
                f"{b['ref']} in '{b['stage']}'"
                + (" [sent back to Backlog via return_task — needs human re-triage]"
                   if b["stage"] == "Backlog" else "")
                for b in w["blocked_by"]
            )
            for w in waiting
        ]
        message = (
            f"{len(waiting)} queued task(s) can't be claimed — each waits on an unfinished "
            f"predecessor (a predecessor is 'ready' only at Review or Done). This is NOT an "
            f"empty queue. Waiting: " + " | ".join(lines)
        )
        if retriage:
            message += (
                f". {len(retriage)} of these are stalled behind a chain HEAD returned to "
                f"Backlog (return_task) — a human must re-triage the head before the tail "
                f"can resume."
            )
        # #1190: the same clause shape as `retriage` above, for the same reason, on the other
        # kind of tail that does NOT self-clear. next_task SKIPS a gated card rather than
        # refusing it, so a card parked by `handoff` behind an unresolvable predecessor never
        # produces claim's refusal under an ordinary /loop drain — this message is the only
        # place its human is ever told anything. Conditional, so the plain starving message is
        # byte-for-byte what it was (pinned wholesale in test_workflow_sequence_gate).
        escapes = self._predecessor_escapes([b for w in waiting for b in w["blocked_by"]])
        if escapes:
            message += f". {escapes}"
        # #1640: the same additive-clause shape once more, for the third kind of tail that does
        # not self-clear. It matters MOST here of the three places a frozen predecessor can be
        # named: claim's refusal reaches an agent that asked for this card by id, while an
        # ordinary /loop drain never claims a gated card at all — it skips it — so without this
        # sentence a chain frozen behind an iceboxed head is a queue that quietly never moves.
        frozen = self._predecessor_frozen([b for w in waiting for b in w["blocked_by"]])
        if frozen:
            message += f". {frozen}"
        return {
            "task": None,
            "starving": True,
            "waiting_count": len(waiting),
            "needs_retriage": bool(retriage),
            "waiting": waiting,
            "message": message,
            "note": (
                "NOT an empty queue: the free Queue is non-empty but every task is gated by an "
                "unfinished predecessor, so nothing is claimable right now. Do NOT treat this "
                "as 'nothing to do' — surface it so a human sees the stalled chain, then "
                "ScheduleWakeup and re-check later. When needs_retriage is set, a chain head "
                "was returned to Backlog and a human must re-triage it before the tail resumes."
            ),
        }

    def _find_predecessor_cycle(
        self, gated: list[tuple[dict, list[dict]]], board: list[dict],
        resolve_full: Callable[[], list[dict]] | None = None,
        foreign_boards: dict[int, dict[int, str] | None] | None = None,
    ) -> list[int] | None:
        """DFS over UNFINISHED-predecessor edges from the gated Queue candidates; return the ids
        on the first cycle found (a back-edge into the current path), else None. A cycle can only
        be introduced by a human hand-editing follows/blocked relations in the web UI (an ordered
        decompose builds a linear, acyclic chain), and when it happens every task in the loop has
        an unfinished predecessor, so nothing is claimable — otherwise indistinguishable from a
        plain starving tail. This runs inside next_task, the pump's own tool, on every idle tick,
        so it MUST terminate and MUST NOT hang: the walk is ITERATIVE (no recursion limit) and
        each node enters the path at most once (guarded by `visited`/`on_path`), so it is bounded
        by the reachable unfinished subgraph. A malformed self-referential relation (A follows A)
        surfaces the node as its own predecessor and is reported as a 1-cycle, never an infinite
        loop. `visited` and `on_path` are SEPARATE sets — a node re-reached off the current path
        (a diamond/converging DAG) is pruned, NOT mistaken for a cycle (the false-positive guard).
        Bounded to unfinished (below-Review) predecessors — the exact edges the gate reads, never
        the whole board. The blockers next_task already computed for the roots seed the edge
        cache, so their get_task calls aren't repeated; deeper nodes are fetched lazily and
        memoized (each expanded at most once). Reuses the ONE board snapshot passed in."""
        preds_cache: dict[int, list[int]] = {
            t["id"]: [b["id"] for b in blockers] for t, blockers in gated
        }

        def preds(tid: int) -> list[int]:
            if tid not in preds_cache:
                preds_cache[tid] = [
                    p["id"] for p in self._unfinished_predecessors(
                        tid, board=board, resolve_full=resolve_full,
                        foreign_boards=foreign_boards,
                    )
                ]
            return preds_cache[tid]

        visited: set[int] = set()  # fully explored, proven not to reach a cycle -> never re-walked
        for root, _blockers in gated:
            if root["id"] in visited:
                continue
            path: list[int] = []       # the CURRENT dfs path, in order
            on_path: set[int] = set()  # its membership -> a hit here is a back-edge (a cycle)
            # explicit stack of (node, iterator-over-its-unfinished-predecessors)
            stack: list[tuple[int, Any]] = [(root["id"], iter(preds(root["id"])))]
            path.append(root["id"])
            on_path.add(root["id"])
            while stack:
                node, it = stack[-1]
                descended = False
                for child in it:
                    if child in on_path:
                        return path[path.index(child):]  # back-edge -> the loop is this slice
                    if child in visited:
                        continue  # already proven cycle-free -> prune, do NOT flag (diamond guard)
                    stack.append((child, iter(preds(child))))
                    path.append(child)
                    on_path.add(child)
                    descended = True
                    break
                if not descended:  # node's predecessors exhausted with no back-edge -> finish it
                    stack.pop()
                    path.pop()
                    on_path.discard(node)
                    visited.add(node)
        return None

    def _cycle_signal(self, cycle_ids: list[int], board: list[dict]) -> dict:
        """The distinguishable "a predecessor CYCLE makes everything unclaimable" signal — a THIRD
        state beside the empty queue and the plain starving tail. A cycle (A follows B, B follows
        A — only ever hand-created in the web UI) can't self-unblock: every task in it waits on
        another, so unlike a starving tail (which clears once a head reaches Review) ONLY a human
        can break it, by removing one follows/blocked link. `task` stays None (nothing to claim);
        `cycle`/`cycle_tasks` are the additive discriminators; the message and note NAME the
        looping tasks and tell the caller to surface it to a human, NOT to read it as 'nothing to
        do' and just sleep. Reuses the passed board snapshot to resolve each id to ref/title/stage
        (a member gone from the board falls back to '#<id>', never crashing)."""
        task_by_id = {
            t["id"]: (t, bucket["title"])
            for bucket in board for t in (bucket.get("tasks") or [])
        }
        nodes: list[dict] = []
        for tid in cycle_ids:
            found = task_by_id.get(tid)
            if found is None:
                nodes.append({"id": tid, "ref": f"#{tid}", "title": "?", "stage": "?"})
            else:
                task, stage = found
                nodes.append(
                    {"id": tid, "ref": self._ref(task), "title": task["title"], "stage": stage}
                )
        # render the loop CLOSED (A → B → A) so a 2-cycle and a self-loop both read unambiguously
        loop = " → ".join([n["ref"] for n in nodes] + [nodes[0]["ref"]])
        detail = "; ".join(f"{n['ref']} in '{n['stage']}'" for n in nodes)
        message = (
            f"PREDECESSOR CYCLE — {loop}: {len(nodes)} task(s) wait on each other "
            f"(their follows/blocked relations form a loop), so NOTHING in the cycle is "
            f"claimable and the chain will NOT unblock itself. This is NOT an empty queue and "
            f"NOT an ordinary starving tail: only a human can break it, by removing one "
            f"follows/blocked relation in the web UI. Tasks in the cycle: {detail}"
        )
        return {
            "task": None,
            "cycle": True,
            "cycle_tasks": nodes,
            "message": message,
            "note": (
                "a predecessor CYCLE (hand-edited follows/blocked relations form a loop) makes "
                "every task in it unclaimable and it can NOT self-unblock — distinct from a plain "
                "starving tail. Do NOT treat this as 'nothing to do' and just ScheduleWakeup: "
                "surface it to a human (call_human) to break the cycle by removing one "
                "follows/blocked link in the web UI. Nothing in the loop moves until they do."
            ),
        }

    def claim(self, task_id: int) -> dict:
        board = self._board()
        task, stage = self._find_task(task_id, board=board)
        if stage != "Queue":
            raise WorkflowError(f"task is in '{stage}', you can only claim from Queue")
        # epic containers are not claimable (epic #94 / #118): a card labelled epic is a
        # CONTAINER, not a unit of work — its evidence lives in its children, each claimed and
        # reviewed on its own. Refuse it here (next_task already skips it, but claim must gate too:
        # it otherwise checks only stage==Queue and would take an epic handed in directly), and
        # point the agent at the children. Keys off the epic LABEL, never the presence of subtasks
        # — an ordinary task may have subtasks and MUST stay claimable (the migration guard, same
        # principle as the sequence gate).
        if self._has_label(task, LABEL_EPIC):
            related = self.api.get_task(task_id).get("related_tasks") or {}
            subtasks = related.get("subtask") or []
            kids = ", ".join(self._ref(s) for s in subtasks) or "its subtasks"
            raise WorkflowError(
                f"{self._ref(task)} is an epic CONTAINER (label epic), not a unit of work — "
                f"there is nothing to claim on the container itself. Its code/evidence lives in "
                f"its children, each claimed and reviewed on its own; work on those instead: "
                f"{kids}"
            )
        # hard sequence gate (option C, epic #94): refuse to START a successor while any of its
        # predecessors is unfinished (below Review). claim otherwise checks only stage==Queue, so
        # without this the gate is trivially bypassed by claiming a successor directly. Keys off
        # follows/blocked only (never parenttask) — old epics stay claimable. Reuses the snapshot.
        blockers = self._unfinished_predecessors(task_id, board=board)
        if blockers:
            joined = "; ".join(f"{b['ref']} in '{b['stage']}'" for b in blockers)
            raise WorkflowError(
                f"can't claim {self._ref(task)} yet — it's waiting on an unfinished "
                f"predecessor: {joined}. "
                + self._predecessor_advice(
                    blockers,
                    "A predecessor becomes ready only at Review or Done; finish that one first",
                )
            )
        # WIP slot gate (generalises the #38 single-WIP flag): refuse a claim that would put this
        # token over its allowed number of simultaneously active tasks. It gates THIS transition
        # only — the count itself is not bounded, and a card bounced back into Build takes it over
        # the limit with no claim involved (#529) — always enforced, since
        # _effective_wip_limit always yields a number (an unset wip_limit means DEFAULT_WIP_LIMIT,
        # tracker #524). Reuse the board snapshot claim already fetched — the old code called
        # _my_active_tasks() with no board and paid for a SECOND full board fetch per gated claim.
        limit, limit_origin = self._wip_limit_with_origin()
        active = self._my_active_tasks(board=board)
        if len(active) >= limit:
            names = ", ".join(f"#{t['id']}" for _stage, t in active)
            # Name the KNOB, not just the number (tracker #517): a surprising refusal is the one
            # moment an agent needs to find where the limit is set, and the origin sentence sits
            # AFTER the "(n/m)" parens so the pins matching on that prefix keep working.
            raise WorkflowError(
                f"WIP limit reached ({len(active)}/{limit}) — you already hold {names}. "
                f"That number comes from {limit_origin}. "
                f"Finish one (advance to Review) or return_task it before claiming another"
            )
        existing = task.get("assignees") or []
        me = self._me()
        # self-heal: партиальный клейм (assign прошёл, move — нет) или человек руками
        # вернул заклеймленную задачу в Queue — я тут единственный assignee, долечиваем
        # вместо отказа. Кто-то ДРУГОЙ среди assignees (один или вместе со мной) — отказ как раньше.
        self_heal = len(existing) == 1 and existing[0].get("id") == me["id"]
        if existing and not self_heal:
            names = ", ".join(a.get("username", "?") for a in existing)
            raise WorkflowError(f"already taken ({names}) — grab the next one via next_task")

        if not self_heal:
            self.api.add_assignee(task_id, me["id"])
        fresh = self.api.get_task(task_id)
        fresh_ids = self._assignee_ids(fresh)
        others = [aid for aid in fresh_ids if aid != me["id"]]
        if others:
            self.api.remove_assignee(task_id, me["id"])
            raise WorkflowError("lost the race for this task — grab the next one via next_task")
        # vanish-window: человек мог снять моё назначение в окно между assign и re-read.
        # others пуст — но без меня в assignees move уведёт задачу в Design «ничьей»
        # (невидимо для next_task и незаклеймимо из Queue). Отказ до move закрывает окно
        # и в обычном, и в self-heal пути (там add_assignee не звался — окно то же).
        if me["id"] not in fresh_ids:
            raise WorkflowError(
                "the assignment vanished during the claim (a human removed it) — retry next_task"
            )

        # #693: entering the active pipeline invalidates any prior verdict, exactly as `advance`
        # has done since #119 — a human can hand-place a verdict-carrying card back in Queue with
        # the assignee cleared, and claiming it then walked `reviewed` into Design. The window is
        # narrower than `return_task`'s (the very next `advance(to='build')` clears it anyway), so
        # this is the same rule applied one step earlier rather than a second mechanism. `fresh`,
        # not the board snapshot: `_clear_verdict_labels` only DELETEs links present on the
        # snapshot it is handed, and the board copy is one read older than the labels being removed.
        self._clear_verdict_labels(fresh)
        view = self._view()
        self.api.move_task(self.project_id, view["id"], self._bucket("Design")["id"], task_id)
        self.api.add_comment(
            task_id, f"[claim] {card_text(self.language, 'claim', username=me['username'])}"
        )
        result = {
            "claimed": True, "task": self._summary(fresh),
            "next": "describe your approach and call advance(to='build', spec=...)",
        }
        divergence = self._kanban_assignee_divergence(task_id, me["id"])
        if divergence:
            result["kanban_assignee_divergence"] = divergence
        return result

    def _kanban_assignee_divergence(self, task_id: int, my_id: int) -> str | None:
        """Does the card, read the way `advance` will read it, still show me as its assignee?

        assign-then-verify above verifies through `GET /tasks/<id>`; every LATER ownership gate
        judges by the copy on the KANBAN BOARD, and #885 measured those two disagreeing on a live
        card — so `claim` used to report plain SUCCESS about a state it had never checked, and the
        silence is what turned a rare server quirk into a card no tool could move. This asks the
        second question with the same read the gates use, so the claim's own report names it.

        REPORTS, never refuses. Refusing here would be the loud half of the fix and would leave
        the card unclaimable FOREVER, because nothing on this side can repair the server's copy
        (measured: re-assigning, moving columns and a full read-modify-write all left it empty).
        What makes reporting sufficient is the OTHER half landing with it — `_require_mine`
        re-reads on an empty list, so the card is workable — and what makes it necessary is that
        `claim` must stop being silent about a state it did not verify.

        `require_titles={"Design"}` and not the full board: the card was just moved into Design,
        so THAT bucket is paged exhaustively exactly as `advance`'s own read pages it, and the
        answer for this card is the same one `advance` would get — without paying for the
        exhaustive Done/Backlog read `_board()` otherwise makes (#43).

        Best-effort: a verification must never fail the claim it verifies. Anything raised here —
        including `_find_task` not finding the card, itself a divergence of a kind — is swallowed
        into "could not check", which is reported as no divergence rather than as a false alarm."""
        try:
            board = self._board(require_titles=frozenset({"Design"}))
            kanban_task, _stage = self._find_task(task_id, board=board)
            if my_id in self._assignee_ids(kanban_task):
                return None
        except (WorkflowError, VikunjaError, httpx.HTTPError) as exc:
            _stderr_note_best_effort(
                f"vikunja-mcp: kanban assignee verification skipped for #{task_id}", exc
            )
            return None
        return (
            "the claim SUCCEEDED (GET /tasks/{tid} shows you as the assignee), but the copy of "
            "this card in the KANBAN VIEW came back WITHOUT it — and the kanban copy is what "
            "every later ownership gate reads. Ownership gates re-read the task itself when the "
            "board copy is empty, so this card IS workable; nothing on this side can repair the "
            "server's copy, though (re-assigning, moving columns and a full task rewrite were all "
            "measured leaving it empty), so if a tool still refuses this card as 'not assigned to "
            "you', the known workaround is to re-create the card with the same content and park "
            "the original in Backlog. Say so in your report — this is a Vikunja-side anomaly, "
            "measured once in 31 cards, not something you did"
        ).format(tid=task_id)

    def _move(self, task_id: int, stage: str) -> None:
        self.api.move_task(
            self.project_id, self._view()["id"], self._bucket(stage)["id"], task_id
        )

    def _target_bucket(self, project_id: int, stage: str = "Backlog") -> tuple[int, int]:
        """(view_id, bucket_id) КОЛОНКИ `stage` на ЧУЖОЙ доске — кросс-проектная половина
        file_task. Сознательно ОТДЕЛЬНА от _view/_bucket/_move: те (и их кэши) привязаны к
        self.project_id и питают каждый горячий гейт, а кросс-файлинг — редкое событие
        координации, поэтому здесь свежий kanban_view+buckets на каждый вызов (без кэша ->
        без новой поверхности устаревания). Резолв происходит ДО создания карточки
        (fail-fast): кривой id, недоступный токену проект или не-трекерная доска отказывают,
        НИЧЕГО не осиротив в дефолт-бакете цели. 403/404 заворачиваются в actionable
        WorkflowError с именем цели — граница безопасности ЗДЕСЬ сам скоуп-токен (решает
        Vikunja, мы только внятно показываем отказ). 401 НЕ заворачиваем намеренно: он
        должен дойти до server._tool как VikunjaError, чтобы сработал reload-and-retry
        ротации токена (#140)."""
        try:
            view = self.api.kanban_view(project_id)
            found = self.api.buckets(project_id, view["id"])
        except VikunjaError as exc:
            if exc.status in (403, 404):
                raise WorkflowError(
                    f"can't file into project {project_id}: Vikunja said {exc.status} "
                    f"({exc.message}). Either the token's user has no access to that "
                    f"project (the scoped API token is the security boundary — a human "
                    f"must share the target project with this agent), the project id is "
                    f"wrong, or the project has no kanban board. Nothing was created."
                ) from exc
            raise
        bucket = next((b for b in found if b["title"] == stage), None)
        if bucket is None:
            # #1640 split this one refusal in two, because the two absences mean opposite
            # things. No 'Backlog' says the target is not a tracker board AT ALL; no 'Icebox'
            # says it is a perfectly good board that predates this stage — telling that human
            # their board is "not tracker-managed" would send them to re-run setup on a fear
            # rather than on the reason. Both still refuse BEFORE the card exists.
            if stage == "Backlog":
                raise WorkflowError(
                    f"can't file into project {project_id}: its board has no 'Backlog' "
                    f"column — not a tracker-managed board (run `vikunja-mcp setup` for it "
                    f"first). Nothing was created."
                )
            raise WorkflowError(
                f"can't file into project {project_id}'s '{stage}': that board has no "
                f"'{stage}' column yet — it is a tracker board from before this stage "
                f"existed, and a human must run `vikunja-mcp setup` for it. File without "
                f"icebox=True to reach their Backlog instead. Nothing was created."
            )
        return view["id"], bucket["id"]

    def _mark_epic_if_children_complete(self, child: dict, board: list[dict]) -> None:
        """Best-effort epic-complete marker (#118 Part 2). When THIS child's advance→review makes
        EVERY child of an epic parent ready (Review or Done — READY_STAGES, the same readiness the
        sequence gate uses; NOT a second definition), leave a VISIBLE marker on the EPIC so the
        human sees the container is assembled and can close the set: the LABEL_EPIC_READY label
        (at-a-glance on the board) plus an explanatory comment. It does NOT move the epic — agents
        can't and mustn't (Part 1 made epics unclaimable; only a human moves anything to Done). This
        is deliberately the ADDITIVE form of the cross-task write #103 rejected in its STRUCTURAL
        form: it reaches out of the child's transition to touch a DIFFERENT card, but adds only a
        label + comment — no stage move, no lost work, no gate effect. It MUST therefore be called
        strictly best-effort (the caller swallows every exception): a cosmetic marker on someone
        else's card must never strand the child's own advance, and it adds nothing to the child's
        result. Idempotent — skips if the epic already carries LABEL_EPIC_READY, so a bounced-and-
        re-advanced child never double-marks. Keys off the epic LABEL and the parenttask relation,
        never structure alone. `board` is the full snapshot advance already fetched; the current
        child moved to Review AFTER it was taken, so the child is scored as Review explicitly while
        every other sibling is read from the snapshot."""
        child_id = child["id"]
        related = self.api.get_task(child_id).get("related_tasks") or {}
        parents = related.get("parenttask") or []
        if not parents:
            return  # not a subtask of anything — nothing to mark
        stage_by_id = {
            t["id"]: bucket["title"]
            for bucket in board for t in (bucket.get("tasks") or [])
        }
        for parent in parents:
            # `parent` here is a related_tasks SUB-DICT, and the real server HOLLOWS those — labels/
            # assignees/nested related_tasks come back as None even when the task carries them (only
            # scalars survive; verified on real 2.3.0, #118 rework). So its labels can NOT be read
            # here — doing so silently no-op'd the marker in production while the too-generous fake
            # stayed green (#125). Re-fetch the FULL parent and read labels (both epic and the
            # idempotency marker) off IT. This is the same get_task the sibling read already needs,
            # so it is ZERO extra calls in the epic case (one hoisted, not added); for a non-epic
            # parent it costs +1 get_task, which is fine (best-effort, off next_task's hot path).
            full_parent = self.api.get_task(parent["id"])
            if not self._has_label(full_parent, LABEL_EPIC):
                continue  # parent isn't an epic container — not ours to mark
            if self._has_label(full_parent, LABEL_EPIC_READY):
                continue  # already marked — idempotent (a bounced+re-advanced child won't re-fire)
            siblings = (full_parent.get("related_tasks") or {}).get("subtask") or []
            if not siblings:
                continue
            all_ready = all(
                ("Review" if s["id"] == child_id else stage_by_id.get(s["id"])) in READY_STAGES
                for s in siblings
            )
            if not all_ready:
                continue
            # label FIRST (the idempotency key AND the board marker), THEN the comment: a partial
            # failure (label lands, comment doesn't) still leaves the epic consistently "marked", so
            # a later advance won't double-fire.
            self._add_label(full_parent, LABEL_EPIC_READY)
            self.api.add_comment(
                parent["id"],
                f"[epic-ready] {card_text(self.language, 'epic_ready', children=len(siblings))}"
            )

    def advance(
        self, task_id: int, to: str,
        spec: str | None = None, worklog: str | None = None, evidence: str | None = None,
        root_cause: str | None = None,
    ) -> dict:
        to = (to or "").strip().lower()
        if to == "done":
            raise WorkflowError("only a human moves a task to Done after review — not you")
        if to not in AGENT_ADVANCE:
            raise WorkflowError(f"invalid transition '{to}'; available: build, review")
        from_stage, to_stage = AGENT_ADVANCE[to]

        board = self._board()
        task, stage = self._find_task(task_id, board=board)
        self._require_mine(task, stage)
        if stage != from_stage:
            raise WorkflowError(
                f"moving to {to_stage} is only possible from {from_stage}; task is now in {stage}"
            )

        if to == "build":
            unusable = _unusable_report_fields(("spec", spec))
            if unusable:
                raise WorkflowError(
                    f"a spec is required — this call's spec was {unusable[0][1]}: describe "
                    f"your approach before implementing. {_LOST_ARGUMENT_HINT}"
                )
            self.api.add_comment(task_id, f"[spec]\n{spec.strip()}")
            # (пере)сборка тоже инвалидирует любой прошлый вердикт: человек мог руками
            # вернуть одобренную/отбитую карточку сюда (#119). На свежем клейме меток нет —
            # это no-op; needs_work-цикл идёт через Build (не Design), сюда не заходит.
            self._clear_verdict_labels(task)
        else:
            # hard sequence gate (option C, epic #94, mechanism 2): the advance→review LATCH on
            # an in-flight successor — the case the human asked about. Refuse to land THIS task in
            # Review while any of its predecessors is below Review: a predecessor P that had
            # reached Review (so this successor got claimed) but was then bounced Review→Build
            # must be reworked back to Review before this one may advance. Applies ONLY to
            # to='review' (to='build' and every other transition are untouched); keys off
            # follows/blocked only, never parenttask (migration guard); reuses the full board
            # already fetched (must be full, not light — a predecessor may sit in Your Call/Done).
            # Known residual gap accepted by design: if THIS task was ALREADY in Review when P
            # bounced, the latch doesn't apply retroactively — the human-only Done move backstops.
            blockers = self._unfinished_predecessors(task_id, board=board)
            if blockers:
                joined = "; ".join(f"{b['ref']} in '{b['stage']}'" for b in blockers)
                raise WorkflowError(
                    f"can't move {self._ref(task)} to Review yet — its predecessor is being "
                    f"reworked below Review: {joined}. "
                    + self._predecessor_advice(
                        blockers,
                        "Finish that predecessor's rework and get it back to Review first, then "
                        "advance this one (a predecessor is 'ready' only at Review or Done).",
                    )
                )
            # #657: DISJUNCTIVE guard, so it must name WHICH field failed it. The old text
            # listed both whatever was actually wrong, which made two very different states
            # read identically: an agent who wrote a full worklog and merely forgot evidence
            # got the same sentence as an agent whose 7 KB worklog never arrived. See
            # _LOST_ARGUMENT_HINT for what is and is not provable about the second one.
            # #718: `root_cause` joins that guard for a BUG, and only for a bug. Until this card
            # the field was a silent no-op — measured, a card labelled `bug` advanced to Review
            # with no cause at all and the tool answered `review_kind: 'bug'` in the SAME payload,
            # i.e. it knew. Meanwhile `advance`'s own docstring called the field MANDATORY and
            # SKILL.md called it ОБЯЗАТЕЛЕН, so the rules promised a gate that did not exist and
            # the reviewer's 'bug' rubric ("confirm the fix closes the CAUSE from the report")
            # could be handed a report with no cause in it. Asked with the same expression that
            # computes `review_kind` below, deliberately: a second definition of "what counts as
            # a bug" is how the two would drift. Folded into the SAME call rather than a separate
            # `if`, so an agent missing two fields of three is told all three at once — that
            # disjunctive shape is #657's, and splitting it would undo it.
            # The epic container is exempt for the reason the push-nudge below exempts it: its
            # code lives in its children, no reviewer is ever offered it, so a cause demanded here
            # would have no consumer.
            fields = [("worklog", worklog), ("evidence", evidence)]
            if self._has_label(task, LABEL_BUG) and not self._has_label(task, LABEL_EPIC):
                fields.append(("root_cause", root_cause))
            unusable = _unusable_report_fields(*fields)
            if unusable:
                named = "; ".join(f"{name} — {state}" for name, state in unusable)
                raise WorkflowError(
                    f"Review needs a report. Unusable in this call: {named}. worklog = what "
                    f"was done and how it was VERIFIED (by running it, not by reading the "
                    f"code); evidence = the commit sha / PR link / verification output; for a "
                    f"bug fix root_cause too — the cause of the bug, not the symptom. "
                    f"{_LOST_ARGUMENT_HINT}"
                )
            report = ["[worklog]"]
            if (root_cause or "").strip():
                report.append(
                    card_text(self.language, "worklog_root_cause", root_cause=root_cause.strip())
                )
            report.append(card_text(self.language, "worklog_worklog", worklog=worklog.strip()))
            # the leading newline is LAYOUT, not prose: it keeps the blank line that separates the
            # evidence from the body, and formatting.text_to_html turns it into a paragraph break.
            report.append(
                "\n" + card_text(self.language, "worklog_evidence", evidence=evidence.strip())
            )
            self.api.add_comment(task_id, "\n".join(report))
            # resubmit-reset: ресабмит инвалидирует ЛЮБОЙ прошлый вердикт — снимаем ОБЕ
            # вердикт-метки, и review-failed, и reviewed (#119: человек мог руками вытащить
            # одобренную карточку из Review на доработку — reviewed не должен уехать на новое
            # ревью). No-op на первом сабмите (меток ещё нет).
            self._clear_verdict_labels(task)
        self._move(task_id, to_stage)
        result = {"moved_to": to_stage, "task_id": task_id}
        if to == "review":
            # best-effort epic-complete marker (#118 Part 2): if THIS child was the LAST of an epic
            # parent to reach Review-or-Done, mark the epic (label + comment) so the human sees it's
            # ready to close. It writes to a DIFFERENT card, so it is wrapped so NOTHING it does can
            # fail the child's advance or change this result's shape (it adds no keys) — see the
            # helper's docstring. Any exception (epic lookup, comment, or label) is swallowed after a
            # one-line stderr note (#134).
            try:
                self._mark_epic_if_children_complete(task, board)
            except Exception as exc:
                # strictly best-effort — a marker on another card never fails the child's advance, so
                # the exception is still swallowed; but NO LONGER silently (#134). A bare
                # `except Exception: pass` hid a marker broken by a refactor: `except Exception`
                # catches TypeError/AttributeError (programmer errors), not just network blips, and
                # the marker IS the human's visibility mechanism for an assembled epic, so a
                # silently-dead indicator is worse than none. Leave one line on STDERR only (never
                # stdout — a stray byte corrupts the MCP stdio protocol), naming the advancing child
                # and the exception class so the failure is actionable (the epic's own id isn't
                # reliably known here — the helper can raise before resolving a parent — and the
                # helper is out of this card's slice; the child is one get_task from the epic). Same
                # best-effort-with-a-stderr-trace contract as sync_installed_artifacts (#88).
                #
                # #135: the LOG path must be as guarded as the marker it reports on. `{exc}`
                # calls str(exc) INSIDE this handler, so an exception whose __str__ itself
                # raises would escape advance(). By now the child has ALREADY reached Review
                # and written its [worklog], so a leaked exception makes advance raise for work
                # that genuinely succeeded — a state/report divergence, worse than a lost log.
                # So format the always-safe parts (exception CLASS + child id) unconditionally,
                # fall back to "<unprintable>" when str(exc) blows up so the diagnostic survives
                # the pathological case (a silent swallow would undo #134), then wrap the write
                # itself so nothing on this best-effort path can propagate. For ordinary
                # exceptions detail == str(exc), so the line is byte-for-byte the #134 one.
                try:
                    detail = str(exc)
                except Exception:
                    detail = "<unprintable>"
                try:
                    print(
                        f"vikunja-mcp: epic-complete marker skipped for child #{task_id}: "
                        f"{exc.__class__.__name__}: {detail}",
                        file=sys.stderr,
                    )
                except Exception:
                    pass
        # push-нудж (#117): ЛЮБАЯ задача, доведённая до Review, требует независимого ревью —
        # не только багфикс. Исключение — epic-контейнер (label epic): его код лежит в детях
        # (каждый отревьюен на своём advance), ревьюить нечего. Скип цепляется за метку epic,
        # НИКОГДА за наличие подзадач (тот же миграционный принцип, что у гейта
        # последовательности). Пер-таск-агент вернёт review_needed оркестратору, тот задиспатчит
        # свежего ревьюера (author != reviewer); review_kind задаёт рубрику: 'bug' —
        # воспроизвести и закрыть причину; 'change' — соответствие spec, реальные тесты, слайс.
        if to == "review" and not self._has_label(task, LABEL_EPIC):
            result["review_needed"] = True
            result["review_kind"] = "bug" if self._has_label(task, LABEL_BUG) else "change"
            result["note"] = (
                "this task needs independent review — return the review_needed flag to the "
                "orchestrator in your result: it will dispatch a fresh reviewer in the "
                "background (author ≠ reviewer). review_kind tells it the rubric: 'bug' — "
                "reproduce and confirm the cause is closed; 'change' — conforms to spec, real "
                "tests, stayed in slice, obvious regressions nearby"
            )
        return result

    def review_task(self, task_id: int, verdict: str, report: str) -> dict:
        verdict = (verdict or "").strip().lower()
        if verdict not in ("approve", "needs_work"):
            raise WorkflowError("verdict must be 'approve' or 'needs_work'")
        if not (report or "").strip():
            raise WorkflowError(
                "report required: what you reproduced/verified by running and why this verdict"
            )
        task, stage = self._find_task(task_id)
        if stage != "Review":
            raise WorkflowError(f"only tasks in Review can be reviewed; this one is in {stage}")
        self._require_review_independence(task)

        # LABELS FIRST, THE VERDICT COMMENT LAST — on BOTH branches, and the order is measured
        # (#1216). It used to be the other way round, and that made a failed label write leave a
        # HALF-APPLIED verdict: the report on the card, the label absent. Both orphan states were
        # constructed and driven through the real `next_task`. UNDER THE OLD ORDER, label write
        # fails (`[review]` comment present, no label): the card is NOT offered again — the
        # offering branch above compares the last `[worklog]` against the last `[review]` COMMENT
        # and never reads a verdict label, so the orphan comment takes the card out of the offering
        # while the board still says un-reviewed. Nothing then routes a reviewer back to it
        # AUTOMATICALLY — a narrower claim than "unrecoverable", and deliberately: `review_task`
        # gates on stage alone, so a human handing someone the id still lands a verdict, exactly as
        # the decompose site below already records. UNDER THIS ORDER, comment write fails (label
        # present, no report): the card is offered exactly as it was BEFORE the failure, because
        # nothing about a verdict label enters the offering at all — so the next tick dispatches a
        # reviewer who writes the report. That is the new
        # failure mode and it is the whole reason for the swap — it self-heals, and a verdict label
        # with no report is not a lie, a verdict WAS reached. `_add_label` stays BEFORE
        # `_remove_label` in the pair: if the add fails, the prior verdict label survives untouched
        # rather than being cleared for a verdict that never landed.
        # `_mark_epic_if_children_complete` already writes label-then-comment, for a neighbouring
        # reason of its own — there the label IS the idempotency key, so a partial failure must
        # leave the epic consistently marked. Same direction, different argument.
        if verdict == "approve":
            self._add_label(task, LABEL_REVIEWED)
            self._remove_label(task, LABEL_REVIEW_FAILED)
            self.api.add_comment(task_id, f"[review] APPROVE\n{report.strip()}")
            return {
                "verdict": "approve", "task_id": task_id,
                "note": "verdict recorded; a human moves the task to Done",
            }
        self._add_label(task, LABEL_REVIEW_FAILED)
        self._remove_label(task, LABEL_REVIEWED)
        self.api.add_comment(task_id, f"[review] NEEDS WORK\n{report.strip()}")
        # An OWNERLESS card bounces to QUEUE, not Build (#705). Build means "someone is working
        # on this"; with no assignee there is no implementer to hand it back TO, and the card
        # measured UNREACHABLE there. Precisely: it can still be READ and commented on
        # (get_task/comment/attach_file need no ownership) — what no agent tool could do is MOVE
        # it or make it anyone's. Measured at 3a0ee77 by sweeping all 12 registered tools (14
        # forms) against such a card in Design AND in Build: ZERO movers from either, against an
        # assigned control that yields four. Individually: call_human/advance/return_task/
        # decompose all answer "not assigned to you — claim it first", claim refuses ("you can
        # only claim from Queue") so that advice cannot be followed, and next_task offers
        # nothing — no branch of it can, since resume keys off assignees, the stuck-claim and
        # free-queue branches off stage == Queue, and the review offer off stage == Review.
        # The reviewer's question then dies on a card nobody comes back for.
        # This is the SAME state claim's vanish-window guard already refuses to create ("без меня
        # в assignees move уведёт задачу в Design «ничьей» (невидимо для next_task и
        # незаклеймимо из Queue)") — so it is fixed the same way: by not producing it, rather
        # than by teaching the other tools to live with it. Same way means same WINDOW, too, and
        # that half was missing from this card's first draft: routing off `task` — the board
        # snapshot _find_task took at the top of this method — decides ownership up to four API
        # calls before the move — the sequence, re-read after #1216 REORDERED it and re-read wider
        # than the original listing, which omitted the conditional DELETE that was always there:
        # view_tasks -> get_or_create_label -> [add_label, skipped when that label id is already on
        # the snapshot] -> [remove_label, only for a verdict label the snapshot carries] ->
        # add_comment -> get_task -> buckets -> move_task. The reorder changes the ORDER, not which
        # calls happen; the guard can only REMOVE a call, never add one — so the window this
        # paragraph rests on is no wider than it was. A human clearing
        # the assignee in the web UI mid-call put the card in Build ownerless and reproduced #705
        # through this very method. claim pays for the same guarantee with TWO get_task re-reads
        # before ITS move; this pays one, here, and routes on the FRESH read. The price is one
        # extra GET on the needs_work path and one more place this method can fail after the
        # verdict comment has landed — the shape move_task itself already has, and cheaper than
        # a window that recreates the bug the method exists to close.
        #
        # Queue and not Build/Design: an ownerless card that needs work IS free work, so the
        # ordinary path reopens — next_task offers it (priority-sorted, like any queue item),
        # claim takes it, and the four routes a bounce can need — advance, call_human,
        # return_task, decompose — are open to the new owner, who reads the [review] comment
        # from the dossier and can forward the question with call_human.
        # It does NOT weaken "the WIP limit gates claim(), it is not an invariant on active"
        # nor "rework outranks a fresh claim": both are about a card WITH an owner, which
        # re-enters THEIR active set without passing the gate. This card was in nobody's active
        # set, so routing it through claim strands nothing — at a saturated board it waits in
        # Queue, visible and offered the moment a slot frees, exactly like the fresh work beside
        # it. The ASSIGNED path is untouched, byte for byte, note included: a card assigned to
        # someone ELSE still goes back to Build for THAT implementer and never becomes claimable
        # by whoever reviewed it ("assigned to another" keeps meaning "not yours"), and the split
        # asks for NO assignee AT ALL, so a card I merely CO-own is an assigned card too.
        #
        # "Reopens the ordinary path" is measured, not assumed, and it is not universal — two
        # label sub-cases keep the card out of next_task's free-queue offer, both by PRE-EXISTING
        # filters and neither made worse by landing in Queue (in Build nothing could see it at
        # all). Measured: `epic` — not offered AND claim refuses it as a container, so an
        # ownerless epic ends up parked in Queue for a human rather than in Build for nobody;
        # `blocked` — not offered, though claim still takes it by id (that asymmetry between the
        # two is older than this change). An unfinished predecessor behaves as designed: claim
        # refuses by the sequence gate and the card becomes claimable once the head is ready.
        if self._assignee_ids(self.api.get_task(task_id)):
            self._move(task_id, "Build")
            return {
                "verdict": "needs_work", "task_id": task_id, "moved_to": "Build",
                "note": "the task went back to the implementer — they'll see it in next_task",
            }
        self._move(task_id, "Queue")
        return {
            "verdict": "needs_work", "task_id": task_id, "moved_to": "Queue",
            "note": (
                "this card had NO assignee, so there is no implementer to hand it back to — it "
                "went to the QUEUE as free work instead of Build, where an ownerless card can "
                "be read but no agent tool can move it or make it anyone's. Whoever claims it "
                "next reads your report in the dossier; if it was a question for the human, "
                "they forward it with call_human from Design/Build"
            ),
        }

    def call_human(self, task_id: int, question: str) -> dict:
        if not (question or "").strip():
            raise WorkflowError(
                "state your question: what you need from the human and which options you weighed"
            )
        task, stage = self._find_task(task_id)
        # Stage BEFORE ownership (#590). The refused SET is unchanged — both checks are
        # conjunctive — but the ORDER decides which refusal a REVIEWER reads, and a reviewer's
        # card is in Review and (multi-identity) assigned to the implementer, so the old order
        # answered "claim it first": advice that is actively wrong here. You never claim work
        # you are reviewing. The stage message below tells them where the question really goes.
        if stage not in ACTIVE_STAGES:
            msg = f"call_human works only from Design/Build; task is in {stage}"
            if stage == "Review":
                # Measured (#590): parking from Review is not merely disallowed, it is lossy —
                # this method's body would _move the card to Your Call, and from Your Call
                # review_task refuses BOTH verdicts, so the verdict dies with the question.
                msg += (
                    " — a reviewer's question goes in review_task(task_id, verdict='needs_work', "
                    "report=<the question>): the card returns to its implementer in Build, who can "
                    "call_human from there. Parking it from here would move it OUT of Review, and "
                    "review_task then refuses — your verdict would die with your question."
                )
            raise WorkflowError(msg)
        self._require_mine(task, stage)
        self.api.add_comment(task_id, f"[needs-human] {question.strip()}")
        self._move(task_id, "Your Call")
        result = {
            "moved_to": "Your Call", "task_id": task_id,
            "note": "assignee kept; the human replies and moves the task back to Design/Build",
        }
        # Slack-webhook ping (#252): the human used to discover a YC card only by looking at
        # the board — when VIKUNJA_NOTIFY_WEBHOOK is configured, tell them. Fires only AFTER
        # the park fully succeeded (comment + move) — never about a card that isn't actually
        # in Your Call — and is STRICTLY BEST-EFFORT (same contract as the epic marker,
        # #134/#135): the notifier raises on any failure, and this single boundary swallows
        # it with one guarded stderr line, so a down/misconfigured gateway costs the ping,
        # never the parked question. notified=true/false surfaces delivery honestly (the
        # attach_file journal_comment pattern) so the agent's report can say "check the
        # board" when the ping was lost; the key is absent entirely when no webhook is
        # configured (zero result-shape change for the feature-off default).
        if self.notifier is not None:
            try:
                self.notifier.your_call(
                    ref=self._ref(task), title=task["title"],
                    question=question.strip(), task_id=task_id,
                )
                result["notified"] = True
            except Exception as exc:
                _stderr_note_best_effort(
                    f"vikunja-mcp: Your Call webhook ping skipped for #{task_id}", exc
                )
                result["notified"] = False
        return result

    def return_task(self, task_id: int, reason: str) -> dict:
        if not (reason or "").strip():
            raise WorkflowError("give the reason for the block — it'll be posted as a comment")
        task, stage = self._find_task(task_id)
        # TWO stages are shut, both BEFORE _require_mine so solo and multi-identity read the same,
        # correct refusal (in multi-identity a reviewer's card is the implementer's, and
        # "claim it first" would send them the wrong way). The five OTHER stages stay open on
        # purpose — Backlog/Queue/Design/Build/Your Call: returning a half-claimed or in-flight
        # card is a defensible "externally blocked", which is what this tool is for. Your Call is
        # deliberately among them: that card is still the agent's OWN work in flight (call_human
        # keeps the assignee), the [needs-human] question survives in the append-only journal,
        # and a block that appears while waiting for an answer is the same defensible case as from
        # Design/Build. That choice is not free, and the price is named rather than hidden: the
        # webhook ping (if configured) has already gone out and points at a card no longer in the
        # column the human looks at, and `parked_task_ids` stops covering it, so a dead tree's
        # unpushed work regrades from `--gc`'s `expected` to `kept` and wakes someone. Both are
        # noise, neither destroys work — unlike Done, where the card is not the agent's to move.
        # Review (#590): measured — without this gate the tool passed from Review and silently
        # walked reviewed work to Backlog, unassigned + `blocked`.
        if stage == "Review":
            raise WorkflowError(
                "return_task is not available from Review: it would unassign the card and send "
                "work that is under review (or already approved) back to Backlog for re-triage. "
                "A reviewer who needs a human decision puts it in review_task(task_id, "
                "verdict='needs_work', report=<the question>) — the card goes back to its "
                "implementer in Build, who owns it and can call_human from there; a finding "
                "outside the card's slice goes to file_task. Anything else genuinely blocked in "
                "Review takes that same door back to Build first."
            )
        # Done is NOT gated here any more (#662). #626's personal `if stage == "Done"` stood at
        # exactly this spot and became DEAD CODE the moment the shared guard went into
        # `_find_task` five lines above this method's own first statement — measured with
        # trace.Trace, it never executed again. Removing it is what makes the landing honest:
        # leaving a gate that can no longer fire would mean the tree carries a rule nobody can
        # tell is live. What #626 MEASURED is not repealed and is worth keeping in view: on a
        # card driven the normal way (Queue -> claim -> Design -> Build -> Review -> approve ->
        # a human moves it to Done) the ungated tool left the card in Backlog with NO assignee,
        # and #693 later narrowed the end state it lands in from `reviewed` + `blocked` to
        # `blocked` alone — the acceptance ERASED rather than contradicted, which is what the
        # shared refusal now says for every tool at once. Ownership could never have stood in
        # for the check: a human moving a card into Done does not unassign it, so `_require_mine`
        # passes on the very card that must be untouchable — which is also why the shared guard
        # runs BEFORE it and a Done card belonging to someone else reads the stage refusal
        # rather than "claim it first".
        self._require_mine(task, stage)
        self.api.add_comment(task_id, f"[blocked] {reason.strip()}")
        # #693: the card LEAVES the pipeline unassigned, so any prior verdict has stopped
        # describing it — same reason `decompose` clears on its way out (#673). Measured before
        # the call was added: approve -> a human hand-drags the approved card back to Build ->
        # return_task left Backlog holding `['blocked', 'reviewed']` at once — the 'approved AND
        # blocked' pair #626 measured coming out of Done, reachable here from an OPEN stage, where
        # `return_task` is legitimate and there is nothing to gate. This call is ALSO why the Done
        # refusal three blocks up no longer names that pair as its counterfactual: with the verdict
        # cleared first, walking a Done card out would ERASE the acceptance rather than contradict
        # it, so the refusal now says that instead — measured, `['blocked']` alone with the Done
        # gate lifted. `review-failed` + `blocked` is the weaker form of the same shape and goes
        # with it: both are stale once the card is ownerless in Backlog awaiting a human's
        # re-triage.
        self._clear_verdict_labels(task)
        # #1216: was an INLINE copy of `_add_label` (get_or_create_label + api.add_label), which is
        # how it missed the guard that helper now carries — a card a human had already labelled
        # `blocked` made this a 400 on a real server. `_clear_verdict_labels` just above touches
        # only the two verdict labels, so it cannot have changed whether THIS one is on the
        # snapshot.
        self._add_label(task, LABEL_BLOCKED)
        self.api.remove_assignee(task_id, self._me()["id"])
        self._move(task_id, "Backlog")
        return {"moved_to": "Backlog", "task_id": task_id, "labeled": LABEL_BLOCKED}

    def decompose(self, task_id: int, subtasks: list[dict], ordered: bool = False) -> dict:
        if not subtasks or len(subtasks) < 2:
            raise WorkflowError("decomposition means at least 2 subtasks")
        if any(not (st.get("title") or "").strip() for st in subtasks):
            raise WorkflowError("every subtask must have a title")
        task, stage = self._find_task(task_id)
        # Review (#663): the shape #590 gated for `return_task`, still open on the sibling tool —
        # #649 shut Done here and said so in this very block. Measured through the real `Workflow`
        # over a FakeAPI board, on a card driven the NORMAL way (Queue -> claim -> Design -> Build
        # -> Review): decompose did not refuse and left the parent in Backlog with NO assignee and
        # `epic`, two children in Queue and a `[decompose]` comment — work under review pulled out
        # of the pipeline and re-declared an unfinished container before anyone ruled on it. On an
        # APPROVED card still waiting for a human's Done the same run produced `reviewed` AND
        # `epic` at once: the Done block's own end state, one stage early. Per-tool for the reason
        # spelled out below, and the PLACEMENT is measured, not chosen by taste: a guard inside
        # `_move` fires LAST — both children already on the board, assignee off, comment posted —
        # so its refusal would LIE to the caller. It also runs BEFORE `_require_mine`, because in
        # multi-identity the card under review is the IMPLEMENTER's: measured, the ungated tool
        # answered "not assigned to you — claim it first", the one answer a reviewer must never be
        # given (you never claim work you are reviewing).
        if stage == "Review":
            raise WorkflowError(
                "decompose is not available from Review: it would unassign the card, CLEAR the "
                "verdict label on the way out and drop fresh children into Queue, so work that is "
                "under review would be pulled out of the pipeline and re-declared an unfinished "
                "container before anyone ruled on it — and on a card already APPROVED, the "
                "reviewer's verdict would vanish from the board along with it. Deciding that work "
                "needs splitting is a Build-time call, so the card has to come back to Build "
                "first: a reviewer sends it there with review_task(task_id, verdict='needs_work', "
                "report=<why it should be split>), and its implementer, who owns it in Build, "
                "decomposes from there; a human can also move it back themselves. A finding "
                "outside this card's slice goes to file_task instead."
            )
        # Done is NOT gated here any more (#662), for the same reason as in `return_task`:
        # #649's personal gate stood here and became DEAD CODE under the shared guard in
        # `_find_task` (measured with trace.Trace — never executed again), so the honest landing
        # removes it. #649's own measurement stands as history: on an accepted card the ungated
        # tool walked the parent to Backlog carrying `reviewed` AND `epic` at once, with two
        # fresh children in Queue — the board claiming work a human accepted is now an
        # unfinished container. Its closing caveat is what #662 acted on: the class stayed open
        # because the rule was nowhere written ONCE, so the next mutating tool reopened it and
        # nothing caught that. It is written once now, in `_find_task`, and the meta-test over
        # `server._DEFERRED_TOOLS` is what makes a new tool ask the question out loud.
        self._require_mine(task, stage)

        created: list[dict] = []
        try:
            for st in subtasks:
                child = self.api.create_task(
                    self.project_id, st["title"].strip(),
                    description=st.get("description", ""), priority=int(st.get("priority", 0)),
                )
                # record the child the instant it exists on the board — BEFORE add_relation
                # /_move — so a failure anywhere below still reports it. This is the retry-
                # duplication boundary: once create_task returned, a naive re-run doubles it.
                # `ref` alongside the id (#749), the same fix #735 made in `file_task`:
                # `child` IS the create_task response and already carries `identifier`
                # (measured on live 2.3.0 — a project with no prefix yields `#<index>`,
                # byte-identical to a read-back), so this value was on hand and thrown
                # away. SKILL.md forbids an agent to BUILD a ref: the per-project index
                # follows from nothing about the global id, so a composed one does not
                # look broken — it points at an unrelated LIVE card. Without this key the
                # rulebook's own advice was a `get_task` per child, or a guess.
                created.append(
                    {"id": child["id"], "ref": self._ref(child), "title": child["title"]}
                )
                self.api.add_relation(child["id"], task_id, "parenttask")
                self._move(child["id"], "Queue")
            # ordered chain (option C, epic #94): link adjacent children so each precedes the
            # next, in ARRAY ORDER — child[i] `precedes` child[i+1]. Vikunja auto-creates the
            # inverse `follows` on the SUCCESSOR (empirically verified on real 2.3.0), which is
            # exactly the kind the sequence gate reads (PREDECESSOR_RELATION_KINDS). So the head
            # keeps only an outgoing `precedes` (no follows -> claimable now) while every later
            # child gains `follows`→its predecessor the instant the chain is built (gated until
            # that predecessor reaches Review). The direction is load-bearing: a flipped chain
            # would gate the head and free the tail — the exact silent corruption to prevent.
            # Kept INSIDE the try so a chaining failure (children already exist) is surfaced by
            # the same partial-failure handler, never blind-retried. range(len(created) - 1) is a
            # no-op for 0/1 children. No cycle detection — a linear chain is acyclic by
            # construction (that's #105, deliberately out of scope).
            if ordered:
                for i in range(len(created) - 1):
                    self.api.add_relation(created[i]["id"], created[i + 1]["id"], "precedes")
        except (VikunjaError, httpx.HTTPError) as exc:
            if not created:
                raise  # nothing landed on the board yet — the bare error is safe to retry
            listing = ", ".join(f"#{c['id']} {c['title']}" for c in created)
            raise WorkflowError(
                f"decompose failed after creating {len(created)} of {len(subtasks)} "
                f"subtask(s) ({exc}). Already on the board: {listing}. Do NOT blindly "
                f"retry — you would duplicate these; delete them first, or re-run "
                f"decompose for the remaining subtasks only."
            ) from exc

        listing = ", ".join(f"#{c['id']} {c['title']}" for c in created)
        comment = f"[decompose] {card_text(self.language, 'decompose_created', listing=listing)}"
        if ordered:
            comment += card_text(self.language, "decompose_ordered")
        self.api.add_comment(task_id, comment)
        # #673: a card that BECOMES A CONTAINER carries no verdict. `advance` already clears both
        # mutually-exclusive verdict labels on both of its forms — #119's ruling, in ITS OWN
        # words, is that "a resubmission into the active pipeline invalidates any prior verdict"
        # — and decompose is the same kind of resumption, the work simply
        # moves into the children; it just cleared nothing, so the parent kept whatever label it
        # arrived with. Measured through the real Workflow over a FakeAPI board, along the exact
        # route #663's refusal recommends (a reviewer is refused from Review -> review_task(
        # verdict='needs_work') -> the owner decomposes from Build): the parent landed in Backlog
        # carrying `epic` AND `review-failed` at once. The other verdict reaches it too — an
        # APPROVED card a human hand-pulled back to Build gave `epic` AND `reviewed` — which is why
        # both go, via the SAME helper `advance` uses rather than a second spelling of the rule.
        # And the label here is not merely stale, it is INAPPLICABLE. A card is offered for
        # independent review in exactly two places — the push nudge at the end of `advance` and
        # `next_task`'s pull path — and LABEL_EPIC is skipped by BOTH, so nothing in the pipeline
        # ever routes a reviewer to a container and the normal flow can never refresh that
        # verdict. (Not "can never be refreshed at all": `review_task` gates on stage alone, so a
        # reviewer handed the id by hand still lands a verdict on an epic. Measured, not assumed.)
        # PARENT only. FOUR calls above touch a child — create_task, the `parenttask` relation,
        # the move to Queue, and the `ordered` `precedes` chain — and not one of them takes a
        # label argument, so no decompose path can put a verdict on a child and there is nothing
        # on them to clear. Placed with the
        # other PARENT mutations instead of before the children: they are grouped here, and
        # clearing earlier would invent a half-applied state (verdict gone, never became an epic).
        self._clear_verdict_labels(task)
        # #1216: the second INLINE copy of `_add_label`, and the same 400 on a card a human had
        # already labelled `epic`. Both are gone; `api.add_label` now has exactly one caller.
        self._add_label(task, LABEL_EPIC)
        self.api.remove_assignee(task_id, self._me()["id"])
        self._move(task_id, "Backlog")
        result = {
            "created": created,
            "parent": {"id": task_id, "moved_to": "Backlog", "labeled": LABEL_EPIC},
        }
        if ordered:
            result["ordered"] = True
            result["note"] = (
                "children are chained head→tail (precedes/follows); only the head is claimable "
                "now — each successor unlocks when its predecessor reaches Review"
            )
        return result

    def file_task(
        self, title: str, description: str = "", priority: int = 0,
        related_task_id: int | None = None, project_id: int | None = None,
        queue: bool = False, icebox: bool = False,
    ) -> dict:
        """File a finding (a bug/tech-debt OUTSIDE the current task) into Backlog for
        human triage — NOT into Queue (a human prioritizes). Optionally: a 'related'
        relation to the task it was found during. No ownership required — this is a new
        card, not an edit of your task (unlike decompose). project_id (agent-to-agent
        coordination): file into ANOTHER project's Backlog; the target board is resolved
        BEFORE the card is created (fail-fast — no orphan in its default bucket), the
        token's access to the target is Vikunja's call (403 -> clear refusal), and the
        marker names the SOURCE project so the target's humans see provenance. None (or
        the own project id) keeps today's behavior bit-for-bit. queue=True (#249) is the
        explicit human-asked opt-in: the card lands in the OWN project's Queue instead —
        unassigned, so immediately claimable (next_task / the hub's `claimable` poll see
        it) — because the human's instruction to create the work IS the triage. It is
        deliberately OWN-PROJECT-ONLY: injecting ready-for-pickup work into ANOTHER
        project's Queue would bypass that project's human (and wake their fleet loop
        with work nobody there sanctioned), so queue+cross is refused before anything
        is created. The result's filed.ref (#735) is the card's readable name — echo it
        VERBATIM, never reconstruct one from the id (it is not searchable, so nobody
        downstream can check a fabricated one; see _ref).

        icebox=True (#1640) files into the freezer instead: the Icebox column plus the
        `icebox` label, for a finding that is real but very minor — cosmetic legacy, lyricism,
        something nobody is ever expected to pick up. It is the honest destination for the
        finding an agent would otherwise drop in Backlog to sit forever, and it exists so that
        Backlog keeps meaning "work a human still has to triage". It is NOT combinable with
        queue (opposite instructions: "do this now" against "nobody will do this") and, unlike
        queue, it IS allowed cross-project — the asymmetry is the whole reason queue is
        refused there. Another project's Queue injects work their human never sanctioned and
        wakes their fleet; another project's Icebox wakes nobody and claims nothing of theirs.
        Both destinations are resolved BEFORE the card is created, so a board that predates
        this stage refuses with nothing left behind."""
        if not (title or "").strip():
            raise WorkflowError("a non-empty title is required for the new task")
        target = self.project_id if project_id is None else int(project_id)
        cross = target != self.project_id
        if queue and cross:
            raise WorkflowError(
                "queue=True can't be combined with a cross-project project_id: filing "
                "into ANOTHER project is Backlog-only — that project's human triages "
                "their own board, an agent must not inject ready-for-pickup work into "
                "someone else's Queue. Drop queue to file into their Backlog, or ask "
                "via call_human. Nothing was created."
            )
        if queue and icebox:
            raise WorkflowError(
                "queue=True can't be combined with icebox=True: they are opposite "
                "instructions. queue means a human asked for this work NOW (it lands "
                "claimable), icebox means nobody is expected to do it at all. Pick the one "
                "the human actually said, or file plainly into Backlog and let them triage. "
                "Nothing was created."
            )
        if cross and target <= 0:
            raise WorkflowError(
                f"project_id must be a positive Vikunja project id, got {target} "
                f"(negative ids are Vikunja pseudo-projects like favorites)"
            )
        # явно в Backlog/Queue/Icebox: не полагаемся на то, что default-бакет проекта == Backlog
        stage = "Icebox" if icebox else ("Queue" if queue else "Backlog")
        # кросс: резолвим доску ЦЕЛИ до create_task (fail-fast, см. _target_bucket);
        # свой проект: порядок сегодняшний (create -> _move), байт-в-байт.
        coords = self._target_bucket(target, stage) if cross else None
        # #1640: свой проект — та же проверка ДО create_task, и только для Icebox. Backlog/Queue
        # обязательны (REQUIRED_STAGES), их отсутствие ловит первый же _bucket; Icebox же может
        # законно отсутствовать на не мигрированной доске, а _move зовётся ПОСЛЕ create_task —
        # то есть без этой строки отказ оставлял бы карточку сиротой в дефолт-бакете. Кэш
        # бакетов один на Workflow, так что лишнего запроса это не стоит.
        if icebox and not cross:
            self._bucket("Icebox")
        created = self.api.create_task(
            target, title.strip(),
            description=(description or "").strip(), priority=int(priority or 0),
        )
        new_id = created["id"]
        if cross:
            view_id, bucket_id = coords
            self.api.move_task(target, view_id, bucket_id, new_id)
        else:
            self._move(new_id, stage)
        # #1640: the label goes on in BOTH branches — it is what survives the card being dragged
        # out of the column, and the only part of "iceboxed" a cross-project reader can see
        # without knowing which column it landed in. Through `_add_label` like every other label
        # write in this class, so the already-labelled case is a no-op rather than a 400.
        if icebox:
            self._add_label(created, LABEL_ICEBOX)
        if related_task_id is not None:
            self.api.add_relation(new_id, related_task_id, "related")
        # #1640 adds the fourth and fifth provenance bases beside the three below, rather than a
        # suffix on them: an iceboxed card is NOT "for human triage" and must not say it is —
        # Backlog's whole meaning is that a human still owes it a decision, and the freezer's is
        # that one has already been made. The cross variant stays separate for the same reason
        # the cross base itself does: the target's humans read it, and what they need first is
        # where the card came from.
        if cross and icebox:
            body = card_text(self.language, "filed_cross_icebox", project_id=self.project_id)
        elif icebox:
            body = card_text(self.language, "filed_icebox")
        elif cross:
            # provenance: люди ЦЕЛЕВОГО проекта должны видеть, откуда пришла карточка
            body = card_text(self.language, "filed_cross_project", project_id=self.project_id)
        elif queue:
            # честный провенанс: триаж Backlog пропущен — по явной просьбе человека
            body = card_text(self.language, "filed_queue")
        else:
            body = card_text(self.language, "filed_backlog")
        marker = f"[filed-by-agent] {body}"
        if related_task_id is not None:
            marker += card_text(self.language, "filed_related", related_task_id=related_task_id)
        self.api.add_comment(new_id, marker)
        # `ref` (#735): the readable name of the card THIS tool just created. The tools
        # that HAND BACK a task already carry one (_summary for next_task/claim, get_task), so an
        # agent told by SKILL.md to echo a ref, having only `filed.id`, had to invent the half no
        # tool gave it — and #660 shipped exactly that: "Filed as VMCP-181 (732)", where 732 is
        # really VMCP-195 and VMCP-181 is a LIVE unrelated card (id 706). A fabricated identifier
        # resolves to plausibly the WRONG card, which is worse than a broken link: it takes the
        # reader somewhere. Note the scope: this closes file_task, NOT the class — `decompose`
        # creates cards too and still records its children as {id, title}, measured by running it
        # and by reading every historical version of the line that records a child (introduced by
        # f6508ac, unchanged since; no `git log -S` spelling is quoted for it, because a command
        # written INTO the file it interrogates changes its own answer — this comment would be a
        # new match). So #735's own description was wrong to list decompose among the tools that
        # already return a ref; that half is filed as VMCP-206 (749), and until it lands a child's
        # ref costs a get_task, which SKILL.md says rather than implying otherwise.
        # It costs ZERO extra requests, and that is measured twice, not assumed: real 2.3.0
        # returns `identifier` in the PUT /projects/{id}/tasks response itself ('PRB-1'; '#1' for
        # a project with no prefix — byte-identical to the read-back), and a hooked call
        # inventory of a live file_task shows NO GET of the new card in either branch. So _ref is
        # a pure format over the dict `create_task` already returned — which is also why "the
        # token may not see the card it just filed" cannot arise here: nothing is re-read.
        # CROSS-PROJECT: the identifier is computed by the TARGET's board, so the ref carries
        # THEIR prefix (measured live: 'TGT-1 (5)' filed from a project prefixed OWN) — that
        # prefix is what makes the card findable on the board it actually lives on, and the note
        # says so out loud.
        # What `ref` still does NOT cover, pre-existing and deliberately not widened here: the
        # result is assembled LAST, so a failure between create and here (a scope gap on the
        # move, the relation or the marker) raises with the card already on the board and hands
        # back neither id nor ref. `decompose` takes the other choice a few hundred lines up —
        # it records each child the instant it exists, BEFORE its relation and move — and the
        # asymmetry is worth knowing about rather than assuming away.
        result = {
            "filed": {
                "id": new_id, "ref": self._ref(created),
                "title": created["title"], "stage": stage,
            },
            "note": (
                "in Icebox, labelled `icebox` — the freezer: very minor / legacy work nobody "
                "is expected to pick up. next_task never offers it (the COLUMN is the gate), "
                "and if a human later drags it into Queue it IS offered, carrying the label "
                "as an instruction to do the minimum. Do not treat filing here as fixing it"
                if icebox
                else "in Queue, unassigned — immediately claimable (Backlog triage bypassed; "
                "queue=True is only for tasks a human explicitly asked to file as work)"
                if queue
                else "in Backlog for human triage (not Queue — a human prioritizes)"
            ),
        }
        if cross and icebox:
            result["filed"]["project_id"] = target
            result["note"] = (
                f"filed into project {target}'s Icebox, labelled `icebox` — their freezer "
                f"for very minor / legacy work. It wakes nobody there and claims nothing of "
                f"theirs, which is why this is allowed cross-project where queue=True is not. "
                f"The card lives on the TARGET board: your other tools (get_task/comment/"
                f"next_task) are bound to your own project and won't see it — the 'related' "
                f"link is the cross-reference. `ref` carries the TARGET project's identifier "
                f"prefix, not yours"
            )
        elif cross:
            result["filed"]["project_id"] = target
            result["note"] = (
                f"filed into project {target}'s Backlog for THAT project's human to "
                f"triage (not Queue — a human prioritizes). The card lives on the TARGET "
                f"board: your other tools (get_task/comment/next_task) are bound to your "
                f"own project and won't see it — the 'related' link is the cross-reference. "
                f"`ref` carries the TARGET project's identifier prefix, not yours: that is "
                f"the name their humans READ the card by on their own board, so echo it "
                f"verbatim (it is not a search key — the id in parens is what opens it)"
            )
        if related_task_id is not None:
            result["related_to"] = related_task_id
        return result

    def _resolve_sibling(self, to: str | int) -> int:
        """What an agent typed -> a project id, for `handoff` and `transfer_task` (#1179).

        Accepts a NAME from the repo toml's `siblings` registry, or a bare id for the same
        free-form addressing `file_task` has always allowed. The registry is not a gate —
        the scoped token decides what a cross-project write may touch — so a raw id is not
        refused for being unlisted; what the registry provides is a name to type and, more
        basically, the knowledge that a neighbour exists. A refusal therefore LISTS the
        configured names and says which file they come from, because "unknown target" with
        no inventory is a dead end for an agent that cannot see the toml."""
        if isinstance(to, bool) or not isinstance(to, (int, str)):
            raise WorkflowError(
                f"`to` must be a sibling name or a project id, got {to!r}"
            )
        if isinstance(to, int):
            target = to
        else:
            name = to.strip()
            if name in self.siblings:
                target = self.siblings[name]
            elif name.isdigit():
                target = int(name)
            else:
                known = ", ".join(sorted(self.siblings)) or "(none configured)"
                raise WorkflowError(
                    f"unknown target {to!r}. Configured siblings: {known}. Names come from "
                    f"this repo's .vikunja-mcp.toml — `[tracker] siblings = {{ backend = 17 }}` "
                    f"— which is committed team policy, so a human adds the neighbour there; a "
                    f"bare project id works too if you have one. Nothing was changed."
                )
        if target < 1:
            raise WorkflowError(
                f"the target must be a positive Vikunja project id, got {target} (negative "
                f"ids are pseudo-projects like favorites). Nothing was changed."
            )
        if target == self.project_id:
            raise WorkflowError(
                f"the target is this project ({target}) — there is nothing to cross. Use "
                f"file_task for a finding on your own board, or decompose to split this card."
            )
        return target

    def handoff(
        self, task_id: int, to: str | int, title: str,
        description: str = "", priority: int = 0,
    ) -> dict:
        """Park THIS card and file the work it is waiting for onto a NEIGHBOUR's board (#1179).

        The dependency shape: an agent in `dogiators-front` finds that the next step needs an
        endpoint that does not exist yet. It cannot do that work (wrong repo) and must not
        silently drop the card, so it files the backend half over there and stands its own
        card down until that half is ready.

        What makes the pause self-clearing is the `blocked` relation, not a label: the
        predecessor gate withholds this card while the new one is below Review and offers it
        again the moment it gets there — no human in the middle. So the card goes back to
        QUEUE and carries NO `blocked` label. The label means "externally blocked, a human
        must look" (that is `return_task`), and it suppresses the offer permanently, which
        would turn an automatic resume into a card nobody ever picks up again.

        The new card lands in the neighbour's BACKLOG, never their Queue: their human triages
        their own board. Same rule, same reason, as `file_task`'s cross-project branch.

        Ordering is fail-fast throughout — target resolved, stage and ownership checked, and
        the neighbour's Backlog located, all BEFORE anything is created or moved. A handoff
        that fails leaves no orphan over there and no parked card over here."""
        if not (title or "").strip():
            raise WorkflowError(
                "a non-empty title is required: it is what the neighbour's human triages, so "
                "say what THEY need to build, not what you were doing"
            )
        target = self._resolve_sibling(to)
        task, stage = self._find_task(task_id)
        if stage not in ACTIVE_STAGES:
            raise WorkflowError(
                f"handoff works only from Design/Build; task is in {stage}. It stands YOUR "
                f"active card down — there is nothing to pause from {stage}. To file work "
                f"for a neighbour without pausing anything, use file_task(project_id=...)."
            )
        self._require_mine(task, stage)
        # fail-fast, the _target_bucket rule: a target the token cannot reach refuses here,
        # with nothing created and this card untouched.
        view_id, bucket_id = self._target_bucket(target)
        created = self.api.create_task(
            target, title.strip(),
            description=(description or "").strip(), priority=int(priority or 0),
        )
        new_id = created["id"]
        self.api.move_task(target, view_id, bucket_id, new_id)
        # THE link. Written on OUR card ("this one is blocked by that one"), which is the
        # direction PREDECESSOR_RELATION_KINDS reads; Vikunja mirrors the inverse onto theirs.
        self.api.add_relation(task_id, new_id, "blocked")
        self.api.add_comment(new_id, "[filed-by-agent] " + card_text(
            self.language, "handoff_filed",
            project_id=self.project_id, blocked_task_id=task_id,
        ))
        self.api.add_comment(task_id, "[handoff] " + card_text(
            self.language, "handoff_parked", project_id=target, new_id=new_id,
        ))
        # park last: the card only stands down once the thing it waits for actually exists
        for uid in self._assignee_ids(task):
            self.api.remove_assignee(task_id, uid)
        # same family as return_task/decompose (#693): the card leaves the active pipeline, so
        # any verdict on it is stale. Left standing, the board shows a card parked on a
        # dependency AND labelled `review-failed`, and nothing says which is the live fact.
        # The FRESH read, not the board copy (#786) — a verdict written after this call's board
        # read is exactly the one a stale snapshot cannot see.
        self._clear_verdict_labels(self.api.get_task(task_id))
        self._move(task_id, "Queue")
        return {
            "filed": {
                "id": new_id, "ref": self._ref(created),
                "title": created["title"], "project_id": target, "stage": "Backlog",
            },
            "parked": {"id": task_id, "ref": self._ref(task), "stage": "Queue"},
            "note": (
                "your card is back in Queue, unassigned, and blocked on the new one — the WIP "
                "slot is free. Nobody needs to move it back by hand: it is offered again "
                "automatically once the filed card reaches Review. Do NOT keep working this "
                "card; pick up the next one."
            ),
        }

    def transfer_task(self, task_id: int, to: str | int, reason: str) -> dict:
        """Move THIS card, history and all, onto a NEIGHBOUR's board (#1179) — the misfile.

        Distinct from `handoff`: nothing stays behind and nothing is created. Use it when the
        card was filed on the wrong board in the first place, not when your card depends on
        someone else's work.

        THE REF CHANGES, and callers must not paper over it. Measured on a real 2.3.0: a card
        moved into a project is re-indexed by the TARGET's own counter (FRNT-2 arrived as
        BACK-3 in a project already holding BACK-2 — no collision, and the old identifier
        simply stops naming it). So every ref quoted in an earlier comment, worklog or commit
        message is dead, and this returns the new one with that said out loud.

        Also measured there: labels, assignees and relations all SURVIVE the move, including
        a relation whose far end stayed on the source board. Surviving is right for relations
        and wrong for the other two — the claim is void on a board where nobody claimed it,
        and a `reviewed` verdict earned under one project's review must not travel — so both
        are cleared here. Vikunja drops the card into the target's DEFAULT bucket, which is
        why the explicit move into Backlog is not optional."""
        if not (reason or "").strip():
            raise WorkflowError(
                "state why this card belongs on the other board — it is the only context the "
                "people over there will have for a card that arrives with someone else's "
                "comment history attached"
            )
        target = self._resolve_sibling(to)
        task, stage = self._find_task(task_id)          # refuses Done on its own
        # Review and Your Call are shut, and the two refusals are one idea: a card in either
        # has something PENDING that belongs to THIS board — a verdict not yet cast, or a
        # question a human here is being asked. Carrying the card away strands it silently.
        # Review's closure also keeps #672's invariant intact ("out of Review a card is walked
        # by exactly ONE agent tool"), which a second mover would quietly falsify. The way out
        # of Review is review_task(needs_work); the card is then transferred from Build.
        if stage in ("Review", "Your Call"):
            raise WorkflowError(
                f"transfer_task is shut from {stage}: this card has something pending on THIS "
                f"board — a verdict not yet cast, or a question a human here was asked — and "
                f"moving it to project {target} would strand that with nobody watching. From "
                f"Review, send it back with review_task(verdict='needs_work') saying it belongs "
                f"on another board; whoever picks it up in Build transfers it. Nothing changed."
            )
        related = self.api.get_task(task_id).get("related_tasks") or {}
        if self._has_label(task, LABEL_EPIC) or (related.get("subtask") or []):
            raise WorkflowError(
                f"{self._ref(task)} is an epic container: its code lives in its children, and "
                f"moving the parent alone splits the set across two boards, leaving the "
                f"children pointing at a parent nobody there can open. Move the children "
                f"first (or transfer them individually). Nothing was changed."
            )
        # fail-fast before the first write, exactly as in handoff/file_task
        view_id, bucket_id = self._target_bucket(target)
        source_project = self.project_id
        self.api.update_task(task_id, project_id=target)
        # Vikunja parks a moved card in the target's DEFAULT bucket (measured), so Backlog is
        # reached explicitly rather than assumed.
        self.api.move_task(target, view_id, bucket_id, task_id)
        for uid in self._assignee_ids(task):
            self.api.remove_assignee(task_id, uid)
        # the claim is void and any verdict was earned elsewhere; `blocked` was about the old
        # board's situation. Relations are deliberately NOT touched.
        for label in (LABEL_BLOCKED, LABEL_REVIEWED, LABEL_REVIEW_FAILED, LABEL_EPIC_READY):
            self._remove_label(task, label)
        self.api.add_comment(task_id, "[moved] " + card_text(
            self.language, "moved_from", project_id=source_project,
        ) + f": {reason.strip()}")
        moved = self.api.get_task(task_id)
        return {
            "moved": {
                "id": task_id, "ref": self._ref(moved),
                "project_id": target, "stage": "Backlog",
            },
            "note": (
                f"the card was re-indexed by project {target}, so its ref CHANGED: quote "
                f"{self._ref(moved)} from now on. The old one no longer names this card — do "
                f"not reuse it, and do not go back to fix refs in comments already written. "
                f"It is in their Backlog, unassigned, for their human to triage."
            ),
        }

    def comment(self, task_id: int, text: str) -> dict:
        if not (text or "").strip():
            raise WorkflowError("an empty comment is not needed")
        # allow_done (#662): READING an accepted card stays open — the guard in `_find_task`
        # is fail-closed, so a reader has to say so. Refusing here would make a human's own
        # accepted card unreadable, the regression #649 measured as worse than the hole.
        self._find_task(task_id, allow_done=True, allow_icebox=True)
        self.api.add_comment(task_id, text.strip())
        return {"commented": task_id}

    def get_task(self, task_id: int) -> dict:
        """Full dossier: unlike _summary (next_task/claim), the description is NOT
        truncated and related is added — a compact dict {relation_kind: [{"id", "title"}, ...]}.
        attachments lists each file's METADATA only ({id, name, mime, size}) — no bytes, so a
        card that is nothing but a screenshot is SEEN, not guessed at; fetch the bytes with
        download_attachment(task_id, attachment_id) using the `id` here."""
        _, stage = self._find_task(
            task_id, allow_done=True, allow_icebox=True,   # a READ path (#662, #1640)
        )
        task = self.api.get_task(task_id)
        raw_comments = self.api.comments(task_id)
        related_raw = task.get("related_tasks") or {}
        related = {
            kind: [{"id": rt["id"], "title": rt["title"]} for rt in items]
            for kind, items in related_raw.items()
        }
        # attachments come INSIDE the task JSON (tasks:read_one, no extra scope), each
        # {id, task_id, file:{name,mime,size}}; the server sends None (not []) when there are
        # none. Surface METADATA ONLY — the bytes would bloat every dossier (the point is the
        # agent SEES "shot.png (image/png)" and chooses whether to download_attachment it). `id`
        # is the attachment id download_attachment keys off (NOT file.id), so it is load-bearing.
        attachments = [
            {
                "id": a.get("id"),
                "name": (a.get("file") or {}).get("name"),
                "mime": (a.get("file") or {}).get("mime"),
                "size": (a.get("file") or {}).get("size"),
            }
            for a in task.get("attachments") or []
        ]
        # #1640: `labels` below already NAMES the label, so this is not the fact — it is the
        # INSTRUCTION that follows from it, the same one _summary attaches to an offer. A
        # dossier is read by an agent about to work the card, which is precisely the moment the
        # effort budget applies; leaving it to be inferred from a label list is how it gets
        # missed. Absent on an ordinary card, so the dossier's shape does not move.
        icebox = ICEBOX_HINT if Workflow._has_label(task, LABEL_ICEBOX) else None
        return {
            "id": task["id"],
            "ref": self._ref(task),
            "title": task["title"],
            "priority": task.get("priority", 0),
            "description": task.get("description") or "",
            "stage": stage,
            "assignees": [a.get("username", "?") for a in task.get("assignees") or []],
            "labels": [lb.get("title") for lb in task.get("labels") or []],
            "related": related,
            "attachments": attachments,
            # comments are stored as HTML (#85); render back to plain text so the agent
            # reads clean multiline text (the human reads the formatted HTML in the UI).
            "comments": [
                {"author": c.get("author", {}).get("username", "?"),
                 "text": html_to_text(c.get("comment", ""))}
                for c in raw_comments
            ],
            **({"icebox": icebox} if icebox else {}),
        }

    def download_attachment(self, task_id: int, attachment_id: int) -> dict:
        """Download a task attachment's bytes to a TEMP FILE and return its path (an agent then
        Reads the path — a PNG/JPG renders visually — instead of a base64 blob that bloats the
        context). `attachment_id` is the id from get_task's attachments[] (NOT the filename).
        Fails in agent-actionable ways: a wrong/absent id lists the task's real attachments; an
        oversized file (metadata size > cap) is refused BEFORE downloading, naming the size."""
        # same board-membership check as get_task/comment, and the same #662 read opt-in
        self._find_task(task_id, allow_done=True, allow_icebox=True)
        task = self.api.get_task(task_id)
        attachments = task.get("attachments") or []
        match = next((a for a in attachments if a.get("id") == attachment_id), None)
        if match is None:
            available = ", ".join(
                f"#{a.get('id')} {(a.get('file') or {}).get('name')}" for a in attachments
            ) or "none"
            raise WorkflowError(
                f"task {task_id} has no attachment #{attachment_id} — its attachments are: "
                f"{available}. Use the `id` from get_task's attachments[]"
            )
        file_meta = match.get("file") or {}
        name = file_meta.get("name") or f"attachment-{attachment_id}"
        # size cap read from METADATA, BEFORE downloading — so a runaway file fails fast and
        # actionably instead of pulling GBs into a temp file / the agent's context.
        size = file_meta.get("size")
        if isinstance(size, int) and size > _MAX_ATTACHMENT_BYTES:
            raise WorkflowError(
                f"attachment #{attachment_id} ({name}) is {size} bytes — over the "
                f"{_MAX_ATTACHMENT_BYTES}-byte download cap. Fetch it directly from the tracker "
                f"UI instead of pulling it into the agent context"
            )
        data = self.api.download_attachment(task_id, attachment_id)
        # Second-line cap: the metadata pre-check above is cheap but can under-report (or be
        # missing/0 — a legit 0-byte file is fine, so we do NOT refuse on that). len(data) is the
        # real bound, so re-check the bytes we actually pulled before writing them to a temp file.
        if len(data) > _MAX_ATTACHMENT_BYTES:
            raise WorkflowError(
                f"attachment #{attachment_id} ({name}) downloaded as {len(data)} bytes — over the "
                f"{_MAX_ATTACHMENT_BYTES}-byte cap; its metadata under-reported the size. Fetch it "
                f"directly from the tracker UI instead of pulling it into the agent context"
            )
        path = _write_attachment_to_temp(name, data, fallback=f"attachment-{attachment_id}")
        return {
            "path": path,
            "name": name,
            "mime": file_meta.get("mime"),
            "size": len(data),
            "note": (
                "Read this path to view the file — an image (PNG/JPG) renders visually, a "
                "text/PDF opens as text. It sits in a temp dir and is cleaned up automatically; "
                "Read it now rather than saving the path for later"
            ),
        }

    def attach_file(self, task_id: int, path: str, note: str | None = None) -> dict:
        """Upload a LOCAL file — typically a SCREENSHOT of finished, visually-verifiable work — as
        an attachment on the task, so a human and the independent reviewer SEE the result instead
        of taking 'done' on faith. The UPLOAD twin of download_attachment; deliberately a STANDALONE
        tool, NOT an argument to advance: a failed upload is its own actionable error, never a
        half-finished stage transition (the #118/#134/#135 lesson — keep cross-cutting side effects
        out of advance), and both the implementer (own task) and the reviewer (a task in Review)
        can attach. No ownership is required (same as download_attachment) — only board membership.

        Every successful upload also JOURNALS itself (#184): an `[attach] <name> (<mime>, <size>)`
        comment — plus the optional `note` (one line on WHAT the file shows, e.g. «доска после
        reconcile») — lands in the task's comment stream through the same add_comment chokepoint
        as every other marker, so a human reading the comments sees «вот здесь бот приложил четыре
        скрина» in the story itself, not just rows in the attachments widget. Deliberately a plain-
        text marker, no deep-link/embed: comment bodies are HTML-ESCAPED (text_to_html, #85), so an
        <img>/<a> would render as literal text — the filename is the honest reference. The journal
        comment is posted AFTER the upload landed, and its own failure never fails the tool: an
        {"error": ...} result would read as 'the attach failed' and provoke a blind re-upload (a
        duplicate attachment); instead the result carries journal_comment=False plus an actionable
        note (don't re-upload; post a comment() manually if the trace matters).

        Validated BEFORE any bytes hit the wire: `path` must resolve (realpath, so a symlink to a
        real file is followed) to an existing REGULAR file — a symlink to a dir/socket, a missing
        path, or a directory is refused with an actionable message — within the _MAX_ATTACHMENT_BYTES
        cap (checked via getsize, so a runaway file fails fast, never loaded). The path is NOT
        confined to the workspace: screenshots routinely land in a temp/Downloads dir outside the
        repo (a browser tool, an OS screenshot), so confining it would break the primary use case;
        the size cap + regular-file check are the guardrails. The basename becomes the attachment
        name (never the full path) and the MIME is guessed from the extension. Needs the
        tasks_attachments:create token scope — a 401 means the token is read-only for attachments
        and a human must add the `create` op (verified on real 2.3.0: create governs the upload)."""
        # same board-membership check as comment/download_attachment, same #662 read opt-in
        self._find_task(task_id, allow_done=True, allow_icebox=True)
        try:
            real = os.path.realpath(path)
        except ValueError as exc:
            raise WorkflowError(
                f"can't attach {path!r}: invalid path ({exc}) — a path can't contain a NUL byte; "
                f"pass a real local screenshot/render path"
            ) from exc
        if not os.path.isfile(real):
            raise WorkflowError(
                f"no file to attach at {path!r} — it doesn't exist or isn't a regular file. "
                f"Pass the path to a screenshot/render you already produced while verifying the "
                f"work (a directory, a broken symlink, or a missing path is refused here)"
            )
        # getsize→open is a TOCTOU window: the file can be removed/replaced/made unreadable after
        # the isfile guard, so an OSError from either becomes an actionable WorkflowError, never a
        # raw traceback. The oversized WorkflowError raised BETWEEN them is not an OSError, so it
        # propagates cleanly past this handler.
        try:
            size = os.path.getsize(real)
            if size > _MAX_ATTACHMENT_BYTES:
                raise WorkflowError(
                    f"{path} is {size} bytes — over the {_MAX_ATTACHMENT_BYTES}-byte upload cap. "
                    f"Attach a screenshot/thumbnail, not a large asset or a runtime artifact"
                )
            with open(real, "rb") as fh:
                data = fh.read()
        except OSError as exc:
            raise WorkflowError(
                f"the file at {path!r} could not be read ({exc}) — it may have been removed, "
                f"replaced, or made unreadable after the size check; re-produce it and retry"
            ) from exc
        # Second-line cap mirroring download_attachment: getsize is a cheap pre-check but can lie
        # (the file grew between stat and read), so len(data) is the real bound and the honest
        # uploaded length reported below.
        if len(data) > _MAX_ATTACHMENT_BYTES:
            raise WorkflowError(
                f"{path} read as {len(data)} bytes — over the {_MAX_ATTACHMENT_BYTES}-byte upload "
                f"cap (its size grew after the pre-check). Attach a screenshot/thumbnail, not a "
                f"large asset or a runtime artifact"
            )
        name = _safe_attachment_name(os.path.basename(real), fallback=f"attachment-{task_id}")
        mime, _ = mimetypes.guess_type(name)
        resp = self.api.upload_attachment(task_id, name, data, mime=mime)
        created = (resp or {}).get("success") or []
        new_id = created[0].get("id") if created and isinstance(created[0], dict) else None
        # журнальный след аплоада (#184): человек листает ЛЕНТУ КОММЕНТОВ, а не виджет файлов —
        # без следа «бот приложил скрин» в истории задачи невидимо. mime может быть None
        # (неизвестное расширение) — тогда в скобках только размер.
        size = _human_size(len(data), self.language)
        meta = f"{mime}, {size}" if mime else size
        journal = f"[attach] {name} ({meta})"
        if (note or "").strip():
            journal += f" - {note.strip()}"
        journal_failure: str | None = None
        try:
            self.api.add_comment(task_id, journal)
        except (VikunjaError, httpx.HTTPError) as exc:
            # файл УЖЕ на карточке — ошибка коммента не имеет права выглядеть как ошибка
            # загрузки (слепой повтор = дубль вложения); деградируем в journal_comment=False
            # с подсказкой, что делать. Ловим только API/сетевые ошибки — программные пусть падают.
            journal_failure = str(exc)
        return {
            "attached": True,
            "task_id": task_id,
            "attachment_id": new_id,
            "name": name,
            "mime": mime,
            "size": len(data),
            "journal_comment": journal_failure is None,
            "note": (
                "the file is on the card and journaled as an [attach] comment in the task's "
                "comment stream — don't post a separate comment about the upload. For a "
                "visually-verifiable change, cite it in your advance(to='review') worklog as "
                "evidence alongside the commit sha"
            ) if journal_failure is None else (
                f"the file IS on the card, but posting the [attach] journal comment failed "
                f"({journal_failure}) — do NOT re-upload (that would duplicate the attachment); "
                f"if the journal trace matters, post a brief comment() naming the file"
            ),
        }
