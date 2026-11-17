#!/usr/bin/env bash
# Lint the package lists of ONE image (<dir>/packages/*.txt): the rules installer/install.sh relies on. Prints every problem, exits 1 if there is one.
#
#   lint-packages.sh <list>...        the lists of one image together (duplicates are looked for across all of them)
#
# File names: {required,optional}-{fedora,negativo17,copr}.txt, nothing else. Every list: at least one entry, one token per line, no RPM Fusion. *-copr.txt:
# owner/project:name-version-release. The others: `name`, `name.arch`, `@group` or `a|b` alternatives, and every `name.i686` entry needs `name.x86_64` listed explicitly in
# the same file (a bare name next to name.i686 installs only the i686 package on a host). No entry twice, in one list or across the image's lists (a package in both tiers
# would be installed strictly AND reported as optional).
set -uo pipefail
status=0
err() { echo "lint-packages: $*" >&2; status=1; }
[[ $# -gt 0 ]] || { echo "usage: lint-packages.sh <list>..." >&2; exit 2; }
all=""
for list in "$@"; do
    [[ -f "${list}" ]] || { err "${list}: not a file"; continue; }
    name=$(basename "${list}")
    [[ "${name}" =~ ^(required|optional)-(fedora|negativo17|copr)\.txt$ ]] || { err "${list}: the name must be {required,optional}-{fedora,negativo17,copr}.txt"; continue; }
    entries=$(sed -e 's/[[:space:]]*#.*$//' -e '/^[[:space:]]*$/d' "${list}")
    [[ -n "${entries}" ]] || { err "${list}: no packages (delete an empty list)"; continue; }
    bad=$(grep -nE '[[:space:]]' <<< "${entries}" || true)
    [[ -z "${bad}" ]] || err "${list}: more than one token per line: ${bad}"
    if [[ "${name}" == *-copr.txt ]]; then
        bad=$(grep -vE '^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+:[A-Za-z0-9_.+]+(-[A-Za-z0-9_.+]+)*-[0-9][A-Za-z0-9_.+~^]*-[0-9][A-Za-z0-9_.+~^]*$' <<< "${entries}" || true)
        [[ -z "${bad}" ]] || err "${list}: not owner/project:name-version-release: ${bad}"
    else
        bad=$(grep -vE '^(@[A-Za-z0-9._+-]+|[A-Za-z0-9._+-]+(\|[A-Za-z0-9._+-]+)*)$' <<< "${entries}" || true)
        [[ -z "${bad}" ]] || err "${list}: not 'name', '@group' or 'a|b' alternatives: ${bad}"
        while read -r e; do
            grep -qxF "${e%.i686}.x86_64" <<< "${entries}" || err "${list}: ${e} needs ${e%.i686}.x86_64 listed explicitly"
        done < <(grep -E '\.i686$' <<< "${entries}" || true)
    fi
    ! grep -qi 'rpmfusion\|rpm fusion' <<< "${entries}" || err "${list}: RPM Fusion is not used (negativo17 and COPR only)"
    all+="${entries}"$'\n'
done
dups=$(sed '/^$/d' <<< "${all}" | sort | uniq -d)
[[ -z "${dups}" ]] || err "duplicates: ${dups//$'\n'/ }"
exit "${status}"
