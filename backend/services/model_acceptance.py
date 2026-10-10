"""Per-model licence acceptance: a non-commercial-category model is unusable until accepted.

VoiceStudio is not the licensor of any model and cannot grant model access.
Acceptance records that the user confirmed they have the rights the model's
own licence requires (for example non-commercial use, or a separate grant
from the rights holder). It is a local, honest-acknowledgement gate, not a
security boundary and not legal clearance.

Every model whose declared licence category is not ``commercial`` (see
``model_licenses.license_category``) needs acceptance. Acceptance is bound to
a fingerprint of the recorded terms, so changed terms ask again. Unknown and
unmapped licences fail closed: they are gated.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
from pathlib import Path
from typing import Iterable

from services import model_licenses

logger = logging.getLogger(__name__)

ERROR_CODE = "model_licence_required"
_KEY_PREFIX = "model_licence_accepted:"
# Pre-existing per-engine acceptances that covered the same upstream terms.
# They count once, so users who already accepted are not asked twice.
_LEGACY_ENGINE_ACCEPTANCE = {"supertone/supertonic-3": "supertonic3"}

_REPO = re.compile(r"[A-Za-z0-9][\w.-]*/[\w.-]+\Z")
_cache_lock = threading.Lock()
_cache: tuple[float, dict] | None = None


class ModelLicenceNotAccepted(RuntimeError):
    """Raised before a gated model is loaded or used without acceptance."""

    def __init__(self, models: list[dict], reason: str | None = None):
        self.models = models
        names = ", ".join(m["repo_id"] for m in models)
        super().__init__(
            f"Licence not accepted for {names}. Review and accept it in Model Catalogue."
            if models
            else "Model licence records are unreadable, so models cannot be verified. "
            "Reinstall or update VoiceStudio."
        )
        self.reason = reason

    def detail(self) -> dict:
        return {"code": ERROR_CODE, "message": str(self), "models": self.models}


def _registry() -> dict:
    """Registry cached by file mtime; it changes only on app update."""
    global _cache
    path = model_licenses.REGISTRY_PATH
    mtime = Path(path).stat().st_mtime
    with _cache_lock:
        if _cache is None or _cache[0] != mtime:
            _cache = (mtime, model_licenses.load_registry(path))
        return _cache[1]


def _disclosure(repo_id: str) -> dict:
    try:
        return model_licenses.disclosure(repo_id, _registry())
    except Exception:  # noqa: BLE001 — a malformed registry must fail closed
        logger.warning("model licence registry unreadable", exc_info=True)
        return {"license_category": "unknown", "license": None, "blockers": ["registry_invalid"]}


# Everything the licence dialog shows about the terms. Dates such as
# ``evidence_checked_at`` are excluded: a re-check that changes nothing must not
# ask every user again.
_TERM_FIELDS = (
    "license", "credit", "notes", "license_category", "commercial_inference",
    "commercial_outputs", "redistribution", "voice_rights", "source_url",
    "evidence_url", "evidence_revision", "runtime_revision",
)
_VARIANT_FIELDS = (
    "id", "label", "license_category", "commercial_inference", "commercial_outputs",
    "evidence_url",
)
_OBSERVATION_FIELDS = ("license", "license_status", "output_terms")
_HISTORY_LIMIT = 20


def _documents(repo_id: str) -> list[list[str]]:
    try:
        rows = _registry()["models"]
    except Exception:  # noqa: BLE001 — the disclosure already reports the registry
        return []
    row = next((r for r in rows if r["id"].casefold() == repo_id.casefold()), None)
    docs = (row or {}).get("documents") or []
    return sorted([str(d.get("id")), str(d.get("sha256"))] for d in docs if isinstance(d, dict))


def terms(info: dict, repo_id: str | None = None) -> dict:
    """The user-visible terms a model's acceptance is bound to."""
    variants = []
    for v in info.get("variants") or []:
        obs = v.get("observations") if isinstance(v.get("observations"), dict) else {}
        variants.append({
            **{k: v.get(k) for k in _VARIANT_FIELDS},
            **{f"observations.{k}": obs.get(k) for k in _OBSERVATION_FIELDS},
        })
    return {
        **{k: info.get(k) for k in _TERM_FIELDS},
        "variants": sorted(variants, key=lambda v: str(v.get("id"))),
        "documents": _documents(repo_id) if repo_id else [],
    }


