// IIFE: all state is module-scoped. Window-exported API (for HTML onclick):
//   doLogin, doLogout, switchTab, switchWorkspace, switchSettingsTab,
//   addRoom, deleteRoom, openRoomModal, closeRoomModal, saveRoomSettings,
//   openAddDevice, closeAddDevice, submitDevice, toggleAddPort,
//   openEditDevice, closeEditModal, saveDeviceEdit, toggleEditPort,
//   deleteDevice, renameWs, closeWsModal, saveWsSettings, deleteWs, popoutWs,
//   triggerPoll, pollRoom, exportConfig, importConfig,
//   showPrivacy, closePrivacy, closeConfirm,
//   resetRoomSize, changePin, enablePin, disablePin
(function() {
'use strict';

// ═══════════════════════════════════════════════════════════════════════
//  STATE
// ═══════════════════════════════════════════════════════════════════════
let _workspaces = [];       // All workspaces with their rooms
let _activeWsId = null;     // Currently selected workspace id
let _rooms      = [];       // Shortcut: rooms of the active workspace
let _cols       = 3;
let _curTab     = 'dash';
let _staggerDash = true;
let _staggerSettings = true;
let _refreshId  = null;
const REFRESH_MS = 8000;
const MODAL_FOCUS_DELAY = 50;
const POLL_RESULT_DELAY = 2500;
const LAYOUT_SAVE_DEBOUNCE = 400;
const RESIZE_GUARD_DELAY = 1000;
let _prevStatuses = {};

// If the URL is /w/<workspace-id>, lock to that workspace (standalone window mode)
let _lockedWsId = null;
(function detectLockedWs() {
  const m = window.location.pathname.match(/^\/w\/([^/]+)\/?$/);
  if (m) {
    _lockedWsId = m[1];
    _activeWsId = m[1];
  }
})();

// ═══════════════════════════════════════════════════════════════════════
//  UTILITIES
//  Small helpers used everywhere: HTML escaping, safe DOM accessors,
//  toast notifications, and human-friendly time formatting.
// ═══════════════════════════════════════════════════════════════════════
/** Escape a value for safe insertion into HTML (prevents XSS via room/device names). */
function esc(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;')
    .replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');
}

// Safe getElementById — returns null without crashing if element missing
function $id(id) { return document.getElementById(id); }
// Safe set value — no-op if element not found
function $val(id, v) { const e=$id(id); if(e) e.value=v; return e; }
// Safe classList operation — no-op if element not found
function $cls(id, op, cls) { const e=$id(id); if(e) e.classList[op](cls); return e; }

/** Show a transient toast in the corner. `type` is 'info' | 'success' | 'error'. */
function notify(msg, type='info', ms=3500) {
  const el = document.createElement('div');
  el.className = 'notif' + (type !== 'info' ? ' ' + type : '');
  el.textContent = msg;
  document.getElementById('notif').appendChild(el);
  setTimeout(() => {
    el.classList.add('dismissing');
    el.addEventListener('animationend', () => el.remove(), { once: true });
    setTimeout(() => el.remove(), 200);
  }, ms);
}

let _confirmResolve = null;
function vigilConfirm(msg, title) {
  return new Promise(resolve => {
    _confirmResolve = resolve;
    const el = $id('confirm-msg'); if (el) el.textContent = msg;
    const t = $id('confirm-title'); if (t) t.textContent = title || 'Confirm';
    $cls('confirm-modal', 'add', 'open');
  });
}
function closeConfirm(result) {
  $cls('confirm-modal', 'remove', 'open');
  if (_confirmResolve) { _confirmResolve(result); _confirmResolve = null; }
}

function timeAgo(iso) {
  if (!iso) return '—';
  const s = Math.max(0, Math.floor((Date.now() - new Date(iso).getTime()) / 1000));
  if (s < 5)     return 'just now';
  if (s < 60)    return s + 's ago';
  if (s < 3600)  return Math.floor(s/60) + 'm ago';
  if (s < 86400) return Math.floor(s/3600) + 'h ago';
  return Math.floor(s/86400) + 'd ago';
}

// ═══════════════════════════════════════════════════════════════════════
//  BROWSER NOTIFICATIONS
//  Fires a desktop notification when a device transitions to offline.
//  Permission is requested lazily on the first status change detection.
// ═══════════════════════════════════════════════════════════════════════
function requestNotifPermission() {
  if ('Notification' in window && Notification.permission === 'default') {
    Notification.requestPermission();
  }
}

function checkStatusChanges(rooms) {
  const newStatuses = {};
  const wentOffline = [];
  const cameOnline  = [];

  rooms.forEach(r => (r.devices || []).forEach(d => {
    const key = r.id + '/' + d.id;
    newStatuses[key] = { status: d.status, name: d.name, room: r.name };
    const prev = _prevStatuses[key];
    if (!prev) return;
    if (prev.status === 'online' && d.status === 'offline') {
      wentOffline.push(d.name + ' (' + r.name + ')');
    } else if (prev.status === 'offline' && d.status === 'online') {
      cameOnline.push(d.name + ' (' + r.name + ')');
    }
  }));

  _prevStatuses = newStatuses;

  if (wentOffline.length && 'Notification' in window && Notification.permission === 'granted') {
    new Notification('Vigil — Device Offline', {
      body: wentOffline.join(', '),
      icon: '/static/vigil-icon.png',
      tag: 'vigil-offline-' + Date.now()
    });
  }
  if (cameOnline.length && 'Notification' in window && Notification.permission === 'granted') {
    new Notification('Vigil — Device Online', {
      body: cameOnline.join(', '),
      icon: '/static/vigil-icon.png',
      tag: 'vigil-online-' + Date.now()
    });
  }
}

// ═══════════════════════════════════════════════════════════════════════
//  API
//  Thin fetch wrapper used by every endpoint call. Auto-parses JSON,
//  surfaces server `{error: "..."}` messages, and turns network failures
//  into a friendly "Cannot reach server" message instead of a TypeError.
// ═══════════════════════════════════════════════════════════════════════
/** Call a Vigil REST endpoint and return the parsed JSON. Throws on non-2xx. */
async function api(method, path, body) {
  const opts = { method, headers: {'Content-Type':'application/json'}, credentials:'same-origin' };
  if (body !== undefined) opts.body = JSON.stringify(body);
  let r;
  try {
    r = await fetch(path, opts);
  } catch(e) {
    throw new Error('Cannot reach server. Is vigil.py running?');
  }
  const data = await r.json().catch(() => ({}));

  if (r.status === 401 && !path.startsWith('/api/auth/')) {
    showLogin();
    throw new Error('Session expired — please log in.');
  }
  if (!r.ok) throw new Error(data.error || 'HTTP ' + r.status);
  return data;
}

// ═══════════════════════════════════════════════════════════════════════
//  CLOCK
//  Drives the date+time readout in the header. Ticks once a second so the
//  seconds field is always live. Cheap — no DOM thrash if no clock element.
// ═══════════════════════════════════════════════════════════════════════
/** Update both clock elements (dashboard + settings header) with the current local time. */
function tickClock() {
  const t = new Date().toLocaleDateString('en-US',{weekday:'short',month:'short',day:'numeric'})
            + ' · '
            + new Date().toLocaleTimeString('en-US',{hour12:false});
  ['clock','clock2'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.textContent = t;
  });
}
setInterval(tickClock, 1000);
tickClock();

// ═══════════════════════════════════════════════════════════════════════
//  SCREEN NAVIGATION
//  Show/hide the Dashboard and Settings screens; controls auto-refresh
//  cadence (only the dashboard refreshes, settings stays static).
// ═══════════════════════════════════════════════════════════════════════
/** Activate the Dashboard screen, load workspace data, and start auto-refresh. */
function showDashboard() {
  document.querySelectorAll('.screen').forEach(s => s.classList.remove('active'));
  $cls('screen-dash', 'add', 'active');
  _curTab = 'dash';
  setCols(3);
  requestNotifPermission();
  loadWorkspaces().then(() => { renderDash(); renderSettings(); renderDeletedRooms(); });
  startRefresh();
}

/** Swap between the Dashboard and Settings screens. Starts/stops the
 *  dashboard auto-refresh accordingly. */
function switchTab(name) {
  _curTab = name;
  _staggerDash = true;
  _staggerSettings = true;
  if (name === 'dash') {
    document.querySelectorAll('.screen').forEach(s => s.classList.remove('active'));
    $cls('screen-dash', 'add', 'active');
    startRefresh();
    loadWorkspaces().then(() => renderDash());
  } else {
    stopRefresh();
    document.querySelectorAll('.screen').forEach(s => s.classList.remove('active'));
    $cls('screen-settings', 'add', 'active');
    loadWorkspaces().then(() => renderSettings());
  }
}

function switchSettingsTab(name) {
  const tabs = { rooms: 'stab-content-rooms', security: 'stab-content-security', config: 'stab-content-config' };
  for (const [key, id] of Object.entries(tabs)) {
    const el = $id(id); if (el) el.style.display = key === name ? 'block' : 'none';
    const btn = $id('stab-' + key); if (btn) btn.classList.toggle('active', key === name);
  }
  if (name === 'security') renderSecurityTab();
  if (name === 'config') renderConfigTab();
}

// ═══════════════════════════════════════════════════════════════════════
//  AUTO REFRESH (dashboard only)
//  Polls the server every REFRESH_MS (8s) and re-renders the dashboard.
//  Stopped when the user is on the Settings tab to avoid clobbering edits.
// ═══════════════════════════════════════════════════════════════════════
/** Begin the dashboard refresh loop. Idempotent — clears any prior timer first. */
function startRefresh() {
  stopRefresh();
  _refreshId = setInterval(() => {
    if (_curTab === 'dash') loadRooms().then(renderDash);
  }, REFRESH_MS);
}

/** Cancel the dashboard refresh loop (called when switching to Settings). */
function stopRefresh() {
  if (_refreshId) { clearInterval(_refreshId); _refreshId = null; }
}

// ═══════════════════════════════════════════════════════════════════════
//  DATA
//  Fetches workspaces + their live device status from the server and feeds
//  the renderers. Also computes the footer summary (online/offline counts,
//  avg latency, last-poll timestamp).
// ═══════════════════════════════════════════════════════════════════════
/** Pull /api/workspaces, pick an active workspace (locked → current → first),
 *  and update the workspace tab bars + footer. Returns the workspaces array. */
async function loadWorkspaces() {
  try {
    _workspaces = await api('GET', '/api/workspaces');
    // Pick the active workspace
    if (!_workspaces.length) {
      _activeWsId = null;
      _rooms = [];
    } else {
      // Prefer locked (URL-pinned) workspace, then current, then first
      const hasLocked  = _lockedWsId && _workspaces.find(w => w.id === _lockedWsId);
      const hasCurrent = _activeWsId && _workspaces.find(w => w.id === _activeWsId);
      const active = hasLocked ? _workspaces.find(w => w.id === _lockedWsId)
                   : hasCurrent ? _workspaces.find(w => w.id === _activeWsId)
                   : _workspaces[0];
      _activeWsId = active.id;
      _rooms = active.rooms || [];
    }
    updateWsButtons();
    updateWsSettingsName();
    updateFooter();
    const allRooms = _workspaces.flatMap(w => w.rooms || []);
    checkStatusChanges(allRooms);
  } catch(e) {
    notify('Load error: ' + e.message, 'error');
  }
  return _workspaces;
}

/** Alias kept for back-compat with code that pre-dates workspaces. */
async function loadRooms() { return loadWorkspaces(); }

/** Recompute footer counts (online / offline / pending), avg latency, and
 *  last-poll timestamp from the current _rooms array, then paint them. */
function updateFooter() {
  let total=0, online=0, offline=0, unknown=0;
  let latSum=0, latCount=0;
  _rooms.forEach(r => (r.devices||[]).forEach(d => {
    total++;
    if (d.status==='online') online++;
    else if (d.status==='offline') offline++;
    else unknown++;
    const lat = d.avg_latency_ms != null ? d.avg_latency_ms : d.latency_ms;
    if (d.status === 'online' && typeof lat === 'number') { latSum += lat; latCount++; }
  }));
  const last = _rooms.flatMap(r=>r.devices||[]).map(d=>d.last_checked).filter(Boolean).sort().pop();

  ['online-count','offline-count'].forEach((id, i) => {
    const el = document.getElementById(id);
    if (el) el.textContent = i===0 ? online : offline;
  });
  const avgEl = document.getElementById('avg-latency');
  if (avgEl) {
    if (latCount === 0) avgEl.textContent = '—';
    else {
      const avg = latSum / latCount;
      avgEl.textContent = avg < 1 ? '<1ms' : Math.round(avg) + 'ms';
    }
  }
  const ps = document.getElementById('poll-status');
  if (ps) ps.textContent = last ? 'last polled ' + timeAgo(last) : 'not yet polled';

  ['foot-count','foot-count2'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.textContent = total + ' device' + (total!==1?'s':'') + (unknown ? ' · ' + unknown + ' pending' : '');
  });
}

