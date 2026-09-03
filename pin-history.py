#!/usr/bin/env python3
"""Thumbnail picker for previous snips (GTK4).

usage: pin-history.py CACHE_DIR SNIP_PIN_SH

  click / arrows / WASD / HJKL   select a snip
  double-click / Enter   pin it again, centred on the screen
  right-click     copy the snip to the clipboard and close
  F or *          keep / unkeep: kept snips never expire
  Delete          remove it from the cache
  Esc             close (right-click on empty space closes too)
  Clear button    remove every snip that is not kept

Snips are the PNG files in CACHE_DIR and CACHE_DIR/kept, newest first. Pinning
runs `SNIP_PIN_SH pin FILE`.
"""
import datetime
import os
import subprocess
import sys
import warnings

warnings.filterwarnings("ignore", category=DeprecationWarning)
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gtk

APP_ID = "snip-pin"          # same class as pins: the float rule applies
THUMB_W, THUMB_H = 220, 140
COLUMNS = 4
MOVE_KEYS = {Gdk.KEY_a: -1, Gdk.KEY_h: -1, Gdk.KEY_d: 1, Gdk.KEY_l: 1,
             Gdk.KEY_w: -COLUMNS, Gdk.KEY_k: -COLUMNS, Gdk.KEY_s: COLUMNS, Gdk.KEY_j: COLUMNS}

CSS = """
window.snip-history { border: 2px solid #ff9f1c; }
.snip-thumb { padding: 6px; border-radius: 8px; }
.snip-thumb picture { border-radius: 4px; }
.snip-thumb label { font-size: 0.85em; opacity: 0.8; }
.snip-thumb.kept label { color: #ff9f1c; opacity: 1; }
.snip-hint { opacity: 0.6; font-size: 0.9em; }
"""


def snips(cache):
    files = []
    for d in (cache, os.path.join(cache, "kept")):
        if os.path.isdir(d):
            files += [os.path.join(d, f) for f in os.listdir(d) if f.endswith(".png")]
    return sorted(files, key=os.path.getmtime, reverse=True)


def is_kept(path):
    return os.path.basename(os.path.dirname(path)) == "kept"


