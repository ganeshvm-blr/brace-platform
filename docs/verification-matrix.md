# Brace Platform Verification Matrix

This matrix maps each architectural and operational claim made by the public platform to its concrete implementation, verification evidence, and verification boundaries.

---

## 1. Executable Coordinator and Package Tests

The coordinator is tested using production-shaped in-process test doubles that execute all lifecycle paths, boundary handoffs, and error states.

| Public Claim | Implementation Reference | Exact Test Name | Evidence Level | What It Establishes | What It Does Not Establish |
|---|---|---|---|---|---|
| **Local Simulated Orchestration** | `src/brace/demo.py`, `src/brace/_runtime_owned_verification.py` | `test_demo_run_default_output_and_result`, `test_demo_cli_invocation_and_clean_import` | Executable Unit Test | Proves the public package sequences the 7-phase coordination chain locally using in-process test doubles; outputs required disclaimers. | Does not contact a live cluster, create AWS resources, or make network calls. |
| **Exact 7-Phase Execution Sequence** | `src/brace/_runtime_owned_verification.py` (`coordinate`) | `test_successful_chain_exact_phase_order_and_single_cleanup` | Executable Unit Test | Sequences `namespace_create`, `workload_apply`, `readiness_observe`, `responsiveness_observe`, `observation_project`, `reasoning_boundary`, and `namespace_cleanup` in order, recording each phase. | Does not verify live Kubernetes cluster execution; adapters and observers are in-process doubles. |
| **Input Identity Continuity** | `src/brace/_runtime_owned_verification.py` (`_validate_input`) | `test_event_workload_id_mismatch_rejected_before_io`, `test_namespace_run_id_mismatch_rejected`, `test_ownership_run_id_mismatch_rejected`, `test_ownership_workload_id_mismatch_rejected`, `test_ownership_bundle_mismatch_rejected` | Executable Unit Test | Rejects mismatched workload, event, namespace, ownership, and bundle identifiers before invoking any operational adapter. | Does not establish database or external metadata store integrity. |
| **Evaluator Return Type Validation** | `src/brace/_runtime_owned_verification.py` (`coordinate`) | `test_invalid_evaluator_return_type_rejected_and_cleans_up` | Executable Unit Test | Verifies that an evaluator returning an object not an instance of `RecoverabilityKnowledge` is rejected with `TypeError` and triggers cleanup. | Does not validate the mathematical or semantic validity of the evaluator internal reasoning logic. |
| **Evaluator Subject Continuity** | `src/brace/_runtime_owned_verification.py` (`coordinate`) | `test_evaluator_correct_subject_accepted`, `test_evaluator_unrelated_subject_rejected_and_cleans_up` | Executable Unit Test | Validates that `knowledge.subject.subject_id.value` matches `protected_workload.workload_id`; rejects unrelated subjects with `ValueError` and invokes cleanup. | Does not prove external evaluator authority, correctness, or authenticity. |
| **Primary Failure and Cleanup Error Preservation** | `src/brace/_runtime_owned_verification.py` (`_attempt_cleanup_after_failure`) | `test_cleanup_failure_after_primary_failure_is_preserved` | Executable Unit Test | When an operational phase fails and cleanup also fails, `VerificationCoordinationFailed` preserves the primary exception as the main cause and stores the cleanup exception on `cleanup_failure`. | Does not guarantee external infrastructure state if cleanup fails on a live cluster. |
| **Cleanup Failure After Successful Evaluation** | `src/brace/_runtime_owned_verification.py` (`coordinate`) | `test_cleanup_failure_after_success_raises_verification_cleanup_failed` | Executable Unit Test | If evaluation succeeds but namespace deletion fails, raises `VerificationCleanupFailed`, retaining both `cleanup_failure` and the generated `RecoverabilityKnowledge`. | Does not retry live infrastructure cleanup or resolve cloud resource leaks. |
| **Operational Phase Failure Cleanup** | `src/brace/_runtime_owned_verification.py` | `test_namespace_create_failure_records_phase_and_cleans_up`, `test_workload_apply_failure_triggers_owned_cleanup`, `test_readiness_failure_triggers_owned_cleanup`, `test_responsiveness_failure_triggers_owned_cleanup`, `test_projection_failure_triggers_owned_cleanup`, `test_evaluator_failure_triggers_owned_cleanup` | Executable Unit Test | Proves that a failure in any operational phase triggers ownership-scoped namespace deletion. | Does not test non-standard runtime terminations (e.g., SIGKILL or power outage). |
| **Evaluator Absence Handling** | `src/brace/_runtime_owned_verification.py`, `src/brace/reasoning/boundary.py` | `test_missing_evaluator_reaches_real_boundary_and_still_cleans_up`, `test_public_composition_without_evaluator_reaches_real_boundary` | Executable Unit Test | With no evaluator, executes all operational phases up to the boundary, raises `ReasoningCoreNotProvided`, and cleans up the namespace. | Does not supply a default, mock, or fallback recoverability decision. |
| **Timestamp Awareness and Normalization** | `src/brace/_recoverability_knowledge.py` | `test_recoverability_knowledge_rejects_naive_timestamp`, `test_recoverability_knowledge_rejects_unavailable_utc_offset`, `test_recoverability_knowledge_normalizes_non_utc_offset_to_utc` | Executable Unit Test | Rejects naive datetime objects and timestamps without available UTC offsets; normalizes non-UTC timezone-aware timestamps to UTC. | Does not verify clock synchronization (NTP) or host system clock drift. |
| **Observation Projection Immutability** | `src/brace/_operational_projection.py`, `src/brace/_recovery_observation.py` | `test_result_carries_exact_projected_observations_and_knowledge`, `test_boundary_request_carries_exact_identities_and_observations` | Executable Unit Test | Projects operational results into frozen dataclass observations with deterministic IDs derived from run, event, workload, and evidence kind. | Does not prove observations reflect live production service traffic. |

