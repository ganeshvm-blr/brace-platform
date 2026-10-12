import re
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping, Protocol, Tuple

from kubernetes.client.exceptions import ApiException  # type: ignore

from brace._execution import RunNamespaceIdentity, WorkloadBundleIdentity
from brace._kubernetes_namespace import NamespaceOwnership, _ownership_labels

_DNS_LABEL_REGEX = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?$")


def _validate_dns_name(name: str) -> None:
    if not isinstance(name, str):
        raise TypeError("Name must be a string")
    if not name:
        raise ValueError("Name must not be empty")
    if len(name) > 63:
        raise ValueError("Name must not be longer than 63 characters")
    if not _DNS_LABEL_REGEX.match(name):
        raise ValueError(f"Name '{name}' is not a valid DNS-1123 label")


def _validate_image(image: str) -> None:
    if not isinstance(image, str):
        raise TypeError("Image must be a string")
    if not image:
        raise ValueError("Image must not be empty")
    if "@sha256:" not in image:
        raise ValueError("Image must be digest-pinned using @sha256:")
    parts = image.split("@sha256:")
    if len(parts) != 2:
        raise ValueError("Image must contain exactly one @sha256: separator")
    repo, digest = parts
    if not repo:
        raise ValueError("Image repository component must not be empty")
    if len(digest) != 64 or not all(c in "0123456789abcdef" for c in digest):
        raise ValueError("Digest must be exactly 64 hexadecimal characters")


@dataclass(frozen=True)
class ApprovedConfigMap:
    name: str
    data: Mapping[str, str]

    def __post_init__(self) -> None:
        _validate_dns_name(self.name)
        if not isinstance(self.data, Mapping):
            raise TypeError("data must be a mapping")
        for k, v in self.data.items():
            if not isinstance(k, str) or not k:
                raise ValueError("ConfigMap keys must be non-empty strings")
            if not isinstance(v, str):
                raise TypeError("ConfigMap values must be strings")
        copied = {str(k): str(v) for k, v in self.data.items()}
        object.__setattr__(self, "data", MappingProxyType(copied))


@dataclass(frozen=True)
class ApprovedContainer:
    name: str
    image: str
    container_port: int
    command: Tuple[str, ...] | None = None
    args: Tuple[str, ...] | None = None

    def __post_init__(self) -> None:
        _validate_dns_name(self.name)
        _validate_image(self.image)
        if not isinstance(self.container_port, int) or isinstance(self.container_port, bool):
            raise TypeError("container_port must be an integer")
        if not (1 <= self.container_port <= 65535):
            raise ValueError("container_port must be between 1 and 65535")
        if self.command is not None:
            if not isinstance(self.command, tuple):
                raise TypeError("command must be a tuple")
            for cmd in self.command:
                if not isinstance(cmd, str) or not cmd:
                    raise ValueError("command elements must be non-empty strings")
        if self.args is not None:
            if not isinstance(self.args, tuple):
                raise TypeError("args must be a tuple")
            for arg in self.args:
                if not isinstance(arg, str) or not arg:
                    raise ValueError("args elements must be non-empty strings")


@dataclass(frozen=True)
class ApprovedDeployment:
    name: str
    container: ApprovedContainer
    replicas: int
    config_map_name: str | None = None

    def __post_init__(self) -> None:
        _validate_dns_name(self.name)
        if not isinstance(self.container, ApprovedContainer):
            raise TypeError("container must be an ApprovedContainer")
        if not isinstance(self.replicas, int) or isinstance(self.replicas, bool):
            raise TypeError("replicas must be an integer")
        if self.replicas != 1:
            raise ValueError("replicas must be exactly 1")
        if self.config_map_name is not None:
            _validate_dns_name(self.config_map_name)


