"""Read and write the pin files: src/<component>/upstream.env, src/ghostty/zig.env and src/builder.env. Standard library only.

upstream.env is the Python twin of src/lib/upstream-env.sh (the build scripts inside the container read it with bash; the CI scripts here read it with this module).
Format: KEY=VALUE lines, `#` comments and blank lines allowed, nothing else (the file is parsed, never sourced).
  UPSTREAM_REPO    owner/name on github.com: the repository the build clones (our fork, for extest)
  UPSTREAM_WATCH   owner/name the weekly check asks for new releases; default UPSTREAM_REPO. When it differs the build source is a fork: the pin is the commit and UPSTREAM_TAG is a label
  UPSTREAM_TAG     the release tag; empty for a project pinned by commit only
  UPSTREAM_COMMIT  the 40-hex commit the pin must resolve to (for an annotated tag: the peeled commit)
"""
import re
from pathlib import Path

REPO = re.compile(r"[A-Za-z0-9._-]+/[A-Za-z0-9._-]+")
KEYS = ("UPSTREAM_REPO", "UPSTREAM_WATCH", "UPSTREAM_TAG", "UPSTREAM_COMMIT")


def load_upstream(path):
    """The pin of one component as a dict with the four keys; ValueError (with the reason) when the file is invalid."""
    path = Path(path)
    if not path.is_file():
        raise ValueError(f"not found: {path}")
    pin = dict.fromkeys(KEYS, "")
    seen = set()
    for line in path.read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        m = re.fullmatch(r"([A-Z_]+)=(.*)", line)
        if not m:
            raise ValueError(f"not KEY=VALUE: '{line}'")
        key, value = m.groups()
        if key not in KEYS:
            raise ValueError(f"unknown key {key}")
        if key in seen:
            raise ValueError(f"{key} appears twice")
        seen.add(key)
        pin[key] = value[1:] if value[:1] in "\"'" else value
        pin[key] = pin[key][:-1] if pin[key][-1:] in "\"'" else pin[key]
    if not REPO.fullmatch(pin["UPSTREAM_REPO"]):
        raise ValueError(f"UPSTREAM_REPO must be owner/name (got '{pin['UPSTREAM_REPO']}')")
    pin["UPSTREAM_WATCH"] = pin["UPSTREAM_WATCH"] or pin["UPSTREAM_REPO"]
    if not REPO.fullmatch(pin["UPSTREAM_WATCH"]):
        raise ValueError(f"UPSTREAM_WATCH must be owner/name (got '{pin['UPSTREAM_WATCH']}')")
    if pin["UPSTREAM_TAG"] and not re.fullmatch(r"[A-Za-z0-9._+-]+", pin["UPSTREAM_TAG"]):
        raise ValueError(f"bad UPSTREAM_TAG '{pin['UPSTREAM_TAG']}'")
    if not re.fullmatch(r"[0-9a-f]{40}", pin["UPSTREAM_COMMIT"]):
        raise ValueError(f"UPSTREAM_COMMIT must be 40 lowercase hex characters (got '{pin['UPSTREAM_COMMIT']}')")
    return pin


def set_lines(path, values):
    """Rewrite `KEY=...` lines of a file in place (comments and other lines stay); ValueError when a key has no line."""
    path = Path(path)
    content = path.read_text()
    for key, value in values.items():
        content, n = re.subn(rf"^{key}=.*$", lambda _m, v=value: f"{key}={v}", content, flags=re.M)
        if n == 0:
            raise ValueError(f"{path} has no {key} line")
    path.write_text(content)


def set_upstream(path, tag, commit):
    """A bump: rewrite the two pin lines and read the file back."""
    if not (re.fullmatch(r"[A-Za-z0-9._+-]*", tag) and re.fullmatch(r"[0-9a-f]{40}", commit)):
        raise ValueError("bad tag or commit")
    set_lines(path, {"UPSTREAM_TAG": tag, "UPSTREAM_COMMIT": commit})
    pin = load_upstream(path)
    if pin["UPSTREAM_TAG"] != tag or pin["UPSTREAM_COMMIT"] != commit:
        raise ValueError(f"{path} does not hold the new pin after writing it")


def read_env(path):
    """A plain KEY=VALUE file (zig.env, builder.env) as a dict; comment lines skipped."""
    out = {}
    for line in Path(path).read_text().splitlines():
        m = re.fullmatch(r"([A-Z_0-9]+)=(.*)", line)
        if m:
            out[m.group(1)] = m.group(2)
    return out


def write_ghostty(directory, tag, commit, zig_version, zig_url, zig_sha256, zig_size):
    """A new stable Ghostty tag changes upstream.env (tag AND peeled commit) and zig.env (version, URL, sha256, size, signature URL) together, because the Zig minor
    follows from the Ghostty tag. The Zig values must be an official ziglang.org x86_64-linux release entry (what fetch-zig.sh requires), else ValueError and nothing is touched."""
    directory = Path(directory)
    official = f"https://ziglang.org/download/{zig_version}/zig-x86_64-linux-{zig_version}.tar.xz"
    if not (re.fullmatch(r"0\.[0-9]+\.[0-9]+", zig_version) and re.fullmatch(r"[0-9a-f]{64}", zig_sha256) and re.fullmatch(r"[0-9]+", str(zig_size)) and zig_url == official):
        raise ValueError("the Zig arguments are not an official x86_64-linux release entry")
    set_upstream(directory / "upstream.env", tag, commit)
    values = {"ZIG_VERSION": zig_version, "ZIG_URL": zig_url, "ZIG_SHA256": zig_sha256, "ZIG_SIZE": str(zig_size), "ZIG_SIGNATURE_URL": f"{zig_url}.minisig"}
    if "ZIG_SIZE" not in read_env(directory / "zig.env"):  # not pinned today: the size must equal the one index.json lists, so there is no line to write
        del values["ZIG_SIZE"]
    set_lines(directory / "zig.env", values)
