"""
Translation engine registry + UI-driven install/uninstall.

This is the single source of truth for which translation providers we know
about, what pip package they need, and whether that package is importable
right now. The Engine dropdown in the Dub tab reads list_engines() to
decide which options are ready-to-use vs. "needs install".

Why a registry rather than inline probes in dub_translate.py? The UI wants
to render the availability table BEFORE the user clicks Translate, so we
don't surface a cryptic ModuleNotFoundError for every segment. Having the
registry live next to the dub_translate dispatch also means adding a new
engine is one entry here + one branch in _build_translator.
"""
from __future__ import annotations

import asyncio
import importlib
import importlib.util
import json
import logging
import functools
import re
import os
import shutil
import subprocess
import sys
import threading

logger = logging.getLogger("omnivoice.translation_engines")

_NLLB_REPO_ID = "facebook/nllb-200-distilled-600M"
_ARGOS_INSTALL_LOCK = threading.Lock()
# UI labels that name a language instead of coding it, mapped to Argos' token.
_ARGOS_NAME_ALIASES = {
    "chinese": "zh",
    "chinese (simplified)": "zh",
    "mandarin": "zh",
}
# Other ISO 639 spellings (three-letter or retired two-letter) mapped to the
# token Argos package metadata uses.
_ARGOS_CODE_ALIASES = {
    "cmn": "zh",
    "zho": "zh",
    "in": "id",   # retired Indonesian code
    "iw": "he",   # retired Hebrew code
    "fil": "tl",  # Filipino is packaged as Tagalog
}
_ARGOS_CHINESE_CODES = frozenset({"zh", "zho", "cmn"})
_ARGOS_TRADITIONAL_SUBTAGS = frozenset({"hant", "tw", "hk", "mo"})


# Engine ID → registry entry. Keyed by the `provider` string sent from the
# frontend (must match the values of `translateProvider` in the store).
REGISTRY: dict[str, dict] = {
    "argos": {
        "id": "argos",
        "display_name": "Argos (Local, Fast)",
        "pip_package": "argostranslate",
        # The bare package imports without the native library; the translate
        # submodule is what loads CTranslate2 and can fail (#692, #1185).
        "probe_module": "argostranslate.translate",
        "category": "offline",
        "needs_key": False,
        "builtin": True,
        "notes": "Pure-CPU offline translator. Install the required language pack explicitly for each pair.",
    },
    "nllb": {
        "id": "nllb",
        "display_name": "NLLB-200 (Local, Heavy)",
        "pip_package": None,          # uses HF transformers — already a core dep
        "probe_module": "transformers",
        "category": "offline",
        "needs_key": False,
        "builtin": True,
        # A gated model: /dub/translate refuses it with a typed 403 until the
        # user accepts its terms (#2689). Declared here so readiness answers
        # the same question the translate path does.
        "licence_repo_id": _NLLB_REPO_ID,
        "notes": "Meta's 200-language NMT model. Large download (~2.4GB), best offline quality.",
    },
    "google": {
        "id": "google",
        "display_name": "Google Translate (Online, Free)",
        "pip_package": "deep_translator",
        "probe_module": "deep_translator",
        "category": "online",
        "needs_key": False,
        "notes": "Free web endpoint via deep_translator. Rate-limited by Google; no API key required.",
    },
    "deepl": {
        "id": "deepl",
        "display_name": "DeepL (Online, Key)",
        "pip_package": "httpx",
        "probe_module": "httpx",
        "builtin": True,
        "category": "online",
        "needs_key": True,
        "notes": "High-quality EU MT. Free tier: 500K chars/month. Set DEEPL_API_KEY.",
    },
    "microsoft": {
        "id": "microsoft",
        "display_name": "Microsoft Translator (Online, Key)",
        "pip_package": "httpx",
        "probe_module": "httpx",
        "builtin": True,
        "category": "online",
        "needs_key": True,
        "notes": "Azure Cognitive Services. Free tier: 2M chars/month. Set MICROSOFT_API_KEY.",
    },
    "google-cloud": {
        "id": "google-cloud", "display_name": "Google Cloud Translation", "pip_package": "httpx",
        "probe_module": "httpx", "category": "online", "needs_key": True, "builtin": True,
    },
    "amazon": {
        "id": "amazon", "display_name": "Amazon Translate", "pip_package": "boto3",
        "probe_module": "boto3", "category": "online", "needs_key": True, "builtin": True,
    },
    "mymemory": {
        "id": "mymemory",
        "display_name": "MyMemory (Online, No Key)",
        "pip_package": "deep_translator",
        "probe_module": "deep_translator",
        "category": "online",
        "needs_key": False,
        "notes": "Crowdsourced MT. Free, 5K chars/day anonymous; more with an email param.",
    },
    "openai": {
        "id": "openai",
        "display_name": "LLM (OpenAI-compatible)",
        "pip_package": "openai",
        "probe_module": "openai",
        "category": "llm",
        "needs_key": True,
        # A core dependency: Settings → LLM Providers uses it too.
        "builtin": True,
        "notes": (
            "Uses the LLM provider you configure in Settings → LLM Providers "
            "(route it via the 'Dub translation' skill in Settings → LLM Skills): "
            "GPT (OpenAI), Claude (via OpenRouter), Gemini, DeepSeek, Qwen, "
            "Ollama, LM Studio. Power-user env override: TRANSLATE_BASE_URL + "
            "TRANSLATE_API_KEY + TRANSLATE_MODEL."
        ),
    },
}


