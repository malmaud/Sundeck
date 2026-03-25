/**
 * @jest-environment jsdom
 */

import { render, screen, fireEvent, waitFor, act } from "@testing-library/react";
import { App } from "../renderer";

interface Game {
  app_id: number;
  name: string;
  thumbnail: string;
}

const GAMES: Game[] = [
  { app_id: 100, name: "Half-Life", thumbnail: "" },
  { app_id: 200, name: "Portal", thumbnail: "" },
];

type FetchHandler = () => { status?: number; body: unknown };

function mockFetch(handlers: Record<string, FetchHandler>): void {
  (globalThis as any).fetch = jest.fn((url: string, opts?: RequestInit) => {
    const method = (opts && opts.method) || "GET";
    const key = `${method} ${url.replace(/\?.*$/, "")}`;
    const handler = handlers[key];
    if (!handler) return Promise.reject(new Error(`Unexpected fetch: ${method} ${url}`));
    const { status = 200, body } = handler();
    return Promise.resolve({
      ok: status >= 200 && status < 300,
      status,
      json: () => Promise.resolve(body),
    });
  });
}

const DEFAULT_SETTINGS = {
  config_path: "C:\\Program Files\\Apollo\\config\\apps.json",
  suggestions: [
    "C:\\Program Files\\Apollo\\config\\apps.json",
    "C:\\Program Files\\Sunshine\\config\\apps.json",
  ],
  unchecked_games: [],
  show_debug: false,
  count: 10,
  auto_sync: true,
};

const DEFAULT_SYNC_STATUS = { state: "idle", games_version: 0 };

beforeEach(() => {
  jest.useFakeTimers();
  mockFetch({
    "GET /api/games": () => ({ body: GAMES }),
    "GET /api/config": () => ({ body: [] }),
    "POST /api/config": () => ({ body: { status: "ok", count: 2 } }),
    "GET /api/settings": () => ({ body: DEFAULT_SETTINGS }),
    "POST /api/settings": () => ({ body: { status: "ok" } }),
    "GET /api/sync-status": () => ({ body: DEFAULT_SYNC_STATUS }),
    "GET /api/log": () => ({ body: [] }),
  });
});

afterEach(() => {
  jest.useRealTimers();
});

async function renderApp(): Promise<void> {
  render(<App />);
  await screen.findByText("Half-Life");
}

// ---------------------------------------------------------------------------
// manual sync
// ---------------------------------------------------------------------------

