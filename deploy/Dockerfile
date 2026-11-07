# Base image for the Python/PyTorch runtime stage. The default builds the
# CUDA variant; CI also builds a ROCm/AMD variant (issue #1165) by overriding:
#   BASE_IMAGE=rocm/pytorch:rocm7.2.4_ubuntu24.04_py3.12_pytorch_release_2.8.0
#   GPU_FLAVOR=rocm
# and an arm64 NVIDIA variant (DGX Spark / Grace, #2831) on a native arm64
# runner, from the cuda-arm64-base stage below:
#   BASE_IMAGE=cuda-arm64-base   (GPU_FLAVOR stays cuda)
# Every base ships torch/torchaudio 2.8.0 preinstalled; the dependency install
# below deliberately preserves them (see the GPU_FLAVOR guard).
ARG BASE_IMAGE=pytorch/pytorch:2.8.0-cuda12.8-cudnn9-runtime

# ==========================================
# Builder Stage: Compile React Frontend
# ==========================================
# Keep in lockstep with package.json packageManager (tests/test_bun_toolchain_pins.py).
FROM oven/bun:1.4.2-alpine AS frontend-builder
WORKDIR /app

# Monorepo — bun workspace with lockfile at repo root. Copy manifests first
# so `bun install` caches independently of source edits.
COPY package.json bun.lock ./
COPY electron/package.json ./electron/
COPY patches/ ./patches/
COPY config/ ./config/

RUN bun install --frozen-lockfile

# Build the maintained Electron renderer as the browser UI. `electron/src/shared`
# contains transitional shared modules only; it has no app entry point.
COPY frontend/ ./frontend/
COPY electron/ ./electron/
RUN bun run --cwd electron build:web

# ==========================================
# arm64 NVIDIA base (#2831): built only when BASE_IMAGE=cuda-arm64-base
# ==========================================
# pytorch/pytorch publishes amd64 images only, so this stage assembles the same
# torch 2.8.0 stack for linux/arm64 (SBSA: DGX Spark / GB10, GH200, GB200).
# PyTorch's cu128 index has no aarch64 2.8.0 build; cu129 has all three wheels
# for cp312. The aarch64 torch wheel bundles its CUDA and cuDNN 9 libraries
# (unlike x86_64, it declares no nvidia-* dependencies), so plain Ubuntu
# suffices; the host driver comes from the NVIDIA Container Toolkit. BuildKit
# skips this stage entirely for the amd64 CUDA and ROCm builds.
#
# Jetson (L4T) is not covered: it needs JetPack's own torch builds.
FROM ubuntu:24.04 AS cuda-arm64-base
ENV DEBIAN_FRONTEND=noninteractive \
    PIP_BREAK_SYSTEM_PACKAGES=1 \
    NVIDIA_VISIBLE_DEVICES=all \
    NVIDIA_DRIVER_CAPABILITIES=compute,utility
