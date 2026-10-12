"""Public demonstration entrypoint for Brace Platform.

Executes a local demonstration of the public coordination chain using
in-process test doubles.

This demonstration proves:
- Run-scoped request and identity validation
- In-process namespace and workload adapter orchestration through test doubles
- Simulated readiness and HTTP responsiveness observation
- Operational observation projection
- Evaluator protocol boundary handoff and subject continuity validation
- Ownership-scoped cleanup execution through test double

Important limitations:
- This is a local public demonstration using in-process test doubles.
- No live Kubernetes cluster is contacted.
- No backup restoration is performed.
- No AWS resources are created.
- No network request was made.
- The demonstration evaluator is a minimal presentation test double
  implementing RecoverabilityEvaluator; it contains no proprietary reasoning
  logic, rules, scoring, evidence interpretation, thresholds, or decision
  pipeline.
"""

from __future__ import annotations

import datetime
import sys
import uuid
from typing import TextIO

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
from brace._recoverability_knowledge import RecoverabilityKnowledge
from brace._recoverability_subject import (
    RecoverabilitySubject,
    RecoverabilitySubjectId,
)
from brace._recoverability_truth_standing_value import (
    RecoverabilityTruthStandingValue,
)
from brace._recovery_event import RecoveryEvent
from brace._runtime_owned_verification import (
    BoundaryEvaluationRequest,
    RuntimeOwnedVerificationCoordinator,
    VerificationCoordinationResult,
    VerificationPhase,
)
from brace._workload import ProtectedWorkload
from brace.reasoning.boundary import RecoverabilityEvaluator


class DemoRecoverabilityEvaluator:
    """Public presentation test double returning fixed affirmed standing.

    This evaluator exists solely for local public demonstration. It is a
    presentation test double only and contains no rules, scoring, evidence
    interpretation, thresholds, decision pipeline, or proprietary
    recoverability reasoning. Its output is non-authoritative.
    """

    def evaluate(self, request: BoundaryEvaluationRequest) -> RecoverabilityKnowledge:
        return RecoverabilityKnowledge(
            knowledge_id=uuid.uuid4(),
            subject=RecoverabilitySubject(
                subject_id=RecoverabilitySubjectId(
                    value=request.protected_workload.workload_id
                )
            ),
            standing=RecoverabilityTruthStandingValue.RECOVERABILITY_AFFIRMED,
            established_at=datetime.datetime.now(datetime.timezone.utc),
        )


class _DemoNamespaceAdapter:
    def __init__(self) -> None:
        self.created: list[tuple[RunNamespaceIdentity, NamespaceOwnership]] = []
        self.deleted: list[tuple[RunNamespaceIdentity, NamespaceOwnership]] = []

    def create(
        self,
        namespace: RunNamespaceIdentity,
        ownership: NamespaceOwnership,
    ) -> None:
        self.created.append((namespace, ownership))

    def delete_owned(
        self,
        namespace: RunNamespaceIdentity,
        ownership: NamespaceOwnership,
    ) -> None:
        self.deleted.append((namespace, ownership))


class _DemoWorkloadAdapter:
    def __init__(self) -> None:
        self.applied: list[
            tuple[
                RunNamespaceIdentity,
                NamespaceOwnership,
                ApprovedWorkloadBundle,
            ]
        ] = []

    def apply(
        self,
        namespace: RunNamespaceIdentity,
        ownership: NamespaceOwnership,
        bundle: ApprovedWorkloadBundle,
    ) -> None:
        self.applied.append((namespace, ownership, bundle))


class _DemoReadinessObserver:
    def __init__(self) -> None:
        self.observed: list[
            tuple[RunNamespaceIdentity, NamespaceOwnership, ApprovedDeployment]
        ] = []

    def observe(
        self,
        namespace: RunNamespaceIdentity,
        ownership: NamespaceOwnership,
        deployment: ApprovedDeployment,
    ) -> OperationalCheckResult:
        self.observed.append((namespace, ownership, deployment))
        return OperationalCheckResult(
            kind=OperationalCheckKind.KUBERNETES_READY,
            observed=True,
            detail="Simulated deployment readiness observed (1/1 replicas available)",
            source="brace.demo.readiness_test_double",
            occurrence_time=datetime.datetime.now(datetime.timezone.utc),
        )


class _DemoResponsivenessObserver:
    def __init__(self) -> None:
        self.observed: list[
            tuple[RunNamespaceIdentity, NamespaceOwnership, ApprovedService]
        ] = []

    def observe(
        self,
        namespace: RunNamespaceIdentity,
        ownership: NamespaceOwnership,
        service: ApprovedService,
    ) -> OperationalCheckResult:
        self.observed.append((namespace, ownership, service))
        return OperationalCheckResult(
            kind=OperationalCheckKind.APPLICATION_RESPONSIVE,
            observed=True,
            detail="Simulated service responsiveness observed with status 200",
            source="brace.demo.responsiveness_test_double",
            occurrence_time=datetime.datetime.now(datetime.timezone.utc),
        )


