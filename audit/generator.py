import json
import os
import hashlib
from datetime import datetime, timezone
from audit.hashing import sha256_object

class AuditGenerator:
    def __init__(self):
        os.makedirs("out", exist_ok=True)

    def generate(self, pipeline_results, events):
        case_id = os.getenv("CASE_ID", "CEDX-DEMO")

        # ---- Compute amendment from CASE_ID ----
        H = hashlib.sha256(case_id.encode()).hexdigest()
        roles = ["risk_officer", "legal_counsel", "compliance", "finance_controller"]
        role = roles[int(H[0], 16) % 4]
        threshold = 10000 + (int(H[1:3], 16) % 50) * 1000

        audit = {
            "case_id": case_id,
            "pipeline_version": "1.0.0",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "seed_dir": os.getenv("SEED_DIR", "seed"),
            "pipeline_now": os.getenv("PIPELINE_NOW", "2026-06-26"),
            "amendment": {"role": role, "threshold": threshold},
            "agents": [
                {"name": "orchestrator", "role": "orchestrator", "models": ["rule-engine"],
                 "prompt_version": "v1", "can_call": ["worker", "verifier"]},
                {"name": "worker", "role": "worker", "models": ["rule-worker"],
                 "prompt_version": "v1", "can_call": []},
                {"name": "verifier", "role": "verifier", "models": ["rule-verifier"],
                 "prompt_version": "v1", "can_call": []}
            ],
            "cost": self.build_cost_summary(pipeline_results),
            "records": [],
            "events": events
        }

        for result in pipeline_results:
            record = self.build_record(result)
            if record is not None:
                audit["records"].append(record)

        audit["output_package_hash"] = "sha256:" + sha256_object(audit)

        with open("out/audit.json", "w", encoding="utf-8") as f:
            json.dump(audit, f, indent=4)

        print("\nAudit written to out/audit.json")
        return audit

    def build_cost_summary(self, pipeline_results):
        total = 0.0
        latency = []
        for r in pipeline_results:
            if not r:
                continue
            w = r.worker_decision
            if w:
                total += w.cost_usd or 0.0
                total += w.verifier_cost_usd or 0.0
                latency.append(w.latency_ms or 0)
                latency.append(w.verifier_latency_ms or 0)
        avg = total / len(pipeline_results) if pipeline_results else 0.0
        return {
            "total_usd": round(total, 6),
            "avg_usd_per_record": round(avg, 6),
            "p95_latency_ms": max(latency) if latency else 0,
            "records": len(pipeline_results),
            "projected_usd_per_10k": round(avg * 10000, 2)
        }

    def build_record(self, result):
        if result is None:
            return None

        if result.final_status == "APPROVED":
            status = "delivered"
            reason_code = None
        elif result.final_status == "MANUAL_REVIEW":
            status = "exception"
            reason_code = "UNVERIFIED_ANOMALY"
        else:
            status = "exception"
            reason_code = result.worker_decision.reason_code if result.worker_decision else None

        delivered_fields = result.delivered_fields or {}
        delivered_hash = sha256_object(delivered_fields)

        return {
            "id": result.record_id,
            "version": result.version,
            "source_format": result.source_format,
            "source_version_hash": "sha256:" + delivered_hash,
            "status": status,
            "reason_code": reason_code,
            "reason_class": None,
            "transcript_hash": result.transcript_hash,
            "delivered_fields": delivered_fields,
            "delivered_fields_hash": "sha256:" + delivered_hash,
            "agent_trace": result.agent_trace or [],
            "approval_trail": result.approval_trail or []
        }