import os
import json
import time
import hashlib
from pathlib import Path
from typing import Any, Dict
import random

def call_llm(agent: str, prompt: str, model: str, max_retries: int = 2) -> Dict[str, Any]:
    """
    Call LLM with offline replay and mock support.
    Environment:
        REPLAY_LLM (true/false) – default false
        LLM_API_KEY – optional, only for real calls
        FORCE_MOCK (true/false) – override to use mock even with API key
    """
    replay = os.getenv("REPLAY_LLM", "false").lower() == "true"
    force_mock = os.getenv("FORCE_MOCK", "false").lower() == "true"

    Path("transcripts").mkdir(exist_ok=True)

    prompt_hash = hashlib.sha256(prompt.encode()).hexdigest()
    key = f"{agent}_{model}_{prompt_hash[:16]}"
    transcript_path = Path(f"transcripts/{key}.json")

    # Replay mode
    if replay:
        if not transcript_path.exists():
            raise FileNotFoundError(
                f"Transcript for {key} not found. "
                "Please commit transcripts/ folder or set REPLAY_LLM=false to generate them."
            )
        with open(transcript_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return {
            "response": data["response"],
            "tokens_in": data.get("tokens_in", 0),
            "tokens_out": data.get("tokens_out", 0),
            "cost_usd": data.get("cost_usd", 0.0),
            "latency_ms": data.get("latency_ms", 0.0),
            "retries": data.get("retries", 0),
        }

    # Determine if we should mock
    api_key = os.getenv("LLM_API_KEY")
    use_mock = force_mock or (api_key is None) or (api_key.strip() == "")

    # If we have a key and not forcing mock, try real API; fallback to mock if openai not installed
    if not use_mock:
        try:
            import openai
        except ImportError:
            print("  [WARN] openai not installed; falling back to mock")
            use_mock = True

    if use_mock:
        print(f"  [MOCK] {agent} using mock LLM response for {key}")
        if "worker" in agent:
            response_text = '{"decision": "APPROVE", "confidence": 0.95, "reason": "All fields valid"}'
        elif "verifier" in agent:
            response_text = '{"verdict": "pass", "reason": "Worker decision is consistent"}'
        else:
            response_text = '{"decision": "APPROVE", "confidence": 0.90, "reason": "Default mock"}'

        tokens_in = int(len(prompt.split()) * 1.3)
        tokens_out = int(len(response_text.split()) * 1.3)
        cost_usd = 0.00001
        latency_ms = random.randint(100, 500)
        retries = 0

        transcript_data = {
            "agent": agent,
            "model": model,
            "prompt": prompt,
            "response": response_text,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "cost_usd": cost_usd,
            "latency_ms": latency_ms,
            "retries": retries,
            "timestamp": time.time(),
        }
        with open(transcript_path, "w", encoding="utf-8") as f:
            json.dump(transcript_data, f, indent=2)

        return {
            "response": response_text,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "cost_usd": cost_usd,
            "latency_ms": latency_ms,
            "retries": retries,
        }

    # ---- Real API (openai installed and key provided) ----
    client = openai.OpenAI(
        api_key=api_key,
        base_url=os.getenv("LLM_BASE_URL", None)
    )

    pricing = {
        "gpt-4o-mini": (0.15, 0.60),
        "gpt-4": (30.0, 60.0),
    }
    input_price, output_price = pricing.get(model, (0.15, 0.60))

    retries = 0
    while retries <= max_retries:
        try:
            start = time.time()
            response = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=500
            )
            latency_ms = (time.time() - start) * 1000

            tokens_in = response.usage.prompt_tokens
            tokens_out = response.usage.completion_tokens
            content = response.choices[0].message.content

            cost_usd = (tokens_in * input_price / 1_000_000) + (tokens_out * output_price / 1_000_000)

            transcript_data = {
                "agent": agent,
                "model": model,
                "prompt": prompt,
                "response": content,
                "tokens_in": tokens_in,
                "tokens_out": tokens_out,
                "cost_usd": cost_usd,
                "latency_ms": latency_ms,
                "retries": retries,
                "timestamp": time.time(),
            }
            with open(transcript_path, "w", encoding="utf-8") as f:
                json.dump(transcript_data, f, indent=2)

            return {
                "response": content,
                "tokens_in": tokens_in,
                "tokens_out": tokens_out,
                "cost_usd": cost_usd,
                "latency_ms": latency_ms,
                "retries": retries,
            }
        except Exception as e:
            retries += 1
            if retries > max_retries:
                raise
            time.sleep(1)

    raise RuntimeError("LLM call failed after all retries.")