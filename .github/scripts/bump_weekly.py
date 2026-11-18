#!/usr/bin/env python3
"""The weekly pin bump: ONE INDEPENDENT pull request into testing PER PIN that moved. Usage: bump_weekly.py   (env GITHUB_TOKEN, GITHUB_REPOSITORY, GITHUB_API_URL; DRY_RUN=1; BUMP_PINS required: the pin(s) to check, space separated, set per job by the workflow matrix; BUMP_BASE, REPO_ROOT for the tests)

Every pin starts from current testing on its own branch bump/<pin>-YYYYMMDD with ONE commit (the pin change, made by bump_run.py), so the PRs are not a chain and touch different
pin lines, and any subset can be merged in any order. Rules:
  - the pin already equals upstream (testing has it): nothing is opened; an open bump PR of that pin is closed with a comment (stale);
  - an open bump PR of the pin already carries the value upstream has now: it stays as it is, nothing new (idempotent reruns);
  - an open bump PR of the pin carries an OLDER value (upstream moved again): a new branch (bump/<pin>-YYYYMMDD-2 ...: a new name, no force-push is ever used) and a new PR are
    made, and the older one is closed with a comment pointing at the new one, so at most one bump PR per pin stays open;
  - a failing pin changes nothing, turns the run red and does not stop the other pins.
Every new PR gets the required `test` and `secrets` check runs from bump_checks.py (keyed by its commit sha, so parallel PRs do not collide). DRY_RUN=1 does every decision locally and
prints what it would open, keep or close, pushing and opening nothing. A real run needs HEAD to be origin/testing (the workflow checks testing out).
"""

import os
import subprocess
import sys
from datetime import datetime, timezone
from itertools import count
from pathlib import Path

from githubkit.exception import RequestFailed

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import ci  # noqa: E402

NOTES = {
    "scx": " Check the scheduler READMEs when scx moves.",
    "builder": " A builder bump changes the `FROM` of every stage: every component rebuilds once, with no layer cache hit.",
    "ghostty": " Zig moves only inside this commit (a Ghostty tag needs its Zig minor), never on its own.",
}
BODY = """One commit, one pin; this pull request is independent of the other bump pull requests (merge any subset, in any order, and close the rest).

| pin | from | to | what changed upstream | days the new value has existed |
|---|---|---|---|---|
| {row} |

Only the pin lines change.{note} Verified: the decision script compared the pin with upstream and the writer changed only the pin files. Not verified: nothing was built or booted by the bump; the build on `{base}` after the merge is the first build with this pin. The owner merges this pull request.

Checks: this commit is tested by the `Test` workflow (dispatched on `{base}` for it, because a pull request opened with the workflow token starts no workflow itself) and its `test` and `secrets` results are reported here as check runs. If they are missing, run the bump workflow again (`gh workflow run bump-pins.yml --ref {base}`): it makes a new branch and pull request and closes this one.

🤖 Generated with [Claude Code](https://claude.com/claude-code)"""


def git(*args):
    return subprocess.run(["git", *args], capture_output=True, text=True)


def summary(line):
    ci.log.info(line)
    ci.append_to("GITHUB_STEP_SUMMARY", line)


def row_of(rev):
    """The pin row of a commit: the first line of its body (pin | from | to | what changed upstream | days)."""
    return (git("log", "-1", "--format=%b", rev).stdout.splitlines() or [""])[0]


def to_of(rev):
    fields = row_of(rev).split(" | ")
    return fields[2] if len(fields) > 2 else ""


def repo_parts():
    owner, name = os.environ["GITHUB_REPOSITORY"].split("/")
    return {"owner": owner, "repo": name}


def open_prs(pin, base):
    """(number, branch) of every open PR into the base whose branch is bump/<pin>-..."""
    gh = ci.github()
    prs = gh.rest.paginate(gh.rest.pulls.list, state="open", base=base, **repo_parts())
    return [
        (pr.number, pr.head.ref) for pr in prs if pr.head.ref.startswith(f"bump/{pin}-")
    ]


def close_pr(number, branch, comment):
    """Comment on a pull request, close it and delete its branch."""
    gh = ci.github()
    rest = gh.rest
    rest.issues.create_comment(
        issue_number=number, data={"body": comment}, **repo_parts()
    )
    rest.pulls.update(pull_number=number, data={"state": "closed"}, **repo_parts())
    rest.git.delete_ref(ref=f"heads/{branch}", **repo_parts())


def open_pr(branch, title, body, base):
    """Open the pull request and return its URL."""
    gh = ci.github()
    try:
        return gh.rest.pulls.create(
            data={"title": title, "head": branch, "base": base, "body": body},
            **repo_parts(),
        ).parsed_data.html_url
    except RequestFailed:
        ci.log.error(
            "Could not open the pull request. Is Settings, Actions, General, Allow GitHub Actions to create and approve pull requests, on?"
        )
        raise


