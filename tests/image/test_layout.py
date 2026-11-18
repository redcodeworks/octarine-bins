"""The image layout, writer into reader.

The REAL assembler (src/lib/assemble.sh, the `assemble` stage of src/Containerfile)
writes COMPONENTS and the combined LICENSES/SOURCE/BUILDINFO around fake component
directories, and the layout checker reads the listing of the result with the
component list of the real src/components.tsv and the real scheduler manifest. The
layout is what octarine-os's installer reads.
"""

import shutil
import subprocess

import pytest

import binaries_layout as layout
from conftest import ROOT

SCHEDULERS = layout.table_names(ROOT / "src/scx/schedulers.tsv")
COMPONENTS = layout.table_names(ROOT / "src/components.tsv")


def assemble(img, components_tsv):
    """Run the real src/lib/assemble.sh on the directory `img`; returns the process."""
    return subprocess.run(
        ["bash", str(ROOT / "src/lib/assemble.sh"), str(img), str(components_tsv)],
        capture_output=True,
        text=True,
    )


def listing(img):
    """The sorted relative names of everything under `img`, like a tar listing."""
    # every file and directory below the image root
    return sorted(str(p.relative_to(img)) for p in img.rglob("*"))


@pytest.fixture
def img(tmp_path):
    """A component tree as the build stages leave it, assembled by the real script.

    The script is src/lib/assemble.sh; the files are placeholders with the right names.
    """
    d = tmp_path / "img"

    # the directories the build stages create
    for sub in (
        "scx/bin",
        "scx/LICENSES",
        "extest/lib",
        "extest/lib64",
        "extest/LICENSES",
        "ghostty/root/usr/bin",
        "ghostty/root/usr/share/applications",
        "ghostty/LICENSES",
    ):
        (d / sub).mkdir(parents=True)

    # one binary per scheduler of the real manifest
    for n in SCHEDULERS:
        (d / "scx/bin" / n).write_text("#!/bin/sh\n")
    shutil.copy(ROOT / "src/scx/schedulers.tsv", d / "scx/schedulers.tsv")
    (d / "extest/lib/libextest.so").write_text("32")
    (d / "extest/lib64/libextest.so").write_text("64")
    (d / "ghostty/root/usr/bin/ghostty").write_text("")
    (
        d / "ghostty/root/usr/share/applications/com.mitchellh.ghostty.desktop"
    ).write_text("")
    (d / "ghostty/FILES").write_text(
        "usr/bin/ghostty\nusr/share/applications/com.mitchellh.ghostty.desktop\n"
    )

    # the files every component carries
    for c in COMPONENTS:
        (d / c / "LICENSES" / f"{c}-LICENSE").write_text(c)
        (d / c / "SOURCE").write_text("src")
        (d / c / "BUILDINFO").write_text("build")

    # the real assembler writes COMPONENTS and the combined top-level files
    assert assemble(d, ROOT / "src/components.tsv").returncode == 0
    return d


def problems(img, components=COMPONENTS):
    """The layout problems of the assembled image `img` (an empty list when fine)."""
    return layout.check(listing(img), SCHEDULERS, components)


def test_assembled_image_passes_and_components_are_one_per_line(img):
    """The real assembler's output passes the real checker.

    COMPONENTS names the components one per line.
    """
    assert problems(img) == []
    assert (img / "COMPONENTS").read_text().split() == COMPONENTS


def remove_scheduler(img):
    """Damage the image: delete the first scheduler's binary."""
    (img / "scx/bin" / SCHEDULERS[0]).unlink()


def remove_components_file(img):
    """Damage the image: delete the COMPONENTS file."""
    (img / "COMPONENTS").unlink()


def add_stray_directory(img):
    """Damage the image: add a top-level directory nobody asked for."""
    (img / "stray").mkdir()
    (img / "stray/file").write_text("x")


@pytest.mark.parametrize(
    "mutate,expected",
    [
        # named after the real manifest
        (remove_scheduler, f"the binaries image lacks scx/bin/{SCHEDULERS[0]}"),
        (remove_components_file, "the binaries image lacks COMPONENTS"),
        (
            add_stray_directory,
            "the binaries image holds unexpected top-level entries: stray",
        ),
    ],
)
def test_a_damaged_image_fails_and_names_the_problem(img, mutate, expected):
    """Each damage gives exactly one problem message, and it names what is wrong."""
    mutate(img)
    assert problems(img) == [expected]
