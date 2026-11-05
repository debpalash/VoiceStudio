"""VoiceStudio application releases; installing an update belongs to the app's own updater."""
from .application import installed_application
from .errors import CLIError


def install_info(app=None):
    app = app or installed_application()
    instructions = {
        'source': 'Update and rebuild VoiceStudio from its source checkout.',
        'store': ('Update VoiceStudio through Snap.' if app.distribution == 'snap-store' else
                  'Update VoiceStudio through the Mac App Store.'),
        'directory': 'Install an application package to enable managed updates.',
    }
    automatic = app.method in {'appimage', 'dmg', 'nsis', 'deb'}
    fallback = ('Run voicestudio update, or use Settings → Updates in the app.' if automatic
                else 'Update VoiceStudio using its application installer.')
    return app.info() | {'automatic': automatic, 'instructions': instructions.get(app.method, fallback)}


def open_app_updater(info):
    """Hand the update to the app, which checks, downloads, verifies and installs it.

    The CLI makes no network requests of its own: the app's updater is the one
    sanctioned update check, and it uses the channel chosen in Settings → Updates.
    """
    if not info['automatic']:
        raise CLIError('update_unsupported', info['instructions'])
    from .desktop import CHECK_UPDATES_FLAG, desktop_process
    desktop_process([CHECK_UPDATES_FLAG], detached=True)
    return {'current': info['version'], 'opened': True,
            'message': f'VoiceStudio {info["version"]} opened Settings → Updates to check for an update. '
                       'Updates download and install from there.'}
