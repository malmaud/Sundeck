# Builds steamlaunch into a standalone Windows executable under dist/steamlaunch/.
# Requirements (developer machine only): uv, node/npm
$ErrorActionPreference = "Stop"

$ROOT = $PSScriptRoot

Write-Host "==> Building UI bundle..."
Push-Location "$ROOT\ui"
npm ci
npm run build
Pop-Location

Write-Host "==> Bundling Python app with PyInstaller..."
Push-Location "$ROOT\python"
uv run pyinstaller `
  --name steamlaunch `
  --onedir `
  --clean `
  --noconfirm `
  --add-data "..\ui\index.html;ui" `
  --add-data "..\ui\styles.css;ui" `
  --add-data "..\ui\renderer.bundle.js;ui" `
  --distpath "$ROOT\dist" `
  --workpath "$ROOT\build" `
  --specpath "$ROOT\build" `
  server.py
Pop-Location

Write-Host ""
Write-Host "Done. Distribute the folder: dist\steamlaunch\"
Write-Host "Run: dist\steamlaunch\steamlaunch.exe"
