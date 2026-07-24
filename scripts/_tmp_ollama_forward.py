#!/usr/bin/env python3
"""More robust threaded TCP forwarder: 0.0.0.0:11435 -> 192.168.10.158:11434."""
from __future__ import annotations

import socket
import socketserver
import threading

LISTEN = ("0.0.0.0", 11435)
UPSTREAM = ("192.168.10.158", 11434)
BUF = 256 * 1024


def relay(src: socket.socket, dst: socket.socket) -> None:
    try:
        while True:
            data = src.recv(BUF)
            if not data:
                break
            dst.sendall(data)
    except OSError:
        pass
    finally:
        for s in (src, dst):
            try:
                s.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                s.close()
            except OSError:
                pass


class Handler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        try:
            upstream = socket.create_connection(UPSTREAM, timeout=30)
        except OSError:
            self.request.close()
            return
        upstream.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.request.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        t1 = threading.Thread(target=relay, args=(self.request, upstream), daemon=True)
        t2 = threading.Thread(target=relay, args=(upstream, self.request), daemon=True)
        t1.start()
        t2.start()
        t1.join()
        t2.join()


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def main() -> None:
    print(f"forward {LISTEN} -> {UPSTREAM}", flush=True)
    with Server(LISTEN, Handler) as srv:
        srv.serve_forever()


if __name__ == "__main__":
    main()
