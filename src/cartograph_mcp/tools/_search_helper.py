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
