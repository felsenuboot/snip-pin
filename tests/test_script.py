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
    monitors = ('[{"activeWorkspace":{"id":1},"specialWorkspace":{"id":0},'
                '  "x":0,"y":0,"width":3440,"height":1440,"scale":1,"transform":0},'
                ' {"activeWorkspace":{"id":5},"specialWorkspace":{"id":-98},'
                '  "x":3440,"y":0,"width":3840,"height":2160,"scale":2,"transform":1}]')
    clients = ('[{"workspace":{"id":1},"mapped":true,"hidden":false,"at":[10,20],"size":[300,200]},'
               ' {"workspace":{"id":5},"mapped":true,"hidden":false,"at":[3440,0],"size":[1920,1080]},'
               ' {"workspace":{"id":-98},"mapped":true,"hidden":false,"at":[500,500],"size":[100,100]},'
               ' {"workspace":{"id":2},"mapped":true,"hidden":false,"at":[0,0],"size":[50,50]},'
               ' {"workspace":{"id":1},"mapped":false,"hidden":false,"at":[1,1],"size":[2,2]},'
               ' {"workspace":{"id":1},"mapped":true,"hidden":true,"at":[3,3],"size":[4,4]}]')
    stub("hyprctl", f'case "$1" in monitors) echo \'{monitors}\';; clients) echo \'{clients}\';; '
                    'cursorpos) echo \'{"x": 3500, "y": 100}\';; keyword) echo "ok";; *) echo ok;; esac')
    stub("slurp", f'cat > "{tmp_path}/slurp.in"; printf "%s" "{geom}"; exit {slurp_rc}')
    stub("grim", 'for a; do f=$a; done; printf "png" > "$f"')
    stub("wl-copy", "cat >/dev/null")
    return b, log


def fake_selector(tmp_path, body):
    """A stand-in for snip-select.py: `body` is shell run with the frame in $1 and the JSON on stdin."""
    p = tmp_path / "select.sh"
    p.write_text("#!/bin/sh\n" + body + "\n")
    p.chmod(0o755)
    return str(p)


def test_selection_path_with_fake_tools(tmp_path):
    viewer_log = tmp_path / "viewer.log"
    env = make_env(tmp_path, viewer_log)
    b, log = fake_tools(tmp_path)
    env["PATH"] = f"{b}:{env['PATH']}"
    env["SNIP_NO_ELEMENTS"] = "1"
    env["SNIP_PIN_SELECTOR"] = "slurp"
    r = run([], env)
    assert r.returncode == 0, r.stderr
    args = wait_for(viewer_log)
    assert args[0].endswith("_x600_y400.png") and args[1:] == ["600", "400"]
    assert os.path.exists(args[0])
    tools = log.read_text()
    assert "grim -g 600,400 400x300" in tools and "wl-copy" in tools
    assert "slurp -b #00000080 -c #888888ff -s #00000000 -w 1 -d -f" in tools      # the default look
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
    env["SNIP_PIN_SELECTOR"] = "slurp"
    env["SNIP_NO_ELEMENTS"] = "1"
    assert run([], env).returncode == 0
    assert "grim" not in log.read_text()
    assert not os.path.exists(tmp_path / "viewer.log")


def test_corrupt_double_tap_state_is_ignored(tmp_path):
    env = make_env(tmp_path, tmp_path / "viewer.log")
    b, log = fake_tools(tmp_path)
    env["PATH"] = f"{b}:{env['PATH']}"
    env["SNIP_PIN_SELECTOR"] = "slurp"
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


def test_config_file_fills_in_and_environment_wins(tmp_path):
    env = make_env(tmp_path)
    cfg = tmp_path / ".config" / "snip-pin"
    cfg.mkdir(parents=True)
    (cfg / "config").write_text(
        "# comment\nkeep_days = 3   # trailing comment\n  tap_ms=999\nborder = #123456\n"
        "save_dir = ~/Shots  # synced\nelements_budget_ms = 42\nbogus line\n$(touch /tmp/never)\n"
        "[keys]\ncopy = ctrl+shift+c\n[mouse]\nright = menu\n")
    env["SNIP_PIN_TAP_MS"] = "111"
    r = run(["config"], env)
    assert r.returncode == 0, r.stderr
    out = r.stdout
    assert "keep_days" in out and "= 3 " in out and "(file)" in out
    assert "tap_ms" in out and "= 111 " in out and "(environment)" in out
    assert "border" in out and "#123456" in out
    assert "= ~/Shots " in out and "synced" not in out
    assert "elements_budget_ms" in out and "= 42 " in out
    assert "ctrl+shift+c" not in out and "right" not in out     # sections are the viewer's business
    assert not os.path.exists("/tmp/never")


