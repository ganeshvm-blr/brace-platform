import uuid
from dataclasses import dataclass
from typing import Callable, NoReturn, TypeVar, cast

from brace._execution import RunNamespaceIdentity, WorkloadBundleIdentity
from brace._kubernetes_namespace import NamespaceOwnership
from brace._kubernetes_workload import (
    ApprovedConfigMap,
    ApprovedContainer,
    ApprovedDeployment,
    ApprovedService,
    ApprovedWorkloadBundle,
)
from brace._recovery_event import RecoveryEvent
from brace._workload import ProtectedWorkload

_MODE = "runtime_owned_verification"
_CREATION_SOURCE = "brace_runtime_owned_verification"
_TOP_LEVEL_FIELDS = {
    "mode",
    "run_id",
    "evaluation_id",
    "workload",
    "event",
    "bundle",
}
_PROHIBITED_TOP_LEVEL_FIELDS = (
    "observations",
    "namespace",
    "namespace_identity",
    "ownership",
    "creation_source",
)


class _RuntimeOwnedRequestError(ValueError):
    pass


@dataclass(frozen=True)
class RuntimeOwnedVerificationRequest:
    run_id: uuid.UUID
    evaluation_id: uuid.UUID
    protected_workload: ProtectedWorkload
    recovery_event: RecoveryEvent
    namespace: RunNamespaceIdentity
    ownership: NamespaceOwnership
    workload_bundle: ApprovedWorkloadBundle


def parse_runtime_owned_verification_request(
    value: object,
) -> RuntimeOwnedVerificationRequest:
    data = _object(value, "top-level value")
    _require_mode(data.get("mode", _MISSING))
    _reject_top_level_fields(data)

    run_id = _parse_uuid(_required(data, "run_id"), "run_id")
    evaluation_id = _parse_uuid(_required(data, "evaluation_id"), "evaluation_id")
    protected_workload = _parse_workload(_required(data, "workload"))
    recovery_event = _parse_event(_required(data, "event"))
    if recovery_event.workload_id != protected_workload.workload_id:
        _fail("event.workload_id", "must equal workload.workload_id")

    workload_bundle = _parse_bundle(_required(data, "bundle"))
    namespace = RunNamespaceIdentity(run_id)
    ownership = NamespaceOwnership(
        run_id=run_id,
        workload_id=protected_workload.workload_id,
        bundle=workload_bundle.identity,
        creation_source=_CREATION_SOURCE,
    )

    return RuntimeOwnedVerificationRequest(
        run_id=run_id,
        evaluation_id=evaluation_id,
        protected_workload=protected_workload,
        recovery_event=recovery_event,
        namespace=namespace,
        ownership=ownership,
        workload_bundle=workload_bundle,
    )


class _Missing:
    pass


_MISSING = _Missing()
_T = TypeVar("_T")


def _fail(path: str, reason: str) -> NoReturn:
    raise _RuntimeOwnedRequestError(f"{path} {reason}")


def _object(value: object, path: str) -> dict[object, object]:
    if type(value) is not dict:
        _fail(path, "must be a JSON object")
    return cast(dict[object, object], value)


def _required(data: dict[object, object], field: str, parent: str | None = None) -> object:
    if field not in data:
        path = field if parent is None else f"{parent}.{field}"
        _fail(path, "is required")
    return data[field]


def _reject_unknown(data: dict[object, object], allowed: set[str], path: str) -> None:
    rejected_paths: list[str] = []
    for key in data:
        if not isinstance(key, str) or key not in allowed:
            display = key if isinstance(key, str) else "<non-string>"
            rejected_paths.append(f"{path}.{display}")
    if rejected_paths:
        _fail(sorted(rejected_paths)[0], "is not allowed")


def _reject_top_level_fields(data: dict[object, object]) -> None:
    for field in _PROHIBITED_TOP_LEVEL_FIELDS:
        if field in data:
            _fail(field, "is not allowed")
    _reject_unknown(data, _TOP_LEVEL_FIELDS, "top-level value")


def _require_mode(value: object) -> None:
    if value is _MISSING:
        _fail("mode", "is required")
    if not isinstance(value, str):
        _fail("mode", "must be a string")
    if value != _MODE:
        _fail("mode", "must be runtime_owned_verification")


def _parse_uuid(value: object, path: str) -> uuid.UUID:
    if not isinstance(value, str):
        _fail(path, "must be a string")
    try:
        return uuid.UUID(value)
    except ValueError:
        _fail(path, "is not a valid UUID")


def _string(value: object, path: str) -> str:
    if not isinstance(value, str):
        _fail(path, "must be a string")
    if not value:
        _fail(path, "must not be empty")
    return value


def _identity_string(value: object, path: str) -> str:
    text = _string(value, path)
    if text != text.strip():
        _fail(path, "must not contain leading or trailing whitespace")
    return text


