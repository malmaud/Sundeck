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
};

beforeEach(() => {
  localStorage.clear();
  mockFetch({
    "GET /api/games": () => ({ body: GAMES }),
    "GET /api/config": () => ({ body: [] }),
    "POST /api/config": () => ({ body: { status: "ok", count: 2 } }),
    "GET /api/settings": () => ({ body: DEFAULT_SETTINGS }),
    "POST /api/settings": () => ({ body: { status: "ok", config_path: DEFAULT_SETTINGS.config_path } }),
  });
});

async function renderApp(): Promise<void> {
  render(<App />);
  await screen.findByText("Half-Life");
}

describe("renderer sunshine sync", () => {
  test("calls POST /api/config with all loaded game IDs on update", async () => {
    await renderApp();
    fireEvent.click(screen.getByText("Update Apollo"));
    await waitFor(() => {
      const fetchMock = (globalThis as any).fetch as jest.Mock;
      const postCall = fetchMock.mock.calls.find(
        ([, opts]: [string, RequestInit?]) => opts && opts.method === "POST"
      );
      expect(postCall).toBeTruthy();
      const body = JSON.parse(postCall[1].body as string);
      expect(body.app_ids).toEqual(expect.arrayContaining([100, 200]));
    });
  });

  test("shows success status after sync", async () => {
    await renderApp();
    fireEvent.click(screen.getByText("Update Apollo"));
    await screen.findByText(/2 games/);
  });

  test("shows error status when no games are selected", async () => {
    await renderApp();
    screen.getAllByRole("checkbox").forEach((cb) => {
      if ((cb as HTMLInputElement).checked) fireEvent.click(cb);
    });
    fireEvent.click(screen.getByText("Update Apollo"));
    await screen.findByText(/No games selected/);
  });

  test("shows error status when sync fails", async () => {
    (globalThis as any).fetch = jest.fn((url: string, opts?: RequestInit) => {
      const method = (opts && opts.method) || "GET";
      if (method === "POST") {
        return Promise.resolve({
          ok: false,
          status: 500,
          json: () => Promise.resolve({ error: "Access denied" }),
        });
      }
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve(method === "GET" && url.includes("/api/games") ? GAMES : []),
      });
    });
    await renderApp();
    fireEvent.click(screen.getByText("Update Apollo"));
    await screen.findByText("Access denied");
  });

  test("disables buttons while sync is in progress", async () => {
    let resolvePost: () => void;
    (globalThis as any).fetch = jest.fn((url: string, opts?: RequestInit) => {
      const method = (opts && opts.method) || "GET";
      if (method === "POST") {
        return new Promise((resolve) => {
          resolvePost = () =>
            resolve({
              ok: true,
              status: 200,
              json: () => Promise.resolve({ status: "ok", count: 2 }),
            });
        });
      }
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve(url.includes("/api/games") ? GAMES : []),
      });
    });
    await renderApp();
    fireEvent.click(screen.getByText("Update Apollo"));

    await waitFor(() => {
      expect(screen.getByText("Refresh")).toBeDisabled();
      expect(screen.getByText("Update Apollo")).toBeDisabled();
    });

    act(() => resolvePost());

    await waitFor(() => {
      expect(screen.getByText("Refresh")).not.toBeDisabled();
      expect(screen.getByText("Update Apollo")).not.toBeDisabled();
    });
  });

  test("re-enables buttons after sync failure", async () => {
    (globalThis as any).fetch = jest.fn((url: string, opts?: RequestInit) => {
      const method = (opts && opts.method) || "GET";
      if (method === "POST") {
        return Promise.resolve({
          ok: false,
          status: 500,
          json: () => Promise.resolve({ error: "oops" }),
        });
      }
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve(url.includes("/api/games") ? GAMES : []),
      });
    });
    await renderApp();
    fireEvent.click(screen.getByText("Update Apollo"));

    await waitFor(() => {
      expect(screen.getByText("Refresh")).not.toBeDisabled();
      expect(screen.getByText("Update Apollo")).not.toBeDisabled();
    });
  });
});

describe("renderer settings panel", () => {
  test("settings panel is hidden by default", async () => {
    await renderApp();
    expect(screen.queryByPlaceholderText("config-path-input")).toBeNull();
    expect(screen.queryByText("Save")).toBeNull();
  });

  test("gear button toggles settings panel open and closed", async () => {
    await renderApp();
    const gear = screen.getByText("Settings");
    fireEvent.click(gear);
    expect(screen.getByText("Save")).toBeInTheDocument();
    fireEvent.click(gear);
    expect(screen.queryByText("Save")).toBeNull();
  });

  test("settings panel shows config path loaded from api", async () => {
    await renderApp();
    fireEvent.click(screen.getByText("Settings"));
    const input = screen.getByDisplayValue(DEFAULT_SETTINGS.config_path) as HTMLInputElement;
    expect(input).toBeInTheDocument();
  });

  test("save calls POST /api/settings with current input value", async () => {
    await renderApp();
    fireEvent.click(screen.getByText("Settings"));

    const newPath = "C:\\Program Files\\Sunshine\\config\\apps.json";
    const input = screen.getByDisplayValue(DEFAULT_SETTINGS.config_path) as HTMLInputElement;
    fireEvent.change(input, { target: { value: newPath } });
    fireEvent.click(screen.getByText("Save"));

    await waitFor(() => {
      const fetchMock = (globalThis as any).fetch as jest.Mock;
      const postCall = fetchMock.mock.calls.find(
        ([url, opts]: [string, RequestInit?]) =>
          url === "/api/settings" && opts?.method === "POST"
      );
      expect(postCall).toBeTruthy();
      const body = JSON.parse(postCall[1].body as string);
      expect(body.config_path).toBe(newPath);
    });
  });

  test("settings panel closes after successful save", async () => {
    await renderApp();
    fireEvent.click(screen.getByText("Settings"));
    fireEvent.click(screen.getByText("Save"));
    await waitFor(() => {
      expect(screen.queryByText("Save")).toBeNull();
    });
  });

  test("shows success status after save", async () => {
    await renderApp();
    fireEvent.click(screen.getByText("Settings"));
    fireEvent.click(screen.getByText("Save"));
    await screen.findByText("Settings saved.");
  });

  test("shows error status when save fails", async () => {
    mockFetch({
      "GET /api/games": () => ({ body: GAMES }),
      "GET /api/config": () => ({ body: [] }),
      "GET /api/settings": () => ({ body: DEFAULT_SETTINGS }),
      "POST /api/settings": () => ({ status: 500, body: { error: "Permission denied" } }),
    });
    await renderApp();
    fireEvent.click(screen.getByText("Settings"));
    fireEvent.click(screen.getByText("Save"));
    await screen.findByText("Permission denied");
  });

  test("settings panel stays open after failed save", async () => {
    mockFetch({
      "GET /api/games": () => ({ body: GAMES }),
      "GET /api/config": () => ({ body: [] }),
      "GET /api/settings": () => ({ body: DEFAULT_SETTINGS }),
      "POST /api/settings": () => ({ status: 500, body: { error: "Permission denied" } }),
    });
    await renderApp();
    fireEvent.click(screen.getByText("Settings"));
    fireEvent.click(screen.getByText("Save"));
    await screen.findByText("Permission denied");
    expect(screen.getByText("Save")).toBeInTheDocument();
  });
});