def free_branch(pin, day):
    """bump/<pin>-<day>, or the first of bump/<pin>-<day>-2, -3 ... that does not exist on origin yet (a new name, never a force-push)."""
    names = (f"bump/{pin}-{day}" + (f"-{n}" if n > 1 else "") for n in count(1))
    return next(
        name
        for name in names
        if git("ls-remote", "--exit-code", "--heads", "origin", name).returncode != 0
    )


def proposes(pr_branch, to):
    """Does the open pull request's branch carry the value `to`?"""
    return (
        git("fetch", "-q", "origin", pr_branch).returncode == 0
        and to_of("FETCH_HEAD") == to
    )


def bump_pin(p, start, base, day, dry):
    """Check ONE pin on its own branch from `start`; returns True when it FAILED (the run turns red, the other pins go on)."""
    if git("checkout", "-q", "--detach", start).returncode != 0:
        return True
    branch = f"bump/{p}-{day}" if dry else free_branch(p, day)
    if git("checkout", "-q", "-B", branch).returncode != 0:
        return True
    if (
        subprocess.run([sys.executable, "-I", str(HERE / "bump_run.py"), p]).returncode
        != 0
    ):
        return True
    pending = open_prs(p, base)
    if not git("rev-list", f"{start}..HEAD").stdout.strip():
        for number, pr_branch in pending:
            if dry:
                summary(
                    f"Dry run: would close #{number} ({p}: testing already has the upstream value)"
                )
            else:
                close_pr(
                    number,
                    pr_branch,
                    f"Closing: `{p}` already equals upstream on `{base}`, so this bump is not needed any more.",
                )
        return False
    commit = git("rev-parse", "HEAD").stdout.strip()
    row, to = row_of(commit), to_of(commit)
    if " | " not in row:
        ci.log.error(f"{p}: the bump commit has no table row")
        return True
    if keep := next(
        reversed([number for number, pr_branch in pending if proposes(pr_branch, to)]),
        None,
    ):
        summary(f"`{p}`: #{keep} already proposes {to}; nothing new")
        return False
    fields = row.split("|")
    title = f"Bump {p}: {fields[1].strip()} to {fields[2].strip()}"[:140]
    body = BODY.format(row=row, note=NOTES.get(p, ""), base=base)
    if dry:
        summary(f"Dry run: would open {branch} ({commit[:7]}), title: {title}")
        for number, pr_branch in pending:
            summary(
                f"Dry run: would close the older #{number} ({pr_branch}), superseded"
            )
        summary(body)
        return False
    if (
        git(
            "push", "-q", "origin", f"refs/heads/{branch}:refs/heads/{branch}"
        ).returncode
        != 0
    ):
        ci.log.error(f"cannot push {branch}")
        return True
    url = open_pr(branch, title, body, base)
    summary(f"`{p}`: opened {url}")
    for number, pr_branch in pending:
        close_pr(
            number,
            pr_branch,
            f"Superseded by {url}: upstream moved again, so `{p}` has a newer bump pull request.",
        )
    subprocess.run([sys.executable, "-I", str(HERE / "bump_checks.py"), branch, commit])
    return False


def main(argv=None):
    ci.arg_parser(__doc__).parse_args(argv)
    os.chdir(os.environ.get("REPO_ROOT") or HERE.parents[1])
    base = os.environ.get("BUMP_BASE", "testing")
    pins = os.environ.get(
        "BUMP_PINS", ""
    ).split()  # the workflow matrix is the only list of pins; a hand run names them
    if not pins:
        ci.log.error(
            "BUMP_PINS is empty: name the pin(s) to check, e.g. BUMP_PINS='scx extest' (the workflow sets it from its matrix)"
        )
        return 1
    dry = bool(os.environ.get("DRY_RUN"))
    if git("fetch", "-q", "origin", base).returncode != 0:
        ci.log.error(f"cannot fetch {base}")
        return 1
    start = git("rev-parse", "HEAD").stdout.strip()
    if not dry and start != git("rev-parse", f"origin/{base}").stdout.strip():
        ci.log.error(
            f"a real run must start from origin/{base} (this checkout is not it); use dry_run to try another ref"
        )
        return 1
    day = datetime.now(timezone.utc).strftime("%Y%m%d")
    summary("## Pin bumps")
    failures = [
        bump_pin(p, start, base, day, dry) for p in pins
    ]  # one pin after the other: they share this checkout
    git("checkout", "-q", "--detach", start)
    return 1 if any(failures) else 0


if __name__ == "__main__":
    sys.exit(main())
