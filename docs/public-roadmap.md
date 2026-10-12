# Public Capability Roadmap

This roadmap describes capability horizons, not committed release dates or implementation contracts. The current public repository represents the V1 platform baseline. Later implementation details remain intentionally outside the public artifact.

## V1: Public Platform Baseline

The public repository demonstrates a concrete, working verification platform:

- run-scoped orchestration;
- request and identity validation;
- workload application and inspection;
- readiness and HTTP responsiveness observation;
- immutable operational observations;
- external evaluator boundary;
- failure and cleanup semantics;
- package and CI verification.

The evaluator implementation is external. Public tests use in-process doubles. V1 does not restore backups or prove live infrastructure recovery.

## Future Horizon 1: Configurable Verification

High-level direction only:

- workload-specific verification definitions;
- approved collectors;
- versioned verification configurations;
- clearer operator-facing evidence and reports.

## Future Horizon 2: Enterprise Integration and Governance

High-level direction only:

- integration with existing infrastructure and recovery tooling;
- authentication and authorization;
- auditability;
- evidence retention;
- API and SDK access;
- multiple environments.

## Future Horizon 3: Fleet and Continuous Assurance

High-level direction only:

- recurring verification;
- verification freshness;
- historical comparison;
- fleet-level visibility;
- change-aware re-verification.

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

- V1: Observe and orchestrate
- Future: Configure
- Future: Integrate and govern
- Future: Assure across fleets
- Future: Enable higher-level operational analysis
