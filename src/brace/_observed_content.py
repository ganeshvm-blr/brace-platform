from dataclasses import dataclass

from brace._evidence_kind import EvidenceKind
from brace._evidence_semantic import EvidenceSemantic


@dataclass(frozen=True)
class ObservedContent:
    kind: EvidenceKind
    semantic: EvidenceSemantic
    description: str

    def __post_init__(self) -> None:
        if not self.description.strip():
            raise ValueError("description must not be empty")