---

## 2. Verified AWS Demonstration Harness Evidence

The standalone live demonstration harness (`scripts/live_demo.py` & `infra/terraform`) was executed against a live AWS environment and frozen as milestone evidence:

| Public Claim | Implementation Reference | Verification Evidence / Command | Evidence Level | What It Establishes | What It Does Not Establish |
|---|---|---|---|---|---|
| **Real AWS Infrastructure Provisioning** | `infra/terraform/main.tf`, `scripts/live_demo.py` | Freeze record: commit `e8329b6`, Run ID `895844a6-afb2-45e1-a2c6-30c1229dc844` (`ap-south-1`) | Live AWS Execution | Successfully provisioned temporary AWS VPC, 2 public & 2 private subnets, NAT gateway, and EKS cluster with 1x `t3.small` node. | Does not prove multi-region provisioning or persistent cluster operation. |
| **Real Readiness Observation** | `scripts/live_demo.py`, `workloads/demo-app/` | Freeze record section 3: Deployment `demo-workload` ready condition observed | Live AWS Execution | Proves Kubernetes deployment converged to `availableReplicas == 1` within bounded deadline on live EKS. | Does not prove application health beyond Kubernetes deployment ready status. |
| **Real HTTP 200 Observation** | `scripts/live_demo.py`, verifier image | Freeze record section 3: In-cluster HTTP probe returned status code `200` | Live AWS Execution | In-cluster verifier Job probed the service endpoint and observed real HTTP status `200 OK`. | Does not prove application business logic correctness or high load capacity. |
| **Kubernetes Cleanup** | `scripts/live_demo.py` | Freeze record section 2: `K8S_CLEANUP` stage `PASS` | Live AWS Execution | Deleted run-scoped Namespace, Job, ConfigMap, ServiceAccount, and RBAC roles under ownership labels. | Does not clean cluster-wide resources not labeled with run ownership. |
| **Teardown & Final-State Emptiness** | `infra/terraform/`, `scripts/live_demo.py` | Freeze record section 2: `TEARDOWN` stage `PASS` (22 destroyed, 0 errors) | Live AWS Execution | Terraform cleanly destroyed all 22 provisioned AWS resources, confirming final-state emptiness. | Does not prove safety against manual cloud resource modifications during execution. |
| **Independent Residue Audit** | `scripts/live_demo.py` (`audit_residue`) | Freeze record section 2: `RESIDUE_AUDIT` stage `CLEAN` | Live AWS Execution | Out-of-band AWS CLI queries confirmed zero surviving VPCs, subnets, NAT gateways, security groups, or clusters for run. | Does not audit AWS services not managed by the harness. |

---

## 3. Implementation-Only Operational Capabilities

These capabilities are implemented in production modules using standard library and client libraries. In coordinator unit tests, these adapters and observers are replaced with in-process test doubles; in the AWS harness, they operate against live cluster APIs:

