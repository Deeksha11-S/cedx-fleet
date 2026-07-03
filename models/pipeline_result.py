from dataclasses import dataclass
from typing import List, Optional
from models.decision import Decision

@dataclass
class PipelineResult:
    record_id: str
    version: int
    source_format: str
    worker_decision: Decision
    verifier_decision: Decision
    final_status: str
    approval_trail: List[dict]
    agent_trace: List[dict]
    delivered_fields: dict
    transcript_hash: Optional[str] = None