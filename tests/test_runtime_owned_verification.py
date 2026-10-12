"""Focused public-surface tests for RuntimeOwnedVerificationCoordinator.

These tests exercise only the public types of the sanitized repository: the
real coordination chain, the real boundary handoff (evaluate_recoverability
plus a recording RecoverabilityEvaluator), the real project_operational_check
projection, and the real cross-field validation predicates. External
Kubernetes I/O (namespace/workload adapters, observers) and the proprietary
reasoning core are represented by test doubles, as permitted.
"""

import datetime
import uuid

import pytest

from brace._evidence_kind import EvidenceKind
from brace._evidence_semantic import EvidenceSemantic
from brace._execution import (
    OperationalCheckKind,
    OperationalCheckResult,
    RunNamespaceIdentity,
    WorkloadBundleIdentity,
)
from brace._kubernetes_namespace import NamespaceOwnership
from brace._kubernetes_workload import (
    ApprovedContainer,
    ApprovedDeployment,
    ApprovedService,
    ApprovedWorkloadBundle,
)
from brace._observed_content import ObservedContent
from brace._operational_projection import project_operational_check
from brace._provenance import Provenance
from brace._recoverability_knowledge import RecoverabilityKnowledge
from brace._recoverability_subject import (
    RecoverabilitySubject,
    RecoverabilitySubjectId,
)
from brace._recoverability_truth_standing_value import (
    RecoverabilityTruthStandingValue,
)
from brace._recovery_event import RecoveryEvent
from brace._recovery_observation import RecoveryObservation
from brace._runtime_owned_verification import (
    BoundaryEvaluationRequest,
    ExecutionPhaseResult,
    RuntimeOwnedVerificationCoordinator,
    VerificationCleanupFailed,
    VerificationCoordinationFailed,
    VerificationCoordinationResult,
    VerificationPhase,
)
from brace._workload import ProtectedWorkload
from brace.reasoning.boundary import (
    ReasoningCoreNotProvided,
    RecoverabilityEvaluator,
)

_T0 = datetime.datetime(2026, 8, 9, 12, 0, tzinfo=datetime.timezone.utc)
_T1 = datetime.datetime(2026, 8, 9, 12, 1, tzinfo=datetime.timezone.utc)
_T2 = datetime.datetime(2026, 8, 9, 12, 2, tzinfo=datetime.timezone.utc)

_SUCCESS_ORDER = [
    VerificationPhase.NAMESPACE_CREATE,
    VerificationPhase.WORKLOAD_APPLY,
    VerificationPhase.READINESS_OBSERVE,
    VerificationPhase.RESPONSIVENESS_OBSERVE,
    VerificationPhase.OBSERVATION_PROJECT,
    VerificationPhase.REASONING_BOUNDARY,
    VerificationPhase.NAMESPACE_CLEANUP,
]

_SUCCESS_ORDER_STRINGS = [
    "namespace_create",
    "workload_apply",
    "readiness_observe",
    "responsiveness_observe",
    "observation_project",
    "reasoning_boundary",
    "namespace_cleanup",
]


class _Inputs:
    def __init__(self) -> None:
        self.run_id = uuid.uuid4()
        self.evaluation_id = uuid.uuid4()
        self.workload_id = uuid.uuid4()
        self.workload = ProtectedWorkload(
            workload_id=self.workload_id, name="brace-demo"
        )
        self.event = RecoveryEvent(
            event_id=uuid.uuid4(), workload_id=self.workload_id
        )
        self.namespace = RunNamespaceIdentity(self.run_id)
        self.identity = WorkloadBundleIdentity(
            bundle_name="brace-demo", bundle_version="1.0.0"
        )
        self.deployment = ApprovedDeployment(
            name="brace-demo",
            container=ApprovedContainer(
                name="app",
                image="registry.example.com/brace-demo@sha256:" + ("a" * 64),
                container_port=8080,
            ),
            replicas=1,
        )
        self.service = ApprovedService(
            name="brace-demo", port=8080, target_port=8080
        )
        self.bundle = ApprovedWorkloadBundle(
            identity=self.identity,
            config_map=None,
            deployment=self.deployment,
            service=self.service,
        )
        self.ownership = NamespaceOwnership(
            run_id=self.run_id,
            workload_id=self.workload_id,
            bundle=self.identity,
            creation_source="brace_runtime_owned_verification",
        )


def _readiness_result() -> OperationalCheckResult:
    return OperationalCheckResult(
        kind=OperationalCheckKind.KUBERNETES_READY,
        observed=True,
        detail="Deployment brace-demo is ready",
        source="brace._kubernetes_readiness.KubernetesReadinessObserver",
        occurrence_time=_T0,
    )


def _responsiveness_result() -> OperationalCheckResult:
    return OperationalCheckResult(
        kind=OperationalCheckKind.APPLICATION_RESPONSIVE,
        observed=True,
        detail="HTTP response status 200 from http://brace-demo.svc:8080/",
        source="kubernetes-service-http",
        occurrence_time=_T1,
    )


