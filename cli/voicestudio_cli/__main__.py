"""Command tree and shared terminal contracts."""
import argparse
import difflib
import io
import json
import math
import os
from pathlib import Path
import re
import sys
import tempfile
from contextlib import contextmanager
from urllib.parse import quote

from .application import app_version, installed_application
from .config import connection, load_config, save_config
from .errors import CLIError, scrub
from .registry import COMMANDS
from .transport import Client, download
from . import updates
from .desktop import launch_desktop, remember_launcher_arguments, split_arguments
from .render import render


class Parser(argparse.ArgumentParser):
    """argparse with plain-language errors that do not depend on its message wording."""

    def _check_value(self, action, value):
        # argparse's own "invalid choice" text differs between Python versions,
        # so build the message from the real choices instead of parsing it.
        if action.choices is None or value in action.choices:
            return
        choices = [str(choice) for choice in action.choices]
        listed = choices
        if isinstance(action, argparse._SubParsersAction):
            listed = [choice.dest for choice in action._choices_actions]  # visible names only
        close = difflib.get_close_matches(str(value), listed, n=1)
        if isinstance(action, argparse._SubParsersAction):
            message = f"Unknown command '{value}'."
        else:
            name = (action.metavar if isinstance(action.metavar, str) else None) or (
                action.option_strings[-1] if action.option_strings else action.dest)
            message = f"'{value}' is not a valid {name.lstrip('-').replace('-', ' ').replace('_', ' ').lower()}."
            if not close:
                message += ' Choose one of: ' + ', '.join(listed) + '.'
        if close:
            message += f" Did you mean '{close[0]}'?"
        raise CLIError('usage_error', f'{message} Run {self.prog} --help.', exit_code=2)

    def error(self, message):
        message = message[:1].upper() + message[1:].rstrip('.') + '.'
        raise CLIError('usage_error', f'{message} Run {self.prog} --help.', exit_code=2)


class AppVersionAction(argparse.Action):
    def __call__(self, parser, namespace, values, option_string=None):
        print('VoiceStudio ' + app_version())
        parser.exit()


def positive(value):
    try:
        number = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError('must be a positive number') from None
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError('must be a positive finite number')
    return number


def common(parser):
    # Suppressed defaults prevent a nested parser resetting inherited flags.
    parser.add_argument('--url', default=argparse.SUPPRESS,
                        help='Use the VoiceStudio backend at this address instead of this computer\'s app')
    parser.add_argument('--json', action='store_true', default=argparse.SUPPRESS,
                        help='Print machine-readable JSON (errors too, on stderr)')
    parser.add_argument('--verbose', action='store_true', default=argparse.SUPPRESS,
                        help='Show more detail when something goes wrong')
    parser.add_argument('--timeout', type=positive, default=argparse.SUPPRESS,
                        help='Seconds to wait for a response (default: 600)')
    parser.add_argument('-o', '--output', default=argparse.SUPPRESS,
                        help='Save the result to this file; - prints it')
    parser.add_argument('--overwrite', action='store_true', default=argparse.SUPPRESS,
                        help='Replace the --output file if it exists')
    parser.add_argument('--yes', action='store_true', default=argparse.SUPPRESS,
                        help='Delete without asking for confirmation')


# Commands with hand-written arguments and handlers (the rest come from the registry).
CUSTOM = {'status', 'version', 'help', 'connection show', 'connection set', 'connection test',
          'connection auth set', 'connection auth clear', 'speak', 'transcribe', 'translate-audio',
          'voices clone', 'voices design', 'voices describe', 'voices consent show',
          'voices compare', 'projects rename', 'history star', 'history unstar',
          'models list', 'models install', 'models install-cancel', 'models license accept',
          'engines list', 'engines select', 'engines install', 'engines install-status',
          'settings get', 'settings set', 'settings hf-token set',
          'integrations api docs', 'integrations mcp config', 'integrations mcp bindings set',
          'dub export', 'update', 'update info', 'update check', 'stop', 'completion'}


