"""Phase 10.3: shared SQL builder for the six deterministic search tools.

Each per-table search tool composes its filters via the helpers here:

- `pattern_clause(column, value)` — auto-detects exact vs ILIKE based on
  whether the value contains SQL wildcard characters (`%` or `_`).
- `in_clause(column, values)` — for list-valued OR-within-column filters.
- `eq_clause(column, value)` — for exact-match scalars.
- `assemble(filters)` — joins non-None clauses with AND, returns
  `(where_sql, params)`.
- `MAX_RESULTS = 100` — cap applied by every search tool.
- `BlankFilterError` — raised when no filter is provided (refuses
  whole-table dumps).

Auto-detection rules:
- Plain string with no `%` and no `_` → exact `column = %s`.
- String containing `%` or `_` → `column ILIKE %s` (case-insensitive
  pattern; `_` matches single char, `%` matches zero-or-more).

This file is a pure SQL builder — no DB connection, no side effects.
Tested standalone in test_search_tools.py.
"""

from __future__ import annotations

from typing import Any


MAX_RESULTS = 100


class BlankFilterError(ValueError):
    """Raised when a search call provides no filters at all.

    We refuse to dump a whole table — every search tool requires at
    least one non-None filter. Caller is told which fields to use.
    """


def pattern_clause(
    column: str, value: str | None,
) -> tuple[str | None, Any]:
    """Build a pattern-match WHERE fragment for one column.

    Returns:
      (sql_fragment, param) — both None when value is None.
      sql_fragment uses ILIKE for `%`/`_`-bearing values, `=` for plain.
    """
    if value is None:
        return None, None
    if not isinstance(value, str):
        raise ValueError(
            f"Pattern for column '{column}' must be a string, got {type(value).__name__}"
        )
    has_wildcard = ("%" in value) or ("_" in value)
    if has_wildcard:
        return f"{column} ILIKE %s", value
    return f"{column} = %s", value


def eq_clause(
    column: str, value: Any,
) -> tuple[str | None, Any]:
    """Exact equality fragment. Returns (None, None) when value is None."""
    if value is None:
        return None, None
    return f"{column} = %s", value


def in_clause(
    column: str, values: list | tuple | None,
) -> tuple[str | None, Any]:
    """OR-within-column via SQL IN. Returns (None, None) when values is None.

    Single-value lists collapse to `column = %s` for marginal SQL clarity.
    Empty list treated as None (no filter applied).
    """
    if values is None:
        return None, None
    if not isinstance(values, (list, tuple)):
        # Convenience: single scalar → wrap as list.
        values = [values]
    if len(values) == 0:
        return None, None
    if len(values) == 1:
        return f"{column} = %s", values[0]
    placeholders = ", ".join(["%s"] * len(values))
    return f"{column} IN ({placeholders})", list(values)


def assemble(
    filters: list[tuple[str | None, Any]],
) -> tuple[str, list[Any]]:
    """Combine filter fragments with AND. Drops Nones.

    Args:
      filters: list of (sql_fragment, param) tuples from the *_clause
        helpers. param may be a list (from in_clause) — flattened into
        positional params.

    Returns:
      (where_sql, params) — `where_sql` is the AND-joined fragment WITHOUT
      a leading WHERE keyword (caller decides whether to use WHERE / AND).
      Empty when no filters provided.

    Raises:
      BlankFilterError when ALL filters are None.
    """
    parts: list[str] = []
    params: list[Any] = []
    for frag, p in filters:
        if frag is None:
            continue
        parts.append(frag)
        if isinstance(p, list):
            params.extend(p)
        else:
            params.append(p)
    if not parts:
        raise BlankFilterError(
            "blank filter would dump entire table; provide at least one "
            "non-None filter to search_*"
        )
    return " AND ".join(parts), params


def at_most_one(*named_values: tuple[str, Any]) -> None:
    """Validate that at most one of the named scalars is non-None.

    Used for mutually-exclusive convenience filters (e.g. on
    search_components, only one of name_pattern / canonical_name_pattern /
    display_name_pattern may be set).
    """
    set_names = [n for n, v in named_values if v is not None]
    if len(set_names) > 1:
        raise ValueError(
            f"Pass at most one of: {', '.join(n for n, _ in named_values)}. "
            f"Got: {', '.join(set_names)}."
        )


# ============ Phase 10.7: filters dict + exclude_self for vector_search ============

