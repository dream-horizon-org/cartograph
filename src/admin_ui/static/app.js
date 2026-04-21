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
  const $from = document.getElementById('from-options');
  const $to = document.getElementById('to-options');
  if (!$from || !$to) return;

  const agentIds = state.agents.map(a => a.agent_id);
  const agentTypes = ['orchestrator', 'iterator', 'sme', 'resolver'];

  // From: real agent_ids + 'admin' pseudo-agent.
  $from.innerHTML = '';
  ['admin', ...agentIds].forEach(id => {
    const opt = document.createElement('option');
    opt.value = id;
    $from.appendChild(opt);
  });

  // To: agent_ids + 'admin' + agent_types (broadcast targets).
  $to.innerHTML = '';
  [...agentIds, 'admin', ...agentTypes].forEach(v => {
    const opt = document.createElement('option');
    opt.value = v;
    $to.appendChild(opt);
  });
}

function renderAgents() {
  if (state.agents.length === 0) {
    $agentList.innerHTML = '<li class="empty">No agents yet. Create one.</li>';
    return;
  }
  $agentList.innerHTML = '';
  state.agents.forEach(agent => {
    const li = document.createElement('li');
    li.dataset.agentId = agent.agent_id;
    if (agent.agent_id === state.selectedAgentId) li.classList.add('selected');
    li.innerHTML = `
      <div class="agent-id">${escapeHtml(agent.agent_id)}</div>
      <div class="agent-meta">
        <span class="agent-type">${agent.agent_type}</span>
        <span class="status status-${agent.status}">${agent.status}</span>
      </div>
    `;
    li.addEventListener('click', () => selectAgent(agent.agent_id));
    $agentList.appendChild(li);
  });
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
  if (name === 'comms') fetchCommunications();
}

// --- Communications list ---

async function fetchCommunications() {
  const params = new URLSearchParams();
  const f = commsState.filters;
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
        <span class="to">${escapeHtml(m.to_agent)}</span>
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
    $body.innerHTML = `
      <div class="detail-kv"><b>From</b><span>${escapeHtml(comm.from_agent)}</span></div>
      <div class="detail-kv"><b>To</b><span>${escapeHtml(comm.to_agent)}</span></div>
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
  // Generic fallback for consolidation / clarification (Phase 3)
  return `<pre>${escapeHtml(JSON.stringify(data, null, 2))}</pre>`;
}

document.getElementById('filter-apply').addEventListener('click', () => {
  commsState.filters = {
    from_agent: document.getElementById('filter-from').value.trim(),
    to_agent: document.getElementById('filter-to').value.trim(),
    from_agent_type: document.getElementById('filter-from-type').value,
    to_agent_type: document.getElementById('filter-to-type').value,
    type: document.getElementById('filter-type').value,
  };
  fetchCommunications();
});

document.getElementById('filter-reset').addEventListener('click', () => {
  ['filter-from', 'filter-to'].forEach(id =>
    document.getElementById(id).value = '');
  ['filter-from-type', 'filter-to-type', 'filter-type'].forEach(id =>
    document.getElementById(id).value = '');
  commsState.filters = {
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
  if (!type || !msg) return;
  try {
    const res = await fetch('/api/broadcast', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ to_agent_type: type, message: msg }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      alert(err.detail || `Broadcast failed: ${res.status}`);
      return;
    }
    document.getElementById('bcast-msg').value = '';
    $broadcastDialog.close();
    // Refresh comms view if currently active
    if (document.getElementById('comms-view').classList.contains('active')) {
      fetchCommunications();
    }
  } catch (e) {
    alert(`Broadcast failed: ${e.message}`);
  }
});
