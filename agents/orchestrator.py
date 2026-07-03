import os
from datetime import datetime
from agents.worker import WorkerAgent
from agents.verifier import VerifierAgent
from agents.router import ModelRouter
from pipeline.approval import ApprovalPipeline
from models.pipeline_result import PipelineResult
from audit.transcript_writer import TranscriptWriter
from audit.event_logger import EventLogger
from models.record import Record

class Orchestrator:
    def __init__(self):
        self.worker = WorkerAgent()
        self.verifier = VerifierAgent()
        self.router = ModelRouter()
        self.approval = ApprovalPipeline()
        self.transcript_writer = TranscriptWriter()
        self.events = EventLogger()
        self.max_cost = float(os.getenv("MAX_COST_USD_PER_RECORD", 0.10))
        self.max_steps = int(os.getenv("MAX_STEPS_PER_RECORD", 5))

    def process(self, record: Record, source_format: str = "feed", raw_dict: dict | None = None) -> PipelineResult:
        route = self.router.route(record)
        worker_model = route["model"]
        verifier_model = "gpt-4o-mini"

        trace = []
        approval = []
        steps_used = 0
        total_cost = 0.0

        # Worker step
        worker_decision = self.worker.process(record, raw_dict, model_override=worker_model)
        steps_used += 1
        total_cost += worker_decision.cost_usd
        trace.append({
            "agent": self.worker.name,
            "status": "ok" if worker_decision.verdict != "fail" else "rejected",
            "verdict": worker_decision.verdict,
            "model": worker_decision.model,
            "prompt_version": self.worker.prompt_version,
            "tokens_in": worker_decision.tokens_in,
            "tokens_out": worker_decision.tokens_out,
            "cost_usd": worker_decision.cost_usd,
            "latency_ms": worker_decision.latency_ms,
            "retries": worker_decision.retries,
            "transcript_hash": None
        })
        approval.append({
            "state": "draft",
            "actor": self.worker.name,
            "ts": datetime.utcnow().isoformat(),
            "reason": worker_decision.reason
        })
        self.events.log(actor="worker", action="decision_generated", record_id=record.id)

        if total_cost > self.max_cost or steps_used > self.max_steps:
            worker_decision.decision = "REJECT"
            worker_decision.reason_code = "BUDGET_EXCEEDED"
            worker_decision.verdict = "fail"
            return self._build_failed_result(record, source_format, worker_decision, trace, approval)

        # Verifier step
        verifier_decision = self.verifier.verify(record, worker_decision, model_override=verifier_model)
        steps_used += 1
        total_cost += verifier_decision.verifier_cost_usd
        trace.append({
            "agent": self.verifier.name,
            "status": "ok" if verifier_decision.verdict != "fail" else "overruled",
            "verdict": verifier_decision.verdict,
            "model": verifier_decision.verifier_model,
            "prompt_version": self.verifier.prompt_version,
            "tokens_in": verifier_decision.verifier_tokens_in,
            "tokens_out": verifier_decision.verifier_tokens_out,
            "cost_usd": verifier_decision.verifier_cost_usd,
            "latency_ms": verifier_decision.verifier_latency_ms,
            "retries": verifier_decision.verifier_retries,
            "transcript_hash": None
        })
        approval.append({
            "state": "in_review",
            "actor": self.verifier.name,
            "ts": datetime.utcnow().isoformat(),
            "reason": verifier_decision.reason
        })
        self.events.log(actor="verifier", action="decision_verified", record_id=record.id)

        if total_cost > self.max_cost or steps_used > self.max_steps:
            verifier_decision.decision = "REJECT"
            verifier_decision.reason_code = "BUDGET_EXCEEDED"
            verifier_decision.verdict = "fail"
            return self._build_failed_result(record, source_format, verifier_decision, trace, approval)

        final = self.approval.finalize(verifier_decision)
        self.events.log(actor="orchestrator", action=f"final_status={final}", record_id=record.id)

        delivered_fields = {
            "owner": record.owner,
            "category": record.category,
            "amount": record.amount,
            "deadline": record.deadline
        }

        transcript_hash = None
        if final == "APPROVED":
            transcript_hash = self.transcript_writer.write(delivered_fields)
            if trace and trace[0]["agent"] == self.worker.name:
                trace[0]["transcript_hash"] = transcript_hash

        now = datetime.utcnow().isoformat()
        if final == "APPROVED":
            approval.append({"state": "approved", "actor": self.verifier.name, "ts": now, "reason": None})
            approval.append({"state": "delivered", "actor": "system", "ts": now, "reason": None})
        elif final == "MANUAL_REVIEW":
            approval.append({"state": "changes_requested", "actor": self.verifier.name, "ts": now, "reason": verifier_decision.reason})
        else:
            approval.append({"state": "blocked", "actor": "system", "ts": now, "reason": verifier_decision.reason})

        return PipelineResult(
            record_id=record.id,
            version=record.version,
            source_format=source_format,
            worker_decision=verifier_decision,
            verifier_decision=verifier_decision,
            final_status=final,
            approval_trail=approval,
            agent_trace=trace,
            delivered_fields=delivered_fields,
            transcript_hash=transcript_hash
        )

    def _build_failed_result(self, record, source_format, decision, trace, approval):
        now = datetime.utcnow().isoformat()
        approval.append({"state": "blocked", "actor": "system", "ts": now, "reason": decision.reason})
        delivered_fields = {
            "owner": record.owner,
            "category": record.category,
            "amount": record.amount,
            "deadline": record.deadline
        }
        return PipelineResult(
            record_id=record.id,
            version=record.version,
            source_format=source_format,
            worker_decision=decision,
            verifier_decision=decision,
            final_status="REJECTED",
            approval_trail=approval,
            agent_trace=trace,
            delivered_fields=delivered_fields,
            transcript_hash=None
        )