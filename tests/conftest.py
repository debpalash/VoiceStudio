import os
import sys
import tempfile
import time

# Backend runs with `--app-dir backend`, so tests must do the same.
_BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend"))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)


# ── Hermetic app state (issue #878) ────────────────────────────────────────
# Tests must never read or write the developer's real app state. Without
# this, `core.config.DATA_DIR` resolves to the real per-user data dir
# (~/Library/Application Support/OmniVoice, %APPDATA%\OmniVoice, ~/.omnivoice)
# so prefs.json / omnivoice.db writes made by tests land in — and leak from —
# the developer's actual install, and a dev who used the app sees LLM tests
# fail that pass on clean CI. Redirecting here (before pytest imports any
# test module, which is what freezes DATA_DIR at `core.config` import time)
# makes every local run behave like a clean CI runner. `setdefault` semantics:
# an explicitly exported OMNIVOICE_DATA_DIR still wins.
if not os.environ.get("OMNIVOICE_DATA_DIR"):
    os.environ["OMNIVOICE_DATA_DIR"] = tempfile.mkdtemp(prefix="omnivoice-test-data-")
# Same story for the durable per-user env file (~/.config/omnivoice/env):
# `main.py` loads it with override=True at import, so a TestClient importing
# the app mid-suite would inject the developer's real TRANSLATE_* / key vars
# into this process. `core.user_env` resolves OMNIVOICE_ENV_FILE at call
# time, so pointing it into the throwaway data dir neutralizes both the
# load and any test that writes user-env without stubbing.
if not os.environ.get("OMNIVOICE_ENV_FILE"):
    os.environ["OMNIVOICE_ENV_FILE"] = os.path.join(
        os.environ["OMNIVOICE_DATA_DIR"], "user-env"
    )
# The TTS checkpoint sentinel, suite-wide. Individual modules used to opt in
# (`OMNIVOICE_MODEL=test`), but any module that boots the real app lifespan
# (`with TestClient(main.app)`) without it lets `preload_model()` resolve the
# real k2-fsa/OmniVoice checkpoint — on a networked machine with an empty HF
# cache that meant a silent multi-GB background download mid-suite. The
# sentinel is honored verbatim by `resolve_omnivoice_checkpoint()` (never
# self-healed to the real default), so no test can trigger a real model
# download/load without explicitly overriding OMNIVOICE_MODEL. Unconditional
# on purpose (#1175 review): a `setdefault` preserved an ambient
# OMNIVOICE_MODEL from the dev's shell, silently re-enabling exactly the
# real-checkpoint resolution this sentinel exists to prevent; tests that
# need a different value monkeypatch it explicitly.
os.environ["OMNIVOICE_MODEL"] = "test"

# Background warm-ups must not fire mid-suite: many tests boot the app
# lifespan via TestClient, and any that exits without a lifespan shutdown
# leaves the deferred preload task pending — 35s later (mid-suite, in
# another thread) it loads real models and mutates watermark module state
# under whatever test happens to be running (seen as a CI-only flake in the
# prefetch cold-start tests). Unconditional: a stray export from the runner
# shell must not re-enable it; a test that wants the warm-up monkeypatches.
os.environ["OMNIVOICE_PRELOAD_WATERMARK"] = "0"


# ── Test fixtures ──────────────────────────────────────────────────────────


import pytest
import warnings as _warnings


# ── Persisted user settings: per-test isolation ────────────────────────────
# The hermetic data dir above is per SESSION, so prefs.json and the user-env
# file are shared by every test in the run. Anything a test persists there —
# directly, or as a side effect such as the app lifespan's startup
# `reconcile_active_profile()` — silently changes what every later test
# resolves. That lifespan pass picks models from the developer's real HF cache
# and persists them (`dictation.model_id`, `asr_backend`, …), so a dev with a
# sherpa dictation model installed saw later capture-WebSocket tests routed to
# the sherpa handler, while CI (empty cache, nothing to persist) stayed green.
# Restoring the files byte-for-byte at every test and module boundary closes
# the whole class — including leaks from module-scoped fixtures — without each
# test having to redirect `prefs._PREFS_PATH` itself. The paths are frozen
# here, not read from `core.prefs`, so a test that re-imports or repoints the
# module cannot move the store this guard protects.
_PERSISTED_SETTINGS_FILES = (
    os.path.join(os.environ["OMNIVOICE_DATA_DIR"], "prefs.json"),
    os.environ["OMNIVOICE_ENV_FILE"],
)


