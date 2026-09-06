#!/usr/bin/env python3
"""Snipaste-style pinned image window for Hyprland (GTK4).

usage: pin-view.py IMAGE [X Y]

  drag           move            wheel             zoom (10% steps)
  Ctrl+wheel     opacity         Ctrl+0            reset (zoom, opacity, rotation)
  Ctrl+R / Ctrl+Shift+R  rotate   Ctrl+H / Ctrl+J   flip   Ctrl+1  reset opacity
  Ctrl+C         copy image & close        Ctrl+S    save to screenshot folder & close
  Ctrl+Shift+S   save as (dialog; PNG, JPEG or WebP by extension), the pin stays
  Ctrl+P         print (GTK's dialog, also print to PDF), the pin stays
  dbl-click      copy image & close        Esc       close (snip-pin.sh reopen brings it back)
  Shift+Esc      destroy: close for good
  right-click    copy image & close        middle-click  menu

Annotations (toolbar under the pin while the pointer hovers it, or keys):
  R rectangle   E ellipse   A arrow   P pen   T text   M marker   B blur (mosaic)
  N counter (click: 1, 2, 3 ...)   C crop (drag, Enter applies, Esc cancels; undoable)
  1-7 colour    [ ] stroke width    Ctrl+Z / Ctrl+Shift+Z undo / redo
  With a tool selected, left-drag draws; press its key again (or Esc) to
  deselect. Copy and save bake the annotations into the image.
"""
import atexit
import datetime
import json
import math
import os
import re
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
if os.environ.get("SNIP_PIN_SOCKET"):
    SOCKET = os.environ["SNIP_PIN_SOCKET"]          # override: tests
elif sys.platform.startswith("linux"):
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


COMMANDS = ("--toggle", "--close-all", "--click-through", "--reopen")   # requests to the running pins, not a file

if __name__ == "__main__" and len(sys.argv) >= 2:
    if sys.argv[1] in COMMANDS:
        if hand_over(sys.argv[1:2]) or sys.argv[1] != "--reopen":
            sys.exit(0)                        # no viewer: no pins to act on; only --reopen starts one
    elif hand_over([os.path.abspath(sys.argv[1])] + sys.argv[2:]):
        sys.exit(0)

warnings.filterwarnings("ignore", category=DeprecationWarning)
import cairo
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Gdk, GdkPixbuf, Gio, GLib, GObject, Gtk, Pango, PangoCairo

APP_ID = "snip-pin"
ZOOM_STEP = 1.10
MIN_PX = 40                                        # the pin's longer side never shrinks below this
MIN_SIDE = 4                                       # ... and the shorter side stays grabbable
MAX_SCALE = 8.0
BORDER = 2                                         # px, drawn by the viewer itself
DEFAULT_BORDER_COLOR = "#ff9f1c"
EDIT_COLOR = "#3fa7ff"                             # border tint while a tool is active

