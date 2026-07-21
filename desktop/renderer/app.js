const closeButton = document.getElementById("close-button");
const minimizeButton = document.getElementById("minimize-button");
const pinButton = document.getElementById("pin-button");
const statusMessage = document.getElementById("status-message");
const canvas = document.getElementById("live2d-canvas");

let alwaysOnTop = true;
let live2dModel = null;
let targetMouthValue = 0;
let currentMouthValue = 0;
let pythonSocket = null;

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

    window.elainaDesktop.setAlwaysOnTop(alwaysOnTop);
});

async function loadElaina() {
    try {
        if (!window.PIXI) {
            throw new Error("PixiJS did not load.");
        }

        if (!window.PIXI.live2d) {
            throw new Error(
                "pixi-live2d-display did not load."
            );
        }

        const pixiApp = new PIXI.Application({
            view: canvas,
            width: window.innerWidth,
            height: window.innerHeight,
            transparent: true,
            antialias: true,
            autoDensity: true,
            resolution: window.devicePixelRatio || 1,
            backgroundAlpha: 0
        });

        const { Live2DModel } = PIXI.live2d;

        live2dModel = await Live2DModel.from(
            "./models/elaina/Elaina.model3.json"
        );

        pixiApp.stage.addChild(live2dModel);

        live2dModel.internalModel.on(
            "beforeModelUpdate",
            () => {
                currentMouthValue +=
                    (targetMouthValue - currentMouthValue) * 0.45;

                if (currentMouthValue < 0.01) {
                    currentMouthValue = 0;
                }

                live2dModel.internalModel.coreModel
                    .setParameterValueById(
                        "ParamMouthOpenY",
                        currentMouthValue
                    );
            }
        );

        connectToPython();

        fitModelToWindow(live2dModel, pixiApp);

        statusMessage.classList.add("hidden");

        window.addEventListener("resize", () => {
            pixiApp.renderer.resize(
                window.innerWidth,
                window.innerHeight
            );

            fitModelToWindow(live2dModel, pixiApp);
        });

        console.log("Elaina loaded successfully.");
    } catch (error) {
        console.error("Failed to load Elaina:", error);

        statusMessage.textContent =
            "Failed to load Elaina. Press Ctrl+Shift+I.";
    }
}

function connectToPython() {
    if (
        pythonSocket &&
        (
            pythonSocket.readyState === WebSocket.OPEN ||
            pythonSocket.readyState === WebSocket.CONNECTING
        )
    ) {
        return;
    }

    console.log("Connecting to Python WebSocket...");

    pythonSocket = new WebSocket(
        "ws://127.0.0.1:8765"
    );

    pythonSocket.addEventListener("open", () => {
        console.log(
            "Connected to Elaina Python backend."
        );
    });

    pythonSocket.addEventListener("message", event => {
        try {
            const message = JSON.parse(event.data);

            if (message.event === "lip_sync") {
                const value = Number(message.value);

                if (Number.isFinite(value)) {
                    targetMouthValue = Math.max(
                        0,
                        Math.min(1, value)
                    );
                }
            }

            if (
                message.event === "tts_finished" ||
                message.event === "tts_interrupted"
            ) {
                targetMouthValue = 0;
            }
        } catch (error) {
            console.error(
                "Invalid WebSocket message:",
                event.data,
                error
            );
        }
    });

    pythonSocket.addEventListener("close", () => {
        console.log(
            "Python WebSocket disconnected. Retrying..."
        );

        targetMouthValue = 0;
        currentMouthValue = 0;
        pythonSocket = null;

        setTimeout(connectToPython, 2000);
    });

    pythonSocket.addEventListener("error", error => {
        console.error(
            "Python WebSocket error:",
            error
        );
    });
}

function fitModelToWindow(model, pixiApp) {
    // Use logical CSS dimensions, not physical WebGL pixel dimensions.
    const screenWidth = pixiApp.screen.width;
    const screenHeight = pixiApp.screen.height;

    model.scale.set(1);
    model.position.set(0, 0);

    const bounds = model.getLocalBounds();

    console.log("Screen size:", screenWidth, screenHeight);
    console.log("Model bounds:", bounds);

    const availableWidth = screenWidth * 0.95;
    const availableHeight = screenHeight * 0.95;

    const scaleX = availableWidth / bounds.width;
    const scaleY = availableHeight / bounds.height;
    const scale = Math.min(scaleX, scaleY);

    model.scale.set(scale);

    // Center horizontally.
    model.x =
        screenWidth / 2 -
        (bounds.x + bounds.width / 2) * scale;

    // Align the bottom of the model with the window bottom.
    model.y =
        screenHeight -
        (bounds.y + bounds.height) * scale;

    model.visible = true;
    model.alpha = 1;

    console.log("Model scale:", scale);
    console.log("Model position:", model.x, model.y);
}

loadElaina();

