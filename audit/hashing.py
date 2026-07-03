import hashlib
import json

def sha256_object(obj) -> str:
    data = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(data).hexdigest()

def sha256_file(filepath) -> str:
    with open(filepath, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()