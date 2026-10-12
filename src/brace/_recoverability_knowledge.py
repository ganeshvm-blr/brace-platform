"""Public recoverability result shape for the Brace platform.

``RecoverabilityKnowledge`` is the immutable result the reasoning core returns
across the reasoning boundary (see ``brace.reasoning.boundary``). It is a
runtime-derived, evidence-backed representation of whether recoverability has
been established for a subject workload.

This public definition intentionally exposes only the result *shape*. How the
standing is derived - the proprietary evidence-to-knowledge reasoning - is not
part of this public platform repository.
"""

from __future__ import annotations

import datetime as _dt
import uuid
from dataclasses import dataclass

from brace._recoverability_subject import RecoverabilitySubject
from brace._recoverability_truth_standing_value import RecoverabilityTruthStandingValue


@dataclass(frozen=True, slots=True)
class RecoverabilityKnowledge:
    """An immutable, evidence-backed recoverability result for one workload.

    Attributes:
        knowledge_id: unique identity of this recoverability result.
        subject: the workload the result concerns.
        standing: the established recoverability standing.
        established_at: when the result was produced (timezone-aware, UTC).
    """

    knowledge_id: uuid.UUID
    subject: RecoverabilitySubject
    standing: RecoverabilityTruthStandingValue
    established_at: _dt.datetime

    def __post_init__(self) -> None:
        if not isinstance(self.knowledge_id, uuid.UUID):
            raise TypeError("knowledge_id must be a UUID")
        if not isinstance(self.subject, RecoverabilitySubject):
            raise TypeError("subject must be a RecoverabilitySubject")
        if not isinstance(self.standing, RecoverabilityTruthStandingValue):
            raise TypeError("standing must be a RecoverabilityTruthStandingValue")
        if not isinstance(self.established_at, _dt.datetime):
            raise TypeError("established_at must be a datetime")
        if self.established_at.tzinfo is None:
            raise ValueError("established_at must be timezone-aware")
        if self.established_at.utcoffset() is None:
            raise ValueError("established_at must have an available UTC offset")
        object.__setattr__(
            self,
            "established_at",
            self.established_at.astimezone(_dt.timezone.utc),
        )
