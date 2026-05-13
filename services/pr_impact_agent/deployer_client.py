"""gRPC client for the Odin deployer's
dream11.od.service.v1.ServiceService/GetLatestCatalogueReleaseByRepository.

We stay on a single long-lived TLS channel for the lifetime of the
FastAPI process (created and closed by the app's lifespan handler).
Per-call deadline is governed by DEPLOYER_TIMEOUT.

Auth flow: client passes a Bearer token in the Authorization header of
their pr_impact_agent request; we forward it verbatim as the gRPC
`authorization` metadata key. Never logged, never persisted.

For dependent components: send concrete=true (catalogued / released).
For the root component (the PR's own repo): send concrete=false so the
deployer doesn't filter out -SNAPSHOT versions (the in-flight build).
"""

from __future__ import annotations

import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import grpc

from .proto_stubs import odin_deployer_pb2 as pb
from .proto_stubs import odin_deployer_pb2_grpc as pb_grpc


logger = logging.getLogger(__name__)


_GRPC_ENDPOINT = os.getenv("DEPLOYER_GRPC_ENDPOINT", "")
_TIMEOUT = int(os.getenv("DEPLOYER_TIMEOUT", "10"))
_MAX_WORKERS = int(os.getenv("DEPLOYER_MAX_WORKERS", "8"))
# Set to "true" for plaintext channel (local dev against an insecure
# deployer mock). Defaults to TLS for production endpoints.
_INSECURE = os.getenv("DEPLOYER_GRPC_INSECURE", "").lower() == "true"


class DeployerError(RuntimeError):
    pass


class DeployerAuthError(DeployerError):
    pass


class DeployerNotFound(DeployerError):
    pass


_channel_lock = threading.Lock()
_channel: grpc.Channel | None = None
_stub: pb_grpc.ServiceServiceStub | None = None


def init_channel(endpoint: str | None = None) -> None:
    """Open the long-lived gRPC channel. Idempotent — calling again is a
    no-op while the existing channel is open."""
    global _channel, _stub
    target = endpoint or _GRPC_ENDPOINT
    if not target:
        logger.info(
            "deployer channel NOT initialised — DEPLOYER_GRPC_ENDPOINT is empty; "
            "deploy_plan will be skipped on every request"
        )
        return
    with _channel_lock:
        if _channel is not None:
            return
        if _INSECURE:
            _channel = grpc.insecure_channel(target)
            logger.info("deployer channel: insecure %s", target)
        else:
            creds = grpc.ssl_channel_credentials()
            _channel = grpc.secure_channel(target, creds)
            logger.info("deployer channel: TLS %s", target)
        _stub = pb_grpc.ServiceServiceStub(_channel)


def close_channel() -> None:
    global _channel, _stub
    with _channel_lock:
        if _channel is not None:
            try:
                _channel.close()
            except Exception:
                logger.exception("error closing deployer channel")
            _channel = None
            _stub = None


def _get_stub() -> pb_grpc.ServiceServiceStub:
    if _stub is None:
        # Lazy init for test paths that don't go through FastAPI lifespan.
        init_channel()
    if _stub is None:
        raise DeployerError(
            "deployer channel is not configured — set DEPLOYER_GRPC_ENDPOINT"
        )
    return _stub


def _normalise_auth(token: str) -> str:
    t = token.strip()
    return t if t.lower().startswith("bearer ") else f"Bearer {t}"


def lookup_service(github_url: str,
                   concrete: bool,
                   auth_token: str,
                   timeout: int | None = None) -> dict[str, Any]:
    """Calls GetLatestCatalogueReleaseByRepository. Returns a dict with
    keys service_name, version, created_at (any may be None).

    Maps gRPC status codes to our exception types so analyzer code stays
    transport-agnostic."""
    stub = _get_stub()
    req = pb.GetLatestCatalogueReleaseByRepositoryRequest(
        github_url=github_url,
        concrete=concrete,
    )
    metadata = [("authorization", _normalise_auth(auth_token))]

    logger.info("deployer lookup: repo=%s concrete=%s", github_url, concrete)

    try:
        resp: pb.GetLatestCatalogueReleaseByRepositoryResponse = (
            stub.GetLatestCatalogueReleaseByRepository(
                req,
                metadata=metadata,
                timeout=timeout or _TIMEOUT,
            )
        )
    except grpc.RpcError as e:
        # grpc.RpcError instances also implement Call interface.
        code = e.code() if hasattr(e, "code") else grpc.StatusCode.UNKNOWN
        detail = (e.details() if hasattr(e, "details") else "") or ""
        snippet = detail[:200]
        if code in (grpc.StatusCode.UNAUTHENTICATED,
                    grpc.StatusCode.PERMISSION_DENIED):
            raise DeployerAuthError(f"deployer auth {code.name}: {snippet}") from e
        if code == grpc.StatusCode.NOT_FOUND:
            raise DeployerNotFound(f"deployer NOT_FOUND: {snippet}") from e
        if code in (grpc.StatusCode.UNAVAILABLE,
                    grpc.StatusCode.DEADLINE_EXCEEDED):
            raise DeployerError(
                f"deployer transport {code.name}: {snippet}"
            ) from e
        raise DeployerError(f"deployer gRPC {code.name}: {snippet}") from e

    # Proto3 optional fields: HasField() tells us whether they were set.
    return {
        "service_name": resp.service_name if resp.HasField("service_name") else None,
        "version": resp.version if resp.HasField("version") else None,
        "created_at": resp.created_at if resp.HasField("created_at") else None,
    }


