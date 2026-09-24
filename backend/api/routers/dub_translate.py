import json
import os
import time
import asyncio
import logging
from typing import Optional
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from schemas.requests import AgentFitRequest, TranslateRequest
from services.model_manager import _cpu_pool, _gpu_pool
from services.hf_revisions import revision_for
from services.translator import cinematic_available, cinematic_refine_many, _cinematic_budget
from api.routers.dub_core import _get_job, _save_job

router = APIRouter()
logger = logging.getLogger("omnivoice.api")

_NLLB_REPO_ID = "facebook/nllb-200-distilled-600M"


def _load_nllb_component(factory):
    """Load explicitly installed NLLB weights at their reviewed revision."""
    return factory.from_pretrained(
        _NLLB_REPO_ID,
        revision=revision_for(_NLLB_REPO_ID),
        local_files_only=True,
    )

TRANSLATE_CODES = {
    "en": "en", "es": "es", "fr": "fr", "de": "de", "it": "it", "pt": "pt",
    "ru": "ru", "ja": "ja", "ko": "ko", "zh": "zh-CN", "cmn-Hans": "zh-CN",
    "ar": "ar", "hi": "hi", "tr": "tr", "pl": "pl", "nl": "nl", "sv": "sv",
    "th": "th", "vi": "vi", "id": "id", "uk": "uk", "ml": "ml",
}

FLORES_CODES = {
    "en": "eng_Latn", "es": "spa_Latn", "fr": "fra_Latn", "de": "deu_Latn",
    "it": "ita_Latn", "pt": "por_Latn", "ru": "rus_Cyrl", "ja": "jpn_Jpan",
    "ko": "kor_Hang", "zh": "zho_Hans", "zh-CN": "zho_Hans", "cmn-Hans": "zho_Hans", "ar": "arb_Arab",
    "hi": "hin_Deva", "tr": "tur_Latn", "pl": "pol_Latn", "nl": "nld_Latn",
    "sv": "swe_Latn", "th": "tha_Thai", "vi": "vie_Latn", "id": "ind_Latn",
    "uk": "ukr_Cyrl",
    "zh-TW": "zho_Hant", "zh-Hant": "zho_Hant", "cmn-Hant": "zho_Hant",
    "zh-Hans": "zho_Hans", "yue": "yue_Hant",
    "bn": "ben_Beng", "ta": "tam_Taml", "te": "tel_Telu", "ml": "mal_Mlym",
    "kn": "kan_Knda", "gu": "guj_Gujr", "mr": "mar_Deva", "ur": "urd_Arab",
    "fa": "pes_Arab", "he": "heb_Hebr", "el": "ell_Grek", "cs": "ces_Latn",
    "da": "dan_Latn", "fi": "fin_Latn", "nb": "nob_Latn", "nn": "nno_Latn",
    "ro": "ron_Latn", "hu": "hun_Latn", "bg": "bul_Cyrl", "sk": "slk_Latn",
    "sl": "slv_Latn", "hr": "hrv_Latn", "sr": "srp_Cyrl", "lt": "lit_Latn",
    "et": "est_Latn", "sw": "swh_Latn", "af": "afr_Latn", "ms": "zsm_Latn",
}


def _nllb_language(code: str) -> str | None:
    """Resolve aliases or tokenizer-supported FLORES codes without loading weights."""
    from transformers.models.nllb.tokenization_nllb import FAIRSEQ_LANGUAGE_CODES

    normalized = code.strip().replace("_", "-").lower()
    aliases = {key.lower(): value for key, value in FLORES_CODES.items()}
    if normalized in aliases:
        return aliases[normalized]
    exact = [value for value in FAIRSEQ_LANGUAGE_CODES if value.replace("_", "-").lower() == normalized]
    if exact:
        return exact[0]
    # Bare ISO-639-3 codes are safe only when the tokenizer has one script.
    matches = [value for value in FAIRSEQ_LANGUAGE_CODES if value.split("_")[0] == normalized]
    return matches[0] if len(matches) == 1 else None

# Human-readable language names for LLM prompts. Empirically a tiny / 7B
# local LLM produces Devanagari Hindi reliably when told "translate into
# Hindi" but drifts to German / English / phonetic-Latin when told
# "translate into hi". The two-letter ISO codes "hi" / "de" / "fr" can
# overlap with everyday tokens ("hi" = greeting), which throws off small
# instruction-tuned models. Pass the full name in the prompt so the model
# can't misread it.
LANG_NAMES = {
    "en": "English", "es": "Spanish", "fr": "French", "de": "German",
    "it": "Italian", "pt": "Portuguese", "ru": "Russian", "ja": "Japanese",
    "ko": "Korean", "zh": "Chinese (Simplified)", "zh-CN": "Chinese (Simplified)", "cmn-Hans": "Chinese (Simplified)",
    "ar": "Arabic", "hi": "Hindi", "tr": "Turkish", "pl": "Polish",
    "nl": "Dutch", "sv": "Swedish", "th": "Thai", "vi": "Vietnamese",
    "id": "Indonesian", "uk": "Ukrainian",
}

# Regional dialect hints (#280 item 2). Maps a BCP-47 dialect code to the
# instruction injected into LLM translation prompts so the output uses that
# region's vocabulary and grammar (the reporter's example: choosing Argentina
# should yield "Vos sos muy listo", not the Peninsular "Tú eres muy listo").
# Only LLM-backed paths can honor these — provider="openai" and the
# quality="cinematic" refine pass. Keep entries short: they ride on every
# per-segment prompt, so verbosity = wall time.
DIALECT_HINTS = {
    # Spanish
    "es-ES": "European Spanish (Spain): use tú/vosotros forms and Peninsular vocabulary.",
    "es-MX": "Mexican Spanish: use tú/ustedes forms and Mexican vocabulary.",
    "es-AR": "Rioplatense Spanish (Argentina): use voseo — 'vos' with its verb forms (e.g. 'vos sos', 'tenés') and 'ustedes'; prefer Argentinian vocabulary.",
    "es-CO": "Colombian Spanish: use tú/usted as natural in Colombia and Colombian vocabulary.",
    "es-CL": "Chilean Spanish: use Chilean vocabulary and expressions.",
    # Portuguese
    "pt-BR": "Brazilian Portuguese: use 'você' forms, Brazilian vocabulary and spelling.",
    "pt-PT": "European Portuguese: use European vocabulary, spelling, and 'tu' where natural.",
    # English
    "en-US": "American English: use US spelling and vocabulary.",
    "en-GB": "British English: use UK spelling and vocabulary.",
    "en-AU": "Australian English: use Australian spelling and vocabulary.",
    "en-IN": "Indian English: use Indian English vocabulary and conventions.",
    # French
    "fr-FR": "Metropolitan French (France): use standard French vocabulary.",
    "fr-CA": "Canadian French (Québec): use Québécois vocabulary and expressions.",
    "fr-BE": "Belgian French: use Belgian vocabulary (e.g. septante, nonante).",
    # German
    "de-DE": "Standard German (Germany): use Federal German vocabulary.",
    "de-AT": "Austrian German: use Austrian vocabulary (e.g. Jänner, Erdapfel).",
    "de-CH": "Swiss Standard German: use Swiss vocabulary and 'ss' instead of 'ß'.",
    # Arabic
    "ar-EG": "Egyptian Arabic: use Egyptian colloquial vocabulary where natural for dubbing.",
    "ar-SA": "Gulf/Saudi Arabic flavor: prefer vocabulary natural to the Gulf region.",
    "ar-MA": "Moroccan Arabic (Darija) flavor: prefer vocabulary natural to Morocco.",
    # Dutch
    "nl-NL": "Netherlands Dutch: use vocabulary standard in the Netherlands.",
    "nl-BE": "Belgian Dutch (Flemish): use Flemish vocabulary and expressions.",
}


