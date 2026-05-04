"""
Batch Merger — V2 replacement for SME negotiation.

Runs as code after all SMEs finish materialisation. No LLM involved.
Finds merge candidates, classifies them by confidence tier, and executes
AUTO merges. CONFIRM candidates are surfaced to the human admin.
"""

from __future__ import annotations

import logging
from typing import Callable

from agent_management import db

logger = logging.getLogger(__name__)

TIER_AUTO    = "auto"
TIER_CONFIRM = "confirm"
TIER_FLAG    = "flag"
TIER_SKIP    = "skip"

# Attributes that are conclusive on their own (same entry_point = same process)
_STRONG_ATTRS = {"entry_point", "deploy_config"}

# Attributes that need 2+ to form a confident match
_WEAK_ATTRS = {"hostname", "repo", "asg", "k8s_workload", "datadog_service",
               "load_balancer", "config_key"}

# Runtime/type mismatches that block a merge regardless of name similarity
_BLOCKING_RUNTIME_PAIRS = frozenset([
    ("java", "python"), ("java", "node"), ("java", "go"),
    ("python", "node"), ("python", "go"), ("node", "go"),
])


def find_candidates() -> list[dict]:
    """
    Find all pairs of active components that share at least one exact
    attribution (same resource_type + identifier).

    Returns a deduplicated list of dicts:
      { component_a_id, component_b_id, evidence: [{resource_type, identifier, match_type}] }
    """
    raw_matches = db.find_exact_attribute_matches()

    # Group matches by (component_a_id, component_b_id) pair
    pair_evidence: dict[tuple[str, str], list[dict]] = {}
    for row in raw_matches:
        key = (str(row["component_a_id"]), str(row["component_b_id"]))
        pair_evidence.setdefault(key, []).append({
            "resource_type": row["resource_type"],
            "identifier":    row["identifier"],
            "match_type":    "exact",
        })

    return [
        {
            "component_a_id": k[0],
            "component_b_id": k[1],
            "evidence": v,
        }
        for k, v in pair_evidence.items()
    ]


def _has_hard_block(component_a_id: str, component_b_id: str) -> bool:
    """Return True if merging these two components must be blocked."""
    if db.edge_exists_between(component_a_id, component_b_id):
        logger.debug("Hard block: edge exists between %s and %s",
                     component_a_id, component_b_id)
        return True

    comp_a = db.get_component(component_a_id)
    comp_b = db.get_component(component_b_id)
    if not comp_a or not comp_b:
        logger.debug(
            "Hard block: component not found (%s or %s) — likely decommissioned"
            " by an earlier merge in this pass",
            component_a_id, component_b_id,
        )
        return True

    if comp_a["component_type"] != comp_b["component_type"]:
        logger.debug("Hard block: type mismatch %s vs %s",
                     comp_a["component_type"], comp_b["component_type"])
        return True

    attrs_a = {a["resource_type"]: a["identifier"]
               for a in db.get_attributions(component_a_id)}
    attrs_b = {a["resource_type"]: a["identifier"]
               for a in db.get_attributions(component_b_id)}
    rt_a = attrs_a.get("runtime", "").lower()
    rt_b = attrs_b.get("runtime", "").lower()
    if rt_a and rt_b and rt_a != rt_b:
        pair = tuple(sorted([rt_a, rt_b]))
        if pair in _BLOCKING_RUNTIME_PAIRS:
            logger.debug("Hard block: runtime mismatch %s vs %s", rt_a, rt_b)
            return True

    return False


def classify_tier(evidence: list[dict], has_hard_block: bool) -> str:
    """
    Determine the confidence tier for a merge candidate.

    AUTO    — execute immediately, no human needed
    CONFIRM — surface to human for bulk approval
    FLAG    — record only, do not merge
    SKIP    — discard
    """
    if has_hard_block or not evidence:
        return TIER_SKIP

    matched_types = {e["resource_type"] for e in evidence}

    if matched_types & _STRONG_ATTRS:
        return TIER_AUTO

    weak_hits = matched_types & _WEAK_ATTRS
    if len(weak_hits) >= 2:
        return TIER_AUTO

    if len(weak_hits) == 1:
        return TIER_CONFIRM

    return TIER_SKIP


def _pick_surviving(component_a_id: str, component_b_id: str) -> tuple[str, str]:
    """Return (surviving_id, absorbed_id). Surviving = more attributions."""
    count_a = len(db.get_attributions(component_a_id))
    count_b = len(db.get_attributions(component_b_id))
    if count_a >= count_b:
        return component_a_id, component_b_id
    return component_b_id, component_a_id


def _confidence_score(evidence: list[dict]) -> float:
    """Heuristic confidence score based on evidence strength.

    Strong attrs (entry_point, deploy_config) score 0.95.
    Weak attrs accumulate additively in +0.20 steps, capped at 0.90.
    Overall score capped at 0.99.
    """
    if not evidence:
        return 0.0
    score = 0.0
    for e in evidence:
        if e["resource_type"] in _STRONG_ATTRS:
            score = max(score, 0.95)
        elif e["resource_type"] in _WEAK_ATTRS:
            score = max(score, min(score + 0.20, 0.90))
    return round(min(score, 0.99), 2)


def run_batch_merge(
    re_embed_fn: Callable[[dict, list[dict]], list[float]] | None = None,
) -> dict:
    """
    Full batch merge pass. Call after all SMEs finish materialisation.

    re_embed_fn: optional callable(component, attributions) -> vector.
                 Pass None in tests to skip embedding.

    Returns: { auto_executed: N, pending_human: N, skipped: N }
    """
    candidates = find_candidates()
    logger.info("Batch merger: found %d raw candidate pairs", len(candidates))

    summary = {"auto_executed": 0, "pending_human": 0, "skipped": 0}

    for cand in candidates:
        a_id = cand["component_a_id"]
        b_id = cand["component_b_id"]
        evidence = cand["evidence"]

        hard_block = _has_hard_block(a_id, b_id)
        tier = classify_tier(evidence, hard_block)

        if tier == TIER_SKIP:
            summary["skipped"] += 1
            continue

        candidate_id = db.create_merge_candidate(
            component_a_id=a_id,
            component_b_id=b_id,
            tier=tier,
            confidence=_confidence_score(evidence),
            evidence=evidence,
        )

        if tier == TIER_AUTO:
            surviving_id, absorbed_id = _pick_surviving(a_id, b_id)
            db.execute_merge_in_db(surviving_id, absorbed_id)
            db.update_merge_candidate(candidate_id, "executed", surviving_id)

            if re_embed_fn is not None:
                component = db.get_component(surviving_id)
                attributions = db.get_attributions(surviving_id)
                vector = re_embed_fn(component, attributions)
                db.update_component_embedding(surviving_id, vector)

            logger.info("Auto-merged %s <- %s (evidence: %s)",
                        surviving_id, absorbed_id,
                        [e["resource_type"] for e in evidence])
            summary["auto_executed"] += 1

        elif tier == TIER_CONFIRM:
            logger.info("Merge candidate surfaced for human review: %s <-> %s",
                        a_id, b_id)
            summary["pending_human"] += 1

    return summary