// ═══════════════════════════════════════════════════════════════════════
//  SAVE INDICATOR
//  Tiny dot + label in the footer that flashes "unsaved" while a write is
//  in flight and switches to "saved <time>" when the server confirms.
// ═══════════════════════════════════════════════════════════════════════
/** Flip the save dot to "saved" with the current time. */
function markSaved() {
  ['save-dot','save-dot2'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.classList.remove('unsaved');
  });
  ['save-label','save-label2'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.textContent = 'saved ' + new Date().toLocaleTimeString('en-US',{hour12:false});
  });
}
/** Flip the save dot to "unsaved" — called the instant an edit is initiated. */
function markUnsaved() {
  ['save-dot','save-dot2'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.classList.add('unsaved');
  });
  ['save-label','save-label2'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.textContent = 'unsaved';
  });
}

// ═══════════════════════════════════════════════════════════════════════
//  WORKSPACE ACTIONS
//  Rename and pop-out for the fixed output workspaces. Creation and
//  deletion are disabled (workspaces are always present).
// ═══════════════════════════════════════════════════════════════════════
/** Switch to a workspace by ID (used internally and from settings rename). */
function switchWs(wsId) {
  if (_lockedWsId) return;
  const idx = _workspaces.findIndex(w => w.id === wsId);
  if (idx >= 0) switchWorkspace(idx);
}


async function addWorkspace() {
  notify('All workspaces already exist.', 'error');
}

/** Open the workspace-rename modal pre-filled with the current name. */
function renameWs(wsId) {
  const ws = _workspaces.find(w => w.id === wsId);
  if (!ws) return;
  $val('ws-edit-id',   wsId);
  $val('ws-edit-name', ws.name);
  $cls('ws-modal', 'add', 'open');
  setTimeout(() => { const e = $id('ws-edit-name'); if (e) { e.focus(); e.select(); } }, MODAL_FOCUS_DELAY);
}

/** Close the rename-workspace modal without saving. */
function closeWsModal() {
  $cls('ws-modal', 'remove', 'open');
}

/** Submit the rename modal: validate, PUT to /api/workspaces/<id>, re-render. */
async function saveWsSettings() {
  const id   = $id('ws-edit-id').value;
  const name = $id('ws-edit-name').value.trim();
  if (!name) { notify('Workspace name is required.', 'error'); return; }
  try {
    await api('PUT', '/api/workspaces/' + id, { name });
    closeWsModal();
    notify('Workspace updated.', 'success');
    await loadWorkspaces();
    renderDash();
    renderSettings();
  } catch(e) {
    notify(e.message, 'error');
  }
}

/** Workspaces are fixed — deletion is disabled. */
async function deleteWs(wsId, name) {
  notify('Output workspaces cannot be deleted.', 'error');
}

/** Open a workspace in its own browser window at /w/<id>. The receiving page
 *  detects this URL pattern at boot and locks itself to that workspace
 *  (hides the tab bar, ignores switch attempts). Designed for multi-monitor. */
function popoutWs(wsId) {
  const url = window.location.origin + '/w/' + wsId;
  window.open(url, '_blank', 'width=1400,height=900');
}

// ═══════════════════════════════════════════════════════════════════════
//  DASHBOARD RENDER
//  Paints the live monitoring grid. Each room is a card; cards are drag-
//  reorderable, resizable, and store per-room size in localStorage.
// ═══════════════════════════════════════════════════════════════════════
async function switchWorkspace(idx) {
  if (_lockedWsId) return;
  if (idx < 0 || idx >= _workspaces.length) return;
  const ws = _workspaces[idx];
  if (!ws || ws.id === _activeWsId) return;
  _activeWsId = ws.id;
  updateWsButtons();
  _lastDashFP = '';
  await loadWorkspaces();
  renderDash();
  renderSettings();
}
const switchOutput = switchWorkspace;

function updateWsButtons() {
  const strip = document.getElementById('ws-strip');
  if (!strip) return;
  const activeIdx = _workspaces.findIndex(w => w.id === _activeWsId);
  strip.innerHTML = '';
  _workspaces.forEach((ws, i) => {
    const btn = document.createElement('button');
    btn.className = 'col-btn' + (i === activeIdx ? ' active' : '');
    btn.textContent = ws.name;
    btn.onclick = () => switchWorkspace(i);
    strip.appendChild(btn);
  });
}

