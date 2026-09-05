#!/usr/bin/env bash
# snip-pin: Snipaste-style snip & pin for Hyprland.
#   1. freeze the screen, select a region with slurp; hovering a window or an
#      element inside it (image, panel, table cell; found by snip-elements.py)
#      highlights it and a single click snaps to it, drag for a free region,
#      right-click or Esc aborts
#   2. grim captures the region; a copy goes to the clipboard
#   3. pin-view.py shows it pinned on top, exactly where it was captured
# Pin controls: drag = move, wheel = zoom, Ctrl+wheel = opacity, Ctrl+C copy,
# Ctrl+S save, middle-click menu, Esc = close, double-/right-click = copy & close.
#
# Subcommands (all bindable):
#   snip-pin.sh            select, capture, pin (default)
#   snip-pin.sh last       pin the newest cached snip again
#   snip-pin.sh history    thumbnail picker for cached snips
#   snip-pin.sh clipboard  pin the image in the clipboard
#   snip-pin.sh pin FILE   pin an image file, centred (used by the history picker)
#   snip-pin.sh clear      empty the history (kept snips stay)
#   snip-pin.sh abort      end the selection this script started (right-click bind)
#   snip-pin.sh --version  print the version
# Pressing the snip key twice within SNIP_PIN_TAP_MS (default 300) aborts the
# selection the first press started and opens the history instead.
# Snips are kept in ~/.cache/snip-pin for SNIP_PIN_KEEP_DAYS days (default 7,
# 0 = keep forever); snips marked as kept in the picker live in the kept/
# subfolder and never expire. The file name carries the capture position
# (_x<X>_y<Y>), so `last` re-pins a snip where it was taken.
# Testing hooks: SNIP_GEOM=WxH+X+Y skips the interactive selection,
# SNIP_NO_ELEMENTS=1 disables element snapping.

set -u -o pipefail            # no -e: the abort paths rely on non-zero statuses
HERE=$(dirname "$(readlink -f "$0")")
VIEWER="${SNIP_PIN_VIEWER:-$HERE/pin-view.py}"       # override: tests
CACHE="${XDG_CACHE_HOME:-$HOME/.cache}/snip-pin"
STATE="${XDG_RUNTIME_DIR:-/tmp}/snip-pin"
SNIP_GEOM="${SNIP_GEOM:-}"
SNIP_NO_ELEMENTS="${SNIP_NO_ELEMENTS:-}"

# integer settings from the environment; anything else falls back to the default
int_or() { if [[ "${!1:-}" =~ ^[0-9]+$ ]]; then echo "${!1}"; else echo "$2"; fi; }
KEEP=$(int_or SNIP_PIN_KEEP_DAYS 7)
TAP_MS=$(int_or SNIP_PIN_TAP_MS 300)

mkdir -p "$CACHE/kept" "$STATE"
[[ "$KEEP" -gt 0 ]] && find "$CACHE" -maxdepth 1 -name '*.png' -mtime +"$KEEP" -delete 2>/dev/null
# baked copies that versions before 0.1.0 left next to the original
find "$CACHE" -maxdepth 2 -name '*_annotated.png' -delete 2>/dev/null

notify() { command -v notify-send >/dev/null && notify-send -i camera-photo-symbolic -t 2000 "Snip" "$1"; }

# End our own slurp (its PID is in the state file), never somebody else's.
abort_selection() {
    local pid
    [[ -r "$STATE/slurp" ]] && read -r pid < "$STATE/slurp" || return 1
    [[ "$pid" =~ ^[0-9]+$ && "$(cat "/proc/$pid/comm" 2>/dev/null)" == slurp ]] || return 1
    kill "$pid" 2>/dev/null
}

# Pin a cached file; the position comes from its name when present. All pins
# live in one pin-view.py process: a running one takes the file over a socket
# (see pin-view.py), so this returns in a few milliseconds after the first.
pin_file() {
    local file=$1 args=("$1")
    if [[ $(basename "$file") =~ _x(-?[0-9]+)_y(-?[0-9]+)\.png$ ]]; then
        args+=("${BASH_REMATCH[1]}" "${BASH_REMATCH[2]}")
    fi
    setsid -f "$VIEWER" "${args[@]}" >/dev/null 2>&1
}

