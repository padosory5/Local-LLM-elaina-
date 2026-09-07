"use strict";

/*
 * Proof of identity, printed before anything else can go wrong. When the
 * window shows nothing, the first question is whether this file is the one
 * running: Electron caches, a copied asset looks identical from outside,
 * and every later checkpoint is worthless if the answer is no.
 */
const RENDERER_BUILD = "4F.6-surface-trace";
console.log(
    `[Surface UI] renderer loaded: build=${RENDERER_BUILD} src=` +
    `${(document.currentScript && document.currentScript.src) || "unknown"}`
);

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
    ambientSurface: document.getElementById("ambient-surface"),
    computerControlButton: document.getElementById("computer-control-button"),
    computerControlText: document.getElementById("computer-control-text"),
    screenButton: document.getElementById("screen-button"),
    chatToggleButton: document.getElementById("chat-toggle-button"),
    chatDrawerClose: document.getElementById("chat-drawer-close"),
    chatInputRow: document.getElementById("chat-input-row"),
    chatTextInput: document.getElementById("chat-text-input"),
    chatSendButton: document.getElementById("chat-send-button"),
    modeVoiceButton: document.getElementById("mode-voice-button"),
    modeTextButton: document.getElementById("mode-text-button"),
    connectionStatus: document.getElementById("connection-status"),
    connectionText: document.getElementById("connection-text"),
    projectApproval: document.getElementById("project-approval"),
    projectApprovalSummary: document.getElementById("project-approval-summary"),
    projectApprovalFiles: document.getElementById("project-approval-files"),
    projectChangeEditors: document.getElementById("project-change-editors"),
    projectApprovalDiff: document.getElementById("project-approval-diff"),
    projectApprovalNote: document.getElementById("project-approval-note"),
    projectApproveButton: document.getElementById("project-approve-button"),
    projectRejectButton: document.getElementById("project-reject-button"),
    gitApproval: document.getElementById("git-approval"),
    gitTarget: document.getElementById("git-target"),
    gitApprovalFiles: document.getElementById("git-approval-files"),
    gitCommitMessage: document.getElementById("git-commit-message"),
    gitApprovalDiff: document.getElementById("git-approval-diff"),
    gitApprovalNote: document.getElementById("git-approval-note"),
    gitRejectButton: document.getElementById("git-reject-button"),
    gitCommitButton: document.getElementById("git-commit-button"),
    gitPushButton: document.getElementById("git-push-button"),
    actionApproval: document.getElementById("action-approval"),
    actionApprovalTitle: document.getElementById("action-approval-title"),
    actionApprovalRisk: document.getElementById("action-approval-risk"),
    actionApprovalSummary: document.getElementById("action-approval-summary"),
    actionApprovalDetails: document.getElementById("action-approval-details"),
    actionApprovalNote: document.getElementById("action-approval-note"),
    actionRejectButton: document.getElementById("action-reject-button"),
    actionApproveButton: document.getElementById("action-approve-button")
};

/* Values that change while the application is running. */
const state = {
    positionLocked: false,
    pixiApp: null,
    live2dModel: null,
    pythonSocket: null,
    reconnectTimer: null,
    cursorTrackingTimer: null,
    activeProposalId: null,
    proposalEditors: [],
    activeGitProposalId: null,
    gitPushAvailable: false,
    activeActionProposalId: null,
    targetMouthValue: 0,
    currentMouthValue: 0,
    inputMode: "voice",
    computerControlEnabled: false,
    computerControlAvailable: false,
    computerControlPending: false
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
        if (!elements.chatDrawer.classList.contains("closed")) {
            elements.chatTextInput.focus();
        }
    });

    elements.chatDrawerClose.addEventListener("click", () => {
        elements.chatDrawer.classList.add("closed");
    });
}

function setupChatTextInput() {
    elements.chatInputRow.addEventListener("submit", event => {
        event.preventDefault();
        sendTypedMessage();
    });
}

