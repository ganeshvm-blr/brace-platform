"""Runtime-owned Kubernetes verification orchestration.

Coordinates the public operational portion of a verification and hands
immutable observations across an opaque reasoning boundary.

The proprietary recoverability decision logic is not included here.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from enum import Enum

from brace._execution import RunNamespaceIdentity
from brace._kubernetes_namespace import (
    KubernetesNamespaceAdapter,
    NamespaceOwnership,
)
from brace._kubernetes_readiness import KubernetesReadinessObserver
from brace._kubernetes_responsiveness import ApplicationResponsivenessObserver
from brace._kubernetes_workload import (
    ApprovedWorkloadBundle,
    KubernetesWorkloadAdapter,
)
from brace._operational_projection import project_operational_check
from brace._recoverability_knowledge import RecoverabilityKnowledge
from brace._recoverability_subject import RecoverabilitySubject
from brace._recovery_event import RecoveryEvent
from brace._recovery_observation import RecoveryObservation
from brace._workload import ProtectedWorkload
from brace.reasoning.boundary import (
    RecoverabilityEvaluator,
    evaluate_recoverability,
)


class VerificationPhase(str, Enum):
    """Observable phases of a runtime-owned verification."""

    NAMESPACE_CREATE = "namespace_create"
    WORKLOAD_APPLY = "workload_apply"
    READINESS_OBSERVE = "readiness_observe"
    RESPONSIVENESS_OBSERVE = "responsiveness_observe"
    OBSERVATION_PROJECT = "observation_project"
    REASONING_BOUNDARY = "reasoning_boundary"
    NAMESPACE_CLEANUP = "namespace_cleanup"


@dataclass(frozen=True)
class ExecutionPhaseResult:
    """Recorded outcome of one operational verification phase."""

    phase: VerificationPhase
    succeeded: bool

    def __post_init__(self) -> None:
        if not isinstance(self.phase, VerificationPhase):
            raise TypeError("phase must be a VerificationPhase")
        if not isinstance(self.succeeded, bool):
            raise TypeError("succeeded must be a bool")


@dataclass(frozen=True)
class BoundaryEvaluationRequest:
    """Minimal public envelope passed to the opaque reasoning boundary."""

    run_id: uuid.UUID
    evaluation_id: uuid.UUID
    protected_workload: ProtectedWorkload
    recovery_event: RecoveryEvent
    observations: tuple[RecoveryObservation, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.run_id, uuid.UUID):
            raise TypeError("run_id must be a uuid.UUID")
        if not isinstance(self.evaluation_id, uuid.UUID):
            raise TypeError("evaluation_id must be a uuid.UUID")
        if not isinstance(self.protected_workload, ProtectedWorkload):
            raise TypeError(
                "protected_workload must be a ProtectedWorkload"
            )
        if not isinstance(self.recovery_event, RecoveryEvent):
            raise TypeError("recovery_event must be a RecoveryEvent")
        if not isinstance(self.observations, tuple):
            raise TypeError("observations must be a tuple")
        if not self.observations:
            raise ValueError("observations must not be empty")
        if not all(
            isinstance(observation, RecoveryObservation)
            for observation in self.observations
        ):
            raise TypeError(
                "observations must contain only RecoveryObservation values"
            )
        if (
            self.recovery_event.workload_id
            != self.protected_workload.workload_id
        ):
            raise ValueError(
                "recovery_event workload_id does not match workload"
            )
        for observation in self.observations:
            if observation.recovery_event_id != self.recovery_event.event_id:
                raise ValueError(
                    "observation recovery_event_id does not match event"
                )
            if (
                observation.workload_id
                != self.protected_workload.workload_id
            ):
                raise ValueError(
                    "observation workload_id does not match workload"
                )


@dataclass(frozen=True)
class VerificationCoordinationResult:
    """Public result of runtime-owned verification coordination."""

    run_id: uuid.UUID
    evaluation_id: uuid.UUID
    knowledge: RecoverabilityKnowledge
    phase_results: tuple[ExecutionPhaseResult, ...]
    observations: tuple[RecoveryObservation, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.run_id, uuid.UUID):
            raise TypeError("run_id must be a uuid.UUID")
        if not isinstance(self.evaluation_id, uuid.UUID):
            raise TypeError("evaluation_id must be a uuid.UUID")
        if not isinstance(self.knowledge, RecoverabilityKnowledge):
            raise TypeError(
                "knowledge must be a RecoverabilityKnowledge"
            )
        if not isinstance(self.phase_results, tuple):
            raise TypeError("phase_results must be a tuple")
        if not all(
            isinstance(result, ExecutionPhaseResult)
            for result in self.phase_results
        ):
            raise TypeError(
                "phase_results must contain only ExecutionPhaseResult values"
            )
        if not isinstance(self.observations, tuple):
            raise TypeError("observations must be a tuple")
        if not self.observations:
            raise ValueError("observations must not be empty")
        if not all(
            isinstance(observation, RecoveryObservation)
            for observation in self.observations
        ):
            raise TypeError(
                "observations must contain only RecoveryObservation values"
            )


class VerificationCoordinationFailed(Exception):
    """Raised when runtime-owned coordination cannot complete."""

    def __init__(
        self,
        run_id: uuid.UUID,
        failed_phase: VerificationPhase,
        primary_failure: Exception,
        cleanup_failure: Exception | None,
        phase_results: tuple[ExecutionPhaseResult, ...],
    ) -> None:
        self.run_id = run_id
        self.failed_phase = failed_phase
        self.primary_failure = primary_failure
        self.cleanup_failure = cleanup_failure
        self.phase_results = phase_results

        message = (
            f"verification coordination failed at {failed_phase.value}"
        )
        if cleanup_failure is not None:
            message = f"{message}; namespace cleanup also failed"
        super().__init__(message)


class VerificationCleanupFailed(Exception):
    """Raised when namespace cleanup fails after a successful verification.

    The verification itself completed through the reasoning boundary; the
    produced knowledge, projected observations, and full phase record are
    preserved on this exception. The original cleanup exception is available
    as ``cleanup_failure`` and as this exception's ``__cause__``.
    """

    def __init__(
        self,
        run_id: uuid.UUID,
        cleanup_failure: Exception,
        knowledge: RecoverabilityKnowledge,
        observations: tuple[RecoveryObservation, ...],
        phase_results: tuple[ExecutionPhaseResult, ...],
    ) -> None:
        self.run_id = run_id
        self.cleanup_failure = cleanup_failure
        self.knowledge = knowledge
        self.observations = observations
        self.phase_results = phase_results
        super().__init__(
            f"verification coordination succeeded but namespace cleanup "
            f"failed for run {run_id}"
        )


@dataclass(frozen=True)
class _PhaseFailure(Exception):
    phase: VerificationPhase
    original: Exception


class RuntimeOwnedVerificationCoordinator:
    """Coordinate public runtime operations and the opaque boundary handoff."""

    def __init__(
        self,
        namespace_adapter: KubernetesNamespaceAdapter,
        workload_adapter: KubernetesWorkloadAdapter,
        readiness_observer: KubernetesReadinessObserver,
        responsiveness_observer: ApplicationResponsivenessObserver,
        evaluator: RecoverabilityEvaluator | None,
    ) -> None:
        if namespace_adapter is None:
            raise TypeError("namespace_adapter must not be None")
        if workload_adapter is None:
            raise TypeError("workload_adapter must not be None")
        if readiness_observer is None:
            raise TypeError("readiness_observer must not be None")
        if responsiveness_observer is None:
            raise TypeError(
                "responsiveness_observer must not be None"
            )
        if evaluator is not None and not isinstance(
            evaluator,
            RecoverabilityEvaluator,
        ):
            raise TypeError(
                "evaluator must implement RecoverabilityEvaluator or be None"
            )

        self._namespace_adapter = namespace_adapter
        self._workload_adapter = workload_adapter
        self._readiness_observer = readiness_observer
        self._responsiveness_observer = responsiveness_observer
        self._evaluator = evaluator


    def coordinate(
        self,
        run_id: uuid.UUID,
        evaluation_id: uuid.UUID,
        protected_workload: ProtectedWorkload,
        recovery_event: RecoveryEvent,
        namespace: RunNamespaceIdentity,
        ownership: NamespaceOwnership,
        workload_bundle: ApprovedWorkloadBundle,
    ) -> VerificationCoordinationResult:
        self._validate_input(
            run_id,
            evaluation_id,
            protected_workload,
            recovery_event,
            namespace,
            ownership,
            workload_bundle,
        )

        phase_results: list[ExecutionPhaseResult] = []
        cleanup_required = False

        try:
            try:
                cleanup_required = True
                self._namespace_adapter.create(namespace, ownership)
            except Exception as error:
                self._record(
                    phase_results,
                    VerificationPhase.NAMESPACE_CREATE,
                    False,
                )
                raise _PhaseFailure(
                    VerificationPhase.NAMESPACE_CREATE,
                    error,
                ) from error

            self._record(
                phase_results,
                VerificationPhase.NAMESPACE_CREATE,
                True,
            )

            try:
                self._workload_adapter.apply(
                    namespace,
                    ownership,
                    workload_bundle,
                )
            except Exception as error:
                self._record(
                    phase_results,
                    VerificationPhase.WORKLOAD_APPLY,
                    False,
                )
                raise _PhaseFailure(
                    VerificationPhase.WORKLOAD_APPLY,
                    error,
                ) from error

            self._record(
                phase_results,
                VerificationPhase.WORKLOAD_APPLY,
                True,
            )

            try:
                readiness_result = self._readiness_observer.observe(
                    namespace,
                    ownership,
                    workload_bundle.deployment,
                )
            except Exception as error:
                self._record(
                    phase_results,
                    VerificationPhase.READINESS_OBSERVE,
                    False,
                )
                raise _PhaseFailure(
                    VerificationPhase.READINESS_OBSERVE,
                    error,
                ) from error

            self._record(
                phase_results,
                VerificationPhase.READINESS_OBSERVE,
                True,
            )

            try:
                responsiveness_result = (
                    self._responsiveness_observer.observe(
                        namespace,
                        ownership,
                        workload_bundle.service,
                    )
                )
            except Exception as error:
                self._record(
                    phase_results,
                    VerificationPhase.RESPONSIVENESS_OBSERVE,
                    False,
                )
                raise _PhaseFailure(
                    VerificationPhase.RESPONSIVENESS_OBSERVE,
                    error,
                ) from error

            self._record(
                phase_results,
                VerificationPhase.RESPONSIVENESS_OBSERVE,
                True,
            )

            try:
                observations = (
                    project_operational_check(
                        run_id,
                        protected_workload,
                        recovery_event,
                        readiness_result,
                    ),
                    project_operational_check(
                        run_id,
                        protected_workload,
                        recovery_event,
                        responsiveness_result,
                    ),
                )
            except Exception as error:
                self._record(
                    phase_results,
                    VerificationPhase.OBSERVATION_PROJECT,
                    False,
                )
                raise _PhaseFailure(
                    VerificationPhase.OBSERVATION_PROJECT,
                    error,
                ) from error

            self._record(
                phase_results,
                VerificationPhase.OBSERVATION_PROJECT,
                True,
            )

            try:
                boundary_request = BoundaryEvaluationRequest(
                    run_id=run_id,
                    evaluation_id=evaluation_id,
                    protected_workload=protected_workload,
                    recovery_event=recovery_event,
                    observations=observations,
                )
                knowledge = evaluate_recoverability(
                    boundary_request,
                    self._evaluator,
                )
                if not isinstance(knowledge, RecoverabilityKnowledge):
                    raise TypeError(
                        f"evaluator must return a RecoverabilityKnowledge instance, "
                        f"got {type(knowledge).__name__}"
                    )
                if knowledge.subject.subject_id.value != protected_workload.workload_id:
                    raise ValueError(
                        f"evaluator result subject {knowledge.subject.subject_id.value} does not match "
                        f"requested workload {protected_workload.workload_id}"
                    )
            except Exception as error:
                self._record(
                    phase_results,
                    VerificationPhase.REASONING_BOUNDARY,
                    False,
                )
                raise _PhaseFailure(
                    VerificationPhase.REASONING_BOUNDARY,
                    error,
                ) from error

            self._record(
                phase_results,
                VerificationPhase.REASONING_BOUNDARY,
                True,
            )

        except _PhaseFailure as failure:
            self._attempt_cleanup_after_failure(
                run_id,
                namespace,
                ownership,
                failure,
                phase_results,
                cleanup_required,
            )

        try:
            self._namespace_adapter.delete_owned(
                namespace,
                ownership,
            )
        except Exception as error:
            self._record(
                phase_results,
                VerificationPhase.NAMESPACE_CLEANUP,
                False,
            )
            raise VerificationCleanupFailed(
                run_id=run_id,
                cleanup_failure=error,
                knowledge=knowledge,
                observations=observations,
                phase_results=tuple(phase_results),
            ) from error
        else:
            self._record(
                phase_results,
                VerificationPhase.NAMESPACE_CLEANUP,
                True,
            )

        return VerificationCoordinationResult(
            run_id=run_id,
            evaluation_id=evaluation_id,
            knowledge=knowledge,
            phase_results=tuple(phase_results),
            observations=observations,
        )

    @staticmethod
    def _record(
        phase_results: list[ExecutionPhaseResult],
        phase: VerificationPhase,
        succeeded: bool,
    ) -> None:
        phase_results.append(
            ExecutionPhaseResult(
                phase=phase,
                succeeded=succeeded,
            )
        )

    def _attempt_cleanup_after_failure(
        self,
        run_id: uuid.UUID,
        namespace: RunNamespaceIdentity,
        ownership: NamespaceOwnership,
        failure: _PhaseFailure,
        phase_results: list[ExecutionPhaseResult],
        cleanup_required: bool,
    ) -> None:
        cleanup_failure: Exception | None = None

        if cleanup_required:
            try:
                self._namespace_adapter.delete_owned(
                    namespace,
                    ownership,
                )
            except Exception as error:
                cleanup_failure = error
                self._record(
                    phase_results,
                    VerificationPhase.NAMESPACE_CLEANUP,
                    False,
                )
            else:
                self._record(
                    phase_results,
                    VerificationPhase.NAMESPACE_CLEANUP,
                    True,
                )

        raise VerificationCoordinationFailed(
            run_id=run_id,
            failed_phase=failure.phase,
            primary_failure=failure.original,
            cleanup_failure=cleanup_failure,
            phase_results=tuple(phase_results),
        ) from failure.original

    @staticmethod
    def _validate_input(
        run_id: uuid.UUID,
        evaluation_id: uuid.UUID,
        protected_workload: ProtectedWorkload,
        recovery_event: RecoveryEvent,
        namespace: RunNamespaceIdentity,
        ownership: NamespaceOwnership,
        workload_bundle: ApprovedWorkloadBundle,
    ) -> None:
        if not isinstance(run_id, uuid.UUID):
            raise TypeError("run_id must be a uuid.UUID")
        if not isinstance(evaluation_id, uuid.UUID):
            raise TypeError("evaluation_id must be a uuid.UUID")
        if not isinstance(protected_workload, ProtectedWorkload):
            raise TypeError(
                "protected_workload must be a ProtectedWorkload"
            )
        if not isinstance(recovery_event, RecoveryEvent):
            raise TypeError(
                "recovery_event must be a RecoveryEvent"
            )
        if not isinstance(namespace, RunNamespaceIdentity):
            raise TypeError(
                "namespace must be a RunNamespaceIdentity"
            )
        if not isinstance(ownership, NamespaceOwnership):
            raise TypeError(
                "ownership must be a NamespaceOwnership"
            )
        if not isinstance(
            workload_bundle,
            ApprovedWorkloadBundle,
        ):
            raise TypeError(
                "workload_bundle must be an ApprovedWorkloadBundle"
            )

        if (
            recovery_event.workload_id
            != protected_workload.workload_id
        ):
            raise ValueError(
                "recovery_event workload_id does not match workload"
            )
        if namespace.run_id != run_id:
            raise ValueError(
                "namespace run_id does not match run_id"
            )
        if ownership.run_id != run_id:
            raise ValueError(
                "ownership run_id does not match run_id"
            )
        if (
            ownership.workload_id
            != protected_workload.workload_id
        ):
            raise ValueError(
                "ownership workload_id does not match workload"
            )
        if ownership.bundle != workload_bundle.identity:
            raise ValueError(
                "ownership bundle does not match workload bundle"
            )