def test_config_defaults_without_a_file(tmp_path):
    r = run(["config"], make_env(tmp_path))
    assert r.returncode == 0 and "not found" in r.stdout
    assert "keep_days" in r.stdout and "= 7 " in r.stdout and "(default)" in r.stdout


def test_config_file_keep_days_is_applied(tmp_path):
    env = make_env(tmp_path)
    cfg = tmp_path / ".config" / "snip-pin"
    cfg.mkdir(parents=True)
    (cfg / "config").write_text("keep_days = 2\n")
    cache = tmp_path / "cache" / "snip-pin"
    (cache / "kept").mkdir(parents=True)
    stale = cache / "stale.png"
    stale.write_bytes(b"")
    os.utime(stale, (time.time() - 5 * 86400,) * 2)               # older than 2 days, younger than 7
    run(["bogus"], env)
    assert not stale.exists()


def test_selection_look_from_config_with_validation(tmp_path):
    env = make_env(tmp_path, tmp_path / "viewer.log")
    b, log = fake_tools(tmp_path)
    env["PATH"] = f"{b}:{env['PATH']}"
    env["SNIP_PIN_SELECTOR"] = "slurp"
    env["SNIP_NO_ELEMENTS"] = "1"
    cfg = tmp_path / ".config" / "snip-pin"
    cfg.mkdir(parents=True)
    (cfg / "config").write_text("sel_border = #ff0000\nsel_width = 3\nsel_mask = notacolour\nsel_size = 0\n")
    assert run([], env).returncode == 0
    tools = log.read_text()
    assert "slurp -b #00000080 -c #ff0000 -s #00000000 -w 3 -f" in tools           # bad mask: default; no -d


def test_cursor_setting_adds_grim_flag(tmp_path):
    env = make_env(tmp_path, tmp_path / "viewer.log")
    b, log = fake_tools(tmp_path)
    env["PATH"] = f"{b}:{env['PATH']}"
    env["SNIP_PIN_SELECTOR"] = "slurp"
    env["SNIP_NO_ELEMENTS"] = "1"
    env["SNIP_PIN_CURSOR"] = "1"
    assert run([], env).returncode == 0
    assert "grim -g 600,400 400x300 -l 1 -c " in log.read_text()
    env["SNIP_PIN_CURSOR"] = "0"
    assert run([], env).returncode == 0
    assert " -c " not in log.read_text().splitlines()[-2]              # the second grim line


def test_screen_captures_the_monitor_under_the_pointer(tmp_path):
    viewer_log = tmp_path / "viewer.log"
    env = make_env(tmp_path, viewer_log)
    b, log = fake_tools(tmp_path)
    env["PATH"] = f"{b}:{env['PATH']}"
    assert run(["screen"], env).returncode == 0
    # the pointer (3500, 100) is on the second monitor: 3840x2160 at scale 2, rotated -> 1080x1920 logical
    assert "grim -g 3440,0 1080x1920" in log.read_text() and "slurp" not in log.read_text()
    assert wait_for(viewer_log)[1:] == ["3440", "0"]


def test_screen_all_is_the_bounding_box(tmp_path):
    env = make_env(tmp_path, tmp_path / "viewer.log")
    b, log = fake_tools(tmp_path)
    env["PATH"] = f"{b}:{env['PATH']}"
    assert run(["screen", "all"], env).returncode == 0
    assert "grim -g 0,0 4520x1920" in log.read_text()


def test_repeat_reuses_remembered_areas(tmp_path):
    viewer_log = tmp_path / "viewer.log"
    env = make_env(tmp_path, viewer_log)
    b, log = fake_tools(tmp_path)
    env["PATH"] = f"{b}:{env['PATH']}"
    assert run(["repeat"], env).returncode == 0                 # nothing remembered yet: a toast, no capture
    assert not log.exists()
    env["SNIP_GEOM"] = "100x50+10+20"
    assert run([], env).returncode == 0
    env["SNIP_GEOM"] = "300x200+5+5"
    assert run([], env).returncode == 0
    env["SNIP_GEOM"] = "300x200+5+5"                             # a duplicate is not stored twice
    assert run([], env).returncode == 0
    del env["SNIP_GEOM"]
    areas = (tmp_path / "cache" / "snip-pin" / "areas").read_text().split()
    assert areas == ["300x200+5+5", "100x50+10+20"]
    assert run(["repeat"], env).returncode == 0
    assert "grim -g 5,5 300x200" in log.read_text().splitlines()[-2]
    assert run(["repeat", "2"], env).returncode == 0
    assert "grim -g 10,20 100x50" in log.read_text().splitlines()[-2]
    assert run(["repeat", "9"], env).returncode == 0 and log.read_text().count("grim") == 5
    assert run(["repeat", "x"], env).returncode == 1