function sendTypedMessage() {
    const text = elements.chatTextInput.value.trim();
    if (!text) return;

    if (
        !state.pythonSocket ||
        state.pythonSocket.readyState !== WebSocket.OPEN
    ) {
        setActivity("offline", "Elaina is offline");
        return;
    }

    state.pythonSocket.send(JSON.stringify({
        command: "send_text_message",
        text
    }));

    elements.chatTextInput.value = "";
}

/* --------------------------- Input mode toggle ------------------------ */

function setupInputModeToggle() {
    elements.modeVoiceButton.addEventListener("click", () => requestInputMode("voice"));
    elements.modeTextButton.addEventListener("click", () => requestInputMode("text"));
}

function requestInputMode(mode) {
    if (mode === state.inputMode) return;

    if (
        !state.pythonSocket ||
        state.pythonSocket.readyState !== WebSocket.OPEN
    ) {
        setActivity("offline", "Elaina is offline");
        return;
    }

    state.pythonSocket.send(JSON.stringify({
        command: "set_input_mode",
        mode
    }));
}

/* Applies a mode confirmed by Python. Never flips the UI on the click alone,
 * since the backend is what actually stops or restarts the microphone. */
function applyInputMode(mode) {
    state.inputMode = mode;
    const isTextMode = mode === "text";

    elements.modeVoiceButton.classList.toggle("active", !isTextMode);
    elements.modeTextButton.classList.toggle("active", isTextMode);
    updateChatInputAvailability();

    if (isTextMode) {
        setActivity("listening", "Text mode: type a message");
    } else {
        setActivity("listening", "Listening...");
    }
}

function updateChatInputAvailability() {
    const isConnected = Boolean(
        state.pythonSocket &&
        state.pythonSocket.readyState === WebSocket.OPEN
    );
    const canType = isConnected && state.inputMode === "text";
    elements.chatTextInput.disabled = !canType;
    elements.chatSendButton.disabled = !canType;
}

/* ---------------------- Computer Control toggle --------------------- */

function setupComputerControlToggle() {
    elements.computerControlButton.addEventListener("click", () => {
        if (
            state.computerControlPending ||
            !state.pythonSocket ||
            state.pythonSocket.readyState !== WebSocket.OPEN
        ) {
            return;
        }

        state.computerControlPending = true;
        updateComputerControlAvailability();
        state.pythonSocket.send(JSON.stringify({
            command: "set_computer_control_mode",
            enabled: !state.computerControlEnabled
        }));
    });
}

function applyComputerControlMode(enabled, available = true) {
    state.computerControlEnabled = Boolean(enabled) && Boolean(available);
    state.computerControlAvailable = Boolean(available);
    state.computerControlPending = false;

    elements.computerControlButton.classList.toggle(
        "active",
        state.computerControlEnabled
    );
    elements.computerControlButton.setAttribute(
        "aria-pressed",
        String(state.computerControlEnabled)
    );
    elements.computerControlText.textContent = state.computerControlAvailable
        ? `Control ${state.computerControlEnabled ? "On" : "Off"}`
        : "Control Unavailable";
    elements.computerControlButton.title = state.computerControlAvailable
        ? `Turn ${state.computerControlEnabled ? "off" : "on"} Computer Control Mode`
        : "Computer control is disabled in configuration";
    updateComputerControlAvailability();
}

function updateComputerControlAvailability() {
    const isConnected = Boolean(
        state.pythonSocket &&
        state.pythonSocket.readyState === WebSocket.OPEN
    );
    elements.computerControlButton.disabled = !isConnected ||
        !state.computerControlAvailable || state.computerControlPending;
}

/* ----------------------- Project change approval -------------------- */

function setupProjectApproval() {
    elements.projectApproveButton.addEventListener("click", () => {
        sendProjectDecision("approve");
    });

    elements.projectRejectButton.addEventListener("click", () => {
        sendProjectDecision("reject");
    });
}

