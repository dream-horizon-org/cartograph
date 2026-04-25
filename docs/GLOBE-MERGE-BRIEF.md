# Globe (Phase 6) — Merge Brief

> **Purpose:** captures everything that landed on this branch
> (`feat/globe-experimental`) so that when the PR is merged into
> `feat/trigger-manager-cartograh-mcp`, the canonical 6 docs +
> memory file can be brought up to date in one go.
>
> **This file lives on the experimental branch only.** It is NOT
> intended to ship into the main branch — delete after the PR is
> merged and the proper doc sync is done.

---

## 1. What is the Globe?

A **deletable, experimental** new top-nav tab in the admin UI that
renders the same `/api/graph` payload on a sphere instead of in free
3D space. Nodes are constrained to the surface of an invisible
sphere; edges are great-circle arcs (sync deps) or ballistic arcs
(async fan-out). All Phase 3.10 Graph features are ported: bundled
junctions, dangling stubs, light-of-sight click with layered reveal,
3-zone hover, gold curve-following particles, full drill-down
sidebar (Doc / Slice / Catalog / Bindings in / Bindings out / Flows).

Sphere radius scales with N nodes: `R = max(80, 30·√N)`.

The Graph view is **completely untouched** — Globe is additive.

---

## 2. Commit map

In ship order:

| Commit  | What |
|---------|------|
| `0482ae1` | Phase 6 plan in IMPLEMENTATION-PHASES.md (Globe spec + sub-phase ordering + reshuffle of future phases — phase-flow completion moved from Phase 6 to Phase 7, etc.) |
| `11824a0` | 6.1 — routing + tab shell + sphere projection scaffold |
| `727427b` | 6.2 — great-circle + ballistic edge curves |
| `c3ddd9d` | 6.3 — feature parity: junctions / stubs / LOS click |
| `f12d43f` | 6.4 + 6.5 + render fix (the "missing height" CSS fix that finally made Globe render) |
| `780278c` | Tube-mesh edges + manual curve-following arrows (THREE.Line was too thin to raycast reliably) |
| `10d199e` | Hover cyan glow + decouple async color from LOS amber (async = purple) |
| `91e4571` | Kill straight-chord particles + tune trunk arrows |
| `28c131b` | Arrow raycast off + visible junctions + longer trunks |
| `825628a` | Diagnostic console.log on hover/LOS handlers + refresh visuals |
| `10dde82` | Server-side log sink at /api/debug/log (debugging aid) |
| `fc638f1` | Use `__lineObj` (not `__threeObj`) for link mesh lookup — the bug that broke LOS+hover for several iterations |
| `28f060a` | Per-edge growing-tube reveal + curve-following gold particles |
| `1b14370` | 3-zone hover (caller / convergence / target) |

---

## 3. Files touched

### New / shipped (would migrate to main on merge)
- `src/admin_ui/static/index.html` — Globe tab button + `<section id="globe-view">` shell
- `src/admin_ui/static/app.js` — ~750 lines appended at the bottom (one contiguous `// Phase 6: Globe` block, all `_globe*` prefixed)
- `src/admin_ui/static/style.css` — `// Globe view (Phase 6)` block at the end
- `docs/IMPLEMENTATION-PHASES.md` — Phase 6 plan (commit 0482ae1)

### Existing-file touchpoints (minimal)
- `src/admin_ui/static/index.html` nav: +1 `<button data-tab="globe">`
- `src/admin_ui/static/app.js` `switchTab()`: +2 lines (toggle .active + call `initOrRefreshGlobe`)
- `src/admin_ui/static/app.js` `_applyRoute()`: +1 entry in `knownTabs`
- `src/admin_ui/static/app.js` `<script src=...?v=>`: cache-bust bumped through v=70

### Temporary / should be removed before merge
- `src/admin_ui/server.py` — `POST /api/debug/log` endpoint (writes to `/tmp/cartograph_fe_debug.log`). This was a debugging aid; should be deleted before the PR merges OR kept if still useful.

---

## 4. Architecture decisions

