# Decisions

Answers to the questions that come up about the shape of this tool, with the
numbers behind them. Measured on 2026-09-06 on Arch Linux, Hyprland 0.56.2,
GTK 4.22, Python 3.14, an RTX 2080 SUPER and one 3440×1440 output at scale 1.
Re-measure with `tests/` and the commands in the README before trusting them
on other hardware.

## Where the time goes

One snip, from the key press to the pin on screen:

| Stage | Time | Runs in |
|---|---|---|
| `grim` full-screen grab for element detection | 69 ms | C (grim) |
| `python3 -c "import numpy"` | 72 ms | interpreter start-up |
| element detection, typical desktop | 42 ms | numpy (C loops) |
| element detection, worst case (spreadsheet grid, noise) | ≤ 150 ms | bounded by the budget since 0.1.0; 2.9 s before |
| `hyprctl clients` + `jq` | 11 ms | C |
| slurp | until the click | C |
| `grim` region → PNG (`-l 1`) | 59 ms | C |
| `wl-copy` | 3 ms | C |
| first pin: `import gi` + `Gtk.init` | 150 ms | GTK + GL driver; ~120 ms of it is not Python |
| later pins: socket hand-over | 21 ms | Python without GTK |
| bare `python3` start | 11 ms | |

Before slurp appears: about 140 ms, bounded by the grab (grim and the numpy
import overlap). From the click to the pin: about 80 ms for the second pin
onwards, about 210 ms for the first.

Memory per process (from the 2026-09-02 measurement in #4): ~200 MB RSS, of
which ~110 MB is the NVIDIA GL driver, ~8 MB libgtk-4 and ~20 MB libpython
plus heap. All pins share one process.

## Rewrite in Rust (or C, Go, …)? No.

grim, slurp, wl-copy, hyprctl and GTK are already native. A compiled snip-pin
would remove the 72 ms numpy import and the 21 ms hand-over start-up and could
trim the first pin's GTK start-up to roughly 60 ms (the GL driver and the GTK
CSS still load). Best case: **about 90 ms saved per snip, out of ~280 ms**,
and about 10 MB of memory per process. The one real pathology, seconds of
element detection on grid-like screens, was algorithmic and was fixed in
Python (#9).

What would change the answer:

- packaging for other people as a distro package or a single binary: then
  Rust with `gtk4-rs`, which maps one to one onto `pin-view.py`;
- live element highlighting while moving the pointer (detection on every
  frame instead of once before slurp);
- moving the capture into the process (a wlr-screencopy client) to drop
  grim's PNG round-trip.

Cheaper wins inside Python, if the numbers ever matter: keep the detector
warm in the pin server (saves the 72 ms import), and capture with `-l 0`
(39 ms instead of 59 ms).

## Port to GNOME? Not as a port.

What snip-pin needs and what Mutter offers:

| Need | Hyprland today | GNOME Wayland |
|---|---|---|
| Frozen screen + region selection | hyprpicker + slurp (wlr-layer-shell, wlr-screencopy) | neither protocol exists in Mutter; `slurp` and `grim` do not run |
| Full-screen grab for element detection | `grim`, 70 ms | only the Screenshot portal (interactive dialog) or `org.gnome.Shell.Screenshot`, restricted to allow-listed callers since GNOME 41 |
| Window rectangles | `hyprctl clients -j` | not available to applications; only from a Shell extension |
| Place the pin at the capture position | `hyprctl dispatch` | impossible: Wayland apps cannot position toplevels and Mutter has no layer shell for third-party apps |
| Keep the pin above everything, undecorated | window rule | "always on top" needs a Shell extension |
| Clipboard that survives the process | `wl-copy` (wlr-data-control) | Mutter has no data-control; wl-clipboard needs a focus workaround, GTK's clipboard needs the process to stay alive |

Everything except the drawing code, the toolbar and the history picker is
compositor-specific, and the two features that make the tool worthwhile,
instant capture and a pin exactly where the region was, are exactly the ones
GNOME withholds from applications. A GNOME version would be a Shell extension
(capture, placement, always-on-top) plus this viewer: a new project sharing
perhaps 40 % of the code.

The realistic middle ground is the other wlroots-style compositors. Sway,
river and niri run grim, slurp and wl-copy unchanged and support the layer
shell; KDE Plasma supports the layer shell but not wlr-screencopy (so no
grim). What they need from snip-pin is a placement backend that does not go
through `hyprctl` (#28) and a window-list backend per compositor (#29).

## Port to macOS? No.

None of the stack exists there: no Wayland, no grim, slurp, wl-copy or
hyprctl, and GTK 4 through Homebrew runs but looks and feels foreign. Only
`snip-elements.py` and the cairo drawing code would carry over.

macOS provides natively what took the most work here: `screencapture -i`
gives the built-in interactive region and window selection with a frozen
screen; an `NSWindow` sets its own frame and `level = .floating`; the
pasteboard keeps the image after the process exits. A macOS "port" would be
a few hundred lines of Swift reusing the ideas, not the code, and that app
already exists several times: Snipaste itself ships for macOS, plus Shottr,
CleanShot X and PinPoint.

## Toplevel window, not a layer surface

A layer surface could position itself and would need no window rule, but it
has no app id in docks, cannot be pinned per workspace and would need
hand-made dragging. Pins are meant to behave like windows, so they are
windows and Hyprland places them. A layer-shell backend as an *option* for
other compositors is #28.

## Python

GTK 4 and the GL driver dominate start-up and memory; the language is not the
cost, and the single-process model removes the start-up for every pin but the
first. See the Rust section above for the numbers.
