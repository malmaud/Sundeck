"use strict";

// Mock electron before requiring main.js
jest.mock("electron", () => ({
  app: {
    whenReady: jest.fn().mockReturnValue({ then: jest.fn() }),
    on: jest.fn(),
  },
  BrowserWindow: jest.fn(),
  ipcMain: { handle: jest.fn() },
}));

jest.mock("child_process");
jest.mock("fs");

const { ipcMain } = require("electron");
const { execFile } = require("child_process");
const fs = require("fs");

// execFile is now used for all three calls:
//   1. uv run cli.py build ...     (runCliRaw)
//   2. powershell.exe ...          (writeFileElevated via runElevated)
//   3. powershell.exe ...          (restartServiceElevated via runElevated)
function mockExecFileSuccess(configJson) {
  execFile.mockImplementation((...args) => {
    const cb = args[args.length - 1];
    if (args[0] === "powershell.exe") {
      cb(null); // elevated calls succeed
    } else {
      cb(null, configJson, ""); // CLI call returns JSON
    }
  });
}

describe("update-config IPC handler (sunshine sync)", () => {
  let handlers;

  beforeAll(() => {
    handlers = {};
    ipcMain.handle.mockImplementation((event, fn) => {
      handlers[event] = fn;
    });
    require("../main");
  });

  beforeEach(() => {
    jest.clearAllMocks();
    fs.writeFileSync.mockImplementation(() => {});
  });

  test("builds config via CLI and writes to Apollo config path", async () => {
    const configJson = JSON.stringify({ apps: [{ name: "Half-Life" }] });
    mockExecFileSuccess(configJson);

    const result = await handlers["update-config"](null, [100, 200]);

    expect(result).toEqual({ status: "ok", count: 2 });
    expect(execFile).toHaveBeenCalledWith(
      expect.anything(),
      expect.arrayContaining(["cli.py", "build", "--app_ids=100,200"]),
      expect.any(Object),
      expect.any(Function)
    );
  });

  test("writes config content to a temp file before elevated copy", async () => {
    const configJson = '{"apps":[]}';
    mockExecFileSuccess(configJson);

    await handlers["update-config"](null, [100]);

    expect(fs.writeFileSync).toHaveBeenCalledWith(
      expect.stringContaining("steamlaunch-"),
      configJson,
      "utf-8"
    );
  });

  test("invokes elevated powershell for the write and the restart", async () => {
    mockExecFileSuccess('{"apps":[]}');

    await handlers["update-config"](null, [100]);

    // Call 1: uv cli, calls 2+3: powershell.exe elevated
    const powershellCalls = execFile.mock.calls.filter(
      ([cmd]) => cmd === "powershell.exe"
    );
    expect(powershellCalls).toHaveLength(2);
    powershellCalls.forEach(([, args]) => {
      expect(args[0]).toBe("-NoProfile");
      expect(args[1]).toBe("-Command");
      expect(args[2]).toMatch(/Start-Process powershell -Verb RunAs/);
    });
  });

  test("encodes restart command with ApolloService in the elevated call", async () => {
    mockExecFileSuccess('{"apps":[]}');

    await handlers["update-config"](null, [100]);

    // The 3rd execFile call is restartServiceElevated — check the EncodedCommand decodes correctly
    const allPsCalls = execFile.mock.calls.filter(([cmd]) => cmd === "powershell.exe");
    const restartArg = allPsCalls[allPsCalls.length - 1][1][2];
    const encoded = restartArg.match(/EncodedCommand\s+(\S+)/)[1];
    const decoded = Buffer.from(encoded, "base64").toString("utf16le");
    expect(decoded).toContain("ApolloService");
    expect(decoded).toMatch(/net stop/);
    expect(decoded).toMatch(/net start/);
  });

  test("propagates CLI error", async () => {
    execFile.mockImplementation((...args) => {
      const cb = args[args.length - 1];
      cb(new Error("uv not found"), "", "uv not found");
    });

    await expect(handlers["update-config"](null, [100])).rejects.toThrow(
      "uv not found"
    );
  });

  test("propagates elevated write failure", async () => {
    execFile
      .mockImplementationOnce((_cmd, _args, _opts, cb) =>
        cb(null, '{"apps":[]}', "")
      ) // runCliRaw succeeds
      .mockImplementationOnce((...args) => {
        const cb = args[args.length - 1];
        cb(new Error("UAC cancelled"));
      }); // writeFileElevated fails

    await expect(handlers["update-config"](null, [100])).rejects.toThrow(
      "Elevated write failed or was cancelled."
    );
  });

  test("propagates service restart failure", async () => {
    execFile
      .mockImplementationOnce((_cmd, _args, _opts, cb) =>
        cb(null, '{"apps":[]}', "")
      ) // runCliRaw succeeds
      .mockImplementationOnce((...args) => {
        const cb = args[args.length - 1];
        cb(null); // writeFileElevated succeeds
      })
      .mockImplementationOnce((...args) => {
        const cb = args[args.length - 1];
        cb(new Error("Access denied")); // restartServiceElevated fails
      });

    await expect(handlers["update-config"](null, [100])).rejects.toThrow(
      "Service restart failed or was cancelled."
    );
  });
});
