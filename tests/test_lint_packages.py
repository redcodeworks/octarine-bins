"""The lint rules of the package lists: the real lists of the three images pass, and each rule catches what it is for."""
import pytest

import lint_packages
from conftest import ROOT, script


@pytest.mark.parametrize("directory", ["src", "dx", "gx"])
def test_the_real_lists_pass(directory):
    assert script("lint_packages.py", *map(str, sorted((ROOT / directory / "packages").glob("*.txt")))).returncode == 0


def lint(tmp_path, **lists):
    paths = []
    for name, text in lists.items():
        p = tmp_path / name
        p.write_text(text)
        paths.append(p)
    return lint_packages.lint(paths)


@pytest.mark.parametrize("files,fragment", [
    ({"required-fedora.txt": "mangohud.i686\n"}, "needs mangohud.x86_64 listed explicitly"),
    ({"required-copr.txt": "owner/project:Pkg\n"}, "not owner/project:name-version-release"),
    ({"required-fedora.txt": "gcc\n", "optional-fedora.txt": "gcc\n"}, "duplicates: gcc"),
    ({"fedora.txt": "gcc\n"}, "the name must be"),
    ({"required-fedora.txt": "gcc make\n"}, "more than one token per line"),
    ({"required-fedora.txt": "# only a comment\n"}, "no packages"),
    ({"required-negativo17.txt": "rpmfusion-thing\n"}, "RPM Fusion is not used"),
])
def test_each_rule_catches_its_problem(tmp_path, files, fragment):
    assert any(fragment in p for p in lint(tmp_path, **files))


def test_an_i686_entry_with_its_x86_64_sibling_passes(tmp_path):
    assert lint(tmp_path, **{"required-fedora.txt": "mangohud.i686\nmangohud.x86_64  # both\n"}) == []
