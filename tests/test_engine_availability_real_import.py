"""An engine whose package is installed but cannot be imported (missing
dependency, broken native library) must not report ready. #4320 briefly checked
only that the package spec existed, so /engines and /engines/{id}/health said
"ready" for engines that would fail on first use."""
import sys

import pytest

from services.tts_backend import KittenTTSBackend, OmniVoiceBackend


def _purge(prefix):
    for name in [n for n in sys.modules if n == prefix or n.startswith(prefix + ".")]:
        del sys.modules[name]


@pytest.fixture()
def broken_packages(tmp_path, monkeypatch):
    (tmp_path / "kittentts").mkdir()
    (tmp_path / "kittentts" / "__init__.py").write_text("import libmissing_native_dep\n")
    models = tmp_path / "omnivoice" / "models"
    models.mkdir(parents=True)
    (tmp_path / "omnivoice" / "__init__.py").write_text("")
    (models / "__init__.py").write_text("")
    (models / "omnivoice.py").write_text("raise OSError('libcudnn.so.9: cannot open shared object file')\n")
    saved = dict(sys.modules)
    monkeypatch.syspath_prepend(str(tmp_path))
    _purge("kittentts")
    _purge("omnivoice")
    yield
    # Drop the fake packages and put back exactly what was loaded before.
    _purge("kittentts")
    _purge("omnivoice")
    sys.modules.update({n: m for n, m in saved.items() if n.split(".")[0] in ("kittentts", "omnivoice")})


@pytest.mark.parametrize(
    ("backend", "hint"),
    [(KittenTTSBackend, "libmissing_native_dep"), (OmniVoiceBackend, "libcudnn")],
)
def test_present_but_unimportable_package_is_not_ready(broken_packages, backend, hint):
    ok, message = backend.is_available()
    assert ok is False
    assert hint in message  # names the actual cause, so the user can act on it


@pytest.mark.parametrize("engine_id", ["kittentts"])  # omnivoice resolves to its sidecar class
def test_health_endpoint_reports_not_ready_for_unimportable_package(broken_packages, engine_id):
    from api.routers.engines import _resolve_engine_class, engine_health

    if _resolve_engine_class(engine_id) is None:
        pytest.skip(f"{engine_id} is not a registered engine id in this build")
    result = engine_health(engine_id)
    assert result["ok"] is False
    assert result["message"]