/** Update the workspace name shown in settings. */
function updateWsSettingsName() {
  const el = document.getElementById('ws-settings-name');
  if (!el) return;
  const ws = _workspaces.find(w => w.id === _activeWsId);
  el.textContent = ws ? ws.name : '—';
}

/** Set the dashboard column count and persist via CSS var. Fixed at 3 on boot. */
function setCols(n) {
  _cols = n;
  const g = document.getElementById('dash-grid');
  if (g) g.style.setProperty('--cols', n);
}

/** Format an ISO timestamp as "4d 12h", "3h 42m", "17m", or "—". */
function fmtUptime(iso) {
  if (!iso) return '—';
  const then  = new Date(iso).getTime();
  if (isNaN(then)) return '—';
  const diff  = Math.max(0, Date.now() - then);
  const secs  = Math.floor(diff / 1000);
  const mins  = Math.floor(secs / 60);
  const hours = Math.floor(mins / 60);
  const days  = Math.floor(hours / 24);
  if (days  >= 1) return `${days}d ${hours % 24}h`;
  if (hours >= 1) return `${hours}h ${mins % 60}m`;
  if (mins  >= 1) return `${mins}m`;
  return `${secs}s`;
}

/** Format latency in ms, preferring the rolling average when available. */
function fmtLatency(d) {
  const v = d.avg_latency_ms != null ? d.avg_latency_ms : d.latency_ms;
  if (v == null) return '—';
  return v < 1 ? '<1 ms' : `${Math.round(v)} ms`;
}

function _pollTooltip(ps) {
  if (!ps) return '';
  const parts = [];
  if (ps.last_polled_secs_ago != null) parts.push('polled ' + ps.last_polled_secs_ago + 's ago');
  if (ps.next_due_secs != null) parts.push('next in ' + ps.next_due_secs + 's');
  if (ps.polling) parts.push('polling now');
  return parts.join(' · ');
}

function _dashFingerprint() {
  return _rooms.map(r => r.id + ':' + (r.devices||[]).map(d=>d.id).join(',')).join('|');
}
let _lastDashFP = '';

function renderDash() {
  const grid = $id('dash-grid');
  if (!grid) return;
  if (!_rooms.length) {
    _lastDashFP = '';
    if (_roomResizeObs) { _roomResizeObs.disconnect(); _roomResizeObs = null; }
    grid.innerHTML = '<div class="empty-state"><svg class="empty-state-icon" width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><line x1="17.5" y1="14" x2="17.5" y2="21"/><line x1="14" y1="17.5" x2="21" y2="17.5"/></svg>No rooms in this workspace — go to Settings to add one.</div>';
    return;
  }

  const fp = _dashFingerprint();
  if (fp === _lastDashFP && grid.querySelector('.room-card')) {
    _patchDash(grid);
    return;
  }
  _lastDashFP = fp;

  if (_roomResizeObs) { _roomResizeObs.disconnect(); _roomResizeObs = null; }
  grid.innerHTML = _rooms.map(room => {
    const devs = room.devices || [];
    const ledRail = devs.length
      ? `<div class="led-rail">
          <div class="led-rail-hd"></div>
          ${devs.map(d => {
            const s = d.status || 'pending';
            return `<div class="led-slot"><span class="status-light ${s}"></span></div>`;
          }).join('')}
        </div>`
      : '';
    const tableHtml = devs.length
      ? `<div class="table-area"><table class="dev-table">
          <thead><tr>
            <th class="col-name">Device<span class="col-resize-handle" data-col="name"></span></th>
            <th class="col-status">Status<span class="col-resize-handle" data-col="status"></span></th>
            <th class="col-ip">IP<span class="col-resize-handle" data-col="ip"></span></th>
            <th class="col-uptime">Uptime<span class="col-resize-handle" data-col="uptime"></span></th>
            <th class="col-latency">Latency<span class="col-resize-handle" data-col="latency"></span></th>
          </tr></thead>
          <tbody>
            ${devs.map(d => {
              const s = d.status || 'pending';
              return `<tr data-dev-id="${d.id}">
                <td class="td-name" title="${esc(d.name)}">${esc(d.name)}</td>
                <td class="td-status ${s}">${s}</td>
                <td class="td-ip">${esc(d.ip || '')}</td>
                <td class="td-uptime">${s === 'online' ? fmtUptime(d.online_since) : '—'}</td>
                <td class="td-latency">${s === 'online' ? esc(fmtLatency(d)) : '—'}</td>
              </tr>`;
            }).join('')}
          </tbody>
        </table></div>`
      : '<div class="table-area"><div class="no-devices">No devices — add some in Settings.</div></div>';
    const saved = _roomSize(room.id);
    const sizeStyle = (saved && saved.w > 0 && saved.h > 0) ? `width:${saved.w}px;height:${saved.h}px` : '';
    const roomStatus = _roomStatus(devs);
    const resetBtn = saved ? `<button class="room-reset-btn" onclick="resetRoomSize(event,'${room.id}')" title="Reset size">↺</button>` : '';
    const ps = room.poll_status;
    const pollCls = ps && ps.polling ? 'room-poll active' : 'room-poll';
    const pollTip = _pollTooltip(ps);
    return `<div class="room-card" data-room-id="${room.id}" data-status="${roomStatus}" style="${sizeStyle}" ondblclick="resetRoomSize(event,'${room.id}')">
      <div class="room-hd" draggable="true" title="Drag to reorder">
        <span class="room-name-label">${esc(room.name)}</span>
        <span class="room-count" title="${pollTip}">${devs.length} device${devs.length !== 1 ? 's' : ''}</span>
        <span class="${pollCls}">⟳</span>
        <button class="room-poll-btn" onclick="pollRoom(event,'${room.id}')" title="Poll this room now">▶</button>
        ${resetBtn}
      </div>
      <div class="room-body">
        ${ledRail}
        ${tableHtml}
      </div>
    </div>`;
  }).join('');
  grid.style.setProperty('--cols', _cols);
  _observeRoomResize(grid);
  _initRoomDragOnce(grid);
  if (_staggerDash) {
    _staggerDash = false;
    grid.querySelectorAll('.room-card').forEach((c, i) => {
      c.style.setProperty('--i', i);
      c.classList.add('enter');
    });
  }
}

function _patchDash(grid) {
  _rooms.forEach(room => {
    const card = grid.querySelector(`.room-card[data-room-id="${room.id}"]`);
    if (!card) return;
    const devs = room.devices || [];
    const roomStatus = _roomStatus(devs);
    card.setAttribute('data-status', roomStatus);
    const nameEl = card.querySelector('.room-name-label');
    if (nameEl && nameEl.textContent !== room.name) nameEl.textContent = room.name;
    const countEl = card.querySelector('.room-count');
    const countStr = devs.length + ' device' + (devs.length !== 1 ? 's' : '');
    if (countEl && countEl.textContent !== countStr) countEl.textContent = countStr;
    if (countEl) countEl.title = _pollTooltip(room.poll_status);
    const pollEl = card.querySelector('.room-poll');
    if (pollEl) {
      const isPolling = room.poll_status && room.poll_status.polling;
      pollEl.classList.toggle('active', !!isPolling);
    }
    const leds = card.querySelectorAll('.led-slot .status-light');
    devs.forEach((d, i) => {
      const s = d.status || 'pending';
      if (leds[i]) leds[i].className = 'status-light ' + s;
      const row = card.querySelector(`tr[data-dev-id="${d.id}"]`);
      if (!row) return;
      const tdStatus = row.querySelector('.td-status');
      if (tdStatus) { tdStatus.className = 'td-status ' + s; tdStatus.textContent = s; }
      const tdUptime = row.querySelector('.td-uptime');
      if (tdUptime) tdUptime.textContent = s === 'online' ? fmtUptime(d.online_since) : '—';
      const tdLatency = row.querySelector('.td-latency');
      if (tdLatency) tdLatency.textContent = s === 'online' ? fmtLatency(d) : '—';
    });
  });
}

// ── Drag-and-drop room reordering ─────────────────────────────────────
//   Wires up HTML5 drag events on the dashboard grid so users can drag a
//   room's title bar to reorder cards. On drop, the new order is PUT to
//   /api/workspaces/<id>/rooms/order so config.json is updated server-side.
let _draggedRoomId = null;
/** Attach drag/drop listeners to the grid once per page lifetime. Safe to
 *  call on every renderDash — re-init is short-circuited by a flag. */