function showProjectProposal(message) {
    const proposalId = cleanText(message.proposal_id);
    if (!proposalId) return;

    state.activeProposalId = proposalId;
    elements.projectApprovalSummary.textContent =
        cleanText(message.summary) || "Elaina prepared project changes.";

    elements.projectApprovalFiles.replaceChildren();
    const files = Array.isArray(message.files) ? message.files : [];

    for (const filePath of files) {
        const file = document.createElement("div");
        file.className = "project-file";
        file.textContent = String(filePath);
        elements.projectApprovalFiles.appendChild(file);
    }

    elements.projectChangeEditors.replaceChildren();
    state.proposalEditors = [];
    const editableChanges = Array.isArray(message.editable_changes)
        ? message.editable_changes
        : [];

    editableChanges.forEach((change, index) => {
        const editor = document.createElement("section");
        editor.className = "project-change-editor";

        const label = document.createElement("label");
        label.textContent =
            `${change.action || "change"} · ${change.path || `Change ${index + 1}`}`;

        const hint = document.createElement("span");
        hint.textContent = change.action === "create"
            ? "New file content"
            : "Replacement code";

        const textarea = document.createElement("textarea");
        textarea.className = "project-code-editor";
        textarea.spellcheck = false;
        textarea.value = String(change.new_text ?? "");
        textarea.setAttribute(
            "aria-label",
            `Editable replacement for ${change.path || `change ${index + 1}`}`
        );

        label.appendChild(hint);
        editor.append(label, textarea);
        elements.projectChangeEditors.appendChild(editor);
        state.proposalEditors.push(textarea);
    });

    elements.projectApprovalDiff.textContent =
        cleanText(message.diff) || "No preview was provided.";
    elements.projectApprovalNote.textContent = message.diff_truncated
        ? "Preview shortened. No files have been changed yet."
        : "No files have been changed yet.";

    setProjectApprovalBusy(false);
    elements.projectApproval.classList.remove("hidden");
}

function setProjectApprovalBusy(isBusy) {
    elements.projectApproveButton.disabled = isBusy;
    elements.projectRejectButton.disabled = isBusy;
}

function sendProjectDecision(decision) {
    if (!state.activeProposalId) return;

    if (
        !state.pythonSocket ||
        state.pythonSocket.readyState !== WebSocket.OPEN
    ) {
        elements.projectApprovalNote.textContent =
            "Elaina is offline. Reconnect before deciding.";
        return;
    }

    setProjectApprovalBusy(true);
    elements.projectApprovalNote.textContent = decision === "approve"
        ? "Applying the approved changes..."
        : "Rejecting the proposal...";

    const command = {
        command: "project_change_decision",
        proposal_id: state.activeProposalId,
        decision
    };

    if (decision === "approve") {
        command.revised_texts = state.proposalEditors.map(
            editor => editor.value
        );
    }

    state.pythonSocket.send(JSON.stringify(command));
}

function closeProjectApproval() {
    state.activeProposalId = null;
    state.proposalEditors = [];
    elements.projectChangeEditors.replaceChildren();
    elements.projectApproval.classList.add("hidden");
    setProjectApprovalBusy(false);
}

/* --------------------------- Git approval --------------------------- */

function setupGitApproval() {
    elements.gitRejectButton.addEventListener("click", () => {
        sendGitDecision("reject");
    });
    elements.gitCommitButton.addEventListener("click", () => {
        sendGitDecision("commit_only");
    });
    elements.gitPushButton.addEventListener("click", () => {
        sendGitDecision("commit_push");
    });
}

