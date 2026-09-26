# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for VoiceStudio backend.
#
# Produces a one-folder bundle at dist/omnivoice-backend/ that Tauri launches
# as a sidecar binary. Kept intentionally permissive with collect_all(...)
# on the heavy ML deps because PyInstaller's static analysis misses their
# runtime-imported submodules, C extensions, and data files.
#
# Cross-platform: targets mac-ARM, mac-Intel, Linux x64, Windows x64. mlx
# deps are gated on mac-ARM (sys.platform=='darwin' + machine=='arm64') so
# PyInstaller on other hosts doesn't blow up trying to find mlx wheels.
#
# Run:  uv run pyinstaller backend.spec --noconfirm --clean
import platform
import sys
from PyInstaller.utils.hooks import collect_data_files, collect_all, collect_submodules, copy_metadata

IS_MAC_ARM = sys.platform == "darwin" and platform.machine() == "arm64"

datas = []
binaries = []

# Bundle the omnivoice package's .dist-info so importlib.metadata.version()
# resolves inside the frozen build. Without it the backend can't read its own
# version and falls back to the literal in backend/core/version.py — which is
# how a 0.3.6 desktop build shipped reporting "0.3.5" in About + bug reports.
datas += copy_metadata('omnivoice')
hiddenimports = [
    # Web stack
    'uvicorn', 'uvicorn.logging', 'uvicorn.loops', 'uvicorn.loops.auto',
    'uvicorn.protocols', 'uvicorn.protocols.http', 'uvicorn.protocols.http.auto',
    'uvicorn.protocols.websockets', 'uvicorn.protocols.websockets.auto',
    'uvicorn.lifespan', 'uvicorn.lifespan.on',
    'fastapi', 'fastapi.responses', 'starlette',
    'multipart',
    # SOCKS proxy support (#959). httpx imports socksio lazily inside a
    # try/except (only when a socks5:// proxy env var is set), so
    # PyInstaller's static tracer never sees it — without this entry the
    # frozen installers keep raising "Using SOCKS proxy, but the 'socksio'
    # package is not installed" on every model load under a SOCKS proxy,
    # even though pyproject.toml ships the package. Guarded by
    # tests/test_socks_proxy.py.
    'socksio',
    # Remote GPU workers (backend/worker/). The feature is opt-in, so every
    # import of it is deliberately deferred to the moment it is switched on —
    # inside `lifespan` and inside `ControlPlane.start()`. That keeps the cost
    # off users who never enable it, but it also means a frozen build has no
    # static import chain to follow, so the modules must be named here or the
    # feature raises ModuleNotFoundError only in the installers.
    'grpc', 'grpc.aio',
    'worker.service', 'worker.agent',
    'worker.transport.server', 'worker.transport.client',
    'worker.protocol.gen.worker_v1_pb2', 'worker.protocol.gen.worker_v1_pb2_grpc',

    # Core
    'uuid', 'asyncio',

    # Audio / ML
    'torch', 'torchaudio', 'soundfile', 'scipy', 'numpy',
    'numpy.random._pickle',

    # Cross-platform primary ASR — WhisperX (faster-whisper + wav2vec2
    # alignment) is the default on every platform. faster-whisper is the
    # transcription engine; WhisperX adds forced alignment for ±10-30 ms
    # word timing, which directly improves dub lip-sync. Both backends are
    # registered in asr_backend.py; the user can switch via Settings.
    'whisperx', 'whisperx.alignment', 'whisperx.asr', 'whisperx.diarize',
    'whisperx.vad', 'whisperx.audio', 'whisperx.utils',
    'faster_whisper', 'faster_whisper.transcribe', 'faster_whisper.audio',
    'faster_whisper.utils', 'faster_whisper.tokenizer', 'faster_whisper.vad',
    'ctranslate2',

    # Lightweight English TTS tier — ONNX-based, cross-platform. The ONNX
    # Runtime wheels ship platform-specific .so/.dll/.dylib which collect_all
    # picks up; the kittentts Python package is pure Python but has a couple
    # of asset files the bundler needs to include.
    'kittentts', 'onnxruntime',

    # Pipeline
    'yt_dlp', 'demucs', 'demucs.separate',

    # Numbers→words for the pre-TTS text normalization pass
    # (services/text_normalization.py). Imported inside a function (lazy),
    # so pin it explicitly rather than trusting the tracer.
    'num2words',

    # OmniVoice's own package
    'omnivoice', 'omnivoice.models', 'omnivoice.models.omnivoice',
]

