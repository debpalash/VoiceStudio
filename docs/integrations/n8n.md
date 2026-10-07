# n8n: local text to speech

**Integrations → n8n** exports a manual workflow using VoiceStudio's current
backend address. It contains a Manual Trigger and an HTTP Request node; it does
not activate a schedule, install n8n, expose your backend, or include credentials.

1. Start VoiceStudio and install/select a working TTS engine.
2. Choose **Save as…** on the n8n detail page. Import `voicestudio-n8n.json`
   through n8n's workflow import menu. You can also copy its JSON into n8n.
3. Open the **VoiceStudio** HTTP Request node. Edit `input` in its JSON body.
   `voice: "default"` uses the engine's default; use a saved voice profile's ID
   or name for cloning. `model: "tts-1"` selects VoiceStudio's active engine.
4. Run the workflow manually. The request returns WAV audio in the binary
   `audio` field, ready for a following n8n node or download.

## Reachability and authentication

The exported address is correct for a native n8n process on the same computer.
Inside Docker, `127.0.0.1` refers to the container, not your desktop. Replace the
node URL with the address your n8n process can actually reach; Docker Desktop
commonly provides `host.docker.internal`. Linux container networking may require
an explicit host-gateway mapping. A cloud n8n instance cannot reach a private
loopback address. Configure remote access deliberately using the
[API authentication guide](../api-auth.md), rather than exposing a desktop port
without protection.

For a protected backend, first use **HTTPS for every non-loopback connection**
(including container host-gateway addresses). Do not attach a bearer key to a
plain HTTP remote URL; configure TLS or a local encrypted tunnel first. Then
select **Generic Credential Type → Header Auth** in the
HTTP Request node and store `Authorization: Bearer <your key>` in n8n's credential
manager. Never put keys in the URL or exported workflow JSON. Redirects are
explicitly disabled, and non-success responses remain errors. The timeout is
31 minutes, covering the default 20-minute model-load and 10-minute CPU-generation
budgets plus overhead. Longer scripts or custom backend budgets may require a
larger timeout in n8n.

Copying/importing this workflow does not establish a live connection. A successful
manual execution producing a WAV confirms your engine, address and credentials.
VoiceStudio processes speech at the selected backend; what subsequent n8n nodes
do with the audio is controlled by your workflow.

The node configuration follows n8n's official
[HTTP Request documentation](https://docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.httprequest/).
Tests send the exported request through VoiceStudio's real HTTP route with a local
test engine, checking its WAV response. The generated export was also imported
and executed with n8n 2.39.8 on Linux, producing a valid WAV in its binary output.
Each generated configuration has its own workflow ID, avoiding collisions with
unrelated workflows. No external account is connected automatically.
