"""Private CLI configuration, independent of Electron browser storage."""
import json
import os
from pathlib import Path
import tempfile
import sys
from urllib.parse import urlsplit
import ipaddress

from .errors import CLIError


def config_path() -> Path:
    override = os.environ.get('VOICESTUDIO_CONFIG')
    base = Path(os.environ.get('XDG_CONFIG_HOME', str(Path.home() / '.config')))
    return Path(override).expanduser() if override else base / 'voicestudio-cli' / 'config.json'


def cache_dir() -> Path:
    if os.environ.get('VOICESTUDIO_CONFIG'):
        return config_path().parent / 'cache'
    if sys.platform == 'darwin':
        return Path.home() / 'Library/Caches/voicestudio-cli'
    if sys.platform == 'win32':
        return Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData/Local')) / 'voicestudio-cli/Cache'
    return Path(os.environ.get('XDG_CACHE_HOME', Path.home() / '.cache')) / 'voicestudio-cli'


def load_config() -> dict:
    try:
        value = json.loads(config_path().read_text(encoding='utf-8'))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        raise CLIError('config_error', f'Cannot read CLI configuration: {exc}') from exc
    if not isinstance(value, dict):
        raise CLIError('config_error', 'CLI configuration must be a JSON object')
    for key in ['url', 'api_key', 'credential_url']:
        if key in value and not isinstance(value[key], str):
            raise CLIError('config_error', f'CLI configuration field {key} must be text')
    return value


def save_config(value: dict) -> None:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix='.config-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(value, stream, indent=2)
            stream.write('\n')
        os.replace(temp, path)
    finally:
        Path(temp).unlink(missing_ok=True)


def normalize_url(value: str) -> str:
    try:
        url = urlsplit(value)
        port = url.port
    except (ValueError, TypeError) as exc:
        raise CLIError('usage_error', 'Invalid backend URL', exit_code=2) from exc
    if (url.scheme not in {'http', 'https'} or not url.hostname or
            url.username or url.password or url.query or url.fragment or
            any(ord(c) < 33 for c in value)):
        raise CLIError('usage_error', 'Use an http(s) backend URL without credentials, query or fragment', exit_code=2)
    if port == 0:
        raise CLIError('usage_error', 'Backend port must be between 1 and 65535', exit_code=2)
    return value.rstrip('/')


def loopback(host: str | None) -> bool:
    if host == 'localhost':
        return True
    try:
        return ipaddress.ip_address(host or '').is_loopback
    except ValueError:
        return False


def connection(explicit: str | None) -> tuple[str, str, str]:
    config = load_config()
    candidates = [(explicit, 'flag'), (os.environ.get('VOICESTUDIO_URL'), 'environment'),
                  (config.get('url'), 'configuration')]
    chosen = next(((value, source) for value, source in candidates if value), None)
    if chosen:
        raw, source = chosen
    else:
        raw_port = os.environ.get('OMNIVOICE_PORT', '3900')
        if not raw_port.isascii() or not raw_port.isdecimal() or not 0 < int(raw_port) < 65536:
            raise CLIError('config_error', 'OMNIVOICE_PORT must be an integer from 1 to 65535')
        raw, source = f'http://127.0.0.1:{int(raw_port)}', 'default'
    url = normalize_url(raw)
    # Credentials saved for one endpoint must never follow a --url override elsewhere.
    saved_key = config.get('api_key', '') if config.get('credential_url') == url else ''
    key = os.environ.get('OMNIVOICE_API_KEY', saved_key).strip()
    if any(c in key for c in '\r\n'):
        raise CLIError('config_error', 'Invalid API credential')
    if key and urlsplit(url).scheme != 'https' and not loopback(urlsplit(url).hostname):
        raise CLIError('credential_transport', 'Remote credentials require HTTPS')
    return url, key, source
