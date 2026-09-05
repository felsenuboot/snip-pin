# Changelog

All notable changes to snip-pin are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[Semantic Versioning](https://semver.org/) and start at 0.x while the tool is
Hyprland-only and its interfaces (subcommands, environment variables, cache
layout) may still change.

## [Unreleased]

### Fixed
- `install.sh` quotes the script path in the desktop entry, so a checkout under a path with spaces launches from the menu (#11).
- A JPEG or WebP opened with `pin FILE` is re-encoded as PNG when copied or saved; before, the raw file was sent to the clipboard labelled `image/png`.
- Element detection is bounded: a spreadsheet or a terminal full of box-drawing lines took up to 3 s with the screen frozen; the join now runs longest-first with a 150 ms budget and a pair window, and `snip-pin.sh` stops waiting after 0.6 s. An empty capture no longer makes the helper raise (#9).
- Thin snips (a single line of text) are no longer stretched to 40 px: the minimum size applies to the scale, not to each side, and annotations on such pins land where the pointer is (#8).
- Two pins saved within the same second no longer overwrite each other (`pin_..._2.png`), and a save that fails (unwritable folder, full disk) shows the error and keeps the pin open instead of dying with a traceback (#10).
- A missing, truncated or non-image file (a snip deleted before `last`, a bad clipboard image, `pin FILE` on a text file) shows a "Cannot open" toast instead of crashing the viewer silently and making the next pin wait three seconds (#7).
- Copying or saving an annotated pin no longer leaves a `_annotated.png` next to the original: in the cache (where the history picker and `last` picked it up), in `kept/` (where it never expired) or in the user's folder for `pin FILE`. Leftovers from earlier versions are cleaned up and ignored (#5).
- Copy and save no longer crash, leaving the pin open, when `notify-send` is not installed (#6).

## [0.1.0] – unreleased

First tagged release. Everything before it was developed on `main` without
version numbers between 2026-09-02 and 2026-09-05.

### Added
- Test suite (`tests/`, pytest) for the display-free code and a third CI job that runs it (#13).
- `install.sh --uninstall`, and a hidden "Pin image" desktop entry so file managers offer *Open with → Pin image* for PNG, JPEG, WebP, BMP and GIF files.
- `SNIP_ELEMENTS_BUDGET_MS` to tune the detection budget.
- Snip a region, a window or an element inside a window and pin it where it
  was taken; drag, zoom, fade, copy, save.
- Annotations on the pin: rectangle, arrow, pen, text, marker, blur.
- History: re-pin the last snip, thumbnail picker, kept snips, pin from the
  clipboard, double-tap to open the picker.
- One process for all pins; a second pin appears in about 100 ms.
- `VERSION` file and `snip-pin.sh --version`.

[Unreleased]: https://github.com/felsenuboot/snip-pin/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/felsenuboot/snip-pin/releases/tag/v0.1.0