def _knowledge(workload_id: uuid.UUID) -> RecoverabilityKnowledge:
    return RecoverabilityKnowledge(
        knowledge_id=uuid.uuid4(),
        subject=RecoverabilitySubject(
            subject_id=RecoverabilitySubjectId(value=workload_id)
        ),
        standing=RecoverabilityTruthStandingValue.RECOVERABILITY_AFFIRMED,
        established_at=_T2,
    )


def _observation(
    workload_id: uuid.UUID,
    event_id: uuid.UUID,
    kind: EvidenceKind = EvidenceKind.KUBERNETES_READINESS,
) -> RecoveryObservation:
    return RecoveryObservation(
        observation_id=uuid.uuid4(),
        recovery_event_id=event_id,
        workload_id=workload_id,
        occurrence_time=_T0,
        content=ObservedContent(
            kind=kind,
            semantic=EvidenceSemantic.AFFIRMATIVE,
            description="observed",
        ),
        provenance=Provenance(source="test"),
    )


class _RecordingNamespaceAdapter:
    def __init__(self) -> None:
        self.created: list[tuple] = []
        self.deleted: list[tuple] = []
        self.create_error: Exception | None = None
        self.delete_error: Exception | None = None

    def create(self, namespace, ownership) -> None:
        if self.create_error is not None:
            raise self.create_error
        self.created.append((namespace, ownership))

    def delete_owned(self, namespace, ownership) -> None:
        self.deleted.append((namespace, ownership))
        if self.delete_error is not None:
            raise self.delete_error


class _RecordingWorkloadAdapter:
    def __init__(self) -> None:
        self.applied: list[tuple] = []
        self.apply_error: Exception | None = None

    def apply(self, namespace, ownership, bundle) -> None:
        if self.apply_error is not None:
            raise self.apply_error
        self.applied.append((namespace, ownership, bundle))


class _RecordingReadinessObserver:
    def __init__(self, result: OperationalCheckResult) -> None:
        self.result = result
        self.observed: list[tuple] = []
        self.error: Exception | None = None

    def observe(self, namespace, ownership, deployment):
        if self.error is not None:
            raise self.error
        self.observed.append((namespace, ownership, deployment))
        return self.result


class _RecordingResponsivenessObserver:
    def __init__(self, result: OperationalCheckResult) -> None:
        self.result = result
        self.observed: list[tuple] = []
        self.error: Exception | None = None

    def observe(self, namespace, ownership, service):
        if self.error is not None:
            raise self.error
        self.observed.append((namespace, ownership, service))
        return self.result


class _RecordingEvaluator:
    def __init__(self, knowledge: RecoverabilityKnowledge) -> None:
        self.knowledge = knowledge
        self.requests: list[BoundaryEvaluationRequest] = []
        self.error: Exception | None = None

    def evaluate(self, request: BoundaryEvaluationRequest):
        if self.error is not None:
            raise self.error
        self.requests.append(request)
        return self.knowledge


class _Harness:
    def __init__(
        self,
        namespace_adapter: _RecordingNamespaceAdapter,
        workload_adapter: _RecordingWorkloadAdapter,
        readiness_observer: _RecordingReadinessObserver,
        responsiveness_observer: _RecordingResponsivenessObserver,
        evaluator: _RecordingEvaluator | None,
        coordinator: RuntimeOwnedVerificationCoordinator,
    ) -> None:
        self.namespace_adapter = namespace_adapter
        self.workload_adapter = workload_adapter
        self.readiness_observer = readiness_observer
        self.responsiveness_observer = responsiveness_observer
        self.evaluator = evaluator
        self.coordinator = coordinator


def _build(
    readiness_result: OperationalCheckResult = _readiness_result(),
    responsiveness_result: OperationalCheckResult = _responsiveness_result(),
    evaluator: _RecordingEvaluator | None = None,
    *,
    ns_create_error: Exception | None = None,
    ns_delete_error: Exception | None = None,
    apply_error: Exception | None = None,
    readiness_error: Exception | None = None,
    responsiveness_error: Exception | None = None,
) -> _Harness:
    namespace_adapter = _RecordingNamespaceAdapter()
    namespace_adapter.create_error = ns_create_error
    namespace_adapter.delete_error = ns_delete_error

    workload_adapter = _RecordingWorkloadAdapter()
    workload_adapter.apply_error = apply_error

    readiness_observer = _RecordingReadinessObserver(readiness_result)
    readiness_observer.error = readiness_error

    responsiveness_observer = _RecordingResponsivenessObserver(
        responsiveness_result
    )
    responsiveness_observer.error = responsiveness_error

    coordinator = RuntimeOwnedVerificationCoordinator(
        namespace_adapter=namespace_adapter,
        workload_adapter=workload_adapter,
        readiness_observer=readiness_observer,
        responsiveness_observer=responsiveness_observer,
        evaluator=evaluator,
    )
    return _Harness(
        namespace_adapter,
        workload_adapter,
        readiness_observer,
        responsiveness_observer,
        evaluator,
        coordinator,
    )


