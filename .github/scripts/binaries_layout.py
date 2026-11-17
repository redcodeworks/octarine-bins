#!/usr/bin/env python3
"""Check a built binaries image against the layout octarine-os reads (its build_files/install-binaries.sh). Usage: binaries_layout.py <local image> <dir>   (dir: src, dx or gx)

Exports the image with podman (env PODMAN, default `podman`; for example `podman --root R --runroot X --storage-driver overlay`), lists its files and calls check().
The components come from <dir>/components.tsv, the schedulers from src/scx/schedulers.tsv, the files of /packages from <dir>/packages plus install.sh. dx and gx may hold no
component (allow_empty), the base never. Per component: scx needs scx/bin/<name> for every scheduler of the manifest and scx/schedulers.tsv; extest both libraries; ghostty its
binary, desktop file and FILES; every component needs <c>/SOURCE, <c>/BUILDINFO and <c>/LICENSES/*. The assembled image also needs the top-level BUILDINFO and COMPONENTS (with
components: SOURCE and LICENSES/ too) and holds no other top-level entry than its components, those files and `packages`. Nothing else is judged here (the build already ran
the smoke tests and the ELF checks). Exit 0 ok, 1 problems (each on stderr as ::error::).
"""
import os
import shlex
import subprocess
import sys
import tarfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ci  # noqa: E402


def table_names(path):
    """First column of a tab separated list; comments and blank lines skipped."""
    return [line.split("\t")[0] for line in Path(path).read_text().splitlines() if line.strip() and not line.lstrip().startswith("#")]


def check(listing, schedulers, components, allow_empty=False, packages=()):
    """Problems of an assembled image: `listing` is the names in it, `schedulers` the scheduler names of the manifest. Returns a list of messages."""
    if not components and not allow_empty:
        raise ValueError("name at least one component")
    files = {name[2:] if name.startswith("./") else name for name in listing}
    problems = []

    def need(name):
        if name not in files:
            problems.append(f"the binaries image lacks {name}")

    for c in components:
        if c == "scx":
            if not schedulers:
                raise ValueError("no scheduler in the manifest")
            for name in schedulers:
                need(f"scx/bin/{name}")
            need("scx/schedulers.tsv")
        elif c == "extest":
            need("extest/lib/libextest.so")
            need("extest/lib64/libextest.so")
        elif c == "ghostty":
            need("ghostty/root/usr/bin/ghostty")
            need("ghostty/root/usr/share/applications/com.mitchellh.ghostty.desktop")
            need("ghostty/FILES")
        else:
            raise ValueError(f"unknown component '{c}'")
        need(f"{c}/SOURCE")
        need(f"{c}/BUILDINFO")
        if not any(f.startswith(f"{c}/LICENSES/") and len(f) > len(f"{c}/LICENSES/") for f in files):
            problems.append(f"the binaries image has no {c}/LICENSES")
    need("BUILDINFO")
    need("COMPONENTS")
    if components:
        need("SOURCE")
        if not any(f.startswith("LICENSES/") and len(f) > len("LICENSES/") for f in files):
            problems.append("the binaries image has no top-level LICENSES")
    allowed = {"SOURCE", "BUILDINFO", "COMPONENTS", "LICENSES", *components} | ({"packages"} if packages else set())
    extra = sorted({f.split("/")[0] for f in files} - allowed - {""})
    if extra:
        problems.append(f"the binaries image holds unexpected top-level entries: {' '.join(extra)}")
    for name in packages:
        need(f"packages/{name}")
    return problems


def export_listing(image):
    """The names in the image, from `podman export`."""
    podman = shlex.split(os.environ.get("PODMAN", "podman"))
    cid = subprocess.run([*podman, "create", image, "/nonexistent"], capture_output=True, text=True, check=True).stdout.strip()
    try:
        with subprocess.Popen([*podman, "export", cid], stdout=subprocess.PIPE) as proc:
            with tarfile.open(fileobj=proc.stdout, mode="r|") as tar:
                return [m.name for m in tar]
    finally:
        subprocess.run([*podman, "rm", cid], capture_output=True)


def main(argv=None):
    ap = ci.arg_parser(__doc__)
    ap.add_argument("image", help="the locally built image")
    ap.add_argument("dir", choices=["src", "dx", "gx"], help="the image directory (its components and package lists are the expectation)")
    args = ap.parse_args(argv)
    directory = args.dir
    components = table_names(f"{directory}/components.tsv")
    packages = sorted(p.name for p in Path(directory, "packages").iterdir()) + ["install.sh"]
    try:
        problems = check(export_listing(args.image), table_names("src/scx/schedulers.tsv"), components, allow_empty=directory != "src", packages=packages)
    except (ValueError, subprocess.CalledProcessError) as e:
        ci.log.error(str(e))
        return 1
    for p in problems:
        ci.log.error(p)
    if problems:
        return 1
    ci.log.info(f"binaries image layout ok: {' '.join(components) or '<no component>'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
