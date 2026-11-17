#!/usr/bin/env bash
# The paths a binaries image is built from, one per line: the ONE place that says what rebuilds which image.
#
#   binaries-inputs.sh <dir> [--trees]      <dir> is src (the base image, octarine-bins), dx or gx
#
# --trees: only the directories whose content IS the image: the image's own directory and, for dx and gx, the shared helpers (src/lib), the pinned builder
# (src/builder.env) and the package installers (installer/). binaries-promote.sh compares those. Without it the list also holds what decides HOW the image is built (the Justfile, the shared build
# and sign actions, push-image.sh, binaries-layout.sh, the image's own caller workflow); binaries-skip.sh hashes all of it. A change to src/lib or the builder
# therefore rebuilds all three images, a change to dx/ (its package list included) only dx, to gx/ only gx, to installer/ dx and gx but not the base. Docs, tests, other workflows and scripts rebuild nothing.
set -euo pipefail
dir=${1:?directory: src, dx or gx}
case "${dir}" in
    src) workflow=binaries.yml; trees=(src) ;;
    dx | gx) workflow="binaries-${dir}.yml"; trees=("${dir}" src/lib src/builder.env installer) ;;
    *) echo "binaries-inputs.sh: unknown image directory '${dir}'" >&2; exit 2 ;;
esac
printf '%s\n' "${trees[@]}"
[[ "${2:-}" == --trees ]] && exit 0
printf '%s\n' Justfile ".github/workflows/${workflow}" .github/actions/build-image .github/scripts/push-image.sh .github/scripts/binaries-layout.sh
exit 0