# python3-dev + pkg-config/cmake/libopus-dev: sphn (via demucs) and miniaudio
# publish no aarch64 wheels and build from source; sphn's Opus binding needs
# libopus.
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    python3-dev \
    python3-pip \
    pkg-config \
    cmake \
    libopus-dev \
    && rm -rf /var/lib/apt/lists/*
# Same trio as deploy/torch-constraints.txt, from the cu129 index only (no
# PyPI fallback, so no other index can supply a torch build).
RUN python3 -m pip install --no-cache-dir \
    --index-url https://download.pytorch.org/whl/cu129 \
    torch==2.8.0+cu129 torchaudio==2.8.0 torchvision==0.23.0

# ==========================================
# Runtime Stage: Python & PyTorch Backend
# ==========================================
FROM ${BASE_IMAGE} AS runtime
WORKDIR /app

# Enable unbuffered logs and optimizations
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV HF_HOME=/app/omnivoice_data/huggingface
# Allow bare imports (from core.config, from services.*, etc.) when
# uvicorn is started as `backend.main:app` from WORKDIR /app.
ENV PYTHONPATH=/app/backend
# Headless server deployment: relax the desktop-only loopback origin gate.
# Docker's network NAT rewrites the client host to the bridge gateway, so the
# gate would otherwise 403 the operator out of /system/* and /api/settings/*
# ("Loopback origin required", issue #261). Exposure is governed by the
# operator's `-p` port mapping plus the optional share PIN. Desktop builds
# never set this, so their loopback boundary is unchanged.
ENV OMNIVOICE_SERVER_MODE=1

# Install system dependencies (FFmpeg is critical for torchaudio/scene splitting)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    ffmpeg \
    libsndfile1 \
    curl \
    && rm -rf /var/lib/apt/lists/*

# PEP 668: the ROCm base (Ubuntu 24.04) marks its system Python
# EXTERNALLY-MANAGED, which would refuse installing into the selected base
# interpreter. Inside a single-purpose container image that is exactly what
# we want. No-ops on the conda-based CUDA image.
ENV PIP_BREAK_SYSTEM_PACKAGES=1
ENV UV_BREAK_SYSTEM_PACKAGES=1

# Install `uv` for blazing-fast reliable pip resolution
# (`python3 -m pip` — not every base symlinks a bare `pip` onto PATH)
RUN python3 -m pip install --no-cache-dir uv

# Copy python packaging specs (README.md required by hatchling metadata)
COPY pyproject.toml uv.lock README.md ./
COPY deploy/torch-constraints.txt ./deploy/torch-constraints.txt
COPY deploy/uv-overrides.txt ./deploy/uv-overrides.txt
COPY deploy/security-constraints.txt deploy/security_floors.py ./deploy/

# Install the project (non-editable — no need for -e in containers).
# Uses `uv` for exponentially faster resolution than plain pip.
#
# Target the exact interpreter selected by the base image. The ROCm image has
# both /opt/venv/bin/python3 (ROCm torch) and /usr/bin/python (a CUDA-default
# environment); `--system` used the latter while the build guard used the
# former, so a green image launched a CPU-only backend on AMD (#1274).
#
# NOTE: `uv pip install` (without --upgrade) keeps already-installed packages
# that satisfy the requirements, so the base image's GPU-built torch/torchaudio
# (2.8.0, satisfying our `torch>=2.4`) survive this step instead of being
# clobbered by PyPI's CUDA-default wheels. That property is what makes the
# ROCm variant possible at all — the guard below pins it down.
#
# --constraint because `uv pip install` ignores `[tool.uv]
# constraint-dependencies` (that is a project-API setting), so without it the
# trio resolves on its bare lower bounds and torch may move while torchvision
# stays put — an ABI mismatch at import (#1357). The pins carry no local
# segment, so they match the base image's +cu128 / +rocm6.4 builds rather than
# replacing them.
#
# The second --constraint carries the rest of `constraint-dependencies` (the
# security floors), for the same reason: a base image's older compatible
# package would otherwise survive below the floor.
#
# --override: `[tool.uv] override-dependencies` lifts caps that block security
# releases (gradio's `pillow<13`), but `uv pip install` applies it only when run
# from the project directory with a recent uv; pass it explicitly (#3275).
RUN uv pip install --python "$(command -v python3)" --no-cache \
    --constraint deploy/torch-constraints.txt \
    --constraint deploy/security-constraints.txt \
    --override deploy/uv-overrides.txt .

# Guard: every security override must be what the image actually installed.
RUN python3 -c "from importlib.metadata import version; from packaging.requirements import Requirement; \
    reqs = [Requirement(l.strip()) for l in open('deploy/uv-overrides.txt') if l.strip() and not l.lstrip().startswith('#')]; \
    bad = [f'{r.name} {version(r.name)} does not satisfy {r}' for r in reqs if not r.specifier.contains(version(r.name), prereleases=True)]; \
    assert not bad, bad; \
    print('uv overrides applied:', ', '.join(f'{r.name} {version(r.name)}' for r in reqs))"

# Security floors for packages the base image already ships but nothing here
# requires: the install above leaves them alone (msgpack 1.1.1 in
# pytorch/pytorch), so upgrade what is below its floor, then fail the build if
# anything still is. Packages the image does not install are skipped.
RUN floors="$(python3 deploy/security_floors.py requirements)" \
    && if [ -n "$floors" ]; then \
        echo "Raising to security floors: $floors" \
        && uv pip install --python "$(command -v python3)" --no-cache \
            --constraint deploy/torch-constraints.txt \
            --constraint deploy/security-constraints.txt \
            --override deploy/uv-overrides.txt $floors; \
    fi \
    && python3 deploy/security_floors.py check

# Guard (fails the build, not the user at runtime): assert the dependency
# install did NOT replace the base image's GPU torch. A future dep bump that
# forces a different torch version would otherwise silently ship a CUDA build
# in the ROCm image (= CPU-only for AMD users) — catch it here instead.
ARG GPU_FLAVOR=cuda
RUN python3 -c "import os, torch, torchaudio, torchvision; \
    flavor = os.environ['GPU_FLAVOR']; \
    accel = torch.version.hip if flavor == 'rocm' else torch.version.cuda; \
    print(f'torch={torch.__version__} torchaudio={torchaudio.__version__} torchvision={torchvision.__version__} {flavor}={accel}'); \
    assert accel, f'base image {flavor} torch was clobbered (now {torch.__version__})'; \
    import torchvision.ops; torchvision.ops.nms; \
    print('torchvision C++ ops resolve against this torch')"

# CTranslate2 (WhisperX, faster-whisper) is linked against cuDNN 8 while the
# CUDA base image ships cuDNN 9. When libcudnn_ops_infer.so.8 is missing the
# whole backend process aborts instead of raising (#1371). scripts/setup.py
# side-loads cuDNN 8 for source installs (#2050); the image needs the same,
# placed where backend/core/cudnn8.py looks.
# Skipped for ROCm (no cuDNN there) and for aarch64: nvidia-cudnn-cu12==8.9.7.29
# has no aarch64 wheel (DGX Spark / GH200 builds, #2831), so CTranslate2 ASR
# stays unavailable on those images.
RUN if [ "$GPU_FLAVOR" = "cuda" ] && [ "$(uname -m)" != "aarch64" ]; then \
        target="$(python3 -c "import sys; print(f'{sys.prefix}/lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages/cudnn8_compat')")" \
        && uv pip install --python "$(command -v python3)" --no-cache --no-deps \
            --target "$target" nvidia-cudnn-cu12==8.9.7.29 \
        && CUDNN8_TARGET="$target" python3 -c "import glob, os; t = os.environ['CUDNN8_TARGET']; found = [f for f in glob.glob(t + '/nvidia/cudnn/lib/*') if '.so.8' in f]; assert found, 'cuDNN 8 libraries missing from ' + t; print(f'cuDNN 8 compat: {len(found)} libraries in ' + t)"; \
    else \
        echo "cuDNN 8 compat skipped (GPU_FLAVOR=$GPU_FLAVOR, arch=$(uname -m))"; \
    fi
# Copy application source
COPY backend/ ./backend/
COPY omnivoice/ ./omnivoice/
COPY config/ ./config/
# Alembic config so schema migrations run natively on existing volumes
# (without it the backend fell back to the additive-column self-heal —
# functional, but the real migration chain is the first-class path).
COPY alembic.ini ./

# Copy the pre-built React frontend from the builder stage
COPY --from=frontend-builder /app/frontend/dist ./frontend/dist

# Stable container interface for `docker run -P` and published-image consumers.
EXPOSE 3900

# Image-level health probe (compose files define their own; this covers plain
# `docker run`). Generous start period: first boot creates the venv-less
# schema + may pull model metadata before /health answers.
HEALTHCHECK --interval=30s --timeout=5s --start-period=120s --retries=5 \
  CMD ["python3", "backend/docker_entrypoint.py", "--health-check"]

# Mount points for persistent data (sqlite db, user voices, huggingface cache)
VOLUME ["/app/omnivoice_data"]

# Bind to 0.0.0.0 for external access. The entrypoint reads the manifest default
# (or OMNIVOICE_PORT) and forwards any explicit Uvicorn options after the image.
ENV OMNIVOICE_BIND_HOST=0.0.0.0
# Late on purpose: a new commit changes this arg every build, and nothing above may
# be invalidated by it. Shown in bug reports to tell builds of one version apart.
ARG BUILD_COMMIT=unknown
ENV OMNIVOICE_BUILD_COMMIT=${BUILD_COMMIT}
ENTRYPOINT ["python3", "backend/docker_entrypoint.py"]
