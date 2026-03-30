# Builds sundeck into a standalone single-file Windows executable under dist/.
# Requirements (developer machine only): uv, node/npm
$ErrorActionPreference = "Stop"

$ROOT = "$PSScriptRoot/.."

Write-Host "==> Building UI bundle..."
Push-Location "$ROOT\ui"
npm install
npm run build
Pop-Location

Write-Host "==> Converting favicon to ICO..."
uv --project "$ROOT\backend" run python -c "from PIL import Image; Image.open(r'$ROOT\images\favicon.png').save(r'$ROOT\images\favicon.ico')"

Write-Host "==> Bundling Python app with PyInstaller..."
Push-Location "$ROOT\backend"
uv run python -m PyInstaller `
  --name sundeck `
  --onefile `
  --noconsole `
  --icon "..\images\favicon.ico" `
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
Write-Host "Done. Distribute the file: dist\sundeck.exe"
