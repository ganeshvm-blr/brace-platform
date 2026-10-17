import datetime
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import uuid
from unittest.mock import MagicMock, patch

import pytest
import shutil

_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import scripts.live_demo as live_demo
import workloads.verifier.verifier as verifier
execute_verifier = verifier.execute_verifier
from brace._runtime_owned_verification import (
    VerificationCoordinationResult,
    ExecutionPhaseResult,
    VerificationPhase,
)
from brace._recovery_observation import RecoveryObservation
from brace._evidence_kind import EvidenceKind
from brace._evidence_semantic import EvidenceSemantic
from brace._observed_content import ObservedContent
from brace._provenance import Provenance
from brace._recoverability_knowledge import RecoverabilityKnowledge
from brace._recoverability_subject import RecoverabilitySubject, RecoverabilitySubjectId
from brace._recoverability_truth_standing_value import RecoverabilityTruthStandingValue


def test_verifier_packaging_and_job_manifest_render():
    k8s_dir = _REPO_ROOT / "k8s" / "verifier"
    job_template_file = k8s_dir / "job-template.yaml"
    rbac_file = k8s_dir / "rbac.yaml"

    assert job_template_file.is_file()
    assert rbac_file.is_file()

    template = job_template_file.read_text(encoding="utf-8")
    assert "${VERIFIER_IMAGE}" in template
    assert "/app/verifier.py" in template
    assert "${JOB_NAME}" in template
    assert "${VERIFIER_SERVICE_ACCOUNT}" in template
    assert "${RUN_ID}" in template
    assert "output-volume" not in template
    assert "--output-file" not in template

    test_image = "ghcr.io/test/verifier@sha256:" + ("a" * 64)
    rendered = (
        template
        .replace("${VERIFIER_IMAGE}", test_image)
        .replace("${JOB_NAME}", "job-123")
        .replace("${POD_NAME}", "pod-123")
        .replace("${VERIFIER_SERVICE_ACCOUNT}", "sa-123")
        .replace("${CONFIGMAP_NAME}", "cm-123")
        .replace("${RUN_ID}", "run-123")
    )
    assert test_image in rendered
    assert "${VERIFIER_IMAGE}" not in rendered
    assert "job-123" in rendered
    assert "run-123" in rendered

    rbac = rbac_file.read_text(encoding="utf-8")
    assert "${VERIFIER_SERVICE_ACCOUNT}" in rbac
    assert "${VERIFIER_CLUSTER_ROLE}" in rbac
    assert "${RUN_ID}" in rbac
    assert "ClusterRole" in rbac
    assert "ServiceAccount" in rbac


def test_command_stdin_input_piping():
    with patch("subprocess.run") as mock_sub:
        mock_sub.return_value = subprocess.CompletedProcess(
            args=["kubectl", "apply", "-f", "-"],
            returncode=0,
            stdout="configured",
            stderr="",
        )
        res = live_demo.run_command(
            ["kubectl", "apply", "-f", "-"],
            input_data="apiVersion: v1\nkind: ConfigMap",
        )
        assert res.returncode == 0
        assert mock_sub.call_args[1]["input"] == "apiVersion: v1\nkind: ConfigMap"


def test_command_deadline_and_timeout():
    def fake_subprocess_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=kwargs.get("timeout", 1.0))

    with patch("subprocess.run", fake_subprocess_run):
        with tempfile.TemporaryDirectory() as tmpdir:
            res = live_demo.run_command(["test_prog"], evidence_dir=tmpdir, timeout=1.5)
            assert res.returncode == -124
            assert "Command timed out after 1.5 seconds." in res.stderr
            diag_file = os.path.join(tmpdir, "diagnostics")
            assert os.path.isdir(diag_file)
            assert len(os.listdir(diag_file)) == 1


def test_explicit_result_validation_rejects_negative_or_incomplete_or_unordered():
    run_id = str(uuid.uuid4())
    eval_id = str(uuid.uuid4())

    valid_result = {
        "run_id": run_id,
        "evaluation_id": eval_id,
        "phases": [
            {"phase": p.value, "succeeded": True} for p in VerificationPhase
        ],
        "observations": [
            {"kind": EvidenceKind.KUBERNETES_READINESS.value, "semantic": EvidenceSemantic.AFFIRMATIVE.value},
            {"kind": EvidenceKind.APPLICATION_RESPONSIVENESS.value, "semantic": EvidenceSemantic.AFFIRMATIVE.value},
        ],
        "knowledge": {"standing": RecoverabilityTruthStandingValue.RECOVERABILITY_AFFIRMED.value},
        "disclaimer": "No production recoverability decision was made.",
    }

    live_demo.validate_verification_result(valid_result, run_id, eval_id)

    with pytest.raises(ValueError, match="run_id mismatch"):
        live_demo.validate_verification_result(valid_result, str(uuid.uuid4()), eval_id)

    bad_truth = dict(valid_result)
    bad_truth["phases"] = [
        {"phase": p.value, "succeeded": 1} for p in VerificationPhase
    ]
    with pytest.raises(ValueError, match="exact boolean True"):
        live_demo.validate_verification_result(bad_truth, run_id, eval_id)

    bad_order = dict(valid_result)
    bad_order["phases"] = list(reversed(valid_result["phases"]))
    with pytest.raises(ValueError, match="order mismatch"):
        live_demo.validate_verification_result(bad_order, run_id, eval_id)

    bad_dup = dict(valid_result)
    bad_dup["phases"] = list(valid_result["phases"]) + [valid_result["phases"][0]]
    with pytest.raises(ValueError, match="Duplicate phase detected"):
        live_demo.validate_verification_result(bad_dup, run_id, eval_id)

    bad_obs = dict(valid_result)
    bad_obs["observations"] = [
        {"kind": EvidenceKind.KUBERNETES_READINESS.value, "semantic": EvidenceSemantic.NEGATIVE.value},
        {"kind": EvidenceKind.APPLICATION_RESPONSIVENESS.value, "semantic": EvidenceSemantic.AFFIRMATIVE.value},
    ]
    with pytest.raises(ValueError, match="non-affirmative"):
        live_demo.validate_verification_result(bad_obs, run_id, eval_id)

    bad_disclaimer = dict(valid_result)
    bad_disclaimer["disclaimer"] = "Production recoverability verified."
    with pytest.raises(ValueError, match="disclaimer missing"):
        live_demo.validate_verification_result(bad_disclaimer, run_id, eval_id)


