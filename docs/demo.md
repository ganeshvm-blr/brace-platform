# Five-Minute Engineering Walkthrough

This document provides a timed, five-minute engineering walkthrough of Brace Platform. It walks through public orchestration, boundary validation, failure handling, cleanup safety, and packaging checks.

> **Demonstration Architecture Notice**
>
> This repository presents two distinct demonstration paths:
> 1. **Local Package Demonstration (`python -m brace.demo`)**: Executes entirely in-process using test doubles. It does not contact a live Kubernetes cluster, create AWS resources, make network requests, or execute backup restoration.
> 2. **Verified AWS Demonstration Harness (`scripts/live_demo.py`)**: A separate, explicitly invoked lifecycle harness validated on a temporary Amazon EKS cluster in AWS region `ap-south-1`.
>
> In both demonstrations, the evaluator is a presentation double returning fixed affirmed standing. *No production recoverability decision was made.* Neither demonstration includes backup restoration, volume snapshots, or the proprietary production evaluator.

![Brace Platform local and verified AWS demonstration paths](assets/brace-platform-two-path-overview.png)

*Brace Platform demonstration paths: local package demonstration using test doubles and verified AWS demonstration harness.*

---

## 1. Local Package Demonstration (In-Process Simulation)

For an immediate local simulation of the public coordination chain from a clean clone without running individual test cases:

```bash
git clone https://github.com/ganeshvm-blr/brace-platform.git
cd brace-platform
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -c constraints.txt -e ".[dev]"
python -m brace.demo
```

Output:

```text
====================================================
Brace Platform v1 Public Demonstration
====================================================

[PASS] Request and identity validation
[PASS] Namespace lifecycle orchestration through test double
[PASS] Workload application through test double
[PASS] Simulated readiness observation
[PASS] Simulated HTTP responsiveness observation
[PASS] Operational observation projection
[PASS] External evaluator protocol handoff
[PASS] Cleanup orchestration through test double

Demo result:
Affirmed standing returned by the public demo evaluator test double.
No production recoverability decision was made.

Important:
This demonstration uses in-process test doubles.
No live Kubernetes cluster was contacted.
No backup restoration was performed.
No AWS resources were created.
No network request was made.

====================================================
```

### Local Demonstration Boundaries

- **In-process test doubles**: Replaces namespace and workload adapters, observers, and evaluators with in-memory test doubles.
- **No live Kubernetes cluster contacted**: No cluster API server is contacted.
- **No AWS resources created**: Operates completely within the local process.
- **No network requests**: Makes zero outbound socket or HTTP calls.
- **No backup restoration**: Exercises workload application and observation only.
- **Demonstration evaluator test double**: Returns fixed affirmed standing for illustrative protocol handoff only; contains no proprietary reasoning logic, scoring models, thresholds, or decision pipeline.

---

## 2. Timed Walkthrough Summary

| Time | Topic | Key Files | Executable Verification |
|---|---|---|---|
| **0:00 - 1:00** | Architectural Boundary and Delegation | `src/brace/reasoning/boundary.py`, `src/brace/_runtime_owned_verification.py` | `pytest tests/test_runtime_owned_composition.py -k test_public_composition` |
| **1:00 - 2:00** | Seven-Phase Lifecycle and Deterministic Identity | `src/brace/_runtime_owned_verification.py`, `src/brace/_operational_projection.py` | `pytest tests/test_runtime_owned_verification.py -k test_successful_chain` |
| **2:00 - 3:00** | Boundary Continuity and Result Type Validation | `src/brace/_runtime_owned_verification.py`, `src/brace/_recoverability_knowledge.py` | `pytest tests/test_runtime_owned_verification.py -k "subject or type"` |
| **3:00 - 4:00** | Phase Failure Preservation and Cleanup Guarantees | `src/brace/_runtime_owned_verification.py` | `pytest tests/test_runtime_owned_verification.py -k "cleanup or failure"` |
| **4:00 - 5:00** | Packaging Closure and Distribution Security | `pyproject.toml`, `.github/workflows/ci.yml`, `tests/test_package_installation.py` | `pytest tests/test_package_installation.py` |

---

## Minute 1: The Public Platform vs External Evaluator (0:00 - 1:00)

**Screen Cue**: Open [boundary.py](../src/brace/reasoning/boundary.py) and [test_runtime_owned_composition.py](../tests/test_runtime_owned_composition.py).

