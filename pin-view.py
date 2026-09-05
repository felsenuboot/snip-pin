#!/usr/bin/env python3
"""Snipaste-style pinned image window for Hyprland (GTK4).

usage: pin-view.py IMAGE [X Y]

  drag           move            wheel             zoom (10% steps)
  Ctrl+wheel     opacity         Ctrl+0 / 1        reset zoom / opacity
  Ctrl+C         copy image & close        Ctrl+S    save to screenshot folder & close
  dbl-click      copy image & close        Esc       close without copying
  right-click    copy image & close        middle-click  menu

Annotations (toolbar under the pin while the pointer hovers it, or keys):
  R rectangle   A arrow   P pen   T text   M marker   B blur (mosaic)
  1-7 colour    [ ] stroke width    Ctrl+Z / Ctrl+Shift+Z undo / redo
  With a tool selected, left-drag draws; press its key again (or Esc) to
  deselect. Copy and save bake the annotations into the image.
"""
import atexit
import datetime
import json
import math
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import warnings

# One process for all pins: the first viewer listens on a socket, later ones
# hand their arguments over and exit before GTK is even imported (~20 ms
# instead of a full GTK start-up), so the second and later pins appear at
# once and share one GL driver instance. Each pin is its own window; closing
# one leaves the others alone, and the process ends with the last window.
#
# The socket is abstract (Linux): no file to unlink, nothing stale after a
# crash, and bind() failing means exactly "a live viewer holds the name", so
# no process ever takes the socket over from another. Elsewhere a path in
# $XDG_RUNTIME_DIR is used.
RUNTIME_DIR = os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir()
if sys.platform.startswith("linux"):
    SOCKET = "\0snip-pin-%d" % os.getuid()
else:
    SOCKET = os.path.join(RUNTIME_DIR, "snip-pin.sock")
HANDOVER_TIMEOUT = 1.5      # a live server answers in milliseconds; longer means it is stuck


def hand_over(args):
    """Send [path, x, y] to a running viewer; True if it took the pin."""
    s = socket.socket(socket.AF_UNIX)
    s.settimeout(HANDOVER_TIMEOUT)
    try:
        s.connect(SOCKET)
        s.sendall(json.dumps(args).encode() + b"\n")
        return s.recv(1) == b"1"
    except OSError:
        return False
    finally:
        s.close()


if __name__ == "__main__" and len(sys.argv) >= 2:
    if hand_over([os.path.abspath(sys.argv[1])] + sys.argv[2:]):
        sys.exit(0)

warnings.filterwarnings("ignore", category=DeprecationWarning)
import cairo
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gtk, Pango, PangoCairo

APP_ID = "snip-pin"
ZOOM_STEP = 1.10
MIN_PX = 40                                        # the pin's longer side never shrinks below this
MIN_SIDE = 4                                       # ... and the shorter side stays grabbable
MAX_SCALE = 8.0
BORDER = 2                                         # px, drawn by the viewer itself
BORDER_COLOR = os.environ.get("SNIP_PIN_BORDER", "#ff9f1c")
EDIT_COLOR = "#3fa7ff"                             # border tint while a tool is active

# ---- annotation presets --------------------------------------------------
COLORS = [("red", "#e5312b"), ("orange", "#ff8c1a"), ("yellow", "#ffd21f"),
          ("green", "#2fbf4f"), ("blue", "#2f7fe5"), ("white", "#ffffff"),
          ("black", "#000000")]
WIDTHS = [("thin", 2), ("normal", 4), ("thick", 7)]      # stroke width in image px
TOOLS = [("rect", "R", "Rect", "Rectangle outline"), ("arrow", "A", "Arrow", "Arrow"),
         ("pen", "P", "Pen", "Freehand pen"), ("text", "T", "Text", "Text: click, type, Enter"),
         ("marker", "M", "Mark", "Highlighter"), ("blur", "B", "Blur", "Mosaic (hide secrets)")]
TOOL_KEYS = {k.lower(): t for t, k, _, _ in TOOLS}
MARKER_ALPHA = 0.4
MARKER_FACTOR = 3.5                                # marker stroke = width * factor
TEXT_PX = {2: 16, 4: 22, 7: 30}                    # font size per stroke width
MOSAIC_PX = {2: 5, 4: 9, 7: 14}                    # block size per stroke width
TOOLBAR_HIDE_MS = 300                              # grace period after the pointer leaves
SMOOTH_STEP = 30.0                                 # touchpad scroll units per zoom/opacity step
OSD_MS = 700                                       # how long the zoom / opacity readout stays

