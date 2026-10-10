"""Hermetic reviewed-install tests; synthetic terms/bytes NEVER enter production.

Run without optional ML/test dependencies:
    HF_HUB_OFFLINE=1 python tests/test_model_license_workflow.py
"""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from services import model_licenses as ml


def synthetic_registry() -> dict:
    """Invented positive fixture, constructed only inside the test process."""
    text = "Synthetic test notice. This is not a real upstream licence."
    doc = {"id": "fixture-terms", "title": "Synthetic notice", "text": text,
           "source_url": "https://example.invalid/test/LICENSE",
           "sha256": hashlib.sha256(text.encode()).hexdigest(),
           "redistribution": "permitted", "agreement_required": False}
    row = {"id": "fixture/model", "license": "LicenseRef-Synthetic-Test",
           "credit": "Test only", "source_url": "https://example.invalid/test",
           "evidence_url": "https://example.invalid/test/LICENSE",
           "revision": "a" * 40, "runtime_revision": "a" * 40,
           "component_closure": "complete", "required_components": [],
           "review_status": "unreviewed", "commercial_use": False,
           "commercial_inference": "unknown", "commercial_outputs": "unknown",
           "upstream_access": "not_required", "documents": [doc],
           "artifacts": [{"path": "weights.bin", "sha256": hashlib.sha256(b"test").hexdigest(),
                          "size_bytes": 4, "document_ids": [doc["id"]]}]}
    return {"schema_version": 2, "registry_version": "fixture-only", "models": [row]}


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.registry = synthetic_registry()
        self.catalog = [{"repo_id": "fixture/model"}]
        self.source = "https://huggingface.co"
        self.service = ml.ReviewedInstalls(self.root / "receipts-store", lambda: self.registry,
                                         lambda: self.catalog, lambda: self.source, "test-build")
        self.file = self.root / "artifact"
        self.file.write_bytes(b"test")
        self.calls = []

    def prepare(self):
        return self.service.prepare("fixture/model")

    def commit(self, plan):
        return self.service.commit(plan["plan_id"], plan["plan_digest"], ["fixture-terms"], True)

    def fetch(self, *args):
        self.calls.append(args)
        return str(self.file)

    def install(self, plan):
        return self.service.download(plan["plan_id"], plan["plan_digest"], self.fetch)

    def assert_code(self, code, fn, *args, **kwargs):
        with self.assertRaises(ml.ModelTermsError) as raised:
            fn(*args, **kwargs)
        self.assertEqual(raised.exception.code, code)

    def test_prepare_never_fetches(self):
        self.assertEqual(self.prepare()["readiness"], "ready")
        self.assertEqual(self.calls, [])
        self.assertFalse(self.service.store.exists())

    def test_no_bytes_without_receipt(self):
        self.assert_code("evidence_unavailable", self.install, self.prepare())
        self.assertEqual(self.calls, [])

    def test_exact_acknowledgement_set(self):
        p = self.prepare()
        for acknowledged, ids in [(False, ["fixture-terms"]), (True, []),
                                   (True, ["fixture-terms", "fixture-terms"]),
                                   (True, ["anything"]), (1, ["fixture-terms"])]:
            self.assert_code("terms_required", self.service.commit, p["plan_id"], p["plan_digest"],
                             ids, acknowledged)
        self.assertEqual(self.calls, [])

    def test_receipt_precedes_transfer_and_is_idempotent(self):
        p = self.prepare()
        receipt = self.commit(p)
        self.assertEqual(receipt, self.commit(p))
        def fetch(*args):
            self.assertEqual(self.service.export(p["plan_digest"])["receipt"], receipt)
            return self.fetch(*args)
        result = self.service.download(p["plan_id"], p["plan_digest"], fetch)
        self.assertEqual(result["status"], "verified")
        self.assertEqual(result["activation"], "not_integrated")
        self.assertEqual(self.calls, [("fixture/model", "a" * 40, "weights.bin", self.source, False)])

    def test_persistence_failure_prevents_transfer(self):
        p = self.prepare()
        with patch.object(ml, "_atomic_json", side_effect=OSError("disk unavailable")):
            with self.assertRaises(OSError):
                self.commit(p)
        self.assertEqual(self.calls, [])

    def test_registry_change_requires_new_plan(self):
        p = self.prepare()
        self.registry["models"][0]["runtime_revision"] = "b" * 40
        self.assert_code("plan_changed", self.commit, p)

    def test_source_change_requires_new_plan(self):
        p = self.prepare()
        self.source = "https://hf-mirror.com"
        self.assert_code("plan_changed", self.commit, p)

    def test_target_change_rejected(self):
        p = self.prepare()
        self.assert_code("plan_changed", self.service.commit, p["plan_id"], p["plan_digest"],
                         ["fixture-terms"], True, "worker-id")
        self.assert_code("review_required", self.service.prepare, "fixture/model", "worker-id")

    def test_manifest_changes_during_last_transfer_prevent_verification(self):
        p = self.prepare(); self.commit(p)
        def changed(*args):
            self.registry["models"][0]["notes"] = "changed during transfer"
            return self.fetch(*args)
        self.assert_code("plan_changed", self.service.download, p["plan_id"], p["plan_digest"], changed)
        self.assertFalse((self.service.store / "installed").exists())

    def test_bad_artifact_hash_stops_without_fallback(self):
        p = self.prepare(); self.commit(p); self.file.write_bytes(b"evil")
        self.assert_code("integrity_failed", self.install, p)
        self.assertEqual(len(self.calls), 1)
        self.assertFalse((self.service.store / "installed").exists())

    def test_fetch_denial_stops_without_fallback(self):
        p = self.prepare(); self.commit(p)
        def denied(*args):
            self.calls.append(args)
            raise ml.ModelTermsError("upstream_access_required")
        self.assert_code("upstream_access_required", self.service.download,
                         p["plan_id"], p["plan_digest"], denied)
        self.assertEqual(len(self.calls), 1)

    def test_incomplete_evidence_cannot_be_acknowledged(self):
        for key, value in [("component_closure", "incomplete"), ("artifacts", []),
                           ("documents", []), ("upstream_access", "unknown")]:
            with self.subTest(key=key):
                self.registry = synthetic_registry()
                self.registry["models"][0][key] = value
                if key == "documents":
                    self.registry["models"][0]["artifacts"] = []
                p = self.prepare()
                self.assertEqual(p["readiness"], "blocked")
                self.assert_code("terms_unavailable", self.commit, p)

    def test_term_tampering_rejected(self):
        self.registry["models"][0]["documents"][0]["text"] += "tampered"
        self.assert_code("terms_unavailable", self.prepare)

    def test_provider_assent_is_never_local_ack(self):
        self.registry["models"][0]["documents"][0]["agreement_required"] = True
        self.assert_code("terms_unavailable", self.prepare)

    def test_bad_paths_and_globs_rejected(self):
        for path in ("../x", "/root", "a\\b", "weights*", "a/../b", "C:/bad", "NUL.bin", "a./b"):
            self.registry["models"][0]["artifacts"][0]["path"] = path
            self.assert_code("registry_invalid", self.prepare)

    def test_casefold_and_unicode_collisions_rejected(self):
        for a, b in [("A.bin", "a.bin"), ("é.bin", "e\u0301.bin")]:
            artifacts = self.registry["models"][0]["artifacts"]
            artifacts[0]["path"] = a
            other = copy.deepcopy(artifacts[0]); other["path"] = b
            self.registry["models"][0]["artifacts"] = [artifacts[0], other]
            self.assert_code("registry_invalid", self.prepare)

    def test_catalogue_selection_constrains_manifest(self):
        self.catalog[0]["allow_patterns"] = ["other.bin"]
        self.assert_code("registry_invalid", self.prepare)

    def test_missing_required_file_blocks(self):
        self.catalog[0]["required_files"] = ["missing.bin"]
        self.assertIn("artifact_manifest_unverified", self.prepare()["blockers"])

    def test_config_only_required_files_block_when_missing(self):
        self.catalog[0].update(config_only=True, config_required_files=["config.yaml"])
        self.assertIn("artifact_manifest_unverified", self.prepare()["blockers"])

    def test_receipt_identity_corruption_rejected(self):
        p = self.prepare(); self.commit(p)
        path = self.service.store / "receipts" / f"{p['plan_digest']}.json"
        original = json.loads(path.read_text())
        for field in ("repo_id", "registry_digest"):
            bad = copy.deepcopy(original); bad["receipt"][field] = "different"
            path.write_text(json.dumps(bad))
            self.assert_code("receipt_invalid", self.install, p)
        self.assertEqual(self.calls, [])

    def test_cycles_and_missing_dependencies_fail_closed(self):
        self.registry["models"][0]["required_components"] = ["fixture/model"]
        self.assert_code("registry_invalid", self.prepare)
        self.registry["models"][0]["required_components"] = ["fixture/missing"]
        self.assert_code("terms_unavailable", self.prepare)

    def test_repeated_repo_cannot_drop_different_nested_selection(self):
        b = copy.deepcopy(self.registry["models"][0]); b["id"] = "fixture/b"
        self.registry["models"].append(b)
        self.catalog[0]["dependencies"] = [{"repo_id": "fixture/b"},
            {"repo_id": "fixture/b", "dependencies": [{"repo_id": "fixture/missing"}]}]
        self.assert_code("registry_invalid", self.prepare)

    def test_unknown_variants_do_not_inherit_parent_ready_state(self):
        self.registry["models"][0]["variants"] = [{"id": "voice-unresolved"}]
        self.assertIn("variant_provenance_unverified", self.prepare()["blockers"])

    def test_offline_verification_uses_historical_terms(self):
        p = self.prepare(); self.commit(p); self.install(p)
        self.registry["models"][0]["runtime_revision"] = "b" * 40
        self.service.verify_installed(p["plan_digest"], self.fetch)
        self.assertTrue(self.calls[-1][-1])
        self.assertEqual(self.calls[-1][1], "a" * 40)

    def test_presentation_only_update_can_reuse_historical_receipt(self):
        p = self.prepare(); old = self.commit(p)
        self.registry["registry_version"] = "fixture-only-presentation-correction"
        q = self.prepare()
        self.assertEqual(p["plan_digest"], q["plan_digest"])
        self.assertEqual(old, self.commit(q))
        self.install(q)
        self.service.verify_installed(q["plan_digest"], self.fetch)

    def test_removal_requires_review_again_preserves_files(self):
        p = self.prepare(); self.commit(p); self.install(p)
        self.service.remove(p["plan_digest"])
        self.assertTrue(self.file.exists())
        self.assertTrue((self.service.store / "evidence" / f"{p['plan_digest']}.json").exists())
        self.assertTrue((self.service.store / "installed" / f"{p['plan_digest']}.json").exists())
        self.assert_code("evidence_unavailable", self.service.verify_installed, p["plan_digest"], self.fetch)

    def test_receipt_corruption_fails_without_overwriting(self):
        p = self.prepare(); self.commit(p)
        path = self.service.store / "receipts" / f"{p['plan_digest']}.json"
        path.write_text('{"schema_version": 99}')
        self.assert_code("receipt_invalid", self.commit, p)
        self.assertEqual(path.read_text(), '{"schema_version": 99}')

    def test_receipt_paths_reject_untrusted_names(self):
        for value in ("../outside", "/tmp/file", "a" * 63, "a" * 65,
                      "A" * 64, "a" * 63 + "\n", "a" * 63 + "/", None, 12):
            for group in ("receipts", "evidence", "installed"):
                self.assert_code("receipt_invalid", self.service._record_path, group, value)
        self.assert_code("receipt_invalid", self.service._record_path, "../escape", "a" * 64)

    def test_record_paths_preserve_existing_digest_filenames(self):
        p = self.prepare()
        for group in ("receipts", "evidence", "installed"):
            self.assertEqual(self.service._record_path(group, p["plan_digest"]),
                             self.service.store / group / f"{p['plan_digest']}.json")

    def test_symlinked_receipt_directory_cannot_escape_store(self):
        p = self.prepare()
        self.service.store.mkdir()
        outside = self.root / "outside"; outside.mkdir()
        try:
            (self.service.store / "receipts").symlink_to(outside, target_is_directory=True)
        except (NotImplementedError, OSError):
            self.skipTest("Host does not permit symlink creation")
        self.assert_code("receipt_invalid", self.commit, p)
        self.assert_code("receipt_invalid", self.service.export, p["plan_digest"])
        self.assert_code("receipt_invalid", self.service.remove, p["plan_digest"])
        self.assertEqual(list(outside.iterdir()), [])
        self.assertEqual(self.calls, [])

    def test_configured_store_root_can_be_relocated_by_symlink(self):
        actual = self.root / "relocated-store"; actual.mkdir()
        try:
            self.service.store.symlink_to(actual, target_is_directory=True)
        except (NotImplementedError, OSError):
            self.skipTest("Host does not permit symlink creation")
        p = self.prepare(); receipt = self.commit(p)
        self.assertEqual(self.service.export(p["plan_digest"])["receipt"], receipt)
        self.assertTrue((actual / "receipts" / f"{p['plan_digest']}.json").is_file())

    def test_archive_symlink_escape_preserves_acknowledgement(self):
        p = self.prepare(); self.commit(p)
        outside = self.root / "outside-evidence"; outside.mkdir()
        try:
            (self.service.store / "evidence").symlink_to(outside, target_is_directory=True)
        except (NotImplementedError, OSError):
            self.skipTest("Host does not permit symlink creation")
        self.assert_code("receipt_invalid", self.service.remove, p["plan_digest"])
        self.assertEqual(list(outside.iterdir()), [])
        self.assertEqual(self.service.export(p["plan_digest"])["receipt"]["plan_digest"], p["plan_digest"])

    def test_symlinked_receipt_file_cannot_read_or_remove_outside(self):
        p = self.prepare(); self.commit(p)
        receipt = self.service.store / "receipts" / f"{p['plan_digest']}.json"
        outside = self.root / "outside.json"; outside.write_bytes(receipt.read_bytes())
        receipt.unlink()
        try:
            receipt.symlink_to(outside)
        except (NotImplementedError, OSError):
            self.skipTest("Host does not permit symlink creation")
        self.assert_code("receipt_invalid", self.service.export, p["plan_digest"])
        self.assert_code("receipt_invalid", self.service.remove, p["plan_digest"])
        self.assertTrue(outside.is_file())

    def test_minimal_receipt_no_actor_or_tracking_fields(self):
        receipt = self.commit(self.prepare())
        for key in ("name", "email", "ip", "actor", "device_id", "token", "audio", "prompt"):
            self.assertNotIn(key, receipt)
        self.assertEqual(receipt["action"], "acknowledge_notices")

    def test_plan_is_server_issued_not_arbitrary_accept_flag(self):
        self.assert_code("plan_expired", self.service.commit, "0" * 32, "0" * 64,
                         ["fixture-terms"], True)

    def test_issued_plan_is_defensive_copy(self):
        p = self.prepare()
        p["documents"][0]["text"] = "client mutation"
        self.assertNotEqual(self.service._plans[p["plan_id"]][1]["documents"][0]["text"],
                            p["documents"][0]["text"])

    def test_plan_capacity_and_expiry(self):
        p = self.prepare()
        with patch.object(ml.time, "monotonic", return_value=10**15):
            self.assert_code("plan_expired", self.commit, p)
        self.service._plans = {str(i): (ml.time.monotonic(), {}) for i in range(128)}
        self.assert_code("review_capacity_reached", self.prepare)


