/* HAPI Admin UI — agent-konfigurasjon
 *
 * Auth: passord lagres i sessionStorage etter form-login. Alle fetch sender
 * Basic-Authorization-header eksplisitt — vi unngår nettleserens HTTPBasic-
 * prompt fordi den blokkeres av enkelte enterprise-policies.
 */

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

const state = {
  agents: [],
  selected: null,
  models: [],
};

// ---- Auth ----

const AUTH_KEY = 'hapi-admin-auth';

function setAuth(user, pass) {
  sessionStorage.setItem(AUTH_KEY, btoa(`${user}:${pass}`));
}
function getAuthHeader() {
  const v = sessionStorage.getItem(AUTH_KEY);
  return v ? `Basic ${v}` : null;
}
function clearAuth() {
  sessionStorage.removeItem(AUTH_KEY);
}

// ---- API ----

async function api(path, opts = {}) {
  const auth = getAuthHeader();
  const res = await fetch(`/admin${path}`, {
    headers: {
      'Content-Type': 'application/json',
      ...(auth ? { 'Authorization': auth } : {}),
      ...(opts.headers || {}),
    },
    ...opts,
  });
  if (res.status === 401) {
    clearAuth();
    showLogin('Sesjonen er utløpt. Logg inn på nytt.');
    throw new Error('401 Unauthorized');
  }
  if (!res.ok) {
    const text = await res.text().catch(() => '');
    throw new Error(`${res.status} ${res.statusText}: ${text}`);
  }
  return res.headers.get('content-type')?.includes('json') ? res.json() : res.text();
}

// ---- Render ----

function pill(text, kind = '') {
  const span = document.createElement('span');
  span.className = `pill ${kind}`;
  span.textContent = text;
  return span;
}

function renderAgentList() {
  const ul = $('#agents');
  ul.innerHTML = '';
  for (const a of state.agents) {
    const li = document.createElement('li');
    li.dataset.name = a.agent_name;
    if (state.selected === a.agent_name) li.classList.add('active');

    const name = document.createElement('div');
    name.className = 'name';
    name.textContent = a.label;
    li.appendChild(name);

    const desc = document.createElement('div');
    desc.className = 'desc';
    desc.textContent = a.description;
    li.appendChild(desc);

    const meta = document.createElement('div');
    meta.className = 'meta';
    if (a.model) meta.appendChild(pill(a.model));
    if (a.sync_status === 'ok') meta.appendChild(pill('synket', 'ok'));
    else if (a.sync_status === 'not-seeded') meta.appendChild(pill('ikke seedet', 'warn'));
    else if (a.sync_status === 'pending') meta.appendChild(pill('venter sync', 'warn'));
    else if (a.sync_status?.startsWith('error')) meta.appendChild(pill('sync feilet', 'err'));
    else if (a.sync_status === 'skipped') meta.appendChild(pill('lokal', 'ok'));
    li.appendChild(meta);

    li.addEventListener('click', () => selectAgent(a.agent_name));
    ul.appendChild(li);
  }
}

async function selectAgent(name) {
  state.selected = name;
  renderAgentList();
  const editor = $('#editor');
  editor.innerHTML = '<div class="empty">Laster…</div>';

  try {
    const [agent, history] = await Promise.all([
      api(`/api/agents/${encodeURIComponent(name)}`),
      api(`/api/agents/${encodeURIComponent(name)}/history?limit=30`),
    ]);
    renderEditor(agent, history);
  } catch (e) {
    editor.innerHTML = `<div class="status err">Kunne ikke laste agent: ${escapeHtml(e.message)}</div>`;
  }
}

