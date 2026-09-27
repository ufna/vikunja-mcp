"""The `search` tool's server-side semantics, pinned against a REAL Vikunja.

The unit tests in tests/unit/test_search_tool.py encode what live 2.6.0 answered
(2026-09-13); a FakeAPI cannot be evidence about a server, so the claims the tool's
docstring makes about `s=` are re-measured here on the version CONTRIBUTING pins for the
integration container. Two claims are version-sensitive enough to be worth a real server:

- `s=` matches the WHOLE query as ONE substring over title AND description. The tool's
  docstring teaches agents to pass one distinctive word; if a later Vikunja starts
  word-splitting, that guidance goes stale and this goes red — a WELCOME red, same shape
  as test_ref_is_not_searchable.py's.
- done-state visibility: on 2.6.0 the include_done param is IGNORED and done tasks come
  back regardless. What the container does decides what `Workflow.search` may promise —
  measure here first, and if the versions disagree, the divergence belongs in api.py's
  comment, not in a test that only re-states one version's belief.
"""
import uuid

import httpx
import pytest

from tests.integration.conftest import BASE, _api

pytestmark = pytest.mark.skipif(not BASE, reason="VIKUNJA_TEST_URL not set")


@pytest.fixture(scope="module")
def seeded(boss_jwt):
    """One project holding a task whose title AND description each carry a token unique to
    this run, so searches cannot collide with anything a previous run left behind."""
    h = {"Authorization": f"Bearer {boss_jwt}"}
    token = uuid.uuid4().hex[:10]
    project = httpx.put(
        _api("/projects"), headers=h, json={"title": f"search-tool-{token}"},
    )
    project.raise_for_status()
    pid = project.json()["id"]
    task = httpx.put(
        _api(f"/projects/{pid}/tasks"),
        headers=h,
        json={"title": f"frobnicator {token} haystack", "description": f"body token {token}"},
    )
    task.raise_for_status()
    return h, token, task.json()


def _search(headers, query):
    r = httpx.get(_api("/tasks"), headers=headers, params={"s": query})
    r.raise_for_status()
    return r.json() or []


def test_search_finds_a_task_by_a_word_from_its_title(seeded):
    """CONTROL: the positive case every negative assertion below depends on."""
    headers, token, task = seeded
    hits = _search(headers, token)
    assert task["id"] in [t["id"] for t in hits]


def test_search_matches_the_description_too(seeded):
    headers, token, task = seeded
    hits = _search(headers, f"body token {token}")
    assert task["id"] in [t["id"] for t in hits]


def test_the_whole_query_is_matched_as_one_substring(seeded):
    """Both words appear in the title, separated — a word-splitting server would find the
    card; the measured substring server must not. This is the measurement behind the
    docstring's 'pass one distinctive word'."""
    headers, token, task = seeded
    assert _search(headers, "frobnicator haystack") == []


def test_a_done_task_still_comes_back(seeded):
    """2.6.0 measured: include_done ignored, done tasks always answer `s=`. Pinned here so
    a version that HIDES done cards from `s=` shows itself as a red here instead of as a
    silent blind spot in every duplicate check the tool runs."""
    headers, token, task = seeded
    r = httpx.post(_api(f"/tasks/{task['id']}"), headers=headers, json={"done": True})
    r.raise_for_status()
    hits = _search(headers, token)
    assert task["id"] in [t["id"] for t in hits]
    found = next(t for t in hits if t["id"] == task["id"])
    assert found["done"] is True
