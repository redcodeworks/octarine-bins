"""The pin files, writer into reader (the real ghostty pins are copied, written by the real writer and read back by the real reader), and the weekly decisions against a fake
GitHub API (githubkit talks to it over HTTP) and a Zig index file."""
import base64
import json
import shutil

import httpx
import pytest
from githubkit.exception import RequestFailed

import ghostty_check
import pins
import upstream_check
from conftest import ROOT, fixture

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


def test_real_pin_files_load():
    for c in ("scx", "extest", "ghostty"):
        pin = pins.load_upstream(ROOT / "src" / c / "upstream.env")
        assert pin.watch and len(pin.commit) == 40


def test_the_ghostty_writer_changes_the_two_pin_files_together_and_nothing_else(root):
    before = (root / "src/ghostty/zig.env").read_text().splitlines()
    pins.write_ghostty(root / "src/ghostty", "v1.9.0", SHA, ZIG, URL, "c" * 64, 123)
    pin = pins.load_upstream(root / "src/ghostty/upstream.env")
    assert (pin.tag, pin.commit) == ("v1.9.0", SHA)
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


def test_a_tag_pinned_component_bumps_to_a_newer_release(root, github):
    tag = pins.load_upstream(root / "src/scx/upstream.env").tag
    github.routes.update({"/repos/sched-ext/scx/releases/latest": fixture("release", tag_name="v9.0.0"), "/repos/sched-ext/scx/commits/v9.0.0": fixture("commit", sha=SHA)})
    d = upstream_check.check(root, "src/scx")
    assert (d.verb, d.tag, d.commit) == ("bump", "v9.0.0", SHA)
    github.routes["/repos/sched-ext/scx/releases/latest"] = fixture("release", tag_name=tag)
    assert upstream_check.check(root, "src/scx").verb == "current"


def test_an_api_error_ends_the_check_with_the_sdks_error(root, github):
    with pytest.raises(ValueError, match="no release"):  # 404 on the release of a tag-pinned project
        upstream_check.check(root, "src/scx")
    github.routes["/repos/sched-ext/scx/releases/latest"] = fixture("release", tag_name="v9.0.0")
    with pytest.raises(RequestFailed):  # the commit of the tag cannot be read: nothing is guessed
        upstream_check.check(root, "src/scx")


def test_a_fork_pin_waits_for_the_fork_to_contain_the_new_commit(root, github):
    pinned = pins.load_upstream(root / "src/extest/upstream.env").commit
    github.routes.update({"/repos/Supreeeme/extest/releases/latest": fixture("release", tag_name="v2"), "/repos/Supreeeme/extest/commits/v2": fixture("commit", sha=SHA),
                          f"/repos/Supreeeme/extest/compare/{pinned}...{SHA}": fixture("compare", status="ahead"), "/repos/redcodeworks/extest": fixture("repo", default_branch="main"),
                          f"/repos/redcodeworks/extest/compare/{SHA}...main": fixture("compare", status="behind")})
    assert upstream_check.check(root, "src/extest").verb == "forkbehind"
    github.routes[f"/repos/redcodeworks/extest/compare/{SHA}...main"] = fixture("compare", status="ahead")
    d = upstream_check.check(root, "src/extest")
    assert (d.verb, d.tag, d.commit) == ("bump", "v2", SHA)
    del github.routes[f"/repos/redcodeworks/extest/compare/{SHA}...main"]  # 404: the fork does not have the commit at all
    assert upstream_check.check(root, "src/extest").verb == "forkbehind"


def ghostty_world(monkeypatch, github, tmp_path, root, tags, zon, index):
    tag = pins.load_upstream(root / "src/ghostty/upstream.env").tag
    f = tmp_path / "index.json"
    f.write_text(json.dumps(index))
    monkeypatch.setenv("ZIG_INDEX_FILE", str(f))
    github.routes.update({"/repos/ghostty-org/ghostty/tags": [{**fixture("tag"), "name": n} for n in tags(tag)],
                          "/repos/ghostty-org/ghostty/commits/v9.0.0": fixture("commit", sha=SHA2),
                          "/repos/ghostty-org/ghostty/contents/build.zig.zon": fixture("content", content=base64.b64encode(zon.encode()).decode())})


ZON = '.{ .minimum_zig_version = "0.17.0", }'
ENTRY = {"x86_64-linux": {"tarball": URL, "shasum": "c" * 64, "size": "5"}}


