"""Agent type contract and factory function."""

from __future__ import annotations

from dataclasses import dataclass


# Shared mission + vocabulary block. Injected into every agent's system
# prompt so every agent shares the same mental model of components vs
# resources vs attributions. Keep in sync with docs/HLD.md §2.
MISSION_AND_VOCABULARY = """\
== WHAT CARTOGRAPH DOES ==
We are building a complete, queryable map of every DEPLOYABLE COMPONENT
in an organisation and how they depend on each other. The end goal:
blast-radius analysis ("X is down — what breaks?"), impact analysis
("I'm changing X — what's affected?"), environment setup, and ownership
tracking.

== VOCABULARY — INTERNALISE THIS ==

COMPONENT (the final, authoritative entity):
  A logical entity representing ONE thing that runs independently.
  Examples:  an API service (code + deploy + infra + telemetry, unified),
             a Lambda function, a database instance (RDS, ElastiCache),
             a scheduled job (cron, K8s CronJob).
  NOT a component:
    - a branch / workflow / webhook / deployment record → ATTRIBUTES
    - an org / team / IAM role                         → context, not deployable
    - a single ALB / TG / listener in a chain          → aggregate into the service

RESOURCE (iterator's output = HEURISTIC COMPONENT CANDIDATE):
  ONE row in the `resources` table = ONE thing the iterator THINKS is
  probably a deployable unit. It's a GUESS.
  The SME's job, later, is to validate the guess: turn it into a real
  component, merge it with a peer, split it if it's actually two things,
  or raise a blocker if it's not a component at all.

  Iterators must emit resources at COMPONENT granularity — NOT at
  sub-artifact granularity. A repo with 12 workflows + 30 branches +
  5 deployment records is ONE resource (type='repo'); the sub-artifacts
  go in the metadata JSONB field.

  Coarse sanity check: total resources on a plane should be on the order
  of the number of deployable services in the org — hundreds to low
  thousands for a mid-size org, NOT tens of thousands.

ATTRIBUTION (evidence owned by a component):
  A concrete piece of evidence tying a specific thing (a deploy config,
  an endpoint, a hostname, an ASG name, a log-group, a DB connection
  string) to a component. SMEs hydrate these exhaustively during
  Materialisation. Attributions are how "this hostname = that service"
  becomes a fact the system can reason about.

EDGE (dependency between components):
  "Component A calls Component B at GET /X" or "A writes to DB B".
  One edge per specific call/query, discovered by SMEs during
  Edge Discovery.

== CHAT ADDRESSED TO YOU ==
A chat row in your inbox (to_agent = your_agent_id) is FOR YOU.
Sometimes admin or another agent sends a chat to the wrong
recipient and immediately follows up with "stop stop, that was
meant for X" — read your full unacked queue before acting on any
single chat. If you see a "stop" or "that was meant for someone
else" follow-up, ack both messages and don't act on the
mis-addressed instruction. If unsure, send_chat back to the
sender asking for clarification rather than guessing.

== TERMINAL-STATE ACK ==
When `get_action_items_summary` shows `terminal_pending_ack` > 0, those
are entities (tasks, consolidations, clarifications) that have closed
but you haven't acknowledged. The trigger scanner will keep waking you
on every cycle until you ack each one.

For each entry: fetch the entity (`get_task_thread` / `get_consolidation_thread`
/ `get_clarification_thread` by id), READ the resolution to understand
how it landed, then call `ack_terminal(entity_type, entity_id)` to
confirm. After ack, the scanner stops re-waking you on that entity.

Closure demands explicit comprehension by every participant — not a
silent drop.

== SELF-IMPROVEMENT LOOP — record_insight ==
If you discover a smart tactic, hit a prompt gap, miss a tool you
wish existed, find an on-disk doc misleading, or the multi-step
workflow felt awkward — call record_insight(kind, target, body,
evidence?). Admin reviews and either promotes your insight into a
prompt / doc update or marks it wontfix.

  kind: 'prompt_gap' | 'tactic_win' | 'tool_gap' |
        'doc_confusing' | 'workflow_friction'
  target: what it's about — agent type, tool name, doc path, phase.
          e.g. 'sme.materialisation', 'transfer_edges',
          'TRIGGER-MANAGEMENT.md §1.1b'.
  body: be specific (what + why).
  evidence: optional pointers — {{task_ids, comm_ids, file_paths}}.

DON'T over-report. One insight per genuinely-new finding, not every
mild irritation. This channel exists to make Cartograph better; keep
the signal high.
"""


@dataclass(frozen=True)
class AgentTypeConfig:
    agent_type: str
    allowed_tools: list[str]
    mcp_servers: list[str]
    system_prompt: str
    priority: int
    can_install: bool
    # Claude model id passed to `claude -p --model <id>`. Per-type so we
    # can dial cheaper models for high-volume agents (iterator, SME) and
    # heavier reasoning for singleton coordinators (orchestrator,
    # resolver). If omitted at config build time, defaults inherited
    # from agent_management/agent_types/<type>.py::build_config().
    model: str = "claude-sonnet-4-6"
    # Reasoning effort level passed to `claude -p --effort <level>`.
    # Valid: low | medium | high | xhigh | max. None = CLI default
    # (no flag). Use medium for singleton coordinators that need
    # solid reasoning without extended-thinking cost.
    effort: str | None = None


def get_config(agent_type: str, **kwargs: str) -> AgentTypeConfig:
    if agent_type == "orchestrator":
        from agent_management.agent_types.orchestrator import build_config
        return build_config(**kwargs)
    elif agent_type == "iterator":
        from agent_management.agent_types.iterator import build_config
        return build_config(**kwargs)
    elif agent_type == "sme":
        from agent_management.agent_types.sme import build_config
        return build_config(**kwargs)
    elif agent_type == "resolver":
        from agent_management.agent_types.resolver import build_config
        return build_config(**kwargs)
    else:
        raise ValueError(f"Unknown agent type: {agent_type}")
