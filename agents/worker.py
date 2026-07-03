from models.record import Record
from models.decision import Decision
from pipeline.validate import validate_record
from utils.llm import call_llm
import json

class WorkerAgent:
    name = "worker"
    model = "rule-worker"          # fallback, but we use router's choice
    prompt_version = "v1"

    def __init__(self):
        self.tokens_in = 0
        self.tokens_out = 0
        self.cost_usd = 0.0
        self.latency_ms = 0
        self.retries = 0

    def process(self, record: Record, raw_dict: dict | None = None, model_override: str | None = None) -> Decision:
        # 1. Validate the record
        validation = validate_record(record, raw_dict)
        if not validation.valid:
            return Decision(
                record_id=record.id,
                decision="REJECT",
                confidence=1.0,
                reason=validation.reason_code,
                reason_code=validation.reason_code,
                requires_review=False,
                model="rule-worker",
                prompt_version=self.prompt_version,
                verdict="fail"
            )

        # 2. Build prompt for LLM
        prompt = f"""
        You are a worker agent that processes a work request.
        Given the following record, extract the core fields and decide if it can be approved.

        Record:
        - ID: {record.id}
        - Owner: {record.owner}
        - Category: {record.category}
        - Amount: {record.amount}
        - Deadline: {record.deadline}
        - Notes: {record.notes}

        Instructions:
        - If the category is "REVIEW", you must flag for manual review.
        - If the category is "REPORT", it is generally approvable.
        - Otherwise, approve if all fields are present and amount < 100000.
        - Output a JSON with keys: "decision" ("APPROVE" or "REVIEW" or "REJECT"), "confidence" (0-1), "reason" (string).
        """

        # 3. Choose model (use override if provided, else use cheap default)
        model = model_override or "gpt-4o-mini"

        # 4. Call LLM (with replay support)
        response = call_llm(
            agent=self.name,
            prompt=prompt,
            model=model,
            max_retries=2
        )

        # 5. Parse response
        try:
            data = json.loads(response["response"])
            decision = data.get("decision", "REJECT")
            confidence = data.get("confidence", 0.5)
            reason = data.get("reason", "LLM decision")
        except:
            decision = "REJECT"
            confidence = 0.0
            reason = "Failed to parse LLM response"

        # 6. Update metrics from LLM call
        self.tokens_in = response.get("tokens_in", 0)
        self.tokens_out = response.get("tokens_out", 0)
        self.cost_usd = response.get("cost_usd", 0.0)
        self.latency_ms = response.get("latency_ms", 0)
        self.retries = response.get("retries", 0)
        self.model = model

        # 7. Build Decision object
        requires_review = (decision == "REVIEW")
        if decision == "APPROVE":
            verdict = "pass"
        elif decision == "REVIEW":
            verdict = "needs_human"
        else:
            verdict = "fail"

        # 8. Derive delivered_fields (what will be output)
        # In this simple version, we just pass through the record's fields.
        delivered_fields = {
            "owner": record.owner,
            "category": record.category,
            "amount": record.amount,
            "deadline": record.deadline
        }

        return Decision(
            record_id=record.id,
            decision=decision,
            confidence=confidence,
            reason=reason,
            reason_code=None if decision != "REJECT" else "LLM_REJECT",
            requires_review=requires_review,
            model=model,
            prompt_version=self.prompt_version,
            verdict=verdict,
            delivered_fields=delivered_fields,   # we add this field to Decision
            tokens_in=self.tokens_in,
            tokens_out=self.tokens_out,
            cost_usd=self.cost_usd,
            latency_ms=self.latency_ms,
            retries=self.retries
        )