CSS = f"""
window.snip-pin {{
    background: transparent;
    border: {BORDER}px solid {BORDER_COLOR};
    box-shadow: inset 0 0 0 1px rgba(0, 0, 0, 0.45);   /* dark inner line for light pages */
}}
window.snip-pin.editing {{ border-color: {EDIT_COLOR}; }}
.snip-toolbar button {{ padding: 2px 7px; min-height: 22px; min-width: 0; }}
.snip-toolbar .swatch {{ min-width: 14px; min-height: 14px; padding: 0; margin: 4px 1px;
                         border-radius: 9px; border: 1px solid rgba(0,0,0,0.5); }}
.snip-toolbar .swatch.sel {{ box-shadow: 0 0 0 2px {EDIT_COLOR}; }}
""" + "".join(f".snip-toolbar .swatch.c{i} {{ background: {hexc}; }}\n"
              for i, (_, hexc) in enumerate(COLORS))

def hex_to_rgb(h):
    return tuple(int(h[i:i + 2], 16) / 255 for i in (1, 3, 5))

NOTIFY_SEND = shutil.which("notify-send")           # libnotify is optional

def notify(msg, ms=1500):
    """Desktop toast; silently a no-op without libnotify."""
    if NOTIFY_SEND is None:
        return
    try:
        subprocess.Popen([NOTIFY_SEND, "-i", "camera-photo-symbolic", "-t", str(ms), "Snip", msg],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        pass

def scroll_steps(wheel, dy, acc):
    """(steps, new_acc): a wheel notch is one step; smooth deltas accumulate, one step per SMOOTH_STEP."""
    if wheel:
        return int(dy), acc
    acc += dy
    steps = int(acc / SMOOTH_STEP)
    return steps, acc - steps * SMOOTH_STEP

def unique_path(folder, stem, ext):
    """folder/stem.ext, or stem_2.ext, stem_3.ext ... if that exists already."""
    p = os.path.join(folder, stem + ext)
    n = 2
    while os.path.exists(p):
        p = os.path.join(folder, f"{stem}_{n}{ext}")
        n += 1
    return p

def screenshot_folder():
    """Where Ctrl+S saves: $SNIP_PIN_SAVE_DIR, the ML4W setting, the XDG pictures dir, ~/Pictures."""
    d = os.environ.get("SNIP_PIN_SAVE_DIR", "").strip()
    if d:
        return os.path.expandvars(os.path.expanduser(d))
    try:
        with open(os.path.expanduser("~/.config/ml4w/settings/screenshot-folder")) as f:
            d = os.path.expandvars(os.path.expanduser(f.read().strip()))
            if d:
                return d
    except OSError:
        pass
    d = GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_PICTURES)   # xdg-user-dirs, localised
    return d or os.path.expanduser("~/Pictures")

# ---- annotation rendering (pure cairo, image coordinates) ----------------
# An op is a dict: kind, pts [(x, y), ...], color (r, g, b), width, text.
def norm_rect(pts):
    (x0, y0), (x1, y1) = pts[0], pts[-1]
    return min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)

