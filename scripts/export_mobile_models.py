#!/usr/bin/env python3
"""Export the phone model catalogue, offline by default.

--pin explicitly refreshes HF main revisions and file metadata in the source
manifest (OmniVoice always uses the desktop pin). It does not change legal
reviews or the generated catalogue. Review changed evidence, regenerate the
licensing projection, then run this script without --pin. No model bytes are
saved: LFS SHA-256 oids are used directly; ordinary Git files are streamed and
hashed, with their Git blob identity verified against the API response.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "mobile/contract/models.source.json"
OUTPUT = ROOT / "mobile/contract/models.json"
LICENSES = ROOT / "docs/licensing/model-license-data.json"
sys.path.insert(0, str(ROOT / "backend"))
from services.model_acceptance import fingerprint, terms  # noqa: E402
from services.model_licenses import safe_file  # noqa: E402

CAPABILITIES = {
    "tts": "sherpa-onnx", "asr": "sherpa-onnx", "asr-stream": "sherpa-onnx",
    "clone": "omnivoice.cpp", "translate": "llama.cpp",
}
RUNTIMES = {key: {runtime} for key,runtime in CAPABILITIES.items()}
RUNTIMES["clone"].add("sherpa-onnx")
CATEGORIES = {
    "commercial": "commercial-ok", "noncommercial": "non-commercial",
    "conditions": "conditional", "unknown": "unidentified",
}
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
REVISION = re.compile(r"[0-9a-f]{40}\Z")
REPO = re.compile(r"[A-Za-z0-9][\w.-]*/[\w.-]+\Z")
ROLES = {
    "model", "encoder", "decoder", "joiner", "tokens", "voices", "tokenizer",
    "espeak-data", "lexicon", "rule-fst", "dictionary", "vocoder", "duration-predictor",
    "text-encoder", "vector-estimator", "tts-config", "unicode-indexer", "lm-flow", "lm-main",
    "text-conditioner", "vocab", "token-scores",
}


def valid_asset_url(url: str) -> bool:
    from urllib.parse import urlparse
    if not isinstance(url, str):
        return False
    try:
        u = urlparse(url)
        if u.scheme != "https" or u.username or u.password or u.port is not None or u.query or u.fragment:
            return False
    except ValueError:
        return False
    parts = u.path.strip("/").split("/")
    if not all(part not in {".", ".."} and re.fullmatch(r"[A-Za-z0-9_.-]+", part) for part in parts):
        return False
    if u.hostname == "huggingface.co":
        return (len(parts) >= 5 and REPO.fullmatch("/".join(parts[:2])) is not None
                and parts[2] == "resolve" and REVISION.fullmatch(parts[3]) is not None)
    return (u.hostname == "github.com" and len(parts) == 6
            and parts[:4] == ["k2-fsa", "sherpa-onnx", "releases", "download"]
            and parts[-1].endswith(".onnx"))

def read_json(path: Path) -> dict[str, Any]:
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"{path}: duplicate JSON key {key}")
            result[key] = value
        return result

    data = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique)
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected a JSON object")
    return data


def validate_source(source: dict[str, Any], *, pinned: bool = True) -> None:
    if source.get("schema_version") != 1 or not isinstance(source.get("models"), list) or not source["models"]:
        raise ValueError("Expected a nonempty schema_version 1 model catalogue")
    ids: set[str] = set()
    for model in source["models"]:
        mid = model.get("id")
        if not isinstance(mid, str) or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", mid) or mid in ids:
            raise ValueError(f"Invalid or duplicate model id: {mid}")
        ids.add(mid)
        # Display name: the model's product name only (never translated); what it
        # is for comes from the app's localized capability label.
        if not isinstance(model.get("name"), str) or not model["name"].strip() or len(model["name"]) > 24:
            raise ValueError(f"{mid}: missing or overlong display name")
        if model.get("capability") not in CAPABILITIES or model.get("runtime") not in RUNTIMES[model["capability"]]:
            raise ValueError(f"{mid}: invalid capability/runtime pair")
        if not isinstance(model.get("repo"), str) or not REPO.fullmatch(model["repo"]):
            raise ValueError(f"{mid}: invalid HF repo")
        if pinned and (not isinstance(model.get("revision"), str) or not REVISION.fullmatch(model["revision"])):
            raise ValueError(f"{mid}: revision must be an immutable 40-hex commit")
        if (type(model.get("min_ram_gb")) not in (int, float) or model["min_ram_gb"] <= 0
                or model["min_ram_gb"] > 128):
            raise ValueError(f"{mid}: invalid minimum RAM")
        if (model["capability"] == "translate" or model["runtime"] == "omnivoice.cpp") and model["min_ram_gb"] < 6:
            raise ValueError(f"{mid}: cloning/translation require at least 6 GB RAM")
        languages = model.get("languages")
        if (not isinstance(languages, list) or not languages
                or any(not isinstance(lang, str) or not re.fullmatch(r"[a-z]{2,3}(?:-[A-Za-z0-9]+)*", lang) for lang in languages)
                or len(languages) != len(set(languages))):
            raise ValueError(f"{mid}: invalid languages")
        if not isinstance(model.get("config"), dict):
            raise ValueError(f"{mid}: missing runtime config")
        if model["capability"] == "clone" and model["runtime"] == "sherpa-onnx" and model["config"].get("model_type") not in {"pocket","zipvoice"}:
            raise ValueError(f"{mid}: unsupported reference speech family")
        files = model.get("files")
        if not isinstance(files, list) or not files:
            raise ValueError(f"{mid}: empty file manifest")
        paths: set[str] = set()
        roles: set[str] = set()
        for file in files:
            path = file.get("path")
            if not safe_file(path) or path.casefold() in paths:
                raise ValueError(f"{mid}: unsafe or duplicate file path: {path}")
            paths.add(path.casefold())
            if file.get("role") not in ROLES:
                raise ValueError(f"{mid}: unknown file role: {file.get('role')}")
            roles.add(file["role"])
            if file.get("url") is not None and not valid_asset_url(file["url"]):
                raise ValueError(f"{mid}: invalid fixed asset URL")
            if file.get("bundled") is not None:
                asset = file["bundled"]
                if not safe_file(asset) or file.get("url") is not None:
                    raise ValueError(f"{mid}: invalid bundled asset")
                data = (ROOT / "mobile/contract" / asset).read_bytes()
                if pinned and (len(data) != file["size"] or hashlib.sha256(data).hexdigest() != file["sha256"]):
                    raise ValueError(f"{mid}: bundled asset pin mismatch")

            if pinned and (type(file.get("size")) is not int or file["size"] <= 0
                           or not isinstance(file.get("sha256"), str) or not SHA256.fullmatch(file["sha256"])):
                raise ValueError(f"{mid}: {path} needs a positive size and 64-hex SHA-256")
        family = model["config"].get("model_type", "")
        speech_roles = {
            "vits": {"model", "tokens", "espeak-data"},
            "supertonic": {"duration-predictor", "text-encoder", "vector-estimator", "vocoder", "tts-config", "unicode-indexer", "voices"},
            "pocket": {"lm-flow", "lm-main", "encoder", "decoder", "text-conditioner", "vocab", "token-scores"},
            "zipvoice": {"encoder", "decoder", "vocoder", "tokens", "espeak-data", "lexicon"},
        }
        required = {
            "tts": speech_roles.get(family, {"model", "tokens", "voices", "espeak-data"}),
            "asr": {"encoder", "decoder", "tokens"},
            "asr-stream": {"encoder", "decoder", "joiner", "tokens"},
            "clone": speech_roles.get(family, {"model", "tokenizer"}), "translate": {"model"},
        }[model["capability"]]
        if not required <= roles:
            raise ValueError(f"{mid}: missing runtime file roles: {required - roles}")


def build_catalogue(source: dict[str, Any], licenses: dict[str, Any]) -> dict[str, Any]:
    """Join only bundled data; importing or calling this function never networks."""
    validate_source(source)
    if licenses.get("schema_version") != 1:
        raise ValueError("Unsupported licensing projection schema")
    rows = {row["id"]: row for row in licenses["models"]}
    if len(rows) != len(licenses["models"]):
        raise ValueError("Duplicate licensing registry identity")
    models = []
    for source_model in source["models"]:
        model = copy.deepcopy(source_model)
        repo = model["repo"]
        if repo not in rows:
            raise ValueError(f"{repo}: missing registry entry; follow docs/licensing/model-review-workflow.md")
        info = rows[repo]
        category = CATEGORIES.get(info.get("license_category"), "unidentified")
        if not all(isinstance(info.get(key), str) and info[key].strip() for key in ("license", "credit")):
            raise ValueError(f"{repo}: missing licence or attribution")
        # Desktop currently presents credit as the rights-holder attribution.
        # Retain the exact disclosure and desktop canonicalization, including
        # restrictive variants, instead of hashing just a short licence label.
        model["licence"] = {
            "id": info["license"], "category": category,
            "requires_acceptance": category != "commercial-ok",
            "credit": info["credit"], "rights_holder": info["credit"],
            "terms_fingerprint": fingerprint(info, repo).removeprefix("v2:"),
            "terms": terms(info, repo),
        }
        model["total_bytes"] = sum(file["size"] for file in model["files"])
        # Source-only review notes/pinning controls are not runtime settings.
        for key in ("selection_rationale", "revision_policy", "evidence"):
            model.pop(key, None)
        models.append(model)
    return {"schema_version": 1, "models": models}


def render(data: dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=True, indent=2, allow_nan=False) + "\n"


def _open_hf(path: str):
    """Public HF only, with the same allowed mirror as the desktop."""
    for host in ("https://huggingface.co", "https://hf-mirror.com"):
        try:
            return urlopen(host + path, timeout=60)
        except (HTTPError, URLError, TimeoutError):
            if host == "https://hf-mirror.com":
                raise
    raise AssertionError("unreachable")


def pin_source(source: dict[str, Any]) -> dict[str, Any]:
    """Refresh the existing explicit selection; never add upstream extra files."""
    validate_source(source, pinned=False)
    result = copy.deepcopy(source)
    desktop = read_json(ROOT / "backend/engines/omnivoice_gguf/quant_map.json")["_meta"]
    for model in result["models"]:
        repo = model["repo"]
        revision = "main"
        if model.get("revision_policy") == "desktop":
            if repo != desktop["source_model"]:
                raise ValueError(f"{repo}: desktop revision policy only supports OmniVoice")
            revision = desktop["source_commit_sha"]
        elif model.get("revision_policy", "main") != "main":
            raise ValueError(f"{repo}: unknown revision policy")
        with _open_hf(f"/api/models/{repo}/revision/{revision}?blobs=true") as response:
            info = json.load(response)
        revision = info["sha"]
        if not isinstance(revision, str) or not REVISION.fullmatch(revision):
            raise ValueError(f"{repo}: HF did not resolve an immutable revision")
        siblings = {file["rfilename"]: file for file in info["siblings"]}

        def pin_file(file: dict[str, Any]) -> dict[str, Any]:
            path = file["path"]
            if file.get("bundled"):
                data=(ROOT / "mobile/contract" / file["bundled"]).read_bytes()
                return {**file,"size":len(data),"sha256":hashlib.sha256(data).hexdigest()}
            if file.get("url"):
                # Fixed publisher release asset: explicit URL plus SHA-256, never an SDK lookup.
                if not valid_asset_url(file["url"]):
                    raise ValueError("Invalid model asset URL")
                return file
            if path not in siblings:
                raise ValueError(f"{repo}@{revision}: selected file missing: {path}")
            metadata = siblings[path]
            size = metadata.get("size")
            lfs = metadata.get("lfs")
            if lfs:
                sha = lfs.get("sha256")
                if not isinstance(sha, str) or not SHA256.fullmatch(sha) or size != lfs.get("size"):
                    raise ValueError(f"{repo}/{path}: invalid LFS SHA-256/size")
            else:
                if type(size) is not int or size <= 0:
                    raise ValueError(f"{repo}/{path}: missing Git file size")
                sha256 = hashlib.sha256()
                git_blob = hashlib.sha1(f"blob {size}\0".encode(), usedforsecurity=False)
                count = 0
                with _open_hf(f"/{repo}/resolve/{revision}/{quote(path, safe='/')}") as response:
                    for chunk in iter(lambda: response.read(1024 * 1024), b""):
                        count += len(chunk)
                        sha256.update(chunk)
                        git_blob.update(chunk)
                if count != size or git_blob.hexdigest() != metadata.get("blobId"):
                    raise ValueError(f"{repo}/{path}: upstream Git blob/size mismatch")
                sha = sha256.hexdigest()
            return {"path": path, "size": size, "sha256": sha, "role": file["role"]}

        # map preserves source order; one failed request leaves the source intact.
        with ThreadPoolExecutor(max_workers=8) as pool:
            model["files"] = list(pool.map(pin_file, model["files"]))
        model["revision"] = revision
        print(f"Pinned {model['id']}: {revision} ({len(model['files'])} files)")
    validate_source(result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--pin", action="store_true", help="Network: refresh source pins, not legal reviews")
    modes.add_argument("--check", action="store_true", help="Offline: fail if generated catalogue is stale")
    args = parser.parse_args()
    try:
        source = read_json(SOURCE)
        if args.pin:
            SOURCE.write_text(render(pin_source(source)), encoding="utf-8")
            return 0
        content = render(build_catalogue(source, read_json(LICENSES)))
        if args.check:
            if not OUTPUT.exists() or OUTPUT.read_text(encoding="utf-8") != content:
                print("Mobile models stale; run `uv run python scripts/export_mobile_models.py`", file=sys.stderr)
                return 1
        else:
            OUTPUT.write_text(content, encoding="utf-8")
        return 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"Mobile models: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