def test_areas_setting_limits_the_list(tmp_path):
    env = make_env(tmp_path, tmp_path / "viewer.log")
    b, log = fake_tools(tmp_path)
    env["PATH"] = f"{b}:{env['PATH']}"
    env["SNIP_PIN_AREAS"] = "2"
    for g in ("10x10+0+0", "20x20+0+0", "30x30+0+0"):
        env["SNIP_GEOM"] = g
        run([], env)
    assert (tmp_path / "cache" / "snip-pin" / "areas").read_text().split() == ["30x30+0+0", "20x20+0+0"]


def test_copy_mode_copies_without_a_pin(tmp_path):
    viewer_log = tmp_path / "viewer.log"
    env = make_env(tmp_path, viewer_log)
    b, log = fake_tools(tmp_path)
    env["PATH"] = f"{b}:{env['PATH']}"
    env["SNIP_PIN_SELECTOR"] = "slurp"
    env["SNIP_NO_ELEMENTS"] = "1"
    assert run(["copy"], env).returncode == 0
    tools = log.read_text()
    assert "grim" in tools and "wl-copy" in tools
    time.sleep(0.3)
    assert not viewer_log.exists()


def test_save_mode_saves_to_save_dir_without_copy_or_pin(tmp_path):
    viewer_log = tmp_path / "viewer.log"
    env = make_env(tmp_path, viewer_log)
    b, log = fake_tools(tmp_path)
    env["PATH"] = f"{b}:{env['PATH']}"
    env["SNIP_PIN_SELECTOR"] = "slurp"
    env["SNIP_NO_ELEMENTS"] = "1"
    env["SNIP_PIN_SAVE_DIR"] = "~/Shots/$USER/${KIND}"
    env["USER"] = "me"
    env["KIND"] = "work"
    assert run(["save"], env).returncode == 0
    assert run(["save"], env).returncode == 0                    # a second one in the same second: unique name
    saved = sorted(os.listdir(tmp_path / "Shots" / "me" / "work"))
    assert len(saved) == 2 and all(f.startswith("pin_") and f.endswith(".png") for f in saved)
    assert "wl-copy" not in log.read_text()
    time.sleep(0.3)
    assert not viewer_log.exists()


def test_action_setting_controls_the_bare_command(tmp_path):
    viewer_log = tmp_path / "viewer.log"
    env = make_env(tmp_path, viewer_log)
    b, log = fake_tools(tmp_path)
    env["PATH"] = f"{b}:{env['PATH']}"
    env["SNIP_PIN_SELECTOR"] = "slurp"
    env["SNIP_NO_ELEMENTS"] = "1"
    cfg = tmp_path / ".config" / "snip-pin"
    cfg.mkdir(parents=True)
    (cfg / "config").write_text("action = pin\n")
    assert run([], env).returncode == 0
    wait_for(viewer_log)
    assert "wl-copy" not in log.read_text()
    (cfg / "config").write_text("action = bogus\n")               # invalid: the default copy+pin
    viewer_log.unlink()
    assert run([], env).returncode == 0
    wait_for(viewer_log)
    assert "wl-copy" in log.read_text()


def test_autosave_dir_gets_every_capture(tmp_path):
    viewer_log = tmp_path / "viewer.log"
    env = make_env(tmp_path, viewer_log)
    b, log = fake_tools(tmp_path)
    env["PATH"] = f"{b}:{env['PATH']}"
    env["SNIP_PIN_SELECTOR"] = "slurp"
    env["SNIP_NO_ELEMENTS"] = "1"
    env["SNIP_PIN_AUTOSAVE_DIR"] = "~/Auto/$USER"
    env["USER"] = "me"
    assert run([], env).returncode == 0                          # the normal snip: copied, pinned, autosaved
    wait_for(viewer_log)
    env["SNIP_GEOM"] = "10x10+0+0"
    assert run(["copy"], env).returncode == 0                    # copy-only: autosaved too
    saved = os.listdir(tmp_path / "Auto" / "me")
    assert len(saved) == 2 and all(f.startswith("pin_") for f in saved)
    assert "wl-copy" in log.read_text()
    # unwritable target: the snip still goes through
    env["SNIP_PIN_AUTOSAVE_DIR"] = "/proc/nope"
    assert run(["copy"], env).returncode == 0
    assert log.read_text().count("grim") == 3


