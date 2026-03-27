# Reset settings to simulate a first-launch experience.
# Backs up the current settings.json, then removes it so the app
# starts with needs_setup = true.

$settingsFile = "$PSScriptRoot/../backend/settings.json"

if (Test-Path $settingsFile) {
    $backup = "$settingsFile.bak"
    Copy-Item $settingsFile $backup -Force
    Remove-Item $settingsFile
    Write-Host "Backed up settings to $backup and removed settings.json"
    Write-Host "Run the dev server to see the first-launch setup flow."
    Write-Host "To restore: Copy-Item '$backup' '$settingsFile'"
} else {
    Write-Host "No settings.json found - app is already in first-launch state."
}