def test_actual_verifier_output_tested_directly_through_validator():
    run_id = str(uuid.uuid4())
    img = "ghcr.io/demo/app@sha256:" + ("1" * 64)
    payload = live_demo.build_verification_request_payload(run_id, img)

    with patch("workloads.verifier.verifier.run_runtime_owned_verification") as mock_coord:
        mock_coord.return_value = VerificationCoordinationResult(
            run_id=uuid.UUID(run_id),
            evaluation_id=uuid.UUID(payload["evaluation_id"]),
            observations=(
                RecoveryObservation(
                    observation_id=uuid.uuid4(),
                    recovery_event_id=uuid.UUID(payload["event_id"]),
                    workload_id=uuid.UUID(payload["workload_id"]),
                    occurrence_time=datetime.datetime.now(datetime.timezone.utc),
                    content=ObservedContent(
                        kind=EvidenceKind.KUBERNETES_READINESS,
                        semantic=EvidenceSemantic.AFFIRMATIVE,
                        description="Deployment ready",
                    ),
                    provenance=Provenance("k8s"),
                ),
                RecoveryObservation(
                    observation_id=uuid.uuid4(),
                    recovery_event_id=uuid.UUID(payload["event_id"]),
                    workload_id=uuid.UUID(payload["workload_id"]),
                    occurrence_time=datetime.datetime.now(datetime.timezone.utc),
                    content=ObservedContent(
                        kind=EvidenceKind.APPLICATION_RESPONSIVENESS,
                        semantic=EvidenceSemantic.AFFIRMATIVE,
                        description="HTTP response 200 OK",
                    ),
                    provenance=Provenance("http"),
                ),
            ),
            knowledge=RecoverabilityKnowledge(
                knowledge_id=uuid.uuid4(),
                subject=RecoverabilitySubject(RecoverabilitySubjectId(uuid.UUID(payload["workload_id"]))),
                standing=RecoverabilityTruthStandingValue.RECOVERABILITY_AFFIRMED,
                established_at=datetime.datetime.now(datetime.timezone.utc),
            ),
            phase_results=tuple(
                ExecutionPhaseResult(phase=p, succeeded=True) for p in VerificationPhase
            ),
        )

        verifier_output = execute_verifier(payload)
        live_demo.validate_verification_result(verifier_output, run_id, payload["evaluation_id"])


def test_missing_repo_root_rejected_without_fallback():
    with pytest.raises(ValueError, match="does not exist"):
        live_demo.execute_public_live_demo(
            workload_image="ghcr.io/org/app@sha256:" + ("1" * 64),
            verifier_image="ghcr.io/org/verifier@sha256:" + ("2" * 64),
            repo_root="/nonexistent/path/for/demo",
            dry_run=False,
        )


def test_preexisting_terraform_state_refused():
    with tempfile.TemporaryDirectory() as tmpdir:
        tf_dir = os.path.join(tmpdir, "infra", "terraform")
        k8s_dir = os.path.join(tmpdir, "k8s", "verifier")
        os.makedirs(tf_dir, exist_ok=True)
        os.makedirs(k8s_dir, exist_ok=True)

        with open(os.path.join(tf_dir, "terraform.tfstate"), "w") as f:
            f.write('{"version": 4, "serial": 1, "resources": [{"name": "old"}]}')

        code = live_demo.execute_public_live_demo(
            workload_image="ghcr.io/demo/app@sha256:" + ("1" * 64),
            verifier_image="ghcr.io/demo/verifier@sha256:" + ("2" * 64),
            repo_root=tmpdir,
            dry_run=False,
        )
        assert code == 1
        summary_base = os.path.join(tmpdir, ".demo", "evidence")
        run_ids = os.listdir(summary_base)
        with open(os.path.join(summary_base, run_ids[0], "summary.json")) as f:
            summary = json.load(f)
            assert any("Unrelated pre-existing Terraform state found" in e for e in summary["errors"])


def test_ownership_verification_fails_closed():
    calls = []
    base_cmd = ["kubectl", "--kubeconfig", "mock"]

    def mock_run_timeout(*args, **kwargs):
        return live_demo.CommandResult(args[0], -124, "", "timeout", "", "")
    with patch.object(live_demo, "run_command", mock_run_timeout):
        owned, msg = live_demo.verify_kubernetes_resource_ownership(base_cmd, "/tmp", "job", "my-job", {"k": "v"})
        assert not owned
        assert "Timeout" in msg

    def mock_run_denied(*args, **kwargs):
        return live_demo.CommandResult(args[0], 1, "", "Error from server (Forbidden): unauthorized", "", "")
    with patch.object(live_demo, "run_command", mock_run_denied):
        owned, msg = live_demo.verify_kubernetes_resource_ownership(base_cmd, "/tmp", "job", "my-job", {"k": "v"})
        assert not owned
        assert "Access denied" in msg

    def mock_run_mismatch(*args, **kwargs):
        return live_demo.CommandResult(args[0], 0, json.dumps({"metadata": {"labels": {"k": "other"}}}), "", "", "")
    with patch.object(live_demo, "run_command", mock_run_mismatch):
        owned, msg = live_demo.verify_kubernetes_resource_ownership(base_cmd, "/tmp", "job", "my-job", {"k": "v"})
        assert not owned
        assert "mismatch" in msg

    def mock_run_generic_not_found(*args, **kwargs):
        return live_demo.CommandResult(args[0], 1, "", "some unexpected error: file not found on host", "", "")
    with patch.object(live_demo, "run_command", mock_run_generic_not_found):
        owned, msg = live_demo.verify_kubernetes_resource_ownership(base_cmd, "/tmp", "job", "my-job", {"k": "v"})
        assert not owned
        assert "Error checking" in msg

    def mock_run_exact_not_found(*args, **kwargs):
        return live_demo.CommandResult(args[0], 1, "", 'Error from server (NotFound): jobs.batch "my-job" not found', "", "")
    with patch.object(live_demo, "run_command", mock_run_exact_not_found):
        owned, msg = live_demo.verify_kubernetes_resource_ownership(base_cmd, "/tmp", "job", "my-job", {"k": "v"})
        assert owned
        assert msg == "absent"


