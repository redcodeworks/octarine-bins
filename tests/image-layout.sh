#!/usr/bin/env bash
# The writer-to-reader test of the image layout: the REAL assembler (src/lib/assemble.sh, the `assemble` stage of src/Containerfile) writes COMPONENTS and the combined
# LICENSES/SOURCE/BUILDINFO around fake component directories, and the layout check (.github/scripts/binaries-layout.sh, run on every built image) reads the listing of the
# result with the component list of the real src/components.tsv and the real scheduler manifest. The layout is what octarine-os's installer reads. Run: tests/image-layout.sh
set -uo pipefail
here=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
root=$(mktemp -d)
trap 'rm -rf -- "$root"' EXIT
pass=0; failn=0; rc=0
ok() { pass=$((pass + 1)); echo "ok   $1"; }
bad() { failn=$((failn + 1)); echo "FAIL $1"; sed 's/^/     run: /' "$root/run" 2> /dev/null; echo "     rc=$rc"; }
check() { local d=$1; shift; if "$@" > "$root/out" 2>&1; then ok "$d"; else bad "$d"; fi; }

tsv="$here/src/scx/schedulers.tsv"
comps=$(grep -vE '^[[:space:]]*(#|$)' "$here/src/components.tsv" | cut -f1)
img="$root/img"
mk_image() { # a component tree as the build stages leave it, assembled by the real assemble.sh
    rm -rf "$img"; mkdir -p "$img/scx/bin" "$img/scx/LICENSES" "$img/extest/lib" "$img/extest/lib64" "$img/extest/LICENSES" "$img/ghostty/root/usr/bin" "$img/ghostty/root/usr/share/applications" "$img/ghostty/LICENSES"
    for n in $(grep -vE '^[[:space:]]*(#|$)' "$tsv" | cut -f1); do printf '#!/bin/sh\n' > "$img/scx/bin/$n"; done
    cp "$tsv" "$img/scx/schedulers.tsv"
    echo 32 > "$img/extest/lib/libextest.so"; echo 64 > "$img/extest/lib64/libextest.so"
    : > "$img/ghostty/root/usr/bin/ghostty"; : > "$img/ghostty/root/usr/share/applications/com.mitchellh.ghostty.desktop"
    printf '%s\n' usr/bin/ghostty usr/share/applications/com.mitchellh.ghostty.desktop > "$img/ghostty/FILES"
    for c in scx extest ghostty; do echo "$c-license" > "$img/$c/LICENSES/$c-LICENSE"; echo src > "$img/$c/SOURCE"; echo build > "$img/$c/BUILDINFO"; done
    bash "$here/src/lib/assemble.sh" "$img" "$here/src/components.tsv" > /dev/null
}
layout() { (cd "$img" && find . -mindepth 1 | sed 's#^\./##' | sort) > "$root/listing"; bash "$here/.github/scripts/binaries-layout.sh" --assembled "$root/listing" "$tsv" $comps > "$root/run" 2>&1; rc=$?; }

