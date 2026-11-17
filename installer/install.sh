#!/usr/bin/env bash
# The ONE package installer of octarine-bins. Every image carries it as /packages/install.sh next to its lists; the consumer (octarine-os) calls it once and it
# installs the two tiers in this order:
#
#   required-fedora.txt  required-negativo17.txt  required-copr.txt     STRICT: one dnf5 transaction per file, any failure fails the build
#   optional-fedora.txt  optional-negativo17.txt  optional-copr.txt     BEST EFFORT: never fails the build, what cannot be installed is reported
#
# Missing list files are skipped (an image carries the ones it needs). The file name says the tier and the source:
#   fedora      Fedora's own repositories
#   negativo17  negativo17's repository (id fedora-multimedia), enabled for that call only (the consumer must have it configured)
#   copr        entries are `owner/project:name-version-release` (an exact version and release); per entry: `dnf5 -y copr enable`, one install, `copr disable` (always)
# Entries of the other files: one token per line, blank lines and #-comments ignored; `name`, `name.arch`, `@group-id`, or `a|b|c` alternatives (the FIRST one that exists
# in the enabled repositories is installed; none existing fails a required file and reports an optional one).
#
# An optional fedora/negativo17 file is ONE transaction; when it fails it is retried once as a whole (after RETRY_SLEEP seconds and a metadata refresh, so a mirror
# hiccup does not cost the image its packages), then package by package. A package that still cannot be installed is printed as `OPTIONAL PACKAGE MISSING: <name>` and
# appended to MISSING_FILE. A required COPR entry that cannot be installed, or enabled, fails the build; an optional one is reported the same way.
#
#   bash /packages/install.sh            (needs dnf5 and root; `dnf5-plugins` for copr lists; works from a bind mount of the image)
#
# Environment:
#   MISSING_FILE=<path>   append the names of optional packages that could not be installed, one per line. The file is created when absent and never truncated
#                         (the consumer empties it once, so several calls add up). Unset: the report is only printed.
#   EXCLUDE="a b"         drop entries (a name, an alternative of an entry, an @group); printed. An excluded entry is not looked up.
#   ENABLE_REPO="id ..."  more repositories to enable (normally disabled) for every call
#   NO_CLEANUP=1          skip the dnf cache cleanup at the end (CLEANUP_ROOT=<dir> prefixes its paths: tests only)
#   RETRY_SLEEP=<s>       wait before the whole-group retry (default 20; tests)
#   PACKAGES_DIR=<dir>    where the lists are (default: next to this script)
set -euo pipefail

dir=${PACKAGES_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}
negativo17_repo=fedora-multimedia

# Log groups: GitHub Actions folds "::group::" ... "::endgroup::" (plain text elsewhere), never nested. A failure closes the open group and prints an "::error::" line
# outside it so it is not folded away.
trap 'rc=$?; if (( rc != 0 )); then echo "::endgroup::"; echo "::error::install.sh failed (exit ${rc})"; fi' EXIT

declare -A excluded=()
for x in ${EXCLUDE:-}; do excluded["${x}"]=1; done
extra_repos=()
for r in ${ENABLE_REPO:-}; do extra_repos+=("--enable-repo=${r}"); done
if [[ -n "${MISSING_FILE:-}" ]]; then mkdir -p "$(dirname "${MISSING_FILE}")"; touch "${MISSING_FILE}"; fi

die() { echo "install.sh: $*" >&2; exit 1; }

missing() { # <name> [why]: one optional package that could not be installed
    echo "OPTIONAL PACKAGE MISSING: $1${2:+ ($2)}"
    if [[ -n "${MISSING_FILE:-}" ]]; then echo "$1" >> "${MISSING_FILE}"; fi
}

# true when the package exists in the enabled repositories (exact name match)
exists() { # <name> <repo arg>...
    local n=$1 out; shift
    out=$(dnf5 -q repoquery "$@" --available --latest-limit=1 --queryformat '%{name}\n' "${n}") || return 1
    grep -Fxq -- "${n}" <<<"${out}"
}

