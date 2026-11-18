#!/usr/bin/env python3
"""The weekly pin bump: ONE INDEPENDENT pull request into testing PER PIN that moved.

Usage: bump_weekly.py
(env GITHUB_TOKEN, GITHUB_REPOSITORY, GITHUB_API_URL; DRY_RUN=1; BUMP_PINS required:
the pin(s) to check, space separated, set per job by the workflow matrix; BUMP_BASE,
REPO_ROOT for the tests)

Every pin starts from current testing on its own branch bump/<pin>-YYYYMMDD with ONE
commit (the pin change, made by bump_run.py), so the PRs are not a chain and touch
different pin lines, and any subset can be merged in any order. Rules:
  - the pin already equals upstream (testing has it): nothing is opened; an open bump
    PR of that pin is closed with a comment (stale);
  - an open bump PR of the pin already carries the value upstream has now: it stays
    as it is, nothing new (idempotent reruns);
  - an open bump PR of the pin carries an OLDER value (upstream moved again): a new
    branch (bump/<pin>-YYYYMMDD-2 ...: a new name, no force-push is ever used) and a
    new PR are made, and the older one is closed with a comment pointing at the new
    one, so at most one bump PR per pin stays open;
  - a failing pin changes nothing, turns the run red and does not stop the other pins.
Every new PR gets the required `test` and `secrets` check runs from bump_checks.py
(keyed by its commit sha, so parallel PRs do not collide). DRY_RUN=1 does every
decision locally and prints what it would open, keep or close, pushing and opening
nothing. A real run needs HEAD to be origin/testing (the workflow checks testing out).
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
    """Run git with `args`; returns the CompletedProcess (text output), never raises.

    Callers look at `returncode` themselves, because most failures here (a branch
    that does not exist yet, a fetch that fails) are expected answers.
    """
    return subprocess.run(["git", *args], capture_output=True, text=True)


def summary(line):
    """Log `line` and append it to the job summary (the dry run's report too)."""
    ci.log.info(line)
    ci.append_to("GITHUB_STEP_SUMMARY", line)


def row_of(rev):
    """The pin row of a commit `rev`.

    That is the first line of its body: `pin | from | to | what changed upstream |
    days`, written by bump_run.commit_pin. Returns "" for a commit without a body.
    """
    return (git("log", "-1", "--format=%b", rev).stdout.splitlines() or [""])[0]


def to_of(rev):
    """The `to` value of a bump commit's table row ("" when the row has no such field).

    Two bump commits with the same `to` propose the same value, which is how a rerun
    recognizes a pull request that is already open.
    """
    fields = row_of(rev).split(" | ")
    return fields[2] if len(fields) > 2 else ""


def repo_parts():
    """The {owner, repo} keyword arguments of the SDK calls, from GITHUB_REPOSITORY."""
    owner, name = os.environ["GITHUB_REPOSITORY"].split("/")
    return {"owner": owner, "repo": name}


def open_prs(pin, base):
    """(number, branch) of every open PR into `base` whose branch is bump/<pin>-...

    Reads all open pages through the SDK; an API error propagates as its exception.
    """
    gh = ci.github()
    prs = gh.rest.paginate(gh.rest.pulls.list, state="open", base=base, **repo_parts())

    # only this pin's bump branches; other bump PRs are not ours to touch
    return [
        (pr.number, pr.head.ref) for pr in prs if pr.head.ref.startswith(f"bump/{pin}-")
    ]


def close_pr(number, branch, comment):
    """Comment on pull request `number`, close it and delete its branch `branch`.

    The comment says why, so nobody has to guess. An API error propagates.
    """
    gh = ci.github()
    rest = gh.rest
    rest.issues.create_comment(
        issue_number=number, data={"body": comment}, **repo_parts()
    )
    rest.pulls.update(pull_number=number, data={"state": "closed"}, **repo_parts())
    rest.git.delete_ref(ref=f"heads/{branch}", **repo_parts())


def open_pr(branch, title, body, base):
    """Open the pull request from `branch` into `base` and return its URL.

    On a refusal (RequestFailed) the likely cause, a repository setting, is logged as
    an error and the exception is raised again, so the run turns red.
    """
    gh = ci.github()

    # the one expected failure has a known fix in the repository settings
    try:
        return gh.rest.pulls.create(
            data={"title": title, "head": branch, "base": base, "body": body},
            **repo_parts(),
        ).parsed_data.html_url
    except RequestFailed:
        ci.log.error(
            "Could not open the pull request. Is Settings, Actions, General, "
            "Allow GitHub Actions to create and approve pull requests, on?"
        )
        raise


def free_branch(pin, day):
    """The first branch name for this pin and day that does not exist on origin.

    Tries bump/<pin>-<day>, then bump/<pin>-<day>-2, -3 ... : a new name every time,
    so a force-push is never needed. Asks origin with `git ls-remote`.
    """
    # candidate names in order, without end
    names = (f"bump/{pin}-{day}" + (f"-{n}" if n > 1 else "") for n in count(1))

    # the first candidate that origin does not have
    return next(
        name
        for name in names
        if git("ls-remote", "--exit-code", "--heads", "origin", name).returncode != 0
    )


def proposes(pr_branch, to):
    """Does the open pull request's branch `pr_branch` carry the bump value `to`?

    Fetches the branch and compares the `to` field of its commit's table row. A failed
    fetch counts as "no": the caller then opens its own pull request.
    """
    return (
        git("fetch", "-q", "origin", pr_branch).returncode == 0
        and to_of("FETCH_HEAD") == to
    )


def bump_pin(p, start, base, day, dry):
    """Check ONE pin on its own branch from `start` and open its pull request.

    `p` is the pin, `start` the commit of current `base`, `day` the YYYYMMDD of the
    branch name, `dry` whether to only report. Returns True when the pin FAILED (the
    run turns red, the other pins go on) and False otherwise, whether or not a pull
    request was opened, kept or closed.
    """
    # every pin starts from the same commit, whatever the previous pin left behind
    if git("checkout", "-q", "--detach", start).returncode != 0:
        return True
    branch = f"bump/{p}-{day}" if dry else free_branch(p, day)

    # a dry run only names the branch; a real run needs it created
    if git("checkout", "-q", "-B", branch).returncode != 0:
        return True

    # bump_run decides and makes the commit; its exit status is the pin's verdict
    if (
        subprocess.run([sys.executable, "-I", str(HERE / "bump_run.py"), p]).returncode
        != 0
    ):
        return True
    pending = open_prs(p, base)

    # no new commit: testing already has the upstream value, so open PRs are stale
    if not git("rev-list", f"{start}..HEAD").stdout.strip():
        # close each stale PR (a dry run only says so)
        for number, pr_branch in pending:
            # the dry run must not touch the repository
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

    # a bump commit without its table row cannot become a pull request
    if " | " not in row:
        ci.log.error(f"{p}: the bump commit has no table row")
        return True

    # an open PR that already proposes this value stays as it is (idempotent rerun)
    if keep := next(
        reversed([number for number, pr_branch in pending if proposes(pr_branch, to)]),
        None,
    ):
        summary(f"`{p}`: #{keep} already proposes {to}; nothing new")
        return False

    fields = row.split("|")
    title = f"Bump {p}: {fields[1].strip()} to {fields[2].strip()}"[:140]
    body = BODY.format(row=row, note=NOTES.get(p, ""), base=base)

    # the dry run reports what it would open and close, and stops
    if dry:
        summary(f"Dry run: would open {branch} ({commit[:7]}), title: {title}")
        # older PRs of this pin are superseded by the one that would be opened
        for number, pr_branch in pending:
            summary(
                f"Dry run: would close the older #{number} ({pr_branch}), superseded"
            )
        summary(body)
        return False

    # a branch that is not on origin cannot get a pull request
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

    # at most one open bump PR per pin: close the older ones, pointing at the new
    for number, pr_branch in pending:
        close_pr(
            number,
            pr_branch,
            f"Superseded by {url}: upstream moved again, so `{p}` has a newer bump pull request.",
        )

    # the new PR needs its `test` and `secrets` checks (its exit status is ignored)
    subprocess.run([sys.executable, "-I", str(HERE / "bump_checks.py"), branch, commit])
    return False


def main(argv=None):
    """Bump every pin named in BUMP_PINS, one pull request per pin that moved.

    Reads BUMP_PINS (required), BUMP_BASE (default testing), DRY_RUN and REPO_ROOT
    from the environment. Returns 0 when every pin was checked, 1 when a pin failed
    or the run cannot start (no pins, no fetch of the base, not on origin/<base> in a
    real run). The failing pin does not stop the others.
    """
    ci.arg_parser(__doc__).parse_args(argv)
    os.chdir(os.environ.get("REPO_ROOT") or HERE.parents[1])
    base = os.environ.get("BUMP_BASE", "testing")
    # the workflow matrix is the only list of pins; a hand run names them
    pins = os.environ.get("BUMP_PINS", "").split()

    # nothing to check means a misconfigured caller: say so
    if not pins:
        ci.log.error(
            "BUMP_PINS is empty: name the pin(s) to check, e.g. BUMP_PINS='scx extest'"
            " (the workflow sets it from its matrix)"
        )
        return 1
    dry = bool(os.environ.get("DRY_RUN"))

    # a base that cannot be fetched cannot be compared with
    if git("fetch", "-q", "origin", base).returncode != 0:
        ci.log.error(f"cannot fetch {base}")
        return 1
    start = git("rev-parse", "HEAD").stdout.strip()

    # a real run must start from the base, or its branches would carry other commits
    if not dry and start != git("rev-parse", f"origin/{base}").stdout.strip():
        ci.log.error(
            f"a real run must start from origin/{base} (this checkout is not it); "
            "use dry_run to try another ref"
        )
        return 1

    day = datetime.now(timezone.utc).strftime("%Y%m%d")
    summary("## Pin bumps")

    # one pin after the other: they share this checkout
    failures = [bump_pin(p, start, base, day, dry) for p in pins]
    git("checkout", "-q", "--detach", start)
    return 1 if any(failures) else 0


# run only as a script, not when a test imports the module
if __name__ == "__main__":
    sys.exit(main())