class History(Gtk.ApplicationWindow):
    def __init__(self, app, cache, snip_pin):
        super().__init__(application=app, title="Snip history")
        self.cache, self.snip_pin = cache, snip_pin
        self.add_css_class("snip-history")
        self.set_default_size(COLUMNS * (THUMB_W + 28) + 24, 620)

        self.flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.SINGLE, homogeneous=True,
                                min_children_per_line=COLUMNS, max_children_per_line=COLUMNS,
                                row_spacing=6, column_spacing=6, activate_on_single_click=False)
        self.flow.set_margin_top(12); self.flow.set_margin_bottom(12)
        self.flow.set_margin_start(12); self.flow.set_margin_end(12)
        self.flow.set_valign(Gtk.Align.START)
        self.flow.connect("child-activated", self.on_activate)

        self.empty = Gtk.Label(label="No snips in the last days.", vexpand=True)
        scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        scroller.set_child(self.flow)
        bar = Gtk.ActionBar()
        hint = Gtk.Label(label="double-click / Enter pin  ·  right-click copy  ·  F keep  ·  Del remove"
                               "  ·  arrows / WASD / HJKL move")
        hint.add_css_class("snip-hint")
        bar.pack_start(hint)
        clear = Gtk.Button(label="Clear history")
        clear.connect("clicked", self.clear_all)
        bar.pack_end(clear)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.append(scroller)
        box.append(self.empty)
        box.append(bar)
        self.set_child(box)

        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self.on_key)
        self.add_controller(keys)
        # right-click on a thumbnail copies it; anywhere else just closes
        rclick = Gtk.GestureClick(button=3)
        rclick.connect("pressed", self.on_rclick)
        self.flow.add_controller(rclick)
        rclose = Gtk.GestureClick(button=3)
        rclose.connect("pressed", lambda *a: self.close())
        self.add_controller(rclose)

        self.pending = snips(cache)
        self.empty.set_visible(not self.pending)
        # decode thumbnails one per idle tick so the window appears at once
        GLib.idle_add(self.load_next)

    def load_next(self):
        if not self.pending:
            return False
        path = self.pending.pop(0)
        try:
            pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(path, THUMB_W, THUMB_H, True)
            full = GdkPixbuf.Pixbuf.get_file_info(path)
            w, h = full[1], full[2]
        except GLib.Error:
            return True
        pic = Gtk.Picture.new_for_paintable(Gdk.Texture.new_for_pixbuf(pb))
        pic.set_size_request(THUMB_W, THUMB_H)
        pic.set_can_shrink(False)
        label = Gtk.Label()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.add_css_class("snip-thumb")
        box.append(pic); box.append(label)
        child = Gtk.FlowBoxChild()
        child.set_child(box)
        child.path, child.box, child.label, child.size = path, box, label, (w, h)
        self.relabel(child)
        self.flow.append(child)
        if not self.flow.get_selected_children():
            self.flow.select_child(child)
            child.grab_focus()
        return True

    def relabel(self, child):
        when = datetime.datetime.fromtimestamp(os.path.getmtime(child.path))
        w, h = child.size
        kept = is_kept(child.path)
        child.label.set_label(("★ " if kept else "") + f"{when:%a %H:%M}  ·  {w}×{h}")
        if kept:
            child.box.add_css_class("kept")
        else:
            child.box.remove_css_class("kept")

    def toggle_keep(self, child):
        src = child.path
        dst_dir = self.cache if is_kept(src) else os.path.join(self.cache, "kept")
        os.makedirs(dst_dir, exist_ok=True)
        dst = os.path.join(dst_dir, os.path.basename(src))
        try:
            os.rename(src, dst)      # keeps the mtime, so the order stays
        except OSError:
            return
        child.path = dst
        self.relabel(child)

    def clear_all(self, *_):
        dialog = Gtk.AlertDialog(message="Clear the history?",
                                 detail="Every snip that is not kept (★) is deleted.",
                                 buttons=["Cancel", "Clear"], cancel_button=0, default_button=1)

        def done(d, result):
            try:
                if d.choose_finish(result) != 1:
                    return
            except GLib.Error:
                return
            self.clear_unkept()
        dialog.choose(self, None, done)

    def clear_unkept(self):
        children, c = [], self.flow.get_first_child()
        while c is not None:
            children.append(c); c = c.get_next_sibling()
        for child in children:
            if not is_kept(child.path):
                self.remove(child)

    def selected(self):
        sel = self.flow.get_selected_children()
        return sel[0] if sel else None

    def on_activate(self, flow, child):
        self.pin(child.path)

    def pin(self, path):
        subprocess.Popen([self.snip_pin, "pin", path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.close()

    def copy(self, path):
        with open(path, "rb") as f:
            subprocess.Popen(["wl-copy", "-t", "image/png"], stdin=f,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.Popen(["notify-send", "-i", "camera-photo-symbolic", "-t", "1500", "Snip", "Copied to clipboard"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.close()

    def on_rclick(self, gesture, n, x, y):
        child = self.flow.get_child_at_pos(int(x), int(y))
        if child is not None:
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)
            self.copy(child.path)

    def remove(self, child):
        try:
            os.remove(child.path)
        except OSError:
            pass
        idx = child.get_index()
        self.flow.remove(child)
        nxt = self.flow.get_child_at_index(idx) or self.flow.get_child_at_index(max(idx - 1, 0))
        if nxt:
            self.flow.select_child(nxt)
            nxt.grab_focus()
        else:
            self.empty.set_visible(True)

    def on_key(self, ctrl, keyval, keycode, state):
        if keyval == Gdk.KEY_Escape:
            self.close(); return True
        child = self.selected()
        if child is None:
            return False
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter, Gdk.KEY_space):
            self.pin(child.path); return True
        if keyval == Gdk.KEY_Delete:
            self.remove(child); return True
        if keyval in (Gdk.KEY_f, Gdk.KEY_F, Gdk.KEY_asterisk):
            self.toggle_keep(child); return True
        step = MOVE_KEYS.get(keyval)
        if step is not None:
            self.move_selection(child, step); return True
        return False

    def move_selection(self, child, step):
        nxt = self.flow.get_child_at_index(child.get_index() + step)
        if nxt is not None:
            self.flow.select_child(nxt)
            nxt.grab_focus()


def main():
    if len(sys.argv) < 3:
        print(__doc__); sys.exit(1)
    cache, snip_pin = sys.argv[1], sys.argv[2]
    app = Gtk.Application(application_id=None, flags=Gio.ApplicationFlags.NON_UNIQUE)

    def activate(app):
        css = Gtk.CssProvider()
        css.load_from_string(CSS)
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), css,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        History(app, cache, snip_pin).present()
    app.connect("activate", activate)
    GLib.set_prgname(APP_ID)
    Gtk.Window.set_default_icon_name(APP_ID)
    app.run([sys.argv[0]])


if __name__ == "__main__":
    main()
