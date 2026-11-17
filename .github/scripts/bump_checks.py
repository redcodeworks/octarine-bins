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
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ci  # noqa: E402

MARKER = "Reported by bump_checks.py"


def warn(msg):
    print(f"::warning::{msg}")
    ci.append_to("GITHUB_STEP_SUMMARY", f"- {msg}")


def gh(*args, **kw):
    return subprocess.run(["gh", *args], capture_output=True, text=True, **kw)


def find_run(title):
    """The id of the dispatched Test run named `title`, or None."""
    proc = gh("run", "list", "--workflow", "test.yml", "--event", "workflow_dispatch", "--limit", "50", "--json", "databaseId,displayTitle")
    try:
        runs = json.loads(proc.stdout)
    except ValueError:
        return None
    return next((r["databaseId"] for r in runs if r["displayTitle"] == title), None)


def main(argv):
    if len(argv) != 2:
        print("usage: bump_checks.py <branch> <commit sha>", file=sys.stderr)
        return 2
    branch, sha = argv
    repo = os.environ["GITHUB_REPOSITORY"]
    base = os.environ.get("BUMP_BASE", "testing")
    wait_s, poll, run_max = ci.int_env("BUMP_CHECKS_WAIT", 90), ci.int_env("BUMP_CHECKS_POLL", 5), ci.int_env("BUMP_CHECKS_RUN_MAX", 900)
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        warn(f"bump checks for {branch}: not a commit sha '{sha}', nothing posted")
        return 0
    existing = ci.gh_api(f"repos/{repo}/commits/{sha}/check-runs?per_page=100") or {}
    if any(r.get("name") == "test" and (r.get("output") or {}).get("title") == MARKER for r in existing.get("check_runs", [])):
        print(f"{sha[:7]} already has its reported checks")
        return 0
    title = f"Test of {sha}"
    run_id = find_run(title)
    if run_id is None:
        if gh("workflow", "run", "test.yml", "--ref", base, "-f", f"ref={sha}").returncode == 0:
            print(f"Dispatched the Test workflow on {base} for {sha[:7]} ({branch})")
        else:
            warn(f"Could not dispatch the Test workflow on {branch}: start it by hand with: gh workflow run test.yml --ref {base} -f ref={sha}")
            return 0
    waited = 0
    while run_id is None and waited < wait_s:
        time.sleep(poll)
        waited += poll
        run_id = find_run(title)
    if run_id is None:
        warn(f"bump checks for {branch}: no Test run for {sha[:7]} found, nothing posted (re-run the bump workflow)")
        return 0
    try:
        subprocess.run(["gh", "run", "watch", str(run_id), "-R", repo, "--interval", "20"], capture_output=True, timeout=run_max)
    except subprocess.TimeoutExpired:
        pass
    try:
        info = json.loads(gh("run", "view", str(run_id), "-R", repo, "--json", "status,url,jobs").stdout)
    except ValueError:
        info = {}
    if info.get("status") != "completed":
        warn(f"bump checks for {branch}: the Test run {run_id} did not finish, nothing posted")
        return 0
    url = info["url"]
    for name in ("test", "secrets"):
        job = next((j for j in info.get("jobs", []) if j["name"] == name), {})
        conclusion = "success" if job.get("conclusion") == "success" else "failure"
        posted = gh("api", "-X", "POST", f"repos/{repo}/check-runs", "-f", f"name={name}", "-f", f"head_sha={sha}", "-f", "status=completed", "-f", f"conclusion={conclusion}",
                    "-f", f"details_url={url}", "-f", f"output[title]={MARKER}",
                    "-f", f"output[summary]=The job `{name}` of the dispatched Test run {url} finished with {conclusion}.")
        if posted.returncode == 0:
            print(f"posted check run {name}={conclusion} on {sha[:7]}")
        else:
            warn(f"bump checks for {branch}: could not post the check run {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