function showGitProposal(message) {
    const proposalId = cleanText(message.proposal_id);
    if (!proposalId) return;

    state.activeGitProposalId = proposalId;
    const branch = cleanText(message.branch) || "(unknown branch)";
    const remote = cleanText(message.remote);
    elements.gitTarget.textContent = remote
        ? `${remote} · ${branch}`
        : `Local commit · ${branch}`;

    elements.gitCommitMessage.value =
        cleanText(message.commit_message) || "Update project files";

    elements.gitApprovalFiles.replaceChildren();
    const files = Array.isArray(message.files) ? message.files : [];
    for (const item of files) {
        const row = document.createElement("div");
        row.className = "git-file";

        const status = document.createElement("span");
        status.className = "git-file-status";
        status.textContent = cleanText(item.status) || "??";

        const path = document.createElement("span");
        path.textContent = String(item.path || "");

        row.append(status, path);
        elements.gitApprovalFiles.appendChild(row);
    }

    const stat = cleanText(message.diff_stat);
    const diff = cleanText(message.diff);
    elements.gitApprovalDiff.textContent = [stat, diff]
        .filter(Boolean)
        .join("\n\n") || "No textual diff preview is available.";
    elements.gitApprovalNote.textContent = message.diff_truncated
        ? "Diff preview shortened. Nothing has been staged yet."
        : "Nothing has been staged, committed, or pushed.";

    state.gitPushAvailable = Boolean(message.push_available);
    setGitApprovalBusy(false);
    elements.gitApproval.classList.remove("hidden");
}

function setGitApprovalBusy(isBusy) {
    elements.gitRejectButton.disabled = isBusy;
    elements.gitCommitButton.disabled = isBusy;
    elements.gitPushButton.disabled = isBusy || !state.gitPushAvailable;
    elements.gitCommitMessage.disabled = isBusy;
}

function sendGitDecision(decision) {
    if (!state.activeGitProposalId) return;

    if (
        !state.pythonSocket ||
        state.pythonSocket.readyState !== WebSocket.OPEN
    ) {
        elements.gitApprovalNote.textContent =
            "Elaina is offline. Reconnect before deciding.";
        return;
    }

    const commitMessage = elements.gitCommitMessage.value.trim();
    if (decision !== "reject" && !commitMessage) {
        elements.gitApprovalNote.textContent =
            "Enter a commit message before continuing.";
        elements.gitCommitMessage.focus();
        return;
    }

    setGitApprovalBusy(true);
    elements.gitApprovalNote.textContent = decision === "commit_push"
        ? "Committing and pushing the approved files..."
        : decision === "commit_only"
            ? "Committing the approved files..."
            : "Rejecting the Git proposal...";

    state.pythonSocket.send(JSON.stringify({
        command: "git_action_decision",
        proposal_id: state.activeGitProposalId,
        decision,
        commit_message: commitMessage
    }));
}

function closeGitApproval() {
    state.activeGitProposalId = null;
    state.gitPushAvailable = false;
    elements.gitApproval.classList.add("hidden");
    elements.gitApprovalFiles.replaceChildren();
    elements.gitCommitMessage.value = "";
    setGitApprovalBusy(false);
}

/* ------------------------ Agent action approval --------------------- */

function setupActionApproval() {
    elements.actionRejectButton.addEventListener("click", () => {
        sendActionDecision("reject");
    });
    elements.actionApproveButton.addEventListener("click", () => {
        sendActionDecision("approve");
    });
}

function showActionProposal(message) {
    const proposalId = cleanText(message.proposal_id);
    if (!proposalId) return;

    state.activeActionProposalId = proposalId;
    elements.actionApprovalTitle.textContent =
        cleanText(message.title) || "Review action";
    elements.actionApprovalRisk.textContent =
        cleanText(message.risk).replaceAll("_", " ") || "approval required";
    elements.actionApprovalSummary.textContent =
        cleanText(message.summary) || "Elaina prepared an agent action.";

    elements.actionApprovalDetails.replaceChildren();
    const details = Array.isArray(message.details) ? message.details : [];
    for (const item of details) {
        const row = document.createElement("div");
        row.className = "action-detail";

        const label = document.createElement("span");
        label.className = "action-detail-label";
        label.textContent = cleanText(item.label);

        const value = document.createElement("span");
        value.className = "action-detail-value";
        value.textContent = cleanText(item.value);

        row.append(label, value);
        elements.actionApprovalDetails.appendChild(row);
    }

    elements.actionApprovalNote.textContent =
        "Nothing has been changed yet. Review every detail before approving.";
    setActionApprovalBusy(false);
    elements.actionApproval.classList.remove("hidden");
}

