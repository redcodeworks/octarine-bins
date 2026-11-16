# CLAUDE.md

Guidance for Claude Code in this repository. Read it first; the repo's agent (`bins-project` in the hub) is the only writer here.

## What this repo is

It builds the binaries octarine-os installs (the sched_ext schedulers `scx`, `extest`, Ghostty with its Zig toolchain) as ONE signed image, `ghcr.io/redcodeworks/octarine-bins`. The octarine-os image build, on `testing` and in the daily build, reads `octarine-bins:latest` and nothing else: it never looks at this repo's source tree, tags or workflow results. The code was moved here on 2026-11-16 from `binaries/` in octarine-os (now `src/`), together with its CI.

`README.md` is user-facing (what, why, how to use, license, credits, disclaimer); implementation details live here.

## Layout of the repo

- `src/Containerfile`: ONE multi-stage build. A stage per component (`scx`, `extest`, `ghostty`), an `assemble` stage, a final `FROM scratch`. Build context is `src/`. Every component is required: a failing stage fails the build and nothing is pushed (atomic).
- `src/components.tsv` (component names, one per line, comments allowed), `src/builder.env` (the digest-pinned Fedora builder every stage starts from, passed as `--build-arg BUILDER`), `src/<component>/` (`upstream.env` pin, `build.sh`, for scx `schedulers.tsv`, for ghostty `zig.env` and `fetch-zig.sh`), `src/lib/` (`upstream-env.sh` parser, `fetch-source.sh`, `stage.sh`, `assemble.sh`).
- `.github/workflows/`: `test.yml`, `binaries.yml` (build, sign, promote), `bump-pins.yml`, `zizmor.yml`, `require-testing-branch.yml`. `.github/scripts/`: the helpers (below). `.github/actions/setup-tool` installs pinned cosign or just. `.github/dependabot.yml` bumps the pinned actions (PRs into `testing`).
- `Justfile`: `check` (format check), `build`, `layout`, `test`; CI calls `build` and `layout` too. `tests/*.sh`: stub-based tests, no network.
- `cosign.pub` (public, committed). `cosign.key` sits in the repo directory, git-ignored: NEVER read, print, copy, stage or commit it. CI signs with the secret `SIGNING_SECRET` of the environment `signing` (branch policy `main`, `testing`); do not create other secrets or tokens.

## The image layout (the contract with octarine-os)

```
/binaries/            (the image root; octarine-os bind-mounts the image at /binaries)
  COMPONENTS          component names, one per line (written by src/lib/assemble.sh from components.tsv)
  SOURCE, BUILDINFO   the components' records one after the other, each under a [name] heading
  LICENSES/           union of the component license directories
  scx/       bin/<scheduler>..., schedulers.tsv (tab separated, 4 fields: name, bin/name, minimum kernel, description), SOURCE, BUILDINFO, LICENSES/
  extest/    lib/libextest.so (32-bit), lib64/libextest.so, SOURCE, BUILDINFO, LICENSES/
  ghostty/   root/usr/... (the installed tree), FILES (one path under root/ per line), SOURCE, BUILDINFO, LICENSES/
```

The label `org.opencontainers.image.version` is `<UTC date>-<short commit sha>` (also the immutable tag of that build); `...revision` is the full commit sha. Informational: the OS does not check either (the old tree-id key is gone, the OS reads `latest` only). Why: a tag per commit is easy to find from a workflow run, and the content identity does not need to be carried because `latest` is the signed digest of one build.

**A layout change goes reader-first:** octarine-os (`build_files/install-binaries.sh`, `gen-schedulers.sh`, the smoke tests) first accepts BOTH the old and the new layout, then this repo writes the new one, then the OS drops the old one. Say so under `Needs from hub:` whenever the layout would change. `.github/scripts/binaries-layout.sh --assembled` (run on every built image by `just layout`) is this repo's copy of what the reader needs; `tests/image-layout.sh` runs the real assembler into it.

## Branches, CI and promotion (GitLab Flow)

1. A push to a feature branch (`feat/`, `fix/`, `chore/`) runs `test.yml`: job `test` (`bash -n` on every script, workflow parse, `just check`, `just test`) and job `secrets` (gitleaks over the full history). `zizmor.yml` audits `.github/` on PRs that touch it. Open a PR into `testing`. Required checks (ruleset, set by the hub): `test` and `secrets`.
2. A push to `testing` runs `binaries.yml`: `build` (`just build`: the whole multi-stage build, about 10 to 15 minutes, Ghostty the longest; `just layout`; `push-image.sh` pushes the immutable tag `<date>-<sha>` and prints the digest), then `sign` (environment `signing`: `cosign sign` that digest with the old bundle format, verify with `cosign.pub`, and only THEN `skopeo copy --preserve-digests` to `:testing`, read it back with `registry-settled.sh`, verify again). So `testing` never points at an unsigned image.
3. A promotion PR from `testing` into `main` (only from `testing`: `require-testing-branch.yml`; Kevin merges it) makes the push to `main` run the `promote` job: `binaries-promote.sh` retags the TESTED, signed `:testing` digest to `:latest` (no rebuild). It fails loudly, changing nothing, unless `testing` exists, verifies with `cosign.pub` and was built from this tree (guard below); when `latest` already is that digest it does nothing; afterwards it reads the tag back and verifies through `:latest`. Nothing else moves `latest`.
4. A dispatch from any other ref builds for validation and publishes nothing. By hand: `gh workflow run binaries.yml --ref testing` rebuilds, `--ref main` promotes.
5. Builds are not path filtered: every push to `testing` and `main` runs the workflow (`main` only promotes). Runs of one ref queue, none is cancelled (`cancel-in-progress: false`), so two runs never move `testing` out of order. A superseded older `testing` run may be cancelled by hand; never a `main` or a signing run, never the tip.

