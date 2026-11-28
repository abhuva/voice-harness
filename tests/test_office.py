from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from threading import Event, Thread
import unittest

from voice_harness.contracts import Message
from voice_harness.office import OfficeJevos
from voice_harness.office_worker import Handler, Worker, loopback_url
from voice_harness.work_types import Lineage


class OfficeTests(unittest.TestCase):
    def server(self, handler):
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.addCleanup(server.server_close)
        Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.shutdown)
        return server, f"http://127.0.0.1:{server.server_port}"

    def test_loopback_broker_for_proof_of_existing_jevos(self):
        sent = []

        class ExistingJevos(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def answer(self, value):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(json.dumps(value).encode())

            def do_GET(self):
                self.answer({"status": "ready"})

            def do_POST(self):
                sent.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
                self.answer({"model": "jevos-v5", "answers": {"0": {"type": "noul", "noul": 0.7}}})

        _, upstream = self.server(ExistingJevos)
        broker, url = self.server(Handler)
        broker.worker = Worker(upstream, "fixture", "OpenVINO")
        broker.worker.metrics = (2000, None)
        from time import monotonic
        broker.worker.sampled = monotonic()
        client = OfficeJevos(url)
        reading = client.status()
        self.assertEqual((reading.state, reading.backend, reading.ram), ("ready", "OpenVINO", 2000))
        messages = [Message(str(i), "Marc", f"Fixture {i}") for i in range(3)]
        result = client.score(Lineage("fixture", "job-1", "2"), messages, ("Testing?",), Event())
        self.assertEqual(result, (0.7,))
        self.assertEqual(sent[0]["state"], "Marc:\nFixture 0\n\nMarc:\nFixture 1\n\nMarc:\nFixture 2")
        self.assertEqual(sent[0]["questions"]["0"]["instructions"], "Testing?")
        with self.assertRaises(ValueError):
            loopback_url("https://example.invalid/")
