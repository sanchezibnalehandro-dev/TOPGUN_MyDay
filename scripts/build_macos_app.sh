#!/usr/bin/env bash
# Сборка выполняется только на macOS: PyInstaller не собирает проверяемый .app из Windows.
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3.12}"
VENV_DIR="$PROJECT_DIR/.build-venv-macos"
BUILD_DIR="$PROJECT_DIR/.build-macos"
DIST_DIR="$PROJECT_DIR/dist-macos"
ICONSET_DIR="$BUILD_DIR/topgun_myday.iconset"
ICON_PATH="$BUILD_DIR/topgun_myday.icns"
APP_NAME="TOPGUN Мой день"

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "Не найден $PYTHON_BIN. Установите Python 3.12 с python.org или укажите PYTHON_BIN."
  exit 1
fi

if ! command -v sips >/dev/null 2>&1 || ! command -v iconutil >/dev/null 2>&1; then
  echo "Эта сборка запускается только на macOS: нужны системные sips и iconutil."
  exit 1
fi

rm -rf "$BUILD_DIR" "$DIST_DIR"
mkdir -p "$BUILD_DIR" "$DIST_DIR" "$ICONSET_DIR"

"$PYTHON_BIN" -m venv "$VENV_DIR"
"$VENV_DIR/bin/python" -m pip install --upgrade pip
"$VENV_DIR/bin/python" -m pip install -r "$PROJECT_DIR/requirements.txt" -r "$PROJECT_DIR/requirements-macos-build.txt"
"$VENV_DIR/bin/python" -c "import tkinter; import pandas; import openpyxl"

for icon_spec in \
  "16:icon_16x16.png" "32:icon_16x16@2x.png" \
  "32:icon_32x32.png" "64:icon_32x32@2x.png" \
  "128:icon_128x128.png" "256:icon_128x128@2x.png" \
  "256:icon_256x256.png" "512:icon_256x256@2x.png" \
  "512:icon_512x512.png" "1024:icon_512x512@2x.png"; do
  icon_size="${icon_spec%%:*}"
  icon_file="${icon_spec#*:}"
  sips -z "$icon_size" "$icon_size" "$PROJECT_DIR/assets/topgun_myday_icon.svg" --out "$ICONSET_DIR/$icon_file" >/dev/null
done
iconutil -c icns "$ICONSET_DIR" -o "$ICON_PATH"

"$VENV_DIR/bin/pyinstaller" \
  --noconfirm \
  --clean \
  --windowed \
  --name "$APP_NAME" \
  --icon "$ICON_PATH" \
  --add-data "$PROJECT_DIR/data:data" \
  --add-data "$PROJECT_DIR/config:config" \
  --distpath "$DIST_DIR" \
  --workpath "$BUILD_DIR/pyinstaller-work" \
  --specpath "$BUILD_DIR" \
  "$PROJECT_DIR/app.py"

APP_PATH="$DIST_DIR/$APP_NAME.app"
codesign --force --deep --sign - "$APP_PATH"
ARCHITECTURE="$(uname -m)"
ARCHIVE_PATH="$DIST_DIR/TOPGUN_MyDay-macos-$ARCHITECTURE.zip"
ditto -c -k --sequesterRsrc --keepParent "$APP_PATH" "$ARCHIVE_PATH"

echo "Готово: $ARCHIVE_PATH"
echo "Проверьте .app вручную, затем передайте именно ZIP-архив пользователям."