def _phase_pairs(phase_results) -> list[tuple[VerificationPhase, bool]]:
    return [(result.phase, result.succeeded) for result in phase_results]


# ---------------------------------------------------------------------------
# A + B: successful real public chain with exact phase ordering
# ---------------------------------------------------------------------------


def test_successful_chain_exact_phase_order_and_single_cleanup():
    inputs = _Inputs()
    knowledge = _knowledge(inputs.workload_id)
    evaluator = _RecordingEvaluator(knowledge)
    harness = _build(evaluator=evaluator)

    result = harness.coordinator.coordinate(
        inputs.run_id,
        inputs.evaluation_id,
        inputs.workload,
        inputs.event,
        inputs.namespace,
        inputs.ownership,
        inputs.bundle,
    )

    assert isinstance(result, VerificationCoordinationResult)
    assert result.run_id == inputs.run_id
    assert result.evaluation_id == inputs.evaluation_id
    assert result.knowledge is knowledge
    assert len(result.observations) == 2
    assert all(isinstance(o, RecoveryObservation) for o in result.observations)

    assert [r.phase for r in result.phase_results] == _SUCCESS_ORDER
    assert [r.phase.value for r in result.phase_results] == _SUCCESS_ORDER_STRINGS
    assert [r.succeeded for r in result.phase_results] == [True] * 7
    assert all(isinstance(r, ExecutionPhaseResult) for r in result.phase_results)

    assert harness.namespace_adapter.created == [
        (inputs.namespace, inputs.ownership)
    ]
    assert harness.namespace_adapter.deleted == [
        (inputs.namespace, inputs.ownership)
    ]
    assert len(harness.namespace_adapter.deleted) == 1
    assert harness.workload_adapter.applied == [
        (inputs.namespace, inputs.ownership, inputs.bundle)
    ]
    assert harness.readiness_observer.observed == [
        (inputs.namespace, inputs.ownership, inputs.deployment)
    ]
    assert harness.responsiveness_observer.observed == [
        (inputs.namespace, inputs.ownership, inputs.service)
    ]


# ---------------------------------------------------------------------------
# C: exact BoundaryEvaluationRequest captured by the evaluator
# ---------------------------------------------------------------------------


def test_boundary_request_carries_exact_identities_and_observations():
    inputs = _Inputs()
    readiness = _readiness_result()
    responsiveness = _responsiveness_result()
    evaluator = _RecordingEvaluator(_knowledge(inputs.workload_id))
    harness = _build(readiness, responsiveness, evaluator)

    harness.coordinator.coordinate(
        inputs.run_id,
        inputs.evaluation_id,
        inputs.workload,
        inputs.event,
        inputs.namespace,
        inputs.ownership,
        inputs.bundle,
    )

    assert isinstance(evaluator, RecoverabilityEvaluator)
    assert len(evaluator.requests) == 1
    request = evaluator.requests[0]
    assert isinstance(request, BoundaryEvaluationRequest)
    assert request.run_id is inputs.run_id
    assert request.evaluation_id is inputs.evaluation_id
    assert request.protected_workload is inputs.workload
    assert request.recovery_event is inputs.event
    assert len(request.observations) == 2
    for observation in request.observations:
        assert isinstance(observation, RecoveryObservation)
        assert observation.recovery_event_id == inputs.event.event_id
        assert observation.workload_id == inputs.workload.workload_id
    assert {o.content.kind for o in request.observations} == {
        EvidenceKind.KUBERNETES_READINESS,
        EvidenceKind.APPLICATION_RESPONSIVENESS,
    }
    readiness_observation, responsiveness_observation = request.observations
    assert readiness_observation.content.description == readiness.detail
    assert readiness_observation.content.semantic is EvidenceSemantic.AFFIRMATIVE
    assert readiness_observation.provenance.source == readiness.source
    assert readiness_observation.occurrence_time == readiness.occurrence_time
    assert (
        responsiveness_observation.content.description
        == responsiveness.detail
    )
    assert (
        responsiveness_observation.content.semantic
        is EvidenceSemantic.AFFIRMATIVE
    )
    assert (
        responsiveness_observation.provenance.source
        == responsiveness.source
    )
    assert (
        responsiveness_observation.occurrence_time
        == responsiveness.occurrence_time
    )


# ---------------------------------------------------------------------------
# D: returned result carries the exact knowledge, observations, and phase record
# ---------------------------------------------------------------------------


