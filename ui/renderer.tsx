import { useState, useEffect, useRef, useCallback, useMemo } from "react";
import ReactDOM from "react-dom/client";

import type { Game, Settings, Status, LogEntry } from "./types";
import { OTHER_INITIAL_LIMIT } from "./types";
import { apiGetGames, apiGetSettings, apiPatchSettings, apiGetLog, apiSync } from "./api";
import { computeAutoSyncIds } from "./utils";
import { GameCard } from "./GameCard";
import { SettingsPanel } from "./SettingsPanel";
import { SetupModal } from "./SetupModal";
import { LogPanel } from "./LogPanel";

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
    config_path: "", needs_setup: false, suggestions: [], excluded_games: [], included_games: [],
    show_debug: false, count: 10, auto_sync: true, run_at_startup: true,
    desktop_position: "end", has_desktop_app: false,
  });
  const [needsSetup, setNeedsSetup] = useState(false);
  const [configPathInput, setConfigPathInput] = useState("");
  const [autoSync, setAutoSync] = useState(true);
  const [runAtStartup, setRunAtStartup] = useState(true);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [showDebug, setShowDebug] = useState(false);
  const [logOpen, setLogOpen] = useState(false);
  const [logEntries, setLogEntries] = useState<LogEntry[]>([]);
  const [hasLogError, setHasLogError] = useState(false);
  const [errorBannerMsg, setErrorBannerMsg] = useState<string | null>(null);
  const [shutdownState, setShutdownState] = useState<"off" | "stopping" | "stopped">("off");
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
      setRunAtStartup(s.run_at_startup);
      setShowDebug(s.show_debug);
      setConfigPathInput(s.config_path);
      setNeedsSetup(s.needs_setup);
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

  if (shutdownState !== "off") {
    return <div className="shutting-down">{shutdownState === "stopping" ? "Shutting down…" : "SunDeck has shut down."}</div>;
  }

  return (
    <>
      <header>
        <div className="header-row">
          <div className="header-title">
            <img src="/images/logo.png" className="header-logo" alt="SunDeck" />
            <h1>SunDeck</h1>
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
            {(syncState === "pending" || syncState === "syncing") && <span className="sync-status syncing">Syncing…</span>}
            <button className="btn-secondary activity-btn" onClick={() => setLogOpen((o) => !o)}>
              Activity{hasLogError && <span className="log-error-badge" />}
            </button>
            <button className="btn-secondary" onClick={async () => {
              if (!confirm('Shut down SunDeck?')) return;
              setShutdownState("stopping");
              fetch('/api/shutdown', { method: 'POST' }).catch(() => {});
              while (true) {
                await new Promise(r => setTimeout(r, 500));
                try { await fetch('/api/sync-status'); } catch { break; }
              }
              setShutdownState("stopped");
            }}>Shut down</button>
          </div>
        </div>
        {settingsOpen && (
          <SettingsPanel
            configPathInput={configPathInput}
            setConfigPathInput={setConfigPathInput}
            suggestions={settings.suggestions}
            autoSync={autoSync}
            setAutoSync={setAutoSync}
            runAtStartup={runAtStartup}
            setRunAtStartup={setRunAtStartup}
            showDebug={showDebug}
            setShowDebug={setShowDebug}
            hasDesktopApp={settings.has_desktop_app}
            desktopPosition={settings.desktop_position}
            setDesktopPosition={(v) => setSettings(s => ({ ...s, desktop_position: v }))}
          />
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
          <LogPanel logEntries={logEntries} refreshLog={refreshLog} />
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
              <span className="section-desc">{excludedGames.length} games excluded from sync</span>
            </h2>
            <div className="game-grid">
              {excludedGames.map(g => (
                <GameCard key={g.app_id} game={g} showDebug={showDebug}
                  action={{ label: "Stop excluding", className: "action-restore", title: "Remove exclusion", onClick: () => restoreGame(g.app_id) }} />
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
      {needsSetup && (
        <SetupModal
          defaultPath={configPathInput}
          suggestions={settings.suggestions}
          onSave={() => { setNeedsSetup(false); loadGames(); }}
        />
      )}
    </>
  );
}

export { App };

const rootEl = document.getElementById("root");
if (rootEl) {
  ReactDOM.createRoot(rootEl).render(<App />);
}
