"""Read and write the pin files of the build.

The files are src/<component>/upstream.env, src/ghostty/zig.env and src/builder.env.
Pydantic validates what is read.

upstream.env is the Python twin of src/lib/upstream-env.sh (the build scripts inside
the container read it with bash; the CI scripts here read it with this module).
Format: KEY=VALUE lines, `#` comments and blank lines allowed, nothing else (the file
is parsed, never sourced).
  UPSTREAM_REPO    owner/name on github.com: the repository the build clones (our
                   fork, for extest)
  UPSTREAM_WATCH   owner/name the weekly check asks for new releases; default
                   UPSTREAM_REPO. When it differs the build source is a fork: the pin
                   is the commit and UPSTREAM_TAG is a label
  UPSTREAM_TAG     the release tag; empty for a project pinned by commit only
  UPSTREAM_COMMIT  the 40-hex commit the pin must resolve to (for an annotated tag:
                   the peeled commit)
"""

import re
from pathlib import Path
from typing import Literal

from pydantic import Field, ValidationError
from pydantic.dataclasses import dataclass

REPO = re.compile(r"[A-Za-z0-9._-]+/[A-Za-z0-9._-]+")
KEYS = ("UPSTREAM_REPO", "UPSTREAM_WATCH", "UPSTREAM_TAG", "UPSTREAM_COMMIT")


