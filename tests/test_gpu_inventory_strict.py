"""Provisioning must reject partial adapter reads while inventory stays advisory."""
import importlib
from types import SimpleNamespace

import pytest


class Key:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


@pytest.mark.parametrize('failure', ['enumeration', 'identity', 'open'])
def test_partial_registry_is_not_safe_for_repair(failure):
    """A recoverable advisory read must not become provisioning evidence."""
    inventory = importlib.import_module('core.gpu_inventory')
    def enum(key, index):
        if index == 0:
            return '0000'
        error = OSError('incomplete enumeration')
        error.winerror = 5 if failure == 'enumeration' else 259
        raise error
    def open_key(*args):
        if failure == 'open' and len(args) > 1 and args[1] == '0000':
            raise OSError('inaccessible adapter')
        return Key()
    def value(key, name):
        if name == 'MatchingDeviceId':
            if failure == 'identity':
                raise OSError('missing identity')
            return (r'PCI\VEN_1002&DEV_73DF', 0)
        return ('Radeon' if name == 'DriverDesc' else 0, 0)
    registry = SimpleNamespace(OpenKey=open_key, EnumKey=enum, QueryValueEx=value,
                               HKEY_LOCAL_MACHINE='root')
    inventory._read_windows(registry)
    with pytest.raises(OSError):
        inventory._read_windows(registry, require_complete=True)


def test_strict_registry_accepts_complete_identity():
    """The documented end-of-enumeration status is accepted by strict reads."""
    inventory = importlib.import_module('core.gpu_inventory')
    def end(*args):
        error = OSError('no more entries')
        error.winerror = 259
        raise error
    registry = SimpleNamespace(OpenKey=lambda *args: Key(), EnumKey=end,
                               HKEY_LOCAL_MACHINE='root')
    assert inventory._read_windows(registry, require_complete=True) == ()


def test_overlay_selection_uses_complete_snapshot(monkeypatch):
    """A repaired advisory read must not hide an additional unsupported adapter."""
    recipe = importlib.import_module('core.windows_rocm.recipe')
    inventory = importlib.import_module('core.gpu_inventory')
    monkeypatch.setattr(recipe.sys, 'platform', 'win32')
    monkeypatch.setattr(inventory, 'detect_host_gpus', lambda: pytest.fail('advisory reread'))
    gpu = inventory.HostGPU
    assert recipe.detected_rdna2_arch(inventory=[gpu('amd', 'Radeon', pci_device_id='73df')]) == 'gfx1031'
    with pytest.raises(Exception, match='Multiple AMD'):
        recipe.detected_rdna2_arch(inventory=[gpu('amd', 'Radeon', pci_device_id='73df'),
                                            gpu('amd', 'Unknown', pci_device_id='abcd')])
