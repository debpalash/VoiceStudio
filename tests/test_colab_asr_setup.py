import json
import os
import types
import unittest
from pathlib import Path
from unittest.mock import patch


NOTEBOOK = Path(__file__).resolve().parents[1] / "notebooks" / "OmniVoice_Studio_Colab.ipynb"


class ColabWebBuildTests(unittest.TestCase):
    def build_source(self):
        cells = json.loads(NOTEBOOK.read_text(encoding="utf-8"))["cells"]
        source = next("".join(c["source"]) for c in cells
                      if "# 2d." in "".join(c["source"]))
        return source[source.index("# 2d."):source.index("# 2e.")]

    def run_build(self, exists):
        calls = []
        with patch("os.path.exists", side_effect=exists):
            exec(self.build_source(), {
                "os": os, "REPO_DIR": "/content/VoiceStudio", "BUN": "bun",
                "run": lambda cmd, **kwargs: calls.append((cmd, kwargs)),
            })
        return calls

    def test_clean_runtime_builds_electron_web_renderer(self):
        calls = self.run_build([False, True])
        self.assertEqual([cmd for cmd, _ in calls], [
            ["bun", "install", "--frozen-lockfile"],
            ["bun", "run", "--cwd", "electron", "build:web"],
        ])
        self.assertTrue(all(opts["cwd"] == "/content/VoiceStudio" for _, opts in calls))

    def test_existing_bundle_skips_install_and_build(self):
        self.assertEqual(self.run_build([True]), [])

    def test_missing_build_output_stops_setup(self):
        with self.assertRaisesRegex(SystemExit, "frontend/dist/index.html is missing"):
            self.run_build([False, False])


class ColabAsrSetupTests(unittest.TestCase):
    """Cell 12a installs the ASR model; cells 13 and 18 refuse to run without it."""

    REPO = "Systran/faster-whisper-large-v3"

    def cells(self):
        return json.loads(NOTEBOOK.read_text(encoding="utf-8"))["cells"]

    def source_index(self, predicate):
        for i, c in enumerate(self.cells()):
            if c["cell_type"] == "code" and predicate("".join(c["source"])):
                return i
        self.fail("no matching code cell")

    def code_source(self, marker):
        cells = self.cells()
        return "".join(cells[self.source_index(lambda s: s.startswith(marker))]["source"])

    def run_setup(self, snapshot_download, namespace=None):
        fake = types.ModuleType("huggingface_hub")
        fake.snapshot_download = snapshot_download
        ns = namespace if namespace is not None else {}
        with patch.dict("sys.modules", {"huggingface_hub": fake}):
            exec(self.code_source("# ── 12a."), ns)
        return ns

    def test_setup_downloads_the_default_model_into_the_shared_cache(self):
        calls = []
        ns = self.run_setup(lambda *a, **kw: calls.append((a, kw)) or "/cache/snap")
        self.assertEqual(len(calls), 1)
        args, kwargs = calls[0]
        self.assertEqual(kwargs.get("repo_id", args[0] if args else None), self.REPO)
        self.assertNotIn("local_dir", kwargs)
        self.assertIs(ns["ASR_MODEL_READY"], True)

    def test_setup_can_be_rerun(self):
        calls = []
        ns = self.run_setup(lambda **kw: calls.append(kw) or "/cache/snap")
        self.run_setup(lambda **kw: calls.append(kw) or "/cache/snap", ns)
        self.assertEqual(len(calls), 2)
        self.assertIs(ns["ASR_MODEL_READY"], True)

    def test_failed_download_propagates_and_clears_the_flag(self):
        def broken(**kwargs):
            raise OSError("network down")
        ns = {"ASR_MODEL_READY": True}
        with self.assertRaises(OSError):
            self.run_setup(broken, ns)
        self.assertIs(ns["ASR_MODEL_READY"], False)

    def test_transcription_and_dubbing_need_the_setup_cell(self):
        for marker in ("# ── 13.", "# ── 18."):
            with self.subTest(cell=marker):
                with self.assertRaisesRegex(SystemExit, "12a"):
                    exec(self.code_source(marker), {})

    def test_setup_cell_runs_before_transcription_cell(self):
        setup = self.source_index(lambda s: s.startswith("# ── 12a."))
        transcribe = self.source_index(lambda s: s.startswith("# ── 13."))
        dub = self.source_index(lambda s: s.startswith("# ── 18."))
        self.assertLess(setup, transcribe)
        self.assertLess(setup, dub)

    def test_prefetch_cell_runs_before_backend_launch(self):
        prefetch = self.source_index(lambda s: f"hf download {self.REPO}" in s)
        launch = self.source_index(lambda s: s.startswith("# ── 5. Launch the backend"))
        self.assertLess(prefetch, launch)



class ColabLicenceErrorTests(unittest.TestCase):
    """Model-licence 403s read as a licence problem, not a download/GPU failure (#3076)."""

    BODY = {"detail": {"code": "model_licence_required", "message": "Accept the licence.",
                       "models": [{"repo_id": "org/model-a"}]}}

    def cell(self, marker):
        cells = json.loads(NOTEBOOK.read_text(encoding="utf-8"))["cells"]
        return next("".join(c["source"]) for c in cells
                    if c["cell_type"] == "code" and marker in "".join(c["source"]))

    def helpers(self):
        src = self.cell("def api_fail")
        ns = {"json": json, "LOG_HINT": "LOG"}
        exec(src[src.index("def _error_detail"):src.index("def speak")], ns)
        return ns

    def test_api_fail_names_licence_and_models(self):
        resp = types.SimpleNamespace(status_code=403, text=json.dumps(self.BODY),
                                     json=lambda: self.BODY)
        with self.assertRaisesRegex(SystemExit, r"model licence not accepted[\s\S]*org/model-a[\s\S]*cell 6[\s\S]*Model Catalogue"):
            self.helpers()["api_fail"](resp, "/generate")

    def test_api_fail_keeps_plain_http_errors(self):
        def not_json():
            raise ValueError("not json")
        resp = types.SimpleNamespace(status_code=500, text="boom", json=not_json)
        with self.assertRaisesRegex(SystemExit, r"HTTP 500\nboom") as cm:
            self.helpers()["api_fail"](resp, "/generate")
        self.assertNotIn("licence", str(cm.exception))

    def run_smoke(self, body, code):
        import io
        import urllib.error

        def urlopen(req, timeout=None):
            if isinstance(req, str):
                return io.BytesIO(b"{}")
            raise urllib.error.HTTPError(
                "http://x/generate", code, "err", {}, io.BytesIO(body.encode()))
        with patch("urllib.request.urlopen", side_effect=urlopen):
            exec(self.cell("# ── 7. Smoke test"), {})

    def test_smoke_cell_names_licence(self):
        with self.assertRaisesRegex(SystemExit, r"model licence not accepted[\s\S]*org/model-a[\s\S]*cell 6[\s\S]*Model Catalogue"):
            self.run_smoke(json.dumps(self.BODY), 403)

    def test_smoke_cell_keeps_plain_http_errors(self):
        with self.assertRaisesRegex(SystemExit, r"HTTP 500\nboom") as cm:
            self.run_smoke("boom", 500)
        self.assertNotIn("licence", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