def _settings_files_snapshot() -> dict:
    snapshot = {}
    for path in _PERSISTED_SETTINGS_FILES:
        try:
            with open(path, "rb") as handle:
                snapshot[path] = handle.read()
        except FileNotFoundError:
            snapshot[path] = None
    return snapshot


def _settings_files_restore(snapshot: dict) -> None:
    # `core.prefs` freezes its path at first import. When that first import
    # happens inside a test that aimed OMNIVOICE_DATA_DIR at its tmp_path
    # (the reload-fixture shape), the rest of the session would read and
    # write prefs in that test's directory, out of this guard's reach. Test
    # monkeypatches are already undone here, so any other value is a leak.
    prefs = sys.modules.get("core.prefs")
    if prefs is not None and getattr(prefs, "_PREFS_PATH", None) != _PERSISTED_SETTINGS_FILES[0]:
        prefs._PREFS_PATH = _PERSISTED_SETTINGS_FILES[0]
    for path, before in snapshot.items():
        try:
            with open(path, "rb") as handle:
                after = handle.read()
        except FileNotFoundError:
            after = None
        if after == before:
            continue
        if before is None:
            os.remove(path)
            continue
        tmp = f"{path}.conftest-restore"
        with open(tmp, "wb") as handle:
            handle.write(before)
        os.replace(tmp, path)


@pytest.fixture(scope="module", autouse=True)
def _isolate_persisted_settings_per_module():
    snapshot = _settings_files_snapshot()
    yield
    _settings_files_restore(snapshot)


@pytest.fixture(autouse=True)
def _isolate_persisted_settings():
    snapshot = _settings_files_snapshot()
    yield
    _settings_files_restore(snapshot)


# App lifespans deliberately close watermark admission during shutdown. The
# test process then keeps running and many route tests call handlers without
# starting another lifespan, so restore the equivalent of a fresh lifespan at
# every test boundary. ``begin_watermark_pool_lifecycle`` still refuses to
# reopen while a timed-out worker is genuinely alive, preserving that race
# signal instead of hiding it.
@pytest.fixture(autouse=True)
def _watermark_pool_lifecycle_baseline():
    model_manager = sys.modules.get("services.model_manager")
    begin = getattr(model_manager, "begin_watermark_pool_lifecycle", None)
    if callable(begin):
        begin()
    yield


# ── torch default-dtype isolation (CI flaky trio) ───────────────────────────
# Three tests (test_effects_chain / test_generation_audio_guard /
# test_persona_bundle) fail intermittently on CI — never locally — with
# signatures that all trace to one cause: a leaked
# `torch.set_default_dtype(torch.float16)` from some earlier test. The
# smoking gun is test_generation_audio_guard's observed value
# 0.0999755859375, which is exactly float16(0.1): `torch.tensor([0.1, …])`
# built under a leaked fp16 default. The same leak collapses
# test_effects_chain's preset differences into identical quantized outputs,
# and hands test_persona_bundle's soundfile writer fp16 data libsndfile
# can't encode. The known polluter TESTS are
# test_dub_onsets_route.py::test_prefers_vocals_over_mix and
# test_smart_fit_generate.py::test_final_dub_track_and_seg_wav_are_watermarked
# — both now carry the opt-in `torch_dtype_isolation` fixture below, so this
# autouse guard is pure insurance for new polluters. The CALL that flips the
# dtype only executes on CI-Linux (it never reproduces on macOS — local
# instrumentation of torch.set_default_dtype across both tests recorded zero
# non-fp32 sets), so the recorder below captures the setter's stack trace and
# both fixtures print it when they fire: the next CI occurrence hands us the
# exact culprit call chain, not just the test nodeid.

_DTYPE_SETTER = {"stack": None, "dtype": None}


def _install_torch_dtype_recorder():
    """Wrap torch's default-dtype setters to capture the caller's stack.

    Only records on a *non-float32* set (the rare, offending case), so the
    overhead on the hot path is one dtype comparison. Installed lazily the
    first time torch shows up in sys.modules; idempotent. If torch is first
    imported inside the polluting test itself, the recorder installs after
    the fact and the warning says the stack wasn't captured.
    """
    torch = sys.modules.get("torch")
    if torch is None or getattr(torch, "_omnivoice_dtype_recorder", False):
        return

    import traceback

    _orig_set_dtype = torch.set_default_dtype

    def _recording_set_default_dtype(d):
        if d != torch.float32:
            _DTYPE_SETTER["stack"] = "".join(traceback.format_stack(limit=30))
            _DTYPE_SETTER["dtype"] = repr(d)
        return _orig_set_dtype(d)

    torch.set_default_dtype = _recording_set_default_dtype

    # Legacy API — can also flip the default dtype (e.g. HalfTensor).
    _orig_set_tt = torch.set_default_tensor_type
    if _orig_set_tt is not None:  # removed in newer torch

        def _recording_set_default_tensor_type(t):
            _DTYPE_SETTER["stack"] = "".join(traceback.format_stack(limit=30))
            _DTYPE_SETTER["dtype"] = f"tensor_type={t!r}"
            return _orig_set_tt(t)

        torch.set_default_tensor_type = _recording_set_default_tensor_type

    torch._omnivoice_dtype_recorder = True


