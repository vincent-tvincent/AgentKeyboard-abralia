// Copyright 2026 blue_lobster
// SPDX-License-Identifier: Apache-2.0
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawnSync } = require('node:child_process');

test('plugin release preserves hidden marketplace and manifest files in one portable root', async () => {
  const { packagePlugin } = await import('../scripts/package-plugin.mjs');
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'abralia-plugin-test-'));
  try {
    const { zipPath } = packagePlugin({ outDir: root });
    const files = spawnSync('/usr/bin/unzip', ['-Z1', zipPath], { encoding: 'utf8' });
    assert.equal(files.status, 0);
    assert.ok(files.stdout.includes('abralia-codex/.agents/plugins/marketplace.json'));
    assert.ok(files.stdout.includes('abralia-codex/plugins/abralia/.codex-plugin/plugin.json'));
    assert.ok(files.stdout.includes('abralia-codex/plugins/abralia/.mcp.json'));
    assert.ok(files.stdout.trim().split('\n').every(file => file.startsWith('abralia-codex/')));
    assert.ok(!files.stdout.includes('__MACOSX'));
  } finally { fs.rmSync(root, { recursive: true, force: true }); }
});

test('plugin release rejects symlinks to prevent packaging external machine state', async () => {
  const { validatePluginBundle } = await import('../scripts/package-plugin.mjs');
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'abralia-plugin-test-'));
  try {
    fs.symlinkSync('/private/tmp', path.join(root, 'outside'));
    assert.throws(() => validatePluginBundle(root), /regular files/);
  } finally { fs.rmSync(root, { recursive: true, force: true }); }
});