if IS_MAC_ARM:
    # MLX Whisper on Apple Silicon (optional speedup path). mlx's pure-Python
    # submodules (nn, utils, …) are imported lazily by mlx_whisper at
    # transcribe time and the plain dep tracer misses them. We deliberately
    # do NOT collect_all() mlx because that double-registers mlx.core with
    # nanobind and the binary aborts on the first mlx.core touch.
    hiddenimports.append('mlx_whisper')
    # Parakeet TDT v3 ASR (services.asr_backend.ParakeetMLXBackend) — imported
    # lazily at is_available()/transcribe time, so the tracer misses it. Same
    # rule as mlx_whisper: list the package, never collect_all() anything that
    # touches nanobind-registered mlx.core.
    hiddenimports.append('parakeet_mlx')
    # mlx-audio engine multiplexer — Kokoro / CSM / Dia / Qwen3-TTS /
    # Chatterbox / MeloTTS / OuteTTS / … — gives mac-ARM users a rich
    # engine picker. Like mlx_whisper it's mac-ARM-only; also like
    # mlx_whisper we list it here but avoid collect_all() because it
    # depends on the same nanobind-registered mlx.core.
    hiddenimports += [
        'mlx_audio', 'mlx_audio.tts', 'mlx_audio.tts.utils',
        'mlx_audio.tts.models', 'mlx_audio.tts.generate',
        'mlx_audio.stt', 'mlx_audio.codec',
    ]
    # Kokoro's phonemizer (misaki) loads the spaCy model en_core_web_sm
    # DYNAMICALLY (spacy.load by name), so PyInstaller never sees the import —
    # a frozen build without it would hit misaki's in-process downloader at
    # first English generation (#1133 class; contained since #1143, but the
    # generation still degrades). It's a plain data-heavy package with no
    # nanobind involvement, so collect_all is safe here (unlike mlx itself).
    _sm_datas, _sm_bins, _sm_hidden = collect_all('en_core_web_sm')
    datas += _sm_datas
    binaries += _sm_bins
    hiddenimports += _sm_hidden

# Note: we deliberately DON'T enumerate mlx submodules here. Any variant of
# `collect_submodules('mlx')` or `collect_all('mlx')` — even filtered to
# exclude mlx.core — reliably re-triggers the nanobind duplicate-key error
# the first time anything imports mlx.core ("refusing to add duplicate key
# 'cpu' to enumeration mlx.core.DeviceType"). Shipping without mlx in the
# frozen bundle leaves mlx-whisper unavailable; asr_backend falls back to
# pytorch-whisper (slower but functional on Apple Silicon). Revisit once
# we have a minimal repro or a PyInstaller hook specifically for mlx.

# The nuclear option on heavy ML libs — pull every submodule, C ext, and
# data file. Cost: bigger bundle. Benefit: we don't ship a binary that
# ImportErrors the first time a user hits a code path.
# Note: 'mlx' is intentionally NOT in this list. Calling collect_all('mlx')
# alongside collect_all('mlx_whisper') causes the nanobind binding init to
# run twice in the frozen bundle, crashing with
#   "Critical nanobind error: refusing to add duplicate key 'cpu'
#    to enumeration 'mlx.core.DeviceType'!"
# the first time anything imports mlx.core. mlx_whisper already depends on
# mlx and PyInstaller's dep tracer pulls the needed mlx submodules + the .so.
_collect_pkgs = [
    'torch', 'torchaudio', 'soundfile', 'scipy', 'numpy',
    'omnivoice', 'demucs', 'yt_dlp', 'fastapi', 'uvicorn',
    # Primary cross-platform ASR. collect_all pulls CTranslate2's bundled
    # .so/.dylib/.dll plus its compiled kernel data. WhisperX ships its own
    # pure-Python code + some asset files (e.g. language metadata).
    'whisperx', 'faster_whisper', 'ctranslate2',
    # ONNX-based lightweight TTS. onnxruntime's collect_all pulls the
    # platform-appropriate .so/.dll/.dylib + CUDA providers when present.
    'kittentts', 'onnxruntime',
]
if IS_MAC_ARM:
    # Only attempt mlx_whisper collection on mac-ARM — no wheels exist for
    # Linux/Windows/mac-Intel, so collect_all would fail on CI for those.
    _collect_pkgs.append('mlx_whisper')

