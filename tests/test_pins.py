"""The pin files, writer into reader (the real ghostty pins are copied, written by the real writer and read back by the real reader), and the weekly decisions with the
GitHub API replaced by a table of answers."""
import json
import shutil

import pytest

import ci
import ghostty_check
import pins
import upstream_check
from conftest import ROOT

SHA = "a" * 40
SHA2 = "b" * 40
ZIG = "0.17.2"
URL = f"https://ziglang.org/download/{ZIG}/zig-x86_64-linux-{ZIG}.tar.xz"


@pytest.fixture
def root(tmp_path):
    shutil.copytree(ROOT / "src/ghostty", tmp_path / "src/ghostty", ignore=shutil.ignore_patterns("*.sh"))
    shutil.copytree(ROOT / "src/scx", tmp_path / "src/scx", ignore=shutil.ignore_patterns("*.sh", "schedulers.tsv"))
    shutil.copytree(ROOT / "src/extest", tmp_path / "src/extest", ignore=shutil.ignore_patterns("*.sh"))
    return tmp_path


def api(monkeypatch, answers):
    """Answer `gh api` reads from a dict path -> JSON; a path that is not in it cannot be read."""
    monkeypatch.setattr(ci, "gh_api", lambda path, paginate=False: answers.get(path))


def test_real_pin_files_load():
    for c in ("scx", "extest", "ghostty"):
        pin = pins.load_upstream(ROOT / "src" / c / "upstream.env")
        assert pin["UPSTREAM_WATCH"] and len(pin["UPSTREAM_COMMIT"]) == 40


def test_the_ghostty_writer_changes_the_two_pin_files_together_and_nothing_else(root):
    before = (root / "src/ghostty/zig.env").read_text().splitlines()
    pins.write_ghostty(root / "src/ghostty", "v1.9.0", SHA, ZIG, URL, "c" * 64, 123)
    pin = pins.load_upstream(root / "src/ghostty/upstream.env")
    assert (pin["UPSTREAM_TAG"], pin["UPSTREAM_COMMIT"]) == ("v1.9.0", SHA)
    zig = pins.read_env(root / "src/ghostty/zig.env")
    assert (zig["ZIG_VERSION"], zig["ZIG_URL"], zig["ZIG_SIGNATURE_URL"]) == (ZIG, URL, URL + ".minisig")
    after = (root / "src/ghostty/zig.env").read_text().splitlines()
    assert [l for l in before if l.startswith("#")] == [l for l in after if l.startswith("#")]  # comments and the key stay
    assert zig["ZIG_MINISIGN_KEY"].startswith("RW")


def test_the_writer_refuses_an_unofficial_zig_entry_and_touches_nothing(root):
    before = (root / "src/ghostty/upstream.env").read_text()
    with pytest.raises(ValueError):
        pins.write_ghostty(root / "src/ghostty", "v1.9.0", SHA, ZIG, "https://example.com/zig.tar.xz", "c" * 64, 1)
    assert (root / "src/ghostty/upstream.env").read_text() == before


def test_a_tag_pinned_component_bumps_to_a_newer_release(root, monkeypatch):
    tag = pins.load_upstream(root / "src/scx/upstream.env")["UPSTREAM_TAG"]
    api(monkeypatch, {"repos/sched-ext/scx/releases/latest": {"tag_name": "v9.0.0"}, "repos/sched-ext/scx/commits/v9.0.0": {"sha": SHA}})
    assert upstream_check.check(root, "src/scx") == ("bump", "v9.0.0", SHA)
    api(monkeypatch, {"repos/sched-ext/scx/releases/latest": {"tag_name": tag}})
    assert upstream_check.check(root, "src/scx")[0] == "current"
    api(monkeypatch, {})
    assert upstream_check.check(root, "src/scx")[0] == "unreachable"


def test_a_fork_pin_waits_for_the_fork_to_contain_the_new_commit(root, monkeypatch):
    pinned = pins.load_upstream(root / "src/extest/upstream.env")["UPSTREAM_COMMIT"]
    answers = {"repos/Supreeeme/extest/releases/latest": {"tag_name": "v2"}, "repos/Supreeeme/extest/commits/v2": {"sha": SHA},
               f"repos/Supreeeme/extest/compare/{pinned}...{SHA}": {"status": "ahead"}, "repos/redcodeworks/extest": {"default_branch": "main"},
               f"repos/redcodeworks/extest/compare/{SHA}...main": {"status": "behind"}}
    api(monkeypatch, answers)
    assert upstream_check.check(root, "src/extest")[0] == "forkbehind"
    answers[f"repos/redcodeworks/extest/compare/{SHA}...main"] = {"status": "ahead"}
    assert upstream_check.check(root, "src/extest") == ("bump", "v2", SHA)


def ghostty_world(monkeypatch, tmp_path, root, tags, zon, index):
    tag = pins.load_upstream(root / "src/ghostty/upstream.env")["UPSTREAM_TAG"]
    f = tmp_path / "index.json"
    f.write_text(json.dumps(index))
    monkeypatch.setenv("ZIG_INDEX_FILE", str(f))
    import base64
    api(monkeypatch, {"repos/ghostty-org/ghostty/tags?per_page=100": [{"name": n} for n in tags(tag)], "repos/ghostty-org/ghostty/commits/v9.0.0": {"sha": SHA2},
                      "repos/ghostty-org/ghostty/contents/build.zig.zon?ref=v9.0.0": {"content": base64.b64encode(zon.encode()).decode()}})


ZON = '.{ .minimum_zig_version = "0.17.0", }'
ENTRY = {"x86_64-linux": {"tarball": URL, "shasum": "c" * 64, "size": "5"}}


def test_ghostty_bump_brings_the_newest_patch_of_the_zig_minor_the_tag_needs(root, tmp_path, monkeypatch):
    ghostty_world(monkeypatch, tmp_path, root, lambda t: [t, "v9.0.0", "tip", "vt-0.1.0", "v9.1.0-rc1"], ZON, {ZIG: ENTRY, "0.17.0": {}, "master": {}})
    assert ghostty_check.check(root) == ("bump", "v9.0.0", SHA2, ZIG, URL, "c" * 64, "5")


def test_ghostty_waits_when_ziglang_has_no_tarball_of_the_minor_yet(root, tmp_path, monkeypatch):
    ghostty_world(monkeypatch, tmp_path, root, lambda t: [t, "v9.0.0"], ZON, {"0.16.0": ENTRY})
    assert ghostty_check.check(root)[0] == "wait"


def test_ghostty_current_when_the_pin_is_the_newest_stable_tag(root, tmp_path, monkeypatch):
    ghostty_world(monkeypatch, tmp_path, root, lambda t: [t, "tip"], ZON, {ZIG: ENTRY})
    assert ghostty_check.check(root)[0] == "current"
