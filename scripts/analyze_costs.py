#!/usr/bin/env python3
"""Cartograph cost forensics — per-agent / per-type / per-tool spend analysis.

Reads two data sources:

1. **Claude Code JSONL transcripts** at
   ``~/.claude/projects/<cwd-encoded>/<session_id>.jsonl``.
   Each assistant turn carries ``message.usage`` (input + output + cache_read +
   cache_creation tokens) and ``message.model``. Agent id is derived from the
   workspace-encoded directory name (suffix after the literal ``workspaces-``).

2. **mcp_audit table** (optional) — one row per MCP tool call, joined to
   agent_runs for agent_type. Used to enrich the per-agent + per-tool view.
   Skipped silently if Postgres is unreachable.

Cost model uses public Anthropic API list prices (Bedrock per-token rates
match closely enough for forensic purposes). Override with --price-* flags or
edit ``PRICING`` below.

Usage:
    python scripts/analyze_costs.py                    # all-time, default prices
    python scripts/analyze_costs.py --since 2026-05-12 # window filter
    python scripts/analyze_costs.py --agent sme-0810c668
    python scripts/analyze_costs.py --json             # machine-readable output
    python scripts/analyze_costs.py --tool-detail      # per-tool breakdown too

Exit code 0 even when DB unreachable — JSONL analysis is the primary signal.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

# ----- Pricing -----------------------------------------------------------------
# USD per million tokens. Source: anthropic.com/pricing (list prices).
# Bedrock is comparable; for exact billing use AWS Cost Explorer with the
# AWSBedrock service filter. These constants are for forensic estimation only.
PRICING = {
    # model_substring -> {input, output, cache_read, cache_create_5m, cache_create_1h}
    "opus":   {"in": 15.00, "out": 75.00, "cr": 1.50, "cc5": 18.75, "cc1h": 30.00},
    "sonnet": {"in":  3.00, "out": 15.00, "cr": 0.30, "cc5":  3.75, "cc1h":  6.00},
    "haiku":  {"in":  1.00, "out":  5.00, "cr": 0.10, "cc5":  1.25, "cc1h":  2.00},
}


def model_tier(model: str) -> str:
    """Map a Claude model id (api or bedrock profile) to a pricing tier."""
    m = (model or "").lower()
    for tier in ("opus", "sonnet", "haiku"):
        if tier in m:
            return tier
    return "sonnet"  # safe default — the most common case


def cost_for_turn(usage: dict, model: str) -> float:
    """Compute USD cost for one assistant turn given its usage block."""
    p = PRICING[model_tier(model)]
    inp = usage.get("input_tokens", 0)
    out = usage.get("output_tokens", 0)
    cr = usage.get("cache_read_input_tokens", 0)
    cc = usage.get("cache_creation", {}) or {}
    cc5 = cc.get("ephemeral_5m_input_tokens", 0)
    cc1h = cc.get("ephemeral_1h_input_tokens", 0)
    return (
        inp  * p["in"]   / 1_000_000
        + out * p["out"]  / 1_000_000
        + cr  * p["cr"]   / 1_000_000
        + cc5 * p["cc5"]  / 1_000_000
        + cc1h * p["cc1h"] / 1_000_000
    )


# ----- Data model --------------------------------------------------------------
def type_from_agent_id(aid: str) -> str:
    """Best-effort prefix-derived agent_type when DB is unreachable."""
    if aid.startswith("orch-"):
        return "orchestrator"
    if aid.startswith("res-"):
        return "resolver"
    if aid.startswith("iter-"):
        return "iterator"
    if aid.startswith("sme-"):
        return "sme"
    return "unknown"


@dataclass
class AgentStats:
    agent_id: str
    agent_type: str = "unknown"  # filled from agent_runs if available
    wakes: int = 0               # distinct session_ids seen
    turns: int = 0               # assistant turns
    tool_uses: int = 0           # tool_use blocks emitted (LLM-side count)
    audit_calls: int = 0         # mcp_audit rows (server-side count)
    in_tokens: int = 0
    out_tokens: int = 0
    cr_tokens: int = 0
    cc5_tokens: int = 0
    cc1h_tokens: int = 0
    cost_usd: float = 0.0
    models_used: dict = field(default_factory=lambda: defaultdict(int))
    sessions: set = field(default_factory=set)


def agent_id_from_dir(dirname: str) -> str | None:
    """Extract agent_id from a Claude Code project-dir encoding.

    Encoding replaces '/' with '-', so the workspace path
        /.../cartograph/src/workspaces/sme-9f6deaef
    becomes the dir
        -Users-.../cartograph-src-workspaces-sme-9f6deaef

    We anchor on the literal '-workspaces-' substring.
    """
    marker = "-workspaces-"
    idx = dirname.find(marker)
    if idx < 0:
        return None
    tail = dirname[idx + len(marker):]
    # tail may contain further path segments after the agent_id; split on '-'
    # but agent ids themselves contain hyphens (sme-9f6deaef). Heuristic: the
    # agent_id is everything up to the next path-separator-encoded '-', which
    # happens to be 1 hyphen-segment after the type prefix.
    parts = tail.split("-")
    # Recognised prefixes:
    if not parts:
        return None
    if parts[0] in ("orch", "res", "sme") and len(parts) >= 2:
        return f"{parts[0]}-{parts[1]}"
    if parts[0] == "iter" and len(parts) >= 3:
        # iter-github-a756891f
        return f"iter-{parts[1]}-{parts[2]}"
    return None


# ----- JSONL parsing -----------------------------------------------------------
def iter_assistant_turns(jsonl_path: Path, since_ts: datetime | None):
    """Yield (timestamp, message_dict) for each assistant turn in a JSONL file."""
    try:
        with jsonl_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if row.get("type") != "assistant":
                    continue
                ts_str = row.get("timestamp", "")
                ts = None
                if ts_str:
                    try:
                        ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                    except ValueError:
                        ts = None
                if since_ts and ts and ts < since_ts:
                    continue
                msg = row.get("message", {}) or {}
                yield ts, msg, row.get("sessionId")
    except OSError:
        return


def count_tool_uses(msg: dict) -> int:
    """Count tool_use blocks in an assistant message's content."""
    content = msg.get("content", []) or []
    if isinstance(content, str):
        return 0
    return sum(1 for blk in content if isinstance(blk, dict) and blk.get("type") == "tool_use")


