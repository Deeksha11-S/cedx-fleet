import os
import json
from models.record import Record
from models.decision import Decision
from utils.llm import call_llm

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

    def _detect_hallucinations(self, record: Record, worker_decision: Decision) -> tuple[bool, str]:
        """
        Compare delivered fields from worker against original record.
        Returns (is_hallucination, reason)
        """
        delivered = worker_decision.delivered_fields or {}
        mismatches = []

        # Check critical fields
        if delivered.get("owner") != record.owner:
            mismatches.append(f"owner: {delivered.get('owner')} vs {record.owner}")
        # Amount: compare with tolerance
        if delivered.get("amount") is not None and record.amount is not None:
            if abs(float(delivered["amount"]) - record.amount) > 0.01:
                mismatches.append(f"amount: {delivered['amount']} vs {record.amount}")
        elif delivered.get("amount") != record.amount:
            mismatches.append(f"amount: {delivered.get('amount')} vs {record.amount}")
        if delivered.get("deadline") != record.deadline:
            mismatches.append(f"deadline: {delivered.get('deadline')} vs {record.deadline}")

        if mismatches:
            return True, f"Hallucination detected: {', '.join(mismatches)}"
        return False, ""

    def verify(self, record: Record, worker_decision: Decision, model_override: str | None = None) -> Decision:
        # ---- Hallucination detection (independent check) ----
        is_hallucination, hallucination_reason = self._detect_hallucinations(record, worker_decision)
        if is_hallucination:
            # Reject immediately – overrule worker
            worker_decision.decision = "REJECT"
            worker_decision.verdict = "fail"
            worker_decision.reason = hallucination_reason
            worker_decision.reason_code = "HALLUCINATION"
            # Also set verification metrics to zero (no LLM call)
            worker_decision.verifier_tokens_in = 0
            worker_decision.verifier_tokens_out = 0
            worker_decision.verifier_cost_usd = 0.0
            worker_decision.verifier_latency_ms = 0
            worker_decision.verifier_retries = 0
            worker_decision.verifier_model = "rule-verifier"
            return worker_decision

        # ---- Proceed with LLM verification ----
        prompt_template = """
        You are an independent verifier agent. Your task is to check the worker's decision.

        Original record:
        - ID: {record_id}
        - Owner: {owner}
        - Category: {category}
        - Amount: {amount}
        - Deadline: {deadline}
        - Notes: {notes}

        Worker's decision:
        - Decision: {decision}
        - Confidence: {confidence}
        - Reason: {reason}
        - Delivered fields: {delivered_fields}

        Instructions:
        - Verify that the decision is consistent with the record.
        - If the worker approved something that is obviously wrong (e.g., amount > 100000, missing owner), reject it.
        - If the worker flagged manual review correctly, approve that.
        - Output a JSON with keys: "verdict" ("pass" or "fail" or "needs_human"), "reason" (string).
        Do NOT include any other text.
        """

        # Use given model or default
        model = model_override or "gpt-4o-mini"

        # Helper to perform one verification attempt
        def _call_verifier(prompt: str) -> dict:
            response = call_llm(
                agent=self.name,
                prompt=prompt,
                model=model,
                max_retries=2
            )
            try:
                data = json.loads(response["response"])
                verdict = data.get("verdict", "fail")
                reason = data.get("reason", "No reason given")
            except:
                verdict = "fail"
                reason = "Failed to parse LLM response"
            # Return metrics as well
            return {
                "verdict": verdict,
                "reason": reason,
                "tokens_in": response.get("tokens_in", 0),
                "tokens_out": response.get("tokens_out", 0),
                "cost_usd": response.get("cost_usd", 0.0),
                "latency_ms": response.get("latency_ms", 0),
                "retries": response.get("retries", 0)
            }

        # First attempt
        prompt = prompt_template.format(
            record_id=record.id,
            owner=record.owner,
            category=record.category,
            amount=record.amount,
            deadline=record.deadline,
            notes=record.notes,
            decision=worker_decision.decision,
            confidence=worker_decision.confidence,
            reason=worker_decision.reason,
            delivered_fields=worker_decision.delivered_fields
        )
        result = _call_verifier(prompt)
        verdict = result["verdict"]
        reason = result["reason"]

        # If, fail, retry once with a modified prompt (escalation)
        if verdict == "fail":
            # Retry with additional instruction to be more thorough
            retry_prompt = prompt + "\n\nPlease double‑check your assessment. If you are still unsure, output 'needs_human'."
            result2 = _call_verifier(retry_prompt)
            verdict2 = result2["verdict"]
            reason2 = result2["reason"]

            # If still fail, escalate to manual review
            if verdict2 == "fail":
                # Escalate: set to REVIEW instead of REJECT
                worker_decision.decision = "REVIEW"
                worker_decision.verdict = "needs_human"
                worker_decision.requires_review = True
                worker_decision.reason = f"Verifier failed twice; escalating: {reason2}"
                worker_decision.reason_code = "ESCALATED"
                # Use metrics from second attempt
                result = result2
            else:
                # Second attempt passed – use its result
                verdict = verdict2
                reason = reason2
                result = result2

        # Update metrics (from the last attempt)
        self.tokens_in = result.get("tokens_in", 0)
        self.tokens_out = result.get("tokens_out", 0)
        self.cost_usd = result.get("cost_usd", 0.0)
        self.latency_ms = result.get("latency_ms", 0)
        self.retries = result.get("retries", 0)
        self.model = model

        # Apply final verdict
        if verdict == "fail":
            # Overrule: reject
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
            # Optionally, we can update reason to include verifier's positive check
            worker_decision.reason = f"{worker_decision.reason} (verified)"

        # Store verification metrics
        worker_decision.verifier_tokens_in = self.tokens_in
        worker_decision.verifier_tokens_out = self.tokens_out
        worker_decision.verifier_cost_usd = self.cost_usd
        worker_decision.verifier_latency_ms = self.latency_ms
        worker_decision.verifier_retries = self.retries
        worker_decision.verifier_model = self.model

        return worker_decision