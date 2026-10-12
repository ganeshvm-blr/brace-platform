import uuid
from dataclasses import dataclass


@dataclass(frozen=True)
class RecoveryEvent:
    event_id: uuid.UUID
    workload_id: uuid.UUID

    def __post_init__(self) -> None:
        if not isinstance(self.event_id, uuid.UUID):
            raise TypeError("event_id must be a uuid.UUID")
        if not isinstance(self.workload_id, uuid.UUID):
            raise TypeError("workload_id must be a uuid.UUID")
