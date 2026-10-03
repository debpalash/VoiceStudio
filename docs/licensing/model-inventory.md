# Model licence inventory

`backend/config/model_licenses.json` records 56 configured model repositories and
seven unresolved asset families. Each record has an upstream licence label,
commercial-use review flag, credit text, source/evidence links and review notes.
Pinned model-card revisions identify the evidence inspected; they do not pin
runtime downloads or establish that future model revisions have the same terms.

`commercial_use: true` means the recorded distribution scope has been reviewed
and cleared, with a reviewer, date, revision and scope. No record currently makes
that claim. `false` covers both known non-commercial terms (`noncommercial`) and
unfinished review (`unreviewed`); it does **not** mean every listed model forbids
commercial use. Model-card labels such as MIT and Apache-2.0 remain upstream
metadata until the relevant weights, conversion and component terms are checked.
The initial repository-name credits identify sources; they are not a substitute
for completing each upstream's required attribution text and licence notices.

The four non-commercial records cite the inspected OmniVoice, OmniVoice-GGUF,
NLLB and Llama-OuteTTS sources documented in [model credits](../model-credits.md).
Other records explicitly retain their review gaps. In particular, a gated or
missing card is not permission; SenseVoice's configured identifier is a
ModelScope model, and the failed matching Hugging Face query is not evidence of
its terms. SDK-selected alignment, VAD, watermark, separation and translation
assets need individual resolved identities and terms before clearance. Arbitrary
user models and environment overrides cannot inherit a family's clearance.

Run the offline inventory check:

```sh
python scripts/check_model_licenses.py
pytest tests/test_model_licenses.py
```

The normal CI pytest job runs the same gate. It fails for missing catalogue
records, including nested dependencies, and literal repository defaults/download
arguments, checkpoint/environment defaults and curated revision identifiers in
backend Python and the maintained `omnivoice/models` runtime. It also rejects incomplete or duplicate records
and commercial flags inconsistent with the review state. The static scan cannot
resolve SDK-internal mappings, arbitrary generated identifiers, external servers
or user files; those remain explicitly unresolved asset families. Native
audio.cpp/FFmpeg program repositories are excluded from the model scan and need
their separate dependency notices.

This inventory does not yet enforce acceptance or Pro exclusions at runtime.
Versioned acceptance, resolved-asset checks, backend entitlement enforcement and
complete redistribution notices remain necessary before a paid release. The
existing free application's engine behaviour is unchanged by this inventory.