function setActionApprovalBusy(isBusy) {
    elements.actionRejectButton.disabled = isBusy;
    elements.actionApproveButton.disabled = isBusy;
}

function sendActionDecision(decision) {
    if (!state.activeActionProposalId) return;

    if (
        !state.pythonSocket ||
        state.pythonSocket.readyState !== WebSocket.OPEN
    ) {
        elements.actionApprovalNote.textContent =
            "Elaina is offline. Reconnect before deciding.";
        return;
    }

    setActionApprovalBusy(true);
    elements.actionApprovalNote.textContent = decision === "approve"
        ? "Performing the approved action..."
        : "Rejecting the action...";

    state.pythonSocket.send(JSON.stringify({
        command: "action_approval_decision",
        proposal_id: state.activeActionProposalId,
        decision
    }));
}

function closeActionApproval() {
    state.activeActionProposalId = null;
    elements.actionApproval.classList.add("hidden");
    elements.actionApprovalDetails.replaceChildren();
    setActionApprovalBusy(false);
}

/* ------------------------- Screen selection ------------------------- */

function setupScreenSelection() {
    elements.screenButton.addEventListener("click", () => {
        if (
            !state.pythonSocket ||
            state.pythonSocket.readyState !== WebSocket.OPEN
        ) {
            setActivity("offline", "Elaina is offline");
            return;
        }

        window.elainaDesktop?.openScreenSelector();
    });

    window.elainaDesktop?.onScreenRegionSelected(region => {
        if (
            !state.pythonSocket ||
            state.pythonSocket.readyState !== WebSocket.OPEN
        ) {
            setActivity("offline", "Elaina is offline");
            return;
        }

        setActivity("thinking", "Analyzing...");

        state.pythonSocket.send(JSON.stringify({
            command: "queue_screen_region",
            region
        }));
    });
}

