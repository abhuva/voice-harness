"""Private HTTPS-proxied phone controls and push-to-talk audio."""

import asyncio
from concurrent.futures import Future
import json
from pathlib import Path
from queue import Empty
from threading import Thread

from aiohttp import WSMsgType, web

from .contracts import VoiceError
from .phone_audio import PhoneAudio
from .phone_pairing import PhonePairing
from .phone_rtc import PhonePeer


ASSETS = Path(__file__).with_name("phone_web")
FILES = {"/": ("index.html", "text/html"), "/phone.css": ("phone.css", "text/css"),
         "/phone.js": ("phone.js", "text/javascript"), "/capture.js": ("capture.js", "text/javascript")}
MAX_SOCKETS = 8


def action(message):
    if message.type == WSMsgType.TEXT:
        try:
            payload = json.loads(message.data)
            if isinstance(payload, dict):
                return payload
        except (ValueError, RecursionError):
            pass
    return {}


class PhoneGateway:
    def __init__(self, origin, ice_address, start_call, stop_call):
        self.origin = origin
        self.ice_address = ice_address
        self.start_call, self.stop_call = start_call, stop_call
        self.pairing = PhonePairing()
        self.active = None
        self.sockets = set()

    def app(self):
        app = web.Application(client_max_size=64 * 1024)
        for path in FILES:
            app.router.add_get(path, self.asset)
        app.router.add_get("/session", self.handle)
        app.on_shutdown.append(self.shutdown)
        return app

    async def asset(self, request):
        asset, content_type = FILES[request.path]
        socket_origin = self.origin.replace("http", "ws", 1)
        return web.Response(body=(ASSETS / asset).read_bytes(), content_type=content_type, headers={
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer",
            "Content-Security-Policy": ("default-src 'none'; script-src 'self'; style-src 'self'; "
                                        f"worker-src 'self'; media-src 'self' blob:; connect-src 'self' {socket_origin}; "
                                        "base-uri 'none'; frame-ancestors 'none'; form-action 'none'"),
        })

    async def shutdown(self, _app):
        await asyncio.gather(*(socket.close(code=1001) for socket in tuple(self.sockets)))

    async def handle(self, request):
        if request.headers.get("Origin") != self.origin:
            raise web.HTTPForbidden()
        if len(self.sockets) >= MAX_SOCKETS:
            raise web.HTTPServiceUnavailable()
        socket = web.WebSocketResponse(heartbeat=12, max_msg_size=64 * 1024, compress=False)
        self.sockets.add(socket)
        audio = peer = updates = None
        started = False
        try:
            await socket.prepare(request)
            connect = action(await socket.receive(timeout=8))
            if connect.get("type") != "call":
                return socket
            self.pairing.verify(connect.get("code"))
            mode = connect.get("mode")
            if mode not in ("ptt", "rtc"):
                raise VoiceError("Choose push-to-talk or WebRTC.")
            if self.active is not None:
                raise VoiceError("Another phone call is already active.")
            self.active = audio = PhoneAudio(mode)
            if mode == "rtc":
                offer = connect.get("offer")
                if not isinstance(offer, str):
                    raise VoiceError("The phone did not send a WebRTC audio offer.")
                peer = PhonePeer(audio, self.ice_address)
                answer = await asyncio.wait_for(peer.answer(offer), timeout=8)
                await socket.send_json({"type": "answer", "sdp": answer})
                await asyncio.wait_for(peer.connected.wait(), timeout=12)
                if audio.closed.is_set() or socket.closed:
                    raise VoiceError("The WebRTC audio connection closed.")
            updates = asyncio.create_task(self.send_updates(socket, audio))
            self.start_call(audio)
            started = True
            async for message in socket:
                if mode == "ptt" and message.type == WSMsgType.BINARY:
                    if not (0 < len(message.data) <= 640 and len(message.data) % 2 == 0):
                        raise VoiceError("The phone sent an invalid audio frame.")
                    if not audio.feed(message.data):
                        break
                    continue
                command = action(message).get("type")
                if command == "hangup":
                    break
                if mode == "ptt" and command in ("press", "release", "cancel"):
                    if not audio.feed(command):
                        break
        except asyncio.TimeoutError:
            if not socket.closed:
                text = ("WebRTC audio timed out. Check the laptop’s Tailscale address and UDP connectivity."
                        if peer is not None else "Phone connection timed out.")
                await socket.send_json({"type": "ended", "text": text})
        except VoiceError as exc:
            if not socket.closed:
                await socket.send_json({"type": "ended", "text": str(exc)})
        except (ConnectionError, asyncio.CancelledError):
            pass
        finally:
            if audio is not None:
                audio.disconnect()
            if started and not audio.finished.is_set():
                self.stop_call(audio)
            if updates is not None:
                updates.cancel()
                await asyncio.gather(updates, return_exceptions=True)
            if peer is not None:
                await peer.close()
            if self.active is audio:
                self.active = None
            self.sockets.discard(socket)
            if socket.prepared:
                await socket.close()
        return socket

    async def send_updates(self, socket, audio):
        clock = asyncio.get_running_loop().time
        next_audio = clock()
        try:
            while not socket.closed:
                while audio.events:
                    event = audio.events.popleft()
                    await socket.send_json(event)
                    if event["type"] == "ended":
                        await socket.close()
                        return
                if audio.closed.is_set():
                    await socket.close()
                    return
                if audio.mode == "ptt" and clock() >= next_audio:
                    try:
                        frame = audio.output.get_nowait()
                    except Empty:
                        pass
                    else:
                        await socket.send_bytes(frame)
                        next_audio = clock() + 0.02
                await asyncio.sleep(0.005)
        except ConnectionError:
            await socket.close()


class PhoneServer:
    def __init__(self, port, origin, ice_address, start_call, stop_call):
        self.gateway = PhoneGateway(origin, ice_address, start_call, stop_call)
        self.port = port
        self.ready = Future()
        self.thread = Thread(target=self._run, name="phone-server", daemon=True)

    def start(self):
        self.thread.start()
        try:
            self.ready.result(timeout=5)
        except (OSError, TimeoutError):
            raise VoiceError("Could not start the local phone server. Check the port and restart.") from None

    def _run(self):
        try:
            asyncio.run(self._serve())
        except Exception as exc:
            if not self.ready.done():
                self.ready.set_exception(exc)

    async def _serve(self):
        self.loop = asyncio.get_running_loop()
        self.stopped = asyncio.Event()
        runner = web.AppRunner(self.gateway.app(), access_log=None, shutdown_timeout=2)
        await runner.setup()
        try:
            await web.TCPSite(runner, "127.0.0.1", self.port).start()
            self.ready.set_result(None)
            await self.stopped.wait()
        finally:
            await runner.cleanup()

    def close(self):
        if self.ready.done() and self.ready.exception() is None and self.thread.is_alive():
            self.loop.call_soon_threadsafe(self.stopped.set)
            self.thread.join(timeout=4)
