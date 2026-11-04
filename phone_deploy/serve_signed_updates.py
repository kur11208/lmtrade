"""Read-only update server: publish only the three signed release artifacts.

This server never lists directories or serves source, logs, runtime DBs, or keys.
It is LAN-only when bound to an explicitly supplied local address.
"""
from __future__ import annotations

import argparse
import ipaddress
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

RELEASE_DIR = Path(__file__).resolve().parent
ALLOWED = frozenset({
    "/etf_mobile_pull.tar.gz",
    "/etf_mobile_pull.sha256",
    "/etf_mobile_pull.sha256.hmac",
})
MAX_ARCHIVE_BYTES = 40 * 1024 * 1024


class SignedArtifactHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.serve_release(include_body=True)

    def do_HEAD(self):
        self.serve_release(include_body=False)

    def serve_release(self, include_body: bool) -> None:
        if self.path not in ALLOWED:
            self.send_error(404, "Not found")
            return
        file = RELEASE_DIR / self.path.lstrip("/")
        try:
            if file.is_symlink() or not file.is_file():
                raise FileNotFoundError
            length = file.stat().st_size
            if length > MAX_ARCHIVE_BYTES:
                raise ValueError("Oversized release")
            with file.open("rb") as body:
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Length", str(length))
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                if include_body:
                    while block := body.read(64 * 1024):
                        self.wfile.write(block)
        except (FileNotFoundError, ValueError):
            self.send_error(404, "Not found")
        except (BrokenPipeError, ConnectionResetError):
            pass


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve signed update files only")
    parser.add_argument("--host", required=True, help="Explicit PC interface IP (no wildcards)")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    host = ipaddress.ip_address(args.host)
    if host.is_unspecified or host.is_multicast:
        parser.error("Wildcard and multicast bindings are forbidden")
    if not 1 <= args.port <= 65535:
        parser.error("Invalid port")
    for filename in ALLOWED:
        file = RELEASE_DIR / filename.lstrip("/")
        if not file.is_file() or file.is_symlink():
            parser.error(f"Signed update release is missing: {filename}")
    server = ThreadingHTTPServer((str(host), args.port), SignedArtifactHandler)
    print(f"Signed updates only: http://{host}:{args.port}/", flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
