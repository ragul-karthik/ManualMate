"""Length-limited, newline-delimited JSON protocol over local TCP sockets."""
from __future__ import annotations

import json
import logging
import os
import socket
import threading
from typing import Any

from manualmate.security import MessageAuthenticationError, canonical_json, get_agent_key, sign_payload, verify_payload

MAX_MESSAGE = 64 * 1024
logger = logging.getLogger("manualmate.security")


def _encode_agent_frame(message: dict[str, Any], key: bytes | None) -> bytes:
    if key is None:
        return (json.dumps(message, separators=(",", ":")) + "\n").encode("utf-8")
    payload = canonical_json(message)
    envelope = {"message": message, "signature": sign_payload(payload, key)}
    return (json.dumps(envelope, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8")


def _decode_agent_frame(raw: bytes, key: bytes | None) -> dict[str, Any]:
    if key is None:
        return decode_message(raw)
    if len(raw) > MAX_MESSAGE:
        raise ValueError("Message exceeds 64 KiB limit")
    try:
        envelope = json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise ValueError("Malformed signed message envelope") from None
    if not isinstance(envelope, dict) or not isinstance(envelope.get("message"), dict):
        raise ValueError("Malformed signed message envelope")
    message = envelope["message"]
    verify_payload(canonical_json(message), envelope.get("signature", ""), key)
    return decode_message(canonical_json(message) + b"\n")


def encode_message(kind: str, sender: str, payload: dict[str, Any]) -> bytes:
    if kind not in {"STATE", "PROACTIVE", "ACK", "ERROR"}:
        raise ValueError(f"Unsupported message type: {kind}")
    return (json.dumps({"type": kind, "sender": sender, "payload": payload}, separators=(",", ":")) + "\n").encode("utf-8")


def decode_message(raw: bytes) -> dict[str, Any]:
    if len(raw) > MAX_MESSAGE:
        raise ValueError("Message exceeds 64 KiB limit")
    obj = json.loads(raw.decode("utf-8"))
    if not isinstance(obj, dict) or obj.get("type") not in {"STATE", "PROACTIVE", "ACK", "ERROR"} or not isinstance(obj.get("payload"), dict) or not isinstance(obj.get("sender"), str):
        raise ValueError("Malformed typed message")
    return obj


class AgentBServer:
    def __init__(self, host: str = "127.0.0.1", port: int = 0, signing_key: bytes | None = None, allowed_hosts: set[str] | None = None):
        self.signing_key = signing_key
        self.allowed_hosts = allowed_hosts if allowed_hosts is not None else self._configured_allowed_hosts()
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind((host, port))
        self.sock.listen(4)
        self.sock.settimeout(0.1)
        self.address = self.sock.getsockname()
        self.received: list[dict[str, Any]] = []
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)

    @staticmethod
    def _configured_allowed_hosts() -> set[str]:
        configured = os.environ.get("MANUALMATE_AGENT_ALLOWED_HOSTS", "127.0.0.1,::1")
        return {host.strip() for host in configured.split(",") if host.strip()}

    def start(self) -> None:
        self._thread.start()

    def close(self) -> None:
        self._stop.set()
        self._thread.join(1)
        self.sock.close()

    def _serve(self) -> None:
        while not self._stop.is_set():
            try:
                conn, _ = self.sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            with conn:
                conn.settimeout(2)
                peer_host = conn.getpeername()[0]
                data = bytearray()
                while b"\n" not in data and len(data) <= MAX_MESSAGE:
                    chunk = conn.recv(4096)
                    if not chunk:
                        break
                    data.extend(chunk)
                try:
                    if peer_host not in self.allowed_hosts:
                        raise MessageAuthenticationError("connection host is not trusted")
                    message = _decode_agent_frame(bytes(data).split(b"\n", 1)[0], self.signing_key)
                    if message["type"] not in {"STATE", "PROACTIVE"}:
                        raise ValueError("Agent B expects STATE or PROACTIVE")
                    self.received.append(message)
                    state = message["payload"]
                    question = str(state.get("question", "")).lower()
                    if any(term in question for term in ("leak", "leaking", "water at")):
                        triage = {"category": "water leak", "safety_note": "Stop the cycle, unplug the dishwasher, and close the water tap before inspecting the hose."}
                    elif any(term in question for term in ("electric", "power cord", "shock", "smoke")):
                        triage = {"category": "electrical risk", "safety_note": "Stop using the appliance and keep it unplugged. Contact a qualified technician; do not open electrical panels."}
                    elif any(term in question for term in ("drain", "standing water", "filter")):
                        triage = {"category": "drainage", "safety_note": "Keep a towel and bucket nearby; unplug the unit before cleaning the filter."}
                    else:
                        triage = {"category": "setup help", "safety_note": "Follow the matching manual page and stop if the appliance appears unsafe."}
                    response_message = {"type": "ACK", "sender": "Agent-B", "payload": {"status": "received", "summary": f"Agent-B parsed {message['type']} from {message['sender']}; active step={state.get('step', 'n/a')}", "triage": triage, "authentication": "verified" if self.signing_key else "legacy unsigned mode"}}
                    response = _encode_agent_frame(response_message, self.signing_key)
                except MessageAuthenticationError as exc:
                    logger.warning("blocked untrusted Agent-A message: %s", exc)
                    response = _encode_agent_frame({"type": "ERROR", "sender": "Agent-B", "payload": {"error": "request rejected", "security": "blocked"}}, self.signing_key)
                except (ValueError, json.JSONDecodeError, UnicodeDecodeError) as exc:
                    response = _encode_agent_frame({"type": "ERROR", "sender": "Agent-B", "payload": {"error": str(exc)}}, self.signing_key)
                conn.sendall(response)


def send_state(address: tuple[str, int], state: dict[str, Any], proactive: bool = True, signing_key: bytes | None = None) -> dict[str, Any]:
    kind = "PROACTIVE" if proactive else "STATE"
    message = {"type": kind, "sender": "Agent-A", "payload": state}
    with socket.create_connection(address, timeout=3) as sock:
        sock.sendall(_encode_agent_frame(message, signing_key))
        data = bytearray()
        while b"\n" not in data and len(data) <= MAX_MESSAGE:
            chunk = sock.recv(4096)
            if not chunk:
                break
            data.extend(chunk)
    return _decode_agent_frame(bytes(data).split(b"\n", 1)[0], signing_key)


def local_socket_demo(state: dict[str, Any], signing_key: bytes | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    server = AgentBServer(signing_key=signing_key)
    server.start()
    try:
        ack = send_state(server.address, state, signing_key=signing_key)
        return server.received[0], ack
    finally:
        server.close()
