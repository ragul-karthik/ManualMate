"""Terminal 1: start Agent B TCP listener and keep it running."""
import json
import os
import time
from manualmate.messaging import AgentBServer
from manualmate.security import get_agent_key

bind_host = os.environ.get("MANUALMATE_AGENT_BIND", "127.0.0.1")
bind_port = int(os.environ.get("MANUALMATE_AGENT_PORT", "8765"))
server = AgentBServer(host=bind_host, port=bind_port, signing_key=get_agent_key())
server.start()
print(f"Authenticated Agent-B listening on {server.address[0]}:{server.address[1]}; Ctrl+C to stop")
try:
    while True:
        time.sleep(0.5)
        while server.received:
            print("Agent-B received:", json.dumps(server.received.pop(0)))
except KeyboardInterrupt:
    print("Stopping Agent-B")
finally:
    server.close()
