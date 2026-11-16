#!/usr/bin/env bash
# Promote the binaries: retag the TESTED, signed octarine-bins:testing to :latest (no rebuild, the same digest and the same signature). Runs on main only
# (binaries.yml). Nothing else moves `latest`; octarine-os reads `latest` and nothing else.
#
#   binaries-promote.sh <registry-image>        env: GITHUB_OUTPUT (changed=true|false, digest=<digest of latest>)
#
# Fails, and changes nothing, unless `testing` exists, verifies with cosign.pub and was built from exactly what is being promoted: the tree of the commit named by
# its label org.opencontainers.image.revision must equal the tree of this checkout (HEAD, the merge commit of the promotion PR, whose tree equals the testing tip's
# when main has nothing testing lacks). A running or failed build on testing, or a testing that moved on, therefore fails the promotion; re-run it when the tip's
# build is green. Run it in a git checkout (the revision commit is fetched when missing). Afterwards the tag is read back until it shows the digest, and the
# signature is verified through `latest`.
set -euo pipefail
image=${1:?registry image}
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
fail() { echo "::error::$*" >&2; exit 1; }
emit() { echo "$1=$2"; [[ -z "${GITHUB_OUTPUT:-}" ]] || echo "$1=$2" >> "${GITHUB_OUTPUT}"; }

rc=0
tested=$(IMAGE="${image}" "${here}/registry-digest.sh" testing) || rc=$?
[[ "${rc}" == 0 ]] || fail "${image}:testing could not be read (exit ${rc}; 3 = it does not exist): build on testing first"
"${here}/cosign-check.sh" --key cosign.pub "${image}@${tested}" || fail "${image}@${tested} is not signed with cosign.pub"
cfg=$("${here}/registry-read.sh" config "${image}@${tested}") || fail "the config of ${image}@${tested} could not be read"
rev=$(jq -r '.config.Labels["org.opencontainers.image.revision"] // empty' <<< "${cfg}")
[[ "${rev}" =~ ^[0-9a-f]{40}$ ]] || fail "${image}:testing has no valid revision label ('${rev}')"
tree_of() { git rev-parse --verify -q "$1^{tree}" 2> /dev/null; }
built=$(tree_of "${rev}") || { git fetch -q --depth=1 origin "${rev}" 2> /dev/null || true; built=$(tree_of "${rev}") || built=unknown; }
this=$(tree_of HEAD) || fail "cannot read the tree of HEAD"
[[ "${built}" == "${this}" ]] || fail "testing was built from ${rev} (tree ${built}), this tree is ${this}: the build on testing is running, failed, or testing moved on; re-run promote when the tip's build is green"

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
