# Converting the CI shell scripts to Python (notes from octarine-bins, PR #21)

A maintainers' note, not user documentation. The original shell scripts are kept in `.archive/shell-ci/`.

## 1. The recipe, in the order that worked
- Inventory first: list every script, who calls it (workflows, composite actions, Justfile, other scripts) and which tests exist; extract the old tree with `git archive origin/<base>` into a scratch directory for later comparison.
- Write the shared helpers first (`ci.py`: logging, retry, outputs; `registry.py`: skopeo/cosign), then port script by script, leaves before callers.
- After each script, compare it with the old one on a table of cases (section 2), not by reading.
- Convert the tests to pytest next, with fakes for the external tools (section 6), then switch workflows, actions and the Justfile to the new call lines, then the docs.
- Only then add the dependency layer (pyproject, lock, setup action) and the expressiveness pass (comprehensions, `match`, typed records); do not mix them with the port, or a behavior change hides in a style change.

## 2. Old-vs-new comparison
- Run the old script (from the extracted tree) and the new one in the same scratch clone with the same fake `skopeo`/`cosign`, and compare the exit code and the `GITHUB_OUTPUT` file (also stdout for decision scripts): 96 cases for skip/promote (15 changes x 3 images, plus 3 registry conditions).
- Also compared: inputs lists, writers (diff of the written files), lint on fixtures, layout messages on a mutated real listing, the weekly bump dry run (the full PR body), live decisions against the real GitHub API.
- It caught: output ordering when a child process and the parent both print (stdout buffering), a manifest-digest assumption (checked against live `skopeo manifest-digest`), a ZIG_SIZE line that does not exist in `zig.env` (sed ignores a missing line, my writer raised), and the harness's own stub missing a subcommand.

## 3. Bash habits that bite in Python
- `set -e` becomes exceptions: a failing subprocess is a value you must check (`check=True`) or you carry on silently; decide per call which failures are expected (EAFP with `CalledProcessError`).
- Distinct exit codes (3 absent, 4 read error, 5 not verified) become typed exceptions; a definitive answer must not be in a retry's `on` list.
- A subprocess that reads stdin consumes it (dnf5 swallowed a list in the installer); in Python pass `stdin=DEVNULL` or read the list first.
- Quoting and globbing disappear (argument lists, `Path.glob`), but zsh/bash differences still bite the test harness: a `$PODMAN` variable is not word-split in zsh.
- PATH fakes work the same, but a fake directory on PATH shadows real tools in every test; keep its contents few.
- stdout is block-buffered when piped: flush (the logging handler does) or the child's lines and yours interleave wrongly in the CI log.
- `python -I` drops the script's directory from `sys.path`: each script starts with two lines that add it back; `-I` also ignores `PYTHONPATH`, which is the point.
- githubkit traps: `gh.rest` holds only a weak reference to the client (keep the `gh` variable); a zero `timedelta` in a retry option means "wait 60 s"; `rest.paginate` replaces the deprecated `gh.paginate`; typed responses validate every field, so fakes need real captured answers.

## 3a. Style rules for the converted scripts (Kevin, 2026-11-18)

