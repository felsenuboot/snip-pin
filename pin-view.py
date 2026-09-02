#!/usr/bin/env python3
"""Snipaste-style pinned image window for Hyprland (GTK4).

usage: pin-view.py IMAGE [X Y]

  drag           move            wheel             zoom (10% steps)
  Ctrl+wheel     opacity         Ctrl+0 / 1        reset zoom / opacity
  Ctrl+C         copy image      Ctrl+S            save to screenshot folder
  right-click    menu            Esc / dbl-click   close
"""
import os, subprocess, sys, time, json, datetime, shutil
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gtk, Gdk, GdkPixbuf, GLib, Gio

APP_ID = "snip-pin"
ZOOM_STEP = 1.10
MIN_PX = 40

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

class Pin(Gtk.ApplicationWindow):
    def __init__(self, app, path, pos):
        super().__init__(application=app, title="snip-pin")
        self.path = path
        self.pos = pos
        self.pixbuf = GdkPixbuf.Pixbuf.new_from_file(path)
        self.iw, self.ih = self.pixbuf.get_width(), self.pixbuf.get_height()
        self.scale = 1.0
        self.opacity = 1.0
        self.set_decorated(False)
        self.set_resizable(False)

        self.area = Gtk.DrawingArea()
        self.area.set_draw_func(self.draw)
        self.set_child(self.area)
        self.apply_scale()

        drag = Gtk.GestureDrag(button=1)
        drag.connect("drag-begin", self.on_drag_begin)
        self.area.add_controller(drag)

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
                         ("reset", lambda *a: self.set_scale(1.0)), ("close", lambda *a: self.close())):
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
        self.area.queue_draw()

    def set_scale(self, s):
        self.scale = max(MIN_PX / max(self.iw, self.ih), min(s, 8.0))
        self.apply_scale()

    def draw(self, area, cr, w, h):
        cr.scale(w / self.iw, h / self.ih)
        Gdk.cairo_set_source_pixbuf(cr, self.pixbuf, 0, 0)
        cr.get_source().set_filter(1)  # cairo.FILTER_GOOD
        cr.paint()

    # ---- placement via Hyprland (apps cannot position themselves on Wayland)
    def place(self):
        if self.pos is None:
            return
        x, y = self.pos
        deadline = time.time() + 2
        def tick():
            try:
                clients = json.loads(subprocess.run(["hyprctl", "clients", "-j"], capture_output=True, text=True).stdout)
            except Exception:
                return time.time() < deadline
            for c in clients:
                if c.get("pid") == os.getpid():
                    subprocess.run(["hyprctl", "dispatch",
                        f"hl.dsp.window.move({{ x = {x}, y = {y}, exact = true, window = 'address:{c['address']}' }})"],
                        capture_output=True)
                    return False
            return time.time() < deadline
        GLib.timeout_add(15, tick)

    # ---- input ------------------------------------------------------------
    def on_drag_begin(self, gesture, x, y):
        surface = self.get_surface()
        if isinstance(surface, Gdk.Toplevel):
            surface.begin_move(gesture.get_device(), 1, x, y, gesture.get_current_event_time())

    def on_click(self, gesture, n, x, y):
        if n == 2:
            self.close()

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
        if keyval == Gdk.KEY_Escape:
            self.close(); return True
        if ctrl_held and keyval in (Gdk.KEY_c, Gdk.KEY_C):
            self.copy(); return True
        if ctrl_held and keyval in (Gdk.KEY_s, Gdk.KEY_S):
            self.save(); return True
        if ctrl_held and keyval == Gdk.KEY_0:
            self.set_scale(1.0); return True
        if ctrl_held and keyval == Gdk.KEY_1:
            self.opacity = 1.0; self.set_opacity(1.0); return True
        return False

    # ---- actions ----------------------------------------------------------
    def build_menu(self):
        m = Gio.Menu()
        m.append("Copy image\tCtrl+C", "win.copy")
        m.append("Save to screenshots\tCtrl+S", "win.save")
        m.append("Reset zoom\tCtrl+0", "win.reset")
        m.append("Close\tEsc", "win.close")
        return m

    def copy(self, *a):
        with open(self.path, "rb") as f:
            subprocess.run(["wl-copy", "--type", "image/png"], stdin=f)
        self.notify_user("Copied to clipboard")

    def save(self, *a):
        folder = screenshot_folder()
        os.makedirs(folder, exist_ok=True)
        dest = os.path.join(folder, datetime.datetime.now().strftime("pin_%Y%m%d_%H%M%S.png"))
        shutil.copyfile(self.path, dest)
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
