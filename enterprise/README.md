# enterprise/

This directory is reserved for Enterprise modules (corporate IAM, compliance, federation, agent workforce management). In the MVP it is **empty by design**: the Core / Enterprise boundary is Foundation Now (Requirements §10), and everything in the MVP is Community under Apache 2.0.

Boundary rules:

- Core (`core/`) works entirely without this directory; no core path imports from here.
- Enterprise modules plug in only through the core Plugin/Extension API (`yorath.plugins`, entry points in the `yorath.plugins` group).
- License checks, once they exist, live here and **never stop basic agent execution**.