- Prefer expressions over statements. Compute a value with a comprehension, generator expression, conditional expression, `any`/`all`/`sum`/`min`/`max`/`sorted(key=)`, `itertools` or a function that returns it, and bind it once; do not mutate accumulators or flags (`result = []` plus `append`, `found = False` plus a loop).
- Exception: `match` is a statement but comes from the functional tradition. Use it freely to branch on a value or shape (decision tables, classification, exit-code dispatch), each arm returning its value instead of mutating shared state.
- When per-item work is messy (match, try/except, logging, subprocess), put it in a small named helper that takes one item and returns its value, and call it from a flat one-line comprehension: `results = [check(x) for x in xs]`. Avoid comprehensions with inlined nested conditionals, deep nesting and walrus puzzles. A helper that only has side effects runs in a plain loop. Example from the pilot: `bump_weekly.bump_pin` returns True when its pin failed, `failures = [bump_pin(p, ...) for p in pins]`, `any(failures)` is the exit status.
- Use these only where they make the code shorter and clearer; control flow that is really a sequence of guarded side effects (a retry loop, a polling loop, a validation that must report the FIRST error in file order) stays a loop.
- Logging: classify every `print`. A script's RESULT (something a workflow captures or a caller reads: `key=value`, a digest, a decision line) goes to stdout through ONE helper (`ci.result`, `ci.emit`). Reasons, progress, decisions and diagnostics are `log.info` / `log.warning` / `log.error` through the Annotations handler, which writes everything to stderr, so no log line can land in a captured stdout. One pytest runs a script as a subprocess and asserts stdout holds only the result. A script that cannot import the helper (it runs before uv is set up, as `plan_tests.py`) follows the same stream rule by hand with `print(..., file=sys.stderr)`.
- WARN vs ERROR: a check that breaks nothing is `log.warning` (a `::warning::` annotation, the step stays green); `log.error` plus a nonzero exit only for conditions that make the result wrong or unsafe (signature, digest, promotion, a published tag). Never downgrade a check that guards one of those; list every level change in the PR.
- Formatting: ruff with its defaults (no `[tool.ruff]` table, no lint rules, no CI check), locked as a dev dependency; an opt-in `githooks/pre-commit` runs `uv run --frozen ruff format` on the staged Python files (and `shfmt -w`, defaults, on staged shell files) and re-stages them, `just hooks` enables it (`git config core.hooksPath githooks`).

## 4. Dependency and CI setup that worked
- `pyproject.toml` (`requires-python >=3.14`, dependencies, a `dev` group), a committed `uv.lock` with hashes, `.python-version` (3.14, uv-managed).
- Every script: `uv run --frozen --no-dev python -I .github/scripts/<name>.py`; `--frozen` resolves nothing at run time, `uv run` creates the venv, so there is no separate install step.
- One composite action, `setup-ci-python`, wraps the SHA-pinned `astral-sh/setup-uv` with its cache on (keyed on `uv.lock`, plus the managed Python); the sign job, which holds the signing key, passes `cache: false`. Other steps in that job (cosign through `setup-tool`) still restore their own cache; that is a separate decision.
- zizmor stayed green without a new ignore; the cache needed none. Action versions stay pinned by SHA.
- A weekly `python-deps` bump kind runs `uv lock --upgrade` and opens its own PR like the other pins.

## 5. Shared helpers: generic and repo-specific
- Generic (reusable as they are): the `@retry` decorator, step outputs (`emit`, `append_to`), the logging handler that writes `::error::`/`::warning::`, `group`, `arg_parser`, the githubkit client factory with `retry_decision`, `git`, `version`.
- Repo-specific: `inputs(dir)` (what rebuilds which image), `pins.py` (the pin files and decisions), `registry.py` (our reads, typed answers, and the sign/copy/push calls: reusable in octarine-images, not in octarine-os).
- Recommendation: copy `ci.py` per repo (about 150 lines) and keep `registry.py` only where images are pushed. A shared package would be one more pinned, versioned artifact with its own release and bump flow, and every consumer would need a PR for each helper change; copies drift, but this code is small, changes rarely, and each repo has its own signing key and pin rules.

## 6. Testing
- Real script as a subprocess against fakes: executable Python files in `tests/fakes/` (`skopeo`, `cosign`), and a fake GitHub API server (stdlib `http.server` in a thread, started by a fixture, reached through `GITHUB_API_URL`; it can drop connections). Real git repositories for skip/promote and the weekly bump; the real assembler into the real layout checker.
- Tables are `pytest.mark.parametrize` (the skip/promote matrix, lint fixtures, layout damage).
- Only a real run proves: the build/sign/promote jobs calling the scripts, the real registry (push read-back), the real signing key, githubkit against the live API inside a workflow (PR creation, dispatch, check runs), and the uv cache restore.