def test_residue_auditing_empty_inventory_fails_error():
    status, errors = live_demo.audit_cloud_residue(
        tf_dir="/tmp/tf",
        evidence_dir="/tmp/evidence",
        state_file="/tmp/evidence/terraform.tfstate",
        tf_vars=["-state=/tmp/evidence/terraform.tfstate"],
        cloud_resources={},
        aws_region="ap-south-1",
    )
    assert status == "ERROR"
    assert any("empty" in e for e in errors)


def test_residue_auditing_refuses_default_state():
    tf_dir = "/tmp/demo_tf"
    default_state = os.path.join(tf_dir, "terraform.tfstate")
    status, errors = live_demo.audit_cloud_residue(
        tf_dir=tf_dir,
        evidence_dir="/tmp/evidence",
        state_file=default_state,
        tf_vars=[f"-state={default_state}"],
        cloud_resources={"cluster_name": "test"},
        aws_region="ap-south-1",
    )
    assert status == "ERROR"
    assert any("cannot use default terraform.tfstate" in e for e in errors)


def test_terraform_show_real_cli_regression_without_aws():
    if not shutil.which("terraform"):
        pytest.skip("terraform CLI is not installed or available on PATH")
    with tempfile.TemporaryDirectory() as tmpdir:
        tf_file = os.path.join(tmpdir, "main.tf")
        with open(tf_file, "w", encoding="utf-8") as f:
            f.write("""
terraform {
  required_version = ">= 1.0"
}
output "demo_cluster_name" {
  value = "brace-test-cluster"
}
""")
        evidence_dir = os.path.join(tmpdir, "evidence")
        os.makedirs(evidence_dir, exist_ok=True)
        init_res = live_demo.run_command(["terraform", "init", "-no-color"], cwd=tmpdir, evidence_dir=evidence_dir)
        assert init_res.returncode == 0

        state_file = os.path.join(evidence_dir, "run.tfstate")
        apply_res = live_demo.run_command(
            ["terraform", "apply", "-auto-approve", "-no-color", f"-state={state_file}"],
            cwd=tmpdir,
            evidence_dir=evidence_dir,
        )
        assert apply_res.returncode == 0
        assert os.path.exists(state_file)

        show_res = live_demo.run_command(["terraform", "show", "-json", state_file], cwd=tmpdir, evidence_dir=evidence_dir)
        assert show_res.returncode == 0
        parsed = json.loads(show_res.stdout)
        outputs = parsed.get("values", {}).get("outputs", {})
        assert outputs.get("demo_cluster_name", {}).get("value") == "brace-test-cluster"

        extracted = live_demo.extract_cloud_resources_from_state(tmpdir, evidence_dir, state_file)
        assert isinstance(extracted, dict)


def test_partial_apply_extracts_identities_directly_from_state():
    fake_state = {
        "format_version": "1.0",
        "values": {
            "root_module": {
                "child_modules": [
                    {
                        "resources": [
                            {
                                "type": "aws_eks_cluster",
                                "values": {
                                    "name": "brace-demo-partial-cluster",
                                    "arn": "arn:aws:eks:ap-south-1:111:cluster/brace-demo-partial-cluster",
                                },
                            },
                            {
                                "type": "aws_vpc",
                                "values": {
                                    "id": "vpc-partial-999",
                                },
                            },
                            {
                                "type": "aws_subnet",
                                "values": {
                                    "id": "subnet-partial-1",
                                },
                            },
                            {
                                "type": "aws_subnet",
                                "values": {
                                    "id": "subnet-partial-2",
                                },
                            },
                        ],
                    }
                ]
            }
        }
    }
    with tempfile.TemporaryDirectory() as tmpdir:
        state_file = os.path.join(tmpdir, "partial.tfstate")
        with open(state_file, "w", encoding="utf-8") as f:
            json.dump(fake_state, f)

        def mock_show(cmd, cwd=None, evidence_dir=None, **kwargs):
            if "show" in cmd and state_file in cmd:
                return live_demo.CommandResult(cmd, 0, json.dumps(fake_state), "", "", "")
            return live_demo.CommandResult(cmd, 0, "{}", "", "", "")

        with patch.object(live_demo, "run_command", mock_show):
            extracted = live_demo.extract_cloud_resources_from_state(tmpdir, tmpdir, state_file)
            assert extracted["cluster_name"] == "brace-demo-partial-cluster"
            assert extracted["cluster_arn"] == "arn:aws:eks:ap-south-1:111:cluster/brace-demo-partial-cluster"
            assert extracted["vpc_id"] == "vpc-partial-999"
            assert "subnet-partial-1" in extracted["subnet_ids"]
            assert "subnet-partial-2" in extracted["subnet_ids"]


def test_configmap_structured_document_modification():
    run_id = str(uuid.uuid4())
    img = "ghcr.io/brace-demo/workload@sha256:" + ("a" * 64)
    payload = live_demo.build_verification_request_payload(run_id, img)
    raw_kubectl_output = json.dumps({
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {
            "name": "brace-request-123",
            "creationTimestamp": None,
        },
        "data": {
            "request.json": json.dumps(payload),
        },
    })
    cm_obj = json.loads(raw_kubectl_output)
    if "metadata" not in cm_obj:
        cm_obj["metadata"] = {}
    if "labels" not in cm_obj["metadata"]:
        cm_obj["metadata"]["labels"] = {}
    cm_obj["metadata"]["labels"]["app.kubernetes.io/managed-by"] = "brace"
    cm_obj["metadata"]["labels"]["brace.io/run-id"] = run_id

    assert cm_obj["metadata"]["labels"]["app.kubernetes.io/managed-by"] == "brace"
    assert cm_obj["metadata"]["labels"]["brace.io/run-id"] == run_id
    restored_request = json.loads(cm_obj["data"]["request.json"])
    assert restored_request["run_id"] == run_id
    assert restored_request["bundle"]["deployment"]["container"]["image"] == img
    assert restored_request["evaluation_id"] == payload["evaluation_id"]


