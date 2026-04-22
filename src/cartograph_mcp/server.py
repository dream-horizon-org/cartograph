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
from cartograph_mcp.tools import notifications as notifications_tool
from cartograph_mcp.tools import search as search_tool
from cartograph_mcp.tools import sleep as sleep_tool

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)

# Create the MCP server. Streamable HTTP listens at /mcp endpoint.
mcp = FastMCP("cartograph-db", host="0.0.0.0", port=8100)

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

    Returns a dict with keys: consolidations_pending, tasks_pending,
    clarifications_pending, unacked_chats, unacked_broadcasts.

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
      confidence (default 1.0), metadata (dict).
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
    """
    return components_tool.upsert_attribution(agent_id, component_id, attribution_data)


@mcp.tool()
def create_edge(agent_id: str, edge_data: dict[str, Any]) -> dict[str, Any]:
    """Record a dependency edge from this SME's component. SME-ONLY (owns source).

    Idempotent on (source_id, target_id, edge_type, identifier). Refuses
    self-loops (source == target).

    edge_data keys:
      source_id (your component), target_id, edge_type (calls/reads_from/
        writes_to/triggers/publishes_to/consumes_from/runs_on),
      identifier (specific call, e.g. 'GET /scorecard'),
      source_attr_id? target_attr_id? (ids of the attributions on either side),
      evidence (array), confidence, metadata.
    """
    return components_tool.create_edge(agent_id, edge_data)


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
) -> dict[str, Any]:
    """SME proposes a merge or split. Creates consolidation row (status=B2)
    + communication. Merge requires component_b_id owned by a different SME.
    Split accepts component_b_id=None (child is spawned after approval).
    """
    return consolidation_tool.nominate_consolidation(
        agent_id, component_a_id, component_b_id, nomination_type,
        confidence, message,
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
) -> dict[str, Any]:
    """Embed `query_text` and return top-N rows from `table` by cosine similarity.

    Open to all active agents. `table` must be one of:
    components, attributions, unresolved, edges.

    Interpretation bands (caller applies):
      similarity > 0.85  → strong match (confident attribution)
      0.7 to 0.85        → hint (insert_unresolved + mark candidate)
      < 0.7              → treat as no match (create new component)

    If the query can't be embedded (missing API key, transport error, empty
    text), returns {"query_embedded": False, "results": []}. Callers must
    check this flag to distinguish "no hits" from "could not search".
    """
    return search_tool.vector_search(agent_id, query_text, table, limit)


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


# ============ ENTRY POINT ============

def main() -> None:
    """Start the MCP server on streamable-http transport.

    Agents connect at http://localhost:8100/mcp via .mcp.json:
      {"mcpServers": {"cartograph-db": {"type": "http", "url": "http://localhost:8100/mcp"}}}
    """
    init_pool()
    run_migrations()
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
