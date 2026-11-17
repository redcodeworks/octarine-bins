#!/usr/bin/env bash
# The installer (installer/install.sh) read through the real package lists of the three images (src, dx, gx) with a stub dnf5 (no network, no root, no podman): each image's /packages tree
# laid out as its Containerfile does and installed from there the way octarine-os will, and the two tiers
# (strict required transactions; optional files that retry as a whole, then package by package, and report what is missing). The lint of the lists is tests/test_lint_packages.py. Run: tests/installer.sh
set -uo pipefail
here=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
root=$(mktemp -d)
trap 'rm -rf -- "$root"' EXIT
pass=0; failn=0
ok() { pass=$((pass + 1)); echo "ok   $1"; }
bad() { failn=$((failn + 1)); echo "FAIL $1"; sed 's/^/     /' "$root/out"; }
check() { local d=$1; shift; if "$@" > "$root/out" 2>&1; then ok "$d"; else bad "$d"; fi; }
# --- a stub dnf5: STUB_AVAILABLE is what the repositories have, STUB_LOG records the calls, FAIL_PKGS lists packages that never install, FLAKY_ONCE=1 fails the first
# multi-package transaction only, COPR_FAIL=1 makes `copr enable` fail
mkdir -p "$root/bin"
cat > "$root/bin/dnf5" <<'STUB'
#!/usr/bin/env bash
echo "dnf5 $*" >> "${STUB_LOG}"
case " $* " in
    *" copr enable "*) [[ -z "${COPR_FAIL:-}" ]]; exit $? ;;
    *" copr "*) exit 0 ;;
    *" repoquery "*) cat > /dev/null; n="${@: -1}"; grep -Fxq -- "$n" "${STUB_AVAILABLE}" && echo "$n"; exit 0 ;;
    *" install "*)
        pk=()
        for a in "$@"; do case "$a" in -* | install) ;; *) pk+=("$a") ;; esac; done
        for a in "${pk[@]}"; do
            case " ${FAIL_PKGS:-} " in *" $a "*) echo "No match for argument: $a" >&2; exit 1 ;; esac
            case "$a" in @*) ;; *) grep -Fxq -- "$a" "${STUB_AVAILABLE}" || { echo "No match for argument: $a" >&2; exit 1; } ;; esac
        done
        if [[ -n "${FLAKY_ONCE:-}" && "${#pk[@]}" -gt 1 ]]; then n=$(cat "${STUB_COUNT}" 2> /dev/null || echo 0); echo $((n + 1)) > "${STUB_COUNT}"; [[ "$n" -gt 0 ]] || exit 1; fi
        exit 0 ;;
    *" clean "*) exit 0 ;;
