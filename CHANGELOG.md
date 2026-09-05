# Changelog

All notable changes to snip-pin are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[Semantic Versioning](https://semver.org/) and start at 0.x while the tool is
Hyprland-only and its interfaces (subcommands, environment variables, cache
layout) may still change.

## [Unreleased]

### Fixed
- Copy and save no longer crash, leaving the pin open, when `notify-send` is not installed (#6).

## [0.1.0] – unreleased

First tagged release. Everything before it was developed on `main` without
version numbers between 2026-09-02 and 2026-09-05.

### Added
- Snip a region, a window or an element inside a window and pin it where it
  was taken; drag, zoom, fade, copy, save.
- Annotations on the pin: rectangle, arrow, pen, text, marker, blur.
- History: re-pin the last snip, thumbnail picker, kept snips, pin from the
  clipboard, double-tap to open the picker.
- One process for all pins; a second pin appears in about 100 ms.
- `VERSION` file and `snip-pin.sh --version`.

[Unreleased]: https://github.com/felsenuboot/snip-pin/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/felsenuboot/snip-pin/releases/tag/v0.1.0
