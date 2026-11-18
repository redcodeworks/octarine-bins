"""sanity.py skips the .archive/ folder (the kept shell scripts are inert), and still finds a broken live script."""

import os
import subprocess

import sanity


def tracked_repo(tmp_path, files):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    for name, content in files.items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(content)
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)


def test_a_broken_script_in_the_archive_is_not_checked(tmp_path, monkeypatch):
    tracked_repo(
        tmp_path,
        {".archive/shell-ci/scripts/old.sh": "if then fi (\n", "ok.sh": "echo ok\n"},
    )
    monkeypatch.chdir(tmp_path)
    assert sanity.problems() == []


def test_a_broken_live_script_is_found(tmp_path, monkeypatch):
    tracked_repo(tmp_path, {"bad.sh": "if then fi (\n"})
    monkeypatch.chdir(tmp_path)
    assert sanity.problems() == ["bash -n bad.sh"]


def test_the_real_containerfiles_copy_only_listed_inputs():
    assert sanity.input_problems() == []


def test_a_copy_of_an_unlisted_path_is_found(tmp_path, monkeypatch):
    tracked_repo(
        tmp_path,
        {
            "src/Containerfile": "FROM x AS a\nCOPY --from=a /out /out\nCOPY lib /l\nCOPY --from=extra extra.txt /e\n",
            "Justfile": "--build-context extra=tools\n",
        },
    )
    monkeypatch.chdir(tmp_path)
    assert sanity.input_problems() == [
        "src/Containerfile copies 'tools/extra.txt', which is not in ci.inputs('src'): a change to it would not rebuild the image"
    ]
