"""Committed guard tests for the public composition entry point.

``run_runtime_owned_verification`` must construct the real coordinator with
the exact ``evaluator`` object passed by the caller, reach the real reasoning
boundary, and preserve cleanup. External Kubernetes configuration loading,
Kubernetes API client construction, Kubernetes I/O, HTTP I/O, and the
proprietary evaluator are replaced with doubles; everything else is the real
production chain. No Kubernetes cluster is contacted.

Cross-field semantic predicates exercised by the composition request and
fixture (each enforced by real production validation):

- recovery_event.workload_id == protected_workload.workload_id
  (BoundaryEvaluationRequest.__post_init__; coordinator _validate_input)
- namespace.run_id == run_id
  (coordinator _validate_input)
- ownership.run_id == run_id
  (coordinator _validate_input)
- ownership.workload_id == protected_workload.workload_id
  (coordinator _validate_input)
- ownership.bundle == workload_bundle.identity
  (coordinator _validate_input)
- service.target_port == deployment.container.container_port
  (fixture coherence: both 8080; the target the Service routes to is the
  container port the Deployment exposes)
- replicas == 1
  (ApprovedDeployment.__post_init__)
- image uses @sha256: followed by exactly 64 hexadecimal characters
  (ApprovedContainer._validate_image)
- every projected observation event ID == recovery_event.event_id
  (projection from event.event_id; BoundaryEvaluationRequest.__post_init__)
- every projected observation workload ID == protected_workload.workload_id
  (projection from workload.workload_id; BoundaryEvaluationRequest.__post_init__)

The same production-valid fixture (fixed strings, no parsed or
authority-validated substitutions) is used in every test.
"""

import ast
import datetime
import inspect
import pathlib
import uuid
from types import SimpleNamespace

import pytest
from kubernetes.client.exceptions import ApiException

import brace._runtime_owned_composition as comp
from brace._execution import (
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
    VerificationCoordinationFailed,
    VerificationCoordinationResult,
    VerificationPhase,
)
from brace._workload import ProtectedWorkload
from brace.reasoning.boundary import (
    ReasoningCoreNotProvided,
    RecoverabilityEvaluator,
)

_SOURCE = (
    pathlib.Path(__file__).resolve().parents[1]
    / "src"
    / "brace"
    / "_runtime_owned_composition.py"
).read_text(encoding="utf-8")

_APPROVED_PUBLIC_MODULES = frozenset(
    (
        "brace",
        "brace._evidence_kind",
        "brace._evidence_semantic",
        "brace._execution",
        "brace._http_client_transport",
        "brace._kubernetes_namespace",
        "brace._kubernetes_readiness",
        "brace._kubernetes_responsiveness",
        "brace._kubernetes_workload",
        "brace._observed_content",
        "brace._operational_projection",
        "brace._provenance",
        "brace._recoverability_knowledge",
        "brace._recoverability_subject",
        "brace._recoverability_truth_standing_value",
        "brace._recovery_event",
        "brace._recovery_observation",
        "brace._runtime_observation_convergence",
        "brace._runtime_owned_composition",
        "brace._runtime_owned_request",
        "brace._runtime_owned_verification",
        "brace._workload",
        "brace.reasoning",
        "brace.reasoning.boundary",
    )
)


