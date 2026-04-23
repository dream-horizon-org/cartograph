const state = {
  agents: [],
  selectedAgentId: null,
  messages: [],
  hasMore: false,
  oldestTimestamp: null,
  newestTimestamp: null,
  pollInterval: null,
  agentRefreshInterval: null,
  loadingHistory: false,
};

const $agentList = document.getElementById('agent-list');
const $messages = document.getElementById('messages');
const $chatTitle = document.getElementById('chat-title');
const $chatSubtitle = document.getElementById('chat-subtitle');
const $chatForm = document.getElementById('chat-form');
const $chatInput = document.getElementById('chat-input');
const $sendBtn = $chatForm.querySelector('button');
const $refreshBtn = document.getElementById('refresh-btn');

async function fetchAgents() {
  try {
    const res = await fetch('/api/agents');
    const data = await res.json();
    state.agents = data.agents || [];
    renderAgents();
    populateAgentDatalists();
  } catch (e) {
    console.error('Failed to fetch agents:', e);
  }
}

/** Refresh the from/to typable-dropdown suggestion lists from /api/agents.
 *  <datalist> gives the native combobox behaviour: type freely OR pick
 *  an existing agent_id / agent_type.
 */
function populateAgentDatalists() {
  const $agent = document.getElementById('agent-options');
  const $from = document.getElementById('from-options');
  const $to = document.getElementById('to-options');
  if (!$from || !$to || !$agent) return;

  const agentIds = state.agents.map(a => a.agent_id);
  const agentTypes = ['orchestrator', 'iterator', 'sme', 'resolver'];
  const fill = (el, values) => {
    el.innerHTML = '';
    values.forEach(v => {
      const opt = document.createElement('option');
      opt.value = v;
      el.appendChild(opt);
    });
  };

  // Participant filter: any real agent_id + 'admin' (direction-agnostic).
  fill($agent, ['admin', ...agentIds]);
  // From: same set (directional sender).
  fill($from, ['admin', ...agentIds]);
  // To: agent_ids + 'admin' + agent_types (broadcast targets).
  fill($to, [...agentIds, 'admin', ...agentTypes]);
}

// Fixed type order so the sidebar is predictable even when agent counts
// fluctuate. Types not in this list are appended at the end.
const _AGENT_TYPE_ORDER = ['orchestrator', 'resolver', 'iterator', 'sme'];

/** Per-group search strings (persist across renders so typing doesn't
 *  clobber when fetchAgents re-runs on the 5s poll). */
const agentGroupSearch = Object.create(null);

function renderAgents() {
  if (state.agents.length === 0) {
    $agentList.innerHTML = '<li class="empty">No agents yet. Create one.</li>';
    return;
  }
  // Group agents by type.
  const groups = new Map();
  state.agents.forEach(a => {
    if (!groups.has(a.agent_type)) groups.set(a.agent_type, []);
    groups.get(a.agent_type).push(a);
  });
  const orderedTypes = [
    ..._AGENT_TYPE_ORDER.filter(t => groups.has(t)),
    ...[...groups.keys()].filter(t => !_AGENT_TYPE_ORDER.includes(t)),
  ];

  $agentList.innerHTML = '';
  orderedTypes.forEach(type => {
    const agents = groups.get(type);
    const search = (agentGroupSearch[type] || '').toLowerCase();
    const filtered = search
      ? agents.filter(a => a.agent_id.toLowerCase().includes(search))
      : agents;

    const now = new Date();
    const sleepingCount = agents.filter(
      a => a.sleep_until && new Date(a.sleep_until) > now
    ).length;
    const groupLi = document.createElement('li');
    groupLi.className = 'agent-group';
    // Group-level buttons use the agent_type path. The sleep tool refuses
    // agent_type='orchestrator' by design (never bulk-pause coordinators),
    // so we hide group buttons for orchestrator. Per-agent row buttons
    // still work on the orchestrator (they use agent_ids=[...]).
    const showBulk = type !== 'orchestrator';
    groupLi.innerHTML = `
      <header class="agent-group-header">
        <span class="group-title">${type}</span>
        <span class="group-count">${filtered.length}${
          search ? `/${agents.length}` : ''
        }${sleepingCount ? ` · 💤${sleepingCount}` : ''}</span>
        ${showBulk ? `
          <span class="group-actions">
            <button class="btn-sleep" data-type="${type}" title="Sleep all ${type}s">💤</button>
            <button class="btn-wake" data-type="${type}" title="Wake all ${type}s">⏰</button>
          </span>
        ` : ''}
      </header>
      <input type="search" class="group-search" data-type="${type}"
             placeholder="filter ${type}s…"
             value="${escapeHtml(agentGroupSearch[type] || '')}"
             autocomplete="off">
      <ul class="group-list"></ul>
    `;
    const $ul = groupLi.querySelector('.group-list');
    if (filtered.length === 0) {
      const empty = document.createElement('li');
      empty.className = 'empty';
      empty.textContent = search ? `No ${type} matches "${search}"` : `No ${type}s yet.`;
      $ul.appendChild(empty);
    } else {
      filtered.forEach(agent => _renderAgentRow(agent, $ul));
    }
    $agentList.appendChild(groupLi);
  });
  // Wire search inputs — each re-renders on input (debounced minimal).
  $agentList.querySelectorAll('.group-search').forEach(inp => {
    inp.addEventListener('input', () => {
      agentGroupSearch[inp.dataset.type] = inp.value;
      renderAgents();
      // Restore focus + caret after re-render (renderAgents recreates DOM).
      const next = $agentList.querySelector(
        `.group-search[data-type="${inp.dataset.type}"]`
      );
      if (next) {
        next.focus();
        next.setSelectionRange(next.value.length, next.value.length);
      }
    });
  });
  // Wire sleep/wake bulk buttons.
  $agentList.querySelectorAll('.btn-sleep').forEach(btn => {
    btn.addEventListener('click', () => openSleepDialog(btn.dataset.type));
  });
  $agentList.querySelectorAll('.btn-wake').forEach(btn => {
    btn.addEventListener('click', () => wakeAgentType(btn.dataset.type));
  });
}

function _renderAgentRow(agent, $ul) {
  const li = document.createElement('li');
  li.dataset.agentId = agent.agent_id;
  if (agent.agent_id === state.selectedAgentId) li.classList.add('selected');
  // isSleeping = column is set AND the deadline is still in the future.
  // Backend treats past sleep_until as already-awake; FE must agree.
  const isSleeping = !!(agent.sleep_until && new Date(agent.sleep_until) > new Date());
  const sleepTag = isSleeping
    ? `<span class="status status-sleep">💤 until ${new Date(agent.sleep_until).toLocaleString()}</span>`
    : '';
  const actionBtn = isSleeping
    ? `<button class="row-btn row-wake" title="Wake ${escapeHtml(agent.agent_id)}">⏰</button>`
    : `<button class="row-btn row-sleep" title="Sleep ${escapeHtml(agent.agent_id)}">💤</button>`;
  li.innerHTML = `
    <div class="agent-row-head">
      <div class="agent-id">${escapeHtml(agent.agent_id)}</div>
      ${actionBtn}
    </div>
    <div class="agent-meta">
      <span class="status status-${agent.status}">${agent.status}</span>
      ${sleepTag}
    </div>
  `;
  // Clicking the row selects the agent for chat; clicking the sleep/wake
  // button must NOT propagate into the row-level selection handler.
  li.addEventListener('click', () => selectAgent(agent.agent_id));
  const btn = li.querySelector('.row-btn');
  if (btn) {
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      if (isSleeping) wakeAgentIds([agent.agent_id]);
      else openSleepDialogForIds([agent.agent_id]);
    });
  }
  $ul.appendChild(li);
}

