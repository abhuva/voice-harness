from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, Thread
from time import monotonic
from urllib.parse import parse_qs, urlsplit
import unittest

from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

from voice_harness.lfm3_playground import Playground


class PlaygroundTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.posts = []
        self.budget_models = []
        self.release = Event()
        self.budget_fixture = None
        test = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def respond(self, body):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(json.dumps(body).encode())

            def do_GET(self):
                url = urlsplit(self.path)
                if url.path == "/props" and test.budget_fixture is not None:
                    test.budget_models.append(parse_qs(url.query).get("model", [None])[0])
                    self.respond({"default_generation_settings": {"n_ctx": test.budget_fixture[1]}})
                else:
                    self.send_error(501)

            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                if self.path == "/apply-template":
                    test.budget_models.append(payload["model"])
                    self.respond({"prompt": payload["messages"][-1]["content"]})
                    return
                if self.path == "/tokenize":
                    test.budget_models.append(payload["model"])
                    self.respond({"tokens": [1] * test.budget_fixture[0]})
                    return
                test.posts.append(payload)
                self.send_response(200)
                self.end_headers()
                try:
                    if payload["stream"]:
                        if payload["messages"][-1]["content"] == "Think":
                            for delta in (
                                {"reasoning_content": "Native thought. "},
                                {"content": "<thi"},
                                {"content": "nk>Raw thought.</th"},
                                {"content": "ink>"},
                            ):
                                body = {"choices": [{"delta": delta}]}
                                self.wfile.write(b"data: " + json.dumps(body).encode() + b"\n\n")
                        body = {"choices": [{"delta": {"content": "<b>Fixture reply</b>"}}]}
                        self.wfile.write(b"data: " + json.dumps(body).encode() + b"\n\n")
                        self.wfile.flush()
                        if payload["messages"][-1]["content"] == "Hold":
                            test.release.wait(3)
                        self.wfile.write(b"data: [DONE]\n\n")
                    else:
                        self.wfile.write(b'{"choices":[{"message":{"content":"Fixture reply."}}]}')
                except (BrokenPipeError, ConnectionResetError):
                    pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.window = Playground(("lfm3-local", "lfm3-abliterated"))
        self.window.endpoint.setText(f"http://127.0.0.1:{self.server.server_port}/v1/chat/completions")
        self.addCleanup(self.close_fixture)

    def wait_for(self, predicate):
        deadline = monotonic() + 4
        while not predicate():
            self.assertLess(monotonic(), deadline, self.window.status.text())
            QTest.qWait(15)

    def close_fixture(self):
        self.release.set()
        self.window.close()
        self.wait_for(lambda: self.window.job is None)
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(1)

    def send(self, text):
        self.window.user.setPlainText(text)
        self.window.send.click()
        self.wait_for(lambda: self.window.job is None)

    def test_switches_the_model_for_budget_and_chat_with_fresh_history(self):
        original, alternate = "lfm3-local", "lfm3-abliterated"
        self.budget_fixture = (25, 4096)
        self.window.system.setPlainText("Shared system prompt")
        self.window.seed.setValue(42)
        self.send("Same prompt")
        self.window.model.setCurrentIndex(1)
        self.send("Same prompt")

        self.assertEqual([post["model"] for post in self.posts], [original, alternate])
        self.assertEqual(self.budget_models, [original] * 3 + [alternate] * 3)
        self.assertEqual(self.posts[0]["messages"], self.posts[1]["messages"])
        self.assertEqual(self.posts[1]["seed"], 42)
        self.send("Follow-up on this model")
        self.assertEqual(len(self.posts[-1]["messages"]), 4)
        self.window.model.setCurrentIndex(0)
        self.send("Fresh original turn")
        self.assertEqual(len(self.posts[-1]["messages"]), 2)
        self.assertIn("lfm3-local", self.window.output.toPlainText())
        self.assertIn("lfm3-abliterated", self.window.output.toPlainText())

    def test_edits_prompts_sampling_and_context(self):
        self.window.system.setPlainText("Fixture system")
        self.window.temperature.setValue(0.55)
        self.window.tokens.setValue(77)
        self.window.top_p.setValue(0.81)
        self.window.top_k.setValue(25)
        self.window.repeat.setValue(1.25)
        self.window.seed.setValue(8)
        self.send("First")
        self.assertIn("<b>Fixture reply</b>", self.window.output.toPlainText())
        first = self.posts[0]
        self.assertEqual((first["temperature"], first["max_tokens"], first["top_p"], first["top_k"],
                          first["repeat_penalty"], first["seed"]), (0.55, 77, 0.81, 25, 1.25, 8))
        self.assertEqual(first["messages"], [{"role": "system", "content": "Fixture system"},
                                             {"role": "user", "content": "First"}])
        self.window.stream.setChecked(False)
        self.send("Follow-up")
        self.assertFalse(self.posts[-1]["stream"])
        self.assertEqual([msg["content"] for msg in self.posts[-1]["messages"]],
                         ["Fixture system", "First", "<b>Fixture reply</b>", "Follow-up"])

        self.window.context.setChecked(False)
        self.send("Independent")
        self.assertEqual(len(self.posts[-1]["messages"]), 2)
        self.window.context.setChecked(True)
        self.window.clear.click()
        self.assertEqual(self.window.output.toPlainText(), "")
        self.assertEqual(self.window.user.toPlainText(), "Independent")
        self.send("After clear")
        self.assertEqual(len(self.posts[-1]["messages"]), 2)
        self.window.clear_prompts.click()
        self.assertEqual(self.window.user.toPlainText(), "")
        self.assertEqual(self.window.system.toPlainText(), "")

    def test_clear_cancels_stream_and_discards_partial_context(self):
        self.window.user.setPlainText("Hold")
        self.window.send.click()
        self.wait_for(lambda: "Fixture reply" in self.window.output.toPlainText())
        self.window.clear.click()
        self.assertEqual(self.window.output.toPlainText(), "")
        self.wait_for(lambda: self.window.job is None)
        self.assertEqual(list(self.window.history), [])
        self.assertEqual(self.window.status.text(), "Chat cleared.")

    def test_prompt_stays_local(self):
        self.window.endpoint.setText("https://example.invalid/v1/chat/completions")
        self.send("Fixture")
        self.assertEqual(self.posts, [])
        self.assertIn("loopback", self.window.status.text())

    def test_loads_and_reloads_large_markdown_without_rewriting_it(self):
        with TemporaryDirectory() as temp:
            path = Path(temp) / "test-prompt.md"
            large = "# Backstory\n" + "A long fixture paragraph.\n" * 500
            path.write_text(large, encoding="utf-8")
            self.window.load_system_file(path)
            self.assertEqual(self.window.system.toPlainText(), large)
            self.send("Fixture")
            self.assertEqual(self.posts[-1]["messages"][0]["content"], large)
            path.write_text("Updated café", encoding="utf-8-sig")
            self.window.reload_prompt.click()
            self.assertEqual(self.window.system.toPlainText(), "Updated café")
            self.assertEqual(path.read_text("utf-8-sig"), "Updated café")

    def test_context_check_refuses_overflow_before_generation(self):
        self.budget_fixture = (4000, 4096)
        self.window.tokens.setValue(192)
        self.send("Fixture")
        self.assertEqual(self.posts, [])
        self.assertIn("exceeds the context", self.window.status.text())
        self.assertEqual(self.window.budget.text(), "4,000 prompt + 192 output / 4,096 context tokens")

    def test_thinking_controls_and_raw_tags_stay_out_of_conversation(self):
        self.window.thinking_mode.setCurrentIndex(self.window.thinking_mode.findData("on"))
        self.window.thinking_limit.setValue(25)
        self.send("Think")
        self.assertTrue(self.posts[0]["chat_template_kwargs"]["enable_thinking"])
        self.assertEqual(self.posts[0]["reasoning_budget_tokens"], 25)
        self.assertIn("Native thought. Raw thought.", self.window.thinking_output.toPlainText())
        self.assertNotIn("thought", self.window.output.toPlainText())
        self.window.thinking_mode.setCurrentIndex(self.window.thinking_mode.findData("off"))
        self.send("Fixture")
        self.assertFalse(self.posts[-1]["chat_template_kwargs"]["enable_thinking"])
        self.assertEqual(self.posts[-1]["messages"][-2]["content"], "<b>Fixture reply</b>")
        self.assertEqual(self.window.thinking_output.toPlainText(), "")
