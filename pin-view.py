#!/usr/bin/env python3
"""Snipaste-style pinned image window for Hyprland (GTK4).

usage: pin-view.py IMAGE [X Y]

  drag           move            wheel             zoom (10% steps)
  Ctrl+wheel     opacity         Ctrl+0 / 1        reset zoom / opacity
  Ctrl+C         copy image & close        Ctrl+S    save to screenshot folder
  dbl-click      copy image & close        Esc       close without copying
  right-click    menu

Annotations (Ctrl+E toggles edit mode, or press a tool key directly):
  R rectangle   A arrow   P pen   T text   M marker   B blur (mosaic)
  1-7 colour    [ ] stroke width    Ctrl+Z / Ctrl+Shift+Z undo / redo
  Esc leaves edit mode; copy and save bake the annotations into the image.
"""
import os, subprocess, sys, time, json, datetime, shutil, warnings, math
warnings.filterwarnings("ignore", category=DeprecationWarning)
import cairo
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Gtk, Gdk, GdkPixbuf, GLib, Gio, Pango, PangoCairo

APP_ID = "snip-pin"
ZOOM_STEP = 1.10
MIN_PX = 40
BORDER = 2                                         # px, drawn by the viewer itself
BORDER_COLOR = os.environ.get("SNIP_PIN_BORDER", "#ff9f1c")
EDIT_COLOR = "#3fa7ff"                             # border tint while editing

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

def screenshot_folder():
    p = os.path.expanduser("~/.config/ml4w/settings/screenshot-folder")
    try:
        with open(p) as f:
            d = os.path.expandvars(os.path.expanduser(f.read().strip()))
            if d:
                return d
    except OSError:
        pass
    return os.path.expanduser("~/Pictures")

# ---- annotation rendering (pure cairo, image coordinates) ----------------
# An op is a dict: kind, pts [(x, y), ...], color (r, g, b), width, text.
def norm_rect(pts):
    (x0, y0), (x1, y1) = pts[0], pts[-1]
    return min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)

def mosaic_pixbuf(pixbuf, op):
    x0, y0, x1, y1 = norm_rect(op["pts"])
    x0, y0 = max(0, int(x0)), max(0, int(y0))
    x1, y1 = min(pixbuf.get_width(), int(math.ceil(x1))), min(pixbuf.get_height(), int(math.ceil(y1)))
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