def test_toggle_and_close_all_go_to_the_viewer(tmp_path):
    log = tmp_path / "viewer.log"
    env = make_env(tmp_path, log)
    assert run(["toggle"], env).returncode == 0
    assert wait_for(log) == ["--toggle"]
    log.unlink()
    assert run(["close-all"], env).returncode == 0
    assert wait_for(log) == ["--close-all"]
    log.unlink()
    assert run(["clickthrough"], env).returncode == 0
    assert wait_for(log) == ["--click-through"]
    log.unlink()
    assert run(["reopen"], env).returncode == 0
    assert wait_for(log) == ["--reopen"]
    log.unlink()
    assert run(["group"], env).returncode == 0
    assert wait_for(log) == ["--group", "next"]
    log.unlink()
    assert run(["group", "3"], env).returncode == 0
    assert wait_for(log) == ["--group", "3"]
    assert run(["group", "x"], env).returncode == 1


def test_save_mode_honours_filename_and_format(tmp_path):
    env = make_env(tmp_path, tmp_path / "viewer.log")
    b, log = fake_tools(tmp_path)
    env["PATH"] = f"{b}:{env['PATH']}"
    env["SNIP_PIN_SELECTOR"] = "slurp"
    env["SNIP_NO_ELEMENTS"] = "1"
    env["SNIP_PIN_SAVE_DIR"] = str(tmp_path / "out")
    env["SNIP_PIN_FILENAME"] = "shot_%Y"
    env["SNIP_PIN_FORMAT"] = "jpg"
    # the grim stub writes "png" bytes, which no encoder can read: the conversion fails, the snip survives
    assert run(["save"], env).returncode == 0
    assert not (tmp_path / "out").exists() or not os.listdir(tmp_path / "out")
    env["SNIP_PIN_FORMAT"] = "png"
    assert run(["save"], env).returncode == 0
    assert run(["save"], env).returncode == 0
    year = time.strftime("%Y")
    assert sorted(os.listdir(tmp_path / "out")) == [f"shot_{year}.png", f"shot_{year}_2.png"]


def test_sound_plays_on_copy_mode(tmp_path):
    env = make_env(tmp_path, tmp_path / "viewer.log")
    b, log = fake_tools(tmp_path)
    (b / "canberra-gtk-play").write_text(f'#!/bin/sh\necho "canberra-gtk-play $*" >> "{log}"\n')
    (b / "canberra-gtk-play").chmod(0o755)
    env["PATH"] = f"{b}:{env['PATH']}"
    env["SNIP_PIN_SELECTOR"] = "slurp"
    env["SNIP_NO_ELEMENTS"] = "1"
    env["SNIP_GEOM"] = "10x10+0+0"
    assert run(["copy"], env).returncode == 0
    time.sleep(0.3)
    assert "canberra-gtk-play" not in log.read_text()               # off by default
    env["SNIP_PIN_SOUND"] = "default"
    assert run(["copy"], env).returncode == 0
    time.sleep(0.3)
    assert "canberra-gtk-play -i screen-capture" in log.read_text()
    env["SNIP_PIN_SOUND"] = "~/ding.oga"
    assert run(["copy"], env).returncode == 0
    time.sleep(0.3)
    assert f"canberra-gtk-play -f {tmp_path}/ding.oga" in log.read_text()


def test_clipboard_text_is_rendered_and_pinned(tmp_path):
    viewer_log = tmp_path / "viewer.log"
    env = make_env(tmp_path, viewer_log)
    txt = tmp_path / "clip.txt"
    txt.write_text("Traceback (most recent call last):\n  File x, line 1\nValueError: boom\n")
    b = fake_wl_paste(tmp_path, ["text/plain;charset=utf-8", "TEXT"], {"text/plain;charset=utf-8": txt})
    env["PATH"] = f"{b}:{env['PATH']}"
    env["SNIP_PIN_TEXT_FONT"] = "Monospace 10"
    assert run(["clipboard"], env).returncode == 0
    args = wait_for(viewer_log)
    assert args[0].endswith("_text.png") and os.path.exists(args[0])
    with open(args[0], "rb") as f:
        assert f.read(8) == b"\x89PNG\r\n\x1a\n"


