#!/usr/bin/env bash
# Decide whether a push to testing needs a build: NOT when the inputs of the build equal those of the commit the current `testing` image was built from (its label
# org.opencontainers.image.revision) and that image verifies with cosign.pub. Prints and exports skip=true|false (GITHUB_OUTPUT).
#
#   binaries-skip.sh <registry-image>      run in a git checkout; env: GITHUB_OUTPUT
#
# The inputs are everything that decides what the image contains or how it is built: src/ (the Containerfile, pins, builder, scripts), the Justfile (build arguments),
# binaries.yml, push-image.sh and binaries-layout.sh. A change to docs, tests, other workflows or scripts does not rebuild. Absent testing, an unreadable revision, other
# inputs, or an image that does not verify (a signature of an old key, a failed signing) all mean skip=false: build. Only a READ ERROR (registry or cosign could not be
# asked) fails the script: a build must not be skipped or forced on a guess. The job that calls this always succeeds, so the required check names always report.
set -euo pipefail
image=${1:?registry image}
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
paths=(src Justfile .github/workflows/binaries.yml .github/scripts/push-image.sh .github/scripts/binaries-layout.sh)
emit() { echo "skip=$1 ($2)"; [[ -z "${GITHUB_OUTPUT:-}" ]] || echo "skip=$1" >> "${GITHUB_OUTPUT}"; exit 0; }
inputs_of() { git ls-tree -r "$1" -- "${paths[@]}" 2> /dev/null | sha256sum | sed 's/ .*//'; }

rc=0
tested=$(IMAGE="${image}" "${here}/registry-digest.sh" testing) || rc=$?
[[ "${rc}" != 3 ]] || emit false "no testing image yet"
[[ "${rc}" == 0 ]] || { echo "::error::${image}:testing could not be read (exit ${rc}): not deciding on a guess" >&2; exit 1; }
cfg=$("${here}/registry-read.sh" config "${image}@${tested}") || { echo "::error::the config of ${image}@${tested} could not be read" >&2; exit 1; }
rev=$(jq -r '.config.Labels["org.opencontainers.image.revision"] // empty' <<< "${cfg}")
[[ "${rev}" =~ ^[0-9a-f]{40}$ ]] || emit false "testing has no revision label"
git cat-file -e "${rev}^{commit}" 2> /dev/null || git fetch -q --depth=1 origin "${rev}" 2> /dev/null || true
git cat-file -e "${rev}^{commit}" 2> /dev/null || emit false "the revision ${rev} of testing cannot be fetched"
[[ "$(inputs_of "${rev}")" == "$(inputs_of HEAD)" ]] || emit false "the build inputs differ from those of ${rev}"
rc=0
"${here}/cosign-check.sh" --key cosign.pub "${image}@${tested}" || rc=$?
[[ "${rc}" != 5 ]] || emit false "testing does not verify with cosign.pub"
[[ "${rc}" == 0 ]] || { echo "::error::the signature of ${image}@${tested} could not be checked (exit ${rc})" >&2; exit 1; }
emit true "testing is the signed build of ${rev}, same inputs"
