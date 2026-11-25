"""Durable operation status and delivery claims. No transcript is written here."""

from contextlib import closing, contextmanager
import sqlite3

from .contracts import VoiceError


class State:
    def __init__(self, directory):
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / "state.sqlite3"
        with self.connect() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version == 0:
                db.executescript("""
                    BEGIN;
                    CREATE TABLE operation (
                        slot INTEGER PRIMARY KEY CHECK (slot = 1),
                        id TEXT NOT NULL,
                        label TEXT NOT NULL,
                        status TEXT NOT NULL,
                        active INTEGER NOT NULL CHECK (active IN (0, 1))
                    );
                    CREATE TABLE delivery (
                        slot INTEGER PRIMARY KEY CHECK (slot = 1),
                        turn_id TEXT NOT NULL
                    );
                    PRAGMA user_version = 1;
                    COMMIT;
                """)
            elif version != 1:
                raise VoiceError("The local voice state requires a different version of the app.")
            db.execute("""
                UPDATE operation SET active = 0, status = label || ' interrupted by shutdown.'
                WHERE active = 1
            """)

    @contextmanager
    def connect(self):
        with closing(sqlite3.connect(self.path, timeout=2)) as db, db:
            yield db

    def last_status(self):
        with self.connect() as db:
            row = db.execute("SELECT status FROM operation WHERE slot = 1").fetchone()
            return row[0] if row else "Ready"

    def begin(self, ident, label):
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO operation VALUES (1, ?, ?, ?, 1)",
                       (ident, label, label + " submitted"))

    def finish(self, ident, status):
        with self.connect() as db:
            db.execute("UPDATE operation SET status = ?, active = 0 WHERE id = ?", (status, ident))

    def pending_delivery(self):
        with self.connect() as db:
            row = db.execute("SELECT turn_id FROM delivery WHERE slot = 1").fetchone()
            return row[0] if row else None

    def claim_delivery(self, turn_id):
        try:
            with self.connect() as db:
                db.execute("INSERT INTO delivery VALUES (1, ?)", (turn_id,))
        except sqlite3.IntegrityError:
            raise VoiceError("Check the previous delivery before starting another call.") from None

    def clear_delivery(self, turn_id):
        with self.connect() as db:
            db.execute("DELETE FROM delivery WHERE turn_id = ?", (turn_id,))
