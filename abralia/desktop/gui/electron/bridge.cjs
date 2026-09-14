// Copyright 2026 blue_lobster
// SPDX-License-Identifier: Apache-2.0
const { spawn } = require('node:child_process');

const MAX_LINE = 4 * 1024 * 1024;
const channels = Object.freeze({
  'abralia:overview': 'overview',
  'abralia:scan': 'scan_devices',
  'abralia:select-device': 'select_device',
  'abralia:project-mute': 'set_project_muted',
  'abralia:start-backend': 'start_backend',
  'abralia:stop-backend': 'stop_backend',
  'abralia:remove-project': 'remove_project',
  'abralia:integration-status': 'integration_status',
  'abralia:install-integration': 'install_integration',
  'abralia:remove-integration': 'remove_integration',
});

function validateCommand(command, args = {}) {
  if (!Object.values(channels).includes(command) && !['register_project', 'manual_hooks'].includes(command)) throw Error('Unsupported operation');
  if (!args || typeof args !== 'object' || Array.isArray(args)) throw Error('Invalid arguments');
  const allowed = command === 'select_device' ? ['device_id'] : command === 'set_project_muted'
    ? ['project_id', 'muted', 'expected_epoch'] : ['start_backend', 'remove_project'].includes(command) ? ['project_id'] : command === 'register_project' ? ['path'] : [];
  if (Object.keys(args).some(k => !allowed.includes(k))) throw Error('Unexpected argument');
  for (const key of allowed.filter(k => k !== 'muted')) {
    if (command === 'start_backend' && !(key in args)) continue;
    if (typeof args[key] !== 'string' || !args[key] || args[key].length > (key === 'path' ? 4096 : 512)) throw Error(`Invalid ${key}`);
  }
  if (command === 'set_project_muted' && typeof args.muted !== 'boolean') throw Error('Invalid mute setting');
  return args;
}

class PythonBridge {
  constructor(executable, args, options = {}) {
    this.pending = new Map(); this.nextId = 1; this.buffer = ''; this.closed = false;
    this.process = (options.spawn || spawn)(executable, args, {
      stdio: ['pipe', 'pipe', 'pipe'], shell: false, windowsHide: true,
      env: { ...process.env, PYTHONUNBUFFERED: '1', PYTHONDONTWRITEBYTECODE: '1' },
    });
    this.process.stdout.setEncoding('utf8');
    this.process.stdout.on('data', chunk => this.receive(chunk));
    this.process.stdout.on('error', () => this.fail('The backend helper disconnected.'));
    this.process.stdin.on?.('error', () => this.fail('The backend helper disconnected.'));
    this.process.stderr.on('data', () => {}); // GUI displays structured errors, never raw transcripts.
    this.process.on('error', () => this.fail('The bundled backend helper could not start.'));
    this.process.on('exit', () => this.fail('The backend helper stopped. Reopen Abralia to reconnect.'));
    this.timeout = options.timeout || 15000;
    this.lifecycleTimeout = options.lifecycleTimeout || 45000;
    this.installationTimeout = options.installationTimeout || 120000;
  }
  receive(chunk) {
    this.buffer += chunk;
    if (this.buffer.length > MAX_LINE) { this.fail('Backend response was too large.'); this.process.kill(); return; }
    let newline;
    while ((newline = this.buffer.indexOf('\n')) >= 0) {
      const line = this.buffer.slice(0, newline); this.buffer = this.buffer.slice(newline + 1);
      let row;
      try { row = JSON.parse(line); } catch { this.fail('Invalid backend response.'); this.process.kill(); return; }
      if (!row || typeof row !== 'object' || Array.isArray(row) || !Number.isSafeInteger(row.id) || typeof row.ok !== 'boolean') {
        this.fail('Invalid backend response.'); this.process.kill(); return;
      }
      const pending = this.pending.get(row.id);
      if (!pending) continue;
      clearTimeout(pending.timer); this.pending.delete(row.id);
      if (row.ok === true) pending.resolve(row.result);
      else pending.reject(new Error(row.error?.message || 'Backend request failed.'));
    }
  }
  request(command, args = {}) {
    validateCommand(command, args);
    if (this.closed) return Promise.reject(Error('The backend helper is unavailable. Reopen Abralia.'));
    if (this.pending.size >= 32) return Promise.reject(Error('The backend helper is busy. Try again shortly.'));
    const id = this.nextId++;
    const lifecycle = ['start_backend', 'stop_backend', 'select_device'].includes(command);
    const installation = ['install_integration', 'remove_integration'].includes(command);
    const timeout = installation || [...this.pending.values()].some(p => p.installation) ? this.installationTimeout
      : lifecycle || [...this.pending.values()].some(p => p.lifecycle) ? this.lifecycleTimeout : this.timeout;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => { this.pending.delete(id); reject(Error('Backend request timed out. Try refreshing.')); }, timeout);
      this.pending.set(id, { resolve, reject, timer, lifecycle, installation });
      this.process.stdin.write(JSON.stringify({ id, command, args }) + '\n', error => {
        if (error) { clearTimeout(timer); this.pending.delete(id); reject(Error('The backend helper disconnected.')); }
      });
    });
  }
  fail(message) {
    this.closed = true;
    for (const pending of this.pending.values()) { clearTimeout(pending.timer); pending.reject(Error(message)); }
    this.pending.clear();
  }
  close(grace = [...this.pending.values()].some(p => p.installation) ? 165000 : 45000) {
    if (this.closing) return this.closing;
    this.fail('Abralia is stopping its backend.');
    this.closing = new Promise(resolve => {
      if (this.process.exitCode !== null || this.process.signalCode != null) { resolve({ clean: this.process.exitCode === 0 }); return; }
      const timer = setTimeout(() => {
        resolve({ clean: false, reason: 'Backend shutdown timed out; restoration is unconfirmed.' });
        this.process.kill();
      }, grace);
      timer.unref();
      this.process.once('exit', code => { clearTimeout(timer); resolve({ clean: code === 0 }); });
      // EOF runs GuiHost.close(), including owned-backend RGB restoration.
      // Do not terminate the helper before its cleanup window has elapsed.
      this.process.stdin.end();
    });
    return this.closing;
  }
}

module.exports = { PythonBridge, validateCommand, channels };
