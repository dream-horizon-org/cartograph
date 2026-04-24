"""Phase 4 mutation tools — absorb_agent, spawn_child_agent, transfer_attributions.

All three are gated on a consolidation in state M where the caller is
mutation_assigned_to. They write across agent_runs / components /
attributions / resource_component_agents and are meant to be called
before execute_mutation (which flips the consolidation M → MD).

Design choices:
- Each tool is atomic within its own scope. We do NOT bundle absorb +
  spawn + transfer into one giant transaction because each step's
  effect must be observable in the resolver thread (per-step
  communication rows). Atomicity within each individual call is
  enough.
- absorb_agent populates the three new agent_runs deactivation columns
  (phase 4 schema delta) so `get_my_proxy_items` can walk the chain.
  We never flatten chains: if B was survivor of A, and now C absorbs
  B, B.merged_into_agent_id becomes C but A.merged_into_agent_id
  stays B. Chain is walked at read time; historical notes stay true.
- source_slice union on merge uses per-resource-key JSONB merge (union
  of path arrays, dedup preserving first appearance). Target component's
  slice stays frozen as a tombstone (not wiped).
- transfer_attributions is deliberately DECOUPLED from source_slice —
  attributions and slices move independently. If slice also needs to
  change, caller runs a separate upsert_component.
"""

from __future__ import annotations

import json

from shared import embedding as emb
from shared.actor_auth import require_active_agent
from shared.db import execute, execute_mutate, execute_one, execute_returning


# ---------- helpers ----------


def _assert_mutation_gate(agent_id: str, consolidation_id: str) -> dict:
    """Validate caller is the assigned mutation agent on a consolidation
    currently in state 'M'. Returns the consolidation row."""
    require_active_agent(agent_id)
    cons = execute_one(
        "SELECT * FROM consolidations WHERE id = %s", (consolidation_id,)
    )
    if cons is None:
        raise ValueError(f"Consolidation {consolidation_id} not found")
    if cons["status"] != "M":
        raise ValueError(
            f"mutation requires consolidation status='M'; current is '{cons['status']}'"
        )
    if cons["mutation_assigned_to"] != agent_id:
        raise ValueError(
            f"Only mutation_assigned_to may mutate consolidation {consolidation_id}. "
            f"(assigned: {cons['mutation_assigned_to']}, caller: {agent_id})"
        )
    return cons


def _component_of_agent(agent_id: str) -> str | None:
    """Return the active component_id this agent owns, if any."""
    row = execute_one(
        """SELECT rca.component_id
           FROM resource_component_agents rca
           JOIN components c ON c.id = rca.component_id
           WHERE rca.agent_id = %s AND c.status != 'decommissioned'
           LIMIT 1""",
        (agent_id,),
    )
    return str(row["component_id"]) if row else None


def _assert_transfer_scope(
    agent_id: str,
    consolidation_id: str,
    from_component_id: str,
    to_component_id: str,
) -> dict:
    """Common gate for mutation-scoped transfers. Requires:
      1. Active consolidation in state 'M'
      2. Caller = consolidation.mutation_assigned_to
      3. (from, to) pair is legal for the consolidation's nomination_type
         - merge: either direction between component_a and component_b
         - split: parent (component_a) → newly-spawned child only
                  (child resolved via consolidation.child_agent_id)

    Returns the consolidation row."""
    cons = _assert_mutation_gate(agent_id, consolidation_id)
    from_component_id = str(from_component_id)
    to_component_id = str(to_component_id)

    if cons["nomination_type"] == "merge":
        comp_a = str(cons["component_a_id"])
        comp_b = str(cons["component_b_id"]) if cons["component_b_id"] else None
        if comp_b is None:
            raise ValueError(
                f"merge consolidation {consolidation_id} has no component_b"
            )
        allowed = {(comp_a, comp_b), (comp_b, comp_a)}
    else:  # split
        if not cons["child_agent_id"]:
            raise ValueError(
                f"Split consolidation {consolidation_id} has no spawned child yet. "
                "Call spawn_child_agent before transferring edges/flows/attributions."
            )
        child_comp = _component_of_agent(cons["child_agent_id"])
        if child_comp is None:
            raise ValueError(
                f"Split child {cons['child_agent_id']} has no active component."
            )
        allowed = {(str(cons["component_a_id"]), child_comp)}

    if (from_component_id, to_component_id) not in allowed:
        raise ValueError(
            f"Transfer ({from_component_id} → {to_component_id}) outside "
            f"scope of {cons['nomination_type']} consolidation {consolidation_id}. "
            f"Allowed pairs: {sorted(allowed)}"
        )
    return cons


