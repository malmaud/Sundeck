import { useState, useEffect, useRef, useCallback, useMemo } from "react";
import ReactDOM from "react-dom/client";

interface Game {
  app_id: number;
  name: string;
  thumbnail: string;
  last_played: number;
}

interface Settings {
  config_path: string;
  suggestions: string[];
  excluded_games: number[];
  included_games: number[];
  show_debug: boolean;
  count: number;
  auto_sync: boolean;
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

interface CardAction {
  label: string;
  className: string;
  title: string;
  onClick: () => void;
}

interface GameCardProps {
  game: Game;
  action: CardAction;
  showDebug: boolean;
}

const OTHER_INITIAL_LIMIT = 24;

async function apiGetGames(): Promise<Game[]> {
  const res = await fetch("/api/games");
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json() as Promise<Game[]>;
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

async function apiSync(): Promise<void> {
  const res = await fetch("/api/sync", { method: "POST" });
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
}

function computeAutoSyncIds(games: Game[], count: number, excludedIds: Set<number>): Set<number> {
  const ids = new Set<number>();
  let n = 0;
  for (const g of games) {
    if (excludedIds.has(g.app_id)) continue;
    if (n >= count) break;
    ids.add(g.app_id);
    n++;
  }
  return ids;
}

function GameCard({ game, action, showDebug }: GameCardProps) {
  const [imgLoaded, setImgLoaded] = useState(false);
  const [imgError, setImgError] = useState(false);
  const imgRef = useRef<HTMLImageElement>(null);

  useEffect(() => {
    if (imgRef.current?.complete && imgRef.current.naturalWidth > 0) {
      setImgLoaded(true);
    }
  }, []);

  return (
    <div className="game-card">
      <button
        className={`card-action ${action.className}`}
        onClick={action.onClick}
        title={action.title}
      >
        {action.label}
      </button>
      {!imgError && game.thumbnail
        ? <>
            <img
              ref={imgRef}
              src={game.thumbnail}
              alt={game.name}
              loading="lazy"
              onLoad={() => setImgLoaded(true)}
              onError={() => setImgError(true)}
            />
            {!imgLoaded && <div className="game-thumbnail-placeholder loading" aria-hidden="true" />}
          </>
        : <div className="game-thumbnail-placeholder" aria-hidden="true" />
      }
      <div className="game-name-row">
        <div className="game-name" title={game.name}>{game.name}</div>
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
  const [excludedIds, setExcludedIds] = useState<Set<number>>(new Set());
  const [includedIds, setIncludedIds] = useState<Set<number>>(new Set());
  const [count, setCount] = useState(10);
  const [countInput, setCountInput] = useState("10");
  const [searchQuery, setSearchQuery] = useState("");
  const [showAllOther, setShowAllOther] = useState(false);
  const [status, setStatus] = useState<Status | null>(null);
  const [busy, setBusy] = useState(false);
  const [settings, setSettings] = useState<Settings>({
    config_path: "", suggestions: [], excluded_games: [], included_games: [],
    show_debug: false, count: 10, auto_sync: true,
  });
  const [configPathInput, setConfigPathInput] = useState("");
  const [autoSync, setAutoSync] = useState(true);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [showDebug, setShowDebug] = useState(false);
  const [logOpen, setLogOpen] = useState(false);
  const [logEntries, setLogEntries] = useState<LogEntry[]>([]);
  const [hasLogError, setHasLogError] = useState(false);
  const [errorBannerMsg, setErrorBannerMsg] = useState<string | null>(null);
  const [syncState, setSyncState] = useState<string>("idle");
  const prevSyncState = useRef("idle");
  const prevGamesVersion = useRef<number | null>(null);

  const refreshLog = useCallback(() => {
    apiGetLog().then(entries => {
      setLogEntries(entries);
      setHasLogError(!!entries[0] && !entries[0].success);
    }).catch(() => {});
  }, []);

  useEffect(() => {
    if (!logOpen) return;
    refreshLog();
  }, [logOpen, refreshLog]);

  const loadGames = useCallback(async () => {
    setBusy(true);
    try {
      const [result, s] = await Promise.all([apiGetGames(), apiGetSettings()]);
      setGames(result);
      setExcludedIds(new Set(s.excluded_games));
      setIncludedIds(new Set(s.included_games));
      setSettings(s);
      setCount(s.count);
      setCountInput(String(s.count));
      setAutoSync(s.auto_sync);
      setShowDebug(s.show_debug);
      setConfigPathInput(s.config_path);
      setStatus(null);
    } catch (e) {
      setStatus({ msg: (e as Error).message, type: "error" });
    } finally {
      setBusy(false);
    }
  }, []);

  useEffect(() => {
    const es = new EventSource("/api/events");

    es.addEventListener("sync_status", (e: MessageEvent) => {
      const { state, games_version } = JSON.parse(e.data) as { state: string; games_version: number };
      setSyncState(state);
      if (prevSyncState.current === "syncing" && state === "idle") {
        refreshLog();
      }
      prevSyncState.current = state;
      if (prevGamesVersion.current !== null && games_version !== prevGamesVersion.current) {
        loadGames();
      }
      prevGamesVersion.current = games_version;
    });

    es.addEventListener("log_updated", () => {
      refreshLog();
    });

    return () => es.close();
  }, [refreshLog, loadGames]);

  useEffect(() => {
    loadGames();
    apiGetLog().then(entries => {
      setLogEntries(entries);
      const latest = entries[0];
      if (latest && !latest.success) {
        setHasLogError(true);
        setErrorBannerMsg(latest.message);
      }
    }).catch(() => {});
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const autoSyncIds = useMemo(
    () => computeAutoSyncIds(games, count, excludedIds),
    [games, count, excludedIds],
  );

  // ── Section actions ──────────────────────────────────────────────────────

  function excludeGame(appId: number): void {
    setExcludedIds(prev => {
      const next = new Set(prev);
      next.add(appId);
      apiPatchSettings({ excluded_games: [...next] }).catch(() => {});
      return next;
    });
  }

  function pinGame(appId: number): void {
    setIncludedIds(prev => {
      const next = new Set(prev);
      next.add(appId);
      apiPatchSettings({ included_games: [...next] }).catch(() => {});
      return next;
    });
  }

  function unpinGame(appId: number): void {
    setIncludedIds(prev => {
      const next = new Set(prev);
      next.delete(appId);
      apiPatchSettings({ included_games: [...next] }).catch(() => {});
      return next;
    });
  }

  function restoreGame(appId: number): void {
    setExcludedIds(prev => {
      const next = new Set(prev);
      next.delete(appId);
      apiPatchSettings({ excluded_games: [...next] }).catch(() => {});
      return next;
    });
  }

  // ── Sections ─────────────────────────────────────────────────────────────

  const matchesSearch = useCallback((g: Game) => {
    if (!searchQuery.trim()) return true;
    return g.name.toLowerCase().includes(searchQuery.toLowerCase());
  }, [searchQuery]);

  const recentGames = useMemo(() =>
    games.filter(g => autoSyncIds.has(g.app_id) && !includedIds.has(g.app_id) && matchesSearch(g)),
    [games, autoSyncIds, includedIds, matchesSearch],
  );

  const pinnedGames = useMemo(() =>
    games.filter(g => includedIds.has(g.app_id) && !excludedIds.has(g.app_id) && matchesSearch(g)),
    [games, includedIds, excludedIds, matchesSearch],
  );

  const excludedGames = useMemo(() =>
    games.filter(g => excludedIds.has(g.app_id) && matchesSearch(g)),
    [games, excludedIds, matchesSearch],
  );

  const otherGames = useMemo(() =>
    games.filter(g =>
      !autoSyncIds.has(g.app_id) && !includedIds.has(g.app_id) && !excludedIds.has(g.app_id) && matchesSearch(g)
    ),
    [games, autoSyncIds, includedIds, excludedIds, matchesSearch],
  );

  const displayedOther = showAllOther ? otherGames : otherGames.slice(0, OTHER_INITIAL_LIMIT);
  const syncedCount = recentGames.length + pinnedGames.length;
  const totalVisible = recentGames.length + pinnedGames.length + excludedGames.length + otherGames.length;

  async function handleSync(): Promise<void> {
    setBusy(true);
    setSyncState("syncing");
    try {
      await apiSync();
      setStatus({ msg: "Sync complete.", type: "success" });
      refreshLog();
    } catch (e) {
      setStatus({ msg: (e as Error).message, type: "error" });
      refreshLog();
    } finally {
      setBusy(false);
      setSyncState("idle");
    }
  }

  return (
    <>
      <header>
        <div className="header-row">
          <div className="header-title">
            <img src="/images/logo.png" className="header-logo" alt="SteamLaunch" />
            <h1>SteamLaunch</h1>
          </div>
          <div className="controls">
            <label>
              Recent games to sync:
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
                  }
                }}
              />
            </label>
            <span className="synced-count">{syncedCount} synced</span>
            <button className="btn-secondary" onClick={handleSync} disabled={busy}>Sync now</button>
            <button className="btn-secondary" onClick={() => setSettingsOpen((o) => !o)}>Settings</button>
            {syncState === "syncing" && <span className="sync-status syncing">Syncing…</span>}
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
                onBlur={() => apiPatchSettings({ config_path: configPathInput }).catch(() => {})}
                onKeyDown={(e) => e.key === "Enter" && apiPatchSettings({ config_path: configPathInput }).catch(() => {})}
                spellCheck={false}
              />
              <datalist id="config-path-suggestions">
                {settings.suggestions.map((s) => <option key={s} value={s} />)}
              </datalist>
            </label>
            <label className="auto-sync-toggle">
              <input
                type="checkbox"
                checked={autoSync}
                onChange={(e) => {
                  setAutoSync(e.target.checked);
                  apiPatchSettings({ auto_sync: e.target.checked }).catch(() => {});
                }}
              />
              Auto-sync when game list changes
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
        <div className="search-bar">
          <input
            type="text"
            placeholder="Search games..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="search-input"
          />
          {searchQuery && (
            <button className="search-clear" onClick={() => setSearchQuery("")}>✕</button>
          )}
          <span className="search-count">
            {searchQuery ? `${totalVisible} of ${games.length}` : `${games.length} games`}
          </span>
        </div>

        {recentGames.length > 0 && (
          <section className="game-section">
            <h2 className="section-header section-recent">
              Recent
              <span className="section-desc">{recentGames.length} games synced automatically</span>
            </h2>
            <div className="game-grid">
              {recentGames.map(g => (
                <GameCard key={g.app_id} game={g} showDebug={showDebug}
                  action={{ label: "Exclude", className: "action-exclude", title: "Exclude from sync", onClick: () => excludeGame(g.app_id) }} />
              ))}
            </div>
          </section>
        )}

        {pinnedGames.length > 0 && (
          <section className="game-section">
            <h2 className="section-header section-pinned">
              Pinned
              <span className="section-desc">{pinnedGames.length} games always synced</span>
            </h2>
            <div className="game-grid">
              {pinnedGames.map(g => (
                <GameCard key={g.app_id} game={g} showDebug={showDebug}
                  action={{ label: "Unpin", className: "action-unpin", title: "Stop pinning", onClick: () => unpinGame(g.app_id) }} />
              ))}
            </div>
          </section>
        )}

        {excludedGames.length > 0 && (
          <section className="game-section">
            <h2 className="section-header section-excluded">
              Excluded
              <span className="section-desc">{excludedGames.length} games never synced</span>
            </h2>
            <div className="game-grid">
              {excludedGames.map(g => (
                <GameCard key={g.app_id} game={g} showDebug={showDebug}
                  action={{ label: "Restore", className: "action-restore", title: "Remove exclusion", onClick: () => restoreGame(g.app_id) }} />
              ))}
            </div>
          </section>
        )}

        {otherGames.length > 0 && (
          <section className="game-section">
            <h2 className="section-header section-other">
              Other
              <span className="section-desc">{otherGames.length} games not synced</span>
            </h2>
            <div className="game-grid">
              {displayedOther.map(g => (
                <GameCard key={g.app_id} game={g} showDebug={showDebug}
                  action={{ label: "Pin", className: "action-pin", title: "Always sync this game", onClick: () => pinGame(g.app_id) }} />
              ))}
            </div>
            {!showAllOther && otherGames.length > OTHER_INITIAL_LIMIT && (
              <button className="btn-secondary show-more" onClick={() => setShowAllOther(true)}>
                Show all {otherGames.length} games
              </button>
            )}
          </section>
        )}
      </main>
    </>
  );
}

export { App };

const rootEl = document.getElementById("root");
if (rootEl) {
  ReactDOM.createRoot(rootEl).render(<App />);
}