function _initRoomDragOnce(grid) {
  if (grid.__dragInit) return;
  grid.__dragInit = true;

  grid.addEventListener('dragstart', e => {
    const hd = e.target.closest('.room-hd');
    if (!hd) return;
    const card = hd.closest('.room-card');
    if (!card) return;
    _draggedRoomId = card.getAttribute('data-room-id');
    card.classList.add('dragging');
    try { e.dataTransfer.effectAllowed = 'move'; e.dataTransfer.setData('text/plain', _draggedRoomId); } catch(_){}
  });

  grid.addEventListener('dragend', () => {
    grid.querySelectorAll('.room-card.dragging').forEach(el => el.classList.remove('dragging'));
    grid.querySelectorAll('.room-card.drag-over').forEach(el => el.classList.remove('drag-over'));
    _draggedRoomId = null;
  });

  grid.addEventListener('dragover', e => {
    if (!_draggedRoomId) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = 'move';
    const target = e.target.closest('.room-card');
    if (!target || target.getAttribute('data-room-id') === _draggedRoomId) return;
    grid.querySelectorAll('.room-card.drag-over').forEach(el => { if (el !== target) el.classList.remove('drag-over'); });
    target.classList.add('drag-over');
    const rect = target.getBoundingClientRect();
    const after = (e.clientX - rect.left) > rect.width / 2;
    const draggedEl = grid.querySelector(`.room-card[data-room-id="${_draggedRoomId}"]`);
    if (!draggedEl || draggedEl === target) return;
    if (after) { if (target.nextSibling !== draggedEl) target.after(draggedEl); }
    else       { if (target.previousSibling !== draggedEl) target.before(draggedEl); }
  });

  grid.addEventListener('drop', async e => {
    if (!_draggedRoomId) return;
    e.preventDefault();
    const wsId = _activeWsId;
    const newOrder = Array.from(grid.querySelectorAll('.room-card[data-room-id]'))
      .map(el => el.getAttribute('data-room-id'));
    const map = Object.fromEntries(_rooms.map(r => [r.id, r]));
    _rooms = newOrder.map(id => map[id]).filter(Boolean);
    grid.querySelectorAll('.room-card.drag-over').forEach(el => el.classList.remove('drag-over'));
    try {
      await api('PUT', `/api/workspaces/${wsId}/rooms/order`, { order: newOrder });
    } catch (err) {
      console.error('Room reorder failed:', err);
      notify('Could not save new room order — refresh to see saved order.', 'error');
    }
  });
}

// ── room status rollup: worst-of devices, used for status strip + glow ─
//   pending wins over everything (room is "warming up"); otherwise
//   offline > degraded > online > unknown.
/** Reduce a room's device statuses into a single room-level status string. */
function _roomStatus(devs) {
  if (!devs || !devs.length) return 'unknown';
  let online = 0, offline = 0, pending = 0;
  for (const d of devs) {
    const s = d.status || 'pending';
    if (s === 'online') online++;
    else if (s === 'offline') offline++;
    else if (s === 'pending') pending++;
  }
  if (pending > 0) return 'pending';            // still warming up
  if (offline === devs.length) return 'offline';
  if (offline > 0) return 'degraded';
  if (online === devs.length) return 'online';
  return 'unknown';
}

// ── per-room dashboard resize: persist size to config.json via API ─────
//   Each room card is `resize:both`. A ResizeObserver below watches every
//   card and PUTs w/h to /api/rooms/<id>/layout so sizes survive restarts.
let _layoutSaveTimers = {};
let _layoutSaveQueue = Promise.resolve();
const _GRID_SNAP = 20;
function _snap(v) { return Math.round(v / _GRID_SNAP) * _GRID_SNAP; }
function _roomSize(id) {
  const room = _rooms.find(r => r.id === id);
  return room && room.layout ? room.layout : null;
}
function _saveRoomSize(id, w, h) {
  const sw = _snap(w), sh = _snap(h);
  const room = _rooms.find(r => r.id === id);
  if (room) room.layout = { w: sw, h: sh };
  clearTimeout(_layoutSaveTimers[id]);
  _layoutSaveTimers[id] = setTimeout(() => {
    _layoutSaveQueue = _layoutSaveQueue.then(() =>
      api('PUT', `/api/rooms/${id}/layout`, { w: sw, h: sh })
    ).catch(() => {});
  }, LAYOUT_SAVE_DEBOUNCE);
}
function _clearRoomSize(id) {
  const room = _rooms.find(r => r.id === id);
  if (room) delete room.layout;
  clearTimeout(_layoutSaveTimers[id]);
  _layoutSaveQueue = _layoutSaveQueue.then(() =>
    api('PUT', `/api/rooms/${id}/layout`, { w: null, h: null })
  ).catch(() => {});
}
let _roomResizeObs = null;
let _resizeActive = false;
function _observeRoomResize(grid) {
  if (_roomResizeObs) _roomResizeObs.disconnect();
  _resizeActive = false;
  setTimeout(() => { _resizeActive = true; }, RESIZE_GUARD_DELAY);
  _roomResizeObs = new ResizeObserver(entries => {
    if (!_resizeActive) return;
    for (const e of entries) {
      const el = e.target;
      const id = el.getAttribute('data-room-id');
      if (!id) continue;
      if (el.style.width && el.style.height) {
        const sw = _snap(e.contentRect.width);
        const sh = _snap(e.contentRect.height);
        if (sw <= 0 || sh <= 0) continue;
        el.style.width  = sw + 'px';
        el.style.height = sh + 'px';
        _saveRoomSize(id, sw, sh);
      }
    }
  });
  grid.querySelectorAll('.room-card[data-room-id]').forEach(el => _roomResizeObs.observe(el));
}
function resetRoomSize(evt, id) {
  if (evt.target.closest('.dev-table')) return;
  const card = evt.target.closest('.room-card');
  if (!card) return;
  card.style.width = '';
  card.style.height = '';
  _clearRoomSize(id);
}

// ── column resize: shared CSS vars across all room tables ─────────────
//   Column widths are global: dragging the "IP" handle in one room resizes
//   it in every room (so columns line up visually). Widths persist in
//   localStorage under _COL_KEY. _COL_MIN / _COL_MAX clamp the drag range.
const _COL_KEY = 'vigil.colWidths.v1';
const _COL_DEFAULTS = { name: null, ip: null, status: 54, uptime: 50, latency: 50 };
const _COL_MIN = { name: 60, ip: 40, status: 36, uptime: 36, latency: 36 };
const _COL_MAX = { name: 600, ip: 240, status: 160, uptime: 160, latency: 160 };

/** Load saved column widths from localStorage, merged onto defaults. */
function _loadColWidths() {
  try { return Object.assign({}, _COL_DEFAULTS, JSON.parse(localStorage.getItem(_COL_KEY) || '{}')); }
  catch (e) { return Object.assign({}, _COL_DEFAULTS); }
}
/** Persist a column-widths object to localStorage. */
function _saveColWidths(w) { localStorage.setItem(_COL_KEY, JSON.stringify(w)); }
/** Apply column widths by writing each one as a --col-<name>-w CSS variable
 *  on :root, where the table CSS picks it up. */
function _applyColWidths(w) {
  const root = document.documentElement.style;
  for (const c of Object.keys(_COL_DEFAULTS)) {
    const v = w[c];
    root.setProperty('--col-' + c + '-w', v == null ? 'auto' : (v + 'px'));
  }
}
_applyColWidths(_loadColWidths());

let _colDrag = null;
document.addEventListener('mousedown', e => {
  const h = e.target.closest('.col-resize-handle');
  if (!h) return;
  const col = h.getAttribute('data-col');
  const th = h.parentElement;
  e.preventDefault();
  _colDrag = { col, startX: e.clientX, startW: th.getBoundingClientRect().width, handle: h };
  h.classList.add('dragging');
  document.body.classList.add('resizing-col');
});
document.addEventListener('mousemove', e => {
  if (!_colDrag) return;
  const { col, startX, startW } = _colDrag;
  const min = _COL_MIN[col] || 40;
  const max = _COL_MAX[col] || 600;
  const w = Math.max(min, Math.min(max, Math.round(startW + (e.clientX - startX))));
  document.documentElement.style.setProperty('--col-' + col + '-w', w + 'px');
});
document.addEventListener('mouseup', () => {
  if (!_colDrag) return;
  const { col, handle } = _colDrag;
  handle.classList.remove('dragging');
  document.body.classList.remove('resizing-col');
  // read back the applied value and persist
  const applied = document.documentElement.style.getPropertyValue('--col-' + col + '-w');
  const m = applied.match(/(\d+)px/);
  if (m) {
    const all = _loadColWidths();
    all[col] = parseInt(m[1], 10);
    _saveColWidths(all);
  }
  _colDrag = null;
});
// double-click handle = reset that column
document.addEventListener('dblclick', e => {
  const h = e.target.closest('.col-resize-handle');
  if (!h) return;
  e.stopPropagation();
  const col = h.getAttribute('data-col');
  const all = _loadColWidths();
  all[col] = _COL_DEFAULTS[col];
  _saveColWidths(all);
  _applyColWidths(all);
});

