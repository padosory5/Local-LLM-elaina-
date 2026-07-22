"use strict";

/*
 * ================================================================
 * SETTINGS: these are the values you will most likely want to change
 * ================================================================
 */
const CONFIG = {
    websocketUrl: "ws://127.0.0.1:8765",
    live2dModelPath: "./models/elaina/Elaina.model3.json",
    reconnectDelayMs: 2000,
    mouthSmoothing: 0.45, // Higher = faster mouth movement (range: 0 to 1).
    cursorUpdateIntervalMs: 33, // About 30 updates per second.
    cursorTrackingEnabled: true,
    conversationRailWidthRatio: 0,
    modelWidthRatio: 0.92,
    modelHeightRatio: 0.90,
    modelBottomMargin: 38
};

/* Store all HTML elements in one place so they are easy to find. */
const elements = {
    closeButton: document.getElementById("close-button"),
    minimizeButton: document.getElementById("minimize-button"),
    pinButton: document.getElementById("pin-button"),
    statusMessage: document.getElementById("status-message"),
    canvas: document.getElementById("live2d-canvas"),
    desktopCharacter: document.getElementById("desktop-character"),
    activityStatus: document.getElementById("activity-status"),
    activityText: document.getElementById("activity-text"),
    chatHistory: document.getElementById("chat-history"),
    chatDrawer: document.getElementById("chat-drawer"),
    chatToggleButton: document.getElementById("chat-toggle-button"),
    chatDrawerClose: document.getElementById("chat-drawer-close"),
    connectionStatus: document.getElementById("connection-status"),
    connectionText: document.getElementById("connection-text")
};

/* Values that change while the application is running. */
const state = {
    positionLocked: false,
    pixiApp: null,
    live2dModel: null,
    pythonSocket: null,
    reconnectTimer: null,
    userCaptionTimer: null,
    cursorTrackingTimer: null,
    targetMouthValue: 0,
    currentMouthValue: 0
};

/* -------------------------- Window controls -------------------------- */

function setupWindowControls() {
    elements.closeButton.addEventListener("click", () => {
        window.elainaDesktop?.closeWindow();
    });

    elements.minimizeButton.addEventListener("click", () => {
        window.elainaDesktop?.minimizeWindow();
    });

    elements.pinButton.addEventListener("click", () => {
        state.positionLocked = !state.positionLocked;
        elements.pinButton.classList.toggle("active", state.positionLocked);
        document.body.classList.toggle("position-locked", state.positionLocked);
        elements.pinButton.textContent = state.positionLocked ? "Locked" : "Pin";
        elements.pinButton.title = state.positionLocked
            ? "Unlock window position"
            : "Lock window position";
    });
}

/* -------------------------- Chat interface --------------------------- */

function setupChatDrawer() {
    elements.chatToggleButton.addEventListener("click", () => {
        elements.chatDrawer.classList.toggle("closed");
    });

    elements.chatDrawerClose.addEventListener("click", () => {
        elements.chatDrawer.classList.add("closed");
    });
}

function setConnectionState(className, text) {
    elements.connectionStatus.classList.remove("connected", "disconnected");

    if (className) {
        elements.connectionStatus.classList.add(className);
    }

    elements.connectionText.textContent = text;
}

function setActivity(activityName, text) {
    elements.activityStatus.className = activityName;
    elements.activityText.textContent = text;
}

function cleanText(text) {
    return String(text ?? "").trim();
}

function appendChatMessage(role, text) {
    const cleanedText = cleanText(text);
    if (!cleanedText) return;

    const message = document.createElement("div");
    message.className = `message ${role}`;
    message.textContent = cleanedText;
    elements.chatHistory.appendChild(message);
    elements.chatHistory.scrollTop = elements.chatHistory.scrollHeight;
}

function addUserMessage(text) {
    appendChatMessage("user", text);
}

function addAssistantMessage(text) {
    appendChatMessage("assistant", text);
}

/* ---------------------------- Live2D model --------------------------- */

async function loadElaina() {
    try {
        if (!window.PIXI) throw new Error("PixiJS did not load.");
        if (!window.PIXI.live2d) throw new Error("pixi-live2d-display did not load.");

        state.pixiApp = new PIXI.Application({
            view: elements.canvas,
            width: elements.desktopCharacter.clientWidth,
            height: elements.desktopCharacter.clientHeight,
            transparent: true,
            antialias: true,
            autoDensity: true,
            resolution: window.devicePixelRatio || 1,
            backgroundAlpha: 0
        });

        const { Live2DModel } = PIXI.live2d;
        state.live2dModel = await Live2DModel.from(CONFIG.live2dModelPath);
        state.pixiApp.stage.addChild(state.live2dModel);

        // Apply our lip-sync value immediately before each Live2D update.
        state.live2dModel.internalModel.on("beforeModelUpdate", updateMouth);
        fitModelToWindow();
        startCursorTracking();

        elements.statusMessage.classList.add("hidden");
        window.addEventListener("resize", resizeRenderer);
        console.log("Elaina loaded successfully.");
    } catch (error) {
        console.error("Failed to load Elaina:", error);
        elements.statusMessage.textContent = "Failed to load Elaina. Press Ctrl+Shift+I for details.";
    }
}

/*
 * Ask Electron for the system-wide cursor position and let Live2D smoothly
 * move the eyes, head, and body toward it. model.focus() performs Live2D's
 * built-in smoothing, so the movement is natural rather than instant.
 */
