from models.record import Record
from models.decision import Decision
from utils.llm import call_llm
import json

class VerifierAgent:
    name = "verifier"
    model = "rule-verifier"
    prompt_version = "v1"

    def __init__(self):
        self.tokens_in = 0
        self.tokens_out = 0
        self.cost_usd = 0.0
        self.latency_ms = 0
        self.retries = 0

    def verify(self, record: Record, worker_decision: Decision, model_override: str | None = None) -> Decision:
        # 1. Build verification prompt
        prompt = f"""
        You are an independent verifier agent. Your task is to check the worker's decision.

        Original record:
        - ID: {record.id}
        - Owner: {record.owner}
        - Category: {record.category}
        - Amount: {record.amount}
        - Deadline: {record.deadline}
        - Notes: {record.notes}

        Worker's decision:
        - Decision: {worker_decision.decision}
        - Confidence: {worker_decision.confidence}
        - Reason: {worker_decision.reason}
        - Delivered fields: {worker_decision.delivered_fields}

        Instructions:
        - Verify that the decision is consistent with the record.
        - If the worker approved something that is obviously wrong (e.g., amount > 100000, missing owner), reject it.
        - If the worker flagged manual review correctly, approve that.
        - Output a JSON with keys: "verdict" ("pass" or "fail" or "needs_human"), "reason" (string).
        """

        # 2. Use given model or choose a reliable one (verifier can use a more expensive model)
        model = model_override or "gpt-4o-mini"

        # 3. Call LLM
        response = call_llm(
            agent=self.name,
            prompt=prompt,
            model=model,
            max_retries=2
        )

        # 4. Parse
        try:
            data = json.loads(response["response"])
            verdict = data.get("verdict", "fail")
            reason = data.get("reason", "No reason given")
        except:
            verdict = "fail"
            reason = "Failed to parse LLM response"

        # 5. Update metrics
        self.tokens_in = response.get("tokens_in", 0)
        self.tokens_out = response.get("tokens_out", 0)
        self.cost_usd = response.get("cost_usd", 0.0)
        self.latency_ms = response.get("latency_ms", 0)
        self.retries = response.get("retries", 0)
        self.model = model

        # 6. Overrule worker if verifier says fail
        if verdict == "fail":
            # Overrule: mark as REJECT
            worker_decision.decision = "REJECT"
            worker_decision.verdict = "fail"
            worker_decision.reason = reason
            worker_decision.reason_code = "UNVERIFIED_ANOMALY"
        elif verdict == "needs_human":
            worker_decision.decision = "REVIEW"
            worker_decision.verdict = "needs_human"
            worker_decision.reason = reason
            worker_decision.requires_review = True
        else:
            # pass: keep worker decision
            worker_decision.verdict = "pass"

        # 7. Store verification metrics in the decision (we'll copy them to the trace)
        worker_decision.verifier_tokens_in = self.tokens_in
        worker_decision.verifier_tokens_out = self.tokens_out
        worker_decision.verifier_cost_usd = self.cost_usd
        worker_decision.verifier_latency_ms = self.latency_ms
        worker_decision.verifier_retries = self.retries
        worker_decision.verifier_model = self.model

        return worker_decision