def is_frozen() -> bool:
    """True when running inside a packaged Tauri / PyInstaller bundle.

    In that case the Python site-packages is read-only and signed, so we
    refuse install/uninstall requests instead of corrupting the bundle.
    """
    return bool(getattr(sys, "frozen", False) or os.environ.get("OMNIVOICE_FROZEN"))


def _nllb_weights_probe() -> tuple[bool, str | None]:
    # Transformers being importable only proves the runtime exists.
    # The weights are a separate explicit model install; do not report
    # NLLB ready and let from_pretrained download 2.4 GB silently.
    from api.routers.setup.models import cache_is_complete, is_cached

    model = {"repo_id": _NLLB_REPO_ID}
    if not is_cached(_NLLB_REPO_ID) or not cache_is_complete(model):
        return False, "NLLB model weights are not installed"
    return True, None


def _argos_native_blocker() -> str | None:
    """Try to make CTranslate2 loadable before Argos is imported (#692).

    Returns the failure text if it stays unloadable, else None. A crash in the
    repair code itself is only logged.
    """
    try:
        from core.execstack import ensure_ctranslate2_loadable

        loadable, why = ensure_ctranslate2_loadable()
    except Exception as exc:  # noqa: BLE001
        logger.debug("CTranslate2 repair before Argos probe failed: %s", exc)
        return None
    return None if loadable else why


def _probe(entry: dict, *, import_check: bool = True) -> tuple[bool, str | None]:
    mod = entry.get("probe_module")
    if not mod:
        return True, None
    if not import_check:
        # Inventory-only answer: is the package installed? Importing it is the
        # expensive part (argostranslate is 8-54 s cold), and the real
        # translate path still imports - and fails loudly - on first use.
        if importlib.util.find_spec(mod.split(".")[0]) is None:
            return False, f"{mod!r} is not installed"
        if entry.get("id") == "nllb":
            return _nllb_weights_probe()
        return True, None
    if mod.split(".")[0] == "argostranslate":
        blocked = _argos_native_blocker()
        if blocked:
            return False, blocked
    try:
        importlib.import_module(mod)
        if entry.get("id") == "nllb":
            return _nllb_weights_probe()
        # availability_reason is failure-only metadata. Returning a success
        # label here made every healthy provider look unavailable after the
        # public diagnostic scrubber intentionally replaced non-null details.
        return True, None
    except ImportError as e:
        return False, f"import {mod!r} failed: {e}"
    except Exception as e:  # noqa: BLE001 — native libraries raise OSError
        return False, f"import {mod!r} failed ({type(e).__name__}): {e}"


