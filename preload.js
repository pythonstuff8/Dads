const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("api", {
  selectFolder: () => ipcRenderer.invoke("select-folder"),
  startScan: (opts) => ipcRenderer.invoke("start-scan", opts),
  cancelScan: () => ipcRenderer.invoke("cancel-scan"),
  onBackendEvent: (callback) => {
    ipcRenderer.on("backend-event", (event, data) => callback(data));
  },
  windowMinimize: () => ipcRenderer.invoke("window-minimize"),
  windowMaximize: () => ipcRenderer.invoke("window-maximize"),
  windowClose: () => ipcRenderer.invoke("window-close"),
});
