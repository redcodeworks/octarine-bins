"""The image layout, writer into reader: the REAL assembler (src/lib/assemble.sh, the `assemble` stage of src/Containerfile) writes COMPONENTS and the combined
LICENSES/SOURCE/BUILDINFO around fake component directories, and the layout checker reads the listing of the result with the component list of the real
src/components.tsv and the real scheduler manifest. The layout is what octarine-os's installer reads."""
import shutil
import subprocess

import pytest

import binaries_layout as layout
from conftest import ROOT

SCHEDULERS = layout.table_names(ROOT / "src/scx/schedulers.tsv")
COMPONENTS = layout.table_names(ROOT / "src/components.tsv")


def assemble(img, components_tsv, *flags):
    return subprocess.run(["bash", str(ROOT / "src/lib/assemble.sh"), *flags, str(img), str(components_tsv)], capture_output=True, text=True)


def listing(img):
    return sorted(str(p.relative_to(img)) for p in img.rglob("*"))


@pytest.fixture
def img(tmp_path):
    """A component tree as the build stages leave it, assembled by the real assemble.sh."""
    d = tmp_path / "img"
    for sub in ("scx/bin", "scx/LICENSES", "extest/lib", "extest/lib64", "extest/LICENSES", "ghostty/root/usr/bin", "ghostty/root/usr/share/applications", "ghostty/LICENSES"):
        (d / sub).mkdir(parents=True)
    for n in SCHEDULERS:
        (d / "scx/bin" / n).write_text("#!/bin/sh\n")
    shutil.copy(ROOT / "src/scx/schedulers.tsv", d / "scx/schedulers.tsv")
    (d / "extest/lib/libextest.so").write_text("32")
    (d / "extest/lib64/libextest.so").write_text("64")
    (d / "ghostty/root/usr/bin/ghostty").write_text("")
    (d / "ghostty/root/usr/share/applications/com.mitchellh.ghostty.desktop").write_text("")
    (d / "ghostty/FILES").write_text("usr/bin/ghostty\nusr/share/applications/com.mitchellh.ghostty.desktop\n")
    for c in COMPONENTS:
        (d / c / "LICENSES" / f"{c}-LICENSE").write_text(c)
        (d / c / "SOURCE").write_text("src")
        (d / c / "BUILDINFO").write_text("build")
    assert assemble(d, ROOT / "src/components.tsv").returncode == 0
    return d


def problems(img, components=COMPONENTS, **kw):
    return layout.check(listing(img), SCHEDULERS, components, **kw)


def test_assembled_image_passes_and_components_are_one_per_line(img):
    assert problems(img) == []
    assert (img / "COMPONENTS").read_text().split() == COMPONENTS


def remove_scheduler(img):
    (img / "scx/bin" / SCHEDULERS[0]).unlink()


def remove_components_file(img):
    (img / "COMPONENTS").unlink()


def add_stray_directory(img):
    (img / "stray").mkdir()
    (img / "stray/file").write_text("x")


@pytest.mark.parametrize("mutate,expected", [
    (remove_scheduler, f"the binaries image lacks scx/bin/{SCHEDULERS[0]}"),  # named after the real manifest
    (remove_components_file, "the binaries image lacks COMPONENTS"),
    (add_stray_directory, "the binaries image holds unexpected top-level entries: stray"),
])
def test_a_damaged_image_fails_and_names_the_problem(img, mutate, expected):
    mutate(img)
    assert problems(img) == [expected]


def test_empty_dx_gx_image_passes_only_with_allow_empty(tmp_path):
    d = tmp_path / "empty"
    d.mkdir()
    assert assemble(d, ROOT / "dx/components.tsv", "--allow-empty").returncode == 0
    assert layout.check(listing(d), SCHEDULERS, [], allow_empty=True) == []
    with pytest.raises(ValueError):
        layout.check(listing(d), SCHEDULERS, [])  # the base never accepts zero components
    assert assemble(d, ROOT / "dx/components.tsv").returncode != 0  # nor does the assembler
    (d / "BUILDINFO").unlink()
    assert layout.check(listing(d), SCHEDULERS, [], allow_empty=True) == ["the binaries image lacks BUILDINFO"]


def test_an_incomplete_base_fails_even_when_empty_is_allowed(img):
    shutil.rmtree(img / "extest")
    shutil.rmtree(img / "ghostty")
    assert "the binaries image lacks extest/lib/libextest.so" in problems(img, allow_empty=True)


@pytest.mark.parametrize("directory,components", [("src", COMPONENTS), ("dx", []), ("gx", [])])
def test_packages_tree_of_every_image(img, tmp_path, directory, components):
    """/packages as the Containerfile lays it out: the image's lists plus the shared installer, read with the file names `main` derives."""
    if components:
        top = img
    else:
        top = tmp_path / "empty"
        top.mkdir()
        assert assemble(top, ROOT / "dx/components.tsv", "--allow-empty").returncode == 0
    (top / "packages").mkdir()
    for f in (ROOT / directory / "packages").iterdir():
        shutil.copy(f, top / "packages")
    shutil.copy(ROOT / "installer/install.sh", top / "packages")
    names = sorted(p.name for p in (ROOT / directory / "packages").iterdir()) + ["install.sh"]
    assert layout.check(listing(top), SCHEDULERS, components, allow_empty=not components, packages=names) == []
    (top / "packages/install.sh").unlink()
    assert layout.check(listing(top), SCHEDULERS, components, allow_empty=not components, packages=names) == ["the binaries image lacks packages/install.sh"]
    # a layout check that does not expect /packages rejects the directory
    assert "unexpected top-level entries: packages" in layout.check(listing(top), SCHEDULERS, components, allow_empty=not components)[0]