def install_command(engine: "str | dict | None") -> str | None:
    """The exact shell command that makes this engine importable, or None.

    Single source of truth for the install string. BOTH the proactive Install
    affordance in the Engine selector (via list_engines' ``install_command``
    field) AND the translate-time 400 error (dub_translate.py) read from here,
    so the command a user is told to run can never drift between the two
    surfaces. Returns None when the engine needs no separate install — either
    it's unknown or its dependency is a core dep already pinned in
    ``pyproject.toml`` (e.g. NLLB → transformers), in which case a
    ``uv pip install`` line would be misleading.
    """
    entry = engine if isinstance(engine, dict) else REGISTRY.get(engine) if engine else None
    pkg = entry.get("pip_package") if entry else None
    return f"uv pip install {pkg}" if pkg else None


def _llm_configured() -> tuple[bool, "str | None"]:
    """Whether the LLM translation engine has something to call, and via what.

    Resolution mirrors the translate-time path in dub_translate.py: the
    "dub_translation" LLM skill (per-skill override → active provider from
    Settings → LLM Providers) first, then the TRANSLATE_* env override. Lets
    the Engine dropdown say "ready via <provider>" / "needs setup" up front
    instead of a per-segment failure after the user clicks Translate.
    """
    try:
        from services import llm_skills
        res = llm_skills.resolve_skill("dub_translation")
        if res.ready and res.provider is not None:
            return True, res.provider.display_name
    except Exception:  # noqa: BLE001 — a probe must never break list_engines()
        logger.debug("dub_translation skill probe failed", exc_info=True)
    if os.environ.get("TRANSLATE_BASE_URL") or os.environ.get("TRANSLATE_API_KEY"):
        return True, "env"
    return False, None


def _licence_ready(entry: dict) -> tuple[bool, str | None]:
    """Whether an engine's gated model has been accepted in Model Catalogue.

    The registry declares ``licence_repo_id`` for engines whose model carries
    non-commercial terms. ``/dub/translate`` refuses such a model with a typed
    403 (#2689), but nothing else consulted the gate: the Engine selector
    listed NLLB as ready and the performance presets auto-selected it, so the
    first translate could only fail. Availability must answer the same
    question the translate path does — the whole point of this registry is
    that the UI knows before the user clicks Translate. Fails closed: an
    unreadable licence record is not evidence of acceptance.
    """
    repo_id = entry.get("licence_repo_id")
    if not repo_id:
        return True, None
    try:
        from services.model_acceptance import status

        state = status(repo_id)
    except Exception:  # noqa: BLE001 — a probe must never break list_engines()
        logger.debug("licence probe failed for %s", repo_id, exc_info=True)
        return False, "Model licence records are unreadable — review the model in Model Catalogue."
    if not state.get("required") or state.get("accepted"):
        return True, None
    return False, (
        f"{state.get('license') or 'This model'} licence is not accepted — "
        "review and accept it in Model Catalogue before translating."
    )


def _configured(entry: dict) -> tuple[bool, str | None]:
    """Whether an installed engine has the configuration needed to run."""
    engine_id = entry.get("id")
    if engine_id == "openai":
        return _llm_configured()
    if engine_id == "deepl":
        return bool(os.environ.get("DEEPL_API_KEY") or os.environ.get("TRANSLATE_API_KEY")), None
    if engine_id == "microsoft":
        return bool(os.environ.get("MICROSOFT_API_KEY") or os.environ.get("TRANSLATE_API_KEY")), None
    if engine_id == "google-cloud":
        return bool(os.environ.get("GOOGLE_TRANSLATE_API_KEY")), None
    if engine_id == "amazon":
        # Resolve shared-file, SSO and role credentials only on an explicit request.
        return True, None
    return True, None


