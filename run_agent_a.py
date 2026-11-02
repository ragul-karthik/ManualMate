"""Terminal 2: send a proactive state message to a running Agent B."""
import os
from manualmate.messaging import send_state
from manualmate.security import get_agent_key

host = os.environ.get("MANUALMATE_AGENT_HOST", "127.0.0.1")
port = int(os.environ.get("MANUALMATE_AGENT_PORT", "8765"))
response = send_state((host, port), {"question": "Check the inlet for a leak", "step": 3, "manual": "Northstar NDW-240"}, signing_key=get_agent_key())
print("Agent-A received:", response)
