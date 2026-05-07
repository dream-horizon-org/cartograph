"""FastAPI app — PR Impact Agent (Phase 1).

POST /analyze  →  given a GitHub repo or PR URL, returns the transitive
                  downstream component graph from the cartograph DB.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException

from .analyzer import analyze, analyze_changes, parse_github_url
from .db import close_pool, init_pool
from .github_client import parse_pr_url
from .models import (
    AnalyzeChangesRequest,
    AnalyzeChangesResponse,
    AnalyzeRequest,
    AnalyzeResponse,
)


logger = logging.getLogger(__name__)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    init_pool()
    logger.info("pr_impact_agent: db pool initialised")
    yield
    close_pool()
    logger.info("pr_impact_agent: db pool closed")


app = FastAPI(
    title="PR Impact Agent",
    version="0.1.0",
    description="Phase 1: repo URL → transitive downstream services.",
    lifespan=_lifespan,
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/analyze", response_model=AnalyzeResponse)
def analyze_endpoint(req: AnalyzeRequest) -> AnalyzeResponse:
    try:
        parse_github_url(req.url)  # validate up-front
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    result = analyze(req.url)
    return AnalyzeResponse(**result)


@app.post("/analyze/changes", response_model=AnalyzeChangesResponse)
def analyze_changes_endpoint(req: AnalyzeChangesRequest) -> AnalyzeChangesResponse:
    try:
        parse_pr_url(req.url)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    result = analyze_changes(req.url)
    return AnalyzeChangesResponse(**result)
