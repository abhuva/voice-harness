"""Identifiers and resource declarations shared by jobs and artifacts."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Lineage:
    workflow_id: str
    job_id: str
    turn_id: str | None = None


@dataclass(frozen=True)
class ArtifactRef:
    id: str
    version: str


@dataclass(frozen=True)
class DataGrant:
    ref: ArtifactRef
    allow_cloud: bool = False


@dataclass(frozen=True)
class ResourceProfile:
    resident_bytes: int | None = None
    extra_bytes: int | None = None
    cpu_threads: int | None = None
    accelerator_bytes: int | None = 0
    basis: str = "Unmeasured"
    remote_requests: int | None = None
    remote_cost_microunits: int | None = None
    advisory_memory: bool = False

    def __post_init__(self):
        for value in (self.resident_bytes, self.extra_bytes, self.cpu_threads, self.accelerator_bytes,
                      self.remote_requests, self.remote_cost_microunits):
            if value is not None and (type(value) is not int or value < 0):
                raise ValueError("Resource estimates must be non-negative integers or unknown.")

    @property
    def memory(self):
        if self.resident_bytes is None or self.extra_bytes is None:
            return None
        return self.resident_bytes + self.extra_bytes


@dataclass(frozen=True)
class WorkRequest:
    capability: str
    grants: tuple[DataGrant, ...] = ()
    cloud: bool = False
    retry_safe: bool = False
    for_next_turn: bool = False
    session_only: bool = False
    yield_to_interactive: bool = False

    def __post_init__(self):
        if self.session_only and self.retry_safe:
            raise ValueError("Session-only input cannot be retried after shutdown.")
        if self.cloud and (self.retry_safe or self.yield_to_interactive):
            raise ValueError("Cloud work needs explicit recovery.")
        if self.cloud and any(not grant.allow_cloud for grant in self.grants):
            raise ValueError("Cloud access requires an explicit grant for every input.")
