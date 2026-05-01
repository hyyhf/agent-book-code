const { contextBridge } = require('electron');

contextBridge.exposeInMainWorld('funharness', {
  apiBase: process.env.FUNGUI_API_BASE || 'http://127.0.0.1:8765',
});
