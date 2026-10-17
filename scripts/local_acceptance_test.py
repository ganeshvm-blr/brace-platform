from __future__ import annotations

import json
import os
import pathlib
import re
import subprocess
import sys
import time
import uuid

import yaml

_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import scripts.live_demo as live_demo


def main() -> int:
    evidence_dir = os.path.abspath(os.path.join(_REPO_ROOT, ".demo", "evidence", "local-cluster-acceptance"))
    os.makedirs(evidence_dir, exist_ok=True)

    cluster_name = "brace-local-acc-test"
    isolated_kubeconfig = os.path.join(evidence_dir, "isolated-kubeconfig")
    run_id = str(uuid.uuid4())

    print(f"Starting dedicated local Kubernetes acceptance proof [Run ID: {run_id}]...")
    print(f"Isolated kubeconfig: {isolated_kubeconfig}")

    kbase = ["kubectl", "--kubeconfig", isolated_kubeconfig]

    workload_img = "brace-demo-workload:local"
    verifier_img = "brace-verifier:local"

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
        print(f"Creating dedicated kind cluster {cluster_name}...")
        subprocess.run(
            [
                "kind", "create", "cluster",
                "--name", cluster_name,
                "--kubeconfig", isolated_kubeconfig,
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        print("Dedicated cluster created successfully.")

        nodes_res = subprocess.run(kbase + ["get", "nodes", "-o", "wide"], check=True, capture_output=True, text=True)
        print("Cluster nodes:\n" + nodes_res.stdout)

        print("Loading local images into kind cluster...")
        subprocess.run(["kind", "load", "docker-image", workload_img, "--name", cluster_name], check=True)
        subprocess.run(["kind", "load", "docker-image", verifier_img, "--name", cluster_name], check=True)

        workload_inspect = subprocess.run(["docker", "inspect", workload_img, "--format", "{{.Id}}"], capture_output=True, text=True, check=True)
        raw_digest = workload_inspect.stdout.strip()
        if raw_digest.startswith("sha256:"):
            digest_hex = raw_digest.split("sha256:")[1]
        else:
            digest_hex = raw_digest

        node_name = f"{cluster_name}-control-plane"
        pinned_workload_img = f"docker.io/library/brace-demo-workload@sha256:{digest_hex}"

        print(f"Registering digest-pinned image {pinned_workload_img} in containerd...")
        subprocess.run([
            "docker", "exec", node_name, "ctr", "-n", "k8s.io", "images", "tag",
            f"docker.io/library/{workload_img}",
            pinned_workload_img,
        ], check=True)
        print("Images loaded and tagged successfully.")

        print("Rendering and validating RBAC manifest...")
        rbac_template_path = os.path.join(_REPO_ROOT, "k8s", "verifier", "rbac.yaml")
        with open(rbac_template_path, "r", encoding="utf-8") as f:
            rbac_template = f.read()

        rendered_rbac = (
            rbac_template
            .replace("${VERIFIER_SERVICE_ACCOUNT}", sa_name)
            .replace("${VERIFIER_CLUSTER_ROLE}", cr_name)
            .replace("${VERIFIER_CLUSTER_ROLE_BINDING}", crb_name)
            .replace("${RUN_ID}", run_id)
        )

        if len(rendered_rbac) > 5000:
            raise ValueError(f"Rendered RBAC unexpectedly large: {len(rendered_rbac)} bytes")
        if re.search(r"\$\{[A-Z0-9_]+\}", rendered_rbac):
            raise ValueError(f"Unresolved placeholders detected in rendered RBAC: {rendered_rbac}")

        rbac_docs = list(yaml.safe_load_all(rendered_rbac))
        if len(rbac_docs) != 3:
            raise ValueError(f"Expected 3 YAML documents in RBAC, got {len(rbac_docs)}")

        sa_doc, cr_doc, crb_doc = rbac_docs
        assert sa_doc["kind"] == "ServiceAccount" and sa_doc["metadata"]["name"] == sa_name
        assert sa_doc["metadata"]["labels"] == expected_labels
        assert cr_doc["kind"] == "ClusterRole" and cr_doc["metadata"]["name"] == cr_name
        assert cr_doc["metadata"]["labels"] == expected_labels
        assert crb_doc["kind"] == "ClusterRoleBinding" and crb_doc["metadata"]["name"] == crb_name
        assert crb_doc["metadata"]["labels"] == expected_labels
        assert crb_doc["roleRef"]["name"] == cr_name
        assert crb_doc["subjects"][0]["name"] == sa_name

        with open(os.path.join(evidence_dir, "rendered-rbac.yaml"), "w", encoding="utf-8") as f:
            f.write(rendered_rbac)

        subprocess.run(kbase + ["apply", "-f", "-"], input=rendered_rbac, text=True, check=True)
        print("RBAC applied successfully.")

        print("Rendering and validating ConfigMap manifest...")
        request_payload = live_demo.build_verification_request_payload(run_id, pinned_workload_img)
        with open(os.path.join(evidence_dir, "request-payload.json"), "w", encoding="utf-8") as f:
            json.dump(request_payload, f, indent=2)

        request_json_str = json.dumps(request_payload)
        cm_gen = subprocess.run(
            [
                "kubectl", "--kubeconfig", isolated_kubeconfig, "create", "configmap", cm_name,
                f"--from-literal=request.json={request_json_str}",
                "-o", "json", "--dry-run=client",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        cm_obj = json.loads(cm_gen.stdout)
        cm_obj.setdefault("metadata", {}).setdefault("labels", {})
        cm_obj["metadata"]["labels"]["app.kubernetes.io/managed-by"] = "brace"
        cm_obj["metadata"]["labels"]["brace.io/run-id"] = run_id

        assert cm_obj["metadata"]["name"] == cm_name
        assert cm_obj["metadata"]["labels"] == expected_labels
        cm_inner_req = json.loads(cm_obj["data"]["request.json"])
        assert cm_inner_req["run_id"] == run_id
        assert cm_inner_req["bundle"]["deployment"]["container"]["image"] == pinned_workload_img

        rendered_cm = json.dumps(cm_obj, indent=2)
        with open(os.path.join(evidence_dir, "rendered-configmap.json"), "w", encoding="utf-8") as f:
            f.write(rendered_cm)

        subprocess.run(kbase + ["apply", "-f", "-"], input=rendered_cm, text=True, check=True)
        print("ConfigMap applied successfully.")

        print("Rendering and validating Job manifest...")
        job_template_path = os.path.join(_REPO_ROOT, "k8s", "verifier", "job-template.yaml")
        with open(job_template_path, "r", encoding="utf-8") as f:
            job_template = f.read()

        rendered_job = (
            job_template
            .replace("${JOB_NAME}", job_name)
            .replace("${POD_NAME}", pod_name)
            .replace("${VERIFIER_SERVICE_ACCOUNT}", sa_name)
            .replace("${CONFIGMAP_NAME}", cm_name)
            .replace("${VERIFIER_IMAGE}", verifier_img)
            .replace("${RUN_ID}", run_id)
        )

        if len(rendered_job) > 5000:
            raise ValueError(f"Rendered Job manifest unexpectedly large: {len(rendered_job)} bytes")
        if re.search(r"\$\{[A-Z0-9_]+\}", rendered_job):
            raise ValueError(f"Unresolved placeholders detected in rendered Job: {rendered_job}")

        job_doc = yaml.safe_load(rendered_job)
        assert job_doc["kind"] == "Job"
        assert job_doc["metadata"]["name"] == job_name
        assert job_doc["metadata"]["labels"] == expected_labels
        pod_spec = job_doc["spec"]["template"]["spec"]
        assert pod_spec["serviceAccountName"] == sa_name
        assert pod_spec["containers"][0]["image"] == verifier_img
        assert job_doc["spec"]["template"]["metadata"]["labels"] == expected_labels

        with open(os.path.join(evidence_dir, "rendered-job.yaml"), "w", encoding="utf-8") as f:
            f.write(rendered_job)

        subprocess.run(kbase + ["apply", "-f", "-"], input=rendered_job, text=True, check=True)
        print("Job submitted successfully.")

        print("Waiting for verifier Job to complete (up to 300s)...")
        wait_res = subprocess.run(
            kbase + ["wait", "--for=condition=complete", "--timeout=300s", f"job/{job_name}"],
            capture_output=True,
            text=True,
        )

        pods_status = subprocess.run(kbase + ["get", "pods", "-A", "-o", "wide"], capture_output=True, text=True).stdout
        with open(os.path.join(evidence_dir, "pods-status.txt"), "w", encoding="utf-8") as f:
            f.write(pods_status)

        job_desc = subprocess.run(kbase + ["describe", f"job/{job_name}"], capture_output=True, text=True).stdout
        with open(os.path.join(evidence_dir, "job-describe.txt"), "w", encoding="utf-8") as f:
            f.write(job_desc)

        logs_res = subprocess.run(kbase + ["logs", f"job/{job_name}"], capture_output=True, text=True)
        with open(os.path.join(evidence_dir, "verifier-stdout.log"), "w", encoding="utf-8") as f:
            f.write(logs_res.stdout)
        with open(os.path.join(evidence_dir, "verifier-stderr.log"), "w", encoding="utf-8") as f:
            f.write(logs_res.stderr)

        if wait_res.returncode != 0:
            print("Verifier Job wait failed!", file=sys.stderr)
            print("wait stderr:", wait_res.stderr, file=sys.stderr)
            print("verifier stdout:", logs_res.stdout, file=sys.stderr)
            print("verifier stderr:", logs_res.stderr, file=sys.stderr)
            return 1

        print("Verifier Job completed successfully.")

        print("Validating verifier stdout through production validator...")
        output_data = json.loads(logs_res.stdout)
        with open(os.path.join(evidence_dir, "validated-result.json"), "w", encoding="utf-8") as f:
            json.dump(output_data, f, indent=2)

        live_demo.validate_verification_result(
            output_data=output_data,
            expected_run_id=run_id,
            expected_evaluation_id=request_payload["evaluation_id"],
        )
        print("Production result validation passed: affirmative recoverability standing and all required phases affirmed.")

        print("Cleaning up Kubernetes resources with ownership verification...")
        target_ns = f"brace-run-{uuid.UUID(run_id).hex}"
        objects_to_clean = [
            ("namespace", target_ns),
            ("job", job_name),
            ("configmap", cm_name),
            ("clusterrolebinding", crb_name),
            ("clusterrole", cr_name),
            ("serviceaccount", sa_name),
        ]
        cleanup_log = []
        for rtype, rname in objects_to_clean:
            owned, msg = live_demo.verify_kubernetes_resource_ownership(
                kbase, evidence_dir, rtype, rname, expected_labels
            )
            del_res = subprocess.run(kbase + ["delete", rtype, rname, "--ignore-not-found"], capture_output=True, text=True)
            cleanup_log.append(f"{rtype}/{rname}: owned={owned} ({msg}), delete_exit={del_res.returncode}")

        with open(os.path.join(evidence_dir, "cleanup-log.txt"), "w", encoding="utf-8") as f:
            f.write("\n".join(cleanup_log) + "\n")
        print("Kubernetes cleanup completed.")

    finally:
        print(f"Deleting dedicated kind cluster {cluster_name}...")
        subprocess.run(["kind", "delete", "cluster", "--name", cluster_name, "--kubeconfig", isolated_kubeconfig], check=False)
        clusters_check = subprocess.run(["kind", "get", "clusters"], capture_output=True, text=True)
        if cluster_name in clusters_check.stdout:
            print(f"WARNING: Cluster {cluster_name} still listed in kind clusters:\n{clusters_check.stdout}", file=sys.stderr)
        else:
            print(f"Confirmed removal of kind cluster {cluster_name}.")

    print("Dedicated local Kubernetes acceptance proof succeeded completely.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
