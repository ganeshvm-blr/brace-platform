import uuid

from ._evidence_kind import EvidenceKind
from ._evidence_semantic import EvidenceSemantic
from ._execution import OperationalCheckKind, OperationalCheckResult
from ._observed_content import ObservedContent
from ._provenance import Provenance
from ._recovery_event import RecoveryEvent
from ._recovery_observation import RecoveryObservation
from ._workload import ProtectedWorkload

_OBSERVATION_ID_NAMESPACE = uuid.UUID("c214c93f-c474-5b93-ab4e-da1431767be1")


def project_operational_check(
    run_id: uuid.UUID,
    workload: ProtectedWorkload,
    event: RecoveryEvent,
    result: OperationalCheckResult,
) -> RecoveryObservation:
    evidence_kind = _evidence_kind_from(result.kind)
    return RecoveryObservation(
        observation_id=uuid.uuid5(
            _OBSERVATION_ID_NAMESPACE,
            (
                f"run_id={str(run_id)}|"
                f"recovery_event_id={str(event.event_id)}|"
                f"workload_id={str(workload.workload_id)}|"
                f"evidence_kind={evidence_kind.value}"
            ),
        ),
        recovery_event_id=event.event_id,
        workload_id=workload.workload_id,
        occurrence_time=result.occurrence_time,
        content=ObservedContent(
            kind=evidence_kind,
            semantic=(
                EvidenceSemantic.AFFIRMATIVE
                if result.observed
                else EvidenceSemantic.NEGATIVE
            ),
            description=result.detail,
        ),
        provenance=Provenance(source=result.source),
    )


def _evidence_kind_from(kind: OperationalCheckKind) -> EvidenceKind:
    if kind is OperationalCheckKind.KUBERNETES_READY:
        return EvidenceKind.KUBERNETES_READINESS
    if kind is OperationalCheckKind.APPLICATION_RESPONSIVE:
        return EvidenceKind.APPLICATION_RESPONSIVENESS
    raise ValueError("unsupported operational check kind")
