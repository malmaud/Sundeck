& "$PSScriptRoot/build_ui.ps1"
Push-Location "$PSScriptRoot/../backend"
try { uv run server.py } finally { Pop-Location }
