from typing import Any, Callable, Protocol

from kubernetes.client.exceptions import ApiException  # type: ignore

from brace._execution import (
    OperationalCheckKind,
    OperationalCheckResult,
    RunNamespaceIdentity,
    _ObservationClock,
)
from brace._kubernetes_namespace import NamespaceOwnership, _ownership_labels
from brace._kubernetes_workload import (
    ApprovedDeployment,
    WorkloadMismatchRefused,
    WorkloadOwnershipRefused,
)
from brace._runtime_observation_convergence import (
    _readiness_api_retryable,
    _ReadinessConvergence,
    _ReadinessSample,
    _RuntimeConvergenceTiming,
)


class _AppsV1Api(Protocol):
    def read_namespaced_deployment(self, name: str, namespace: str) -> Any: ...


class ReadinessOperationFailed(Exception):
    def __init__(
        self,
        operation: str,
        namespace: str,
        deployment_name: str,
        status_code: int | None,
        reason: str | None,
    ) -> None:
        self.operation = operation
        self.namespace = namespace
        self.deployment_name = deployment_name
        self.status_code = status_code
        self.reason = reason
        super().__init__(
            f"Operation {operation} failed for deployment {deployment_name} "
            f"in namespace {namespace}: {reason} ({status_code})"
        )


_OBSERVER_SOURCE = "brace._kubernetes_readiness.KubernetesReadinessObserver"
_RESOURCE_KIND_LABEL = "brace.io/resource-kind"
_DEPLOYMENT_KIND = "deployment"
_WORKLOAD_INSTANCE_LABEL = "brace.io/workload-instance"


def _validate_ownership_labels(
    labels: dict[str, str], ownership: NamespaceOwnership
) -> None:
    expected = _ownership_labels(ownership)
    for k, v in expected.items():
        if k not in labels:
            raise WorkloadMismatchRefused(
                f"Deployment missing required label {k}"
            )
        if labels[k] != v:
            raise WorkloadMismatchRefused(
                f"Deployment label mismatch for {k}: expected {v}, got {labels[k]}"
            )


def _validate_pod_labels(
    pod_labels: dict[str, str],
    ownership: NamespaceOwnership,
    deployment_name: str,
) -> None:
    expected = _ownership_labels(ownership)
    for k, v in expected.items():
        if pod_labels.get(k) != v:
            raise WorkloadMismatchRefused(
                f"Pod-template label mismatch for {k}: expected {v}, "
                f"got {pod_labels.get(k)}"
            )

    if pod_labels.get(_RESOURCE_KIND_LABEL) != _DEPLOYMENT_KIND:
        actual = pod_labels.get(_RESOURCE_KIND_LABEL)
        raise WorkloadMismatchRefused(
            f"Pod-template resource-kind mismatch: expected {_DEPLOYMENT_KIND}, "
            f"got {actual}"
        )

    if pod_labels.get(_WORKLOAD_INSTANCE_LABEL) != deployment_name:
        actual = pod_labels.get(_WORKLOAD_INSTANCE_LABEL)
        raise WorkloadMismatchRefused(
            f"Pod-template workload-instance mismatch: expected {deployment_name}, "
            f"got {actual}"
        )


def _validate_selector(
    spec: Any, deployment_name: str
) -> None:
    if not spec or not spec.selector:
        raise WorkloadMismatchRefused("Deployment missing selector")
    actual = (spec.selector.match_labels or {}) if spec.selector else {}
    expected = {_WORKLOAD_INSTANCE_LABEL: deployment_name}
    if actual != expected:
        raise WorkloadMismatchRefused(
            f"Deployment selector mismatch: expected {expected}, got {actual}"
        )



def _evaluate_readiness(
    dep: Any, deployment_name: str
) -> tuple[bool, str]:
    spec = dep.spec

    if spec.replicas != 1:
        return (
            False,
            (
                f"Deployment {deployment_name} "
                f"desired replicas not equal to one"
            ),
        )

    metadata = dep.metadata
    if metadata.generation is None:
        return (
            False,
            (
                f"Deployment {deployment_name} "
                f"deployment generation missing"
            ),
        )

    status = dep.status
    if status is None or status.observed_generation is None:
        return (
            False,
            (
                f"Deployment {deployment_name} "
                f"observed generation missing"
            ),
        )

    if status.observed_generation < metadata.generation:
        return (
            False,
            (
                f"Deployment {deployment_name} "
                f"observed generation behind desired generation"
            ),
        )

    if status.updated_replicas is None:
        return (
            False,
            (
                f"Deployment {deployment_name} "
                f"updated replicas missing"
            ),
        )
    if status.updated_replicas != 1:
        return (
            False,
            (
                f"Deployment {deployment_name} "
                f"updated replicas not equal to one"
            ),
        )

    if status.ready_replicas is None:
        return (
            False,
            (
                f"Deployment {deployment_name} "
                f"ready replicas missing"
            ),
        )
    if status.ready_replicas != 1:
        return (
            False,
            (
                f"Deployment {deployment_name} "
                f"ready replicas not equal to one"
            ),
        )

    if status.available_replicas is None:
        return (
            False,
            (
                f"Deployment {deployment_name} "
                f"available replicas missing"
            ),
        )
    if status.available_replicas != 1:
        return (
            False,
            (
                f"Deployment {deployment_name} "
                f"available replicas not equal to one"
            ),
        )

    unavailable = status.unavailable_replicas
    if unavailable is not None and unavailable > 0:
        return (
            False,
            (
                f"Deployment {deployment_name} "
                f"unavailable replicas greater than zero"
            ),
        )

    return (True, f"Deployment {deployment_name} is ready")


