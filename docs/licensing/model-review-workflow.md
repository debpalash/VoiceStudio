# Model notices and reviewed-file preview

This is a local-first notice and review foundation. **No production model is
currently review-ready.** Licence acceptance (below) is enforced when a model is
used; installation paths are unchanged. Nothing here is commercial,
redistribution, copyright or voice-rights clearance. Existing models and engine
preferences are preserved.

## Licence acceptance before use

VoiceStudio is not the owner or licensor of any model and cannot grant model
access or rights. Every model whose licence category is not **Commercial use
allowed** (MIT, Apache-2.0, BSD, ISC, CC0, CC-BY) must be accepted before it can
be used, including models that are already downloaded. That covers
non-commercial, conditional and unidentified licences, so the default OmniVoice
model needs one acceptance. Accepting confirms that you have the rights the
model's licence requires for your use, for example non-commercial use only or a
separate licence from the rights holder for commercial use.

- Until accepted, the model card shows **Accept licence to use** and every
  feature that would load the model (generation, transcription, dictation,
  dubbing, translation, batch, workflows, API) stops with an acceptance dialog
  instead of loading it. Nothing is reinstalled.
- Acceptance is recorded locally and separately for each model: when you
  accepted or withdrew, the fingerprint of the exact terms you were shown, and
  the app version. The last 20 actions are kept as history, shown under
  **Technical details** in the licence dialog.
- The fingerprint covers everything the dialog shows about the terms: the
  licence, rights holder, category, commercial, output, redistribution and
  voice fields, notes, variant terms, links, evidence and model versions, and
  the hashes of stored licence documents. If any of these change in a later
  app update, the model is blocked again and the dialog says which parts
  changed. A re-check that changes nothing (only the checked date) does not ask
  again. **Withdraw acceptance** blocks the model until you accept again.
- When a feature needs several unaccepted models at once (OmniVoice and its
  tokenizer, for example), the dialog shows each model's full licence and asks
  for a separate confirmation for each one.
- An earlier Supertonic-3 licence acceptance carries over.
- API and headless users get HTTP 403 with
  `{"code": "model_licence_required", "models": [...]}` listing each model's
  `repo_id` and terms `fingerprint`. Accept after reviewing the terms with
  `POST /models/licenses/accept` and body
  `{"repo_id": "...", "fingerprint": "...", "accepted": true}`;
  `GET /models/licenses/acceptance/{repo_id}` reports the state
  (`not_required`, `accepted`, `not_accepted`, `withdrawn` or `terms_updated`),
  `GET /models/licenses/details/{repo_id}` adds the full disclosure and history,
  and `POST /models/licenses/revoke` withdraws it. Accepting and withdrawing
  need admin on a shared server.

## In Model Manager

Each model card has a **Licence:** button with a coloured icon. Its shape and colour show
the category of the *declared* licence, with the licence name, category and
review state in its tooltip and accessible name:

| Icon | Category | Declared licences |
|---|---|---|
| Green briefcase | Commercial use allowed | MIT, Apache-2.0, BSD, ISC, CC0, CC-BY |
| Amber briefcase with `!` | Commercial use with conditions | OpenRAIL, CC-BY-SA, GPL family, Llama community licences |
| Red struck-through briefcase | Personal and research use only | Any `-NC` licence or a recorded non-commercial restriction |
| Grey question mark | License not identified | `NOASSERTION`, `other`, or any licence id nobody has mapped |

A record takes its most restrictive part, including restricted file variants.
The category summarises the declared text only; it is not a review result, and
"Not reviewed" stays visible until the record is cleared. New licence ids must be
mapped in `backend/services/model_licenses.py` (CI fails otherwise).

Clicking it opens the licence dialog, on a solid background, in this order:

1. **Summary**: the licence category in plain words, the declared licence, the
   review state and your acceptance state (accepted with date and terms
   fingerprint, withdrawn, terms updated with what changed, or not accepted).
2. **What this licence allows**: commercial use of the model, commercial use
   of generated audio, sharing the model files, and voice and recording consent
   (always your responsibility), each with a status.
3. **Source**: the rights holder and links to the licence and the model page.
4. Collapsed sections: **Files and variants** (files with their own terms;
   the most restrictive applies), **Recorded notes**, **Technical details**
   (model and evidence versions, evidence date, terms fingerprint, registry
   version and acceptance history; long hashes are shortened with a copy
   button) and **Verified install (preview)**.
5. A footer that stays visible: the notice that VoiceStudio is not the licensor
   and cannot grant access, a confirmation checkbox, and **Accept licence** or
   **Withdraw acceptance**.

The dialog that opens when another feature needs an unaccepted model shows the
same full view for every model involved. Unmapped or conflicting licence data
falls back to the unknown category, never to commercial. A licence
label alone does not establish the rights of a complete workflow.

Pinned evidence links and the current upstream page are distinct. Opening an
external link uses the browser; the current page may describe a newer version.
The app renders upstream documents as inert text and accepts only HTTPS links
without embedded credentials. Legal-document text is not interpreted as code.

**Prepare reviewed install** is an optional preview, not the ordinary Download
button. It reads bundled evidence and does not download model bytes. The initial
registry returns blocked plans because component closure, exact file manifests,
document bytes, revision parity or provider access remain unverified. A blocked
plan has no acknowledgement checkbox or transfer action. The preview currently
supports local targets only.

