"""Declarative HTTP commands. Resource IDs are encoded by the dispatcher."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Command:
    name: str
    method: str
    route: str
    description: str
    params: tuple[str, ...] = ()
    body: bool = False
    upload: str = ''
    binary: bool = False
    destructive: bool = False
    stream: bool = False


COMMANDS: list[Command] = []

# Plain-language help, written for people rather than derived from routes.
# How input is passed (--file, --field) belongs to the option help, not here.
DESCRIPTIONS = {
    'voices show': 'Show a saved voice',
    'voices usage': 'Show where a saved voice has been used',
    'voices update': 'Change a saved voice',
    'voices reference get': "Download a voice's reference recording",
    'voices reference set': "Replace a voice's reference recording",
    'voices image set': "Replace a voice's picture",
    'voices consent set': 'Record consent for a cloned voice (consent text via --field consent_text=...)',
    'voices consent revoke': 'Withdraw recorded consent for a voice',
    'voices lock': 'Protect a voice from changes',
    'voices unlock': 'Remove protection from a voice',
    'voices export': 'Export a voice as a shareable file',
    'voices inspect': 'Show what a shareable voice file contains, without importing it',
    'voices import': 'Import a shareable voice file',
    'voices describe': 'Preview how a text description maps to voice-design settings',
    'gallery list': 'List voices in the gallery',
    'gallery import': 'Add an audio clip to the gallery',
    'gallery download': 'Download audio from a link into the gallery',
    'gallery search': 'Search online videos for voice samples',
    'gallery preview': 'Download a gallery voice preview',
    'gallery update': 'Change a gallery voice',
    'gallery save-voice': 'Save a gallery voice as one of your voices',
    'gallery delete': 'Delete a gallery voice',
    'gallery presets list': 'List ready-made voice presets',
    'gallery presets use': 'Create a voice from a preset',
    'gallery community list': 'List voices shared by the community',
    'gallery community preview': 'Download a community voice preview',
    'gallery community use': 'Create a voice from a community voice',
    'gallery community submit': 'Show where to submit a voice to the community gallery',
    'dictation models': 'List dictation models',
    'dictation readiness': 'Check whether dictation is ready to use',
    'dictation preferences show': 'Show dictation preferences',
    'dictation preferences set': 'Change dictation preferences',
    'stories render': 'Render a story to audio from a chapter plan',
    'stories encode': 'Convert a WAV file to another format (--field format=mp3)',
    'audiobook jobs': 'List interrupted audiobook and story renders you can resume',
    'stories resume': 'Resume an interrupted story render',
    'audiobook plan': 'Plan chapters and voices for an audiobook',
    'audiobook preview': 'Render a short audiobook preview',
    'audiobook render': 'Render an audiobook',
    'dub transcript get': "Show a dubbing job's transcript",
    'dub transcript set': "Replace a dubbing job's transcript",
    'dub transcript clean': "Tidy a dubbing job's transcript segments",
    'dub translate': 'Translate transcript segments',
    'dub fit': 'Shorten translations to fit the original timing',
    'dub generate': 'Generate the dubbed speech',
    'dub preview': 'Generate one segment to preview it',
    'dub cancel': 'Stop a running dubbing job',
    'dub history': 'List past dubbing jobs',
    'dub history-delete': 'Delete a past dubbing job',
    'batch enqueue': 'Queue a video for dubbing (languages via --field langs=es,fr)',
    'batch show': 'Show a batch job',
    'batch cancel': 'Stop a batch job',
    'batch retry': 'Retry a failed batch job',
    'batch delete': 'Delete a batch job',
    'jobs list': 'List background jobs',
    'jobs show': 'Show a background job',
    'jobs events': "Show a background job's progress events",
    'jobs cancel': 'Stop a background job',
    'jobs longform list': 'List finished audiobook and story renders',
    'jobs longform delete': 'Delete a finished audiobook or story render',
    'history list': 'List generated clips',
    'history download': 'Download a generated clip by its file name',
    'history quality': 'Show audio quality measurements for a clip',
    'history delete': 'Delete a generated clip',
    'history clear': 'Delete all generated clips',
    'projects show': 'Show a project',
    'projects export': 'Export a project to share or move it',
    'projects import': 'Import an exported project',
    'projects glossary list': "Show a project's glossary",
    'projects glossary add': 'Add a glossary term',
    'projects glossary update': 'Change a glossary term',
    'projects glossary delete': 'Delete a glossary term',
    'projects glossary extract': 'Suggest glossary terms from the project',
    'audio clean': 'Remove noise from a recording',
    'audio normalize': 'Even out the loudness of speech',
    'audio convert-voice': 'Make a recording sound like a saved voice',
    'audio direction': 'Read delivery directions from a script',
    'audio rate-fit': 'Adjust speech speed to fit a duration',
    'audio effects': 'List available audio effects',
    'audio plugins': 'List installed audio plugins',
    'audio watermark status': 'Check whether audio watermarking is available',
    'audio watermark detect': 'Check a recording for a VoiceStudio watermark',
    'audio watermark settings': 'Change watermark settings',
    'pronunciation add': 'Add a pronunciation rule',
    'pronunciation test': 'Show how text is read with the current rules',
    'pronunciation export': 'Export pronunciation rules',
    'pronunciation import': 'Import pronunciation rules',
    'engines health': 'Check whether an engine is working',
    'engines selftest': 'Run a quick test of an engine',
    'engines disk-usage': 'Show how much disk space an engine uses',
    'engines quants list': 'List size and quality variants of the GGUF engine',
    'engines quants select': 'Choose a GGUF engine variant',
    'engines quants download': 'Download a GGUF engine variant',
    'models recommend': 'Suggest models that suit this computer',
    'models access': 'Check whether a model can be downloaded (--query repo_id=<id>)',
    'models install-status': 'Show model downloads in progress',
    'models loaded': 'List models loaded in memory',
    'models unload': 'Free the memory used by a loaded model',
    'models delete': "Delete a model's downloaded files",
    'models license show': "Show a model's license terms",
    'models license revoke': "Withdraw acceptance of a model's terms",
    'settings providers list': 'List AI text providers (used for translation and scripts)',
    'settings providers configure': 'Set up an AI text provider',
    'settings providers select': 'Choose the AI text provider to use',
    'settings providers test': 'Check that an AI text provider responds',
    'settings skills list': 'List AI text skills',
    'settings skills set': 'Change an AI text skill',
    'settings hf-token status': 'Show whether a Hugging Face token is saved',
    'settings hf-token clear': 'Remove the saved Hugging Face token',
    'settings storage show': 'Show how much disk space VoiceStudio uses',
    'settings storage clear-temp': 'Delete temporary files',
    'sharing status': 'Show whether other devices on your network can connect',
    'sharing enable': 'Let other devices on your network connect',
    'sharing disable': 'Stop other devices on your network from connecting',
    'sharing tailscale status': 'Show Tailscale sharing status',
    'sharing tailscale enable': 'Share VoiceStudio over Tailscale',
    'sharing tailscale disable': 'Stop sharing VoiceStudio over Tailscale',
    'integrations api schema': "Download the API's OpenAPI description",
    'integrations mcp bindings list': 'List which voice each AI agent uses',
    'integrations mcp bindings delete': "Remove an AI agent's voice choice",
    'calls options': 'List call options',
    'calls readiness': 'Check whether calls are ready to use',
    'calls list': 'List calls',
    'calls start': 'Start a call',
    'calls configure': 'Change call settings',
    'calls show': 'Show a call',
    'calls events': "Follow a call's live events",
    'calls say': 'Speak a line in a live call',
    'calls takeover': 'Take over a live call yourself',
    'calls hangup': 'End a call',
    'calls delete': 'Delete a call record',
    'calls recording': 'Download a call recording',
    'diagnostics system': 'Show system and hardware information',
    'diagnostics preflight': 'Check that this computer is ready to run VoiceStudio',
    'diagnostics logs': 'Show recent backend logs',
    'diagnostics errors': 'Show recent errors',
    'diagnostics crashes': 'Show details of the last crash',
    'diagnostics usage': 'Show usage statistics recorded on this computer',
    'diagnostics notifications': 'Show app notifications',
    'diagnostics bundle': 'Create a diagnostics file to attach to a bug report',
    'diagnostics flush-memory': 'Free memory held by models and caches',
    'diagnostics media-tools': 'Check the bundled ffmpeg and media tools',
    'dub ingest': 'Start a dubbing job from a video link',
    'projects create': 'Create a project',
    'projects update': 'Change a project',
}


def add(name, method, route, description, params=(), **kwargs):
    description = DESCRIPTIONS.get(name, description)
    COMMANDS.append(Command(name, method, route, description, tuple(params), **kwargs))


add('voices list', 'GET', '/profiles', 'List your saved voices')
add('voices show', 'GET', '/profiles/{id}', 'Inspect a voice profile', ['id'])
add('voices update', 'PUT', '/profiles/{id}', 'Edit profile fields from --file JSON', ['id'], body=True)
add('voices usage', 'GET', '/profiles/{id}/usage', 'Inspect voice usage', ['id'])
add('voices delete', 'DELETE', '/profiles/{id}', 'Delete a saved voice', ['id'], destructive=True)
add('voices reference get', 'GET', '/profiles/{id}/audio', 'Download reference audio', ['id'], binary=True)
add('voices reference set', 'PUT', '/profiles/{id}/audio', 'Replace reference audio', ['id'], upload='ref_audio')
add('voices image set', 'PUT', '/profiles/{id}/image', 'Replace profile artwork', ['id'], upload='image')
add('voices consent set', 'POST', '/profiles/{id}/consent', 'Upload consent audio; --field consent_text=...', ['id'], upload='consent_audio')
add('voices consent revoke', 'DELETE', '/profiles/{id}/consent', 'Revoke consent', ['id'], destructive=True)
add('voices lock', 'POST', '/profiles/{id}/lock', 'Lock a voice with --file JSON', ['id'], body=True)
add('voices unlock', 'POST', '/profiles/{id}/unlock', 'Unlock with the required proof in --file JSON', ['id'], body=True)
add('voices export', 'POST', '/personas/export/{id}', 'Export a portable persona', ['id'], binary=True)
add('voices inspect', 'POST', '/personas/inspect', 'Inspect a portable persona bundle', upload='file')
add('voices import', 'POST', '/personas/import', 'Import a portable persona bundle', upload='file')
add('voices describe', 'POST', '/design/describe', 'Map a description to voice-design attributes', body=True)

for name, method, route, params, body, upload, binary in [
    ('list', 'GET', '/gallery/voices', (), False, '', False),
    ('import', 'POST', '/gallery/upload', (), False, 'audio', False),
    ('download', 'POST', '/gallery/download', (), True, '', False),
    ('search', 'POST', '/gallery/search/youtube', (), True, '', False),
    ('preview', 'GET', '/gallery/voices/{id}/preview', ('id',), False, '', True),
    ('update', 'PATCH', '/gallery/voices/{id}', ('id',), True, '', False),
    ('save-voice', 'POST', '/gallery/voices/{id}/save-as-profile', ('id',), True, '', False),
    ('delete', 'DELETE', '/gallery/voices/{id}', ('id',), False, '', False),
    ('presets list', 'GET', '/archetypes', (), False, '', False),
    ('presets use', 'POST', '/archetypes/{id}/use', ('id',), True, '', False),
    ('community list', 'GET', '/community/items', (), False, '', False),
    ('community preview', 'GET', '/community/items/{id}/preview', ('id',), False, '', True),
    ('community use', 'POST', '/community/items/{id}/use', ('id',), True, '', False),
    ('community submit', 'GET', '/community/submit-url', (), False, '', False),
]:
    add('gallery ' + name, method, route, 'Gallery: ' + name, params, body=body,
        upload=upload, binary=binary, destructive=method == 'DELETE')

for name in ['models', 'readiness']:
    add('dictation ' + name, 'GET', '/dictation/' + name, 'Inspect dictation ' + name)
add('dictation preferences show', 'GET', '/dictation/prefs', 'Read dictation preferences')
add('dictation preferences set', 'POST', '/dictation/prefs', 'Set dictation preferences from --file JSON', body=True)
add('stories render', 'POST', '/longform/render', 'Render a chapter/span plan from --file JSON', body=True, stream=True)
add('stories encode', 'POST', '/stories/encode', 'Encode WAV; options through --field format=mp3', upload='file', binary=True)
add('stories resume', 'POST', '/audiobook/resume/{id}', 'Resume a longform job', ['id'], stream=True)
add('audiobook import', 'POST', '/audiobook/import', 'Import a supported document', upload='file')
add('audiobook cover', 'POST', '/audiobook/cover', 'Upload cover artwork', upload='cover')
for name, route, stream in [('plan', '/audiobook/plan', False), ('preview', '/audiobook/preview', True),
                            ('render', '/audiobook', True)]:
    add('audiobook ' + name, 'POST', route, 'Audiobook ' + name + ' from --file JSON', body=True, stream=stream)
add('audiobook jobs', 'GET', '/audiobook/jobs', 'List audiobook jobs')
add('audiobook resume', 'POST', '/audiobook/resume/{id}', 'Resume an audiobook', ['id'], stream=True)

add('dub upload', 'POST', '/dub/upload', 'Upload video to create a dubbing job', upload='video')
add('dub ingest', 'POST', '/dub/ingest-url', 'Import a media URL from --file JSON', body=True)
add('dub transcribe', 'POST', '/dub/transcribe/{id}', 'Transcribe a dubbing job', ['id'])
add('dub subtitles import', 'POST', '/dub/import-srt/{id}', 'Import subtitles', ['id'], upload='file')
add('dub transcript get', 'GET', '/dub/segments-text/{id}', 'Read transcript segments', ['id'])
add('dub transcript set', 'POST', '/dub/sync-transcript/{id}', 'Save segments from --file JSON', ['id'], body=True)
add('dub transcript clean', 'POST', '/dub/cleanup-segments/{id}', 'Clean segments from --file JSON', ['id'], body=True)
add('dub translate', 'POST', '/dub/translate', 'Translate segments from --file JSON', body=True)
add('dub fit', 'POST', '/dub/agent-fit', 'Fit translated segments from --file JSON', body=True)
add('dub generate', 'POST', '/dub/generate/{id}', 'Generate dubbed speech from --file JSON', ['id'], body=True, stream=True)
add('dub preview', 'POST', '/dub/preview-segment/{id}', 'Preview a segment from --file JSON', ['id'], body=True)
add('dub tracks', 'GET', '/dub/tracks/{id}', 'List language tracks', ['id'])
add('dub quality', 'POST', '/dub/qc/{id}', 'Check dubbing quality', ['id'], body=True)
add('dub cancel', 'POST', '/dub/abort/{id}', 'Abort a dubbing job', ['id'])
add('dub history', 'GET', '/dub/history', 'List dubbing history')
add('dub history-delete', 'DELETE', '/dub/history/{id}', 'Delete a dubbing record', ['id'], destructive=True)

add('batch enqueue', 'POST', '/batch/enqueue', 'Queue a video; --field langs=es,fr', upload='video')
add('batch list', 'GET', '/batch/jobs', 'List batch jobs')
add('batch show', 'GET', '/batch/jobs/{id}', 'Inspect a batch job', ['id'])
for action in ['cancel', 'retry']:
    add('batch ' + action, 'POST', '/batch/jobs/{id}/' + action, 'Batch ' + action, ['id'])
add('batch delete', 'DELETE', '/batch/jobs/{id}', 'Delete a batch record', ['id'], destructive=True)
add('batch download', 'GET', '/batch/download/{id}/{language}', 'Download a batch result', ['id', 'language'], binary=True)
add('jobs list', 'GET', '/jobs', 'List shared jobs')
add('jobs show', 'GET', '/jobs/{id}', 'Inspect a shared job', ['id'])
add('jobs events', 'GET', '/jobs/{id}/events', 'Read persisted job events', ['id'])
add('jobs cancel', 'POST', '/tasks/cancel/{id}', 'Cancel a task by task ID', ['id'])
add('jobs longform list', 'GET', '/longform/jobs', 'List longform jobs')
add('jobs longform delete', 'DELETE', '/longform/jobs/{id}', 'Delete a longform run', ['id'], destructive=True)
add('history list', 'GET', '/history', 'List generated takes')
add('history download', 'GET', '/audio/{id}', 'Download a take by audio filename', ['id'], binary=True)
add('history quality', 'GET', '/audio/{id}/quality', 'Read audio quality', ['id'])
add('history delete', 'DELETE', '/history/{id}', 'Delete a take', ['id'], destructive=True)
add('history clear', 'DELETE', '/history', 'Clear generation history', destructive=True)

add('projects list', 'GET', '/projects', 'List projects')
add('projects show', 'GET', '/projects/{id}', 'Read a project', ['id'])
add('projects create', 'POST', '/projects', 'Create a project from --file JSON', body=True)
add('projects update', 'PUT', '/projects/{id}', 'Update a project from --file JSON', ['id'], body=True)
add('projects delete', 'DELETE', '/projects/{id}', 'Delete a project', ['id'], destructive=True)
add('projects export', 'POST', '/project-bundles/export', 'Export a project handoff from --file JSON', body=True)
add('projects import', 'POST', '/project-bundles/import', 'Import a project handoff', upload='@raw')
add('projects glossary list', 'GET', '/glossary/{id}', 'Read a project glossary', ['id'])
add('projects glossary add', 'POST', '/glossary/{id}', 'Add a glossary entry from --file JSON', ['id'], body=True)
add('projects glossary update', 'PUT', '/glossary/{id}/{term}', 'Update a glossary entry', ['id', 'term'], body=True)
add('projects glossary delete', 'DELETE', '/glossary/{id}/{term}', 'Delete a glossary entry', ['id', 'term'], destructive=True)
add('projects glossary extract', 'POST', '/glossary/{id}/auto-extract', 'Extract glossary terms', ['id'], body=True)

add('audio clean', 'POST', '/clean-audio', 'Clean reference audio', upload='audio', binary=True)
add('audio normalize', 'POST', '/tools/normalize-speech', 'Normalize speech audio', upload='audio', binary=True)
add('audio convert-voice', 'POST', '/convert', 'Convert a voice; options via --field', upload='audio', binary=True)
add('audio direction', 'POST', '/tools/direction', 'Parse direction from --file JSON', body=True)
add('audio rate-fit', 'POST', '/tools/rate-fit', 'Fit speech timing from --file JSON', body=True)
add('audio effects', 'GET', '/tools/effects', 'List audio effects')
add('audio plugins', 'GET', '/tools/plugins', 'Inspect plugin capabilities')
add('audio watermark status', 'GET', '/watermark/status', 'Inspect watermark support')
add('audio watermark detect', 'POST', '/watermark/detect', 'Detect an audio watermark', upload='file')
add('audio watermark settings', 'POST', '/watermark/settings', 'Update watermark settings', body=True)
add('pronunciation list', 'GET', '/pronunciation', 'List pronunciation rules')
add('pronunciation add', 'POST', '/pronunciation', 'Add a rule from --file JSON', body=True)
add('pronunciation update', 'PUT', '/pronunciation/{id}', 'Update a rule', ['id'], body=True)
add('pronunciation delete', 'DELETE', '/pronunciation/{id}', 'Delete a rule', ['id'], destructive=True)
add('pronunciation test', 'POST', '/pronunciation/test', 'Test substitutions from --file JSON', body=True)
add('pronunciation export', 'GET', '/pronunciation/export', 'Export pronunciation JSON')
add('pronunciation import', 'POST', '/pronunciation/import', 'Import pronunciation JSON', body=True)

add('engines health', 'GET', '/engines/{id}/health', 'Inspect engine health', ['id'])
add('engines selftest', 'POST', '/engines/{id}/selftest', 'Run an engine self-test', ['id'])
add('engines disk-usage', 'GET', '/engines/{id}/disk-usage', 'Inspect engine disk use', ['id'])
add('engines quants list', 'GET', '/engines/omnivoice-gguf/quants', 'List GGUF quantizations')
for action in ['select', 'download']:
    add('engines quants ' + action, 'POST', '/engines/omnivoice-gguf/quants/' + action,
        'GGUF quantization ' + action + ' from --file JSON', body=True)
add('models recommend', 'GET', '/setup/recommendations', 'Recommend models for this host')
add('models access', 'GET', '/models/access/status', 'Inspect model access; --query repo_id=<id>')
add('models install-status', 'GET', '/models/install/status', 'Inspect download jobs')
add('models loaded', 'GET', '/model/loaded', 'List resident models')
add('models unload', 'POST', '/model/unload/{id}', 'Unload a resident model', ['id'])
add('models delete', 'DELETE', '/models/{id}', 'Delete model files by catalogue ID', ['id'], destructive=True)
add('models license show', 'GET', '/models/licenses/details/{id}', 'Read model terms', ['id'])
add('models license revoke', 'POST', '/models/licenses/revoke', 'Revoke acceptance from --file JSON', body=True, destructive=True)

add('settings providers list', 'GET', '/api/settings/llm-providers', 'List LLM providers')
add('settings providers configure', 'PUT', '/api/settings/llm-providers/{id}', 'Configure a provider', ['id'], body=True)
add('settings providers select', 'POST', '/api/settings/llm-providers/active', 'Select provider from --file JSON', body=True)
add('settings providers test', 'POST', '/api/settings/llm-providers/{id}/test', 'Test provider connectivity', ['id'])
add('settings skills list', 'GET', '/api/settings/llm-skills', 'List LLM skills')
add('settings skills set', 'PUT', '/api/settings/llm-skills/{id}', 'Configure a skill', ['id'], body=True)
add('settings hf-token status', 'GET', '/api/settings/hf-token/state', 'Inspect HF credential state')
add('settings hf-token clear', 'DELETE', '/api/settings/hf-token', 'Remove HF credential', destructive=True)
add('settings storage show', 'GET', '/api/settings/storage', 'Inspect storage use')
add('settings storage clear-temp', 'POST', '/api/settings/storage/temp/clear', 'Clear temporary files', destructive=True)
for name, route in [('status', '/system/network/state'), ('enable', '/system/network/enable'),
                    ('disable', '/system/network/disable'), ('tailscale status', '/system/tailscale/status'),
                    ('tailscale enable', '/system/tailscale/enable'), ('tailscale disable', '/system/tailscale/disable')]:
    add('sharing ' + name, 'GET' if name.endswith('status') else 'POST', route, 'Sharing: ' + name)
add('integrations api schema', 'GET', '/openapi.json', 'Download the running API schema')
add('integrations mcp bindings list', 'GET', '/api/mcp/bindings', 'List agent voice bindings')
add('integrations mcp bindings delete', 'DELETE', '/api/mcp/bindings/{id}', 'Delete an agent binding', ['id'], destructive=True)
for name, route in [('options', '/calls/options'), ('readiness', '/calls/readiness'), ('list', '/calls')]:
    add('calls ' + name, 'GET', route, 'Calls: ' + name)
add('calls start', 'POST', '/calls', 'Start a call from --file JSON', body=True)
add('calls configure', 'PUT', '/calls/settings', 'Configure calls', body=True)
for name, method, suffix in [('show', 'GET', ''), ('events', 'GET', '/events'),
                              ('say', 'POST', '/say'), ('takeover', 'POST', '/takeover'),
                              ('hangup', 'POST', '/hangup'), ('delete', 'DELETE', ''),
                              ('recording', 'GET', '/recording')]:
    add('calls ' + name, method, '/calls/{id}' + suffix, 'Calls: ' + name, ['id'],
        body=name == 'say', binary=name == 'recording', destructive=name == 'delete', stream=name == 'events')
for name, route in [('system', '/system/info'), ('preflight', '/setup/preflight'),
                    ('logs', '/system/logs'), ('errors', '/system/errors/recent'),
                    ('crashes', '/system/last-run-crash'), ('usage', '/stats/usage'),
                    ('notifications', '/system/notifications'), ('media-tools', '/media-tools/status')]:
    add('diagnostics ' + name, 'GET', route, 'Diagnostics: ' + name)
add('diagnostics bundle', 'POST', '/system/diagnostic-bundle', 'Create a diagnostic bundle')
add('diagnostics flush-memory', 'POST', '/system/flush-memory', 'Release backend memory')
