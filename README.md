# snip-pin

Snipaste-style **snip and pin** for [Hyprland](https://hyprland.org).

Press a key, drag a region (or click a window, an image or a panel to snap to
it), and the screenshot is pinned to the screen exactly where it was taken,
floating above everything.
Move it around, zoom it with the mouse wheel, fade it, copy or save it. A copy of
every snip also lands in the clipboard.

Four small pieces, no daemon, no portal round trip:

- `snip-pin.sh` – freezes the screen, runs `slurp` (fed with window and
  element rectangles so they highlight and snap), captures with `grim`, copies
  with `wl-copy`, and launches the viewer. Click a window or an element, or
  drag a region; right-click or `Esc` aborts without taking a screenshot.
- `snip-elements.py` – finds rectangular elements *inside* windows (images,
  cards, panels, table cells) on a frame of the screen, the way Snipaste does:
  long horizontal and vertical luminance edges are joined into rectangles.
  Pure numpy, about 40 ms for a 3440×1440 frame, run while the screen freezes.
  slurp highlights the smallest rectangle under the pointer, so an element
  wins over its window. Elements whose edges do not contrast with their
  surroundings (a dark photo on a dark page) cannot be found this way.
- `pin-view.py` – a single-file GTK4 window that shows the image 1:1, asks
  Hyprland to place it at the capture position (Wayland apps cannot position
  themselves) and doubles as a small annotation editor.
- `pin-history.py` – a thumbnail picker for previous snips (see History).

## Pin controls

| Action | Input |
|---|---|
| Move | drag with left mouse button |
| Zoom | mouse wheel (10 % steps), `Ctrl+0` resets |
| Opacity | `Ctrl` + wheel, `Ctrl+1` resets |
| Copy image and close | `Ctrl+C`, double-click, or right-click |
| Save to screenshot folder | `Ctrl+S` or middle-click menu |
| Close without copying | `Esc` |
| Annotate | toolbar under the pin (on hover), or tool keys |

## Annotations

The pin is the editor: a toolbar appears under the pin while the pointer is
over it. Pick a tool and draw
with the left mouse button; with no tool selected the pin behaves exactly as
before (drag moves it, double- or right-click copies). Whatever is drawn is baked into
the image that `Ctrl+C` and `Ctrl+S` export. The original snip on disk stays
untouched.

| Tool | Key | Notes |
|---|---|---|
| Rectangle | `R` | outline, drag |
| Arrow | `A` | drag from tail to head |
| Pen | `P` | freehand |
| Text | `T` | click to place, type, `Enter` commits, `Esc` cancels |
| Marker | `M` | wide, semi-transparent highlighter |
| Blur | `B` | pixelates a rectangle, for hiding secrets |

| Action | Input |
|---|---|
| Colour | `1`–`7` or the swatches (red, orange, yellow, green, blue, white, black) |
| Stroke width | `[` / `]` or the three dots (thin, normal, thick); also sets text size and mosaic block size |
| Undo / redo | `Ctrl+Z` / `Ctrl+Shift+Z` |
| Deselect tool | press its key again, click its button, or `Esc`; dragging then moves the pin as usual |
| Close | `Esc` with no tool selected |

Annotations are stored in image coordinates, so zooming the pin scales them
along with the image. The border turns blue while a tool is selected.

## History

Every snip is kept in `~/.cache/snip-pin` for seven days (set
`SNIP_PIN_KEEP_DAYS` in the environment to change that, `0` keeps them
forever). The file name carries the capture position, so a snip pinned again
lands where it was taken. Three subcommands bring snips back without taking a
new screenshot:

| Command | What it does |
|---|---|
| `snip-pin.sh last` | pin the newest snip again |
| `snip-pin.sh history` | open a thumbnail grid of all cached snips, newest first |
| `snip-pin.sh clipboard` | pin the image in the clipboard, centred on the screen |

In the history picker: click or `Enter` pins the selected snip, arrow keys
move the selection, `Delete` removes a snip from the cache, `Esc` closes.
Clipboard pins are added to the cache too, so they show up in the history.

## Requirements

Arch package names; everything is standard Hyprland tooling:

```
sudo pacman -S --needed grim slurp wl-clipboard jq python-gobject gtk4 hyprpicker python-numpy
```

`hyprpicker` is optional and only used to freeze the screen during selection.
`python-numpy` is optional and only used for element snapping; without it only
windows snap.
`libnotify` (`notify-send`) is optional for the copy/save toasts.

Tested on Hyprland 0.56 (Lua config) with GTK 4.22. The placement call uses the
Lua dispatcher syntax, `hl.dsp.window.move(...)`; on older Hyprland releases
replace it in `pin-view.py` with `movewindowpixel exact X Y,address:...`.

## Install

```
git clone https://github.com/felsenuboot/snip-pin ~/.local/share/snip-pin
~/.local/share/snip-pin/install.sh
```

`install.sh` puts a desktop entry and icon into `~/.local/share` so docks and
taskbars show a proper icon for pins; it is optional and safe to rerun.

Bind the script to a key and add a window rule so pins float above everything
without animations, blur, shadows or rounded corners.

Hyprland Lua config (0.56+):

```lua
hl.bind("PRINT", hl.dsp.exec_cmd("~/.local/share/snip-pin/snip-pin.sh"), { description = "Snip a region and pin it" })
hl.bind("SHIFT + PRINT", hl.dsp.exec_cmd("~/.local/share/snip-pin/snip-pin.sh last"), { description = "Pin the last snip again" })
hl.bind("SUPER + PRINT", hl.dsp.exec_cmd("~/.local/share/snip-pin/snip-pin.sh history"), { description = "Snip history" })
hl.bind("CTRL + PRINT", hl.dsp.exec_cmd("~/.local/share/snip-pin/snip-pin.sh clipboard"), { description = "Pin the clipboard image" })

hl.window_rule({
    name = "snip-pin",
    match = { class = "^(snip-pin)$" },
    float = true,
    pin = true,
    no_anim = true,
    no_blur = true,
    no_shadow = true,
    border_size = 0,
    rounding = 0,
})
```

Classic `hyprland.conf`:

```
bind = , PRINT, exec, ~/.local/share/snip-pin/snip-pin.sh
bind = SHIFT, PRINT, exec, ~/.local/share/snip-pin/snip-pin.sh last
bind = SUPER, PRINT, exec, ~/.local/share/snip-pin/snip-pin.sh history
bind = CTRL, PRINT, exec, ~/.local/share/snip-pin/snip-pin.sh clipboard

windowrulev2 = float, class:^(snip-pin)$
windowrulev2 = pin, class:^(snip-pin)$
windowrulev2 = noanim, class:^(snip-pin)$
windowrulev2 = noblur, class:^(snip-pin)$
windowrulev2 = noshadow, class:^(snip-pin)$
windowrulev2 = noborder, class:^(snip-pin)$
windowrulev2 = rounding 0, class:^(snip-pin)$
```

The pin draws its own 2 px border (orange by default) so it stands out on
static pages; set `SNIP_PIN_BORDER="#89b4fa"` in the environment to change it.

The save target is read from `~/.config/ml4w/settings/screenshot-folder` if
present (ML4W dotfiles), otherwise `~/Pictures`.

## Why not Flameshot?

Flameshot has a pin feature, but on Wayland its pin widget cannot size or place
its own window: pins open centred, grow when zooming in but never shrink again,
and end up as a huge transparent window around a small image. The capture also
goes through the screenshot portal, which on Hyprland shells out to `grim` with
full PNG compression and costs over a second on a large screen. This tool skips
both problems.

## Testing without a mouse

`SNIP_GEOM=400x300+600+400 ./snip-pin.sh` pins that region directly.
`SNIP_NO_ELEMENTS=1 ./snip-pin.sh` snaps to windows only.
`grim -s 1 -t ppm - | ./snip-elements.py --debug out.png` draws the detected
edges and rectangles onto a copy of the screen for tuning.

## License

MIT
