// Copyright 2026 blue_lobster
// SPDX-License-Identifier: Apache-2.0
const test = require('node:test');
const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

async function loadMain({ platform = 'darwin', primary = true } = {}) {
  const app = new EventEmitter(), windows = [], bridges = [];
  let finishCleanup;
  const cleanup = new Promise(resolve => { finishCleanup = resolve; });
  Object.assign(app, {
    quitCalls: 0, exited: false, isPackaged: false,
    setName() {}, setPath() {}, getPath:() => '/fixture/Abralia',
    requestSingleInstanceLock:() => primary, whenReady:() => Promise.resolve(),
    quit() {
      this.quitCalls++;
      const event = { prevented:false, preventDefault() { this.prevented = true; } };
      this.emit('before-quit', event);
      if (event.prevented) return;
      for (const window of windows.filter(item => !item.destroyed)) window.attemptClose();
      this.exited = true;
      this.emit('quit');
    },
  });
  class BrowserWindow extends EventEmitter {
    constructor() {
      super(); windows.push(this);
      this.webContents = new EventEmitter(); this.webContents.setWindowOpenHandler = () => {};
      this.hidden = true; this.destroyed = false; this.minimized = false;
      this.hideCalls = 0; this.showCalls = 0; this.focusCalls = 0; this.restoreCalls = 0;
    }
    loadURL(url) { this.url = url; }
    hide() { this.hidden = true; this.hideCalls++; }
    show() { this.hidden = false; this.showCalls++; }
    focus() { this.focusCalls++; }
    isMinimized() { return this.minimized; }
    restore() { this.minimized = false; this.restoreCalls++; }
    attemptClose() {
      const event = { prevented:false, preventDefault() { this.prevented = true; } };
      this.emit('close', event);
      if (!event.prevented) {
        this.destroyed = true; this.emit('closed');
        if (windows.every(item => item.destroyed)) app.emit('window-all-closed');
      }
      return event;
    }
  }
  class PythonBridge {
    constructor() { this.requests = []; this.closeCalls = 0; bridges.push(this); }
    request(command) { this.requests.push(command); return Promise.resolve({}); }
    close() { this.closeCalls++; return cleanup; }
  }
  const electron = {
    app, BrowserWindow, ipcMain:{ handle() {} },
    protocol:{ registerSchemesAsPrivileged() {}, handle() {} }, net:{},
    session:{ defaultSession:{ setPermissionRequestHandler() {}, setPermissionCheckHandler() {} } },
    Menu:{ buildFromTemplate:template => template, setApplicationMenu() {} },
  };
  const source = fs.readFileSync(path.join(__dirname, 'main.cjs'), 'utf8');
  vm.runInNewContext(source, {
    require(name) {
      if (name === 'electron') return electron;
      if (name === './bridge.cjs') return { PythonBridge, channels:{}, validateCommand() {} };
      return require(name);
    },
    __dirname, process:{ platform, env:{} }, console,
  }, { filename:'main.cjs' });
  await Promise.resolve();
  return { app, windows, bridges, finishCleanup };
}

test('macOS red close button hides the existing window without stopping its backend', async () => {
  const { app, windows, bridges } = await loadMain();
  const window = windows[0], bridge = bridges[0];
  window.emit('ready-to-show');
  assert.equal(window.hidden, false);
  const close = window.attemptClose();
  assert.equal(close.prevented, true);
  assert.equal(window.hidden, true);
  assert.equal(window.destroyed, false);
  assert.equal(app.quitCalls, 0);
  assert.equal(bridge.closeCalls, 0);
  assert.deepEqual(bridge.requests, ['start_backend']);
  // Even loss of every window is not itself a macOS application quit.
  app.emit('window-all-closed');
  assert.equal(app.quitCalls, 0);
});

test('Dock activation and second-instance requests show the same hidden macOS window', async () => {
  const { app, windows, bridges } = await loadMain();
  const window = windows[0];
  window.attemptClose();
  app.emit('activate');
  assert.equal(window.hidden, false);
  assert.equal(window.focusCalls, 1);
  assert.equal(windows.length, 1);
  window.attemptClose(); window.minimized = true;
  app.emit('second-instance');
  assert.equal(window.hidden, false);
  assert.equal(window.restoreCalls, 1);
  assert.equal(window.focusCalls, 2);
  assert.equal(windows.length, 1);
  assert.equal(bridges.length, 1);
  assert.deepEqual(bridges[0].requests, ['start_backend']);
});

test('explicit Quit waits for owned-backend cleanup before destroying the hidden window', { timeout:1000 }, async () => {
  const { app, windows, bridges, finishCleanup } = await loadMain();
  const window = windows[0], bridge = bridges[0];
  window.attemptClose();
  const quit = new Promise(resolve => app.once('quit', resolve));
  app.quit();
  assert.equal(bridge.closeCalls, 1);
  assert.equal(window.destroyed, false);
  assert.equal(app.exited, false);
  app.emit('activate'); app.emit('second-instance'); app.quit();
  assert.equal(window.hidden, true);
  assert.equal(bridge.closeCalls, 1);
  assert.equal(windows.length, 1);
  finishCleanup({ clean:true });
  await quit;
  assert.equal(window.destroyed, true);
  assert.equal(app.exited, true);
  assert.equal(bridge.closeCalls, 1);
});

test('a rejected second application instance never creates a helper or window', async () => {
  const { app, windows, bridges } = await loadMain({ primary:false });
  assert.equal(app.exited, true);
  assert.equal(windows.length, 0);
  assert.equal(bridges.length, 0);
});
