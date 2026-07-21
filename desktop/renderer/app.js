const closeButton =
    document.getElementById("close-button");

const minimizeButton =
    document.getElementById("minimize-button");

const pinButton =
    document.getElementById("pin-button");

const statusMessage =
    document.getElementById("status-message");

const canvas =
    document.getElementById("live2d-canvas");

const desktopCharacter =
    document.getElementById("desktop-character");

const userCaption =
    document.getElementById("user-caption");

const userCaptionText =
    document.getElementById("user-caption-text");

const assistantBubble =
    document.getElementById("assistant-bubble");

const assistantBubbleText =
    document.getElementById("assistant-bubble-text");

const chatHistory =
    document.getElementById("chat-history");

const chatDrawer =
    document.getElementById("chat-drawer");

const chatToggleButton =
    document.getElementById("chat-toggle-button");

const chatDrawerClose =
    document.getElementById("chat-drawer-close");

const connectionStatus =
    document.getElementById("connection-status");

const connectionText =
    document.getElementById("connection-text");


let alwaysOnTop = true;

let live2dModel = null;
let pixiApp = null;

let targetMouthValue = 0;
let currentMouthValue = 0;

let pythonSocket = null;
let reconnectTimeout = null;

let activeAssistantMessage = null;

let userCaptionTimeout = null;
let assistantBubbleTimeout = null;


/*
 * Window controls
 */

pinButton.classList.add("active");

closeButton.addEventListener("click", () => {
    window.elainaDesktop.closeWindow();
});

minimizeButton.addEventListener("click", () => {
    window.elainaDesktop.minimizeWindow();
});

pinButton.addEventListener("click", () => {
    alwaysOnTop = !alwaysOnTop;

    pinButton.classList.toggle(
        "active",
        alwaysOnTop
    );

    window.elainaDesktop.setAlwaysOnTop(
        alwaysOnTop
    );
});


/*
 * Chat drawer controls
 */

chatToggleButton.addEventListener("click", () => {
    chatDrawer.classList.toggle("closed");
});

chatDrawerClose.addEventListener("click", () => {
    chatDrawer.classList.add("closed");
});


/*
 * Connection status
 */

function setConnectionState(state, text) {
    connectionStatus.classList.remove(
        "connected",
        "disconnected"
    );

    if (state) {
        connectionStatus.classList.add(state);
    }

    connectionText.textContent = text;
}


/*
 * User-message UI
 */

function showUserCaption(text) {
    const cleanedText = String(text || "").trim();

    if (!cleanedText) {
        return;
    }

    clearTimeout(userCaptionTimeout);

    userCaptionText.textContent = cleanedText;

    userCaption.classList.remove("hidden");

    console.log(
        "Showing user bubble:",
        cleanedText
    );

    userCaptionTimeout = setTimeout(() => {
        userCaption.classList.add("hidden");
    }, 5000);
}

function addUserMessage(text) {
    const cleanedText = String(text || "").trim();

    if (!cleanedText) {
        return;
    }

    showUserCaption(cleanedText);

    const messageElement =
        document.createElement("div");

    messageElement.className = "message user";
    messageElement.textContent = cleanedText;

    chatHistory.appendChild(messageElement);

    scrollChatToBottom();
}


/*
 * Assistant-message UI
 */

function beginAssistantResponse() {
    clearTimeout(assistantBubbleTimeout);

    assistantBubbleText.textContent = "";

    assistantBubble.classList.remove("hidden");

    activeAssistantMessage =
        document.createElement("div");

    activeAssistantMessage.className =
        "message assistant";

    chatHistory.appendChild(
        activeAssistantMessage
    );

    scrollChatToBottom();

    console.log(
        "Started assistant bubble"
    );
}

function appendAssistantChunk(text) {
    const chunk = String(text || "");

    if (!chunk) {
        return;
    }

    clearTimeout(assistantBubbleTimeout);

    if (!activeAssistantMessage) {
        beginAssistantResponse();
    }

    assistantBubble.classList.remove("hidden");

    assistantBubbleText.textContent += chunk;
    activeAssistantMessage.textContent += chunk;

    scrollChatToBottom();

    console.log(
        "Assistant bubble chunk:",
        chunk
    );
}

function finishAssistantResponse(finalText = "") {
    const text = String(finalText || "");

    if (!activeAssistantMessage && text) {
        beginAssistantResponse();
    }

    if (
        activeAssistantMessage &&
        !activeAssistantMessage.textContent &&
        text
    ) {
        activeAssistantMessage.textContent = text;
        assistantBubbleText.textContent = text;
    }

    assistantBubble.classList.remove("hidden");

    activeAssistantMessage = null;

    clearTimeout(assistantBubbleTimeout);

    assistantBubbleTimeout = setTimeout(() => {
        assistantBubble.classList.add("hidden");
    }, 8000);
}

function scrollChatToBottom() {
    chatHistory.scrollTop =
        chatHistory.scrollHeight;
}


/*
 * Live2D setup
 */