def mosaic_pixbuf(pixbuf, op):
    x0, y0, x1, y1 = norm_rect(op["pts"])
    x0, y0 = max(0, int(x0)), max(0, int(y0))
    x1, y1 = min(pixbuf.get_width(), math.ceil(x1)), min(pixbuf.get_height(), math.ceil(y1))
    w, h = x1 - x0, y1 - y0
    if w < 1 or h < 1:
        return None
    key = (x0, y0, w, h, op["width"])
    cached = op.get("_mosaic")
    if cached and cached[0] == key:
        return cached[1]
    blk = MOSAIC_PX.get(op["width"], 9)
    sub = pixbuf.new_subpixbuf(x0, y0, w, h)
    small = sub.scale_simple(max(1, w // blk), max(1, h // blk), GdkPixbuf.InterpType.BILINEAR)
    big = small.scale_simple(w, h, GdkPixbuf.InterpType.NEAREST)
    op["_mosaic"] = (key, (x0, y0, w, h, big))
    return op["_mosaic"][1]

def draw_op(cr, pixbuf, op, caret=False):
    k, pts, w = op["kind"], op["pts"], op["width"]
    r, g, b = op["color"]
    cr.save()
    cr.set_line_cap(cairo.LINE_CAP_ROUND)
    cr.set_line_join(cairo.LINE_JOIN_ROUND)
    if k == "rect":
        x0, y0, x1, y1 = norm_rect(pts)
        cr.set_source_rgb(r, g, b)
        cr.set_line_width(w)
        cr.set_line_join(cairo.LINE_JOIN_MITER)
        cr.rectangle(x0, y0, x1 - x0, y1 - y0)
        cr.stroke()
    elif k == "arrow":
        (x0, y0), (x1, y1) = pts[0], pts[-1]
        length = math.hypot(x1 - x0, y1 - y0)
        if length >= 1:
            ang = math.atan2(y1 - y0, x1 - x0)
            head = max(10, w * 4)
            spread = 0.5
            cr.set_source_rgb(r, g, b)
            cr.set_line_width(w)
            cr.move_to(x0, y0)
            cr.line_to(x1 - head * 0.7 * math.cos(ang), y1 - head * 0.7 * math.sin(ang))
            cr.stroke()
            cr.move_to(x1, y1)
            cr.line_to(x1 - head * math.cos(ang - spread), y1 - head * math.sin(ang - spread))
            cr.line_to(x1 - head * math.cos(ang + spread), y1 - head * math.sin(ang + spread))
            cr.close_path()
            cr.fill()
    elif k in ("pen", "marker"):
        if k == "marker":
            cr.set_source_rgba(r, g, b, MARKER_ALPHA)
            cr.set_line_width(w * MARKER_FACTOR)
        else:
            cr.set_source_rgb(r, g, b)
            cr.set_line_width(w)
        cr.move_to(*pts[0])
        for p in pts[1:]:
            cr.line_to(*p)
        if len(pts) == 1:
            cr.line_to(pts[0][0] + 0.01, pts[0][1])       # a tap leaves a dot
        cr.stroke()
    elif k == "blur":
        m = mosaic_pixbuf(pixbuf, op)
        if m is not None:
            x0, y0, mw, mh, pb = m
            Gdk.cairo_set_source_pixbuf(cr, pb, x0, y0)
            cr.get_source().set_filter(cairo.FILTER_NEAREST)
            cr.rectangle(x0, y0, mw, mh)
            cr.fill()
    elif k == "text":
        size = TEXT_PX.get(w, 22)
        x, y = pts[0]
        layout = PangoCairo.create_layout(cr)
        layout.set_font_description(Pango.FontDescription.from_string(f"Sans Bold {size}px"))
        layout.set_text(op["text"], -1)
        PangoCairo.update_layout(cr, layout)
        cr.move_to(x, y - size * 0.75)
        PangoCairo.layout_path(cr, layout)
        dark = (0.299 * r + 0.587 * g + 0.114 * b) < 0.5
        cr.set_source_rgba(1, 1, 1, 0.75) if dark else cr.set_source_rgba(0, 0, 0, 0.75)
        cr.set_line_width(max(1.5, size / 9))
        cr.stroke_preserve()
        cr.set_source_rgb(r, g, b)
        cr.fill()
        if caret:
            _, logical = layout.get_pixel_extents()
            cx = x + logical.width + 2
            cr.set_source_rgb(r, g, b)
            cr.set_line_width(max(1.5, size / 12))
            cr.move_to(cx, y - size * 0.75)
            cr.line_to(cx, y - size * 0.75 + max(logical.height, size * 1.2))
            cr.stroke()
    cr.restore()

def render_png(pixbuf, ops, out_path):
    """Bake pixbuf + ops into a PNG at native size."""
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, pixbuf.get_width(), pixbuf.get_height())
    cr = cairo.Context(surf)
    Gdk.cairo_set_source_pixbuf(cr, pixbuf, 0, 0)
    cr.paint()
    for op in ops:
        draw_op(cr, pixbuf, op)
    surf.flush()
    surf.write_to_png(out_path)
    return out_path

_css_loaded = False
_seq = 0


class Pin(Gtk.ApplicationWindow):
    def __init__(self, app, path, pos):
        # All pins share one process (see main), so the placement loop tells
        # windows apart by a unique title until each one has been placed.
        global _seq
        # load before the window exists: a window registered with the
        # application would keep the process alive after a failed load
        pixbuf = GdkPixbuf.Pixbuf.new_from_file(path)
        _seq += 1
        super().__init__(application=app, title=f"snip-pin #{_seq}" if pos else "snip-pin")
        self.path = path
        self.pos = pos
        self.pixbuf = pixbuf
        self.iw, self.ih = self.pixbuf.get_width(), self.pixbuf.get_height()
        self.scale = max(1.0, self.min_scale())        # tiny snips open enlarged, uniformly
        self.opacity = 1.0
        # annotation state
        self.tool = None
        self.color_idx = 0
        self.width_idx = 1
        self.ops = []                 # committed annotations, in order
        self.redo_stack = []
        self.pending = None           # op being dragged out
        self.typing = None            # text op being typed
        self._syncing = False
        self.hovered = False
        self.toolbar_shown = False    # tracked ourselves: get_visible() is true during the fade-out
        self.hide_timer = None
        self.scroll_acc = 0.0         # smooth-scroll distance not yet turned into a step
        self.osd = None               # (text, timer id): zoom / opacity readout drawn on the pin

        self.set_decorated(False)
        self.set_resizable(False)
        self.add_css_class("snip-pin")
        global _css_loaded
        if not _css_loaded:
            css = Gtk.CssProvider()
            css.load_from_string(CSS)
            Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), css,
                                                      Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
            _css_loaded = True

        self.area = Gtk.DrawingArea()
        self.area.set_draw_func(self.draw)
        self.set_child(self.area)
        self.toolbar = self.build_toolbar()
        self.apply_scale()

        # Start the compositor move only after the pointer really moved: handing
        # the pointer to Hyprland on the first press would swallow double-clicks.
        drag = Gtk.GestureDrag(button=1)
        drag.connect("drag-begin", self.on_drag_begin)
        drag.connect("drag-update", self.on_drag_update)
        drag.connect("drag-end", self.on_drag_end)
        self.area.add_controller(drag)
        self.moving = False

        click = Gtk.GestureClick(button=1)
        click.connect("pressed", self.on_click)
        self.area.add_controller(click)

        rclick = Gtk.GestureClick(button=3)
        rclick.connect("pressed", lambda g, n, x, y: self.copy())
        self.area.add_controller(rclick)

        mclick = Gtk.GestureClick(button=2)
        mclick.connect("pressed", self.on_menu)
        self.area.add_controller(mclick)

        scroll = Gtk.EventControllerScroll(flags=Gtk.EventControllerScrollFlags.VERTICAL)
        scroll.connect("scroll", self.on_scroll)
        self.area.add_controller(scroll)

        hover = Gtk.EventControllerMotion()
        hover.connect("enter", lambda c, x, y: self.on_hover(True))
        hover.connect("leave", lambda c: self.on_hover(False))
        self.area.add_controller(hover)

        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self.on_key)
        self.add_controller(keys)

        self.menu = Gtk.PopoverMenu.new_from_model(self.build_menu())
        self.menu.set_parent(self.area)
        self.menu.set_has_arrow(False)
        for name, cb in (("copy", self.copy), ("save", self.save),
                         ("reset", lambda *a: self.set_scale(1.0)), ("close", lambda *a: self.close()),
                         ("undo", lambda *a: self.undo()), ("redo", lambda *a: self.redo())):
            act = Gio.SimpleAction.new(name, None)
            act.connect("activate", cb)
            self.add_action(act)

    # ---- geometry -------------------------------------------------------
    def apply_scale(self):
        # both sides from the same scale: clamping each side on its own
        # stretched thin snips (a line of text at 800x18 became 800x40)
        w = max(1, round(self.iw * self.scale))
        h = max(1, round(self.ih * self.scale))
        self.area.set_content_width(w)
        self.area.set_content_height(h)
        self.set_default_size(w, h)
        if self.toolbar_shown:
            # the popup does not follow a resize of its parent on its own
            GLib.timeout_add(80, self.show_toolbar)
        self.area.queue_draw()

    def show_toolbar(self):
        if self.toolbar_shown:
            self.toolbar.popdown()
            self.toolbar.popup()
        return False

    # The toolbar is its own surface, so moving the pointer from the pin onto
    # it counts as leaving the pin: hide only after a short grace period that
    # entering either surface cancels. The fading toolbar sends a final leave
    # of its own; a second popdown would restart the fade, so ignore it.
    def on_hover(self, entered):
        self.hovered = entered
        if self.hide_timer is not None:
            GLib.source_remove(self.hide_timer)
            self.hide_timer = None
        if entered:
            if not self.toolbar_shown:
                self.toolbar_shown = True
                self.toolbar.popup()
        elif self.toolbar_shown:
            self.hide_timer = GLib.timeout_add(TOOLBAR_HIDE_MS, self.hide_toolbar)

    def hide_toolbar(self):
        self.hide_timer = None
        if not self.hovered and self.toolbar_shown:
            self.toolbar_shown = False
            self.toolbar.popdown()
        return False

    def min_scale(self):
        return max(MIN_PX / max(self.iw, self.ih), MIN_SIDE / min(self.iw, self.ih))

    def set_scale(self, s):
        self.scale = max(self.min_scale(), min(s, MAX_SCALE))
        self.apply_scale()

    def to_img(self, x, y):
        """Widget coordinates -> image coordinates."""
        return (x * self.iw / self.area.get_width(), y * self.ih / self.area.get_height())

    def draw(self, area, cr, w, h):
        cr.scale(w / self.iw, h / self.ih)
        Gdk.cairo_set_source_pixbuf(cr, self.pixbuf, 0, 0)
        cr.get_source().set_filter(cairo.FILTER_GOOD)
        cr.paint()
        for op in self.ops:
            draw_op(cr, self.pixbuf, op)
        if self.pending is not None:
            draw_op(cr, self.pixbuf, self.pending)
        if self.typing is not None:
            draw_op(cr, self.pixbuf, self.typing, caret=True)
        if self.osd is not None:
            self.draw_osd(cr, w, h)

    def draw_osd(self, cr, w, h):
        """Zoom / opacity readout in the corner; on screen only, never exported."""
        cr.identity_matrix()
        layout = PangoCairo.create_layout(cr)
        layout.set_font_description(Pango.FontDescription.from_string("Sans Bold 11px"))
        layout.set_text(self.osd[0], -1)
        _, logical = layout.get_pixel_extents()
        pad, x, y = 5, 6, 6
        cr.set_source_rgba(0, 0, 0, 0.6)
        cr.rectangle(x, y, logical.width + 2 * pad, logical.height + 2 * pad)
        cr.fill()
        cr.set_source_rgb(1, 1, 1)
        cr.move_to(x + pad, y + pad)
        PangoCairo.show_layout(cr, layout)

    def show_osd(self, text):
        if self.osd is not None:
            GLib.source_remove(self.osd[1])
        self.osd = (text, GLib.timeout_add(OSD_MS, self.hide_osd))
        self.area.queue_draw()

    def hide_osd(self):
        self.osd = None
        self.area.queue_draw()
        return False

    # ---- placement via Hyprland (apps cannot position themselves on Wayland)
    def place(self):
        if self.pos is None:
            return
        x, y = self.pos[0] - BORDER, self.pos[1] - BORDER   # keep the image itself at pos
        deadline = time.time() + 2
        state = {"addr": None, "ok": 0}

        def me():
            try:
                clients = json.loads(subprocess.run(["hyprctl", "clients", "-j"],
                                                    capture_output=True, text=True).stdout)
            except Exception:
                return None
            for c in clients:
                if c.get("pid") == os.getpid() and c.get("title") == self.get_title():
                    return c
            return None

        def tick():
            again = step()
            if not again:
                self.set_title("snip-pin")
            return again

        def step():
            c = me()
            if c is None:
                return time.time() < deadline
            if list(c["at"]) == [x, y]:
                # Hyprland re-centres a floating window when its size settles after
                # mapping, so keep checking briefly and re-move if it drifted
                state["ok"] += 1
                return state["ok"] < 6 and time.time() < deadline
            state["ok"] = 0
            subprocess.run(["hyprctl", "dispatch",
                f"hl.dsp.window.move({{ x = {x}, y = {y}, exact = true, window = 'address:{c['address']}' }})"],
                capture_output=True)
            return time.time() < deadline
        GLib.timeout_add(30, tick)

    # ---- annotation state ------------------------------------------------
    @property
    def color(self):
        return hex_to_rgb(COLORS[self.color_idx][1])

    @property
    def width(self):
        return WIDTHS[self.width_idx][1]

    def set_tool(self, tool):
        if tool != "text":
            self.commit_text()
        self.pending = None
        self.tool = tool
        if tool is None:
            self.remove_css_class("editing")
        else:
            self.add_css_class("editing")
        cursor = {None: None, "text": "text"}.get(tool, "crosshair")
        self.area.set_cursor(Gdk.Cursor.new_from_name(cursor) if cursor else None)
        self.sync_toolbar()

    def push(self, op):
        self.ops.append(op)
        self.redo_stack.clear()
        self.sync_toolbar()
        self.area.queue_draw()

    def undo(self):
        if self.typing is not None:
            self.typing = None
        elif self.ops:
            self.redo_stack.append(self.ops.pop())
        self.sync_toolbar()
        self.area.queue_draw()

    def redo(self):
        if self.redo_stack:
            self.ops.append(self.redo_stack.pop())
        self.sync_toolbar()
        self.area.queue_draw()

    def commit_text(self):
        op, self.typing = self.typing, None
        if op is not None and op["text"].strip():
            self.push(op)
        self.area.queue_draw()

    def new_op(self, kind, p):
        return {"kind": kind, "pts": [p], "color": self.color, "width": self.width, "text": ""}

    # ---- toolbar --------------------------------------------------------
    def build_toolbar(self):
        pop = Gtk.Popover()
        pop.set_parent(self.area)
        pop.set_autohide(False)
        pop.set_has_arrow(False)
        pop.set_position(Gtk.PositionType.BOTTOM)   # no pointing_to: anchors to the whole pin
        pop.add_css_class("snip-toolbar")
        box = Gtk.Box(spacing=2)
        pop.set_child(box)
        hover = Gtk.EventControllerMotion()
        hover.connect("enter", lambda c, x, y: self.on_hover(True))
        hover.connect("leave", lambda c: self.on_hover(False))
        box.add_controller(hover)

        def add(widget):
            widget.set_focusable(False)
            box.append(widget)
            return widget

        self.tool_btns = {}
        for tool, key, label, tip in TOOLS:
            b = add(Gtk.ToggleButton(label=label))
            b.set_tooltip_text(f"{tip}  [{key}]")
            b.connect("toggled", self.on_tool_btn, tool)
            self.tool_btns[tool] = b
        box.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))

        self.swatches = []
        for i, (name, _) in enumerate(COLORS):
            b = add(Gtk.Button())
            b.add_css_class("swatch")
            b.add_css_class(f"c{i}")
            b.set_tooltip_text(f"{name}  [{i + 1}]")
            b.connect("clicked", lambda _b, i=i: self.set_color(i))
            self.swatches.append(b)
        box.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))

        self.width_btns = []
        for i, (name, px) in enumerate(WIDTHS):
            b = add(Gtk.ToggleButton(label="●" if i == 2 else ("•" if i == 1 else "·")))
            b.set_tooltip_text(f"{name} ({px} px)  [ [ / ] ]")
            b.connect("toggled", self.on_width_btn, i)
            self.width_btns.append(b)
        box.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))

        self.undo_btn = add(Gtk.Button(icon_name="edit-undo-symbolic"))
        self.undo_btn.set_tooltip_text("Undo  [Ctrl+Z]")
        self.undo_btn.connect("clicked", lambda *a: self.undo())
        self.redo_btn = add(Gtk.Button(icon_name="edit-redo-symbolic"))
        self.redo_btn.set_tooltip_text("Redo  [Ctrl+Shift+Z]")
        self.redo_btn.connect("clicked", lambda *a: self.redo())
        return pop

    def sync_toolbar(self):
        self._syncing = True
        for tool, b in self.tool_btns.items():
            b.set_active(tool == self.tool)
        for i, b in enumerate(self.swatches):
            b.remove_css_class("sel")
            if i == self.color_idx:
                b.add_css_class("sel")
        for i, b in enumerate(self.width_btns):
            b.set_active(i == self.width_idx)
        self.undo_btn.set_sensitive(bool(self.ops) or self.typing is not None)
        self.redo_btn.set_sensitive(bool(self.redo_stack))
        self._syncing = False

    def on_tool_btn(self, btn, tool):
        if self._syncing:
            return
        self.set_tool(tool if btn.get_active() else None)

    def on_width_btn(self, btn, idx):
        if self._syncing:
            return
        if btn.get_active():
            self.set_width(idx)
        elif idx == self.width_idx:
            self.sync_toolbar()      # keep one width selected

    def set_color(self, idx):
        self.color_idx = idx % len(COLORS)
        if self.typing is not None:
            self.typing["color"] = self.color
        self.sync_toolbar()
        self.area.queue_draw()

    def set_width(self, idx):
        self.width_idx = max(0, min(len(WIDTHS) - 1, idx))
        if self.typing is not None:
            self.typing["width"] = self.width
        self.sync_toolbar()
        self.area.queue_draw()

    # ---- input ------------------------------------------------------------
    def on_drag_begin(self, gesture, x, y):
        self.moving = False
        if self.tool in (None, "text"):
            return
        self.pending = self.new_op(self.tool, self.to_img(x, y))
        self.area.queue_draw()

    def on_drag_update(self, gesture, dx, dy):
        if self.pending is not None:
            ok, sx, sy = gesture.get_start_point()
            p = self.to_img(sx + dx, sy + dy)
            op = self.pending
            if op["kind"] in ("pen", "marker"):
                lx, ly = op["pts"][-1]
                if math.hypot(p[0] - lx, p[1] - ly) >= 2:      # simplify: skip tiny steps
                    op["pts"].append(p)
            else:
                op["pts"] = [op["pts"][0], p]
            self.area.queue_draw()
            return
        if self.tool is not None:
            return                                              # text tool: no move
        if self.moving or (abs(dx) < 4 and abs(dy) < 4):
            return
        self.moving = True
        _ok, sx, sy = gesture.get_start_point()
        surface = self.get_surface()
        if isinstance(surface, Gdk.Toplevel):
            surface.begin_move(gesture.get_device(), 1, sx + dx, sy + dy, gesture.get_current_event_time())

    def on_drag_end(self, gesture, dx, dy):
        op, self.pending = self.pending, None
        if op is None:
            return
        if len(op["pts"]) < 2:                                # a tap draws nothing
            self.area.queue_draw(); return
        if op["kind"] in ("rect", "arrow", "blur"):
            (x0, y0), (x1, y1) = op["pts"]
            if math.hypot(x1 - x0, y1 - y0) < 3:
                self.area.queue_draw(); return
        self.push(op)

    def on_click(self, gesture, n, x, y):
        if n == 2:
            self.copy()
        elif self.tool == "text":
            self.commit_text()
            self.typing = self.new_op("text", self.to_img(x, y))
            self.sync_toolbar()
            self.area.queue_draw()

    def on_menu(self, gesture, n, x, y):
        self.menu.set_pointing_to(Gdk.Rectangle(x=int(x), y=int(y), width=1, height=1))
        self.menu.popup()

    def on_scroll(self, ctrl, dx, dy):
        # A wheel notch is one step. A touchpad sends many small smooth events
        # per flick; those accumulate and give one step per SMOOTH_STEP units,
        # so one flick no longer zooms 3-5x or fades the pin to nothing.
        steps, self.scroll_acc = scroll_steps(ctrl.get_unit() == Gdk.ScrollUnit.WHEEL, dy, self.scroll_acc)
        if steps == 0:
            return True
        ctrl_held = ctrl.get_current_event_state() & Gdk.ModifierType.CONTROL_MASK
        if ctrl_held:
            self.opacity = min(1.0, max(0.1, self.opacity - 0.1 * steps))
            self.set_opacity(self.opacity)
            self.show_osd(f"{round(self.opacity * 100)} %")
        else:
            self.set_scale(self.scale / ZOOM_STEP ** steps)
            self.show_osd(f"{round(self.scale * 100)} %")
        return True

    def on_key(self, ctrl, keyval, keycode, state):
        ctrl_held = state & Gdk.ModifierType.CONTROL_MASK
        shift = state & Gdk.ModifierType.SHIFT_MASK
        # text entry swallows plain keys
        if self.typing is not None and not ctrl_held:
            if keyval == Gdk.KEY_Escape:
                self.typing = None; self.sync_toolbar(); self.area.queue_draw(); return True
            if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
                self.commit_text(); return True
            if keyval == Gdk.KEY_BackSpace:
                self.typing["text"] = self.typing["text"][:-1]; self.area.queue_draw(); return True
            u = Gdk.keyval_to_unicode(keyval)
            if u and chr(u).isprintable():
                self.typing["text"] += chr(u); self.area.queue_draw(); return True
            return True
        if keyval == Gdk.KEY_Escape:
            if self.tool is not None:
                self.set_tool(None)
            else:
                self.close()
            return True
        if ctrl_held:
            if keyval in (Gdk.KEY_c, Gdk.KEY_C):
                self.copy(); return True
            if keyval in (Gdk.KEY_s, Gdk.KEY_S):
                self.save(); return True
            if keyval in (Gdk.KEY_z, Gdk.KEY_Z):
                self.redo() if shift else self.undo(); return True
            if keyval in (Gdk.KEY_y, Gdk.KEY_Y):
                self.redo(); return True
            if keyval == Gdk.KEY_0:
                self.set_scale(1.0); self.show_osd("100 %"); return True
            if keyval == Gdk.KEY_1:
                self.opacity = 1.0; self.set_opacity(1.0); self.show_osd("100 %"); return True
            return False
        name = Gdk.keyval_name(keyval) or ""
        if name.lower() in TOOL_KEYS:
            tool = TOOL_KEYS[name.lower()]
            self.set_tool(None if tool == self.tool else tool); return True
        if name.isdigit() and 1 <= int(name) <= len(COLORS):
            self.set_color(int(name) - 1); return True
        if keyval == Gdk.KEY_bracketleft:
            self.set_width(self.width_idx - 1); return True
        if keyval == Gdk.KEY_bracketright:
            self.set_width(self.width_idx + 1); return True
        return False

    # ---- actions ----------------------------------------------------------
    def build_menu(self):
        m = Gio.Menu()
        m.append("Copy image & close\tCtrl+C", "win.copy")
        m.append("Save to screenshots & close\tCtrl+S", "win.save")
        edit = Gio.Menu()
        edit.append("Undo\tCtrl+Z", "win.undo")
        edit.append("Redo\tCtrl+Shift+Z", "win.redo")
        m.append_section(None, edit)
        tail = Gio.Menu()
        tail.append("Reset zoom\tCtrl+0", "win.reset")
        tail.append("Close\tEsc", "win.close")
        m.append_section(None, tail)
        return m

    def export(self):
        """(path, temporary): the original, or the annotations baked into a temp file.

        The caller deletes a temporary file when done. Rendering next to the
        original would litter the cache (and the history) or the user's own
        folder for `pin FILE`.
        """
        ops = list(self.ops)
        if self.typing is not None and self.typing["text"].strip():
            ops.append(self.typing)
        # the clipboard and the save folder always get PNG: a JPEG opened via
        # "pin FILE" must be re-encoded even without annotations
        if not ops and self.path.lower().endswith(".png"):
            return self.path, False
        fd, tmp = tempfile.mkstemp(prefix="snip-pin-", suffix=".png", dir=RUNTIME_DIR)
        os.close(fd)
        render_png(self.pixbuf, ops, tmp)
        return tmp, True

    def copy(self, *a):
        path, temporary = self.export()
        try:
            # wl-copy reads all of stdin, then forks a helper that keeps
            # serving the clipboard after we exit
            with open(path, "rb") as f:
                subprocess.run(["wl-copy", "--type", "image/png"], stdin=f)
        finally:
            if temporary:
                os.unlink(path)
        notify("Copied to clipboard")
        self.close()

    def save(self, *a):
        folder = screenshot_folder()
        path, temporary = self.export()
        try:
            os.makedirs(folder, exist_ok=True)
            dest = unique_path(folder, datetime.datetime.now().strftime("pin_%Y%m%d_%H%M%S"), ".png")
            shutil.copyfile(path, dest)
        except OSError as e:
            # unwritable folder, full disk: keep the pin so nothing is lost
            notify(f"Cannot save to {folder}: {e.strerror}", 3000)
            print(f"pin-view: cannot save to {folder}: {e}", file=sys.stderr)
            return
        finally:
            if temporary:
                os.unlink(path)
        notify(f"Saved {dest}")
        self.close()

