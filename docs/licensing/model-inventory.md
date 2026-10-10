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

## Shared disclosure chain

`backend/config/models.yaml` remains the operational catalogue. The existing
`model_licenses.json` is schema v2; its original 56 identities and seven dynamic
families remain intact. The upgrade preserves observed terms, distinguishes
evidence revisions from runtime revisions, and keeps incomplete component and
variant provenance explicit. It does not turn the legacy false commercial flag
into a blanket non-commercial label.

`GET /models` adds `license_info` to the existing catalogue rows. The maintained
Electron Model Manager renders these notices alongside the same models and
keeps commercial model use, output use, redistribution and voice consent
separate. Original upstream licence labels and evidence are data, not promises
that all selected assets or activities are covered.

`scripts/generate_model_license_data.py` generates
[`model-license-data.json`](model-license-data.json) from the same registry and
catalogue without importing engines or downloading models. It includes all 56
inventory rows, including dependencies outside the visible catalogue, the
dynamic families, registry version/digest and source provenance. The website
vendors these exact bytes as `public/model-license-data.json`, with the immutable
app implementation commit, path and SHA-256 in `contract/model-licenses/source.json`.
Its existing `scripts/introspect-reference.py --licenses-only` checks this input
and generates legal-source provenance; `--verify-source` checks upstream byte
parity in CI. This legal-source commit is separate from both the registry's
audited inventory source and the website's capability submodule pin. The website
must not replace an unknown assessment with a handwritten permission badge.

```sh
python scripts/generate_model_license_data.py
python scripts/generate_model_license_data.py --check
```

The initial projection is disclosure-only. Every production record has missing
exact-file and/or terms evidence; no commercial clearance or fully reviewed
production selection is asserted.

## Optional reviewed files, not global enforcement

The additive [reviewed-file workflow](model-review-workflow.md) prepares an exact
local plan and rejects incomplete evidence before acknowledging or transferring
files. A qualifying plan binds its source, revisions, documents, artifact hashes
and notice version to a local receipt. Verified files remain in a separate cache
and are not activated for ordinary model use.

Existing installation, repair, model packs, recommendations, first-use SDK
downloads and remote workers remain unchanged. The legacy per-engine booleans
are not migrated into versioned receipts or treated as provider approval.
Complete upstream evidence, selected-component closure, redistribution notices,
provider-specific access handling and integration at all runtime boundaries
remain unimplemented requirements, not properties of this preview.