// ═══════════════════════════════════════════════════════════════════════
//  SETTINGS RENDER
//  Builds the editable rooms+devices list. Every row has Edit/Remove
//  buttons; each room has an inline "+ Add Device" form that toggles open.
// ═══════════════════════════════════════════════════════════════════════
/** Re-render the Settings rooms list from _rooms. Idempotent rebuild. */
function renderSettings() {
  const container = document.getElementById('settings-rooms-list');
  if (!container) return;

  if (!_rooms.length) {
    container.innerHTML = '<div class="empty-state empty-state-compact"><svg class="empty-state-icon" width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><line x1="17.5" y1="14" x2="17.5" y2="21"/><line x1="14" y1="17.5" x2="21" y2="17.5"/></svg>No rooms yet — add one above.</div>';
    return;
  }

  container.innerHTML = _rooms.map(room => {
    const devs = room.devices || [];
    const interval = Math.round((room.interval||300)/60);

    const tableRows = devs.map(d => {
      const typeStr = d.check_type==='udp' ? 'UDP :' + (d.udp_port||9)
                    : d.check_type==='ssh' ? 'SSH :' + (d.ssh_port||22)
                    : d.check_type==='http' ? 'HTTP :' + (d.http_port||80)
                    : 'PING';
      return `<tr>
        <td class="td-name">${esc(d.name)}</td>
        <td class="td-ip">${esc(d.ip)}</td>
        <td class="td-type">${typeStr} · ${d.timeout||2}s</td>
        <td class="td-actions">
          <button class="btn sm" data-action="edit-device" data-room-id="${room.id}" data-device-id="${d.id}">Edit</button>
          <button class="btn sm danger" data-action="delete-device" data-room-id="${room.id}" data-device-id="${d.id}">✕</button>
        </td>
      </tr>`;
    }).join('');

    const table = devs.length
      ? `<table class="dev-table">
          <thead><tr><th>Device</th><th>IP</th><th>Check</th><th></th></tr></thead>
          <tbody>${tableRows}</tbody>
        </table>`
      : '<div class="no-devices">No devices yet — click + Add Device below.</div>';

    return `<div class="s-room-card" id="scard-${room.id}">
      <div class="s-room-hd">
        <span class="s-room-name">${esc(room.name)}</span>
        <span class="s-room-badge">${interval} min</span>
        <button class="btn sm" data-action="edit-room" data-room-id="${room.id}">⚙ Edit</button>
        <button class="btn sm primary" data-action="add-device" data-room-id="${room.id}">+ Add Device</button>
        <button class="btn sm danger" data-action="delete-room" data-room-id="${room.id}">Remove</button>
      </div>
      <div class="s-room-body" id="sbody-${room.id}">
        ${table}
        <div class="add-dev-form" id="add-form-${room.id}">
          <div class="form-row edit-top-row">
            <div class="form-group form-group-wide">
              <label class="form-label" for="new-dev-name-${room.id}">Device Name</label>
              <input class="input input-full" type="text" id="new-dev-name-${room.id}" placeholder="e.g. Core Switch">
            </div>
            <div class="form-group form-group-wide">
              <label class="form-label" for="new-dev-ip-${room.id}">IP Address</label>
              <input class="input input-full" type="text" id="new-dev-ip-${room.id}" placeholder="192.168.1.1">
            </div>
          </div>
          <div class="form-row edit-bottom-row">
            <div class="form-group">
              <label class="form-label" for="new-dev-type-${room.id}">Check Type</label>
              <select class="select" id="new-dev-type-${room.id}" data-action="toggle-port" data-room-id="${room.id}">
                <option value="ping" title="ICMP echo — tests host reachability">ICMP Ping</option>
                <option value="udp" title="UDP probe — online if host responds or refuses port">UDP</option>
                <option value="ssh" title="TCP connect — online if SSH banner received">SSH</option>
                <option value="http" title="HEAD request — online if server responds (follows redirects)">HTTP(S)</option>
              </select>
            </div>
            <div class="form-group port-group" id="new-dev-udp-group-${room.id}">
              <label class="form-label" for="new-dev-udp-${room.id}">UDP Port</label>
              <input class="input input-timeout" type="number" id="new-dev-udp-${room.id}" value="9" min="1" max="65535">
            </div>
            <div class="form-group port-group" id="new-dev-ssh-group-${room.id}">
              <label class="form-label" for="new-dev-ssh-${room.id}">SSH Port</label>
              <input class="input input-timeout" type="number" id="new-dev-ssh-${room.id}" value="22" min="1" max="65535">
            </div>
            <div class="form-group port-group" id="new-dev-http-group-${room.id}">
              <label class="form-label" for="new-dev-http-${room.id}">HTTP Port</label>
              <input class="input input-timeout" type="number" id="new-dev-http-${room.id}" value="80" min="1" max="65535">
            </div>
            <div class="form-group">
              <label class="form-label" for="new-dev-timeout-${room.id}">Timeout (s)</label>
              <input class="input input-timeout" type="number" id="new-dev-timeout-${room.id}" value="2.0" min="0.5" max="30" step="0.5">
            </div>
            <div class="form-group form-group-end">
              <div class="form-label">&nbsp;</div>
              <div class="form-actions">
                <button class="btn sm" data-action="cancel-add" data-room-id="${room.id}">Cancel</button>
                <button class="btn sm primary" data-action="submit-device" data-room-id="${room.id}">Add Device</button>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>`;
  }).join('');
  if (_staggerSettings) {
    _staggerSettings = false;
    container.querySelectorAll('.s-room-card').forEach((c, i) => {
      c.style.setProperty('--i', i);
      c.classList.add('enter');
    });
  }
}

// ── Settings event delegation ────────────────────────────────────────
//   Single click listener on the settings room list replaces all inline
//   onclick handlers. Buttons carry data-action, data-room-id, and
//   data-device-id attributes; this handler dispatches to the right function.
(function initSettingsDelegation() {
  const root = document.getElementById('settings-rooms-list');
  if (!root) return;
  root.addEventListener('click', e => {
    const btn = e.target.closest('[data-action]');
    if (!btn) return;
    const { action, roomId, deviceId } = btn.dataset;
    switch (action) {
      case 'edit-room':     openRoomModal(roomId); break;
      case 'add-device':    openAddDevice(roomId); break;
      case 'delete-room':   deleteRoom(roomId); break;
      case 'edit-device':   openEditDevice(roomId, deviceId); break;
      case 'delete-device': deleteDevice(roomId, deviceId); break;
      case 'cancel-add':    closeAddDevice(roomId); break;
      case 'submit-device': submitDevice(roomId); break;
    }
  });
  root.addEventListener('change', e => {
    const sel = e.target.closest('[data-action="toggle-port"]');
    if (sel) toggleAddPort(sel.dataset.roomId);
  });
})();

(function initDeletedRoomsDelegation() {
  const root = document.getElementById('deleted-rooms-list');
  if (!root) return;
  root.addEventListener('click', e => {
    const btn = e.target.closest('[data-action]');
    if (!btn) return;
    const { action, roomId, roomName } = btn.dataset;
    switch (action) {
      case 'restore-room': restoreRoom(roomId); break;
      case 'purge-room':   permanentlyDeleteRoom(roomId, roomName || 'this room'); break;
    }
  });
})();

// ═══════════════════════════════════════════════════════════════════════
//  ROOM ACTIONS
//  Create / edit / delete handlers for rooms within the active workspace.
//  Every write goes through markUnsaved → API call → markSaved so the
//  footer indicator reflects what's in flight.
// ═══════════════════════════════════════════════════════════════════════
/** Read the "new room" form, POST to /api/workspaces/<id>/rooms, refresh. */
async function addRoom() {
  const nameEl     = document.getElementById('new-room-name');
  const intervalEl = document.getElementById('new-room-interval');
  const name = nameEl ? nameEl.value.trim() : '';
  if (!name) { notify('Enter a room name.', 'error'); if (nameEl) nameEl.focus(); return; }
  const interval   = ((intervalEl ? parseInt(intervalEl.value) : 5) || 5) * 60;
  try {
    if (!_activeWsId) { notify('No workspace selected.', 'error'); return; }
    await api('POST', '/api/workspaces/' + _activeWsId + '/rooms', { name, interval });
    if (nameEl) nameEl.value = '';
    markUnsaved();
    notify(`Room "${name}" added.`, 'success');
    await loadRooms();
    renderSettings();
    markSaved();
  } catch(e) {
    notify(e.message, 'error');
  }
}