def test_result_carries_exact_projected_observations_and_knowledge():
    inputs = _Inputs()
    readiness = _readiness_result()
    responsiveness = _responsiveness_result()
    knowledge = _knowledge(inputs.workload_id)
    evaluator = _RecordingEvaluator(knowledge)
    harness = _build(readiness, responsiveness, evaluator)

    result = harness.coordinator.coordinate(
        inputs.run_id,
        inputs.evaluation_id,
        inputs.workload,
        inputs.event,
        inputs.namespace,
        inputs.ownership,
        inputs.bundle,
    )

    expected_observations = (
        project_operational_check(
            inputs.run_id, inputs.workload, inputs.event, readiness
        ),
        project_operational_check(
            inputs.run_id, inputs.workload, inputs.event, responsiveness
        ),
    )
    assert result.observations == expected_observations
    request = evaluator.requests[0]
    assert result.observations is request.observations
    assert all(
        a is b
        for a, b in zip(result.observations, request.observations)
    )
    assert result.knowledge is knowledge
    assert result.knowledge == knowledge
    assert _phase_pairs(result.phase_results) == [
        (VerificationPhase.NAMESPACE_CREATE, True),
        (VerificationPhase.WORKLOAD_APPLY, True),
        (VerificationPhase.READINESS_OBSERVE, True),
        (VerificationPhase.RESPONSIVENESS_OBSERVE, True),
        (VerificationPhase.OBSERVATION_PROJECT, True),
        (VerificationPhase.REASONING_BOUNDARY, True),
        (VerificationPhase.NAMESPACE_CLEANUP, True),
    ]


# ---------------------------------------------------------------------------
# E: failure and cleanup behavior
# ---------------------------------------------------------------------------


def test_namespace_create_failure_records_phase_and_cleans_up():
    inputs = _Inputs()
    error = RuntimeError("namespace create failed")
    harness = _build(ns_create_error=error)

    with pytest.raises(VerificationCoordinationFailed) as caught:
        harness.coordinator.coordinate(
            inputs.run_id,
            inputs.evaluation_id,
            inputs.workload,
            inputs.event,
            inputs.namespace,
            inputs.ownership,
            inputs.bundle,
        )

    failure = caught.value
    assert failure.failed_phase is VerificationPhase.NAMESPACE_CREATE
    assert failure.primary_failure is error
    assert failure.cleanup_failure is None
    assert _phase_pairs(failure.phase_results) == [
        (VerificationPhase.NAMESPACE_CREATE, False),
        (VerificationPhase.NAMESPACE_CLEANUP, True),
    ]
    assert len(harness.namespace_adapter.deleted) == 1


def test_workload_apply_failure_triggers_owned_cleanup():
    inputs = _Inputs()
    error = RuntimeError("workload apply failed")
    harness = _build(apply_error=error)

    with pytest.raises(VerificationCoordinationFailed) as caught:
        harness.coordinator.coordinate(
            inputs.run_id,
            inputs.evaluation_id,
            inputs.workload,
            inputs.event,
            inputs.namespace,
            inputs.ownership,
            inputs.bundle,
        )

    failure = caught.value
    assert failure.failed_phase is VerificationPhase.WORKLOAD_APPLY
    assert failure.primary_failure is error
    assert failure.cleanup_failure is None
    assert _phase_pairs(failure.phase_results) == [
        (VerificationPhase.NAMESPACE_CREATE, True),
        (VerificationPhase.WORKLOAD_APPLY, False),
        (VerificationPhase.NAMESPACE_CLEANUP, True),
    ]
    assert len(harness.namespace_adapter.deleted) == 1


def test_readiness_failure_triggers_owned_cleanup():
    inputs = _Inputs()
    error = RuntimeError("readiness observe failed")
    harness = _build(readiness_error=error)

    with pytest.raises(VerificationCoordinationFailed) as caught:
        harness.coordinator.coordinate(
            inputs.run_id,
            inputs.evaluation_id,
            inputs.workload,
            inputs.event,
            inputs.namespace,
            inputs.ownership,
            inputs.bundle,
        )

    failure = caught.value
    assert failure.failed_phase is VerificationPhase.READINESS_OBSERVE
    assert failure.primary_failure is error
    assert failure.cleanup_failure is None
    assert _phase_pairs(failure.phase_results) == [
        (VerificationPhase.NAMESPACE_CREATE, True),
        (VerificationPhase.WORKLOAD_APPLY, True),
        (VerificationPhase.READINESS_OBSERVE, False),
        (VerificationPhase.NAMESPACE_CLEANUP, True),
    ]
    assert len(harness.namespace_adapter.deleted) == 1


def test_responsiveness_failure_triggers_owned_cleanup():
    inputs = _Inputs()
    error = RuntimeError("responsiveness observe failed")
    harness = _build(responsiveness_error=error)

    with pytest.raises(VerificationCoordinationFailed) as caught:
        harness.coordinator.coordinate(
            inputs.run_id,
            inputs.evaluation_id,
            inputs.workload,
            inputs.event,
            inputs.namespace,
            inputs.ownership,
            inputs.bundle,
        )

    failure = caught.value
    assert failure.failed_phase is VerificationPhase.RESPONSIVENESS_OBSERVE
    assert failure.primary_failure is error
    assert failure.cleanup_failure is None
    assert _phase_pairs(failure.phase_results) == [
        (VerificationPhase.NAMESPACE_CREATE, True),
        (VerificationPhase.WORKLOAD_APPLY, True),
        (VerificationPhase.READINESS_OBSERVE, True),
        (VerificationPhase.RESPONSIVENESS_OBSERVE, False),
        (VerificationPhase.NAMESPACE_CLEANUP, True),
    ]
    assert len(harness.namespace_adapter.deleted) == 1


