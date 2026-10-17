from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from typing import Any, NamedTuple

from brace._evidence_kind import EvidenceKind
from brace._evidence_semantic import EvidenceSemantic
from brace._execution import RunNamespaceIdentity, WorkloadBundleIdentity
from brace._kubernetes_namespace import (
    NamespaceInspectionOutcome,
    NamespaceOwnership,
    _ownership_labels,
)
from brace._recoverability_truth_standing_value import RecoverabilityTruthStandingValue
from brace._runtime_owned_verification import VerificationPhase

TIMEOUT_DEFAULT_COMMAND = 120.0
TIMEOUT_TERRAFORM_APPLY = 1800.0
TIMEOUT_TERRAFORM_DESTROY = 1800.0
TIMEOUT_KUBECTL_WAIT = 600.0


class CommandResult(NamedTuple):
    args: list[str]
    returncode: int
    stdout: str
    stderr: str
    start_time: str
    end_time: str


def sanitize_cmd_name(name: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]", "", name)


def log_command_evidence(
    diagnostics_dir: str,
    cmd: list[str],
    stdout: str,
    stderr: str,
    returncode: int,
    start_time: str,
    end_time: str,
) -> None:
    os.makedirs(diagnostics_dir, exist_ok=True)
    prog = sanitize_cmd_name(os.path.basename(cmd[0])) if cmd else "cmd"
    cmd_id = str(uuid.uuid4())[:8]
    filepath = os.path.join(diagnostics_dir, f"{prog}-{cmd_id}.json")
    data = {
        "command": cmd,
        "stdout": stdout,
        "stderr": stderr,
        "exit_code": returncode,
        "start_time": start_time,
        "end_time": end_time,
    }
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def run_command(
    cmd: list[str],
    cwd: str | None = None,
    evidence_dir: str | None = None,
    timeout: float = TIMEOUT_DEFAULT_COMMAND,
    env: dict[str, str] | None = None,
    check: bool = False,
    input_data: str | None = None,
) -> CommandResult:
    start_time = datetime.datetime.now(datetime.timezone.utc).isoformat()
    diagnostics_dir = os.path.join(evidence_dir, "diagnostics") if evidence_dir is not None else None
    try:
        proc = subprocess.run(
            cmd,
            cwd=cwd,
            input=input_data,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
            check=False,
        )
        end_time = datetime.datetime.now(datetime.timezone.utc).isoformat()
        res = CommandResult(cmd, proc.returncode, proc.stdout, proc.stderr, start_time, end_time)
    except subprocess.TimeoutExpired as exc:
        end_time = datetime.datetime.now(datetime.timezone.utc).isoformat()
        stdout = exc.stdout or "" if isinstance(exc.stdout, str) else ""
        stderr = (exc.stderr or "" if isinstance(exc.stderr, str) else "") + f"\nError: Command timed out after {timeout} seconds."
        res = CommandResult(cmd, -124, stdout, stderr, start_time, end_time)

    if diagnostics_dir is not None:
        log_command_evidence(diagnostics_dir, res.args, res.stdout, res.stderr, res.returncode, res.start_time, res.end_time)

    if check and res.returncode != 0:
        raise RuntimeError(f"Command failed with exit code {res.returncode}: {' '.join(cmd)}\nStderr: {res.stderr}")

    return res


def validate_image_digest_pinning(image: str, allow_placeholder: bool = False) -> None:
    if "@sha256:" not in image:
        raise ValueError(f"Image {image} must be pinned by cryptographic digest (@sha256:)")
    repo, digest = image.split("@sha256:", 1)
    if not repo:
        raise ValueError(f"Image repository in {image} must not be empty")
    if len(digest) != 64 or not all(c in "0123456789abcdef" for c in digest):
        raise ValueError(f"Image digest {digest} must be exactly 64 lowercase hexadecimal characters")
    if not allow_placeholder and (digest == "0" * 64 or digest == "f" * 64):
        raise ValueError(f"Placeholder image digest {digest} is prohibited for live execution")


def build_verification_request_payload(
    run_id: str,
    workload_image: str,
) -> dict[str, Any]:
    evaluation_id = str(uuid.uuid4())
    workload_id = str(uuid.uuid4())
    event_id = str(uuid.uuid4())

    return {
        "run_id": run_id,
        "evaluation_id": evaluation_id,
        "workload_id": workload_id,
        "event_id": event_id,
        "bundle": {
            "name": "public-demo-workload",
            "version": "1.0.0",
            "deployment": {
                "name": "demo-workload",
                "replicas": 1,
                "container": {
                    "name": "demo-app",
                    "image": workload_image,
                    "port": 8080,
                },
            },
            "service": {
                "name": "demo-service",
                "port": 8080,
                "target_port": 8080,
            },
        },
    }


