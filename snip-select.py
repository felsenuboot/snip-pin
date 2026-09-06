#!/usr/bin/env python3
"""Selection overlay for snip-pin (GTK 4 + gtk4-layer-shell): Snipaste's snip mode.

usage: snip-select.py FRAME.ppm < input.json  ->  prints WxH+X+Y, exit 0
       exit 1: aborted (Esc, right-click); exit 3: refresh requested but not possible in-process
       exit 127: gtk4-layer-shell is not available (the caller falls back to slurp)

input.json: {"monitors": [{"name", "x", "y", "width", "height", "scale", "transform"}, ...],
             "windows": [[x, y, w, h], ...], "elements": [[x, y, w, h], ...],
             "areas": ["WxH+X+Y", ...], "select": "WxH+X+Y" (optional, a selection to start with),
             "frames": [{"png": path, "json": path, "time": unix seconds}, ...]  (earlier screens, newest first),
             "settings": {"border": "#rrggbb[aa]", "width": 1, "mask": "#rrggbbaa", "fill": "#rrggbbaa",
                          "size": 1, "hints": 1, "magnify": 9, "adjust": 0, "cursor": 0}}

One layer-shell window per monitor draws its part of the frozen frame (FRAME is
the whole virtual screen at scale 1, as grim -s 1 produces it), the dim mask,
the selection with its size label and anchors, the window / element under the
pointer, a magnifier with the pixel colour, and a key-hint panel.

  drag                 select freely           click            take the window / element under the pointer
  anchors / inside     resize / move           arrows           move by 1 px (Shift: 10, Ctrl: resize)
  hover / grab an anchor, then arrows or W A S D move that corner or edge
  Enter / double-click confirm                 Esc / right-click abort
  Tab                  detection: both / windows / elements / off
  1 / 2 / wheel        parent / child element  W A S D          move the pointer by 1 px
  Ctrl+A               this monitor, again: everything
  R / Shift+R          previous capture areas  F5               refresh the frozen frame
  , / .                earlier / later frozen screen (the frames kept by earlier snips)
  C                    copy the colour under the pointer        Shift  HEX / RGB
  Q                    adjust mode on / off (a drag waits for Enter instead of capturing at once)
"""
import ctypes
import json
import math
import subprocess
import sys
import warnings

# gtk4-layer-shell interposes Wayland symbols, so its library must be in the
# process before GTK loads libwayland-client (gtk4-layer-shell/linking.md); the
# typelib alone loads GTK first. Without the library the script exits 127 and
# snip-pin.sh falls back to slurp.
try:
    ctypes.CDLL("libgtk4-layer-shell.so.0", mode=ctypes.RTLD_GLOBAL)
except OSError:
    pass
warnings.filterwarnings("ignore", category=DeprecationWarning)
import cairo  # noqa: E402
import gi  # noqa: E402

try:
    gi.require_version("Gtk4LayerShell", "1.0")
    from gi.repository import Gtk4LayerShell as LayerShell
except (ValueError, ImportError):
    LayerShell = None
gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gtk, Pango, PangoCairo  # noqa: E402

MODES = ("both", "windows", "elements", "off")
ANCHOR_R = 5                       # handle radius in px
GRAB = 8                           # how close to an edge / handle a press counts
MAG_CELLS = 21                     # magnifier: pixels per side (odd, the centre is the pointer)
HINTS = [("Enter", "confirm"), ("Esc", "abort"), ("drag / click", "select / take the window under the pointer"),
         ("arrows", "move 1 px (Shift 10, Ctrl resize)"), ("anchor + arrows / WASD", "move that corner or edge"),
         ("Tab", "windows / elements / both / off"),
         ("1 2 / wheel", "parent / child element"), ("Ctrl+A", "this monitor, again: everything"),
         ("R Shift+R", "previous capture areas"), ("W A S D", "move the pointer by 1 px"),
         ("C", "copy the colour"), ("Shift", "HEX / RGB"), ("F5", "refresh the frame"),
         ("Q", "adjust mode: a drag waits for Enter"), (", .", "earlier / later screen")]


def parse_color(spec, default):
    """#rrggbb or #rrggbbaa -> (r, g, b, a) in 0..1."""
    s = (spec or "").strip()
    if len(s) in (7, 9) and s.startswith("#") and all(c in "0123456789abcdefABCDEF" for c in s[1:]):
        r, g, b = (int(s[i:i + 2], 16) / 255 for i in (1, 3, 5))
        a = int(s[7:9], 16) / 255 if len(s) == 9 else 1.0
        return r, g, b, a
    return default


