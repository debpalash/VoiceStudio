"""The published image must expose a worker-only Compose path.

The worker agent already starts from the FastAPI lifespan.  Packaging is the
contract here: a GPU box must be able to run that lifespan with only a join
token, without publishing the Studio UI or inventing a console script that the
wheel does not install.
"""
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
HEADLESS_SOURCE_COMMAND = (
    "uv run uvicorn backend.main:app --host 127.0.0.1 --port 3900"
)


def _environment(service: dict) -> dict[str, str]:
    values: dict[str, str] = {}
    for item in service.get("environment", []):
        key, _, value = item.partition("=")
        values[key] = value
    return values


@pytest.mark.parametrize(
    ("service_name", "profile", "image_suffix"),
    [
        ("omnivoice-worker-gpu", "worker-gpu", ":stable"),
        ("omnivoice-worker-rocm", "worker-rocm", ":stable-rocm"),
    ],
)
def test_compose_has_worker_only_gpu_profiles(service_name, profile, image_suffix):
    compose = yaml.safe_load(
        (ROOT / "deploy" / "docker-compose.yml").read_text(encoding="utf-8")
    )
    service = compose["services"][service_name]
    environment = _environment(service)

    assert profile in service["profiles"]
    assert service["image"].endswith(image_suffix)
    assert "ports" not in service, "a worker-only container must not publish the UI"
    assert service["entrypoint"] == ["python3", "-m", "uvicorn"]
    assert service["command"] == [
        "backend.main:app",
        "--host",
        "127.0.0.1",
        "--port",
        "3900",
    ]
    assert environment["OMNIVOICE_WORKER_MODE"] == "1"
    assert environment["OMNIVOICE_WORKER_TOKEN"] == "${OMNIVOICE_WORKER_TOKEN:-}"
    assert service["healthcheck"]["test"] == [
        "CMD",
        "curl",
        "-fsS",
        "http://127.0.0.1:3900/workers/agent/readiness",
    ]


@pytest.mark.parametrize(
    "service_name", ["omnivoice", "omnivoice-gpu", "omnivoice-rocm"]
)
def test_compose_studios_publish_a_reachable_worker_control_plane(service_name):
    compose = yaml.safe_load(
        (ROOT / "deploy" / "docker-compose.yml").read_text(encoding="utf-8")
    )
    service = compose["services"][service_name]
    environment = _environment(service)

    assert (
        "${OMNIVOICE_WORKER_PUBLISH_HOST:-127.0.0.1}:"
        "${OMNIVOICE_WORKER_PORT:-7443}:${OMNIVOICE_WORKER_PORT:-7443}"
    ) in service["ports"]
    assert environment["OMNIVOICE_WORKER_PORT"] == "${OMNIVOICE_WORKER_PORT:-7443}"
    assert environment["OMNIVOICE_WORKER_ENDPOINT_HOST"] == (
        "${OMNIVOICE_WORKER_ENDPOINT_HOST:-}"
    )


def test_headless_docs_and_acceptance_script_use_the_supported_backend_command():
    guide = (ROOT / "docs" / "remote-workers.md").read_text(encoding="utf-8")
    acceptance = (ROOT / "scripts" / "verify-remote-worker.sh").read_text(
        encoding="utf-8"
    )

    assert HEADLESS_SOURCE_COMMAND in guide
    assert HEADLESS_SOURCE_COMMAND in acceptance
    assert "OMNIVOICE_WORKER_MODE=1 omnivoice" not in guide
    assert "OMNIVOICE_WORKER_MODE=1 omnivoice" not in acceptance


def test_compose_quick_start_pulls_released_images_without_dead_knobs():
    """`:latest` is the rolling main preview; the quick start must pull the
    release. An active `build:` next to `image:` would tag a local build as
    that release, and OMNIVOICE_BIND_HOST is a no-op under the image's uvicorn
    ENTRYPOINT, which binds 0.0.0.0 itself."""
    compose = yaml.safe_load(
        (ROOT / "deploy" / "docker-compose.yml").read_text(encoding="utf-8")
    )
    dockerfile = (ROOT / "deploy" / "Dockerfile").read_text(encoding="utf-8")
    assert '"--host", "0.0.0.0"' in dockerfile
    for name, service in compose["services"].items():
        assert service["image"].rsplit(":", 1)[1] in {"stable", "stable-rocm"}, name
        assert "build" not in service, name
        assert "OMNIVOICE_BIND_HOST" not in _environment(service), name