def _integer(value: object, path: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        _fail(path, "must be an integer")
    return value


def _construct(
    path: str, constructor: Callable[..., _T], *args: object
) -> _T:
    try:
        return constructor(*args)
    except (TypeError, ValueError):
        _fail(path, "is invalid")


def _parse_workload(value: object) -> ProtectedWorkload:
    data = _object(value, "workload")
    _reject_unknown(data, {"workload_id", "name"}, "workload")
    workload_id = _parse_uuid(
        _required(data, "workload_id", "workload"), "workload.workload_id"
    )
    name = _string(_required(data, "name", "workload"), "workload.name")
    return _construct("workload", ProtectedWorkload, workload_id, name)


def _parse_event(value: object) -> RecoveryEvent:
    data = _object(value, "event")
    _reject_unknown(data, {"event_id", "workload_id"}, "event")
    event_id = _parse_uuid(_required(data, "event_id", "event"), "event.event_id")
    workload_id = _parse_uuid(
        _required(data, "workload_id", "event"), "event.workload_id"
    )
    return _construct("event", RecoveryEvent, event_id, workload_id)


def _parse_bundle(value: object) -> ApprovedWorkloadBundle:
    data = _object(value, "bundle")
    _reject_unknown(data, {"identity", "config_map", "deployment", "service"}, "bundle")
    identity = _parse_bundle_identity(_required(data, "identity", "bundle"))
    config_map = (
        None
        if "config_map" not in data or data["config_map"] is None
        else _parse_config_map(data["config_map"])
    )
    deployment = _parse_deployment(_required(data, "deployment", "bundle"), config_map)
    service = _parse_service(_required(data, "service", "bundle"))
    if service.target_port != deployment.container.container_port:
        _fail(
            "bundle.service.target_port",
            "must equal bundle.deployment.container.container_port",
        )
    return _construct(
        "bundle",
        ApprovedWorkloadBundle,
        identity,
        config_map,
        deployment,
        service,
    )


def _parse_bundle_identity(value: object) -> WorkloadBundleIdentity:
    data = _object(value, "bundle.identity")
    _reject_unknown(data, {"name", "version"}, "bundle.identity")
    name = _identity_string(
        _required(data, "name", "bundle.identity"), "bundle.identity.name"
    )
    version = _identity_string(
        _required(data, "version", "bundle.identity"), "bundle.identity.version"
    )
    return _construct("bundle.identity", WorkloadBundleIdentity, name, version)


def _parse_config_map(value: object) -> ApprovedConfigMap:
    data = _object(value, "bundle.config_map")
    _reject_unknown(data, {"name", "data"}, "bundle.config_map")
    name = _string(_required(data, "name", "bundle.config_map"), "bundle.config_map.name")
    config_data_value = _required(data, "data", "bundle.config_map")
    config_data = _object(config_data_value, "bundle.config_map.data")
    values: dict[str, str] = {}
    for key, item in config_data.items():
        if not isinstance(key, str):
            _fail("bundle.config_map.data", "keys must be strings")
        if not isinstance(item, str):
            _fail("bundle.config_map.data", "values must be strings")
        values[key] = item
    return _construct("bundle.config_map", ApprovedConfigMap, name, values)


def _parse_deployment(
    value: object, config_map: ApprovedConfigMap | None
) -> ApprovedDeployment:
    data = _object(value, "bundle.deployment")
    _reject_unknown(
        data,
        {"name", "replicas", "container"},
        "bundle.deployment",
    )
    name = _string(
        _required(data, "name", "bundle.deployment"), "bundle.deployment.name"
    )
    replicas = _integer(
        _required(data, "replicas", "bundle.deployment"),
        "bundle.deployment.replicas",
    )
    if replicas != 1:
        _fail("bundle.deployment.replicas", "must equal 1")
    container = _parse_container(_required(data, "container", "bundle.deployment"))
    config_map_name = None if config_map is None else config_map.name
    return _construct(
        "bundle.deployment",
        ApprovedDeployment,
        name,
        container,
        replicas,
        config_map_name,
    )


def _parse_container(value: object) -> ApprovedContainer:
    data = _object(value, "bundle.deployment.container")
    _reject_unknown(
        data,
        {"name", "image", "container_port"},
        "bundle.deployment.container",
    )
    name = _string(
        _required(data, "name", "bundle.deployment.container"),
        "bundle.deployment.container.name",
    )
    image = _string(
        _required(data, "image", "bundle.deployment.container"),
        "bundle.deployment.container.image",
    )
    container_port = _integer(
        _required(data, "container_port", "bundle.deployment.container"),
        "bundle.deployment.container.container_port",
    )
    return _construct(
        "bundle.deployment.container",
        ApprovedContainer,
        name,
        image,
        container_port,
    )


def _parse_service(value: object) -> ApprovedService:
    data = _object(value, "bundle.service")
    _reject_unknown(data, {"name", "port", "target_port"}, "bundle.service")
    name = _string(_required(data, "name", "bundle.service"), "bundle.service.name")
    port = _integer(_required(data, "port", "bundle.service"), "bundle.service.port")
    target_port = _integer(
        _required(data, "target_port", "bundle.service"), "bundle.service.target_port"
    )
    return _construct("bundle.service", ApprovedService, name, port, target_port)
