#!/usr/bin/env python3
"""Make the checks of a bump pull request count. Usage: bump_checks.py <branch> <commit sha>   (env GH_TOKEN, GITHUB_REPOSITORY)

Two facts found 2026-11-15 on #137 (check runs existed on the head commit, `gh pr checks` reported nothing, the PR stayed BLOCKED on the ruleset's required `test` and `secrets`):
  1. a workflow_dispatch run on the bump branch is NOT part of the pull request's status rollup;
  2. check runs posted through the API join the commit's GitHub Actions check suite when one exists (the dispatch run's own) and are not counted there either; on a commit
     WITHOUT a GitHub Actions suite the API creates one, and that one counts.
So the Test workflow is dispatched on the testing ref with `ref=<bump commit>` (it checks that commit out; its suite belongs to testing's commit, the bump commit stays without a
suite), this script waits for that run and posts check runs named `test` and `secrets` on the bump commit with the real conclusions of those two jobs. No token beyond GITHUB_TOKEN
(`checks: write`, `actions: write`); a run that did not finish posts nothing (the PR stays blocked: fail closed). Idempotent: nothing happens when the commit already has a check
run `test` with the marker title. Always exits 0 (a warning line says what is missing). Env: BUMP_BASE (the ref Test is dispatched on, default testing), BUMP_CHECKS_WAIT (seconds
to wait for the dispatched run to appear, 90; 0 = do not wait), BUMP_CHECKS_POLL (5), BUMP_CHECKS_RUN_MAX (seconds to watch the run, 900).
"""
import os
import re
import sys
import time
from pathlib import Path

from githubkit.exception import RequestError

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ci  # noqa: E402

MARKER = "Reported by bump_checks.py"
API_ERRORS = (RequestError, ci.GhTransportError)


def warn(msg):
    ci.log.warning(msg)
    ci.append_to("GITHUB_STEP_SUMMARY", f"- {msg}")


def find_run(repo, title):
    """The id of the dispatched Test run named `title`, or None."""
    runs = ci.gh_api(f"/repos/{repo}/actions/workflows/test.yml/runs", event="workflow_dispatch", per_page=50) or {}
    return next((r["id"] for r in runs.get("workflow_runs", []) if r.get("display_title") == title), None)


def wait_for_completion(repo, run_id, run_max, poll):
    """Poll the run until it is completed or `run_max` seconds have passed; returns the run (a dict) or None."""
    deadline = time.monotonic() + run_max
    while True:
        run = ci.gh_api(f"/repos/{repo}/actions/runs/{run_id}")
        if run and run.get("status") == "completed":
            return run
        if time.monotonic() >= deadline:
            return None
        time.sleep(poll)


def main(argv=None):
    ap = ci.arg_parser(__doc__)
    ap.add_argument("branch", help="the bump branch (for messages)")
    ap.add_argument("sha", help="the bump commit")
    args = ap.parse_args(argv)
    branch, sha = args.branch, args.sha
    repo = os.environ["GITHUB_REPOSITORY"]
    base = os.environ.get("BUMP_BASE", "testing")
    wait_s, poll, run_max = ci.int_env("BUMP_CHECKS_WAIT", 90), ci.int_env("BUMP_CHECKS_POLL", 5), ci.int_env("BUMP_CHECKS_RUN_MAX", 900)
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        warn(f"bump checks for {branch}: not a commit sha '{sha}', nothing posted")
        return 0
    existing = ci.gh_api(f"/repos/{repo}/commits/{sha}/check-runs", per_page=100) or {}
    if any(r.get("name") == "test" and (r.get("output") or {}).get("title") == MARKER for r in existing.get("check_runs", [])):
        ci.log.info(f"{sha[:7]} already has its reported checks")
        return 0
    title = f"Test of {sha}"
    run_id = find_run(repo, title)
    if run_id is None:
        try:
            ci.gh_request("POST", f"/repos/{repo}/actions/workflows/test.yml/dispatches", json={"ref": base, "inputs": {"ref": sha}})
            ci.log.info(f"Dispatched the Test workflow on {base} for {sha[:7]} ({branch})")
        except API_ERRORS:
            warn(f"Could not dispatch the Test workflow on {branch}: start it by hand with: gh workflow run test.yml --ref {base} -f ref={sha}")
            return 0
    waited = 0
    while run_id is None and waited < wait_s:  # a poll, not a retry: the dispatched run does not exist yet for a moment
        time.sleep(poll)
        waited += poll
        run_id = find_run(repo, title)
    if run_id is None:
        warn(f"bump checks for {branch}: no Test run for {sha[:7]} found, nothing posted (re-run the bump workflow)")
        return 0
    run = wait_for_completion(repo, run_id, run_max, 20)
    if run is None:
        warn(f"bump checks for {branch}: the Test run {run_id} did not finish, nothing posted")
        return 0
    url = run["html_url"]
    jobs = (ci.gh_api(f"/repos/{repo}/actions/runs/{run_id}/jobs", per_page=100) or {}).get("jobs", [])
    for name in ("test", "secrets"):
        job = next((j for j in jobs if j["name"] == name), {})
        conclusion = "success" if job.get("conclusion") == "success" else "failure"
        try:
            ci.gh_request("POST", f"/repos/{repo}/check-runs", json={
                "name": name, "head_sha": sha, "status": "completed", "conclusion": conclusion, "details_url": url,
                "output": {"title": MARKER, "summary": f"The job `{name}` of the dispatched Test run {url} finished with {conclusion}."}})
            ci.log.info(f"posted check run {name}={conclusion} on {sha[:7]}")
        except API_ERRORS:
            warn(f"bump checks for {branch}: could not post the check run {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