def test_clipboard_text_naming_an_image_file_pins_the_file(tmp_path):
    viewer_log = tmp_path / "viewer.log"
    env = make_env(tmp_path, viewer_log)
    img = tmp_path / "photo.png"
    make_png_file(str(img))
    txt = tmp_path / "clip.txt"
    txt.write_text(f"  {img}\n")
    env["PATH"] = f"{fake_wl_paste(tmp_path, ['text/plain'], {'text/plain': txt})}:{env['PATH']}"
    assert run(["clipboard"], env).returncode == 0
    args = wait_for(viewer_log)
    assert args[0].endswith("_clipboard.png") and os.path.getsize(args[0]) == os.path.getsize(img)


def make_png_file(path):
    import struct
    import zlib
    raw = b"".join(b"\x00" + b"\xff\x00\x00" * 4 for _ in range(4))
    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 4, 4, 8, 2, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def test_debug_log_and_log_subcommand(tmp_path):
    env = make_env(tmp_path, tmp_path / "viewer.log")
    b, log = fake_tools(tmp_path)
    env["PATH"] = f"{b}:{env['PATH']}"
    env["XDG_STATE_HOME"] = str(tmp_path / "state")
    env["SNIP_GEOM"] = "10x10+0+0"
    r = run(["log"], env)
    assert r.returncode == 0 and "no log" in r.stdout
    assert run([], env).returncode == 0
    assert not (tmp_path / "state" / "snip-pin" / "log").exists()          # off by default
    env["SNIP_PIN_DEBUG"] = "1"
    assert run([], env).returncode == 0
    text = (tmp_path / "state" / "snip-pin" / "log").read_text()
    assert "sh[" in text and "geometry 10x10+0+0" in text and "captured" in text
    r = run(["log"], env)
    assert r.returncode == 0 and "captured" in r.stdout


def test_color_subcommand_copies_the_picked_value(tmp_path):
    env = make_env(tmp_path)
    b, log = fake_tools(tmp_path)
    (b / "hyprpicker").write_text(f'#!/bin/sh\necho "hyprpicker $*" >> "{log}"\nprintf "#a1b2c3"\n')
    (b / "hyprpicker").chmod(0o755)
    (b / "wl-copy").write_text(f'#!/bin/sh\nprintf "wl-copy: %s\\n" "$(cat)" >> "{log}"\n')
    (b / "wl-copy").chmod(0o755)
    env["PATH"] = f"{b}:{env['PATH']}"
    assert run(["color"], env).returncode == 0
    text = log.read_text()
    assert "hyprpicker -f hex -b -q" in text and "wl-copy: #a1b2c3" in text
    env["SNIP_PIN_COLOR_FORMAT"] = "rgb"
    run(["color"], env)
    assert "hyprpicker -f rgb -b -q" in log.read_text()
    env["SNIP_PIN_COLOR_FORMAT"] = "bogus"
    run(["color"], env)
    assert log.read_text().count("-f hex") == 2


def test_capture_comes_from_the_frozen_frame(tmp_path):
    """With the overlay, the capture is cropped out of the frame file, not grabbed again."""
    viewer_log = tmp_path / "viewer.log"
    env = make_env(tmp_path, viewer_log)
    b, log = fake_tools(tmp_path)
    # the grim stub copies a real 60x40 PPM frame: blue, with a red part at x >= 30
    ppm = tmp_path / "frame.ppm"
    rows = b"".join(bytes([255, 0, 0] if x >= 30 else [0, 0, 255]) for _y in range(40) for x in range(60))
    ppm.write_bytes(b"P6\n60 40\n255\n" + rows)
    (b / "grim").write_text(f'#!/bin/sh\necho "grim $*" >> "{log}"\nfor a; do f=$a; done\ncp "{ppm}" "$f"\n')
    (b / "grim").chmod(0o755)
    env["PATH"] = f"{b}:{env['PATH']}"
    env["SNIP_NO_ELEMENTS"] = "1"
    env["SNIP_PIN_SELECT"] = fake_selector(tmp_path, 'printf "20x10+35+5"')
    assert run([], env).returncode == 0
    args = wait_for(viewer_log)
    assert args[0].endswith("_x35_y5.png")
    tools = log.read_text()
    assert tools.count("grim") == 1 and "grim -g" not in tools                  # only the frame grab
    import gi
    gi.require_version("GdkPixbuf", "2.0")
    from gi.repository import GdkPixbuf
    pb = GdkPixbuf.Pixbuf.new_from_file(args[0])
    assert (pb.get_width(), pb.get_height()) == (20, 10)
    px = pb.get_pixels()
    assert (px[0], px[1], px[2]) == (255, 0, 0)                                  # the red part, x >= 30
    # cursor = 1 forces grim
    viewer_log.unlink()
    env["SNIP_PIN_CURSOR"] = "1"
    assert run([], env).returncode == 0
    wait_for(viewer_log)
    assert "grim -g 35,5 20x10 -l 1 -c" in log.read_text()