- **Hybrid: copy-and-adapt for data preprocessing + LOS BFS** (junction grouping, stub bucketing, edgeById index, BFS algorithm) since they're pure logic that would risk divergence/bugs if rewritten. **From-scratch** for everything sphere-specific (projection, edge curves, per-tick pinning math, particle animator).
- **Globe is fully self-contained** — separate `globeSnapshot`, `globeInstance`, separate state vars (`_globeLitEdgeIds`, `_globeHoverLitEdgeIds`, `_globeRevealStart`). Reads from but never writes Graph's state.
- **Detail-panel renderers duplicated locally** (`_globeRenderEdgeList`, `_globeRenderFlowList`, `_globeRenderCatalogList`) — couldn't reuse Graph's because they close over the script-scoped `let graphSnapshot` which can't be shadowed via `window.graphSnapshot`. ~80 lines of intentional duplication for module isolation.
- **Tube meshes (not THREE.Line)** for edges so raycasting hits them reliably. `THREE.Line` is 1px wide; the library's `onLinkClick` / `onLinkHover` couldn't fire consistently.
- **Manual arrow cones** as tube children, positioned per-frame from the curve tangent (the library's built-in arrows assume a straight chord — would float in space on a curved edge). Cones have `raycast = () => {}` so they don't intercept hover/click events.
- **Library particles disabled, custom particle animator runs them along the curve** — built-in particles interpolate along the straight chord between source/target node positions, beaming through the globe interior. Our animator samples positions from the tube's stashed `CatmullRomCurve3` each frame.
- **Per-edge growing-tube reveal** via `BufferGeometry.setDrawRange(0, N)`. TubeGeometry indices are path-segment-major, so showing the first N indices renders the source-end portion. Quantised to whole radial rings for clean cap edges. Re-applied after every linkPositionUpdate so the geometry rebuild doesn't reset drawRange.
- **3-zone hover** via canvas mousemove projecting the curve to screen space (24 samples), finding the closest sample to cursor, deriving t (0..1). Junction-aware: middle 33% of a junction trunk/contributor is "convergence". Per-zone glow uses the same catalog-bridging helper (`_globeEffectiveFlowIncomingsForEdge`) as the LOS BFS.

---

## 5. Color precedence (in `_globeLinkColor`)

Highest precedence first:

| State | Color |
|---|---|
| LOS-lit (after edge click) | Amber `#fbbf24` |
| Hover-lit | Cyan `#22d3ee` |
| Async default (publishes / triggers / consumes) | Purple `#c084fc` |
| Stub (orphan catalog / dangling) | Muted slate `#475569` |
| Sync default (calls / reads / writes / runs_on) | Slate `#9ca3af` |

---

## 6. Known limitations / pending polish

- 3-zone split is fixed at 33/33/33 — could be tuned (e.g. more space for convergence on long trunks).
- No tooltip text on hover yet (the Graph view has rich per-zone tooltips). Trivial to add — same `hoverZoneResolve`-style helper, just renders into a DOM element instead of `linkLabel`.
- No keyboard shortcut to clear LOS (Esc in Graph view does this).
- No `sphere wireframe toggle` for depth perception — the spec defaulted to invisible-sphere. Users with poor depth cues might want a faint grid.

---

## 7. What docs to update on merge

Reviewing the canonical 6 docs:

- **HLD.md**:
  - §10.2 (Frontend Behaviour): change "Six tabs..." to "Seven tabs..." and add a Globe tab description block. List the URL scheme entry: `/globe`, `/globe/component/:id`.
  - §11 Future Scope: remove the "list-view tab" entry if still there from Phase 5 prep (it's superseded; Globe is its own thing).
  - §10.1 (API contract): no changes — Globe reuses /api/graph. (Unless the temporary `/api/debug/log` is kept; if so, document it as a debug aid.)
- **SCHEMA.md**: no changes (no schema work).
- **TRIGGER-MANAGEMENT.md**: no changes (no agent / scanner work).
- **AGENT-PROMPTS.md**: no changes (no prompt work).
- **IMPLEMENTATION-PHASES.md**:
  - The Phase 6 plan is already present (from commit 0482ae1). After merge, mark Phase 6 as ✅ SHIPPED with the commit map from §2 above. Update the test count line if any tests were added (none in this branch — Globe is FE-only and the FE has no test harness).
- **ONE-PAGER.md**: probably no change.
- **memory/cartograph_state.md**: append a Phase 6 SHIPPED entry summarizing the Globe.

Also update the IMPLEMENTATION-PHASES.md status header at top to include Phase 6 once shipped.

---

## 8. Removed-from-main note

This branch was carved out of `feat/trigger-manager-cartograh-mcp` to keep the main branch clean. The 14 Globe commits were dropped from main via a hard reset to `bc483d7`, then force-pushed. To re-introduce, raise a PR from `feat/globe-experimental` → `feat/trigger-manager-cartograh-mcp`. If accepted as-is, the doc sync above is the next step.
