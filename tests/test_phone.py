from contextlib import nullcontext
from importlib.util import find_spec
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, Thread
from types import SimpleNamespace
from uuid import uuid4
import asyncio
import unittest

import numpy as np

from voice_harness.call import run_call
from voice_harness.contracts import CallInput, DeliveryConfirmed, ReplyCompleted, ReplyText, VoiceError
from voice_harness.phone_audio import OUTPUT_RATE, PhoneAudio
from voice_harness.phone_pairing import PhonePairing
from voice_harness.speaker import SynthesizedSpeech
from voice_harness.state import State
from voice_harness.synthesis import PCM


PHONE_MODULES = all(find_spec(name) for name in ("aiohttp", "aiortc"))
if PHONE_MODULES:
    from aiohttp import ClientSession, WSServerHandshakeError, WSMsgType
    from aiohttp.test_utils import TestServer
    from aiortc import RTCConfiguration, RTCPeerConnection, RTCSessionDescription
    from aiortc.sdp import SessionDescription

    from voice_harness.phone_rtc import ReplyTrack, local_ipv4
    from voice_harness.phone_server import PhoneGateway


ORIGIN = "http://127.0.0.1:8768"


class PhonePairingTests(unittest.TestCase):
    def test_short_pins_require_rate_limited_pairing(self):
        now = 0
        pairing = PhonePairing(clock=lambda: now)
        self.assertRegex(pairing.pin, r"^[0-9]{6}$")
        pairing.set_pin("0042")
        for _ in range(5):
            with self.assertRaisesRegex(VoiceError, "Check the pairing PIN"):
                pairing.verify("123")
        with self.assertRaisesRegex(VoiceError, "Too many"):
            pairing.verify("0042")
        now = 60
        pairing.verify("0042")
        pairing.set_pin("7890")
        with self.assertRaises(VoiceError):
            pairing.verify("0042")
        pairing.verify("7890")