def _merge_source_slices(survivor: dict | None, target: dict | None) -> dict:
    """Per-resource union of two source_slice dicts.

    Each slice is {resource_id: {plane, paths, files, ...}}. Merge rule:
    union the arrays inside each inner dict, dedup preserving first
    appearance. Non-array scalar fields from survivor win; target's
    scalar values only fill in gaps.
    """
    out: dict = dict(survivor or {})
    for rid, t_entry in (target or {}).items():
        if rid not in out:
            out[rid] = dict(t_entry)
            continue
        s_entry = dict(out[rid])
        for k, t_val in t_entry.items():
            if isinstance(t_val, list):
                s_list = s_entry.get(k) or []
                seen = set()
                merged = []
                for item in list(s_list) + list(t_val):
                    key = json.dumps(item, sort_keys=True) if isinstance(item, dict) else item
                    if key in seen:
                        continue
                    seen.add(key)
                    merged.append(item)
                s_entry[k] = merged
            else:
                s_entry.setdefault(k, t_val)
        out[rid] = s_entry
    return out


# ---------- absorb_agent ----------


def absorb_agent(
    agent_id: str,
    consolidation_id: str,
    target_agent_id: str,
    deactivation_reason: str = "merged",
    deactivation_notes: str | None = None,
    cascade_attributions: bool = True,
    cascade_edges: bool = True,
    cascade_flows: bool = True,
) -> dict:
    """Merge target into the caller's component.

    Gated on consolidation.status='M', caller=mutation_assigned_to.
    Refuses if target IS the caller. Target must be the other party on
    the consolidation.

    Phase 4.1: cascade_* flags (default True) pull all of target's
    attributions / edges / flows into the survivor's component via
    the mutation-scoped transfer_* tools. With defaults, the survivor
    workflow collapses from 5 calls (transfer_attrs + transfer_edges +
    transfer_flows + absorb + execute_mutation) to 2 (absorb +
    execute_mutation). Set any flag to False to hand-pick movement —
    e.g. keep survivor's existing edges and discard target's.

    Effects (all in one operation, but not one DB transaction — each
    step is atomic on its own table):
      1. target agent_runs: status='decommissioned', trigger_lock=false,
         merged_into_agent_id=caller, deactivation_reason,
         deactivation_notes. Chain is NOT flattened — transitive chains
         resolve at read time.
      2. survivor component: source_slice unioned with target's slice.
      3. target component: status='decommissioned'. Its source_slice
         stays frozen as a tombstone.
      4. resource_component_agents rows owned by target are re-pointed
         to the survivor. Duplicates collapse.
      5. (cascade_attributions) attributions on target → survivor.
      6. (cascade_edges) edges touching target → survivor (catalog
         collisions collapse; bound/dangling collisions raise).
      7. (cascade_flows) flows on target → survivor.

    Does NOT flip the consolidation status (use execute_mutation).
    """
    cons = _assert_mutation_gate(agent_id, consolidation_id)
    if target_agent_id == agent_id:
        raise ValueError("cannot absorb self")
    # Validate target is the OTHER party on the consolidation.
    parties = {cons["agent_a_id"], cons["agent_b_id"]}
    if target_agent_id not in parties:
        raise ValueError(
            f"target_agent_id {target_agent_id} is not a party on consolidation "
            f"{consolidation_id} (parties: {sorted(p for p in parties if p)})"
        )
    target_row = execute_one(
        "SELECT status FROM agent_runs WHERE agent_id = %s", (target_agent_id,)
    )
    if target_row is None:
        raise ValueError(f"Target agent {target_agent_id} not found")
    if target_row["status"] == "decommissioned":
        raise ValueError(f"Target agent {target_agent_id} already decommissioned")

    survivor_component_id = _component_of_agent(agent_id)
    target_component_id = _component_of_agent(target_agent_id)

    # 1. Flip target agent + populate the 3 deactivation columns in ONE UPDATE.
    execute_mutate(
        """UPDATE agent_runs
           SET status = 'decommissioned',
               trigger_lock = FALSE,
               merged_into_agent_id = %s,
               deactivation_reason = %s,
               deactivation_notes = %s,
               updated_at = now()
           WHERE agent_id = %s""",
        (agent_id, deactivation_reason, deactivation_notes, target_agent_id),
    )

    # 2. Union source_slice onto survivor's component.
    merged_slice = None
    if survivor_component_id and target_component_id:
        rows = execute(
            "SELECT id, source_slice FROM components WHERE id IN (%s, %s)",
            (survivor_component_id, target_component_id),
        )
        by_id = {str(r["id"]): r["source_slice"] for r in rows}
        merged_slice = _merge_source_slices(
            by_id.get(survivor_component_id),
            by_id.get(target_component_id),
        )
        execute_mutate(
            "UPDATE components SET source_slice = %s::jsonb, updated_at = now() WHERE id = %s",
            (json.dumps(merged_slice), survivor_component_id),
        )

    # 3. Decommission target component (slice stays as-is — tombstone).
    if target_component_id:
        execute_mutate(
            """UPDATE components
               SET status = 'decommissioned', updated_at = now()
               WHERE id = %s""",
            (target_component_id,),
        )

    # 4. Re-point RCA rows: target -> survivor.
    #    Duplicates on (resource_id, survivor_component) are dropped first
    #    to avoid unique-constraint collisions.
    if survivor_component_id:
        execute_mutate(
            """DELETE FROM resource_component_agents t
               USING resource_component_agents s
               WHERE t.agent_id = %s
                 AND s.agent_id = %s
                 AND s.resource_id = t.resource_id
                 AND s.component_id = %s""",
            (target_agent_id, agent_id, survivor_component_id),
        )
        execute_mutate(
            """UPDATE resource_component_agents
               SET agent_id = %s, component_id = %s
               WHERE agent_id = %s""",
            (agent_id, survivor_component_id, target_agent_id),
        )
    else:
        # Survivor has no component yet (edge case). Just flip agent_id so
        # the survivor inherits the resource assignments.
        execute_mutate(
            "UPDATE resource_component_agents SET agent_id = %s WHERE agent_id = %s",
            (agent_id, target_agent_id),
        )

    cascade_result = {"attributions": 0, "edges": 0, "flows": 0, "collapsed_edges": 0}

    # Steps 5-7 only run when both components exist AND the respective
    # cascade flag is on. Cascades use the mutation-scoped transfer_*
    # helpers — same gate (_assert_transfer_scope) re-runs per call,
    # which is cheap (single consolidation SELECT) and keeps the
    # transfer audit trail uniform whether invoked directly or via
    # cascade.
    if survivor_component_id and target_component_id:
        if cascade_attributions:
            attr_rows = execute(
                "SELECT id FROM attributions WHERE component_id = %s",
                (target_component_id,),
            )
            attr_ids = [str(r["id"]) for r in attr_rows]
            if attr_ids:
                r = transfer_attributions(
                    agent_id, consolidation_id, attr_ids,
                    target_component_id, survivor_component_id,
                )
                cascade_result["attributions"] = r["transferred"]

        if cascade_edges:
            edge_rows = execute(
                """SELECT id FROM edges
                   WHERE from_component_id = %s OR to_component_id = %s""",
                (target_component_id, target_component_id),
            )
            edge_ids = [str(r["id"]) for r in edge_rows]
            if edge_ids:
                r = transfer_edges(
                    agent_id, consolidation_id, edge_ids, direction="both",
                )
                cascade_result["edges"] = r["transferred"]
                cascade_result["collapsed_edges"] = r["collapsed"]

        if cascade_flows:
            flow_rows = execute(
                "SELECT id FROM flows WHERE component_id = %s",
                (target_component_id,),
            )
            flow_ids = [str(r["id"]) for r in flow_rows]
            if flow_ids:
                r = transfer_flows(agent_id, consolidation_id, flow_ids)
                cascade_result["flows"] = r["transferred"]

    return {
        "absorbed": target_agent_id,
        "survivor": agent_id,
        "survivor_component_id": survivor_component_id,
        "target_component_id": target_component_id,
        "merged_source_slice": merged_slice,
        "cascade": cascade_result,
    }


