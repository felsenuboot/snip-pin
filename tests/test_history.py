import datetime
import os
import time


def touch(path, mtime):
    open(path, "w").close()
    os.utime(path, (mtime, mtime))


def test_snips_newest_first_including_kept(tmp_path, history):
    cache = str(tmp_path)
    os.makedirs(os.path.join(cache, "kept"))
    now = time.time()
    touch(os.path.join(cache, "old.png"), now - 300)
    touch(os.path.join(cache, "new.png"), now - 10)
    touch(os.path.join(cache, "kept", "mid.png"), now - 100)
    touch(os.path.join(cache, "notes.txt"), now)
    names = [os.path.relpath(p, cache) for p in history.snips(cache)]
    assert names == ["new.png", os.path.join("kept", "mid.png"), "old.png"]


def test_snips_ignore_annotated_leftovers(tmp_path, history):
    cache = str(tmp_path)
    touch(os.path.join(cache, "a.png"), time.time())
    touch(os.path.join(cache, "a_annotated.png"), time.time())
    assert [os.path.basename(p) for p in history.snips(cache)] == ["a.png"]


def test_snips_without_kept_dir(tmp_path, history):
    touch(str(tmp_path / "a.png"), time.time())
    assert len(history.snips(str(tmp_path))) == 1


def test_is_kept(history):
    assert history.is_kept("/c/snip-pin/kept/x.png")
    assert not history.is_kept("/c/snip-pin/x.png")


def make_png(path, w, h):
    import gi
    gi.require_version("GdkPixbuf", "2.0")
    from gi.repository import GdkPixbuf
    pb = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, w, h)
    pb.fill(0x80C0FFFF)
    pb.savev(str(path), "png", [], [])


def test_load_thumb_builds_then_reuses_the_cache(tmp_path, history):
    cache = str(tmp_path)
    src = tmp_path / "a.png"
    make_png(src, 640, 200)
    pb, size = history.load_thumb(cache, str(src))
    assert size == (640, 200)
    assert pb.get_width() <= history.THUMB_W and pb.get_height() <= history.THUMB_H
    thumb = tmp_path / "thumbs" / "a.png"
    assert thumb.exists()
    first = thumb.stat().st_mtime_ns
    pb2, size2 = history.load_thumb(cache, str(src))            # served from the cache
    assert size2 == (640, 200) and thumb.stat().st_mtime_ns == first
    # a newer snip invalidates the thumb
    make_png(src, 300, 300)
    os.utime(src, None)
    os.utime(thumb, (time.time() - 100, time.time() - 100))
    assert history.load_thumb(cache, str(src))[1] == (300, 300)


def test_load_thumb_unreadable(tmp_path, history):
    bad = tmp_path / "bad.png"
    bad.write_bytes(b"nope")
    assert history.load_thumb(str(tmp_path), str(bad)) is None
    assert history.load_thumb(str(tmp_path), str(tmp_path / "missing.png")) is None


def test_prune_thumbs(tmp_path, history):
    d = tmp_path / "thumbs"
    d.mkdir()
    (d / "keep.png").write_bytes(b"")
    (d / "gone.png").write_bytes(b"")
    history.prune_thumbs(str(tmp_path), [str(tmp_path / "keep.png"), str(tmp_path / "kept" / "other.png")])
    assert os.listdir(d) == ["keep.png"]


def test_label_text_recent_and_old(history):
    now = datetime.datetime(2026, 9, 6, 12, 0)
    recent = datetime.datetime(2026, 9, 5, 14, 23)
    old = datetime.datetime(2026, 8, 20, 9, 5)
    assert history.label_text(recent, (800, 600), False, now) == f"{recent:%a} 14:23  ·  800×600"
    assert history.label_text(old, (10, 20), True, now) == f"★ {old:%d %b} 09:05  ·  10×20"
