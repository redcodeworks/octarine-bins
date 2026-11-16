#!/usr/bin/env bash
# The fast sanity check (first step of the required `test` job of test.yml, about 10 s): every shell script parses, and every workflow file is valid YAML (a broken
# one gives a "workflow file issue" with no job and no useful message). Everything else shows where it matters. Run from the repository root.
set -uo pipefail
fail=0
while IFS= read -r f; do bash -n "$f" || { echo "FAIL: bash -n $f"; fail=1; }; done < <(git ls-files '*.sh')
python3 -I - <<'PY' || fail=1
import glob, sys, yaml
for f in sorted(glob.glob(".github/workflows/*.yml")):
    try:
        yaml.safe_load(open(f))
    except Exception as e:
        print("FAIL: %s does not parse: %s" % (f, e)); sys.exit(1)
PY
[[ "$fail" = 0 ]] && echo "sanity: all checks passed"
exit "$fail"
