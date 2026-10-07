"""#2040 — the MCP tools gave up after a fixed 120 s while the backend's own
budgets run longer (ASR: 300 s), so a request the backend would have finished
came back as an empty client-side timeout."""
import pytest

_BUDGET_VARS = (
    "OMNIVOICE_MCP_TIMEOUT_S",
    "OMNIVOICE_ASR_TRANSCRIBE_TIMEOUT_S",
    "OMNIVOICE_GENERATE_TIMEOUT_S",
    "OMNIVOICE_CPU_GENERATE_TIMEOUT_S",
    "OMNIVOICE_GPU_QUEUE_TIMEOUT_S",
    "OMNIVOICE_MODEL_LOAD_TIMEOUT",
    "OMNIVOICE_PROGRESS_EXTENSION_CAP_S",
    "OMNIVOICE_MODEL_LOAD_TIMEOUT_S",
)
QUEUE = 1800.0
GRACE = 30.0


def _generate_wait(
    execution, *, queue=QUEUE, model_load=1200.0, extension_cap=1800.0,
    reference_base=600.0,
):
    execution += 5.0  # sidecar watchdog grace
    reference = queue + reference_base + max(extension_cap, 3.0 * reference_base)
    return model_load + reference + queue + execution + max(extension_cap, 3.0 * execution) + GRACE


@pytest.fixture
def post_timeout(monkeypatch):
    for name in _BUDGET_VARS:
        monkeypatch.setenv(name, "1")  # recorded, so the teardown restores it
        monkeypatch.delenv(name)
    import mcp_server

    return mcp_server._post_timeout_s


def test_transcribe_waits_past_the_backends_asr_budget(post_timeout):
    assert post_timeout("transcribe") == 300.0 + GRACE


def test_a_raised_asr_budget_is_followed(post_timeout, monkeypatch):
    monkeypatch.setenv("OMNIVOICE_ASR_TRANSCRIBE_TIMEOUT_S", "900")
    assert post_timeout("transcribe") == 900.0 + GRACE


def test_generation_covers_the_queue_and_the_length_scaled_budget(post_timeout):
    # With the default CPU budget the backend grants up to the automatic ceiling
    # for text whose normalized length the tool cannot see (#2609), so the tool
    # waits for that ceiling whatever the typed length.
    from core.generate_budget import CPU_AUTO_CAP_S

    assert post_timeout("generate", "short") == _generate_wait(CPU_AUTO_CAP_S)
    assert post_timeout("generate", "x" * 1600) == _generate_wait(CPU_AUTO_CAP_S)


def test_the_larger_cpu_generation_budget_wins(post_timeout, monkeypatch):
    monkeypatch.setenv("OMNIVOICE_CPU_GENERATE_TIMEOUT_S", "900")
    assert post_timeout("generate", "short") == _generate_wait(900.0, reference_base=900.0)
    # An explicit CPU budget is authoritative and uncapped: no ceiling applies.
    assert post_timeout("generate", "x" * 1600) == _generate_wait(
        900.0 + (1600 * 16 - 1200) / 40.0, reference_base=900.0,
    )


def test_a_raised_gpu_generation_budget_wins_when_larger(post_timeout, monkeypatch):
    monkeypatch.setenv("OMNIVOICE_GENERATE_TIMEOUT_S", "1200")
    monkeypatch.setenv("OMNIVOICE_CPU_GENERATE_TIMEOUT_S", "600")
    assert post_timeout("generate", "short") == _generate_wait(1200.0, reference_base=1200.0)


def test_a_shorter_queue_budget_is_followed(post_timeout, monkeypatch):
    monkeypatch.setenv("OMNIVOICE_GPU_QUEUE_TIMEOUT_S", "60")
    from core.generate_budget import CPU_AUTO_CAP_S

    assert post_timeout("generate", "short") == _generate_wait(CPU_AUTO_CAP_S, queue=60.0)


def test_an_explicit_mcp_timeout_still_wins(post_timeout, monkeypatch):
    monkeypatch.setenv("OMNIVOICE_MCP_TIMEOUT_S", "45")
    assert post_timeout("transcribe") == 45.0
    assert post_timeout("generate", "x" * 5000) == 45.0


def test_other_posts_keep_the_old_default(post_timeout):
    assert post_timeout() == 120.0


def test_unusable_values_fall_back(post_timeout, monkeypatch):
    monkeypatch.setenv("OMNIVOICE_ASR_TRANSCRIBE_TIMEOUT_S", "soon")
    assert post_timeout("transcribe") == 300.0 + GRACE
    monkeypatch.setenv("OMNIVOICE_MCP_TIMEOUT_S", "-5")
    assert post_timeout("transcribe") == 120.0