# ---- configuration ----------------------------------------------------------
# ~/.config/snip-pin/config: `key = value` lines, `#` comments, and the
# sections [keys] and [mouse] for bindings. An environment variable
# SNIP_PIN_<KEY> overrides a top-level key. The viewer is one long-lived
# process for every pin, so the file is re-read whenever a pin is opened and a
# change applies to the next pin without a restart.
CONFIG_PATH = os.environ.get("SNIP_PIN_CONFIG") or os.path.join(
    os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"), "snip-pin", "config")


def parse_config(text):
    """{"": {...}, "keys": {...}, "mouse": {...}}: sections of lower-case key -> value."""
    out, section = {"": {}}, ""
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        head = re.split(r"\s#", line, 1)[0].strip()
        if head.startswith("[") and head.endswith("]"):
            section = head[1:-1].strip().lower()
            out.setdefault(section, {})
            continue
        if "=" not in line:
            continue
        key, val = (s.strip() for s in line.split("=", 1))
        # a trailing comment is a `#` after a blank with a value before it, so
        # `border = #ff9f1c  # orange` keeps the colour and drops the note
        val = re.split(r"\s#", val, 1)[0].strip()
        if val == "#" or val.startswith("# "):
            val = ""
        if key and key.replace("_", "a").isalnum():
            out[section][key.lower()] = val
    return out


class Config:
    def __init__(self, path=CONFIG_PATH):
        self.path = path
        self.stamp = None
        self.data = {"": {}}
        self.reload()

    def reload(self):
        """Re-read the file if it changed; True when the settings may differ."""
        try:
            st = os.stat(self.path)
            stamp = (st.st_mtime_ns, st.st_size)
        except OSError:
            stamp = None
        if stamp == self.stamp:
            return False
        self.stamp = stamp
        data = {"": {}}
        if stamp is not None:
            try:
                with open(self.path, encoding="utf-8", errors="replace") as f:
                    data = parse_config(f.read())
            except OSError:
                pass
        self.data = data
        return True

    def get(self, key, default=""):
        """A top-level setting: the environment first, then the file, then the default."""
        env = os.environ.get("SNIP_PIN_" + key.upper())
        if env is not None:
            return env
        return self.data[""].get(key, default)

    def section(self, name):
        return self.data.get(name, {})


CFG = Config()


def alpha_background():
    """How transparent parts of an image show: "transparent" (the desktop shows
    through), "checker", or a solid colour ("#rrggbb"). Setting alpha_bg."""
    v = CFG.get("alpha_bg", "transparent").strip().lower()
    if v in ("checker", "checkerboard"):
        return "checker"
    if len(v) == 7 and v.startswith("#") and all(ch in "0123456789abcdef" for ch in v[1:]):
        return v
    return "transparent"


def default_opacity():
    """Starting opacity of a new pin in percent (setting opacity, 10-100)."""
    try:
        return min(100, max(10, int(CFG.get("opacity", "100")))) / 100
    except ValueError:
        return 1.0


_checker = None


def checker_pattern():
    global _checker
    if _checker is None:
        surf = cairo.ImageSurface(cairo.FORMAT_RGB24, 16, 16)
        cr = cairo.Context(surf)
        cr.set_source_rgb(0.80, 0.80, 0.80); cr.paint()
        cr.set_source_rgb(0.55, 0.55, 0.55)
        cr.rectangle(0, 0, 8, 8); cr.rectangle(8, 8, 8, 8); cr.fill()
        _checker = cairo.SurfacePattern(surf)
        _checker.set_extend(cairo.EXTEND_REPEAT)
    return _checker


def default_tool():
    """The tool a new pin starts with (default_tool), or None; unknown names are reported once."""
    name = CFG.get("default_tool", "none").strip().lower()
    if name in ("", "none"):
        return None
    if name in {t for t, _, _, _ in TOOLS}:
        return name
    if name not in _warned:
        _warned.add(name)
        print(f"pin-view: config default_tool = {name}: unknown tool", file=sys.stderr)
    return None


_warned = set()


def border_color():
    c = CFG.get("border", DEFAULT_BORDER_COLOR).strip()
    if len(c) in (7, 9) and c.startswith("#") and all(ch in "0123456789abcdefABCDEF" for ch in c[1:]):
        return c
    return DEFAULT_BORDER_COLOR

# ---- closed pins ----------------------------------------------------------
# A closed pin is written to $XDG_RUNTIME_DIR/snip-pin/closed as the current
# image (crop and rotation baked in) plus a JSON file with position, zoom,
# opacity and the drawing annotations, so `snip-pin.sh reopen` brings it back
# as it was, even after the viewer process has ended with its last pin.
CLOSED_DIR = os.path.join(RUNTIME_DIR, "snip-pin", "closed")


def reopen_limit():
    try:
        return max(0, int(CFG.get("reopen", "5")))
    except ValueError:
        return 5


def ops_to_json(ops):
    """Drawing ops only, plain JSON types."""
    out = []
    for op in ops:
        if op["kind"] in MARKERS:
            continue
        o = {k: v for k, v in op.items() if not k.startswith("_")}
        o["pts"] = [[float(x), float(y)] for x, y in op["pts"]]
        o["color"] = list(op["color"])
        out.append(o)
    return out


def ops_from_json(data):
    ops = []
    for o in data:
        if not isinstance(o, dict) or "kind" not in o or "pts" not in o:
            continue
        op = dict(o)
        op["pts"] = [(float(x), float(y)) for x, y in o["pts"]]
        op["color"] = tuple(o.get("color", (1, 0, 0)))
        op.setdefault("width", 4)
        op.setdefault("text", "")
        ops.append(op)
    return ops


def closed_entries():
    """Stored closed pins, newest first: (json path, png path)."""
    try:
        names = sorted(n[:-5] for n in os.listdir(CLOSED_DIR) if n.endswith(".json"))
    except OSError:
        return []
    return [(os.path.join(CLOSED_DIR, n + ".json"), os.path.join(CLOSED_DIR, n + ".png")) for n in reversed(names)]


def prune_closed(limit):
    for j, png in closed_entries()[limit:]:
        for f in (j, png):
            try:
                os.unlink(f)
            except OSError:
                pass


# ---- annotation presets --------------------------------------------------
COLORS = [("red", "#e5312b"), ("orange", "#ff8c1a"), ("yellow", "#ffd21f"),
          ("green", "#2fbf4f"), ("blue", "#2f7fe5"), ("white", "#ffffff"),
          ("black", "#000000")]
WIDTHS = [("thin", 2), ("normal", 4), ("thick", 7)]      # stroke width in image px
TOOLS = [("rect", "R", "Rect", "Rectangle outline"), ("ellipse", "E", "Ellipse", "Ellipse outline"),
         ("arrow", "A", "Arrow", "Arrow"), ("pen", "P", "Pen", "Freehand pen"),
         ("text", "T", "Text", "Text: click, type, Enter"), ("counter", "N", "1 2 3", "Numbered step: click"),
         ("marker", "M", "Mark", "Highlighter"), ("blur", "B", "Blur", "Mosaic (hide secrets)"),
         ("crop", "C", "Crop", "Crop: drag, Enter applies, Esc cancels")]
CLICK_TOOLS = ("text", "counter")                  # placed with a click, not a drag
MARKER_ALPHA = 0.4
MARKER_FACTOR = 3.5                                # marker stroke = width * factor
TEXT_PX = {2: 16, 4: 22, 7: 30}                    # font size per stroke width
COUNTER_R = {2: 11, 4: 14, 7: 18}                  # counter badge radius per stroke width
MOSAIC_PX = {2: 5, 4: 9, 7: 14}                    # block size per stroke width
TOOLBAR_HIDE_MS = 300                              # grace period after the pointer leaves
SMOOTH_STEP = 30.0                                 # touchpad scroll units per zoom/opacity step
OSD_MS = 700                                       # how long the zoom / opacity readout stays

def build_css(border):
    return f"""
window.snip-pin {{
    background: transparent;
    border: {BORDER}px solid {border};
    box-shadow: inset 0 0 0 1px rgba(0, 0, 0, 0.45);   /* dark inner line for light pages */
}}
window.snip-pin.editing {{ border-color: {EDIT_COLOR}; }}
window.snip-pin.ghost {{ border-style: dashed; }}          /* click-through: the mouse goes to what is below */
.snip-toolbar button {{ padding: 2px 7px; min-height: 22px; min-width: 0; }}
.snip-toolbar .swatch {{ min-width: 14px; min-height: 14px; padding: 0; margin: 4px 1px;
                         border-radius: 9px; border: 1px solid rgba(0,0,0,0.5); }}
.snip-toolbar .swatch.sel {{ box-shadow: 0 0 0 2px {EDIT_COLOR}; }}
""" + "".join(f".snip-toolbar .swatch.c{i} {{ background: {hexc}; }}\n"
              for i, (_, hexc) in enumerate(COLORS))


# ---- key and mouse bindings -----------------------------------------------
# Every keyboard action with its default binding; [keys] in the config file
# overrides an entry ("copy = ctrl+shift+c"), several bindings are separated
# by spaces, an empty value unbinds. Colours stay on the digits.
ACTIONS = {
    "copy": "ctrl+c", "save": "ctrl+s", "save_as": "ctrl+shift+s", "print": "ctrl+p",
    "close": "", "destroy": "shift+Escape",
    "cancel": "Escape", "confirm": "Return KP_Enter",
    "undo": "ctrl+z", "redo": "ctrl+shift+z ctrl+y",
    "reset": "ctrl+0", "reset_zoom": "", "reset_opacity": "ctrl+1", "click_through": "ctrl+t",
    "rotate_cw": "ctrl+r", "rotate_ccw": "ctrl+shift+r", "flip_h": "ctrl+h", "flip_v": "ctrl+j", "smooth": "",
    "thumbnail": "ctrl+m shift+Return",
    "width_down": "bracketleft", "width_up": "bracketright", "menu": "F10",
}
ACTIONS.update({f"tool_{tool}": key.lower() for tool, key, _, _ in TOOLS})
ACTIONS.update({f"color_{i + 1}": str(i + 1) for i in range(len(COLORS))})
# what the mouse does; [mouse] in the config file overrides ("right = menu")
MOUSE_DEFAULTS = {"right": "copy", "double": "copy", "shift_double": "thumbnail", "middle": "menu"}
MOUSE_ACTIONS = ("copy", "save", "save_as", "print", "close", "destroy", "menu", "reset", "reset_zoom",
                 "thumbnail", "none")
MOD_NAMES = {"ctrl": "CONTROL_MASK", "control": "CONTROL_MASK", "shift": "SHIFT_MASK",
             "alt": "ALT_MASK", "super": "SUPER_MASK", "win": "SUPER_MASK", "meta": "META_MASK"}
KEY_ALIASES = {"esc": "Escape", "enter": "Return", "del": "Delete", "[": "bracketleft", "]": "bracketright",
               "+": "plus", "-": "minus", "=": "equal", ",": "comma", ".": "period", "space": "space",
               "tab": "Tab", "backspace": "BackSpace", "pgup": "Page_Up", "pgdn": "Page_Down"}


def parse_binding(spec):
    """'ctrl+shift+z' -> (lower keyval, modifier mask), or None for an unknown key."""
    s = spec.strip()
    if not s:
        return None
    parts = s[:-1].split("+") + ["plus"] if s.endswith("+") else s.split("+")   # "ctrl++" binds Ctrl and +
    parts = [p for p in parts if p]
    mods = 0
    for p in parts[:-1]:
        name = MOD_NAMES.get(p.lower())
        if name is None:
            return None
        mods |= int(getattr(Gdk.ModifierType, name))
    key = parts[-1]
    key = KEY_ALIASES.get(key.lower(), key)
    keyval = 0
    if len(key) == 1:
        keyval = Gdk.unicode_to_keyval(ord(key))
    else:
        segs = key.split("_")
        for variant in (key, key.capitalize(), key.upper(), "_".join(s.capitalize() for s in segs),
                        "_".join([segs[0].upper()] + [s.capitalize() for s in segs[1:]])):   # Page_Up, KP_Enter
            keyval = Gdk.keyval_from_name(variant)
            if keyval not in (0, Gdk.KEY_VoidSymbol):
                break
    if keyval in (0, Gdk.KEY_VoidSymbol):
        return None
    return Gdk.keyval_to_lower(keyval), mods


MOD_MASK = int(Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.SHIFT_MASK
               | Gdk.ModifierType.ALT_MASK | Gdk.ModifierType.SUPER_MASK | Gdk.ModifierType.META_MASK)


def build_keymap(overrides):
    """{(keyval, mods): action} from the defaults and the [keys] section; bad specs are reported and skipped."""
    keymap = {}
    for action, default in ACTIONS.items():
        spec = overrides.get(action, default) if action in overrides else default
        for one in spec.split():
            b = parse_binding(one)
            if b is None:
                print(f"pin-view: config [keys] {action} = {one}: unknown key", file=sys.stderr)
                continue
            keymap[b] = action
    for action in overrides:
        if action not in ACTIONS:
            print(f"pin-view: config [keys] {action}: unknown action", file=sys.stderr)
    return keymap


def build_mouse(overrides):
    mouse = dict(MOUSE_DEFAULTS)
    for button, action in overrides.items():
        if button in mouse and action in MOUSE_ACTIONS:
            mouse[button] = action
        else:
            print(f"pin-view: config [mouse] {button} = {action}: unknown button or action", file=sys.stderr)
    return mouse


def key_label(action):
    """The first binding of an action as shown in menus and tooltips: 'Ctrl+Shift+Z'."""
    for (keyval, mods), name in KEYMAP.items():
        if name != action:
            continue
        mt = Gdk.ModifierType
        parts = [label for mask, label in ((mt.CONTROL_MASK, "Ctrl"), (mt.SHIFT_MASK, "Shift"),
                                           (mt.ALT_MASK, "Alt"), (mt.SUPER_MASK, "Super")) if mods & int(mask)]
        key = Gdk.keyval_name(keyval) or "?"
        key = {"Escape": "Esc", "bracketleft": "[", "bracketright": "]", "Return": "Enter"}.get(key, key)
        return "+".join(parts + [key.upper() if len(key) == 1 else key])
    return ""


KEYMAP = build_keymap(CFG.section("keys"))
MOUSE = build_mouse(CFG.section("mouse"))
_css = None


def apply_config(force=False):
    """Re-read the config file; refresh the CSS, the key map and the mouse map when it changed."""
    global KEYMAP, MOUSE, _css
    if not CFG.reload() and not force:
        return
    KEYMAP = build_keymap(CFG.section("keys"))
    MOUSE = build_mouse(CFG.section("mouse"))
    if _css is None:
        _css = Gtk.CssProvider()
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), _css,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    _css.load_from_string(build_css(border_color()))


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

# ---- sound -------------------------------------------------------------------
CANBERRA = shutil.which("canberra-gtk-play")
THEME_SOUNDS = ["/usr/share/sounds/freedesktop/stereo/screen-capture.oga",
                "/usr/share/sounds/freedesktop/stereo/camera-shutter.oga"]


def sound_command(setting, canberra=CANBERRA):
    """argv that plays the configured sound, or None: `sound = default` plays the
    theme's screen-capture event, a path plays that file, anything else is off."""
    v = (setting or "").strip()
    if v.lower() in ("", "0", "off", "no", "false", "none"):
        return None
    if canberra:
        return [canberra, "-i", "screen-capture"] if v == "default" else [canberra, "-f", os.path.expanduser(v)]
    return None


