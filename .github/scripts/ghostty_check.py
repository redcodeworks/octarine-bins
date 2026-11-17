#!/usr/bin/env python3
"""The weekly decision for the Ghostty pin and its Zig toolchain. Usage: ghostty_check.py   (env REPO_ROOT, ZIG_INDEX_FILE: a local copy of index.json, for the tests)

Prints ONE line, the verb first (bump_run.py calls check() and reads the same answer as a Decision):
  current <what>                                              nothing to do
  bump <tag> <commit> <zig-version> <url> <sha256> <size>     a newer STABLE Ghostty tag (v<major>.<minor>.<patch> only: not tip, not vt-*, not a pre-release) with its peeled
                                                              commit, and the Zig release that goes with it
  wait <tag> <text>                                           a newer Ghostty tag exists but ziglang.org has no published x86_64-linux tarball of the Zig minor it needs
                                                              yet: nothing is opened; the next weekly run tries again

The Zig MINOR follows from the Ghostty tag, never independently: for a new tag the minimum is read from `.minimum_zig_version` in that tag's build.zig.zon (gh api), the newest
published patch of that minor comes from ziglang.org/download/index.json (x86_64-linux tarball, shasum, size). Ghostty's build accepts the same major.minor with any patch >= the
minimum, so the newest published patch of that minor goes in with the tag. A Zig release alone never causes a bump. Reads upstream.env and zig.env of src/ghostty. Only reads. Anything that cannot be read (a GitHub API error, the Zig index, a pin that is not a release version) ends the script with its own error; nothing is guessed.
"""
import base64
import json
import os
import re
import sys
from pathlib import Path

import httpx
from pydantic import BaseModel, ConfigDict, Field, RootModel

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ci  # noqa: E402
import pins  # noqa: E402
from pins import Decision  # noqa: E402

ZIG_INDEX_URL = "https://ziglang.org/download/index.json"


class ZigTarball(BaseModel):
    tarball: str = ""
    shasum: str = ""
    size: str | int = ""


class ZigRelease(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)
    x86_64_linux: ZigTarball | None = Field(None, alias="x86_64-linux")


class ZigIndex(RootModel):
    """ziglang.org/download/index.json (untrusted): release name -> its x86_64 Linux tarball, when there is one."""
    root: dict[str, ZigRelease]


@ci.retry(on=(httpx.TransportError, ci.ServerError))
def download_index():
    """The bytes of ziglang.org's index.json: transport errors and 5xx answers are retried, any other error status (and bad JSON, later) is final."""
    response = httpx.get(ZIG_INDEX_URL, timeout=30, follow_redirects=True)
    if response.status_code >= 500:
        raise ci.ServerError(f"{ZIG_INDEX_URL}: HTTP {response.status_code}")
    response.raise_for_status()
    return response.content


def load_index():
    """index.json of ziglang.org as a ZigIndex (from ZIG_INDEX_FILE when set, else downloaded); bad JSON or an unexpected shape raises."""
    local = os.environ.get("ZIG_INDEX_FILE")
    data = Path(local).read_bytes() if local else download_index()
    return ZigIndex.model_validate(json.loads(data))


def check(root):
    """The decision for the Ghostty pin."""
    directory = Path(root) / "src" / "ghostty"
    pin = pins.load_upstream(directory / "upstream.env")
    pin_tag = pin.tag
    pin_zig = pins.read_env(directory / "zig.env").get("ZIG_VERSION", "")
    if not (re.fullmatch(r"0\.[0-9]+\.[0-9]+", pin_zig) and re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+", pin_tag)):
        raise ValueError(f"the pins in src/ghostty are not release versions (tag '{pin_tag}', zig '{pin_zig}')")
    owner, name = pin.watch.split("/")
    gh = ci.github()  # keep the client: gh.rest only holds a weak reference to it
    rest = gh.rest

    stable = [t.name for t in rest.paginate(rest.repos.list_tags, owner=owner, repo=name) if re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+", t.name)]
    if not stable:
        raise ValueError(f"{pin.watch} has no stable v<major>.<minor>.<patch> tag")
    latest = max(stable, key=ci.version)
    index = load_index()

    def tarball(release):
        entry = index.root.get(release)
        return (entry.x86_64_linux if entry else None) or ZigTarball()

    if latest != pin_tag and ci.version(latest) > ci.version(pin_tag):
        commit = rest.repos.get_commit(owner, name, latest).parsed_data.sha
        zon = rest.repos.get_content(owner, name, "build.zig.zon", ref=latest).parsed_data
        m = re.search(r'\.minimum_zig_version *= *"([0-9][0-9.]*)"', base64.b64decode(zon.content).decode(errors="replace"))
        if not m:
            raise ValueError(f"cannot read minimum_zig_version of {pin.watch} {latest}")
        minimum = m.group(1)
        minor = minimum.rsplit(".", 1)[0]
        patches = [v for v in index.root if re.fullmatch(re.escape(minor) + r"\.[0-9]+", v) and tarball(v).tarball]
        if not patches:
            return Decision("wait", tag=latest, text=f"{latest} needs Zig {minor} (minimum {minimum}) and ziglang.org/download/index.json lists no x86_64-linux tarball of {minor}.x yet")
        zv = max(patches, key=ci.version)
        if ci.version(minimum) > ci.version(zv):
            return Decision("wait", tag=latest, text=f"{latest} needs Zig {minimum} or newer and the newest published {minor} is {zv}")
        entry = tarball(zv)
        if not (entry.tarball == f"https://ziglang.org/download/{zv}/zig-x86_64-linux-{zv}.tar.xz" and re.fullmatch(r"[0-9a-f]{64}", entry.shasum) and re.fullmatch(r"[0-9]+", str(entry.size))):
            raise ValueError(f"index.json has no official x86_64-linux tarball entry for {zv}")
        return Decision("bump", tag=latest, commit=commit, zig=zv, zig_url=entry.tarball, zig_sha256=entry.shasum, zig_size=str(entry.size))

    # the pinned tag is current (or ahead of the newest stable tag): nothing to do; Zig changes only together with a Ghostty tag
    return Decision("current", text=f"{pin_tag} is the newest stable Ghostty tag (Zig {pin_zig})")


def main(argv=None):
    ci.arg_parser(__doc__).parse_args(argv)
    ci.log.info(check(os.environ.get("REPO_ROOT") or Path(__file__).resolve().parents[2]).line())
    return 0


if __name__ == "__main__":
    sys.exit(main())
