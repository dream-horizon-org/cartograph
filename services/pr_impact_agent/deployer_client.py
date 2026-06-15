"""HTTP/1.1 client for the Odin deployer's
dream11.od.service.v1.ServiceService/GetLatestCatalogueReleaseByRepository.

The deployer sits behind an istio-envoy gateway that exposes the
grpc_http1_bridge — gRPC methods are reachable as plain HTTP/1.1 POSTs
to /<service>/<method> with `content-type: application/grpc` and a
gRPC-framed protobuf body. Native HTTP/2 gRPC isn't available on the
internal LB, so we use this bridge instead of `grpcio`.

Auth flow: client passes a Bearer token in the Authorization header of
their pr_impact_agent request; we forward it verbatim in the upstream
`authorization` header. Never logged, never persisted.
"""

from __future__ import annotations

import logging
import os
import struct
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import httpx

from .proto_stubs import odin_deployer_pb2 as pb


logger = logging.getLogger(__name__)


_GRPC_ENDPOINT = os.getenv("DEPLOYER_GRPC_ENDPOINT", "")
_TIMEOUT = int(os.getenv("DEPLOYER_TIMEOUT", "10"))
_MAX_WORKERS = int(os.getenv("DEPLOYER_MAX_WORKERS", "8"))
_INSECURE = os.getenv("DEPLOYER_GRPC_INSECURE", "").lower() == "true"

_METHOD_PATH = (
    "/dream11.od.service.v1.ServiceService/GetLatestCatalogueReleaseByRepository"
)

# gRPC status codes (subset we care about)
_GRPC_OK = 0
_GRPC_NOT_FOUND = 5
_GRPC_PERMISSION_DENIED = 7
_GRPC_UNAUTHENTICATED = 16
_GRPC_UNAVAILABLE = 14
_GRPC_DEADLINE_EXCEEDED = 4


class DeployerError(RuntimeError):
    pass


class DeployerAuthError(DeployerError):
    pass


class DeployerNotFound(DeployerError):
    pass


_channel_lock = threading.Lock()
_client: httpx.Client | None = None
_base_url: str = ""


def init_channel(endpoint: str | None = None) -> None:
    """Open the long-lived HTTP client to the deployer. Idempotent."""
    global _client, _base_url
    target = endpoint or _GRPC_ENDPOINT
    if not target:
        logger.info(
            "deployer client NOT initialised — DEPLOYER_GRPC_ENDPOINT is empty; "
            "deploy_plan will be skipped on every request"
        )
        return
    with _channel_lock:
        if _client is not None:
            return
        scheme = "http" if _INSECURE else "https"
        _base_url = f"{scheme}://{target}"
        _client = httpx.Client(
            http2=False,
            timeout=_TIMEOUT,
            base_url=_base_url,
        )
        logger.info(
            "deployer client: %s %s",
            "insecure HTTP/1.1" if _INSECURE else "TLS HTTP/1.1",
            _base_url,
        )


def close_channel() -> None:
    global _client
    with _channel_lock:
        if _client is not None:
            try:
                _client.close()
            except Exception:
                logger.exception("error closing deployer client")
            _client = None


def _get_client() -> httpx.Client:
    if _client is None:
        init_channel()
    if _client is None:
        raise DeployerError(
            "deployer client is not configured — set DEPLOYER_GRPC_ENDPOINT"
        )
    return _client


def _normalise_auth(token: str) -> str:
    t = token.strip()
    return t if t.lower().startswith("bearer ") else f"Bearer {t}"


def _frame(payload: bytes) -> bytes:
    return b"\x00" + struct.pack(">I", len(payload)) + payload


def _unframe(body: bytes) -> bytes:
    if len(body) < 5:
        raise DeployerError(f"deployer returned short body ({len(body)} bytes)")
    msg_len = struct.unpack(">I", body[1:5])[0]
    payload = body[5:5 + msg_len]
    if len(payload) != msg_len:
        raise DeployerError(
            f"deployer body length mismatch: header={msg_len} actual={len(payload)}"
        )
    return payload


def _raise_for_status(grpc_status: int, msg: str) -> None:
    snippet = msg[:200]
    if grpc_status in (_GRPC_UNAUTHENTICATED, _GRPC_PERMISSION_DENIED):
        raise DeployerAuthError(f"deployer auth status={grpc_status}: {snippet}")
    if grpc_status == _GRPC_NOT_FOUND:
        raise DeployerNotFound(f"deployer NOT_FOUND: {snippet}")
    if grpc_status in (_GRPC_UNAVAILABLE, _GRPC_DEADLINE_EXCEEDED):
        raise DeployerError(f"deployer transport status={grpc_status}: {snippet}")
    raise DeployerError(f"deployer gRPC status={grpc_status}: {snippet}")


def lookup_service(github_url: str,
                   concrete: bool,
                   auth_token: str,
                   timeout: int | None = None) -> dict[str, Any]:
    """Calls GetLatestCatalogueReleaseByRepository via the HTTP/1.1 bridge.
    Returns a dict with keys service_name, version, created_at."""
    client = _get_client()
    req = pb.GetLatestCatalogueReleaseByRepositoryRequest(
        github_url=github_url,
        concrete=concrete,
    )
    body = _frame(req.SerializeToString())
    headers = {
        "content-type": "application/grpc",
        "te": "trailers",
        "authorization": _normalise_auth(auth_token),
    }

    logger.info("deployer lookup: repo=%s concrete=%s", github_url, concrete)

    try:
        r = client.post(
            _METHOD_PATH,
            content=body,
            headers=headers,
            timeout=timeout or _TIMEOUT,
        )
    except httpx.TimeoutException as e:
        raise DeployerError(f"deployer transport timeout: {e}") from e
    except httpx.RequestError as e:
        raise DeployerError(f"deployer transport error: {e}") from e

    if r.status_code != 200:
        raise DeployerError(
            f"deployer HTTP {r.status_code}: {r.text[:200]}"
        )

    # Envoy's grpc_http1_bridge surfaces grpc-status as a response header
    # (and may also include it in trailers; httpx exposes headers only).
    grpc_status_h = r.headers.get("grpc-status")
    grpc_msg = r.headers.get("grpc-message", "")

    if grpc_status_h is not None and grpc_status_h != "0":
        _raise_for_status(int(grpc_status_h), grpc_msg)

    payload = _unframe(r.content)
    resp = pb.GetLatestCatalogueReleaseByRepositoryResponse()
    resp.ParseFromString(payload)

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
