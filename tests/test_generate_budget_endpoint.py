"""GET /generate/budget reports the active budgets the UI backstop must outlast."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUDGET_TS = ROOT / "electron/src/shared/utils/generateBudget.ts"


def test_reported_keys_are_ones_the_client_reads():
    from services import model_manager

    reported = set(model_manager.generate_budget_s())
    client = re.search(r"Record<\s*([^,]+),\s*unknown", BUDGET_TS.read_text(encoding="utf-8")).group(1)
    assert reported == set(re.findall(r"'(\w+)'", client))


def test_operator_overrides_are_reported(monkeypatch):
    from services import model_manager

    monkeypatch.setattr(model_manager, "GPU_QUEUE_TIMEOUT_S", 7200.0)
    monkeypatch.setattr(model_manager, "CPU_JOB_TIMEOUT_S", 4000.0)
    monkeypatch.setenv("OMNIVOICE_MODEL_LOAD_TIMEOUT", "9000")
    budget = model_manager.generate_budget_s()
    assert budget["queueWait"] == 7200.0
    assert budget["executionBase"] >= 4000.0
    assert budget["modelLoad"] == 9000.0


def test_route_is_registered():
    from api.routers import generation

    paths = {(route.path, tuple(sorted(route.methods))) for route in generation.router.routes}
    assert ("/generate/budget", ("GET",)) in paths
