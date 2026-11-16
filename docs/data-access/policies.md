---
title: Policies
description: "The four built-in policy sets that decide every access: user permissions, agent restrictions, models and data classes, calls between agents."
---

Policies are [Cedar](https://www.cedarpolicy.com/) rules checked in the Gateway on every access to data, every tool call, every model call and every call between agents — before the model sees anything. The Policies screen shows them; they are read-only in the panel, so a mistake in an editor can't open access.

| Set | What it decides |
|---|---|
| User permissions | Who sees what. The owner sees everything. Documents are open by role, department and author. Employee records: your own, your reports', those of assigned departments; the HR administrator sees all. |
| Agent restrictions | An agent reads only its own sources, no higher than its data class, and calls only its own tools. A tool that sends data outside is not called after reading data above its ceiling. Restrictions override user permissions. |
| Models and data classes | A model is called only if the data class in the request is allowed for it. Otherwise the call is blocked; the request is not switched to another model. |
| Calls between agents | An agent calls only an allowed recipient, and the data class in the context is no higher than the recipient's ceiling. |

User rights are `permit` rules; agent and data restrictions are `forbid` rules, and a `forbid` always wins. So effective access is the intersection: **user ∩ agent ∩ data** — see [Who sees what](who-sees-what.md).

## Change a policy

Policies live in files and are loaded with `yorath sync-policies` inside the core container (see [Commands](../reference/commands.md#the-cores-own-commands)); the owner's changes are kept on update, and the screen marks a changed policy.

Every decision — allowed or denied, with the policy that decided it — is in the [Journal](journal.md).
