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


if __name__ == "__main__":
    unittest.main()
