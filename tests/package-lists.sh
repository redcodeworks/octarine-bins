#!/usr/bin/env bash
# The package lists of the dx and gx images, read by the real installers (installer/install.sh, install-copr.sh) with a stub dnf5 (no network, no root, no podman):
# the lint rules on the real lists (and on bad fixtures), the image's /packages tree laid out as the Containerfile does and installed from there the way octarine-os will,
# and the installer behaviors the lists rely on (alternatives, EXCLUDE, ENABLE_REPO, strictness, COPR order). Run: tests/package-lists.sh
set -uo pipefail
here=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
root=$(mktemp -d)
trap 'rm -rf -- "$root"' EXIT
pass=0; failn=0
ok() { pass=$((pass + 1)); echo "ok   $1"; }
bad() { failn=$((failn + 1)); echo "FAIL $1"; }
check() { local d=$1; shift; if "$@" > "$root/out" 2>&1; then ok "$d"; else bad "$d"; sed 's/^/     /' "$root/out"; fi; }
lint="$here/.github/scripts/lint-packages.sh"

# --- lint: the real lists pass, the rules catch what they are for
check "the real lists pass the lint" "$lint" "$here"/dx/packages/*.txt "$here"/gx/packages/*.txt
printf 'mangohud.i686\n' > "$root/fedora.txt"
check "an .i686 entry without an explicit .x86_64 entry fails" bash -c "! '$lint' '$root/fedora.txt'"
printf 'mangohud.i686\nmangohud.x86_64\n' > "$root/fedora.txt"
check "an .i686 entry with its .x86_64 sibling passes" "$lint" "$root/fedora.txt"
printf 'owner/project:Pkg\n' > "$root/copr.txt"
check "a COPR entry without version and release fails" bash -c "! '$lint' '$root/copr.txt'"
printf 'gcc\ngcc\n' > "$root/fedora.txt"
check "a duplicate fails" bash -c "! '$lint' '$root/fedora.txt'"

# --- a stub dnf5: STUB_AVAILABLE is what the repositories have, STUB_LOG records the calls, STUB_FAIL an install argument that fails
mkdir -p "$root/bin"
cat > "$root/bin/dnf5" <<'STUB'
#!/usr/bin/env bash
echo "dnf5 $*" >> "${STUB_LOG}"
case " $* " in
    *" copr "*) exit 0 ;;
    *" repoquery "*) n="${@: -1}"; grep -Fxq -- "$n" "${STUB_AVAILABLE}" && echo "$n"; exit 0 ;;
    *" install "*)
        for a in "$@"; do
            [[ "$a" == "${STUB_FAIL:-@none@}" ]] && { echo "No match for argument: $a" >&2; exit 1; }
            case "$a" in -* | install | copr) ;; @*) ;; *) grep -Fxq -- "$a" "${STUB_AVAILABLE}" || { echo "No match for argument: $a" >&2; exit 1; } ;; esac
        done
        exit 0 ;;
    *" clean "*) exit 0 ;;
esac
exit 2
STUB
chmod +x "$root/bin/dnf5"
export STUB_LOG="$root/log" STUB_AVAILABLE="$root/available" PATH="$root/bin:$PATH" CLEANUP_ROOT="$root/root"
entries() { sed -e 's/[[:space:]]*#.*$//' -e '/^[[:space:]]*$/d' "$1"; }

# /packages as the Containerfile of an image lays it out: the image's lists plus the shared installers
mk_packages() { rm -rf "$root/img"; mkdir -p "$root/img/packages"; cp "$here/$1/packages/"* "$here"/installer/*.sh "$root/img/packages/"; }

# --- dx: install.sh with no argument installs dev.txt, strictly, in one call
mk_packages dx
# everything exists except the Fedora 44 name of libgit2: the first alternative wins on Fedora 45
entries "$root/img/packages/dev.txt" | tr '|' '\n' | grep -v '^@' | grep -vx 'libgit2-devel' > "$STUB_AVAILABLE"
: > "$STUB_LOG"
check "dx: /packages/install.sh installs dev.txt (the default list) on a Fedora 45 like repository" bash "$root/img/packages/install.sh"
check "dx: one strict install call, the first alternative of libgit2 chosen, groups passed through" bash -c "grep -c ' install ' '$STUB_LOG' | grep -qx 1 && grep -q 'install --setopt=strict=1 .*libgit2_1.9-devel' '$STUB_LOG' && grep -q ' @c-development' '$STUB_LOG' && ! grep -q 'skip-unavailable' '$STUB_LOG'"
grep -vx 'libgit2_1.9-devel' "$STUB_AVAILABLE" > "$root/a2"; echo libgit2-devel >> "$root/a2"; cp "$root/a2" "$STUB_AVAILABLE"
: > "$STUB_LOG"
check "dx: on a repository with only the older libgit2 name the second alternative is installed" bash -c "bash '$root/img/packages/install.sh' && grep ' install ' '$STUB_LOG' | grep -q 'libgit2-devel' && ! grep ' install ' '$STUB_LOG' | grep -q 'libgit2_1.9-devel'"
: > "$STUB_LOG"
check "dx: EXCLUDE drops an entry" bash -c "EXCLUDE=gcc bash '$root/img/packages/install.sh' && grep ' install ' '$STUB_LOG' | tr ' ' '\\n' > '$root/args' && grep -qx make '$root/args' && ! grep -qx gcc '$root/args'"
: > "$STUB_LOG"; echo gcc > "$root/avail1"; STUB_AVAILABLE="$root/avail1" bash "$root/img/packages/install.sh" > /dev/null 2>&1
check "dx: a package the repositories lack fails the install (strict, nothing skipped)" test "$?" != 0

# --- gx: fedora.txt, then negativo17.txt with the repository enabled for that call, then copr.txt
mk_packages gx
p="$root/img/packages"
{ entries "$p/fedora.txt"; entries "$p/negativo17.txt"; entries "$p/copr.txt" | sed 's/^[^:]*://'; } > "$STUB_AVAILABLE"
: > "$STUB_LOG"
check "gx: fedora.txt, then negativo17.txt (ENABLE_REPO=fedora-multimedia), each from /packages with the installer next to the list" bash -c "NO_CLEANUP=1 bash '$p/install.sh' '$p/fedora.txt' && ENABLE_REPO=fedora-multimedia NO_CLEANUP=1 bash '$p/install.sh' '$p/negativo17.txt'"
want="dnf5 -y install --setopt=strict=1 $(entries "$p/fedora.txt" | tr '\n' ' ' | sed 's/ $//')"$'\n'"dnf5 -y install --setopt=strict=1 --enable-repo=fedora-multimedia $(entries "$p/negativo17.txt" | tr '\n' ' ' | sed 's/ $//')"
check "gx: exactly two strict dnf calls, name.arch entries unchanged, the negativo17 repository only on the second" test "$(grep ' install ' "$STUB_LOG")" = "$want"
: > "$STUB_LOG"
check "gx: install.sh with several lists next to it and no argument fails (the list must be named)" bash -c "! bash '$p/install.sh'"
: > "$STUB_LOG"
check "gx: install-copr.sh on copr.txt: enable, one strict install of the exact NVR, disable" bash -c "NO_CLEANUP=1 bash '$p/install-copr.sh' '$p/copr.txt'"
repo=$(entries "$p/copr.txt" | head -1); repo=${repo%%:*}; pkg=$(entries "$p/copr.txt" | head -1); pkg=${pkg#*:}
want=$'dnf5 copr --help\n'"dnf5 -y copr enable ${repo}"$'\n'"dnf5 -y install --setopt=strict=1 ${pkg}"$'\n'"dnf5 -y copr disable ${repo}"
check "gx: the COPR call sequence" test "$(cat "$STUB_LOG")" = "$want"
: > "$STUB_LOG"
STUB_FAIL=$pkg NO_CLEANUP=1 bash "$p/install-copr.sh" "$p/copr.txt" > /dev/null 2>&1; rc=$?
check "gx: a failing COPR install fails the call and the COPR is disabled again" bash -c "[ $rc != 0 ] && grep -qx 'dnf5 -y copr disable ${repo}' '$STUB_LOG'"
echo; echo "passed: $pass, failed: $failn"
[ "$failn" = 0 ]