_media = []


def play_sound():
    """Copy / save feedback: canberra-gtk-play when installed, else GTK's own player."""
    setting = CFG.get("sound", "")
    argv = sound_command(setting)
    try:
        if argv:
            subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return
        v = setting.strip()
        if v.lower() in ("", "0", "off", "no", "false", "none"):
            return
        path = next((p for p in THEME_SOUNDS if os.path.exists(p)), None) if v == "default" else os.path.expanduser(v)
        if path and os.path.exists(path):
            media = Gtk.MediaFile.new_for_filename(path)
            _media.append(media)                                  # keep it alive while it plays
            media.connect("notify::ended", lambda m, p: _media.remove(m) if m in _media else None)
            media.play()
    except (OSError, GLib.Error) as e:
        print(f"pin-view: sound: {e}", file=sys.stderr)


def scroll_steps(wheel, dy, acc):
    """(steps, new_acc): a wheel notch is one step; smooth deltas accumulate, one step per SMOOTH_STEP."""
    if wheel:
        return int(dy), acc
    acc += dy
    steps = int(acc / SMOOTH_STEP)
    return steps, acc - steps * SMOOTH_STEP

# ---- Hyprland IPC -----------------------------------------------------------
# Requests go straight to Hyprland's socket (0.2 ms) instead of spawning
# hyprctl (10 ms): the placement loop runs on the GTK main loop and must not
# stall the first frames of a pin.
HYPR_SOCKET = None
_sig = os.environ.get("HYPRLAND_INSTANCE_SIGNATURE")
if _sig:
    for base in (os.environ.get("XDG_RUNTIME_DIR"), "/tmp"):
        if base and os.path.exists(os.path.join(base, "hypr", _sig, ".socket.sock")):
            HYPR_SOCKET = os.path.join(base, "hypr", _sig, ".socket.sock")
            break


def hypr(cmd):
    """One request to Hyprland ('j/clients', 'dispatch ...'); '' when unavailable."""
    if HYPR_SOCKET:
        s = socket.socket(socket.AF_UNIX)
        s.settimeout(1)
        try:
            s.connect(HYPR_SOCKET)
            s.sendall(cmd.encode())
            buf = b""
            while True:
                chunk = s.recv(65536)
                if not chunk:
                    break
                buf += chunk
            return buf.decode(errors="replace")
        except OSError:
            return ""
        finally:
            s.close()
    try:
        argv = ["hyprctl"] + (["-j", cmd[2:]] if cmd.startswith("j/") else cmd.split(" ", 1))
        return subprocess.run(argv, capture_output=True, text=True, timeout=2).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def hypr_json(cmd):
    try:
        return json.loads(hypr(cmd) or "null")
    except ValueError:
        return None


_move_syntax = None            # "lua" (0.56+ with a Lua config) or "classic", found on first use


def move_window(address, x, y):
    """Move a window to an exact position, with whichever dispatcher syntax this Hyprland takes."""
    global _move_syntax
    forms = {"lua": f"dispatch hl.dsp.window.move({{ x = {x}, y = {y}, exact = true, window = 'address:{address}' }})",
             "classic": f"dispatch movewindowpixel exact {x} {y},address:{address}"}
    order = [_move_syntax] if _move_syntax else ["lua", "classic"]
    for name in order:
        if hypr(forms[name]).strip() == "ok":
            _move_syntax = name
            return True
    return False


def set_no_focus(address, on):
    """Hyprland: a no_focus window is skipped when the window under the pointer is
    looked up, so every click goes to what is below (the Wayland input region
    alone is not honoured for toplevels). Lua and classic syntax; the property
    takes effect at Hyprland's next property refresh, which is requested too."""
    v = int(on)
    forms = [f"dispatch hl.dsp.window.set_prop({{ window = 'address:{address}', prop = 'no_focus', value = {v} }})",
             f"dispatch setprop address:{address} nofocus {v}"]
    for form in forms:
        if hypr(form).strip() == "ok":
            hypr("eval hl.exec_scheduled_prop_refresh_immediately()")
            return True
    return False


def monitor_at(monitors, x, y):
    """The monitor dict whose logical rectangle contains (x, y), else the first, else None."""
    for m in monitors or []:
        mx, my = m.get("x", 0), m.get("y", 0)
        scale = m.get("scale") or 1
        mw, mh = m.get("width", 0) / scale, m.get("height", 0) / scale
        if m.get("transform", 0) in (1, 3, 5, 7):
            mw, mh = mh, mw
        if mx <= x < mx + mw and my <= y < my + mh:
            return m
    return monitors[0] if monitors else None


def clamp_to_monitor(monitor, x, y, w, h):
    """Shift (x, y) so a w x h window stays inside the monitor's logical area."""
    if not monitor:
        return x, y
    mx, my = monitor.get("x", 0), monitor.get("y", 0)
    scale = monitor.get("scale") or 1
    mw, mh = monitor.get("width", 0) / scale, monitor.get("height", 0) / scale
    if monitor.get("transform", 0) in (1, 3, 5, 7):
        mw, mh = mh, mw
    x = max(mx, min(x, mx + mw - w)) if w <= mw else mx
    y = max(my, min(y, my + mh - h)) if h <= mh else my
    return int(round(x)), int(round(y))


def thumb_scale(w, h, size):
    """Scale that fits a w x h region into a size x size tile."""
    return size / max(1, w, h)


def thumb_size():
    try:
        return max(16, int(CFG.get("thumb_size", "75")))
    except ValueError:
        return 75


def zoom_shift(pointer, img_pt, new_scale):
    """How far the window must move so that img_pt (image coordinates) stays
    under the pointer (widget coordinates) after the image is drawn at new_scale."""
    return (round(pointer[0] - img_pt[0] * new_scale), round(pointer[1] - img_pt[1] * new_scale))


def pick_filter(smooth, scale, base_scale):
    """Bilinear as a rule; nearest neighbour when smoothing is off and the pin is zoomed in."""
    return cairo.FILTER_GOOD if smooth or scale <= base_scale * 1.001 else cairo.FILTER_NEAREST


STATE_DIR = os.path.join(GLib.get_user_state_dir(), "snip-pin")


def last_extension():
    """The extension the Save As dialog used last (.png, .jpg or .webp)."""
    try:
        with open(os.path.join(STATE_DIR, "last-ext")) as f:
            ext = f.read().strip()
        return ext if ext in EXTENSIONS.values() else ".png"
    except OSError:
        return ".png"


def remember_extension(ext):
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        with open(os.path.join(STATE_DIR, "last-ext"), "w") as f:
            f.write(ext)
    except OSError:
        pass


# ---- clipboard -------------------------------------------------------------
# The viewer owns the clipboard itself (GTK) instead of spawning wl-copy, so
# it can offer the image as image/png and as a file (text/uri-list) at once:
# an image editor takes the pixels, a file manager or a chat client the file.
# Whoever owns a Wayland clipboard must stay alive to serve it, so after the
# last pin closes the process lingers (app.hold) until another program takes
# the clipboard over, then exits and removes the file it offered.
CLIP_DIR = os.path.join(RUNTIME_DIR, "snip-pin", "clip")


def copy_as_file():
    return CFG.get("copy_file", "always").strip().lower() not in ("never", "0", "no", "false")


def clip_file_path(path):
    """Where the offered file lives: the export copied into the runtime dir, so
    it outlives the pin and the cache's expiry but not the session."""
    return os.path.join(CLIP_DIR, datetime.datetime.now().strftime("snip_%Y%m%d_%H%M%S") + os.path.splitext(path)[1])


class ClipboardOwner:
    """Sets the clipboard content and keeps the application alive while it is ours."""
    def __init__(self, app):
        self.app = app
        self.holding = False
        self.file = None
        self.clipboard = Gdk.Display.get_default().get_clipboard()
        self.clipboard.connect("changed", self.on_changed)

    def offer(self, png_path, as_file):
        texture = Gdk.Texture.new_from_filename(png_path)
        providers = [Gdk.ContentProvider.new_for_value(GObject.Value(Gdk.Texture, texture))]
        self.drop_file()
        if as_file:
            os.makedirs(CLIP_DIR, exist_ok=True)
            self.file = clip_file_path(png_path)
            shutil.copyfile(png_path, self.file)
            files = Gdk.FileList.new_from_list([Gio.File.new_for_path(self.file)])
            providers.append(Gdk.ContentProvider.new_for_value(GObject.Value(Gdk.FileList, files)))
        self.clipboard.set_content(Gdk.ContentProvider.new_union(providers))
        if not self.holding:
            self.holding = True
            self.app.hold()

    def on_changed(self, clipboard):
        if self.holding and not clipboard.is_local():        # somebody else owns it now
            self.holding = False
            self.drop_file()
            self.app.release()

    def drop_file(self):
        if self.file:
            try:
                os.unlink(self.file)
            except OSError:
                pass
            self.file = None


_clip = None


def clipboard_owner(app):
    global _clip
    if _clip is None:
        _clip = ClipboardOwner(app)
    return _clip


