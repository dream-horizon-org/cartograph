"""Parse a unified-diff `patch` string into post-PR line ranges of ACTUAL
changes — i.e. the lines that are `+` (added) or attributed to `-` (deleted),
not the surrounding context that GitHub bundles into the hunk.

Why: a single GitHub hunk header `@@ -X,Y +Z,W @@` covers ALL lines in the
hunk including ~3 lines of context before/after each change. When changes
are scattered across a file, GitHub merges nearby hunks, and the merged
hunk's line range spans multiple functions that weren't actually edited.
Using the full hunk extent for line-range overlap produces false positives
on every function whose body sits inside the hunk's context window.

This module returns only the lines that the diff really touches, so the
overlap query in mcp_indexer flags only handlers that were genuinely edited.
"""

from __future__ import annotations

import re


_HUNK_RE = re.compile(
    r"^@@ -\d+(?:,\d+)? \+(?P<start>\d+)(?:,(?P<count>\d+))? @@"
)


def parse_patch(patch: str | None) -> list[tuple[int, int]]:
    """Returns inclusive (start_line, end_line) ranges in the post-PR file
    that the diff genuinely changes (additions or deletions).

    Context-only lines are excluded. Consecutive changed lines are coalesced
    into a single range for SQL efficiency."""
    if not patch:
        return []

    changed: list[int] = []
    cur: int | None = None  # current line number in the post-PR file

    for line in patch.split("\n"):
        m = _HUNK_RE.match(line)
        if m:
            cur = int(m.group("start"))
            continue
        if cur is None:
            continue

        # Skip patch metadata that occasionally appears in some unified-diff flavours.
        if line.startswith("+++") or line.startswith("---") or line.startswith("\\"):
            continue

        if line.startswith("+"):
            # Added line — present at `cur` in the post-PR file.
            changed.append(cur)
            cur += 1
        elif line.startswith("-"):
            # Deleted line — was at this position in the OLD file, doesn't
            # exist in the new file. Attribute the change to `cur`, which
            # in the new file is the line that follows the deletion (or the
            # next context line). Don't advance `cur`.
            changed.append(cur)
        elif line.startswith(" ") or line == "":
            # Context line — present in both old and new, advance pointer
            # but don't flag it as a change.
            cur += 1
        # Anything else: ignore defensively.

    if not changed:
        return []

    # Coalesce consecutive (or duplicate) line numbers into ranges.
    changed.sort()
    ranges: list[tuple[int, int]] = []
    run_start = run_end = changed[0]
    for ln in changed[1:]:
        if ln <= run_end + 1:
            run_end = max(run_end, ln)
        else:
            ranges.append((run_start, run_end))
            run_start = run_end = ln
    ranges.append((run_start, run_end))
    return ranges
