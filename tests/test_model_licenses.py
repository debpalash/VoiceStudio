"""Offline CI coverage of configured model licence records and review states."""
import copy
import importlib.util
import json
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


def checker():
    spec = importlib.util.spec_from_file_location("check_model_licenses", ROOT / "scripts/check_model_licenses.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def registry():
    return json.loads((ROOT / "backend/config/model_licenses.json").read_text(encoding="utf-8"))


def catalog():
    return yaml.safe_load((ROOT / "backend/config/models.yaml").read_text(encoding="utf-8"))


def test_bundled_model_licence_records_are_complete():
    module = checker()
    assert module.validate(registry(), catalog(), module.source_model_ids(ROOT)) == []


def test_hardcoded_defaults_and_download_calls_need_records(tmp_path):
    source = tmp_path / "backend" / "engines" / "new_engine.py"
    source.parent.mkdir(parents=True)
    source.write_text('''
DEFAULT_REPO = os.environ.get("CUSTOM_REPO", "example/default")
PIPELINE_MODEL_ID = "example/pipeline"
DEFAULT_MODEL = "example/another-default"
checkpoint = "example/checkpoint"
weights = os.environ.get("ENGINE_WEIGHTS", "example/env-default")
weights2 = os.getenv("ENGINE_OTHER", "example/getenv-default")
CURATED_REVISIONS = {"example/pinned": "0123456789"}
snapshot_download(repo_id="example/download")
AutoModel(model="example/keyword")
AutoModel.from_pretrained("example/pretrained")
_FW_ALIAS_REPOS = {"tiny": "example/alias"}
ASR_MODEL = "moonshine/base"
MANAGED_MODEL_SUBDIR = "pretrained_models/Fun-CosyVoice3-0.5B"
GH_REPO = "0xShug0/audio.cpp"
''', encoding="utf-8")
    model_source = tmp_path / "omnivoice/models/model.py"
    model_source.parent.mkdir(parents=True)
    model_source.write_text('_AUDIO_TOKENIZER_FALLBACK_REPO = "example/tokenizer"', encoding="utf-8")
    module = checker()
    ids = module.source_model_ids(tmp_path)
    assert ids == {"example/default", "example/pipeline", "example/download",
                   "example/pretrained", "example/alias", "example/another-default",
                   "example/keyword", "UsefulSensors/moonshine-base", "example/checkpoint",
                   "example/env-default", "example/getenv-default", "example/pinned",
                   "example/tokenizer"}
    errors = module.validate(registry(), catalog(), ids)
    assert all(f"Missing model record: {rid}" in errors
               for rid in ids if rid.startswith("example/"))


def test_new_catalog_model_requires_its_own_record():
    models = catalog()
    models["models"].append({"repo_id": "example/new-model"})
    assert "Missing model record: example/new-model" in checker().validate(registry(), models)


def test_new_transitive_dependency_requires_its_own_record():
    models = {"models": [{"repo_id": "k2-fsa/OmniVoice", "dependencies": [
        {"repo_id": "example/dependency", "dependencies": [{"repo_id": "example/nested"}]}
    ]}]}
    errors = checker().validate(registry(), models)
    assert "Missing model record: example/dependency" in errors
    assert "Missing model record: example/nested" in errors


@pytest.mark.parametrize("field", ["license", "credit", "source_url", "evidence_url", "review_status", "commercial_use"])
def test_incomplete_model_record_is_rejected(field):
    data = registry()
    del data["models"][0][field]
    assert checker().validate(data, catalog())


def test_unreviewed_metadata_cannot_claim_commercial_clearance():
    data = registry()
    data["models"][0]["review_status"] = "unreviewed"
    data["models"][0]["commercial_use"] = True
    assert any("commercial_use" in error for error in checker().validate(data, catalog()))


def test_duplicate_records_are_rejected():
    data = registry()
    data["models"].append(copy.deepcopy(data["models"][0]))
    assert any("Duplicate" in error for error in checker().validate(data, catalog()))


@pytest.mark.parametrize("records", [None, {}, [{"id": []}]])
def test_malformed_record_groups_report_errors(records):
    data = registry()
    data["models"] = records
    assert checker().validate(data, catalog())


def test_dynamic_assets_cannot_inherit_blanket_clearance():
    data = registry()
    data["dynamic_assets"][0]["commercial_use"] = True
    assert any("commercial_use" in error for error in checker().validate(data, catalog()))