| Capability | Module Reference | Implementation Evidence | Limits and Boundaries |
|---|---|---|---|
| **Kubernetes Namespace Management** | `src/brace/_kubernetes_namespace.py` | `KubernetesNamespaceAdapter` creates, labels, and deletes namespaces using `kubernetes.client.CoreV1Api`. | Implementation code reviewed; coordinator tests replace adapter with test doubles. |
| **Approved Workload Bundle Application** | `src/brace/_kubernetes_workload.py` | `KubernetesWorkloadAdapter` applies Deployment, Service, and ConfigMap manifests, and inspects created resources for exact matching. | Implementation code reviewed; coordinator tests replace adapter with test doubles. |
| **Kubernetes Readiness Sampling** | `src/brace/_kubernetes_readiness.py` | `KubernetesReadinessObserver` inspects deployment available replicas against requested replicas within a bounded deadline. | Implementation code reviewed; coordinator tests replace observer with test doubles. |
| **HTTP Responsiveness Probing** | `src/brace/_kubernetes_responsiveness.py`, `src/brace/_http_client_transport.py` | `ApplicationResponsivenessObserver` samples endpoints using `_HttpClientTransport` (`http.client.HTTPConnection`) and classifies errors. | Implementation code reviewed; coordinator tests replace observer with test doubles. |
| **Sampling Convergence Engine** | `src/brace/_runtime_observation_convergence.py` | Convergence classes (`_ReadinessConvergence`, `_ResponsivenessConvergence`) manage bounded deadlines and error classification. | Implementation code reviewed; verified in unit tests via observers with test clocks. |

---

## 4. Packaging and CI Gates

Packaging closure and supply chain checks are enforced by dedicated unit test guards and CI workflow jobs.

| Gate | Configuration / File | Verification Tool / Command | What It Establishes | What It Does Not Establish |
|---|---|---|---|---|
| **Closed Runtime Dependencies** | `pyproject.toml`, `src/brace/` | `pytest tests/test_package_installation.py -k test_runtime_dependencies_match_retained_imports` | Asserts declared runtime dependencies (`kubernetes`, `urllib3`) match exactly third-party imports in retained code in both directions. | Does not prove compatibility with unpinned future library versions. |
| **Clean Distribution Member Allowlist** | `.github/workflows/ci.yml`, `MANIFEST.in` | `python -m build`, `audit_wheel_archive` in CI | Verifies built wheel contains only approved Python package modules (`src/brace/`) and allowed metadata, and sdist contains approved Python modules; no tests or temporary files. | Does not constitute a complete-file allowlist for sdist non-module members. |
| **Distribution Content Scanner** | `.github/workflows/ci.yml`, `tests/test_package_installation.py` | `pytest tests/test_package_installation.py -k test_distribution_content_scanner_detects_synthetic_credential_in_metadata` | Exercises the actual CI scanner against wheel archives, proving clean archives pass and synthetic credential patterns in `METADATA`, `WHEEL`, and `RECORD` fail. | Scans for configured regex patterns; does not guarantee that no credentials exist. |
| **Public Package Import Allowlist** | `tests/test_runtime_owned_composition.py` | `pytest tests/test_runtime_owned_composition.py` | Asserts that every package import in composition code belongs to `_APPROVED_PUBLIC_MODULES`. | Does not analyze dynamic runtime reflection or `getattr` tricks. |
| **Isolated Clean-Wheel Installation** | `.github/workflows/ci.yml` | Fresh venv: `pip install -c constraints.txt dist/*.whl`, `pip check`, outside-checkout import | Proves the built wheel installs with valid dependencies, passes `pip check`, and imports cleanly from `site-packages` outside source tree. | Matrix runs (3.10, 3.11, 3.12, 3.13) execute in CI; verified locally on Python 3.14.6. |
| **Tracked File Source Hygiene** | `.github/workflows/ci.yml` | Checked-out hygiene script in CI | Scans tracked source, test, workflow, and doc files for backup extensions, bytecode artifacts, and configured credential patterns. | Does not scan git commit history or uncommitted local ignored files. |

---

## 5. Explicit Scope and Boundary Exclusions

The following claims are explicitly outside the scope of Brace Platform:

1. **Backup-Restoration Exclusion**:
   Neither the installable public package nor the verified live AWS demonstration harness implements backup restoration. The platform deploys and observes synthetic workloads only.

2. **External Evaluator and Production Decision Exclusion**:
   Neither demonstration path includes the proprietary recoverability evaluator, scoring heuristics, inference models, or decision engine. Both demonstrations use presentation doubles returning fixed affirmed standing. *No production recoverability decision was made.*

3. **No Live Cluster Proof from Unit Tests**:
   The unit and composition tests execute entirely in-process against test doubles. They verify state transitions, invariants, and failure handling, but do not constitute live-cluster operational proof.

4. **Phase Success Is Not Observation Outcome**:
   An operational phase recording `succeeded=True` indicates only that the lifecycle phase executed without an unhandled infrastructure error. It is distinct from whether the observed check condition evaluated to an affirmative or negative result.
