# Base image for the Python/PyTorch runtime stage. The default builds the
# CUDA variant; CI also builds a ROCm/AMD variant (issue #1165) by overriding:
#   BASE_IMAGE=rocm/pytorch:rocm7.2.4_ubuntu24.04_py3.12_pytorch_release_2.8.0
#   GPU_FLAVOR=rocm
# Both bases ship torch/torchaudio 2.8.0 preinstalled; the dependency install
# below deliberately preserves them (see the GPU_FLAVOR guard).
ARG BASE_IMAGE=pytorch/pytorch:2.8.0-cuda12.8-cudnn9-runtime

# ==========================================
# Builder Stage: Compile React Frontend
# ==========================================
FROM oven/bun:1-alpine AS frontend-builder
WORKDIR /app

# Monorepo — bun workspace with lockfile at repo root. Copy manifests first
# so `bun install` caches independently of source edits.
COPY package.json bun.lock ./
COPY frontend/package.json ./frontend/

RUN bun install --frozen-lockfile

# Build static files (output lands in /app/frontend/dist)
COPY frontend/ ./frontend/
RUN bun run --cwd frontend build

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
RUN uv pip install --python "$(command -v python3)" --no-cache \
    --constraint deploy/torch-constraints.txt .

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

# CTranslate2 (WhisperX, faster-whisper) links cuDNN 8, but the CUDA base image
# ships cuDNN 9, so libcudnn_ops_infer.so.8 is absent and loading it aborts the
# backend process outright rather than raising (#1371). scripts/setup.py
# side-loads the cuDNN 8 libraries for source installs; the image needs the same
# shim or Docker users lose every CTranslate2 ASR engine (#2050).
#
# The target is derived from sys.prefix rather than hardcoded: backend/core/
# cudnn8.py looks for <sys.prefix>/lib/pythonX.Y/site-packages/cudnn8_compat,
# and sys.prefix differs between the conda-based CUDA image and the ROCm venv.
# --no-deps keeps this to the cuDNN wheels alone, leaving the base image's torch
# stack untouched. Skipped for ROCm, which does not use cuDNN.
RUN if [ "$GPU_FLAVOR" = "cuda" ]; then \
      target="$(python3 -c "import os, sys; print(os.path.join(sys.prefix, 'lib', 'python%d.%d' % sys.version_info[:2], 'site-packages', 'cudnn8_compat'))")" && \
      uv pip install --python "$(command -v python3)" --no-cache --no-deps \
        --target "$target" nvidia-cudnn-cu12==8.9.7.29 && \
      python3 -c "import os, sys; d = os.path.join(sys.prefix, 'lib', 'python%d.%d' % sys.version_info[:2], 'site-packages', 'cudnn8_compat', 'nvidia', 'cudnn', 'lib'); \
        libs = [f for f in os.listdir(d) if '.so.8' in f]; \
        assert libs, 'cudnn8_compat installed but no .so.8 libraries in ' + d; \
        print('cuDNN 8 compat libraries: %d' % len(libs))"; \
    fi

# Copy application source
COPY backend/ ./backend/
COPY omnivoice/ ./omnivoice/
# Alembic config so schema migrations run natively on existing volumes
# (without it the backend fell back to the additive-column self-heal —
# functional, but the real migration chain is the first-class path).
COPY alembic.ini ./

# Copy the pre-built React frontend from the builder stage
COPY --from=frontend-builder /app/frontend/dist ./frontend/dist

# Expose the single unified API and UI port
EXPOSE 3900

# Image-level health probe (compose files define their own; this covers plain
# `docker run`). Generous start period: first boot creates the venv-less
# schema + may pull model metadata before /health answers.
HEALTHCHECK --interval=30s --timeout=5s --start-period=120s --retries=5 \
  CMD curl -fsS http://127.0.0.1:3900/health || exit 1

# Mount points for persistent data (sqlite db, user voices, huggingface cache)
VOLUME ["/app/omnivoice_data"]

# Bind to 0.0.0.0 for external access. `python3 -m` keeps runtime imports on
# the same interpreter whose torch flavor the build guard validated (#1274).
ENTRYPOINT ["python3", "-m", "uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "3900"]