mk_image; layout
check "assembled image: every component, COMPONENTS, top-level LICENSES/SOURCE/BUILDINFO pass, for all schedulers of the real manifest" test "$rc" = 0
check "COMPONENTS names the components one per line, as components.tsv lists them" bash -c "[ \"\$(cat '$img/COMPONENTS')\" = \"$comps\" ]"
mk_image; rm "$img/scx/bin/scx_cake"; layout
check "a scheduler of the manifest missing from the image fails and is named" bash -c "[ $rc != 0 ] && grep -q 'lacks scx/bin/scx_cake' '$root/run'"
mk_image; rm "$img/COMPONENTS"; layout
check "an image without COMPONENTS fails" bash -c "[ $rc != 0 ] && grep -q 'lacks COMPONENTS' '$root/run'"
mk_image; mkdir "$img/stray"; echo x > "$img/stray/file"; layout
check "an unexpected top-level directory fails" bash -c "[ $rc != 0 ] && grep -q 'unexpected top-level' '$root/run'"
mk_image; for i in $(seq 1 6000); do echo x > "$img/ghostty/root/f$i"; done; layout
check "a long listing (6000 files) passes: no SIGPIPE of the normalizing sed under pipefail makes a present file look missing" test "$rc" = 0
# dx and gx carry no component: the real assembler with --allow-empty, read by the layout check with --allow-empty; the base never accepts that
empty_image() { rm -rf "$img"; mkdir -p "$img"; bash "$here/src/lib/assemble.sh" --allow-empty "$img" "$here/dx/components.tsv" > /dev/null; }
elayout() { (cd "$img" && find . -mindepth 1 | sed 's#^\./##' | sort) > "$root/listing"; local flag=$1; shift; bash "$here/.github/scripts/binaries-layout.sh" --assembled "$flag" "$root/listing" "$tsv" "$@" > "$root/run" 2>&1; rc=$?; }
empty_image; elayout --allow-empty
check "an empty dx/gx image (no component, empty COMPONENTS, BUILDINFO) passes with --allow-empty" bash -c "[ $rc = 0 ] && [ ! -s '$img/COMPONENTS' ]"
empty_image; elayout --assembled
check "the same image without --allow-empty (the base) fails" test "$rc" != 0
empty_image; rm "$img/BUILDINFO"; elayout --allow-empty
check "an empty image without BUILDINFO fails" bash -c "[ $rc != 0 ] && grep -q 'lacks BUILDINFO' '$root/run'"
check "assemble.sh without --allow-empty still refuses an empty component list (the base)" bash -c "! bash '$here/src/lib/assemble.sh' '$img' '$here/dx/components.tsv'"
mk_image; rm -rf "$img/extest" "$img/ghostty"; elayout --allow-empty scx extest ghostty
check "an incomplete base (extest and ghostty missing) fails even with --allow-empty" bash -c "[ $rc != 0 ] && grep -q 'lacks extest/lib/libextest.so' '$root/run'"
# /packages (every image): the packages tree as the Containerfile lays it out (the image's lists plus the shared installer), read by the layout check with the file list `just layout`
# builds. A missing list or installer fails, and an image without --packages (a layout check that does not expect /packages) rejects the directory.
pimage() { if [[ "$1" == src ]]; then mk_image; else empty_image; fi; mkdir -p "$img/packages"; cp "$here/$1/packages/"* "$here"/installer/install.sh "$img/packages/"; }
players() { (cd "$img" && find . -mindepth 1 | sed 's#^\./##' | sort) > "$root/listing"; local d=$1; shift; bash "$here/.github/scripts/binaries-layout.sh" --assembled "$@" --packages "$(ls "$here/$d/packages" | tr '\n' ' ')install.sh" "$root/listing" "$tsv" $comps > "$root/run" 2>&1; rc=$?; }
pimage src; players src; check "base image with /packages (its lists and install.sh) passes" test "$rc" = 0
comps_save=$comps; comps=""
for d in dx gx; do pimage $d; players $d --allow-empty; check "$d image with /packages (its lists and install.sh) passes" test "$rc" = 0; done
pimage gx; rm "$img/packages/required-negativo17.txt"; players gx --allow-empty
check "a missing list in /packages fails and is named" bash -c "[ $rc != 0 ] && grep -q 'lacks packages/required-negativo17.txt' '$root/run'"
pimage dx; rm "$img/packages/install.sh"; players dx --allow-empty
check "a missing installer in /packages fails" bash -c "[ $rc != 0 ] && grep -q 'lacks packages/install.sh' '$root/run'"
pimage dx; elayout --allow-empty
check "a /packages directory without --packages is an unexpected top-level entry" bash -c "[ $rc != 0 ] && grep -q 'unexpected top-level' '$root/run'"
comps=$comps_save
echo; echo "passed: $pass, failed: $failn"
[ "$failn" = 0 ]
