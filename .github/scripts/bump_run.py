#!/usr/bin/env python3
"""The weekly bump check of ONE pin. Usage: bump_run.py scx|extest|ghostty|builder|python-deps|python   (from the repository root, on a clean checkout; GH_TOKEN, a git identity, skopeo for the builder)

Decides (upstream_check.check / ghostty_check.check, or the registry for the builder) and, when something is newer, writes the pin into the working tree and makes ONE commit for it;
the commit message ends with the row of the pull request's table: pin | from | to | what changed upstream | days the new value has existed. bump_weekly.py runs every pin on its
own checkout of testing, one commit and one pull request per pin. Exit 1 when the pin's check FAILED (a registry that cannot be reached, a bad pin): the working tree is restored
then. Prints one line per pin (also into the job summary).

Kinds: `python` (the newest patch release of the minor in .python-version that the pinned uv can install, read from uv's own download list: no network; .python-version only), `python-deps` (`uv lock --upgrade`: newer locked versions of the CI scripts' own dependencies, uv.lock only), the source pins (scx, extest; ghostty, which also writes the Zig pin: Zig never moves alone) and the Fedora builder digest of src/builder.env (the same major, the newest digest
of the tag once it has been the tag's value for MIN_AGE_DAYS, default 7: the creation date of the image is the stand-in for "has not been replaced since", a registry has no tag history).
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

BUILDER_PIN = re.compile(r"(registry\.fedoraproject\.org/fedora):([0-9]+)@(sha256:[0-9a-f]{64})")


class Fail(Exception):
    pass


def say(line):
    ci.log.info(line)
    ci.append_to("GITHUB_STEP_SUMMARY", f"- {line}")


def days_since(when):
    """Whole days since a datetime or an ISO date string; None when a string cannot be read."""
    if isinstance(when, str):
        try:
            when = datetime.fromisoformat(re.sub(r"(\.\d{6})\d+", r"\1", when.replace("Z", "+00:00")))
        except ValueError:
            return None
    return int((datetime.now(timezone.utc) - when).total_seconds() // 86400) if when.tzinfo else None


def commit_age(repo, sha):
    owner, name = repo.split("/")
    gh = ci.github()
    return days_since(gh.rest.repos.get_commit(owner, name, sha).parsed_data.commit.committer.date)


def git(*args):
    return subprocess.run(["git", *args], capture_output=True, text=True)


def commit_pin(pin, files, subject, current, proposed, what, days):
    """The pin was written: check that only its files changed, commit them (the last line of the message is the table row)."""
    changed = git("diff", "--name-only").stdout.split()
    if not changed:
        raise Fail(f"the writer changed nothing for {proposed}")
    for f in changed:
        if f not in files:
            raise Fail(f"the writer changed {f}, which is not one of the pin files ({' '.join(files)})")
    p = git("commit", "-q", "-a", "-m", f"{subject}\n\n{pin} | {current} | {proposed} | {what} | {days}")
    if p.returncode != 0:
        raise Fail(f"git commit failed: {p.stderr.strip()}")
    return f"moved {current} to {proposed}"


def bump_source(pin, root):
    path = f"src/{pin}/upstream.env"
    try:
        cur = pins.load_upstream(Path(root) / path)
    except ValueError as e:
        raise Fail(f"invalid {path} ({e})")
    current = cur.tag or cur.commit[:7]
    decision = upstream_check.check(root, f"src/{pin}")
    ci.log.info(f"{pin}: {decision.line()}")
    match decision.verb:
        case "bump":
            tag, commit = decision.tag, decision.commit
            old = cur.tag or cur.commit
            try:
                pins.set_upstream(Path(root) / path, tag, commit)
            except ValueError as e:
                raise Fail(f"cannot write {path} ({e})")
            return commit_pin(
                pin, [path], f"Bump {pin} to {tag}", current, tag, f"https://github.com/{cur.watch}/compare/{old}...{tag}", commit_age(cur.watch, commit)
            )
        case "forkbehind":
            return f"the fork is behind upstream ({decision.tag}): run gh repo sync on it first"
        case _:
            return f"nothing to bump: {decision.line()}"


def bump_ghostty(root):
    files = ["src/ghostty/upstream.env", "src/ghostty/zig.env"]
    try:
        cur = pins.load_upstream(Path(root) / files[0])
    except ValueError:
        raise Fail("invalid upstream.env")
    current = f"{cur.tag}, zig {pins.read_env(Path(root) / files[1]).get('ZIG_VERSION', '')}"
    decision = ghostty_check.check(root)
    ci.log.info(f"ghostty: {decision.line()}")
    match decision.verb:
        case "bump":  # a new Ghostty tag brings the Zig minor it needs: one commit for both, they cannot be applied apart
            tag, commit, zig = decision.tag, decision.commit, decision.zig
            try:
                pins.write_ghostty(Path(root) / "src/ghostty", tag, commit, zig, decision.zig_url, decision.zig_sha256, decision.zig_size)
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


def bump_builder(root):
    path = "src/builder.env"
    m = BUILDER_PIN.fullmatch(pins.read_env(Path(root) / path).get("BUILDER", ""))
    if not m:
        raise Fail(f"{path} has no digest-pinned BUILDER")
    repo, major, cur_digest = m.groups()
    current = f"{major}@{cur_digest[:19]}"
    try:
        manifest = registry.read("raw", f"{repo}:{major}")
    except registry.Absent, registry.ReadError:
        raise Fail(f"cannot resolve {repo}:{major}")
    new = "sha256:" + hashlib.sha256(manifest).hexdigest()
    if new == cur_digest:
        return f"nothing to bump: the pin is the current digest of {major}"
    try:
        created = json.loads(registry.read("config", f"{repo}:{major}", linux_amd64=True)).get("created") or ""
    except registry.Absent, registry.ReadError, ValueError:
        created = ""
    if not created:
        raise Fail(f"cannot read the creation date of {repo}:{major}")
    age = days_since(created) or 0
    minimum = int(os.environ.get("MIN_AGE_DAYS", "7"))
    if age < minimum:
        return f"nothing to bump yet: {new[:19]} was built {age} days ago ({created}); a digest is proposed once it has been the tag's value for {minimum} days"
    pins.set_lines(Path(root) / path, {"BUILDER": f"{repo}:{major}@{new}"})
    return commit_pin(
        "builder", [path], f"Bump the Fedora {major} builder digest (built {created.split('T')[0]})", current, f"{major}@{new[:19]}", f"{repo}:{major}", age
    )


def locked_versions(lock_text):
    """name -> version of every package in a uv.lock."""
    return {p["name"]: p["version"] for p in tomllib.loads(lock_text).get("package", []) if "version" in p}


def lock_changes(before, after):
    """The packages whose locked version differs between two uv.lock texts, as `name old to new` (added and removed ones included)."""
    old, new = locked_versions(before), locked_versions(after)
    return [f"{n} {old.get(n, '-')} to {new.get(n, '-')}" for n in sorted(old.keys() | new.keys()) if old.get(n) != new.get(n)]


def bump_python_deps(root):
    path = Path(root) / "uv.lock"
    before = path.read_text()
    proc = subprocess.run(["uv", "lock", "--upgrade"], cwd=root, capture_output=True, text=True)
    if proc.returncode != 0:
        raise Fail(f"uv lock --upgrade failed: {ci.text(proc.stderr, 200)}")
    changes = lock_changes(before, path.read_text())
    if not changes:
        return "nothing to bump: every locked version is current"
    shown = ", ".join(changes[:4]) + (f" and {len(changes) - 4} more" if len(changes) > 4 else "")
    return commit_pin(
        "python-deps",
        ["uv.lock"],
        "Bump the locked Python dependencies of the CI scripts",
        "the locked versions",
        f"{len(changes)} updated: {shown}",
        "https://github.com/redcodeworks/octarine-bins/blob/testing/pyproject.toml (the ranges stay, only uv.lock moves)",
        "n/a",
    )


def newest_python(listing, current):
    """The newest default CPython of the same minor as `current` (`3.14.10`) in `uv python list --output-format json`, or None."""
    want = tuple(current.split(".")[:2])
    found = {
        tuple(int(x) for x in e["version"].split("."))
        for e in listing
        if e["implementation"] == "cpython"
        and e["variant"] == "default"
        and tuple(e["version"].split(".")[:2]) == want
        and e["version"].replace(".", "").isdigit()
    }
    return ".".join(map(str, max(found))) if found else None


def bump_python(root):
    path = Path(root) / ".python-version"
    current = path.read_text().strip()
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", current):
        raise Fail(f".python-version is '{current}', not an exact x.y.z version")
    proc = subprocess.run(
        ["uv", "python", "list", "--only-downloads", "--output-format", "json", ".".join(current.split(".")[:2])], cwd=root, capture_output=True, text=True
    )
    if proc.returncode != 0:
        raise Fail(f"uv python list failed: {ci.text(proc.stderr, 200)}")
    new = newest_python(json.loads(proc.stdout), current)
    if new is None:
        raise Fail(f"uv lists no CPython {'.'.join(current.split('.')[:2])}")
    if tuple(map(int, new.split("."))) <= tuple(map(int, current.split("."))):
        return f"nothing to bump: {current} is the newest patch release this uv installs"
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
    """Check one pin; returns the status line, raises Fail. The working tree is restored on failure."""
    if git("diff", "--quiet").returncode != 0:
        raise Fail("the working tree is not clean")
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
    ap = ci.arg_parser(__doc__)
    ap.add_argument("pin", choices=["scx", "extest", "ghostty", "builder", "python-deps", "python"], help="the pin to check")
    pin = ap.parse_args(argv).pin
    try:
        status = bump(pin)
    except Fail as e:
        ci.log.error(f"{pin}: {e}")
        git("checkout", "-q", "--", ".")
        say(f"`{pin}`: FAILED, {e}")
        return 1
    except BaseException:  # any other error (an API error, a bad pin file) crashes with its own traceback, after the working tree is restored
        git("checkout", "-q", "--", ".")
        raise
    say(f"`{pin}`: {status}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
