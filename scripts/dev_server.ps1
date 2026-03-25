& "$PSScriptRoot/build_ui.ps1"
$watcher = Start-Process -FilePath "cmd.exe" -ArgumentList "/c npm run watch" -WorkingDirectory "$PSScriptRoot/../ui" -PassThru -NoNewWindow
try {
    Set-Location "$PSScriptRoot/../backend"
    uv run server.py --dev
} finally {
    Stop-Process -Id $watcher.Id -ErrorAction SilentlyContinue
}
