"""Tests for public demonstration entrypoint (brace.demo)."""

import io
import subprocess
import sys
import uuid
from unittest.mock import patch

import pytest

from brace._recoverability_knowledge import RecoverabilityKnowledge
from brace._recoverability_subject import (
    RecoverabilitySubject,
    RecoverabilitySubjectId,
)
from brace._recoverability_truth_standing_value import (
    RecoverabilityTruthStandingValue,
)
from brace._runtime_owned_verification import (
    BoundaryEvaluationRequest,
    VerificationCoordinationResult,
)
import brace.demo
from brace.demo import DemoRecoverabilityEvaluator, run_demo


def test_demo_run_default_output_and_result():
    buffer = io.StringIO()
    result = run_demo(out=buffer)

    assert isinstance(result, VerificationCoordinationResult)
    assert result.knowledge is not None
    assert (
        result.knowledge.standing
        == RecoverabilityTruthStandingValue.RECOVERABILITY_AFFIRMED
    )

    output = buffer.getvalue()
    expected_markers = [
        "[PASS] Request and identity validation",
        "[PASS] Namespace lifecycle orchestration through test double",
        "[PASS] Workload application through test double",
        "[PASS] Simulated readiness observation",
        "[PASS] Simulated HTTP responsiveness observation",
        "[PASS] Operational observation projection",
        "[PASS] External evaluator protocol handoff",
        "[PASS] Cleanup orchestration through test double",
    ]
    for marker in expected_markers:
        assert output.count(marker) == 1

    limitations = [
        "This demonstration uses in-process test doubles.",
        "No live Kubernetes cluster was contacted.",
        "No backup restoration was performed.",
        "No AWS resources were created.",
        "No network request was made.",
    ]
    for limitation in limitations:
        assert limitation in output

    assert (
        "Affirmed standing returned by the public demo evaluator test double."
        in output
    )
    assert "No production recoverability decision was made." in output


def test_demo_custom_evaluator_injection_and_invariants():
    class CustomAffirmedEvaluator:
        def __init__(self):
            self.received_request: BoundaryEvaluationRequest | None = None

        def evaluate(
            self, request: BoundaryEvaluationRequest
        ) -> RecoverabilityKnowledge:
            self.received_request = request
            return RecoverabilityKnowledge(
                knowledge_id=uuid.uuid4(),
                subject=RecoverabilitySubject(
                    subject_id=RecoverabilitySubjectId(
                        value=request.protected_workload.workload_id
                    )
                ),
                standing=RecoverabilityTruthStandingValue.RECOVERABILITY_AFFIRMED,
                established_at=request.observations[0].occurrence_time,
            )

    evaluator = CustomAffirmedEvaluator()

    created_ns = []
    deleted_ns = []
    applied_wl = []
    observed_readiness = []
    observed_resp = []

    orig_ns = brace.demo._DemoNamespaceAdapter
    orig_wl = brace.demo._DemoWorkloadAdapter
    orig_readiness = brace.demo._DemoReadinessObserver
    orig_resp = brace.demo._DemoResponsivenessObserver

    class MonitoredNS(orig_ns):
        def create(self, namespace, ownership):
            created_ns.append((namespace, ownership))
            super().create(namespace, ownership)

        def delete_owned(self, namespace, ownership):
            deleted_ns.append((namespace, ownership))
            super().delete_owned(namespace, ownership)

    class MonitoredWL(orig_wl):
        def apply(self, namespace, ownership, bundle):
            applied_wl.append((namespace, ownership, bundle))
            super().apply(namespace, ownership, bundle)

    class MonitoredReadiness(orig_readiness):
        def observe(self, namespace, ownership, deployment):
            observed_readiness.append((namespace, ownership, deployment))
            return super().observe(namespace, ownership, deployment)

    class MonitoredResp(orig_resp):
        def observe(self, namespace, ownership, service):
            observed_resp.append((namespace, ownership, service))
            return super().observe(namespace, ownership, service)

    with (
        patch("brace.demo._DemoNamespaceAdapter", MonitoredNS),
        patch("brace.demo._DemoWorkloadAdapter", MonitoredWL),
        patch("brace.demo._DemoReadinessObserver", MonitoredReadiness),
        patch("brace.demo._DemoResponsivenessObserver", MonitoredResp),
        patch("brace._kubernetes_namespace.KubernetesNamespaceAdapter") as mock_prod_ns,
        patch("brace._kubernetes_workload.KubernetesWorkloadAdapter") as mock_prod_wl,
        patch("brace._kubernetes_readiness.KubernetesReadinessObserver") as mock_prod_ro,
        patch("brace._kubernetes_responsiveness.ApplicationResponsivenessObserver") as mock_prod_resp,
        patch("brace._http_client_transport._HttpClientTransport") as mock_prod_tr,
    ):
        buffer = io.StringIO()
        result = run_demo(evaluator=evaluator, out=buffer)

    # 1. Custom evaluator received boundary request and subject continuity is preserved
    assert evaluator.received_request is not None
    req = evaluator.received_request
    assert isinstance(req, BoundaryEvaluationRequest)
    assert len(req.observations) == 2
    assert result.knowledge is not None
    assert result.knowledge.subject.subject_id.value == req.protected_workload.workload_id
    assert (
        result.knowledge.standing
        == RecoverabilityTruthStandingValue.RECOVERABILITY_AFFIRMED
    )

    # 2. In-process test doubles invoked exactly once
    assert len(created_ns) == 1
    assert len(deleted_ns) == 1
    assert len(applied_wl) == 1
    assert len(observed_readiness) == 1
    assert len(observed_resp) == 1

    # 3. No production adapters or transports instantiated
    mock_prod_ns.assert_not_called()
    mock_prod_wl.assert_not_called()
    mock_prod_ro.assert_not_called()
    mock_prod_resp.assert_not_called()
    mock_prod_tr.assert_not_called()


def test_demo_cli_invocation_and_clean_import():
    # Test CLI invocation exits 0 with expected output
    proc = subprocess.run(
        [sys.executable, "-m", "brace.demo"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert proc.returncode == 0
    assert "Brace Platform v1 Public Demonstration" in proc.stdout
    assert "[PASS] External evaluator protocol handoff" in proc.stdout
    assert "No AWS resources were created." in proc.stdout
    assert "No network request was made." in proc.stdout

    # Test importing brace.demo produces no stdout/stderr or side-effects
    import_proc = subprocess.run(
        [sys.executable, "-c", "import brace.demo"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert import_proc.returncode == 0
    assert import_proc.stdout == ""
    assert import_proc.stderr == ""
