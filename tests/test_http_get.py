import sys
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import build_feed as bf  # noqa: E402


class HttpRetryTests(unittest.TestCase):
    def test_broken_pipe_and_reset_are_retried(self):
        ok = mock.MagicMock()
        ok.__enter__.return_value.read.return_value = b"ok"
        calls = [BrokenPipeError(32, "Broken pipe"), ConnectionResetError(54, "reset"), ok]

        def fake_urlopen(req, timeout):
            result = calls.pop(0)
            if isinstance(result, Exception):
                raise result
            return result

        with mock.patch.object(bf.urllib.request, "urlopen", fake_urlopen), mock.patch.object(bf.time, "sleep"), \
                mock.patch.object(bf, "log"):
            self.assertEqual(bf.http_get("https://api.queridodiario.org.br/gazettes"), b"ok")

    def test_gives_up_after_the_last_attempt(self):
        with mock.patch.object(bf.urllib.request, "urlopen", side_effect=BrokenPipeError(32, "Broken pipe")), \
                mock.patch.object(bf.time, "sleep"), mock.patch.object(bf, "log"):
            with self.assertRaises(BrokenPipeError):
                bf.http_get("https://x", attempts=2)

    def test_http_errors_still_retried(self):
        error = urllib.error.HTTPError("https://x", 522, "origin down", {}, None)
        with mock.patch.object(bf.urllib.request, "urlopen", side_effect=error) as urlopen, \
                mock.patch.object(bf.time, "sleep"), mock.patch.object(bf, "log"):
            with self.assertRaises(urllib.error.HTTPError):
                bf.http_get("https://x", attempts=3)
        self.assertEqual(urlopen.call_count, 3)


if __name__ == "__main__":
    unittest.main()