def test_ghostty_bump_brings_the_newest_patch_of_the_zig_minor_the_tag_needs(root, tmp_path, monkeypatch, github):
    ghostty_world(monkeypatch, github, tmp_path, root, lambda t: [t, "v9.0.0", "tip", "vt-0.1.0", "v9.1.0-rc1"], ZON, {ZIG: ENTRY, "0.17.0": {}, "master": {}})
    d = ghostty_check.check(root)
    assert (d.verb, d.tag, d.commit, d.zig, d.zig_url, d.zig_sha256, d.zig_size) == ("bump", "v9.0.0", SHA2, ZIG, URL, "c" * 64, "5")
    assert d.line().startswith(f"bump v9.0.0 {SHA2} {ZIG} {URL}")


def test_ghostty_waits_when_ziglang_has_no_tarball_of_the_minor_yet(root, tmp_path, monkeypatch, github):
    ghostty_world(monkeypatch, github, tmp_path, root, lambda t: [t, "v9.0.0"], ZON, {"0.16.0": ENTRY})
    assert ghostty_check.check(root).verb == "wait"


def test_ghostty_current_when_the_pin_is_the_newest_stable_tag(root, tmp_path, monkeypatch, github):
    ghostty_world(monkeypatch, github, tmp_path, root, lambda t: [t, "tip"], ZON, {ZIG: ENTRY})
    assert ghostty_check.check(root).verb == "current"


def fake_get(monkeypatch, *responses):
    """httpx.get answers with the given responses in turn (an exception instance is raised); returns the list of calls."""
    calls, queue = [], list(responses)

    def get(url, **kwargs):
        calls.append(kwargs)
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(ghostty_check.httpx, "get", get)
    return calls


def response(status, content=b""):
    return httpx.Response(status, content=content, request=httpx.Request("GET", ghostty_check.ZIG_INDEX_URL))


def test_the_zig_index_download_retries_transport_errors_and_5xx(monkeypatch):
    monkeypatch.delenv("ZIG_INDEX_FILE", raising=False)
    monkeypatch.setenv("RETRY_DELAY", "0")
    calls = fake_get(monkeypatch, httpx.ConnectTimeout("timed out"), response(503), response(200, b'{"0.17.1": {}}'))
    assert list(ghostty_check.load_index().root) == ["0.17.1"] and len(calls) == 3
    assert calls[0]["timeout"] == 30 and calls[0]["follow_redirects"] is True


@pytest.mark.parametrize("answer,error", [(response(404), httpx.HTTPStatusError), (response(200, b"not json"), ValueError), (response(200, b'{"0.17.1": "not a release"}'), ValueError)])
def test_a_definitive_or_invalid_zig_index_raises_and_is_not_retried(monkeypatch, answer, error):
    monkeypatch.delenv("ZIG_INDEX_FILE", raising=False)
    calls = fake_get(monkeypatch, answer)
    with pytest.raises(error):
        ghostty_check.load_index()
    assert len(calls) == 1


def test_a_zig_index_that_stays_down_raises_after_three_tries(monkeypatch):
    monkeypatch.delenv("ZIG_INDEX_FILE", raising=False)
    monkeypatch.setenv("RETRY_DELAY", "0")
    calls = fake_get(monkeypatch, *[httpx.ConnectError("refused")] * 3)
    with pytest.raises(httpx.ConnectError, match="failed 3 attempts"):
        ghostty_check.load_index()
    assert len(calls) == 3


def test_lock_changes_lists_the_packages_that_moved():
    import bump_run
    old = '[[package]]\nname = "httpx"\nversion = "0.28.1"\n\n[[package]]\nname = "yaml"\nversion = "6"\n'
    new = '[[package]]\nname = "httpx"\nversion = "0.28.2"\n\n[[package]]\nname = "yaml"\nversion = "6"\n\n[[package]]\nname = "new"\nversion = "1"\n'
    assert bump_run.lock_changes(old, new) == ["httpx 0.28.1 to 0.28.2", "new - to 1"]
    assert bump_run.lock_changes(old, old) == []


def test_newest_python_is_the_highest_default_cpython_of_the_same_minor():
    import bump_run
    listing = [{"implementation": i, "variant": v, "version": ver} for i, v, ver in [
        ("cpython", "default", "3.14.9"), ("cpython", "default", "3.14.10"), ("cpython", "freethreaded", "3.14.11"),
        ("pypy", "default", "3.14.12"), ("cpython", "default", "3.15.0")]]
    assert bump_run.newest_python(listing, "3.14.4") == "3.14.10"
    assert bump_run.newest_python(listing, "3.13.1") is None