def test_evidence_survives_verification_failure_and_is_retained():
    calls = []

    def mock_run_command(cmd, cwd=None, evidence_dir=None, **kwargs):
        calls.append(cmd)
        if "output" in cmd:
            return live_demo.CommandResult(cmd, 0, json.dumps({
                "cluster_name": {"value": "test-cluster"},
                "cluster_arn": {"value": "arn:aws:eks:ap-south-1:123:cluster/test-cluster"},
                "vpc_id": {"value": "vpc-123"},
                "subnet_ids": {"value": ["subnet-1", "subnet-2"]},
            }), "", "", "")
        if "update-kubeconfig" in cmd:
            for i, arg in enumerate(cmd):
                if arg == "--kubeconfig":
                    pathlib.Path(cmd[i+1]).touch()
            return live_demo.CommandResult(cmd, 0, "", "", "", "")
        if "kubectl" in cmd and "wait" in cmd:
            return live_demo.CommandResult(cmd, 1, "", "Timed out waiting for Job", "", "")
        if "show" in cmd:
            return live_demo.CommandResult(cmd, 0, json.dumps({"values": {"root_module": {}}}), "", "", "")
        if "plan" in cmd:
            return live_demo.CommandResult(cmd, 0, "", "", "", "")
        if "describe-cluster" in cmd:
            return live_demo.CommandResult(cmd, 254, "", "ResourceNotFoundException: cluster not found", "", "")
        if "describe-subnets" in cmd:
            return live_demo.CommandResult(cmd, 254, "", "InvalidSubnetID.NotFound: subnets not found", "", "")
        if "describe-vpcs" in cmd:
            return live_demo.CommandResult(cmd, 254, "", "InvalidVpcID.NotFound: vpc not found", "", "")
        return live_demo.CommandResult(cmd, 0, "{}", "", "", "")

    with patch.object(live_demo, "run_command", mock_run_command):
        with tempfile.TemporaryDirectory() as tmpdir:
            tf_dir = os.path.join(tmpdir, "infra", "terraform")
            k8s_dir = os.path.join(tmpdir, "k8s", "verifier")
            os.makedirs(tf_dir, exist_ok=True)
            os.makedirs(k8s_dir, exist_ok=True)
            with open(os.path.join(k8s_dir, "rbac.yaml"), "w") as f:
                f.write("kind: ServiceAccount\nmetadata:\n  name: ${VERIFIER_SERVICE_ACCOUNT}")
            with open(os.path.join(k8s_dir, "job-template.yaml"), "w") as f:
                f.write("kind: Job\nmetadata:\n  name: ${JOB_NAME}")

            code = live_demo.execute_public_live_demo(
                workload_image="ghcr.io/demo/app@sha256:" + ("1" * 64),
                verifier_image="ghcr.io/demo/verifier@sha256:" + ("2" * 64),
                repo_root=tmpdir,
                dry_run=False,
            )

            assert code == 1
            assert any("describe" in c for c in calls)
            assert any("get" in c and "pods" in c for c in calls)

            evidence_base = os.path.join(tmpdir, ".demo", "evidence")
            assert os.path.exists(evidence_base)
            run_ids = os.listdir(evidence_base)
            assert len(run_ids) == 1
            summary_path = os.path.join(evidence_base, run_ids[0], "summary.json")
            with open(summary_path) as f:
                summary = json.load(f)
                assert summary["stages"]["OVERALL_RESULT"] == "FAIL"
                assert summary["statement"] == "No production recoverability decision was made."
                assert len(summary["errors"]) > 0


def test_dry_run_performs_zero_mutations():
    calls = []

    def mock_run_command(cmd, **kwargs):
        calls.append(cmd)
        return live_demo.CommandResult(cmd, 0, "", "", "", "")

    with patch.object(live_demo, "run_command", mock_run_command):
        with tempfile.TemporaryDirectory() as tmpdir:
            code = live_demo.execute_public_live_demo(
                repo_root=tmpdir,
                dry_run=True,
            )
            assert code == 0
            assert len(calls) == 0

            evidence_base = os.path.join(tmpdir, ".demo", "evidence")
            run_ids = os.listdir(evidence_base)
            summary_path = os.path.join(evidence_base, run_ids[0], "summary.json")
            with open(summary_path) as f:
                summary = json.load(f)
                assert summary["stages"]["PROVISION"] == "SIMULATED"
                assert summary["stages"]["OVERALL_RESULT"] == "PASS"


def test_partial_provisioning_triggers_cleanup():
    calls = []

    def mock_run_command(cmd, cwd=None, evidence_dir=None, **kwargs):
        calls.append(cmd)
        if "apply" in cmd and cwd is not None:
            raise RuntimeError("Simulated Terraform apply crash")
        if "show" in cmd:
            return live_demo.CommandResult(cmd, 0, json.dumps({"values": {"root_module": {}}}), "", "", "")
        if "plan" in cmd:
            return live_demo.CommandResult(cmd, 0, "", "", "", "")
        return live_demo.CommandResult(cmd, 0, "{}", "", "", "")

    with patch.object(live_demo, "run_command", mock_run_command):
        with tempfile.TemporaryDirectory() as tmpdir:
            tf_dir = os.path.join(tmpdir, "infra", "terraform")
            k8s_dir = os.path.join(tmpdir, "k8s", "verifier")
            os.makedirs(tf_dir, exist_ok=True)
            os.makedirs(k8s_dir, exist_ok=True)
            with open(os.path.join(k8s_dir, "rbac.yaml"), "w") as f:
                f.write("kind: ServiceAccount\nmetadata:\n  name: ${VERIFIER_SERVICE_ACCOUNT}")
            with open(os.path.join(k8s_dir, "job-template.yaml"), "w") as f:
                f.write("kind: Job\nmetadata:\n  name: ${JOB_NAME}")

            code = live_demo.execute_public_live_demo(
                workload_image="ghcr.io/demo/app@sha256:" + ("1" * 64),
                verifier_image="ghcr.io/demo/verifier@sha256:" + ("2" * 64),
                repo_root=tmpdir,
                dry_run=False,
            )
            assert code == 1
            assert any("apply" in c for c in calls)
            assert any("destroy" in c for c in calls)


