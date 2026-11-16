# octarine-bins

The few programs that [octarine-os](https://github.com/redcodeworks/octarine-os) ships but Fedora does not package, built from pinned upstream sources and published as one small, signed container image: `ghcr.io/redcodeworks/octarine-bins`.

- the [sched_ext schedulers](https://github.com/sched-ext/scx) (`scx_rusty`, `scx_lavd` and the rest of the set octarine-os offers),
- [extest](https://github.com/Supreeeme/extest), the `libextest.so` libraries that make Steam Input work on Wayland,
- [Ghostty](https://ghostty.org), the terminal, built with its pinned Zig toolchain.

Two more images, `octarine-bins-dx` and `octarine-bins-gx`, are reserved for programs that only the developer (dx) or gaming (gx) variants of octarine-os need; they are empty for now. Each of the three is built, signed and published on its own.

## Why

octarine-os is a bootc image built every day. Compiling Rust, Zig and BPF programs inside that build would make it slow and fragile, and an upstream hiccup would stop the whole OS build. Building the programs here, once, and publishing the result keeps the OS build short and keeps its inputs simple: it reads `octarine-bins:latest` and nothing else. `latest` only moves after a build here succeeded as a whole, was signed and was approved, so if any program fails to build, nothing new is published and the OS keeps using the previous binaries.

## Requirements

Nothing, if you only use octarine-os: its build picks the image up by itself. To build the image yourself you need `podman` and `just`; the full build takes about 10 to 15 minutes, most of it Ghostty.

## Install and usage

The image is not meant to be run. It is `FROM scratch` and holds one directory per program (`/scx`, `/extest`, `/ghostty`), the license texts and source records of each, and a list of the components. A consumer copies from it, for example by mounting it in a step of its own Containerfile:

```
RUN --mount=type=bind,from=ghcr.io/redcodeworks/octarine-bins:latest,target=/binaries ...
```

Verify what you pull with the public key in this repository:

```
cosign verify --key cosign.pub ghcr.io/redcodeworks/octarine-bins:latest
```

Build it locally with `just build` and check the result with `just layout`.

## Contributing

This is a personal project. Issues and pull requests are welcome, but they may sit unanswered. Pinned versions change only through a reviewed pull request (a weekly workflow proposes each one as its own pull request). Run `just check` and `just test` before opening one.

## License

Apache-2.0 for the code in this repository (see `LICENSE`). The programs it builds keep their own licenses (scx: GPL-2.0, extest: MIT, Ghostty: MIT); the texts ship inside the image under `LICENSES/`, with the exact source each binary was built from.

## Credits

- [scx](https://github.com/sched-ext/scx) by the sched-ext authors.
- [extest](https://github.com/Supreeeme/extest) by Supreeeme and contributors.
- [Ghostty](https://github.com/ghostty-org/ghostty) by Mitchell Hashimoto and contributors, and [Zig](https://ziglang.org) by the Zig Software Foundation and contributors.

## Disclaimer

This is a personal project, written with Claude's help, so please excuse the slop. I have only tested it on my own setup and will not maintain it beyond my own needs. Forks are welcome.
