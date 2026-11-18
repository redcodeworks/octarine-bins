"""stamp_image.py with real podman in a scratch storage (skipped without podman).

A stamp is written on top of a cached build, so every build is stamped fresh.
"""

import json
import re
import shutil
import subprocess

import pytest

from conftest import script

pytestmark = pytest.mark.skipif(
    not shutil.which("podman"), reason="podman is not installed"
)


def test_the_stamp_is_fresh_on_a_build_served_from_the_layer_cache(tmp_path):
    """Two builds of the same content, each stamped, differ in labels and digest."""
    podman = [
        "podman",
        "--root",
        str(tmp_path / "root"),
        "--runroot",
        str(tmp_path / "run"),
        "--storage-driver",
        "overlay",
    ]
    ctx = tmp_path / "ctx"
    ctx.mkdir()
    (ctx / "f").write_text("content")
    (ctx / "Containerfile").write_text('FROM scratch\nCOPY f /f\nLABEL static="yes"\n')

    def build_and_stamp(revision):
        """Build ctx (from the layer cache after the first time), stamp it.

        Returns the labels and the digest of the stamped image.
        """
        # the second time, all layers come from the cache
        subprocess.run(
            [
                *podman,
                "build",
                "--layers",
                "--quiet",
                "--tag",
                "octarine-bins:t",
                str(ctx),
            ],
            check=True,
            capture_output=True,
        )
        p = script(
            "stamp_image.py",
            "octarine-bins:t",
            "20261117-" + revision[:7],
            revision,
            extra={"PODMAN": " ".join(podman)},
        )
        assert p.returncode == 0, p.stderr
        info = json.loads(
            subprocess.run(
                [*podman, "inspect", "octarine-bins:t"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout
        )[0]
        return info["Config"]["Labels"], info["Digest"]

    one, d1 = build_and_stamp("a" * 40)
    two, d2 = build_and_stamp("b" * 40)
    assert (
        one["org.opencontainers.image.revision"] == "a" * 40
        and two["org.opencontainers.image.revision"] == "b" * 40
    )

    # the label must be what binaries_skip.py and binaries_promote.py accept
    assert re.fullmatch(r"[0-9a-f]{40}", two["org.opencontainers.image.revision"])
    assert (
        two["org.opencontainers.image.version"] == "20261117-bbbbbbb"
        and two["static"] == "yes"
    )
    assert d1 != d2