@dataclass(frozen=True)
class ApprovedService:
    name: str
    port: int
    target_port: int

    def __post_init__(self) -> None:
        _validate_dns_name(self.name)
        if not isinstance(self.port, int) or isinstance(self.port, bool):
            raise TypeError("port must be an integer")
        if not (1 <= self.port <= 65535):
            raise ValueError("port must be between 1 and 65535")
        if not isinstance(self.target_port, int) or isinstance(self.target_port, bool):
            raise TypeError("target_port must be an integer")
        if not (1 <= self.target_port <= 65535):
            raise ValueError("target_port must be between 1 and 65535")


@dataclass(frozen=True)
class ApprovedWorkloadBundle:
    identity: WorkloadBundleIdentity
    config_map: ApprovedConfigMap | None
    deployment: ApprovedDeployment
    service: ApprovedService

    def __post_init__(self) -> None:
        if not isinstance(self.identity, WorkloadBundleIdentity):
            raise TypeError("identity must be a WorkloadBundleIdentity")
        if self.config_map is not None:
            if not isinstance(self.config_map, ApprovedConfigMap):
                raise TypeError("config_map must be an ApprovedConfigMap or None")
        if not isinstance(self.deployment, ApprovedDeployment):
            raise TypeError("deployment must be an ApprovedDeployment")
        if not isinstance(self.service, ApprovedService):
            raise TypeError("service must be an ApprovedService")

        # Bundle Coherence
        if self.config_map is None:
            if self.deployment.config_map_name is not None:
                msg = "deployment config_map_name must be None when config_map is absent"
                raise ValueError(msg)
        else:
            if self.deployment.config_map_name != self.config_map.name:
                raise ValueError("deployment config_map_name must match config_map name")


class WorkloadResourceKind(str, Enum):
    CONFIG_MAP = "config_map"
    DEPLOYMENT = "deployment"
    SERVICE = "service"


class WorkloadInspectionOutcome(str, Enum):
    ABSENT = "absent"
    EXACT = "exact"
    MISMATCH = "mismatch"


@dataclass(frozen=True)
class WorkloadResourceInspection:
    namespace: RunNamespaceIdentity
    kind: WorkloadResourceKind
    name: str
    outcome: WorkloadInspectionOutcome
    detail: str

    def __post_init__(self) -> None:
        if not isinstance(self.namespace, RunNamespaceIdentity):
            raise TypeError("namespace must be a RunNamespaceIdentity")
        if not isinstance(self.kind, WorkloadResourceKind):
            raise TypeError("kind must be a WorkloadResourceKind")
        if not isinstance(self.name, str) or not self.name:
            raise ValueError("name must be a non-empty string")
        if not isinstance(self.outcome, WorkloadInspectionOutcome):
            raise TypeError("outcome must be a WorkloadInspectionOutcome")
        if not isinstance(self.detail, str) or not self.detail:
            raise ValueError("detail must be a non-empty string")


class KubernetesWorkloadError(Exception):
    pass


class WorkloadOwnershipRefused(KubernetesWorkloadError):
    pass


class WorkloadMismatchRefused(KubernetesWorkloadError):
    pass


class WorkloadOperationFailed(KubernetesWorkloadError):
    def __init__(
        self,
        operation: str,
        namespace: str,
        kind: WorkloadResourceKind,
        name: str,
        status_code: int | None,
        reason: str | None,
    ) -> None:
        self.operation = operation
        self.namespace = namespace
        self.kind = kind
        self.name = name
        self.status_code = status_code
        self.reason = reason
        super().__init__(
            f"Operation {operation} failed for {kind.value} {name} "
            f"in namespace {namespace}: {reason} ({status_code})"
        )


class _CoreV1Api(Protocol):
    def read_namespaced_config_map(self, name: str, namespace: str) -> Any: ...

    def create_namespaced_config_map(self, namespace: str, body: Any) -> Any: ...

    def read_namespaced_service(self, name: str, namespace: str) -> Any: ...

    def create_namespaced_service(self, namespace: str, body: Any) -> Any: ...


