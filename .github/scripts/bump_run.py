#!/usr/bin/env python3
"""The weekly bump check of ONE pin.

Usage: bump_run.py scx|extest|ghostty|builder|python-deps|python
(from the repository root, on a clean checkout; env GH_TOKEN, a git identity, skopeo
for the builder)

Decides (upstream_check.check / ghostty_check.check, or the registry for the builder)
and, when something is newer, writes the pin into the working tree and makes ONE
commit for it; the commit message ends with the row of the pull request's table:
pin | from | to | what changed upstream | days the new value has existed.
bump_weekly.py runs every pin on its own checkout of testing, one commit and one pull
request per pin. Exit 1 when the pin's check FAILED (a registry that cannot be
reached, a bad pin): the working tree is restored then. Prints one line per pin (also
into the job summary).

Kinds: `python` (the newest patch release of the minor in .python-version that the
pinned uv can install, read from uv's own download list: no network;
.python-version only), `python-deps` (`uv lock --upgrade`: newer locked versions of
the CI scripts' own dependencies, uv.lock only), the source pins (scx, extest;
ghostty, which also writes the Zig pin: Zig never moves alone) and the Fedora builder
digest of src/builder.env (the same major, the newest digest of the tag once it has
been the tag's value for MIN_AGE_DAYS, default 7: the creation date of the image is
the stand-in for "has not been replaced since", a registry has no tag history).
"""

import hashlib
import json
import os
import re
import subprocess
import sys
import tomllib
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ci  # noqa: E402
import ghostty_check  # noqa: E402
import pins  # noqa: E402
import registry  # noqa: E402
import upstream_check  # noqa: E402

BUILDER_PIN = re.compile(
    r"(registry\.fedoraproject\.org/fedora):([0-9]+)@(sha256:[0-9a-f]{64})"
)


class Fail(Exception):
    """A pin check that failed for a reason worth a red run (bad pin, no registry)."""


def say(line):
    """Log `line` and add it to the job summary as a bullet (one line per pin)."""
    ci.log.info(line)
    ci.append_to("GITHUB_STEP_SUMMARY", f"- {line}")


def parse_when(when):
    """A datetime as it is, an ISO date string as a datetime.

    Returns None when the string cannot be read. Accepts a trailing Z and more than
    six fractional digits (registries send both), which fromisoformat does not.
    """
    # anything that is not a string is already a datetime
    if not isinstance(when, str):
        return when

    # an unreadable date is a normal answer here, not an error
    try:
        return datetime.fromisoformat(
            re.sub(r"(\.\d{6})\d+", r"\1", when.replace("Z", "+00:00"))
        )
    except ValueError:
        return None