describe("manual sync", () => {
  test("calls POST /api/config with all loaded game IDs", async () => {
    await renderApp();
    fireEvent.click(screen.getByText("Manually sync now"));
    await waitFor(() => {
      const fetchMock = (globalThis as any).fetch as jest.Mock;
      const postCall = fetchMock.mock.calls.find(
        ([, opts]: [string, RequestInit?]) => opts && opts.method === "POST" && fetchMock.mock.calls.find(([u]: [string]) => u === "/api/config")
      );
      expect(postCall).toBeTruthy();
      const body = JSON.parse(postCall[1].body as string);
      expect(body.app_ids).toEqual(expect.arrayContaining([100, 200]));
    });
  });

  test("shows success status after sync", async () => {
    await renderApp();
    fireEvent.click(screen.getByText("Manually sync now"));
    await screen.findByText(/2 games/);
  });

  test("shows error status when no games are selected", async () => {
    await renderApp();
    screen.getAllByRole("checkbox").forEach((cb) => {
      if ((cb as HTMLInputElement).checked) fireEvent.click(cb);
    });
    fireEvent.click(screen.getByText("Manually sync now"));
    await screen.findByText(/No games selected/);
  });

  test("shows error status when sync fails", async () => {
    mockFetch({
      "GET /api/games": () => ({ body: GAMES }),
      "GET /api/config": () => ({ body: [] }),
      "GET /api/settings": () => ({ body: DEFAULT_SETTINGS }),
      "GET /api/sync-status": () => ({ body: DEFAULT_SYNC_STATUS }),
      "GET /api/log": () => ({ body: [] }),
      "POST /api/config": () => ({ status: 500, body: { error: "Access denied" } }),
    });
    await renderApp();
    fireEvent.click(screen.getByText("Manually sync now"));
    await screen.findByText("Access denied");
  });

  test("disables buttons while sync is in progress", async () => {
    let resolvePost!: () => void;
    (globalThis as any).fetch = jest.fn((url: string, opts?: RequestInit) => {
      const method = (opts && opts.method) || "GET";
      if (method === "POST" && url === "/api/config") {
        return new Promise((resolve) => {
          resolvePost = () => resolve({ ok: true, status: 200, json: () => Promise.resolve({ status: "ok", count: 2 }) });
        });
      }
      const body = url.includes("/api/games") ? GAMES
        : url.includes("/api/sync-status") ? DEFAULT_SYNC_STATUS
        : [];
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) });
    });
    await renderApp();
    fireEvent.click(screen.getByText("Manually sync now"));

    await waitFor(() => {
      expect(screen.getByText("Manually sync now")).toBeDisabled();
    });

    act(() => resolvePost());

    await waitFor(() => {
      expect(screen.getByText("Manually sync now")).not.toBeDisabled();
    });
  });

  test("re-enables buttons after sync failure", async () => {
    mockFetch({
      "GET /api/games": () => ({ body: GAMES }),
      "GET /api/config": () => ({ body: [] }),
      "GET /api/settings": () => ({ body: DEFAULT_SETTINGS }),
      "GET /api/sync-status": () => ({ body: DEFAULT_SYNC_STATUS }),
      "GET /api/log": () => ({ body: [] }),
      "POST /api/config": () => ({ status: 500, body: { error: "oops" } }),
    });
    await renderApp();
    fireEvent.click(screen.getByText("Manually sync now"));
    await waitFor(() => {
      expect(screen.getByText("Manually sync now")).not.toBeDisabled();
    });
  });
});

// ---------------------------------------------------------------------------
// settings panel
// ---------------------------------------------------------------------------