def build_parser(only=None):
    """The command tree; ``only`` builds just one command path, for fast completion."""
    root = Parser(prog='voicestudio', description='Launch VoiceStudio with no arguments, or use its features from the terminal.', allow_abbrev=False)
    common(root)
    root.add_argument('-v', '--version', action=AppVersionAction, nargs=0, help='Show the installed VoiceStudio version')
    nodes = {(): root}
    groups = {}
    def command(path, handler, description, hidden=False):
        parts = tuple(path.split())
        for n in range(1, len(parts) + 1):
            key, parent = parts[:n], parts[:n-1]
            if key not in nodes:
                if parent not in groups:
                    groups[parent] = nodes[parent].add_subparsers(dest=f'command_{n}', title='Commands', metavar='COMMAND')
                if n == len(parts):
                    text = description
                elif hidden:
                    text = 'Former command group; run voicestudio --help for current names'
                else:
                    text = GROUPS.get(' '.join(key), key[-1])
                # Old names stay accepted but are left out of help listings.
                listing = {} if hidden else {'help': text}
                parser = groups[parent].add_parser(key[-1], description=text, allow_abbrev=False, **listing)
                common(parser)
                nodes[key] = parser
        parser = nodes[parts]
        parser.set_defaults(handler=handler)
        return parser

    def paths(name):
        """The public path, plus the hidden former path of a renamed command."""
        description = DESCRIPTIONS.get(name) or next(spec.description for spec in COMMANDS if spec.name == name)
        if name in HIDDEN:
            yield name, f'Former command; run voicestudio --help for current names', True
            return
        yield RENAMED.get(name, name), description, False
        if name in RENAMED:
            yield name, f'Former name of voicestudio {RENAMED[name]}', True

    custom = CUSTOM
    def registry_args(p, spec):
        p.set_defaults(spec=spec)
        for param in spec.params:
            p.add_argument(param, help='Backend resource identifier')
        if spec.upload:
            p.add_argument('input', help='Local file to upload; - reads stdin')
            p.add_argument('--stdin-filename', default='upload.bin', help='Filename/format hint for stdin uploads')
        p.add_argument('--file', help='Request details as a JSON file; - reads stdin' if spec.body
                       else 'Request details as a JSON file (optional); - reads stdin')
        p.add_argument('--field', action='append', default=[], metavar='NAME=VALUE', help='Request field; values accept JSON or plain text')
        p.add_argument('--query', action='append', default=[], metavar='NAME=VALUE', help='Query parameter')

    def custom_args(p, name):
        if name == 'help':
            p.add_argument('path', nargs='*', help='Command path to describe')
        elif name == 'completion':
            p.add_argument('shell', choices=['bash', 'zsh', 'fish', 'powershell'])
        elif name in {'connection set'}:
            p.add_argument('backend_url')
        elif name == 'connection test':
            p.add_argument('backend_url', nargs='?')
        elif name == 'connection auth set' or name == 'settings hf-token set':
            p.add_argument('--stdin', action='store_true', required=True)
        elif name in {'speak', 'voices compare'}:
            if name == 'voices compare':
                p.add_argument('ids', nargs='+')
                p.add_argument('--out-dir', required=True)
                p.add_argument('--text', required=True)
            else:
                p.add_argument('text', nargs='?')
                p.add_argument('--text-file')
            p.add_argument('--voice', required=name == 'speak')
            p.add_argument('--engine', help='Per-request TTS engine override')
            p.add_argument('--language')
            p.add_argument('--speed', type=positive, default=1)
            p.add_argument('--seed', type=int)
            p.add_argument('--instruct')
        elif name in {'transcribe', 'translate-audio'}:
            p.add_argument('input')
            p.add_argument('--model', default='whisper-1', help='ASR compatibility alias or the already-active ASR engine ID')
            p.add_argument('--language')
            p.add_argument('--format', choices=['text', 'json', 'verbose_json', 'srt', 'vtt'], default='text')
            p.add_argument('--stdin-filename', default='audio.wav')
        elif name in {'voices clone', 'voices design'}:
            p.add_argument('--name', required=True)
            p.add_argument('--language', default='Auto')
            p.add_argument('--engine')
            if name.endswith('clone'):
                p.add_argument('--audio', required=True)
                p.add_argument('--ref-text', default='')
            else:
                p.add_argument('--description', required=True)
        elif name == 'voices describe':
            p.add_argument('description')
        elif name == 'voices consent show':
            p.add_argument('id')
        elif name in {'projects rename'}:
            p.add_argument('id'); p.add_argument('name')
        elif name in {'history star', 'history unstar'}:
            p.add_argument('id')
        elif name == 'models list':
            p.add_argument('--installed', action='store_true')
        elif name in {'models install', 'models install-cancel', 'models license accept'}:
            p.add_argument('id', help='Catalogue model ID from models list')
            if name != 'models install-cancel':
                p.add_argument('--accept-license', action='store_true', help='Explicitly accept the displayed current model terms')
        elif name in {'engines list', 'engines select', 'engines install', 'engines install-status'}:
            p.add_argument('--type', choices=['tts', 'asr', 'llm', 'translation', 'diarisation'],
                           required=name != 'engines list')
            if name != 'engines list':
                p.add_argument('id')
            if name == 'engines select':
                p.add_argument('--model', help='Supported engine-specific checkpoint ID')
        elif name in {'settings get', 'settings set'}:
            p.add_argument('key', choices=list(SETTINGS), metavar='SETTING', nargs='?' if name == 'settings get' else None,
                           help='Setting name (see the list below)' + ('; omit to show all' if name == 'settings get' else ''))
            if name == 'settings set':
                p.add_argument('value', help='New value: JSON or plain text')
            p.formatter_class = argparse.RawDescriptionHelpFormatter
            p.epilog = 'Settings:\n' + '\n'.join(f'  {key:<21} {text}' for key, text in SETTING_HELP.items())
        elif name == 'integrations mcp config':
            p.add_argument('--client', default='generic', choices=['generic', 'claude-code', 'cursor', 'codex'])
        elif name == 'integrations mcp bindings set':
            p.add_argument('client'); p.add_argument('--voice', required=True)
        elif name == 'dub export':
            p.add_argument('id')
            p.add_argument('--format', choices=list(DUB_EXPORTS), default='video')
            p.add_argument('--language')

    def wanted(path):
        return only is None or tuple(path.split()) == tuple(only)

    for spec in COMMANDS:
        if spec.name not in custom:
            for path, description, hidden in paths(spec.name):
                if wanted(path):
                    registry_args(command(path, spec.name, description, hidden), spec)
    for name in sorted(custom, key=lambda name: (len(name.split()), name)):
        for path, description, hidden in paths(name):
            if wanted(path):
                custom_args(command(path, name, description, hidden), name)

    # Groups without a handler show their own remaining command tree.
    for key, parser in nodes.items():
        parser.set_defaults(current_parser=parser)
        name = parser.get_default('handler')
        spec = parser.get_default('spec')
        hidden = set()
        if not key:
            hidden = {'output', 'overwrite', 'yes'}
        elif parser.get_default('handler') is None:
            hidden = set(GLOBAL_FLAGS)  # a group only lists its commands
        else:
            if name in OFFLINE:
                hidden |= {'url', 'timeout', 'output', 'overwrite'}
            if not (spec and spec.destructive):
                hidden.add('yes')
        for action in parser._actions:
            if action.dest in hidden:
                action.help = argparse.SUPPRESS
    # A command whose subcommands are all former names lists no "Commands:" section.
    for group in groups.values():
        if not group._choices_actions:
            group.help = argparse.SUPPRESS
    return root, nodes