def unique_path(folder, stem, ext):
    """folder/stem.ext, or stem_2.ext, stem_3.ext ... if that exists already."""
    p = os.path.join(folder, stem + ext)
    n = 2
    while os.path.exists(p):
        p = os.path.join(folder, f"{stem}_{n}{ext}")
        n += 1
    return p

def screenshot_folder():
    """Where Ctrl+S saves: save_dir ($SNIP_PIN_SAVE_DIR), the ML4W setting, the XDG pictures dir, ~/Pictures."""
    d = CFG.get("save_dir", "").strip()
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

MARKERS = ("crop", "rotate", "flip")               # undo markers, not drawings


def transform_point(kind, arg, iw, ih):
    """A function (x, y) -> (x', y') for rotating an iw x ih image by `arg` quarter
    turns clockwise (kind "rotate") or flipping it (kind "flip", arg "h" or "v")."""
    if kind == "flip":
        return (lambda x, y: (iw - x, y)) if arg == "h" else (lambda x, y: (x, ih - y))
    turns = arg % 4
    if turns == 1:
        return lambda x, y: (ih - y, x)
    if turns == 2:
        return lambda x, y: (iw - x, ih - y)
    if turns == 3:
        return lambda x, y: (y, iw - x)
    return lambda x, y: (x, y)


def transform_ops(ops, kind, arg, iw, ih):
    """Rotate or flip every annotation with the image (in place); text keeps its orientation."""
    f = transform_point(kind, arg, iw, ih)
    for op in ops:
        if op["kind"] in MARKERS:
            continue
        op["pts"] = [f(x, y) for x, y in op["pts"]]
        op.pop("_mosaic", None)


def shift_ops(ops, dx, dy):
    """Move every annotation by (dx, dy) in image coordinates (after a crop)."""
    for op in ops:
        if op["kind"] in MARKERS:
            continue
        op["pts"] = [(x + dx, y + dy) for x, y in op["pts"]]
        op.pop("_mosaic", None)                    # the cached mosaic was cut from the old pixbuf


def crop_rect(pixbuf, pts):
    """The crop rectangle (x0, y0, w, h) clipped to the image, or None if too small."""
    x0, y0, x1, y1 = norm_rect(pts)
    x0, y0 = max(0, int(round(x0))), max(0, int(round(y0)))
    x1, y1 = min(pixbuf.get_width(), int(round(x1))), min(pixbuf.get_height(), int(round(y1)))
    if x1 - x0 < 4 or y1 - y0 < 4:
        return None
    return x0, y0, x1 - x0, y1 - y0


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
    if k in MARKERS:
        return                                     # an undo marker, not a drawing
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
    elif k == "ellipse":
        x0, y0, x1, y1 = norm_rect(pts)
        rx, ry = (x1 - x0) / 2, (y1 - y0) / 2
        if rx >= 0.5 and ry >= 0.5:
            cr.save()
            cr.translate(x0 + rx, y0 + ry)
            cr.scale(rx, ry)
            cr.arc(0, 0, 1, 0, 2 * math.pi)
            cr.restore()                              # the pen width must not scale with the ellipse
            cr.set_source_rgb(r, g, b)
            cr.set_line_width(w)
            cr.stroke()
    elif k == "counter":
        x, y = pts[0]
        rad = COUNTER_R.get(w, 14)
        cr.set_source_rgb(r, g, b)
        cr.arc(x, y, rad, 0, 2 * math.pi)
        cr.fill()
        layout = PangoCairo.create_layout(cr)
        layout.set_font_description(Pango.FontDescription.from_string(f"Sans Bold {int(rad * 1.2)}px"))
        layout.set_text(str(op.get("n", 1)), -1)
        _, logical = layout.get_pixel_extents()
        dark = (0.299 * r + 0.587 * g + 0.114 * b) < 0.5
        cr.set_source_rgb(1, 1, 1) if dark else cr.set_source_rgb(0, 0, 0)
        cr.move_to(x - logical.width / 2 - logical.x, y - logical.height / 2 - logical.y)
        PangoCairo.show_layout(cr, layout)
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
        layout.set_text(op["text"] + (op.get("_preedit", "") if caret else ""), -1)
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

def render_surface(pixbuf, ops):
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, pixbuf.get_width(), pixbuf.get_height())
    cr = cairo.Context(surf)
    Gdk.cairo_set_source_pixbuf(cr, pixbuf, 0, 0)
    cr.paint()
    for op in ops:
        draw_op(cr, pixbuf, op)
    surf.flush()
    return surf


def render_png(pixbuf, ops, out_path):
    """Bake pixbuf + ops into a PNG at native size."""
    render_surface(pixbuf, ops).write_to_png(out_path)
    return out_path


def render_pixbuf(pixbuf, ops):
    """Bake pixbuf + ops into a new pixbuf (for JPEG and WebP output)."""
    if not ops:
        return pixbuf
    surf = render_surface(pixbuf, ops)
    return Gdk.pixbuf_get_from_surface(surf, 0, 0, surf.get_width(), surf.get_height())


# ---- output files ----------------------------------------------------------
FORMATS = {"png": "png", "jpg": "jpeg", "jpeg": "jpeg", "webp": "webp"}     # extension -> GdkPixbuf type
EXTENSIONS = {"png": ".png", "jpeg": ".jpg", "webp": ".webp"}


def file_pattern():
    """strftime pattern for saved files (setting filename); no directory parts."""
    p = CFG.get("filename", "pin_%Y%m%d_%H%M%S").strip()
    return p if p and "/" not in p else "pin_%Y%m%d_%H%M%S"


def save_format():
    """GdkPixbuf type for quick save (setting format: png, jpg, webp)."""
    return FORMATS.get(CFG.get("format", "png").strip().lower().lstrip("."), "png")


def save_quality():
    try:
        return min(100, max(1, int(CFG.get("quality", "90"))))
    except ValueError:
        return 90


def flatten(pb):
    """An RGB copy of an RGBA pixbuf over white (JPEG has no alpha)."""
    dest = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, pb.get_width(), pb.get_height())
    dest.fill(0xffffffff)
    pb.composite(dest, 0, 0, pb.get_width(), pb.get_height(), 0, 0, 1.0, 1.0, GdkPixbuf.InterpType.NEAREST, 255)
    return dest


def write_image(pixbuf, ops, dest, fmt, quality=90):
    """Bake and encode to dest as PNG, JPEG (flattened on white) or WebP."""
    pb = render_pixbuf(pixbuf, ops)
    if fmt == "jpeg" and pb.get_has_alpha():
        pb = flatten(pb)
    if fmt == "png":
        pb.savev(dest, "png", [], [])
    else:
        pb.savev(dest, fmt, ["quality"], [str(quality)])
    return dest

_seq = 0


def output_scale(monitors, pos):
    """Scale factor of the output a positioned pin was captured on (1 without a position)."""
    if pos is None:
        return 1.0
    m = monitor_at(monitors, pos[0], pos[1])
    return float((m or {}).get("scale") or 1) or 1.0


