# Hugging Face Token Setup

VoiceStudio uses a single HF token for every model download, license-gate
check, and an explicit **Test now** action. This page covers the three places VoiceStudio will
look for a token and the recommended path for v0.3+.

## Three sources (cascade)

VoiceStudio resolves the active HF token by walking three sources in priority
order — the first source that has a token *and* survives a live `whoami`
call wins:

1. **App** — encrypted in VoiceStudio's SQLite settings store.
   Set via the in-app **Settings → Credentials** panel.
2. **Env** — `HF_TOKEN` (or the legacy `HUGGING_FACE_HUB_TOKEN`) environment
   variable visible to the VoiceStudio process.
3. **HF CLI** — the local `HF_TOKEN_PATH` file (normally `~/.cache/huggingface/token`) written by
   `huggingface-cli login`.

Source labels in onboarding and Settings follow the selected UI language;
product names such as HuggingFace CLI remain unchanged.

On opening **Settings → Credentials**, each row shows local set/unset state and
a masked preview (`hf_…3jw`). Tokens remain **Not tested** until you select
**Test now**. That explicit check displays the `whoami` username and a green
check for valid sources, with an **Active** badge on the highest-priority valid source.

## Setting via the app (recommended)

1. Open **Settings → Credentials**.
2. Paste your HF token (get one from
   [huggingface.co/settings/tokens](https://huggingface.co/settings/tokens) —
   the "read" scope is enough).
3. Click **Save**. The token is encrypted at rest (Fernet symmetric AEAD,
   key derived per-install from machine-id) and also written to the
   canonical `huggingface_hub` token location so subprocess engines pick it
   up automatically.
4. Select **Test now** to validate the token with Hugging Face. A successful test turns the indicator green and moves the **Active** badge to "App".

> **Known limitation (honest disclosure):** the encryption key is derived
> per-install from the machine identifier. If you copy `omnivoice_data/`
> across machines, the token row in `settings` will fail to decrypt on the
> new machine — the resolver logs a warning and falls back to the env / CLI
> source. Re-save the token on the new machine to re-encrypt with the
> new install's key.

## Setting via environment variable (power users)

If you launch VoiceStudio from a terminal or CI and prefer env-var management,
export `HF_TOKEN` from your shell's startup file:

```bash
# macOS (zsh — default since 10.15)
echo 'export HF_TOKEN=hf_yourtokenhere' >> ~/.zshrc && source ~/.zshrc

# Linux (bash)
echo 'export HF_TOKEN=hf_yourtokenhere' >> ~/.bashrc && source ~/.bashrc
```

**Windows PowerShell** — write to user-scope environment:

```powershell
[Environment]::SetEnvironmentVariable("HF_TOKEN","hf_yourtokenhere","User")
```

That persists for new shells. Close and reopen PowerShell or your terminal
to see it.

> **Don't use `setx`.** `setx HF_TOKEN "hf_..."` writes the variable but
> *doesn't propagate to the current shell* — a common source of "I set it
> but it's empty" bug reports. Use the in-app Settings → Credentials path or
> the `[Environment]::SetEnvironmentVariable` one-liner above.

## Setting via `huggingface-cli`

If you already use the HuggingFace CLI:

```bash
pip install --upgrade huggingface_hub
huggingface-cli login
# paste token at the prompt
```

That normally writes to `~/.cache/huggingface/token`. VoiceStudio reads the
selected local token file directly — you'll see the
**HF CLI** row in **Settings → Credentials** flip to "set".

## Accepting model licenses

Some models need both a token *and* a license acceptance click before
downloads work. Visit each page while signed in with the same HF account:

- `pyannote/speaker-diarization-3.1` — required for diarization.
  See [docs/features/diarization.md](../features/diarization.md).
- `pyannote/segmentation-3.0` — required transitively by the above.
- `IndexTeam/IndexTTS-2.5` — required if you use IndexTTS 2.5 for voice cloning.
- `Supertone/supertonic-3` — required if you enable the Supertonic-3 engine.
- `kyutai/pocket-tts` — required if you enable PocketTTS; first accept its
  access conditions on Hugging Face, then use a token from the same account.

After clicking **"Agree and access repository"** on each page, restart any
in-flight VoiceStudio job (the gated check is cached for the lifetime of the
process).

## Troubleshooting

- **HF 401 even though a token is set** — visit the model's HuggingFace page
  and accept the license (see above). The token is fine; the *license* gate
  is separate.
- **Token row stays red after Test now** — the `whoami` call failed. Check the
  token is valid at
  [huggingface.co/settings/tokens](https://huggingface.co/settings/tokens)
  and has at least the "read" scope.
- **Token didn't survive a reboot** — open **Settings → Credentials** and check
  the App row. If it's empty, the SQLite store may have been wiped — re-save.
  If it's set but the active source is "Env" or "HF CLI", that's the cascade
  working as intended (App is highest priority).

Opening onboarding or Settings only reads local token presence and masked previews; it does not contact Hugging Face. Untested tokens are shown as **Not tested**, and onboarding reports where a token was found without claiming it is valid. **Test now** explicitly contacts Hugging Face. Replacing a saved token does not revoke the previous token on Hugging Face.


Windows automatically shortens the model cache path while keeping the normal CLI token location. If only a previous VoiceStudio short-cache token exists, the app continues using that file. An existing normal CLI token takes priority; explicit token or cache overrides remain authoritative. Credentials are never copied between these locations.

The CLI row reads only the selected local file, without OAuth refresh or environment-token fallback. **Also clear saved HuggingFace CLI token files** removes both active and stored-token files at recognized automatic locations, so an older app token cannot reappear on restart. Explicit overrides limit clearing to their selected location. Clearing only the app token preserves CLI files; neither action revokes tokens on Hugging Face or changes Git credentials. A file permission failure is reported instead of claiming the files were cleared.


If onboarding cannot read token state, it shows an error and **Retry**, keeping token entry hidden until discovery succeeds. **Replace token** writes the encrypted app token through the same endpoint as Settings, so it replaces the highest-priority app credential even when an older one exists. The success message confirms saving only; validation remains a separate explicit action.