# ---------- spawn_child_agent (split) ----------


def spawn_child_agent(
    agent_id: str,
    consolidation_id: str,
    child_agent_id: str,
    child_component_data: dict,
    child_source_slice: dict,
    split_briefing: str,
    transfer_edge_ids: list[str] | None = None,
    transfer_flow_ids: list[str] | None = None,
    transfer_attribution_ids: list[str] | None = None,
) -> dict:
    """SPLIT: carve out a new component + new SME from the caller's scope.

    Atomic within its scope. Effects:
      1. INSERT new component with split_from_component_id = parent's,
         source_slice = `child_source_slice`.
      2. UPDATE parent component: source_slice = parent_slice MINUS child_slice.
      3. INSERT child_agent_id into agent_runs as an idle SME.
      4. INSERT resource_component_agents for the child.
      5. SET consolidations.child_agent_id to block re-spawn.
      6. Auto-embed the new component.
      7. (Phase 4.1) Create a BW "[split-welcome]" task for the child
         with component_id + split_briefing in the description so the
         child wakes with visible context (fixes the "child doesn't
         know their component" gap).
      8. (Phase 4.1) Transfer specified edge_ids + flow_ids to the
         child via the mutation-scoped transfer tools.

    Phase 4.1 tightens parent-slice validation: requires the top-level
    components.source_slice column to be non-empty (previously fell
    back to empty carve when agents stashed slice in metadata).

    Idempotency: consolidation.child_agent_id blocks re-spawn. Caller
    re-runs safely only while child_agent_id IS NULL.
    """
    cons = _assert_mutation_gate(agent_id, consolidation_id)
    if cons["nomination_type"] != "split":
        raise ValueError(
            f"spawn_child_agent requires nomination_type='split'; got "
            f"'{cons['nomination_type']}'"
        )
    if cons["child_agent_id"] is not None:
        raise ValueError(
            f"Consolidation {consolidation_id} already spawned child "
            f"{cons['child_agent_id']} — refusing re-spawn"
        )
    if not split_briefing or not split_briefing.strip():
        raise ValueError("split_briefing is required")
    for required in ("canonical_name", "display_name", "component_type"):
        if required not in child_component_data or not child_component_data[required]:
            raise ValueError(f"child_component_data.{required} is required")
    if not child_source_slice:
        raise ValueError("child_source_slice is required (non-empty dict)")

    parent_component_id = _component_of_agent(agent_id)
    if not parent_component_id:
        raise ValueError(
            f"Parent agent {agent_id} owns no active component — cannot split"
        )

    # Phase 4.1 hardening: verify the parent's TOP-LEVEL source_slice
    # column is populated. The demo-POC gotcha was agents stashing
    # slice in metadata.source_slice; the carve below reads the column,
    # would see NULL, and emit an empty new_parent_slice silently.
    _parent_slice_row = execute_one(
        "SELECT source_slice FROM components WHERE id = %s",
        (parent_component_id,),
    )
    if not _parent_slice_row or not _parent_slice_row["source_slice"]:
        raise ValueError(
            f"Parent component {parent_component_id} has no source_slice set "
            f"at the top-level components.source_slice column. Call "
            f"upsert_component with source_slice={{<resource_id>: {{...}}}} "
            f"first — stashing slice inside metadata will not work."
        )

    # 1. Insert the child component with split_from pointer + child slice.
    child_component = execute_returning(
        """INSERT INTO components
           (canonical_name, display_name, component_type,
            source_slice, component_doc_md,
            split_from_component_id, split_briefing,
            confidence, metadata)
           VALUES (%s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s::jsonb)
           RETURNING id""",
        (
            child_component_data["canonical_name"],
            child_component_data["display_name"],
            child_component_data["component_type"],
            json.dumps(child_source_slice),
            child_component_data.get("component_doc_md"),
            parent_component_id,
            split_briefing,
            child_component_data.get("confidence", 1.0),
            json.dumps(child_component_data.get("metadata") or {}),
        ),
    )
    child_component_id = str(child_component["id"])

    # 2. Parent slice = parent MINUS child. Per-resource subtract: drop
    #    paths/files/etc that appear in child's slice. If all inner
    #    arrays end up empty for a resource_id, drop the whole key.
    parent_row = execute_one(
        "SELECT source_slice FROM components WHERE id = %s", (parent_component_id,)
    )
    parent_slice = parent_row["source_slice"] or {}
    new_parent_slice: dict = {}
    for rid, p_entry in parent_slice.items():
        c_entry = child_source_slice.get(rid) or {}
        reduced = {}
        for k, v in p_entry.items():
            if isinstance(v, list):
                c_vals = set(
                    json.dumps(x, sort_keys=True) if isinstance(x, dict) else x
                    for x in (c_entry.get(k) or [])
                )
                kept = [
                    x for x in v
                    if (json.dumps(x, sort_keys=True) if isinstance(x, dict) else x)
                    not in c_vals
                ]
                if kept:
                    reduced[k] = kept
            else:
                reduced[k] = v
        # Drop the resource key entirely if only scalars/empty arrays remain.
        has_content = any(
            v for k, v in reduced.items() if isinstance(v, list)
        )
        if has_content or (not parent_slice.get(rid, {}).keys() <= c_entry.keys()):
            new_parent_slice[rid] = reduced
    execute_mutate(
        "UPDATE components SET source_slice = %s::jsonb, updated_at = now() WHERE id = %s",
        (json.dumps(new_parent_slice) if new_parent_slice else None, parent_component_id),
    )

    # 3. Insert idle SME agent_runs row.
    #    plane + workspace_path copied from parent for consistency.
    parent_agent = execute_one(
        "SELECT plane, workspace_path FROM agent_runs WHERE agent_id = %s",
        (agent_id,),
    )
    execute_mutate(
        """INSERT INTO agent_runs
           (agent_id, agent_type, status, plane, workspace_path)
           VALUES (%s, 'sme', 'idle', %s, %s)
           ON CONFLICT (agent_id) DO NOTHING""",
        (
            child_agent_id,
            parent_agent["plane"] if parent_agent else None,
            parent_agent["workspace_path"] if parent_agent else None,
        ),
    )

    # 4. RCA rows for the child — one per resource the child's slice covers.
    for rid in child_source_slice.keys():
        execute_mutate(
            """INSERT INTO resource_component_agents (resource_id, component_id, agent_id)
               VALUES (%s, %s, %s)
               ON CONFLICT (resource_id, agent_id) DO UPDATE
                 SET component_id = EXCLUDED.component_id""",
            (rid, child_component_id, child_agent_id),
        )

    # 5. Block re-spawn.
    execute_mutate(
        "UPDATE consolidations SET child_agent_id = %s, updated_at = now() WHERE id = %s",
        (child_agent_id, consolidation_id),
    )

    # 6. Auto-embed the new component (best-effort; surface via embedding module).
    try:
        text = f"{child_component_data['display_name']}\n{child_component_data.get('component_doc_md') or ''}"
        vec = emb.embed_text(text)
        if vec is not None:
            execute_mutate(
                "UPDATE components SET embedding = %s::vector WHERE id = %s",
                (vec, child_component_id),
            )
    except Exception:
        pass  # embedding is best-effort; absent vectors just skip vector_search hits

    # 7. Phase 4.1 welcome task: hand the child their component_id +
    #    split_briefing via a BW task. Without this, the child wakes
    #    with no context — their new component exists but they can't
    #    find it without calling get_my_components (added in 4.1.7).
    welcome_description = (
        f"[split-welcome] component_id={child_component_id}\n\n"
        f"{split_briefing}"
    )
    execute_mutate(
        """INSERT INTO tasks (owner_agent_id, worker_agent_id, description, status)
           VALUES (%s, %s, %s, 'BW')""",
        (agent_id, child_agent_id, welcome_description),
    )

    # 8. Phase 4.1+: optional atomic transfers of edges/flows/attributions
    #    into the child. Caller supplies the specific IDs (the parent knows
    #    which belong to the carved slice). Uses the mutation-scoped
    #    helpers — same gate re-runs for audit uniformity.
    transferred_edges = 0
    collapsed_edges = 0
    transferred_flows = 0
    transferred_attributions = 0
    if transfer_edge_ids:
        r = transfer_edges(
            agent_id, consolidation_id, list(transfer_edge_ids),
            direction="both",
        )
        transferred_edges = r["transferred"]
        collapsed_edges = r["collapsed"]
    if transfer_flow_ids:
        r = transfer_flows(agent_id, consolidation_id, list(transfer_flow_ids))
        transferred_flows = r["transferred"]
    if transfer_attribution_ids:
        r = transfer_attributions(
            agent_id, consolidation_id, list(transfer_attribution_ids),
            parent_component_id, child_component_id,
        )
        transferred_attributions = r["transferred"]

    return {
        "child_agent_id": child_agent_id,
        "child_component_id": child_component_id,
        "parent_component_id": parent_component_id,
        "new_parent_slice": new_parent_slice,
        "welcome_task_created": True,
        "transferred_edges": transferred_edges,
        "collapsed_edges": collapsed_edges,
        "transferred_flows": transferred_flows,
        "transferred_attributions": transferred_attributions,
    }


