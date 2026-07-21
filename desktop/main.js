const { app, BrowserWindow, ipcMain } = require("electron");
const path = require("path");

let mainWindow = null;

function createWindow() {
    mainWindow = new BrowserWindow({
        width: 420,
        height: 650,

        // Desktop companion appearance
        frame: false,
        transparent: true,
        backgroundColor: "#00000000",
        hasShadow: false,

        // Window behavior
        alwaysOnTop: true,
        resizable: false,
        movable: true,
        fullscreenable: false,
        skipTaskbar: false,

        webPreferences: {
            preload: path.join(__dirname, "preload.js"),
            contextIsolation: true,
            nodeIntegration: false
        }
    });

    mainWindow.loadFile(
        path.join(__dirname, "renderer", "index.html")
    );

    mainWindow.webContents.openDevTools({
        mode: "detach"
    });

    // Keep Elaina above normal windows.
    mainWindow.setAlwaysOnTop(true, "floating");

    // Place her near the bottom-right of the primary monitor.
    const { screen } = require("electron");
    const display = screen.getPrimaryDisplay();
    const workArea = display.workArea;

    const x = workArea.x + workArea.width - 440;
    const y = workArea.y + workArea.height - 670;

    mainWindow.setPosition(x, y);

    mainWindow.on("closed", () => {
        mainWindow = null;
    });
}

ipcMain.on("window-close", () => {
    mainWindow?.close();
});

ipcMain.on("window-minimize", () => {
    mainWindow?.minimize();
});

ipcMain.on("toggle-always-on-top", (_event, enabled) => {
    if (!mainWindow) {
        return;
    }

    mainWindow.setAlwaysOnTop(Boolean(enabled), "floating");
});

app.whenReady().then(() => {
    createWindow();

    app.on("activate", () => {
        if (BrowserWindow.getAllWindows().length === 0) {
            createWindow();
        }
    });
});

app.on("window-all-closed", () => {
    if (process.platform !== "darwin") {
        app.quit();
    }
});