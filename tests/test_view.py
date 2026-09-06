import os
import struct
import zlib


def test_norm_rect(view):
    assert view.norm_rect([(10, 20), (5, 30)]) == (5, 20, 10, 30)
    assert view.norm_rect([(0, 0), (3, 3), (1, 1)]) == (0, 0, 1, 1)   # first and last point


def test_hex_to_rgb(view):
    assert view.hex_to_rgb("#ffffff") == (1.0, 1.0, 1.0)
    assert view.hex_to_rgb("#000000") == (0.0, 0.0, 0.0)
    r, g, b = view.hex_to_rgb("#ff8000")
    assert (r, b) == (1.0, 0.0) and abs(g - 128 / 255) < 1e-9


def test_unique_path(tmp_path, view):
    d = str(tmp_path)
    assert view.unique_path(d, "pin", ".png") == os.path.join(d, "pin.png")
    open(os.path.join(d, "pin.png"), "w").close()
    assert view.unique_path(d, "pin", ".png") == os.path.join(d, "pin_2.png")
    open(os.path.join(d, "pin_2.png"), "w").close()
    assert view.unique_path(d, "pin", ".png") == os.path.join(d, "pin_3.png")


def test_screenshot_folder_default(tmp_path, view, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("SNIP_PIN_SAVE_DIR", raising=False)
    # GLib caches the special dirs per process; with no user-dirs.dirs it answers ~/Pictures (or None)
    got = view.screenshot_folder()
    assert got.endswith("Pictures") or got.endswith("Bilder")


def test_screenshot_folder_env_wins(tmp_path, view, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("SNIP_PIN_SAVE_DIR", "~/Shots/$USER")
    monkeypatch.setenv("USER", "me")
    assert view.screenshot_folder() == str(tmp_path / "Shots" / "me")


def test_screenshot_folder_ml4w(tmp_path, view, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("SNIP_PIN_SAVE_DIR", raising=False)
    cfg = tmp_path / ".config/ml4w/settings"
    cfg.mkdir(parents=True)
    (cfg / "screenshot-folder").write_text("~/Shots\n")
    assert view.screenshot_folder() == str(tmp_path / "Shots")
    (cfg / "screenshot-folder").write_text("\n")                      # empty: fall back
    assert view.screenshot_folder() != str(tmp_path / "Shots")


def test_notify_without_libnotify_is_a_noop(view, monkeypatch):
    monkeypatch.setattr(view, "NOTIFY_SEND", None)
    view.notify("hello")                                               # must not raise


def png_size(path):
    with open(path, "rb") as f:
        head = f.read(24)
    assert head[:8] == b"\x89PNG\r\n\x1a\n"
    return struct.unpack(">II", head[16:24])


def make_png(path, w, h, rgb=(255, 255, 255)):
    raw = b"".join(b"\0" + bytes(rgb) * w for _ in range(h))

    def chunk(kind, data):
        c = kind + data
        return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)
    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def test_render_png_bakes_ops_at_native_size(tmp_path, view):
    from gi.repository import GdkPixbuf
    src = tmp_path / "src.png"
    make_png(str(src), 120, 80)
    pb = GdkPixbuf.Pixbuf.new_from_file(str(src))
    red = (1.0, 0.0, 0.0)
    ops = [{"kind": "rect", "pts": [(10, 10), (60, 40)], "color": red, "width": 4, "text": ""},
           {"kind": "arrow", "pts": [(5, 70), (100, 20)], "color": red, "width": 2, "text": ""},
           {"kind": "pen", "pts": [(10, 60), (30, 65), (50, 60)], "color": red, "width": 2, "text": ""},
           {"kind": "marker", "pts": [(10, 20), (100, 20)], "color": red, "width": 4, "text": ""},
           {"kind": "text", "pts": [(10, 50)], "color": red, "width": 4, "text": "hi"},
           {"kind": "blur", "pts": [(70, 40), (110, 75)], "color": red, "width": 4, "text": ""},
           {"kind": "ellipse", "pts": [(60, 10), (110, 40)], "color": red, "width": 2, "text": ""},
           {"kind": "counter", "pts": [(100, 60)], "color": red, "width": 4, "text": "", "n": 7}]
    out = tmp_path / "out.png"
    assert view.render_png(pb, ops, str(out)) == str(out)
    assert png_size(str(out)) == (120, 80)
    baked = GdkPixbuf.Pixbuf.new_from_file(str(out))
    px = baked.get_pixels()
    stride, n = baked.get_rowstride(), baked.get_n_channels()

    def at(x, y):
        o = y * stride + x * n
        return tuple(px[o:o + 3])
    assert at(10, 25) == (255, 0, 0)          # on the rectangle's left edge
    assert at(90, 5) == (255, 255, 255)       # untouched corner stays white
    assert at(85, 10) == (255, 0, 0)          # top of the ellipse (centre 85,25, ry 15)
    assert at(85, 36) == (255, 255, 255)      # inside the ellipse, clear of the marker: an outline
    assert at(89, 60) == (255, 0, 0)          # inside the badge (radius 14), clear of the digit


def test_mosaic_pixbuf_clips_to_the_image(tmp_path, view):
    from gi.repository import GdkPixbuf
    src = tmp_path / "src.png"
    make_png(str(src), 50, 50)
    pb = GdkPixbuf.Pixbuf.new_from_file(str(src))
    op = {"kind": "blur", "pts": [(-10, -10), (30, 20)], "width": 4}
    x0, y0, w, h, big = view.mosaic_pixbuf(pb, op)
    assert (x0, y0, w, h) == (0, 0, 30, 20)
    assert (big.get_width(), big.get_height()) == (30, 20)
    assert view.mosaic_pixbuf(pb, {"kind": "blur", "pts": [(60, 60), (70, 70)], "width": 4}) is None


def test_scroll_steps_wheel_is_one_per_notch(view):
    assert view.scroll_steps(True, -1.0, 0.0) == (-1, 0.0)
    assert view.scroll_steps(True, 1.0, 12.0) == (1, 12.0)        # the smooth remainder is untouched


def test_scroll_steps_smooth_accumulates(view):
    acc, steps = 0.0, []
    for _ in range(10):                                             # a flick: ten events of 7 units
        st, acc = view.scroll_steps(False, 7.0, acc)
        steps.append(st)
    assert sum(steps) == 2 and max(steps) <= 1 and 0 <= acc < view.SMOOTH_STEP
    st, acc = view.scroll_steps(False, -100.0, 0.0)                 # a big negative delta: whole steps only
    assert st == -3 and abs(acc - (-10.0)) < 1e-9


class FakeApp:
    """Enough of Gtk.Application for serve(): windows, signals, an attribute slot."""
    def __init__(self):
        self.windows = [object()]
        self.handlers = {}

    def get_windows(self):
        return self.windows

    def connect(self, name, cb):
        self.handlers[name] = cb


def run_loop_until(pred, timeout=3.0):
    import time as _t
    from gi.repository import GLib
    ctx = GLib.MainContext.default()
    end = _t.monotonic() + timeout
    while _t.monotonic() < end:
        if pred():
            return True
        ctx.iteration(False)
        _t.sleep(0.005)
    return False


def test_serve_and_hand_over(view, monkeypatch, tmp_path):
    import socket
    import threading
    # a private socket name so the test never talks to a real viewer
    monkeypatch.setattr(view, "SOCKET", "\0snip-pin-test-%d" % os.getpid() if os.name != "nt" else str(tmp_path / "s"))
    got = []
    monkeypatch.setattr(view, "open_pin", lambda app, args: got.append(args) or True)
    app = FakeApp()
    assert view.serve(app) is True
    assert view.serve(FakeApp()) is False                      # the name is taken by a live server
    result = []
    t = threading.Thread(target=lambda: result.append(view.hand_over(["/x/a.png", "10", "20"])))
    t.start()
    assert run_loop_until(lambda: got)
    t.join(2)
    assert result == [True] and got == [["/x/a.png", "10", "20"]]
    # garbage and a stalled client are acknowledged (so the client does not start GTK) and ignored
    c = socket.socket(socket.AF_UNIX)
    c.connect(view.SOCKET)
    c.sendall(b"{not json\n")
    c.setblocking(False)                                        # poll without ever blocking the loop
    ack = []

    def acked():
        try:
            ack.append(c.recv(1))
        except BlockingIOError:
            return False
        return True
    assert run_loop_until(acked, 2) and ack == [b"1"]
    assert got == [["/x/a.png", "10", "20"]]
    c.close()
    stalled = socket.socket(socket.AF_UNIX)
    stalled.connect(view.SOCKET)
    assert run_loop_until(lambda: False, 0.4) is False          # loop turns while the client says nothing
    assert got == [["/x/a.png", "10", "20"]]
    stalled.close()
    # the last window closing stops the server: a hand-over is refused at once
    app.windows.clear()
    app.handlers["window-removed"](app, None)
    assert view.hand_over(["/x/b.png"]) is False


def test_read_request_validates(view):
    import socket
    a, b = socket.socketpair()
    b.sendall(b'["/p.png", "1", "2"]\n')
    assert view.read_request(a) == ["/p.png", "1", "2"]
    b.sendall(b'{"k": 1}\n')
    assert view.read_request(a) is None
    b.sendall(b'[1, 2]\n')
    assert view.read_request(a) is None
    b.sendall(b'\n')
    assert view.read_request(a) is None
    assert view.read_request(a, timeout=0.05) is None          # nothing pending: bounded wait
    a.close(); b.close()


MONS = [{"name": "A", "x": 0, "y": 0, "width": 3440, "height": 1440, "scale": 1, "transform": 0},
        {"name": "B", "x": 3440, "y": 0, "width": 3840, "height": 2160, "scale": 2, "transform": 0},
        {"name": "C", "x": -1080, "y": 0, "width": 1920, "height": 1080, "scale": 1, "transform": 1}]


def test_monitor_at(view):
    assert view.monitor_at(MONS, 100, 100)["name"] == "A"
    assert view.monitor_at(MONS, 3500, 100)["name"] == "B"          # 1920x1080 logical at scale 2
    assert view.monitor_at(MONS, 5400, 1000)["name"] == "A"         # outside B's logical area: first
    assert view.monitor_at(MONS, -500, 1500)["name"] == "C"         # rotated: 1080 wide, 1920 tall
    assert view.monitor_at([], 0, 0) is None and view.monitor_at(None, 0, 0) is None


def test_clamp_to_monitor(view):
    assert view.clamp_to_monitor(MONS[0], 100, 100, 400, 300) == (100, 100)
    assert view.clamp_to_monitor(MONS[0], 3300, 1300, 400, 300) == (3040, 1140)
    assert view.clamp_to_monitor(MONS[0], -50, -50, 400, 300) == (0, 0)
    assert view.clamp_to_monitor(MONS[1], 5000, 900, 400, 300) == (4960, 780)   # logical 1920x1080
    assert view.clamp_to_monitor(MONS[0], 10, 10, 5000, 300) == (0, 10)          # wider than the screen
    assert view.clamp_to_monitor(None, 7, 8, 1, 1) == (7, 8)


def test_move_window_tries_lua_then_classic(view, monkeypatch):
    calls = []

    def fake_hypr(cmd):
        calls.append(cmd)
        return "ok" if cmd.startswith("dispatch movewindowpixel") else "error: unknown"
    monkeypatch.setattr(view, "hypr", fake_hypr)
    monkeypatch.setattr(view, "_move_syntax", None)
    assert view.move_window("0x1", 10, 20) is True
    assert [c.split(" ")[1][:4] for c in calls] == ["hl.d", "move"]
    calls.clear()
    assert view.move_window("0x1", 30, 40) is True                # remembered: classic only
    assert len(calls) == 1 and "exact 30 40,address:0x1" in calls[0]


def test_hypr_json_handles_no_compositor(view, monkeypatch):
    monkeypatch.setattr(view, "hypr", lambda cmd: "")
    assert view.hypr_json("j/monitors") is None
    monkeypatch.setattr(view, "hypr", lambda cmd: "not json")
    assert view.hypr_json("j/monitors") is None


def test_output_scale(view):
    assert view.output_scale(MONS, (100, 100)) == 1.0
    assert view.output_scale(MONS, (3500, 100)) == 2.0
    assert view.output_scale(MONS, None) == 1.0
    assert view.output_scale(None, (5, 5)) == 1.0
    assert view.output_scale([{"x": 0, "y": 0, "width": 100, "height": 100, "scale": 0}], (1, 1)) == 1.0


def test_degenerate_ellipse_and_counter_do_not_raise(tmp_path, view):
    from gi.repository import GdkPixbuf
    src = tmp_path / "s.png"
    make_png(str(src), 40, 40)
    pb = GdkPixbuf.Pixbuf.new_from_file(str(src))
    ops = [{"kind": "ellipse", "pts": [(5, 5), (5, 30)], "color": (0, 0, 1), "width": 2, "text": ""},
           {"kind": "counter", "pts": [(-5, 50)], "color": (0, 0, 0), "width": 7, "text": ""}]   # no "n": defaults to 1
    view.render_png(pb, ops, str(tmp_path / "o.png"))


def test_shift_ops_moves_points_and_drops_mosaic_cache(view):
    ops = [{"kind": "rect", "pts": [(10, 10), (20, 30)], "_mosaic": ("k", None)},
           {"kind": "crop", "pts": [], "dx": 5, "dy": 5}]
    view.shift_ops(ops, -3, 4)
    assert ops[0]["pts"] == [(7, 14), (17, 34)] and "_mosaic" not in ops[0]
    assert ops[1] == {"kind": "crop", "pts": [], "dx": 5, "dy": 5}


def test_crop_rect_clips_and_rejects_tiny(tmp_path, view):
    from gi.repository import GdkPixbuf
    src = tmp_path / "s.png"
    make_png(str(src), 100, 50)
    pb = GdkPixbuf.Pixbuf.new_from_file(str(src))
    assert view.crop_rect(pb, [(-10, -10), (60.4, 30.6)]) == (0, 0, 60, 31)
    assert view.crop_rect(pb, [(90, 40), (200, 200)]) == (90, 40, 10, 10)
    assert view.crop_rect(pb, [(10, 10), (12, 40)]) is None
    assert view.crop_rect(pb, [(30, 30), (10, 10)]) == (10, 10, 20, 20)     # any drag direction


def test_render_skips_crop_markers(tmp_path, view):
    from gi.repository import GdkPixbuf
    src = tmp_path / "s.png"
    make_png(str(src), 40, 40)
    pb = GdkPixbuf.Pixbuf.new_from_file(str(src))
    marker = {"kind": "crop", "pts": [], "color": (0, 0, 0), "width": 2, "text": ""}
    view.render_png(pb, [marker], str(tmp_path / "o.png"))
    assert png_size(str(tmp_path / "o.png")) == (40, 40)


def test_parse_config_sections_and_comments(view):
    text = ("# c\nkeep_days = 3 # x\nborder=#fff\nsave_dir = #123456  # orange\nempty = # nothing here\n"
            "[keys]  # bindings\ncopy = ctrl+c ctrl+shift+c\n[Mouse]\nright = menu\nnoequals\n")
    cfg = view.parse_config(text)
    assert cfg[""] == {"keep_days": "3", "border": "#fff", "save_dir": "#123456", "empty": ""}
    assert cfg["keys"] == {"copy": "ctrl+c ctrl+shift+c"}
    assert cfg["mouse"] == {"right": "menu"}


def test_config_env_overrides_file_and_reload_tracks_changes(tmp_path, view, monkeypatch):
    path = tmp_path / "config"
    path.write_text("border = #123456\n")
    cfg = view.Config(str(path))
    assert cfg.get("border") == "#123456"
    monkeypatch.setenv("SNIP_PIN_BORDER", "#abcdef")
    assert cfg.get("border") == "#abcdef"
    monkeypatch.delenv("SNIP_PIN_BORDER")
    assert cfg.reload() is False
    path.write_text("border = #654321\n[keys]\ncopy = ctrl+shift+c\n")
    os.utime(path, (1, 1))                                  # any change of mtime or size counts
    assert cfg.reload() is True
    assert cfg.get("border") == "#654321" and cfg.section("keys") == {"copy": "ctrl+shift+c"}
    path.unlink()
    assert cfg.reload() is True and cfg.get("border", "dflt") == "dflt"


def test_parse_binding(view):
    from gi.repository import Gdk
    ctrl, shift = int(Gdk.ModifierType.CONTROL_MASK), int(Gdk.ModifierType.SHIFT_MASK)
    assert view.parse_binding("ctrl+c") == (Gdk.KEY_c, ctrl)
    assert view.parse_binding("Ctrl+Shift+Z") == (Gdk.KEY_z, ctrl | shift)
    assert view.parse_binding("Escape") == (Gdk.KEY_Escape, 0)
    assert view.parse_binding("esc") == (Gdk.KEY_Escape, 0)
    assert view.parse_binding("[") == (Gdk.KEY_bracketleft, 0)
    assert view.parse_binding("ctrl++") == (Gdk.KEY_plus, ctrl)
    assert view.parse_binding("f10") == (Gdk.KEY_F10, 0)
    assert view.parse_binding("kp_enter") == (Gdk.KEY_KP_Enter, 0)
    assert view.parse_binding("hyper+c") is None
    assert view.parse_binding("ctrl+nosuchkey") is None
    assert view.parse_binding("") is None


def test_default_keymap_has_no_conflicts_and_overrides_apply(view, capsys):
    from gi.repository import Gdk
    km = view.build_keymap({})
    assert km[(Gdk.KEY_c, int(Gdk.ModifierType.CONTROL_MASK))] == "copy"
    assert km[(Gdk.KEY_r, 0)] == "tool_rect" and km[(Gdk.KEY_3, 0)] == "color_3"
    assert km[(Gdk.KEY_Escape, 0)] == "cancel"
    # every default binding is parseable and maps to exactly one action
    n = sum(len(v.split()) for v in view.ACTIONS.values())
    assert len(km) == n
    km = view.build_keymap({"copy": "ctrl+shift+c", "redo": "", "bogus": "x", "save": "ctrl+nosuch"})
    assert km[(Gdk.KEY_c, int(Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.SHIFT_MASK))] == "copy"
    assert (Gdk.KEY_c, int(Gdk.ModifierType.CONTROL_MASK)) not in km
    assert "redo" not in km.values() and "save" not in km.values()
    err = capsys.readouterr().err
    assert "bogus" in err and "nosuch" in err


def test_mouse_map_defaults_and_overrides(view, capsys):
    assert view.build_mouse({}) == {"right": "copy", "double": "copy", "shift_double": "thumbnail", "middle": "menu"}
    m = view.build_mouse({"right": "menu", "double": "close", "middle": "bogus", "left": "copy"})
    assert m == {"right": "menu", "double": "close", "shift_double": "thumbnail", "middle": "menu"}
    assert "bogus" in capsys.readouterr().err


def test_key_label(view, monkeypatch):
    monkeypatch.setattr(view, "KEYMAP", view.build_keymap({}))
    assert view.key_label("copy") == "Ctrl+C"
    assert view.key_label("redo") in ("Ctrl+Shift+Z", "Ctrl+Y")
    assert view.key_label("cancel") == "Esc"
    assert view.key_label("width_down") == "["
    assert view.key_label("close") == ""


def test_default_tool_setting(view, monkeypatch, capsys):
    monkeypatch.setenv("SNIP_PIN_DEFAULT_TOOL", "arrow")
    assert view.default_tool() == "arrow"
    monkeypatch.setenv("SNIP_PIN_DEFAULT_TOOL", "None")
    assert view.default_tool() is None
    monkeypatch.setenv("SNIP_PIN_DEFAULT_TOOL", "lasso")
    assert view.default_tool() is None and view.default_tool() is None
    assert capsys.readouterr().err.count("unknown tool") == 1               # warned once, not per pin


def test_commands_without_a_viewer_exit_quietly(view):
    import subprocess
    from conftest import ROOT
    env = dict(os.environ, SNIP_PIN_SOCKET="/nonexistent-dir-for-test/sock")   # never the user's live viewer
    r = subprocess.run([os.path.join(ROOT, "pin-view.py"), "--toggle"], env=env,
                       capture_output=True, text=True, timeout=10)
    assert r.returncode == 0 and r.stdout == "" and r.stderr == ""
    assert "--toggle" in view.COMMANDS and "--close-all" in view.COMMANDS and "--click-through" in view.COMMANDS
    assert view.ACTIONS["click_through"] == "ctrl+t"


def test_transform_ops_rotates_and_flips_points(view):
    ops = [{"kind": "arrow", "pts": [(10, 20), (30, 40)], "_mosaic": "x"},
           {"kind": "crop", "pts": []}]
    view.transform_ops(ops, "rotate", 1, 100, 50)            # 90 deg clockwise: (x, y) -> (ih - y, x)
    assert ops[0]["pts"] == [(30, 10), (10, 30)] and "_mosaic" not in ops[0]
    view.transform_ops(ops, "rotate", 3, 50, 100)            # and back
    assert ops[0]["pts"] == [(10, 20), (30, 40)]
    view.transform_ops(ops, "flip", "h", 100, 50)
    assert ops[0]["pts"] == [(90, 20), (70, 40)]
    view.transform_ops(ops, "flip", "v", 100, 50)
    assert ops[0]["pts"] == [(90, 30), (70, 10)]
    view.transform_ops(ops, "rotate", 2, 100, 50)
    assert ops[0]["pts"] == [(10, 20), (30, 40)]
    assert ops[1]["pts"] == []                                # markers untouched


def test_render_skips_transform_markers(tmp_path, view):
    from gi.repository import GdkPixbuf
    src = tmp_path / "s.png"
    make_png(str(src), 40, 20)
    pb = GdkPixbuf.Pixbuf.new_from_file(str(src))
    ops = [{"kind": "rotate", "pts": [], "color": (0, 0, 0), "width": 2, "text": "", "arg": 1},
           {"kind": "flip", "pts": [], "color": (0, 0, 0), "width": 2, "text": "", "arg": "h"}]
    view.render_png(pb.rotate_simple(GdkPixbuf.PixbufRotation.CLOCKWISE), ops, str(tmp_path / "o.png"))
    assert png_size(str(tmp_path / "o.png")) == (20, 40)
    assert view.ACTIONS["reset"] == "ctrl+0" and "reset" in view.MOUSE_ACTIONS


def test_zoom_shift_keeps_the_image_point_under_the_pointer(view):
    # pointer at widget (100, 50) over image point (200, 100) at scale 0.5; zooming to 1.0
    # draws that point at (200, 100), so the window must move by (-100, -50)
    assert view.zoom_shift((100, 50), (200, 100), 1.0) == (-100, -50)
    assert view.zoom_shift((100, 50), (200, 100), 0.5) == (0, 0)


def test_pick_filter(view):
    import cairo
    assert view.pick_filter(True, 3.0, 1.0) == cairo.FILTER_GOOD
    assert view.pick_filter(False, 1.0, 1.0) == cairo.FILTER_GOOD        # not zoomed in: smooth downscale
    assert view.pick_filter(False, 0.5, 1.0) == cairo.FILTER_GOOD
    assert view.pick_filter(False, 2.0, 1.0) == cairo.FILTER_NEAREST


def test_ops_json_round_trip_drops_markers_and_caches(view):
    ops = [{"kind": "arrow", "pts": [(1.5, 2), (3, 4)], "color": (1, 0, 0), "width": 4, "text": "", "_mosaic": "x"},
           {"kind": "crop", "pts": [], "color": (0, 0, 0), "width": 2, "text": "", "pixbuf": object()},
           {"kind": "counter", "pts": [(5, 5)], "color": (0, 1, 0), "width": 4, "text": "", "n": 3}]
    data = view.ops_to_json(ops)
    assert len(data) == 2 and "_mosaic" not in data[0]
    back = view.ops_from_json(json_round_trip(data))
    assert back[0]["pts"] == [(1.5, 2.0), (3.0, 4.0)] and back[0]["color"] == (1, 0, 0)
    assert back[1]["n"] == 3
    assert view.ops_from_json([{"bogus": 1}, "junk"]) == []


def json_round_trip(x):
    import json
    return json.loads(json.dumps(x))


def test_closed_entries_newest_first_and_prune(tmp_path, view, monkeypatch):
    monkeypatch.setattr(view, "CLOSED_DIR", str(tmp_path))
    for n in ("20260906_100000_000001", "20260906_100000_000003", "20260906_100000_000002"):
        (tmp_path / f"{n}.json").write_text("{}")
        (tmp_path / f"{n}.png").write_bytes(b"")
    entries = view.closed_entries()
    assert [os.path.basename(j) for j, _ in entries] == ["20260906_100000_000003.json", "20260906_100000_000002.json",
                                                         "20260906_100000_000001.json"]
    view.prune_closed(1)
    assert sorted(os.listdir(tmp_path)) == ["20260906_100000_000003.json", "20260906_100000_000003.png"]
    monkeypatch.setattr(view, "CLOSED_DIR", str(tmp_path / "missing"))
    assert view.closed_entries() == []
    assert view.ACTIONS["destroy"] == "shift+Escape" and "--reopen" in view.COMMANDS


def test_display_settings(view, monkeypatch):
    monkeypatch.setenv("SNIP_PIN_ALPHA_BG", "Checker")
    assert view.alpha_background() == "checker"
    monkeypatch.setenv("SNIP_PIN_ALPHA_BG", "#FFEEDD")
    assert view.alpha_background() == "#ffeedd"
    monkeypatch.setenv("SNIP_PIN_ALPHA_BG", "bogus")
    assert view.alpha_background() == "transparent"
    monkeypatch.setenv("SNIP_PIN_OPACITY", "70")
    assert view.default_opacity() == 0.7
    monkeypatch.setenv("SNIP_PIN_OPACITY", "3")
    assert view.default_opacity() == 0.1
    monkeypatch.setenv("SNIP_PIN_OPACITY", "x")
    assert view.default_opacity() == 1.0
    assert view.checker_pattern() is view.checker_pattern()


def test_thumbnail_helpers(view, monkeypatch):
    assert view.thumb_scale(914, 404, 75) == 75 / 914
    assert view.thumb_scale(0, 0, 75) == 75
    monkeypatch.setenv("SNIP_PIN_THUMB_SIZE", "120")
    assert view.thumb_size() == 120
    monkeypatch.setenv("SNIP_PIN_THUMB_SIZE", "x")
    assert view.thumb_size() == 75
    assert view.MOUSE_DEFAULTS["shift_double"] == "thumbnail" and "thumbnail" in view.MOUSE_ACTIONS
    assert view.ACTIONS["thumbnail"] == "ctrl+m shift+Return"


def test_output_settings(view, monkeypatch):
    monkeypatch.setenv("SNIP_PIN_FILENAME", "shot_%H%M")
    assert view.file_pattern() == "shot_%H%M"
    monkeypatch.setenv("SNIP_PIN_FILENAME", "sub/dir_%H")
    assert view.file_pattern() == "pin_%Y%m%d_%H%M%S"
    for v, fmt in (("png", "png"), ("JPG", "jpeg"), (".jpeg", "jpeg"), ("webp", "webp"), ("gif", "png")):
        monkeypatch.setenv("SNIP_PIN_FORMAT", v)
        assert view.save_format() == fmt
    monkeypatch.setenv("SNIP_PIN_QUALITY", "150")
    assert view.save_quality() == 100
    monkeypatch.setenv("SNIP_PIN_QUALITY", "x")
    assert view.save_quality() == 90


def test_write_image_png_and_jpeg(tmp_path, view):
    from gi.repository import GdkPixbuf
    src = tmp_path / "s.png"
    make_png(str(src), 30, 20)
    pb = GdkPixbuf.Pixbuf.new_from_file(str(src)).add_alpha(False, 0, 0, 0)
    ops = [{"kind": "rect", "pts": [(2, 2), (20, 15)], "color": (1, 0, 0), "width": 2, "text": ""}]
    view.write_image(pb, ops, str(tmp_path / "o.png"), "png")
    assert png_size(str(tmp_path / "o.png")) == (30, 20)
    view.write_image(pb, ops, str(tmp_path / "o.jpg"), "jpeg", 80)           # alpha flattened, no error
    _, w, h = GdkPixbuf.Pixbuf.get_file_info(str(tmp_path / "o.jpg"))
    assert (w, h) == (30, 20)
    assert view.render_pixbuf(pb, []) is pb


def test_last_extension_round_trip(tmp_path, view, monkeypatch):
    monkeypatch.setattr(view, "STATE_DIR", str(tmp_path / "state"))
    assert view.last_extension() == ".png"
    view.remember_extension(".webp")
    assert view.last_extension() == ".webp"
    (tmp_path / "state" / "last-ext").write_text(".exe")
    assert view.last_extension() == ".png"


def test_clipboard_settings_and_paths(view, monkeypatch):
    monkeypatch.setenv("SNIP_PIN_COPY_FILE", "never")
    assert view.copy_as_file() is False
    monkeypatch.setenv("SNIP_PIN_COPY_FILE", "always")
    assert view.copy_as_file() is True
    p = view.clip_file_path("/tmp/x/snip-pin-abc.png")
    assert p.startswith(view.CLIP_DIR) and p.endswith(".png") and "snip_" in os.path.basename(p)


def test_print_action_is_registered(view):
    assert view.ACTIONS["print"] == "ctrl+p" and "print" in view.MOUSE_ACTIONS


def test_sound_command(view):
    assert view.sound_command("", "/usr/bin/cgp") is None
    assert view.sound_command("off", "/usr/bin/cgp") is None
    assert view.sound_command("default", "/usr/bin/cgp") == ["/usr/bin/cgp", "-i", "screen-capture"]
    assert view.sound_command("~/x.oga", "/usr/bin/cgp") == ["/usr/bin/cgp", "-f", os.path.expanduser("~/x.oga")]
    assert view.sound_command("default", None) is None                  # GTK's player takes over


def test_command_argv_substitution(view):
    assert view.command_argv("gimp %f", "/tmp/a.png", "/c/o.png") == ["gimp", "/tmp/a.png"]
    argv = view.command_argv("sh -c 'cp %f %F.bak'", "/tmp/a b.png", "/c/o.png")
    assert argv == ["sh", "-c", "cp /tmp/a b.png /c/o.png.bak"]
    assert view.command_argv("printf %%s %f", "x", "y") == ["printf", "%s", "x"]
    assert view.command_argv("", "x", "y") == []


def test_commands_keep_their_names_and_order(view):
    cfg = view.parse_config("[commands]\nOpen in GIMP = gimp %f\nUpload = up.sh %f\nempty =\n")
    assert list(cfg["commands"]) == ["Open in GIMP", "Upload", "empty"]
    assert view.ACTIONS["command_1"] == "ctrl+shift+1" and view.ACTIONS["command_9"] == "ctrl+shift+9"
    assert "command_3" in view.MOUSE_ACTIONS and "open_with" in view.MOUSE_ACTIONS


def test_prune_dir_removes_old_files_only(tmp_path, view):
    old, new = tmp_path / "old.png", tmp_path / "new.png"
    old.write_bytes(b""); new.write_bytes(b"")
    import time as t
    os.utime(old, (t.time() - 7200,) * 2)
    view.prune_dir(str(tmp_path), 3600)
    assert os.listdir(tmp_path) == ["new.png"]
    view.prune_dir(str(tmp_path / "missing"))                       # no error


def test_palette_and_widths_settings(view):
    assert view.load_palette("") == view.DEFAULT_COLORS
    pal = view.load_palette("#FF0000, #00ff00,bogus, #0000ff")
    assert pal == [("colour 1", "#ff0000"), ("colour 2", "#00ff00"), ("colour 3", "#0000ff")]
    assert view.load_palette("#e5312b")[0] == ("red", "#e5312b")                 # a default keeps its name
    assert len(view.load_palette(",".join(["#111111"] * 12))) == 9
    assert view.load_widths("") == view.DEFAULT_WIDTHS
    assert view.load_widths("8, 2, 4") == [("thin", 2), ("normal", 4), ("thick", 8)]
    assert view.load_widths("3, 6") == [("thin", 3), ("normal", 6)]
    assert view.load_widths("1,2,3,4,5,6,7") == [("thin", 1), ("normal", 2), ("3 px", 3), ("4 px", 4), ("5 px", 5)]
    assert view.load_widths("x") == view.DEFAULT_WIDTHS and view.load_widths("5") == view.DEFAULT_WIDTHS
    assert view.size_for(view.TEXT_PX, 4, 12, 2.5) == 22 and view.size_for(view.TEXT_PX, 10, 12, 2.5) == 37


def test_tool_memory(view, tmp_path, monkeypatch):
    mem = {}
    view.remember_tool(mem, "arrow", "#e5312b", 7)
    view.remember_tool(mem, None, "#000000", 2)
    assert mem == {"arrow": ("#e5312b", 7)}
    assert view.recall_tool(mem, "arrow", view.DEFAULT_COLORS, view.DEFAULT_WIDTHS) == (0, 2)
    assert view.recall_tool(mem, "pen", view.DEFAULT_COLORS, view.DEFAULT_WIDTHS) == (None, None)
    assert view.recall_tool(mem, "arrow", [("x", "#123456")], [("w", 3)]) == (None, None)   # palette changed
    monkeypatch.setattr(view, "tool_memory_path", lambda: str(tmp_path / "tc.json"))
    view.TOOL_MEMORY.clear()
    view.TOOL_MEMORY.update(mem)
    view.save_tool_memory()
    view.TOOL_MEMORY.clear()
    view.load_tool_memory()
    assert view.TOOL_MEMORY == {"arrow": ("#e5312b", 7)}
    (tmp_path / "tc.json").write_text('{"pen": ["nothex", 2], "text": ["#000000", 4]}')
    view.TOOL_MEMORY.clear()
    view.load_tool_memory()
    assert view.TOOL_MEMORY == {"text": ("#000000", 4)}