# ---------- transfer_attributions ----------


def transfer_attributions(
    agent_id: str,
    consolidation_id: str,
    attribution_ids: list[str],
    from_component_id: str,
    to_component_id: str,
) -> dict:
    """Mutation-scoped attribution transfer. Phase 4.1 retightens the
    Phase 4 version (which only checked "caller owns from_component"):
    now gated on an active consolidation in state 'M' with caller as
    mutation_assigned_to AND (from, to) within the consolidation's scope.

    For merge (component_a ↔ component_b): either direction allowed.
    For split (component_a → child): only parent→child allowed; requires
    spawn_child_agent to have run first.

    Re-embeds BOTH components so vector_search stays calibrated. Does
    NOT touch source_slice — attributions are evidence, slice is
    structural; caller runs upsert_component separately if slice also
    changes.
    """
    if not attribution_ids:
        raise ValueError("attribution_ids must be non-empty")
    from_component_id = str(from_component_id).strip()
    to_component_id = str(to_component_id).strip()
    if from_component_id == to_component_id:
        raise ValueError("from_component_id and to_component_id must differ")
    attribution_ids = [str(a) for a in attribution_ids]
    # Single scope check — replaces the old RCA-ownership lookup.
    _assert_transfer_scope(agent_id, consolidation_id, from_component_id, to_component_id)

    rowcount = execute_mutate(
        """UPDATE attributions
           SET component_id = %s, last_seen_at = now()
           WHERE id = ANY(%s) AND component_id = %s""",
        (to_component_id, attribution_ids, from_component_id),
    )
    if rowcount == 0:
        raise ValueError(
            "No attributions moved — check IDs actually belonged to from_component"
        )

    # Re-embed both components (best-effort).
    for cid in (from_component_id, to_component_id):
        row = execute_one(
            "SELECT display_name, component_doc_md FROM components WHERE id = %s",
            (cid,),
        )
        if row is None:
            continue
        try:
            text = f"{row['display_name']}\n{row['component_doc_md'] or ''}"
            vec = emb.embed_text(text)
            if vec is not None:
                execute_mutate(
                    "UPDATE components SET embedding = %s::vector WHERE id = %s",
                    (vec, cid),
                )
        except Exception:
            pass

    return {
        "transferred": rowcount,
        "from_component_id": from_component_id,
        "to_component_id": to_component_id,
    }