def scan_jsonls(root: Path, since_ts: datetime | None, agent_filter: str | None) -> dict[str, AgentStats]:
    """Walk all Claude Code project dirs under ~/.claude/projects/."""
    by_agent: dict[str, AgentStats] = {}
    for proj_dir in sorted(root.iterdir() if root.exists() else []):
        if not proj_dir.is_dir():
            continue
        aid = agent_id_from_dir(proj_dir.name)
        if not aid:
            continue
        if agent_filter and agent_filter != aid:
            continue
        stats = by_agent.setdefault(aid, AgentStats(agent_id=aid, agent_type=type_from_agent_id(aid)))
        for jsonl in proj_dir.rglob("*.jsonl"):
            for _ts, msg, session_id in iter_assistant_turns(jsonl, since_ts):
                stats.turns += 1
                if session_id:
                    stats.sessions.add(session_id)
                usage = msg.get("usage", {}) or {}
                model = msg.get("model", "unknown")
                stats.models_used[model_tier(model)] += 1
                stats.in_tokens += usage.get("input_tokens", 0)
                stats.out_tokens += usage.get("output_tokens", 0)
                stats.cr_tokens += usage.get("cache_read_input_tokens", 0)
                cc = usage.get("cache_creation", {}) or {}
                stats.cc5_tokens += cc.get("ephemeral_5m_input_tokens", 0)
                stats.cc1h_tokens += cc.get("ephemeral_1h_input_tokens", 0)
                stats.cost_usd += cost_for_turn(usage, model)
                stats.tool_uses += count_tool_uses(msg)
        stats.wakes = len(stats.sessions)
    return by_agent


# ----- mcp_audit join (optional) ----------------------------------------------
def enrich_from_db(by_agent: dict[str, AgentStats], since_ts: datetime | None):
    """Fill agent_type + audit_calls from postgres if reachable."""
    try:
        import psycopg
    except ImportError:
        print("[info] psycopg not installed — skipping DB enrichment", file=sys.stderr)
        return None
    dbname = os.getenv("CARTOGRAPH_DB_NAME", "cartograph")
    try:
        conn = psycopg.connect(
            host="localhost", port=5432,
            user=os.getenv("CARTOGRAPH_DB_USER", "cartograph"),
            password=os.getenv("CARTOGRAPH_DB_PASSWORD", "cartograph"),
            dbname=dbname,
            connect_timeout=3,
        )
    except Exception as exc:  # noqa: BLE001 — best-effort enrichment
        print(f"[info] DB unreachable ({exc.__class__.__name__}) — JSONL-only mode", file=sys.stderr)
        return None

    cur = conn.cursor()
    cur.execute("SELECT agent_id, agent_type FROM agent_runs")
    type_by_id = {r[0]: r[1] for r in cur.fetchall()}

    since_clause, since_args = ("", ())
    if since_ts:
        since_clause = " WHERE created_at >= %s"
        since_args = (since_ts,)

    cur.execute(f"SELECT agent_id, COUNT(*) FROM mcp_audit{since_clause} GROUP BY agent_id", since_args)
    audit_by_id = {r[0]: r[1] for r in cur.fetchall()}

    cur.execute(f"""
        SELECT tool_name,
               COUNT(*) AS calls,
               COUNT(*) FILTER (WHERE result_status='error') AS errors,
               COALESCE(AVG(duration_ms), 0)::int AS avg_ms
        FROM mcp_audit{since_clause}
        GROUP BY tool_name
        ORDER BY calls DESC
    """, since_args)
    per_tool = cur.fetchall()
    cur.close()
    conn.close()

    for aid, stats in by_agent.items():
        if aid in type_by_id:
            stats.agent_type = type_by_id[aid]
        if aid in audit_by_id:
            stats.audit_calls = audit_by_id[aid]
    # Inject orphan agent_runs with zero JSONL data (decom agents may have it)
    for aid, atype in type_by_id.items():
        if aid not in by_agent:
            by_agent[aid] = AgentStats(agent_id=aid, agent_type=atype, audit_calls=audit_by_id.get(aid, 0))
    return per_tool


