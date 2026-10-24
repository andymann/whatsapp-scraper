#!/usr/bin/env bash
# Build "WhatsApp Scraper.app" (the Tkinter GUI) with Chromium bundled inside.
#
# Run from the repo root with the venv active (see VENV_SETUP.md):
#     ./build_macos_app.sh
# Output: dist/WhatsApp Scraper.app
set -euo pipefail

cd "$(dirname "$0")"

if [[ -z "${VIRTUAL_ENV:-}" && -x .venv/bin/python ]]; then
    PYTHON=.venv/bin/python
else
    PYTHON=python
fi

echo "==> Installing build dependencies"
"$PYTHON" -m pip install -q -r requirements-build.txt

APP="dist/WhatsApp Scraper.app"
BROWSERS=build/ms-playwright

echo "==> Building the app"
rm -rf build dist
"$PYTHON" -m PyInstaller \
    --noconfirm \
    --windowed \
    --name "WhatsApp Scraper" \
    --osx-bundle-identifier info.andyland.whatsapp-scraper \
    --collect-all playwright \
    --add-data "config.json:." \
    whatsapp_web_gui.py

# Chromium is added after PyInstaller, not through it: PyInstaller re-signs
# every binary it collects on its own, which fails for Chrome's nested .app.
# The GUI points PLAYWRIGHT_BROWSERS_PATH at Contents/Resources/ms-playwright
# when frozen. --no-shell skips the headless-only shell the GUI never uses.
echo "==> Bundling Chromium"
PLAYWRIGHT_BROWSERS_PATH="$BROWSERS" "$PYTHON" -m playwright install --no-shell chromium
cp -R "$BROWSERS" "$APP/Contents/Resources/ms-playwright"

echo "==> Signing (ad-hoc)"
codesign --force --deep --sign - "$APP"
codesign --verify --deep --strict "$APP"

echo
echo "Done: $APP ($(du -sh "$APP" | cut -f1))"
