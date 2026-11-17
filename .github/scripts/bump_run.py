#!/usr/bin/env python3
"""The weekly bump check of ONE pin. Usage: bump_run.py scx|extest|ghostty|builder|python-deps   (from the repository root, on a clean checkout; GH_TOKEN, a git identity, skopeo for the builder)

Decides (upstream_check.check / ghostty_check.check, or the registry for the builder) and, when something is newer, writes the pin into the working tree and makes ONE commit for it;
the commit message ends with the row of the pull request's table: pin | from | to | what changed upstream | days the new value has existed. bump_weekly.py runs every pin on its
own checkout of testing, one commit and one pull request per pin. Exit 1 when the pin's check FAILED (a registry that cannot be reached, a bad pin): the working tree is restored
then. Prints one line per pin (also into the job summary).

Kinds: `python-deps` (`uv lock --upgrade`: newer locked versions of the CI scripts' own dependencies, uv.lock only), the source pins (scx, extest; ghostty, which also writes the Zig pin: Zig never moves alone) and the Fedora builder digest of src/builder.env (the same major, the newest digest
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


def days_since(iso):
    """Whole days since an ISO date, or None when it cannot be read."""
    try:
        when = datetime.fromisoformat(re.sub(r"(\.\d{6})\d+", r"\1", iso.replace("Z", "+00:00")))
    except ValueError:
        return None
    return int((datetime.now(timezone.utc) - when).total_seconds() // 86400) if when.tzinfo else None


def commit_age(repo, sha):
    info = ci.gh_api(f"/repos/{repo}/commits/{sha}") or {}
    days = days_since(((info.get("commit") or {}).get("committer") or {}).get("date", ""))
    return "unknown" if days is None else days


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
    verb = decision.verb
    if verb == "bump":
        tag, commit = decision.tag, decision.commit
        old = cur.tag or cur.commit
        try:
            pins.set_upstream(Path(root) / path, tag, commit)
        except ValueError as e:
            raise Fail(f"cannot write {path} ({e})")
        return commit_pin(pin, [path], f"Bump {pin} to {tag}", current, tag, f"https://github.com/{cur.watch}/compare/{old}...{tag}", commit_age(cur.watch, commit))
    if verb == "forkbehind":
        return f"the fork is behind upstream ({decision.tag}): run gh repo sync on it first"
    if verb == "unreachable":
        raise Fail(decision.text)
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
    verb = decision.verb
    if verb == "bump":  # a new Ghostty tag brings the Zig minor it needs: one commit for both, they cannot be applied apart
        tag, commit, zig = decision.tag, decision.commit, decision.zig
        try:
            pins.write_ghostty(Path(root) / "src/ghostty", tag, commit, zig, decision.zig_url, decision.zig_sha256, decision.zig_size)
        except ValueError as e:
            raise Fail(f"writing the Ghostty and Zig pins failed ({e})")
        return commit_pin("ghostty", files, f"Bump ghostty to {tag} (Zig {zig})", current, f"{tag}, zig {zig}",
                          f"https://github.com/{cur.watch}/compare/{cur.tag}...{tag}", commit_age(cur.watch, commit))
    if verb == "wait":
        return f"nothing to bump yet: {decision.tag} {decision.text}"
    if verb == "unreachable":
        raise Fail(decision.text)
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
    except (registry.Absent, registry.ReadError):
        raise Fail(f"cannot resolve {repo}:{major}")
    new = "sha256:" + hashlib.sha256(manifest).hexdigest()
    if new == cur_digest:
        return f"nothing to bump: the pin is the current digest of {major}"
    try:
        created = json.loads(registry.read("config", f"{repo}:{major}", linux_amd64=True)).get("created") or ""
    except (registry.Absent, registry.ReadError, ValueError):
        created = ""
    if not created:
        raise Fail(f"cannot read the creation date of {repo}:{major}")
    age = days_since(created) or 0
    minimum = int(os.environ.get("MIN_AGE_DAYS", "7"))
    if age < minimum:
        return (f"nothing to bump yet: {new[:19]} was built {age} days ago ({created}); a digest is proposed once it has been the tag's value for {minimum} days")
    pins.set_lines(Path(root) / path, {"BUILDER": f"{repo}:{major}@{new}"})
    return commit_pin("builder", [path], f"Bump the Fedora {major} builder digest (built {created.split('T')[0]})", current, f"{major}@{new[:19]}", f"{repo}:{major}", age)


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
    return commit_pin("python-deps", ["uv.lock"], "Bump the locked Python dependencies of the CI scripts", "the locked versions", f"{len(changes)} updated: {shown}",
                      "https://github.com/redcodeworks/octarine-bins/blob/testing/pyproject.toml (the ranges stay, only uv.lock moves)", "n/a")


def bump(pin, root="."):
    """Check one pin; returns the status line, raises Fail. The working tree is restored on failure."""
    if git("diff", "--quiet").returncode != 0:
        raise Fail("the working tree is not clean")
    if pin in ("scx", "extest"):
        return bump_source(pin, root)
    if pin == "ghostty":
        return bump_ghostty(root)
    if pin == "builder":
        return bump_builder(root)
    if pin == "python-deps":
        return bump_python_deps(root)
    raise Fail(f"unknown pin '{pin}'")


def main(argv=None):
    ap = ci.arg_parser(__doc__)
    ap.add_argument("pin", choices=["scx", "extest", "ghostty", "builder", "python-deps"], help="the pin to check")
    pin = ap.parse_args(argv).pin
    try:
        status = bump(pin)
    except Fail as e:
        ci.log.error(f"{pin}: {e}")
        git("checkout", "-q", "--", ".")
        say(f"`{pin}`: FAILED, {e}")
        return 1
    say(f"`{pin}`: {status}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
