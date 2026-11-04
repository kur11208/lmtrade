"""Check the update HTTP server never exposes unrelated files."""
from __future__ import annotations

import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import serve_signed_updates


class SignedArtifactServerTests(unittest.TestCase):
    def test_exact_release_allowlist_and_no_directory_listing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in (
                "etf_mobile_pull.tar.gz",
                "etf_mobile_pull.sha256",
                "etf_mobile_pull.sha256.hmac",
            ):
                (root / name).write_bytes(b"safe-release")
            (root / "private-secret.key").write_text("NOT-PUBLIC")
            with patch.object(serve_signed_updates, "RELEASE_DIR", root):
                server = ThreadingHTTPServer(
                    ("127.0.0.1", 0), serve_signed_updates.SignedArtifactHandler
                )
                worker = threading.Thread(target=server.serve_forever, daemon=True)
                worker.start()
                try:
                    url = f"http://127.0.0.1:{server.server_address[1]}"
                    with urllib.request.urlopen(url + "/etf_mobile_pull.sha256") as response:
                        self.assertEqual(response.read(), b"safe-release")
                    for forbidden in (
                        "/", "/private-secret.key", "/phone_auto_update.sh",
                        "/etf_mobile_pull.sha256?download=1",
                        "/../private-secret.key",
                    ):
                        with self.subTest(forbidden=forbidden):
                            with self.assertRaises(urllib.error.HTTPError) as exc:
                                urllib.request.urlopen(url + forbidden)
                            self.assertEqual(exc.exception.code, 404)
                finally:
                    server.shutdown()
                    server.server_close()
                    worker.join(timeout=3)


if __name__ == "__main__":
    unittest.main()
