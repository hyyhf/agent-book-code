const { app, BrowserWindow, Menu, ipcMain } = require('electron');
const fs = require('fs');
const path = require('path');
const { spawn } = require('child_process');

const API_HOST = process.env.FUNGUI_HOST || '127.0.0.1';
const API_PORT = process.env.FUNGUI_PORT || '8765';
const API_BASE = `http://${API_HOST}:${API_PORT}`;

let backendProcess = null;

function appIconPath() {
  const candidates = app.isPackaged
    ? [path.join(__dirname, '..', 'dist', 'logo_fh.png'), path.join(__dirname, '..', 'public', 'logo_fh.png')]
    : [path.join(__dirname, '..', 'public', 'logo_fh.png')];
  return candidates.find((candidate) => fs.existsSync(candidate));
}

const menuTemplates = {
  file: [
    { role: 'close', label: 'Close' },
    { type: 'separator' },
    { role: 'quit', label: 'Quit FunHarness GUI' },
  ],
  edit: [
    { role: 'undo', label: 'Undo' },
    { role: 'redo', label: 'Redo' },
    { type: 'separator' },
    { role: 'cut', label: 'Cut' },
    { role: 'copy', label: 'Copy' },
    { role: 'paste', label: 'Paste' },
    { role: 'selectAll', label: 'Select All' },
  ],
  view: [
    { role: 'reload', label: 'Reload' },
    { role: 'forceReload', label: 'Force Reload' },
    { role: 'toggleDevTools', label: 'Developer Tools' },
    { type: 'separator' },
    { role: 'resetZoom', label: 'Actual Size' },
    { role: 'zoomIn', label: 'Zoom In' },
    { role: 'zoomOut', label: 'Zoom Out' },
    { type: 'separator' },
    { role: 'togglefullscreen', label: 'Toggle Full Screen' },
  ],
  window: [
    { role: 'minimize', label: 'Minimize' },
    { role: 'zoom', label: 'Zoom' },
    { type: 'separator' },
    { role: 'front', label: 'Bring All to Front' },
  ],
  help: [
    {
      label: 'FunHarness GUI',
      click: () => {
        const focused = BrowserWindow.getFocusedWindow();
        focused?.webContents.send('funharness:menu-help');
      },
    },
  ],
};

function startBackend() {
  if (process.env.FUNGUI_EXTERNAL_BACKEND === '1') {
    return;
  }
  const repoRoot = path.resolve(__dirname, '..', '..');
  const command = process.env.FUNGUI_BACKEND_COMMAND || 'uv';
  const args = process.env.FUNGUI_BACKEND_COMMAND
    ? []
    : ['run', 'python', '-m', 'funGUI.backend', '--host', API_HOST, '--port', API_PORT];
  const backendEnv = {
    ...process.env,
    FUNGUI_API_BASE: API_BASE,
    UV_CACHE_DIR: process.env.UV_CACHE_DIR || path.join(repoRoot, '.uv-cache'),
  };
  backendProcess = spawn(command, args, {
    cwd: repoRoot,
    shell: process.platform === 'win32',
    env: backendEnv,
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  backendProcess.stdout.on('data', (chunk) => {
    console.log(`[backend] ${chunk}`);
  });
  backendProcess.stderr.on('data', (chunk) => {
    console.error(`[backend] ${chunk}`);
  });
}

function stopBackend() {
  if (backendProcess && !backendProcess.killed) {
    backendProcess.kill();
  }
  backendProcess = null;
}

function createWindow() {
  const icon = appIconPath();
  const window = new BrowserWindow({
    width: 1440,
    height: 900,
    minWidth: 1080,
    minHeight: 720,
    title: 'FunHarness GUI',
    icon,
    backgroundColor: '#f7f8fb',
    titleBarStyle: process.platform === 'darwin' ? 'hiddenInset' : 'hidden',
    titleBarOverlay: process.platform === 'darwin'
      ? false
      : {
          color: '#fffaf5',
          symbolColor: '#1f2933',
          height: 34,
        },
    autoHideMenuBar: true,
    webPreferences: {
      preload: path.join(__dirname, 'preload.cjs'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });

  window.setMenuBarVisibility(false);

  process.env.FUNGUI_API_BASE = API_BASE;
  const devUrl = process.env.VITE_DEV_SERVER_URL || 'http://127.0.0.1:5173';
  if (!app.isPackaged) {
    window.loadURL(devUrl);
  } else {
    window.loadFile(path.join(__dirname, '..', 'dist', 'index.html'));
  }
}

app.whenReady().then(() => {
  if (process.platform === 'win32') {
    app.setAppUserModelId('com.funharness.gui');
  }
  Menu.setApplicationMenu(null);
  ipcMain.handle('funharness:show-menu', (event, menuName) => {
    const template = menuTemplates[String(menuName)] || [];
    if (!template.length) return;
    const menu = Menu.buildFromTemplate(template);
    const owner = BrowserWindow.fromWebContents(event.sender);
    menu.popup({ window: owner || undefined });
  });

  startBackend();
  createWindow();
  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      createWindow();
    }
  });
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    app.quit();
  }
});

app.on('before-quit', stopBackend);