DESCRIPTIONS = {
    'version': 'Show the installed VoiceStudio version',
    'help': 'Show help for a command, for example: voicestudio help voices clone',
    'status': 'Show whether VoiceStudio is running',
    'stop': 'Stop VoiceStudio when it runs in the background for terminal commands',
    'speak': 'Turn text into speech with a saved voice (by name or ID)',
    'transcribe': 'Turn speech in an audio file into text',
    'translate-audio': 'Turn speech in an audio file into English text',
    'connection show': 'Show which VoiceStudio backend commands use',
    'connection set': 'Use another VoiceStudio backend, for example on another computer',
    'connection test': 'Check that the selected backend responds',
    'connection auth set': 'Save the API key for the selected backend (read from stdin)',
    'connection auth clear': 'Forget the saved API key',
    'voices clone': 'Create a voice from a recording',
    'voices design': 'Create a voice from a text description',
    'voices describe': 'Preview how a text description maps to voice-design settings',
    'voices compare': 'Speak the same text with several voices to compare them',
    'voices consent show': 'Show the consent recorded for a voice',
    'projects rename': 'Rename a project',
    'history star': 'Star a generated clip',
    'history unstar': 'Remove the star from a generated clip',
    'models list': 'List available models',
    'models install': "Download a model (after you accept its license)",
    'models install-cancel': 'Stop a model download',
    'models license accept': "Accept a model's license terms",
    'engines list': 'List engines',
    'engines select': 'Choose the engine used for a task',
    'engines install': 'Install an engine',
    'engines install-status': 'Show engine installation progress',
    'settings get': 'Show settings',
    'settings set': 'Change a setting',
    'settings hf-token set': 'Save a Hugging Face token (read from stdin)',
    'integrations api docs': 'Show the address of the API documentation',
    'integrations mcp config': 'Print the configuration that connects an AI agent to VoiceStudio',
    'integrations mcp bindings set': 'Choose the voice an AI agent uses',
    'dub export': 'Save a dubbed video, audio track or subtitles',
    'update': "Open the app's updater to check for and install an update",
    'update info': 'Show how VoiceStudio is installed and how it updates',
    'update check': "Open the app's updater to check for an update",
    'completion': 'Print a script that enables Tab completion for your shell',
}
GROUPS = {
    'voices': 'Your saved voices: create, change, share',
    'voices reference': "A voice's reference recording",
    'voices image': "A voice's picture",
    'voices consent': 'Consent recorded for cloned voices',
    'gallery': 'Voice gallery, presets and community voices',
    'gallery presets': 'Ready-made voice presets',
    'gallery community': 'Voices shared by the community',
    'dictation': 'Speech-to-text dictation',
    'dictation preferences': 'How dictation behaves',
    'audiobook': 'Audiobooks and stories: plan, render, resume, download',
    'dub': 'Dub videos into other languages',
    'dub subtitles': 'Subtitles of a dubbing job',
    'dub transcript': 'Transcript of a dubbing job',
    'batch': 'Dub many videos in a queue',
    'jobs': 'Background jobs',
    'history': 'Generated clips',
    'projects': 'Projects and their glossaries',
    'projects glossary': 'Terms a project always translates the same way',
    'audio': 'Audio tools: clean, normalize, convert, watermark',
    'audio watermark': 'Audio watermarks',
    'pronunciation': 'Pronunciation rules',
    'engines': 'Speech, transcription and translation engines',
    'engines quants': 'Size and quality variants of the GGUF engine',
    'models': 'Download models and manage their licenses and memory',
    'models license': 'Model license terms',
    'settings': 'App settings',
    'settings providers': 'AI text providers',
    'settings skills': 'AI text skills',
    'settings hf-token': 'Hugging Face token',
    'settings storage': 'Disk space',
    'sharing': 'Let other devices use this VoiceStudio',
    'sharing tailscale': 'Sharing over Tailscale',
    'integrations': 'Connect other apps and AI agents',
    'integrations api': 'HTTP API',
    'integrations mcp': 'AI agents (MCP)',
    'integrations mcp bindings': 'Voices chosen for AI agents',
    'calls': 'Phone calls',
    'diagnostics': 'Troubleshooting information',
    'connection': 'Which VoiceStudio backend commands use',
    'connection auth': 'API key for the selected backend',
}
# Renamed commands: internal ID (kept for contracts and policies) -> public path.
# The former path keeps working, hidden from help, so existing scripts do not break.
RENAMED = {
    'voices inspect': 'voices show-file',
    'voices reference get': 'voices reference download',
    'dub transcript get': 'dub transcript show',
    'dub history-delete': 'dub history delete',
    'settings hf-token status': 'settings hf-token show',
    'models install-status': 'models progress',
    'models install-cancel': 'models cancel',
    'engines install-status': 'engines progress',
    'gallery save-voice': 'gallery use',
    'audiobook jobs': 'audiobook resumable',
    'jobs longform list': 'audiobook finished',
    'jobs longform delete': 'audiobook delete',
    'stories render': 'audiobook render-story',
    'stories encode': 'audio encode',
}
# Kept only for compatibility: duplicates or folded into another command.
HIDDEN = {'stories resume', 'update info', 'update check'}
GLOBAL_FLAGS = ('url', 'json', 'verbose', 'timeout', 'output', 'overwrite', 'yes')
# Commands that never contact a backend; backend options would do nothing.
OFFLINE = {'version', 'help', 'stop', 'completion', 'update', 'update info', 'update check',
           'connection set', 'connection auth clear'}