document.getElementById('new-room-name').addEventListener('keydown', e => {
  if (e.key === 'Enter') addRoom();
});

/** Confirm + soft-delete a room (recoverable for 24 hours). */
async function deleteRoom(roomId) {
  const room = _rooms.find(r => r.id === roomId);
  const name = room?.name || 'this room';
  const count = (room?.devices||[]).length;
  const msg = count > 0
    ? `Remove "${name}" and its ${count} device${count!==1?'s':''}?\nRecoverable for 24 hours from Recently Removed.`
    : `Remove room "${name}"?\nRecoverable for 24 hours from Recently Removed.`;
  const ok = await vigilConfirm(msg, 'Remove Room');
  if (!ok) return;
  try {
    await api('DELETE', '/api/rooms/' + roomId);
    markUnsaved();
    notify(`Room "${name}" removed. Restore from Recently Removed.`, 'success');
    await loadRooms();
    renderSettings();
    renderDeletedRooms();
    renderDash();
    markSaved();
  } catch(e) {
    notify(e.message, 'error');
  }
}

async function restoreRoom(roomId) {
  try {
    await api('POST', '/api/deleted-rooms/' + roomId + '/restore');
    markUnsaved();
    notify('Room restored.', 'success');
    await loadRooms();
    renderSettings();
    renderDeletedRooms();
    renderDash();
    markSaved();
  } catch(e) {
    notify(e.message, 'error');
  }
}

async function permanentlyDeleteRoom(roomId, name) {
  const ok = await vigilConfirm(
    `Permanently delete "${name}"? This cannot be undone.`,
    'Delete Forever');
  if (!ok) return;
  try {
    await api('DELETE', '/api/deleted-rooms/' + roomId);
    notify('Room permanently deleted.', 'success');
    renderDeletedRooms();
  } catch(e) {
    notify(e.message, 'error');
  }
}

async function renderDeletedRooms() {
  const container = document.getElementById('deleted-rooms-list');
  if (!container) return;
  try {
    const items = await api('GET', '/api/deleted-rooms');
    if (!items.length) {
      container.style.display = 'none';
      container.innerHTML = '';
      return;
    }
    container.style.display = '';
    container.innerHTML =
      '<div class="settings-hd"><span class="settings-hd-title">Recently Removed</span></div>' +
      items.map(e => {
        const ago = timeAgo(e.deleted_at);
        const devLabel = e.device_count === 1 ? '1 device' : e.device_count + ' devices';
        return `<div class="deleted-room-row">
          <span class="deleted-room-name">${esc(e.name)}</span>
          <span class="deleted-room-meta">${devLabel} · removed ${ago}</span>
          <button class="btn sm primary" data-action="restore-room" data-room-id="${e.id}">Restore</button>
          <button class="btn sm danger" data-action="purge-room" data-room-id="${e.id}" data-room-name="${esc(e.name)}">Delete Forever</button>
        </div>`;
      }).join('');
  } catch(e) { notify('Failed to load deleted rooms.', 'error'); }
}


// ── Room modal ────────────────────────────────────────────────────────
/** Open the room-settings modal pre-filled with this room's values. */
function openRoomModal(roomId) {
  const room = _rooms.find(r => r.id === roomId);
  if (!room) return;
  $val('rs-room-id', roomId);
  $val('rs-name',    room.name);
  $val('rs-interval', Math.round((room.interval||300)/60));
  $cls('room-modal', 'add', 'open');
  setTimeout(() => { const e=$id('rs-name'); if(e) e.focus(); }, MODAL_FOCUS_DELAY);
}

/** Close the room-settings modal without saving. */
function closeRoomModal() {
  $cls('room-modal', 'remove', 'open');
}

/** Submit the room-settings modal: PUT /api/rooms/<id>, refresh both views. */
async function saveRoomSettings() {
  const roomId   = document.getElementById('rs-room-id').value;
  const name     = document.getElementById('rs-name').value.trim();
  const interval = (parseInt(document.getElementById('rs-interval').value)||5) * 60;
  if (!name) { notify('Room name is required.', 'error'); return; }
  try {
    await api('PUT', '/api/rooms/' + roomId, { name, interval });
    closeRoomModal();
    markUnsaved();
    notify('Room updated.', 'success');
    await loadRooms();
    renderSettings();
    renderDash();
    markSaved();
  } catch(e) {
    notify(e.message, 'error');
  }
}

// ═══════════════════════════════════════════════════════════════════════
//  DEVICE ACTIONS
//  Add / edit / delete handlers for devices within a room. UDP-specific
//  form fields are only shown when check_type === 'udp'.
// ═══════════════════════════════════════════════════════════════════════
function toggleAddPort(roomId) {
  const v = document.getElementById('new-dev-type-' + roomId)?.value;
  const udp  = document.getElementById('new-dev-udp-group-' + roomId);
  const ssh  = document.getElementById('new-dev-ssh-group-' + roomId);
  const http = document.getElementById('new-dev-http-group-' + roomId);
  if (udp)  udp.style.display  = v === 'udp'  ? 'flex' : 'none';
  if (ssh)  ssh.style.display  = v === 'ssh'  ? 'flex' : 'none';
  if (http) http.style.display = v === 'http' ? 'flex' : 'none';
}

function toggleEditPort() {
  const v = document.getElementById('edit-check-type')?.value;
  const udp  = document.getElementById('edit-udp-group');
  const ssh  = document.getElementById('edit-ssh-group');
  const http = document.getElementById('edit-http-group');
  if (udp)  udp.style.display  = v === 'udp'  ? 'flex' : 'none';
  if (ssh)  ssh.style.display  = v === 'ssh'  ? 'flex' : 'none';
  if (http) http.style.display = v === 'http' ? 'flex' : 'none';
}

/** Toggle the inline Add-Device form open under a specific room. Closes any
 *  other room's open form first so only one is visible at a time. */
function openAddDevice(roomId) {
  // Close all other open add forms first
  document.querySelectorAll('.add-dev-form.open').forEach(f => {
    if (f.id !== 'add-form-' + roomId) f.classList.remove('open');
  });
  const form = document.getElementById('add-form-' + roomId);
  if (!form) { notify('Cannot find form — refresh the settings page.', 'error'); return; }
  const isOpen = form.classList.toggle('open');
  if (isOpen) {
    const nameInput = document.getElementById('new-dev-name-' + roomId);
    if (nameInput) setTimeout(() => nameInput.focus(), MODAL_FOCUS_DELAY);
  }
}

/** Close the inline Add-Device form for a specific room. */
function closeAddDevice(roomId) {
  const form = document.getElementById('add-form-' + roomId);
  if (form) form.classList.remove('open');
}

/** Validate the Add-Device form and POST to /api/rooms/<id>/devices. */
async function submitDevice(roomId) {
  // Read values before any async operation
  const nameEl    = document.getElementById('new-dev-name-' + roomId);
  const ipEl      = document.getElementById('new-dev-ip-' + roomId);
  const typeEl    = document.getElementById('new-dev-type-' + roomId);
  const udpEl     = document.getElementById('new-dev-udp-' + roomId);
  const sshEl     = document.getElementById('new-dev-ssh-' + roomId);
  const httpEl    = document.getElementById('new-dev-http-' + roomId);
  const timeoutEl = document.getElementById('new-dev-timeout-' + roomId);

  if (!nameEl || !ipEl) {
    notify('Form not found — refresh the settings page.', 'error');
    return;
  }

  const name       = nameEl.value.trim();
  const ip         = ipEl.value.trim();
  const check_type = typeEl ? typeEl.value : 'ping';
  const udp_port   = udpEl ? (parseInt(udpEl.value)||9) : 9;
  const ssh_port   = sshEl ? (parseInt(sshEl.value)||22) : 22;
  const http_port  = httpEl ? (parseInt(httpEl.value)||80) : 80;
  const timeout    = timeoutEl ? (parseFloat(timeoutEl.value)||2.0) : 2.0;

  if (!name) { notify('Device name is required.', 'error'); nameEl.focus(); return; }
  if (!ip)   { notify('IP address is required.', 'error'); ipEl.focus(); return; }

  try {
    await api('POST', '/api/rooms/' + roomId + '/devices',
              { name, ip, check_type, udp_port, ssh_port, http_port, timeout });
    // Clear form fields
    nameEl.value = '';
    ipEl.value   = '';
    closeAddDevice(roomId);
    markUnsaved();
    notify(`Device "${name}" added.`, 'success');
    await loadRooms();
    renderSettings();
    renderDash();
    markSaved();
  } catch(e) {
    notify(e.message, 'error');
  }
}

