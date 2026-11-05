"""Subprocess test guard: CLI tests may contact loopback servers only."""
import ipaddress
import socket

original_connect = socket.socket.connect
original_getaddrinfo = socket.getaddrinfo


def allowed(host):
    if host == 'localhost':
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def connect(self, address):
    if self.family in {socket.AF_INET, socket.AF_INET6} and not allowed(address[0]):
        raise OSError('External network access forbidden in CLI tests')
    return original_connect(self, address)


def getaddrinfo(host, *args, **kwargs):
    if not allowed(host):
        raise OSError('External DNS access forbidden in CLI tests')
    return original_getaddrinfo(host, *args, **kwargs)


socket.socket.connect = connect
socket.getaddrinfo = getaddrinfo