def dialect_clause(dialect: Optional[str]) -> str:
    """Prompt fragment for a requested dialect, or '' when unset/unknown.

    Unknown-but-plausible codes (e.g. "es-PE") still get a generic regional
    clause so users aren't limited to the curated list.
    """
    if not dialect or not str(dialect).strip():
        return ""
    code = str(dialect).strip()
    hint = DIALECT_HINTS.get(code)
    if hint:
        return f" Target dialect — {hint}"
    # Generic fallback for any lang-REGION shaped code we don't curate.
    if "-" in code:
        lang, _, region = code.partition("-")
        lang_name = LANG_NAMES.get(lang, lang)
        if region:
            return (
                f" Use the vocabulary, grammar, and expressions of {lang_name} "
                f"as spoken in the region '{region}'."
            )
    return ""


# Per-language script enforcement. Maps language code → required Unicode
# block(s) the translation must contain. Used as a sanity gate after the
# LLM responds: if the output contains <50% characters from the expected
# block, we treat the translation as corrupted and retry. The block names
# here are the keys recognised by Python's `unicodedata.name()` lookup or
# regex Unicode property classes.
LANG_REQUIRED_SCRIPT = {
    "hi":  ("DEVANAGARI", (0x0900, 0x097F)),
    "ar":  ("ARABIC",     (0x0600, 0x06FF)),
    "zh":  ("CJK",        (0x4E00, 0x9FFF)),
    "zh-CN": ("CJK",      (0x4E00, 0x9FFF)),
    "ja":  ("JAPANESE",   (0x3040, 0x30FF)),
    "ko":  ("HANGUL",     (0xAC00, 0xD7AF)),
    "th":  ("THAI",       (0x0E00, 0x0E7F)),
    "ru":  ("CYRILLIC",   (0x0400, 0x04FF)),
    "uk":  ("CYRILLIC",   (0x0400, 0x04FF)),
}


def _script_ratio(text: str, code: str) -> float:
    """Fraction of letters in `text` that fall inside the script block we
    expect for `code`. Punctuation/digits/whitespace are excluded from the
    denominator so a Hindi sentence ending in "." still scores 1.0."""
    info = LANG_REQUIRED_SCRIPT.get(code)
    if not info:
        return 1.0
    _, (lo, hi) = info
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return 1.0
    inside = sum(1 for c in letters if lo <= ord(c) <= hi)
    return inside / len(letters)


def _looks_like_target(text: str, code: str, threshold: float = 0.5) -> bool:
    """Sanity gate for non-Latin targets. True if `text` is *plausibly* in
    the target language by script. Only meaningful for languages with a
    distinctive script (Indic, CJK, Arabic, etc.); Latin-script targets
    always return True since we can't distinguish English from German by
    codepoints alone."""
    return _script_ratio(text, code) >= threshold


def _translation_output_error(text: object) -> str | None:
    """Reject provider error pages that arrive with HTTP 200.

    Google's mobile endpoint occasionally returns its generic HTML error copy
    inside the element deep-translator treats as a successful translation.
    Passing that through would replace the user's transcript with the error
    page, so treat it like any other transient provider failure and retry.
    """
    if not isinstance(text, str) or not text.strip():
        return "empty translation"
    normalized = " ".join(text.split()).casefold()
    error_markers = (
        "error 500 (server error)",
        "that's an error",
        "that’s an error",
        "there was an error. please try again later",
        "no translation was found using the current translator",
    )
    if "\ufffd" in text or any(marker in normalized for marker in error_markers):
        return "translation provider returned invalid output"
    return None

_nllb_model = None
_nllb_tokenizer = None
_nllb_device = None
_NLLB_BATCH_SIZE_ENV = "OMNIVOICE_NLLB_BATCH_SIZE"
_NLLB_MAX_BATCH_SIZE = 32


def _nllb_batch_size() -> int:
    """Bound NLLB forward-pass width; explicit overrides remain available."""
    configured = os.environ.get(_NLLB_BATCH_SIZE_ENV, "").strip()
    if configured:
        try:
            return max(1, min(_NLLB_MAX_BATCH_SIZE, int(configured)))
        except (TypeError, ValueError):
            logger.warning("%s=%r is not an integer; using the safe default", _NLLB_BATCH_SIZE_ENV, configured)
    # The 600M checkpoint leaves ample room on modern discrete GPUs. Scale the
    # forward-pass width there; CPU and unified-memory MPS keep the conservative
    # width because their failure recovery moves the whole model.
    if _nllb_device == "cuda":
        try:
            import torch

            free_gib = int(torch.cuda.mem_get_info()[0]) / 1024**3
            if free_gib >= 16:
                return 24
            if free_gib >= 8:
                return 12
        except Exception:
            pass
        return 8
    return 4


def _nllb_hypothesis_budget() -> int:
    """Bound batch × beam hypotheses by currently available device memory."""
    if _nllb_device != "cuda":
        return 16
    try:
        import torch

        free_gib = int(torch.cuda.mem_get_info()[0]) / 1024**3
        if free_gib >= 16:
            return 64
        if free_gib >= 8:
            return 32
    except Exception:
        pass
    return 16


def _dialect_flags(req, applied: bool) -> dict:
    """Response fields describing whether the requested dialect was honored.

    Empty dict when no dialect was requested, so existing response shapes
    stay byte-identical for callers that never send one.
    """
    if not getattr(req, "dialect", None):
        return {}
    return {"dialect": req.dialect, "dialect_applied": bool(applied)}


def _guess_lang_from_text(segments) -> str | None:
    """Best-effort source language from segment text, by script.

    Used only as a last resort when neither the request nor the job carries a
    detected language. Without this, the bare "en" fallback below forces
    en -> en on non-English audio (e.g. Korean), which has no Argos package and
    fails every segment even though ASR detected the language correctly.
    """
    text = " ".join((getattr(s, "text", "") or "") for s in (segments or [])[:8])
    has = lambda lo, hi: any(lo <= ord(c) <= hi for c in text)
    if has(0x3040, 0x30FF):
        return "ja"  # Hiragana/Katakana — check before CJK (Japanese uses Kanji too)
    if has(0xAC00, 0xD7A3) or has(0x1100, 0x11FF):
        return "ko"  # Hangul
    if has(0x4E00, 0x9FFF):
        return "zh"  # CJK ideographs
    if has(0x0400, 0x04FF):
        return "ru"  # Cyrillic
    if has(0x0600, 0x06FF):
        return "ar"  # Arabic
    return None