SETTING_HELP = {
    'compute-device': 'Processor used to run models (CPU or a GPU)',
    'performance-profile': 'Preference for speed or quality',
    'history-retention': 'How many generated clips are kept',
    'hf-mirror': 'Mirror used for Hugging Face downloads',
    'analytics': 'Whether anonymous usage analytics are shared',
    'pause-fill': 'What fills pauses: silence or room_tone',
}
SETTINGS = {
    'compute-device': ('compute-device', 'value'),
    'performance-profile': ('performance-profile', 'tier'),
    'history-retention': ('history-retention', 'cap'),
    'hf-mirror': ('hf-mirror', 'url'),
    'analytics': ('analytics', 'enabled'),
    'pause-fill': ('pause-fill', 'mode'),
}
DUB_EXPORTS = {'video': 'download', 'audio': 'download-audio', 'mp3': 'download-mp3',
               'srt': 'srt', 'vtt': 'vtt', 'ass': 'ass', 'stems': 'export-stems', 'segments': 'export-segments'}


def value(raw):
    try:
        return json.loads(raw, parse_constant=reject_constant)
    except ValueError:
        return raw


def reject_constant(name):
    raise ValueError(f'Non-finite JSON value: {name}')


def assignments(items):
    result = {}
    for item in items:
        key, sep, text = item.partition('=')
        if not sep or not re.fullmatch(r'[A-Za-z_][\w\[\]-]*', key):
            raise CLIError('usage_error', 'Expected NAME=VALUE', exit_code=2)
        if key in result:
            raise CLIError('usage_error', f'Duplicate field: {key}', exit_code=2)
        result[key] = value(text)
    return result


def read_text(path):
    return sys.stdin.read() if path == '-' else Path(path).expanduser().read_text(encoding='utf-8')


def document(path):
    try:
        result = json.loads(read_text(path), parse_constant=reject_constant)
    except ValueError:
        raise CLIError('usage_error', 'Request file contains invalid JSON', exit_code=2) from None
    if not isinstance(result, dict):
        raise CLIError('usage_error', 'Request file must contain a JSON object', exit_code=2)
    return result


def confirm(args, description):
    if getattr(args, 'yes', False):
        return
    if not sys.stdin.isatty():
        raise CLIError('confirmation_required', description + '. Rerun with --yes to confirm.')
    print(description + ' [y/N] ', end='', file=sys.stderr, flush=True)
    if sys.stdin.readline().strip().lower() not in {'y', 'yes'}:
        raise CLIError('cancelled', 'Cancelled; nothing was changed.')


def stream_events(response, *, quiet=False):
    """Decode SSE or NDJSON, refusing error events even after a successful HTTP status."""
    ct = response.headers.get('Content-Type', '')
    sse = 'event-stream' in ct
    last = None
    data = []
    event_type = ''
    buffered = 0
    def decode(text, kind=''):
        try:
            event = json.loads(text)
        except ValueError:
            raise CLIError('invalid_response', 'Backend returned a malformed stream event') from None
        if not isinstance(event, dict):
            raise CLIError('invalid_response', 'Expected a stream event object')
        if kind == 'error' or event.get('type') == 'error' or event.get('error'):
            raise CLIError('backend_error', str(event.get('detail') or event.get('message') or event.get('error') or 'Streaming operation failed'))
        # Never print inline audio chunks into a terminal or JSON metadata stream.
        summary = {k: v for k, v in event.items() if k not in {'pcm', 'audio', 'wav_base64'}}
        if not quiet:
            print(scrub(json.dumps(summary, ensure_ascii=False)), file=sys.stderr)
        return event
    while True:
        line = response.readline(1024 * 1024 + 1)
        if not line:
            break
        if len(line) > 1024 * 1024:
            raise CLIError('invalid_response', 'Stream event exceeded 1 MiB')
        text = line.decode('utf-8').rstrip('\r\n')
        if not sse:
            if text.strip():
                last = decode(text)
        elif not text:
            if data:
                last = decode('\n'.join(data), event_type)
            data, event_type, buffered = [], '', 0
        elif text.startswith('data:'):
            buffered += len(line)
            if buffered > 1024 * 1024:
                raise CLIError('invalid_response', 'Stream event exceeded 1 MiB')
            data.append(text[5:].lstrip(' '))
        elif text.startswith('event:'):
            event_type = text[6:].strip()
    if data:
        last = decode('\n'.join(data), event_type)
    if last is None:
        raise CLIError('incomplete_response', 'Backend stream ended without any events')
    return last


