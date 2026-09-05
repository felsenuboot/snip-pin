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
