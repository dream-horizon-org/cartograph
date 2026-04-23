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

// Richer saturated jewel tones — Tailwind 600-range. Brighter chroma
// than the 500s but slightly darker lightness; reads as "rich + deep"
// against #050505 without losing vibrancy.
const PLANE_COLORS = {
  github:    '#0d9488',  // teal-600
  deploy:    '#2563eb',  // blue-600
  cloud:     '#db2777',  // pink-600
  telemetry: '#9333ea',  // purple-600
  config:    '#ea580c',  // orange-600 — more saturated than amber
};
const NO_PLANE_COLOR = '#1e293b';  // slate-800 — unattributed sinks deeper

let graphInstance = null;
let graphLoadedOnce = false;
// Per-type mesh factory — returns a fresh THREE.Mesh for each node so
// component_type is visually distinct. Loaded after three.js is ready.
// See makeNodeMesh() below; set on .nodeThreeObject(makeNodeMesh).
// Pending LOS timers — kept so a new click cancels an in-flight layered
// reveal from a prior click.
let _losTimers = [];
// Snapshot of the latest /api/graph response. Kept in module scope so
// click handlers for nodes + edges can resolve related rows (catalog,
// dangling, flows) without re-fetching.
let graphSnapshot = {nodes: [], edges: [], flows: [], nodeById: {}, edgeById: {}};
// Which component's detail tab is currently open. Used when switching
// tabs to keep scrolled state clean.
let activeDetailNodeId = null;
let activeDetailTab = 'doc';
// Two independent highlight sets:
//  litEdgeIds:       click-driven light-of-sight (amber + particles).
//                    Persists until user clicks empty space or another edge.
//  hoverLitEdgeIds:  current hover zone's corresponding edges (cyan).
//                    Transient — clears when mouse leaves the link.
// A link lit by BOTH renders as amber (LOS takes precedence visually).
let litEdgeIds = new Set();
let hoverLitEdgeIds = new Set();

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

// ---------- Per-type node mesh factory ----------
//
// Different component types render as different THREE geometries so
// shape communicates type at a glance, independent of plane color.
// Junctions are deliberately tiny (size 0.3) so they read as routing
// dots, not components.

function _sizeForNode(node) {
  if (node.isJunction) return 0.3;
  // Modest scaling with plane count so multi-plane nodes pop a bit.
  return 2 + (node.planes?.length || 0) * 0.7;
}

