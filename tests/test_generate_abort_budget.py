"""The UI's /generate backstop must outlast the backend's own budget.

electron/src/shared/utils/generateBudget.ts mirrors the backend's default
budgets. If the backend grows one (a longer queue wait, a bigger sidecar
receive timeout) without the client following, the UI reports a failure while
the job is still running — the 21-minute backstop did exactly that against a
30-minute queue wait. This keeps the mirror equal to the backend defaults.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUDGET_TS = ROOT / "electron/src/shared/utils/generateBudget.ts"


def _client_budget() -> dict[str, float]:
    text = BUDGET_TS.read_text(encoding="utf-8")
    block = re.search(r"BACKEND_GENERATE_BUDGET_S = \{(.*?)\} as const", text, re.S).group(1)
    return {key: float(value) for key, value in re.findall(r"(\w+): ([\d.]+)", block)}


def _source_default(path: Path, env: str) -> float:
    match = re.search(rf'"{env}",\s*"([\d.]+)"', path.read_text(encoding="utf-8"))
    assert match, f"default for {env} not found in {path}"
    return float(match.group(1))


def test_client_budget_mirrors_backend_defaults(monkeypatch):
    from services import model_manager
    from worker import deadlines
    import mcp_server

    monkeypatch.delenv("OMNIVOICE_MODEL_LOAD_TIMEOUT", raising=False)
    manager = ROOT / "backend/services/model_manager.py"
    client = _client_budget()

    assert client["modelLoad"] == model_manager._model_load_timeout()
    assert client["queueWait"] == _source_default(manager, "OMNIVOICE_GPU_QUEUE_TIMEOUT_S")
    assert client["progressExtensionCap"] == model_manager.progress_extension_cap_s({})
    assert client["progressExtensionBudgets"] == model_manager.PROGRESS_EXTENSION_BUDGETS
    assert mcp_server._GENERATE_PROGRESS_BUDGETS == model_manager.PROGRESS_EXTENSION_BUDGETS
    assert mcp_server._GENERATE_SIDECAR_FLOOR_S == client["executionBase"]
    assert mcp_server._GENERATE_SIDECAR_GRACE_S == client["sidecarGrace"]
    assert client["freeChars"] == deadlines._FREE_CHARS
    assert client["charsPerSecond"] == deadlines._CHARS_PER_SECOND
    from core import generate_budget as gb

    assert client["cpuSecondsPerChar"] == gb.CPU_SECONDS_PER_CHAR
    assert client["cpuAutoCap"] == gb.CPU_AUTO_CAP_S
    assert client["textExpansionFactor"] == gb.TEXT_EXPANSION_FACTOR
    grace = re.search(r"sidecar_grace = ([\d.]+) if _include_sidecar_grace", manager.read_text())
    assert grace and client["sidecarGrace"] >= float(grace.group(1))


def test_client_execution_base_covers_every_default_execution_budget():
    """The largest of the host budgets and every sidecar's receive timeout."""
    manager = ROOT / "backend/services/model_manager.py"
    bases = [
        _source_default(manager, "OMNIVOICE_GENERATE_TIMEOUT_S"),
        _source_default(manager, "OMNIVOICE_CPU_GENERATE_TIMEOUT_S"),
    ]
    from services.subprocess_backend import GENERATE_RECV_TIMEOUT_S

    bases.append(GENERATE_RECV_TIMEOUT_S)
    for path in (ROOT / "backend/engines").rglob("*.py"):
        bases += [
            float(value)
            for value in re.findall(
                r'"OMNIVOICE_\w+_RECV_TIMEOUT_S",\s*"([\d.]+)"', path.read_text(encoding="utf-8")
            )
        ]
    assert len(bases) > 3, "receive-timeout scan found nothing; fix the regex"
    assert _client_budget()["executionBase"] >= max(bases)

