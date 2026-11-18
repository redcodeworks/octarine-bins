#!/usr/bin/env python3
"""Make the checks of a bump pull request count.

Usage: bump_checks.py <branch> <commit sha>   (env GH_TOKEN, GITHUB_REPOSITORY)

Two facts found 2026-11-15 on #137 (check runs existed on the head commit,
`gh pr checks` reported nothing, the PR stayed BLOCKED on the ruleset's required
`test` and `secrets`):
  1. a workflow_dispatch run on the bump branch is NOT part of the pull request's
     status rollup;
  2. check runs posted through the API join the commit's GitHub Actions check suite
     when one exists (the dispatch run's own) and are not counted there either; on a
     commit WITHOUT a GitHub Actions suite the API creates one, and that one counts.
So the Test workflow is dispatched on the testing ref with `ref=<bump commit>` (it
checks that commit out; its suite belongs to testing's commit, the bump commit stays
without a suite), this script waits for that run and posts check runs named `test`
and `secrets` on the bump commit with the real conclusions of those two jobs.

No token beyond GITHUB_TOKEN (`checks: write`, `actions: write`); a run that did not
finish posts nothing (the PR stays blocked: fail closed). Idempotent: nothing happens
when the commit already has a check run `test` with the marker title. A GitHub API
error ends the script with the SDK's error (bump_weekly.py ignores its exit status:
the PR then lacks the checks and stays blocked, fail closed); a missing run only
warns.

Env: BUMP_BASE (the ref Test is dispatched on, default testing), BUMP_CHECKS_WAIT
(seconds to wait for the dispatched run to appear, 90; 0 = do not wait),
BUMP_CHECKS_POLL (5), BUMP_CHECKS_RUN_MAX (seconds to watch the run, 900).
"""

import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "00-lib"))
import ci  # noqa: E402

MARKER = "Reported by bump_checks.py"


def warn(msg):
    """Log `msg` as a warning and add it to the job summary; never fails the step.

    Used for the cases where no checks are posted (the PR then stays blocked, which is
    the safe state), so the reason has to be visible without turning the run red.
    """
    ci.log.warning(msg)
    ci.append_to("GITHUB_STEP_SUMMARY", f"- {msg}")


def find_run(rest, repo, title):
    """The id of the dispatched Test run named `title`, or None.

    `rest` is the githubkit REST client, `repo` the {owner, repo} keyword arguments.
    Looks at the 50 newest workflow_dispatch runs of test.yml, so a run that is
    older than that is never found (it would have been dispatched long ago anyway).
    A GitHub API error propagates as the SDK's own exception.
    """
    runs = rest.actions.list_workflow_runs(
        workflow_id="test.yml", event="workflow_dispatch", per_page=50, **repo
    ).parsed_data.workflow_runs
    # the display title carries the commit sha, so it identifies the run of this bump
    return next((r.id for r in runs if r.display_title == title), None)


def wait_for_run(rest, repo, title, wait_s, poll):
    """The id of the dispatched run once it appears, or None when it never does.

    Polls every `poll` seconds for at most `wait_s` seconds. This is a poll, not a
    retry: a run that was dispatched a moment ago does not exist yet. With
    `wait_s` 0 it does not wait at all; `poll` must not be 0.
    """
    # one look per poll interval, up to the time limit
    for _ in range(0, wait_s, poll):
        time.sleep(poll)
        # stop at the first look that finds the run
        if (run_id := find_run(rest, repo, title)) is not None:
            return run_id
    return None


def wait_for_completion(rest, repo, run_id, run_max, poll):
    """Poll a workflow run until it is completed; returns the run, or None on timeout.

    Looks every `poll` seconds and gives up after `run_max` seconds (the run is then
    still going and nothing is posted). A GitHub API error propagates.
    """
    deadline = time.monotonic() + run_max

    # the loop ends by return: a finished run, or the deadline
    while True:
        run = rest.actions.get_workflow_run(run_id=run_id, **repo).parsed_data

        # a finished run is the answer, whatever its conclusion
        if run.status == "completed":
            return run

        # out of time: report "did not finish" to the caller
        if time.monotonic() >= deadline:
            return None
        time.sleep(poll)