def build_plan(components: list[dict[str, Any]],
               auth_token: str
               ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Look up every component's repo at the deployer in parallel.

    Inputs: list of {github_url, canonical_name, is_root}. The is_root
    flag controls the `concrete` field (false for the PR's own repo so
    -SNAPSHOT versions are included, true for dependents).

    Returns (services_to_deploy, unresolved_services).

    Raises DeployerAuthError if ANY lookup returns UNAUTHENTICATED /
    PERMISSION_DENIED — caller (analyzer → app) turns that into HTTP 401."""
    services_to_deploy: list[dict[str, Any]] = []
    unresolved_services: list[dict[str, Any]] = []

    # Dedupe by github_url; prefer is_root=True if duplicates disagree.
    deduped: dict[str, dict[str, Any]] = {}
    for c in components:
        url = c.get("github_url")
        name = c.get("canonical_name")
        if not url:
            unresolved_services.append({
                "component_canonical_name": name,
                "github_url": None,
                "reason": "no_github_url_in_cartograph",
            })
            continue
        if url in deduped:
            if c.get("is_root"):
                deduped[url]["is_root"] = True
        else:
            deduped[url] = {
                "github_url": url,
                "canonical_name": name,
                "is_root": bool(c.get("is_root")),
            }

    if not deduped:
        return services_to_deploy, unresolved_services

    def _one(item: dict[str, Any]) -> dict[str, Any]:
        url = item["github_url"]
        is_root = item["is_root"]
        # Root uses concrete=false directly (SNAPSHOTs allowed). Dependents
        # try concrete=true first (only stable, catalogued releases); if
        # that returns empty (200 with null serviceName/version because the
        # filter excluded all rows), fall back to concrete=false to pick
        # up a SNAPSHOT version. Per-call retry inside this thread keeps
        # the outer parallelism simple.
        fallback_used = False
        try:
            data = lookup_service(
                url,
                concrete=not is_root,
                auth_token=auth_token,
            )
            empty = not (data.get("service_name") or data.get("version"))
            if empty and not is_root:
                logger.info(
                    "deployer empty for %s with concrete=true; "
                    "retrying with concrete=false (SNAPSHOT fallback)",
                    url,
                )
                data = lookup_service(
                    url, concrete=False, auth_token=auth_token,
                )
                fallback_used = True
            return {
                "status": "ok",
                "data": data,
                "fallback_used": fallback_used,
                **item,
            }
        except DeployerNotFound:
            return {"status": "not_found", **item}
        except DeployerAuthError as e:
            return {"status": "auth_error", "error": str(e), **item}
        except DeployerError as e:
            return {"status": "error", "error": str(e), **item}

    with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as ex:
        futures = [ex.submit(_one, item) for item in deduped.values()]
        results = [f.result() for f in as_completed(futures)]

    auth_errs = [r for r in results if r["status"] == "auth_error"]
    if auth_errs:
        raise DeployerAuthError(auth_errs[0]["error"])

    for r in results:
        if r["status"] == "ok":
            data = r["data"] or {}
            svc_name = data.get("service_name")
            version = data.get("version")
            if not svc_name and not version:
                unresolved_services.append({
                    "component_canonical_name": r["canonical_name"],
                    "github_url": r["github_url"],
                    "reason": "deployer_returned_empty_release",
                })
                continue
            services_to_deploy.append({
                "component_canonical_name": r["canonical_name"],
                "github_url": r["github_url"],
                "is_root": r["is_root"],
                "release": {
                    "name": svc_name,
                    "version": version,
                    "created_at": data.get("created_at"),
                    "from_snapshot_fallback": r.get("fallback_used", False),
                },
            })
        elif r["status"] == "not_found":
            unresolved_services.append({
                "component_canonical_name": r["canonical_name"],
                "github_url": r["github_url"],
                "reason": "deployer_not_found",
            })
        else:
            unresolved_services.append({
                "component_canonical_name": r["canonical_name"],
                "github_url": r["github_url"],
                "reason": f"deployer_error: {r.get('error', '?')[:120]}",
            })

    return services_to_deploy, unresolved_services
