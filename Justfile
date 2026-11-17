# Local helpers; CI calls the same recipes (build, layout), so a failure reproduces here.
# BUILD_ARGS adds podman build arguments (CI: --cache-from/--cache-to). PODMAN overrides the podman command (for example `PODMAN="podman --root /scratch/root --runroot /scratch/run --storage-driver overlay"`).

[private]
default:
    @just --list

# Check Just syntax
check:
    just --unstable --fmt --check -f Justfile

# Build an image: dir is src (the base image, octarine-bins), dx or gx (octarine-bins-dx, -gx). Every component in its own stage; a failing stage fails the build and nothing is tagged
build image="octarine-bins:dev" version="dev" revision="unknown" dir="src":
    #!/usr/bin/env bash
    set -euo pipefail
    builder=$(sed -n 's/^BUILDER=//p' src/builder.env)
    [[ "${builder}" =~ ^registry\.fedoraproject\.org/fedora:[0-9]+@sha256:[0-9a-f]{64}$ ]] || { echo "::error::src/builder.env has no digest-pinned BUILDER"; exit 1; }
    # --jobs 3: the three component stages are independent and build at the same time
    ${PODMAN:-podman} build --jobs 3 ${BUILD_ARGS:-} \
        --build-arg "BUILDER=${builder}" \
        --build-arg "IMAGE_VERSION={{ version }}" \
        --build-arg "IMAGE_REVISION={{ revision }}" \
        --build-arg "IMAGE_CREATED=$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
        --tag "{{ image }}" \
        --build-context lib=src/lib \
        --build-context installer=installer \
        --file {{ dir }}/Containerfile {{ dir }}

# Check a built image against the layout octarine-os reads (/binaries/<name>/, COMPONENTS, BUILDINFO, LICENSES/); only dx and gx may hold no component; every image carries /packages
layout image="octarine-bins:dev" dir="src":
    uv run --frozen --no-dev python -I .github/scripts/binaries_layout.py "{{ image }}" "{{ dir }}"

# Run the tests: pytest through uv (the locked dependencies, Python from .python-version) and the bash tests (installer). group is one directory of tests/ (CI runs one job per group); empty runs all
test group="":
    #!/usr/bin/env bash
    set -euo pipefail
    dir="tests/{{ group }}"
    uv run --frozen pytest "${dir}"
    for t in $(find "${dir}" -name '*.sh' | sort); do bash "$t"; done
