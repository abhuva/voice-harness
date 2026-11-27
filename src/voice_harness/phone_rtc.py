"""An audio-only WebRTC peer with browser-initiated ICE checks."""

import asyncio
from fractions import Fraction
from ipaddress import ip_address
from queue import Empty
from time import monotonic

from aiortc import AudioStreamTrack, RTCConfiguration, RTCPeerConnection, RTCSessionDescription
from aiortc.mediastreams import MediaStreamError
from aiortc.sdp import SessionDescription
from av import AudioFrame, AudioResampler

from .contracts import VoiceError
from .echo import FRAME_SAMPLES
from .phone_audio import INPUT_RATE, OUTPUT_BYTES, OUTPUT_RATE, OUTPUT_SAMPLES


def local_ipv4(address):
    ip = ip_address(address)
    return ip.version == 4 and ip.is_private and not ip.is_link_local


class ReplyTrack(AudioStreamTrack):
    def __init__(self, audio):
        super().__init__()
        self.audio = audio
        self.pts = 0
        self.next_at = None

    async def recv(self):
        clock = asyncio.get_running_loop().time
        now = clock()
        self.next_at = now if self.next_at is None else max(now, self.next_at + 0.02)
        await asyncio.sleep(max(0, self.next_at - clock()))
        if self.audio.closed.is_set() or self.readyState != "live":
            raise MediaStreamError
        try:
            samples = self.audio.output.get_nowait()
        except Empty:
            samples = bytes(OUTPUT_BYTES)
        frame = AudioFrame(format="s16", layout="mono", samples=OUTPUT_SAMPLES)
        frame.planes[0].update(samples)
        frame.sample_rate = OUTPUT_RATE
        frame.time_base = Fraction(1, OUTPUT_RATE)
        frame.pts = self.pts
        self.pts += OUTPUT_SAMPLES
        return frame


async def consume_mic(track, audio):
    resample = AudioResampler(format="s16", layout="mono", rate=INPUT_RATE, frame_size=FRAME_SAMPLES)
    previous_end = None
    missing = 0
    try:
        while not audio.closed.is_set():
            frame = await track.recv()
            if frame.time is not None:
                if previous_end is not None:
                    missing += max(0, round((frame.time - previous_end) * 100))
                previous_end = frame.time + frame.samples / frame.sample_rate
            frame.pts = None
            for block in resample.resample(frame):
                samples = bytes(block.planes[0])[:2 * FRAME_SAMPLES]
                if not audio.feed((samples, monotonic(), missing)):
                    return
                missing = 0
    except (MediaStreamError, ValueError, RuntimeError):
        if not audio.closed.is_set():
            audio.say("Phone microphone stream ended.")
            audio.disconnect()


class PhonePeer:
    def __init__(self, audio, address):
        self.audio = audio
        self.address = address
        self.peer = RTCPeerConnection(RTCConfiguration(iceServers=[]))
        self.connected = asyncio.Event()
        self.reader = None
        self.peer.on("track", self._track)
        self.peer.on("connectionstatechange", self._state)

    def _track(self, track):
        self.reader = asyncio.create_task(consume_mic(track, self.audio))

    def _state(self):
        if self.peer.connectionState == "connected":
            self.connected.set()
        elif self.peer.connectionState in ("failed", "closed"):
            self.connected.set()
            self.audio.disconnect()

    async def answer(self, offer):
        try:
            # Only the phone initiates checks. Remote SDP cannot select addresses for the host to contact.
            offer = "\r\n".join(line for line in offer.splitlines()
                                if not line.startswith(("a=candidate:", "a=end-of-candidates"))) + "\r\n"
            description = SessionDescription.parse(offer)
            media, = description.media
            if media.kind != "audio" or media.direction != "sendrecv":
                raise ValueError
            await self.peer.setRemoteDescription(RTCSessionDescription(offer, "offer"))
            self.peer.addTrack(ReplyTrack(self.audio))
            await self.peer.setLocalDescription(await self.peer.createAnswer())
            answer = SessionDescription.parse(self.peer.localDescription.sdp)
        except (ValueError, TypeError, AttributeError, AssertionError, IndexError):
            raise VoiceError("The phone must offer one two-way audio track.") from None
        media, = answer.media
        media.ice_candidates = [candidate for candidate in media.ice_candidates
                                if (candidate.ip == self.address if self.address else local_ipv4(candidate.ip))]
        if not media.ice_candidates:
            raise VoiceError("No WebRTC address is available. Check the laptop’s phone network settings.")
        candidate = media.ice_candidates[0]
        media.host, media.port = candidate.ip, candidate.port
        return str(answer)

    async def close(self):
        if self.reader is not None:
            self.reader.cancel()
            await asyncio.gather(self.reader, return_exceptions=True)
        await self.peer.close()
