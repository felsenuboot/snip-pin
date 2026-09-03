#!/usr/bin/env python3
"""Find rectangular UI elements (images, panels, cards) in a screenshot.

Reads a binary PPM (P6) from a file or stdin and prints one "x,y wxh" line
per rectangle, the format slurp takes on stdin. Pure numpy, no OpenCV.

Approach, borrowed from Snipaste: long horizontal and vertical luminance
steps are collected as line segments, and a rectangle is reported wherever a
top and a bottom segment are bridged by a left and a right segment. The pass
runs on the green channel at full resolution; a 3440x1440 frame takes about
50 ms after numpy is imported.

Usage: snip-elements.py [FILE.ppm] [--debug OUT.png]
"""

import sys
import time

import numpy as np

EDGE = 14          # min luminance step (0-255) that counts as an edge
MIN_LEN = 32       # min segment length in pixels
TOL = 24           # slack when joining segments into corners (rounded corners)
MIN_SIDE = 24      # min rectangle side
MAX_SEGS = 3000    # safety cap on segments per orientation


def read_ppm(data):
    # P6\nW H\nMAXVAL\n<binary>; grim writes exactly this without comments.
    parts = data.split(maxsplit=4)
    if parts[0] != b"P6":
        raise SystemExit("not a binary PPM")
    w, h = int(parts[1]), int(parts[2])
    px = np.frombuffer(data, dtype=np.uint8, count=w * h * 3, offset=len(data) - w * h * 3)
    return px.reshape(h, w, 3)


def runs(mask):
    """Runs of True along axis 1 -> (row, start, end, length), end exclusive."""
    _h, w = mask.shape
    start = np.empty_like(mask)
    start[:, 0] = mask[:, 0]
    np.logical_and(mask[:, 1:], ~mask[:, :-1], out=start[:, 1:])
    end = np.empty_like(mask)
    end[:, -1] = mask[:, -1]
    np.logical_and(mask[:, :-1], ~mask[:, 1:], out=end[:, :-1])
    # flatnonzero is an order of magnitude faster than 2-D nonzero here
    rows, x0 = np.divmod(np.flatnonzero(start), w)
    x1 = np.flatnonzero(end) % w + 1
    length = x1 - x0
    keep = length >= MIN_LEN
    return rows[keep], x0[keep], x1[keep], length[keep]


def cap(segs):
    a, b, c, length = segs
    if len(a) > MAX_SEGS:
        idx = np.argsort(-length)[:MAX_SEGS]
        return a[idx], b[idx], c[idx]
    return a, b, c


def segments(gray):
    # horizontal edge between rows y-1 and y is reported at y; vertical likewise
    gy = np.abs(np.diff(gray, axis=0)) >= EDGE
    gx = np.abs(np.diff(gray, axis=1)) >= EDGE
    hy, hx0, hx1 = cap(runs(gy))
    vx, vy0, vy1 = cap(runs(np.ascontiguousarray(gx.T)))
    return (hy + 1, hx0, hx1), (vx + 1, vy0, vy1)


def rectangles(hsegs, vsegs):
    hy, hx0, hx1 = (a.astype(np.int32) for a in hsegs)
    vx, vy0, vy1 = (a.astype(np.int32) for a in vsegs)
    out = set()
    if len(hy) == 0 or len(vx) == 0:
        return out
    order = np.argsort(hy)
    hy, hx0, hx1 = hy[order], hx0[order], hx1[order]
    for i in range(len(hy)):
        y, x0, x1 = hy[i], hx0[i], hx1[i]
        # verticals whose top meets this segment somewhere along its span
        touch = (np.abs(vy0 - y) <= TOL) & (vx >= x0 - TOL) & (vx <= x1 + TOL) & (vy1 - y >= MIN_SIDE)
        cand = np.nonzero(touch)[0]
        if len(cand) < 2:
            continue
        cand = cand[np.argsort(vx[cand])]
        cx, cy1 = vx[cand], vy1[cand]
        below = np.nonzero((hy >= y + MIN_SIDE) & (hx0 <= cx.max() + TOL) & (hx1 >= cx.min() - TOL))[0]
        if len(below) == 0:
            continue
        by, bx0, bx1 = hy[below], hx0[below], hx1[below]
        for a in range(len(cand)):
            xa, ya1 = cx[a], cy1[a]
            for b in range(a + 1, len(cand)):
                xb, yb1 = cx[b], cy1[b]
                if xb - xa < MIN_SIDE:
                    continue
                # a bottom segment both verticals reach, spanning both of them
                m = (by <= min(ya1, yb1) + TOL) & (bx0 <= xa + TOL) & (bx1 >= xb - TOL)
                if m.any():
                    out.add((int(xa), int(y), int(xb), int(by[m].min())))
    return out


def dedupe(rects):
    rects = sorted(rects, key=lambda r: (r[2] - r[0]) * (r[3] - r[1]))
    kept = []
    for r in rects:
        for k in kept:
            if all(abs(a - b) <= TOL for a, b in zip(r, k)):
                break
        else:
            kept.append(r)
    return kept


def detect(img):
    gray = img[:, :, 1].astype(np.int16)
    hs, vs = segments(gray)
    rects = dedupe(rectangles(hs, vs))
    return [(x0, y0, x1 - x0, y1 - y0) for x0, y0, x1, y1 in rects], (hs, vs)


def main(argv):
    debug = None
    if "--debug" in argv:
        i = argv.index("--debug")
        debug = argv[i + 1]
        del argv[i:i + 2]
    src = argv[1] if len(argv) > 1 else None
    t0 = time.perf_counter()
    data = open(src, "rb").read() if src else sys.stdin.buffer.read()
    img = read_ppm(data)
    t1 = time.perf_counter()
    rects, segs = detect(img)
    t2 = time.perf_counter()
    sys.stdout.write("".join(f"{x},{y} {w}x{h}\n" for x, y, w, h in rects))
    if debug:
        from PIL import Image, ImageDraw
        im = Image.fromarray(img)
        dr = ImageDraw.Draw(im)
        (hy, hx0, hx1), (vx, vy0, vy1) = segs
        for y, x0, x1 in zip(hy, hx0, hx1):
            dr.line([(x0, y), (x1, y)], fill=(0, 255, 0), width=1)
        for x, y0, y1 in zip(vx, vy0, vy1):
            dr.line([(x, y0), (x, y1)], fill=(0, 200, 255), width=1)
        for x, y, w, h in rects:
            dr.rectangle([x, y, x + w, y + h], outline=(255, 0, 0), width=3)
        im.save(debug)
        print(f"read {1000*(t1-t0):.0f} ms, detect {1000*(t2-t1):.0f} ms, "
              f"{len(hy)} h-segs, {len(vx)} v-segs, {len(rects)} rects", file=sys.stderr)


if __name__ == "__main__":
    main(sys.argv)