def list_engines() -> list[dict]:
    """Return a UI-ready list with per-engine availability stamped in."""
    out = []
    for e in REGISTRY.values():
        installed, reason = _probe(e)
        configured, via = _configured(e)
        licence_ok, licence_reason = _licence_ready(e)
        ready = installed and configured and licence_ok
        entry = {
            **e,
            "installed": installed,
            "configured": configured,
            "configured_via": via,
            "ready": ready,
            "availability_reason": reason or licence_reason or (
                None if configured else "Translation provider is not configured"
            ),
            "install_command": install_command(e),
        }
        # LLM engines additionally need a provider/key — surface configured-ness
        # so the UI can distinguish "importable" from "actually ready to call".
        out.append(entry)
    return out


def _normalize(name: str) -> str:
    """A distribution name in PEP 503 form (deep_translator == deep-translator)."""
    return re.sub(r"[-_.]+", "-", name).lower()


@functools.lru_cache(maxsize=1)
def _app_dependency_names() -> frozenset[str]:
    """Distribution names VoiceStudio itself requires, normalized.

    Read from the installed package metadata, so it follows the lockfile with
    no second list to keep in step. Without metadata this guards nothing
    rather than failing.
    """
    try:
        from importlib.metadata import requires

        reqs = requires("omnivoice") or []
    except Exception:  # noqa: BLE001
        return frozenset()
    names = set()
    for req in reqs:
        if "extra ==" in req:
            continue
        names.add(_normalize(re.split(r"[\s;<>=!~\[@(]", req, maxsplit=1)[0]))
    return frozenset(names)


def uninstall_blocker(engine_id: str) -> "tuple[int, str] | None":
    """Why removing this engine's package would break something, or None.

    `pip uninstall` acts on the app's own environment. A package VoiceStudio
    depends on (openai, argostranslate) would break the app, and a package
    other translation engines share (deep_translator backs four) would break
    those engines too.
    """
    entry = REGISTRY.get(engine_id)
    pkg = entry.get("pip_package") if entry else None
    if not pkg:
        return None
    if entry.get("builtin") or _normalize(pkg) in _app_dependency_names():
        return 400, (
            f"{entry['display_name']} uses {pkg}, which VoiceStudio itself "
            "depends on. Uninstalling it would break the app."
        )
    sharing = [
        other["display_name"]
        for other_id, other in REGISTRY.items()
        if other_id != engine_id
        and other.get("pip_package")
        and _normalize(other["pip_package"]) == _normalize(pkg)
    ]
    if sharing:
        return 409, (
            f"{entry['display_name']} shares {pkg} with {', '.join(sharing)}. "
            "Uninstalling it would stop those working too."
        )
    return None


def get_engine(engine_id: str) -> dict | None:
    return REGISTRY.get(engine_id)


def is_installed(engine_id: str) -> bool:
    entry = REGISTRY.get(engine_id)
    if not entry:
        return False
    ok, _ = _probe(entry)
    return ok


def is_ready(engine_id: str, *, import_check: bool = True) -> bool:
    """True only when both runtime/model and required configuration exist.

    ``import_check=False`` answers from the installed packages alone, for
    inventory scans that must not import every engine runtime.
    """
    entry = REGISTRY.get(engine_id)
    if not entry:
        return False
    installed, _ = _probe(entry, import_check=import_check)
    configured, _ = _configured(entry)
    licence_ok, _ = _licence_ready(entry)
    return installed and configured and licence_ok


