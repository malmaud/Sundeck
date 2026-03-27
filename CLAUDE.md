# SunDeck

A local web app that keeps an Apollo/Sunshine game streaming config in sync with your most recently played Steam games.

## What it does

- Reads recent games from Steam's `localconfig.vdf` (no Steam API key needed)
- Writes the game list to Apollo/Sunshine's `apps.json` and restarts the service
- Writing to `apps.json` and restarting the service requires elevation; the app uses PowerShell with `Start-Process -Verb RunAs` if not already running as admin, or runs directly if it is
- Auto-sync triggers when `localconfig.vdf` changes (watchdog file watcher) or when the user changes sync-relevant settings (unchecked games, count, auto_sync)
- Auto-sync is debounced (5s) and deferred while a streaming session is active (detected via established TCP connections on Sunshine/Apollo ports)

## Architecture

- `backend/server.py` — Flask backend + sync logic
- `backend/steam.py` — reads `localconfig.vdf`, fetches game names/thumbnails
- `backend/sunshine.py` — reads/writes Apollo/Sunshine `apps.json`
- `ui/renderer.tsx` — React frontend (single file, bundled with esbuild)
- `ui/styles.css` — styles

The UI is a static bundle served by Flask. There is no separate dev server.

## Commands

### Run

```
cd backend
uv run server.py
```

### Build UI

```
cd ui
npm run build
```

### Run Python tests

```
cd backend
uv run python -m unittest test_server -v
```

### TypeScript type-check

```
cd ui
npx tsc --noEmit
```

## Development notes

- When you finishing making changes to the ui, you should rebuild it before finishing your response.
- `sync_log.json` is gitignored — it's a runtime file written next to the executable
- Settings are stored in `settings.json` next to the executable (or `backend/settings.json` in dev)
- The server runs under Werkzeug's reloader; the sync watcher and browser open only start in the reloader child process (`WERKZEUG_RUN_MAIN` env var)

## Key design decisions

- **No polling**: auto-sync is triggered by watchdog file events on `localconfig.vdf`, not a timer
- **Elevation**: file writes and service restarts go through PowerShell elevated commands; if already admin, runs directly with `CREATE_NO_WINDOW` to avoid popups
- **No-op detection**: `_do_auto_sync` compares the would-be config against what's on disk and skips write+restart if nothing changed
- **Sync state**: `_sync_state` (`idle`/`pending`/`syncing`) is exposed via `GET /api/sync-status` and polled by the UI every 2s to drive the "Syncing…" indicator
- **Pydantic throughout**: settings, log entries, and sunshine config all use Pydantic v2 models; partial updates use `SettingsPatch` with `model_dump(exclude_none=True)`