def parse_geom(text):
    """'WxH+X+Y' -> (x, y, w, h) or None."""
    try:
        wh, x, y = text.strip().split("+")
        w, h = wh.split("x")
        return int(x), int(y), int(w), int(h)
    except (ValueError, AttributeError):
        return None


def format_geom(rect):
    x, y, w, h = rect
    return f"{w}x{h}+{x}+{y}"


def norm(x0, y0, x1, y1):
    """Two corners -> (x, y, w, h) with integer edges and at least 1 px."""
    x, y = int(round(min(x0, x1))), int(round(min(y0, y1)))
    w, h = int(round(max(x0, x1))) - x, int(round(max(y0, y1))) - y
    return x, y, max(1, w), max(1, h)


def contains(rect, px, py):
    x, y, w, h = rect
    return x <= px < x + w and y <= py < y + h


def candidates(rects, px, py):
    """Rects under the point, smallest first (the way slurp picks, plus the parents behind it)."""
    hits = [r for r in rects if contains(r, px, py)]
    return sorted(hits, key=lambda r: r[2] * r[3])


def clamp_rect(rect, bounds):
    """Keep rect inside bounds (x, y, w, h), shrinking when it does not fit."""
    x, y, w, h = rect
    bx, by, bw, bh = bounds
    w, h = min(w, bw), min(h, bh)
    x = max(bx, min(x, bx + bw - w))
    y = max(by, min(y, by + bh - h))
    return x, y, w, h


def monitor_rect(m):
    """Logical rectangle of a hyprctl monitor entry (scale and rotation applied)."""
    scale = m.get("scale") or 1
    w, h = m.get("width", 0) / scale, m.get("height", 0) / scale
    if m.get("transform", 0) % 2 == 1:
        w, h = h, w
    return int(m.get("x", 0)), int(m.get("y", 0)), int(round(w)), int(round(h))


def anchor_points(rect):
    """The eight handles: corners and edge midpoints, with a tag for the cursor / resize logic."""
    x, y, w, h = rect
    return {"nw": (x, y), "n": (x + w / 2, y), "ne": (x + w, y), "e": (x + w, y + h / 2),
            "se": (x + w, y + h), "s": (x + w / 2, y + h), "sw": (x, y + h), "w": (x, y + h / 2)}


def resize_rect(rect, tag, px, py):
    """Move the edge(s) named by the anchor tag to the pointer."""
    x, y, w, h = rect
    x0, y0, x1, y1 = x, y, x + w, y + h
    if "w" in tag:
        x0 = px
    if "e" in tag:
        x1 = px
    if "n" in tag:
        y0 = py
    if "s" in tag:
        y1 = py
    return norm(x0, y0, x1, y1)


def move_anchor(rect, tag, dx, dy):
    """Move the edge(s) an anchor stands for by (dx, dy); a corner moves both, a midpoint one."""
    x, y, w, h = rect
    x0, y0, x1, y1 = x, y, x + w, y + h
    if "w" in tag:
        x0 += dx
    if "e" in tag:
        x1 += dx
    if "n" in tag:
        y0 += dy
    if "s" in tag:
        y1 += dy
    return norm(x0, y0, x1, y1)


def anchor_near(rect, px, py, grab=GRAB):
    """The tag of the anchor within `grab` px of the point, or None."""
    for tag, (ax, ay) in anchor_points(rect).items():
        if abs(px - ax) <= grab and abs(py - ay) <= grab:
            return tag
    return None


def frame_label(age_seconds):
    """'now', '12 s ago', '3 min ago', '2 h ago'."""
    if age_seconds is None:
        return "now"
    if age_seconds < 60:
        return f"{int(age_seconds)} s ago"
    if age_seconds < 3600:
        return f"{int(age_seconds // 60)} min ago"
    return f"{age_seconds / 3600:.1f} h ago"


def pixel_at(pixbuf, x, y):
    """(r, g, b) 0..255 of a pixbuf pixel, or None outside."""
    if not (0 <= x < pixbuf.get_width() and 0 <= y < pixbuf.get_height()):
        return None
    n, stride = pixbuf.get_n_channels(), pixbuf.get_rowstride()
    data = pixbuf.get_pixels()
    i = y * stride + x * n
    return data[i], data[i + 1], data[i + 2]


def color_text(rgb, hex_mode):
    r, g, b = rgb
    return f"#{r:02x}{g:02x}{b:02x}" if hex_mode else f"rgb({r}, {g}, {b})"


