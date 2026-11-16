#!/usr/bin/env bash
# The weekly bump check of ONE pin: decide (the decision scripts of its kind) and, when something is newer, write the pin into the working tree and make ONE
# commit for it (its message ends with the row of the pull request's table: pin | from | to | what changed upstream | days the new value has existed).
# bump-weekly.sh runs every pin one after the other on one branch and opens the one pull request. Exits non-zero when the pin's check FAILED (a registry that
# cannot be reached, a signature that does not verify, a bad pin); the working tree is restored then. Prints one line per pin (also into the job summary).
# Run from the repository root on a clean checkout, with GH_TOKEN set, a git identity, and skopeo for the builder kind.
#
#   bump-run.sh scx|extest|ghostty|builder
#
# Kinds: source pins (upstream-check.sh and ghostty-check.sh; bump-ghostty.sh writes the Ghostty and Zig pins together) and the Fedora builder digest of src/builder.env
# (the same major, the newest digest of the tag once it has been the tag's value for MIN_AGE_DAYS, default 7: the creation date of the image is the stand-in for "has not been replaced since", a registry has no tag history).
set -uo pipefail
pin=${1:?pin name}
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
current="-" proposed="-" status="nothing to bump" files=()
say() { echo "$*"; [[ -z "${GITHUB_STEP_SUMMARY:-}" ]] || echo "- $*" >> "${GITHUB_STEP_SUMMARY}" || true; }
finish() {
    local rc=$?
    if [[ "${rc}" != 0 ]]; then git checkout -q -- . 2> /dev/null; say "\`${pin}\`: FAILED, ${status}"; else say "\`${pin}\`: ${status}"; fi
    exit "${rc}"
}
trap finish EXIT
fail() { echo "::error::${pin}: $*"; status="$*"; exit 1; }
days() { # an ISO date -> whole days since then, or "unknown"
    local t; [[ -n "${1:-}" ]] && t=$(date -u -d "$1" +%s 2> /dev/null) || { echo unknown; return; }
    echo $(( ($(date -u +%s) - t) / 86400 ))
}
commit_age() { days "$(gh api "repos/$1/commits/$2" --jq .commit.committer.date 2> /dev/null)"; }
# commit_pin <subject> <what changed upstream: a link or an image> <days>: the pin was written; check that only its files changed, commit them
commit_pin() {
    local changed; changed=$(git diff --name-only)
    [[ -n "${changed}" ]] || fail "the writer changed nothing for ${proposed}"
    local f; for f in ${changed}; do printf '%s\n' "${files[@]}" | grep -qxF -- "${f}" || fail "the writer changed ${f}, which is not one of the pin files (${files[*]})"; done
    git commit -q -a -m "$1

${pin} | ${current} | ${proposed} | $2 | $3"
    status="moved ${current} to ${proposed}"
}
git diff --quiet || fail "the working tree is not clean"

case "${pin}" in
    scx | extest)
        files=("src/${pin}/upstream.env")
        # shellcheck source=../../src/lib/upstream-env.sh
        source src/lib/upstream-env.sh; upstream_env_load "${files[0]}" || fail "invalid ${files[0]}"
        current=${UPSTREAM_TAG:-${UPSTREAM_COMMIT:0:7}}
        decision=$("${here}/upstream-check.sh" "src/${pin}")
        echo "${pin}: ${decision}"
        read -r verb tag commit _ <<<"${decision}"
        case "${verb}" in
            bump)
                proposed=${tag}; old=${UPSTREAM_TAG:-${UPSTREAM_COMMIT}}; upstream_env_set "${files[0]}" "${tag}" "${commit}" || fail "cannot write ${files[0]}"
                commit_pin "Bump ${pin} to ${tag}" "https://github.com/${UPSTREAM_WATCH}/compare/${old}...${tag}" "$(commit_age "${UPSTREAM_WATCH}" "${commit}")" ;;
            forkbehind) status="the fork is behind upstream (${tag}): run gh repo sync on it first" ;;
            unreachable) fail "${decision#unreachable }" ;;
            *) status="nothing to bump: ${decision}" ;;
        esac ;;
    ghostty)
        files=(src/ghostty/upstream.env src/ghostty/zig.env)
        source src/lib/upstream-env.sh; upstream_env_load "${files[0]}" || fail "invalid upstream.env"
        current="${UPSTREAM_TAG}, zig $(sed -n 's/^ZIG_VERSION=//p' "${files[1]}")"
        decision=$("${here}/ghostty-check.sh")
        echo "ghostty: ${decision}"
        read -r verb a1 a2 a3 a4 a5 a6 <<<"${decision}"
        case "${verb}" in
            bump) # a new Ghostty tag brings the Zig minor it needs: one commit for both, they cannot be applied apart
                proposed="${a1}, zig ${a3}"; "${here}/bump-ghostty.sh" ghostty "${a1}" "${a2}" "${a3}" "${a4}" "${a5}" "${a6}" || fail "bump-ghostty.sh failed"
                commit_pin "Bump ghostty to ${a1} (Zig ${a3})" "https://github.com/${UPSTREAM_WATCH}/compare/${UPSTREAM_TAG}...${a1}" "$(commit_age "${UPSTREAM_WATCH}" "${a2}")" ;;
            wait) status="nothing to bump yet: ${decision#wait }" ;;
            unreachable) fail "${decision#unreachable }" ;;
            *) status="nothing to bump: ${decision}" ;;
        esac ;;
    builder)
        files=(src/builder.env)
        cur=$(sed -n 's/^BUILDER=//p' "${files[0]}")
        [[ "${cur}" =~ ^(registry\.fedoraproject\.org/fedora):([0-9]+)@(sha256:[0-9a-f]{64})$ ]] || fail "src/builder.env has no digest-pinned BUILDER"
        repo=${BASH_REMATCH[1]} major=${BASH_REMATCH[2]} curdigest=${BASH_REMATCH[3]}
        current="${major}@${curdigest:0:19}"
        manifest=$(skopeo inspect --raw "docker://${repo}:${major}" 2> /dev/null) || manifest=""
        [[ -n "${manifest}" ]] || fail "cannot resolve ${repo}:${major}"
        new="sha256:$(printf '%s' "${manifest}" | sha256sum | sed 's/ .*//')"
        [[ "${new}" =~ ^sha256:[0-9a-f]{64}$ ]] || fail "cannot compute the digest of ${repo}:${major}"
        if [[ "${new}" == "${curdigest}" ]]; then status="nothing to bump: the pin is the current digest of ${major}"
        else
            created=$(skopeo inspect --override-os linux --override-arch amd64 --config "docker://${repo}:${major}" 2> /dev/null | jq -r '.created // empty') || created=""
            [[ -n "${created}" ]] || fail "cannot read the creation date of ${repo}:${major}"
            age=$(days "${created}"); min=${MIN_AGE_DAYS:-7}
            if [[ "${age}" -lt "${min}" ]]; then status="nothing to bump yet: ${new:0:19} was built ${age} days ago (${created}); a digest is proposed once it has been the tag's value for ${min} days"
            else
                proposed="${major}@${new:0:19}"
                sed -i "s|^BUILDER=.*|BUILDER=${repo}:${major}@${new}|" "${files[0]}"
                commit_pin "Bump the Fedora ${major} builder digest (built ${created%%T*})" "${repo}:${major}" "${age}"
            fi
        fi ;;
    *) fail "unknown pin '${pin}'" ;;
esac
exit 0
