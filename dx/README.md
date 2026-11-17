# octarine-bins-dx

The inputs of the dx (developer) image of octarine-os: the developer package list and its installers, plus (later) any prebuilt binary only dx needs.

- `packages/dev.txt`: the package list (Fedora packages only; moved here from octarine-images on 2026-11-16). Syntax and policy: `CLAUDE.md`, "The package lists".
- In the image: `/packages/dev.txt`, `/packages/install.sh`, `/packages/install-copr.sh` (the installers are the shared copies in `installer/`). `bash /packages/install.sh` installs the list.
- No binary component yet: `/COMPONENTS` is empty and there is no component directory.

To add a binary component `<name>`: copy a stage of `src/Containerfile` into `dx/Containerfile` (above the assemble stage, plus a `COPY --from=<name> /out /out/<name>` line there), put its pin and build script in `dx/<name>/` (`upstream.env`, `build.sh`; the helpers in `src/lib` are shared, reached as `COPY --from=lib`), add the name to `dx/components.tsv`, add a case for it to `.github/scripts/binaries-layout.sh`, and remove `--allow-empty` from the assemble line. The OS side must learn the component first (reader first, see CLAUDE.md).
