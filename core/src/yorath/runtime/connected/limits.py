"""What a worker node may send to the main node in one request (change pre-public-hardening, S6/R3).

A node passed the owner's approval, but its host can be compromised: the main node bounds what it
accepts, and the node service keeps under the same bounds so that its own reports are never refused.
"""

NODE_BODY_BYTES = 1_000_000  # any POST /node/*: larger → 413
EGRESS_LINES = 5_000  # proxy log lines per POST /node/egress
EGRESS_CHUNK_BYTES = 512 * 1024  # what the node service reads from its proxy log per pass
BLOCKED_EVENTS = 500  # egress.blocked audit events per batch; the rest only in the hourly summary
HOST_AGENTS = 200  # agents outside Yorath in one host report
HOST_TRACES = 200
HOST_CONTAINERS = 500
