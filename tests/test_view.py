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
           {"kind": "blur", "pts": [(70, 40), (110, 75)], "color": red, "width": 4, "text": ""}]
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
