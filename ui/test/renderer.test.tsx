/**
 * @jest-environment jsdom
 */

import { render, screen, fireEvent, waitFor, act } from "@testing-library/react";
import { App } from "../renderer";

interface Game {
  app_id: number;
  name: string;
  thumbnail: string;
  last_played: number;
}

const GAMES: Game[] = [
  { app_id: 100, name: "Half-Life", thumbnail: "/thumbnails/100.png", last_played: 1000 },
  { app_id: 200, name: "Portal", thumbnail: "/thumbnails/200.png", last_played: 900 },
  { app_id: 300, name: "Dota 2", thumbnail: "/thumbnails/300.png", last_played: 800 },
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
  needs_setup: false,
  suggestions: [
    "C:\\Program Files\\Apollo\\config\\apps.json",
    "C:\\Program Files\\Sunshine\\config\\apps.json",
  ],
  excluded_games: [],
  included_games: [],
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
    "GET /api/settings": () => ({ body: DEFAULT_SETTINGS }),
    "POST /api/settings": () => ({ body: { status: "ok" } }),
    "POST /api/sync": () => ({ body: { status: "ok" } }),
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
// sections
// ---------------------------------------------------------------------------

describe("sections", () => {
  test("shows recent section with all games when count >= total", async () => {
    await renderApp();
    expect(screen.getByText("Recent")).toBeInTheDocument();
    expect(screen.getByText("Half-Life")).toBeInTheDocument();
    expect(screen.getByText("Portal")).toBeInTheDocument();
    expect(screen.getByText("Dota 2")).toBeInTheDocument();
  });

  test("shows other section when count is less than total games", async () => {
    mockFetch({
      "GET /api/games": () => ({ body: GAMES }),
      "GET /api/settings": () => ({ body: { ...DEFAULT_SETTINGS, count: 2 } }),
      "POST /api/settings": () => ({ body: { status: "ok" } }),
      "GET /api/log": () => ({ body: [] }),
    });
    await renderApp();
    expect(screen.getByText("Recent")).toBeInTheDocument();
    expect(screen.getByText("Other")).toBeInTheDocument();
  });

  test("shows pinned section when games are included", async () => {
    mockFetch({
      "GET /api/games": () => ({ body: GAMES }),
      "GET /api/settings": () => ({ body: { ...DEFAULT_SETTINGS, included_games: [300] } }),
      "POST /api/settings": () => ({ body: { status: "ok" } }),
      "GET /api/log": () => ({ body: [] }),
    });
    await renderApp();
    expect(screen.getByText("Pinned")).toBeInTheDocument();
  });

  test("shows excluded section when games are excluded", async () => {
    mockFetch({
      "GET /api/games": () => ({ body: GAMES }),
      "GET /api/settings": () => ({ body: { ...DEFAULT_SETTINGS, excluded_games: [100] } }),
      "POST /api/settings": () => ({ body: { status: "ok" } }),
      "GET /api/log": () => ({ body: [] }),
    });
    await renderApp();
    expect(screen.getByText("Excluded")).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// card actions
// ---------------------------------------------------------------------------

describe("card actions", () => {
  test("exclude button sends excluded_games update", async () => {
    await renderApp();
    // Hover over the first card to reveal the action button
    const excludeButtons = screen.getAllByText("Exclude");
    fireEvent.click(excludeButtons[0]);
    await waitFor(() => {
      const fetchMock = (globalThis as any).fetch as jest.Mock;
      const postCall = fetchMock.mock.calls.find(
        ([url, opts]: [string, RequestInit?]) => url === "/api/settings" && opts?.method === "POST"
      );
      expect(postCall).toBeTruthy();
      const body = JSON.parse(postCall[1].body);
      expect(body.excluded_games).toContain(100);
    });
  });

  test("pin button sends included_games update", async () => {
    mockFetch({
      "GET /api/games": () => ({ body: GAMES }),
      "GET /api/settings": () => ({ body: { ...DEFAULT_SETTINGS, count: 2 } }),
      "POST /api/settings": () => ({ body: { status: "ok" } }),
      "GET /api/log": () => ({ body: [] }),
    });
    await renderApp();
    const pinButtons = screen.getAllByText("Pin");
    fireEvent.click(pinButtons[0]);
    await waitFor(() => {
      const fetchMock = (globalThis as any).fetch as jest.Mock;
      const postCall = fetchMock.mock.calls.find(
        ([url, opts]: [string, RequestInit?]) => url === "/api/settings" && opts?.method === "POST"
      );
      expect(postCall).toBeTruthy();
      const body = JSON.parse(postCall[1].body);
      expect(body.included_games).toContain(300);
    });
  });
});

// ---------------------------------------------------------------------------
// manual sync
// ---------------------------------------------------------------------------

describe("manual sync", () => {
  test("calls POST /api/sync when clicked", async () => {
    await renderApp();
    fireEvent.click(screen.getByText("Sync now"));
    await waitFor(() => {
      const fetchMock = (globalThis as any).fetch as jest.Mock;
      const postCall = fetchMock.mock.calls.find(
        ([url, opts]: [string, RequestInit?]) => url === "/api/sync" && opts?.method === "POST"
      );
      expect(postCall).toBeTruthy();
    });
  });

  test("shows success status after sync", async () => {
    await renderApp();
    fireEvent.click(screen.getByText("Sync now"));
    await screen.findByText(/Sync complete/);
  });

  test("shows error status when sync fails", async () => {
    mockFetch({
      "GET /api/games": () => ({ body: GAMES }),
      "GET /api/settings": () => ({ body: DEFAULT_SETTINGS }),
      "GET /api/log": () => ({ body: [] }),
      "POST /api/sync": () => ({ status: 500, body: { error: "Access denied" } }),
    });
    await renderApp();
    fireEvent.click(screen.getByText("Sync now"));
    await screen.findByText("Access denied");
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
});

// ---------------------------------------------------------------------------
// sync status indicator
// ---------------------------------------------------------------------------

describe("sync status indicator", () => {
  test("not shown when state is idle", async () => {
    await renderApp();
    expect(screen.queryByText("Syncing…")).toBeNull();
  });

  test("shown when SSE delivers syncing state", async () => {
    await renderApp();
    act(() => { MockEventSource.latest().dispatch("sync_status", { state: "syncing", games_version: 0 }); });
    await screen.findByText("Syncing…");
  });
});

// ---------------------------------------------------------------------------
// games_version — game list refresh
// ---------------------------------------------------------------------------

describe("games_version refresh", () => {
  test("reloads game list when games_version increments via SSE", async () => {
    const UPDATED_GAMES = [
      { app_id: 100, name: "Half-Life", thumbnail: "", last_played: 1000 },
      { app_id: 300, name: "Portal 2", thumbnail: "", last_played: 900 },
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

    act(() => { MockEventSource.latest().dispatch("sync_status", { state: "idle", games_version: 0 }); });
    await waitFor(() => {});

    gameList = UPDATED_GAMES;
    act(() => { MockEventSource.latest().dispatch("sync_status", { state: "idle", games_version: 1 }); });

    await screen.findByText("Portal 2");
    expect(screen.queryByText("Portal")).toBeNull();
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
});

// ---------------------------------------------------------------------------
// search
// ---------------------------------------------------------------------------

describe("search", () => {
  test("filters games by name across all sections", async () => {
    await renderApp();
    const searchInput = screen.getByPlaceholderText("Search games...");
    fireEvent.change(searchInput, { target: { value: "Half" } });
    expect(screen.getByText("Half-Life")).toBeInTheDocument();
    expect(screen.queryByText("Portal")).toBeNull();
    expect(screen.queryByText("Dota 2")).toBeNull();
  });

  test("shows all games when search is cleared", async () => {
    await renderApp();
    const searchInput = screen.getByPlaceholderText("Search games...");
    fireEvent.change(searchInput, { target: { value: "Half" } });
    fireEvent.change(searchInput, { target: { value: "" } });
    expect(screen.getByText("Half-Life")).toBeInTheDocument();
    expect(screen.getByText("Portal")).toBeInTheDocument();
    expect(screen.getByText("Dota 2")).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// setup modal
// ---------------------------------------------------------------------------

describe("setup modal", () => {
  test("shows setup modal when needs_setup is true", async () => {
    mockFetch({
      "GET /api/games": () => ({ body: GAMES }),
      "GET /api/settings": () => ({ body: { ...DEFAULT_SETTINGS, needs_setup: true } }),
      "POST /api/settings": () => ({ body: { status: "ok" } }),
      "GET /api/log": () => ({ body: [] }),
    });
    render(<App />);
    await screen.findByText("Welcome to SteamLaunch");
  });

  test("does not show setup modal when needs_setup is false", async () => {
    await renderApp();
    expect(screen.queryByText("Welcome to SteamLaunch")).toBeNull();
  });

  test("pre-fills config path input with default value", async () => {
    mockFetch({
      "GET /api/games": () => ({ body: GAMES }),
      "GET /api/settings": () => ({ body: { ...DEFAULT_SETTINGS, needs_setup: true } }),
      "POST /api/settings": () => ({ body: { status: "ok" } }),
      "GET /api/log": () => ({ body: [] }),
    });
    render(<App />);
    await screen.findByText("Welcome to SteamLaunch");
    expect(screen.getByDisplayValue(DEFAULT_SETTINGS.config_path)).toBeInTheDocument();
  });

  test("dismisses modal after saving config path", async () => {
    let settingsCallCount = 0;
    mockFetch({
      "GET /api/games": () => ({ body: GAMES }),
      "GET /api/settings": () => {
        settingsCallCount++;
        return {
          body: settingsCallCount <= 1
            ? { ...DEFAULT_SETTINGS, needs_setup: true }
            : { ...DEFAULT_SETTINGS, needs_setup: false },
        };
      },
      "POST /api/settings": () => ({ body: { status: "ok" } }),
      "GET /api/log": () => ({ body: [] }),
    });
    render(<App />);
    await screen.findByText("Welcome to SteamLaunch");

    fireEvent.click(screen.getByText("Save & Continue"));

    await waitFor(() => {
      expect(screen.queryByText("Welcome to SteamLaunch")).toBeNull();
    });
  });

  test("sends config_path to API on save", async () => {
    mockFetch({
      "GET /api/games": () => ({ body: GAMES }),
      "GET /api/settings": () => ({ body: { ...DEFAULT_SETTINGS, needs_setup: true } }),
      "POST /api/settings": () => ({ body: { status: "ok" } }),
      "GET /api/log": () => ({ body: [] }),
    });
    render(<App />);
    await screen.findByText("Welcome to SteamLaunch");

    fireEvent.click(screen.getByText("Save & Continue"));

    await waitFor(() => {
      const fetchMock = (globalThis as any).fetch as jest.Mock;
      const postCall = fetchMock.mock.calls.find(
        ([url, opts]: [string, RequestInit?]) => url === "/api/settings" && opts?.method === "POST"
      );
      expect(postCall).toBeTruthy();
      expect(JSON.parse(postCall[1].body).config_path).toBe(DEFAULT_SETTINGS.config_path);
    });
  });
});
