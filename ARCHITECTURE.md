# Architecture – Multi‑Agent Fleet for Financial Workflow

## High‑Level Topology
+-------------+      +------------------+      +--------------+
|    Seed     |----->|   Orchestrator   |----->|    Worker    |
| (feed/inbox)|      |  (router/budget) |      | (LLM decision)|
+-------------+      +------------------+      +------+-------+
                                                       |
                                                       v
                                          +-----------------------+
                                          |      Verifier         |
                                          | (independent critic) |
                                          +----------+------------+
                                                     |
                                           (checks/overrules)
                                                     |
                                        +------------+------------+
                                        |                         |
                                        v                         v
                                +--------------+      +------------------+
                                |   Approved   |      |   Rejected       |
                                |  (delivered) |      |  (exception)     |
                                +------+-------+      +--------+---------+
                                       |                        |
                                       +-----+------------------+
                                             |
                                             v
                                     +-----------------+
                                     |  Audit & Output |
                                     | (out/audit.json)|
                                     +-----------------+

## Agent Details

### 1. Orchestrator (`agents/orchestrator.py`)
- **Role:** Run controller – routes each record to the appropriate worker, enforces cost/step budgets, calls Verifier, and finalises the approval state.
- **Input:** `Record` (with optional `raw_dict`)
- **Output:** `PipelineResult` (includes final status, agent trace, approval trail)
- **`can_call`:** `["worker", "verifier"]`
- **Key logic:**
  - Calls `ModelRouter.route(record)` to decide cheap vs strong model.
  - Ensures `total_cost <= MAX_COST_USD_PER_RECORD` and `steps_used <= MAX_STEPS_PER_RECORD`.
  - If exceeded, sets `reason_code = "BUDGET_EXCEEDED"` and rejects.
  - Writes transcript for delivered records via `TranscriptWriter`.

### 2. Worker (`agents/worker.py`)
- **Role:** Primary decision maker – uses LLM to decide `APPROVE`, `REVIEW`, or `REJECT` based on record content.
- **Input:** `Record` + optional `raw_dict` + `model_override`
- **Output:** `Decision` (with `decision`, `confidence`, `reason`, `verdict`)
- **`can_call`:** None (it does not call other agents)
- **Key logic:**
  - Validates record via `validate_record()` first – if invalid, returns `REJECT` immediately.
  - Builds a prompt and calls `call_llm()` (mock or real).
  - Parses JSON response and populates `Decision`.
  - Records tokens, cost, latency, retries.

### 3. Verifier (`agents/verifier.py`)
- **Role:** Independent critic – checks the Worker’s output and can overrule it.
- **Input:** `Record` + `Decision` (worker’s output)
- **Output:** `Decision` (possibly overridden)
- **`can_call`:** None
- **Key logic:**
  - Builds a verification prompt asking to judge consistency.
  - Calls LLM (mock or real) to get `verdict` (`pass`, `fail`, `needs_human`).
  - If `verdict == "fail"`, sets worker decision to `REJECT` and marks `overruled` in trace.
  - If `verdict == "needs_human"`, sets decision to `REVIEW`.
  - Records verifier‑specific metrics in the decision object.

### 4. Model Router (`agents/router.py`)
- **Role:** Decides which model to use per record based on complexity.
- **Input:** `Record`
- **Output:** `{"model": str, "reason": str}`
- **Heuristic:**
  - `REVIEW` or `REPORT` category → +2 complexity
  - Amount > 10000 → +1
  - Notes length > 200 chars → +1
  - If complexity >= 3 → use `gpt-4`; else `gpt-4o-mini`

## Data Flow (Per Record)

1. **Intake:** Parse feed.json + inbox files → `Record` objects.
2. **Validation:** `validate_record()` checks required fields, injection, staleness, etc. – sets `reason_code` if invalid.
3. **Orchestrator route:** Calls `router.route(record)` to pick model.
4. **Worker call:** `worker.process(record, model_override)` – returns `Decision`.
5. **Budget check:** If cost/step exceeded → `BUDGET_EXCEEDED`.
6. **Verifier call:** `verifier.verify(record, worker_decision)` – may overrule.
7. **Finalisation:** `approval.finalize(verifier_decision)` → `APPROVED`, `REJECTED`, or `MANUAL_REVIEW`.
8. **Approval state machine:** Builds `approval_trail` with states `draft → in_review → approved/changes_requested → delivered/blocked`.
9. **Transcript:** If `APPROVED`, write `delivered_fields` to `transcripts/` with worker agent tag.
10. **Audit:** `AuditGenerator` builds `audit.json` with `agent_trace`, `cost`, `events`.

## Append‑Only Event Log
- `EventLogger` writes events with `seq` starting at 0, strictly incrementing.
- `audit.json` `events` array is the append‑only log – any attempt to modify or reorder is detected by the `probe-append-only` check.

## Traces
Each record contains an `agent_trace` array with one span per agent step:
```json
{
  "agent": "worker",
  "status": "ok",
  "verdict": "pass",
  "model": "gpt-4o-mini",
  "tokens_in": 123,
  "tokens_out": 45,
  "cost_usd": 0.00001,
  "latency_ms": 234,
  "retries": 0,
  "transcript_hash": "sha256:..."
}