// ── Edit device modal ─────────────────────────────────────────────────
/** Open the device-edit modal pre-filled with this device's values. */
function openEditDevice(roomId, deviceId) {
  const room   = _rooms.find(r => r.id === roomId);
  const device = room?.devices?.find(d => d.id === deviceId);
  if (!device) { notify('Device not found.', 'error'); return; }

  $val('edit-room-id', roomId);
  $val('edit-dev-id',    deviceId);
  $val('edit-name',      device.name);
  $val('edit-ip',        device.ip);
  $val('edit-check-type', device.check_type || 'ping');
  $val('edit-udp-port',  device.udp_port || 9);
  $val('edit-ssh-port',  device.ssh_port || 22);
  $val('edit-http-port', device.http_port || 80);
  $val('edit-timeout',   device.timeout != null ? device.timeout : 2.0);
  toggleEditPort();
  $cls('edit-modal', 'add', 'open');
  setTimeout(() => { const e=$id('edit-name'); if(e) e.focus(); }, MODAL_FOCUS_DELAY);
}

/** Close the device-edit modal without saving. */
function closeEditModal() {
  $cls('edit-modal', 'remove', 'open');
}

/** Submit the device-edit modal: PUT /api/rooms/<r>/devices/<d>, refresh. */
async function saveDeviceEdit() {
  const roomId   = document.getElementById('edit-room-id').value;
  const deviceId = document.getElementById('edit-dev-id').value;
  const name     = document.getElementById('edit-name').value.trim();
  const ip       = document.getElementById('edit-ip').value.trim();
  const check_type = document.getElementById('edit-check-type').value;
  const udp_port  = parseInt(document.getElementById('edit-udp-port').value)||9;
  const ssh_port  = parseInt(document.getElementById('edit-ssh-port').value)||22;
  const http_port = parseInt(document.getElementById('edit-http-port').value)||80;
  const timeout   = parseFloat(document.getElementById('edit-timeout').value)||2.0;

  if (!name) { notify('Name is required.', 'error'); return; }
  if (!ip)   { notify('IP address is required.', 'error'); return; }

  try {
    await api('PUT', '/api/rooms/' + roomId + '/devices/' + deviceId,
              { name, ip, check_type, udp_port, ssh_port, http_port, timeout });
    closeEditModal();
    markUnsaved();
    notify(`Device "${name}" updated.`, 'success');
    await loadRooms();
    renderSettings();
    renderDash();
    markSaved();
  } catch(e) {
    notify(e.message, 'error');
  }
}

/** Confirm + delete a device, then refresh both views. */
async function deleteDevice(roomId, deviceId) {
  const room = _rooms.find(r => r.id === roomId);
  const device = room?.devices?.find(d => d.id === deviceId);
  const name = device?.name || 'this device';
  const ok = await vigilConfirm(`Remove device "${name}"?`, 'Remove Device');
  if (!ok) return;
  try {
    await api('DELETE', '/api/rooms/' + roomId + '/devices/' + deviceId);
    markUnsaved();
    notify(`Device "${name}" removed.`, 'success');
    await loadRooms();
    renderSettings();
    renderDash();
    markSaved();
  } catch(e) {
    notify(e.message, 'error');
  }
}

// ═══════════════════════════════════════════════════════════════════════
//  CONFIG
//  Manual poll trigger plus import/export of the full config.json. The
//  export is a normal download; import takes a JSON file the user picks.
// ═══════════════════════════════════════════════════════════════════════
async function triggerPoll() {
  try {
    await api('POST', '/api/poll');
    notify('Poll triggered.', 'success');
    setTimeout(() => loadRooms().then(() => { renderSettings(); renderDash(); }), POLL_RESULT_DELAY);
  } catch(e) {
    notify(e.message, 'error');
  }
}

async function pollRoom(evt, roomId) {
  evt.stopPropagation();
  try {
    await api('POST', '/api/poll', { room_id: roomId });
    notify('Room poll triggered.', 'success');
    setTimeout(() => loadRooms().then(() => { renderSettings(); renderDash(); }), POLL_RESULT_DELAY);
  } catch(e) {
    notify(e.message, 'error');
  }
}

/** Download the current config.json as an attachment via the server endpoint. */
function exportConfig() {
  window.location.href = '/api/config/export';
  notify('Config exported.', 'success');
}

/** Read a user-selected JSON file and POST it to the import endpoint. */
async function importConfig(input) {
  const file = input.files?.[0];
  if (!file) return;
  try {
    const text = await file.text();
    const data = JSON.parse(text);
    const wsList = data.workspaces || [];
    const roomCount = wsList.reduce((n, ws) => n + (ws.rooms || []).length, 0);
    const devCount  = wsList.reduce((n, ws) => (ws.rooms || []).reduce((m, r) => m + (r.devices || []).length, n), 0);
    const summary = roomCount || devCount
      ? `This will replace your current configuration with ${roomCount} room${roomCount !== 1 ? 's' : ''} and ${devCount} device${devCount !== 1 ? 's' : ''}.`
      : 'This will replace your current configuration.';
    const ok = await vigilConfirm(summary + ' Your current config will be backed up automatically. PIN will not be affected.', 'Import Configuration');
    if (!ok) { input.value = ''; return; }
    const res = await api('POST', '/api/config/import', data);
    const msg = res.backup
      ? `Config imported. Previous config backed up to ${res.backup}`
      : 'Config imported.';
    notify(msg, 'success');
    await loadRooms();
    renderSettings();
    renderDash();
    input.value = '';
  } catch(e) {
    notify('Import failed: ' + e.message, 'error');
    input.value = '';
  }
}

// ═══════════════════════════════════════════════════════════════════════
//  CONFIGURATION TAB
// ═══════════════════════════════════════════════════════════════════════
function renderConfigTab() {
  const el = $id('stab-content-config');
  if (!el) return;
  el.innerHTML = `
    <div class="config-tab-body">
      <div class="settings-hd"><span class="settings-hd-title">Export Configuration</span></div>
      <p class="config-hint">
        Download the current configuration as a JSON file. Includes all workspaces,
        rooms, devices, and layout settings. Your PIN is <strong>not</strong> included in the export.
      </p>
      <button class="btn primary config-action" onclick="exportConfig()">Export config.json</button>

      <div class="settings-hd"><span class="settings-hd-title">Import Configuration</span></div>
      <p class="config-hint">
        Replace the current configuration by uploading a previously exported JSON file.
        Your current config is automatically backed up to <strong>config.backup.json</strong> before importing.
        Your existing PIN will be preserved &mdash; imported files cannot overwrite it.
      </p>
      <label class="btn primary config-import-label">
        Choose file&hellip;
        <input type="file" accept=".json" style="display:none" onchange="importConfig(this)">
      </label>
    </div>`;
}

// ═══════════════════════════════════════════════════════════════════════
//  PRIVACY POLICY
//  Open / close handlers for the privacy-policy modal. Content is static
//  HTML in the modal body; these only toggle visibility.
// ═══════════════════════════════════════════════════════════════════════
/** Open the privacy-policy modal. */
function showPrivacy() {
  $cls('privacy-modal', 'add', 'open');
}
/** Close the privacy-policy modal. */
function closePrivacy() {
  $cls('privacy-modal', 'remove', 'open');
}

// ═══════════════════════════════════════════════════════════════════════
//  KEYBOARD & CLICK HANDLERS
//  Global UX glue: Escape closes any open modal; clicking the dark
//  backdrop closes the modal it's behind. Attached once at script load.
// ═══════════════════════════════════════════════════════════════════════
document.addEventListener('keydown', e => {
  if (e.key === 'Escape') {
    closeEditModal();
    closeRoomModal();
    closeWsModal();
    closePrivacy();
    closeConfirm(false);
  }
});
document.getElementById('edit-modal').addEventListener('click', function(e) {
  if (e.target === this) closeEditModal();
});
document.getElementById('room-modal').addEventListener('click', function(e) {
  if (e.target === this) closeRoomModal();
});
document.getElementById('ws-modal').addEventListener('click', function(e) {
  if (e.target === this) closeWsModal();
});
document.getElementById('privacy-modal').addEventListener('click', function(e) {
  if (e.target === this) closePrivacy();
});
document.getElementById('confirm-modal').addEventListener('click', function(e) {
  if (e.target === this) closeConfirm(false);
});

