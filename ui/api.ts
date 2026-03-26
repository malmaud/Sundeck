import type { Game, Settings, LogEntry } from "./types";

export async function apiGetGames(): Promise<Game[]> {
  const res = await fetch("/api/games");
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json() as Promise<Game[]>;
}

export async function apiGetSettings(): Promise<Settings> {
  const res = await fetch("/api/settings");
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json() as Promise<Settings>;
}

export async function apiPatchSettings(updates: Partial<Omit<Settings, "suggestions">>): Promise<void> {
  const res = await fetch("/api/settings", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(updates),
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
}

export async function apiGetLog(): Promise<LogEntry[]> {
  const res = await fetch("/api/log");
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json() as Promise<LogEntry[]>;
}

export async function apiSync(): Promise<void> {
  const res = await fetch("/api/sync", { method: "POST" });
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
}