For a future fully evidenced selection, the preview displays the exact source,
target, components, revisions, file hashes, complete documents and notice
version. The checkbox starts unchecked. **Acknowledge and verify files** records
the local acknowledgement before transferring those exact files into a separate
reviewed cache. Success means those bytes were verified; **they are not activated
for normal use**. The prototype transfer is synchronous and not cancellable once
started. The dialog prevents a duplicate commit and does not allow dismissal
during that transfer. It does not integrate with normal download progress jobs.

Closing a preview or changing the catalogue digest/target invalidates the UI's
prepared plan and checkbox. A changed or expired plan is rejected by the backend;
prepare again before retrying. A transfer failure may leave an exact receipt
already recorded, but never silently acknowledges a different selection.

## What an acknowledgement means

The receipt records an explicit local action for the exact prepared notice. It
does not prove the user read every word, acted with organizational authority or
obtained a separate licence. It does not accept provider terms, grant gated
access, establish voice consent, relicense outputs or waive anyone's rights.
The app's AGPL licence and any commercial app licence cover separate rights.

The backend's original notice is English (`locale: en`) and is displayed
verbatim. The receipt's prompt digest covers that versioned original text, not
the localized surrounding controls or the explanatory checkbox label. The UI
controls are translated in all maintained renderer locales; upstream documents
and recorded evidence notes remain in their original language. No translated legal agreement is
claimed by this preview.

Legacy Supertonic/PocketTTS engine booleans are kept unchanged. They are not
backdated, converted to receipts or treated as provider acceptance. Existing
installed revisions are not deleted, revoked or deemed accepted by this change.

## API and local records

- `POST /models/install/prepare` with `repo_id` and `target` returns a versioned
  plan, blockers and `readiness`. It makes no model download.
- `POST /models/install/commit` requires the exact `plan_id`, `plan_digest`,
  complete `acknowledged_document_ids`, the literal boolean `acknowledged: true`
  and `target: local`. It rechecks the current plan, stores the local receipt
  before transfer and verifies every downloaded file. It does not call the
  ordinary model installer or SDK fallback.
- `GET /models/licenses/receipts/{plan_digest}` exports an existing local
  receipt with its archived evidence, without network access. Responses are
  `no-store` and subject to the app's browser-origin protections.
- `DELETE /models/licenses/receipts/{plan_digest}` removes that local
  acknowledgement. It retains model files and archived evidence. Deletion does
  not erase a legal obligation or revoke a licence; subsequent reviewed-file
  operations must not assume the removed acknowledgement still exists.
- `POST /models/licenses/verify/{plan_digest}` checks the recorded exact files
  offline. It is an explicit helper; ordinary first-use loaders do not call it.

Records live under `model-notices` in the app data directory for the current OS
profile. The preview does not transmit receipts or collect names, emails, IP
addresses, device fingerprints, tokens, prompts, audio, voice samples or private
commercial contracts for this purpose. File downloads still use the selected
provider endpoint and its normal authentication rules. A local file and device
timestamp are not tamper-proof evidence of a legal transaction. Export/removal
currently use the API; no dedicated receipt-management screen is implemented.

Plan digests use the backend's documented Python canonical JSON format v1;
cross-language RFC 8785 interoperability is not claimed. Original terms and
artifact bytes use SHA-256. Test documents and hashes stay in tests and cannot
be substituted into the production registry.

## Boundaries still to complete

Licence acceptance is enforced when a model is used (see above). These existing
paths remain **unchanged** by the reviewed-install preview, so its exact-file
evidence is not applied to them:

- Ordinary `/models/install`, recommendations, model packs, repair and cache
  resume; engine installation and sidecar bootstraps
- Direct SDK auxiliary and fallback downloads, including PocketTTS non-cloning
  weights and selected voice embeddings
- Dynamic assets with no registry record (WhisperX alignment and VAD, FunASR
  VAD, Argos packs, TTS plugins, user overrides and imported models)

Provider-specific agreements/access, user-selected variant/voice resolution,
private commercial-policy decisions, model activation and full executable
dependency/redistribution notice coverage are not implemented. A permissive
engine licence does not settle any of these model or output questions.

Before any production selection or broader enforcement is enabled, verify:

- [ ] Exact production component/variant closure and provenance; reproducible
  immutable artifact manifests, original document hashes and required notices
- [ ] Scoped upstream permission and condition review, including output use,
  redistribution and compatibility; separate provider assent/access protocol
- [ ] All applicable UI/API/CLI/worker/SDK paths stop before undeclared or changed
  bytes; fallback and first-use cannot bypass the same boundary
- [ ] Interrupted transfer, duplicate commit, changed plan, persistence failure,
  offline reuse, receipt deletion and historic version behavior are tested
- [ ] Exact app/website/repository projection parity and stale-output checks pass
- [ ] Real-app keyboard, focus return, screen-reader announcements, 200–400% zoom,
  small windows, RTL, long terms and macOS/Windows/Linux behavior are verified

## Checks

Run offline backend/inventory checks with an empty model cache, and the normal
Electron locale, test and typecheck commands described in `electron/package.json`.
Focused regression coverage is in `tests/test_model_license_workflow.py` and
`electron/src/renderer/src/features/settings/model-license.test.tsx`.

```sh
python scripts/check_model_licenses.py
python scripts/generate_model_license_data.py --check
HF_HUB_OFFLINE=1 pytest tests/test_model_license_workflow.py tests/test_model_licenses.py
cd electron
bun run locale:check
bun run typecheck
bun run test
```

Focused UI tests use synthetic plans only. Passing them is not a claim that
production model evidence is complete or that a full packaged app has passed
cross-platform accessibility or first-use integration testing.
