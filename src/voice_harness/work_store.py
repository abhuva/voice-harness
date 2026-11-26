"""Durable queue, exclusive claims and result references over local SQLite."""

from dataclasses import asdict, dataclass, replace
import json
from uuid import uuid4

from .work_types import ArtifactRef, DataGrant, Lineage, WorkRequest


@dataclass(frozen=True)
class Work:
    scope: Lineage
    request: WorkRequest


class WorkStore:
    def __init__(self, state):
        self.state = state

    def enqueue(self, request, *, workflow_id=None, turn_id=None, foreground=False):
        ident = uuid4().hex
        scope = Lineage(workflow_id or ident, ident, turn_id)
        with self.state.connect() as db:
            db.execute("""
                INSERT INTO work(id, workflow, turn, request, retry_safe, status, foreground)
                VALUES (?, ?, ?, ?, ?, 'queued', ?)
            """, (ident, scope.workflow_id, turn_id, json.dumps(asdict(request)),
                  request.retry_safe, foreground))
        return Work(scope, request)

    def queued(self):
        with self.state.connect() as db:
            rows = db.execute("""
                SELECT workflow, id, turn, request FROM work
                WHERE status = 'queued' AND foreground = 0 ORDER BY rowid
            """).fetchall()

        def decode(workflow, ident, turn, body):
            data = json.loads(body)
            data["grants"] = tuple(DataGrant(ArtifactRef(**item["ref"]), item["allow_cloud"])
                                   for item in data["grants"])
            return Work(Lineage(workflow, ident, turn), WorkRequest(**data))

        return [decode(*row) for row in rows]

    def call_reservation(self):
        with self.state.connect() as db:
            rows = db.execute("""
                SELECT measurement.sample FROM measurement JOIN work ON work.id = measurement.job
                WHERE json_extract(work.request, '$.capability') = 'desktop.Call'
                  AND work.status IN ('completed', 'cancelled')
                ORDER BY work.rowid DESC LIMIT 5
            """).fetchall()
        largest = 0
        for (sample,) in rows:
            data = json.loads(sample)
            host, child = data["peak_host_bytes"], data["peak_child_bytes"]
            if data["startup_seconds"] is not None and host is not None and child is not None:
                largest = max(largest, host + child)
        return (5 * largest + 3) // 4

    def calibrated(self, capability, profile):
        if profile.memory is not None:
            return profile
        with self.state.connect() as db:
            rows = db.execute("""
                SELECT measurement.sample FROM measurement JOIN work ON work.id = measurement.job
                WHERE json_extract(work.request, '$.capability') = ?
                  AND json_extract(work.profile, '$.basis') = ?
                  AND work.status IN ('completed', 'cancelled')
                ORDER BY work.rowid DESC LIMIT 5
            """, (capability, profile.basis)).fetchall()
        resident, extra = [], []
        for (sample,) in rows:
            data = json.loads(sample)
            values = [data[name] for name in ("ready_host_bytes", "ready_child_bytes",
                                             "peak_host_bytes", "peak_child_bytes")]
            if None not in values:
                host, child, host_peak, child_peak = values
                resident.append(host + child)
                extra.append(max(0, host_peak + child_peak - host - child))
        if resident:
            return replace(profile, resident_bytes=(5 * max(resident) + 3) // 4,
                           extra_bytes=(5 * max(extra) + 3) // 4, advisory_memory=True)
        return profile

    def claim(self, work, worker, profile):
        with self.state.connect() as db:
            return db.execute("""
                UPDATE work SET status = 'running', owner = ?, profile = ?, attempts = attempts + 1
                WHERE id = ? AND status = 'queued'
            """, (worker, json.dumps(asdict(profile)), work.scope.job_id)).rowcount == 1

    def recent(self, limit=50):
        with self.state.connect() as db:
            return db.execute("""
                SELECT id, json_extract(request, '$.capability'), status, owner, turn
                FROM work ORDER BY status IN ('queued', 'running', 'cancelling') DESC, rowid DESC LIMIT ?
            """, (limit,)).fetchall()

    def status(self, ident):
        with self.state.connect() as db:
            row = db.execute("SELECT status FROM work WHERE id = ?", (ident,)).fetchone()
            return row[0] if row else None

    def cancel(self, ident):
        with self.state.connect() as db:
            return db.execute("""
                UPDATE work SET status = CASE status WHEN 'queued' THEN 'cancelled' ELSE 'cancelling' END
                WHERE id = ? AND status IN ('queued', 'running')
            """, (ident,)).rowcount == 1

    def finish(self, work, owner, status, result=None):
        with self.state.connect() as db:
            row = db.execute("""
                UPDATE work SET status = CASE status WHEN 'cancelling' THEN 'cancelled' ELSE ? END,
                    result_id = CASE status WHEN 'running' THEN ? END,
                    result_version = CASE status WHEN 'running' THEN ? END
                WHERE id = ? AND owner = ? AND status IN ('running', 'cancelling')
                RETURNING status
            """, (status, result.id if result else None, result.version if result else None,
                  work.scope.job_id, owner)).fetchone()
            return row[0] if row else None

    def context(self, workflow_id, current_turn):
        with self.state.connect() as db:
            rows = db.execute("""
                SELECT result_id, result_version FROM work
                WHERE workflow = ? AND turn IS NOT NULL AND turn != ?
                  AND status = 'completed' AND result_id IS NOT NULL
                  AND consumed = 0 AND json_extract(request, '$.for_next_turn') = 1
                ORDER BY rowid LIMIT 8
            """, (workflow_id, current_turn)).fetchall()
        return tuple(ArtifactRef(*row) for row in rows)

    def consume_context(self, workflow_id, refs):
        if refs:
            with self.state.connect() as db:
                db.executemany("""
                    UPDATE work SET consumed = 1 WHERE workflow = ? AND result_id = ? AND result_version = ?
                """, [(workflow_id, ref.id, ref.version) for ref in refs])