def validate_verification_result(
    output_data: dict[str, Any],
    expected_run_id: str,
    expected_evaluation_id: str,
) -> None:
    if not isinstance(output_data, dict):
        raise ValueError("Verifier output must be a JSON object")

    actual_run_id = output_data.get("run_id")
    if actual_run_id != expected_run_id:
        raise ValueError(f"Result run_id mismatch: expected {expected_run_id}, got {actual_run_id}")

    actual_eval_id = output_data.get("evaluation_id")
    if actual_eval_id != expected_evaluation_id:
        raise ValueError(f"Result evaluation_id mismatch: expected {expected_evaluation_id}, got {actual_eval_id}")

    disclaimer = output_data.get("disclaimer")
    if disclaimer != "No production recoverability decision was made.":
        raise ValueError(f"Required disclaimer missing or modified: {disclaimer}")

    required_phase_order = [p.value for p in VerificationPhase]
    phases = output_data.get("phases")
    if not isinstance(phases, list):
        raise ValueError("Result phases must be a list")

    seen_phases: set[str] = set()
    phase_names: list[str] = []
    for pr in phases:
        if not isinstance(pr, dict):
            raise ValueError("Malformed phase item in verifier output")
        pname = pr.get("phase")
        if not isinstance(pname, str):
            raise ValueError("Malformed phase name in verifier output")
        if pname in seen_phases:
            raise ValueError(f"Duplicate phase detected in verifier output: {pname}")
        seen_phases.add(pname)
        phase_names.append(pname)

        succeeded = pr.get("succeeded")
        if succeeded is not True:
            raise ValueError(f"Phase {pname} did not succeed with exact boolean True (got {succeeded})")

    if phase_names != required_phase_order:
        raise ValueError(f"Phase execution order mismatch. Expected {required_phase_order}, got {phase_names}")

    observations = output_data.get("observations")
    if not isinstance(observations, list):
        raise ValueError("Result observations must be a list")

    seen_obs_kinds: set[str] = set()
    obs_by_kind: dict[str, dict[str, Any]] = {}
    for obs in observations:
        if not isinstance(obs, dict):
            raise ValueError("Malformed observation item in verifier output")
        okind = obs.get("kind")
        if not isinstance(okind, str):
            raise ValueError("Malformed observation kind in verifier output")
        if okind in seen_obs_kinds:
            raise ValueError(f"Duplicate observation kind detected: {okind}")
        seen_obs_kinds.add(okind)
        obs_by_kind[okind] = obs

    required_kinds = [
        EvidenceKind.KUBERNETES_READINESS.value,
        EvidenceKind.APPLICATION_RESPONSIVENESS.value,
    ]
    for rk in required_kinds:
        if rk not in obs_by_kind:
            raise ValueError(f"Missing required observation kind: {rk}")

    expected_semantic = EvidenceSemantic.AFFIRMATIVE.value
    for kind, obs in obs_by_kind.items():
        semantic = obs.get("semantic")
        if semantic != expected_semantic:
            raise ValueError(f"Observation {kind} has non-affirmative semantic: {semantic}")

    expected_standing = RecoverabilityTruthStandingValue.RECOVERABILITY_AFFIRMED.value
    knowledge = output_data.get("knowledge", {})
    if knowledge.get("standing") != expected_standing:
        raise ValueError(f"Knowledge standing is not {expected_standing}: {knowledge.get('standing')}")


def verify_kubernetes_resource_ownership(
    kubectl_base_cmd: list[str],
    evidence_dir: str,
    resource_type: str,
    name: str,
    expected_labels: dict[str, str],
) -> tuple[bool, str]:
    cmd = list(kubectl_base_cmd) + ["get", resource_type, name, "-o", "json"]
    res = run_command(cmd, evidence_dir=evidence_dir)
    if res.returncode != 0:
        stderr = res.stderr.lower()
        if "(notfound)" in stderr or 'status 404' in stderr or "notfound" in stderr:
            if "(notfound)" in stderr or "notfound" in stderr:
                return True, "absent"
        if "forbidden" in stderr or "unauthorized" in stderr:
            return False, f"Access denied checking {resource_type}/{name}: {res.stderr}"
        if res.returncode == -124:
            return False, f"Timeout checking {resource_type}/{name}"
        return False, f"Error checking {resource_type}/{name}: {res.stderr}"

    try:
        data = json.loads(res.stdout)
        labels = data.get("metadata", {}).get("labels", {})
    except Exception as exc:
        return False, f"Malformed json reading {resource_type}/{name}: {exc}"

    for k, v in expected_labels.items():
        if labels.get(k) != v:
            return False, f"Ownership label mismatch for {resource_type}/{name}: expected {k}={v}, got {labels.get(k)}"

    return True, "owned"


