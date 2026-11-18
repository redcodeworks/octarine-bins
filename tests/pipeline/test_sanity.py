"""sanity.py skips the .archive/ folder and still finds a broken live script.

The kept shell scripts in .archive/ are inert, so they are never checked.
"""

import os
import subprocess

import sanity


def tracked_repo(tmp_path, files):
    """Create a git repository in `tmp_path` with the `files` ({path: text}) added.

    The files are staged, not committed, which is enough for `git ls-files`.
    """
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)

    # write each file, creating its directories
    for name, content in files.items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(content)
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)


def test_a_broken_script_in_the_archive_is_not_checked(tmp_path, monkeypatch):
    """A syntax error under .archive/ is inert and gives no problem."""
    tracked_repo(
        tmp_path,
        {".archive/shell-ci/scripts/old.sh": "if then fi (\n", "ok.sh": "echo ok\n"},
    )
    monkeypatch.chdir(tmp_path)
    assert sanity.problems() == []


def test_a_broken_live_script_is_found(tmp_path, monkeypatch):
    """The same syntax error in a live script is reported by `bash -n`."""
    tracked_repo(tmp_path, {"bad.sh": "if then fi (\n"})
    monkeypatch.chdir(tmp_path)
    assert sanity.problems() == ["bash -n bad.sh"]


def test_the_real_containerfiles_copy_only_listed_inputs():
    """The repository's own Containerfiles copy only paths that ci.inputs lists."""
    assert sanity.input_problems() == []


def test_a_copy_of_an_unlisted_path_is_found(tmp_path, monkeypatch):
    """A COPY from a named context of a path outside the inputs is reported."""
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
