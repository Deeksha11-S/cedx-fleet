import os
from models.record import Record

class ModelRouter:
    """
    Selects which model to use based on record complexity.
    Returns a dict: {"model": "gpt-4o-mini" | "gpt-4", "reason": str}
    """
    def route(self, record: Record) -> dict:
        # Simple heuristics – can be extended
        complexity = 0
        if record.category in ("REVIEW", "REPORT"):
            complexity += 2
        if record.amount and record.amount > 10000:
            complexity += 1
        if record.notes and len(record.notes) > 200:
            complexity += 1

        # Use cheap model for low complexity, expensive for high
        if complexity >= 3:
            model = "gpt-4"
            reason = "high complexity (review/report or large notes)"
        else:
            model = "gpt-4o-mini"
            reason = "low complexity"

        return {"model": model, "reason": reason}