def extract_cloud_resources_from_state(
    tf_dir: str,
    evidence_dir: str,
    state_file: str,
) -> dict[str, Any]:
    """Extract cloud resource identities directly from the run-specific Terraform state."""
    resources: dict[str, Any] = {
        "cluster_name": None,
        "cluster_arn": None,
        "vpc_id": None,
        "subnet_ids": [],
    }
    if not os.path.isfile(state_file) or os.path.getsize(state_file) == 0:
        return resources

    show_res = run_command(["terraform", "show", "-json", state_file], cwd=tf_dir, evidence_dir=evidence_dir)
    if show_res.returncode != 0:
        return resources

    try:
        data = json.loads(show_res.stdout)
    except Exception:
        return resources

    outputs = data.get("values", {}).get("outputs", {})
    if isinstance(outputs, dict):
        if "cluster_name" in outputs:
            resources["cluster_name"] = outputs["cluster_name"].get("value")
        if "cluster_arn" in outputs:
            resources["cluster_arn"] = outputs["cluster_arn"].get("value")
        if "vpc_id" in outputs:
            resources["vpc_id"] = outputs["vpc_id"].get("value")
        if "subnet_ids" in outputs and isinstance(outputs["subnet_ids"].get("value"), list):
            resources["subnet_ids"] = list(outputs["subnet_ids"]["value"])

    def scan_module(mod: dict[str, Any]) -> None:
        for r in mod.get("resources", []):
            rtype = r.get("type")
            values = r.get("values", {})
            if rtype == "aws_eks_cluster":
                if not resources["cluster_name"]:
                    resources["cluster_name"] = values.get("name") or values.get("id")
                if not resources["cluster_arn"]:
                    resources["cluster_arn"] = values.get("arn")
            elif rtype == "aws_vpc":
                if not resources["vpc_id"]:
                    resources["vpc_id"] = values.get("id")
            elif rtype == "aws_subnet":
                sid = values.get("id")
                if sid and sid not in resources["subnet_ids"]:
                    resources["subnet_ids"].append(sid)

        for child in mod.get("child_modules", []):
            scan_module(child)

    root_module = data.get("values", {}).get("root_module", {})
    if root_module:
        scan_module(root_module)

    return resources


