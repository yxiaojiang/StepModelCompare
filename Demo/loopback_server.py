"""Rehearsal server entry point with a loopback-only Python socket audit hook."""

import argparse
import ipaddress
import socket
import sys


def loopback(host):
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except (TypeError, ValueError):
        return False


def only_loopback(event, arguments):
    if event in ("socket.bind", "socket.connect", "socket.sendto"):
        address = arguments[1]
        if isinstance(address, tuple) and not loopback(address[0]):
            raise PermissionError("rehearsal: non-loopback socket")
    if event in ("socket.getaddrinfo", "socket.gethostbyname", "socket.gethostbyaddr"):
        if not loopback(arguments[0]):
            raise PermissionError("rehearsal: non-loopback DNS")


# Windows multiprocessing re-imports this entry point in each alignment worker.
sys.addaudithook(only_loopback)


if __name__ == "__main__":
    import uvicorn

    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    with socket.socket() as probe:
        try:
            probe.connect(("192.0.2.1", 80))
        except PermissionError as error:
            print(f"Outbound canary rejected: {error}", flush=True)
        else:
            raise RuntimeError("External socket probe was not denied")
    uvicorn.run("app:app", host="127.0.0.1", port=args.port, access_log=False)
