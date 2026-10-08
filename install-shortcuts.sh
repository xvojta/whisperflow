#!/usr/bin/env bash
# Nastaví GNOME zkratky: Super+H = toggle, Super+Shift+H = zrušit.
set -euo pipefail
SCRIPT="$(cd "$(dirname "$0")" && pwd)/whisperflow.py"
SCHEMA=org.gnome.settings-daemon.plugins.media-keys
BASE=/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings

# Super+H je v GNOME standardně „minimalizovat okno“ – uvolnit
gsettings set org.gnome.desktop.wm.keybindings minimize "[]"

add_binding() {  # id name command binding
  local path="$BASE/$1/"
  local list; list=$(gsettings get $SCHEMA custom-keybindings)
  if [[ "$list" != *"$path"* ]]; then
    if [[ "$list" == "@as []" || "$list" == "[]" ]]; then list="['$path']"
    else list="${list%]}, '$path']"; fi
    gsettings set $SCHEMA custom-keybindings "$list"
  fi
  local s="$SCHEMA.custom-keybinding:$path"
  gsettings set "$s" name "$2"
  gsettings set "$s" command "$3"
  gsettings set "$s" binding "$4"
}

add_binding whisperflow-toggle "Whisperflow toggle" "$SCRIPT toggle" "<Super>h"
add_binding whisperflow-cancel "Whisperflow cancel" "$SCRIPT cancel" "<Super><Shift>h"
echo "Hotovo: $(gsettings get $SCHEMA custom-keybindings)"
