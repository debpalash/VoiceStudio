"""Pydantic v2 schemas for request/response validation.

Shared across routers — import from here rather than defining inline.
Using ``model_config = ConfigDict(...)`` for Pydantic v2 compat.
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


# ── System ────────────────────────────────────────────────────────────────

class SysinfoResponse(BaseModel):
    """GET /sysinfo"""
    model_config = ConfigDict(extra="allow")

    cpu: float = Field(description="CPU usage percentage (0–100)")
    ram: float = Field(description="Used RAM in GiB")
    total_ram: float = Field(description="Total RAM in GiB")
    vram: float = Field(0.0, description="Used VRAM in GiB")
    gpu_active: bool = Field(False, description="Whether a GPU is actively used")


class SystemInfoResponse(BaseModel):
    """GET /system/info"""
    model_config = ConfigDict(extra="allow")

    app_version: str = ""
    # Effective compute-time budgets (seconds) for one synthesis job — the
    # values services/model_manager.py's GPU_JOB_TIMEOUT_S / CPU_JOB_TIMEOUT_S
    # captured at backend import time (#1787). A value just saved via
    # /system/set-env is NOT reflected here until the next restart.
    generate_timeout_s: float = 300.0
    cpu_generate_timeout_s: float = 600.0
    # True when an external env var (shell, `.env`, Docker, …) is currently
    # shadowing a prefs.json save for this key — see core.prefs.is_env_shadowed.
    generate_timeout_shadowed: bool = False
    cpu_generate_timeout_shadowed: bool = False
    # #1770: the desktop attach handshake's code fingerprint — whatever
    # Tauri set OMNIVOICE_BUILD_FINGERPRINT to when it spawned this process,
    # echoed back verbatim. Blank when unset (dev mode, a manually started
    # backend). See frontend/src-tauri/src/backend.rs::code_fingerprint_is_current.
    code_fingerprint: str = ""
    data_dir: str
    outputs_dir: str
    crash_log_path: str
    idle_timeout_seconds: int
    model_checkpoint: str = "unknown"
    asr_model: str = "unknown"
    translate_provider: str = "unknown"
    has_hf_token: bool = False
    # Xet fast-download backend state (FDL-03): {xet_enabled, xet_version, high_performance}
    fast_download: dict | None = None
    device: str = "cpu"
    python: str = ""
    platform: str = ""
    arch: str = ""
    os_version: str = ""
    cpu_model: str = ""
    cpu_count: int = 0
    ram_total_gb: float = 0.0
    gpu_name: str = ""
    vram_total_gb: float = 0.0
    disk_free_gb: float = 0.0
    error: str | None = None
    ffmpeg_ok: bool = False
    ffmpeg_path: str = ""
    proxy_url: str = ""
    share_enabled: bool = False
    share_port: int | None = None
    lan_addresses: list[str] = []
    pin_required: bool = False
    backend_port: int = 3900
    share_port_base: int = 3901
    ui_port: int = 3901


class ModelStatusResponse(BaseModel):
    """GET /model/status"""
    model_config = ConfigDict(extra="allow")

    status: str = Field(description="idle | loading | ready")
    checkpoint: str | None = None
    loaded_at: str | None = None
    sub_stage: str | None = Field(None, description="Current loading sub-stage: importing | loading_weights | loading_asr | compiling | ready | error")
    detail: str | None = Field(None, description="Human-readable detail of current loading phase")
    error: str | None = Field(None, description="Error message if loading failed")


class LogsResponse(BaseModel):
    """GET /system/logs"""
    lines: list[str] = Field(default_factory=list)
    path: str = ""
    exists: bool = False
    total_lines: int = 0
    error: str | None = None
    candidates: list[str] | None = None


class FlushMemoryResponse(BaseModel):
    """POST /system/flush-memory"""
    flushed: bool = True
    unloaded_model: bool = False
    ram_after: float = 0.0
    vram_after: float = 0.0


# ── Setup ─────────────────────────────────────────────────────────────────

class MissingModel(BaseModel):
    repo_id: str
    label: str


class SetupStatusResponse(BaseModel):
    """GET /setup/status"""
    models_ready: bool
    missing: list[MissingModel] = Field(default_factory=list)
    hf_cache_dir: str
    disk_free_gb: float
    min_free_gb: int = 10
    enough_disk: bool = True


class PreflightCheck(BaseModel):
    """One check in the preflight report."""
    model_config = ConfigDict(extra="allow")

    id: str
    label: str
    status: str = Field(description="pass | warn | fail")
    detail: str = ""
    fix: str | None = None


class DeviceInfo(BaseModel):
    """GPU/system device info from preflight."""
    model_config = ConfigDict(extra="allow")

    os: str
    arch: str
    gpu_vendor: str = "none"
    gpu_backend: str = "cpu"
    gpu_available: bool = False
    gpu_driver: str | None = None
    gpu_device_name: str | None = None
    # From the canonical device probe (core.device_caps) — distinguishes ROCm
    # from CUDA, unlike the legacy nvidia-smi-based gpu_vendor/gpu_backend.
    gpu_family: str = "cpu"
    vram_gb: float = 0.0
    ram_gb: float = 0.0
    disk_free_gb: float = 0.0


class GpuRouting(BaseModel):
    """Routing verdict for the active TTS engine on THIS host (#21).

    Distinct from the per-engine `routing_*` keys in `/engines`: this is the
    single verdict for the *currently-selected* engine, surfaced in preflight +
    diagnose so the user hears about a CPU fallback / unavailable GPU before a
    slow or failed synth — no silent CPU fallback.
    """
    model_config = ConfigDict(extra="allow")

    engine: str | None = None            # active TTS engine id
    effective_device: str | None = None  # device it will actually use here
    routing_status: str | None = None    # accelerated|cpu_fallback|cpu_only|unavailable|none
    routing_reason: str | None = None    # scrubbed; null when none
    host_family: str = "cpu"             # detect_host_caps().family
    vram_gb: float = 0.0


class PreflightResponse(BaseModel):
    """GET /setup/preflight"""
    ok: bool
    has_warnings: bool = False
    checks: list[PreflightCheck] = Field(default_factory=list)
    device: DeviceInfo
    # Explicit field (PreflightResponse has no extra="allow") so the verdict
    # survives serialization instead of being silently dropped.
    gpu_routing: GpuRouting | None = None
    # Media-engine verdict (ffmpeg/ffprobe) — NOT a check row: an internal
    # dependency the app provisions for itself. Shape: {ready, acquire:
    # {state, progress, error}}. The wizard renders a quiet progress line /
    # failure card from it instead of "install ffmpeg" system requirements.
    media_tools: dict | None = None


class InstallModelRequest(BaseModel):
    """POST /models/install"""
    repo_id: str


class DeleteModelResponse(BaseModel):
    """DELETE /models/{repo_id}"""
    deleted: bool = True
    repo_id: str
    freed_bytes: int = 0


# ── Models list ───────────────────────────────────────────────────────────

class ModelEntry(BaseModel):
    """One model in the GET /models response."""
    model_config = ConfigDict(extra="allow")

    repo_id: str
    label: str
    role: str
    size: str = ""
    required: bool = False
    installed: bool = False
    supported: bool = True
    size_on_disk: int | None = None
    nb_files: int | None = None


# ── Effect presets ─────────────────────────────────────────────────────

class EffectPresetEntry(BaseModel):
    """One DSP effect preset."""
    model_config = ConfigDict(extra="allow")

    id: str
    label: str
    icon: str
    description: str


class EffectPresetsResponse(BaseModel):
    """GET /engines/effects/presets"""
    presets: list[EffectPresetEntry] = Field(default_factory=list)
