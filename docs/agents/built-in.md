---
title: Built-in agents
description: "What the four built-in agents do and how to check each one in the Journal."
---

Yorath ships with four configured agents of different types. They are enabled right away; the
"Agents" step of the setup wizard shows their permissions, and you can disable the ones you don't need
there or in the Agents section. They are examples: build your own agent from one with the "Create from
this" button.

Every check below is visible in the Journal: the agent's execution, tool calls, policy decisions,
model and tokens.

For these checks, enable the HR demo set (the "Documents" step of the wizard or the Data section): it
creates the `team-docs` source with the vacation policy, the `hr-vacations` table and three users.

| Login | Who | What they see in the vacation table |
|---|---|---|
| `anna.parker` | employee, Engineering | only her own row |
| `max.lewis` | Engineering manager | himself and his reports |
| `olivia.newman` | HRBP for Field Sales | Field Sales employees |

Passwords are shown once, when the demo set is enabled.

## document-assistant — answers from documents

| | |
|---|---|
| Type | own loop, `delegated`, control `tool` |
| Tools | `search_docs`, `query_records`, `ask_agent → research-agent` |
| Data | `team-docs`, `hr-vacations`, ceiling `confidential` |
| Models | `local/default`, `openrouter/default` |

Check in Chat with the `document-assistant` agent:

1. As the owner: "How many vacation days can be carried over to next year?" — the answer is "no more
   than 56 days" with a `[1]` citation to the vacation policy.
2. Sign in as `anna.parker`: "How many vacation days do I have left?" — 21 days. "And Miller?" —
   "no records found": the agent can't see someone else's row, and the journal shows the denial only as
   a row count.
3. Sign in as `max.lewis`: "How many vacation days does Miller have left?" — 12 days: Peter Miller
   reports to him.

## research-agent — web search

| | |
|---|---|
| Type | own loop, `delegated`, control `tool` |
| Tools | `web_search` (Tavily) |
| Data | none, ceiling `internal` |
| Models | `local/default`, `openrouter/default` |

Needs a Tavily key (the "External services" step of the wizard). Check: "What is the latest version of
Python?" — an answer with `[1]`, `[2]` links. Through `document-assistant` the agent is called only while
the conversation has no data above `internal`: after a question about an employee's vacation, the
`ask_agent` call is blocked and the reason is visible in the journal.

## developer-agent — tasks from GitHub

| | |
|---|---|
| Type | Codex CLI in a gVisor sandbox, `autonomous`, control `sandbox` |
| Tools | `github` (a GitHub App token for one repository) |
| Data | none, ceiling `internal` |
| Models | `openrouter/default`, `local/default` |

It doesn't run without setup: it needs Linux with `runsc`, a GitHub App and the sandbox — step by step
in [Developer Agent setup](developer-agent.md). Check: an issue with the agent's label in the connected
repository → an execution appears in Agents → Tasks, and when it's done, a pull request as the outcome.
A local model works for it only on a GPU with a context window of 32,768.

## model-api — the model over an API

| | |
|---|---|
| Type | external agent (`hosting: external`), `delegated`, control `gateway` |
| Tools | none: the client runs its own tools |
| Data | none, ceiling `restricted` (the class of the text sent is unknown) |
| Models | `local/default` only |

Check: the API keys screen → create a key → run the `curl` shown there. The model's answer comes in
OpenAI format; the journal gets a `model-api` execution on your behalf, without the request or response
text. More in [API](../reference/api.md).