async function selectAgent(agentId) {
  if (state.pollInterval) clearInterval(state.pollInterval);

  state.selectedAgentId = agentId;
  state.messages = [];
  state.oldestTimestamp = null;
  state.newestTimestamp = null;

  renderAgents();
  $chatTitle.textContent = agentId;
  const agent = state.agents.find(a => a.agent_id === agentId);
  $chatSubtitle.textContent = agent ? `${agent.agent_type} · ${agent.status}` : '';

  $chatInput.disabled = false;
  $sendBtn.disabled = false;
  $chatInput.focus();

  await loadInitialMessages();
  startPolling();
}

async function loadInitialMessages() {
  $messages.innerHTML = '<p class="empty">Loading...</p>';
  try {
    const res = await fetch(`/api/chat/${encodeURIComponent(state.selectedAgentId)}`);
    const data = await res.json();
    state.messages = data.messages;
    state.hasMore = data.has_more;
    if (state.messages.length > 0) {
      state.oldestTimestamp = state.messages[0].created_at;
      state.newestTimestamp = state.messages[state.messages.length - 1].created_at;
    }
    renderMessages();
    scrollToBottom();
    ackAgentMessages();
  } catch (e) {
    console.error('Failed to load messages:', e);
    $messages.innerHTML = '<p class="empty">Failed to load messages.</p>';
  }
}

async function loadOlderMessages() {
  if (!state.hasMore || state.loadingHistory) return;
  state.loadingHistory = true;
  const prevScrollHeight = $messages.scrollHeight;

  try {
    const params = new URLSearchParams({
      before: state.oldestTimestamp,
      limit: 20,
    });
    const res = await fetch(`/api/chat/${encodeURIComponent(state.selectedAgentId)}?${params}`);
    const data = await res.json();
    if (data.messages.length > 0) {
      state.messages = [...data.messages, ...state.messages];
      state.oldestTimestamp = state.messages[0].created_at;
      state.hasMore = data.has_more;
      renderMessages();
      // Keep scroll position after prepending
      $messages.scrollTop = $messages.scrollHeight - prevScrollHeight;
    } else {
      state.hasMore = false;
    }
  } catch (e) {
    console.error('Failed to load older messages:', e);
  } finally {
    state.loadingHistory = false;
  }
}

async function pollNewMessages() {
  if (!state.selectedAgentId || !state.newestTimestamp) return;
  try {
    const params = new URLSearchParams({ after: state.newestTimestamp });
    const res = await fetch(`/api/chat/${encodeURIComponent(state.selectedAgentId)}/new?${params}`);
    const data = await res.json();
    if (data.messages.length > 0) {
      state.messages.push(...data.messages);
      state.newestTimestamp = data.messages[data.messages.length - 1].created_at;
      const wasAtBottom = isScrolledToBottom();
      renderMessages();
      if (wasAtBottom) scrollToBottom();
      ackAgentMessages();
    }
  } catch (e) {
    console.error('Polling error:', e);
  }
}

function startPolling() {
  state.pollInterval = setInterval(pollNewMessages, 2000);
}

async function ackAgentMessages() {
  const ids = state.messages
    .filter(m => m.from_agent === state.selectedAgentId && !m.acked_at)
    .map(m => m.id);
  if (ids.length === 0) return;
  try {
    await fetch(`/api/chat/${encodeURIComponent(state.selectedAgentId)}/ack`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ communication_ids: ids }),
    });
    state.messages.forEach(m => {
      if (ids.includes(m.id)) m.acked_at = new Date().toISOString();
    });
  } catch (e) {
    console.error('Ack failed:', e);
  }
}

function renderMessages() {
  if (state.messages.length === 0) {
    $messages.innerHTML = '<p class="empty">No messages yet. Start the conversation.</p>';
    return;
  }
  $messages.innerHTML = state.hasMore
    ? '<div class="loading-more">Scroll up for older messages</div>'
    : '';
  state.messages.forEach(msg => {
    const div = document.createElement('div');
    const fromAdmin = msg.from_agent === 'admin';
    div.className = `message from-${fromAdmin ? 'admin' : 'agent'}`;

    const body = document.createElement('div');
    body.className = 'message-body';
    if (fromAdmin) {
      // Admin messages: plain text, preserve line breaks
      body.textContent = msg.text;
    } else {
      // Agent messages: render as markdown
      body.innerHTML = renderMarkdown(msg.text);
      highlightCodeBlocks(body);
    }
    div.appendChild(body);

    const meta = document.createElement('div');
    meta.className = 'message-meta';
    meta.textContent = formatTime(msg.created_at);
    div.appendChild(meta);

    $messages.appendChild(div);
  });
}

function renderMarkdown(text) {
  if (!window.marked || !window.DOMPurify) {
    // Fallback if CDN didn't load
    return escapeHtml(text).replace(/\n/g, '<br>');
  }
  const html = marked.parse(text, {
    breaks: true,
    gfm: true,
  });
  return DOMPurify.sanitize(html);
}

function highlightCodeBlocks(container) {
  if (!window.hljs) return;
  container.querySelectorAll('pre code').forEach(block => {
    try {
      hljs.highlightElement(block);
    } catch (e) {
      // ignore highlight failures
    }
  });
}

function isScrolledToBottom() {
  return $messages.scrollHeight - $messages.scrollTop - $messages.clientHeight < 100;
}

function scrollToBottom() {
  $messages.scrollTop = $messages.scrollHeight;
}

