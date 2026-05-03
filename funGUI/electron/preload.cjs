const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('funharness', {
  apiBase: process.env.FUNGUI_API_BASE || 'http://127.0.0.1:8765',
  isElectron: true,
  platform: process.platform,
  showMenu: (menuName) => ipcRenderer.invoke('funharness:show-menu', menuName),
});
