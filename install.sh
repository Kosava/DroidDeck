#!/bin/sh
# Dodaje DroidDeck u meni aplikacija (LXQt i ostali) sa ikonicom.
# Pokreni jednom iz foldera projekta:   sh install.sh
# Uklanjanje iz menija:                 sh install.sh --ukloni
set -e

DIR="$(cd "$(dirname "$0")" && pwd)"
ICON_DIR="$HOME/.local/share/icons/hicolor/256x256/apps"
APP_DIR="$HOME/.local/share/applications"

if [ "$1" = "--ukloni" ]; then
    rm -f "$APP_DIR/droiddeck.desktop" "$ICON_DIR/droiddeck.png"
    update-desktop-database "$APP_DIR" 2>/dev/null || true
    echo "DroidDeck je uklonjen iz menija (podešavanja u ~/.config/DroidDeck su ostala)."
    exit 0
fi

mkdir -p "$ICON_DIR" "$APP_DIR"

# ikonicu crta sama aplikacija; putanju prosleđujemo kroz okruženje,
# ne ubacujemo je u Python kod (radi i sa navodnicima/razmacima u putanji)
cd "$DIR"
ICON_OUT="$ICON_DIR/droiddeck.png" QT_QPA_PLATFORM=offscreen python3 -B -c '
import os
from PyQt6.QtWidgets import QApplication
app = QApplication([])
from ui.icon import _draw
_draw(256, None).save(os.environ["ICON_OUT"])
'

# Exec po .desktop specifikaciji: putanja u navodnicima; \ " ` $ dobijaju
# dvostruki escape (string + quoting pravilo), a % se piše kao %%
ESC_DIR=$(printf '%s' "$DIR" | sed -e 's/[\\"`$]/\\\\&/g' -e 's/%/%%/g')
# Path je obična string vrednost: samo \ se escape-uje
PATH_DIR=$(printf '%s' "$DIR" | sed 's/\\/\\\\/g')

cat > "$APP_DIR/droiddeck.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=DroidDeck
Comment=Android aplikacije i igre preko Waydroid-a
Exec=python3 "$ESC_DIR/main.py"
Path=$PATH_DIR
Icon=droiddeck
Terminal=false
Categories=Utility;Game;
StartupWMClass=DroidDeck
EOF

update-desktop-database "$APP_DIR" 2>/dev/null || true
echo "DroidDeck je dodat u meni aplikacija."