def _drain_leaked_dtype(nodeid: str) -> None:
    """Warn (with the captured setter stack, if any) and reset to float32."""
    torch = sys.modules.get("torch")
    # A test may have stubbed sys.modules["torch"] with a bare namespace
    # (test_torch_compile_gate), and fixture teardown ordering can run this
    # guard before that stub is undone — a stub can't leak a dtype, skip it.
    if torch is None or not hasattr(torch, "get_default_dtype"):
        return
    if torch.get_default_dtype() is torch.float32:
        return
    stack = _DTYPE_SETTER["stack"]
    origin = (
        f" set to {_DTYPE_SETTER['dtype']} at:\n{stack}"
        if stack
        else (
            " (setter stack not captured — torch.set_default_dtype was "
            "called before the recorder installed, or the dtype changed "
            "through another API)"
        )
    )
    _warnings.warn(
        f"{nodeid} leaked torch default dtype {torch.get_default_dtype()} — "
        f"resetting to float32.{origin}",
        stacklevel=1,
    )
    torch.set_default_dtype(torch.float32)


@pytest.fixture(autouse=True)
def _torch_default_dtype_guard(request):
    _install_torch_dtype_recorder()
    yield
    # The test itself may have been the first to import torch.
    _install_torch_dtype_recorder()
    _drain_leaked_dtype(request.node.nodeid)


@pytest.fixture
def asr_model_installed(monkeypatch, request):
    """Neutralize the no-ASR-installed preflight (asr_model_missing_error →
    None) for tests that exercise batch/dub/dictation/clone-ref *mechanics*
    and assume ASR weights are present. The hermetic test env has no HF model
    cache, so without this every ASR consumer answers the typed 409/SSE/WS
    ``asr_model_missing`` payload before the code under test even runs. The
    preflight itself has its own suite (tests/test_asr_model_missing.py).
    Every consumer resolves the helper off ``services.asr_backend`` at call
    time, so patching the module covers them all. Opt in per module with
    ``pytestmark = pytest.mark.usefixtures("asr_model_installed")``.

    Patches BOTH the freshly imported module and any module-typed alias the
    test module itself holds (``import services.asr_backend as ab`` at top
    level): in a full-suite run an earlier test can purge ``services.*`` from
    sys.modules, leaving the test module's alias pointing at a STALE pre-purge
    module object — code invoked through that alias resolves the preflight in
    the stale module's globals, which a single sys.modules-based setattr would
    miss (the CI-only empty-HF-cache failure mode). Never patch by name
    string alone here. (Same fixture exists in backend/tests/conftest.py.)"""
    import types

    from services import asr_backend

    targets = {id(asr_backend): asr_backend}
    test_module = getattr(request, "module", None)
    if test_module is not None:
        for val in vars(test_module).values():
            if (isinstance(val, types.ModuleType)
                    and getattr(val, "__name__", "") == "services.asr_backend"):
                targets[id(val)] = val
    for mod in targets.values():
        monkeypatch.setattr(mod, "asr_model_missing_error", lambda **_kw: None)


@pytest.fixture(autouse=True)
def _clear_asr_installed_memo(request):
    """The ASR preflight memoizes installed-POSITIVE repos process-wide
    (services.asr_backend._INSTALLED_REPO_MEMO) so dictation stops paying a
    scan_cache_dir walk per utterance. Tests stub ``is_cached`` both ways, so
    a positive memoized under one test's stub (or from a dev machine's real
    HF cache) must never leak into the next test's 'missing' expectations.
    Clears the canonical module AND any module-typed alias the test module
    holds (``import services.asr_backend as ab``): after a sys.modules purge
    the alias points at a STALE module object with its own memo. Touches the
    memo only when the module is already imported — never forces the import.
    (Same guard exists in backend/tests/conftest.py.)"""
    def _clear_all():
        import types
        mod = sys.modules.get("services.asr_backend")
        targets = {} if mod is None else {id(mod): mod}
        test_module = getattr(request, "module", None)
        if test_module is not None:
            for val in vars(test_module).values():
                if (isinstance(val, types.ModuleType)
                        and getattr(val, "__name__", "") == "services.asr_backend"):
                    targets[id(val)] = val
        for m in targets.values():
            getattr(m, "_INSTALLED_REPO_MEMO", set()).clear()

    _clear_all()
    yield
    _clear_all()


