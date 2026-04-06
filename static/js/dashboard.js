/* ── Dashboard JavaScript ───────────────────────────────────── */

const API = '';          // same origin
let currentDeviceIp = null;
let currentCapabilities = [];
let selectedWlanSsid = null;   // SSID chosen by the user in the WLAN modal

// ── WLAN ──────────────────────────────────────────────────────

async function refreshWlanStatus() {
  try {
    const r = await fetch(`${API}/api/wlan/status`);
    const s = await r.json();
    const badge = document.getElementById('wlan-badge');
    if (s.connected) {
      badge.className = 'badge badge-connected';
      badge.innerHTML = `<i class="bi bi-wifi"></i> ${escHtml(s.ssid)} · ${escHtml(s.ip_address)}`;
      // Auto-select the connected SSID so scans default to that network.
      if (!selectedWlanSsid) {
        selectedWlanSsid = s.ssid;
      }
    } else {
      badge.className = 'badge bg-secondary';
      badge.innerHTML = `<i class="bi bi-wifi-off"></i> Disconnected`;
    }
  } catch (_) {}
}

async function loadNetworks() {
  const el = document.getElementById('network-list');
  el.innerHTML = '<span class="text-muted">Scanning…</span>';
  // Check availability first to decide if demo banner is needed
  let demoMode = false;
  try {
    const ar = await fetch(`${API}/api/wlan/availability`);
    const availability = await ar.json();
    demoMode = !!availability.demo;
  } catch (_) {}
  try {
    const r = await fetch(`${API}/api/wlan/networks`);
    const nets = await r.json();
    if (!nets.length) {
      el.innerHTML = `<p class="text-muted mb-0">No networks found.</p>
        <div class="mt-2 small text-warning">
          <div>No wireless interface or scan tools detected on this host.</div>
          <div class="mt-1">Run this app on a host with Wi-Fi hardware to see nearby SSIDs.</div>
          <div class="mt-1 text-info">Tip: set <code>HA_DEMO_MODE=1</code> to enable mock networks for UI testing.</div>
        </div>`;
      return;
    }
    const demoBanner = demoMode
      ? '<div class="alert alert-warning alert-sm py-1 px-2 mb-2 small">⚠️ Demo mode – these are mock networks (set <code>HA_DEMO_MODE=1</code>)</div>'
      : '';
    el.innerHTML = demoBanner + nets.map(n => {
      const pct = Math.min(100, Math.max(0, (n.signal || 0)));
      const encodedSsid = encodeURIComponent(n.ssid || '');
      const demoTag = n._demo ? ' <span class="badge bg-secondary ms-1" style="font-size:0.65em">DEMO</span>' : '';
      return `
        <div class="network-row" onclick="selectNetworkFromEncoded('${encodedSsid}')">
          <div class="flex-grow-1">
            <span class="fw-semibold">${escHtml(n.ssid)}</span>${demoTag}
            <span class="text-muted small ms-2">${escHtml(n.security || 'Open')}</span>
          </div>
          <div class="d-flex align-items-center gap-2 me-3">
            <span class="text-muted small">ch ${n.channel || '?'}</span>
          </div>
          <div class="signal-bar">
            <div class="signal-fill" style="width:${pct}%"></div>
          </div>
          <span class="ms-2 small text-muted">${n.signal}%</span>
        </div>`;
    }).join('');
  } catch (e) {
    el.innerHTML = `<p class="text-danger">Error: ${escHtml(e.message)}</p>`;
  }
}

function selectNetwork(ssid) {
  document.getElementById('connect-ssid').value = ssid;
  selectedWlanSsid = ssid;
  updateScanContextBadge();
}

function selectNetworkFromEncoded(encodedSsid) {
  selectNetwork(decodeURIComponent(encodedSsid));
}

async function connectWlan() {
  const ssid     = document.getElementById('connect-ssid').value.trim();
  const password = document.getElementById('connect-password').value;
  const resultEl = document.getElementById('connect-result');
  if (!ssid) { resultEl.innerHTML = '<span class="text-warning">Enter an SSID first.</span>'; return; }
  resultEl.innerHTML = 'Connecting…';
  try {
    const r = await fetch(`${API}/api/wlan/connect`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ssid, password}),
    });
    const d = await r.json();
    if (d.ok) {
      resultEl.innerHTML = '<span class="text-success"><i class="bi bi-check-circle"></i> Connected!</span>';
      selectedWlanSsid = ssid;
      updateScanContextBadge();
      refreshWlanStatus();
    } else {
      resultEl.innerHTML = `<span class="text-danger">Failed: ${escHtml(d.error || 'unknown error')}</span>`;
    }
  } catch (e) {
    resultEl.innerHTML = `<span class="text-danger">Error: ${escHtml(e.message)}</span>`;
  }
}

function showWlanModal() {
  new bootstrap.Modal(document.getElementById('wlanModal')).show();
  loadNetworks();
}

// ── Device scanning ───────────────────────────────────────────

