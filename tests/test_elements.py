import time

import numpy as np

W, H = 640, 400


def blank(w=W, h=H, v=240):
    return np.full((h, w, 3), v, np.uint8)


def box(img, x0, y0, x1, y1, v=40, t=1):
    """Outline of a rectangle (x1, y1 exclusive), t px thick."""
    img[y0:y0 + t, x0:x1] = v
    img[y1 - t:y1, x0:x1] = v
    img[y0:y1, x0:x0 + t] = v
    img[y0:y1, x1 - t:x1] = v


def filled(img, x0, y0, x1, y1, v=40):
    img[y0:y1, x0:x1] = v


def grid(w, h, cols, rows):
    img = blank(w, h)
    for c in range(cols + 1):
        img[:, min(w - 1, c * w // cols), :] = 40
    for r in range(rows + 1):
        img[min(h - 1, r * h // rows), :, :] = 40
    return img


def ppm(img):
    h, w, _ = img.shape
    return b"P6\n%d %d\n255\n" % (w, h) + img.tobytes()


def near(rect, x, y, w, h, tol=3):
    return abs(rect[0] - x) <= tol and abs(rect[1] - y) <= tol and abs(rect[2] - w) <= tol and abs(rect[3] - h) <= tol


# ---- read_ppm -------------------------------------------------------------

def test_read_ppm_roundtrip(elements):
    img = blank(5, 3)
    img[1, 2] = (1, 2, 3)
    out = elements.read_ppm(ppm(img))
    assert out.shape == (3, 5, 3)
    assert tuple(out[1, 2]) == (1, 2, 3)


def test_read_ppm_rejects_bad_input(elements):
    assert elements.read_ppm(b"") is None
    assert elements.read_ppm(b"P6\n") is None
    assert elements.read_ppm(b"P5\n1 1\n255\n\0") is None                 # not P6
    assert elements.read_ppm(b"P6\n10 10\n255\n" + b"\0" * 10) is None    # truncated
    assert elements.read_ppm(b"P6\nx y\n255\n\0\0\0") is None             # garbage size


def test_read_ppm_uses_the_tail_as_pixels(elements):
    # grim writes exactly header + pixels; the parser must not depend on the header length
    img = blank(4, 4)
    data = b"P6\n4 4\n255\n" + img.tobytes()
    assert elements.read_ppm(data).shape == (4, 4, 3)


# ---- detect ---------------------------------------------------------------

def test_single_filled_box(elements):
    img = blank()
    filled(img, 100, 80, 300, 220)
    rects, _ = elements.detect(img)
    assert any(near(r, 100, 80, 200, 140) for r in rects), rects


def test_single_outlined_box(elements):
    img = blank()
    box(img, 50, 50, 250, 200, t=2)
    rects, _ = elements.detect(img)
    assert any(near(r, 50, 50, 200, 150, tol=4) for r in rects), rects


def test_nested_boxes_both_found(elements):
    img = blank()
    filled(img, 40, 40, 600, 360, v=200)         # a panel
    filled(img, 100, 100, 300, 250, v=40)        # an image inside it
    rects, _ = elements.detect(img)
    assert any(near(r, 40, 40, 560, 320) for r in rects), rects
    assert any(near(r, 100, 100, 200, 150) for r in rects), rects


def test_smallest_first(elements):
    img = blank()
    filled(img, 40, 40, 600, 360, v=200)
    filled(img, 100, 100, 300, 250, v=40)
    rects, _ = elements.detect(img)
    areas = [w * h for _, _, w, h in rects]
    assert areas == sorted(areas)


def test_blank_screen_has_no_elements(elements):
    rects, _ = elements.detect(blank())
    assert rects == []


def test_tiny_boxes_are_ignored(elements):
    img = blank()
    filled(img, 10, 10, 20, 20)                  # below MIN_SIDE
    rects, _ = elements.detect(img)
    assert rects == []


def test_grid_cells(elements):
    img = grid(W, H, 4, 2)
    rects, _ = elements.detect(img)
    assert len(rects) >= 8                       # every cell, plus merged neighbours
    cw, ch = W // 4, H // 2
    assert any(near(r, cw, 0, cw, ch, tol=4) for r in rects), rects[:10]


def test_budget_bounds_a_pathological_frame(elements):
    img = grid(3440, 1440, 60, 30)               # took ~3 s before the budget
    t0 = time.perf_counter()
    rects, _ = elements.detect(img, budget_ms=50)
    dt = time.perf_counter() - t0
    assert dt < 0.6, dt                          # segments ~35 ms + budget + dedupe
    assert rects                                 # and still returns something


def test_rects_are_capped(elements):
    rects, _ = elements.detect(grid(3440, 1440, 60, 30), budget_ms=0)
    assert len(rects) <= elements.MAX_RECTS


def test_dedupe_drops_near_duplicates_and_keeps_order(elements):
    a = (0, 0, 100, 100)
    b = (1, 1, 101, 101)                         # same up to TOL
    c = (0, 0, 500, 500)
    out = elements.dedupe({a, b, c})
    assert len(out) == 2
    assert out[0] in (a, b) and out[1] == c


# ---- CLI ------------------------------------------------------------------

def test_cli_output_format(tmp_path, elements):
    import subprocess
    import sys
    img = blank()
    filled(img, 100, 80, 300, 220)
    f = tmp_path / "in.ppm"
    f.write_bytes(ppm(img))
    out = subprocess.run([sys.executable, elements.__file__, str(f)], capture_output=True, text=True, check=True).stdout
    lines = out.splitlines()
    assert lines and all(line.count(",") == 1 and "x" in line for line in lines)


def test_cli_empty_input_is_quiet(elements):
    import subprocess
    import sys
    r = subprocess.run([sys.executable, elements.__file__], input=b"", capture_output=True)
    assert r.returncode == 0 and r.stdout == b"" and r.stderr == b""