def open_pin(app, args):
    """Open a pin for [path, x, y]; a bad file tells the user instead of crashing."""
    path = args[0]
    try:
        pos = (int(args[1]), int(args[2])) if len(args) >= 3 else None
    except ValueError:
        pos = None
    try:
        win = Pin(app, path, pos)
    except GLib.Error as e:
        # deleted between 'last' and here, a truncated clipboard image, 'pin FILE' on a non-image
        notify(f"Cannot open {os.path.basename(path)}", 3000)
        print(f"pin-view: cannot open {path}: {e.message.splitlines()[0]}", file=sys.stderr)
        if not app.get_windows():
            app.quit()             # nothing to show: a window-less GtkApplication would idle forever
        return False
    win.present()
    win.place()
    return True


def read_request(conn, timeout=0.2):
    """One JSON line from a hand-over client, or None. Bounded: a stalled
    client may cost every pin a fifth of a second, never more."""
    conn.settimeout(timeout)
    buf = b""
    try:
        while b"\n" not in buf and len(buf) < 65536:
            chunk = conn.recv(4096)
            if not chunk:
                break
            buf += chunk
    except OSError:
        return None
    line = buf.split(b"\n", 1)[0].strip()
    if not line:
        return None
    try:
        args = json.loads(line)
    except ValueError:
        return None
    return args if isinstance(args, list) and args and all(isinstance(a, str) for a in args) else None


