import pytest
from agent_management.db import init_db, upsert_component, upsert_attribution
from agent_management.batch_merger import (
    find_candidates,
    classify_tier,
    TIER_AUTO,
    TIER_CONFIRM,
    TIER_SKIP,
)


@pytest.fixture(autouse=True)
def setup_db():
    init_db()


def _make_component(name: str, ctype: str = "application") -> str:
    return upsert_component(
        canonical_name=name,
        display_name=name,
        component_type=ctype,
    )


def test_find_candidates_exact_hostname_match():
    a = _make_component("feeds-aggregator-v2")
    b = _make_component("fav2-api-prod")
    upsert_attribution(a, "github", "hostname", "feeds-agg.dream11.local")
    upsert_attribution(b, "cloud",  "hostname", "feeds-agg.dream11.local")

    candidates = find_candidates()
    pairs = {
        (min(c["component_a_id"], c["component_b_id"]),
         max(c["component_a_id"], c["component_b_id"]))
        for c in candidates
    }
    assert (min(a, b), max(a, b)) in pairs


def test_find_candidates_no_match():
    a = _make_component("service-alpha")
    b = _make_component("service-beta")
    upsert_attribution(a, "github", "hostname", "alpha.dream11.local")
    upsert_attribution(b, "github", "hostname", "beta.dream11.local")

    candidates = find_candidates()
    assert candidates == []


def test_find_candidates_deduplicates_same_pair():
    a = _make_component("svc-a")
    b = _make_component("svc-b")
    # Two matching attributions between same pair
    upsert_attribution(a, "github", "hostname", "shared.local")
    upsert_attribution(b, "cloud",  "hostname", "shared.local")
    upsert_attribution(a, "github", "repo",     "org/shared-repo")
    upsert_attribution(b, "deploy", "repo",     "org/shared-repo")

    candidates = find_candidates()
    pair_ids = [
        (min(c["component_a_id"], c["component_b_id"]),
         max(c["component_a_id"], c["component_b_id"]))
        for c in candidates
    ]
    # Should appear once per matching attribute but union-find groups them
    assert len(set(pair_ids)) == 1


def test_classify_tier_strong_single_attr():
    evidence = [{"resource_type": "entry_point", "identifier": "Main.java",
                 "match_type": "exact"}]
    assert classify_tier(evidence, has_hard_block=False) == TIER_AUTO


def test_classify_tier_two_weak_attrs():
    evidence = [
        {"resource_type": "hostname", "identifier": "svc.local",
         "match_type": "exact"},
        {"resource_type": "repo",     "identifier": "org/svc",
         "match_type": "exact"},
    ]
    assert classify_tier(evidence, has_hard_block=False) == TIER_AUTO


def test_classify_tier_one_weak_attr():
    evidence = [{"resource_type": "hostname", "identifier": "svc.local",
                 "match_type": "exact"}]
    assert classify_tier(evidence, has_hard_block=False) == TIER_CONFIRM


def test_classify_tier_hard_block_overrides():
    evidence = [{"resource_type": "entry_point", "identifier": "Main.java",
                 "match_type": "exact"}]
    assert classify_tier(evidence, has_hard_block=True) == TIER_SKIP


def test_classify_tier_no_evidence():
    assert classify_tier([], has_hard_block=False) == TIER_SKIP
