"""Контракт мульти-проектного FakeAPI (кросс-проектный file_task). Фейк обязан РАЗЛИЧАТЬ
проекты: до этого каждый project-scoped метод игнорировал project_id, и workflow-баг,
двигающий задачу координатами ЧУЖОЙ доски, был невидим юнитам — ровно #125-режим
«фейк щедрее сервера». Эти тесты — растяжки, на которые опираются кросс-тесты workflow."""
import pytest

from tests.unit.fakes import FakeAPI
from vikunja_mcp.api import VikunjaError
from vikunja_mcp.workflow import STAGES


def test_second_project_has_its_own_view_and_disjoint_buckets():
    api = FakeAPI(buckets=STAGES)
    other = api.add_project("neighbor", buckets=STAGES)
    assert other["id"] != api.project["id"]
    other_view = api.kanban_view(other["id"])
    assert other_view["id"] != api.view["id"]
    own_ids = {b["id"] for b in api.buckets(api.project["id"], api.view["id"])}
    other_ids = {b["id"] for b in api.buckets(other["id"], other_view["id"])}
    assert own_ids.isdisjoint(other_ids)
    # primary state untouched — existing single-project tests see zero change
    assert api.kanban_view(api.project["id"])["id"] == api.view["id"]


def test_create_task_lands_in_the_target_projects_default_bucket():
    api = FakeAPI(buckets=STAGES)
    other = api.add_project("neighbor", buckets=["Inbox", *STAGES])
    t = api.create_task(other["id"], "filed elsewhere")
    other_view = api.kanban_view(other["id"])
    inbox = next(
        b for b in api.buckets(other["id"], other_view["id"]) if b["title"] == "Inbox"
    )
    assert api.task_bucket[t["id"]] == inbox["id"]   # ЦЕЛЕВОЙ дефолт-бакет, не свой
    assert api.stage_of(t["id"]) == "Inbox"          # stage_of видит чужие доски


def test_move_task_refuses_a_bucket_of_another_projects_view():
    # РАСТЯЖКА: workflow, передавший координаты СВОЕЙ доски для задачи в чужом
    # проекте, обязан здесь упасть — как реальный сервер (bucket не на том view -> 404).
    api = FakeAPI(buckets=STAGES)
    other = api.add_project("neighbor", buckets=STAGES)
    t = api.create_task(other["id"], "x")
    own_backlog = api.bucket_id("Backlog")           # бакет ПЕРВИЧНОГО проекта
    with pytest.raises(VikunjaError) as err:
        api.move_task(other["id"], api.kanban_view(other["id"])["id"], own_backlog, t["id"])
    assert err.value.status == 404


def test_unknown_project_404s_and_forbidden_project_403s():
    api = FakeAPI(buckets=STAGES)
    secret = api.add_project("secret", buckets=STAGES, forbidden=True)
    with pytest.raises(VikunjaError) as e403:
        api.kanban_view(secret["id"])
    assert e403.value.status == 403                  # есть, но токену не расшарен
    with pytest.raises(VikunjaError) as e404:
        api.kanban_view(999999)
    assert e404.value.status == 404                  # не существует вовсе
    assert all(p["id"] != secret["id"] for p in api.projects())  # и в листинге его нет


# --- #1200: the two 1:1 gaps #1179 left standing, found by its independent reviewer ----------
#
# Both are the same failure shape this file exists to prevent: a fake that answers differently
# from the client makes a production branch untestable, and an untestable branch reads as
# working. They are pinned HERE rather than beside the workflow tests that consume them because
# what is being asserted is the FAKE's contract, not a gate's behaviour.
#
# MUTATION SWEEP over the four pins below, run in a CLONE of this tree (never the tree being
# edited), `__pycache__` deleted and PYTHONDONTWRITEBYTECODE=1 per round, `vikunja_mcp.__file__`
# printed and confirmed to resolve inside the clone each round. Selection: this file plus
# tests/unit/test_workflow_cross_project_predecessor.py — the file that CONSUMES these knobs, so
# a mutation that only relocates coverage is visible. 26 collected in the control and in every
# round; rounds read by COUNTING lines that begin `FAILED `, `ERROR ` counted separately and 0
# throughout; control 0 failed before the first round and again after the last restore. Each pin
# carries its own round below, control re-stated on the line beside it.


def test_update_task_404s_on_an_unknown_id_instead_of_raising_KeyError():
    """#1200 item 2. `VikunjaAPI.update_task` is read-modify-write — `current = self.get_task(...)`
    is its first statement — so an unknown id raises `VikunjaError(404)` from that READ, before a
    POST is ever built. The fake raised a bare `KeyError`, which NO production
    `except VikunjaError` can catch, so every "the task went away under us" branch around an
    update was untestable and read as working. This is the exact gap #1179 closed for `get_task`
    and left standing in its neighbour.

    `pytest.raises(VikunjaError)` is the whole pin: a `KeyError` does not satisfy it — it escapes
    as an error, which is what a revert looks like here.

    control 0 failed   round: `update_task` back to `t = self.tasks[task_id]`  -> 3 failed
    """
    api = FakeAPI(buckets=STAGES)
    live = api.add_task("live one", "Queue")
    with pytest.raises(VikunjaError) as err:
        api.update_task(999999, done=True)
    assert err.value.status == 404
    with pytest.raises(VikunjaError) as read:          # the neighbour it must agree with
        api.get_task(999999)
    assert read.value.status == 404
    # control in the same round: a live id still updates, so the guard is not just a wall
    assert api.update_task(live["id"], done=True)["done"] is True