function makeNodeMesh(node) {
  const THREE = window.THREE;
  if (!THREE) return null;  // fallback → library default sphere
  const s = _sizeForNode(node);

  // Geometries tuned so every type reads as the same approximate
  // visual volume as a sphere of radius s. Pure math volume matching
  // isn't perceptual, so these are empirically bumped.
  let geom;
  switch (node.type) {
    case 'database':
      // Short cylinder; radius ≈ s, height ≈ 2s.
      geom = new THREE.CylinderGeometry(s * 1.05, s * 1.05, s * 2, 28);
      break;
    case 'cache':
      // Torus: outer radius ≈ s, tube radius ≈ 0.5s — fatter so it
      // visually matches the sphere's bulk.
      geom = new THREE.TorusGeometry(s, s * 0.5, 16, 32);
      break;
    case 'queue':
      // Cone: base ≈ 1.2s, height ≈ 2.2s.
      geom = new THREE.ConeGeometry(s * 1.2, s * 2.4, 24);
      break;
    case 'lambda':
      // Octahedron: bump to 1.5s so it's not dwarfed by spheres.
      geom = new THREE.OctahedronGeometry(s * 1.55);
      break;
    case 'cron':
      geom = new THREE.IcosahedronGeometry(s * 1.35);
      break;
    case 'external-service':
      geom = new THREE.TetrahedronGeometry(s * 1.75);
      break;
    case 'library':
      geom = new THREE.BoxGeometry(s * 1.7, s * 1.7, s * 1.7);
      break;
    case 'infrastructure':
      // Flat wide slab: meant to feel like a floor/cluster.
      geom = new THREE.BoxGeometry(s * 2.5, s * 0.7, s * 2.5);
      break;
    case 'junction':
      // Intentionally tiny — routing dot, not a component.
      geom = new THREE.TetrahedronGeometry(s);
      break;
    case 'application':
    default:
      geom = new THREE.SphereGeometry(s, 32, 32);
      break;
  }
  // Matte Lambert (no glassy/metallic feel). We add bright scene
  // lights separately so the nodes read as properly illuminated
  // solids, not translucent bubbles.
  const mat = new THREE.MeshLambertMaterial({
    color: node.color || '#cccccc',
    transparent: true,
    opacity: node.isJunction ? 0.55 : 0.95,
  });
  return new THREE.Mesh(geom, mat);
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

  // Transform once, then use the same view in both 3d-force-graph and
  // the sidebar. Prior bug: sidebar looked up the raw server rows and
  // referenced .name / .doc / .canonical which only exist on the
  // transformed shape → "undefined" everywhere.
  const transformedNodes = data.nodes.map(n => ({
    id: n.id,
    name: n.display_name || n.canonical_name,
    canonical: n.canonical_name,
    doc: n.component_doc_md,
    slice: n.source_slice,
    type: n.component_type,
    planes: n.planes,
    color: nodeColor(n.planes),
  }));
  graphSnapshot = {
    nodes: transformedNodes,
    edges: data.edges,
    flows: data.flows || [],
    nodeById: Object.fromEntries(transformedNodes.map(n => [n.id, n])),
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

  // Edge bundling: when N≥3 edges share (to_component_id, edge_type,
  // identifier) — i.e. many callers hit the same catalog endpoint —
  // route them through a virtual junction node so they visually
  // converge at one point and proceed as a single fat line into the
  // target. For N<3 we stick with curved parallel lines.
  const convergenceGroups = {};  // key → [edge rows]
  for (const e of boundEdges) {
    const key = `${e.target_id}|${e.edge_type}|${e.identifier}`;
    (convergenceGroups[key] ||= []).push(e);
  }

  const junctionNodes = [];
  const junctionOutLinks = [];       // virtual links junction→target
  const junctionOutById = {};        // junctionLinkId → {group, target}
  const bundledEdgeIds = new Set();  // edges routed via a junction

  for (const [key, group] of Object.entries(convergenceGroups)) {
    if (group.length < 3) continue;  // no bundle for pairs
    const junctionId = `__junction__${key}`;
    junctionNodes.push({
      id: junctionId,
      isJunction: true,
      name: `⌖ ${group.length} callers → ${group[0].edge_type} ${group[0].identifier}`,
      canonical: key,
      type: 'junction',
      planes: [],
      color: '#94a3b8',
      // Onengineink pins each junction at a FIXED OFFSET from its
      // target node each tick — so the junction is always "just
      // outside the target," regardless of where the force layout
      // puts anything. These two fields tell the pinner which
      // target and which callers to use.
      junctionTargetId: group[0].target_id,
      junctionCallerIds: group.map(e => e.source_id),
    });
    const sample = group[0];
    const outId = `__junction_out__${key}`;
    junctionOutLinks.push({
      id: outId,
      source: junctionId,
      target: sample.target_id,
      edge_type: sample.edge_type,
      identifier: sample.identifier,
      confidence: 1.0,
      curvature: 0,
      isJunctionOut: true,
      groupEdgeIds: group.map(e => e.id),  // the real edges that bundle here
    });
    junctionOutById[outId] = {
      groupEdgeIds: group.map(e => e.id),
      target_id: sample.target_id,
      edge_type: sample.edge_type,
      identifier: sample.identifier,
    };
    for (const e of group) bundledEdgeIds.add(e.id);
  }

  // Now lay out the remaining (non-bundled) edges — apply curvature
  // for same-pair duplicates so parallel rows still fan out cleanly.
  const pairOrder = {};
  const regularLinks = [];
  const bundledLinks = [];
  for (const e of boundEdges) {
    if (bundledEdgeIds.has(e.id)) {
      // Route this edge through its junction.
      const key = `${e.target_id}|${e.edge_type}|${e.identifier}`;
      const junctionId = `__junction__${key}`;
      const pairKey = [e.source_id, junctionId].sort().join('|');
      const i = (pairOrder[pairKey] = (pairOrder[pairKey] ?? -1) + 1);
      const curvature = i === 0 ? 0 : (i % 2 === 1 ? 1 : -1) * (0.2 * Math.ceil(i / 2));
      bundledLinks.push({
        id: e.id,
        source: e.source_id,
        target: junctionId,
        edge_type: e.edge_type,
        identifier: e.identifier,
        confidence: e.confidence,
        curvature,
        isJunctionIn: true,
      });
    } else {
      const pairKey = [e.source_id, e.target_id].sort().join('|');
      const i = (pairOrder[pairKey] = (pairOrder[pairKey] ?? -1) + 1);
      const curvature = i === 0 ? 0 : (i % 2 === 1 ? 1 : -1) * (0.25 * Math.ceil(i / 2));
      regularLinks.push({
        id: e.id,
        source: e.source_id,
        target: e.target_id,
        edge_type: e.edge_type,
        identifier: e.identifier,
        confidence: e.confidence,
        curvature,
      });
    }
  }

  const links = [...regularLinks, ...bundledLinks, ...junctionOutLinks];
  const allNodes = [...transformedNodes, ...junctionNodes];
  const gData = {nodes: allNodes, links};

  // Expose junction metadata to the rest of the module so hover on a
  // junction-out link can explain the whole bundle + light it up.
  graphSnapshot.junctionOutById = junctionOutById;
  graphSnapshot.bundledEdgeIds = bundledEdgeIds;

  const canvas = document.getElementById('graph-canvas');
  if (!graphInstance) {
    graphInstance = ForceGraph3D()(canvas)
      .backgroundColor('#050505')
      .nodeLabel(n => n.isJunction
        ? `${n.name}`
        : `${n.name} (${n.type})`
      )
      // Per-type THREE.Mesh replaces the default sphere so shape
      // communicates component_type. makeNodeMesh reads node.color
      // internally so .nodeColor() / .nodeVal() / .nodeOpacity() are
      // unused here.
      .nodeThreeObject(makeNodeMesh)
      .nodeThreeObjectExtend(false)
      .linkCurvature(l => l.curvature || 0)
      .linkColor(l => resolveLinkColor(l))
      .linkWidth(l => resolveLinkWidth(l))
      .linkOpacity(0.65)
      .linkDirectionalArrowLength(l => l.isJunctionIn ? 0 : 3)  // arrows only on final segment
      .linkDirectionalArrowRelPos(1)
      .linkDirectionalParticles(l => resolveLinkParticles(l))
      .linkDirectionalParticleSpeed(0.008)
      .linkDirectionalParticleWidth(2.5)
      .linkDirectionalParticleColor(() => '#fbbf24')
      .linkLabel(l => edgeHoverLabel(l))
      .onNodeClick(n => {
        if (n.isJunction) return;  // junctions aren't real components
        showGraphNodeDetail(n);
        hoverLitEdgeIds = new Set();
        refreshGraphVisuals();
      })
      .onLinkClick(l => lightOfSight(l))
      .onBackgroundClick(() => {
        _losTimers.forEach(t => clearTimeout(t));
        _losTimers = [];
        litEdgeIds = new Set();
        hoverLitEdgeIds = new Set();
        refreshGraphVisuals();
      });

    // Mild layout tuning. Only touch charge + link distance; leave
    // link strength and center force at library defaults. Previous
    // aggressive tuning (link.strength=0.8, center=0.06, charge=-60)
    // compressed everything into the origin → invisible graph.
    const chargeForce = graphInstance.d3Force('charge');
    if (chargeForce && typeof chargeForce.strength === 'function') {
      chargeForce.strength(-180);
    }
    const linkForce = graphInstance.d3Force('link');
    if (linkForce && typeof linkForce.distance === 'function') {
      linkForce.distance(l => {
        if (l.isJunctionOut) return 12;
        if (l.isJunctionIn)  return 40;
        return 55;
      });
    }

    // Junction pinning. Each tick, place each junction at a fixed
    // OFFSET of ~18 units from its real target component, in the
    // direction of the callers' centroid. This means:
    //   - Junction always "just outside" its target, visually close.
    //   - Trunk (junction → target) is always short, regardless of
    //     where callers end up.
    //   - Offset direction points toward callers so the fan-in geometry
    //     looks natural.
    // Positions are computed only when target + at least one caller
    // have converged; otherwise we leave the junction to d3-force for
    // this tick.
    const JUNCTION_OFFSET = 14;
    graphInstance.onEngineTick(() => {
      if (!graphSnapshot.nodeById) return;
      const gd = graphInstance.graphData();
      if (!gd || !gd.nodes) return;
      // Build a quick id → node lookup over the LIVE nodes (these have
      // x/y/z populated by the simulation).
      const byId = {};
      for (const n of gd.nodes) byId[n.id] = n;
      // Soft spherical containment: nodes beyond a radius get pulled
      // back toward origin, but nodes inside the radius feel nothing.
      // Unlike a per-tick axis pull (which doesn't decay with alpha
      // and eventually collapses the graph), this acts only at the
      // edge and lets charge/link forces arrange the interior
      // freely. Keeps the cluster roughly bounded without crushing it.
      const MAX_R = 110;
      for (const n of gd.nodes) {
        if (n.isJunction) continue;
        if (n.fx != null || n.fy != null || n.fz != null) continue;
        if (typeof n.x !== 'number') continue;
        const r = Math.sqrt(n.x * n.x + n.y * n.y + n.z * n.z);
        if (r <= MAX_R) continue;
        const pull = ((r - MAX_R) / r) * 0.05;
        n.vx = (n.vx || 0) - n.x * pull;
        n.vy = (n.vy || 0) - n.y * pull;
        n.vz = (n.vz || 0) - n.z * pull;
      }

      // Junction pinning (unchanged): place each junction at a fixed
      // offset from its target, in the direction of the callers'
      // centroid.
      for (const n of gd.nodes) {
        if (!n.isJunction) continue;
        const target = byId[n.junctionTargetId];
        if (!target || target.x == null) continue;
        let cx = 0, cy = 0, cz = 0, count = 0;
        for (const cid of (n.junctionCallerIds || [])) {
          const c = byId[cid];
          if (c && c.x != null) {
            cx += c.x; cy += c.y; cz += c.z; count += 1;
          }
        }
        if (count === 0) continue;
        cx /= count; cy /= count; cz /= count;
        const dx = cx - target.x;
        const dy = cy - target.y;
        const dz = cz - target.z;
        const len = Math.sqrt(dx * dx + dy * dy + dz * dz);
        if (len < 0.1) continue;
        n.fx = target.x + (dx / len) * JUNCTION_OFFSET;
        n.fy = target.y + (dy / len) * JUNCTION_OFFSET;
        n.fz = target.z + (dz / len) * JUNCTION_OFFSET;
      }
    });
    // Cursor-centric zoom. The library's default wheel zoom is
    // disabled so our manual handler owns all wheel events (no more
    // two-zoom-systems-fighting direction weirdness). Orbit target
    // stays stable (no lerp) so focus doesn't drift during repeated
    // scroll events — zoom is purely "move camera along ray through
    // cursor," not "pan + zoom."
    try {
      const controls = graphInstance.controls();
      if (controls) {
        controls.enableZoom = false;  // we own the wheel
      }
    } catch (e) { /* no-op */ }

    // Boost scene lighting so Lambert materials read brightly without
    // looking translucent. 3d-force-graph ships with a default
    // AmbientLight + DirectionalLight at modest intensity; we add a
    // stronger ambient + key + fill so colours pop on the dark bg.
    try {
      const THREE = window.THREE;
      const scene = (typeof graphInstance.scene === 'function')
        ? graphInstance.scene() : null;
      if (THREE && scene) {
        const ambient = new THREE.AmbientLight(0xffffff, 0.9);
        scene.add(ambient);
        const key = new THREE.DirectionalLight(0xffffff, 0.7);
        key.position.set(80, 120, 60);
        scene.add(key);
        const fill = new THREE.DirectionalLight(0xffffff, 0.35);
        fill.position.set(-120, -40, -80);
        scene.add(fill);
      }
    } catch (e) { /* no-op */ }

    canvas.addEventListener('wheel', (ev) => {
      if (!window.THREE || !graphInstance.camera) return;
      ev.preventDefault();
      const THREE = window.THREE;
      const camera = graphInstance.camera();
      const controlsObj = graphInstance.controls ? graphInstance.controls() : null;
      const target = (controlsObj && controlsObj.target)
        || new THREE.Vector3(0, 0, 0);

      // Project cursor to a world-space point at the current
      // camera→target distance. That's what we zoom toward / away from.
      const rect = canvas.getBoundingClientRect();
      const nx = ((ev.clientX - rect.left) / rect.width) * 2 - 1;
      const ny = -((ev.clientY - rect.top) / rect.height) * 2 + 1;
      const ray = new THREE.Raycaster();
      ray.setFromCamera({x: nx, y: ny}, camera);
      const dist = camera.position.distanceTo(target);
      const cursorWorld = ray.ray.at(dist, new THREE.Vector3());

      // Gentle exponential zoom. deltaY comes in many ranges depending
      // on input device:
      //   - mouse wheel: ±100 per tick
      //   - trackpad two-finger: 1-20, hundreds of events per gesture
      //   - pinch (ctrl+wheel on mac): 1-5
      // exp(delta * 0.0015) gives a smooth continuous zoom across all
      // of them. Clamp per-event to ±6% so a single event can't throw
      // the camera; rapid scrolls still compound smoothly.
      let factor = Math.exp(ev.deltaY * 0.0015);
      factor = Math.max(0.94, Math.min(1.06, factor));

      // Scale camera position relative to cursorWorld.
      // newPos = cursorWorld + (camera.position - cursorWorld) * factor
      const newPos = camera.position.clone()
        .sub(cursorWorld).multiplyScalar(factor).add(cursorWorld);

      // Avoid degenerate collapse: don't let camera get closer than
      // 5 units from target.
      if (newPos.distanceTo(target) > 5) {
        camera.position.copy(newPos);
      }

      if (controlsObj && typeof controlsObj.update === 'function') {
        controlsObj.update();
      }
    }, {passive: false});
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

function linkFraction(link, mouseX, mouseY) {
  // Return normalised cursor position along the projected link
  // (0 = source end, 1 = target end), or null if we can't compute.
  if (!link || !link.source || !link.target) return null;
  if (typeof graphInstance.camera !== 'function') return null;
  const camera = graphInstance.camera();
  const canvas = document.getElementById('graph-canvas');
  const w = canvas.clientWidth, h = canvas.clientHeight;
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
  if (len2 < 1) return 0.5;
  return Math.max(0, Math.min(1, ((mouseX - src.x) * dx + (mouseY - src.y) * dy) / len2));
}

function linkZoneForCursor(link, mouseX, mouseY) {
  // Zones:
  //   caller      : pre-junction / first half of edge → caller-side view
  //   target      : post-junction / second half of edge → callee-side view
  //   convergence : tiny space around the junction itself → show all
  //                 edges converging at that junction (bundle only)
  const t = linkFraction(link, mouseX, mouseY);
  if (t == null) return null;
  if (link.isJunctionIn) {
    // Whole in-segment is caller-side, but the junction end is the
    // convergence zone.
    return t > 0.85 ? 'convergence' : 'caller';
  }
  if (link.isJunctionOut) {
    // The junction sits at the START (source end) of this link.
    // A small zone near there = convergence; the rest = target-side.
    return t < 0.15 ? 'convergence' : 'target';
  }
  // Regular bound: split at 50%.
  return t < 0.5 ? 'caller' : 'target';
}

function edgeHoverLabel(link) {
  // Text shown in the library's built-in tooltip. Keep it concise —
  // the rich 3-zone detail lives in the sidebar via clicking.
  return `${link.edge_type}: ${link.identifier} (click for light-of-sight)`;
}

// ---------- Link visual resolvers ----------
//
// A link is "lit" if its own id is in the corresponding set OR if it's
// part of a bundle whose junction-out is lit. This makes the bundle
// behave as one unit when LOS or hover targets it.

function isLinkLit(link, set) {
  if (set.has(link.id)) return true;
  if (link.isJunctionIn) {
    // The bundled in-segment lights up if its junction-out is lit.
    const meta = graphSnapshot.junctionOutById || {};
    for (const out of Object.values(meta)) {
      if (out.groupEdgeIds.includes(link.id)) {
        const outId = `__junction_out__${out.target_id}|${out.edge_type}|${out.identifier}`;
        if (set.has(outId)) return true;
      }
    }
  }
  if (link.isJunctionOut) {
    // The junction-out segment lights up when ANY of its bundled
    // contributors lights up — gives a "the whole bundle is hot" feel.
    if (link.groupEdgeIds && link.groupEdgeIds.some(id => set.has(id))) return true;
  }
  return false;
}

function resolveLinkColor(l) {
  if (isLinkLit(l, litEdgeIds))      return 'rgba(251, 191, 36, 0.98)';   // LOS amber
  if (isLinkLit(l, hoverLitEdgeIds)) return 'rgba(34, 211, 238, 0.9)';    // hover cyan
  // Bundled in-segments + the trunk itself share a muted tone so the
  // bundle reads as one structure. Trunk NOT visually thicker or
  // brighter than contributors by default — user wanted it to match.
  if (l.isJunctionOut)               return 'rgba(148, 163, 184, 0.55)';
  if (l.isJunctionIn)                return 'rgba(148, 163, 184, 0.55)';
  return 'rgba(168, 168, 168, 0.45)';                                     // regular bound
}

function resolveLinkWidth(l) {
  // All edges (including junction trunks + contributors) share the
  // same base width. Lit states bump width uniformly.
  if (isLinkLit(l, litEdgeIds))      return 2.0;
  if (isLinkLit(l, hoverLitEdgeIds)) return 1.4;
  return 0.8;
}

function resolveLinkParticles(l) {
  if (!isLinkLit(l, litEdgeIds)) return 0;
  return 4;  // same density on trunk + contributors
}

// ---------- Visual refresh ----------
//
// 3d-force-graph's link accessor setters short-circuit when passed the
// SAME function reference (identity check). So
// `graphInstance.linkColor(graphInstance.linkColor())` is a no-op —
// which is exactly what was breaking hover+LOS glow.
//
// Fix: wrap each accessor in a fresh arrow function every call. New
// reference → library treats it as a real change → rebuilds link
// materials → our resolver reads the latest litEdgeIds/hoverLitEdgeIds.
function refreshGraphVisuals() {
  if (!graphInstance) return;
  graphInstance
    .linkColor(l => resolveLinkColor(l))
    .linkWidth(l => resolveLinkWidth(l))
    .linkDirectionalParticles(l => resolveLinkParticles(l));
}

// ---------- Light-of-sight BFS ----------
//
// Click an edge → BFS outward through bindings + flows, lighting every
// reachable edge. Forward (follow this outgoing's downstream flows) and
// backward (incoming(s) whose flows fire this outgoing). Capped at
// depth 8. Persists until user clicks empty space, another edge, or
// a node (in which case hover-glow clears but LOS stays).

// ---------- Catalog bridging helpers ----------
//
// A bound edge "A calls B at GET /x" fires B's catalog endpoint
// "B exposes GET /x". Flows on B typically reference the CATALOG row
// as their incoming_edge_id (that's how the mock + production SMEs
// record it — the catalog represents "this endpoint fired", independent
// of which caller triggered it). For LOS forward expansion to work,
// we treat these as equivalent incomings.

function effectiveFlowIncomingsForEdge(edgeId) {
  // Return [edgeId plus any catalog rows matching (to, type, identifier)]
  // so the BFS can find flows keyed on either.
  const e = graphSnapshot.edgeById[edgeId];
  if (!e || !e.to_component_id) return [edgeId];
  const out = [edgeId];
  for (const cat of graphSnapshot.edges) {
    if (cat.kind === 'catalog'
        && cat.to_component_id === e.to_component_id
        && cat.edge_type === e.edge_type
        && cat.identifier === e.identifier) {
      out.push(cat.id);
    }
  }
  return out;
}

function catalogEdgeFor(edge) {
  // If `edge` is a bound row, find its matching catalog; otherwise null.
  if (!edge || !edge.to_component_id || edge.kind === 'catalog') return null;
  return graphSnapshot.edges.find(c =>
    c.kind === 'catalog'
    && c.to_component_id === edge.to_component_id
    && c.edge_type === edge.edge_type
    && c.identifier === edge.identifier
  );
}

function lightOfSight(link) {
  // Cancel any in-flight reveal from a prior click.
  _losTimers.forEach(t => clearTimeout(t));
  _losTimers = [];
  litEdgeIds = new Set();
  refreshGraphVisuals();

  // Resolve seed edges (real edge rows). Bundle-aware: clicking trunk
  // OR any contributor seeds the whole bundle in layer 0.
  let seedEdges = [];
  const seedVisualIds = new Set();
  if (link.isJunctionOut && link.groupEdgeIds) {
    seedVisualIds.add(link.id);  // trunk lights too
    for (const id of link.groupEdgeIds) {
      const e = graphSnapshot.edgeById[id];
      if (e) { seedEdges.push(e); seedVisualIds.add(e.id); }
    }
  } else if (link.isJunctionIn) {
    const meta = graphSnapshot.junctionOutById || {};
    for (const [outId, out] of Object.entries(meta)) {
      if (out.groupEdgeIds.includes(link.id)) {
        seedVisualIds.add(outId);
        for (const id of out.groupEdgeIds) {
          const e = graphSnapshot.edgeById[id];
          if (e) { seedEdges.push(e); seedVisualIds.add(e.id); }
        }
        break;
      }
    }
    if (seedEdges.length === 0) {
      const e = graphSnapshot.edgeById[link.id];
      if (e) { seedEdges.push(e); seedVisualIds.add(e.id); }
    }
  } else {
    const e = graphSnapshot.edgeById[link.id];
    if (e) { seedEdges.push(e); seedVisualIds.add(e.id); }
  }

  // Forward BFS starting from DESTINATION of each seed edge.
  // For each edge e (acting as an incoming at dest=e.to_component_id):
  //   find flows on e.to_component_id where incoming matches e (or its
  //   catalog bridge). Each such flow's outgoing goes to the next layer.
  // Visited tracking:
  //   visitedPairs       : "component|incoming_edge_id" — skip if seen
  //   visitedOutgoing    : outgoing_edge_id already emitted
  // Prevents infinite loops; keeps layers strict.

  const layers = [[...seedVisualIds]];
  const visitedPairs = new Set();
  const visitedOutgoing = new Set();
  seedEdges.forEach(e => visitedOutgoing.add(e.id));

  let currentEdges = seedEdges;
  for (let depth = 1; depth < 100; depth++) {
    const nextOutgoingIds = new Set();
    for (const e of currentEdges) {
      const destComponentId = e.to_component_id;
      if (!destComponentId) continue;
      // Catalog-bridged incoming set for this edge at the destination.
      const flowIncomings = effectiveFlowIncomingsForEdge(e.id);
      // Record visited-pair for each bridged incoming so we don't
      // re-explore this (component, incoming) combo.
      let alreadySeen = false;
      for (const fi of flowIncomings) {
        const pairKey = destComponentId + '|' + fi;
        if (visitedPairs.has(pairKey)) { alreadySeen = true; break; }
      }
      if (alreadySeen) continue;
      for (const fi of flowIncomings) {
        visitedPairs.add(destComponentId + '|' + fi);
      }
      for (const f of graphSnapshot.flows) {
        if (f.component_id !== destComponentId) continue;
        if (!flowIncomings.includes(f.incoming_edge_id)) continue;
        if (visitedOutgoing.has(f.outgoing_edge_id)) continue;
        visitedOutgoing.add(f.outgoing_edge_id);
        nextOutgoingIds.add(f.outgoing_edge_id);
      }
    }
    if (nextOutgoingIds.size === 0) break;
    layers.push([...nextOutgoingIds]);
    currentEdges = [...nextOutgoingIds]
      .map(id => graphSnapshot.edgeById[id])
      .filter(Boolean);
  }

  // Stagger the reveal so the light propagates layer by layer.
  const STAGGER_MS = 220;
  const lit = new Set();
  layers.forEach((layer, i) => {
    const t = setTimeout(() => {
      layer.forEach(id => lit.add(id));
      litEdgeIds = new Set(lit);
      refreshGraphVisuals();
    }, i * STAGGER_MS);
    _losTimers.push(t);
  });
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

  // mousemove over the canvas → update tooltip AND hover-glow set with
  // zone-specific view.
  canvas.addEventListener('mousemove', e => {
    const tt = ensureHoverTooltip();
    if (!lastHoveredLink) {
      tt.style.display = 'none';
      if (hoverLitEdgeIds.size > 0) {
        hoverLitEdgeIds = new Set();
        refreshGraphVisuals();
      }
      return;
    }
    const rect = canvas.getBoundingClientRect();
    const x = e.clientX - rect.left, y = e.clientY - rect.top;
    const zone = linkZoneForCursor(lastHoveredLink, x, y);
    if (!zone) {
      tt.style.display = 'none';
      return;
    }
    // Compute which corresponding edges should glow + render the
    // tooltip from the same data. Both use the same resolver so the
    // tooltip and the canvas never drift out of sync.
    const {tooltipHtml, edgeIds} = hoverZoneResolve(lastHoveredLink, zone);
    lastZone = zone;
    tt.innerHTML = tooltipHtml;
    tt.style.display = 'block';
    tt.style.left = (e.clientX + 12) + 'px';
    tt.style.top  = (e.clientY + 12) + 'px';
    // Update hover-glow set only if it actually changed — avoids
    // redraw thrashing every mousemove frame.
    const newSet = new Set(edgeIds);
    if (newSet.size !== hoverLitEdgeIds.size
        || [...newSet].some(id => !hoverLitEdgeIds.has(id))) {
      hoverLitEdgeIds = newSet;
      refreshGraphVisuals();
    }
  });

  canvas.addEventListener('mouseleave', () => {
    ensureHoverTooltip().style.display = 'none';
    if (hoverLitEdgeIds.size > 0) {
      hoverLitEdgeIds = new Set();
      refreshGraphVisuals();
    }
  });
}

function resolveEdgeEndpoints(link) {
  // Always return the REAL component nodes, never junctions.
  // - Regular bound: source = caller, target = callee
  // - Junction-in: source = caller, target = real callee (looked up
  //   via the shared junction's out-link metadata)
  // - Junction-out: source = the "canonical" caller (first of the
  //   bundle — not a meaningful choice, but consistent), target = real callee
  const meta = graphSnapshot.junctionOutById || {};
  if (link.isJunctionIn) {
    const src = graphSnapshot.nodeById[link.source?.id || link.source];
    for (const out of Object.values(meta)) {
      if (out.groupEdgeIds.includes(link.id)) {
        const tgt = graphSnapshot.nodeById[out.target_id];
        return {srcNode: src, tgtNode: tgt};
      }
    }
    return {srcNode: src, tgtNode: null};
  }
  if (link.isJunctionOut) {
    const tgt = graphSnapshot.nodeById[link.target?.id || link.target];
    // Pick the first bundled edge's source as the canonical caller
    // for tooltip purposes; the bundle covers many.
    const firstEdge = graphSnapshot.edgeById[link.groupEdgeIds?.[0]];
    const src = firstEdge ? graphSnapshot.nodeById[firstEdge.source_id] : null;
    return {srcNode: src, tgtNode: tgt};
  }
  // Regular bound: look up directly.
  return {
    srcNode: graphSnapshot.nodeById[link.source?.id || link.source],
    tgtNode: graphSnapshot.nodeById[link.target?.id || link.target],
  };
}

function resolveUnderlyingEdge(link) {
  // Return the REAL (not synthetic) edge row this link corresponds to.
  // For regular bound + junction-in: the edge row itself.
  // For junction-out: the first bundled contributor (they all share
  //   target+type+identifier so flow resolution is equivalent).
  if (link.isJunctionOut) {
    return graphSnapshot.edgeById[link.groupEdgeIds?.[0]];
  }
  return graphSnapshot.edgeById[link.id];
}

function hoverZoneResolve(link, zone) {
  // Three zones:
  //   caller      → glow caller's incoming edges whose flows have this
  //                 edge as an outgoing.
  //   target      → glow callee's outgoing edges whose flows have this
  //                 edge as an incoming (catalog-bridged).
  //   convergence → at a junction: glow all edges arriving there
  //                 (contributors + trunk), regardless of flows.
  const {srcNode, tgtNode} = resolveEdgeEndpoints(link);
  const underlying = resolveUnderlyingEdge(link);
  const header = `
    <div class="tt-head">
      <span class="tt-kind">${escapeHtml(link.edge_type)}</span>
      <code class="tt-ident">${escapeHtml(link.identifier)}</code>
    </div>
    <div class="tt-endpoints">
      ${escapeHtml(srcNode?.name || '?')} → ${escapeHtml(tgtNode?.name || '?')}
    </div>
  `;

  // Convergence zone: only meaningful at a bundle junction. For regular
  // bound edges (no junction), we'd treat this as caller — but the
  // zone classifier doesn't emit 'convergence' for non-bundled links,
  // so we only handle it here for junction cases.
  if (zone === 'convergence') {
    const ids = new Set();
    if (link.isJunctionOut) {
      ids.add(link.id);  // trunk
      (link.groupEdgeIds || []).forEach(id => ids.add(id));
    } else if (link.isJunctionIn) {
      const meta = graphSnapshot.junctionOutById || {};
      for (const [outId, out] of Object.entries(meta)) {
        if (out.groupEdgeIds.includes(link.id)) {
          ids.add(outId);
          out.groupEdgeIds.forEach(id => ids.add(id));
          break;
        }
      }
    } else {
      ids.add(link.id);
    }
    const contribList = [...ids].map(id => {
      const e = graphSnapshot.edgeById[id];
      if (!e) return null;
      const src = graphSnapshot.nodeById[e.source_id];
      return src ? `<li>${escapeHtml(src.name)}</li>` : null;
    }).filter(Boolean).join('');
    return {
      tooltipHtml: `
        ${header}
        <div class="tt-zone">JUNCTION — ${ids.size} edge(s) converging here</div>
        <ul class="tt-list">${contribList || '<li><em>no contributors</em></li>'}</ul>
      `,
      edgeIds: [...ids],
    };
  }

  const edgeIds = new Set();

  if (zone === 'caller') {
    // Caller-side: glow all incoming edges of the CALLER whose flows
    // have this edge as an outgoing.
    edgeIds.add(link.id);
    const feederIds = [];
    if (srcNode && underlying) {
      for (const f of graphSnapshot.flows) {
        if (f.component_id === srcNode.id
            && f.outgoing_edge_id === underlying.id) {
          feederIds.push(f.incoming_edge_id);
        }
      }
    }
    // Bound feeders glow in canvas; catalog feeders only listed in tooltip.
    for (const fid of feederIds) {
      const fe = graphSnapshot.edgeById[fid];
      if (fe && fe.kind === 'bound') edgeIds.add(fid);
    }
    const feederList = feederIds
      .map(id => graphSnapshot.edgeById[id])
      .filter(Boolean)
      .map(fe => `<li>${escapeHtml(fe.edge_type)} <code>${escapeHtml(fe.identifier)}</code>${fe.kind === 'catalog' ? ' <em>(catalog)</em>' : ''}</li>`)
      .join('');
    return {
      tooltipHtml: `
        ${header}
        <div class="tt-zone">CALLER — incomings of <b>${escapeHtml(srcNode?.name || '?')}</b> firing this outgoing</div>
        <ul class="tt-list">${feederList || '<li><em>no flow recorded</em></li>'}</ul>
      `,
      edgeIds: [...edgeIds],
    };
  }

  // zone === 'target'
  // Glow all outgoing edges of the CALLEE whose flows have this edge
  // as an incoming (catalog-bridged).
  edgeIds.add(link.id);
  const downstreamIds = [];
  if (tgtNode && underlying) {
    const incomingCandidates = effectiveFlowIncomingsForEdge(underlying.id);
    for (const f of graphSnapshot.flows) {
      if (f.component_id === tgtNode.id
          && incomingCandidates.includes(f.incoming_edge_id)) {
        downstreamIds.push(f.outgoing_edge_id);
      }
    }
  }
  downstreamIds.forEach(id => edgeIds.add(id));
  const downstreamList = downstreamIds
    .map(id => graphSnapshot.edgeById[id])
    .filter(Boolean)
    .map(de => {
      const nextTgt = de.target_id ? graphSnapshot.nodeById[de.target_id] : null;
      return `<li>${escapeHtml(de.edge_type)} <code>${escapeHtml(de.identifier)}</code> → ${escapeHtml(nextTgt?.name || 'dangling')}</li>`;
    }).join('');
  return {
    tooltipHtml: `
      ${header}
      <div class="tt-zone">CALLEE — outgoings of <b>${escapeHtml(tgtNode?.name || '?')}</b> fired by this incoming</div>
      <ul class="tt-list">${downstreamList || '<li><em>no downstream flow</em></li>'}</ul>
    `,
    edgeIds: [...edgeIds],
  };
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