function updateScanContextBadge() {
  const el = document.getElementById('scan-context');
  if (!el) return;
  if (selectedWlanSsid) {
    el.innerHTML = `<span class="badge bg-info text-dark"><i class="bi bi-wifi"></i> ${escHtml(selectedWlanSsid)}</span>`;
    el.title = `Scan will target the subnet of WLAN: ${selectedWlanSsid}`;
  } else {
    el.innerHTML = '';
    el.title = '';
  }
}

async function scanDevices() {
  const btn      = document.getElementById('scan-btn');
  const statusEl = document.getElementById('scan-status');
  const network  = document.getElementById('network-input').value.trim();
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner-pulse"><i class="bi bi-radar"></i></span> Scanning…';
  statusEl.textContent = '';
  const ctxEl = document.getElementById('scan-context-info');
  if (ctxEl) ctxEl.innerHTML = '';
  try {
    let url = `${API}/api/devices/scan`;
    const params = new URLSearchParams();
    if (network) {
      params.set('network', network);
    } else if (selectedWlanSsid) {
      params.set('wlan', selectedWlanSsid);
    }
    if ([...params].length) url += '?' + params.toString();

    const r = await fetch(url);
    const body = await r.json();

    if (!r.ok) {
      // Structured error from WLAN-scoped scan (e.g. not connected)
      statusEl.innerHTML = `<span class="text-danger"><i class="bi bi-exclamation-triangle"></i> ${escHtml(body.error || 'Scan failed')}</span>`;
      renderDevices([]);
      return;
    }

    // Response is {devices: [...], scan_context: {...}}
    const devices = Array.isArray(body.devices) ? body.devices : [];
    const ctx = body.scan_context || {};
    renderDevices(devices);
    if (likelyGatewayOnlyResult(devices)) {
      statusEl.textContent = 'Found 1 device (likely your router). Containerized environments can limit LAN discovery.';
    } else {
      statusEl.textContent = `Found ${devices.length} device(s).`;
    }

    // Show resolved scan context (subnet / interface used)
    if (ctxEl && (ctx.ssid || ctx.subnet || ctx.interface)) {
      const parts = [];
      if (ctx.ssid)      parts.push(`<i class="bi bi-wifi"></i> <strong>${escHtml(ctx.ssid)}</strong>`);
      if (ctx.subnet)    parts.push(`subnet: <code>${escHtml(ctx.subnet)}</code>`);
      if (ctx.interface) parts.push(`interface: <code>${escHtml(ctx.interface)}</code>`);
      ctxEl.innerHTML = `<span class="text-info small">${parts.join(' · ')}</span>`;
    }
  } catch (e) {
    statusEl.textContent = `Error: ${e.message}`;
  } finally {
    btn.disabled = false;
    btn.innerHTML = '<i class="bi bi-radar"></i> Scan Network';
  }
}

function renderDevices(devices) {
  const grid = document.getElementById('device-grid');
  document.getElementById('no-devices-msg')?.remove();

  if (!devices.length) {
    grid.innerHTML = `
      <div class="col-12 text-center text-muted py-5">
        <i class="bi bi-question-circle fs-1 d-block mb-2"></i>
        No devices found on the network.
      </div>`;
    return;
  }

  grid.innerHTML = devices.map(d => deviceCard(d)).join('');

  // Use event delegation — avoids XSS via inline onclick
  grid.addEventListener('click', (e) => {
    const card = e.target.closest('.device-card[data-device-ip]');
    if (card) {
      openDeviceModal(card.dataset.deviceIp, card.dataset.deviceType);
    }
  }, { once: false });
}

function likelyGatewayOnlyResult(devices) {
  if (!Array.isArray(devices) || devices.length !== 1) {
    return false;
  }
  const d = devices[0] || {};
  const ip = String(d.ip || '');
  const octets = ip.split('.');
  const lastOctet = parseInt(octets[octets.length - 1], 10);
  const isGatewayLikeIp = Number.isInteger(lastOctet) && (lastOctet === 1 || lastOctet === 254);
  const plugin = String(d.plugin_id || '');
  const services = Array.isArray(d.services) ? d.services : [];
  const genericLike = plugin === 'generic_http' || plugin === 'generic';
  const gatewayLikeServices = services.includes('http') || services.includes('https');
  return isGatewayLikeIp && genericLike && gatewayLikeServices;
}