@pytest.fixture
def torch_dtype_isolation(request):
    """Opt-in save/restore for tests known to trip the CI-Linux fp16 leak.

    Runs *inside* the test's own fixture stack (i.e. before the autouse
    guard's teardown), so tagged tests can never spread a leaked default
    dtype — and the warning below keeps CI attribution alive: it prints the
    recorded setter stack so the culprit call chain lands in the CI log.
    """
    _install_torch_dtype_recorder()
    yield
    _install_torch_dtype_recorder()
    _drain_leaked_dtype(request.node.nodeid)


# ── LLM-provider state isolation (issue #878) ──────────────────────────────
# LLM provider selection is process-global three ways: env vars (the
# resolution roots for llm_providers/llm_backend, and `main.py` import loads
# .env files straight into os.environ), the SQLite settings store
# (llm.active_provider / llm.base_url.* / encrypted llm_key.* secrets), and
# prefs.json (llm_backend pick, env.TRANSLATE_* persistence). Any test that
# mutates one of these without teardown — or merely imports `main` — used to
# change what *later* tests' `active_backend_id()` / `active_provider_id()`
# resolved to (order-dependent failures in test_engines.py,
# test_llm_endpoint_settings.py, test_llm_providers.py). The autouse guard
# below snapshots the env vars and settings rows before every test and
# restores them exactly afterwards; prefs.json is restored whole by
# `_isolate_persisted_settings` above.

# Env vars that are NOT declared on a Provider entry but still steer LLM /
# translation resolution.
_LLM_ENV_EXTRAS = (
    "LLM_DEFAULT_PROVIDER",    # llm_providers.active_provider_id() override
    "OMNIVOICE_LLM_BACKEND",   # llm_backend.active_backend_id() override
    "OMNIVOICE_LLM_TIMEOUT",
    "TRANSLATE_PROVIDER",      # dub translate default provider
    "TRANSLATE_BASE_URL",
    "TRANSLATE_API_KEY",
    "TRANSLATE_MODEL",
)

_llm_env_names_cache: tuple = ()


def _llm_env_names() -> tuple:
    """Every env var the LLM-provider registry resolves through.

    Derived from `services.llm_providers._PROVIDERS` so a newly added
    provider is guarded automatically. Falls back to the static extras if
    the import is unavailable (e.g. sys.modules stubbed by tests/backend/**);
    only a successful full derivation is cached.
    """
    global _llm_env_names_cache
    if _llm_env_names_cache:
        return _llm_env_names_cache
    names = set(_LLM_ENV_EXTRAS)
    try:
        from services import llm_providers
        for p in llm_providers.all_providers():
            names.update(p.key_envs)
            for n in (p.base_url_env, p.model_env, p.account_env):
                if n:
                    names.add(n)
    except Exception:
        return tuple(sorted(names))  # degraded, uncached — retry next test
    _llm_env_names_cache = tuple(sorted(names))
    return _llm_env_names_cache


_LLM_STORE_SQL = (
    "SELECT key, value FROM settings "
    "WHERE key LIKE 'llm.%' OR key LIKE 'secret.llm_key.%'"
)


def _llm_store_snapshot() -> dict:
    """Raw llm.* / secret.llm_key.* rows (ciphertext included — no decrypt)."""
    try:
        from core.db import db_conn
        with db_conn() as conn:
            return {k: v for k, v in conn.execute(_LLM_STORE_SQL).fetchall()}
    except Exception:
        # Missing settings table / stubbed core.* — nothing to snapshot.
        return {}


def _llm_store_restore(before: dict) -> None:
    try:
        from core.db import db_conn
        with db_conn() as conn:
            after = {k: v for k, v in conn.execute(_LLM_STORE_SQL).fetchall()}
            if after == before:
                return
            for k in after.keys() - before.keys():
                conn.execute("DELETE FROM settings WHERE key = ?", (k,))
            for k, v in before.items():
                if after.get(k) != v:
                    conn.execute(
                        "INSERT OR REPLACE INTO settings(key, value, updated_at) "
                        "VALUES (?, ?, ?)",
                        (k, v, time.time()),
                    )
    except Exception:
        pass  # table never existed during the test → nothing leaked


