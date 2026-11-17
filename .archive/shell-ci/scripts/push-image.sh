#!/usr/bin/env bash
# Push the built image under ONE tag and print its manifest digest, which the signature belongs to:
#
#   push-image.sh <local-image> <registry-image> <tag>
#
# `podman push --digestfile` writes the digest of the manifest it pushed (retried: a network call); the registry must then show that digest for the tag
# (registry-settled.sh polls, because a read right after a write may still answer with the previous manifest). Other tags (`testing`, `latest`) are never
# pushed from here: they are registry-side copies of this digest (skopeo copy --preserve-digests) made after it was signed.
#
# Output: `digests=<digest>` printed and appended to $GITHUB_OUTPUT (when set). Tools: podman, skopeo.
set -euo pipefail
local_image="${1:?local image}"
registry_image="${2:?registry image}"
tag="${3:?tag}"
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
digestfile=$(mktemp)
trap 'rm -f "${digestfile}"' EXIT
"${here}/retry.sh" podman push --digestfile="${digestfile}" "${local_image}" "${registry_image}:${tag}"
digest=$(tr -d '[:space:]' < "${digestfile}")
[[ "${digest}" =~ ^sha256:[0-9a-f]{64}$ ]] || { echo "::error::podman push gave no digest for ${tag} ('${digest}')" >&2; exit 1; }
"${here}/registry-settled.sh" "${registry_image}" "${tag}" "${digest}"
echo "pushed ${tag} -> ${digest}"
echo "digests=${digest}"
[[ -z "${GITHUB_OUTPUT:-}" ]] || echo "digests=${digest}" >> "${GITHUB_OUTPUT}"