async function loadElaina() {
    try {
        if (!window.PIXI) {
            throw new Error(
                "PixiJS did not load."
            );
        }

        if (!window.PIXI.live2d) {
            throw new Error(
                "pixi-live2d-display did not load."
            );
        }

        pixiApp = new PIXI.Application({
            view: canvas,

            width:
                desktopCharacter.clientWidth,

            height:
                desktopCharacter.clientHeight,

            transparent: true,
            antialias: true,
            autoDensity: true,

            resolution:
                window.devicePixelRatio || 1,

            backgroundAlpha: 0
        });

        const { Live2DModel } =
            PIXI.live2d;

        live2dModel =
            await Live2DModel.from(
                "./models/elaina/Elaina.model3.json"
            );

        pixiApp.stage.addChild(
            live2dModel
        );

        live2dModel.internalModel.on(
            "beforeModelUpdate",
            updateMouth
        );

        fitModelToWindow(
            live2dModel,
            pixiApp
        );

        statusMessage.classList.add(
            "hidden"
        );

        window.addEventListener(
            "resize",
            resizeRenderer
        );

        console.log(
            "Elaina loaded successfully."
        );
    } catch (error) {
        console.error(
            "Failed to load Elaina:",
            error
        );

        statusMessage.textContent =
            "Failed to load Elaina. Press Ctrl+Shift+I.";
    }
}

function updateMouth() {
    if (!live2dModel) {
        return;
    }

    currentMouthValue +=
        (
            targetMouthValue -
            currentMouthValue
        ) * 0.45;

    if (currentMouthValue < 0.01) {
        currentMouthValue = 0;
    }

    live2dModel.internalModel.coreModel
        .setParameterValueById(
            "ParamMouthOpenY",
            currentMouthValue
        );
}

function resizeRenderer() {
    if (!pixiApp || !live2dModel) {
        return;
    }

    pixiApp.renderer.resize(
        desktopCharacter.clientWidth,
        desktopCharacter.clientHeight
    );

    fitModelToWindow(
        live2dModel,
        pixiApp
    );
}

function fitModelToWindow(model, application) {
    const screenWidth =
        application.screen.width;

    const screenHeight =
        application.screen.height;

    model.scale.set(1);
    model.position.set(0, 0);

    const bounds =
        model.getLocalBounds();

    /*
     * Leave room near the top for speech bubbles
     * and near the bottom for the status bar.
     */
    const availableWidth =
        screenWidth * 0.95;

    const availableHeight =
        screenHeight * 0.82;

    const scaleX =
        availableWidth / bounds.width;

    const scaleY =
        availableHeight / bounds.height;

    const scale =
        Math.min(scaleX, scaleY);

    model.scale.set(scale);

    model.x =
        screenWidth / 2 -
        (
            bounds.x +
            bounds.width / 2
        ) * scale;

    model.y =
        screenHeight -
        38 -
        (
            bounds.y +
            bounds.height
        ) * scale;

    model.visible = true;
    model.alpha = 1;

    console.log(
        "Model scale:",
        scale
    );

    console.log(
        "Model position:",
        model.x,
        model.y
    );
}


/*
 * Python WebSocket
 */

function connectToPython() {
    if (
        pythonSocket &&
        (
            pythonSocket.readyState ===
                WebSocket.OPEN ||
            pythonSocket.readyState ===
                WebSocket.CONNECTING
        )
    ) {
        return;
    }

    clearTimeout(reconnectTimeout);

    console.log(
        "Connecting to Python WebSocket..."
    );

    setConnectionState(
        "",
        "Connecting..."
    );

    pythonSocket = new WebSocket(
        "ws://127.0.0.1:8765"
    );

    pythonSocket.addEventListener(
        "open",
        () => {
            console.log(
                "Connected to Elaina Python backend."
            );

            setConnectionState(
                "connected",
                "Connected"
            );
        }
    );

    pythonSocket.addEventListener(
        "message",
        handlePythonMessage
    );

    pythonSocket.addEventListener(
        "close",
        () => {
            console.log(
                "Python WebSocket disconnected. Retrying..."
            );

            setConnectionState(
                "disconnected",
                "Disconnected"
            );

            targetMouthValue = 0;
            currentMouthValue = 0;
            pythonSocket = null;

            reconnectTimeout = setTimeout(
                connectToPython,
                2000
            );
        }
    );

    pythonSocket.addEventListener(
        "error",
        error => {
            console.error(
                "Python WebSocket error:",
                error
            );
        }
    );
}

function handlePythonMessage(event) {
    try {
        const message =
            JSON.parse(event.data);

        console.log(
            "Python event:",
            message
        );

        switch (message.event) {
            case "user_message":
                addUserMessage(
                    message.text
                );
                break;

            case "assistant_started":
                beginAssistantResponse();
                break;

            case "assistant_stream":
                appendAssistantChunk(
                    message.text
                );
                break;

            case "assistant_finished":
                finishAssistantResponse(
                    message.text
                );
                break;

            case "lip_sync":
                handleLipSync(
                    message.value
                );
                break;

            case "tts_started":
                break;

            case "tts_finished":
            case "tts_interrupted":
                targetMouthValue = 0;
                break;

            case "speech_started":
                targetMouthValue = 0;
                break;

            default:
                console.log(
                    "Unhandled Python event:",
                    message.event
                );
                break;
        }
    } catch (error) {
        console.error(
            "Invalid WebSocket message:",
            event.data,
            error
        );
    }
}

function handleLipSync(rawValue) {
    const value = Number(rawValue);

    if (!Number.isFinite(value)) {
        return;
    }

    targetMouthValue = Math.max(
        0,
        Math.min(1, value)
    );
}


/*
 * Start application
 */


loadElaina();
connectToPython();
