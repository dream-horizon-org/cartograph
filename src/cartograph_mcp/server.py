"""Cartograph MCP Server — proper MCP protocol over streamable HTTP.

Phase 0: action_items, chat, broadcast tools.
More tools added in subsequent phases.

Agents connect via .mcp.json like:
  {
    "mcpServers": {
      "cartograph-db": { "type": "http", "url": "http://localhost:8100/mcp" }
    }
  }
"""

import logging
import os
import sys
from typing import Any

from mcp.server.fastmcp import FastMCP

from shared.db import init_pool, close_pool, execute, execute_one
from shared.migrations import run_migrations
from cartograph_mcp.tools import action_items, chat, broadcast, secrets
from cartograph_mcp.tools import tasks as tasks_tool
from cartograph_mcp.tools import resources as resources_tool
from cartograph_mcp.tools import agent_lifecycle
from cartograph_mcp.tools import clarification as clarification_tool
from cartograph_mcp.tools import components as components_tool
from cartograph_mcp.tools import consolidation as consolidation_tool
from cartograph_mcp.tools import mutation as mutation_tool
from cartograph_mcp.tools import notifications as notifications_tool
from cartograph_mcp.tools import proxy as proxy_tool
from cartograph_mcp.tools import search as search_tool
from cartograph_mcp.tools import sleep as sleep_tool
from cartograph_mcp.tools import insights as insights_tool
from cartograph_mcp.tools import terminal_acks as terminal_acks_tool
from cartograph_mcp.tools import catalogs as catalogs_tool
from cartograph_mcp.tools import call_batch as call_batch_tool

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)

# Create the MCP server. Streamable HTTP listens at /mcp endpoint.
mcp = FastMCP("cartograph-db", host="0.0.0.0", port=8100)

# Phase 5.10: install the per-call audit wrapper BEFORE any @mcp.tool()
# decorator runs. Every subsequent tool registration gets transparently
# wrapped — no per-tool annotation needed. Records (agent, tool, args
# hash, status, duration) to mcp_audit. Failures swallowed.
from cartograph_mcp.audit import install as _install_audit
_install_audit(mcp)

# Import AgentManager as a library — the MCP server instantiates its own
# for the create_agent tool. Same DB state, different Python instance
# than the one running the invoke loop. That's fine: AgentManager is stateless
# with respect to DB (all state lives in agent_runs table).
from agent_management.agent_manager import AgentManager

_DEFAULT_WORKSPACE_ROOT = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "workspaces"
)
_DEFAULT_MCP_CONFIG = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "mcp_servers.yaml"
)
_agent_manager_for_spawn = AgentManager(
    workspace_root=_DEFAULT_WORKSPACE_ROOT,
    mcp_config_path=_DEFAULT_MCP_CONFIG,
)


def _get_agent_type(agent_id: str) -> str:
    """Look up an agent's type; raise if not found."""
    row = execute_one(
        "SELECT agent_type FROM agent_runs WHERE agent_id = %s",
        (agent_id,),
    )
    if row is None:
        raise ValueError(f"Agent {agent_id} not found")
    return row["agent_type"]


# ============ ACTION ITEMS ============

@mcp.tool()
def get_action_items_summary(agent_id: str) -> dict[str, int]:
    """Quick counts of all pending action items for this agent.

    Phase 7.4.5: response is now a uniform `dict[str, int]` — every
    value is a count. Pre-7.4.5 it carried a `proxied: list[dict]`
    field whose mixed type confused MCP-client pydantic inference and
    crashed every wake (`Input should be a valid integer
    [type=int_type, input_value=[], input_type=list]`). The per-proxy
    rich breakdown (proxy_agent_id, depth, item lists) moved to
    `get_action_items_detail` — call it when `proxied_count > 0`.

    Returns:
      consolidations_pending, tasks_pending, clarifications_pending,
      unacked_chats, unacked_broadcasts, terminal_pending_ack,
      proxied_count — all ints.

    Call this FIRST on every wake-up to see what needs attention.
    """
    agent_type = _get_agent_type(agent_id)
    return action_items.get_action_items_summary(agent_id, agent_type)


@mcp.tool()
def get_action_items_detail(agent_id: str) -> dict[str, Any]:
    """Full rows for every pending action item across all categories.

    Returns a dict with keys: consolidations, tasks, clarifications,
    chats, broadcasts — each a list of full row objects.

    Call this after get_action_items_summary to get details on items
    you plan to address.
    """
    agent_type = _get_agent_type(agent_id)
    return action_items.get_action_items_detail(agent_id, agent_type)


# ============ CHAT ============

@mcp.tool()
def send_chat(from_agent_id: str, to_agent_id: str, message: str) -> dict[str, Any]:
    """Send a chat message.

    Restrictions: agents can only message 'admin'; admin can message any agent.
    Use this for free-form communication with the admin user.

    Returns the inserted communication row.
    """
    return chat.send_chat(from_agent_id, to_agent_id, message)


@mcp.tool()
def ack_chats(agent_id: str, communication_ids: list[str]) -> dict[str, int]:
    """Acknowledge specific chat messages by ID.

    Selective ack — agent chooses which messages to mark as read.
    Only affects messages where to_agent = agent_id, type = 'chat'.

    Returns: {"acked": <count>}.
    """
    count = chat.ack_chats(agent_id, communication_ids)
    return {"acked": count}


@mcp.tool()
def get_unacked_chats(agent_id: str) -> dict[str, list]:
    """Get all unacked chat messages addressed to this agent.

    Returns: {"messages": [...]}.
    """
    return {"messages": chat.get_unacked_chats(agent_id)}


@mcp.tool()
def get_chat_history(agent_id: str, page: int = 1, limit: int = 20) -> dict[str, list]:
    """Get paginated chat history involving this agent.

    Returns messages where agent is from_agent OR to_agent, ordered newest-first.
    Default page=1, limit=20.

    Returns: {"messages": [...]}.
    """
    return {"messages": chat.get_chat_history(agent_id, page, limit)}


# ============ BROADCAST ============

@mcp.tool()
def send_broadcast(
    from_agent_id: str,
    to_agent_type: str,
    message: str,
    persistent: bool = False,
) -> dict[str, Any]:
    """Send a broadcast to all agents of a type.

    Restriction: only orchestrator and admin can broadcast.

    persistent: default False — forward-only, seen only by agents existing
    at send time. Set True for standing policy that should also apply to
    agents spawned later (e.g. 'all SMEs use merge threshold 0.9 from now').

    Returns the inserted communication row.
    """
    return broadcast.send_broadcast(from_agent_id, to_agent_type, message, persistent)


@mcp.tool()
def ack_broadcast(agent_id: str, communication_id: str) -> dict[str, Any]:
    """Acknowledge a broadcast message.

    Once acked, trigger manager stops re-invoking this agent for this broadcast.
    Idempotent — safe to call multiple times.
    """
    result = broadcast.ack_broadcast(agent_id, communication_id)
    return result or {"status": "already_acked"}


@mcp.tool()
def get_unacked_broadcasts(agent_id: str) -> dict[str, list]:
    """Get broadcast messages this agent hasn't acked yet.

    Returns: {"messages": [...]}.
    """
    agent_type = _get_agent_type(agent_id)
    return {"messages": broadcast.get_unacked_broadcasts(agent_id, agent_type)}


@mcp.tool()
def update_broadcast_persistence(
    agent_id: str, communication_id: str, persistent: bool
) -> dict[str, Any]:
    """Phase 5.7: flip is_persistent on an existing broadcast.

    Admin/orchestrator only. Use when a broadcast that started as a
    quick fix turns out to be standing policy (or vice versa).
    Toggling OFF leaves existing acks intact — only future scanner
    reads + newly-spawned agents see the change.

    Returns the updated communication row.
    """
    return broadcast.update_broadcast_persistence(agent_id, communication_id, persistent)


# ============ SECRETS ============

@mcp.tool()
def put_secret(agent_id: str, plane: str, key: str, value: str) -> dict[str, Any]:
    """Store a credential for a plane. ONLY the orchestrator can write secrets.

    Args:
        agent_id: The caller's agent_id (must be an orchestrator).
        plane: The plane this credential is for (e.g., 'github', 'cloud').
        key: Credential name (e.g., 'github_token', 'aws_access_key').
        value: The secret value to store. Upserts on (plane, key).

    Returns: the stored row metadata (NOT the value).
    """
    return secrets.put_secret(agent_id, plane, key, value)


@mcp.tool()
def get_secret(agent_id: str, plane: str, key: str) -> dict[str, Any]:
    """Read a specific secret value. Any active agent can read.

    Args:
        agent_id: The caller's agent_id.
        plane: The plane the credential is for.
        key: The credential name.

    Returns: {plane, key, value, created_at, updated_at}, or null if not found.
    """
    result = secrets.get_secret(agent_id, plane, key)
    return result or {"error": "secret not found"}


@mcp.tool()
def list_secrets_for_plane(agent_id: str, plane: str) -> dict[str, list]:
    """List all secret KEYS for a plane (values NOT returned for security).

    Use this to discover what credentials are available, then call
    get_secret() for the ones you need.
    """
    return {"secrets": secrets.list_secrets_for_plane(agent_id, plane)}


@mcp.tool()
def delete_secret(agent_id: str, plane: str, key: str) -> dict[str, Any]:
    """Delete a credential. ONLY the orchestrator can delete secrets.

    Returns: {"deleted": bool}.
    """
    return secrets.delete_secret(agent_id, plane, key)


# ============ TASKS ============

@mcp.tool()
def create_task(owner_agent_id: str, worker_agent_id: str, description: str) -> dict[str, Any]:
    """Create a task and assign it to a worker agent.

    Only orchestrator (or 'admin') can create tasks.
    Task starts in state 'BW' (Blocked-on-Worker — worker's turn).

    Returns the created task row.
    """
    return tasks_tool.create_task(owner_agent_id, worker_agent_id, description)


@mcp.tool()
def respond_task(
    agent_id: str,
    task_id: str,
    message: str,
    new_status: str,
    blocker_detail: str | None = None,
) -> dict[str, Any]:
    """Respond to a task with a message AND a state transition.

    States: BW (worker's turn), BO (owner's turn, blocker raised),
            WD (worker done), TC (task completed — terminal).

    Worker allowed transitions:
      BW → BO (raise blocker — pass blocker_detail)
      BW → WD (mark complete)

    Owner allowed transitions:
      BO → BW (resolve blocker, back to worker)
      BO → TC (close task directly)
      WD → BW (reject, send back to worker)
      WD → TC (accept — task complete)

    Every response MUST include a valid state change. Cannot respond without moving.
    """
    return tasks_tool.respond_task(agent_id, task_id, message, new_status, blocker_detail)


