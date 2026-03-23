import { useState, useEffect, useCallback } from "react";
import ReactDOM from "react-dom/client";

interface Game {
  app_id: number;
  name: string;
  thumbnail: string;
}

interface ConfigApp {
  app_id: number;
  name: string;
}

interface Settings {
  config_path: string;
  suggestions: string[];
}

interface Status {
  msg: string;
  type: "loading" | "error" | "success";
}

interface GameCardProps {
  game: Game;
  checked: boolean;
  onToggle: () => void;
  willAdd: boolean;
  willRemove: boolean;
}

const UNCHECKED_KEY = "uncheckedGames";

function loadUnchecked(): Set<number> {
  try {
    return new Set(JSON.parse(localStorage.getItem(UNCHECKED_KEY) || "[]"));
  } catch {
    return new Set();
  }
}

function saveUnchecked(unchecked: Set<number>): void {
  localStorage.setItem(UNCHECKED_KEY, JSON.stringify([...unchecked]));
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

async function apiSaveSettings(configPath: string): Promise<void> {
  const res = await fetch("/api/settings", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ config_path: configPath }),
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
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

function GameCard({ game, checked, onToggle, willAdd, willRemove }: GameCardProps) {
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
      {game.thumbnail && (
        <img src={game.thumbnail} alt={game.name} />
      )}
      <div className="game-name" title={game.name}>{game.name}</div>
      <div className="game-id">App ID: {game.app_id}</div>
      {willAdd && <div className="diff-badge add">+ add</div>}
      {willRemove && <div className="diff-badge remove">− remove</div>}
    </div>
  );
}

function App() {
  const [games, setGames] = useState<Game[]>([]);
  const [checkedIds, setCheckedIds] = useState<Set<number>>(new Set());
  const [configApps, setConfigApps] = useState<ConfigApp[]>([]);
  const [count, setCount] = useState(10);
  const [status, setStatus] = useState<Status | null>(null);
  const [busy, setBusy] = useState(false);
  const [settings, setSettings] = useState<Settings>({ config_path: "", suggestions: [] });
  const [configPathInput, setConfigPathInput] = useState("");
  const [settingsOpen, setSettingsOpen] = useState(false);

  const loadGames = useCallback(async () => {
    setBusy(true);
    setStatus({ msg: "Loading games...", type: "loading" });
    try {
      const [result, currentConfig] = await Promise.all([
        apiGetGames(count),
        apiGetConfig(),
      ]);
      const unchecked = loadUnchecked();
      const checked = new Set(
        result.filter((g) => !unchecked.has(g.app_id)).map((g) => g.app_id)
      );
      setGames(result);
      setCheckedIds(checked);
      setConfigApps(currentConfig);
      setStatus(null);
    } catch (e) {
      setStatus({ msg: (e as Error).message, type: "error" });
    } finally {
      setBusy(false);
    }
  }, [count]);

  useEffect(() => {
    loadGames();
    apiGetSettings().then((s) => {
      setSettings(s);
      setConfigPathInput(s.config_path);
    }).catch(() => {});
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  async function handleSaveSettings(): Promise<void> {
    setBusy(true);
    try {
      await apiSaveSettings(configPathInput);
      const s = await apiGetSettings();
      setSettings(s);
      setConfigPathInput(s.config_path);
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
      const unchecked = loadUnchecked();
      if (next.has(appId)) {
        next.delete(appId);
        unchecked.add(appId);
      } else {
        next.add(appId);
        unchecked.delete(appId);
      }
      saveUnchecked(unchecked);
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
    setStatus({ msg: "Updating Apollo config...", type: "loading" });
    try {
      const result = await apiUpdateConfig(appIds);
      const updated = await apiGetConfig();
      setConfigApps(updated);
      setStatus({ msg: `Apollo config updated with ${result.count} games.`, type: "success" });
    } catch (e) {
      setStatus({ msg: (e as Error).message, type: "error" });
    } finally {
      setBusy(false);
    }
  }

  const configIdSet = new Set(configApps.map((a) => a.app_id));

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
              value={count}
              min="1"
              max="50"
              onChange={(e) => setCount(parseInt(e.target.value) || 10)}
              onKeyDown={(e) => e.key === "Enter" && loadGames()}
            />
          </label>
          <button className="btn-secondary" onClick={loadGames} disabled={busy}>Refresh</button>
          <button className="btn-primary" onClick={handleUpdate} disabled={busy}>Update Apollo</button>
          <button className="btn-secondary" onClick={() => setSettingsOpen((o) => !o)}>Settings</button>
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
            <button className="btn-primary" onClick={handleSaveSettings} disabled={busy}>Save</button>
          </div>
        )}
      </header>
      <main>
        {status && (
          <div className={`status ${status.type}`}>{status.msg}</div>
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