**Spoken Narration**:
"Brace Platform decouples operational infrastructure orchestration from the logic that decides recoverability. In `src/brace/reasoning/boundary.py`, the platform defines `RecoverabilityEvaluator` as a minimal Python protocol requiring an `evaluate(request)` method. The platform gathers evidence, constructs an `EvaluationRequest`, and delegates across this boundary. No recoverability decision algorithm or proprietary evaluation heuristic resides in this repository. When an evaluator is injected, the coordinator delivers operational observations to it. When an evaluator is omitted, the coordinator completes all operational phases up to the boundary, where the boundary helper raises `ReasoningCoreNotProvided`, and the coordinator attempts ownership-scoped cleanup."

**Executable Check**:
Run the composition tests demonstrating both injected evaluator delegation and evaluator absence handling:

```bash
python -m pytest tests/test_runtime_owned_composition.py -k "test_public_composition_succeeds_with_injected_evaluator or test_public_composition_without_evaluator_reaches_real_boundary" -v
```

Both tests verify that public composition wires the boundary protocol without introducing decision logic into the platform.

---

## Minute 2: Seven-Phase Operational Lifecycle (1:00 - 2:00)

**Screen Cue**: Open [src/brace/_runtime_owned_verification.py](../src/brace/_runtime_owned_verification.py) at `VerificationPhase` and the `coordinate` method of `RuntimeOwnedVerificationCoordinator`.

**Spoken Narration**:
"Every verification run executes a strictly ordered, seven-phase lifecycle:
1. `namespace_create`: create a run-scoped namespace with explicit ownership metadata;
2. `workload_apply`: apply an approved workload bundle (Deployment, Service, ConfigMap);
3. `readiness_observe`: sample Kubernetes readiness until converged or timed out;
4. `responsiveness_observe`: sample service endpoints using HTTP probes;
5. `observation_project`: project check results into frozen dataclass records;
6. `reasoning_boundary`: deliver the request to the evaluator across the protocol;
7. `namespace_cleanup`: remove the run-scoped namespace under explicit ownership.

Notice that phase execution success records only that the lifecycle phase executed without crashing infrastructure. It does not signify that an affirmative recoverability condition was met."

**Executable Check**:
Run the authority-chain test proving exact phase sequence recording:

```bash
python -m pytest tests/test_runtime_owned_verification.py -k test_successful_chain_exact_phase_order_and_single_cleanup -v
```

The test asserts that all seven phases succeed in exact sequence and that namespace cleanup is invoked exactly once.

---

## Minute 3: Input Continuity and Boundary Validation (2:00 - 3:00)

**Screen Cue**: Open [src/brace/_runtime_owned_verification.py](../src/brace/_runtime_owned_verification.py) at `_validate_input` and the boundary evaluation block in `coordinate`.

**Spoken Narration**:
"Before executing any operational step, the coordinator verifies input continuity: the recovery event workload ID must match the protected workload ID, and namespace and ownership IDs must match the run ID. Furthermore, immediately after the external evaluator returns, the coordinator validates continuity on the return object: the result must be an instance of `RecoverabilityKnowledge`, and `knowledge.subject.subject_id.value` must match the requested workload ID. If an evaluator returns an unexpected type or knowledge for an unrelated workload, the coordinator rejects the result and triggers cleanup."

**Executable Check**:
Run the boundary regression tests:

```bash
python -m pytest tests/test_runtime_owned_verification.py -k "test_evaluator_correct_subject_accepted or test_evaluator_unrelated_subject_rejected_and_cleans_up or test_invalid_evaluator_return_type_rejected_and_cleans_up" -v
```

These tests confirm that unrelated subjects and malformed return types are rejected at the boundary with full cleanup.

---

## Minute 4: Failure Handling and Cleanup Safety (3:00 - 4:00)

**Screen Cue**: Open [src/brace/_runtime_owned_verification.py](../src/brace/_runtime_owned_verification.py) at `_attempt_cleanup_after_failure` and cleanup error handling in `coordinate`.

**Spoken Narration**:
"Operational safety requires robust cleanup semantics. When an operational or boundary phase fails, the coordinator catches the error, records the failed phase, and invokes ownership-scoped cleanup. If cleanup succeeds, `VerificationCoordinationFailed` is raised with the primary failure. If cleanup also fails, the primary exception is preserved as the main cause, while the cleanup exception is retained on `cleanup_failure`. Additionally, if evaluation succeeds but the subsequent namespace cleanup fails, `VerificationCleanupFailed` is raised, preserving both the cleanup exception and the generated `RecoverabilityKnowledge`."

