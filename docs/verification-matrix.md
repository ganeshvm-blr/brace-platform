# Brace Platform Verification Matrix

This matrix maps each architectural and operational claim made by the public platform to its concrete implementation, verification evidence, and verification boundaries.

---

## 1. Executable Coordinator Tests

The coordinator is tested using production-shaped in-process test doubles that execute all lifecycle paths, boundary handoffs, and error states.

| Public Claim | Implementation Reference | Exact Test Name | Evidence Level | What It Establishes | What It Does Not Establish |
|---|---|---|---|---|---|
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

## 2. Implementation-Only Operational Capabilities

These capabilities are implemented in production modules using standard library and client libraries. In coordinator tests, these adapters and observers are replaced with in-process test doubles.

| Capability | Module Reference | Implementation Evidence | Limits and Boundaries |
|---|---|---|---|
| **Kubernetes Namespace Management** | `src/brace/_kubernetes_namespace.py` | `KubernetesNamespaceAdapter` creates, labels, and deletes namespaces using `kubernetes.client.CoreV1Api`. | Implementation code reviewed; coordinator tests replace the adapter with test doubles. Does not verify live Kubernetes RBAC, API throttling, or admission webhooks. |
| **Approved Workload Bundle Application** | `src/brace/_kubernetes_workload.py` | `KubernetesWorkloadAdapter` applies Deployment, Service, and ConfigMap manifests, and inspects created resources for exact matching. | Implementation code reviewed; coordinator tests replace the adapter with test doubles. Does not verify live scheduler placement, node affinity, or container registry pulling. |
| **Kubernetes Readiness Sampling** | `src/brace/_kubernetes_readiness.py` | `KubernetesReadinessObserver` inspects deployment available replicas against requested replicas within a bounded deadline. | Implementation code reviewed; coordinator tests replace the observer with test doubles. Does not establish pod health beyond Kubernetes deployment status conditions. |
| **HTTP Responsiveness Probing** | `src/brace/_kubernetes_responsiveness.py`, `src/brace/_http_client_transport.py` | `ApplicationResponsivenessObserver` samples endpoints using `_HttpClientTransport` (`http.client.HTTPConnection`) and classifies errors. | Implementation code reviewed; coordinator tests replace the observer with test doubles. Probing an endpoint verifies HTTP availability; it does not constitute full application integration testing. |
| **Sampling Convergence Engine** | `src/brace/_runtime_observation_convergence.py` | Convergence classes (`_ReadinessConvergence`, `_ResponsivenessConvergence`) manage bounded deadlines and error classification, importing `urllib3.exceptions`. | Implementation code reviewed; verified in unit tests via observers with test clocks. |

---

## 3. Packaging and CI Gates

Packaging closure and supply chain checks are enforced by dedicated unit test guards and CI workflow jobs.

| Gate | Configuration / File | Verification Tool / Command | What It Establishes | What It Does Not Establish |
|---|---|---|---|---|
| **Closed Runtime Dependencies** | `pyproject.toml`, `src/brace/` | `pytest tests/test_package_installation.py -k test_runtime_dependencies_match_retained_imports` | Asserts that declared runtime dependencies (`kubernetes`, `urllib3`) match exactly the third-party imports in retained code. | Does not prove compatibility with unpinned future library versions. |
| **Clean Distribution Member Allowlist** | `.github/workflows/ci.yml`, `MANIFEST.in` | `python -m build --no-isolation`, `audit_wheel_archive` in CI | Verifies built wheel contains only approved Python package modules (`src/brace/`) and allowed metadata, and sdist contains approved Python modules; no tests, caches, or unexpected files. | Does not constitute a complete-file allowlist for sdist non-module members. |
| **Distribution Content Scanner** | `.github/workflows/ci.yml`, `tests/test_package_installation.py` | `pytest tests/test_package_installation.py -k test_distribution_content_scanner_detects_synthetic_credential_in_metadata` | Exercises the actual CI scanner against wheel archives, proving clean archives pass and configured synthetic credential patterns in `METADATA`, `WHEEL`, and `RECORD` fail. | Scans for configured regex patterns; does not guarantee that no credentials exist. |
| **Public Package Import Allowlist** | `tests/test_runtime_owned_composition.py` | `pytest tests/test_runtime_owned_composition.py` | Asserts that every package import in composition code belongs to `_APPROVED_PUBLIC_MODULES` (exercised in `test_builder_threads_exact_evaluator`). | Does not analyze dynamic runtime reflection or `getattr` tricks. |
| **Isolated Clean-Wheel Installation** | `.github/workflows/ci.yml` (and verified on host Mac) | Fresh venv: `pip install -c constraints.txt dist/*.whl`, `pip check`, outside-checkout import | Proves the built wheel installs with valid dependencies, passes `pip check`, and imports cleanly from `site-packages` outside the source tree. | Verified on Python 3.14.6 locally; matrix runs (3.10, 3.11, 3.12, 3.13) execute in CI. |
| **Tracked File Source Hygiene** | `.github/workflows/ci.yml` | Checked-out hygiene script in CI | Scans tracked source, test, workflow, and doc files for backup extensions, bytecode artifacts, and configured credential patterns. | Does not scan git commit history or uncommitted local ignored files. |

---

## 4. Unsupported Claims and Explicit Exclusions

The following claims are explicitly outside the scope of Brace Platform:

1. **No Backup Restoration**:
   The platform applies an approved workload bundle to an isolated namespace and observes its behavior. It does not perform volume restores, snapshot recoveries, database replays, or storage system restorations.

2. **No Live Cluster Proof from Unit Tests**:
   The 39 unit tests pass against in-process fakes. These tests prove coordinator state machine transitions, failure preservation, and boundary invariants; they do not constitute proof of live Kubernetes cluster functionality.

3. **Phase Success Is Not Observation Outcome**:
   A phase result recording `succeeded=True` indicates that the orchestration phase ran without an unhandled runtime error. It is distinct from whether the observed check condition evaluated to an affirmative or negative result.

4. **No Production Recoverability Decision Algorithm**:
   No recoverability decision algorithm, proprietary heuristic, or recovery truth model is included in this repository. All recoverability decisions are delegated across the opaque `RecoverabilityEvaluator` boundary.

5. **No Live Integrity, RTO/RPO, or Performance Proof**:
   The repository does not make or prove claims regarding recovery time objectives (RTO), recovery point objectives (RPO), system throughput, or end-to-end data integrity.
