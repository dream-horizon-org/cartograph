#!/usr/bin/env python3
"""Cartograph PostToolUse notification hook.

Run as:  notify.py --agent-id=<id> --priority=<csv>

Claude Code invokes this after every tool call (config in the agent's
`.claude/settings.json`). We hit cartograph-db MCP's get_agent_notifications
to check for new unacked chats/broadcasts from priority sources. If any,
we print `[NOTIFY] ...` to stdout — Claude surfaces that as system-reminder
text so the agent sees it mid-session and can choose to read the detail.

Rate-limited: no-op if called within 10s of the last run (state in
`{cwd}/.cartograph-notify-last` — a few bytes per agent workspace).

Quiet output (empty stdout) on no news keeps tool-chatter clean.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request


MCP_URL = os.environ.get("CARTOGRAPH_MCP_URL", "http://localhost:8100/mcp")
MARKER_FILE = ".cartograph-notify-last"
RATE_LIMIT_S = 10


def _read_marker() -> tuple[float, str | None]:
    """Returns (last_check_epoch, max_seen_at_iso). 0 / None if file missing."""
    try:
        with open(MARKER_FILE) as f:
            data = json.load(f)
        return float(data.get("last_check", 0)), data.get("max_seen_at")
    except (FileNotFoundError, ValueError, OSError):
        return 0.0, None


def _write_marker(max_seen_at: str | None) -> None:
    try:
        with open(MARKER_FILE, "w") as f:
            json.dump({"last_check": time.time(), "max_seen_at": max_seen_at}, f)
    except OSError:
        pass  # non-fatal


def _mcp_call(method: str, params: dict, sid: str | None = None) -> tuple[dict, dict]:
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    headers = {"Content-Type": "application/json", "Accept": "application/json,text/event-stream"}
    if sid:
        headers["Mcp-Session-Id"] = sid
    req = urllib.request.Request(MCP_URL, data=body, headers=headers)
    try:
        r = urllib.request.urlopen(req, timeout=5)
    except (urllib.error.URLError, OSError):
        return {}, {}
    raw = r.read().decode()
    hdrs = dict(r.headers)
    if raw.startswith("event:"):
        for line in raw.splitlines():
            if line.startswith("data: "):
                return hdrs, json.loads(line[6:])
    return hdrs, (json.loads(raw) if raw.strip() else {})


def _mcp_notification(method: str, params: dict, sid: str) -> None:
    body = json.dumps({"jsonrpc": "2.0", "method": method, "params": params}).encode()
    headers = {"Content-Type": "application/json", "Accept": "application/json,text/event-stream", "Mcp-Session-Id": sid}
    try:
        urllib.request.urlopen(urllib.request.Request(MCP_URL, data=body, headers=headers), timeout=2)
    except Exception:
        pass


def check_notifications(agent_id: str, priority: list[str], since: str | None) -> dict | None:
    """Return the notifications dict or None on any failure (silent degrade)."""
    # 1) initialize a fresh MCP session
    hdrs, resp = _mcp_call(
        "initialize",
        {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "notify-hook", "version": "1"}},
    )
    sid = hdrs.get("mcp-session-id") or hdrs.get("Mcp-Session-Id")
    if not sid:
        return None
    _mcp_notification("notifications/initialized", {}, sid)

    # 2) call the tool
    args: dict = {"agent_id": agent_id, "priority_from_agent_types": priority}
    if since:
        args["since"] = since
    _, resp = _mcp_call(
        "tools/call",
        {"name": "get_agent_notifications", "arguments": args},
        sid=sid,
    )
    try:
        result = resp["result"]["structuredContent"]
    except (KeyError, TypeError):
        return None
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent-id", required=True)
    parser.add_argument("--priority", default="admin",
                        help="Comma-separated list of priority source types")
    args = parser.parse_args()

    last_check, prev_max_seen = _read_marker()
    if time.time() - last_check < RATE_LIMIT_S:
        return 0  # quiet; within rate-limit window

    priority = [p.strip() for p in args.priority.split(",") if p.strip()]
    result = check_notifications(args.agent_id, priority, prev_max_seen)
    if result is None:
        # silent degrade — hook shouldn't block the agent on an MCP blip
        _write_marker(prev_max_seen)
        return 0

    _write_marker(result.get("max_seen_at") or prev_max_seen)

    count = result.get("high_priority_count", 0)
    if count == 0:
        return 0

    breakdown = result.get("breakdown", [])
    pieces = [
        f"{b['count']} {b['type']}{'s' if b['count'] > 1 else ''} from {b['from']}"
        for b in breakdown
    ]
    print(
        f"[NOTIFY] {count} new high-priority item(s): " + ", ".join(pieces) +
        ". Call get_action_items_detail for specifics, or keep going if not urgent."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
