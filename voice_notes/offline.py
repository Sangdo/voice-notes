"""Keep recordings on this computer: block every non-local network connection in this process."""
import ipaddress
import os
import socket

_LOCAL = {'localhost', '', None}


class NetworkBlocked(OSError):
    pass


def _is_local(address):
    host = address[0] if isinstance(address, tuple) else address
    if isinstance(host, bytes):
        host = host.decode()
    if host in _LOCAL or not isinstance(host, str):
        # Unix-domain sockets (str paths) and AF_UNIX are local by definition.
        return True
    try:
        return ipaddress.ip_address(host.split('%')[0]).is_loopback
    except ValueError:
        return host == 'localhost'


def enable():
    """Call before importing any library that might phone home."""
    for key in ('HF_HUB_OFFLINE', 'TRANSFORMERS_OFFLINE', 'HF_HUB_DISABLE_TELEMETRY', 'DO_NOT_TRACK'):
        os.environ[key] = '1'
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex

    def guard(sock, address):
        if sock.family in (socket.AF_INET, socket.AF_INET6) and not _is_local(address):
            raise NetworkBlocked(f'Offline mode: blocked a connection to {address[0]}')

    def connect(sock, address):
        guard(sock, address)
        return original_connect(sock, address)

    def connect_ex(sock, address):
        guard(sock, address)
        return original_connect_ex(sock, address)

    def getaddrinfo(host, *args, **kwargs):
        if not _is_local(host):
            raise NetworkBlocked(f'Offline mode: blocked a lookup of {host}')
        return _getaddrinfo(host, *args, **kwargs)

    _getaddrinfo = socket.getaddrinfo
    socket.socket.connect = connect
    socket.socket.connect_ex = connect_ex
    socket.getaddrinfo = getaddrinfo
