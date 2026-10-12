import enum


class EvidenceKind(enum.Enum):
    KUBERNETES_READINESS = "kubernetes_readiness"
    APPLICATION_RESPONSIVENESS = "application_responsiveness"
