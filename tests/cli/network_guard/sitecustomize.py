"""Subprocess test guard: CLI tests may contact loopback servers only."""
import ipaddress
import socket

original_connect = socket.socket.connect
original_connect_ex = socket.socket.connect_ex
original_sendto = socket.socket.sendto
original_getaddrinfo = socket.getaddrinfo


def allowed(host):
    if host == 'localhost':
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def check(sock, address):
    if sock.family in {socket.AF_INET, socket.AF_INET6} and not allowed(address[0]):
        raise OSError('External network access forbidden in CLI tests')


def connect(self, address):
    check(self, address)
    return original_connect(self, address)


def connect_ex(self, address):
    check(self, address)
    return original_connect_ex(self, address)


def sendto(self, data, *args):
    check(self, args[-1])  # sendto(data, address) or sendto(data, flags, address)
    return original_sendto(self, data, *args)


def getaddrinfo(host, *args, **kwargs):
    if not allowed(host):
        raise OSError('External DNS access forbidden in CLI tests')
    return original_getaddrinfo(host, *args, **kwargs)


socket.socket.connect = connect
socket.socket.connect_ex = connect_ex
socket.socket.sendto = sendto
socket.getaddrinfo = getaddrinfo
