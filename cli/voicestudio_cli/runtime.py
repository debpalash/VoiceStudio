"""Reach the desktop app's local backend, starting the app in the background.

The CLI never launches the Python backend itself. When no backend answers, it
starts the installed desktop app without a window; the app's own supervisor
starts the backend with the same environment and port selection as the GUI,
publishes the address in ``backend.json``, and stops after an idle period.
Opening the app later shows a window in that same process.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from .config import loopback
from .desktop import BACKGROUND_FLAG, STOP_FLAG, desktop_process
from .errors import CLIError
from .transport import Client

# Questions about live state must not create a backend just to answer "nothing".
OBSERVE = frozenset('''batch cancel|batch delete|batch download|batch list|batch show|batch retry|
calls delete|calls events|calls hangup|calls list|calls say|calls show|calls takeover|
diagnostics flush-memory|diagnostics notifications|diagnostics system|dub cancel|
engines install-status|jobs cancel|models install-cancel|models install-status|
models loaded|models unload|sharing disable|sharing status|sharing tailscale disable|
sharing tailscale status|status|connection test'''.replace('\n', '').split('|'))

DISCOVERY_FILE = 'backend.json'


def user_data():
    if override := os.environ.get('VOICESTUDIO_USER_DATA'):
        return Path(override).expanduser()
    if sys.platform == 'darwin':
        return Path.home() / 'Library/Application Support/VoiceStudio'
    if sys.platform == 'win32':
        return Path(os.environ.get('APPDATA', Path.home() / 'AppData/Roaming')) / 'VoiceStudio'
    return Path(os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config')) / 'VoiceStudio'


def discovery():
    """The running app's published backend record, or None."""
    try:
        record = json.loads((user_data() / DISCOVERY_FILE).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    if not isinstance(record, dict) or record.get('schema') != 1 or not isinstance(record.get('url'), str):
        return None
    from urllib.parse import urlsplit
    try:
        parsed = urlsplit(record['url'])
        _ = parsed.port  # raises ValueError for an invalid port
    except ValueError:
        return None
    if not record.get('remote') and (parsed.scheme != 'http' or not loopback(parsed.hostname)):
        return None  # A local record must never redirect requests off this machine.
    return record


def healthy(url):
    try:
        Client(url, '', 1).health()
        return True
    except CLIError:
        return False


def running_backend(default_url):
    """The URL of a live local backend: the app's published one, else the default."""
    record = discovery()
    if record and record.get('remote'):
        raise CLIError('remote_backend',
                       f'VoiceStudio is connected to the remote backend {record["url"]}. Use it with '
                       f'"voicestudio connection set {record["url"]}" and "voicestudio connection auth set --stdin".')
    # The default port may belong to another profile; a chosen profile is only
    # reached through its own published record.
    fallback = [] if os.environ.get('VOICESTUDIO_USER_DATA') else [default_url]
    for url in ([record['url']] if record else []) + fallback:
        if healthy(url):
            return url, record if record and record['url'] == url else None
    return None, record


def _check_stage(record):
    stage = record.get('stage') if record else None
    if stage == 'setup_required':
        raise CLIError('runtime_not_ready', 'VoiceStudio has not finished setup. Open VoiceStudio, complete '
                       'setup, then run this command again.')
    if stage in {'failed', 'crashed', 'port_in_use'}:  # the app does not restart these on its own
        raise CLIError('backend_failed', 'VoiceStudio could not start its backend. Open VoiceStudio to see '
                       'why and repair it, then run this command again.')


def startup_budget():
    try:
        value = float(os.environ.get('OMNIVOICE_STARTUP_BUDGET_S', ''))
    except ValueError:
        value = 0
    # Electron's low-spec budget doubles its default; never give up before it.
    return value if value > 0 else 600


def start_backend(default_url, *, quiet=False):
    """Start the desktop app in the background and wait for its backend."""
    before = discovery()
    started = time.monotonic()
    try:
        child = desktop_process([BACKGROUND_FLAG], detached=True)
    except CLIError as exc:
        if exc.code != 'desktop_not_found':
            raise
        raise CLIError('backend_stopped', 'VoiceStudio is not running and the desktop app was not found. '
                       'Install VoiceStudio, set VOICESTUDIO_DESKTOP_APP, or pass --url for another '
                       'backend.') from None
    if not quiet:
        print('Starting VoiceStudio in the background…', file=sys.stderr, flush=True)
    deadline = started + startup_budget()
    while time.monotonic() < deadline:
        url, record = running_backend(default_url)
        if url:
            return url
        code = child.poll()
        # A record left from before this launch may describe a process that is
        # gone; trust it once it changed, or once an existing instance took over.
        if record and (record != before or code == 0):
            _check_stage(record)
        if code not in (None, 0):
            raise CLIError('backend_failed', f'VoiceStudio exited during startup (status {code}). Open '
                           'VoiceStudio to see why.')
        time.sleep(.5)
    raise CLIError('runtime_timeout', 'VoiceStudio did not finish starting its backend in time. Open '
                   'VoiceStudio to see its progress, or run "voicestudio status".')


def stop_backend(default_url):
    record = discovery()
    url = record['url'] if record and not record.get('remote') else None
    if not url or not healthy(url):
        if not os.environ.get('VOICESTUDIO_USER_DATA') and healthy(default_url):
            raise CLIError('not_app_owned', f'VoiceStudio at {default_url} cannot be stopped from the terminal: it '
                           'was started by an older VoiceStudio app, a source checkout or Docker. Quit it where it '
                           'was started (update VoiceStudio to stop it from the terminal next time).')
        return {'stopped': False, 'message': 'VoiceStudio is not running.'}
    if record.get('window'):
        raise CLIError('app_open', 'VoiceStudio is open in a window. Quit it from the app to stop it.')
    try:
        desktop_process([STOP_FLAG]).wait(timeout=30)
    except subprocess.TimeoutExpired:
        pass  # still running; the health poll below reports the timeout
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if not healthy(url):
            return {'stopped': True, 'message': 'VoiceStudio stopped.'}
        time.sleep(.5)
    raise CLIError('stop_timeout', 'VoiceStudio did not stop within 30 seconds; quit it from the app.')


class LocalClient(Client):
    """The default target: this machine's desktop app, started when needed."""

    def __init__(self, url, key, timeout, command, *, quiet=False):
        super().__init__(url, key, timeout)
        self.command = command
        self.quiet = quiet
        self.remote = None

    def _resolve(self):
        url, _record = running_backend(self.url)
        if not url:
            if self.command in OBSERVE:
                raise CLIError('backend_stopped', 'VoiceStudio is not running, so nothing is active. Open '
                               'VoiceStudio, or run any other command to start it in the background.')
            url = start_backend(self.url, quiet=self.quiet)
        if url != self.url:
            # A saved credential is bound to its origin; only the environment key may follow.
            self.key = os.environ.get('OMNIVOICE_API_KEY', '').strip()
        self.url = url
        self.remote = Client(url, self.key, self.timeout)

    def open(self, method, path, **kwargs):
        if self.remote is None:
            self._resolve()
        return self.remote.open(method, path, **kwargs)

    def health(self):
        if self.remote is None:
            self._resolve()
        return self.remote.health()
