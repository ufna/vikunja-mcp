"""`DELETE /tasks/{id}/labels/{label_id}` naming a link that is NOT there — against REAL Vikunja
2.3.0 (#1457).

WHY THIS HAS TO BE AN INTEGRATION TEST. #1457 turned `Workflow._remove_label` from one DELETE
into a loop over every row of one normalised key, and the whole argument that the loop opens NO
new route into a benign refusal rests on what the server answers for an ABSENT link. No fake can
be asked: `FakeAPI.remove_label` is deliberately an idempotent no-op, a divergence
`FakeAPI._read_task` keeps on the ground that only snapshot staleness reaches it — "a race, not a
route" — so the unit suite is blind here BY DESIGN and its silence is not evidence. Until this
module the answer lived only in prose: measured on a live 2.3.0 in #1211, quoted in three places
since, and re-measured by hand on a throwaway container while #1457 was written. A hand
measurement decays silently; this is the thing that goes red instead if a later Vikunja changes
the answer.

THREE ABSENT-LINK SHAPES, ONE STATUS, and the competing hypothesis is named because it was live
when the card was written: a link removed a moment ago, a label that exists but was never
attached, and a `label_id` that does not exist at all all answer 403 `Forbidden` — 404 is none of
the three. The 200 happy path is asserted in the same test, because a 403 proves nothing without
a DELETE that does succeed: a token that simply cannot delete answers on every call alike, and
`FakeAPI._read_task` records exactly that trap — the integration suite's own AGENT_PERMS token,
which grants `tasks_labels` create/read_all and no delete, got 401 on the happy path too, i.e.
never reached the endpoint. That is why these run with a full JWT.

AND THE TWO-ROW CARD ITSELF, so the state #1457 is about is known to be server-reachable rather
than a fake's artefact, and so the loop's premise — that its DELETEs name DISTINCT ids — is
checked against the server that supplies them.
"""
import uuid

import pytest

from tests.integration.conftest import BASE
from vikunja_mcp.api import VikunjaAPI, VikunjaError, label_key


@pytest.fixture(scope="module")
def rmproj(boss_jwt):
    """A plain project and the full-JWT client. No kanban board is reconciled: every assertion
    here is about the label endpoints, and a board would only add ways for the fixture to fail."""
    boss = VikunjaAPI(BASE, boss_jwt)
    return boss, boss.create_project(f"rmlabel-{uuid.uuid4().hex[:8]}")["id"]


def _titles(api, task_id):
    return [lb["title"] for lb in (api.get_task(task_id).get("labels") or [])]


def test_delete_of_an_absent_label_link_is_a_403_in_all_three_shapes(rmproj):
    """THE SERVER FACT #1457's design rests on. Asserted on the STATUS, because status is what a
    production `except` branches on and what `VikunjaError` carries."""
    boss, pid = rmproj
    task = boss.create_task(pid, "absent-link subject")
    on_it = boss.get_or_create_label(f"rm-on-{uuid.uuid4().hex[:8]}")
    never = boss.get_or_create_label(f"rm-never-{uuid.uuid4().hex[:8]}")

    boss.add_label(task["id"], on_it["id"])
    assert _titles(boss, task["id"]) == [on_it["title"]]

    # THE CONTROL, and it comes first: a DELETE that SHOULD work does. Without it every 403 below
    # is equally explained by "this caller cannot delete anything".
    boss.remove_label(task["id"], on_it["id"])
    assert _titles(boss, task["id"]) == []

    shapes = {
        "a link removed a moment ago": on_it["id"],
        "a label that exists but was never attached": never["id"],
        "a label_id that does not exist at all": 99999999,
    }
    for shape, label_id in shapes.items():
        with pytest.raises(VikunjaError) as err:
            boss.remove_label(task["id"], label_id)
        assert err.value.status == 403, f"{shape}: expected 403, got {err.value.status}"
        assert err.value.status != 404, f"{shape}: 404 was the competing hypothesis and is wrong"


def test_a_two_row_card_of_one_key_is_reachable_and_every_row_comes_off(rmproj):
    """The state the card is ABOUT, built on the server rather than on the fake — and with it the
    loop's premise, that the rows it iterates carry DISTINCT ids that each exist as a link."""
    boss, pid = rmproj
    stem = f"rmtwo{uuid.uuid4().hex[:8]}"
    lower = boss.create_label(stem)
    upper = boss.create_label(stem.capitalize())
    assert lower["id"] != upper["id"], "two rows, and the server kept both"
    assert label_key(lower["title"]) == label_key(upper["title"]), "one normalised key"

    task = boss.create_task(pid, "two-row subject")
    boss.add_label(task["id"], lower["id"])
    boss.add_label(task["id"], upper["id"])          # the SECOND row onto a card wearing the first
    assert _titles(boss, task["id"]) == [stem, stem.capitalize()], (
        "a real 2.3.0 lets one card wear two rows of one normalised key — this is the board "
        "#1457 is about, and it is not a fake's artefact"
    )

    # and the id the ADD endpoint refuses is the one already carried, which is what makes a
    # server-supplied snapshot carry each id at most once
    with pytest.raises(VikunjaError) as err:
        boss.add_label(task["id"], lower["id"])
    assert err.value.status == 400 and "8001" in err.value.message

    for row in (lower, upper):
        boss.remove_label(task["id"], row["id"])
    assert _titles(boss, task["id"]) == [], "one DELETE per row clears the card"
