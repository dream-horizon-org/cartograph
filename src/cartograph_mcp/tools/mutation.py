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
) -> dict:
    """Merge target into the caller's component.

    Gated on consolidation.status='M', caller=mutation_assigned_to.
    Refuses if target IS the caller. Target must be the other party on
    the consolidation.

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
         to the survivor (agent_id + component_id flipped). Duplicate
         (resource_id, survivor_component_id) rows collapse.

    Does NOT:
      - move attributions (use transfer_attributions separately).
      - flip the consolidation status (use execute_mutation).
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

    return {
        "absorbed": target_agent_id,
        "survivor": agent_id,
        "survivor_component_id": survivor_component_id,
        "target_component_id": target_component_id,
        "merged_source_slice": merged_slice,
    }


# ---------- spawn_child_agent (split) ----------


def spawn_child_agent(
    agent_id: str,
    consolidation_id: str,
    child_agent_id: str,
    child_component_data: dict,
    child_source_slice: dict,
    split_briefing: str,
) -> dict:
    """SPLIT: carve out a new component + new SME from the caller's scope.

    Atomic within its scope. Effects:
      1. INSERT new component with split_from_component_id = parent's,
         source_slice = `child_source_slice`, + the rest from
         child_component_data (canonical_name, display_name, component_type, ...).
      2. UPDATE parent component: source_slice = parent_slice MINUS child_slice.
      3. INSERT child_agent_id into agent_runs as an idle SME.
      4. INSERT resource_component_agents for the child (one per resource the
         child's slice covers).
      5. SET consolidations.child_agent_id = child_agent_id (block re-spawn).
      6. Auto-embed the new component.

    Idempotency: consolidation.child_agent_id blocks re-spawn. Caller re-runs
    safely if consolidation.child_agent_id IS NULL only.
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

    return {
        "child_agent_id": child_agent_id,
        "child_component_id": child_component_id,
        "parent_component_id": parent_component_id,
        "new_parent_slice": new_parent_slice,
    }


# ---------- transfer_attributions ----------


def transfer_attributions(
    agent_id: str,
    from_component_id: str,
    to_component_id: str,
    attribution_ids: list[str],
) -> dict:
    """Move a specific set of attribution rows between two components
    owned by the caller. Re-embeds BOTH components so vector_search
    stays calibrated to the new evidence set.

    Does NOT touch source_slice. That's structural + moves via
    upsert_component explicitly. Attributions are evidence; decoupled
    from structure.

    Validates: caller owns from_component. Refuses empty attribution_ids.
    """
    require_active_agent(agent_id)
    if not attribution_ids:
        raise ValueError("attribution_ids must be non-empty")
    from_component_id = str(from_component_id).strip()
    to_component_id = str(to_component_id).strip()
    if from_component_id == to_component_id:
        raise ValueError("from_component_id and to_component_id must differ")
    attribution_ids = [str(a) for a in attribution_ids]

    # Ownership check: caller must own from_component via RCA.
    owns = execute_one(
        """SELECT 1 FROM resource_component_agents
           WHERE agent_id = %s AND component_id = %s LIMIT 1""",
        (agent_id, from_component_id),
    )
    if owns is None:
        raise ValueError(
            f"Agent {agent_id} does not own from_component {from_component_id}"
        )
    # Validate target component exists + is active.
    to_row = execute_one(
        "SELECT status FROM components WHERE id = %s", (to_component_id,)
    )
    if to_row is None:
        raise ValueError(f"to_component {to_component_id} not found")
    if to_row["status"] == "decommissioned":
        raise ValueError(f"to_component {to_component_id} is decommissioned")

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
