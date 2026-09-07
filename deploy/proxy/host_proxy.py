"""
Minimal, allowlist-restricted HTTP proxy (CONNECT + absolute-form GET/POST).

WHY THIS EXISTS
---------------
On this campus network the portal (jw.smbu.edu.cn) only completes a TLS
handshake with the host's authenticated wired interface. A Docker container
behind the WSL2 NAT is refused at the TLS layer (verified: the container can
reach the public internet fine, and --network host does not help). Running this
proxy on the Windows host lets the container tunnel out through a source
address the campus already trusts.

SECURITY
--------
This is NOT an open proxy. Every request is checked against an allowlist of
destination host suffixes (default: smbu.edu.cn). Anything else gets a 403, so
it cannot be abused as a general-purpose relay on the campus LAN.

USAGE (on the Windows host)
---------------------------
    python host_proxy.py --bind 0.0.0.0 --port 8899

Then point the container at it:
    docker run ... -e SMBU_UPSTREAM_PROXY=http://host.docker.internal:8899 smbu-calendar:latest
"""

from __future__ import annotations

import argparse
import select
import socket
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

CONNECT_TIMEOUT = 15.0
IDLE_TIMEOUT = 120.0
BUFSIZE = 65536

DEFAULT_ALLOW = "smbu.edu.cn"

# Headers we must not forward through the tunnel.
HOP_BY_HOP = {
    "proxy-connection",
    "proxy-authenticate",
    "proxy-authorization",
    "connection",
    "keep-alive",
}


def _allowed(host: str, allow: tuple[str, ...]) -> bool:
    host = (host or "").lower().rstrip(".")
    return any(host == a or host.endswith("." + a) for a in allow)


def _pump(a: socket.socket, b: socket.socket) -> None:
    """Copy bytes both ways until either side closes or we go idle."""
    socks = [a, b]
    try:
        while True:
            readable, _, errored = select.select(socks, [], socks, IDLE_TIMEOUT)
            if errored or not readable:
                return
            for s in readable:
                try:
                    data = s.recv(BUFSIZE)
                except OSError:
                    return
                if not data:
                    return
                dst = b if s is a else a
                try:
                    dst.sendall(data)
                except OSError:
                    return
    finally:
        for s in socks:
            try:
                s.close()
            except OSError:
                pass


class ProxyHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    allow: tuple[str, ...] = ()

    def log_message(self, fmt, *args):  # quiet by default
        pass

    def _deny(self, host: str) -> None:
        self.send_error(403, f"destination not allowed: {host}")

    # -------------------------------------------------------------- HTTPS
    def do_CONNECT(self) -> None:
        host, _, port_s = self.path.partition(":")
        host = host.strip("[]")
        if not _allowed(host, self.allow):
            return self._deny(host)
        try:
            port = int(port_s or 443)
        except ValueError:
            self.send_error(400, "bad port")
            return
        try:
            upstream = socket.create_connection((host, port), timeout=CONNECT_TIMEOUT)
        except OSError as exc:
            self.send_error(502, f"connect failed: {exc}")
            return

        self.send_response(200, "Connection Established")
        self.end_headers()
        self.close_connection = True
        _pump(self.connection, upstream)

    # --------------------------------------------------------------- HTTP
    def _absolute(self) -> None:
        parts = urlsplit(self.path)
        host = parts.hostname or ""
        if not _allowed(host, self.allow):
            return self._deny(host)

        port = parts.port or (443 if parts.scheme == "https" else 80)
        try:
            upstream = socket.create_connection((host, port), timeout=CONNECT_TIMEOUT)
        except OSError as exc:
            self.send_error(502, f"connect failed: {exc}")
            return

        path = parts.path or "/"
        if parts.query:
            path = f"{path}?{parts.query}"
        version = self.requestline.split(" ")[-1] if self.requestline else "HTTP/1.1"

        lines = [f"{self.command} {path} {version}"]
        host_seen = False
        for key, value in self.headers.items():
            low = key.lower()
            if low == "host":
                host_seen = True
            if low in HOP_BY_HOP:
                continue
            lines.append(f"{key}: {value}")
        if not host_seen:
            lines.append(f"Host: {host}")
        lines.append("Connection: close")

        try:
            upstream.sendall(("\r\n".join(lines) + "\r\n\r\n").encode("latin-1"))
        except OSError:
            upstream.close()
            self.send_error(502)
            return
        self.close_connection = True
        _pump(self.connection, upstream)

    do_GET = _absolute
    do_POST = _absolute
    do_HEAD = _absolute


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--bind", default="0.0.0.0", help="interface to listen on")
    ap.add_argument("--port", type=int, default=8899)
    ap.add_argument(
        "--allow",
        default=DEFAULT_ALLOW,
        help="comma-separated destination host suffixes (default: smbu.edu.cn)",
    )
    ap.add_argument(
        "--log-file",
        default="",
        help="redirect stdout/stderr here (needed when run under pythonw, "
        "which has no console to inherit)",
    )
    args = ap.parse_args()

    if args.log_file:
        # Text mode + line buffering: print() writes str, and output must be
        # visible promptly even though pythonw gives us no console.
        log = open(args.log_file, "a", buffering=1, encoding="utf-8")
        sys.stdout = sys.stderr = log

    allow = tuple(a.strip().lower().lstrip(".") for a in args.allow.split(",") if a.strip())
    if not allow:
        raise SystemExit("--allow must list at least one destination suffix")

    handler = type("BoundProxyHandler", (ProxyHandler,), {"allow": allow})
    srv = ThreadingHTTPServer((args.bind, args.port), handler)
    srv.daemon_threads = True
    print(f"host proxy on {args.bind}:{args.port} allow={allow}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
