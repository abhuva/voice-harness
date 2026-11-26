"""Compose the Windows desktop application."""

import argparse
from pathlib import Path
from sqlite3 import Error as StateError
import sys

from PyQt6.QtCore import QLockFile
from PyQt6.QtWidgets import QApplication, QMessageBox

from .contracts import VoiceError
from .harness import Harness
from .settings import Settings
from .state import State
from .window import Window


def main():
    parser = argparse.ArgumentParser(description="Start the Voice Harness desktop window.")
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="Directory for .env and local state")
    parser.add_argument("--assets", type=Path, help="Directory containing voice/ and pocket-tts/")
    args = parser.parse_args()
    root = args.root.resolve()
    settings = Settings(root, (args.assets or root / "var").resolve())
    app = QApplication([sys.argv[0]])

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
    except (OSError, StateError, VoiceError) as exc:
        error = str(exc) if isinstance(exc, VoiceError) else "Could not open the local voice state or bundle."
        QMessageBox.critical(None, "Voice Harness", error)
        return 1

    try:
        window.show()
        window.start_history()
        return app.exec()
    finally:
        harness.models.evict_idle()


if __name__ == "__main__":
    sys.exit(main())
