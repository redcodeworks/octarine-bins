#!/usr/bin/env python3
"""The weekly decision for a source pin (scx, extest). Usage: upstream_check.py <component dir, for example src/scx>   (env REPO_ROOT for the tests)

Prints ONE line, the verb first (bump_run.py calls check() and reads the same answer as a tuple):
  current <what>             nothing newer (pinned tag is the latest release, or the pin is ahead of it)
  bump <tag> <commit>        a newer release exists: <tag> and the commit it resolves to (peeled for annotated tags)
  report <text>              commit-pinned project without a release or tag ahead of the pin: nothing to open
  forkbehind <tag> <commit> <text>  upstream has a newer release but our fork (UPSTREAM_REPO) does not contain its commit yet: no bump PR; Kevin syncs the fork
                             (`gh repo sync <fork>`) and the next run opens it
  unreachable <text>         upstream could not be asked; fail soft (exit 0), the next weekly run tries again

Reads upstream.env. Releases are asked of UPSTREAM_WATCH (the real project); when that differs from UPSTREAM_REPO the build source is our fork, the pin is a commit and a bump
needs the fork to contain the new commit. Tag pin: the latest release (not a draft or pre-release) is newer when it sorts above the pinned tag by version. Commit pin (empty
UPSTREAM_TAG): the latest release, else the newest tag, counts only when it is NOT an ancestor of (or equal to) the pinned commit (compare API status ahead or diverged), so a
release behind the pin, such as the one extest's pin sits one commit after, is never "newer". Only `gh api` reads; nothing is written.
"""
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ci  # noqa: E402
import pins  # noqa: E402
from pins import Decision  # noqa: E402

SHA = re.compile(r"[0-9a-f]{40}")


def unreachable(text):
    return Decision("unreachable", text=text)


def check(root, directory):
    """The decision for the component in `directory` (for example src/scx)."""
    try:
        pin = pins.load_upstream(Path(root) / directory / "upstream.env")
    except ValueError as e:
        return unreachable(f"invalid {directory}/upstream.env ({e})")
    repo, fork, tag, pinned = pin.watch, pin.repo, pin.tag, pin.commit

    release = ci.gh_api(f"/repos/{repo}/releases/latest", ci.Release)
    latest = release.tag_name if release else ""
    if not latest and (not tag or repo != fork):  # a commit-pinned project may have tags but no release object
        tags = ci.gh_api(f"/repos/{repo}/tags")
        if tags is None:
            return unreachable(f"cannot read releases or tags of {repo}")
        latest = tags[0]["name"] if tags else ""
    if not latest:
        if tag and repo == fork:
            return unreachable(f"cannot read the latest release of {repo}")
        return Decision("report", text=f"{repo} has no release or tag; pinned at {pinned[:7]}")

    def commit_of(ref):
        c = (ci.gh_api(f"/repos/{repo}/commits/{ref}") or {}).get("sha", "")
        return c if SHA.fullmatch(c) else None

    if tag and repo == fork:
        if latest == tag:
            return Decision("current", text=f"{tag} is the latest release")
        try:
            newer = ci.version(latest) > ci.version(tag)
        except ValueError as e:
            return unreachable(str(e))
        if not newer:
            return Decision("current", text=f"pinned {tag} is ahead of the latest release {latest}")
        commit = commit_of(latest)
        if not commit:
            return unreachable(f"cannot resolve {repo} {latest} to a commit")
        return Decision("bump", tag=latest, commit=commit)

    if repo != fork and latest == tag:
        return Decision("current", text=f"{tag} is the latest release")
    commit = commit_of(latest)
    if not commit:
        return unreachable(f"cannot resolve {repo} {latest} to a commit")
    compare = ci.gh_api(f"/repos/{repo}/compare/{pinned}...{commit}")
    if compare is None:
        return unreachable(f"cannot compare {repo} {pinned[:7]} with {latest}")
    status = compare.get("status", "")
    if status in ("ahead", "diverged"):
        if repo == fork:
            return Decision("bump", tag=latest, commit=commit)
        # a fork: only bump when it already contains the commit (compare the new commit with the fork's default branch; asking the commits API would also find commits
        # that only exist elsewhere in the fork network)
        info = ci.gh_api(f"/repos/{fork}")
        if info is None:
            return unreachable(f"cannot read {fork}")
        branch = info.get("default_branch", "")
        fstatus = (ci.gh_api(f"/repos/{fork}/compare/{commit}...{branch}") or {}).get("status", "missing")
        if fstatus in ("ahead", "identical"):
            return Decision("bump", tag=latest, commit=commit)
        return Decision("forkbehind", tag=latest, commit=commit, text=f"the fork {fork} is behind {repo}: {latest} is not in {fork}/{branch}")
    if status == "identical":
        return Decision("current", text=f"the pinned commit is {latest}")
    return Decision("report", text=f"latest release {latest} is {status} of the pinned commit {pinned[:7]}; nothing to bump")


def main(argv=None):
    ap = ci.arg_parser(__doc__)
    ap.add_argument("directory", help="component directory, for example src/scx")
    args = ap.parse_args(argv)
    root = os.environ.get("REPO_ROOT") or Path(__file__).resolve().parents[2]
    ci.log.info(check(root, args.directory).line())
    return 0


if __name__ == "__main__":
    sys.exit(main())
