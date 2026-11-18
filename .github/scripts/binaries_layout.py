#!/usr/bin/env python3
"""Check a built binaries image against the layout octarine-os reads.

Usage: binaries_layout.py <local image> <dir>   (dir: src)
The layout is what octarine-os's build_files/install-binaries.sh reads.

Exports the image with podman (env PODMAN, default `podman`; for example `podman
--root R --runroot X --storage-driver overlay`), lists its files and calls check().
The components come from <dir>/components.tsv, the schedulers from
src/scx/schedulers.tsv; at least one component is required. Per component: scx needs
scx/bin/<name> for every scheduler of the manifest and scx/schedulers.tsv; extest both
libraries; ghostty its binary, desktop file and FILES; every component needs
<c>/SOURCE, <c>/BUILDINFO and <c>/LICENSES/*. The assembled image also needs the
top-level BUILDINFO and COMPONENTS (with components: SOURCE and LICENSES/ too) and
holds no other top-level entry than its components, those files. Nothing else is
judged here (the build already ran the smoke tests and the ELF checks). Exit 0 ok, 1
problems (each on stderr as ::error::).
"""

import os
import shlex
import subprocess
import sys
import tarfile
from itertools import chain
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "00-lib"))
import ci  # noqa: E402


def table_names(path):
    """The first column of a tab separated list file, as a list.

    `path` is the file (components.tsv or schedulers.tsv). Comment lines (starting
    with #) and blank lines are skipped. A missing file raises FileNotFoundError.
    """
    # the name of every real row
    return [
        line.split("\t")[0]
        for line in Path(path).read_text().splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def required_files(component, schedulers):
    """Generate the files one component must have in the image.

    This is the part of the layout that is specific to the component; `schedulers` are
    the scheduler names of the manifest (scx has one binary per scheduler). Raises
    ValueError for an unknown component, or scx without any scheduler.
    """
    # each component has its own files; the common ones follow after the match
    match component:
        case "scx":
            # an empty manifest would make the scx check vacuous
            if not schedulers:
                raise ValueError("no scheduler in the manifest")
            # one binary per scheduler of the manifest
            yield from (f"scx/bin/{name}" for name in schedulers)
            yield "scx/schedulers.tsv"
        case "extest":
            yield from ("extest/lib/libextest.so", "extest/lib64/libextest.so")
        case "ghostty":
            yield from (
                "ghostty/root/usr/bin/ghostty",
                "ghostty/root/usr/share/applications/com.mitchellh.ghostty.desktop",
                "ghostty/FILES",
            )
        case _:
            raise ValueError(f"unknown component '{component}'")
    yield from (f"{component}/SOURCE", f"{component}/BUILDINFO")


def check(listing, schedulers, components):
    """The problems of an assembled image, as a list of messages (empty when fine).

    `listing` is the names in the image, `schedulers` the scheduler names of the
    manifest, `components` the component names the image must hold. Raises ValueError
    for an empty component list, which would check nothing.
    """
    # an image with no components is a wrong call, not a passing image
    if not components:
        raise ValueError("name at least one component")
    # tar names may start with ./, the expectations do not
    files = {name.removeprefix("./") for name in listing}

    def has_files_under(prefix):
        """Does the image hold at least one file below the directory `prefix`?"""
        # a name longer than the prefix itself is something inside the directory
        return any(f.startswith(prefix) and len(f) > len(prefix) for f in files)

    # every file the components and the assembled image require
    needed = [
        *chain.from_iterable(required_files(c, schedulers) for c in components),
        "BUILDINFO",
        "COMPONENTS",
        "SOURCE",
    ]

    allowed = {"SOURCE", "BUILDINFO", "COMPONENTS", "LICENSES", *components}
    # top-level entries nobody asked for
    extra = sorted({f.split("/")[0] for f in files} - allowed - {""})

    # one list: missing files, missing licenses, the top-level licenses, extra entries
    return [
        *(f"the binaries image lacks {name}" for name in needed if name not in files),
        *(
            f"the binaries image has no {c}/LICENSES"
            for c in components
            if not has_files_under(f"{c}/LICENSES/")
        ),
        *(
            []
            if has_files_under("LICENSES/")
            else ["the binaries image has no top-level LICENSES"]
        ),
        *(
            [
                f"the binaries image holds unexpected top-level entries: {' '.join(extra)}"
            ]
            if extra
            else []
        ),
    ]


def export_listing(image):
    """The names of all files in an image, read from `podman export`.

    `image` is the local image. The PODMAN environment variable replaces the podman
    command (for a scratch storage). Raises CalledProcessError when the container
    cannot be created; the container is removed in every case.
    """
    podman = shlex.split(os.environ.get("PODMAN", "podman"))
    cid = subprocess.run(
        [*podman, "create", image, "/nonexistent"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()

    # remove the scratch container however the export ends
    try:
        # stream the export: the image can be large, only the names are needed
        with subprocess.Popen([*podman, "export", cid], stdout=subprocess.PIPE) as proc:
            # read the tar as a stream (r|), it cannot seek
            with tarfile.open(fileobj=proc.stdout, mode="r|") as tar:
                # the name of every member of the archive
                return [m.name for m in tar]
    finally:
        subprocess.run([*podman, "rm", cid], capture_output=True)


def main(argv=None):
    """Check the image named on the command line against the expected layout.

    Returns 0 when the layout is right, 1 when there are problems or the image cannot
    be exported (each problem is logged as an error).
    """
    ap = ci.arg_parser(__doc__)
    ap.add_argument("image", help="the locally built image")
    ap.add_argument(
        "dir",
        choices=["src"],
        help="the image directory (its components are the expectation)",
    )
    args = ap.parse_args(argv)
    directory = args.dir
    components = table_names(f"{directory}/components.tsv")

    # a layout that cannot be read is as bad as a wrong one
    try:
        problems = check(
            export_listing(args.image),
            table_names("src/scx/schedulers.tsv"),
            components,
        )
    except (ValueError, subprocess.CalledProcessError) as e:
        ci.log.error(str(e))
        return 1

    # one error line per problem, so a run shows all of them at once
    for p in problems:
        ci.log.error(p)

    # any problem fails the check
    if problems:
        return 1
    ci.log.info(f"binaries image layout ok: {' '.join(components)}")
    return 0


# run only as a script, not when a test imports the module
if __name__ == "__main__":
    sys.exit(main())