def _resolve_source_lang(req: TranslateRequest) -> str:
    """Pick source language: explicit request > job.source_lang > text guess > 'en'."""
    if getattr(req, "source_lang", None):
        return req.source_lang
    if getattr(req, "job_id", None):
        job = _get_job(req.job_id)
        if job and job.get("source_lang"):
            return job["source_lang"]
    return _guess_lang_from_text(getattr(req, "segments", None)) or "en"


def _resolve_translation_context(req, client, model_name: str, timeout: float,
                                 src_lang: str) -> Optional[dict]:
    """Cached auto-glossary context (theme + terms) for this job/target.

    Cache lives on the dub job dict (``job["translation_context"][target]``)
    and persists through the existing ``job_data`` JSON blob via ``_save_job``
    — no schema change. A transcript fingerprint keys the cache so an edited
    transcript re-extracts; an unchanged transcript costs zero LLM calls on
    re-translate. Any failure returns None — translation proceeds without
    context, never fails because of it. Blocking; run in an executor.
    """
    from services import translation_quality as tq

    texts = [(s.text or "") for s in req.segments]
    fp = tq.transcript_fingerprint(texts)
    job = _get_job(req.job_id) if getattr(req, "job_id", None) else None
    if job is not None:
        cached = (job.get("translation_context") or {}).get(req.target_lang)
        if isinstance(cached, dict) and cached.get("fingerprint") == fp:
            return cached
    ctx = tq.extract_context_sync(
        client, model_name, timeout,
        segment_texts=texts,
        source_lang=src_lang,
        target_lang=req.target_lang,
        source_name=LANG_NAMES.get(src_lang, src_lang),
        target_name=LANG_NAMES.get(req.target_lang, req.target_lang),
    )
    if ctx is None:
        return None
    ctx = {**ctx, "fingerprint": fp}
    if job is not None:
        try:
            job.setdefault("translation_context", {})[req.target_lang] = ctx
            _save_job(req.job_id, job)
        except Exception:  # noqa: BLE001 — persistence is best-effort
            logger.debug("translation context persist skipped", exc_info=True)
    return ctx


def _unload_nllb():
    """Release NLLB VRAM so TTS model can reload."""
    global _nllb_device, _nllb_model, _nllb_tokenizer
    import gc
    from services.model_manager import release_device_cache
    device = _nllb_device
    _nllb_model = None
    _nllb_tokenizer = None
    _nllb_device = None
    gc.collect()
    release_device_cache(device=device)


def _should_unload_nllb() -> bool:
    """Retain a warm local translator only when the accelerator has safe headroom."""
    override = os.environ.get("OMNIVOICE_UNLOAD_NLLB")
    if override is not None:
        return override.strip().lower() not in {"0", "false", "no", "off"}
    if _nllb_device != "cuda":
        return True
    try:
        import torch

        free_bytes, total_bytes = torch.cuda.mem_get_info()
        return total_bytes < 16 * 1024**3 or free_bytes < 8 * 1024**3
    except Exception:
        return True


