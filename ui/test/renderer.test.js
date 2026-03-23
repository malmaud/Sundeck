/**
 * @jest-environment jsdom
 */
"use strict";

const GAMES = [
  { app_id: 100, name: "Half-Life", thumbnail: "" },
  { app_id: 200, name: "Portal", thumbnail: "" },
];

function flushPromises() {
  return new Promise((resolve) => setTimeout(resolve, 0));
}

function setupDom() {
  document.body.innerHTML = `
    <input type="number" id="count" value="10" />
    <button id="btn-refresh">Refresh</button>
    <button id="btn-update">Update Apollo</button>
    <div id="status" class="status" hidden></div>
    <div id="games" class="game-grid"></div>
  `;
}

describe("renderer sunshine sync", () => {
  beforeEach(async () => {
    setupDom();
    window.api = {
      getGames: jest.fn().mockResolvedValue(GAMES),
      updateConfig: jest.fn().mockResolvedValue({ status: "ok", count: 2 }),
    };
    jest.resetModules();
    require("../renderer");
    await flushPromises(); // let initial loadGames() complete
  });

  test("calls updateConfig with all loaded game IDs on update", async () => {
    document.getElementById("btn-update").click();
    await flushPromises();

    expect(window.api.updateConfig).toHaveBeenCalledTimes(1);
    expect(window.api.updateConfig).toHaveBeenCalledWith(
      expect.arrayContaining([100, 200])
    );
  });

  test("shows success status after sync", async () => {
    document.getElementById("btn-update").click();
    await flushPromises();

    const status = document.getElementById("status");
    expect(status.hidden).toBe(false);
    expect(status.className).toContain("success");
    expect(status.textContent).toMatch(/2 games/);
  });

  test("shows error status when no games are selected", async () => {
    // Uncheck all rendered game cards
    document.querySelectorAll(".game-check").forEach((cb) => {
      cb.checked = false;
      cb.dispatchEvent(new Event("change"));
    });

    document.getElementById("btn-update").click();
    await flushPromises();

    const status = document.getElementById("status");
    expect(status.hidden).toBe(false);
    expect(status.className).toContain("error");
    expect(status.textContent).toMatch(/No games selected/);
  });

  test("shows error status when sync fails", async () => {
    window.api.updateConfig.mockRejectedValue(new Error("Access denied"));

    document.getElementById("btn-update").click();
    await flushPromises();

    const status = document.getElementById("status");
    expect(status.hidden).toBe(false);
    expect(status.className).toContain("error");
    expect(status.textContent).toBe("Access denied");
  });

  test("disables buttons while sync is in progress", async () => {
    let resolveUpdate;
    window.api.updateConfig.mockReturnValue(
      new Promise((resolve) => {
        resolveUpdate = resolve;
      })
    );

    document.getElementById("btn-update").click();
    await flushPromises();

    expect(document.getElementById("btn-refresh").disabled).toBe(true);
    expect(document.getElementById("btn-update").disabled).toBe(true);

    resolveUpdate({ status: "ok", count: 2 });
    await flushPromises();

    expect(document.getElementById("btn-refresh").disabled).toBe(false);
    expect(document.getElementById("btn-update").disabled).toBe(false);
  });

  test("re-enables buttons after sync failure", async () => {
    window.api.updateConfig.mockRejectedValue(new Error("oops"));

    document.getElementById("btn-update").click();
    await flushPromises();

    expect(document.getElementById("btn-refresh").disabled).toBe(false);
    expect(document.getElementById("btn-update").disabled).toBe(false);
  });
});
