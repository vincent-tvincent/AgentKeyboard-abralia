// Copyright 2026 blue_lobster
// SPDX-License-Identifier: Apache-2.0

// Stage outside sync folders before verifying and archiving a macOS preview.
// The source app is never modified; quarantine is preserved, never removed.
import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { fileURLToPath } from 'node:url';

const exec = promisify(execFile);
const DETRITUS = new Set(['com.apple.FinderInfo', 'com.apple.ResourceFork']);
const run = (executable, args, options = {}) => exec(executable, args, {
  shell: false, maxBuffer: 32 * 1024 * 1024, ...options,
});

function contained(root, candidate) {
  const relative = path.relative(root, candidate);
  return relative === '' || (!relative.startsWith('..' + path.sep) && relative !== '..' && !path.isAbsolute(relative));
}

async function entries(root) {
  const result = [];
  async function visit(relative) {
    const filename = path.join(root, relative);
    const info = await fs.lstat(filename);
    result.push(relative);
    if (info.isSymbolicLink()) {
      const target = await fs.readlink(filename);
      if (path.isAbsolute(target) || !contained(root, path.resolve(path.dirname(filename), target))) {
        throw Error(`App has a non-portable symlink: ${relative}`);
      }
      if (!contained(root, await fs.realpath(filename))) throw Error(`App symlink escapes its bundle: ${relative}`);
    } else if (info.isDirectory()) {
      for (const child of (await fs.readdir(filename)).sort()) await visit(path.join(relative, child));
    }
  }
  await visit('');
  return result;
}

async function attributeNames(filename) {
  const { stdout } = await run('/usr/bin/xattr', ['-s', filename]);
  return stdout.split('\n').map(name => name.trim()).filter(Boolean);
}

async function attributeHex(filename, name) {
  const { stdout } = await run('/usr/bin/xattr', ['-p', '-x', '-s', name, filename]);
  const hex = stdout.replace(/\s/g, '');
  if (!/^(?:[a-fA-F0-9]{2})*$/.test(hex)) throw Error('Unexpected extended-attribute encoding.');
  return hex;
}

async function copyMetadata(source, destination, relative, warnings) {
  const from = path.join(source, relative), to = path.join(destination, relative);
  const names = await attributeNames(from);
  for (const name of names) {
    if (DETRITUS.has(name)) continue;
    try {
      const hex = await attributeHex(from, name);
      await run('/usr/bin/xattr', ['-w', '-x', '-s', name, hex, to]);
    } catch (error) {
      if (name === 'com.apple.quarantine') {
        throw Error(`Cannot preserve quarantine metadata on ${relative || path.basename(source)}.`, { cause: error });
      }
      // Some provider/filesystem attributes are not writable outside their
      // original volume. Report that boundary without printing their values.
      warnings.push({ path: relative || '.', attribute: name, reason: 'attribute_not_transferable' });
    }
  }
  for (const name of await attributeNames(to)) {
    if (DETRITUS.has(name)) await run('/usr/bin/xattr', ['-d', '-s', name, to]);
  }
}

async function boundedMap(items, workers, callback) {
  let next = 0;
  await Promise.all(Array.from({ length: Math.min(workers, items.length) }, async () => {
    while (next < items.length) await callback(items[next++]);
  }));
}

async function verify(bundle) {
  await run('/usr/bin/codesign', ['--verify', '--deep', '--strict', bundle]);
}

/**
 * Keep stagingApp available for local testing; the caller owns its cleanup.
 * allowAdHocResign is only for an explicitly selected unsigned/ad-hoc preview.
 * A Developer ID signature is never silently replaced by ad-hoc signing.
 */
export async function archiveMac({ appPath, zipPath, allowAdHocResign = false }) {
  if (process.platform !== 'darwin') throw Error('macOS archiving requires a macOS host.');
  if (!appPath || !zipPath) throw Error('appPath and zipPath are required.');
  const source = await fs.realpath(path.resolve(appPath));
  const archive = path.resolve(zipPath);
  if (!source.endsWith('.app') || !(await fs.stat(source)).isDirectory()) throw Error('appPath must be an app bundle.');
  if (!archive.endsWith('.zip') || contained(source, archive)) throw Error('zipPath must be a ZIP outside the app bundle.');
  const files = await entries(source);
  const stageRoot = await fs.mkdtemp(path.join(os.tmpdir(), 'abralia-mac-stage-'));
  const stagingApp = path.join(stageRoot, path.basename(source));
  const metadataWarnings = [];
  try {
    await fs.cp(source, stagingApp, { recursive: true, dereference: false,
      verbatimSymlinks: true, preserveTimestamps: true, errorOnExist: true, force: false });
    // fs.cp does not guarantee xattr preservation. Copy transferable attributes
    // explicitly, then delete only the two attributes rejected by codesign.
    await boundedMap(files, 8, relative => copyMetadata(source, stagingApp, relative, metadataWarnings));
    let signature = 'verified';
    try {
      await verify(stagingApp);
    } catch (verificationError) {
      if (!allowAdHocResign) throw verificationError;
      let details = '';
      try { details = (await run('/usr/bin/codesign', ['--display', '--verbose=4', stagingApp])).stderr; }
      catch (error) { details = error.stderr || ''; }
      if (/^Authority=/m.test(details)) throw Error('Refusing to replace an existing identified signing authority.');
      await run('/usr/bin/codesign', ['--force', '--deep', '--sign', '-', '--timestamp=none', stagingApp]);
      await verify(stagingApp);
      signature = 'ad-hoc-signed';
    }
    const localZip = path.join(stageRoot, 'archive.zip');
    await run('/usr/bin/zip', ['-q', '-r', '-y', '-X', localZip, path.basename(stagingApp),
      '-x', '*/._*', '*/__MACOSX/*'], { cwd: stageRoot });
    const { stdout } = await run('/usr/bin/unzip', ['-Z1', localZip]);
    if (stdout.split('\n').some(name => /(^|\/)(__MACOSX|\._[^/]*)($|\/)/.test(name))) {
      throw Error('Archive unexpectedly contains resource-fork sidecars.');
    }
    await fs.mkdir(path.dirname(archive), { recursive: true });
    const temporary = path.join(path.dirname(archive), `.${path.basename(archive)}.${process.pid}.tmp`);
    try {
      await fs.copyFile(localZip, temporary);
      // ZIP intentionally has no AppleDouble entries. Preserve root quarantine
      // on the container itself when the source app carried that attribute.
      if ((await attributeNames(source)).includes('com.apple.quarantine')) {
        const hex = await attributeHex(source, 'com.apple.quarantine');
        await run('/usr/bin/xattr', ['-w', '-x', 'com.apple.quarantine', hex, temporary]);
      }
      await fs.rename(temporary, archive);
    } finally { await fs.rm(temporary, { force: true }); }
    return { stagingApp, zipPath: archive, signature, metadataWarnings };
  } catch (error) {
    await fs.rm(stageRoot, { recursive: true, force: true });
    throw error;
  }
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const args = process.argv.slice(2);
  const get = flag => { const index = args.indexOf(flag); return index >= 0 ? args[index + 1] : undefined; };
  try {
    const result = await archiveMac({ appPath: get('--app'), zipPath: get('--zip'), allowAdHocResign: args.includes('--ad-hoc') });
    console.log(JSON.stringify(result));
  } catch (error) {
    console.error(error.message);
    process.exitCode = 1;
  }
}
