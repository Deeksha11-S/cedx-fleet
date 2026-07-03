from dataclasses import dataclass
from typing import Optional


@dataclass
class AuditRecord:

    id: str

    version: int

    source_format: str

    status: str

    reason_code: Optional[str]

    reason_class: Optional[str]

    transcript_hash: Optional[str]

    delivered_fields: dict

    delivered_fields_hash: Optional[str]

    approval_trail: list

    agent_trace: list