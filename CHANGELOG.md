# Changelog

All notable changes to snip-pin are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[Semantic Versioning](https://semver.org/) and start at 0.x while the tool is
Hyprland-only and its interfaces (subcommands, environment variables, cache
layout) may still change.

## [Unreleased]

### Added
- Configuration file `~/.config/snip-pin/config` (`key = value`, see `config.example`) read by the script and the viewer; environment variables still override. The viewer re-reads it whenever a pin is opened, so a change applies to the next pin even though all pins share one process. `snip-pin.sh config` prints the effective settings with their source (#87).
- Rebindable keys and mouse buttons on a pin: `[keys]` maps actions (`copy`, `save`, `undo`, `tool_arrow`, ...) to bindings such as `ctrl+shift+c`; `[mouse]` sets what right-click, double-click and middle-click do (`copy`, `save`, `close`, `menu`, `reset_zoom`, `none`). Menus show the configured keys (#87).
- The selection's look is configurable: `sel_border`, `sel_width`, `sel_mask`, `sel_fill` (colours as `#rrggbb` or `#rrggbbaa`), and slurp shows the selection's size while dragging (`sel_size = 0` hides it) (#74).
- `cursor = 1` includes the mouse cursor in the capture (#75).
- `snip-pin.sh screen` captures the monitor under the pointer without a selection, `screen all` every monitor as one image; both pin the result like a snip (#96).
- `snip-pin.sh repeat [N]` captures the area of the N-th last snip again without a selection; the last `areas` (default 8) regions are remembered in the cache (#76).
- `snip-pin.sh copy` and `snip-pin.sh save` select and copy or save without pinning; `action` in the config sets what the bare command does (`copy`, `save`, `pin` joined by `+`, default `copy+pin`) (#77).
- `autosave_dir`: every capture is also saved to that folder (raw, without annotations), whatever happens to the pin afterwards (#82).
- `default_tool`: the annotation tool that is active as soon as a pin opens (#78).
- `snip-pin.sh toggle` hides every pin or shows them all again where they were, with their annotations, zoom and opacity; `snip-pin.sh close-all` closes every pin and asks first when there are several (`confirm_close_all = 0` skips the question) (#64).
- Click-through: `Ctrl+T` on a pin (dashed border) lets every click, drag and scroll pass to the window below; `snip-pin.sh clickthrough` toggles all pins, which is also the way back since a click-through pin receives no input (#65).
- Rotate (`Ctrl+R` / `Ctrl+Shift+R`) and flip (`Ctrl+H` / `Ctrl+J`) a pin; annotations turn with the image, each step is undoable, the window keeps its centre. `Ctrl+0` is now "reset image": zoom, opacity, rotation and flips (#67).
- The wheel zooms around the pointer: the image point under the cursor stays put and the pin moves (`zoom_at_pointer = 0` for the old behaviour). "Smooth scaling" in the menu (`smooth = 0` in the config) switches to nearest-neighbour when zoomed in, for pixel-exact inspection (#68).
- `snip-pin.sh reopen` brings the last closed pin back where it was, with zoom, opacity and annotations (crop and rotation baked in); the last `reopen` = 5 closed pins are kept for the session, and it works after the viewer process has ended. `Shift+Esc` destroys a pin for good (#69).
- `opacity` sets the starting opacity of every pin (`Ctrl+1` returns to it) and `alpha_bg` what shows behind transparent images: the desktop, a checkerboard or a colour (#72).
- Thumbnail mode: `Ctrl+M` or `Shift` + double-click collapses a pin into a `thumb_size` (75 px) tile at its corner, a click brings it back; with a crop marquee drawn, `Shift+Enter` shows only that part as the tile (#66).
- `Ctrl+Shift+S` opens a Save As dialog preset with the folder, the `filename` pattern and the last extension used; `.png`, `.jpg` or `.webp` picks the encoder and the pin stays open. Quick save, autosave and the `save` mode use the `filename` strftime pattern and the `format` / `quality` settings (#81).
- Copying from a pin offers the image as `image/png` and as a file (`text/uri-list`) at once, so file managers and chat clients paste a file while image editors take the pixels; `copy_file = never` keeps PNG only. The viewer owns the clipboard itself and lingers after the last pin until another program takes the clipboard over (#83).
- `Ctrl+P` prints a pin through GTK's print dialog, annotations included, scaled to fit the page and centred (#85).
- `sound = default` (or a file) plays a sound when a snip is copied or saved, from a pin or from the `copy` / `save` modes (#88).
- Custom commands: `[commands]` entries (`Open in GIMP = gimp %f`) appear under *Send to* in the menu, the first nine on `Ctrl+Shift+1` … `9`, and run detached with the exported image; *Open with…* asks the desktop which application to use (#86).
- Palette: `palette` sets up to nine swatches (keys `1`–`9`), `widths` two to five stroke widths, `Ctrl`+click on a swatch opens a colour chooser for the session, and every tool remembers the colour and width it was last used with (`tool_colors = 0` turns that off) (#79).
- Eraser tool (`X`): click an annotation to remove it. With no tool active a click selects an annotation, a drag moves it, `Delete` removes it, a swatch or width restyles it; every step is undoable and numbered steps renumber (#80).
- `snip-pin.sh clipboard` with text in the clipboard renders it into an image (Pango, `text_font`, `text_width`, `text_margin`, `text_fg`, `text_bg`) and pins that; a copied path to an image file pins the file (#71).
- `Ctrl+Shift+C` copies the text in a pin, recognised by tesseract (`ocr_lang`, or any engine via `ocr_cmd`); with a crop marquee drawn only that part is read. Small captures are upscaled first; the result is toasted and the pin stays (#84).
- `debug = 1` logs subcommands, geometry, captures, hand-overs, placement replies, copies, saves, warnings and uncaught exceptions with timestamps to `~/.local/state/snip-pin/log` (rotated at 1 MB); `snip-pin.sh log [-f]` shows it and `doctor` names it (#89).
- Pin groups: `snip-pin.sh group next|prev|N` shows another group of pins and hides the current one; new pins join the current group, *Move to group* in the menu moves a pin, `toggle` acts on the current group (#70).
- Translations: the viewer's and the picker's strings go through gettext; a German translation ships in `po/de.po` and `install.sh` compiles it into `~/.local/share/locale` (#90).

## [0.3.0] – 2026-09-06

The editor milestone: every item verified live on Hyprland 0.56.2 with input injection.

### Added
- The text tool takes its input through a GTK input-method context: dead keys, the Compose key and ibus/fcitx input methods (CJK, emoji pickers) work, composing text is shown while typing, and `Ctrl+V` pastes (#30).
- Crop tool (`C`): drag the part to keep, `Enter` applies, `Esc` cancels. Undo restores the full image; the pin shrinks in place so the kept part stays where it was (#32).
- Image files dropped onto a pin open as new pins; together with the *Open with → Pin image* entry from 0.1.0 this closes #33.
- Ellipse tool (`E`) and numbered-step counter (`N`): a click places a badge with the next number; undo takes the number back (#31).

## [0.2.0] – 2026-09-06

Every monitor, scaled outputs, and the robustness items from the review;
all changes verified on Hyprland 0.56.2 except the HiDPI one.

### Added
- `docs/decisions.md`: measurements and the decisions on a Rust rewrite, GNOME and macOS ports, layer shell and Python (#25, #26, #27).
- `snip-pin.sh doctor` lists the required and optional tools, the GTK, numpy and Hyprland versions and the config style; `install.sh` runs it at the end (#24).
- `snip-pin.sh clipboard` pins JPEG, WebP, BMP, GIF and TIFF clipboard images and copied image files (`text/uri-list`), not only PNG; they are converted into the cache as PNG (#17).
- `SNIP_PIN_SAVE_DIR` chooses the save folder; without it the XDG pictures directory (localised) is used after the ML4W setting (#16).

### Fixed
- HiDPI: a snip taken on a scaled output opens at 1/scale, covering exactly the region it was taken from, and `Ctrl+0` returns to that size (#15, untested on real scaled hardware; identical to before at scale 1).
- Placement talks to Hyprland's IPC socket directly (0.2 ms per request instead of a 10 ms `hyprctl` spawn on the GTK main loop), works with both the Lua (`hl.dsp.window.move`) and the classic (`movewindowpixel`) dispatcher syntax, and keeps a pin taken at the screen edge fully on its monitor (#22).
- The viewer's hand-over socket is an abstract Unix socket: nothing stale survives a crash, a stuck viewer is never taken over by a second one, a stalled client can no longer freeze the pins for three seconds, and a hand-over that arrives while the last pin is closing is refused immediately instead of timing out (#19).
- The history picker decodes thumbnails on a worker thread and caches them in `~/.cache/snip-pin/thumbs`, so a large history no longer stutters for seconds; snips older than six days show their date (#18).
- Touchpad scrolling zooms and fades in single steps instead of bursts: smooth-scroll events accumulate, one step per 30 units; the wheel keeps one step per notch. The pin briefly shows the zoom or opacity percentage (#21).
- Window snapping offers the windows on every monitor's visible workspace (and an open special workspace), not only the focused monitor's (#14).
- Right-click and the double tap end only the slurp that snip-pin started (by PID, through the new `abort` subcommand), not every slurp on the system (#20).
- `snip-pin.sh` validates `SNIP_PIN_KEEP_DAYS` and `SNIP_PIN_TAP_MS` (non-numbers fall back to the defaults instead of bash errors), tolerates a corrupt double-tap state file, runs under `set -u -o pipefail`, and cannot leak its temp file on an early exit (#23).

## [0.1.0] – 2026-09-06

First tagged release. The tool was developed on `main` without version
numbers between 2026-09-02 and 2026-09-05; the review on 2026-09-06 filed
the issues below and fixed the ones in this milestone.

### Added
- Snip a region, a window or an element inside a window and pin it where it
  was taken; drag, zoom, fade, copy, save.
- Annotations on the pin: rectangle, arrow, pen, text, marker, blur.
- History: re-pin the last snip, thumbnail picker, kept snips, pin from the
  clipboard, double-tap to open the picker.
- One process for all pins; a second pin appears in about 50 ms.
- `VERSION` file and `snip-pin.sh --version` (#12).
- Test suite (`tests/`, pytest) for the display-free code and a third CI job
  that runs it (#13).
- `install.sh --uninstall`, and a hidden "Pin image" desktop entry so file
  managers offer *Open with → Pin image* for PNG, JPEG, WebP, BMP and GIF
  files (#11).
- `SNIP_ELEMENTS_BUDGET_MS` to tune the element-detection budget (#9).

### Fixed
- Element detection is bounded: a spreadsheet or a terminal full of
  box-drawing lines took up to 3 s with the screen frozen; the join now runs
  longest-first with a 150 ms budget and a pair window, and `snip-pin.sh`
  stops waiting after 0.6 s. An empty capture no longer makes the helper
  raise (#9).
- Copying or saving an annotated pin no longer leaves a `_annotated.png`
  next to the original: in the cache (where the history picker and `last`
  picked it up), in `kept/` (where it never expired) or in the user's folder
  for `pin FILE`. Leftovers from earlier versions are cleaned up and
  ignored (#5).
- Copy and save no longer crash, leaving the pin open, when `notify-send` is
  not installed (#6).
- A missing, truncated or non-image file (a snip deleted before `last`, a
  bad clipboard image, `pin FILE` on a text file) shows a "Cannot open" toast
  instead of crashing the viewer silently and making the next pin wait three
  seconds (#7).
- Thin snips (a single line of text) are no longer stretched to 40 px: the
  minimum size applies to the scale, not to each side, and annotations on
  such pins land where the pointer is. Tiny snips still open enlarged,
  uniformly (#8).
- Two pins saved within the same second no longer overwrite each other
  (`pin_..._2.png`), and a save that fails (unwritable folder, full disk)
  shows the error and keeps the pin open instead of dying with a
  traceback (#10).
- `install.sh` quotes the script path in the desktop entry, so a checkout
  under a path with spaces launches from the menu (#11).
- A JPEG or WebP opened with `pin FILE` is re-encoded as PNG when copied or
  saved; before, the raw file was sent to the clipboard labelled `image/png`.

[Unreleased]: https://github.com/felsenuboot/snip-pin/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/felsenuboot/snip-pin/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/felsenuboot/snip-pin/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/felsenuboot/snip-pin/releases/tag/v0.1.0
