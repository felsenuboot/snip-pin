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
# Snips are kept in ~/.cache/snip-pin for SNIP_PIN_KEEP_DAYS days (default 7,
# 0 = keep forever). The file name carries the capture position (_x<X>_y<Y>),
# so re-pinned snips land where they were taken.
# Testing hooks: SNIP_GEOM=WxH+X+Y skips the interactive selection,
# SNIP_NO_ELEMENTS=1 disables element snapping.

HERE=$(dirname "$(readlink -f "$0")")
VIEWER="$HERE/pin-view.py"
CACHE="${XDG_CACHE_HOME:-$HOME/.cache}/snip-pin"
KEEP="${SNIP_PIN_KEEP_DAYS:-7}"
mkdir -p "$CACHE"
[[ "$KEEP" -gt 0 ]] && find "$CACHE" -name '*.png' -mtime +"$KEEP" -delete 2>/dev/null

notify() { command -v notify-send >/dev/null && notify-send -i camera-photo-symbolic -t 2000 "Snip" "$1"; }

# pin a cached file; the position comes from its name when present
pin_file() {
    local file=$1 x y
    if [[ $(basename "$file") =~ _x(-?[0-9]+)_y(-?[0-9]+)\.png$ ]]; then
        x=${BASH_REMATCH[1]}; y=${BASH_REMATCH[2]}
        setsid -f "$VIEWER" "$file" "$x" "$y" >/dev/null 2>&1
    else
        setsid -f "$VIEWER" "$file" >/dev/null 2>&1
    fi
}

case "${1:-}" in
    last)
        file=$(ls -t "$CACHE"/*.png 2>/dev/null | head -1)
        [[ -z "$file" ]] && { notify "No snips in the cache"; exit 0; }
        pin_file "$file"
        exit 0 ;;
    history)
        exec "$HERE/pin-history.py" "$CACHE" "$VIEWER" ;;
    clipboard)
        if ! wl-paste --list-types 2>/dev/null | grep -qx 'image/png'; then
            notify "The clipboard holds no image"; exit 0
        fi
        file="$CACHE/$(date +%Y%m%d_%H%M%S_%N)_clipboard.png"
        wl-paste --type image/png > "$file" || exit 1
        pin_file "$file"
        exit 0 ;;
    "") ;;
    *)  echo "usage: snip-pin.sh [last|history|clipboard]" >&2; exit 2 ;;
esac

if [[ -n "$SNIP_GEOM" ]]; then
    geom=$SNIP_GEOM
else
    # Element snapping: grab the screen and look for rectangles (images, panels,
    # table cells) while the freeze and the window list are prepared. Needs
    # python-numpy; without it the helper fails quietly and only windows snap.
    elems=$(mktemp)
    if [[ -z "$SNIP_NO_ELEMENTS" ]]; then
        (grim -s 1 -t ppm - | "$HERE/snip-elements.py" > "$elems") 2>/dev/null &
        pid_detect=$!
    fi

    ws=$(hyprctl activeworkspace -j | jq '.id')
    rects=$(hyprctl clients -j | jq -r --argjson ws "$ws" '
        .[] | select(.workspace.id == $ws and .mapped and (.hidden | not))
            | "\(.at[0]),\(.at[1]) \(.size[0])x\(.size[1])"')

    # Right-click aborts the selection. slurp treats every mouse button alike,
    # so a temporary Hyprland bind swallows the press and ends slurp instead.
    # Hyprland with a Lua config rejects `keyword`; it takes `eval` instead.
    cleanup() {
        [[ -n "$pid_freeze" ]] && kill "$pid_freeze" 2>/dev/null
        rm -f "$elems"
        if [[ -n "$lua_cfg" ]]; then
            hyprctl eval 'hl.unbind("mouse:274")' >/dev/null 2>&1
        else
            hyprctl keyword unbind ", mouse:274" >/dev/null 2>&1
        fi
    }
    trap cleanup EXIT
    if hyprctl keyword bind ", mouse:274, exec, pkill -x slurp" 2>&1 | grep -q non-legacy; then
        lua_cfg=1
        hyprctl eval 'hl.bind("mouse:274", hl.dsp.exec_cmd("pkill -x slurp"))' >/dev/null 2>&1
    fi

    if command -v hyprpicker >/dev/null; then
        hyprpicker -r -z &
        pid_freeze=$!
        sleep 0.1
    fi
    [[ -n "$pid_detect" ]] && wait "$pid_detect"
    # slurp highlights the smallest rectangle under the pointer, so elements
    # inside a window win over the window itself
    geom=$(printf '%s\n' "$rects" | cat - "$elems" | slurp -b "#00000080" -c "#888888ff" -w 1 -f "%wx%h+%x+%y")
    rc=$?
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
