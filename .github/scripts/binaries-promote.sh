#!/usr/bin/env bash
# Promote the binaries: retag the TESTED, signed octarine-bins:testing to :latest (no rebuild, the same digest and the same signature). Runs on main only
# (binaries.yml). Nothing else moves `latest`; octarine-os reads `latest` and nothing else.
#
#   binaries-promote.sh <registry-image>        env: GITHUB_OUTPUT (changed=true|false, digest=<digest of latest>)
#
# Fails, and changes nothing, unless `testing` exists and verifies with cosign.pub: content that was not built and signed on testing is never put on latest. A failed
# or still running build on testing therefore fails the promotion (or promotes the previous testing build, which was signed and approved before); re-run the
# promotion when the build on testing is green. Afterwards the tag is read back until it shows the digest, and the signature is verified through `latest`.
set -euo pipefail
image=${1:?registry image}
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
fail() { echo "::error::$*" >&2; exit 1; }
emit() { echo "$1=$2"; [[ -z "${GITHUB_OUTPUT:-}" ]] || echo "$1=$2" >> "${GITHUB_OUTPUT}"; }

rc=0
tested=$(IMAGE="${image}" "${here}/registry-digest.sh" testing) || rc=$?
[[ "${rc}" == 0 ]] || fail "${image}:testing could not be read (exit ${rc}; 3 = it does not exist): build on testing first"
"${here}/cosign-check.sh" --key cosign.pub "${image}@${tested}" || fail "${image}@${tested} is not signed with cosign.pub"

rc=0
current=$(IMAGE="${image}" "${here}/registry-digest.sh" latest) || rc=$?
[[ "${rc}" == 0 || "${rc}" == 3 ]] || fail "${image}:latest could not be read (exit ${rc})"
if [[ "${rc}" == 0 && "${current}" == "${tested}" ]]; then
    echo "latest already is the tested image ${tested}: nothing to promote"
    emit changed false; emit digest "${tested}"
    exit 0
fi
"${here}/retry.sh" skopeo copy --all --preserve-digests "docker://${image}@${tested}" "docker://${image}:latest"
"${here}/registry-settled.sh" "${image}" latest "${tested}"
"${here}/cosign-check.sh" --key cosign.pub "${image}:latest" || fail "${image}:latest does not verify after the retag"
echo "promoted ${image}:latest ${current:-<none>} -> ${tested}"
emit changed true; emit digest "${tested}"