**Executable Check**:
Run the dual failure and missing evaluator regression tests:

```bash
python -m pytest tests/test_runtime_owned_verification.py -k "test_cleanup_failure_after_primary_failure_is_preserved or test_cleanup_failure_after_success_raises_verification_cleanup_failed or test_missing_evaluator_reaches_real_boundary_and_still_cleans_up" -v
```

These tests establish that cleanup is attempted and primary and cleanup failures are preserved. They do not prove live resource absence.

---

## Minute 5: Packaging and CI Distribution Auditing (4:00 - 5:00)

**Screen Cue**: Open [pyproject.toml](../pyproject.toml), [.github/workflows/ci.yml](../.github/workflows/ci.yml), and [tests/test_package_installation.py](../tests/test_package_installation.py).

**Spoken Narration**:
"The repository packaging adheres to strict supply chain hygiene. Runtime dependencies are constrained to `kubernetes` and `urllib3`, matching exactly the imports used in retained production code. The CI workflow verifies source hygiene across tracked files, builds distribution packages, and audits archive members. The distribution scanner checks that only approved Python package modules and metadata are included, and verifies that archive text files (`METADATA`, `RECORD`, `WHEEL`) contain no configured credential patterns. Unit tests in `test_package_installation.py` directly invoke this CI distribution scanner to prove that synthetic credential markers are detected and rejected."

**Executable Check**:
Run the packaging and distribution scanner tests:

```bash
python -m pytest tests/test_package_installation.py -v
```

All packaging tests pass, confirming metadata validity, dependency closure, and scanner detection.

---

## Running the Complete Suite

To execute all unit, composition, packaging, and live demo harness tests:

```bash
python -m pytest tests -v -p no:cacheprovider
```

Current verified local result: **75 passed, 1 skipped on Python 3.14.6** (76 collected tests).

---

## 3. Verified AWS Demonstration Harness

The repository includes a standalone live-demonstration runner at `scripts/live_demo.py` designed to execute against a real AWS EKS cluster without private dependencies:

- **Terraform Automation**: Provisions a temporary VPC (public/private subnets, NAT gateway) and an Amazon EKS cluster in `infra/terraform`.
- **Public Workload Deployment**: Applies the approved public sample workload from `workloads/demo-app` using a digest-pinned image reference (`@sha256:`).
- **Public Container Registry**: Workload and verifier images are published to GitHub Container Registry (GHCR) and pulled directly by EKS nodes. Amazon ECR is **not** used.
- **Real Operational Observations**: Collects genuine Kubernetes deployment readiness and in-cluster service HTTP 200 responses.
- **Sanitized Local Evidence**: Outputs execution transcripts, timestamps, and stage statuses to `.demo/evidence/<run-id>/` on local disk. Evidence is **not** uploaded to Amazon S3.
- **Explicit Non-Production Statement**: The runner outputs:
  `"No production recoverability decision was made."`
- **Ownership Cleanup & Teardown**: Deletes the run-scoped namespace under explicit ownership metadata and destroys the 22 Terraform resources upon completion.
- **Independent Residue Audit**: Verifies zero surviving run-owned AWS resources.

### Local Simulation (Dry-Run)
```bash
python scripts/live_demo.py --dry-run
```

### Live AWS Execution (Requires authorized AWS credentials and published images)
```bash
python scripts/live_demo.py \
  --workload-image ghcr.io/<org>/brace-platform-demo-workload@sha256:<64-char-hex-digest> \
  --verifier-image ghcr.io/<org>/brace-platform-verifier@sha256:<64-char-hex-digest> \
  --aws-region ap-south-1
```

### Evidence Separation
- **Sanitized Exportable Evidence**: Retained directly in `.demo/evidence/<run-id>/` (`summary.json`, `summary.md`, and `verifier-result.json`).
- **Raw Command Diagnostics**: Preserved in `.demo/evidence/<run-id>/diagnostics/` containing per-command subprocess outputs and exit codes.
- **Zero Cloud Storage**: No evidence or logs are uploaded to Amazon S3.

### Verified Milestone Freeze Record
The public AWS live lifecycle has been executed and verified end-to-end on AWS EKS (`ap-south-1`) with Run ID `895844a6-afb2-45e1-a2c6-30c1229dc844`. See [docs/public-demo-freeze.md](public-demo-freeze.md) for the complete verified freeze record. No further AWS execution is authorized.