# ── HF endpoint probes: no real network, ever ───────────────────────────────
# services.endpoint_race probes huggingface.co / hf-mirror.com over HTTPS.
# Several suites reach it indirectly (/setup/preflight forces a race, the
# model-cache repair ladder failovers on "connection reset"-class errors), so
# without a suite-wide stub any of those tests would hit the real network —
# slow offline, flaky on CI. Deterministic default: canonical reachable and
# fastest. Tests that need other outcomes monkeypatch over this (their patch
# is applied later, so it wins).
@pytest.fixture(autouse=True)
def _no_real_endpoint_probes():
    # Deliberately NOT the shared `monkeypatch` fixture: requesting it from an
    # autouse fixture hoists its setup earlier for every test, which reorders
    # teardown against other autouse guards (it broke the dtype guard vs
    # test_torch_compile_gate's torch stub). An isolated MonkeyPatch leaves
    # the shared fixture's position untouched.
    from services import endpoint_race as _er

    def _fake_probe(endpoint, timeout=None):
        return _er.ProbeResult(
            endpoint=endpoint,
            reachable=True,
            latency_ms=50.0 if endpoint == _er.CANONICAL_ENDPOINT else 80.0,
        )

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(_er, "probe_endpoint", _fake_probe)
        mp.setattr(_er, "throughput_probe", lambda endpoint, timeout=None: None)
        yield
    # The decision cache lives in prefs; `_isolate_persisted_settings` restores
    # it, so one test's auto pick cannot leak into another's preflight.


@pytest.fixture(autouse=True)
def _isolate_llm_provider_state():
    """Snapshot/restore the three global LLM-provider state surfaces per test."""
    names = _llm_env_names()
    env_before = {n: os.environ.get(n) for n in names}
    store_before = _llm_store_snapshot()
    yield
    for n, v in env_before.items():
        if os.environ.get(n) != v:
            if v is None:
                os.environ.pop(n, None)
            else:
                os.environ[n] = v
    _llm_store_restore(store_before)


@pytest.fixture
def clean_llm_env(monkeypatch):
    """Delete every LLM-provider env var for the duration of a test.

    For tests that assert on the *unconfigured* state (auto-select 'off',
    empty endpoint settings, provider precedence): ambient shell exports or
    a `.env` loaded by an earlier `main` import must not read as
    'something configured'. Restoration is monkeypatch's.
    """
    for name in _llm_env_names():
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def mock_settings_store(monkeypatch):
    """In-memory replacement for ``services.settings_store`` license helpers.

    Phase 3 Plan 03-01 / Wave 0 gap: the real settings_store talks to
    SQLite via ``core.db.db_conn()``; that opens the project SQLite
    file as a side effect of the import. Tests that exercise
    ``Supertonic3Backend.is_available()`` shouldn't need the SQLite
    plumbing online ‑‑ they just need a controllable
    ``get_license_accepted`` / ``set_license_accepted`` pair.

    Yields a dict ``{engine_id: bool}`` so tests can pre-seed
    acceptance state or assert on what got written. The dict is
    re-bound to the monkeypatched helpers on every read/write so a
    test can mutate it directly to simulate "user clicked Accept".
    """
    state: dict[str, bool] = {}

    def fake_get(engine_id: str) -> bool:
        return bool(state.get(engine_id, False))

    def fake_set(engine_id: str, accepted: bool) -> None:
        state[engine_id] = bool(accepted)

    # Patch the canonical module so any importer (Supertonic3Backend,
    # api.routers.settings, etc.) sees the fakes. Using setattr+
    # monkeypatch lets pytest restore the originals between tests.
    from services import settings_store as _ss

    monkeypatch.setattr(_ss, "get_license_accepted", fake_get)
    monkeypatch.setattr(_ss, "set_license_accepted", fake_set)
    return state