def test_a_task_in_an_unshared_project_403s_on_both_read_and_write():
    """#1200 item 3. `add_project(forbidden=True)` models a project that EXISTS but was never
    shared with this token; measured on real 2.3.0 (#1198), reading a task in it gives
    `{"message":"You don't have the permission to see this"}` — a 403, NOT a 404. That split is
    what `_offboard_predecessor` keys "gone" against "unknown" on, and before this the fake could
    not produce a 403 on a TASK at all: `_forbidden` was consulted by project-scoped calls only.
    #1179 shipped that branch with no fixture of any kind and #1190 reached it by hand-rolling a
    wrapper around `api.get_task` (`git log -S'_forbid_task'` names `5f26333`, nothing earlier).

    The WRITE is asserted beside the read for the same reason as the test above — the real
    client reads first, so it cannot 403 on one and succeed on the other.

    control 0 failed   round: `_read_task` stops consulting `_forbidden`   -> 3 failed
    """
    api = FakeAPI(buckets=STAGES)
    # built readable, then closed — the order life uses, and the only order that works: a
    # project-scoped write into an already-forbidden project 403s in the fixture itself.
    secret = api.add_project("secret", buckets=STAGES)
    hidden = api.create_task(secret["id"], "not yours")
    api.forbid_project(secret["id"])
    for call in (lambda: api.get_task(hidden["id"]),
                 lambda: api.update_task(hidden["id"], done=True)):
        with pytest.raises(VikunjaError) as err:
            call()
        assert err.value.status == 403, err.value
    # CONTROL, same round: a task on a readable board is untouched by the guard
    mine = api.add_task("mine", "Queue")
    assert api.get_task(mine["id"])["id"] == mine["id"]
    assert api.update_task(mine["id"], done=True)["done"] is True


def test_an_unshared_project_403s_where_a_deleted_task_404s():
    """The two must not collapse into one another: "gone" is a 404 and licenses releasing the
    successor, "unreadable" is a 403 and must block it. `vanish()` is the fake's existing knob for
    the first; `forbidden` is now the knob for the second.

    control 0 failed   round: `_read_task` raises 404 for a forbidden project  -> 3 failed
    """
    api = FakeAPI(buckets=STAGES)
    secret = api.add_project("secret", buckets=STAGES)
    hidden = api.create_task(secret["id"], "not yours")
    api.forbid_project(secret["id"])
    gone = api.add_task("about to vanish", "Queue")
    api.vanish(gone["id"])
    with pytest.raises(VikunjaError) as forbidden:
        api.get_task(hidden["id"])
    with pytest.raises(VikunjaError) as deleted:
        api.get_task(gone["id"])
    assert (forbidden.value.status, deleted.value.status) == (403, 404)


def test_drop_kanban_view_darkens_the_BOARD_and_leaves_everything_else_readable():
    """The route #1198 measured live (`DELETE /projects/<pid>/views/<kanban>` -> 200, no
    permission change), and the one route into `_offboard_predecessor`'s unreadable-board branch
    that anyone has measured. Not the only one that exists — `_foreign_stages` renders
    403-on-the-project, 404-on-the-project and no-kanban-view alike as None. What the other
    PERMISSION route cannot do is reach this branch: an unshared project 403s the TASK one branch
    earlier.

    Every clause is asserted because the fixture's value is precisely that these four facts hold
    TOGETHER: the board read fails, the far task still reads, its `done` can still be written
    (that is the escape the refusal advertises), and the relation is still embedded in the
    successor's payload.

    control 0 failed   round: `kanban_view` returns the stored view regardless   -> 10 failed
    """
    api = FakeAPI(buckets=STAGES)
    neighbour = api.add_project("neighbour", buckets=STAGES, identifier="NB")
    far = api.create_task(neighbour["id"], "the far half")
    here = api.add_task("my card", "Queue")
    api.add_relation(here["id"], far["id"], "blocked")
    api.drop_kanban_view(neighbour["id"])

    with pytest.raises(VikunjaError) as err:
        api.kanban_view(neighbour["id"])
    assert err.value.status == 404
    assert api.views(neighbour["id"]) == []
    assert api.get_task(far["id"])["title"] == "the far half"
    assert api.update_task(far["id"], done=True)["done"] is True
    embedded = api.get_task(here["id"])["related_tasks"]["blocked"]
    assert [t["id"] for t in embedded] == [far["id"]]
