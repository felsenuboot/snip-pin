#!/usr/bin/env python3
"""Thumbnail picker for previous snips (GTK4).

usage: pin-history.py CACHE_DIR SNIP_PIN_SH

  click / Enter   pin the snip again (where it was taken, if known)
  right-click     copy the snip to the clipboard and close
  Delete          remove it from the cache
  Esc             close (right-click on empty space closes too)

Snips are the PNG files in CACHE_DIR, newest first. Pinning runs
`SNIP_PIN_SH pin FILE`, which restores the capture position from the name.
"""
import os, subprocess, sys, datetime, warnings
warnings.filterwarnings("ignore", category=DeprecationWarning)
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gtk, Gdk, GdkPixbuf, GLib, Gio

APP_ID = "snip-pin"          # same class as pins: the float rule applies
THUMB_W, THUMB_H = 220, 140
COLUMNS = 4

CSS = """
window.snip-history { border: 2px solid #ff9f1c; }
.snip-thumb { padding: 6px; border-radius: 8px; }
.snip-thumb picture { border-radius: 4px; }
.snip-thumb label { font-size: 0.85em; opacity: 0.8; }
"""


def snips(cache):
    files = [os.path.join(cache, f) for f in os.listdir(cache) if f.endswith(".png")]
    return sorted(files, key=os.path.getmtime, reverse=True)


class History(Gtk.ApplicationWindow):
    def __init__(self, app, cache, snip_pin):
        super().__init__(application=app, title="Snip history")
        self.cache, self.snip_pin = cache, snip_pin
        self.add_css_class("snip-history")
        self.set_default_size(COLUMNS * (THUMB_W + 28) + 24, 620)

        self.flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.SINGLE, homogeneous=True,
                                min_children_per_line=COLUMNS, max_children_per_line=COLUMNS,
                                row_spacing=6, column_spacing=6, activate_on_single_click=True)
        self.flow.set_margin_top(12); self.flow.set_margin_bottom(12)
        self.flow.set_margin_start(12); self.flow.set_margin_end(12)
        self.flow.set_valign(Gtk.Align.START)
        self.flow.connect("child-activated", self.on_activate)

        self.empty = Gtk.Label(label="No snips in the last days.", vexpand=True)
        scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        scroller.set_child(self.flow)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.append(scroller)
        box.append(self.empty)
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
        when = datetime.datetime.fromtimestamp(os.path.getmtime(path))
        label = Gtk.Label(label=f"{when:%a %H:%M}  ·  {w}×{h}")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.add_css_class("snip-thumb")
        box.append(pic); box.append(label)
        child = Gtk.FlowBoxChild()
        child.set_child(box)
        child.path = path
        self.flow.append(child)
        if not self.flow.get_selected_children():
            self.flow.select_child(child)
            child.grab_focus()
        return True

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
        return False


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
