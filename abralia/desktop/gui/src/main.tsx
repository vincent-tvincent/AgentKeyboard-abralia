// Copyright 2026 blue_lobster
// SPDX-License-Identifier: Apache-2.0
import React, { useCallback, useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import type { Device, Overview, Project } from './types';
import './styles.css';

function Icon({ name, size = 20 }: { name: string; size?: number }) {
  const paths: Record<string, React.ReactNode> = {
    keyboard: <><rect x="2" y="5" width="20" height="14" rx="3" /><path d="M6 9h.01M10 9h.01M14 9h.01M18 9h.01M6 13h.01M10 13h.01M14 13h.01M18 13h.01M8 16h8" /></>,
    projects: <><path d="M3 7a2 2 0 0 1 2-2h5l2 2h7a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z" /></>,
    refresh: <><path d="M20 7v5h-5M4 17v-5h5" /><path d="M6 7a7 7 0 0 1 12-1l2 3M4 15l2 3a7 7 0 0 0 12-1" /></>,
    check: <path d="m5 12 4 4L19 6" />,
    bell: <><path d="M5 17h14l-2-3V9a5 5 0 0 0-10 0v5ZM10 21h4" /></>,
    quiet: <><path d="m3 3 18 18M6 10v4l-2 3h13M10 4a5 5 0 0 1 8 4v6M10 21h4" /></>,
    arrow: <><path d="M5 12h14m-5-5 5 5-5 5" /></>,
    info: <><circle cx="12" cy="12" r="9" /><path d="M12 11v6m0-10h.01" /></>,
    plug: <><path d="M8 3v5m8-5v5M6 8h12v3a6 6 0 0 1-12 0ZM12 17v5" /></>,
  };
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[name] || paths.info}</svg>;
}

function KeyboardArt({ selected }: { selected: boolean }) {
  return <div className={`keyboard-art ${selected ? 'selected' : ''}`} aria-hidden="true"><div className="board">
    <div className="board-top">{Array.from({ length: 14 }, (_, i) => <i key={i} className={i > 0 && i < 6 ? `tint tint-${i}` : ''} />)}<b /></div>
    {[13, 13, 12, 12].map((n, row) => <div className="board-row" key={row}>{Array.from({ length: n }, (_, col) => <i key={col} />)}</div>)}
    <div className="board-bottom"><i /><i /><i /><i className="space" /><i /><i /><i /></div>
  </div><div className="art-label"><span /> {selected ? 'Your selected keyboard' : 'A home for your agents'}</div></div>;
}

const initial: Overview = { devices: [], selected_device: null, projects: [], errors: [], backend: { state: 'starting', owned: false } };

