#!/usr/bin/env bash
# Installs the desktop entries and the icon so docks and taskbars show a proper
# icon for pins (they look it up by the window's app_id, "snip-pin") and file
# managers offer "Open with -> Pin image". Safe to rerun.
#   install.sh              install into $XDG_DATA_HOME (~/.local/share), translations included
#   install.sh --uninstall  remove what install.sh put there
set -e
HERE=$(dirname "$(readlink -f "$0")")
DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
ICON="$DATA/icons/hicolor/scalable/apps/snip-pin.svg"
ENTRY="$DATA/applications/snip-pin.desktop"
OPEN_ENTRY="$DATA/applications/snip-pin-open.desktop"

refresh() {
    if command -v update-desktop-database >/dev/null; then update-desktop-database -q "$DATA/applications" 2>/dev/null || true; fi
    if command -v gtk-update-icon-cache >/dev/null; then gtk-update-icon-cache -q -t "$DATA/icons/hicolor" 2>/dev/null || true; fi
}

if [[ "${1:-}" == "--uninstall" ]]; then
    rm -f "$ICON" "$ENTRY" "$OPEN_ENTRY" "$DATA"/locale/*/LC_MESSAGES/snip-pin.mo
    refresh
    echo "removed $ENTRY, $OPEN_ENTRY and the icon"
    exit 0
fi

# Exec is a quoted argument in desktop-entry syntax: backslash, double quote,
# dollar and backtick get a backslash, and the backslash itself is doubled
# once more by the general string escaping that applies first.
# shellcheck disable=SC2016  # the sed patterns are meant literally
exec_quote() { printf '"%s"' "$(printf '%s' "$1" | sed -e 's/\\/\\\\\\\\/g' -e 's/"/\\\\"/g' -e 's/\$/\\\\$/g' -e 's/`/\\\\`/g')"; }
SCRIPT=$(exec_quote "$HERE/snip-pin.sh")

install -Dm644 "$HERE/snip-pin.svg" "$ICON"
install -d "$DATA/applications"
cat > "$ENTRY" <<DESKTOP
[Desktop Entry]
Type=Application
Name=Snip & Pin
Comment=Snip a region of the screen and pin it on top
Exec=$SCRIPT
Icon=snip-pin
Terminal=false
Categories=Utility;GTK;
Keywords=screenshot;snip;pin;snipaste;
StartupWMClass=snip-pin
DESKTOP
# hidden from menus, offered by file managers for images
cat > "$OPEN_ENTRY" <<DESKTOP
[Desktop Entry]
Type=Application
Name=Pin image
Comment=Pin an image file on top of the screen
Exec=$SCRIPT pin %f
Icon=snip-pin
Terminal=false
NoDisplay=true
MimeType=image/png;image/jpeg;image/webp;image/bmp;image/gif;
StartupWMClass=snip-pin
DESKTOP
# translations: po/<lang>.po -> ~/.local/share/locale/<lang>/LC_MESSAGES/snip-pin.mo (needs gettext's msgfmt)
if command -v msgfmt >/dev/null; then
    for po in "$HERE"/po/*.po; do
        [[ -f "$po" ]] || continue
        lang=$(basename "$po" .po)
        install -d "$DATA/locale/$lang/LC_MESSAGES"
        msgfmt -o "$DATA/locale/$lang/LC_MESSAGES/snip-pin.mo" "$po" && echo "translation: $lang"
    done
else
    echo "msgfmt (gettext) not found: translations not installed"
fi
refresh
echo "installed $ENTRY and $OPEN_ENTRY"
echo
"$HERE/snip-pin.sh" doctor || true      # a missing tool is a hint here, not a failure to install