def test_projection_failure_triggers_owned_cleanup():
    inputs = _Inputs()
    unsupported = OperationalCheckResult(
        kind=OperationalCheckKind.WORKLOAD_RECREATED,
        observed=True,
        detail="unsupported check",
        source="test",
        occurrence_time=_T0,
    )
    harness = _build(responsiveness_result=unsupported)

    with pytest.raises(VerificationCoordinationFailed) as caught:
        harness.coordinator.coordinate(
            inputs.run_id,
            inputs.evaluation_id,
            inputs.workload,
            inputs.event,
            inputs.namespace,
            inputs.ownership,
            inputs.bundle,
        )

    failure = caught.value
    assert failure.failed_phase is VerificationPhase.OBSERVATION_PROJECT
    assert isinstance(failure.primary_failure, ValueError)
    assert "unsupported operational check kind" in str(
        failure.primary_failure
    )
    assert failure.cleanup_failure is None
    assert _phase_pairs(failure.phase_results) == [
        (VerificationPhase.NAMESPACE_CREATE, True),
        (VerificationPhase.WORKLOAD_APPLY, True),
        (VerificationPhase.READINESS_OBSERVE, True),
        (VerificationPhase.RESPONSIVENESS_OBSERVE, True),
        (VerificationPhase.OBSERVATION_PROJECT, False),
        (VerificationPhase.NAMESPACE_CLEANUP, True),
    ]
    assert len(harness.namespace_adapter.deleted) == 1


def test_evaluator_failure_triggers_owned_cleanup():
    inputs = _Inputs()
    evaluator = _RecordingEvaluator(_knowledge(inputs.workload_id))
    error = RuntimeError("evaluator failed")
    evaluator.error = error
    harness = _build(evaluator=evaluator)

    with pytest.raises(VerificationCoordinationFailed) as caught:
        harness.coordinator.coordinate(
            inputs.run_id,
            inputs.evaluation_id,
            inputs.workload,
            inputs.event,
            inputs.namespace,
            inputs.ownership,
            inputs.bundle,
        )

    failure = caught.value
    assert failure.failed_phase is VerificationPhase.REASONING_BOUNDARY
    assert failure.primary_failure is error
    assert failure.cleanup_failure is None
    assert _phase_pairs(failure.phase_results) == [
        (VerificationPhase.NAMESPACE_CREATE, True),
        (VerificationPhase.WORKLOAD_APPLY, True),
        (VerificationPhase.READINESS_OBSERVE, True),
        (VerificationPhase.RESPONSIVENESS_OBSERVE, True),
        (VerificationPhase.OBSERVATION_PROJECT, True),
        (VerificationPhase.REASONING_BOUNDARY, False),
        (VerificationPhase.NAMESPACE_CLEANUP, True),
    ]
    assert len(harness.namespace_adapter.deleted) == 1


def test_cleanup_failure_after_primary_failure_is_preserved():
    inputs = _Inputs()
    readiness_error = RuntimeError("readiness observe failed")
    delete_error = RuntimeError("delete owned failed")
    harness = _build(
        readiness_error=readiness_error, ns_delete_error=delete_error
    )

    with pytest.raises(VerificationCoordinationFailed) as caught:
        harness.coordinator.coordinate(
            inputs.run_id,
            inputs.evaluation_id,
            inputs.workload,
            inputs.event,
            inputs.namespace,
            inputs.ownership,
            inputs.bundle,
        )

    failure = caught.value
    assert failure.failed_phase is VerificationPhase.READINESS_OBSERVE
    assert failure.primary_failure is readiness_error
    assert failure.cleanup_failure is delete_error
    assert "namespace cleanup also failed" in str(failure)
    assert _phase_pairs(failure.phase_results) == [
        (VerificationPhase.NAMESPACE_CREATE, True),
        (VerificationPhase.WORKLOAD_APPLY, True),
        (VerificationPhase.READINESS_OBSERVE, False),
        (VerificationPhase.NAMESPACE_CLEANUP, False),
    ]
    assert len(harness.namespace_adapter.deleted) == 1