def _api_retryable_detail(
    namespace: RunNamespaceIdentity, deployment_name: str, error: ApiException
) -> str:
    if error.status == 404:
        return (
            f"Deployment {deployment_name} not found "
            f"in namespace {namespace.namespace}"
        )
    if error.status is not None:
        return (
            f"Deployment {deployment_name} unavailable "
            f"in namespace {namespace.namespace} (HTTP {error.status})"
        )
    return (
        f"Deployment {deployment_name} unavailable "
        f"in namespace {namespace.namespace}"
    )


def _result(observed: bool, detail: str, clock: _ObservationClock) -> OperationalCheckResult:
    return OperationalCheckResult(
        kind=OperationalCheckKind.KUBERNETES_READY,
        observed=observed,
        detail=detail,
        source=_OBSERVER_SOURCE,
        occurrence_time=clock.now(),
    )


class KubernetesReadinessObserver:
    def __init__(
        self,
        apps_v1_api: _AppsV1Api,
        clock: _ObservationClock,
        timing: _RuntimeConvergenceTiming,
        monotonic_time_source: Callable[[], float],
        sleeper: Callable[[float], None],
    ) -> None:
        self._apps_api = apps_v1_api
        self._clock = clock
        self._readiness = _ReadinessConvergence(
            timing, monotonic_time_source, sleeper
        )

    def observe(
        self,
        namespace: RunNamespaceIdentity,
        ownership: NamespaceOwnership,
        deployment: ApprovedDeployment,
    ) -> OperationalCheckResult:
        if namespace.run_id != ownership.run_id:
            raise WorkloadOwnershipRefused(
                f"Run ID mismatch: namespace.run_id ({namespace.run_id}) "
                f"!= ownership.run_id ({ownership.run_id})"
            )

        def _sample() -> _ReadinessSample:
            try:
                dep = self._apps_api.read_namespaced_deployment(
                    name=deployment.name, namespace=namespace.namespace
                )
            except ApiException as e:
                if _readiness_api_retryable(e.status, e.__cause__):
                    return _ReadinessSample(
                        False,
                        _api_retryable_detail(namespace, deployment.name, e),
                    )
                raise ReadinessOperationFailed(
                    "read_namespaced_deployment",
                    namespace.namespace,
                    deployment.name,
                    e.status,
                    e.reason,
                ) from e

            if dep.metadata.name != deployment.name:
                raise WorkloadOwnershipRefused(
                    f"Deployment name mismatch: expected {deployment.name}, "
                    f"got {dep.metadata.name}"
                )

            if dep.metadata.namespace and dep.metadata.namespace != namespace.namespace:
                raise WorkloadOwnershipRefused(
                    f"Deployment namespace mismatch: expected {namespace.namespace}, "
                    f"got {dep.metadata.namespace}"
                )

            dep_labels = dep.metadata.labels or {}
            _validate_ownership_labels(dep_labels, ownership)

            if dep_labels.get(_RESOURCE_KIND_LABEL) != _DEPLOYMENT_KIND:
                actual = dep_labels.get(_RESOURCE_KIND_LABEL)
                raise WorkloadMismatchRefused(
                    f"Deployment resource-kind mismatch: expected {_DEPLOYMENT_KIND}, "
                    f"got {actual}"
                )

            _validate_selector(dep.spec, deployment.name)

            template = dep.spec.template
            if not template or not template.metadata:
                raise WorkloadMismatchRefused("Deployment missing pod template")
            pod_labels = template.metadata.labels or {}
            _validate_pod_labels(pod_labels, ownership, deployment.name)

            observed, detail = _evaluate_readiness(dep, deployment.name)
            return _ReadinessSample(observed, detail)

        terminal = self._readiness.observe(_sample)
        return _result(terminal.observed, terminal.detail, self._clock)
