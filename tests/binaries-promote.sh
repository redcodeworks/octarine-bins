#!/usr/bin/env bash
# Tests for the promotion (.github/scripts/binaries-promote.sh, run by binaries.yml on main): the tested, signed `testing` image is retagged to `latest`; nothing else is
# ever put there. Fake skopeo (a directory of tags) and cosign; nothing is pulled. Needs: bash. Run: tests/binaries-promote.sh
set -uo pipefail
export RETRY_DELAY=0 READBACK_WAIT=0
here=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
root=$(mktemp -d)
trap 'rm -rf -- "$root"' EXIT
pass=0; failn=0; rc=0
ok() { pass=$((pass + 1)); echo "ok   $1"; }
bad() { failn=$((failn + 1)); echo "FAIL $1"; sed 's/^/     run: /' "$root/run" 2> /dev/null; echo "     rc=$rc"; }
check() { local d=$1; shift; if "$@" > "$root/out" 2>&1; then ok "$d"; else bad "$d"; fi; }

bin="$root/bin"; st="$root/state"; mkdir -p "$bin" "$st"
cat > "$bin/skopeo" <<'F'
#!/usr/bin/env bash
echo "skopeo $*" >> "$LOG"
ref="${@: -1}"; ref="${ref#docker://}"; tag="${ref##*:}"
case "$1" in
    inspect)
        if [[ "$2" == --config ]]; then printf '{"config":{"Labels":{"org.opencontainers.image.revision":"%s"}}}' "$REV"; exit 0; fi
        [[ -s "$ST/$tag" ]] || { echo "manifest unknown" >&2; exit 1; }
        cat "$ST/$tag" ;;
    manifest-digest) printf 'sha256:%s\n' "$(sha256sum "$2" | sed 's/ .*//')" ;;
    copy) cp "$ST/testing" "$ST/latest" ;;
esac
F
cat > "$bin/cosign" <<'F'
#!/usr/bin/env bash
echo "cosign $*" >> "$LOG"
[ "${COSIGN_RC:-0}" != 0 ] && echo "Error: no signatures found" >&2
exit "${COSIGN_RC:-0}"
F
chmod +x "$bin"/*
export LOG="$root/log" ST="$st"
IMG=ghcr.io/redcodeworks/octarine-bins
# a real repository: the revision the testing image names, then a promotion merge commit whose src/ tree equals it (src/ is what is compared, not the commit)
repo="$root/repo"; git init -q "$repo"; git -C "$repo" config user.email t@t; git -C "$repo" config user.name t
mkdir -p "$repo/src/lib" "$repo/dx" "$repo/installer"; echo i1 > "$repo/installer/install.sh"; echo one > "$repo/src/f"; echo l1 > "$repo/src/lib/x"; echo b1 > "$repo/src/builder.env"; echo d1 > "$repo/dx/f"; echo doc > "$repo/README"; git -C "$repo" add .; git -C "$repo" commit -q -m one
export REV; REV=$(git -C "$repo" rev-parse HEAD)
git -C "$repo" commit -q --amend -m "merge commit, same src/ tree"
dir=src  # the image directory promote is told (src, dx or gx)
promote() { # env assignments...
    : > "$LOG"; : > "$root/gho"
    (cd "$repo" && env "$@" PATH="$bin:$PATH" GITHUB_OUTPUT="$root/gho" bash "$here/.github/scripts/binaries-promote.sh" "$IMG" "$dir") > "$root/run" 2>&1; rc=$?
}

printf tested > "$st/testing"; rm -f "$st/latest"
promote X=1
check "tested image, no latest yet: copied, changed=true, latest verified after the retag" bash -c "[ $rc = 0 ] && [ \$(grep -c '^skopeo copy' '$LOG') = 1 ] && grep -qx 'changed=true' '$root/gho' && grep -q 'cosign verify --key cosign.pub $IMG:latest' '$LOG' && cmp -s '$st/testing' '$st/latest'"
promote X=1
check "latest already is the tested image: nothing copied, changed=false" bash -c "[ $rc = 0 ] && [ \$(grep -c '^skopeo copy' '$LOG') = 0 ] && grep -qx 'changed=false' '$root/gho'"
printf older > "$st/latest"
promote X=1
check "latest holds older content: it moves to the tested digest" bash -c "[ $rc = 0 ] && [ \$(grep -c '^skopeo copy' '$LOG') = 1 ] && grep -qx 'changed=true' '$root/gho' && cmp -s '$st/testing' '$st/latest'"

printf older > "$st/latest"
promote COSIGN_RC=1
check "testing is not signed: fails, nothing copied, latest untouched" bash -c "[ $rc != 0 ] && [ \$(grep -c '^skopeo copy' '$LOG') = 0 ] && [ \"\$(cat '$st/latest')\" = older ]"
echo newer-docs > "$repo/README"; git -C "$repo" commit -q -am docs
promote X=1
check "only docs changed since the tested build (src/ equal): promotes" test "$rc" = 0
printf older > "$st/latest"
echo two > "$repo/src/f"; git -C "$repo" commit -q -am two
promote X=1
check "testing was built from another src/ tree: fails loudly, nothing copied" bash -c "[ $rc != 0 ] && grep -q 'testing moved on' '$root/run' && [ \$(grep -c '^skopeo copy' '$LOG') = 0 ]"
rm -f "$st/testing"
promote X=1
check "no testing image at all: fails, nothing copied, nothing is built instead" bash -c "[ $rc != 0 ] && grep -q 'build on testing first' '$root/run' && [ \$(grep -c '^skopeo copy' '$LOG') = 0 ]"
# the dx image: its trees are dx/, src/lib, src/builder.env and installer/, so a change of the base's own files is not a change of dx
dir=dx; printf tested > "$st/testing"; printf older > "$st/latest"
promote X=1
check "dx: src/ changed since the tested build but dx/, src/lib and the builder did not: dx promotes" bash -c "[ $rc = 0 ] && grep -qx 'changed=true' '$root/gho'"
printf older > "$st/latest"; echo l2 > "$repo/src/lib/x"; git -C "$repo" commit -q -am lib
promote X=1
check "dx: the shared src/lib changed: fails, nothing copied" bash -c "[ $rc != 0 ] && grep -q 'testing moved on' '$root/run' && [ \$(grep -c '^skopeo copy' '$LOG') = 0 ]"
dir=src; promote X=1
check "base: the shared src/lib changed, so src/ differs too: fails" test "$rc" != 0
echo; echo "passed: $pass, failed: $failn"
[ "$failn" = 0 ]
