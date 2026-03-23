#!/usr/bin/env bash
# Builds steamlaunch into a standalone Windows executable under dist/steamlaunch/.
# Requirements (developer machine only): uv, node/npm
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"

echo "==> Building UI bundle..."
(cd "$ROOT/ui" && npm ci && npm run build)

echo "==> Bundling Python app with PyInstaller..."
(cd "$ROOT/python" && uv run pyinstaller \
  --name steamlaunch \
  --onedir \
  --clean \
  --noconfirm \
  --add-data "../ui/index.html;ui" \
  --add-data "../ui/styles.css;ui" \
  --add-data "../ui/renderer.bundle.js;ui" \
  --distpath "$ROOT/dist" \
  --workpath "$ROOT/build" \
  --specpath "$ROOT/build" \
  server.py)

echo ""
echo "Done. Distribute the folder: dist/steamlaunch/"
echo "Run: dist/steamlaunch/steamlaunch.exe"