def test_cleanup_failure_does_not_hide_original_failure():
    calls = []

    def mock_run_command(cmd, cwd=None, evidence_dir=None, **kwargs):
        calls.append(cmd)
        if "output" in cmd:
            return live_demo.CommandResult(cmd, 0, json.dumps({
                "cluster_name": {"value": "test-cluster"},
            }), "", "", "")
        if "update-kubeconfig" in cmd:
            for i, arg in enumerate(cmd):
                if arg == "--kubeconfig":
                    pathlib.Path(cmd[i+1]).touch()
            return live_demo.CommandResult(cmd, 0, "", "", "", "")
        if "wait" in cmd:
            return live_demo.CommandResult(cmd, 1, "", "Primary verifier failure", "", "")
        if "destroy" in cmd:
            raise RuntimeError("Cleanup destroy failure")
        if "show" in cmd:
            return live_demo.CommandResult(cmd, 0, json.dumps({"values": {"root_module": {}}}), "", "", "")
        if "plan" in cmd:
            return live_demo.CommandResult(cmd, 0, "", "", "", "")
        if "describe-cluster" in cmd:
            return live_demo.CommandResult(cmd, 254, "", "ResourceNotFoundException: cluster not found", "", "")
        return live_demo.CommandResult(cmd, 0, "{}", "", "", "")

    with patch.object(live_demo, "run_command", mock_run_command):
        with tempfile.TemporaryDirectory() as tmpdir:
            tf_dir = os.path.join(tmpdir, "infra", "terraform")
            k8s_dir = os.path.join(tmpdir, "k8s", "verifier")
            os.makedirs(tf_dir, exist_ok=True)
            os.makedirs(k8s_dir, exist_ok=True)
            with open(os.path.join(k8s_dir, "rbac.yaml"), "w") as f:
                f.write("kind: ServiceAccount\nmetadata:\n  name: ${VERIFIER_SERVICE_ACCOUNT}")
            with open(os.path.join(k8s_dir, "job-template.yaml"), "w") as f:
                f.write("kind: Job\nmetadata:\n  name: ${JOB_NAME}")

            code = live_demo.execute_public_live_demo(
                workload_image="ghcr.io/demo/app@sha256:" + ("1" * 64),
                verifier_image="ghcr.io/demo/verifier@sha256:" + ("2" * 64),
                repo_root=tmpdir,
                dry_run=False,
            )
            assert code == 1
            evidence_base = os.path.join(tmpdir, ".demo", "evidence")
            run_ids = os.listdir(evidence_base)
            summary_path = os.path.join(evidence_base, run_ids[0], "summary.json")
            with open(summary_path) as f:
                summary = json.load(f)
                assert summary["stages"]["TEARDOWN"] == "FAIL"
                assert summary["stages"]["OVERALL_RESULT"] == "FAIL"
                assert any("destroy failure" in err for err in summary["errors"])


def test_residue_access_denied_fails_completion_gate():
    calls = []

    def mock_run_command(cmd, cwd=None, evidence_dir=None, **kwargs):
        calls.append(cmd)
        if "output" in cmd:
            return live_demo.CommandResult(cmd, 0, json.dumps({
                "cluster_name": {"value": "test-cluster"},
            }), "", "", "")
        if "update-kubeconfig" in cmd:
            for i, arg in enumerate(cmd):
                if arg == "--kubeconfig":
                    pathlib.Path(cmd[i+1]).touch()
            return live_demo.CommandResult(cmd, 0, "", "", "", "")
        if "show" in cmd:
            return live_demo.CommandResult(cmd, 0, json.dumps({"values": {"root_module": {}}}), "", "", "")
        if "plan" in cmd:
            return live_demo.CommandResult(cmd, 0, "", "", "", "")
        if "describe-cluster" in cmd:
            return live_demo.CommandResult(cmd, 254, "", "AccessDeniedException: User unauthorized", "", "")
        if "logs" in cmd:
            return live_demo.CommandResult(cmd, 0, json.dumps({
                "run_id": "test-run",
                "evaluation_id": "test-eval",
                "disclaimer": "No production recoverability decision was made.",
                "phases": [{"phase": p.value, "succeeded": True} for p in VerificationPhase],
                "observations": [
                    {"kind": EvidenceKind.KUBERNETES_READINESS.value, "semantic": EvidenceSemantic.AFFIRMATIVE.value},
                    {"kind": EvidenceKind.APPLICATION_RESPONSIVENESS.value, "semantic": EvidenceSemantic.AFFIRMATIVE.value},
                ],
                "knowledge": {"standing": RecoverabilityTruthStandingValue.RECOVERABILITY_AFFIRMED.value},
            }), "", "", "")
        return live_demo.CommandResult(cmd, 0, "{}", "", "", "")

    with patch.object(live_demo, "run_command", mock_run_command):
        with tempfile.TemporaryDirectory() as tmpdir:
            tf_dir = os.path.join(tmpdir, "infra", "terraform")
            k8s_dir = os.path.join(tmpdir, "k8s", "verifier")
            os.makedirs(tf_dir, exist_ok=True)
            os.makedirs(k8s_dir, exist_ok=True)
            with open(os.path.join(k8s_dir, "rbac.yaml"), "w") as f:
                f.write("kind: ServiceAccount\nmetadata:\n  name: ${VERIFIER_SERVICE_ACCOUNT}")
            with open(os.path.join(k8s_dir, "job-template.yaml"), "w") as f:
                f.write("kind: Job\nmetadata:\n  name: ${JOB_NAME}")

            code = live_demo.execute_public_live_demo(
                workload_image="ghcr.io/demo/app@sha256:" + ("1" * 64),
                verifier_image="ghcr.io/demo/verifier@sha256:" + ("2" * 64),
                repo_root=tmpdir,
                dry_run=False,
            )
            assert code == 1
            evidence_base = os.path.join(tmpdir, ".demo", "evidence")
            run_ids = os.listdir(evidence_base)
            summary_path = os.path.join(evidence_base, run_ids[0], "summary.json")
            with open(summary_path) as f:
                summary = json.load(f)
                assert summary["stages"]["RESIDUE_AUDIT"] == "ERROR"
                assert summary["stages"]["OVERALL_RESULT"] == "FAIL"


