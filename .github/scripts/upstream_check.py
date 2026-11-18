#!/usr/bin/env python3
"""The weekly decision for a source pin (scx, extest). Usage: upstream_check.py <component dir, for example src/scx>   (env REPO_ROOT for the tests)

Prints ONE line, the verb first (bump_run.py calls check() and reads the same answer as a Decision):
  current <what>             nothing newer (pinned tag is the latest release, or the pin is ahead of it)
  bump <tag> <commit>        a newer release exists: <tag> and the commit it resolves to (peeled for annotated tags)
  report <text>              commit-pinned project without a release or tag ahead of the pin: nothing to open
  forkbehind <tag> <commit> <text>  upstream has a newer release but our fork (UPSTREAM_REPO) does not contain its commit yet: no bump PR; Kevin syncs the fork
                             (`gh repo sync <fork>`) and the next run opens it
A GitHub API error (the SDK's own exception) ends the script: nothing is guessed, the next weekly run tries again.

Reads upstream.env. Releases are asked of UPSTREAM_WATCH (the real project); when that differs from UPSTREAM_REPO the build source is our fork, the pin is a commit and a bump needs
the fork to contain the new commit. Tag pin: the latest release (not a draft or pre-release) is newer when it sorts above the pinned tag by version. Commit pin (empty
UPSTREAM_TAG): the latest release, else the newest tag, counts only when it is NOT an ancestor of (or equal to) the pinned commit (compare status ahead or
diverged), so a release behind the pin, such as the one extest's pin sits one commit after, is never "newer". Only reads; nothing is written.
"""

import os
import sys
from pathlib import Path

from githubkit.exception import RequestFailed

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ci  # noqa: E402
import pins  # noqa: E402
from pins import Decision  # noqa: E402


def latest_ref(rest, owner, name, tag_fallback):
    """The tag of the latest release; a project without a release object (404) falls back to its newest tag when `tag_fallback`; "" when there is nothing."""
    try:
        return rest.repos.get_latest_release(owner, name).parsed_data.tag_name
    except RequestFailed as e:
        if e.response.status_code != 404:
            raise
    if not tag_fallback:
        return ""
    tags = rest.repos.list_tags(owner, name).parsed_data
    return tags[0].name if tags else ""


def fork_status(rest, owner, name, commit, branch):
    """How the fork's default branch compares with the commit (ahead, identical, ...); "missing" when the fork does not have the commit at all (404)."""
    try:
        return rest.repos.compare_commits(
            owner, name, f"{commit}...{branch}"
        ).parsed_data.status
    except RequestFailed as e:
        if e.response.status_code != 404:
            raise
        return "missing"


def check(root, directory):
    """The decision for the component in `directory` (for example src/scx)."""
    pin = pins.load_upstream(Path(root) / directory / "upstream.env")
    repo, fork, tag, pinned = pin.watch, pin.repo, pin.tag, pin.commit
    owner, name = repo.split("/")
    gh = ci.github()  # keep the client: gh.rest only holds a weak reference to it
    rest = gh.rest

    latest = latest_ref(rest, owner, name, tag_fallback=not tag or repo != fork)
    if not latest:
        if tag and repo == fork:
            raise ValueError(
                f"{repo} has no release but {directory} pins the tag {tag}"
            )
        return Decision(
            "report", text=f"{repo} has no release or tag; pinned at {pinned[:7]}"
        )

    def commit_of(ref):
        return rest.repos.get_commit(owner, name, ref).parsed_data.sha

    if tag and repo == fork:
        if latest == tag:
            return Decision("current", text=f"{tag} is the latest release")
        if ci.version(latest) <= ci.version(tag):
            return Decision(
                "current", text=f"pinned {tag} is ahead of the latest release {latest}"
            )
        return Decision("bump", tag=latest, commit=commit_of(latest))

    if repo != fork and latest == tag:
        return Decision("current", text=f"{tag} is the latest release")
    commit = commit_of(latest)
    status = rest.repos.compare_commits(
        owner, name, f"{pinned}...{commit}"
    ).parsed_data.status
    if status in ("ahead", "diverged"):
        if repo == fork:
            return Decision("bump", tag=latest, commit=commit)
        # a fork: only bump when it already contains the commit (compare the new commit with the fork's default branch; asking the commits API would also find commits
        # that only exist elsewhere in the fork network)
        fork_owner, fork_name = fork.split("/")
        branch = rest.repos.get(fork_owner, fork_name).parsed_data.default_branch
        fstatus = fork_status(rest, fork_owner, fork_name, commit, branch)
        if fstatus in ("ahead", "identical"):
            return Decision("bump", tag=latest, commit=commit)
        return Decision(
            "forkbehind",
            tag=latest,
            commit=commit,
            text=f"the fork {fork} is behind {repo}: {latest} is not in {fork}/{branch}",
        )
    if status == "identical":
        return Decision("current", text=f"the pinned commit is {latest}")
    return Decision(
        "report",
        text=f"latest release {latest} is {status} of the pinned commit {pinned[:7]}; nothing to bump",
    )


def main(argv=None):
    ap = ci.arg_parser(__doc__)
    ap.add_argument("directory", help="component directory, for example src/scx")
    args = ap.parse_args(argv)
    root = os.environ.get("REPO_ROOT") or Path(__file__).resolve().parents[2]
    ci.result(check(root, args.directory).line())
    return 0


if __name__ == "__main__":
    sys.exit(main())