case "${1:-}" in
    last)
        file=$(find "$CACHE" -maxdepth 2 -name '*.png' -printf '%T@ %p\n' | sort -rn | head -1 | cut -d' ' -f2-)
        [[ -z "$file" ]] && { notify "No snips in the cache"; exit 0; }
        pin_file "$file"
        exit 0 ;;
    history)
        exec "$HERE/pin-history.py" "$CACHE" "$0" ;;
    pin)
        [[ -f "${2:-}" ]] || { echo "usage: snip-pin.sh pin FILE" >&2; exit 1; }
        setsid -f "$VIEWER" "$2" >/dev/null 2>&1
        exit 0 ;;
    clear)
        n=$(find "$CACHE" -maxdepth 1 -name '*.png' -print -delete | wc -l)
        notify "History cleared ($n snips removed)"
        exit 0 ;;
    clipboard)
        if ! wl-paste --list-types 2>/dev/null | grep -qx 'image/png'; then
            notify "The clipboard holds no image"; exit 0
        fi
        file="$CACHE/$(date +%Y%m%d_%H%M%S_%N)_clipboard.png"
        wl-paste --type image/png > "$file" || exit 1
        pin_file "$file"
        exit 0 ;;
    "")
        # Double tap: if another instance started a selection less than TAP_MS
        # ago, tell it to abort (flag file, and end slurp if it is already up)
        # and open the history instead.
        if [[ -z "$SNIP_GEOM" ]]; then
            now=$(date +%s%3N)
            other='' started=''
            if [[ -r "$STATE/selecting" ]] && read -r other started < "$STATE/selecting" \
               && [[ "$other" =~ ^[0-9]+$ && "$started" =~ ^[0-9]+$ ]] \
               && kill -0 "$other" 2>/dev/null && (( now - started < TAP_MS )); then
                touch "$STATE/abort"
                abort_selection; sleep 0.05; abort_selection     # slurp may start in between
                exec "$0" history
            fi
            rm -f "$STATE/abort"
            echo "$$ $now" > "$STATE/selecting"
        fi ;;
    abort)
        abort_selection; exit 0 ;;
    --version|-V)
        cat "$HERE/VERSION"; exit 0 ;;
    *)  echo "usage: snip-pin.sh [last|history|clipboard|pin FILE|clear|--version]" >&2
        echo "  (no argument: select a region, capture it, pin it)" >&2; exit 2 ;;
esac

if [[ -n "$SNIP_GEOM" ]]; then
    geom=$SNIP_GEOM
else
    # Element snapping: grab the screen and look for rectangles (images, panels,
    # table cells) while the freeze and the window list are prepared. Needs
    # python-numpy; without it the helper fails quietly and only windows snap.
    # The detector bounds its own work (about 150 ms); the timeout is the
    # backstop so a stuck helper can never hold the frozen screen.
    elems='' pid_detect='' pid_freeze='' lua_cfg=''
    cleanup() {
        [[ -n "$pid_freeze" ]] && kill "$pid_freeze" 2>/dev/null
        rm -f "$elems" "$STATE/selecting" "$STATE/abort" "$STATE/slurp"
        if [[ -n "$lua_cfg" ]]; then
            hyprctl eval 'hl.unbind("mouse:274")' >/dev/null 2>&1
        else
            hyprctl keyword unbind ", mouse:274" >/dev/null 2>&1
        fi
    }
    trap cleanup EXIT
    elems=$(mktemp)
    if [[ -z "$SNIP_NO_ELEMENTS" ]]; then
        (grim -s 1 -t ppm - | timeout 0.6 "$HERE/snip-elements.py" > "$elems") 2>/dev/null &
        pid_detect=$!
    fi

    # Windows on every monitor's visible workspace (plus an open special
    # workspace), since slurp spans all outputs; `activeworkspace` alone would
    # cover only the focused monitor.
    ids=$(hyprctl monitors -j | jq -c '[.[] | .activeWorkspace.id, .specialWorkspace.id] | map(select(. != 0))')
    rects=$(hyprctl clients -j | jq -r --argjson ids "$ids" '
        .[] | select((.workspace.id as $w | $ids | index($w)) != null and .mapped and (.hidden | not))
            | "\(.at[0]),\(.at[1]) \(.size[0])x\(.size[1])"')

    # Right-click aborts the selection. slurp treats every mouse button alike,
    # so a temporary Hyprland bind swallows the press and runs `abort`, which
    # ends our slurp by PID (not every slurp on the system).
    # Hyprland with a Lua config rejects `keyword`; it takes `eval` instead.
    abort_cmd="$HERE/snip-pin.sh abort"
    if hyprctl keyword bind ", mouse:274, exec, $abort_cmd" 2>&1 | grep -q non-legacy; then
        lua_cfg=1
        hyprctl eval "hl.bind(\"mouse:274\", hl.dsp.exec_cmd(\"$abort_cmd\"))" >/dev/null 2>&1
    fi

    if command -v hyprpicker >/dev/null; then
        hyprpicker -r -z &
        pid_freeze=$!
        sleep 0.1
    fi
    [[ -n "$pid_detect" ]] && wait "$pid_detect"
    [[ -e "$STATE/abort" ]] && exit 0            # second tap arrived meanwhile
    # slurp highlights the smallest rectangle under the pointer, so elements
    # inside a window win over the window itself. It runs in the background so
    # its PID can be recorded for `abort`.
    geom_file=$(mktemp)
    printf '%s\n' "$rects" | cat - "$elems" | slurp -b "#00000080" -c "#888888ff" -w 1 -f "%wx%h+%x+%y" > "$geom_file" &
    echo $! > "$STATE/slurp"
    wait $!
    rc=$?
    geom=$(<"$geom_file")
    rm -f "$geom_file"
    cleanup
    trap - EXIT
    [[ $rc -ne 0 || -z "$geom" ]] && exit 0
fi

IFS='x+' read -r W H X Y <<< "$geom"
[[ "$W" -lt 1 || "$H" -lt 1 ]] && exit 0

file="$CACHE/$(date +%Y%m%d_%H%M%S_%N)_x${X}_y${Y}.png"
grim -g "${X},${Y} ${W}x${H}" -l 1 "$file" || exit 1
wl-copy --type image/png < "$file"
pin_file "$file"