# ── #1269: config drift from importlib.reload leaks across suites ────────────
#
# Ten test modules share a fixture shape: monkeypatch OMNIVOICE_DATA_DIR to a
# tmp_path, then importlib.reload(core.config) (plus core.db, a router, main)
# so the app rebinds its paths under the temp dir. monkeypatch faithfully
# restores the ENV VAR at teardown — and nothing reloads the modules back, so
# every path constant keeps pointing at that test's tmp_path for the rest of
# the session.
#
# The damage lands on whoever runs next. In a combined `pytest tests/
# backend/tests/` run this produced three different answers to "where is the
# voices directory":
#
#   OMNIVOICE_DATA_DIR      .../omnivoice-test-data-vna0ywre        (correct)
#   core.config.VOICES_DIR  .../test_fitted_srt_last_cue_withi0/…   (leaked)
#   profiles.VOICES_DIR     .../test_clone_profile_save_saniti0/…   (leaked)
#
# — which is why the personas import tests wrote a file to one directory and
# then asserted it existed in another (4 failures, #1269).
#
# Restoring by re-reloading would re-register FastAPI routes and rebuild
# module state as a side effect. Snapshot/restore of the constants themselves
# is inert: a plain setattr, only for values a test actually changed, and it
# fixes all ten modules without editing any of them. New reload fixtures are
# covered automatically.
_CONFIG_PATH_CONSTANTS = (
    "DATA_DIR", "VOICES_DIR", "OUTPUTS_DIR", "DUB_DIR", "DB_PATH",
    "PREVIEW_DIR", "CRASH_LOG_PATH", "LOG_PATH",
)


@pytest.fixture(scope="module", autouse=True)
def _restore_config_paths_after_reload():
    """Undo cross-MODULE leakage of core.config's path constants.

    Module scope, not function scope, and that boundary is the whole design.

    Deliberate re-pointing is normal and must survive: tests/smoke/
    test_boot_smoke.py has a module-scoped fixture that aims core.config at a
    frozen fixture directory for the length of that file. A per-test restore
    reset it between that module's own tests and broke it — the fixture cannot
    tell a deliberate setup from a leak *within* a module.

    Across modules there is no such ambiguity: whatever a module pointed
    core.config at is that module's business, and the next one is entitled to
    the paths it started with. Restoring at module teardown lets each file keep
    its own arrangement and hands the next file a clean slate.
    """
    import sys

    def _snapshot():
        # IMPORT rather than only reading sys.modules. If this module is the
        # first to import core.config, a sys.modules-only probe returns {} —
        # and then the empty-snapshot guard below skips restoration entirely,
        # so the very module most likely to reload config is the one least
        # protected (Greptile P1). Importing is cheap and idempotent, and
        # tests/conftest.py has already pointed OMNIVOICE_DATA_DIR at a
        # throwaway dir by the time any fixture runs, so the values are right.
        try:
            import core.config as cfg  # noqa: PLC0415
        except Exception:
            return {}
        return {
            c: getattr(cfg, c)
            for c in _CONFIG_PATH_CONSTANTS
            if isinstance(getattr(cfg, c, None), str)
        }

    before = _snapshot()
    try:
        yield
    finally:
        cfg = sys.modules.get("core.config")
        if cfg is None or not before:
            return
        # 1. core.config itself back to what this module inherited.
        for const, value in before.items():
            if getattr(cfg, const, None) != value:
                setattr(cfg, const, value)
        # 2. Re-sync every module that copied a value out of it. A reload
        #    fixture typically imports the router under test for the first
        #    time, so it has no earlier value to restore — which is how
        #    api.routers.profiles kept a tmp_path VOICES_DIR while core.config
        #    was already correct.
        for name, mod in list(sys.modules.items()):
            if mod is None or not name.startswith(("api.routers.", "services.", "core.")):
                continue
            for const, value in before.items():
                if isinstance(getattr(mod, const, None), str) and getattr(mod, const) != value:
                    setattr(mod, const, value)


# ── Symlinks on a stock Windows checkout ───────────────────────────────────
# Creating a symlink on Windows needs SeCreateSymbolicLinkPrivilege, which a
# normal user account does not hold unless Developer Mode is on. Hosted CI
# runs elevated, so unguarded `Path.symlink_to` / `os.symlink` calls pass
# there and hand a Windows contributor a suite that fails on their machine
# for reasons that have nothing to do with their change (WinError 1314). Tests
# that need a real symlink take this fixture, so the environment that cannot
# make one skips instead of erroring. Coverage still holds: the full pytest
# job runs on Linux.
@pytest.fixture
def symlink_or_skip():
    def _make(link, target, *, target_is_directory: bool = False):
        try:
            os.symlink(target, link, target_is_directory=target_is_directory)
        except (OSError, NotImplementedError) as exc:  # pragma: no cover - Windows-only
            pytest.skip(f"symlinks unavailable in this environment: {exc}")

    return _make
