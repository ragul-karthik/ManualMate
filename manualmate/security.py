"""Local HMAC message authentication for trusted ManualMate TCP agents."""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from pathlib import Path


class MessageAuthenticationError(ValueError):
    """Raised before an untrusted or modified message can be processed."""


KEY_PATH = Path(__file__).resolve().parents[1] / ".manualmate" / "agent_hmac.key"


def get_agent_key() -> bytes:
    """Read a shared deployment key or create a private local demo key."""
    configured = os.environ.get("MANUALMATE_AGENT_HMAC_KEY", "").strip()
    if configured:
        try:
            key = bytes.fromhex(configured)
        except ValueError as exc:
            raise ValueError("MANUALMATE_AGENT_HMAC_KEY must be a 64-character hexadecimal key") from exc
        if len(key) != 32:
            raise ValueError("MANUALMATE_AGENT_HMAC_KEY must decode to exactly 32 bytes")
        return key

    KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(KEY_PATH, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        key = KEY_PATH.read_bytes()
        if len(key) != 32:
            raise ValueError("ManualMate local agent key is invalid; remove it and restart to create a fresh key")
        return key
    key = secrets.token_bytes(32)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(key)
    return key


def canonical_json(value: dict) -> bytes:
    import json

    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def sign_payload(payload: bytes, key: bytes) -> str:
    return hmac.new(key, payload, hashlib.sha256).hexdigest()


def verify_payload(payload: bytes, signature: str, key: bytes) -> None:
    expected = sign_payload(payload, key)
    if not isinstance(signature, str) or not hmac.compare_digest(expected, signature):
        raise MessageAuthenticationError("message signature is invalid")