# entries <file>: the file's tokens, comments and blanks dropped
entries() { sed -e 's/[[:space:]]*#.*$//' -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' -e '/^$/d' "$1"; }

# resolve <tier> <repo arg>...  (entries on stdin): applies EXCLUDE and the alternatives; prints the package arguments to install. A required entry with no
# existing alternative fails; an optional one is reported as missing.
resolve() {
    local tier=$1 entry a pick skip; shift
    local alts=()
    while IFS= read -r entry; do
        [[ ! "${entry}" =~ [[:space:]] ]] || die "more than one token on a line: '${entry}'"
        IFS='|' read -r -a alts <<<"${entry}"
        skip=""
        for a in "${alts[@]}"; do [[ -z "${excluded[${a}]:-}" ]] || { skip=${a}; break; }; done
        if [[ -n "${skip}" ]]; then echo "excluded: ${entry} (EXCLUDE has ${skip})" >&2; continue; fi
        if [[ "${#alts[@]}" -eq 1 ]]; then echo "${entry}"; continue; fi
        for a in "${alts[@]}"; do
            [[ -n "${a}" && "${a}" != @* ]] || die "bad alternative in '${entry}' (empty, or a group: groups cannot have alternatives)"
        done
        pick=""
        for a in "${alts[@]}"; do if exists "${a}" "$@"; then pick=${a}; break; fi; done
        if [[ -n "${pick}" ]]; then
            echo "resolved: ${entry} -> ${pick}" >&2
            echo "${pick}"
        elif [[ "${tier}" == required ]]; then
            die "none of the alternatives exists in the enabled repositories: ${entry}"
        else
            missing "${entry}" "no alternative exists" >&2
        fi
    done
}

# dnf_install <tier> <repo arg>... -- <package>...
dnf_install() {
    local tier=$1; shift
    local args=()
    while [[ "$1" != -- ]]; do args+=("$1"); shift; done; shift
    if [[ "${tier}" == required ]]; then dnf5 -y install --setopt=strict=1 "${args[@]}" "$@"; else dnf5 -y install "${args[@]}" "$@"; fi
}

# optional <repo arg>... -- <package>...: one transaction; on failure once more as a whole, then one by one
optional() {
    local args=()
    while [[ "$1" != -- ]]; do args+=("$1"); shift; done; shift
    local pkg
    [[ $# -gt 0 ]] || return 0
    if dnf_install optional "${args[@]}" -- "$@"; then return 0; fi
    echo "WARNING: the optional file failed, retrying it once as a whole"
    sleep "${RETRY_SLEEP:-20}"
    dnf5 clean expire-cache > /dev/null 2>&1 || true
    if dnf_install optional "${args[@]}" -- "$@"; then return 0; fi
    echo "WARNING: the optional file failed as a whole twice, retrying its packages one by one"
    for pkg in "$@"; do
        dnf_install optional "${args[@]}" -- "${pkg}" || missing "${pkg}"
    done
}

copr() { # <tier> <file>
    local tier=$1 file=$2 entry repo pkg
    while IFS= read -r entry; do
        [[ "${entry}" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+:[A-Za-z0-9_.+]+(-[A-Za-z0-9_.+]+)*-[0-9][A-Za-z0-9_.+~^]*-[0-9][A-Za-z0-9_.+~^]*$ ]] \
            || die "'${entry}' is not owner/project:name-version-release (an exact version and release is required)"
        repo=${entry%%:*}; pkg=${entry#*:}
        if [[ -n "${excluded[${pkg}]:-}" ]]; then echo "excluded: ${entry}"; continue; fi
        echo "COPR ${repo}: ${pkg}"
        if dnf5 -y copr enable "${repo}"; then
            if [[ "${tier}" == required ]]; then
                dnf5 -y install --setopt=strict=1 "${extra_repos[@]}" "${pkg}" || { dnf5 -y copr disable "${repo}" || true; die "could not install ${pkg} from the COPR ${repo}"; }
            else
                optional "${extra_repos[@]}" -- "${pkg}" || true
            fi
        else
            [[ "${tier}" != required ]] || die "could not enable the COPR ${repo} (is dnf5-plugins installed?)"
            missing "${pkg}" "COPR ${repo} could not be enabled"
        fi
        dnf5 -y copr disable "${repo}" || echo "install.sh: WARNING could not disable the COPR ${repo}" >&2
    done < <(entries "${file}")
}

for tier in required optional; do
    for source in fedora negativo17 copr; do
        file=${dir}/${tier}-${source}.txt
        [[ -f "${file}" ]] || continue
        [[ -n "$(entries "${file}")" ]] || { echo "${file} lists nothing, skipped"; continue; }
        echo "::group::Packages: ${tier} ${source}"
        repo_args=("${extra_repos[@]}")
        [[ "${source}" != negativo17 ]] || repo_args+=("--enable-repo=${negativo17_repo}")
        if [[ "${source}" == copr ]]; then
            copr "${tier}" "${file}"
        else
            # (a command substitution, not mapfile < <(...): a failing resolve must fail the build)
            resolved=$(entries "${file}" | resolve "${tier}" "${repo_args[@]}")
            pkgs=()
            [[ -z "${resolved}" ]] || mapfile -t pkgs <<<"${resolved}"
            echo "Installing ${#pkgs[@]} ${tier} entries from ${file}"
            if [[ "${tier}" == required ]]; then
                [[ "${#pkgs[@]}" -gt 0 ]] || die "nothing to install from ${file}"
                dnf_install required "${repo_args[@]}" -- "${pkgs[@]}"
            else
                optional "${repo_args[@]}" -- "${pkgs[@]}"
            fi
        fi
        echo "::endgroup::"
    done
done

if [[ -n "${MISSING_FILE:-}" ]]; then echo "== optional packages missing:"; cat "${MISSING_FILE}"; fi

if [[ "${NO_CLEANUP:-}" == "1" ]]; then
    echo "NO_CLEANUP=1: skipping the cleanup"
    exit 0
fi
dnf5 clean all
# Build residue that must not ship: dnf logs (they contain mirror URLs), root's ssh, vim, wget and cache leftovers, and the dnf caches
r=${CLEANUP_ROOT:-}
rm -rf "${r}"/var/cache/libdnf5 "${r}"/var/cache/dnf "${r}"/var/log/dnf5.log* "${r}"/var/log/dnf.log* \
    "${r}"/root/.ssh "${r}"/root/.viminfo "${r}"/root/.cache "${r}"/root/.local/share/wget \
    "${r}"/root/.wget-hsts 2>/dev/null || true