def response_result(client, method, path, args, *, binary=False, stream=False, **kwargs):
    output = getattr(args, 'output', None)
    with client.open(method, path, **kwargs) as response:
        ct = response.headers.get('Content-Type', '')
        if 'event-stream' in ct or 'ndjson' in ct:
            result = stream_events(response, quiet=getattr(args, 'json', False))
            if result.get('type') not in {'done', 'complete', 'completed', 'result'} and stream and method == 'POST':
                raise CLIError('incomplete_response', 'Backend stream ended without a completion event')
            if output:
                audio = result.get('audio_url') or result.get('url')
                if not audio and (result.get('audio_path') or result.get('output')):
                    audio = '/audio/' + quote(str(result.get('audio_path') or result['output']), safe='/')
                if not audio:
                    raise CLIError('invalid_response', 'Completed render has no downloadable result URL')
                return response_result(client, 'GET', audio, args, binary=True)
            return result
        if 'json' in ct:
            raw = response.read(16 * 1024 * 1024 + 1)
            if len(raw) > 16 * 1024 * 1024:
                raise CLIError('invalid_response', 'JSON response exceeded 16 MiB')
            try:
                result = json.loads(raw) if raw else {'ok': True}
            except ValueError:
                raise CLIError('invalid_response', 'Backend returned invalid JSON') from None
            if binary:
                if isinstance(result, dict) and result.get('audio_url') and output:
                    return response_result(client, 'GET', result['audio_url'], args, binary=True)
                raise CLIError('invalid_response', 'Expected audio/file bytes but backend returned JSON')
            if output and isinstance(result, dict) and result.get('output') and stream:
                return response_result(client, 'GET', '/audio/' + quote(str(result['output']), safe='/'), args, binary=True)
            if output:
                return write_json(result, args)
            return result
        if response.status == 204:
            return {'ok': True}
        if binary and not output:
            raise CLIError('usage_error', 'This command needs --output <file> (or - for stdout)', exit_code=2)
        if output:
            args.output_written = True
            return download(response, output, overwrite=getattr(args, 'overwrite', False))
        raw = response.read(16 * 1024 * 1024 + 1)
        if len(raw) > 16 * 1024 * 1024:
            raise CLIError('invalid_response', 'Response too large; use --output')
        if 'text/' not in ct:
            raise CLIError('invalid_response', 'Expected JSON/text response; use --output for files')
        try:
            return raw.decode('utf-8')
        except UnicodeError:
            raise CLIError('invalid_response', 'Backend returned invalid UTF-8 text') from None


def write_json(result, args):
    raw = (json.dumps(result, ensure_ascii=False, indent=2) + '\n').encode()
    response = io.BytesIO(raw)
    response.headers = {'Content-Length': str(len(raw))}
    args.output_written = True
    return download(response, args.output, overwrite=getattr(args, 'overwrite', False))


def resolve_voice(client, selected):
    profiles = client.json('GET', '/profiles')
    if not isinstance(profiles, list):
        raise CLIError('invalid_response', 'Expected a voice profile list')
    for profile in profiles:
        if profile.get('id') == selected:
            return selected
    matches = [p['id'] for p in profiles if p.get('name', '').casefold() == selected.casefold()]
    if len(matches) > 1:
        raise CLIError('ambiguous_voice', 'More than one voice has that name; use a profile ID')
    if not matches:
        raise CLIError('voice_not_found', 'Voice was not found; run voicestudio voices list')
    return matches[0]


