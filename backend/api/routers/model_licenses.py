"""Model-notice API: licence acceptance (enforced on use) and the opt-in reviewed install."""
from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from api.dependencies import require_admin
from core.browser_guard import reject_cross_site_get
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from services.model_licenses import ModelTermsError, ReviewedInstalls, load_registry

router = APIRouter()
logger = logging.getLogger("omnivoice.model_licenses")


class PrepareRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    repo_id: str = Field(min_length=3, max_length=200)
    target: str = Field(default="local", max_length=200)


class AcceptRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    repo_id: str = Field(min_length=3, max_length=200, pattern=r"^[A-Za-z0-9][\w.-]*/[\w.-]+$")
    fingerprint: str = Field(pattern=r"^v2:[a-f0-9]{64}$")
    accepted: StrictBool


class RevokeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    repo_id: str = Field(min_length=3, max_length=200, pattern=r"^[A-Za-z0-9][\w.-]*/[\w.-]+$")


class CommitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    plan_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    plan_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    acknowledged_document_ids: list[str] = Field(max_length=100)
    acknowledged: StrictBool
    target: Literal["local"] = "local"


def _catalog() -> list[dict]:
    from api.routers.setup.models import KNOWN_MODELS
    return KNOWN_MODELS


def _source() -> str:
    from api.routers.setup.download import _download_endpoint
    return (_download_endpoint() or "https://huggingface.co").rstrip("/")


@lru_cache(maxsize=1)
def reviewed_installs() -> ReviewedInstalls:
    from core.config import DATA_DIR
    from core.version import APP_VERSION
    return ReviewedInstalls(Path(DATA_DIR) / "model-notices", load_registry, _catalog,
                            _source, app_version=APP_VERSION)


def _fail(exc: ModelTermsError) -> HTTPException:
    return HTTPException(status_code=409, detail=exc.detail())


def _fetch_file(repo_id: str, revision: str, filename: str,
                endpoint: str, local_only: bool) -> str:
    from huggingface_hub import hf_hub_download
    # Keep unverified/reviewed bytes outside the cache consulted by legacy SDKs.
    cache_dir = str(reviewed_installs().store / "artifacts")
    from services.hf_auth import token_for_endpoint
    from services.token_resolver import resolve

    token = False
    if not local_only:
        resolved = resolve()
        token = token_for_endpoint(endpoint, resolved.token if resolved else None) or False
    return hf_hub_download(repo_id=repo_id, revision=revision, filename=filename,
                           endpoint=endpoint, token=token, local_files_only=local_only,
                           cache_dir=cache_dir)


@router.post("/models/install/prepare")
def prepare_model_install(req: PrepareRequest) -> dict:
    """Read bundled evidence only; never resolve/download model weights."""
    try:
        return reviewed_installs().prepare(req.repo_id, req.target)
    except ModelTermsError as exc:
        raise _fail(exc) from exc


@router.post("/models/install/commit")
def commit_model_install(req: CommitRequest) -> dict:
    """Persist acknowledgement before a bounded exact-file reviewed transfer.

    This synchronous endpoint runs in FastAPI's worker threadpool. Failed transfer
    retains the exact receipt for retry and never invokes the ordinary installer.
    No reviewed production rows are currently enabled. Upstream gated access and
    worker review need their own verified protocol before those rows can be ready.
    """
    service = reviewed_installs()
    try:
        receipt = service.commit(req.plan_id, req.plan_digest,
                                 req.acknowledged_document_ids, req.acknowledged, req.target)
        result = service.download(req.plan_id, req.plan_digest, _fetch_file)
        return {**result, "receipt": receipt}
    except ModelTermsError as exc:
        raise _fail(exc) from exc
    except Exception as exc:
        # Never expose tokens, local paths, provider bodies or fallback sources.
        logger.warning("Reviewed model transfer failed: %s", type(exc).__name__)
        raise HTTPException(status_code=503, detail={"code": "reviewed_transfer_failed"}) from exc


@router.get("/models/licenses/receipts/{plan_digest}",
            dependencies=[Depends(reject_cross_site_get)])
def export_model_receipt(plan_digest: str, response: Response) -> dict:
    """Export one local receipt and its exact archived evidence; no network."""
    response.headers["Cache-Control"] = "no-store"
    try:
        return reviewed_installs().export(plan_digest)
    except ModelTermsError as exc:
        raise _fail(exc) from exc


@router.delete("/models/licenses/receipts/{plan_digest}")
def remove_model_receipt(plan_digest: str) -> dict:
    """Remove local acknowledgement only; keep files and historical evidence."""
    try:
        reviewed_installs().remove(plan_digest)
        return {"removed": True}
    except ModelTermsError as exc:
        raise _fail(exc) from exc


@router.post("/models/licenses/verify/{plan_digest}")
def verify_model_install(plan_digest: str) -> dict:
    """Explicit verification helper; legacy first-use/SDKs do not yet call it."""
    try:
        return reviewed_installs().verify_installed(plan_digest, _fetch_file)
    except ModelTermsError as exc:
        raise _fail(exc) from exc
    except Exception as exc:
        raise HTTPException(status_code=409, detail={"code": "installed_evidence_unavailable"}) from exc


@router.get("/models/licenses/acceptance/{repo_id:path}",
            dependencies=[Depends(reject_cross_site_get)])
def get_model_licence_acceptance(repo_id: str) -> dict:
    from services import model_acceptance
    return model_acceptance.status(repo_id)


@router.get("/models/licenses/details/{repo_id:path}",
            dependencies=[Depends(reject_cross_site_get)])
def get_model_licence_details(repo_id: str) -> dict:
    """Licence disclosure, acceptance state and acceptance history for one model."""
    from services import model_acceptance
    return model_acceptance.details(repo_id)


@router.post("/models/licenses/accept", dependencies=[Depends(require_admin)])
def accept_model_licence(body: AcceptRequest) -> dict:
    """Record that the user confirmed they hold the rights these exact terms require."""
    from services import model_acceptance
    if body.accepted is not True:
        raise HTTPException(status_code=400, detail={"code": "acceptance_required"})
    try:
        return model_acceptance.accept(body.repo_id, body.fingerprint)
    except ValueError as exc:
        # The terms changed since the user saw them: show the new terms first.
        raise HTTPException(status_code=409, detail={"code": "terms_changed"}) from exc


@router.post("/models/licenses/revoke", dependencies=[Depends(require_admin)])
def revoke_model_licence(body: RevokeRequest) -> dict:
    from services import model_acceptance
    return model_acceptance.revoke(body.repo_id)