def test_interruption_safe_teardown_guarantee():
    calls = []

    def mock_run_command(cmd, cwd=None, evidence_dir=None, **kwargs):
        calls.append(cmd)
        if "apply" in cmd and cwd is not None:
            raise KeyboardInterrupt("Simulated SIGINT")
        return live_demo.CommandResult(cmd, 0, "{}", "", "", "")

    with patch.object(live_demo, "run_command", mock_run_command):
        with tempfile.TemporaryDirectory() as tmpdir:
            tf_dir = os.path.join(tmpdir, "infra", "terraform")
            k8s_dir = os.path.join(tmpdir, "k8s", "verifier")
            os.makedirs(tf_dir, exist_ok=True)
            os.makedirs(k8s_dir, exist_ok=True)
            with open(os.path.join(k8s_dir, "rbac.yaml"), "w") as f:
                f.write("kind: ServiceAccount\nmetadata:\n  name: ${VERIFIER_SERVICE_ACCOUNT}")
            with open(os.path.join(k8s_dir, "job-template.yaml"), "w") as f:
                f.write("kind: Job\nmetadata:\n  name: ${JOB_NAME}")

            with pytest.raises(KeyboardInterrupt):
                live_demo.execute_public_live_demo(
                    workload_image="ghcr.io/demo/app@sha256:" + ("1" * 64),
                    verifier_image="ghcr.io/demo/verifier@sha256:" + ("2" * 64),
                    repo_root=tmpdir,
                    dry_run=False,
                )
            assert any("destroy" in c for c in calls)


def test_k8s_cleanup_exception_and_interruption_still_executes_teardown():
    calls = []

    def mock_run_command(cmd, cwd=None, evidence_dir=None, **kwargs):
        calls.append(cmd)
        if "output" in cmd:
            return live_demo.CommandResult(cmd, 0, json.dumps({
                "cluster_name": {"value": "test-cluster"},
            }), "", "", "")
        if "update-kubeconfig" in cmd:
            for i, arg in enumerate(cmd):
                if arg == "--kubeconfig":
                    pathlib.Path(cmd[i+1]).touch()
            return live_demo.CommandResult(cmd, 0, "", "", "", "")
        if "get" in cmd and "job" in cmd:
            raise RuntimeError("Simulated Kubernetes API connection drop during cleanup")
        if "show" in cmd:
            return live_demo.CommandResult(cmd, 0, json.dumps({"values": {"root_module": {}}}), "", "", "")
        if "plan" in cmd:
            return live_demo.CommandResult(cmd, 0, "", "", "", "")
        if "describe-cluster" in cmd:
            return live_demo.CommandResult(cmd, 254, "", "ResourceNotFoundException: not found", "", "")
        if "logs" in cmd:
            return live_demo.CommandResult(cmd, 0, json.dumps({
                "run_id": "test-run",
                "evaluation_id": "test-eval",
                "disclaimer": "No production recoverability decision was made.",
                "phases": [{"phase": p.value, "succeeded": True} for p in VerificationPhase],
                "observations": [
                    {"kind": EvidenceKind.KUBERNETES_READINESS.value, "semantic": EvidenceSemantic.AFFIRMATIVE.value},
                    {"kind": EvidenceKind.APPLICATION_RESPONSIVENESS.value, "semantic": EvidenceSemantic.AFFIRMATIVE.value},
                ],
                "knowledge": {"standing": RecoverabilityTruthStandingValue.RECOVERABILITY_AFFIRMED.value},
            }), "", "", "")
        return live_demo.CommandResult(cmd, 0, "{}", "", "", "")

    with patch.object(live_demo, "run_command", mock_run_command):
        with tempfile.TemporaryDirectory() as tmpdir:
            tf_dir = os.path.join(tmpdir, "infra", "terraform")
            k8s_dir = os.path.join(tmpdir, "k8s", "verifier")
            os.makedirs(tf_dir, exist_ok=True)
            os.makedirs(k8s_dir, exist_ok=True)
            with open(os.path.join(k8s_dir, "rbac.yaml"), "w") as f:
                f.write("kind: ServiceAccount\nmetadata:\n  name: ${VERIFIER_SERVICE_ACCOUNT}")
            with open(os.path.join(k8s_dir, "job-template.yaml"), "w") as f:
                f.write("kind: Job\nmetadata:\n  name: ${JOB_NAME}")

            code = live_demo.execute_public_live_demo(
                workload_image="ghcr.io/demo/app@sha256:" + ("1" * 64),
                verifier_image="ghcr.io/demo/verifier@sha256:" + ("2" * 64),
                repo_root=tmpdir,
                dry_run=False,
            )
            assert code == 1
            assert any("destroy" in c for c in calls)
            evidence_base = os.path.join(tmpdir, ".demo", "evidence")
            run_ids = os.listdir(evidence_base)
            summary_path = os.path.join(evidence_base, run_ids[0], "summary.json")
            with open(summary_path) as f:
                summary = json.load(f)
                assert summary["stages"]["K8S_CLEANUP"] == "FAIL"
                assert summary["stages"]["TEARDOWN"] == "PASS"
                assert any("Kubernetes cleanup error" in err for err in summary["errors"])