def _build_demo_request() -> tuple[
    uuid.UUID,
    uuid.UUID,
    ProtectedWorkload,
    RecoveryEvent,
    RunNamespaceIdentity,
    NamespaceOwnership,
    ApprovedWorkloadBundle,
]:
    run_id = uuid.uuid4()
    evaluation_id = uuid.uuid4()
    workload_id = uuid.uuid4()

    workload = ProtectedWorkload(workload_id=workload_id, name="demo-workload")
    event = RecoveryEvent(event_id=uuid.uuid4(), workload_id=workload_id)
    namespace = RunNamespaceIdentity(run_id)
    identity = WorkloadBundleIdentity(bundle_name="demo-bundle", bundle_version="1.0.0")

    container = ApprovedContainer(
        name="demo-app",
        image="registry.example.invalid/demo-app@sha256:" + ("0" * 64),
        container_port=8080,
    )
    deployment = ApprovedDeployment(
        name="demo-deployment",
        container=container,
        replicas=1,
    )
    service = ApprovedService(
        name="demo-service",
        port=8080,
        target_port=8080,
    )
    bundle = ApprovedWorkloadBundle(
        identity=identity,
        config_map=None,
        deployment=deployment,
        service=service,
    )
    ownership = NamespaceOwnership(
        run_id=run_id,
        workload_id=workload_id,
        bundle=identity,
        creation_source="brace_runtime_owned_verification",
    )

    return (
        run_id,
        evaluation_id,
        workload,
        event,
        namespace,
        ownership,
        bundle,
    )


def run_demo(
    evaluator: RecoverabilityEvaluator | None = None,
    out: TextIO | None = None,
) -> VerificationCoordinationResult:
    """Run the local public demonstration and print the structured walkthrough."""
    output = out if out is not None else sys.stdout

    if evaluator is None:
        evaluator = DemoRecoverabilityEvaluator()

    (
        run_id,
        evaluation_id,
        workload,
        event,
        namespace,
        ownership,
        bundle,
    ) = _build_demo_request()

    coordinator = RuntimeOwnedVerificationCoordinator(
        namespace_adapter=_DemoNamespaceAdapter(),
        workload_adapter=_DemoWorkloadAdapter(),
        readiness_observer=_DemoReadinessObserver(),
        responsiveness_observer=_DemoResponsivenessObserver(),
        evaluator=evaluator,
    )

    output.write("====================================================\n")
    output.write("Brace Platform v1 Public Demonstration\n")
    output.write("====================================================\n\n")

    output.write("[PASS] Request and identity validation\n")

    result = coordinator.coordinate(
        run_id=run_id,
        evaluation_id=evaluation_id,
        protected_workload=workload,
        recovery_event=event,
        namespace=namespace,
        ownership=ownership,
        workload_bundle=bundle,
    )

    phase_labels = {
        VerificationPhase.NAMESPACE_CREATE: "Namespace lifecycle orchestration through test double",
        VerificationPhase.WORKLOAD_APPLY: "Workload application through test double",
        VerificationPhase.READINESS_OBSERVE: "Simulated readiness observation",
        VerificationPhase.RESPONSIVENESS_OBSERVE: "Simulated HTTP responsiveness observation",
        VerificationPhase.OBSERVATION_PROJECT: "Operational observation projection",
        VerificationPhase.REASONING_BOUNDARY: "External evaluator protocol handoff",
        VerificationPhase.NAMESPACE_CLEANUP: "Cleanup orchestration through test double",
    }

    for phase_result in result.phase_results:
        label = phase_labels.get(phase_result.phase, phase_result.phase.value)
        status = "PASS" if phase_result.succeeded else "FAIL"
        output.write(f"[{status}] {label}\n")

    output.write("\nDemo result:\n")
    output.write(
        "Affirmed standing returned by the public demo evaluator test double.\n"
        "No production recoverability decision was made.\n\n"
    )
    output.write("Important:\n")
    output.write("This demonstration uses in-process test doubles.\n")
    output.write("No live Kubernetes cluster was contacted.\n")
    output.write("No backup restoration was performed.\n")
    output.write("No AWS resources were created.\n")
    output.write("No network request was made.\n\n")
    output.write("====================================================\n")

    return result


def main() -> None:
    run_demo()


if __name__ == "__main__":
    main()
