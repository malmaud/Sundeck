# SteamLaunch

A local web app that keeps an Apollo/Sunshine game streaming config in sync with your most recently played Steam games.

## What it does

- Reads your recent games from Steam's `localconfig.vdf` (no API key needed)
- Writes the game list to Apollo/Sunshine's `apps.json` and restarts the service
- Auto-syncs when `localconfig.vdf` changes or when sync-relevant settings are changed
- Defers sync while a streaming session is active

## Requirements

- [uv](https://github.com/astral-sh/uv)
- [Node.js](https://nodejs.org) + npm

## Usage

Run the server (builds the UI first, then starts the backend):

```powershell
./scripts/start_server.ps1
```

Or in dev mode (enables the Werkzeug reloader):

```powershell
./scripts/dev_server.ps1
```

To rebuild the UI on its own:

```powershell
./scripts/build_ui.ps1
```

The app will be available at `http://localhost:5000`.

## Building the executable

```powershell
./scripts/build_exe.ps1
```
