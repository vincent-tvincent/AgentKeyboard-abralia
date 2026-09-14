// Copyright 2026 blue_lobster
// SPDX-License-Identifier: Apache-2.0
const test = require('node:test');
const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const { PythonBridge, validateCommand } = require('./bridge.cjs');

function fakeProcess({ exitOnEnd = false } = {}) {
  const child = new EventEmitter();
  child.stdout = new EventEmitter(); child.stdout.setEncoding = () => {};
  child.stderr = new EventEmitter(); child.stdin = new EventEmitter();
  child.requests = []; child.endCalls = 0; child.killSignals = [];
  child.stdin.write = (line, callback) => { child.lastRequest = JSON.parse(line); child.requests.push(child.lastRequest); callback?.(); };
  child.stdin.end = () => { child.endCalls++; if (exitOnEnd) child.finish(0); };
  child.exitCode = null; child.signalCode = null;
  child.finish = (code = 0, signal = null) => {
    child.exitCode = code; child.signalCode = signal;
    child.emit('exit', code, signal);
  };
  child.kill = (signal = 'SIGTERM') => { child.killSignals.push(signal); child.finish(null, signal); return true; };
  return child;
}

function fakeClock(t) {
  let now = 0;
  const timers = new Map();
  t.mock.method(global, 'setTimeout', (callback, delay) => {
    const timer = { at: now + delay, unref() { return timer; } };
    timers.set(timer, callback);
    return timer;
  });
  t.mock.method(global, 'clearTimeout', timer => timers.delete(timer));
  return {
    advance(milliseconds) {
      const end = now + milliseconds;
      for (;;) {
        const timer = [...timers.keys()].filter(item => item.at <= end).sort((a, b) => a.at - b.at)[0];
        if (!timer) break;
        now = timer.at;
        const callback = timers.get(timer); timers.delete(timer); callback();
      }
      now = end;
    },
    pending: () => timers.size,
  };
}

test('management API refuses arbitrary commands and malformed mutations', () => {
  for (const operation of ['exec', 'shell', 'send_keys', 'release_all', 'install_skill']) assert.throws(() => validateCommand(operation));
  assert.throws(() => validateCommand('set_project_muted', { project_id:'p', muted:'false', expected_epoch:'epoch' }));
  assert.throws(() => validateCommand('overview', { path:'/etc/passwd' }));
  assert.throws(() => validateCommand('select_device', { device_id:'device', fingerprint:{} }));
  assert.doesNotThrow(() => validateCommand('set_project_muted', { project_id:'p', muted:false, expected_epoch:'epoch' }));
});

test('NDJSON handles split replies and multiple responses without mixing request IDs', async () => {
  const child = fakeProcess(); let spawnOptions;
  const bridge = new PythonBridge('/path with spaces/helper', ['--state-dir', '/a b'], { spawn:(_exe,_args,options) => { spawnOptions=options; return child; } });
  const first = bridge.request('overview'); const second = bridge.request('scan_devices');
  child.stdout.emit('data', '{"id":2,"ok":true,"result":{"devices":[]}}\n{"id":');
  child.stdout.emit('data', '1,"ok":true,"result":{"projects":[]}}\n');
  assert.deepEqual(await first, { projects:[] }); assert.deepEqual(await second, { devices:[] });
  assert.equal(spawnOptions.shell, false);
  const closing = bridge.close(); child.finish(0);
  assert.deepEqual(await closing, { clean:true });
});

test('malformed output stops the helper and rejects pending work', async () => {
  const child=fakeProcess(), bridge=new PythonBridge('helper',[],{spawn:()=>child});
  const reply=bridge.request('overview'); child.stdout.emit('data','not json\n');
  await assert.rejects(reply,/Invalid backend response/); assert.equal(bridge.closed,true);
});

test('valid JSON with an invalid response shape fails without a main-process crash', async () => {
  for (const value of ['null', '[]', '{"id":1}', '{"id":"1","ok":true}']) {
    const child=fakeProcess(), bridge=new PythonBridge('helper',[],{spawn:()=>child});
    const reply=bridge.request('overview'); child.stdout.emit('data',value+'\n');
    await assert.rejects(reply,/Invalid backend response/);
  }
});

test('pipe errors reject pending work instead of escaping as unhandled errors', async () => {
  const child=fakeProcess();
  child.stdin=Object.assign(new EventEmitter(),child.stdin);
  const bridge=new PythonBridge('helper',[],{spawn:()=>child});
  const reply=bridge.request('overview'); child.stdin.emit('error',Error('EPIPE'));
  await assert.rejects(reply,/disconnected/);
  assert.equal(bridge.pending.size,0); child.finish(1);
});

test('timeouts and helper exit release pending requests', async () => {
  const child=fakeProcess(), bridge=new PythonBridge('helper',[],{spawn:()=>child,timeout:15});
  await assert.rejects(bridge.request('overview'),/timed out/); assert.equal(bridge.pending.size,0);
  const reply=bridge.request('overview'); child.finish(1);
  await assert.rejects(reply,/helper stopped/); assert.equal(bridge.pending.size,0);
});

test('graceful close sends EOF and waits for cleanup without a premature signal', async t => {
  const clock = fakeClock(t), child = fakeProcess();
  const bridge = new PythonBridge('helper', [], { spawn:() => child });
  const pending = bridge.request('overview');
  const rejected = assert.rejects(pending, /stopping its backend/);
  const closing = bridge.close();
  let settled = false; closing.then(() => { settled = true; });
  assert.equal(child.endCalls, 1);
  assert.equal(bridge.pending.size, 0);
  assert.equal(child.exitCode, null);
  clock.advance(44999);
  await Promise.resolve();
  assert.equal(settled, false);
  assert.deepEqual(child.killSignals, []);
  child.finish(0);
  assert.deepEqual(await closing, { clean:true });
  await rejected;
  assert.equal(clock.pending(), 0);
});

