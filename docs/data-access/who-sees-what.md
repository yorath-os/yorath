---
title: Who sees what
description: "Effective access is user ∩ agent ∩ data, checked in the Gateway before the model sees anything. A worked example with the HR demo."
---

An agent never sees more than the person it works for, and never more than its own file allows:

**effective access = what the user may see ∩ what the agent may use ∩ what the data allows**

The Gateway computes this on every read and every tool call, before the model gets anything. Instructions in the prompt don't change it: a document the user can't open never reaches the model, so the model can't leak it.

## Example: the HR demo

Enable the HR demo set (wizard → Documents, or the Data screen). It creates a vacation policy, a vacation table and three users. Ask `document-assistant` "How many vacation days does Miller have left?" as each of them:

| Login | Who | What they see in the vacation table |
|---|---|---|
| `anna.parker` | employee, Engineering | only her own row |
| `max.lewis` | Engineering manager | himself and his reports |
| `olivia.newman` | HRBP for Field Sales | Field Sales employees |

The same agent with the same instructions gives three different answers, and Journal → Denials shows the rows that were not given to it.

## Where each part comes from

- **User** — role, department and links set on the Users screen — [Users and roles](users-and-roles.md).
- **Agent** — `data`, `tools` and `max_data_class` in the agent's file — [Agent file format](../reference/agent-file-format.md).
- **Data** — the access rules and class of each source on the Data screen — [Data classes](data-classes.md).

For connected agents (OpenClaw, Hermes) and agents by API key, Yorath controls model calls, requests and the network, not the agent's own tools — see [Agents](https://yorath.io/agents/#summary).
