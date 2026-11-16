#!/usr/bin/env bash
# Tests for the build skip (.github/scripts/binaries-skip.sh): a push whose build inputs equal those of the commit testing was built from, with a verifying testing
# image, builds nothing. Real git repositories, fake skopeo and cosign. Run: tests/binaries-skip.sh
set -uo pipefail
export RETRY_DELAY=0
here=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
root=$(mktemp -d)
trap 'rm -rf -- "$root"' EXIT
pass=0; failn=0; rc=0
ok() { pass=$((pass + 1)); echo "ok   $1"; }
bad() { failn=$((failn + 1)); echo "FAIL $1"; sed 's/^/     run: /' "$root/run" 2> /dev/null; echo "     rc=$rc"; }
check() { local d=$1; shift; if "$@" > "$root/out" 2>&1; then ok "$d"; else bad "$d"; fi; }
bin="$root/bin"; mkdir -p "$bin"
cat > "$bin/skopeo" <<'F'
#!/usr/bin/env bash
case "$1" in
    inspect)
        [[ "${NO_TESTING:-0}" = 1 ]] && { echo "manifest unknown" >&2; exit 1; }
        [[ "${DOWN:-0}" = 1 ]] && { echo "dial tcp: i/o timeout" >&2; exit 1; }
        if [[ "$2" == --config ]]; then printf '{"config":{"Labels":{"org.opencontainers.image.revision":"%s"}}}' "$REV"; else echo manifest; fi ;;
    manifest-digest) echo "sha256:$(printf 'a%.0s' {1..64})" ;;
esac
F
cat > "$bin/cosign" <<'F'
#!/usr/bin/env bash
[ "${COSIGN_RC:-0}" != 0 ] && echo "Error: no signatures found" >&2
exit "${COSIGN_RC:-0}"
F
chmod +x "$bin"/*
repo="$root/repo"; git init -q "$repo"; git -C "$repo" config user.email t@t; git -C "$repo" config user.name t
mkdir -p "$repo/src" "$repo/.github/workflows" "$repo/.github/scripts"
echo one > "$repo/src/f"; echo doc > "$repo/README"; echo wf > "$repo/.github/workflows/binaries.yml"; echo p > "$repo/.github/scripts/push-image.sh"
echo l > "$repo/.github/scripts/binaries-layout.sh"; echo j > "$repo/Justfile"; echo t > "$repo/.github/workflows/test.yml"
git -C "$repo" add .; git -C "$repo" commit -q -m one
export REV; REV=$(git -C "$repo" rev-parse HEAD)
skip() { : > "$root/gho"; (cd "$repo" && env "$@" PATH="$bin:$PATH" GITHUB_OUTPUT="$root/gho" bash "$here/.github/scripts/binaries-skip.sh" ghcr.io/o/i) > "$root/run" 2>&1; rc=$?; }
skip X=1
check "same inputs, testing verifies: skip=true" bash -c "[ $rc = 0 ] && grep -qx skip=true '$root/gho'"
echo docs > "$repo/README"; echo t2 > "$repo/.github/workflows/test.yml"; git -C "$repo" commit -q -am docs
skip X=1
check "only docs and another workflow changed: still skip=true" bash -c "[ $rc = 0 ] && grep -qx skip=true '$root/gho'"
skip COSIGN_RC=1
check "testing does not verify (an old key): skip=false, build" bash -c "[ $rc = 0 ] && grep -qx skip=false '$root/gho'"
skip NO_TESTING=1
check "no testing image yet: skip=false, build" bash -c "[ $rc = 0 ] && grep -qx skip=false '$root/gho'"
skip DOWN=1
check "an unreadable registry fails instead of guessing" test "$rc" != 0
echo two > "$repo/src/f"; git -C "$repo" commit -q -am src
skip X=1
check "src/ changed: skip=false, build" bash -c "[ $rc = 0 ] && grep -qx skip=false '$root/gho'"
git -C "$repo" checkout -q -- . ; git -C "$repo" reset -q --hard HEAD~1; echo j2 > "$repo/Justfile"; git -C "$repo" commit -q -am just
skip X=1
check "the Justfile (build arguments) changed: skip=false, build" bash -c "[ $rc = 0 ] && grep -qx skip=false '$root/gho'"
echo; echo "passed: $pass, failed: $failn"
[ "$failn" = 0 ]
