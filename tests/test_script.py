"""snip-pin.sh without a compositor: version, usage, and the cached-file paths."""
import os
import subprocess
import time

from conftest import ROOT

SCRIPT = os.path.join(ROOT, "snip-pin.sh")


def run(args, env, **kw):
    return subprocess.run([SCRIPT, *args], env=env, capture_output=True, text=True, **kw)


def make_env(tmp_path, viewer_log=None):
    env = {"PATH": os.environ["PATH"], "HOME": str(tmp_path),
           "XDG_CACHE_HOME": str(tmp_path / "cache"), "XDG_RUNTIME_DIR": str(tmp_path / "run")}
    (tmp_path / "run").mkdir(exist_ok=True)
    if viewer_log is not None:
        fake = tmp_path / "viewer.sh"
        fake.write_text(f'#!/bin/sh\nprintf "%s\\n" "$@" > "{viewer_log}"\n')
        fake.chmod(0o755)
        env["SNIP_PIN_VIEWER"] = str(fake)
    return env


def test_version_matches_the_file(tmp_path):
    r = run(["--version"], make_env(tmp_path))
    assert r.returncode == 0
    assert r.stdout.strip() == open(os.path.join(ROOT, "VERSION")).read().strip()


def test_usage_on_unknown_subcommand(tmp_path):
    r = run(["bogus"], make_env(tmp_path))
    assert r.returncode == 2 and "usage" in r.stderr


def test_pin_requires_an_existing_file(tmp_path):
    assert run(["pin", str(tmp_path / "missing.png")], make_env(tmp_path)).returncode == 1


def wait_for(path, seconds=3):
    for _ in range(int(seconds * 50)):
        if os.path.exists(path) and open(path).read():
            return open(path).read().splitlines()
        time.sleep(0.02)
    raise AssertionError(f"{path} not written")


def test_last_passes_the_position_from_the_file_name(tmp_path):
    log = tmp_path / "viewer.log"
    env = make_env(tmp_path, log)
    cache = tmp_path / "cache" / "snip-pin"
    cache.mkdir(parents=True)
    old = cache / "20260901_000000_000_x1_y2.png"
    new = cache / "20260902_000000_000_x-15_y700.png"
    for p, age in ((old, 100), (new, 10)):
        p.write_bytes(b"png")
        os.utime(p, (time.time() - age, time.time() - age))
    assert run(["last"], env).returncode == 0
    assert wait_for(log) == [str(new), "-15", "700"]


def test_last_without_position_passes_only_the_file(tmp_path):
    log = tmp_path / "viewer.log"
    env = make_env(tmp_path, log)
    cache = tmp_path / "cache" / "snip-pin"
    cache.mkdir(parents=True)
    (cache / "20260901_000000_000_clipboard.png").write_bytes(b"png")
    run(["last"], env)
    assert wait_for(log) == [str(cache / "20260901_000000_000_clipboard.png")]


def test_startup_removes_annotated_leftovers_and_expired_snips(tmp_path):
    env = make_env(tmp_path)
    env["SNIP_PIN_KEEP_DAYS"] = "7"
    cache = tmp_path / "cache" / "snip-pin"
    (cache / "kept").mkdir(parents=True)
    keep = cache / "fresh.png"
    keep.write_bytes(b"")
    stale = cache / "stale.png"
    stale.write_bytes(b"")
    os.utime(stale, (time.time() - 10 * 86400,) * 2)
    (cache / "x_annotated.png").write_bytes(b"")
    (cache / "kept" / "y_annotated.png").write_bytes(b"")
    kept = cache / "kept" / "forever.png"
    kept.write_bytes(b"")
    os.utime(kept, (time.time() - 100 * 86400,) * 2)
    run(["bogus"], env)                          # any invocation runs the cleanup
    assert sorted(os.listdir(cache)) == ["fresh.png", "kept"]
    assert os.listdir(cache / "kept") == ["forever.png"]


def test_invalid_environment_numbers_fall_back(tmp_path):
    env = make_env(tmp_path)
    env["SNIP_PIN_KEEP_DAYS"] = "abc"
    env["SNIP_PIN_TAP_MS"] = "-3"
    r = run(["bogus"], env)
    assert r.returncode == 2
    assert "usage" in r.stderr and "integer" not in r.stderr and "syntax" not in r.stderr


def test_pin_without_a_file_is_usage(tmp_path):
    r = run(["pin"], make_env(tmp_path))
    assert r.returncode == 1 and "usage" in r.stderr


