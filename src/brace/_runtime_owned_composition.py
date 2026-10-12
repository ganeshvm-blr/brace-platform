import datetime
import time
import uuid

from kubernetes import client, config  # type: ignore

from brace._execution import RunNamespaceIdentity
from brace._http_client_transport import _HttpClientTransport
from brace._kubernetes_namespace import KubernetesNamespaceAdapter, NamespaceOwnership
from brace._kubernetes_readiness import KubernetesReadinessObserver
from brace._kubernetes_responsiveness import ApplicationResponsivenessObserver
from brace._kubernetes_workload import ApprovedWorkloadBundle, KubernetesWorkloadAdapter
from brace._recovery_event import RecoveryEvent
from brace._runtime_observation_convergence import _RuntimeConvergenceTiming
from brace._runtime_owned_verification import (
    RuntimeOwnedVerificationCoordinator,
    VerificationCoordinationResult,
)
from brace._workload import ProtectedWorkload
from brace.reasoning.boundary import RecoverabilityEvaluator


class _SystemUtcObservationClock:
    def now(self) -> datetime.datetime:
        return datetime.datetime.now(datetime.timezone.utc)


def _build_runtime_coordinator(
    evaluator: RecoverabilityEvaluator | None = None,
) -> RuntimeOwnedVerificationCoordinator:
    config.load_incluster_config()

    core_v1_api = client.CoreV1Api()
    apps_v1_api = client.AppsV1Api()

    namespace_adapter = KubernetesNamespaceAdapter(core_v1_api)
    workload_adapter = KubernetesWorkloadAdapter(core_v1_api, apps_v1_api)

    clock = _SystemUtcObservationClock()
    readiness_timing = _RuntimeConvergenceTiming(120.0, 2.0)
    readiness_observer = KubernetesReadinessObserver(
        apps_v1_api,
        clock,
        readiness_timing,
        time.monotonic,
        time.sleep,
    )

    transport = _HttpClientTransport()
    responsiveness_timing = _RuntimeConvergenceTiming(60.0, 2.0, 5.0)
    responsiveness_observer = ApplicationResponsivenessObserver(
        transport,
        clock,
        responsiveness_timing,
        time.monotonic,
        time.sleep,
    )

    return RuntimeOwnedVerificationCoordinator(
        namespace_adapter,
        workload_adapter,
        readiness_observer,
        responsiveness_observer,
        evaluator,
    )


def run_runtime_owned_verification(
    run_id: uuid.UUID,
    evaluation_id: uuid.UUID,
    protected_workload: ProtectedWorkload,
    recovery_event: RecoveryEvent,
    namespace: RunNamespaceIdentity,
    ownership: NamespaceOwnership,
    workload_bundle: ApprovedWorkloadBundle,
    evaluator: RecoverabilityEvaluator | None = None,
) -> VerificationCoordinationResult:
    coordinator = _build_runtime_coordinator(evaluator)
    return coordinator.coordinate(
        run_id,
        evaluation_id,
        protected_workload,
        recovery_event,
        namespace,
        ownership,
        workload_bundle,
    )