def dispatch(args):
    name = args.handler
    if name == 'completion':
        from .completion import script
        return script(args.shell)
    if name == 'version':
        if getattr(args, 'verbose', False):
            return updates.install_info(installed_application())
        return {'version': app_version()} if getattr(args, 'json', False) else 'VoiceStudio ' + app_version()
    if name == 'connection set':
        from .config import normalize_url
        config = load_config(); config['url'] = normalize_url(args.backend_url); save_config(config)
        return {'url': config['url']}
    if name == 'connection auth clear':
        config = load_config(); config.pop('api_key', None); config.pop('credential_url', None); save_config(config)
        return {'cleared': True}
    if name.startswith('update'):
        app = installed_application()
        info = updates.install_info(app)
        if name == 'update info':
            return info
        # The app's updater is the only update check; the CLI makes no network requests.
        return updates.open_app_updater(info)

    url, key, source = connection(getattr(args, 'url', None))
    quiet = getattr(args, 'json', False)
    if name == 'stop':
        if source != 'default':
            raise CLIError('usage_error', 'stop manages this machine\'s VoiceStudio app; it never stops a backend '
                           'selected with --url, VOICESTUDIO_URL or a saved connection.', exit_code=2)
        from .runtime import stop_backend
        return stop_backend(url)
    if name == 'connection show':
        return {'url': url, 'source': source, 'authenticated': bool(key)}
    if name == 'connection auth set':
        secret = sys.stdin.read().strip()
        if not secret or any(c in secret for c in '\r\n'):
            raise CLIError('usage_error', 'Expected one nonempty credential on stdin', exit_code=2)
        args.redaction_secrets.append(secret)
        from .config import normalize_url
        Client(url, secret)  # Validate transport before saving anything.
        config = load_config(); config.update(api_key=secret, credential_url=normalize_url(url)); save_config(config)
        return {'saved': True, 'url': url}
    test_url = getattr(args, 'backend_url', None)
    if test_url and test_url.rstrip('/') != url:
        key = ''  # A one-off probe must not forward another endpoint's credential.
    client = Client(test_url or url, key, getattr(args, 'timeout', 600))
    if name in {'status', 'connection test'}:
        if source == 'default' and not test_url:
            from .runtime import LocalClient
            client = LocalClient(url, key, getattr(args, 'timeout', 600), name, quiet=quiet)
        try:
            health = client.health()
        except CLIError as exc:
            if name != 'status' or exc.code != 'backend_stopped':
                raise
            # "Not running" is an answer, not a failure; exit 3 as `systemctl status` does.
            args.exit_code = 3
            return {'running': False, 'message': 'VoiceStudio is not running.'}
        version = health.get('version', '')
        return {'running': True, 'url': client.url, **health,
                'message': f'VoiceStudio {version} is running at {client.url}.'.replace('  ', ' ')}
    if name == 'integrations api docs':
        return client.url + '/docs'
    if name == 'integrations mcp config':
        headers = {'X-OmniVoice-Client-Id': args.client}
        if args.client == 'codex':
            return ('[mcp_servers.voicestudio]\nurl = ' + json.dumps(client.url + '/mcp/') +
                    '\nhttp_headers = { "X-OmniVoice-Client-Id" = "codex" }')
        entry = {'url': client.url + '/mcp/', 'headers': headers}
        return {'mcpServers': {'voicestudio': entry}}
    if source == 'default':
        from .runtime import LocalClient
        client = LocalClient(url, key, getattr(args, 'timeout', 600), name, quiet=quiet)
    if name == 'integrations mcp bindings set':
        return client.json('PUT', '/api/mcp/bindings', payload={'client_id': args.client,
                           'label': args.client, 'profile_id': resolve_voice(client, args.voice)})
    if name == 'models list':
        result = client.json('GET', '/models')
        if args.installed:
            result['models'] = [m for m in result['models'] if m['installed']]
        return result
    if name in {'engines list', 'engines select', 'engines install', 'engines install-status'}:
        if name == 'engines list':
            return client.json('GET', '/engines' + ('/' + args.type if args.type else ''))
        if name == 'engines select':
            if args.type in {'translation', 'diarisation'}:
                if args.model:
                    raise CLIError('usage_error', '--model is not supported by this engine family', exit_code=2)
                return client.json('POST', f'/engines/{args.type}/select', payload={'engine_id': args.id})
            payload = {'family': args.type, 'backend_id': args.id}
            if args.model:
                payload['model_id'] = args.model
            return client.json('POST', '/engines/select', payload=payload)
        if args.type == 'translation':
            route = f'/engines/translation/{quote(args.id, safe="")}/install'
            if name.endswith('status'):
                return client.json('GET', '/engines/translation')
        else:
            route = f'/engines/sidecar/{quote(args.id, safe="")}/install'
        return client.json('GET' if name.endswith('status') else 'POST', route + ('/status' if name.endswith('status') else ''))
    if name in {'models install', 'models license accept', 'models install-cancel'}:
        return model_operation(client, args)
    if name == 'voices describe':
        return client.json('POST', '/design/describe', payload={'description': args.description})
    if name == 'voices consent show':
        profile = client.json('GET', '/profiles/' + quote(args.id, safe=''))
        return {k: v for k, v in profile.items() if 'consent' in k or 'attest' in k or k == 'id'}
    if name in {'voices clone', 'voices design'}:
        form = {'name': args.name, 'language': args.language}
        if not args.name.strip():
            raise CLIError('usage_error', 'Voice name must not be empty', exit_code=2)
        if args.engine:
            form['engine'] = args.engine
        files = {}
        if name.endswith('clone'):
            form.update(kind='clone', ref_text=args.ref_text); files['ref_audio'] = args.audio
        else:
            design = client.json('POST', '/design/describe', payload={'description': args.description})
            if not design.get('matched'):
                raise CLIError('validation_error', 'No supported voice attributes matched the description')
            form.update(kind='design', vd_states=json.dumps(design['attrs']), instruct=design['instruct'])
        return client.json('POST', '/profiles', form=form, files=files)
    if name in {'speak', 'voices compare'}:
        return speak(client, args)
    if name in {'transcribe', 'translate-audio'}:
        form = {'model': args.model, 'response_format': args.format}
        if args.language:
            form['language'] = args.language
        route = '/v1/audio/' + ('transcriptions' if name == 'transcribe' else 'translations')
        with input_file(args.input, args.stdin_filename) as input_path:
            return response_result(client, 'POST', route, args, form=form, files={'file': input_path})
    if name == 'projects rename':
        return client.json('PATCH', '/projects/' + quote(args.id, safe=''), payload={'name': args.name})
    if name in {'history star', 'history unstar'}:
        return client.json('PUT', '/history/' + quote(args.id, safe='') + '/starred', payload={'starred': name.endswith(' star')})
    if name == 'settings get' and not args.key:
        result = {}
        for setting, (route, field) in SETTINGS.items():
            try:
                state = client.json('GET', '/api/settings/' + route)
            except CLIError as exc:
                if exc.status != 404:
                    raise
                continue  # an older backend without this setting
            result[setting] = state.get(field, state) if isinstance(state, dict) else state
        return result
    if name in {'settings get', 'settings set'}:
        route, field = SETTINGS[args.key]
        return client.json('GET' if name.endswith('get') else 'PUT', '/api/settings/' + route,
                           **({} if name.endswith('get') else {'payload': {field: value(args.value)}}))
    if name == 'settings hf-token set':
        token = sys.stdin.read().strip()
        if not token:
            raise CLIError('usage_error', 'Expected a token on stdin', exit_code=2)
        args.redaction_secrets.append(token)
        return client.json('POST', '/api/settings/hf-token', payload={'token': token})
    if name == 'dub export':
        if not getattr(args, 'output', None):
            raise CLIError('usage_error', 'Export needs --output', exit_code=2)
        return response_result(client, 'GET', '/dub/' + DUB_EXPORTS[args.format] + '/' + quote(args.id, safe=''), args,
                               binary=True, query={'lang': args.language} if args.language else None)
    return generic(client, args)


