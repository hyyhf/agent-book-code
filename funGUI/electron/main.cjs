const { app, BrowserWindow } = require('electron');
const path = require('path');
const { spawn } = require('child_process');

const API_HOST = process.env.FUNGUI_HOST || '127.0.0.1';
const API_PORT = process.env.FUNGUI_PORT || '8765';
const API_BASE = `http://${API_HOST}:${API_PORT}`;

let backendProcess = null;

function startBackend() {
  if (process.env.FUNGUI_EXTERNAL_BACKEND === '1') {
    return;
  }
  const repoRoot = path.resolve(__dirname, '..', '..');
  const command = process.env.FUNGUI_BACKEND_COMMAND || 'uv';
  const args = process.env.FUNGUI_BACKEND_COMMAND
    ? []
    : ['run', 'python', '-m', 'funGUI.backend', '--host', API_HOST, '--port', API_PORT];
  backendProcess = spawn(command, args, {
    cwd: repoRoot,
    shell: process.platform === 'win32',
    env: { ...process.env, FUNGUI_API_BASE: API_BASE },
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
  const window = new BrowserWindow({
    width: 1440,
    height: 900,
    minWidth: 1080,
    minHeight: 720,
    title: 'FunHarness GUI',
    backgroundColor: '#f7f8fb',
    titleBarStyle: process.platform === 'darwin' ? 'hiddenInset' : 'default',
    webPreferences: {
      preload: path.join(__dirname, 'preload.cjs'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });

  process.env.FUNGUI_API_BASE = API_BASE;
  const devUrl = process.env.VITE_DEV_SERVER_URL || 'http://127.0.0.1:5173';
  if (!app.isPackaged) {
    window.loadURL(devUrl);
  } else {
    window.loadFile(path.join(__dirname, '..', 'dist', 'index.html'));
  }
}

app.whenReady().then(() => {
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
