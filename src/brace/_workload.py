import uuid
from dataclasses import dataclass


@dataclass(frozen=True)
class ProtectedWorkload:
    workload_id: uuid.UUID
    name: str

    def __post_init__(self) -> None:
        if not isinstance(self.workload_id, uuid.UUID):
            raise TypeError("workload_id must be a uuid.UUID")
        if not self.name.strip():
            raise ValueError("name must not be empty")