function App() {
  const [data, setData] = useState<Overview>(initial);
  const [page, setPage] = useState<'devices' | 'projects' | 'integrations'>('devices');
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState('');
  const [feedback, setFeedback] = useState('');
  const [updated, setUpdated] = useState('');
  const inFlight = useRef(false);
  const mutating = useRef(false);
  const api = window.abralia;

  const refresh = useCallback(async (rescan = false) => {
    if (inFlight.current || mutating.current) return;
    if (!api) { setError('Open this control panel from the Abralia desktop app.'); setLoading(false); return; }
    inFlight.current = true;
    try {
      if (rescan) await api.scanDevices();
      const next = await api.overview(); setData(next); setError('');
      setUpdated(new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }));
    } catch (e) { setError(e instanceof Error ? e.message : 'Unable to reach the backend helper.'); }
    finally { inFlight.current = false; setLoading(false); }
  }, [api]);

  useEffect(() => {
    void refresh();
    const timer = setInterval(() => { if (!document.hidden) void refresh(); }, 3000);
    const visible = () => { if (!document.hidden) void refresh(); };
    document.addEventListener('visibilitychange', visible);
    return () => { clearInterval(timer); document.removeEventListener('visibilitychange', visible); };
  }, [refresh]);

  async function select(device: Device) {
    if (!api || busy) return;
    mutating.current = true; setBusy(device.id); setFeedback(''); setError('');
    try {
      const result = await api.selectDevice(device.id);
      setData(d => ({ ...d, selected_device: result.selected_device }));
      const failed = result.applied.find(a => a.status !== 'accepted');
      setFeedback(failed ? `Selection saved, but the backend could not use it: ${failed.detail || failed.reason || 'device unavailable'}.`
        : result.errors.length ? result.errors.map(e => e.code === 'selection_requires_backend_choice'
          ? 'Selection saved. More than one backend matches. Keep the intended backend running, then select this keyboard again.' : e.message).join(' ')
        : 'Keyboard selected and remembered.');
      await refresh();
    } catch (e) { setError(e instanceof Error ? e.message : 'Could not select this keyboard.'); }
    finally { mutating.current = false; setBusy(null); void refresh(); }
  }

  async function mute(project: Project) {
    if (!api || busy || !project.backend_epoch || !project.can_mute) return;
    mutating.current = true; setBusy(project.id); setFeedback(''); setError('');
    try {
      const result = await api.setProjectMuted(project.id, !project.muted, project.backend_epoch);
      setData(d => ({ ...d, projects: d.projects.map(p => p.id === result.project.id ? result.project : p) }));
      setFeedback(result.project.muted ? `${project.name} is muted. Its agents keep working.` : `Notifications restored for ${project.name}.`);
    } catch (e) { setError(e instanceof Error ? e.message : 'Could not update project mute.'); }
    finally { mutating.current = false; setBusy(null); void refresh(); }
  }

  async function backendAction(action: 'start' | 'stop', project?: Project) {
    if (!api || busy) return;
    mutating.current = true; setBusy(action); setFeedback(''); setError('');
    try {
      const result = action === 'start' ? await api.startBackend(project?.id) : await api.stopBackend();
      setData(d => ({ ...d, backend: result.backend }));
      if (result.backend.error) setError(result.backend.error);
      else setFeedback(action === 'stop' ? 'Backend stopped. Your keyboard lighting has been released.' : 'Backend ready.');
    } catch (e) { setError(e instanceof Error ? e.message : 'Could not update the backend.'); }
    finally { mutating.current = false; setBusy(null); void refresh(); }
  }

  async function addProject() {
    if (!api || busy) return;
    mutating.current = true; setBusy('add-project'); setError(''); setFeedback('');
    try {
      const result = await api.addProject();
      if ('projects' in result) { setData(result); setFeedback('Project enabled for Abralia.'); }
    } catch (e) { setError(e instanceof Error ? e.message : 'Could not enable this project.'); }
    finally { mutating.current = false; setBusy(null); void refresh(); }
  }

  async function removeProject(project: Project) {
    if (!api || busy) return;
    mutating.current = true; setBusy(project.id); setError(''); setFeedback('');
    try { setData(await api.removeProject(project.id)); setFeedback(`${project.name} is no longer enabled in Abralia.`); }
    catch (e) { setError(e instanceof Error ? e.message : 'Could not remove this project.'); }
    finally { mutating.current = false; setBusy(null); void refresh(); }
  }

  async function integrationAction(action: 'install' | 'remove') {
    if (!api || busy) return;
    mutating.current = true; setBusy(`${action}-integration`); setError(''); setFeedback('');
    try {
      const result = action === 'install' ? await api.installIntegration() : await api.removeIntegration();
      setData(d => ({ ...d, integration: result }));
      if (result.error) setError(result.error);
      else setFeedback(action === 'install' ? 'Abralia plugin installed. Review its hooks in Codex, then refresh the plugin or start a new task.' : 'Abralia plugin removed from Codex.');
    } catch (e) { setError(e instanceof Error ? e.message : 'Could not update the Codex integration.'); }
    finally { mutating.current = false; setBusy(null); void refresh(); }
  }

  const connected = data.projects.filter(p => p.connected);
  const muted = connected.filter(p => p.muted).length;
  const selected = data.selected_device;
  const backend = data.backend;
  const running = backend?.state === 'ready' || backend?.state === 'external';
  const backendLabel = busy === 'stop' ? 'Restoring keyboard…' : busy === 'start' || backend?.state === 'starting' ? 'Starting backend…'
    : running ? 'Backend running' : backend?.state === 'error' ? 'Backend needs attention' : 'Backend stopped';
  const problems = [...new Set([...data.errors.map(e => e.message), ...(backend?.error ? [backend.error] : []), ...connected.filter(p => p.device_error).map(p => p.device_error!)])];
  const needsRestart = connected.some(p => p.restart_required);
  const chooseProject = !data.shared && data.projects.length > 1;
  const integration = data.integration;
  return <div className="app-shell">
    <aside className="sidebar">
      <div className="traffic-space" />
      <div className="brand"><img src="./logo.png" alt="" /><span>Abralia<small>AGENT KEYBOARD</small></span></div>
      <div className="sidebar-label">YOUR WORKSPACE</div>
      <nav aria-label="Main navigation">
        <button className={page === 'devices' ? 'nav-item active' : 'nav-item'} onClick={() => setPage('devices')}><Icon name="keyboard" />Keyboards<span>{data.devices.length}</span></button>
        <button className={page === 'projects' ? 'nav-item active' : 'nav-item'} onClick={() => setPage('projects')}><Icon name="projects" />Projects<span>{data.projects.length}</span></button>
        <button className={page === 'integrations' ? 'nav-item active' : 'nav-item'} onClick={() => setPage('integrations')}><Icon name="plug" />Integrations<span>{integration?.installed ? '1' : '0'}</span></button>
      </nav>
      <div className="sidebar-note"><div className="note-mark">↗</div><strong>Keep the flow.</strong><p>Your agents work.<br />You choose when to listen.</p></div>
      <div className="connection"><span className={`status-dot ${running ? 'on' : ''}`} /><div>{backendLabel}<small>macOS preview · 0.1</small></div></div>
    </aside>
    <main>
      <div className="titlebar"><span>CONTROL PANEL</span><div className="titlebar-right"><span className="local-dot" /> All local</div></div>
      <header className="page-heading"><div><div className="eyebrow">{page === 'devices' ? 'MAKE IT YOURS' : page === 'projects' ? 'MAKE ROOM TO FOCUS' : 'CONNECT YOUR AGENTS'}</div><h1>{page === 'devices' ? 'Your keyboard. Your agents.' : page === 'projects' ? 'Choose what gets through.' : 'Bring Codex to your keyboard.'}</h1><p>{page === 'devices' ? 'Pick a keyboard. We’ll remember it for next time.' : page === 'projects' ? 'Enable projects and choose which ones can notify you.' : 'Install Abralia’s skill, tools and event hooks together.'}</p></div><button className={`button secondary refresh ${busy === 'scan' ? 'spinning' : ''}`} disabled={!!busy} onClick={async () => { setBusy('scan'); await refresh(true); setBusy(null); }}><Icon name="refresh" size={16} />Refresh</button></header>
      {(error || feedback || problems.length > 0 || needsRestart) && <div className="notices" aria-live="polite">
        {error && <div className="notice error"><Icon name="info" size={18} />{error}</div>}
        {feedback && <div className="notice"><Icon name="check" size={18} />{feedback}</div>}
        {needsRestart && <div className="notice warning"><Icon name="info" size={18} /><span>Your terminal backend needs one restart to enable device and project controls.</span></div>}
        {problems.slice(0, 2).map((message, i) => <div className="notice warning" key={i}><Icon name="info" size={18} />{message}</div>)}
      </div>}
      <section className="backend-bar" aria-label="Backend control" aria-live="polite"><div><strong>{backendLabel}</strong><p>{running ? backend?.owned ? 'Keeps running when the window is hidden. Quit releases your keyboard.' : 'Connected to your existing service.' : loading ? 'Preparing your saved keyboard and project.' : !selected ? 'Choose a keyboard below to get started.' : chooseProject ? 'Choose a project below to start its backend.' : 'Abralia starts and manages the backend for you.'}</p></div>
        {backend?.owned ? <button className="button secondary" disabled={!!busy} onClick={() => void backendAction('stop')}>Stop backend</button>
          : !running && !loading && <button className="button primary" disabled={!!busy || !selected?.connected} onClick={() => chooseProject ? setPage('projects') : void backendAction('start')}>{busy === 'start' ? 'Starting…' : chooseProject ? 'Choose project' : 'Start backend'}</button>}
      </section>
      {page === 'devices' ? <>
        <section className="hero-card"><div className="hero-copy"><span className="pill">{selected ? selected.connected ? 'REMEMBERED DEVICE' : 'DEVICE DISCONNECTED' : 'READY WHEN YOU ARE'}</span><h2>{selected ? selected.name : 'Give your keyboard\nan Agent Mode.'}</h2><p>{selected ? selected.connected ? 'Your choice is saved on this Mac. Project notifications stay under your control.' : 'Your saved keyboard isn’t connected. Plug it back in or choose another below.' : 'Supported keyboards appear below. Choose one and Abralia starts the backend for you.'}</p><div className="hero-detail"><Icon name="plug" size={16} />{selected?.connected ? 'USB connection detected' : selected ? 'Waiting for this keyboard' : 'Built for your everyday keyboard'}</div></div><KeyboardArt selected={!!selected?.connected} /></section>
        <section aria-labelledby="devices-heading"><div className="section-heading"><h2 id="devices-heading">Available keyboards <span>{data.devices.length}</span></h2><span className="section-caption">Connected devices with an Abralia profile</span></div>
          {loading ? <div className="empty"><div className="loader" />Looking for your keyboard…</div> : data.devices.length === 0 ? <div className="empty"><Icon name="keyboard" size={34} /><h3>No supported keyboard detected</h3><p>Connect a supported Keychron V3 keyboard, then refresh.</p></div> : <div className="device-list">{data.devices.map(device => <article className={`device-row ${selected?.id === device.id ? 'chosen' : ''}`} key={device.id}>
            <div className="device-icon"><Icon name="keyboard" size={28} /></div><div className="device-description"><h3>{device.name}{device.experimental && <span className="experimental">Experimental profile</span>}</h3><p>{device.profile_name || 'Abralia keyboard profile'}</p><div className="device-meta"><span className="tiny-dot" /> USB connected{device.serial && <span> · {device.serial}</span>}</div></div>
            <button className={`button ${selected?.id === device.id ? 'selected-button' : 'primary'}`} disabled={!!busy || !device.selectable} onClick={() => void select(device)} aria-label={`Select ${device.name}`} title={device.selection_reason || ''}>{busy === device.id ? 'Selecting…' : selected?.id === device.id ? <><Icon name="check" size={16} />Selected</> : <>Use keyboard<Icon name="arrow" size={16} /></>}</button>
          </article>)}</div>}
          <p className="fine-print">Detection identifies the device profile. The backend checks firmware before taking control.</p>
        </section>
        <button className="project-shortcut" onClick={() => setPage('projects')}><div className="shortcut-icon"><Icon name="projects" /></div><div><strong>{connected.length ? `${connected.length} project${connected.length > 1 ? 's' : ''} connected` : data.projects.length ? 'Your saved projects' : 'Your projects will appear here'}</strong><span>{connected.length ? `${muted ? `${muted} muted · ` : ''}Manage which projects can notify you` : 'View projects and their connection status'}</span></div><Icon name="arrow" /></button>
      </> : page === 'projects' ? <>
        <div className="project-stats"><div><span>CONNECTED PROJECTS</span><strong>{connected.length}</strong></div><div><span>ACTIVE TASK SLOTS</span><strong>{connected.reduce((n, p) => n + p.task_count, 0)}</strong></div><div><span>PROJECTS MUTED</span><strong>{muted}</strong></div></div>
        <section><div className="section-heading"><h2>Projects using Abralia</h2><button className="button primary" disabled={!!busy || !data.shared} onClick={() => void addProject()}>Add project</button></div>
          {loading ? <div className="empty"><div className="loader" />Finding your projects…</div> : data.projects.length === 0 ? <div className="empty"><Icon name="projects" size={34} /><h3>No projects added yet</h3><p>{running ? 'Your keyboard backend is running. Enabled projects will appear here when they connect.' : 'Choose your keyboard to start Abralia.'}</p><small>Use Add project to enable a folder, then connect Codex from Integrations.</small></div> : <div className="project-list">{data.projects.map(project => <article className={`project-row ${project.muted ? 'muted' : ''}`} key={project.id}><div className="folder-icon"><Icon name="projects" size={24} /></div><div className="project-description"><h3>{project.name}</h3><p title={project.path}>{project.path}</p><div className="project-meta">{project.connected ? <><span>{project.task_count} task{project.task_count === 1 ? '' : 's'}</span><span>·</span><span>{project.connected_count} connected</span></> : <span>Backend stopped</span>}{project.device_error && <span className="project-error">Keyboard needs attention</span>}</div></div>{project.connected && !backend?.project_choices?.some(p => p.id === project.id) ? <div className="mute-control"><span><Icon name={project.muted ? 'quiet' : 'bell'} size={16} />{project.restart_required ? 'Restart required' : project.muted ? 'Muted' : 'Notifications on'}</span><button role="switch" aria-checked={!project.muted} aria-label={`Notifications for ${project.name}`} className="switch" disabled={!!busy || !project.can_mute} onClick={() => void mute(project)}><i /></button></div> : <button className="button secondary" disabled={!!busy || running || !selected?.connected} onClick={() => void backendAction('start', project)} aria-label={`Start backend for ${project.name}`}>{project.connected ? 'Use this project' : 'Start backend'}</button>}{project.shared && <button className="button text-button" aria-label={`Remove ${project.name}`} disabled={!!busy} onClick={() => void removeProject(project)}>Remove</button>}</article>)}</div>}
        </section>
        <div className="project-explainer"><Icon name="info" size={20} /><p><strong>Mute the interruptions, keep the work.</strong> Project mute quiets incoming calls and fog reminders. Your agents stay registered and keep working. Turn notifications back on whenever you’re ready.</p></div>
      </> : <>
        <section className="integration-card" aria-label="Codex integration">
          <div className="integration-heading"><div className="integration-icon"><Icon name="plug" size={30} /></div><div><h2>Codex</h2><p>Abralia skill, agent tools and lifecycle hooks</p></div><span className="pill">{integration?.installed ? 'INSTALLED' : 'NOT CONNECTED'}</span></div>
          <p className="integration-description">Connect your Codex tasks to one shared keyboard. Enable the folders you want in Projects; other projects stay outside Abralia.</p>
          <div className="integration-facts"><div><span>Plugin</span><strong>{integration?.installed ? `Abralia ${integration.version || ''}` : 'Ready to install'}</strong></div><div><span>Bundled runtime</span><strong>{integration?.runtime_available ? 'Available' : integration?.installed ? 'Needs repair' : 'Included with setup'}</strong></div><div><span>Hook activity</span><strong>{connected.some(p => (p.hooks_received || 0) > 0) ? 'Events received' : 'No events observed yet'}</strong></div></div>
          {integration?.error && <div className="notice error">{integration.error}</div>}
          <div className="integration-actions"><button className="button primary" disabled={!!busy} onClick={() => void integrationAction('install')}>{busy === 'install-integration' ? 'Installing Abralia…' : integration?.installed ? 'Repair / update plugin' : 'Connect Codex'}</button>{integration?.installed && <button className="button secondary" disabled={!!busy} onClick={() => void integrationAction('remove')}>{busy === 'remove-integration' ? 'Removing…' : 'Remove plugin'}</button>}</div>
          <div className="manual-config"><button className="button secondary" disabled={!!busy} onClick={async () => { try { await api?.copyHooks(); setFeedback('Manual hook configuration copied. Use it as an alternative to plugin hooks.'); } catch (e) { setError(e instanceof Error ? e.message : 'Could not copy configuration.'); } }}>Copy manual hook TOML</button><p>For manual setup instead of plugin hooks. Enable only one route to avoid duplicate events.</p></div>
        </section>
        <div className="setup-steps"><div><b>1</b><p><strong>Install the Abralia plugin</strong>The app registers its bundled skill, tools and named hooks with Codex.</p></div><div><b>2</b><p><strong>Review hooks in Codex</strong>Open Codex’s hook settings and trust the Abralia entries. Installing a plugin does not grant hook trust.</p></div><div><b>3</b><p><strong>Enable project folders</strong>Add a folder here or ask Codex “Enable Abralia for this task.” Each agent activates only after you ask.</p></div></div>
      </>}
      <footer><span>Your keyboard choice and mute settings stay on this Mac.</span><span>{updated ? `Updated ${updated}` : 'Connecting…'}</span></footer>
    </main>
  </div>;
}

createRoot(document.getElementById('root')!).render(<App />);