class _AppsV1Api(Protocol):
    def read_namespaced_deployment(self, name: str, namespace: str) -> Any: ...

    def create_namespaced_deployment(self, namespace: str, body: Any) -> Any: ...


def _build_config_map(
    namespace: RunNamespaceIdentity, ownership: NamespaceOwnership, approved: ApprovedConfigMap
) -> Any:
    from kubernetes.client import V1ConfigMap, V1ObjectMeta  # type: ignore

    labels = _ownership_labels(ownership)
    labels["brace.io/resource-kind"] = "config_map"

    return V1ConfigMap(
        metadata=V1ObjectMeta(
            name=approved.name,
            namespace=namespace.namespace,
            labels=labels,
        ),
        data=dict(approved.data),
    )


def _build_deployment(
    namespace: RunNamespaceIdentity, ownership: NamespaceOwnership, approved: ApprovedDeployment
) -> Any:
    from kubernetes.client import (
        V1Container,
        V1ContainerPort,
        V1Deployment,
        V1DeploymentSpec,
        V1EnvFromSource,
        V1LabelSelector,
        V1ObjectMeta,
        V1PodSpec,
        V1PodTemplateSpec,
    )

    dep_labels = _ownership_labels(ownership)
    dep_labels["brace.io/resource-kind"] = "deployment"

    pod_labels = _ownership_labels(ownership)
    pod_labels["brace.io/resource-kind"] = "deployment"
    pod_labels["brace.io/workload-instance"] = approved.name

    container = approved.container
    c_port = V1ContainerPort(container_port=container.container_port)

    v1_container = V1Container(
        name=container.name,
        image=container.image,
        ports=[c_port],
        command=list(container.command) if container.command else None,
        args=list(container.args) if container.args else None,
    )

    if approved.config_map_name:
        from kubernetes.client import V1ConfigMapEnvSource

        v1_container.env_from = [
            V1EnvFromSource(
                config_map_ref=V1ConfigMapEnvSource(name=approved.config_map_name)
            )
        ]

    pod_spec = V1PodSpec(
        containers=[v1_container],
    )

    selector = V1LabelSelector(match_labels={"brace.io/workload-instance": approved.name})

    spec = V1DeploymentSpec(
        replicas=1,
        selector=selector,
        template=V1PodTemplateSpec(
            metadata=V1ObjectMeta(labels=pod_labels),
            spec=pod_spec,
        ),
    )

    return V1Deployment(
        metadata=V1ObjectMeta(
            name=approved.name,
            namespace=namespace.namespace,
            labels=dep_labels,
        ),
        spec=spec,
    )


def _build_service(
    namespace: RunNamespaceIdentity,
    ownership: NamespaceOwnership,
    approved: ApprovedService,
    deployment_name: str,
) -> Any:
    from kubernetes.client import V1ObjectMeta, V1Service, V1ServicePort, V1ServiceSpec

    svc_labels = _ownership_labels(ownership)
    svc_labels["brace.io/resource-kind"] = "service"

    port = V1ServicePort(
        port=approved.port,
        target_port=approved.target_port,
    )

    spec = V1ServiceSpec(
        type="ClusterIP",
        ports=[port],
        selector={"brace.io/workload-instance": deployment_name},
    )

    return V1Service(
        metadata=V1ObjectMeta(
            name=approved.name,
            namespace=namespace.namespace,
            labels=svc_labels,
        ),
        spec=spec,
    )


def _validate_run_consistency_refused(
    identity: RunNamespaceIdentity, ownership: NamespaceOwnership
) -> None:
    if identity.run_id != ownership.run_id:
        raise WorkloadOwnershipRefused(
            f"Run ID mismatch: identity.run_id ({identity.run_id}) "
            f"!= ownership.run_id ({ownership.run_id})"
        )


