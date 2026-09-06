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
#   snip-pin.sh            select, capture, then what `action` says (default: copy+pin)
#   snip-pin.sh copy       select, capture, copy to the clipboard; no pin
#   snip-pin.sh save       select, capture, save to the screenshot folder; no pin
#   snip-pin.sh last       pin the newest cached snip again
#   snip-pin.sh history    thumbnail picker for cached snips
#   snip-pin.sh clipboard  pin the image in the clipboard (or its text, rendered as an image)
#   snip-pin.sh pin FILE   pin an image file, centred (used by the history picker)
#   snip-pin.sh screen     capture the monitor under the pointer, no selection
#   snip-pin.sh screen all capture every monitor as one image
#   snip-pin.sh repeat [N] capture the area of the N-th last snip again (default 1)
#   snip-pin.sh toggle     hide every pin, or show them all again where they were
#   snip-pin.sh close-all  close every pin (asks first when there are several)
#   snip-pin.sh clickthrough  toggle click-through for every pin (the mouse goes to what is below)
#   snip-pin.sh reopen     bring the last closed pin back as it was (position, zoom, annotations)
#   snip-pin.sh clear      empty the history (kept snips stay)
#   snip-pin.sh abort      end the selection this script started (right-click bind)
#   snip-pin.sh doctor     check the dependencies (exit 1 if a required one is missing)
#   snip-pin.sh config     print the effective settings and where each comes from
#   snip-pin.sh --version  print the version
# Pressing the snip key twice within tap_ms (default 300) aborts the
# selection the first press started and opens the history instead.
# Snips are kept in ~/.cache/snip-pin for keep_days days (default 7,
# 0 = keep forever); snips marked as kept in the picker live in the kept/
# subfolder and never expire. The file name carries the capture position
# (_x<X>_y<Y>), so `last` re-pins a snip where it was taken.
# Settings live in ~/.config/snip-pin/config (key = value lines, see the
# README); an environment variable SNIP_PIN_<KEY> overrides the file.
# Testing hooks: SNIP_GEOM=WxH+X+Y skips the interactive selection,
# SNIP_NO_ELEMENTS=1 disables element snapping.

set -u -o pipefail            # no -e: the abort paths rely on non-zero statuses
HERE=$(dirname "$(readlink -f "$0")")
VIEWER="${SNIP_PIN_VIEWER:-$HERE/pin-view.py}"       # override: tests
CACHE="${XDG_CACHE_HOME:-$HOME/.cache}/snip-pin"
STATE="${XDG_RUNTIME_DIR:-/tmp}/snip-pin"
CONFIG="${SNIP_PIN_CONFIG:-${XDG_CONFIG_HOME:-$HOME/.config}/snip-pin/config}"
SNIP_GEOM="${SNIP_GEOM:-}"
SNIP_NO_ELEMENTS="${SNIP_NO_ELEMENTS:-}"