@mcp.tool()
def raise_blocker(agent_id: str, task_id: str, blocker_detail: str) -> dict[str, Any]:
    """Shortcut: worker raises a blocker (BW → BO).

    Equivalent to respond_task(new_status='BO', blocker_detail=...) with a
    standardised message. Use this when you can't proceed — orchestrator
    will see it and unblock you.
    """
    return tasks_tool.raise_blocker(agent_id, task_id, blocker_detail)


@mcp.tool()
def get_my_tasks(agent_id: str) -> dict[str, list]:
    """Get all non-terminal tasks where you are owner or worker.

    Returns tasks with status BW/BO/WD (not TC). Ordered by most recent update.
    """
    return {"tasks": tasks_tool.get_my_tasks(agent_id)}


@mcp.tool()
def get_task_thread(
    task_id: str, agent_id: str, page: int = 1, limit: int = 50
) -> dict[str, list]:
    """Get the conversation thread for a task.

    Scoping: only the task's owner or worker can read.
    Paginated, oldest-first (default limit=50).
    """
    return {"messages": tasks_tool.get_task_thread(task_id, agent_id, page, limit)}


# ============ RESOURCES ============

@mcp.tool()
def upsert_resource(
    agent_id: str,
    plane: str,
    resource_type: str,
    identifier: str,
    access_desc: str = "",
    metadata: dict | None = None,
) -> dict[str, Any]:
    """Register a discovered resource. ITERATOR-ONLY.

    Idempotent on (plane, resource_type, identifier). If the same resource
    already exists, only access_desc and metadata are updated; status and
    created_at are preserved.

    Iterators can only write resources for their own plane.

    Args:
        plane: one of github, deploy, cloud, telemetry, config
        resource_type: e.g., 'repo', 'r53_chain', 'k8s_workload', 'rds',
                       'elasticache', 'lambda', 'service' (Datadog), etc.
        identifier: stable identifier (e.g., 'dream11/feeds-agg-v2',
                    'feeds-agg-v2.dream11.local', 'sg-3e743b49')
        access_desc: brief note on how to access it ('clone via SSH',
                     'kubectl get', 'aws describe-asg')
        metadata: any extra context (pre-resolved chain data, cluster name, etc.)

    Returns: the stored row.
    """
    return resources_tool.upsert_resource(
        agent_id, plane, resource_type, identifier, access_desc, metadata
    )


@mcp.tool()
def upsert_resources_bulk(
    agent_id: str,
    plane: str,
    items: list[dict],
) -> dict[str, Any]:
    """Bulk upsert many resources for one plane in a single transaction. ITERATOR-ONLY.

    Use this instead of calling upsert_resource N times when you have enumerated
    many resources at once — one round-trip, one transaction, same idempotency
    on (plane, resource_type, identifier).

    Args:
        plane: one of github, deploy, cloud, telemetry, config
        items: list of dicts, each with keys:
               - resource_type (required)
               - identifier (required)
               - access_desc (optional)
               - metadata (optional dict)

    Returns: {"inserted_or_updated": N, "ids": [uuid, ...]}
    Limit: 5000 items per call.
    """
    return resources_tool.upsert_resources_bulk(agent_id, plane, items)


@mcp.tool()
def get_resource(agent_id: str, resource_id: str) -> dict[str, Any]:
    """Read a single resource by id. Any active agent can read."""
    return resources_tool.get_resource(agent_id, resource_id)


@mcp.tool()
def list_resources_for_plane(agent_id: str, plane: str) -> dict[str, list]:
    """List all resources registered for a plane. Any active agent can read.

    Useful for iterators checking what's already registered (to avoid
    duplicate work across re-invocations) and for orchestrator monitoring.
    """
    return {"resources": resources_tool.list_resources_for_plane(agent_id, plane)}


@mcp.tool()
def list_all_resources(agent_id: str, status: str | None = None) -> dict[str, list]:
    """List all resources across all planes, optionally filtered by status.

    status values: pending, assigned, done.
    """
    return {"resources": resources_tool.list_all_resources(agent_id, status)}


@mcp.tool()
def get_resource_counts(agent_id: str) -> dict[str, Any]:
    """Get counts of resources grouped by plane and status.

    Returns: {"by_plane_status": [{plane, status, cnt}, ...]}
    """
    return resources_tool.get_resource_counts(agent_id)


@mcp.tool()
def mark_resource_done(agent_id: str, resource_id: str) -> dict[str, Any]:
    """Mark a resource as done (SME only, for its own resource).

    Called by an SME when materialisation of this resource is complete.
    Validates agent is the SME assigned to this resource.
    """
    return resources_tool.mark_resource_done(agent_id, resource_id)


@mcp.tool()
def bulk_spawn_smes(
    agent_id: str,
    plane: str,
    resource_ids: list[str] | None = None,
    all_pending: bool = False,
    task_description: str | None = None,
) -> dict[str, Any]:
    """Spawn N SMEs for a plane in one call. ORCHESTRATOR-ONLY.

    Writes agent_runs rows + RCA reservation rows (component_id=NULL) +
    flips matching resources.status to 'assigned'. Optionally creates one
    task per SME (owner=caller, worker=new SME, status=BW) as the initial
    wake signal.

    Filters: exactly one of resource_ids or all_pending=True required
    (refuses blank-wipe). Skips resources already having an RCA row.

    Returns: {spawned: N, skipped_already_assigned: [...], items: [...]}
    """
    return resources_tool.bulk_spawn_smes(
        agent_id=agent_id,
        plane=plane,
        agent_manager=_agent_manager_for_spawn,
        resource_ids=resource_ids,
        all_pending=all_pending,
        task_description=task_description,
    )


@mcp.tool()
def decommission_agent(
    agent_id: str,
    target_agent_id: str,
    reason: str,
    resource_action: str = "leave",
) -> dict[str, Any]:
    """Flip an agent to 'decommissioned'. ORCHESTRATOR-ONLY.

    resource_action controls what happens to the agent's assigned resources
    (via RCA):
      - 'leave'  → keep the RCA row as an orphan (debugging only)
      - 'reset'  → delete RCA row + flip resource back to 'pending' for re-spawn
      - 'reject' → delete RCA row + mark resource 'rejected' with audit trail

    Refuses self-decommission. Reason is required.
    """
    return agent_lifecycle.decommission_agent(
        agent_id, target_agent_id, reason, resource_action
    )


@mcp.tool()
def decommission_agents_bulk(
    agent_id: str,
    reason: str,
    agent_ids: list[str] | None = None,
    agent_type: str | None = None,
    resource_action: str = "leave",
) -> dict[str, Any]:
    """Bulk variant — one transaction, cohort-wide teardown. ORCHESTRATOR-ONLY.

    At least one of agent_ids (explicit list) or agent_type ('sme'/'iterator'/
    'resolver') required. Refuses 'orchestrator' as a target type. Same
    resource_action semantics as decommission_agent.
    """
    return agent_lifecycle.decommission_agents_bulk(
        agent_id=agent_id,
        reason=reason,
        agent_ids=agent_ids,
        agent_type=agent_type,
        resource_action=resource_action,
    )


@mcp.tool()
def decommission_component(
    agent_id: str, component_id: str, reason: str
) -> dict[str, Any]:
    """Soft-delete a component (status='decommissioned'). ORCHESTRATOR-ONLY.

    Attributions and edges are left as-is (mirrors merge semantics).
    Refuses if component is already decommissioned.
    """
    return agent_lifecycle.decommission_component(agent_id, component_id, reason)


@mcp.tool()
def decommission_components_bulk(
    agent_id: str, component_ids: list[str], reason: str
) -> dict[str, Any]:
    """Bulk variant — one transaction. Requires explicit component_ids list.
    Refuses blank-wipe (no "all components" filter).
    """
    return agent_lifecycle.decommission_components_bulk(
        agent_id, component_ids, reason
    )


# ============ COMPONENT GRAPH ============


@mcp.tool()
def upsert_component(
    agent_id: str, component_data: dict[str, Any]
) -> dict[str, Any]:
    """Create or update this SME's single active component. SME-ONLY.

    First call creates the component + fills the SME's RCA reservation row
    (component_id NULL → new). Subsequent calls update in place.
    Enforces 1-active-component-per-SME (use consolidation splits for more).

    component_data keys:
      canonical_name (required), display_name (required),
      component_type (required; application/database/cache/queue/lambda/cron/
        external-service/library/infrastructure),
      confidence (default 1.0),
      metadata (dict, optional),
      description (Phase 10.7, optional, ≤400 chars soft cap) — DENSE
        machine-readable summary; THE embed-target for vector_search
        ranking. REPLACE-on-provide / None=preserve / ""=clear.
      component_doc_md (optional, no length cap) — multi-paragraph
        human-readable prose; renders in graph-viz hover popup. NOT
        embedded post-Phase-10.7. REPLACE/preserve/clear semantics
        identical to description.
      source_slice (dict keyed by resource_id, optional).
    """
    return components_tool.upsert_component(agent_id, component_data)


@mcp.tool()
def upsert_attribution(
    agent_id: str, component_id: str, attribution_data: dict[str, Any]
) -> dict[str, Any]:
    """Attach evidence to this SME's component. SME-ONLY (own component).

    Idempotent on (plane, resource_type, identifier). Refuses if the same
    tuple is already attributed to a different component (resolve via
    consolidation in Phase 3).

    attribution_data keys:
      plane (github/deploy/cloud/telemetry/config), resource_type, identifier,
      evidence (text), confidence (default 1.0), metadata (dict).

    For >3 attributions on the same component, prefer
    `upsert_attributions_bulk` — atomic, fewer round-trips.
    """
    return components_tool.upsert_attribution(agent_id, component_id, attribution_data)


@mcp.tool()
def upsert_attributions_bulk(
    agent_id: str,
    component_id: str,
    attributions: list[dict[str, Any]],
) -> dict[str, Any]:
    """[Phase 7.4.12] Atomic-with-pre-validation bulk upsert of N
    attributions on this SME's component. Round 2 #3 of token
    optimisation: replaces N sequential `upsert_attribution` calls
    with one MCP call + one DB transaction. Caller must own component_id.

    Each row in `attributions`: same keys as `upsert_attribution`'s
    `attribution_data` (plane, resource_type, identifier, evidence?,
    confidence?, metadata?). All rows target the same component_id
    (caller's own).

    Pre-validate every row BEFORE opening transaction. If any row
    fails pre-check, return per-row errors and write NOTHING (atomic).
    If all rows pass, commit all in one transaction.

    Returns:
      {"committed": True,  "applied": N, "rows": [...]}
      {"committed": False, "applied": 0, "errors": {<row_idx>: <reason>}}

    Cross-component conflicts (any row's (plane, type, identifier)
    already belongs to a different component) → reject the whole
    batch with per-row error pointing at the conflicting component_id.

    Max 500 attributions per call.
    """
    return components_tool.upsert_attributions_bulk(agent_id, component_id, attributions)


