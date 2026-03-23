const { app, BrowserWindow, ipcMain } = require("electron");
const { execFile } = require("child_process");
const fs = require("fs");
const os = require("os");
const path = require("path");

const PYTHON_DIR = path.resolve(__dirname, "..", "python");
const UV = process.env.UV_PATH || "uv";
const APOLLO_CONFIG =
  process.env.APOLLO_CONFIG ||
  "C:\\Program Files\\Apollo\\config\\apps.json";

function runCli(...args) {
  return new Promise((resolve, reject) => {
    execFile(
      UV,
      ["run", "cli.py", ...args],
      { cwd: PYTHON_DIR },
      (err, stdout, stderr) => {
        if (err) return reject(new Error(stderr || err.message));
        try {
          resolve(JSON.parse(stdout));
        } catch {
          reject(new Error(`Bad JSON: ${stdout}`));
        }
      }
    );
  });
}

function runCliRaw(...args) {
  return new Promise((resolve, reject) => {
    execFile(
      UV,
      ["run", "cli.py", ...args],
      { cwd: PYTHON_DIR },
      (err, stdout, stderr) => {
        if (err) return reject(new Error(stderr || err.message));
        resolve(stdout);
      }
    );
  });
}

function encodeCommand(cmd) {
  return Buffer.from(cmd, "utf16le").toString("base64");
}

function runElevated(innerCmd) {
  // Use execFile to bypass cmd.exe quoting entirely.
  // EncodedCommand avoids any quoting issues in the inner command.
  const encoded = encodeCommand(innerCmd);
  return new Promise((resolve, reject) => {
    execFile(
      "powershell.exe",
      [
        "-NoProfile",
        "-Command",
        `Start-Process powershell -Verb RunAs -Wait -ArgumentList '-NoProfile -NonInteractive -EncodedCommand ${encoded}'`,
      ],
      (err) => {
        if (err) return reject(err);
        resolve();
      }
    );
  });
}

function writeFileElevated(targetPath, content) {
  return new Promise((resolve, reject) => {
    const tmp = path.join(os.tmpdir(), `steamlaunch-${Date.now()}.json`);
    fs.writeFileSync(tmp, content, "utf-8");
    const innerCmd = `Copy-Item -LiteralPath '${tmp}' -Destination '${targetPath}' -Force; Remove-Item -LiteralPath '${tmp}'`;
    runElevated(innerCmd)
      .then(resolve)
      .catch(() => reject(new Error("Elevated write failed or was cancelled.")));
  });
}

function restartServiceElevated() {
  return runElevated("net stop ApolloService; net start ApolloService").catch(
    () => {
      throw new Error("Service restart failed or was cancelled.");
    }
  );
}

function createWindow() {
  const win = new BrowserWindow({
    width: 960,
    height: 680,
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
    },
  });
  win.loadFile("index.html");
}

app.whenReady().then(createWindow);

app.on("window-all-closed", () => app.quit());

ipcMain.handle("get-games", (_e, count) => runCli("games", `--count=${count}`));

ipcMain.handle("update-config", async (_e, appIds) => {
  const configJson = await runCliRaw("build", `--app_ids=${appIds.join(",")}`);
  await writeFileElevated(APOLLO_CONFIG, configJson);
  await restartServiceElevated();
  return { status: "ok", count: appIds.length };
});