@contextmanager
def input_file(path, filename='audio.wav'):
    if path != '-':
        yield Path(path).expanduser()
        return
    suffix = Path(filename).suffix
    with tempfile.TemporaryDirectory(prefix='voicestudio-stdin-') as directory:
        temp = Path(directory) / ('stdin' + suffix)
        with temp.open('wb') as output:
            while chunk := sys.stdin.buffer.read(64 * 1024):
                output.write(chunk)
        yield temp


def speak(client, args):
    if args.handler == 'voices compare':
        directory = Path(args.out_dir); directory.mkdir(parents=True, exist_ok=True)
        results = []
        from copy import copy
        for index, voice in enumerate(args.ids):
            child = copy(args); child.handler = 'speak'; child.voice = voice
            child.output = str(directory / f'voice-{index + 1}.wav'); child.text_file = None
            results.append(speak(client, child))
        return results
    if bool(args.text is not None) == bool(args.text_file):
        raise CLIError('usage_error', 'Supply text or --text-file, but not both', exit_code=2)
    text = read_text(args.text_file) if args.text_file else args.text
    if not text.strip():
        raise CLIError('usage_error', 'Text must not be empty', exit_code=2)
    if not getattr(args, 'output', None):
        raise CLIError('usage_error', 'Speech generation needs --output', exit_code=2)
    form = {'text': text, 'profile_id': resolve_voice(client, args.voice), 'speed': args.speed}
    for key in ['engine', 'language', 'seed', 'instruct']:
        if getattr(args, key, None) is not None:
            form[key] = getattr(args, key)
    return response_result(client, 'POST', '/generate', args, form=form, binary=True)


def model_operation(client, args):
    if args.handler == 'models install-cancel':
        return client.json('POST', '/models/install/cancel', payload={'repo_id': args.id})
    model = next((m for m in client.json('GET', '/models')['models'] if m['repo_id'] == args.id), None)
    if model is None:
        raise CLIError('model_not_found', 'Unknown catalogue model; run voicestudio models list')
    if args.handler == 'models install' and model.get('installed'):
        return {'message': 'Model is already installed', 'id': args.id}
    if not model.get('supported', True):
        raise CLIError('unsupported_model', 'This model is unsupported on the backend host')
    terms = client.json('GET', '/models/licenses/acceptance/' + quote(args.id, safe=''))
    acceptance = terms.get('acceptance', terms)
    if acceptance.get('required') and not acceptance.get('accepted'):
        details = client.json('GET', '/models/licenses/details/' + quote(args.id, safe=''))
        if not getattr(args, 'json', False):
            print(scrub(json.dumps(details, ensure_ascii=False, indent=2)), file=sys.stderr)
        if not args.accept_license:
            raise CLIError('license_required', 'Review the model terms, then rerun with --accept-license to accept them explicitly.', details=details)
        client.json('POST', '/models/licenses/accept', payload={'repo_id': args.id,
                    'fingerprint': acceptance['fingerprint'], 'accepted': True})
    if args.handler == 'models license accept':
        return {'id': args.id, 'accepted': True}
    return client.json('POST', '/models/install', payload={'repo_id': args.id, 'target': 'local'})


def generic(client, args):
    spec = args.spec
    if spec.binary and not getattr(args, 'output', None):
        raise CLIError('usage_error', 'This command needs --output', exit_code=2)
    contracts = json.loads(Path(__file__).with_name('contracts.json').read_text())
    contract = contracts.get(spec.name, {})
    if args.file == '-' and spec.upload and args.input == '-':
        raise CLIError('usage_error', 'JSON fields and upload cannot both read stdin', exit_code=2)
    fields = document(args.file) if args.file else {}
    fields.update(assignments(args.field))
    remember_secrets(fields, args.redaction_secrets)
    query = assignments(args.query)
    for key in list(fields):
        if key in contract.get('query', []):
            query[key] = fields.pop(key)
    declared = contract.get('fields', [])
    unknown = set(fields) - set(declared)
    if declared and unknown:
        raise CLIError('usage_error', 'Unknown request fields: ' + ', '.join(sorted(unknown)), exit_code=2)
    for required in contract.get('required_query', []):
        if required not in query:
            raise CLIError('usage_error', f'Missing query parameter {required}; use --query {required}=VALUE', exit_code=2)
    files = {spec.upload: args.input} if spec.upload and spec.upload != '@raw' else {}
    for required in contract.get('required', []):
        if required not in fields and required not in files:
            raise CLIError('usage_error', f'Missing request field {required}; use --file or --field {required}=VALUE', exit_code=2)
    route = spec.route.format(**{param: quote(getattr(args, param), safe='') for param in spec.params})
    if spec.destructive:
        confirm(args, f'Confirm {spec.name}')
    encoding = contract.get('encoding', '')
    kwargs = {'query': query}
    if spec.upload == '@raw':
        kwargs['raw_file'] = args.input
    elif files or encoding in {'multipart/form-data', 'application/x-www-form-urlencoded'}:
        kwargs.update(form=fields, files=files)
    elif fields or encoding == 'application/json':
        kwargs['payload'] = fields
    if spec.upload:
        with input_file(args.input, args.stdin_filename) as uploaded:
            if spec.upload == '@raw':
                kwargs['raw_file'] = uploaded
            else:
                kwargs['files'] = {spec.upload: uploaded}
            return response_result(client, spec.method, route, args, binary=spec.binary, stream=spec.stream, **kwargs)
    return response_result(client, spec.method, route, args, binary=spec.binary, stream=spec.stream, **kwargs)