# ----- Reporting ---------------------------------------------------------------
def fmt_usd(v: float) -> str:
    return f"${v:,.4f}"


def fmt_int(v: int) -> str:
    return f"{v:,}"


def print_human_report(by_agent: dict[str, AgentStats], per_tool, tool_detail: bool):
    if not by_agent:
        print("No agents matched. JSONLs missing or filter too narrow.")
        return

    # Per-agent table
    print("=" * 110)
    print("PER-AGENT BREAKDOWN")
    print("=" * 110)
    hdr = f"{'agent_id':22} {'type':10} {'wakes':>6} {'turns':>6} {'tools':>6} {'audit':>6} {'in':>9} {'out':>9} {'cache_r':>10} {'cost':>11}"
    print(hdr)
    print("-" * len(hdr))
    sorted_agents = sorted(by_agent.values(), key=lambda s: -s.cost_usd)
    for s in sorted_agents:
        print(f"{s.agent_id:22} {s.agent_type:10} {fmt_int(s.wakes):>6} {fmt_int(s.turns):>6} "
              f"{fmt_int(s.tool_uses):>6} {fmt_int(s.audit_calls):>6} "
              f"{fmt_int(s.in_tokens):>9} {fmt_int(s.out_tokens):>9} {fmt_int(s.cr_tokens):>10} "
              f"{fmt_usd(s.cost_usd):>11}")

    # Per-type aggregate
    by_type: dict[str, list[AgentStats]] = defaultdict(list)
    for s in by_agent.values():
        by_type[s.agent_type].append(s)

    print()
    print("=" * 110)
    print("PER-AGENT-TYPE ROLLUP")
    print("=" * 110)
    hdr2 = (f"{'type':12} {'agents':>6} {'wakes':>8} {'turns':>8} {'tools':>8} "
            f"{'cost':>12} {'$/agent':>10} {'$/wake':>10} {'$/turn':>10} {'tools/turn':>11}")
    print(hdr2)
    print("-" * len(hdr2))
    grand_total = 0.0
    grand_turns = 0
    grand_tools = 0
    for atype, agents in sorted(by_type.items()):
        n = len(agents)
        wakes = sum(a.wakes for a in agents)
        turns = sum(a.turns for a in agents)
        tools = sum(a.tool_uses for a in agents)
        cost = sum(a.cost_usd for a in agents)
        per_agent = cost / n if n else 0.0
        per_wake = cost / wakes if wakes else 0.0
        per_turn = cost / turns if turns else 0.0
        tools_per_turn = tools / turns if turns else 0.0
        print(f"{atype:12} {fmt_int(n):>6} {fmt_int(wakes):>8} {fmt_int(turns):>8} {fmt_int(tools):>8} "
              f"{fmt_usd(cost):>12} {fmt_usd(per_agent):>10} {fmt_usd(per_wake):>10} {fmt_usd(per_turn):>10} "
              f"{tools_per_turn:>11.2f}")
        grand_total += cost
        grand_turns += turns
        grand_tools += tools

    # Headline + efficiency metrics
    print()
    print("=" * 110)
    print("HEADLINE")
    print("=" * 110)
    total_in = sum(s.in_tokens for s in by_agent.values())
    total_out = sum(s.out_tokens for s in by_agent.values())
    total_cr = sum(s.cr_tokens for s in by_agent.values())
    total_cc = sum(s.cc5_tokens + s.cc1h_tokens for s in by_agent.values())
    cache_hit_ratio = total_cr / (total_cr + total_in + total_cc) if (total_cr + total_in + total_cc) else 0.0
    print(f"Total spend:         {fmt_usd(grand_total)}")
    print(f"Total agents:        {fmt_int(len(by_agent))}")
    print(f"Total turns:         {fmt_int(grand_turns)}")
    print(f"Total tool_uses:     {fmt_int(grand_tools)}")
    print(f"Avg cost / agent:    {fmt_usd(grand_total / len(by_agent) if by_agent else 0.0)}")
    print(f"Avg cost / turn:     {fmt_usd(grand_total / grand_turns if grand_turns else 0.0)}")
    print(f"Avg tools / turn:    {grand_tools / grand_turns:.2f}" if grand_turns else "Avg tools / turn:    n/a")
    print(f"Cache-read tokens:   {fmt_int(total_cr)}  ({cache_hit_ratio:.1%} of total input incl. cache_creation)")
    print(f"Output tokens:       {fmt_int(total_out)}  (paid at full output rate, never cached)")

    if tool_detail and per_tool:
        print()
        print("=" * 110)
        print("PER-TOOL BREAKDOWN (from mcp_audit)")
        print("=" * 110)
        print(f"{'tool_name':45} {'calls':>10} {'errors':>8} {'avg_ms':>8}")
        print("-" * 73)
        for tool, calls, errors, avg_ms in per_tool[:50]:
            print(f"{tool:45} {fmt_int(calls):>10} {fmt_int(errors):>8} {avg_ms:>8}")
        if len(per_tool) > 50:
            print(f"... ({len(per_tool) - 50} more tools omitted; pass --tool-detail-all)")


