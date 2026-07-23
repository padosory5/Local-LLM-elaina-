"use strict";

const MINIMUM_SIZE = 40;

const surface = document.getElementById("selection-surface");
const box = document.getElementById("selection-box");
const analyzeButton = document.getElementById("analyze-selection");
const cancelButton = document.getElementById("cancel-selection");

const state = {
    mode: null,
    resizeDirection: "",
    startX: 0,
    startY: 0,
    initial: null,
    rectangle: null
};

function clamp(value, minimum, maximum) {
    return Math.max(minimum, Math.min(maximum, value));
}

function readRectangle() {
    return {
        x: box.offsetLeft,
        y: box.offsetTop,
        width: box.offsetWidth,
        height: box.offsetHeight
    };
}

function drawRectangle(rectangle) {
    const width = Math.max(MINIMUM_SIZE, rectangle.width);
    const height = Math.max(MINIMUM_SIZE, rectangle.height);
    const x = clamp(rectangle.x, 0, window.innerWidth - width);
    const y = clamp(rectangle.y, 0, window.innerHeight - height);

    state.rectangle = { x, y, width, height };
    box.classList.toggle(
        "actions-above",
        y + height + 54 > window.innerHeight
    );

    Object.assign(box.style, {
        left: `${x}px`,
        top: `${y}px`,
        width: `${width}px`,
        height: `${height}px`
    });

    box.classList.remove("hidden");
}

function beginCreate(event) {
    state.mode = "create";
    state.startX = event.clientX;
    state.startY = event.clientY;

    drawRectangle({
        x: event.clientX,
        y: event.clientY,
        width: MINIMUM_SIZE,
        height: MINIMUM_SIZE
    });
}

function beginMove(event) {
    state.mode = "move";
    state.startX = event.clientX;
    state.startY = event.clientY;
    state.initial = readRectangle();
}

function beginResize(event, direction) {
    state.mode = "resize";
    state.resizeDirection = direction;
    state.startX = event.clientX;
    state.startY = event.clientY;
    state.initial = readRectangle();
}

function updateCreate(event) {
    drawRectangle({
        x: Math.min(state.startX, event.clientX),
        y: Math.min(state.startY, event.clientY),
        width: Math.abs(event.clientX - state.startX),
        height: Math.abs(event.clientY - state.startY)
    });
}

function updateMove(event) {
    drawRectangle({
        ...state.initial,
        x: state.initial.x + event.clientX - state.startX,
        y: state.initial.y + event.clientY - state.startY
    });
}

function updateResize(event) {
    const direction = state.resizeDirection;
    const deltaX = event.clientX - state.startX;
    const deltaY = event.clientY - state.startY;
    let { x, y, width, height } = state.initial;

    if (direction.includes("e")) {
        width += deltaX;
    }
    if (direction.includes("s")) {
        height += deltaY;
    }
    if (direction.includes("w")) {
        x += deltaX;
        width -= deltaX;
    }
    if (direction.includes("n")) {
        y += deltaY;
        height -= deltaY;
    }

    if (width < MINIMUM_SIZE) {
        if (direction.includes("w")) {
            x -= MINIMUM_SIZE - width;
        }
        width = MINIMUM_SIZE;
    }

    if (height < MINIMUM_SIZE) {
        if (direction.includes("n")) {
            y -= MINIMUM_SIZE - height;
        }
        height = MINIMUM_SIZE;
    }

    drawRectangle({ x, y, width, height });
}

surface.addEventListener("mousedown", event => {
    if (event.button !== 0) {
        return;
    }

    if (event.target.closest("#selection-actions")) {
        return;
    }

    const resizeHandle = event.target.closest("[data-resize]");
    if (resizeHandle) {
        beginResize(event, resizeHandle.dataset.resize);
    } else if (event.target.closest("#selection-box")) {
        beginMove(event);
    } else {
        beginCreate(event);
    }

    event.preventDefault();
});

window.addEventListener("mousemove", event => {
    if (state.mode === "create") {
        updateCreate(event);
    } else if (state.mode === "move") {
        updateMove(event);
    } else if (state.mode === "resize") {
        updateResize(event);
    }
});

window.addEventListener("mouseup", () => {
    state.mode = null;
    state.resizeDirection = "";
    state.initial = null;
});

analyzeButton.addEventListener("click", event => {
    event.stopPropagation();

    if (!state.rectangle) {
        return;
    }

    window.elainaDesktop?.confirmScreenSelection(
        readRectangle()
    );
});

cancelButton.addEventListener("click", event => {
    event.stopPropagation();
    window.elainaDesktop?.cancelScreenSelection();
});

window.addEventListener("keydown", event => {
    if (event.key === "Escape") {
        window.elainaDesktop?.cancelScreenSelection();
    }
});