def emit(result, args):
    if getattr(args, 'output', None) and not getattr(args, 'output_written', False):
        result = write_json(result, args)
    if getattr(args, 'output', None) == '-':
        return
    text = json.dumps(result, ensure_ascii=False, indent=2) if getattr(args, 'json', False) else render(result)
    key = os.environ.get('OMNIVOICE_API_KEY', '')
    text = scrub(text, [key, *getattr(args, 'redaction_secrets', [])])
    if not text:
        return
    sys.stdout.write(text)
    if not text.endswith('\n'):
        sys.stdout.write('\n')


def remember_secrets(value, secrets):
    if isinstance(value, dict):
        for key, child in value.items():
            if str(key).lower() in {'api_key', 'token', 'password', 'access_token', 'refresh_token', 'pin'} and isinstance(child, str):
                secrets.append(child)
            else:
                remember_secrets(child, secrets)
    elif isinstance(value, list):
        for child in value:
            remember_secrets(child, secrets)


def main(argv=None):
    # Piped text has the same encoding on Windows, macOS and Linux.
    for stream in [sys.stdin, sys.stdout, sys.stderr]:
        if hasattr(stream, 'reconfigure'):
            try:
                stream.reconfigure(encoding='utf-8')
            except (OSError, ValueError):
                pass  # Embedders may already have read from a text stream.
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ['__complete']:
        # Called by the shell on every Tab; must stay fast and never fail loudly.
        from .completion import complete
        try:
            for line in complete(argv[1:]):
                print(line)
        except Exception:
            pass
        return 0
    json_errors = '--json' in argv
    secrets = [os.environ.get('OMNIVOICE_API_KEY', '')]
    try:
        argv, desktop_arguments = split_arguments(argv)
        if not argv:
            return launch_desktop(desktop_arguments)
        remember_launcher_arguments(desktop_arguments)
        root, nodes = build_parser()
        args = root.parse_args(argv)
        if not hasattr(args, 'handler'):
            args.current_parser.print_help()
            return 0
        if args.handler == 'help':
            target = nodes.get(tuple(args.path))
            if target is None:
                raise CLIError('usage_error', 'Unknown help command path; run voicestudio --help.', exit_code=2)
            target.print_help()
            return 0
        if hasattr(args, 'output') and args.output != '-':
            target = Path(args.output).expanduser()
            if target.exists() and not getattr(args, 'overwrite', False):
                raise CLIError('output_exists', 'Output exists; use --overwrite to replace it.')
            if not target.parent.is_dir():
                raise CLIError('file_error', 'Output directory does not exist.')
        if args.handler != 'version' and not args.handler.startswith('update'):
            secrets.append(load_config().get('api_key', ''))
        args.redaction_secrets = secrets
        result = dispatch(args)
        emit(result, args)
        return getattr(args, 'exit_code', 0)
    except CLIError as exc:
        message = scrub(str(exc), secrets)
        if json_errors:
            detail = {'code': exc.code, 'message': message}
            if exc.status is not None:
                detail['status'] = exc.status
            if exc.retry_after:
                detail['retry_after'] = exc.retry_after
            if exc.details is not None:
                detail['details'] = exc.details
            print(scrub(json.dumps({'error': detail}, ensure_ascii=False), secrets), file=sys.stderr)
        else:
            print(f'voicestudio: {message}', file=sys.stderr)
            if exc.details is not None and '--verbose' in argv:
                print(f'voicestudio: details: {scrub(str(exc.details), secrets)}', file=sys.stderr)
        return exc.exit_code
    except KeyboardInterrupt:
        print(json.dumps({'error': {'code': 'cancelled', 'message': 'Cancelled.'}}) if json_errors else
              'voicestudio: Cancelled.', file=sys.stderr)
        return 130
    except BrokenPipeError:
        # Prevent a second failure when Python flushes stdout during shutdown.
        try:
            sink = os.open(os.devnull, os.O_WRONLY)
            try:
                os.dup2(sink, sys.stdout.fileno())
            finally:
                os.close(sink)
        except (OSError, io.UnsupportedOperation):
            pass
        return 1
    except (OSError, UnicodeError) as exc:
        code, message = 'file_error', scrub(str(exc), secrets)
    except Exception as exc:
        code = 'internal_error'
        message = 'Unexpected CLI error. Rerun with --verbose for diagnostic details.'
        if '--verbose' in argv:
            message += ' ' + type(exc).__name__ + ': ' + scrub(str(exc), secrets)
    if json_errors:
        print(json.dumps({'error': {'code': code, 'message': message}}), file=sys.stderr)
    else:
        print('voicestudio: ' + message, file=sys.stderr)
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
