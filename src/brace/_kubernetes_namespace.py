from dataclasses import dataclass
from enum import Enum
from typing import Any
from uuid import UUID

from kubernetes.client.exceptions import ApiException  # type: ignore

from brace._execution import RunNamespaceIdentity, WorkloadBundleIdentity


@dataclass(frozen=True)
class NamespaceOwnership:
    run_id: UUID
    workload_id: UUID
    bundle: WorkloadBundleIdentity
    creation_source: str

    def __post_init__(self) -> None:
        if not isinstance(self.run_id, UUID):
            raise TypeError("run_id must be a UUID")
        if not isinstance(self.workload_id, UUID):
            raise TypeError("workload_id must be a UUID")
        if not isinstance(self.bundle, WorkloadBundleIdentity):
            raise TypeError("bundle must be a WorkloadBundleIdentity")
        if not isinstance(self.creation_source, str):
            raise TypeError("creation_source must be a string")
        if not self.creation_source.strip():
            raise ValueError("creation_source must not be empty or blank")


class NamespaceInspectionOutcome(str, Enum):
    ABSENT = "absent"
    OWNED_BY_CURRENT_RUN = "owned_by_current_run"
    OWNERSHIP_MISMATCH = "ownership_mismatch"
    OWNERSHIP_MISSING = "ownership_missing"


@dataclass(frozen=True)
class NamespaceInspection:
    identity: RunNamespaceIdentity
    outcome: NamespaceInspectionOutcome
    detail: str

    def __post_init__(self) -> None:
        if not isinstance(self.identity, RunNamespaceIdentity):
            raise TypeError("identity must be a RunNamespaceIdentity")
        if not isinstance(self.outcome, NamespaceInspectionOutcome):
            raise TypeError("outcome must be a NamespaceInspectionOutcome")
        if not isinstance(self.detail, str):
            raise TypeError("detail must be a string")
        if not self.detail.strip():
            raise ValueError("detail must not be empty or blank")


def _ownership_labels(ownership: NamespaceOwnership) -> dict[str, str]:
    return {
        "app.kubernetes.io/managed-by": "brace",
        "brace.io/run-id": str(ownership.run_id),
        "brace.io/workload-id": str(ownership.workload_id),
        "brace.io/bundle-name": ownership.bundle.bundle_name,
        "brace.io/bundle-version": ownership.bundle.bundle_version,
        "brace.io/creation-source": ownership.creation_source,
    }


class KubernetesNamespaceError(Exception):
    pass


class NamespaceOwnershipRefused(KubernetesNamespaceError):
    pass


class NamespaceOperationFailed(KubernetesNamespaceError):
    def __init__(
        self, operation: str, namespace: str, status_code: int | None, reason: str | None
    ) -> None:
        self.operation = operation
        self.namespace = namespace
        self.status_code = status_code
        self.reason = reason
        super().__init__(
            f"Operation {operation} failed for namespace {namespace}: {reason} ({status_code})"
        )


def _validate_run_consistency(
    identity: RunNamespaceIdentity, ownership: NamespaceOwnership
) -> None:
    if identity.run_id != ownership.run_id:
        msg = (
            f"Run ID mismatch: identity.run_id ({identity.run_id}) "
            f"!= ownership.run_id ({ownership.run_id})"
        )
        raise NamespaceOwnershipRefused(msg)