def test_cleanup_failure_after_success_raises_verification_cleanup_failed():
    inputs = _Inputs()
    readiness = _readiness_result()
    responsiveness = _responsiveness_result()
    knowledge = _knowledge(inputs.workload_id)
    evaluator = _RecordingEvaluator(knowledge)
    delete_error = RuntimeError("delete owned failed")
    harness = _build(
        readiness, responsiveness, evaluator, ns_delete_error=delete_error
    )

    with pytest.raises(VerificationCleanupFailed) as caught:
        harness.coordinator.coordinate(
            inputs.run_id,
            inputs.evaluation_id,
            inputs.workload,
            inputs.event,
            inputs.namespace,
            inputs.ownership,
            inputs.bundle,
        )

    failure = caught.value
    assert failure.run_id == inputs.run_id
    assert failure.cleanup_failure is delete_error
    assert failure.__cause__ is delete_error
    assert failure.knowledge is knowledge
    assert failure.observations == (
        project_operational_check(
            inputs.run_id, inputs.workload, inputs.event, readiness
        ),
        project_operational_check(
            inputs.run_id, inputs.workload, inputs.event, responsiveness
        ),
    )
    assert len(evaluator.requests) == 1
    assert failure.observations is evaluator.requests[0].observations
    assert failure.observations is not None
    assert _phase_pairs(failure.phase_results) == [
        (VerificationPhase.NAMESPACE_CREATE, True),
        (VerificationPhase.WORKLOAD_APPLY, True),
        (VerificationPhase.READINESS_OBSERVE, True),
        (VerificationPhase.RESPONSIVENESS_OBSERVE, True),
        (VerificationPhase.OBSERVATION_PROJECT, True),
        (VerificationPhase.REASONING_BOUNDARY, True),
        (VerificationPhase.NAMESPACE_CLEANUP, False),
    ]
    assert [
        phase_result.phase for phase_result in failure.phase_results
    ].count(VerificationPhase.NAMESPACE_CLEANUP) == 1
    assert len(harness.namespace_adapter.deleted) == 1
    assert not isinstance(failure, VerificationCoordinationResult)


# ---------------------------------------------------------------------------
# F: input and boundary predicates
# ---------------------------------------------------------------------------


def test_event_workload_id_mismatch_rejected_before_io():
    inputs = _Inputs()
    harness = _build()
    bad_event = RecoveryEvent(
        event_id=uuid.uuid4(), workload_id=uuid.uuid4()
    )

    with pytest.raises(ValueError):
        harness.coordinator.coordinate(
            inputs.run_id,
            inputs.evaluation_id,
            inputs.workload,
            bad_event,
            inputs.namespace,
            inputs.ownership,
            inputs.bundle,
        )

    assert harness.namespace_adapter.created == []
    assert harness.namespace_adapter.deleted == []
    assert harness.workload_adapter.applied == []


def test_namespace_run_id_mismatch_rejected():
    inputs = _Inputs()
    harness = _build()
    other_namespace = RunNamespaceIdentity(uuid.uuid4())

    with pytest.raises(ValueError):
        harness.coordinator.coordinate(
            inputs.run_id,
            inputs.evaluation_id,
            inputs.workload,
            inputs.event,
            other_namespace,
            inputs.ownership,
            inputs.bundle,
        )

    assert harness.namespace_adapter.created == []


def test_ownership_run_id_mismatch_rejected():
    inputs = _Inputs()
    harness = _build()
    other_ownership = NamespaceOwnership(
        run_id=uuid.uuid4(),
        workload_id=inputs.workload_id,
        bundle=inputs.identity,
        creation_source="brace_runtime_owned_verification",
    )

    with pytest.raises(ValueError):
        harness.coordinator.coordinate(
            inputs.run_id,
            inputs.evaluation_id,
            inputs.workload,
            inputs.event,
            inputs.namespace,
            other_ownership,
            inputs.bundle,
        )

    assert harness.namespace_adapter.created == []


def test_ownership_workload_id_mismatch_rejected():
    inputs = _Inputs()
    harness = _build()
    other_ownership = NamespaceOwnership(
        run_id=inputs.run_id,
        workload_id=uuid.uuid4(),
        bundle=inputs.identity,
        creation_source="brace_runtime_owned_verification",
    )

    with pytest.raises(ValueError):
        harness.coordinator.coordinate(
            inputs.run_id,
            inputs.evaluation_id,
            inputs.workload,
            inputs.event,
            inputs.namespace,
            other_ownership,
            inputs.bundle,
        )

    assert harness.namespace_adapter.created == []


def test_ownership_bundle_mismatch_rejected():
    inputs = _Inputs()
    harness = _build()
    other_ownership = NamespaceOwnership(
        run_id=inputs.run_id,
        workload_id=inputs.workload_id,
        bundle=WorkloadBundleIdentity(
            bundle_name="other-bundle", bundle_version="9.9.9"
        ),
        creation_source="brace_runtime_owned_verification",
    )

    with pytest.raises(ValueError):
        harness.coordinator.coordinate(
            inputs.run_id,
            inputs.evaluation_id,
            inputs.workload,
            inputs.event,
            inputs.namespace,
            other_ownership,
            inputs.bundle,
        )

    assert harness.namespace_adapter.created == []


def test_boundary_request_rejects_empty_observations():
    inputs = _Inputs()

    with pytest.raises(ValueError, match="observations must not be empty"):
        BoundaryEvaluationRequest(
            run_id=inputs.run_id,
            evaluation_id=inputs.evaluation_id,
            protected_workload=inputs.workload,
            recovery_event=inputs.event,
            observations=(),
        )


