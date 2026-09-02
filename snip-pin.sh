#!/usr/bin/env bash
# snip-pin: Snipaste-style snip & pin for Hyprland.
#   1. freeze the screen, select a region with slurp; hovering a window highlights
#      it and a single click snaps to the whole window, drag for a free region
#   2. grim captures the region; a copy goes to the clipboard
#   3. pin-view.py shows it pinned on top, exactly where it was captured
# Pin controls: drag = move, wheel = zoom, Ctrl+wheel = opacity, Ctrl+C copy,
# Ctrl+S save, right-click menu, Esc / double-click = close.
# Testing hook: SNIP_GEOM=WxH+X+Y skips the interactive selection.

HERE=$(dirname "$(readlink -f "$0")")
VIEWER="$HERE/pin-view.py"
CACHE="${XDG_CACHE_HOME:-$HOME/.cache}/snip-pin"
mkdir -p "$CACHE"
find "$CACHE" -name '*.png' -mtime +7 -delete 2>/dev/null

if [[ -n "$SNIP_GEOM" ]]; then
    geom=$SNIP_GEOM
else
    ws=$(hyprctl activeworkspace -j | jq '.id')
    rects=$(hyprctl clients -j | jq -r --argjson ws "$ws" '
        .[] | select(.workspace.id == $ws and .mapped and (.hidden | not))
            | "\(.at[0]),\(.at[1]) \(.size[0])x\(.size[1])"')

    if command -v hyprpicker >/dev/null; then
        hyprpicker -r -z &
        pid_freeze=$!
        trap 'kill "$pid_freeze" 2>/dev/null' EXIT
        sleep 0.1
    fi
    geom=$(printf '%s\n' "$rects" | slurp -b "#00000080" -c "#888888ff" -w 1 -f "%wx%h+%x+%y")
    rc=$?
    [[ -n "$pid_freeze" ]] && kill "$pid_freeze" 2>/dev/null
    trap - EXIT
    [[ $rc -ne 0 || -z "$geom" ]] && exit 0
fi

IFS='x+' read -r W H X Y <<< "$geom"
[[ "$W" -lt 1 || "$H" -lt 1 ]] && exit 0

file="$CACHE/$(date +%Y%m%d_%H%M%S_%N).png"
grim -g "${X},${Y} ${W}x${H}" -l 1 "$file" || exit 1
wl-copy --type image/png < "$file"
setsid -f "$VIEWER" "$file" "$X" "$Y" >/dev/null 2>&1