function setConnectionState(className, text) {
    elements.connectionStatus.classList.remove("connected", "disconnected");

    if (className) {
        elements.connectionStatus.classList.add(className);
    }

    elements.connectionText.textContent = text;
    updateChatInputAvailability();
    updateComputerControlAvailability();
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

function addObservationMessage(text) {
    const cleanedText = cleanText(text);
    if (!cleanedText) return;

    const message = document.createElement("pre");
    message.className = "message observation";
    message.textContent = cleanedText;
    elements.chatHistory.appendChild(message);
    elements.chatHistory.scrollTop = elements.chatHistory.scrollHeight;
}

/* ------------------------- Structured surfaces ------------------------- */
/*
 * The optional structured half of a reply. Elaina's backend decides whether
 * one exists and what goes in it; this file renders what it is given and
 * makes no judgement of its own. It must never read a reply, notice the word
 * "hotel" and produce cards, because the layer that knows whether a real
 * result set exists is the layer that found it.
 *
 * Validation here is not politeness, it is the contract: anything malformed
 * is dropped and the turn stays a plain-text reply, which is exactly what it
 * would have been before surfaces existed.
 */

const SURFACE_TYPES = ["shortlist", "comparison", "ambient_images"];
const SURFACE_ACTIONS = ["open", "compare", "tell_me_more"];
const SURFACE_ACTION_LABELS = {
    open: "Open",
    compare: "Compare",
    tell_me_more: "Tell me more"
};
const SURFACE_MAX_ITEMS = 6;

function safeSurfaceText(value, limit) {
    if (typeof value !== "string") return "";
    const text = value.replace(/\s+/g, " ").trim();
    if (!text || /[<>]|javascript:|data:text\/html/i.test(text)) return "";
    return text.slice(0, limit || 240);
}

function safeSurfaceLink(value) {
    const url = safeSurfaceText(value, 2048);
    return /^https?:\/\//i.test(url) ? url : "";
}

function validateSurface(message) {
    if (!message || typeof message !== "object") return null;
    if (SURFACE_TYPES.indexOf(message.type) === -1) return null;
    if (!Array.isArray(message.items)) return null;

    const items = [];
    for (const raw of message.items.slice(0, SURFACE_MAX_ITEMS)) {
        if (!raw || typeof raw !== "object") continue;
        const id = safeSurfaceText(raw.id, 64);
        const name = safeSurfaceText(raw.name, 120);
        if (!id || !name) continue;
        items.push({
            id,
            name,
            image: safeSurfaceLink(raw.image),
            subtitle: safeSurfaceText(raw.subtitle, 90),
            description: safeSurfaceText(raw.description, 240),
            metadata: Array.isArray(raw.metadata)
                ? raw.metadata
                    .filter((pair) => Array.isArray(pair) && pair.length === 2)
                    .map((pair) => [
                        safeSurfaceText(pair[0], 40),
                        safeSurfaceText(pair[1], 60)
                    ])
                    .filter((pair) => pair[0])
                : [],
            actions: Array.isArray(raw.actions)
                ? raw.actions.filter((a) => SURFACE_ACTIONS.indexOf(a) !== -1)
                : [],
            url: safeSurfaceLink(raw.url)
        });
    }

    if (message.type !== "ambient_images" && items.length < 2) return null;
    if (!items.length) return null;

    return {
        type: message.type,
        title: safeSurfaceText(message.title, 90),
        items
    };
}

/*
 * At most four. The window is 422 pixels wide and they float beside her,
 * not in a list -- a fifth has nowhere to go that is not on top of a fourth.
 */
const AMBIENT_MAX = 4;

/*
 * Why a payload was refused. validateSurface stays a pure yes/no -- this
 * runs only on the failure path, because "rejected" and "never called" look
 * identical from the outside and telling them apart cost a debugging pass.
 */
function surfaceRejection(message) {
    if (!message || typeof message !== "object") return "not an object";
    if (SURFACE_TYPES.indexOf(message.type) === -1) {
        return `unknown type ${JSON.stringify(message.type)}`;
    }
    if (!Array.isArray(message.items)) return "items is not an array";
    return `no item survived validation of ${message.items.length} sent`;
}

/*
 * A shortlist describes one moment. Leaving it floating through the next
 * question would turn it into a claim about a turn it has nothing to do
 * with, so the conversation moving on takes it down.
 */
function clearAmbientSurface(why) {
    if (!elements.ambientSurface) return;
    if (elements.ambientSurface.children.length) {
        console.log(`[Surface UI] cleared (${why})`);
    }
    elements.ambientSurface.textContent = "";
}

/*
 * Open what a card points at.
 *
 * The address is the backend's: it came from a candidate a search actually
 * returned, and it was validated on the way out and again on the way in.
 * Electron only carries it to the operating system's browser -- it decides
 * nothing -- and the backend is told which card was opened so the window
 * and the conversation cannot end up with different ideas of what the
 * person is looking at.
 */
function openCandidate(item) {
    if (!item.url) return;
    console.log(`[Surface UI] opening [${item.id}] ${item.url}`);
    if (window.elainaDesktop && window.elainaDesktop.openExternal) {
        window.elainaDesktop.openExternal(item.url);
    }
    if (
        state.pythonSocket &&
        state.pythonSocket.readyState === WebSocket.OPEN
    ) {
        state.pythonSocket.send(JSON.stringify({
            command: "surface_opened",
            candidate_id: item.id
        }));
    }
}

/* No picture came back. The name is the part that matters, and an empty
 * frame reads as something broken rather than as something plain. */
function ambientPlaceholder(name) {
    const mark = document.createElement("div");
    mark.className = "ambient-placeholder";
    mark.textContent = String(name || "?").trim().charAt(0).toUpperCase();
    return mark;
}

function ambientCard(item) {
    const card = document.createElement("div");
    card.className = item.url ? "ambient-card" : "ambient-card ambient-flat";
    card.title = item.url ? `Open ${item.name}` : item.name;

    const inner = document.createElement("div");
    inner.className = "ambient-inner";

    const frame = document.createElement("div");
    frame.className = "ambient-frame";

    if (item.image) {
        const image = document.createElement("img");
        image.className = "ambient-image";
        image.alt = "";
        // A search-index thumbnail can be gone, blocked, or slow by the
        // time it is asked for. Both outcomes say so: a blank card and a
        // card that never got a picture look the same from outside.
        image.addEventListener("load", () => {
            console.log(
                `[Surface UI] picture loaded for ${item.name}: ` +
                `${image.naturalWidth}x${image.naturalHeight}`
            );
        });
        image.addEventListener("error", () => {
            console.log(`[Surface UI] picture failed for ${item.name}`);
            if (frame.contains(image)) frame.removeChild(image);
            frame.appendChild(ambientPlaceholder(item.name));
        });
        image.src = item.image;
        frame.appendChild(image);
    } else {
        frame.appendChild(ambientPlaceholder(item.name));
    }

    const name = document.createElement("div");
    name.className = "ambient-name";
    name.textContent = item.name;

    inner.appendChild(frame);
    inner.appendChild(name);
    card.appendChild(inner);

    if (item.url) {
        card.addEventListener("click", () => openCandidate(item));
    }
    return card;
}

/* Where the cards actually ended up, once the arrival animation has run.
 * Kept because "I see nothing" is not a diagnosis, and last time telling a
 * hidden card from an absent one took a whole pass. */
function reportAmbientLayout() {
    const first = elements.ambientSurface.firstElementChild;
    if (!first) return;
    const box = first.getBoundingClientRect();
    const style = window.getComputedStyle(first);
    console.log(
        `[Surface UI] layout: firstCard=${Math.round(box.left)},` +
        `${Math.round(box.top)} ${Math.round(box.width)}x` +
        `${Math.round(box.height)} opacity=${style.opacity} ` +
        `visibility=${style.visibility} ` +
        `window=${window.innerWidth}x${window.innerHeight}`
    );
}

function renderSurface(message) {
    const surface = validateSurface(message);
    if (!surface) {
        console.log(
            `[Surface UI] validation rejected reason=${surfaceRejection(message)}`
        );
        return;
    }
    console.log(
        `[Surface UI] validation accepted type=${surface.type} ` +
        `items=${surface.items.length}`
    );
    if (!elements.ambientSurface) {
        console.log("[Surface UI] #ambient-surface MISSING; nothing to draw");
        return;
    }

    clearAmbientSurface("a newer surface arrived");
    const shown = surface.items.slice(0, AMBIENT_MAX);
    for (const item of shown) {
        elements.ambientSurface.appendChild(ambientCard(item));
    }

    console.log(
        `[Surface UI] floating cards=${elements.ambientSurface.children.length}` +
        ` withImage=${shown.filter((item) => item.image).length}` +
        ` openable=${shown.filter((item) => item.url).length}`
    );
    setTimeout(reportAmbientLayout, 500);
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
        state.pythonSocket.send(JSON.stringify({
            command: "get_computer_control_mode"
        }));
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
                clearAmbientSurface("a new turn started");
                break;
            case "assistant_status":
                // Status lines are part of Elaina's side of the conversation,
                // so render them exactly like every other assistant message.
                addAssistantMessage(message.text);
                setActivity("thinking", message.text || "Working...");
                break;
            case "assistant_interrupted":
                setActivity("listening", "Listening...");
                stopMouthMovement();
                break;
            case "assistant_finished":
                addAssistantMessage(message.text);
                setActivity("speaking", "Speaking...");
                break;
            case "assistant_surface":
                // Optional and additive. A reply without one never sends
                // this, and a malformed one draws nothing at all.
                console.log(
                    `[Surface UI] event received type=${message.type} ` +
                    `items=${(message.items || []).length}`
                );
                renderSurface(message);
                break;
            case "input_mode_changed":
                applyInputMode(message.mode);
                break;
            case "computer_control_mode_changed":
                applyComputerControlMode(message.enabled, message.available);
                break;
            case "computer_action_completed":
                if (
                    message.operation === "list_windows" ||
                    message.operation === "describe_window"
                ) {
                    // Elaina's spoken reply is deliberately trimmed for
                    // voice; this shows the complete, unabridged list of
                    // windows or controls she actually observed.
                    addObservationMessage(message.message);
                }
                break;
            case "screen_region_ready":
                setActivity("listening", "Ask about selection...");
                break;
            case "screen_region_error":
                setActivity(
                    "offline",
                    message.text || "Could not capture selection"
                );
                break;
            case "visual_match_found": {
                const title = cleanText(message.title) || "Visual web match";
                const score = Number(message.score);
                const confidence = Number.isFinite(score) && score > 0
                    ? ` (${Math.round(score * 100)}% retrieval score)`
                    : "";
                addAssistantMessage(`Matched source: ${title}${confidence}`);
                break;
            }
            case "project_change_proposed":
                showProjectProposal(message);
                setActivity("thinking", "Waiting for approval...");
                break;
            case "project_change_applied":
                closeProjectApproval();
                addAssistantMessage(
                    `Changes applied to ${(message.files || []).join(", ")}.`
                );
                setActivity("listening", "Changes applied");
                break;
            case "project_change_rejected":
                closeProjectApproval();
                addAssistantMessage("Project changes rejected. Nothing was edited.");
                setActivity("listening", "Changes rejected");
                break;
            case "project_change_error":
                elements.projectApprovalNote.textContent =
                    message.message || "The project change failed.";
                setProjectApprovalBusy(false);
                setActivity("offline", "Change failed");
                break;
            case "git_action_proposed":
                showGitProposal(message);
                setActivity("thinking", "Waiting for Git approval...");
                break;
            case "git_action_completed": {
                const actionText = message.status === "pushed"
                    ? `Committed ${message.commit} and pushed to ${message.remote}.`
                    : `Created commit ${message.commit}.`;
                closeGitApproval();
                addAssistantMessage(actionText);
                setActivity("listening", message.status === "pushed"
                    ? "Push complete"
                    : "Commit complete");
                break;
            }
            case "git_action_rejected":
                closeGitApproval();
                addAssistantMessage(
                    "Git action rejected. Nothing was staged or committed."
                );
                setActivity("listening", "Git action rejected");
                break;
            case "git_action_partial":
                closeGitApproval();
                addAssistantMessage(
                    `Commit ${message.commit} was created, but push failed: ` +
                    `${message.error || "Unknown push error"}`
                );
                setActivity("offline", "Push failed after commit");
                break;
            case "git_action_error":
                if (state.activeGitProposalId) {
                    elements.gitApprovalNote.textContent =
                        message.message || "The Git action failed.";
                    setGitApprovalBusy(false);
                } else {
                    addAssistantMessage(
                        message.message || "The Git proposal could not be prepared."
                    );
                }
                setActivity("offline", "Git action failed");
                break;
            case "agent_task_started":
                setActivity(
                    "thinking",
                    `${cleanText(message.agent_name) || "Agent"} is working...`
                );
                break;
            case "action_approval_requested":
                showActionProposal(message);
                setActivity("thinking", "Waiting for action approval...");
                break;
            case "action_approval_completed":
                closeActionApproval();
                addAssistantMessage(
                    message.message || "The approved action was completed."
                );
                setActivity("listening", "Action completed");
                break;
            case "action_approval_rejected":
                closeActionApproval();
                addAssistantMessage(
                    message.message || "The action was rejected."
                );
                setActivity("listening", "Action rejected");
                break;
            case "action_approval_error":
                if (state.activeActionProposalId) {
                    elements.actionApprovalNote.textContent =
                        message.message || "The agent action failed.";
                    setActionApprovalBusy(false);
                } else {
                    addAssistantMessage(
                        message.message || "The agent action failed."
                    );
                }
                setActivity("offline", "Agent action failed");
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
    setupChatTextInput();
    setupInputModeToggle();
    setupComputerControlToggle();
    setupProjectApproval();
    setupGitApproval();
    setupActionApproval();
    setupScreenSelection();
    loadElaina();
    connectToPython();
}

startApplication();
