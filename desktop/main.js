const {
    app,
    BrowserWindow,
    ipcMain,
    screen
} = require("electron");

const { spawn } = require("child_process");
const path = require("path");

const WINDOW_WIDTH = 420;
const WINDOW_HEIGHT = 650;

let mainWindow = null;
let selectionWindow = null;
let selectionDesktopBounds = null;
let pythonProcess = null;
let isQuitting = false;

/*
 * Start the Python backend.
 *
 * Electron owns this process, allowing it to stop Python
 * automatically when the Electron application closes.
 */
function startPythonBackend() {
    if (pythonProcess) {
        return;
    }

    const projectFolder = path.resolve(__dirname, "..");

    const pythonExecutable = path.join(
        projectFolder,
        ".venv",
        "Scripts",
        "python.exe"
    );

    const mainScript = path.join(
        projectFolder,
        "main.py"
    );

    pythonProcess = spawn(
        pythonExecutable,
        [mainScript],
        {
            cwd: projectFolder,

            // Prevent Python from opening a command window.
            windowsHide: true,

            // Python terminal output will be hidden.
            stdio: "ignore"
        }
    );

    pythonProcess.on("error", (error) => {
        console.error(
            "Failed to start Python backend:",
            error
        );

        pythonProcess = null;
    });

    pythonProcess.on("exit", () => {
        pythonProcess = null;
    });
}

/*
 * Stop the Python backend.
 *
 * On Windows, taskkill also closes any child processes
 * created by Python.
 */
function stopPythonBackend() {
    if (!pythonProcess?.pid) {
        return;
    }

    const backendPid = pythonProcess.pid;
    pythonProcess = null;

    if (process.platform === "win32") {
        spawn(
            "taskkill",
            [
                "/pid",
                String(backendPid),
                "/T",
                "/F"
            ],
            {
                windowsHide: true,
                stdio: "ignore"
            }
        );

        return;
    }

    try {
        process.kill(
            backendPid,
            "SIGTERM"
        );
    } catch (error) {
        console.error(
            "Failed to stop Python backend:",
            error
        );
    }
}

/*
 * Create the transparent Electron window containing Elaina.
 */
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
            preload: path.join(
                __dirname,
                "preload.js"
            ),

            contextIsolation: true,
            nodeIntegration: false
        }
    });

    mainWindow.loadFile(
        path.join(
            __dirname,
            "renderer",
            "index.html"
        )
    );

    mainWindow.setAlwaysOnTop(
        true,
        "floating"
    );

    /*
     * Place Elaina near the bottom-right corner
     * of the primary monitor.
     */
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

    /*
     * Uncomment this while debugging:
     *
     * mainWindow.webContents.openDevTools({
     *     mode: "detach"
     * });
     */
}

/*
 * Return one rectangle covering every connected display.
 * Coordinates may be negative when a monitor is left of the primary display.
 */
function getVirtualDesktopBounds() {
    const displays = screen.getAllDisplays();

    const left = Math.min(...displays.map(display => display.bounds.x));
    const top = Math.min(...displays.map(display => display.bounds.y));
    const right = Math.max(
        ...displays.map(display => display.bounds.x + display.bounds.width)
    );
    const bottom = Math.max(
        ...displays.map(display => display.bounds.y + display.bounds.height)
    );

    return {
        x: left,
        y: top,
        width: right - left,
        height: bottom - top
    };
}

/*
 * Open a transparent overlay where the user can create, move, and resize
 * the region that Elaina should analyze.
 */
function openScreenSelector() {
    if (selectionWindow && !selectionWindow.isDestroyed()) {
        selectionWindow.focus();
        return;
    }

    selectionDesktopBounds = getVirtualDesktopBounds();

    selectionWindow = new BrowserWindow({
        ...selectionDesktopBounds,
        frame: false,
        transparent: true,
        backgroundColor: "#00000000",
        hasShadow: false,
        alwaysOnTop: true,
        skipTaskbar: true,
        resizable: false,
        movable: false,
        fullscreenable: false,
        webPreferences: {
            preload: path.join(__dirname, "preload.js"),
            contextIsolation: true,
            nodeIntegration: false
        }
    });

    selectionWindow.setAlwaysOnTop(true, "screen-saver");
    selectionWindow.loadFile(
        path.join(__dirname, "screen-selector.html")
    );

    selectionWindow.on("closed", () => {
        selectionWindow = null;
        selectionDesktopBounds = null;
    });
}

function closeScreenSelector() {
    if (selectionWindow && !selectionWindow.isDestroyed()) {
        selectionWindow.close();
    }
}

/*
 * Window buttons used by renderer/app.js.
 */
ipcMain.on("window-close", () => {
    mainWindow?.close();
});

ipcMain.on("window-minimize", () => {
    mainWindow?.minimize();
});

ipcMain.on("open-screen-selector", () => {
    openScreenSelector();
});

ipcMain.on("screen-selection-cancel", () => {
    closeScreenSelector();
});

ipcMain.on("screen-selection-confirm", (_event, region) => {
    if (!selectionDesktopBounds || !mainWindow || !region) {
        closeScreenSelector();
        return;
    }

    const values = [
        region.x,
        region.y,
        region.width,
        region.height
    ].map(Number);

    if (
        values.some(value => !Number.isFinite(value)) ||
        values[2] < 20 ||
        values[3] < 20
    ) {
        return;
    }

    const selectedRegion = {
        left: Math.round(selectionDesktopBounds.x + values[0]),
        top: Math.round(selectionDesktopBounds.y + values[1]),
        width: Math.round(values[2]),
        height: Math.round(values[3])
    };

    // Remove the overlay before Python captures the selected pixels.
    closeScreenSelector();

    setTimeout(() => {
        if (mainWindow && !mainWindow.isDestroyed()) {
            mainWindow.webContents.send(
                "screen-region-selected",
                selectedRegion
            );
        }
    }, 100);
});

/*
 * Pin or unpin Elaina above other applications.
 */
ipcMain.on(
    "toggle-always-on-top",
    (_event, enabled) => {
        if (!mainWindow) {
            return;
        }

        mainWindow.setAlwaysOnTop(
            Boolean(enabled),
            "floating"
        );
    }
);

/*
 * Return the global mouse position and Elaina's window
 * position to the renderer.
 *
 * renderer/app.js uses these values to make the Live2D
 * model look toward the mouse cursor.
 */
ipcMain.handle(
    "get-cursor-state",
    () => {
        if (!mainWindow) {
            return null;
        }

        const cursor =
            screen.getCursorScreenPoint();

        const windowBounds =
            mainWindow.getBounds();

        return {
            cursorX: cursor.x,
            cursorY: cursor.y,
            windowX: windowBounds.x,
            windowY: windowBounds.y
        };
    }
);

/*
 * Start Python and create the Electron window.
 */
app.whenReady().then(() => {
    startPythonBackend();
    createWindow();

    app.on("activate", () => {
        if (
            BrowserWindow
                .getAllWindows()
                .length === 0
        ) {
            createWindow();
        }
    });
});

/*
 * Closing the final window quits Electron.
 */
app.on("window-all-closed", () => {
    if (process.platform !== "darwin") {
        app.quit();
    }
});

/*
 * Stop Python before Electron finishes quitting.
 */
app.on("before-quit", () => {
    if (isQuitting) {
        return;
    }

    isQuitting = true;
    closeScreenSelector();
    stopPythonBackend();
});