def argos_lang_code(value: str) -> str:
    """Reduce a UI label or locale tag to the base code Argos packages use.

    ``zh-CN``, ``cmn_Hans``, ``Mandarin`` and ``zho`` all become ``zh``;
    region and script suffixes are dropped. Raises ``ValueError`` for
    Traditional Chinese (Argos has no such model) and for anything that does
    not reduce to a two- or three-letter code.
    """
    text = str(value or "").strip().lower()
    parts = text.replace("_", "-").split("-")
    if text == "chinese (traditional)" or (
        parts[0] in _ARGOS_CHINESE_CODES
        and any(tag in _ARGOS_TRADITIONAL_SUBTAGS for tag in parts[1:])
    ):
        raise ValueError(
            "Argos has no Traditional Chinese model; pick NLLB for Traditional "
            "Chinese, or choose Simplified Chinese for Argos."
        )
    if text in _ARGOS_NAME_ALIASES:
        return _ARGOS_NAME_ALIASES[text]
    code = _ARGOS_CODE_ALIASES.get(parts[0], parts[0])
    code = _ARGOS_NAME_ALIASES.get(code, code)
    if not re.fullmatch(r"[a-z]{2,3}", code):
        raise ValueError("Choose a valid source and target language")
    return code


def _configure_argos_cache() -> None:
    cache_dir = os.environ.get("OMNIVOICE_CACHE_DIR")
    if not cache_dir:
        return
    argos_cache = os.path.join(cache_dir, "argos-translate")
    os.makedirs(argos_cache, exist_ok=True)
    os.environ.setdefault("ARGOS_PACKAGES_DIR", argos_cache)
    os.environ.setdefault("ARGOS_DATA_DIR", argos_cache)


def argos_pack_status(source_lang: str, target_langs: list[str]) -> dict:
    """Report installed Argos pairs without refreshing the remote index."""
    _configure_argos_cache()
    import argostranslate.package

    source = argos_lang_code(source_lang)
    targets = list(dict.fromkeys(argos_lang_code(code) for code in target_langs))
    installed = {
        (package.from_code, package.to_code)
        for package in argostranslate.package.get_installed_packages()
    }
    return {
        "source_lang": source,
        "pairs": [
            {
                "source_lang": source,
                "target_lang": target,
                "installed": source == target or (source, target) in installed,
            }
            for target in targets
        ],
    }


def _argos_index_usable(index_path: str) -> bool:
    """True when the Argos package index exists on disk and parses as JSON.

    Upstream argostranslate.get_available_packages() retries a missing index
    by calling update_package_index() and then itself — with NO recursion
    bound. When the index host is unreachable (this machine: argosopentech.com
    drops the TLS handshake), update_package_index() merely logs the failure
    and returns, so the retry never terminates: the request dies with
    RecursionError (~975 frames) instead of a network error, and the error's
    own log emission crashes into the same exhausted recursion budget, which
    is what produced the giant nested traceback behind the packs-install 500.
    """
    try:
        with open(index_path, encoding="utf-8") as index_file:
            data = json.load(index_file)
    except (OSError, ValueError):
        return False
    return isinstance(data, list)


def install_argos_packs(source_lang: str, target_langs: list[str]) -> dict:
    """Explicitly download and install the requested Argos language pairs."""
    _configure_argos_cache()
    import argostranslate.package

    source = argos_lang_code(source_lang)
    targets = list(dict.fromkeys(argos_lang_code(code) for code in target_langs))
    with _ARGOS_INSTALL_LOCK:
        status = argos_pack_status(source, targets)
        missing = {
            pair["target_lang"]
            for pair in status["pairs"]
            if not pair["installed"]
        }
        if missing:
            argostranslate.package.update_package_index()
            # Guard the upstream unbounded self-retry (see _argos_index_usable):
            # if the update could not materialise a readable index, calling
            # get_available_packages() now would recurse until RecursionError.
            import argostranslate.settings as _argos_settings

            if not _argos_index_usable(_argos_settings.local_package_index):
                raise ValueError(
                    "Could not download the Argos package index from "
                    "argosopentech.com — the index host is unreachable from "
                    "this machine. Check the network connection (a proxy may "
                    "be required), or switch the translation engine in "
                    "Settings → Dub."
                )
            available = argostranslate.package.get_available_packages()
            for target in targets:
                if target not in missing:
                    continue
                package = next(
                    (
                        item
                        for item in available
                        if item.from_code == source and item.to_code == target
                    ),
                    None,
                )
                if package is None:
                    raise ValueError(
                        f"No Argos language pack is available for {source} → {target}"
                    )
                argostranslate.package.install_from_path(package.download())
        return argos_pack_status(source, targets)