function formatTime(iso) {
  const d = new Date(iso);
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

function escapeHtml(s) {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

$chatForm.addEventListener('submit', async (e) => {
  e.preventDefault();
  const text = $chatInput.value.trim();
  if (!text || !state.selectedAgentId) return;

  $sendBtn.disabled = true;
  try {
    const res = await fetch(`/api/chat/${encodeURIComponent(state.selectedAgentId)}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: text }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      alert(err.detail || 'Failed to send message');
      return;
    }
    const msg = await res.json();
    state.messages.push(msg);
    state.newestTimestamp = msg.created_at;
    $chatInput.value = '';
    renderMessages();
    scrollToBottom();
  } finally {
    $sendBtn.disabled = false;
    $chatInput.focus();
  }
});

$messages.addEventListener('scroll', () => {
  if ($messages.scrollTop < 50) {
    loadOlderMessages();
  }
});

$refreshBtn.addEventListener('click', fetchAgents);

fetchAgents();
state.agentRefreshInterval = setInterval(fetchAgents, 5000);


// ================================================================
// Tab switching + Communications panel + Broadcast (Phase 2.4)
// ================================================================

const commsState = {
  messages: [],
  hasMore: false,
  filters: {
    agent: '', agent_type: '',
    from_agent: '', to_agent: '',
    from_agent_type: '', to_agent_type: '',
    type: '',
  },
  selectedCommId: null,
};

document.querySelectorAll('.tab').forEach(btn => {
  btn.addEventListener('click', () => switchTab(btn.dataset.tab));
});

function switchTab(name) {
  document.querySelectorAll('.tab').forEach(b =>
    b.classList.toggle('active', b.dataset.tab === name));
  document.getElementById('chat-view').classList.toggle('active', name === 'chat');
  document.getElementById('comms-view').classList.toggle('active', name === 'comms');
  document.getElementById('graph-view').classList.toggle('active', name === 'graph');
  if (name === 'comms') fetchCommunications();
  if (name === 'graph') initOrRefreshGraph();
}

// --- Communications list ---

async function fetchCommunications() {
  const params = new URLSearchParams();
  const f = commsState.filters;
  if (f.agent) params.set('agent', f.agent);
  if (f.agent_type) params.set('agent_type', f.agent_type);
  if (f.from_agent) params.set('from_agent', f.from_agent);
  if (f.to_agent) params.set('to_agent', f.to_agent);
  if (f.from_agent_type) params.set('from_agent_type', f.from_agent_type);
  if (f.to_agent_type) params.set('to_agent_type', f.to_agent_type);
  if (f.type) params.set('type', f.type);
  params.set('limit', '100');
  try {
    const res = await fetch(`/api/communications?${params.toString()}`);
    const data = await res.json();
    commsState.messages = data.messages || [];
    commsState.hasMore = data.has_more;
    renderCommunications();
  } catch (e) {
    console.error('Failed to fetch communications:', e);
  }
}

/** Target label: for broadcasts, "all <type>" on the agent_type column;
 *  otherwise the specific agent_id. Falls back to "—" if both columns
 *  are somehow null. */
function commTargetLabel(m) {
  if (m.to_agent_type) return `all ${m.to_agent_type}s`;
  if (m.to_agent) return m.to_agent;
  return '—';
}

function renderCommunications() {
  const $list = document.getElementById('comms-items');
  const $count = document.getElementById('comms-count');
  $count.textContent = `${commsState.messages.length}${commsState.hasMore ? '+' : ''} rows`;
  if (commsState.messages.length === 0) {
    $list.innerHTML = '<li class="empty">No communications match these filters.</li>';
    return;
  }
  $list.innerHTML = '';
  commsState.messages.forEach(m => {
    const li = document.createElement('li');
    li.className = `comm comm-${m.type}`;
    if (m.id === commsState.selectedCommId) li.classList.add('selected');
    const excerpt = (m.text || '').substring(0, 120).replace(/\n/g, ' ');
    const state = (m.metadata && m.metadata.state_transition)
      ? ` <span class="pill">${m.metadata.state_transition.from} → ${m.metadata.state_transition.to}</span>`
      : '';
    li.innerHTML = `
      <div class="comm-head">
        <span class="type-pill type-${m.type}">${m.type}</span>
        <span class="from">${escapeHtml(m.from_agent)}</span>
        <span class="arrow">→</span>
        <span class="to">${escapeHtml(commTargetLabel(m))}</span>
        ${state}
        <span class="ts">${new Date(m.created_at).toLocaleString()}</span>
      </div>
      <div class="comm-body">${escapeHtml(excerpt)}${excerpt.length >= 120 ? '…' : ''}</div>
    `;
    li.addEventListener('click', () => selectCommunication(m));
    $list.appendChild(li);
  });
}

async function selectCommunication(comm) {
  commsState.selectedCommId = comm.id;
  renderCommunications();
  const $body = document.getElementById('detail-body');
  const $title = document.getElementById('detail-title');

  if (comm.type === 'chat' || comm.type === 'broadcast') {
    $title.textContent = `${comm.type} detail`;
    const toLabel = comm.type === 'broadcast' && comm.to_agent_type
      ? `all ${escapeHtml(comm.to_agent_type)}s  (broadcast target)`
      : escapeHtml(comm.to_agent || '—');
    $body.innerHTML = `
      <div class="detail-kv"><b>From</b><span>${escapeHtml(comm.from_agent)}</span></div>
      <div class="detail-kv"><b>To</b><span>${toLabel}</span></div>
      <div class="detail-kv"><b>Sent</b><span>${new Date(comm.created_at).toLocaleString()}</span></div>
      <div class="detail-kv"><b>Acked at</b><span>${comm.acked_at || '—'}</span></div>
      <hr>
      <div class="message agent-message">${renderMarkdown(comm.text || '')}</div>
    `;
    return;
  }

  if (!comm.source_id) {
    $title.textContent = `${comm.type} detail`;
    $body.innerHTML = `<p class="empty">No source entity linked.</p>`;
    return;
  }

  const endpoint = { task: 'task', consolidation: 'consolidation', clarification: 'clarification' }[comm.type];
  try {
    const res = await fetch(`/api/${endpoint}/${comm.source_id}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    $title.textContent = `${comm.type} ${comm.source_id.substring(0, 8)}`;
    $body.innerHTML = renderSourceEntity(comm.type, data);
  } catch (e) {
    $body.innerHTML = `<p class="empty">Failed to load ${comm.type} detail: ${e.message}</p>`;
  }
}

function renderSourceEntity(type, data) {
  if (type === 'task') {
    const t = data.task;
    const threadHtml = (data.thread || []).map(c => `
      <div class="thread-entry">
        <div class="thread-head">
          <b>${escapeHtml(c.from_agent)}</b> → <b>${escapeHtml(c.to_agent)}</b>
          ${c.metadata && c.metadata.state_transition
            ? `<span class="pill">${c.metadata.state_transition.from} → ${c.metadata.state_transition.to}</span>`
            : ''}
          <span class="ts">${new Date(c.created_at).toLocaleString()}</span>
        </div>
        <div class="thread-body">${renderMarkdown(c.text || '')}</div>
      </div>
    `).join('');
    return `
      <div class="detail-kv"><b>Status</b><span class="status-${t.status}">${t.status}</span></div>
      <div class="detail-kv"><b>Owner</b><span>${escapeHtml(t.owner_agent_id)}</span></div>
      <div class="detail-kv"><b>Worker</b><span>${escapeHtml(t.worker_agent_id)}</span></div>
      <div class="detail-kv"><b>Created</b><span>${new Date(t.created_at).toLocaleString()}</span></div>
      <div class="detail-kv"><b>Updated</b><span>${new Date(t.updated_at).toLocaleString()}</span></div>
      ${t.blocker_detail ? `<div class="detail-kv"><b>Blocker</b><span>${escapeHtml(t.blocker_detail)}</span></div>` : ''}
      <hr>
      <h3>Description</h3>
      <div class="message agent-message">${renderMarkdown(t.description || '')}</div>
      <hr>
      <h3>Thread (${data.thread.length})</h3>
      ${threadHtml || '<p class="empty">No thread yet.</p>'}
    `;
  }
  if (type === 'consolidation') return renderConsolidationDetail(data);
  if (type === 'clarification') return renderClarificationDetail(data);
  return `<pre>${escapeHtml(JSON.stringify(data, null, 2))}</pre>`;
}

function renderThread(thread) {
  return (thread || []).map(c => `
    <div class="thread-entry">
      <div class="thread-head">
        <b>${escapeHtml(c.from_agent)}</b> → <b>${escapeHtml(c.to_agent || c.to_agent_type || 'admin')}</b>
        ${c.metadata && c.metadata.state_transition
          ? `<span class="pill">${c.metadata.state_transition.from ?? '∅'} → ${c.metadata.state_transition.to}</span>`
          : ''}
        ${c.metadata && c.metadata.role ? `<span class="pill pill-role">${c.metadata.role}</span>` : ''}
        <span class="ts">${new Date(c.created_at).toLocaleString()}</span>
      </div>
      <div class="thread-body">${renderMarkdown(c.text || '')}</div>
    </div>
  `).join('');
}

function confidencePill(label, score) {
  if (score == null) return `<span class="conf-pill conf-null">${label}: —</span>`;
  const cls = score >= 0.85 ? 'conf-high' : score >= 0.5 ? 'conf-mid' : 'conf-low';
  return `<span class="conf-pill ${cls}">${label}: ${Number(score).toFixed(2)}</span>`;
}

function renderConsolidationDetail(data) {
  const c = data.consolidation;
  return `
    <div class="detail-kv"><b>Status</b><span class="status-${c.status}">${c.status}</span></div>
    <div class="detail-kv"><b>Type</b><span>${c.nomination_type}</span></div>
    <div class="detail-kv"><b>Agent A (nominator)</b><span>${escapeHtml(c.agent_a_id || '')}</span></div>
    <div class="detail-kv"><b>Agent B (nominated)</b><span>${escapeHtml(c.agent_b_id || '—')}</span></div>
    <div class="detail-kv"><b>Component A</b><span>${escapeHtml(c.component_a_id || '')}</span></div>
    <div class="detail-kv"><b>Component B</b><span>${escapeHtml(c.component_b_id || '—')}</span></div>
    <div class="detail-kv"><b>Confidence</b><span>
      ${confidencePill('A', c.a_conf_score)}
      ${confidencePill('B', c.b_conf_score)}
      ${confidencePill('R', c.r_conf_score)}
    </span></div>
    ${c.mutation_assigned_to ? `<div class="detail-kv"><b>Mutation POC</b><span>${escapeHtml(c.mutation_assigned_to)}</span></div>` : ''}
    <div class="detail-kv"><b>Created</b><span>${new Date(c.created_at).toLocaleString()}</span></div>
    <div class="detail-kv"><b>Updated</b><span>${new Date(c.updated_at).toLocaleString()}</span></div>
    <hr>
    <h3>Thread (${(data.thread || []).length})</h3>
    ${renderThread(data.thread) || '<p class="empty">No thread yet.</p>'}
  `;
}

function renderClarificationDetail(data) {
  const c = data.clarification;
  return `
    <div class="detail-kv"><b>Status</b><span class="status-${c.status}">${c.status}</span></div>
    <div class="detail-kv"><b>Asker</b><span>${escapeHtml(c.asker_agent_id || '')}</span></div>
    <div class="detail-kv"><b>Responder</b><span>${escapeHtml(c.responder_agent_id || '—')}</span></div>
    <div class="detail-kv"><b>Created</b><span>${new Date(c.created_at).toLocaleString()}</span></div>
    <div class="detail-kv"><b>Updated</b><span>${new Date(c.updated_at).toLocaleString()}</span></div>
    <hr>
    <h3>Thread (${(data.thread || []).length})</h3>
    ${renderThread(data.thread) || '<p class="empty">No thread yet.</p>'}
  `;
}

document.getElementById('filter-apply').addEventListener('click', () => {
  commsState.filters = {
    agent: document.getElementById('filter-agent').value.trim(),
    agent_type: document.getElementById('filter-agent-type').value,
    from_agent: document.getElementById('filter-from').value.trim(),
    to_agent: document.getElementById('filter-to').value.trim(),
    from_agent_type: document.getElementById('filter-from-type').value,
    to_agent_type: document.getElementById('filter-to-type').value,
    type: document.getElementById('filter-type').value,
  };
  fetchCommunications();
});

document.getElementById('filter-reset').addEventListener('click', () => {
  ['filter-agent', 'filter-from', 'filter-to'].forEach(id =>
    document.getElementById(id).value = '');
  ['filter-agent-type', 'filter-from-type', 'filter-to-type', 'filter-type'].forEach(id =>
    document.getElementById(id).value = '');
  commsState.filters = {
    agent: '', agent_type: '',
    from_agent: '', to_agent: '',
    from_agent_type: '', to_agent_type: '',
    type: '',
  };
  fetchCommunications();
});

document.getElementById('detail-close').addEventListener('click', () => {
  commsState.selectedCommId = null;
  document.getElementById('detail-title').textContent = 'Detail';
  document.getElementById('detail-body').innerHTML =
    '<p class="empty">Click a communication on the left to see its source entity.</p>';
  renderCommunications();
});

// --- Broadcast dialog ---

const $broadcastDialog = document.getElementById('broadcast-dialog');
document.getElementById('broadcast-btn').addEventListener('click', () => {
  $broadcastDialog.showModal();
});
document.getElementById('bcast-cancel').addEventListener('click', () => {
  $broadcastDialog.close();
});
document.getElementById('broadcast-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const type = document.getElementById('bcast-type').value;
  const msg = document.getElementById('bcast-msg').value.trim();
  const persistent = document.getElementById('bcast-persistent').checked;
  if (!type || !msg) return;
  try {
    const res = await fetch('/api/broadcast', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ to_agent_type: type, message: msg, persistent }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      alert(err.detail || `Broadcast failed: ${res.status}`);
      return;
    }
    document.getElementById('bcast-msg').value = '';
    document.getElementById('bcast-persistent').checked = false;
    $broadcastDialog.close();
    // Refresh comms view if currently active
    if (document.getElementById('comms-view').classList.contains('active')) {
      fetchCommunications();
    }
  } catch (e) {
    alert(`Broadcast failed: ${e.message}`);
  }
});


// =============================================================
// Sleep / wake (Phase 2.5)
// =============================================================

const $sleepDialog = document.getElementById('sleep-dialog');
/** Sleep dialog target state: either {type: 'sme'} for a group-level
 *  sleep or {ids: [agent_id, ...]} for individual agents. Never both. */
let _sleepTarget = null;

function openSleepDialog(agentType) {
  _sleepTarget = { type: agentType };
  document.getElementById('sleep-title').textContent = `Sleep all ${agentType}s`;
  document.getElementById('sleep-target-note').textContent =
    `Target: agent_type = ${agentType}. Orchestrator is always excluded from bulk.`;
  _resetSleepDialogFields();
  $sleepDialog.showModal();
}

function openSleepDialogForIds(agentIds) {
  _sleepTarget = { ids: agentIds };
  const label = agentIds.length === 1
    ? agentIds[0]
    : `${agentIds.length} agents (${agentIds.slice(0, 3).join(', ')}${agentIds.length > 3 ? ', …' : ''})`;
  document.getElementById('sleep-title').textContent =
    agentIds.length === 1 ? `Sleep ${agentIds[0]}` : `Sleep ${agentIds.length} agents`;
  document.getElementById('sleep-target-note').textContent = `Target: ${label}`;
  _resetSleepDialogFields();
  $sleepDialog.showModal();
}

function _resetSleepDialogFields() {
  document.getElementById('sleep-reason').value = '';
  document.getElementById('sleep-value').value = '1';
  document.getElementById('sleep-unit').value = '3600';  // hours
  _updateSleepPreview();
}

function _sleepDurationSeconds() {
  const v = parseFloat(document.getElementById('sleep-value').value);
  const u = parseInt(document.getElementById('sleep-unit').value, 10);
  if (!v || v <= 0 || !u) return 0;
  return Math.round(v * u);
}

function _updateSleepPreview() {
  const secs = _sleepDurationSeconds();
  const preview = document.getElementById('sleep-preview');
  if (!secs) {
    preview.textContent = 'Wakes at: —';
    return;
  }
  const wake = new Date(Date.now() + secs * 1000);
  preview.textContent = `Wakes at: ${wake.toLocaleString()}`;
}

document.getElementById('sleep-value').addEventListener('input', _updateSleepPreview);
document.getElementById('sleep-unit').addEventListener('change', _updateSleepPreview);

document.getElementById('sleep-cancel').addEventListener('click', () => {
  $sleepDialog.close();
});

document.getElementById('sleep-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const seconds = _sleepDurationSeconds();
  const reason = document.getElementById('sleep-reason').value.trim();
  if (!_sleepTarget || !seconds || !reason) return;
  const until = new Date(Date.now() + seconds * 1000).toISOString();
  const body = { until, reason };
  if (_sleepTarget.type) body.agent_type = _sleepTarget.type;
  if (_sleepTarget.ids) body.agent_ids = _sleepTarget.ids;
  try {
    const res = await fetch('/api/sleep', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      alert(err.detail || `Sleep failed: ${res.status}`);
      return;
    }
    const data = await res.json();
    $sleepDialog.close();
    fetchAgents();  // refresh counts / 💤 indicator
    console.log(`Slept ${data.count} agent(s) until ${data.sleep_until}`);
  } catch (e) {
    alert(`Sleep failed: ${e.message}`);
  }
});

async function wakeAgentType(agentType) {
  return _postWake({ agent_type: agentType });
}

async function wakeAgentIds(agentIds) {
  return _postWake({ agent_ids: agentIds });
}

async function _postWake(body) {
  try {
    const res = await fetch('/api/wake', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      alert(err.detail || `Wake failed: ${res.status}`);
      return;
    }
    const data = await res.json();
    fetchAgents();
    console.log(`Woke ${data.count} agent(s)`);
  } catch (e) {
    alert(`Wake failed: ${e.message}`);
  }
}

// ============ GRAPH VIEW (Phase 3.5) ============
//
// 3d-force-graph renders components as nodes and edges from the edges table.
// Node color is derived from the set of planes the component has attributions
// in — HSL blend when multiple. Hovering or clicking a node shows its
// component_doc_md (markdown) in the sidebar.

// Polished palette — Tailwind 400-range, cohesive on a dark background.
const PLANE_COLORS = {
  github:    '#4ade80',  // emerald
  deploy:    '#60a5fa',  // sky
  cloud:     '#fb7185',  // rose
  telemetry: '#c084fc',  // violet
  config:    '#fbbf24',  // amber
};
const NO_PLANE_COLOR = '#71717a';  // zinc

let graphInstance = null;
let graphLoadedOnce = false;
// Snapshot of the latest /api/graph response. Kept in module scope so
// click handlers for nodes + edges can resolve related rows (catalog,
// dangling, flows) without re-fetching.
let graphSnapshot = {nodes: [], edges: [], flows: [], nodeById: {}, edgeById: {}};
// Which component's detail tab is currently open. Used when switching
// tabs to keep scrolled state clean.
let activeDetailNodeId = null;
let activeDetailTab = 'doc';
// Light-of-sight highlighting state — set of edge ids to light up.
let litEdgeIds = new Set();

function blendColors(hexColors) {
  // Average RGB components. For 1 color, returns it unchanged; for N,
  // gives a balanced blend. Good enough for the legend — users still see
  // the exact plane set in the sidebar.
  if (!hexColors.length) return NO_PLANE_COLOR;
  if (hexColors.length === 1) return hexColors[0];
  const rgb = hexColors.map(h => [
    parseInt(h.slice(1, 3), 16),
    parseInt(h.slice(3, 5), 16),
    parseInt(h.slice(5, 7), 16),
  ]);
  const avg = [0, 1, 2].map(i =>
    Math.round(rgb.reduce((s, c) => s + c[i], 0) / rgb.length));
  return '#' + avg.map(v => v.toString(16).padStart(2, '0')).join('');
}

function nodeColor(planes) {
  const colors = (planes || []).map(p => PLANE_COLORS[p]).filter(Boolean);
  return blendColors(colors);
}

async function initOrRefreshGraph() {
  if (typeof ForceGraph3D !== 'function') {
    document.getElementById('graph-canvas').innerHTML =
      '<p class="empty" style="padding:20px">3d-force-graph CDN failed to load. Check network.</p>';
    return;
  }
  const res = await fetch('/api/graph');
  if (!res.ok) {
    document.getElementById('graph-canvas').innerHTML =
      `<p class="empty" style="padding:20px">Graph fetch failed: ${res.status}</p>`;
    return;
  }
  const data = await res.json();

  // Build lookup maps once per load — used by sidebar + LOS BFS.
  graphSnapshot = {
    nodes: data.nodes,
    edges: data.edges,
    flows: data.flows || [],
    nodeById: Object.fromEntries(data.nodes.map(n => [n.id, n])),
    edgeById: Object.fromEntries(data.edges.map(e => [e.id, e])),
  };

  // Live counts in sidebar. Edge count is only BOUND rows (those shown
  // as links in the 3d graph); catalog + dangling count separately.
  const boundCount   = data.edges.filter(e => e.kind === 'bound').length;
  const catalogCount = data.edges.filter(e => e.kind === 'catalog').length;
  const danglingCount= data.edges.filter(e => e.kind === 'dangling').length;
  document.getElementById('graph-comp-count').textContent = data.nodes.length;
  document.getElementById('graph-edge-count').textContent =
    `${boundCount} bound · ${catalogCount} catalog · ${danglingCount} dangling`;

  // Only bound edges render as 3d links. Catalog + dangling surface in
  // the sidebar when a node is clicked (a 3.11 follow-up could float
  // them as stubs, but that adds visual noise up front).
  const boundEdges = data.edges.filter(e => e.kind === 'bound');

  const gData = {
    nodes: data.nodes.map(n => ({
      id: n.id,
      name: n.display_name || n.canonical_name,
      canonical: n.canonical_name,
      doc: n.component_doc_md,
      slice: n.source_slice,
      type: n.component_type,
      planes: n.planes,
      color: nodeColor(n.planes),
    })),
    links: boundEdges.map(e => ({
      id: e.id,
      source: e.source_id,
      target: e.target_id,
      edge_type: e.edge_type,
      identifier: e.identifier,
      confidence: e.confidence,
    })),
  };

  const canvas = document.getElementById('graph-canvas');
  if (!graphInstance) {
    graphInstance = ForceGraph3D()(canvas)
      .backgroundColor('#0a0a0a')
      .nodeResolution(32)
      .nodeOpacity(0.92)
      .nodeLabel(n => `${n.name} (${n.type})`)
      .nodeColor(n => n.color)
      .nodeVal(n => 4 + (n.planes?.length || 0) * 1.5)
      .linkColor(l => litEdgeIds.has(l.id)
        ? 'rgba(251, 191, 36, 0.95)'  // amber when lit
        : 'rgba(168, 168, 168, 0.32)'
      )
      .linkWidth(l => litEdgeIds.has(l.id) ? 1.6 : 0.6)
      .linkOpacity(0.55)
      .linkDirectionalArrowLength(3)
      .linkDirectionalArrowRelPos(1)
      .linkDirectionalParticles(l => litEdgeIds.has(l.id) ? 4 : 0)
      .linkDirectionalParticleSpeed(0.008)
      .linkDirectionalParticleWidth(2)
      .linkLabel(l => edgeHoverLabel(l))
      .onNodeClick(n => showGraphNodeDetail(n))
      .onLinkClick(l => lightOfSight(l.id));
  }
  graphInstance.graphData(gData);

  // Resize the canvas to its container on first render (fresh tab).
  if (!graphLoadedOnce) {
    setTimeout(() => {
      graphInstance.width(canvas.clientWidth).height(canvas.clientHeight);
    }, 50);
    graphLoadedOnce = true;
  }
}

// ---------- Edge hover label (3-zone aware) ----------
//
// 3d-force-graph's built-in linkLabel is a plain tooltip — it doesn't
// know cursor position on the link. For the three-zone semantics we
// project the link's source/target positions to screen space and
// measure the cursor's fractional position along the projected line.
// Called from a mousemove listener; the label re-renders live.

let lastHoveredLink = null;
let lastZone = null;

function linkZoneForCursor(link, mouseX, mouseY) {
  // Returns 'source' | 'midpoint' | 'target' based on cursor position
  // along the link's 2D projection. Needs the 3d-force-graph camera
  // to project node positions. Returns null if link is missing coords.
  if (!link || !link.source || !link.target) return null;
  if (typeof graphInstance.camera !== 'function') return null;
  const camera = graphInstance.camera();
  const canvas = document.getElementById('graph-canvas');
  const w = canvas.clientWidth, h = canvas.clientHeight;
  // Project THREE.Vector3 → screen space.
  const project = (node) => {
    if (!window.THREE) return null;
    const v = new window.THREE.Vector3(node.x || 0, node.y || 0, node.z || 0);
    v.project(camera);
    return {x: (v.x + 1) * w / 2, y: (-v.y + 1) * h / 2};
  };
  const src = project(link.source);
  const tgt = project(link.target);
  if (!src || !tgt) return null;
  const dx = tgt.x - src.x, dy = tgt.y - src.y;
  const len2 = dx * dx + dy * dy;
  if (len2 < 1) return 'midpoint';
  const t = ((mouseX - src.x) * dx + (mouseY - src.y) * dy) / len2;
  if (t < 0.33) return 'source';
  if (t > 0.66) return 'target';
  return 'midpoint';
}

function edgeHoverLabel(link) {
  // Text shown in the library's built-in tooltip. Keep it concise —
  // the rich 3-zone detail lives in the sidebar via clicking.
  return `${link.edge_type}: ${link.identifier} (click for light-of-sight)`;
}

// ---------- Light-of-sight BFS ----------
//
// Click an edge → BFS outward through bindings + flows, lighting every
// reachable edge. Forward (from this outgoing to downstream) and
// backward (to the incoming(s) that trigger this outgoing). Capped at
// depth ~8 to keep pathological graphs sane.

function lightOfSight(startEdgeId) {
  const cap = 8;
  const visited = new Set([startEdgeId]);
  const frontier = [{edgeId: startEdgeId, depth: 0}];
  while (frontier.length) {
    const {edgeId, depth} = frontier.shift();
    if (depth >= cap) continue;
    const edge = graphSnapshot.edgeById[edgeId];
    if (!edge) continue;
    // Forward: find flows where this edge is the incoming; enqueue their outgoings.
    for (const f of graphSnapshot.flows) {
      if (f.incoming_edge_id === edgeId && !visited.has(f.outgoing_edge_id)) {
        visited.add(f.outgoing_edge_id);
        frontier.push({edgeId: f.outgoing_edge_id, depth: depth + 1});
      }
      // Backward: if this edge is an outgoing on some flow, enqueue the incoming.
      if (f.outgoing_edge_id === edgeId && !visited.has(f.incoming_edge_id)) {
        visited.add(f.incoming_edge_id);
        frontier.push({edgeId: f.incoming_edge_id, depth: depth + 1});
      }
    }
  }
  litEdgeIds = visited;
  // Force link style refresh — redraw using the same data.
  const current = graphInstance.graphData();
  graphInstance.graphData(current);
  // Auto-clear after 5s.
  setTimeout(() => {
    litEdgeIds = new Set();
    const cur = graphInstance.graphData();
    graphInstance.graphData(cur);
  }, 5000);
}

function showGraphNodeDetail(node) {
  const $doc = document.getElementById('graph-hover-doc');
  if (!node) {
    activeDetailNodeId = null;
    $doc.innerHTML = '<p class="empty">Click a component in the graph to inspect it.</p>';
    return;
  }
  activeDetailNodeId = node.id;
  // Reset tab to Doc on a fresh click; subsequent tab switches use
  // setGraphDetailTab (below).
  activeDetailTab = 'doc';
  renderGraphDetailPanel();
}

function renderGraphDetailPanel() {
  const $doc = document.getElementById('graph-hover-doc');
  if (!activeDetailNodeId) {
    $doc.innerHTML = '<p class="empty">Click a component in the graph to inspect it.</p>';
    return;
  }
  const node = graphSnapshot.nodeById[activeDetailNodeId];
  if (!node) {
    $doc.innerHTML = '<p class="empty">Component no longer in graph.</p>';
    return;
  }
  const planesArr = node.planes || [];
  const planes = planesArr.length
    ? planesArr.map(p => `<span class="plane-pill" style="background:${PLANE_COLORS[p] || NO_PLANE_COLOR}22;color:${PLANE_COLORS[p] || NO_PLANE_COLOR}">${p}</span>`).join('')
    : '<span class="plane-pill">no attributions</span>';

  // Edges + flows touching this component.
  const incoming_bound = graphSnapshot.edges.filter(
    e => e.kind === 'bound' && e.target_id === node.id
  );
  const incoming_catalog = graphSnapshot.edges.filter(
    e => e.kind === 'catalog' && e.target_id === node.id
  );
  const outgoing_bound = graphSnapshot.edges.filter(
    e => e.kind === 'bound' && e.source_id === node.id
  );
  const outgoing_dangling = graphSnapshot.edges.filter(
    e => e.kind === 'dangling' && e.source_id === node.id
  );
  const flows = graphSnapshot.flows.filter(f => f.component_id === node.id);

  const tabs = [
    {id: 'doc',       label: 'Doc'},
    {id: 'slice',     label: 'Slice'},
    {id: 'catalog',   label: `Catalog (${incoming_catalog.length})`},
    {id: 'in',        label: `Bindings in (${incoming_bound.length})`},
    {id: 'out',       label: `Bindings out (${outgoing_bound.length + outgoing_dangling.length})`},
    {id: 'flows',     label: `Flows (${flows.length})`},
  ];
  const tabHtml = tabs.map(t =>
    `<button class="detail-tab ${t.id === activeDetailTab ? 'active' : ''}"
             data-tab="${t.id}">${escapeHtml(t.label)}</button>`
  ).join('');

  let body = '';
  if (activeDetailTab === 'doc') {
    body = node.doc
      ? renderMarkdown(node.doc)
      : '<p class="empty">No doc written yet — SME will populate on next materialisation.</p>';
  } else if (activeDetailTab === 'slice') {
    const sliceHtml = renderSourceSlice(node.slice);
    body = sliceHtml || '<p class="empty">No source_slice — this component covers its whole source resource.</p>';
  } else if (activeDetailTab === 'catalog') {
    body = incoming_catalog.length
      ? renderEdgeList(incoming_catalog, {showSide: 'caller-side-unknown'})
      : '<p class="empty">No catalog rows. Applications/lambdas/external-services should declare exposed endpoints via upsert_edge_catalog.</p>';
  } else if (activeDetailTab === 'in') {
    body = incoming_bound.length
      ? renderEdgeList(incoming_bound, {showSide: 'caller'})
      : '<p class="empty">No inbound bindings from other components yet.</p>';
  } else if (activeDetailTab === 'out') {
    const merged = [...outgoing_bound, ...outgoing_dangling];
    body = merged.length
      ? renderEdgeList(merged, {showSide: 'callee'})
      : '<p class="empty">No outbound edges yet.</p>';
  } else if (activeDetailTab === 'flows') {
    body = flows.length ? renderFlowList(flows) : '<p class="empty">No flows recorded. During Edge Discovery, SMEs upsert_flow to link each incoming edge to its downstream outgoings.</p>';
  }

  $doc.innerHTML = `
    <div class="graph-node-head">
      <b>${escapeHtml(node.name)}</b>
      <div class="kv-row"><span>canonical</span><code>${escapeHtml(node.canonical)}</code></div>
      <div class="kv-row"><span>type</span><code>${escapeHtml(node.type)}</code></div>
      <div class="plane-pills">${planes}</div>
    </div>
    <div class="detail-tabs">${tabHtml}</div>
    <div class="detail-body">${body}</div>
  `;

  // Wire up tab clicks.
  $doc.querySelectorAll('.detail-tab').forEach(btn => {
    btn.addEventListener('click', () => {
      activeDetailTab = btn.dataset.tab;
      renderGraphDetailPanel();
    });
  });
}

function setGraphDetailTab(tabId) {
  activeDetailTab = tabId;
  renderGraphDetailPanel();
}

function renderEdgeList(edges, opts) {
  // opts.showSide: 'caller' → show "from: <component>"
  //                 'callee' → show "to: <component> | dangling"
  //                 'caller-side-unknown' → catalog, no caller known
  return edges.map(e => {
    let sideHtml = '';
    if (opts.showSide === 'caller') {
      const srcNode = graphSnapshot.nodeById[e.source_id];
      sideHtml = `<span class="edge-from">from ${escapeHtml(srcNode?.name || e.source_id.slice(0, 8))}</span>`;
    } else if (opts.showSide === 'callee') {
      if (e.target_id) {
        const tgtNode = graphSnapshot.nodeById[e.target_id];
        sideHtml = `<span class="edge-to">to ${escapeHtml(tgtNode?.name || e.target_id.slice(0, 8))}</span>`;
      } else {
        sideHtml = `<span class="edge-to dangling">dangling (to unresolved)</span>`;
      }
    } else if (opts.showSide === 'caller-side-unknown') {
      sideHtml = `<span class="edge-from catalog">exposed — no caller bound yet</span>`;
    }
    const conf = e.confidence != null ? ` · conf ${Number(e.confidence).toFixed(2)}` : '';
    return `
      <div class="edge-row" data-edge-id="${e.id}">
        <div class="edge-head">
          <span class="edge-type">${escapeHtml(e.edge_type)}</span>
          <code class="edge-identifier">${escapeHtml(e.identifier)}</code>
        </div>
        <div class="edge-sub">${sideHtml}${conf}</div>
      </div>
    `;
  }).join('');
}

function renderFlowList(flows) {
  // Group by incoming_edge_id to show fan-out structure.
  const byIncoming = {};
  for (const f of flows) {
    (byIncoming[f.incoming_edge_id] ||= []).push(f);
  }
  return Object.entries(byIncoming).map(([incomingId, rows]) => {
    const incoming = graphSnapshot.edgeById[incomingId];
    const outgoings = rows.map(r => graphSnapshot.edgeById[r.outgoing_edge_id]).filter(Boolean);
    const incHead = incoming
      ? `${escapeHtml(incoming.edge_type)} <code>${escapeHtml(incoming.identifier)}</code>`
      : `<em>missing edge ${incomingId.slice(0, 8)}</em>`;
    const outList = outgoings.map(o => {
      const tgt = o.target_id ? graphSnapshot.nodeById[o.target_id]?.name : 'dangling';
      return `<li>${escapeHtml(o.edge_type)} <code>${escapeHtml(o.identifier)}</code> <span class="edge-to">→ ${escapeHtml(tgt || 'dangling')}</span></li>`;
    }).join('');
    return `
      <div class="flow-block">
        <div class="flow-incoming">${incHead}</div>
        <ul class="flow-outgoings">${outList}</ul>
      </div>
    `;
  }).join('');
}

function renderSourceSlice(slice) {
  // slice is {resource_uuid: {plane, paths, files, manifests, workflows, ...}}
  // or null when the component covers its whole source resource.
  if (!slice || typeof slice !== 'object' || Object.keys(slice).length === 0) {
    return '';
  }
  const entries = Object.entries(slice).map(([resourceId, info]) => {
    const plane = info?.plane || 'unknown';
    const planeColor = PLANE_COLORS[plane] || NO_PLANE_COLOR;
    // known typed sub-keys we render first; anything else surfaces under "other"
    const KNOWN = ['paths','files','manifests','workflows','entry_points','k8s_workloads'];
    const rowsHtml = Object.entries(info || {})
      .filter(([k, v]) => k !== 'plane' && Array.isArray(v) && v.length)
      .sort((a, b) => (KNOWN.indexOf(a[0]) - KNOWN.indexOf(b[0])))
      .map(([k, v]) => `
        <div class="slice-row">
          <span class="slice-k">${escapeHtml(k)}</span>
          <span class="slice-v">${v.map(s => `<code>${escapeHtml(String(s))}</code>`).join(' ')}</span>
        </div>
      `).join('');
    return `
      <div class="slice-block">
        <div class="slice-head">
          <span class="plane-pill" style="background:${planeColor}22;color:${planeColor}">${plane}</span>
          <code class="slice-resource">${escapeHtml(resourceId.slice(0, 8))}…</code>
        </div>
        ${rowsHtml || '<p class="empty">(empty slice)</p>'}
      </div>
    `;
  }).join('');
  return `
    <hr>
    <h3>Source slice</h3>
    ${entries}
  `;
}

document.getElementById('graph-refresh')?.addEventListener('click', initOrRefreshGraph);

// Resize graph if window resizes and graph tab is visible.
window.addEventListener('resize', () => {
  if (graphInstance && document.getElementById('graph-view').classList.contains('active')) {
    const canvas = document.getElementById('graph-canvas');
    graphInstance.width(canvas.clientWidth).height(canvas.clientHeight);
  }
});

// ---------- 3-zone hover: custom DOM tooltip aware of cursor position
// along the hovered link. Updates live as the mouse moves. ----------

let hoverTooltip = null;

function ensureHoverTooltip() {
  if (hoverTooltip) return hoverTooltip;
  hoverTooltip = document.createElement('div');
  hoverTooltip.id = 'graph-edge-tooltip';
  hoverTooltip.style.display = 'none';
  document.body.appendChild(hoverTooltip);
  return hoverTooltip;
}

// Hook into the graph instance once it exists to capture hover state.
function wireEdgeHoverZones() {
  if (!graphInstance) return;
  const canvas = document.getElementById('graph-canvas');
  if (!canvas || canvas.dataset.hoverWired) return;
  canvas.dataset.hoverWired = '1';

  // Track the currently-hovered link via the library callback.
  graphInstance.onLinkHover(l => {
    lastHoveredLink = l;
    if (!l) {
      ensureHoverTooltip().style.display = 'none';
    }
  });

  // mousemove over the canvas → update tooltip with zone-specific view.
  canvas.addEventListener('mousemove', e => {
    const tt = ensureHoverTooltip();
    if (!lastHoveredLink) {
      tt.style.display = 'none';
      return;
    }
    const rect = canvas.getBoundingClientRect();
    const x = e.clientX - rect.left, y = e.clientY - rect.top;
    const zone = linkZoneForCursor(lastHoveredLink, x, y);
    if (!zone) {
      tt.style.display = 'none';
      return;
    }
    lastZone = zone;
    tt.innerHTML = renderEdgeZoneTooltip(lastHoveredLink, zone);
    tt.style.display = 'block';
    tt.style.left = (e.clientX + 12) + 'px';
    tt.style.top  = (e.clientY + 12) + 'px';
  });

  canvas.addEventListener('mouseleave', () => {
    ensureHoverTooltip().style.display = 'none';
  });
}

function renderEdgeZoneTooltip(link, zone) {
  // The hovered link is a BOUND edge (we only render bound in the 3d
  // graph). For each zone we show a different piece of context.
  const srcNode = graphSnapshot.nodeById[link.source?.id || link.source];
  const tgtNode = graphSnapshot.nodeById[link.target?.id || link.target];
  const header = `
    <div class="tt-head">
      <span class="tt-kind">${escapeHtml(link.edge_type)}</span>
      <code class="tt-ident">${escapeHtml(link.identifier)}</code>
    </div>
    <div class="tt-endpoints">
      ${escapeHtml(srcNode?.name || '?')} → ${escapeHtml(tgtNode?.name || '?')}
    </div>
  `;

  if (zone === 'source') {
    // Caller-internal: which of SOURCE's incoming edges trigger this outgoing?
    const feeders = graphSnapshot.flows
      .filter(f => f.component_id === (srcNode?.id) && f.outgoing_edge_id === link.id)
      .map(f => graphSnapshot.edgeById[f.incoming_edge_id])
      .filter(Boolean);
    const body = feeders.length
      ? feeders.map(fe => `<li>${escapeHtml(fe.edge_type)} <code>${escapeHtml(fe.identifier)}</code></li>`).join('')
      : '<li><em>no flow recorded from source incoming edges</em></li>';
    return `
      ${header}
      <div class="tt-zone">CALLER view — incoming edges of <b>${escapeHtml(srcNode?.name || '?')}</b> whose flows fire this outgoing</div>
      <ul class="tt-list">${body}</ul>
    `;
  }
  if (zone === 'target') {
    // Callee-internal: which of TARGET's outgoing edges fire when this incoming hits?
    const downstream = graphSnapshot.flows
      .filter(f => f.component_id === (tgtNode?.id) && f.incoming_edge_id === link.id)
      .map(f => graphSnapshot.edgeById[f.outgoing_edge_id])
      .filter(Boolean);
    const body = downstream.length
      ? downstream.map(de => `<li>${escapeHtml(de.edge_type)} <code>${escapeHtml(de.identifier)}</code></li>`).join('')
      : '<li><em>no flow recorded from this incoming edge</em></li>';
    return `
      ${header}
      <div class="tt-zone">CALLEE view — outgoing edges of <b>${escapeHtml(tgtNode?.name || '?')}</b> triggered by this incoming</div>
      <ul class="tt-list">${body}</ul>
    `;
  }
  // Midpoint: convergence at the target endpoint. Who else calls it?
  const siblings = graphSnapshot.edges.filter(e =>
    e.kind === 'bound'
    && e.target_id === (tgtNode?.id)
    && e.edge_type === link.edge_type
    && e.identifier === link.identifier
    && e.id !== link.id
  );
  const body = siblings.length
    ? siblings.map(s => {
        const src = graphSnapshot.nodeById[s.source_id];
        return `<li>from ${escapeHtml(src?.name || s.source_id.slice(0, 8))}</li>`;
      }).join('')
    : '<li><em>only you call this endpoint</em></li>';
  return `
    ${header}
    <div class="tt-zone">CONVERGENCE — others calling the same <code>${escapeHtml(tgtNode?.name)}</code> endpoint</div>
    <ul class="tt-list">${body}</ul>
  `;
}

// The graph instance is created inside initOrRefreshGraph. Attach the
// hover wiring after each (re)init — idempotent thanks to dataset guard.
const _origInit = initOrRefreshGraph;
// Intentionally NOT wrapping — hook at the end of init by extending it:
// we already do this because wireEdgeHoverZones is called at the end
// of initOrRefreshGraph below (via an event-driven re-attach). Instead
// let's just invoke it on each refresh by hooking into the refresh btn
// + first tab switch.
function _attachHoverOnNextFrame() {
  requestAnimationFrame(() => wireEdgeHoverZones());
}
document.getElementById('graph-refresh')?.addEventListener('click', _attachHoverOnNextFrame);
// Also wire when the Graph tab becomes active (first reveal).
document.querySelector('.tab[data-tab="graph"]')?.addEventListener('click', () => {
  setTimeout(wireEdgeHoverZones, 300);  // after initOrRefreshGraph resolves
});
