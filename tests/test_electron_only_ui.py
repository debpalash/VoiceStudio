import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_tauri_shell_and_legacy_ui_entrypoints_are_removed():
    assert not (ROOT / "frontend/src-tauri").exists()
    assert not (ROOT / "frontend/src").exists()
    assert not (ROOT / ".github/workflows/release.yml").exists()
    for path in (
        "frontend/index.html",
        "electron/src/shared/main.jsx",
        "electron/src/shared/main-app.jsx",
        "electron/src/shared/App.jsx",
        "scripts/desktop-dev.mjs",
        "scripts/desktop-dev-launch.mjs",
        "scripts/desktop-prod.sh",
        "scripts/desktop-prod.mjs",
        "scripts/desktop-fresh.mjs",
    ):
        assert not (ROOT / path).exists(), f"retired Tauri entrypoint remains: {path}"


def test_supported_ui_commands_target_electron_only():
    root_package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
    assert root_package["workspaces"] == ["electron"]
    for command in ("dev", "desktop", "build", "build:web", "start", "test", "typecheck"):
        assert "electron" in root_package["scripts"][command]

    assert not (ROOT / "frontend/package.json").exists()
    manifests = [ROOT / "package.json", ROOT / "electron/package.json"]
    assert all("@tauri-apps/" not in path.read_text(encoding="utf-8") for path in manifests)

    electron_package = json.loads((ROOT / "electron/package.json").read_text(encoding="utf-8"))
    assert "vite.shared.config.ts" in electron_package["scripts"]["test"]


def test_remote_worker_acceptance_uses_the_electron_workspace():
    script = (ROOT / "scripts/verify-remote-worker.sh").read_text(encoding="utf-8")
    assert "--cwd frontend" not in script
    assert script.count("bun run --cwd electron test -- src/shared/") == 2


def test_docker_builds_the_electron_renderer():
    dockerfile = (ROOT / "deploy/Dockerfile").read_text(encoding="utf-8")
    assert "--cwd electron build:web" in dockerfile
    assert "frontend/index.html" not in dockerfile


def test_windows_install_smoke_builds_the_pull_request_checkout():
    workflow = (ROOT / ".github/workflows/install-smoke.yml").read_text(encoding="utf-8")
    windows_step = workflow.split("- name: Build and install main (Windows)", 1)[1]
    assert "GITHUB_EVENT_NAME -eq 'pull_request'" in windows_step
    assert "git push $bare HEAD:refs/heads/main" in windows_step
    assert "url.file:///" in windows_step
