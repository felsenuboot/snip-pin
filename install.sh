#!/usr/bin/env bash
# Installs the desktop entry and icon so docks and taskbars show a proper icon
# for pins (they look it up by the window's app_id, "snip-pin"). Safe to rerun.
set -e
HERE=$(dirname "$(readlink -f "$0")")
DATA="${XDG_DATA_HOME:-$HOME/.local/share}"

install -Dm644 "$HERE/snip-pin.svg" "$DATA/icons/hicolor/scalable/apps/snip-pin.svg"
install -d "$DATA/applications"
cat > "$DATA/applications/snip-pin.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=Snip & Pin
Comment=Snip a region of the screen and pin it on top
Exec=$HERE/snip-pin.sh
Icon=snip-pin
Terminal=false
Categories=Utility;Graphics;GTK;
Keywords=screenshot;snip;pin;snipaste;
StartupWMClass=snip-pin
DESKTOP

if command -v update-desktop-database >/dev/null; then update-desktop-database "$DATA/applications"; fi
if command -v gtk-update-icon-cache >/dev/null; then gtk-update-icon-cache -q -t "$DATA/icons/hicolor" 2>/dev/null; fi
echo "installed $DATA/applications/snip-pin.desktop"
