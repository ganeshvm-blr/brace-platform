# Brace Platform

[![CI](https://github.com/ganeshvm-blr/brace-platform/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/ganeshvm-blr/brace-platform/actions/workflows/ci.yml)

Brace Platform orchestrates isolated Kubernetes verification runs to observe application workloads and deliver structured evidence across a pluggable decision boundary.

## Why This Exists

In disaster recovery and platform operations, teams frequently confirm that Kubernetes manifests applied successfully without verifying that the workload converged to operational readiness or responded to health probes. Declaring an environment recoverable based solely on resource creation leaves operational gaps when pods crash, dependencies fail, or in-cluster networking faults prevent traffic flow.

Brace Platform addresses this problem by decoupling operational infrastructure orchestration from the evaluation logic that decides recoverability:
1. It validates requests with strict cross-field structural invariants before any I/O.
2. It orchestrates a run-scoped, ownership-labeled namespace.
3. It applies an approved workload bundle (Deployment, Service, and ConfigMap) with digest-pinned images.
4. It observes Kubernetes readiness and in-cluster HTTP responsiveness within bounded deadlines.
5. It projects operational results into immutable evidence records with deterministic identities.
6. It delivers structured evidence across a one-way boundary protocol to an external evaluator.
7. It executes ownership-scoped cleanup while preserving primary and cleanup failure states.

## Architecture Overview

![Brace Platform Architecture and Demonstration Paths](docs/assets/brace-platform-architecture.png)

*Architecture overview showing the verified AWS demonstration harness (top path) and the local package demonstration (bottom path). Both paths preserve the external evaluator boundary: No production recoverability decision was made.*

## Two Demonstration Paths

The repository exposes two related but architecturally separate demonstration surfaces:

1. **Installable Public Package (`python -m brace.demo`)**:
   The core Python package orchestrating request validation, run-scoped namespace lifecycle, workload application, bounded readiness and HTTP observation, immutable projection, boundary handoff, and ownership-scoped cleanup. The local demonstration runs entirely in-process using test doubles—contacting no live cluster, creating no AWS resources, and making no network requests.

2. **Verified AWS Demonstration Harness (`scripts/live_demo.py` & `infra/terraform`)**:
   A separately invoked, standalone demonstration harness that automates temporary AWS infrastructure (VPC, public/private subnets, NAT gateway, Amazon EKS, and managed node group), pulls digest-pinned images from GitHub Container Registry (GHCR), observes real Kubernetes readiness and in-cluster HTTP 200 responses, stores evidence locally on disk, deletes Kubernetes resources, destroys Terraform infrastructure, and confirms zero cloud residue through an independent audit.

> **Architecture Boundary Notice**
> The installable public package does not provision AWS infrastructure. The repository also contains a separate, explicitly invoked Terraform demonstration harness that has been validated on a temporary Amazon EKS environment. Neither path implements backup restoration or contains the proprietary production recoverability evaluator.

## Local Package Quick Start (5 Minutes)

Run the local demonstration of the public coordination chain from a clean clone:

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

- **In-process test doubles**: Uses test doubles for namespace and workload adapters, observers, and evaluators.
- **No live cluster contacted**: No Kubernetes cluster or API server is accessed.
- **No AWS resources created**: Runs entirely within the local Python runtime.
- **No network requests made**: Network sockets and cloud endpoints are not touched.
- **Demonstration evaluator test double**: Returns fixed affirmed standing for illustrative protocol handoff only; contains no proprietary reasoning logic, scoring models, thresholds, or decision pipeline.

See [docs/demo.md](docs/demo.md) for the detailed step-by-step engineering walkthrough.

## Verified AWS Demonstration Summary

The repository includes a standalone demonstration harness designed for live-cluster verification without private dependencies:

| Dimension | Verified AWS Demonstration Specification |
|---|---|
| **Lifecycle Runner** | `scripts/live_demo.py` |
| **Infrastructure Stack** | `infra/terraform` (Terraform AWS VPC & EKS modules) |
| **Target Cloud** | Amazon Web Services (AWS EKS in `ap-south-1`) |
| **Compute Configuration** | Managed node group with 1x `t3.small` instance |
| **Container Registry** | GitHub Container Registry (`ghcr.io`), not Amazon ECR |
| **Image Pinning** | Digest-pinned: `@sha256:588c07b235...` (workload), `@sha256:d06d70a5b8...` (verifier) |
| **Real Observations** | Kubernetes deployment readiness condition `True`; In-cluster HTTP `200 OK` |
| **Evidence Storage** | Retained locally on disk in `.demo/evidence/<run-id>/` (NOT in Amazon S3) |
| **Teardown & Residue** | 22 Terraform resources cleanly destroyed; independent audit confirmed 0 surviving run-owned resources |
| **Freeze Milestone** | Validated on commit `e8329b6` with Run ID `895844a6-afb2-45e1-a2c6-30c1229dc844` |
| **Mandatory Boundary** | *No production recoverability decision was made.* |

See [docs/public-demo-freeze.md](docs/public-demo-freeze.md) for the complete freeze record and [docs/aws-case-study.md](docs/aws-case-study.md) for the sanitized deployment case study.

## Public Architecture Boundary

```
Kubernetes execution and observation
    -> immutable operational observations
    -> RecoverabilityEvaluator protocol
    -> RecoverabilityKnowledge
```

The system enforces a strict one-way dependency boundary:
- **Platform (Public)**: Coordinates namespace lifecycle, applies workloads, samples readiness and responsiveness, projects immutable evidence records, and manages cleanup. It never decides recoverability.
- **Evaluator (External)**: An injected component implementing the `RecoverabilityEvaluator` protocol that accepts an `EvaluationRequest` and returns `RecoverabilityKnowledge`. It never operates infrastructure.
- **Missing Evaluator Behavior**: Running with `evaluator=None` completes all operational phases and surfaces `ReasoningCoreNotProvided` at the `reasoning_boundary` phase, followed by ownership-scoped cleanup.
- **No Embedded Decision Logic**: The internal decision procedure of an evaluator is external to this repository and is not part of the public contract.

## Core Engineering Properties

- **Deterministic Identity**: Namespaces and observation records derive identities deterministically from run, event, workload, and evidence-kind references.
- **Immutable Evidence Models**: Observations and knowledge envelopes are frozen dataclasses; `EvidenceKind` is an exact two-member enumeration (`KUBERNETES_READINESS`, `APPLICATION_RESPONSIVENESS`).
- **Strict Cross-Field Validation**: Rejects requests before any I/O if run, event, workload, ownership, bundle, port, replica, or digest references do not match.
- **Cryptographic Digest Pinning**: Workload container images strictly require `@sha256:` followed by exactly 64 hexadecimal characters.
- **Dependency Inversion**: The coordinator depends exclusively on abstract adapter protocols and the external `RecoverabilityEvaluator` protocol.
- **Phase Execution Success vs Observation Outcome**: Operational phase success records that a lifecycle phase completed without infrastructure crash; it is distinct from whether an observation evaluated to an affirmative or negative result.
- **Robust Failure & Cleanup Semantics**: Every phase failure triggers cleanup; primary errors are never masked by cleanup failures; post-evaluation cleanup failures raise `VerificationCleanupFailed` while preserving the generated knowledge.
- **Dependency-Closed Package**: Declared runtime dependencies (`kubernetes`, `urllib3`) match retained production imports in both directions.

## What Is Intentionally Excluded

- **No Backup Restoration**: The platform applies and observes an approved workload bundle; it does not restore backups or interface with external backup tools.
- **No Production Recoverability Decision Algorithm**: The proprietary evaluator, scoring heuristics, and recoverability decision engines are intentionally external.
- **No Cloud Infrastructure in Package**: The installable package (`brace`) contains no AWS provisioning automation; cloud automation is restricted to the standalone demonstration harness (`scripts/live_demo.py` + `infra/terraform`).
- **No Live Cluster Contact in Package Tests**: The package unit and composition test suite runs entirely in-process against test doubles.

## Deeper Documentation

- [docs/architecture.md](docs/architecture.md): Detailed component responsibilities, public package architecture, verified AWS demonstration harness architecture, sequence diagrams, and boundary contracts.
- [docs/demo.md](docs/demo.md): Timed five-minute engineering walkthrough of orchestration, boundary validation, failure handling, and cleanup safety.
- [docs/verification-matrix.md](docs/verification-matrix.md): Exhaustive matrix mapping every public architectural and operational claim to concrete verification evidence and boundaries.
- [docs/aws-case-study.md](docs/aws-case-study.md): Sanitized deployment case study detailing the verified AWS EKS demonstration context.
- [docs/public-demo-freeze.md](docs/public-demo-freeze.md): Auditable freeze record documenting verified run facts on AWS EKS (`ap-south-1`).

## Development and Verification Commands

The package requires Python >= 3.10.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -c constraints.txt -e ".[dev]"
```

Run the complete test suite:

```bash
python -m pytest tests -v -p no:cacheprovider
```

Current verified local result: **75 passed, 1 skipped on Python 3.14.6** (76 collected tests covering coordinator, composition, packaging, and live demo harness contracts).

Verify distribution packaging:

```bash
python -m build
```

The distribution audit verifies that built wheels and source distributions contain only approved package modules and metadata, with no tests, no generated artifacts, and no credential markers.

## Repository Structure

```
src/brace/                 Public platform package (src layout)
src/brace/reasoning/       External reasoning boundary protocol contract
infra/terraform/           Standalone AWS VPC & EKS demonstration infrastructure
scripts/live_demo.py       Standalone live AWS demonstration lifecycle runner
workloads/demo-app/        Public demonstration workload manifests
tests/                     Unit, composition, packaging, and live harness tests
docs/                      Architecture, demo walkthrough, verification matrix, case studies
docs/assets/               Architecture diagrams (PNG and Mermaid source)
pyproject.toml             Package metadata and configuration
MANIFEST.in                Source-distribution file selection
```

## Current Status

- **Installable Package**: Baseline V1 complete, dependency-closed, tested against Python 3.10–3.14.
- **Live AWS Demonstration**: Validated end-to-end on AWS EKS; milestone frozen; zero cloud residue confirmed.
- **Execution Authority**: `main` branch is authoritative; no further AWS execution is authorized.
- **Evaluator Decision**: *No production recoverability decision was made.*

## Licence

Copyright © 2026 Ganesh V M. All rights reserved.

Brace Platform is public source-available software, not open-source software.
The repository may be viewed for evaluation, but copying, modification,
redistribution, derivative works, commercial use, and production use are not
permitted unless separately authorised in writing.

See [LICENSE](LICENSE) for the controlling terms.
