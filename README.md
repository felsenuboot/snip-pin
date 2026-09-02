# snip-pin

Snipaste-style **snip and pin** for [Hyprland](https://hyprland.org).

Press a key, drag a region (or click a window to snap to it), and the screenshot
is pinned to the screen exactly where it was taken, floating above everything.
Move it around, zoom it with the mouse wheel, fade it, copy or save it. A copy of
every snip also lands in the clipboard.

Two small pieces, no daemon, no portal round trip:

- `snip-pin.sh` – freezes the screen, runs `slurp` (fed with window rectangles
  so windows highlight and snap), captures with `grim`, copies with `wl-copy`,
  and launches the viewer.
- `pin-view.py` – a ~200 line GTK4 window that shows the image 1:1 and asks
  Hyprland to place it at the capture position (Wayland apps cannot position
  themselves).

## Pin controls

| Action | Input |
|---|---|
| Move | drag with left mouse button |
| Zoom | mouse wheel (10 % steps), `Ctrl+0` resets |
| Opacity | `Ctrl` + wheel, `Ctrl+1` resets |
| Copy image | `Ctrl+C` or right-click menu |
| Save to screenshot folder | `Ctrl+S` or right-click menu |
| Close | `Esc` or double-click |

## Requirements

Arch package names; everything is standard Hyprland tooling:

```
sudo pacman -S --needed grim slurp wl-clipboard jq python-gobject gtk4 hyprpicker
```

`hyprpicker` is optional and only used to freeze the screen during selection.
`libnotify` (`notify-send`) is optional for the copy/save toasts.

Tested on Hyprland 0.56 (Lua config) with GTK 4.22. The placement call uses the
Lua dispatcher syntax, `hl.dsp.window.move(...)`; on older Hyprland releases
replace it in `pin-view.py` with `movewindowpixel exact X Y,address:...`.

## Install

```
git clone https://github.com/felsenuboot/snip-pin ~/code/snip-pin
```

Bind the script to a key and add a window rule so pins float above everything
without animations, blur, shadows or rounded corners.

Hyprland Lua config (0.56+):

```lua
hl.bind("PRINT", hl.dsp.exec_cmd("~/code/snip-pin/snip-pin.sh"), { description = "Snip a region and pin it" })

hl.window_rule({
    name = "snip-pin",
    match = { class = "^(snip-pin)$" },
    float = true,
    pin = true,
    no_anim = true,
    no_blur = true,
    no_shadow = true,
    rounding = 0,
})
```

Classic `hyprland.conf`:

```
bind = , PRINT, exec, ~/code/snip-pin/snip-pin.sh

windowrulev2 = float, class:^(snip-pin)$
windowrulev2 = pin, class:^(snip-pin)$
windowrulev2 = noanim, class:^(snip-pin)$
windowrulev2 = noblur, class:^(snip-pin)$
windowrulev2 = noshadow, class:^(snip-pin)$
windowrulev2 = rounding 0, class:^(snip-pin)$
```

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

## License

MIT
