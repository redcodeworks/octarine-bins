"""The orchestration of the weekly bump (bump_weekly.py): one independent branch, commit and pull request per pin, an unchanged rerun, a moved upstream (new PR, older one closed),
a stale PR, a failing pin, the dry run and the refusal to start from another checkout. Real git (a bare origin and a clone); gh, bump_run.py and bump_checks.py are stubs (the
decisions per pin are bump_run.py's own business, see test_pins.py)."""
import json
import os
import shutil
import subprocess
import sys

import pytest

from conftest import SCRIPTS, git

GH = '''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
db = Path(os.environ["DB"]); a = sys.argv[1:]
prs = json.loads((db / "prs.json").read_text())
if a[:2] == ["pr", "list"]:
    print(json.dumps(prs))
elif a[:2] == ["pr", "create"]:
    head, title = a[a.index("--head") + 1], a[a.index("--title") + 1]
    n = len(prs) + len((db / "created").read_text().splitlines() if (db / "created").exists() else []) + 100
    open(db / "created", "a").write(f"{head}|{title}\\n")
    (db / "prs.json").write_text(json.dumps(prs + [{"number": n, "headRefName": head}]))
    print(f"https://example.test/pull/{n}")
elif a[:2] == ["pr", "close"]:
    open(db / "closed", "a").write(a[2] + "\\n")
    (db / "prs.json").write_text(json.dumps([p for p in prs if str(p["number"]) != a[2]]))
'''
# moves its pin in pins.txt to UPSTREAM_<PIN> when that differs (UPSTREAM_<PIN>=fail: a failing check): one commit with the table row
BUMP_RUN = '''#!/usr/bin/env python3
import os, re, subprocess, sys
pin = sys.argv[1]
text = open("pins.txt").read()
cur = re.search(rf"^{pin}=(.*)$", text, re.M).group(1)
new = os.environ.get(f"UPSTREAM_{pin.upper()}", cur)
if new == "fail":
    print(f"::error::{pin}: cannot read upstream"); sys.exit(1)
if new != cur:
    open("pins.txt", "w").write(re.sub(rf"^{pin}=.*$", f"{pin}={new}", text, flags=re.M))
    subprocess.run(["git", "commit", "-q", "-a", "-m", f"Bump {pin} to {new}\\n\\n{pin} | {cur} | {new} | upstream | 3"], check=True)
'''
BUMP_CHECKS = '''#!/usr/bin/env python3
import os, sys
open(os.environ["DB"] + "/checks", "a").write(" ".join(sys.argv[1:]) + "\\n")
'''


class World:
    def __init__(self, tmp):
        self.tmp, self.db = tmp, tmp / "db"
        self.bin = tmp / "bin"
        self.bin.mkdir()
        (self.bin / "gh").write_text(GH)
        (self.bin / "gh").chmod(0o755)
        self.setup()

    def setup(self):
        """A fresh origin (testing with pins.txt and the stub scripts) and a clone on testing."""
        for d in ("origin.git", "clone", "db"):
            shutil.rmtree(self.tmp / d, ignore_errors=True)
        self.db.mkdir()
        (self.db / "prs.json").write_text("[]")
        self.origin, self.clone = self.tmp / "origin.git", self.tmp / "clone"
        subprocess.run(["git", "init", "-q", "--bare", "-b", "testing", str(self.origin)], check=True)
        subprocess.run(["git", "clone", "-q", str(self.origin), str(self.clone)], check=True, capture_output=True)
        git(self.clone, "checkout", "-q", "-b", "testing")
        scripts = self.clone / ".github/scripts"
        scripts.mkdir(parents=True)
        for f in ("bump_weekly.py", "ci.py"):
            shutil.copy(SCRIPTS / f, scripts)
        (scripts / "bump_run.py").write_text(BUMP_RUN)
        (scripts / "bump_checks.py").write_text(BUMP_CHECKS)
        (self.clone / "pins.txt").write_text("scx=1\nextest=1\nghostty=1\nbuilder=1\n")
        git(self.clone, "add", "-A")
        git(self.clone, "commit", "-q", "-m", "init")
        git(self.clone, "push", "-q", "origin", "testing")

    def run(self, **env):
        e = {**os.environ, "PATH": f"{self.bin}:{os.environ['PATH']}", "DB": str(self.db), "GITHUB_REPOSITORY": "o/r", "BUMP_CHECKS_WAIT": "0",
             "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t", **env}
        return subprocess.run([sys.executable, "-I", ".github/scripts/bump_weekly.py"], cwd=self.clone, env=e, capture_output=True, text=True)

    def lines(self, name):
        f = self.db / name
        return f.read_text().splitlines() if f.exists() else []

    def branches(self):
        return git(self.origin, "for-each-ref", "--format=%(refname:short)", "refs/heads/bump/*").split()

    def open_prs(self, pin):
        return [p for p in json.loads((self.db / "prs.json").read_text()) if p["headRefName"].startswith(f"bump/{pin}-")]


