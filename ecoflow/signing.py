"""EcoFlow Developer API request signing.

Scheme (developer.ecoflow.com, General Information): flatten the request
params (dot notation for nested objects, bracket notation for arrays), sort
key=value pairs ASCII-ascending, append accessKey/nonce/timestamp, then
HMAC-SHA256 the string with the secretKey (lowercase hex).
"""

import hashlib
import hmac
import random
import time


def flatten_params(params, prefix=""):
    flat = {}
    for key, value in params.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            flat.update(flatten_params(value, path))
        elif isinstance(value, list):
            for i, item in enumerate(value):
                if isinstance(item, dict):
                    flat.update(flatten_params(item, f"{path}[{i}]"))
                else:
                    flat[f"{path}[{i}]"] = item
        else:
            flat[path] = value
    return flat


def build_sign_string(params, access_key, nonce, timestamp):
    parts = []
    if params:
        flat = flatten_params(params)
        parts = [f"{k}={flat[k]}" for k in sorted(flat)]
    parts += [f"accessKey={access_key}", f"nonce={nonce}", f"timestamp={timestamp}"]
    return "&".join(parts)


def hmac_sha256(message, secret_key):
    return hmac.new(
        secret_key.encode("utf-8"), message.encode("utf-8"), hashlib.sha256
    ).hexdigest()


def build_headers(params, access_key, secret_key, nonce=None, timestamp=None):
    nonce = nonce or f"{random.randint(0, 999999):06d}"
    timestamp = timestamp or str(int(time.time() * 1000))
    sign = hmac_sha256(build_sign_string(params, access_key, nonce, timestamp), secret_key)
    return {
        "accessKey": access_key,
        "nonce": nonce,
        "timestamp": timestamp,
        "sign": sign,
    }
