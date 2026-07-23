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
