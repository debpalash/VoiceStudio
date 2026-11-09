"""Setup package — modular replacement for the monolithic ``setup.py``.

Re-exports a single ``router`` that includes all three sub-routers so
``main.py`` can continue doing ``from api.routers import setup`` and
``app.include_router(setup.router)`` without changes.
"""
from __future__ import annotations

from fastapi import APIRouter

from .models import router as _models_router
from .wizard import router as _wizard_router
from .download import router as _download_router
from ..model_licenses import router as _model_licenses_router

# Re-export commonly used symbols for backward compatibility.
from .models import KNOWN_MODELS, REQUIRED_MODELS, hf_cache_dir, is_cached  # noqa: F401

router = APIRouter()
router.include_router(_models_router)
router.include_router(_wizard_router)
router.include_router(_model_licenses_router)
router.include_router(_download_router)