@pytest.fixture
def world(tmp_path):
    return World(tmp_path)


def test_two_moved_pins_give_two_independent_branches_one_commit_one_line_each(world):
    p = world.run(UPSTREAM_SCX="2", UPSTREAM_GHOSTTY="2")
    assert p.returncode == 0, p.stdout + p.stderr
    created = world.lines("created")
    assert len(created) == 2 and not any("extest" in c for c in created) and len(world.lines("checks")) == 2
    assert "Bump scx: 1 to 2" in created[0].split("|")[1] and all(len(c.split("|")[1]) <= 140 for c in created)
    for b in world.branches():  # cut from testing: one commit, one changed pin line (no chain)
        assert git(world.origin, "rev-list", "--count", f"testing..{b}") == "1"
        assert len(git(world.origin, "diff", "--numstat", "testing", b).splitlines()) == 1


def test_a_rerun_with_the_same_values_opens_and_closes_nothing(world):
    world.run(UPSTREAM_SCX="2", UPSTREAM_GHOSTTY="2")
    p = world.run(UPSTREAM_SCX="2", UPSTREAM_GHOSTTY="2")
    assert p.returncode == 0 and len(world.lines("created")) == 2 and world.lines("closed") == []


def test_upstream_moved_again_replaces_that_pins_pr_only(world):
    world.run(UPSTREAM_SCX="2", UPSTREAM_GHOSTTY="2")
    assert world.run(UPSTREAM_SCX="3", UPSTREAM_GHOSTTY="2").returncode == 0
    assert len(world.lines("created")) == 3 and len(world.lines("closed")) == 1
    assert len(world.open_prs("scx")) == 1 and len(world.open_prs("ghostty")) == 1


def test_a_pin_testing_already_has_closes_its_open_pr_as_stale(world):
    world.run(UPSTREAM_GHOSTTY="2")
    git(world.clone, "checkout", "-q", "testing")
    text = (world.clone / "pins.txt").read_text().replace("ghostty=1", "ghostty=2")  # the bump was merged
    (world.clone / "pins.txt").write_text(text)
    git(world.clone, "commit", "-q", "-am", "merged ghostty")
    git(world.clone, "push", "-q", "origin", "testing")
    p = world.run(UPSTREAM_GHOSTTY="2")
    assert p.returncode == 0 and len(world.lines("closed")) == 1 and len(world.lines("created")) == 1 and world.open_prs("ghostty") == []


def test_a_failing_pin_turns_the_run_red_and_the_others_still_go_in(world):
    p = world.run(UPSTREAM_SCX="fail", UPSTREAM_EXTEST="2", UPSTREAM_GHOSTTY="2")
    created = world.lines("created")
    assert p.returncode == 1 and len(created) == 2 and not any("scx" in c for c in created)


def test_dry_run_lists_prs_and_pushes_and_creates_nothing(world):
    p = world.run(DRY_RUN="1", UPSTREAM_SCX="2", UPSTREAM_EXTEST="2")
    assert p.returncode == 0 and p.stdout.count("would open") == 2
    assert world.lines("created") == [] and world.branches() == []


def test_a_real_run_from_a_checkout_that_is_not_origin_testing_is_refused(world):
    git(world.clone, "commit", "-q", "--allow-empty", "-m", "local")
    p = world.run(UPSTREAM_SCX="2")
    assert p.returncode == 1 and "must start from origin/testing" in p.stderr and world.lines("created") == []
