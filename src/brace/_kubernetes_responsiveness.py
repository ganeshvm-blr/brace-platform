from dataclasses import dataclass
from typing import Callable, Final, Literal, Protocol

from brace._execution import (
    OperationalCheckKind,
    OperationalCheckResult,
    RunNamespaceIdentity,
    _ObservationClock,
)
from brace._kubernetes_namespace import NamespaceOwnership
from brace._kubernetes_workload import ApprovedService, WorkloadOwnershipRefused
from brace._runtime_observation_convergence import (
    _responsiveness_cause_retryable,
    _ResponsivenessConvergence,
    _ResponsivenessSample,
    _RuntimeConvergenceTiming,
)

_SOURCE: Final[str] = "kubernetes-service-http"


def _evaluate_status(
    target_url: str, status_code: int
) -> tuple[bool, str]:
    if status_code == 200:
        return (True, f"HTTP response status 200 from {target_url}")
    return (False, f"HTTP response status {status_code} from {target_url}")


def _result(observed: bool, detail: str, clock: _ObservationClock) -> OperationalCheckResult:
    return OperationalCheckResult(
        kind=OperationalCheckKind.APPLICATION_RESPONSIVE,
        observed=observed,
        detail=detail,
        source=_SOURCE,
        occurrence_time=clock.now(),
    )


@dataclass(frozen=True)
class _ServiceTarget:
    service_name: str
    namespace: str
    port: int

    def __post_init__(self) -> None:
        if not isinstance(self.service_name, str):
            raise TypeError("service_name must be a string")
        if not isinstance(self.namespace, str):
            raise TypeError("namespace must be a string")
        if not isinstance(self.port, int) or isinstance(self.port, bool):
            raise TypeError("port must be an integer")
        if not self.service_name:
            raise ValueError("service_name must not be empty")
        if not self.namespace:
            raise ValueError("namespace must not be empty")
        if not (1 <= self.port <= 65535):
            raise ValueError("port must be between 1 and 65535")

    @property
    def url(self) -> str:
        return f"http://{self.service_name}.{self.namespace}.svc:{self.port}/"

    def __str__(self) -> str:
        return self.url


@dataclass(frozen=True)
class _HttpRequest:
    target: _ServiceTarget
    timeout: float

    def __post_init__(self) -> None:
        if not isinstance(self.target, _ServiceTarget):
            raise TypeError("target must be a _ServiceTarget")
        if not isinstance(self.timeout, (int, float)) or isinstance(self.timeout, bool):
            raise TypeError("timeout must be a number")
        if self.timeout <= 0:
            raise ValueError("timeout must be positive")


@dataclass(frozen=True)
class _HttpResponse:
    status_code: int

    def __post_init__(self) -> None:
        if not isinstance(self.status_code, int) or isinstance(self.status_code, bool):
            raise TypeError("status_code must be an integer")


class _HttpTransport(Protocol):
    def get(
        self,
        request: _HttpRequest,
        *,
        follow_redirects: Literal[False],
    ) -> _HttpResponse: ...


class _TransportError(Exception):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


class ResponsivenessOperationFailed(Exception):
    def __init__(
        self,
        operation: str,
        target: str,
        timeout: float,
        reason: str,
    ) -> None:
        self.operation = operation
        self.target = target
        self.timeout = timeout
        self.reason = reason
        super().__init__(
            f"Operation {operation} failed for {target}: {reason} ({timeout}s)"
        )


def _build_service_target(
    service: ApprovedService, namespace: RunNamespaceIdentity
) -> _ServiceTarget:
    return _ServiceTarget(
        service_name=service.name,
        namespace=namespace.namespace,
        port=service.port,
    )


class ApplicationResponsivenessObserver:
    def __init__(
        self,
        transport: _HttpTransport,
        clock: _ObservationClock,
        timing: _RuntimeConvergenceTiming,
        monotonic_time_source: Callable[[], float],
        sleeper: Callable[[float], None],
    ) -> None:
        self._transport = transport
        self._clock = clock
        self._responsiveness = _ResponsivenessConvergence(
            timing, monotonic_time_source, sleeper
        )

    def observe(
        self,
        namespace: RunNamespaceIdentity,
        ownership: NamespaceOwnership,
        service: ApprovedService,
    ) -> OperationalCheckResult:
        if namespace.run_id != ownership.run_id:
            raise WorkloadOwnershipRefused(
                f"Run ID mismatch: namespace.run_id ({namespace.run_id}) "
                f"!= ownership.run_id ({ownership.run_id})"
            )

        target = _build_service_target(service, namespace)

        def _sample(effective_timeout: float) -> _ResponsivenessSample:
            request = _HttpRequest(target=target, timeout=effective_timeout)
            try:
                response = self._transport.get(request, follow_redirects=False)
            except _TransportError as e:
                if _responsiveness_cause_retryable(e):
                    return _ResponsivenessSample(
                        False,
                        (
                            f"HTTP response failed for {target.url}: "
                            f"{e.reason}"
                        ),
                    )
                raise ResponsivenessOperationFailed(
                    "GET",
                    target.url,
                    effective_timeout,
                    e.reason,
                ) from e

            observed, detail = _evaluate_status(target.url, response.status_code)
            return _ResponsivenessSample(observed, detail)

        terminal = self._responsiveness.observe(_sample)
        return _result(terminal.observed, terminal.detail, self._clock)