def days_since(when):
    """Whole days since a datetime or an ISO date string.

    Returns None when a string cannot be read or the date has no time zone (a naive
    date cannot be compared with now in UTC).
    """
    parsed = parse_when(when)

    return (
        int((datetime.now(timezone.utc) - parsed).total_seconds() // 86400)
        if parsed and parsed.tzinfo
        else None
    )


def commit_age(repo, sha):
    """Days since the committer date of commit `sha` in `repo` (owner/name).

    The age goes into the pull request's table ("days the new value has existed").
    A GitHub API error propagates as the SDK's own exception.
    """
    owner, name = repo.split("/")
    gh = ci.github()
    return days_since(
        gh.rest.repos.get_commit(owner, name, sha).parsed_data.commit.committer.date
    )


def git(*args):
    """Run git with `args`; returns the CompletedProcess (text output), never raises."""
    return subprocess.run(["git", *args], capture_output=True, text=True)


def commit_pin(pin, files, subject, current, proposed, what, days):
    """Commit a pin that was just written, after checking what changed.

    `files` are the only paths the writer may have touched; the last line of the
    commit message is the pull request's table row (`pin | from | to | what | days`).
    Returns the status line "moved <current> to <proposed>". Raises Fail when nothing
    changed, a file outside `files` changed, or git commit fails.
    """
    changed = git("diff", "--name-only").stdout.split()

    # a writer that changed nothing means the pin did not take the new value
    if not changed:
        raise Fail(f"the writer changed nothing for {proposed}")

    # a file outside the pin files must never ride along in a bump commit
    if stray := [f for f in changed if f not in files]:
        raise Fail(
            f"the writer changed {stray[0]}, which is not one of the pin files ({' '.join(files)})"
        )

    p = git(
        "commit",
        "-q",
        "-a",
        "-m",
        f"{subject}\n\n{pin} | {current} | {proposed} | {what} | {days}",
    )

    # a failed commit leaves the tree dirty; the caller restores it
    if p.returncode != 0:
        raise Fail(f"git commit failed: {p.stderr.strip()}")
    return f"moved {current} to {proposed}"


def bump_source(pin, root):
    """Check the scx or extest pin and, when a newer release exists, bump it.

    `pin` is the component name, `root` the repository root. Returns the status line
    (a bump commit was made, or why nothing was). Raises Fail for an invalid
    upstream.env or a pin file that cannot be written; an API error propagates.
    """
    path = f"src/{pin}/upstream.env"

    # a broken pin file is a failure, not "nothing to bump"
    try:
        cur = pins.load_upstream(Path(root) / path)
    except ValueError as e:
        raise Fail(f"invalid {path} ({e})")
    current = cur.tag or cur.commit[:7]
    decision = upstream_check.check(root, f"src/{pin}")
    ci.log.info(f"{pin}: {decision.line()}")

    # the verb of the weekly decision says what to do with the pin
    match decision.verb:
        case "bump":
            tag, commit = decision.tag, decision.commit
            old = cur.tag or cur.commit

            # the writer refuses a malformed tag or commit
            try:
                pins.set_upstream(Path(root) / path, tag, commit)
            except ValueError as e:
                raise Fail(f"cannot write {path} ({e})")
            return commit_pin(
                pin,
                [path],
                f"Bump {pin} to {tag}",
                current,
                tag,
                f"https://github.com/{cur.watch}/compare/{old}...{tag}",
                commit_age(cur.watch, commit),
            )
        case "forkbehind":
            return f"the fork is behind upstream ({decision.tag}): run gh repo sync on it first"
        case _:
            return f"nothing to bump: {decision.line()}"


def bump_ghostty(root):
    """Check the Ghostty pin and its Zig toolchain and bump both together.

    `root` is the repository root. Returns the status line (a bump commit was made,
    waiting for a Zig release, or nothing to do). Raises Fail for an invalid
    upstream.env or a pin pair that cannot be written; an API error propagates.
    """
    files = ["src/ghostty/upstream.env", "src/ghostty/zig.env"]

    # a broken pin file is a failure, not "nothing to bump"
    try:
        cur = pins.load_upstream(Path(root) / files[0])
    except ValueError:
        raise Fail("invalid upstream.env")
    current = (
        f"{cur.tag}, zig {pins.read_env(Path(root) / files[1]).get('ZIG_VERSION', '')}"
    )
    decision = ghostty_check.check(root)
    ci.log.info(f"ghostty: {decision.line()}")

    # the verb of the weekly decision says what to do with the pins
    match decision.verb:
        case "bump":
            # a new Ghostty tag brings the Zig minor it needs: one commit for both,
            # they cannot be applied apart
            tag, commit, zig = decision.tag, decision.commit, decision.zig

            # the writer refuses Zig values that are not an official release entry
            try:
                pins.write_ghostty(
                    Path(root) / "src/ghostty",
                    tag,
                    commit,
                    zig,
                    decision.zig_url,
                    decision.zig_sha256,
                    decision.zig_size,
                )
            except ValueError as e:
                raise Fail(f"writing the Ghostty and Zig pins failed ({e})")
            return commit_pin(
                "ghostty",
                files,
                f"Bump ghostty to {tag} (Zig {zig})",
                current,
                f"{tag}, zig {zig}",
                f"https://github.com/{cur.watch}/compare/{cur.tag}...{tag}",
                commit_age(cur.watch, commit),
            )
        case "wait":
            return f"nothing to bump yet: {decision.tag} {decision.text}"
        case _:
            return f"nothing to bump: {decision.line()}"


def builder_created(repo, major):
    """The creation date of the builder image's config, "" when it cannot be read.

    `repo` is the image without a tag, `major` the Fedora release (the tag). Asks for
    the linux/amd64 config, because a multi-arch tag has one per platform.
    """
    # an unreadable config is reported by the caller as a failure
    try:
        return (
            json.loads(
                registry.read("config", f"{repo}:{major}", linux_amd64=True)
            ).get("created")
            or ""
        )
    except registry.Absent, registry.ReadError, ValueError:
        return ""


def bump_builder(root):
    """Propose the newest digest of the Fedora builder tag once it is old enough.

    `root` is the repository root. Returns the status line (a bump commit was made,
    or why nothing was). Raises Fail when the pin is not digest-pinned or the
    registry cannot be read (a read error is never taken for "unchanged").
    """
    path = "src/builder.env"
    m = BUILDER_PIN.fullmatch(pins.read_env(Path(root) / path).get("BUILDER", ""))

    # the pin must have the registry/fedora:<major>@sha256:<digest> shape
    if not m:
        raise Fail(f"{path} has no digest-pinned BUILDER")
    repo, major, cur_digest = m.groups()
    current = f"{major}@{cur_digest[:19]}"

    # a registry that cannot be read is a failure, not "nothing to bump"
    try:
        manifest = registry.read("raw", f"{repo}:{major}")
    except registry.Absent, registry.ReadError:
        raise Fail(f"cannot resolve {repo}:{major}")
    new = "sha256:" + hashlib.sha256(manifest).hexdigest()

    # the tag still points at the pinned digest
    if new == cur_digest:
        return f"nothing to bump: the pin is the current digest of {major}"
    created = builder_created(repo, major)

    # without a creation date the age of the digest is unknown
    if not created:
        raise Fail(f"cannot read the creation date of {repo}:{major}")
    age = days_since(created) or 0
    minimum = int(os.environ.get("MIN_AGE_DAYS", "7"))

    # a digest that is too young may still be replaced by the next build
    if age < minimum:
        return (
            f"nothing to bump yet: {new[:19]} was built {age} days ago ({created}); "
            f"a digest is proposed once it has been the tag's value for {minimum} days"
        )
    pins.set_lines(Path(root) / path, {"BUILDER": f"{repo}:{major}@{new}"})
    return commit_pin(
        "builder",
        [path],
        f"Bump the Fedora {major} builder digest (built {created.split('T')[0]})",
        current,
        f"{major}@{new[:19]}",
        f"{repo}:{major}",
        age,
    )


def locked_versions(lock_text):
    """The locked version of every package in a uv.lock text, as {name: version}.

    Packages without a version (the project itself) are left out.
    """
    # one entry per locked package
    return {
        p["name"]: p["version"]
        for p in tomllib.loads(lock_text).get("package", [])
        if "version" in p
    }


def lock_changes(before, after):
    """The packages whose locked version differs between two uv.lock texts.

    Returns lines `name old to new` (added and removed packages included, with `-`
    for the missing side), sorted by name; an empty list when nothing moved.
    """
    old, new = locked_versions(before), locked_versions(after)

    # every package name seen on either side whose version differs
    return [
        f"{n} {old.get(n, '-')} to {new.get(n, '-')}"
        for n in sorted(old.keys() | new.keys())
        if old.get(n) != new.get(n)
    ]


def bump_python_deps(root):
    """Upgrade the locked Python dependencies of the CI scripts (uv.lock only).

    `root` is the repository root. Returns the status line (a bump commit was made, or
    nothing moved). Raises Fail when `uv lock --upgrade` fails.
    """
    path = Path(root) / "uv.lock"
    before = path.read_text()
    proc = subprocess.run(
        ["uv", "lock", "--upgrade"], cwd=root, capture_output=True, text=True
    )

    # a failed resolution leaves uv.lock as it was
    if proc.returncode != 0:
        raise Fail(f"uv lock --upgrade failed: {ci.text(proc.stderr, 200)}")
    changes = lock_changes(before, path.read_text())

    # an unchanged lock means every locked version is already current
    if not changes:
        return "nothing to bump: every locked version is current"

    # the table row shows the first four changes and counts the rest
    shown = ", ".join(changes[:4]) + (
        f" and {len(changes) - 4} more" if len(changes) > 4 else ""
    )
    return commit_pin(
        "python-deps",
        ["uv.lock"],
        "Bump the locked Python dependencies of the CI scripts",
        "the locked versions",
        f"{len(changes)} updated: {shown}",
        "https://github.com/redcodeworks/octarine-bins/blob/testing/pyproject.toml (the ranges stay, only uv.lock moves)",
        "n/a",
    )


def version_tuple(version):
    """A version string like `3.14.10` as the tuple (3, 14, 10), which compares right.

    Raises ValueError for anything that is not dot-separated integers.
    """
    return tuple(map(int, version.split(".")))


def newest_python(listing, current):
    """The newest default CPython of the same minor as `current` (`3.14.10`).

    `listing` is the parsed output of `uv python list --output-format json`. Only
    plain CPython builds with an exact x.y.z version count (no free-threaded or debug
    variants, no pre-releases). Returns the version string, or None when none matches.
    """
    want = tuple(current.split(".")[:2])

    # every installable x.y.z of the same minor, as comparable tuples
    found = {
        version_tuple(e["version"])
        for e in listing
        if e["implementation"] == "cpython"
        and e["variant"] == "default"
        and tuple(e["version"].split(".")[:2]) == want
        and e["version"].replace(".", "").isdigit()
    }

    return ".".join(map(str, max(found))) if found else None


def bump_python(root):
    """Move .python-version to the newest patch release of its minor that uv installs.

    `root` is the repository root. Returns the status line (a bump commit was made, or
    nothing newer). Raises Fail when the file is not an exact x.y.z version, uv fails
    or uv lists no CPython of that minor.
    """
    path = Path(root) / ".python-version"
    current = path.read_text().strip()

    # the pin is exact on purpose (CLAUDE.md): a range cannot be bumped
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", current):
        raise Fail(f".python-version is '{current}', not an exact x.y.z version")

    proc = subprocess.run(
        [
            "uv",
            "python",
            "list",
            "--only-downloads",
            "--output-format",
            "json",
            ".".join(current.split(".")[:2]),
        ],
        cwd=root,
        capture_output=True,
        text=True,
    )

    # without uv's list nothing can be decided
    if proc.returncode != 0:
        raise Fail(f"uv python list failed: {ci.text(proc.stderr, 200)}")
    new = newest_python(json.loads(proc.stdout), current)

    # no CPython of this minor at all means uv cannot install what is pinned
    if new is None:
        raise Fail(f"uv lists no CPython {'.'.join(current.split('.')[:2])}")

    # the pin already is the newest installable patch release
    if version_tuple(new) <= version_tuple(current):
        return (
            f"nothing to bump: {current} is the newest patch release this uv installs"
        )
    path.write_text(new + "\n")
    return commit_pin(
        "python",
        [".python-version"],
        f"Bump the CI Python to {new}",
        current,
        new,
        "https://www.python.org/downloads/release/python-" + new.replace(".", "") + "/",
        "n/a",
    )


def bump(pin, root="."):
    """Check one pin: dispatch to its kind and return the status line.

    `pin` is scx, extest, ghostty, builder, python-deps or python; `root` the
    repository root. Raises Fail for a dirty working tree (a bump commit would take
    unrelated changes), an unknown pin or any failure of the kind. The caller
    restores the working tree on failure.
    """
    # a dirty tree would end up in the bump commit
    if git("diff", "--quiet").returncode != 0:
        raise Fail("the working tree is not clean")

    # one branch per kind of pin
    match pin:
        case "scx" | "extest":
            return bump_source(pin, root)
        case "ghostty":
            return bump_ghostty(root)
        case "builder":
            return bump_builder(root)
        case "python-deps":
            return bump_python_deps(root)
        case "python":
            return bump_python(root)
        case _:
            raise Fail(f"unknown pin '{pin}'")


def main(argv=None):
    """Check the pin named on the command line and make its bump commit.

    Returns 0 when the check ran (bumped or not) and 1 when it FAILED; the working tree
    is restored in both failure paths. An unexpected error (an API error, a bad pin
    file) crashes with its own traceback after the restore.
    """
    ap = ci.arg_parser(__doc__)
    ap.add_argument(
        "pin",
        choices=["scx", "extest", "ghostty", "builder", "python-deps", "python"],
        help="the pin to check",
    )
    pin = ap.parse_args(argv).pin

    # restore the working tree on every way out that is not a bump
    try:
        status = bump(pin)
    except Fail as e:
        ci.log.error(f"{pin}: {e}")
        git("checkout", "-q", "--", ".")
        say(f"`{pin}`: FAILED, {e}")
        return 1
    except BaseException:
        # any other error crashes with its own traceback, after the tree is restored
        git("checkout", "-q", "--", ".")
        raise
    say(f"`{pin}`: {status}")
    return 0


# run only as a script, not when a test imports the module
if __name__ == "__main__":
    sys.exit(main())