esac
exit 2
STUB
chmod +x "$root/bin/dnf5"
export STUB_LOG="$root/log" STUB_COUNT="$root/count" STUB_AVAILABLE="$root/available" PATH="$root/bin:$PATH" CLEANUP_ROOT="$root/root" RETRY_SLEEP=0
entries() { sed -e 's/[[:space:]]*#.*$//' -e '/^[[:space:]]*$/d' "$1"; }
# everything the lists name exists (alternatives: the first of them, except the old libgit2 name), COPR entries as name-version-release
avail_all() { for f in "$1"/packages/*.txt; do case "$f" in *-copr.txt) entries "$f" | sed 's/^[^:]*://' ;; *) entries "$f" | tr '|' '\n' | grep -v '^@' | grep -vx 'libgit2-devel' ;; esac; done > "$STUB_AVAILABLE"; }
# /packages as the Containerfile of an image lays it out: the image's lists plus the shared installer
mk_packages() { rm -rf "$root/img"; mkdir -p "$root/img/packages"; cp "$here/$1/packages/"* "$here"/installer/install.sh "$root/img/packages/"; p="$root/img/packages"; }
run() { : > "$STUB_LOG"; rm -f "$STUB_COUNT" "$root/missing"; env "$@" MISSING_FILE="$root/missing" bash "$p/install.sh" < /dev/null; }

# --- every image: the real lists, installed from /packages with everything available: exit 0, nothing missing, no call without its package list
for d in src dx gx; do
    mk_packages "$d"; avail_all "$d"
    run X=1 > "$root/out" 2>&1; rc=$?
    check "$d: /packages/install.sh installs the real lists (all available): exit 0, the missing file exists and is empty" bash -c "[ $rc = 0 ] && [ -f '$root/missing' ] && [ ! -s '$root/missing' ]"
done

# --- dx: required-fedora.txt is ONE strict call (groups passed through, first alternative of libgit2), optional-fedora.txt ONE non-strict call after it
mk_packages dx; avail_all dx
run X=1 > "$root/out" 2>&1
check "dx: two install calls, the strict one first; the first alternative of libgit2 and the groups in it; never --skip-unavailable" bash -c "[ \$(grep -c ' install ' '$STUB_LOG') = 2 ] && grep ' install ' '$STUB_LOG' | head -1 | grep -q 'strict=1 .*libgit2_1.9-devel' && grep ' install ' '$STUB_LOG' | head -1 | grep -q ' @c-development' && grep ' install ' '$STUB_LOG' | tail -1 | grep -qv strict && ! grep -q skip-unavailable '$STUB_LOG'"
grep -vx 'libgit2_1.9-devel' "$STUB_AVAILABLE" > "$root/a2"; echo libgit2-devel >> "$root/a2"; cp "$root/a2" "$STUB_AVAILABLE"
run X=1 > "$root/out" 2>&1
check "dx: on a repository with only the older libgit2 name the second alternative is installed" bash -c "grep ' install ' '$STUB_LOG' | head -1 | grep -q ' libgit2-devel' && ! grep ' install ' '$STUB_LOG' | grep -q 'libgit2_1.9-devel'"
avail_all dx
run EXCLUDE=gcc > "$root/out" 2>&1
check "dx: EXCLUDE drops an entry" bash -c "grep ' install ' '$STUB_LOG' | head -1 | tr ' ' '\n' > '$root/args' && grep -qx make '$root/args' && ! grep -qx gcc '$root/args'"
grep -vx 'gcc' "$STUB_AVAILABLE" > "$root/a2"; cp "$root/a2" "$STUB_AVAILABLE"
run X=1 > "$root/out" 2>&1; rc=$?
check "dx: a required package the repositories lack fails the install (strict) before any optional package is touched" bash -c "[ $rc != 0 ] && [ \$(grep -c ' install ' '$STUB_LOG') = 1 ]"

# --- gx: required-fedora, then required-negativo17 with the repository enabled for that call only, then the COPR sequence, then the optional list
mk_packages gx; avail_all gx
run X=1 > "$root/out" 2>&1
want="dnf5 -y install --setopt=strict=1 $(entries "$p/required-fedora.txt" | tr '\n' ' ' | sed 's/ $//')"$'\n'"dnf5 -y install --setopt=strict=1 --enable-repo=fedora-multimedia $(entries "$p/required-negativo17.txt" | tr '\n' ' ' | sed 's/ $//')"
check "gx: the first two install calls are the strict Fedora list and the strict negativo17 list (repository enabled for that call only), name.arch entries unchanged" test "$(grep ' install ' "$STUB_LOG" | head -2)" = "$want"
cline=$(entries "$p/required-copr.txt" | head -1); repo=${cline%%:*}; pkg=${cline#*:}
want="dnf5 -y copr enable ${repo}"$'\n'"dnf5 -y install --setopt=strict=1 ${pkg}"$'\n'"dnf5 -y copr disable ${repo}"
check "gx: the COPR sequence is enable, one strict install of the exact NVR, disable, after the two lists" test "$(grep -A2 'copr enable' "$STUB_LOG")" = "$want"
check "gx: the optional list comes last and is not strict" bash -c "grep ' install ' '$STUB_LOG' | tail -1 | grep -v strict | grep -q vkBasalt"
run FAIL_PKGS="$pkg" > "$root/out" 2>&1; rc=$?
check "gx: a failing required COPR install fails the build and the COPR is disabled again" bash -c "[ $rc != 0 ] && grep -qx 'dnf5 -y copr disable ${repo}' '$STUB_LOG'"
run COPR_FAIL=1 > "$root/out" 2>&1; rc=$?
check "gx: a required COPR that cannot be enabled fails the build" test "$rc" != 0

# --- the optional tier, on small fixture lists (PACKAGES_DIR): the ported test of build.sh's optional groups
mkdir -p "$root/o"; rm -f "$root/o"/*
printf 'fish\nniri\ngum\nwtype\n' > "$root/o/optional-fedora.txt"
printf 'core\n' > "$root/o/required-fedora.txt"
printf 'fish\nniri\ngum\nwtype\ncore\n' > "$STUB_AVAILABLE"
orun() { : > "$STUB_LOG"; rm -f "$STUB_COUNT"; env "$@" PACKAGES_DIR="$root/o" MISSING_FILE="$root/missing" NO_CLEANUP=1 bash "$here/installer/install.sh" > "$root/orun.out" 2>&1; rc=$?; }
rm -f "$root/missing"; orun FAIL_PKGS=gum
check "optional: a package that never installs is reported and written to the missing file, the others are installed one by one, the build does not fail" bash -c "[ $rc = 0 ] && [ \"\$(cat '$root/missing')\" = gum ] && grep -q 'OPTIONAL PACKAGE MISSING: gum' '$root/orun.out' && grep -q ' install niri\$' '$STUB_LOG'"
orun FLAKY_ONCE=1
check "optional: a transaction that fails once is retried as a whole (two whole calls, no one-by-one call)" bash -c "[ $rc = 0 ] && [ \"\$(grep -c ' install fish niri' '$STUB_LOG')\" = 2 ] && ! grep -q ' install niri\$' '$STUB_LOG'"
rm -f "$root/missing"; orun FLAKY_ONCE=1
check "optional: the whole-group retry leaves the missing file empty" bash -c "[ $rc = 0 ] && [ ! -s '$root/missing' ]"
printf 'old\n' > "$root/missing"; orun FAIL_PKGS="gum wtype"
check "optional: the missing file is appended to, never truncated (several calls add up)" bash -c "[ \"\$(cat '$root/missing')\" = \"\$(printf 'old\\ngum\\nwtype')\" ]"
orun FAIL_PKGS=core
check "optional: a failing REQUIRED package still fails the build" test "$rc" != 0
printf 'nothere|alsonot\n' >> "$root/o/optional-fedora.txt"; rm -f "$root/missing"; orun X=1
check "optional: an entry whose alternatives all lack is reported as missing, not a failure" bash -c "[ $rc = 0 ] && grep -qx 'nothere|alsonot' '$root/missing'"
printf 'nothere|alsonot\n' >> "$root/o/required-fedora.txt"; orun X=1
check "required: an entry whose alternatives all lack fails the build" test "$rc" != 0
rm -f "$root/o"/*; printf 'o/p:Pkg-1.0-1.fc45\n' > "$root/o/optional-copr.txt"; echo Pkg-1.0-1.fc45 > "$STUB_AVAILABLE"
rm -f "$root/missing"; orun COPR_FAIL=1
check "optional COPR: a COPR that cannot be enabled is reported as missing, the build does not fail, the COPR is still disabled" bash -c "[ $rc = 0 ] && grep -qx 'Pkg-1.0-1.fc45' '$root/missing' && grep -qx 'dnf5 -y copr disable o/p' '$STUB_LOG'"

# --- an `a|b` line in the MIDDLE of a list must not swallow the entries after it (the real dnf5 reads stdin; the stub does too): all of them reach the install call
rm -rf "$root/img"; mkdir -p "$root/img/packages"; cp "$here"/installer/install.sh "$root/img/packages/"; p="$root/img/packages"
printf 'alpha\nbeta-old|beta\ngamma\ndelta\nepsilon\n' > "$p/required-fedora.txt"
printf 'alpha\nbeta\ngamma\ndelta\nepsilon\n' > "$STUB_AVAILABLE"
: > "$STUB_LOG"; env MISSING_FILE="$root/missing" bash "$p/install.sh" > "$root/out" 2>&1 < /dev/null; rc=$?
check "an alternatives line in the middle of a list: every later entry is installed" bash -c "[ $rc = 0 ] && grep ' install ' '$STUB_LOG' | head -1 | tr ' ' '\n' | tr '\n' ' ' | grep -q ' alpha beta gamma delta epsilon '"
echo; echo "passed: $pass, failed: $failn"
[ "$failn" = 0 ]