def audit_cloud_residue(
    tf_dir: str,
    evidence_dir: str,
    state_file: str,
    tf_vars: list[str],
    cloud_resources: dict[str, Any],
    aws_region: str,
    max_poll_seconds: float = 120.0,
    poll_interval_seconds: float = 5.0,
) -> tuple[str, list[str]]:
    residue_errors: list[str] = []

    if not state_file or not os.path.isabs(state_file):
        residue_errors.append(f"Invalid or non-absolute run-specific state file provided for residue audit: {state_file}")
        return "ERROR", residue_errors

    default_state_path = os.path.join(tf_dir, "terraform.tfstate")
    if os.path.abspath(state_file) == os.path.abspath(default_state_path):
        residue_errors.append("Residue audit refused: cannot use default terraform.tfstate")
        return "ERROR", residue_errors

    if not cloud_resources or not any(cloud_resources.values()):
        residue_errors.append("Cloud resource inventory is empty; cannot establish clean state without recorded resources")
        return "ERROR", residue_errors

    show_res = run_command(["terraform", "show", "-json", state_file], cwd=tf_dir, evidence_dir=evidence_dir)
    if show_res.returncode != 0:
        residue_errors.append(f"Terraform state query error: {show_res.stderr}")
        return "ERROR", residue_errors

    try:
        state_data = json.loads(show_res.stdout)
        resources = state_data.get("values", {}).get("root_module", {}).get("resources", [])
        child_modules = state_data.get("values", {}).get("root_module", {}).get("child_modules", [])
        if resources or child_modules:
            residue_errors.append("Terraform state still contains active resources or child modules after destroy")
            return "DIRTY", residue_errors
    except Exception as exc:
        residue_errors.append(f"Failed to parse terraform state JSON: {exc}")
        return "ERROR", residue_errors

    plan_cmd = ["terraform", "plan", "-destroy", "-detailed-exitcode", "-no-color"] + tf_vars
    plan_res = run_command(plan_cmd, cwd=tf_dir, evidence_dir=evidence_dir)
    if plan_res.returncode == 2:
        residue_errors.append("Terraform destroy-mode plan detected lingering infrastructure residue")
        return "DIRTY", residue_errors
    elif plan_res.returncode != 0:
        residue_errors.append(f"Terraform plan error during residue audit: {plan_res.stderr}")
        return "ERROR", residue_errors

    start_time = time.monotonic()
    cluster_name = cloud_resources.get("cluster_name")
    if cluster_name:
        while True:
            check_res = run_command(
                ["aws", "eks", "describe-cluster", "--name", cluster_name, "--region", aws_region],
                evidence_dir=evidence_dir,
            )
            if check_res.returncode == 0:
                try:
                    cdata = json.loads(check_res.stdout)
                    cstatus = cdata.get("cluster", {}).get("status", "")
                    if cstatus == "DELETING":
                        if time.monotonic() - start_time > max_poll_seconds:
                            residue_errors.append(f"Cluster {cluster_name} lingered in DELETING status beyond {max_poll_seconds}s deadline")
                            return "DIRTY", residue_errors
                        time.sleep(poll_interval_seconds)
                        continue
                    else:
                        residue_errors.append(f"Cloud residue audit found surviving EKS cluster {cluster_name} in status {cstatus}")
                        return "DIRTY", residue_errors
                except Exception as exc:
                    residue_errors.append(f"Malformed cluster describe json: {exc}")
                    return "ERROR", residue_errors
            else:
                stderr = check_res.stderr
                if "ResourceNotFoundException" in stderr:
                    break
                elif "AccessDeniedException" in stderr or "unauthorized" in stderr.lower():
                    residue_errors.append(f"Residue check access denied for cluster {cluster_name}: {check_res.stderr}")
                    return "ERROR", residue_errors
                elif "timed out" in stderr.lower() or check_res.returncode == -124:
                    residue_errors.append(f"Residue check timed out for cluster {cluster_name}")
                    return "ERROR", residue_errors
                else:
                    residue_errors.append(f"Residue check unexpected error for cluster {cluster_name}: {check_res.stderr}")
                    return "ERROR", residue_errors

    subnet_ids = cloud_resources.get("subnet_ids", [])
    if isinstance(subnet_ids, list) and subnet_ids:
        for subnet_id in subnet_ids:
            check_subnet = run_command(
                ["aws", "ec2", "describe-subnets", "--subnet-ids", subnet_id, "--region", aws_region],
                evidence_dir=evidence_dir,
            )
            if check_subnet.returncode == 0:
                try:
                    sdata = json.loads(check_subnet.stdout)
                    found_subnets = sdata.get("Subnets", [])
                    if found_subnets:
                        residue_errors.append(f"Cloud residue audit found surviving subnet: {subnet_id}")
                        return "DIRTY", residue_errors
                except Exception as exc:
                    residue_errors.append(f"Malformed describe-subnets json for {subnet_id}: {exc}")
                    return "ERROR", residue_errors
            else:
                stderr = check_subnet.stderr
                if "InvalidSubnetID.NotFound" in stderr:
                    continue
                elif "AccessDenied" in stderr or "unauthorized" in stderr.lower():
                    residue_errors.append(f"Residue check access denied for subnet {subnet_id}: {check_subnet.stderr}")
                    return "ERROR", residue_errors
                elif check_subnet.returncode == -124:
                    residue_errors.append(f"Residue check timed out for subnet {subnet_id}")
                    return "ERROR", residue_errors
                else:
                    residue_errors.append(f"Residue check unexpected error for subnet {subnet_id}: {check_subnet.stderr}")
                    return "ERROR", residue_errors

    vpc_id = cloud_resources.get("vpc_id")
    if vpc_id:
        check_vpc = run_command(
            ["aws", "ec2", "describe-vpcs", "--vpc-ids", vpc_id, "--region", aws_region],
            evidence_dir=evidence_dir,
        )
        if check_vpc.returncode == 0:
            try:
                vpcs = json.loads(check_vpc.stdout).get("Vpcs", [])
                if vpcs:
                    residue_errors.append(f"Cloud residue audit found surviving VPC: {vpc_id}")
                    return "DIRTY", residue_errors
            except Exception as exc:
                residue_errors.append(f"Malformed describe-vpcs json: {exc}")
                return "ERROR", residue_errors
        else:
            stderr = check_vpc.stderr
            if "InvalidVpcID.NotFound" in stderr:
                pass
            elif "AccessDenied" in stderr or "unauthorized" in stderr.lower():
                residue_errors.append(f"Residue check access denied for VPC {vpc_id}: {check_vpc.stderr}")
                return "ERROR", residue_errors
            elif "timed out" in stderr.lower() or check_vpc.returncode == -124:
                residue_errors.append(f"Residue check timed out for VPC {vpc_id}")
                return "ERROR", residue_errors
            else:
                residue_errors.append(f"Residue check unexpected error for VPC {vpc_id}: {check_vpc.stderr}")
                return "ERROR", residue_errors

    return "CLEAN", residue_errors