def _in_virtualenv() -> bool:
    """True if the current interpreter is inside a venv/virtualenv."""
    return getattr(sys, "base_prefix", sys.prefix) != sys.prefix or hasattr(sys, "real_prefix")


def _installer_cmd() -> list[str]:
    """Prefer `uv pip` (the dev install's default), fall back to `python -m pip`.

    `python -m pip` ensures we target the same interpreter the server is
    running under — avoids the classic "pip installed into the wrong venv"
    footgun.
    """
    if shutil.which("uv"):
        return ["uv", "pip"]
    return [sys.executable, "-m", "pip"]


async def run_pip(args: list[str], timeout: float = 600.0) -> tuple[int, str]:
    """Run a pip command async and return (rc, combined_output).

    Combines stdout + stderr so the UI can surface a useful tail on failure
    (pip's "ERROR: ..." lines go to stderr).

    When using `uv pip` and running outside a venv (e.g. inside the Docker
    image where Python runs as system), inject `--system` after the
    install/uninstall subcommand. Without it, uv refuses to write to system
    Python with: "No virtual environment found; run `uv venv` to create an
    environment, or pass `--system`...". The `UV_SYSTEM_PYTHON` env var only
    affects `uv venv`, not `uv pip install`.
    """
    base = _installer_cmd()
    using_uv = base[:1] == ["uv"]
    # Pin `uv pip` to the interpreter the backend ACTUALLY runs under. The desktop
    # spawns `<venv>/bin/python -m uvicorn` WITHOUT exporting VIRTUAL_ENV, so bare
    # `uv pip install` finds no venv and 500s with "No virtual environment found"
    # (#529/#527) — and the `--system` branch below never fires, because the
    # running interpreter genuinely IS in a venv (uv just can't auto-discover it).
    # `--python sys.executable` targets the same interpreter _probe()/is_installed()
    # import from, and takes precedence when both flags are present, so the Docker
    # `--system` path is unaffected.
    if using_uv and args and args[0] in ("install", "uninstall") and "--python" not in args:
        args = [args[0], "--python", sys.executable, *args[1:]]
    if using_uv and not _in_virtualenv() and args and args[0] in ("install", "uninstall") and "--system" not in args:
        args = [args[0], "--system", *args[1:]]
    cmd = base + args
    logger.info("pip: %s", " ".join(cmd))
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
    except NotImplementedError:
        logger.debug("Async process launching is unavailable; running the installer in a worker thread")
        return await _run_installer_in_thread(cmd, timeout)
    except FileNotFoundError as e:
        return 1, f"installer not found: {e}"
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        return 1, f"pip timed out after {timeout:.0f}s"
    out = stdout.decode(errors="replace") if stdout else ""
    return proc.returncode or 0, out


async def _run_installer_in_thread(cmd: list[str], timeout: float) -> tuple[int, str]:
    loop = asyncio.get_running_loop()
    factory = functools.partial(
        subprocess.Popen,
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    proc = await loop.run_in_executor(None, factory)
    try:
        stdout, _ = await loop.run_in_executor(
            None, functools.partial(proc.communicate, timeout=timeout)
        )
    except subprocess.TimeoutExpired:
        proc.kill()
        await loop.run_in_executor(None, proc.communicate)
        return 1, f"pip timed out after {timeout:.0f}s"
    out = stdout.decode(errors="replace") if stdout else ""
    return proc.returncode or 0, out

