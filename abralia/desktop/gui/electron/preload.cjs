// Copyright 2026 blue_lobster
// SPDX-License-Identifier: Apache-2.0
const { contextBridge, ipcRenderer } = require('electron');
contextBridge.exposeInMainWorld('abralia', Object.freeze({
  overview: () => ipcRenderer.invoke('abralia:overview'),
  scanDevices: () => ipcRenderer.invoke('abralia:scan'),
  selectDevice: device_id => ipcRenderer.invoke('abralia:select-device', { device_id }),
  setProjectMuted: (project_id, muted, expected_epoch) => ipcRenderer.invoke('abralia:project-mute', { project_id, muted, expected_epoch }),
  startBackend: project_id => ipcRenderer.invoke('abralia:start-backend', project_id ? { project_id } : {}),
  stopBackend: () => ipcRenderer.invoke('abralia:stop-backend'),
  addProject: () => ipcRenderer.invoke('abralia:add-project'),
  removeProject: project_id => ipcRenderer.invoke('abralia:remove-project', { project_id }),
  integrationStatus: () => ipcRenderer.invoke('abralia:integration-status'),
  installIntegration: () => ipcRenderer.invoke('abralia:install-integration'),
  removeIntegration: () => ipcRenderer.invoke('abralia:remove-integration'),
  copyHooks: () => ipcRenderer.invoke('abralia:copy-hooks'),
}));