def write_summary(
    evidence_dir: str,
    run_id: str,
    stages: dict[str, str],
    details: dict[str, Any] | None = None,
    errors: list[str] | None = None,
) -> None:
    os.makedirs(evidence_dir, exist_ok=True)
    sanitized_details = dict(details or {})
    if "cloud_resources" in sanitized_details:
        cr = sanitized_details["cloud_resources"]
        sanitized_details["cloud_resources"] = {
            "cluster_name": cr.get("cluster_name"),
            "vpc_id": cr.get("vpc_id"),
            "subnet_ids_count": len(cr.get("subnet_ids", [])) if isinstance(cr.get("subnet_ids"), list) else 0,
        }

    data = {
        "run_id": run_id,
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "stages": stages,
        "errors": errors or [],
        "details": sanitized_details,
        "statement": "No production recoverability decision was made.",
    }
    with open(os.path.join(evidence_dir, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

    with open(os.path.join(evidence_dir, "summary.md"), "w", encoding="utf-8") as f:
        f.write("# Brace Platform Public Demonstration Summary\n\n")
        f.write(f"- **Run ID**: `{run_id}`\n")
        f.write(f"- **Timestamp**: `{data['timestamp']}`\n\n")
        f.write("## Lifecycle Stages\n\n")
        for stage, status in stages.items():
            f.write(f"- **{stage}**: `{status}`\n")
        if errors:
            f.write("\n## Recorded Errors\n\n")
            for err in errors:
                f.write(f"- {err}\n")
        f.write("\n## Non-Production Recoverability Notice\n\n")
        f.write("> No production recoverability decision was made.\n\n")
        f.write("This run exercises public orchestration and observation mechanics against a public sample workload.\n")


def execute_public_live_demo(
    workload_image: str | None = None,
    verifier_image: str | None = None,
    repo_root: str | None = None,
    dry_run: bool = False,
    aws_region: str = "ap-south-1",
) -> int:
    root = repo_root or os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    if repo_root is not None and not os.path.isdir(repo_root):
        raise ValueError(f"Requested repository path does not exist: {repo_root}")

    tf_dir = os.path.join(root, "infra", "terraform")
    k8s_dir = os.path.join(root, "k8s", "verifier")
    if not dry_run:
        if not os.path.isdir(tf_dir):
            raise RuntimeError(f"Missing required Terraform directory: {tf_dir}")
        if not os.path.isdir(k8s_dir):
            raise RuntimeError(f"Missing required Kubernetes manifests directory: {k8s_dir}")

    run_id = str(uuid.uuid4())
    evidence_dir = os.path.join(root, ".demo", "evidence", run_id)
    os.makedirs(evidence_dir, exist_ok=True)

    if dry_run:
        workload_image = workload_image or ("ghcr.io/brace-demo/workload@sha256:" + ("0" * 64))
        verifier_image = verifier_image or ("ghcr.io/brace-demo/verifier@sha256:" + ("0" * 64))
        validate_image_digest_pinning(workload_image, allow_placeholder=True)
        validate_image_digest_pinning(verifier_image, allow_placeholder=True)
    else:
        if not workload_image:
            raise ValueError("--workload-image must be provided for live demonstration execution")
        if not verifier_image:
            raise ValueError("--verifier-image must be provided for live demonstration execution")
        validate_image_digest_pinning(workload_image, allow_placeholder=False)
        validate_image_digest_pinning(verifier_image, allow_placeholder=False)

    stages = {
        "VALIDATION": "PASS",
        "PROVISION": "PENDING",
        "KUBECONFIG": "PENDING",
        "IN_CLUSTER_VERIFICATION": "PENDING",
        "RESULT_VALIDATION": "PENDING",
        "K8S_CLEANUP": "PENDING",
        "TEARDOWN": "PENDING",
        "RESIDUE_AUDIT": "PENDING",
        "OVERALL_RESULT": "RUNNING",
    }
    errors: list[str] = []

    if dry_run:
        stages["PROVISION"] = "SIMULATED"
        stages["KUBECONFIG"] = "SIMULATED"
        stages["IN_CLUSTER_VERIFICATION"] = "SIMULATED"
        stages["RESULT_VALIDATION"] = "SIMULATED"
        stages["K8S_CLEANUP"] = "SIMULATED"
        stages["TEARDOWN"] = "SIMULATED"
        stages["RESIDUE_AUDIT"] = "SIMULATED_CLEAN"
        stages["OVERALL_RESULT"] = "PASS"
        write_summary(evidence_dir, run_id, stages, {
            "dry_run": True,
            "workload_image": workload_image,
            "verifier_image": verifier_image,
        })
        print(f"Public Live Demo dry-run completed successfully. Run ID: {run_id}")
        return 0

    applied_provision = False
    applied_k8s = False
    kubeconfig_path: str | None = None
    cloud_resources: dict[str, Any] = {}

    unique_cluster_name = f"brace-demo-{run_id[:8]}"
    state_file = os.path.join(evidence_dir, "terraform.tfstate")

    sa_name = f"brace-verifier-{run_id[:8]}"
    cr_name = f"brace-verifier-{run_id[:8]}"
    crb_name = f"brace-verifier-{run_id[:8]}"
    cm_name = f"brace-request-{run_id[:8]}"
    job_name = f"brace-verifier-{run_id[:8]}"
    pod_name = f"brace-verifier-pod-{run_id[:8]}"

    expected_labels = {
        "app.kubernetes.io/managed-by": "brace",
        "brace.io/run-id": run_id,
    }

    try:
        existing_state = os.path.join(tf_dir, "terraform.tfstate")
        if os.path.exists(existing_state) and os.path.getsize(existing_state) > 0:
            raise RuntimeError(f"Refusing to execute: Unrelated pre-existing Terraform state found at {existing_state}")

        print(f"Initializing demonstration infrastructure in {tf_dir}...")
        run_command(["terraform", "init", "-no-color"], cwd=tf_dir, evidence_dir=evidence_dir, check=True)
        applied_provision = True

        tf_vars = [
            f"-var=aws_region={aws_region}",
            f"-var=cluster_name={unique_cluster_name}",
            f"-var=run_id={run_id}",
            f"-state={state_file}",
        ]

        print(f"Provisioning demonstration infrastructure in region {aws_region}...")
        run_command(
            ["terraform", "apply", "-auto-approve", "-no-color"] + tf_vars,
            cwd=tf_dir,
            evidence_dir=evidence_dir,
            timeout=TIMEOUT_TERRAFORM_APPLY,
            check=True,
        )
        stages["PROVISION"] = "PASS"

        out_res = run_command(["terraform", "output", f"-state={state_file}", "-json"], cwd=tf_dir, evidence_dir=evidence_dir, check=True)
        tf_outputs = json.loads(out_res.stdout)
        cloud_resources = {
            "cluster_name": tf_outputs.get("cluster_name", {}).get("value"),
            "cluster_arn": tf_outputs.get("cluster_arn", {}).get("value"),
            "vpc_id": tf_outputs.get("vpc_id", {}).get("value"),
            "subnet_ids": tf_outputs.get("subnet_ids", {}).get("value"),
        }

        cluster_name = cloud_resources.get("cluster_name")
        if not cluster_name:
            raise RuntimeError("Terraform output missing cluster_name")

        kubeconfig_path = os.path.join(evidence_dir, "run-scoped-kubeconfig")
        run_command(
            [
                "aws", "eks", "update-kubeconfig",
                "--name", cluster_name,
                "--region", aws_region,
                "--kubeconfig", kubeconfig_path,
            ],
            evidence_dir=evidence_dir,
            check=True,
        )
        stages["KUBECONFIG"] = "PASS"

        print("Rendering and applying run-scoped RBAC...")
        rbac_template_path = os.path.join(k8s_dir, "rbac.yaml")
        with open(rbac_template_path, "r", encoding="utf-8") as f:
            rbac_content = f.read()
        rendered_rbac = (
            rbac_content
            .replace("${VERIFIER_SERVICE_ACCOUNT}", sa_name)
            .replace("${VERIFIER_CLUSTER_ROLE}", cr_name)
            .replace("${VERIFIER_CLUSTER_ROLE_BINDING}", crb_name)
            .replace("${RUN_ID}", run_id)
        )
        run_command(
            ["kubectl", "--kubeconfig", kubeconfig_path, "apply", "-f", "-"],
            evidence_dir=evidence_dir,
            input_data=rendered_rbac,
            check=True,
        )
        applied_k8s = True

        print("Creating verification request ConfigMap...")
        request_payload = build_verification_request_payload(run_id, workload_image)
        request_json_str = json.dumps(request_payload)

        cm_gen = run_command(
            [
                "kubectl", "--kubeconfig", kubeconfig_path, "create", "configmap", cm_name,
                f"--from-literal=request.json={request_json_str}",
                "-o", "json", "--dry-run=client",
            ],
            evidence_dir=evidence_dir,
            check=True,
        )
        cm_obj = json.loads(cm_gen.stdout)
        if "metadata" not in cm_obj:
            cm_obj["metadata"] = {}
        if "labels" not in cm_obj["metadata"]:
            cm_obj["metadata"]["labels"] = {}
        cm_obj["metadata"]["labels"]["app.kubernetes.io/managed-by"] = "brace"
        cm_obj["metadata"]["labels"]["brace.io/run-id"] = run_id

        run_command(
            ["kubectl", "--kubeconfig", kubeconfig_path, "apply", "-f", "-"],
            evidence_dir=evidence_dir,
            input_data=json.dumps(cm_obj),
            check=True,
        )

        print(f"Launching in-cluster verifier Job using image {verifier_image}...")
        job_template_path = os.path.join(k8s_dir, "job-template.yaml")
        with open(job_template_path, "r", encoding="utf-8") as f:
            job_template = f.read()
        rendered_job = (
            job_template
            .replace("${JOB_NAME}", job_name)
            .replace("${POD_NAME}", pod_name)
            .replace("${VERIFIER_SERVICE_ACCOUNT}", sa_name)
            .replace("${CONFIGMAP_NAME}", cm_name)
            .replace("${VERIFIER_IMAGE}", verifier_image)
            .replace("${RUN_ID}", run_id)
        )
        run_command(
            ["kubectl", "--kubeconfig", kubeconfig_path, "apply", "-f", "-"],
            evidence_dir=evidence_dir,
            input_data=rendered_job,
            check=True,
        )

        print("Waiting for in-cluster verifier Job to complete...")
        wait_res = run_command(
            ["kubectl", "--kubeconfig", kubeconfig_path, "wait", "--for=condition=complete", "--timeout=600s", f"job/{job_name}"],
            evidence_dir=evidence_dir,
            timeout=TIMEOUT_KUBECTL_WAIT + 30.0,
            check=False,
        )
        if wait_res.returncode != 0:
            run_command(["kubectl", "--kubeconfig", kubeconfig_path, "get", "pods", f"--selector=job-name={job_name}"], evidence_dir=evidence_dir)
            run_command(["kubectl", "--kubeconfig", kubeconfig_path, "describe", f"job/{job_name}"], evidence_dir=evidence_dir)
            run_command(["kubectl", "--kubeconfig", kubeconfig_path, "logs", f"job/{job_name}"], evidence_dir=evidence_dir)
            raise RuntimeError(f"Verifier Job wait failed: {wait_res.stderr or wait_res.stdout}")

        stages["IN_CLUSTER_VERIFICATION"] = "PASS"

        print("Retrieving in-cluster verification result...")
        logs_res = run_command(
            ["kubectl", "--kubeconfig", kubeconfig_path, "logs", f"job/{job_name}"],
            evidence_dir=evidence_dir,
            check=True,
        )
        try:
            output_data = json.loads(logs_res.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Failed to parse verifier output JSON from Job logs: {logs_res.stdout}") from exc

        with open(os.path.join(evidence_dir, "verifier-result.json"), "w", encoding="utf-8") as f:
            json.dump(output_data, f, indent=2)

        validate_verification_result(
            output_data=output_data,
            expected_run_id=run_id,
            expected_evaluation_id=request_payload["evaluation_id"],
        )
        stages["RESULT_VALIDATION"] = "PASS"

    except Exception as exc:
        print(f"Error during demonstration run: {exc}", file=sys.stderr)
        errors.append(f"Demonstration execution failure: {exc}")
        stages["OVERALL_RESULT"] = "FAIL"
    finally:
        tf_destroy_vars = [
            f"-var=aws_region={aws_region}",
            f"-var=cluster_name={unique_cluster_name}",
            f"-var=run_id={run_id}",
            f"-state={state_file}",
        ]
        try:
            if applied_k8s and kubeconfig_path and os.path.exists(kubeconfig_path):
                print("Cleaning up verification Job, ConfigMap and RBAC manifests...")
                k8s_cleanup_failed = False
                kbase = ["kubectl", "--kubeconfig", kubeconfig_path]

                objects_to_clean = [
                    ("namespace", f"brace-run-{uuid.UUID(run_id).hex}"),
                    ("job", job_name),
                    ("configmap", cm_name),
                    ("clusterrolebinding", crb_name),
                    ("clusterrole", cr_name),
                    ("serviceaccount", sa_name),
                ]

                for rtype, rname in objects_to_clean:
                    owned, msg = verify_kubernetes_resource_ownership(
                        kbase, evidence_dir, rtype, rname, expected_labels
                    )
                    if not owned:
                        k8s_cleanup_failed = True
                        errors.append(f"Kubernetes ownership verification failed for {rtype}/{rname}: {msg}")
                    else:
                        del_res = run_command(kbase + ["delete", rtype, rname, "--ignore-not-found"], evidence_dir=evidence_dir)
                        if del_res.returncode != 0:
                            k8s_cleanup_failed = True
                            errors.append(f"Failed to delete {rtype}/{rname}: {del_res.stderr}")

                stages["K8S_CLEANUP"] = "FAIL" if k8s_cleanup_failed else "PASS"
                if k8s_cleanup_failed:
                    stages["OVERALL_RESULT"] = "FAIL"
        except Exception as exc:
            print(f"Error during Kubernetes cleanup: {exc}", file=sys.stderr)
            errors.append(f"Kubernetes cleanup error: {exc}")
            stages["K8S_CLEANUP"] = "FAIL"
            stages["OVERALL_RESULT"] = "FAIL"
        finally:
            try:
                if applied_provision:
                    if os.path.exists(state_file):
                        extracted = extract_cloud_resources_from_state(tf_dir, evidence_dir, state_file)
                        for k, v in extracted.items():
                            if v and not cloud_resources.get(k):
                                cloud_resources[k] = v

                    print(f"Tearing down demonstration infrastructure in region {aws_region}...")
                    try:
                        run_command(
                            ["terraform", "destroy", "-auto-approve", "-no-color"] + tf_destroy_vars,
                            cwd=tf_dir,
                            evidence_dir=evidence_dir,
                            timeout=TIMEOUT_TERRAFORM_DESTROY,
                            check=True,
                        )
                        stages["TEARDOWN"] = "PASS"
                    except Exception as exc:
                        print(f"Error during Terraform destroy: {exc}", file=sys.stderr)
                        errors.append(f"Terraform destroy failure: {exc}")
                        stages["TEARDOWN"] = "FAIL"
                        stages["OVERALL_RESULT"] = "FAIL"
                else:
                    stages["TEARDOWN"] = "SKIP"
            finally:
                try:
                    if applied_provision:
                        print("Auditing cloud residue...")
                        residue_status, residue_errs = audit_cloud_residue(
                            tf_dir=tf_dir,
                            evidence_dir=evidence_dir,
                            state_file=state_file,
                            tf_vars=tf_destroy_vars,
                            cloud_resources=cloud_resources,
                            aws_region=aws_region,
                        )
                        stages["RESIDUE_AUDIT"] = residue_status
                        if residue_errs:
                            errors.extend(residue_errs)
                        if residue_status != "CLEAN":
                            stages["OVERALL_RESULT"] = "FAIL"
                    else:
                        stages["RESIDUE_AUDIT"] = "CLEAN"
                except Exception as exc:
                    print(f"Error during residue audit: {exc}", file=sys.stderr)
                    errors.append(f"Residue audit exception: {exc}")
                    stages["RESIDUE_AUDIT"] = "ERROR"
                    stages["OVERALL_RESULT"] = "FAIL"
                finally:
                    if stages["OVERALL_RESULT"] == "RUNNING":
                        stages["OVERALL_RESULT"] = "PASS"

                    write_summary(evidence_dir, run_id, stages, {
                        "workload_image": workload_image,
                        "verifier_image": verifier_image,
                        "aws_region": aws_region,
                        "cloud_resources": cloud_resources,
                    }, errors=errors)

    return 0 if stages["OVERALL_RESULT"] == "PASS" else 1


def main() -> None:
    parser = argparse.ArgumentParser(description="Brace Platform Public Live Demonstration Runner")
    parser.add_argument("--workload-image", help="Digest-pinned workload image")
    parser.add_argument("--verifier-image", help="Digest-pinned verifier image")
    parser.add_argument("--aws-region", default="ap-south-1", help="Explicit AWS region (default: ap-south-1)")
    parser.add_argument("--repo-root", help="Explicit path to public repository checkout")
    parser.add_argument("--dry-run", action="store_true", help="Simulate execution without cloud mutations")
    args = parser.parse_args()

    sys.exit(
        execute_public_live_demo(
            workload_image=args.workload_image,
            verifier_image=args.verifier_image,
            repo_root=args.repo_root,
            dry_run=args.dry_run,
            aws_region=args.aws_region,
        )
    )


if __name__ == "__main__":
    main()