def fingerprint(info: dict, repo_id: str | None = None) -> str:
    """Digest of the terms a user accepts; any change to them asks again."""
    blob = json.dumps(terms(info, repo_id), sort_keys=True, separators=(",", ":"))
    return "v2:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _key(repo_id: str) -> str:
    return _KEY_PREFIX + repo_id.casefold()


_UNREADABLE = object()


def _record(repo_id: str):
    """Stored record, ``None`` when absent, ``_UNREADABLE`` when the read failed.

    Records are JSON ``{"last": {...}, "accepted_terms": {...}, "history": [...]}``.
    Earlier builds stored a bare fingerprint or ``"revoked"``; those still read,
    as an undated action.
    """
    from services import settings_store

    try:
        raw = settings_store.get_text(_key(repo_id))
    except Exception:  # noqa: BLE001 — unreadable settings fail closed
        logger.warning("model licence acceptance unreadable", exc_info=True)
        return _UNREADABLE
    if raw is None:
        return None
    try:
        value = json.loads(raw)
    except ValueError:
        value = None
    if isinstance(value, dict) and isinstance(value.get("last"), dict):
        return value
    action = "withdrawn" if raw == "revoked" else "accepted"
    entry = {"action": action, "at": None, "fingerprint": None if action == "withdrawn" else raw}
    return {"last": entry, "accepted_terms": None, "history": [entry]}


def _write(repo_id: str, action: str, current: str, snapshot: dict | None) -> None:
    from datetime import datetime, timezone

    from services import settings_store

    try:
        from core.version import APP_VERSION
    except Exception:  # noqa: BLE001
        APP_VERSION = None
    previous = _record(repo_id)
    previous = previous if isinstance(previous, dict) else {}
    entry = {
        "action": action,
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "fingerprint": current,
        "app_version": APP_VERSION,
    }
    record = {
        "last": entry,
        "accepted_terms": snapshot if action == "accepted" else previous.get("accepted_terms"),
        "accepted_at": entry["at"] if action == "accepted" else previous.get("accepted_at"),
        "history": ([entry] + list(previous.get("history") or []))[:_HISTORY_LIMIT],
    }
    settings_store.set_text(_key(repo_id), json.dumps(record, sort_keys=True))


def _legacy_accepted(repo_id: str) -> bool:
    engine = _LEGACY_ENGINE_ACCEPTANCE.get(repo_id.casefold())
    if engine is None:
        return False
    from services import settings_store

    try:
        return settings_store.get_license_accepted(engine)
    except Exception:  # noqa: BLE001
        return False


def _sync_legacy(repo_id: str, accepted: bool) -> None:
    """Engines with their own gate (Supertonic-3) read the per-engine flag, so the
    two records must never disagree."""
    engine = _LEGACY_ENGINE_ACCEPTANCE.get(repo_id.casefold())
    if engine is None:
        return
    from services import settings_store

    settings_store.set_license_accepted(engine, accepted)


def sync_from_engine(engine_id: str, accepted: bool) -> None:
    """Called by the legacy per-engine toggle so it updates this record too."""
    for repo_id, engine in _LEGACY_ENGINE_ACCEPTANCE.items():
        if engine != engine_id:
            continue
        registered = next(
            (r["id"] for r in _registry()["models"] if r["id"].casefold() == repo_id), None
        )
        if registered is None:
            continue
        if accepted:
            accept(registered, status(registered)["fingerprint"])
        else:
            revoke(registered)


def _changed_fields(before: dict | None, after: dict) -> list[str]:
    if not isinstance(before, dict):
        return []
    return sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))