@mcp.tool()
def create_edge(agent_id: str, edge_data: dict[str, Any]) -> dict[str, Any]:
    """[Phase 3.9 backward-compat shim] Record a bound edge from this
    SME's component. Prefer upsert_edge_outbound for new code.

    Equivalent to upsert_edge_outbound with both endpoints set.
    Self-loops permitted (Phase 7.3 dropped the DB CHECK; Phase 7.4.4
    dropped the Python guard) — cron self-trigger / recursive
    component-level calls / service publish+consume on the same topic
    all model directly. Idempotent on (from, to, type, identifier).

    edge_data keys:
      source_id (your component — accepted as from_component_id alias),
      target_id (callee — accepted as to_component_id alias),
      edge_type, identifier, source_attr_id?, target_attr_id?, evidence,
      confidence, metadata.
    """
    return components_tool.create_edge(agent_id, edge_data)


@mcp.tool()
def upsert_edge_catalog(
    agent_id: str, edge_data: dict[str, Any]
) -> dict[str, Any]:
    """[Phase 3.9] Callee declares an exposed endpoint / consumed topic /
    accepted query. Writes a catalog row (from_component_id IS NULL),
    owned by this SME. SME-ONLY (must own to_component_id).

    Idempotent on (to_component_id, edge_type, identifier) WHERE
    from_component_id IS NULL — re-calling accumulates metadata + takes
    max confidence; never duplicates.

    edge_data keys: to_component_id (your component), edge_type
    (calls/reads_from/writes_to/triggers/publishes_to/consumes_from/runs_on),
    identifier (endpoint path / topic name / query template), metadata?,
    confidence?, target_attr_id?.

    When to use: applications/lambdas/external-services should declare
    every endpoint they expose and every topic they consume. DBs /
    caches / queues / object-stores typically don't bother — they
    accept arbitrary queries / writes.
    """
    return components_tool.upsert_edge_catalog(agent_id, edge_data)


@mcp.tool()
def upsert_edge_outbound(
    agent_id: str, edge_data: dict[str, Any]
) -> dict[str, Any]:
    """[Phase 3.9] Caller declares an outgoing edge. to_component_id may
    be set (bound edge) or NULL (dangling — target not yet identified).
    SME-ONLY (must own from_component_id). Caller permanently owns the
    row; filling to later via bind_edge does NOT shift ownership.

    Idempotent — re-calls accumulate metadata + take max confidence.
    Routing of ON CONFLICT depends on whether to_component_id is set:
      bound:    (from, to, type, identifier)   WHERE both non-null
      dangling: (from, type, identifier)       WHERE to IS NULL

    edge_data keys: from_component_id (your component), to_component_id?
    (omit/null for dangling), edge_type, identifier, source_attr_id?,
    target_attr_id?, evidence (array), confidence, metadata.

    When to use:
      - Bound: target component is known + (recommended) target's
        catalog has a matching row. Just bind directly.
      - Dangling: target's catalog is missing the identifier; write the
        outgoing with to=NULL + create_clarification to the callee.
        Once they add the catalog row, call bind_edge to set to.
      - For db/cache/queue/etc. callees with no catalog: bind freely;
        no clarification needed.
    """
    return components_tool.upsert_edge_outbound(agent_id, edge_data)


@mcp.tool()
def upsert_edges_outbound_bulk(
    agent_id: str, edges: list[dict[str, Any]]
) -> dict[str, Any]:
    """[Phase 7.4.12] Atomic-with-pre-validation bulk upsert of N
    outbound edges from this SME's components. Round 2 #3 of token
    optimisation.

    Each row mirrors `upsert_edge_outbound`'s edge_data shape:
      {from_component_id, to_component_id?, edge_type, identifier,
       metadata?, confidence?, source_attr_id?, target_attr_id?,
       evidence?}.

    Multiple `from_component_id`s allowed across rows — caller must
    own EACH of them via RCA. Pre-validate every row; if any fails,
    return per-row errors and write nothing. If all pass, commit all
    in one transaction.

    Bound + dangling rows can be mixed in the same batch — each row's
    ON CONFLICT routing depends on whether `to_component_id` is set
    (same as `upsert_edge_outbound`). Self-loops allowed (Phase 7.3).

    Returns:
      {"committed": True,  "applied": N, "rows": [...]}
      {"committed": False, "applied": 0, "errors": {<row_idx>: <reason>}}

    Max 500 edges per call.
    """
    return components_tool.upsert_edges_outbound_bulk(agent_id, edges)


@mcp.tool()
def bind_edge(
    agent_id: str, edge_id: str, to_component_id: str
) -> dict[str, Any]:
    """[Phase 3.9] Resolve a dangling outgoing edge by setting
    to_component_id. SME-ONLY (must own the edge's from_component_id).

    Refuses if a bound row with the resulting (from, to, type,
    identifier) already exists — caller chooses to merge metadata into
    that existing row + delete the dangling, or rename the dangling
    identifier. No silent merge.

    Use when a previously-dangling outgoing's target component appears
    in the graph (your hygiene pass / vector_search succeeded).
    """
    return components_tool.bind_edge(agent_id, edge_id, to_component_id)


@mcp.tool()
def delete_edge(agent_id: str, edge_id: str) -> dict[str, Any]:
    """[Phase 7.4.11] Owner-scoped, idempotent edge delete.

    Closes the gap where an SME ends up with two edges for the same
    logical dependency (e.g. one telemetry-discovered with bare hostname
    + one github-discovered with `host/dbname` suffix, post-merge) and
    needs to consolidate them into one canonical row. Without this tool
    the only options were leaving a stale duplicate or marking it via
    metadata.superseded_by_edge_id.

    Authorisation: caller must own the row's from_component_id. Catalog
    rows (from_component_id IS NULL — pre-Phase-7.4 remnants) refuse
    with reason='catalog_not_supported'. Use upsert_catalog deletion or
    decommission_component for those.

    Cascade: flows.outgoing_edge_id has ON DELETE CASCADE — flows
    anchored on the edge are removed atomically. attributions.source_attr_id
    / target_attr_id stay (FK ON DELETE SET NULL).

    Idempotent: deleting a non-existent edge_id returns
    {"deleted": False, "reason": "not_found"}.

    Returns:
      {"deleted": True,  "edge_id": <id>, "cascaded_flows": <int>}
      {"deleted": False, "edge_id": <id>, "reason": <"not_found"|"catalog_not_supported">}

    Common use case (post-merge edge dedup):
      A) upsert_edge_outbound on the canonical (richer-identifier) edge
         with the union of metadata from both planes.
      B) delete_edge on the leaner-identifier duplicate.
      Repeat per duplicate pair.
    """
    return components_tool.delete_edge(agent_id, edge_id)


@mcp.tool()
def delete_attribution(
    agent_id: str, attribution_id: str, reason: str | None = None
) -> dict[str, Any]:
    """[Phase 8.2] Owner-scoped, idempotent attribution delete.

    Closes the corrective-action gap from sme-daa3b7b3 insight (an
    SME wrote `outbound_db_host` as own attribution; the DB hostname
    should have been an EDGE to the DB component). Without this tool,
    the only recovery was leaving the wrong-shape row in place.

    Authorisation: caller must own the row's component_id via RCA.

    Cascade: edges.source_attr_id / target_attr_id pointing at this
    row → SET NULL (existing FK). Edge survives as a structural fact;
    only the evidence pointer is severed. Cross-owner edges are
    affected (callers who cited this attribution as target_attr_id);
    their edge.evidence JSONB still carries free-form context.

    Idempotent: deleting a non-existent attribution_id returns
    {"deleted": False, "id": <id>, "reason": "not_found"}.

    Optional `reason` surfaces in mcp_audit.args_hash for forensic
    queries (e.g. reason='wrong shape — converting to edge').

    Returns:
      {"deleted": True,  "id": <id>, "severed_edge_pointers": <int>}
      {"deleted": False, "id": <id>, "reason": "not_found"}
    """
    return components_tool.delete_attribution(agent_id, attribution_id, reason)


@mcp.tool()
def delete_catalog(
    agent_id: str, catalog_id: str, reason: str | None = None
) -> dict[str, Any]:
    """[Phase 8.2] Owner-scoped, idempotent catalog delete.

    Authorisation: caller must own the row's component_id via RCA.

    Cascade: flows.incoming_catalog_id has ON DELETE CASCADE — flows
    anchored on this catalog row are removed atomically. Bound caller
    edges bridged via (target, edge_type, identifier) are NOT FK-
    linked — they surface via get_unmatched_callers on the next
    hygiene sweep.

    Idempotent: missing catalog_id → {"deleted": False, "reason": "not_found"}.

    Optional `reason` surfaces in mcp_audit.args_hash (e.g.
    reason='deprecated since YYYY-MM').

    Returns:
      {"deleted": True,  "id": <id>, "cascaded_flows": <int>}
      {"deleted": False, "id": <id>, "reason": "not_found"}
    """
    return catalogs_tool.delete_catalog(agent_id, catalog_id, reason)


@mcp.tool()
def delete_flow(
    agent_id: str, flow_id: str, reason: str | None = None
) -> dict[str, Any]:
    """[Phase 8.2] Owner-scoped, idempotent flow delete.

    Authorisation: caller must own the flow's component_id via RCA.

    No cascade — flows are leaf rows.

    Use case: you wired a flow with the wrong catalog→outgoing join
    and need to remove it (upsert_flow is set-based on the unique
    triple — calling it with a different triple ADDS a flow rather
    than replacing the wrong one).

    Idempotent: missing flow_id → {"deleted": False, "reason": "not_found"}.

    Returns:
      {"deleted": True,  "id": <id>}
      {"deleted": False, "id": <id>, "reason": "not_found"}
    """
    return components_tool.delete_flow(agent_id, flow_id, reason)


@mcp.tool()
def delete_unresolved(
    agent_id: str, unresolved_id: str, reason: str | None = None
) -> dict[str, Any]:
    """[Phase 8.2] Owner-scoped, idempotent unresolved delete.

    Authorisation: caller must own the row's found_in_component_id
    via RCA.

    No cascade — unresolved is a leaf row.

    Use case: SME flagged a reference as unresolved, later realised
    it was a typo / not actually a dependency, needs to remove it.

    Idempotent: missing unresolved_id → {"deleted": False, "reason": "not_found"}.

    Returns:
      {"deleted": True,  "id": <id>}
      {"deleted": False, "id": <id>, "reason": "not_found"}
    """
    return components_tool.delete_unresolved(agent_id, unresolved_id, reason)


@mcp.tool()
def delete_attributions_bulk(
    agent_id: str, attribution_ids: list[str]
) -> dict[str, Any]:
    """[Phase 8.3] Atomic-with-pre-validation bulk attribution delete.

    Per-row owner check via RCA. Missing ids silently skipped
    (idempotent). Cross-owner rows reject the WHOLE batch with per-row
    errors. Max 500 ids per call.

    Cascade: edges' source_attr_id / target_attr_id pointing at
    deleted rows go NULL (existing FK SET NULL).

    Returns {"committed": True, "applied": N, "rows": [...]}
        OR  {"committed": False, "applied": 0, "errors": {<idx>: <reason>}}
    """
    return components_tool.delete_attributions_bulk(agent_id, attribution_ids)