# ── Parity with the backend's own clocks: a tool must never give up first ──


def test_transcribe_never_gives_up_before_the_backend(post_timeout):
    from services import asr_backend

    assert post_timeout("transcribe") > asr_backend.ASR_TRANSCRIBE_TIMEOUT_S


@pytest.mark.parametrize("device", ["cpu", "cuda", "mps"])
@pytest.mark.parametrize("text", ["short", "x" * 5000])
def test_generation_never_gives_up_before_the_backend(post_timeout, device, text):
    from services import model_manager as mm

    from types import SimpleNamespace

    execution = mm.generate_timeout_s(
        text, execution_device=device, engine=SimpleNamespace(recv_timeout_s=900.0),
    )
    reference = mm.generate_timeout_s("", execution_device=device)
    backend_worst = (
        mm._model_load_timeout() + 2 * mm.GPU_QUEUE_TIMEOUT_S + reference
        + max(mm.progress_extension_cap_s(), mm.PROGRESS_EXTENSION_BUDGETS * reference)
        + execution
        + max(mm.progress_extension_cap_s(), mm.PROGRESS_EXTENSION_BUDGETS * execution)
    )
    assert post_timeout("generate", text) > backend_worst


def test_cpu_generation_covers_cold_load_and_progress_extensions(post_timeout):
    # Fail-before: queue + base + grace was 9,030 s, but a progressing CPU
    # render can legally use 28,800 s of compute alone. Sidecar grace is
    # included before sizing its extension, just as in the real guard.
    assert post_timeout("generate", "x" * 2000) == 36_050.0


def test_a_raised_model_load_budget_is_followed(post_timeout, monkeypatch):
    monkeypatch.setenv("OMNIVOICE_MODEL_LOAD_TIMEOUT", "2000")
    assert post_timeout("generate", "short") == 36_850.0


@pytest.mark.parametrize("primary", [None, "", "10000"])
def test_progress_extension_cap_honors_the_legacy_alias(post_timeout, monkeypatch, primary):
    monkeypatch.setenv("OMNIVOICE_CPU_GENERATE_TIMEOUT_S", "900")
    monkeypatch.setenv("OMNIVOICE_MODEL_LOAD_TIMEOUT_S", "20000")
    if primary is not None:
        monkeypatch.setenv("OMNIVOICE_PROGRESS_EXTENSION_CAP_S", primary)
    cap = 10000.0 if primary else 20000.0
    assert post_timeout("generate", "short") == _generate_wait(
        900.0, extension_cap=cap, reference_base=900.0,
    )


def test_transcriptless_clone_covers_both_serial_jobs(post_timeout):
    # Fail-before: the one-job wait was 31,850 s, but reference ASR and
    # synthesis can together consume 36,000 s under their separate guards.
    reference = QUEUE + 600.0 + 1800.0
    synthesis = QUEUE + 7200.0 + 21_600.0
    assert post_timeout("generate", "x" * 2000) > 1200.0 + reference + synthesis


def test_reference_transcription_uses_generation_not_standalone_asr_budget(post_timeout, monkeypatch):
    monkeypatch.setenv("OMNIVOICE_CPU_GENERATE_TIMEOUT_S", "2000")
    monkeypatch.setenv("OMNIVOICE_GPU_QUEUE_TIMEOUT_S", "60")
    monkeypatch.setenv("OMNIVOICE_PROGRESS_EXTENSION_CAP_S", "10000")
    monkeypatch.setenv("OMNIVOICE_ASR_TRANSCRIBE_TIMEOUT_S", "50000")
    assert post_timeout("generate", "short") == _generate_wait(
        2000.0, queue=60.0, extension_cap=10000.0, reference_base=2000.0,
    )


def test_model_load_and_progress_settings_do_not_change_transcribe(post_timeout, monkeypatch):
    for name in ("OMNIVOICE_MODEL_LOAD_TIMEOUT", "OMNIVOICE_PROGRESS_EXTENSION_CAP_S"):
        monkeypatch.setenv(name, "20000")
    assert post_timeout("transcribe") == 300.0 + GRACE


def test_explicit_mcp_timeout_still_wins_over_every_generate_phase(post_timeout, monkeypatch):
    monkeypatch.setenv("OMNIVOICE_MCP_TIMEOUT_S", "45")
    for name in ("OMNIVOICE_MODEL_LOAD_TIMEOUT", "OMNIVOICE_PROGRESS_EXTENSION_CAP_S"):
        monkeypatch.setenv(name, "20000")
    assert post_timeout("generate", "x" * 5000) == 45.0