"""Durable operation status and delivery claims. No transcript is written here."""

from contextlib import closing, contextmanager
import sqlite3

from .contracts import VoiceError


class State:
    def __init__(self, directory):
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / "state.sqlite3"
        with self.connect() as db:
            db.execute("PRAGMA journal_mode = WAL")
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
                version = 1
            if version == 1:
                db.executescript("""
                    BEGIN;
                    CREATE TABLE work (
                        id TEXT PRIMARY KEY,
                        workflow TEXT NOT NULL,
                        turn TEXT,
                        request TEXT NOT NULL,
                        retry_safe INTEGER NOT NULL,
                        status TEXT NOT NULL,
                        foreground INTEGER NOT NULL,
                        owner TEXT,
                        attempts INTEGER NOT NULL DEFAULT 0,
                        profile TEXT,
                        result_id TEXT,
                        result_version TEXT,
                        consumed INTEGER NOT NULL DEFAULT 0
                    );
                    CREATE INDEX work_queue ON work(status, foreground);
                    CREATE TABLE artifact (
                        id TEXT NOT NULL,
                        version TEXT NOT NULL,
                        file TEXT,
                        digest TEXT,
                        media_type TEXT NOT NULL,
                        source TEXT NOT NULL,
                        revision TEXT NOT NULL,
                        producer TEXT,
                        PRIMARY KEY (id, version)
                    );
                    CREATE TABLE measurement (
                        job TEXT PRIMARY KEY,
                        sample TEXT NOT NULL
                    );
                    PRAGMA user_version = 2;
                    COMMIT;
                """)
                version = 2
            if version == 2:
                db.executescript("""
                    BEGIN;
                    CREATE TABLE decision_score (
                        tick INTEGER PRIMARY KEY,
                        job TEXT NOT NULL UNIQUE,
                        model TEXT NOT NULL,
                        observed REAL NOT NULL,
                        questions TEXT NOT NULL,
                        scores TEXT NOT NULL
                    );
                    PRAGMA user_version = 3;
                    COMMIT;
                """)
                version = 3
            if version != 3:
                raise VoiceError("The local voice state requires a different version of the app.")
            db.execute("""
                UPDATE work SET status = CASE
                    WHEN status = 'running' AND retry_safe = 1 AND foreground = 0 THEN 'queued'
                    ELSE 'interrupted' END, owner = NULL
                WHERE status IN ('running', 'cancelling') OR (status = 'queued'
                    AND (foreground = 1 OR json_extract(request, '$.session_only') = 1))
            """)
            db.execute("""
                DELETE FROM decision_score WHERE job NOT IN (SELECT id FROM work WHERE status = 'completed')
                OR tick IN (SELECT tick FROM decision_score JOIN work ON work.id = decision_score.job
                    WHERE work.status = 'completed' ORDER BY tick DESC LIMIT -1 OFFSET 100)
            """)
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
