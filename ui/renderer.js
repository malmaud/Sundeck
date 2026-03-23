import { useState, useEffect, useCallback } from "react";
import ReactDOM from "react-dom/client";

const UNCHECKED_KEY = "uncheckedGames";

function loadUnchecked() {
  try {
    return new Set(JSON.parse(localStorage.getItem(UNCHECKED_KEY) || "[]"));
  } catch {
    return new Set();
  }
}

function saveUnchecked(unchecked) {
  localStorage.setItem(UNCHECKED_KEY, JSON.stringify([...unchecked]));
}

async function apiGetGames(count) {
  const res = await fetch(`/api/games?count=${count}`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

async function apiGetConfig() {
  const res = await fetch("/api/config");
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

async function apiUpdateConfig(appIds) {
  const res = await fetch("/api/config", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ app_ids: appIds }),
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}

function GameCard({ game, checked, onToggle, willAdd, willRemove }) {
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
  const [games, setGames] = useState([]);
  const [checkedIds, setCheckedIds] = useState(new Set());
  const [configApps, setConfigApps] = useState([]);
  const [count, setCount] = useState(10);
  const [status, setStatus] = useState(null);
  const [busy, setBusy] = useState(false);

  const loadGames = useCallback(async () => {
    setBusy(true);
    setStatus({ msg: "Loading games...", type: "loading" });
    try {
      const [result, currentConfig] = await Promise.all([
        apiGetGames(count),
        apiGetConfig(),
      ]);
      if (result.error) throw new Error(result.error);
      const unchecked = loadUnchecked();
      const checked = new Set(
        result.filter((g) => !unchecked.has(g.app_id)).map((g) => g.app_id)
      );
      setGames(result);
      setCheckedIds(checked);
      setConfigApps(currentConfig);
      setStatus(null);
    } catch (e) {
      setStatus({ msg: e.message, type: "error" });
    } finally {
      setBusy(false);
    }
  }, [count]);

  useEffect(() => { loadGames(); }, []);

  function toggleGame(appId) {
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

  async function handleUpdate() {
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
      setStatus({ msg: e.message, type: "error" });
    } finally {
      setBusy(false);
    }
  }

  const configIdSet = new Set(configApps.map((a) => a.app_id));

  return (
    <>
      <header>
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
        </div>
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