# ---------- transfer_edges ----------


def transfer_edges(
    agent_id: str,
    consolidation_id: str,
    edge_ids: list[str],
    direction: str = "both",
) -> dict:
    """Mutation-scoped edge transfer. Re-points `from_component_id`
    and/or `to_component_id` on the given edges to the consolidation's
    target component.

    `direction`:
      - 'from' : rewrite from_component_id only (bound/dangling — the
                 caller owns these; they move with their owner).
      - 'to'   : rewrite to_component_id only (catalogs — the callee
                 owns these).
      - 'both' : apply to both endpoints where they match the from
                 component (default; safe for mixed batches).

    Collision rules:
      - Catalog (kind='catalog'): on (to_component_id, edge_type,
        identifier) collision, COLLAPSE — target component keeps its
        existing row, source's is deleted. Catalogs are declarative;
        duplicates represent the same endpoint.
      - Bound/dangling: on full-key collision (from, to, type,
        identifier), REJECT — caller must reconcile manually (they
        represent distinct caller claims).

    Returns {"transferred": int, "collapsed": int, "from_component_id",
              "to_component_id", "direction"}.
    """
    if direction not in ("from", "to", "both"):
        raise ValueError("direction must be one of 'from' | 'to' | 'both'")
    if not edge_ids:
        raise ValueError("edge_ids must be non-empty")
    edge_ids = [str(e) for e in edge_ids]

    # Gate-check consolidation state FIRST so callers see the M-state
    # error before any edge-existence errors (better UX + matches
    # transfer_flows ordering).
    _assert_mutation_gate(agent_id, consolidation_id)

    # `kind` is derived from the nullable from/to columns — not a
    # physical column on the table. Compute it inline so the collision
    # rules can branch on catalog vs bound/dangling.
    edges = execute(
        """SELECT id, from_component_id, to_component_id, edge_type, identifier,
                  CASE
                    WHEN from_component_id IS NULL THEN 'catalog'
                    WHEN to_component_id IS NULL THEN 'dangling'
                    ELSE 'bound'
                  END AS kind
           FROM edges WHERE id = ANY(%s)""",
        (edge_ids,),
    )
    if len(edges) != len(edge_ids):
        found = {str(e["id"]) for e in edges}
        missing = [e for e in edge_ids if e not in found]
        raise ValueError(f"Edges not found: {missing}")

    # Resolve from_component_id: every edge must share the same "source"
    # component for the given direction, derived from the first row.
    # Caller passes a homogeneous batch (sibling edges of one component).
    first = edges[0]
    from_comp = None
    if direction in ("from", "both") and first["from_component_id"]:
        from_comp = str(first["from_component_id"])
    if direction == "to" or (from_comp is None and direction == "both"):
        if first["to_component_id"]:
            from_comp = str(first["to_component_id"])
    if from_comp is None:
        raise ValueError(
            f"Cannot resolve source component for edge {first['id']} "
            f"with direction={direction}"
        )

    # Resolve to_component_id via the scope gate.
    # Caller must tell us which component to transfer to — derive from
    # consolidation scope. Ambiguous without; require explicit target
    # via a synthetic lookup: caller of this function is paired with a
    # mutation consolidation; scope_assert picks the legal target.
    cons = execute_one(
        "SELECT * FROM consolidations WHERE id = %s", (consolidation_id,)
    )
    if cons is None:
        raise ValueError(f"Consolidation {consolidation_id} not found")
    # Compute the expected target based on scope rules.
    if cons["nomination_type"] == "merge":
        a = str(cons["component_a_id"])
        b = str(cons["component_b_id"]) if cons["component_b_id"] else None
        if from_comp == a:
            to_comp = b
        elif from_comp == b:
            to_comp = a
        else:
            to_comp = None
    else:  # split
        child_comp = (
            _component_of_agent(cons["child_agent_id"])
            if cons["child_agent_id"] else None
        )
        to_comp = child_comp if from_comp == str(cons["component_a_id"]) else None
    if to_comp is None:
        raise ValueError(
            f"Cannot derive transfer target for from_component={from_comp} "
            f"on {cons['nomination_type']} consolidation {consolidation_id}"
        )
    # Enforce gate (raises if scope violated).
    _assert_transfer_scope(agent_id, consolidation_id, from_comp, to_comp)

    transferred = 0
    collapsed = 0

    for e in edges:
        eid = str(e["id"])
        kind = e["kind"]
        # Decide which columns to rewrite for THIS edge.
        rewrite_from = direction in ("from", "both") and (
            str(e["from_component_id"]) == from_comp
            if e["from_component_id"] else False
        )
        rewrite_to = direction in ("to", "both") and (
            str(e["to_component_id"]) == from_comp
            if e["to_component_id"] else False
        )
        if not (rewrite_from or rewrite_to):
            continue  # edge doesn't reference from_comp; skip

        # Catalog collision handling: if we're moving a catalog's
        # to_component_id to one that already has an equivalent catalog
        # (same (to, type, identifier)), drop THIS row instead of
        # colliding the unique index.
        if rewrite_to and kind == "catalog":
            existing = execute_one(
                """SELECT 1 FROM edges
                   WHERE from_component_id IS NULL
                     AND to_component_id = %s
                     AND edge_type = %s AND identifier = %s
                     AND id != %s""",
                (to_comp, e["edge_type"], e["identifier"], eid),
            )
            if existing is not None:
                execute_mutate("DELETE FROM edges WHERE id = %s", (eid,))
                collapsed += 1
                continue

        # Build the UPDATE.
        set_cols = []
        params: list = []
        if rewrite_from:
            set_cols.append("from_component_id = %s")
            params.append(to_comp)
        if rewrite_to:
            set_cols.append("to_component_id = %s")
            params.append(to_comp)
        set_cols.append("last_seen_at = now()")
        params.append(eid)
        try:
            execute_mutate(
                f"UPDATE edges SET {', '.join(set_cols)} WHERE id = %s",
                params,
            )
            transferred += 1
        except Exception as exc:
            # Bound/dangling full-key collision — reject.
            raise ValueError(
                f"Edge {eid} collision on target component {to_comp}: {exc}"
            ) from exc

    return {
        "transferred": transferred,
        "collapsed": collapsed,
        "from_component_id": from_comp,
        "to_component_id": to_comp,
        "direction": direction,
    }


