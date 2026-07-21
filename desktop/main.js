const { app, BrowserWindow, ipcMain } = require("electron");
const path = require("path");

const WINDOW_WIDTH = 420;
const WINDOW_HEIGHT = 650;

let mainWindow = null;

function createWindow() {
    mainWindow = new BrowserWindow({
        width: WINDOW_WIDTH,
        height: WINDOW_HEIGHT,

        frame: false,
        transparent: true,
        backgroundColor: "#00000000",
        hasShadow: false,

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

    mainWindow.setAlwaysOnTop(true, "floating");

    const { screen } = require("electron");
    const display = screen.getPrimaryDisplay();
    const workArea = display.workArea;
    const margin = 20;

    const x =
        workArea.x +
        workArea.width -
        WINDOW_WIDTH -
        margin;

    const y =
        workArea.y +
        workArea.height -
        WINDOW_HEIGHT -
        margin;

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

    mainWindow.setAlwaysOnTop(
        Boolean(enabled),
        "floating"
    );
});

app.whenReady().then(() => {
    createWindow();

    app.on("activate", () => {
        if (
            BrowserWindow.getAllWindows().length === 0
        ) {
            createWindow();
        }
    });
});

app.on("window-all-closed", () => {
    if (process.platform !== "darwin") {
        app.quit();
    }
});