---
title: Journal
description: "Every agent run with model calls, tokens, access checks and denials, and the domains agents contacted. What is recorded and what is not."
---

Journal shows every run of every agent in one place, whoever started it: the chat, an API key, a GitHub issue or another agent.

| Tab | What it shows |
|---|---|
| All events | runs with the agent, who asked and on whose behalf it acted, model calls with tokens and time, access checks |
| Denials | only denied checks, with the policy and the reason |
| Network | domains contacted by agents and by the server through the node proxy |

## Who asked and on whose behalf

Each run records two people separately: who **started** it, and on whose **behalf** the agent acted (the effective principal). For a `delegated` agent they are the same user; for an `autonomous` agent the agent acts under its own identity. The rights used are always those of the effective principal.

## What is not recorded

- The text of requests and answers through the model API (`/v1`): the journal keeps the model, tokens, time, result and denials, not the text.
- What connected agents do inside their containers — only their model calls, requests to them and their network.
- The contents of HTTPS traffic — only domains.

## How long it is kept

Traces (the step-by-step detail of a run) are kept for 30 days (`journal.trace_retention_days` in `distribution.yaml`); `yorath purge-traces` removes older ones. The audit (decisions and changes) is a separate table and is not purged with traces.
