"""Small shared response schemas."""
from app.schemas.base import CamelModel


class HealthOut(CamelModel):
    status: str
    db: str
    model: str | None = None


class HybridConfigOut(CamelModel):
    alpha: float
    beta: float
    pass_threshold: float
    exception_threshold: float
    psi_threshold: float
