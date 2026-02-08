const { app, BrowserWindow, ipcMain, dialog } = require("electron");
const path = require("path");
const { spawn } = require("child_process");
const readline = require("readline");

let mainWindow;
let pythonProcess = null;

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 900,
    height: 700,
    minWidth: 750,
    minHeight: 550,
    frame: false,
    transparent: false,
    backgroundColor: "#0a0a0f",
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
    },
    icon: path.join(__dirname, "icon.png"),
  });

  mainWindow.loadFile(path.join(__dirname, "renderer", "index.html"));

  mainWindow.on("closed", () => {
    mainWindow = null;
    killPython();
  });
}

function findPython() {
  // Try common Python executable names
  const candidates = ["python", "python3", "py"];
  return candidates[0]; // Default to 'python', will fail gracefully
}

function spawnPython() {
  if (pythonProcess) return;

  const pythonCmd = findPython();
  const backendPath = path.join(__dirname, "backend.py");

  pythonProcess = spawn(pythonCmd, [backendPath], {
    stdio: ["pipe", "pipe", "pipe"],
    cwd: __dirname,
  });

  const rl = readline.createInterface({ input: pythonProcess.stdout });

  rl.on("line", (line) => {
    try {
      const msg = JSON.parse(line);
      if (mainWindow && !mainWindow.isDestroyed()) {
        mainWindow.webContents.send("backend-event", msg);
      }
    } catch (e) {
      // Ignore non-JSON output
    }
  });

  pythonProcess.stderr.on("data", (data) => {
    const errMsg = data.toString().trim();
    if (errMsg && mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.webContents.send("backend-event", {
        event: "error",
        message: errMsg,
      });
    }
  });

  pythonProcess.on("close", (code) => {
    pythonProcess = null;
    if (mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.webContents.send("backend-event", {
        event: "process-exit",
        code,
      });
    }
  });
}

function sendToPython(cmd) {
  if (!pythonProcess) {
    spawnPython();
  }
  if (pythonProcess && pythonProcess.stdin.writable) {
    pythonProcess.stdin.write(JSON.stringify(cmd) + "\n");
  }
}

function killPython() {
  if (pythonProcess) {
    try {
      pythonProcess.stdin.write(JSON.stringify({ cmd: "quit" }) + "\n");
    } catch (e) {
      // ignore
    }
    setTimeout(() => {
      if (pythonProcess) {
        pythonProcess.kill();
        pythonProcess = null;
      }
    }, 1000);
  }
}

// IPC Handlers
ipcMain.handle("select-folder", async () => {
  const result = await dialog.showOpenDialog(mainWindow, {
    properties: ["openDirectory"],
  });
  if (result.canceled) return null;
  return result.filePaths[0];
});

ipcMain.handle("start-scan", async (event, { source, output, threshold }) => {
  spawnPython();
  sendToPython({ cmd: "scan", source, output, threshold });
  return true;
});

ipcMain.handle("cancel-scan", async () => {
  sendToPython({ cmd: "cancel" });
  return true;
});

ipcMain.handle("window-minimize", () => {
  mainWindow.minimize();
});

ipcMain.handle("window-maximize", () => {
  if (mainWindow.isMaximized()) {
    mainWindow.unmaximize();
  } else {
    mainWindow.maximize();
  }
});

ipcMain.handle("window-close", () => {
  mainWindow.close();
});

app.whenReady().then(createWindow);

app.on("window-all-closed", () => {
  killPython();
  app.quit();
});

app.on("activate", () => {
  if (BrowserWindow.getAllWindows().length === 0) {
    createWindow();
  }
});
