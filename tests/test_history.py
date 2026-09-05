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
