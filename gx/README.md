# octarine-bins-gx

The gx image of octarine-os needs no prebuilt binary yet, so this image is an empty skeleton: `/COMPONENTS` is empty and there is no component directory.

To add a component `<name>`: copy a stage of `src/Containerfile` into `gx/Containerfile` (above the assemble stage, plus a `COPY --from=<name> /out /out/<name>` line there), put its pin and build script in `gx/<name>/` (`upstream.env`, `build.sh`; the helpers in `src/lib` are shared, reached as `COPY --from=lib`), add the name to `gx/components.tsv`, add a case for it to `.github/scripts/binaries-layout.sh`, and remove `--allow-empty` from the assemble line. The OS side must learn the component first (reader first, see CLAUDE.md).
