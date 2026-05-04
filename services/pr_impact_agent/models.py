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
