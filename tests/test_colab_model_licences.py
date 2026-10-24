"""The Colab smoke test asks for model licence acceptance instead of dying on 403 (#2921)."""
import io
import json
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request


NOTEBOOK = Path(__file__).resolve().parents[1] / "notebooks/OmniVoice_Studio_Colab.ipynb"
TOGGLE = 'ACCEPT_MODEL_LICENCES = False  # @param {type:"boolean"}'
MODELS = [
    {"repo_id": "k2-fsa/OmniVoice", "license": "CC-BY-NC", "category": "noncommercial",
     "fingerprint": "v2:" + "a" * 64},
    {"repo_id": "eustlb/higgs-audio-v2-tokenizer", "license": "NOASSERTION",
     "category": "unknown", "fingerprint": "v2:" + "b" * 64},
]
GATE = json.dumps({"detail": {"code": "model_licence_required",
                              "message": "Licence not accepted", "models": MODELS}})


def cell(marker):
    cells = json.loads(NOTEBOOK.read_text())["cells"]
    return next("".join(c["source"]) for c in cells
                if c["cell_type"] == "code" and "".join(c["source"]).startswith(marker))


class _Response(io.BytesIO):
    def __init__(self, body=b"", headers=None):
        super().__init__(body)
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def _http_error(url, code, body):
    return urllib.error.HTTPError(url, code, "error", {}, io.BytesIO(body.encode()))


class FakeBackend:
    """Answers /health, gates /generate until both models are accepted."""

    def __init__(self, generate_error=None):
        self.accepted = []
        self.generate_calls = 0
        self.generate_error = generate_error

    def urlopen(self, request, timeout=None):
        url = request if isinstance(request, str) else request.full_url
        if url.endswith("/health"):
            return _Response(b'{"status": "ok"}')
        if url.endswith("/models/licenses/accept"):
            self.accepted.append(json.loads(request.data))
            return _Response(b'{"accepted": true}')
        if url.endswith("/generate"):
            self.generate_calls += 1
            if self.generate_error:
                raise _http_error(url, *self.generate_error)
            if len(self.accepted) < len(MODELS):
                raise _http_error(url, 403, GATE)
            return _Response(b"RIFFwav", {"X-Audio-Duration": "1.0"})
        raise AssertionError(f"unexpected request {url}")


class ColabSmokeLicenceTests(unittest.TestCase):
    def run_smoke(self, backend, accept):
        source = cell("# ── 7. Smoke test")
        self.assertIn(TOGGLE, source)
        if accept:
            source = source.replace(TOGGLE, "ACCEPT_MODEL_LICENCES = True")
        written = {}

        class _Sink(io.BytesIO):
            def __init__(self, path):
                super().__init__()
                self.path = path

            def __exit__(self, *exc):
                written[self.path] = self.getvalue()

        display = types.ModuleType("IPython.display")
        display.Audio = lambda path: path
        display.display = lambda obj: None
        ipython = types.ModuleType("IPython")
        ipython.display = display
        scope = {"open": lambda path, mode="r": _Sink(path)}
        with patch.object(urllib.request, "urlopen", backend.urlopen), \
                patch.dict(sys.modules, {"IPython": ipython, "IPython.display": display}), \
                patch("builtins.print"):
            exec(source, scope)
        return written

    def test_gate_without_acceptance_stops_with_the_licences(self):
        backend = FakeBackend()
        with self.assertRaises(SystemExit) as stop:
            self.run_smoke(backend, accept=False)
        message = str(stop.exception)
        self.assertIn("k2-fsa/OmniVoice: CC-BY-NC (noncommercial)", message)
        self.assertIn("eustlb/higgs-audio-v2-tokenizer", message)
        self.assertIn("ACCEPT_MODEL_LICENCES", message)
        self.assertEqual(backend.accepted, [])
        self.assertEqual(backend.generate_calls, 1)

    def test_ticked_box_accepts_the_named_terms_and_retries(self):
        backend = FakeBackend()
        written = self.run_smoke(backend, accept=True)
        self.assertEqual(backend.accepted, [
            {"repo_id": m["repo_id"], "fingerprint": m["fingerprint"], "accepted": True}
            for m in MODELS
        ])
        self.assertEqual(backend.generate_calls, 2)
        self.assertEqual(written, {"/content/omnivoice_smoke.wav": b"RIFFwav"})

    def test_other_errors_keep_the_generation_failure_message(self):
        backend = FakeBackend(generate_error=(500, '{"detail": "boom"}'))
        with self.assertRaisesRegex(SystemExit, "Generation failed: HTTP 500"):
            self.run_smoke(backend, accept=True)
        self.assertEqual(backend.accepted, [])
        self.assertEqual(backend.generate_calls, 1)

    def test_a_gate_after_acceptance_is_not_retried_forever(self):
        backend = FakeBackend(generate_error=(403, GATE))
        with self.assertRaisesRegex(SystemExit, "Generation failed: HTTP 403"):
            self.run_smoke(backend, accept=True)
        self.assertEqual(backend.generate_calls, 2)


class ColabTourHelperTests(unittest.TestCase):
    def test_tour_failure_points_at_the_licence_step(self):
        requests = types.ModuleType("requests")
        display = types.ModuleType("IPython.display")
        display.Audio = display.display = lambda *a: None
        ipython = types.ModuleType("IPython")
        ipython.display = display
        scope = {}
        with patch.dict(sys.modules, {"requests": requests, "IPython": ipython,
                                      "IPython.display": display}), \
                patch("os.makedirs"), patch("builtins.print"):
            exec(cell("# ── 8. Feature-tour helpers"), scope)
        response = types.SimpleNamespace(status_code=403, text=GATE)
        with self.assertRaisesRegex(SystemExit, "run cell 7"):
            scope["api_fail"](response, "/generate (demo.wav)")
        response = types.SimpleNamespace(status_code=500, text="boom")
        with self.assertRaisesRegex(SystemExit, "omnivoice_backend.log"):
            scope["api_fail"](response, "/generate (demo.wav)")


if __name__ == "__main__":
    unittest.main()
