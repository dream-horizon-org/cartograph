"""FastAPI app — PR Impact Agent (Phase 1).

POST /analyze  →  given a GitHub repo or PR URL, returns the transitive
                  downstream component graph from the cartograph DB.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from typing import Optional

from fastapi import FastAPI, Header, HTTPException

from .analyzer import analyze, analyze_changes, parse_github_url
from .db import close_pool, init_pool
from .deployer_client import (
    DeployerAuthError,
    close_channel as deployer_close_channel,
    init_channel as deployer_init_channel,
)
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
    deployer_init_channel()  # no-op if DEPLOYER_GRPC_ENDPOINT is unset
    yield
    deployer_close_channel()
    close_pool()
    logger.info("pr_impact_agent: db pool + deployer channel closed")


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
def analyze_endpoint(
    req: AnalyzeRequest,
    authorization: Optional[str] = Header(None),
) -> AnalyzeResponse:
    try:
        parse_github_url(req.url)  # validate up-front
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    try:
        result = analyze(req.url, auth_token=authorization)
    except DeployerAuthError as e:
        raise HTTPException(
            status_code=401,
            detail=f"Deployer rejected the forwarded token — refresh and retry. ({e})",
        )
    return AnalyzeResponse(**result)


@app.post("/analyze/changes", response_model=AnalyzeChangesResponse)
def analyze_changes_endpoint(
    req: AnalyzeChangesRequest,
    authorization: Optional[str] = Header(None),
) -> AnalyzeChangesResponse:
    try:
        parse_pr_url(req.url)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    try:
        result = analyze_changes(req.url, auth_token=authorization)
    except DeployerAuthError as e:
        raise HTTPException(
            status_code=401,
            detail=f"Deployer rejected the forwarded token — refresh and retry. ({e})",
        )
    return AnalyzeChangesResponse(**result)