@unittest.skipUnless(PHONE_MODULES, "requires scripts/setup-phone.ps1")
class PhoneTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.calls = []
        self.sent = []
        self.errors = []
        self.receiver = None
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.state = State(Path(self.temp.name))
        self.gateway = PhoneGateway(ORIGIN, None, self.run_fixture, lambda audio: audio.disconnect())
        self.server = TestServer(self.gateway.app())
        await self.server.start_server()
        self.addAsyncCleanup(self.server.close)
        self.client = ClientSession()
        self.addAsyncCleanup(self.client.close)

    def run_fixture(self, phone):
        stop = Event()
        phone.bind_call(stop)
        if self.receiver is not None:
            self.receiver.bind_call(stop)
        meter = lambda *_: None
        preferences = SimpleNamespace(speed=1.0, bass=0, mid=0, treble=0)
        synth = SimpleNamespace(render=lambda *_: PCM(OUTPUT_RATE, tone(OUTPUT_RATE, 10).tobytes()))
        speaker = SynthesizedSpeech(synth, (self.receiver or phone).playback(preferences, meter))
        provider = SimpleNamespace(send=self.send)
        mic = phone.microphone(meter)
        recognizer = SimpleNamespace(transcribe=lambda _: "Fixture words")

        def call():
            try:
                run_call(mic, lambda *_: nullcontext(recognizer), speaker, provider, self.state,
                         CallInput(finish=0.25), stop, phone.say, phone.message, lambda: None)
            except Exception as exc:
                self.errors.append(exc)
            finally:
                phone.finish("Fixture ended.")

        thread = Thread(target=call, name="fixture-phone-call")
        self.calls.append((phone, thread))
        thread.start()
        self.addAsyncCleanup(self.join_call, phone, thread)

    async def join_call(self, phone, thread):
        phone.disconnect()
        await asyncio.to_thread(thread.join, 3)
        self.assertFalse(thread.is_alive())
        self.assertEqual(self.errors, [])

    def send(self, request, _stop):
        self.sent.append(request.text)
        yield DeliveryConfirmed()
        yield ReplyText("A local fixture reply.")
        yield ReplyCompleted()

    async def connect(self, mode, **data):
        ws = await self.client.ws_connect(self.server.make_url("/session"), origin=ORIGIN)
        self.addAsyncCleanup(ws.close)
        await ws.send_json({"type": "call", "code": self.gateway.pairing.pin, "mode": mode, **data})
        return ws

    async def until(self, ws, kind):
        async with asyncio.timeout(7):
            while (message := await ws.receive()).type == WSMsgType.TEXT:
                body = message.json()
                if body["type"] == kind:
                    return body
                if body["type"] == "ended":
                    self.fail(body["text"])
        self.fail(message)

    async def test_origin_pairing_and_one_session_are_enforced(self):
        async with self.client.get(self.server.make_url("/")) as page:
            self.assertEqual(page.status, 200)
            self.assertIn("Hands-free", await page.text())
            self.assertIn('inputmode="numeric"', await page.text())
            self.assertNotIn(self.gateway.pairing.pin, await page.text())
        with self.assertRaises(WSServerHandshakeError) as exc:
            await self.client.ws_connect(self.server.make_url("/session"), origin="https://example.invalid")
        self.assertEqual(exc.exception.status, 403)
        async with self.client.ws_connect(self.server.make_url("/session"), origin=ORIGIN) as socket:
            await socket.send_json({"type": "call", "mode": "ptt", "code": "bad"})
            result = await socket.receive_json(timeout=3)
            self.assertEqual(result["type"], "ended")
        self.assertEqual(self.calls, [])
        first = await self.connect("ptt")
        await self.until(first, "ready")
        second = await self.connect("ptt")
        refused = await second.receive_json(timeout=3)
        self.assertIn("already active", refused["text"])
        self.assertEqual(len(self.calls), 1)

    async def test_custom_pin_takes_effect_for_the_next_connection(self):
        previous = self.gateway.pairing.pin
        self.gateway.pairing.set_pin("0971")
        rejected = await self.connect("ptt", code=previous)
        self.assertIn("Check the pairing PIN", (await rejected.receive_json(timeout=3))["text"])
        socket = await self.connect("ptt", code="0971")
        await self.until(socket, "ready")

    async def test_push_to_talk_sends_on_release_and_streams_reply(self):
        ws = await self.connect("ptt")
        await self.until(ws, "ready")
        await ws.send_json({"type": "press"})
        frame = (tone(16_000, 1) * 32767).astype("<i2").tobytes()
        for _ in range(20):
            await ws.send_bytes(frame)
        await asyncio.sleep(0.1)
        self.assertEqual(self.sent, [])
        await ws.send_json({"type": "release"})
        async with asyncio.timeout(7):
            while (packet := await ws.receive()).type == WSMsgType.TEXT:
                self.assertNotEqual(packet.json()["type"], "ended", packet.data)
        self.assertEqual(packet.type, WSMsgType.BINARY)
        self.assertEqual(len(packet.data), 960)
        self.assertTrue(any(packet.data))
        self.assertEqual(self.sent, ["Fixture words"])
        await ws.close()
        await self.join_call(*self.calls[0])
        self.assertIsNone(self.state.pending_delivery())

    async def test_laptop_can_end_the_phone_session(self):
        socket = await self.connect("ptt")
        await self.until(socket, "ready")
        phone, thread = self.calls[0]
        phone.call_stop.set()
        ended = await self.until(socket, "ended")
        self.assertEqual(ended["text"], "Fixture ended.")
        self.assertIn((await socket.receive(timeout=3)).type, (WSMsgType.CLOSE, WSMsgType.CLOSED))
        await self.join_call(phone, thread)

    async def test_paired_receiver_gets_reply_pcm_and_stops_call_on_disconnect(self):
        ident = str(uuid4())
        url = self.server.make_url("/session")
        request = {"type": "receiver", "id": ident, "name": "Laptop"}
        async with self.client.ws_connect(url, origin=ORIGIN) as denied:
            await denied.send_json({**request, "code": "invalid"})
            self.assertEqual((await denied.receive_json(timeout=3))["type"], "ended")
        self.assertEqual(self.gateway.receivers(), ())

        async with self.client.ws_connect(url, origin=ORIGIN,
                                          headers={"X-Forwarded-For": "192.0.2.8, 100.64.1.9"}) as receiver:
            await receiver.send_json({**request, "code": self.gateway.pairing.pin})
            await self.until(receiver, "ready")
            self.receiver = self.gateway.receiver(ident)
            self.assertEqual(self.receiver.address, "100.64.1.9")
            phone = await self.connect("ptt")
            await self.until(phone, "ready")
            await phone.send_json({"type": "press"})
            frame = (tone(16_000, 1) * 32767).astype("<i2").tobytes()
            for _ in range(20):
                await phone.send_bytes(frame)
            await phone.send_json({"type": "release"})
            pcm = await receiver.receive(timeout=7)
            self.assertEqual(pcm.type, WSMsgType.BINARY)
            self.assertEqual(len(pcm.data), 960)
            self.assertTrue(any(pcm.data))
            self.assertEqual(self.sent, ["Fixture words"])
            self.assertTrue(self.calls[0][0].output.empty())
            await receiver.close()
        await self.until(phone, "ended")
        self.assertTrue(self.receiver.closed.is_set())
        self.assertEqual(self.gateway.receivers(), ())

    async def test_webrtc_sends_microphone_and_receives_synthesized_audio(self):
        client = RTCPeerConnection(RTCConfiguration(iceServers=[]))
        self.addAsyncCleanup(client.close)
        microphone = PhoneAudio("rtc")
        incoming = asyncio.Queue()
        client.on("track", incoming.put_nowait)
        client.addTrack(ReplyTrack(microphone))
        await client.setLocalDescription(await client.createOffer())
        local, = SessionDescription.parse(client.localDescription.sdp).media
        self.gateway.ice_address = next(candidate.ip for candidate in local.ice_candidates if local_ipv4(candidate.ip))
        ws = await self.connect("rtc", offer=client.localDescription.sdp)
        answer = await self.until(ws, "answer")
        remote, = SessionDescription.parse(answer["sdp"]).media
        self.assertEqual({candidate.ip for candidate in remote.ice_candidates}, {self.gateway.ice_address})
        await client.setRemoteDescription(RTCSessionDescription(answer["sdp"], "answer"))
        await self.until(ws, "ready")
        reply = await asyncio.wait_for(incoming.get(), timeout=4)
        samples = (tone(OUTPUT_RATE, 1) * 32767).astype("<i2").tobytes()
        for _ in range(24):
            microphone.output.put_nowait(samples)
            await asyncio.sleep(0.018)
        async with asyncio.timeout(7):
            while frame := await reply.recv():
                if np.max(np.abs(frame.to_ndarray().astype(np.int32))) > 100:
                    break
        self.assertEqual(self.sent, ["Fixture words"])
        await ws.close()
        await self.join_call(*self.calls[0])


def tone(rate, packets):
    return (0.35 * np.sin(2 * np.pi * 450 * np.arange(rate * packets // 50) / rate)).astype("<f4")