@dataclass(frozen=True)
class Pin:
    """The pin of one component (upstream.env), validated when it is read."""

    repo: str = Field(pattern=r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$")
    watch: str = Field(pattern=r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$")
    tag: str = Field(pattern=r"^[A-Za-z0-9._+-]*$")
    commit: str = Field(pattern=r"^[0-9a-f]{40}$")


@dataclass(frozen=True)
class Decision:
    """The answer of a weekly check (upstream_check, ghostty_check).

    The verb is current, bump, wait, report or forkbehind. A bump carries the new tag
    and commit, and for Ghostty the Zig release that goes with it. Validated when
    built: the commit is empty or 40 hex characters, the Zig sha256 empty or 64.
    """

    verb: Literal["current", "bump", "wait", "report", "forkbehind"]
    text: str = ""
    tag: str = ""
    commit: str = Field("", pattern=r"^([0-9a-f]{40})?$")
    zig: str = ""
    zig_url: str = ""
    zig_sha256: str = Field("", pattern=r"^([0-9a-f]{64})?$")
    zig_size: str = ""

    def line(self):
        """The one-line form that the scripts print and the job summary shows.

        The verb first, then every field that is set, separated by spaces.
        """
        # the non-empty fields, in the order the scripts document
        return " ".join(
            w
            for w in (
                self.verb,
                self.tag,
                self.commit,
                self.zig,
                self.zig_url,
                self.zig_sha256,
                self.zig_size,
                self.text,
            )
            if w
        )


def load_upstream(path):
    """The pin of one component, read from its upstream.env, as a Pin.

    `path` is the file. Raises ValueError, with the reason, when the file is missing,
    a line is not KEY=VALUE, a key is unknown or repeated, or a value fails the Pin
    validation. The first problem in file order is the one reported. The file is
    parsed, never sourced.
    """
    path = Path(path)

    # a missing file is a pin error, not an empty pin
    if not path.is_file():
        raise ValueError(f"not found: {path}")
    values = dict.fromkeys(KEYS, "")
    seen = set()

    # one line at a time, so the first bad line is the one reported
    for line in path.read_text().splitlines():
        line = line.split("#", 1)[0].strip()

        # comments and blank lines carry no pin
        if not line:
            continue
        m = re.fullmatch(r"([A-Z_]+)=(.*)", line)

        # nothing but KEY=VALUE lines is allowed
        if not m:
            raise ValueError(f"not KEY=VALUE: '{line}'")
        key, value = m.groups()

        # a typo in a key would silently leave the real one empty
        if key not in KEYS:
            raise ValueError(f"unknown key {key}")

        # two lines for one key leave it unclear which one counts
        if key in seen:
            raise ValueError(f"{key} appears twice")
        seen.add(key)
        values[key] = value.strip("\"'")

    # the validation of the values (repo shape, 40-hex commit) is pydantic's
    try:
        return Pin(
            repo=values["UPSTREAM_REPO"],
            watch=values["UPSTREAM_WATCH"] or values["UPSTREAM_REPO"],
            tag=values["UPSTREAM_TAG"],
            commit=values["UPSTREAM_COMMIT"],
        )
    except ValidationError as e:
        raise ValueError(
            f"{path}: {e.errors()[0]['loc'][0]} is invalid ({e.errors()[0]['msg']})"
        ) from e


def set_lines(path, values):
    """Rewrite `KEY=...` lines of a file in place; comments and other lines stay.

    `values` maps each key to its new value. Raises ValueError when a key has no line
    in the file (then nothing is written, the file is only written at the end).
    """
    path = Path(path)
    content = path.read_text()

    # one substitution per key, each of which must hit a line
    for key, value in values.items():
        content, n = re.subn(
            rf"^{key}=.*$", lambda _m, v=value: f"{key}={v}", content, flags=re.M
        )

        # a key without a line cannot be set: the file and the writer disagree
        if n == 0:
            raise ValueError(f"{path} has no {key} line")
    path.write_text(content)


def set_upstream(path, tag, commit):
    """A bump: rewrite the tag and commit lines of an upstream.env and read it back.

    Raises ValueError for a malformed tag or commit, a file without the two lines, or
    a file that does not hold the new pin after writing it.
    """
    # the writer accepts only a plain tag and a full 40-hex commit
    if not (
        re.fullmatch(r"[A-Za-z0-9._+-]*", tag) and re.fullmatch(r"[0-9a-f]{40}", commit)
    ):
        raise ValueError("bad tag or commit")
    set_lines(path, {"UPSTREAM_TAG": tag, "UPSTREAM_COMMIT": commit})
    pin = load_upstream(path)

    # the read-back proves the file really holds what was meant
    if pin.tag != tag or pin.commit != commit:
        raise ValueError(f"{path} does not hold the new pin after writing it")


def read_env(path):
    """A plain KEY=VALUE file (zig.env, builder.env) as a dict.

    Lines that are not KEY=VALUE (comments, blank lines) are skipped; values are kept
    as written, quotes included. A missing file raises FileNotFoundError.
    """
    # the (key, value) pair of every matching line
    return dict(
        m.groups()
        for line in Path(path).read_text().splitlines()
        if (m := re.fullmatch(r"([A-Z_0-9]+)=(.*)", line))
    )


def write_ghostty(directory, tag, commit, zig_version, zig_url, zig_sha256, zig_size):
    """Write a new stable Ghostty tag and its Zig release into the pin files.

    A new tag changes upstream.env (tag AND peeled commit) and zig.env (version, URL,
    sha256, size, signature URL) together, because the Zig minor follows from the
    Ghostty tag. `directory` is src/ghostty. The Zig values must be an official
    ziglang.org x86_64-linux release entry (what fetch-zig.sh requires), else
    ValueError and nothing is touched.
    """
    directory = Path(directory)
    official = f"https://ziglang.org/download/{zig_version}/zig-x86_64-linux-{zig_version}.tar.xz"

    # validate everything before the first write, so a bad entry touches nothing
    if not (
        re.fullmatch(r"0\.[0-9]+\.[0-9]+", zig_version)
        and re.fullmatch(r"[0-9a-f]{64}", zig_sha256)
        and re.fullmatch(r"[0-9]+", str(zig_size))
        and zig_url == official
    ):
        raise ValueError(
            "the Zig arguments are not an official x86_64-linux release entry"
        )
    set_upstream(directory / "upstream.env", tag, commit)

    # when the size is not pinned today there is no line to write: the size must equal
    # the one index.json lists
    size_pinned = "ZIG_SIZE" in read_env(directory / "zig.env")
    values = {
        "ZIG_VERSION": zig_version,
        "ZIG_URL": zig_url,
        "ZIG_SHA256": zig_sha256,
        **({"ZIG_SIZE": str(zig_size)} if size_pinned else {}),
        "ZIG_SIGNATURE_URL": f"{zig_url}.minisig",
    }
    set_lines(directory / "zig.env", values)
