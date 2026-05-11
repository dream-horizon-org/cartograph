# PR Impact Agent

A FastAPI microservice that, given a GitHub PR URL, returns the set of downstream services and infrastructure dependencies needed to deploy that PR for testing. Reads from cartograph's Postgres; writes nothing.

## Why this exists

Cartograph already knows that `service-a` calls `service-b` and `service-c`. But not every PR to `service-a` needs both downstream services live to test. If a PR only modifies the handler at `GET /call/b`, you only need `service-b` (and its dependencies) running.

This service answers two questions:

- **"What do I need to bring up to run this whole repo end-to-end?"** → `POST /analyze` (full transitive closure from cartograph's graph).
- **"What do I need to bring up to test just this PR?"** → `POST /analyze/changes` (per-PR deploy planner using diff + code graph + LLM).

## Quickstart

Prerequisites:
- Cartograph Postgres running (port 5432)
- `git` on PATH
- `claude` CLI (Claude Code) on PATH and authenticated
- `codebase-memory-mcp` on PATH: `npm install -g codebase-memory-mcp@0.6.1`
- A GitHub PAT with `repo` read scope

Run from the cartograph repo root:

```bash
# one-time
cd services/pr_impact_agent
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cd ../..

# every time
GITHUB_PAT=<your-pat> services/pr_impact_agent/.venv/bin/python -m services.pr_impact_agent
```

Listens on port `8300`. Health check: `GET /health`.

## Endpoints

### `POST /analyze` — repo-level closure

```bash
curl -sX POST http://localhost:8300/analyze \
  -H 'content-type: application/json' \
  -d '{"url":"https://github.com/owner/repo"}' | jq
```

Returns the root component(s) for the repo and every transitively reachable downstream component, each with its outbound edges. Dangling edges (like `reads_from DATABASE_URL` to an unresolved infra target) appear under each component, so the deploy planner can see "deploy `service-b` together with its DB."

### `POST /analyze/changes` — per-PR impact

```bash
curl -sX POST http://localhost:8300/analyze/changes \
  -H 'content-type: application/json' \
  --max-time 360 \
  -d '{"url":"https://github.com/owner/repo/pull/42"}' | jq
```

Returns:

| Field | What it tells you |
|---|---|
| `impacted_endpoints` | Existing endpoints whose handler code was touched by this PR (with each endpoint's direct call targets, minus removed calls) |
| `new_endpoints` | New routes the PR adds |
| `new_outbound_calls` | New external calls the PR adds, each with a `resolution` (resolved / ambiguous / unresolved against existing components) |
| `removed_outbound_calls` | Outbound calls the PR removes (used to subtract dependencies) |
| `unresolved_new_dependencies` | Subset of `new_outbound_calls` where the target couldn't be matched to any cartograph component — flag for the operator |
| `downstream` | Transitive closure of the dependency graph from the impacted endpoints' direct targets, including resolved new-call targets, excluding removed-call targets where they were the only path |
| `code_graph_evidence` | Debug: the static-analysis pre-pass output (which handlers were directly / transitively impacted, with file paths and line ranges) |

Latency is typically 20–60 seconds per PR (clone + index + Claude call).

## How `/analyze/changes` works

```
PR URL
  ↓ parse → owner / repo / pr_number
  │
  ├─ GitHub API: PR meta (head_sha, head_ref, head_repo)
  ├─ GitHub API: PR file diffs    [filter out lockfiles, test files, binaries]
  │
  ├─ git clone --depth 1 (PAT auth)  →  /tmp/pr_workspaces/<sha>/
  │
  ├─ codebase-memory-mcp cli index_repository
  │   → ~/.cache/codebase-memory-mcp/<project>.db (SQLite)
  │
  ├─ Static pre-analysis (deterministic SQL on the SQLite):
  │     For each diff hunk's actual +/- lines (NOT context lines),
  │     find Function/Method nodes whose [start_line, end_line]
  │     overlaps. Then walk CALLS edges in reverse for transitive
  │     callers (depth ≤ 3). Each handler carries its route_method
  │     and route_path directly on the node.
  │
  ├─ Claude classification (`claude -p` with Read/Grep/Glob)
  │     Prompt embeds the pre-analysis as text. Claude trusts it for
  │     "which handler changed" and spends its tool budget on:
  │       • new outbound calls in the diff
  │       • removed outbound calls
  │       • new endpoints
  │       • LLM-suggested target matches (Layer 5')
  │
  ├─ Resolution pipeline (per new/removed call hint):
  │     L1: components.canonical_name exact
  │     L2: attribution exact match (repo / hostname / service_name)
  │     L3: catalog endpoint path match
  │     L4: attribution substring (only if L1–L3 empty)
  │     L5': LLM suggestion (only if L1–L4 empty)
  │     Deterministic layers always win over LLM suggestion.
  │
  ├─ Removed-call subtraction:
  │     For each cleanly-resolved removed call, drop the matching
  │     (catalog_id, target_component_id) pair from the flow rows.
  │     Survivors define direct_target_ids.
  │
  ├─ Add resolved new-call targets to direct_target_ids
  │
  ├─ Transitive closure walk over cartograph's edges
  │
  └─ Cleanup: rm workspace, delete codebase-memory-mcp project
```

### Why a static code graph AND an LLM

The code graph (`codebase-memory-mcp`) gives us **deterministic** answers to:
- Which handlers does each diff hunk overlap?
- What's the transitive call chain (best-effort)?
- What are the route bindings?

The LLM gives us **semantic** answers that the graph can't:
- What new outbound calls were added by the diff?
- What was removed?
- What's a sensible match for an unknown target hint (e.g. `SVC_D_BASE_URL` → maybe `service-d`)?

The two are complementary. Pre-computing the deterministic part lets us hand Claude a focused prompt; Claude's tool budget drops from 6–10 calls to 0–3, which is most of where the speedup comes from.

## Configuration

| Env var | Default | Notes |
|---|---|---|
| `GITHUB_PAT` | — | **Required.** PR meta + file fetch + clone |
| `PR_IMPACT_AGENT_PORT` | `8300` | HTTP port |
| `CARTOGRAPH_DB_HOST` | `localhost` | |
| `CARTOGRAPH_DB_PORT` | `5432` | |
| `CARTOGRAPH_DB_NAME` | `cartograph` | |
| `CARTOGRAPH_DB_USER` | `cartograph` | |
| `CARTOGRAPH_DB_PASSWORD` | `cartograph` | |
| `PR_IMPACT_CLAUDE_MODEL` | (unset) | Hard override — forces ALL calls to one model. Leave unset for auto-selection. |
| `PR_IMPACT_CLAUDE_MODEL_SMALL` | `claude-haiku-4-5-20251001` | Used when ≤ `PR_IMPACT_SMALL_PR_FILES` files AND ≤ `PR_IMPACT_SMALL_PR_DIFF_CHARS` chars |
| `PR_IMPACT_CLAUDE_MODEL_LARGE` | `claude-sonnet-4-6` | Used otherwise |
| `PR_IMPACT_SMALL_PR_FILES` | `0` (disabled) | Set both this and the diff-chars threshold to non-zero values to opt in to Haiku auto-selection for small PRs. Disabled by default because Haiku 4.5 sometimes ignores the "JSON-only" output instruction. |
| `PR_IMPACT_SMALL_PR_DIFF_CHARS` | `0` (disabled) | Threshold for "small" PR (diff text size, post-filter). |
| `PR_IMPACT_CLAUDE_TIMEOUT` | `600` | Subprocess timeout (s) |
| `PR_IMPACT_CLAUDE_MAX_TURNS` | `20` | Bound on Claude tool-use turns |
| `PR_IMPACT_GIT_TIMEOUT` | `180` | Clone timeout (s) |
| `PR_IMPACT_WORKSPACE_ROOT` | `/tmp/pr_workspaces` | Where clones live |
| `PR_IMPACT_CMM_INDEX_TIMEOUT` | `120` | codebase-memory-mcp index timeout (s) |
| `PR_IMPACT_CMM_DEPTH` | `3` | Transitive caller walk depth |

## Failure modes

| What happened | `fell_back_to_phase_1` | `fallback_reason` | Result |
|---|---|---|---|
| Repo not in cartograph | false | `no_root_components` | Empty downstream + warning |
| GitHub API failed | true | `github_fetch_failed` | Full Phase-1 closure (over-deploy as defense) |
| No endpoint catalogs in DB | true | `no_endpoint_catalogs` | Full Phase-1 closure |
| Diff empty after filters (test/lockfile only) | false | `empty_diff_after_filters` | Empty downstream — legitimately nothing to deploy |
| git clone failed | true | `git_clone_failed` | Full Phase-1 closure |
| Claude classification crashed | true | `llm_classification_failed` | Full Phase-1 closure |
| LLM said no handler touched | false | `no_handler_code_touched` | Empty downstream |
| codebase-memory-mcp unavailable / failed | false | (no fallback flag) | Continue without pre-analysis; LLM does impact reasoning solo (slower, less precise) |

## Files

| File | Purpose |
|---|---|
| `app.py` | FastAPI app + endpoints |
| `models.py` | Pydantic request/response models |
| `analyzer.py` | Both endpoints' core logic — orchestration, resolution pipeline, closure walk |
| `db.py` | Postgres connection pool |
| `github_client.py` | Parse PR URLs, fetch PR meta, fetch PR file diffs |
| `git_client.py` | Shallow clone of PR HEAD with PAT auth + cleanup |
| `claude_client.py` | `claude -p` subprocess wrapper, system prompt, pre-analysis injection |
| `mcp_indexer.py` | codebase-memory-mcp wrapper — index repo, read SQLite directly, line-range overlap, transitive callers |
| `diff_hunks.py` | Parse unified-diff patches into post-PR line ranges of actual changes (NOT full hunk extents) |
| `requirements.txt` | Deps |

## Known limitations

1. **Python call-graph is partial.** codebase-memory-mcp's CALLS edges miss external library calls (`httpx`, `requests`, queue clients). Transitive impact via shared utils works for in-package internal calls; broader cases rely on Claude's Grep fallback.
2. **codebase-memory-mcp is pre-1.0.** Pinned to `v0.6.1`. Schema drift on upgrade would degrade us to LLM-only mode (we have a startup schema check).
3. **GitHub PAT briefly appears in `ps`** during clone subprocess. Fine for local-dev; production should use a git credential helper.
4. **No concurrent PR processing.** One PR at a time per process.
5. **Output verbosity dominates cost, not input.** Prompt caching is already on automatically via `claude -p` (verified: ~75% input-token savings on warm calls within the cache window). Most billing now comes from `output_tokens` — Claude's rationale + resolution candidates can balloon the response. Capping output via prompt instructions is a next-step lever. Haiku 4.5 was tried as a small-PR optimisation but is unreliable here (sometimes outputs prose instead of JSON, sometimes ignores the "trust the pre-analysis" guidance), so auto-Haiku is **disabled by default**. Opt back in with `PR_IMPACT_SMALL_PR_FILES`/`PR_IMPACT_SMALL_PR_DIFF_CHARS` env vars if you want to retry on a different workload.
6. **Resolution is read-only.** This service never mutates cartograph's data. New components introduced by a PR are surfaced as `unresolved_new_dependencies` for the operator to onboard separately.

## Possible next steps

- Cap output verbosity via system prompt + use Haiku 4.5 for small PRs.
- Concurrent PR processing with disk-cache eviction by `head_sha`.
- GitHub webhook → background pre-clone, so first call latency drops to LLM-only.
- Bundle the codebase-memory-mcp pre-pass into cartograph's iterator pipeline so the per-handler index is materialised at indexing time, not re-computed per PR.