class KubernetesWorkloadAdapter:
    def __init__(self, core_v1_api: _CoreV1Api, apps_v1_api: _AppsV1Api) -> None:
        self._core_api = core_v1_api
        self._apps_api = apps_v1_api

    def inspect_config_map(
        self,
        namespace: RunNamespaceIdentity,
        ownership: NamespaceOwnership,
        approved_config_map: ApprovedConfigMap,
    ) -> WorkloadResourceInspection:
        _validate_run_consistency_refused(namespace, ownership)
        try:
            cm = self._core_api.read_namespaced_config_map(
                name=approved_config_map.name, namespace=namespace.namespace
            )
        except ApiException as e:
            if e.status == 404:
                return WorkloadResourceInspection(
                    namespace,
                    WorkloadResourceKind.CONFIG_MAP,
                    approved_config_map.name,
                    WorkloadInspectionOutcome.ABSENT,
                    "ConfigMap not found",
                )
            raise WorkloadOperationFailed(
                "inspect_config_map",
                namespace.namespace,
                WorkloadResourceKind.CONFIG_MAP,
                approved_config_map.name,
                e.status,
                e.reason,
            ) from e

        labels = (cm.metadata.labels or {}) if cm.metadata else {}
        expected = _ownership_labels(ownership)
        expected["brace.io/resource-kind"] = "config_map"

        for k, v in expected.items():
            if labels.get(k) != v:
                return WorkloadResourceInspection(
                    namespace,
                    WorkloadResourceKind.CONFIG_MAP,
                    approved_config_map.name,
                    WorkloadInspectionOutcome.MISMATCH,
                    f"Label mismatch: expected {k}={v}",
                )

        actual_data = cm.data or {}
        if dict(actual_data) != dict(approved_config_map.data):
            return WorkloadResourceInspection(
                namespace,
                WorkloadResourceKind.CONFIG_MAP,
                approved_config_map.name,
                WorkloadInspectionOutcome.MISMATCH,
                "ConfigMap data mismatch",
            )

        return WorkloadResourceInspection(
            namespace,
            WorkloadResourceKind.CONFIG_MAP,
            approved_config_map.name,
            WorkloadInspectionOutcome.EXACT,
            "ConfigMap matches approved configuration exactly",
        )

    def inspect_deployment(
        self,
        namespace: RunNamespaceIdentity,
        ownership: NamespaceOwnership,
        approved_deployment: ApprovedDeployment,
    ) -> WorkloadResourceInspection:
        _validate_run_consistency_refused(namespace, ownership)
        try:
            dep = self._apps_api.read_namespaced_deployment(
                name=approved_deployment.name, namespace=namespace.namespace
            )
        except ApiException as e:
            if e.status == 404:
                return WorkloadResourceInspection(
                    namespace,
                    WorkloadResourceKind.DEPLOYMENT,
                    approved_deployment.name,
                    WorkloadInspectionOutcome.ABSENT,
                    "Deployment not found",
                )
            raise WorkloadOperationFailed(
                "inspect_deployment",
                namespace.namespace,
                WorkloadResourceKind.DEPLOYMENT,
                approved_deployment.name,
                e.status,
                e.reason,
            ) from e

        spec = dep.spec
        if not spec:
            return WorkloadResourceInspection(
                namespace,
                WorkloadResourceKind.DEPLOYMENT,
                approved_deployment.name,
                WorkloadInspectionOutcome.MISMATCH,
                "Missing deployment spec",
            )

        labels = (dep.metadata.labels or {}) if dep.metadata else {}
        expected_dep_labels = _ownership_labels(ownership)
        expected_dep_labels["brace.io/resource-kind"] = "deployment"

        for k, v in expected_dep_labels.items():
            if labels.get(k) != v:
                return WorkloadResourceInspection(
                    namespace,
                    WorkloadResourceKind.DEPLOYMENT,
                    approved_deployment.name,
                    WorkloadInspectionOutcome.MISMATCH,
                    f"Deployment label mismatch: expected {k}={v}",
                )

        if spec.replicas != 1:
            return WorkloadResourceInspection(
                namespace,
                WorkloadResourceKind.DEPLOYMENT,
                approved_deployment.name,
                WorkloadInspectionOutcome.MISMATCH,
                f"Replicas mismatch: expected 1, got {spec.replicas}",
            )

        expected_selector = {"brace.io/workload-instance": approved_deployment.name}
        actual_selector = (spec.selector.match_labels or {}) if spec.selector else {}
        if actual_selector != expected_selector:
            return WorkloadResourceInspection(
                namespace,
                WorkloadResourceKind.DEPLOYMENT,
                approved_deployment.name,
                WorkloadInspectionOutcome.MISMATCH,
                "Selector labels mismatch",
            )

        template = spec.template
        if not template:
            return WorkloadResourceInspection(
                namespace,
                WorkloadResourceKind.DEPLOYMENT,
                approved_deployment.name,
                WorkloadInspectionOutcome.MISMATCH,
                "Missing pod template spec",
            )

        template_labels = (template.metadata.labels or {}) if template.metadata else {}
        expected_pod_labels = _ownership_labels(ownership)
        expected_pod_labels["brace.io/resource-kind"] = "deployment"
        expected_pod_labels["brace.io/workload-instance"] = approved_deployment.name

        for k, v in expected_pod_labels.items():
            if template_labels.get(k) != v:
                return WorkloadResourceInspection(
                    namespace,
                    WorkloadResourceKind.DEPLOYMENT,
                    approved_deployment.name,
                    WorkloadInspectionOutcome.MISMATCH,
                    f"Pod template label mismatch: expected {k}={v}",
                )

        pod_spec = template.spec
        if not pod_spec or not pod_spec.containers:
            return WorkloadResourceInspection(
                namespace,
                WorkloadResourceKind.DEPLOYMENT,
                approved_deployment.name,
                WorkloadInspectionOutcome.MISMATCH,
                "Missing pod containers",
            )

        if len(pod_spec.containers) != 1:
            return WorkloadResourceInspection(
                namespace,
                WorkloadResourceKind.DEPLOYMENT,
                approved_deployment.name,
                WorkloadInspectionOutcome.MISMATCH,
                f"Expected exactly one container, got {len(pod_spec.containers)}",
            )

        container = pod_spec.containers[0]
        approved_c = approved_deployment.container

        if container.name != approved_c.name:
            return WorkloadResourceInspection(
                namespace,
                WorkloadResourceKind.DEPLOYMENT,
                approved_deployment.name,
                WorkloadInspectionOutcome.MISMATCH,
                f"Container name mismatch: expected {approved_c.name}, got {container.name}",
            )

        if container.image != approved_c.image:
            return WorkloadResourceInspection(
                namespace,
                WorkloadResourceKind.DEPLOYMENT,
                approved_deployment.name,
                WorkloadInspectionOutcome.MISMATCH,
                f"Container image mismatch: expected {approved_c.image}, got {container.image}",
            )

        ports = container.ports or []
        if len(ports) != 1 or ports[0].container_port != approved_c.container_port:
            return WorkloadResourceInspection(
                namespace,
                WorkloadResourceKind.DEPLOYMENT,
                approved_deployment.name,
                WorkloadInspectionOutcome.MISMATCH,
                "Container ports mismatch",
            )

        actual_command = tuple(container.command) if container.command else None
        if actual_command != approved_c.command:
            return WorkloadResourceInspection(
                namespace,
                WorkloadResourceKind.DEPLOYMENT,
                approved_deployment.name,
                WorkloadInspectionOutcome.MISMATCH,
                "Container command mismatch",
            )

        actual_args = tuple(container.args) if container.args else None
        if actual_args != approved_c.args:
            return WorkloadResourceInspection(
                namespace,
                WorkloadResourceKind.DEPLOYMENT,
                approved_deployment.name,
                WorkloadInspectionOutcome.MISMATCH,
                "Container args mismatch",
            )

        # ConfigMap reference checks
        env_from = container.env_from or []
        if approved_deployment.config_map_name:
            if len(env_from) != 1:
                return WorkloadResourceInspection(
                    namespace,
                    WorkloadResourceKind.DEPLOYMENT,
                    approved_deployment.name,
                    WorkloadInspectionOutcome.MISMATCH,
                    "Expected exactly one envFrom reference",
                )
            cm_ref = env_from[0].config_map_ref
            if not cm_ref or cm_ref.name != approved_deployment.config_map_name:
                return WorkloadResourceInspection(
                    namespace,
                    WorkloadResourceKind.DEPLOYMENT,
                    approved_deployment.name,
                    WorkloadInspectionOutcome.MISMATCH,
                    "envFrom configMapRef name mismatch",
                )
        else:
            if env_from:
                return WorkloadResourceInspection(
                    namespace,
                    WorkloadResourceKind.DEPLOYMENT,
                    approved_deployment.name,
                    WorkloadInspectionOutcome.MISMATCH,
                    "Deployment has unexpected envFrom configMapRef reference",
                )

        # Security/Host settings checks
        if (
            pod_spec.host_network
            or pod_spec.host_pid
            or pod_spec.host_ipc
            or pod_spec.volumes
        ):
            return WorkloadResourceInspection(
                namespace,
                WorkloadResourceKind.DEPLOYMENT,
                approved_deployment.name,
                WorkloadInspectionOutcome.MISMATCH,
                "Deployment pod spec uses prohibited host or volume settings",
            )

        sc = container.security_context
        if sc and sc.privileged:
            return WorkloadResourceInspection(
                namespace,
                WorkloadResourceKind.DEPLOYMENT,
                approved_deployment.name,
                WorkloadInspectionOutcome.MISMATCH,
                "Deployment uses prohibited privileged container setting",
            )

        return WorkloadResourceInspection(
            namespace,
            WorkloadResourceKind.DEPLOYMENT,
            approved_deployment.name,
            WorkloadInspectionOutcome.EXACT,
            "Deployment matches approved configuration exactly",
        )

    def inspect_service(
        self,
        namespace: RunNamespaceIdentity,
        ownership: NamespaceOwnership,
        approved_service: ApprovedService,
        deployment_name: str,
    ) -> WorkloadResourceInspection:
        _validate_run_consistency_refused(namespace, ownership)
        try:
            svc = self._core_api.read_namespaced_service(
                name=approved_service.name, namespace=namespace.namespace
            )
        except ApiException as e:
            if e.status == 404:
                return WorkloadResourceInspection(
                    namespace,
                    WorkloadResourceKind.SERVICE,
                    approved_service.name,
                    WorkloadInspectionOutcome.ABSENT,
                    "Service not found",
                )
            raise WorkloadOperationFailed(
                "inspect_service",
                namespace.namespace,
                WorkloadResourceKind.SERVICE,
                approved_service.name,
                e.status,
                e.reason,
            ) from e

        spec = svc.spec
        if not spec:
            return WorkloadResourceInspection(
                namespace,
                WorkloadResourceKind.SERVICE,
                approved_service.name,
                WorkloadInspectionOutcome.MISMATCH,
                "Missing service spec",
            )

        labels = (svc.metadata.labels or {}) if svc.metadata else {}
        expected_svc_labels = _ownership_labels(ownership)
        expected_svc_labels["brace.io/resource-kind"] = "service"

        for k, v in expected_svc_labels.items():
            if labels.get(k) != v:
                return WorkloadResourceInspection(
                    namespace,
                    WorkloadResourceKind.SERVICE,
                    approved_service.name,
                    WorkloadInspectionOutcome.MISMATCH,
                    f"Service label mismatch: expected {k}={v}",
                )

        if spec.type != "ClusterIP":
            return WorkloadResourceInspection(
                namespace,
                WorkloadResourceKind.SERVICE,
                approved_service.name,
                WorkloadInspectionOutcome.MISMATCH,
                f"Service type mismatch: expected ClusterIP, got {spec.type}",
            )

        expected_selector = {"brace.io/workload-instance": deployment_name}
        if spec.selector != expected_selector:
            return WorkloadResourceInspection(
                namespace,
                WorkloadResourceKind.SERVICE,
                approved_service.name,
                WorkloadInspectionOutcome.MISMATCH,
                "Service selector mismatch",
            )

        ports = spec.ports or []
        if len(ports) != 1:
            return WorkloadResourceInspection(
                namespace,
                WorkloadResourceKind.SERVICE,
                approved_service.name,
                WorkloadInspectionOutcome.MISMATCH,
                f"Expected exactly one service port, got {len(ports)}",
            )

        svc_port = ports[0]
        if svc_port.port != approved_service.port:
            return WorkloadResourceInspection(
                namespace,
                WorkloadResourceKind.SERVICE,
                approved_service.name,
                WorkloadInspectionOutcome.MISMATCH,
                f"Port mismatch: expected {approved_service.port}, got {svc_port.port}",
            )

        actual_tp = svc_port.target_port
        if actual_tp != approved_service.target_port:
            return WorkloadResourceInspection(
                namespace,
                WorkloadResourceKind.SERVICE,
                approved_service.name,
                WorkloadInspectionOutcome.MISMATCH,
                f"TargetPort mismatch: expected {approved_service.target_port}, got {actual_tp}",
            )

        return WorkloadResourceInspection(
            namespace,
            WorkloadResourceKind.SERVICE,
            approved_service.name,
            WorkloadInspectionOutcome.EXACT,
            "Service matches approved configuration exactly",
        )

    def apply(
        self,
        namespace: RunNamespaceIdentity,
        ownership: NamespaceOwnership,
        bundle: ApprovedWorkloadBundle,
    ) -> None:
        _validate_run_consistency_refused(namespace, ownership)

        # 1. Preflight inspection of all resources
        cm_insp = None
        if bundle.config_map:
            cm_insp = self.inspect_config_map(namespace, ownership, bundle.config_map)
            if cm_insp.outcome == WorkloadInspectionOutcome.MISMATCH:
                raise WorkloadMismatchRefused(f"ConfigMap mismatch: {cm_insp.detail}")

        dep_insp = self.inspect_deployment(namespace, ownership, bundle.deployment)
        if dep_insp.outcome == WorkloadInspectionOutcome.MISMATCH:
            raise WorkloadMismatchRefused(f"Deployment mismatch: {dep_insp.detail}")

        svc_insp = self.inspect_service(
            namespace, ownership, bundle.service, bundle.deployment.name
        )
        if svc_insp.outcome == WorkloadInspectionOutcome.MISMATCH:
            raise WorkloadMismatchRefused(f"Service mismatch: {svc_insp.detail}")

        # 2. Ordered creation
        # ConfigMap
        if bundle.config_map and cm_insp and cm_insp.outcome == WorkloadInspectionOutcome.ABSENT:
            cm_body = _build_config_map(namespace, ownership, bundle.config_map)
            try:
                self._core_api.create_namespaced_config_map(
                    namespace=namespace.namespace, body=cm_body
                )
            except ApiException as e:
                if e.status == 409:
                    re_insp = self.inspect_config_map(namespace, ownership, bundle.config_map)
                    if re_insp.outcome == WorkloadInspectionOutcome.EXACT:
                        raise WorkloadOwnershipRefused(
                            f"ConfigMap {bundle.config_map.name} EXACT but post-conflict refused"
                        )
                    elif re_insp.outcome == WorkloadInspectionOutcome.MISMATCH:
                        msg = (
                            f"ConfigMap {bundle.config_map.name} "
                            f"MISMATCH post-conflict: {re_insp.detail}"
                        )
                        raise WorkloadMismatchRefused(msg)
                    else:
                        raise WorkloadOperationFailed(
                            "create_namespaced_config_map",
                            namespace.namespace,
                            WorkloadResourceKind.CONFIG_MAP,
                            bundle.config_map.name,
                            e.status,
                            f"Conflict reinspection outcome: {re_insp.outcome.value}",
                        ) from e
                raise WorkloadOperationFailed(
                    "create_namespaced_config_map",
                    namespace.namespace,
                    WorkloadResourceKind.CONFIG_MAP,
                    bundle.config_map.name,
                    e.status,
                    e.reason,
                ) from e

        # Deployment
        if dep_insp.outcome == WorkloadInspectionOutcome.ABSENT:
            dep_body = _build_deployment(namespace, ownership, bundle.deployment)
            try:
                self._apps_api.create_namespaced_deployment(
                    namespace=namespace.namespace, body=dep_body
                )
            except ApiException as e:
                if e.status == 409:
                    re_insp = self.inspect_deployment(namespace, ownership, bundle.deployment)
                    if re_insp.outcome == WorkloadInspectionOutcome.EXACT:
                        raise WorkloadOwnershipRefused(
                            f"Deployment {bundle.deployment.name} EXACT but post-conflict refused"
                        )
                    elif re_insp.outcome == WorkloadInspectionOutcome.MISMATCH:
                        msg = (
                            f"Deployment {bundle.deployment.name} "
                            f"MISMATCH post-conflict: {re_insp.detail}"
                        )
                        raise WorkloadMismatchRefused(msg)
                    else:
                        raise WorkloadOperationFailed(
                            "create_namespaced_deployment",
                            namespace.namespace,
                            WorkloadResourceKind.DEPLOYMENT,
                            bundle.deployment.name,
                            e.status,
                            f"Conflict reinspection outcome: {re_insp.outcome.value}",
                        ) from e
                raise WorkloadOperationFailed(
                    "create_namespaced_deployment",
                    namespace.namespace,
                    WorkloadResourceKind.DEPLOYMENT,
                    bundle.deployment.name,
                    e.status,
                    e.reason,
                ) from e

        # Service
        if svc_insp.outcome == WorkloadInspectionOutcome.ABSENT:
            svc_body = _build_service(
                namespace, ownership, bundle.service, bundle.deployment.name
            )
            try:
                self._core_api.create_namespaced_service(
                    namespace=namespace.namespace, body=svc_body
                )
            except ApiException as e:
                if e.status == 409:
                    re_insp = self.inspect_service(
                        namespace, ownership, bundle.service, bundle.deployment.name
                    )
                    if re_insp.outcome == WorkloadInspectionOutcome.EXACT:
                        raise WorkloadOwnershipRefused(
                            f"Service {bundle.service.name} EXACT but post-conflict refused"
                        )
                    elif re_insp.outcome == WorkloadInspectionOutcome.MISMATCH:
                        msg = (
                            f"Service {bundle.service.name} "
                            f"MISMATCH post-conflict: {re_insp.detail}"
                        )
                        raise WorkloadMismatchRefused(msg)
                    else:
                        raise WorkloadOperationFailed(
                            "create_namespaced_service",
                            namespace.namespace,
                            WorkloadResourceKind.SERVICE,
                            bundle.service.name,
                            e.status,
                            f"Conflict reinspection outcome: {re_insp.outcome.value}",
                        ) from e
                raise WorkloadOperationFailed(
                    "create_namespaced_service",
                    namespace.namespace,
                    WorkloadResourceKind.SERVICE,
                    bundle.service.name,
                    e.status,
                    e.reason,
                ) from e