@router.post("/dub/translate")
async def dub_translate(req: TranslateRequest):
    try:
        provider = (req.provider if req.provider else os.environ.get("TRANSLATE_PROVIDER", "google")).lower()
        from services import translation_engines

        if not translation_engines.get_engine(provider):
            return JSONResponse(
                status_code=400,
                content={"error": "Choose a supported translation engine."},
            )
        lang_code = TRANSLATE_CODES.get(req.target_lang, req.target_lang)
        api_key = os.environ.get("TRANSLATE_API_KEY", "")
        loop = asyncio.get_running_loop()
        src_lang = _resolve_source_lang(req)

        # Offline NLLB Transformer Translation
        if provider == "nllb":
            requested = [src_lang, req.target_lang, *(seg.target_lang for seg in req.segments if seg.target_lang)]
            resolved = {code: _nllb_language(code) for code in requested}
            unsupported = [code for code, language in resolved.items() if language is None]
            if unsupported:
                return JSONResponse(status_code=400, content={
                    "error": "NLLB does not support the requested language.",
                    "code": "unsupported_translation_language", "languages": unsupported,
                })
            flores_tgt = resolved[req.target_lang]
            flores_src = resolved[src_lang]

            def _translate_nllb():
                global _nllb_model, _nllb_tokenizer, _nllb_device
                import torch
                from services.model_manager import release_device_cache
                from transformers import AutoTokenizer, AutoModelForSeq2SeqLM

                if torch.cuda.is_available():
                    target_device = "cuda"
                elif hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
                    target_device = "mps"
                else:
                    target_device = "cpu"

                try:
                    if _nllb_tokenizer is None:
                        _nllb_tokenizer = _load_nllb_component(AutoTokenizer)
                    if _nllb_model is None:
                        _nllb_model = _load_nllb_component(AutoModelForSeq2SeqLM)
                        if target_device != "cpu":
                            try:
                                _nllb_model = _nllb_model.to(target_device)
                                _nllb_device = target_device
                            except Exception as e:
                                logger.warning("NLLB %s placement failed, falling back to CPU: %s", target_device, e)
                                _nllb_device = "cpu"
                        else:
                            _nllb_device = "cpu"
                except Exception as e:
                    logger.exception("NLLB model load failed")
                    return [{"id": seg.id, "text": seg.text, "error": f"Model load error: {str(e)}"} for seg in req.segments]

                from services.performance_profiles import translation_decode_defaults

                # Snapshot once so every segment and device fallback in this
                # job uses the same decoding effort even if preferences change.
                decode_options = translation_decode_defaults()
                def _generate_rows(rows, target_language):
                    global _nllb_device

                    _nllb_tokenizer.src_lang = flores_src
                    inputs = _nllb_tokenizer(
                        [seg.text for _, seg in rows],
                        return_tensors="pt",
                        padding=True,
                    )
                    if _nllb_device and _nllb_device != "cpu":
                        inputs = {key: value.to(_nllb_device) for key, value in inputs.items()}
                    forced_bos_token_id = _nllb_tokenizer.convert_tokens_to_ids(target_language)
                    try:
                        tokens = _nllb_model.generate(
                            **inputs,
                            forced_bos_token_id=forced_bos_token_id,
                            max_length=400,
                            **decode_options,
                        )
                    except (RuntimeError, NotImplementedError) as error:
                        if _nllb_device != "mps":
                            raise
                        logger.warning("MPS generate failed, retrying on CPU: %s", error)
                        _nllb_model.to("cpu")
                        _nllb_device = "cpu"
                        inputs = {key: value.to("cpu") for key, value in inputs.items()}
                        release_device_cache(device="mps")
                        tokens = _nllb_model.generate(
                            **inputs,
                            forced_bos_token_id=forced_bos_token_id,
                            max_length=400,
                            **decode_options,
                        )
                    decoded = _nllb_tokenizer.batch_decode(tokens, skip_special_tokens=True)
                    if len(decoded) != len(rows):
                        raise RuntimeError(
                            f"NLLB returned {len(decoded)} translations for {len(rows)} segments"
                        )
                    return decoded

                # A target-language BOS token is shared by a forward pass, so
                # group mixed-language rows first. Preserve request order in
                # the final response even though groups render independently.
                grouped: dict[str, list[tuple[int, object]]] = {}
                results_by_index: dict[int, dict] = {}
                for index, seg in enumerate(req.segments):
                    if not seg.text or not seg.text.strip():
                        results_by_index[index] = {"id": seg.id, "text": seg.text}
                        continue
                    target = resolved[seg.target_lang] if seg.target_lang else flores_tgt
                    grouped.setdefault(target, []).append((index, seg))

                # Beam search multiplies decoder memory per row. Keep the
                # effective hypothesis count bounded while still widening the
                # Fast path aggressively.
                beam_count = max(1, int(decode_options.get("num_beams", 1)))
                width = min(
                    _nllb_batch_size(),
                    max(1, _nllb_hypothesis_budget() // beam_count),
                )
                for target, rows in grouped.items():
                    for start in range(0, len(rows), width):
                        batch = rows[start : start + width]
                        try:
                            translated_texts = _generate_rows(batch, target)
                        except Exception as batch_error:
                            if len(batch) == 1:
                                index, seg = batch[0]
                                results_by_index[index] = {
                                    "id": seg.id,
                                    "text": seg.text,
                                    "error": str(batch_error),
                                }
                                continue
                            # A single unusually long row must not sink its
                            # neighbours. Clear a failed device allocation and
                            # retain the established per-segment degradation —
                            # on whichever accelerator the translator is on
                            # (the ladder above picks CUDA or MPS, and a
                            # CUDA-only flush missed the MPS case).
                            release_device_cache(device=_nllb_device)
                            logger.warning(
                                "NLLB batch of %d failed; retrying rows individually: %s",
                                len(batch),
                                batch_error,
                            )
                            for index, seg in batch:
                                try:
                                    translated_text = _generate_rows([(index, seg)], target)[0]
                                    results_by_index[index] = {"id": seg.id, "text": translated_text}
                                except Exception as row_error:
                                    results_by_index[index] = {
                                        "id": seg.id,
                                        "text": seg.text,
                                        "error": str(row_error),
                                    }
                            continue
                        for (index, seg), translated_text in zip(batch, translated_texts):
                            results_by_index[index] = {"id": seg.id, "text": translated_text}

                return [results_by_index[index] for index in range(len(req.segments))]

            translated = await loop.run_in_executor(_gpu_pool, _translate_nllb)
            if _should_unload_nllb():
                _unload_nllb()
            # Cinematic/Autofit refine + rate-ratio badges must run for NLLB too
            # (previously this returned before _maybe_cinematic, so a Cinematic
            # pick on NLLB silently produced plain Fast output). Unloading NLLB
            # first is fine — the refine LLM is a separate network provider.
            return await _maybe_cinematic(translated, req, src_lang, loop)

        # LLM translation — resolves through the LLM Skills registry: per-skill
        # "Dub translation" override → global active provider (Settings → LLM
        # Providers). The keys users configure + test in the app now actually
        # power this engine; the raw TRANSLATE_* env vars stay working as a
        # power-user override so pre-skills setups see zero behavior change.
        if provider == "openai":
            from services import llm_skills

            llm_timeout = llm_skills._default_timeout()
            handle = None
            try:
                handle = llm_skills.resolve_skill_client("dub_translation")
            except Exception:  # noqa: BLE001 — resolution must never 500 a translate
                logger.exception("dub_translation skill resolution failed; trying env fallback")
            if handle is not None:
                client = handle.client
                model_name = handle.model
                llm_timeout = handle.timeout
                # The provider-store key never touches env; resolve it so the
                # error scrubber below can redact it if a provider echoes it.
                try:
                    from services import llm_providers
                    api_key = llm_providers.resolve_api_key(
                        llm_skills.effective_provider("dub_translation")) or api_key
                except Exception:  # noqa: BLE001 — scrub-key resolution is best-effort
                    pass
            elif os.environ.get("TRANSLATE_BASE_URL") or api_key:
                # Legacy env-only setup (no provider configured in-app).
                from openai import OpenAI
                # max_retries=0: a 429 + long Retry-After must not let one segment's
                # SDK call sleep+retry and blow the overall translate wall time.
                client = OpenAI(base_url=os.environ.get("TRANSLATE_BASE_URL"),
                                api_key=api_key or "local", max_retries=0)
                model_name = os.environ.get("TRANSLATE_MODEL", "gpt-4o-mini")
            else:
                # Nothing configured anywhere — name the exact next step instead
                # of letting an empty key surface as a raw 401 per segment.
                try:
                    reason = llm_skills.resolve_skill("dub_translation").reason
                except Exception:  # noqa: BLE001
                    reason = None
                if reason == "disabled":
                    friendly = (
                        "The LLM translation engine is turned off — enable the "
                        "'Dub translation' skill in Settings → LLM Skills, or "
                        "pick another engine in the Engine dropdown."
                    )
                else:
                    friendly = (
                        "The LLM translation engine has no provider configured. "
                        "Add and test one in Settings → LLM Providers (it powers "
                        "this engine; route it per-skill in Settings → LLM "
                        "Skills), or set TRANSLATE_BASE_URL + TRANSLATE_API_KEY "
                        "+ TRANSLATE_MODEL. Or pick another engine in the "
                        "Engine dropdown."
                    )
                return JSONResponse(status_code=400, content={"error": friendly})

            from services import translation_quality as tq

            # Two-stage quality toggles. None (old clients) = ON — an LLM
            # translator is active on this branch by definition.
            auto_glossary_on = req.auto_glossary if req.auto_glossary is not None else True
            reflect_on = req.reflect if req.reflect is not None else True

            # Stage 1 — auto-glossary: ONE pass over the full transcript for a
            # theme summary + terminology map (cached per job/target/transcript),
            # merged with the user's manual glossary (user entries win) and
            # injected into every per-segment prompt below. With the toggle off
            # the manual glossary still rides along — that costs no extra call.
            auto_ctx = None
            if auto_glossary_on:
                auto_ctx = await loop.run_in_executor(
                    _cpu_pool, _resolve_translation_context,
                    req, client, model_name, llm_timeout, src_lang,
                )
            merged_terms = tq.merge_glossary(req.glossary, (auto_ctx or {}).get("terms"))
            context_extra = tq.context_clause((auto_ctx or {}).get("theme", ""), merged_terms)

            def _build_prompt(src_code: str, tgt_code: str) -> str:
                """Build a system prompt that resists hallucinations on small
                local LLMs. Three things matter:

                1. Use full language names (Hindi, German) not ISO codes —
                   tiny models read 'hi' as a greeting and drift.
                2. For non-Latin targets, name the required script explicitly
                   so the model can't fall back to phonetic Latin or another
                   target it knows better (Hindi → German is a common drift
                   we've actually observed).
                3. End with a strict format guard so the model can't prepend
                   'Translation:' or quote the output.
                """
                src_name = LANG_NAMES.get(src_code, src_code)
                tgt_name = LANG_NAMES.get(tgt_code, tgt_code)
                script_clause = ""
                info = LANG_REQUIRED_SCRIPT.get(tgt_code)
                if info:
                    script_name, _ = info
                    script_clause = (
                        f" The output MUST be written in {script_name} script "
                        f"only — do not use Latin/Roman letters, do not "
                        f"transliterate, do not output any other language."
                    )
                # #280 item 2 — regional dialect/vocabulary. Only applied when
                # the dialect belongs to the target language (a leftover
                # "es-AR" must not contaminate a French translation).
                dia_clause = ""
                if req.dialect and str(req.dialect).lower().startswith(str(tgt_code).lower()[:2]):
                    dia_clause = dialect_clause(req.dialect)
                base = (
                    f"You are a professional dubbing translator. "
                    f"Translate the user's text from {src_name} into "
                    f"{tgt_name}.{script_clause}{dia_clause} "
                    f"{translation_style_brief(req)} "
                    f"Reply ONLY with the translated {tgt_name} text, do not "
                    f"add quotes, notes, headers, explanations, or commentary."
                )
                # Auto-glossary theme + merged terminology (user terms win) —
                # every segment prompt carries the same brief, so recurring
                # names/terms come out consistent across the whole dub.
                if context_extra:
                    base = base + "\n\n" + context_extra
                return base

            def _translate_llm(seg):
                if not seg.text or not seg.text.strip():
                    return {"id": seg.id, "text": seg.text}
                tgt_code = seg.target_lang if seg.target_lang else req.target_lang
                system_msg = _build_prompt(src_lang, tgt_code)
                last_err = None
                # Up to 2 attempts: if the first response fails the
                # script-ratio gate (e.g. Hindi target but mostly Latin
                # output), retry once with a more emphatic instruction.
                for attempt in range(2):
                    sys_for_attempt = system_msg
                    if attempt == 1:
                        sys_for_attempt = (
                            system_msg
                            + " Your previous attempt produced output in the "
                            "wrong language or script. Output ONLY the "
                            f"{LANG_NAMES.get(tgt_code, tgt_code)} translation."
                        )
                    try:
                        res = client.chat.completions.create(
                            model=model_name,
                            temperature=0.2,  # less drift than default 1.0
                            timeout=llm_timeout,  # bound per call (OMNIVOICE_LLM_TIMEOUT, 45s default)
                            messages=[
                                {"role": "system", "content": sys_for_attempt},
                                {"role": "user", "content": seg.text},
                            ],
                        )
                        out_text = (res.choices[0].message.content or "").strip()
                        if not out_text:
                            last_err = "empty LLM response"
                            continue
                        if not _looks_like_target(out_text, tgt_code):
                            last_err = (
                                f"LLM output script_ratio={_script_ratio(out_text, tgt_code):.2f} "
                                f"below threshold for {tgt_code}"
                            )
                            logger.warning(
                                "translate %s: attempt %d wrong script (%s); retrying",
                                seg.id, attempt + 1, last_err,
                            )
                            continue
                        # Stage 2 — reflect pass: critique→rewrite the direct
                        # translation into natural spoken dialogue. Returns None
                        # on ANY failure/timeout/divergence, in which case the
                        # direct translation stands — refinement can never fail
                        # a segment that already translated fine. The belt-and-
                        # braces except keeps that guarantee even if the helper
                        # itself ever raised: without it, the enclosing attempt
                        # handler would burn a retry on a segment that already
                        # translated successfully.
                        if reflect_on:
                            polished = None
                            try:
                                polished = tq.reflect_translation_sync(
                                    client, model_name, llm_timeout,
                                    source_text=seg.text,
                                    direct_text=out_text,
                                    source_lang=src_lang,
                                    target_lang=tgt_code,
                                    target_name=LANG_NAMES.get(tgt_code, tgt_code),
                                    extra_clause="\n".join(filter(None, [context_extra, translation_style_brief(req)])),
                                )
                            except Exception as e:  # noqa: BLE001
                                logger.warning("reflect pass skipped for %s: %s",
                                               seg.id, e)
                            if polished:
                                return {"id": seg.id, "text": polished,
                                        "literal": out_text}
                        return {"id": seg.id, "text": out_text}
                    except Exception as e:
                        last_err = f"{type(e).__name__}: {e}"
                        logger.warning(
                            "translate %s: LLM attempt %d failed: %s",
                            seg.id, attempt + 1, e,
                        )
                # Both attempts failed — keep source text + flag error so the
                # frontend can surface "fallback to literal" warning. Scrub the
                # provider error: some OpenAI-compatible providers echo the key
                # or a user_id in the body, which must not reach the UI verbatim.
                from core.scrub import scrub_provider_error
                return {"id": seg.id, "text": seg.text,
                        "error": scrub_provider_error(last_err, api_key) or "llm-failed"}

            tasks = [loop.run_in_executor(_cpu_pool, _translate_llm, seg) for seg in req.segments]
            translated = await asyncio.gather(*tasks)
            translated.sort(key=lambda x: str(x["id"]))
            # provider="openai" is already an LLM translation — _maybe_cinematic
            # skips the reflect/adapt re-refine (already_llm) but still stamps
            # rate-ratio badges and runs the bounded Autofit fit pass. Before
            # this it returned here, so Cinematic/Autofit on the LLM engine did
            # nothing.
            return await _maybe_cinematic(translated, req, src_lang, loop, already_llm=True)

        # Offline Argos Translate
        if provider == "argos" or provider == "libretranslate":
            try:
                import argostranslate  # noqa: F401
            except ImportError:
                # Single-source the install command from the engine registry so
                # this 400 and the proactive Install button in the Engine
                # selector can never drift (see translation_engines.install_command).
                from services.translation_engines import install_command
                cmd = install_command("argos") or "uv pip install argostranslate"
                friendly = (
                    f"The '{provider}' translation engine needs the optional "
                    f"`argostranslate` Python package, which isn't installed in "
                    f"this backend. Install it with `{cmd}` "
                    f"and restart the server, or "
                    f"switch the Engine dropdown to another provider."
                )
                return JSONResponse(status_code=400, content={"error": friendly})
            # The package imports without its native dep; the *translator*
            # needs CTranslate2, whose library is rejected outright by kernels
            # that refuse an executable stack (#692). Repair it (a one-bit ELF
            # patch), and if that is impossible say so in one actionable 400
            # instead of the opaque 500 every segment used to produce.
            try:
                from core.execstack import ensure_ctranslate2_loadable

                ensure_ctranslate2_loadable()
            except Exception as e:  # noqa: BLE001 — repair must not block translation
                logger.debug("exec-stack repair unavailable (%s) — continuing", e)
            try:
                import argostranslate.translate  # noqa: F401
            except Exception as e:  # noqa: BLE001 — OSError here, not ImportError
                friendly = (
                    f"The '{provider}' engine's CTranslate2 runtime could not be "
                    "loaded in this backend."
                    + " Switch the Engine dropdown to NLLB (local) or an online "
                    "provider, or reinstall the backend, then retry."
                )
                return JSONResponse(status_code=400, content={"error": friendly, "detail": {"code": "argos_runtime_unavailable", "message": friendly}})

            target_codes = list(dict.fromkeys(
                seg.target_lang if seg.target_lang else req.target_lang
                for seg in req.segments
            ))
            try:
                pack_status = translation_engines.argos_pack_status(src_lang, target_codes)
            except (ImportError, ValueError) as exc:
                return JSONResponse(status_code=422, content={"error": str(exc)})
            missing_packs = [
                pair for pair in pack_status["pairs"] if not pair["installed"]
            ]
            if missing_packs:
                pairs = ", ".join(
                    f'{pair["source_lang"]} → {pair["target_lang"]}'
                    for pair in missing_packs
                )
                return JSONResponse(
                    status_code=409,
                    content={
                        "error": f"Install the Argos language pack for {pairs} before translating.",
                        "code": "argos_pack_missing",
                        "pairs": missing_packs,
                    },
                )

            def _translate_argos():
                import argostranslate.translate

                from_code = pack_status["source_lang"]

                results = []
                for seg in req.segments:
                    try:
                        if not seg.text or not seg.text.strip():
                            results.append({"id": seg.id, "text": seg.text})
                            continue
                        to_code = seg.target_lang if seg.target_lang else req.target_lang
                        to_code = translation_engines.argos_lang_code(to_code)
                        translated_text = (
                            seg.text
                            if from_code == to_code
                            else argostranslate.translate.translate(seg.text, from_code, to_code)
                        )
                        results.append({"id": seg.id, "text": translated_text})
                    except Exception as e:
                        results.append({"id": seg.id, "text": seg.text, "error": str(e)})
                return results

            translated = await loop.run_in_executor(_cpu_pool, _translate_argos)
            # Argos is the DEFAULT engine — routing it through _maybe_cinematic is
            # the headline fix: a user who picks Cinematic/Autofit on Argos now
            # gets the LLM refine + fit pass (and rate-ratio badges in Fast mode)
            # instead of silent plain-Fast output.
            return await _maybe_cinematic(translated, req, src_lang, loop)

        # Legacy / API Deep_Translator logic.
        # Preflight the optional `deep_translator` dep once so we fail with a
        # single actionable error instead of N identical per-segment
        # ModuleNotFoundErrors that flood the UI's error badge.
        try:
            import deep_translator  # noqa: F401
        except ImportError:
            # Same single-source install command as the Engine selector's Install
            # button (translation_engines.install_command) — google/deepl/
            # microsoft/mymemory all share the deep_translator package.
            from services.translation_engines import install_command
            cmd = install_command(provider) or "uv pip install deep_translator"
            friendly = (
                f"The '{provider}' translation engine needs the optional "
                f"`deep_translator` Python package, which isn't installed in "
                f"this backend. Install it with `{cmd}` "
                f"and restart the server, or "
                f"switch the Engine dropdown to Argos (local, bundled), NLLB "
                f"(local, heavier), or OpenAI (LLM)."
            )
            return JSONResponse(status_code=400, content={"error": friendly})

        src_arg = TRANSLATE_CODES.get(src_lang, src_lang) or "auto"

        _proxies = {"http": None, "https": None}
        _deepl_key = os.environ.get("DEEPL_API_KEY") or api_key
        _msft_key = os.environ.get("MICROSOFT_API_KEY") or api_key

        def _build_translator(src, tgt):
            if provider == "deepl":
                from deep_translator import DeeplTranslator
                tr = DeeplTranslator(api_key=_deepl_key, source=src, target=tgt, use_free_api=False)
                _custom = os.environ.get("DEEPL_BASE_URL")
                if _custom:
                    tr._base_url = _custom.rstrip("/") + "/"
                return tr
            if provider == "mymemory":
                from deep_translator import MyMemoryTranslator
                return MyMemoryTranslator(source=src, target=tgt, proxies=_proxies)
            if provider == "microsoft":
                from deep_translator import MicrosoftTranslator
                tr = MicrosoftTranslator(api_key=_msft_key, source=src, target=tgt, proxies=_proxies)
                _custom = os.environ.get("MICROSOFT_BASE_URL")
                if _custom:
                    tr._base_url = _custom.rstrip("/") + "/translate?api-version=3.0"
                return tr
            from deep_translator import GoogleTranslator
            return GoogleTranslator(source=src, target=tgt, proxies=_proxies)

        def _translate_single(seg):
            seg_lc = (
                TRANSLATE_CODES.get(seg.target_lang, seg.target_lang)
                if seg.target_lang else lang_code
            )
            if not seg.text or not seg.text.strip():
                return {"id": seg.id, "text": seg.text}
            last_err = None
            # Try: (src_arg, tgt) → retry once → fall back to (auto, tgt).
            for attempt, src in enumerate([src_arg, src_arg, "auto"]):
                try:
                    out = _build_translator(src, seg_lc).translate(seg.text)
                    output_error = _translation_output_error(out)
                    if output_error is None:
                        return {"id": seg.id, "text": out}
                    last_err = output_error
                except Exception as e:
                    last_err = f"{type(e).__name__}: {e}"
                    logger.warning(
                        "translate attempt %d %s->%s (provider=%s) failed: %s",
                        attempt + 1, src, seg_lc, provider, e,
                    )
                    time.sleep(0.25 * (attempt + 1))
            logger.error("translate %s -> %s gave up (provider=%s): %s", src_arg, seg_lc, provider, last_err)
            # Scrub before it reaches the UI — DeepL/Microsoft errors can echo
            # the API key (same class as the OpenAI user_id leak).
            from core.scrub import scrub_provider_error
            return {"id": seg.id, "text": seg.text,
                    "error": scrub_provider_error(last_err, _deepl_key or _msft_key or api_key) or "unknown"}

        tasks = [loop.run_in_executor(_cpu_pool, _translate_single, seg) for seg in req.segments]
        translated = await asyncio.gather(*tasks)
        translated.sort(key=lambda x: str(x["id"]))

        return await _maybe_cinematic(
            translated, req, src_lang, loop,
        )
    except Exception as e:
        from core.public_errors import public_failure

        error = public_failure(
            logger,
            "Translation request failed",
            e,
            response="Translation failed; check the backend log for details.",
            traceback=True,
        )
        return JSONResponse(status_code=500, content={"error": error})


def _stamp_duration_plan(rows, req) -> None:
    """Attach a pre-synthesis duration-plan verdict to every row (in place).

    Pure planning (services/duration_planner.py): estimate the natural
    speech duration of each row's FINAL text — self-calibrated from this
    job's already-synthesized segments when possible — and classify it
    against slot + borrowable gap using fit_planner's own caps. The verdict
    rides on the row as ``plan`` so the segment table can badge tight/
    impossible segments BEFORE any GPU time is spent. Informational only —
    generation is never blocked. Never raises.
    """
    try:
        from services.duration_planner import calibration_from_job, classify_segments

        timed = [
            s for s in req.segments
            if getattr(s, "start", None) is not None and getattr(s, "end", None) is not None
        ]
        if not timed:
            return  # old client — no timeline info, no plan
        text_by_id = {str(r["id"]): (r.get("text") or "") for r in rows}
        segs = sorted(
            (
                {
                    "id": str(s.id),
                    "start": float(s.start),
                    "end": float(s.end),
                    "text": text_by_id.get(str(s.id), ""),
                }
                for s in timed
            ),
            key=lambda d: d["start"],
        )
        calib = None
        total_dur = 0.0
        if getattr(req, "job_id", None):
            job = _get_job(req.job_id)
            if job:
                calib = calibration_from_job(job, req.target_lang)
                total_dur = float(job.get("duration") or 0.0)
        verdicts = {
            v["id"]: v
            for v in classify_segments(
                segs, req.target_lang, calibration=calib, total_dur_s=total_dur,
            )
        }
        for row in rows:
            v = verdicts.get(str(row["id"]))
            if v is None or row.get("error") or not (row.get("text") or "").strip():
                continue
            row["plan"] = {
                "status": v["status"],
                "est_dur_s": v["est_dur_s"],
                "available_s": v["available_s"],
                "est_overrun_s": v["est_overrun_s"],
                "calibrated": v["calibrated"],
            }
    except Exception as e:  # noqa: BLE001 — planning must never sink a translate
        logger.debug("duration-plan stamping skipped: %s", e)


async def _apply_condense_pass(rows, req, loop) -> None:
    """Opt-in LLM condensation for ``impossible`` rows (in place).

    Fans ``condense_for_slot`` out on the CPU pool under the same wall-clock
    budget the cinematic phase uses, so a slow LLM can't hang the translate.
    Suggestions land as ``plan.suggested_text`` — the user applies them per
    segment; the row's ``text`` is never touched here. Every failure mode
    (no LLM, LLM error, divergent reply, budget) degrades to no suggestion.
    """
    targets = [
        row for row in rows
        if (row.get("plan") or {}).get("status") == "impossible"
        and (row.get("text") or "").strip() and not row.get("error")
    ]
    if not targets:
        return
    try:
        from services.duration_planner import calibration_from_job, condense_for_slot

        calib = None
        if getattr(req, "job_id", None):
            job = _get_job(req.job_id)
            if job:
                calib = calibration_from_job(job, req.target_lang)
        source_by_id = {str(s.id): s.text for s in req.segments}
        sem = asyncio.Semaphore(int(os.environ.get("OMNIVOICE_LLM_CONCURRENCY", "6")))

        async def _one(row):
            async with sem:
                res = await loop.run_in_executor(
                    _cpu_pool,
                    lambda: condense_for_slot(
                        row["text"],
                        available_s=float(row["plan"]["available_s"]),
                        target_lang=req.target_lang,
                        source_text=source_by_id.get(str(row["id"])),
                        calibration=calib,
                    ),
                )
            if res.get("applied") and res.get("text"):
                row["plan"]["suggested_text"] = res["text"]
                row["plan"]["suggested_est_dur_s"] = res.get("est_dur_s")

        tasks = [asyncio.ensure_future(_one(row)) for row in targets]
        budget = _cinematic_budget()
        done, pending = await asyncio.wait(
            tasks, timeout=budget if budget and budget > 0 else None,
        )
        for task in pending:
            task.cancel()  # abandon the executor thread (#730 pattern)
        for task in done:
            exc = task.exception()
            if exc is not None:
                logger.warning("condense pass segment failed: %s", exc)
    except Exception as e:  # noqa: BLE001 — a suggestion pass must never sink a translate
        logger.warning("condense pass skipped: %s", e)


async def _finalize_duration_plan(rows, req, loop) -> None:
    """Stamp plan verdicts on the FINAL row texts, then (opt-in) condense."""
    _stamp_duration_plan(rows, req)
    if getattr(req, "condense", False):
        await _apply_condense_pass(rows, req, loop)


def _stamp_predicted_rate_ratio(translated, req) -> None:
    """Stamp a predicted ``rate_ratio`` on every row that has a known slot.

    No LLM needed — just the per-language CPS table from ``services/speech_rate``.
    The UI's ``seg-rate-badge`` reads it (Fast mode included) to show which
    segments will compress hard at generation time, so users can edit text or
    pick a heavier quality. Mutates ``translated`` in place; never raises.
    """
    try:
        from services.speech_rate import rate_ratio as _predict_rate_ratio
        slots = {str(s.id): getattr(s, "slot_seconds", None) for s in req.segments}
        for row in translated:
            slot = slots.get(str(row["id"]))
            text = (row.get("text") or "").strip()
            if slot and text and not row.get("error"):
                row["rate_ratio"] = round(
                    _predict_rate_ratio(text, float(slot), req.target_lang), 3,
                )
    except Exception as e:
        logger.debug("non-LLM rate_ratio prediction skipped: %s", e)


async def _apply_fit_pass(rows, req, slots_by_id, source_by_id, quality, loop, deadline) -> None:
    """Run the Autofit slot-fit pass over ``rows`` concurrently, in place.

    Bounded by ``deadline`` (shared with the cinematic refine) so a slow /
    rate-limited LLM can't spin the fit pass per-segment unbounded — the old
    behavior, which ran one blocking ``adjust_for_slot`` per segment in the
    merge loop, outside any budget. Segments still running at the deadline keep
    their current text and get ``rate_error='fit-budget'``. Only rows with a
    slot + text + no prior error participate.
    """
    strict = quality in ("autofit", "agent")
    items = []
    for row in rows:
        seg_id = str(row["id"])
        slot = slots_by_id.get(seg_id)
        text = row.get("text") or ""
        if slot and text and not row.get("error"):
            items.append((seg_id, text, float(slot), req.target_lang,
                          source_by_id.get(seg_id), strict))
    if not items:
        return
    try:
        from services.speech_rate import adjust_for_slot_many
        fits = await adjust_for_slot_many(
            items, executor=_cpu_pool, deadline=deadline, loop=loop,
        )
    except Exception as e:
        logger.warning("rate-fit pass skipped: %s", e)
        return
    for row in rows:
        f = fits.get(str(row["id"]))
        if not f:
            continue
        if f.get("text"):
            row["text"] = f["text"]
        if f.get("rate_ratio") is not None:
            row["rate_ratio"] = f["rate_ratio"]
        if f.get("error"):
            row["rate_error"] = f["error"]


async def _maybe_cinematic(translated, req, src_lang, loop, *, already_llm=False):
    """Post-process a literal translation into Cinematic/Autofit output.

    Runs for EVERY provider now (Argos/NLLB/Google/…/OpenAI). The three
    LLM-independent branches (nllb/argos) and the openai branch used to return
    *before* reaching this, so a Cinematic/Autofit pick on them — including the
    DEFAULT Argos engine — silently produced plain Fast output with a success
    toast. Fast mode still returns the plain translation (plus rate-ratio badges).

    ``already_llm`` (provider="openai"): the translation was itself produced by
    an LLM, so the REFLECT+ADAPT *re*-refine is skipped, but the bounded Autofit
    fit pass + rate-ratio stamping still run, and the dialect the translate
    prompt already baked in is reported as applied.
    """
    quality = (getattr(req, "quality", None) or "fast").lower()

    _stamp_predicted_rate_ratio(translated, req)

    # #280 item 2 — regional dialect hint, guarded against a stale dialect from
    # another language. For already_llm the initial translate prompt already
    # applied it, so it's reported applied in the Fast-shape base too.
    dialect_hint = ""
    _dialect = getattr(req, "dialect", None)
    if _dialect and str(_dialect).lower().startswith(str(req.target_lang).lower()[:2]):
        dialect_hint = dialect_clause(_dialect)

    base = {"translated": translated, "target_lang": req.target_lang, "source_lang": src_lang,
            "quality_used": "fast",
            **_dialect_flags(req, applied=(already_llm and bool(dialect_hint)))}

    # Fast (and anything unrecognised) returns the plain translation unchanged
    # (plus the pre-synthesis duration-plan badges — no LLM needed for those).
    if quality not in ("cinematic", "autofit", "agent"):
        await _finalize_duration_plan(translated, req, loop)
        return base

    source_by_id: dict[str, str] = {str(s.id): s.text for s in req.segments}
    slots_by_id = {
        str(s.id): getattr(s, "slot_seconds", None)
        for s in req.segments
        if getattr(s, "slot_seconds", None)
    }

    # One wall-clock deadline shared by the whole LLM phase (refine + fit), so a
    # slow/rate-limited provider can't run either pass unbounded. <=0 disables.
    budget = _cinematic_budget()
    deadline = (loop.time() + budget) if budget and budget > 0 else None

    # provider="openai": already an LLM translation → skip REFLECT+ADAPT, keep
    # the rate-ratio badges, still run the bounded fit pass.
    if already_llm:
        merged = []
        for row in translated:
            # A reflect-pass row already carries its pre-polish direct
            # translation as `literal` — keep it instead of clobbering.
            out = {"id": row["id"],
                   "text": row.get("text", "") or "",
                   "literal": row.get("literal") or row.get("text", "") or ""}
            if row.get("error"):
                out["error"] = row["error"]
            if "rate_ratio" in row:
                out["rate_ratio"] = row["rate_ratio"]
            merged.append(out)
        await _apply_fit_pass(merged, req, slots_by_id, source_by_id, quality, loop, deadline)
        # Plan AFTER the fit pass — verdicts must describe the final text.
        await _finalize_duration_plan(merged, req, loop)
        return {"translated": merged, "target_lang": req.target_lang,
                "source_lang": src_lang, "quality_used": quality,
                **_dialect_flags(req, applied=bool(dialect_hint))}

    # Non-LLM provider → the reflect/adapt refine needs a separately-configured
    # LLM (Settings → LLM Providers). Without one, degrade to Fast with a flag.
    if not cinematic_available():
        logger.warning("%s requested but no LLM configured — returning Fast result.", quality)
        base["cinematic_skipped"] = "no-llm-configured"
        await _finalize_duration_plan(translated, req, loop)
        return base

    directions: dict[str, str] = {
        str(s.id): s.direction
        for s in req.segments
        if getattr(s, "direction", None)
    }
    pairs = []
    passthrough_index = {}
    for row in translated:
        seg_id = str(row["id"])
        literal = row.get("text", "") or ""
        if row.get("error") or not literal.strip():
            passthrough_index[seg_id] = row  # keep as-is, LLM won't help
            continue
        pairs.append((seg_id, source_by_id.get(seg_id, ""), literal))

    if not pairs:
        await _finalize_duration_plan(translated, req, loop)
        return base

    refined = await cinematic_refine_many(
        pairs,
        source_lang=src_lang,
        target_lang=req.target_lang,
        glossary=req.glossary,
        directions=directions,
        dialect_hint="\n".join(filter(None, [dialect_hint, translation_style_brief(req)])),
        executor=_cpu_pool,
    )
    refined_by_id = {r["id"]: r for r in refined}

    merged = []
    for row in translated:
        seg_id = str(row["id"])
        if seg_id in passthrough_index:
            merged.append(row)
            continue
        r = refined_by_id.get(seg_id)
        if r is None:
            merged.append(row)
            continue
        out = {
            "id": row["id"],
            "text": r["text"],
            "literal": r["literal"],
            "critique": r.get("critique", ""),
        }
        # `degraded` ≠ `error`: a degraded row fell back to its literal text
        # (reflect/adapt skipped — rate limit, budget, divergence) but is fully
        # usable, so the fit pass, condense pass, and duration planning below
        # must still run on it. Marking these `error` used to (a) skip all
        # three passes — overlong lines then hit heavy time-compression at mix,
        # audibly degrading the dub — and (b) make the UI report "N/N segments
        # failed" for a translate that succeeded.
        if r.get("degraded"):
            out["degraded"] = r["degraded"]
        if r.get("error"):
            out["error"] = r["error"]
        merged.append(out)

    # Phase 4.4 speech-rate fit pass — now concurrent + bounded (see helper).
    await _apply_fit_pass(merged, req, slots_by_id, source_by_id, quality, loop, deadline)

    # Plan AFTER the fit pass — verdicts must describe the final text.
    await _finalize_duration_plan(merged, req, loop)

    return {
        "translated": merged,
        "target_lang": req.target_lang,
        "source_lang": src_lang,
        "quality_used": quality,
        **_dialect_flags(req, applied=bool(dialect_hint)),
    }


def translation_style_brief(req) -> str:
    instructions = (getattr(req, "translation_instructions", None) or "").strip()
    return ("User translation style brief (tone and wording only; preserve meaning, timing and output format): "
            + json.dumps(instructions, ensure_ascii=False)) if instructions else ""


@router.post("/dub/agent-fit")
async def dub_agent_fit(req: AgentFitRequest):
    """Rewrite rendered lines from real duration evidence.

    Synthesis stays in the normal Dubbing pipeline. The client renders each
    candidate, measures it, and may request one more bounded correction.
    """
    from services import llm_skills
    from services.speech_rate import adjust_for_measured_slot_many

    readiness = llm_skills.resolve_skill("slot_fitting")
    if not readiness.ready:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "llm_skill_unavailable",
                "skill": "slot_fitting",
                "reason": readiness.reason or "unavailable",
            },
        )

    items = [
            (
                segment.id,
                segment.text,
                segment.slot_seconds,
                segment.measured_seconds,
                req.target_lang,
                segment.source_text,
                segment.context_before,
                segment.context_after,
            )
            for segment in req.segments
        ]
    budget = _cinematic_budget()
    try:
        call = adjust_for_measured_slot_many(items, executor=_cpu_pool, translation_instructions=req.translation_instructions)
        rows = await asyncio.wait_for(call, timeout=budget) if budget and budget > 0 else await call
    except asyncio.TimeoutError:
        rows = {
            segment.id: {
                "text": segment.text,
                "changed": False,
                "measured_seconds": round(segment.measured_seconds, 3),
                "target_seconds": round(segment.slot_seconds, 3),
                "measured_ratio": round(
                    segment.measured_seconds / max(segment.slot_seconds, 0.001), 3
                ),
                "error": "fit-budget",
            }
            for segment in req.segments
        }
    return {
        "target_lang": req.target_lang,
        "segments": [
            {"id": segment.id, **rows[str(segment.id)]}
            for segment in req.segments
        ],
    }