test('repeated close calls share one promise and one EOF even with different grace', async t => {
  const clock = fakeClock(t), child = fakeProcess();
  const bridge = new PythonBridge('helper', [], { spawn:() => child });
  const first = bridge.close(100);
  assert.strictEqual(bridge.close(1), first);
  assert.equal(child.endCalls, 1);
  clock.advance(2);
  assert.deepEqual(child.killSignals, []);
  child.finish(0);
  assert.deepEqual(await first, { clean:true });
  assert.equal(clock.pending(), 0);
});

test('shutdown timeout reports unconfirmed cleanup and signals only after grace', async t => {
  const clock = fakeClock(t), child = fakeProcess();
  const bridge = new PythonBridge('helper', [], { spawn:() => child });
  const closing = bridge.close(100);
  clock.advance(99);
  assert.deepEqual(child.killSignals, []);
  clock.advance(1);
  const result = await closing;
  assert.equal(result.clean, false);
  assert.match(result.reason, /timed out; restoration is unconfirmed/);
  assert.deepEqual(child.killSignals, ['SIGTERM']);
  assert.equal(clock.pending(), 0);
  assert.strictEqual(bridge.close(), closing);
});

test('a helper that exits immediately on EOF resolves cleanly', async t => {
  const clock = fakeClock(t), child = fakeProcess({ exitOnEnd:true });
  const bridge = new PythonBridge('helper', [], { spawn:() => child });
  assert.deepEqual(await bridge.close(), { clean:true });
  assert.equal(child.endCalls, 1);
  assert.equal(clock.pending(), 0);
});

test('already exited helpers return actual clean or failed state without another cleanup wait', async t => {
  const clock = fakeClock(t);
  for (const [code, signal, clean] of [[0, null, true], [1, null, false], [null, 'SIGTERM', false]]) {
    const child = fakeProcess(), bridge = new PythonBridge('helper', [], { spawn:() => child });
    child.finish(code, signal);
    const closing = bridge.close();
    let result; closing.then(value => { result = value; });
    await Promise.resolve();
    assert.deepEqual(result, { clean });
    assert.equal(child.endCalls, 0);
    assert.deepEqual(child.killSignals, []);
    assert.equal(clock.pending(), 0);
  }
});

for (const [command, args] of [['start_backend', {}], ['stop_backend', {}], ['select_device', { device_id:'device' }]]) {
  test(`${command} and reads queued behind it receive the longer lifecycle timeout`, async t => {
    const clock = fakeClock(t), child = fakeProcess();
    const bridge = new PythonBridge('helper', [], { spawn:() => child, timeout:10, lifecycleTimeout:100 });
    const lifecycle = bridge.request(command, args);
    const read = bridge.request('overview');
    clock.advance(11);
    assert.equal(bridge.pending.size, 2);
    child.stdout.emit('data', JSON.stringify({ id:child.requests[0].id, ok:true, result:{ ready:true } }) + '\n');
    child.stdout.emit('data', JSON.stringify({ id:child.requests[1].id, ok:true, result:{ projects:[] } }) + '\n');
    assert.deepEqual(await lifecycle, { ready:true });
    assert.deepEqual(await read, { projects:[] });
    const normal = bridge.request('overview');
    const rejected = assert.rejects(normal, /timed out/);
    clock.advance(10);
    await rejected;
    assert.equal(bridge.pending.size, 0);
    child.finish(0);
    assert.deepEqual(await bridge.close(), { clean:true });
    assert.equal(clock.pending(), 0);
  });
}

test('a lifecycle request eventually times out and releases its pending slot', async t => {
  const clock = fakeClock(t), child = fakeProcess();
  const bridge = new PythonBridge('helper', [], { spawn:() => child, timeout:10, lifecycleTimeout:100 });
  const reply = bridge.request('start_backend');
  const rejected = assert.rejects(reply, /timed out/);
  clock.advance(100);
  await rejected;
  assert.equal(bridge.pending.size, 0);
  child.finish(0);
  assert.deepEqual(await bridge.close(), { clean:true });
});

test('packaged runtime links stay relative and survive removal of the build source', async () => {
  const fs=require('node:fs'), path=require('node:path'), os=require('node:os');
  const {copyBackendRuntime}=await import('../scripts/copy-backend.mjs');
  const temp=fs.mkdtempSync(path.join(os.tmpdir(),'abralia-bundle-test-'));
  try {
    const source=path.join(temp,'source'), target=path.join(temp,'app');
    fs.mkdirSync(source);fs.writeFileSync(path.join(source,'library'),'runtime');
    fs.symlinkSync('library',path.join(source,'Python'));
    copyBackendRuntime(source,target);
    fs.rmSync(source,{recursive:true});
    assert.equal(fs.readlinkSync(path.join(target,'Python')),'library');
    assert.equal(fs.readFileSync(path.join(target,'Python'),'utf8'),'runtime');
    const bad=path.join(temp,'bad');fs.mkdirSync(bad);
    fs.symlinkSync(path.join(target,'library'),path.join(bad,'external'));
    assert.throws(()=>copyBackendRuntime(bad,path.join(temp,'bad-app')),/escapes the app/);
  } finally { fs.rmSync(temp,{recursive:true,force:true}); }
});