# Allowed filter keys per table for `vector_search(... filters=...)`.
# AND across keys; OR within key via list. Invalid key for table → ValueError.
# search_* tools (Phase 10.3) reuse this same map.
#
# NOTE: "plane" on `components` is NOT here — components.plane is derived via
# RCA→resources (Phase 7.4.7), not a direct column. For plane-scoped component
# lookups, callers vector_search the `attributions` table with plane filter
# (each component has at least one attribution; the plane match propagates).
# If real-data shows this two-step is too awkward, add a JOIN-through-RCA
# clause in a follow-up phase.
_VECTOR_FILTER_KEYS: dict[str, set[str]] = {
    "components":   {"component_type", "status"},
    "attributions": {"plane", "resource_type", "component_id"},
    "edges":        {"edge_type", "from_component_id", "to_component_id"},
    "catalogs":     {"kind", "component_id"},
    "unresolved":   {"reference_type", "found_in_component_id", "resolved"},
}


def validate_filter_keys(table: str, filters: dict | None) -> None:
    """Reject filter keys not in the per-table whitelist.

    Empty dict / None / missing entirely → no-op. First invalid key
    raises with a list of legal keys for that table.
    """
    if not filters:
        return
    if table not in _VECTOR_FILTER_KEYS:
        raise ValueError(
            f"Filters not supported for table '{table}'. Tables with filter "
            f"support: {sorted(_VECTOR_FILTER_KEYS)}."
        )
    allowed = _VECTOR_FILTER_KEYS[table]
    for key in filters.keys():
        if key not in allowed:
            raise ValueError(
                f"Filter key '{key}' not allowed for table '{table}'. "
                f"Allowed keys: {sorted(allowed)}."
            )


def build_filter_clauses(
    filters: dict | None, table_alias: str = "",
) -> tuple[list[tuple[str | None, Any]], list[Any]]:
    """Build clause tuples from a per-table filter dict.

    Auto-routes scalar → eq_clause, list → in_clause. Caller has
    already done validate_filter_keys() so unknown keys won't show up
    here.

    Args:
      filters: dict like {"plane": "github"} or
        {"plane": ["github", "deploy"], "resource_type": "endpoint"}.
      table_alias: SQL alias prefix (e.g. "c" for "c.plane = %s"). Empty
        string means use bare column name.

    Returns:
      list of clause tuples ready to feed to assemble(), and the
      raw collected params (assembly handles param flattening).
    """
    clauses: list[tuple[str | None, Any]] = []
    if not filters:
        return clauses, []
    prefix = f"{table_alias}." if table_alias else ""
    for key, value in filters.items():
        column = f"{prefix}{key}"
        if isinstance(value, (list, tuple)):
            clauses.append(in_clause(column, value))
        else:
            clauses.append(eq_clause(column, value))
    return clauses, []


# Per-table predicate: "row owned by caller's component(s)".
# Used by `exclude_self=True` to NOT-IN-ify the caller's RCA-linked component
# set. Implemented as a SQL EXISTS subquery so we don't have to fetch the
# caller's component-id list separately. Non-SME callers (orch / iter /
# resolver own zero components) → predicate evaluates to TRUE always for
# anyone, meaning no exclusion happens — silent no-op.
#
# The KEY each table uses to match against the caller's owned set:
_OWNED_BY_AGENT_COLUMN: dict[str, str] = {
    "components":   "id",                     # component itself is the owned thing
    "attributions": "component_id",
    "catalogs":     "component_id",
    "unresolved":   "found_in_component_id",
    # edges handled specially (either-side ownership) — see exclude_self_clause()
}


def exclude_self_clause(
    table: str, table_alias: str, agent_id: str,
) -> tuple[str | None, Any]:
    """Build a WHERE-fragment that excludes rows owned by the caller's
    component(s) via RCA.

    Returns (None, None) if exclude_self semantics aren't applicable
    (unknown table, etc.).

    For `edges`: a row is "caller's" if EITHER endpoint is the caller's
    component. Both endpoints checked. Rare cross-component edges where
    only one endpoint is caller's still get excluded by this predicate
    — conservative but matches the "I don't want to see my own work in
    sibling search" intent.
    """
    if table == "edges":
        return (
            f"({table_alias}.from_component_id NOT IN ("
            f"  SELECT component_id FROM resource_component_agents "
            f"  WHERE agent_id = %s AND component_id IS NOT NULL"
            f") OR {table_alias}.from_component_id IS NULL)"
            f" AND ({table_alias}.to_component_id NOT IN ("
            f"  SELECT component_id FROM resource_component_agents "
            f"  WHERE agent_id = %s AND component_id IS NOT NULL"
            f") OR {table_alias}.to_component_id IS NULL)",
            [agent_id, agent_id],
        )
    col = _OWNED_BY_AGENT_COLUMN.get(table)
    if col is None:
        return None, None
    return (
        f"({table_alias}.{col} NOT IN ("
        f"  SELECT component_id FROM resource_component_agents "
        f"  WHERE agent_id = %s AND component_id IS NOT NULL"
        f") OR {table_alias}.{col} IS NULL)",
        agent_id,
    )
