"""Authenticated handoff from the PDF reader agent to the setup display agent."""
from __future__ import annotations

import hashlib
import logging
from typing import Any

from manualmate.gemini_steps import GeminiStepsError, extract_steps_with_gemini, validate_steps
from manualmate.security import MessageAuthenticationError, canonical_json, sign_payload, verify_payload

logger = logging.getLogger("manualmate.security")
READER_AGENT = "manualmate-pdf-reader"
MESSAGE_TYPE = "VERIFIED_MANUAL_STEPS"


def _manual_fingerprint(pages: list[str]) -> str:
    joined = "\0".join(pages).encode("utf-8")
    return hashlib.sha256(joined).hexdigest()


class PDFReaderAgent:
    """Extract page-cited Gemini steps, then sign the typed result for handoff."""

    def __init__(self, signing_key: bytes):
        self.signing_key = signing_key

    def extract_and_sign(self, pages: list[str], api_key: str) -> dict[str, Any]:
        steps = extract_steps_with_gemini(pages, api_key)
        message = {
            "type": MESSAGE_TYPE,
            "sender": READER_AGENT,
            "payload": {
                "source": "gemini",
                "manual_sha256": _manual_fingerprint(pages),
                "page_count": len(pages),
                "steps": steps,
            },
        }
        envelope = {**message, "signature": sign_payload(canonical_json(message), self.signing_key)}
        logger.info("PDF reader signed extracted steps count=%d", len(steps))
        return envelope


class SetupDisplayAgent:
    """Verify sender, signature, and manual binding before accepting setup steps."""

    def __init__(self, verification_key: bytes, trusted_senders: set[str] | None = None):
        self.verification_key = verification_key
        self.trusted_senders = trusted_senders if trusted_senders is not None else {READER_AGENT}

    def verify_for_display(self, envelope: dict[str, Any], pages: list[str]) -> list[dict[str, Any]]:
        if not isinstance(envelope, dict):
            raise MessageAuthenticationError("reader-agent message is malformed")
        sender = envelope.get("sender")
        if sender not in self.trusted_senders:
            logger.warning("blocked untrusted PDF reader agent sender=%s", sender)
            raise MessageAuthenticationError("PDF reader agent is not trusted")
        if envelope.get("type") != MESSAGE_TYPE or not isinstance(envelope.get("payload"), dict):
            raise MessageAuthenticationError("reader-agent message type is invalid")

        message = {key: envelope[key] for key in ("type", "sender", "payload")}
        try:
            verify_payload(canonical_json(message), envelope.get("signature", ""), self.verification_key)
        except MessageAuthenticationError:
            logger.warning("blocked modified or unsigned PDF reader agent payload")
            raise

        payload = envelope["payload"]
        if payload.get("source") != "gemini" or payload.get("manual_sha256") != _manual_fingerprint(pages):
            logger.warning("blocked PDF reader payload with mismatched manual context")
            raise MessageAuthenticationError("reader-agent payload does not match this manual")
        if payload.get("page_count") != len(pages):
            raise MessageAuthenticationError("reader-agent page count does not match this manual")

        raw_steps = payload.get("steps")
        if not isinstance(raw_steps, list):
            raise MessageAuthenticationError("reader-agent steps are malformed")
        try:
            verified = validate_steps({"steps": [
                {
                    "title": item.get("title"),
                    "instruction": item.get("body"),
                    "page": item.get("page"),
                    "safety_note": item.get("tip", ""),
                }
                for item in raw_steps if isinstance(item, dict)
            ]}, len(pages))
        except (AttributeError, TypeError, ValueError, GeminiStepsError) as exc:
            raise MessageAuthenticationError("reader-agent steps failed validation") from exc
        logger.info("setup display agent verified reader signature and accepted steps count=%d", len(verified))
        return verified
