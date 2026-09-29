# Language selection

The Electron language picker shows a representative flag (or globe), localized name,
native name when different, and language code. Search matches all names and codes,
the representative flag's country name/code, and flag emoji,
ignores case and accents, and runs locally without a typing delay or network requests.
The selected value remains the existing canonical language name in projects and requests.

![Responsive language picker with supported choices first](assets/language-picker.png)

The screenshot uses fixture model metadata to demonstrate both enabled and disabled rows.

## Selection and accessibility

The popup adapts to one, two or three columns. Auto and up to four recent enabled
choices lead the list; remaining enabled languages precede unavailable languages.
Unavailable results remain searchable and have a model-specific disabled explanation.
Recents stay on this device and never override model support.

Opening focuses search. Up/Down follow reading order across enabled choices; Enter
selects and Escape cancels. Alt+Left/Right also navigate, respecting RTL; ordinary
Left/Right and Home/End keep their native text-editing behavior. IME confirmation
does not select a language. Closing restores focus; Tab exits the popup.
The list exposes selected/disabled states, result counts, positions and total size.
Virtualization keeps the active option mounted even when scrolled outside the viewport.

## Model compatibility

Clone, Design, Stories, Audiobook, Batch Dub and dub segment output pickers use
the selected TTS model's declared language names. Source recordings, translation
targets and existing dub track navigation remain independent of TTS; the shared
search surface improves those pickers too. A saved incompatible language stays
visible with a warning rather than being silently changed. Known incompatible local
requests are checked before clone/design, long-form, batch and dub synthesis;
engine-side validation remains authoritative.

Finite engines use their adapter's language declarations. Native OmniVoice adapters
use the bundled language vocabulary. Each curated MLX-Audio model declares the
languages its model card documents (`MLXAudioBackend.CURATED_MODEL_LANGUAGES`, with
sources in the code): CSM, Dia, Chatterbox and MeloTTS-English are English-only,
Qwen3-TTS covers 10 languages and OuteTTS 1.0 covers 23. The same list drives the
picker and the synthesis guard. Kokoro reads its tables from the installed package
(including `dict(...)` declarations) without importing MLX or loading weights, and
falls back to its declared set if those tables cannot be read. Only a custom
MLX-Audio repo is explicitly unknown, which is not a claim of universal support. Worker language
metadata is not yet advertised by the runtime API, so local lists never restrict a
remote worker. Loading and failed discovery have separate labels. No models are
downloaded or switched by selecting a language. Auto retains each engine's existing
automatic/default behavior; it does not add support for another language.

## Maintenance and validation

`SearchableSelect` owns an opt-in virtualized surface; existing classic consumers
retain their behavior. `language-codes.json` mirrors `LANG_NAME_TO_ID` and is guarded
by a backend parity test. User-facing labels live in both Electron locale trees.

Run picker, language-options, TTS-language and affected generation unit tests;
`tests/test_engine_language_options.py` with `HF_HUB_OFFLINE=1` and an empty cache;
Electron typecheck and locale checks. `electron/tests/language-picker-smoke.mjs`
checks browser layout, flags, focus return, disabled selection, active-option mounting,
and measures filtering with English/German/Arabic and desktop platform bridge fixtures.
Set `VOICESTUDIO_UI_URL` to the smoke server and optionally `PLAYWRIGHT_CHANNEL`.
These bridge fixtures do not replace native OS or manual screen-reader testing.

Partial dub regeneration checks the selected segments in the renderer. The backend
validates its final local render set, including segments promoted because their
cache is missing, corrupt or from an incompatible timing format, before synthesis.

MeloTTS English keeps its optional `g2p_en` package as a manual dependency; it is
not bundled. Generation reports that prerequisite when absent. Install its NLTK data explicitly in the same Python environment:
`python -m nltk.downloader averaged_perceptron_tagger averaged_perceptron_tagger_eng cmudict`.
Generation checks local resources before importing the text frontend and never
downloads missing NLTK data. Existing NLTK search directories and the app's
`nltk_data` directory are reused.

Qwen3-TTS receives the full language names required by its
[upstream adapter](https://github.com/Blaizzy/mlx-audio/blob/main/mlx_audio/tts/models/qwen3_tts/qwen3_tts.py),
including when users select an ISO code or region alias.

Engine rendering and warm-up hold a residency lease, including dub and batch calls.
Switching engines defers unloading any model still rendering; a retired model is
released after its last render finishes.

When TorchCodec is unavailable, audio decoding uses libsndfile first and the
already-installed FFmpeg for compressed containers such as M4A and AAC. Channels
and sample rates are retained; decoding does not download tools.
