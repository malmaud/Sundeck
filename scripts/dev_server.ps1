& "$PSScriptRoot/build_ui.ps1"
$watcher = Start-Process -FilePath "cmd.exe" -ArgumentList "/c npm run watch" -WorkingDirectory "$PSScriptRoot/../ui" -PassThru -NoNewWindow
Push-Location "$PSScriptRoot/../backend"
try {
    uv run server.py --dev
} finally {
    Pop-Location
    Stop-Process -Id $watcher.Id -ErrorAction SilentlyContinue
}
