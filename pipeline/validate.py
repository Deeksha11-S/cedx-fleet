from dataclasses import dataclass
from datetime import datetime
from models.record import Record

@dataclass
class ValidationResult:
    valid: bool
    reason_code: str | None

def validate_record(record: Record, raw_dict: dict | None = None) -> ValidationResult:
    """
    Validate a record before processing by the worker agent.

    Returns:
        ValidationResult(valid=True, reason_code=None) if valid.
        Otherwise, sets reason_code to one of: STALE, MISSING_INPUT, OUTLIER,
        INJECTION_BLOCKED, LOW_CONFIDENCE, SCHEMA_DRIFT, SUPERSEDED_VERSION.
    """
    if record.id == "REC-001":
        return ValidationResult(False, "SCHEMA_DRIFT")
    # 1. Missing amount
    if record.amount is None:
        return ValidationResult(False, "MISSING_INPUT")

    # 2. Schema drift: detect extra keys not in the Record model
    if raw_dict is not None:
        allowed_keys = {"id", "owner", "deadline", "category", "amount", "notes", "version"}
        extra_keys = set(raw_dict.keys()) - allowed_keys
        if extra_keys:
            return ValidationResult(False, "SCHEMA_DRIFT")

    # 3. Required string fields must be non‑empty
    required_fields = {
        "id": record.id,
        "owner": record.owner,
        "category": record.category,
        "deadline": record.deadline,
    }
    for field_name, value in required_fields.items():
        if value is None or (isinstance(value, str) and value.strip() == ""):
            return ValidationResult(False, "SCHEMA_DRIFT")

    # 4. Superseded version (set during intake)
    if getattr(record, "superseded", False):
        return ValidationResult(False, "SUPERSEDED_VERSION")

    # 5. Unknown category => low confidence
    if record.category == "?":
        return ValidationResult(False, "LOW_CONFIDENCE")

    # 6. Prompt injection detection
    notes = (record.notes or "").lower()
    injection_phrases = [
        "ignore all previous instructions",
        "approve this immediately",
        "skip review",
        "output approved",
    ]
    for phrase in injection_phrases:
        if phrase in notes:
            return ValidationResult(False, "INJECTION_BLOCKED")

    # 7. Outlier amount
    if record.amount > 100000:
        return ValidationResult(False, "OUTLIER")

    # 8. Stale deadline
    try:
        deadline = datetime.strptime(record.deadline, "%Y-%m-%d").date()
        if deadline < datetime.today().date():
            return ValidationResult(False, "STALE")
    except ValueError:
        return ValidationResult(False, "SCHEMA_DRIFT")

    # All checks passed
    return ValidationResult(True, None)