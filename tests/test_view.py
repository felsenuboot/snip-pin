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
    assert view.screenshot_folder() == str(tmp_path / "Pictures")


def test_screenshot_folder_ml4w(tmp_path, view, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    cfg = tmp_path / ".config/ml4w/settings"
    cfg.mkdir(parents=True)
    (cfg / "screenshot-folder").write_text("~/Shots\n")
    assert view.screenshot_folder() == str(tmp_path / "Shots")
    (cfg / "screenshot-folder").write_text("\n")                      # empty: fall back
    assert view.screenshot_folder() == str(tmp_path / "Pictures")


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