def main(argv=None):
    """Dispatch the Test run for the bump commit and post its results as check runs.

    Takes the bump branch (for messages) and the bump commit sha. Returns 0 in every
    case that is not an API error: checks posted, already there, or nothing posted
    because the run is missing or did not finish (a warning, the PR stays blocked).
    """
    ap = ci.arg_parser(__doc__)
    ap.add_argument("branch", help="the bump branch (for messages)")
    ap.add_argument("sha", help="the bump commit")
    args = ap.parse_args(argv)
    branch, sha = args.branch, args.sha

    owner, name = os.environ["GITHUB_REPOSITORY"].split("/")
    repo = {"owner": owner, "repo": name}
    gh = ci.github()  # keep the client: gh.rest only holds a weak reference to it
    rest = gh.rest
    base = os.environ.get("BUMP_BASE", "testing")
    wait_s, poll, run_max = (
        ci.int_env("BUMP_CHECKS_WAIT", 90),
        ci.int_env("BUMP_CHECKS_POLL", 5),
        ci.int_env("BUMP_CHECKS_RUN_MAX", 900),
    )

    # only a full commit sha can name a run and a check's head
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        warn(f"bump checks for {branch}: not a commit sha '{sha}', nothing posted")
        return 0
    existing = rest.checks.list_for_ref(
        ref=sha, per_page=100, **repo
    ).parsed_data.check_runs

    # idempotent: a commit that already carries our marker is done
    if any(r.name == "test" and r.output.title == MARKER for r in existing):
        ci.log.info(f"{sha[:7]} already has its reported checks")
        return 0

    title = f"Test of {sha}"
    run_id = find_run(rest, repo, title)

    # dispatch only when no run exists yet (a rerun of this script finds the first)
    if run_id is None:
        rest.actions.create_workflow_dispatch(
            workflow_id="test.yml", data={"ref": base, "inputs": {"ref": sha}}, **repo
        )
        ci.log.info(f"Dispatched the Test workflow on {base} for {sha[:7]} ({branch})")

    # a freshly dispatched run takes a moment to show up
    if run_id is None:
        run_id = wait_for_run(rest, repo, title, wait_s, poll)

    # no run after waiting: post nothing, the PR stays blocked (fail closed)
    if run_id is None:
        warn(
            f"bump checks for {branch}: no Test run for {sha[:7]} found, "
            "nothing posted (re-run the bump workflow)"
        )
        return 0
    run = wait_for_completion(rest, repo, run_id, run_max, 20)

    # an unfinished run has no result to report: post nothing (fail closed)
    if run is None:
        warn(
            f"bump checks for {branch}: the Test run {run_id} did not finish, "
            "nothing posted"
        )
        return 0
    jobs = rest.actions.list_jobs_for_workflow_run(
        run_id=run_id, per_page=100, **repo
    ).parsed_data.jobs

    # one check run per required context, with the real conclusion of that job
    for name in ("test", "secrets"):
        # the job of this context in the run; a missing job counts as a failure
        job = next((j for j in jobs if j.name == name), None)
        conclusion = "success" if job and job.conclusion == "success" else "failure"
        summary = (
            f"The job `{name}` of the dispatched Test run {run.html_url} "
            f"finished with {conclusion}."
        )
        rest.checks.create(
            data={
                "name": name,
                "head_sha": sha,
                "status": "completed",
                "conclusion": conclusion,
                "details_url": run.html_url,
                "output": {"title": MARKER, "summary": summary},
            },
            **repo,
        )
        ci.log.info(f"posted check run {name}={conclusion} on {sha[:7]}")
    return 0


# run only as a script, not when a test imports the module
if __name__ == "__main__":
    sys.exit(main())
