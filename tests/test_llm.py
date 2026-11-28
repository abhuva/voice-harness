from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
import unittest

from voice_harness.contracts import DeliveryConfirmed, ReplyCompleted, ReplyFailed, ReplyText, TurnRequest, VoiceError
from voice_harness.llm import ChatClient, KINDROID, LlmProfile, chunks
from voice_harness.provider import PostRejected
from voice_harness.settings import Settings


class LlmTests(unittest.TestCase):
    def test_saved_profile_and_activation_are_independent_of_other_settings(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            settings = Settings(root, root)
            profiles, active = settings.load_llm()
            self.assertEqual(active, KINDROID)
            profile = replace(profiles["Gemini Fast"], name="Test profile", stream=False, temperature=1.25)
            settings.save_llm({profile.name: profile}, profile.name)
            settings.save_phone_output("desktop")
            self.assertEqual(Settings(root, root).load_llm(), ({profile.name: profile}, profile.name))
            self.assertEqual(settings.load_phone_output(), "desktop")

    def test_streaming_bounds_and_finish_markers(self):
        body = json.dumps({"choices": [{"delta": {"content": "Grüße"}}]}, ensure_ascii=False).encode()
        stream = (b": ping\r\n\r\ndata: " + body + b"\r\n\r\ndata: [DONE]\n\n")
        stop = Event()
        self.assertEqual(list(chunks((bytes((b,)) for b in stream), True, stop)), ["Grüße"])
        with self.assertRaisesRegex(VoiceError, "before completion"):
            list(chunks(iter([b"data: " + body + b"\n"]), True, stop))

    def test_context_is_fresh_for_each_client_and_incomplete_reply_does_not_enter_history(self):
        payloads = []

        def post(_, endpoint, key, payload, stop, **kwargs):
            payloads.append(payload)
            if payload["messages"][-1]["content"] == "reject":
                raise PostRejected(401)
            yield b'{"choices":[{"message":{"content":"Fixture reply."}}]}'

        profile = replace(LlmProfile(), stream=False, system_prompt="Fixture system")
        first = ChatClient(profile, "fixture", manager=object(), post=post)
        stop = Event()
        expected = [DeliveryConfirmed(), ReplyText("Fixture reply."), ReplyCompleted()]
        self.assertEqual(list(first.send(TurnRequest("one"), stop)), expected)
        self.assertEqual(list(first.send(TurnRequest("two"), stop)), expected)
        failed, = first.send(TurnRequest("reject"), stop)
        self.assertIsInstance(failed, ReplyFailed)
        self.assertEqual(failed.delivery, "not_sent")
        fresh = ChatClient(profile, "fixture", manager=object(), post=post)
        self.assertEqual(list(fresh.send(TurnRequest("fresh"), stop)), expected)
        self.assertEqual([item["content"] for item in payloads[1]["messages"]],
                         ["Fixture system", "one", "Fixture reply.", "two"])
        self.assertEqual([item["content"] for item in payloads[-1]["messages"]], ["Fixture system", "fresh"])

    def test_openrouter_key_cannot_be_reused_at_a_different_host(self):
        with self.assertRaisesRegex(VoiceError, "separate credential"):
            replace(LlmProfile(), endpoint="https://example.invalid/v1/chat/completions").validate()
        replace(LlmProfile(), endpoint="http://127.0.0.1:3000/v1/chat/completions", credential="").validate()