def fake_tools(tmp_path, geom="400x300+600+400", slurp_rc=0):
    """Stubs for hyprctl, slurp, grim and wl-copy so the selection path runs without a compositor."""
    b = tmp_path / "bin"
    b.mkdir(exist_ok=True)
    log = tmp_path / "tools.log"

    def stub(name, body):
        p = b / name
        p.write_text(f'#!/bin/sh\necho "{name} $*" >> "{log}"\n{body}\n')
        p.chmod(0o755)
    monitors = ('[{"activeWorkspace":{"id":1},"specialWorkspace":{"id":0}},'
                ' {"activeWorkspace":{"id":5},"specialWorkspace":{"id":-98}}]')
    clients = ('[{"workspace":{"id":1},"mapped":true,"hidden":false,"at":[10,20],"size":[300,200]},'
               ' {"workspace":{"id":5},"mapped":true,"hidden":false,"at":[3440,0],"size":[1920,1080]},'
               ' {"workspace":{"id":-98},"mapped":true,"hidden":false,"at":[500,500],"size":[100,100]},'
               ' {"workspace":{"id":2},"mapped":true,"hidden":false,"at":[0,0],"size":[50,50]},'
               ' {"workspace":{"id":1},"mapped":false,"hidden":false,"at":[1,1],"size":[2,2]},'
               ' {"workspace":{"id":1},"mapped":true,"hidden":true,"at":[3,3],"size":[4,4]}]')
    stub("hyprctl", f'case "$1" in monitors) echo \'{monitors}\';; clients) echo \'{clients}\';; '
                    'keyword) echo "ok";; *) echo ok;; esac')
    stub("slurp", f'cat > "{tmp_path}/slurp.in"; printf "%s" "{geom}"; exit {slurp_rc}')
    stub("grim", 'for a; do f=$a; done; printf "png" > "$f"')
    stub("wl-copy", "cat >/dev/null")
    return b, log


def test_selection_path_with_fake_tools(tmp_path):
    viewer_log = tmp_path / "viewer.log"
    env = make_env(tmp_path, viewer_log)
    b, log = fake_tools(tmp_path)
    env["PATH"] = f"{b}:{env['PATH']}"
    env["SNIP_NO_ELEMENTS"] = "1"
    r = run([], env)
    assert r.returncode == 0, r.stderr
    args = wait_for(viewer_log)
    assert args[0].endswith("_x600_y400.png") and args[1:] == ["600", "400"]
    assert os.path.exists(args[0])
    tools = log.read_text()
    assert "grim -g 600,400 400x300" in tools and "wl-copy" in tools
    assert not os.path.exists(tmp_path / "run" / "snip-pin" / "selecting")   # cleaned up
    # windows on both monitors' workspaces and the special workspace; not on an
    # inactive workspace, not unmapped, not hidden
    boxes = (tmp_path / "slurp.in").read_text().split()
    assert "10,20 300x200" in " ".join(boxes) and "3440,0 1920x1080" in " ".join(boxes)
    assert "500,500 100x100" in " ".join(boxes)
    assert "0,0 50x50" not in " ".join(boxes) and "1,1 2x2" not in " ".join(boxes) and "3,3 4x4" not in " ".join(boxes)


def test_aborted_selection_captures_nothing(tmp_path):
    env = make_env(tmp_path, tmp_path / "viewer.log")
    b, log = fake_tools(tmp_path, geom="", slurp_rc=1)
    env["PATH"] = f"{b}:{env['PATH']}"
    env["SNIP_NO_ELEMENTS"] = "1"
    assert run([], env).returncode == 0
    assert "grim" not in log.read_text()
    assert not os.path.exists(tmp_path / "viewer.log")


def test_corrupt_double_tap_state_is_ignored(tmp_path):
    env = make_env(tmp_path, tmp_path / "viewer.log")
    b, log = fake_tools(tmp_path)
    env["PATH"] = f"{b}:{env['PATH']}"
    env["SNIP_NO_ELEMENTS"] = "1"
    state = tmp_path / "run" / "snip-pin"
    state.mkdir(parents=True)
    (state / "selecting").write_text("garbage\n")
    r = run([], env)
    assert r.returncode == 0 and "syntax" not in r.stderr and "error" not in r.stderr.lower()
    wait_for(tmp_path / "viewer.log")


def test_abort_ends_only_our_slurp(tmp_path):
    import signal
    import subprocess as sp
    env = make_env(tmp_path)
    state = tmp_path / "run" / "snip-pin"
    state.mkdir(parents=True)
    # a process that is not slurp, recorded under our PID file: must survive
    other = sp.Popen(["sleep", "30"])
    try:
        (state / "slurp").write_text(f"{other.pid}\n")
        assert run(["abort"], env).returncode == 0
        assert other.poll() is None
    finally:
        other.send_signal(signal.SIGTERM)
    # no state file at all: still a clean exit
    (state / "slurp").unlink()
    assert run(["abort"], env).returncode == 0