class ProductionTests(unittest.TestCase):
    def test_api_fixture_survives_service_module_purge(self):
        # Other suites clear services.* after test collection. Reproduce that
        # lifecycle in a child so this regression cannot pollute other tests.
        code = (
            "import sys, unittest; sys.path.insert(0, 'tests'); "
            "import test_model_license_workflow as test; import services; "
            "sys.modules.pop('services.model_licenses'); "
            "delattr(services, 'model_licenses'); "
            "suite = unittest.TestSuite(test.ApiTests(name) for name in ["
            "'test_api_blocked_production_rows_never_transfer', "
            "'test_api_boolean_not_coerced_and_unissued_plan_denied']); "
            "result = unittest.TextTestRunner(verbosity=2).run(suite); "
            "sys.exit(not result.wasSuccessful())"
        )
        result = subprocess.run([sys.executable, "-c", code], cwd=ROOT,
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_production_rows_never_use_synthetic_positive_fixtures(self):
        registry = ml.load_registry()
        self.assertEqual(len(registry["models"]), 57)
        self.assertEqual(len(registry["dynamic_assets"]), 7)
        for row in registry["models"]:
            self.assertEqual(row["component_closure"], "incomplete")
            self.assertEqual(row["documents"], [])
            self.assertEqual(row["artifacts"], [])
            self.assertTrue(ml.record_blockers(row))

    def test_duplicate_json_key_and_synthetic_registry_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "registry.json"
            path.write_text('{"schema_version": 2, "schema_version": 2}')
            with self.assertRaises(ml.ModelTermsError): ml.load_registry(path)
            value = synthetic_registry(); value["data_kind"] = "synthetic"
            path.write_text(json.dumps(value))
            with self.assertRaises(ml.ModelTermsError): ml.load_registry(path)

    def test_all_central_pins_preserved_and_from_one_registry(self):
        from services.hf_revisions import CURATED_REVISIONS
        data = ml.load_registry()
        expected = {r["id"]: r["runtime_revision"] for r in data["models"]
                    if r["runtime_pin_scope"] == "central"}
        self.assertEqual(CURATED_REVISIONS, expected)
        self.assertEqual(len(expected), 43)
        self.assertEqual(expected.pop("OpenMOSS-Team/MOSS-Audio-Tokenizer"),
                         "3cd226ba2947efa357ef453bcad111b6eafba782")
        # Preserve ALL 42 pre-existing pins from base 06c6e077 unchanged.
        self.assertEqual(ml.digest(expected), "421276b5ac88b32b36b838667e4cd2da8d410d48ba489dcffb8486772df9d858")
        self.assertEqual(expected["openbmb/VoxCPM2"], "bffb3df5a29440629464e5e839f4d214c8714c3d")

    def test_generated_projection_is_current(self):
        spec = importlib.util.spec_from_file_location("generate_model_license_data",
                                                      ROOT / "scripts/generate_model_license_data.py")
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        expected = module.generate(ROOT)
        actual = json.loads((ROOT / "docs/licensing/model-license-data.json").read_text(encoding="utf-8"))
        self.assertEqual(actual, expected)
        self.assertEqual(actual["enforcement"], "disclosure_only")

    def test_v1_migration_preserves_unknowns_and_evidence(self):
        spec = importlib.util.spec_from_file_location("migrate_model_licenses",
                                                      ROOT / "scripts/migrate_model_licenses.py")
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        old = {"schema_version": 1, "models": [{"id": "fixture/model", "review_status": "unreviewed",
               "commercial_use": False, "revision": "b" * 40, "notes": "Original unreviewed evidence"}],
               "dynamic_assets": [{"id": "dynamic", "commercial_use": False}]}
        runtime = 'CURATED_REVISIONS: dict[str, str] = {"fixture/model": "' + "a" * 40 + '"}'
        migrated = module.migrate(old, runtime, "c" * 40)
        self.assertEqual(old["schema_version"], 1)
        for key, value in old["models"][0].items():
            self.assertEqual(migrated["models"][0][key], value)
        row = migrated["models"][0]
        self.assertEqual(row["runtime_revision"], "a" * 40)
        self.assertEqual(row["commercial_inference"], "unknown")
        self.assertEqual(row["component_closure"], "incomplete")
        self.assertEqual(row["documents"], [])
        self.assertEqual(migrated["dynamic_assets"], old["dynamic_assets"])

    def test_explicit_legacy_scope_and_separate_cache(self):
        source = (ROOT / "backend/api/routers/model_licenses.py").read_text()
        self.assertIn('store / "artifacts"', source)
        self.assertIn('cache_dir=cache_dir', source)
        self.assertNotIn('snapshot_download(', source)
        self.assertNotIn('install_model(', source)
        router = (ROOT / "backend/api/routers/setup/__init__.py").read_text()
        self.assertLess(router.index('include_router(_model_licenses_router)'),
                        router.index('include_router(_download_router)'))


class ApiTests(unittest.TestCase):
    prepare = WorkflowTests.prepare
    fetch = WorkflowTests.fetch

    # Reuse synthetic setup only, without duplicating the core test suite.
    def setUp(self):
        WorkflowTests.setUp(self)
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from core.browser_guard import BrowserGuardMiddleware
        from api.routers import model_licenses as routes
        self.routes = routes
        # Keep fixture exceptions identical to the mounted router's imports,
        # even when another suite purges services.* after this test is collected.
        self.service = routes.ReviewedInstalls(self.root / "receipts-store", lambda: self.registry,
                                             lambda: self.catalog, lambda: self.source, "test-build")
        self.app = FastAPI()
        self.app.include_router(routes.router)
        self.app.add_middleware(BrowserGuardMiddleware)
        self.client = TestClient(self.app, base_url="http://127.0.0.1")
        self.addCleanup(self.client.close)
        self.patcher = patch.object(routes, "reviewed_installs", return_value=self.service)
        self.patcher.start(); self.addCleanup(self.patcher.stop)
        self.fetch_patch = patch.object(routes, "_fetch_file", side_effect=self.fetch)
        self.fetch_patch.start(); self.addCleanup(self.fetch_patch.stop)

    def request_body(self, p):
        return {"plan_id": p["plan_id"], "plan_digest": p["plan_digest"],
                "acknowledged_document_ids": ["fixture-terms"], "acknowledged": True,
                "target": "local"}

    def test_api_roundtrip_export_verify_remove(self):
        response = self.client.post("/models/install/prepare", json={"repo_id": "fixture/model"})
        self.assertEqual(response.status_code, 200)
        p = response.json()
        response = self.client.post("/models/install/commit", json=self.request_body(p))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["activation"], "not_integrated")
        response = self.client.get(f"/models/licenses/receipts/{p['plan_digest']}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(self.client.post(f"/models/licenses/verify/{p['plan_digest']}").status_code, 200)
        self.assertTrue(self.calls[-1][-1])
        self.assertEqual(self.client.delete(f"/models/licenses/receipts/{p['plan_digest']}").status_code, 200)
        self.assertEqual(self.client.post(f"/models/licenses/verify/{p['plan_digest']}").status_code, 409)

    def test_api_boolean_not_coerced_and_unissued_plan_denied(self):
        p = self.prepare(); body = self.request_body(p)
        body["acknowledged"] = "true"
        self.assertEqual(self.client.post("/models/install/commit", json=body).status_code, 422)
        body["acknowledged"] = True; body["plan_id"] = "0" * 32
        self.assertEqual(self.client.post("/models/install/commit", json=body).status_code, 409)
        self.assertEqual(self.calls, [])

    def test_api_rejects_unanticipated_selection_fields(self):
        response = self.client.post("/models/install/prepare", json={"repo_id": "fixture/model", "voice": "other"})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(self.calls, [])

    def test_api_blocked_production_rows_never_transfer(self):
        self.registry = ml.load_registry()
        self.catalog = [{"repo_id": self.registry["models"][0]["id"]}]
        p = self.client.post("/models/install/prepare", json={"repo_id": self.catalog[0]["repo_id"]}).json()
        self.assertEqual(p["readiness"], "blocked")
        response = self.client.post("/models/install/commit", json=self.request_body(p))
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"]["code"], "terms_unavailable")
        self.assertEqual(self.calls, [])

    def test_api_foreign_origin_cannot_acknowledge_or_remove(self):
        p = self.prepare()
        headers = {"Origin": "https://foreign.example", "Sec-Fetch-Site": "cross-site"}
        self.assertEqual(self.client.post("/models/install/commit", json=self.request_body(p), headers=headers).status_code, 403)
        self.assertEqual(self.client.delete(f"/models/licenses/receipts/{p['plan_digest']}", headers=headers).status_code, 403)
        self.assertEqual(self.client.get(f"/models/licenses/receipts/{p['plan_digest']}", headers=headers).status_code, 403)
        self.assertEqual(self.calls, [])

    def test_api_fetch_failure_is_sanitized_and_no_fallback(self):
        p = self.prepare()
        with patch.object(self.routes, "_fetch_file", side_effect=RuntimeError("secret /private/path")):
            response = self.client.post("/models/install/commit", json=self.request_body(p))
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {"detail": {"code": "reviewed_transfer_failed"}})



if __name__ == "__main__":
    unittest.main(verbosity=2)
