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

beforeEach(() => {
  localStorage.clear();
  window.api = {
    getGames: jest.fn().mockResolvedValue(GAMES),
    updateConfig: jest.fn().mockResolvedValue({ status: "ok", count: 2 }),
    getCurrentConfig: jest.fn().mockResolvedValue([]),
  };
});

async function renderApp() {
  render(<App />);
  await screen.findByText("Half-Life");
}

describe("renderer sunshine sync", () => {
  test("calls updateConfig with all loaded game IDs on update", async () => {
    await renderApp();
    fireEvent.click(screen.getByText("Update Apollo"));
    await waitFor(() => {
      expect(window.api.updateConfig).toHaveBeenCalledTimes(1);
      expect(window.api.updateConfig).toHaveBeenCalledWith(
        expect.arrayContaining([100, 200])
      );
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
    window.api.updateConfig.mockRejectedValue(new Error("Access denied"));
    await renderApp();
    fireEvent.click(screen.getByText("Update Apollo"));
    await screen.findByText("Access denied");
  });

  test("disables buttons while sync is in progress", async () => {
    let resolveUpdate;
    window.api.updateConfig.mockReturnValue(
      new Promise((resolve) => {
        resolveUpdate = resolve;
      })
    );
    await renderApp();
    fireEvent.click(screen.getByText("Update Apollo"));

    await waitFor(() => {
      expect(screen.getByText("Refresh")).toBeDisabled();
      expect(screen.getByText("Update Apollo")).toBeDisabled();
    });

    act(() => resolveUpdate({ status: "ok", count: 2 }));

    await waitFor(() => {
      expect(screen.getByText("Refresh")).not.toBeDisabled();
      expect(screen.getByText("Update Apollo")).not.toBeDisabled();
    });
  });

  test("re-enables buttons after sync failure", async () => {
    window.api.updateConfig.mockRejectedValue(new Error("oops"));
    await renderApp();
    fireEvent.click(screen.getByText("Update Apollo"));

    await waitFor(() => {
      expect(screen.getByText("Refresh")).not.toBeDisabled();
      expect(screen.getByText("Update Apollo")).not.toBeDisabled();
    });
  });
});
