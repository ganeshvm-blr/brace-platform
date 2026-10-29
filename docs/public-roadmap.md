# Public Capability Roadmap

This roadmap describes high-level capability horizons, not committed release dates or implementation contracts. The current public repository represents the V1 platform baseline. Later implementation details remain intentionally outside the public artifact.

## V1: Public Platform Baseline

The public repository demonstrates a concrete, working verification platform across two demonstration surfaces:
1. An **installable Python package** orchestrating:
   - run-scoped namespace lifecycle;
   - strict cross-field request and identity validation;
   - approved workload application and inspection;
   - bounded readiness and HTTP responsiveness observation;
   - immutable operational observation projection;
   - external evaluator boundary protocol handoff;
   - failure and ownership-scoped cleanup semantics;
   - closed packaging and supply-chain auditing.
2. A **verified AWS demonstration harness** proving:
   - automated temporary AWS VPC and Amazon EKS provisioning;
   - digest-pinned container workload pulling from GitHub Container Registry (GHCR);
   - real in-cluster readiness and HTTP 200 observation;
   - clean Kubernetes and Terraform teardown with zero surviving cloud residue.

The recoverability evaluator implementation is external to this repository. Public package unit and composition tests execute against in-process doubles. V1 does not restore backups, manage volume snapshots, or prove live disaster recovery. *No production recoverability decision was made.*

## Future Horizon 1: Configurable Verification

High-level direction only:

- workload-specific verification definitions;
- approved collectors;
- versioned verification configurations;
- operator-facing evidence export and reports.

## Future Horizon 2: Enterprise Integration and Governance

High-level direction only:

- integration with existing infrastructure and recovery tooling;
- authentication and authorization;
- auditability and compliance trails;
- evidence retention policies;
- API and SDK access;
- multi-cluster verification orchestration.

## Future Horizon 3: Fleet and Continuous Assurance

High-level direction only:

- recurring and scheduled verification;
- verification freshness and drift detection;
- historical comparison across verification runs;
- fleet-level visibility and reporting;
- change-aware re-verification triggers.

These represent future capability directions, not current features or commitments. Continuous verification does not exist in the platform today.

## Future Horizon 4: Broader Operational Intelligence

Verified recoverability information may eventually become an input to broader operational-intelligence systems. Brace should remain authoritative for its own verification results rather than becoming a general workflow, monitoring, policy, or incident-management platform.

## Product Boundary

Brace should not become:

- a backup platform;
- a recovery automation engine;
- a generic workflow engine;
- a generic monitoring platform;
- a policy engine;
- an AI incident copilot;
- an infrastructure-management platform.

## Roadmap Summary

- **V1**: Observe and orchestrate workloads across bounded lifecycles (Current Baseline)
- **Future Horizon 1**: Configure workload-specific verification rules
- **Future Horizon 2**: Integrate and govern across enterprise environments
- **Future Horizon 3**: Assure continuously across fleets
- **Future Horizon 4**: Enable higher-level operational analysis