for pkg in _collect_pkgs:
    try:
        tmp_datas, tmp_binaries, tmp_hidden = collect_all(pkg)
        datas += tmp_datas
        binaries += tmp_binaries
        hiddenimports += tmp_hidden
    except Exception as e:  # noqa: BLE001
        print(f"[backend.spec] collect_all({pkg!r}) skipped: {e}")

# Include the backend's own modules as data so imports like
# `api.routers.dub_generate` resolve inside the frozen bundle.
datas += [
    ('backend/api', 'api'),
    ('backend/core', 'core'),
    ('backend/services', 'services'),
    ('backend/schemas', 'schemas'),
    ('backend/migrations', 'migrations'),
]

a = Analysis(
    ['backend/main.py'],
    pathex=['backend', '.'],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[
        'backend/hooks/pyi_rth_numpy_compat.py',
        'backend/hooks/pyi_rth_torch_compiler_disable.py',
    ],
    excludes=[
        # Desktop-only bloat the frozen backend never uses.
        'tkinter', 'matplotlib', 'PIL.ImageQt', 'PyQt5', 'PyQt6',
        # CUDA / NVIDIA wheels on every platform — we ship CPU-only inference
        # for the desktop app. Models download on first run via HF cache, and
        # GPU use is surfaced only when a user-installed driver is detected
        # at runtime. Excluding these saves ~2 GB per bundle, which is what
        # keeps Linux .deb / Windows MSI under GH Releases' 2 GB asset cap.
        'nvidia', 'nvidia.cublas', 'nvidia.cudnn', 'nvidia.cuda_runtime',
        'nvidia.cuda_nvrtc', 'nvidia.nccl', 'nvidia.nvtx',
        'nvidia.curand', 'nvidia.cusolver', 'nvidia.cusparse',
        'nvidia.cufft', 'nvidia.cuda_cupti', 'nvidia.cusparselt',
        'nvidia.nvjitlink', 'nvidia.cufile',
        'triton', 'flash_attn',
        # Torch internals we never invoke at inference time — distributed
        # training, compile, FX tracing, tensorboard, testing helpers. These
        # pull hundreds of MB of Python source + transitive deps.
        'torch.distributed', 'torch._dynamo', 'torch._inductor',
        'torch._export', 'torch.testing', 'torch.utils.tensorboard',
        'torch.utils.benchmark', 'torch.fx.experimental',
        'torch._functorch', 'torch.ao', 'torch.onnx',
        # torchaudio prototype / deprecated — nothing in the backend touches
        # these; removing saves tens of MB and silences the deprecation log
        # noise on startup.
        'torchaudio.prototype', 'torchaudio.models.hifigan',
        # Heavy optional deps that are in pyproject.toml but the Studio
        # backend never imports (verified with `grep ^import`). Excluding
        # keeps them out of the frozen bundle; nothing on the runtime path
        # breaks.
        'gradio', 'gradio_client', 'tensorboardX', 'webdataset',
        's3prl', 'funasr', 'pedalboard',
        # Test / example trees that get swept up by collect_all.
        'scipy.special.tests', 'scipy.tests', 'numpy.f2py.tests',
        'numpy.tests', 'numpy.testing.tests',
    ],
    noarchive=False,
    # optimize=2 compiles the embedded stdlib + site-packages with -OO,
    # stripping assert statements + docstrings. Saves ~50-80 MB on a bundle
    # this size. Runtime impact is negligible because we never inspect
    # docstrings at runtime.
    optimize=2,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='omnivoice-backend',
    debug=False,
    bootloader_ignore_signals=False,
    # strip=True removes debug symbols from ELF/Mach-O binaries (no-op on
    # Windows since MSVC doesn't emit symbols in the same way). Saves
    # 10-30% on native libraries like libtorch_cpu.so (~300 MB → ~220 MB).
    strip=True,
    upx=False,              # UPX often corrupts ML native libs — disabled.
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=True,
    upx=False,
    upx_exclude=[],
    name='omnivoice-backend',
)
