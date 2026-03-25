import { useState, useEffect, useRef, useCallback } from "react";
import ReactDOM from "react-dom/client";

interface Game {
  app_id: number;
  name: string;
  thumbnail: string;
  last_played: number;
}

interface ConfigApp {
  app_id: number;
  name: string;
}

interface Settings {
  config_path: string;
  suggestions: string[];
  unchecked_games: number[];
  show_debug: boolean;
  count: number;
  auto_sync_hours: number;
  last_sync_time: number;
}

interface Status {
  msg: string;
  type: "loading" | "error" | "success";
}

interface LogEntry {
  timestamp: number;
  kind: "manual" | "auto";
  success: boolean;
  message: string;
  detail: string;
}

interface GameCardProps {
  game: Game;
  checked: boolean;
  onToggle: () => void;
  willAdd: boolean;
  willRemove: boolean;
  showDebug: boolean;
}

async function apiGetGames(count: number): Promise<Game[]> {
  const res = await fetch(`/api/games?count=${count}`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json() as Promise<Game[]>;
}

async function apiGetConfig(): Promise<ConfigApp[]> {
  const res = await fetch("/api/config");
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json() as Promise<ConfigApp[]>;
}

async function apiGetSettings(): Promise<Settings> {
  const res = await fetch("/api/settings");
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json() as Promise<Settings>;
}

async function apiPatchSettings(updates: Partial<Omit<Settings, "suggestions">>): Promise<void> {
  const res = await fetch("/api/settings", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(updates),
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
}

async function apiGetLog(): Promise<LogEntry[]> {
  const res = await fetch("/api/log");
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json() as Promise<LogEntry[]>;
}

async function apiUpdateConfig(appIds: number[]): Promise<{ status: string; count: number }> {
  const res = await fetch("/api/config", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ app_ids: appIds }),
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return data as { status: string; count: number };
}

function GameCard({ game, checked, onToggle, willAdd, willRemove, showDebug }: GameCardProps) {
  const [imgLoaded, setImgLoaded] = useState(false);
  const imgRef = useRef<HTMLImageElement>(null);

  useEffect(() => {
    if (imgRef.current?.complete && imgRef.current.naturalWidth > 0) {
      setImgLoaded(true);
    }
  }, []);

  let extra = "";
  if (willAdd) extra = " will-add";
  else if (willRemove) extra = " will-remove";
  return (
    <div className={`game-card${checked ? " checked" : ""}${extra}`}>
      <input
        type="checkbox"
        className="game-check"
        checked={checked}
        onChange={onToggle}
      />
      {game.thumbnail
        ? <>
            <img
              ref={imgRef}
              src={game.thumbnail}
              alt={game.name}
              loading="lazy"
              onLoad={() => setImgLoaded(true)}
            />
            {!imgLoaded && <div className="game-thumbnail-placeholder loading" aria-hidden="true" />}
          </>
        : <div className="game-thumbnail-placeholder" aria-hidden="true" />
      }
      <div className="game-name-row">
        <div className="game-name" title={game.name}>{game.name}</div>
        {willAdd && <div className="diff-badge add">+ add</div>}
        {willRemove && <div className="diff-badge remove">− remove</div>}
      </div>
      {showDebug && <div className="game-id">App ID: {game.app_id}</div>}
      {game.last_played > 0 && (
        <div className="game-last-played">
          {new Date(game.last_played * 1000).toLocaleDateString()}
        </div>
      )}
    </div>
  );
}

function App() {
  const [games, setGames] = useState<Game[]>([]);
  const [checkedIds, setCheckedIds] = useState<Set<number>>(new Set());
  const [configApps, setConfigApps] = useState<ConfigApp[]>([]);
  const [count, setCount] = useState(10);
  const [countInput, setCountInput] = useState("10");
  const [status, setStatus] = useState<Status | null>(null);
  const [busy, setBusy] = useState(false);
  const [settings, setSettings] = useState<Settings>({ config_path: "", suggestions: [], unchecked_games: [], show_debug: false, count: 10, auto_sync_hours: 0, last_sync_time: 0 });
  const [configPathInput, setConfigPathInput] = useState("");
  const [autoSyncHoursInput, setAutoSyncHoursInput] = useState("0");
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [showDebug, setShowDebug] = useState(false);
  const [logOpen, setLogOpen] = useState(false);
  const [logEntries, setLogEntries] = useState<LogEntry[]>([]);
  const [hasLogError, setHasLogError] = useState(false);
  const [errorBannerMsg, setErrorBannerMsg] = useState<string | null>(null);

  const refreshLog = useCallback(() => {
    apiGetLog().then(entries => {
      setLogEntries(entries);
      setHasLogError(!!entries[0] && !entries[0].success);
    }).catch(() => {});
  }, []);

  useEffect(() => {
    if (!logOpen) return;
    refreshLog();
    const id = setInterval(refreshLog, 10_000);
    return () => clearInterval(id);
  }, [logOpen, refreshLog]);

  const loadGames = useCallback(async (n = count) => {
    setBusy(true);
    setStatus({ msg: "Loading games...", type: "loading" });
    try {
      const [result, currentConfig, s] = await Promise.all([
        apiGetGames(n),
        apiGetConfig(),
        apiGetSettings(),
      ]);
      const unchecked = new Set<number>(s.unchecked_games);
      setGames(result);
      setCheckedIds(new Set(result.filter((g) => !unchecked.has(g.app_id)).map((g) => g.app_id)));
      setConfigApps(currentConfig);
      setStatus(null);
    } catch (e) {
      setStatus({ msg: (e as Error).message, type: "error" });
    } finally {
      setBusy(false);
    }
  }, [count]);

  useEffect(() => {
    apiGetSettings().then((s) => {
      setSettings(s);
      setConfigPathInput(s.config_path);
      setShowDebug(s.show_debug);
      setAutoSyncHoursInput(String(s.auto_sync_hours));
      setCount(s.count);
      setCountInput(String(s.count));
      loadGames(s.count);
    }).catch(() => {
      loadGames();
    });
    apiGetLog().then(entries => {
      setLogEntries(entries);
      const latest = entries[0];
      if (latest && !latest.success) {
        setHasLogError(true);
        setErrorBannerMsg(latest.message);
      }
    }).catch(() => {});
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  async function handleSaveSettings(): Promise<void> {
    setBusy(true);
    try {
      const autoSyncHours = Math.max(0, parseFloat(autoSyncHoursInput) || 0);
      await apiPatchSettings({ config_path: configPathInput, auto_sync_hours: autoSyncHours });
      const s = await apiGetSettings();
      setSettings(s);
      setConfigPathInput(s.config_path);
      setAutoSyncHoursInput(String(s.auto_sync_hours));
      setSettingsOpen(false);
      setStatus({ msg: "Settings saved.", type: "success" });
    } catch (e) {
      setStatus({ msg: (e as Error).message, type: "error" });
    } finally {
      setBusy(false);
    }
  }

  function toggleGame(appId: number): void {
    setCheckedIds((prev) => {
      const next = new Set(prev);
      if (next.has(appId)) {
        next.delete(appId);
      } else {
        next.add(appId);
      }
      const unchecked = games.filter((g) => !next.has(g.app_id)).map((g) => g.app_id);
      apiPatchSettings({ unchecked_games: unchecked }).catch(() => {});
      return next;
    });
  }

  async function handleUpdate(): Promise<void> {
    const appIds = [...checkedIds];
    if (appIds.length === 0) {
      setStatus({ msg: "No games selected.", type: "error" });
      return;
    }
    setBusy(true);
    setStatus({ msg: `Syncing to ${serviceName}...`, type: "loading" });
    try {
      const result = await apiUpdateConfig(appIds);
      const updated = await apiGetConfig();
      setConfigApps(updated);
      setStatus({ msg: `Synced ${result.count} games to ${serviceName}.`, type: "success" });
      if (logOpen) refreshLog();
    } catch (e) {
      setStatus({ msg: (e as Error).message, type: "error" });
      if (logOpen) refreshLog();
    } finally {
      setBusy(false);
    }
  }

  const configIdSet = new Set(configApps.map((a) => a.app_id));
  const serviceName = /sunshine/i.test(settings.config_path) ? "Sunshine"
    : /apollo/i.test(settings.config_path) ? "Apollo"
    : "Streaming App";

  return (
    <>
      <header>
        <div className="header-row">
        <h1>SteamLaunch</h1>
        <div className="controls">
          <label>
            Games:
            <input
              type="number"
              value={countInput}
              min="1"
              max="9999"
              onChange={(e) => setCountInput(e.target.value)}
              onBlur={() => {
                const n = Math.max(1, parseInt(countInput) || count);
                setCount(n);
                setCountInput(String(n));
                apiPatchSettings({ count: n }).catch(() => {});
              }}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  const n = Math.max(1, parseInt(countInput) || count);
                  setCount(n);
                  setCountInput(String(n));
                  apiPatchSettings({ count: n }).catch(() => {});
                  loadGames(n);
                }
              }}
            />
          </label>
          <button className="btn-secondary" onClick={() => loadGames()} disabled={busy}>Refresh</button>
          <button className="btn-primary" onClick={handleUpdate} disabled={busy}>Sync to {serviceName}</button>
          <button className="btn-secondary" onClick={() => setSettingsOpen((o) => !o)}>Settings</button>
          <button className="btn-secondary activity-btn" onClick={() => setLogOpen((o) => !o)}>
            Activity{hasLogError && <span className="log-error-badge" />}
          </button>
        </div>
        </div>
        {settingsOpen && (
          <div className="settings-panel">
            <label>
              Config path:
              <input
                list="config-path-suggestions"
                className="config-path-input"
                value={configPathInput}
                onChange={(e) => setConfigPathInput(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && handleSaveSettings()}
                spellCheck={false}
              />
              <datalist id="config-path-suggestions">
                {settings.suggestions.map((s) => <option key={s} value={s} />)}
              </datalist>
            </label>
            <label>
              Auto-sync every
              <input
                type="number"
                value={autoSyncHoursInput}
                min="0"
                step="1"
                onChange={(e) => setAutoSyncHoursInput(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && handleSaveSettings()}
                style={{ width: 60 }}
              />
              hours (0 to disable)
              {settings.auto_sync_hours > 0 && settings.last_sync_time > 0 && (
                <span className="last-sync">last synced {new Date(settings.last_sync_time * 1000).toLocaleString()}</span>
              )}
            </label>
            <label className="debug-toggle">
              <input
                type="checkbox"
                checked={showDebug}
                onChange={(e) => {
                  setShowDebug(e.target.checked);
                  apiPatchSettings({ show_debug: e.target.checked }).catch(() => {});
                }}
              />
              Show debug information
            </label>
            <button className="btn-primary" onClick={handleSaveSettings} disabled={busy}>Save</button>
          </div>
        )}
      </header>
      <main>
        {status && (
          <div className={`status ${status.type}`}>{status.msg}</div>
        )}
        {errorBannerMsg && (
          <div className="error-banner">
            <span>Last sync failed: {errorBannerMsg}</span>
            <button className="error-banner-dismiss" onClick={() => setErrorBannerMsg(null)}>✕</button>
          </div>
        )}
        {logOpen && (
          <div className="log-panel">
            <div className="log-panel-header">
              <span>Activity Log</span>
              <button className="btn-secondary" onClick={refreshLog}>Refresh</button>
            </div>
            {logEntries.length === 0
              ? <div className="log-empty">No activity yet.</div>
              : <div className="log-entries">
                  {logEntries.map((e, i) => (
                    <div key={i} className="log-entry" title={e.detail || undefined}>
                      <span className="log-time">
                        {new Date(e.timestamp * 1000).toLocaleString()}
                      </span>
                      <span className={`log-kind ${e.kind}`}>{e.kind}</span>
                      <span className={`log-status ${e.success ? "success" : "error"}`}>
                        {e.success ? "✓" : "✗"}
                      </span>
                      <span className="log-msg">{e.message}</span>
                    </div>
                  ))}
                </div>
            }
          </div>
        )}
        <div className="game-grid">
          {games.map((game) => (
            <GameCard
              key={game.app_id}
              game={game}
              checked={checkedIds.has(game.app_id)}
              onToggle={() => toggleGame(game.app_id)}
              willAdd={checkedIds.has(game.app_id) && !configIdSet.has(game.app_id)}
              willRemove={!checkedIds.has(game.app_id) && configIdSet.has(game.app_id)}
              showDebug={showDebug}
            />
          ))}
        </div>
      </main>
    </>
  );
}

export { App };

const rootEl = document.getElementById("root");
if (rootEl) {
  ReactDOM.createRoot(rootEl).render(<App />);
}