def print_json_report(by_agent: dict[str, AgentStats], per_tool):
    """Machine-readable dump."""
    by_type: dict[str, list[AgentStats]] = defaultdict(list)
    for s in by_agent.values():
        by_type[s.agent_type].append(s)
    out = {
        "agents": {
            aid: {
                "agent_type": s.agent_type,
                "wakes": s.wakes,
                "turns": s.turns,
                "tool_uses": s.tool_uses,
                "audit_calls": s.audit_calls,
                "in_tokens": s.in_tokens,
                "out_tokens": s.out_tokens,
                "cache_read_tokens": s.cr_tokens,
                "cache_create_5m": s.cc5_tokens,
                "cache_create_1h": s.cc1h_tokens,
                "cost_usd": round(s.cost_usd, 4),
                "models_used": dict(s.models_used),
            } for aid, s in by_agent.items()
        },
        "by_type": {
            atype: {
                "agents": len(agents),
                "wakes": sum(a.wakes for a in agents),
                "turns": sum(a.turns for a in agents),
                "tools": sum(a.tool_uses for a in agents),
                "cost_usd": round(sum(a.cost_usd for a in agents), 4),
            } for atype, agents in by_type.items()
        },
        "per_tool": [
            {"tool_name": t, "calls": c, "errors": e, "avg_ms": ms}
            for (t, c, e, ms) in (per_tool or [])
        ],
        "totals": {
            "agents": len(by_agent),
            "turns": sum(s.turns for s in by_agent.values()),
            "tool_uses": sum(s.tool_uses for s in by_agent.values()),
            "cost_usd": round(sum(s.cost_usd for s in by_agent.values()), 4),
        },
    }
    print(json.dumps(out, indent=2, sort_keys=True))


# ----- CLI ---------------------------------------------------------------------
def parse_since(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        if "T" not in value:
            value = f"{value}T00:00:00+00:00"
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError as exc:
        sys.exit(f"--since: invalid ISO timestamp: {exc}")


def main():
    ap = argparse.ArgumentParser(description="Cartograph cost forensics from JSONL + mcp_audit.")
    ap.add_argument("--since", help="ISO timestamp (UTC). Defaults to all time.")
    ap.add_argument("--agent", help="Only include this agent_id.")
    ap.add_argument("--json", action="store_true", help="Emit machine-readable JSON instead of tables.")
    ap.add_argument("--tool-detail", action="store_true", help="Include per-tool breakdown (top 50).")
    ap.add_argument("--projects-root", default=str(Path.home() / ".claude" / "projects"),
                    help="Override Claude Code projects root.")
    ap.add_argument("--no-db", action="store_true", help="Skip mcp_audit enrichment.")
    args = ap.parse_args()

    since_ts = parse_since(args.since)
    root = Path(args.projects_root)
    by_agent = scan_jsonls(root, since_ts, args.agent)
    per_tool = None
    if not args.no_db:
        per_tool = enrich_from_db(by_agent, since_ts)

    if args.json:
        print_json_report(by_agent, per_tool)
    else:
        print_human_report(by_agent, per_tool, args.tool_detail)


if __name__ == "__main__":
    main()
