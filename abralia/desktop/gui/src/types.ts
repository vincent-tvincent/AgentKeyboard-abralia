// Copyright 2026 blue_lobster
// SPDX-License-Identifier: Apache-2.0
export type Device = {
  id: string; name: string; profile_id: string; profile_name?: string; serial?: string;
  connected: boolean; experimental?: boolean; selectable: boolean; selection_reason?: string | null;
};
export type Project = {
  id: string; name: string; path: string; muted: boolean | null; task_count: number;
  connected_count: number; connected: boolean; backend_epoch: string | null;
  can_mute: boolean; restart_required: boolean; profile?: string; selected_device_id?: string | null;
  delivery?: string; device_error?: string | null;
  shared?: boolean; enrolled?: boolean; hooks_received?: number; last_hook_at?: string | number | null;
};
export type Notice = { code: string; message: string };
export type Backend = { state: 'stopped' | 'starting' | 'ready' | 'external' | 'error'; owned: boolean;
  project_id?: string | null; project_name?: string | null; error?: string | null; mode?: string;
  reason?: string | null; project_choices?: { id: string; name: string; path: string }[] };
export type Integration = { installed: boolean; version?: string | null; source?: string | null;
  runtime_available?: boolean; error?: string | null; trust?: string; restart_required?: boolean };
export type Overview = { devices: Device[]; selected_device: Device | null; projects: Project[]; errors: Notice[];
  backend?: Backend; shared?: boolean; integration?: Integration };
export type SelectResult = { selected_device: Device; saved: boolean; applied: { project_id: string; status: string; reason?: string; detail?: string }[]; errors: Notice[] };
export type AbraliaAPI = {
  overview(): Promise<Overview>;
  scanDevices(): Promise<{ devices: Device[]; errors: Notice[]; selected_device?: Device | null }>;
  selectDevice(id: string): Promise<SelectResult>;
  setProjectMuted(id: string, muted: boolean, epoch: string): Promise<{ project: Project }>;
  startBackend(project_id?: string): Promise<{ backend: Backend }>;
  stopBackend(): Promise<{ backend: Backend }>;
  addProject(): Promise<Overview | { cancelled: true }>;
  removeProject(project_id: string): Promise<Overview>;
  integrationStatus(): Promise<Integration>;
  installIntegration(): Promise<Integration>;
  removeIntegration(): Promise<Integration>;
  copyHooks(): Promise<{ copied: boolean }>;
};
declare global { interface Window { abralia?: AbraliaAPI } }