def fake_wl_paste(tmp_path, types, payloads):
    """wl-paste stub: --list-types prints TYPES, --type X cats payloads[X]."""
    b = tmp_path / "bin"
    b.mkdir(exist_ok=True)
    cases = "".join(f'"{t}") cat "{p}";;\n' for t, p in payloads.items())
    (b / "wl-paste").write_text('#!/bin/sh\nif [ "$1" = "--list-types" ]; then printf "%s\\n" '
                                + " ".join(f'"{t}"' for t in types) + '; exit 0; fi\n'
                                'case "$2" in\n' + cases + '*) exit 1;;\nesac\n')
    (b / "wl-paste").chmod(0o755)
    return b


def make_jpeg(path):
    import gi
    gi.require_version("GdkPixbuf", "2.0")
    from gi.repository import GdkPixbuf
    pb = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, 24, 16)
    pb.fill(0x336699FF)
    pb.savev(str(path), "jpeg", [], [])


def png_header(path):
    with open(path, "rb") as f:
        return f.read(8) == b"\x89PNG\r\n\x1a\n"


def test_clipboard_png_is_cached_and_pinned(tmp_path):
    log = tmp_path / "viewer.log"
    env = make_env(tmp_path, log)
    src = tmp_path / "src.png"
    src.write_bytes(b"\x89PNG\r\n\x1a\n" + b"rest")
    env["PATH"] = f"{fake_wl_paste(tmp_path, ['text/plain', 'image/png'], {'image/png': src})}:{env['PATH']}"
    assert run(["clipboard"], env).returncode == 0
    args = wait_for(log)
    assert args[0].endswith("_clipboard.png") and len(args) == 1 and png_header(args[0])


def test_clipboard_jpeg_is_converted(tmp_path):
    log = tmp_path / "viewer.log"
    env = make_env(tmp_path, log)
    src = tmp_path / "src.jpg"
    make_jpeg(src)
    env["PATH"] = f"{fake_wl_paste(tmp_path, ['image/jpeg'], {'image/jpeg': src})}:{env['PATH']}"
    r = run(["clipboard"], env)
    assert r.returncode == 0, r.stderr
    args = wait_for(log)
    assert args[0].endswith("_clipboard.png") and png_header(args[0])


def test_clipboard_uri_list_to_a_local_file(tmp_path):
    log = tmp_path / "viewer.log"
    env = make_env(tmp_path, log)
    img = tmp_path / "my pic.jpg"
    make_jpeg(img)
    uris = tmp_path / "uris.txt"
    uris.write_text(f"file://{str(img).replace(' ', '%20')}\r\n")
    env["PATH"] = f"{fake_wl_paste(tmp_path, ['text/uri-list'], {'text/uri-list': uris})}:{env['PATH']}"
    r = run(["clipboard"], env)
    assert r.returncode == 0, r.stderr
    args = wait_for(log)
    assert png_header(args[0]) and img.exists()          # the original is left alone


def test_clipboard_without_an_image(tmp_path):
    env = make_env(tmp_path, tmp_path / "viewer.log")
    env["PATH"] = f"{fake_wl_paste(tmp_path, ['text/plain'], {})}:{env['PATH']}"
    assert run(["clipboard"], env).returncode == 0
    assert not (tmp_path / "viewer.log").exists()
    assert not list((tmp_path / "cache" / "snip-pin").glob("*.png"))


def test_doctor_reports_and_exits_by_required_tools(tmp_path):
    env = make_env(tmp_path)
    b, _ = fake_tools(tmp_path)
    for name in ("wl-paste", "jq"):
        (b / name).write_text("#!/bin/sh\nexit 0\n")
        (b / name).chmod(0o755)
    env["PATH"] = f"{b}:{env['PATH']}"
    r = run(["doctor"], env)
    assert "required:" in r.stdout and "optional:" in r.stdout and "hyprland:" in r.stdout
    assert r.returncode == 0, r.stdout + r.stderr
    # a PATH with only what the script itself needs: every capture tool is missing
    import shutil
    import sys
    mini = tmp_path / "mini"
    mini.mkdir()
    for tool in ("bash", "sh", "cat", "readlink", "dirname", "basename", "mkdir", "find", "rm", "sort",
                 "head", "cut", "date", "wc", "od", "tr", "grep", "sed", "jq", "env"):
        real = shutil.which(tool)
        if real:
            os.symlink(real, mini / tool)
    os.symlink(sys.executable, mini / "python3")
    env["PATH"] = str(mini)
    r = run(["doctor"], env)
    assert r.returncode == 1, r.stdout + r.stderr
    for tool in ("grim", "slurp", "wl-copy", "hyprctl"):
        assert f"{tool:<12} MISSING" in r.stdout
