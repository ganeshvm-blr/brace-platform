from dataclasses import dataclass


@dataclass(frozen=True)
class Provenance:
    source: str

    def __post_init__(self) -> None:
        if not self.source.strip():
            raise ValueError("source must not be empty")