## 7. What not to convert
- Anything that runs inside a build container or on the host image: `installer/install.sh`, `build.sh`, `src/lib/*.sh`, Containerfile shell steps, ujust recipes. The container has no uv or Python by design, the scripts are the product, and their tests (a stub `dnf5`) already work. Keep a bash test for bash code.

## 8. Size and effort (octarine-bins)
- 18 shell scripts, 771 lines (551 code lines), 6 shell tests (505 lines) became 15 scripts plus `ci.py`, `registry.py` and `pins.py`: about 1640 lines, and 1018 lines of tests and fakes (the installer test stays bash, 111 lines). The Python is roughly twice the code lines; it is longer mostly in docstrings, typed models and argparse.
- Cheap: the pure decision scripts (skip, promote, lint, layout), because the comparison table exists. Expensive: the bump scripts (git plus API plus PR state) and the tests for them, the githubkit migration (typed models need real fixtures), and the dependency/CI layer.
- Agent turns: the first port and tests took about two thirds of the work; the follow-up rounds (decorator, SDK, fakes as files, uv layer, style) were several smaller rounds each.

## 9. Mistakes, rework, surprises
- Rework from changing direction: retries first as a function around subprocess, then a decorator, then the SDK's own retry decision; the SDK direct was the simplest, so ask for the target design before writing wrappers.
- A private retry loop was left in `ghostty_check.py` (a `time.sleep` around `urlopen`) and found only in review; grep for `sleep`/`range(` loops after any retry refactor.
- The fakes began as Python inside string literals; real files are easier to read and lint.
- The local build's scratch storage on the session tmpfs hit `Disk quota exceeded` (cpio and cargo errors that look like build bugs); the second time its script and logs disappeared between two runs for a reason I did not find (not caused by my commands that I can tell), so images had to be rebuilt.
- A `git stash` of the working tree mid-task (restored at once, nothing lost) is a reminder: never stash while untracked scratch files matter.
- Surprises: githubkit validates typed responses fully (fakes need captured answers), `gh.rest` weak reference, zero timedelta, `uv run --no-project` picked Python 3.15 until `.python-version` pinned 3.14.

## What the first real run found
- Two failures that the fake registry could not show (fixed in PR #24): after `skopeo copy` to `:testing`, `cosign verify` of the tag answered `image tag not found ... MANIFEST_UNKNOWN` (dx sign), and the config read of `image@digest` answered `manifest unknown` for a digest just taken from the `testing` tag (gx build, skip step).
- Cause of both: GHCR read-after-write lag and inconsistent answers between requests, combined with a classification that treated "not found" as a definitive answer (`NotVerified`, `Absent`).
- Both the shell and the Python versions had that same latent classification; the old shell passed mostly by timing. The conversion did not introduce the bug, it only made it visible once a run hit the window.
- Lesson: a fake registry cannot show read-after-write lag or the real registry's messages. Test the classification with fakes that copy the real messages (lagging fakes: tag not found for the first N reads, no signatures found for the first N verifies), and treat "not found" on anything the registry just named or you just wrote as transient (retry it); only a real signature verdict is definitive.

## 10. Go / no-go checklist for the next repo
- Go if: the scripts run on the runner (not in containers), there are fewer than about 25 of them, the callers are few and found by one grep, and there is a way to run the old and new on the same inputs (fakes or a dry-run mode).
- Check first: inline shell in workflow YAML (it must move into scripts before it can be converted or tested), existing shell tests (they become the case table), network calls (what must be retried and what is definitive), secrets in the job (cache decision), whether a `bump`/PR flow exists (the expensive part).
- If I did it again: decide the target design first (SDK direct, one retry decorator, typed records, fakes as files, uv layer) and port once; do the old-vs-new harness before the first script; keep the style pass as its own commit; keep the scratch podman storage outside tmpfs from the start.
- Before declaring a conversion done, run it once on the real runner and registry (a draft PR is not enough) and keep the revert path (the archived shell scripts) until that run is green.
