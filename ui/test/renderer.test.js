/**
 * @jest-environment jsdom
 */
"use strict";

const { render, screen, fireEvent, waitFor, act } = require("@testing-library/react");
const { App } = require("../renderer");

const GAMES = [
  { app_id: 100, name: "Half-Life", thumbnail: "" },
  { app_id: 200, name: "Portal", thumbnail: "" },
];

function mockFetch(handlers) {
  global.fetch = jest.fn((url, opts) => {
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

beforeEach(() => {
  localStorage.clear();
  mockFetch({
    "GET /api/games": () => ({ body: GAMES }),
    "GET /api/config": () => ({ body: [] }),
    "POST /api/config": () => ({ body: { status: "ok", count: 2 } }),
  });
});

async function renderApp() {
  render(<App />);
  await screen.findByText("Half-Life");
}

describe("renderer sunshine sync", () => {
  test("calls POST /api/config with all loaded game IDs on update", async () => {
    await renderApp();
    fireEvent.click(screen.getByText("Update Apollo"));
    await waitFor(() => {
      const postCall = global.fetch.mock.calls.find(
        ([url, opts]) => opts && opts.method === "POST"
      );
      expect(postCall).toBeTruthy();
      const body = JSON.parse(postCall[1].body);
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
      if (cb.checked) fireEvent.click(cb);
    });
    fireEvent.click(screen.getByText("Update Apollo"));
    await screen.findByText(/No games selected/);
  });

  test("shows error status when sync fails", async () => {
    global.fetch = jest.fn((url, opts) => {
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
    let resolvePost;
    global.fetch = jest.fn((url, opts) => {
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
    global.fetch = jest.fn((url, opts) => {
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
