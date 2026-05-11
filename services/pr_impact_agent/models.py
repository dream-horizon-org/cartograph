"""Request/response models."""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


class AnalyzeRequest(BaseModel):
    url: str = Field(..., description="GitHub repo URL or PR URL")


class OutboundEdge(BaseModel):
    edge_type: str
    identifier: str
    to_component_id: Optional[str] = None
    to_canonical_name: Optional[str] = None
    to_component_type: Optional[str] = None
    to_github_url: Optional[str] = None
    resolved: bool


class ComponentNode(BaseModel):
    id: str
    canonical_name: str
    display_name: str
    component_type: str
    github_url: Optional[str]
    github_metadata: dict[str, Any] = Field(default_factory=dict)
    is_root: bool
    depth: int
    outbound_edges: list[OutboundEdge] = Field(default_factory=list)


class AnalyzeResponse(BaseModel):
    input_url: str
    repo: str  # owner/repo
    matched: bool
    root_components: list[ComponentNode] = Field(default_factory=list)
    downstream: list[ComponentNode] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    phase: Literal["1"] = "1"


class AnalyzeChangesRequest(BaseModel):
    url: str = Field(..., description="GitHub PR URL (must be /pull/N form)")


class DirectCall(BaseModel):
    edge_type: str
    identifier: str
    to_component_id: Optional[str] = None
    to_canonical_name: Optional[str] = None
    to_component_type: Optional[str] = None
    resolved: bool


class ImpactedEndpoint(BaseModel):
    endpoint: str
    catalog_id: str
    component_id: str
    directly_calls: list[DirectCall] = Field(default_factory=list)


class ResolutionCandidate(BaseModel):
    component_id: str
    canonical_name: str
    component_type: str
    github_url: Optional[str] = None
    score: float
    match_method: str


class Resolution(BaseModel):
    status: Literal["resolved", "ambiguous", "unresolved"]
    candidates: list[ResolutionCandidate] = Field(default_factory=list)


class OutboundCallChange(BaseModel):
    kind: str
    target_hint: str
    host_or_service: str
    evidence: str
    from_endpoint: Optional[str] = None
    llm_match_canonical_name: Optional[str] = None
    resolution: Resolution


class NewEndpoint(BaseModel):
    identifier: str
    description: Optional[str] = None
    evidence: Optional[str] = None


class LlmUsage(BaseModel):
    model: Optional[str] = None
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    cache_creation_input_tokens: Optional[int] = None
    cache_read_input_tokens: Optional[int] = None
    total_cost_usd: Optional[float] = None
    duration_ms: Optional[int] = None
    num_turns: Optional[int] = None


class CodeGraphHandler(BaseModel):
    qualified_name: str
    name: str
    file_path: str
    start_line: int
    end_line: int
    route_path: Optional[str] = None
    route_method: Optional[str] = None
    depth: int = 0


class CodeGraphEvidence(BaseModel):
    indexed: bool
    project_name: Optional[str] = None
    nodes_count: Optional[int] = None
    edges_count: Optional[int] = None
    directly_impacted_handlers: list[CodeGraphHandler] = Field(default_factory=list)
    transitively_impacted_handlers: list[CodeGraphHandler] = Field(default_factory=list)
    note: Optional[str] = None


class AnalyzeChangesResponse(BaseModel):
    input_url: str
    repo: str
    pr_number: Optional[int]
    matched: bool
    fell_back_to_phase_1: bool
    fallback_reason: Optional[str] = None
    rationale: Optional[str] = None
    diff_files_included: list[str] = Field(default_factory=list)
    diff_files_skipped: list[str] = Field(default_factory=list)
    root_components: list[ComponentNode] = Field(default_factory=list)
    impacted_endpoints: list[ImpactedEndpoint] = Field(default_factory=list)
    new_endpoints: list[NewEndpoint] = Field(default_factory=list)
    new_outbound_calls: list[OutboundCallChange] = Field(default_factory=list)
    removed_outbound_calls: list[OutboundCallChange] = Field(default_factory=list)
    unresolved_new_dependencies: list[OutboundCallChange] = Field(default_factory=list)
    downstream: list[ComponentNode] = Field(default_factory=list)
    code_graph_evidence: Optional[CodeGraphEvidence] = None
    llm_usage: Optional[LlmUsage] = None
    warnings: list[str] = Field(default_factory=list)
    phase: Literal["3"] = "3"
