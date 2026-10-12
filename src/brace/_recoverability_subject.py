import uuid
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RecoverabilitySubjectId:
    value: uuid.UUID

    def __post_init__(self) -> None:
        if not isinstance(self.value, uuid.UUID):
            raise TypeError("RecoverabilitySubjectId value must be a uuid.UUID")
        if type(self.value) is not uuid.UUID:
            raise TypeError("RecoverabilitySubjectId value must be exactly a uuid.UUID")


@dataclass(frozen=True, slots=True)
class RecoverabilitySubject:
    subject_id: RecoverabilitySubjectId

    def __post_init__(self) -> None:
        if not isinstance(self.subject_id, RecoverabilitySubjectId):
            raise TypeError("subject_id must be a RecoverabilitySubjectId")
