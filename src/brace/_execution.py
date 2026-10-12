import datetime
from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol
from uuid import UUID


@dataclass(frozen=True)
class WorkloadBundleIdentity:
    bundle_name: str
    bundle_version: str

    def __post_init__(self) -> None:
        if not isinstance(self.bundle_name, str):
            raise TypeError("bundle_name must be a string")
        if not isinstance(self.bundle_version, str):
            raise TypeError("bundle_version must be a string")
        if not self.bundle_name.strip():
            raise ValueError("bundle_name must not be empty or blank")
        if not self.bundle_version.strip():
            raise ValueError("bundle_version must not be empty or blank")


@dataclass(frozen=True)
class RunNamespaceIdentity:
    run_id: UUID
    namespace: str = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.run_id, UUID):
            raise TypeError("run_id must be a UUID")
        object.__setattr__(self, "namespace", f"brace-run-{self.run_id.hex}")


class OperationalCheckKind(str, Enum):
    WORKLOAD_RECREATED = "workload_recreated"
    KUBERNETES_READY = "kubernetes_ready"
    APPLICATION_RESPONSIVE = "application_responsive"


@dataclass(frozen=True)
class OperationalCheckResult:
    kind: OperationalCheckKind
    observed: bool
    detail: str
    source: str
    occurrence_time: datetime.datetime

    def __post_init__(self) -> None:
        if not isinstance(self.kind, OperationalCheckKind):
            raise TypeError("kind must be an OperationalCheckKind")
        if not isinstance(self.observed, bool):
            raise TypeError("observed must be a bool")
        if not isinstance(self.detail, str):
            raise TypeError("detail must be a string")
        if not isinstance(self.source, str):
            raise TypeError("source must be a string")
        if type(self.occurrence_time) is not datetime.datetime:
            raise TypeError("occurrence_time must be a datetime.datetime")
        if self.occurrence_time.tzinfo is None:
            raise ValueError("occurrence_time must be timezone-aware")
        if self.occurrence_time.utcoffset() is None:
            raise ValueError("occurrence_time must have an available UTC offset")
        if not self.detail.strip():
            raise ValueError("detail must not be empty or blank")
        if not self.source.strip():
            raise ValueError("source must not be empty or blank")
        object.__setattr__(
            self,
            "occurrence_time",
            self.occurrence_time.astimezone(datetime.timezone.utc),
        )


class _ObservationClock(Protocol):
    def now(self) -> datetime.datetime: ...