class Pin(Gtk.ApplicationWindow):
    def __init__(self, app, path, pos):
        super().__init__(application=app, title="snip-pin")
        self.path = path
        self.pos = pos
        self.pixbuf = GdkPixbuf.Pixbuf.new_from_file(path)
        self.iw, self.ih = self.pixbuf.get_width(), self.pixbuf.get_height()
        self.scale = 1.0
        self.opacity = 1.0
        # annotation state
        self.editing = False
        self.tool = None
        self.color_idx = 0
        self.width_idx = 1
        self.ops = []                 # committed annotations, in order
        self.redo_stack = []
        self.pending = None           # op being dragged out
        self.typing = None            # text op being typed
        self._syncing = False

        self.set_decorated(False)
        self.set_resizable(False)
        self.add_css_class("snip-pin")
        css = Gtk.CssProvider()
        css.load_from_string(CSS)
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), css,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

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
        rclick.connect("pressed", self.on_rclick)
        self.area.add_controller(rclick)

        scroll = Gtk.EventControllerScroll(flags=Gtk.EventControllerScrollFlags.VERTICAL)
        scroll.connect("scroll", self.on_scroll)
        self.area.add_controller(scroll)

        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self.on_key)
        self.add_controller(keys)

        self.menu = Gtk.PopoverMenu.new_from_model(self.build_menu())
        self.menu.set_parent(self.area)
        self.menu.set_has_arrow(False)
        for name, cb in (("copy", self.copy), ("save", self.save),
                         ("reset", lambda *a: self.set_scale(1.0)), ("close", lambda *a: self.close()),
                         ("edit", lambda *a: self.set_editing(not self.editing)),
                         ("undo", lambda *a: self.undo()), ("redo", lambda *a: self.redo())):
            act = Gio.SimpleAction.new(name, None)
            act.connect("activate", cb)
            self.add_action(act)

    # ---- geometry -------------------------------------------------------
    def apply_scale(self):
        w = max(MIN_PX, int(round(self.iw * self.scale)))
        h = max(MIN_PX, int(round(self.ih * self.scale)))
        self.area.set_content_width(w)
        self.area.set_content_height(h)
        self.set_default_size(w, h)
        if self.editing:
            # the popup does not follow a resize of its parent on its own
            GLib.timeout_add(80, self.reposition_toolbar)
        self.area.queue_draw()

    def reposition_toolbar(self):
        if self.editing:
            self.toolbar.popdown()
            self.toolbar.popup()
        return False

    def set_scale(self, s):
        self.scale = max(MIN_PX / max(self.iw, self.ih), min(s, 8.0))
        self.apply_scale()

    def to_img(self, x, y):
        """Widget coordinates -> image coordinates."""
        f = self.area.get_width() / self.iw
        return (x / f, y / f)

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
                if c.get("pid") == os.getpid():
                    return c
            return None

        def tick():
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

    def set_editing(self, on):
        if on == self.editing:
            return
        self.editing = on
        if on:
            self.add_css_class("editing")
            self.toolbar.popup()
        else:
            self.commit_text()
            self.pending = None
            self.set_tool(None)
            self.remove_css_class("editing")
            self.toolbar.popdown()
        self.sync_toolbar()
        self.area.queue_draw()

    def set_tool(self, tool):
        if tool is not None and not self.editing:
            self.set_editing(True)
        if tool != "text":
            self.commit_text()
        self.tool = tool
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
        done = add(Gtk.Button(icon_name="object-select-symbolic"))
        done.set_tooltip_text("Leave edit mode  [Esc / Ctrl+E]")
        done.connect("clicked", lambda *a: self.set_editing(False))
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
        ok, sx, sy = gesture.get_start_point()
        surface = self.get_surface()
        if isinstance(surface, Gdk.Toplevel):
            surface.begin_move(gesture.get_device(), 1, sx + dx, sy + dy, gesture.get_current_event_time())

    def on_drag_end(self, gesture, dx, dy):
        op, self.pending = self.pending, None
        if op is None:
            return
        if op["kind"] in ("rect", "arrow", "blur"):
            if len(op["pts"]) < 2:
                self.area.queue_draw(); return
            (x0, y0), (x1, y1) = op["pts"]
            if math.hypot(x1 - x0, y1 - y0) < 3:
                self.area.queue_draw(); return
        self.push(op)

    def on_click(self, gesture, n, x, y):
        if self.tool == "text" and n == 1:
            self.commit_text()
            self.typing = self.new_op("text", self.to_img(x, y))
            self.sync_toolbar()
            self.area.queue_draw()
        elif n == 2 and not self.editing:
            self.copy()

    def on_rclick(self, gesture, n, x, y):
        self.menu.set_pointing_to(Gdk.Rectangle(x=int(x), y=int(y), width=1, height=1))
        self.menu.popup()

    def on_scroll(self, ctrl, dx, dy):
        ctrl_held = ctrl.get_current_event_state() & Gdk.ModifierType.CONTROL_MASK
        if ctrl_held:
            self.opacity = min(1.0, max(0.1, self.opacity + (-0.1 if dy > 0 else 0.1)))
            self.set_opacity(self.opacity)
        else:
            self.set_scale(self.scale / ZOOM_STEP if dy > 0 else self.scale * ZOOM_STEP)
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
            if self.editing:
                self.set_editing(False)
            else:
                self.close()
            return True
        if ctrl_held:
            if keyval in (Gdk.KEY_c, Gdk.KEY_C):
                self.copy(); return True
            if keyval in (Gdk.KEY_s, Gdk.KEY_S):
                self.save(); return True
            if keyval in (Gdk.KEY_e, Gdk.KEY_E):
                self.set_editing(not self.editing); return True
            if keyval in (Gdk.KEY_z, Gdk.KEY_Z):
                self.redo() if shift else self.undo(); return True
            if keyval in (Gdk.KEY_y, Gdk.KEY_Y):
                self.redo(); return True
            if keyval == Gdk.KEY_0:
                self.set_scale(1.0); return True
            if keyval == Gdk.KEY_1:
                self.opacity = 1.0; self.set_opacity(1.0); return True
            return False
        name = Gdk.keyval_name(keyval) or ""
        if name.lower() in TOOL_KEYS:
            tool = TOOL_KEYS[name.lower()]
            self.set_tool(None if tool == self.tool else tool); return True
        if self.editing:
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
        m.append("Save to screenshots\tCtrl+S", "win.save")
        edit = Gio.Menu()
        edit.append("Edit annotations\tCtrl+E", "win.edit")
        edit.append("Undo\tCtrl+Z", "win.undo")
        edit.append("Redo\tCtrl+Shift+Z", "win.redo")
        m.append_section(None, edit)
        tail = Gio.Menu()
        tail.append("Reset zoom\tCtrl+0", "win.reset")
        tail.append("Close\tEsc", "win.close")
        m.append_section(None, tail)
        return m

    def export_path(self):
        """Path of the image to copy/save: the original, or a baked annotated copy."""
        ops = list(self.ops)
        if self.typing is not None and self.typing["text"].strip():
            ops.append(self.typing)
        if not ops:
            return self.path
        base, _ = os.path.splitext(self.path)
        return render_png(self.pixbuf, ops, base + "_annotated.png")

    def copy(self, *a):
        # wl-copy forks a helper that keeps serving the clipboard after we exit
        with open(self.export_path(), "rb") as f:
            subprocess.run(["wl-copy", "--type", "image/png"], stdin=f)
        self.notify_user("Copied to clipboard")
        self.close()

    def save(self, *a):
        folder = screenshot_folder()
        os.makedirs(folder, exist_ok=True)
        dest = os.path.join(folder, datetime.datetime.now().strftime("pin_%Y%m%d_%H%M%S.png"))
        shutil.copyfile(self.export_path(), dest)
        self.notify_user(f"Saved {dest}")

    def notify_user(self, msg):
        subprocess.Popen(["notify-send", "-i", "camera-photo-symbolic", "-t", "1500", "Snip", msg])

def main():
    if len(sys.argv) < 2:
        print(__doc__); sys.exit(1)
    path = sys.argv[1]
    pos = (int(sys.argv[2]), int(sys.argv[3])) if len(sys.argv) >= 4 else None
    # NON_UNIQUE: every pin is its own process, so closing one never touches another
    app = Gtk.Application(application_id=None, flags=Gio.ApplicationFlags.NON_UNIQUE)
    def activate(app):
        win = Pin(app, path, pos)
        win.present()
        win.place()
    app.connect("activate", activate)
    GLib.set_prgname(APP_ID)   # -> Wayland app_id / Hyprland class "snip-pin"
    app.run([sys.argv[0]])

if __name__ == "__main__":
    main()