class Pin(Gtk.ApplicationWindow):
    def __init__(self, app, path, pos, out_scale=1.0, state=None):
        # All pins share one process (see main), so the placement loop tells
        # windows apart by a unique title until each one has been placed.
        global _seq
        # load before the window exists: a window registered with the
        # application would keep the process alive after a failed load
        pixbuf = GdkPixbuf.Pixbuf.new_from_file(path)
        _seq += 1
        super().__init__(application=app, title=f"snip-pin #{_seq}")
        self.path = path
        self.pos = pos
        self.pixbuf = pixbuf
        self.iw, self.ih = self.pixbuf.get_width(), self.pixbuf.get_height()
        # grim captures at the output's scale, so on a 2x monitor the image has
        # twice the pixels of the logical region: show it at 1/scale so the pin
        # covers exactly the region it was taken from; zoom is relative to that
        self.base_scale = 1.0 / out_scale
        self.scale = max(self.base_scale, self.min_scale())   # tiny snips open enlarged, uniformly
        self.opacity = default_opacity()
        self.alpha_bg = alpha_background()
        # annotation state
        self.tool = None
        self.color_idx = 0
        self.width_idx = 1
        self.ops = []                 # committed annotations, in order
        self.redo_stack = []
        self.pending = None           # op being dragged out
        self.typing = None            # text op being typed
        self.crop_pending = None      # (x0, y0, w, h) waiting for Enter
        self.thumb = None             # thumbnail mode: {"scale": scale before, "region": (x0, y0, w, h) or None}
        self.turns = 0                # net quarter turns clockwise, flips: for "reset"
        self.flipped = {"h": False, "v": False}
        self.address = None           # Hyprland window address once known (placement, crop)
        self._syncing = False
        self.hovered = False
        self.hidden = False           # set by toggle_pins while every pin is hidden
        self.ghost = False            # click-through: an empty input region, see set_click_through
        self.toolbar_shown = False    # tracked ourselves: get_visible() is true during the fade-out
        self.hide_timer = None
        self.scroll_acc = 0.0         # smooth-scroll distance not yet turned into a step
        self.pointer = None           # last pointer position over the pin (widget coordinates)
        self.smooth = CFG.get("smooth", "1").strip() not in ("0", "no", "false")
        self.zoom_at_pointer = CFG.get("zoom_at_pointer", "1").strip() not in ("0", "no", "false")
        self.osd = None               # (text, timer id): zoom / opacity readout drawn on the pin

        self.set_decorated(False)
        self.set_resizable(False)
        self.add_css_class("snip-pin")
        apply_config(force=_css is None)          # first pin: install the CSS; later: pick up edits

        self.area = Gtk.DrawingArea()
        self.area.set_draw_func(self.draw)
        self.set_child(self.area)
        self.toolbar = self.build_toolbar()
        self.destroying = False       # Shift+Esc: close without a way back
        self.connect("close-request", self.on_close_request)
        if state:                     # a reopened pin: zoom, opacity and annotations as they were
            self.ops = ops_from_json(state.get("ops", []))
            self.scale = max(self.min_scale(), min(float(state.get("scale", self.scale)), MAX_SCALE))
            self.opacity = min(1.0, max(0.1, float(state.get("opacity", 1.0))))
        self.set_opacity(self.opacity)
        self.apply_scale()
        if default_tool() is not None:
            self.set_tool(default_tool())

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
        rclick.connect("pressed", lambda g, n, x, y: self.mouse_action("right", x, y))
        self.area.add_controller(rclick)

        mclick = Gtk.GestureClick(button=2)
        mclick.connect("pressed", lambda g, n, x, y: self.mouse_action("middle", x, y))
        self.area.add_controller(mclick)

        scroll = Gtk.EventControllerScroll(flags=Gtk.EventControllerScrollFlags.VERTICAL)
        scroll.connect("scroll", self.on_scroll)
        self.area.add_controller(scroll)

        hover = Gtk.EventControllerMotion()
        hover.connect("enter", lambda c, x, y: self.on_hover(True))
        hover.connect("leave", lambda c: self.on_hover(False))
        hover.connect("motion", lambda c, x, y: setattr(self, "pointer", (x, y)))
        self.area.add_controller(hover)

        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self.on_key)
        self.add_controller(keys)
        # Text goes through an input method context while typing: dead keys,
        # Compose, ibus/fcitx (CJK, emoji) all work; it is focused only while
        # a text op is open so tool shortcuts keep working otherwise.
        self.im = Gtk.IMMulticontext()
        self.im.set_client_widget(self.area)
        self.im.connect("commit", self.on_im_commit)
        self.im.connect("preedit-changed", self.on_im_preedit)

        # dropping image files (from a file manager) opens each as a new pin
        drop = Gtk.DropTarget.new(Gio.File, Gdk.DragAction.COPY)
        drop.set_gtypes([Gdk.FileList, Gio.File])
        drop.connect("drop", self.on_drop)
        self.area.add_controller(drop)

        self.menu = Gtk.PopoverMenu.new_from_model(self.build_menu())
        self.menu.set_parent(self.area)
        self.menu.set_has_arrow(False)
        for name in ("copy", "save", "save_as", "print", "reset", "close", "destroy", "undo", "redo", "click_through",
                     "thumbnail", "rotate_cw", "rotate_ccw", "flip_h", "flip_v"):
            act = Gio.SimpleAction.new(name, None)
            act.connect("activate", lambda *a, name=name: self.run_action(name))
            self.add_action(act)
        self.smooth_action = Gio.SimpleAction.new_stateful("smooth", None, GLib.Variant.new_boolean(self.smooth))
        self.smooth_action.connect("activate", lambda a, p: self.set_smooth(not self.smooth))
        self.add_action(self.smooth_action)

    # ---- geometry -------------------------------------------------------
    def view(self):
        """The part of the image the window shows: everything, or a thumbnail's region."""
        if self.thumb is not None and self.thumb["region"] is not None:
            return self.thumb["region"]
        return (0, 0, self.iw, self.ih)

    def apply_scale(self):
        # both sides from the same scale: clamping each side on its own
        # stretched thin snips (a line of text at 800x18 became 800x40)
        _, _, vw, vh = self.view()
        w = max(1, round(vw * self.scale))
        h = max(1, round(vh * self.scale))
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
            if not self.toolbar_shown and self.thumb is None and not self.ghost:
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
        x0, y0, vw, vh = self.view()
        return (x0 + x * vw / self.area.get_width(), y0 + y * vh / self.area.get_height())

    def draw(self, area, cr, w, h):
        if self.ghost:
            self.apply_input_region()
        if self.pixbuf.get_has_alpha() and self.alpha_bg != "transparent":
            if self.alpha_bg == "checker":
                cr.set_source(checker_pattern())
            else:
                cr.set_source_rgb(*hex_to_rgb(self.alpha_bg))
            cr.paint()
        x0, y0, vw, vh = self.view()
        cr.scale(w / vw, h / vh)
        cr.translate(-x0, -y0)
        cr.rectangle(x0, y0, vw, vh)
        cr.clip()
        Gdk.cairo_set_source_pixbuf(cr, self.pixbuf, 0, 0)
        cr.get_source().set_filter(pick_filter(self.smooth, self.scale, self.base_scale))
        cr.paint()
        for op in self.ops:
            draw_op(cr, self.pixbuf, op)
        if self.pending is not None:
            draw_op(cr, self.pixbuf, self.pending)
        if self.typing is not None:
            draw_op(cr, self.pixbuf, self.typing, caret=True)
        rect = self.crop_pending
        if rect is None and self.pending is not None and self.pending["kind"] == "crop":
            rect = crop_rect(self.pixbuf, self.pending["pts"])
        if rect is not None:
            self.draw_crop(cr, rect)
        if self.osd is not None:
            self.draw_osd(cr, w, h)

    def draw_crop(self, cr, rect):
        """Dim everything outside the crop rectangle and outline it."""
        x0, y0, cw, ch = rect
        cr.save()
        cr.set_source_rgba(0, 0, 0, 0.55)
        cr.rectangle(0, 0, self.iw, self.ih)
        cr.rectangle(x0, y0, cw, ch)
        cr.set_fill_rule(cairo.FILL_RULE_EVEN_ODD)
        cr.fill()
        cr.restore()
        f = self.area.get_width() / self.iw or 1
        cr.set_source_rgb(1, 1, 1)
        cr.set_line_width(1.5 / f)
        cr.set_dash([6 / f, 4 / f])
        cr.rectangle(x0 + 0.5 / f, y0 + 0.5 / f, cw - 1 / f, ch - 1 / f)
        cr.stroke()

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

    # ---- thumbnail -------------------------------------------------------------
    def set_thumbnail(self, on, region=None):
        """Collapse the pin into a thumb_size tile (of the whole image, or of `region`
        in image coordinates) at its top-left corner, or restore the previous size."""
        c = self.client() if self.address else None
        if on and self.thumb is None:
            if self.typing is not None:
                self.commit_text()
            self.set_tool(None)
            if self.toolbar_shown:
                self.toolbar_shown = False
                self.toolbar.popdown()
            self.thumb = {"scale": self.scale, "region": region}
            _, _, vw, vh = self.view()
            self.scale = thumb_scale(vw, vh, thumb_size())
        elif not on and self.thumb is not None:
            self.scale = max(self.min_scale(), self.thumb["scale"])
            self.thumb = None
        else:
            return
        self.apply_scale()
        if c is not None:
            self.move_to(c["at"][0], c["at"][1])          # Hyprland re-centres on resize; keep the corner

    def toggle_thumbnail(self):
        if self.thumb is not None:
            self.set_thumbnail(False)
        else:
            region, self.crop_pending = self.crop_pending, None
            self.set_thumbnail(True, region)

    # ---- closing -----------------------------------------------------------------
    def on_close_request(self, *a):
        if not self.destroying and reopen_limit() > 0:
            self.record_closed()
        return False                                     # and close

    def record_closed(self):
        """Store the pin for `snip-pin.sh reopen`: image with crop and rotation baked in, plus state."""
        if self.typing is not None:
            self.commit_text()
        c = self.client() if self.address else None
        pos = (c["at"][0] + BORDER, c["at"][1] + BORDER) if c else self.pos
        try:
            os.makedirs(CLOSED_DIR, exist_ok=True)
            stem = os.path.join(CLOSED_DIR, datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f"))
            self.pixbuf.savev(stem + ".png", "png", [], [])
            scale = self.thumb["scale"] if self.thumb is not None else self.scale
            state = {"path": self.path, "pos": list(pos) if pos else None, "scale": scale,
                     "base_scale": self.base_scale, "opacity": self.opacity, "ops": ops_to_json(self.ops)}
            with open(stem + ".json", "w") as f:
                json.dump(state, f)
            prune_closed(reopen_limit())
        except (OSError, GLib.Error) as e:
            print(f"pin-view: cannot record the closed pin: {e}", file=sys.stderr)

    def destroy_pin(self):
        self.destroying = True
        self.close()

    # ---- click-through -------------------------------------------------------
    def set_click_through(self, on):
        """Let every click, drag and scroll pass to the window below (Snipaste's
        mouse click-through). The pin keeps no way to receive input, so the way
        back is the same request from a bind (`snip-pin.sh clickthrough`)."""
        self.ghost = on
        if self.address is None:
            c = self.client()
            self.address = c["address"] if c else None
        if self.address:
            set_no_focus(self.address, on)
        if on:
            self.add_css_class("ghost")
            self.set_tool(None)
            if self.toolbar_shown:
                self.toolbar_shown = False
                self.toolbar.popdown()
        else:
            self.remove_css_class("ghost")
        self.apply_input_region()
        self.show_osd("click-through" if on else "solid")

    def apply_input_region(self):
        """GTK recomputes the input region on every layout; draw() runs after
        layout in the same frame and re-applies the empty one while ghost.
        (A handler on the surface's "layout" signal would do too, but it makes
        GTK lay the window out every frame and Hyprland re-centre it endlessly.)"""
        surface = self.get_surface()
        if surface is None:
            return
        if self.ghost:
            surface.set_input_region(cairo.Region())
        else:
            surface.set_input_region(cairo.Region(cairo.RectangleInt(0, 0, surface.get_width(), surface.get_height())))

    # ---- placement via Hyprland (apps cannot position themselves on Wayland)
    def place(self):
        if self.pos is None:
            return
        x, y = self.pos[0] - BORDER, self.pos[1] - BORDER   # keep the image itself at pos
        w = round(self.iw * self.scale) + 2 * BORDER
        h = round(self.ih * self.scale) + 2 * BORDER
        x, y = clamp_to_monitor(monitor_at(hypr_json("j/monitors"), x, y), x, y, w, h)
        self.move_to(x, y)

    def client(self):
        """Our Hyprland client entry: by address once known, by the unique title before."""
        for c in hypr_json("j/clients") or []:
            if self.address:
                if c.get("address") == self.address:
                    return c
            elif c.get("pid") == os.getpid() and c.get("title") == self.get_title():
                return c
        return None

    def move_to(self, x, y, settle=6):
        """Move the window to (x, y) and keep it there while its size settles:
        Hyprland re-centres a floating window on every resize (after mapping,
        after a crop), so the move is repeated until it has held for a few ticks."""
        deadline = time.time() + 2
        state = {"ok": 0}

        def tick():
            c = self.client()
            if c is None:
                return time.time() < deadline
            self.address = c["address"]
            if list(c["at"]) == [x, y]:
                state["ok"] += 1
                return state["ok"] < settle and time.time() < deadline
            state["ok"] = 0
            move_window(self.address, x, y)
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
        if tool != "crop":
            self.crop_pending = None
        self.tool = tool
        if tool is None:
            self.remove_css_class("editing")
        else:
            self.add_css_class("editing")
        cursor = {None: None, "text": "text", "counter": "pointer"}.get(tool, "crosshair")
        self.area.set_cursor(Gdk.Cursor.new_from_name(cursor) if cursor else None)
        self.sync_toolbar()

    def push(self, op):
        self.ops.append(op)
        self.redo_stack.clear()
        self.sync_toolbar()
        self.area.queue_draw()

    def undo(self):
        if self.typing is not None:
            self.cancel_text()
        elif self.crop_pending is not None:
            self.crop_pending = None
        elif self.ops:
            op = self.ops.pop()
            if op["kind"] == "crop":
                self.set_pixbuf(op["pixbuf"], -op["dx"], -op["dy"])
            elif op["kind"] == "rotate":
                self.transform("rotate", 4 - op["arg"], record=False)
            elif op["kind"] == "flip":
                self.transform("flip", op["arg"], record=False)
            self.redo_stack.append(op)
        self.sync_toolbar()
        self.area.queue_draw()

    def redo(self):
        if self.redo_stack:
            op = self.redo_stack.pop()
            if op["kind"] == "crop":
                x0, y0, cw, ch = op["dx"], op["dy"], op["w"], op["h"]
                self.set_pixbuf(self.pixbuf.new_subpixbuf(x0, y0, cw, ch).copy(), x0, y0)
            elif op["kind"] in ("rotate", "flip"):
                self.transform(op["kind"], op["arg"], record=False)
            self.ops.append(op)
        self.sync_toolbar()
        self.area.queue_draw()

    # ---- crop ---------------------------------------------------------------
    def apply_crop(self):
        rect = self.crop_pending
        self.crop_pending = None
        if rect is None:
            return
        x0, y0, cw, ch = rect
        marker = {"kind": "crop", "pts": [], "color": self.color, "width": self.width, "text": "",
                  "pixbuf": self.pixbuf, "dx": x0, "dy": y0, "w": cw, "h": ch}
        self.set_pixbuf(self.pixbuf.new_subpixbuf(x0, y0, cw, ch).copy(), x0, y0)
        self.push(marker)
        self.set_tool(None)

    # ---- rotate / flip ---------------------------------------------------------
    def transform(self, kind, arg, record=True):
        """Rotate by `arg` quarter turns clockwise or flip ("h"/"v"); undoable as a marker op."""
        if self.typing is not None:
            self.commit_text()
        self.crop_pending = None
        iw, ih = self.iw, self.ih
        if kind == "rotate":
            arg %= 4
            if arg == 0:
                return
            rot = {1: GdkPixbuf.PixbufRotation.CLOCKWISE, 2: GdkPixbuf.PixbufRotation.UPSIDEDOWN,
                   3: GdkPixbuf.PixbufRotation.COUNTERCLOCKWISE}[arg]
            pixbuf = self.pixbuf.rotate_simple(rot)
            self.turns = (self.turns + arg) % 4
        else:
            pixbuf = self.pixbuf.flip(arg == "h")
            self.flipped[arg] = not self.flipped[arg]
        transform_ops(self.ops, kind, arg, iw, ih)
        transform_ops(self.redo_stack, kind, arg, iw, ih)
        if self.typing is not None:
            transform_ops([self.typing], kind, arg, iw, ih)
        self.swap_pixbuf(pixbuf)
        if record:
            self.ops.append({"kind": kind, "pts": [], "color": self.color, "width": self.width, "text": "", "arg": arg})
            self.redo_stack.clear()
        self.sync_toolbar()
        self.area.queue_draw()

    def swap_pixbuf(self, pixbuf):
        """Replace the image by a rotated or flipped one; the window keeps its centre."""
        c = self.client() if self.address else None
        old_w, old_h = round(self.iw * self.scale), round(self.ih * self.scale)
        self.pixbuf = pixbuf
        self.iw, self.ih = pixbuf.get_width(), pixbuf.get_height()
        self.scale = max(self.scale, self.min_scale())
        self.apply_scale()
        if c is not None:
            new_w, new_h = round(self.iw * self.scale), round(self.ih * self.scale)
            self.move_to(c["at"][0] + (old_w - new_w) // 2, c["at"][1] + (old_h - new_h) // 2)

    def reset_image(self):
        """Back to 100 %, opaque, unrotated, unflipped (each step undoable)."""
        if self.flipped["h"]:
            self.transform("flip", "h")
        if self.flipped["v"]:
            self.transform("flip", "v")
        if self.turns:
            self.transform("rotate", 4 - self.turns)
        self.set_thumbnail(False)
        self.set_scale(self.base_scale)
        self.opacity = default_opacity()
        self.set_opacity(self.opacity)
        self.show_osd("reset")

    def set_pixbuf(self, pixbuf, dx, dy):
        """Swap the image for a crop (or its undo): ops move by (-dx, -dy), the
        window shrinks or grows in place so the content stays where it was."""
        shift_ops(self.ops, -dx, -dy)
        shift_ops(self.redo_stack, -dx, -dy)
        self.pixbuf = pixbuf
        self.iw, self.ih = pixbuf.get_width(), pixbuf.get_height()
        self.scale = max(self.scale, self.min_scale())
        # the target is fixed before the resize: Hyprland re-centres on resize
        # and move_to() puts the window back until the new size has settled
        c = self.client() if self.address else None
        self.apply_scale()
        if c is not None:
            self.move_to(c["at"][0] + round(dx * self.scale), c["at"][1] + round(dy * self.scale))

    def start_text(self, p):
        self.typing = self.new_op("text", p)
        self.im.reset()
        self.im.focus_in()
        self.sync_toolbar()
        self.area.queue_draw()

    def commit_text(self):
        op, self.typing = self.typing, None
        if op is not None:
            self.im.focus_out()
            op.pop("_preedit", None)
            if op["text"].strip():
                self.push(op)
        self.area.queue_draw()

    def cancel_text(self):
        if self.typing is not None:
            self.im.focus_out()
        self.typing = None
        self.sync_toolbar()
        self.area.queue_draw()

    def on_im_commit(self, im, text):
        if self.typing is not None and text:
            self.typing["text"] += text
            self.area.queue_draw()

    def on_im_preedit(self, im):
        if self.typing is not None:
            self.typing["_preedit"] = im.get_preedit_string()[0]
            self.area.queue_draw()

    def paste_text(self):
        def done(clipboard, result):
            try:
                text = clipboard.read_text_finish(result)
            except GLib.Error:
                return
            if text and self.typing is not None:
                self.typing["text"] += " ".join(text.split())
                self.area.queue_draw()
        self.get_clipboard().read_text_async(None, done)

    def new_op(self, kind, p):
        return {"kind": kind, "pts": [p], "color": self.color, "width": self.width, "text": ""}

    def next_counter(self):
        return 1 + sum(1 for op in self.ops if op["kind"] == "counter")

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
        self.undo_btn.set_sensitive(bool(self.ops) or self.typing is not None or self.crop_pending is not None)
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
        if self.tool is None or self.tool in CLICK_TOOLS or self.thumb is not None:
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
        if op["kind"] in ("rect", "ellipse", "arrow", "blur", "crop"):
            (x0, y0), (x1, y1) = op["pts"]
            if math.hypot(x1 - x0, y1 - y0) < 3:
                self.area.queue_draw(); return
        if op["kind"] == "crop":
            self.crop_pending = crop_rect(self.pixbuf, op["pts"])
            self.sync_toolbar()
            self.area.queue_draw()
            return
        self.push(op)

    def on_click(self, gesture, n, x, y):
        shift = gesture.get_current_event_state() & Gdk.ModifierType.SHIFT_MASK
        if n == 2:
            self.mouse_action("shift_double" if shift else "double", x, y)
        elif self.thumb is not None:
            if n == 1:
                self.set_thumbnail(False)
        elif self.tool == "text":
            self.commit_text()
            self.start_text(self.to_img(x, y))
        elif self.tool == "counter" and n == 1:
            op = self.new_op("counter", self.to_img(x, y))
            op["n"] = self.next_counter()             # undo takes the number back with the badge
            self.push(op)

    def on_drop(self, target, value, x, y):
        files = value.get_files() if isinstance(value, Gdk.FileList) else [value]
        paths = [f.get_path() for f in files if isinstance(f, Gio.File) and f.get_path()]
        for p in paths:
            open_pin(self.get_application(), [p])        # a non-image gets the "Cannot open" toast
        return bool(paths)

    def on_menu(self, x=None, y=None):
        if x is None:
            x, y = self.area.get_width() / 2, self.area.get_height() / 2
        self.menu.set_pointing_to(Gdk.Rectangle(x=int(x), y=int(y), width=1, height=1))
        self.menu.popup()

    def mouse_action(self, button, x, y):
        """Run what the [mouse] section assigns to right / double / middle."""
        self.run_action(MOUSE.get(button, "none"), x, y)

    def run_action(self, action, x=None, y=None):
        """Dispatch a named action (see ACTIONS and MOUSE_ACTIONS); False if unknown."""
        if action == "copy":
            self.copy()
        elif action == "save":
            self.save()
        elif action == "save_as":
            self.save_as()
        elif action == "print":
            self.print_pin()
        elif action == "close":
            self.close()
        elif action == "destroy":
            self.destroy_pin()
        elif action == "menu":
            self.on_menu(x, y)
        elif action == "undo":
            self.undo()
        elif action == "redo":
            self.redo()
        elif action == "reset":
            self.reset_image()
        elif action == "reset_zoom":
            self.set_scale(self.base_scale); self.show_osd("100 %")
        elif action == "rotate_cw":
            self.transform("rotate", 1)
        elif action == "rotate_ccw":
            self.transform("rotate", 3)
        elif action in ("flip_h", "flip_v"):
            self.transform("flip", action[-1])
        elif action == "smooth":
            self.set_smooth(not self.smooth)
        elif action == "thumbnail":
            self.toggle_thumbnail()
        elif action == "reset_opacity":
            self.opacity = default_opacity()
            self.set_opacity(self.opacity)
            self.show_osd(f"{round(self.opacity * 100)} %")
        elif action == "click_through":
            self.set_click_through(not self.ghost)
        elif action == "width_down":
            self.set_width(self.width_idx - 1)
        elif action == "width_up":
            self.set_width(self.width_idx + 1)
        elif action == "cancel":
            if self.crop_pending is not None:
                self.crop_pending = None; self.sync_toolbar(); self.area.queue_draw()
            elif self.tool is not None:
                self.set_tool(None)
            else:
                self.close()
        elif action == "confirm":
            if self.crop_pending is not None:
                self.apply_crop()
            else:
                return False
        elif action.startswith("tool_"):
            tool = action[5:]
            if self.thumb is None:
                self.set_tool(None if tool == self.tool else tool)
        elif action.startswith("color_"):
            self.set_color(int(action[6:]) - 1)
        elif action == "none":
            pass
        else:
            return False
        return True

    def on_scroll(self, ctrl, dx, dy):
        # A wheel notch is one step. A touchpad sends many small smooth events
        # per flick; those accumulate and give one step per SMOOTH_STEP units,
        # so one flick no longer zooms 3-5x or fades the pin to nothing.
        steps, self.scroll_acc = scroll_steps(ctrl.get_unit() == Gdk.ScrollUnit.WHEEL, dy, self.scroll_acc)
        if steps == 0 or self.thumb is not None:
            return True
        ctrl_held = ctrl.get_current_event_state() & Gdk.ModifierType.CONTROL_MASK
        if ctrl_held:
            self.opacity = min(1.0, max(0.1, self.opacity - 0.1 * steps))
            self.set_opacity(self.opacity)
            self.show_osd(f"{round(self.opacity * 100)} %")
        else:
            self.zoom(self.scale / ZOOM_STEP ** steps)
        return True

    def zoom(self, new_scale):
        """Zoom, keeping the image point under the pointer where it is (zoom_at_pointer = 1):
        the window moves by the difference, which on Hyprland means a move request
        held through the resize like a crop's."""
        new_scale = max(self.min_scale(), min(new_scale, MAX_SCALE))
        c = self.client() if self.zoom_at_pointer and self.pointer is not None and self.address else None
        if c is not None:
            img_pt = self.to_img(*self.pointer)
            dx, dy = zoom_shift(self.pointer, img_pt, new_scale)
            self.pointer = (img_pt[0] * new_scale, img_pt[1] * new_scale)
        self.set_scale(new_scale)
        if c is not None:
            self.move_to(c["at"][0] + dx, c["at"][1] + dy)
        self.show_osd(f"{round(self.scale / self.base_scale * 100)} %")

    def set_smooth(self, on):
        self.smooth = on
        self.smooth_action.set_state(GLib.Variant.new_boolean(on))
        self.area.queue_draw()

    def on_key(self, ctrl, keyval, keycode, state):
        mods = int(state) & MOD_MASK
        action = KEYMAP.get((Gdk.keyval_to_lower(keyval), mods))
        # text entry: the input method first (dead keys, Compose, CJK), then
        # the editing keys; everything else is swallowed while typing
        if self.typing is not None:
            event = ctrl.get_current_event()
            if event is not None and self.im.filter_keypress(event):
                return True
            ctrl_held = mods & int(Gdk.ModifierType.CONTROL_MASK)
            if ctrl_held and keyval in (Gdk.KEY_v, Gdk.KEY_V):
                self.paste_text(); return True
            if action in ("cancel", "close"):
                self.cancel_text(); return True
            if action == "confirm":
                self.commit_text(); return True
            if keyval == Gdk.KEY_BackSpace:
                self.typing["text"] = self.typing["text"][:-1]; self.area.queue_draw(); return True
            if not ctrl_held:                             # Ctrl+C, Ctrl+S, Ctrl+Z ... fall through
                u = Gdk.keyval_to_unicode(keyval)         # fallback when no IM consumed the key
                if u and chr(u).isprintable():
                    self.typing["text"] += chr(u); self.area.queue_draw()
                return True
        if action is None:
            return False
        return self.run_action(action)

    # ---- actions ----------------------------------------------------------
    def build_menu(self):
        m = Gio.Menu()
        m.append(f"Copy image & close\t{key_label('copy')}", "win.copy")
        m.append(f"Save to screenshots & close\t{key_label('save')}", "win.save")
        m.append(f"Save as…\t{key_label('save_as')}", "win.save_as")
        m.append(f"Print…\t{key_label('print')}", "win.print")
        edit = Gio.Menu()
        edit.append(f"Undo\t{key_label('undo')}", "win.undo")
        edit.append(f"Redo\t{key_label('redo')}", "win.redo")
        m.append_section(None, edit)
        tr = Gio.Menu()
        tr.append(f"Rotate right\t{key_label('rotate_cw')}", "win.rotate_cw")
        tr.append(f"Rotate left\t{key_label('rotate_ccw')}", "win.rotate_ccw")
        tr.append(f"Flip horizontally\t{key_label('flip_h')}", "win.flip_h")
        tr.append(f"Flip vertically\t{key_label('flip_v')}", "win.flip_v")
        m.append_section(None, tr)
        tail = Gio.Menu()
        tail.append(f"Thumbnail\t{key_label('thumbnail')}", "win.thumbnail")
        tail.append("Smooth scaling", "win.smooth")
        tail.append(f"Reset image\t{key_label('reset')}", "win.reset")
        tail.append(f"Click-through\t{key_label('click_through')}", "win.click_through")
        tail.append(f"Close (snip-pin.sh reopen brings it back)\t{key_label('cancel')}", "win.close")
        tail.append(f"Destroy\t{key_label('destroy')}", "win.destroy")
        m.append_section(None, tail)
        return m

    def export(self):
        """(path, temporary): the original, or the annotations baked into a temp file.

        The caller deletes a temporary file when done. Rendering next to the
        original would litter the cache (and the history) or the user's own
        folder for `pin FILE`.
        """
        ops = [op for op in self.ops if op["kind"] not in MARKERS]
        cropped = any(op["kind"] in MARKERS for op in self.ops)        # the pixels differ from the file
        if self.typing is not None and self.typing["text"].strip():
            ops.append(self.typing)
        # the clipboard and the save folder always get PNG: a JPEG opened via
        # "pin FILE" must be re-encoded even without annotations
        if not ops and not cropped and self.path.lower().endswith(".png"):
            return self.path, False
        fd, tmp = tempfile.mkstemp(prefix="snip-pin-", suffix=".png", dir=RUNTIME_DIR)
        os.close(fd)
        render_png(self.pixbuf, ops, tmp)
        return tmp, True

    def copy(self, *a):
        path, temporary = self.export()
        try:
            clipboard_owner(self.get_application()).offer(path, copy_as_file())
        except (GLib.Error, OSError) as e:
            # no display clipboard (odd session) or an unreadable export: fall back to wl-copy, PNG only
            print(f"pin-view: clipboard: {e}; using wl-copy", file=sys.stderr)
            with open(path, "rb") as f:
                subprocess.run(["wl-copy", "--type", "image/png"], stdin=f)
        finally:
            if temporary:
                os.unlink(path)
        notify("Copied to clipboard")
        play_sound()
        self.close()

    def export_ops(self):
        """The drawing ops to bake, the text being typed included."""
        ops = [op for op in self.ops if op["kind"] not in MARKERS]
        if self.typing is not None and self.typing["text"].strip():
            ops.append(self.typing)
        return ops

    def save(self, *a):
        """Quick save: the screenshot folder, the file-name pattern, the configured format; then close."""
        folder = screenshot_folder()
        fmt = save_format()
        try:
            os.makedirs(folder, exist_ok=True)
            dest = unique_path(folder, datetime.datetime.now().strftime(file_pattern()), EXTENSIONS[fmt])
            write_image(self.pixbuf, self.export_ops(), dest, fmt, save_quality())
        except (OSError, GLib.Error) as e:
            # unwritable folder, full disk, missing encoder: keep the pin so nothing is lost
            msg = getattr(e, "strerror", None) or getattr(e, "message", None) or str(e)
            notify(f"Cannot save to {folder}: {msg}", 3000)
            print(f"pin-view: cannot save to {folder}: {e}", file=sys.stderr)
            return
        notify(f"Saved {dest}")
        play_sound()
        self.close()

    def print_pin(self, *a, export_to=None):
        """GTK's print dialog; the image with its annotations at screen size (96 dpi),
        shrunk to fit the page if needed, centred. The pin stays. export_to: a PDF path
        instead of the dialog."""
        pb = render_pixbuf(self.pixbuf, self.export_ops())
        op = Gtk.PrintOperation()
        op.set_n_pages(1)
        op.set_job_name(os.path.basename(self.path))
        op.set_embed_page_setup(True)

        def draw_page(operation, context, page_nr):
            cr = context.get_cairo_context()
            pw, ph = context.get_width(), context.get_height()          # in points (1/72 inch)
            f = min(pw / pb.get_width(), ph / pb.get_height(), 72 / 96)
            w, h = pb.get_width() * f, pb.get_height() * f
            cr.translate((pw - w) / 2, (ph - h) / 2)
            cr.scale(f, f)
            Gdk.cairo_set_source_pixbuf(cr, pb, 0, 0)
            cr.get_source().set_filter(cairo.FILTER_BEST)
            cr.paint()
        op.connect("draw-page", draw_page)
        try:
            if export_to:
                op.set_export_filename(export_to)
                result = op.run(Gtk.PrintOperationAction.EXPORT, None)
            else:
                result = op.run(Gtk.PrintOperationAction.PRINT_DIALOG, self)
        except GLib.Error as e:
            notify(f"Cannot print: {e.message}", 3000)
            return
        if result == Gtk.PrintOperationResult.ERROR:
            notify("Printing failed", 3000)

    def save_as(self, *a):
        """A file dialog, preset with the folder, the pattern and the last used extension; the pin stays."""
        dialog = Gtk.FileDialog()
        folder = screenshot_folder()
        if os.path.isdir(folder):
            dialog.set_initial_folder(Gio.File.new_for_path(folder))
        dialog.set_initial_name(datetime.datetime.now().strftime(file_pattern()) + last_extension())

        def done(d, result):
            try:
                f = d.save_finish(result)
            except GLib.Error:
                return                                       # cancelled
            dest = f.get_path()
            if not dest:
                return
            ext = os.path.splitext(dest)[1].lower().lstrip(".")
            fmt = FORMATS.get(ext)
            if fmt is None:
                fmt, dest = "png", dest + ".png"
            try:
                write_image(self.pixbuf, self.export_ops(), dest, fmt, save_quality())
            except (OSError, GLib.Error) as e:
                msg = getattr(e, "strerror", None) or getattr(e, "message", None) or str(e)
                notify(f"Cannot save {os.path.basename(dest)}: {msg}", 3000)
                return
            remember_extension(EXTENSIONS[fmt])
            notify(f"Saved {dest}")
            play_sound()
        dialog.save(self, None, done)

def pins(app):
    return [w for w in app.get_windows() if isinstance(w, Pin)]


def toggle_pins(app):
    """Hide every pin, or show them all again where they were (Hyprland forgets
    the position of an unmapped window, so each pin re-places itself)."""
    wins = pins(app)
    if any(w.get_visible() for w in wins):
        for w in wins:
            if w.get_visible():
                c = w.client()
                if c is not None:
                    w.pos = (c["at"][0] + BORDER, c["at"][1] + BORDER)
                w.hidden = True
                w.set_visible(False)
    else:
        for w in wins:
            w.hidden = False
            w.address = None               # a remapped surface is a new Hyprland client
            w.present()
            w.place()


def close_all(app):
    """Close every pin; with more than one, ask first (confirm_close_all = 0 skips the question)."""
    wins = pins(app)
    if not wins:
        return
    if len(wins) == 1 or CFG.get("confirm_close_all", "1").strip() in ("0", "no", "false"):
        for w in wins:
            w.close()
        return
    dialog = Gtk.AlertDialog(message=f"Close {len(wins)} pins?", buttons=["Cancel", "Close all"],
                             detail="Annotations that were not copied or saved are lost.",
                             default_button=1, cancel_button=0)

    def done(d, result):
        try:
            if d.choose_finish(result) == 1:
                for w in pins(app):
                    w.close()
        except GLib.Error:
            pass
    anchor = next((w for w in wins if w.get_visible()), wins[0])
    dialog.choose(anchor, None, done)


def click_through_all(app):
    """All pins solid if any is click-through, else all click-through."""
    wins = pins(app)
    on = not any(w.ghost for w in wins)
    for w in wins:
        w.set_click_through(on)


def reopen_pin(app):
    """Bring the most recently closed pin back as it was; False if there is none."""
    for j, png in closed_entries():
        try:
            with open(j) as f:
                state = json.load(f)
        except (OSError, ValueError):
            state = None
        try:
            os.unlink(j)
        except OSError:
            pass
        if not state or not os.path.exists(png):
            continue
        pos = tuple(state["pos"]) if state.get("pos") else None
        try:
            win = Pin(app, png, pos, out_scale=1.0 / float(state.get("base_scale") or 1.0), state=state)
        except (GLib.Error, ValueError, ZeroDivisionError) as e:
            print(f"pin-view: cannot reopen {png}: {e}", file=sys.stderr)
            continue
        win.path = state.get("path", png)          # the original file, for exports without annotations
        try:
            os.unlink(png)                          # loaded into the pixbuf; the entry is used up
        except OSError:
            pass
        win.present()
        win.place()
        return True
    notify("No closed pin to reopen")
    return False


def run_command(app, cmd):
    if cmd == "--reopen":
        if not reopen_pin(app) and not app.get_windows():
            app.quit()
    elif cmd == "--toggle":
        toggle_pins(app)
    elif cmd == "--close-all":
        close_all(app)
    elif cmd == "--click-through":
        click_through_all(app)


def open_pin(app, args):
    """Open a pin for [path, x, y]; a bad file tells the user instead of crashing."""
    path = args[0]
    try:
        pos = (int(args[1]), int(args[2])) if len(args) >= 3 else None
    except ValueError:
        pos = None
    try:
        win = Pin(app, path, pos, output_scale(hypr_json("j/monitors") if pos else None, pos))
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
            if args and args[0] in COMMANDS:
                run_command(app, args[0])
            elif args:
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
        if sys.argv[1] in COMMANDS:
            run_command(app, sys.argv[1])
        else:
            open_pin(app, [os.path.abspath(sys.argv[1])] + sys.argv[2:])
    app.connect("activate", activate)
    GLib.set_prgname(APP_ID)   # -> Wayland app_id / Hyprland class "snip-pin"
    # Docks look the icon up by app_id via snip-pin.desktop (see README).
    Gtk.Window.set_default_icon_name(APP_ID)
    app.run([sys.argv[0]])

if __name__ == "__main__":
    main()
