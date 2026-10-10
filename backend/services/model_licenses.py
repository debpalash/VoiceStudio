"""Local model disclosures and an additive reviewed-install foundation.

This is deliberately NOT a global download/first-use enforcement mechanism.
Ordinary installers, SDK loads and remote workers remain unchanged. Production
records currently have incomplete evidence, so no reviewed install is enabled.
No network call, ML import, provider assent or commercial entitlement is implied.
"""
from __future__ import annotations

import copy
import hashlib
import fnmatch
import json
import os
import re
import tempfile
import threading
import time
import uuid
import unicodedata
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Callable
from urllib.parse import urlparse

from core.path_security import UnsafePath, resolve_within

REGISTRY_PATH = Path(__file__).resolve().parents[1] / "config" / "model_licenses.json"
_SHA = re.compile(r"[0-9a-f]{40}\Z")
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_REPO = re.compile(r"[A-Za-z0-9][\w.-]*/[\w.-]+\Z")
PROMPT = {
    "prompt_id": "model-notices",
    "prompt_version": 1,
    "locale": "en",
    "text": "I acknowledge the listed notices for this exact model selection. "
            "This local acknowledgement grants no additional rights and does not "
            "replace an upstream access agreement or verify voice consent. "
            "Reviewed files are downloaded to a separate cache and are not activated "
            "for ordinary model use.",
}


class ModelTermsError(ValueError):
    """Stable, non-retryable boundary error; never feed to SDK fallback."""

    def __init__(self, code: str, blockers: list[str] | None = None):
        self.code = code
        self.blockers = blockers or []
        super().__init__(code)

    def detail(self) -> dict:
        return {"code": self.code, "blockers": self.blockers}


def _unique_object(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise ModelTermsError("registry_invalid", ["duplicate_json_key"])
        out[key] = value
    return out


def read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique_object)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ModelTermsError("evidence_unavailable") from exc
    if not isinstance(value, dict):
        raise ModelTermsError("registry_invalid")
    return value


def digest(value) -> str:
    """Python-only v1 canonical encoding, not a claim of JCS interoperability."""
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"),
                     ensure_ascii=True, allow_nan=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def file_digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def safe_file(value: str) -> bool:
    if not isinstance(value, str) or not value or "\\" in value or ":" in value:
        return False
    parts = value.split("/")
    reserved = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
                *(f"LPT{i}" for i in range(1, 10))}
    return (not PurePosixPath(value).is_absolute()
            and all(p not in {"", ".", ".."} and not p.endswith((".", " "))
                    and p.split(".")[0].upper() not in reserved for p in parts)
            and not any(ord(c) < 32 or c in '*?[]<>|"' for c in value))


def _https(value: str) -> bool:
    if not isinstance(value, str):
        return False
    parsed = urlparse(value)
    return (parsed.scheme == "https" and bool(parsed.hostname)
            and not parsed.username and not parsed.password)


def load_registry(path: Path = REGISTRY_PATH) -> dict:
    value = read_json(path)
    if value.get("schema_version") != 2 or value.get("data_kind", "production") != "production":
        raise ModelTermsError("registry_invalid")
    rows = value.get("models")
    if not isinstance(rows, list) or not rows:
        raise ModelTermsError("registry_invalid")
    seen = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ModelTermsError("registry_invalid")
        rid = row.get("id")
        if not isinstance(rid, str) or not _REPO.fullmatch(rid) or rid.casefold() in seen:
            raise ModelTermsError("registry_invalid")
        if row.get("data_kind", "production") != "production":
            raise ModelTermsError("registry_invalid")
        seen.add(rid.casefold())
    return value


def record_blockers(record: dict) -> list[str]:
    """Readiness is independent of the legacy commercial review boolean."""
    blockers = []
    revision = record.get("runtime_revision")
    if not isinstance(revision, str) or not _SHA.fullmatch(revision):
        blockers.append("runtime_revision_unverified")
    if revision and record.get("revision") != revision:
        blockers.append("evidence_revision_mismatch")
    if record.get("component_closure") != "complete":
        blockers.append("component_closure_incomplete")
    if not record.get("artifacts"):
        blockers.append("artifact_manifest_unverified")
    if not record.get("documents"):
        blockers.append("terms_unverified")
    if record.get("upstream_access") != "not_required":
        # Provider-gated assent/access needs an independent adapter, never a bool.
        blockers.append("upstream_access_unverified")
    return blockers