@mcp.tool()
def delete_catalogs_bulk(
    agent_id: str, catalog_ids: list[str]
) -> dict[str, Any]:
    """[Phase 8.3] Atomic-with-pre-validation bulk catalog delete.

    Per-row owner check via RCA. Missing ids skipped. Cascades flows
    (FK CASCADE on flows.incoming_catalog_id). Max 500.
    """
    return catalogs_tool.delete_catalogs_bulk(agent_id, catalog_ids)


@mcp.tool()
def delete_flows_bulk(
    agent_id: str, flow_ids: list[str]
) -> dict[str, Any]:
    """[Phase 8.3] Atomic-with-pre-validation bulk flow delete.

    Per-row owner check via RCA. Missing ids skipped. No cascade
    (flows are leaf rows). Max 500.
    """
    return components_tool.delete_flows_bulk(agent_id, flow_ids)


@mcp.tool()
def delete_edges_bulk(
    agent_id: str, edge_ids: list[str]
) -> dict[str, Any]:
    """[Phase 8.3] Atomic-with-pre-validation bulk edge delete.

    Per-row owner check on from_component_id (mirrors single delete_edge).
    Catalog rows (from IS NULL — pre-7.4 remnants) reject with
    'catalog_not_supported' on those rows. Missing ids skipped.
    Cascades flows (FK CASCADE on flows.outgoing_edge_id). Max 500.

    Companion to delete_edge — useful for post-merge dedup batches.
    """
    return components_tool.delete_edges_bulk(agent_id, edge_ids)


@mcp.tool()
def delete_unresolved_bulk(
    agent_id: str, unresolved_ids: list[str]
) -> dict[str, Any]:
    """[Phase 8.3] Atomic-with-pre-validation bulk unresolved delete.

    Per-row owner check via RCA on found_in_component_id. Missing ids
    skipped. No cascade. Max 500.
    """
    return components_tool.delete_unresolved_bulk(agent_id, unresolved_ids)


@mcp.tool()
def upsert_flows_bulk(
    agent_id: str,
    component_id: str,
    flows: list[dict[str, Any]],
) -> dict[str, Any]:
    """[Phase 8.4] Atomic-with-pre-validation bulk flow upsert on THIS
    SME's component. Each row: {incoming_catalog_id, outgoing_edge_id,
    metadata?, confidence?}. Idempotent on the unique triple.

    Pre-validates every row (catalog belongs to component, outgoing
    edge originates from component). All-or-nothing. Max 500.
    """
    return components_tool.upsert_flows_bulk(agent_id, component_id, flows)


@mcp.tool()
def insert_unresolved_bulk(
    agent_id: str, items: list[dict[str, Any]]
) -> dict[str, Any]:
    """[Phase 8.4] Atomic-with-pre-validation bulk unresolved insert.
    Idempotent on (found_in_component_id, reference_type, reference_value)
    per the new UNIQUE constraint — repeats bump attempts.

    Each row: {found_in_component_id, reference_type, reference_value,
    context?}. RCA-checks each found_in_component_id. Max 500.
    """
    return components_tool.insert_unresolved_bulk(agent_id, items)


@mcp.tool()
def ack_broadcasts_bulk(
    agent_id: str, communication_ids: list[str]
) -> dict[str, Any]:
    """[Phase 8.4] Bulk ack N broadcasts in one round-trip.

    Each id ack'd via INSERT ON CONFLICT DO NOTHING. Max 500.

    Returns {"committed": True, "applied": <newly-acked count>,
             "rows": [{communication_id, already_acked}]}.
    """
    from cartograph_mcp.tools import broadcast as broadcast_tool
    return broadcast_tool.ack_broadcasts_bulk(agent_id, communication_ids)


@mcp.tool()
def ack_terminals_bulk(
    agent_id: str, items: list[dict[str, Any]]
) -> dict[str, Any]:
    """[Phase 8.4] Bulk ack N terminal entities in one round-trip.

    Each item: {entity_type, entity_id}. Pre-validates participation +
    terminal-state for every item. Max 500. Atomic — if any item
    fails pre-validation, reject WHOLE batch.
    """
    return terminal_acks_tool.ack_terminals_bulk(agent_id, items)


@mcp.tool()
def get_components_bulk(
    agent_id: str, component_ids: list[str]
) -> dict[str, Any]:
    """[Phase 8.5] Multi-component bulk read. Returns
    `dict[component_id_str, component_row | None]` (None for missing
    ids). Open to all active agents. Max 500.
    """
    return components_tool.get_components_bulk(agent_id, component_ids)


@mcp.tool()
def get_attributions_bulk(
    agent_id: str, component_ids: list[str]
) -> dict[str, Any]:
    """[Phase 8.5] Multi-component bulk attribution read. Returns
    `dict[component_id_str, list[attribution_row]]` — one round-trip
    across N candidates for resolver triangulation. Open to all
    active agents. Max 500.
    """
    return components_tool.get_attributions_bulk(agent_id, component_ids)


@mcp.tool()
def get_component_edges_bulk(
    agent_id: str, component_ids: list[str]
) -> dict[str, Any]:
    """[Phase 8.5] Multi-component categorised edge read. Returns
    `dict[component_id_str, {incoming_bound, incoming_catalog,
    outgoing_bound, outgoing_dangling}]`. Open to all active agents.
    Max 500.
    """
    return components_tool.get_component_edges_bulk(agent_id, component_ids)


@mcp.tool()
def get_catalogs_bulk(
    agent_id: str, component_ids: list[str]
) -> dict[str, Any]:
    """[Phase 8.5] Multi-component bulk catalog read. Returns
    `dict[component_id_str, list[catalog_row]]`. Resolver triangulation
    use case: catalog overlap is a strong merge signal (Phase 7.4).
    Max 500.
    """
    return catalogs_tool.get_catalogs_bulk(agent_id, component_ids)


@mcp.tool()
def get_flows_bulk(
    agent_id: str, component_ids: list[str]
) -> dict[str, Any]:
    """[Phase 8.5] Multi-component bulk flow read. Returns
    `dict[component_id_str, list[flow_row]]`. Each row carries
    incoming_catalog_id + outgoing_edge_id. Open to all active
    agents. Max 500.
    """
    return components_tool.get_flows_bulk(agent_id, component_ids)


@mcp.tool()
def upsert_flow(
    agent_id: str,
    component_id: str,
    incoming_catalog_id: str,
    outgoing_edge_id: str,
    metadata: dict[str, Any] | None = None,
    confidence: float = 1.0,
) -> dict[str, Any]:
    """[Phase 3.9 + 7.4.2] Link an incoming catalog (a surface YOUR
    component exposes) to an outgoing edge inside one of YOUR
    components. Set-based (many-to-many): one catalog can fan out to
    multiple outgoings; multiple catalogs can share an outgoing. SME-
    ONLY (must own component_id; catalog must belong to component).

    Validates that catalog.component_id == component_id (Phase 7.4.2:
    incoming is a catalog row, not an edge) and
    outgoing_edge.from_component_id == component_id.

    Idempotent on (component_id, incoming_catalog_id, outgoing_edge_id).
    Re-call accumulates metadata + max confidence.

    Use during Edge Discovery: for each of your catalog rows (exposed
    surfaces), declare which of your outgoing edges fire when the
    surface is hit. Powers blast-radius / impact analysis.
    """
    return components_tool.upsert_flow(
        agent_id, component_id, incoming_catalog_id, outgoing_edge_id,
        metadata, confidence,
    )


@mcp.tool()
def get_flow(
    agent_id: str, component_id: str, incoming_catalog_id: str
) -> dict[str, list[dict[str, Any]]]:
    """[Phase 3.9 + 7.4.2] Outgoing edges fired when the catalog at
    incoming_catalog_id on component_id is hit. Open to all active
    agents."""
    return {
        "outgoing": components_tool.get_flow(agent_id, component_id, incoming_catalog_id)
    }


@mcp.tool()
def get_flow_inverse(
    agent_id: str, component_id: str, outgoing_edge_id: str
) -> dict[str, list[dict[str, Any]]]:
    """[Phase 3.9 + 7.4.2] Incoming catalog rows whose hit triggers this
    outgoing edge inside the component. Returns catalog rows (Phase
    7.4.2: incoming is a catalog, not an edge). Open to all active
    agents."""
    return {
        "incoming": components_tool.get_flow_inverse(agent_id, component_id, outgoing_edge_id)
    }


@mcp.tool()
def get_component_edges(
    agent_id: str, component_id: str
) -> dict[str, list[dict[str, Any]]]:
    """[Phase 3.9] Categorised view of all edges touching this component.
    Open to all active agents.

    Returns:
      incoming_bound    — bound edges into this component (callers)
      incoming_catalog  — this component's own catalog (exposed surfaces)
      outgoing_bound    — bound edges out (resolved targets)
      outgoing_dangling — bound edges out with target unresolved (to=NULL)

    Replaces the older get_edges() in new code; get_edges still works
    for the simpler {outbound, inbound} shape.
    """
    return components_tool.get_component_edges(agent_id, component_id)


@mcp.tool()
def insert_unresolved(
    agent_id: str, unresolved_data: dict[str, Any]
) -> dict[str, Any]:
    """Record a reference found but not resolved yet. SME-ONLY (own component).

    Keys: found_in_component_id (your component), reference_type,
    reference_value, context (dict).
    """
    return components_tool.insert_unresolved(agent_id, unresolved_data)


@mcp.tool()
def resolve_reference(
    agent_id: str, unresolved_id: str, resolved_to_component_id: str
) -> dict[str, Any]:
    """Mark an unresolved reference as resolved to a component.

    Open to any active agent — resolution is often cross-SME work (config
    SMEs resolve hostname refs, etc.). Refuses if already resolved or if
    the target component is decommissioned.
    """
    return components_tool.resolve_reference(
        agent_id, unresolved_id, resolved_to_component_id
    )


@mcp.tool()
def get_component(agent_id: str, component_id: str) -> dict[str, Any]:
    """Read any component. Open to all active agents."""
    return components_tool.get_component(agent_id, component_id)


@mcp.tool()
def get_attributions(
    agent_id: str, component_id: str
) -> dict[str, list[dict[str, Any]]]:
    """Read all attributions for a component. Open to all active agents."""
    return {"attributions": components_tool.get_attributions(agent_id, component_id)}


@mcp.tool()
def get_edges(agent_id: str, component_id: str) -> dict[str, list]:
    """Read all edges for a component (inbound + outbound). Open to all agents.

    Returns: {"outbound": [...], "inbound": [...]}
    """
    return components_tool.get_edges(agent_id, component_id)


