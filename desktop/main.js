const {
    app,
    BrowserWindow,
    ipcMain,
    screen,
    shell
} = require("electron");

const { spawn } = require("child_process");
const path = require("path");
const fs = require("fs");

/*
 * Where the renderer's console goes.
 *
 * On Windows a GUI process has no console attached, so console.log from this
 * process is written to a handle nobody is reading -- which is why a window
 * that silently fails looks exactly like a backend that sent nothing. A file
 * is readable afterwards, from any terminal, without DevTools.
 */
const RENDERER_LOG_PATH = path.resolve(
    __dirname,
    "..",
    "runtime",
    "renderer.log"
);

function writeRendererLine(line) {
    console.log(line);
    try {
        fs.mkdirSync(path.dirname(RENDERER_LOG_PATH), { recursive: true });
        fs.appendFileSync(RENDERER_LOG_PATH, `${line}\n`, "utf8");
    } catch (error) {
        // Logging must never be the reason the window stops working.
    }
}

const WINDOW_WIDTH = 420;
const WINDOW_HEIGHT = 650;

let mainWindow = null;
let selectionWindow = null;
let selectionDesktopBounds = null;
let pythonProcess = null;
let isQuitting = false;
const pythonOwnedExternally =
    process.env.ELAINA_PYTHON_OWNS_BACKEND === "1";

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
            stdio: "ignore",
            env: {
                ...process.env,
                ELAINA_STARTED_BY_ELECTRON: "1"
            }
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
     * Carry the renderer's console into this process's stdout.
     *
     * Without this the only way to see what the window is doing is to open
     * DevTools by hand, so a renderer that silently fails is indistinguishable
     * from a backend that sent nothing. Electron's stdout is inherited from
     * whatever started it, which means `python main.py` in a terminal now
     * shows the backend and the window in one stream.
     */
    try {
        fs.mkdirSync(path.dirname(RENDERER_LOG_PATH), { recursive: true });
        fs.writeFileSync(RENDERER_LOG_PATH, "", "utf8");
    } catch (error) {
        // Not fatal; appending to a stale log is still better than nothing.
    }
    writeRendererLine(
        `[Renderer] console attached; renderer log at ${RENDERER_LOG_PATH}`
    );

    mainWindow.webContents.on(
        "console-message",
        (...args) => {
            // Electron 35 replaced the positional signature with one event
            // object. Accept both so this keeps working across upgrades.
            const details = args[0] && typeof args[0].message === "string"
                ? args[0]
                : { message: args[2], level: args[1] };

            const stamp = new Date().toISOString().slice(11, 23);
            writeRendererLine(`[Renderer ${stamp}] ${details.message}`);
        }
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

/*
 * Open a card's page in the user's own browser.
 *
 * The address originates in the backend -- a candidate a real search
 * returned -- and is checked once more here rather than trusted, because
 * this process is the one that can actually launch things. Anything that is
 * not a plain web address is dropped without comment: there is no reason
 * for a card to point at a file, a script, or an application.
 */
ipcMain.on("open-external", (_event, url) => {
    const address = String(url || "").trim();

    if (!/^https?:\/\//i.test(address) || address.length > 2048) {
        writeRendererLine(`[Renderer] refused to open ${address.slice(0, 80)}`);
        return;
    }

    writeRendererLine(`[Renderer] opening externally: ${address}`);
    shell.openExternal(address);
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

    // The selector renderer reports coordinates in Electron DIP units.
    // Windows screen capture APIs such as MSS use physical pixel coordinates.
    // At 125%, 150%, or 200% display scaling, sending DIP values directly
    // captures a different area of the screen.
    const selectedDipRegion = {
        left: Math.round(selectionDesktopBounds.x + values[0]),
        top: Math.round(selectionDesktopBounds.y + values[1]),
        width: Math.round(values[2]),
        height: Math.round(values[3])
    };

    const dipStart = {
        x: selectedDipRegion.left,
        y: selectedDipRegion.top
    };
    const dipEnd = {
        x: selectedDipRegion.left + selectedDipRegion.width,
        y: selectedDipRegion.top + selectedDipRegion.height
    };

    let selectedRegion;

    if (process.platform === "win32") {
        // Electron selects the correct display scale factor for each point.
        // Converting both corners also works when monitors have different DPI.
        const pixelStart = screen.dipToScreenPoint(dipStart);
        const pixelEnd = screen.dipToScreenPoint(dipEnd);

        selectedRegion = {
            left: Math.min(pixelStart.x, pixelEnd.x),
            top: Math.min(pixelStart.y, pixelEnd.y),
            width: Math.abs(pixelEnd.x - pixelStart.x),
            height: Math.abs(pixelEnd.y - pixelStart.y)
        };
    } else {
        selectedRegion = selectedDipRegion;
    }

    console.log("[Screen Selection] DIP region:", selectedDipRegion);
    console.log("[Screen Selection] Pixel region:", selectedRegion);

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
    if (!pythonOwnedExternally) {
        startPythonBackend();
    }
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
    if (!pythonOwnedExternally) {
        stopPythonBackend();
    }
});
