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

class MockEventSource {
  static instances: MockEventSource[] = [];
  private listeners: Record<string, Array<(e: MessageEvent) => void>> = {};
  closed = false;
  readonly url: string;

  constructor(url: string) {
    this.url = url;
    MockEventSource.instances.push(this);
  }

  addEventListener(type: string, fn: (e: MessageEvent) => void): void {
    (this.listeners[type] ??= []).push(fn);
  }

  dispatch(type: string, data: unknown): void {
    const event = new MessageEvent(type, { data: JSON.stringify(data) });
    (this.listeners[type] ?? []).forEach(fn => fn(event));
  }

  close(): void { this.closed = true; }

  static latest(): MockEventSource {
    return MockEventSource.instances[MockEventSource.instances.length - 1];
  }

  static reset(): void { MockEventSource.instances = []; }
}

beforeEach(() => {
  jest.useFakeTimers();
  MockEventSource.reset();
  (globalThis as any).EventSource = MockEventSource;
  mockFetch({
    "GET /api/games": () => ({ body: GAMES }),
    "GET /api/config": () => ({ body: [] }),
    "POST /api/config": () => ({ body: { status: "ok", count: 2 } }),
    "GET /api/settings": () => ({ body: DEFAULT_SETTINGS }),
    "POST /api/settings": () => ({ body: { status: "ok" } }),
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

  test("shown when SSE delivers pending state", async () => {
    await renderApp();
    act(() => { MockEventSource.latest().dispatch("sync_status", { state: "pending", games_version: 0 }); });
    await screen.findByText("Syncing…");
  });

  test("shown when SSE delivers syncing state", async () => {
    await renderApp();
    act(() => { MockEventSource.latest().dispatch("sync_status", { state: "syncing", games_version: 0 }); });
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
  test("reloads game list when games_version increments via SSE", async () => {
    const UPDATED_GAMES = [
      { app_id: 100, name: "Half-Life", thumbnail: "" },
      { app_id: 300, name: "Portal 2", thumbnail: "" },
    ];
    let gameList = GAMES;

    (globalThis as any).fetch = jest.fn((url: string, opts?: RequestInit) => {
      const method = (opts && opts.method) || "GET";
      if (url.includes("/api/games")) {
        return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(gameList) });
      }
      const body = url.includes("/api/settings") && method === "GET" ? DEFAULT_SETTINGS : [];
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) });
    });

    await renderApp();
    expect(screen.getByText("Portal")).toBeInTheDocument();

    // First event establishes the baseline version (no reload since prevGamesVersion starts null)
    act(() => { MockEventSource.latest().dispatch("sync_status", { state: "idle", games_version: 0 }); });
    await waitFor(() => {});

    // Bump version and update game list, then fire SSE event
    gameList = UPDATED_GAMES;
    act(() => { MockEventSource.latest().dispatch("sync_status", { state: "idle", games_version: 1 }); });

    await screen.findByText("Portal 2");
    expect(screen.queryByText("Portal")).toBeNull();
  });

  test("does not reload when games_version is unchanged", async () => {
    let gameListFetchCount = 0;
    (globalThis as any).fetch = jest.fn((url: string, opts?: RequestInit) => {
      const method = (opts && opts.method) || "GET";
      if (url.includes("/api/games")) gameListFetchCount++;
      const body = url.includes("/api/settings") && method === "GET" ? DEFAULT_SETTINGS : GAMES;
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) });
    });

    await renderApp();

    // Establish baseline version
    act(() => { MockEventSource.latest().dispatch("sync_status", { state: "idle", games_version: 0 }); });
    await waitFor(() => {});
    const fetchesAfterBaseline = gameListFetchCount;

    // Same version again — no reload
    act(() => { MockEventSource.latest().dispatch("sync_status", { state: "idle", games_version: 0 }); });
    await waitFor(() => {});

    expect(gameListFetchCount).toBe(fetchesAfterBaseline);
  });
});

// ---------------------------------------------------------------------------
// SSE connection
// ---------------------------------------------------------------------------

describe("SSE connection", () => {
  test("connects to /api/events on mount", async () => {
    await renderApp();
    expect(MockEventSource.latest().url).toBe("/api/events");
  });

  test("closes EventSource on unmount", async () => {
    const { unmount } = render(<App />);
    await screen.findByText("Half-Life");
    const es = MockEventSource.latest();
    unmount();
    expect(es.closed).toBe(true);
  });

  test("hides syncing indicator when SSE delivers idle after syncing", async () => {
    await renderApp();
    const es = MockEventSource.latest();
    act(() => { es.dispatch("sync_status", { state: "syncing", games_version: 0 }); });
    await screen.findByText("Syncing…");
    act(() => { es.dispatch("sync_status", { state: "idle", games_version: 0 }); });
    await waitFor(() => expect(screen.queryByText("Syncing…")).toBeNull());
  });

  test("refreshes log when SSE delivers log_updated", async () => {
    let logFetchCount = 0;
    (globalThis as any).fetch = jest.fn((url: string, opts?: RequestInit) => {
      const method = (opts && opts.method) || "GET";
      if (url.includes("/api/log")) logFetchCount++;
      const body = url.includes("/api/settings") && method === "GET" ? DEFAULT_SETTINGS
        : url.includes("/api/games") ? GAMES : [];
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) });
    });

    await renderApp();
    const countAfterMount = logFetchCount;

    act(() => { MockEventSource.latest().dispatch("log_updated", {}); });
    await waitFor(() => expect(logFetchCount).toBeGreaterThan(countAfterMount));
  });

  test("refreshes log when sync transitions from syncing to idle", async () => {
    let logFetchCount = 0;
    (globalThis as any).fetch = jest.fn((url: string, opts?: RequestInit) => {
      const method = (opts && opts.method) || "GET";
      if (url.includes("/api/log")) logFetchCount++;
      const body = url.includes("/api/settings") && method === "GET" ? DEFAULT_SETTINGS
        : url.includes("/api/games") ? GAMES : [];
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) });
    });

    await renderApp();
    const es = MockEventSource.latest();

    act(() => { es.dispatch("sync_status", { state: "syncing", games_version: 0 }); });
    const countBeforeIdle = logFetchCount;

    act(() => { es.dispatch("sync_status", { state: "idle", games_version: 0 }); });
    await waitFor(() => expect(logFetchCount).toBeGreaterThan(countBeforeIdle));
  });
});
