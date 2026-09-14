// Copyright 2026 blue_lobster
// SPDX-License-Identifier: Apache-2.0
import fs from 'node:fs';
import path from 'node:path';

export function copyBackendRuntime(source, destination) {
  // Node's default cp resolves relative links to absolute source paths. A
  // packaged Python framework must retain its internal relative links.
  fs.cpSync(source, destination, { recursive: true, verbatimSymlinks: true });
  const realRoot = fs.realpathSync(destination);
  function inspect(directory) {
    for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
      const file = path.join(directory, entry.name);
      if (entry.isSymbolicLink()) {
        const target = fs.readlinkSync(file);
        const resolved = fs.realpathSync(file);
        const relative = path.relative(realRoot, resolved);
        if (path.isAbsolute(target) || relative.startsWith('..') || path.isAbsolute(relative)) {
          throw Error(`Backend library link escapes the app: ${path.relative(destination, file)}`);
        }
      } else if (entry.isDirectory()) inspect(file);
    }
  }
  inspect(destination);
}