# ---------- transfer_flows ----------


def transfer_flows(
    agent_id: str,
    consolidation_id: str,
    flow_ids: list[str],
) -> dict:
    """Mutation-scoped flow transfer. Re-points `flows.component_id` on
    the given flows to the consolidation's target component.

    Rejects on (component_id, incoming_edge_id, outgoing_edge_id)
    collision — flows are unique per triple, so a duplicate at the
    target means the target already has that wiring and caller should
    delete one side explicitly.

    Returns {"transferred": int, "from_component_id", "to_component_id"}.
    """
    if not flow_ids:
        raise ValueError("flow_ids must be non-empty")
    flow_ids = [str(f) for f in flow_ids]

    # Gate-check consolidation state FIRST so callers get the M-state error
    # before flow-existence errors (improves UX + matches transfer_edges).
    cons = _assert_mutation_gate(agent_id, consolidation_id)

    flows = execute(
        "SELECT id, component_id, incoming_edge_id, outgoing_edge_id "
        "FROM flows WHERE id = ANY(%s)",
        (flow_ids,),
    )
    if len(flows) != len(flow_ids):
        found = {str(f["id"]) for f in flows}
        missing = [f for f in flow_ids if f not in found]
        raise ValueError(f"Flows not found: {missing}")

    from_comp = str(flows[0]["component_id"])
    if any(str(f["component_id"]) != from_comp for f in flows):
        raise ValueError(
            "All flows in a batch must share the same component_id"
        )
    if cons["nomination_type"] == "merge":
        a = str(cons["component_a_id"])
        b = str(cons["component_b_id"]) if cons["component_b_id"] else None
        to_comp = b if from_comp == a else a if from_comp == b else None
    else:
        child_comp = (
            _component_of_agent(cons["child_agent_id"])
            if cons["child_agent_id"] else None
        )
        to_comp = child_comp if from_comp == str(cons["component_a_id"]) else None
    if to_comp is None:
        raise ValueError(
            f"Cannot derive transfer target for from_component={from_comp} "
            f"on {cons['nomination_type']} consolidation {consolidation_id}"
        )
    _assert_transfer_scope(agent_id, consolidation_id, from_comp, to_comp)

    transferred = 0
    for f in flows:
        fid = str(f["id"])
        try:
            execute_mutate(
                """UPDATE flows
                   SET component_id = %s, updated_at = now()
                   WHERE id = %s""",
                (to_comp, fid),
            )
            transferred += 1
        except Exception as exc:
            raise ValueError(
                f"Flow {fid} collision at target component {to_comp}: {exc}"
            ) from exc

    return {
        "transferred": transferred,
        "from_component_id": from_comp,
        "to_component_id": to_comp,
    }