// ═══════════════════════════════════════════════════════════════════════
//  AUTH
//  Login / logout flows. On boot we hit /api/auth/check — if auth is
//  enabled and the session is invalid, we show the login screen instead
//  of the dashboard. The login screen is a full-viewport gate.
// ═══════════════════════════════════════════════════════════════════════
let _authEnabled = false;

function showLogin() {
  stopRefresh();
  document.querySelectorAll('.screen').forEach(s => s.classList.remove('active'));
  $cls('screen-login', 'add', 'active');
  const inp = $id('login-pin');
  if (inp) { inp.value = ''; setTimeout(() => inp.focus(), 100); }
  const err = $id('login-err');
  if (err) err.textContent = '';
}

async function doLogin() {
  const inp = $id('login-pin');
  const err = $id('login-err');
  const btn = $id('login-btn');
  const pin = inp ? inp.value : '';
  if (!pin) { if (err) err.textContent = 'Enter your PIN.'; return; }
  if (btn) { btn.disabled = true; btn.classList.add('loading'); }
  try {
    await api('POST', '/api/auth/login', { pin });
    if (err) err.textContent = '';
    showDashboard();
  } catch(e) {
    if (err) err.textContent = e.message || 'Invalid PIN.';
    if (inp) { inp.value = ''; inp.focus(); }
  } finally {
    if (btn) { btn.disabled = false; btn.classList.remove('loading'); }
  }
}

async function doLogout() {
  try { await api('POST', '/api/auth/logout'); } catch(_) {}
  showLogin();
}

async function changePin() {
  const cur = $id('sec-current-pin');
  const nw  = $id('sec-new-pin');
  if (!cur || !nw) return;
  const current_pin = cur.value;
  const new_pin = nw.value;
  if (!new_pin || new_pin.length < 4) { notify('New PIN must be at least 4 characters.', 'error'); nw.focus(); return; }
  try {
    await api('POST', '/api/auth/change-pin', { current_pin, new_pin });
    cur.value = ''; nw.value = '';
    notify('PIN changed.', 'success');
  } catch(e) {
    notify(e.message, 'error');
  }
}

async function enablePin() {
  const inp = $id('sec-enable-pin');
  if (!inp) return;
  const pin = inp.value.trim();
  if (!pin || pin.length < 4) { notify('PIN must be at least 4 characters.', 'error'); inp.focus(); return; }
  try {
    await api('POST', '/api/auth/change-pin', { current_pin: '', new_pin: pin });
    await api('POST', '/api/auth/login', { pin });
    inp.value = '';
    _authEnabled = true;
    _updateAuthUI();
    renderSecurityTab();
    notify('PIN protection enabled.', 'success');
  } catch(e) {
    notify(e.message, 'error');
  }
}

async function disablePin() {
  const inp = $id('sec-disable-pin');
  if (!inp) return;
  const pin = inp.value;
  if (!pin) { notify('Enter your current PIN to confirm.', 'error'); inp.focus(); return; }
  const ok = await vigilConfirm('Disable PIN protection? Anyone on this network will be able to access Vigil without authenticating.', 'Disable Protection');
  if (!ok) return;
  try {
    await api('POST', '/api/auth/disable', { pin });
    inp.value = '';
    _authEnabled = false;
    _updateAuthUI();
    renderSecurityTab();
    notify('PIN protection disabled.', 'success');
  } catch(e) {
    notify(e.message, 'error');
  }
}

function renderSecurityTab() {
  const el = $id('stab-content-security');
  if (!el) return;

  const statusBadge = _authEnabled
    ? '<span class="sec-status on"><span class="sec-dot on"></span> Enabled</span>'
    : '<span class="sec-status off"><span class="sec-dot off"></span> Disabled</span>';

  const info = `<div class="sec-info">
      <div class="sec-info-title">About PIN Protection</div>
      When enabled, a PIN is required before anyone can access the Vigil dashboard or modify settings.
      Your PIN cannot be recovered if forgotten&nbsp;&mdash; it is stored securely as a one-way hash, never in plaintext.
      Sessions expire automatically after 8 hours.
    </div>`;

  let body = '';
  if (_authEnabled) {
    body = `
      <div class="sec-section">
        <div class="sec-section-title">Change PIN</div>
        <div class="sec-form-row">
          <div class="form-group">
            <label class="form-label" for="sec-current-pin">Current PIN</label>
            <input class="input sec-pin-input" type="password" id="sec-current-pin" placeholder="····">
          </div>
          <div class="form-group">
            <label class="form-label" for="sec-new-pin">New PIN</label>
            <input class="input sec-pin-input" type="password" id="sec-new-pin" placeholder="Min 4 characters">
          </div>
          <button class="btn primary" onclick="changePin()">Update PIN</button>
        </div>
      </div>
      <div class="sec-section">
        <div class="sec-section-title">Disable Protection</div>
        <div class="sec-hint">
          Remove the PIN requirement. Anyone with network access to this server will be able to view and modify Vigil.
        </div>
        <div class="sec-form-row">
          <div class="form-group">
            <label class="form-label" for="sec-disable-pin">Confirm Current PIN</label>
            <input class="input sec-pin-input" type="password" id="sec-disable-pin" placeholder="····">
          </div>
          <button class="btn danger" onclick="disablePin()">Disable PIN</button>
        </div>
      </div>`;
  } else {
    body = `
      <div class="sec-section">
        <div class="sec-section-title">Enable Protection</div>
        <div class="sec-hint">
          Set a PIN to require authentication before anyone can access Vigil.
        </div>
        <div class="sec-form-row">
          <div class="form-group">
            <label class="form-label" for="sec-enable-pin">New PIN</label>
            <input class="input sec-pin-input" type="password" id="sec-enable-pin" placeholder="Min 4 characters">
          </div>
          <button class="btn primary" onclick="enablePin()">Enable PIN</button>
        </div>
      </div>`;
  }

  el.innerHTML = `
    <div class="settings-hd">
      <span class="settings-hd-title">Security</span>
      <span class="sec-status-wrap">${statusBadge}</span>
    </div>
    <div class="s-room-card">
      <div class="s-room-body sec-body">
        ${info}
        ${body}
      </div>
    </div>`;
}

function _updateAuthUI() {
  document.querySelectorAll('.auth-only').forEach(el => {
    el.style.display = _authEnabled ? '' : 'none';
  });
  renderSecurityTab();
}

// Enter key submits on login and modal forms
document.getElementById('login-pin').addEventListener('keydown', e => {
  if (e.key === 'Enter') doLogin();
});
document.getElementById('edit-modal').addEventListener('keydown', e => {
  if (e.key === 'Enter' && e.target.tagName !== 'SELECT') saveDeviceEdit();
});
document.getElementById('room-modal').addEventListener('keydown', e => {
  if (e.key === 'Enter') saveRoomSettings();
});
document.getElementById('ws-modal').addEventListener('keydown', e => {
  if (e.key === 'Enter') saveWsSettings();
});

// ═══════════════════════════════════════════════════════════════════════
//  STARTUP
//  Check auth status, then show login or dashboard accordingly.
// ═══════════════════════════════════════════════════════════════════════
(async function boot() {
  try {
    const auth = await api('GET', '/api/auth/check');
    _authEnabled = auth.auth_enabled;
    _updateAuthUI();
    if (auth.authenticated) {
      showDashboard();
    } else {
      showLogin();
    }
  } catch(e) {
    _authEnabled = true;
    _updateAuthUI();
    showLogin();
  }
})();

// ── Expose to window for HTML onclick handlers ──────────────────────────
// WARNING: every function referenced by an onclick attribute in HTML or
// template strings must be listed here, otherwise it silently fails.
const _api = { doLogin, doLogout, switchTab, switchWorkspace, switchOutput, switchSettingsTab,
  addRoom, deleteRoom, openRoomModal, closeRoomModal, saveRoomSettings,
  openAddDevice, closeAddDevice, submitDevice, toggleAddPort,
  openEditDevice, closeEditModal, saveDeviceEdit, toggleEditPort,
  deleteDevice, renameWs, closeWsModal, saveWsSettings, deleteWs, popoutWs,
  triggerPoll, pollRoom, exportConfig, importConfig,
  showPrivacy, closePrivacy, closeConfirm,
  resetRoomSize, changePin, enablePin, disablePin };
for (const [k, v] of Object.entries(_api)) window[k] = v;

})(); // end IIFE