class KubernetesNamespaceAdapter:
    def __init__(self, core_v1_api: Any) -> None:
        self._api = core_v1_api

    def inspect(
        self, identity: RunNamespaceIdentity, expected_ownership: NamespaceOwnership
    ) -> NamespaceInspection:
        if not isinstance(identity, RunNamespaceIdentity):
            raise TypeError("identity must be a RunNamespaceIdentity")
        if not isinstance(expected_ownership, NamespaceOwnership):
            raise TypeError("expected_ownership must be a NamespaceOwnership")

        _validate_run_consistency(identity, expected_ownership)

        try:
            ns = self._api.read_namespace(name=identity.namespace)
        except ApiException as e:
            if e.status == 404:
                return NamespaceInspection(
                    identity, NamespaceInspectionOutcome.ABSENT, "namespace not found"
                )
            raise NamespaceOperationFailed(
                "inspect", identity.namespace, e.status, e.reason
            ) from e

        labels = (ns.metadata.labels or {}) if ns.metadata else {}
        expected = _ownership_labels(expected_ownership)

        missing_keys = [k for k in expected if k not in labels]
        if missing_keys:
            return NamespaceInspection(
                identity,
                NamespaceInspectionOutcome.OWNERSHIP_MISSING,
                f"missing labels: {', '.join(missing_keys)}",
            )

        mismatched = [k for k in expected if labels[k] != expected[k]]
        if mismatched:
            return NamespaceInspection(
                identity,
                NamespaceInspectionOutcome.OWNERSHIP_MISMATCH,
                f"mismatched labels: {', '.join(mismatched)}",
            )

        return NamespaceInspection(
            identity, NamespaceInspectionOutcome.OWNED_BY_CURRENT_RUN, "owned by current run"
        )

    def create(self, identity: RunNamespaceIdentity, ownership: NamespaceOwnership) -> None:
        if not isinstance(identity, RunNamespaceIdentity):
            raise TypeError("identity must be a RunNamespaceIdentity")
        if not isinstance(ownership, NamespaceOwnership):
            raise TypeError("ownership must be a NamespaceOwnership")

        _validate_run_consistency(identity, ownership)

        insp = self.inspect(identity, ownership)
        if insp.outcome == NamespaceInspectionOutcome.OWNED_BY_CURRENT_RUN:
            raise NamespaceOwnershipRefused(
                f"namespace {identity.namespace} is already owned by current run"
            )
        if insp.outcome in (
            NamespaceInspectionOutcome.OWNERSHIP_MISMATCH,
            NamespaceInspectionOutcome.OWNERSHIP_MISSING,
        ):
            msg = (
                f"namespace {identity.namespace} exists with mismatched/missing ownership: "
                f"{insp.detail}"
            )
            raise NamespaceOwnershipRefused(msg)

        from kubernetes.client import V1Namespace, V1ObjectMeta  # type: ignore

        body = V1Namespace(
            metadata=V1ObjectMeta(
                name=identity.namespace,
                labels=_ownership_labels(ownership),
            )
        )

        try:
            self._api.create_namespace(body=body)
        except ApiException as e:
            if e.status == 409:
                insp2 = self.inspect(identity, ownership)
                if insp2.outcome == NamespaceInspectionOutcome.OWNED_BY_CURRENT_RUN:
                    msg = (
                        f"namespace {identity.namespace} already owned by current run "
                        "after conflict"
                    )
                    raise NamespaceOwnershipRefused(msg)
                elif insp2.outcome in (
                    NamespaceInspectionOutcome.OWNERSHIP_MISMATCH,
                    NamespaceInspectionOutcome.OWNERSHIP_MISSING,
                ):
                    msg = (
                        f"namespace {identity.namespace} mismatched or missing ownership "
                        "after conflict"
                    )
                    raise NamespaceOwnershipRefused(msg)
                else:
                    msg = f"Conflict and subsequent inspection yielded: {insp2.outcome.value}"
                    raise NamespaceOperationFailed(
                        "create", identity.namespace, e.status, msg
                    ) from e
            raise NamespaceOperationFailed(
                "create", identity.namespace, e.status, e.reason
            ) from e

    def delete_owned(
        self, identity: RunNamespaceIdentity, expected_ownership: NamespaceOwnership
    ) -> None:
        if not isinstance(identity, RunNamespaceIdentity):
            raise TypeError("identity must be a RunNamespaceIdentity")
        if not isinstance(expected_ownership, NamespaceOwnership):
            raise TypeError("expected_ownership must be a NamespaceOwnership")

        _validate_run_consistency(identity, expected_ownership)

        insp = self.inspect(identity, expected_ownership)
        if insp.outcome == NamespaceInspectionOutcome.ABSENT:
            return
        if insp.outcome in (
            NamespaceInspectionOutcome.OWNERSHIP_MISMATCH,
            NamespaceInspectionOutcome.OWNERSHIP_MISSING,
        ):
            msg = (
                f"refusing to delete namespace {identity.namespace} "
                f"due to {insp.outcome.value}: {insp.detail}"
            )
            raise NamespaceOwnershipRefused(msg)

        try:
            self._api.delete_namespace(name=identity.namespace)
        except ApiException as e:
            if e.status == 404:
                return
            raise NamespaceOperationFailed(
                "delete_owned", identity.namespace, e.status, e.reason
            ) from e