def test_overlay_gets_the_input_and_its_geometry_is_captured(tmp_path):
    viewer_log = tmp_path / "viewer.log"
    env = make_env(tmp_path, viewer_log)
    b, log = fake_tools(tmp_path)
    env["PATH"] = f"{b}:{env['PATH']}"
    env["SNIP_NO_ELEMENTS"] = "1"
    (tmp_path / "cache" / "snip-pin").mkdir(parents=True)
    (tmp_path / "cache" / "snip-pin" / "areas").write_text("10x10+1+1\n20x20+2+2\n")
    env["SNIP_PIN_SEL_ADJUST"] = "1"
    body = f'cat > "{tmp_path}/input.json"; echo "$1" > "{tmp_path}/frame.txt"; printf "300x200+50+60"'
    env["SNIP_PIN_SELECT"] = fake_selector(tmp_path, body)
    assert run([], env).returncode == 0
    args = wait_for(viewer_log)
    assert args[0].endswith("_x50_y60.png")
    assert "slurp" not in log.read_text() and "hyprpicker" not in log.read_text()
    import json
    data = json.loads((tmp_path / "input.json").read_text())
    assert data["monitors"][0]["name"] is None or "width" in data["monitors"][0]
    assert [10, 20, 300, 200] in data["windows"] and data["elements"] == []
    assert data["areas"] == ["10x10+1+1", "20x20+2+2"] and data["settings"]["adjust"] == 1
    assert data["settings"]["border"] == "#888888ff" and data["settings"]["magnify"] == 9
    frame = (tmp_path / "frame.txt").read_text().strip()
    assert frame.endswith(".ppm") and "grim -s 1 -t ppm" in log.read_text()


def test_overlay_refresh_and_fallback(tmp_path):
    viewer_log = tmp_path / "viewer.log"
    env = make_env(tmp_path, viewer_log)
    b, log = fake_tools(tmp_path)
    env["PATH"] = f"{b}:{env['PATH']}"
    env["SNIP_NO_ELEMENTS"] = "1"
    # first call asks for a refresh (exit 3) with a selection, the second gets it back and confirms
    body = (f'n=$(cat "{tmp_path}/n" 2>/dev/null || echo 0); echo $((n + 1)) > "{tmp_path}/n"; '
            f'cat > "{tmp_path}/input$n.json"; '
            'if [ "$n" = 0 ]; then printf "100x100+5+5"; exit 3; fi; printf "100x100+5+5"')
    env["SNIP_PIN_SELECT"] = fake_selector(tmp_path, body)
    assert run([], env).returncode == 0
    wait_for(viewer_log)
    import json
    assert json.loads((tmp_path / "input0.json").read_text())["select"] == ""
    assert json.loads((tmp_path / "input1.json").read_text())["select"] == "100x100+5+5"
    assert log.read_text().count("grim -s 1 -t ppm") == 2                       # a fresh frame for the retry
    # exit 127 (no layer shell): slurp takes over
    viewer_log.unlink()
    env["SNIP_PIN_SELECT"] = fake_selector(tmp_path, "exit 127")
    assert run([], env).returncode == 0
    wait_for(viewer_log)
    assert "slurp -b" in log.read_text()
    # an aborted overlay (exit 1) captures nothing
    viewer_log.unlink()
    env["SNIP_PIN_SELECT"] = fake_selector(tmp_path, "exit 1")
    assert run([], env).returncode == 0
    time.sleep(0.3)
    assert not viewer_log.exists()