def test_boundary_request_rejects_wrong_observation_types():
    inputs = _Inputs()

    with pytest.raises(
        TypeError, match="observations must contain only RecoveryObservation"
    ):
        BoundaryEvaluationRequest(
            run_id=inputs.run_id,
            evaluation_id=inputs.evaluation_id,
            protected_workload=inputs.workload,
            recovery_event=inputs.event,
            observations=(object(),),
        )


def test_boundary_request_rejects_event_workload_mismatch():
    inputs = _Inputs()
    observation = _observation(inputs.workload_id, inputs.event.event_id)
    bad_event = RecoveryEvent(
        event_id=inputs.event.event_id, workload_id=uuid.uuid4()
    )

    with pytest.raises(
        ValueError, match="recovery_event workload_id does not match workload"
    ):
        BoundaryEvaluationRequest(
            run_id=inputs.run_id,
            evaluation_id=inputs.evaluation_id,
            protected_workload=inputs.workload,
            recovery_event=bad_event,
            observations=(observation,),
        )


def test_boundary_request_rejects_observation_event_mismatch():
    inputs = _Inputs()
    observation = _observation(inputs.workload_id, uuid.uuid4())

    with pytest.raises(
        ValueError, match="observation recovery_event_id does not match event"
    ):
        BoundaryEvaluationRequest(
            run_id=inputs.run_id,
            evaluation_id=inputs.evaluation_id,
            protected_workload=inputs.workload,
            recovery_event=inputs.event,
            observations=(observation,),
        )


def test_boundary_request_rejects_observation_workload_mismatch():
    inputs = _Inputs()
    observation = _observation(uuid.uuid4(), inputs.event.event_id)

    with pytest.raises(
        ValueError, match="observation workload_id does not match workload"
    ):
        BoundaryEvaluationRequest(
            run_id=inputs.run_id,
            evaluation_id=inputs.evaluation_id,
            protected_workload=inputs.workload,
            recovery_event=inputs.event,
            observations=(observation,),
        )


def test_missing_evaluator_reaches_real_boundary_and_still_cleans_up():
    inputs = _Inputs()
    harness = _build(evaluator=None)

    with pytest.raises(VerificationCoordinationFailed) as caught:
        harness.coordinator.coordinate(
            inputs.run_id,
            inputs.evaluation_id,
            inputs.workload,
            inputs.event,
            inputs.namespace,
            inputs.ownership,
            inputs.bundle,
        )

    failure = caught.value
    assert failure.failed_phase is VerificationPhase.REASONING_BOUNDARY
    assert isinstance(failure.primary_failure, ReasoningCoreNotProvided)
    assert failure.cleanup_failure is None
    assert _phase_pairs(failure.phase_results) == [
        (VerificationPhase.NAMESPACE_CREATE, True),
        (VerificationPhase.WORKLOAD_APPLY, True),
        (VerificationPhase.READINESS_OBSERVE, True),
        (VerificationPhase.RESPONSIVENESS_OBSERVE, True),
        (VerificationPhase.OBSERVATION_PROJECT, True),
        (VerificationPhase.REASONING_BOUNDARY, False),
        (VerificationPhase.NAMESPACE_CLEANUP, True),
    ]
    assert len(harness.namespace_adapter.deleted) == 1
    assert harness.namespace_adapter.deleted == [
        (inputs.namespace, inputs.ownership)
    ]


def test_evaluator_correct_subject_accepted():
    inputs = _Inputs()
    knowledge = _knowledge(inputs.workload.workload_id)
    evaluator = _RecordingEvaluator(knowledge)
    harness = _build(evaluator=evaluator)

    result = harness.coordinator.coordinate(
        inputs.run_id,
        inputs.evaluation_id,
        inputs.workload,
        inputs.event,
        inputs.namespace,
        inputs.ownership,
        inputs.bundle,
    )

    assert result.knowledge is knowledge
    assert result.run_id == inputs.run_id
    assert len(harness.namespace_adapter.deleted) == 1


def test_evaluator_unrelated_subject_rejected_and_cleans_up():
    inputs = _Inputs()
    unrelated_workload_id = uuid.uuid4()
    knowledge = _knowledge(unrelated_workload_id)
    evaluator = _RecordingEvaluator(knowledge)
    harness = _build(evaluator=evaluator)

    with pytest.raises(VerificationCoordinationFailed) as caught:
        harness.coordinator.coordinate(
            inputs.run_id,
            inputs.evaluation_id,
            inputs.workload,
            inputs.event,
            inputs.namespace,
            inputs.ownership,
            inputs.bundle,
        )

    failure = caught.value
    assert failure.failed_phase is VerificationPhase.REASONING_BOUNDARY
    assert isinstance(failure.primary_failure, ValueError)
    assert "does not match requested workload" in str(failure.primary_failure)
    assert failure.cleanup_failure is None
    assert _phase_pairs(failure.phase_results) == [
        (VerificationPhase.NAMESPACE_CREATE, True),
        (VerificationPhase.WORKLOAD_APPLY, True),
        (VerificationPhase.READINESS_OBSERVE, True),
        (VerificationPhase.RESPONSIVENESS_OBSERVE, True),
        (VerificationPhase.OBSERVATION_PROJECT, True),
        (VerificationPhase.REASONING_BOUNDARY, False),
        (VerificationPhase.NAMESPACE_CLEANUP, True),
    ]
    assert harness.namespace_adapter.deleted == [
        (inputs.namespace, inputs.ownership)
    ]