function escapeHtml(s) {
  return String(s ?? '')
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function renderEditor(agent, history) {
  const editor = $('#editor');
  const syncBadge = (() => {
    if (!agent.sync_status || agent.sync_status === 'ok') return pill('synket til Foundry', 'ok');
    if (agent.sync_status === 'not-seeded') return pill('ikke seedet — kjør seed_agentconfig.py', 'warn');
    if (agent.sync_status === 'skipped') return pill('lokal (ingen Foundry-sync)', 'ok');
    return pill(`sync-status: ${agent.sync_status}`, 'err');
  })();

  editor.innerHTML = `
    <h2>${escapeHtml(agent.label)}-agent</h2>
    <p class="sub">${escapeHtml(agent.description)} · <code>${escapeHtml(agent.agent_name)}</code></p>
    <div class="status info" id="meta-line">
      Sist endret: ${escapeHtml(agent.updated_at || '–')}
      ${agent.updated_by ? ` av ${escapeHtml(agent.updated_by)}` : ''}
    </div>

    <div class="field">
      <label for="model-select">Språkmodell</label>
      <select id="model-select"></select>
    </div>

    <div class="field">
      <label for="prompt">Prompt-instruksjoner</label>
      <textarea id="prompt" spellcheck="false">${escapeHtml(agent.prompt)}</textarea>
    </div>

    <div class="actions">
      <button class="primary" id="save-btn">Lagre</button>
      <button class="secondary" id="test-btn">Test...</button>
      ${(agent.sync_status?.startsWith('error') || agent.sync_status === 'pending')
        ? '<button class="warn" id="resync-btn">Re-sync til Foundry</button>'
        : ''}
    </div>

    <div id="save-status" class="status"></div>

    <h3>Versjonshistorikk</h3>
    ${history.length === 0
      ? '<p class="sub">Ingen tidligere versjoner enda.</p>'
      : `<table class="history">
          <thead><tr>
            <th>Endret</th><th>Av</th><th>Modell</th><th>Prompt-start</th><th></th>
          </tr></thead>
          <tbody>
            ${history.map(h => `
              <tr>
                <td>${escapeHtml(h.updated_at)}</td>
                <td>${escapeHtml(h.updated_by)}</td>
                <td><code>${escapeHtml(h.model)}</code></td>
                <td title="${escapeHtml(h.prompt)}">${escapeHtml((h.prompt || '').slice(0, 60))}…</td>
                <td><button class="secondary" data-rk="${escapeHtml(h.row_key)}">Rull tilbake</button></td>
              </tr>
            `).join('')}
          </tbody>
        </table>`}
  `;

  // Sync-status linje (overskrev info i meta-line ved behov)
  const meta = $('#meta-line');
  meta.appendChild(document.createTextNode(' · '));
  meta.appendChild(syncBadge);

  // Fyll modell-dropdown
  fillModelSelect(agent.model);

  // Bindings
  $('#save-btn').addEventListener('click', () => saveAgent(agent.agent_name));
  $('#test-btn').addEventListener('click', () => openTestModal(agent.agent_name));
  const resync = $('#resync-btn');
  if (resync) resync.addEventListener('click', () => resyncAgent(agent.agent_name));

  $$('#editor table.history button').forEach(b => {
    b.addEventListener('click', () => restoreHistory(agent.agent_name, b.dataset.rk));
  });
}

async function fillModelSelect(currentModel) {
  const sel = $('#model-select');
  sel.innerHTML = '<option>laster…</option>';
  if (!state.models.length) {
    try {
      state.models = await api('/api/models');
    } catch {
      state.models = [];
    }
  }
  const models = state.models.length ? state.models : [];
  // Sørg for at gjeldende modell alltid er med — også hvis listen ikke kjenner den.
  const all = new Set(models);
  if (currentModel) all.add(currentModel);
  sel.innerHTML = '';
  for (const m of Array.from(all).sort()) {
    const opt = document.createElement('option');
    opt.value = m;
    opt.textContent = m;
    if (m === currentModel) opt.selected = true;
    sel.appendChild(opt);
  }
}

// ---- Mutations ----

function showStatus(id, text, kind = 'info') {
  const el = $(id);
  if (!el) return;
  el.className = `status ${kind}`;
  el.textContent = text;
}

async function saveAgent(name) {
  const prompt = $('#prompt').value;
  const model = $('#model-select').value;
  if (!prompt.trim()) {
    showStatus('#save-status', 'Prompt kan ikke være tom.', 'err');
    return;
  }
  if (!confirm(`Lagre endringer for ${name}?\nDette synces til Foundry og påvirker neste produksjons-spørring.`)) {
    return;
  }
  $('#save-btn').disabled = true;
  showStatus('#save-status', 'Lagrer + synker til Foundry…', 'info');
  try {
    const result = await api(`/api/agents/${encodeURIComponent(name)}`, {
      method: 'POST',
      body: JSON.stringify({ prompt, model }),
    });
    const ok = result.sync_status === 'ok' || result.sync_status === 'skipped';
    showStatus('#save-status', ok ? `Lagret (${result.sync_status}).` : `Lagret lokalt, men sync feilet: ${result.sync_status}`, ok ? 'ok' : 'err');
    await loadAgents();
    selectAgent(name);
  } catch (e) {
    showStatus('#save-status', `Feil: ${e.message}`, 'err');
  } finally {
    const btn = $('#save-btn');
    if (btn) btn.disabled = false;
  }
}

async function resyncAgent(name) {
  showStatus('#save-status', 'Re-syncer til Foundry…', 'info');
  try {
    const result = await api(`/api/agents/${encodeURIComponent(name)}/resync`, { method: 'POST' });
    const ok = result.sync_status === 'ok';
    showStatus('#save-status', ok ? 'Re-sync ok.' : `Re-sync feilet: ${result.sync_status}`, ok ? 'ok' : 'err');
    await loadAgents();
    selectAgent(name);
  } catch (e) {
    showStatus('#save-status', `Feil: ${e.message}`, 'err');
  }
}

async function restoreHistory(name, rowKey) {
  if (!confirm(`Rulle tilbake til versjon ${rowKey}?\nDette synces til Foundry.`)) return;
  showStatus('#save-status', 'Ruller tilbake…', 'info');
  try {
    await api(`/api/agents/${encodeURIComponent(name)}/restore/${encodeURIComponent(rowKey)}`, { method: 'POST' });
    await loadAgents();
    selectAgent(name);
    showStatus('#save-status', 'Rullet tilbake.', 'ok');
  } catch (e) {
    showStatus('#save-status', `Feil: ${e.message}`, 'err');
  }
}

// ---- Test-modal ----

function openTestModal(name) {
  $('#test-modal-bg').classList.add('show');
  $('#test-query').value = '';
  $('#test-result').style.display = 'none';
  $('#test-status').className = 'status';
  $('#test-status').textContent = '';

  $('#run-test').onclick = () => runTest(name);
  $('#close-test').onclick = closeTestModal;
}
function closeTestModal() {
  $('#test-modal-bg').classList.remove('show');
}

async function runTest(name) {
  const query = $('#test-query').value.trim();
  if (!query) {
    showStatus('#test-status', 'Skriv et spørsmål.', 'err');
    return;
  }
  const prompt = $('#prompt').value;
  const model = $('#model-select').value;
  $('#run-test').disabled = true;
  showStatus('#test-status', 'Kjører…', 'info');
  try {
    const result = await api(`/api/agents/${encodeURIComponent(name)}/test`, {
      method: 'POST',
      body: JSON.stringify({ prompt, model, query }),
    });
    showStatus('#test-status', `OK (${result.duration_ms} ms, ${model})`, 'ok');
    const out = $('#test-result');
    out.textContent = result.answer;
    out.style.display = 'block';
  } catch (e) {
    showStatus('#test-status', `Feil: ${e.message}`, 'err');
  } finally {
    $('#run-test').disabled = false;
  }
}

// ---- Init / login ----

function showLogin(message) {
  $('#login-screen').style.display = 'flex';
  $('#dashboard').style.display = 'none';
  if (message) {
    showStatus('#login-status', message, 'err');
  }
  setTimeout(() => $('#login-pass').focus(), 50);
}

function showDashboard(user) {
  $('#login-screen').style.display = 'none';
  $('#dashboard').style.display = 'grid';
  $('#user-badge').textContent = user;
}

async function loadAgents() {
  try {
    state.agents = await api('/api/agents');
    renderAgentList();
    if (!state.selected && state.agents.length) {
      selectAgent(state.agents[0].agent_name);
    }
  } catch (e) {
    if (e.message?.startsWith('401')) return; // showLogin allerede kalt
    $('#dashboard').innerHTML = `<div class="empty">Kunne ikke laste agenter: ${escapeHtml(e.message)}</div>`;
  }
}

async function tryLogin(user, pass) {
  setAuth(user, pass);
  try {
    const r = await api('/api/login');
    showDashboard(r.user || user);
    await loadAgents();
    return true;
  } catch (e) {
    clearAuth();
    return false;
  }
}

(async function init() {
  $('#login-form').addEventListener('submit', async (ev) => {
    ev.preventDefault();
    const user = $('#login-user').value.trim();
    const pass = $('#login-pass').value;
    if (!user || !pass) {
      showStatus('#login-status', 'Fyll inn brukernavn og passord.', 'err');
      return;
    }
    $('#login-submit').disabled = true;
    showStatus('#login-status', 'Logger inn…', 'info');
    const ok = await tryLogin(user, pass);
    $('#login-submit').disabled = false;
    if (!ok) {
      showStatus('#login-status', 'Feil brukernavn eller passord.', 'err');
      $('#login-pass').value = '';
      $('#login-pass').focus();
    }
  });

  // Hvis sessionStorage allerede har gyldig auth, gå rett til dashboard.
  if (getAuthHeader()) {
    try {
      const r = await api('/api/login');
      showDashboard(r.user || 'admin');
      await loadAgents();
      return;
    } catch {
      clearAuth();
    }
  }
  showLogin();
})();
