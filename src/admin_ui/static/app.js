// ================================================================
// Phase 5.1: URL routing — History API + query-param preservation
// ================================================================
//
// All FE state changes that should be deep-linkable go through Router.navigate().
// Existing query params are preserved by default; opt-in to clear via
// {clear: ['key']} or {set: {key: undefined}}.
//
// URL scheme:
//   /                                       — default (chat tab)
//   /chat/:agent_id?q=&group=               — chat with agent
//   /communications?agent=&type=&...        — communications panel
//   /graph                                  — graph view
//   /graph/component/:id                    — component selected in graph
//   /entities[?type=&status=&...]           — entities list (Phase 5.2)
//   /entities/{kind}/:id                    — entity drill-down
//   /catalog[?type=&plane=&status=&...]     — components list (Phase 5.3)
//   /catalog/component/:id                  — component drill-down
//   /agent/:id/chain                        — merge lineage view
//   /broadcast/new                          — open broadcast dialog
//
// Filter / search state lives in query params so it survives navigation
// and is shareable. The invariant: navigate(path) preserves all existing
// query keys unless explicitly cleared/replaced via opts.set/opts.clear.

const Router = (() => {
  function buildUrl(path, set, clear) {
    const url = new URL(path, window.location.origin);
    const incoming = new URLSearchParams(window.location.search);
    // Preserve existing query unless the new path already specifies the key.
    incoming.forEach((v, k) => {
      if (!url.searchParams.has(k)) url.searchParams.set(k, v);
    });
    if (set) {
      Object.entries(set).forEach(([k, v]) => {
        if (v === null || v === undefined || v === '') url.searchParams.delete(k);
        else url.searchParams.set(k, String(v));
      });
    }
    if (clear) clear.forEach(k => url.searchParams.delete(k));
    const qs = url.searchParams.toString();
    return url.pathname + (qs ? '?' + qs : '');
  }

  function navigate(path, opts = {}) {
    const target = buildUrl(path, opts.set, opts.clear);
    const fullCurrent = window.location.pathname + window.location.search;
    if (target === fullCurrent) return; // no-op when URL is unchanged
    if (opts.replace) history.replaceState({}, '', target);
    else history.pushState({}, '', target);
    window.dispatchEvent(new CustomEvent('routechange'));
  }

  function current() {
    const path = window.location.pathname;
    const query = Object.fromEntries(new URLSearchParams(window.location.search));
    const segments = path.split('/').filter(Boolean);
    return { path, segments, query };
  }

  window.addEventListener('popstate', () => {
    window.dispatchEvent(new CustomEvent('routechange'));
  });

  return { navigate, current, buildUrl };
})();

// Recursion guard — when routechange triggers switchTab/selectAgent we
// don't want them to call Router.navigate again and bounce. The flag
// short-circuits the navigate call inside those mutators.
let _navigatingFromRoute = false;

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
  // Phase 4: when true, /api/agents?include_decommissioned=true. Flips
  // on a sidebar toggle so admins can audit merged/absorbed agents.
  showDecommissioned: false,
};

const $agentList = document.getElementById('agent-list');
const $messages = document.getElementById('messages');
const $chatTitle = document.getElementById('chat-title');
const $chatSubtitle = document.getElementById('chat-subtitle');
const $chatForm = document.getElementById('chat-form');
const $chatInput = document.getElementById('chat-input');
const $sendBtn = $chatForm.querySelector('button');
const $refreshBtn = document.getElementById('refresh-btn');

// Phase 7.4.9: parallel cache of ALL agents (active + decommissioned),
// keyed by agent_id. Used by Communications + Entities renderers so
// decommissioned agents still get their 📦 component pill + plane
// symbols rendered next to their agent_id in historical rows. The
// chat sidebar continues to render from `state.agents` (which respects
// the showDecommissioned toggle), so this doesn't change the sidebar.
const _allAgentsById = new Map();