@mcp.tool()
def get_unresolved(
    agent_id: str, component_id: str
) -> dict[str, list[dict[str, Any]]]:
    """Read unresolved references for a component. Open to all active agents."""
    return {"unresolved": components_tool.get_unresolved(agent_id, component_id)}


@mcp.tool()
def nominate_consolidation(
    agent_id: str,
    component_a_id: str,
    component_b_id: str | None,
    nomination_type: str,
    confidence: float,
    message: str,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """SME proposes a merge or split. Creates consolidation row (status=B2
    for merge, R for split) + communication. Merge requires component_b_id
    owned by a different SME. Split accepts component_b_id=None.
    Phase 4.1: metadata JSONB optional for structured tags (evidence,
    cosine scores, demo=true, etc.)."""
    return consolidation_tool.nominate_consolidation(
        agent_id, component_a_id, component_b_id, nomination_type,
        confidence, message, metadata,
    )


@mcp.tool()
def respond_consolidation(
    agent_id: str,
    consolidation_id: str,
    confidence: float,
    message: str,
    new_status: str,
) -> dict[str, Any]:
    """Nominator or nominated responds with updated confidence + state change.

    Valid transitions:
      agent_a (nominator):   B1 → B2 | B1 → R (only if r_conf set)
      agent_b (nominated):   B2 → B1 | B2 → R (only if r_conf set)

    Auto-escalation to R is handled by the auto_transitions scanner when
    both confidence scores breach thresholds — this tool only allows manual
    escalation after the resolver has already weighed in at least once.
    """
    return consolidation_tool.respond_consolidation(
        agent_id, consolidation_id, confidence, message, new_status,
    )


@mcp.tool()
def review_consolidation(
    agent_id: str,
    consolidation_id: str,
    r_confidence: float,
    message: str,
    new_status: str,
    mutation_assigned_to: str | None = None,
) -> dict[str, Any]:
    """Resolver-only. Review and decide.

    Valid transitions:
      R → B1/B2 (send back for more info)
      R → F     (reject — terminal)
      R → M     (approve — mutation_assigned_to REQUIRED)
                 merge: pick agent_a or agent_b (resolver picks the one
                        with more planes of attribution)
                 split: must be agent_a (enforced)
      MD → D    (Phase 4 completion ack)
    """
    return consolidation_tool.review_consolidation(
        agent_id, consolidation_id, r_confidence, message, new_status,
        mutation_assigned_to,
    )


@mcp.tool()
def get_my_proxy_items(
    agent_id: str,
    limit_per_type: int = 50,
    include_empty: bool = False,
):
    """Phase 4 + 4.2. Return inherited work inbox for a survivor: all
    pending tasks / chats / consolidations / clarifications / broadcasts
    owned by any agent in the survivor's transitive merge-chain (walked
    via agent_runs.merged_into_agent_id). Grouped by proxy agent with
    deactivation_reason + notes + chain depth per group.

    By default (include_empty=False) drops proxy groups with zero
    pending items — keeps the survivor's wake-up inbox clean. Pass
    include_empty=True to force the full transitive chain into the
    response regardless of inbox state (for audit / verification)."""
    return proxy_tool.get_my_proxy_items(
        agent_id, limit_per_type, include_empty,
    )


@mcp.tool()
def act_on_proxy_item(
    survivor_id: str,
    item_type: str,
    item_id: str,
    action: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Phase 4. Act on an inherited item as the original (decommissioned)
    owner. Router validates survivor is a legal proxy, invokes the
    existing public tool with actor=proxy_agent_id under a ContextVar
    that only relaxes the actor-active check for that exact agent.
    Writes a proxy_audit row on success.

    Supported (item_type, action) + required payload keys:
      ('task', 'respond'):
          payload = {"message": str, "new_status": str, "blocker_detail": str?}
      ('clarification', 'respond'):
          payload = {"message": str, "new_status": str}
      ('consolidation', 'respond'):
          payload = {"confidence": float, "message": str, "new_status": str}
      ('chat', 'ack'):
          payload = {}   (item_id is the communication row being acked)
      ('chat', 'send'):
          payload = {"proxy_agent_id": str, "to_agent_id": str, "message": str}
          (item_id is ignored for chat.send — just pass any UUID)
      ('broadcast', 'ack'):
          payload = {"proxy_agent_id": str}   (item_id is broadcast comm row)
    """
    return proxy_tool.act_on_proxy_item(
        survivor_id, item_type, item_id, action, payload,
    )


@mcp.tool()
def absorb_agent(
    agent_id: str,
    consolidation_id: str,
    target_agent_id: str,
    deactivation_reason: str = "merged",
    deactivation_notes: str | None = None,
    cascade_attributions: bool = True,
    cascade_edges: bool = True,
    cascade_flows: bool = True,
) -> dict[str, Any]:
    """Phase 4 + 4.1 cascade. MERGE: flip target to decommissioned,
    union source_slice, decommission target component, re-point RCA.
    Phase 4.1: by default also cascades attributions + edges + flows
    from target to survivor via the mutation-scoped transfer_* tools
    (survivor workflow collapses to absorb + execute_mutation). Set any
    cascade_* flag to False to opt out."""
    return mutation_tool.absorb_agent(
        agent_id, consolidation_id, target_agent_id,
        deactivation_reason, deactivation_notes,
        cascade_attributions, cascade_edges, cascade_flows,
    )


@mcp.tool()
def spawn_child_agent(
    agent_id: str,
    consolidation_id: str,
    child_agent_id: str,
    child_component_data: dict[str, Any],
    child_source_slice: dict[str, Any],
    split_briefing: str,
    transfer_edge_ids: list[str] | None = None,
    transfer_flow_ids: list[str] | None = None,
    transfer_attribution_ids: list[str] | None = None,
    transfer_catalog_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Phase 4 + 4.1 + 4.2 + 7.4.2. SPLIT: carve a new component + idle
    SME out of the caller's component. Child source_slice is subtracted
    atomically. Phase 4.1: strict top-level source_slice guard,
    [split-welcome] BW task, optional edge + flow transfers.
    Phase 4.2: transfer_attribution_ids for hostname/evidence carve.
    Phase 7.4.2: transfer_catalog_ids — atomically move catalog rows
    (the surfaces the carved-out concern exposes) to the child. Runs
    BEFORE flow transfer so flow.incoming_catalog_id refs land
    correctly. Without this, post-split callers resolve to the wrong
    component when binding to the carved-out endpoint."""
    return mutation_tool.spawn_child_agent(
        agent_id, consolidation_id, child_agent_id,
        child_component_data, child_source_slice, split_briefing,
        transfer_edge_ids, transfer_flow_ids, transfer_attribution_ids,
        transfer_catalog_ids,
        agent_manager=_agent_manager_for_spawn,
    )


@mcp.tool()
def transfer_attributions(
    agent_id: str,
    consolidation_id: str,
    attribution_ids: list[str],
    from_component_id: str,
    to_component_id: str,
) -> dict[str, Any]:
    """Phase 4.1. Mutation-scoped attribution transfer. Gated on:
      - active consolidation in state 'M'
      - caller = consolidation.mutation_assigned_to
      - (from, to) within consolidation scope (merge: a↔b; split: parent→child)
    Re-embeds both components. Does NOT touch source_slice."""
    return mutation_tool.transfer_attributions(
        agent_id, consolidation_id, attribution_ids,
        from_component_id, to_component_id,
    )


@mcp.tool()
def transfer_edges(
    agent_id: str,
    consolidation_id: str,
    edge_ids: list[str],
    direction: str = "both",
) -> dict[str, Any]:
    """Phase 4.1. Mutation-scoped edge transfer. Rewrites
    from_component_id and/or to_component_id per `direction`:
      'from' | 'to' | 'both'.
    Catalog collision → collapse (target wins, source dropped).
    Bound/dangling full-key collision → reject."""
    return mutation_tool.transfer_edges(
        agent_id, consolidation_id, edge_ids, direction,
    )


@mcp.tool()
def transfer_flows(
    agent_id: str,
    consolidation_id: str,
    flow_ids: list[str],
) -> dict[str, Any]:
    """Phase 4.1. Mutation-scoped flow transfer. Rewrites flows.component_id
    to the consolidation's target. Rejects on (component_id, incoming,
    outgoing) triple collision at target."""
    return mutation_tool.transfer_flows(
        agent_id, consolidation_id, flow_ids,
    )


@mcp.tool()
def get_my_components(agent_id: str) -> list[dict[str, Any]]:
    """Phase 4.1. List active components this agent owns via RCA.
    Returns id, canonical_name, display_name, component_type, status,
    source_slice, split_briefing, split_from_component_id, doc, timestamps.
    Use on wake if you're unsure which component is yours — especially
    right after a split spawn."""
    return components_tool.get_my_components(agent_id)


@mcp.tool()
def get_component_owner(agent_id: str, component_id: str) -> dict[str, Any]:
    """Phase 10.13.3. Return the active SME owning a component via RCA.

    Use BEFORE creating a clarification about another component — saves
    the round-trip cost of guessing wrong owners (insights b5c84e9e +
    a587f682 + 4be95d57). Returns:
      {component_id, canonical_name, component_status, owner_agent_id,
       owner_status, merged_into_agent_id}

    Triage by owner_status:
      - 'idle' / 'running' → address clarification to owner_agent_id
      - 'decommissioned' + merged_into_agent_id set → address to survivor
      - 'decommissioned' + merged_into_agent_id None → orphaned; escalate
        via send_chat to admin, don't create a clarification

    Raises ValueError if component_id not found."""
    return components_tool.get_component_owner(agent_id, component_id)


@mcp.tool()
def get_stale_edges(agent_id: str) -> list[dict[str, Any]]:
    """Phase 4.1 hygiene. Return edges owned by caller's component where
    the OTHER endpoint's component is decommissioned. Each row carries
    stale_component_merged_into_agent_id when the dead component was
    absorbed — caller can re-bind to the survivor via bind_edge."""
    return components_tool.get_stale_edges(agent_id)


@mcp.tool()
def get_stale_flows(agent_id: str) -> list[dict[str, Any]]:
    """Phase 4.1 hygiene. Return flows on caller's component where an
    edge endpoint's component is decommissioned."""
    return components_tool.get_stale_flows(agent_id)


@mcp.tool()
def execute_mutation(
    agent_id: str,
    consolidation_id: str,
    message: str,
) -> dict[str, Any]:
    """Phase 4. M → MD. Signal mutation work is applied; hand off to resolver
    for verification. Gated on caller = mutation_assigned_to and status = M."""
    return consolidation_tool.execute_mutation(agent_id, consolidation_id, message)


@mcp.tool()
def complete_consolidation(
    agent_id: str,
    consolidation_id: str,
    message: str,
) -> dict[str, Any]:
    """Phase 4. MD → D. Resolver verifies mutation landed + closes. Gated on
    caller.agent_type = 'resolver' and status = MD."""
    return consolidation_tool.complete_consolidation(agent_id, consolidation_id, message)


@mcp.tool()
def get_my_consolidations(agent_id: str) -> dict[str, list[dict[str, Any]]]:
    """All non-terminal consolidations involving this agent (or all for resolver)."""
    return {"consolidations": consolidation_tool.get_my_consolidations(agent_id)}


@mcp.tool()
def get_consolidation_thread(
    agent_id: str,
    consolidation_id: str,
    page: int = 1,
    limit: int = 50,
) -> dict[str, list[dict[str, Any]]]:
    """Paginated consolidation thread. Scoped to participants + resolver."""
    return {
        "thread": consolidation_tool.get_consolidation_thread(
            agent_id, consolidation_id, page, limit
        )
    }


@mcp.tool()
def create_clarification(
    asker_agent_id: str,
    responder_agent_id: str,
    question_message: str,
) -> dict[str, Any]:
    """Any agent asks another agent (or 'admin') a question.
    Inserts clarification row (status=B2) + communication.
    """
    return clarification_tool.create_clarification(
        asker_agent_id, responder_agent_id, question_message,
    )


@mcp.tool()
def respond_clarification(
    agent_id: str,
    clarification_id: str,
    message: str,
    new_status: str,
) -> dict[str, Any]:
    """Asker or responder responds with a state transition + message.

    Valid transitions:
      responder (B2): B2 → B1 | B2 → QR | B2 → QC
      asker (B1):     B1 → B2 | B1 → QC
      asker (QC):     QC → CC | QC → B2
      asker (QR):     QR → CC
    """
    return clarification_tool.respond_clarification(
        agent_id, clarification_id, message, new_status,
    )


@mcp.tool()
def get_my_clarifications(agent_id: str) -> dict[str, list[dict[str, Any]]]:
    """Non-terminal clarifications where agent is asker or responder."""
    return {"clarifications": clarification_tool.get_my_clarifications(agent_id)}


@mcp.tool()
def get_clarification_thread(
    agent_id: str,
    clarification_id: str,
    page: int = 1,
    limit: int = 50,
) -> dict[str, list[dict[str, Any]]]:
    """Paginated clarification thread. Scoped to asker + responder."""
    return {
        "thread": clarification_tool.get_clarification_thread(
            agent_id, clarification_id, page, limit
        )
    }


@mcp.tool()
def vector_search(
    agent_id: str,
    query_text: str,
    table: str,
    limit: int = 10,
    filters: dict[str, Any] | None = None,
    exclude_self: bool = True,
) -> dict[str, Any]:
    """Embed `query_text` and return top-N rows from `table` by cosine similarity.

    Open to all active agents. `table` must be one of:
    components, attributions, unresolved, edges, catalogs.

    Interpretation bands (calibrated for mxbai-embed-large, the model
    wired up in production — Phase 3.7):
      similarity >= 0.75  → strong match
      0.60 to 0.75        → hint (candidate; verify before acting)
      <  0.60             → treat as no match
    Noise floor is ~0.40-0.50 on this model; scores below 0.60 are
    cosine artefacts, not semantic matches.

    Phase 10.7 — components projection now includes `description` (the
    new dense embed-target field). Plus two optional kwargs:

    `filters: dict | None = None` — per-table filter dict. AND across
    keys; OR within key via list. Allowed keys per table:
      components:   component_type, status
      attributions: plane, resource_type, component_id
      edges:        edge_type, from_component_id, to_component_id
      catalogs:     kind, component_id
      unresolved:   reference_type, found_in_component_id, resolved
    Plane filter on components is NOT supported — use
    vector_search(table='attributions', filters={'plane': ...}) for
    plane-scoped lookups (each component has at least one attribution).
    Invalid key for table → ValueError listing allowed keys.

    `exclude_self: bool = True` (DEFAULT ON) — excludes rows owned by
    caller's component(s) via RCA. Non-SME callers (orch / iter /
    resolver own zero components) → silent no-op. Set False to
    include own rows (rare: self-loop sanity check, debugging).

    Typical SME usage — outbound reference resolution:
      While hydrating your component during Materialisation, find refs
      to OTHER components (DB hostname, API URL, Kafka topic). Search:
        strong match → create_edge from your component to the target.
        hint        → insert_unresolved with candidate component_id.
        no match    → insert_unresolved with no candidate; Resolution
                      phase (config SMEs) links it later.
      Default exclude_self=True keeps your own component out of results
      where it would only confuse the triage.

    Sibling-search during Consolidation:
      vector_search(query=<my canonical_name>, table='components',
                    limit=20)
      → top-N candidates for nominate_consolidation(type='merge').
      exclude_self=True (default) drops your own component (which would
      cosine ≈ 1.0) so all returned rows are real candidates.

    If the query can't be embedded (Ollama unreachable, empty text),
    returns {"query_embedded": False, "results": []}. Callers must
    check this flag to distinguish "no hits" from "could not search".
    """
    return search_tool.vector_search(
        agent_id, query_text, table, limit,
        filters=filters, exclude_self=exclude_self,
    )


# ============ DETERMINISTIC SEARCH (Phase 10.3) ============
# Six SQL-LIKE search tools that fill the "find rows without knowing
# the component_id first" gap. AND across columns; OR within column
# via list. Plain string → exact; %/_-bearing → ILIKE. Cap 100 rows.
# Refuses blank-filter calls.


@mcp.tool()
def search_components(
    agent_id: str,
    canonical_name_pattern: str | None = None,
    display_name_pattern: str | None = None,
    name_pattern: str | None = None,
    component_type: Any = None,
    status: Any = "active",
    plane: Any = None,
    exclude_self: bool = True,
) -> list[dict]:
    """Phase 10.3. Find components by exact/ILIKE patterns + filters.

    Args:
      canonical_name_pattern: ILIKE if contains %/_, else exact match.
      display_name_pattern: same.
      name_pattern: convenience — ILIKE both canonical_name AND
        display_name (admin-UI `q` parity). Pass at most ONE of
        {name_pattern, canonical_name_pattern, display_name_pattern}.
      component_type: scalar or list (OR within column).
      status: defaults to 'active'. Pass None to include all statuses.
      plane: filter via RCA → resources.plane. Scalar or list.
      exclude_self: Phase 10.7. DEFAULT TRUE. Skip caller's own
        component. Non-SME callers no-op silently.

    Returns: lean list[dict] with id + canonical_name + display_name +
      component_type + status + planes[]. Capped at 100 rows.
    """
    return search_tool.search_components(
        agent_id, canonical_name_pattern, display_name_pattern,
        name_pattern, component_type, status, plane,
        exclude_self=exclude_self,
    )


@mcp.tool()
def search_attributions(
    agent_id: str,
    identifier_pattern: str | None = None,
    plane: Any = None,
    resource_type: Any = None,
    component_id: str | None = None,
    exclude_self: bool = True,
) -> list[dict]:
    """Phase 10.3. Find attributions by identifier pattern + filters.

    Returns lean rows: {id, component_id, plane, resource_type,
    identifier, confidence}. Capped at 100.

    Phase 10.7: exclude_self default TRUE — skip rows on caller's own
    component.
    """
    return search_tool.search_attributions(
        agent_id, identifier_pattern, plane, resource_type, component_id,
        exclude_self=exclude_self,
    )


@mcp.tool()
def search_edges(
    agent_id: str,
    identifier_pattern: str | None = None,
    edge_type: Any = None,
    kind: Any = None,
    from_component_id: str | None = None,
    to_component_id: str | None = None,
    exclude_self: bool = True,
) -> list[dict]:
    """Phase 10.3. Find edges by identifier + edge_type + kind + endpoints.

    `kind` ∈ {'bound', 'catalog', 'dangling'} (scalar or list). Note
    'catalog' kind is historical — post-Phase-7.4 catalog rows live
    in the `catalogs` table; use search_catalogs for live catalogs.

    Phase 10.7: exclude_self default TRUE — excludes edges where
    EITHER endpoint is caller's component.

    Returns lean rows including the computed `kind` for each edge. Cap 100.
    """
    return search_tool.search_edges(
        agent_id, identifier_pattern, edge_type, kind,
        from_component_id, to_component_id,
        exclude_self=exclude_self,
    )


@mcp.tool()
def search_catalogs(
    agent_id: str,
    identifier_pattern: str | None = None,
    kind: Any = None,
    component_id: str | None = None,
    exclude_self: bool = True,
) -> list[dict]:
    """Phase 10.3. Find catalog rows by identifier + kind + owner.

    Phase 10.7: exclude_self default TRUE — skip catalogs owned by
    caller's component.

    Returns lean rows: {id, component_id, kind, identifier, confidence}.
    Cap 100.
    """
    return search_tool.search_catalogs(
        agent_id, identifier_pattern, kind, component_id,
        exclude_self=exclude_self,
    )


@mcp.tool()
def search_flows(
    agent_id: str,
    component_id: str | None = None,
    incoming_catalog_id: str | None = None,
    outgoing_edge_id: str | None = None,
) -> list[dict]:
    """Phase 10.3. Find flows by component / catalog / edge id.

    Flows have no human-readable identifier — only FK references. So
    this is ID-based filtering only, no string pattern field.

    No `exclude_self` here — flows have no clear ownership predicate
    independent of their FK targets. Filter by component_id directly
    if you need owner-scoping.
    Cap 100.
    """
    return search_tool.search_flows(
        agent_id, component_id, incoming_catalog_id, outgoing_edge_id,
    )


@mcp.tool()
def search_unresolved(
    agent_id: str,
    reference_value_pattern: str | None = None,
    reference_type: Any = None,
    found_in_component_id: str | None = None,
    only_unresolved: bool = True,
    exclude_self: bool = True,
) -> list[dict]:
    """Phase 10.3. Find unresolved refs by value pattern + filters.

    `only_unresolved=True` (default) excludes resolved=TRUE rows.
    Phase 10.7: exclude_self default TRUE — skip rows where
    found_in_component_id matches caller's own component.
    Cap 100.
    """
    return search_tool.search_unresolved(
        agent_id, reference_value_pattern, reference_type,
        found_in_component_id, only_unresolved,
        exclude_self=exclude_self,
    )


@mcp.tool()
def get_agent_notifications(
    agent_id: str,
    priority_from_agent_types: list[str] | None = None,
    since: str | None = None,
) -> dict[str, Any]:
    """Compact unacked-message count from priority sources (for PostToolUse hook).

    Used by the notify hook script to decide whether to interrupt the agent
    mid-session with a [NOTIFY] line. Cheap: one indexed query per message
    type. Tasks are intentionally excluded — they're persistent action items
    that surface via get_action_items_summary on normal wake-up.

    Args:
        priority_from_agent_types: source types to count (default ['admin']).
            Valid: 'admin', 'orchestrator', 'iterator', 'sme', 'resolver'.
        since: ISO timestamp; only items strictly after this count. None =
            all unacked. Round-trip the response's max_seen_at back as
            since on the next call to see only newer items.

    Returns: {high_priority_count, breakdown: [{from, type, count}],
              max_seen_at: ISO string | null}
    """
    return notifications_tool.get_agent_notifications(
        agent_id, priority_from_agent_types, since
    )


@mcp.tool()
def reject_resource(
    agent_id: str,
    resource_id: str,
    reason: str,
    force: bool = False,
) -> dict[str, Any]:
    """Soft-delete a single resource (status='rejected'). Prefer the bulk variant.

    ITERATOR (own plane) or ORCHESTRATOR (force=True). Records rejected_by,
    rejected_at, rejected_reason for audit. Skipped if an SME is already
    assigned to the resource (decommission that SME first).

    Args:
        agent_id: caller
        resource_id: row to reject
        reason: required, used for audit trail
        force: orchestrator-only override to bypass iterator/plane scoping
    """
    return resources_tool.reject_resource(agent_id, resource_id, reason, force)


@mcp.tool()
def reject_resources_bulk(
    agent_id: str,
    plane: str,
    resource_ids: list[str] | None = None,
    resource_types: list[str] | None = None,
    reason: str = "",
    force: bool = False,
) -> dict[str, Any]:
    """Bulk soft-delete resources in one transaction. Plane-scoped. ITERATOR or ORCHESTRATOR.

    Filters AND together. At least one of (resource_ids, resource_types)
    MUST be non-empty — the tool refuses a blank-wipe call. Useful for
    cleaning up over-granular iterator emissions (e.g. sub-artifact rows
    that should have been folded into parent metadata).

    Example — iterator cleans up over-granular GitHub rows:
      reject_resources_bulk(
        agent_id="iter-github-abc",
        plane="github",
        resource_types=["branch","workflow","webhook","deployment","environment","team","org"],
        reason="over-granular emission — folding sub-artifacts into repo metadata"
      )

    Cascade safety: rows with an SME already assigned (via
    resource_component_agents) are skipped and returned in
    'skipped_cascade'. Rows in terminal status ('done' or already
    'rejected') are skipped and returned in 'skipped_terminal'.

    Returns: {rejected: N, ids: [...], skipped_cascade: [...], skipped_terminal: [...]}
    """
    return resources_tool.reject_resources_bulk(
        agent_id, plane, resource_ids, resource_types, reason, force
    )


# ============ AGENT LIFECYCLE ============

@mcp.tool()
def create_agent(
    agent_id: str,
    new_agent_type: str,
    plane: str = "",
    resource_id: str = "",
) -> dict[str, Any]:
    """Create a new agent (iterator or SME). ORCHESTRATOR-ONLY.

    Args:
        agent_id: The caller's agent_id — must be an orchestrator.
        new_agent_type: Type of agent to create. Valid: 'iterator' or 'sme'.
        plane: Required for iterator — which plane the iterator is for
               (github, deploy, cloud, telemetry, config).
        resource_id: Required for SME — the resource row ID this SME will analyse.

    The new agent is created in 'idle' state with:
      - A fresh workspace directory
      - An .mcp.json pointing at this MCP server
      - System prompt for its type/plane

    The new agent will be auto-invoked by the trigger manager when it has
    pending items (e.g., a task assigned to it).

    Returns: {"agent_id": <new_agent_id>, "status": "created"}
    """
    # Only orchestrator can create agents
    caller_type = _get_agent_type(agent_id)
    if caller_type != "orchestrator":
        raise ValueError(
            f"Only orchestrator agents can create other agents. "
            f"{agent_id} is of type '{caller_type}'."
        )

    # Enforce allowed types for this tool — can't create orchestrator/resolver
    # (they are singletons, auto-created at boot)
    if new_agent_type not in ("iterator", "sme"):
        raise ValueError(
            f"Can only spawn 'iterator' or 'sme' via this tool. "
            f"'{new_agent_type}' is not allowed."
        )

    # Per-type required fields
    if new_agent_type == "iterator" and not plane:
        raise ValueError("iterator requires 'plane' parameter")
    if new_agent_type == "sme" and not resource_id:
        raise ValueError("sme requires 'resource_id' parameter")

    new_agent_id = _agent_manager_for_spawn.create_agent(
        agent_type=new_agent_type,
        plane=plane or None,
        resource_id=resource_id or None,
    )
    logger.info(
        "Orchestrator %s created new %s: %s (plane=%s, resource_id=%s)",
        agent_id,
        new_agent_type,
        new_agent_id,
        plane or "-",
        resource_id or "-",
    )
    return {"agent_id": new_agent_id, "status": "created"}


@mcp.tool()
def list_agents(agent_id: str) -> dict[str, list]:
    """List all non-decommissioned agents in the system.

    Returns agents with agent_id, agent_type, status, plane (iterators only),
    invocation_count, sleep_until (nullable), errored_at (nullable),
    created_at. Any agent can call this. SME→resource assignment lives in
    resource_component_agents, not on agent_runs — join that table if you
    need it.
    """
    # Validate caller is a real agent
    _get_agent_type(agent_id)
    rows = execute(
        """SELECT agent_id, agent_type, status, plane,
                  invocation_count, sleep_until, errored_at, created_at
           FROM agent_runs
           WHERE status != 'decommissioned'
           ORDER BY
             CASE agent_type
               WHEN 'orchestrator' THEN 0
               WHEN 'resolver' THEN 1
               WHEN 'sme' THEN 2
               WHEN 'iterator' THEN 3
               ELSE 99
             END,
             created_at"""
    )
    return {"agents": rows}


# ============ SLEEP / WAKE ============

@mcp.tool()
def sleep_self(
    agent_id: str, duration_seconds: int, reason: str
) -> dict[str, Any]:
    """Put YOURSELF to sleep so the trigger scanner stops waking you until
    the wall-clock deadline. Max 7 days. Use when you're legitimately
    waiting on an external event (admin input, deploy window, another
    agent's response) so you don't burn invocations re-checking.

    Admin chat to you auto-wakes; broadcasts / tasks / orch chats do NOT.
    Admin or orchestrator can force-wake you via bulk_wake_agents.
    """
    return sleep_tool.sleep_self(agent_id, duration_seconds, reason)


@mcp.tool()
def bulk_sleep_agents(
    agent_id: str,
    until: str,
    reason: str,
    agent_ids: list[str] | None = None,
    agent_type: str | None = None,
) -> dict[str, Any]:
    """Put a cohort to sleep until an absolute ISO timestamp. ORCH/ADMIN only.

    `until` = ISO-8601 string (e.g. '2026-04-22T03:00:00+00:00').
    Refuses agent_type='orchestrator'. Never sleeps the caller.
    """
    return sleep_tool.bulk_sleep_agents(
        agent_id, until, reason, agent_ids, agent_type
    )


@mcp.tool()
def bulk_wake_agents(
    agent_id: str,
    agent_ids: list[str] | None = None,
    agent_type: str | None = None,
) -> dict[str, Any]:
    """Wake a cohort of sleeping agents. ORCH/ADMIN only. Clears their
    sleep_until so the trigger scanner picks them up next cycle.
    """
    return sleep_tool.bulk_wake_agents(agent_id, agent_ids, agent_type)


@mcp.tool()
def reset_agent(agent_id: str, target_agent_id: str) -> dict[str, Any]:
    """Force-reset a permanently-errored agent back to idle. ORCHESTRATOR-ONLY.

    Use only when bounded auto-recovery has given up (recovery_attempts maxed
    out) and you've diagnosed the underlying cause. Clears error state entirely:
    status='idle', error_msg cleared, recovery_attempts=0, trigger_lock=FALSE.

    Pending items (tasks, chats, etc.) remain untouched, so the trigger manager
    will pick the agent up again on the next scan cycle.

    Args:
        agent_id: Caller (must be orchestrator).
        target_agent_id: The errored agent to reset.

    Returns: {"reset": true, "agent_id": <target_agent_id>} on success.
    Raises ValueError if target is not currently errored.
    """
    # Import here to avoid circular imports at module load
    from agent_management import db as am_db

    caller_type = _get_agent_type(agent_id)
    if caller_type != "orchestrator":
        raise ValueError(
            f"Only orchestrator can force-reset agents. "
            f"{agent_id} is of type '{caller_type}'."
        )
    if not am_db.force_reset_agent(target_agent_id):
        raise ValueError(
            f"Agent {target_agent_id} is not currently errored (or not found)."
        )
    logger.info("Orchestrator %s force-reset agent %s", agent_id, target_agent_id)
    return {"reset": True, "agent_id": target_agent_id}


# ============ CATALOGS (Phase 7.4 — first-class table) ============


@mcp.tool()
def upsert_catalog(
    agent_id: str,
    component_id: str,
    kind: str,
    identifier: str,
    metadata: dict[str, Any] | None = None,
    confidence: float = 1.0,
) -> dict[str, Any]:
    """Phase 7.4: callee declares an exposed thing in their catalog.

    Use noun-form `kind` instead of the verb-form edge_type used in
    Phase 3.9: 'endpoint' (HTTP), 'topic' (pub/sub), 'queue', 'data_source'
    (DB / cache / object store), 'trigger_target' (cron-fire-able).

    Idempotent on (component_id, kind, identifier). Owner-only —
    caller must own component_id via RCA.

    Replaces upsert_edge_catalog (which now forwards here for
    backwards compat).
    """
    return catalogs_tool.upsert_catalog(
        agent_id, component_id, kind, identifier, metadata, confidence,
    )


@mcp.tool()
def upsert_catalogs_bulk(
    agent_id: str,
    component_id: str,
    catalogs: list[dict[str, Any]],
) -> dict[str, Any]:
    """[Phase 7.4.12] Atomic-with-pre-validation bulk upsert of N
    catalog rows on this SME's component. Round 2 #3 of token
    optimisation.

    Each row in `catalogs`:
      {kind, identifier, metadata?, confidence?}.
    All rows target the same component_id (caller's own).

    Pre-validate every row BEFORE opening transaction. If any row
    fails pre-check, return per-row errors and write NOTHING (atomic).
    If all rows pass, commit all in one transaction.

    Returns:
      {"committed": True,  "applied": N, "rows": [...]}
      {"committed": False, "applied": 0, "errors": {<row_idx>: <reason>}}

    For STEP 2b catalog declarations spanning many endpoints/topics on
    one component: prefer this over N sequential `upsert_catalog`
    calls. Max 500 rows per call.
    """
    return catalogs_tool.upsert_catalogs_bulk(agent_id, component_id, catalogs)


@mcp.tool()
def get_my_catalogs(agent_id: str) -> list[dict[str, Any]]:
    """List every catalog row I own, with caller_count per row so I
    can spot orphans at a glance."""
    return catalogs_tool.get_my_catalogs(agent_id)


@mcp.tool()
def get_my_catalog_callers(
    agent_id: str, catalog_id: str | None = None
) -> dict[str, Any]:
    """For each of my catalogs, return the bound callers matched via
    kind ↔ edge_type bridging. Pass catalog_id to filter to one row."""
    return catalogs_tool.get_my_catalog_callers(agent_id, catalog_id)


@mcp.tool()
def get_unmatched_callers(agent_id: str) -> list[dict[str, Any]]:
    """Bound edges INTO my components that have no matching catalog
    row. Triage each:
    - dynamic identifier (DB-like / per-call) → ignore
    - missing catalog row → call upsert_catalog
    - caller error (typo, hallucination, deprecated) → raise clarification
    """
    return catalogs_tool.get_unmatched_callers(agent_id)


@mcp.tool()
def get_orphan_catalogs(agent_id: str) -> list[dict[str, Any]]:
    """Catalogs I own that no bound caller currently matches.
    Companion to get_unmatched_callers — together they show my
    catalog-coverage health."""
    return catalogs_tool.get_orphan_catalogs(agent_id)


# ============ TERMINAL ACKS (Phase 7.1) ============


@mcp.tool()
def ack_terminal(agent_id: str, entity_type: str, entity_id: str) -> dict[str, Any]:
    """Acknowledge a terminal-state entity (task TC, consolidation D/F,
    clarification CC/QR) so the trigger scanner stops re-waking you.

    Validates: entity_type ∈ {'task','consolidation','clarification'},
    entity exists, entity is in a terminal state, and you are a
    participant. Idempotent — calling twice returns
    {acked: false, already_acked: true}.

    Phase 7.1 replaced Phase 5.5's auto-ack at write site. Closure
    announcements now land unacked; you'll be re-woken on every cycle
    until you call this for each terminal entity in your
    `terminal_pending_ack` list.
    """
    return terminal_acks_tool.ack_terminal(agent_id, entity_type, entity_id)


# ============ AGENT INSIGHTS (Phase 5.9) ============


@mcp.tool()
def record_insight(
    agent_id: str,
    kind: str,
    target: str,
    body: str,
    evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Record an insight from your own agent's perspective.

    Call this when you discover something worth feeding back into the
    system: a clever tactic, a prompt gap, a missing tool, a misleading
    on-disk doc, or workflow friction. Admin reviews these and either
    promotes them into prompt updates or marks them wontfix.

    Args:
      kind: 'prompt_gap'        — something missing or confusing in the prompt
            'tactic_win'        — a smart approach worth sharing
            'tool_gap'          — a tool that doesn't exist but should
            'doc_confusing'     — an on-disk doc misled you
            'workflow_friction' — multi-step dance felt awkward
      target: what the insight is about — agent type, tool name, doc
              path, phase. Examples: 'sme.materialisation',
              'transfer_edges', 'TRIGGER-MANAGEMENT.md §1.1b'.
      body: the insight itself (what + why). Be specific.
      evidence: optional pointers — {task_ids, comm_ids, file_paths}.

    Don't over-report — one insight per genuinely-new finding, not
    every mild irritation.
    """
    return insights_tool.record_insight(agent_id, kind, target, body, evidence)


# ============ BATCH DISPATCH (Phase 9.1) ============

# Lazy build the dispatch map from this module's namespace. Each entry
# points at the @mcp.tool()-decorated wrapper (which is the audited
# version, since `cartograph_mcp.audit.install` patches mcp.tool to
# wrap every registration). Sub-calls dispatched through mcp_call_batch
# therefore each get their own row in mcp_audit AND their own
# auth/state validation as if they had been called individually.
#
# Built lazily (first call) so all `def`s above have run by the time
# we resolve them.
_BATCH_DISPATCH: dict | None = None


def _build_batch_dispatch() -> dict:
    """Enumerate every @mcp.tool() function in this module by name.

    Excludes mcp_call_batch itself (nesting is rejected at the call
    site as well, but excluding here is belt-and-suspenders).
    """
    g = globals()
    eligible = [
        # action_items
        "get_action_items_summary", "get_action_items_detail",
        # chat
        "send_chat", "ack_chats", "get_unacked_chats", "get_chat_history",
        # broadcast
        "send_broadcast", "ack_broadcast", "ack_broadcasts_bulk",
        "get_unacked_broadcasts", "update_broadcast_persistence",
        # secrets
        "put_secret", "get_secret", "list_secrets_for_plane", "delete_secret",
        # tasks
        "create_task", "respond_task", "raise_blocker",
        "get_my_tasks", "get_task_thread",
        # resources
        "upsert_resource", "upsert_resources_bulk",
        "reject_resource", "reject_resources_bulk",
        "mark_resource_done",
        "get_resource", "list_resources_for_plane", "list_all_resources",
        "get_resource_counts",
        # agent_lifecycle
        "create_agent", "bulk_spawn_smes", "list_agents", "reset_agent",
        "decommission_agent", "decommission_agents_bulk",
        "decommission_component", "decommission_components_bulk",
        "sleep_self", "bulk_sleep_agents", "bulk_wake_agents",
        # components — writes
        "upsert_component", "upsert_attribution",
        "upsert_attributions_bulk",
        "create_edge",
        "upsert_edge_outbound", "upsert_edges_outbound_bulk",
        "bind_edge",
        "delete_edge", "delete_edges_bulk",
        "upsert_flow", "upsert_flows_bulk",
        "delete_flow", "delete_flows_bulk",
        "delete_attribution", "delete_attributions_bulk",
        "insert_unresolved", "insert_unresolved_bulk",
        "delete_unresolved", "delete_unresolved_bulk",
        "resolve_reference",
        # components — reads
        "get_component", "get_components_bulk",
        "get_attributions", "get_attributions_bulk",
        "get_edges", "get_component_edges", "get_component_edges_bulk",
        "get_flow", "get_flow_inverse", "get_flows_bulk",
        "get_unresolved",
        "get_stale_edges", "get_stale_flows",
        "get_my_components",
        "get_component_owner",
        # catalogs
        "upsert_catalog", "upsert_catalogs_bulk", "upsert_edge_catalog",
        "delete_catalog", "delete_catalogs_bulk",
        "get_my_catalogs", "get_catalogs_bulk",
        "get_my_catalog_callers", "get_unmatched_callers",
        "get_orphan_catalogs",
        # notifications
        "get_agent_notifications",
        # consolidation
        "nominate_consolidation", "respond_consolidation",
        "review_consolidation",
        "get_my_consolidations", "get_consolidation_thread",
        # clarification
        "create_clarification", "respond_clarification",
        "get_my_clarifications", "get_clarification_thread",
        # search
        "vector_search",
        # Phase 10.3 deterministic search
        "search_components", "search_attributions", "search_edges",
        "search_catalogs", "search_flows", "search_unresolved",
        # mutation
        "execute_mutation", "complete_consolidation",
        "absorb_agent", "spawn_child_agent",
        "transfer_attributions", "transfer_edges", "transfer_flows",
        # proxy
        "get_my_proxy_items", "act_on_proxy_item",
        # terminal acks
        "ack_terminal", "ack_terminals_bulk",
        # insights
        "record_insight",
    ]
    out = {}
    missing = []
    for name in eligible:
        fn = g.get(name)
        if fn is None or not callable(fn):
            missing.append(name)
            continue
        out[name] = fn
    if missing:
        # Loud at startup so we catch enumeration drift quickly.
        logger.warning(
            "mcp_call_batch dispatch: %d missing tool(s): %s",
            len(missing), ", ".join(missing),
        )
    return out


@mcp.tool()
def mcp_call_batch(agent_id: str, calls: list[dict]) -> dict:
    """Dispatch a heterogeneous batch of MCP tool calls in parallel.

    Phase 9.1. Use when you have N DIFFERENT tools to call in one
    logical step (wake-start hygiene sweep, resolver triangulation,
    mid-investigation reads). Server runs sub-calls concurrently in a
    thread pool — the LLM sees ONE round-trip instead of N.

    Args:
      agent_id: your agent id. Injected into any sub-call whose
        `args` doesn't carry its own.
      calls: list of `{"tool": str, "args": dict}`. Up to 50 sub-calls
        per batch. `args` may omit `agent_id` (auto-injected).

    Returns:
      `{"results": [{"idx": int, "tool": str, "ok": bool,
                     "result": ...} OR
                    {"idx": int, "tool": str, "ok": False,
                     "error": str}]}`

    Each sub-call succeeds or fails independently — one error does
    NOT abort siblings. Both shapes carry `idx` so callers can
    correlate results back to input order.

    Rules:
      - No nesting. `mcp_call_batch` inside calls[] is rejected.
      - Cap 50 sub-calls. For >50 same-shape rows use a bulk variant
        (e.g. upsert_attributions_bulk takes 500 rows; multiple bulk
        calls can themselves be batched here).
      - Each sub-call goes through its own auth/state validation +
        is recorded in mcp_audit individually.

    Use over native parallel tool_use blocks: Claude Code's agent
    loop disables emission of multiple tool_use per turn (DEMO8
    confirmed 0/851), so emitting [tool_use_A, tool_use_B] in one
    assistant turn does NOT save round-trips. mcp_call_batch is the
    only path to batch-with-one-round-trip on this runtime.

    Example (5-tool wake-start hygiene sweep — heterogeneous reads):
      mcp_call_batch(agent_id='sme-x', calls=[
        {"tool": "get_my_catalogs", "args": {}},
        {"tool": "get_unmatched_callers", "args": {}},
        {"tool": "get_orphan_catalogs", "args": {}},
        {"tool": "get_stale_edges", "args": {}},
        {"tool": "get_stale_flows", "args": {}},
      ])
    """
    global _BATCH_DISPATCH
    if _BATCH_DISPATCH is None:
        _BATCH_DISPATCH = _build_batch_dispatch()
    return call_batch_tool.call_batch(agent_id, calls, _BATCH_DISPATCH)


# ============ ENTRY POINT ============

def main() -> None:
    """Start the MCP server on streamable-http transport.

    Agents connect at http://localhost:8100/mcp via .mcp.json:
      {"mcpServers": {"cartograph-db": {"type": "http", "url": "http://localhost:8100/mcp"}}}
    """
    init_pool()
    run_migrations()

    # Load the Ollama embedding model into GPU memory so the first real
    # MCP call doesn't eat the ~1s cold-start latency. Non-fatal if
    # Ollama is down — the server boots, writes still land with
    # embedding=NULL, and vector_search returns query_embedded=False.
    from shared import embedding as _emb
    _emb.warmup()

    # Count live @mcp.tool() registrations instead of maintaining a
    # hand-written list (which drifts — was stale through Phase 3).
    tool_names = sorted(mcp._tool_manager._tools.keys())
    logger.info(
        "Cartograph MCP server starting on port 8100 (streamable-http) — "
        "%d tools registered", len(tool_names),
    )
    logger.info("Registered tools: %s", ", ".join(tool_names))
    try:
        # FastMCP.run() with transport='streamable-http' serves at /mcp
        mcp.run(transport="streamable-http")
    finally:
        close_pool()


if __name__ == "__main__":
    main()