function startCursorTracking() {
    if (!CONFIG.cursorTrackingEnabled || !window.elainaDesktop?.getCursorState) return;

    clearInterval(state.cursorTrackingTimer);
    state.cursorTrackingTimer = setInterval(updateCursorFocus, CONFIG.cursorUpdateIntervalMs);
}

async function updateCursorFocus() {
    if (!state.live2dModel) return;

    try {
        const cursor = await window.elainaDesktop.getCursorState();
        if (!cursor) return;

        const localX = cursor.cursorX - cursor.windowX;
        const localY = cursor.cursorY - cursor.windowY;

        // Coordinates may be outside the window; Live2D safely limits the turn.
        state.live2dModel.focus(localX, localY, false);
    } catch (error) {
        console.error("Could not read cursor position:", error);
        clearInterval(state.cursorTrackingTimer);
    }
}

function updateMouth() {
    if (!state.live2dModel) return;

    // Move gradually toward the newest audio value for smoother animation.
    state.currentMouthValue +=
        (state.targetMouthValue - state.currentMouthValue) * CONFIG.mouthSmoothing;

    if (state.currentMouthValue < 0.01) state.currentMouthValue = 0;

    state.live2dModel.internalModel.coreModel.setParameterValueById(
        "ParamMouthOpenY",
        state.currentMouthValue
    );
}

function resizeRenderer() {
    if (!state.pixiApp || !state.live2dModel) return;

    state.pixiApp.renderer.resize(
        elements.desktopCharacter.clientWidth,
        elements.desktopCharacter.clientHeight
    );
    fitModelToWindow();
}

function fitModelToWindow() {
    const model = state.live2dModel;
    const app = state.pixiApp;
    if (!model || !app) return;

    model.scale.set(1);
    model.position.set(0, 0);

    const bounds = model.getLocalBounds();
    // Reserve the left side for text and fit the model only in the right region.
    const modelAreaLeft = app.screen.width * CONFIG.conversationRailWidthRatio;
    const modelAreaWidth = app.screen.width - modelAreaLeft;
    const availableWidth = modelAreaWidth * CONFIG.modelWidthRatio;
    const availableHeight = app.screen.height * CONFIG.modelHeightRatio;
    const scale = Math.min(
        availableWidth / bounds.width,
        availableHeight / bounds.height
    );

    model.scale.set(scale);
    model.x = modelAreaLeft + modelAreaWidth / 2 -
        (bounds.x + bounds.width / 2) * scale;
    model.y = app.screen.height - CONFIG.modelBottomMargin -
        (bounds.y + bounds.height) * scale;
    model.visible = true;
    model.alpha = 1;
}

/* ------------------------- Python WebSocket -------------------------- */

function connectToPython() {
    const socketIsActive = state.pythonSocket &&
        [WebSocket.OPEN, WebSocket.CONNECTING].includes(state.pythonSocket.readyState);
    if (socketIsActive) return;

    clearTimeout(state.reconnectTimer);
    setConnectionState("", "Connecting...");
    console.log(`Connecting to ${CONFIG.websocketUrl}...`);

    state.pythonSocket = new WebSocket(CONFIG.websocketUrl);

    state.pythonSocket.addEventListener("open", () => {
        setConnectionState("connected", "Connected");
        setActivity("listening", "Listening...");
        console.log("Connected to the Elaina Python backend.");
    });

    state.pythonSocket.addEventListener("message", handlePythonMessage);

    state.pythonSocket.addEventListener("close", () => {
        setConnectionState("disconnected", "Disconnected");
        setActivity("offline", "Elaina is offline");
        stopMouthMovement();
        state.pythonSocket = null;

        // Keep trying so the Electron app can be opened before main.py.
        state.reconnectTimer = setTimeout(connectToPython, CONFIG.reconnectDelayMs);
    });

    state.pythonSocket.addEventListener("error", error => {
        // The close event performs the actual reconnect.
        console.error("Python WebSocket error:", error);
    });
}

function handlePythonMessage(event) {
    try {
        const message = JSON.parse(event.data);

        switch (message.event) {
            case "user_message":
                addUserMessage(message.text);
                setActivity("thinking", "Thinking...");
                break;
            case "assistant_finished":
                addAssistantMessage(message.text);
                setActivity("speaking", "Speaking...");
                break;
            case "lip_sync":
                handleLipSync(message.value);
                break;
            case "tts_finished":
                setActivity("listening", "Listening...");
                stopMouthMovement();
                break;
            case "tts_interrupted":
            case "speech_started":
                setActivity("listening", "Listening...");
                stopMouthMovement();
                break;
            case "tts_started":
                setActivity("speaking", "Speaking...");
                break;
            default:
                console.log("Unhandled Python event:", message.event);
        }
    } catch (error) {
        console.error("Invalid WebSocket message:", event.data, error);
    }
}

function handleLipSync(rawValue) {
    const value = Number(rawValue);
    if (!Number.isFinite(value)) return;

    // Limit the value to the Live2D mouth range: 0 (closed) to 1 (open).
    state.targetMouthValue = Math.max(0, Math.min(1, value));
}

function stopMouthMovement() {
    state.targetMouthValue = 0;
    state.currentMouthValue = 0;
}

/* -------------------------- Start the app ---------------------------- */

function startApplication() {
    setupWindowControls();
    setupChatDrawer();
    loadElaina();
    connectToPython();
}

startApplication();
