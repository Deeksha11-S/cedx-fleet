import json
import os
from audit.hashing import sha256_object

class TranscriptWriter:
    def write(self, delivered_fields: dict) -> str:
        """Write a transcript file and return its SHA256 hash with 'sha256:' prefix."""
        os.makedirs("transcripts", exist_ok=True)
        hash_val = sha256_object(delivered_fields)
        filename = f"transcripts/{hash_val}.json"
        data = {
            "agent": "worker",
            "response": delivered_fields,
            "response_hash": "sha256:" + hash_val,
            "delivered_fields_hash": "sha256:" + hash_val
        }
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        return "sha256:" + hash_val