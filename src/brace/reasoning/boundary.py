"""Reasoning boundary for the Brace recoverability-verification platform.

This module defines the contract between the public verification *platform*
(Kubernetes runtime orchestration, evidence transport, observation, packaging,
deployment, and observability) and the Brace *reasoning core* that decides
whether recoverability has been demonstrated.

The reasoning core - the proprietary logic that transforms authoritative
operational evidence into a recoverability conclusion - is intentionally NOT
included in this public repository. It is supplied at runtime as an
implementation of the Protocols defined here.
The boundary contract exposes:
- the public RecoverabilityEvaluator protocol and EvaluationRequest envelope;
- the evaluate_recoverability delegation helper and ReasoningCoreNotProvided exception.

The concrete evaluator implementation and decision logic are external to this repository.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from brace._recoverability_knowledge import RecoverabilityKnowledge


class ReasoningCoreNotProvided(RuntimeError):
    """Raised when a verification is attempted without a reasoning core.

    The public platform is fully functional up to the boundary: it can build,
    deploy, create an isolated environment, observe a workload, and assemble
    authoritative evidence. Reaching an authoritative recoverability conclusion
    requires a ``RecoverabilityEvaluator`` implementation (the proprietary
    Brace reasoning core), which is not distributed with this repository.
    """


@runtime_checkable
class RecoverabilityEvaluator(Protocol):
    """The boundary into the proprietary Brace reasoning core.

    An implementation receives the authoritative evidence assembled by the
    platform for a single verification run and returns an immutable
    recoverability result. The platform depends only on this contract; it has
    no knowledge of how the result is derived.

    Implementations are expected to be:

    - deterministic - identical evidence yields an identical result;
    - immutable - the returned knowledge is a frozen, evidence-backed artifact;
    - self-contained - the evaluator performs no infrastructure operations,
      no discovery, and no side effects.

    The concrete evaluator is supplied at runtime and is not part of this
    public repository.
    """

    def evaluate(self, request: "EvaluationRequest") -> RecoverabilityKnowledge:
        """Return the recoverability result for one verification run.

        Args:
            request: the authoritative evidence and subject references the
                platform has assembled for a single verification run.

        Returns:
            An immutable ``RecoverabilityKnowledge`` result describing whether
            recoverability has been established for the subject workload.
        """
        ...


class EvaluationRequest(Protocol):
    """The evidence a reasoning core receives at the boundary.

    This is the *structural* contract only. The platform populates it with the
    authoritative evidence and subject references gathered during a verification
    run. The specific fields consumed by the reasoning core, and the way they
    are interpreted, are properties of the proprietary core and are not part of
    this public contract.
    """

    ...


def evaluate_recoverability(
    request: "EvaluationRequest",
    evaluator: RecoverabilityEvaluator | None,
) -> RecoverabilityKnowledge:
    """Cross the reasoning boundary for a single verification run.

    The platform calls this to hand assembled evidence to the reasoning core.
    If no reasoning core has been provided, the platform has done everything it
    can up to the boundary, and this raises ``ReasoningCoreNotProvided``.

    Args:
        request: the authoritative evidence for one verification run.
        evaluator: an implementation of the proprietary reasoning core, or
            ``None`` when running the public platform without it.

    Returns:
        The immutable recoverability result produced by the reasoning core.

    Raises:
        ReasoningCoreNotProvided: if ``evaluator`` is ``None``.
    """
    if evaluator is None:
        raise ReasoningCoreNotProvided(
            "The Brace reasoning core is not included in this public platform "
            "repository. Provide a RecoverabilityEvaluator implementation to "
            "reach an authoritative recoverability conclusion."
        )
    return evaluator.evaluate(request)
