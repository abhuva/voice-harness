from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, Thread
import unittest
from unittest.mock import MagicMock, Mock, patch

from PyQt6.QtNetwork import QNetworkAccessManager
from PyQt6.QtWidgets import QApplication

from voice_harness.contracts import VoiceError
from voice_harness.provider import decode_reply, read_history, stream_reply


class ProviderTests(unittest.TestCase):
    def test_decoder_handles_utf8_and_split_error_markers(self):
        body = "   Hi, it’s Hannah.".encode()
        self.assertEqual("".join(decode_reply((bytes([b]) for b in body), Event())), body.decode().strip())
        for marker in (b"<<HTTP_ERROR>>", b"<<ERROR>>"):
            for cut in range(1, len(marker)):
                stream = decode_reply([b"Hello. " + marker[:cut], marker[cut:] + b"private"], Event())
                self.assertEqual(next(stream), "Hello. ")
                with self.assertRaises(VoiceError) as caught:
                    next(stream)
                self.assertNotIn("private", str(caught.exception))

    def test_history_requests_one_forward_page_and_preserves_cooldown(self):
        now = 1_800_000_000
        cursor = (now - 24 * 3600) * 1000
        items = [{"id": str(i), "sender": "user", "message": "Fixture",
                  "timestamp": cursor + i + 1, **extra}
                 for i, extra in enumerate([{}, {"type": "call_message"},
                                            {"source": "api", "sender_type": "ai"}])]
        response = MagicMock(status_code=200)
        response.__enter__.return_value = response
        response.iter_content.return_value = [json.dumps({
            "messages": items + items[:1], "pagination": {"hasMore": True},
        }).encode()]
        get = Mock(return_value=response)
        with TemporaryDirectory() as directory, patch("voice_harness.provider.time.time", return_value=now):
            guard = Path(directory) / "next"
            rows, more = read_history(get, "fixture", "example", guard, Event())
            self.assertTrue(more)
            self.assertEqual([r[1] for r in rows], ["?", "VK", "API"])
            self.assertEqual(get.call_args.kwargs["params"],
                             {"ai_id": "example", "limit": 100, "start_after_timestamp": cursor})
            with self.assertRaisesRegex(VoiceError, "cooldown"):
                read_history(get, "fixture", "example", guard, Event())
            guard.unlink()
            response.status_code = 429
            response.headers = {"Retry-After": "600"}
            with self.assertRaisesRegex(VoiceError, "429"):
                read_history(get, "fixture", "example", guard, Event())
            self.assertEqual(float(guard.read_text()), now + 600)
            self.assertEqual(get.call_count, 2)

    def test_qt_sends_exactly_one_post_and_never_follows_redirect(self):
        app = QApplication.instance() or QApplication([])
        posts = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_POST(self):
                data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                posts.append((self.path, data))
                self.send_response(307 if data["message"] == "redirect" else 200)
                if data["message"] == "redirect":
                    self.send_header("Location", "/redirected")
                self.end_headers()
                self.wfile.write(b"Test reply.")

        with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
            producer = Thread(target=server.serve_forever, daemon=True)
            producer.start()
            manager = QNetworkAccessManager()
            try:
                with patch("voice_harness.provider.BASE", f"http://127.0.0.1:{server.server_port}/"):
                    self.assertEqual("".join(stream_reply(manager, "fixture", "example", "hello", Event())),
                                     "Test reply.")
                    with self.assertRaisesRegex(VoiceError, "307"):
                        list(stream_reply(manager, "fixture", "example", "redirect", Event()))
                self.assertEqual([path for path, _ in posts], ["/send-message", "/send-message"])
                self.assertEqual(posts[0][1], {"ai_id": "example", "message": "hello", "stream": True})
            finally:
                server.shutdown()
                producer.join(timeout=2)
                app.processEvents()
