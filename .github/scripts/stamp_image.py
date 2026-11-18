#!/usr/bin/env python3
"""Stamp a built image with the build's identity. Usage: stamp_image.py <local-image> <version> <revision>   (env PODMAN: the podman command, default `podman`)

Sets org.opencontainers.image.version, .revision and .created on the image, in place (the tag is moved to the stamped image). The stamp is its own `podman build --no-cache` of a
three-line Containerfile on top of the built image, so it never comes from the layer cache: a cached build (all layers served from <image>-cache, same content) still gets a fresh
stamp. A label adds no layer, so the layers stay those of the build; only the config, and with it the manifest digest, differs on every build (accepted: "build on content, stamp every build").
"""

import os
import shlex
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ci  # noqa: E402

CONTAINERFILE = """FROM {image}
ARG IMAGE_VERSION
ARG IMAGE_REVISION
ARG IMAGE_CREATED
LABEL org.opencontainers.image.version="${{IMAGE_VERSION}}" \\
      org.opencontainers.image.revision="${{IMAGE_REVISION}}" \\
      org.opencontainers.image.created="${{IMAGE_CREATED}}"
"""


def main(argv=None):
    ap = ci.arg_parser(__doc__)
    ap.add_argument("image", help="the locally built image (stamped in place)")
    ap.add_argument("version", help="<UTC date>-<short sha>")
    ap.add_argument("revision", help="the full commit sha the image was built from")
    args = ap.parse_args(argv)
    created = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with (
        tempfile.TemporaryDirectory() as empty
    ):  # an empty build context: the Containerfile copies nothing
        cmd = [
            *shlex.split(os.environ.get("PODMAN", "podman")),
            "build",
            "--no-cache",
            "--quiet",
            "--file",
            "-",
            "--tag",
            args.image,
            "--build-arg",
            f"IMAGE_VERSION={args.version}",
            "--build-arg",
            f"IMAGE_REVISION={args.revision}",
            "--build-arg",
            f"IMAGE_CREATED={created}",
            empty,
        ]
        done = subprocess.run(
            cmd,
            input=CONTAINERFILE.format(image=args.image),
            text=True,
            stdout=subprocess.DEVNULL,
        )
    if done.returncode:
        ci.log.error(f"stamping {args.image} failed")
        return 1
    ci.log.info(
        f"stamped {args.image}: version {args.version}, revision {args.revision}, created {created}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
