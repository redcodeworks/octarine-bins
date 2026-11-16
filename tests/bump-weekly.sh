#!/usr/bin/env bash
# Tests for the orchestration of the weekly bump (.github/scripts/bump-weekly.sh): one independent branch and pull request per pin that moved, an existing PR of a pin is
# updated instead of duplicated, a stale one is closed, a dry run creates nothing. Real git repositories (a bare "origin"), the real script, stubs for bump-run.sh (the pin
# writer: edits src/<pin>.env and commits), bump-checks.sh and gh. Run: tests/bump-weekly.sh
set -uo pipefail
here=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
root=$(mktemp -d)
trap 'rm -rf -- "$root"' EXIT
pass=0; failn=0; rc=0
ok() { pass=$((pass + 1)); echo "ok   $1"; }
bad() { failn=$((failn + 1)); echo "FAIL $1"; sed 's/^/     run: /' "$root/run" 2> /dev/null; echo "     rc=$rc"; }
check() { local d=$1; shift; if "$@" > "$root/out" 2>&1; then ok "$d"; else bad "$d"; fi; }
export GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@t GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@t
git init -q --bare -b testing "$root/origin.git"
w="$root/w"; git init -q -b testing "$w"; git -C "$w" remote add origin "$root/origin.git"
mkdir -p "$w/src" "$w/.github/scripts"
for p in scx extest ghostty builder; do echo "$p=old" > "$w/src/$p.env"; done
cp "$here/.github/scripts/bump-weekly.sh" "$w/.github/scripts/"
cat > "$w/.github/scripts/bump-run.sh" <<'F'
#!/usr/bin/env bash
# stub pin writer: the pins named in MOVE change src/<pin>.env and get ONE commit whose body starts with the table row
[[ " ${MOVE:-} " == *" $1 "* ]] || exit 0
echo "$1=new-${NEW:-1}" > "src/$1.env"
git commit -q -a -m "Bump $1 to new-${NEW:-1}" -m "$1 | old | new-${NEW:-1} | https://example.test/compare | 3"
F
printf '#!/usr/bin/env bash\necho "bump-checks $*" >> "$LOG"\n' > "$w/.github/scripts/bump-checks.sh"
bin="$root/bin"; mkdir -p "$bin" "$root/open"
cat > "$bin/gh" <<'F'
#!/usr/bin/env bash
# stub gh: `pr list --head bump/x` answers the number in $OPEN/<x> when that file exists; every write is logged
echo "gh $*" >> "$LOG"
[[ "$1 $2" == "pr list" ]] || exit 0
head=""; while (($#)); do [[ "$1" == --head ]] && head=$2; shift; done
[[ -f "$OPEN/${head#bump/}" ]] && cat "$OPEN/${head#bump/}"
exit 0
F
chmod +x "$bin/gh" "$w/.github/scripts/"*.sh
export LOG="$root/log" OPEN="$root/open" GITHUB_REPOSITORY=o/r
git -C "$w" add -A; git -C "$w" commit -q -m init; git -C "$w" push -q origin testing
run() { : > "$LOG"; (cd "$w" && git checkout -q testing && git reset -q --hard origin/testing && env "$@" PATH="$bin:$PATH" bash .github/scripts/bump-weekly.sh) > "$root/run" 2>&1; rc=$?; }
n() { grep -c "$1" "$LOG"; }
parent() { git -C "$root/origin.git" rev-parse "bump/$1~1"; }
tip=$(git -C "$w" rev-parse HEAD)

run MOVE="scx ghostty"
check "two pins moved: two branches, each ONE commit on testing (independent, not a chain), two pull requests, checks for each" bash -c "
  [ $rc = 0 ] && [ \"\$(git -C '$root/origin.git' rev-parse bump/scx~1)\" = $tip ] && [ \"\$(git -C '$root/origin.git' rev-parse bump/ghostty~1)\" = $tip ] &&
  [ \"\$(git -C '$root/origin.git' diff --name-only testing bump/scx)\" = src/scx.env ] &&
  ! git -C '$root/origin.git' rev-parse -q --verify bump/extest > /dev/null &&
  [ \$(grep -c '^gh pr create' '$LOG') = 2 ] && [ \$(grep -c '^bump-checks' '$LOG') = 2 ]"

echo 7 > "$root/open/scx"
run MOVE=scx NEW=2
check "scx moved again with its PR open: the branch is rewritten and the PR updated, no second PR" bash -c "
  [ $rc = 0 ] && grep -q '^gh pr edit 7 ' '$LOG' && [ \$(grep -c '^gh pr create' '$LOG') = 0 ] && [ \"\$(git -C '$root/origin.git' show bump/scx:src/scx.env)\" = scx=new-2 ]"
run MOVE=scx NEW=2
check "the same bump again: the PR is up to date, nothing pushed or edited" bash -c "
  [ $rc = 0 ] && [ \$(grep -c '^gh pr edit\|^gh pr create' '$LOG') = 0 ] && grep -q 'is up to date' '$root/run'"
run MOVE=""
check "nothing moves but scx has an open PR: it is closed with a comment, other pins untouched" bash -c "
  [ $rc = 0 ] && grep -q '^gh pr close 7 .*--delete-branch' '$LOG' && [ \$(grep -c '^gh pr close' '$LOG') = 1 ]"
rm -f "$root/open/scx"
before=$(git -C "$root/origin.git" for-each-ref | md5sum)
run MOVE="extest builder" DRY_RUN=1
check "dry run: lists the would-be PRs, creates and pushes nothing" bash -c "
  [ $rc = 0 ] && grep -q 'would open a PR for extest' '$root/run' && grep -q 'would open a PR for builder' '$root/run' &&
  [ \$(grep -c '^gh pr create\|^gh pr edit\|^gh pr close' '$LOG') = 0 ] && [ \"\$(git -C '$root/origin.git' for-each-ref | md5sum)\" = '$before' ]"
echo; echo "passed: $pass, failed: $failn"
[ "$failn" = 0 ]
