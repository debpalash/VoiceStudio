"""Docker quick starts must preserve the server-mode admin credential."""

import re
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "relative_path",
    ["docs/install/docker.md", "deploy/dockerhub-overview.md"],
)
def test_every_studio_docker_run_passes_the_admin_key(relative_path):
    text = (ROOT / relative_path).read_text(encoding="utf-8")
    commands = [
        block
        for block in re.findall(r"```bash\n(.*?)```", text, flags=re.DOTALL)
        if "docker run" in block and "omnivoice-studio:" in block
    ]

    assert commands
    for command in commands:
        assert '-e OMNIVOICE_API_KEY="$OMNIVOICE_API_KEY"' in command


def test_every_compose_studio_service_receives_the_admin_key():
    text = (ROOT / "deploy/docker-compose.yml").read_text(encoding="utf-8")
    studio_half = text.split("worker-gpu:", 1)[0]

    required_key = (
        "OMNIVOICE_API_KEY=${OMNIVOICE_API_KEY:?export a long random "
        "OMNIVOICE_API_KEY before starting a Studio profile}"
    )
    assert studio_half.count(required_key) == 3
    assert "OMNIVOICE_API_KEY=${OMNIVOICE_API_KEY:-}" not in studio_half


def test_server_mode_rejects_a_whitespace_admin_key(monkeypatch):
    from api.dependencies import validate_server_admin_key

    monkeypatch.setenv("OMNIVOICE_SERVER_MODE", "1")
    monkeypatch.setenv("OMNIVOICE_API_KEY", "   \t")

    with pytest.raises(RuntimeError, match="non-whitespace administrator key"):
        validate_server_admin_key()


def test_server_mode_allows_an_unset_or_nonblank_admin_key(monkeypatch):
    from api.dependencies import validate_server_admin_key

    monkeypatch.setenv("OMNIVOICE_SERVER_MODE", "1")
    monkeypatch.delenv("OMNIVOICE_API_KEY", raising=False)
    validate_server_admin_key()
    monkeypatch.setenv("OMNIVOICE_API_KEY", "real-secret")
    validate_server_admin_key()


def test_wsl_rocm_command_carries_the_complete_dxg_bridge():
    text = (ROOT / "docs/install/docker.md").read_text(encoding="utf-8")
    section = text.split("### AMD GPU on WSL2", 1)[1].split("###", 1)[0]

    for required in (
        "--device /dev/dxg",
        "/usr/lib/wsl/lib/libdxcore.so:/usr/lib/libdxcore.so",
        "/opt/rocm/lib/librocdxg.so:/usr/lib/librocdxg.so",
        "/opt/rocm/share/rocdxg/dids.conf:/usr/share/rocdxg/dids.conf",
        "HSA_ENABLE_DXG_DETECTION=1",
        "--cap-add SYS_PTRACE",
        "--security-opt seccomp=unconfined",
    ):
        assert required in section


def test_wsl_rocm_matrix_marks_rx_6700_xt_unverified():
    text = (ROOT / "docs/install/docker.md").read_text(encoding="utf-8")
    section = text.split("#### WSL2 architecture compatibility matrix", 1)[1].split(
        "###", 1
    )[0]

    for classification in (
        "Supported",
        "Best-effort override",
        "Unverified",
        "Unsupported",
    ):
        assert f"| **{classification}** |" in section

    rx_row = next(line for line in section.splitlines() if "RX 6700 XT" in line)
    assert "`gfx1031`" in rx_row
    assert "**Unverified**" in rx_row
    assert "`gfx1030`" in rx_row
    assert "`/dev/dxg` alone is not proof of acceleration" in section
    assert "CPU fallback stage and reason" in section
    assert "CPU-only completion" in section
    assert "successful GPU validation" in section