function deviceCard(d) {
  const iconMap = {
    philips_hue:   'bi-lightbulb',
    sonos:         'bi-speaker',
    lifx:          'bi-lightbulb-fill',
    chromecast:    'bi-cast',
    mqtt:          'bi-diagram-3',
    generic_http:  'bi-hdd-network',
    generic:       'bi-cpu',
    generic_camera:'bi-camera-video',
    homekit:       'bi-house-door',
    home_assistant:'bi-house-heart',
  };
  const icon = iconMap[d.plugin_id] || 'bi-cpu';
  const services = (d.services || []).slice(0, 3).join(', ');
  const hasDistinctHostname = !!d.hostname && d.hostname !== d.ip;
  const title = hasDistinctHostname ? d.hostname : (d.vendor || d.device_type || 'Unknown device');

  return `
    <div class="col-6 col-sm-4 col-md-3 col-xl-2">
      <div class="device-card p-3 h-100"
           data-device-ip="${escHtml(d.ip)}"
           data-device-type="${escHtml(d.device_type)}">
        <div class="device-icon mb-2"><i class="bi ${icon}"></i></div>
        <div class="fw-semibold small text-truncate" title="${escHtml(title)}">
          ${escHtml(title)}
        </div>
        <div class="text-muted" style="font-size:0.72rem">${escHtml(d.ip)}</div>
        <div class="mt-1">
          <span class="badge bg-indigo device-type-badge"
                style="background:#6366f1!important">
            ${escHtml(d.device_type)}
          </span>
        </div>
        ${d.vendor ? `<div class="text-muted mt-1" style="font-size:0.7rem">${escHtml(d.vendor)}</div>` : ''}
        ${services ? `<div class="text-muted mt-1" style="font-size:0.68rem">${escHtml(services)}</div>` : ''}
      </div>
    </div>`;
}

// ── Device control modal ──────────────────────────────────────

async function openDeviceModal(ip, deviceType) {
  currentDeviceIp = ip;
  document.getElementById('device-modal-title').innerHTML =
    `<i class="bi bi-cpu"></i> ${escHtml(deviceType)} · ${escHtml(ip)}`;
  const body = document.getElementById('device-modal-body');
  body.innerHTML = 'Loading…';
  new bootstrap.Modal(document.getElementById('deviceModal')).show();

  try {
    const [stateR, capsR] = await Promise.all([
      fetch(`${API}/api/devices/${encodeURIComponent(ip)}/state`),
      fetch(`${API}/api/devices/${encodeURIComponent(ip)}/capabilities`),
    ]);
    const state = await stateR.json();
    const caps  = await capsR.json();
    currentCapabilities = caps;
    renderDeviceModal(ip, state, caps);
  } catch (e) {
    body.innerHTML = `<p class="text-danger">Error: ${escHtml(e.message)}</p>`;
  }
}

function renderDeviceModal(ip, state, caps) {
  const capButtons = caps.map((c, i) => `
    <button class="btn btn-outline-info btn-sm capability-btn me-2 mb-2"
            data-cap-index="${i}">
      ${escHtml(c.command)}
    </button>`).join('');

  document.getElementById('device-modal-body').innerHTML = `
    <h6 class="text-muted">State</h6>
    <pre class="state-json">${escHtml(JSON.stringify(state, null, 2))}</pre>

    <h6 class="text-muted mt-3">Commands</h6>
    <div id="cap-buttons">${capButtons || '<span class="text-muted">No commands available.</span>'}</div>

    <div id="command-form-area" class="mt-3"></div>
    <div id="command-result" class="mt-2"></div>`;

  // Event delegation for capability buttons
  document.getElementById('cap-buttons').addEventListener('click', (e) => {
    const btn = e.target.closest('button[data-cap-index]');
    if (btn) openCommandForm(parseInt(btn.dataset.capIndex, 10));
  });
}

function openCommandForm(capIndex) {
  const cap = currentCapabilities[capIndex];
  if (!cap) return;
  const params = cap.params || [];

  const inputs = params.map(p => `
    <div class="mb-2">
      <label class="form-label small text-muted">${escHtml(p)}</label>
      <input type="text" class="form-control bg-dark text-light border-secondary form-control-sm"
             data-param="${escHtml(p)}" placeholder="${escHtml(p)}"/>
    </div>`).join('');

  const area = document.getElementById('command-form-area');
  area.innerHTML = `
    <div class="border border-secondary rounded p-3">
      <h6>${escHtml(cap.command)}</h6>
      <p class="text-muted small">${escHtml(cap.description || '')}</p>
      ${inputs}
      <button class="btn btn-primary btn-sm" id="run-cmd-btn">Run</button>
    </div>`;

  document.getElementById('run-cmd-btn').addEventListener('click', () => {
    const paramValues = {};
    area.querySelectorAll('input[data-param]').forEach(el => {
      paramValues[el.dataset.param] = el.value;
    });
    runCommand(cap.command, paramValues);
  });
}

async function runCommand(command, params) {
  const resultEl = document.getElementById('command-result');
  resultEl.innerHTML = 'Running…';
  try {
    const r = await fetch(
      `${API}/api/devices/${encodeURIComponent(currentDeviceIp)}/command`,
      {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({command, params}),
      }
    );
    const d = await r.json();
    resultEl.innerHTML = `
      <pre class="state-json">${escHtml(JSON.stringify(d, null, 2))}</pre>`;
  } catch (e) {
    resultEl.innerHTML = `<p class="text-danger">Error: ${escHtml(e.message)}</p>`;
  }
}

// ── Utilities ─────────────────────────────────────────────────

function escHtml(str) {
  return String(str ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}

// ── Init ──────────────────────────────────────────────────────

(async () => {
  await refreshWlanStatus();
  // Load cached devices (if any from a previous scan)
  try {
    const r = await fetch(`${API}/api/devices`);
    const devices = await r.json();
    if (devices.length) renderDevices(devices);
  } catch (_) {}
})();
