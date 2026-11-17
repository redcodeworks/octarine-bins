#!/usr/bin/env bash
# Lint package lists (dx/packages/*.txt, gx/packages/*.txt): the rules the installers rely on. Prints every problem, exits 1 if there is one.
#
#   lint-packages.sh <list>...
#
# Every list: at least one entry, one token per line, no duplicates, no RPM Fusion. copr.txt: owner/project:name-version-release (as install-copr.sh demands). Every other list:
# `name`, `name.arch`, `@group` or `a|b` alternatives, and every `name.i686` entry needs `name.x86_64` listed explicitly (a bare name next to name.i686 installs only
# the i686 package on a host).
set -uo pipefail
status=0
err() { echo "lint-packages: $*" >&2; status=1; }
[[ $# -gt 0 ]] || { echo "usage: lint-packages.sh <list>..." >&2; exit 2; }
for list in "$@"; do
    [[ -f "${list}" ]] || { err "${list}: not a file"; continue; }
    entries=$(sed -e 's/[[:space:]]*#.*$//' -e '/^[[:space:]]*$/d' "${list}")
    [[ -n "${entries}" ]] || { err "${list}: no packages"; continue; }
    bad=$(grep -nE '[[:space:]]' <<< "${entries}" || true)
    [[ -z "${bad}" ]] || err "${list}: more than one token per line: ${bad}"
    if [[ "$(basename "${list}")" == copr.txt ]]; then
        bad=$(grep -vE '^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+:[A-Za-z0-9_.+]+(-[A-Za-z0-9_.+]+)*-[0-9][A-Za-z0-9_.+~^]*-[0-9][A-Za-z0-9_.+~^]*$' <<< "${entries}" || true)
        [[ -z "${bad}" ]] || err "${list}: not owner/project:name-version-release: ${bad}"
    else
        bad=$(grep -vE '^(@[A-Za-z0-9._+-]+|[A-Za-z0-9._+-]+(\|[A-Za-z0-9._+-]+)*)$' <<< "${entries}" || true)
        [[ -z "${bad}" ]] || err "${list}: not 'name', '@group' or 'a|b' alternatives: ${bad}"
        while read -r e; do
            grep -qxF "${e%.i686}.x86_64" <<< "${entries}" || err "${list}: ${e} needs ${e%.i686}.x86_64 listed explicitly"
        done < <(grep -E '\.i686$' <<< "${entries}" || true)
    fi
    dups=$(sort <<< "${entries}" | uniq -d)
    [[ -z "${dups}" ]] || err "${list}: duplicates: ${dups}"
    ! grep -qi 'rpmfusion\|rpm fusion' <<< "${entries}" || err "${list}: RPM Fusion is not used (negativo17 and COPR only)"
done
exit "${status}"
