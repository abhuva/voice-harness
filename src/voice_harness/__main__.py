"""Compose the Windows desktop application."""

import argparse
from ipaddress import IPv4Address
from pathlib import Path
from sqlite3 import Error as StateError
import sys
from urllib.parse import urlsplit

from PyQt6.QtCore import QLockFile
from PyQt6.QtWidgets import QApplication, QMessageBox

from .contracts import VoiceError
from .harness import Harness
from .nodes import NodesPanel
from .presets import TTS_MODELS
from .recognizer import STT_MODELS
from .settings import Settings
from .state import State
from .window import Window


def main():
    parser = argparse.ArgumentParser(description="Start the Voice Harness desktop window.")
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="Directory for .env and local state")
    parser.add_argument("--assets", type=Path, help="Directory containing voice/ and pocket-tts/")
    parser.add_argument("--phone", action="store_true", help="Enable the private phone server")
    parser.add_argument("--phone-port", type=int, default=8768, help="Phone server loopback port")
    parser.add_argument("--phone-origin", help="Private HTTPS origin exposed by Tailscale Serve")
    parser.add_argument("--phone-ice-address", type=IPv4Address, help="IPv4 address offered for WebRTC")
    args = parser.parse_args()
    if not 1 <= args.phone_port <= 65535:
        parser.error("--phone-port must be between 1 and 65535")
    origin = f"http://127.0.0.1:{args.phone_port}"
    if args.phone_origin:
        try:
            url = urlsplit(args.phone_origin)
            if (url.scheme != "https" or not url.hostname or url.username is not None
                    or url.path not in ("", "/") or url.query or url.fragment):
                raise ValueError
            port = url.port
            host = f"[{url.hostname}]" if ":" in url.hostname else url.hostname
            origin = f"https://{host}" + (f":{port}" if port not in (None, 443) else "")
        except ValueError:
            parser.error("--phone-origin must be an HTTPS origin, such as https://laptop.example.ts.net")
    root = args.root.resolve()
    settings = Settings(root, (args.assets or root / "var").resolve())
    app = QApplication([sys.argv[0]])

    phone = None
    nodes = None
    try:
        settings.local.mkdir(parents=True, exist_ok=True)
        settings.history_guard.parent.mkdir(parents=True, exist_ok=True)
        with_locks = [QLockFile(str(settings.local / "app.lock")),
                      QLockFile(str(settings.history_guard.with_name("listener.lock")))]
        for lock in with_locks:
            lock.setStaleLockTime(0)
            if not lock.tryLock():
                raise VoiceError("A voice window is already using this state or speech bundle.")
        harness = Harness(State(settings.local))
        window = Window(settings, harness)
        if args.phone:
            try:
                from .phone_server import PhoneServer
            except ImportError:
                raise VoiceError("Install the optional phone runtime with scripts/setup-phone.ps1.") from None
            address = str(args.phone_ice_address) if args.phone_ice_address else None
            phone = PhoneServer(args.phone_port, origin, address, window.phone_requested.emit,
                                window.phone_disconnected.emit)
            phone.start()
            window.enable_phone(origin, phone.gateway.pairing, phone.gateway)
        phone_tab = window.tabs.currentIndex()
        if phone is not None:
            phone_tab = window.tabs.count() - 1
        nodes = NodesPanel(
            phone.gateway if phone else None,
            lambda: (f"Call processing, local playback · {STT_MODELS[window.saved_call_input.model]}"
                     f" · {TTS_MODELS[window.voice.model]}"),
            lambda: window.tabs.setCurrentIndex(phone_tab),
            office_node=window.decisions.office_node,
        )
        window.tabs.addTab(nodes, "Nodes")
    except (OSError, StateError, VoiceError) as exc:
        error = str(exc) if isinstance(exc, VoiceError) else "Could not open the local voice state or bundle."
        QMessageBox.critical(None, "Voice Harness", error)
        return 1

    try:
        window.show()
        window.start_history()
        return app.exec()
    finally:
        if nodes is not None:
            nodes.close_polling()
        if phone is not None:
            phone.close()
        harness.models.evict_idle()


if __name__ == "__main__":
    sys.exit(main())
