"""The selection overlay's geometry helpers (no display needed)."""


def test_geometry_parsing_and_formatting(select):
    assert select.parse_geom("400x300+600+400") == (600, 400, 400, 300)
    assert select.parse_geom("100x50+-5+7") == (-5, 7, 100, 50)
    assert select.parse_geom("junk") is None and select.parse_geom("") is None
    assert select.format_geom((600, 400, 400, 300)) == "400x300+600+400"
    assert select.norm(10.4, 20.6, 5.2, 30) == (5, 21, 5, 9)
    assert select.norm(3, 3, 3, 3) == (3, 3, 1, 1)


def test_candidates_smallest_first_and_depth(select):
    rects = [(0, 0, 1000, 800), (100, 100, 300, 200), (120, 120, 50, 50), (600, 600, 10, 10)]
    assert select.candidates(rects, 130, 130) == [(120, 120, 50, 50), (100, 100, 300, 200), (0, 0, 1000, 800)]
    assert select.candidates(rects, 2000, 2000) == []
    assert select.contains((0, 0, 10, 10), 9, 9) and not select.contains((0, 0, 10, 10), 10, 10)


def test_clamp_resize_anchors(select):
    bounds = (0, 0, 1920, 1080)
    assert select.clamp_rect((-10, -10, 100, 100), bounds) == (0, 0, 100, 100)
    assert select.clamp_rect((1900, 1000, 100, 100), bounds) == (1820, 980, 100, 100)
    assert select.clamp_rect((0, 0, 5000, 100), bounds) == (0, 0, 1920, 100)
    pts = select.anchor_points((10, 20, 100, 50))
    assert pts["nw"] == (10, 20) and pts["se"] == (110, 70) and pts["e"] == (110, 45)
    assert select.resize_rect((10, 20, 100, 50), "se", 200, 100) == (10, 20, 190, 80)
    assert select.resize_rect((10, 20, 100, 50), "nw", 0, 0) == (0, 0, 110, 70)
    assert select.resize_rect((10, 20, 100, 50), "e", 5, 999) == (5, 20, 5, 50)      # dragged past the other edge


def test_monitor_rect_and_colours(select):
    rotated = {"x": 3440, "y": 0, "width": 3840, "height": 2160, "scale": 2, "transform": 1}
    assert select.monitor_rect(rotated) == (3440, 0, 1080, 1920)
    plain = {"x": 0, "y": 0, "width": 3440, "height": 1440, "scale": 1, "transform": 0}
    assert select.monitor_rect(plain) == (0, 0, 3440, 1440)
    assert select.parse_color("#ff0000", None) == (1.0, 0.0, 0.0, 1.0)
    r, g, b, a = select.parse_color("#00000080", None)
    assert (r, g, b) == (0, 0, 0) and abs(a - 128 / 255) < 1e-9
    assert select.parse_color("nope", (1, 2, 3, 4)) == (1, 2, 3, 4)
    assert select.color_text((255, 128, 0), True) == "#ff8000"
    assert select.color_text((255, 128, 0), False) == "rgb(255, 128, 0)"


def test_pixel_at(select, tmp_path):
    from gi.repository import GdkPixbuf
    pb = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, 4, 3)
    pb.fill(0x10203000)
    assert select.pixel_at(pb, 0, 0) == (0x10, 0x20, 0x30)
    assert select.pixel_at(pb, 4, 0) is None and select.pixel_at(pb, 0, -1) is None


def test_move_anchor_and_anchor_near(select):
    r = (10, 20, 100, 50)
    assert select.move_anchor(r, "se", 5, -3) == (10, 20, 105, 47)
    assert select.move_anchor(r, "nw", -2, -2) == (8, 18, 102, 52)
    assert select.move_anchor(r, "n", 99, 5) == (10, 25, 100, 45)             # a midpoint ignores the other axis
    assert select.move_anchor(r, "e", -200, 0) == (0, 20, 10, 50)             # dragged past the far edge: flips
    assert select.anchor_near(r, 110, 70) == "se" and select.anchor_near(r, 60, 20) == "n"
    assert select.anchor_near(r, 60, 45) is None
