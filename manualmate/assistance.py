"""End-to-end ManualMate help request pipeline used by the Ask tab."""
from __future__ import annotations

from collections.abc import Callable
import os
from typing import Any

from manualmate.bdi import BDIAgent
from manualmate.messaging import local_socket_demo
from manualmate.messaging import send_state
from manualmate.negotiation import negotiate_assistance
from manualmate.security import get_agent_key
from manualmate.utility import rank_candidates


def process_assistance_request(
    question: str,
    step: int,
    manual_name: str,
    setup_done: bool,
    answer_fn: Callable[[str], tuple[str, int]],
    *,
    recall_knowledge: dict[str, Any] | None = None,
    signing_key: bytes | None = None,
) -> dict[str, Any]:
    """Run ManualMate's unified answer, authenticated triage, BDI and assistance pipeline."""
    answer, page = answer_fn(question)
    active_state = {
        "question": question,
        "step": step + 1,
        "manual": manual_name,
        "setup_complete": setup_done,
    }
    signing_key = signing_key or get_agent_key()
    remote_host = os.environ.get("MANUALMATE_AGENT_HOST", "").strip()
    if remote_host:
        remote_port = int(os.environ.get("MANUALMATE_AGENT_PORT", "8765"))
        ack = send_state((remote_host, remote_port), active_state, signing_key=signing_key)
        received = {"type": "PROACTIVE", "sender": "Agent-A", "payload": active_state}
    else:
        received, ack = local_socket_demo(active_state, signing_key=signing_key)
    if ack.get("type") != "ACK":
        raise RuntimeError("Agent B rejected the request; no unverified safety advice was used")
    triage = ack["payload"]["triage"]
    answer = f"{answer}\n\nAgent-B safety check: {triage['safety_note']}"
    recall_knowledge = recall_knowledge or {"status": "not_checked", "matches": []}
    matches = recall_knowledge.get("matches", []) if recall_knowledge.get("status") == "checked" else []
    agent = BDIAgent({
        "manual_name": manual_name,
        "current_step": step + 1,
        "setup_complete": setup_done,
        "product_query": recall_knowledge.get("product_query", ""),
        "recall_status": recall_knowledge.get("status", "not_checked"),
        "recall_matches": matches,
    })
    agent.set_desire("resolve_manual_request_safely", priority=50)
    agent.observe(safety_category=triage["category"], agent_b_authenticated=ack["payload"].get("authentication") == "verified")
    if matches:
        first = matches[0]
        answer += (
            f"\n\nThe official recall search returned {len(matches)} possible match(es) for "
            f"{recall_knowledge.get('product_query', 'this product')}. Pause repair and verify the model "
            f"against the CPSC notice before continuing. First listing: "
            f"{first.get('product') or 'product recall'}; hazard: {first.get('hazard') or 'see official notice'}."
        )
        ranked, _ = rank_candidates(question)
        ranked = [(item, False) for item, _ in ranked]
        selected = None
        timeline = ["Negotiation paused: a possible product recall must be checked against the exact model first."]
    else:
        ranked, selected, timeline = negotiate_assistance(question)
    return {
        "answer": answer,
        "page": page,
        "received": received,
        "ack": ack,
        "triage": triage,
        "ranked": ranked,
        "selected": selected,
        "negotiation": timeline,
        "agent_state": agent.snapshot(),
        "recall_knowledge": recall_knowledge,
        "security": {"authenticated": ack["payload"].get("authentication") == "verified", "host": remote_host or "127.0.0.1 (local)"},
    }
