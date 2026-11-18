# Local helpers; CI calls the same recipes (build, layout), so a failure reproduces here.
# BUILD_ARGS adds podman build arguments (CI: --cache-from/--cache-to). PODMAN overrides the podman command (for example `PODMAN="podman --root /scratch/root --runroot /scratch/run --storage-driver overlay"`).

[private]
default:
    @just --list

# Check Just syntax
check:
    just --unstable --fmt --check -f Justfile

# Build an image: dir is src (octarine-bins). Every component in its own stage; a failing stage fails the build and nothing is tagged
build image="octarine-bins:dev" version="dev" revision="unknown" dir="src":
    #!/usr/bin/env bash
    set -euo pipefail
    builder=$(sed -n 's/^BUILDER=//p' src/builder.env)
    [[ "${builder}" =~ ^registry\.fedoraproject\.org/fedora:[0-9]+@sha256:[0-9a-f]{64}$ ]] || { echo "::error::src/builder.env has no digest-pinned BUILDER"; exit 1; }
    # --jobs 3: the three component stages are independent and build at the same time
    ${PODMAN:-podman} build --jobs 3 ${BUILD_ARGS:-} \
        --build-arg "BUILDER=${builder}" \
        --tag "{{ image }}" \
        --file {{ dir }}/Containerfile {{ dir }}
    # the stamp (version, revision, created labels) is a separate no-cache step, so a cache-served build is stamped too (stamp_image.py)
    uv run --frozen --no-dev python -I .github/scripts/stamp_image.py "{{ image }}" "{{ version }}" "{{ revision }}"

# Check a built image against the layout octarine-os reads (/binaries/<name>/, COMPONENTS, BUILDINFO, LICENSES/)
layout image="octarine-bins:dev" dir="src":
    uv run --frozen --no-dev python -I .github/scripts/binaries_layout.py "{{ image }}" "{{ dir }}"

# Run the tests: pytest through uv (the locked dependencies, Python from .python-version). group is one directory of tests/ (CI runs one job per group); empty runs all
test group="":
    #!/usr/bin/env bash
    set -euo pipefail
    dir="tests/{{ group }}"
    uv run --frozen pytest "${dir}"

# Opt in to the pre-commit hook (githooks/pre-commit): it formats the staged Python files with ruff. Undo with `git config --unset core.hooksPath`
hooks:
    git config core.hooksPath githooks
