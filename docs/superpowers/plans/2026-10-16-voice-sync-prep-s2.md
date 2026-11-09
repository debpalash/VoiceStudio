# Voice Sync Preparation (S2 prep) Implementation Plan

> Executed inline by the session that wrote it; the tests named per task are the contract.

**Goal:** Make voices ready to sync once the server ships blobs. Every voice gets a `sync_id`, consent is captured as an explicit attestation, a voice without one is never pushed, and the app can encrypt and decrypt `VSB1` files.

**Spec:** `account_login/HANDOFF.md` ("Get ready for voice sync (S2) now"), `account_login/desktop-app-guide.md` §4 and §9, and `account_login/sync-collections.md` (`voices`).

**Builds on:** the S0 and S1 plans of 2026-10-16.

## Constraints
- `sync_id` is 16 Base62 characters (`services.cloud_sync.ids.new_sync_id`). It is filled the first time sync is turned on and never changes. Local IDs stay local.
- Consent `basis` is `own_voice` or `speaker_permission`, with `attested_at` in RFC 3339 UTC. A voice with no attestation is never pushed (`voice_sync_content` returns `None`). The consent recording travels only when "Include consent recordings" is on (S2 server phase).
- `VSB1` (guide §9): `"VSB1"` followed by an 8-byte nonce prefix, then chunks of 4 194 304 bytes, each AES-256-GCM. The nonce is the prefix plus `uint32_be(i)`. The AAD is `"VSB1"` plus the `blob_id` (16 ASCII bytes), `uint32_be(i)`, and a final-flag byte. Size is `12 + plaintext + 16 × n`. Any tag failure, or a final flag that is not on the last chunk, fails the whole file. Range decryption is supported. The data key is never written to disk or a log.
- Database changes go through alembic (`0015_voice_sync_prep`) and are mirrored in `_BASE_SCHEMA`, so `_reconcile_additive_columns` heals databases where alembic cannot run. Existing voices keep working untouched.
- Localization: all 21 locales.

## Tasks
1. **Columns and IDs.** Alembic 0015 adds `voice_profiles.sync_id`, `consent_basis` and `consent_attested_at`, plus a unique partial index on `sync_id`. `cloud_sync/record_ids.ensure_sync_ids()` fills missing IDs, and `CloudSyncService.turn_on` calls it. Tests: upgrade from 0014 keeps rows; IDs are filled once and are stable; the reconcile step adds the columns without alembic.
2. **Attestation API and the push gate.** `POST/DELETE /profiles/{id}/attestation {basis}`; profile payloads include `consent_basis` and `consent_attested_at`. `cloud_sync/voices.voice_sync_content(row)` builds the `voices` content, or returns `None` when the voice is not attested. Tests: basis validation, set and clear, no attestation means no content, the content shape matches sync-collections, and the content has no local paths or IDs.
3. **`VSB1`** (`cloud_sync/vsb1.py`). Tests: round trips for 0 bytes, 1 byte, exactly one chunk and one chunk plus 1 byte; the size formula; a tampered tag fails; a truncated file fails (the final flag is missing); wrong magic fails; a chunk-swap fails; a range decrypt of a middle chunk works.
4. **Consent UI.** In the voice's consent panel, "This is my voice" and "I have the speaker's permission", with Remove. Strings in 21 locales. Tests: each button posts its basis; an attested voice shows its basis and can clear it.
