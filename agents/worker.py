import os
import json
from models.record import Record
from models.decision import Decision
from pipeline.validate import validate_record
from utils.llm import call_llm

class WorkerAgent:
    name = "worker"
    model = "rule-worker"          # fallback, overridden by router
    prompt_version = "v1"

    def __init__(self):
        self.tokens_in = 0
        self.tokens_out = 0
        self.cost_usd = 0.0
        self.latency_ms = 0
        self.retries = 0
        self.confidence_threshold = float(os.getenv("WORKER_CONFIDENCE_THRESHOLD", 0.7))

    def process(self, record: Record, raw_dict: dict | None = None, model_override: str | None = None) -> Decision:
        # 1. Basic validation (existing)
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

        # 2. Build prompt for LLM – branded output structure
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
        - If the category is "REVIEW", you MUST flag for manual review.
        - If the category is "REPORT", it is generally approvable.
        - Otherwise, approve if all fields are present and amount < 100000.
        - Output a **branded** JSON with the following exact keys:
          "brand": "CedX-Fleet",
          "template": "worker-v1",
          "decision": "APPROVE" or "REVIEW" or "REJECT",
          "confidence": a float between 0 and 1,
          "reason": a string explaining your decision,
          "delivered_fields": {{"owner": ..., "category": ..., "amount": ..., "deadline": ...}}

        Do NOT include any other text – only the JSON object.
        """

        # 3. Choose model
        model = model_override or "gpt-4o-mini"

        # 4. Call LLM
        response = call_llm(
            agent=self.name,
            prompt=prompt,
            model=model,
            max_retries=2
        )

        # 5. Parse response
        try:
            data = json.loads(response["response"])
            # Ensure required keys exist
            decision = data.get("decision", "REJECT")
            confidence = float(data.get("confidence", 0.5))
            reason = data.get("reason", "LLM decision")
            delivered_fields = data.get("delivered_fields", {})
            # Branded fields can be ignored, but we keep them for traceability
            brand = data.get("brand", "unknown")
            template = data.get("template", "unknown")
        except:
            decision = "REJECT"
            confidence = 0.0
            reason = "Failed to parse LLM response"
            delivered_fields = {}

        # 6. Update metrics
        self.tokens_in = response.get("tokens_in", 0)
        self.tokens_out = response.get("tokens_out", 0)
        self.cost_usd = response.get("cost_usd", 0.0)
        self.latency_ms = response.get("latency_ms", 0)
        self.retries = response.get("retries", 0)
        self.model = model

        # 7. Confidence check – abstain if below threshold
        if confidence < self.confidence_threshold:
            # Abstain: force REVIEW and lower confidence
            decision = "REVIEW"
            reason = f"Confidence too low ({confidence:.2f} < {self.confidence_threshold}) – abstaining"
            confidence = confidence  # keep original, but will be flagged

        # 8. Build Decision
        requires_review = (decision == "REVIEW")
        if decision == "APPROVE":
            verdict = "pass"
        elif decision == "REVIEW":
            verdict = "needs_human"
        else:
            verdict = "fail"

        # If delivered_fields not provided, fallback to record fields
        if not delivered_fields:
            delivered_fields = {
                "owner": record.owner,
                "category": record.category,
                "amount": record.amount,
                "deadline": record.deadline
            }

        # Store branded info if needed for trace
        # We'll store them in a custom attribute or ignore – for simplicity we store in reason
        # , but we can keep them as extra fields if Decision supports it; we'll just log.

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
            delivered_fields=delivered_fields,
            tokens_in=self.tokens_in,
            tokens_out=self.tokens_out,
            cost_usd=self.cost_usd,
            latency_ms=self.latency_ms,
            retries=self.retries
        )