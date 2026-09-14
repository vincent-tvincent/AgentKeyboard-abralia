// Copyright 2026 blue_lobster
// SPDX-License-Identifier: Apache-2.0
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import { fileURLToPath } from 'node:url';
import { spawnSync } from 'node:child_process';
import { packager } from '@electron/packager';
import { copyBackendRuntime } from './copy-backend.mjs';
import { archiveMac } from './archive-mac.mjs';
import { packagePlugin, validatePluginBundle } from './package-plugin.mjs';

const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const repository=path.resolve(root, '../../..');
if(process.platform!=='darwin')throw Error('This first package target is macOS only.');
function run(executable,args){
 const result=spawnSync(executable,args,{cwd:root,stdio:'inherit',shell:false});
 if(result.error)throw result.error;
 if(result.status!==0)throw Error(`${path.basename(executable)} failed (${result.status}).`);
}
const build=path.join(root,'.build');
fs.mkdirSync(build,{recursive:true});
const pluginBundle=validatePluginBundle(path.resolve(root,'../plugin-bundle')).root;
console.log(`CODEX_PLUGIN_ZIP=${packagePlugin({bundlePath:pluginBundle}).zipPath}`);
const python=process.env.ABRALIA_PYTHON || path.join(repository,'.venv/bin/python');
run(python,['-m','PyInstaller','--noconfirm','--clean','--onedir','--name','abralia-gui-host',
 '--distpath',path.join(build,'backend'),'--workpath',path.join(build,'pyinstaller'),
 '--specpath',build,'--paths',path.join(repository,'abralia/desktop/src'),
 '--copy-metadata','abralia-desktop',
 '--add-data',`${path.join(repository,'abralia/desktop/src/abralia/resources')}:abralia/resources`,
 path.join(root,'scripts/frozen_host.py')]);

const iconset=path.join(build,'Abralia.iconset');
fs.mkdirSync(iconset,{recursive:true});
const logo=path.join(repository,'docs/Abralia_logo.png');
for(const size of [16,32,128,256,512]){
 run('/usr/bin/sips',['-z',String(size),String(size),logo,'--out',path.join(iconset,`icon_${size}x${size}.png`)]);
 run('/usr/bin/sips',['-z',String(size*2),String(size*2),logo,'--out',path.join(iconset,`icon_${size}x${size}@2x.png`)]);
}
const icon=path.join(build,'Abralia.icns');
run('/usr/bin/iconutil',['-c','icns',iconset,'-o',icon]);
const appdir=path.join(build,'application');
fs.rmSync(appdir,{recursive:true,force:true});fs.mkdirSync(appdir,{recursive:true});
for(const dir of ['dist','electron']) fs.cpSync(path.join(root,dir),path.join(appdir,dir),{recursive:true});
const pkg=JSON.parse(fs.readFileSync(path.join(root,'package.json')));
fs.writeFileSync(path.join(appdir,'package.json'),JSON.stringify({name:pkg.name,productName:'Abralia',version:pkg.version,main:pkg.main,license:pkg.license,author:pkg.author}));
fs.cpSync(path.join(repository,'LICENSES'),path.join(appdir,'LICENSES'),{recursive:true});
fs.copyFileSync(path.join(repository,'LICENSE.md'),path.join(appdir,'LICENSE.md'));
fs.copyFileSync(path.join(root,'THIRD_PARTY_NOTICES.md'),path.join(appdir,'THIRD_PARTY_NOTICES.md'));
fs.mkdirSync(path.join(appdir,'third-party'),{recursive:true});
for(const name of ['react','react-dom']) fs.copyFileSync(path.join(root,'node_modules',name,'LICENSE'),path.join(appdir,'third-party',`${name}-LICENSE.txt`));
const outputs=await packager({dir:appdir,name:'Abralia',appBundleId:'cc.abralia.control-panel',
 electronVersion:pkg.devDependencies.electron,
 appVersion:pkg.version,buildVersion:pkg.version,platform:'darwin',arch:process.arch,
 out:fs.mkdtempSync(path.join(os.tmpdir(),'abralia-package-')),overwrite:true,asar:true,icon,
 extendInfo:{NSHumanReadableCopyright:'Copyright 2026 blue_lobster. Apache-2.0.',
  NSAppleEventsUsageDescription:'Abralia brings the terminal for your selected agent to the front when you pick up a keyboard notification.'},
});
for(const output of outputs){
 const bundle=path.join(output,'Abralia.app');
 copyBackendRuntime(path.join(build,'backend'),path.join(bundle,'Contents/Resources/backend'));
 fs.cpSync(pluginBundle,path.join(bundle,'Contents/Resources/plugin-bundle'),{recursive:true,verbatimSymlinks:true});
 // Local preview signing only. Public releases still need Developer ID and notarization.
 run('/usr/bin/codesign',['--force','--deep','--sign','-',bundle]);
 run('/usr/bin/codesign',['--verify','--deep','--strict',bundle]);
 const archive=await archiveMac({appPath:bundle,zipPath:path.join(root,'out',`Abralia-macos-${process.arch}-preview.zip`)});
 console.log(`MACOS_APP=${archive.stagingApp}`);
 console.log(`MACOS_ZIP=${archive.zipPath}`);
}