def _evaluate(repo_id: str) -> tuple[dict, object]:
    info = _disclosure(repo_id)
    category = info.get("license_category") or "unknown"
    required = category != "commercial"
    current_terms = terms(info, repo_id)
    current = fingerprint(info, repo_id)
    record = _record(repo_id)  # one read; an unreadable store never counts as absent
    last = record.get("last") if isinstance(record, dict) else None
    if not required:
        state = "not_required"
    elif record is _UNREADABLE:
        state = "not_accepted"
    elif record is None:
        state = "accepted" if _legacy_accepted(repo_id) else "not_accepted"
    elif last["action"] == "withdrawn":
        state = "withdrawn"
    elif last.get("fingerprint") == current:
        state = "accepted"
    else:
        state = "terms_updated"
    result = {
        "repo_id": repo_id,
        "license": info.get("license"),
        "category": category,
        "required": required,
        "accepted": state in ("accepted", "not_required"),
        "state": state,
        "fingerprint": current,
        "last_action": last if isinstance(last, dict) else None,
        "accepted_at": record.get("accepted_at") if isinstance(record, dict) else None,
        "changed_fields": (
            _changed_fields(record.get("accepted_terms"), current_terms)
            if state == "terms_updated" and isinstance(record, dict) else []
        ),
    }
    return result, (current_terms, info, record)


def status(repo_id: str) -> dict:
    """Acceptance state of one model: ``state`` is not_required, accepted,
    not_accepted, withdrawn or terms_updated."""
    return _evaluate(repo_id)[0]


def details(repo_id: str) -> dict:
    """Everything the licence dialog shows: disclosure, acceptance and history."""
    result, (_terms, info, record) = _evaluate(repo_id)
    history = record.get("history") if isinstance(record, dict) else []
    return {"repo_id": repo_id, "info": info, "acceptance": result, "history": history or []}


def accept(repo_id: str, expected_fingerprint: str) -> dict:
    """Record acceptance of exactly the terms the user was shown."""
    result, (current_terms, _info, _record_) = _evaluate(repo_id)
    if expected_fingerprint != result["fingerprint"]:
        raise ValueError("terms_changed")
    _write(repo_id, "accepted", result["fingerprint"], current_terms)
    _sync_legacy(repo_id, True)
    return status(repo_id)


def revoke(repo_id: str) -> dict:
    # A withdrawn record also overrides any legacy acceptance.
    _write(repo_id, "withdrawn", status(repo_id)["fingerprint"], None)
    _sync_legacy(repo_id, False)
    return status(repo_id)


def ensure_accepted(repo_ids: Iterable[str]) -> None:
    """Raise ``ModelLicenceNotAccepted`` listing every unaccepted gated model."""
    missing = []
    for repo_id in dict.fromkeys(r for r in repo_ids if r):
        state = status(repo_id)
        if state["required"] and not state["accepted"]:
            missing.append({k: state[k] for k in ("repo_id", "license", "category", "fingerprint")})
    if missing:
        raise ModelLicenceNotAccepted(missing)


def repos_for_engine(engine_id: str, identity: str | None = None) -> list[str]:
    """Registry models an engine loads.

    ``identity`` is the concrete model a preference-dependent engine will load
    (for example mlx-audio's selected repo). Without one, every registry model
    recorded for the engine applies: the engine ships them all.
    """
    if identity and _REPO.fullmatch(identity):
        return [identity]
    if identity:
        # A local model folder is not a registry model (ASR treats it the same way).
        return []
    rows = _registry()["models"]  # unreadable registry: let the caller fail closed
    return [row["id"] for row in rows if engine_id in (row.get("engines") or [])]


def ensure_engine_accepted(engine_id: str, identity: str | None = None) -> None:
    try:
        repos = repos_for_engine(engine_id, identity)
    except Exception as exc:  # noqa: BLE001 — an unreadable registry must not open the gate
        logger.warning("model licence registry unreadable", exc_info=True)
        raise ModelLicenceNotAccepted([], reason="registry_unreadable") from exc
    ensure_accepted(repos)