def test_invalid_evaluator_return_type_rejected_and_cleans_up():
    inputs = _Inputs()

    class _InvalidTypeEvaluator:
        def __init__(self) -> None:
            self.requests: list[BoundaryEvaluationRequest] = []

        def evaluate(self, request: BoundaryEvaluationRequest):
            self.requests.append(request)
            return {"not": "a RecoverabilityKnowledge"}

    harness = _build(evaluator=_InvalidTypeEvaluator())  # type: ignore

    with pytest.raises(VerificationCoordinationFailed) as caught:
        harness.coordinator.coordinate(
            inputs.run_id,
            inputs.evaluation_id,
            inputs.workload,
            inputs.event,
            inputs.namespace,
            inputs.ownership,
            inputs.bundle,
        )

    failure = caught.value
    assert failure.failed_phase is VerificationPhase.REASONING_BOUNDARY
    assert isinstance(failure.primary_failure, TypeError)
    assert "RecoverabilityKnowledge" in str(failure.primary_failure)
    assert failure.cleanup_failure is None
    assert harness.namespace_adapter.deleted == [
        (inputs.namespace, inputs.ownership)
    ]


def test_evaluator_rejection_retains_primary_failure_if_cleanup_fails():
    inputs = _Inputs()
    unrelated_workload_id = uuid.uuid4()
    knowledge = _knowledge(unrelated_workload_id)
    evaluator = _RecordingEvaluator(knowledge)
    cleanup_error = RuntimeError("cleanup failed after rejection")
    harness = _build(evaluator=evaluator, ns_delete_error=cleanup_error)

    with pytest.raises(VerificationCoordinationFailed) as caught:
        harness.coordinator.coordinate(
            inputs.run_id,
            inputs.evaluation_id,
            inputs.workload,
            inputs.event,
            inputs.namespace,
            inputs.ownership,
            inputs.bundle,
        )

    failure = caught.value
    assert failure.failed_phase is VerificationPhase.REASONING_BOUNDARY
    assert isinstance(failure.primary_failure, ValueError)
    assert failure.cleanup_failure is cleanup_error
    assert _phase_pairs(failure.phase_results) == [
        (VerificationPhase.NAMESPACE_CREATE, True),
        (VerificationPhase.WORKLOAD_APPLY, True),
        (VerificationPhase.READINESS_OBSERVE, True),
        (VerificationPhase.RESPONSIVENESS_OBSERVE, True),
        (VerificationPhase.OBSERVATION_PROJECT, True),
        (VerificationPhase.REASONING_BOUNDARY, False),
        (VerificationPhase.NAMESPACE_CLEANUP, False),
    ]


def test_recoverability_knowledge_rejects_naive_timestamp():
    naive_dt = datetime.datetime(2026, 8, 9, 12, 0)
    with pytest.raises(ValueError, match="must be timezone-aware"):
        RecoverabilityKnowledge(
            knowledge_id=uuid.uuid4(),
            subject=RecoverabilitySubject(
                subject_id=RecoverabilitySubjectId(value=uuid.uuid4())
            ),
            standing=RecoverabilityTruthStandingValue.RECOVERABILITY_AFFIRMED,
            established_at=naive_dt,
        )


def test_recoverability_knowledge_rejects_unavailable_utc_offset():
    class _NoOffsetTz(datetime.tzinfo):
        def utcoffset(self, dt):
            return None

        def dst(self, dt):
            return None

        def tzname(self, dt):
            return "NoOffset"

    dt_no_offset = datetime.datetime(2026, 8, 9, 12, 0, tzinfo=_NoOffsetTz())
    with pytest.raises(ValueError, match="must have an available UTC offset"):
        RecoverabilityKnowledge(
            knowledge_id=uuid.uuid4(),
            subject=RecoverabilitySubject(
                subject_id=RecoverabilitySubjectId(value=uuid.uuid4())
            ),
            standing=RecoverabilityTruthStandingValue.RECOVERABILITY_AFFIRMED,
            established_at=dt_no_offset,
        )


def test_recoverability_knowledge_normalizes_non_utc_offset_to_utc():
    ist_tz = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
    ist_dt = datetime.datetime(2026, 8, 9, 12, 0, tzinfo=ist_tz)
    k = RecoverabilityKnowledge(
        knowledge_id=uuid.uuid4(),
        subject=RecoverabilitySubject(
            subject_id=RecoverabilitySubjectId(value=uuid.uuid4())
        ),
        standing=RecoverabilityTruthStandingValue.RECOVERABILITY_AFFIRMED,
        established_at=ist_dt,
    )
    assert k.established_at.tzinfo == datetime.timezone.utc
    assert k.established_at.hour == 6
    assert k.established_at.minute == 30
    assert k.established_at == ist_dt
