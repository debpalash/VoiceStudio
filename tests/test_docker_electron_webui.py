from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_docker_builds_and_serves_the_maintained_electron_renderer():
    dockerfile = (ROOT / "deploy" / "Dockerfile").read_text(encoding="utf-8")
    assert "COPY electron/package.json ./electron/" in dockerfile
    assert "COPY electron/ ./electron/" in dockerfile
    assert "bun run --cwd electron build:web" in dockerfile
    assert "bun run --cwd frontend build" not in dockerfile
    assert "COPY --from=frontend-builder /app/frontend/dist ./frontend/dist" in dockerfile


def test_root_web_scripts_use_the_electron_renderer():
    package = (ROOT / "package.json").read_text(encoding="utf-8")
    assert '"dev:frontend": "bun run --cwd electron dev:web"' in package
    assert '"build:web": "bun run --cwd electron build:web"' in package
