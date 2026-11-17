#!/usr/bin/env python3
"""The weekly decision for the Ghostty pin and its Zig toolchain. Usage: ghostty_check.py   (env REPO_ROOT, ZIG_INDEX_FILE: a local copy of index.json, for the tests)

Prints ONE line, the verb first (bump_run.py calls check() and reads the same answer as a tuple):
  current <what>                                              nothing to do
  bump <tag> <commit> <zig-version> <url> <sha256> <size>     a newer STABLE Ghostty tag (v<major>.<minor>.<patch> only: not tip, not vt-*, not a pre-release) with its peeled
                                                              commit, and the Zig release that goes with it
  wait <tag> <text>                                           a newer Ghostty tag exists but ziglang.org has no published x86_64-linux tarball of the Zig minor it needs
                                                              yet: nothing is opened; the next weekly run tries again
  unreachable <text>                                          something could not be asked or read; fail soft (exit 0)

The Zig MINOR follows from the Ghostty tag, never independently: for a new tag the minimum is read from `.minimum_zig_version` in that tag's build.zig.zon (gh api), the newest
published patch of that minor comes from ziglang.org/download/index.json (x86_64-linux tarball, shasum, size). Ghostty's build accepts the same major.minor with any patch >= the
minimum, so the newest published patch of that minor goes in with the tag. A Zig release alone never causes a bump. Reads upstream.env and zig.env of src/ghostty. Only reads.
"""
import base64
import json
import os
import re
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ci  # noqa: E402
import pins  # noqa: E402

ZIG_INDEX_URL = "https://ziglang.org/download/index.json"


def load_index():
    """index.json of ziglang.org as a dict, or None (from ZIG_INDEX_FILE when set, else downloaded; two retries)."""
    local = os.environ.get("ZIG_INDEX_FILE")
    for attempt in range(1 if local else 3):
        try:
            if local:
                data = Path(local).read_bytes()
            else:
                with urllib.request.urlopen(ZIG_INDEX_URL, timeout=30) as r:
                    data = r.read()
            index = json.loads(data)
            return index if isinstance(index, dict) else None
        except (OSError, ValueError):
            if not local and attempt < 2:
                time.sleep(attempt + 1)
    return None


def check(root):
    """The decision as a tuple (verb, *words)."""
    directory = Path(root) / "src" / "ghostty"
    try:
        pin = pins.load_upstream(directory / "upstream.env")
    except ValueError:
        return ("unreachable", "invalid src/ghostty/upstream.env")
    repo, pin_tag = pin["UPSTREAM_WATCH"], pin["UPSTREAM_TAG"]
    try:
        pin_zig = pins.read_env(directory / "zig.env").get("ZIG_VERSION", "")
    except OSError:
        pin_zig = ""
    if not (re.fullmatch(r"0\.[0-9]+\.[0-9]+", pin_zig) and re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+", pin_tag)):
        return ("unreachable", f"the pins in src/ghostty are not release versions (tag '{pin_tag}', zig '{pin_zig}')")

    tags = ci.gh_api(f"repos/{repo}/tags?per_page=100", paginate=True)
    if tags is None:
        return ("unreachable", f"cannot read the tags of {repo}")
    stable = [t["name"] for t in tags if re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+", t["name"])]
    if not stable:
        return ("unreachable", f"{repo} has no stable v<major>.<minor>.<patch> tag")
    latest = max(stable, key=ci.vkey)

    index = load_index()
    if index is None:
        return ("unreachable", f"cannot read {ZIG_INDEX_URL}")

    def tarball(version):
        return (index.get(version) or {}).get("x86_64-linux") or {}

    if latest != pin_tag and max(pin_tag, latest, key=ci.vkey) == latest:
        commit = (ci.gh_api(f"repos/{repo}/commits/{latest}") or {}).get("sha", "")
        if not re.fullmatch(r"[0-9a-f]{40}", commit):
            return ("unreachable", f"cannot resolve {repo} {latest} to a commit")
        zon = ci.gh_api(f"repos/{repo}/contents/build.zig.zon?ref={latest}") or {}
        try:
            text = base64.b64decode(zon.get("content", "")).decode(errors="replace")
        except ValueError:
            text = ""
        m = re.search(r'\.minimum_zig_version *= *"([0-9][0-9.]*)"', text)
        minimum = m.group(1) if m else ""
        if not re.fullmatch(r"0\.[0-9]+\.[0-9]+", minimum):
            return ("unreachable", f"cannot read minimum_zig_version of {repo} {latest}")
        minor = minimum.rsplit(".", 1)[0]
        patches = [v for v in index if re.fullmatch(re.escape(minor) + r"\.[0-9]+", v) and tarball(v).get("tarball")]
        if not patches:
            return ("wait", latest, f"{latest} needs Zig {minor} (minimum {minimum}) and ziglang.org/download/index.json lists no x86_64-linux tarball of {minor}.x yet")
        zv = max(patches, key=ci.vkey)
        if max(minimum, zv, key=ci.vkey) != zv:
            return ("wait", latest, f"{latest} needs Zig {minimum} or newer and the newest published {minor} is {zv}")
        entry = tarball(zv)
        url, sha, size = entry.get("tarball", ""), entry.get("shasum", ""), str(entry.get("size", ""))
        if not (url == f"https://ziglang.org/download/{zv}/zig-x86_64-linux-{zv}.tar.xz" and re.fullmatch(r"[0-9a-f]{64}", sha) and re.fullmatch(r"[0-9]+", size)):
            return ("unreachable", f"index.json has no official x86_64-linux tarball entry for {zv}")
        return ("bump", latest, commit, zv, url, sha, size)

    # the pinned tag is current (or ahead of the newest stable tag): nothing to do; Zig changes only together with a Ghostty tag
    return ("current", f"{pin_tag} is the newest stable Ghostty tag (Zig {pin_zig})")


def main(argv):
    root = os.environ.get("REPO_ROOT") or Path(__file__).resolve().parents[2]
    print(" ".join(check(root)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
