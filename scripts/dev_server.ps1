& "$PSScriptRoot/build_ui.ps1"
Set-Location "$PSScriptRoot/../backend"
uv run server.py --dev