# Declared-licence categories for display only: they summarise what the declared
# licence text says, never what this app has verified (see review_status).
# Order is restrictiveness; a record takes its most restrictive part.
CATEGORIES = ("commercial", "conditions", "unknown", "noncommercial")
_COMMERCIAL_LICENSES = frozenset({
    "mit", "apache-2.0", "bsd-2-clause", "bsd-3-clause", "isc", "cc0-1.0",
    "cc-by-3.0", "cc-by-4.0", "unlicense",
})
_CONDITIONS_LICENSES = frozenset({
    "openrail", "openrail++", "openrail-m", "creativeml-openrail-m",
    "bigscience-openrail-m", "cc-by-sa-3.0", "cc-by-sa-4.0",
    "gpl-3.0", "agpl-3.0", "lgpl-3.0",
})
# Deliberately unknown: no assertion, or bespoke terms nobody has categorised.
_UNKNOWN_LICENSES = frozenset({"noassertion", "other", "unknown", "higgs audio 2"})
_LLAMA = re.compile(r"(meta[ -])?llama[ -]?\d+(\.\d+)?\Z")
_NONCOMMERCIAL = re.compile(r"(^|-)nc(-|\b)|non-?commercial")
_LICENSE_SPLIT = re.compile(r"\s+(?:and|or|with)\s+|[;,+]")


def license_token_category(token: str) -> str | None:
    """Category of one SPDX-like licence id; None means nobody has mapped it."""
    token = re.sub(r"\(.*?\)", "", token).strip().casefold()
    if _NONCOMMERCIAL.search(token):
        return "noncommercial"
    if token in _COMMERCIAL_LICENSES:
        return "commercial"
    if token in _CONDITIONS_LICENSES or _LLAMA.fullmatch(token):
        return "conditions"
    if token in _UNKNOWN_LICENSES:
        return "unknown"
    return None


def license_tokens(value: object) -> list[str]:
    if not isinstance(value, str):
        return []
    return [t for t in (p.strip() for p in _LICENSE_SPLIT.split(value.casefold())) if t]


