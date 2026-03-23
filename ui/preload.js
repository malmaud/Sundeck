const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("api", {
  getGames: (count) => ipcRenderer.invoke("get-games", count),
  updateConfig: (appIds) => ipcRenderer.invoke("update-config", appIds),
  getCurrentConfig: () => ipcRenderer.invoke("get-current-config"),
});