async function fetchAgents() {
  try {
    const qs = state.showDecommissioned ? '?include_decommissioned=true' : '';
    // Two requests in parallel: the user-facing list (respects toggle)
    // and the always-include-decom dataset that powers row-level lookups.
    const [resA, resB] = await Promise.all([
      fetch(`/api/agents${qs}`),
      fetch('/api/agents?include_decommissioned=true'),
    ]);
    const dataA = await resA.json();
    const dataB = await resB.json();
    state.agents = dataA.agents || [];
    _allAgentsById.clear();
    for (const a of (dataB.agents || [])) {
      _allAgentsById.set(a.agent_id, a);
    }
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

  // Phase 7.4.9: datalist suggestions pull from the ALL-agents cache
  // (active + decommissioned) so admins can filter Communications by a
  // historically-merged-away agent. The chat sidebar still renders from
  // state.agents (which respects the showDecommissioned toggle).
  const agentIds = Array.from(_allAgentsById.keys());
  agentIds.sort();
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
    // Phase 5.4: search predicate also matches component canonical /
    // display names so SMEs are findable by what they own. /api/agents
    // already returns component_canonical + component_display per row
    // (Phase 4); we just widen the match.
    const filtered = search
      ? agents.filter(a =>
          a.agent_id.toLowerCase().includes(search) ||
          (a.component_canonical || '').toLowerCase().includes(search) ||
          (a.component_display   || '').toLowerCase().includes(search))
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
  const isDecom = agent.status === 'decommissioned';
  if (isDecom) li.classList.add('decommissioned');
  // isSleeping = column is set AND the deadline is still in the future.
  // Backend treats past sleep_until as already-awake; FE must agree.
  const isSleeping = !!(agent.sleep_until && new Date(agent.sleep_until) > new Date());
  const sleepTag = isSleeping
    ? `<span class="status status-sleep">💤 until ${new Date(agent.sleep_until).toLocaleString()}</span>`
    : '';
  // Phase 4: show a compact "→ <survivor>" tag for merged agents so
  // the chain is visible at a glance without opening the chat header.
  const mergedTag = (isDecom && agent.merged_into_agent_id)
    ? `<span class="status status-merged" title="${escapeHtml(agent.deactivation_reason || '')}: ${escapeHtml(agent.deactivation_notes || '')}">→ ${escapeHtml(agent.merged_into_agent_id)}</span>`
    : '';
  const actionBtn = (isSleeping && !isDecom)
    ? `<button class="row-btn row-wake" title="Wake ${escapeHtml(agent.agent_id)}">⏰</button>`
    : (!isDecom
        ? `<button class="row-btn row-sleep" title="Sleep ${escapeHtml(agent.agent_id)}">💤</button>`
        : '');
  // Phase 4 pre-demo polish: show the SME's managed component inline so
  // admin can tell "agent sme-abc-123 → Payments Service" at a glance
  // without clicking into the graph tab.
  // Plane symbols sourced from agent.resource_planes (RCA→resources.plane,
  // canonical "what plane(s) does this agent's resource cover"). Letters
  // chosen per user spec: G/C/T/D for github/cloud/telemetry/deploy.
  // Config uses F (con-F-ig) to avoid collision with cloud's C.
  // Colors mirror the graph PLANE_COLORS palette.
  const PLANE_LETTER = {github: 'G', cloud: 'C', telemetry: 'T', deploy: 'D', config: 'F'};
  const PLANE_HUE    = {github: '#0d9488', cloud: '#db2777', telemetry: '#9333ea', deploy: '#2563eb', config: '#ea580c'};
  const planeSyms = (agent.resource_planes || []).map(p => {
    const letter = PLANE_LETTER[p];
    const color  = PLANE_HUE[p];
    if (!letter) return '';
    return `<span class="plane-sym" title="${escapeHtml(p)}" style="background:${color}33;color:${color};border:1px solid ${color}66">${letter}</span>`;
  }).join('');
  const compTag = agent.component_canonical
    ? `<span class="component-tag" title="Manages ${escapeHtml(agent.component_canonical)}">📦 ${escapeHtml(agent.component_display || agent.component_canonical)}</span>${planeSyms}`
    : planeSyms;
  li.innerHTML = `
    <div class="agent-row-head">
      <div class="agent-id">${escapeHtml(agent.agent_id)}</div>
      ${actionBtn}
    </div>
    <div class="agent-meta">
      <span class="status status-${agent.status}">${agent.status}</span>
      ${sleepTag}
      ${mergedTag}
    </div>
    ${compTag ? `<div class="agent-component-row">${compTag}</div>` : ''}
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

// Phase 4: deactivation block rendered above the messages pane when
// a decommissioned agent is selected. Walks /api/agent/:id/chain to
// show the full merge lineage (A → B → C (active)).
async function _renderDeactivationBlock(agent) {
  let block = document.getElementById('deactivation-block');
  if (!agent || agent.status !== 'decommissioned') {
    if (block) block.remove();
    return;
  }
  if (!block) {
    block = document.createElement('div');
    block.id = 'deactivation-block';
    const messagesEl = document.getElementById('messages');
    messagesEl.parentNode.insertBefore(block, messagesEl);
  }
  block.innerHTML = '<div class="deactivation-loading">Loading chain…</div>';
  try {
    const res = await fetch(`/api/agent/${encodeURIComponent(agent.agent_id)}/chain`);
    const data = await res.json();
    const chain = data.chain || [];
    const chainHtml = chain.map((a, i) => {
      const arrow = i === chain.length - 1 ? '' : ' → ';
      const cls = a.status === 'decommissioned' ? 'chain-hop-dead' : 'chain-hop-active';
      return `<span class="${cls}" data-agent-id="${escapeHtml(a.agent_id)}">${escapeHtml(a.agent_id)}</span>${arrow}`;
    }).join('');
    block.innerHTML = `
      <div class="deactivation-title">⚰️ Decommissioned agent</div>
      <div class="deactivation-row">
        <b>Reason:</b> ${escapeHtml(agent.deactivation_reason || '—')}
      </div>
      ${agent.deactivation_notes ? `
        <div class="deactivation-row">
          <b>Notes:</b> ${escapeHtml(agent.deactivation_notes)}
        </div>
      ` : ''}
      <div class="deactivation-row">
        <b>Merge chain:</b> ${chainHtml || '(single)'}
      </div>
    `;
    block.querySelectorAll('.chain-hop-active, .chain-hop-dead').forEach(el => {
      el.style.cursor = 'pointer';
      el.addEventListener('click', () => selectAgent(el.dataset.agentId));
    });
  } catch (e) {
    block.innerHTML = '<div class="deactivation-loading">Failed to load chain.</div>';
  }
}

// Phase 4: toggle for showing decommissioned agents in the sidebar.
// Mounted lazily at init — the checkbox lives above the agent-list.
function _mountDecommissionedToggle() {
  const sidebar = document.querySelector('#sidebar header');
  if (!sidebar || document.getElementById('show-decom-toggle')) return;
  const label = document.createElement('label');
  label.className = 'show-decom-toggle';
  label.style.cssText = 'display:block;font-size:11px;color:#94a3b8;margin-top:4px;cursor:pointer;';
  label.innerHTML = `
    <input type="checkbox" id="show-decom-toggle" style="margin-right:4px;">
    Show merged / decommissioned
  `;
  sidebar.appendChild(label);
  const cb = label.querySelector('input');
  cb.checked = state.showDecommissioned;
  cb.addEventListener('change', () => {
    state.showDecommissioned = cb.checked;
    fetchAgents();
  });
}

async function selectAgent(agentId) {
  if (state.pollInterval) clearInterval(state.pollInterval);

  // Phase 5.1: push URL so the chosen agent is deep-linkable. Done up
  // front (before the network round-trip) so the URL reflects user intent
  // immediately. Skip when the call originated from a routechange to
  // avoid bouncing back through Router.navigate.
  if (!_navigatingFromRoute && state.selectedAgentId !== agentId) {
    Router.navigate(`/chat/${encodeURIComponent(agentId)}`);
  }

  state.selectedAgentId = agentId;
  state.messages = [];
  state.oldestTimestamp = null;
  state.newestTimestamp = null;

  renderAgents();
  $chatTitle.textContent = agentId;
  const agent = state.agents.find(a => a.agent_id === agentId);
  $chatSubtitle.textContent = agent ? `${agent.agent_type} · ${agent.status}` : '';
  // Phase 4: when selecting a decommissioned agent, render the
  // deactivation + merge-chain block above the message pane so the
  // admin sees why the agent exited + who picked up its work.
  _renderDeactivationBlock(agent);

  const disabled = !!(agent && agent.status === 'decommissioned');
  $chatInput.disabled = disabled;
  $sendBtn.disabled = disabled;
  $chatInput.placeholder = disabled
    ? 'Agent decommissioned — read-only'
    : 'Type a message...';
  if (!disabled) $chatInput.focus();

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

// Enter submits; Shift+Enter inserts a newline. Auto-grow the textarea
// so pasted multi-line content is visible up to a reasonable cap.
$chatInput.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    $chatForm.requestSubmit();
  }
});
const _autoGrow = () => {
  $chatInput.style.height = 'auto';
  $chatInput.style.height = Math.min($chatInput.scrollHeight, 300) + 'px';
};
$chatInput.addEventListener('input', _autoGrow);
$chatInput.addEventListener('paste', () => setTimeout(_autoGrow, 0));

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
    $chatInput.style.height = 'auto';
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

_mountDecommissionedToggle();
fetchAgents();
state.agentRefreshInterval = setInterval(fetchAgents, 5000);

// Phase 5.1: routechange handler + initial bootstrap from URL.
//
// The handler maps a URL onto FE state by calling switchTab and (where
// relevant) selectAgent. Sets _navigatingFromRoute so those mutators
// don't bounce back through Router.navigate.
function _applyRoute() {
  const { segments, query } = Router.current();
  _navigatingFromRoute = true;
  try {
    const tab = segments[0] || 'chat';
    const knownTabs = {
      chat: 'chat', communications: 'comms', graph: 'graph',
      entities: 'entities', catalog: 'catalog', insights: 'insights',
    };
    if (knownTabs[tab]) {
      switchTab(knownTabs[tab]);
      if (tab === 'chat' && segments[1]) {
        selectAgent(decodeURIComponent(segments[1]));
      }
      // 5.2 / 5.3 will hook entity / component drill-down rendering here
      // once those tabs land.
    } else if (tab === 'broadcast' && segments[1] === 'new') {
      switchTab('comms');
      const dlg = document.getElementById('broadcast-dialog');
      if (dlg && !dlg.open) dlg.showModal();
    } else if (tab === 'agent' && segments[2] === 'chain') {
      // /agent/:id/chain — open chat tab on that agent (the existing
      // selectAgent already renders the chain panel above the chat pane
      // when the selected agent is decommissioned).
      switchTab('chat');
      selectAgent(decodeURIComponent(segments[1]));
    } else {
      // Unknown route → default to chat.
      switchTab('chat');
    }
  } finally {
    _navigatingFromRoute = false;
  }
}

window.addEventListener('routechange', _applyRoute);
// Initial dispatch — kicks off after the synchronous bootstrap above.
// selectAgent works even before fetchAgents resolves; the chat header
// fills in once the agent list arrives.
setTimeout(_applyRoute, 0);


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
    participant_component: '',  // Phase 7.4.9
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
  document.getElementById('entities-view').classList.toggle('active', name === 'entities');
  document.getElementById('catalog-view').classList.toggle('active', name === 'catalog');
  document.getElementById('insights-view').classList.toggle('active', name === 'insights');
  document.getElementById('graph-view').classList.toggle('active', name === 'graph');
  // Phase 7.4.10: pause the 3d-force-graph render loop when the user
  // leaves the Graph tab; resume on entry. ROOT CAUSE of recurring
  // Chrome GPU process crashes (Exit code 5) was that 3d-force-graph
  // keeps requestAnimationFrame-driven rendering alive even when the
  // canvas is hidden via display:none — every frame allocates per-edge
  // line materials, arrow heads, particles, etc., and after 30–60 min
  // of offscreen rendering the GPU process OOMs. Browser.log captured
  // 9 webglcontextlost events on URLs OTHER than /graph (chat tabs,
  // entities, catalog). Pause/resume eliminates the offscreen burn.
  if (graphInstance) {
    if (name === 'graph') {
      try { graphInstance.resumeAnimation && graphInstance.resumeAnimation(); } catch (e) {}
    } else {
      try { graphInstance.pauseAnimation && graphInstance.pauseAnimation(); } catch (e) {}
    }
  }
  if (name === 'comms') fetchCommunications();
  if (name === 'entities') fetchEntities();
  if (name === 'catalog') fetchCatalog();
  if (name === 'insights') fetchInsights();
  if (name === 'graph') initOrRefreshGraph();
  // Phase 5.1: push URL when the tab change came from a click/code path,
  // not from a routechange that already advanced the URL.
  if (!_navigatingFromRoute) {
    const urlName = name === 'comms' ? 'communications' : name;
    Router.navigate(`/${urlName}`);
  }
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
  if (f.participant_component) params.set('participant_component', f.participant_component);
  if (f.type) params.set('type', f.type);
  params.set('limit', '100');
  try {
    const [commsRes, auditRes] = await Promise.all([
      fetch(`/api/communications?${params.toString()}`),
      // Phase 4: batch-load recent proxy_audit so the row renderer can
      // stamp "via <survivor>" badges without N+1 requests. The audit
      // log is small — one row per proxy act — and we cap at 500.
      fetch('/api/proxy_audit?limit=500'),
    ]);
    const data = await commsRes.json();
    const auditData = await auditRes.json();
    commsState.messages = data.messages || [];
    commsState.hasMore = data.has_more;
    // Map keyed "item_type|item_id" → [audit rows].
    commsState.proxyAudit = {};
    for (const a of (auditData.entries || [])) {
      const k = `${a.item_type}|${a.item_id}`;
      (commsState.proxyAudit[k] ||= []).push(a);
    }
    renderCommunications();
  } catch (e) {
    console.error('Failed to fetch communications:', e);
  }
}

// Look up an agent's managed component for inline tagging in
// Communications. Phase 7.4.9: reads from the ALL-agents cache
// (_allAgentsById) so decommissioned agents on historical rows still
// surface their 📦 component pill — without this they'd render as a
// bare agent_id with no context. Returns null for non-SMEs + admin +
// unknown ids.
function _componentLabelForAgent(agent_id) {
  if (!agent_id || agent_id === 'admin') return null;
  const a = _allAgentsById.get(agent_id);
  if (!a || !a.component_canonical) return null;
  return {
    canonical: a.component_canonical,
    display: a.component_display || a.component_canonical,
    id: a.component_id,
    decommissioned: a.status === 'decommissioned',
  };
}

// Phase 7.4.9: return plane-symbol HTML pills for an agent (G/C/T/D/F).
// Mirrors the chat-sidebar rendering in _renderAgentRow. Returns ''
// (empty string) for unknown / no-resource agents so the caller can
// concatenate without conditionals.
const _PLANE_LETTER = {github: 'G', cloud: 'C', telemetry: 'T', deploy: 'D', config: 'F'};
const _PLANE_HUE    = {github: '#0d9488', cloud: '#db2777', telemetry: '#9333ea', deploy: '#2563eb', config: '#ea580c'};
function _planeSymbolsForAgent(agent_id) {
  if (!agent_id || agent_id === 'admin') return '';
  const a = _allAgentsById.get(agent_id);
  if (!a || !a.resource_planes || !a.resource_planes.length) return '';
  return a.resource_planes.map(p => {
    const letter = _PLANE_LETTER[p];
    const color  = _PLANE_HUE[p];
    if (!letter) return '';
    return `<span class="plane-sym" title="${escapeHtml(p)}" style="background:${color}33;color:${color};border:1px solid ${color}66">${letter}</span>`;
  }).join('');
}

// Phase 4: key a communication row to proxy_audit. item_id is the
// underlying entity for consolidation/clarification/task (source_id),
// otherwise the communication row's own id (chat/broadcast).
function _proxyAuditFor(m) {
  if (!commsState.proxyAudit) return null;
  const id = (m.type === 'chat' || m.type === 'broadcast')
    ? m.id
    : m.source_id;
  if (!id) return null;
  const hits = commsState.proxyAudit[`${m.type}|${id}`];
  return (hits && hits.length) ? hits[0] : null;
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
    // Phase 4: if this row's ledger author was acted-as via proxy, show
    // "via <survivor>" — the real click came from the survivor, but the
    // row records the original (decommissioned) owner.
    const audit = _proxyAuditFor(m);
    const viaBadge = audit
      ? ` <span class="pill proxy-via" title="Proxied by ${escapeHtml(audit.survivor_id)}">via ${escapeHtml(audit.survivor_id)}</span>`
      : '';
    // Pre-demo polish: show 📦 Component pill next to from/to when
    // the participant is an SME with a managed component. Phase 7.4.9:
    // also render plane-symbol pills (G/C/T/D/F) next to the component
    // tag — same palette as the chat sidebar — and dim the 📦 tag for
    // decommissioned agents so historical rows are visually distinct.
    const fromComp = _componentLabelForAgent(m.from_agent);
    const toComp = _componentLabelForAgent(m.to_agent);
    const _compTagHtml = (comp) => {
      if (!comp) return '';
      const decomCls = comp.decommissioned ? ' component-tag-decom' : '';
      const decomTitle = comp.decommissioned ? ' (decommissioned)' : '';
      return ` <span class="component-tag-inline${decomCls}" title="${escapeHtml(comp.canonical)}${decomTitle}">📦 ${escapeHtml(comp.display)}</span>`;
    };
    const fromCompTag = _compTagHtml(fromComp);
    const toCompTag = _compTagHtml(toComp);
    const fromPlanes = _planeSymbolsForAgent(m.from_agent);
    const toPlanes = _planeSymbolsForAgent(m.to_agent);
    // Phase 5.4: 📌 pill on persistent broadcasts so admins can tell at
    // a glance which broadcasts are standing policy vs forward-only.
    // Phase 5.7: also a clickable toggle to flip is_persistent on the fly.
    const persistentPill = m.type === 'broadcast'
      ? ` <button class="pill persistence-toggle ${m.is_persistent ? 'persistent-pill' : 'forward-only-pill'}"
            data-bcast-id="${m.id}" data-persistent="${m.is_persistent ? '1' : '0'}"
            title="Click to ${m.is_persistent ? 'make forward-only' : 'mark persistent'}"
        >${m.is_persistent ? '📌 persistent' : '↪ forward-only'}</button>`
      : '';
    // Phase 5.12: short entity-id badge for cross-tab cross-reference.
    // For task / consolidation / clarification: that's source_id (the
    // owning entity). For broadcast: the comm row itself IS the entity,
    // so use m.id. For chat: no entity (chats aren't in Entities).
    // Click → drill into Entities tab for that row.
    let entityRefHtml = '';
    if (m.type !== 'chat') {
      const entityId = (m.type === 'broadcast') ? m.id : m.source_id;
      if (entityId) {
        entityRefHtml = ` <a class="entity-ref" href="/entities/${m.type}/${entityId}"
            data-entity-type="${m.type}" data-entity-id="${entityId}"
            title="Open in Entities tab"
          >${m.type}/<code>${escapeHtml(String(entityId).slice(0, 8))}</code></a>`;
      }
    }
    li.innerHTML = `
      <div class="comm-head">
        <span class="type-pill type-${m.type}">${m.type}</span>
        <span class="from">${escapeHtml(m.from_agent)}</span>${fromCompTag}${fromPlanes}
        <span class="arrow">→</span>
        <span class="to">${escapeHtml(commTargetLabel(m))}</span>${toCompTag}${toPlanes}
        ${state}${viaBadge}${persistentPill}${entityRefHtml}
        <span class="ts">${new Date(m.created_at).toLocaleString()}</span>
      </div>
      <div class="comm-body">${escapeHtml(excerpt)}${excerpt.length >= 120 ? '…' : ''}</div>
    `;
    li.addEventListener('click', e => {
      // Phase 5.7: persistence toggle inside a row — handle it here, don't
      // drill into the row's detail panel.
      if (e.target.closest('.persistence-toggle')) {
        e.stopPropagation();
        const btn = e.target.closest('.persistence-toggle');
        const id = btn.dataset.bcastId;
        const next = btn.dataset.persistent !== '1';
        toggleBroadcastPersistence(id, next);
        return;
      }
      // Phase 5.12: entity-ref click → SPA navigate to Entities tab,
      // don't trigger full page load + don't drill into detail.
      const ref = e.target.closest('.entity-ref');
      if (ref) {
        e.preventDefault();
        e.stopPropagation();
        Router.navigate(`/entities/${ref.dataset.entityType}/${ref.dataset.entityId}`);
        return;
      }
      selectCommunication(m);
    });
    $list.appendChild(li);
  });
}

async function toggleBroadcastPersistence(communicationId, persistent) {
  try {
    const res = await fetch(
      `/api/broadcast/${encodeURIComponent(communicationId)}/persistence`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ persistent }),
      }
    );
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      alert(`Failed to toggle persistence: ${err.detail || res.status}`);
      return;
    }
    // Update local state and re-render to flip the pill instantly.
    const row = commsState.messages.find(m => m.id === communicationId);
    if (row) row.is_persistent = persistent;
    renderCommunications();
  } catch (e) {
    console.error('toggleBroadcastPersistence failed:', e);
  }
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
    participant_component:
      document.getElementById('filter-component').value.trim(),  // Phase 7.4.9
    type: document.getElementById('filter-type').value,
  };
  fetchCommunications();
});

document.getElementById('filter-reset').addEventListener('click', () => {
  // Phase 7.4.9: include 'filter-component' in the reset sweep
  ['filter-agent', 'filter-from', 'filter-to', 'filter-component'].forEach(id =>
    document.getElementById(id).value = '');
  ['filter-agent-type', 'filter-from-type', 'filter-to-type', 'filter-type'].forEach(id =>
    document.getElementById(id).value = '');
  commsState.filters = {
    agent: '', agent_type: '',
    from_agent: '', to_agent: '',
    from_agent_type: '', to_agent_type: '',
    participant_component: '',
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

// GPU-resource caches. Geometries + materials are reused across nodes
// keyed on shape parameters / color so we don't allocate fresh GPU
// buffers per node per refresh. Cap each cache at 256 entries — well
// above realistic graph variety; eviction order doesn't matter since
// disposal happens on cache wipe (graph teardown).
const _geomCache = new Map();   // key → THREE.BufferGeometry
const _matCache  = new Map();   // key → THREE.MeshLambertMaterial
const GPU_CACHE_MAX = 256;

function _geomKey(type, s) {
  // Quantise s to 0.1 so floating-point jitter doesn't fragment the cache.
  return `${type}:${Math.round(s * 10) / 10}`;
}

function _getGeometry(THREE, type, s) {
  const key = _geomKey(type, s);
  if (_geomCache.has(key)) return _geomCache.get(key);
  let g;
  switch (type) {
    case 'database':         g = new THREE.CylinderGeometry(s * 1.05, s * 1.05, s * 2, 28); break;
    case 'cache':            g = new THREE.TorusGeometry(s, s * 0.5, 16, 32); break;
    case 'queue':            g = new THREE.ConeGeometry(s * 1.2, s * 2.4, 24); break;
    case 'lambda':           g = new THREE.OctahedronGeometry(s * 1.55); break;
    case 'cron':             g = new THREE.IcosahedronGeometry(s * 1.35); break;
    case 'external-service': g = new THREE.TetrahedronGeometry(s * 1.75); break;
    case 'library':          g = new THREE.BoxGeometry(s * 1.7, s * 1.7, s * 1.7); break;
    case 'infrastructure':   g = new THREE.BoxGeometry(s * 2.5, s * 0.7, s * 2.5); break;
    case 'junction':         g = new THREE.TetrahedronGeometry(s); break;
    case 'stub':             g = new THREE.SphereGeometry(s * 0.55, 20, 20); break;
    case 'application':
    default:                 g = new THREE.SphereGeometry(s, 32, 32); break;
  }
  if (_geomCache.size < GPU_CACHE_MAX) _geomCache.set(key, g);
  return g;
}

function _getMaterial(THREE, color, isMuted) {
  const key = `${color || '#cccccc'}:${isMuted ? 'm' : 's'}`;
  if (_matCache.has(key)) return _matCache.get(key);
  const m = new THREE.MeshLambertMaterial({
    color: color || '#cccccc',
    transparent: true,
    opacity: isMuted ? 0.55 : 0.95,
  });
  if (_matCache.size < GPU_CACHE_MAX) _matCache.set(key, m);
  return m;
}

function _disposeGpuCaches() {
  // Called on full graph teardown (page unload, context-lost). Releases
  // GPU buffers held in the geometry/material caches. Safe to call on
  // empty caches.
  for (const g of _geomCache.values()) { try { g.dispose(); } catch (e) {} }
  for (const m of _matCache.values())  { try { m.dispose(); } catch (e) {} }
  _geomCache.clear();
  _matCache.clear();
}

function makeNodeMesh(node) {
  const THREE = window.THREE;
  if (!THREE) return null;  // fallback → library default sphere
  const s = _sizeForNode(node);
  // Both geometry + material come from caches keyed on shape
  // parameters / color. The Mesh wrapper itself is cheap (no GPU
  // allocation) — only the underlying geometry+material are GPU
  // resources, and those are now shared across all nodes of the same
  // shape+color. Net effect: GPU buffer count is bounded by distinct
  // (type, size, color) tuples, regardless of node count or refresh
  // count. Pre-fix: 1 fresh geometry + 1 fresh material PER node PER
  // refresh → unbounded growth → Chrome GPU process OOM exit-code-5.
  const isMuted = node.isJunction || node.isStub;
  const geom = _getGeometry(THREE, node.type, s);
  const mat  = _getMaterial(THREE, node.color, isMuted);
  return new THREE.Mesh(geom, mat);
}


// ---------- Browser-side crash + error capture (POSTs to /api/clientlog) ----------
//
// Chrome's GPU process can die mid-session (exit code 5 → "GPU process
// crashed" → after 11 crashes Chrome blocklists WebGL entirely until a
// full app restart). When that happens the WebGL context is lost; the
// canvas goes black and no client error is thrown — there is nothing to
// surface to the developer without instrumentation. These hooks POST
// every relevant client-side signal to /api/clientlog (appended to
// /tmp/cartograph-logs/browser.log on the server) so we can grep crashes
// after the fact.
const _clientLogEndpoint = '/api/clientlog';
let _clientLogInstalled = false;

function _postClientLog(event, detail) {
  try {
    fetch(_clientLogEndpoint, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        ts: new Date().toISOString(),
        event,
        url: window.location.pathname,
        ua: navigator.userAgent,
        detail,
      }),
      keepalive: true,
    }).catch(() => {});
  } catch (e) { /* never throw from a logger */ }
}

function _installClientLogHooks() {
  if (_clientLogInstalled) return;
  _clientLogInstalled = true;
  window.addEventListener('error', (ev) => {
    _postClientLog('window.error', {
      message: ev.message, filename: ev.filename,
      line: ev.lineno, col: ev.colno,
      stack: ev.error && ev.error.stack ? ev.error.stack : null,
    });
  });
  window.addEventListener('unhandledrejection', (ev) => {
    _postClientLog('unhandledrejection', {
      reason: String(ev.reason),
      stack: ev.reason && ev.reason.stack ? ev.reason.stack : null,
    });
  });
  window.addEventListener('beforeunload', () => {
    _postClientLog('beforeunload', {graphLoadedOnce});
  });
}

// Phase 7.4.10: idempotency flag so a duplicate webglcontextlost (Chrome
// sometimes fires on both inner <canvas> and outer container) doesn't
// re-dispose already-disposed caches. Reset on successful restore.
let _ctxLostHandled = false;

function _installWebGLContextHooks(canvasEl) {
  // 3d-force-graph mounts a <canvas> inside our container. Find it.
  const innerCanvas = canvasEl.querySelector('canvas') || canvasEl;
  if (!innerCanvas || innerCanvas._cartoHooked) return;
  innerCanvas._cartoHooked = true;
  // Default behaviour on context-lost is for Chrome to NOT redispatch
  // webglcontextrestored unless preventDefault is called. So we always
  // call it. The library doesn't auto-restore — we manually re-init.
  innerCanvas.addEventListener('webglcontextlost', (ev) => {
    ev.preventDefault();
    _postClientLog('webglcontextlost', {
      stats: {
        geomCacheSize: _geomCache.size,
        matCacheSize: _matCache.size,
        nodes: graphSnapshot.nodes ? graphSnapshot.nodes.length : 0,
        edges: graphSnapshot.edges ? graphSnapshot.edges.length : 0,
        duplicate: _ctxLostHandled,
      },
    });
    if (_ctxLostHandled) return;  // double-fire guard
    _ctxLostHandled = true;
    console.warn('[viz] WebGL context lost — Chrome GPU process likely crashed');
    // Free our caches so a future restore starts clean and doesn't
    // reference the dead GPU resources.
    _disposeGpuCaches();
    // Tear down our local references so a fresh init can rebuild.
    if (graphInstance) {
      try { graphInstance._destructor && graphInstance._destructor(); } catch (e) {}
    }
    graphInstance = null;
    graphLoadedOnce = false;
    // Show user a clear hint in the canvas surface.
    canvasEl.innerHTML = '<p class="empty" style="padding:20px;color:#fbbf24">'
      + 'WebGL context lost (Chrome GPU process died). '
      + 'Click Refresh on the Graph tab once Chrome restarts the GPU process; '
      + 'if the canvas stays empty, hard-reload the page or restart Chrome. '
      + 'Captured crash details to server log.</p>';
  });
  innerCanvas.addEventListener('webglcontextrestored', () => {
    _postClientLog('webglcontextrestored', {});
    console.info('[viz] WebGL context restored — re-initialising graph');
    _ctxLostHandled = false;
    initOrRefreshGraph();
  });
}

// Phase 7.4.10: Page Visibility API — pause animation when the BROWSER
// TAB itself is hidden (admin alt-tabs / minimises the window). Belt
// and suspenders alongside the per-tab pause/resume in switchTab —
// guards against the case where the user leaves the page open with
// Graph as the active app-tab and walks away. RAF on a hidden browser
// tab is throttled but not stopped; pause stops it cold.
document.addEventListener('visibilitychange', () => {
  if (!graphInstance) return;
  try {
    if (document.hidden) {
      graphInstance.pauseAnimation && graphInstance.pauseAnimation();
    } else {
      // Only resume if Graph is the currently-active app tab — don't
      // override a deliberate pause from switchTab.
      const graphTabActive = document.getElementById('graph-view')
        && document.getElementById('graph-view').classList.contains('active');
      if (graphTabActive) {
        graphInstance.resumeAnimation && graphInstance.resumeAnimation();
      }
    }
  } catch (e) { /* no-op */ }
});

async function initOrRefreshGraph() {
  _installClientLogHooks();
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
    description: n.description,            // Phase 10.7: dense embed-target
    doc: n.component_doc_md,                // human-render only
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
    if (group.length < 2) continue;  // bundle every shared-target endpoint
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

  // --- Dangling stubs ---
  //
  // Two kinds of stubs surface as visual links:
  //   1. Orphan catalog    : kind='catalog' with NO matching bound caller.
  //                          Rendered as "? → X" (stub source → real target).
  //   2. Outgoing dangling : kind='dangling' (to_component_id NULL).
  //                          Rendered as "X → ?" (real source → stub target).
  // Catalogs that DO have bound callers stay hidden — the bound edge
  // already represents them. Each stub gets a unique "?" placeholder
  // node so they don't fake-converge in the layout.
  const boundKeySet = new Set(
    boundEdges.map(e => `${e.target_id}|${e.edge_type}|${e.identifier}`)
  );
  const stubNodes = [];
  const stubLinks = [];
  const stubEdgeIds = new Set();
  // Group first so we can assign distinct per-anchor offset directions.
  // Overlap → charge-force collisions → cluster-wide shaking, so each
  // sibling stub must get its own fixed direction vector.
  const pendingStubs = [];  // {e, stubKind, anchorId}
  for (const e of data.edges) {
    if (e.kind === 'catalog') {
      const key = `${e.target_id}|${e.edge_type}|${e.identifier}`;
      if (boundKeySet.has(key)) continue;  // has a bound caller → implicit
      pendingStubs.push({e, stubKind: 'inbound', anchorId: e.target_id});
    } else if (e.kind === 'dangling') {
      pendingStubs.push({e, stubKind: 'outbound', anchorId: e.source_id});
    }
  }
  // Anchor → ordered list of its stubs.
  const anchorBuckets = {};
  for (const p of pendingStubs) {
    (anchorBuckets[p.anchorId] ||= []).push(p);
  }
  // Precompute an offset vector per stub. Offsets are FROZEN (anchor-
  // relative, never re-derived from the anchor's moving position) so
  // the stub pin doesn't oscillate with anchor motion, and every
  // sibling stub of one anchor gets a distinct direction.
  const STUB_R = 22;
  for (const [anchorId, list] of Object.entries(anchorBuckets)) {
    const k = list.length;
    for (let i = 0; i < k; i++) {
      // Spread around a small tilted ring. z stays near the equator so
      // stubs read as "beside" their anchor rather than "above/below."
      const theta = k > 1 ? (i / k) * 2 * Math.PI : 0;
      const phi = 1.3;  // ~74° from +Z → mostly lateral spread
      const dir = {
        x: Math.sin(phi) * Math.cos(theta),
        y: Math.sin(phi) * Math.sin(theta),
        z: Math.cos(phi) * (i % 2 === 0 ? 1 : -1),  // alternate up/down so 3+ stubs spread in 3D
      };
      list[i].offset = {x: dir.x * STUB_R, y: dir.y * STUB_R, z: dir.z * STUB_R};
    }
  }
  for (const {e, stubKind, anchorId, offset} of pendingStubs) {
    const stubId = stubKind === 'inbound' ? `__stub_in__${e.id}` : `__stub_out__${e.id}`;
    stubNodes.push({
      id: stubId,
      isStub: true,
      stubKind,
      stubAnchorId: anchorId,
      stubOffset: offset,
      name: '?',
      type: 'stub',
      planes: [],
      color: '#475569',
    });
    if (stubKind === 'inbound') {
      stubLinks.push({
        id: e.id, source: stubId, target: anchorId,
        edge_type: e.edge_type, identifier: e.identifier,
        confidence: e.confidence, curvature: 0,
        isStub: true, stubKind: 'inbound',
      });
    } else {
      stubLinks.push({
        id: e.id, source: anchorId, target: stubId,
        edge_type: e.edge_type, identifier: e.identifier,
        confidence: e.confidence, curvature: 0,
        isStub: true, stubKind: 'outbound',
      });
    }
    stubEdgeIds.add(e.id);
  }

  const links = [...regularLinks, ...bundledLinks, ...junctionOutLinks, ...stubLinks];
  const allNodes = [...transformedNodes, ...junctionNodes, ...stubNodes];
  const gData = {nodes: allNodes, links};

  // Expose junction metadata to the rest of the module so hover on a
  // junction-out link can explain the whole bundle + light it up.
  graphSnapshot.junctionOutById = junctionOutById;
  graphSnapshot.bundledEdgeIds = bundledEdgeIds;
  graphSnapshot.stubEdgeIds = stubEdgeIds;

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
        if (n.isStub) return;      // "?" placeholders aren't real components
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
        if (l.isStub)        return 22;  // short hop from anchor to "?"
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

      // Stub pinning: each "?" placeholder sits at a FROZEN offset
      // from its anchor — the offset was assigned at transform time
      // with a distinct direction per sibling stub. We don't recompute
      // anything anchor-dependent here (used to use radial outward,
      // which rotates as the anchor settles → caused whole-cluster
      // shaking when the stub moved and its charge pushed neighbors).
      for (const n of gd.nodes) {
        if (!n.isStub) continue;
        const anchor = byId[n.stubAnchorId];
        if (!anchor || anchor.x == null) continue;
        const o = n.stubOffset || {x: 22, y: 0, z: 0};
        n.fx = anchor.x + o.x;
        n.fy = anchor.y + o.y;
        n.fz = anchor.z + o.z;
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
      // Phase 7.4.10: defend against `graphInstance = null` after a
      // webglcontextlost teardown. Pre-fix this read `null.camera` and
      // threw TypeError on every wheel event after a context loss
      // (153 spam errors observed in browser.log). Order matters: short-
      // circuit on `!graphInstance` BEFORE accessing .camera.
      if (!window.THREE || !graphInstance || !graphInstance.camera) return;
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

  // Hook the WebGL canvas for context-lost / restored signals.
  // Must run AFTER ForceGraph3D mounts its inner <canvas>.
  _installWebGLContextHooks(canvas);

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
  // Stubs are muted — they mark a dangling boundary, not the primary
  // structure. Dimmer than regular bound so the bound chain remains
  // visually dominant.
  if (l.isStub)                      return 'rgba(148, 163, 184, 0.35)';
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
  if (l.isStub)                      return 0.6;
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
  // Toggle accessors to null then re-set with a fresh closure. The
  // null→fn transition GUARANTEES the library treats this as a
  // structural change and rebuilds link materials. Just passing a
  // new function reference wasn't enough in this build.
  graphInstance.linkColor(null);
  graphInstance.linkColor(l => resolveLinkColor(l));
  graphInstance.linkWidth(null);
  graphInstance.linkWidth(l => resolveLinkWidth(l));
  graphInstance.linkDirectionalParticles(null);
  graphInstance.linkDirectionalParticles(l => resolveLinkParticles(l));
  console.log('[viz] refresh — lit=%d hover=%d', litEdgeIds.size, hoverLitEdgeIds.size);
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
  // Return [edgeId plus any catalog rows matching (target, type, identifier)]
  // so the BFS can find flows keyed on either. Server uses target_id (not
  // to_component_id) in the /api/graph response shape.
  const e = graphSnapshot.edgeById[edgeId];
  if (!e || !e.target_id) return [edgeId];
  const out = [edgeId];
  for (const cat of graphSnapshot.edges) {
    if (cat.kind === 'catalog'
        && cat.target_id === e.target_id
        && cat.edge_type === e.edge_type
        && cat.identifier === e.identifier) {
      out.push(cat.id);
    }
  }
  return out;
}

function catalogEdgeFor(edge) {
  // If `edge` is a bound row, find its matching catalog; otherwise null.
  if (!edge || !edge.target_id || edge.kind === 'catalog') return null;
  return graphSnapshot.edges.find(c =>
    c.kind === 'catalog'
    && c.target_id === edge.target_id
    && c.edge_type === edge.edge_type
    && c.identifier === edge.identifier
  );
}

function boundEdgesForIncoming(edgeId) {
  // Reverse of effectiveFlowIncomingsForEdge: given a flow's
  // incoming_edge_id (typically a catalog row), return all BOUND edges
  // that bridge to it — i.e. the real callers represented by that
  // catalog. Used by caller-side hover so hovering the caller half of
  // R→S lights up Q→R (the bound edge whose flow fires R→S's feeder).
  const e = graphSnapshot.edgeById[edgeId];
  if (!e) return [];
  if (e.kind === 'bound') return [e];
  if (e.kind !== 'catalog' || !e.target_id) return [];
  return graphSnapshot.edges.filter(c =>
    c.kind === 'bound'
    && c.target_id === e.target_id
    && c.edge_type === e.edge_type
    && c.identifier === e.identifier
  );
}

function lightOfSight(link) {
  // Cancel any in-flight reveal from a prior click.
  _losTimers.forEach(t => clearTimeout(t));
  _losTimers = [];
  litEdgeIds = new Set();
  refreshGraphVisuals();
  console.log('[LOS] click', {id: link.id, type: link.edge_type, ident: link.identifier,
    junctionIn: !!link.isJunctionIn, junctionOut: !!link.isJunctionOut});

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
  // For each edge e (acting as an incoming at dest=e.target_id):
  //   find flows on e.target_id where incoming matches e (or its
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
      const destComponentId = e.target_id;
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
        if (!flowIncomings.includes(f.incoming_catalog_id)) continue;
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

  // Phase 10.7: description (dense embed-target) renders as a header
  // line above whatever the active tab shows. Always visible regardless
  // of tab — it's the at-a-glance identity of the component.
  const descBlock = node.description
    ? `<div class="hover-description"><em>${escapeHtml(node.description)}</em></div>`
    : '';

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
      ? renderCatalogList(incoming_catalog)
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
      ${descBlock}
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

function renderCatalogList(catalogs) {
  // Render catalog rows with their ACTUAL binding state: list the
  // bound callers matched by (target, edge_type, identifier). Only
  // mark "no caller bound yet" when the row is truly orphan.
  return catalogs.map(e => {
    const callers = graphSnapshot.edges.filter(b =>
      b.kind === 'bound'
      && b.target_id === e.target_id
      && b.edge_type === e.edge_type
      && b.identifier === e.identifier
    );
    let sideHtml;
    if (callers.length === 0) {
      sideHtml = `<span class="edge-from catalog">exposed — no caller bound yet</span>`;
    } else {
      const names = callers.map(c => {
        const n = graphSnapshot.nodeById[c.source_id];
        return escapeHtml(n?.name || c.source_id.slice(0, 8));
      });
      sideHtml = `<span class="edge-from">${callers.length} caller(s): ${names.join(', ')}</span>`;
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
    (byIncoming[f.incoming_catalog_id] ||= []).push(f);
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

  // --- Stub side hovers ---
  //
  // Orphan catalog stub  (stubSrc → realTgt): caller zone is the
  //   stub side → no known caller. Target zone behaves normally.
  // Outgoing dangling    (realSrc → stubTgt): caller zone behaves
  //   normally. Target zone is the stub side → no known target.
  if (link.isStub) {
    if (link.stubKind === 'inbound' && zone === 'caller') {
      edgeIds.add(link.id);
      return {
        tooltipHtml: `
          ${header}
          <div class="tt-zone">DANGLING — no known caller for this inbound</div>
          <ul class="tt-list"><li><em>orphan catalog: no bound caller recorded</em></li></ul>
        `,
        edgeIds: [...edgeIds],
      };
    }
    if (link.stubKind === 'outbound' && zone === 'target') {
      edgeIds.add(link.id);
      return {
        tooltipHtml: `
          ${header}
          <div class="tt-zone">DANGLING — unknown target for this outbound</div>
          <ul class="tt-list"><li><em>caller knows the identifier but target isn't in graph</em></li></ul>
        `,
        edgeIds: [...edgeIds],
      };
    }
    // Other cases fall through: outbound caller-zone + inbound target-zone
    // run the standard flow lookup, which naturally finds the feeders /
    // downstreams of the real anchor component.
  }

  if (zone === 'caller') {
    // Caller-side: glow all incoming edges of the CALLER whose flows
    // have this edge as an outgoing.
    edgeIds.add(link.id);
    const feederIds = [];
    if (srcNode && underlying) {
      for (const f of graphSnapshot.flows) {
        if (f.component_id === srcNode.id
            && f.outgoing_edge_id === underlying.id) {
          feederIds.push(f.incoming_catalog_id);
        }
      }
    }
    // Glow bound edges: either the feeder itself (if bound) or the
    // bound edges bridging to a catalog feeder. A catalog is a shape,
    // not a real caller — the user wants to SEE the real callers.
    for (const fid of feederIds) {
      for (const be of boundEdgesForIncoming(fid)) {
        edgeIds.add(be.id);
      }
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
          && incomingCandidates.includes(f.incoming_catalog_id)) {
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


// ================================================================
// Phase 5.2: Entities tab — workflow-entity browser
// ================================================================
//
// Lists every task / consolidation / clarification / broadcast in one
// view. Filters: kind, status, participant, search, open-only. Click a
// row → drill-down panel with full thread (or ack roster for
// broadcasts).

const entitiesState = {
  rows: [],
  hasMore: false,
  selectedType: null,
  selectedId: null,
};

// Phase 5.12 cleanup: 'type' is the canonical term across both
// Communications (communications.type) and Entities (entity-row type).
// Filter dropdowns / labels / state keys all use 'type'.
const _STATUS_OPTIONS_BY_TYPE = {
  '':              [],   // all types → no specific list
  'task':          ['BW', 'BO', 'WD', 'TC'],
  'consolidation': ['B1', 'B2', 'R', 'M', 'MD', 'D', 'F'],
  'clarification': ['B1', 'B2', 'QR', 'QC', 'CC'],
  'broadcast':     ['persistent', 'forward-only'],
};

function _populateStatusOptions(type) {
  const sel = document.getElementById('ent-status');
  if (!sel) return;
  const current = sel.value;
  sel.innerHTML = '<option value="">All</option>';
  (_STATUS_OPTIONS_BY_TYPE[type] || []).forEach(s => {
    const o = document.createElement('option');
    o.value = s; o.textContent = s;
    sel.appendChild(o);
  });
  // Preserve previous selection if still valid.
  if (current && (_STATUS_OPTIONS_BY_TYPE[type] || []).includes(current)) {
    sel.value = current;
  } else {
    sel.value = '';
  }
}

function _readEntityFiltersFromUrl() {
  const q = Router.current().query;
  const setVal = (id, v) => { const el = document.getElementById(id); if (el) el.value = v || ''; };
  const setChk = (id, v) => { const el = document.getElementById(id); if (el) el.checked = v === 'true'; };
  setVal('ent-type', q.type);
  _populateStatusOptions(q.type || '');
  setVal('ent-status', q.status);
  setChk('ent-open-only', q.open_only);
  setVal('ent-participant', q.participant);
  setVal('ent-q', q.q);
}

function _writeEntityFiltersToUrl() {
  const get = id => (document.getElementById(id) || {}).value || '';
  const chk = id => (document.getElementById(id) || {}).checked;
  Router.navigate('/entities', {
    set: {
      type: get('ent-type'),
      status: get('ent-status'),
      open_only: chk('ent-open-only') ? 'true' : '',
      participant: get('ent-participant'),
      q: get('ent-q'),
    },
  });
}

async function fetchEntities() {
  _readEntityFiltersFromUrl();
  const q = Router.current().query;
  const params = new URLSearchParams();
  ['type', 'status', 'participant', 'q'].forEach(k => {
    if (q[k]) params.set(k, q[k]);
  });
  if (q.open_only === 'true') params.set('open_only', 'true');
  params.set('limit', '100');
  try {
    const res = await fetch(`/api/entities?${params}`);
    const data = await res.json();
    entitiesState.rows = data.entities || [];
    entitiesState.hasMore = data.has_more;
    renderEntities();
  } catch (e) {
    console.error('fetchEntities failed:', e);
    document.getElementById('ent-items').innerHTML =
      '<li class="empty">Failed to load entities.</li>';
  }
  // If URL pointed at a specific entity, render its drill-down.
  const segs = Router.current().segments;
  if (segs[0] === 'entities' && segs[1] && segs[2]) {
    showEntityDetail(segs[1], segs[2]);
  }
}

function renderEntities() {
  const $items = document.getElementById('ent-items');
  const $count = document.getElementById('ent-count');
  $count.textContent = `${entitiesState.rows.length}${entitiesState.hasMore ? '+' : ''}`;
  if (!entitiesState.rows.length) {
    $items.innerHTML = '<li class="empty">No entities match the filters.</li>';
    return;
  }
  $items.innerHTML = '';
  for (const e of entitiesState.rows) {
    const li = document.createElement('li');
    li.className = 'ent-row';
    li.dataset.type = e.type;
    li.dataset.id = e.id;
    if (entitiesState.selectedType === e.type &&
        entitiesState.selectedId === e.id) {
      li.classList.add('selected');
    }
    const ts = e.last_activity ? new Date(e.last_activity).toLocaleString() : '';
    const summaryText = (e.summary || '').slice(0, 140) || '(no summary)';
    const part = [e.participant_a, e.participant_b]
      .filter(Boolean).join(' ↔ ');
    // Phase 5.12: persistence toggle on broadcast rows here too — same
    // affordance as the Communications tab. Click is intercepted via
    // .stopPropagation so the row doesn't drill into the broadcast detail.
    const isPersistent = e.extra && e.extra.is_persistent;
    const persistenceToggle = e.type === 'broadcast'
      ? ` <button class="pill persistence-toggle ${isPersistent ? 'persistent-pill' : 'forward-only-pill'}"
            data-bcast-id="${e.id}" data-persistent="${isPersistent ? '1' : '0'}"
            title="Click to ${isPersistent ? 'make forward-only' : 'mark persistent'}"
        >${isPersistent ? '📌 persistent' : '↪ forward-only'}</button>`
      : '';
    // Phase 5.12: short ID badge for cross-tab cross-reference.
    const idBadge = `<code class="ent-id" title="${escapeHtml(e.id)}">${escapeHtml(e.id.slice(0, 8))}</code>`;
    li.innerHTML = `
      <div class="ent-row-top">
        <span class="ent-kind ent-kind-${e.type}">${e.type}</span>
        <span class="ent-status">${e.status || ''}</span>
        ${persistenceToggle}
        ${idBadge}
        <span class="ent-ts">${ts}</span>
      </div>
      <div class="ent-summary">${escapeHtml(summaryText)}</div>
      <div class="ent-participants">${escapeHtml(part)}</div>
    `;
    li.addEventListener('click', evt => {
      // Phase 5.12: same persistence-toggle interception used in
      // Communications. Don't drill when admin clicks the toggle.
      if (evt.target.closest('.persistence-toggle')) {
        evt.stopPropagation();
        const btn = evt.target.closest('.persistence-toggle');
        const id = btn.dataset.bcastId;
        const next = btn.dataset.persistent !== '1';
        toggleBroadcastPersistenceFromEntities(id, next);
        return;
      }
      Router.navigate(`/entities/${e.type}/${e.id}`);
    });
    $items.appendChild(li);
  }
}

async function toggleBroadcastPersistenceFromEntities(communicationId, persistent) {
  try {
    const res = await fetch(
      `/api/broadcast/${encodeURIComponent(communicationId)}/persistence`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ persistent }),
      }
    );
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      alert(`Failed to toggle persistence: ${err.detail || res.status}`);
      return;
    }
    // Update local state in-place + re-render so the pill flips instantly.
    const row = entitiesState.rows.find(r => r.id === communicationId);
    if (row) {
      row.extra = row.extra || {};
      row.extra.is_persistent = persistent;
      row.status = persistent ? 'persistent' : 'forward-only';
    }
    renderEntities();
  } catch (e) {
    console.error('toggleBroadcastPersistenceFromEntities failed:', e);
  }
}

async function showEntityDetail(type, entityId) {
  entitiesState.selectedType = type;
  entitiesState.selectedId = entityId;
  renderEntities();   // refresh selected highlight
  const $title = document.getElementById('ent-detail-title');
  const $body = document.getElementById('ent-detail-body');
  $title.textContent = `${type} · ${entityId.slice(0, 8)}…`;
  $body.innerHTML = '<p class="empty">Loading…</p>';
  try {
    const res = await fetch(`/api/entity/${type}/${encodeURIComponent(entityId)}`);
    if (!res.ok) {
      $body.innerHTML = `<p class="empty">Not found (${res.status}).</p>`;
      return;
    }
    const data = await res.json();
    $body.innerHTML = _renderEntityDetailHtml(data);
  } catch (e) {
    console.error('showEntityDetail failed:', e);
    $body.innerHTML = '<p class="empty">Failed to load.</p>';
  }
}

function _renderEntityDetailHtml(data) {
  const { type, entity, thread = [], extras } = data;
  const head = `<div class="ent-detail-head">
    <div><b>type</b> ${type}</div>
    <div><b>id</b> <code>${entity.id || ''}</code></div>
    <div><b>status</b> ${entity.status || ''}</div>
  </div>`;
  let metaRows = '';
  for (const [k, v] of Object.entries(entity)) {
    if (['id', 'status', 'text'].includes(k)) continue;
    if (v == null || v === '') continue;
    const valStr = (typeof v === 'object') ? JSON.stringify(v) : String(v);
    metaRows += `<tr><th>${escapeHtml(k)}</th><td>${escapeHtml(valStr.slice(0, 400))}</td></tr>`;
  }
  const meta = metaRows ? `<table class="ent-meta-table">${metaRows}</table>` : '';
  const threadHtml = thread.length
    ? thread.map(m => {
        const stateTrans = m.metadata && m.metadata.state_transition;
        const conf = m.metadata && m.metadata.confidence_at_send;
        return `<div class="ent-msg">
          <div class="ent-msg-head">
            <span><b>${escapeHtml(m.from_agent || '')}</b>
              → ${escapeHtml(m.to_agent || m.to_agent_type || '')}</span>
            <span>${new Date(m.created_at).toLocaleString()}</span>
            ${stateTrans ? `<span class="state-pill">${stateTrans.from} → ${stateTrans.to}</span>` : ''}
            ${conf ? `<span class="conf-pill" title="confidence at send">a:${conf.a ?? '–'} b:${conf.b ?? '–'} r:${conf.r ?? '–'}</span>` : ''}
          </div>
          <div class="ent-msg-body">${escapeHtml(m.text || '')}</div>
        </div>`;
      }).join('')
    : '<p class="empty">No thread messages yet.</p>';
  const extrasHtml = extras
    ? `<div class="ent-extras"><h3>Extras</h3><pre>${escapeHtml(JSON.stringify(extras, null, 2))}</pre></div>`
    : '';
  return head + meta + '<h3>Thread</h3>' + threadHtml + extrasHtml;
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[c]));
}

// Wire entity-tab UI events.
document.getElementById('ent-apply')?.addEventListener('click', _writeEntityFiltersToUrl);
document.getElementById('ent-reset')?.addEventListener('click', () => {
  Router.navigate('/entities', { clear: ['type', 'status', 'participant', 'q', 'open_only'] });
});
document.getElementById('ent-type')?.addEventListener('change', e => {
  _populateStatusOptions(e.target.value);
});
document.getElementById('ent-detail-close')?.addEventListener('click', () => {
  Router.navigate('/entities');
});


// ================================================================
// Phase 5.3: Catalog tab — components browser + drill-down
// ================================================================

const catalogState = {
  rows: [],
  hasMore: false,
  selectedId: null,
};

function _readCatalogFiltersFromUrl() {
  const q = Router.current().query;
  const setVal = (id, v) => { const el = document.getElementById(id); if (el) el.value = v ?? ''; };
  setVal('cat-type', q.type);
  setVal('cat-plane', q.plane);
  setVal('cat-status', q.status === undefined ? 'active' : q.status);
  setVal('cat-q', q.q);
}

function _writeCatalogFiltersToUrl() {
  const get = id => (document.getElementById(id) || {}).value || '';
  Router.navigate('/catalog', {
    set: {
      type: get('cat-type'),
      plane: get('cat-plane'),
      status: get('cat-status'),
      q: get('cat-q'),
    },
  });
}

async function fetchCatalog() {
  _readCatalogFiltersFromUrl();
  const q = Router.current().query;
  const params = new URLSearchParams();
  ['type', 'plane', 'q'].forEach(k => { if (q[k]) params.set(k, q[k]); });
  // Default status = active when not in URL.
  const status = (q.status === undefined) ? 'active' : q.status;
  if (status) params.set('status', status);
  params.set('limit', '200');
  try {
    const res = await fetch(`/api/components?${params}`);
    const data = await res.json();
    catalogState.rows = data.components || [];
    catalogState.hasMore = data.has_more;
    renderCatalog();
  } catch (e) {
    console.error('fetchCatalog failed:', e);
    document.getElementById('cat-items').innerHTML =
      '<li class="empty">Failed to load components.</li>';
  }
  const segs = Router.current().segments;
  if (segs[0] === 'catalog' && segs[1] === 'component' && segs[2]) {
    showComponentDetail(segs[2]);
  }
}

function renderCatalog() {
  const $items = document.getElementById('cat-items');
  const $count = document.getElementById('cat-count');
  $count.textContent = `${catalogState.rows.length}${catalogState.hasMore ? '+' : ''}`;
  if (!catalogState.rows.length) {
    $items.innerHTML = '<li class="empty">No components match the filters.</li>';
    return;
  }
  $items.innerHTML = '';
  for (const c of catalogState.rows) {
    const li = document.createElement('li');
    li.className = 'cat-row';
    li.dataset.id = c.id;
    if (catalogState.selectedId === c.id) li.classList.add('selected');
    const planes = (c.planes || []).map(p =>
      `<span class="plane-pill plane-${p}">${p}</span>`).join(' ');
    const ec = c.edge_count || {bound: 0, catalog: 0, dangling: 0};
    li.innerHTML = `
      <div class="cat-row-top">
        <span class="cat-name">${escapeHtml(c.canonical_name || c.id)}</span>
        <span class="cat-type">${c.component_type}</span>
        <span class="cat-status status-${c.status}">${c.status}</span>
      </div>
      <div class="cat-display">${escapeHtml(c.display_name || '')}</div>
      <div class="cat-row-meta">
        ${planes || '<span class="muted">no attributions</span>'}
        <span class="muted">attrs:${c.attribution_count || 0} ·
          edges b:${ec.bound} c:${ec.catalog} d:${ec.dangling}</span>
      </div>
    `;
    li.addEventListener('click', () => {
      Router.navigate(`/catalog/component/${c.id}`);
    });
    $items.appendChild(li);
  }
}

async function showComponentDetail(componentId) {
  catalogState.selectedId = componentId;
  renderCatalog();
  const $title = document.getElementById('cat-detail-title');
  const $body = document.getElementById('cat-detail-body');
  $title.textContent = componentId.slice(0, 8) + '…';
  $body.innerHTML = '<p class="empty">Loading…</p>';
  try {
    const res = await fetch(`/api/component/${encodeURIComponent(componentId)}/drilldown`);
    if (!res.ok) {
      $body.innerHTML = `<p class="empty">Not found (${res.status}).</p>`;
      return;
    }
    const data = await res.json();
    $body.innerHTML = _renderComponentDetailHtml(data);
  } catch (e) {
    console.error('showComponentDetail failed:', e);
    $body.innerHTML = '<p class="empty">Failed to load.</p>';
  }
}

function _renderComponentDetailHtml(data) {
  const { component, attributions = [], edges = {}, flows = [], resources = [] } = data;
  const c = component;
  const planes = (c.planes || []).map(p =>
    `<span class="plane-pill plane-${p}">${p}</span>`).join(' ');
  const head = `<h3>${escapeHtml(c.canonical_name || '')}</h3>
    <div class="muted">${escapeHtml(c.display_name || '')}</div>
    <div class="muted">${c.component_type} · ${c.status}</div>
    <div>${planes}</div>`;
  // Phase 10.7: description is the dense embed-target field, distinct
  // from doc_md (which is multi-paragraph human-render). Render desc
  // first as a tight italic blurb, then doc_md below for fuller prose.
  const desc = c.description
    ? `<div class="cat-desc"><em>${escapeHtml(c.description)}</em></div>`
    : '';
  const doc = c.component_doc_md
    ? `<h4>Doc</h4><div class="cat-doc">${
        DOMPurify.sanitize(marked.parse(c.component_doc_md))
      }</div>`
    : '';
  const slice = c.source_slice
    ? `<h4>Slice</h4><pre class="cat-slice">${escapeHtml(JSON.stringify(c.source_slice, null, 2))}</pre>`
    : '';
  const attrsHtml = attributions.length
    ? '<h4>Attributions</h4><ul class="cat-attrs">' +
      attributions.map(a =>
        `<li><b>${escapeHtml(a.resource_type)}</b>: ${escapeHtml(a.identifier)}
          <span class="muted">[${a.plane}]</span></li>`).join('') + '</ul>'
    : '';
  const edgeBucketHtml = (label, rows) => rows.length ? (
    `<h5>${label} (${rows.length})</h5><ul>` +
    rows.map(e =>
      `<li><b>${escapeHtml(e.edge_type)}</b> ${escapeHtml(e.identifier)}
        <span class="muted">→ ${e.to_component_id || '?'}</span></li>`
    ).join('') + '</ul>'
  ) : '';
  // Phase 7.4.2: catalog rows have a noun-form `kind` + identifier and
  // are owned by the component itself, so the "→ to_component_id" arrow
  // doesn't apply — render kind + identifier + confidence inline.
  const catalogBucketHtml = (rows) => rows.length ? (
    `<h5>Catalog (${rows.length})</h5><ul class="cat-catalog-list">` +
    rows.map(c => {
      const kind = c.catalog_kind || c.kind || c.edge_type || '';
      const conf = c.confidence != null
        ? ` <span class="muted">· conf ${Number(c.confidence).toFixed(2)}</span>`
        : '';
      return `<li><span class="cat-kind">${escapeHtml(kind)}</span>
        <code>${escapeHtml(c.identifier)}</code>${conf}</li>`;
    }).join('') + '</ul>'
  ) : '';
  const edgesHtml = '<h4>Edges</h4>' +
    edgeBucketHtml('Bound in', edges.bound_in || []) +
    edgeBucketHtml('Bound out', edges.bound_out || []) +
    catalogBucketHtml(edges.catalog || []) +
    edgeBucketHtml('Dangling out', edges.dangling_out || []);
  const flowsHtml = flows.length
    ? '<h4>Flows</h4><ul>' +
      flows.map(f =>
        `<li>catalog <code>${f.incoming_catalog_id.slice(0, 8)}</code> →
          outgoing <code>${f.outgoing_edge_id.slice(0, 8)}</code></li>`
      ).join('') + '</ul>'
    : '';
  const resHtml = resources.length
    ? '<h4>Source resources</h4><ul>' +
      resources.map(r =>
        `<li><b>${r.plane}/${r.resource_type}</b>: ${escapeHtml(r.identifier)}</li>`
      ).join('') + '</ul>'
    : '';
  return head + desc + doc + slice + attrsHtml + edgesHtml + flowsHtml + resHtml;
}

document.getElementById('cat-apply')?.addEventListener('click', _writeCatalogFiltersToUrl);
document.getElementById('cat-reset')?.addEventListener('click', () => {
  Router.navigate('/catalog', { clear: ['type', 'plane', 'status', 'q'] });
});
document.getElementById('cat-detail-close')?.addEventListener('click', () => {
  Router.navigate('/catalog');
});


// ================================================================
// Phase 5.9: Insights tab — agent self-improvement triage
// ================================================================

const insightsState = { rows: [] };

function _readInsightFiltersFromUrl() {
  const q = Router.current().query;
  const setVal = (id, v) => { const el = document.getElementById(id); if (el) el.value = v ?? ''; };
  setVal('ins-status', q.status === undefined ? 'open' : q.status);
  setVal('ins-kind', q.kind);
  setVal('ins-target', q.target);
  setVal('ins-agent', q.agent_id);
}

function _writeInsightFiltersToUrl() {
  const get = id => (document.getElementById(id) || {}).value || '';
  Router.navigate('/insights', {
    set: {
      status: get('ins-status'),
      kind: get('ins-kind'),
      target: get('ins-target'),
      agent_id: get('ins-agent'),
    },
  });
}

async function fetchInsights() {
  _readInsightFiltersFromUrl();
  const q = Router.current().query;
  const params = new URLSearchParams();
  ['status', 'kind', 'target', 'agent_id'].forEach(k => {
    if (q[k]) params.set(k, q[k]);
  });
  // Default: show open if no filter set in URL.
  if (!q.status && !params.has('status')) params.set('status', 'open');
  params.set('limit', '200');
  try {
    const res = await fetch(`/api/insights?${params}`);
    const data = await res.json();
    insightsState.rows = data.insights || [];
    renderInsights();
  } catch (e) {
    console.error('fetchInsights failed:', e);
    document.getElementById('ins-items').innerHTML =
      '<li class="empty">Failed to load insights.</li>';
  }
}

function renderInsights() {
  const $items = document.getElementById('ins-items');
  const $count = document.getElementById('ins-count');
  $count.textContent = `${insightsState.rows.length}`;
  if (!insightsState.rows.length) {
    $items.innerHTML = '<li class="empty">No insights match the filters.</li>';
    return;
  }
  $items.innerHTML = '';
  for (const r of insightsState.rows) {
    const li = document.createElement('li');
    li.className = `ins-row ins-status-${r.status}`;
    const evidenceHtml = r.evidence
      ? `<details><summary>evidence</summary><pre>${escapeHtml(JSON.stringify(r.evidence, null, 2))}</pre></details>`
      : '';
    const triageHtml = `
      <div class="ins-triage">
        <button data-action="investigating" data-id="${r.id}" title="Mark investigating">🔍</button>
        <button data-action="promoted"     data-id="${r.id}" title="Promoted into prompt/doc">✅</button>
        <button data-action="wontfix"      data-id="${r.id}" title="Won't fix">🚫</button>
        <button data-action="open"         data-id="${r.id}" title="Reopen">↩</button>
      </div>`;
    const triageNote = r.triage_note
      ? `<div class="ins-note"><b>note</b>: ${escapeHtml(r.triage_note)}</div>`
      : '';
    li.innerHTML = `
      <div class="ins-row-top">
        <span class="ins-kind ins-kind-${r.kind}">${r.kind}</span>
        <span class="ins-status-pill">${r.status}</span>
        <span class="ins-target">${escapeHtml(r.target)}</span>
        <span class="ins-agent">${escapeHtml(r.agent_id)}</span>
        <span class="ins-ts">${new Date(r.created_at).toLocaleString()}</span>
      </div>
      <div class="ins-body">${escapeHtml(r.body)}</div>
      ${evidenceHtml}
      ${triageNote}
      ${triageHtml}
    `;
    $items.appendChild(li);
  }
}

document.getElementById('ins-apply')?.addEventListener('click', _writeInsightFiltersToUrl);
document.getElementById('ins-reset')?.addEventListener('click', () => {
  Router.navigate('/insights', { clear: ['status', 'kind', 'target', 'agent_id'] });
});

// Delegated triage clicks.
document.getElementById('ins-items')?.addEventListener('click', async e => {
  const btn = e.target.closest('[data-action]');
  if (!btn) return;
  const id = btn.dataset.id;
  const action = btn.dataset.action;
  const note = action === 'wontfix' || action === 'promoted'
    ? prompt(`Optional note for status='${action}':`, '') || null
    : null;
  try {
    const res = await fetch(`/api/insight/${encodeURIComponent(id)}/triage`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ status: action, triage_note: note }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      alert(`Failed: ${err.detail || res.status}`);
      return;
    }
    fetchInsights();
  } catch (err) {
    console.error('triage failed:', err);
  }
});
