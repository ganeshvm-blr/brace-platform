# Public Demonstration Freeze Record

- **Status**: `PUBLIC AWS LIFECYCLE PASS`
- **Milestone**: Verified Public Demonstration Lifecycle
- **Commit**: `e8329b6d2150e1bcba787d96aa8e4dd238561c49`
- **Run ID**: `895844a6-afb2-45e1-a2c6-30c1229dc844`
- **AWS Region**: `ap-south-1`
- **Node Instance Type**: `t3.small`
- **Architecture Reference**: [assets/brace-platform-architecture.png](assets/brace-platform-architecture.png)

---

## 1. Digest-Pinned Demonstration Images

The public demonstration images were built from the exact repository baseline, published to GitHub Container Registry, and pulled by digest:

- **Workload Image**:
  `ghcr.io/ganeshvm-blr/brace-platform-demo-workload@sha256:588c07b235103a9f73d5f069418c1b1e2966c57245fd4be4bab8658053a5650a`
- **Verifier Image**:
  `ghcr.io/ganeshvm-blr/brace-platform-verifier@sha256:d06d70a5b892705ad537f05eb246d8171cc236d52310912b800789bc84509fc8`

---

## 2. Lifecycle Execution Results

| Stage | Status | Observation |
|---|:---:|---|
| **VALIDATION** | `PASS` | Image digests verified; node instance eligibility confirmed (`FreeTierEligible=true`, `x86_64`, offered). |
| **PROVISION** | `PASS` | Terraform VPC and EKS cluster converged within bounded apply timeout. |
| **KUBECONFIG** | `PASS` | Run-scoped isolated kubeconfig established. |
| **IN_CLUSTER_VERIFICATION** | `PASS` | In-cluster verifier Job launched and completed successfully. |
| **RESULT_VALIDATION** | `PASS` | Output schema, phase sequence, and required disclaimer verified. |
| **K8S_CLEANUP** | `PASS` | Run-scoped Namespace, Job, ConfigMap, ServiceAccount, and RBAC deleted under ownership labels. |
| **TEARDOWN** | `PASS` | Pre-destroy node-group guard passed; Terraform resources cleanly destroyed (22 destroyed, 0 errors). |
| **RESIDUE_AUDIT** | `CLEAN` | Post-destroy audit confirmed zero surviving run-owned resources. |
| **OVERALL_RESULT** | `PASS` | Full lifecycle executed to completion with runner exit code `0`. |

---

## 3. Real Operational Observations

The in-cluster verifier executed against the live cluster deployment and recorded the following affirmative observations:

1. **Kubernetes Readiness**:
   - `kind`: `kubernetes_readiness`
   - `semantic`: `affirmative`
   - `detail`: Deployment `demo-workload` converged to ready status within the bounded deadline.

2. **Application Responsiveness**:
   - `kind`: `application_responsiveness`
   - `semantic`: `affirmative`
   - `detail`: HTTP request to the in-cluster service returned HTTP status `200`.

---

## 4. Evidence Boundary

- **Sanitization**: This record intentionally excludes AWS account IDs, ARNs, VPC IDs, subnet IDs, security-group IDs, NAT gateway IDs, KMS key IDs, raw kubeconfigs, Terraform state files, internal logs, and cloud credentials.
- **Local Isolation**: Raw diagnostic transcripts, subprocess stdout/stderr recordings, and local state files remain exclusively in the local `.demo/evidence/` directory and are untracked and excluded from Git.

---

## 5. Accuracy Boundary

- **Operational Reality**: Kubernetes deployment convergence, endpoint HTTP responsiveness, ownership-scoped teardown, and cloud residue elimination were genuinely observed on a live AWS EKS cluster.
- **Demonstration Scope**: The public evaluator is an illustrative demonstration double returning affirmative standing.
- **No Proprietary Logic**: This public package does not include the proprietary Brace evaluator engine, scoring logic, or decision pipeline.
- **Disclaimer**: *No production recoverability decision was made.*
- **No Parity Claim**: This public workload observation demonstration does not claim parity with the private Brace Backup/Restore lifecycle, Velero integration, or production recovery validation.

---

## 6. Milestone Closure

- **Milestone Complete**: The public demonstration lifecycle has successfully validated end-to-end orchestration, in-cluster observation, cleanup safety, and residue verification on AWS.
- **Finality**: No further AWS runs are required for this public demonstration milestone.
- **Future Changes**: Any future modifications to orchestration, Terraform definitions, or container images must be independently validated against their exact commit and published digests.