# Settings: the config file fills in whatever the environment does not set.
# Only the top-level `key = value` lines are for this script; the [keys] and
# [mouse] sections belong to the viewer, which reads the file itself. Lines
# are parsed, never sourced, so a stray `$(...)` in the file runs nothing.
declare -A FROM_FILE=()
load_config() {
    local line key val section=''
    [[ -r "$CONFIG" ]] || return 0
    while IFS= read -r line || [[ -n "$line" ]]; do
        line="${line#"${line%%[![:space:]]*}"}"         # leading blanks
        [[ -z "$line" || "$line" == \#* ]] && continue    # blank or comment line
        if [[ "${line%%[[:space:]]#*}" =~ ^\[([a-z_]+)\][[:space:]]*$ ]]; then section=${BASH_REMATCH[1]}; continue; fi
        [[ -n "$section" ]] && continue
        [[ "$line" =~ ^([a-z][a-z0-9_]*)[[:space:]]*=[[:space:]]*(.*)$ ]] || continue
        key=${BASH_REMATCH[1]^^}; val=${BASH_REMATCH[2]}
        # a trailing comment is a `#` after a blank with a value before it, so
        # `border = #ff9f1c  # orange` keeps the colour and drops the note
        val="${val%%[[:space:]]#*}"; val="${val%"${val##*[![:space:]]}"}"
        [[ "$val" == "#" || "$val" == "# "* ]] && val=""
        [[ "$key" == ELEMENTS_BUDGET_MS ]] && key=SNIP_ELEMENTS_BUDGET_MS || key="SNIP_PIN_$key"
        FROM_FILE[$key]=$val
        [[ -z "${!key+x}" ]] && export "$key=$val"
    done < "$CONFIG"
}
load_config

# integer settings; anything else falls back to the default
int_or() { if [[ "${!1:-}" =~ ^[0-9]+$ ]]; then echo "${!1}"; else echo "$2"; fi; }
KEEP=$(int_or SNIP_PIN_KEEP_DAYS 7)
TAP_MS=$(int_or SNIP_PIN_TAP_MS 300)
# colour settings (#rrggbb or #rrggbbaa); anything else falls back to the default
color_or() { if [[ "${!1:-}" =~ ^#[0-9a-fA-F]{6}([0-9a-fA-F]{2})?$ ]]; then echo "${!1}"; else echo "$2"; fi; }
SEL_BORDER=$(color_or SNIP_PIN_SEL_BORDER '#888888ff')     # slurp: selection outline
SEL_WIDTH=$(int_or SNIP_PIN_SEL_WIDTH 1)                  #        outline width in px
SEL_MASK=$(color_or SNIP_PIN_SEL_MASK '#00000080')         #        dim over the rest of the screen
SEL_FILL=$(color_or SNIP_PIN_SEL_FILL '#00000000')         #        fill inside the selection
SEL_SIZE=$(int_or SNIP_PIN_SEL_SIZE 1)                    #        1 = show the size while dragging
CURSOR=$(int_or SNIP_PIN_CURSOR 0)                        # 1 = include the mouse cursor in the capture
AREAS=$(int_or SNIP_PIN_AREAS 8)                          # how many capture areas `repeat` remembers
FILENAME="${SNIP_PIN_FILENAME:-}"                         # strftime pattern for saved files
[[ -n "$FILENAME" && "$FILENAME" != */* ]] || FILENAME='pin_%Y%m%d_%H%M%S'
case "${SNIP_PIN_FORMAT:-png}" in jpg|jpeg|.jpg|.jpeg) FORMAT=jpeg; EXT=jpg ;; webp|.webp) FORMAT=webp; EXT=webp ;; *) FORMAT=png; EXT=png ;; esac
QUALITY=$(int_or SNIP_PIN_QUALITY 90)
# what a capture ends with: any combination of copy, save and pin joined by +
if [[ "${SNIP_PIN_ACTION:-}" =~ ^(copy|save|pin)(\+(copy|save|pin))*$ ]]; then ACTION=$SNIP_PIN_ACTION; else ACTION="copy+pin"; fi
MODE=$ACTION

mkdir -p "$CACHE/kept" "$STATE"
[[ "$KEEP" -gt 0 ]] && find "$CACHE" -maxdepth 1 -name '*.png' -mtime +"$KEEP" -delete 2>/dev/null
# baked copies that versions before 0.1.0 left next to the original
find "$CACHE" -maxdepth 2 -name '*_annotated.png' -delete 2>/dev/null

notify() { command -v notify-send >/dev/null && notify-send -i camera-photo-symbolic -t 2000 "Snip" "$1"; }
# `sound = default` plays the theme's screen-capture event, a path plays that file (copy / save without a pin)
play_sound() {
    local v="${SNIP_PIN_SOUND:-}"
    case "${v,,}" in ""|0|off|no|false|none) return 0 ;; esac
    if command -v canberra-gtk-play >/dev/null; then
        if [[ "$v" == default ]]; then canberra-gtk-play -i screen-capture >/dev/null 2>&1 &
        else canberra-gtk-play -f "${v/#\~/$HOME}" >/dev/null 2>&1 & fi
    else
        local player
        player=$(command -v pw-play || command -v paplay) || return 0
        [[ "$v" == default ]] && v=/usr/share/sounds/freedesktop/stereo/screen-capture.oga
        v="${v/#\~/$HOME}"
        [[ -r "$v" ]] && "$player" "$v" >/dev/null 2>&1 &
    fi
    return 0
}

# End our own slurp (its PID is in the state file), never somebody else's.
abort_selection() {
    local pid
    [[ -r "$STATE/slurp" ]] && read -r pid < "$STATE/slurp" || return 1
    [[ "$pid" =~ ^[0-9]+$ && "$(cat "/proc/$pid/comm" 2>/dev/null)" == slurp ]] || return 1
    kill "$pid" 2>/dev/null
}

# Where `save` puts the file: save_dir (~ and $VARS expanded), the ML4W
# setting, xdg-user-dir PICTURES, ~/Pictures. Same order as the viewer.
save_dir() {
    local d="${SNIP_PIN_SAVE_DIR:-}" name
    [[ -z "$d" && -r "$HOME/.config/ml4w/settings/screenshot-folder" ]] && read -r d < "$HOME/.config/ml4w/settings/screenshot-folder"
    if [[ -n "$d" ]]; then
        d="${d/#\~/$HOME}"
        while [[ "$d" =~ \$\{?([A-Za-z_][A-Za-z0-9_]*)\}? ]]; do    # expand $VAR and ${VAR}, unset -> empty
            name=${BASH_REMATCH[1]}; d="${d/"${BASH_REMATCH[0]}"/${!name:-}}"
        done
        echo "$d"; return
    fi
    d=$(xdg-user-dir PICTURES 2>/dev/null)
    echo "${d:-$HOME/Pictures}"
}

# Copy a capture into a folder (default: the screenshot folder) under a
# unique name; prints the path.
quick_save() {
    local dir=${2:-} dest n=2
    [[ -n "$dir" ]] || dir=$(save_dir)
    mkdir -p "$dir" || return 1
    local stem; stem="$dir/$(date +"$FILENAME")"
    dest="$stem.$EXT"
    while [[ -e "$dest" ]]; do dest="${stem}_$n.$EXT"; n=$((n + 1)); done
    if [[ "$FORMAT" == png ]]; then
        cp "$1" "$dest" || return 1
    else
        python3 - "$1" "$dest" "$FORMAT" "$QUALITY" <<'PY' || return 1
import sys, warnings
warnings.filterwarnings("ignore")
import gi
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf
pb = GdkPixbuf.Pixbuf.new_from_file(sys.argv[1])
if sys.argv[3] == "jpeg" and pb.get_has_alpha():          # JPEG has no alpha: flatten on white
    flat = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, pb.get_width(), pb.get_height())
    flat.fill(0xffffffff)
    pb.composite(flat, 0, 0, pb.get_width(), pb.get_height(), 0, 0, 1.0, 1.0, GdkPixbuf.InterpType.NEAREST, 255)
    pb = flat
pb.savev(sys.argv[2], sys.argv[3], ["quality"], [sys.argv[4]])
PY
    fi
    echo "$dest"
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
        # PNG when offered, else the first image type GdkPixbuf can read, else
        # a copied local image file (text/uri-list); everything lands in the
        # cache as PNG so the history, `last` and the export treat it alike.
        types=$(wl-paste --list-types 2>/dev/null)
        mime=$(grep -m1 -x 'image/png' <<< "$types" || grep -m1 -E '^image/(jpeg|webp|bmp|gif|tiff|x-portable-pixmap)$' <<< "$types")
        src=""
        if [[ -n "$mime" ]]; then
            src=$(mktemp)
            wl-paste --type "$mime" > "$src" || { rm -f "$src"; exit 1; }
        elif grep -qx 'text/uri-list' <<< "$types"; then
            uri=$(wl-paste --type text/uri-list 2>/dev/null | head -1 | tr -d '\r')
            # file:///a/b%20c -> /a/b c (a back-reference: parameter expansion cannot do it)
            # shellcheck disable=SC2001
            [[ "$uri" == file://* ]] && src=$(printf '%b' "$(sed 's/%\([0-9A-Fa-f]\{2\}\)/\\x\1/g' <<< "${uri#file://}")")
            [[ -f "$src" ]] || { notify "The clipboard holds no image"; exit 0; }
        elif ttype=$(grep -m1 -E '^text/plain' <<< "$types") && text=$(wl-paste --type "$ttype" 2>/dev/null) \
             && [[ -n "${text//[[:space:]]/}" ]]; then
            # Text to image: a copied error message, snippet or URL becomes a pin.
            # A single line naming an image file pins that file instead.
            line="${text#"${text%%[![:space:]]*}"}"; line="${line%"${line##*[![:space:]]}"}"
            if [[ "$line" != *$'\n'* && -f "${line/#\~/$HOME}" && "${line,,}" =~ \.(png|jpe?g|webp|bmp|gif|tiff?)$ ]]; then
                src="${line/#\~/$HOME}"
            else
                tmp=$(mktemp); printf '%s' "$text" > "$tmp"
                file="$CACHE/$(date +%Y%m%d_%H%M%S_%N)_text.png"
                if "$HERE/pin-view.py" --render-text "$tmp" "$file" "${SNIP_PIN_TEXT_FONT:-Sans 11}" \
                        "$(int_or SNIP_PIN_TEXT_WIDTH 900)" "$(int_or SNIP_PIN_TEXT_MARGIN 15)" \
                        "$(color_or SNIP_PIN_TEXT_FG '#000000')" "$(color_or SNIP_PIN_TEXT_BG '#ffffff')"; then
                    rm -f "$tmp"; pin_file "$file"; exit 0
                fi
                rm -f "$tmp"; notify "The clipboard text cannot be rendered"; exit 1
            fi
        else
            notify "The clipboard holds no image or text"; exit 0
        fi
        file="$CACHE/$(date +%Y%m%d_%H%M%S_%N)_clipboard.png"
        if [[ "$(head -c 8 "$src" | od -An -tx1 | tr -d ' \n')" == 89504e470d0a1a0a ]]; then
            if [[ -n "$mime" ]]; then mv "$src" "$file"; else cp "$src" "$file"; fi
        else
            python3 - "$src" "$file" <<'PY' || { notify "The clipboard image cannot be read"; [[ -n "$mime" ]] && rm -f "$src"; exit 1; }
import sys, warnings
warnings.filterwarnings("ignore")
import gi
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf
GdkPixbuf.Pixbuf.new_from_file(sys.argv[1]).savev(sys.argv[2], "png", [], [])
PY
            [[ -n "$mime" ]] && rm -f "$src"
        fi
        pin_file "$file"
        exit 0 ;;
    screen)
        # The monitor under the pointer (logical size: scale and rotation
        # applied), or the bounding box of every monitor for `screen all`.
        # The capture then runs below exactly like a selected region.
        read -r cx cy < <(hyprctl cursorpos -j 2>/dev/null | jq -r '"\(.x) \(.y)"' 2>/dev/null)
        [[ "${cx:-}" =~ ^-?[0-9]+$ ]] || { cx=0; cy=0; }
        SNIP_GEOM=$(hyprctl monitors -j | jq -r --argjson cx "$cx" --argjson cy "$cy" --arg all "${2:-}" '
            map({x, y, w: (if (.transform % 2) == 1 then .height / .scale else .width / .scale end),
                        h: (if (.transform % 2) == 1 then .width / .scale else .height / .scale end)})
            | if $all == "all" then
                {x: (map(.x) | min), y: (map(.y) | min), r: (map(.x + .w) | max), b: (map(.y + .h) | max)}
                | "\(.r - .x | floor)x\(.b - .y | floor)+\(.x)+\(.y)"
              else
                ((map(select(.x <= $cx and $cx < .x + .w and .y <= $cy and $cy < .y + .h)) | first) // first)
                | "\(.w | floor)x\(.h | floor)+\(.x)+\(.y)"
              end')
        [[ "$SNIP_GEOM" =~ ^[0-9]+x[0-9]+\+-?[0-9]+\+-?[0-9]+$ ]] || { notify "No monitor found"; exit 1; } ;;
    repeat)
        # the N-th most recent capture area (newest first in $CACHE/areas)
        n=${2:-1}
        [[ "$n" =~ ^[1-9][0-9]*$ ]] || { echo "usage: snip-pin.sh repeat [N]" >&2; exit 1; }
        SNIP_GEOM=$(sed -n "${n}p" "$CACHE/areas" 2>/dev/null)
        [[ "$SNIP_GEOM" =~ ^[0-9]+x[0-9]+\+-?[0-9]+\+-?[0-9]+$ ]] || { notify "No previous area to repeat"; exit 0; } ;;
    ""|copy|save)
        [[ -n "${1:-}" ]] && MODE=$1
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
    toggle|close-all|clickthrough|reopen)
        # a request to the running viewer; without one only reopen starts a new one
        cmd=$1; [[ "$cmd" == clickthrough ]] && cmd=click-through
        if [[ "$cmd" == reopen ]]; then setsid -f "$VIEWER" --reopen >/dev/null 2>&1; exit 0; fi
        exec "$VIEWER" "--$cmd" ;;
    doctor)
        missing=0
        check() {   # name, required|optional, what it is for
            local p; p=$(command -v "$1" 2>/dev/null)
            if [[ -n "$p" ]]; then printf '  %-12s %s\n' "$1" "$p"
            else printf '  %-12s MISSING  (%s)\n' "$1" "$3"; [[ "$2" == required ]] && missing=1; fi
        }
        echo "snip-pin $(cat "$HERE/VERSION") in $HERE"
        echo "required:"
        check grim required "captures the screen"
        check slurp required "region selection"
        check wl-copy required "clipboard (wl-clipboard)"
        check wl-paste required "clipboard (wl-clipboard)"
        check jq required "reads hyprctl's JSON"
        check hyprctl required "window list and placement (Hyprland)"
        check python3 required "the viewer and the picker"
        if python3 -c 'import gi; gi.require_version("Gtk", "4.0"); from gi.repository import Gtk' 2>/dev/null; then
            printf '  %-12s %s\n' "GTK 4" "$(python3 -c 'import gi; gi.require_version("Gtk", "4.0"); from gi.repository import Gtk; print(f"{Gtk.get_major_version()}.{Gtk.get_minor_version()}.{Gtk.get_micro_version()}")' 2>/dev/null)"
        else
            printf '  %-12s MISSING  (python-gobject and gtk4)\n' "GTK 4"; missing=1
        fi
        echo "optional:"
        check hyprpicker optional "freezes the screen during the selection"
        check notify-send optional "toasts (libnotify)"
        if python3 -c 'import numpy' 2>/dev/null; then printf '  %-12s %s\n' "numpy" "$(python3 -c 'import numpy; print(numpy.__version__)')"
        else printf '  %-12s MISSING  (element snapping; windows still snap)\n' "numpy"; fi
        if command -v hyprctl >/dev/null && tag=$(hyprctl version -j 2>/dev/null | jq -r '.tag // .version' 2>/dev/null) && [[ -n "$tag" ]]; then
            # `eval` exists only with a Lua config (it answers "ok"); a classic config rejects it
            if [[ "$(hyprctl eval 'return 1' 2>/dev/null)" == ok ]]; then style="Lua config"; else style="classic config"; fi
            echo "hyprland:    $tag, $style"
        else
            echo "hyprland:    not running (or hyprctl missing)"
        fi
        [[ $missing -eq 0 ]] && echo "all required tools found" || echo "required tools missing" >&2
        exit $missing ;;
    config)
        # every setting with its effective value and where it comes from
        if [[ -r "$CONFIG" ]]; then echo "config file: $CONFIG"; else echo "config file: $CONFIG (not found, defaults)"; fi
        show() {   # key, default
            local var=SNIP_PIN_${1^^} src val
            [[ "$1" == elements_budget_ms ]] && var=SNIP_ELEMENTS_BUDGET_MS
            if [[ -n "${!var+x}" && -n "${FROM_FILE[$var]+x}" && "${!var}" == "${FROM_FILE[$var]}" ]]; then src="file"
            elif [[ -n "${!var+x}" ]]; then src="environment"
            else src="default"; fi
            val="${!var-$2}"
            printf '  %-20s = %-28s (%s)\n' "$1" "${val:-<unset>}" "$src"
        }
        show keep_days 7
        show tap_ms 300
        show border '#ff9f1c'
        show save_dir ''
        show elements_budget_ms 150
        show sel_border '#888888ff'
        show sel_width 1
        show sel_mask '#00000080'
        show sel_fill '#00000000'
        show sel_size 1
        show cursor 0
        show areas 8
        show action copy+pin
        show autosave_dir ''
        show default_tool none
        show confirm_close_all 1
        show zoom_at_pointer 1
        show smooth 1
        show reopen 5
        show opacity 100
        show alpha_bg transparent
        show thumb_size 75
        show filename 'pin_%Y%m%d_%H%M%S'
        show format png
        show quality 90
        show copy_file always
        show sound off
        show palette 'default (7 colours)'
        show widths '2,4,7'
        show tool_colors 1
        show text_font 'Sans 11'
        show text_width 900
        show text_margin 15
        show text_fg '#000000'
        show text_bg '#ffffff'
        exit 0 ;;
    --version|-V)
        cat "$HERE/VERSION"; exit 0 ;;
    *)  echo "usage: snip-pin.sh [copy|save|last|history|clipboard|pin FILE|screen [all]|repeat [N]|toggle|close-all|clickthrough|reopen|clear|doctor|config|--version]" >&2
        echo "  (no argument: select a region, capture it, then copy and pin it, or what \`action\` says)" >&2; exit 2 ;;
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
    slurp_opts=(-b "$SEL_MASK" -c "$SEL_BORDER" -s "$SEL_FILL" -w "$SEL_WIDTH")
    [[ "$SEL_SIZE" -ne 0 ]] && slurp_opts+=(-d)
    printf '%s\n' "$rects" | cat - "$elems" | slurp "${slurp_opts[@]}" -f "%wx%h+%x+%y" > "$geom_file" &
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

# remember the area for `repeat`: newest first, no duplicates, the last AREAS
if [[ "$AREAS" -gt 0 ]]; then
    { echo "$geom"; grep -vx -- "$geom" "$CACHE/areas" 2>/dev/null || true; } | head -n "$AREAS" > "$CACHE/areas.new"
    mv -f "$CACHE/areas.new" "$CACHE/areas"
fi

file="$CACHE/$(date +%Y%m%d_%H%M%S_%N)_x${X}_y${Y}.png"
grim_opts=(-l 1)
[[ "$CURSOR" -ne 0 ]] && grim_opts+=(-c)
grim -g "${X},${Y} ${W}x${H}" "${grim_opts[@]}" "$file" || exit 1
# autosave_dir: every capture also lands there, whatever happens to the pin
if [[ -n "${SNIP_PIN_AUTOSAVE_DIR:-}" ]]; then
    autosave=$(SNIP_PIN_SAVE_DIR=$SNIP_PIN_AUTOSAVE_DIR save_dir)
    quick_save "$file" "$autosave" >/dev/null || notify "Cannot autosave to $autosave"
fi
if [[ "$MODE" == *copy* ]]; then
    wl-copy --type image/png < "$file"
    [[ "$MODE" == *pin* ]] || { notify "Copied to clipboard"; play_sound; }
fi
if [[ "$MODE" == *save* ]]; then
    if dest=$(quick_save "$file"); then notify "Saved $dest"; play_sound; else notify "Cannot save to $(save_dir)"; fi
fi
[[ "$MODE" == *pin* ]] && pin_file "$file"
exit 0
