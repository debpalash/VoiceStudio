# Electron LLM provider settings

The local setup on Windows can use Ollama at `http://127.0.0.1:11434/v1` with
`qwen3:4b-instruct-2507-q4_K_M` (approximately 2.5 GB). Install Ollama, pull the
model, then choose Ollama in this page and use **Connect & enable**. No key is
needed. This setup was verified with real translation and dictation cleanup.

## Provider options

The same provider and per-skill selectors support three transports:

| Option | Configuration |
| --- | --- |
| OpenAI-compatible SDK | OpenAI, Gemini, Mistral, Cohere, DeepSeek, xAI, Together, Fireworks, Perplexity, Qwen, Kimi, MiniMax, Z.AI, OpenRouter and other listed hosts; key, model, optional endpoint override |
| Native LiteLLM SDK | Anthropic directly; Amazon Bedrock; Google Vertex AI; other LiteLLM providers using `provider/model` |
| Installed CLI | Claude Code, Codex, Pi and OpenCode using their existing CLI login; model is optional |

Azure OpenAI uses the resource's `/openai/v1` base URL and the deployment name
as Model. Bedrock accepts a Bedrock API key or an existing `AWS_PROFILE` /
AWS credentials, with region set in the AWS environment. Vertex uses Account ID
as the Google project, application default credentials, and `VERTEXAI_LOCATION`
(default `global`). Provider access and billing remain tied to your accounts.
API Route uses `https://global.api-route.com/v1`, with `deepseek-v4.1-flash`
as its default model. Select API Route and enter a key from
[api-route.com](https://www.api-route.com); `API_ROUTE_API_KEY`,
`API_ROUTE_BASE_URL` and `API_ROUTE_MODEL` also configure this entry.

Model IDs vary by account; native SDK and CLI entries use manual model entry,
while compatible endpoints offer model discovery. The generic LiteLLM entry
requires a provider-prefixed model. Credentials can use a saved key, provider
environment variables or the provider's identity chain; Connect validates access.
The Google AI default is `gemini-3.8-flash`; the former `gemini-2.0-flash` default
has been retired ([Google's deprecation schedule](https://ai.google.dev/gemini-api/docs/deprecations)).
Explicitly saved model choices remain unchanged.

CLI completions reuse Electron's **Ask VoiceStudio Agent** discovery, process
runner, output parser, timeout and shutdown cleanup. They run in a temporary
directory with restricted tools, without the repair API capability. A private
loopback bridge lets backend LLM skills call that runner; its credential never
reaches renderer JavaScript or the CLI child. Standalone web/backend deployments
do not advertise desktop CLI providers as configured. Install/sign in to a CLI
outside the app, then restart VoiceStudio to refresh discovery. CLI startup can
exceed dictation's short latency budget; Ollama or a direct API is preferable for
live cleanup. Existing batch agent dubbing remains available in the Dubbing tab.

SDK imports are lazy and LiteLLM telemetry and remote pricing-map downloads are
disabled. Reading the provider catalogue starts no CLI and performs no cloud
request. Paid-provider transport tests use loopback fixtures, not paid accounts.

## Translation services

The Dubbing engine selector also offers Google Cloud Translation and Amazon
Translate alongside DeepL, Microsoft, Google web translation, MyMemory, and
offline engines. Configure Google Cloud's API key, Azure region, AWS profile and
AWS region in **Settings > Credentials**. Amazon uses the standard AWS credential
chain. Selecting Amazon or starting/retrying a translation checks the resolved
AWS credentials and region before work starts; catalogue reads remain network-free.
Identity-based LLM setups require explicit selection and never auto-activate from
a model name alone. DeepL Free keys ending in `:fx` automatically use the Free API; paid keys
use the Pro endpoint. DeepL/Microsoft/Google Cloud keys are encrypted, including
migration of older plaintext translation-key preferences. The legacy compatible
endpoint key uses the same encrypted storage. These paid requests
require HTTPS, have bounded connection/read timeouts, and do not follow credential-bearing redirects.
Connect also checks that the project/account stayed unchanged during verification.
Credentialed LLM endpoints require HTTPS outside loopback; local HTTP servers remain supported.

Provider protocol references: [LiteLLM](https://docs.litellm.ai/docs/providers),
[Codex](https://developers.openai.com/codex/noninteractive/),
[Claude Code](https://code.claude.com/docs/en/cli-reference),
[Pi](https://github.com/badlogic/pi-mono/blob/main/packages/coding-agent/docs/cli.md),
[OpenCode](https://opencode.ai/docs/cli/),
[Google Cloud Translation](https://cloud.google.com/translate/docs/reference/rest/v2/translate),
[Amazon Translate](https://docs.aws.amazon.com/translate/latest/APIReference/API_TranslateText.html),
[DeepL](https://developers.deepl.com/api-reference/translate),
[Microsoft](https://learn.microsoft.com/en-us/azure/ai-services/translator/text-translation/reference/v3/translate).

## Connection behavior

Settings > Models > LLM includes the existing provider catalogue. Translation
settings links to it. Configure an endpoint/model and, where needed, an API key
or account ID. Save preserves a stored key when the key input is blank. Keys
are sent only to the existing backend credential storage, never localStorage.
Environment-pinned fields remain read-only. Activation cannot override a pinned
provider or an `OMNIVOICE_LLM_BACKEND=off` setting; the currently pinned provider
can still save its editable fields and enable the engine when permitted.

LLM settings uses one provider status and setup form. It does not show the generic
hardware-engine inventory with a misleading "Off: Available" row or an unavailable
backend count. Without an active LLM, the status explains how to connect a server
or configure a provider key. The provider picker is searchable; local API keys are
optional and collapsed by default.

Connect & enable saves the form, then calls `/llm-providers/{id}/connect`. The
backend verifies a usable model response before activating the provider and LLM
engine. A failed connection leaves the engine mode unchanged and shows the
classified failure. Turn off LLM disables the engine without deleting credentials.
Incomplete settings cannot replace the active selection. The editor passes `activate_if_unset=false`
for ordinary saves and probes; older API clients retain the first-save activation
behavior. A first editor save preserves the prior engine mode so adding a cloud
key cannot auto-enable features before connection verification. Existing
environment/key auto-selection outside this editor remains supported.
Test and Fetch models first save the current
form, stop if saving fails, and then call the backend probe. They never run
on page load. Provider calls may use the network only when explicitly requested;
local endpoints remain supported. Failed probes use classified localized messages.
Local connection tests allow up to 120 seconds for a cold model load, while
connection establishment remains bounded to five seconds (cloud responses: 20 seconds).

Local servers can use optional API keys, stored encrypted like cloud keys, or
`OLLAMA_API_KEY` / `LMSTUDIO_API_KEY` environment overrides. Blank key inputs keep
the stored key. Fetch models works before a custom model is selected and reports
empty and truncated listings. Test requires a nonempty answer, not merely an HTTP
success. Configuration readiness checks the HTTP(S) endpoint, required credentials,
model and account ID; a stopped server is detected by the explicit Test action.

LM Studio's blank model field preserves automatic loaded-model discovery.
Opening settings or reading engine inventory never probes that server, and saving
the form never freezes a discovered model. Existing `local-model` placeholder
settings recover automatic discovery; a real explicit model still takes precedence.
Discovery runs only on an explicit probe or feature request.

LLM skills following the active provider respect the selected Off engine. Explicit
per-skill provider overrides remain usable independently, but the environment's
global Off switch disables all skills. Changing skill routing refreshes translation
and dictation readiness in the UI.

The browser smoke `node electron/tests/llm-providers-smoke.mjs` mocks credentials
and provider responses. It verifies blank-key preservation, environment pinning,
save-before-test, no probe after failed save, model choice and activation. A live
catalogue read returned 17 provider descriptors without key material. Actual
external credentials and remote-provider calls are not verified by those mocks.
Backend regression tests also exercise the actual OpenAI SDK against a loopback
HTTP fixture through model listing, activation, probing and skill completion.
That verifies transport and routing, not a real model's translation quality.
Per-skill routing is available beneath providers. Each backend capability can be
disabled or assigned a configured provider, with an option to follow the active
provider. Existing unavailable overrides stay visible. Readiness comes from the
backend; it is not proof of a successful network probe. Non-LLM translation-provider
credentials for DeepL and Microsoft are available under Settings > Credentials and are
written through the backend environment-setting endpoint. The skills browser smoke
verifies routing and disable behavior against mocked API responses.

LM Studio discovery honors the selected model before probing loaded models. Its
native loaded-model probe uses the configured API key; embedding-only listings
are never selected for chat. Dictation refinement disables optional reasoning
where supported, retries only errors naming that parameter, and preserves literal
reasoning tags within an answer rather than truncating technical text.

In the supported web development client, API-reference recovery may use the
same-origin proxy only for its known local backend; remote overrides never fall
back to a different server. The configured authentication is retained.

LM Studio automatic discovery accepts only loaded `llm`/`vlm` entries from its native model metadata. If that metadata is unavailable, set a model explicitly in Settings; untyped OpenAI-compatible IDs are not used to guess whether a model supports chat. The authenticated native probe rejects redirects so credentials remain on the configured origin.

SDK transport validates the final request URL, including environment-derived
OpenAI endpoints, and disables redirects for OpenAI-compatible and native
Anthropic, Bedrock, and Vertex requests.
The shared HTTP client bypasses environment proxies for loopback requests so local
prompts stay local, preserves proxy settings for remote HTTPS providers, and never
stores response cookies between provider requests.
Changing proxy settings refreshes the pool for subsequent requests while existing
streams keep their original connections until they finish.
