import os
import re
import sqlite3
from datetime import datetime, date, timezone
from typing import Dict, Any

from agents.worker import WorkerAgent
from agents.verifier import VerifierAgent
from agents.router import ModelRouter
from pipeline.approval import ApprovalPipeline
from models.pipeline_result import PipelineResult
from models.decision import Decision
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

        self.max_amount = float(os.getenv("MAX_AMOUNT_USD", 1_000_000.0))
        self.min_amount = float(os.getenv("MIN_AMOUNT_USD", 0.0))

        self.injection_patterns = [
            r"(?i)(\bselect\b.*\bfrom\b)",
            r"(?i)(\bunion\b.*\bselect\b)",
            r"(?i)(\bdrop\b.*\btable\b)",
            r"(?i)(\binsert\b.*\binto\b)",
            r"(?i)(\bupdate\b.*\bset\b)",
            r"(?i)(\bdelete\b.*\bfrom\b)",
            r"(?i)(--\s*$)",
            r"(?i)(;\s*$)",
            r"(?i)(\bexec\b.*\bxp_)",
            r"(?i)(\bcmd\b.*\bshell\b)",
        ]

    def _validate_record(self, record: Record) -> Dict[str, Any]:
        errors = []

        required = ["id", "owner", "amount", "deadline"]
        for field in required:
            value = getattr(record, field, None)
            if value is None or (isinstance(value, str) and not value.strip()):
                errors.append(f"Missing or empty field: {field}")

        if record.deadline:
            try:
                deadline_date = datetime.strptime(record.deadline, "%Y-%m-%d").date()
                if deadline_date < date.today():
                    errors.append(f"Deadline {record.deadline} is in the past")
            except ValueError:
                errors.append(f"Invalid deadline format: {record.deadline} (expected YYYY-MM-DD)")

        text_fields = [record.notes, record.owner, record.category, record.id]
        for field in text_fields:
            if not field:
                continue
            for pattern in self.injection_patterns:
                if re.search(pattern, field):
                    errors.append(f"Potential prompt injection detected in field: {field[:50]}...")
                    break

        if record.amount is not None:
            if record.amount < self.min_amount:
                errors.append(f"Amount {record.amount} is below minimum {self.min_amount}")
            if record.amount > self.max_amount:
                errors.append(f"Amount {record.amount} exceeds maximum {self.max_amount}")

        if errors:
            return {"valid": False, "reason": "; ".join(errors)}
        return {"valid": True, "reason": None}

    def _route_to_exception_queue(self, record: Record, reason: str, source_format: str):
        conn = sqlite3.connect("exception_queue.db")
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS exception_queue (
                id TEXT,
                version INTEGER,
                source_format TEXT,
                owner TEXT,
                deadline TEXT,
                category TEXT,
                amount REAL,
                notes TEXT,
                reason TEXT,
                timestamp TEXT
            )
        """)
        cursor.execute("""
            INSERT INTO exception_queue
            (id, version, source_format, owner, deadline, category, amount, notes, reason, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            record.id,
            record.version,
            source_format,
            record.owner,
            record.deadline,
            record.category,
            record.amount,
            record.notes,
            reason,
            datetime.now(timezone.utc).isoformat()
        ))
        conn.commit()
        conn.close()
        # Log without 'extra' argument (just record_id)
        self.events.log(actor="orchestrator", action="exception_queued", record_id=record.id)

    def process(self, record: Record, source_format: str = "feed", raw_dict: dict | None = None) -> PipelineResult:
        validation = self._validate_record(record)
        if not validation["valid"]:
            self._route_to_exception_queue(record, validation["reason"], source_format)

            now = datetime.now(timezone.utc).isoformat()
            approval_trail = [
                {"state": "blocked", "actor": "orchestrator", "ts": now, "reason": validation["reason"]}
            ]

            # Create a minimal Decision object, then assign extra attributes
            dummy = Decision(
                record_id=record.id,
                decision="REJECT",
                confidence=0.0,
                reason=validation["reason"],
                reason_code="VALIDATION_FAILED",
                requires_review=False,
                model="N/A",
                prompt_version="N/A",
                verdict="fail"
            )
            # Assign additional fields that are not in the constructor
            dummy.delivered_fields = {}
            dummy.tokens_in = 0
            dummy.tokens_out = 0
            dummy.cost_usd = 0.0
            dummy.latency_ms = 0
            dummy.retries = 0

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
                worker_decision=dummy,
                verifier_decision=dummy,
                final_status="REJECTED",
                approval_trail=approval_trail,
                agent_trace=[],
                delivered_fields=delivered_fields,
                transcript_hash=None
            )

        # ----- Original processing (unchanged) -----
        route = self.router.route(record)
        worker_model = route["model"]
        verifier_model = "gpt-4o-mini"

        trace = []
        approval = []
        steps_used = 0
        total_cost = 0.0

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
            "ts": datetime.now(timezone.utc).isoformat(),
            "reason": worker_decision.reason
        })
        self.events.log(actor="worker", action="decision_generated", record_id=record.id)

        if total_cost > self.max_cost or steps_used > self.max_steps:
            worker_decision.decision = "REJECT"
            worker_decision.reason_code = "BUDGET_EXCEEDED"
            worker_decision.verdict = "fail"
            return self._build_failed_result(record, source_format, worker_decision, trace, approval)

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
            "ts": datetime.now(timezone.utc).isoformat(),
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

        now = datetime.now(timezone.utc).isoformat()
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
        now = datetime.now(timezone.utc).isoformat()
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