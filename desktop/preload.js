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
    }
});