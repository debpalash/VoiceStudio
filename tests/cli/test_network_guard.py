"""The CLI test subprocess guard blocks every way out to a non-loopback address."""
import os
from pathlib import Path
import subprocess
import sys

GUARD = Path(__file__).resolve().parent / 'network_guard'
# 192.0.2.1 is TEST-NET-1: never routed, so even an unguarded attempt goes nowhere.
PROBE = r'''
import socket
results = []
for name, attempt in [
    ('connect', lambda s: s.connect(('192.0.2.1', 9))),
    ('connect_ex', lambda s: s.connect_ex(('192.0.2.1', 9))),
]:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.2)
        try:
            attempt(sock)
            results.append(name + ':allowed')
        except OSError as exc:
            results.append(name + (':blocked' if 'forbidden' in str(exc) else ':allowed'))
with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
    for name, args in [('sendto', (('192.0.2.1', 9),)), ('sendto-flags', (0, ('192.0.2.1', 9)))]:
        try:
            sock.sendto(b'x', *args)
            results.append(name + ':allowed')
        except OSError as exc:
            results.append(name + (':blocked' if 'forbidden' in str(exc) else ':allowed'))
    sock.sendto(b'x', ('127.0.0.1', 9))  # loopback stays allowed
print(' '.join(results))
'''


def test_guard_blocks_connect_connect_ex_and_sendto():
    env = {**os.environ, 'PYTHONPATH': str(GUARD)}
    result = subprocess.run([sys.executable, '-c', PROBE], env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == ['connect:blocked', 'connect_ex:blocked',
                                     'sendto:blocked', 'sendto-flags:blocked']
