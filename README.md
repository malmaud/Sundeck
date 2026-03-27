# SteamLaunch

A local web app that keeps an Apollo/Sunshine game streaming config in sync with your most recently played Steam games.

## Advantages over launching from Sunshine directly

- **Stream closes when your game exits** — no need to manually end the session
- **Skip Steam Big Picture** — launching through Sunshine's built-in Steam app adds an extra step and can be sluggish
- **Control what appears** — pin games you always want available, or exclude ones you don't, on top of your recent games
- **Always up to date** — your game list syncs automatically when your recently played games change; no manual refresh needed

## How it works

- Reads your recent games from Steam's `localconfig.vdf` (no API key needed)
- Writes the game list to Apollo/Sunshine's `apps.json` and restarts the service
- Auto-syncs when `localconfig.vdf` changes or when sync-relevant settings are changed
- Defers sync while a streaming session is active

## Usage

Download the latest release and run `steamlaunch.exe`. The app will be available at `http://localhost:5000`.

## Development

### Requirements

- [uv](https://github.com/astral-sh/uv)
- [Node.js](https://nodejs.org) + npm

### Running from source

Run the server (builds the UI first, then starts the backend):

```powershell
./scripts/start_server.ps1
```

Or in dev mode (enables auto-reloader):

```powershell
./scripts/dev_server.ps1
```

To rebuild the UI on its own:

```powershell
./scripts/build_ui.ps1
```

The app will be available at `http://localhost:5000`.

### Building the executable

```powershell
./scripts/build_exe.ps1
```
