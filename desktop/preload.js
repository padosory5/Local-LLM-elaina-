const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("elainaDesktop", {
    closeWindow: () => {
        ipcRenderer.send("window-close");
    },

    minimizeWindow: () => {
        ipcRenderer.send("window-minimize");
    },

    setAlwaysOnTop: (enabled) => {
        ipcRenderer.send(
            "toggle-always-on-top",
            Boolean(enabled)
        );
    },

    openExternal: (url) => {
        // Only carries the address. The main process is what decides
        // whether it is allowed to be opened, because a renderer is the
        // one place in this app that touches remote content.
        ipcRenderer.send("open-external", String(url || ""));
    },

    getCursorState: () => {
        return ipcRenderer.invoke("get-cursor-state");
    },

    openScreenSelector: () => {
        ipcRenderer.send("open-screen-selector");
    },

    cancelScreenSelection: () => {
        ipcRenderer.send("screen-selection-cancel");
    },

    confirmScreenSelection: (region) => {
        ipcRenderer.send("screen-selection-confirm", region);
    },

    onScreenRegionSelected: (callback) => {
        const listener = (_event, region) => callback(region);
        ipcRenderer.on("screen-region-selected", listener);

        return () => {
            ipcRenderer.removeListener(
                "screen-region-selected",
                listener
            );
        };
    }
});