describe("settings panel", () => {
  test("is hidden by default", async () => {
    await renderApp();
    expect(screen.queryByDisplayValue(DEFAULT_SETTINGS.config_path)).toBeNull();
  });

  test("toggles open and closed", async () => {
    await renderApp();
    const btn = screen.getByText("Settings");
    fireEvent.click(btn);
    expect(screen.getByDisplayValue(DEFAULT_SETTINGS.config_path)).toBeInTheDocument();
    fireEvent.click(btn);
    expect(screen.queryByDisplayValue(DEFAULT_SETTINGS.config_path)).toBeNull();
  });

  test("shows config path loaded from api", async () => {
    await renderApp();
    fireEvent.click(screen.getByText("Settings"));
    expect(screen.getByDisplayValue(DEFAULT_SETTINGS.config_path)).toBeInTheDocument();
  });

  test("saves config path on blur", async () => {
    await renderApp();
    fireEvent.click(screen.getByText("Settings"));
    const newPath = "C:\\Program Files\\Sunshine\\config\\apps.json";
    const input = screen.getByDisplayValue(DEFAULT_SETTINGS.config_path) as HTMLInputElement;
    fireEvent.change(input, { target: { value: newPath } });
    fireEvent.blur(input);
    await waitFor(() => {
      const fetchMock = (globalThis as any).fetch as jest.Mock;
      const postCall = fetchMock.mock.calls.find(
        ([url, opts]: [string, RequestInit?]) => url === "/api/settings" && opts?.method === "POST"
      );
      expect(postCall).toBeTruthy();
      expect(JSON.parse(postCall[1].body).config_path).toBe(newPath);
    });
  });

  test("stays open after settings panel interaction", async () => {
    await renderApp();
    fireEvent.click(screen.getByText("Settings"));
    const input = screen.getByDisplayValue(DEFAULT_SETTINGS.config_path) as HTMLInputElement;
    fireEvent.blur(input);
    expect(screen.getByDisplayValue(DEFAULT_SETTINGS.config_path)).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// sync status indicator
// ---------------------------------------------------------------------------

describe("sync status indicator", () => {
  test("not shown when state is idle", async () => {
    await renderApp();
    expect(screen.queryByText("Syncing…")).toBeNull();
  });

  test("shown when sync-status is pending", async () => {
    mockFetch({
      "GET /api/games": () => ({ body: GAMES }),
      "GET /api/config": () => ({ body: [] }),
      "GET /api/settings": () => ({ body: DEFAULT_SETTINGS }),
      "GET /api/sync-status": () => ({ body: { state: "pending", games_version: 0 } }),
      "GET /api/log": () => ({ body: [] }),
    });
    await renderApp();
    await screen.findByText("Syncing…");
  });

  test("shown when sync-status is syncing", async () => {
    mockFetch({
      "GET /api/games": () => ({ body: GAMES }),
      "GET /api/config": () => ({ body: [] }),
      "GET /api/settings": () => ({ body: DEFAULT_SETTINGS }),
      "GET /api/sync-status": () => ({ body: { state: "syncing", games_version: 0 } }),
      "GET /api/log": () => ({ body: [] }),
    });
    await renderApp();
    await screen.findByText("Syncing…");
  });

  test("shown immediately when a game is toggled with auto_sync enabled", async () => {
    await renderApp();
    const checkboxes = screen.getAllByRole("checkbox");
    fireEvent.click(checkboxes[0]);
    await screen.findByText("Syncing…");
  });

  test("not shown immediately when a game is toggled with auto_sync disabled", async () => {
    mockFetch({
      "GET /api/games": () => ({ body: GAMES }),
      "GET /api/config": () => ({ body: [] }),
      "GET /api/settings": () => ({ body: { ...DEFAULT_SETTINGS, auto_sync: false } }),
      "GET /api/sync-status": () => ({ body: DEFAULT_SYNC_STATUS }),
      "GET /api/log": () => ({ body: [] }),
      "POST /api/settings": () => ({ body: { status: "ok" } }),
    });
    await renderApp();
    const checkboxes = screen.getAllByRole("checkbox");
    fireEvent.click(checkboxes[0]);
    expect(screen.queryByText("Syncing…")).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// games_version — game list refresh
// ---------------------------------------------------------------------------

describe("games_version refresh", () => {
  test("reloads game list when games_version increments", async () => {
    const UPDATED_GAMES = [
      { app_id: 100, name: "Half-Life", thumbnail: "" },
      { app_id: 300, name: "Portal 2", thumbnail: "" },
    ];
    let version = 0;
    let gameList = GAMES;

    (globalThis as any).fetch = jest.fn((url: string, opts?: RequestInit) => {
      const method = (opts && opts.method) || "GET";
      if (url.includes("/api/sync-status")) {
        return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ state: "idle", games_version: version }) });
      }
      if (url.includes("/api/games")) {
        return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(gameList) });
      }
      const body = url.includes("/api/settings") && method === "GET" ? DEFAULT_SETTINGS : [];
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) });
    });

    await renderApp();
    expect(screen.getByText("Portal")).toBeInTheDocument();

    // Simulate vdf change: bump version and swap game list
    version = 1;
    gameList = UPDATED_GAMES;

    // Advance timers to trigger the 2s poll
    act(() => { jest.advanceTimersByTime(2000); });

    await screen.findByText("Portal 2");
    expect(screen.queryByText("Portal")).toBeNull();
  });

  test("does not reload when games_version is unchanged", async () => {
    let gameListFetchCount = 0;
    (globalThis as any).fetch = jest.fn((url: string, opts?: RequestInit) => {
      const method = (opts && opts.method) || "GET";
      if (url.includes("/api/games")) gameListFetchCount++;
      if (url.includes("/api/sync-status")) {
        return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ state: "idle", games_version: 0 }) });
      }
      const body = url.includes("/api/settings") && method === "GET" ? DEFAULT_SETTINGS : GAMES;
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) });
    });

    await renderApp();
    const fetchesAfterLoad = gameListFetchCount;

    act(() => { jest.advanceTimersByTime(2000); });
    await waitFor(() => {}); // flush

    expect(gameListFetchCount).toBe(fetchesAfterLoad); // no extra fetch
  });
});
