# Builds sundeck into a standalone Windows executable under dist/sundeck/.
# Requirements (developer machine only): uv, node/npm
$ErrorActionPreference = "Stop"

$ROOT = "$PSScriptRoot/.."

Write-Host "==> Building UI bundle..."
Push-Location "$ROOT\ui"
npm install
npm run build
Pop-Location

Write-Host "==> Bundling Python app with PyInstaller..."
Push-Location "$ROOT\backend"
uv run pyinstaller `
  --name sundeck `
  --onedir `
  --clean `
  --noconfirm `
  --add-data "..\ui\index.html;ui" `
  --add-data "..\ui\styles.css;ui" `
  --add-data "..\ui\renderer.bundle.js;ui" `
  --add-data "..\images;images" `
  --distpath "$ROOT\dist" `
  --workpath "$ROOT\build" `
  --specpath "$ROOT\build" `
  main.py
Pop-Location

Write-Host ""
Write-Host "Done. Distribute the folder: dist\sundeck\"
Write-Host "Run: dist\sundeck\sundeck.exe"
