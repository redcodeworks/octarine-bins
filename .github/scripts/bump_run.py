#!/usr/bin/env python3
"""The weekly bump check of ONE pin. Usage: bump_run.py scx|extest|ghostty|builder   (from the repository root, on a clean checkout; GH_TOKEN, a git identity, skopeo for the builder)

Decides (upstream_check.check / ghostty_check.check, or the registry for the builder) and, when something is newer, writes the pin into the working tree and makes ONE commit for it;
the commit message ends with the row of the pull request's table: pin | from | to | what changed upstream | days the new value has existed. bump_weekly.py runs every pin on its
own checkout of testing, one commit and one pull request per pin. Exit 1 when the pin's check FAILED (a registry that cannot be reached, a bad pin): the working tree is restored
then. Prints one line per pin (also into the job summary).

Kinds: the source pins (scx, extest; ghostty, which also writes the Zig pin: Zig never moves alone) and the Fedora builder digest of src/builder.env (the same major, the newest digest
of the tag once it has been the tag's value for MIN_AGE_DAYS, default 7: the creation date of the image is the stand-in for "has not been replaced since", a registry has no tag history).
"""
import hashlib
import json
import os
import re
import subprocess
import sys
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
    print(line)
    ci.append_to("GITHUB_STEP_SUMMARY", f"- {line}")


def days_since(iso):
    """Whole days since an ISO date, or None when it cannot be read."""
    try:
        when = datetime.fromisoformat(re.sub(r"(\.\d{6})\d+", r"\1", iso.replace("Z", "+00:00")))
    except ValueError:
        return None
    return int((datetime.now(timezone.utc) - when).total_seconds() // 86400) if when.tzinfo else None


def commit_age(repo, sha):
    info = ci.gh_api(f"repos/{repo}/commits/{sha}") or {}
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
    current = cur["UPSTREAM_TAG"] or cur["UPSTREAM_COMMIT"][:7]
    decision = upstream_check.check(root, f"src/{pin}")
    print(f"{pin}: {' '.join(decision)}")
    verb = decision[0]
    if verb == "bump":
        tag, commit = decision[1], decision[2]
        old = cur["UPSTREAM_TAG"] or cur["UPSTREAM_COMMIT"]
        try:
            pins.set_upstream(Path(root) / path, tag, commit)
        except ValueError as e:
            raise Fail(f"cannot write {path} ({e})")
        return commit_pin(pin, [path], f"Bump {pin} to {tag}", current, tag, f"https://github.com/{cur['UPSTREAM_WATCH']}/compare/{old}...{tag}",
                          commit_age(cur["UPSTREAM_WATCH"], commit))
    if verb == "forkbehind":
        return f"the fork is behind upstream ({decision[1]}): run gh repo sync on it first"
    if verb == "unreachable":
        raise Fail(" ".join(decision[1:]))
    return f"nothing to bump: {' '.join(decision)}"


def bump_ghostty(root):
    files = ["src/ghostty/upstream.env", "src/ghostty/zig.env"]
    try:
        cur = pins.load_upstream(Path(root) / files[0])
    except ValueError:
        raise Fail("invalid upstream.env")
    current = f"{cur['UPSTREAM_TAG']}, zig {pins.read_env(Path(root) / files[1]).get('ZIG_VERSION', '')}"
    decision = ghostty_check.check(root)
    print(f"ghostty: {' '.join(decision)}")
    verb = decision[0]
    if verb == "bump":  # a new Ghostty tag brings the Zig minor it needs: one commit for both, they cannot be applied apart
        tag, commit, zig = decision[1], decision[2], decision[3]
        try:
            pins.write_ghostty(Path(root) / "src/ghostty", *decision[1:])
        except ValueError as e:
            raise Fail(f"writing the Ghostty and Zig pins failed ({e})")
        return commit_pin("ghostty", files, f"Bump ghostty to {tag} (Zig {zig})", current, f"{tag}, zig {zig}",
                          f"https://github.com/{cur['UPSTREAM_WATCH']}/compare/{cur['UPSTREAM_TAG']}...{tag}", commit_age(cur["UPSTREAM_WATCH"], commit))
    if verb == "wait":
        return f"nothing to bump yet: {' '.join(decision[1:])}"
    if verb == "unreachable":
        raise Fail(" ".join(decision[1:]))
    return f"nothing to bump: {' '.join(decision)}"


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
    raise Fail(f"unknown pin '{pin}'")


def main(argv):
    if len(argv) != 1:
        print("usage: bump_run.py scx|extest|ghostty|builder", file=sys.stderr)
        return 2
    pin = argv[0]
    try:
        status = bump(pin)
    except Fail as e:
        ci.error(f"{pin}: {e}")
        git("checkout", "-q", "--", ".")
        say(f"`{pin}`: FAILED, {e}")
        return 1
    say(f"`{pin}`: {status}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