def serve(app):
    """Listen for hand-overs from later invocations (see hand_over). False if
    another live viewer already serves; this one then just shows its own pin."""
    srv = socket.socket(socket.AF_UNIX)
    try:
        if not SOCKET.startswith("\0") and os.path.exists(SOCKET):
            # path sockets only: a leftover of a crashed viewer, or a live one
            if hand_over([]):
                srv.close()
                return False
            os.unlink(SOCKET)
        srv.bind(SOCKET)
    except OSError:
        srv.close()
        return False
    srv.listen(8)
    srv.setblocking(False)
    if not SOCKET.startswith("\0"):
        atexit.register(lambda: os.path.exists(SOCKET) and os.unlink(SOCKET))

    def on_connect(fd, cond):
        try:
            conn, _ = srv.accept()
        except OSError:
            return True
        try:
            args = read_request(conn)
            # acknowledge first: the client must not wait for GTK, and must
            # not start its own viewer when the file turns out to be bad
            conn.sendall(b"1")
            if args:
                open_pin(app, args)
        except OSError:
            pass
        finally:
            conn.close()
        return True
    watch = GLib.io_add_watch(srv.fileno(), GLib.PRIORITY_DEFAULT, GLib.IO_IN, on_connect)

    def on_window_removed(app, win):
        # the process ends with the last window: stop accepting right away so
        # a hand-over arriving in that moment is refused instead of ignored
        if not app.get_windows():
            GLib.source_remove(watch)
            srv.close()
    app.connect("window-removed", on_window_removed)
    app.hold_socket = srv          # keep it alive with the application
    return True


def main():
    if len(sys.argv) < 2:
        print(__doc__); sys.exit(1)
    # NON_UNIQUE: single-instance handling is the socket in serve(), not
    # GApplication, because a GApplication id would replace the Wayland
    # app_id that the window rules and the dock icon key on.
    app = Gtk.Application(application_id=None, flags=Gio.ApplicationFlags.NON_UNIQUE)

    def activate(app):
        serve(app)
        open_pin(app, [os.path.abspath(sys.argv[1])] + sys.argv[2:])
    app.connect("activate", activate)
    GLib.set_prgname(APP_ID)   # -> Wayland app_id / Hyprland class "snip-pin"
    # Docks look the icon up by app_id via snip-pin.desktop (see README).
    Gtk.Window.set_default_icon_name(APP_ID)
    app.run([sys.argv[0]])

if __name__ == "__main__":
    main()