def _status_category(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    if value == "restricted" or value.startswith("noncommercial"):
        return "noncommercial"
    if value == "separate_permission_required":
        return "conditions"
    return None


def license_category(record: dict, inherited: str | None = None) -> str:
    """Most restrictive declared category; unmapped or missing terms are unknown."""
    found = [] if inherited is None else [inherited]
    tokens = license_tokens(record.get("license"))
    found += [license_token_category(t) or "unknown" for t in tokens]
    observations = record.get("observations") if isinstance(record.get("observations"), dict) else {}
    found += [license_token_category(t) or "unknown" for t in license_tokens(observations.get("license"))]
    for value in (record.get("commercial_inference"), observations.get("commercial_inference")):
        status = _status_category(value)
        if status:
            found.append(status)
    if not found:
        return "unknown"
    return max(found, key=CATEGORIES.index)


def _variants(row: dict, parent: str) -> list[dict]:
    variants = copy.deepcopy(row.get("variants", []))
    for variant in variants:
        variant["license_category"] = license_category(variant, inherited=parent)
    return variants


def disclosure(repo_id: str, registry: dict | None = None) -> dict:
    registry = registry if registry is not None else load_registry()
    row = next((r for r in registry["models"] if r["id"] == repo_id), None)
    if row is None:
        return {"readiness": "not_verified", "blockers": ["record_missing"],
                "commercial_inference": "unknown", "commercial_outputs": "unknown",
                "license_category": "unknown", "enforcement": "disclosure_only"}
    own = license_category(row)
    variants = _variants(row, own)
    # A repo that ships a restricted variant is as restricted as that variant.
    category = max([own, *(v["license_category"] for v in variants)], key=CATEGORIES.index)
    return {
        "registry_version": registry["registry_version"],
        "registry_digest": digest(registry),
        "license": row["license"], "credit": row["credit"],
        "notes": row.get("notes", ""),
        "source_url": row["source_url"], "evidence_url": row["evidence_url"],
        "evidence_revision": row.get("revision"),
        "runtime_revision": row.get("runtime_revision"),
        "review_status": row["review_status"],
        "commercial_inference": row.get("commercial_inference", "unknown"),
        "commercial_outputs": row.get("commercial_outputs", "unknown"),
        "component_closure": row.get("component_closure", "incomplete"),
        "readiness": "not_verified" if record_blockers(row) else "reviewable",
        "blockers": record_blockers(row), "enforcement": "disclosure_only",
        "license_category": category,
        "variants": variants,
        "evidence_checked_at": registry.get("checked_at"),
        "evidence_sha256": None,
        "redistribution": "unknown",
        "voice_rights": "unknown",
    }


def safe_disclosure(repo_id: str) -> dict:
    """A malformed legal registry must not break the existing catalogue."""
    try:
        return disclosure(repo_id)
    except (ModelTermsError, KeyError, TypeError, ValueError):
        return {"readiness": "not_verified", "blockers": ["registry_invalid"],
                "commercial_inference": "unknown", "commercial_outputs": "unknown",
                "license_category": "unknown", "enforcement": "disclosure_only"}


def _component(row: dict) -> tuple[dict, list[dict]]:
    artifacts = copy.deepcopy(row.get("artifacts", []))
    documents = copy.deepcopy(row.get("documents", []))
    if not isinstance(artifacts, list) or not isinstance(documents, list):
        raise ModelTermsError("registry_invalid")
    doc_ids = set()
    for doc in documents:
        if (not isinstance(doc, dict) or set(doc) != {
                "id", "title", "source_url", "sha256", "text", "redistribution", "agreement_required"}
                or not all(isinstance(doc.get(k), str) and doc[k] for k in
                           ("id", "title", "source_url", "sha256", "text"))
                or doc["id"] in doc_ids or not _https(doc["source_url"])
                or doc["redistribution"] != "permitted" or doc["agreement_required"] is not False
                or hashlib.sha256(doc["text"].encode("utf-8")).hexdigest() != doc["sha256"]):
            raise ModelTermsError("terms_unavailable", ["terms_unverified"])
        doc_ids.add(doc["id"])
    paths = set()
    for artifact in artifacts:
        if (not isinstance(artifact, dict) or set(artifact) != {
                "path", "sha256", "size_bytes", "document_ids"}
                or not safe_file(artifact.get("path")) or artifact["path"] in paths
                or not isinstance(artifact.get("sha256"), str)
                or not _DIGEST.fullmatch(artifact["sha256"])
                or type(artifact.get("size_bytes")) is not int or artifact["size_bytes"] < 1
                or not isinstance(artifact.get("document_ids"), list)
                or not artifact["document_ids"]
                or not all(isinstance(d, str) and d in doc_ids for d in artifact["document_ids"])):
            raise ModelTermsError("registry_invalid", ["artifact_manifest_unverified"])
        canonical = unicodedata.normalize("NFC", artifact["path"]).casefold()
        if canonical in paths:
            raise ModelTermsError("registry_invalid", ["artifact_path_collision"])
        paths.add(canonical)
    return ({"id": row["id"], "revision": row.get("runtime_revision"),
             "license": row["license"], "credit": row["credit"],
             "evidence_url": row["evidence_url"],
             "component_closure": row.get("component_closure", "incomplete"),
             "artifacts": sorted(artifacts, key=lambda a: a["path"])}, documents)


def build_plan(registry: dict, catalog: list[dict], repo_id: str, target: str,
               source: str = "https://huggingface.co") -> dict:
    if target != "local":
        raise ModelTermsError("review_required", ["remote_review_unsupported"])
    if source not in {"https://huggingface.co", "https://hf-mirror.com"}:
        raise ModelTermsError("review_required", ["source_unverified"])
    specs = {m["repo_id"]: m for m in catalog}
    if len(specs) != len(catalog):
        raise ModelTermsError("registry_invalid", ["duplicate_catalogue_identity"])
    if repo_id not in specs:
        raise ModelTermsError("review_required", ["record_missing"])
    rows = {row["id"]: row for row in registry["models"]}
    components, documents, blockers, visiting, visited = [], {}, [], set(), set()
    seen_specs = {}

    def visit(rid: str, spec: dict) -> None:
        if rid in visiting:
            raise ModelTermsError("registry_invalid", ["dependency_cycle"])
        if rid in seen_specs and seen_specs[rid] != spec:
            raise ModelTermsError("registry_invalid", ["conflicting_dependency_selection"])
        seen_specs[rid] = copy.deepcopy(spec)
        if rid in visited:
            return
        row = rows.get(rid)
        if row is None:
            raise ModelTermsError("terms_unavailable", ["record_missing"])
        visiting.add(rid)
        component, docs = _component(row)
        paths = {a["path"] for a in component["artifacts"]}
        patterns = spec.get("allow_patterns") or []
        if patterns and any(not any(fnmatch.fnmatchcase(p, pattern) for pattern in patterns)
                            for p in paths):
            raise ModelTermsError("registry_invalid", ["selection_manifest_mismatch"])
        required = set(spec.get("required_files") or []) | set(spec.get("config_required_files") or [])
        if not required.issubset(paths):
            blockers.append("artifact_manifest_unverified")
        components.append(component)
        blockers.extend(record_blockers(row))
        for doc in docs:
            if doc["id"] in documents and documents[doc["id"]] != doc:
                raise ModelTermsError("registry_invalid", ["duplicate_document_id"])
            documents[doc["id"]] = doc
        # YAML owns offered selections; legal evidence only adds dependencies.
        for dependency in spec.get("dependencies", []):
            visit(dependency["repo_id"], dependency)
        for dependency_id in row.get("required_components", []):
            visit(dependency_id, specs.get(dependency_id, {}))
        visiting.remove(rid)
        visited.add(rid)

    visit(repo_id, specs[repo_id])
    # No reviewed variants are selectable yet. An unsplit family cannot claim a
    # complete graph merely because a parent repository has a permissive label.
    if any(rows[c["id"]].get("variants") for c in components):
        blockers.append("variant_provenance_unverified")
    blockers = sorted(set(blockers))
    subject = {"format_version": 1, "repo_id": repo_id, "target": target, "source": source,
               "components": sorted(components, key=lambda c: c["id"]),
               "documents": [documents[k] for k in sorted(documents)],
               "acknowledgement": PROMPT}
    plan = {**subject, "registry_version": registry["registry_version"],
            "registry_digest": digest(registry), "catalog_digest": digest(specs[repo_id]),
            "readiness": "blocked" if blockers else "ready", "blockers": blockers,
            "enforcement": "reviewed_install_only", "plan_digest": digest(subject)}
    return copy.deepcopy(plan)


def _atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".model-notices-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, sort_keys=True, ensure_ascii=True, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            # Preserve the original write failure if temporary cleanup also fails.
            pass
        raise


class ReviewedInstalls:
    """Bounded issued plans, transactional local receipts and exact-file transfer.

    No receipts are migrated from legacy booleans. Their missing version/evidence
    cannot constitute acceptance. The local store is not tamper-proof legal proof.
    """

    def __init__(self, store: Path, registry_loader: Callable[[], dict],
                 catalog_loader: Callable[[], list[dict]], source_loader: Callable[[], str],
                 app_version: str = "unknown"):
        self.store = store
        self.registry_loader = registry_loader
        self.catalog_loader = catalog_loader
        self.source_loader = source_loader
        self.app_version = app_version
        self._plans: dict[str, tuple[float, dict]] = {}
        self._lock = threading.RLock()
        self._running: set[str] = set()

    def _record_path(self, group: str, plan_digest: str) -> Path:
        """Build a bounded record path without interpolating request text.

        Numeric reconstruction retains existing digest filenames while making
        the restricted filename alphabet explicit at the filesystem boundary.
        Containment also rejects receipt directories/files symlinked outside the
        configured local store, including export and removal paths.
        """
        if (group not in {"receipts", "evidence", "installed"}
                or not isinstance(plan_digest, str) or len(plan_digest) != 64
                or not _DIGEST.fullmatch(plan_digest)):
            raise ModelTermsError("receipt_invalid")
        filename = f"{int(plan_digest, 16):064x}.json"
        try:
            return resolve_within(self.store, f"{group}/{filename}")
        except UnsafePath as exc:
            raise ModelTermsError("receipt_invalid") from exc

    def prepare(self, repo_id: str, target: str = "local") -> dict:
        plan = build_plan(self.registry_loader(), self.catalog_loader(), repo_id,
                          target, self.source_loader())
        with self._lock:
            now = time.monotonic()
            self._plans = {k: v for k, v in self._plans.items() if now - v[0] < 1800}
            if len(self._plans) >= 128:
                raise ModelTermsError("review_capacity_reached")
            plan["plan_id"] = uuid.uuid4().hex
            self._plans[plan["plan_id"]] = (now, copy.deepcopy(plan))
        return plan

    def _current(self, plan_id: str, plan_digest: str, target: str) -> dict:
        entry = self._plans.get(plan_id)
        if entry is None or time.monotonic() - entry[0] >= 1800:
            raise ModelTermsError("plan_expired")
        original = entry[1]
        if original["target"] != target or original["plan_digest"] != plan_digest:
            raise ModelTermsError("plan_changed")
        current = build_plan(self.registry_loader(), self.catalog_loader(),
                             original["repo_id"], target, self.source_loader())
        if any(original[k] != current[k] for k in (
                "plan_digest", "registry_digest", "catalog_digest", "readiness", "blockers")):
            raise ModelTermsError("plan_changed")
        if current["readiness"] != "ready":
            raise ModelTermsError("terms_unavailable", current["blockers"])
        return original

    def commit(self, plan_id: str, plan_digest: str, document_ids: list[str],
               acknowledged: bool, target: str = "local") -> dict:
        with self._lock:
            plan = self._current(plan_id, plan_digest, target)
            expected = sorted(doc["id"] for doc in plan["documents"])
            if acknowledged is not True or sorted(document_ids) != expected:
                raise ModelTermsError("terms_required")
            path = self._record_path("receipts", plan_digest)
            if path.exists():
                return self._receipt(plan_digest)["receipt"]
            receipt = {"schema_version": 1, "receipt_id": uuid.uuid4().hex,
                       "recorded_at": datetime.now(timezone.utc).isoformat(),
                       "app_version": self.app_version, "action": "acknowledge_notices",
                       "repo_id": plan["repo_id"], "target": "local",
                       "registry_digest": plan["registry_digest"],
                       "plan_digest": plan_digest, "terms_digest": digest(plan["documents"]),
                       "prompt_digest": digest(PROMPT)}
            _atomic_json(path, {"schema_version": 1, "receipt": receipt, "plan": plan})
            return receipt

    def _receipt(self, plan_digest: str) -> dict:
        value = read_json(self._record_path("receipts", plan_digest))
        receipt, plan = value.get("receipt", {}), value.get("plan", {})
        keys = {"schema_version", "receipt_id", "recorded_at", "app_version", "action",
                "repo_id", "target", "registry_digest", "plan_digest", "terms_digest", "prompt_digest"}
        subject_keys = ("format_version", "repo_id", "target", "source", "components",
                        "documents", "acknowledgement")
        if (value.get("schema_version") != 1 or set(receipt) != keys
                or receipt.get("schema_version") != 1
                or receipt.get("action") != "acknowledge_notices"
                or receipt.get("plan_digest") != plan_digest
                or plan.get("plan_digest") != plan_digest
                or digest({k: plan.get(k) for k in subject_keys}) != plan_digest
                or receipt.get("terms_digest") != digest(plan.get("documents"))
                or receipt.get("prompt_digest") != digest(plan.get("acknowledgement"))
                or receipt.get("repo_id") != plan.get("repo_id")
                or receipt.get("registry_digest") != plan.get("registry_digest")
                or receipt.get("target") != "local" or plan.get("target") != "local"):
            raise ModelTermsError("receipt_invalid")
        return value

    def export(self, plan_digest: str) -> dict:
        with self._lock:
            return self._receipt(plan_digest)

    def remove(self, plan_digest: str) -> None:
        with self._lock:
            self._receipt(plan_digest)
            if plan_digest in self._running:
                raise ModelTermsError("install_in_progress")
            archived = self._receipt(plan_digest)["plan"]
            _atomic_json(self._record_path("evidence", plan_digest),
                         {"schema_version": 1, "plan": archived})
            (self._record_path("receipts", plan_digest)).unlink()
            # Keep archived/installed evidence and all model files. This removes only the
            # local acknowledgement, never an upstream agreement or obligations.

    def download(self, plan_id: str, plan_digest: str, fetch_file: Callable) -> dict:
        """Fetch only declared files; no SDK, repository-wide fetch or fallback."""
        with self._lock:
            plan = self._current(plan_id, plan_digest, "local")
            saved = self._receipt(plan_digest)
            receipt = saved["receipt"]
            if plan_digest in self._running:
                raise ModelTermsError("install_in_progress")
            self._running.add(plan_digest)
        try:
            verified_files = []
            for component in plan["components"]:
                for artifact in component["artifacts"]:
                    # Revalidate before each byte-producing call and stop on all
                    # errors, including mirror changes and acknowledgement removal.
                    with self._lock:
                        self._current(plan_id, plan_digest, "local")
                        self._receipt(plan_digest)
                    path = Path(fetch_file(component["id"], component["revision"],
                                           artifact["path"], plan["source"], False))
                    self._verify(path, artifact)
                    verified_files.append((path, artifact))
            for path, artifact in verified_files:
                self._verify(path, artifact)
            with self._lock:
                self._current(plan_id, plan_digest, "local")
                self._receipt(plan_digest)
            installed = {"schema_version": 1, "plan_digest": plan_digest,
                         "receipt_id": receipt["receipt_id"], "plan": saved["plan"]}
            _atomic_json(self._record_path("installed", plan_digest), installed)
            return {"status": "verified", "plan_digest": plan_digest,
                    "receipt_id": receipt["receipt_id"], "enforcement": "reviewed_install_only",
                    "activation": "not_integrated"}
        finally:
            with self._lock:
                self._running.discard(plan_digest)

    @staticmethod
    def _verify(path: Path, artifact: dict) -> None:
        try:
            valid = path.is_file() and path.stat().st_size == artifact["size_bytes"]
            valid = valid and file_digest(path) == artifact["sha256"]
        except OSError:
            valid = False
        if not valid:
            raise ModelTermsError("integrity_failed")

    def verify_installed(self, plan_digest: str, locate_file: Callable) -> dict:
        """Explicit offline verification, NOT yet wired into legacy SDK loaders.

        Uses historical evidence rather than reinterpreting new upstream terms.
        Caller must pass a local-only locator; returned files must be used as-is.
        """
        with self._lock:
            value = self._receipt(plan_digest)
            installed = read_json(self._record_path("installed", plan_digest))
            if (installed.get("schema_version") != 1
                    or installed.get("plan_digest") != plan_digest
                    or installed.get("receipt_id") != value["receipt"]["receipt_id"]
                    or installed.get("plan") != value["plan"]):
                raise ModelTermsError("installed_evidence_invalid")
        for component in value["plan"]["components"]:
            for artifact in component["artifacts"]:
                path = Path(locate_file(component["id"], component["revision"],
                                        artifact["path"], value["plan"]["source"], True))
                self._verify(path, artifact)
        return {"status": "verified", "plan_digest": plan_digest,
                "enforcement": "reviewed_install_only"}