class _Fixture:
    """Exact production-valid fixture shared by every test."""

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
        self.container = ApprovedContainer(
            name="app",
            image="registry.example.com/brace-demo@sha256:" + ("a" * 64),
            container_port=8080,
        )
        self.deployment = ApprovedDeployment(
            name="brace-demo", container=self.container, replicas=1
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

    def run_args(self):
        return (
            self.run_id,
            self.evaluation_id,
            self.workload,
            self.event,
            self.namespace,
            self.ownership,
            self.bundle,
        )


def _knowledge(workload_id: uuid.UUID) -> RecoverabilityKnowledge:
    return RecoverabilityKnowledge(
        knowledge_id=uuid.uuid4(),
        subject=RecoverabilitySubject(
            subject_id=RecoverabilitySubjectId(value=workload_id)
        ),
        standing=RecoverabilityTruthStandingValue.RECOVERABILITY_AFFIRMED,
        established_at=datetime.datetime(
            2026, 8, 9, 12, 2, tzinfo=datetime.timezone.utc
        ),
    )


class _RecordingEvaluator:
    """Public test evaluator; returns a fixed RecoverabilityKnowledge."""

    def __init__(self, knowledge: RecoverabilityKnowledge) -> None:
        self.knowledge = knowledge
        self.requests: list[BoundaryEvaluationRequest] = []

    def evaluate(self, request: BoundaryEvaluationRequest) -> RecoverabilityKnowledge:
        self.requests.append(request)
        return self.knowledge


class _FakeCoreV1Api:
    instances: list["_FakeCoreV1Api"] = []

    def __init__(self) -> None:
        _FakeCoreV1Api.instances.append(self)
        self.namespace_labels = None
        self.service_bodies = {}
        self.create_namespace_calls = 0
        self.delete_namespace_calls = 0

    def read_namespace(self, name):
        if self.namespace_labels is None:
            raise ApiException(status=404, reason="Not Found")
        return SimpleNamespace(
            metadata=SimpleNamespace(labels=self.namespace_labels)
        )

    def create_namespace(self, body):
        self.create_namespace_calls += 1
        self.namespace_labels = body.metadata.labels

    def delete_namespace(self, name):
        self.delete_namespace_calls += 1

    def read_namespaced_service(self, name, namespace):
        if name not in self.service_bodies:
            raise ApiException(status=404, reason="Not Found")
        body = self.service_bodies[name]
        return SimpleNamespace(
            metadata=SimpleNamespace(name=body.metadata.name, labels=body.metadata.labels),
            spec=SimpleNamespace(
                type=body.spec.type,
                selector=body.spec.selector,
                ports=body.spec.ports,
            ),
        )

    def create_namespaced_service(self, namespace, body):
        self.service_bodies[body.metadata.name] = body


class _FakeAppsV1Api:
    instances: list["_FakeAppsV1Api"] = []

    def __init__(self) -> None:
        _FakeAppsV1Api.instances.append(self)
        self.deployment_bodies = {}

    def read_namespaced_deployment(self, name, namespace):
        if name not in self.deployment_bodies:
            raise ApiException(status=404, reason="Not Found")
        body = self.deployment_bodies[name]
        return SimpleNamespace(
            metadata=SimpleNamespace(
                name=body.metadata.name,
                namespace=body.metadata.namespace,
                generation=1,
                labels=body.metadata.labels,
            ),
            spec=SimpleNamespace(
                replicas=body.spec.replicas,
                selector=body.spec.selector,
                template=body.spec.template,
            ),
            status=SimpleNamespace(
                observed_generation=1,
                updated_replicas=1,
                ready_replicas=1,
                available_replicas=1,
                unavailable_replicas=0,
            ),
        )

    def create_namespaced_deployment(self, namespace, body):
        self.deployment_bodies[body.metadata.name] = body


class _FakeTransport:
    instances: list["_FakeTransport"] = []

    def __init__(self) -> None:
        _FakeTransport.instances.append(self)

    def get(self, request, *, follow_redirects):
        return SimpleNamespace(status_code=200)


@pytest.fixture
def cluster(monkeypatch):
    _FakeCoreV1Api.instances.clear()
    _FakeAppsV1Api.instances.clear()
    _FakeTransport.instances.clear()
    monkeypatch.setattr(comp.config, "load_incluster_config", lambda: None)
    monkeypatch.setattr(comp.client, "CoreV1Api", _FakeCoreV1Api)
    monkeypatch.setattr(comp.client, "AppsV1Api", _FakeAppsV1Api)
    monkeypatch.setattr(comp, "_HttpClientTransport", _FakeTransport)


def _phases(result) -> list[tuple[VerificationPhase, bool]]:
    return [(p.phase, p.succeeded) for p in result.phase_results]


def _no_private_evaluator_and_no_excluded_imports() -> None:
    tree = ast.parse(_SOURCE, filename="_runtime_owned_composition.py")
    brace_imports = set()
    imports_evaluator_from_boundary = False
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            if node.module.startswith("brace"):
                brace_imports.add(node.module)
            if node.module == "brace.reasoning.boundary":
                if any(a.name == "RecoverabilityEvaluator" for a in node.names):
                    imports_evaluator_from_boundary = True
    assert imports_evaluator_from_boundary, (
        "composition must import RecoverabilityEvaluator from brace.reasoning.boundary"
    )
    for mod in brace_imports:
        assert mod in _APPROVED_PUBLIC_MODULES, f"unexpected package import: {mod}"


# ---------------------------------------------------------------------------
# 1. Builder threads exact evaluator
# ---------------------------------------------------------------------------


def test_builder_threads_exact_evaluator(cluster):
    fixture = _Fixture()
    evaluator = _RecordingEvaluator(_knowledge(fixture.workload_id))

    coordinator = comp._build_runtime_coordinator(evaluator)

    assert isinstance(coordinator, RuntimeOwnedVerificationCoordinator)
    assert coordinator._evaluator is evaluator
    _no_private_evaluator_and_no_excluded_imports()

    defaulted = comp._build_runtime_coordinator()
    assert defaulted._evaluator is None


# ---------------------------------------------------------------------------
# 2. Public composition succeeds with injected evaluator
# ---------------------------------------------------------------------------

_SUCCESS_PHASES = [
    (VerificationPhase.NAMESPACE_CREATE, True),
    (VerificationPhase.WORKLOAD_APPLY, True),
    (VerificationPhase.READINESS_OBSERVE, True),
    (VerificationPhase.RESPONSIVENESS_OBSERVE, True),
    (VerificationPhase.OBSERVATION_PROJECT, True),
    (VerificationPhase.REASONING_BOUNDARY, True),
    (VerificationPhase.NAMESPACE_CLEANUP, True),
]


def test_public_composition_succeeds_with_injected_evaluator(cluster):
    fixture = _Fixture()
    knowledge = _knowledge(fixture.workload_id)
    evaluator = _RecordingEvaluator(knowledge)

    result = comp.run_runtime_owned_verification(
        *fixture.run_args(),
        evaluator=evaluator,
    )

    assert isinstance(result, VerificationCoordinationResult)
    assert result.run_id is fixture.run_id
    assert result.evaluation_id is fixture.evaluation_id
    assert result.knowledge is knowledge
    assert _phases(result) == _SUCCESS_PHASES

    assert len(evaluator.requests) == 1
    request = evaluator.requests[0]
    assert isinstance(request, BoundaryEvaluationRequest)
    assert request.run_id is fixture.run_id
    assert request.evaluation_id is fixture.evaluation_id
    assert request.protected_workload is fixture.workload
    assert request.recovery_event is fixture.event
    assert request.observations == result.observations

    assert len(result.observations) == 2
    kinds = sorted(o.content.kind.value for o in result.observations)
    assert kinds == ["application_responsiveness", "kubernetes_readiness"]

    core = _FakeCoreV1Api.instances[-1]
    apps = _FakeAppsV1Api.instances[-1]
    assert core.create_namespace_calls == 1
    assert core.delete_namespace_calls == 1
    assert len(apps.deployment_bodies) == 1
    assert len(core.service_bodies) == 1
    assert len(_FakeTransport.instances) == 1


# ---------------------------------------------------------------------------
# 3. Public composition without evaluator reaches the real boundary
# ---------------------------------------------------------------------------


def test_public_composition_without_evaluator_reaches_real_boundary(cluster):
    fixture = _Fixture()

    with pytest.raises(VerificationCoordinationFailed) as exc_info:
        comp.run_runtime_owned_verification(*fixture.run_args())

    failure = exc_info.value
    assert failure.failed_phase is VerificationPhase.REASONING_BOUNDARY
    assert isinstance(failure.primary_failure, ReasoningCoreNotProvided)

    pairs = [(p.phase, p.succeeded) for p in failure.phase_results]
    assert pairs == [
        (VerificationPhase.NAMESPACE_CREATE, True),
        (VerificationPhase.WORKLOAD_APPLY, True),
        (VerificationPhase.READINESS_OBSERVE, True),
        (VerificationPhase.RESPONSIVENESS_OBSERVE, True),
        (VerificationPhase.OBSERVATION_PROJECT, True),
        (VerificationPhase.REASONING_BOUNDARY, False),
        (VerificationPhase.NAMESPACE_CLEANUP, True),
    ]
    assert pairs.count((VerificationPhase.REASONING_BOUNDARY, False)) == 1

    core = _FakeCoreV1Api.instances[-1]
    assert core.create_namespace_calls == 1
    assert core.delete_namespace_calls == 1


# ---------------------------------------------------------------------------
# 4. Exact evaluator forwarding
# ---------------------------------------------------------------------------


def test_exact_evaluator_forwarding(cluster, monkeypatch):
    fixture = _Fixture()
    evaluator = _RecordingEvaluator(_knowledge(fixture.workload_id))

    real_builder = comp._build_runtime_coordinator
    captured: dict[str, object] = {}

    def observing_builder(evaluator=None):
        captured["evaluator"] = evaluator
        return real_builder(evaluator)

    monkeypatch.setattr(comp, "_build_runtime_coordinator", observing_builder)

    result = comp.run_runtime_owned_verification(
        *fixture.run_args(),
        evaluator=evaluator,
    )

    assert captured["evaluator"] is evaluator
    coordinator = real_builder(evaluator)
    assert coordinator._evaluator is evaluator
    assert result.knowledge is evaluator.knowledge
    assert len(evaluator.requests) == 1
    assert evaluator.requests[0].observations == result.observations


# ---------------------------------------------------------------------------
# 5. Signature guard
# ---------------------------------------------------------------------------


def test_signature_guard():
    run_params = list(inspect.signature(comp.run_runtime_owned_verification).parameters.values())
    assert run_params[-1].name == "evaluator"
    assert run_params[-1].default is None

    builder_params = list(inspect.signature(comp._build_runtime_coordinator).parameters.values())
    assert any(p.name == "evaluator" for p in builder_params)
    evaluator_param = next(p for p in builder_params if p.name == "evaluator")
    assert evaluator_param.default is None
