from __future__ import annotations

import argparse
import datetime
import json
import sys
import uuid
from typing import Any

from brace._execution import (
    RunNamespaceIdentity,
    WorkloadBundleIdentity,
)
from brace._kubernetes_namespace import NamespaceOwnership
from brace._kubernetes_workload import (
    ApprovedContainer,
    ApprovedDeployment,
    ApprovedService,
    ApprovedWorkloadBundle,
)
from brace._recoverability_knowledge import RecoverabilityKnowledge
from brace._recoverability_subject import (
    RecoverabilitySubject,
    RecoverabilitySubjectId,
)
from brace._recoverability_truth_standing_value import (
    RecoverabilityTruthStandingValue,
)
from brace._recovery_event import RecoveryEvent
from brace._runtime_owned_composition import run_runtime_owned_verification
from brace._runtime_owned_verification import (
    BoundaryEvaluationRequest,
)
from brace._workload import ProtectedWorkload
from brace.reasoning.boundary import RecoverabilityEvaluator


class DemoInClusterEvaluator:
    def evaluate(self, request: BoundaryEvaluationRequest) -> RecoverabilityKnowledge:
        return RecoverabilityKnowledge(
            knowledge_id=uuid.uuid4(),
            subject=RecoverabilitySubject(
                subject_id=RecoverabilitySubjectId(
                    value=request.protected_workload.workload_id
                )
            ),
            standing=RecoverabilityTruthStandingValue.RECOVERABILITY_AFFIRMED,
            established_at=datetime.datetime.now(datetime.timezone.utc),
        )


def execute_verifier(request_data: dict[str, Any]) -> dict[str, Any]:
    run_id = uuid.UUID(request_data["run_id"])
    evaluation_id = uuid.UUID(request_data["evaluation_id"])
    workload_id = uuid.UUID(request_data["workload_id"])

    workload = ProtectedWorkload(workload_id=workload_id, name="public-demo-workload")
    event = RecoveryEvent(event_id=uuid.UUID(request_data["event_id"]), workload_id=workload_id)
    namespace = RunNamespaceIdentity(run_id)

    bundle_info = request_data["bundle"]
    identity = WorkloadBundleIdentity(
        bundle_name=bundle_info.get("name", "public-demo-bundle"),
        bundle_version=bundle_info.get("version", "1.0.0"),
    )

    container_info = bundle_info["deployment"]["container"]
    container = ApprovedContainer(
        name=container_info["name"],
        image=container_info["image"],
        container_port=container_info["port"],
    )
    deployment = ApprovedDeployment(
        name=bundle_info["deployment"]["name"],
        container=container,
        replicas=bundle_info["deployment"].get("replicas", 1),
    )
    service_info = bundle_info["service"]
    service = ApprovedService(
        name=service_info["name"],
        port=service_info["port"],
        target_port=service_info["target_port"],
    )
    bundle = ApprovedWorkloadBundle(
        identity=identity,
        config_map=None,
        deployment=deployment,
        service=service,
    )
    ownership = NamespaceOwnership(
        run_id=run_id,
        workload_id=workload_id,
        bundle=identity,
        creation_source="brace_runtime_owned_verification",
    )

    evaluator = DemoInClusterEvaluator()

    coord_result = run_runtime_owned_verification(
        run_id=run_id,
        evaluation_id=evaluation_id,
        protected_workload=workload,
        recovery_event=event,
        namespace=namespace,
        ownership=ownership,
        workload_bundle=bundle,
        evaluator=evaluator,
    )

    return {
        "run_id": str(coord_result.run_id),
        "evaluation_id": str(coord_result.evaluation_id),
        "phases": [
            {
                "phase": pr.phase.value,
                "succeeded": pr.succeeded,
            }
            for pr in coord_result.phase_results
        ],
        "observations": [
            {
                "observation_id": str(obs.observation_id),
                "kind": obs.content.kind.value,
                "semantic": obs.content.semantic.value,
                "description": obs.content.description,
                "occurrence_time": obs.occurrence_time.isoformat(),
            }
            for obs in coord_result.observations
        ],
        "knowledge": {
            "knowledge_id": str(coord_result.knowledge.knowledge_id) if coord_result.knowledge else None,
            "standing": coord_result.knowledge.standing.value if coord_result.knowledge else None,
            "established_at": coord_result.knowledge.established_at.isoformat() if coord_result.knowledge else None,
        },
        "disclaimer": "No production recoverability decision was made.",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Brace In-Cluster Verifier Entrypoint")
    parser.add_argument("--request-file", help="Path to request JSON (defaults to stdin)")
    args = parser.parse_args()

    if args.request_file:
        with open(args.request_file, "r", encoding="utf-8") as f:
            req_data = json.load(f)
    else:
        req_data = json.load(sys.stdin)

    result = execute_verifier(req_data)

    output_str = json.dumps(result, indent=2)
    sys.stdout.write(output_str + "\n")
    sys.stdout.flush()


if __name__ == "__main__":
    main()