def test_local_detached_container_fails_without_cluster_config():
    if not shutil.which("docker"):
        pytest.skip("docker CLI is not installed or available on PATH")
    inspect_proc = subprocess.run(
        ["docker", "image", "inspect", "brace-verifier:local"],
        capture_output=True,
        text=True,
    )
    if inspect_proc.returncode != 0:
        pytest.skip("local image 'brace-verifier:local' is not present in Docker storage")

    run_id = str(uuid.uuid4())
    img = "brace-demo-workload:local@sha256:" + ("a" * 64)
    payload = live_demo.build_verification_request_payload(run_id, img)
    req_json_str = json.dumps(payload)

    proc = subprocess.run(
        ["docker", "run", "--rm", "-i", "--pull=never", "brace-verifier:local"],
        input=req_json_str,
        text=True,
        capture_output=True,
    )
    assert "ConfigException" in proc.stderr or "Service host/port is not set" in proc.stderr


def test_terraform_apply_receives_explicit_2700_second_timeout():
    assert live_demo.TIMEOUT_TERRAFORM_APPLY == 2700.0

    calls: list[dict[str, Any]] = []

    def mock_run_command(cmd, **kwargs):
        calls.append({"cmd": cmd, "kwargs": kwargs})
        if "apply" in cmd:
            return live_demo.CommandResult(args=cmd, returncode=0, stdout="", stderr="", start_time="", end_time="")
        if "output" in cmd:
            return live_demo.CommandResult(args=cmd, returncode=0, stdout=json.dumps({"cluster_name": {"value": "test"}}), stderr="", start_time="", end_time="")
        return live_demo.CommandResult(args=cmd, returncode=0, stdout="", stderr="", start_time="", end_time="")

    with tempfile.TemporaryDirectory() as tmpdir:
        tf_dir = os.path.join(tmpdir, "infra", "terraform")
        k8s_dir = os.path.join(tmpdir, "k8s", "verifier")
        os.makedirs(tf_dir, exist_ok=True)
        os.makedirs(k8s_dir, exist_ok=True)

        with patch("scripts.live_demo.run_command", side_effect=mock_run_command):
            with patch("scripts.live_demo.validate_image_digest_pinning"):
                try:
                    live_demo.execute_public_live_demo(
                        workload_image="ghcr.io/test/workload@sha256:" + ("0" * 64),
                        verifier_image="ghcr.io/test/verifier@sha256:" + ("0" * 64),
                        repo_root=tmpdir,
                        dry_run=False,
                    )
                except Exception:
                    pass

    apply_calls = [c for c in calls if "apply" in c["cmd"] and "-auto-approve" in c["cmd"]]
    assert len(apply_calls) == 1
    assert apply_calls[0]["kwargs"].get("timeout") == 2700.0


def test_cleanup_attached_nodegroups_handles_cluster_absence():
    with tempfile.TemporaryDirectory() as tmpdir:
        def mock_run_cmd(cmd, **kwargs):
            if "list-nodegroups" in cmd:
                return live_demo.CommandResult(args=cmd, returncode=254, stdout="", stderr="ResourceNotFoundException: No cluster found", start_time="", end_time="")
            return live_demo.CommandResult(args=cmd, returncode=0, stdout="", stderr="", start_time="", end_time="")

        with patch("scripts.live_demo.run_command", side_effect=mock_run_cmd):
            success, msg = live_demo.cleanup_attached_nodegroups("test-cluster", "test-run", "ap-south-1", tmpdir)
            assert success is True
            assert "absent" in msg.lower()


def test_cleanup_attached_nodegroups_zero_attached():
    with tempfile.TemporaryDirectory() as tmpdir:
        def mock_run_cmd(cmd, **kwargs):
            if "list-nodegroups" in cmd:
                return live_demo.CommandResult(args=cmd, returncode=0, stdout=json.dumps({"nodegroups": []}), stderr="", start_time="", end_time="")
            return live_demo.CommandResult(args=cmd, returncode=0, stdout="", stderr="", start_time="", end_time="")

        with patch("scripts.live_demo.run_command", side_effect=mock_run_cmd):
            success, msg = live_demo.cleanup_attached_nodegroups("test-cluster", "test-run", "ap-south-1", tmpdir)
            assert success is True
            assert "no attached nodegroups" in msg.lower()


def test_cleanup_attached_nodegroups_discovers_verifies_deletes_and_polls():
    with tempfile.TemporaryDirectory() as tmpdir:
        poll_count = 0
        delete_called = False
        deleted_ngs: list[str] = []

        def mock_run_cmd(cmd, **kwargs):
            nonlocal poll_count, delete_called
            if "list-nodegroups" in cmd:
                return live_demo.CommandResult(args=cmd, returncode=0, stdout=json.dumps({"nodegroups": ["ng-demo-1"]}), stderr="", start_time="", end_time="")
            if "describe-nodegroup" in cmd:
                if delete_called:
                    poll_count += 1
                    if poll_count >= 2:
                        return live_demo.CommandResult(args=cmd, returncode=254, stdout="", stderr="ResourceNotFoundException", start_time="", end_time="")
                    return live_demo.CommandResult(args=cmd, returncode=0, stdout=json.dumps({"nodegroup": {"nodegroupName": "ng-demo-1", "status": "DELETING"}}), stderr="", start_time="", end_time="")
                return live_demo.CommandResult(args=cmd, returncode=0, stdout=json.dumps({
                    "nodegroup": {
                        "nodegroupName": "ng-demo-1",
                        "status": "CREATING",
                        "tags": {
                            "Project": "brace-platform-public-demo",
                            "brace.io/run-id": "run-xyz-123",
                        }
                    }
                }), stderr="", start_time="", end_time="")
            if "delete-nodegroup" in cmd:
                delete_called = True
                deleted_ngs.append("ng-demo-1")
                return live_demo.CommandResult(args=cmd, returncode=0, stdout="", stderr="", start_time="", end_time="")
            return live_demo.CommandResult(args=cmd, returncode=0, stdout="", stderr="", start_time="", end_time="")

        with patch("scripts.live_demo.run_command", side_effect=mock_run_cmd):
            success, msg = live_demo.cleanup_attached_nodegroups(
                cluster_name="test-cluster",
                run_id="run-xyz-123",
                aws_region="ap-south-1",
                evidence_dir=tmpdir,
                poll_interval_seconds=0.01,
            )
            assert success is True
            assert "deleted" in msg.lower()
            assert "ng-demo-1" in deleted_ngs
            assert poll_count >= 2


