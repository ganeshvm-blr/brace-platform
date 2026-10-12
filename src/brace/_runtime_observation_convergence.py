"""Private bounded operational sampling for the runtime observers.

This module owns the exclusive-deadline sampling loop and the narrow typed
transient classification. It never constructs observations, results, or
knowledge artifacts.
"""

import math
import socket
from dataclasses import dataclass
from typing import Callable

import urllib3.exceptions


def _validate_positive_number(value: float, name: str) -> None:
    if isinstance(value, bool):
        raise TypeError(f"{name} must not be a bool")
    if not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a number")
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite")
    if value <= 0:
        raise ValueError(f"{name} must be greater than zero")


@dataclass(frozen=True, slots=True)
class _RuntimeConvergenceTiming:
    convergence_deadline_seconds: float
    polling_interval_seconds: float
    maximum_request_timeout_seconds: float | None = None

    def __post_init__(self) -> None:
        _validate_positive_number(
            self.convergence_deadline_seconds, "convergence_deadline_seconds"
        )
        _validate_positive_number(
            self.polling_interval_seconds, "polling_interval_seconds"
        )
        if self.polling_interval_seconds > self.convergence_deadline_seconds:
            raise ValueError(
                "polling_interval_seconds must not exceed "
                "convergence_deadline_seconds"
            )
        timeout = self.maximum_request_timeout_seconds
        if timeout is not None:
            _validate_positive_number(
                timeout, "maximum_request_timeout_seconds"
            )
            if timeout > 5.0:
                raise ValueError(
                    "maximum_request_timeout_seconds must be no greater than 5.0"
                )
            if timeout > self.convergence_deadline_seconds:
                raise ValueError(
                    "maximum_request_timeout_seconds must not exceed "
                    "convergence_deadline_seconds"
                )


@dataclass(frozen=True)
class _ReadinessSample:
    observed: bool
    detail: str


@dataclass(frozen=True)
class _ReadinessTerminal:
    observed: bool
    detail: str


@dataclass(frozen=True)
class _ResponsivenessSample:
    observed: bool
    detail: str


@dataclass(frozen=True)
class _ResponsivenessTerminal:
    observed: bool
    detail: str


_RETRYABLE_API_STATUSES: frozenset[int] = frozenset(
    (404, 408, 409, 429, 500, 502, 503, 504)
)


def _readiness_status_retryable(status: int | None) -> bool:
    if status is None:
        return False
    return status in _RETRYABLE_API_STATUSES


def _readiness_wire_retryable(cause: BaseException) -> bool:
    if isinstance(cause, urllib3.exceptions.MaxRetryError):
        reason = cause.reason
        if not isinstance(reason, BaseException):
            return False
        return _readiness_wire_retryable(reason)
    if isinstance(cause, urllib3.exceptions.ProtocolError):
        inner = cause.__cause__
        return isinstance(inner, (ConnectionResetError, ConnectionAbortedError))
    return isinstance(
        cause,
        (
            urllib3.exceptions.NameResolutionError,
            urllib3.exceptions.NewConnectionError,
            urllib3.exceptions.ConnectTimeoutError,
            urllib3.exceptions.ReadTimeoutError,
        ),
    )


def _readiness_api_retryable(
    status: int | None, cause: BaseException | None
) -> bool:
    if _readiness_status_retryable(status):
        return True
    return cause is not None and _readiness_wire_retryable(cause)


def _responsiveness_cause_retryable(transport_error: BaseException) -> bool:
    inner = transport_error.__cause__
    if inner is None:
        return False
    return isinstance(
        inner,
        (
            socket.gaierror,
            ConnectionRefusedError,
            ConnectionResetError,
            ConnectionAbortedError,
            TimeoutError,
        ),
    )


class _ReadinessConvergence:
    def __init__(
        self,
        timing: _RuntimeConvergenceTiming,
        monotonic_time_source: Callable[[], float],
        sleeper: Callable[[float], None],
    ) -> None:
        if timing.maximum_request_timeout_seconds is not None:
            raise ValueError(
                "readiness convergence timing must not carry a request timeout"
            )
        self._timing = timing
        self._monotonic = monotonic_time_source
        self._sleeper = sleeper

    def observe(
        self, sampler: Callable[[], _ReadinessSample]
    ) -> _ReadinessTerminal:
        deadline = (
            self._monotonic() + self._timing.convergence_deadline_seconds
        )
        last_detail = ""
        remaining = deadline - self._monotonic()
        while remaining > 0:
            sample = sampler()
            last_detail = sample.detail
            if sample.observed:
                return _ReadinessTerminal(True, sample.detail)
            sleep_duration = min(self._timing.polling_interval_seconds, remaining)
            if sleep_duration <= 0:
                return _ReadinessTerminal(False, sample.detail)
            self._sleeper(sleep_duration)
            remaining = deadline - self._monotonic()
        return _ReadinessTerminal(False, last_detail)


class _ResponsivenessConvergence:
    def __init__(
        self,
        timing: _RuntimeConvergenceTiming,
        monotonic_time_source: Callable[[], float],
        sleeper: Callable[[float], None],
    ) -> None:
        if timing.maximum_request_timeout_seconds is None:
            raise ValueError(
                "responsiveness convergence timing requires a request timeout"
            )
        self._timing = timing
        self._maximum_timeout = timing.maximum_request_timeout_seconds
        self._monotonic = monotonic_time_source
        self._sleeper = sleeper

    def observe(
        self,
        sampler: Callable[[float], _ResponsivenessSample],
    ) -> _ResponsivenessTerminal:
        deadline = (
            self._monotonic() + self._timing.convergence_deadline_seconds
        )
        last_detail = ""
        remaining = deadline - self._monotonic()
        while remaining > 0:
            effective_timeout = min(self._maximum_timeout, remaining)
            sample = sampler(effective_timeout)
            last_detail = sample.detail
            attempt_end = self._monotonic()
            if sample.observed:
                if attempt_end < deadline:
                    return _ResponsivenessTerminal(True, sample.detail)
                return _ResponsivenessTerminal(False, sample.detail)
            sleep_duration = min(self._timing.polling_interval_seconds, remaining)
            if sleep_duration <= 0:
                return _ResponsivenessTerminal(False, sample.detail)
            self._sleeper(sleep_duration)
            remaining = deadline - self._monotonic()
        return _ResponsivenessTerminal(False, last_detail)
