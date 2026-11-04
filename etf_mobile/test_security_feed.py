"""Regression tests: the market-data write endpoint must fail closed."""
import io
import os
import unittest
from unittest.mock import patch

import daytrade_app


def make_handler(token=""):
    handler = object.__new__(daytrade_app.Handler)
    handler.path = "/feed/bars"
    handler.headers = {
        "X-Daytrade-Feed-Token": token,
        "Content-Length": "2",
    }
    handler.rfile = io.BytesIO(b"{}")
    captured = []
    handler.send_response_content = lambda status, content_type, body, **kw: captured.append(status)
    return handler, captured


class FeedAuthenticationTests(unittest.TestCase):
    def test_no_secret_rejects_post(self):
        with patch.dict(os.environ, {}, clear=True):
            handler, responses = make_handler()
            handler.do_POST()
        self.assertEqual(responses, [401])

    def test_wrong_or_short_secret_rejects_post(self):
        for key, supplied in [("short", "short"), ("A" * 40, "B" * 40)]:
            with self.subTest(key=key), patch.dict(os.environ, {"DAYTRADE_FEED_TOKEN": key}):
                handler, responses = make_handler(supplied)
                handler.do_POST()
                self.assertEqual(responses, [401])

    def test_correct_long_token_allows_valid_post(self):
        key = "A" * 40
        with patch.dict(os.environ, {"DAYTRADE_FEED_TOKEN": key}):
            handler, responses = make_handler(key)
            with patch.object(daytrade_app, "accept_feed_bars", return_value={"ok": True}):
                handler.do_POST()
            self.assertEqual(responses, [200])


class ReadAuthenticationTests(unittest.TestCase):
    def test_remote_read_requires_token(self):
        with patch.dict(os.environ, {"DAYTRADE_FEED_TOKEN": "A" * 40}):
            handler, responses = make_handler()
            handler.path = "/device.json"
            handler.client_address = ("192.168.0.20", 12345)
            handler.do_GET()
            self.assertEqual(responses, [401])
            handler, responses = make_handler()
            handler.path = "/"
            handler.client_address = ("192.168.0.20", 12345)
            handler.do_HEAD()
            self.assertEqual(responses, [401])

    def test_local_read_stays_accessible(self):
        with patch.dict(os.environ, {}, clear=True):
            handler, responses = make_handler()
            handler.path = "/not-found"
            handler.client_address = ("127.0.0.1", 12345)
            handler.do_GET()
            self.assertEqual(responses, [404])

    def test_remote_authenticated_read(self):
        with patch.dict(os.environ, {"DAYTRADE_FEED_TOKEN": "A" * 40}):
            handler, responses = make_handler("A" * 40)
            handler.path = "/not-found"
            handler.client_address = ("192.168.0.20", 12345)
            handler.do_GET()
            self.assertEqual(responses, [404])


if __name__ == "__main__":
    unittest.main()