class Selector:
    """The shared selection state; one Overlay window per monitor draws it."""

    def __init__(self, app, frame, data, frame_path=None):
        self.app = app
        self.frame = frame
        self.frame_file = frame_path       # F5 regrabs into this file; the caller crops from it
        self.blank = False                 # True while the overlay paints nothing so grim sees the screen
        self.live = (frame, [tuple(r) for r in data.get("windows", []) if len(r) == 4],
                     [tuple(r) for r in data.get("elements", []) if len(r) == 4])   # the current screen
        self.frames = [f for f in data.get("frames", []) if isinstance(f, dict) and f.get("png")]
        self.frame_idx = -1                # -1 = the live frame, else an index into self.frames
        self.frame_path = None             # the kept frame in use, printed after the geometry
        self.origin = None                 # top-left of the virtual screen (the frame's (0, 0))
        self.monitors = [monitor_rect(m) for m in data.get("monitors", [])]
        if not self.monitors:
            self.monitors = [(0, 0, frame.get_width(), frame.get_height())]
        self.names = [m.get("name", "") for m in data.get("monitors", [])]
        ox = min(r[0] for r in self.monitors)
        oy = min(r[1] for r in self.monitors)
        self.origin = (ox, oy)
        right = max(r[0] + r[2] for r in self.monitors)
        bottom = max(r[1] + r[3] for r in self.monitors)
        self.bounds = (ox, oy, right - ox, bottom - oy)
        self.windows = [tuple(r) for r in data.get("windows", []) if len(r) == 4]
        self.elements = [tuple(r) for r in data.get("elements", []) if len(r) == 4]
        self.areas = [g for g in (parse_geom(a) for a in data.get("areas", [])) if g]
        st = data.get("settings", {})
        self.border = parse_color(st.get("border"), (0.53, 0.53, 0.53, 1.0))
        self.line_w = max(1, int(st.get("width") or 1))
        self.mask = parse_color(st.get("mask"), (0, 0, 0, 0.5))
        self.fill = parse_color(st.get("fill"), (0, 0, 0, 0))
        self.show_size = str(st.get("size", 1)) not in ("0", "false")
        self.show_hints = str(st.get("hints", 1)) not in ("0", "false")
        self.magnify = max(2, min(20, int(st.get("magnify") or 9)))
        self.adjust = str(st.get("adjust", 0)) not in ("0", "false")
        self.mode = 0                                  # index into MODES
        self.pointer = None                            # global logical coordinates
        self.selection = parse_geom(data.get("select", "")) if data.get("select") else None
        self.press = None                              # (kind, x, y, rect at press, anchor tag)
        self.active_anchor = None                      # hovered / grabbed anchor: arrows and WASD move it
        self.dragging = False
        self.depth = 0                                 # parent / child level among the candidates
        self.area_idx = -1
        self.hex_mode = True
        self.screen_step = 0                           # Ctrl+A: 1 = this monitor, 2 = everything
        self.result = None
        self.windows_ = []                             # Overlay instances
        for i, rect in enumerate(self.monitors):
            self.windows_.append(Overlay(self, i, rect))

    # ---- rectangles under the pointer ---------------------------------------------
    def rects(self):
        mode = MODES[self.mode]
        if mode == "both":
            return self.windows + self.elements
        if mode == "windows":
            return self.windows
        if mode == "elements":
            return self.elements
        return []

    def candidate(self):
        if self.pointer is None:
            return None
        cands = candidates(self.rects(), *self.pointer)
        if not cands:
            return None
        return cands[min(self.depth, len(cands) - 1)]

    # ---- state changes -------------------------------------------------------------
    def redraw(self):
        for w in self.windows_:
            w.area.queue_draw()

    def set_pointer(self, x, y):
        self.pointer = (x, y)
        if self.selection and self.press is None:
            tag = anchor_near(self.selection, x, y)
            if tag is not None:
                self.active_anchor = tag                # sticky: keys keep moving it after the pointer leaves
        self.redraw()

    def finish(self, rect):
        rect = clamp_rect(rect, self.bounds)
        self.result = format_geom(rect) + (f"\n{self.frame_path}" if self.frame_path else "")
        self.app.quit()

    def show_frame(self, step):
        """`,` / `.`: an earlier or later kept screen instead of the live one; windows and
        elements come from the JSON saved with that frame."""
        if not self.frames:
            return
        idx = max(-1, min(len(self.frames) - 1, self.frame_idx + step))
        if idx == self.frame_idx:
            return
        meta = {}
        if idx == -1:
            frame, windows, elements, path = self.live[0], self.live[1], self.live[2], None
        else:
            entry = self.frames[idx]
            try:
                frame = GdkPixbuf.Pixbuf.new_from_file(entry["png"])
            except GLib.Error:
                return
            windows, elements = [], []
            try:
                with open(entry.get("json", ""), encoding="utf-8") as f:
                    meta = json.load(f)
                windows = [tuple(r) for r in meta.get("windows", []) if len(r) == 4]
                elements = [tuple(r) for r in meta.get("elements", []) if len(r) == 4]
            except (OSError, ValueError):
                pass
            path = entry["png"]
        self.frame_idx, self.frame, self.frame_path = idx, frame, path
        self.windows, self.elements = windows, elements
        self.depth = 0
        self.active_anchor = None
        # the area that was snipped from that screen comes back as the selection
        old = parse_geom(meta.get("select", "")) if idx >= 0 else None
        if old:
            self.selection = clamp_rect(old, self.bounds)
        elif idx >= 0:
            self.selection = None
        for w in self.windows_:
            w.set_crop()
        self.redraw()

    def refresh(self):
        """F5: paint nothing for a couple of frames, grab the screen (into the frame
        file the caller crops from), show the new frame. Unmapping the overlay and
        grabbing from outside raced the compositor and captured the old overlay."""
        if self.blank or self.frame_idx >= 0:
            if self.frame_idx >= 0:
                self.show_frame(-len(self.frames))          # back to the live screen first
            if self.blank:
                return
        self.blank = True
        self.redraw()
        GLib.timeout_add(120, self._grab)

    def _grab(self):
        path = self.frame_file or "/tmp/snip-pin-frame.ppm"
        try:
            subprocess.run(["grim", "-s", "1", "-t", "ppm", path], check=True, timeout=5,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            frame = GdkPixbuf.Pixbuf.new_from_file(path)
        except (OSError, subprocess.SubprocessError, GLib.Error) as e:
            print(f"snip-select: refresh: {e}", file=sys.stderr)
            frame = None
        self.blank = False
        if frame is not None:
            self.frame = frame
            self.live = (frame, self.live[1], self.live[2])
            for w in self.windows_:
                w.set_crop()
        self.redraw()
        return False

    def frame_age(self):
        if self.frame_idx < 0:
            return None
        try:
            return max(0.0, __import__("time").time() - float(self.frames[self.frame_idx].get("time", 0)))
        except (TypeError, ValueError):
            return None

    def abort(self, code=1):
        self.result = format_geom(self.selection) if code == 3 and self.selection else None
        self.exit_code = code
        self.app.quit()

    def confirm(self):
        if self.selection:
            self.finish(self.selection)
        elif self.candidate():
            self.finish(self.candidate())

    def nudge(self, dx, dy, resize=False):
        if not self.selection:
            return
        x, y, w, h = self.selection
        if self.active_anchor is not None:
            self.selection = clamp_rect(move_anchor(self.selection, self.active_anchor, dx, dy), self.bounds)
            self.redraw()
            return
        if resize:
            self.selection = clamp_rect((x, y, max(1, w + dx), max(1, h + dy)), self.bounds)
        else:
            self.selection = clamp_rect((x + dx, y + dy, w, h), self.bounds)
        self.redraw()

    def whole_screen(self):
        """Ctrl+A: the monitor under the pointer, a second time everything."""
        self.screen_step = 1 if self.screen_step != 1 else 2
        if self.screen_step == 1 and self.pointer:
            for rect in self.monitors:
                if contains(rect, *self.pointer):
                    self.selection = rect
                    break
            else:
                self.selection = self.monitors[0]
        else:
            self.selection = self.bounds
        self.redraw()

    def cycle_area(self, step):
        if not self.areas:
            return
        self.area_idx = (self.area_idx + step) % len(self.areas)
        self.selection = clamp_rect(self.areas[self.area_idx], self.bounds)
        self.redraw()

    def move_pointer(self, dx, dy):
        """W A S D: ask the compositor to move the cursor by a pixel (Hyprland)."""
        if self.pointer is None:
            return
        x, y = self.pointer[0] + dx, self.pointer[1] + dy
        for cmd in (["hyprctl", "dispatch", f"hl.dsp.cursor.move({{ x = {x}, y = {y} }})"],
                    ["hyprctl", "dispatch", "movecursor", str(x), str(y)]):
            try:
                if subprocess.run(cmd, capture_output=True, text=True, timeout=1).stdout.strip() == "ok":
                    break
            except (OSError, subprocess.SubprocessError):
                break
        self.set_pointer(x, y)

    def copy_color(self):
        rgb = self.pixel_under_pointer()
        if rgb is None:
            return
        text = color_text(rgb, self.hex_mode)
        try:
            subprocess.run(["wl-copy", text], timeout=2)
            subprocess.Popen(["notify-send", "-t", "1500", "Snip", f"Colour {text} copied"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError:
            pass

    def pixel_under_pointer(self):
        if self.pointer is None:
            return None
        return pixel_at(self.frame, int(self.pointer[0] - self.origin[0]), int(self.pointer[1] - self.origin[1]))

    # ---- keys (shared by every window) ------------------------------------------------
    def on_key(self, keyval, state):
        shift = state & Gdk.ModifierType.SHIFT_MASK
        ctrl = state & Gdk.ModifierType.CONTROL_MASK
        name = Gdk.keyval_name(keyval) or ""
        lower = Gdk.keyval_to_lower(keyval)
        if keyval == Gdk.KEY_Escape:
            self.abort(1)
        elif keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter, Gdk.KEY_space):
            self.confirm()
        elif keyval == Gdk.KEY_Tab or keyval == Gdk.KEY_ISO_Left_Tab:
            self.mode = (self.mode + 1) % len(MODES)
            self.depth = 0
            self.redraw()
        elif lower in (Gdk.KEY_1, Gdk.KEY_2):
            self.depth = max(0, self.depth + (1 if lower == Gdk.KEY_1 else -1))
            self.redraw()
        elif ctrl and lower == Gdk.KEY_a:
            self.whole_screen()
        elif lower == Gdk.KEY_r:
            self.cycle_area(-1 if shift else 1)
        elif lower == Gdk.KEY_c and not ctrl:
            self.copy_color()
        elif keyval in (Gdk.KEY_Shift_L, Gdk.KEY_Shift_R):
            self.hex_mode = not self.hex_mode
            self.redraw()
        elif keyval == Gdk.KEY_F5:
            self.refresh()
        elif lower == Gdk.KEY_q:
            self.adjust = not self.adjust
            self.redraw()
        elif keyval == Gdk.KEY_comma:
            self.show_frame(1)                              # earlier
        elif keyval == Gdk.KEY_period:
            self.show_frame(-1)                             # later, back to now
        elif name in ("Left", "Right", "Up", "Down"):
            step = 10 if shift else 1
            dx = {"Left": -step, "Right": step}.get(name, 0)
            dy = {"Up": -step, "Down": step}.get(name, 0)
            self.nudge(dx, dy, resize=bool(ctrl))
        elif lower in (Gdk.KEY_w, Gdk.KEY_a, Gdk.KEY_s, Gdk.KEY_d) and not ctrl:
            dx, dy = {Gdk.KEY_a: -1, Gdk.KEY_d: 1}.get(lower, 0), {Gdk.KEY_w: -1, Gdk.KEY_s: 1}.get(lower, 0)
            step = 10 if shift else 1
            if self.selection and self.active_anchor is not None:
                self.nudge(dx * step, dy * step)        # W A S D move the active anchor, like the arrows
            else:
                self.move_pointer(dx, dy)
        else:
            return False
        return True


class Overlay(Gtk.Window):
    """One monitor's share of the selection UI."""

    def __init__(self, sel, index, rect):
        super().__init__(application=sel.app, title="snip-pin-select")
        self.sel = sel
        self.index = index
        self.rect = rect                                  # logical monitor rectangle
        self.set_decorated(False)
        if LayerShell is not None:
            LayerShell.init_for_window(self)
            LayerShell.set_namespace(self, "snip-pin-select")
            LayerShell.set_layer(self, LayerShell.Layer.OVERLAY)
            LayerShell.set_exclusive_zone(self, -1)
            LayerShell.set_keyboard_mode(self, LayerShell.KeyboardMode.EXCLUSIVE)
            for edge in (LayerShell.Edge.TOP, LayerShell.Edge.BOTTOM, LayerShell.Edge.LEFT, LayerShell.Edge.RIGHT):
                LayerShell.set_anchor(self, edge, True)
            mon = self.gdk_monitor()
            if mon is not None:
                LayerShell.set_monitor(self, mon)
        self.set_default_size(rect[2], rect[3])
        css = Gtk.CssProvider()
        css.load_from_string("window.snip-select { background: transparent; }")
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), css,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self.add_css_class("snip-select")
        self.area = Gtk.DrawingArea()
        self.area.set_draw_func(self.draw)
        self.area.set_cursor(Gdk.Cursor.new_from_name("crosshair"))
        self.set_child(self.area)

        motion = Gtk.EventControllerMotion()
        motion.connect("motion", lambda c, x, y: self.sel.set_pointer(self.gx(x), self.gy(y)))
        motion.connect("enter", lambda c, x, y: self.sel.set_pointer(self.gx(x), self.gy(y)))
        self.area.add_controller(motion)
        click = Gtk.GestureClick(button=0)
        click.connect("pressed", self.on_press)
        click.connect("released", self.on_release)
        self.area.add_controller(click)
        drag = Gtk.GestureDrag(button=1)
        drag.connect("drag-update", self.on_drag_update)
        self.area.add_controller(drag)
        scroll = Gtk.EventControllerScroll(flags=Gtk.EventControllerScrollFlags.VERTICAL)
        scroll.connect("scroll", self.on_scroll)
        self.area.add_controller(scroll)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", lambda c, kv, kc, st: self.sel.on_key(kv, st))
        self.add_controller(keys)
        self.set_crop()

    def set_crop(self):
        """The current frame cropped to this monitor (drawn at 1:1)."""
        sel, rect = self.sel, self.rect
        fx, fy = rect[0] - sel.origin[0], rect[1] - sel.origin[1]
        fw = max(1, min(rect[2], sel.frame.get_width() - fx))
        fh = max(1, min(rect[3], sel.frame.get_height() - fy))
        self.crop = sel.frame.new_subpixbuf(fx, fy, fw, fh) if fx >= 0 and fy >= 0 and fw > 0 and fh > 0 else sel.frame

    def gdk_monitor(self):
        display = Gdk.Display.get_default()
        mons = display.get_monitors()
        name = self.sel.names[self.index] if self.index < len(self.sel.names) else ""
        for i in range(mons.get_n_items()):
            m = mons.get_item(i)
            if name and m.get_connector() == name:
                return m
        return mons.get_item(self.index) if self.index < mons.get_n_items() else None

    # global <-> local
    def gx(self, x):
        return x + self.rect[0]

    def gy(self, y):
        return y + self.rect[1]

    def lx(self, x):
        return x - self.rect[0]

    def ly(self, y):
        return y - self.rect[1]

    # ---- input ------------------------------------------------------------------
    def on_press(self, gesture, n, x, y):
        button = gesture.get_current_button()
        gx, gy = self.gx(x), self.gy(y)
        if button == 3:
            self.sel.abort(1)
            return
        if button == 2:
            self.sel.confirm()
            return
        if n == 2 and self.sel.selection:
            self.sel.confirm()
            return
        sel = self.sel.selection
        kind, tag = "new", None
        if sel:
            tag = anchor_near(sel, gx, gy)
            if tag is not None:
                kind = "resize"
            elif contains(sel, gx, gy):
                kind = "move"
        self.sel.active_anchor = tag                    # a grabbed anchor stays active; elsewhere clears it
        self.sel.press = (kind, gx, gy, sel, tag)
        self.sel.dragging = False

    def on_drag_update(self, gesture, dx, dy):
        p = self.sel.press
        if p is None:
            return
        kind, sx, sy, start, tag = p
        gx, gy = sx + dx, sy + dy
        if not self.sel.dragging and abs(dx) < 3 and abs(dy) < 3:
            return
        self.sel.dragging = True
        if kind == "new":
            self.sel.selection = norm(sx, sy, gx, gy)
        elif kind == "move":
            self.sel.selection = clamp_rect((start[0] + int(round(dx)), start[1] + int(round(dy)), start[2], start[3]),
                                            self.sel.bounds)
        elif kind == "resize":
            self.sel.selection = resize_rect(start, tag, gx, gy)
        self.sel.set_pointer(gx, gy)

    def on_release(self, gesture, n, x, y):
        p, self.sel.press = self.sel.press, None
        if p is None or gesture.get_current_button() != 1:
            return
        kind = p[0]
        if not self.sel.dragging:
            if kind == "new":                                   # a click: take the window / element
                cand = self.sel.candidate()
                if cand:
                    self.sel.selection = cand
                    if not self.sel.adjust:
                        self.sel.finish(cand)
                        return
                elif self.sel.selection and not contains(self.sel.selection, p[1], p[2]):
                    self.sel.selection = None                   # a click outside: start over
            self.sel.redraw()
            return
        self.sel.dragging = False
        if kind == "new" and not self.sel.adjust:
            self.sel.finish(self.sel.selection)
            return
        self.sel.redraw()

    def on_scroll(self, ctrl, dx, dy):
        self.sel.depth = max(0, self.sel.depth + (1 if dy > 0 else -1))
        self.sel.redraw()
        return True

    # ---- drawing ------------------------------------------------------------------
    def draw(self, area, cr, w, h):
        sel = self.sel
        if sel.blank:                                       # F5 in progress: let the screen through
            return
        Gdk.cairo_set_source_pixbuf(cr, self.crop, 0, 0)
        cr.paint()
        # dim everything but the selection (or the candidate while nothing is selected)
        shown = sel.selection or (sel.candidate() if sel.press is None else None)
        cr.set_source_rgba(*sel.mask)
        cr.rectangle(0, 0, w, h)
        if shown:
            cr.rectangle(self.lx(shown[0]), self.ly(shown[1]), shown[2], shown[3])
            cr.set_fill_rule(cairo.FILL_RULE_EVEN_ODD)
        cr.fill()
        if shown:
            x, y, sw, sh = self.lx(shown[0]), self.ly(shown[1]), shown[2], shown[3]
            if sel.fill[3] > 0:
                cr.set_source_rgba(*sel.fill)
                cr.rectangle(x, y, sw, sh)
                cr.fill()
            cr.set_source_rgba(*sel.border)
            cr.set_line_width(sel.line_w)
            half = sel.line_w / 2
            cr.rectangle(x - half, y - half, sw + sel.line_w, sh + sel.line_w)
            if sel.selection is None:
                cr.set_dash([6, 4])
            cr.stroke()
            cr.set_dash([])
            if sel.selection:
                for tag, (ax, ay) in anchor_points(shown).items():
                    active = tag == sel.active_anchor
                    cr.arc(self.lx(ax), self.ly(ay), ANCHOR_R + (2 if active else 0), 0, 2 * math.pi)
                    cr.set_source_rgba(*(sel.border[:3] if active else (0.2, 0.55, 0.95)), 1)
                    cr.fill_preserve()
                    cr.set_source_rgba(1, 1, 1, 1)
                    cr.set_line_width(1.5)
                    cr.stroke()
            if sel.show_size:
                self.label(cr, f"{sw} × {sh} px", x, y - 8, above=True)
        here = sel.pointer is not None and contains(self.rect, *sel.pointer)
        if here and (sel.press is None or sel.dragging):
            self.draw_magnifier(cr, w, h)
        if sel.show_hints and sel.pointer is not None and contains(self.rect, *sel.pointer):
            self.draw_hints(cr, w, h)

    def label(self, cr, text, x, y, above=False, font="Sans Bold 11px"):
        layout = PangoCairo.create_layout(cr)
        layout.set_font_description(Pango.FontDescription.from_string(font))
        layout.set_text(text, -1)
        _ink, logical = layout.get_pixel_extents()
        pad = 4
        bw, bh = logical.width + 2 * pad, logical.height + 2 * pad
        if above:
            y = y - bh
        x = max(2, min(x, self.rect[2] - bw - 2))
        y = max(2, y)
        cr.set_source_rgba(0, 0, 0, 0.7)
        cr.rectangle(x, y, bw, bh)
        cr.fill()
        cr.set_source_rgb(1, 1, 1)
        cr.move_to(x + pad, y + pad)
        PangoCairo.show_layout(cr, layout)
        return bw, bh

    def draw_magnifier(self, cr, w, h):
        sel = self.sel
        px, py = int(sel.pointer[0]), int(sel.pointer[1])
        cells, z = MAG_CELLS, sel.magnify
        size = cells * z
        half = cells // 2
        fx, fy = px - sel.origin[0] - half, py - sel.origin[1] - half
        lx, ly = self.lx(px), self.ly(py)
        ox = lx + 20 if lx + 20 + size < w else lx - 20 - size
        oy = ly + 20 if ly + 20 + size + 70 < h else ly - 20 - size - 70
        ox, oy = max(0, ox), max(0, oy)
        cr.save()
        cr.rectangle(ox, oy, size, size)
        cr.clip()
        cr.set_source_rgb(0.1, 0.1, 0.1)
        cr.paint()
        frame = sel.frame
        sx0, sy0 = max(0, fx), max(0, fy)
        sx1, sy1 = min(frame.get_width(), fx + cells), min(frame.get_height(), fy + cells)
        if sx1 > sx0 and sy1 > sy0:
            sub = frame.new_subpixbuf(sx0, sy0, sx1 - sx0, sy1 - sy0)
            cr.translate(ox + (sx0 - fx) * z, oy + (sy0 - fy) * z)
            cr.scale(z, z)
            Gdk.cairo_set_source_pixbuf(cr, sub, 0, 0)
            cr.get_source().set_filter(cairo.FILTER_NEAREST)
            cr.paint()
        cr.restore()
        # grid, crosshair, frame
        cr.set_source_rgba(1, 1, 1, 0.18)
        cr.set_line_width(1)
        for i in range(1, cells):
            cr.move_to(ox + i * z + 0.5, oy); cr.line_to(ox + i * z + 0.5, oy + size)
            cr.move_to(ox, oy + i * z + 0.5); cr.line_to(ox + size, oy + i * z + 0.5)
        cr.stroke()
        cr.set_source_rgba(0.3, 0.6, 1, 0.55)
        cr.set_line_width(z)
        cr.move_to(ox + half * z + z / 2, oy); cr.line_to(ox + half * z + z / 2, oy + size)
        cr.move_to(ox, oy + half * z + z / 2); cr.line_to(ox + size, oy + half * z + z / 2)
        cr.stroke()
        cr.set_source_rgba(1, 1, 1, 0.9)
        cr.set_line_width(1)
        cr.rectangle(ox + half * z + 0.5, oy + half * z + 0.5, z - 1, z - 1)
        cr.stroke()
        cr.set_source_rgba(0, 0, 0, 0.8)
        cr.rectangle(ox - 0.5, oy - 0.5, size + 1, size + 1)
        cr.stroke()
        # readout: coordinate, colour swatch and value, hints
        rgb = sel.pixel_under_pointer()
        cr.set_source_rgba(0, 0, 0, 0.75)
        cr.rectangle(ox, oy + size, size, 70)
        cr.fill()
        layout = PangoCairo.create_layout(cr)
        layout.set_font_description(Pango.FontDescription.from_string("Monospace 11px"))
        lines = [f"({px}, {py})", color_text(rgb, sel.hex_mode) if rgb else "", ]
        layout.set_text("\n".join(lines), -1)
        cr.set_source_rgb(1, 1, 1)
        cr.move_to(ox + 24, oy + size + 5)
        PangoCairo.show_layout(cr, layout)
        if rgb:
            cr.set_source_rgb(*(c / 255 for c in rgb))
            cr.rectangle(ox + 6, oy + size + 20, 12, 12)
            cr.fill()
            cr.set_source_rgb(1, 1, 1)
            cr.rectangle(ox + 6.5, oy + size + 20.5, 11, 11)
            cr.set_line_width(1)
            cr.stroke()
        layout = PangoCairo.create_layout(cr)
        layout.set_font_description(Pango.FontDescription.from_string("Sans 10px"))
        layout.set_text("C copies the colour · Shift: HEX / RGB", -1)
        cr.set_source_rgba(1, 1, 1, 0.8)
        cr.move_to(ox + 6, oy + size + 44)
        PangoCairo.show_layout(cr, layout)

    def draw_hints(self, cr, w, h):
        layout = PangoCairo.create_layout(cr)
        layout.set_font_description(Pango.FontDescription.from_string("Sans 11px"))
        mode = MODES[self.sel.mode]
        adjust = "on" if self.sel.adjust else "off"
        rows = [f"{k}\t{v}" for k, v in HINTS] + [f"detection\t{mode}", f"adjust mode\t{adjust}"]
        if self.sel.frames:
            n = len(self.sel.frames)
            idx = self.sel.frame_idx
            shown = "now" if idx < 0 else f"{frame_label(self.sel.frame_age())} ({idx + 1}/{n})"
            rows.append(f"screen\t{shown}")
        layout.set_text("\n".join(rows), -1)
        tabs = Pango.TabArray.new(1, True)
        tabs.set_tab(0, Pango.TabAlign.LEFT, 110)
        layout.set_tabs(tabs)
        _ink, logical = layout.get_pixel_extents()
        pad = 10
        bw, bh = logical.width + 2 * pad, logical.height + 2 * pad
        x, y = 12, h - bh - 12
        cr.set_source_rgba(0.08, 0.08, 0.08, 0.85)
        cr.rectangle(x, y, bw, bh)
        cr.fill()
        cr.set_source_rgba(1, 1, 1, 0.92)
        cr.move_to(x + pad, y + pad)
        PangoCairo.show_layout(cr, layout)


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    if LayerShell is None:
        sys.exit(127)
    try:
        data = json.load(sys.stdin)
    except ValueError:
        data = {}
    try:
        frame = GdkPixbuf.Pixbuf.new_from_file(sys.argv[1])
    except GLib.Error as e:
        print(f"snip-select: cannot read the frame: {e.message}", file=sys.stderr)
        sys.exit(1)
    app = Gtk.Application(application_id=None, flags=Gio.ApplicationFlags.NON_UNIQUE)
    state = {}

    def activate(app):
        sel = Selector(app, frame, data, sys.argv[1])
        state["sel"] = sel
        for win in sel.windows_:
            win.present()
    app.connect("activate", activate)
    GLib.set_prgname("snip-pin-select")
    app.run([sys.argv[0]])
    sel = state.get("sel")
    if sel is None:
        sys.exit(1)
    if sel.result:
        print(sel.result)
    sys.exit(getattr(sel, "exit_code", 0 if sel.result else 1))


if __name__ == "__main__":
    main()
