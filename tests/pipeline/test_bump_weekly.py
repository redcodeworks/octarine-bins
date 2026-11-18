"""The orchestration of the weekly bump (bump_weekly.py): one independent branch, commit and pull request per pin, an unchanged rerun, a moved upstream (new PR, older one closed),
a stale PR, a failing pin, the dry run and the refusal to start from another checkout. Real git (a bare origin and a clone); bump_run.py and bump_checks.py are the stubs of tests/fakes (the
decisions per pin are bump_run.py's own business, see test_pins.py); the pull request calls go through githubkit to the fake GitHub API."""

import os
import shutil
import subprocess
import sys

import pytest

from conftest import FAKES, SCRIPTS, git


class World:
    def __init__(self, tmp, api):
        self.tmp, self.db, self.api = tmp, tmp / "db", api
        self.setup()

    def setup(self):
        """A fresh origin (testing with pins.txt and the stub scripts) and a clone on testing."""
        for d in ("origin.git", "clone", "db"):
            shutil.rmtree(self.tmp / d, ignore_errors=True)
        self.db.mkdir()
        self.api.prs, self.api.created, self.api.closed = [], [], []
        self.origin, self.clone = self.tmp / "origin.git", self.tmp / "clone"
        subprocess.run(["git", "init", "-q", "--bare", "-b", "testing", str(self.origin)], check=True)
        subprocess.run(["git", "clone", "-q", str(self.origin), str(self.clone)], check=True, capture_output=True)
        git(self.clone, "checkout", "-q", "-b", "testing")
        scripts = self.clone / ".github/scripts"
        scripts.mkdir(parents=True)
        for f in ("bump_weekly.py", "ci.py"):
            shutil.copy(SCRIPTS / f, scripts)
        for stub in ("bump_run.py", "bump_checks.py"):
            shutil.copy(FAKES / stub, scripts)
        (self.clone / "pins.txt").write_text("scx=1\nextest=1\nghostty=1\nbuilder=1\n")
        git(self.clone, "add", "-A")
        git(self.clone, "commit", "-q", "-m", "init")
        git(self.clone, "push", "-q", "origin", "testing")

    def run(self, **env):
        e = {
            **os.environ,
            "DB": str(self.db),
            "BUMP_CHECKS_WAIT": "0",
            "BUMP_PINS": "scx extest ghostty builder",
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@t",
            **env,
        }
        return subprocess.run([sys.executable, "-I", ".github/scripts/bump_weekly.py"], cwd=self.clone, env=e, capture_output=True, text=True)

    def lines(self, name):  # what the stub bump_checks.py recorded
        f = self.db / name
        return f.read_text().splitlines() if f.exists() else []

    def branches(self):
        return git(self.origin, "for-each-ref", "--format=%(refname:short)", "refs/heads/bump/*").split()

    def open_prs(self, pin):
        return [p for p in self.api.prs if p["head"]["ref"].startswith(f"bump/{pin}-")]


@pytest.fixture
def world(tmp_path, github):
    return World(tmp_path, github)


def test_two_moved_pins_give_two_independent_branches_one_commit_one_line_each(world):
    p = world.run(UPSTREAM_SCX="2", UPSTREAM_GHOSTTY="2")
    assert p.returncode == 0, p.stdout + p.stderr
    created = world.api.created
    assert len(created) == 2 and not any("extest" in head for head, _ in created) and len(world.lines("checks")) == 2
    assert "Bump scx: 1 to 2" in created[0][1] and all(len(title) <= 140 for _, title in created)
    for b in world.branches():  # cut from testing: one commit, one changed pin line (no chain)
        assert git(world.origin, "rev-list", "--count", f"testing..{b}") == "1"
        assert len(git(world.origin, "diff", "--numstat", "testing", b).splitlines()) == 1


def test_a_rerun_with_the_same_values_opens_and_closes_nothing(world):
    world.run(UPSTREAM_SCX="2", UPSTREAM_GHOSTTY="2")
    p = world.run(UPSTREAM_SCX="2", UPSTREAM_GHOSTTY="2")
    assert p.returncode == 0 and len(world.api.created) == 2 and world.api.closed == []


def test_upstream_moved_again_replaces_that_pins_pr_only(world):
    world.run(UPSTREAM_SCX="2", UPSTREAM_GHOSTTY="2")
    assert world.run(UPSTREAM_SCX="3", UPSTREAM_GHOSTTY="2").returncode == 0
    assert len(world.api.created) == 3 and len(world.api.closed) == 1
    assert len(world.open_prs("scx")) == 1 and len(world.open_prs("ghostty")) == 1


def test_a_pin_testing_already_has_closes_its_open_pr_as_stale(world):
    world.run(UPSTREAM_GHOSTTY="2")
    git(world.clone, "checkout", "-q", "testing")
    text = (world.clone / "pins.txt").read_text().replace("ghostty=1", "ghostty=2")  # the bump was merged
    (world.clone / "pins.txt").write_text(text)
    git(world.clone, "commit", "-q", "-am", "merged ghostty")
    git(world.clone, "push", "-q", "origin", "testing")
    p = world.run(UPSTREAM_GHOSTTY="2")
    assert p.returncode == 0 and len(world.api.closed) == 1 and len(world.api.created) == 1 and world.open_prs("ghostty") == []


def test_a_failing_pin_turns_the_run_red_and_the_others_still_go_in(world):
    p = world.run(UPSTREAM_SCX="fail", UPSTREAM_EXTEST="2", UPSTREAM_GHOSTTY="2")
    created = world.api.created
    assert p.returncode == 1 and len(created) == 2 and not any("scx" in head for head, _ in created)


def test_dry_run_lists_prs_and_pushes_and_creates_nothing(world):
    p = world.run(DRY_RUN="1", UPSTREAM_SCX="2", UPSTREAM_EXTEST="2")
    assert p.returncode == 0 and p.stderr.count("would open") == 2
    assert world.api.created == [] and world.branches() == []


def test_a_real_run_from_a_checkout_that_is_not_origin_testing_is_refused(world):
    git(world.clone, "commit", "-q", "--allow-empty", "-m", "local")
    p = world.run(UPSTREAM_SCX="2")
    assert p.returncode == 1 and "must start from origin/testing" in p.stderr and world.api.created == []
