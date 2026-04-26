"""Vector search across embedded tables.

Embeds the query with the same model used at write time, then does a
cosine-distance KNN against the target table's embedding column. Rows with
embedding IS NULL are skipped (Postgres operator returns NULL which sorts
last and we filter it out explicitly).

Open to all active agents. Callers should treat this as a *hint* — the
<0.7 / 0.7–0.85 / >0.85 similarity bands described in the schema are
interpretation rules applied by the caller, not enforced here.
"""

from __future__ import annotations

from shared import embedding as emb
from shared.db import execute, execute_one


_VALID_TABLES = {
    "components": "SELECT c.*, 1 - (c.embedding <=> %s::vector) AS similarity "
                  "FROM components c "
                  "WHERE c.embedding IS NOT NULL "
                  "ORDER BY c.embedding <=> %s::vector ASC "
                  "LIMIT %s",
    "attributions": "SELECT a.*, 1 - (a.embedding <=> %s::vector) AS similarity "
                    "FROM attributions a "
                    "WHERE a.embedding IS NOT NULL "
                    "ORDER BY a.embedding <=> %s::vector ASC "
                    "LIMIT %s",
    "unresolved": "SELECT u.*, 1 - (u.embedding <=> %s::vector) AS similarity "
                  "FROM unresolved u "
                  "WHERE u.embedding IS NOT NULL "
                  "ORDER BY u.embedding <=> %s::vector ASC "
                  "LIMIT %s",
    "edges": "SELECT e.*, 1 - (e.embedding <=> %s::vector) AS similarity "
             "FROM edges e "
             "WHERE e.embedding IS NOT NULL "
             "ORDER BY e.embedding <=> %s::vector ASC "
             "LIMIT %s",
    # Phase 7.4 follow-up: catalog rows live in their own table now,
    # embedded at write time with "{kind}: {identifier}" — same shape
    # as edges. Lets SMEs do "find an endpoint similar to /payments/charge"
    # across the org without walking get_component_edges per component.
    "catalogs": "SELECT c.*, 1 - (c.embedding <=> %s::vector) AS similarity "
                "FROM catalogs c "
                "WHERE c.embedding IS NOT NULL "
                "ORDER BY c.embedding <=> %s::vector ASC "
                "LIMIT %s",
}


def vector_search(agent_id: str, query_text: str, table: str, limit: int = 10) -> dict:
    """Return top-N rows from `table` by cosine similarity to `query_text`.

    `table` must be one of components / attributions / unresolved / edges.
    `limit` is clamped to [1, 50].

    If the query can't be embedded (missing API key, API error, empty text)
    → returns {"query_embedded": False, "results": []}. Callers distinguish
    "no hits" from "couldn't search" via that flag.
    """
    caller = execute_one(
        "SELECT agent_id FROM agent_runs WHERE agent_id = %s AND status != 'decommissioned'",
        (agent_id,),
    )
    if caller is None:
        raise ValueError(f"Agent {agent_id} not found")

    if table not in _VALID_TABLES:
        raise ValueError(
            f"Invalid table '{table}'. Valid: {sorted(_VALID_TABLES)}"
        )
    try:
        limit_i = int(limit)
    except (TypeError, ValueError) as e:
        raise ValueError("limit must be an integer") from e
    limit_i = max(1, min(50, limit_i))

    vec = emb.vector_literal(emb.embed_text(query_text))
    if vec is None:
        return {"query_embedded": False, "results": []}

    sql = _VALID_TABLES[table]
    rows = execute(sql, (vec, vec, limit_i))
    return {"query_embedded": True, "results": rows}
