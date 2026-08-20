"""`vikunja-mcp workspace` against REAL git in tmp_path (a local origin, no network).

A fake would share this module's model of git and prove nothing about the one behaviour that
matters: that housekeeping can never destroy an agent's unpushed work.
"""
import fcntl
import json
import os
import re
import shutil
import subprocess
import time
import tomllib
from pathlib import Path

import httpx
import pytest

from tests.unit.fakes import FakeAPI
from vikunja_mcp import workspace_cmd
from vikunja_mcp.api import _MAX_UNPROVEN_PAGES as MAX_UNPROVEN_PAGES
from vikunja_mcp.api import VikunjaAPI, VikunjaError
from vikunja_mcp.config import ENV_WORKTREE_ROOT
from vikunja_mcp.workflow import STAGES, Workflow
from vikunja_mcp.workspace_cmd import (
    ReadDeadlineExceeded,
    WorkspaceError,
    ensure_workspace,
    gc_workspaces,
    list_worktrees,
    release_workspace,
    run_workspace,
    worktree_root,
)


def _git(cwd, *args):
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


# --- VMCP-76 (525): a stand-in this file parks must never outlive the test that parked it ---
#
# Two tests need a git child that HANGS, so that the module's own timeout SIGKILLs the git process
# sitting on top of it: a smudge filter parks `worktree add`'s checkout (`_half_created_tree`) and
# an ssh stand-in parks `git fetch` (`test_the_fetch_under_the_lock_...`). `subprocess.run`'s kill
# reaches the DIRECT child ONLY, so whatever is parked UNDER it is reparented onto PID 1 and keeps
# running long after the test that built it has ended.
#
# MEASURED before this fix — `ps` around a run given its own `--basetemp`, so that a sibling agent
# running this same suite could not have its identically named processes counted as ours. ONE
# half-created test left THREE processes behind, all with their cwd inside the tmp worktree:
# `git reset --hard --no-recurse-submodules` at PPID 1, its `/bin/sh …/slow-smudge.sh`, and that
# shell's own `sleep 30` child — the third is easy to miss, because grepping `ps` for the two NAMES
# you expect does not match a bare `sleep`. They were still alive at t+56s and gone by t+61s past
# a 2.70s test (the test's own duration is a sample, 2.65–2.93s across runs, not a constant). The
# open fd on `<tmp>/work/.git/worktrees/task-<id>/index.lock` belongs to the CHECKOUT alone, not to
# all three: `lsof` on the shell lists its cwd, its pipes and the script, and no lock.
#
# `git log` disagrees with both of those, and this file is the measured side. b1139db's own commit
# MESSAGE names only two of the three ("`git reset …` plus its `/bin/sh slow-smudge.sh`") and
# attributes the lock to both ("each with its cwd inside the tmp worktree and an open fd on that
# tree's index.lock") — while the file that same commit landed already said THREE, and the lock on
# the checkout alone. A pushed message cannot be rewritten, so the correction is named here rather
# than left for a `git log -S` reader to take as measured.
#
# Counted rather than derived, by polling `ps` through one pre-fix run of this FILE and unioning
# distinct pids: THIRTY-TWO processes — 6 orphaned checkouts, one per `_half_created_tree` call
# site as the file then stood; the 12 filter shells those spawn, TWO per site because the filter
# runs once per tracked file; those shells' 12 `sleep 30` children; and the fetch test's own
# stand-in plus its sleep. An arithmetic guess of "two per call site" is exactly half of that.
#
# What the leak was OBSERVED not to touch — inferred from where it wrote, not from a constraint
# that forbids it. A full `lsof -p` on one such family is 24 rows across the three, and inside that
# run's own basetemp sit all three cwds plus the only two regular-file rows the loader did NOT map
# (`txt`): the checkout's `3u` on index.lock and the shell's `255r` on its script — the `sleep` has
# no such row at all. The other 19 name nothing of ours: eight shared system paths (the git, bash
# and sleep binaries, three `dyld`, a locale table, `/dev/null` on the checkout's fd 0) and eleven
# anonymous PIPE rows carrying no path to read — the stdio slots, minus the checkout's fd 0 which
# is that `/dev/null`, plus an fd-4 pipe on each of the three. Note
# what that leaves standing: "every open fd sat inside basetemp" is false, and so is any version
# resting on "regular file" or "binary" — a locale table is a mapped REG row and sits outside. It
# is the same overreach retracted higher up about the index.lock, so the claim is now the cwds and
# those two handles. The cost is background processes holding a tmp dir pytest owns and may be
# deleting.
#
# So the stand-ins PARK ON A FILE instead of sleeping a flat 30s, and the fixture unlinks it. The
# reap is COOPERATIVE on purpose. `pgrep`-by-name is out: this suite runs beside sibling agents
# spawning processes with the very same argv, and killing one of those is a far worse failure than
# the leak. A blind SIGKILL of a pid recorded seconds ago is out for the same class of reason — a
# pid can be recycled. The pids are recorded for the WAIT: teardown PROVES the processes are gone
# rather than assuming the unlink worked.
_PARK_HOLD = "park-hold"      # while this file exists, a parked stand-in keeps parking
_PARK_PIDS = "park-pids"      # each stand-in appends its own pid here
_PARK_CEILING = 300           # x 0.1s, the backstop for a pytest that is ITSELF killed: teardown
                              # never runs then, and an UNBOUNDED park would leak worse than the
                              # bug being fixed. MEASURED at 33.2s PER PARK with the hold file
                              # never removed (30s of sleeping plus the loop's own fork/exec
                              # overhead) — and a half-created tree parks once per tracked file,
                              # so the survival bound a killed pytest actually leaves behind is
                              # TWO of those, measured at 66.0s. That is the flat `sleep 30` this
                              # replaced plus ~10%, i.e. no regression, but it is not below it


def _parking_script(tmp_path: Path, name: str, tail: str = "") -> Path:
    """An executable stand-in that parks until the fixture releases it, and says it is there.

    Hand it to something GIT will spawn (a filter, `GIT_SSH_COMMAND`), which is the whole point:
    the process that leaks is the git one in between. Launching it straight from a test body would
    make the test runner itself the parent the reaper derives — see `_live_parent`, which refuses
    that rather than waiting for pytest to exit.
    """
    script = tmp_path / name
    script.write_text(
        "#!/bin/sh\n"
        f'echo "$$" >> "{tmp_path / _PARK_PIDS}"\n'
        "i=0\n"
        f'while [ -e "{tmp_path / _PARK_HOLD}" ] && [ "$i" -lt {_PARK_CEILING} ]; do\n'
        "  sleep 0.1\n"
        "  i=$((i + 1))\n"
        "done\n"
        f"{tail}"
    )
    script.chmod(0o755)
    (tmp_path / _PARK_HOLD).write_text("")
    return script


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:      # alive, and no longer ours — i.e. the pid was recycled
        return True
    return True


def _ps_field(pid: int, field: str) -> str:
    return subprocess.run(
        ["ps", "-o", f"{field}=", "-p", str(pid)], capture_output=True, text=True
    ).stdout.strip()


def _live_parent(pid: int) -> set[int]:
    """The pid's parent, when that parent is something the reaper may legitimately wait out.

    Two parents it may not. PID 1 (or a blank answer, i.e. the child has just exited) means there
    is nothing left to wait for. THIS process means the stand-in was launched by the test runner
    rather than by git, and waiting on it would hang out the whole deadline and then fail on
    pytest's own pid. Not reachable with today's two stand-ins — both are spawned by git — but it
    costs one comparison to keep it unreachable rather than merely undocumented.
    """
    ppid = _ps_field(pid, "ppid")
    if not ppid.isdigit() or int(ppid) <= 1 or int(ppid) == os.getpid():
        return set()
    return {int(ppid)}


def _process_command(pid: int) -> str:
    return _ps_field(pid, "command")


def _parked_pids(tmp_path: Path) -> set[int]:
    """The stand-ins that recorded themselves, PLUS the live parent of any still running.

    The parent is what actually leaks in the smudge case — it is the orphaned
    `git reset --hard --no-recurse-submodules` (verified live: `sh …/slow-smudge.sh` at pid 1991
    had ppid 1990, which `ps` showed as exactly that git process) — so it has to be waited on.
    But it is DERIVED HERE rather than recorded by the script, because a parent is only ever
    safe to wait on while it is alive: the fetch stand-in's parent is the git that was SIGKILLed,
    and a pid recorded a second ago and since dead may already have been recycled onto something
    unrelated, which the reaper would then wait out the full deadline for and fail on. `ppid > 1`
    is the same rule in its other form — a stand-in already reparented onto PID 1 has no parent
    left to wait for.
    """
    pidfile = tmp_path / _PARK_PIDS
    if not pidfile.exists():
        return set()
    recorded = {int(token) for token in pidfile.read_text().split()}
    live = [child for child in recorded if _pid_alive(child)]
    return recorded | {pid for child in live for pid in _live_parent(child)}


def _reap_parked_children(tmp_path: Path, deadline: float = 30.0) -> None:
    """Release every parked stand-in and WAIT until nothing it left behind is still running."""
    # BEFORE the unlink: while everything is still parked, the orphaned checkout is reliably
    # visible as a live parent. That holds only WHILE something is parked — with the ceiling
    # forced to 0.1s and a repo big enough to keep the checkout churning, a live checkout was
    # measured missing from 8 of 20 samples — so it rests on every test body here THAT PARKS
    # finishing inside `_PARK_CEILING`. THE MARGIN IS ONE ORDER OF MAGNITUDE, NOT TWO, and which
    # it is matters because this is a number an editor DIVIDES. Instrumenting this function over
    # three full runs: nine reap calls each, of which EIGHT are real park windows (the ninth is
    # this file's hand-driven teardown firing a second time, after the hold file is already gone,
    # so its stand-in never parks). Those windows run ~1.8s median, WORST 2.26s — so against the
    # 33.2s the constant records (re-timed standalone here at 32.76s and 33.13s) the headroom is
    # ~15x. Quote the worst, not the median: the median is what usually happens, the worst is
    # what the guarantee has to cover.
    #
    # WHERE IT STOPS BEING A GUARANTEE, in the units you would actually edit. Forcing the
    # constant to 25 (~2.8s of real parking) still kept each of the four `_half_created_tree`
    # tests swept — four of the seven that use the helper — inside ONE park; at 20 (~2.2s, just
    # under that 2.26s
    # worst window) the longest outran its first park and teardown caught the SECOND stand-in
    # 0.01s old — green, but by timing rather than by construction. So 25 holds and 20 does not;
    # anything at or below 20 is already in the degraded band. In wall-clock that floor is ~2.3s
    # — INTERPOLATED between those two measured points, never run on its own, and deliberately
    # not restated as its own multiple of the ceiling, since that is a second ratio nobody
    # measured. What "two orders" would have promised, ~100x, overstates the real ~15x by about
    # sevenfold, and in the direction that invites the cut.
    #
    # Below 20 the degradation stays SILENT for a while, which is the danger: at 10 the reap saw the
    # checkout in all four, because this repo leaves it almost no work between its two tracked
    # files before the next stand-in parks — under 0.03s, DERIVED from the `pids=2` rows rather
    # than timed. The 0.1s/big-repo sample above (the previous round's measurement, not re-run
    # here) is that same gap opened wide enough to show. Push down to 5 and it turns loud
    # instead: two parks then total ~1.1s, inside the 2s `_GIT_TIMEOUT` `_half_created_tree`
    # patches in, so `worktree add` SUCCEEDS and these tests fail DID NOT RAISE — 3 of those 4 in
    # one run, 4 of 4 in the next. Where quiet flips to loud was not sampled: it is somewhere
    # between 10 and 5, and the same arithmetic puts it near 9.
    watched = _parked_pids(tmp_path)
    (tmp_path / _PARK_HOLD).unlink(missing_ok=True)
    alive = {pid for pid in watched if _pid_alive(pid)}
    end = time.monotonic() + deadline
    while alive and time.monotonic() < end:
        time.sleep(0.05)
        # UNION, not re-read: releasing the hold lets the orphaned checkout resume and spawn the
        # NEXT stand-in (one per tracked file), which records itself only now — while the parent
        # derived above drops back out of the file's view as soon as its child exits. Either half
        # alone returns with something still running.
        watched |= _parked_pids(tmp_path)
        alive = {pid for pid in watched if _pid_alive(pid)}
    assert not alive, (
        f"{sorted(alive)} outlived the test by {deadline:.0f}s after {tmp_path / _PARK_HOLD} was "
        f"removed: a stand-in stopped honouring the hold file, or something else is parking here"
    )


def _reaping_parked_children(tmp_path: Path):
    """The `repo` fixture's teardown half, as a plain generator SO THAT A TEST CAN DRIVE IT.

    The whole point of putting the reap after a `yield` is that pytest runs it whatever the test's
    outcome — and a test that passes cannot demonstrate that. Driving this generator by hand runs
    the very code the fixture runs, against real processes, in one round.
    """
    yield
    _reap_parked_children(tmp_path)


@pytest.fixture
def _parked_children_reaped(tmp_path):
    yield from _reaping_parked_children(tmp_path)


@pytest.fixture
def repo(tmp_path, monkeypatch, _parked_children_reaped):
    """A work repo on `main` with a local bare origin it has already pushed to.

    It requests `_parked_children_reaped` so that EVERY test built on this fixture is covered
    without having to know the leak exists — set up first, so torn down last, i.e. after
    everything else in the test has finished writing.
    """
    # A REAL review finding: once the pump exports VIKUNJA_WORKTREE_ROOT machine-wide (the
    # exact point of this feature), an agent running this suite inside its own worktree would
    # otherwise get every test here steered at the AMBIENT root instead of tmp_path — and the
    # litter it writes there survives the test run and poisons whatever runs next.
    monkeypatch.delenv(ENV_WORKTREE_ROOT, raising=False)
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(origin)],
                   check=True, capture_output=True)
    work = tmp_path / "work"
    work.mkdir()
    _git(work, "init", "-b", "main")
    _git(work, "config", "user.email", "t@example.com")
    _git(work, "config", "user.name", "Tester")
    (work / "README.md").write_text("hi\n")
    _git(work, "add", "README.md")
    _git(work, "commit", "-m", "init")
    _git(work, "remote", "add", "origin", str(origin))
    _git(work, "push", "-u", "origin", "main")
    return work


def test_create_makes_a_worktree_on_a_task_branch(repo):
    res = ensure_workspace(42, cwd=repo)
    path = Path(res["path"])
    assert res["created"] is True and res["branch"] == "task/42"
    assert path.is_dir() and (path / "README.md").exists()
    assert path.parent == worktree_root(repo) == repo.parent / "work.worktrees"
    assert _git(path, "rev-parse", "--abbrev-ref", "HEAD") == "task/42"


def test_create_is_idempotent(repo):
    first = ensure_workspace(42, cwd=repo)
    second = ensure_workspace(42, cwd=repo)
    assert second["path"] == first["path"] and second["created"] is False


def test_create_reuses_an_existing_branch_and_keeps_its_commits(repo):
    """The resume-after-crash path: the agent's unfinished commits must survive."""
    path = Path(ensure_workspace(42, cwd=repo)["path"])
    (path / "wip.txt").write_text("half done\n")
    _git(path, "add", "wip.txt")
    _git(path, "commit", "-m", "wip")
    sha = _git(path, "rev-parse", "HEAD")
    _git(repo, "worktree", "remove", str(path))          # agent died, tree gone, branch kept

    again = ensure_workspace(42, cwd=repo)
    assert again["created"] is True and again["branch"] == "task/42"
    assert _git(Path(again["path"]), "rev-parse", "HEAD") == sha


def test_review_role_is_a_separate_detached_tree(repo):
    build = ensure_workspace(42, cwd=repo)
    head = _git(repo, "rev-parse", "HEAD")
    review = ensure_workspace(42, role="review", at=head, cwd=repo)
    assert review["path"] != build["path"]
    assert Path(review["path"]).name == "review-42"
    assert _git(Path(review["path"]), "rev-parse", "HEAD") == head
    assert _git(Path(review["path"]), "rev-parse", "--abbrev-ref", "HEAD") == "HEAD"  # detached


def test_release_removes_a_clean_pushed_tree_and_its_branch(repo):
    path = Path(ensure_workspace(42, cwd=repo)["path"])
    res = release_workspace(42, cwd=repo)
    assert res["released"] is True
    assert not path.exists()
    assert "task/42" not in _git(repo, "branch", "--list", "task/42")


def test_release_refuses_a_dirty_tree(repo):
    path = Path(ensure_workspace(42, cwd=repo)["path"])
    (path / "scratch.txt").write_text("uncommitted\n")
    res = release_workspace(42, cwd=repo)
    assert res["released"] is False and "dirty" in res["reason"]
    assert path.exists() and (path / "scratch.txt").exists()


def test_release_refuses_unpushed_commits(repo):
    """THE guard: housekeeping must never be how an agent's work disappears."""
    path = Path(ensure_workspace(42, cwd=repo)["path"])
    (path / "feature.txt").write_text("real work\n")
    _git(path, "add", "feature.txt")
    _git(path, "commit", "-m", "real work")
    res = release_workspace(42, cwd=repo)
    assert res["released"] is False and "commit" in res["reason"]
    assert path.exists()
    assert _git(path, "log", "--oneline", "-1")


def _commit_a_gitlink(work: Path, path: str = "sub") -> None:
    """Put a GITLINK (mode 160000) in the index and on origin, WITHOUT populating it.

    Plumbing rather than `git submodule add`, like `_bump_gitlink_on_origin` far below and for its
    reasons: the local-protocol config stays out of the shape, and the index IS what the guard
    reads. Measured equivalent to a real submodule on both facts this depends on — `git worktree
    add` materialises an EMPTY directory at the gitlink's path, and the new worktree's own index
    carries the same `160000` entry.
    """
    sha = _git(work, "rev-parse", "HEAD")          # any commit object serves as the pointer
    _git(work, "update-index", "--add", "--cacheinfo", f"160000,{sha},{path}")
    _git(work, "commit", "-m", "add a gitlink")
    _git(work, "push", "origin", "HEAD:main")


def test_release_refuses_a_tree_whose_gitlink_directory_is_not_empty(repo):
    """VMCP-266. The victim here is untracked AND NOT ignored — precisely what the dirty guard
    PROMISES to hold — and before this refusal it died at rc 0 with no `--force` and no report.

    Why the ordinary guard cannot see it: `git status` does not answer about paths under a gitlink,
    so `_inspect_status` returns `([], [])` and `release_workspace` answered `{"released": true}`
    with no `code` and no `removed_ignored`. Measured end to end on a real submodule and a worktree
    the module itself created, plus the POSITIVE CONTROL that keeps the empty `dirty` honest: the
    same probe in the same tree DOES report an untracked file at the tree ROOT (`?? ...`), so the
    blindness is the gitlink's shadow and not a broken probe.

    The neighbouring test is the other half of this pin and must stay next to it: an EMPTY gitlink
    directory still releases, so this guard cannot be satisfied by refusing everything.

    MUTATION-CHECKED in a separate clone, one selection (`tests/unit/test_workspace_cmd.py`), `-q`
    dropped so `collected` is readable, `FAILED `- and `ERROR `-prefixed lines counted separately,
    `__pycache__` purged and `PYTHONDONTWRITEBYTECODE=1` each round, `vikunja_mcp.__file__` printed
    each round, control at BOTH ends. **control 0 failed (0 ERROR, collected 226)** and every count
    here is a delta on it, same selection size in every round: delete the refusal and leave the
    probe -> 1 failed (this test); make the probe return `[]` always -> 1 failed (this test); drop
    the code from the grid test's row list -> 1 failed (the grid test, on its `declared <= grid`
    assertion); drop it from the derived policy-comment list -> 1 failed
    (test_the_policy_comment_enumerations_are_derived_from_the_code); **control 0 failed** at the
    end, clone left clean.

    THE EACCES BRANCH WAS FOUND UNPINNED BY THAT SWEEP AND IS PINNED NOW, which is why the round
    for it is reported separately and against its OWN control: at the state the run above measured
    (collected 225 throughout, control 0 failed both ends), flipping the unreadable-directory
    branch to `populated = False` gave **0 failed** — nothing in the file objected to a guard that
    releases a tree it could not look inside. With
    test_a_gitlink_directory_that_cannot_be_READ_counts_as_populated added, the same mutation on
    the rebased tree reads **control 0 failed (collected 226) -> 1 failed**, that test alone. Two
    runs rather than one table because the selection SIZE moved between them, and a count only
    means something against the control that shares its selection.
    """
    _commit_a_gitlink(repo)
    path = Path(ensure_workspace(42, cwd=repo)["path"])
    (path / "sub").mkdir(exist_ok=True)
    (path / "sub" / "precious.txt").write_text("untracked AND NOT ignored\n")

    res = release_workspace(42, cwd=repo)

    assert res["released"] is False
    assert res["code"] == workspace_cmd.CODE_POPULATED_GITLINK
    assert "sub" in res["reason"]
    assert path.exists()
    # BY CONTENT, never `exists()`: a probe that only asks whether the path resolves answers False
    # on an unreadable directory too, which is the one case this guard treats as populated.
    assert (path / "sub" / "precious.txt").read_text() == "untracked AND NOT ignored\n"


def test_a_gitlink_directory_that_cannot_be_READ_counts_as_populated(repo, monkeypatch):
    """The fail-closed direction, pinned because the sweep found it unpinned: flip that one branch
    to `populated = False` and every other test in this file stays green (control 0 failed,
    mutation 0 failed) — the guard would then release a tree whose gitlink directory it could not
    look inside, which is the one reading that destroys work.

    `os.scandir` raising rather than a real `chmod 000`, deliberately: measured on the stand, the
    real thing does raise PermissionError here and the release IS refused, but as a unit pin it
    would depend on the test not running as root — root reads a 000 directory perfectly well, so
    the pin would silently invert in exactly the environment nobody checks. The branch under test
    is "scandir raised", so raise it."""
    _commit_a_gitlink(repo)
    path = Path(ensure_workspace(42, cwd=repo)["path"])
    real = os.scandir

    def refuse(target):
        if Path(target) == path / "sub":
            raise PermissionError(13, "Permission denied")
        return real(target)

    monkeypatch.setattr(workspace_cmd.os, "scandir", refuse)
    res = release_workspace(42, cwd=repo)

    assert res["released"] is False
    assert res["code"] == workspace_cmd.CODE_POPULATED_GITLINK
    assert path.exists()


def test_release_still_removes_a_tree_whose_gitlink_directory_is_empty(repo):
    """The control for the guard above: the state the pipeline ACTUALLY produces must still reap.

    `git worktree add` never populates a submodule (no `git submodule` call in the package, no
    `--recurse-submodules` on any of the three add forms), so an empty gitlink directory is the
    normal condition of every tree in a consumer checkout that has one. If this went red the guard
    would have stopped the reaper on every such tree, which is how `--gc` gets turned off."""
    _commit_a_gitlink(repo)
    path = Path(ensure_workspace(42, cwd=repo)["path"])
    assert (path / "sub").is_dir() and not list((path / "sub").iterdir())

    res = release_workspace(42, cwd=repo)

    assert res["released"] is True and "code" not in res
    assert not path.exists()


def test_release_of_a_missing_tree_is_not_an_error(repo):
    res = release_workspace(999, cwd=repo)
    assert res["released"] is False and "no worktree" in res["reason"]


def test_occupied_path_that_is_not_a_worktree_is_refused(repo):
    squatter = worktree_root(repo) / "task-42"
    squatter.mkdir(parents=True)
    (squatter / "precious.txt").write_text("do not clobber\n")
    with pytest.raises(WorkspaceError, match="not a registered worktree"):
        ensure_workspace(42, cwd=repo)
    assert (squatter / "precious.txt").exists()


def test_worktree_root_honours_an_explicit_override(repo, monkeypatch):
    monkeypatch.setenv("VIKUNJA_WORKTREE_ROOT", str(repo.parent / "elsewhere"))
    res = ensure_workspace(42, cwd=repo)
    assert Path(res["path"]).parent == repo.parent / "elsewhere"


def test_list_worktrees_reports_slashed_branch_names_intact(repo):
    ensure_workspace(42, cwd=repo)
    branches = {wt["branch"] for wt in list_worktrees(repo)}
    assert "task/42" in branches       # not "42" — refs/heads/task/42 must not be split


# --- review round 1, Finding 1: a symlinked root must still be found by realpath ---

def test_worktree_root_through_a_symlink_is_found_by_realpath(repo, monkeypatch):
    """`git worktree list` prints the REALPATH; worktree_root must resolve too, or a
    symlinked VIKUNJA_WORKTREE_ROOT makes a live, registered tree invisible to `_find` —
    breaking BOTH the resume-after-crash path (ensure_workspace re-clobbers a live tree)
    and release (falsely reports 'no worktree', leaking the tree forever)."""
    real = repo.parent / "real-trees"
    real.mkdir()
    link = repo.parent / "link-trees"
    link.symlink_to(real, target_is_directory=True)
    monkeypatch.setenv(ENV_WORKTREE_ROOT, str(link))

    first = ensure_workspace(42, cwd=repo)
    second = ensure_workspace(42, cwd=repo)          # resume path: must find the live tree
    assert second["created"] is False and second["path"] == first["path"]

    res = release_workspace(42, cwd=repo)            # release path: must find it too
    assert res["released"] is True


# --- review round 1, Finding 4: "head" (a sha) vs list_worktrees's "detached" (a bool) ---

def test_review_payload_head_sha_is_distinct_from_list_worktrees_detached_bool(repo):
    """Pin the two meanings so they can never drift back onto one key: ensure_workspace's
    review payload carries the checked-out SHA under 'head'; list_worktrees's 'detached' is
    git's own porcelain BOOL. Reusing one key for both only "worked" because a hex string
    is truthy."""
    head = _git(repo, "rev-parse", "HEAD")
    review = ensure_workspace(42, role="review", at=head, cwd=repo)
    assert review["head"] == head
    assert "detached" not in review

    entries = {wt["path"]: wt for wt in list_worktrees(repo)}
    wt = entries[Path(review["path"])]
    assert wt["detached"] is True
    assert isinstance(wt["detached"], bool)


# --- review round 1, Minor A: a review tree's unique-history guard must not misfire ---

def test_release_of_a_review_tree_reachable_from_a_branch_is_allowed(repo):
    """Case A. A review pinned at a build branch's tip — BY DEFINITION not yet on
    origin/main — must still be releasable: the commit is reachable from task/<id>, so
    nothing is lost. (Round 1 called this "ignores the unpushed guard"; round 2 replaced
    that blanket skip with a reachability check, and this case must keep passing under it —
    the branch-history guard above still does not apply to a detached tree, but the
    reachability check in the `else` branch below does, and task/<id> satisfies it.)"""
    build = ensure_workspace(8, cwd=repo)
    build_path = Path(build["path"])
    (build_path / "wip.txt").write_text("wip\n")
    _git(build_path, "add", "wip.txt")
    _git(build_path, "commit", "-m", "wip")
    tip = _git(build_path, "rev-parse", "HEAD")       # ahead of origin/main, never pushed

    review = ensure_workspace(8, role="review", at=tip, cwd=repo)
    res = release_workspace(8, role="review", cwd=repo)
    assert res["released"] is True
    assert not Path(review["path"]).exists()


def test_release_of_an_ordinary_review_tree_is_allowed(repo):
    """The everyday path, not a corner case: a review tree at origin/main with nothing
    committed inside it must RELEASE. Same code lines as Case A below, but this is the one
    that will actually run thousands of times — worth pinning on its own."""
    review = ensure_workspace(7, role="review", cwd=repo)   # at origin/main, untouched
    res = release_workspace(7, role="review", cwd=repo)
    assert res["released"] is True
    assert not Path(review["path"]).exists()


def test_release_of_a_review_tree_keeps_a_commit_made_inside_it(repo):
    """Case B — THE regression round 1 introduced: a reviewer can commit INSIDE a detached
    review tree (the dirty guard only catches uncommitted changes; a fresh commit makes the
    tree clean again). That commit is reachable from NO ref — `git worktree remove` has no
    unpushed-commit check for a detached HEAD, and a later `gc` would prune the object
    outright once the worktree's reflog is gone with it. Must KEEP, and the object must
    genuinely survive (not just the call returning False)."""
    review = ensure_workspace(7, role="review", cwd=repo)   # at origin/main by default
    path = Path(review["path"])
    (path / "review-notes.md").write_text("looks good, minor nit\n")
    _git(path, "add", "review-notes.md")
    _git(path, "commit", "-m", "review notes")
    sha = _git(path, "rev-parse", "HEAD")

    res = release_workspace(7, role="review", cwd=repo)

    assert res["released"] is False and "reachable from no ref" in res["reason"]
    assert path.exists()
    # NOT `rev-parse` — given a full 40-hex string, `rev-parse` echoes it back with exit 0
    # WITHOUT checking the object actually exists (verified against real git: even after the
    # object is truly gone — worktree remove + `reflog expire --expire-unreachable=now --all`
    # + `gc --prune=now` — `rev-parse <sha>` still prints it back). `cat-file -e` is the one
    # that actually looks the object up; `check=True` in the _git helper makes a missing
    # object raise, so this line can genuinely fail.
    _git(repo, "cat-file", "-e", f"{sha}^{{commit}}")


# --- review round 1, Minor B: an unknown role must be refused, not silently coerced ---

def test_ensure_workspace_rejects_an_unknown_role(repo):
    with pytest.raises(WorkspaceError, match="unknown role"):
        ensure_workspace(42, role="Build", cwd=repo)      # wrong case is NOT "build"


def test_release_workspace_rejects_an_unknown_role(repo):
    with pytest.raises(WorkspaceError, match="unknown role"):
        release_workspace(42, role="bogus", cwd=repo)


# --- review round 1, Finding 3: the CLI entry point + dispatch are a contract, not a demo ---

def test_run_workspace_release_of_missing_tree_is_exit_0(repo, monkeypatch, capsys):
    """A refusal is a NEGATIVE VERDICT, not a CLI failure: the command RAN, exit 0.

    Task 4 review (Minor): "path" now names WHERE a worktree for this task would have been —
    even a "nothing to release" verdict must be actionable, not just a bare task id.

    VMCP-68: and it carries a machine-readable `code` beside the prose `reason`, asserted here by
    WHOLE-DICT equality on purpose — the JSON line is a contract SKILL.md tells agents to branch
    on, so a key silently appearing or vanishing has to fail somewhere."""
    monkeypatch.chdir(repo)
    code = run_workspace(["--release", "999"])
    assert code == 0
    out = json.loads(capsys.readouterr().out.strip())
    assert out == {"released": False, "task_id": 999, "role": "build",
                   "path": str(repo.parent / "work.worktrees" / "task-999"),
                   "code": workspace_cmd.CODE_NO_WORKTREE,
                   "reason": "no worktree for this task"}


def test_run_workspace_error_is_one_json_line_exit_1(tmp_path, monkeypatch, capsys):
    """A real failure (here: not even a git repo) is one {"error"} line and exit 1 — never
    silently swallowed, never a bare traceback on a CLI a script parses."""
    monkeypatch.chdir(tmp_path)                        # no git repo at all here
    code = run_workspace(["42"])
    assert code == 1
    lines = [ln for ln in capsys.readouterr().out.splitlines() if ln.strip()]
    assert len(lines) == 1
    err = json.loads(lines[0])
    assert "WorkspaceError" in err["error"]


def test_run_workspace_with_no_args_is_one_json_error_line_exit_1(tmp_path, monkeypatch, capsys):
    # Harmless today (run_workspace raises before any git call), but isolate the cwd anyway —
    # this is one refactor away from touching whatever repo the test happens to run inside.
    monkeypatch.chdir(tmp_path)
    code = run_workspace([])
    assert code == 1
    err = json.loads(capsys.readouterr().out.strip())
    assert "task id" in err["error"]


def test_run_workspace_role_and_at_plumb_through_the_cli(repo, monkeypatch, capsys):
    monkeypatch.chdir(repo)
    head = _git(repo, "rev-parse", "HEAD")
    code = run_workspace(["42", "--role", "review", "--at", head])
    assert code == 0
    out = json.loads(capsys.readouterr().out.strip())
    assert out["role"] == "review" and out["head"] == head
    assert Path(out["path"]).name == "review-42"


# --- Task 4: workspace --gc — reap orphaned trees using tracker liveness ---

@pytest.fixture
def tracker():
    api = FakeAPI(buckets=STAGES)
    return api, Workflow(api, project_id=3)


def _grace_markers(tree: Path) -> list[Path]:
    """The mtimes VMCP-71's grace window reads, derived the way production derives them (git owns
    the `.git/worktrees/<n>` naming, so ask it rather than assemble the path)."""
    index = Path(_git(tree, "rev-parse", "--git-path", "index"))
    return [tree, index if index.is_absolute() else tree / index]


def _quiesce(tree: Path) -> None:
    """Age every marker so a DEAD tree reads as "gone quiet" and is eligible for the reaper NOW.

    VMCP-71 gave `--gc` a grace window: a dead tree touched within `_REAP_GRACE_SECONDS` is not
    inspected, so its agent cannot have its cwd removed between `advance(to='review')` and
    `--release`. (Until VMCP-300 that skip was also SILENT; it is now reported in `deferred`, and
    the tests below that assert three empty verdict lists say so explicitly.)

    Every test below that asserts a REAP (or a `kept` line, which is also a
    verdict only reached past the window) works on a tree created milliseconds earlier, so it has
    to say out loud that the tree has gone quiet. Call this AFTER the last git call in the tree —
    a commit or a `git status` rewrites the index and un-quiesces it.
    """
    old = time.time() - workspace_cmd._REAP_GRACE_SECONDS - 60
    for target in _grace_markers(tree):
        if target.exists():
            # MEASURED: a half-created tree (`locked initializing`) has no index FILE at all — the
            # kill lands before git writes one, which is also why `git status` there reports every
            # tracked file as a staged deletion. Production stats each marker independently for
            # exactly this reason; the helper must not assume both exist either.
            os.utime(target, (old, old))
    # Self-check against PRODUCTION's own reader, so that a helper which stops covering a marker
    # fails here, legibly, instead of turning every reap assertion below into a silent skip.
    quiet_for = time.time() - workspace_cmd._last_activity(tree)
    assert quiet_for >= workspace_cmd._REAP_GRACE_SECONDS, (
        f"{tree} still reads as active ({quiet_for:.0f}s) — _grace_markers is missing a marker "
        f"that _last_activity looks at"
    )


def test_gc_reaps_a_tree_whose_task_is_no_longer_active(repo, tracker):
    api, wf = tracker
    path = Path(ensure_workspace(42, cwd=repo)["path"])          # nothing on the board -> dead
    _quiesce(path)
    res = gc_workspaces(cwd=repo, workflow=wf)
    assert [r["task_id"] for r in res["released"]] == [42]
    assert not path.exists()


def test_gc_keeps_a_tree_whose_task_is_still_in_build(repo, tracker):
    api, wf = tracker
    task = api.add_task("live work", "Queue")
    wf.claim(task["id"])
    path = Path(ensure_workspace(task["id"], cwd=repo)["path"])
    res = gc_workspaces(cwd=repo, workflow=wf)
    assert res["released"] == []
    assert path.exists()


def test_gc_keeps_a_review_tree_while_the_card_is_in_review(repo, tracker):
    api, wf = tracker
    task = api.add_task("under review", "Review")
    head = _git(repo, "rev-parse", "HEAD")
    path = Path(ensure_workspace(task["id"], role="review", at=head, cwd=repo)["path"])
    res = gc_workspaces(cwd=repo, workflow=wf)
    assert res["released"] == []
    assert path.exists()


def test_gc_keeps_a_quiesced_review_tree_only_because_its_card_is_in_review(repo, tracker):
    """The BOARD is what keeps a reviewer's tree, not the clock.

    This is the promise SKILL.md makes to a reviewer in plain words — while the card sits in
    Review the sweep will not touch your worktree, however long it has stood without a single
    write — and until now nothing tested it. The sibling directly above does not: its tree is
    created milliseconds before the sweep, so the GRACE WINDOW alone explains the skip and the
    role-keyed liveness carries no weight in that fixture at all. MEASURED three times as
    siblings landed underneath (f891add, 7742d07, 4fe44e4 — 2026-07-30/31), the count moving
    with the suite and the verdict never: make review trees never alive by role (`if task_id in
    (set() if role == "review" else alive[role])`) and the whole PRE-EXISTING unit suite stays
    GREEN — 613 then 628 passed, including all 109 of this file's and 563's source-ORDER pin in
    test_skill_contract.py, which reads the token `alive[role]` and cannot see what it now
    contains — while a 31-minute-quiet review tree with its card in Review is REAPED and its
    directory is gone. With this test present it is the only red in the repository.

    So ROLE has to be the only thing left explaining the outcome. Both trees below are review
    trees, both at the SAME age (quiesced past `_REAP_GRACE_SECONDS`), both clean and pushed,
    both swept in ONE call: the single difference is where their card sits. The control is
    additionally ALIVE as a build task, which pins that liveness is keyed on the ROLE rather
    than on "this id is somewhere on the board" — and that is not a contrived state, it is
    exactly what a `needs_work` verdict leaves behind.

    The second half is that verdict, i.e. what 563 could only state as prose: nothing moves but
    the board, and the next sweep takes the same tree away.
    """
    api, wf = tracker
    head = _git(repo, "rev-parse", "HEAD")

    reviewing = api.add_task("under review", "Queue")          # card IN Review -> tree lives
    wf.claim(reviewing["id"])
    wf.advance(reviewing["id"], to="build", spec="approach")
    wf.advance(reviewing["id"], to="review", worklog="done", evidence="abc1234")

    bounced = api.add_task("already back in build", "Queue")   # card NOT in Review -> tree dies
    wf.claim(bounced["id"])
    wf.advance(bounced["id"], to="build", spec="approach")     # alive as BUILD, never as REVIEW

    live = Path(ensure_workspace(reviewing["id"], role="review", at=head, cwd=repo)["path"])
    dead = Path(ensure_workspace(bounced["id"], role="review", at=head, cwd=repo)["path"])
    _quiesce(live)                                             # both aged identically, and past
    _quiesce(dead)                                             # the window, before ONE sweep

    res = gc_workspaces(cwd=repo, workflow=wf)

    assert [r["task_id"] for r in res["released"]] == [bounced["id"]]
    assert not dead.exists()
    assert live.exists()
    assert res["kept"] == [] and res["expected"] == []         # a live tree is skipped silently

    # Second half: the reviewer's own verdict moves the card out of Review and nothing else
    # moves — same tree, same mtimes, same sweep call. First say out loud that the tree really
    # is still quiet, so a future sweep that started WRITING in the trees it keeps (the VMCP-90
    # regression, from the other side) cannot quietly turn the reap below into a grace-window
    # artefact. `_last_activity` reads the index through `_git_inspect`, which takes no optional
    # locks, so asking the question does not itself disturb the answer.
    quiet_for = time.time() - workspace_cmd._last_activity(live)
    assert quiet_for >= workspace_cmd._REAP_GRACE_SECONDS, (
        f"the kept review tree stopped reading as quiet ({quiet_for:.0f}s) — something wrote in "
        f"it during the first sweep, so the reap below would prove the clock, not the board"
    )

    wf.review_task(reviewing["id"], verdict="needs_work", report="repro'd; fix misses the cause")

    res = gc_workspaces(cwd=repo, workflow=wf)

    assert [r["task_id"] for r in res["released"]] == [reviewing["id"]]
    assert not live.exists()


def test_gc_never_reaps_unpushed_work_and_reports_it(repo, tracker):
    """The orphan of a crashed agent that got as far as committing: dead on the board, but
    its commits are the whole reason we keep it. GC must REPORT, not destroy."""
    api, wf = tracker
    path = Path(ensure_workspace(42, cwd=repo)["path"])
    (path / "feature.txt").write_text("real work\n")
    _git(path, "add", "feature.txt")
    _git(path, "commit", "-m", "crashed mid-task")
    _quiesce(path)                                    # after the commit: it rewrites the index
    res = gc_workspaces(cwd=repo, workflow=wf)
    assert res["released"] == []
    assert [k["task_id"] for k in res["kept"]] == [42]
    assert "commit" in res["kept"][0]["reason"]
    assert path.exists()


def test_gc_ignores_directories_that_are_not_task_worktrees(repo, tracker):
    api, wf = tracker
    stray = repo.parent / "unrelated"
    stray.mkdir()
    _git(repo, "worktree", "add", str(stray), "-b", "unrelated-branch")
    res = gc_workspaces(cwd=repo, workflow=wf)
    assert res["released"] == [] and res["kept"] == []
    assert stray.exists()


def test_gc_from_inside_a_linked_worktree_still_reaps(repo, tracker):
    """Correction A: `repo_root(cwd)` (via `git rev-parse --show-toplevel`) returns the
    LINKED worktree's own toplevel when invoked from inside one, not the main repo's — the
    normal case once SKILL.md has per-task agents working inside their own tree. If
    gc_workspaces derived `worktree_root` from that unresolved root, every entry would fail
    the "is this one of ours" parent check and --gc would silently reap nothing while still
    reporting success. Run the sweep with cwd INSIDE a live tree and prove a DIFFERENT,
    dead-on-the-board tree still gets reaped."""
    api, wf = tracker
    task = api.add_task("live work", "Queue")
    wf.claim(task["id"])
    live_path = Path(ensure_workspace(task["id"], cwd=repo)["path"])
    dead_path = Path(ensure_workspace(42, cwd=repo)["path"])      # nothing on the board -> dead
    _quiesce(dead_path)

    res = gc_workspaces(cwd=live_path, workflow=wf)               # invoked FROM the live tree

    assert [r["task_id"] for r in res["released"]] == [42]
    assert not dead_path.exists()
    assert live_path.exists()                                     # the live tree survives too


# --- Task 4 review, round 1: Criticals ---

def test_release_from_inside_its_own_tree_succeeds_and_leaves_no_dangling_branch(repo):
    """Critical 1 repro: an agent's own 'I'm done, release me' call runs with cwd INSIDE the
    tree being released — SKILL.md's normal shape, not a corner case. Before the fix this
    raised a bare FileNotFoundError: `git worktree remove` SUCCEEDS even when its own
    subprocess cwd is the directory being removed (verified against real git), but the very
    next call, `git branch -D ... cwd=root`, needs `root` to still EXIST — and `root` was the
    just-deleted tree. The tree vanished (the real work had actually completed) while the CLI
    reported exit 1 and the branch leaked forever."""
    path = Path(ensure_workspace(42, cwd=repo)["path"])
    res = release_workspace(42, cwd=path)                # cwd IS the tree being released
    assert res["released"] is True
    assert not path.exists()
    assert "task/42" not in _git(repo, "branch", "--list", "task/42")


def test_gc_from_inside_a_dead_tree_completes_the_whole_sweep(repo, tracker):
    """Critical 2 repro: --gc invoked from inside a worktree whose OWN task has also gone
    dead — an agent calls advance(to='review') and then runs its next --gc tick before it
    gets around to releasing itself, or just never does. Must not remove the tree gc is
    itself standing in (that is the process's shell cwd disappearing underneath it, not
    merely 'a red test'), and must not abort the sweep before reaping the OTHER dead tree.

    VMCP-71: the self tree is left YOUNG on purpose, so this test also pins the guard ORDER — the
    self-guard must run BEFORE the grace window. Flip them and a dead-and-young self tree never
    reaches the self-guard at all — since VMCP-300 it lands in `deferred` instead of `kept`, so
    `kept` comes back empty and this goes red either way. That order is the deliberate
    one: this guard KNOWS a process is standing in the tree, the window only suspects it, so the
    report a human can act on must win."""
    api, wf = tracker
    self_path = Path(ensure_workspace(42, cwd=repo)["path"])     # dead, and cwd is INSIDE it
    other_path = Path(ensure_workspace(43, cwd=repo)["path"])    # also dead, different tree
    _quiesce(other_path)

    res = gc_workspaces(cwd=self_path, workflow=wf)

    assert [r["task_id"] for r in res["released"]] == [43]
    assert not other_path.exists()
    assert [k["task_id"] for k in res["kept"]] == [42]
    assert "invoked from inside" in res["kept"][0]["reason"]
    assert self_path.exists()


def test_gc_from_inside_a_live_self_tree_reports_nothing(repo, tracker):
    """Round 2, Minor 1: --gc runs on EVERY tick from inside the agent's own tree — that is
    the mainline, not a corner case — so a healthy self-tree must not show up in `kept` every
    single sweep (a signal that is never empty is a signal nobody reads). The alive check now
    runs BEFORE the self-guard, so a LIVE self-tree is just another live tree: no entry in
    EITHER list. (test_gc_from_inside_a_dead_tree_completes_the_whole_sweep above is the
    complementary case — a DEAD self-tree must still be refused and reported.)"""
    api, wf = tracker
    task = api.add_task("live work", "Queue")
    wf.claim(task["id"])
    self_path = Path(ensure_workspace(task["id"], cwd=repo)["path"])   # alive, cwd is INSIDE it

    res = gc_workspaces(cwd=self_path, workflow=wf)

    assert res["released"] == []
    assert res["kept"] == []
    assert self_path.exists()


# --- Task 4 review, round 1: Importants ---

def test_gc_isolates_a_release_failure_and_keeps_sweeping_the_rest(repo, tracker):
    """Important 3: one bad tree must not abort the whole sweep and discard every verdict
    already decided for the OTHERS — reported like any other refusal, as CODE_RELEASE_ERROR.

    THE INJECTOR CHANGED IN VMCP-142, and the reason is the finding itself. This test used to
    lock the tree with `git worktree lock`, because git refusing to remove a locked tree is a
    real, non-contrived WorkspaceError. That state is no longer one: `_release_locked` now
    answers a lock with its own coded verdict, so the lock reaches `git worktree remove` never
    and this test would have pinned a branch nothing can reach. A read-only worktree DIRECTORY
    is the replacement and keeps the property that mattered — real git, real failure, no mock
    standing in for an untested branch: `git status` inside it still succeeds (so the dirty
    guard passes) and `git worktree remove` dies on `failed to delete …: Permission denied`.
    """
    if os.geteuid() == 0:
        pytest.skip("root ignores the directory mode this test uses to make removal fail")
    api, wf = tracker
    doomed_path = Path(ensure_workspace(42, cwd=repo)["path"])   # dead, clean, pushed
    other_path = Path(ensure_workspace(43, cwd=repo)["path"])    # also dead
    _quiesce(doomed_path)
    _quiesce(other_path)
    doomed_path.chmod(0o500)                                     # git cannot delete its contents
    try:
        res = gc_workspaces(cwd=repo, workflow=wf)
    finally:
        doomed_path.chmod(0o700)                                 # or tmp_path cleanup inherits it

    assert [r["task_id"] for r in res["released"]] == [43]
    assert not other_path.exists()
    assert [(k["task_id"], k["code"]) for k in res["kept"]] == [
        (42, workspace_cmd.CODE_RELEASE_ERROR)
    ]
    assert "WorkspaceError" in res["kept"][0]["reason"]
    assert doomed_path.exists()


def test_gc_reads_liveness_under_the_lock(repo, tracker):
    """Important 5: the liveness READ must happen INSIDE _repo_lock, not before it, or a task
    claimed (and its tree created — that call takes the SAME lock) between the read and the
    reap races the sweep: the fresh tree is clean and pushed, every guard passes, and it is
    destroyed under a just-dispatched agent. Proven the other way round: a probing Workflow
    tries a NON-BLOCKING second flock on gc's own lock file from inside liveness_board() — if
    the sweep already holds the lock at that point, the probe must fail with
    BlockingIOError (flock is per-open-file-description: even the SAME process contends with
    itself on a second, separately-opened fd)."""
    api, wf = tracker
    common = Path(_git(repo, "rev-parse", "--git-common-dir"))
    if not common.is_absolute():
        common = (repo / common).resolve()
    lock_path = common / "vikunja-mcp-worktree.lock"

    class ProbingWorkflow:
        def liveness_board(self):
            with open(lock_path, "w") as fh:
                with pytest.raises(BlockingIOError):
                    fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return wf.liveness_board()

        def active_task_ids(self, board=None):
            return wf.active_task_ids(board=board)

        def review_task_ids(self, board=None):
            return wf.review_task_ids(board=board)

        def parked_task_ids(self, board=None):
            return wf.parked_task_ids(board=board)

    gc_workspaces(cwd=repo, workflow=ProbingWorkflow())


def test_run_workspace_gc_dispatches_to_gc_workspaces(monkeypatch, capsys, tmp_path):
    """Important 6: Task 3 established run_workspace's dispatch as a TESTED contract; --gc
    must not be the one branch that only ever ran by hand.

    Round 2 hygiene: chdir into tmp_path even though gc_workspaces is stubbed here — the house
    negative-pin rule means someone WILL delete that stub one day to prove it bites, and at
    that moment "safe because gc_workspaces never really runs" stops being true. The isolation
    must be structural (an inert cwd), not incidental (a mock that happens to intercept it)."""
    monkeypatch.chdir(tmp_path)
    empty = {"released": [], "kept": [], "expected": []}     # VMCP-68: the real three-list shape
    monkeypatch.setattr("vikunja_mcp.workspace_cmd.gc_workspaces", lambda: empty)
    code = run_workspace(["--gc"])
    assert code == 0
    assert json.loads(capsys.readouterr().out.strip()) == empty


def test_run_workspace_gc_combined_with_a_task_id_is_refused(monkeypatch, capsys, tmp_path):
    """Important 6: argparse alone lets `42 --gc` through and --gc silently wins, ignoring
    the task id the caller plainly meant to act on — that must be an explicit error."""
    monkeypatch.chdir(tmp_path)                    # see the hygiene note above
    calls = []
    monkeypatch.setattr("vikunja_mcp.workspace_cmd.gc_workspaces", lambda: calls.append(1))
    code = run_workspace(["42", "--gc"])
    assert code == 1
    assert not calls
    err = json.loads(capsys.readouterr().out.strip())
    assert "cannot be combined" in err["error"]


def test_run_workspace_gc_combined_with_release_is_refused(monkeypatch, capsys, tmp_path):
    monkeypatch.chdir(tmp_path)                    # see the hygiene note above
    calls = []
    monkeypatch.setattr("vikunja_mcp.workspace_cmd.gc_workspaces", lambda: calls.append(1))
    code = run_workspace(["--release", "9", "--gc"])
    assert code == 1
    assert not calls
    err = json.loads(capsys.readouterr().out.strip())
    assert "cannot be combined" in err["error"]


def test_build_workflow_resolves_config_from_the_given_root(repo, monkeypatch):
    """Important 6 / Minor: gc_workspaces's only production path (workflow=None) must resolve
    config FROM the main worktree it was given, not the process's ambient cwd —
    `.vikunja-mcp.env` (the token) lives beside `.vikunja-mcp.toml` in the repo, found by
    config.py's own walk-up from `cwd`; a linked worktree has neither file."""
    from vikunja_mcp import config as config_mod
    from vikunja_mcp.workspace_cmd import _build_workflow

    seen = {}

    def fake_load_config(cwd=None, environ=None):
        seen["cwd"] = cwd
        return config_mod.Config(url="http://example.invalid", token="t", project_id=7)

    monkeypatch.setattr(config_mod, "load_config", fake_load_config)
    wf, _deadline = _build_workflow(repo)          # VMCP-72: (workflow, read deadline)
    assert seen["cwd"] == repo
    assert wf.project_id == 7


# --- Task 4 review, round 1: Minors ---

def test_gc_ignores_a_stray_dir_under_the_root_not_named_like_a_task(repo, tracker):
    """Minor: a directory that lives INSIDE the workspace root (passes the parent check) but
    whose name doesn't match task-<id>/review-<id> must be SKIPPED, not crash the sweep.
    (test_gc_ignores_directories_that_are_not_task_worktrees above places its stray OUTSIDE
    the root, so it never reaches `_parse_workspace_name` at all — this is the sibling case
    that actually exercises the `parsed is None` branch.)"""
    api, wf = tracker
    ensure_workspace(1, cwd=repo)                          # anything, just to create wt_root
    wt_root = worktree_root(repo)
    hotfix = wt_root / "hotfix"
    _git(repo, "worktree", "add", str(hotfix), "-b", "hotfix-branch")

    res = gc_workspaces(cwd=repo, workflow=wf)

    assert hotfix.exists()
    assert not any(k.get("path") == str(hotfix) for k in res["kept"])
    assert not any(r.get("path") == str(hotfix) for r in res["released"])


def test_gc_reaps_a_review_tree_once_the_card_leaves_review(repo, tracker):
    """Minor: the everyday review-side reap — nothing crashed, review just finished and the
    card moved on. test_gc_keeps_a_review_tree_while_the_card_is_in_review above only proves
    the KEEP side; this proves the matching REAP side actually fires."""
    api, wf = tracker
    task = api.add_task("reviewed and done", "Done")        # already past Review
    head = _git(repo, "rev-parse", "HEAD")
    path = Path(ensure_workspace(task["id"], role="review", at=head, cwd=repo)["path"])
    _quiesce(path)

    res = gc_workspaces(cwd=repo, workflow=wf)

    assert [r["task_id"] for r in res["released"]] == [task["id"]]
    assert not path.exists()


def test_gc_reaps_a_build_tree_once_its_task_reaches_review(repo, tracker):
    """Minor: the everyday build-side reap — the agent finished and advanced its OWN task to
    Review. The BUILD tree is now dead and must be reaped; it must not be kept just because
    the task still exists somewhere on the board.

    VMCP-71 added the one qualifier: reaped once the tree has gone QUIET. Same board state,
    without the `_quiesce`, is
    test_gc_skips_a_dead_tree_whose_agent_may_still_be_standing_in_it below — the two are the
    same case at two ages, and together they are the whole of the grace window."""
    api, wf = tracker
    task = api.add_task("moved to review", "Queue")
    wf.claim(task["id"])
    path = Path(ensure_workspace(task["id"], cwd=repo)["path"])
    wf.advance(task["id"], to="build", spec="approach")
    wf.advance(task["id"], to="review", worklog="done", evidence="abc1234")
    _quiesce(path)

    res = gc_workspaces(cwd=repo, workflow=wf)

    assert [r["task_id"] for r in res["released"]] == [task["id"]]
    assert not path.exists()


# --- final whole-branch review, Critical 1: a reused review tree must never be silently stale ---

def _poisoned_review_tree(repo):
    """Build the state that triggers Critical 1 and that this module deliberately preserves: a
    review tree pinned at sha1 that holds a commit made INSIDE it (reviewer's notes), which
    `--release` refuses to remove (unreachable from any ref) and `--gc` cannot reap either — so
    `review-<id>` lives on. Then the author fixes the code and pushes sha2. Returns
    (tree path, the tree's actual HEAD, sha2)."""
    review = ensure_workspace(7, role="review", at=_git(repo, "rev-parse", "HEAD"), cwd=repo)
    path = Path(review["path"])
    (path / "notes.md").write_text("nit: rename this\n")
    _git(path, "add", "notes.md")
    _git(path, "commit", "-m", "review notes")
    pinned = _git(path, "rev-parse", "HEAD")

    (repo / "README.md").write_text("v2 FIXED\n")          # the fix the round-2 reviewer wants
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "fix")
    _git(repo, "push", "origin", "main")
    return path, pinned, _git(repo, "rev-parse", "HEAD")


def test_review_reuse_reports_the_head_it_is_actually_pinned_at(repo):
    """Half one of Critical 1: the reuse payload was missing the "head" key the CREATED payload
    carries, so nothing in the response could ever reveal where the tree really sits. A caller
    that gets what it asked for must still be told, in the same shape, what it got."""
    head = _git(repo, "rev-parse", "HEAD")
    first = ensure_workspace(7, role="review", at=head, cwd=repo)
    again = ensure_workspace(7, role="review", at=head, cwd=repo)     # same sha -> plain reuse
    assert again["created"] is False
    assert again["head"] == first["head"] == head
    assert _git(Path(again["path"]), "rev-parse", "HEAD") == again["head"]   # not a stale echo


def test_review_reuse_at_a_different_sha_is_refused_not_silently_stale(repo):
    """Half two, and THE finding: round 2 of a review asked for the fix's sha, silently got a
    tree still pinned at the PRE-FIX code, and cast a verdict on it. Refuse — and prove the
    refusal did not become the new destruction path: the unreleasable in-tree commit must still
    be there afterwards, object and all."""
    path, pinned, sha2 = _poisoned_review_tree(repo)
    assert pinned != sha2

    with pytest.raises(WorkspaceError, match="pinned at"):
        ensure_workspace(7, role="review", at=sha2, cwd=repo)

    assert _git(path, "rev-parse", "HEAD") == pinned          # HEAD not re-pointed
    _git(repo, "cat-file", "-e", f"{pinned}^{{commit}}")      # the commit object still exists
    assert (path / "notes.md").exists()
    assert (path / "README.md").read_text() == "hi\n"         # still the old tree, but refused


def test_review_reuse_without_at_still_reports_head_and_does_not_refuse(repo):
    """The bound of the refusal: no --at means "wherever it is is fine" (a resume dispatch that
    doesn't restate the sha), so reuse must succeed — while still naming the head."""
    path, pinned, _sha2 = _poisoned_review_tree(repo)
    again = ensure_workspace(7, role="review", cwd=repo)
    assert again["created"] is False and again["head"] == pinned
    assert Path(again["path"]) == path


def test_build_reuse_is_unaffected_by_the_review_refusal(repo):
    """A build tree is reused by BRANCH, and --at is rejected for it at the CLI (Minor 7): the
    new review-only branch must not leak into the build path."""
    ensure_workspace(42, cwd=repo)
    again = ensure_workspace(42, cwd=repo)
    assert again["created"] is False and again["branch"] == "task/42"
    assert "head" not in again                     # review-only key, same as the created payload


# --- final whole-branch review, Important 2: no git call may block forever under the lock ---

def test_git_calls_do_not_inherit_a_blocking_stdin(repo, monkeypatch):
    """`git hash-object --stdin` genuinely READS stdin: with the DEVNULL redirect it sees EOF
    and returns the empty-blob hash instantly; inheriting a never-written pipe (what a terminal
    looks like to a subprocess) it blocks — and every git call here can be holding the repo-wide
    flock while it does. _GIT_TIMEOUT is dropped to 5s so that removing the redirect FAILS this
    test in seconds instead of hanging the suite."""
    monkeypatch.setattr(workspace_cmd, "_GIT_TIMEOUT", 5.0)
    expected = subprocess.run(["git", "hash-object", "--stdin"], cwd=repo, input="",
                              capture_output=True, text=True, check=True).stdout.strip()
    read_fd, write_fd = os.pipe()                  # nothing is ever written to it
    saved_stdin = os.dup(0)
    try:
        os.dup2(read_fd, 0)
        out = workspace_cmd._git("hash-object", "--stdin", cwd=repo)
    finally:
        os.dup2(saved_stdin, 0)
        for fd in (saved_stdin, read_fd, write_fd):
            os.close(fd)
    assert out == expected


def test_git_runs_with_terminal_prompts_disabled_and_keeps_the_callers_transport(
    repo, tmp_path, monkeypatch
):
    """An https remote with no credential helper prompts on the terminal and waits forever.
    Proven by making git launch a stand-in for ssh that dumps the environment git handed it —
    which also pins the other half: a GIT_SSH_COMMAND the CALLER set must survive untouched (an
    injected BatchMode default would override a configured `core.sshCommand` identity).

    Both variables go through `monkeypatch`, like every other env-touching test in this file.
    Raw `os.environ` assignment with `del` in a `finally` was worse than untidy here: `del`
    DESTROYS an ambient GIT_SSH_COMMAND (this suite runs on developer boxes and on CI runners
    that legitimately set it) instead of restoring it. And GIT_TERMINAL_PROMPT is now set to
    "1" BEFORE the call on purpose: with it merely unset, the assertion below passed
    spuriously on any machine exporting GIT_TERMINAL_PROMPT=0 — the child would report "0"
    whether or not `_run_git` set anything. Seeded with the OPPOSITE value, the assertion pins
    the property it names: `_run_git` OVERRIDES what the caller exported.
    """
    dump = tmp_path / "git-env.txt"
    fake_ssh = tmp_path / "fake-ssh.sh"
    fake_ssh.write_text(f'#!/bin/sh\nenv > "{dump}"\nexit 1\n')
    fake_ssh.chmod(0o755)
    monkeypatch.setenv("GIT_SSH_COMMAND", str(fake_ssh))
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "1")     # the ambient value the override must beat
    _git(repo, "remote", "set-url", "origin", "ssh://git@127.0.0.1/nowhere.git")
    with pytest.raises(WorkspaceError, match="failed"):
        workspace_cmd._git("fetch", "origin", cwd=repo)

    seen = dict(
        line.split("=", 1) for line in dump.read_text().splitlines() if "=" in line
    )
    assert seen["GIT_TERMINAL_PROMPT"] == "0"          # NOT the "1" this process exported
    assert seen["GIT_SSH_COMMAND"] == str(fake_ssh)


def test_the_fetch_under_the_lock_times_out_instead_of_wedging_it_forever(
    repo, tmp_path, monkeypatch
):
    """`git fetch origin` runs INSIDE _repo_lock, before the idempotency early-return, and is
    the one call that can hang on something off this machine — an ssh host-key question read
    straight off /dev/tty (which neither GIT_TERMINAL_PROMPT nor a DEVNULL stdin can reach), a
    black-holed TCP connection. It must END, as the WorkspaceError the CLI and gc's per-tree
    handler already report. Driven through the REAL entry point, so it also pins that the fetch
    call site takes the tight NETWORK bound and not the 600s local ceiling: a stand-in for ssh
    parks for up to 30s, so a call site on the wrong constant fails this on the elapsed time.
    The stand-in is a `_parking_script` and not a flat `sleep 30` because the SIGKILL that ends
    the fetch does not reach it — see VMCP-76 above."""
    slow_ssh = _parking_script(tmp_path, "slow-ssh.sh")
    monkeypatch.setenv("GIT_SSH_COMMAND", str(slow_ssh))
    monkeypatch.setattr(workspace_cmd, "_GIT_NET_TIMEOUT", 2.0)
    _git(repo, "remote", "set-url", "origin", "ssh://git@127.0.0.1/nowhere.git")

    started = time.monotonic()
    with pytest.raises(WorkspaceError, match="fetch origin timed out after 2s"):
        ensure_workspace(42, cwd=repo)
    assert time.monotonic() - started < 15        # not the 30s sleep, not the 600s ceiling


def test_a_local_git_call_keeps_the_generous_ceiling(repo, monkeypatch):
    """The other direction of the two-bound split: killing a `worktree add` mid-checkout is
    destructive (git registers a "locked / initializing" entry BEFORE checking out, which
    `prune` will not drop and `_find` hands back as `created: false`), so local calls must NOT
    inherit the network bound — a big checkout on a slow disk is slow, not hung.

    Asserting the two CONSTANTS proved nothing about that: `worktree add` handed
    `timeout=_GIT_NET_TIMEOUT` tomorrow keeps a constants-only test green while reintroducing
    exactly the destructive kill this bound exists to prevent. So pin it AT THE CALL SITES —
    record the limit each git call actually resolves to (the same
    `_GIT_TIMEOUT if timeout is None else timeout` the subprocess is handed) and check which
    bound each one got. The recorder DELEGATES to the real `_run_git`, so this still drives the
    real create path end to end: it observes, it does not stand in for anything.
    """
    resolved: list[tuple[str, float]] = []
    real_run_git = workspace_cmd._run_git

    # the double mirrors `_run_git`'s signature EXACTLY, `env_extra` included (VMCP-90 added it):
    # a wrapper that drops a parameter the real one grew turns every caller of the new form into a
    # TypeError, which is a loud failure but in the wrong file.
    def recording_run_git(args, cwd, timeout, env_extra=None):
        resolved.append((
            " ".join(args), workspace_cmd._GIT_TIMEOUT if timeout is None else timeout
        ))
        return real_run_git(args, cwd, timeout, env_extra)

    monkeypatch.setattr(workspace_cmd, "_run_git", recording_run_git)
    res = ensure_workspace(42, cwd=repo)          # the real create path still works end to end
    assert res["created"] is True and (Path(res["path"]) / "README.md").exists()

    ceiling, network = workspace_cmd._GIT_TIMEOUT, workspace_cmd._GIT_NET_TIMEOUT
    assert ceiling >= 600 and network < ceiling            # the split itself, still worth pinning
    adds = [limit for cmd, limit in resolved if cmd.startswith("worktree add")]
    assert adds and set(adds) == {ceiling}                 # THE call site a kill corrupts
    fetches = [limit for cmd, limit in resolved if cmd.startswith("fetch")]
    assert fetches and set(fetches) == {network}           # the one call off this machine
    # and no OTHER local call site quietly took the network bound either
    assert {limit for cmd, limit in resolved if not cmd.startswith("fetch")} == {ceiling}


# --- final whole-branch review, Important 3: the board read under the lock must be bounded ---

def test_gc_builds_a_tracker_client_that_cannot_hold_the_lock_for_minutes(repo, monkeypatch):
    """gc reads the board INSIDE the repo lock (Important 5 put it there on purpose). With
    api.py's defaults an unreachable tracker costs 30s x 4 attempts + backoff ~= 2 minutes of
    held lock per request, and every agent's `--release` queues behind it. Pin the bound where
    it is set, since no unit test can make a real tracker hang."""
    from vikunja_mcp import config as config_mod
    from vikunja_mcp.workspace_cmd import _build_workflow

    monkeypatch.setattr(config_mod, "load_config", lambda cwd=None, environ=None:
                        config_mod.Config(url="http://example.invalid", token="t", project_id=7))
    wf, _deadline = _build_workflow(repo)                 # VMCP-72: (workflow, read deadline)

    assert wf.api._MAX_RETRIES == 0                       # no backoff sleeps under the lock
    timeout = wf.api._client.timeout
    assert max(timeout.connect, timeout.read, timeout.write, timeout.pool) <= 10


def test_the_default_api_client_is_untouched_by_the_gc_bound():
    """The other direction: the short timeout is for gc ALONE. The MCP server's own client —
    which is not holding any lock and does want the transient retries — must keep the 30s
    default and its 3 retries."""
    from vikunja_mcp.api import VikunjaAPI

    api = VikunjaAPI("https://t.example", "tk")
    assert api._MAX_RETRIES == 3
    assert api._client.timeout.read == 30
    # VMCP-72: and no read budget either — the MCP server's own calls are not under any lock,
    # so a hook that abandoned them past 30s would be a new failure with nothing to gain.
    assert api._client.event_hooks["request"] == []


# --- final whole-branch review, Minor 7: silently ignored argument combinations ---

@pytest.mark.parametrize("argv, needle", [
    (["42", "--release", "9"], "already names the task"),   # acted on 9, dropped 42, exit 0
    (["--release", "9", "--at", "deadbee"], "--at is for creating"),
    (["--gc", "--role", "review"], "sweeps both roles"),
    (["--gc", "--at", "deadbee"], "sweeps both roles"),
    (["42", "--at", "deadbee"], "only to --role review"),   # --help says review-only; it wasn't
])
def test_run_workspace_refuses_silently_ignored_argument_combinations(
    argv, needle, monkeypatch, capsys, tmp_path
):
    """Same class as the `--gc` + task id combination that WAS rejected: argparse accepts all of
    these and one argument is quietly dropped. On a CLI a pump drives unattended, a dropped
    `--at` is how a reviewer ends up reading a revision nobody asked for."""
    monkeypatch.chdir(tmp_path)
    calls = []
    for name in ("gc_workspaces", "release_workspace", "ensure_workspace"):
        monkeypatch.setattr(f"vikunja_mcp.workspace_cmd.{name}",
                            lambda *a, **k: calls.append(a))
    code = run_workspace(argv)
    assert code == 1
    assert not calls                                  # refused BEFORE acting on either argument
    assert needle in json.loads(capsys.readouterr().out.strip())["error"]


def test_run_workspace_still_accepts_the_legitimate_combinations(repo, monkeypatch, capsys):
    """The guards must not overshoot: `--release <id> --role review` (the reviewer's own
    cleanup, where --role is MANDATORY) and a bare `<id> --role review --at <sha>` stay legal."""
    monkeypatch.chdir(repo)
    head = _git(repo, "rev-parse", "HEAD")
    assert run_workspace(["7", "--role", "review", "--at", head]) == 0
    capsys.readouterr()
    assert run_workspace(["--release", "7", "--role", "review"]) == 0
    assert json.loads(capsys.readouterr().out.strip())["released"] is True


def test_argparse_own_errors_still_exit_rather_than_print_json(monkeypatch, tmp_path, capsys):
    """Minor 10: argparse's own failures (`--role bogus`, `--help`) must keep ARGPARSE's
    behaviour — exit, with argparse's own status and argparse's own message — and must never be
    reshaped into this CLI's `{"error": …}` line + exit 1. What allows that is the handler being
    `except Exception`: SystemExit is a BaseException and is not caught. (The
    `except SystemExit: raise` clause that used to sit above it was dead by that same fact. Its
    REMOVAL is unobservable by construction, so no test can pin it — which is why this one pins
    the observable contract instead of claiming to pin the deletion.)

    A bare `pytest.raises(SystemExit)` was too weak to be even that: a clause that prints the
    JSON error line and THEN re-raises satisfies it, and that is precisely the failure this
    test's name warns about. So assert the whole shape — argparse's exit status (2, never this
    CLI's 1), NOTHING on stdout for a script to parse, and argparse's own diagnostic on stderr,
    the last so the test cannot be satisfied by our own code exiting 2 by hand.
    """
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as excinfo:
        run_workspace(["42", "--role", "bogus"])
    assert excinfo.value.code == 2                     # argparse's status, not this CLI's exit 1
    out, err = capsys.readouterr()
    assert out == ""                                   # no JSON line: nothing was swallowed
    assert "invalid choice" in err and "bogus" in err  # argparse's own message, not ours


# --- VMCP-66 (514): a killed `worktree add` leaves `locked initializing` — refuse, don't reuse ---

def _half_created_tree(repo, monkeypatch, task_id=42):
    """CONSTRUCT the state, do not simulate it: `git worktree add` killed mid-checkout.

    Driven through the REAL entry point with the module's own timeout, because that is the point
    of the finding — since `_GIT_TIMEOUT` landed, `ensure_workspace` can manufacture this state
    BY ITSELF, with no external killer. A `* filter=slow` smudge filter parks the checkout after
    git has already written `.git/worktrees/task-<id>/locked` = "initializing" and before it has
    written any file, and `subprocess.run(timeout=...)` SIGKILLs it there.

    Measured on git 2.50.1: the entry stays listed as `locked initializing`, `git worktree prune`
    exits 0 and keeps it, and the directory holds nothing but `.git`. Returns its path.

    ONE property to know before touching the assertions below: the half-populated directory is a
    TRANSIENT phase, not the state. `worktree add` checks out in a CHILD (`git reset --hard`), and
    SIGKILLing the parent orphans that child onto PID 1, from where it will finish the checkout
    file by file the moment its filter stops parking — while the lock marker (cleared only by the
    dead parent) stays forever. So the "only .git landed" assertion holds at construction time and
    is checked there; nothing afterwards may depend on a file being absent, and nothing may key off
    file contents to recognise the state. `test_ensure_refuses_any_locked_worktree_...` is the
    complementary case — a FULLY checked-out tree that is merely locked must be refused too, which
    is precisely phase two of this one.

    WHEN it resumes is what VMCP-76 (525) pinned down. It used to be a flat `sleep 30`, so the
    orphan resumed on its own clock, minutes into whatever ran next; now it parks on a hold file
    that `repo`'s teardown removes, so it resumes when THIS test is over and is waited out there.
    Either way nothing moves while the test body runs — the state this hands back is unchanged,
    and the `_quiesce`-LAST ordering below still means what it meant.
    """
    slow_smudge = _parking_script(repo.parent, "slow-smudge.sh", tail="cat\n")
    (repo / ".gitattributes").write_text("* filter=slow\n")
    _git(repo, "add", ".gitattributes")
    _git(repo, "commit", "-m", "slow smudge filter")
    _git(repo, "push", "origin", "main")
    _git(repo, "config", "filter.slow.smudge", str(slow_smudge))

    original = workspace_cmd._GIT_TIMEOUT
    monkeypatch.setattr(workspace_cmd, "_GIT_TIMEOUT", 2.0)
    with pytest.raises(WorkspaceError, match="worktree add .* timed out"):
        ensure_workspace(task_id, cwd=repo)
    # put both back: everything AFTER this point must run at full speed and un-smudged, so the
    # tests below exercise the guards and not a second timeout.
    monkeypatch.setattr(workspace_cmd, "_GIT_TIMEOUT", original)
    _git(repo, "config", "filter.slow.smudge", "cat")

    path = worktree_root(repo) / f"task-{task_id}"
    # the state itself, asserted where it is built so every test below inherits the guarantee
    assert path.is_dir() and not (path / "README.md").exists()   # partial: only .git landed
    assert "D" in _git(path, "status", "--porcelain")             # index full of staged deletions
    _git(repo, "worktree", "prune")                               # exits 0 and does NOT drop it
    assert "locked initializing" in _git(repo, "worktree", "list", "--porcelain")
    return path


def test_list_worktrees_surfaces_the_lock_it_used_to_drop(repo, monkeypatch):
    """The one line the card called the fix: the porcelain's `locked` key was parsed and thrown
    away, so no caller could tell a usable tree from a half-created one."""
    path = _half_created_tree(repo, monkeypatch)
    entries = {wt["path"]: wt for wt in list_worktrees(repo)}

    assert entries[path]["locked"] is True
    assert entries[path]["lock_reason"] == "initializing"
    assert entries[repo]["locked"] is False and entries[repo]["lock_reason"] is None


def test_list_worktrees_reports_a_reasonless_lock_as_locked_with_no_reason(repo):
    """The other porcelain shape: `git worktree lock` with no reason emits a BARE `locked` line,
    so the reason must come back None while `locked` still says True. Gate on the bool."""
    path = Path(ensure_workspace(42, cwd=repo)["path"])
    _git(repo, "worktree", "lock", str(path))
    wt = {w["path"]: w for w in list_worktrees(repo)}[path]
    assert wt["locked"] is True and wt["lock_reason"] is None


def test_ensure_refuses_a_half_created_worktree_instead_of_reusing_it(repo, monkeypatch):
    """THE finding. `_find` returns the entry and the idempotency early-return handed it back as
    `created: false` — dispatching an agent into a directory whose tracked files are missing and
    whose index is all staged deletions. Refuse; and prove the refusal did not become a new
    destruction path (the partial tree is the only trace of what killed the add)."""
    path = _half_created_tree(repo, monkeypatch)

    with pytest.raises(WorkspaceError, match="HALF-CREATED"):
        ensure_workspace(42, cwd=repo)
    # the recovery a human actually needs, in the message itself
    with pytest.raises(WorkspaceError, match=r"worktree unlock .*&& git worktree remove -f -f"):
        ensure_workspace(42, cwd=repo)

    assert path.is_dir()                                          # NOT silently removed
    assert "locked initializing" in _git(repo, "worktree", "list", "--porcelain")


def test_ensure_refuses_any_locked_worktree_not_only_the_initializing_marker(repo):
    """The BREADTH of the guard, pinned on its own: it gates on the `locked` BOOL, never on git's
    marker text. A tree we cannot vouch for must not be handed to an agent even when the lock says
    something else entirely — and a locked tree is one git will not let `--release`/`--gc` remove,
    so working in it would leave a tree nothing can reap. Narrow the guard to
    `lock_reason == "initializing"` and this test goes red."""
    path = Path(ensure_workspace(42, cwd=repo)["path"])
    _git(repo, "worktree", "lock", "--reason", "human is inspecting this", str(path))

    with pytest.raises(WorkspaceError, match="LOCKED worktree .human is inspecting this."):
        ensure_workspace(42, cwd=repo)
    assert path.is_dir()


def test_release_names_a_half_created_tree_instead_of_calling_it_dirty(repo, monkeypatch):
    """The reported symptom: release/gc keep it forever "(dirty)" and report that every tick. The
    OUTCOME was already right (keep, never destroy) — the DIAGNOSIS was not: `git status` inside
    the tree reports the staged deletion of every missing file, so a human was sent looking for
    uncommitted work that does not exist."""
    path = _half_created_tree(repo, monkeypatch)

    res = release_workspace(42, cwd=repo)

    assert res["released"] is False
    assert "half-created" in res["reason"] and "killed `worktree add`" in res["reason"]
    assert "dirty" not in res["reason"]
    assert "remove -f -f" in res["reason"]                        # the human's actual next step
    assert path.is_dir()


def test_gc_reports_a_half_created_tree_and_keeps_sweeping(repo, tracker, monkeypatch):
    """End to end through the unattended path, which is where this state is actually met: --gc
    runs every tick, so the half-created tree must produce ONE actionable `kept` line and must not
    cost the sweep its other verdicts."""
    api, wf = tracker
    half = _half_created_tree(repo, monkeypatch)
    other = Path(ensure_workspace(43, cwd=repo)["path"])          # dead, clean, pushed
    _quiesce(half)          # the checkout is PARKED, not finished — quiesce LAST, then sweep
    _quiesce(other)

    res = gc_workspaces(cwd=repo, workflow=wf)

    assert [r["task_id"] for r in res["released"]] == [43]
    assert not other.exists()
    assert [k["task_id"] for k in res["kept"]] == [42]
    assert "half-created" in res["kept"][0]["reason"]
    assert half.is_dir()


# --- VMCP-76 (525): nothing this file parks may outlive the test that parked it ---

def test_a_parked_stand_in_does_not_outlive_the_test_that_parked_it(
    repo, monkeypatch, tmp_path, request
):
    """The leak itself, pinned by RUNNING the teardown against the real processes it must reap.

    FOUR separate things have to hold, and each is asserted against a runtime fact rather than
    against the source:

    1. `repo` pulls the reaper into the fixture closure — `request.fixturenames` is pytest's own
       resolved list, so dropping the dependency from `repo` fails here and not silently in
       whichever other test happens to park something next.
    2. The state really did leave processes running. Without this the test would pass just as
       happily against a `_parking_script` that quietly exits at once, i.e. it would stop pinning
       anything at all.
    3. What is waited on includes the orphaned CHECKOUT and not just the stand-in git spawned.
       This is the one the other three cannot cover: drop the parent derivation and every stand-in
       still gets reaped, but the reaper is free to return during the window between the last one
       exiting and git finishing — a race, so a test that only counted processes afterwards would
       pass most of the time.
    4. Driving `_reaping_parked_children` — the generator `repo` yields from, not a copy of it —
       runs the exact teardown pytest runs, and after it nothing is alive. The final check is the
       UNION of the set from step 2 and a fresh read, because neither alone covers it: releasing
       the hold lets the orphaned checkout resume and spawn one more stand-in that only a fresh
       read knows about, while the checkout itself is only visible in the earlier set (a parent is
       derived from a LIVE child, and by then its child has exited).

    Why the teardown and not a `finally` at the end of `_half_created_tree`: a test that FAILS
    mid-body never reaches the helper's tail, and a leak that depends on the assertions passing is
    the leak. Verified by forcing a failure — an `assert False` planted right after the helper in
    `test_ensure_refuses_a_half_created_worktree_instead_of_reusing_it`: the run went red and `ps`
    immediately after showed nothing of it left, neither stand-in nor checkout.

    MUTATION-CHECKED with a non-mutated CONTROL round first and another last. `__pycache__` was
    cleared between rounds, and every round selected exactly two tests — this one plus
    `test_the_reaper_never_waits_on_the_test_runner_itself`, which stays green in all of them
    except its own. Control 0 failed (2 passed); drop `_reap_parked_children(tmp_path)` from
    `_reaping_parked_children` -> 1 failed here, on step 4; drop `_parked_children_reaped` from
    `repo`'s parameters -> 1 failed here, on step 1; make `_reap_parked_children` skip the
    `unlink` -> 1 failed here on the 30s deadline AND 1 error, because the fixture's own teardown
    then hits the same deadline a second time (63s for that round, so it is not the `1 failed`
    shape the others are); make `_parked_pids` return only what the scripts recorded, deriving no
    live parent -> 1 failed here, on step 3; drop `_live_parent`'s own-pid guard -> 1 failed, in
    the OTHER test and not this one; closing control 0 failed.
    """
    assert "_parked_children_reaped" in request.fixturenames, (
        "`repo` stopped requesting the reaper, so nothing reaps the stand-ins of a test that "
        "never mentions it — which is every test in this file"
    )

    _half_created_tree(repo, monkeypatch)

    parked = {pid: _process_command(pid) for pid in _parked_pids(tmp_path)}
    assert parked, "the stand-in recorded no pid, so the reaper has nothing to wait on"
    assert [pid for pid in parked if _pid_alive(pid)], (
        "nothing was left running at all — this test cannot show a reap that had no work to do"
    )
    assert any("reset --hard" in command for command in parked.values()), (
        f"the watched set is {parked} — it does not include the orphaned CHECKOUT, only the "
        f"stand-in that git spawned. Waiting on the checkout is what keeps the reaper from "
        f"returning in the window between the last stand-in exiting and git finishing"
    )

    teardown = _reaping_parked_children(tmp_path)     # what `repo` runs after a test, pass OR fail
    next(teardown)
    with pytest.raises(StopIteration):
        next(teardown)

    assert not [pid for pid in set(parked) | _parked_pids(tmp_path) if _pid_alive(pid)]


def test_the_reaper_never_waits_on_the_test_runner_itself(tmp_path):
    """`_live_parent`'s own-pid guard, which the test above cannot reach: both stand-ins there are
    spawned BY GIT, so the parent it derives is a git process every time.

    A stand-in launched straight from a test body has pytest for a parent, and without the guard
    the reaper waits for the test runner to exit — i.e. burns the full 30s deadline and then fails
    naming pytest's own pid. Measured as exactly that before the guard existed (30.0s, two pids
    reported as outliving the test, one of them the runner). The child here is killed through the
    `Popen` handle we own, never by pid, and reaped in a `finally`.

    MUTATION-CHECKED in the same series as the test above, same two-test selection: control
    0 failed; drop `int(ppid) == os.getpid()` from `_live_parent` -> 1 failed, and here rather
    than in the other test, which stays green because git is what spawns its stand-ins.
    """
    proc = subprocess.Popen(["sleep", "30"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        assert _ps_field(proc.pid, "ppid").strip() == str(os.getpid()), (
            "this test is not exercising what it claims — the child's parent is not this process"
        )
        assert _live_parent(proc.pid) == set()
    finally:
        proc.kill()
        proc.wait()


# --- VMCP-110 (580): the two refusal CHANNELS are different shapes, and both ends need a net ---

def test_the_two_refusal_channels_are_not_interchangeable(repo, monkeypatch, capsys):
    """`workspace` refuses in two deliberately DIFFERENT shapes, and this pins both ends at once.

    CREATE refuses by RAISING; `run_workspace`'s catch-all renders that as one `{"error": …}` line
    and exit 1 — with NO `code` key. `--release`/`--gc` refuse by RETURNING: exit 0,
    `released: false`, and a machine-readable `code` beside the prose `reason`. Several documents
    had
    copied that second half out as the universal "every refusal carries a machine-readable `code`",
    which is simply false of the create half; 580 weighed making it uniform and re-ratified the
    split instead (see the CODE_* header in workspace_cmd.py for why a create-side code would have
    no consumer and could only ever be present-SOMETIMES). A re-ratified split needs a net in BOTH
    directions, so ONE state is driven through BOTH entry points here — same tree, same tick.

    "Several" carries no digit ON PURPOSE, and dropping the digit is the repair rather than a
    hedge. This sentence opened with "Three", the count 580 scoped, and VMCP-122 (597) then found a
    FOURTH — inside a SUPERSEDED marker in the drain design record, contextually true and therefore
    invisible to sweeps looking for the obvious shape. Bumping the digit is what VMCP-145 (634)
    warned against and is why this repair does not: two passes before 597 had already ruled that
    there was no further copy — 551's own round 3 about that same design record, then 584 about
    the spec doc — so the total is the part of this sentence with a track record of being wrong,
    and a docstring narrating history has no reader who ACTS on it. The running tally lives in the
    drain design RECORD's banner, which is maintained per sweep and ends "Assume a fifth.";
    attribution survives where a total does not.

    The create half is asserted as a WHOLE KEY SET on purpose. The existing create test
    (`test_run_workspace_error_is_one_json_line_exit_1`) only checks `"WorkspaceError" in
    err["error"]`, with no whole-dict equality anywhere — so a `code` key appearing beside it would
    leave every existing test in this file green, which is exactly how someone could quietly make
    the OLD universal claim true and nobody would learn of it. `"code" not in payload` would not do
    either: it names the one key we happen to fear today, and the release channel is pinned by whole
    dict (`test_run_workspace_release_of_missing_tree_is_exit_0`) for the same reason.

    MUTATION-CHECKED (`__pycache__` cleared between rounds, each round confirmed to select exactly
    1 test, workspace_cmd.py restored from a COPY — never `git checkout --`, since this card's edits
    are uncommitted and siblings are live in neighbouring worktrees): control PASS; give
    `run_workspace`'s catch-all a `"code"` beside its `"error"` (the plausible "tidy-up" that makes
    the OLD universal claim true) -> FAIL on the create key set; delete `"code": CODE_HALF_CREATED`
    from `_release_locked`'s half-created refusal -> FAIL on the release half.

    And the size of the gap was measured, not assumed: under that first mutation the ENTIRE
    pre-existing suite — this file's own create test included — stays GREEN. Someone
    could have made the false universal true and no test in this repo would have said a word.
    """
    path = _half_created_tree(repo, monkeypatch)          # ONE state both channels can see
    monkeypatch.chdir(repo)

    assert run_workspace(["42"]) == 1, "a create refusal must be a CLI failure — exit 1"
    created = json.loads(capsys.readouterr().out.strip())
    assert set(created) == {"error"}, (
        f"the CREATE channel grew keys {sorted(set(created) - {'error'})}. If a `code` was added "
        f"here on purpose, that is the split changing: update the CODE_* header in "
        f"workspace_cmd.py and CLAUDE.md's workspace bullet with it, and say what consumer grades "
        f"it — do not just widen this assertion"
    )
    assert "WorkspaceError" in created["error"] and "HALF-CREATED" in created["error"]

    assert run_workspace(["--release", "42"]) == 0, (
        "a --release refusal is a NEGATIVE VERDICT, not a CLI failure: the command RAN"
    )
    released = json.loads(capsys.readouterr().out.strip())
    assert released["released"] is False
    # .get, not [] — an ABSENT code is the likelier regression of the two, and a bare KeyError
    # would swallow the message that says what to do about it
    assert released.get("code") == workspace_cmd.CODE_HALF_CREATED, (
        f"the --release channel came back with code {released.get('code')!r} for a state it "
        f"refuses as half-created. `--gc`'s _keep_is_expected grades on this key: an absent or "
        f"unknown code lands in `kept`, i.e. it tells a human to go and look at a tree the tool "
        f"already understands"
    )
    assert set(released) == {"released", "task_id", "role", "path", "code", "reason"}, (
        f"the RELEASE channel's key set moved to {sorted(released)}; SKILL.md tells agents to "
        f"branch on this JSON line, so a key appearing or vanishing has to fail somewhere"
    )
    assert path.is_dir()                                  # neither channel destroyed the evidence


def test_no_create_path_refusal_carries_a_code(repo, monkeypatch, capsys):
    """The BREADTH of the create half, swept cheaply. The claim above was wrong in ONE direction
    only, so pinning a single create refusal would leave every OTHER one free to grow a `code` and
    re-open the drift — and "measured over every one of them" is what CLAUDE.md now says out loud.

    `_half_created_tree` is deliberately not repeated here: it costs a real ~2 s `worktree add`
    timeout and the test above already drives that state through both channels. Everything below
    reuses a fixture this file already builds for another reason, or costs nothing at all.

    MUTATION-CHECKED alongside the test above: give `run_workspace`'s catch-all a `"code"` -> FAIL,
    naming the first refusal that grew one ("the detached build tree refusal came back as
    ['code', 'error']").
    """
    monkeypatch.chdir(repo)
    _interrupted_rebase_build_tree(repo)                       # 42: detached BUILD tree (VMCP-86)
    _path, _pinned, sha2 = _poisoned_review_tree(repo)         # 7: review tree pinned at sha1
    squatter = worktree_root(repo) / "task-99"                 # 99: occupied, not a worktree
    squatter.mkdir(parents=True, exist_ok=True)
    (squatter / "precious.txt").write_text("do not clobber\n")

    refusals = {
        "detached build tree": ["42"],
        "review tree pinned at another sha": ["7", "--role", "review", "--at", sha2],
        "occupied path": ["99"],
        "task id beside --release": ["42", "--release", "9"],
        "--at without --role review": ["42", "--at", sha2],
    }
    for what, argv in refusals.items():
        assert run_workspace(argv) == 1, f"the {what} refusal stopped being exit 1"
        payload = json.loads(capsys.readouterr().out.strip())  # readouterr DRAINS: once per call
        assert set(payload) == {"error"}, (
            f"the {what} refusal came back as {sorted(payload)} — the create channel is "
            f"`{{\"error\"}}` and exit 1, with the exit code as the whole machine-readable "
            f"verdict. See test_the_two_refusal_channels_are_not_interchangeable"
        )


# --- final whole-branch review, Minor 9: a broken config must surface, not relocate trees ---

def test_a_malformed_repo_toml_is_not_swallowed_into_the_default_root(repo):
    """`except Exception` around load_config treated "this toml is broken" exactly like "there
    is no tracker config here" — and silently put the tree in the default sibling directory,
    where a `worktree_root` the human meant to configure would never be looked for again.

    Pinned by TYPE, not by a message substring: `pytest.raises(Exception, match="[Ee]xpected")`
    accepts ANY exception whose text happens to contain "expected" — an unrelated AssertionError
    or a git failure inside `worktree_root` would have read as this finding being fixed. And the
    value the swallow used to return is spelled out first, so the test names the outcome it
    excludes ("into the default root") instead of only "something raised".
    """
    swallowed_default = repo.parent / "work.worktrees"
    assert worktree_root(repo) == swallowed_default            # where a swallow would put it
    (repo / ".vikunja-mcp.toml").write_text("[tracker\nurl = 'oops'\n")
    with pytest.raises(tomllib.TOMLDecodeError):
        worktree_root(repo)


def test_a_repo_with_no_tracker_config_still_falls_back_silently(repo):
    """The other direction, and the reason the try/except exists at all: create and release need
    no tracker config whatsoever, so ConfigError alone must stay swallowed."""
    assert worktree_root(repo) == repo.parent / "work.worktrees"


# --- VMCP-71 (519): a grace window, so a sweep cannot pull a tree out from under its own agent ---

def _advanced_to_review(repo, api, wf):
    """The exact race state: a claimed task whose agent has just called `advance(to='review')`.
    Its build tree is now DEAD by liveness (alive = Design/Build assigned to me) and is clean and
    fully pushed, so every release guard passes — the tree IS removable, and the only thing that
    should stop the reaper is how recently the agent touched it. Returns (task_id, path)."""
    task = api.add_task("just advanced to review", "Queue")
    wf.claim(task["id"])
    path = Path(ensure_workspace(task["id"], cwd=repo)["path"])
    wf.advance(task["id"], to="build", spec="approach")
    wf.advance(task["id"], to="review", worklog="done", evidence="abc1234")
    return task["id"], path


def test_gc_skips_a_dead_tree_whose_agent_may_still_be_standing_in_it(repo, tracker):
    """THE race, mechanically closed. `--gc` runs at tick start from the MAIN checkout, so the
    self-guard cannot help, and `git push origin HEAD:main` moved the local `origin/main`, so the
    unpushed guard cannot either: before the grace window this tree was removed WITH its branch
    while its agent stood in it, on its way from `advance(to='review')` to `--release`.

    Asserted absent from BOTH lists, not merely surviving: `kept` means "a human should look", and
    a tree that is only YOUNG is not that. And swept alongside a quiet dead sibling that IS
    reaped, because the skip must be per-tree — deferring one tree may not cost the sweep its
    other verdicts. (test_gc_reaps_a_build_tree_once_its_task_reaches_review is this same tree
    once it goes quiet.)"""
    api, wf = tracker
    task_id, young = _advanced_to_review(repo, api, wf)      # touched milliseconds ago
    quiet = Path(ensure_workspace(44, cwd=repo)["path"])     # dead too, but long since idle
    _quiesce(quiet)

    res = gc_workspaces(cwd=repo, workflow=wf)

    assert [r["task_id"] for r in res["released"]] == [44] and not quiet.exists()
    assert res["kept"] == []                                     # young is NOT "look at this"
    assert young.is_dir() and (young / "README.md").exists()
    assert _git(repo, "branch", "--list", f"task/{task_id}")     # the branch survives too


def test_gc_grace_window_sees_a_commit_in_an_old_tree_through_the_index(repo, tracker):
    """The realistic shape of the race, and the reason the INDEX is one of the two markers: a
    tree the agent has worked in for an hour has a stale DIRECTORY mtime (nothing is created at
    its top level while files are merely edited), and the only fresh footprint at the moment the
    task leaves Build is the commit it left in the index.

    Constructed, not simulated: commit inside the tree and push it, so the tree stays clean and
    fully pushed (i.e. genuinely reapable — a skip is distinguishable from a guard's keep), then
    age ONLY the directory. Drop the index from `_last_activity` and this goes red."""
    api, wf = tracker
    _task_id, path = _advanced_to_review(repo, api, wf)
    (path / "feature.txt").write_text("the work\n")
    _git(path, "add", "feature.txt")
    _git(path, "commit", "-m", "the task's one commit")
    _git(path, "push", "origin", "HEAD:main")                    # local origin/main moves with it
    old = time.time() - workspace_cmd._REAP_GRACE_SECONDS - 60
    os.utime(path, (old, old))                                   # an hour-old working directory

    res = gc_workspaces(cwd=repo, workflow=wf)

    assert res["released"] == [] and res["kept"] == []
    assert path.is_dir() and (path / "feature.txt").exists()


def test_gc_grace_window_sees_top_level_churn_through_the_directory(repo, tracker):
    """The other marker, pinned on its own: an agent whose last GIT call is old but that is
    demonstrably still working — a verification run dropping an ignored artifact at the tree root
    (`.pytest_cache` and friends) bumps the directory while touching no index.

    Kept genuinely clean via the common `info/exclude`, so `git status --porcelain` stays empty
    and the tree really is reapable. Drop the worktree directory from `_last_activity` and this
    goes red — that half is also the only signal left when the index cannot be resolved at all."""
    api, wf = tracker
    _task_id, path = _advanced_to_review(repo, api, wf)
    common = Path(_git(repo, "rev-parse", "--git-common-dir"))
    if not common.is_absolute():
        common = (repo / common).resolve()
    (common / "info").mkdir(exist_ok=True)
    (common / "info" / "exclude").write_text(".pytest_cache/\n")
    tree_dir, index = _grace_markers(path)
    old = time.time() - workspace_cmd._REAP_GRACE_SECONDS - 60
    os.utime(index, (old, old))
    (path / ".pytest_cache").mkdir()                    # ignored: the tree stays CLEAN...
    assert _git(path, "status", "--porcelain") == ""
    os.utime(index, (old, old))                         # ...but that status just rewrote the index
    assert tree_dir.stat().st_mtime > index.stat().st_mtime      # the state this test is about

    res = gc_workspaces(cwd=repo, workflow=wf)

    assert res["released"] == [] and res["kept"] == []
    assert path.is_dir()


def test_gc_does_not_skip_forever_on_an_mtime_in_the_future(repo, tracker):
    """The window is bounded BELOW as well as above. A timestamp in the future — clock skew, a
    restored backup, an unpacked archive — would otherwise read as "younger than N" on every
    sweep for as long as it lasts. Before VMCP-300 that skip was also SILENT, which made this
    "the one combination that leaks a tree with nothing anywhere to notice"; it is now a
    `deferred` line instead — one that would arrive on EVERY tick and never clear, which is a
    different failure and not a smaller one, so the bound is what stops both.

    Anything outside the window falls through to the ordinary
    release guards, which still refuse to destroy work. Drop the `0 <=` and this goes red."""
    api, wf = tracker
    path = Path(ensure_workspace(42, cwd=repo)["path"])           # dead: nothing on the board
    ahead = time.time() + 86_400
    for marker in _grace_markers(path):
        os.utime(marker, (ahead, ahead))

    res = gc_workspaces(cwd=repo, workflow=wf)

    assert [r["task_id"] for r in res["released"]] == [42]
    assert not path.exists()


def test_last_activity_still_reports_a_future_mtime_when_every_marker_is_future(repo):
    """The all-future case belongs to the CALLER's `0 <=` bound, and must keep belonging to it.

    `_last_activity` drops future markers so one bad clock reading cannot suppress a good one
    (VMCP-84) — but when there is no good one left it reports the future value anyway rather than
    `None`. Both make the sweep fall through, so behaviour is identical today; the difference is
    that `None` would make the `0 <=` bound above unreachable, i.e. deletable without a single
    test going red, and the test that pins it (`..._does_not_skip_forever_...`) would then be
    pinning nothing. Keep the signal, keep the bound that reads it."""
    path = Path(ensure_workspace(42, cwd=repo)["path"])
    ahead = time.time() + 86_400
    for marker in _grace_markers(path):
        os.utime(marker, (ahead, ahead))

    assert workspace_cmd._last_activity(path) > time.time()


def test_gc_grace_window_is_not_defeated_by_a_future_mtime_on_the_sibling_marker(repo, tracker):
    """VMCP-84, the defect this fixes: the fall-through above used to be decided on the `max()`
    OVER BOTH markers, so a single future mtime MASKED a genuinely fresh one and the tree was
    reaped with its agent still standing in it — the very race VMCP-71 exists to close, reopened
    by a clock reading that says nothing about whether anyone is working here.

    Both orientations, because the two markers move for different reasons and either can be the
    skewed one: task 42 = future DIRECTORY (a restored/copied tree) with an index the agent just
    wrote; task 43 = future INDEX (skew on whatever wrote it) with a directory touched moments
    ago. Swept alongside a quiet dead sibling that IS reaped, so a fix that simply stops reaping
    cannot pass. Revert `_last_activity` to a max over ALL markers and both trees are destroyed."""
    api, wf = tracker
    skewed_dir = Path(ensure_workspace(42, cwd=repo)["path"])     # dead: nothing on the board
    skewed_index = Path(ensure_workspace(43, cwd=repo)["path"])
    quiet = Path(ensure_workspace(44, cwd=repo)["path"])
    _quiesce(quiet)
    ahead = time.time() + 86_400
    os.utime(_grace_markers(skewed_dir)[0], (ahead, ahead))       # ...index stays fresh
    os.utime(_grace_markers(skewed_index)[1], (ahead, ahead))     # ...directory stays fresh

    res = gc_workspaces(cwd=repo, workflow=wf)

    assert [r["task_id"] for r in res["released"]] == [44] and not quiet.exists()
    assert res["kept"] == []                       # young is not "a human should look at this"
    assert skewed_dir.is_dir() and skewed_index.is_dir()
# --- VMCP-68 (516): `--gc` grades its refusals, so `kept` is only what a human should look at ---
#
# Every test here `_quiesce`s its tree, and that is the ORDER these two changes compose in: VMCP-71
# skips a young dead tree before any guard runs, so it produces no refusal to grade and lands in
# NEITHER list. `expected` is for a refusal that WAS made and is routine — never for a tree gc
# declined to inspect. Skip the quiesce and these tests go red on empty lists, loudly.

def _unpushed_build_tree(repo, task_id):
    """A dead build tree every release guard rightly refuses to remove: it holds a commit that is
    not on origin/main. This is what an agent leaves behind when its push was rejected or its
    rebase went sideways — and NOTHING about the tree itself says whether that is routine or
    alarming. Only the board does, which is the whole point of the grading."""
    path = Path(ensure_workspace(task_id, cwd=repo)["path"])
    (path / "feature.txt").write_text("real work\n")
    _git(path, "add", "feature.txt")
    _git(path, "commit", "-m", "work in progress")
    _quiesce(path)                       # after the commit: it rewrites the index
    return path


def _parked(api, wf, title="waiting on a human"):
    """A card in Your Call with its assignee kept — what `call_human` leaves behind."""
    task = api.add_task(title, "Queue")
    wf.claim(task["id"])
    wf.call_human(task["id"], "the rebase conflicted — which side wins?")
    return task


def test_gc_reports_a_parked_cards_unpushed_commit_as_expected_not_as_kept(repo, tracker):
    """THE case that made `kept` never-empty: an agent hits a conflict, calls `call_human`, and its
    card sits in Your Call for HOURS. The tree is dead by liveness the moment the card leaves
    Build, and its unpushed commit is exactly what the guard must refuse to destroy — so the sweep
    reported it on every single tick, and a signal that is never empty stops being read.

    Still reported (nothing hidden) and still not removed (`released: false`) — just not in the
    list SKILL.md tells a human to read."""
    api, wf = tracker
    task = _parked(api, wf)
    path = _unpushed_build_tree(repo, task["id"])

    res = gc_workspaces(cwd=repo, workflow=wf)

    assert res["kept"] == []                                  # nothing for a human to look at
    assert [e["task_id"] for e in res["expected"]] == [task["id"]]
    assert res["expected"][0]["code"] == workspace_cmd.CODE_UNPUSHED
    assert res["expected"][0]["released"] is False             # reported, NOT removed
    assert path.exists()


def test_gc_still_shouts_about_an_unpushed_commit_when_the_card_is_not_parked(repo, tracker):
    """The mirror image, and why the grading needs the BOARD and not just the guard's identity:
    the very same refusal on a card nobody parked is work no agent is coming back for. Here the
    task was returned to Backlog (`return_task`) with its commits still in the tree."""
    api, wf = tracker
    task = api.add_task("abandoned mid-flight", "Queue")
    wf.claim(task["id"])
    path = _unpushed_build_tree(repo, task["id"])
    wf.return_task(task["id"], "the upstream service is down")     # -> Backlog, NOT parked

    res = gc_workspaces(cwd=repo, workflow=wf)

    assert res["expected"] == []
    assert [k["task_id"] for k in res["kept"]] == [task["id"]]
    assert res["kept"][0]["code"] == workspace_cmd.CODE_UNPUSHED
    assert path.exists()


def test_gc_reports_a_parked_cards_dirty_tree_as_expected_too(repo, tracker):
    """The dirty half of the same state, which SKILL.md names explicitly: `call_human` is what an
    agent calls WHEN a rebase conflicts, and a conflicted rebase leaves the tree dirty rather than
    merely unpushed. Grading only `unpushed` would have left the noisier of the two shouting."""
    api, wf = tracker
    task = _parked(api, wf)
    path = Path(ensure_workspace(task["id"], cwd=repo)["path"])
    (path / "README.md").write_text("<<<<<<< HEAD\nhalf-resolved conflict\n")
    _quiesce(path)

    res = gc_workspaces(cwd=repo, workflow=wf)

    assert res["kept"] == []
    assert [e["code"] for e in res["expected"]] == [workspace_cmd.CODE_DIRTY]
    assert path.exists()


def test_gc_reports_a_review_trees_in_tree_commit_as_expected_forever(repo, tracker):
    """The other routine state, and the permanent one: a reviewer committed notes INSIDE its
    detached tree, so the reachability guard refuses to release it and `--gc` cannot reap it —
    there is no board state that ever clears this, which is exactly why it must not sit in the
    list a human is told to read. Expected regardless of any parked card (its card is in Done
    here); SKILL.md's fix is the reviewer's rule, not a chore for the human.

    The `role` assertion is half of the round-2 pin: this refusal is routine BECAUSE it is a
    review tree, so the test that proves it must say which role it observed — its twin below
    holds the same code to the opposite verdict in a build tree."""
    api, wf = tracker
    api.add_task("reviewed and done", "Done")             # task 7's card has LEFT Review -> dead
    path, pinned, _sha2 = _poisoned_review_tree(repo)     # review-7, holds an in-tree commit
    _quiesce(path)

    res = gc_workspaces(cwd=repo, workflow=wf)

    assert res["kept"] == []
    assert [e["task_id"] for e in res["expected"]] == [7]
    assert res["expected"][0]["code"] == workspace_cmd.CODE_UNREACHABLE_HEAD
    assert res["expected"][0]["role"] == "review"
    assert _git(path, "rev-parse", "HEAD") == pinned       # and the commit is still there


def _interrupted_rebase_build_tree(repo, task_id=42):
    """CONSTRUCT the state, do not simulate it: a BUILD tree left mid-`git rebase origin/main`.

    Straight out of this project's own integration recipe — every per-task agent runs
    `git fetch origin && git rebase origin/main` before pushing, and a turn killed inside that
    (session limit, API error) leaves exactly this. `--exec false` is only the KILLER here; what
    the state IS gets asserted, not assumed: clean working tree, detached HEAD, and the replayed
    commit reachable from no ref (`task/<id>` still points at the PRE-rebase commit, which is
    also why the work itself is not at risk). Returns the tree path.
    """
    path = Path(ensure_workspace(task_id, cwd=repo)["path"])
    (path / "feature.txt").write_text("real work\n")
    _git(path, "add", "feature.txt")
    _git(path, "commit", "-m", "work in progress")
    before = _git(path, "rev-parse", "HEAD")

    (repo / "sibling.txt").write_text("a sibling landed while we worked\n")
    _git(repo, "add", "sibling.txt")
    _git(repo, "commit", "-m", "sibling")
    _git(repo, "push", "origin", "main")

    _git(path, "fetch", "origin")
    rebase = subprocess.run(["git", "rebase", "origin/main", "--exec", "false"],
                            cwd=path, capture_output=True, text=True)
    assert rebase.returncode != 0, "the rebase was meant to be INTERRUPTED, not to complete"

    head = _git(path, "rev-parse", "HEAD")
    assert _git(path, "status", "--porcelain") == "", "an interrupted rebase leaves a CLEAN tree"
    assert [w for w in list_worktrees(repo) if w["path"] == path][0]["branch"] is None
    assert head != before, "nothing was replayed — the fixture stopped before it did any work"
    assert _git(repo, "for-each-ref", "--contains", head, "--format=%(refname)") == ""
    _quiesce(path)                                        # after the rebase: it rewrites the index
    return path


def _detached_build_tree_whose_head_is_reachable(repo, task_id=43):
    """THE HOLE VMCP-86 MEASURED, constructed: an interrupted rebase whose HEAD is on the ONTO
    commit, i.e. reachable from origin/main.

    Not an exotic variant — it is where `git rebase` spends its FIRST moment, since it detaches to
    `onto` before replaying anything, so a turn killed at the start lands exactly here. Reproduced
    deterministically as the other everyday route to the same state: the first commit conflicts,
    the agent resolves it in the sibling's favour (so the resolution stages nothing, leaving the
    tree CLEAN), and the turn dies before `git rebase --continue`.

    What makes it the hole is the combination: the tree is detached, so `_release_locked`'s
    `origin/main..HEAD` guard is skipped for the branch; and HEAD is reachable, so the guard that
    replaces it passes. `task/<id>` and its unpushed commit are checked by NEITHER.
    """
    path = Path(ensure_workspace(task_id, cwd=repo)["path"])
    (path / "contested.txt").write_text("mine\n")
    _git(path, "add", "contested.txt")
    _git(path, "commit", "-m", "the task's work, never pushed")

    (repo / "contested.txt").write_text("theirs\n")
    _git(repo, "add", "contested.txt")
    _git(repo, "commit", "-m", "sibling touched the same file")
    _git(repo, "push", "origin", "main")

    _git(path, "fetch", "origin")
    rebase = subprocess.run(["git", "rebase", "origin/main"], cwd=path,
                            capture_output=True, text=True)
    assert rebase.returncode != 0, "the rebase was meant to STOP on the conflict"
    _git(path, "checkout", "--ours", "contested.txt")      # resolve in the sibling's favour…
    _git(path, "add", "contested.txt")                     # …then the turn dies, no --continue

    head = _git(path, "rev-parse", "HEAD")
    assert _git(path, "status", "--porcelain") == "", "the resolved-to-onto tree must read CLEAN"
    assert [w for w in list_worktrees(repo) if w["path"] == path][0]["branch"] is None
    assert head == _git(repo, "rev-parse", "origin/main"), "HEAD must sit on the ONTO commit"
    assert _git(repo, "log", "--oneline", f"origin/main..task/{task_id}") != "", \
        "the branch must still hold work that is NOT on origin/main — that is the point"
    _quiesce(path)
    return path


def test_ensure_refuses_a_build_tree_an_interrupted_rebase_left_detached(repo):
    """VMCP-86, THE bug: `ensure_workspace` found the directory, returned `created: false`, and the
    resume agent was dropped into a half-finished rebase on a detached HEAD while SKILL.md told it
    it was standing on `task/<id>`. Its `git push origin HEAD:main` would push the replayed commit.

    The information was never missing — `list_worktrees` reports `branch: None` — so the fix is
    that ensure ACTS on it, in the module's established shape for a state it cannot safely reason
    about: refuse loudly and name the recovery (514's `locked initializing` refusal). The recovery
    commands are asserted verbatim because they ARE the payload of the refusal; an error that only
    says "detached" leaves the agent exactly as stuck as the silent hand-back did.

    And it must be a pure refusal: nothing recovered on the agent's behalf (`--abort` would discard
    the replayed commit), so HEAD, the branch and the rebase state are all still there afterwards.
    """
    path = _interrupted_rebase_build_tree(repo)
    head_before = _git(path, "rev-parse", "HEAD")

    with pytest.raises(WorkspaceError) as excinfo:
        ensure_workspace(42, cwd=repo)

    message = str(excinfo.value)
    assert str(path) in message and "task/42" in message
    assert f"git -C {path} rebase --continue" in message
    assert f"git -C {path} rebase --abort" in message
    # nothing was decided for the agent
    assert _git(path, "rev-parse", "HEAD") == head_before
    assert [w for w in list_worktrees(repo) if w["path"] == path][0]["branch"] is None
    assert workspace_cmd._rebase_in_progress(path) is True


def test_ensure_hands_the_tree_back_once_the_agent_has_cleared_the_rebase(repo):
    """The refusal must be a POINTER, not a dead end — the whole reason it names two commands the
    agent can run. Run one of them and the ordinary resume path works again, with the branch's
    commits (the ones the rebase was replaying) intact."""
    path = _interrupted_rebase_build_tree(repo)
    with pytest.raises(WorkspaceError):
        ensure_workspace(42, cwd=repo)

    _git(path, "rebase", "--abort")                       # the agent's call, not the tool's

    again = ensure_workspace(42, cwd=repo)
    assert again["created"] is False and again["branch"] == "task/42"
    assert _git(path, "rev-parse", "--abbrev-ref", "HEAD") == "task/42"
    assert "work in progress" in _git(path, "log", "--oneline", "-1")


def test_ensure_still_hands_back_a_review_tree_which_is_detached_by_design(repo):
    """The other side of the `role == "build"` conjunct, and the regression that would matter most:
    a review tree is created with `worktree add --detach` and therefore ALWAYS has `branch: None`.
    Refuse on detachedness alone and every second `--role review` call for a task dies."""
    first = ensure_workspace(7, role="review", cwd=repo)
    second = ensure_workspace(7, role="review", cwd=repo)
    assert first["created"] is True and second["created"] is False
    assert second["branch"] is None and second["path"] == first["path"]


def test_release_refuses_a_detached_build_tree_and_says_what_it_is(repo):
    """The mirror refusal. This state used to come out as `unreachable-head` — true, but it names
    a symptom of the wrong thing (the replayed commit) and offers no recovery, so `--gc`'s `kept`
    line told a human "reachable from no ref" about a tree whose actual problem is that it is off
    its branch mid-rebase."""
    path = _interrupted_rebase_build_tree(repo)

    res = release_workspace(42, cwd=repo)

    assert res["released"] is False
    assert res["code"] == workspace_cmd.CODE_DETACHED_BUILD
    assert "MID-REBASE" in res["reason"] and f"git -C {path} rebase --abort" in res["reason"]
    assert path.exists()


def test_release_no_longer_destroys_a_detached_build_tree_whose_branch_is_unpushed(repo):
    """THE measured hole (see `_detached_build_tree_whose_head_is_reachable`), and the one case
    here where the OLD behaviour was `released: true`, not merely a bad message.

    A build tree detached with its HEAD on `onto` passed every guard: `dirty` (clean), the
    branch-history guard (skipped — `wt["branch"]` is None), the reachability guard (origin/main
    contains HEAD). So `--release` — and `--gc`, unattended, every tick — removed the directory and
    reported success while `task/43` still held a commit that was not on origin/main, and no key in
    the payload said so. The work survives on the branch, so this was never data loss; it was a
    report that said the opposite of what happened."""
    path = _detached_build_tree_whose_head_is_reachable(repo)
    unpushed_before = _git(repo, "log", "--oneline", "origin/main..task/43")

    res = release_workspace(43, cwd=repo)

    assert res["released"] is False
    assert res["code"] == workspace_cmd.CODE_DETACHED_BUILD
    assert path.exists()
    assert _git(repo, "log", "--oneline", "origin/main..task/43") == unpushed_before
    # and the message names the state, not the reachability of a commit nobody asked about
    assert "reachable from no ref" not in res["reason"]


def _detached_build_tree_without_a_rebase(repo, task_id=44):
    """A build tree off its branch with NO rebase state — the other half of the refusal, and the
    reason the guard keys on `branch is None` rather than on the rebase probe. A rebase is the
    commonest way a tree ends up here, not the only one (`git bisect`, a hand `checkout --detach`,
    a rebase somebody half-cleared), and all of them break the same promise: nothing committed
    here reaches `task/<id>`."""
    path = Path(ensure_workspace(task_id, cwd=repo)["path"])
    (path / "wip.txt").write_text("real work\n")
    _git(path, "add", "wip.txt")
    _git(path, "commit", "-m", "work in progress")
    _git(path, "checkout", "--detach", "HEAD")
    assert [w for w in list_worktrees(repo) if w["path"] == path][0]["branch"] is None
    assert workspace_cmd._rebase_in_progress(path) is False
    _quiesce(path)
    return path


def test_release_refuses_a_detached_build_tree_with_no_rebase_in_progress(repo):
    """Same refusal, different recovery — and the message must not claim a rebase that is not
    there. Pinned because the wording is chosen by a PROBE (`_rebase_in_progress`) while the guard
    itself keys on `branch is None`: mixing those up would either refuse the wrong trees or promise
    the reader a `rebase --continue` that exits 'no rebase in progress'."""
    path = _detached_build_tree_without_a_rebase(repo)

    res = release_workspace(44, cwd=repo)

    assert res["released"] is False
    assert res["code"] == workspace_cmd.CODE_DETACHED_BUILD
    assert "no rebase in progress" in res["reason"]
    assert "rebase --continue" not in res["reason"]
    assert f"git -C {path} checkout task/44" in res["reason"]
    assert path.exists()


def test_the_detached_build_refusal_does_not_advise_discarding_an_orphaned_head(repo):
    """The branch can be gone (a hand `git branch -D`, or #517's leaked-branch path in reverse) and
    then this detached HEAD is the ONLY name for the commits in the tree. `checkout task/<id>` —
    the recovery the ordinary case names — would then be advice that orphans them, so the message
    has to be built from the fact rather than written once and assumed.

    (Constructed WITHOUT a rebase in flight on purpose: git refuses `branch -D` for a branch a
    worktree is mid-rebase on — measured, `cannot delete branch 'task/42' used by worktree at …` —
    so the rebase variant of this state cannot be reached from the outside at all.)"""
    path = _detached_build_tree_without_a_rebase(repo, task_id=45)
    _git(repo, "branch", "-D", "task/45")

    res = release_workspace(45, cwd=repo)

    assert res["code"] == workspace_cmd.CODE_DETACHED_BUILD
    assert "does not exist any more" in res["reason"]
    assert f"git -C {path} checkout task/45" not in res["reason"]
    assert f"git -C {path} checkout -b task/45" in res["reason"]


def test_gc_shouts_about_a_build_tree_an_interrupted_rebase_left_detached(repo, tracker):
    """ROUND-2 REVIEW of VMCP-68, THE finding: this refusal used to be graded routine on the code
    alone, on a justification (a reviewer's in-tree notes, above) that is entirely about REVIEW
    trees — while a BUILD tree reaches the same detached branch of `_release_locked` after an
    interrupted rebase.

    Nothing about that is routine, and grading it `expected` filed it under "do not look" FOREVER —
    the exact shape of `half-created`, which this module correctly calls never-routine. VMCP-86
    changed WHICH code the build tree emits (`detached-build`, which names the state and its
    recovery instead of the reachability of the replayed commit) but not the verdict this test
    exists for: `kept`, never `expected`, and the tree never destroyed."""
    api, wf = tracker
    api.add_task("its card has moved on", "Done")         # task 42 is no longer in Build -> dead
    path = _interrupted_rebase_build_tree(repo)

    res = gc_workspaces(cwd=repo, workflow=wf)

    assert res["expected"] == []                          # NOT filed under "no action needed"
    assert [k["task_id"] for k in res["kept"]] == [42]
    assert res["kept"][0]["code"] == workspace_cmd.CODE_DETACHED_BUILD
    assert res["kept"][0]["role"] == "build"
    assert path.exists()                                  # and still refused, never destroyed


def test_keep_grading_of_unreachable_head_still_turns_on_the_role(repo):
    """VMCP-68's `role` conjunct, pinned DIRECTLY because no sweep can construct it any more: since
    VMCP-86 a detached build tree is refused upstream with its own code, so `unreachable-head`
    now only ever arrives from a review tree. The conjunct is kept as a backstop — the grading
    policy's rule is "fail toward shouting", and letting it decay into "expected on the code alone"
    would restore VMCP-68's round-2 bug the moment anything routes a build tree back here.

    Delete the conjunct (`return True` on the code alone) and the first assertion goes red."""
    build = {"code": workspace_cmd.CODE_UNREACHABLE_HEAD, "role": "build", "task_id": 1}
    review = {"code": workspace_cmd.CODE_UNREACHABLE_HEAD, "role": "review", "task_id": 1}
    assert workspace_cmd._keep_is_expected(build, parked=set()) is False
    assert workspace_cmd._keep_is_expected(review, parked=set()) is True


def test_a_parked_card_never_launders_a_half_created_tree_into_expected(repo, tracker, monkeypatch):
    """The boundary of "parked ⇒ routine": it applies to the two guards that protect ORDINARY
    in-progress work, never to a broken tool state. A half-created tree (git's own `locked
    initializing` from a killed `worktree add`) needs a human with two git commands whether or not
    its card happens to be parked — grade it quiet and the one refusal nobody else can resolve
    disappears from the only list anybody reads."""
    api, wf = tracker
    task = _parked(api, wf)
    half = _half_created_tree(repo, monkeypatch, task_id=task["id"])
    _quiesce(half)          # the checkout is PARKED, not finished — quiesce LAST, then sweep

    res = gc_workspaces(cwd=repo, workflow=wf)

    assert res["expected"] == []
    assert [k["code"] for k in res["kept"]] == [workspace_cmd.CODE_HALF_CREATED]


def test_a_parked_card_past_the_first_page_is_still_graded_as_parked(repo, tracker):
    """`liveness_board` must page Your Call EXHAUSTIVELY (it is in require_titles), because a
    parked id that pagination truncated away reads as NOT parked — and gc then grades a routine
    refusal as an alarm, quietly, and only on the boards busy enough to fill a page. Squeeze the
    fake's page size to 1 so the card under test sits past the first page of Your Call."""
    api, wf = tracker
    api.page_size = 1
    _parked(api, wf, title="parked earlier, fills page 1")
    task = _parked(api, wf, title="parked second, past the page")
    _unpushed_build_tree(repo, task["id"])

    res = gc_workspaces(cwd=repo, workflow=wf)

    assert res["kept"] == []
    assert [e["task_id"] for e in res["expected"]] == [task["id"]]


# --- VMCP-91 (547): the parked-card branch turns on the ROLE too ---

def test_a_parked_card_does_not_launder_a_reviewers_dirty_tree(repo, tracker):
    """VMCP-91, and it is VMCP-68's round-2 finding one axis over: `dirty`/`unpushed` were graded
    routine by the PARKED CARD alone, on a justification written entirely about the BUILD agent
    (`call_human` is what an agent calls when a rebase conflicts, so its tree holds unsaved work
    while the card waits). A REVIEW tree reaches the very same role-agnostic `dirty` guard, so a
    reviewer's stranded draft was filed under "do not look" for as long as a card it merely shares
    a task id with sat in Your Call.

    Reachable on the ordinary path, which is what this builds: the reviewer files `needs_work`
    without `--release`, the card goes back to Build, the build agent hits a conflict and calls
    `call_human`. BOTH trees in ONE sweep, at the same age, with the same code and the same board
    state — the only difference that can MOVE the verdict is the role (the fixtures also differ in
    task id and in which file is dirty: the task id DOES reach `_keep_is_expected` — it is the only
    key the grader takes by subscript rather than `.get`, `entry["task_id"] in parked` — but BOTH
    cards are parked, so it cannot change the answer, while which file is dirty never reaches the
    grader at all), so the test holds one code to opposite verdicts and cannot pass by nothing ever
    being graded routine."""
    api, wf = tracker
    head = _git(repo, "rev-parse", "HEAD")

    build_card = _parked(api, wf, title="build agent parked on a conflict")
    build_tree = Path(ensure_workspace(build_card["id"], cwd=repo)["path"])
    (build_tree / "README.md").write_text("<<<<<<< HEAD\nhalf-resolved conflict\n")

    review_card = _parked(api, wf, title="bounced back to build, then parked")
    review_tree = Path(
        ensure_workspace(review_card["id"], role="review", at=head, cwd=repo)["path"])
    (review_tree / "review-notes.md").write_text("draft verdict: needs work because...\n")

    _quiesce(build_tree)
    _quiesce(review_tree)

    res = gc_workspaces(cwd=repo, workflow=wf)

    assert sorted((e["task_id"], e["role"], e["code"]) for e in res["expected"]) == [
        (build_card["id"], "build", workspace_cmd.CODE_DIRTY)], \
        "the BUILD tree under a parked card is the state this branch exists for — it must stay " \
        "`expected`, or the fix has traded one unread signal for a noisy one"
    assert sorted((k["task_id"], k["role"], k["code"]) for k in res["kept"]) == [
        (review_card["id"], "review", workspace_cmd.CODE_DIRTY)], \
        "a reviewer's dirty tree is graded `expected` again — the parked card belongs to someone " \
        "else's unsaved work, and the reviewer's contract is a verdict as a tracker COMMENT"
    assert build_tree.exists() and review_tree.exists()    # refused, never destroyed, either way


def test_the_grading_grid_is_all_kept_outside_the_four_named_cells():
    """The completeness pin, and it exists because of this card's own history. Two branches of
    `_keep_is_expected` had the same role-shaped hole; VMCP-68's round-2 review closed one, and the
    mirror survived in the other for two more rounds of review OF THAT FUNCTION. What nobody had was
    the whole grid in one place, so "we checked the branch we were looking at" kept reading as "we
    checked it".

    Every code the module declares, both roles PLUS a role-less entry, both board states. FOUR of
    the 72 cells are routine, spread over THREE codes and coming from TWO sets (the two the parked
    build tree excuses share one); every other cell is `kept`. Counted as CELLS on purpose — this
    pin's first wording said "two rows", which is the miscount-inside-a-closed-enumeration that the
    card it belongs to was filed against. The `declared <= grid` assertion is the part that survives
    the next card: a new `CODE_*` constant fails here until somebody grades it deliberately, rather
    than inheriting a default nobody read.

    MUTATION-CHECKED, control 0 failed each time, `__pycache__` purged between rounds: drop the
    `role == "build"` conjunct -> 3 failed; invert it to "review" -> 6; drop `role == "review"`
    -> 2; drop the `parked` conjunct -> 6; add CODE_DIRTY to the review set -> 4; add CODE_LOCKED
    to the build set -> 2; declare a new ungraded `CODE_*` -> 1 (this test alone). This test is in
    every one of those counts."""
    codes = [workspace_cmd.CODE_DIRTY, workspace_cmd.CODE_UNPUSHED,
             workspace_cmd.CODE_UNREACHABLE_HEAD, workspace_cmd.CODE_DETACHED_BUILD,
             workspace_cmd.CODE_HALF_CREATED, workspace_cmd.CODE_LOCKED,
             workspace_cmd.CODE_NO_WORKTREE, workspace_cmd.CODE_SELF_TREE,
             workspace_cmd.CODE_RELEASE_ERROR, workspace_cmd.CODE_POPULATED_GITLINK,
             "a-code-nobody-declared", None]           # the fail-toward-shouting fallbacks
    declared = {v for k, v in vars(workspace_cmd).items()
                if k.startswith("CODE_") and isinstance(v, str)}
    assert declared <= set(codes), \
        f"a declared code is ungraded by this grid: {sorted(declared - set(codes))} — add it to " \
        f"the row list AND to the expected-cell set below, deliberately"

    routine = set()
    for code in codes:
        for role in ("build", "review", None):
            for parked in (True, False):
                entry = {"task_id": 7}
                if code is not None:
                    entry["code"] = code
                if role is not None:
                    entry["role"] = role
                if workspace_cmd._keep_is_expected(entry, {7} if parked else set()):
                    routine.add((code, role, parked))

    assert routine == {
        (workspace_cmd.CODE_DIRTY, "build", True),
        (workspace_cmd.CODE_UNPUSHED, "build", True),
        (workspace_cmd.CODE_UNREACHABLE_HEAD, "review", True),
        (workspace_cmd.CODE_UNREACHABLE_HEAD, "review", False),
    }, "the set of cells graded routine moved — every OTHER cell in this grid must be `kept`"


# --- VMCP-183 (708): the policy COMMENT's two closed enumerations, derived instead of restated ---

def _policy_source() -> list[str]:
    return Path(workspace_cmd.__file__).read_text().splitlines()


# A run of separators, not one: a bullet-shaped list puts TWO between members (`, * CODE_X`), and a
# single-separator pattern silently stopped at the first of them — reading four names out of six and
# redding on a purely cosmetic edit. Measured, not guessed.
_LIST_ITEM = re.compile(r"(?:\s*(?:[,;*•-]|\bor\b|\band\b))*\s*(CODE_[A-Z_]+)")


def _enumerated_codes(lines: list[str]) -> list[str]:
    """The MEMBERS of the `Neither set contains ...` list — the run of `CODE_*` names that follows
    that phrase, and nothing else in the paragraph.

    THE LIST, NOT THE PARAGRAPH, and that distinction is not a nicety: the first version of this
    helper collected every `CODE_*` token in the whole paragraph, and a mutation round proved it
    worthless. Deleting CODE_DETACHED_BUILD from the list left the paragraph's own provenance prose
    ("VMCP-86 declared CODE_DETACHED_BUILD and never added it here") holding the name, so the token
    set was unchanged and the whole `tests/unit` selection stayed green — a pin that could not see
    the exact defect its card was filed for. Scoped to the list, the same mutation is caught however
    often the name appears elsewhere.

    Reading a RUN rather than a fixed shape is what keeps re-wrapping, reordering and swapping the
    connector from redding it: lines are flattened first, and separators are optional.

    THE REGION RUNS TO THE END OF THE COMMENT BLOCK, not to the first bare `#`, and blank comment
    lines and bullet markers are absorbed rather than treated as terminators. That is the SECOND
    thing measurement changed here: with a paragraph-terminated region, re-shaping the six names
    into a bullet list (a purely cosmetic edit that leaves every claim true) put a bare `#` between
    the phrase and the names, the region became one line long, zero names parsed, and the test went
    red. It cost nothing to widen, because the run still stops at the first token that is not a
    code name — so the wider region cannot swallow anything the narrow one did not."""
    starts = [i for i, ln in enumerate(lines) if ln.startswith("# Neither set contains")]
    assert len(starts) == 1, \
        f"expected exactly one `# Neither set contains` sentence, found {len(starts)} — this pin " \
        f"cannot tell which enumeration it is meant to be checking"
    end = starts[0] + 1
    while end < len(lines) and lines[end].startswith("#"):
        end += 1
    flat = " ".join(s for ln in lines[starts[0]:end] if (s := ln.lstrip("#").strip()))

    head = "Neither set contains"
    at = flat.index(head) + len(head)
    names: list[str] = []
    while (m := _LIST_ITEM.match(flat, at)) is not None:
        names.append(m.group(1))
        at = m.end()
    assert names, f"no `CODE_*` run found after {head!r} in: {flat[:120]!r}"
    assert len(names) == len(set(names)), f"the list repeats a member: {names}"
    return names


def _grid(lines: list[str]) -> tuple[list[tuple[str, bool]], dict[str, list[str]]]:
    """The `code / build+parked / build / review+parked / review` table, parsed into its column
    keys and its rows.

    Tolerant by construction, because the point is to pin CLAIMS and not FORMATTING: columns come
    from the header rather than from a hard-coded order, cells are the trailing run of E/K tokens,
    and the label is whatever precedes them — so re-padding a column, renaming a row label with a
    space in it, or reordering the columns cannot red this test on its own."""
    heads = [i for i, ln in enumerate(lines)
             if ln.lstrip("# ").startswith("code") and "build+parked" in ln]
    assert len(heads) == 1, f"expected exactly one grid header line, found {len(heads)}"
    tokens = lines[heads[0]].lstrip("# ").split()
    assert tokens[0] == "code", tokens
    columns = [(t.split("+")[0], t.endswith("+parked")) for t in tokens[1:]]

    rows: dict[str, list[str]] = {}
    for ln in lines[heads[0] + 1:]:
        if not ln.startswith("#"):
            break
        body = ln.lstrip("#").split()
        if len(body) <= len(columns) or set(body[-len(columns):]) - {"E", "K"}:
            break
        rows[" ".join(body[:-len(columns)])] = body[-len(columns):]
    assert rows, "no E/K rows were parsed out of the grid"
    return columns, rows


def _graded(code, role: str, parked: bool, *, drop_code: bool = False) -> str:
    entry = {"task_id": 7, "role": role}
    if not drop_code:
        entry["code"] = code
    return "E" if workspace_cmd._keep_is_expected(entry, {7} if parked else set()) else "K"


def test_the_policy_comment_enumerations_are_derived_from_the_code():
    """This block holds TWO closed enumerations over the same population — the `Neither set
    contains ...` paragraph and the E/K grid — and a closed enumeration nobody asserts is a rotting
    form. Measured, not argued. The paragraph opened in VMCP-68 with four members and was COMPLETE
    then — `git show "0da22fd:src/vikunja_mcp/workspace_cmd.py"` names four, and CODE_DETACHED_BUILD
    did not yet exist (0 occurrences at that rev). VMCP-86 declared it and did not add it here,
    which is where the list first went short. VMCP-142 then inserted CODE_LOCKED at position two and
    rewrote the closing clause from "the other three" to "the LAST three", sliding the referent past
    the new member and leaving it with no bin at all. Six codes are in neither `_EXPECTED_*` set and
    the sentence named five. Both rots survived the card that made them and were found by VMCP-91
    rewriting something else nearby.

    WHAT THIS PINS THAT THE NEIGHBOURING TESTS DO NOT. `test_the_grading_grid_is_all_kept_outside_
    the_four_named_cells` pins the GRADER against a cell set written in that test; this one pins the
    COMMENT against the grader. They are different artifacts and each is invisible to the other: a
    new `CODE_*` added to that test's row list and to the grid, but not to the LIST, is green
    everywhere except here.

    NOT the shape either historical rot took, and worth saying so rather than borrowing their
    authority: when VMCP-142 landed there was no grid and no grid test (both arrived with VMCP-91,
    AFTER it — `git show "bb81c39:src/vikunja_mcp/workspace_cmd.py" | grep -c build+parked` is 0),
    so it had exactly one other place to update and missed it. The paired shape is what this region
    produces from NOW on, because keeping a new code honest today means three edits, and only one
    of them had an assert before this test.

    WHAT IT DOES NOT COVER, said plainly because this card is about false completeness — and the
    list itself was wrong twice before it was right, which is the honest reason to read it as a
    floor. (1) TWO of the block's THREE enumerations. The rationale paragraph under the list names
    all six again and sorts them into bins, and the four-bin sentence under the grid does the same
    over prose categories; neither is pinned. Measured: orphaning a member in the rationale
    paragraph, or asserting there outright that CODE_DIRTY is also in neither set, leaves the whole
    selection green. (2) Only NAMES and VERDICTS: it cannot tell whether a rationale clause actually
    accounts for the member it sits next to — the exact defect that orphaned CODE_LOCKED would still
    be green if the name appeared in the list with no reason attached anywhere. The pointer to its
    own paragraph is prose, not a pin. (3) The grid has no role-less column, so this test says
    nothing about an entry carrying no `role`; the neighbouring test owns that. (4) One narrow
    false-red edge, and it is the price of reading a RUN: prose written immediately after the last
    member and opening with a separator plus a code name ("... or CODE_SELF_TREE and CODE_DIRTY is a
    different matter") is swallowed into the list. Anything separated by other words is not — a
    contrastive mention elsewhere in the paragraph is measured GREEN.

    MUTATION-CHECKED — and the sweep earned its keep twice over, because it killed the FIRST version
    of this test (see `_enumerated_codes`) and an independent second pass then killed a false RED
    the first version also had. Selection `tests/unit` (895 collected), `__pycache__` purged and
    `PYTHONDONTWRITEBYTECODE=1` each round, `vikunja_mcp.__file__` printed each round, `--tb=no` so
    no docstring can reach the log. **control 0 failed**, and every count below is a delta on it:
      * drop CODE_DETACHED_BUILD from the LIST, leaving the name in the paragraph's prose -> 1,
        this test. This is the round that read 0 against v1.
      * drop CODE_LOCKED from the list AND from every prose mention -> 1, this test.
      * restore the exact pre-708 defective sentence verbatim -> 1, this test (0 against v1).
        THIS ONE ROUND comes from a separate replay of the second independent pass's attacks, on
        the narrower `tests/unit/test_workspace_cmd.py` selection and against ITS OWN control of
        0 failed. Named rather than blended into the list above, because a count means nothing
        except against the control that shares its selection.
      * flip the `locked`/`build+parked` grid cell K -> E -> 1, this test.
      * add CODE_LOCKED to `_EXPECTED_IN_A_PARKED_BUILD_TREE` -> 3: this test, the grid test, and
        test_a_locked_tree_reports_the_lock_even_when_it_is_also_dirty.
      * PAIRED, three coordinated edits: declare `CODE_NEWTHING`, add its all-K grid row, add it to
        the neighbouring test's row list -> **1, this test ALONE**. Its halves are NOT innocent and
        the sweep says so: the constant alone -> 2, constant plus grid row -> 2 (this test and the
        grid test both). Only the full triple isolates this one.
      * GREEN under: reordering the six names, swapping the `or` connector, re-shaping them into a
        bullet list, re-padding the grid columns, and swapping two grid columns together with their
        cells. On the narrower selection, also green under a contrastive `CODE_DIRTY` elsewhere in
        the paragraph — which v1 red-flagged and had to declare as a cost.
      * A ROUND IS ACCEPTED BY THE FAILING TEST'S NAME, NEVER BY THE COUNT, and this sweep is why.
        Two rounds came back `1 failed` that had nothing to do with this pin: a re-wrap probe that
        wrote a 121-character line (test_line_length_gate), and the second pass's "behaviour-neutral"
        edit, which moved CODE_LOCKED into the parked-build set and duly broke
        test_a_locked_tree_reports_the_lock_even_when_it_is_also_dirty. Read as numbers, both look
        like this test false-redding; read as names, one is a bug in the probe and the other is
        proof the edit was not behaviour-neutral after all."""
    lines = _policy_source()
    declared = {n: v for n, v in vars(workspace_cmd).items()
                if n.startswith("CODE_") and isinstance(v, str)}
    assert len(declared) >= 9, f"the module stopped declaring codes: {sorted(declared)}"
    expected_anywhere = (workspace_cmd._EXPECTED_IN_A_PARKED_BUILD_TREE
                         | workspace_cmd._EXPECTED_IN_A_REVIEW_TREE)

    in_neither = {n for n, v in declared.items() if v not in expected_anywhere}
    named = set(_enumerated_codes(lines))
    assert named == in_neither, (
        "the `Neither set contains ...` list no longer enumerates exactly the codes in "
        f"neither `_EXPECTED_*` set. Missing from the prose: {sorted(in_neither - named)}. "
        f"Named there but actually expected somewhere: {sorted(named - in_neither)}. A code with "
        "no bin is an invitation to grade a NEW guard by a list that already forgot one."
    )

    columns, rows = _grid(lines)
    by_value = {v: n for n, v in declared.items()}
    assert set(declared.values()) <= set(rows), (
        f"declared codes with no row in the policy grid: "
        f"{sorted(declared.values() - set(rows))} — grade it in the table deliberately"
    )
    for label, cells in rows.items():
        for (role, parked), cell in zip(columns, cells):
            if label in by_value:
                want = [_graded(label, role, parked)]
            else:                       # the `<unknown / absent>` row: BOTH fallbacks, one cell
                want = [_graded("a-code-nobody-declared", role, parked),
                        _graded(None, role, parked, drop_code=True)]
            assert set(want) == {cell}, (
                f"the grid claims `{label}` is {cell!r} at role={role} parked={parked}, but "
                f"`_keep_is_expected` answers {sorted(set(want))} — the comment is a copy that "
                f"drifted from the function it describes"
            )


# --- VMCP-69 (517): the two behaviour leftovers of the parallel-drain branch ---

def test_the_main_worktree_lookup_runs_git_once_however_often_it_is_asked(repo, monkeypatch):
    """`worktree_root` is called from `_find`, from `_release_locked`'s not-found branch and once
    per sweep, and each call used to spawn its own `git worktree list --porcelain`. Memoised on
    `_main_worktree`, because that is where the subprocess is and the answer cannot change while
    the process runs. Disable the lru_cache and this counts three listings instead of one."""
    workspace_cmd._main_worktree.cache_clear()
    seen = []
    real = workspace_cmd._run_git

    def counting(args, cwd, timeout, env_extra=None):
        seen.append(tuple(args))
        return real(args, cwd, timeout, env_extra)

    monkeypatch.setattr(workspace_cmd, "_run_git", counting)

    assert worktree_root(repo) == worktree_root(repo) == worktree_root(repo)

    assert [c for c in seen if c[:2] == ("worktree", "list")] == [
        ("worktree", "list", "--porcelain")
    ]


def test_the_env_override_is_not_frozen_by_that_memoisation(repo, monkeypatch):
    """The cache is deliberately on `_main_worktree` and NOT on `worktree_root`: the latter reads
    VIKUNJA_WORKTREE_ROOT and the repo toml, which callers (and this suite) change underneath it.
    Move the cache up a level and this goes red — the second answer would still be the first."""
    workspace_cmd._main_worktree.cache_clear()
    first = worktree_root(repo)
    monkeypatch.setenv(ENV_WORKTREE_ROOT, "elsewhere")
    assert worktree_root(repo) == (repo / "elsewhere").resolve() != first


def _fail_branch_delete(monkeypatch):
    """Make `git branch -D` fail while everything else stays real git. The window is otherwise
    unreachable on demand, and it is the whole point of the guard: the worktree is ALREADY gone
    by the time this fires."""
    real = workspace_cmd._run_git

    def selective(args, cwd, timeout, env_extra=None):
        if args[:2] == ("branch", "-D"):
            raise WorkspaceError(f"git {' '.join(args)} failed: simulated ref-store failure")
        return real(args, cwd, timeout, env_extra)

    monkeypatch.setattr(workspace_cmd, "_run_git", selective)


def test_a_branch_delete_failure_does_not_report_a_tree_that_is_already_gone(repo, monkeypatch):
    """`worktree remove` succeeded, `branch -D` did not. Reporting `released: False` there is not
    a neutral "it failed": SKILL.md teaches that field as "PROTECTION — your unsaved work is still
    in the tree", so it sent a human to a directory git had just deleted. Say what happened."""
    path = Path(ensure_workspace(42, cwd=repo)["path"])
    _fail_branch_delete(monkeypatch)

    res = release_workspace(42, cwd=repo)

    assert res["released"] is True                       # the tree really is gone...
    assert not path.exists()
    assert res["branch_deleted"] is False                # ...and the branch really is not
    assert "task/42" in res["warning"] and "branch -D" in res["warning"]
    monkeypatch.undo()
    assert "task/42" in _git(repo, "branch", "--list", "task/42")


def test_a_leaked_branch_is_recoverable_by_the_ordinary_resume_path(repo, monkeypatch):
    """Why `released: True` is honest rather than a shrug: a surviving `task/<id>` is the same
    state a hand-deleted tree leaves, and `_ensure_locked` reattaches to it instead of recreating
    it. Nothing is lost and no cleanup is required before the task can be worked again."""
    ensure_workspace(42, cwd=repo)
    _fail_branch_delete(monkeypatch)
    release_workspace(42, cwd=repo)
    monkeypatch.undo()

    again = ensure_workspace(42, cwd=repo)

    assert again["created"] is True and again["branch"] == "task/42"
    assert Path(again["path"]).is_dir()


def test_gc_files_a_branch_delete_failure_under_released_not_kept(repo, tracker, monkeypatch):
    """The sweep's side of the same bug: the per-tree `except` recorded a `kept` entry whose
    `path` no longer existed, and `kept` is the list SKILL.md tells the pump a human must read.
    Fixed at the source, so the sweep needs no special case — and 516 is free to keep rewriting
    that handler."""
    _api, wf = tracker
    path = Path(ensure_workspace(42, cwd=repo)["path"])       # dead: nothing on the board
    _quiesce(path)                                            # past VMCP-71's grace window
    _fail_branch_delete(monkeypatch)

    res = gc_workspaces(cwd=repo, workflow=wf)

    assert [r["task_id"] for r in res["released"]] == [42]
    assert res["kept"] == [] and res["expected"] == []      # not a refusal at all, so ungraded
    assert res["released"][0]["branch_deleted"] is False
    assert not path.exists()


def test_a_clean_release_says_nothing_about_the_branch(repo):
    """The failure keys are added ONLY on failure, so their ABSENCE is the success signal and no
    existing consumer of a released entry has to learn a new field."""
    ensure_workspace(42, cwd=repo)
    res = release_workspace(42, cwd=repo)
    assert res["released"] is True
    assert "branch_deleted" not in res and "warning" not in res
# --- VMCP-72: the sweep's liveness read is bounded OVERALL, not just per request ---
#
# Modelled rather than slept: the deadline measures DURATIONS, so a test that really waited would
# be slow AND flaky. `_FakeClock` is the clock the deadline reads and the transport advances, so
# "how long did this read hold the lock" is an exact number here instead of a stopwatch.


class _FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def _slow_board_client(clock, seconds_per_request, sent, attempted, *,
                       review=0, your_call=0, page_size=50, hooks=()):
    """A REAL httpx.Client over a transport that models a tracker answering every request
    `seconds_per_request` late — and, like a real socket, giving up when that exceeds the timeout
    it was handed. That second half is what makes the clamp observable at all: httpcore reads
    `request.extensions["timeout"]` when it sends, MockTransport does not, so the model must.
    (Verified against the real thing before it was modelled: a 10 s budget on a read needing 18 s
    came back at 9.96 s with a ReadTimeout, not at 12 s.)

    `attempted` records every request the CLIENT tried to send (hook level, so it includes the
    ones the deadline refuses); `sent` records only those that reached the transport. Two lists
    because the difference between them IS the deadline's effect.
    """
    def record(request):
        attempted.append(str(request.url).split("/api/v1")[-1])

    def handler(request):
        allowed = (request.extensions.get("timeout") or {}).get("read")
        took = seconds_per_request if allowed is None else min(seconds_per_request, allowed)
        clock.t += took
        sent.append(str(request.url).split("/api/v1")[-1])
        if allowed is not None and seconds_per_request > allowed:
            raise httpx.ReadTimeout("timed out", request=request)
        path = request.url.path
        if path.endswith("/info"):
            return httpx.Response(200, json={"max_items_per_page": page_size})
        if path.endswith("/user"):
            return httpx.Response(200, json={"id": 1, "username": "agent"})
        if path.endswith("/views"):
            return httpx.Response(200, json=[{"id": 7, "view_kind": "kanban", "title": "K"}])
        page = int(request.url.params.get("page", 1))
        counts = {"Review": review, "Your Call": your_call}
        return httpx.Response(200, json=[
            {"id": index, "title": title, "tasks": [
                {"id": index * 10_000 + i, "title": f"t{i}", "assignees": []}
                for i in range((page - 1) * page_size,
                               min(page * page_size, counts.get(title, 0)))
            ]}
            for index, title in enumerate(STAGES, start=1)
        ])

    return httpx.Client(
        base_url="http://tracker.invalid/api/v1",
        transport=httpx.MockTransport(handler),
        timeout=workspace_cmd._READ_TIMEOUT_SECONDS,
        event_hooks={"request": [record, *hooks]},
    )


def _workflow_on(client):
    """The REAL Workflow and the REAL api.py client loop — only the socket is modelled. The
    swallowing that matters (`_fetch_page_size` eating httpx errors) lives in api.py, so a fake
    api here would prove nothing about it."""
    return Workflow(VikunjaAPI("http://tracker.invalid", "t", client=client, max_retries=0), 10)


def _read(wf, deadline=None):
    """PRODUCTION's own read helper — the whole unit the budget covers, arming included. Driven
    here rather than imitated: the relabelling and the arming are the behaviour under test, and a
    test-local copy of the read would exercise neither."""
    return workspace_cmd._read_liveness(wf, deadline)


def test_the_liveness_read_costs_one_more_request_per_page_of_a_human_drained_column():
    """The premise of `_READ_DEADLINE_SECONDS`: the hold is the REQUEST COUNT, and the count is
    not a constant. `liveness_board` pages Review and (since VMCP-68) Your Call exhaustively —
    the two columns the pump cannot drain, because a card leaves them only when a human moves it
    to Done or answers it. So a per-request bound cannot bound the hold: it multiplies.

    Measured against the real tracker at 4 requests / ~1 s; modelled here at 3 s per request so
    the arithmetic is visible. (Since VMCP-103 every board read also spends ONE confirming page
    past its last page with content — a short page stopped proving a bucket is exhausted — so the
    counts here are the old ones plus that flat one, on both boards. VMCP-108 adds ONE more, once
    per read and independent of the board: `views()` is now paged too, and this transport models
    the real 2.3.0 behaviour of serving the whole list and ignoring `?page=`, so it stops on the
    confirming page — the flat +1 the uniform rule costs.)"""
    clock, sent, attempted = _FakeClock(), [], []
    wf = _workflow_on(_slow_board_client(clock, 3.0, sent, attempted, review=41, your_call=5))
    _read(wf)
    assert len(sent) == 6 and clock.t == pytest.approx(18.0)      # today's board (+1 confirming)

    for column in ({"review": 140}, {"your_call": 140}):          # EITHER one drives it
        clock, sent, attempted = _FakeClock(), [], []
        wf = _workflow_on(_slow_board_client(clock, 3.0, sent, attempted, **column))
        _read(wf)
        assert len(sent) == 8, f"{column}: {sent}"
        assert clock.t == pytest.approx(24.0), f"{column} held the lock {clock.t}s"


def test_the_sweep_read_is_bounded_overall_not_only_per_request():
    """The fix itself, stated as the delta it buys. The SAME read, at the SAME per-request
    ceiling: unbounded it holds the lock for eight times that ceiling, budgeted it stops at the
    budget and abandons — refusing the next request BEFORE sending it, so the abandon costs
    nothing more.

    At this deliberately absurd 10 s/request the budget is now spent before the board pages are
    reached at all (VMCP-108's paged `views()` costs one of the three requests that fit). That is
    an artefact of the model, not of production: the same read was MEASURED against the real
    tracker at ~0.25 s/request, where the extra page is noise against a 30 s budget. What the
    assertions below pin is the property that does not depend on the rate — the read is abandoned
    BEFORE any liveness set exists to act on, which is the whole reason the budget is enforced at
    the request hook rather than around the call."""
    per_request = workspace_cmd._READ_TIMEOUT_SECONDS
    budget = workspace_cmd._READ_DEADLINE_SECONDS

    clock, sent, attempted = _FakeClock(), [], []
    wf = _workflow_on(_slow_board_client(clock, per_request, sent, attempted, your_call=140))
    _read(wf)
    assert len(sent) == 8 and clock.t == pytest.approx(8 * per_request)   # 80s of held lock
    # 140 Your Call cards at 50/page = 3 pages + VMCP-103's confirming page; the other four
    # requests are /info, the two views pages and the /user active_task_ids needs.
    assert sum("/tasks" in url for url in sent) == 4

    clock, sent, attempted = _FakeClock(), [], []
    deadline = workspace_cmd._ReadDeadline(budget, now=clock)
    wf = _workflow_on(_slow_board_client(clock, per_request, sent, attempted,
                                         your_call=140, hooks=[deadline]))
    with pytest.raises(ReadDeadlineExceeded):
        _read(wf, deadline)
    assert clock.t == pytest.approx(budget)          # the hold IS the budget, not 80s
    assert len(attempted) == len(sent) + 1           # one refused before it went out
    assert len(sent) == budget / per_request         # and it stopped ON the budget, not past it
    # abandoned with the board unread — no liveness set was ever built, so nothing could be reaped
    assert not any("/tasks" in url for url in sent)


def test_a_spent_read_budget_is_not_swallowed_by_the_page_size_fallback():
    """`api._fetch_page_size` swallows `(VikunjaError, httpx.HTTPError)` and answers "unknown".
    Were `ReadDeadlineExceeded` an httpx exception, a budget that ran out at `/info` would be
    EATEN right there and the read would carry on past its own deadline — the bound silently gone
    on exactly the boards big enough to need it. Being a WorkspaceError, it stops the read where
    it fires: nothing after `/info` is even attempted.

    Since VMCP-108 `/info` is the FIRST request of any read — every list GET resolves the page
    size before it pages — so the way to land the refusal inside that `except` is a budget that is
    already spent when the read starts, which is exactly the state a caller handed a used-up
    deadline is in. Nothing else can reach `/info` any more, and a test that let the refusal fall
    on the NEXT request instead would pass without ever entering the swallowing frame."""
    clock, sent, attempted = _FakeClock(), [], []
    deadline = workspace_cmd._ReadDeadline(0.0, now=clock)
    wf = _workflow_on(_slow_board_client(clock, workspace_cmd._READ_TIMEOUT_SECONDS,
                                         sent, attempted, hooks=[deadline]))
    with pytest.raises(ReadDeadlineExceeded):
        _read(wf, deadline)
    assert attempted == ["/info"]     # refused INSIDE _fetch_page_size — and not swallowed there
    assert sent == []


def test_the_read_budget_clamps_each_request_to_what_is_left():
    """Without the clamp the LAST request keeps its own full ceiling, so a read that starts one
    tick inside the budget overshoots it by a whole `_READ_TIMEOUT_SECONDS` — the bound would be
    "budget plus a timeout", not the budget. Clamped, the read ends ON the budget.

    And it is reported as the BUDGET, not as the bare `ReadTimeout` the clamp actually raises —
    the finding that came out of running this end to end (see `_read_liveness`): a sweep that
    stops exactly on its budget and says "timed out" is indistinguishable from one request timing
    out, so the operator learns nothing from the line that matters most."""
    clock, sent, attempted = _FakeClock(), [], []
    per_request = workspace_cmd._READ_TIMEOUT_SECONDS
    budget = 2.5 * per_request                     # runs out HALFWAY through the third request
    deadline = workspace_cmd._ReadDeadline(budget, now=clock)
    wf = _workflow_on(_slow_board_client(clock, per_request, sent, attempted,
                                         your_call=140, hooks=[deadline]))
    with pytest.raises(ReadDeadlineExceeded) as caught:
        _read(wf, deadline)
    assert clock.t == pytest.approx(budget)        # not 3 x per_request
    assert "overall budget" in str(caught.value)
    assert isinstance(caught.value.__cause__, httpx.ReadTimeout)   # cause kept, not lost


def test_a_failure_with_budget_left_is_not_laundered_into_a_deadline():
    """The other direction of the relabelling, and the reason it keys on `spent()` alone: a read
    that fails while the budget still has time on it — a 500, a refused connection, a bad token —
    is NOT the tracker being slow, and calling it that would send an operator hunting latency for
    a broken token. It must propagate exactly as it is."""
    clock = _FakeClock()          # never advanced: the budget is untouched when this fails
    deadline = workspace_cmd._ReadDeadline(workspace_cmd._READ_DEADLINE_SECONDS, now=clock)

    def refuse(request):
        raise httpx.ConnectError("connection refused", request=request)

    client = httpx.Client(base_url="http://tracker.invalid/api/v1",
                          transport=httpx.MockTransport(refuse),
                          event_hooks={"request": [deadline]})
    with pytest.raises(httpx.ConnectError):
        _read(_workflow_on(client), deadline)


def test_gc_reaps_nothing_when_the_liveness_read_is_abandoned(repo, tracker):
    """THE invariant, and the one that makes this a latency fix rather than a data-loss bug: an
    abandoned read must leave every tree alone — including one that is dead, quiet and otherwise
    perfectly reapable. A partial or failed `alive` set can never reach the reap loop, because
    the read raises before the loop is entered. Also proves the lock is RELEASED on that path:
    bounding the hold is pointless if abandoning leaks it."""
    api, wf = tracker
    path = Path(ensure_workspace(42, cwd=repo)["path"])          # nothing on the board -> dead
    _quiesce(path)

    class AbandoningWorkflow:
        """What the deadline hook looks like from gc's side: the read raises, nothing else runs."""

        def liveness_board(self):
            raise ReadDeadlineExceeded("the liveness read exceeded its overall budget")

        def active_task_ids(self, board=None):
            raise AssertionError("a liveness set was computed from an abandoned read")

        review_task_ids = parked_task_ids = active_task_ids

    with pytest.raises(ReadDeadlineExceeded):
        gc_workspaces(cwd=repo, workflow=AbandoningWorkflow())

    assert path.exists()                                          # KEEP
    assert _git(repo, "branch", "--list", "task/42").strip()      # and its branch
    common = Path(_git(repo, "rev-parse", "--git-common-dir"))
    if not common.is_absolute():
        common = (repo / common).resolve()
    with open(common / "vikunja-mcp-worktree.lock", "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)            # free again, not leaked
        fcntl.flock(fh, fcntl.LOCK_UN)


def test_gc_arms_the_read_budget_only_once_it_holds_the_lock(repo, tracker, monkeypatch):
    """The budget bounds the HOLD, and `_repo_lock` BLOCKS. Armed at construction — before the
    flock — a sweep queued behind another agent's ensure/--release/--gc would spend its budget
    WAITING, then abandon a read it never got to start: every contended tick failing, forever,
    having done nothing wrong. Proven the way Important 5 proves the read's placement: the probe
    for a second, non-blocking flock must FAIL at arming time, i.e. the lock is already held."""
    api, wf = tracker
    common = Path(_git(repo, "rev-parse", "--git-common-dir"))
    if not common.is_absolute():
        common = (repo / common).resolve()
    lock_path = common / "vikunja-mcp-worktree.lock"
    events = []

    class ProbingDeadline:
        def arm(self):
            with open(lock_path, "w") as fh:
                with pytest.raises(BlockingIOError):
                    fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            events.append("armed")

    class RecordingWorkflow:
        def liveness_board(self):
            events.append("read")
            return wf.liveness_board()

        def active_task_ids(self, board=None):
            return wf.active_task_ids(board=board)

        def review_task_ids(self, board=None):
            return wf.review_task_ids(board=board)

        def parked_task_ids(self, board=None):
            return wf.parked_task_ids(board=board)

    monkeypatch.setattr(workspace_cmd, "_build_workflow",
                        lambda root: (RecordingWorkflow(), ProbingDeadline()))
    gc_workspaces(cwd=repo)                     # workflow=None -> the PRODUCTION path
    assert events == ["armed", "read"]          # armed under the lock, and before the read


def test_the_gc_client_carries_the_read_budget_as_a_request_hook(repo, monkeypatch):
    """The bound is only real if it is on the client gc actually BUILDS. Pinned here because
    every other test in this group installs the hook itself, so a `_build_workflow` that quietly
    stopped attaching it would leave them all green and the production sweep unbounded."""
    from vikunja_mcp import config as config_mod
    from vikunja_mcp.workspace_cmd import _build_workflow

    monkeypatch.setattr(config_mod, "load_config", lambda cwd=None, environ=None:
                        config_mod.Config(url="http://example.invalid", token="t", project_id=7))
    wf, deadline = _build_workflow(repo)

    assert isinstance(deadline, workspace_cmd._ReadDeadline)
    assert deadline in wf.api._client.event_hooks["request"]
    assert deadline.budget == workspace_cmd._READ_DEADLINE_SECONDS


def test_an_abandoned_sweep_is_one_json_error_line_the_pump_can_read(monkeypatch, capsys,
                                                                     tmp_path):
    """`ReadDeadlineExceeded` is public and unprefixed because the class name IS the CLI's error
    string. A pump that sees `{"error": "ReadDeadlineExceeded: ..."}` knows the tracker was slow,
    not that its worktrees are broken — and exit 1 puts it on the path SKILL.md already covers
    ("--gc не достучался до трекера": degrade the drain, never stop it)."""
    monkeypatch.chdir(tmp_path)                    # see the hygiene note above

    def boom():
        raise ReadDeadlineExceeded("the liveness read exceeded its 30s overall budget")

    monkeypatch.setattr("vikunja_mcp.workspace_cmd.gc_workspaces", boom)
    assert run_workspace(["--gc"]) == 1
    err = json.loads(capsys.readouterr().out.strip())["error"]
    assert err.startswith("ReadDeadlineExceeded: ") and "overall budget" in err


# --- VMCP-89: a page size the client had to GUESS must never be able to end in a reap ---

def _paginating_tracker(page_size, tasks_by_stage, *, info_status=200, sent=None):
    """A REAL httpx client over a tracker that pages each bucket's `tasks` the way Vikunja 2.3
    does — `page_size` at a time, per bucket, driven by `?page=` — and whose `/info` can be made
    to fail.

    Real client + real api.py, not `FakeAPI`, because the whole mechanism lives in api.py: the
    fake resolves no page size at all, so a board truncated by a WRONG one is invisible to it —
    the shape this project has already been bitten by (a fake that shares the code's own wrong
    model proves nothing about it).
    """
    def handler(request):
        path = request.url.path
        if sent is not None:
            sent.append(f"{path.split('/api/v1')[-1]}?{request.url.params}".rstrip("?"))
        if path.endswith("/info"):
            if info_status != 200:
                return httpx.Response(info_status, json={"message": "boom"})
            return httpx.Response(200, json={"max_items_per_page": page_size})
        if path.endswith("/user"):
            return httpx.Response(200, json={"id": 1, "username": "agent"})
        if path.endswith("/views"):
            return httpx.Response(200, json=[{"id": 7, "view_kind": "kanban", "title": "Kanban"}])
        page = int(request.url.params.get("page", 1))
        return httpx.Response(200, json=[
            {"id": index, "title": title, "tasks": [
                {"id": tid, "title": f"t{tid}", "assignees": [{"id": 1}]}
                for tid in tasks_by_stage.get(title, [])[(page - 1) * page_size:page * page_size]
            ]}
            for index, title in enumerate(STAGES, start=1)
        ])

    return httpx.Client(base_url="http://tracker.invalid/api/v1",
                        transport=httpx.MockTransport(handler))


@pytest.mark.parametrize("info_status, why", [
    (200, "the control: with /info healthy the same board reads whole"),
    (500, "the bug: /info failed, so the page size had to be guessed"),
])
def test_gc_keeps_every_live_tree_when_the_servers_pages_are_smaller_than_the_guess(
    repo, info_status, why
):
    """THE data-loss path this task exists for, constructed rather than reasoned about.

    A server whose real `max_items_per_page` is 3 and an `/info` that fails: the client used to
    fall back to a hardcoded 50, and `view_tasks` stopped paging as soon as no bucket returned a
    FULL page — which on this server is never. The board silently ended after page 1, the tasks
    past it read as gone, and `--gc` destroyed their worktrees. Observed before the fix, on this
    exact test: `released=[804, 805]`, two LIVE trees and their `task/*` branches deleted, in a
    sweep that reported success.

    The 200 row is the control, and it is what makes the 500 row trustworthy: the identical board
    and the identical trees, with the ONLY difference being whether the client could know the page
    size. Both must keep all five.
    """
    live = [801, 802, 803, 804, 805]                 # 5 tasks, 3 per page -> two pages
    trees = {}
    for task_id in live:
        trees[task_id] = Path(ensure_workspace(task_id, cwd=repo)["path"])
        _quiesce(trees[task_id])                     # past the grace window: reapable if dead
    client = _paginating_tracker(3, {"Build": live}, info_status=info_status)
    wf = Workflow(VikunjaAPI("http://tracker.invalid", "t", client=client, max_retries=0), 10)

    res = gc_workspaces(cwd=repo, workflow=wf)

    assert res["released"] == [], why
    assert res["kept"] == []
    for task_id, path in trees.items():
        assert path.is_dir(), f"{why}: live tree for {task_id} was destroyed"
        assert _git(repo, "branch", "--list", f"task/{task_id}").strip()



# --- VMCP-92 (548): the DEGRADED read is bounded, and its bound keeps gc at KEEP ---

def test_gc_keeps_every_tree_when_the_degraded_board_read_hits_its_ceiling(repo):
    """The bound added to `view_tasks` on an unknown page size RAISES rather than returning a
    short board, and this is why that direction matters — end to end through the reaper rather
    than reasoned about.

    A server whose `/info` is down and whose board never converges (a brand-new Build task on
    every page) used to page forever; it now raises, and the exception has to leave `view_tasks`,
    leave `_read_liveness` and abandon the sweep BEFORE the reap loop. Had the read returned its
    partial board instead, every one of these quiesced trees would read as dead and be destroyed
    — the exact VMCP-89 reap, re-opened by a different route."""
    trees = {}
    for task_id in (801, 802, 803):
        trees[task_id] = Path(ensure_workspace(task_id, cwd=repo)["path"])
        _quiesce(trees[task_id])

    sent = []

    def handler(request):
        path = request.url.path
        if path.endswith("/info"):
            return httpx.Response(503, json={"message": "unavailable"})
        if path.endswith("/user"):
            return httpx.Response(200, json={"id": 1, "username": "agent"})
        if path.endswith("/views"):
            return httpx.Response(200, json=[{"id": 7, "view_kind": "kanban", "title": "Kanban"}])
        page = int(request.url.params.get("page", 1))
        sent.append(page)
        if len(sent) > 3 * MAX_UNPROVEN_PAGES:      # the loop does not terminate -> fail LOUDLY
            raise RuntimeError("the liveness read paged past three times the ceiling")
        return httpx.Response(200, json=[
            {"id": 2, "title": "Build",
             "tasks": [{"id": 9000 + page, "title": f"t{page}", "assignees": [{"id": 1}]}]},
        ])

    client = httpx.Client(base_url="http://tracker.invalid/api/v1",
                          transport=httpx.MockTransport(handler))
    wf = Workflow(VikunjaAPI("http://tracker.invalid", "t", client=client, max_retries=0), 10)

    with pytest.raises(VikunjaError) as exc:
        gc_workspaces(cwd=repo, workflow=wf)

    assert "never finished paging" in exc.value.message
    assert len(sent) == MAX_UNPROVEN_PAGES
    for task_id, path in trees.items():
        assert path.is_dir(), f"live tree for {task_id} was destroyed"
        assert _git(repo, "branch", "--list", f"task/{task_id}").strip()


# --- VMCP-90 (545): gc's own inspection is not the tree's activity ---
#
# The interaction between VMCP-71 (the grace window) and VMCP-68 (`kept` means "a human should
# look"): inspecting a tree meant running `git status` in it, that rewrites the index, and the next
# sweep read its own footprint as an agent's and skipped the tree silently. MEASURED before the
# fix, three consecutive sweeps over the same quiesced trees: sweep 1 `kept=[unreachable-head,
# unpushed, half-created]`, sweeps 2 and 3 `kept=[half-created]` — a standing alarm absent from
# ~29 of every 30 minutes of ticks. These pin BOTH directions, because getting it wrong the other
# way (a window that no longer defers to a real write) destroys a working directory under a
# running agent, which is far worse than a delayed alarm.

def test_gc_reports_a_standing_alarm_on_every_consecutive_sweep(repo, tracker):
    """THE defect. Quiesced ONCE, then swept three times back to back with nothing else touching
    the tree: the only writer between sweeps is gc itself, so the entry must appear every time.

    Two trees, because the split was diagnostic: `unpushed` is decided by a guard gc reaches
    THROUGH `git status`, `half-created` before any git call in the tree at all — before the fix
    the first vanished after sweep 1 and the second did not. Make `_git_inspect` a plain `_git`
    again and this goes red on sweep 2."""
    api, wf = tracker
    unpushed = _unpushed_build_tree(repo, 42)
    half = Path(ensure_workspace(99, cwd=repo)["path"])
    common = Path(_git(repo, "rev-parse", "--git-common-dir"))
    if not common.is_absolute():
        common = (repo / common).resolve()
    (common / "worktrees" / half.name / "locked").write_text(workspace_cmd._LOCK_INITIALIZING)
    _quiesce(half)

    sweeps = [
        sorted((e["task_id"], e["code"]) for e in gc_workspaces(cwd=repo, workflow=wf)["kept"])
        for _ in range(3)
    ]

    assert sweeps == [[(42, workspace_cmd.CODE_UNPUSHED),
                       (99, workspace_cmd.CODE_HALF_CREATED)]] * 3
    assert unpushed.is_dir() and half.is_dir()          # reported, never removed


def test_gcs_own_sweep_leaves_the_grace_markers_untouched(repo, tracker):
    """The mechanism, pinned directly rather than through its consequence: a whole sweep over a
    tree it refuses must leave BOTH markers `_last_activity` reads exactly as it found them.

    Cheap net for the next guard added to `_release_locked` — `git diff` refreshes the index the
    same way `git status` does, so a new inspection wired through plain `_git` fails here, in one
    line, instead of quietly restoring the cadence bug two releases later."""
    api, wf = tracker
    path = _unpushed_build_tree(repo, 42)
    before = [m.stat().st_mtime_ns for m in _grace_markers(path)]

    res = gc_workspaces(cwd=repo, workflow=wf)

    assert [k["code"] for k in res["kept"]] == [workspace_cmd.CODE_UNPUSHED]   # it DID inspect
    assert [m.stat().st_mtime_ns for m in _grace_markers(path)] == before


def test_gc_still_defers_to_a_real_write_in_a_tree_it_has_already_inspected(repo, tracker):
    """THE INVARIANT, in the direction that destroys work if it is wrong. A tree gc has already
    inspected once must still read as YOUNG the moment something real writes in it — otherwise the
    fix above trades a late alarm for a working directory vanishing under a running agent.

    Constructed so only the window stands between the tree and removal: sweep 1 refuses it
    (`unpushed`), then the agent — still standing in it between `advance(to='review')` and
    `--release` — pushes, which satisfies the last guard, and runs the `git status` SKILL.md's own
    recipe has it run. The directory is aged back afterwards so the INDEX is the only fresh marker
    left, i.e. the one an hour-old tree really has. Sweep 2 must NOT INSPECT and NOT REMOVE it.
    Drop the index from `_last_activity` and this does not merely fail — the tree is destroyed.

    VMCP-300 (1183) changed how sweep 2 SAYS that, and made this test stronger rather than
    weaker. The skip used to be silent, so "the window fired" could only be inferred from three
    empty lists — which is also what a sweep that never saw the tree looks like. It is now
    reported in `deferred`, so the window firing is asserted POSITIVELY, and the three
    verdict-carrying lists staying empty still says the tree was never inspected."""
    api, wf = tracker
    path = _unpushed_build_tree(repo, 42)
    first = gc_workspaces(cwd=repo, workflow=wf)
    assert [(k["task_id"], k["code"]) for k in first["kept"]] == [(42, workspace_cmd.CODE_UNPUSHED)]

    _git(path, "push", "origin", "HEAD:main")           # every release guard now passes
    tree_dir, _index = _grace_markers(path)
    old = time.time() - workspace_cmd._REAP_GRACE_SECONDS - 60
    os.utime(tree_dir, (old, old))
    _git(path, "status", "--porcelain")                 # the agent's own call: it DOES take the lock
    os.utime(tree_dir, (old, old))                      # ...so the index is the only fresh marker

    second = gc_workspaces(cwd=repo, workflow=wf)

    # VMCP-238 (801) relaxed this from an exact-dict equality, and the reason is that the push
    # above is REAL: it advances `origin/main` past the main checkout, so `--gc`'s new
    # fast-forward legitimately has work to do and reports it. The assertion this test exists
    # for is untouched — the three VERDICT lists still empty, i.e. no guard ran on this tree —
    # and the "gc returns exactly these keys when there is nothing else to say" half did not go
    # unpinned: it moved to test_gc_says_nothing_about_a_main_checkout_that_is_already_current,
    # which asserts it on the only path where it is honestly true.
    assert {k: v for k, v in second.items() if k not in ("main_checkout", "deferred")} == {
        "released": [], "kept": [], "expected": []}
    assert [(d["task_id"], d["code"]) for d in second["deferred"]] == [
        (42, workspace_cmd.DEFER_YOUNG)], (
        "the window did not fire — with the index dropped from `_last_activity` this tree is not "
        "merely un-reported, it is inspected and destroyed"
    )
    assert path.is_dir() and (path / "feature.txt").exists()


# --- VMCP-142 (631): a HUMAN `git worktree lock` is a coded VERDICT, not a raise ---

def _human_locked(repo, task_id, role="build", reason="human says hands off", at=None):
    """A tree a human pinned with `git worktree lock` — the state this section is about.

    Deliberately built through the real CLI helpers and real git: the whole finding was that the
    module's own guards all pass on this tree and the refusal only happens inside `git worktree
    remove`, which no fake would reproduce.
    """
    path = Path(ensure_workspace(task_id, role=role, at=at, cwd=repo)["path"])
    args = ["worktree", "lock"]
    if reason is not None:
        args += ["--reason", reason]
    _git(repo, *args, str(path))
    return path


def test_release_refuses_a_human_locked_tree_with_a_code_in_both_porcelain_shapes(repo):
    """THE finding, reproduced before the fix and pinned here in the shape the fix produces.

    A tree a human locked, and OTHERWISE clean, pushed and on its branch, passed every guard in
    `_release_locked` — not half-created, not dirty, not unpushed, not detached — and then died
    inside `git worktree remove` ("fatal: cannot remove a locked working tree"). That qualifier is
    load-bearing and was missing from the first draft of this docstring: a locked tree that is ALSO
    dirty or unpushed never reached the remove at all, because those guards answered first (see
    `test_a_locked_tree_reports_the_lock_even_when_it_is_also_dirty`).
    `run_workspace`'s catch-all rendered that raise as `{"error": …}` +
    exit 1, i.e. the CREATE channel, for a state that is unambiguously the OTHER kind: the work is
    intact and a human deliberately pinned the tree. SKILL.md has agents branch on exactly that
    split, so the shape decided whether an agent shrugged ("the tool could not run, degrade to one
    slot") or reported a tree a human is holding.

    BOTH porcelain shapes, because the guard must key on the `locked` BOOL and never on the reason
    TEXT: a bare `git worktree lock` reports `lock_reason: None`, so a guard written against a
    reason string would refuse the documented case and sail past the reasonless one.
    """
    path = _human_locked(repo, 42)

    res = release_workspace(42, cwd=repo)

    assert res["released"] is False
    assert res["code"] == workspace_cmd.CODE_LOCKED
    assert "human says hands off" in res["reason"]              # git's own reason, not our guess
    assert f"git worktree unlock {path}" in res["reason"]       # the human's actual next step
    assert path.is_dir()

    _git(repo, "worktree", "unlock", str(path))
    _git(repo, "worktree", "lock", str(path))                   # the REASONLESS shape
    bare = release_workspace(42, cwd=repo)
    assert bare["released"] is False and bare["code"] == workspace_cmd.CODE_LOCKED
    assert f"git worktree unlock {path}" in bare["reason"]
    assert path.is_dir()


def test_release_refuses_a_locked_review_tree_with_the_same_code(repo):
    """Same guard, the other role. A reviewer's detached tree reaches the refusal down a DIFFERENT
    branch of `_release_locked` (no `task/<id>` branch, so the unpushed-commits guard is skipped
    for the reachability one), and the lock has to be answered before either — a lock is a
    statement about the DIRECTORY, and git refuses removal regardless of what is checked out."""
    head = _git(repo, "rev-parse", "HEAD")
    path = _human_locked(repo, 7, role="review", reason="reviewer pinned this", at=head)

    res = release_workspace(7, role="review", cwd=repo)

    assert res["released"] is False and res["code"] == workspace_cmd.CODE_LOCKED
    assert res["role"] == "review"
    assert "reviewer pinned this" in res["reason"]
    assert path.is_dir()


def test_release_of_a_locked_entry_whose_directory_is_gone_is_a_verdict_not_a_crash(repo):
    """The state that pins the guard's PLACEMENT rather than its condition, and the one that used
    to fail loudest: a locked entry whose directory a human moved or deleted.

    `git worktree prune` REFUSES to drop a locked entry (measured, and the reason `list_worktrees`
    reads HEAD out of the porcelain instead of running git inside the tree), so `_find` still hands
    it back — and the very next line used to be `_git_inspect("status", cwd=path)`, whose
    `subprocess.run(cwd=<gone>)` raises a bare `FileNotFoundError` that `_git` cannot convert into
    a WorkspaceError. Verdict shape aside, that is a DIFFERENT mechanism from the lock refusal with
    the same root, and only a guard placed BEFORE the first git call with cwd inside the tree
    answers both. Move the lock guard below the dirty check and this test goes red while every
    other test in this section stays green."""
    path = _human_locked(repo, 44, reason="moved this aside")
    shutil.rmtree(path)

    res = release_workspace(44, cwd=repo)

    assert res["released"] is False and res["code"] == workspace_cmd.CODE_LOCKED
    assert not path.exists()
    # the entry is still registered, so a human still has something to unlock
    assert "task-44" in _git(repo, "worktree", "list", "--porcelain")


def test_run_workspace_release_of_a_locked_tree_stays_in_the_release_channel(
    repo, monkeypatch, capsys
):
    """End to end through the CLI, which is where the channel is actually observable — and the
    whole point of the card. Exit 0 and the release channel's EXACT key set, asserted as a whole
    for the reason `test_the_two_refusal_channels_are_not_interchangeable` gives: a shape that
    grows or loses a key silently is one SKILL.md's branch stops fitting."""
    path = _human_locked(repo, 42)
    monkeypatch.chdir(repo)

    assert run_workspace(["--release", "42"]) == 0, (
        "a locked tree is a NEGATIVE VERDICT, not a CLI failure: the command RAN and is protecting "
        "a tree a human pinned"
    )
    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["released"] is False
    assert payload.get("code") == workspace_cmd.CODE_LOCKED
    assert set(payload) == {"released", "task_id", "role", "path", "code", "reason"}, (
        f"the RELEASE channel's key set moved to {sorted(payload)} for a locked tree"
    )
    assert path.is_dir()


def test_gc_reports_a_human_locked_tree_in_kept_and_keeps_sweeping(repo, tracker):
    """The unattended path: `--gc` meets this state on a tick, and it must produce ONE actionable
    `kept` line without costing the sweep its other verdicts.

    It already kept the tree before the fix — via the catch-all that turns any raise into
    `release-error` — so what changes here is WHICH code a human reads: `locked` names the state
    and the one command that clears it, where `release-error` handed them git's text and left the
    diagnosis to them."""
    api, wf = tracker
    locked = _human_locked(repo, 42)
    other = Path(ensure_workspace(43, cwd=repo)["path"])          # also dead, clean, pushed
    _quiesce(locked)
    _quiesce(other)

    res = gc_workspaces(cwd=repo, workflow=wf)

    assert [r["task_id"] for r in res["released"]] == [43]
    assert not other.exists()
    assert [(k["task_id"], k["code"]) for k in res["kept"]] == [(42, workspace_cmd.CODE_LOCKED)]
    assert res["expected"] == []
    assert locked.is_dir()


def test_a_human_lock_stays_in_kept_even_when_the_board_would_excuse_it(repo, tracker):
    """THE GRADING DECISION, constructed rather than asserted against the frozensets.

    `_keep_is_expected` excuses a refusal only in two board states, and this tree is built to
    satisfy BOTH conjuncts at once — it is a REVIEW tree (what excuses `unreachable-head`) whose
    card is PARKED in Your Call (what excuses `dirty`/`unpushed`) — so the only thing that can send
    it to `kept` is the code itself.

    Why `kept` and not `expected`: `expected` is for states the pipeline produces on the happy path
    AND that already carry their own signal to the human (the parked card IS that signal, for
    hours, while `call_human` waits). A `git worktree lock` is neither — nothing on the board says
    a tree is pinned, and the lock makes it permanently unreapable until a human clears it, which
    is the shape of `half-created`, the code the policy calls correctly-never-routine. Add
    CODE_LOCKED to either `_EXPECTED_*` set and this goes red."""
    api, wf = tracker
    task = api.add_task("parked work", "Queue")
    task_id = task["id"]
    wf.claim(task_id)
    wf.call_human(task_id, "rebase conflict")                     # -> Your Call, i.e. parked
    assert task_id in set(wf.parked_task_ids())
    head = _git(repo, "rev-parse", "HEAD")
    locked = _human_locked(repo, task_id, role="review", reason="human is inspecting", at=head)
    _quiesce(locked)

    res = gc_workspaces(cwd=repo, workflow=wf)

    assert res["expected"] == [], "a human lock was graded routine — nobody reads `expected`"
    assert [(k["task_id"], k["code"]) for k in res["kept"]] == [
        (task_id, workspace_cmd.CODE_LOCKED)
    ]
    assert locked.is_dir()


def test_a_locked_tree_reports_the_lock_even_when_it_is_also_dirty(repo, tracker):
    """THE ONE BEHAVIOUR CHANGE THIS CARD MADE BEYOND THE CHANNEL, found by an independent second
    pass over its own prose and measured on both sides rather than reasoned about.

    The lock guard sits ahead of the dirty/unpushed guards, so a tree that is locked AND dirty now
    reports `locked` where it used to report `dirty`. That is the right code — the lock is what
    makes the tree unremovable, and `dirty`'s recovery ("commit, push, retry") cannot work while it
    stands — but it also changes how `--gc` GRADES the tree when its card is parked in Your Call:
    `dirty`/`unpushed` are excused there (`expected`, the list nobody reads), a lock is not
    (`kept`). MEASURED on the pre-fix code with the same construction: `expected: [(42, "dirty")]`
    and `expected: [(42, "unpushed")]`; now `kept: [(42, "locked")]` for both.

    The direction is the safe one: the parked card excuses UNSAVED WORK because a human is coming
    back to it, and a lock is not unsaved work — nothing on that card says the tree cannot be
    reaped at all. Pinned so a future reordering cannot flip it back silently, into a list whose
    whole purpose is that it does not get read."""
    api, wf = tracker
    task = api.add_task("parked work", "Queue")
    task_id = task["id"]
    wf.claim(task_id)
    wf.call_human(task_id, "rebase conflict")                     # -> Your Call, i.e. parked
    path = Path(ensure_workspace(task_id, cwd=repo)["path"])
    (path / "scratch.txt").write_text("uncommitted\n")            # what `dirty` would answer
    _git(repo, "worktree", "lock", "--reason", "human says hands off", str(path))

    res = release_workspace(task_id, cwd=repo)
    assert res["released"] is False
    assert res["code"] == workspace_cmd.CODE_LOCKED, (
        "a dirty tree that is ALSO locked must report the lock: `dirty`'s recovery is to commit "
        "and retry, and no retry can release a locked tree"
    )

    _quiesce(path)
    swept = gc_workspaces(cwd=repo, workflow=wf)

    assert swept["expected"] == [], (
        "a locked tree was filed under `expected` because its card is parked — the parked card "
        "excuses unsaved work, not a lock nothing can reap"
    )
    assert [(k["task_id"], k["code"]) for k in swept["kept"]] == [
        (task_id, workspace_cmd.CODE_LOCKED)
    ]
    assert path.is_dir() and (path / "scratch.txt").exists()


# --- VMCP-185 (710): the ignored payload a removal destroys is NAMED, never silently dropped ---
#
# `git status --porcelain` does not report ignored paths at all, so the dirty guard reads a tree
# holding nothing but ignored files as CLEAN and both removal paths destroy them without a word.
# These tests fix the REPORT, not the guard: the tree is still removed (the alternative, refusing
# on any ignored path, paralyses every build tree that ran `uv run pytest` — see the module note).
# So each one asserts BOTH halves: what the report now says, and that the files really are gone.


def _ignoring(repo, *rules):
    """Give the repo a committed, PUSHED `.gitignore` — worktrees branch from origin, not here."""
    (repo / ".gitignore").write_text("".join(f"{rule}\n" for rule in rules))
    _git(repo, "add", ".gitignore")
    _git(repo, "commit", "-m", "ignore rules")
    _git(repo, "push", "origin", "main")


def test_release_names_the_ignored_files_it_destroys(repo):
    """The two artifacts SKILL.md tells an agent to write INTO its own worktree, both ignored."""
    _ignoring(repo, "*.png", ".playwright-mcp/")
    path = Path(ensure_workspace(42, cwd=repo)["path"])
    (path / "shot-42.png").write_bytes(b"\x89PNG evidence for the card")
    (path / ".playwright-mcp" / "42").mkdir(parents=True)
    (path / ".playwright-mcp" / "42" / "page-1.yml").write_text("aria snapshot\n")

    res = release_workspace(42, cwd=repo)

    assert res["released"] is True, "reporting a loss must not turn into refusing to reap"
    assert sorted(res["removed_ignored"]) == [".playwright-mcp/", "shot-42.png"]
    # The honest half: naming is not saving. Both are gone, and the report is the only trace.
    assert not path.exists()


def test_release_does_not_flag_reproducible_build_detritus(repo):
    """`.venv/` and `__pycache__/` are in EVERY build tree that ran the gates (measured, 3 of 3).

    Reported, they would put the key on every released entry — the never-read signal VMCP-68 had
    to split `kept` in two to cure, reintroduced in `released`. Absence of the key is the signal.

    Carries one entry per MECHANISM of the filter, not one per name — the set's membership is its
    own tautology, but the three ways a name is matched are real code: a path COMPONENT (`.venv/`),
    the same component NESTED (`pkg/__pycache__/`, which is the shape live trees actually
    have — `src/vikunja_mcp/__pycache__/` — and which a component-wise match reduced to "first
    component" would miss), a SUFFIX (`stray.pyc`) and a LEAF name (`.DS_Store`).
    """
    (repo / "pkg").mkdir()
    (repo / "pkg" / "mod.py").write_text("x = 1\n")        # tracked, so `pkg/` is not itself `??`
    _git(repo, "add", "pkg")
    _git(repo, "commit", "-m", "a package")
    _ignoring(repo, ".venv/", "__pycache__/", "*.pyc", ".DS_Store")
    path = Path(ensure_workspace(42, cwd=repo)["path"])
    (path / ".venv").mkdir()
    (path / ".venv" / "pyvenv.cfg").write_text("home = /usr\n")
    (path / "pkg" / "__pycache__").mkdir()
    (path / "pkg" / "__pycache__" / "mod.cpython-312.pyc").write_bytes(b"\x00")
    (path / "stray.pyc").write_bytes(b"\x00")
    (path / ".DS_Store").write_bytes(b"\x00")

    res = release_workspace(42, cwd=repo)

    assert res["released"] is True
    assert "removed_ignored" not in res, res.get("removed_ignored")
    assert not path.exists()


def test_a_path_git_had_to_QUOTE_is_never_called_routine(repo):
    """Fail-toward-reporting on the one input the filter cannot read: git escapes a name it cannot
    print raw (`core.quotePath`, on by default), so matching it component-wise would match the
    ESCAPE rather than the name.

    THE INPUT IS THE POINT, and a first version of this test got it wrong: it used
    `замер-42.png`, whose classification is False with the quote guard AND without it (no component
    of it is in the filter either way), so DELETING the guard left the whole file green — a
    fictitious pin, caught by an independent pass. The entry below carries a filtered component
    (`node_modules`) inside a directory whose SPACE is what makes git quote the whole path, so it
    is routine-looking to a component match and reported only because the guard fires first.
    Both spellings are asserted: with the guard the tree's payload is NAMED, and the quoting is
    what makes that non-obvious.
    """
    _git(repo, "config", "core.quotePath", "true")          # shared with every linked worktree
    (repo / "my dir").mkdir()
    (repo / "my dir" / "keep.txt").write_text("tracked, so the dir itself is not `??`\n")
    _git(repo, "add", "my dir")
    _git(repo, "commit", "-m", "a directory with a space in its name")
    _ignoring(repo, "node_modules/", "*.png")
    path = Path(ensure_workspace(42, cwd=repo)["path"])
    (path / "my dir" / "node_modules").mkdir(parents=True)
    (path / "my dir" / "node_modules" / "p.js").write_text("1\n")
    (path / "замер-42.png").write_bytes(b"\x89PNG")

    res = release_workspace(42, cwd=repo)

    assert res["released"] is True
    quoted = [e for e in res["removed_ignored"] if e.startswith('"')]
    assert '"my dir/node_modules/"' in quoted, res["removed_ignored"]
    assert len(quoted) == 2, res["removed_ignored"]         # the non-ASCII name is quoted as well
    assert workspace_cmd._is_reproducible_ignored('"my dir/node_modules/"') is False, (
        "an unquoted `my dir/node_modules/` IS routine — the guard is the only thing that keeps "
        "the quoted spelling out of the filter, so this is what a deleted guard flips"
    )


def test_the_report_is_capped_but_the_count_is_not(repo):
    """`--gc` runs unattended and its line is parsed by a hub process; a sibling project has
    already lost a session to an oversized read, so the COUNT has to survive the cap even when the
    names do not.

    WHAT THAT COUNT IS NOT — and this test's own input is why the distinction is easy to miss.
    Every entry here is a loose FILE at the worktree ROOT, so `removed_ignored_truncated` coincides
    with the number of files destroyed; the test below is an input where it does not. No general
    CONDITION for coinciding is claimed, here or above `_MAX_REPORTED_IGNORED`, and that is round 2
    of VMCP-249 (840) removing one rather than an omission. Round 1 stated a condition at BOTH sites
    and in two DIFFERENT strengths — "no more than one" in the module comment, "one per printed
    entry" here — and neither is a criterion, but not for the same reason, which is worth keeping
    straight: an entry standing for ZERO destroyed files satisfies "no more than one" and so breaks
    only the module comment's form, while a FILTERED entry standing for a loss cancels an
    over-report and breaks BOTH, this one included. So equal numbers prove nothing either way. The
    module comment carries both counterexamples and the arithmetic. What is
    measured is the negative: coinciding is NOT about directories being absent (one ignored
    directory holding exactly one file, plus 50 loose files, reports 51 against 51 files). That card
    is also where the module comment stopped calling this number the "TRUE total".

    THIS INPUT IS ALSO THE COUNTEREXAMPLE to that round's other universal, and it needed no new
    code: 57 loose ignored files in the worktree ROOT reach the cap. Read that the way the module
    comment does, which hedges where an earlier draft of THIS docstring stated it flat: it refutes
    "no amount of content inside a single directory gets there" only on the BROAD reading of "a
    single directory", the root being one; on the narrow reading, where the antecedent is an ignored
    directory that collapses to ONE entry, the root is not such a directory at all and what refutes
    the universal is the tracked-anchor case the module comment builds. What decides whether a
    directory becomes ONE entry is whether git DESCENDED into it, not whether it is ignored, and the
    root is never collapsed because the scan STARTS there — measured, that holds even on a worktree
    whose HEAD tree is EMPTY, so it is not about the root holding tracked files.
    """
    _ignoring(repo, "*.png")
    path = Path(ensure_workspace(42, cwd=repo)["path"])
    total = workspace_cmd._MAX_REPORTED_IGNORED + 7
    for n in range(total):
        (path / f"shot-{n:04d}.png").write_bytes(b"\x89PNG")

    res = release_workspace(42, cwd=repo)

    assert res["released"] is True
    assert len(res["removed_ignored"]) == workspace_cmd._MAX_REPORTED_IGNORED
    assert res["removed_ignored_truncated"] == total


# MUTATION SWEEP for the byte budget (VMCP-260, 862). One selection throughout
# (`tests/unit/test_workspace_cmd.py -k 'capped or cap or truncated or byte or BYTES'`, no `-q`),
# `collected 223 items / 210 deselected / 13 selected` and `0 ERROR lines` in EVERY round, each
# round read by COUNTING lines beginning `FAILED ` and lines beginning `ERROR ` separately — never
# by the first `N failed` in stdout, which in this file lands inside a printed docstring. Run in a
# `git clone --no-hardlinks` of its own with `__pycache__` deleted and `PYTHONDONTWRITEBYTECODE=1`,
# `vikunja_mcp.__file__` printed each round and resolving into the clone every time; control at the
# START and at the END, restore verified byte-identical. Control 0 failed:
#   * `_cap_reported` consults no byte budget (entry cap alone) . control 0 failed; 3 failed
#   * `_truncated` marks only ENTRY cuts, so a byte cut is silent  control 0 failed; 2 failed
#   * the never-empty rule dropped (`and kept` deleted) ......... control 0 failed; 1 failed
#   * CHARACTERS counted instead of serialized bytes ............ control 0 failed; 1 failed
# Control 0 failed at the end as well, on the restored tree and the same selection.
def test_the_report_is_capped_in_BYTES_too_and_not_only_in_entries(repo):
    """The entry cap alone does not hold the line under the read it is justified by, which is the
    whole of VMCP-260 (862) and is measured rather than argued.

    BEFORE the byte budget, on this same construction: 50 names — the entry cap's own maximum, so
    nothing was violated — came out as a 40,200-byte array on a 40,376-byte line, against the
    25,088 bytes that the sibling's fatal ">24.5 KiB" is the FLOOR of. What makes the names long
    here is a TRACKED anchor at each level: without it git reports the directory as ONE `!! d/`
    entry and the input collapses (the paragraph above `_MAX_REPORTED_IGNORED` builds that).

    The assertion is on the SERIALIZED array and not on the whole line on purpose: the line also
    carries `path`, whose length is a property of whatever temp root the run got and not of this
    code — measured for VMCP-249, two temp roots differing by 41 characters moved the total by
    exactly 41 bytes.
    """
    _ignoring(repo, "*.png")
    seg = "d" * 200
    deep = Path(seg) / seg / seg
    (repo / deep).mkdir(parents=True)
    cur = repo
    for part in deep.parts:                      # a tracked anchor at EVERY level, so git
        cur = cur / part                         # enumerates the ignored files one by one
        (cur / "anchor.txt").write_text("x\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "deep anchors")
    _git(repo, "push", "origin", "main")
    path = Path(ensure_workspace(42, cwd=repo)["path"])
    total = workspace_cmd._MAX_REPORTED_IGNORED + 10
    for n in range(total):
        pad = 800 - len(str(deep)) - 1 - 4 - len(".png")
        (path / deep / f"{n:04d}{'n' * pad}.png").write_bytes(b"\x89PNG")

    res = release_workspace(42, cwd=repo)

    assert res["released"] is True
    named = res["removed_ignored"]
    assert len(named) < workspace_cmd._MAX_REPORTED_IGNORED, (
        "the ENTRY cap is not what has to bind here — that is the defect 862 was filed for")
    assert len(json.dumps(named)) <= workspace_cmd._MAX_REPORTED_IGNORED_BYTES + 1024, (
        "one entry over budget is allowed (never an empty list); a second is not")
    assert len(json.dumps(res)) < 25088, "the whole line must clear the consumer floor"
    assert res["removed_ignored_truncated"] == total, (
        "a BYTE cut raises the same sibling an ENTRY cut does, still `len(destroyed)`")


def test_the_byte_budget_never_leaves_the_key_present_and_empty(repo):
    """A key present with an EMPTY list is the never-read field VMCP-68 had to split `kept` in two
    to cure, and it would strip the one-way reading — present ⇒ something unrecognised died — of
    the only thing it says. So the FIRST entry is kept even where it alone busts the budget; the
    overshoot is bounded by `PATH_MAX` rather than by hope.

    Driven through `_add_capped`, the shape `overwritten_ignored` and `half_applied` share.
    """
    huge = "z" * (workspace_cmd._MAX_REPORTED_IGNORED_BYTES + 200)

    one = {}
    workspace_cmd._add_capped(one, "overwritten_ignored", [huge])
    assert one["overwritten_ignored"] == [huge], "never empty, never absent"
    assert "overwritten_ignored_truncated" not in one, "nothing was dropped, so no sibling"

    two = {}
    workspace_cmd._add_capped(two, "overwritten_ignored", [huge, huge + "y"])
    assert two["overwritten_ignored"] == [huge], "the first survives, the second does not"
    assert two["overwritten_ignored_truncated"] == 2

    empty = {}
    workspace_cmd._add_capped(empty, "overwritten_ignored", [])
    assert empty == {}, "the only-when-non-empty rule is untouched by the byte budget"


def test_the_byte_budget_counts_serialized_bytes_and_not_characters(repo):
    """`json.dumps` prints at its default `ensure_ascii=True`, so a Cyrillic path costs SIX bytes
    per character. Counting characters would understate by 6x, and UTF-8 bytes by 3x, the very
    thing the budget exists to hold down — measured, 100 Cyrillic characters are 100 chars, 200
    UTF-8 bytes and 602 serialized.

    The ASCII half is the CONTROL: the same CHARACTER count, uncut. Without it a red here would
    not say which of the two axes did the cutting.
    """
    cyr, ascii_ = {}, {}
    workspace_cmd._add_capped(cyr, "half_applied", ["ф" * 100] * 6)
    workspace_cmd._add_capped(ascii_, "half_applied", ["f" * 100] * 6)

    assert len(cyr["half_applied"]) == 2, "602 serialized bytes each, so two fit in 1568"
    assert cyr["half_applied_truncated"] == 6
    assert len(ascii_["half_applied"]) == 6, "102 serialized bytes each — same chars, no cut"
    assert "half_applied_truncated" not in ascii_


def test_the_entry_cap_still_binds_where_the_byte_budget_does_not(repo):
    """The byte budget is a SECOND floor, not a replacement: on the short realistic names this
    repo's own recipes write (`shot-<id>.png`, `.playwright-mcp/<id>/…`) it never fires at all, and
    the report is byte-identical to what it was before 862 — measured end to end, 50 names on a
    1,376-byte line before AND after."""
    names = [f"shot-{n:04d}.png" for n in range(workspace_cmd._MAX_REPORTED_IGNORED + 7)]
    state = {}
    workspace_cmd._add_capped(state, "overwritten_ignored", names)

    assert len(state["overwritten_ignored"]) == workspace_cmd._MAX_REPORTED_IGNORED
    assert state["overwritten_ignored_truncated"] == len(names)
    assert len(json.dumps(state["overwritten_ignored"])) < \
        workspace_cmd._MAX_REPORTED_IGNORED_BYTES, "so it was the ENTRY cap that cut this one"


def test_the_truncated_count_is_entries_not_the_size_of_the_loss(repo):
    """`removed_ignored_truncated` is `len(destroyed)` — POST-filter, POST-collapse — so it is not
    the size of the loss, and it is not a bound on it in EITHER direction (below it here; above it
    when a printed entry is a symlink, or a directory holding only symlinks, which destroys no
    ignored regular file at all). VMCP-249 (840) settled that by building the state rather than by
    preferring a wording, and this is the state, kept as a pin so the prose above
    `_MAX_REPORTED_IGNORED` cannot drift back.

    What disagreed was 3 shipped texts against 1, not one file against another — and round 1 of
    VMCP-249 (840) wrote 2, undercounting the docstring of the function IMMEDIATELY ABOVE this one
    in this same file. Re-read at `469db93` — round 1's OWN parent, which is where all three sit,
    and NOT this commit's parent, where round 1 had already fixed two of them; round 2 wrote "the
    parent commit" and a reader of the shipped file has no cue to read that as anything but
    `HEAD~1`. There the module comment said TRUE total, SKILL.md
    said the same in Russian, and `test_the_report_is_capped_but_the_count_is_not` said truncation
    must not hide the SIZE of the loss. Those three stood against ONE correct SKILL.md paragraph,
    88 lines earlier than the wrong one and counting from the line that names THIS key, which
    already said the number "inherits exactly the blindness of the key" BY NAME — so the rulebook
    contradicted itself, a cross-reference vouched for a text saying the opposite, and the count of
    disagreeing texts repeated this card's own defect in miniature. Counting the neighbouring key's
    docstring as well makes it 4 against 1. The neighbour is named by POSITION rather than by a line
    distance on purpose, and this pair is why: it has produced THREE numbers, no two of them the
    same measurement. Round 1's 88 for the SKILL.md pair is exact and re-derivable. The review that
    bounced round 1 put this neighbour "40 lines" away, which is not what its own tree said —
    measured at `7339e45`, the two `def` lines are 3447 and 3473, 26 apart. Round 2 then answered
    with "34 lines apart function to function", where the NUMBER is real and the LABEL is not: 34
    is that neighbour's `def` to the PHRASE being corrected, a different pair. And measured at
    `211c766` the two `def` lines ARE 40 apart — the review's number arriving true at a tree it was
    never reading, which is the hazard in one line. Correcting a stale distance with a mislabelled
    one is this card's own defect one size down, so it is retired rather than re-fitted. Position
    cannot go stale; a distance can.

    Three shapes at once: a REPRODUCIBLE ignored directory holding a hand-authored file (100
    files, one git entry, filtered away entirely), a NON-reproducible ignored directory (30 files,
    ONE entry) and 57 loose ignored files. 187 ignored files are destroyed; the key reports 58.

    The OVER-reporting direction is deliberately NOT pinned, and the reason is whose behaviour it
    is: 51 reported entries against zero destroyed regular files is a fact about what git chooses
    to PRINT for a symlink, not about any code here, so a test on it would go red on a git change
    with nothing of ours defective. It is measured in the module comment instead.

    Sweep RE-RUN for round 2, selection = this file, `-q` dropped so `collected` is readable, and
    each round read by counting lines beginning `FAILED ` and lines beginning `ERROR ` separately:
    control 0 failed / 0 errors / 200 collected. Counting the number PRE-filter (`len(ignored)`,
    which is 59 on this input) -> 1 failed / 0 errors / 200 collected, this test ALONE. Making
    `_is_reproducible_ignored` return False always -> 5 failed / 0 errors / 200 collected, this
    test among them. Read that second round as collateral rather than as this pin's own strength:
    four of its five kills belong to the filter's existing pins, and the round is here only
    because the number this test asserts moves 58 -> 59 with the filter gone.

    RE-RUN, not inherited, and the SELECTION SIZE is why: round 1 recorded 192 collected, true when
    it measured and already stale when this round opened, siblings having landed 8 more tests in
    this file in between. So re-measure this block rather than quoting it — what carries meaning is
    the DELTA against a control, and a delta against someone else's control is not one.
    """
    _ignoring(repo, "*.png", ".playwright-mcp/", ".venv/")
    path = Path(ensure_workspace(42, cwd=repo)["path"])
    (path / ".venv").mkdir()
    (path / ".venv" / "MEASUREMENTS.md").write_text("irreplaceable, and it dies unnamed\n")
    for n in range(99):
        (path / ".venv" / f"lib{n}.py").write_text("x = 1\n")
    (path / ".playwright-mcp" / "840").mkdir(parents=True)
    for n in range(30):
        (path / ".playwright-mcp" / "840" / f"page-{n}.yml").write_text("aria snapshot\n")
    for n in range(57):
        (path / f"shot-{n:04d}.png").write_bytes(b"\x89PNG")
    destroyed_files = 100 + 30 + 57

    res = release_workspace(42, cwd=repo)

    assert res["released"] is True
    # 59 git `!!` entries, minus `.venv/` as recognisably regenerable. NOT 187.
    assert res["removed_ignored_truncated"] == 58
    assert res["removed_ignored_truncated"] < destroyed_files, (
        "the count is entries, not files: one entry stands for a whole collapsed directory and a "
        "filtered entry stands for arbitrarily many unnamed ones"
    )
    assert not path.exists()


def test_the_detritus_filter_does_not_cover_what_an_agent_authors(repo):
    """The dangerous direction of that filter, pinned: a name ADDED to it is a class of file this
    module destroys silently again. These two are named in the card and must stay out of it."""
    _ignoring(repo, ".vikunja-mcp.env", ".playwright-mcp/", ".venv/")
    path = Path(ensure_workspace(42, cwd=repo)["path"])
    (path / ".vikunja-mcp.env").write_text("VIKUNJA_TOKEN=irrecoverable\n")
    (path / ".playwright-mcp").mkdir()
    (path / ".playwright-mcp" / "page.yml").write_text("snapshot\n")
    (path / ".venv").mkdir()                                  # routine, and must not mask them
    (path / ".venv" / "pyvenv.cfg").write_text("home = /usr\n")

    res = release_workspace(42, cwd=repo)

    assert sorted(res["removed_ignored"]) == [".playwright-mcp/", ".vikunja-mcp.env"]


def test_a_review_tree_reports_its_ignored_payload_too(repo):
    """The role SKILL.md sends down a different code path (detached, `unreachable-head` instead of
    the branch guard) — and the role that is TOLD to take screenshots of somebody else's work."""
    _ignoring(repo, "*.png")
    head = _git(repo, "rev-parse", "HEAD")
    path = Path(ensure_workspace(42, role="review", at=head, cwd=repo)["path"])
    (path / "shot-42.png").write_bytes(b"\x89PNG reviewer evidence")

    res = release_workspace(42, role="review", cwd=repo)

    assert res["released"] is True and res["branch"] is None
    assert res["removed_ignored"] == ["shot-42.png"]
    assert not path.exists()


def test_the_ignored_inventory_leaves_the_dirty_guard_byte_for_byte(repo):
    """`--ignored` only ADDS `!! ` lines — the guard must keep counting the OTHER ones only.

    Its count is user-visible in the refusal text, so a tree with 1 real entry and 2 ignored ones
    must still say `1 entries`, not 3. And a refused tree removes nothing, so it names nothing.
    """
    _ignoring(repo, "*.png", ".venv/")
    path = Path(ensure_workspace(42, cwd=repo)["path"])
    (path / "real-work.txt").write_text("uncommitted, NOT ignored\n")
    (path / "shot-42.png").write_bytes(b"\x89PNG")
    (path / ".venv").mkdir()
    (path / ".venv" / "pyvenv.cfg").write_text("home = /usr\n")

    res = release_workspace(42, cwd=repo)

    assert res["released"] is False and res["code"] == workspace_cmd.CODE_DIRTY
    assert res["reason"] == "working tree is dirty (1 entries)", res["reason"]
    assert "removed_ignored" not in res
    assert path.exists() and (path / "shot-42.png").exists()


def test_gc_names_the_ignored_payload_it_destroys(repo, tracker):
    """The unattended path — the one that runs every tick with nobody watching."""
    _ignoring(repo, "secrets.env", "scratch/")
    api, wf = tracker
    path = Path(ensure_workspace(42, cwd=repo)["path"])        # nothing on the board -> dead
    (path / "secrets.env").write_text("TOKEN=irrecoverable\n")
    (path / "scratch").mkdir()
    (path / "scratch" / "notes.txt").write_text("measurements not written up yet\n")
    _quiesce(path)

    res = gc_workspaces(cwd=repo, workflow=wf)

    assert res["kept"] == [] and res["expected"] == []
    assert [r["task_id"] for r in res["released"]] == [42]
    assert sorted(res["released"][0]["removed_ignored"]) == ["scratch/", "secrets.env"]
    assert not path.exists()


def test_gc_still_reaps_a_tree_that_holds_only_build_detritus(repo, tracker):
    """THE anti-paralysis pin. The failure mode of "fixing" this by holding on any ignored path is
    that `--gc` stops reaping anything at all — every build tree carries `.venv/` — trees pile up,
    and the next human turns the guard off. Dead tree, detritus only: reaped, and quietly."""
    _ignoring(repo, ".venv/", ".ruff_cache/", ".pytest_cache/", "__pycache__/")
    api, wf = tracker
    path = Path(ensure_workspace(42, cwd=repo)["path"])
    for name in (".venv", ".ruff_cache", ".pytest_cache", "__pycache__"):
        (path / name).mkdir()
        (path / name / "marker").write_text("x\n")
    _quiesce(path)

    res = gc_workspaces(cwd=repo, workflow=wf)

    assert [r["task_id"] for r in res["released"]] == [42]
    assert "removed_ignored" not in res["released"][0]
    assert res["kept"] == [] and res["expected"] == []
    assert not path.exists()


# --- VMCP-223 (766): one git setting used to switch the dirty guard off entirely ---------------

def test_the_dirty_guard_survives_status_showUntrackedFiles_no(repo):
    """A performance knob must not be able to destroy an agent's uncommitted work.

    MEASURED before the fix, on this very fixture shape (real bare origin, real worktree):
    with `git config status.showUntrackedFiles no`, `git status --porcelain --ignored` returned
    the EMPTY STRING for a tree holding an untracked-and-NOT-ignored `REAL-WORK.txt` — neither
    `??` nor `!!` lines survive that setting — so the dirty guard passed, `release_workspace`
    answered `{"released": true}` with no `code`, no `warning`, no `removed_ignored`, and the
    file was gone. That is this module's stated invariant failing whole: "push OK -> remove,
    push FAIL -> KEEP … housekeeping is never how an agent's work disappears". `--gc` does it
    unattended on every tick.

    The setting is reachable from ANY config level and a linked worktree shares `.git/config`
    with the main checkout, so an agent cannot rule it out by looking at its own tree.

    This is NOT VMCP-221 (764), which asked whether `dirty` should be WIDENED to hold a tree for
    IGNORED files — no longer an open question either way: a human answered it, report and never
    hold, and the accepted price is recorded beside the filter in `workspace_cmd.py`. The guard
    already claimed untracked-and-not-ignored; the knob was taking that claim away. The sibling
    test below is the other half of the same fix.

    MUTATION-CHECKED, selection `tests/unit/test_workspace_cmd.py`, `__pycache__` deleted and then
    PYTHONDONTWRITEBYTECODE=1, each round restored from a byte copy and the file confirmed
    sha256-identical; the script refuses unless the call matches exactly once. Control round:
    0 failed.
      * drop the `-c` prefix (the pre-#766 call) -> 2 failed, this test and the `removed_ignored`
        sibling — i.e. the two halves the setting silenced, and nothing else
      * a plausible HALF-fix — force the setting but lose `--ignored` -> 7 failed, which is what
        says the two flags are not interchangeable: the override restores `??`, `--ignored`
        restores `!!`, and #766 needed both
    """
    _git(repo, "config", "status.showUntrackedFiles", "no")
    path = Path(ensure_workspace(766, cwd=repo)["path"])
    (path / "REAL-WORK.txt").write_text("unsaved work\n")

    assert _git(path, "status", "--porcelain", "--ignored") == "", (
        "the premise of this test has evaporated: plain `git status` now sees the file under "
        "showUntrackedFiles=no, so this fixture no longer reproduces what 766 measured"
    )
    res = release_workspace(766, cwd=repo)
    assert res["released"] is False and res["code"] == workspace_cmd.CODE_DIRTY, res
    assert (path / "REAL-WORK.txt").read_text() == "unsaved work\n"


def test_removed_ignored_also_survives_status_showUntrackedFiles_no(repo):
    """The same setting silenced `!!` too, so #710's post-mortem list went quiet with the guard.
    A clean, fully-pushed tree still releases — the knob does not paralyse whoever set it — but
    what it destroyed is named."""
    _git(repo, "config", "status.showUntrackedFiles", "no")
    path = Path(ensure_workspace(767, cwd=repo)["path"])
    (repo / ".gitignore").write_text("shot-*.png\n")
    _git(repo, "add", ".gitignore")
    _git(repo, "commit", "-m", "ignore shots")
    _git(repo, "push", "origin", "main")
    _git(path, "fetch", "origin")
    _git(path, "rebase", "origin/main")
    (path / "shot-767.png").write_text("PNG\n")

    res = release_workspace(767, cwd=repo)
    assert res["released"] is True, res
    assert res.get("removed_ignored") == ["shot-767.png"], res


def test_the_override_changes_nothing_at_the_default_setting(repo):
    """The control for the two above, and the reason this is a fix rather than a scope change:
    with the setting at its default the forced `-c` alters no verdict. Same tree, same code, same
    refusal — so no tree that used to be released is held now."""
    path = Path(ensure_workspace(768, cwd=repo)["path"])
    (path / "REAL-WORK.txt").write_text("unsaved work\n")
    res = release_workspace(768, cwd=repo)
    assert res["released"] is False and res["code"] == workspace_cmd.CODE_DIRTY, res

    clean = Path(ensure_workspace(769, cwd=repo)["path"])
    assert release_workspace(769, cwd=repo)["released"] is True
    assert not clean.exists()


# --- VMCP-238 (801): the MAIN checkout is fast-forwarded by --gc, or refused and reported ---
#
# The defect these pin is not a failure anywhere: every task lands from its own worktree with
# `git push origin HEAD:main`, which moves the shared `refs/remotes/origin/<base>` and never the
# local branch the main checkout sits on, so the folder a human works in falls behind forever.
# Measured on this repo the day the card was written: 58 commits over one session.
#
# Every test below drives REAL git — a real bare origin, a real sibling clone landing real
# commits — for the same reason the file's header gives: the one property that matters is that
# housekeeping cannot destroy a human's uncommitted work, and a fake would only mirror this
# module's own beliefs about `merge --ff-only`.

def _land_on_origin(tmp_path: Path, name: str, files: dict, force: bool = False) -> str:
    """Land a commit on the bare origin the way a SIBLING worktree does — from a clone of its
    own, so the main checkout's branch is never touched and its `origin/<base>` goes stale
    exactly as it does in production.

    `force` is `git add -f`, needed only when the incoming path is one the SIBLING's clone also
    ignores — which is the shape VMCP-240 (806) is about, and the harder of its two routes."""
    other = tmp_path / f"sibling-{name}"
    subprocess.run(["git", "clone", str(tmp_path / "origin.git"), str(other)],
                   check=True, capture_output=True)
    _git(other, "config", "user.email", "sibling@example.com")
    _git(other, "config", "user.name", "Sibling")
    for rel, content in files.items():
        (other / rel).parent.mkdir(parents=True, exist_ok=True)
        (other / rel).write_text(content)
    _git(other, "add", *(["-f"] if force else []), "-A")
    _git(other, "commit", "-m", f"sibling: {name}")
    _git(other, "push", "origin", "HEAD:main")
    return _git(other, "rev-parse", "HEAD")


def test_gc_fast_forwards_a_stale_main_checkout(repo, tracker, tmp_path):
    """The card's own scenario, reproduced: work lands from elsewhere, the main checkout does not
    move, and the next sweep brings it up to date and says by how much."""
    _api, wf = tracker
    before = _git(repo, "rev-parse", "HEAD")
    landed = _land_on_origin(tmp_path, "one", {"other.txt": "a\n"})
    landed2 = _land_on_origin(tmp_path, "two", {"other.txt": "b\n"})
    assert _git(repo, "rev-parse", "HEAD") == before, "the drift is real before we sweep"

    res = gc_workspaces(cwd=repo, workflow=wf)

    state = res["main_checkout"]
    assert state["updated"] is True, state
    assert state["from"] == before and state["to"] == landed2
    assert state["commits"] == 2, state
    assert state["branch"] == "main"
    assert _git(repo, "rev-parse", "HEAD") == landed2
    assert (repo / "other.txt").read_text() == "b\n"
    assert landed != landed2                      # both really landed, in order


def test_gc_says_nothing_about_a_main_checkout_that_is_already_current(repo, tracker):
    """ABSENT means "nothing for you to do". The key exists to be READ, and a field present on
    every tick is the signal VMCP-68 had to split `kept` in two to rescue."""
    _api, wf = tracker
    res = gc_workspaces(cwd=repo, workflow=wf)
    assert "main_checkout" not in res, res
    assert set(res) == {"released", "kept", "expected"}


def test_the_fast_forward_leaves_an_untracked_human_file_alone(repo, tracker, tmp_path):
    """The live case this card was filed from: the stale checkout held the human's own untracked
    `BOARD-ANALYSIS-2026-08-03.md`. A guard of ours that demanded a CLEAN tree would refuse here
    — i.e. would never fire in the one situation it was written for."""
    _api, wf = tracker
    (repo / "BOARD-ANALYSIS.md").write_text("the human's own working document\n")
    landed = _land_on_origin(tmp_path, "one", {"other.txt": "a\n"})

    res = gc_workspaces(cwd=repo, workflow=wf)

    assert res["main_checkout"]["updated"] is True, res["main_checkout"]
    assert _git(repo, "rev-parse", "HEAD") == landed
    assert (repo / "BOARD-ANALYSIS.md").read_text() == "the human's own working document\n"


def test_uncommitted_work_in_an_untouched_file_survives_the_fast_forward(repo, tracker, tmp_path):
    """The other half of the same property: a human mid-edit in a file the incoming commits do
    not touch keeps their edit AND gets the update."""
    _api, wf = tracker
    (repo / "README.md").write_text("hi\nHUMAN EDIT IN FLIGHT\n")
    landed = _land_on_origin(tmp_path, "one", {"other.txt": "a\n"})

    res = gc_workspaces(cwd=repo, workflow=wf)

    assert res["main_checkout"]["updated"] is True
    assert _git(repo, "rev-parse", "HEAD") == landed
    assert (repo / "README.md").read_text() == "hi\nHUMAN EDIT IN FLIGHT\n"


def test_the_fast_forward_refuses_rather_than_overwriting_uncommitted_work(repo, tracker,
                                                                          tmp_path):
    """THE invariant. The incoming commit touches the very file the human is editing, so the
    update must not happen at all — refused, reported, and this file untouched. git is what
    enforces this, which is why the ladder ends in `merge --ff-only` and never in a reset.

    IT USED TO ASSERT THE PHRASE "NOTHING was discarded" AND THAT PHRASE IS GONE — VMCP-244 (835),
    because `merge --ff-only` is not atomic and the sentence was therefore false on a DIFFERENT
    input than this one (see the 835 section at the end of this file). This input is the up-front
    refusal, where git checks before writing and really does write nothing; what changed is that
    the branch now says what it FOUND instead of asserting what it never checked. The ground truth
    below is what this test was always for, and none of it moved."""
    _api, wf = tracker
    before = _git(repo, "rev-parse", "HEAD")
    (repo / "README.md").write_text("hi\nWORK THE HUMAN HAS NOT COMMITTED\n")
    _land_on_origin(tmp_path, "one", {"README.md": "landed from a sibling\n"})

    res = gc_workspaces(cwd=repo, workflow=wf)

    state = res["main_checkout"]
    assert state["updated"] is False and state["code"] == workspace_cmd.MAIN_SYNC_BLOCKED, state
    assert "nothing half-written was found" in state["reason"], state["reason"]
    assert _git(repo, "rev-parse", "HEAD") == before, "the checkout must not have moved"
    assert (repo / "README.md").read_text() == "hi\nWORK THE HUMAN HAS NOT COMMITTED\n"


def test_gc_refuses_a_main_checkout_that_has_diverged(repo, tracker, tmp_path):
    """A local commit the remote does not have. A fast-forward would discard it, so there is no
    fast-forward — and the refusal names the count rather than quoting git."""
    _api, wf = tracker
    (repo / "local-only.txt").write_text("a human's unpushed commit\n")
    _git(repo, "add", "local-only.txt")
    _git(repo, "commit", "-m", "local work")
    mine = _git(repo, "rev-parse", "HEAD")
    _land_on_origin(tmp_path, "one", {"other.txt": "a\n"})

    res = gc_workspaces(cwd=repo, workflow=wf)

    state = res["main_checkout"]
    assert state["updated"] is False and state["code"] == workspace_cmd.MAIN_SYNC_DIVERGED, state
    assert "1 local commit" in state["reason"], state
    assert _git(repo, "rev-parse", "HEAD") == mine
    assert (repo / "local-only.txt").exists()


def test_gc_leaves_a_main_checkout_on_another_branch_alone(repo, tracker, tmp_path):
    """Switching branches under someone who is working is not housekeeping. Refused by NAME, so
    the reason can say that, instead of leaving a reader to infer it from a git error."""
    _api, wf = tracker
    _git(repo, "switch", "-c", "human-debugging")
    mine = _git(repo, "rev-parse", "HEAD")
    _land_on_origin(tmp_path, "one", {"other.txt": "a\n"})

    res = gc_workspaces(cwd=repo, workflow=wf)

    state = res["main_checkout"]
    assert state["updated"] is False and state["code"] == workspace_cmd.MAIN_SYNC_OFF_BRANCH
    assert state["branch"] == "human-debugging", state
    assert _git(repo, "rev-parse", "HEAD") == mine
    assert _git(repo, "rev-parse", "--abbrev-ref", "HEAD") == "human-debugging"


def test_gc_leaves_a_detached_main_checkout_alone(repo, tracker, tmp_path):
    """Detached HEAD has no branch to fast-forward. Named separately from `off-branch` because
    the fix a human applies differs, and the reason spells that fix out."""
    _api, wf = tracker
    mine = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "--detach", "HEAD")
    _land_on_origin(tmp_path, "one", {"other.txt": "a\n"})

    res = gc_workspaces(cwd=repo, workflow=wf)

    state = res["main_checkout"]
    assert state["updated"] is False and state["code"] == workspace_cmd.MAIN_SYNC_DETACHED
    assert "switch main" in state["reason"], state
    assert _git(repo, "rev-parse", "HEAD") == mine


def test_the_main_checkout_sync_can_be_opted_out_of(repo, tracker, tmp_path, monkeypatch):
    """Same escape hatch as VIKUNJA_MCP_NO_SKILL_SYNC / VIKUNJA_MCP_NO_TRACE, and silent when
    set: whoever set it does not want to hear about the checkout every tick either."""
    _api, wf = tracker
    before = _git(repo, "rev-parse", "HEAD")
    _land_on_origin(tmp_path, "one", {"other.txt": "a\n"})
    monkeypatch.setenv(workspace_cmd._MAIN_SYNC_OPT_OUT, "1")

    res = gc_workspaces(cwd=repo, workflow=wf)

    assert "main_checkout" not in res, res
    assert _git(repo, "rev-parse", "HEAD") == before


def test_a_failing_main_sync_never_costs_the_sweep_its_verdicts(repo, tracker, monkeypatch):
    """Best-effort in the same sense as the epic marker and the Your Call ping: the reaper must
    not acquire a new way to fail. The tree is still reaped and the failure is still reported."""
    _api, wf = tracker
    path = Path(ensure_workspace(42, cwd=repo)["path"])
    _quiesce(path)

    def boom(_root):
        raise RuntimeError("git fell over")
    monkeypatch.setattr(workspace_cmd, "sync_main_checkout", boom)

    res = gc_workspaces(cwd=repo, workflow=wf)

    assert [r["task_id"] for r in res["released"]] == [42], res
    assert not path.exists()
    assert res["main_checkout"]["code"] == workspace_cmd.MAIN_SYNC_ERROR
    assert "git fell over" in res["main_checkout"]["reason"]


def test_main_sync_codes_are_not_part_of_the_graded_worktree_vocabulary(repo, tracker, tmp_path):
    """The boundary the module's comment claims, asserted instead of promised.

    `CODE_*` is a CLOSED enumeration of per-WORKTREE refusals that `_keep_is_expected` grades
    cell by cell, pinned three separate ways — so a new member there is supposed to redden those
    pins. The main-checkout codes are a different vocabulary: they describe the SHARED checkout,
    they ride in their own key, and they must never reach the grader. Rename them into `CODE_*`
    and the three grid pins go red for a code that has no cell to sit in; leave them apart and
    this test is what keeps the promise honest.

    Both halves are asserted, because the names alone would not catch a colliding VALUE."""
    declared_codes = {n: v for n, v in vars(workspace_cmd).items()
                      if n.startswith("CODE_") and isinstance(v, str)}
    declared_main = {n: v for n, v in vars(workspace_cmd).items()
                     if n.startswith("MAIN_SYNC_") and isinstance(v, str)}
    assert len(declared_main) >= 7, sorted(declared_main)
    assert not set(declared_codes) & set(declared_main)
    assert not set(declared_codes.values()) & set(declared_main.values()), (
        "a main-checkout code now shares a VALUE with a worktree refusal code — the grader keys "
        "on values, so this is exactly the collision the separate prefix exists to prevent"
    )

    # ...and structurally: a sweep in which BOTH a tree is kept and the sync refuses must keep
    # the two apart.
    _api, wf = tracker
    path = Path(ensure_workspace(42, cwd=repo)["path"])
    (path / "UNSAVED.txt").write_text("work\n")             # -> kept: dirty
    _quiesce(path)
    (repo / "README.md").write_text("hi\nhuman edit\n")     # -> main_checkout: blocked
    _land_on_origin(tmp_path, "one", {"README.md": "from a sibling\n"})

    res = gc_workspaces(cwd=repo, workflow=wf)

    assert [e["code"] for e in res["kept"]] == [workspace_cmd.CODE_DIRTY], res["kept"]
    assert res["main_checkout"]["code"] == workspace_cmd.MAIN_SYNC_BLOCKED
    for entry in res["kept"] + res["expected"] + res["released"]:
        assert entry.get("code") not in set(declared_main.values()), entry


def test_the_cli_gc_line_carries_the_main_checkout_key(repo, tracker, tmp_path, monkeypatch,
                                                       capsys):
    """The payload is a cross-process contract — the pump reads the JSON LINE, not the dict — so
    the key has to survive the CLI, and the ABSENT case has to stay absent there too."""
    _api, wf = tracker
    monkeypatch.setattr(workspace_cmd, "_build_workflow", lambda root: (wf, None))
    monkeypatch.chdir(repo)

    assert run_workspace(["--gc"]) == 0
    assert "main_checkout" not in json.loads(capsys.readouterr().out)

    landed = _land_on_origin(tmp_path, "one", {"other.txt": "a\n"})
    assert run_workspace(["--gc"]) == 0
    state = json.loads(capsys.readouterr().out)["main_checkout"]
    assert state["updated"] is True and state["to"] == landed


def test_the_merge_stays_ff_only_even_when_the_ancestor_check_lies(repo, tracker, tmp_path,
                                                                  monkeypatch):
    """`--ff-only` is DEFENCE IN DEPTH, and this is the only input that can show it.

    For every ordinary input the `merge-base --is-ancestor` guard above has already excluded
    every non-fast-forward, so dropping the flag changes NOTHING and no test built from an
    honest input can catch it: measured on this section's own selection WITHOUT this test,
    control 0 failed; `--ff-only` dropped 0 failed. What the flag is actually for is the WINDOW
    between that check and the merge — a human committing in their own checkout, or a sibling
    landing, in those milliseconds. From inside the function that race is indistinguishable
    from the check simply being wrong, so that is how it is built here: force the check to
    answer yes on a checkout that has genuinely diverged.

    Without the flag git would merrily MERGE — inventing a merge commit in a human's working
    directory, which is precisely the class of action this module refuses to take. With it, git
    refuses and the checkout is untouched."""
    _api, wf = tracker
    (repo / "local-only.txt").write_text("a human's unpushed commit\n")
    _git(repo, "add", "local-only.txt")
    _git(repo, "commit", "-m", "local work")
    mine = _git(repo, "rev-parse", "HEAD")
    _land_on_origin(tmp_path, "one", {"other.txt": "a\n"})

    real_git_ok = workspace_cmd._git_ok
    monkeypatch.setattr(workspace_cmd, "_git_ok",
                        lambda *a, **k: True if a[:2] == ("merge-base", "--is-ancestor")
                        else real_git_ok(*a, **k))

    res = gc_workspaces(cwd=repo, workflow=wf)

    state = res["main_checkout"]
    assert state["updated"] is False and state["code"] == workspace_cmd.MAIN_SYNC_BLOCKED, state
    assert _git(repo, "rev-parse", "HEAD") == mine, "the checkout must not have moved"
    assert _git(repo, "rev-list", "--count", "--merges", "HEAD") == "0", (
        "a merge commit was invented in the human's checkout — `--ff-only` is what forbids that"
    )
    assert (repo / "local-only.txt").exists()


# --- VMCP-240 (806): the ff DOES destroy an IGNORED file, and now says which one ---
#
# Filed by the independent review of 801. `merge --ff-only` protects a modified TRACKED file and
# an untracked one that is NOT ignored; an IGNORED file is untracked and is overwritten silently
# — rc=0, `git status --porcelain` empty before and after. Same blind spot as VMCP-185 (710), one
# layer up: not the worktree reaper this time but the shared checkout.
#
# These tests do NOT change the outcome, and that is deliberate: the loss is git's own behaviour
# (a human's `git pull --ff-only` does it too) and refusing on ignored paths would stop the sync
# from ever firing in a repo whose rulebook tells agents to write `shot-<id>.png`. What they pin
# is that the loss stopped being INVISIBLE — and, on the refusal branch, that no such key appears,
# because there nothing died.
#
# MUTATION SWEEP over this whole file, one selection throughout (`collected 157` in every round,
# so no round measured a different selection), control 0 failed:
#   * drop `GIT_OPTIONAL_LOCKS=0` from the probe's diff ......... 0 failed  <- see the note on
#   * report every ignored path (drop the regenerable filter) ... 1 failed     the index test
#   * drop the cap slice ....................................... 1 failed
#   * emit the key unconditionally ............................. 3 failed
#   * drop the try/except around the probe ..................... 1 failed
#   * drop the exists-on-disk filter ........................... 1 failed
#   * run the probe AFTER the merge instead of before .......... 5 failed
#   * drop `--no-renames` ...................................... 1 failed
#   * drop `--diff-filter=ACMT` ................................ 0 failed
#   * grade check-ignore's rc as `!= 0` instead of `not in (0,1)` 0 failed
# The three zeros are all DECLARED as pinning nothing where they live, rather than discovered
# here and left unsaid: the env var is belt over a call shape that never writes (the index test
# says so in full), `--diff-filter=ACMT`'s exclusion of `D` cannot produce a report because a
# deleted path is tracked locally and `check-ignore` drops it anyway, and the rc grade computes
# the same empty list either way because a non-zero exit leaves stdout empty.
#
# ROUND TWO, after the independent second pass forced the directory expansion. Same file, same
# `-p no:randomly`, `collected 160` in every round, control 0 failed:
#   * no directory expansion at all ............................ 2 failed
#   * expansion follows symlinked directories .................. 1 failed
#   * an EMPTY directory returns nothing instead of its own name  0 failed
#   * an un-suppressed `git status --porcelain` inside the probe  1 failed
# The zero is a defensive branch nothing constructs here (an incoming path that is a local EMPTY
# directory), left in and named rather than deleted, because its failure mode is a caller reading
# "no paths at risk" for a path that exists. The last round is the second pass's own M13, replayed
# on this tree: it is what says the index test has teeth against index WRITES even though it does
# not pin the env var — a distinction that took a mutation in each direction to establish.
#
# ROUND THREE, VMCP-240 (806) round two, after the card's independent reviewer disproved "the ONE
# channel that has no path in the diff at all" and `_doomed_ancestor` was added for the shape it
# built. Same file, same `-p no:randomly`, no `-q`; every round read by COUNTING `FAILED `- and
# `ERROR `-prefixed lines separately, with `collected` cross-checked against the control's;
# `collected 170` and `0 errors` in every round including the control, control 0 failed:
#   * no ancestor walk at all ................................... control 0 failed; 8 failed
#   * ask the walk ONLY when `lexists` says ABSENT ............... control 0 failed; 1 failed
#   * `isdir` without `islink` first ............................ control 0 failed; 4 failed
#   * the first-draft walk: bottom-up AND stop at the first
#     ancestor that exists ...................................... control 0 failed; 2 failed
#   * ORDER only: bottom-up, still walking through .............. control 0 failed; 1 failed
#   * STOP only: top-down, but a real directory ends the walk .... control 0 failed; 1 failed
#   * report EVERY existing ancestor, real directories included .. control 0 failed; 3 failed
#   * drop the de-duplication ................................... control 0 failed; 1 failed
# NO ZEROS THIS ROUND, which is the first time in this file, and it took two discarded sweeps to
# get there — both worth knowing about, because each is a way to be fooled rather than a mishap.
# The FIRST recorded `bottom-up 0 failed` and would have shipped "top-down is pinned" as a lie:
# that round flipped only the RANGE, and by then the walk had already stopped returning None on a
# real directory, so the two halves of one edit had to be mutated SEPARATELY before either could
# be measured (rounds four through six above are that split, and the grid in `_doomed_ancestor`
# says which shape owns which). The SECOND was killed mid-round by an infrastructure failure, and
# its restore did not run: the mutant sat in the tree looking exactly like ordinary uncommitted
# work — `git status` cannot tell them apart — and was caught only by grepping for the mutant
# text. The sweep script now restores from a SIGTERM/SIGINT handler and re-checks the file's
# sha256 at the end. That is the same "concurrent writer" failure SKILL.md describes, with the
# sweep and its own killer as the two writers.


def test_the_fast_forward_names_the_ignored_file_it_destroys(repo, tracker, tmp_path):
    """ROUTE ONE, and the whole finding in one input: this repo ignores `*.png`, its own SKILL.md
    tells agents to write `shot-<id>.png`, and upstream force-added a file at that path.

    Both halves are asserted, because naming is not saving: the human's bytes really are gone
    (that is git, and this module does not fight it) and the report is now the only trace there
    ever was of them.

    `ghost.png` rides along to pin the EXISTENCE filter, and it needs to be here rather than in a
    test of its own: `check-ignore` answers about a NAME, not about a file (measured — it reports
    `ghost.png` in a checkout that has no such path), so without the `lexists` step every ignored
    path an incoming commit merely ADDS would be reported as destroyed."""
    _api, wf = tracker
    _ignoring(repo, "*.png")
    (repo / "shot.png").write_bytes(b"\x89PNG the human's own evidence screenshot")
    assert _git(repo, "status", "--porcelain") == "", "the whole problem: git sees nothing here"
    landed = _land_on_origin(tmp_path, "one",
                             {"shot.png": "UPSTREAM\n", "ghost.png": "no local file\n"},
                             force=True)

    res = gc_workspaces(cwd=repo, workflow=wf)

    state = res["main_checkout"]
    assert state["updated"] is True, state
    assert state["overwritten_ignored"] == ["shot.png"], state
    assert "overwritten_ignored_truncated" not in state
    assert _git(repo, "rev-parse", "HEAD") == landed
    assert (repo / "shot.png").read_text() == "UPSTREAM\n", "the human's file really is gone"


def test_a_locally_uncommitted_ignore_rule_is_enough_to_lose_the_file(repo, tracker, tmp_path):
    """ROUTE TWO, and the more reachable one: NO upstream `git add -f` is needed. A rule the human
    typed into their own `.gitignore` and never committed, plus an ordinary incoming file at that
    path, and the same silent overwrite follows.

    It is also what forces the probe to ask `git check-ignore` (which reads the WORKING TREE's
    rules) rather than anything derived from the committed tree."""
    _api, wf = tracker
    (repo / ".gitignore").write_text("notes.txt\n")          # uncommitted, deliberately
    (repo / "notes.txt").write_text("the human's scratch notes\n")
    landed = _land_on_origin(tmp_path, "one", {"notes.txt": "UPSTREAM\n"})

    res = gc_workspaces(cwd=repo, workflow=wf)

    state = res["main_checkout"]
    assert state["updated"] is True, state
    assert state["overwritten_ignored"] == ["notes.txt"], state
    assert _git(repo, "rev-parse", "HEAD") == landed
    assert (repo / "notes.txt").read_text() == "UPSTREAM\n"


def test_the_untracked_but_not_ignored_contrast_refuses_and_reports_no_loss(repo, tracker,
                                                                            tmp_path):
    """THE CONTRAST that makes the two above a finding rather than a complaint about git: the same
    input with the ignore rule REMOVED is refused outright, and the human's file survives.

    So the new key must be ABSENT here. `blocked` already means "nothing was discarded", and a
    post-mortem field on a branch where nothing died would make that sentence unreadable."""
    _api, wf = tracker
    before = _git(repo, "rev-parse", "HEAD")
    (repo / "notes.txt").write_text("the human's scratch notes\n")
    _land_on_origin(tmp_path, "one", {"notes.txt": "UPSTREAM\n"})

    res = gc_workspaces(cwd=repo, workflow=wf)

    state = res["main_checkout"]
    assert state["updated"] is False and state["code"] == workspace_cmd.MAIN_SYNC_BLOCKED, state
    assert "overwritten_ignored" not in state, state
    assert "untracked working tree files would be overwritten" in state["reason"]
    assert _git(repo, "rev-parse", "HEAD") == before
    assert (repo / "notes.txt").read_text() == "the human's scratch notes\n"


def test_an_ordinary_fast_forward_says_nothing_about_ignored_files(repo, tracker, tmp_path):
    """ABSENT is the ordinary answer, and it has to stay ordinary. A checkout holding an ignored
    file the incoming commits do not touch loses nothing, so there is nothing to report — the
    probe asks for the INTERSECTION of what is arriving with what is on disk, never whether the
    tree holds ignored paths at all."""
    _api, wf = tracker
    _ignoring(repo, "*.png")
    (repo / "shot.png").write_bytes(b"\x89PNG untouched by anything landing")
    _land_on_origin(tmp_path, "one", {"other.txt": "a\n"})

    res = gc_workspaces(cwd=repo, workflow=wf)

    assert res["main_checkout"]["updated"] is True
    assert "overwritten_ignored" not in res["main_checkout"], res["main_checkout"]
    assert (repo / "shot.png").read_bytes() == b"\x89PNG untouched by anything landing"


def test_a_path_inside_an_ignored_directory_is_named_in_full(repo, tracker, tmp_path):
    """FINER-GRAINED than `removed_ignored`, and that is measured rather than claimed: VMCP-185's
    report reads `git status --ignored`, which collapses an ignored DIRECTORY into ONE entry, so a
    file inside `.playwright-mcp/` dies unnamed there. `git check-ignore` answers per PATH, so
    here the individual file is named — the one place this report is better than its sibling."""
    _api, wf = tracker
    _ignoring(repo, ".playwright-mcp/")
    (repo / ".playwright-mcp" / "806").mkdir(parents=True)
    (repo / ".playwright-mcp" / "806" / "page.yml").write_text("the human's aria snapshot\n")
    _land_on_origin(tmp_path, "one", {".playwright-mcp/806/page.yml": "UPSTREAM\n"}, force=True)

    res = gc_workspaces(cwd=repo, workflow=wf)

    state = res["main_checkout"]
    assert state["overwritten_ignored"] == [".playwright-mcp/806/page.yml"], state


def test_a_path_RENAMED_into_an_ignored_name_is_still_seen(repo, tracker, tmp_path):
    """`--no-renames` is load-bearing BESIDE `--diff-filter=ACMT`, and only this input shows it.

    Measured on real git: a rename is status `R`, `ACMT` excludes `R`, so with detection left ON
    the pair reports NOTHING for the commit below — `git diff --name-only --diff-filter=ACMT`
    over a pure rename prints an empty list, while the same command with `--no-renames` prints
    the destination (the rename having become an add plus a delete). So the flag is what keeps a
    file renamed INTO an ignored path from being written over unannounced."""
    _api, wf = tracker
    _ignoring(repo, "/notes.txt")          # anchored: an unanchored rule would hide the SOURCE
    (repo / "docs").mkdir()
    (repo / "docs" / "notes.txt").write_text("a\nb\nc\nd\ne\nf\ng\nh\n")   # long enough to detect
    _git(repo, "add", "docs")
    _git(repo, "commit", "-m", "notes live under docs/")
    _git(repo, "push", "origin", "main")
    (repo / "notes.txt").write_text("the human's own scratch notes\n")

    other = tmp_path / "sibling-rename"
    subprocess.run(["git", "clone", str(tmp_path / "origin.git"), str(other)],
                   check=True, capture_output=True)
    _git(other, "config", "user.email", "sibling@example.com")
    _git(other, "config", "user.name", "Sibling")
    _git(other, "mv", "-f", "docs/notes.txt", "notes.txt")
    _git(other, "commit", "-m", "sibling: notes move to the root")
    _git(other, "push", "origin", "HEAD:main")
    assert _git(other, "diff", "--name-status", "HEAD~1..HEAD").startswith("R"), (
        "the input is only interesting while git DETECTS this as a rename"
    )

    res = gc_workspaces(cwd=repo, workflow=wf)

    state = res["main_checkout"]
    assert state["updated"] is True, state
    assert state["overwritten_ignored"] == ["notes.txt"], state


def test_a_DIRECTORY_replaced_upstream_by_a_file_names_what_died_inside_it(repo, tracker,
                                                                            tmp_path):
    """A CHANNEL WITH NO PATH IN THE DIFF — the FIRST of two now measured, built by this card's
    independent second pass and the reason `_expand_if_directory` exists.

    "The one channel" is what this docstring and CLAUDE.md both said for one shipped round, and
    the card's independent reviewer disproved it with the mirror input; that one is next door, in
    `test_a_local_ignored_FILE_whose_name_upstream_turns_into_a_DIRECTORY_is_named`. Two is a
    count of what has been BUILT, not of what exists.

    Upstream turns the tracked directory `out/` into a FILE; the checkout holds its own ignored
    `out/shot.png`. The incoming diff names `out` (added) and `out/bar.txt` (deleted, so filtered)
    — the path that actually dies is in NEITHER, and asking `check-ignore` about `out` answers
    "not ignored", because a directory is not what the rule matches. Measured before the fix: the
    fast-forward returned rc=0, the file was gone, and `overwritten_ignored` was ABSENT. It also
    refutes the sentence this feature's docstring used to carry — git removes an untracked file
    here, as long as it is ignored.

    The contrast holds one level up too, and the second pass measured it: put an untracked-and-NOT
    -ignored file in that directory and git refuses the whole merge (`Updating the following
    directories would lose untracked files in them`)."""
    _api, wf = tracker
    _ignoring(repo, "*.png")
    (repo / "out").mkdir()
    (repo / "out" / "bar.txt").write_text("tracked, so `out/` is a real directory upstream\n")
    _git(repo, "add", "out")
    _git(repo, "commit", "-m", "a directory that upstream will replace")
    _git(repo, "push", "origin", "main")
    (repo / "out" / "shot.png").write_bytes(b"\x89PNG the human's own evidence")
    assert _git(repo, "status", "--porcelain") == "", "invisible before, as it always was"

    other = tmp_path / "sibling-dir2file"
    subprocess.run(["git", "clone", str(tmp_path / "origin.git"), str(other)],
                   check=True, capture_output=True)
    _git(other, "config", "user.email", "sibling@example.com")
    _git(other, "config", "user.name", "Sibling")
    _git(other, "rm", "-r", "-q", "out")
    (other / "out").write_text("now an ordinary file\n")
    _git(other, "add", "out")
    _git(other, "commit", "-m", "sibling: out becomes a file")
    _git(other, "push", "origin", "HEAD:main")

    res = gc_workspaces(cwd=repo, workflow=wf)

    state = res["main_checkout"]
    assert state["updated"] is True, state
    assert state["overwritten_ignored"] == ["out/shot.png"], state
    assert (repo / "out").is_file(), "the directory really was replaced"


def test_an_ignored_DIRECTORY_is_reported_file_by_file_not_as_one_entry(repo, tracker, tmp_path):
    """The mirror case, and the one that makes "finer-grained than `removed_ignored`" true rather
    than a hope: the LOCAL path is an ignored DIRECTORY and the incoming commit puts a file there.

    `check-ignore` answers about `out` — the directory IS ignored here — so without the expansion
    the report is the single entry `out` for any number of dead files inside it, which is exactly
    the collapse `removed_ignored` is bounded by (`git status --ignored` folds a directory into
    one line). Two files die and both are named."""
    _api, wf = tracker
    _ignoring(repo, "out/")
    (repo / "out").mkdir()
    (repo / "out" / "a.txt").write_text("the human's own scratch\n")
    (repo / "out" / "b.txt").write_text("and another\n")
    _land_on_origin(tmp_path, "one", {"out": "upstream puts a file at that name\n"})

    res = gc_workspaces(cwd=repo, workflow=wf)

    state = res["main_checkout"]
    assert state["updated"] is True, state
    assert sorted(state["overwritten_ignored"]) == ["out/a.txt", "out/b.txt"], state


def test_the_directory_expansion_does_not_walk_through_a_symlink(repo, tracker, tmp_path):
    """`islink` before `isdir`, because a symlinked directory is ONE path to git and one path to
    delete — walking it would name files that do not live in this checkout at all, and report as
    destroyed things the merge never touches.

    THE FUNCTION IS ASKED DIRECTLY, AND THE REASON IS WEAKER THAN THE CARD THAT ASKED FOR IT
    PREDICTED — measured, not inherited (VMCP-262, 865). Its worry was that this pin catches the
    guard's removal only through BATCH SIZE: without `islink` the walk yields `link/precious.txt`,
    which is beyond a symbolic link, and a batch of ONE has nothing to bisect, so `_ignored_of`
    gives up exactly as the pre-837 whole-batch code did and the key VANISHES — so add one more
    ordinary ignored casualty to the same commit, the bisect isolates the unaskable path, the key
    comes back, and the pin was expected to go green on the defect. Built and run, it does NOT:
    against a control of 0 failed, `islink` removed plus a second ignored `z.png` dying in the same
    commit is 1 failed WITHOUT this direct assertion and 1 failed WITH it. The second casualty
    changes the FAILURE MODE (`overwritten_ignored` present but missing `link`) rather than
    removing it, because the payload assertion names `link` — so any assertion that names the link
    still bites. That is the difference from the submodule test next door, whose mutant only ever
    ADDED names to an exact list.

    So this call is insurance whose failure mode is not demonstrated, kept because it costs one
    line and states the property (`islink` before `isdir`) where the property lives, instead of
    inferring it from a report two functions away."""
    _api, wf = tracker
    _ignoring(repo, "/link")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "precious.txt").write_text("not in the checkout\n")
    (repo / "link").symlink_to(outside)
    _land_on_origin(tmp_path, "one", {"link": "upstream puts a file at that name\n"},
                    force=True)          # the rule is pushed, so the sibling ignores it too

    expanded = workspace_cmd._expand_if_directory(repo, "link")
    assert expanded == ["link"], (
        "a symlinked directory is ONE path to delete, so the expansion must answer with the link "
        "itself and nothing from beyond it", expanded
    )

    res = gc_workspaces(cwd=repo, workflow=wf)

    state = res["main_checkout"]
    assert state["overwritten_ignored"] == ["link"], state
    assert (outside / "precious.txt").read_text() == "not in the checkout\n"


def test_a_BROKEN_symlink_at_the_incoming_path_is_still_named(repo, tracker, tmp_path):
    """VMCP-268 (884). The `lexists` in `_ignored_paths_the_ff_will_overwrite`'s present-branch
    was held up by NOTHING: swap it for `exists` and the whole suite stayed green, so the comment
    beside it ("a broken symlink still counts as something being written over") described an
    intention no test could tell from its opposite. That claim was INHERITED from VMCP-262 (865)
    and re-measured here before anything was built, which the card asked for and which matters
    because a `0 failed` taken on somebody else's tree is exactly what this repo keeps getting
    wrong: whole file, `collected 226 items` in both rounds, control 0 failed / mutant 0 failed,
    0 ERROR lines either side. The hole was real.

    WHY IT IS A LOSS AND NOT A CURIOSITY: `exists` FOLLOWS the link, so on a broken one it
    answers False, the candidate never enters the batch, and the object the merge really does
    unlink is named nowhere. That is `overwritten_ignored` PRESENT-and-INCOMPLETE — VMCP-245
    (836)'s class by a third road, and this one needs no nesting at all, just a dead target.

    WHERE THE DECISION IS TAKEN, asserted rather than assumed, because the card warned that
    `_doomed_ancestor` is asked FIRST and could be intercepting this input: it is not. That walk
    is over ANCESTORS, and a root-level `shot.png` has none, so it answers None and the `elif`
    is reached — which is why the pin belongs here and not one branch up. Assert it directly, or
    a later reader cannot tell a pin on `lexists` from a pin on the ancestor walk.

    The dangling-symlink case one test over is a DIFFERENT road and does not cover this one:
    there the link is INSIDE an expanded directory, where `os.walk` files it under `filenames`
    and `lexists` is never consulted about it. Here the link IS the incoming path.

    MUTATION SWEEP, `lexists` -> `exists` on the present-branch, measured at `502cfab`. `-q`
    dropped, `FAILED `- and `ERROR `-prefixed lines counted SEPARATELY, `collected` cross-checked
    between control and round, `__pycache__` deleted before each, the target restored from a
    SIGTERM/SIGINT handler and sha256-verified after. WITHOUT this test: whole file, `collected
    226 items` in both, control 0 failed; mutant 0 failed, 0 ERROR lines either side — the round
    that says the choice was pinned by nothing, and the reason this card exists. WITH it, both
    selections agree and both are recorded because the narrow one alone would not show the file
    stayed clean: `-k BROKEN_symlink_at_the_incoming_path`, `collected 227 items / 226 deselected
    / 1 selected` in both, control 0 failed; mutant 1 failed. Whole file, `collected 227 items` in
    both, control 0 failed; mutant 1 failed. The single kill is this test, in both — nothing else
    in the file moves, which is the honest shape for a characterisation pin: it buys exactly the
    one decision it names."""
    _api, wf = tracker
    _ignoring(repo, "*.png")
    (repo / "shot.png").symlink_to(repo / "target-that-never-existed.png")
    assert os.path.lexists(repo / "shot.png"), "the link itself is there"
    assert not os.path.exists(repo / "shot.png"), "and it is BROKEN — this is the whole input"
    assert _git(repo, "status", "--porcelain") == "", "ignored, so git says nothing about it"
    landed = _land_on_origin(tmp_path, "one", {"shot.png": "UPSTREAM\n"}, force=True)

    assert workspace_cmd._doomed_ancestor(repo, "shot.png") is None, (
        "the ancestor walk must NOT claim this input — otherwise the branch under test is never "
        "reached and this test would pin the wrong decision"
    )

    res = gc_workspaces(cwd=repo, workflow=wf)

    state = res["main_checkout"]
    assert state["updated"] is True, state
    assert state["overwritten_ignored"] == ["shot.png"], state
    assert _git(repo, "rev-parse", "HEAD") == landed
    assert not (repo / "shot.png").is_symlink(), "the link really was replaced"
    assert (repo / "shot.png").read_text() == "UPSTREAM\n"


def test_a_NESTED_symlink_to_a_directory_inside_the_expansion_is_named_ONCE(repo, tracker,
                                                                            tmp_path):
    """The test above pins the guard on `rel` ITSELF; this one pins the SECOND guard, which was
    missing for two rounds — VMCP-245 (836), filed by the round-two independent review of
    VMCP-240 (806) and reproduced twice before it was fixed.

    `os.walk` classifies a symlink-to-a-DIRECTORY under `dirnames` and does not descend into it.
    Both halves of that are correct and intended; the defect is that such a path is then in
    NEITHER list the loop read, so it was named ZERO times rather than once. The top-level `islink`
    guard does not reach it — that guard is about `rel`, not about what is inside `rel`.

    TWO STANDS, KEPT APART. The one that ISOLATES the defect has four entries in an ignored `out/`:
    `git check-ignore -z --stdin` answered rc=0 and echoed ALL FOUR of `a.txt`, `to_dir` (-> a
    directory), `to_file` (-> a file) and a DANGLING symlink (git 2.50.1), while
    `_expand_if_directory` returned three, omitting `to_dir` alone — a symlink to a FILE and a
    dangling one land in `filenames`, because `os.walk` splits by `isdir`, which FOLLOWS. THIS test
    is the other, TWO-entry stand (`a.txt` plus `to_dir`), and end to end there the pre-fix probe
    answered `['out/a.txt']`, the fast-forward was rc=0, and `out/to_dir` was destroyed unnamed.
    Those two answers belong to different trees: the four-entry stand's pre-fix answer has THREE
    entries, so quoting `['out/a.txt']` as "the same stand" would describe a tree that never
    existed. An earlier draft of this docstring did exactly that.

    PRESENT and INCOMPLETE is the failure this card is about — not "the one way this key must not
    fail", which `_ignored_paths_the_ff_will_overwrite`'s BOUNDS list contradicts twice over.

    THIS TEST PINS "NAMED", NOT "NOT FOLLOWED", and the split is a measurement rather than a
    preference. Before VMCP-246 (837) it pinned both: `followlinks=True` produced
    `out/to_dir/precious.txt`, which is BEYOND a symlink, so `check-ignore` exited 128, the WHOLE
    batch was discarded and the key vanished. 837 made that give-up LOCAL — it bisects, so an
    unaskable path costs only itself — and the walked-through path is now dropped on its own while
    its neighbours are still reported. Re-measured after the rebase, control 0 failed throughout: the
    `followlinks=True` round fell from 1 failed to 0 failed against this file with the assertions
    unchanged, i.e. 837 DISARMED the half of this pin that watched for following, silently and
    without touching this file. The property did not stop mattering, so it moved
    to `test_the_walk_names_a_nested_symlinked_directory_and_returns_nothing_beneath_it`, which asks
    the walk directly and cannot be masked downstream. A `"outside-nested" not in ...` assert was
    also tried here and removed as decoration: `relpath` is taken against the repo root, so a
    followed symlink yields `out/to_dir/precious.txt` and the outside directory's NAME never appears
    at all — it would pass on a walked-through answer. The target is still placed OUTSIDE the
    checkout, because that is what makes "the merge cannot touch it" true; naming it would be a
    different defect of the same key, claiming a loss that never happened."""
    _api, wf = tracker
    _ignoring(repo, "out/")
    outside = tmp_path / "outside-nested"
    outside.mkdir()
    (outside / "precious.txt").write_text("not in the checkout\n")
    (repo / "out").mkdir()
    (repo / "out" / "a.txt").write_text("the human's own scratch\n")
    (repo / "out" / "to_dir").symlink_to(outside)
    assert _git(repo, "status", "--porcelain") == "", "invisible before, as ever"

    _land_on_origin(tmp_path, "nested-link", {"out": "upstream puts a file at that name\n"})

    res = gc_workspaces(cwd=repo, workflow=wf)

    state = res["main_checkout"]
    assert state["updated"] is True, state
    assert sorted(state["overwritten_ignored"]) == ["out/a.txt", "out/to_dir"], state
    assert (repo / "out").is_file(), "the directory really was replaced"
    assert (outside / "precious.txt").read_text() == "not in the checkout\n"


def test_the_expansion_names_a_symlink_to_a_file_and_a_dangling_one_too(repo, tracker, tmp_path):
    """The CONTRAST that makes the defect above one shape wide rather than "symlinks are lost".

    These two were named all along — both versions of this function ever committed name them — and
    the MECHANISM is incidental rather than chosen: `os.walk` splits its entries with `isdir`, which
    FOLLOWS, so a symlink whose target is a file, and one whose target does not exist at all, fall
    into `filenames` and were already reported. (A FIFO does too, so it was never part of the hole.)
    Whether that was intended is not knowable from the tree and is not claimed here — round one's
    docstring did state the one-path-per-symlink rule, though only for `rel` itself. Without this
    test the fix next door reads as "handle symlinks" rather than "handle the one shape `os.walk`
    hides from the loop".

    It does NOT pin the `islink` FILTER on `dirnames` — the test below this one does, and the
    distinction is a sweep result rather than a guess: dropping the filter (naming EVERY entry of
    `dirnames`) measured control 0 failed / mutation 0 failed until that third test existed."""
    _api, wf = tracker
    _ignoring(repo, "out/")
    (repo / "realfile.txt").write_text("a real file, tracked by nobody\n")
    (repo / "out").mkdir()
    (repo / "out" / "to_file").symlink_to(Path("..") / "realfile.txt")
    (repo / "out" / "dangling").symlink_to(Path("..") / "no-such-thing")

    _land_on_origin(tmp_path, "link-kinds", {"out": "upstream puts a file at that name\n"})

    res = gc_workspaces(cwd=repo, workflow=wf)

    state = res["main_checkout"]
    assert sorted(state["overwritten_ignored"]) == ["out/dangling", "out/to_file"], state
    assert (repo / "realfile.txt").exists(), "the symlink died, its target did not"


def test_a_REAL_subdirectory_inside_the_expansion_is_not_named_only_its_files_are(repo, tracker,
                                                                                  tmp_path):
    """The OTHER half of the `dirnames` read, and it exists because the mutation sweep said so.

    The fix above reads `dirnames` for one reason — a symlink-to-a-directory is in no other list —
    and filters it with `islink`. Dropping that FILTER, so that every entry of `dirnames` is named,
    was a sweep round that KILLED NOTHING: control 0 failed, mutation 0 failed, on an identical
    selection, because every other test here that expands an ignored directory has a FLAT one. With
    this test the same round is control 0 failed, mutation 1 failed. (The selection SIZE is not
    recorded: adding this test is what changed it, so any number written here would name the tree
    before or after itself. What the cross-check needs is that control and round agree, which they
    did in both sweeps.) So the claim "one name per symlink" was unpinned in the direction of
    over-reporting, and that direction is not cosmetic for THIS key: `overwritten_ignored` is read
    as the size of a loss, and a real subdirectory is not a path that dies — its FILES are, and
    they are named individually. Naming both would double-count the same bytes.

    THAT LAST SENTENCE WAS TRUE OF THIS FILTER AND FALSE OF THE KEY for as long as the de-dup was
    an exact-string one: VMCP-257 (859) measured the same bytes being double-counted anyway, by a
    road this filter never touches — an incoming commit carrying two SPELLINGS of one directory
    name on a case-insensitive checkout named every object inside it once per spelling. So the
    filter's stated goal was not actually being met end to end. It is now, in `_same_object_key`;
    the filter is still the right thing here, and the two are separate roads to the same property.

    `out/sub` is genuinely ignored here (the rule is `out/`, so `check-ignore` echoes it), which is
    what makes the mutation's output plausible rather than an obvious error, and is why nothing
    downstream would have caught it either.
    """
    _api, wf = tracker
    _ignoring(repo, "out/")
    (repo / "out").mkdir()
    (repo / "out" / "a.txt").write_text("the human's own scratch\n")
    (repo / "out" / "sub").mkdir()
    (repo / "out" / "sub" / "f.txt").write_text("one level down\n")

    _land_on_origin(tmp_path, "realsub", {"out": "upstream puts a file at that name\n"})

    res = gc_workspaces(cwd=repo, workflow=wf)

    state = res["main_checkout"]
    assert sorted(state["overwritten_ignored"]) == ["out/a.txt", "out/sub/f.txt"], state
    assert "out/sub" not in state["overwritten_ignored"], \
        "the directory itself is not a path that dies; its files are, and they are named"


# VMCP-257 (859): THE DE-DUP KEY. MUTATION SWEEP, one selection throughout — this file with
# `-k "two_spellings or HARDLINKS or real_subdirectory or NESTED_symlink or doomed_ancestor"`,
# no `-q`, `collected 205 items` / `8 selected` / 0 `ERROR ` lines in every round, each round read
# by counting `FAILED ` and `ERROR ` lines separately; control 0 failed:
#   * the key back to the EXACT STRING (the pre-859 shape) ......... control 0 failed; 1 failed
#   * the BARE `(st_dev, st_ino)` key, no casefold conjunct ........ control 0 failed; 1 failed
#   * `os.path.normcase` — the card's own option 1 ................. control 0 failed; 1 failed
#   * `os.lstat` -> `os.stat` ...................................... control 0 failed; 0 failed
# Rows one and three are the same test failing for opposite reasons and together they are the
# argument for the shape chosen: row one says the duplicate is real and the key closes it, row
# three says the fix the card ASKED for does not — `normcase` is a no-op on POSIX. Row two is why
# the key is composite rather than the obvious inode pair, and it is the half that runs on CI.
#
# ROW FOUR IS AN HONEST REMAINDER, not a hole to paper over: `lstat` vs `stat` kills nothing here,
# and it is hard to kill by construction rather than for want of a test. A dangling symlink makes
# `stat` raise, which falls back to the exact string — the pre-859 behaviour for that ONE path, so
# no report changes; and where `stat` would resolve a symlink onto its target's inode, the
# `casefold` conjunct still keeps the two names apart. The nearest live instance of this class is
# `lexists` vs `exists` one function up, which is filed as VMCP-268 (884) rather than smuggled in
# here.

def test_an_unreadable_FILE_is_destroyed_by_the_merge_and_named_anyway(repo, tracker, tmp_path):
    """VMCP-253 (852). The one permission shape where the fast-forward GOES and BYTES die — and
    the reason "nothing carrying bytes dies unnamed" needed a second mechanism to stand on. Do NOT
    carry that phrase anywhere as a guarantee, on this axis or off it — round 3 measured it false
    three ways. An EMPTY unreadable directory also lets the ff through and dies, carrying no bytes;
    a `PATH_MAX` band destroys bytes and names none of them (VMCP-281 (940)); and ON this axis a
    victim whose NAME the regenerable filter drops dies with no key at all, which both of this
    round's independent passes built. What is below is a measured ROW.

    Four DIRECTORY shapes (`000`, `100`, `400`, `500`), a `000` directory two levels down and one
    under a `600` PARENT all make `merge --ff-only` refuse, leaving that directory's own DIRECT
    CHILD alive and no `overwritten_ignored` key for it. Round 3 measured what those two
    qualifiers are worth: an EMPTY such directory stops refusing at `400`/`500`, and bytes one
    level DEEPER in a `700` subdirectory are unlinked at `500` while the direct child is not.
    A FILE is the shape that goes the other way — permissions on a file do not affect the
    enumeration of its directory ENTRY, so git unlinks a
    `chmod 000` file without a word. What saves this row is not the refusal but the NAMING:
    `os.walk` lists the entry regardless of its mode, so the casualty reaches
    `overwritten_ignored`.

    DO NOT SUMMARISE THOSE FOUR AS "git cannot delete what it cannot read" — this docstring did,
    and round two measured it false for half of them with the modes left in place. At `000` and
    `100` READDIR is denied and git says `cannot opendir`; at `400` readdir SUCCEEDS, `os.listdir`
    returns the victim's name and so does git (`cannot lstat`); at `500` git reads all of it and
    dies on the WRITE bit (`cannot unlink`). Two of the four MODES are read refusals — three of
    the six ROWS, since the two-level row is `000` again; keep the denominator explicit. Two
    further things belong to the STAND rather than to the mode and are spelled out in
    `_expand_if_directory`'s own docstring: which `code` comes back, and whether a key comes with
    it — a multi-path incoming commit turns every one of these refusals into `half-applied`, and
    one that also displaces an ignored path can carry `overwritten_ignored` and a real loss
    ELSEWHERE while this directory's DIRECT CHILD sits untouched.

    Pinned because the whole grid's conclusion rests on this row alone — the four refusing shapes
    prove nothing about it, and it was the only shape in the grid that was unpinned.

    MUTATION SWEEP, one selection throughout — this file with `-k "unreadable_FILE or
    real_subdirectory or NESTED_symlink or two_spellings or HARDLINKS"`, no `-q`, `collected 206
    items` / `6 selected` / 0 `ERROR ` lines in every round, rounds read by counting `FAILED ` and
    `ERROR ` lines separately (206 was this file's total when that sweep ran; the round-2 test
    below took it to 207 without changing THIS selection, which is why the cross-check that
    matters is control and rounds agreeing rather than the number); control 0 failed:
      * the walk names only READABLE files ..................... control 0 failed; 1 failed
      * the walk names no plain files at all .................. control 0 failed; 6 failed
      * `os.walk(onerror=raise)` instead of the silent default . control 0 failed; 0 failed
    Row one is the pin, and it is the sharp one: it changes exactly the mechanism this test is
    about and, ON THIS SELECTION, kills exactly this test. That qualifier was missing for two
    rounds and it is what the row is worth — re-run on the WHOLE file, control 0 failed at the
    start and again at the end, the same mutation reads 3 failed, taking the symlink-to-a-file
    test and the round-2 pin below with it, both of them the same stat-blind mechanism. A kill
    count belongs to the selection it was read on. Row two is blunt — it kills all six, so it
    says the walk matters and nothing about WHICH property. Row three is the honest remainder:
    nothing in the suite makes the walk hit an unreadable directory, so the four REFUSING shapes
    of the grid stay unpinned. That is deliberate rather than missing — on those the merge
    refuses, no `overwritten_ignored` key is emitted for the locked path, and the card asked for
    the grid to be measured and written down, explicitly NOT for a guard."""
    _api, wf = tracker
    _ignoring(repo, "out/")
    (repo / "out").mkdir()
    (repo / "out" / "a.txt").write_text("the human's own note\n")
    doomed = repo / "out" / "locked_file"
    doomed.write_text("the human's bytes, behind a mode nobody can read\n")
    doomed.chmod(0o000)
    assert _git(repo, "status", "--porcelain") == "", "ignored, so invisible as always"

    _land_on_origin(tmp_path, "unreadable", {"out": "upstream takes that name\n"})

    res = gc_workspaces(cwd=repo, workflow=wf)

    state = res["main_checkout"]
    assert state["updated"] is True, ("the merge is NOT refused by an unreadable FILE — this is "
                                      "the shape that disproves the general rule", state)
    assert sorted(state["overwritten_ignored"]) == ["out/a.txt", "out/locked_file"], state
    assert not doomed.exists(), "and it really is destroyed; naming is not saving"


def test_the_walk_RECORDS_what_it_could_not_read_instead_of_swallowing_it(tmp_path):
    """VMCP-281 (940), variant B, chosen by the human on the card: `onerror=None` is replaced by a
    RECORDING callback, so "the list is incomplete" becomes an expressible state.

    WHAT WAS WRONG. `os.walk` defaults to `onerror=None`, which swallows EVERY `OSError` — not
    only permission ones. So a directory the walk cannot descend contributes no name and no
    signal, and `overwritten_ignored` comes back PRESENT, NON-EMPTY and SHORT, with no
    `overwritten_ignored_truncated` either. That report is indistinguishable from a complete one,
    and the whole documented reading of the key ("present ⇒ these died; absent ⇒ nothing proven")
    invites treating it as a lower bound on the loss — which it silently was not.

    The fix does NOT save the bytes and is not meant to: git destroys them, a `git pull --ff-only`
    typed by hand does the same, and VMCP-240 (806) settled that this feature is a post-mortem and
    explicitly not a guard. What it buys is that a SHORT list can no longer pass for a full one.

    Asserted on the return value of the walk rather than end to end, so no downstream filter can
    mask it. The trigger here is a mode, because that is portable and deterministic; the channel
    the card was filed for is ENAMETOOLONG, which needs no mode at all and is measured next door.
    """
    root = tmp_path / "checkout"
    (root / "out" / "reachable").mkdir(parents=True)
    (root / "out" / "reachable" / "a.txt").write_text("the human's own scratch\n")
    closed = root / "out" / "closed"
    closed.mkdir()
    (closed / "precious.txt").write_text("bytes nobody will be told about\n")
    closed.chmod(0o000)
    try:
        unreadable: list[str] = []
        got = workspace_cmd._expand_if_directory(root, "out", unreadable=unreadable)

        assert "out/reachable/a.txt" in got, ("the neighbour is still named — a recording "
                                              "`onerror` must not cost the names the walk DID "
                                              "reach", got)
        assert not any(p.startswith("out/closed/") for p in got), \
            "nothing under the unreadable directory can be named; that is the loss, not the bug"
        assert len(unreadable) == 1, (
            "the walk hit exactly one place it could not read and must say so ONCE. Before this "
            "card that information existed inside `os.walk` and was thrown away by the default "
            "`onerror=None`", unreadable
        )
    finally:
        closed.chmod(0o700)                          # or tmp_path cleanup cannot remove it


def test_a_denied_probe_says_so_END_TO_END_beside_the_names_it_did_reach(repo, tracker, tmp_path):
    """VMCP-281 (940) end to end: the state the card is about, on a real repo and a real merge.

    `overwritten_ignored` PRESENT and NON-EMPTY while the walk was denied somewhere — before this
    card that came back with no companion key at all, so it was byte-for-byte the shape of a
    COMPLETE answer and a reader had nothing to tell them apart. The unit tests next door pin the
    walk's own return value; this pins that the signal survives the probe, the ignore filter, the
    cap and the payload assembly, because every one of those has dropped information before.

    The mode is the portable trigger, not the channel: 940 is about ENAMETOOLONG, which needs no
    mode at all. What is shared — and what this asserts — is the callback and the key.
    """
    _api, wf = tracker
    _ignoring(repo, "out/")
    (repo / "out").mkdir()
    (repo / "out" / "named.txt").write_text("the human's own note\n")
    closed = repo / "out" / "closed"
    closed.mkdir()
    (closed / "unnameable.txt").write_text("bytes the report can never list\n")
    closed.chmod(0o000)
    assert _git(repo, "status", "--porcelain") == "", "ignored, so invisible as always"

    _land_on_origin(tmp_path, "denied", {"out": "upstream takes that name\n"})
    try:
        res = gc_workspaces(cwd=repo, workflow=wf)
    finally:
        if closed.exists():
            closed.chmod(0o700)

    state = res["main_checkout"]
    assert state.get("overwritten_ignored"), (
        "the probe still names what it COULD reach — this card added a signal, it did not trade "
        "away the names", state
    )
    assert state.get("overwritten_ignored_incomplete") == 1, (
        "the walk was denied one place and the report does not say so. That is the whole defect: "
        "a SHORT `overwritten_ignored` is indistinguishable from a complete one, and every "
        "document in this repo invites reading a present list as a lower bound on the loss", state
    )


def _build_enametoolong_band(out: Path) -> tuple[int, int, int] | None:
    """Nest inside `out` until its ABSOLUTE path passes PATH_MAX while the RELATIVE one has not.

    THE BAND IS THE WHOLE POINT of VMCP-281 (940). git works RELATIVE to the checkout and the
    probe walked ABSOLUTE, so between those two lengths git sees a file and destroys it while
    `scandir` gets ENAMETOOLONG. Built by `chdir` descent so every individual NAME stays short —
    it is the accumulated path that is long, which is what makes this an ordinary deep tree and
    not a pathological filename.

    Returns (depth, deepest_relative_len, deepest_absolute_len) or None when this machine cannot
    hold the band — PATH_MAX differs (1024 on macOS, 4096 on Linux) and the checkout root eats
    into it, so the caller SKIPS rather than asserting on a stand that was never built.
    """
    limit = os.pathconf("/", "PC_PATH_MAX")
    name = "d" * 34
    root = out.parent
    keep = os.open(".", os.O_RDONLY)
    depth = 0
    try:
        os.chdir(out)
        while depth < (limit // 35) + 4:
            os.mkdir(name)
            os.chdir(name)
            with open("f.txt", "w") as fh:
                fh.write(f"precious-{depth}\n")
            depth += 1
            rel = len(f"{out.name}" + f"/{name}" * depth + "/f.txt")
            absolute = len(str(root)) + 1 + rel
            if absolute > limit and rel < limit:
                return depth, rel, absolute
            if rel >= limit:
                return None                  # overshot: git would not see the file either
    except OSError:
        return None
    finally:
        os.fchdir(keep)
        os.close(keep)
    return None


def test_ENAMETOOLONG_is_reported_on_the_branch_where_the_merge_SUCCEEDS(repo, tracker, tmp_path):
    """VMCP-281 (940)'s actual channel, end to end, on the `updated: true` branch.

    THIS BRANCH RATHER THAN `half-applied`, and the difference is not cosmetic — it is why the
    mode-based stand next door cannot stand in for this one. A denied directory stops GIT too, so
    the merge refuses and the report goes out as `half-applied`; the state the card describes is
    the opposite one, where the merge COMPLETES and the report looks like a full account of what
    died. ENAMETOOLONG is the one measured route into it, because git addresses paths RELATIVE to
    the checkout and the probe walked them ABSOLUTE, so only the probe is blinded.

    Measured while fixing the card, macOS 26.5.2 / APFS, git 2.50.1, PATH_MAX 1024: at depth 28
    (989 relative / 1063 absolute) the merge reports `updated: True`, 28 files with bytes are
    destroyed, `overwritten_ignored` names 27 and — before this fix — no companion key appeared
    at all. Three independent reviewers reached the same shape with 15/14, 22/21 and 25/24.

    SKIPPED rather than asserted where the band cannot be built: PATH_MAX is 1024 here and 4096 on
    the Linux runner, and the checkout root eats into the budget. A skip says "not measured on this
    machine", which is honest; the unit pins next door carry the callback on every machine.
    """
    _api, wf = tracker
    _ignoring(repo, "out/")
    (repo / "out").mkdir()
    band = _build_enametoolong_band(repo / "out")
    if band is None:
        pytest.skip("PATH_MAX and this checkout's root leave no relative/absolute band to build")
    depth, rel_len, abs_len = band
    assert rel_len < abs_len, (rel_len, abs_len)
    assert _git(repo, "status", "--porcelain") == "", "ignored, so invisible as always"

    _land_on_origin(tmp_path, "toolong", {"out": "upstream takes that name\n"})

    res = gc_workspaces(cwd=repo, workflow=wf)

    state = res["main_checkout"]
    assert state.get("updated") is True, (
        "the merge must SUCCEED here — that is what separates this channel from an unreadable "
        "directory, which stops git as well and reports `half-applied`", state
    )
    assert state.get("overwritten_ignored"), (
        "the probe still names what it reached; the deep tail is what it cannot", state
    )
    assert state.get("overwritten_ignored_incomplete"), (
        f"the walk was denied inside a {depth}-deep tree ({rel_len} relative / {abs_len} "
        f"absolute, PATH_MAX {os.pathconf('/', 'PC_PATH_MAX')}) and the report does not say so. "
        f"This is the card's exact state: `overwritten_ignored` PRESENT, NON-EMPTY and SHORT, "
        f"with nothing to distinguish it from a complete account of the loss", state
    )


def test_the_recorded_skip_counts_PLACES_and_never_files(tmp_path):
    """The key's SEMANTICS, pinned because the natural misreading is expensive.

    One unreadable directory hides its WHOLE subtree, so the number of recorded skips is NOT the
    number of files lost — it is the number of places the probe could not look. Measured while
    fixing this card, on the ENAMETOOLONG stand: at depth 28 one file died unnamed and the walk
    reported ONE error; at depth 30 THREE files died unnamed and the walk still reported ONE.

    That is why the sibling key is a bare count of skipped PLACES and why no code here tries to
    turn it into a loss estimate. A reader who takes it for a file count would compute a lower
    bound that is wrong in the unsafe direction.
    """
    root = tmp_path / "checkout"
    closed = root / "out" / "closed"
    (closed / "deeper").mkdir(parents=True)
    for name in ("one.txt", "two.txt"):
        (closed / name).write_text("bytes\n")
    (closed / "deeper" / "three.txt").write_text("bytes\n")
    closed.chmod(0o000)
    try:
        unreadable: list[str] = []
        workspace_cmd._expand_if_directory(root, "out", unreadable=unreadable)
        assert len(unreadable) == 1, (
            "three files and a subdirectory are behind ONE closed door, so the probe was denied "
            "ONCE. If this ever reads 3 or 4 the key has quietly become a file count, and the "
            "docstring above says why that is worse than useless", unreadable
        )
    finally:
        closed.chmod(0o700)


def test_a_symlink_under_a_NON_TRAVERSABLE_parent_is_still_named(tmp_path):
    """VMCP-253 (852) round 2. A BLINDED `is_dir()` does not cost a symlink its name — and the
    real directory beside it is lost, which is VMCP-245 (836)'s road reached a second way.

    The shape is the one 852 listed as unmeasured: the bits sit on the PARENT (`out/mid` at
    `600`, readable but NOT traversable), not on the path that goes unnamed. So "an UNREADABLE
    subdirectory is skipped" is the wrong description of it — `scandir(out/mid)` SUCCEEDS and
    enumerates both entries; what fails is everything one level further in.

    THE ASYMMETRY IS `d_type`, and it is why the two entries end up in different lists. A real
    directory answers `is_dir()` straight from the readdir record with no stat at all, so
    `out/mid/real` reaches `dirnames`, the loop reads `filenames + links`, and it is not named —
    nor is its content, since the descent into it is denied and `onerror=None` swallows that.
    A SYMLINK has to be RESOLVED to answer the same question, and that stat is denied: asked
    directly, `entry.is_dir(follow_symlinks=False)` answers False from `d_type` while the default
    `entry.is_dir()` RAISES EACCES — `os.walk` swallows that into "not a directory", so
    `out/mid/to_dir` lands in `filenames` and IS named. Measured, not reasoned, twice over: the
    guess going in was that the symlink would be lost too, and the first draft of this paragraph
    then said `is_dir()` "answers False", which the direct probe corrected to a raise.

    WHAT THIS PINS THAT 836's TEST DOES NOT — dropping the `links` pick (836's whole fix) leaves
    this test GREEN, because this symlink never travelled that road. Recorded as a sweep row
    below rather than asserted, since a negative is the only thing that shows the two tests cover
    different mechanisms instead of one twice.

    MUTATION SWEEP, one selection throughout — this file with `-k "NON_TRAVERSABLE or
    unreadable_FILE or real_subdirectory or NESTED_symlink or symlink_to_a_file"`, no `-q`,
    `6 selected` and 0 `ERROR ` lines in every round, rounds read by counting `FAILED ` and
    `ERROR ` lines separately, control run at the START and again at the END (0 both times) and
    the source sha256-verified identical to its backup afterwards; control 0 failed:
      * `filenames` filtered by `os.path.isfile` ............... control 0 failed; 2 failed
      * the loop reads `filenames` only, dropping `links` ...... control 0 failed; 2 failed
      * the loop reads `links` only, dropping `filenames` ...... control 0 failed; 6 failed
    Row one is the sharp one and it is what kills THIS test: a stat-based filter cannot see past
    the blinded parent either, so the symlink is dropped — it takes the dangling/symlink-to-file
    test with it, which is the same mechanism and honest to report as two. Row two is the
    negative described above, and note WHICH two died — `NESTED_symlink` and the walk-direct test,
    i.e. 836's pair — while this test stayed GREEN, which is the measurement showing the two
    cover different roads. Row three is blunt, killing all six, so it says the walk matters and
    nothing about which property. The three counts were written from the run: a draft of this
    docstring guessed 1 and 5 for rows two and three before the sweep and both were wrong.
    The `collected` total is NOT recorded as a durable figure — it agreed across control and every
    round, which is what the cross-check needs, and it moves with every test this file gains (207
    while this sweep ran, six agents landing beside it)."""
    root = tmp_path
    out = root / "out"
    (out / "mid").mkdir(parents=True)
    (out / "a.txt").write_text("a plain neighbour, named on every road\n")
    target = root / "target_dir"
    target.mkdir()
    (target / "precious.txt").write_text("lives outside the walk\n")
    (out / "mid" / "to_dir").symlink_to(Path("..") / ".." / "target_dir")
    (out / "mid" / "real").mkdir()
    (out / "mid" / "real" / "buried.txt").write_text("the human's bytes, one level too deep\n")
    (out / "mid").chmod(0o600)
    try:
        got = workspace_cmd._expand_if_directory(root, "out")
    finally:
        # RESTORE before the fixture tears the tree down: a directory without `+x` cannot be
        # walked into, so `tmp_path` cleanup would fail and take unrelated tests with it.
        (out / "mid").chmod(0o700)

    assert "out/mid/to_dir" in got, (
        "the symlink is named even though its parent denies the stat that would classify it", got)
    assert "out/mid/real" not in got and "out/mid/real/buried.txt" not in got, (
        "and the real directory beside it is the documented gap — named nowhere, its content "
        "unreachable, which is what makes the list a lower bound on the loss", got)
    assert "out/a.txt" in got, ("the readable neighbour is unaffected — the blinding is local to "
                               "what sits under `mid`", got)


def _case_insensitive(path: Path) -> bool:
    """Ask the FILESYSTEM, never the platform: `sys.platform` is a proxy and a wrong one — an
    APFS volume can be created case-SENSITIVE, and a Linux checkout on a mounted share can be
    case-insensitive. `core.ignorecase` is git's own answer to this same question, taken at
    clone time from the same place."""
    probe = path / "VMCP859CaseProbe"
    probe.write_text("x\n")
    try:
        return (path / "vmcp859caseprobe").exists()
    finally:
        probe.unlink()


def _land_two_spellings(tmp_path: Path, name: str, spellings: tuple[str, ...],
                        extra: tuple[str, ...] = ()) -> str:
    """Land ONE commit carrying several spellings of one name as separate blobs.

    Through `hash-object` + `update-index --cacheinfo` + `commit-tree` rather than `git add`,
    because on a case-insensitive checkout `git add` collapses the two into one entry — the sibling
    would silently build a tree that does not have the shape under test. `core.ignorecase=false`
    on the sibling for the same reason.
    """
    other = tmp_path / f"sibling-{name}"
    subprocess.run(["git", "clone", "-q", str(tmp_path / "origin.git"), str(other)],
                   check=True, capture_output=True)
    _git(other, "config", "user.email", "sibling@example.com")
    _git(other, "config", "user.name", "Sibling")
    _git(other, "config", "core.ignorecase", "false")
    blob = subprocess.run(["git", "hash-object", "-w", "--stdin"], cwd=other,
                          input="UPSTREAM\n", capture_output=True, text=True,
                          check=True).stdout.strip()
    for rel in (*spellings, *extra):
        _git(other, "update-index", "--add", "--cacheinfo", f"100644,{blob},{rel}")
    tree = _git(other, "write-tree")
    commit = _git(other, "commit-tree", tree, "-p", "HEAD", "-m", f"sibling: {name}")
    _git(other, "push", "-q", "origin", f"{commit}:refs/heads/main")
    return commit


def test_two_spellings_of_one_incoming_name_do_not_double_count_the_same_bytes(repo, tracker,
                                                                               tmp_path):
    """VMCP-257 (859). The ONE road on which `overwritten_ignored` used to OVERSTATE the loss.

    An incoming commit can carry `out` and `OUT` as two blobs in one tree. On a case-insensitive
    checkout both spellings answer `os.path.isdir`, `os.walk` walks each as its own directory,
    `os.path.relpath` keeps whichever spelling it was handed, and the caller de-duped on the exact
    string — so every dying object was named once PER SPELLING. Measured before the fix: three
    objects on disk came back as five names (`OUT/a.txt`, `OUT/to_dir`, `out/a.txt`, `out/to_dir`
    and the unrelated `shot.png`).

    THE DIRECTION IS THE POINT. Everything else about this key makes it UNDERSTATE — the
    regenerable-name filter, `_MAX_DIR_EXPANSION`, every give-up — and it is documented as a lower
    bound on the loss. Two errors in opposite directions in one key are worse than one.

    SKIPPED, NOT FAKED, on a case-sensitive filesystem, which is where CI runs. There `out` and
    `OUT` really are two different directories and there is no duplicate to collapse; asserting
    anything about a shape the disk cannot hold would pin the harness rather than the code. The
    property that carries the fix on every filesystem is the hardlink test below, which does run
    everywhere."""
    if not _case_insensitive(tmp_path):
        pytest.skip("the duplicate only exists where the filesystem folds case")
    _api, wf = tracker
    _ignoring(repo, "out/", "*.png")
    outside = tmp_path / "outside859"
    outside.mkdir()
    (outside / "target.txt").write_text("a file OUTSIDE the checkout\n")
    (repo / "out").mkdir()
    (repo / "out" / "a.txt").write_text("the human's own note\n")
    (repo / "out" / "to_dir").symlink_to(outside)
    (repo / "shot.png").write_bytes(b"\x89PNG an unrelated ignored casualty")
    assert _git(repo, "status", "--porcelain") == "", "all three are ignored, as always"

    _land_two_spellings(tmp_path, "spell", ("out", "OUT"), extra=("shot.png",))

    res = gc_workspaces(cwd=repo, workflow=wf)

    state = res["main_checkout"]
    named = state["overwritten_ignored"]
    assert len(named) == 3, ("three objects die on this disk — one file, one symlink and one "
                             "unrelated png — so three names", named)
    assert sorted(n.lower() for n in named) == ["out/a.txt", "out/to_dir", "shot.png"], named


def test_two_ignored_HARDLINKS_dying_together_are_still_named_separately(repo, tracker, tmp_path):
    """The COST of the fix above, measured and then bought off — and the reason the de-dup key is
    composite instead of the obvious `(st_dev, st_ino)` (VMCP-257, 859).

    Two hardlinks are ONE inode and TWO names, and both names go when the directory holding them
    does. A bare inode key collapses them: measured, this exact stand answered `['out/b.txt']`
    where it should answer both. `rel.casefold()` in the key keeps them apart while still folding
    the case-duplicate, and it buys the property neither half has on its own — the key can never
    merge two DISTINCT objects, because the inode forbids that, so on a case-sensitive filesystem
    it collapses nothing at all.

    Runs EVERYWHERE, unlike its neighbour: hardlinks need no help from the filesystem's case
    folding, so this is the half of the pin CI actually executes.

    What is still open and named rather than hidden: two hardlinks whose names differ only in CASE
    would still collapse. Building that needs a case-sensitive filesystem, and it errs in the
    direction this key errs in everywhere else — understating."""
    _api, wf = tracker
    _ignoring(repo, "out/")
    (repo / "out").mkdir()
    (repo / "out" / "a.txt").write_text("the human's own note\n")
    os.link(repo / "out" / "a.txt", repo / "out" / "b.txt")
    assert (repo / "out" / "a.txt").stat().st_ino == (repo / "out" / "b.txt").stat().st_ino

    _land_on_origin(tmp_path, "hardlink", {"out": "upstream takes that name\n"})

    res = gc_workspaces(cwd=repo, workflow=wf)

    state = res["main_checkout"]
    assert sorted(state["overwritten_ignored"]) == ["out/a.txt", "out/b.txt"], (
        "one inode, two names, two casualties — the de-dup key must not confuse 'the same object' "
        "with 'the same path'", state
    )


def test_the_walk_names_a_nested_symlinked_directory_and_returns_nothing_beneath_it(tmp_path):
    """NAMED once, never FOLLOWED — pinned on the WALK itself, because end to end it is invisible.

    This assertion used to live in the end-to-end test next door, where `followlinks=True` killed it:
    the walked-through `out/to_dir/precious.txt` is BEYOND a symlink, `check-ignore` exited 128, the
    whole batch was discarded and the key vanished. VMCP-246 (837) then made that give-up LOCAL — it
    bisects, so an unaskable path now costs only ITSELF — and the walked-through path is dropped
    individually while its neighbours are still reported. Measured on the rebased tree, control 0
    failed on both sides of the change: the `followlinks=True` round went from 1 failed (pre-rebase)
    to 0 failed (post-rebase) with the end-to-end assertion unchanged, and back to 1 failed once
    this test existed. That is a DISARMED pin, not a fixed bug, and it is exactly the trap 837's own
    card was flagged for. So the property is asserted where 837 cannot mask it — on the return value
    of the walk, which names the symlink once and nothing underneath it whatever `_ignored_of` does.

    The target is OUTSIDE the checkout, so a followed walk would report paths the merge never
    touches; `os.walk`'s `followlinks` default of False is what holds, and this is what says so."""
    root = tmp_path / "checkout"
    (root / "out").mkdir(parents=True)
    (root / "out" / "a.txt").write_text("the human's own scratch\n")
    outside = tmp_path / "outside-walk"
    outside.mkdir()
    (outside / "precious.txt").write_text("not in the checkout\n")
    (root / "out" / "to_dir").symlink_to(outside)

    got = workspace_cmd._expand_if_directory(root, "out")

    assert sorted(got) == ["out/a.txt", "out/to_dir"], got
    assert not any(p.startswith("out/to_dir/") for p in got), \
        "the symlink is one name, not a doorway — nothing beneath it may be reported"


def test_the_symlink_pick_reads_dirnames_AFTER_the_gitlink_prune_not_before(tmp_path):
    """The ORDER of the two `dirnames` reads inside the walk — VMCP-246 (837)'s gitlink prune, then
    VMCP-245 (836)'s symlink pick. This is the seam where the two cards meet, so it gets its own pin.

    The order is IRRELEVANT for an ordinary submodule and load-bearing in exactly one shape, and
    both halves were measured rather than argued. Ordinarily a gitlink is a REAL directory, so the
    `islink` pick skips it on either side of the prune — measured on a real submodule, both orders
    answer `['vendor']`. It matters when the gitlink PATH ITSELF is a symlink-to-a-directory on disk
    (a deinitialised submodule someone replaced with a link): prune-then-pick answers `['vendor']`,
    pick-then-prune answers `['vendor/sub']` — a path inside a live gitlink, which 837 established
    must never be handed to `check-ignore` in the form this code uses.

    `gitlinks` is an ordinary parameter, so this pins the seam with NO submodule, no origin and no
    merge: hand it the set directly. Swapping the two statements in the loop is a round of its own —
    control 0 failed, swapped 1 failed, and the failure is this test."""
    root = tmp_path / "checkout"
    (root / "vendor").mkdir(parents=True)
    (root / "vendor" / "keep.txt").write_text("an ordinary ignored file beside the gitlink\n")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "inner.txt").write_text("inside the link's target\n")
    (root / "vendor" / "sub").symlink_to(elsewhere)

    got = workspace_cmd._expand_if_directory(root, "vendor", frozenset({"vendor/sub"}))

    assert sorted(got) == ["vendor/keep.txt"], got
    assert "vendor/sub" not in got, "a gitlink path is pruned even when it is a symlink on disk"
    assert not any(p.startswith("vendor/sub/") for p in got), "and never walked into"


def test_a_local_ignored_FILE_whose_name_upstream_turns_into_a_DIRECTORY_is_named(
        repo, tracker, tmp_path):
    """THE SECOND CHANNEL WITH NO PATH IN THE DIFF, and the exact input the card's independent
    reviewer built to disprove "the one channel" — VMCP-240 (806), round two.

    The mirror of the test above: there the LOCAL path was a directory, here the INCOMING one is.
    Upstream sends `out/x.txt`; the checkout holds the human's own ignored FILE at `out`. The
    incoming diff names ONLY `out/x.txt` — the path that dies, `out`, is in no entry of it — and
    `lexists` answers False for the incoming child, because `out` is a file and nothing can live
    inside it. Measured on the shipped round-one code: the probe returned `[]` and the sync
    reported `updated: true` with NO key while the bytes went, which is the same silence #806
    was filed to remove, in a shape #806 itself then left open.

    It also disproves, on this same input, the justification the existence filter carried:
    "a path that is not on this disk has nothing to lose". `out/x.txt` is not on this disk. Its
    ANCESTOR is, and that is what dies."""
    _api, wf = tracker
    _ignoring(repo, "/out")
    (repo / "out").write_text("the human's own scratch file\n")
    assert _git(repo, "status", "--porcelain") == "", "invisible before, as ever"

    _land_on_origin(tmp_path, "name2dir", {"out/x.txt": "upstream\n"}, force=True)

    res = gc_workspaces(cwd=repo, workflow=wf)

    state = res["main_checkout"]
    assert state["updated"] is True, state
    assert state["overwritten_ignored"] == ["out"], state
    assert (repo / "out").is_dir(), "the file really was replaced by a directory"
    assert _git(repo, "status", "--porcelain") == "", "and git says nothing about it afterwards"


def test_the_doomed_ancestor_is_found_more_than_one_level_up(repo, tracker, tmp_path):
    """Depth is why `_doomed_ancestor` WALKS instead of asking about the immediate parent.

    Upstream sends `deep/a/b/y.txt` over a local ignored FILE `deep`: neither `deep/a/b` nor
    `deep/a` is on this disk, and the first ancestor that is, three levels up, is the one the
    merge deletes."""
    _api, wf = tracker
    _ignoring(repo, "/deep")
    (repo / "deep").write_text("the human's own notes\n")

    _land_on_origin(tmp_path, "deepdir", {"deep/a/b/y.txt": "upstream\n"}, force=True)

    res = gc_workspaces(cwd=repo, workflow=wf)

    state = res["main_checkout"]
    assert state["overwritten_ignored"] == ["deep"], state
    assert (repo / "deep" / "a" / "b" / "y.txt").exists(), "the fast-forward really happened"


def test_a_doomed_ancestor_that_is_a_SYMLINK_to_a_directory_is_still_named(repo, tracker,
                                                                           tmp_path):
    """`islink` BEFORE `isdir` in the ancestor walk, and this is the input that makes the order
    load-bearing rather than copied from `_expand_if_directory`.

    A local ignored `linkdir -> realdir` with `linkdir/y.txt` incoming: `os.path.isdir` FOLLOWS
    the symlink and answers True, i.e. an `isdir`-only walk would conclude "a real directory, the
    incoming path merely does not exist inside it yet, nothing displaced" — and be wrong.
    Measured: the merge removes the SYMLINK and puts a real directory there, while the target
    directory's own file is untouched. So the thing destroyed is the ignored symlink itself,
    which is exactly one path, and the report says so."""
    _api, wf = tracker
    _ignoring(repo, "/linkdir")
    (repo / "realdir").mkdir()
    (repo / "realdir" / "inside.txt").write_text("not what the merge is aiming at\n")
    (repo / "linkdir").symlink_to("realdir")

    _land_on_origin(tmp_path, "linkdir", {"linkdir/y.txt": "upstream\n"}, force=True)

    res = gc_workspaces(cwd=repo, workflow=wf)

    state = res["main_checkout"]
    assert state["overwritten_ignored"] == ["linkdir"], state
    assert not (repo / "linkdir").is_symlink(), "the symlink really was replaced"
    assert (repo / "realdir" / "inside.txt").read_text() == "not what the merge is aiming at\n"


def test_the_ancestor_walk_goes_TOP_DOWN_so_a_symlink_is_not_resolved_through(repo, tracker,
                                                                              tmp_path):
    """THE THIRD displacement shape, found while writing `_doomed_ancestor`'s own docstring and
    the reason that walk runs SHALLOWEST-FIRST.

    `os.path.lexists` declines to follow only the LAST component of the path it is handed, so a
    BOTTOM-UP walk asking about `a/b` — when `a` is a symlink — silently gets an answer about
    `realdir/b`. Here `realdir/b` exists as a real directory, so the original walk (bottom-up AND
    stopping at the first ancestor that exists) concluded "a real directory, nothing displaced"
    and returned None. Measured on that input: the merge is rc=0, `git status --porcelain` says
    `?? realdir/` and nothing else BOTH before and after, `realdir/b/keep.txt` survives, and the
    human's ignored SYMLINK `a` is replaced by a real directory with nothing reported.

    This is shape `A` of the grid in `_doomed_ancestor`'s docstring, and it is killed only by the
    two defects TOGETHER — which is why the sweep needed a mutation that restores both, and why
    the two neighbouring tests exist: shape `B` pins the ORDER on its own and shape `C` pins the
    walk-through on its own. One shape cannot pin two properties, and trying to was this test's
    own first defect: the round that flipped only the order killed nothing at all."""
    _api, wf = tracker
    _ignoring(repo, "/a")
    (repo / "realdir" / "b").mkdir(parents=True)
    (repo / "realdir" / "b" / "keep.txt").write_text("the symlink's target, not the victim\n")
    (repo / "a").symlink_to("realdir")
    assert (repo / "a" / "b").is_dir(), "the shape only exists because this resolves"

    _land_on_origin(tmp_path, "symthru", {"a/b/c.txt": "upstream\n"}, force=True)

    res = gc_workspaces(cwd=repo, workflow=wf)

    state = res["main_checkout"]
    assert state["updated"] is True, state
    assert state["overwritten_ignored"] == ["a"], state
    assert not (repo / "a").is_symlink(), "the ignored symlink really was destroyed"
    assert (repo / "realdir" / "b" / "keep.txt").exists(), "and its target was not"


def test_the_ancestor_walk_names_the_SYMLINK_and_not_a_path_inside_its_target(repo, tracker,
                                                                              tmp_path):
    """Shape `B` of the grid in `_doomed_ancestor`'s docstring, and it pins the ORDER on its own
    — this is the one a bottom-up walk gets wrong even if it walks THROUGH real directories.

    Same ignored symlink `a -> realdir`, but `realdir/b` is a FILE this time. Bottom-up asks
    about `a/b` first, `lexists` resolves through the symlink and finds that file, and the walk
    answers `a/b` — a path that does not exist as such and that the merge does not touch, while
    the thing that really dies, the symlink `a`, goes unnamed. So the failure here is a WRONG
    NAME rather than a missing one, which is worse for a post-mortem: the human is sent to look
    at the wrong file and finds it intact.

    Top-down reaches `a` before anything can be resolved through it."""
    _api, wf = tracker
    _ignoring(repo, "/a")
    (repo / "realdir").mkdir()
    (repo / "realdir" / "b").write_text("a FILE inside the symlink's target\n")
    (repo / "a").symlink_to("realdir")

    _land_on_origin(tmp_path, "symfile", {"a/b/c.txt": "upstream\n"}, force=True)

    res = gc_workspaces(cwd=repo, workflow=wf)

    state = res["main_checkout"]
    assert state["overwritten_ignored"] == ["a"], state
    assert not (repo / "a").is_symlink(), "the ignored symlink really was destroyed"
    assert (repo / "realdir" / "b").read_text() == "a FILE inside the symlink's target\n", (
        "and the path a bottom-up walk would have named is untouched"
    )


def test_the_ancestor_walk_goes_THROUGH_a_real_directory_to_the_victim_below_it(repo, tracker,
                                                                                tmp_path):
    """Shape `C` of the grid in `_doomed_ancestor`'s docstring, and it pins WALK-THROUGH on its
    own — the property a walk that stops at the first ancestor that exists gets wrong.

    `keep/` is an ordinary tracked directory and `keep/sub` is the human's own ignored FILE;
    upstream sends `keep/sub/new.txt`. The shallowest ancestor, `keep`, exists and is a real
    directory — so a walk that returns None there reports nothing, while the file one level down
    is what the merge deletes."""
    _api, wf = tracker
    _ignoring(repo, "keep/sub")
    (repo / "keep").mkdir()
    (repo / "keep" / "tracked.txt").write_text("makes `keep` a real, tracked directory\n")
    _git(repo, "add", "keep")
    _git(repo, "commit", "-m", "a real directory above the victim")
    _git(repo, "push", "origin", "main")
    (repo / "keep" / "sub").write_text("the human's own notes\n")
    assert _git(repo, "status", "--porcelain") == "", "invisible before, as ever"

    _land_on_origin(tmp_path, "under-real-dir", {"keep/sub/new.txt": "upstream\n"}, force=True)

    res = gc_workspaces(cwd=repo, workflow=wf)

    state = res["main_checkout"]
    assert state["overwritten_ignored"] == ["keep/sub"], state
    assert (repo / "keep" / "sub").is_dir(), "the file really was replaced by a directory"


def test_a_symlink_whose_target_already_holds_the_incoming_name_is_still_named(repo, tracker,
                                                                               tmp_path):
    """THE FOURTH displacement shape, built by this card's independent second pass, and the
    reason `_doomed_ancestor` is asked BEFORE `lexists` rather than only when `lexists` says
    "absent".

    Same ignored symlink `linkdir -> realdir`, but this time the target ALREADY holds a file of
    the incoming name. `os.path.lexists("linkdir/y.txt")` therefore answers TRUE — it follows
    every component but the last — so the incoming path took the PRESENT branch, was reported as
    displacing ITSELF, and the ancestor question was never asked at all.

    Two things then went wrong at once, and the second is why this is not a cosmetic mis-naming.
    The victim (`linkdir`, the symlink) went unnamed. And the name that WAS produced is beyond a
    symbolic link, which makes `check-ignore` exit 128 — so the whole batch was discarded and the
    ordinary ignored `shot.png` landing in the SAME commit died unreported too. Measured before
    the fix: probe `[]`, sync `updated: true` with no key, both files gone. After: both named,
    and no path beyond a symlink is ever fed to `check-ignore`, because the walk returns the
    symlink itself instead."""
    _api, wf = tracker
    _ignoring(repo, "/linkdir", "*.png")
    (repo / "realdir").mkdir()
    (repo / "realdir" / "y.txt").write_text("the target already holds this name\n")
    (repo / "linkdir").symlink_to("realdir")
    (repo / "shot.png").write_bytes(b"\x89PNG an ordinary victim in the same batch")

    _land_on_origin(tmp_path, "symtarget",
                    {"linkdir/y.txt": "upstream\n", "shot.png": "UPSTREAM\n"}, force=True)

    res = gc_workspaces(cwd=repo, workflow=wf)

    state = res["main_checkout"]
    assert sorted(state["overwritten_ignored"]) == ["linkdir", "shot.png"], state
    assert not (repo / "linkdir").is_symlink(), "the ignored symlink really was destroyed"


def test_an_incoming_file_INSIDE_a_locally_ignored_directory_names_nothing(repo, tracker,
                                                                           tmp_path):
    """The over-reporting half of "a real DIRECTORY is walked THROUGH", and the reason that
    branch cannot simply name every ancestor it finds.

    `out/` is the human's own ignored directory and upstream adds `out/new.txt` into it. Nothing
    is displaced — git creates one more file in a directory that stays — so the key must be
    ABSENT. A walk that reported its ancestors regardless would answer `out`, `check-ignore`
    would confirm `out` IS ignored, and the sync would announce a loss that did not happen. A key
    that cries wolf is read exactly as long as a key that never fires."""
    _api, wf = tracker
    _ignoring(repo, "out/")
    (repo / "out").mkdir()
    (repo / "out" / "mine.txt").write_text("the human's own scratch, and it survives\n")

    _land_on_origin(tmp_path, "into-ignored-dir", {"out/new.txt": "upstream\n"}, force=True)

    res = gc_workspaces(cwd=repo, workflow=wf)

    state = res["main_checkout"]
    assert state["updated"] is True, state
    assert "overwritten_ignored" not in state, state
    assert (repo / "out" / "mine.txt").exists(), "and nothing of the human's was touched"


def test_an_incoming_path_under_a_real_local_directory_names_nothing(repo, tracker, tmp_path):
    """The other branch of the same walk, and the one that keeps the key meaning something: an
    ordinary commit adding an ordinary new file must report NOTHING.

    `keep/` really is a directory here, so `keep/new.txt` displaces nothing at all — it is simply
    a path that does not exist yet.

    What this test does NOT pin is the `islink`/`isdir` branch itself, and the second pass
    measured that rather than letting the docstring claim it: strip the branch so every existing
    ancestor is called doomed, and the walk answers `keep` — but `keep` is TRACKED, so
    `check-ignore` drops it and the key is absent anyway (control 0 failed; that round 0 failed).
    An earlier version of this docstring said the key would then "be present on every sync",
    which is false for exactly that reason. The branch is pinned next door, by
    `test_an_incoming_file_INSIDE_a_locally_ignored_directory_names_nothing`, where the ancestor
    IS ignored and the mutant therefore reports a loss that never happened."""
    _api, wf = tracker
    _ignoring(repo, "*.png")
    (repo / "keep").mkdir()
    (repo / "keep" / "already.txt").write_text("nothing to do with the merge\n")
    _git(repo, "add", "keep")
    _git(repo, "commit", "-m", "a real directory")
    _git(repo, "push", "origin", "main")

    _land_on_origin(tmp_path, "ordinary", {"keep/new.txt": "an ordinary new file\n"})

    res = gc_workspaces(cwd=repo, workflow=wf)

    state = res["main_checkout"]
    assert state["updated"] is True, state
    assert "overwritten_ignored" not in state, state
    assert (repo / "keep" / "already.txt").exists()


def test_one_doomed_ancestor_is_named_ONCE_however_many_children_arrive(repo, tracker, tmp_path):
    """De-duplication, and it is not tidiness: `_doomed_ancestor` MAKES collisions by
    construction, because every incoming path under one dead ancestor names that same ancestor.

    Three incoming files under `out/` over one local ignored FILE `out`. Without the dedup the
    report is `out` three times — and, on a commit that adds twenty files there, twenty times
    against a cap of 50, which turns the truncation count into noise as well."""
    _api, wf = tracker
    _ignoring(repo, "/out")
    (repo / "out").write_text("the human's own scratch file\n")

    _land_on_origin(tmp_path, "manychildren",
                    {"out/x.txt": "u1\n", "out/y.txt": "u2\n", "out/deeper/z.txt": "u3\n"},
                    force=True)

    res = gc_workspaces(cwd=repo, workflow=wf)

    state = res["main_checkout"]
    assert state["overwritten_ignored"] == ["out"], state


def test_the_overwrite_report_skips_reproducible_detritus(repo, tracker, tmp_path):
    """The SAME filter as `released`, inherited for one-word-one-meaning. Its own docstring says
    the noise argument is weaker here — an incoming commit has to ADD a path under `.venv/` for
    this to fire at all — so this pins the shared behaviour, not a reason to have it twice."""
    _api, wf = tracker
    _ignoring(repo, ".venv/")
    (repo / ".venv").mkdir()
    (repo / ".venv" / "pyvenv.cfg").write_text("home = /usr\n")
    _land_on_origin(tmp_path, "one", {".venv/pyvenv.cfg": "UPSTREAM\n"}, force=True)

    res = gc_workspaces(cwd=repo, workflow=wf)

    assert res["main_checkout"]["updated"] is True
    assert "overwritten_ignored" not in res["main_checkout"], res["main_checkout"]
    assert workspace_cmd._is_reproducible_ignored(".venv/pyvenv.cfg") is True, (
        "the filter is what keeps this quiet — if that changes, this test is measuring nothing"
    )


def test_the_overwrite_report_is_capped_but_the_count_is_not(repo, tracker, tmp_path):
    """Same consumer bound as `removed_ignored`: `--gc` runs unattended and a hub process parses
    its one JSON line, so the COUNT has to survive the cap even when the names do not.

    NOT the size of the loss, here either — `_add_capped` says so where it is written and VMCP-249
    (840) took the same overclaim out of this docstring: the number is the length of the list AFTER
    every filter and give-up that produced it. That every entry in this input is a loose FILE is
    what makes it coincide with the file count, and #836 already measured the shape where it does
    not (505 destroyed paths reported as 500)."""
    _api, wf = tracker
    _ignoring(repo, "*.png")
    total = workspace_cmd._MAX_REPORTED_IGNORED + 7
    for n in range(total):
        (repo / f"shot-{n:04d}.png").write_bytes(b"\x89PNG")
    _land_on_origin(tmp_path, "one",
                    {f"shot-{n:04d}.png": "UPSTREAM\n" for n in range(total)}, force=True)

    res = gc_workspaces(cwd=repo, workflow=wf)

    state = res["main_checkout"]
    assert state["updated"] is True
    assert len(state["overwritten_ignored"]) == workspace_cmd._MAX_REPORTED_IGNORED
    assert state["overwritten_ignored_truncated"] == total


def test_a_failing_overwrite_probe_costs_the_report_and_never_the_sync(repo, tracker, tmp_path,
                                                                       monkeypatch):
    """A DIAGNOSTIC MUST NOT BECOME A GATE. The fast-forward worked before this report existed and
    has to keep working when the report cannot be computed — same best-effort class as the whole
    `main_checkout` key inside `--gc`, one level down."""
    _api, wf = tracker
    _ignoring(repo, "*.png")
    (repo / "shot.png").write_bytes(b"\x89PNG")
    monkeypatch.setattr(workspace_cmd, "_ignored_paths_the_ff_will_overwrite",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("git fell over")))
    landed = _land_on_origin(tmp_path, "one", {"shot.png": "UPSTREAM\n"}, force=True)

    res = gc_workspaces(cwd=repo, workflow=wf)

    state = res["main_checkout"]
    assert state["updated"] is True and "overwritten_ignored" not in state, state
    assert _git(repo, "rev-parse", "HEAD") == landed


def test_the_probe_leaves_the_index_of_a_REFUSED_checkout_untouched(repo, tracker, tmp_path):
    """The OUTCOME the module's standing rule asks for (`_git_inspect`'s note): the probe runs
    BEFORE the merge, including on the run where the merge is then REFUSED, and on that run
    nothing of ours may have written in a human's working directory.

    IT DOES NOT PIN THE ENV VAR, AND ITS FIRST NAME SAID IT DID. Sweep on this file: control
    0 failed; dropping `env_extra={"GIT_OPTIONAL_LOCKS": "0"}` from the probe's `git diff`
    0 failed. The reason is the SHAPE of the call rather than the flag — `HEAD..<remote>` is a
    TREE-TO-TREE comparison, and measured directly on a repo with a modified tracked file it does
    not move the index mtime with the variable or without it, nor does `check-ignore`, while
    `git status --porcelain` does. So the flag is belt, and this test is a pin on the RESULT.

    IT IS NOT INERT, though, which is the other half and needed its own round: inserting an
    un-suppressed `_run_git(("status", "--porcelain"), root, None)` into the probe gives control
    0 failed; mutation 1 failed, and the one failure is this test. It has teeth against index
    WRITES — it just does not pin the flag. (Both rounds were built by the independent second
    pass and replayed here.)"""
    _api, wf = tracker
    (repo / "README.md").write_text("hi\nWORK THE HUMAN HAS NOT COMMITTED\n")
    _land_on_origin(tmp_path, "one", {"README.md": "landed from a sibling\n"})
    index = Path(_git(repo, "rev-parse", "--git-path", "index"))
    index = index if index.is_absolute() else repo / index
    before = index.stat().st_mtime_ns

    res = gc_workspaces(cwd=repo, workflow=wf)

    assert res["main_checkout"]["code"] == workspace_cmd.MAIN_SYNC_BLOCKED
    assert index.stat().st_mtime_ns == before, (
        "the probe refreshed the index of a checkout whose merge was then refused"
    )


# --- VMCP-244 (835): `merge --ff-only` is NOT ATOMIC, and `blocked` promised that it was ---
#
# Filed by the round-2 independent review of VMCP-240 (806) and reproduced independently here
# before anything was changed, on real git 2.50.1 (Apple Git-155). `merge --ff-only` applies
# entries and can fail PART-WAY, leaving what it already wrote written — and the refusal branch
# said "the checkout is unchanged and NOTHING was discarded", which is the ONE branch where
# #806's `overwritten_ignored` probe was deliberately thrown away. So the branch that promised
# safety was the branch that could destroy an ignored file without leaving any trace.
#
# THREE SHAPES, all built rather than reasoned about, and the third is what decides the design:
#   * an unwritable DIRECTORY (`chmod 500`) -> `unable to unlink old '<p>': Permission denied`;
#   * `chflags uchg` — Finder's "Locked" checkbox — on a tracked FILE -> the same, `Operation
#     not permitted`. A checkbox, not a contrived permission, which is why the reachability
#     caveat on the card is weaker than the card thought;
#   * a shape whose ONLY casualty is the human's ignored file: `git diff --name-only HEAD` and
#     `git status --porcelain` are BOTH empty before AND after. A tracked-diff-only detector
#     reports `blocked` there and hides the loss, which is why the ignored half is fingerprinted.
#
# AND ONE REFUTATION, which changes the framing rather than the fix: the damage is NOT bounded by
# index order. A tracked `zzz.txt` sorting AFTER the locked `zlocked.txt` was applied too, so
# git's checkout loop attempts EVERY entry and writes everything it can; what survives is exactly
# what git could not write. `test_the_half_apply_is_not_bounded_by_index_order` is that pin.
#
# What KEEPS `blocked` honest is measured, not assumed: all three up-front refusals ("Your local
# changes …", "The following untracked working tree files …", "Updating the following directories
# would lose untracked files in them") write NOTHING — a second incoming file sorting first stayed
# at its old content in each, and `test_an_up_front_refusal_writes_nothing_and_stays_blocked`
# replays one of them. Read that as three measured MESSAGES, never as what the CODE promises:
# `blocked` is the fall-through whenever both probes are silent, so a checkout half-applied IN ITS
# TRACKED PATHS reports it on every LATER sweep (measured: sweep 1 `half-applied`, sweeps 2 and 3
# `blocked`, tree still mixed — that state does not heal, because the half-written paths then block
# the ff themselves as local changes). VMCP-252 (851) narrowed that from the universal it was
# written as: on the ignored-only form later sweeps report `half-applied` again and the state heals
# the moment the blocker goes — see that card's own block below.
#
# MUTATION SWEEP, ROUND ONE. One selection throughout (`tests/unit/test_workspace_cmd.py -p
# no:randomly`, no `-q`), `collected 178` and `0 errors` in every round, every round read by
# COUNTING lines that begin `FAILED ` and lines that begin `ERROR ` separately; control 0 failed:
#   * no ignored FINGERPRINT half at all ........................ control 0 failed; 4 failed
#   * tracked half is after-only, no set DIFFERENCE ............. control 0 failed; 5 failed
#   * no partial detection at all: every refusal is `blocked` ... control 0 failed; 7 failed
#   * ONE try/except around both snapshots instead of two ....... control 0 failed; 1 failed
#   * `_add_capped` drops the cap slice ........................ control 0 failed; 2 failed
#   * `_add_capped` emits the key even when empty .............. control 0 failed; 7 failed
#   * `_fingerprints` follows symlinks (`os.stat`, not `os.lstat`) control 0 failed; 0 failed
#   * the fingerprint drops the INODE .......................... control 0 failed; 0 failed
#   * the fingerprint snapshot taken AFTER the merge ............ control 0 failed; 4 failed
#   * `blocked` keeps asserting NOTHING was discarded ........... control 0 failed; 1 failed
# THE TWO ZEROS ARE DECLARED rather than discovered here and left unsaid. `os.lstat` needs a victim
# that is a SYMLINK under a partial apply and nothing here builds one, so that property is measured
# directly instead — `os.stat` on a symlink answers about the TARGET, a different inode and a path
# the merge does not touch. The inode is belt over `mtime_ns` on THIS filesystem and only earns its
# place on a coarse-mtime one, which no test here mounts; the independent second pass built that
# case on a FAT32 image and saw the inode move in both halves of it.
#
# ROUND TWO, after that second pass found a REGRESSION and five overclaims in the round-one text.
# Same selection, `collected 181` and `0 errors` in every round, control 0 failed:
#   * `blocked` claims the check ran when neither probe answered  control 0 failed; 1 failed
#   * `MAIN_SYNC_PARTIAL` renamed, rulebook left alone ......... control 0 failed; 1 failed
#   * `_tracked_changes` back to `git diff` (the regression) .... control 0 failed; 1 failed
#   * `_tracked_changes` drops `--no-renames` .................. control 0 failed; 0 failed
# The `git diff` round is the one worth reading twice: it failed EXACTLY ONE test, the new
# `test_the_partial_apply_probes_do_not_REFRESH_a_refused_checkouts_index`, and that single number
# is the measurement saying the pre-existing index pin next door cannot see this class at all — it
# has no stat-dirty-but-content-clean entry, so git never wants to write. `--no-renames` pins
# nothing: `diff-index` is plumbing and does not read `diff.renames`, so the flag is belt against a
# future default, and rename detection could only ever add a name to BOTH snapshots, where the set
# difference cancels it.

def _unwritable_dir(path: Path):
    """A directory git cannot unlink out of, restored however the test ends — otherwise pytest's
    own tmp_path teardown fails on it."""
    import contextlib

    @contextlib.contextmanager
    def _cm():
        mode = path.stat().st_mode
        path.chmod(0o500)
        try:
            yield
        finally:
            path.chmod(mode)
    return _cm()


def _half_applying_stand(repo, tmp_path, extra_local=(), extra_incoming=(), ignored_victim=True):
    """The card's own stand: tracked `aaa.txt` plus a tracked file inside a directory git will not
    be able to write, an IGNORED `shot.png` of the human's, and a sibling landing all of them.

    `ignored_victim=False` drops that screenshot from BOTH sides, which leaves the pre-merge probe
    with nothing at risk and therefore `doomed == []`. That is not an edge of the stand, it is the
    checkout with NO ignored casualty — how common that is relative to the other shape is not
    measured anywhere here, so it is described and not ranked — and VMCP-258 (860) round 2 needed
    it because an empty `doomed` is exactly where "the probe returned" stopped implying "the probe
    compared something". The tracked half-write still happens, so the checkout is half-applied
    either way; only the ignored half of the report goes away.
    """
    _ignoring(repo, "*.png")
    (repo / "aaa.txt").write_text("v1\n")
    (repo / "ro").mkdir()
    (repo / "ro" / "bbb.txt").write_text("v1\n")
    for rel in extra_local:
        (repo / rel).write_text("v1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "the state the human is sitting on")
    _git(repo, "push", "origin", "main")
    if ignored_victim:
        (repo / "shot.png").write_bytes(b"\x89PNG the human's own evidence screenshot")
    assert _git(repo, "status", "--porcelain") == "", "the ignored file is invisible, as always"

    incoming = {"aaa.txt": "v2\n", "ro/bbb.txt": "v2\n"}
    if ignored_victim:
        incoming["shot.png"] = "UPSTREAM\n"
    incoming.update({rel: "v2\n" for rel in extra_incoming})
    return _land_on_origin(tmp_path, "halfapply", incoming, force=True)


def test_a_fast_forward_that_failed_PART_WAY_is_not_reported_as_blocked(repo, tracker, tmp_path):
    """THE CARD, reproduced and then fixed. Before this, the payload for this exact input was
    `{"updated": false, "code": "blocked", "reason": "… the checkout is unchanged and NOTHING was
    discarded: error: unable to unlink old 'ro/bbb.txt': Permission denied"}` — while `aaa.txt`
    had gone v1->v2 and the human's ignored `shot.png` had been replaced by upstream's bytes.

    Both halves of that sentence were false at once, and the probe had ALREADY named `shot.png`
    on the same input: the code discarded its answer because the branch was not `updated: true`.

    Three assertions, because a code alone would not have caught the lie: the STATE is named,
    the tracked half-write is listed, and the ignored casualty is named."""
    _api, wf = tracker
    _half_applying_stand(repo, tmp_path)
    before = _git(repo, "rev-parse", "HEAD")

    with _unwritable_dir(repo / "ro"):
        res = gc_workspaces(cwd=repo, workflow=wf)

    state = res["main_checkout"]
    assert state["updated"] is False, state
    assert state["code"] == workspace_cmd.MAIN_SYNC_PARTIAL, state
    assert state["half_applied"] == ["aaa.txt"], state
    assert state["overwritten_ignored"] == ["shot.png"], state
    assert "NOTHING was discarded" not in state["reason"], state["reason"]
    assert "unable to unlink old" in state["reason"], "git's own message still rides along"
    # ...and the ground truth the report is about, so this cannot pass on a report alone.
    assert _git(repo, "rev-parse", "HEAD") == before, "HEAD really did not move"
    assert (repo / "aaa.txt").read_text() == "v2\n", "the half-apply really happened"
    assert (repo / "ro" / "bbb.txt").read_text() == "v1\n", "and stopped where git could not write"
    assert (repo / "shot.png").read_text() == "UPSTREAM\n", "the human's bytes really are gone"


@pytest.mark.skipif(not hasattr(os, "chflags"), reason="BSD file flags (macOS): no Linux analogue")
def test_the_finder_LOCKED_checkbox_produces_the_same_half_apply(repo, tracker, tmp_path):
    """The SECOND trigger, measured by 806's own second pass and re-measured here: `chflags uchg`
    is what Finder's "Locked" checkbox sets, and it makes git fail with `Operation not permitted`
    on that one file while writing everything else.

    It matters to how reachable this is, not to the fix: the card's own caveat called an
    unlinkable path rare ("permissions, file flags, a full disk, an open file on Windows"), and a
    checkbox in a file manager is not rare."""
    _api, wf = tracker
    _ignoring(repo, "*.png")
    (repo / "aaa.txt").write_text("v1\n")
    (repo / "zlocked.txt").write_text("v1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "before")
    _git(repo, "push", "origin", "main")
    (repo / "shot.png").write_bytes(b"\x89PNG the human's own evidence")
    _land_on_origin(tmp_path, "locked",
                    {"aaa.txt": "v2\n", "zlocked.txt": "v2\n", "shot.png": "UPSTREAM\n"},
                    force=True)

    os.chflags(repo / "zlocked.txt", 0x00000002)          # UF_IMMUTABLE, i.e. `chflags uchg`
    try:
        res = gc_workspaces(cwd=repo, workflow=wf)
    finally:
        os.chflags(repo / "zlocked.txt", 0)

    state = res["main_checkout"]
    assert state["code"] == workspace_cmd.MAIN_SYNC_PARTIAL, state
    assert "Operation not permitted" in state["reason"], state["reason"]
    assert state["overwritten_ignored"] == ["shot.png"], state
    assert (repo / "zlocked.txt").read_text() == "v1\n", "the locked file is what git could not do"
    assert (repo / "shot.png").read_text() == "UPSTREAM\n"


def test_the_half_apply_is_not_bounded_by_index_order(repo, tracker, tmp_path):
    """THE REFUTATION, and the reason no part of this fix reasons from ordering.

    "Everything sorting BEFORE the failure point is applied" is the natural reading of a loop that
    dies, and it is WRONG here: `zzz.txt` sorts after `ro/bbb.txt` and is applied anyway, so git's
    checkout loop attempts every entry and writes everything it can. What survives is exactly what
    git could not write — which is why the detector asks the WORKING TREE what changed instead of
    deriving a prefix of the index."""
    _api, wf = tracker
    _half_applying_stand(repo, tmp_path, extra_local=["zzz.txt"], extra_incoming=["zzz.txt"])

    with _unwritable_dir(repo / "ro"):
        res = gc_workspaces(cwd=repo, workflow=wf)

    state = res["main_checkout"]
    assert state["code"] == workspace_cmd.MAIN_SYNC_PARTIAL, state
    assert state["half_applied"] == ["aaa.txt", "zzz.txt"], state
    assert (repo / "zzz.txt").read_text() == "v2\n", (
        "a path sorting AFTER the failure was written too — the damage is not order-bounded"
    )


def _ignored_only_stand(repo, tmp_path, name="ignonly"):
    """THE SHAPE THAT DECIDES THE DESIGN, as a helper because four tests now build it.

    The incoming commit touches EXACTLY two paths: the tracked file inside a directory git will not
    be able to write, and a force-added `shot.png` this checkout ignores. So the only casualty is
    the human's ignored file, and `git status --porcelain` is empty before AND after."""
    _ignoring(repo, "*.png")
    (repo / "ro").mkdir()
    (repo / "ro" / "bbb.txt").write_text("v1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "before")
    _git(repo, "push", "origin", "main")
    (repo / "shot.png").write_bytes(b"\x89PNG the human's own evidence")
    return _land_on_origin(tmp_path, name, {"ro/bbb.txt": "v2\n", "shot.png": "UPSTREAM\n"},
                           force=True)


def test_a_half_apply_whose_only_casualty_is_an_IGNORED_file_is_still_reported(repo, tracker,
                                                                              tmp_path):
    """THE SHAPE THAT DECIDES THE DESIGN, and the one a tracked-diff detector cannot see.

    Measured on real git: `git diff --name-only HEAD` is EMPTY before AND after, `git status
    --porcelain` likewise — because the half-written path is untracked locally — while the human's
    bytes are gone. So `half_applied` is empty here, and the only thing that makes this branch
    report at all is the per-path FINGERPRINT of the paths the probe named."""
    _api, wf = tracker
    _ignored_only_stand(repo, tmp_path)

    with _unwritable_dir(repo / "ro"):
        res = gc_workspaces(cwd=repo, workflow=wf)

    state = res["main_checkout"]
    assert state["code"] == workspace_cmd.MAIN_SYNC_PARTIAL, state
    assert state["overwritten_ignored"] == ["shot.png"], state
    assert "half_applied" not in state, (
        "nothing TRACKED changed here, and a key present with an empty list is the never-read "
        "field this module keeps having to rescue"
    )
    assert _git(repo, "status", "--porcelain") == "", "git says nothing, before or after"
    assert (repo / "shot.png").read_text() == "UPSTREAM\n"


# --- VMCP-252 (851): the code was right and the SENTENCE was wrong on that very shape ---
#
# Filed by the independent review of VMCP-244 (835) — by its second pass, then reproduced by the
# reviewer personally, and reproduced a third time here before anything was changed (real git
# 2.50.1, Apple Git-155, the stand `_ignored_only_stand` builds). 835's verdict was `approve` and
# its core stands: the refusal branch no longer claims "NOTHING was discarded" and no longer throws
# away the probe's answer. What was still wrong is what the new branch PRINTS.
#
# ONE `reason` was written for the TRACKED form and emitted on BOTH, and all FOUR of its assertions
# are FALSE when the only casualty is an ignored file. Measured, in that state:
#   * "this checkout now mixes two commits" — no: `ro/bbb.txt` is still v1 and `git diff-index
#     --name-only HEAD` is empty, i.e. the tracked tree is entirely at HEAD;
#   * "`git status` shows the incoming content as the human's own uncommitted work" — no:
#     `git status --porcelain` is `''`. An ignored file is invisible to it, which is the property
#     that makes this shape undetectable by a tracked-diff probe at all, and it is written out in
#     `_fingerprints` in `workspace_cmd.py` (NOT in this file, which defines no such helper);
#   * "clearing whatever stopped the merge is not enough … every later sweep reports `blocked`" —
#     no: `chmod 700 ro` plus ONE sweep gives `{'updated': True, 'commits': 1}`. It heals fully.
#     And while the blocker stands and nothing else in the checkout changes, later sweeps report
#     `half-applied` again rather than `blocked` — "never `blocked`" would be one more universal,
#     and deleting the now-foreign file makes the very next sweep `blocked`;
#   * "A HUMAN has to commit them or drop them (`git -C <root> checkout -- <paths>`, which DISCARDS
#     them)" — there is nothing to commit and nothing to drop, and the command the report names
#     does not even take the path: in the half-applied state `shot.png` is not tracked locally, so
#     `git checkout -- shot.png` answers `error: pathspec 'shot.png' did not match any file(s)
#     known to git`, rc=1. (It answers rc=0 AFTER the ff completes, because upstream force-added
#     it — so this has to be measured in the half-applied state or it measures nothing.)
# And the one thing that DID happen — the human's bytes are gone with nothing anywhere to restore
# them from — the old sentence never said at all.
#
# THE FIX IS A SPLIT, not a rewording — and ROUND ONE OF THAT SPLIT SHIPPED A FRESH OVERCLAIM OF
# THE VERY CLASS IT WAS FIXING, which the independent second pass built and this file now pins. It
# branched on `not half`, reasoning that an empty `half` means the ignored-only form. An empty
# `half` is THREE states. Besides "the probe FAILED" (`tracked_after is None`) there is "the probe
# ANSWERED AND WAS BLIND", because `half` is a SET DIFFERENCE: a tracked file the human had locally
# DELETED which the incoming commit also modifies is in the before set AND the after set, so it
# cancels — and the ignored-only prose then went out over a tree that was really mixed and really
# did not heal (`git status` ` M aaa.txt`, the file at upstream's v2 against HEAD's v1, sweeps 2
# and 3 `blocked` with the blocker CLEARED). `_tracked_changes` states that bound about itself ("at
# worst it hides one"); reading it as proof is what cost the round. So the only branch that may
# claim a quiet tree is the one where `git diff-index` came back EMPTY afterwards, which is a
# direct reading and was already computed. The three pins are
# `test_an_empty_half_applied_is_never_reported_as_a_quiet_TRACKED_tree` (blind-but-answered),
# `test_a_failing_half_apply_check_costs_the_report_and_never_the_verdict` (unanswered, which
# already built that state) and the ignored-only reason test (the empty, provable case).
#
# THE LOSS SENTENCE IS HEDGED FOR THE SAME REASON. Round one said "the human's own are GONE and
# NOTHING can recover them — no git object ever held them", and both halves are false on inputs
# this branch cannot tell apart from the stand: a path force-added once and un-staged with
# `git rm --cached` leaves `status` EMPTY and the human's blob in the object store (`git cat-file
# -p` hands the bytes back, `git fsck` calls it dangling), and an ignored file whose bytes already
# equalled upstream's lost nothing at all. `test_the_overwritten_ignored_bytes_are_not_always_
# unrecoverable` is the first of those, built rather than argued.
#
# WHAT ROUND TWO PARKED AND A HUMAN THEN ANSWERED: `overwritten_ignored` rang on EVERY sweep while
# the blocker stood (measured: three consecutive sweeps, `['shot.png']` each time, because every
# failed attempt unlinks and recreates the file so the fingerprint moves again), and a FOURTH time
# on the `updated: true` sweep that finally heals it — four reports for one loss. That is the
# never-read failure VMCP-68 had to split `kept`/`expected` to cure, while silencing it is the
# one-way reading the whole #710 -> #806 -> #835 chain defends, so WHICH of the two to give up was
# a product decision and 851 parked it (`call_human`). The answer was to filter, on the REFUSAL
# branch only, a path this run can positively show already holds the incoming bytes — accepted
# because it also removes a FALSE POSITIVE (an ignored file whose bytes already equalled
# upstream's), i.e. it makes the key truer rather than merely quieter. The pin that recorded the
# status quo said in its own docstring that this answer would have to move it; it did, and it is
# now `test_the_ignored_only_loss_is_reported_ONCE_and_not_on_every_later_sweep`. Four reports
# become two: the `updated: true` branch keeps its unfiltered list by decision, pinned next door.
#
# MUTATION SWEEP. One selection throughout — `tests/unit/test_workspace_cmd.py -p no:randomly -k
# "half_appl or ignored_only or IGNORED_file or up_front_refusal or partial_apply or overwritten or
# PART_WAY or LOCKED_checkbox"`, no `-q` — `collected 197 items / 177 deselected / 20 selected` and
# `0` ERROR lines in EVERY round, each round read by counting lines that begin `FAILED ` and lines
# that begin `ERROR ` separately; control 0 failed:
#   * one `reason` for both forms (the pre-851 shape) ............ control 0 failed; 3 failed
#   * round one's shape: a BLIND probe reads as a quiet tree ..... control 0 failed; 1 failed
#   * round one's shape: a FAILED probe reads as a quiet tree .... control 0 failed; 1 failed
#   * the tracked form never names the ignored loss .............. control 0 failed; 1 failed
#   * the loss sentence back to the flat overclaim ............... control 0 failed; 3 failed
#   * `half-applied` requires a TRACKED half (835's core) ....... control 0 failed; 7 failed
# The two round-one rounds are the ones worth reading twice: each kills exactly ONE test, and they
# kill DIFFERENT ones, which is the measurement saying the blind-probe pin is not a duplicate of
# the pre-existing failed-probe state — without the new test, round one's own defect ships green.
# An EARLIER sweep over the whole file (`collected 195`, control 0 failed) measured round one's
# pins before the rework: 2, 1, 1, 1 and 5 failed for the same first, third, fourth, fifth and
# sixth rows. It is kept out of the table above because it is a different selection AND different
# code; two rounds of a sweep are not comparable just because their labels match.

def test_the_ignored_only_half_applys_reason_says_what_actually_happened(repo, tracker, tmp_path):
    """The four false assertions, each one asserted ABSENT, and the true one asserted present.

    A substring pin over prose is normally a bad trade; here the prose IS the deliverable — this
    string is the only thing a human ever sees about a loss that already happened — and every one
    of these four phrases was measured false on this exact input before it was split."""
    _api, wf = tracker
    _ignored_only_stand(repo, tmp_path)

    with _unwritable_dir(repo / "ro"):
        res = gc_workspaces(cwd=repo, workflow=wf)

    state = res["main_checkout"]
    reason = state["reason"]
    assert state["code"] == workspace_cmd.MAIN_SYNC_PARTIAL, state
    assert "half_applied" not in state, state
    assert "mixes two commits" not in reason, reason
    assert "uncommitted work" not in reason, reason
    assert "checkout --" not in reason, reason
    assert "every later sweep reports" not in reason, reason
    assert "Nothing tracked differs from HEAD at all" in reason, reason
    assert "not recoverable from anything HERE" in reason, reason
    # ROUND THREE moved this one line. It used to read "nothing is left here to block the merge
    # again … the next sweep completes the fast-forward (measured)", which the independent review
    # falsified on a stand this one is one file away from — see the C-stand test below. The claim
    # is now conditional on a probe and says what it looked at.
    assert "Nothing TRACKED is left here to block the merge again" in reason, reason
    assert "no incoming path was left behind in a state git would refuse to merge over" in reason
    assert "that is what was CHECKED on this run" in reason, reason
    assert "unable to unlink old" in reason, "git's own message still rides along"


def test_the_TRACKED_half_apply_keeps_every_word_it_measured(repo, tracker, tmp_path):
    """The other side of the split: narrowing the sentence must not COST the form it was true of.

    On the tracked form all four assertions hold — 835 measured them — so they stay, and the
    ignored casualty that rode along on the same input gains its own sentence instead of being
    covered by advice that cannot reach it (`checkout --` is offered for the tracked paths and
    named as inapplicable to the untracked ones)."""
    _api, wf = tracker
    _half_applying_stand(repo, tmp_path)

    with _unwritable_dir(repo / "ro"):
        res = gc_workspaces(cwd=repo, workflow=wf)

    state = res["main_checkout"]
    reason = state["reason"]
    assert state["half_applied"] == ["aaa.txt"], state
    assert state["overwritten_ignored"] == ["shot.png"], state
    assert "mixes two commits" in reason, reason
    assert "every later sweep reports `blocked`" in reason, reason
    assert "checkout -- <paths>" in reason, reason
    assert "not recoverable from anything HERE" in reason, "the ignored loss is named here too"


def test_the_ignored_only_half_apply_HEALS_once_the_blocker_is_cleared(repo, tracker, tmp_path):
    """THE THIRD false assertion, refuted by running it rather than by reading the diff.

    "It does NOT heal itself, and clearing whatever stopped the merge is not enough" is true of the
    tracked form, where the half-written paths themselves then block the ff as local changes. Here
    there are none: the tracked tree never moved, so the moment the blocker is gone the ordinary
    fast-forward completes and the checkout is current."""
    _api, wf = tracker
    _ignored_only_stand(repo, tmp_path)

    with _unwritable_dir(repo / "ro"):
        first = gc_workspaces(cwd=repo, workflow=wf)["main_checkout"]
    assert first["code"] == workspace_cmd.MAIN_SYNC_PARTIAL, first

    second = workspace_cmd.sync_main_checkout(repo)

    assert second["updated"] is True, second
    assert second["commits"] == 1, second
    assert (repo / "ro" / "bbb.txt").read_text() == "v2\n", "the ff really finished"


def test_the_ignored_only_loss_is_reported_ONCE_and_not_on_every_later_sweep(repo, tracker,
                                                                             tmp_path):
    """DEFECT 2, and the assertion this replaces was written to be replaced by exactly this.

    It used to pin the status quo — sweeps 1, 2 and 3 all `half-applied` and all naming `shot.png`
    — and said in its own docstring that if the answer came back "filter a path that already holds
    the incoming bytes", the list becomes absent from sweeps 2 and 3 and the pin has to move
    deliberately. That is the answer a human gave (851's `call_human`, option C), so it moved.

    THE REPEAT WAS NEVER A SECOND LOSS. Each failed attempt unlinks and recreates the ignored path,
    so the FINGERPRINT moves again (measured before the fix: three sweeps, inodes 212809910 ->
    212810669 -> 212811229) over content that has been upstream's since sweep 1. The fingerprint is
    still the right question — it is what a REWRITE looks like — and `_paths_already_holding_
    incoming_bytes` is the second half it never had: was anybody's content actually displaced?

    `blocked` afterwards is the documented fall-through (both probes silent), the same answer this
    module already gives a half-apply whose only casualty was filtered as regenerable detritus."""
    _api, wf = tracker
    _ignored_only_stand(repo, tmp_path)

    with _unwritable_dir(repo / "ro"):
        states = [gc_workspaces(cwd=repo, workflow=wf)["main_checkout"],
                  workspace_cmd.sync_main_checkout(repo),
                  workspace_cmd.sync_main_checkout(repo)]

    assert states[0]["code"] == workspace_cmd.MAIN_SYNC_PARTIAL, states[0]
    assert states[0]["overwritten_ignored"] == ["shot.png"], "sweep 1 is where the bytes died"
    assert [s["code"] for s in states[1:]] == [workspace_cmd.MAIN_SYNC_BLOCKED] * 2, states
    assert all("overwritten_ignored" not in s for s in states[1:]), (
        "one loss, one message — and an absent key rather than an empty one, which is the "
        "never-read field this module keeps having to rescue"
    )
    # The ground truth, so this cannot pass on a payload alone: the file really was rewritten again
    # on every sweep, which is why the fingerprint alone could never have told them apart.
    assert (repo / "shot.png").read_text() == "UPSTREAM\n"
    assert _git(repo, "status", "--porcelain") == "", "and the tracked tree is still not mixed"
    assert (repo / "ro" / "bbb.txt").read_text() == "v1\n"


def test_a_RESTORED_ignored_file_dying_a_second_time_is_reported_afresh(repo, tracker, tmp_path):
    """The other half of DEFECT 2's fix, and the reason it is not just "report once".

    Silence has to be a property of the CONTENT, not a latch on the path: if the human puts their
    own bytes back and the next sweep destroys them again, that is a NEW loss and it is named. The
    discriminator gives this for free — before that sweep the file no longer equals the incoming
    blob — where a "seen it already" memo would have swallowed it."""
    _api, wf = tracker
    _ignored_only_stand(repo, tmp_path)

    with _unwritable_dir(repo / "ro"):
        first = gc_workspaces(cwd=repo, workflow=wf)["main_checkout"]
        quiet = workspace_cmd.sync_main_checkout(repo)
        (repo / "shot.png").write_bytes(b"\x89PNG the human put it back")
        again = workspace_cmd.sync_main_checkout(repo)

    assert first["overwritten_ignored"] == ["shot.png"], first
    assert "overwritten_ignored" not in quiet, quiet
    assert again["code"] == workspace_cmd.MAIN_SYNC_PARTIAL, again
    assert again["overwritten_ignored"] == ["shot.png"], "their bytes died a second time"
    assert (repo / "shot.png").read_text() == "UPSTREAM\n"


def test_an_ignored_file_that_ALREADY_held_upstreams_bytes_is_no_longer_a_false_positive(
        repo, tracker, tmp_path):
    """The second false positive the same discriminator removes — this one is not about repeats.

    A human whose ignored file happens to hold exactly what upstream is bringing loses NOTHING when
    the merge rewrites it, and the key named it anyway on the FIRST sweep. `_partial_apply_reason`
    already had to hedge for this input ("if the human's bytes already equalled upstream's, nothing
    was lost at all") because the probe could not tell; now it can, on the refusal branch. So this
    is what makes the fix TRUTHFUL rather than merely quieter, which is the ground the human's
    answer stood on."""
    _api, wf = tracker
    _ignoring(repo, "*.png")
    (repo / "ro").mkdir()
    (repo / "ro" / "bbb.txt").write_text("v1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "before")
    _git(repo, "push", "origin", "main")
    (repo / "shot.png").write_text("UPSTREAM\n")            # byte-identical to what is coming
    _land_on_origin(tmp_path, "sameb", {"ro/bbb.txt": "v2\n", "shot.png": "UPSTREAM\n"}, force=True)

    with _unwritable_dir(repo / "ro"):
        state = gc_workspaces(cwd=repo, workflow=wf)["main_checkout"]

    assert "overwritten_ignored" not in state, state
    assert state["code"] == workspace_cmd.MAIN_SYNC_BLOCKED, (
        "with nothing lost and nothing tracked written, both probes are silent and the "
        "documented fall-through is the honest answer"
    )
    assert (repo / "ro" / "bbb.txt").read_text() == "v1\n", "the ff really did not finish"


def test_a_doomed_ANCESTOR_is_still_reported_because_the_discriminator_cannot_be_asked(
        repo, tracker, tmp_path):
    """EVERY UNANSWERABLE READ FALLS TOWARDS REPORTING, built rather than asserted.

    The human's answer named this branch explicitly: a doomed ANCESTOR is not a blob in the incoming
    tree at all, so the two halves cannot be compared and the path must keep its place. Here the
    human's own ignored FILE `out` is what dies to make room for the incoming `out/x.txt`, and
    `git cat-file` answers `tree` for `<remote>:out` — no blob, no comparison, and `out` is named,
    which is right, because `out` is precisely the thing about to be destroyed."""
    _api, wf = tracker
    _ignoring(repo, "out\nout/\n")
    (repo / "ro").mkdir()
    (repo / "ro" / "bbb.txt").write_text("v1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "before")
    _git(repo, "push", "origin", "main")
    (repo / "out").write_text("the human's own ignored FILE\n")
    _land_on_origin(tmp_path, "ancest", {"ro/bbb.txt": "v2\n", "out/x.txt": "hi\n"}, force=True)

    with _unwritable_dir(repo / "ro"):
        state = gc_workspaces(cwd=repo, workflow=wf)["main_checkout"]

    assert state["code"] == workspace_cmd.MAIN_SYNC_PARTIAL, state
    assert state["overwritten_ignored"] == ["out"], state
    assert (repo / "out").is_dir(), "their file really was replaced by the incoming directory"


def test_the_updated_branch_keeps_its_UNFILTERED_list(repo, tracker, tmp_path):
    """The half of the fix that was deliberately NOT made, and it needs a pin BECAUSE it is a cost.

    The same path is named a second time by the sweep that finally completes the fast-forward, so
    one loss still costs TWO messages rather than one. That branch's list is unfiltered by design —
    the merge completed, so everything incoming was written — and the human who chose this fix
    scoped it to the refusal branch in those words. Pinned so that "surely it should be filtered
    there too" is a decision somebody makes on purpose."""
    _api, wf = tracker
    _ignored_only_stand(repo, tmp_path)

    with _unwritable_dir(repo / "ro"):
        first = gc_workspaces(cwd=repo, workflow=wf)["main_checkout"]
    healed = workspace_cmd.sync_main_checkout(repo)

    assert first["overwritten_ignored"] == ["shot.png"], first
    assert healed["updated"] is True, healed
    assert healed["overwritten_ignored"] == ["shot.png"], (
        "named again on the branch that does not filter — two messages for one loss, by decision"
    )


# --- VMCP-252 (851) ROUND THREE: the round-two FIX shipped a fresh instance of the same class ---
#
# Filed by the independent review of `292fdfc` — this card's own second landing — and by that
# review's second pass after it, then reproduced here from scratch before anything was changed.
# The quiet branch had gained a third sentence: "And unlike the tracked form nothing is left here
# to block the merge again: clearing whatever stopped it is enough, and the next sweep completes
# the fast-forward (measured)." The word doing the damage is "(measured)": it WAS measured, on
# `_ignored_only_stand`, whose own docstring says the incoming commit "touches EXACTLY two paths".
#
# `_tracked_changes` is `git diff-index`, and its own docstring names the hole — it cannot see an
# UNTRACKED path. A merge that fails PART-WAY writes new incoming files to disk without moving the
# index, so a NEW non-ignored file in the incoming range lands untracked, `diff-index` stays empty
# (which is why this is still the quiet branch), and git then refuses every later merge over it.
# Measured on the three-path stand below: sweep 1 `half-applied` with the healing sentence and
# `git status` `?? brandnew.txt`; sweeps 2-5 `blocked` on "The following untracked working tree
# files would be overwritten by merge: brandnew.txt", HEAD never reaching the remote, the blocker
# CLEARED since sweep 2. Removing that one file is what unblocks it, and the report named neither.
#
# TWO THINGS MAKE THIS WORSE THAN AN INHERITED BLIND SPOT, both from the review's second pass.
# On this input the round-two fix REPLACED A TRUE VERDICT WITH A FALSE ONE — the pre-851 sentence
# ("It does NOT heal itself … every later sweep reports `blocked`") is CORRECT here. And the false
# sentence was ratified by an assertion in this file, so no mutation could ever have killed it:
# not one stand landed a new non-ignored path beside a blocked tracked one.
#
# THE FIX RESTS ON A PROBE, NOT ON A STAND'S SHAPE. `_incoming_paths_absent_here` is taken BEFORE
# the merge (afterwards a left-behind file is indistinguishable from one the human always had) and
# `_untracked_left_behind` keeps the ones that reappeared and that git does not ignore — the
# ignored ones do not block, they get overwritten, which is the OTHER key's subject entirely.
#
# MUTATION SWEEP FOR ROUNDS THREE AND FOUR. One selection throughout —
# `tests/unit/test_workspace_cmd.py -p no:randomly -k "half_appl or ignored_only or IGNORED_file or
# up_front_refusal or partial_apply or overwritten or PART_WAY or LOCKED_checkbox or left_behind or
# ANCESTOR or RESTORED or UNFILTERED or quiet_branch or ALREADY_held or unanswerable_left or
# RAW_bytes"`, no `-q` so the `collected` line exists — and it read `collected 214 items / 179
# deselected / 35 selected` with 0 `ERROR ` lines in EVERY round, each round read by counting lines
# beginning `FAILED ` and lines beginning `ERROR ` separately rather than off pytest's summary,
# which lands inside the literal `control 0 failed` these very docstrings print. Mutations were
# applied in a SEPARATE clone with `__pycache__` deleted and `PYTHONDONTWRITEBYTECODE=1`, and
# `vikunja_mcp.__file__` was printed every round and pointed into that clone.
# control 0 failed; drop the option-C filter entirely 3 failed; filter the `updated: true` branch
# too 1 failed; read the discriminator AFTER the merge instead of before 16 failed; an unanswerable
# read falls towards FILTERING instead of reporting 1 failed; always print the reassurance (round
# three's shape, both guarded arms dead) 2 failed; count IGNORED left-behind paths as blockers too
# 1 failed; `_incoming_paths_absent_here` drops its absent-only filter 2 failed; hash WITH filters
# (round three's shape, where an eol attribute hides a loss) 1 failed; an unanswered left-behind
# probe reads as `no blockers` 1 failed; control again 0 failed.
# The last two rows are the round-four pins and each kills exactly one test, so neither is a
# duplicate of anything round three already had. The 16 is not a stronger pin than the 1s — reading
# the discriminator after the merge filters the loss on the FIRST sweep too, so it takes out most of
# the section at once; the narrow rows are the ones that say a specific pin is load-bearing.

def _left_behind_stand(repo, tmp_path, name="leftbehind"):
    """`_ignored_only_stand` plus ONE more incoming path: a NEW file this repo does not ignore.

    That is the entire difference, and it is the difference between "heals once the blocker is
    cleared" and "stuck until a human removes something" — which is why the two-path stand could
    measure the healing claim and be right, and be useless as evidence about the FORM."""
    _ignoring(repo, "*.png")
    (repo / "ro").mkdir()
    (repo / "ro" / "bbb.txt").write_text("v1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "before")
    _git(repo, "push", "origin", "main")
    (repo / "shot.png").write_bytes(b"\x89PNG the human's own evidence")
    return _land_on_origin(tmp_path, name, {"ro/bbb.txt": "v2\n", "shot.png": "UPSTREAM\n",
                                            "brandnew.txt": "hello\n"}, force=True)


def test_the_quiet_branch_no_longer_promises_healing_when_the_merge_left_a_blocker(repo, tracker,
                                                                                   tmp_path):
    """ROUND THREE. The sentence is now conditional, and the condition is a probe.

    Both halves are asserted, because a report is only as good as the tree under it: the prose must
    refuse to promise healing AND must name the file, and the ground truth must be the stuck
    checkout that makes the refusal necessary — including that the blocker being cleared is not
    enough, which is exactly what the old sentence said it would be."""
    _api, wf = tracker
    _left_behind_stand(repo, tmp_path)

    with _unwritable_dir(repo / "ro"):
        state = gc_workspaces(cwd=repo, workflow=wf)["main_checkout"]
    reason = state["reason"]

    assert state["code"] == workspace_cmd.MAIN_SYNC_PARTIAL, state
    assert "half_applied" not in state, "still the quiet branch — `diff-index` is empty"
    assert "Nothing tracked differs from HEAD at all" in reason, reason
    assert "This will NOT heal on its own" in reason, reason
    assert "brandnew.txt" in reason, "and it NAMES the file a human has to remove"
    assert "A HUMAN moving or removing them" in reason, reason
    # Round four narrowed this too: "every later sweep will report `blocked`" ignored an upstream
    # commit that later DROPS the path, which the second pass built and which heals with no human.
    assert "for as long as that path is here AND the incoming range still carries it" in reason
    assert "expected to complete the fast-forward" not in reason, reason
    # Ground truth: untracked, not ignored, and put there by the merge rather than by the human.
    assert _git(repo, "status", "--porcelain") == "?? brandnew.txt", _git(repo, "status", "-s")
    assert _git(repo, "ls-files", "brandnew.txt") == "", "the index never heard of it"
    # ...and it really does not heal. The blocker is released by leaving the `with` above.
    later = [workspace_cmd.sync_main_checkout(repo), workspace_cmd.sync_main_checkout(repo)]
    assert [s["code"] for s in later] == [workspace_cmd.MAIN_SYNC_BLOCKED] * 2, later
    assert (repo / "ro" / "bbb.txt").read_text() == "v1\n", "HEAD never reached the remote"
    # And the one command the report asks for is the one that works.
    (repo / "brandnew.txt").unlink()
    assert workspace_cmd.sync_main_checkout(repo)["updated"] is True
    assert (repo / "ro" / "bbb.txt").read_text() == "v2\n"


def test_an_unanswerable_left_behind_probe_never_prints_the_reassurance(repo, tracker, tmp_path,
                                                                        monkeypatch):
    """ROUND FOUR: the round-three fix could not tell "looked, found none" from "could not look".

    The probe returned `[]` on failure, which is the same value as "nothing was left behind", so
    all three silent routes — either `except` wrapper and an unreadable `git diff` — printed the
    full reassurance over a checkout that then answered `blocked` forever. That is the identical
    defect the TRACKED half was bounced for and has guarded with `looked` since #835. Built here on
    the input where the reassurance is FALSE, so a green run means the refusal fires exactly where
    it is needed rather than everywhere."""
    _api, wf = tracker
    _left_behind_stand(repo, tmp_path)
    monkeypatch.setattr(workspace_cmd, "_incoming_paths_absent_here",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("git said no")))

    with _unwritable_dir(repo / "ro"):
        state = gc_workspaces(cwd=repo, workflow=wf)["main_checkout"]
    reason = state["reason"]

    assert state["code"] == workspace_cmd.MAIN_SYNC_PARTIAL, state
    assert "could NOT be checked on this run" in reason, reason
    assert "expected to complete the fast-forward" not in reason, reason
    assert "This will NOT heal on its own" not in reason, "it does not claim the opposite either"
    # ...and the reassurance it declined to print would have been FALSE.
    later = workspace_cmd.sync_main_checkout(repo)          # blocker released with the `with`
    assert later["code"] == workspace_cmd.MAIN_SYNC_BLOCKED, later


def test_the_filter_compares_RAW_bytes_so_an_eol_attribute_cannot_hide_a_loss(repo, tracker,
                                                                              tmp_path):
    """ROUND FOUR, and it is a CORRECTNESS bug the second pass found in round three's own filter.

    The first version hashed the way `git hash-object <path>` does — through this checkout's
    filters — reasoning that the incoming side is git's stored form. But `clean` need not be
    invertible, so an equal hash does not mean equal bytes, and the filter then DROPS a path whose
    content really did die. This is the counterexample that needs no filter configuration at all:
    one committed `.gitattributes` saying `text eol=lf` and a working copy with CRLF. Measured
    directly on git 2.50.1 — the CRLF file hashes to `fbbee861…` with filters, byte-identical to
    the LF blob, and to `17f2fc0a…` with `--no-filters`.

    The residual error now runs the safe way: with a SMUDGE filter configured a file that already
    equals what the merge will write is reported anyway — noise rather than silence."""
    _api, wf = tracker
    _ignoring(repo, "*.png")
    (repo / ".gitattributes").write_text("shot.png -diff\nnotes.txt text eol=lf\n")
    (repo / "ro").mkdir()
    (repo / "ro" / "bbb.txt").write_text("v1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "before")
    _git(repo, "push", "origin", "main")
    (repo / "notes.txt").write_bytes(b"alpha\r\nbeta\r\n")   # the human's CRLF copy
    _ignoring(repo, "*.png\nnotes.txt\n")                    # ...and it is ignored here
    _land_on_origin(tmp_path, "eol", {"ro/bbb.txt": "v2\n", "notes.txt": "alpha\nbeta\n"},
                    force=True)

    with _unwritable_dir(repo / "ro"):
        state = gc_workspaces(cwd=repo, workflow=wf)["main_checkout"]

    assert (repo / "notes.txt").read_bytes() == b"alpha\nbeta\n", (
        "ground truth first: the bytes on disk really did change"
    )
    assert state["code"] == workspace_cmd.MAIN_SYNC_PARTIAL, state
    assert state["overwritten_ignored"] == ["notes.txt"], (
        "a filtered hash would have called this 'already upstream' and said nothing"
    )


def test_an_IGNORED_file_left_behind_is_not_counted_as_a_blocker(repo, tracker, tmp_path):
    """The narrowing that keeps the new warning from crying wolf, and it is not symmetry for its
    own sake: git does not refuse over an ignored path, it overwrites it silently. So a left-behind
    `*.png` must NOT turn the healing sentence off — measured here by letting the ff finish."""
    _api, wf = tracker
    _ignoring(repo, "*.png")
    (repo / "ro").mkdir()
    (repo / "ro" / "bbb.txt").write_text("v1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "before")
    _git(repo, "push", "origin", "main")
    (repo / "shot.png").write_bytes(b"\x89PNG the human's own evidence")
    _land_on_origin(tmp_path, "ignleft", {"ro/bbb.txt": "v2\n", "shot.png": "UPSTREAM\n",
                                          "fresh.png": "NEW\n"}, force=True)

    with _unwritable_dir(repo / "ro"):
        state = gc_workspaces(cwd=repo, workflow=wf)["main_checkout"]

    assert state["code"] == workspace_cmd.MAIN_SYNC_PARTIAL, state
    assert (repo / "fresh.png").exists(), "the part-way merge really did leave it here"
    assert "This will NOT heal on its own" not in state["reason"], state["reason"]
    # The wording has to survive this input being TRUE-but-irrelevant: a path WAS left behind
    # untracked, it just is not one git will refuse over. Round four narrowed the sentence for
    # exactly this reason, after the second pass measured the flat version literally false here.
    assert "left behind in a state git would refuse to merge over" in state["reason"]
    assert "ignored ones are excluded" in state["reason"], state["reason"]
    second = workspace_cmd.sync_main_checkout(repo)         # blocker released with the `with`
    assert second["updated"] is True, f"and the claim holds: {second}"


def test_an_empty_half_applied_is_never_reported_as_a_quiet_TRACKED_tree(repo, tracker, tmp_path):
    """ROUND TWO OF THIS CARD, and the fresh overclaim its OWN first fix shipped — built by the
    independent second pass, reproduced here on a real stand, and the reason the split branches on
    `git diff-index` AFTERWARDS instead of on `not half`.

    `half` is a SET DIFFERENCE, so a path that is in BOTH snapshots cancels. One ordinary human act
    builds that: they deleted a tracked file locally, and the incoming commit also modifies it. The
    probe ANSWERS, `half` comes back empty, and the first fix then printed the ignored-only prose —
    "Nothing TRACKED moved … `git status` is silent … this DOES heal" — over a tree that really was
    mixed and really did not heal. Measured then: `git status` ` M aaa.txt`, the file holding
    upstream's v2 against HEAD's v1, and sweeps 2 and 3 answering `blocked` with the blocker CLEARED
    and HEAD never reaching the remote. `_tracked_changes` states that bound about itself; this is
    what reading it as proof costs.

    So the assertions are BOTH halves: the report must refuse to claim silence, and the ground truth
    must be the mixed tree that makes the refusal necessary."""
    _api, wf = tracker
    _ignoring(repo, "*.png")
    (repo / "aaa.txt").write_text("v1\n")
    (repo / "ro").mkdir()
    (repo / "ro" / "bbb.txt").write_text("v1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "before")
    _git(repo, "push", "origin", "main")
    (repo / "shot.png").write_bytes(b"\x89PNG the human's own evidence")
    (repo / "aaa.txt").unlink()                  # the human deleted a tracked file, no ceremony
    _land_on_origin(tmp_path, "cancels",
                    {"aaa.txt": "v2\n", "ro/bbb.txt": "v2\n", "shot.png": "UPSTREAM\n"}, force=True)

    with _unwritable_dir(repo / "ro"):
        state = gc_workspaces(cwd=repo, workflow=wf)["main_checkout"]
    reason = state["reason"]

    assert state["code"] == workspace_cmd.MAIN_SYNC_PARTIAL, state
    assert "half_applied" not in state, "the set difference really did cancel"
    assert "Nothing tracked differs from HEAD" not in reason, reason
    assert "nothing is left here to block the merge again" not in reason, reason
    assert "UNCLEAR rather than nothing" in reason, reason
    assert "do not assume this heals on its own" in reason, reason
    # The ground truth the refusal is about, so this cannot pass on a report alone.
    assert (repo / "aaa.txt").read_text() == "v2\n", "the merge DID write a tracked path"
    assert _git(repo, "show", "HEAD:aaa.txt") == "v1", "while HEAD still says v1 — mixed"
    assert _git(repo, "status", "--porcelain") == "M aaa.txt", (   # the helper strips the XY column
        "git is not silent here — which is the whole point"
    )
    second = workspace_cmd.sync_main_checkout(repo)         # blocker released with the `with`
    assert second["code"] == workspace_cmd.MAIN_SYNC_BLOCKED, (
        f"and it does NOT heal: {second}"
    )


def test_the_overwritten_ignored_bytes_are_not_always_unrecoverable(repo, tracker, tmp_path):
    """Why the loss sentence is HEDGED, measured rather than conceded — also the second pass.

    The flat version said "the human's own are GONE and NOTHING can recover them — no git object
    ever held them", and this state disproves the second clause and therefore the first: the human
    force-added their screenshot once and un-staged it (`git rm --cached`), which leaves
    `git status --porcelain` EMPTY — byte-identical in every signal to the plain stand — and their
    blob in the object store. `_ignored_of` asks `check-ignore` without `--no-index`, so that path
    is still ignored and still doomed, the report still fires, and `git cat-file -p` then hands the
    original bytes straight back.

    The neighbouring state is the mirror and needs no test of its own because the probe's own
    docstring already states it: an ignored file whose bytes ALREADY equalled upstream's lost
    nothing at all, and the key names paths that were WRITTEN, never paths whose content differed.
    """
    _api, wf = tracker
    _ignoring(repo, "*.png")
    (repo / "ro").mkdir()
    (repo / "ro" / "bbb.txt").write_text("v1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "before")
    _git(repo, "push", "origin", "main")
    # Text, not the usual PNG magic, only so that the recovery assertion can READ the bytes back
    # through the same text-mode helper every other test here uses. The rule is on the NAME.
    (repo / "shot.png").write_text("the human's own evidence\n")
    _git(repo, "add", "-f", "shot.png")
    _git(repo, "rm", "--cached", "shot.png")
    human_blob = _git(repo, "hash-object", "shot.png")
    assert _git(repo, "status", "--porcelain") == "", "indistinguishable from the plain stand"
    _land_on_origin(tmp_path, "staged", {"ro/bbb.txt": "v2\n", "shot.png": "UPSTREAM\n"}, force=True)

    with _unwritable_dir(repo / "ro"):
        state = gc_workspaces(cwd=repo, workflow=wf)["main_checkout"]

    assert state["overwritten_ignored"] == ["shot.png"], state
    assert "`git fsck --lost-found`" in state["reason"], state["reason"]
    assert (repo / "shot.png").read_text() == "UPSTREAM\n", "the overwrite really happened"
    assert _git(repo, "cat-file", "-p", human_blob) == "the human's own evidence", (
        "and the bytes the flat sentence called unrecoverable come back out of the object store"
    )


def test_an_up_front_refusal_writes_nothing_and_stays_blocked(repo, tracker, tmp_path):
    """THE CONTRAST that keeps `blocked` a useful word, and it is measured rather than argued.

    git checks the whole update BEFORE writing any of it, so a refusal for a local modification
    aborts with nothing written: measured on all three up-front refusals, a second incoming file
    sorting FIRST kept its old content. Here `aaa.txt` is that witness. So this branch keeps
    `blocked`, gains no key, and is what makes the new code mean something."""
    _api, wf = tracker
    (repo / "aaa.txt").write_text("v1\n")
    (repo / "coll.txt").write_text("v1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "before")
    _git(repo, "push", "origin", "main")
    before = _git(repo, "rev-parse", "HEAD")
    (repo / "coll.txt").write_text("THE HUMAN IS EDITING THIS\n")
    _land_on_origin(tmp_path, "upfront", {"aaa.txt": "v2\n", "coll.txt": "UPSTREAM\n"})

    res = gc_workspaces(cwd=repo, workflow=wf)

    state = res["main_checkout"]
    assert state["code"] == workspace_cmd.MAIN_SYNC_BLOCKED, state
    assert "half_applied" not in state and "overwritten_ignored" not in state, state
    assert _git(repo, "rev-parse", "HEAD") == before
    assert (repo / "aaa.txt").read_text() == "v1\n", (
        "a path sorting before the refused one stayed put — the up-front check really is up-front"
    )
    assert (repo / "coll.txt").read_text() == "THE HUMAN IS EDITING THIS\n"


def test_the_humans_own_pre_existing_edit_is_never_called_half_applied(repo, tracker, tmp_path):
    """Why the tracked half is a SET DIFFERENCE and not just "what differs from HEAD afterwards".

    The refusal above happens BECAUSE the human has `coll.txt` modified, so an after-only reading
    would report the human's own in-flight edit as something this tool's merge wrote — on the most
    ordinary refusal there is. The before-snapshot is what tells the two apart, and here the
    half-apply is real (`aaa.txt`) while `coll.txt` must not be in the list."""
    _api, wf = tracker
    _ignoring(repo, "*.png")
    (repo / "aaa.txt").write_text("v1\n")
    (repo / "mine.txt").write_text("v1\n")
    (repo / "ro").mkdir()
    (repo / "ro" / "bbb.txt").write_text("v1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "before")
    _git(repo, "push", "origin", "main")
    (repo / "mine.txt").write_text("THE HUMAN'S OWN UNCOMMITTED EDIT\n")
    _land_on_origin(tmp_path, "mixed", {"aaa.txt": "v2\n", "ro/bbb.txt": "v2\n"})

    with _unwritable_dir(repo / "ro"):
        res = gc_workspaces(cwd=repo, workflow=wf)

    state = res["main_checkout"]
    assert state["code"] == workspace_cmd.MAIN_SYNC_PARTIAL, state
    assert state["half_applied"] == ["aaa.txt"], state
    assert (repo / "mine.txt").read_text() == "THE HUMAN'S OWN UNCOMMITTED EDIT\n", (
        "and it survived, which is the property the report must not misdescribe"
    )


def test_the_half_applied_list_is_capped_and_says_so(repo, tracker, tmp_path):
    """Same cap and same `_truncated` sibling as `overwritten_ignored`, for the same reason: a
    fast-forward can carry hundreds of paths, and a report nobody can read is not a report. The
    number is the length BEFORE the cap."""
    _api, wf = tracker
    n = workspace_cmd._MAX_REPORTED_IGNORED + 3
    names = [f"f{i:03d}.txt" for i in range(n)]
    for name in names:
        (repo / name).write_text("v1\n")
    (repo / "ro").mkdir()
    (repo / "ro" / "bbb.txt").write_text("v1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "before")
    _git(repo, "push", "origin", "main")
    incoming = {name: "v2\n" for name in names}
    incoming["ro/bbb.txt"] = "v2\n"
    _land_on_origin(tmp_path, "many", incoming)

    with _unwritable_dir(repo / "ro"):
        res = gc_workspaces(cwd=repo, workflow=wf)

    state = res["main_checkout"]
    assert state["code"] == workspace_cmd.MAIN_SYNC_PARTIAL, state
    assert len(state["half_applied"]) == workspace_cmd._MAX_REPORTED_IGNORED, state
    assert state["half_applied_truncated"] == n, state


def test_a_failing_half_apply_check_costs_the_report_and_never_the_verdict(repo, tracker, tmp_path,
                                                                          monkeypatch):
    """The same best-effort class as the `overwritten_ignored` probe next door, and the same
    argument: a diagnostic must never become a new way for the reaper to fail. The sweep still
    returns its three lists and the fast-forward is still attempted.

    AND IT PINS THE SEPARATE `except`, which is the part worth having a test for: the two snapshots
    are caught INDEPENDENTLY, so losing the tracked one costs the `half_applied` key and NOT the
    ignored evidence. Wrap both in one `try` and this file's most important report disappears
    whenever `_tracked_changes` (`git diff-index`) hiccups.

    THE EXAMPLE THIS POINTED AT IS HISTORY NOW, and this sentence went on asserting it in the
    PRESENT tense for a whole card after it stopped being true — VMCP-270 (886), which recreates
    the VMCP-254 (854) that the round-one independent review of VMCP-246 (837) filed. It used to
    call the same mistake one
    `_ignored_paths_the_ff_will_overwrite` "documents against itself", quoting a line that function
    no longer carries: 837 made that give-up LOCAL by splitting the batch, and it historicised BOTH
    twins of this sentence in `workspace_cmd.py` in the same commit — leaving this third site
    untouched while its own report said the fix had been made, which is exactly why a later reader
    would not re-check it.

    Dated with `git log -S`, run in both case forms because that search is CASE-SENSITIVE and the
    two can disagree (here they do not — the upper-case spelling is in no commit on any ref):
    "documents against itself" APPEARED in `6231c85`, the landing of 835 that wrote it, and has
    been in every commit since. `8a77387` is absent from that search because it moved the count
    NOWHERE, and it takes BOTH halves to say so. The phrase has only ever lived in THIS file — one
    path, over every object reachable from `origin/main`, scanned FLATTENED so the wrap trap below
    cannot bite the check itself, measured at `077d5aa`. And 837's own 405 lines here added no
    fresh quotation of it: one before, one after. File-exclusivity ALONE would not do, because 837
    was IN this file — which also kills the tempting story about how this third site survived. Its
    whole edit here was ONE hunk appended past the end of a 5038-line file, 405 insertions and
    ZERO deletions, so nothing already standing was ever in its author's diff. "One unreadable
    path used to return" and "used to have a live example one function over" both appear in
    `8a77387`, the landing of 837. Needles have to be picked line by line — the phrase as written
    here wraps, and `-S` compares blob content, so a needle spanning a line break matches nothing
    and reads exactly like "never touched".

    "APPEARED IN" IS THE LOAD-BEARING WORD THERE, and putting it where "and in none after it" used
    to stand is the whole of VMCP-270 (886) round two. That clause was not stale, it was false AT
    BIRTH: `bc960b2` wrote a fresh quotation of the phrase into this docstring while asserting no
    commit after `6231c85` carried one, so it put a second commit into its own search before any
    reader could run it — and the reader it misleads is the one who does the right thing and
    re-runs the command. SKILL.md's second `-S` gotcha, in this file, about this file.

    AND THAT COUNT IS NOT MERELY STALE, IT IS BLIND, which is why the remedy is the FORM and not a
    fresher number. Date a needle by the commit it APPEARED in — no later quotation moves that.
    Never by how many commits `-S` returns, because that answer turns on typography as much as on
    content — and the paragraph above is its own demonstration. `bc960b2` quoted BOTH neighbouring
    needles there, one clause apart, and `-S` reports that commit for "used to have a live example
    one function over" and NOT for the other one, whose copy it wrote straddling a line break.
    One commit, one sentence, a quotation each, opposite answers; a later reflow altering not one
    word can swap them back. A ruler that swings on where the line happens to break cannot date
    the sentence it is measuring.

    THE ARGUMENT IS UNCHANGED AND STRONGER, which is why only the attribution moves: 837 applied
    this very principle — one probe's failure must not discard what another already found — to
    `check-ignore` itself. What was wrong was the tense and the pointer, never the reasoning.

    NO GATE SEES THIS CLASS and none is added here, deliberately — but the reason this paragraph
    gave for two rounds was false, and the correction runs the OPPOSITE way. It used to say that
    `tests/unit/test_repo_quotation_claims.py` stayed green because the quoted string OCCURRED
    elsewhere, in `workspace_cmd.py`, in its historicised form. Measured by importing that file's
    own predicates and running them over the old docstring read out of `6231c85`: the gate never
    looked at the sentence at all — `_CLAIM_TRIGGERS` fires on NONE of it and
    `_quotations_a_claim_makes` yields NOTHING, the old wording carrying not one of the four
    assertive idioms, so no claim was ever raised to check. Nor was the quotation anywhere else to
    be found: flattened over every path of `6231c85`, of `8a77387` and of `bc960b2^` alike it
    occurs exactly ONCE, in THIS file, and never once in `workspace_cmd.py` — though grepping
    there at `6231c85` does meet a twin, differing by exactly the two backticks around `[]`
    (verbatim 0, backticks-stripped 1, and gone by `8a77387`): near enough to mislead a reader
    re-checking by hand, nowhere near enough for a gate that matches exactly. What 837 put there
    is a DIFFERENT string, "One unreadable path used to return". So the counterfactual inverts:
    `_occurs_elsewhere` answers 0 for it at each of those three revisions and that gate flags at
    `<= 0`, i.e. with a trigger present it would have gone RED on the very sentence it is cited
    here for missing.

    WHAT IT GENUINELY CANNOT SEE OUTLIVES THAT CORRECTION, and it is the part worth keeping: a
    stale ATTRIBUTION is a claim about what a NEIGHBOURING file says, and presence cannot see it —
    trigger or no trigger, because a quotation still standing in the tree says nothing about
    whether the sentence around it still describes its neighbour. The habit is the remedy: quoting
    a neighbour's prose, say what it says NOW, or mark the quotation as historical and name the
    card that changed it. `tests/unit/test_measured_figure_anchors.py` does not cover that gap
    either, and where it stops lands on the APPEARED-in rule above: it reads the preposition "at"
    before a backticked sha, so a dating written that way is guarded here while every dating
    written with "in" — the form that rule prescribes — is checked by nothing at all.
    """
    _api, wf = tracker
    _half_applying_stand(repo, tmp_path)
    monkeypatch.setattr(workspace_cmd, "_tracked_changes",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("git fell over")))

    with _unwritable_dir(repo / "ro"):
        res = gc_workspaces(cwd=repo, workflow=wf)

    assert set(res) >= {"released", "kept", "expected", "main_checkout"}
    state = res["main_checkout"]
    assert state["updated"] is False and "half_applied" not in state, state
    assert state["code"] == workspace_cmd.MAIN_SYNC_PARTIAL, state
    assert state["overwritten_ignored"] == ["shot.png"], state
    # VMCP-252 (851): the UNCHECKED branch of the split reason lives here, because this is the
    # state that builds it — `half` is empty because the probe FAILED, not because the tree is
    # silent, and `tracked_after` is therefore None rather than an empty set.
    assert "could NOT be checked" in state["reason"], state["reason"]
    assert "Nothing tracked differs from HEAD" not in state["reason"], (
        "an empty `half` that came from a FAILED probe must never be reported as a silent tree — "
        "that is the same borrowed reassurance #806 shipped and 835 was filed for"
    )


# VMCP-258 (860): THE TWO AFTER-REFUSAL DIAGNOSTICS ARE GUARDED TOO, EACH ON ITS OWN.
#
# MUTATION SWEEP, RE-RUN WHOLE FOR ROUND 2. The round-1 numbers this block used to carry are
# REPLACED rather than appended to, and the reason is ordinary decay, NOT the measure-before-rebase
# class: re-measured, round 1's `collected 203` was CORRECT at its own landing commit `c460190`,
# and five sibling landings have touched this file and the module since. Read every count below as
# a property of the tree at `684bb21` plus this card's diff — the whole sweep was RE-RUN there,
# after a rebase, rather than carried over from the tree it was first cheap on.
#
# THAT ANCHOR IS THE POINT, and this block demonstrates its own subject: between that sweep and
# this landing another sibling arrived, so the SHIPPED tree collects 211 rather than 210, the one
# extra being #852's `test_a_symlink_under_a_NON_TRAVERSABLE_parent_is_still_named`. Re-measured on
# the shipped tree, that control is `collected 211 items`, 0 failed, 0 errors. The rounds below are
# NOT restated against it: with six agents pushing, a count re-derived after every rebase is a
# treadmill, and the honest fix is to NAME the tree a number belongs to rather than to keep chasing
# one. What makes the delta safe to read across the two trees is that the sibling touches none of
# the four mutation anchors and no test this sweep kills.
#
# One selection throughout — `tests/unit/test_workspace_cmd.py`, whole file, `-q` DROPPED —
# `collected 210 items` and `0` ERROR lines in EVERY round. Each round is read by COUNTING lines
# that begin `FAILED ` and, separately, lines that begin `ERROR `, never by the first `N failed` in
# stdout, which lands inside the literal `control 0 failed` these very comments carry and so reads
# DOWNWARD. Mutations are applied in a SEPARATE clone by EXACT-STRING replacement, each anchor
# asserted to occur exactly once, the source restored after every round and confirmed
# sha256-identical to the pristine copy:
#   * the after-refusal `_tracked_changes` guard removed ....... control 0 failed; 4 failed
#   * the after-refusal `_fingerprints` guard removed .......... control 0 failed; 2 failed
#   * BOTH after-refusal guards removed ........................ control 0 failed; 5 failed
#   * `looked` back to round 1's disjunction over the CALLS .... control 0 failed; 3 failed
#   * that disjunction with `prints_answered` deleted .......... control 0 failed; 1 failed
#   * that disjunction with `tracked_after is not None` gone ... control 0 failed; 3 failed
#   * that disjunction tightened by `and bool(doomed)` ......... control 0 failed; 2 failed
#   * conjunct `tracked_compared` dropped ...................... control 0 failed; 3 failed
#   * `bool(doomed)` dropped from `ignored_compared` ........... control 0 failed; 1 failed
#   * `doomed_answered` dropped from `ignored_compared` ........ control 0 failed; 1 failed
# Control re-run after the last restore: 0 failed.
#
# THE MIDDLE THREE ROWS ARE THE POINT OF ROUND 2, because they are exactly the three forms that
# were GREEN when round 1 shipped. Measured by this round rather than inherited from the review
# that found them, on round 1's own landing commit `c460190` (same selection, `collected 203
# items`, control 0 failed at the start and after the last restore): deleting EITHER disjunct of
# `looked = tracked_after is not None or prints_answered` left the suite at 0 failed, and so did
# tightening it to `or (prints_answered and bool(doomed))`. TWO tests asserted on that branch's
# wording there — `test_when_BOTH_after_probes_die_the_refusal_says_it_could_not_look` and the
# pre-existing `test_a_refusal_that_could_not_be_CHECKED_does_not_claim_it_was` — and NEITHER could
# tell the three forms apart, which is how a defect the reviewer then built by hand (the
# reassurance printed over a half-applied checkout) shipped under a green suite. A guard added to a
# diagnostic changes what the sentences ABOUT that diagnostic may claim, and round 1 moved the
# guard without moving the claim or pinning it.
#
# Rows one and two kill more than one test each because the both-probes-died test reaches its state
# through either guard; the paired single-guard tests are what separate them. Row one kills four
# rather than two because two of round 2's new pins also drive the tracked probe to its death
# (`test_an_empty_doomed_list_…` and `test_an_answered_ignored_probe_…` both monkeypatch it).
# No row singles out a DIFFERENT one of round 2's new tests: the last two rows and the
# `ignored_compared`-dropped form all land on
# `test_a_doomed_list_the_PRE_MERGE_probe_never_computed_cannot_vouch_for_anything` ALONE, so that
# one test carries the whole ignored-half conjunct — real work, and a single point of failure worth
# knowing about.
#
# AND ONE ROW IS ABOUT THE OPPOSITE DIRECTION, over-tightening, because without it the fourth new
# test — `test_looked_still_says_CHECKED_when_both_halves_really_were_compared` — is killed by NO
# row at all, which is this repo's "negative pin that pins nothing" shape. Dropping the empty-
# `doomed` alternative outright (`ignored_compared = (prints_answered and bool(doomed))`) makes the
# honest branch start claiming it could not look. Measured on the tree this card SHIPS, control
# first and last, same selection:
#   * empty-`doomed` alternative dropped ....................... control 0 failed; 2 failed
# Honest bound on that row: the second test it kills is the PRE-EXISTING
# `test_the_fast_forward_refuses_rather_than_overwriting_uncommitted_work`, which already pins the
# same property — so no mutation here separates the new test from the old one, and the new test's
# value is that it names the input rather than that it is uniquely load-bearing.
#
# TWO NOTES ON THE STAND ITSELF, both worth more than the numbers. The monkeypatch has to let the
# FIRST call through — a `_tracked_changes` that throws on every call kills the BEFORE snapshot,
# after which `if tracked_before is not None` skips the after-call entirely and the unguarded line
# is never executed. That is exactly why the pre-existing
# `test_a_failing_half_apply_check_costs_the_report_and_never_the_verdict` was green on the defect.
# And the sweep script's own round LABEL was written with `-m`-style double quotes around a
# backticked identifier, so the shell ate it (`line 77: looked: command not found`) and the label
# printed short — CLAUDE.md's commit-message hazard, in a place nobody thinks to apply it. The
# label is not the measurement, so the round stands; the lesson is that the rule is about double
# quotes, not about commits.

def _real_once_then_raises(monkeypatch, name, exc):
    """Let the BEFORE-merge snapshot succeed and blow up the AFTER-refusal one.

    The whole of VMCP-258 (860) lives in that asymmetry, and it is why the pre-existing
    `test_a_failing_half_apply_check_costs_the_report_and_never_the_verdict` cannot reach it: a
    monkeypatch that throws on EVERY call kills the before-snapshot, after which the caller's own
    `if <before> is not None` skips the after-call entirely, so the unguarded line is never run.
    """
    real = getattr(workspace_cmd, name)
    seen: list[int] = []

    def _wrapper(*a, **k):
        seen.append(1)
        if len(seen) == 1:
            return real(*a, **k)
        raise exc

    monkeypatch.setattr(workspace_cmd, name, _wrapper)
    return seen


def test_a_tracked_probe_dying_AFTER_the_refusal_no_longer_costs_the_whole_report(
        repo, tracker, tmp_path, monkeypatch):
    """VMCP-258 (860). `_tracked_changes` runs `git diff-index` through `_run_git`, which RAISES
    `WorkspaceError` on `_GIT_TIMEOUT` — so this is a SLOW repository, not a thought
    experiment. It shipped as "slow or LOCKED", and the second half was exactly backwards
    (VMCP-275 (898), re-measured here rather than inherited): hold `.git/index.lock` open and run
    the argv `_tracked_changes` actually uses — `diff-index --name-only -z --no-renames HEAD` —
    and it answers rc=0 in ~0.02 s, WITH `GIT_OPTIONAL_LOCKS=0` and without it alike, on git
    2.50.1. It never wanted that lock: `_tracked_changes` is itself the caller that passes the
    do-not-take-it knob. A held lock is the one shape this call is IMMUNE to, so naming it as the
    motivating state pointed the reader at the wrong repository.
    Unguarded, that exception leaves `sync_main_checkout` altogether and takes the
    ENTIRE state dict with it, including the `overwritten_ignored` computed BEFORE the merge;
    `gc_workspaces` then reports `MAIN_SYNC_ERROR` and the reaper survives, so the cost is the
    REPORT, on the one branch where a human most needs it.

    What is asserted is what SURVIVES: the ignored casualty is still named, the code is still the
    partial-apply one, and the key whose probe died is ABSENT rather than empty — the same
    one-way reading the rest of this path is built on."""
    _api, wf = tracker
    _half_applying_stand(repo, tmp_path)
    seen = _real_once_then_raises(monkeypatch, "_tracked_changes",
                                  WorkspaceError("git diff-index … timed out after 600s"))

    with _unwritable_dir(repo / "ro"):
        res = gc_workspaces(cwd=repo, workflow=wf)

    assert len(seen) == 2, "the after-refusal call really was reached"
    state = res["main_checkout"]
    assert state["code"] == workspace_cmd.MAIN_SYNC_PARTIAL, state
    assert state["overwritten_ignored"] == ["shot.png"], (
        "the pre-merge probe's answer must survive a sibling diagnostic's death — that is the "
        "rule the before-merge snapshots already state and these two did not follow"
    )
    assert "half_applied" not in state, state
    assert "could NOT be checked" in state["reason"], state["reason"]
    assert (repo / "shot.png").read_text() == "UPSTREAM\n", "the loss the report is about is real"


def test_a_fingerprint_probe_dying_AFTER_the_refusal_no_longer_costs_the_whole_report(
        repo, tracker, tmp_path, monkeypatch):
    """The other half of VMCP-258 (860), and the mirror image of the test above: here the TRACKED
    half survives and the ignored one is lost. `_fingerprints` swallows `OSError` per path, so its
    own reachability is poor — it is guarded because the RULE is per-call, not because this input
    is likely, and because a guard on one of two symmetrical calls invites the next reader to
    conclude the other was deliberate."""
    _api, wf = tracker
    _half_applying_stand(repo, tmp_path)
    seen = _real_once_then_raises(monkeypatch, "_fingerprints", RuntimeError("lstat fell over"))

    with _unwritable_dir(repo / "ro"):
        res = gc_workspaces(cwd=repo, workflow=wf)

    assert len(seen) == 2, "the after-refusal call really was reached"
    state = res["main_checkout"]
    assert state["code"] == workspace_cmd.MAIN_SYNC_PARTIAL, state
    assert state["half_applied"] == ["aaa.txt"], state
    assert "overwritten_ignored" not in state, state


def test_when_BOTH_after_probes_die_the_refusal_says_it_could_not_look(
        repo, tracker, tmp_path, monkeypatch):
    """The guard's own COST, paid in the same card that added it. `looked` used to be read off the
    BEFORE snapshots, which was correct while the after-calls could not give up: a before-snapshot
    then implied a comparison. Guard them and it stops implying one — this checkout really is
    half-applied, both after-probes died, and the old reading would have printed "nothing
    half-written was found afterwards — which is what was CHECKED" over it.

    That is the exact borrowed reassurance #806 shipped and #835 was filed for, so the fix is not
    optional decoration on 860: without it the guard TRADES a lost report for a false one, which
    is worse."""
    _api, wf = tracker
    _half_applying_stand(repo, tmp_path)
    _real_once_then_raises(monkeypatch, "_tracked_changes", RuntimeError("diff-index fell over"))
    _real_once_then_raises(monkeypatch, "_fingerprints", RuntimeError("lstat fell over"))

    with _unwritable_dir(repo / "ro"):
        res = gc_workspaces(cwd=repo, workflow=wf)

    state = res["main_checkout"]
    assert state["code"] == workspace_cmd.MAIN_SYNC_BLOCKED, state
    assert "could NOT be checked" in state["reason"], state["reason"]
    assert "which is what was CHECKED" not in state["reason"], (
        "a before-snapshot with no after-snapshot compared NOTHING; saying otherwise over a "
        "genuinely half-applied checkout is the one outcome worse than saying nothing"
    )
    assert (repo / "aaa.txt").read_text() == "v2\n", "the checkout really is half-applied"


def test_an_empty_doomed_list_is_not_a_comparison_so_it_cannot_vouch_for_the_tracked_half(
        repo, tracker, tmp_path, monkeypatch):
    """VMCP-258 (860) ROUND 2, and the input its round 1 shipped a FALSE report on.

    Round 1 read `looked = tracked_after is not None or prints_answered`. `prints_answered` is set
    from `_fingerprints(...) is not None`, and `_fingerprints(root, [])` returns `{}` — which IS
    "not None". So over an empty `doomed` the ignored probe answers TRUE having compared ZERO
    paths, the disjunction is satisfied by that alone, and `looked` becomes unconditionally true
    whenever the human has no ignored casualty. Which is the checkout with no ignored casualty —
    the shape this branch was written for, and the one where the reassuring sentence then printed
    over a half-applied tree. `looked = False` needed the tracked half to be dead AS WELL as one of
    the two `_fingerprints` calls having failed, so it was two conditions and not one probe.

    That trade is strictly worse than the defect the card was filed for. Unguarded, the same input
    RAISED and `gc_workspaces` said `MAIN_SYNC_ERROR` — a loud, honest nothing. Round 1 turned it
    into a quiet "nothing half-written was found afterwards — which is what was CHECKED" over a
    checkout where `aaa.txt` really had gone v1 -> v2.

    So this pin is about the WORD, not the guard: `looked` must mean a comparison HAPPENED, never
    that a call returned. What removes each conjunct in turn is the mutation sweep recorded ABOVE,
    not the siblings below — those are the other inputs it kills — because a gate nothing can
    redden is the defect this repository keeps re-filing."""
    _api, wf = tracker
    _half_applying_stand(repo, tmp_path, ignored_victim=False)
    seen = _real_once_then_raises(monkeypatch, "_tracked_changes",
                                  WorkspaceError("git diff-index … timed out after 600s"))

    with _unwritable_dir(repo / "ro"):
        res = gc_workspaces(cwd=repo, workflow=wf)

    assert len(seen) == 2, "the after-refusal call really was reached"
    state = res["main_checkout"]
    assert (repo / "aaa.txt").read_text() == "v2\n", "the checkout really is half-applied"
    assert state["code"] == workspace_cmd.MAIN_SYNC_BLOCKED, state
    assert "which is what was CHECKED" not in state["reason"], (
        "an empty `doomed` compares nothing, so it cannot stand in for the tracked probe that "
        "died — this is the borrowed reassurance #806 shipped, over the commonest checkout there "
        f"is: {state['reason']}"
    )
    assert "could NOT be checked" in state["reason"], state["reason"]


def test_an_answered_ignored_probe_cannot_vouch_for_a_tracked_probe_that_died(
        repo, tracker, tmp_path, monkeypatch):
    """The same defect as the test above with the two halves SWAPPED, and the reviewer's second
    finding: it is not confined to an empty `doomed`.

    Here the ignored casualty EXISTS and its probe really did compare it — one path, fingerprint
    taken on both sides — while the tracked probe timed out. `over` is empty because this refusal
    is an ordinary UP-FRONT one (the human is sitting on an uncommitted `aaa.txt` that the update
    has to write, so git aborts before touching anything). Note what that costs the claim and what
    it does not: NOTHING is half-applied on this input, so the round-1 report was unearned here
    rather than false about the disk — the sibling above is the one where a tracked file really
    had moved.

    Round 1's `looked` was an OR across the two halves, so the ignored half answering satisfied it
    by itself and the report said "which is what was CHECKED" over a tracked half nobody looked at
    after the merge. One half of "half-written" is not the half."""
    _api, wf = tracker
    _half_applying_stand(repo, tmp_path)
    # Uncommitted work on a path the incoming commit modifies -> git refuses BEFORE writing, so
    # nothing is half-applied and the ignored fingerprint is unchanged: `over` and `half` both [].
    (repo / "aaa.txt").write_text("the human was editing this\n")
    seen = _real_once_then_raises(monkeypatch, "_tracked_changes",
                                  WorkspaceError("git diff-index … timed out after 600s"))

    state = gc_workspaces(cwd=repo, workflow=wf)["main_checkout"]

    assert len(seen) == 2, "the after-refusal call really was reached"
    assert state["code"] == workspace_cmd.MAIN_SYNC_BLOCKED, state
    assert "which is what was CHECKED" not in state["reason"], (
        "the ignored probe answering says nothing about the TRACKED half, and this sentence "
        f"speaks for both of them: {state['reason']}"
    )
    assert "could NOT be checked" in state["reason"], state["reason"]


def test_a_doomed_list_the_PRE_MERGE_probe_never_computed_cannot_vouch_for_anything(
        repo, tracker, tmp_path, monkeypatch):
    """The third conjunct, and the nastiest input on this branch — the reviewer's composite.

    `_ignored_paths_the_ff_will_overwrite` is best-effort too, so an empty `doomed` is MANY states
    and not one. Here the human's ignored `shot.png` really IS in the incoming commit, the probe
    that would have named it RAISED, and the report is then the only thing anyone will read about
    it. The name of this test describes THAT input — a list the probe never got to compute — and
    the input is built by making it throw.

    READ THE SCOPE OFF THE INPUT AND NOT OFF THE NAME, because the conjunct is narrower than the
    English: `doomed_answered` closes the RAISE, which is the route the after-probe guards made
    reachable, and NOT the probe's several NON-raising give-ups, which arrive as the same bare
    `[]` and are indistinguishable here. Those predate this card and are unchanged by it; the
    measured residue, including the parent it reproduces on, is written beside `looked`.

    Every other consumer of `doomed` may keep conflating all of them — the keys built from it are
    one-way by design and absence never proved anything — but this sentence claims a check was
    RUN, so it is the one place any part of the difference has to be carried. Without the
    `doomed_answered` conjunct the empty list reads as "nothing at risk, so nothing to compare, so
    the ignored half is clean", which is the same borrowed reassurance one level further back."""
    _api, wf = tracker
    _half_applying_stand(repo, tmp_path)
    # Up-front refusal: the human is sitting on `aaa.txt`, so git aborts before writing anything
    # and both halves of the report are legitimately empty — the sentence is all that is left.
    (repo / "aaa.txt").write_text("the human was editing this\n")
    monkeypatch.setattr(workspace_cmd, "_ignored_paths_the_ff_will_overwrite",
                        lambda *a, **k: (_ for _ in ()).throw(WorkspaceError("ls-tree fell over")))

    state = gc_workspaces(cwd=repo, workflow=wf)["main_checkout"]

    assert state["code"] == workspace_cmd.MAIN_SYNC_BLOCKED, state
    assert "which is what was CHECKED" not in state["reason"], (
        "the ignored half was never even asked about — an empty `doomed` the probe RAISED "
        f"instead of returning is not a finding: {state['reason']}"
    )
    assert "could NOT be checked" in state["reason"], state["reason"]


def test_looked_still_says_CHECKED_when_both_halves_really_were_compared(repo, tracker, tmp_path):
    """Input B of the pair above, as its own test because it needs no monkeypatch at all.

    The refusal here is an ordinary up-front one with nothing half-written and no ignored casualty,
    so both probes ran and both found nothing. If a tightening of `looked` ever makes THIS input
    say "could NOT be checked", the gate has stopped distinguishing anything and the sentence goes
    back to being the never-read field this module keeps splitting keys to avoid."""
    _api, wf = tracker
    _half_applying_stand(repo, tmp_path, ignored_victim=False)
    # The human's own uncommitted work on a path the update touches: git refuses BEFORE writing.
    (repo / "aaa.txt").write_text("the human was editing this\n")

    state = gc_workspaces(cwd=repo, workflow=wf)["main_checkout"]

    assert state["code"] == workspace_cmd.MAIN_SYNC_BLOCKED, state
    assert "which is what was CHECKED" in state["reason"], (
        "both probes ran and both compared what there was to compare; refusing to say so would "
        f"make the honest branch fire on every ordinary refusal: {state['reason']}"
    )


def test_the_partial_apply_probes_do_not_REFRESH_a_refused_checkouts_index(repo, tracker, tmp_path):
    """THE REGRESSION THIS CARD SHIPPED FOR ONE ROUND, and the stand its own predecessor could not
    build. `test_the_probe_leaves_the_index_of_a_REFUSED_checkout_untouched` asks the same question
    and stays GREEN here — measured, control 0 failed and the `git diff` form 0 failed against it —
    because its checkout has no STAT-DIRTY-BUT-CONTENT-CLEAN entry, which is the only state that
    makes git want to write the index at all.

    Two things have to be true of that entry and the second is what hid the bug: the stat must
    differ from the index, AND its mtime must be in the PAST. `touch`ed to NOW, git calls the entry
    racily clean, deliberately declines to record the fresh stat, and every read looks innocent.
    Here `stat_dirty.txt` is given mtime 0 — which is just the ordinary state of a file somebody
    stopped editing an hour ago.

    Measured on real git 2.50.1: `git diff --name-only HEAD` moves the index mtime and
    `GIT_OPTIONAL_LOCKS=0` does NOT stop it (nor does `git --no-optional-locks`), while
    `git diff-index --name-only HEAD` leaves it alone. That is why this function uses the plumbing.
    The property being defended is the module's oldest one here: on a run whose merge is REFUSED,
    nothing of ours may write in somebody else's working directory."""
    _api, wf = tracker
    (repo / "stat_dirty.txt").write_text("v1\n")
    (repo / "coll.txt").write_text("v1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "before")
    _git(repo, "push", "origin", "main")
    (repo / "coll.txt").write_text("THE HUMAN IS EDITING THIS\n")     # -> the merge is refused
    os.utime(repo / "stat_dirty.txt", (0, 0))                         # stat-dirty, content-clean
    _land_on_origin(tmp_path, "idx", {"coll.txt": "UPSTREAM\n"})
    index = Path(_git(repo, "rev-parse", "--git-path", "index"))
    index = index if index.is_absolute() else repo / index
    before = index.stat().st_mtime_ns

    res = gc_workspaces(cwd=repo, workflow=wf)

    assert res["main_checkout"]["code"] == workspace_cmd.MAIN_SYNC_BLOCKED, res["main_checkout"]
    assert index.stat().st_mtime_ns == before, (
        "the partial-apply probe refreshed and rewrote the index of a HUMAN's checkout on a run "
        "whose merge was refused — `git diff HEAD` does that and no env var stops it; the "
        "plumbing `git diff-index` is what does not"
    )


def test_a_refusal_that_could_not_be_CHECKED_does_not_claim_it_was(repo, tracker, tmp_path,
                                                                   monkeypatch):
    """The failure this whole chain is made of, one level up: a report that reassures about
    something it never looked at. With BOTH snapshots unavailable there is no evidence either way,
    so the `blocked` reason must not borrow the wording of the branch that did look."""
    _api, wf = tracker
    (repo / "coll.txt").write_text("v1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "before")
    _git(repo, "push", "origin", "main")
    (repo / "coll.txt").write_text("THE HUMAN IS EDITING THIS\n")
    _land_on_origin(tmp_path, "blind", {"coll.txt": "UPSTREAM\n"})
    boom = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("git fell over"))   # noqa: E731
    monkeypatch.setattr(workspace_cmd, "_tracked_changes", boom)
    monkeypatch.setattr(workspace_cmd, "_fingerprints", boom)

    res = gc_workspaces(cwd=repo, workflow=wf)

    state = res["main_checkout"]
    assert state["code"] == workspace_cmd.MAIN_SYNC_BLOCKED, state
    assert "could NOT be checked" in state["reason"], state["reason"]
    assert "was found afterwards" not in state["reason"], state["reason"]


def test_the_rulebook_names_every_main_checkout_code_the_sweep_can_emit():
    """SKILL.md is where an agent LEARNS what a `main_checkout` code means, so the code VALUES are a
    cross-surface contract: add or rename one without the rulebook moving and the orchestrator reads
    a code its own instructions never mention.

    VMCP-244 (835) is why this is asserted rather than assumed. That card added `half-applied` AND
    had to rewrite the rulebook line it landed next to — the line claiming the whole `updated:
    false` family had lost NOTHING — and nothing anywhere would have gone red had the rulebook been
    left alone.

    Its bound, named here rather than discovered by the next reader: PRESENCE of the backticked
    value, so it cannot check that what the rulebook SAYS about a code is true, and a value that is
    also an ordinary word could be vouched for by unrelated prose (`blocked` is a tracker LABEL in
    this same file). It catches the one failure that is otherwise completely silent — a code the
    rulebook does not mention at all."""
    # the WHOLE rulebook — the core plus its `references/*.md`. The rulebook was split into a
    # core (what to do) and references (payload shapes and measured reasons), and the
    # `main_checkout` codes are payload shapes, so they live in `references/gc-report.md`. This
    # asks whether the RULEBOOK names them, which is a question about the set of files, not about
    # which one holds the sentence today.
    tracker = Path(workspace_cmd.__file__).parent / "skills" / "tracker"
    skill = "\n".join([
        (tracker / "SKILL.md").read_text(encoding="utf-8"),
        *(p.read_text(encoding="utf-8") for p in sorted((tracker / "references").glob("*.md"))),
    ])
    codes = {n: v for n, v in vars(workspace_cmd).items()
             if n.startswith("MAIN_SYNC_") and isinstance(v, str)}
    assert len(codes) >= 8, sorted(codes)
    missing = sorted(v for v in codes.values() if f"`{v}`" not in skill)
    assert not missing, f"the rulebook names no such main_checkout code: {missing}"
    for key in ("half_applied", "half_applied_truncated", "overwritten_ignored"):
        assert f"`{key}`" in skill, f"the rulebook never mentions the key {key}"


# --- VMCP-246 (837): a SUBMODULE is the second spelling of `check-ignore`'s fatal --------------
#
# Filed by the round-2 independent review of VMCP-240 (806) and reproduced here on real git 2.50.1
# before anything was changed. `git check-ignore` has a fatal beyond the "beyond a symbolic link"
# one the probe's docstring named:
#
#     $ printf 'sub/x.png\0' | git check-ignore -z --stdin
#     fatal: Pathspec 'sub/x.png' is in submodule 'sub'      rc=128
#
# Both producers named as closing the symlink route miss it, and that is the point of the card:
# neither is looking for a submodule. `_doomed_ancestor` answers None, and `_expand_if_directory`
# walks INTO the submodule's working directory — a REAL directory — handing back the files inside.
# Be exact about the FIRST of those, because the obvious reading is wrong and would mislead anyone
# simplifying that walk: for the bare name `sub` the loop body never runs at all (`'sub'.split("/")`
# has length 1, so `range(1, 1)` is empty) — the "no `/` at all" branch its own docstring names, NOT
# the walk-through branch. Walk-through is what answers None for a path INSIDE the submodule
# (`_doomed_ancestor(root, 'sub/x.txt')`). Measured on a SHELL stand outside this suite (bare origin
# + a checkout with a populated submodule `sub` holding `x.png` and `inside.txt` + the human's
# ignored `shot.png`; a sibling bumps the gitlink AND force-adds its own `shot.png`) — the names
# below are that stand's, not the ones `_with_submodule` builds, so do not read them as quotations
# of anything in this tree:
#   * incoming ACMT diff ............ ['shot.png', 'sub']
#   * _doomed_ancestor('sub') ....... None
#   * _expand_if_directory('sub') ... ['sub/x.png', 'sub/inside.txt', 'sub/.git']
#   * check-ignore over the batch ... rc=128, stdout the ignored answers it had already printed,
#                                     stderr the fatal above
#   * probe ......................... []
#   * sync .......................... {'updated': True, ...} with NO `overwritten_ignored`
#   * the human's shot.png .......... overwritten by the sibling's bytes
# i.e. byte for byte the batch-wipe the symlink round closed, with an unrelated file that really
# died erased from a report that had already found it.
#
# THE SECOND DEFECT, and it is a premise rather than a symptom: a gitlink entry in the diff
# DISPLACES NOTHING. Measured on the same stand after the merge — `git status --porcelain` says
# ` M sub`, `git submodule status` still names the OLD commit, and `sub/inside.txt` still holds the
# old bytes. So the three paths `_expand_if_directory` named were FALSE VICTIMS. Today that
# is invisible: `check-ignore` refuses to answer about them at all, which is what masks it.
#
# MUTATION SWEEP, VMCP-246 (837). One selection throughout —
# `tests/unit/test_workspace_cmd.py -p no:randomly`, no `-q` — read by COUNTING `FAILED `- and
# `ERROR `-prefixed lines separately, with `collected` cross-checked; `collected 175` and `0 errors`
# in every round including both controls. Two sittings, and which round came from which is written
# out because the tree moved between them (see the note under the table):
#   * drop the pure-gitlink filter entirely ....... control 0 failed; 1 failed
#   * filter on EITHER mode instead of both ....... control 0 failed; 1 failed
#   * no bisect: pre-837 whole-batch `return []` .. control 0 failed; 1 failed
#   * keep the fatal call's stdout PREFIX instead
#     of splitting the batch ...................... control 0 failed; 1 failed
#   * no gitlink pruning (always an empty set) .... control 0 failed; 1 failed
#   * DROP an unrecognised `--raw` field instead
#     of keeping it as a path .................... control 0 failed; 0 failed
# THE ZERO IS DECLARED, not discovered here: nothing in this suite emits `--raw` output that fails
# the pair-wise shape, so the keep-it branch is defensive and is named in its own docstring rather
# than pretended to be pinned. The second pass built the adversarial input it wants (files literally
# named `:colon.png` and `:160000 160000 dead beef M`) and measured no desync, which is evidence for
# the branch being CORRECT and none at all for it being pinned.
#
# THE TWO SITTINGS, because the first one is itself a finding. Rounds 1-2 and 5-6 come from the
# 7-round sitting; rounds 3-4 were RE-RUN afterwards, and they had to be: in the 7-round sitting
# both read `control 0 failed; 0 failed`, i.e. THE BISECT WAS PINNED BY NOTHING — because the
# gitlink pruning that landed in the same card had removed the only shape whose batch still held an
# unaskable path. One fix quietly disarmed the other's test. The pin was restored by forcing
# `_index_gitlink_paths`'s own best-effort branch (a failing `git ls-files`) in
# `test_one_unaskable_path_costs_only_itself_and_not_the_paths_around_it`, and only that test's
# SETUP changed between the sittings; both controls are 0 over the same 175. This is the hazard
# CLAUDE.md's memory note calls a negative pin, arriving from an unexpected direction: not a guard
# whose test never had teeth, but a guard whose test LOST them to a sibling change in the same diff.
#
# WHAT NO ROUND HERE PINS, said plainly because the sweep cannot say it: `_MAX_CHECK_IGNORE_CALLS`
# and `_MAX_DIR_EXPANSION` are 15x apart, so a batch can still exhaust the call budget and drop
# askable names. The second pass measured that live (30 files inside one submodule lost `z.png`; 25
# did not) and the pruning is what keeps the bulk producer from arriving — but no test in this file
# constructs a budget exhaustion, and none is claimed to.
#
# WHAT IS NOT TRUE OF THIS REPOSITORY IS NOT THE SAME AS WHAT IS NOT TRUE OF THE CODE. There are
# no submodules here (`git ls-files -s | awk '$1=="160000"'` is empty — `.gitmodules` is the wrong
# evidence, since a gitlink lives in the INDEX), so the defect is latent HERE. But `--gc` ships to
# consumers on the moving `stable` channel and `sync_main_checkout` runs in THEIR main checkout,
# where a submodule is an ordinary thing to have.


def _with_submodule(repo, tmp_path, name="sub", inner=None):
    """Give `repo` a real, POPULATED submodule pinned at its FIRST commit, committed and pushed.

    Returns the LATER submodule sha — the one a sibling bumps the superproject's gitlink to.
    `-c protocol.file.allow=always` is required for a `file://`-ish local submodule source on
    git 2.50.1 and is the test harness's business, not the module's."""
    src = tmp_path / f"{name}src.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(src)],
                   check=True, capture_output=True)
    work = tmp_path / f"{name}work"
    subprocess.run(["git", "clone", "-q", str(src), str(work)], check=True, capture_output=True)
    _git(work, "config", "user.email", "sub@example.com")
    _git(work, "config", "user.name", "Sub")
    for rel, content in (inner or {"inner.txt": "the submodule's first state\n"}).items():
        (work / rel).write_text(content)
    _git(work, "add", "-A")
    _git(work, "commit", "-m", "s1")
    _git(work, "push", "-q", "origin", "HEAD:main")
    first = _git(work, "rev-parse", "HEAD")
    (work / "later.txt").write_text("the submodule's second state\n")
    _git(work, "add", "-A")
    _git(work, "commit", "-m", "s2")
    _git(work, "push", "-q", "origin", "HEAD:main")
    later = _git(work, "rev-parse", "HEAD")
    _git(repo, "-c", "protocol.file.allow=always", "submodule", "add", "-q",
         "--branch", "main", str(src), name)
    _git(repo, "-C", name, "checkout", "-q", first)
    _git(repo, "add", ".gitmodules", name)
    _git(repo, "commit", "-m", f"add submodule {name} pinned at its first commit")
    _git(repo, "push", "origin", "main")
    return later


def _bump_gitlink_on_origin(tmp_path, name, sub_sha, extra=None, path="sub"):
    """Land a commit that moves the superproject's GITLINK — the everyday trigger of this card.

    Plumbing (`update-index --cacheinfo`) rather than `submodule update`: the sibling never needs
    the submodule populated to move the pointer, and this keeps the local-protocol config out of
    the shape under test."""
    other = tmp_path / f"sibling-{name}"
    subprocess.run(["git", "clone", str(tmp_path / "origin.git"), str(other)],
                   check=True, capture_output=True)
    _git(other, "config", "user.email", "sibling@example.com")
    _git(other, "config", "user.name", "Sibling")
    for rel, content in (extra or {}).items():
        (other / rel).parent.mkdir(parents=True, exist_ok=True)
        (other / rel).write_text(content)
        _git(other, "add", "-f", rel)
    _git(other, "update-index", "--cacheinfo", f"160000,{sub_sha},{path}")
    _git(other, "commit", "-m", f"sibling: {name}")
    _git(other, "push", "origin", "HEAD:main")
    return _git(other, "rev-parse", "HEAD")


def test_a_submodule_pointer_bump_no_longer_wipes_the_whole_report(repo, tracker, tmp_path):
    """THE CARD'S OWN INPUT. The unrelated ignored file that really dies must still be named.

    One commit does two things a sibling routinely does together: it bumps the submodule pointer
    and it force-adds a `shot.png` at a path this checkout ignores. The human's own `shot.png` is
    destroyed either way — that is git, and this module does not fight it — but before the fix the
    submodule-internal paths made `check-ignore` exit 128, the probe returned `[]` for the WHOLE
    batch, and the file died unreported.

    THE PAYLOAD ASSERTION ALONE WAS DISARMED BY 837 ITSELF, which is VMCP-262 (865) and the reason
    the candidate set is now asserted directly. Measured: on the shipped code the mutant that
    removes the gitlink filter (plus the subtree prune) leaves this test GREEN, while the same
    mutant over the pre-837 whole-batch give-up fails it with `KeyError: 'overwritten_ignored'` —
    1 -> 0 with the assertions untouched. The mechanism is the axis worth carrying away: the
    mutant's only effect on the REPORT is to ADD unaskable submodule-internal paths beside
    `shot.png`, and the bisect that arrived in the same card drops exactly those and returns
    `['shot.png']` either way. So equality against an exact list is strong against a mutant that
    REMOVES or RENAMES an answerable name and weak against one that ADDS unanswerable ones — and
    which of the two a mutant is cannot be read off the shape of the assertion, only run.

    Adding a SECOND ordinary ignored casualty was the card's other suggestion and is measured NOT
    to help on its own for the same reason: the bisect isolates the unaskable paths whatever their
    answerable neighbours number, so the exact list still comes back whole.

    MUTATION SWEEP for this pin and its neighbour, one selection throughout — this file with
    `-k "submodule_pointer_bump or pure_gitlink_move or expansion_does_not_walk_through_a_symlink
    or NESTED_symlink_to_a_directory"`, no `-q`, `collected 203 items` and `4 selected` and 0
    `ERROR ` lines in every round, each round read by counting `FAILED ` and `ERROR ` lines
    separately; control 0 failed:
      * the gitlink-only diff filter removed (m11) .................... control 0 failed; 2 failed
      * `_index_gitlink_paths` always empty (m12) ..................... control 0 failed; 0 failed
      * m11 + m12, the pair the card measured at 1 -> 0 ............... control 0 failed; 2 failed
      * m11 + m12 with THIS test's direct assertion removed ........... control 0 failed; 1 failed
      * the top-level `islink` guard removed .......................... control 0 failed; 1 failed
    Row four is the card reproduced on this tree — with the direct assertion gone, m11+m12 leaves
    THIS test green and only the sibling fails — and rows one and three together are the repair:
    the pin now bites m11 ALONE, which is more than the card asked for. Row two is the honest
    remainder: m12 has no effect in this shape at all, because with the filter in place the gitlink
    entry never reaches the walk, so this test cannot pin the prune and does not claim to."""
    _api, wf = tracker
    _ignoring(repo, "*.png")
    later = _with_submodule(repo, tmp_path)
    (repo / "shot.png").write_bytes(b"\x89PNG the human's own evidence screenshot")

    _bump_gitlink_on_origin(tmp_path, "bump", later, extra={"shot.png": "UPSTREAM\n"})

    # THE FETCH IS LOAD-BEARING here for the same reason it is next door: the sibling pushed from a
    # clone of its own, so `origin/main` is stale until something fetches, and a direct call ahead
    # of `gc_workspaces` must not borrow the fetch that call does for itself.
    _git(repo, "fetch", "--no-recurse-submodules", "origin")
    incoming = workspace_cmd._incoming_displacing_paths(repo, "origin/main")
    assert incoming == ["shot.png"], (
        "a pure pointer move displaces nothing, so the gitlink entry must not reach the batch at "
        "all — asserted HERE because the report below is the same either way", incoming
    )

    res = gc_workspaces(cwd=repo, workflow=wf)

    state = res["main_checkout"]
    assert state["updated"] is True, state
    assert state["overwritten_ignored"] == ["shot.png"], state
    assert (repo / "shot.png").read_text() == "UPSTREAM\n", "the loss is real; the report is new"


def test_a_pure_gitlink_move_is_not_a_displacement_and_names_nothing_inside_it(repo, tracker,
                                                                              tmp_path):
    """THE SECOND DEFECT, at the level of the candidate set rather than the report.

    A gitlink entry in the ACMT diff is `:160000 160000 <old> <new> M` — git moves the POINTER and
    leaves the submodule's working directory exactly where it was. Measured after the merge:
    ` M sub` in `git status`, the submodule still at its old commit, and the file inside untouched.
    So the incoming path must not reach the batch at all, and `_incoming_displacing_paths` is
    where that is decided — asserted directly, because the report is the same either way (an
    unaskable path is dropped by the bisect too, so a pin on the payload alone would stay green
    with the filter deleted and pin nothing)."""
    _api, wf = tracker
    _ignoring(repo, "*.png")
    later = _with_submodule(repo, tmp_path)
    (repo / "sub" / "keep.png").write_bytes(b"\x89PNG the human's own, INSIDE the submodule")

    _bump_gitlink_on_origin(tmp_path, "purebump", later)

    # THE FETCH IS LOAD-BEARING, and leaving it out made this assertion VACUOUS — caught by this
    # card's independent second pass and reproduced by its own mutation sweep (delete the filter:
    # control 0 failed; that round 0 failed, i.e. this test's only reason for existing was pinning
    # nothing). The sibling pushes from a clone of its own, so `origin/main` HERE is stale until
    # something fetches; `HEAD..origin/main` was empty, and an empty diff has no gitlink entry to
    # drop. `gc_workspaces` below fetches for itself — a direct call ahead of it must not borrow
    # that. With the fetch, deleting the filter fails this line with `['sub']`.
    _git(repo, "fetch", "--no-recurse-submodules", "origin")
    incoming = workspace_cmd._incoming_displacing_paths(repo, "origin/main")
    assert incoming == [], (
        "a pure pointer move displaces nothing, so it must not be offered as a candidate", incoming
    )

    res = gc_workspaces(cwd=repo, workflow=wf)

    state = res["main_checkout"]
    assert state["updated"] is True, state
    assert "overwritten_ignored" not in state, state
    # The truth the filter rests on, asserted rather than described. `_git` strips, so the
    # porcelain ` M sub` arrives here as `M sub`: git moved the POINTER in the index and the
    # submodule's working directory did not follow it — it is still on the commit it was pinned to.
    assert (repo / "sub" / "keep.png").exists(), "the file inside the submodule is untouched"
    assert _git(repo, "status", "--porcelain") == "M sub", "the pointer moved, the tree did not"
    assert _git(repo, "-C", "sub", "rev-parse", "HEAD") != later, "and it did not move to `later`"


def test_an_incoming_SUBMODULE_over_a_local_ignored_file_is_still_named(repo, tracker, tmp_path):
    """WHY THE FILTER TESTS BOTH MODES AND NOT EITHER — and this shape, not the typechange, is
    what discriminates. The author's first draft justified "both, not either" with the typechange
    below, and that was wider than its proof: the typechange's victims live INSIDE a live gitlink,
    so `check-ignore` cannot answer about them and the report is silent either way. Here it is not.

    Upstream ADDS a submodule at `sub` (`:000000 160000 … A`, so the SOURCE mode is not a gitlink)
    while the main checkout holds the human's own IGNORED file at that name. Measured on real git:
    `git status --porcelain` is EMPTY beforehand, `merge --ff-only` returns rc=0, and the file is
    replaced by an empty directory — the invisible loss this whole feature exists for. The local
    path is an ordinary file, so `check-ignore` answers about it perfectly well; dropping the entry
    because its DESTINATION is a gitlink would swallow exactly that."""
    _api, wf = tracker
    _ignoring(repo, "*.png", "/sub")
    (repo / "sub").write_text("the human's own scratch, at a name upstream is about to claim\n")
    assert _git(repo, "status", "--porcelain") == "", "invisible before, as ever"

    src = tmp_path / "incomingsub.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(src)],
                   check=True, capture_output=True)
    seed = tmp_path / "incomingsubwork"
    subprocess.run(["git", "clone", "-q", str(src), str(seed)], check=True, capture_output=True)
    _git(seed, "config", "user.email", "sub@example.com")
    _git(seed, "config", "user.name", "Sub")
    (seed / "inner.txt").write_text("the incoming submodule's own content\n")
    _git(seed, "add", "-A")
    _git(seed, "commit", "-m", "s1")
    _git(seed, "push", "-q", "origin", "HEAD:main")

    other = tmp_path / "sibling-addsub"
    subprocess.run(["git", "clone", str(tmp_path / "origin.git"), str(other)],
                   check=True, capture_output=True)
    _git(other, "config", "user.email", "sibling@example.com")
    _git(other, "config", "user.name", "Sibling")
    # `-f` because the sibling shares the committed `/sub` rule; that is the harness's problem,
    # not the shape's — upstream adding a submodule at an ignored name is ordinary.
    _git(other, "-c", "protocol.file.allow=always", "submodule", "add", "-f", "-q",
         "--branch", "main", str(src), "sub")
    _git(other, "add", "-A", "-f")
    _git(other, "commit", "-m", "sibling: adds a submodule at sub")
    _git(other, "push", "origin", "HEAD:main")

    res = gc_workspaces(cwd=repo, workflow=wf)

    state = res["main_checkout"]
    assert state["updated"] is True, state
    assert state["overwritten_ignored"] == ["sub"], state
    assert (repo / "sub").is_dir(), "the human's file really was replaced by the submodule's dir"


def test_the_expansion_does_not_walk_into_a_NESTED_gitlink(repo, tracker, tmp_path, monkeypatch):
    """A SUBMODULE UNDER AN INCOMING DIRECTORY, built by this card's independent second pass — and
    it refuted the claim that a typechange is the only shape reaching the walk with a submodule on
    disk. `vendor/` is an ordinary tracked directory that happens to CONTAIN a submodule; upstream
    replaces `vendor` with a file, so the ACMT entry is a plain ADD with no gitlink on either side
    and the mode filter rightly keeps it. The walk then goes straight into `vendor/sub`.

    WHY THAT COSTS SOMETHING, measured by that pass on real git: every path inside a live gitlink is
    unaskable, so expanding one yields one unaskable path per file and the bisect pays to isolate
    each. A submodule of THIRTY files exhausted `_MAX_CHECK_IGNORE_CALLS` and dropped `z.png`, an
    askable path that really died; 25 files did not. Pruning removes the cost at its source and can
    lose no name, since none of those paths could ever be answered.

    IT IS PINNED BY THE CALL COUNT, NOT THE PAYLOAD, and that is the lesson of this card's own
    vacuous assertion next door: the bisect recovers `vendor/shot.png` either way, so the report is
    identical with the pruning and without it (measured), and only the number of `check-ignore`
    invocations tells them apart — 1 against several."""
    _api, wf = tracker
    _ignoring(repo, "*.png")
    src = tmp_path / "nestedsub.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(src)],
                   check=True, capture_output=True)
    seed = tmp_path / "nestedsubwork"
    subprocess.run(["git", "clone", "-q", str(src), str(seed)], check=True, capture_output=True)
    _git(seed, "config", "user.email", "sub@example.com")
    _git(seed, "config", "user.name", "Sub")
    for n in range(6):
        (seed / f"f{n}.txt").write_text("inside the nested submodule\n")
    _git(seed, "add", "-A")
    _git(seed, "commit", "-m", "s1")
    _git(seed, "push", "-q", "origin", "HEAD:main")

    (repo / "vendor").mkdir()
    (repo / "vendor" / "note.txt").write_text("makes `vendor` a real tracked directory\n")
    _git(repo, "add", "vendor")
    _git(repo, "-c", "protocol.file.allow=always", "submodule", "add", "-q",
         "--branch", "main", str(src), "vendor/sub")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "a tracked directory holding a submodule")
    _git(repo, "push", "origin", "main")
    (repo / "vendor" / "shot.png").write_bytes(b"\x89PNG the human's own, beside the submodule")

    other = tmp_path / "sibling-nested"
    subprocess.run(["git", "clone", str(tmp_path / "origin.git"), str(other)],
                   check=True, capture_output=True)
    _git(other, "config", "user.email", "sibling@example.com")
    _git(other, "config", "user.name", "Sibling")
    _git(other, "rm", "-r", "-q", "--cached", "vendor")
    shutil.rmtree(other / "vendor", ignore_errors=True)
    (other / "vendor").write_text("upstream made this an ordinary file\n")
    _git(other, "rm", "-q", "-f", ".gitmodules")
    _git(other, "add", "vendor")
    _git(other, "commit", "-m", "sibling: vendor becomes a file")
    _git(other, "push", "origin", "HEAD:main")

    _git(repo, "fetch", "--no-recurse-submodules", "origin")
    gitlinks = workspace_cmd._index_gitlink_paths(repo, ["vendor"])
    assert gitlinks == frozenset({"vendor/sub"}), ("the index is what names a gitlink", gitlinks)
    expanded = workspace_cmd._expand_if_directory(repo, "vendor", gitlinks)
    assert "vendor/shot.png" in expanded, expanded
    assert not [p for p in expanded if p.startswith("vendor/sub/")], (
        "the walk went into a live gitlink, whose paths `check-ignore` can never answer", expanded
    )

    calls = []
    real = workspace_cmd._run_git

    def counting(args, *a, **k):
        if args and args[0] == "check-ignore":
            calls.append(args)
        return real(args, *a, **k)
    monkeypatch.setattr(workspace_cmd, "_run_git", counting)

    res = gc_workspaces(cwd=repo, workflow=wf)

    state = res["main_checkout"]
    assert state["updated"] is True, state
    assert state["overwritten_ignored"] == ["vendor/shot.png"], state
    assert len(calls) == 1, ("no path in the batch is unaskable, so one call answers it", calls)
    assert (repo / "vendor").is_file(), "the directory really was replaced by a file"


def test_a_stray_nested_clone_is_named_ONCE_instead_of_flooding_the_report(repo, tracker, tmp_path):
    """VMCP-256 (858). A FOREIGN CLONE under an incoming directory — and it is the MIRROR of the
    gitlink test above, not another instance of it. There, paths are unaskable and the index says
    so. Here `git ls-files -s -- vendor/stray` is EMPTY (asserted below), no gitlink points at it,
    the index-based prune therefore leaves it alone — correctly — and `check-ignore` answers about
    its paths perfectly well. So nothing in 837's machinery applies, and the walk used to go
    straight in: measured on the filing card's stand, 31 paths came back of which 29 were `.git`
    internals — hook samples, loose objects, refs.

    WHY THAT IS NOT MERELY UNTIDY. `_MAX_REPORTED_IGNORED` is 50, and a clone of ordinary liveness
    has hundreds of paths under `.git`, so the noise does not just bury the real names — it can
    push them PAST the cap. `overwritten_ignored_truncated` would then stand beside a list that
    silently lost the only two names a human needed, and nothing says which. That is the failure
    #516 split `kept` in two to cure, arriving from a new direction.

    WHY ONE NAME IS THE RIGHT ANSWER rather than a filter entry: adding `.git/` to
    `_is_reproducible_ignored` was the cheap option and was REJECTED by the human who chose this
    one, because another repository's contents are not build detritus, and that filter decides what
    gets REPORTED — so the loss would have become entirely nameless. Naming the PLACE keeps the
    casualty in the report at the granularity a reader can act on: a foreign repository died here.

    AND THE TWO CHECKS STAY TWO. `.git`-on-disk is the right evidence for "one repository, not N
    files" and the WRONG evidence for "can this be asked about" — 837 measured it failing at that
    in both directions, so `_holds_a_dot_git` says as much in its own docstring. Do not merge them.

    MUTATION ROUND, measured at `2817dc3` on the whole file, `-q` dropped, `FAILED `- and `ERROR `-
    prefixed lines counted SEPARATELY, `collected` cross-checked, caches cleared and the target
    restored and sha256-verified: control 0 failed; dropping the clone prune (walking in as before)
    1 failed, killing this test and nothing else."""
    _api, wf = tracker
    _ignoring(repo, "*.png", "vendor/stray/")
    (repo / "vendor").mkdir()
    (repo / "vendor" / "note.txt").write_text("makes `vendor` a real tracked directory\n")
    _git(repo, "add", "vendor")
    _git(repo, "commit", "-m", "a tracked directory")
    _git(repo, "push", "origin", "main")
    # A REAL clone, so the `.git` noise is git's own rather than something this test invented.
    stray = repo / "vendor" / "stray"
    stray.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(stray)], check=True, capture_output=True)
    (stray / "work.txt").write_text("somebody's vendored checkout\n")
    (repo / "vendor" / "shot.png").write_bytes(b"\x89PNG the human's own, beside the clone")
    assert _git(repo, "ls-files", "-s", "--", "vendor/stray") == "", (
        "no gitlink points at a stray clone — this is the mirror of the submodule case, and the "
        "index-based prune must NOT be what handles it"
    )
    noise = len([p for p in (stray / ".git").rglob("*") if p.is_file()])
    assert noise >= 10, ("a real `.git` brings real noise, else this test proves nothing", noise)

    other = tmp_path / "sibling-stray"
    subprocess.run(["git", "clone", str(tmp_path / "origin.git"), str(other)],
                   check=True, capture_output=True)
    _git(other, "config", "user.email", "sibling@example.com")
    _git(other, "config", "user.name", "Sibling")
    _git(other, "rm", "-r", "-q", "--cached", "vendor")
    shutil.rmtree(other / "vendor", ignore_errors=True)
    (other / "vendor").write_text("upstream made this an ordinary file\n")
    _git(other, "add", "vendor")
    _git(other, "commit", "-m", "sibling: vendor becomes a file")
    _git(other, "push", "origin", "HEAD:main")
    _git(repo, "fetch", "origin")

    expanded = workspace_cmd._expand_if_directory(repo, "vendor")
    assert "vendor/stray" in expanded, ("the clone is named as ONE casualty", expanded)
    assert not [p for p in expanded if p.startswith("vendor/stray/")], (
        "the walk went INSIDE a foreign repository", expanded
    )

    res = gc_workspaces(cwd=repo, workflow=wf)

    state = res["main_checkout"]
    assert state["updated"] is True, state
    assert sorted(state["overwritten_ignored"]) == ["vendor/shot.png", "vendor/stray"], state
    assert "overwritten_ignored_truncated" not in state, state
    assert (repo / "vendor").is_file(), "the directory really was replaced by a file"
    assert not stray.exists(), "and the foreign repository really is gone"


def test_one_unaskable_path_costs_only_itself_and_not_the_paths_around_it(repo, tracker, tmp_path,
                                                                          monkeypatch):
    """THE BISECT, and it is what covers the OTHER producer — `_expand_if_directory`.

    The gitlink filter closes the everyday route; this shape gets past it, which is why both
    halves of the fix are here. Upstream turns the submodule into an ordinary FILE at the same
    path (`:160000 100644 … T`), so the entry is NOT a pure pointer move, is kept, and the walk
    hands back the files inside the submodule — which `check-ignore` still refuses to answer
    about. Measured: that merge is rc=0 and really does delete the submodule's working directory.

    `a.png` and `z.png` sit either side of `sub` in the diff's path order, and asserting BOTH is
    the point: `check-ignore` prints the answers it reached before dying (measured — stdout
    carries a complete, NUL-terminated `a.png\\0`), so keeping that prefix alone would recover
    `a.png` and still lose `z.png`. Only asking the halves separately recovers both.

    THE HONEST RESIDUE, measured and deliberately not claimed away: `sub/keep.png` really does die
    here and is NOT named, because no path inside a live gitlink can be asked about in the FORM this
    code must use — `--no-index` answers about them perfectly well (measured, rc=0), it just throws
    away the tracked-path filtering the report depends on. A reason, not an impossibility.

    So this pins "an unaskable path no longer costs the askable ones beside it", NOT "nothing is
    missed" — the present key is now incomplete in the same one-way sense the absent key already
    was. Filed as VMCP-247 (838) rather than guessed at, because naming those paths needs
    `--no-index` and that is a widening of the probe's surface, plus a product call about
    NON-ignored content, which is not an implementer's to make."""
    _api, wf = tracker
    _ignoring(repo, "*.png")
    _with_submodule(repo, tmp_path)
    for name in ("a.png", "z.png"):
        (repo / name).write_bytes(b"\x89PNG the human's own")
    (repo / "sub" / "keep.png").write_bytes(b"\x89PNG dies with the submodule, unnamed")

    # THE PRUNING WOULD OTHERWISE REMOVE THIS SHAPE, and saying so is the honest way to keep the
    # pin: `_index_gitlink_paths` keeps the walk out of `sub` on the shipped code, so this batch
    # would hold no unaskable path and the bisect would never be reached — measured, deleting the
    # bisect stopped failing anything the moment the pruning landed. What is forced here is that
    # read's own documented BEST-EFFORT branch, a failing `git ls-files`, which yields an empty set
    # and lets the walk back in. That is the real branch and not a stub: the bisect exists for the
    # fatal nobody has enumerated yet, and a backstop with no test is what this repo will not ship.
    real_run_git = workspace_cmd._run_git

    def ls_files_fails(args, *a, **k):
        if args and args[0] == "ls-files":
            return subprocess.CompletedProcess(args, 128, "", "forced: could not read the index")
        return real_run_git(args, *a, **k)
    monkeypatch.setattr(workspace_cmd, "_run_git", ls_files_fails)

    other = tmp_path / "sibling-sub2file"
    subprocess.run(["git", "clone", str(tmp_path / "origin.git"), str(other)],
                   check=True, capture_output=True)
    _git(other, "config", "user.email", "sibling@example.com")
    _git(other, "config", "user.name", "Sibling")
    _git(other, "rm", "-q", "--cached", "sub")
    shutil.rmtree(other / "sub", ignore_errors=True)
    (other / "sub").write_text("upstream made this an ordinary file\n")
    _git(other, "rm", "-q", "-f", ".gitmodules")
    for name in ("a.png", "z.png"):
        (other / name).write_text("UPSTREAM\n")
        _git(other, "add", "-f", name)
    _git(other, "add", "sub")
    _git(other, "commit", "-m", "sibling: the submodule becomes a file")
    _git(other, "push", "origin", "HEAD:main")

    res = gc_workspaces(cwd=repo, workflow=wf)

    state = res["main_checkout"]
    assert state["updated"] is True, state
    assert state["overwritten_ignored"] == ["a.png", "z.png"], state
    assert (repo / "sub").is_file(), "the submodule really was replaced by a file"


# --- VMCP-247 (838): the CONTRAST fails on ONE diff shape — an entry ON a gitlink's path --------
#
# Filed by the implementer of VMCP-246 (837) as the residue that card deliberately did not fix,
# and reproduced here before a word of prose moved. Everything below is command output on real
# git 2.50.1, taken at `469db93`, on shell stands outside this suite unless a test is named; the
# rounds and counterexamples credited to the second pass were run by it in its own clone.
#
# WHAT THE CHAIN CLAIMED. #710 -> 806 -> 835 -> 837 -> 836 all rest on one contrast, stated in
# `sync_main_checkout`, in `_ignored_paths_the_ff_will_overwrite` and in CLAUDE.md: an IGNORED file
# can be destroyed silently BECAUSE git protects the untracked-and-NOT-ignored case itself, so the
# only silence left to name is the ignored one. Both halves were re-measured rather than inherited:
#   * untracked-and-NOT-ignored at an incoming path .. rc=1, "The following untracked working tree
#                                                      files would be overwritten by merge", intact
#   * a MODIFIED TRACKED file at an incoming path .... rc=1, "Your local changes to the following
#                                                      files would be overwritten by merge", intact
# `test_the_untracked_but_not_ignored_contrast_refuses_and_reports_no_loss` is that half's pin.
#
# THE SHAPE THAT BREAKS IT is an incoming entry landing ON a live GITLINK's own path as a
# non-directory — a TYPECHANGE, `:160000 <non-gitlink> … T` — and in a stronger form than the card
# claimed. The checkout holds, inside that submodule's working directory, `keep.png` (ignored by the
# superproject's `*.png`), `precious.txt` (untracked and NOT ignored by any rule of EITHER repo) and
# a MODIFIED `inside.txt` (tracked — by the SUBMODULE, the only index that has heard of it;
# `git ls-files -s | grep -c '^sub/'` in the superproject is 0, so from up here every file in there
# is untracked):
#   * before ... `git status --porcelain` says ` M sub`; the submodule's own says ` M inside.txt`,
#                `?? keep.png`, `?? precious.txt`
#   * merge .... rc=0, no refusal, no warning, `mode change 160000 => 100644 sub`
#   * after .... all THREE gone, `git status --porcelain` EMPTY, `overwritten_ignored` naming none
#                of them (`['a.png']` when an ignored NEIGHBOUR is in the same commit, absent when
#                there is none)
# The dst mode is NOT part of the shape: `:160000 120000 … T`, upstream putting a SYMLINK there,
# destroys identically (second pass). So for that content there is no refusal to contrast with AND
# no key to name it: the loss is outside this probe's remit, not merely outside its reach.
# `--no-index` does answer about those paths (`sub/keep.png` rc=0, `sub/precious.txt` rc=1,
# `sub/inside.txt` rc=1) and even honours the SUBMODULE's own `.gitignore` — `check-ignore
# --no-index -v sub/scratch/notes.txt` prints `sub/.gitignore:1:scratch/` though the superproject
# has no such rule — so widening the ask would reach the IGNORED half and could never reach
# `precious.txt`, which no rule matches.
#
# SAY "ON THE GITLINK'S PATH", NEVER "INSIDE A LIVE GITLINK". This section, both docstrings and
# CLAUDE.md all carried the wider wording for one round, and the independent second pass disproved
# it by construction: keep the gitlink live and populated, and let the incoming commit land at a
# path INSIDE it (`:160000 000000 … D sub` plus `:000000 100644 … A sub/precious.txt`) —
# `merge --ff-only` is rc=1, "The following untracked working tree files would be overwritten by
# merge: sub/precious.txt", and all three files are intact. Reproduced independently here. The
# asymmetry survives with it: the same shape carrying an incoming `sub/keep.png` is rc=0 and the
# human's ignored bytes are replaced. A live gitlink is not a blanket blind spot; one shape is, and
# a guard written from the wide version would be scoped to the wrong set.
#
# WHAT ELSE BOUNDS THE CLASS, all built rather than reasoned about (the last two by the second
# pass). A plain gitlink DELETE (`:160000 000000 … D`) destroys NOTHING: rc=0 with `warning: unable
# to rmdir 'sub': Directory not empty`, and `sub/` still a directory holding `.git`, `inside.txt`
# and `precious.txt` — the submodule is still a working repo (`git -C sub status` answers). The
# gitlink becoming a real DIRECTORY is the same when its members are new, and REFUSED (rc=1) when an
# incoming member collides with a NOT-IGNORED one on disk. That qualifier is VMCP-265 (877), which
# measured the other half on `5782538`: collide with an IGNORED member (`sub/keep.png`) and it is
# rc=0, `updated: True`, the human's bytes replaced — and `overwritten_ignored` names only the
# ordinary neighbour OUTSIDE the gitlink, never the casualty in it. So the contrast holds on git's
# BEHAVIOUR axis and fails on the REPORT axis, which is the one the key exists for. It is the same
# asymmetry the paragraph above already measured for a path INSIDE the gitlink, and this row
# inherits it rather than escaping it. So this is the TYPECHANGE, not "any incoming change to
# a submodule".
#
# AND THE PRE-MERGE ` M sub` IS NOT A SIGNAL TO BUILD ON, which is what a "refuse when the gitlink
# is dirty" guard would need. Three ways it is absent while the loss is not:
#   * the doomed file is ignored by the SUBMODULE's own rules -> superproject status EMPTY, the
#     submodule's own status EMPTY, before AND after; the file is gone (fully invisible, the
#     #806 shape one level down)
#   * `submodule.<name>.ignore = all` .......... ` M sub` -> empty, file gone (#766's lesson again)
#   * `diff.ignoreSubmodules = all` ............ same, repo-wide
# `git diff-index --name-only HEAD` never names `sub` at all, with or without those knobs, so the
# module's own `_tracked_changes` read could not see it either.
#
# WHAT SURVIVES THE TYPECHANGE, because it is what the loss actually costs: `.git/modules/<name>`
# is untouched, so the submodule's COMMITTED content is recoverable (`cat-file -p HEAD:inside.txt`
# answered after the merge) — but not freely, since the surviving `core.worktree` points at the path
# that is now a file and EVERY read there dies `fatal: cannot chdir to '../../../sub'`; neither
# `-c core.worktree=` nor `git config --unset` helps, because git chdirs first, so it takes a hand
# edit of that config file. What dies for good is exactly the UNCOMMITTED work.
#
# WHAT THIS CARD DID AND DID NOT DO. It corrected the prose at the places the contrast is ASSERTED
# for the FF: `sync_main_checkout`'s founding paragraph (now carrying its THIRD correction), the
# probe's contrast bullet plus a bounds entry, and CLAUDE.md. `_incoming_displacing_paths` needed
# no edit — its typechange paragraph already said "ignored and NOT-ignored content alike" and filed
# this card. SKILL.md was checked and deliberately NOT touched, and the WARRANT is not the one an
# earlier draft of this section gave ("it asserts the contrast nowhere"): the second pass refuted
# that by finding the place — line ~369, for the RELEASE guard rather than the ff
# ("Untracked-но-НЕигнорируемое (`??`) гвард при НАСТРОЙКАХ ПО УМОЛЧАНИЮ видит и дерево держит — то
# есть дыра ровно на игнорируемом"). Two measured reasons stand in its place. For the FF it asserts
# nothing to correct: its `blocked` bullet already says "git отказался, И ЭТОТ ПРОГОН НЕ НАШЁЛ
# недописанного" and "Не пересказывай это человеку как гарантию". For the RELEASE guard this
# section used to say the sentence survives a live gitlink "for a reason of GIT's rather than the
# guard's" — `_inspect_status` BLIND at `([], [])` while `git worktree remove` refuses outright
# (`fatal: working trees containing submodules cannot be moved or removed`, rc=128, the file
# intact), this module never passing `--force`, and `--force` measured to destroy it, so the
# absent flag was load-bearing.
#
# **THAT WARRANT WAS FALSE, and VMCP-266 (878) is the correction — filed by this card's own
# independent reviewer, whose two halves were both right and whose PREMISE was not.** Git's
# refusal exists only on an INITIALISED submodule, and this pipeline never creates one: there is
# no `git submodule` call in the package and no `--recurse-submodules` on any of the three
# `worktree add` forms, so a gitlink's directory comes up EMPTY. Re-measured on real git 2.50.1,
# both states, same repo: POPULATED -> `git worktree remove` rc=128, the file intact;
# NOT populated -> **rc=0, the directory and the file gone**, no `--force` anywhere. So on the
# only configuration the drain produces, the guard was not saved by git at all, and the victim was
# not even limited to ignored content — an untracked-and-NOT-ignored `sub/precious.txt`, the exact
# thing the SKILL.md sentence PROMISES to hold, died at rc 0 with `released: true` and no key.
# `_inspect_status` returning `([], [])` was the only true half.
#
# What stands in its place is not prose: `_release_locked` now REFUSES a tree whose gitlink
# directory is non-empty (CODE_POPULATED_GITLINK, graded `kept`), pinned by
# test_release_refuses_a_tree_whose_gitlink_directory_is_not_empty and its empty-directory
# control. That also retires the residue this paragraph filed as VMCP-261 (863) — the rc=128 raise
# surfacing as `release-error` on every sweep — because the populated tree is now answered by the
# coded refusal BEFORE the removal is attempted, so the raise is no longer reached.
# ONE CLAIM OF THAT WARRANT WAS ALSO TOO WIDE and the second pass caught it: "the loss changes no
# agent ACTION, since there is nothing for the agent to name" holds on the rc=0 branch ONLY. Reached
# through #835's half-applying ff (a `chflags uchg` neighbour, built with it sorting before AND
# after `sub`), the same typechange gives `code: 'half-applied'`, a NON-EMPTY `half_applied` and a
# NON-empty `git status --porcelain` (` D .gitmodules`, ` T sub`) with all three files
# still gone — so there the agent does have something to name, and SKILL.md's existing instruction
# to report `half_applied` already covers it. THE VALUE IS DELIBERATELY NOT WRITTEN, and the earlier
# `['.gitmodules', 'sub']` was measured NOT to reproduce on this card's own shape (VMCP-265 (877)):
# `half_applied` is a DELTA of `_tracked_changes` before/after, so a VISIBLY dirty gitlink is
# already in the before-set and CANCELS — three victims, and `sub` off the list. That is #851's
# lesson one card later. What survives every configuration is the invariant above, which is what to
# assert. Its wording there calls the collateral an IGNORED file
# ("а лежавший там игнорируемый файл мог погибнуть"), which is now known to be narrower than the
# truth; that one word is left to whoever answers the product question rather than changed here.
# What the tool should DO — report the gitlink path, widen the ask with `--no-index`, or refuse the
# ff on that shape — went to a human via `call_human`, AND HAS BEEN ANSWERED. Two of the three are
# NO. Widening the ask with `--no-index` is declined as probe surface, and it could never have
# reached `precious.txt` anyway (rc=1 — no rule matches it, so the instrument is wrong by REMIT,
# not by reach). Refusing the ff is declined: "report and never refuse" stands, #806's reason for
# that rule (the rulebook TELLS agents to write `shot-<id>.png`) indeed does not apply to a
# submodule, where the rulebook tells them to write nothing — but the condition would have had to
# be "typechange onto a gitlink whose directory is non-empty", every populated submodule
# including one with nothing to lose, refusing again on every sweep. The THIRD — reporting the
# gitlink PATH, the only one that covers the non-ignored half — is NOT implemented and is not
# refused either; it stays with the card. So the shape below is a DOCUMENTED GAP rather than an
# open design, and the two mutation rounds recorded further down still guard it: either declined
# option, if it were ever implemented, reddens a test rather than landing quietly.
#
# THE TDD ROUND, recorded because a prose defect has no other red. Selection
# `-k lands_ON_a_gitlinks_path`, `collected 193 items / 192 deselected / 1 selected` in both, no
# `-q`, `FAILED `- and `ERROR `-prefixed
# lines counted separately: control (the shipped assertions) 0 failed, 0 ERROR lines; the round that
# asserted what the contrast bullet READ AS WRITTEN predicts — `updated is False`,
# `code == MAIN_SYNC_BLOCKED`, `precious.txt` still there — 1 failed, 0 ERROR lines, with
# `AssertionError: {'updated': True, …} assert (True is False)`. That failure IS the disproof; the
# assertions were then flipped to the measured truth.
#
# MUTATION SWEEP, VMCP-247 (838). Selection `tests/unit/test_workspace_cmd.py -p no:randomly`, no
# `-q`, `FAILED `- and `ERROR `-prefixed lines counted SEPARATELY, `collected 193` and 0 ERROR lines
# in every round including the control, `__pycache__` deleted before each and the target restored
# and sha256-verified after each. control 0 failed:
#   * simulate REFUSING the ff on a gitlink typechange .............. control 0 failed; 2 failed
#   * simulate NAMING the gitlink path in `overwritten_ignored` ..... control 0 failed; 2 failed
#   * drop the `rel in gitlinks` early return in `_expand_if_directory` control 0 failed; 1 failed
#     — RE-MEASURED, VMCP-265 (877). It shipped as `0 failed`, which was a round taken BEFORE the
#     pin below grew its direct assertion and never re-run. Re-run at `215d38d`, same whole-file
#     selection, `collected 226 items` in both rounds, control 0 failed and 0 ERROR lines: the
#     mutant kills `test_the_contrast_is_FALSE_when_the_incoming_entry_lands_ON_a_gitlinks_path`,
#     and the narrow `-k lands_ON_a_gitlinks_path` selection agrees (1 selected, same verdict). The
#     prose beside it — "the typechange pin below now asserts it directly" — was TRUE all along;
#     the round was what lagged, which is why a stale zero is worth more than a shrug here.
# The first two are the point: each is one of the parked product options, each fails BOTH this
# card's typechange pin and 837's `test_one_unaskable_path_costs_only_itself…`, so neither option
# can be implemented without a test noticing. The THIRD is a real find rather than a shrug — that
# early return was pinned by NOTHING (837's own seam test passes `gitlinks={'vendor/sub'}` with
# `rel='vendor'`, so it exercises the NESTED `dirnames[:]` prune and never the top-level one), and
# the typechange pin below now asserts it directly. The second pass swept the same file
# independently on selection `-k "gitlink or contrast"` and its
# rounds are worth reading beside these: `_add_capped` emitting the key unconditionally 3 failed
# (BOTH new tests), dropping the pointer-move skip 1 failed, dropping the NESTED prune 2 failed,
# `--diff-filter=ACMT` -> `ACMTD` 0 failed, and the probe returning `[]` unconditionally 1 failed —
# that last meaning NEITHER new test exercises the probe, which is why they are labelled
# characterisation pins and not coverage. THAT ROW SHIPPED AS `0 failed` AND THE BREAKDOWN OF ITS
# SELECTION WAS THE WHOLE-FILE `collected 193`, i.e. a number belonging to a DIFFERENT selection;
# both are VMCP-265 (877). Re-measured at `215d38d`, selection unchanged, `collected 226 items /
# 217 deselected / 9 selected` in BOTH rounds, control 0 failed, 0 ERROR lines: the mutant kills
# `test_the_expansion_does_not_walk_into_a_NESTED_gitlink`. The CONCLUSION above survives the
# correction untouched — that test is neither of 838's two new ones — but the zero did not, and a
# recorded zero over a LIVE pin is the error direction CLAUDE.md calls worse than inflation,
# because it reads as an invitation to delete the pin.
#
# THE SUITE'S OWN SCOPE, unchanged from 837 and worth repeating because it is what makes this
# latent: there are no submodules in this repository (`git ls-files -s | awk '$1=="160000"'` is
# empty — `.gitmodules` is the wrong evidence, a gitlink lives in the INDEX). `--gc` ships to
# consumers on the moving `stable` channel and `sync_main_checkout` runs in THEIR main checkout.


def _upstream_replaces_the_gitlink(tmp_path, name, with_a_file=True, path="sub"):
    """Land the shape this card is about: upstream drops the gitlink at `path`.

    `with_a_file=True` puts an ordinary FILE there (`:160000 100644 … T`, a TYPECHANGE);
    `False` removes it outright (`:160000 000000 … D`), which is the discriminator."""
    other = tmp_path / f"sibling-{name}"
    subprocess.run(["git", "clone", str(tmp_path / "origin.git"), str(other)],
                   check=True, capture_output=True)
    _git(other, "config", "user.email", "sibling@example.com")
    _git(other, "config", "user.name", "Sibling")
    _git(other, "rm", "-q", "--cached", path)
    shutil.rmtree(other / path, ignore_errors=True)
    if with_a_file:
        (other / path).write_text("upstream made this an ordinary file\n")
        _git(other, "add", path)
    _git(other, "rm", "-q", "-f", ".gitmodules")
    _git(other, "commit", "-m", f"sibling: {name}")
    _git(other, "push", "origin", "HEAD:main")


def test_the_contrast_is_FALSE_when_the_incoming_entry_lands_ON_a_gitlinks_path(repo, tracker,
                                                                                tmp_path):
    """THE CARD'S OWN INPUT, and it is largely a CHARACTERISATION pin — green on arrival by
    construction, because nothing about the code was wrong here and the defect was in the prose
    describing it. Say that rather than let a reader take a passing test for a fix.

    The founding contrast of #710 -> 806 -> 835 -> 837 -> 836 is that git protects
    untracked-and-NOT-ignored content itself, leaving the IGNORED case as the only silence worth a
    key. It fails on ONE diff shape — an entry landing ON a live gitlink's own path as a
    non-directory — and there in the strongest form available: all THREE shapes die at rc=0, the
    ignored one, the untracked-and-NOT-ignored one, and one that is MODIFIED and TRACKED (by the
    SUBMODULE; the superproject holds no index entry under a gitlink at all, which is why git never
    asks about them). NOT "inside a live gitlink" — an incoming path INSIDE one is refused in the
    ordinary way, which the section header above measures.

    `overwritten_ignored` is ABSENT here rather than short, and that is the sharper statement: the
    ignored NEIGHBOUR case is 837's test next door, so what this adds is an absent key over a
    three-file loss — the one-way reading's worst case, live.

    WHAT IT PINS ABOUT OUR CODE, since the rest is git's and the sweep says so: `_add_capped`'s
    only-when-non-empty rule (the second pass's round kills this and the other new test together),
    and the `rel in gitlinks` early return in `_expand_if_directory`, asserted DIRECTLY below
    because a sweep round found it pinned by nothing at all — 837's own seam test passes
    `gitlinks={'vendor/sub'}` against `rel='vendor'`, so it exercises the nested `dirnames[:]` prune
    and never this one. Of the three parked product options, two — naming the gitlink path and
    refusing the ff — each fail this test in a measured round; the third, widening the ask with
    `--no-index`, would only move a line if the widening answered non-empty here, which no
    assertion constrains, so do not read this as catching all three."""
    _api, wf = tracker
    _ignoring(repo, "*.png")
    _with_submodule(repo, tmp_path, inner={"inside.txt": "the submodule's own tracked file\n"})
    (repo / "sub" / "keep.png").write_bytes(b"\x89PNG the human's own, ignored by `*.png`")
    (repo / "sub" / "precious.txt").write_text("untracked and NOT ignored by any rule\n")
    (repo / "sub" / "inside.txt").write_text("MODIFIED, and TRACKED inside the submodule\n")
    # The superproject cannot see any of it: `check-ignore` is not even askable about those paths
    # (837), and the INDEX is the reason — asserted, since every claim above rests on it.
    assert not [p for p in _git(repo, "ls-files", "-s").splitlines() if "\tsub/" in p], (
        "the superproject holds ZERO index entries under a gitlink"
    )
    # `?? precious.txt` in the SUBMODULE's own status is what makes it untracked-and-NOT-ignored
    # from up here too: no rule of either repo matches it.
    assert "?? precious.txt" in _git(repo, "-C", "sub", "status", "--porcelain")

    _upstream_replaces_the_gitlink(tmp_path, "sub2file")

    # The gitlink path is handed back WHOLE rather than walked into — the top-level early return,
    # which nothing else in this file covers. Asked before the sweep so the state is the one the
    # merge then acts on.
    _git(repo, "fetch", "--no-recurse-submodules", "origin")
    gitlinks = workspace_cmd._index_gitlink_paths(repo, ["sub"])
    assert gitlinks == frozenset({"sub"}), ("the index is what names a gitlink", gitlinks)
    assert workspace_cmd._expand_if_directory(repo, "sub", gitlinks) == ["sub"], (
        "a gitlink path must not be expanded into the unaskable paths inside it"
    )

    res = gc_workspaces(cwd=repo, workflow=wf)

    state = res["main_checkout"]
    assert state["updated"] is True, ("no refusal on this shape", state)
    assert "overwritten_ignored" not in state, ("nor is any of it named", state)
    for dead in ("keep.png", "precious.txt", "inside.txt"):
        assert not (repo / "sub" / dead).exists(), (
            f"sub/{dead} must be gone — this pin is the loss, not a wish about it", dead
        )
    assert (repo / "sub").is_file(), "the submodule really was replaced by a file"
    assert _git(repo, "status", "--porcelain") == "", (
        "and on THIS branch git says nothing about it afterwards; through #835's half-applying ff "
        "the same typechange leaves ` T sub` instead (measured, section header above)"
    )


def test_a_plain_gitlink_DELETE_is_not_the_shape_and_destroys_nothing(repo, tracker, tmp_path):
    """THE DISCRIMINATOR, and it is what keeps the correction from becoming "submodules are
    unsafe". Upstream removes the gitlink OUTRIGHT instead of putting a file there
    (`:160000 000000 … D`): git then needs the path for nothing, refuses to remove a non-empty
    directory, SAYS SO on stderr, and everything survives — the human's untracked file, the
    submodule's own tracked file, and the submodule as a working repository.

    BE HONEST ABOUT WHAT IT PINS: of the four things it asserts, exactly ONE is about our code —
    `_add_capped`'s only-when-non-empty rule, which the second pass's round kills — and the other
    three are properties of GIT. In particular it pins NOTHING about `--diff-filter=ACMT`. An
    earlier draft credited that filter with the silence ("drops a `D` entry before the probe sees
    it, which is the right answer here for once"); the second pass measured both halves and it is
    not what makes it so — restoring `D` to the filter is `control 0 failed; that round 0 failed`,
    and with `D` restored `_ignored_of` still answers `[]`, because `check-ignore sub` is rc=1: the
    silence comes from the ignore answer. The filter is tidiness here, not the guard.

    What the shape itself buys is real: a guard written for "any incoming change to a submodule"
    would fire where nothing is at risk."""
    _api, wf = tracker
    _ignoring(repo, "*.png")
    _with_submodule(repo, tmp_path, inner={"inside.txt": "the submodule's own tracked file\n"})
    (repo / "sub" / "precious.txt").write_text("untracked and NOT ignored by any rule\n")

    _upstream_replaces_the_gitlink(tmp_path, "subdeleted", with_a_file=False)

    res = gc_workspaces(cwd=repo, workflow=wf)

    state = res["main_checkout"]
    assert state["updated"] is True, state
    assert "overwritten_ignored" not in state, state
    assert (repo / "sub" / "precious.txt").read_text() == (
        "untracked and NOT ignored by any rule\n"
    ), "a plain gitlink delete destroys nothing — git will not remove a non-empty directory"
    assert (repo / "sub" / "inside.txt").read_text() == "the submodule's own tracked file\n", (
        "the submodule's OWN tracked file survives too, which is what makes it 'nothing'"
    )
    assert (repo / "sub").is_dir(), "and the directory is still a directory"
    assert _git(repo, "-C", "sub", "status", "--porcelain") == "?? precious.txt", (
        "the submodule is still a working repository, not a husk"
    )


# --- VMCP-300 (1183): a DEFERRED tree used to be reported nowhere at all ---------------------


def _dead_review_tree(api, wf, repo, head, title, *, bounce=False) -> tuple[int, Path]:
    """A card taken all the way to Review, given a review worktree, then moved OUT of Review —
    the two real ways that happens: a human moves it to Done, or a `needs_work` verdict sends it
    back to Build. Returns (task_id, tree). The tree is left YOUNG, as on the real machine."""
    task = api.add_task(title, "Queue")
    wf.claim(task["id"])
    wf.advance(task["id"], to="build", spec="approach")
    wf.advance(task["id"], to="review", worklog="done", evidence="abc1234")
    tree = Path(ensure_workspace(task["id"], role="review", at=head, cwd=repo)["path"])
    if bounce:
        wf.review_task(task["id"], verdict="needs_work", report="repro'd; cause not addressed")
    else:
        api.task_bucket[task["id"]] = api.bucket_id("Done")   # the HUMAN moves it; no tool can
    return task["id"], tree


def test_gc_reports_the_dead_trees_it_deferred_instead_of_answering_three_empty_lists(repo,
                                                                                      tracker):
    """VMCP-300's whole subject: the SILENCE, not the loss.

    The stand is the live observation of #1183 rebuilt — three review trees whose cards have all
    LEFT Review (two moved to Done by the human, one bounced to Build by a `needs_work` verdict),
    every one of them created moments earlier, plus one legitimately live build tree. Before this
    card the sweep answered `{"released": [], "kept": [], "expected": []}`, which is
    byte-identical to a sweep that had nothing to do — so the three deferrals were unobservable
    from the pump, on the one command it runs every tick.

    Nothing here asserts a REAP. That is the point: the three trees must still be on disk
    afterwards, because the fix is a report and not a widening of the reaper.

    The live build tree is the control that keeps this test honest about WHICH skip is reported:
    it is young too, and it must NOT appear, because a live tree is a non-event rather than a
    deferral. Drop the `alive[role]` short-circuit and it turns up in `deferred`.

    THE SWEEP THAT PINS THIS CARD, one selection throughout — this whole FILE, 237 collected in
    every round, run in a clone with `__pycache__` cleared, PYTHONDONTWRITEBYTECODE=1 and
    `vikunja_mcp.__file__` printed each round: control 0 failed / 0 errors; delete the
    `deferred.append(...)` report so the skip goes silent again -> 5 failed; make the key
    unconditional (`if deferred:` -> `if True:`) -> 4 failed; re-value `DEFER_YOUNG` to a graded
    refusal's value (`"dirty"`) -> 1 failed; and, the round that matters most, neuter the grace
    window itself so the reaper WIDENS -> 9 failed. That last one is here deliberately: this card
    adds a report, and the only way to be sure it did not also loosen the reaper is to check that
    loosening the reaper is still LOUD."""
    api, wf = tracker
    head = _git(repo, "rev-parse", "HEAD")

    dead = dict(
        _dead_review_tree(api, wf, repo, head, "human moved it to Done") for _ in (1,)
    )
    dead.update([_dead_review_tree(api, wf, repo, head, "also Done")])
    dead.update([_dead_review_tree(api, wf, repo, head, "bounced back", bounce=True)])

    live_card = api.add_task("live build work", "Queue")
    wf.claim(live_card["id"])
    live_tree = Path(ensure_workspace(live_card["id"], cwd=repo)["path"])

    assert wf.review_task_ids() == [], "the board must read all three review cards as dead"

    res = gc_workspaces(cwd=repo, workflow=wf)

    assert res["released"] == [] and res["kept"] == [] and res["expected"] == []
    assert sorted(e["task_id"] for e in res["deferred"]) == sorted(dead)
    assert all(tree.exists() for tree in dead.values()), (
        "a deferred tree was REMOVED — this card adds a report, it does not widen the reaper"
    )
    assert live_tree.exists()
    assert live_card["id"] not in {e["task_id"] for e in res["deferred"]}, (
        "a LIVE tree was reported as deferred — a working tree is a non-event, not a skip"
    )
    for entry in res["deferred"]:
        assert entry["code"] == workspace_cmd.DEFER_YOUNG
        assert entry["released"] is False
        assert entry["role"] == "review"
        assert 0 <= entry["quiet_for_seconds"] < workspace_cmd._REAP_GRACE_SECONDS
        assert "a later sweep inspects it" in entry["reason"], entry["reason"]


def test_the_deferred_key_is_absent_when_the_sweep_declined_nothing(repo, tracker):
    """OPTIONAL, the `main_checkout` idiom — "present ⇒ read it".

    This is what keeps VMCP-68's cure intact: a key that is on EVERY payload is a key nobody
    reads, which is the disease that forced `kept` to be split in two. A tick that deferred
    nothing must return the payload it always returned, and so must a sweep whose only tree is
    alive.

    Pinned, same selection and same conditions as the flagship above: control 0 failed; make the
    key unconditional -> 4 failed, this test among them. Note WHICH four, because it says
    something this test alone does not: the pre-existing
    test_gc_says_nothing_about_a_main_checkout_that_is_already_current is one of them, so the
    optionality of the payload was already somebody else's promise before this card added a key
    to it."""
    api, wf = tracker
    assert "deferred" not in gc_workspaces(cwd=repo, workflow=wf)      # no worktrees at all

    task = api.add_task("live work", "Queue")
    wf.claim(task["id"])
    ensure_workspace(task["id"], cwd=repo)
    assert "deferred" not in gc_workspaces(cwd=repo, workflow=wf)      # one, and it is alive


def test_a_deferred_tree_is_reaped_by_a_later_sweep_and_stops_being_reported(repo, tracker):
    """POSTPONED, NEVER CANCELLED — the control that makes the report above readable, and the
    refutation of the card's own framing.

    #1183 reasoned that review trees "accumulate across a session and nothing on the board would
    ever say so". The second half was true and is what this card fixed. The first half is false
    FOR THE SHAPE THIS TEST BUILDS — a CLEAN review tree, which is what the observation was — and
    that matters, because a reader who thinks these trees were leaking reaches for the tempting
    fix, shortening or waiving the grace window for review trees, which widens the reaper into
    exactly the VMCP-71 race the window exists for, on the one role whose agent typically writes
    nothing and so has no other protection (see VMCP-84's note above `_REAP_GRACE_SECONDS`).

    DO NOT GENERALISE IT FURTHER — one round of this card did, and its own second pass refuted
    that by construction. A review tree holding an IN-TREE COMMIT genuinely does accumulate:
    measured on this same stand, three consecutive sweeps past the window each answered
    `expected: [(id, "unreachable-head")]` with the directory still there, and one holding a
    stray untracked file answered `kept: [(id, "dirty")]` twice over. Both are pre-existing,
    documented behaviour (`references/drain.md` says such a tree stays forever), and both are the
    OTHER side of the line this card draws: a deferral expires by itself, a refusal does not.

    Same trees, same sweep, one difference: age every marker past the window.

    Pinned, same selection and conditions as the flagship above: control 0 failed; neuter the
    grace window so the reaper widens -> 9 failed, this test among them (it then reaps on the
    FIRST sweep, so the deferral it asserts never happens); delete the report -> 5 failed, also
    including this one."""
    api, wf = tracker
    head = _git(repo, "rev-parse", "HEAD")
    dead = dict([_dead_review_tree(api, wf, repo, head, "done a"),
                 _dead_review_tree(api, wf, repo, head, "bounced", bounce=True)])

    first = gc_workspaces(cwd=repo, workflow=wf)
    assert sorted(e["task_id"] for e in first["deferred"]) == sorted(dead)
    assert first["released"] == []

    for tree in dead.values():
        _quiesce(tree)

    second = gc_workspaces(cwd=repo, workflow=wf)

    assert sorted(r["task_id"] for r in second["released"]) == sorted(dead)
    assert not any(tree.exists() for tree in dead.values())
    assert "deferred" not in second, (
        "the deferral must stop being reported the moment it is acted on — a standing line over "
        "a tree that is gone is the never-read signal all over again"
    )


def test_defer_codes_are_not_part_of_the_graded_worktree_vocabulary(repo, tracker):
    """The boundary asserted rather than promised, mirroring the `MAIN_SYNC_*` pin above.

    `CODE_*` is the CLOSED enumeration of per-worktree REFUSALS that `_keep_is_expected` grades
    cell by cell, pinned three separate ways, so a new member there reddens those pins until it
    is graded deliberately. A deferral is not a refusal — no guard ran, no verdict was reached —
    and it never reaches the grader, so it must not wear the grader's prefix. Both halves are
    asserted: the NAMES cannot overlap, and neither can the VALUES, since the grader keys on
    values and a colliding one is exactly what the separate prefix exists to prevent.

    Pinned, same selection and conditions as the flagship above: control 0 failed; re-value
    `DEFER_YOUNG` to `"dirty"`, a graded refusal's value -> 1 failed, and this is the ONLY test
    in the file that notices — which is the whole argument for writing it, since a colliding
    value is otherwise invisible until `_keep_is_expected` silently grades a deferral."""
    declared_codes = {n: v for n, v in vars(workspace_cmd).items()
                      if n.startswith("CODE_") and isinstance(v, str)}
    declared_defer = {n: v for n, v in vars(workspace_cmd).items()
                      if n.startswith("DEFER_") and isinstance(v, str)}
    assert declared_defer, "the deferral vocabulary vanished"
    assert not set(declared_codes) & set(declared_defer)
    assert not set(declared_codes.values()) & set(declared_defer.values())

    # ...and structurally: in a sweep that both defers a tree and refuses one, the two stay apart.
    api, wf = tracker
    head = _git(repo, "rev-parse", "HEAD")
    _dead_review_tree(api, wf, repo, head, "young and dead")           # -> deferred
    kept_tree = Path(ensure_workspace(42, cwd=repo)["path"])           # nothing on the board
    (kept_tree / "UNSAVED.txt").write_text("an agent's work\n")        # -> kept: dirty
    _quiesce(kept_tree)

    res = gc_workspaces(cwd=repo, workflow=wf)

    assert [e["code"] for e in res["kept"]] == [workspace_cmd.CODE_DIRTY], res["kept"]
    assert [e["code"] for e in res["deferred"]] == [workspace_cmd.DEFER_YOUNG]
    for entry in res["kept"] + res["expected"] + res["released"]:
        assert entry.get("code") not in set(declared_defer.values()), entry
    assert kept_tree.exists(), "the dirty tree was destroyed"


def test_the_cli_gc_line_carries_the_deferred_key(repo, tracker, monkeypatch, capsys):
    """The payload is a cross-process contract — the pump reads the JSON LINE, not the dict — so
    the key has to survive the CLI, and the ABSENT case has to stay absent there too."""
    api, wf = tracker
    monkeypatch.setattr(workspace_cmd, "_build_workflow", lambda root: (wf, None))
    monkeypatch.chdir(repo)

    assert run_workspace(["--gc"]) == 0
    assert "deferred" not in json.loads(capsys.readouterr().out)

    head = _git(repo, "rev-parse", "HEAD")
    task_id, _tree = _dead_review_tree(api, wf, repo, head, "young and dead")

    assert run_workspace(["--gc"]) == 0
    entries = json.loads(capsys.readouterr().out)["deferred"]
    assert [e["task_id"] for e in entries] == [task_id]
    assert entries[0]["code"] == workspace_cmd.DEFER_YOUNG
