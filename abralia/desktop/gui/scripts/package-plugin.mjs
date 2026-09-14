// Copyright 2026 blue_lobster
// SPDX-License-Identifier: Apache-2.0

import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawnSync } from 'node:child_process';

const gui = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

export function validatePluginBundle(bundlePath) {
  const root = path.resolve(bundlePath);
  function walk(folder) {
    for (const name of fs.readdirSync(folder)) {
      const file = path.join(folder, name);
      const info = fs.lstatSync(file);
      if (info.isSymbolicLink() || (!info.isDirectory() && !info.isFile())) {
        throw Error(`Plugin bundle must contain only regular files/directories: ${path.relative(root, file)}`);
      }
      if (info.isDirectory()) walk(file);
    }
  }
  if (fs.lstatSync(root).isSymbolicLink()) throw Error('Plugin bundle root cannot be a symlink.');
  walk(root);
  const marketplace = JSON.parse(fs.readFileSync(path.join(root, '.agents/plugins/marketplace.json')));
  const plugin = path.join(root, 'plugins/abralia');
  const manifest = JSON.parse(fs.readFileSync(path.join(plugin, '.codex-plugin/plugin.json')));
  if (marketplace.name !== 'abralia' || manifest.name !== 'abralia' ||
      !/^\d+\.\d+\.\d+(?:[-+][A-Za-z0-9.-]+)?$/.test(manifest.version)) {
    throw Error('Expected a versioned Abralia plugin and Abralia marketplace.');
  }
  if (marketplace.plugins?.length !== 1 || marketplace.plugins[0].name !== 'abralia' ||
      marketplace.plugins[0].source?.source !== 'local' ||
      marketplace.plugins[0].source?.path !== './plugins/abralia') {
    throw Error('Marketplace must resolve exactly the bundled Abralia plugin.');
  }
  for (const file of ['.mcp.json', 'hooks/hooks.json', 'skills/abralia/SKILL.md',
    'scripts/abralia-launch', 'README.md', 'LICENSE']) {
    if (!fs.statSync(path.join(plugin, file)).isFile()) throw Error(`Missing plugin file: ${file}`);
  }
  if (!fs.statSync(path.join(root, 'README.md')).isFile()) throw Error('Missing bundle installation guide.');
  return { root, version: manifest.version };
}

export function packagePlugin({ bundlePath = path.resolve(gui, '../plugin-bundle'), outDir = path.join(gui, 'out') } = {}) {
  const { root, version } = validatePluginBundle(bundlePath);
  fs.mkdirSync(outDir, { recursive: true });
  const zipPath = path.resolve(outDir, `Abralia-Codex-Plugin-${version}.zip`);
  const stage = fs.mkdtempSync(path.join(os.tmpdir(), 'abralia-plugin-'));
  try {
    fs.cpSync(root, path.join(stage, 'abralia-codex'), { recursive: true, verbatimSymlinks: true });
    const temporary = path.join(stage, 'plugin.zip');
    const result = spawnSync('/usr/bin/zip', ['-q', '-r', '-X', temporary, 'abralia-codex'],
      { cwd: stage, encoding: 'utf8', shell: false });
    if (result.error || result.status !== 0) throw Error('Could not archive the Abralia plugin bundle.');
    fs.copyFileSync(temporary, zipPath);
    return { zipPath, version };
  } finally {
    fs.rmSync(stage, { recursive: true, force: true });
  }
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  console.log(`CODEX_PLUGIN_ZIP=${packagePlugin().zipPath}`);
}