def test_cleanup_attached_nodegroups_ownership_mismatch_refuses_deletion():
    with tempfile.TemporaryDirectory() as tmpdir:
        del_attempted = False

        def mock_run_cmd(cmd, **kwargs):
            nonlocal del_attempted
            if "list-nodegroups" in cmd:
                return live_demo.CommandResult(args=cmd, returncode=0, stdout=json.dumps({"nodegroups": ["ng-foreign"]}), stderr="", start_time="", end_time="")
            if "describe-nodegroup" in cmd:
                return live_demo.CommandResult(args=cmd, returncode=0, stdout=json.dumps({
                    "nodegroup": {
                        "nodegroupName": "ng-foreign",
                        "status": "ACTIVE",
                        "tags": {
                            "Project": "other-project",
                            "brace.io/run-id": "other-run",
                        }
                    }
                }), stderr="", start_time="", end_time="")
            if "delete-nodegroup" in cmd:
                del_attempted = True
                return live_demo.CommandResult(args=cmd, returncode=0, stdout="", stderr="", start_time="", end_time="")
            return live_demo.CommandResult(args=cmd, returncode=0, stdout="", stderr="", start_time="", end_time="")

        with patch("scripts.live_demo.run_command", side_effect=mock_run_cmd):
            success, msg = live_demo.cleanup_attached_nodegroups(
                cluster_name="test-cluster",
                run_id="run-expected-123",
                aws_region="ap-south-1",
                evidence_dir=tmpdir,
            )
            assert success is False
            assert "refusing to delete" in msg.lower()
            assert del_attempted is False


def test_cleanup_attached_nodegroups_errors_fail_closed():
    with tempfile.TemporaryDirectory() as tmpdir:
        def mock_access_denied(cmd, **kwargs):
            return live_demo.CommandResult(args=cmd, returncode=254, stdout="", stderr="AccessDeniedException: not authorized", start_time="", end_time="")

        with patch("scripts.live_demo.run_command", side_effect=mock_access_denied):
            success, msg = live_demo.cleanup_attached_nodegroups("test-cluster", "run-1", "ap-south-1", tmpdir)
            assert success is False
            assert "access denied" in msg.lower()

        def mock_timeout(cmd, **kwargs):
            return live_demo.CommandResult(args=cmd, returncode=-124, stdout="", stderr="timed out", start_time="", end_time="")

        with patch("scripts.live_demo.run_command", side_effect=mock_timeout):
            success, msg = live_demo.cleanup_attached_nodegroups("test-cluster", "run-1", "ap-south-1", tmpdir)
            assert success is False
            assert "timeout" in msg.lower()

        def mock_malformed(cmd, **kwargs):
            return live_demo.CommandResult(args=cmd, returncode=0, stdout="not valid json", stderr="", start_time="", end_time="")

        with patch("scripts.live_demo.run_command", side_effect=mock_malformed):
            success, msg = live_demo.cleanup_attached_nodegroups("test-cluster", "run-1", "ap-south-1", tmpdir)
            assert success is False
            assert "malformed json" in msg.lower()


def test_teardown_blocks_destroy_and_preserves_primary_failure_if_nodegroup_cleanup_fails():
    with tempfile.TemporaryDirectory() as tmpdir:
        tf_dir = os.path.join(tmpdir, "infra", "terraform")
        k8s_dir = os.path.join(tmpdir, "k8s", "verifier")
        os.makedirs(tf_dir, exist_ok=True)
        os.makedirs(k8s_dir, exist_ok=True)

        destroy_called = False

        def mock_run_cmd(cmd, **kwargs):
            nonlocal destroy_called
            if "init" in cmd:
                return live_demo.CommandResult(args=cmd, returncode=0, stdout="", stderr="", start_time="", end_time="")
            if "apply" in cmd:
                raise subprocess.TimeoutExpired(cmd=cmd, timeout=2700.0)
            if "list-nodegroups" in cmd:
                return live_demo.CommandResult(args=cmd, returncode=254, stdout="", stderr="AccessDeniedException", start_time="", end_time="")
            if "destroy" in cmd:
                destroy_called = True
                return live_demo.CommandResult(args=cmd, returncode=0, stdout="", stderr="", start_time="", end_time="")
            return live_demo.CommandResult(args=cmd, returncode=0, stdout="", stderr="", start_time="", end_time="")

        with patch("scripts.live_demo.run_command", side_effect=mock_run_cmd):
            with patch("scripts.live_demo.validate_image_digest_pinning"):
                with patch("scripts.live_demo.audit_cloud_residue", return_value=("DIRTY", ["Residue found"])):
                    code = live_demo.execute_public_live_demo(
                        workload_image="ghcr.io/test/workload@sha256:" + ("0" * 64),
                        verifier_image="ghcr.io/test/verifier@sha256:" + ("0" * 64),
                        repo_root=tmpdir,
                        dry_run=False,
                    )
                    assert code == 1
                    assert destroy_called is False

        evidence_base = os.path.join(tmpdir, ".demo", "evidence")
        run_ids = os.listdir(evidence_base)
        summary_path = os.path.join(evidence_base, run_ids[0], "summary.json")
        with open(summary_path) as f:
            summary = json.load(f)
            assert summary["stages"]["TEARDOWN"] == "FAIL"
            assert any("Command timed out after 2700.0" in err or "timed out" in err.lower() for err in summary["errors"])
            assert any("Pre-destroy nodegroup cleanup failure" in err for err in summary["errors"])