Promotion guard: `binaries-promote.sh` also fails, changing nothing, unless the testing image was built from exactly what is promoted: the tree of the commit in its `org.opencontainers.image.revision` label must equal `HEAD^{tree}` of the main checkout (the promotion PR's merge commit; its tree equals the testing tip's when main has nothing testing lacks, which the ruleset ensures; the first promotion works the same). A tree mismatch means the build on testing is running, failed, or testing moved on (a docs-only push also gets a build, so waiting always resolves it). Hand fix: wait for the tip's build on `testing` to be green, then `gh run rerun` the promote run (or `gh workflow run binaries.yml --ref main`).

## Pin policy

Pins: scx and extest (`src/<c>/upstream.env`: tag and/or commit), Ghostty (`src/ghostty/upstream.env`) with Zig (`src/ghostty/zig.env`: Zig changes ONLY together with a Ghostty pin, its minor follows from the tag), the Fedora builder digest (`src/builder.env`, bumped once the digest has been the tag's value for 7 days), the GitHub Actions (by commit, Dependabot), the CI tool versions (cosign, just, gitleaks, zizmor: by hand). Pins change only through a PR Kevin approves. `bump-pins.yml` (Monday 07:00 UTC, or `gh workflow run bump-pins.yml --ref testing`, `-f dry_run=true` to preview) opens ONE PR into `testing` (`bump/weekly-YYYYMMDD`), one commit per pin that moved; drop a pin by reverting its commit. It uses only `GITHUB_TOKEN` (no PATs, no cross-repo PRs). Needs the repository setting "Allow GitHub Actions to create and approve pull requests". PRs opened with `GITHUB_TOKEN` start no workflow, so `bump-checks.sh` dispatches `test.yml` on `testing` with `ref=<bump commit>` and posts check runs named `test` and `secrets` on the bump commit with the real results (`actions: write`, `checks: write`). Scheduled workflows run only from the default branch, so the schedule starts after the first promotion.

## What octarine-os expects

`octarine-bins:latest` exists, verifies with `cosign.pub` (the OS build resolves the tag to a digest once and verifies it), and has the layout above, with exactly the components its installer's `required` list names (`scx extest ghostty`), no more. A new or removed component means a coordinated change on both sides (reader first). The scheduler list is `src/scx/schedulers.tsv`; octarine-os generates its registry from the copy inside the image.

## Failure modes and hand fixes

- A component does not build (an upstream download failed, a Fedora package moved, Ghostty/Zig broke): `build` is red, nothing is pushed, `testing` and `latest` stay. Re-run the run if it looks transient; otherwise pin back the component (`upstream.env`; for Ghostty also `zig.env`, together) in a PR. A component that keeps failing is removed in a coordinated change (reader first): its stage, directory, line in `components.tsv`, the layout case in `binaries-layout.sh`, and the OS side.
- `sign` fails after a push: the version tag exists unsigned, `testing` did not move. Re-run `sign`, or push to `testing` again. An unsigned version tag is never promoted (only `testing` and `latest` are).
- Promotion fails ("not signed", "does not exist", "could not be read"): fix the testing build, then re-run the promote run (`gh run rerun`, or `gh workflow run binaries.yml --ref main`). Exit 4 from a registry or cosign read means a read error, never "absent".
- The first push of a new package creates it private in GHCR; Kevin makes it public and links it to the repo (package settings, "Manage Actions access" with Write for this repository) by hand.
- Old testing images accumulate; deleting versions is Kevin's, by hand in the packages UI.

## Scripts (`.github/scripts`)

`retry.sh` (the one retry helper for network calls), `registry-read.sh` (exit 3 absent, 4 read error), `registry-digest.sh`, `registry-settled.sh` (read-back until the tag shows the digest), `cosign-check.sh` (exit 5 not verified, 4 read error), `push-image.sh`, `binaries-layout.sh`, `binaries-promote.sh`, `upstream-check.sh` and `ghostty-check.sh` (the decisions), `bump-run.sh`, `bump-ghostty.sh`, `bump-weekly.sh`, `bump-checks.sh` (the bump), `sanity.sh`. Retries stay on network calls; "could not read" is never taken for "not there".

## Rules for the agent

- Commit on a feature branch by explicit paths (never `git add -A`), message says why, no Co-Authored-By trailer. Never commit on `testing` or `main`, never force-push, never merge into `main`, never touch rulesets or repository settings. One PR into `testing` per queue, amended (title at most 140 characters; detail in the body). Check `git remote -v` shows `github.com/redcodeworks/octarine-bins` and pass `-R redcodeworks/octarine-bins` to every `gh` command.
- Machine-read files are JSON read with `jq`, never positional TSV where avoidable (the existing `schedulers.tsv` and `components.tsv` are the OS's contract). Tests run the real writer into the real reader; no tests of workflow or README text; keep them slim.
- Local image build for large changes: `PODMAN="podman --root <scratch>/root --runroot <scratch>/run --storage-driver overlay" just build`. Never bulk-remove (`rmi -a`, `system prune`), never touch the real podman storage.
- README: user-facing only (see its sections), American English, "they/them" or first person for Kevin.
