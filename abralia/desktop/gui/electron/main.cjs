// Copyright 2026 blue_lobster
// SPDX-License-Identifier: Apache-2.0
const { app, BrowserWindow, ipcMain, protocol, net, session, Menu, dialog, clipboard } = require('electron');
const path = require('node:path');
const fs = require('node:fs');
const { pathToFileURL } = require('node:url');
const { PythonBridge, channels, validateCommand } = require('./bridge.cjs');

app.setName('Abralia');
if (process.env.ABRALIA_GUI_USER_DATA) {
  if (!path.isAbsolute(process.env.ABRALIA_GUI_USER_DATA)) throw Error('User-data override must be an absolute path.');
  app.setPath('userData', process.env.ABRALIA_GUI_USER_DATA);
}
protocol.registerSchemesAsPrivileged([{ scheme: 'abralia', privileges: { standard: true, secure: true, supportFetchAPI: true } }]);
let window, bridge, quitting = false, cleanupFinished = false;

function hostLaunch() {
  const state = process.env.ABRALIA_STATE_DIR || path.join(app.getPath('userData'), 'control');
  const args = ['--state-dir', state, '--shared'];
  if (process.env.ABRALIA_RUNTIME_DIR) args.push('--runtime-dir', process.env.ABRALIA_RUNTIME_DIR);
  if (process.env.ABRALIA_MANAGED_MODE) args.push('--managed-mode', process.env.ABRALIA_MANAGED_MODE);
  const bundle = app.isPackaged ? path.join(process.resourcesPath, 'plugin-bundle') : path.resolve(__dirname, '../../plugin-bundle');
  args.push('--plugin-bundle', bundle);
  if (process.env.ABRALIA_RUNTIME_EXECUTABLE) args.push('--runtime-executable', process.env.ABRALIA_RUNTIME_EXECUTABLE);
  if (process.env.ABRALIA_CODEX_EXECUTABLE) args.push('--codex-executable', process.env.ABRALIA_CODEX_EXECUTABLE);
  if (app.isPackaged) return {
    executable: path.join(process.resourcesPath, 'backend', 'abralia-gui-host', 'abralia-gui-host'), args,
  };
  return {
    executable: process.env.ABRALIA_PYTHON || path.resolve(__dirname, '../../../../.venv/bin/python'),
    args: ['-m', 'abralia.backend.gui_host', ...args],
  };
}

function createWindow() {
  window = new BrowserWindow({
    width: 1120, height: 800, minWidth: 820, minHeight: 650,
    title: 'Abralia', titleBarStyle: 'hiddenInset', trafficLightPosition: { x: 22, y: 20 },
    backgroundColor: '#f6f7f9', show: false,
    webPreferences: { preload: path.join(__dirname, 'preload.cjs'), contextIsolation: true, sandbox: true, nodeIntegration: false },
  });
  window.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
  window.webContents.on('will-navigate', (event, url) => { if (url !== 'abralia://app/index.html') event.preventDefault(); });
  window.once('ready-to-show', () => window.show());
  window.on('close', event => {
    if (process.platform === 'darwin' && !quitting) {
      event.preventDefault();
      window.hide();
    }
  });
  window.on('closed', () => { window = null; });
  window.loadURL('abralia://app/index.html');
}

if (!app.requestSingleInstanceLock()) app.quit();
else {
  app.on('second-instance', () => { if (quitting) return; if (window) { if (window.isMinimized()) window.restore(); window.show(); window.focus(); } else createWindow(); });
  app.whenReady().then(() => {
    const contentRoot = path.resolve(__dirname, '../dist');
    protocol.handle('abralia', request => {
      const url = new URL(request.url);
      if (url.host !== 'app' || !['GET', 'HEAD'].includes(request.method)) return new Response('', { status: 403 });
      let filename;
      try { filename = decodeURIComponent(url.pathname); } catch { return new Response('', { status: 400 }); }
      const target = path.resolve(contentRoot, '.' + filename);
      const relative = path.relative(contentRoot, target);
      if (relative.startsWith('..') || path.isAbsolute(relative) || !fs.existsSync(target)) return new Response('', { status: 404 });
      return net.fetch(pathToFileURL(target).href);
    });
    session.defaultSession.setPermissionRequestHandler((_contents, _permission, callback) => callback(false));
    session.defaultSession.setPermissionCheckHandler(() => false);
    const launch = hostLaunch();
    bridge = new PythonBridge(launch.executable, launch.args);
    for (const [channel, command] of Object.entries(channels)) ipcMain.handle(channel, async (event, args = {}) => {
      if (!window || event.sender !== window.webContents || event.senderFrame !== window.webContents.mainFrame || event.senderFrame.url !== 'abralia://app/index.html') throw Error('Untrusted request');
      validateCommand(command, args);
      return bridge.request(command, args);
    });
    ipcMain.handle('abralia:add-project', async event => {
      if (!window || event.sender !== window.webContents || event.senderFrame !== window.webContents.mainFrame || event.senderFrame.url !== 'abralia://app/index.html') throw Error('Untrusted request');
      const result = await dialog.showOpenDialog(window, { title: 'Enable a project in Abralia',
        buttonLabel: 'Enable project', properties: ['openDirectory'] });
      if (result.canceled || !result.filePaths[0]) return { cancelled: true };
      return bridge.request('register_project', { path: result.filePaths[0] });
    });
    ipcMain.handle('abralia:copy-hooks', async event => {
      if (!window || event.sender !== window.webContents || event.senderFrame !== window.webContents.mainFrame || event.senderFrame.url !== 'abralia://app/index.html') throw Error('Untrusted request');
      const result = await bridge.request('manual_hooks');
      clipboard.writeText(result.text);
      return { copied: true };
    });
    Menu.setApplicationMenu(Menu.buildFromTemplate([
      { label: 'Abralia', submenu: [{ role: 'about' }, { type: 'separator' }, { role: 'hide' }, { role: 'hideOthers' }, { role: 'unhide' }, { type: 'separator' }, { role: 'quit' }] },
      { label: 'Edit', submenu: [{ role: 'undo' }, { role: 'redo' }, { type: 'separator' }, { role: 'cut' }, { role: 'copy' }, { role: 'paste' }, { role: 'selectAll' }] },
      { label: 'Window', submenu: [{ role: 'minimize' }, { role: 'zoom' }, { role: 'front' }] },
    ]));
    createWindow();
    // Startup is an app lifecycle operation, never an overview/poll side effect.
    // The helper records actionable startup errors for the UI to display.
    bridge.request('start_backend').catch(() => {});
    app.on('activate', () => {
      if (quitting) return;
      if (window) {
        if (window.isMinimized()) window.restore();
        window.show();
        window.focus();
      } else createWindow();
    });
  });
  app.on('window-all-closed', () => { if (process.platform !== 'darwin') app.quit(); });
  app.on('before-quit', event => {
    if (cleanupFinished || !bridge) return;
    event.preventDefault();
    if (quitting) return;
    quitting = true;
    bridge.close().then(result => {
      if (!result.clean) console.error(result.reason || 'Backend cleanup could not be confirmed.');
    }).finally(() => { cleanupFinished = true; app.quit(); });
  });
}
