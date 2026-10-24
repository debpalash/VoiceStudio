"""Execute notebook prerequisite ordering with mocked downloads (#1922)."""
import json
import os
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch


NOTEBOOK = Path(__file__).resolve().parents[1] / "notebooks/OmniVoice_Studio_Colab.ipynb"


class ColabWebBuildTests(unittest.TestCase):
    def build_source(self):
        cells = json.loads(NOTEBOOK.read_text())["cells"]
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


class ColabASRSetupTests(unittest.TestCase):
    def setup_source(self):
        cells = json.loads(NOTEBOOK.read_text())["cells"]
        for cell in cells:
            source = "".join(cell["source"])
            if cell["cell_type"] == "code" and "12a. Install the transcription model" in source:
                return source
        self.fail("Notebook needs an explicit ASR setup cell before transcription")

    def test_setup_downloads_exact_repo_and_can_be_rerun(self):
        calls = []
        def download(repo_id):
            self.assertEqual(repo_id, "Systran/faster-whisper-large-v3")
            calls.append(repo_id)
            return "/fake/hub/snapshot"

        module = types.ModuleType("huggingface_hub")
        module.snapshot_download = download
        scope = {}
        with patch.dict(sys.modules, {"huggingface_hub": module}):
            exec(self.setup_source(), scope)
            self.assertTrue(scope["ASR_MODEL_READY"])
            self.assertEqual(len(calls), 1)
            calls.clear()
            exec(self.setup_source(), scope)
            self.assertEqual(len(calls), 1)

    def test_failed_download_clears_previous_ready_state(self):
        module = types.ModuleType("huggingface_hub")

        def download(*args, **kwargs):
            raise OSError("download interrupted")

        module.snapshot_download = download
        scope = {"ASR_MODEL_READY": True}
        with patch.dict(sys.modules, {"huggingface_hub": module}):
            with self.assertRaises(OSError):
                exec(self.setup_source(), scope)
        self.assertFalse(scope["ASR_MODEL_READY"])

    def test_transcription_and_dubbing_gate_before_any_work(self):
        cells = json.loads(NOTEBOOK.read_text())["cells"]
        for number in (13, 18):
            source = next("".join(c["source"]) for c in cells
                          if c["cell_type"] == "code"
                          and f" {number}. " in "".join(c["source"]).splitlines()[0])
            with self.subTest(cell=number):
                with self.assertRaisesRegex(SystemExit, "12a"):
                    exec(source, {})

    def test_setup_precedes_transcription_in_run_all(self):
        code = ["".join(c["source"]) for c in json.loads(NOTEBOOK.read_text())["cells"]
                if c["cell_type"] == "code"]
        setup = next(i for i, s in enumerate(code)
                     if "12a. Install the transcription model" in s)
        transcribe = next(i for i, s in enumerate(code)
                          if " 13. " in s.splitlines()[0])
        self.assertLess(setup, transcribe)


class ColabLicenceErrorTests(unittest.TestCase):
    """Model-licence 403s read as a licence problem, not a download/GPU failure (#3076)."""

    BODY = {"detail": {"code": "model_licence_required", "message": "Accept the licence.",
                       "models": [{"repo_id": "org/model-a"}]}}

    def cell(self, marker):
        cells = json.loads(NOTEBOOK.read_text())["cells"]
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
        with self.assertRaisesRegex(RuntimeError, r"model licence not accepted[\s\S]*org/model-a"):
            self.helpers()["api_fail"](resp, "/generate")

    def test_api_fail_keeps_plain_http_errors(self):
        def not_json():
            raise ValueError("not json")
        resp = types.SimpleNamespace(status_code=500, text="boom", json=not_json)
        with self.assertRaisesRegex(RuntimeError, r"HTTP 500\nboom") as cm:
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
        with self.assertRaisesRegex(RuntimeError, r"model licence not accepted[\s\S]*org/model-a"):
            self.run_smoke(json.dumps(self.BODY), 403)

    def test_smoke_cell_keeps_plain_http_errors(self):
        with self.assertRaisesRegex(RuntimeError, r"HTTP 500\nboom") as cm:
            self.run_smoke("boom", 500)
        self.assertNotIn("licence", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
