import datetime
import uuid
from dataclasses import dataclass

from brace._observed_content import ObservedContent
from brace._provenance import Provenance


@dataclass(frozen=True)
class RecoveryObservation:
    observation_id: uuid.UUID
    recovery_event_id: uuid.UUID
    workload_id: uuid.UUID
    occurrence_time: datetime.datetime
    content: ObservedContent
    provenance: Provenance

    def __post_init__(self) -> None:
        if not isinstance(self.observation_id, uuid.UUID):
            raise TypeError("observation_id must be a uuid.UUID")
        if not isinstance(self.recovery_event_id, uuid.UUID):
            raise TypeError("recovery_event_id must be a uuid.UUID")
        if not isinstance(self.workload_id, uuid.UUID):
            raise TypeError("workload_id must be a uuid.UUID")
        if type(self.occurrence_time) is not datetime.datetime:
            raise TypeError("occurrence_time must be a datetime.datetime")
        if self.occurrence_time.tzinfo is None:
            raise ValueError("occurrence_time must be timezone-aware")
        if self.occurrence_time.utcoffset() is None:
            raise ValueError("occurrence_time must have an available UTC offset")
        if self.content is None:
            raise TypeError("content must not be None")
        if self.provenance is None:
            raise TypeError("provenance must not be None")
        object.__setattr__(
            self,
            "occurrence_time",
            self.occurrence_time.astimezone(datetime.timezone.utc),
        )
