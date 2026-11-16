#!/usr/bin/env bash
# The weekly pin bump: ONE pull request PER PIN, each independent. Every pin (scx, extest, ghostty with its Zig pin, builder) goes through bump-run.sh on a checkout of
# current testing; a pin that moved becomes ONE commit on its own branch bump/<pin>, cut from testing (not from another bump), with its own pull request into testing. The
# pins live in different files, so the pull requests never conflict: merge any subset in any order, close the rest. Per pin:
#   - nothing moved, no open bump PR: nothing happens;
#   - nothing moved, a bump PR is open (testing has the value now, or upstream went back): that PR is closed with a comment and its branch deleted;
#   - moved, no open PR: the branch is pushed and the PR opened;
#   - moved, a PR is open: the branch is rewritten from current testing (a forced push of this bot's own bump branch, guarded by --force-with-lease) and the PR's title and
#     body are updated; when the branch already holds the same tree, nothing is touched.
#   Each PR's checks are reported by bump-checks.sh (keyed by the commit, so parallel PRs do not collide). A failing pin (upstream unreachable) changes nothing for that pin,
#   leaves its open PR alone and turns the run red; the other pins still go in.
# DRY_RUN=1 (a manual run with dry_run on) does every decision and commit locally and prints what it would do (open, update, close), pushing and creating nothing.
# A real run needs HEAD to be origin/testing (the workflow checks testing out); a dry run starts from the checked-out ref. Needs git, gh with GH_TOKEN, GITHUB_REPOSITORY;
# BUMP_BASE (testing) and BUMP_PINS (a subset) for the tests and by hand.
set -uo pipefail
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
cd "${REPO_ROOT:-${here}/../..}" || exit 1
base="${BUMP_BASE:-testing}"
read -ra pins <<<"${BUMP_PINS:-scx extest ghostty builder}"
summary() { echo "$*"; [[ -z "${GITHUB_STEP_SUMMARY:-}" ]] || echo "$*" >> "${GITHUB_STEP_SUMMARY}" || true; }
dry=${DRY_RUN:-}

git fetch -q origin "${base}" || { echo "::error::cannot fetch ${base}"; exit 1; }
git fetch -q origin '+refs/heads/bump/*:refs/remotes/origin/bump/*' 2> /dev/null || true  # the bump branches of earlier runs, if any
if [[ -z "${dry}" && "$(git rev-parse HEAD)" != "$(git rev-parse "origin/${base}")" ]]; then
    echo "::error::a real run must start from origin/${base} (this checkout is not it); use dry_run to try another ref"; exit 1
fi
start=$(git rev-parse HEAD)

summary "## Pin bumps"
failed=0
note_for() { # pin -> what the owner should know about that kind of bump
    case "$1" in
        scx) echo "Check the scheduler READMEs when scx moves." ;;
        builder) echo "A builder bump changes the \`FROM\` of every stage: every component rebuilds once, with no layer cache hit." ;;
        ghostty) echo "Zig moves only inside this commit (a Ghostty tag needs its Zig minor), never on its own." ;;
        *) echo "" ;;
    esac
}
open_pr() { gh pr list --base "${base}" --head "$1" --state open --json number --jq '.[0].number // empty' 2> /dev/null; }

for p in "${pins[@]}"; do
    branch="bump/${p}"
    git checkout -q --detach "${start}" || { failed=1; continue; }
    "${here}/bump-run.sh" "${p}" || { failed=1; continue; }   # a failed pin: its open PR stays as it is
    existing=$(open_pr "${branch}")
    if [[ "$(git rev-parse HEAD)" == "${start}" ]]; then
        if [[ -z "${existing}" ]]; then continue; fi
        if [[ -n "${dry}" ]]; then summary "Dry run: would close #${existing} (${p} needs no bump any more)"; continue; fi
        gh pr close "${existing}" --delete-branch --comment "Nothing to bump for ${p} any more (testing has the value, or upstream is at it): closing." || echo "::warning::could not close #${existing}"
        summary "\`${p}\`: closed #${existing}, nothing to bump any more"
        continue
    fi
    sha=$(git rev-parse HEAD)
    row=$(git log -1 --format=%b "${sha}" | sed -n 1p)
    title="$(git log -1 --format=%s "${sha}")"; title=${title:0:140}
    body="One pin, one commit: ${row%% |*}. This pull request is independent of the other bump pull requests (they change other files): merge it, or close it, on its own.

| pin | from | to | what changed upstream | days the new value has existed |
|---|---|---|---|---|
| ${row} |

Verified: the decision script compared the pin with upstream and the pin writer changed only the pin files; \`test\` and \`secrets\` run on this commit. Not verified: nothing was built or booted here; the build on \`${base}\` after the merge is the first build with this pin. $(note_for "${p}") The owner merges this pull request.

Checks: this commit is tested by the \`Test\` workflow (dispatched on \`${base}\` for it, because a pull request opened with the workflow token starts no workflow itself) and its \`test\` and \`secrets\` results are reported here as check runs. If they are missing, run the bump workflow again (\`gh workflow run bump-pins.yml --ref ${base}\`): it updates this pull request.

🤖 Generated with [Claude Code](https://claude.com/claude-code)"
    if [[ -n "${dry}" ]]; then
        action="open a PR"; [[ -z "${existing}" ]] || action="update #${existing}"
        summary "Dry run: would ${action} for ${p} on branch ${branch}: ${title}"
        summary "${body}"
        continue
    fi
    remote_tip=$(git rev-parse -q --verify "refs/remotes/origin/${branch}" || true)
    if [[ -n "${existing}" && -n "${remote_tip}" && "$(git rev-parse "${sha}^{tree}")" == "$(git rev-parse "${remote_tip}^{tree}")" ]]; then
        summary "\`${p}\`: #${existing} is up to date"
        "${here}/bump-checks.sh" "${branch}" "${remote_tip}" || true
        continue
    fi
    git push -q --force-with-lease="refs/heads/${branch}:${remote_tip}" origin "${sha}:refs/heads/${branch}" || { echo "::error::cannot push ${branch}"; failed=1; continue; }
    if [[ -n "${existing}" ]]; then
        gh pr edit "${existing}" --title "${title}" --body "${body}" || { echo "::error::could not update #${existing}"; failed=1; continue; }
        summary "\`${p}\`: updated #${existing} (${title})"
    elif url=$(gh pr create --base "${base}" --head "${branch}" --title "${title}" --body "${body}"); then
        summary "\`${p}\`: opened ${url}"
    else
        echo "::error::Could not open the pull request for ${p}. Is Settings, Actions, General, Allow GitHub Actions to create and approve pull requests, on?"; failed=1; continue
    fi
    "${here}/bump-checks.sh" "${branch}" "${sha}" || true
done
exit "${failed}"
