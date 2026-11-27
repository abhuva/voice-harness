"""Versioned local artifacts and scoped references to external inputs."""

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
from uuid import uuid4

from .contracts import VoiceError
from .work_types import ArtifactRef, Lineage


@dataclass(frozen=True)
class Artifact:
    ref: ArtifactRef
    media_type: str
    source: str
    revision: str
    producer: Lineage | None


class Catalog:
    def __init__(self, state):
        self.state = state
        self.root = state.path.parent / "artifacts"

    def reference(self, *, media_type, source, revision, previous=None):
        """Register an external version without copying the repository or vault."""
        return self._register(media_type, source, revision, previous=previous)

    def save(self, data, *, media_type, source, revision, producer=None, previous=None):
        if not isinstance(data, bytes):
            raise TypeError("Artifact data must be bytes.")
        if len(data) > 16 * 1024 * 1024:
            raise VoiceError("Artifact exceeds the local snapshot limit.")
        return self._register(media_type, source, revision, producer, previous, data)

    def _register(self, media_type, source, revision, producer=None, previous=None, data=None):
        if not revision:
            raise ValueError("A source revision is required.")
        if previous is not None:
            self.describe(previous)
        ref = ArtifactRef(previous.id if previous else uuid4().hex, uuid4().hex)
        name = f"{ref.version}.data" if data is not None else None
        path = self.root / name if name else None
        digest = sha256(data).hexdigest() if data is not None else None
        try:
            if path is not None:
                self.root.mkdir(parents=True, exist_ok=True)
                with path.open("xb") as output:
                    output.write(data)
            with self.state.connect() as db:
                db.execute("""
                    INSERT INTO artifact(id, version, file, digest, media_type, source, revision, producer)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (ref.id, ref.version, name, digest, media_type, source, revision,
                      json.dumps(asdict(producer)) if producer else None))
        except BaseException:
            if path is not None:
                path.unlink(missing_ok=True)
            raise
        return ref

    def _lookup(self, ref):
        with self.state.connect() as db:
            row = db.execute("""
                SELECT file, digest, media_type, source, revision, producer FROM artifact
                WHERE id = ? AND version = ?
            """, (ref.id, ref.version)).fetchone()
        if row is None:
            raise VoiceError("The requested artifact version is unavailable.")
        name, digest, media_type, source, revision, producer = row
        return name, digest, Artifact(ref, media_type, source, revision,
                                      Lineage(**json.loads(producer)) if producer else None)

    def describe(self, ref):
        return self._lookup(ref)[2]

    def access(self, work):
        return ArtifactAccess(self, work)


class ArtifactAccess:
    """A worker receives a view bound to its claimed input versions."""

    def __init__(self, catalog, work):
        self._catalog = catalog
        self._work = work

    def _require_claim(self):
        with self._catalog.state.connect() as db:
            status = db.execute("SELECT status FROM work WHERE id = ?",
                                (self._work.scope.job_id,)).fetchone()
        if status != ("running",):
            raise VoiceError("The artifact grant expired with its job.")

    def _lookup(self, ref):
        request = self._work.request
        if not any(grant.ref == ref and (not request.cloud or grant.allow_cloud)
                   for grant in request.grants):
            raise VoiceError("This job has no access to that artifact version.")
        self._require_claim()
        return self._catalog._lookup(ref)

    def describe(self, ref):
        return self._lookup(ref)[2]

    def read(self, ref):
        name, digest, _ = self._lookup(ref)
        if name is None:
            raise VoiceError("Use the artifact's external source and revision to resolve it.")
        path = self._catalog.root / name
        with path.open("rb") as source:
            data = source.read(16 * 1024 * 1024 + 1)
        if sha256(data).hexdigest() != digest:
            raise VoiceError("The saved artifact no longer matches its version.")
        return data

    def save_result(self, data, *, media_type="text/plain"):
        self._require_claim()
        scope = self._work.scope
        return self._catalog.save(data, media_type=media_type, source="job",
                                  revision=scope.job_id, producer=scope)
