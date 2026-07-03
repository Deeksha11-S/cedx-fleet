from dataclasses import dataclass

@dataclass
class Decision:
    record_id: str
    decision: str          # APPROVE, REVIEW, REJECT
    confidence: float
    reason: str
    reason_code: str | None
    requires_review: bool
    model: str
    prompt_version: str
    verdict: str           # pass, fail, needs_human
    delivered_fields: dict | None = None
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    latency_ms: int = 0
    retries: int = 0
    verifier_tokens_in: int = 0
    verifier_tokens_out: int = 0
    verifier_cost_usd: float = 0.0
    verifier_latency_ms: int = 0
    verifier_retries: int = 0
    verifier_model: str = ""